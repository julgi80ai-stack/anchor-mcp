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


# -- D-074: 화면 선택이 블록 하나를 넘어갈 때 --------------------------------
#
# 사람이 브라우저에서 두 문단·두 목록 항목·주소 블록을 한 번에 끌어 복사하는 것은
# 흔한 행동이다. 그런데 저장 본문의 블록 구분자(빈 줄·줄바꿈·목록 표지)와 복사문의
# 구분자(줄바꿈 하나 또는 공백)가 달라, 그 인용은 전부 `QuoteNotFound`가 됐다.

TWO_PARAGRAPHS = (
    "서론 문단이다. 배경을 설명한다.\n\n"
    "협의는 2026년 9월 15일에 마감된다. 제출된 의견은 모두 공개된다.\n\n"
    "결론 문단이다."
)


@pytest.mark.parametrize(
    "separator",
    ["\n", "\n\n", " "],
    ids=["줄바꿈 하나", "빈 줄", "공백"],
)
def test_quote_spanning_two_blocks_is_anchored(separator):
    quote = f"서론 문단이다. 배경을 설명한다.{separator}협의는 2026년 9월 15일에 마감된다."
    selector = build_selector(TWO_PARAGRAPHS, quote)
    # 앵커의 exact 는 **저장 본문에 실재하는 문자열**이어야 한다 (매칭 1·2단계 전제)
    assert TWO_PARAGRAPHS.find(selector.exact) == selector.position_hint
    assert "협의는 2026년 9월 15일에 마감된다" in selector.exact


LIST_BODY = (
    "다음 두 가지가 바뀐다.\n\n"
    "- 협의 기간이 30일에서 45일로 늘어난다.\n"
    "- 제출된 의견은 접수 후 10일 안에 공개된다.\n\n"
    "시행일은 별도로 공지한다."
)


@pytest.mark.parametrize("separator", ["\n", " "], ids=["줄바꿈", "공백"])
def test_quote_spanning_two_list_items_is_anchored(separator):
    """화면에서 목록 두 항목을 끌면 `- ` 표지는 복사되지 않는다."""
    quote = (
        "협의 기간이 30일에서 45일로 늘어난다."
        f"{separator}제출된 의견은 접수 후 10일 안에 공개된다."
    )
    selector = build_selector(LIST_BODY, quote)
    assert TWO_PARAGRAPHS.find(selector.exact) == -1  # 다른 본문이므로
    assert LIST_BODY.find(selector.exact) == selector.position_hint
    assert "접수 후 10일 안에 공개된다" in selector.exact


def test_quote_with_collapsed_whitespace_is_anchored():
    """복사 과정에서 공백이 뭉개져도 같은 문장이면 찾아야 한다."""
    selector = build_selector(TWO_PARAGRAPHS, "협의는  2026년 9월 15일에\n마감된다.")
    assert TWO_PARAGRAPHS.find(selector.exact) == selector.position_hint


def test_quote_that_is_really_absent_still_fails():
    """유연해진 것이 날조를 허용하는 쪽으로 새면 안 된다 (SPEC §6.1의 기본 계약)."""
    with pytest.raises(QuoteNotFound):
        build_selector(TWO_PARAGRAPHS, "협의는 2026년 9월 30일에 마감된다.")


# -- 유연 검색이 열어 버린 구멍 (1단계 조치 감사) -----------------------------


def test_quote_that_collapses_to_a_single_character_is_rejected():
    """길이 게이트와 **실제로 탐색하는 문자열**이 갈리면 안 된다.

    검색형은 공백과 목록 표지를 걷어내므로, 12자 하한을 통과한 인용문이 1자짜리
    앵커가 될 수 있다. 그런 앵커는 아무 문서에나 걸려 판정이 무의미해진다 —
    존재하지 않는 인용을 기록하지 않는다는 기본 계약이 뚫린다.
    """
    body = "Some ordinary article body with a hyphen - inside it, long enough to matter."
    with pytest.raises((QuoteTooShort, QuoteNotFound)):
        build_selector(body, "- - - - - - - - - - - -")


