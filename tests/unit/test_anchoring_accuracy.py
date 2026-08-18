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


def test_deleted_entry_is_missing_not_a_sibling_paragraph():
    """삭제된 인용문에 **정반대 의미의 형제 문장**이 붙으면 안 된다 (D-115).

    v2.0.0 항목이 지워졌는데 v1.9.0 절의 템플릿 문장이 편집거리 안에 들어와
    "당신 인용문의 현재 모습"으로 보고된다. 뜻은 정반대다("늘렸습니다" →
    "줄였습니다"). D-044가 3단계만 고쳐, 3단계가 정직하게 None을 낸 뒤
    4단계가 같은 거짓 대조표를 만든다.
    """
    text, quote, prefix, suffix = _release_notes(with_v2=False)
    result = match_anchor(
        text, exact=quote, prefix=prefix, suffix=suffix, position_hint=40, budget_ms=2000.0
    )
    assert result.state == MISSING, (
        f"다른 절의 형제 문장을 현재 모습으로 제시했다: {result.found_text!r}"
    )


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
