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

    (document,) = anchor.list_documents()
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