def test_gate_counts_the_string_that_is_actually_searched():
    body = "머리말 문단이다. 본론은 다음과 같다.\n\n- 첫째 항목\n- 둘째 항목\n\n맺음말이다."
    with pytest.raises((QuoteTooShort, QuoteNotFound)):
        build_selector(body, "-  -  -  -  -  -  -")


CJK_BODY = (
    "この文書は日本語で書かれている。目的は引用の再検証である。\n\n"
    "中央のサービスとして作られた基盤は、予算が尽きた瞬間に一緒に消える。\n\n"
    "結論はここに置く。"
)


@pytest.mark.parametrize("separator", ["\n", "\n\n", "", " "], ids=["줄바꿈", "빈 줄", "없음", "공백"])
def test_quote_spanning_two_blocks_is_anchored_in_cjk_documents(separator):
    """일본어·중국어 문서에서도 두 문단 인용이 성립해야 한다.

    본문 쪽은 블록을 공백 없이 잇고 검색형은 공백 하나로 접었기 때문에, 두
    형태가 구조적으로 만나지 못했다 — CJK 문서에서만 D-074가 100% 실패했다.
    """
    quote = (
        "この文書は日本語で書かれている。目的は引用の再検証である。"
        f"{separator}中央のサービスとして作られた基盤は、予算が尽きた瞬間に一緒に消える。"
    )
    selector = build_selector(CJK_BODY, quote)
    assert CJK_BODY.find(selector.exact) == selector.position_hint


def test_han_quote_inside_a_korean_document_is_anchored():
    """인용문의 문자 체계와 문서의 문자 체계가 따로 판정되면 안 된다.

    한국어 문서 안의 한자 인용구는 인용문만 보면 CJK로 판정돼 공백이 지워지고,
    본문에는 공백이 남아 있어 영영 만나지 못했다.
    """
    body = "이 논문은 論語 孟子 大學 中庸 네 문헌을 대상으로 삼는다. 나머지는 후속 연구로 남긴다."
    selector = build_selector(body, "論語 孟子 大學 中庸 네 문헌을 대상으로 삼는다")
    assert body.find(selector.exact) == selector.position_hint


HEADING_BODY = (
    "Background paragraph that sets up the question and does not itself qualify.\n\n"
    "## Interim heading\n\n"
    "Conclusion paragraph that closes the section and states the finding."
)


@pytest.mark.parametrize("separator", ["\n", "\n\n", " "], ids=["줄바꿈", "빈 줄", "공백"])
def test_selection_crossing_a_heading_is_anchored(separator):
    """브라우저는 `##` 표지를 클립보드에 넣지 않는다 — 절을 통째로 복사하는 제스처."""
    quote = (
        "Background paragraph that sets up the question and does not itself qualify."
        f"{separator}Interim heading{separator}"
        "Conclusion paragraph that closes the section and states the finding."
    )
    selector = build_selector(HEADING_BODY, quote)
    assert HEADING_BODY.find(selector.exact) == selector.position_hint


def test_selection_crossing_a_blockquote_is_anchored():
    body = "Lead paragraph here.\n\n> Quoted line inside the article.\n\nTrailing paragraph."
    selector = build_selector(body, "Lead paragraph here.\nQuoted line inside the article.")
    assert body.find(selector.exact) == selector.position_hint


def test_quote_not_found_message_points_at_the_stored_text():
    """날조 거부와 '저장하지 않은 영역'을 구분해 말해야 한다 (D-073)."""
    with pytest.raises(QuoteNotFound) as caught:
        build_selector(TEXT, "이 문장은 본문 어디에도 존재하지 않는 문장이다.")
    message = str(caught.value)
    assert "get_version" in message
    assert "저장" in message


def test_occurrences_counts_repeats_for_the_cite_warning():
    """중복 출현 경고(D-047)가 회귀선 없이 사라지지 않도록 고정한다."""
    body = "같은 문장이 두 번 나온다. 사이 문장이다. 같은 문장이 두 번 나온다."
    selector = build_selector(body, "같은 문장이 두 번 나온다.")
    assert selector.occurrences == 2
