# SPDX-License-Identifier: Apache-2.0
"""속성 테스트 (SPEC §12): Hypothesis(라이브러리)로 무작위 입력 검증.

핵심 속성 — 임의 텍스트에 대해 cite → verify(동일 버전) == INTACT.
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from anchor.anchoring.matcher import INTACT, match_anchor
from anchor.anchoring.selector import build_selector
from anchor.fetcher.urlnorm import normalize_url
from anchor.normalize.text import normalize_text

# 한글·라틴·숫자·공백이 섞인 본문 생성용
_ALPHABET = st.characters(
    codec="utf-8",
    categories=("Lu", "Ll", "Lo", "Nd", "Zs"),
    include_characters=" \n.,",
)


@settings(max_examples=150, deadline=1000)
@given(data=st.data(), raw_text=st.text(alphabet=_ALPHABET, min_size=60, max_size=1500))
def test_cite_then_verify_same_version_is_intact(data, raw_text):
    text = normalize_text(raw_text)
    assume(len(text) >= 40)

    start = data.draw(st.integers(0, len(text) - 13))
    length = data.draw(st.integers(12, min(120, len(text) - start)))
    quote = text[start : start + length]
    assume(len(normalize_text(quote)) >= 12)
    assume(normalize_text(quote) in text)

    selector = build_selector(text, quote)
    result = match_anchor(
        text,
        exact=selector.exact,
        prefix=selector.prefix,
        suffix=selector.suffix,
        position_hint=selector.position_hint,
        budget_ms=200,
    )
    assert result.state == INTACT
    assert result.score == 1.0


@settings(max_examples=200, deadline=500)
@given(raw_text=st.text(max_size=2000))
def test_normalize_text_is_idempotent(raw_text):
    once = normalize_text(raw_text)
    assert normalize_text(once) == once


@settings(max_examples=100, deadline=500)
@given(
    host=st.from_regex(r"[a-z][a-z0-9\-]{0,20}(\.[a-z]{2,6}){1,2}", fullmatch=True),
    path=st.from_regex(r"(/[A-Za-z0-9\-_.]{0,12}){0,4}", fullmatch=True),
    query=st.from_regex(r"([a-z]{1,8}=[A-Za-z0-9]{0,8}(&[a-z]{1,8}=[A-Za-z0-9]{0,8}){0,3})?", fullmatch=True),
)
def test_normalize_url_is_idempotent(host, path, query):
    url = f"https://{host}{path}" + (f"?{query}" if query else "")
    once = normalize_url(url)
    assert normalize_url(once) == once
