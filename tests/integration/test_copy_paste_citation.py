# SPDX-License-Identifier: Apache-2.0
"""실사용 진입 경로 회귀 (D-050 / D-051 / D-052 / D-048 / D-049).

사용자는 브라우저나 PDF 뷰어 화면에서 문장을 복사해 `cite`에 넣는다.
그것이 유일한 진입 경로이므로, 화면에 보이지 않는 것(서식 기호·각주 태그·
조판 줄바꿈·유니코드 공백) 때문에 실패하면 도구를 쓸 수 없다.
"""

from __future__ import annotations

import time

import pytest

from anchor.anchoring.matcher import ALTERED, MISSING, match_anchor
from anchor.anchoring.selector import build_selector
from anchor.errors import QuoteNotFound
from anchor.normalize.extract import to_normalized

# 화면에 보이는 문장 ↔ 그것을 만들어내는 HTML
COPY_PASTE_CASES = [
    (
        "굵게 강조된 구간",
        "<p>The authors conclude that <strong>seventy-five percent</strong> of cited"
        " pages had changed within three years of publication.</p>",
        "The authors conclude that seventy-five percent of cited pages had changed"
        " within three years of publication.",
    ),
    (
        "기울임 강조",
        "<p>This is what we call <em>reference rot</em> in the literature on"
        " scholarly citation practice today.</p>",
        "This is what we call reference rot in the literature on scholarly citation"
        " practice today.",
    ),
    (
        "각주 앞뒤 문장",
        "<p>Roughly three quarters of cited pages had changed<sup>12</sup> within"
        " three years of first publication of the original work.</p>",
        "within three years of first publication of the original work.",
    ),
    (
        "문단 내부 하드랩",
        "<p>The committee concluded that the existing safeguards were\ninsufficient"
        " and recommended an immediate review.</p>",
        "The committee concluded that the existing safeguards were insufficient and"
        " recommended an immediate review.",
    ),
    (
        "프랑스어 NBSP 정서법",
        "<p>La question se pose ainsi&nbsp;: le contenu a-t-il changé&nbsp;? Les"
        " auteurs le pensent fermement.</p>",
        "La question se pose ainsi : le contenu a-t-il changé ? Les auteurs le"
        " pensent fermement.",
    ),
    (
        "영어 숫자 NBSP",
        "<p>The survey covered 10&nbsp;000 documents and Fig.&nbsp;3 understates the"
        " drift observed in practice.</p>",
        "The survey covered 10 000 documents and Fig. 3 understates the drift"
        " observed in practice.",
    ),
    (
        "한국어 하드랩",
        "<p>링크는 살아 있지만 내용이 바뀌는\n인용 표류가 가장 위험하다고 연구진은"
        " 지적했다.</p>",
        "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다고 연구진은 지적했다.",
    ),
]


@pytest.mark.parametrize(("name", "html_body", "screen_text"), COPY_PASTE_CASES)
def test_quote_copied_from_screen_is_citable(name, html_body, screen_text):
    html = f"<html><body><article><h1>Report</h1>{html_body}</article></body></html>"
    doc = to_normalized(html.encode("utf-8"), "text/html; charset=utf-8")
    selector = build_selector(doc.text, screen_text)  # QuoteNotFound면 실패
    assert selector.exact in doc.text


def test_pdf_wrapped_sentence_is_citable():
    """조판 PDF에서 여러 줄에 걸친 완결 문장 (D-052).

    pypdf가 추출한 다행 텍스트를 그대로 재현한다 — 고정 열 폭 조판이라
    문장이 줄을 걸치고 줄 끝 분철이 섞인다.
    """
    from anchor.normalize.text import dehyphenate, normalize_text

    extracted = "\n".join(
        [
            "Recent studies show that roughly seventy-five percent of the method-",
            "ological literature cited by scholarly work had changed within three",
            "years of publication, and the link itself remained perfectly alive.",
        ]
    )
    text = normalize_text(dehyphenate(extracted))
    quote = (
        "roughly seventy-five percent of the methodological literature cited by"
        " scholarly work had changed within three years of publication"
    )
    assert quote in text, text
    build_selector(text, quote)


