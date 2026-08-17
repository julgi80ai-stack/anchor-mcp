# SPDX-License-Identifier: Apache-2.0
"""근사 검색 창 선택 회귀 (D-042 / D-043).

감사 실증: 인용문의 앞(또는 뒤) 64자가 문서 다른 곳에 더 잘, 또는 동등하게
정렬되면 그 디코이 창만 검사하고 진짜 위치를 놓쳐 거짓 MISSING이 났다.
정답은 참조 준전역 DP(`best_substring_match`)와 대조해 확인한다.
"""

from __future__ import annotations

import random

import pytest

from anchor.anchoring.approx import best_substring_match, fuzzy_search_myers
from anchor.anchoring.budget import Budget
from anchor.anchoring.matcher import ALTERED, match_anchor
from anchor.anchoring.selector import build_selector


def k_for(exact: str) -> int:
    return max(1, min(int(len(exact) * 0.15), 64))


def test_lead_repeat_does_not_cause_false_missing():
    """자연어 최소 반례: 요약 박스가 리드문을 반복하는 흔한 기사 구조."""
    exact = (
        "The committee concluded that the existing safeguards were insufficient "
        "and recommended an immediate review of all outstanding licences."
    )
    text = (
        "Summary\nThe committee concluded that the existing safeguards were insufficient.\n"
        "\nFull report\nMembers met over three days to consider the evidence. "
        + exact.replace("The committee concluded", "The panel concluded")
        + " The chair said further hearings would follow in the spring.\n"
    )
    k = k_for(exact)
    truth = best_substring_match(exact, text, k)
    assert truth is not None, "전제: 참값이 k 이내에 존재한다"

    hit = fuzzy_search_myers(text, exact, k, Budget(500))
    assert hit is not None, "디코이 때문에 진짜 위치를 놓쳤다"
    assert hit.edit_distance == truth[0]
    assert text[hit.offset : hit.offset + len(hit.found_text)] == hit.found_text


def test_head_decoy_synthetic():
    exact = "a" * 64 + "b" * 12
    text = "a" * 64 + "z" * 30 + "c" + "a" * 63 + "b" * 12
    k = k_for(exact)
    hit = fuzzy_search_myers(text, exact, k, Budget(500))
    assert hit is not None and hit.edit_distance == best_substring_match(exact, text, k)[0]


def test_window_right_margin_covers_two_k():
    """D-043: 코어가 왼쪽으로 밀려도 인용문 꼬리가 창에 들어와야 한다."""
    exact = "a" * 64 + "b" * 36
    text = "a" * 20 + "a" * 64 + "b" * 18 + "c" * 15 + "b" * 18
    k = k_for(exact)
    truth = best_substring_match(exact, text, k)
    assert truth is not None
    hit = fuzzy_search_myers(text, exact, k, Budget(500))
    assert hit is not None and hit.edit_distance == truth[0]


def test_altered_not_missing_end_to_end():
    exact = (
        "The committee concluded that the existing safeguards were insufficient "
        "and recommended an immediate review of all outstanding licences."
    )
    original = "Summary\n" + exact + "\n\nBody\n" + exact + " Then the meeting closed."
    selector = build_selector(original, exact)
    edited = original.replace(
        exact + " Then", exact.replace("The committee", "The panel") + " Then"
    )
    result = match_anchor(
        edited,
        exact=selector.exact,
        prefix=selector.prefix,
        suffix=selector.suffix,
        position_hint=selector.position_hint,
        budget_ms=500,
    )
    assert result.state != "MISSING"


@pytest.mark.parametrize("seed", range(40))
def test_differential_against_reference_with_decoys(seed):
    """참조 DP와의 무작위 대조 — 디코이(접두/접미 반복)를 일부러 심는다."""
    rng = random.Random(seed)
    alphabet = "abc"
    exact = "".join(rng.choice(alphabet) for _ in range(rng.randint(70, 130)))
    k = k_for(exact)

    mutated = list(exact)
    for _ in range(rng.randint(1, max(1, k // 2))):
        index = rng.randrange(len(mutated))
        mutated[index] = rng.choice(alphabet)
    mutated_text = "".join(mutated)

    decoy = exact[:64] if rng.random() < 0.5 else exact[-64:]
    filler = "".join(rng.choice(alphabet) for _ in range(rng.randint(20, 80)))
    text = decoy + filler + mutated_text + filler

    truth = best_substring_match(exact, text, k)
    hit = fuzzy_search_myers(text, exact, k, Budget(2000))
    if truth is None:
        assert hit is None
        return
    assert hit is not None, f"참값 거리 {truth[0]}가 존재하는데 못 찾았다 (seed={seed})"
    assert hit.edit_distance == truth[0], f"거리 불일치 (seed={seed})"
    assert text[hit.offset : hit.offset + len(hit.found_text)] == hit.found_text
