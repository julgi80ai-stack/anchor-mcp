# SPDX-License-Identifier: Apache-2.0
import pytest

from anchor.anchoring.selector import QUALITY_OK, QUALITY_SHORT, build_selector
from anchor.errors import QuoteNotFound, QuoteTooShort

TEXT = (
    "첫 문단은 배경 설명이다. 조건부 요청과 본문 해시를 함께 쓰면 "
    "재페치와 재파싱을 모두 줄일 수 있다. 마지막 문단은 결론이다."
)


def test_builds_selector_with_context():
    quote = "조건부 요청과 본문 해시를 함께 쓰면 재페치와 재파싱을 모두 줄일 수 있다."
    selector = build_selector(TEXT, quote)
    assert selector.exact == quote
    assert selector.position_hint == TEXT.find(quote)
    assert TEXT[selector.position_hint - len(selector.prefix) : selector.position_hint] == selector.prefix
    assert selector.suffix.startswith(" 마지막")
    assert selector.quality == QUALITY_OK


def test_context_capped_at_context_chars():
    quote = "조건부 요청과 본문 해시를 함께 쓰면 재페치와 재파싱을 모두 줄일 수 있다."
    selector = build_selector(TEXT, quote, context_chars=5)
    assert len(selector.prefix) == 5
    assert len(selector.suffix) == 5


def test_quote_not_found_raises():
    with pytest.raises(QuoteNotFound):
        build_selector(TEXT, "원문에 존재하지 않는 문장이다, 절대로.")


def test_too_short_quote_rejected():
    with pytest.raises(QuoteTooShort):
        build_selector(TEXT, "결론이다.")  # 12자 미만


def test_short_quote_flagged():
    selector = build_selector(TEXT, "마지막 문단은 결론이다.")  # 12~31자
    assert selector.quality == QUALITY_SHORT


def test_quote_is_normalized_before_search():
    # 호출자가 공백을 다르게 넣어도 정규화 후 일치해야 한다.
    selector = build_selector(TEXT, "마지막  문단은   결론이다.")
    assert selector.exact == "마지막 문단은 결론이다."
