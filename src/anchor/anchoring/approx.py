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
    corroborated: bool = True  # 찾은 자리의 양옆이 앵커의 문맥과 닮았는가


# 예산을 확인하는 사이에 하는 일의 양(DP 칸 수). 확인 주기를 **행 수**로 두면
# 한 행의 비용이 `O(len(b))`라 초과량이 인용문 길이에 비례해 커진다 — 예산
# 200ms에 68,902자 인용문이 1,133ms를 썼다(§10의 앵커당 p99 250ms 위반).
# 주기를 칸 수로 환산하면 초과량이 길이와 무관한 상수로 묶인다 (D-111).
_BUDGET_CELLS = 4096


def _check_period(row_cost: int) -> int:
    return max(1, _BUDGET_CELLS // max(1, row_cost))


def bounded_edit_distance(a: str, b: str, k: int, budget=None) -> int | None:
    """편집거리가 k 이하면 그 값을, 넘으면 None을 반환한다.

    `budget`을 주면 주기적으로 확인해 예산 초과 시 TimeoutError를 낸다 —
    이 DP는 O(len(a)·len(b))라 긴 인용문에서 예산을 통째로 넘길 수 있다
    (실측: 4000자에서 7.7초, 예산의 38배) (D-045).
    """
    if abs(len(a) - len(b)) > k:
        return None
    period = _check_period(len(b))
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        if budget is not None and i % period == 0 and budget.exhausted():
            raise TimeoutError("bounded_edit_distance 예산 소진")
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
    """전역 최소 하나만 필요한 호출자를 위한 얇은 껍데기.

    반환: (최소 거리, 끝 인덱스) — 거리가 k를 넘으면 None.
    """
    hits = myers_scan_all(text, pattern, k, budget)
    return hits[0] if hits else None


def myers_scan_all(text: str, pattern: str, k: int, budget) -> list[tuple[int, int]]:
    """Myers(1999) 비트벡터 준전역 검색 — 패턴(≤64자)과의 편집거리가 k 이하인
    **모든 자리**를 찾는다. 비용이 k와 무관하게 O(n)이라, k가 클 때 지수적으로
    느려지는 regex 퍼지의 실패 모드가 없다.

    전역 최소 **하나만** 돌려주면, 인용문의 앞머리를 그대로 옮긴 요약절과
    꼬리를 옮긴 풀인용이 본문보다 앞에 있을 때 앞·뒤 코어의 최적이 둘 다 그
    디코이에 떨어진다 — 진짜 위치의 창은 검사조차 되지 않아 거짓 MISSING이
    된다. 인용문이 64자를 넘으면 regex 구제도 없는 경로다 (D-109).

    이어진 자리는 **한 덩이로 묶어** 그 안의 최소만 남긴다. 한 자리의 일치가
    이웃 몇 칸에서도 k 이하로 나오는 것은 같은 발견이지 다른 후보가 아니다.
    반환: [(거리, 끝 인덱스), ...] — 거리 오름차순.
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
    hits: list[tuple[int, int]] = []
    run_score = k + 1
    run_end = -1

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
        if score <= k:
            if score < run_score:
                run_score, run_end = score, position
        elif run_end != -1:
            hits.append((run_score, run_end))
            run_score, run_end = k + 1, -1
        if position & 0xFFF == 0 and budget.exhausted():
            raise TimeoutError("myers_scan 예산 소진")

    if run_end != -1:
        hits.append((run_score, run_end))
    hits.sort(key=lambda hit: hit[0])
    return hits


def best_substring_match(
    pattern: str, window: str, k: int, budget=None
) -> tuple[int, int, int] | None:
    """window 안에서 pattern과 가장 가까운 부분 문자열을 찾는다 (준전역 DP,
    시작 위치 추적 포함). 반환: (편집거리, 시작, 끝) — k 초과면 None.

    `budget`을 주면 행마다 확인한다 (D-045).
    """
    m, n = len(pattern), len(window)
    period = _check_period(n)
    previous = [0] * (n + 1)
    previous_start = list(range(n + 1))
    for i in range(1, m + 1):
        if budget is not None and i % period == 0 and budget.exhausted():
            raise TimeoutError("best_substring_match 예산 소진")
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


def fuzzy_search_myers(
    text: str, exact: str, k: int, budget, corroborate=None
) -> ApproxMatch | None:
    """Myers 스캔 기반 근사 검색 — regex 퍼지가 병리적으로 느려지는 큰 k에서
    쓰는 경로 (SPEC §6.2 폴백).

    64자를 넘는 인용문은 앞·뒤 64자 코어로 위치를 좁힌 뒤(편집이 한쪽 끝에
    몰려도 반대쪽 코어가 잡는다), 전체 인용문을 창 안에서 준전역 DP로
    검증한다. 예산 소진 시 TimeoutError.

    `corroborate(start, end)`를 주면 그 자리의 양옆이 앵커의 문맥과 닮았는지를
    묻고, **뒷받침되는 후보를 편집거리가 조금 더 큰 쪽이라도 앞세운다**.
    닮은 남(다른 절의 형제 문단)이 진짜보다 거리가 작을 수 있기 때문이다 (D-115).
    """
    m = len(exact)
    cores = [(exact, 0)] if m <= _CORE_LEN else [
        (exact[:_CORE_LEN], 0),
        (exact[-_CORE_LEN:], m - _CORE_LEN),
    ]

    # 코어마다 후보 창을 만들고 **전부** 정제해 본다. 점수가 가장 낮은 코어
    # 하나만 남기면, 인용문의 한쪽 끝이 문서 다른 곳(제목·리드·풀인용)에
    # 더 잘 정렬될 때 그 디코이 창만 검사하고 진짜 위치를 놓쳐 거짓
    # MISSING이 된다 (D-042·D-109).
    candidates: list[tuple[int, int, int]] = []  # (코어 거리, 창 시작, 창 끝)
    for core, core_offset in cores:
        for core_distance, core_end in myers_scan_all(
            text, core, min(k, len(core) - 1), budget
        ):
            quote_start_estimate = core_end + 1 - len(core) - core_offset
            window_start = max(0, quote_start_estimate - k)
            # 오른쪽 여유는 2k가 필요하다. 참 매치의 시작은 추정치에서 ±k,
            # 길이는 m±k까지 벌어지므로 m+k만으로는 꼬리가 잘린다 (D-043).
            window_end = min(len(text), quote_start_estimate + m + 2 * k)
            candidates.append((core_distance, window_start, window_end))
    candidates.sort()

    best: ApproxMatch | None = None
    refined_windows: list[tuple[int, int]] = []
    for core_distance, window_start, window_end in candidates:
        # 코어는 인용문의 부분 문자열이므로 그 창에서 전체 인용문의 거리는
        # **코어 거리 이상**이다. 이미 뒷받침되는 후보를 그만큼 가깝게
        # 찾았다면 남은 후보가 이길 수 없다 — 전부 훑되 헛일은 하지 않는다.
        if best is not None and best.corroborated and best.edit_distance <= core_distance:
            break
        if any(
            start <= window_start and window_end <= end for start, end in refined_windows
        ):
            continue
        if budget.exhausted():
            raise TimeoutError("fuzzy_search_myers 예산 소진")
        refined_windows.append((window_start, window_end))
        window = text[window_start:window_end]
        refined = best_substring_match(exact, window, k, budget)
        if refined is None:
            continue
        distance, relative_start, relative_end = refined
        offset = window_start + relative_start
        candidate = ApproxMatch(
            offset=offset,
            found_text=window[relative_start:relative_end],
            edit_distance=distance,
            corroborated=(
                True if corroborate is None
                else corroborate(offset, window_start + relative_end)
            ),
        )
        if best is None or _rank(candidate) < _rank(best):
            best = candidate
            if distance == 0 and candidate.corroborated:
                break
    return best


def _rank(match: ApproxMatch) -> tuple[int, int]:
    """문맥이 뒷받침하는 후보가 먼저다. 같은 조건이면 거리가 가까운 쪽."""
    return (0 if match.corroborated else 1, match.edit_distance)


def fuzzy_search(
    text: str, exact: str, k: int, timeout_seconds: float, corroborate=None
) -> ApproxMatch | None:
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
        corroborated=(
            True if corroborate is None else corroborate(match.start(), match.end())
        ),
    )
