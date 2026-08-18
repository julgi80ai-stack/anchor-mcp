# SPDX-License-Identifier: Apache-2.0
"""앵커 판정이 **사실을 말하는가** (2차 감사 D-109~D-115).

판정 오류는 조용하다. 거짓 MISSING은 멀쩡한 인용을 죽었다고 하고, 거짓
ALTERED는 **다른 절의 닮은 남**을 "당신 인용문의 현재 모습"으로 내민다.
둘 다 사용자가 확인할 방법이 없다 — 그래서 여기가 이 프로젝트의 급소다.
"""

from __future__ import annotations

import time

import pytest

from anchor.anchoring.approx import (
    best_substring_match,
    bounded_edit_distance,
    fuzzy_search_myers,
)
from anchor.anchoring.budget import Budget
from anchor.anchoring.matcher import ALTERED, INTACT, MISSING, MOVED, UNRESOLVED, match_anchor


def _match(text: str, exact: str, **kwargs):
    kwargs.setdefault("prefix", "")
    kwargs.setdefault("suffix", "")
    kwargs.setdefault("position_hint", 0)
    kwargs.setdefault("budget_ms", 2000.0)
    return match_anchor(text, exact=exact, **kwargs)


# -- D-109: 코어 전역 최소 하나만 보는 탓의 거짓 MISSING ----------------------


def _decoy_document() -> tuple[str, str]:
    """앞·뒤 코어의 **전역 최적이 둘 다 디코이에 떨어지는** 문서.

    요약절이 인용문의 앞머리를 그대로 옮기고, 풀인용이 꼬리를 그대로 옮기는
    것은 기사·릴리스노트의 평범한 편집이다. 그 둘이 본문보다 앞에 오면
    코어 스캔의 전역 최소가 양쪽 다 디코이가 된다.
    """
    quote = (
        "위원회는 2026년 회계연도 예산안에서 기초연구 지원 항목을 전년 대비 12퍼센트 "
        "증액하기로 의결했으며, 이는 최근 5년간 가장 큰 폭의 조정이다."
    )
    assert len(quote) > 64, "코어가 둘로 갈리려면 64자를 넘겨야 한다"
    head, tail = quote[:64], quote[-64:]
    revised = quote.replace("12퍼센트", "15퍼센트").replace("5년간", "7년간")
    assert revised != quote

    text = (
        "요약\n"
        f"{head}\n\n"
        "관련 발언\n"
        f"“{tail}”\n\n"
        "본문\n"
        f"{revised}\n"
    )
    return text, quote


def test_decoy_windows_do_not_hide_the_real_paragraph():
    """디코이 창 때문에 진짜 문단을 놓치면 안 된다 (D-109).

    `myers_scan`이 코어마다 전역 최소 **하나만** 돌려주면 후보 창이 전부
    디코이라 진짜 위치는 검사조차 되지 않는다 — 인용문이 64자를 넘으면
    regex 구제도 없는 경로라 결과는 곧바로 거짓 MISSING이다.
    """
    text, quote = _decoy_document()
    result = _match(text, quote)
    assert result.state == ALTERED, f"진짜 문단을 놓쳤다 (판정: {result.state})"
    assert result.edit_distance == 2
    assert "15퍼센트" in (result.found_text or "")


def test_decoy_windows_do_not_hide_it_at_the_search_layer():
    """같은 사실을 검색 계층에서 직접 확인한다 — 매처 단계 구성과 무관하게."""
    text, quote = _decoy_document()
    found = fuzzy_search_myers(text, quote, 18, Budget(2000.0))
    assert found is not None, "코어 스캔이 진짜 위치의 창을 후보에 넣지 못했다"
    assert found.edit_distance == 2
    assert text[found.offset : found.offset + len(found.found_text)] == found.found_text


# -- D-110: 3단계 후보 상한 ---------------------------------------------------


def _template_document(true_index: int, blocks: int = 40) -> tuple[str, str, str, str]:
    """같은 문맥이 반복되는 문서(약관 조항·변경이력·FAQ의 평범한 모습)."""
    prefix = "\n\n조항 본문 —\n"
    suffix = "\n(개정 이력 참조)\n"
    quote = "본 조항은 서비스 이용자가 제공한 개인정보를 제3자에게 이전하지 아니한다"
    parts = []
    for index in range(blocks):
        if index == true_index:
            body = quote.replace("아니한다", "않는다")  # 거리 2
        else:
            body = quote.replace("제3자", f"제{index}자").replace("개인정보", "이용기록")
        parts.append(f"제{index}조{prefix}{body}{suffix}")
    return "".join(parts), quote, prefix, suffix


