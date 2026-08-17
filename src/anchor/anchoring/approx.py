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


def myers_scan(text: str, pattern: str, k: int, budget) -> tuple[int, int] | None:
    """Myers(1999) 비트벡터 준전역 검색 — 패턴(≤64자)과의 편집거리가 최소인
    끝 위치를 찾는다. 비용이 k와 무관하게 O(n)이라, k가 클 때 지수적으로
    느려지는 regex 퍼지의 실패 모드가 없다.

    반환: (최소 거리, 끝 인덱스) — 거리가 k를 넘으면 None.
    예산 소진 시 TimeoutError.
    """
    m = len(pattern)
    assert 0 < m <= 64
    peq: dict[str, int] = {}
    for index, char in enumerate(pattern):
        peq[char] = peq.get(char, 0) | (1 << index)
    mask_all = (1 << m) - 1
    top_bit = 1 << (m - 1)

    pv = mask_all
    mv = 0
    score = m
    best_score = k + 1
    best_end = -1

    for position, char in enumerate(text):
        eq = peq.get(char, 0)
        xv = eq | mv
        xh = (((eq & pv) + pv) ^ pv) | eq
        ph = mv | ((~(xh | pv)) & mask_all)
        mh = pv & xh
        if ph & top_bit:
            score += 1
        elif mh & top_bit:
            score -= 1
        # 준전역(검색) 변형: 첫 행 D[0][j]=0 이므로 수평 캐리 없이 시프트한다.
        # (|1 을 넣으면 텍스트 시작점 고정 정렬이 되어 검색이 망가진다.)
        ph = (ph << 1) & mask_all
        mh = (mh << 1) & mask_all
        pv = (mh | ((~(xv | ph)) & mask_all)) & mask_all
        mv = ph & xv
        if score < best_score:
            best_score = score
            best_end = position
        if position & 0xFFF == 0 and budget.exhausted():
            raise TimeoutError("myers_scan 예산 소진")

    if best_score > k:
        return None
    return best_score, best_end


def best_substring_match(pattern: str, window: str, k: int) -> tuple[int, int, int] | None:
    """window 안에서 pattern과 가장 가까운 부분 문자열을 찾는다 (준전역 DP,
    시작 위치 추적 포함). 반환: (편집거리, 시작, 끝) — k 초과면 None."""
    m, n = len(pattern), len(window)
    previous = [0] * (n + 1)
    previous_start = list(range(n + 1))
    for i in range(1, m + 1):
        char_p = pattern[i - 1]
        current = [i] + [0] * n
        current_start = [0] * (n + 1)
        for j in range(1, n + 1):
            best = previous[j - 1] + (char_p != window[j - 1])
            origin = previous_start[j - 1]
            if previous[j] + 1 < best:
                best, origin = previous[j] + 1, previous_start[j]
            if current[j - 1] + 1 < best:
                best, origin = current[j - 1] + 1, current_start[j - 1]
            current[j], current_start[j] = best, origin
        previous, previous_start = current, current_start

    end = min(range(n + 1), key=lambda j: previous[j])
    distance = previous[end]
    if distance > k:
        return None
    return distance, previous_start[end], end


_CORE_LEN = 64


def fuzzy_search_myers(text: str, exact: str, k: int, budget) -> ApproxMatch | None:
    """Myers 스캔 기반 근사 검색 — regex 퍼지가 병리적으로 느려지는 큰 k에서
    쓰는 경로 (SPEC §6.2 폴백).

    64자를 넘는 인용문은 앞·뒤 64자 코어로 위치를 좁힌 뒤(편집이 한쪽 끝에
    몰려도 반대쪽 코어가 잡는다), 전체 인용문을 창 안에서 준전역 DP로
    검증한다. 예산 소진 시 TimeoutError.
    """
    m = len(exact)
    cores = [(exact, 0)] if m <= _CORE_LEN else [
        (exact[:_CORE_LEN], 0),
        (exact[-_CORE_LEN:], m - _CORE_LEN),
    ]

    best_hit: tuple[int, int, str, int] | None = None  # (score, end, core, offset)
    for core, offset in cores:
        found = myers_scan(text, core, min(k, len(core) - 1), budget)
        if found is not None and (best_hit is None or found[0] < best_hit[0]):
            best_hit = (found[0], found[1], core, offset)

    if best_hit is None:
        return None

    _, core_end, core, core_offset = best_hit
    quote_start_estimate = core_end + 1 - len(core) - core_offset
    window_start = max(0, quote_start_estimate - k)
    window = text[window_start : min(len(text), quote_start_estimate + m + k)]

    refined = best_substring_match(exact, window, k)
    if refined is None:
        return None
    distance, relative_start, relative_end = refined
    return ApproxMatch(
        offset=window_start + relative_start,
        found_text=window[relative_start:relative_end],
        edit_distance=distance,
    )


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
