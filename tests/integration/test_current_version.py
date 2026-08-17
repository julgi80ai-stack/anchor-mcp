# SPDX-License-Identifier: Apache-2.0
"""현재 본문 버전 추적 회귀 (D-011 / D-012 / D-024).

"가장 최근 캡처된 버전"과 "원문이 지금 서빙하는 본문"은 다를 수 있다.
아카이브 폴백(과거 시각 스냅샷)과 본문 되돌림(옛 버전 행 재사용)에서 갈린다.
"""

from __future__ import annotations

import pytest

from anchor.config import Config
from anchor.errors import FetchFailed
from anchor.fetcher import archive as archive_module
from anchor.service import Anchor
from tests.integration.conftest import article_html

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


@pytest.fixture
def archive_anchor(fixture_server, tmp_path, monkeypatch):
    base_url, _ = fixture_server
    monkeypatch.setattr(archive_module, "WAYBACK_BASE", base_url)
    config = Config(
        db_path=tmp_path / "cur.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        archive_fallback_enabled=True,
    )
    with Anchor(db_path=config.db_path, config=config) as anchor:
        yield anchor


def test_archive_snapshot_is_verified_not_the_stale_live_copy(fixture_server, archive_anchor):
    """D-011: 아카이브 폴백이 GONE 신호를 지우고 거짓 INTACT를 만들면 안 된다."""
    base_url, state = fixture_server

    fetched = archive_anchor.fetch(f"{base_url}/article")
    archive_anchor.cite(fetched.document_id, QUOTE)

    # 원본 사망 + 아카이브에는 인용문이 빠진 옛 스냅샷만 있다.
    state.status_override = 410
    state.archive_html = article_html().replace(QUOTE, "전혀 다른 이야기가 실려 있었다.")

    report = archive_anchor.verify()
    assert report.summary["INTACT"] == 0, "앵커 생성에 쓴 본문과 자기 자신을 대조했다"
    assert report.summary["MISSING"] == 1
    (item,) = report.attention
    assert item.state == "MISSING"


def test_archive_snapshot_containing_quote_still_rescues(fixture_server, archive_anchor):
    """대조군: 아카이브 본문에 인용문이 살아 있으면 구제가 유지되어야 한다."""
    base_url, state = fixture_server
    fetched = archive_anchor.fetch(f"{base_url}/article")
    archive_anchor.cite(fetched.document_id, QUOTE)

    state.status_override = 410
    state.archive_html = article_html()  # 인용문이 그대로 있는 스냅샷

    report = archive_anchor.verify()
    assert report.summary["INTACT"] == 1
    assert report.summary["GONE"] == 0


def test_reverted_content_is_reported_as_current(fixture_server, anchor):
    """D-012/D-024: A→B→A 되돌림 후 fetch가 실제 본문(A)을 돌려줘야 한다."""
    base_url, state = fixture_server
    text_a = article_html()
    text_b = article_html(extra_sentence=" 중간 판에만 있던 문장이다.")

    first = anchor.fetch(f"{base_url}/article")
    assert first.outcome == "created"

    state.html = text_b
    state.etag = '"v2"'
    changed = anchor.fetch(f"{base_url}/article", max_age=0)
    assert changed.outcome == "changed"

    state.html = text_a  # 원문이 A로 되돌아왔다
    state.etag = '"v3"'
    reverted = anchor.fetch(f"{base_url}/article", max_age=0)
    assert reverted.version_id == first.version_id, "A의 기존 버전을 재사용해야 한다"
    assert "중간 판에만 있던" not in (reverted.content or "")

    # 되돌림 이후의 조회 경로가 전부 A를 가리켜야 한다.
    version, text = anchor.get_version(document_id=first.document_id, ref="latest")
    assert version.id == first.version_id
    assert "중간 판에만 있던" not in text

    # 304 경로도 A를 돌려줘야 한다 (서버는 A를 서빙 중).
    not_modified = anchor.fetch(f"{base_url}/article", max_age=0)
    assert not_modified.outcome == "not_modified"
    assert not_modified.version_id == first.version_id
    assert "중간 판에만 있던" not in (not_modified.content or "")


def test_reverted_content_keeps_timemap_chronological(fixture_server, anchor):
    """되돌림이 TimeMap의 시간순 열거를 흔들지 않아야 한다 (캡처 시각 기준 유지)."""
    base_url, state = fixture_server
    first = anchor.fetch(f"{base_url}/article")
    state.html = article_html(extra_sentence=" 중간 판 문장.")
    state.etag = '"v2"'
    anchor.fetch(f"{base_url}/article", max_age=0)
    state.html = article_html()
    state.etag = '"v3"'
    anchor.fetch(f"{base_url}/article", max_age=0)

    body = anchor.get_timemap(first.document_id)["body"]
    assert body.count('rel="first') == 1
    assert body.count('last memento"') == 1
    # 버전은 둘뿐이다 — 되돌림이 새 버전을 만들지 않는다.
    assert body.count("memento") == 2


def test_gone_without_archive_still_reports_gone(fixture_server, archive_anchor):
    base_url, state = fixture_server
    fetched = archive_anchor.fetch(f"{base_url}/article")
    archive_anchor.cite(fetched.document_id, QUOTE)
    state.status_override = 404
    state.archive_html = None

    with pytest.raises(FetchFailed):
        archive_anchor.fetch(f"{base_url}/article", max_age=0)
    report = archive_anchor.verify()
    assert report.summary["GONE"] == 1