def test_context_candidates_are_not_cut_off_at_thirty_two():
    """진짜 문단이 33번째면 앞쪽 형제가 "현재 모습"으로 보고된다 (D-110).

    SPEC §6.2 3단계는 후보를 **전부** 훑으라고 적혀 있다. 상한을 두는 것까지는
    구현의 자유지만, 상한에 걸린 채 **32개 중 최선을 확정**하는 것은 다르다 —
    D-044가 고친 실패 모드가 경계만 1에서 32로 옮겨진 것이다.
    """
    text, quote, prefix, suffix = _template_document(true_index=37)
    result = match_anchor(
        text,
        exact=quote,
        prefix=prefix,
        suffix=suffix,
        position_hint=0,
        budget_ms=4000.0,
    )
    assert result.state == ALTERED
    assert "않는다" in (result.found_text or ""), "형제 문단을 골랐다"
    assert "제3자" in (result.found_text or ""), "형제 문단을 골랐다"


def test_context_search_still_finds_an_early_match():
    """상한을 올려도 앞쪽에 있는 진짜 문단을 그대로 찾는다 (회귀 방지)."""
    text, quote, prefix, suffix = _template_document(true_index=2)
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=0, budget_ms=4000.0
    )
    assert result.state == ALTERED
    assert "않는다" in (result.found_text or "")


# -- D-111: 예산 초과량이 인용문 길이에 비례 ---------------------------------


@pytest.mark.parametrize("length", [4_000, 20_000])
def test_edit_distance_budget_overshoot_does_not_grow_with_length(length):
    """예산 확인이 DP **행**마다면 초과량이 인용문 길이에 비례한다 (D-111).

    한 행의 비용이 `O(len(b))`라 확인 주기 64행 사이의 일이 길이에 비례해
    커진다. §10은 앵커당 p99 250ms를 약속하는데 `cite`에 길이 상한이 없어
    공개 API로 그냥 도달한다.
    """
    a = "가" * length
    b = "나" * length
    budget = Budget(50.0)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        bounded_edit_distance(a, b, k=length, budget=budget)
    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms < 200, f"예산 50ms를 {elapsed_ms:.0f}ms까지 초과했다"


@pytest.mark.parametrize("length", [4_000, 20_000])
def test_substring_search_budget_overshoot_does_not_grow_with_length(length):
    """준전역 DP도 같은 성질을 갖는다 (D-111)."""
    pattern = "가" * length
    window = "나" * length
    budget = Budget(50.0)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        best_substring_match(pattern, window, k=length, budget=budget)
    elapsed_ms = (time.monotonic() - started) * 1000
    assert elapsed_ms < 200, f"예산 50ms를 {elapsed_ms:.0f}ms까지 초과했다"


# -- D-113: 문자 체계 판정의 경성 임계 ----------------------------------------


@pytest.mark.parametrize(
    ("quote", "revised"),
    [
        ("GDPは3.2%増加した。", "GDPは3.2%減少した。"),
        ("2026年3月期のEPSは前年比で改善した。", "2026年3月期のEPSは前年比で悪化した。"),
        ("2026年GDP增长3.5%，为近年最高。", "2026年GDP增长3.5%，为近年最低。"),
    ],
    ids=["일본어+라틴 약어", "일본어+연도", "중국어+백분율"],
)
def test_mixed_script_sentences_are_not_treated_as_latin(quote, revised):
    """라틴 약어·연도·백분율이 섞이면 밀도가 0.5 밑으로 떨어진다 (D-113).

    D-049가 막으려던 실패 모드가 그대로 재현된다 — 경계 바로 아래에서 k가
    2.5배 작아져, 같은 성격의 개정이 ALTERED가 아니라 MISSING이 된다.
    임계는 **연속**이어야 한다. 계단이 있으면 계단 아래가 항상 틀린다.
    """
    text = f"要旨\n{revised}\n以上。\n"
    result = _match(text, quote)
    assert result.state == ALTERED, f"개정이 소멸로 보고됐다 (판정: {result.state})"


