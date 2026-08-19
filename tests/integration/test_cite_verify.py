# SPDX-License-Identifier: Apache-2.0
"""cite → verify 전체 흐름 통합 테스트. GONE/UNREACHABLE 포함 7상태 완결."""

from __future__ import annotations

import pytest

from anchor.errors import QuoteNotFound
from tests.integration.conftest import article_html

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


def _cite(anchor, base_url):
    fetch = anchor.fetch(f"{base_url}/article")
    return fetch, anchor.cite(fetch.document_id, QUOTE, note="테스트 근거")


def test_cite_then_verify_same_version_is_intact(fixture_server, anchor):
    """속성 (SPEC §12): cite → verify(동일 버전) == INTACT."""
    base_url, state = fixture_server
    fetch, cite = _cite(anchor, base_url)
    assert cite.quality == "ok"
    assert cite.warnings == ()

    report = anchor.verify()
    assert report.checked == 1
    assert report.summary["INTACT"] == 1
    assert report.attention == ()


def test_verify_detects_alteration_with_before_after(fixture_server, anchor):
    base_url, state = fixture_server
    _cite(anchor, base_url)

    state.html = article_html().replace("가장 위험하다", "제일 위험하다")
    state.etag = '"v2"'

    report = anchor.verify()
    assert report.summary["ALTERED"] == 1
    (item,) = report.attention
    assert item.state == "ALTERED"
    assert item.before == QUOTE
    assert "제일 위험하다" in (item.after or "")
    assert item.edit_distance is not None and item.edit_distance <= 4
    # 어디서 찾았는지를 함께 준다. 편집거리만으로는 그것이 같은 자리의
    # 개정인지 다른 절의 형제 문단인지 호출자가 알 수 없다 (D-115).
    assert item.found_offset is not None and item.position_hint is not None
    assert abs(item.found_offset - item.position_hint) < 500


def test_verify_detects_missing_quote(fixture_server, anchor):
    base_url, state = fixture_server
    _cite(anchor, base_url)

    state.html = article_html().replace(QUOTE, "전혀 다른 이야기가 대신 들어왔다.")
    state.etag = '"v2"'

    report = anchor.verify()
    assert report.summary["MISSING"] == 1
    (item,) = report.attention
    assert item.state == "MISSING"
    assert item.after is None


def test_verify_reports_gone_on_404(fixture_server, anchor):
    base_url, state = fixture_server
    fetch, cite = _cite(anchor, base_url)
    state.status_override = 404

    report = anchor.verify()
    assert report.summary["GONE"] == 1
    (item,) = report.attention
    assert item.state == "GONE"
    # 원문이 죽어도 인용 당시의 본문은 살아 있다 (버전 보존).
    assert QUOTE in anchor.get_version_text(cite.version_id)


def test_verify_reports_unreachable_on_403(fixture_server, anchor):
    base_url, state = fixture_server
    _cite(anchor, base_url)
    state.status_override = 403

    report = anchor.verify()
    assert report.summary["UNREACHABLE"] == 1
    # UNREACHABLE의 조치는 "재시도 예약"이다(SPEC §6.3) — 조치가 있으면
    # 목록에 있어야 한다. 빼 두면 아무것도 검증하지 못한 배치가 빈
    # attention으로 보여 "이상 없음"으로 읽힌다 (D-229).
    assert [item.state for item in report.attention] == ["UNREACHABLE"]


def test_cite_rejects_absent_quote(fixture_server, anchor):
    base_url, state = fixture_server
    fetch = anchor.fetch(f"{base_url}/article")
    with pytest.raises(QuoteNotFound):
        anchor.cite(fetch.document_id, "이 문장은 원문 어디에도 존재하지 않는다, 확실히.")


def test_cite_accepts_url_reference(fixture_server, anchor):
    base_url, state = fixture_server
    anchor.fetch(f"{base_url}/article")
    cite = anchor.cite(f"{base_url}/article", QUOTE)
    assert cite.anchor_id


def test_short_quote_warns_and_verifies(fixture_server, anchor):
    base_url, state = fixture_server
    fetch = anchor.fetch(f"{base_url}/article")
    cite = anchor.cite(fetch.document_id, "인용 표류가 가장 위험하다.")  # 12~31자
    assert cite.quality == "short"
    assert cite.warnings

    report = anchor.verify()
    assert report.summary["INTACT"] == 1


def test_older_than_skips_recently_verified(fixture_server, anchor):
    base_url, state = fixture_server
    _cite(anchor, base_url)

    first = anchor.verify()
    assert first.checked == 1
    # 방금 검증했으므로 24시간 조건에서는 대상이 없어야 한다.
    second = anchor.verify(older_than="P1D")  # ISO 8601 기간 (SPEC §7.3/§8)
    assert second.checked == 0
