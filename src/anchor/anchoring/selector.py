# SPDX-License-Identifier: Apache-2.0
"""앵커 생성 (SPEC §6.1). W3C Web Annotation의 TextQuoteSelector +
TextPositionSelector 조합.

존재하지 않는 인용을 기록하지 않는 것이 이 도구의 기본 계약이다 —
quote가 원문에 없으면 QuoteNotFound를 던진다.
"""

from __future__ import annotations

from dataclasses import dataclass

from anchor.errors import QuoteNotFound, QuoteTooShort
from anchor.models import Quality
from anchor.normalize.text import normalize_text

QUALITY_OK = Quality.OK
QUALITY_SHORT = Quality.SHORT


@dataclass(frozen=True)
class Selector:
    """인용문 하나를 원문에서 다시 찾기 위한 위치 서술자.

    position_hint는 탐색 시작점 힌트일 뿐이며, 판정의 근거가 아니다.
    광고 삽입만으로도 오프셋은 변한다.
    """

    exact: str
    prefix: str
    suffix: str
    position_hint: int
    quality: Quality


def build_selector(
    text: str,
    quote: str,
    *,
    context_chars: int = 48,
    min_quote_chars: int = 12,
    short_quote_chars: int = 32,
) -> Selector:
    # 호출자의 인용문도 본문과 같은 규칙으로 정규화해야 비교가 성립한다.
    exact = normalize_text(quote)
    if len(exact) < min_quote_chars:
        raise QuoteTooShort(
            f"Quote is {len(exact)} chars; at least {min_quote_chars} required, one complete sentence recommended — 인용문이 {len(exact)}자 (최소 {min_quote_chars}자 필요)"
        )

    offset = text.find(exact)
    if offset == -1:
        raise QuoteNotFound("Quote not found in the source text; nonexistent citations are never recorded — 인용문이 원문에 없어 기록하지 않습니다")

    return Selector(
        exact=exact,
        prefix=text[max(0, offset - context_chars) : offset],
        suffix=text[offset + len(exact) : offset + len(exact) + context_chars],
        position_hint=offset,
        quality=Quality.SHORT if len(exact) < short_quote_chars else Quality.OK,
    )