def test_latin_text_keeps_its_edit_budget():
    """라틴 문서의 k는 그대로여야 한다 — 연속화가 관대해지는 방향이면 안 된다."""
    quote = "The committee approved the research budget increase for the fiscal year."
    text = "Notes\n" + quote.replace("approved", "rejected the request and approved") + "\n"
    result = _match(text, quote)
    assert result.state in {ALTERED, MISSING}
    if result.state == ALTERED:
        assert result.edit_distance is not None and result.edit_distance <= 11


# -- D-114: 절단 경계에 걸친 인용문 -------------------------------------------


def test_quote_cut_by_the_document_limit_is_not_reported_as_altered():
    """잘려나간 꼬리를 편집거리로 세면 **원문 무손상인데 ALTERED**가 된다 (D-114).

    SPEC §6.3의 "다 보지 못했으면 단정하지 않는다"는 MISSING에만 적용돼 있었다.
    잘린 조각이 "현재 모습"으로 제시되는 쪽이 오히려 더 나쁘다 — 사용자는
    본문이 실제로 그렇게 바뀐 줄 안다.
    """
    quote = "이 문장은 문서 상한 경계에 정확히 걸쳐 있으며 뒤쪽 절반이 잘려나간다"
    limit = 4096
    # 잘린 양이 k 이하일 때가 결함 구간이다 — 그보다 크면 애초에 못 찾는다.
    head = "가" * (limit - len(quote) + 4)
    text = head + quote + "그리고 문서는 계속 이어진다." * 50
    result = _match(text, quote, max_chars=limit)
    assert result.truncated
    assert result.state == UNRESOLVED, f"잘린 조각을 현재 모습으로 제시했다 ({result.state})"


def test_truncation_does_not_erase_a_real_finding():
    """상한 안에서 온전히 확인된 개정은 잘림과 무관하게 ALTERED다 (회귀 방지)."""
    quote = "위원회는 기초연구 지원을 전년 대비 12퍼센트 증액하기로 의결했다"
    revised = quote.replace("12퍼센트", "15퍼센트")
    text = revised + "\n" + "나" * 8000
    result = _match(text, quote, max_chars=4096)
    assert result.truncated
    assert result.state == ALTERED


# -- D-115: 다른 절의 형제 문장을 "현재 모습"으로 --------------------------


def _release_notes(with_v2: bool) -> tuple[str, str, str, str]:
    quote = "이 릴리스에서 기본 재시도 횟수를 3회에서 5회로 늘렸습니다"
    prefix = "### v2.0.0\n변경 사항\n"
    suffix = "\n자세한 내용은 마이그레이션 문서를 보세요."
    v2_body = f"{prefix}{quote}{suffix}\n\n" if with_v2 else f"{prefix}(항목 없음)\n\n"
    text = (
        "# 릴리스 노트\n\n"
        + v2_body
        + "### v1.9.0\n변경 사항\n"
        + "이 릴리스에서 기본 재시도 횟수를 5회에서 3회로 줄였습니다\n"
        + "\n자세한 내용은 변경 이력을 보세요.\n"
    )
    return text, quote, prefix, suffix


def test_deleted_entry_does_not_borrow_a_sibling_paragraph():
    """삭제된 인용문에 **정반대 의미의 형제 문장**이 붙으면 안 된다 (D-115).

    v2.0.0 항목이 지워졌는데 v1.9.0 절의 템플릿 문장이 편집거리 안에 들어와
    "당신 인용문의 현재 모습"으로 보고됐다. 뜻은 정반대다("늘렸습니다" →
    "줄였습니다").

    판정은 `MISSING`이 아니라 `UNRESOLVED`다 (D-179). 옛 자리에 다른 것이
    들어앉은 채 닮은 문장이 다른 절에 있는 상황은, "옮겨지며 개정됐다"와
    "삭제됐고 닮은 남이 있다"가 **증거로 갈리지 않는다**. 어느 쪽으로든
    단정하면 하나는 반드시 거짓말이 된다.
    """
    text, quote, prefix, suffix = _release_notes(with_v2=False)
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=40, budget_ms=2000.0
    )
    assert result.state == UNRESOLVED, (
        f"증거가 갈리는데 단정했다: {result.state} {result.found_text!r}"
    )
    assert not result.found_text, "형제 문장을 현재 모습으로 내밀었다"


