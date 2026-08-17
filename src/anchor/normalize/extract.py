# SPDX-License-Identifier: Apache-2.0
"""본문 추출 + 마크다운 변환 (SPEC §5.3).

원본 바이트 → 인코딩 판별(charset-normalizer) → 본문 추출(trafilatura,
마크다운 출력) → 텍스트 정규화. readability-lxml 폴백과 PDF는 v0.1 범위
밖이다.
"""

from __future__ import annotations

from dataclasses import dataclass

import trafilatura
from charset_normalizer import from_bytes

from anchor.errors import ExtractionFailed, UnsupportedContent
from anchor.normalize.text import NORM_VERSION, normalize_text

PIPELINE_VERSION = f"trafilatura/{trafilatura.__version__}+norm/{NORM_VERSION}"

_TEXT_PLAIN_TYPES = ("text/plain", "text/markdown")
_HTML_TYPES = ("text/html", "application/xhtml+xml")


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

    if media_type and not media_type.startswith(_HTML_TYPES):
        raise UnsupportedContent(f"v0.1이 처리하지 않는 콘텐츠 유형: {media_type}")

    html = decode_bytes(raw)
    extracted = trafilatura.extract(html, output_format="markdown")
    if not extracted:
        raise ExtractionFailed("본문 추출 실패 — 추출기가 본문을 찾지 못했습니다")

    title: str | None = None
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata is not None:
            title = metadata.title or None
    except Exception:  # 메타데이터는 부가 정보 — 실패해도 본문 처리를 막지 않는다
        title = None

    return NormalizedDoc(
        text=normalize_text(extracted), title=title, pipeline_version=PIPELINE_VERSION
    )
