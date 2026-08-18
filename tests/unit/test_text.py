# SPDX-License-Identifier: Apache-2.0
"""텍스트 정규화 (SPEC §5.3, NORM_VERSION 2).

핵심 계약: **화면에서 보이는 대로** 저장한다. 화면에 나타나지 않는 것
(마크다운 서식 기호, 추출기가 남긴 태그, 조판용 줄바꿈, 유니코드 공백
변종)은 지우거나 통일해, 사용자가 복사한 인용문이 그대로 발견되게 한다.
"""

from anchor.normalize.text import dehyphenate, normalize_text


def test_collapses_horizontal_whitespace():
    assert normalize_text("가  나\t\t다") == "가 나 다"


def test_strips_trailing_whitespace_per_line():
    assert normalize_text("# 제목  \n\n다라\t") == "# 제목\n\n다라"


def test_collapses_three_plus_newlines_to_two():
    assert normalize_text("가\n\n\n\n나") == "가\n\n나"
    assert normalize_text("가\n\n나") == "가\n\n나"


def test_nfc_normalization():
    decomposed = "école"
    composed = "école"
    assert normalize_text(decomposed) == composed


def test_strips_outer_whitespace():
    assert normalize_text("\n\n 가나 \n\n") == "가나"


# -- D-050 유니코드 공백 -----------------------------------------------------


def test_unicode_spaces_become_plain_space():
    for space in (" ", " ", " ", "　", " "):
        assert normalize_text(f"10{space}000") == "10 000"


def test_unicode_space_runs_collapse_like_ascii():
    assert normalize_text("가   나") == "가 나"


def test_zero_width_characters_are_removed():
    """폭이 없고 **표시에도 관여하지 않는** 문자만 지운다 (D-068에서 범위 축소).

    LRM/RLM은 문단 방향을, ZWJ/ZWNJ는 이모지 시퀀스와 페르시아어 정서법을
    만든다 — 지우면 `get_version`이 내놓는 "인용 당시 텍스트"가 원문과 달라진다.
    보존 쪽 검증은 `test_normalize_rules.py`에 있다.
    """
    assert normalize_text("re​ference­ rot") == "reference rot"


def test_french_nbsp_punctuation_matches_typed_quote():
    stored = normalize_text("La question se pose ainsi : le contenu a-t-il changé ?")
    assert "La question se pose ainsi : le contenu a-t-il changé ?" in stored


# -- D-051 마크다운 서식·잔존 태그 -------------------------------------------


def test_emphasis_markers_are_stripped():
    assert normalize_text("**seventy-five percent** of pages") == "seventy-five percent of pages"
    assert normalize_text("we call *reference rot* here") == "we call reference rot here"


def test_underscore_is_left_alone():
    assert normalize_text("call some_helper_name(x)") == "call some_helper_name(x)"


def test_leftover_html_tags_are_stripped():
    assert normalize_text("had changed<sup>12</sup> within") == "had changed12 within"


def test_prose_comparison_signs_survive():
    assert normalize_text("if a < b and c > d then") == "if a < b and c > d then"


# -- D-052 조판 줄바꿈 -------------------------------------------------------


def test_hard_wrapped_prose_is_joined():
    text = "The committee concluded that the\nsafeguards were insufficient."
    assert normalize_text(text) == "The committee concluded that the safeguards were insufficient."


def test_structural_lines_are_not_joined():
    text = "# 제목\n- 첫 항목\n- 둘째 항목\n> 인용\n| 표 |"
    assert normalize_text(text) == text


def test_paragraph_breaks_survive():
    assert normalize_text("첫 문단이다.\n\n둘째 문단이다.") == "첫 문단이다.\n\n둘째 문단이다."


def test_code_fence_contents_are_untouched():
    text = "```\nline one\nline two\n```"
    assert normalize_text(text) == text


def test_korean_lines_join_with_space():
    assert (
        normalize_text("인용 표류가 가장 위험하다는\n점을 연구진은 확인했다.")
        == "인용 표류가 가장 위험하다는 점을 연구진은 확인했다."
    )


def test_japanese_and_chinese_lines_join_without_space():
    assert (
        normalize_text("気候変動は実在し、モデルの\n予測よりも速く加速している。")
        == "気候変動は実在し、モデルの予測よりも速く加速している。"
    )
    assert (
        normalize_text("气候变化是真实的，并且比模型\n预测的更快加速。")
        == "气候变化是真实的，并且比模型预测的更快加速。"
    )


def test_dehyphenate_is_pdf_only_helper():
    assert dehyphenate("the method-\nological literature") == "the methodological literature"
    assert dehyphenate("Seoul-\nBusan line") == "Seoul-\nBusan line"


# -- 멱등성 -----------------------------------------------------------------


def test_normalization_is_idempotent():
    samples = [
        "# 제목\n\n**굵게** 쓴 문단이\n여러 줄로 접혀 있다.\n\n- 목록\n- 항목",
        "10 000 and Fig. 3 with <sup>1</sup> markers",
        "気候変動は実在し、モデルの\n予測よりも速く加速している。",
        "```\ncode\nblock\n```\n\n뒤 문단.",
    ]
    for sample in samples:
        once = normalize_text(sample)
        assert normalize_text(once) == once, sample