def test_present_entry_is_still_found():
    """항목이 살아 있으면 그대로 찾는다 (회귀 방지)."""
    text, quote, prefix, suffix = _release_notes(with_v2=True)
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=40, budget_ms=2000.0
    )
    assert result.state in {INTACT, MOVED}


def test_edited_entry_in_place_is_altered():
    """문맥이 남아 있는 자리개정은 ALTERED로 남아야 한다 (D-115 조치의 안전선)."""
    text, quote, prefix, suffix = _release_notes(with_v2=True)
    text = text.replace("3회에서 5회로 늘렸습니다", "3회에서 8회로 늘렸습니다")
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=40, budget_ms=2000.0
    )
    assert result.state == ALTERED
    assert "8회" in (result.found_text or "")


def test_a_closer_lookalike_does_not_beat_the_corroborated_paragraph():
    """더 가까운 **닮은 남**이 문맥이 살아 있는 진짜를 밀어내면 안 된다 (D-115).

    D-109 조치로 후보 창이 넓어지면서 새로 생긴 위험이다. 부록의 표준 문안이
    개정된 본문보다 원문에 가까운 것은 약관에서 흔하다 — 거리만으로 고르면
    그 표준 문안이 뽑히고, 문맥 관문에 걸려 판정이 통째로 MISSING이 된다.
    """
    quote = "본 계약의 해지는 상대방에게 30일 전까지 서면으로 통지함으로써 효력이 발생한다"
    prefix = "### 제12조 해지 통지\n"
    suffix = "\n(제2항은 생략)"
    true_body = quote.replace("30일", "60일").replace("서면", "전자문서")
    sibling = quote.replace("30일", "45일")
    text = (
        "# 제3장 계약의 해지\n"
        f"{prefix}{true_body}\n(제3항 신설)\n\n"
        f"# 부록 B 표준 문안 예시\n{sibling}\n"
    )
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=20, budget_ms=2000.0
    )
    assert result.state == ALTERED, f"부록 문안에 밀려 판정이 뒤집혔다 ({result.state})"
    assert "60일" in (result.found_text or ""), f"부록 문안을 골랐다: {result.found_text!r}"


def test_context_cap_defers_to_the_approximate_search():
    """상한에 걸리면 3단계의 답을 **버리고** 4단계로 넘긴다 (D-110).

    문맥이 1000번 넘게 반복되는 문서에서 3단계의 "지금까지의 최선"은 근거가
    없다 — 진짜 문단은 아직 보지 않은 뒤쪽에 있을 수 있다. 상한을 올리는
    것만으로는 경계가 옮겨갈 뿐이고, 경계에서 확정하지 않는 것이 조치다.
    """
    text, quote, prefix, suffix = _template_document(true_index=1050, blocks=1100)
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=0, budget_ms=8000.0
    )
    assert result.state == ALTERED
    assert "않는다" in (result.found_text or ""), f"형제 문단으로 확정했다: {result.found_text!r}"


# -- D-178: 인용문 길이라는 축 -----------------------------------------------


_FILLER = (
    "The committee reviewed the proposal in detail and asked for further "
    "clarification on several points before the next session. "
)
_BASE = (
    "The council agreed that the research budget for the next fiscal year should be "
    "increased by twelve percent, which represents the largest single adjustment "
    "recorded in the past five years of continuous planning work, and that the "
    "additional funds must be directed towards basic research programmes. "
)


def _long_decoy_document(length: int) -> tuple[str, str]:
    """길이만 다른 같은 모양의 디코이 문서."""
    quote = (_BASE * 4)[:length]
    revised = quote.replace("the largest single", "the second largest")
    assert revised != quote
    text = (
        "Summary\n" + quote[:64] + "\n\n" + _FILLER * 20 +
        "\n\nPull quote: " + quote[-64:] + "\n\n" + _FILLER * 20 +
        "\n\nBody\n" + revised + "\n" + _FILLER * 10
    )
    return text, quote


