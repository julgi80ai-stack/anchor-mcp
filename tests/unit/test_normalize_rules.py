# SPDX-License-Identifier: Apache-2.0
"""정규화 규칙의 동작 고정 (2차 감사 군집 1, D-055~D-076).

정규화는 **모든 해시와 앵커의 기준**이다. 여기서 틀리면 저장되는 증거 자체가
원문이 아니게 되고, 그 위에 얹힌 모든 판정이 함께 틀린다.

규칙의 목적은 둘이고 서로 반대 방향으로 당긴다:
  ① 화면에서 복사한 문장이 저장 본문에서 발견될 것 (NORM_VERSION 2의 동기)
  ② 화면에 있는 것을 지우지 말 것 (2차 감사가 찾아낸 반대편 실패)
이 파일은 ②를 고정한다 — ①만 보고 만든 규칙이 무엇을 부쉈는지가 여기 있다.
"""

from __future__ import annotations

import unicodedata

import pytest

from anchor.normalize.text import normalize_text


# -- D-055: 태그 모양 토큰이 본문을 삼킨다 -----------------------------------


def test_angle_bracket_in_prose_does_not_delete_following_text():
    """`<`와 `>` 사이의 본문이 통째로 사라지면 안 된다 (개행을 가로지르며 삭제했다)."""
    document = (
        "첫 문단은 <b 로 시작하면 이렇게 된다.\n\n"
        "둘째 문단이다. 위원회는 2026년 3월에 최종 보고서를 발표하기로 의결했다.\n\n"
        "셋째 문단이다.\n\n"
        "변수 이름이 a>b 인 경우를 다룬다.\n\n"
        "다섯 번째 문단은 결론이다."
    )
    result = normalize_text(document)
    assert "위원회는 2026년 3월에 최종 보고서를 발표하기로 의결했다" in result
    assert "셋째 문단이다" in result
    assert "다섯 번째 문단은 결론이다" in result


def test_element_names_in_prose_survive():
    """산문에 적힌 요소명은 본문이다 — 지우면 개정을 감지하지 못한다.

    `<updated>` → `<published>` 개정이 같은 문자열로 붕괴하면 verify가 INTACT를
    보고한다(거짓 안심). 실제 문서 부류: API 문서·XML 스펙·마크업 튜토리얼.
    """
    prose = "The <updated> element is required on both <feed> and <entry>."
    assert normalize_text(prose) == prose

    revised = "The <published> element is required on both <feed> and <entry>."
    assert normalize_text(prose) != normalize_text(revised)


def test_generic_type_parameters_survive():
    assert normalize_text("반환값은 List<String> 이다.") == "반환값은 List<String> 이다."


def test_formatting_only_tags_are_still_removed():
    """D-051이 실제로 겨냥한 것 — 각주 `<sup>`는 화면에 글자로 보이지 않는다."""
    assert normalize_text("본문 각주<sup>12</sup> 뒤 문장") == "본문 각주12 뒤 문장"
    assert normalize_text("<strong>굵게</strong> 강조") == "굵게 강조"
    assert normalize_text("줄바꿈<br>다음") == "줄바꿈 다음"


# -- D-056: 코드 블록 훼손 ---------------------------------------------------


def test_code_fence_contents_are_untouched():
    document = (
        "다음 요소를 쓴다.\n\n"
        "```\n"
        '<input type="number" min="0" max="10">\n'
        '<input type="text" required>\n'
        "```\n\n"
        "끝."
    )
    result = normalize_text(document)
    assert '<input type="number" min="0" max="10">' in result
    assert '<input type="text" required>' in result


def test_code_fence_indentation_is_preserved():
    """들여쓰기가 의미인 본문을 뭉개면 돌려준 코드가 문법적으로 무효가 된다."""
    document = "```python\ndef f():\n    if x:\n        return 1\n```"
    result = normalize_text(document)
    assert "    if x:" in result
    assert "        return 1" in result


def test_inline_code_span_is_untouched():
    assert normalize_text("`a**b**c` 는 파이썬 표현식이다") == "`a**b**c` 는 파이썬 표현식이다"


# -- D-057: 산문의 별표 ------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "면적은 2*3*4 = 24 이다.",
        "결과는 2**3**2 = 512 이다.",
        "패턴 a*b*c 는 세 글자를 뜻한다.",
        "SELECT * FROM t",
        "rm *.txt *.log",
        "a * b",
        "void f(char *src, char *dst);",
    ],
)
def test_asterisks_in_prose_survive(source):
    """원문에 없던 수치를 만들어내면 안 된다 — `2*3*4` → `234`."""
    assert normalize_text(source) == source


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("**주의**: 확인이 필요하다", "주의: 확인이 필요하다"),
        ("*기울임* 문장이다", "기울임 문장이다"),
        ("문장 끝에 **강조**.", "문장 끝에 강조."),
        ("(**중요**) 괄호 안", "(**중요**) 괄호 안".replace("**", "")),
    ],
)
def test_markdown_emphasis_is_removed(source, expected):
    assert normalize_text(source) == expected


