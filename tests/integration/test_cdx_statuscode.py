# SPDX-License-Identifier: Apache-2.0
"""CDX 응답의 statuscode 열 축 (D-092).

축: 우리가 `filter=statuscode:200`을 **요청했다는 사실**과 응답이 그 필터를
지켰다는 사실은 다른 것이다. 축의 값은 셋이다 — ①필터가 지켜진 응답(200만)
②필터를 무시한 미러(404 행이 섞임·404가 마지막 행) ③statuscode 열 자체가
없는 미러(우리가 알 수 없는 상태).

정상값(200 한 줄)만 두면 이 축은 픽스처에 존재하지 않고, 아카이브된 404
오류 페이지가 "구제된 본문"으로 저장되는 결함이 그대로 산다 — 그 본문에
붙은 인용은 MISSING이 되고, 그것이 원문의 삭제로 보고된다.
"""

from __future__ import annotations

import pytest

from anchor.config import Config
from anchor.fetcher import archive as archive_module
from anchor.service import Anchor
from tests.integration.conftest import article_html

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."

ARCHIVED_404 = """<!DOCTYPE html><html><head><title>Not Found</title></head><body>
<article><h1>404 Not Found</h1>
<p>요청하신 문서를 찾을 수 없습니다. 이 페이지는 오류 안내이며 기사 본문이 아닙니다.
아카이브는 이 오류 응답까지 그대로 보존한다 — 보존됐다는 것이 본문이라는 뜻은 아니다.</p>
</article></body></html>"""

OLD_SNAPSHOT = article_html(extra_sentence=" 이것은 2026년 7월의 스냅샷 본문이다.")


@pytest.fixture
def cdx_anchor(fixture_server, tmp_path, monkeypatch):
    base_url, _state = fixture_server
    monkeypatch.setattr(archive_module, "WAYBACK_BASE", base_url)
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        archive_fallback_enabled=True,
        archive_aggregator="",  # CDX 경로
    )
    with Anchor(db_path=config.db_path, config=config) as instance:
        yield instance


def _rows(*entries: tuple[str, str]) -> list[list[str]]:
    """(timestamp, statuscode) → CDX 행. `$original`은 핸들러가 치환한다."""
    return [
        ["key", timestamp, "$original", "text/html", statuscode, "D", "1"]
        for timestamp, statuscode in entries
    ]


def test_archived_404_is_not_adopted_as_rescued_body(fixture_server, cdx_anchor):
    """필터를 무시한 미러가 404 행을 마지막에 주면, 그것을 본문으로 삼아서는
    안 된다 — 오류 안내가 '구제된 본문'이 되고 인용은 사라진 것이 된다."""
    base_url, state = fixture_server
    fetched = cdx_anchor.fetch(f"{base_url}/article")
    cdx_anchor.cite(fetched.document_id, QUOTE)

    state.status_override = 404  # 원본 사망
    state.cdx_rows = _rows(("20260701000000", "404"))
    state.archive_html_by_timestamp = {"20260701000000": ARCHIVED_404}

    report = cdx_anchor.verify()
    assert report.summary["MISSING"] == 0, (
        "아카이브된 404 페이지를 현재 본문으로 삼아 인용을 MISSING으로 단정했다"
    )
    assert report.summary["GONE"] == 1, "구제가 없었으면 사실대로 GONE이다"


def test_last_200_row_is_preferred_over_a_later_404_row(fixture_server, cdx_anchor):
    """행이 섞여 오면 **마지막 행**이 아니라 **마지막 200 행**을 고른다."""
    base_url, state = fixture_server
    fetched = cdx_anchor.fetch(f"{base_url}/article")

    state.status_override = 404
    state.cdx_rows = _rows(
        ("20260601000000", "200"),
        ("20260701000000", "404"),   # 필터를 무시한 미러 — 마지막 행이 404
    )
    state.archive_html_by_timestamp = {
        "20260601000000": OLD_SNAPSHOT,
        "20260701000000": ARCHIVED_404,
    }

    result = cdx_anchor.fetch(f"{base_url}/article", max_age=0)
    assert result.outcome == "archive"
    assert result.captured_at.startswith("2026-06-01"), (
        f"404 행의 스냅샷을 채택했다: {result.captured_at}"
    )
    _version, text = cdx_anchor.get_version(result.version_id)
    assert "2026년 7월의 스냅샷" in text
    assert "404 Not Found" not in text


def test_missing_statuscode_column_is_not_treated_as_200(fixture_server, cdx_anchor):
    """statuscode 열이 없으면 그 행이 200인지 **알 수 없다**.

    "필터를 요청했으니 200일 것"은 추정이지 사실이 아니다. 모르는 것을 아는
    것처럼 다루면 오류 페이지가 근거가 된다 — 구제를 포기하고 원 상태를
    사실대로 보고하는 편이 인용의 무결성을 지킨다 (§5.4).
    """
    base_url, state = fixture_server
    fetched = cdx_anchor.fetch(f"{base_url}/article")
    cdx_anchor.cite(fetched.document_id, QUOTE)

    state.status_override = 404
    state.cdx_header = ["urlkey", "timestamp", "original", "mimetype", "digest", "length"]
    state.cdx_rows = [["key", "20260701000000", "$original", "text/html", "D", "1"]]
    state.archive_html_by_timestamp = {"20260701000000": ARCHIVED_404}

    report = cdx_anchor.verify()
    assert report.summary["GONE"] == 1
    assert report.summary["MISSING"] == 0


def test_wellformed_200_only_response_still_rescues(fixture_server, cdx_anchor):
    """축의 반대편: 정상 응답에서는 구제가 그대로 일어나야 한다."""
    base_url, state = fixture_server
    fetched = cdx_anchor.fetch(f"{base_url}/article")
    cdx_anchor.cite(fetched.document_id, QUOTE)

    state.status_override = 404
    state.cdx_rows = _rows(("20260701000000", "200"))
    state.archive_html_by_timestamp = {"20260701000000": article_html()}

    report = cdx_anchor.verify()
    assert report.summary["INTACT"] == 1


def test_unreadable_timestamp_falls_back_to_an_older_200_row(fixture_server, cdx_anchor):
    """행 하나의 시각을 읽지 못했다고 더 오래된 200 스냅샷까지 버리지 않는다."""
    base_url, state = fixture_server
    cdx_anchor.fetch(f"{base_url}/article")

    state.status_override = 404
    state.cdx_rows = _rows(
        ("20260601000000", "200"),
        ("2026-07-01T00:00", "200"),   # 형식이 다른 미러
    )
    state.archive_html_by_timestamp = {"20260601000000": OLD_SNAPSHOT}

    result = cdx_anchor.fetch(f"{base_url}/article", max_age=0)
    assert result.outcome == "archive"
    assert result.captured_at.startswith("2026-06-01")
