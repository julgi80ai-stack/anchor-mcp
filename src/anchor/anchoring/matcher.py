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

from anchor.anchoring.approx import bounded_edit_distance, fuzzy_search, fuzzy_search_myers
from anchor.anchoring.budget import Budget

# regex 퍼지는 편집거리 상한이 이보다 크면 미발견 시 지수적으로 느려진다
# (실측: k=11, 1천 자 문서에서 5초 초과). 그 위로는 k와 무관하게 O(n)인
# Myers 경로를 쓴다 (SPEC §6.2 폴백). 영어 인용문은 같은 문장이라도 글자
# 수가 길어 k가 커지므로 이 분기가 실질적으로 언어 간 형평을 만든다.
_REGEX_MAX_K = 6

INTACT = "INTACT"
MOVED = "MOVED"
ALTERED = "ALTERED"
MISSING = "MISSING"
GONE = "GONE"  # 매처가 아니라 페치 계층이 판정한다
UNREACHABLE = "UNREACHABLE"  # 상동
UNRESOLVED = "UNRESOLVED"

ALL_STATES = (INTACT, MOVED, ALTERED, MISSING, GONE, UNREACHABLE, UNRESOLVED)

# 3단계에서 prefix 반복 출현을 따라가는 상한. 문맥이 이보다 흔하면
# 어차피 4단계 근사 검색이 더 믿을 만하다.
_MAX_CONTEXT_CANDIDATES = 32


@dataclass(frozen=True)
class MatchResult:
    state: str
    score: float | None = None
    edit_distance: int | None = None
    found_offset: int | None = None
    found_text: str | None = None
    truncated: bool = False


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
        text = text[:max_chars]
    k = max(1, min(int(len(exact) * max_edit_ratio), max_edit_distance))

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
    context_result = _match_by_context(text, exact, prefix, suffix, k, budget, truncated)
    if context_result is not None:
        return context_result

    if budget.exhausted():
        return MatchResult(UNRESOLVED, truncated=truncated)

    # 4단계 — 편집거리 상한 근사 검색
    try:
        if k <= _REGEX_MAX_K:
            approx = fuzzy_search(text, exact, k, timeout_seconds=budget.remaining_seconds())
        else:
            approx = fuzzy_search_myers(text, exact, k, budget)
    except TimeoutError:
        return MatchResult(UNRESOLVED, truncated=truncated)
    if approx is None:
        return MatchResult(MISSING, truncated=truncated)
    return MatchResult(
        ALTERED,
        score=_score(approx.edit_distance, exact),
        edit_distance=approx.edit_distance,
        found_offset=approx.offset,
        found_text=approx.found_text,
        truncated=truncated,
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
        if candidate_start == -1 or budget.exhausted():
            break
        body_start = candidate_start + len(prefix)
        # 후보 본문은 원 인용문 길이에서 편집거리 상한만큼만 늘어날 수 있다.
        # str.find의 end는 부분문자열 전체를 포함해야 하므로 suffix 길이를 더한다.
        search_end = body_start + len(exact) + k + 1 + len(suffix)
        suffix_at = text.find(suffix, body_start, search_end)
        if suffix_at != -1:
            candidate = text[body_start:suffix_at]
            distance = bounded_edit_distance(exact, candidate, min(k, best_distance - 1))
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
    return best


def _score(edit_distance: int, exact: str) -> float:
    return max(0.0, 1.0 - edit_distance / len(exact))
