# SPDX-License-Identifier: Apache-2.0
"""아카이브 폴백 (SPEC §5.2 6단계). v0.5 완료 기준 시나리오 포함 —
GONE 판정 전 아카이브 확인이 실제로 인용을 구제하는가."""

from __future__ import annotations

import pytest

from anchor.config import Config
from anchor.errors import FetchFailed, RobotsDisallowed
from anchor.fetcher import archive as archive_module
from anchor.service import Anchor
from tests.integration.conftest import article_html

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


def make_anchor(tmp_path, *, enabled=True, aggregator=""):
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        archive_fallback_enabled=enabled,
        archive_aggregator=aggregator,
    )
    return Anchor(db_path=config.db_path, config=config)


@pytest.fixture
def cdx_anchor(fixture_server, tmp_path, monkeypatch):
    base_url, _ = fixture_server
    monkeypatch.setattr(archive_module, "WAYBACK_BASE", base_url)
    with make_anchor(tmp_path) as anchor:
        yield anchor


def test_archive_rescues_citation_from_gone(fixture_server, cdx_anchor):
    """완료 기준 시나리오: 원본 404 → 아카이브 확인 → 인용이 GONE이 아니라
    INTACT로 판정된다."""
    base_url, state = fixture_server

    fetched = cdx_anchor.fetch(f"{base_url}/article")
    cdx_anchor.cite(fetched.document_id, QUOTE)

    state.status_override = 404               # 원본 사망
    state.archive_html = article_html()       # 아카이브에는 스냅샷이 있다

    report = cdx_anchor.verify()
    assert report.summary["GONE"] == 0
    assert report.summary["INTACT"] == 1      # 인용 구제
    assert report.attention == ()

    (document,) = cdx_anchor.list_documents().documents
    assert document.status == "gone"          # 원본 상태는 사실대로 남는다


def test_archive_keeps_provenance_even_when_text_matches(fixture_server, cdx_anchor):
    """D-013: 본문이 live 버전과 같아도 아카이브 관측은 별개의 memento다.

    기존 live 행을 재사용하면 source·source_uri·Memento 시각이 전부
    사라져 "원본이 아니라 아카이브에서 확인됨"을 알 수 없게 된다.
    """
    base_url, state = fixture_server
    first = cdx_anchor.fetch(f"{base_url}/article")
    state.status_override = 404
    state.archive_html = article_html()

    result = cdx_anchor.fetch(f"{base_url}/article", max_age=0)
    assert result.outcome == "archive"
    assert result.version_id != first.version_id
    assert result.source == "archive"
    assert result.captured_at == "2026-08-01T12:34:56Z"  # Memento-Datetime

    version = cdx_anchor._repository.get_version(result.version_id)
    assert version.source_uri and "/web/" in version.source_uri

    # 두 memento가 같은 본문 해시를 공유한다 — 내용은 같고 출처만 다르다.
    assert version.text_hash == first.text_hash


def test_archive_snapshot_with_older_text_creates_archive_version(fixture_server, cdx_anchor):
    base_url, state = fixture_server
    state.html = article_html(extra_sentence=" 최신판에만 있는 문장이다.")
    fetched = cdx_anchor.fetch(f"{base_url}/article")

    state.status_override = 404
    state.archive_html = article_html()  # 아카이브엔 옛 판

    result = cdx_anchor.fetch(f"{base_url}/article", max_age=0)
    assert result.outcome == "archive"
    assert result.source == "archive"
    assert result.captured_at == "2026-08-01T12:34:56Z"  # Memento-Datetime
    assert result.version_id != fetched.version_id

    # TimeMap은 아카이브 버전의 실제 URI-M을 그대로 노출한다 (SPEC §7.8).
    timemap = cdx_anchor.get_timemap(fetched.document_id)
    assert f"/web/{state.archive_timestamp}id_/" in timemap["body"]


def test_memgator_aggregator_path(fixture_server, tmp_path):
    base_url, state = fixture_server
    with make_anchor(tmp_path, aggregator=base_url) as anchor:
        fetched = anchor.fetch(f"{base_url}/article")
        anchor.cite(fetched.document_id, QUOTE)
        state.status_override = 404
        state.archive_html = article_html()

        result = anchor.fetch(f"{base_url}/article", max_age=0)
        assert result.outcome == "archive"
    assert any(path.startswith("/api/json/") for path in state.requests)
    assert not any(path.startswith("/cdx/") for path in state.requests)


