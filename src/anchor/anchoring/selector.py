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
from anchor.normalize.text import WORDLESS_INFORMATION_RATIO, is_wordless_text, normalize_text

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


def scale_for_script(threshold: int, text: str) -> int:
    """문자 수 임계를 문자 체계에 맞게 환산한다 (D-049).

    12자·32자 기준은 라틴 문자를 전제로 잡힌 값이다. 일본어·중국어는 같은
    내용을 훨씬 적은 글자로 적으므로, 그대로 적용하면 완결된 문장이
    `QuoteTooShort`로 거부되거나(중국어) 전부 `SHORT`로 분류된다. 정보량이
    대략 2.5배라는 관찰에 따라 임계를 낮춘다. 한국어는 어절을 띄우고 글자당
    정보량이 중간이라 라틴 기준을 그대로 쓴다.
    """
    if not is_wordless_text(text):
        return threshold
    return max(4, round(threshold / WORDLESS_INFORMATION_RATIO))


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
    minimum = scale_for_script(min_quote_chars, exact)
    short_limit = scale_for_script(short_quote_chars, exact)
    if len(exact) < minimum:
        raise QuoteTooShort(
            f"Quote is {len(exact)} chars; at least {minimum} required for this script, "
            f"one complete sentence recommended — 인용문이 {len(exact)}자 "
            f"(이 문자 체계의 최소 {minimum}자 필요)"
        )

    offset = text.find(exact)
    if offset == -1:
        raise QuoteNotFound("Quote not found in the source text; nonexistent citations are never recorded — 인용문이 원문에 없어 기록하지 않습니다")

    return Selector(
        exact=exact,
        prefix=text[max(0, offset - context_chars) : offset],
        suffix=text[offset + len(exact) : offset + len(exact) + context_chars],
        position_hint=offset,
        quality=Quality.SHORT if len(exact) < short_limit else Quality.OK,
    )