# -- 언어 형평 ---------------------------------------------------------------

WORD_SWAPS = [
    ("EN", "Artificial intelligence is changing scientific research.", "changing", "reshaping"),
    ("KO", "인공지능은 과학 연구를 바꾸고 있다는 평가가 나온다.", "바꾸고", "재편하고"),
    ("JA", "人工知能は科学研究を変えつつある。", "変えつつ", "再編しつつ"),
    ("ZH", "人工智能正在改变科学研究。", "改变", "重塑"),
]


@pytest.mark.parametrize(("lang", "quote", "before", "after"), WORD_SWAPS)
def test_word_swap_is_altered_in_every_language(lang, quote, before, after):
    """D-049: 같은 성격의 개정이 언어에 따라 MISSING으로 갈리면 안 된다."""
    text = "머리말 문장입니다. " * 3 + quote + " 뒤따르는 마무리 문장입니다."
    selector = build_selector(text, quote)
    edited = text.replace(before, after)
    result = match_anchor(
        edited,
        exact=selector.exact,
        prefix=selector.prefix,
        suffix=selector.suffix,
        position_hint=selector.position_hint,
        budget_ms=200,
    )
    assert result.state == ALTERED, f"{lang}에서 {result.state}"


@pytest.mark.parametrize(("lang", "quote", "before", "after"), WORD_SWAPS)
def test_unrelated_replacement_is_still_missing(lang, quote, before, after):
    """상한을 넓혔다고 오탐이 늘면 안 된다."""
    text = "머리말 문장입니다. " * 3 + quote + " 뒤따르는 마무리 문장입니다."
    selector = build_selector(text, quote)
    removed = text.replace(quote, "전혀 무관한 내용이 여기에 들어왔습니다 정말로.")
    result = match_anchor(
        removed,
        exact=selector.exact,
        prefix=selector.prefix,
        suffix=selector.suffix,
        position_hint=selector.position_hint,
        budget_ms=200,
    )
    assert result.state == MISSING, f"{lang}에서 {result.state}"


def test_chinese_complete_sentence_is_citable():
    """D-049: 완결된 중국어 문장이 QuoteTooShort로 거부되면 안 된다."""
    quote = "气候变化是真实的。"
    text = "引言部分的文字。" * 3 + quote + "后面还有更多内容。"
    selector = build_selector(text, quote)
    assert selector.exact == quote


def test_short_latin_quote_is_still_rejected():
    """반대로 라틴 문자의 지나치게 짧은 인용은 계속 거부되어야 한다."""
    text = "Some leading sentence here. Too short. And more text follows."
    with pytest.raises(Exception):
        build_selector(text, "Too short.")


@pytest.mark.parametrize("language", ["ko", "ja", "en"])
def test_realistic_article_resolves_within_budget(language):
    """D-048: 평범한 길이의 기사에서 UNRESOLVED가 나오면 안 된다."""
    sentences = {
        "ko": "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다고 한다. ",
        "ja": "リンクは生きているが内容が変わる引用のずれが最も危険である。",
        "en": "Citation drift is more dangerous than link rot because nothing breaks. ",
    }[language]
    quote = sentences.strip()
    body = sentences * 250  # 실제 기사 규모
    text = body + quote + " 끝."
    selector = build_selector(text, quote)
    removed = text.replace(quote, "무관한 내용입니다.")

    started = time.monotonic()
    result = match_anchor(
        removed,
        exact=selector.exact,
        prefix=selector.prefix,
        suffix=selector.suffix,
        position_hint=selector.position_hint,
        budget_ms=200,
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    assert result.state != "UNRESOLVED", f"{language}: {elapsed_ms:.0f}ms에 판정 실패"