@pytest.mark.parametrize("length", [240, 300, 347, 420, 470, 600])
def test_decoys_do_not_hide_the_paragraph_at_any_quote_length(length):
    """인용문이 길어져도 디코이에 가려지면 안 된다 (D-178).

    D-109 조치는 후보를 **`score ≤ k`인 이어진 자리**로 묶었다. 그런데 k는
    인용문 길이에 비례하고 코어는 64자로 고정이라, 라틴 340자쯤부터 k가 코어
    길이에 근접해 **평범한 산문의 거의 모든 자리가 k 이하**가 된다. 그러면
    문서 전체가 덩이 하나가 되어 후보가 다시 전역 최소 하나뿐 — 조치 이전
    상태다. 실측으로 이 구간의 88.5%가 거짓 MISSING이었다.

    내 픽스처가 이걸 놓친 이유는 인용문이 전부 한글 90자 안팎이라 **길이라는
    축을 한 번도 밟지 않았기** 때문이다. 픽스처의 정상값은 값에만 있는 게
    아니라 축에도 있다.
    """
    text, quote = _long_decoy_document(length)
    result = _match(text, quote, budget_ms=10000.0)
    assert result.state == ALTERED, f"길이 {length}자에서 거짓 MISSING ({result.state})"
    assert "second largest" in (result.found_text or "")


def test_core_scan_splits_a_run_longer_than_the_pattern():
    """덩이가 패턴보다 길어지면 끊어야 한다 (D-178, 검색 계층에서 직접).

    패턴 길이를 넘는 이어진 자리는 하나의 정렬일 수 없다. 끊지 않으면 그
    구간 전체가 후보 하나로 뭉개진다.
    """
    from anchor.anchoring.approx import myers_scan_all

    text, quote = _long_decoy_document(470)
    core = quote[:64]
    hits = myers_scan_all(text, core, 60, Budget(10000.0))  # k가 코어 길이에 근접
    assert len(hits) > 1, "문서 전체가 덩이 하나로 뭉개졌다"
    assert len({end for _, end in hits}) == len(hits)


# -- D-179: 관문이 받아들여야 하는 표본 --------------------------------------


def _moved_document(replace_old_slot: bool) -> tuple[str, str, str, str]:
    items = [
        f"{index + 1}. {topic}. 이 조치는 관련 부처 협의를 거쳐 확정되었으며 시행 시점은 별도로 공지한다."
        for index, topic in enumerate(
            [
                "기초연구 지원 예산을 전년 대비 12퍼센트 늘리기로 의결했다",
                "신진 연구자 채용 규모를 내년부터 단계적으로 확대한다고 밝혔다",
                "국가 연구장비 공동활용 체계는 하반기에 정식 개통된다",
                "성과 평가 지표에서 논문 편수의 비중을 낮추고 재현성 항목을 새로 넣었다",
                "연구 데이터 공개 의무는 공공 재원이 투입된 과제에 우선 적용된다",
                "국제 공동연구 지원 사업은 참여 기관 수 제한을 없애는 방향으로 개편된다",
                "대학원생 연구장학금은 물가 상승률을 반영해 매년 자동으로 조정한다",
            ]
        )
    ]
    target = 5
    quote = items[target]
    first = "\n\n".join(items)
    at = first.index(quote)
    prefix, suffix = first[max(0, at - 48) : at], first[at + len(quote) : at + len(quote) + 48]

    remaining = list(items)
    remaining.pop(target)
    if replace_old_slot:
        remaining.insert(target, "6. 이 항목은 다른 내용으로 대체되었으며 예전 항목과 무관하다.")
    moved = quote.replace("없애는", "완화하는")
    second = "\n\n".join(remaining) + "\n\n부록 — 이관된 항목\n" + moved
    return second, quote, prefix, suffix


def test_a_paragraph_that_moved_and_was_edited_is_still_altered():
    """자리를 옮기며 개정된 문단은 살아 있다 — MISSING이 아니다 (D-179).

    D-115 관문은 "표지가 살아 있는데 인용문이 그 옆에 없다 = 삭제"로 읽었다.
    문단이 빠져나가면 옛 이웃들이 **맞붙으므로** 표지는 당연히 남는다 —
    절 재배치는 릴리스노트·약관의 평범한 편집이라 흔한 거짓 MISSING이 됐다.
    갈라 주는 것은 옛 자리에 무엇이 남았는가다.
    """
    text, quote, prefix, suffix = _moved_document(replace_old_slot=False)
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=300, budget_ms=4000.0
    )
    assert result.state == ALTERED, f"살아 있는 인용문을 죽었다고 했다 ({result.state})"
    assert "완화하는" in (result.found_text or "")