# -- D-058: NFC 순서 ---------------------------------------------------------


def test_output_is_nfc_even_when_characters_were_removed():
    """폭 없는 문자를 지우면 결합 문자가 인접한다 — NFC를 그 뒤에 해야 한다.

    화면 복사문은 OS·브라우저가 NFC로 주므로, 저장 본문이 결합형이면
    `text.find(exact)`가 실패한다.
    """
    source = "cafe\u200b\u0301"  # 'cafe' + ZWSP + combining acute
    result = normalize_text(source)
    assert result == "caf\u00e9"
    assert unicodedata.is_normalized("NFC", result)


def test_hangul_jamo_with_intervening_format_character():
    result = normalize_text("\u1100\u200b\u1161")
    assert result == "\uac00"


# -- D-059: 멱등성 -----------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "<<b>a>",
        "<p<a>>",
        "****bold****",
        "설정은 <server<host>> 형식이다",
        "cafe\u200b\u0301",
        "**<sup>1</sup>**",
        "a\u200b\u0301b\u200c\u0301",
    ],
)
def test_normalization_is_idempotent(source):
    once = normalize_text(source)
    assert normalize_text(once) == once


# 퍼징의 **생성 모델**이 검정력을 정한다. 문자 단위 균등 추출은 `<em>`·`**a**`
# 같은 토큰을 사실상 만들지 못해, 20만 회를 돌려도 이 부류의 위반을 5건밖에
# 못 잡는다(실측 0.0025%). 정규화가 다루는 것은 문자가 아니라 **토큰과 줄**이므로
# 생성기도 그 단위여야 한다 — 토큰 단위로 바꾸면 같은 결함이 0.4%로 나온다.
_FUZZ_TOKENS = [
    "<em>", "</em>", "<b>", "</b>", "<sup>", "</sup>", "<span>", "</span>",
    "<updated>", "<feed>", "**", "*", "`", "```", "~~~",
    "# ", "## ", "- ", "* ", "1. ", "12) ", "> ", "|", "||", "[^1]: ", "[server]",
    "---", "===", "    ", "\t", " ", "  ",
    "a", "ab", "가", "가나", "漢", "漢字", "あ", "アア", "1", "2026", ".", ",", ":", "=",
    "\u200b", "\u200c", "\u200d", "\u0301", "\u00a0", "\u2028", "\ufeff", "\u00ad",
]


def test_normalization_is_idempotent_under_token_fuzzing():
    import random

    rng = random.Random(20260818)
    violations = []
    for _ in range(30000):
        lines = []
        for _ in range(rng.randint(1, 6)):
            lines.append("".join(rng.choice(_FUZZ_TOKENS) for _ in range(rng.randint(1, 8))))
        source = "\n".join(lines)
        once = normalize_text(source)
        if normalize_text(once) != once:
            violations.append(source)
            if len(violations) >= 3:
                break
    assert not violations, f"멱등성 위반: {violations!r}"


# -- D-060/D-063/D-064: 줄 접기가 구조를 훼손한다 ----------------------------


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("YAML front matter", "---\ntitle: Foo\nauthor: Bar\n---"),
        ("setext 제목", "Chapter One\n==========="),
        ("리드 없는 표", "Name | Age |\n-----|----|\nBob | 3 |"),
        ("각주 정의", "[^1]: First note.\n[^2]: Second note."),
        ("INI", "[server]\nhost=example.com\nport=8080"),
        ("4칸 들여쓴 코드", "    def foo():\n        return 1"),
        (
            "로그",
            "2026-08-18 10:00:01 INFO 시작\n2026-08-18 10:00:02 WARN 지연\n"
            "2026-08-18 10:00:03 INFO 완료",
        ),
        ("CSV", "name,age,city\nBob,3,Seoul\nAmy,4,Busan"),
    ],
)
def test_structured_lines_are_not_folded(name, source):
    """산문이 아닌 줄을 이으면 인접하지 않던 값이 이웃이 되어 없던 문장이 생긴다."""
    result = normalize_text(source)
    assert result.count("\n") >= source.count("\n"), f"{name}: 줄이 접혔다 — {result!r}"


def test_hard_wrapped_prose_is_folded():
    """조판 줄바꿈은 접어야 한다 — NORM_VERSION 2가 도입된 이유."""
    source = (
        "조건부 요청과 본문 해시를 함께 쓰면 재페치와 재파싱을 모두 줄일 수\n"
        "있다. 정규화된 본문을 기준으로 해시하면 광고 노이즈에 속지 않는다."
    )
    result = normalize_text(source)
    assert "줄일 수 있다" in result
    assert "\n" not in result


