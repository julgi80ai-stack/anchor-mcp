# SPDX-License-Identifier: Apache-2.0
"""영어 변형 테스트 — 한국어 대비 편집거리 상한 k가 커지는 언어 조건.

배경: 영어 인용문은 같은 문장이라도 글자 수가 길어 k = len×0.15가 커지고,
regex 퍼지는 k가 크면 미발견 시 지수적으로 느려진다 (실측: k=11, 1천 자
문서에서 5초 초과). k > 6이면 Myers 비트벡터 경로(SPEC §6.2 폴백)를 타며,
이 파일이 그 경로의 회귀를 막는다.
"""

from __future__ import annotations

import time

import pytest

from anchor.anchoring.approx import fuzzy_search_myers, myers_scan
from anchor.anchoring.budget import Budget
from anchor.anchoring.matcher import (
    ALTERED,
    INTACT,
    MISSING,
    MOVED,
    UNRESOLVED,
    match_anchor,
)
from anchor.anchoring.selector import build_selector

PARAGRAPHS = [
    "The web link was originally called an anchor, meant to moor a document to a fixed point.",
    "Conditional requests combined with content hashing remove most redundant fetching entirely.",
    "Citation drift is more dangerous than link rot because nothing visibly breaks over time.",
    "Hashing the normalized body makes the cache immune to advertising and timestamp noise.",
    "An honest client reports a refusal exactly as it was received, without any evasion.",
]

QUOTES = [
    "originally called an anchor, meant to moor a document to a fixed point.",
    "Conditional requests combined with content hashing remove most redundant fetching",
    "Citation drift is more dangerous than link rot because nothing visibly breaks",
    "Hashing the normalized body makes the cache immune to advertising and timestamp noise.",
    "An honest client reports a refusal exactly as it was received",
]

FILLER = "This filler sentence only exists to add distance between the paragraphs above. " * 8
BASE_TEXT = ("\n\n" + FILLER + "\n\n").join(PARAGRAPHS)


def run_match(text: str, quote: str, *, budget_ms: float = 200):
    selector = build_selector(BASE_TEXT, quote)
    return match_anchor(
        text,
        exact=selector.exact,
        prefix=selector.prefix,
        suffix=selector.suffix,
        position_hint=selector.position_hint,
        budget_ms=budget_ms,
    )


@pytest.mark.parametrize("quote", QUOTES)
def test_intact(quote):
    result = run_match(BASE_TEXT, quote)
    assert result.state == INTACT


@pytest.mark.parametrize("quote", QUOTES)
def test_moved(quote):
    source = next(p for p in PARAGRAPHS if quote.rstrip(".") in p)
    others = [p for p in PARAGRAPHS if p is not source]
    selector = build_selector(BASE_TEXT, quote)
    moved = ("\n\n" + FILLER + "\n\n").join(others)
    if selector.position_hint < len(BASE_TEXT) / 2:
        moved = moved + "\n\n" + source
    else:
        moved = source + "\n\n" + moved
    assert run_match(moved, quote).state == MOVED


@pytest.mark.parametrize(
    ("quote", "before", "after"),
    # 변형은 편집거리 상한 k = len(quote)×0.15 이내여야 ALTERED다 —
    # 그보다 큰 개편은 MISSING이 옳은 판정이다.
    [
        (QUOTES[0], "moor a document", "bind a document"),
        (QUOTES[1], "remove most", "strip most"),
        (QUOTES[2], "nothing visibly breaks", "nothing openly breaks"),
        (QUOTES[3], "makes the cache immune", "renders the cache immune"),
        (QUOTES[4], "reports a refusal", "reports a denial"),
    ],
)
def test_altered_with_destroyed_context(quote, before, after):
    """문맥(prefix)까지 파괴해 3단계를 무력화 — 4단계 Myers 경로 강제."""
    selector = build_selector(BASE_TEXT, quote)
    edited = BASE_TEXT.replace(before, after)
    edited = edited.replace(selector.prefix, " some entirely new leading context ")
    result = run_match(edited, quote)
    assert result.state == ALTERED
    assert after.split()[0] in (result.found_text or "")


@pytest.mark.parametrize("quote", QUOTES)
def test_missing_resolves_within_budget(quote):
    """수정 전에는 UNRESOLVED로만 끝나던 케이스 — 예산 안에 MISSING 판정."""
    removed = BASE_TEXT.replace(quote, "Entirely unrelated replacement content.")
    started = time.monotonic()
    result = run_match(removed, quote)
    assert result.state == MISSING
    assert (time.monotonic() - started) < 0.5


@pytest.mark.parametrize("quote", QUOTES)
def test_unresolved_on_zero_budget(quote):
    removed = BASE_TEXT.replace(quote, "")
    assert run_match(removed, quote, budget_ms=0).state == UNRESOLVED


# -- Myers 경로 자체 --------------------------------------------------------


def test_myers_scan_exact_substring_is_distance_zero():
    score, end = myers_scan("aaa hello world bbb", "hello world", 3, Budget(200))
    assert score == 0
    assert end == len("aaa hello world") - 1


def test_myers_long_quote_edit_burst_at_front():
    """64자 초과 인용문의 앞쪽에 편집이 몰려도 뒤 코어가 잡는다."""
    quote = (
        "Hashing the normalized body makes the cache immune to advertising "
        "noise and to timestamps injected by page builders"
    )
    text = "leading text. " + quote.replace(
        "Hashing the normalized body", "Digesting a canonicalized form"
    ) + " trailing text."
    hit = fuzzy_search_myers(text, quote, k=min(int(len(quote) * 0.15), 64), budget=Budget(200))
    assert hit is not None
    assert "timestamps injected by page builders" in hit.found_text


def test_myers_respects_budget_on_huge_document():
    huge = "random filler with many different words repeated endlessly. " * 40000
    with pytest.raises(TimeoutError):
        fuzzy_search_myers(huge, "a quote that does not exist anywhere in this document",
                           k=8, budget=Budget(0.5))