def test_moved_with_the_old_slot_taken_is_held_not_asserted():
    """옛 자리가 채워졌으면 **증거가 갈린다** — 어느 쪽으로도 단정하지 않는다.

    "옮겨지며 개정됐다"와 "삭제됐고 닮은 남이 다른 절에 있다"는 이 증거로
    구분되지 않는다. ALTERED로 내밀면 뜻이 다른 문장을 개정문으로 읽게 되고,
    MISSING으로 내밀면 살아 있는 인용을 죽었다고 한다.
    """
    text, quote, prefix, suffix = _moved_document(replace_old_slot=True)
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=300, budget_ms=4000.0
    )
    assert result.state == UNRESOLVED, f"증거가 갈리는데 단정했다 ({result.state})"
    assert not result.found_text


def test_an_absent_quote_is_scanned_only_once_per_core():
    """정말 없는 인용문에 **같은 스캔을 두 번** 하면 안 된다 (D-178 조치의 안전선).

    D-178 조치가 "좁은 임계로 빈손이면 느슨하게 한 번 더"를 두면서, 두 임계가
    같을 때도 전문 스캔이 두 번 돌았다. 인용문이 실제로 없는 큰 문서가 가장
    흔한 최악 사례인데 그 한 번이 예산을 넘겨 맞는 답(MISSING)을 UNRESOLVED로
    바꿨다(§10 최악 사례에서 100/100으로 실측).

    시간으로 재면 기계 속도에 따라 초록·빨강이 바뀐다 — 실제로 그렇게 됐다.
    **스캔 횟수**를 세면 어느 기계에서나 같은 답이 나온다.
    """
    from anchor.anchoring import approx

    big_text = "채움 문장이 끝없이 이어지는 대폭 개편 문서다. " * 5000
    quote = "이 문서 어디에도 없는 인용문 7번이다, 확실히."  # k > 3 → Myers 경로
    calls = []
    original = approx.myers_scan_all

    def counting(text, pattern, k, budget):
        calls.append(pattern)
        return original(text, pattern, k, budget)

    approx.myers_scan_all = counting
    try:
        found = approx.fuzzy_search_myers(big_text, quote, 6, Budget(60_000.0))
    finally:
        approx.myers_scan_all = original

    assert found is None, "없는 인용문을 찾았다고 했다"
    assert len(calls) == 1, f"코어 하나짜리 인용문을 {len(calls)}번 훑었다"


def test_an_absent_quote_in_a_large_document_is_still_missing():
    """정말 없는 인용문은 **없다고** 말해야 한다 — 보류가 아니라 (D-178 조치의 안전선).

    D-178 조치가 "좁은 임계로 빈손이면 느슨하게 한 번 더"를 두면서, 두 임계가
    같을 때도 **같은 스캔을 두 번** 하게 됐다. 인용문이 실제로 없는 큰 문서가
    가장 흔한 최악 사례인데, 그 한 번이 예산을 넘겨 MISSING이 UNRESOLVED로
    바뀐다 — 맞는 답을 잃는 것이다(§10 최악 사례에서 100/100 UNRESOLVED로 실측).
    """
    big_text = "채움 문장이 끝없이 이어지는 대폭 개편 문서다. " * 20000  # ~50만 자
    result = match_anchor(
        big_text,
        # k > 3이어야 D-178이 손댄 Myers 경로를 밟는다 (k ≤ 3은 regex 경로).
        exact="이 문서 어디에도 없는 인용문 7번이다, 확실히.",
        prefix="존재하지 않는 앞 문맥",
        suffix="존재하지 않는 뒤 문맥",
        position_hint=len(big_text) // 2,
        budget_ms=60_000,  # 판정 자체를 본다 — 예산 경계는 위 테스트가 센다
    )
    assert result.state == MISSING, f"없다고 말할 수 있는데 보류했다 ({result.state})"
