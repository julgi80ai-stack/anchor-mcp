# SPDX-License-Identifier: Apache-2.0
"""페치 파이프라인 통합 테스트 (SPEC §5.2 응답 분기 전체)."""

from __future__ import annotations

import pytest

from anchor.errors import FetchFailed, RobotsDisallowed
from anchor.normalize import extract
from anchor.normalize.extract import NormalizedDoc
from tests.integration.conftest import article_html


def test_created_then_cache_hit_is_zero_network(fixture_server, anchor):
    """v0.1 완료 기준: 같은 URL 두 번 호출 시 두 번째가 네트워크 0바이트."""
    base_url, state = fixture_server

    first = anchor.fetch(f"{base_url}/article")
    assert first.outcome == "created"
    assert first.network.bytes_down > 0
    assert "재페치" in (first.content or "")
    requests_after_first = list(state.requests)

    second = anchor.fetch(f"{base_url}/article")
    assert second.outcome == "cache_hit"
    assert second.network.bytes_down == 0
    assert second.version_id == first.version_id
    assert state.requests == requests_after_first  # 서버에 요청 자체가 없었다


def test_conditional_get_304_not_modified(fixture_server, anchor):
    base_url, state = fixture_server

    first = anchor.fetch(f"{base_url}/article")
    result = anchor.fetch(f"{base_url}/article", max_age=0)

    assert result.outcome == "not_modified"
    assert result.network.bytes_down == 0  # 304에는 본문 전송이 없다
    assert result.version_id == first.version_id


def test_raw_noise_does_not_create_version(fixture_server, anchor):
    """광고·A/B 노이즈: raw_hash는 다르지만 text_hash가 같으면 unchanged."""
    base_url, state = fixture_server

    first = anchor.fetch(f"{base_url}/article")
    state.html = article_html(nonce="n1-변경된-노이즈")
    state.etag = '"v2"'  # 검증자도 바뀌어 304가 나오지 않는 상황

    result = anchor.fetch(f"{base_url}/article", max_age=0)
    assert result.outcome == "unchanged"
    assert result.version_id == first.version_id
    assert result.text_hash == first.text_hash


def test_real_content_change_creates_version(fixture_server, anchor):
    base_url, state = fixture_server

    first = anchor.fetch(f"{base_url}/article")
    state.html = article_html(extra_sentence=" 다만 측정 기준은 2026년 상반기 값이다.")
    state.etag = '"v2"'

    result = anchor.fetch(f"{base_url}/article", max_age=0)
    assert result.outcome == "changed"
    assert result.version_id != first.version_id
    assert result.text_hash != first.text_hash


def test_pipeline_change_reports_renormalized(fixture_server, anchor, monkeypatch):
    """raw 동일 + text 상이 + pipeline_version 상이 → changed가 아니라 renormalized."""
    base_url, state = fixture_server

    first = anchor.fetch(f"{base_url}/article")
    state.etag = None  # 304 경로를 막아 200 재수신을 강제

    original = extract.to_normalized

    def new_pipeline(raw: bytes, content_type: str) -> NormalizedDoc:
        doc = original(raw, content_type)
        return NormalizedDoc(
            text=doc.text + "\n\n[새 정규화 규칙이 붙인 꼬리]",
            title=doc.title,
            pipeline_version="trafilatura/999.0+norm/2",
        )

    monkeypatch.setattr(extract, "to_normalized", new_pipeline)

    result = anchor.fetch(f"{base_url}/article", max_age=0)
    assert result.outcome == "renormalized"
    assert result.version_id != first.version_id


def test_robots_disallow_blocks_without_document_request(fixture_server, anchor):
    base_url, state = fixture_server

    with pytest.raises(RobotsDisallowed):
        anchor.fetch(f"{base_url}/private/report")

    assert "/robots.txt" in state.requests
    assert not any(path.startswith("/private") for path in state.requests)


def test_404_reports_gone_status(fixture_server, anchor):
    base_url, state = fixture_server

    anchor.fetch(f"{base_url}/article")
    state.status_override = 404

    with pytest.raises(FetchFailed) as excinfo:
        anchor.fetch(f"{base_url}/article", max_age=0)
    assert excinfo.value.http_status == 404

    (document,) = anchor.list_documents().documents
    assert document.status == "gone"


def test_cached_content_survives_source_death(fixture_server, anchor):
    """원문이 죽어도 인용 당시의 본문은 로컬에서 살아 있다."""
    base_url, state = fixture_server

    first = anchor.fetch(f"{base_url}/article")
    state.status_override = 410

    with pytest.raises(FetchFailed):
        anchor.fetch(f"{base_url}/article", max_age=0)

    preserved = anchor.get_version_text(first.version_id)
    assert "인용 표류" in preserved


# -- 협상을 지나치게 좁히지 않는다 (실사용 406·415 4건) --------------------


def test_a_server_that_negotiates_is_not_refused_by_our_own_accept_header(
    fixture_server, anchor
):
    """`*/*`를 붙이지 않으면 협상하는 서버가 406으로 우리를 돌려보낸다.

    이것은 우회가 아니라 **우리 결함**이다. 403은 사이트 소유자의 의사이고
    202 인터스티셜은 안티봇이지만, 406은 우리가 받아들일 유형을 지나치게 좁게
    선언한 결과다. 브라우저 흉내(UA 위장)와 달리 `*/*;q=0.1`을 꼬리에 다는 것은
    표준 HTTP 예절이고, 사이트가 막은 것을 뚫는 일이 아니다.

    넓힌 대가로 처리 못 하는 유형이 들어올 수는 있다. 그때는 `UnsupportedContent`
    (`error_kind="unsupported_content"`)로 **실패의 이름이 정확해질 뿐**이다 —
    협상 실패로 가장되지 않는다.
    """
    base, state = fixture_server
    state.require_accept_any = True

    result = anchor.fetch(f"{base}/article")

    assert result.outcome == "created"


def test_the_accept_header_still_names_what_we_actually_handle():
    """넓혀도 **선호는 남긴다** — `*/*`만 보내면 서버가 아무거나 골라도 된다.

    우리가 실제로 처리하는 유형(HTML·평문·PDF)이 앞에 오고 `*/*`는 낮은 q로
    꼬리에 붙는다. 그래야 협상하는 서버가 우리에게 HTML을 준다.
    """
    from anchor.fetcher.client import ACCEPT_HEADER

    head, _, tail = ACCEPT_HEADER.rpartition(",")
    assert "*/*" in tail and "q=0" in tail, ACCEPT_HEADER
    for media_type in ("text/html", "text/plain", "application/pdf"):
        assert media_type in head, f"{media_type}가 선호 목록에서 빠졌다"
    assert "*/*" not in head, "`*/*`가 앞에 오면 선호가 무의미해진다"
