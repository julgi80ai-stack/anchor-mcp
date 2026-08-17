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
from dataclasses import dataclass

import markdownify
import pypdf
import trafilatura
from charset_normalizer import from_bytes
from readability import Document as ReadabilityDocument

from anchor.errors import ExtractionFailed, UnsupportedContent
from anchor.normalize.text import NORM_VERSION, normalize_text

PIPELINE_VERSION = f"trafilatura/{trafilatura.__version__}+norm/{NORM_VERSION}"
PDF_PIPELINE_VERSION = f"pypdf/{pypdf.__version__}+norm/{NORM_VERSION}"
READABILITY_PIPELINE_VERSION = (
    f"readability-lxml/{_metadata.version('readability-lxml')}"
    f"+markdownify/{_metadata.version('markdownify')}+norm/{NORM_VERSION}"
)

_TEXT_PLAIN_TYPES = ("text/plain", "text/markdown")
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_PDF_TYPES = ("application/pdf",)


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
    media_type = content_type.split(";", 1)[0].strip().lower()

    if media_type.startswith(_TEXT_PLAIN_TYPES):
        text = decode_bytes(raw)
        return NormalizedDoc(
            text=normalize_text(text), title=None, pipeline_version=PIPELINE_VERSION
        )

    if media_type.startswith(_PDF_TYPES):
        return _from_pdf(raw)

    if media_type and not media_type.startswith(_HTML_TYPES):
        raise UnsupportedContent(f"Unsupported content type — 처리하지 않는 콘텐츠 유형: {media_type}")

    html = decode_bytes(raw)
    extracted = trafilatura.extract(html, output_format="markdown")
    pipeline_version = PIPELINE_VERSION
    if not extracted:
        extracted = _readability_fallback(html)
        pipeline_version = READABILITY_PIPELINE_VERSION
    if not extracted:
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

    return NormalizedDoc(
        text=normalize_text(extracted), title=title, pipeline_version=pipeline_version
    )


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

    text = normalize_text("\n\n".join(pages))
    if not text:
        # 텍스트 레이어가 없는 스캔 PDF. OCR은 범위 밖이다 (SPEC §5.3).
        raise UnsupportedContent("PDF has no text layer (likely scanned); OCR is out of scope — 텍스트 레이어가 없는 PDF (스캔본 추정), OCR은 범위 밖")

    title: str | None = None
    try:
        if reader.metadata is not None and reader.metadata.title:
            title = str(reader.metadata.title)
    except Exception:
        title = None

    return NormalizedDoc(text=text, title=title, pipeline_version=PDF_PIPELINE_VERSION)
