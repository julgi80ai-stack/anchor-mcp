# SPDX-License-Identifier: Apache-2.0
"""4단계 앵커 매칭 (SPEC §6.2). 앞 단계가 성공하면 즉시 종료한다.

1. position_hint ± 500자에서 완전 일치        → INTACT
2. 문서 전체에서 완전 일치                    → MOVED
3. prefix/suffix 문맥으로 후보 구간 특정 후 비교 → ALTERED
4. 편집거리 상한 근사 검색                    → ALTERED | MISSING

시간 예산 초과 시 어느 단계에서든 UNRESOLVED — 정직한 무응답이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from anchor.anchoring.approx import (
    best_substring_match,
    bounded_edit_distance,
    fuzzy_search,
    fuzzy_search_myers,
)
from anchor.anchoring.budget import Budget
from anchor.normalize.text import wordless_edit_factor

# regex 퍼지는 k가 커질수록 미발견 시 지수적으로 느려진다(k 1 증가마다 약 ×3).
# 실측으로 k=4부터 이미 Myers보다 느리고, k=6·7KB 한국어 기사에서 200ms 예산을
# 소진해 UNRESOLVED가 났다 — 같은 입력을 Myers로 돌리면 56~67배 빠르다.
# 짧은 인용문(=CJK 완결 문장)이 느린 경로에 고정되던 문제이므로 상한을 낮춘다
# (D-048). 두 경로의 판정이 일치한다는 것은 200회 차분 비교로 확인됐고,
# 64자 이하 인용문은 코어가 하나뿐이라 D-042의 실패 모드도 없다.
_REGEX_MAX_K = 3

INTACT = "INTACT"
MOVED = "MOVED"
ALTERED = "ALTERED"
MISSING = "MISSING"
GONE = "GONE"  # 매처가 아니라 페치 계층이 판정한다
UNREACHABLE = "UNREACHABLE"  # 상동
UNRESOLVED = "UNRESOLVED"

ALL_STATES = (INTACT, MOVED, ALTERED, MISSING, GONE, UNREACHABLE, UNRESOLVED)

# 3단계에서 prefix 반복 출현을 따라가는 상한. 문맥이 이보다 흔하면
# 어차피 4단계 근사 검색이 더 믿을 만하다 — 그래서 **상한에 걸리면 3단계의
# 답을 버리고 4단계로 넘긴다**. 32개 중 최선을 확정하면 D-044가 고친 실패
# 모드(형제 문단을 "당신 인용문의 현재 모습"으로 제시)가 경계만 옮겨진 채
# 그대로 재현된다 (D-110).
_MAX_CONTEXT_CANDIDATES = 1000

# 4단계 결과를 뒷받침할 때 보는 문맥의 안쪽 끝 길이와, 허용 편집거리 비율.
# 인용문에 **붙어 있던** 쪽만 본다 — 먼 쪽은 개정으로 흔히 바뀐다.
_CONTEXT_PROBE = 24
_CONTEXT_TOLERANCE = 0.34


@dataclass(frozen=True)
class MatchResult:
    state: str
    score: float | None = None
    edit_distance: int | None = None
    found_offset: int | None = None
    found_text: str | None = None
    truncated: bool = False  # 문서가 상한에서 잘렸다 — 판정의 신뢰도를 낮춘다


def match_anchor(
    text: str,
    *,
    exact: str,
    prefix: str,
    suffix: str,
    position_hint: int,
    budget_ms: float,
    max_edit_ratio: float = 0.15,
    max_edit_distance: int = 64,
    hint_radius: int = 500,
    max_chars: int = 2_097_152,
) -> MatchResult:
    budget = Budget(budget_ms)
    truncated = len(text) > max_chars
    if truncated:
        # 잘라낸 뒤 못 찾으면 "사라졌다"가 아니라 "다 보지 못했다"이다.
        # 아래에서 MISSING/ALTERED 판정을 UNRESOLVED로 낮춘다 (D-046).
        text = text[:max_chars]
    # 편집거리 상한도 문자 체계를 반영한다 (D-049). 같은 성격의 개정(단어
    # 하나 교체)이 일본어·중국어에서는 훨씬 적은 글자로 표현되므로, 글자
    # 수에 고정 비율을 곱하면 ALTERED가 MISSING으로 떨어진다. 반영은
    # **연속**이어야 한다 — 계단을 두면 계단 바로 아래가 항상 틀린다 (D-113).
    effective_ratio = max_edit_ratio * wordless_edit_factor(exact)
    k = max(1, min(int(len(exact) * effective_ratio), max_edit_distance))

    # 1단계 — 힌트 주변 완전 일치
    window_start = max(0, position_hint - hint_radius)
    window_end = min(len(text), position_hint + hint_radius + len(exact))
    offset = text.find(exact, window_start, window_end)
    if offset != -1:
        return MatchResult(INTACT, 1.0, 0, offset, exact, truncated)

    # 2단계 — 전체 완전 일치
    offset = text.find(exact)
    if offset != -1:
        return MatchResult(MOVED, 1.0, 0, offset, exact, truncated)

    if budget.exhausted():
        return MatchResult(UNRESOLVED, truncated=truncated)

    # 3단계 — 문맥 기반 후보 비교
    try:
        context_result = _match_by_context(text, exact, prefix, suffix, k, budget, truncated)
    except TimeoutError:
        return MatchResult(UNRESOLVED, truncated=truncated)
    if context_result is not None:
        return context_result

    if budget.exhausted():
        return MatchResult(UNRESOLVED, truncated=truncated)

    # 4단계 — 편집거리 상한 근사 검색
    def corroborate(start: int, end: int) -> bool:
        return _context_supports(text, start, end, prefix, suffix)

    try:
        if k <= _REGEX_MAX_K:
            approx = fuzzy_search(
                text, exact, k, timeout_seconds=budget.remaining_seconds(),
                corroborate=corroborate,
            )
        else:
            approx = fuzzy_search_myers(text, exact, k, budget, corroborate=corroborate)
    except TimeoutError:
        return MatchResult(UNRESOLVED, truncated=truncated)
    if approx is None or not approx.corroborated:
        # 문맥이 뒷받침하지 않는 근사 일치는 "당신 인용문의 현재 모습"이
        # 아니라 **닮은 남**이다. 삭제된 약관 조항에 다른 절의 템플릿 문장이
        # 붙으면 뜻이 정반대인 문장을 개정문으로 읽게 된다 (D-115).
        # 문서를 다 보지 못했다면 "사라졌다"고 단정할 수도 없다.
        return MatchResult(UNRESOLVED if truncated else MISSING, truncated=truncated)
    if truncated and approx.offset + len(approx.found_text) >= len(text):
        # 잘린 꼬리가 편집거리로 계산돼 **원문 무손상인데 ALTERED**가 된다.
        # "다 보지 못했으면 단정하지 않는다"는 ALTERED에도 적용된다 (D-114).
        return MatchResult(UNRESOLVED, truncated=True)
    return MatchResult(
        ALTERED,
        score=_score(approx.edit_distance, exact),
        edit_distance=approx.edit_distance,
        found_offset=approx.offset,
        found_text=approx.found_text,
        truncated=truncated,
    )


def _context_supports(text: str, start: int, end: int, prefix: str, suffix: str) -> bool:
    """찾은 구간이 **인용문이 있던 자리**인가 (D-115).

    닮았는지를 재는 것만으로는 갈리지 않는다. 약관·릴리스노트·FAQ의 문맥은
    템플릿이라, 다른 절의 형제 문단도 문맥이 두어 글자밖에 다르지 않다.

    갈라 주는 신호는 다른 데 있다 — **문맥이 문서에 그대로 살아 있는데
    인용문이 그 옆에 없다면 그것은 삭제다.** 이 경우 다른 곳의 근사 일치는
    인용문의 현재 모습이 아니라 닮은 남이다.

    문맥까지 함께 개정된 경우에만(원래 자리를 가리킬 표지가 아무것도 남지
    않은 경우) 근사 비교로 물러선다. 문맥이 아예 없는 앵커는 통과시킨다.
    """
    sides = [
        side for side in ((prefix, start, True), (suffix, end, False)) if side[0]
    ]
    if not sides:
        return True

    # 인접 여부는 **그 자리 주변만** 보면 된다. 문서 처음부터 출현을 세면
    # 상한이 필요해지고, 그 상한 너머의 진짜 문단이 다시 근거를 잃는다 —
    # D-110과 똑같은 병이다.
    for context, boundary, is_prefix in sides:
        slack = max(8, len(context) // 2)
        if is_prefix:
            low, high = boundary - len(context) - slack, boundary + slack
        else:
            low, high = boundary - slack, boundary + len(context) + slack
        nearby = text.find(context, max(0, low), min(len(text), high))
        if nearby != -1:
            edge = nearby + len(context) if is_prefix else nearby
            if abs(edge - boundary) <= slack:
                return True
    if any(context in text for context, _, _ in sides):
        # 표지는 살아 있는데 인용문이 그 옆에 없다 — 삭제다.
        return False

    # 표지가 하나도 남지 않았다 — 문맥의 안쪽 끝으로 근사 비교한다.
    probes = []
    if prefix:
        probe = prefix[-_CONTEXT_PROBE:]
        probes.append((probe, text[max(0, start - len(probe) - len(probe) // 2) : start]))
    if suffix:
        probe = suffix[:_CONTEXT_PROBE]
        probes.append((probe, text[end : end + len(probe) + len(probe) // 2]))
    return any(
        best_substring_match(probe, window, int(len(probe) * _CONTEXT_TOLERANCE)) is not None
        for probe, window in probes
        if window
    )


def _match_by_context(
    text: str,
    exact: str,
    prefix: str,
    suffix: str,
    k: int,
    budget: Budget,
    truncated: bool,
) -> MatchResult | None:
    if not prefix or not suffix:
        return None

    # 후보를 전부 훑어 **가장 가까운 것**을 고른다. 첫 후보를 그대로 확정하면
    # 템플릿이 반복되는 문서(약관 조항·변경이력·FAQ·표)에서 인용문과 무관한
    # 형제 문단이 "당신 인용문의 현재 모습"으로 보고된다 (D-044).
    best: MatchResult | None = None
    best_distance = k + 1
    candidate_start = text.find(prefix)
    for _ in range(_MAX_CONTEXT_CANDIDATES):
        if candidate_start == -1:
            break
        if budget.exhausted():
            # 후보를 다 보지 못한 채 지금까지의 최선을 확정하면, 뒤에 있는
            # 진짜 문단 대신 앞쪽 형제가 "현재 모습"이 된다. 모르면 보류한다.
            raise TimeoutError("_match_by_context 예산 소진")
        body_start = candidate_start + len(prefix)
        # 후보 본문은 원 인용문 길이에서 편집거리 상한만큼만 늘어날 수 있다.
        # str.find의 end는 부분문자열 전체를 포함해야 하므로 suffix 길이를 더한다.
        search_end = body_start + len(exact) + k + 1 + len(suffix)
        suffix_at = text.find(suffix, body_start, search_end)
        if suffix_at != -1:
            candidate = text[body_start:suffix_at]
            distance = bounded_edit_distance(
                exact, candidate, min(k, best_distance - 1), budget
            )
            if distance is not None and distance < best_distance:
                best_distance = distance
                if distance == 0:
                    # 완전 일치가 문맥으로 확인됨 — 더 가까운 후보는 없다.
                    return MatchResult(MOVED, 1.0, 0, body_start, candidate, truncated)
                best = MatchResult(
                    ALTERED,
                    score=_score(distance, exact),
                    edit_distance=distance,
                    found_offset=body_start,
                    found_text=candidate,
                    truncated=truncated,
                )
        candidate_start = text.find(prefix, candidate_start + 1)
    else:
        if candidate_start != -1:
            # 상한에 걸렸다 — 문맥이 이만큼 흔하면 3단계의 답은 근거가 약하다.
            # 여기서 확정하지 않고 4단계에 넘긴다 (D-110).
            return None
    return best


def _score(edit_distance: int, exact: str) -> float:
    return max(0.0, 1.0 - edit_distance / len(exact))
