# SPDX-License-Identifier: Apache-2.0
"""본문 추출 + 마크다운 변환 (SPEC §5.3).

원본 바이트 → 인코딩 판별(charset-normalizer) → 본문 추출(trafilatura,
마크다운 출력; 실패 시 readability-lxml + markdownify 폴백) → 텍스트
정규화. PDF는 pypdf 텍스트 추출 후 동일 경로를 탄다 (스캔 PDF는
UnsupportedContent).
"""

from __future__ import annotations

import importlib.metadata as _metadata
import io
import re
from dataclasses import dataclass

import markdownify
import pypdf
import trafilatura
from charset_normalizer import from_bytes
from readability import Document as ReadabilityDocument

from anchor.errors import ExtractionFailed, UnsupportedContent
from anchor.normalize.text import NORM_VERSION, dehyphenate, normalize_text

_CHARSET_VERSION = _metadata.version("charset-normalizer")

# pipeline_version은 text_hash의 숨은 입력을 전부 담아야 한다 (SPEC §5.3).
# 경로마다 실제로 쓰인 도구가 다르므로 문자열도 달라야 한다 — 같은 값을
# 쓰면 Content-Type이 흔들릴 때 원문이 그대로인데 `changed`로 오보한다
# (D-015). 인코딩 판별기도 모든 경로의 숨은 입력이므로 포함한다.
PIPELINE_VERSION = (
    f"trafilatura/{trafilatura.__version__}"
    f"+charset/{_CHARSET_VERSION}+norm/{NORM_VERSION}"
)
PDF_PIPELINE_VERSION = f"pypdf/{pypdf.__version__}+norm/{NORM_VERSION}"
PLAIN_PIPELINE_VERSION = f"plain+charset/{_CHARSET_VERSION}+norm/{NORM_VERSION}"
READABILITY_PIPELINE_VERSION = (
    f"readability-lxml/{_metadata.version('readability-lxml')}"
    f"+markdownify/{_metadata.version('markdownify')}"
    f"+charset/{_CHARSET_VERSION}+norm/{NORM_VERSION}"
)

# 접두 일치는 `text/plaintext`를 plain으로 오분류한다 — 정확히 비교한다 (D-017).
_TEXT_PLAIN_TYPES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})
_HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_PDF_TYPES = frozenset({"application/pdf", "application/x-pdf"})


@dataclass(frozen=True)
class NormalizedDoc:
    text: str
    title: str | None
    pipeline_version: str


def decode_bytes(raw: bytes) -> str:
    best = from_bytes(raw).best()
    if best is not None:
        return str(best)
    return raw.decode("utf-8", errors="replace")


def to_normalized(raw: bytes, content_type: str) -> NormalizedDoc:
    # 서버가 `Content-Type`을 두 번 보내면 httpx가 `text/html, text/html`로
    # 이어 붙인다 — 미디어 타입은 첫 항목이다 (D-069).
    media_type = content_type.split(",", 1)[0].split(";", 1)[0].strip().lower()

    if media_type in _TEXT_PLAIN_TYPES:
        text = normalize_text(decode_bytes(raw))
        if not text:
            # HTML 분기와 같은 이유로 거부한다 (D-071). 빈 판본이 저장되면 그
            # 문서의 앵커가 전부 MISSING으로 뒤집힌다.
            raise ExtractionFailed(
                "Plain-text body is empty after normalization — 정규화 후 본문이 비었습니다"
            )
        return NormalizedDoc(
            text=text, title=None, pipeline_version=PLAIN_PIPELINE_VERSION
        )

    if media_type in _PDF_TYPES:
        return _from_pdf(raw)

    if media_type and media_type not in _HTML_TYPES:
        raise UnsupportedContent(f"Unsupported content type — 처리하지 않는 콘텐츠 유형: {media_type}")

    html = decode_bytes(raw)
    extracted = trafilatura.extract(html, output_format="markdown")
    pipeline_version = PIPELINE_VERSION
    text = normalize_text(extracted) if extracted else ""

    # 폴백은 "결과가 없을 때"만이 아니라 **결과의 구조가 무너졌을 때**도 쓴다.
    # 본문이 짧은 페이지에서 trafilatura가 블록 구분 없는 평문을 돌려주면
    # 제목이 뒤 문단에 낱말째 붙어(`Service statusDegraded performance…`)
    # 화면 복사 인용이 성립하지 않는다 (D-072).
    if not text or _blocks_collapsed(text, html):
        fallback = _readability_fallback(html)
        fallback_text = normalize_text(fallback) if fallback else ""
        if fallback_text and (not text or not _blocks_collapsed(fallback_text, html)):
            text = fallback_text
            pipeline_version = READABILITY_PIPELINE_VERSION

    # 추출은 성공했는데 정규화가 비우는 경우가 있다. 빈 판본을 저장하면
    # `char_count: 0` 버전이 `changed`로 기록되고 그 문서의 앵커가 전부
    # MISSING으로 뒤집힌다 — 추출 실패가 인용 무효로 둔갑한다 (D-071).
    if not text:
        raise ExtractionFailed("Extraction failed: neither trafilatura nor readability found body text — 본문 추출 실패 (두 추출기 모두 본문을 찾지 못함)")

    title: str | None = None
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata is not None:
            title = metadata.title or None
    except Exception:  # 메타데이터는 부가 정보 — 실패해도 본문 처리를 막지 않는다
        title = None
    if title is None:
        try:
            title = ReadabilityDocument(html).short_title() or None
        except Exception:
            title = None

    return NormalizedDoc(text=text, title=title, pipeline_version=pipeline_version)


_BLOCK_TAG_RE = re.compile(r"<(?:p|h[1-6]|li|blockquote|pre|tr|dd|dt)\b", re.IGNORECASE)


def _blocks_collapsed(text: str, html: str) -> bool:
    """추출 결과에 블록 구분이 없는데 원본에는 블록이 여럿인가 (D-072)."""
    if "\n" in text:
        return False
    return len(_BLOCK_TAG_RE.findall(html)) >= 2


def _readability_fallback(html: str) -> str | None:
    """trafilatura가 본문을 못 찾은 문서의 폴백 경로 (SPEC §5.3)."""
    try:
        summary_html = ReadabilityDocument(html).summary()
        markdown = markdownify.markdownify(summary_html)
    except Exception:
        return None
    return markdown if markdown and markdown.strip() else None


def _from_pdf(raw: bytes) -> NormalizedDoc:
    try:
        reader = pypdf.PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:
        raise ExtractionFailed(f"PDF parsing failed — PDF 파싱 실패: {error}") from error

    text = normalize_text(dehyphenate("\n\n".join(pages)))
    if not text:
        # 텍스트 레이어가 없는 스캔 PDF. OCR은 범위 밖이다 (SPEC §5.3).
        raise UnsupportedContent("PDF has no text layer (likely scanned); OCR is out of scope — 텍스트 레이어가 없는 PDF (스캔본 추정), OCR은 범위 밖")

    title: str | None = None
    try:
        raw_title = reader.metadata.title if reader.metadata is not None else None
        # 문자열이 아닌 값(null·숫자·배열)을 str()로 감싸면 'NullObject' 같은
        # 내부 표현이 제목으로 저장된다 (D-018).
        if isinstance(raw_title, str) and raw_title.strip():
            title = raw_title.strip()
    except Exception:
        title = None

    return NormalizedDoc(text=text, title=title, pipeline_version=PDF_PIPELINE_VERSION)
