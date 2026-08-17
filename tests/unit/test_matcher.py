# SPDX-License-Identifier: Apache-2.0
"""변형 테스트 (SPEC §12): 원문에 프로그램으로 변형을 가하고 상태 판정 검증.

GONE/UNREACHABLE은 매처가 아니라 페치 계층의 판정이므로 통합 테스트에서
다룬다 (tests/integration/test_cite_verify.py).
"""

from __future__ import annotations

import time

import pytest

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
    "웹의 링크는 원래 앵커라고 불렸다. 문서의 특정 지점에 닻을 내린다는 뜻이었다.",
    "조건부 요청과 본문 해시를 함께 쓰면 재페치와 재파싱을 모두 줄일 수 있다.",
    "학술 문헌이 참조한 웹 콘텐츠의 약 75%가 3년 안에 어느 정도 변경된 것으로 조사되었다.",
    "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다. 깨지지 않기 때문이다.",
    "정규화된 본문을 기준으로 해시하면 광고 노이즈에 속지 않는다. 이것이 이중 해시의 핵심이다.",
    "모르는 것을 모른다고 말하는 것이 틀린 답을 빠르게 주는 것보다 낫다.",
]

QUOTES = [
    "문서의 특정 지점에 닻을 내린다는 뜻이었다.",
    "조건부 요청과 본문 해시를 함께 쓰면 재페치와 재파싱을 모두 줄일 수 있다.",
    "약 75%가 3년 안에 어느 정도 변경된 것으로 조사되었다.",
    "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다.",
    "정규화된 본문을 기준으로 해시하면 광고 노이즈에 속지 않는다.",
]

FILLER = "이 문단은 자리 채움용 문장이다. 특별한 의미 없이 길이만 늘린다. " * 8


def build_text(paragraphs: list[str]) -> str:
    # 문단 사이에 채움 텍스트를 넣어 오프셋 이동이 hint_radius를 넘도록 만든다.
    parts: list[str] = []
    for paragraph in paragraphs:
        parts.append(paragraph)
        parts.append(FILLER)
    return "\n\n".join(parts)


BASE_TEXT = build_text(PARAGRAPHS)


def make_anchor(quote: str):
    return build_selector(BASE_TEXT, quote)


def run_match(text: str, quote: str, *, budget_ms: float = 200, **kwargs):
    anchor = make_anchor(quote)
    return match_anchor(
        text,
        exact=anchor.exact,
        prefix=anchor.prefix,
        suffix=anchor.suffix,
        position_hint=anchor.position_hint,
        budget_ms=budget_ms,
        **kwargs,
    )


@pytest.mark.parametrize("quote", QUOTES)
def test_intact_on_identical_text(quote):
    """속성: cite 직후 같은 버전에 대한 검증은 항상 INTACT (SPEC §12)."""
    result = run_match(BASE_TEXT, quote)
    assert result.state == INTACT
    assert result.score == 1.0


@pytest.mark.parametrize("quote", QUOTES)
def test_intact_survives_small_noise_insertion(quote):
    """힌트 반경(500자) 이내의 오프셋 이동은 INTACT."""
    noisy = "[광고] 오늘의 추천 상품을 확인하세요.\n\n" + BASE_TEXT
    result = run_match(noisy, quote)
    assert result.state == INTACT


@pytest.mark.parametrize("quote", QUOTES)
def test_moved_when_paragraph_relocated_far(quote):
    """인용 문단이 힌트 반경 밖으로 이동하면 MOVED (내용은 그대로)."""
    source = next(p for p in PARAGRAPHS if quote in p)
    others = [p for p in PARAGRAPHS if p is not source]
    anchor = make_anchor(quote)
    # 문단을 문서 반대쪽 끝으로 보낸다.
    if anchor.position_hint < len(BASE_TEXT) / 2:
        moved_text = build_text(others) + "\n\n" + source
    else:
        moved_text = source + "\n\n" + build_text(others)
    result = run_match(moved_text, quote)
    assert result.state == MOVED
    assert result.score == 1.0


@pytest.mark.parametrize(
    ("quote", "before", "after"),
    [
        (QUOTES[0], "뜻이었다.", "말이었다."),
        (QUOTES[1], "모두 줄일 수 있다", "대폭 줄일 수 있다"),
        (QUOTES[2], "약 75%", "약 60%"),
        (QUOTES[3], "가장 위험하다", "제일 위험하다"),
        (QUOTES[4], "속지 않는다", "속지 않게 된다"),
    ],
)
def test_altered_when_sentence_edited(quote, before, after):
    edited = BASE_TEXT.replace(before, after)
    result = run_match(edited, quote)
    assert result.state == ALTERED
    assert result.edit_distance is not None and result.edit_distance > 0
    assert result.score is not None and 0 < result.score < 1.0
    assert after in (result.found_text or "")


@pytest.mark.parametrize("quote", QUOTES)
def test_missing_when_sentence_removed(quote):
    removed = BASE_TEXT.replace(quote, "전혀 무관한 다른 내용이 대신 들어왔다.")
    result = run_match(removed, quote)
    assert result.state == MISSING


def test_altered_found_even_when_context_also_changed():
    """prefix까지 바뀌어 3단계가 실패해도 4단계 근사 검색이 잡아야 한다."""
    quote = QUOTES[1]
    anchor = make_anchor(quote)
    edited = BASE_TEXT.replace("모두 줄일 수 있다", "거의 줄일 수 있다")
    edited = edited.replace(anchor.prefix, "완전히 새로 쓰인 앞 문맥이다. ")
    result = run_match(edited, quote)
    assert result.state == ALTERED


@pytest.mark.parametrize("quote", QUOTES)
def test_unresolved_when_budget_exhausted(quote):
    removed = BASE_TEXT.replace(quote, "")
    result = run_match(removed, quote, budget_ms=0)
    assert result.state == UNRESOLVED


def test_truncation_flag_on_oversized_document():
    quote = QUOTES[0]
    result = run_match(BASE_TEXT, quote, max_chars=100)  # 인용문이 잘려나간 앞부분 밖
    assert result.truncated is True


def test_worst_case_terminates_within_budget():
    """최악 사례 (SPEC §10): 긴 문서 + 대폭 개편 + 부재 인용문 → 정지 없음."""
    big_text = ("무작위에 가까운 채움 문장이 계속 이어진다. " * 5000) + BASE_TEXT.replace(
        QUOTES[3], ""
    )
    anchor = make_anchor(QUOTES[3])
    started = time.monotonic()
    result = match_anchor(
        big_text,
        exact=anchor.exact,
        prefix="존재하지 않는 앞 문맥",
        suffix="존재하지 않는 뒤 문맥",
        position_hint=len(big_text) // 2,
        budget_ms=100,
    )
    elapsed = time.monotonic() - started
    assert result.state in (MISSING, UNRESOLVED)
    assert elapsed < 2.0  # 예산 100ms + regex 타임아웃 오차 여유