def test_no_snapshot_confirms_gone(fixture_server, cdx_anchor):
    base_url, state = fixture_server
    fetched = cdx_anchor.fetch(f"{base_url}/article")
    cdx_anchor.cite(fetched.document_id, QUOTE)
    state.status_override = 404
    state.archive_html = None                 # 아카이브에도 없음

    with pytest.raises(FetchFailed):
        cdx_anchor.fetch(f"{base_url}/article", max_age=0)
    report = cdx_anchor.verify()
    assert report.summary["GONE"] == 1        # 원 상태 확정
    assert any(path.startswith("/cdx/") for path in state.requests)


def test_fallback_disabled_by_default(fixture_server, anchor):
    """기본 설정에서는 아카이브에 조용히 의존하지 않는다 (SPEC §9)."""
    base_url, state = fixture_server
    anchor.fetch(f"{base_url}/article")
    state.status_override = 404
    state.archive_html = article_html()       # 있어도 찾아가지 않는다

    with pytest.raises(FetchFailed):
        anchor.fetch(f"{base_url}/article", max_age=0)
    assert not any(
        path.startswith(("/cdx/", "/api/json/", "/web/")) for path in state.requests
    )


def test_first_fetch_of_dead_url_recovers_from_archive(fixture_server, cdx_anchor):
    """처음 보는 URL이 이미 죽어 있어도 아카이브로 문서를 세울 수 있다."""
    base_url, state = fixture_server
    state.status_override = 404
    state.archive_html = article_html()

    result = cdx_anchor.fetch(f"{base_url}/article")
    assert result.outcome == "archive"
    assert result.source == "archive"
    (document,) = cdx_anchor.list_documents().documents
    assert document.status == "gone"
    assert QUOTE in (result.content or "")


def test_vanished_host_is_recovered_from_archive(fixture_server, tmp_path, monkeypatch):
    """호스트가 통째로 사라져도 아카이브로 구제한다 (D-053).

    robots.txt를 받을 수 없으면 RFC 9309 §2.3.1.4대로 원본 요청은 보류하지만,
    그것이 **다른 호스트인** 공개 아카이브 확인까지 막아서는 안 된다.
    호스트 소멸은 링크 부패의 가장 흔한 형태이자 구제가 가장 필요한 상황이다.
    """
    base_url, state = fixture_server
    monkeypatch.setattr(archive_module, "WAYBACK_BASE", base_url)
    state.archive_html = article_html()

    # 원본은 존재하지 않는 호스트 — 연결 자체가 실패한다.
    dead = "http://127.0.0.1:9/gone"
    config = Config(
        db_path=tmp_path / "vanished.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        archive_fallback_enabled=True,
        timeout_seconds=2,
    )
    with Anchor(db_path=config.db_path, config=config) as anchor:
        result = anchor.fetch(dead, max_age=0)
        assert result.outcome == "archive"
        assert result.source == "archive"
        assert QUOTE in (result.content or "")

        cited = anchor.cite(result.document_id, QUOTE)
        report = anchor.verify()
        assert report.summary["INTACT"] == 1, "아카이브 본문으로 인용이 구제되어야 한다"
        assert cited.anchor_id


def test_explicit_robots_denial_is_never_bypassed(fixture_server, tmp_path, monkeypatch):
    """대조군: 소유자가 **명시적으로** 거부하면 아카이브로도 우회하지 않는다."""
    base_url, state = fixture_server
    monkeypatch.setattr(archive_module, "WAYBACK_BASE", base_url)
    state.archive_html = article_html()

    config = Config(
        db_path=tmp_path / "denied.db",
        rate_limit_rps=1000.0,
        archive_fallback_enabled=True,
    )
    with Anchor(db_path=config.db_path, config=config) as anchor:
        with pytest.raises(RobotsDisallowed):
            anchor.fetch(f"{base_url}/private/report")
    assert not any(path.startswith("/web/") for path in state.requests), (
        "명시적 거부를 아카이브로 우회했다"
    )
