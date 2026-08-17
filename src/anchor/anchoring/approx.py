# SPDX-License-Identifier: Apache-2.0
"""근사 문자열 검색 (SPEC §6.2 4단계).

주 구현은 `regex` 모듈의 퍼지 매칭 — C 구현이며 오류 상한(e<=k)과
타임아웃을 네이티브로 지원한다. 편집거리 상한을 먼저 정하고 그 상한을
넘으면 즉시 포기하는 방식이라, "찾지 못할 때 가장 느린" diff-match-patch
계열의 실패 모드(Hypothesis #3919)를 피한다.

매칭 전략의 단계 구성은 Hypothesis 클라이언트의 앵커링 접근을
참조했다 (BSD-2-Clause, Copyright (c) 2013-2019 Hypothes.is Project
and contributors). 코드는 Python으로 새로 작성했으며, 근사 매칭
단계는 diff-match-patch 대신 편집거리 상한 방식을 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass

import regex


@dataclass(frozen=True)
class ApproxMatch:
    offset: int
    found_text: str
    edit_distance: int


def bounded_edit_distance(a: str, b: str, k: int) -> int | None:
    """편집거리가 k 이하면 그 값을, 넘으면 None을 반환한다."""
    if abs(len(a) - len(b)) > k:
        return None
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i] + [0] * len(b)
        row_min = i
        for j, char_b in enumerate(b, 1):
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (char_a != char_b),
            )
            row_min = min(row_min, current[j])
        if row_min > k:
            return None
        previous = current
    return previous[-1] if previous[-1] <= k else None


def fuzzy_search(text: str, exact: str, k: int, timeout_seconds: float) -> ApproxMatch | None:
    """문서 전체에서 편집거리 k 이하의 최적 근사 일치를 찾는다.

    시간 예산을 넘기면 TimeoutError가 발생한다 — 호출자가 UNRESOLVED로
    변환한다.
    """
    pattern = regex.compile(
        "(?:" + regex.escape(exact) + "){e<=" + str(k) + "}", flags=regex.BESTMATCH
    )
    match = pattern.search(text, timeout=timeout_seconds)
    if match is None:
        return None
    substitutions, insertions, deletions = match.fuzzy_counts
    return ApproxMatch(
        offset=match.start(),
        found_text=match.group(0),
        edit_distance=substitutions + insertions + deletions,
    )
