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

from anchor.anchoring.approx import bounded_edit_distance, fuzzy_search
from anchor.anchoring.budget import Budget

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
        approx = fuzzy_search(text, exact, k, timeout_seconds=budget.remaining_seconds())
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

    candidate_start = text.find(prefix)
    for _ in range(_MAX_CONTEXT_CANDIDATES):
        if candidate_start == -1 or budget.exhausted():
            return None
        body_start = candidate_start + len(prefix)
        # 후보 본문은 원 인용문 길이에서 편집거리 상한만큼만 늘어날 수 있다.
        # str.find의 end는 부분문자열 전체를 포함해야 하므로 suffix 길이를 더한다.
        search_end = body_start + len(exact) + k + 1 + len(suffix)
        suffix_at = text.find(suffix, body_start, search_end)
        if suffix_at != -1:
            candidate = text[body_start:suffix_at]
            distance = bounded_edit_distance(exact, candidate, k)
            if distance is not None:
                if distance == 0:
                    # 완전 일치가 문맥으로 확인됨 — 2단계가 놓쳤을 수 없으나 방어적 처리
                    return MatchResult(MOVED, 1.0, 0, body_start, candidate, truncated)
                return MatchResult(
                    ALTERED,
                    score=_score(distance, exact),
                    edit_distance=distance,
                    found_offset=body_start,
                    found_text=candidate,
                    truncated=truncated,
                )
        candidate_start = text.find(prefix, candidate_start + 1)
    return None


def _score(edit_distance: int, exact: str) -> float:
    return max(0.0, 1.0 - edit_distance / len(exact))
