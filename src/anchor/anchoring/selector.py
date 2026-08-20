# SPDX-License-Identifier: Apache-2.0
"""앵커 생성 (SPEC §6.1). W3C Web Annotation의 TextQuoteSelector +
TextPositionSelector 조합.

존재하지 않는 인용을 기록하지 않는 것이 이 도구의 기본 계약이다 —
quote가 원문에 없으면 QuoteNotFound를 던진다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from anchor.errors import QuoteNotFound, QuoteTooShort
from anchor.models import Quality
from anchor.normalize.text import (
    WORDLESS_INFORMATION_RATIO,
    is_wordless_script,
    is_wordless_text,
    normalize_text,
)

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
    occurrences: int = 1  # 생성 시점에 원문에서 몇 번 나왔는가


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


# 화면에서 끌어 복사한 인용문은 저장 본문과 **블록 구분자가 다르다**. 브라우저는
# 문단 사이를 줄바꿈 하나나 공백으로 주고 목록 표지(`- `)는 아예 주지 않는데,
# 저장 본문에는 빈 줄과 표지가 있다. 그래서 두 문단·두 목록 항목을 한 번에
# 인용하는 흔한 행동이 전부 `QuoteNotFound`가 됐다 (D-074).
# 브라우저는 줄머리의 블록 표지를 클립보드에 넣지 않는다. 목록만 다루면
# "이 절을 통째로 복사"라는 가장 흔한 제스처가 여전히 실패한다 — 골든 36건
# 표본에서 제목을 걸친 선택의 46%가 그래서 깨졌다.
_LIST_MARKER_RE = re.compile(r"(?:[-*+]|\d{1,2}[.)]|#{1,6}|>)\s+")
_FORM_WHITESPACE = " \t\n"


def _form_stream(text: str) -> Iterator[tuple[str, int]]:
    """비교용 검색형과 원본 오프셋을 함께 흘려보낸다.

    공백·줄바꿈의 연속은 공백 하나로 접고, 줄머리의 목록 표지는 건너뛴다.
    오프셋 배열을 통째로 들고 있지 않으려고 스트림으로 만든다 — 2MB 문서에서
    정수 배열은 수십 MB가 된다.
    """
    emitted = False
    pending_space = False
    at_line_start = True
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in _FORM_WHITESPACE:
            pending_space = emitted
            if char == "\n":
                at_line_start = True
            index += 1
            continue
        if at_line_start:
            marker = _LIST_MARKER_RE.match(text, index)
            if marker is not None:
                index = marker.end()
                continue
        at_line_start = False
        if pending_space:
            yield " ", index
            pending_space = False
        yield char, index
        emitted = True
        index += 1


def _search_form(text: str) -> str:
    """비교용 검색형. 양쪽에 같은 규칙을 적용해야 만난다.

    CJK 문자 **사이의** 공백은 없앤다. 본문 쪽은 문서의 문자 체계에 따라 블록을
    공백 없이 잇는데 검색형이 언제나 공백 하나로 접으면 두 형태가 구조적으로
    만나지 못한다 — 일본어·중국어 문서에서 여러 블록에 걸친 인용이 전부
    실패했다. 인용문만 CJK인 한국어 문서도 같은 이유로 실패했다.
    """
    chars = [char for char, _ in _form_stream(text)]
    kept: list[str] = []
    for index, char in enumerate(chars):
        if (
            char == " "
            and kept
            and is_wordless_script(kept[-1])
            and index + 1 < len(chars)
            and is_wordless_script(chars[index + 1])
        ):
            continue
        kept.append(char)
    return "".join(kept)


def _form_pairs(text: str) -> list[tuple[str, int]]:
    """검색형의 (문자, 원본 오프셋) 쌍. `_search_form`과 같은 규칙을 쓴다."""
    raw = list(_form_stream(text))
    kept: list[tuple[str, int]] = []
    for position, (char, index) in enumerate(raw):
        if (
            char == " "
            and kept
            and is_wordless_script(kept[-1][0])
            and position + 1 < len(raw)
            and is_wordless_script(raw[position + 1][0])
        ):
            continue
        kept.append((char, index))
    return kept


def _span_in_source(text: str, start_in_form: int, length_in_form: int) -> tuple[int, int]:
    """검색형의 구간을 원본 오프셋 구간으로 되돌린다."""
    last = start_in_form + length_in_form - 1
    start = end = -1
    for position, (_, index) in enumerate(_form_pairs(text)):
        if position == start_in_form:
            start = index
        if position == last:
            end = index + 1
            break
    return start, end


def build_selector(
    text: str,
    quote: str,
    *,
    context_chars: int = 48,
    min_quote_chars: int = 12,
    short_quote_chars: int = 32,
) -> Selector:
    # 호출자의 인용문도 본문과 같은 규칙으로 정규화해야 비교가 성립한다.
    requested = normalize_text(quote)
    needle = _search_form(requested)
    # 길이 게이트는 **실제로 탐색하는 문자열**을 세야 한다. 검색형은 공백과
    # 목록 표지를 걷어내므로, 원 인용문 기준으로 재면 12자 하한을 통과한
    # 인용문이 1자짜리 앵커가 될 수 있다 — 그런 앵커는 아무 문서에나 걸려
    # "존재하지 않는 인용을 기록하지 않는다"는 계약이 뚫린다.
    minimum = scale_for_script(min_quote_chars, needle)
    short_limit = scale_for_script(short_quote_chars, needle)
    if len(needle) < minimum:
        raise QuoteTooShort(
            f"Quote reduces to {len(needle)} searchable chars; at least {minimum} required "
            f"for this script, one complete sentence recommended — 인용문이 실제 탐색 기준 "
            f"{len(needle)}자 (이 문자 체계의 최소 {minimum}자 필요)"
        )

    haystack = _search_form(text)
    position = haystack.find(needle) if needle else -1
    if position == -1:
        raise QuoteNotFound(
            "Quote not found in this document's stored text; nonexistent citations are never "
            "recorded. If you copied it from the page, that region may not have been extracted "
            "as body text (navigation, figure captions, sidebars) — check with get_version. "
            "— 이 문서의 저장된 본문에서 인용문을 찾지 못했습니다. 없는 인용은 기록하지 "
            "않습니다. 화면에서 복사했다면 그 영역이 본문으로 추출되지 않았을 수 있습니다 "
            "(내비게이션·그림 설명·사이드바) — get_version으로 저장된 본문을 확인하세요."
        )

    # 앵커의 `exact`는 **저장 본문에 실재하는 문자열**이어야 한다 — 매칭
    # 1·2단계가 `text.find(exact)`로 돌기 때문이다. 사용자가 준 형태가 아니라
    # 원문의 형태에 닻을 내린다.
    offset, end = _span_in_source(text, position, len(needle))
    exact = text[offset:end]
    occurrences = _count_occurrences(haystack, needle)

    return Selector(
        exact=exact,
        prefix=text[max(0, offset - context_chars) : offset],
        suffix=text[end : end + context_chars],
        position_hint=offset,
        quality=Quality.SHORT if len(needle) < short_limit else Quality.OK,
        occurrences=occurrences,
    )


# 출현 횟수를 세는 상한. 모호성을 알리는 데 필요한 것은 "여럿인가"이지
# 정확한 개수가 아니므로, 42만 자 본문에서 전수를 세지 않는다. **이 값이
# 나왔다는 것은 "이 값 이상"이라는 뜻이다** — 그 사실은 값과 함께 나가야
# 한다(D-279). 공개 상수인 이유는 응답을 읽는 쪽이 포화 여부를 판정하려면
# 상한을 알아야 하기 때문이다.
OCCURRENCE_COUNT_LIMIT = 8


def _count_occurrences(text: str, exact: str, limit: int = OCCURRENCE_COUNT_LIMIT) -> int:
    """인용문이 원문에 몇 번 나오는지 센다 (limit에서 멈춘다).

    중복 출현은 앵커가 어느 인스턴스를 가리키는지 모호하게 만든다. SPEC
    §6.2의 1·2단계는 단순 완전 일치라 첫 출현을 잡으므로, 사용자가 인용한
    인스턴스가 삭제돼도 다른 인스턴스 때문에 INTACT가 될 수 있다 (D-047).
    호출자에게 경고할 수 있도록 개수를 남긴다.

    돌려주는 값이 `limit`이면 **그 이상**이라는 뜻이다 (D-279).
    """
    count = 0
    start = 0
    while count < limit:
        found = text.find(exact, start)
        if found == -1:
            break
        count += 1
        start = found + 1
    return count