def test_continuation_line_starting_with_date_is_folded():
    """한국어 표준 날짜 표기가 줄머리에 오는 것은 흔하다 (`2026. 3. 15.`)."""
    source = "최종 개정 시점은 아주 늦은 시기였다고 알려져 있으며 정확히는\n2026. 3. 15. 이었다고 밝혔다."
    assert "정확히는 2026. 3. 15. 이었다고" in normalize_text(source)


def test_continuation_line_starting_with_operator_is_folded():
    source = "두 조건을 모두 만족해야 하므로 논리 연산자는 다음과 같이\n|| 를 쓴다."
    assert "다음과 같이 || 를 쓴다." in normalize_text(source)


def test_list_item_continuation_lines_are_folded_consistently():
    """첫 계속 줄만 안 접히고 두 번째부터 접히는 비일관을 없앤다."""
    source = (
        "- 첫 번째 항목인데 문장이 길어서 다음 줄로\n"
        "  넘어갔다. 여기까지가 한 항목이다.\n"
        "- 두 번째 항목이다."
    )
    result = normalize_text(source)
    assert "다음 줄로 넘어갔다. 여기까지가 한 항목이다." in result
    assert result.count("\n- ") == 1 or result.count("\n") == 1


# -- D-061: 접합 시 공백을 넣을지 -------------------------------------------


def test_japanese_paragraph_joins_without_space_even_at_latin_boundary():
    """일본어·중국어는 라틴 단어 뒤에서 줄을 바꾸는 것이 보통이다.

    경계 문자 한 글자만 보면 없던 ASCII 공백이 삽입돼 화면 복사 인용이 깨진다.
    """
    source = "まずこの節では設定の全体像を説明する。具体的には HTTP\nリクエストヘッダを設定する必要がある。"
    result = normalize_text(source)
    assert "HTTPリクエストヘッダ" in result


def test_korean_paragraph_joins_with_space_at_han_boundary():
    """한국어는 어절을 띄우므로 한자 경계에서도 공백이 필요하다."""
    source = "이 조문은 아래에서 보듯이 여러 차례 개정을 거쳤는데 그 근거는 大韓民國\n憲法 제1조에 있다."
    result = normalize_text(source)
    assert "大韓民國 憲法 제1조" in result


# -- D-065: 화면에서 줄바꿈으로 보이는 문자들 --------------------------------


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\r", "\u0085", "\u000b", "\u000c"])
def test_unicode_line_separators_become_newlines(separator):
    result = normalize_text(f"앞 문장이다.{separator}뒤 문장이다.")
    assert separator not in result
    assert "앞 문장이다." in result and "뒤 문장이다." in result


def test_crlf_does_not_leave_stray_carriage_returns():
    assert "\r" not in normalize_text("첫 줄이다.\r\n둘째 줄이다.")


# -- D-068: 폭 없는 문자 중 표시에 관여하는 것 -------------------------------


def test_zero_width_joiner_and_nonjoiner_are_preserved():
    """ZWJ는 이모지 시퀀스를, ZWNJ는 페르시아어 정서법을 만든다 — 글자다."""
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    assert normalize_text(f"우리 {family} 사진") == f"우리 {family} 사진"
    assert normalize_text("او می\u200cرود") == "او می\u200cرود"


def test_invisible_zero_width_characters_are_removed():
    assert normalize_text("보이지\u200b않는\ufeff문자") == "보이지않는문자"


# -- D-067: 줄 끝 하이픈·URL -------------------------------------------------


def test_url_split_across_lines_is_not_broken_by_a_space():
    source = "자세한 내용은 아래 주소에서 확인할 수 있다 https://example.org/a/\nb/c 를 보라."
    assert "https://example.org/a/b/c" in normalize_text(source)


def test_emphasis_followed_by_a_particle_is_removed():
    """한국어·일본어는 조사가 강조 바로 뒤에 붙는다 — 닫는 쪽 경계는 느슨해야 한다."""
    assert normalize_text("**굵게**도 있다") == "굵게도 있다"
    assert normalize_text("*기울임*을 쓴다") == "기울임을 쓴다"


def test_short_setext_underline_is_structural():
    """markdownify는 제목 길이에 맞춰 밑줄을 낸다 — 두 글자 제목이면 `==`."""
    assert normalize_text("제목\n==\n\n본문이다.").startswith("제목\n==")


def test_space_between_cjk_characters_is_dropped_in_cjk_documents():
    """추출기가 CJK 줄바꿈을 공백으로 바꿔 내보내면 화면과 어긋난다 (D-061).

    브라우저는 CJK 사이의 줄바꿈을 공백 없이 렌더하므로(CSS Text 3), 화면에서
    복사한 인용문에는 그 공백이 없다.
    """
    japanese = "この文書は日本語の本文を扱う。行の折り返しが 欧文語の直後で起きる。"
    assert "折り返しが欧文語の直後" in normalize_text(japanese)


def test_space_between_latin_and_cjk_is_kept():
    """라틴 낱말과 CJK 사이의 공백은 브라우저도 렌더한다 — 지우면 안 된다."""
    japanese = "設定では HTTP リクエストヘッダを明示する必要がある。規格は改訂された。"
    assert "HTTP リクエストヘッダ" in normalize_text(japanese)


def test_korean_documents_keep_spaces_between_han_characters():
    korean = "이 조문은 大韓民國 憲法 제1조에 근거한다. 나머지는 하위 법령에 위임한다."
    assert "大韓民國 憲法" in normalize_text(korean)


# -- 1단계 조치 감사에서 나온 것 ---------------------------------------------


def test_tag_names_in_prose_survive_even_when_whitelisted():
    """화이트리스트 **안에 든 이름**을 다루는 산문도 본문이다.

    접근성·HTML 문서가 정확히 이 부류다. 권고가 `<strong>`에서 `<b>`로 바뀐
    개정이 같은 문자열로 붕괴하면 verify가 거짓 INTACT를 준다.
    """
    v1 = "Use the <strong> element for text with strong importance."
    v2 = "Use the <b> element for text with strong importance."
    assert normalize_text(v1) == v1
    assert normalize_text(v1) != normalize_text(v2)
    assert normalize_text("Prefer <em> over <i> here.") != normalize_text("Prefer <i> over <em> here.")


def test_paired_formatting_tags_are_still_removed():
    """짝을 이룬 서식 태그는 화면에 글자로 보이지 않는다 — D-051의 대상."""
    assert normalize_text("본문 각주<sup>12</sup> 뒤") == "본문 각주12 뒤"
    assert normalize_text("<strong>굵게</strong> 강조") == "굵게 강조"
    assert normalize_text("<em>기울임</em>도 있다") == "기울임도 있다"


def test_space_aligned_table_rows_are_not_folded():
    """PDF 표는 공백으로 열을 맞춘다 — 접히면 없던 인접이 만들어진다."""
    source = (
        "Region      2024      2025\n"
        "Europe      41.2      38.7\n"
        "Asia        55.9      61.3\n"
        "Africa      12.0      13.4"
    )
    result = normalize_text(source)
    assert result.count("\n") >= 3, f"표가 접혔다 — {result!r}"


# -- 조치가 세운 전제를 지키는 회귀선 (되돌림 검증에서 비어 있던 자리) --------


def test_inline_code_protects_whitelisted_tags_and_emphasis():
    """인라인 코드 안은 서식이 아니라 글자다.

    바깥에서라면 지워질 형태(짝을 이룬 태그, 어절 경계의 강조)로 시험해야
    보호가 실제로 작동하는지 알 수 있다.
    """
    assert normalize_text("`<b>x</b>` 를 보라") == "`<b>x</b>` 를 보라"
    assert normalize_text("`a **y** b` 는 예제다") == "`a **y** b` 는 예제다"


def test_code_fence_keeps_spaces_between_cjk_in_cjk_documents():
    """CJK 공백 제거는 산문 규칙이다 — 코드 안에서는 공백도 글자다."""
    document = "この文書は日本語である。説明を続ける。\n\n```\n漢字 漢字\n```\n\n終わり。"
    assert "漢字 漢字" in normalize_text(document)


def test_dangling_dash_joins_with_a_space():
    """줄 끝 하이픈 무공백 접합은 **분철**에만 적용된다.

    앞에 낱말 문자가 있어야 한다는 가드가 없으면 문장 부호로 쓰인 하이픈이
    다음 줄을 붙여버리고, 그 결과가 더는 같은 종류로 분류되지 않아 멱등성이
    깨진다.
    """
    result = normalize_text("some words that end with a dash -\ncontinuation here")
    assert "dash - continuation here" in result


def test_list_item_run_is_judged_without_its_marker_line():
    """레코드 판정은 목록 표지 줄을 빼고 **계속 줄만** 봐야 한다.

    표지 줄을 함께 세면 그 줄의 모양 때문에 판정이 뒤집혀, 레코드인 계속 줄들이
    한 줄로 접힌다.
    """
    result = normalize_text("- alpha\n1,2\n3,4")
    assert result.count("\n") >= 2, f"레코드 줄이 접혔다 — {result!r}"
