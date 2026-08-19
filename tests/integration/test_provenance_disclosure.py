# SPDX-License-Identifier: Apache-2.0
"""출처 공시 축 (D-093) — SPEC §5.2: 아카이브에서 확인된 것은 **항상** 알 수 있다.

축: 같은 호출을 **live와 archive 두 값 모두에서** 확인한다. 픽스처가 live만
쓰면 축이 없다 — `source`가 응답에 아예 없어도 테스트는 전부 통과한다.

지금 `source`가 나오는 곳은 `fetch_document`·`get_version`뿐이다. `cite`는
어느 판본에 앵커를 달았는지 말하지 않고, `verify_citations`는 원본이 404이고
아카이브 스냅샷만 대조했는데도 `INTACT`만 보고한다 — 조치가 필요한 항목이
없으면 `attention`도 비어 있어, 출처가 사라진 자리에 "이상 없음"만 남는다.
"""

from __future__ import annotations

import json

import pytest
from mcp.client.client import Client
from typer.testing import CliRunner

from anchor.cli import app
from anchor.config import Config
from anchor.fetcher import archive as archive_module
from anchor.server import build_server
from anchor.service import Anchor
from tests.integration.conftest import article_html

pytestmark = pytest.mark.anyio

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


def _json_object(output: str) -> dict:
    """CLI 출력에서 JSON 객체만 꺼낸다 — 설정 경고가 같은 스트림에 섞인다."""
    return json.JSONDecoder().raw_decode(output[output.index("{"):])[0]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _config(tmp_path, name="store.db") -> Config:
    return Config(
        db_path=tmp_path / name,
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        archive_fallback_enabled=True,
        archive_aggregator="",
    )


@pytest.fixture
def wayback(fixture_server, monkeypatch):
    base_url, state = fixture_server
    monkeypatch.setattr(archive_module, "WAYBACK_BASE", base_url)
    return base_url, state


def _kill_origin_and_publish_snapshot(state, *, snapshot_html: str) -> None:
    state.status_override = 404
    state.archive_html = snapshot_html


# ---------------------------------------------------------------------------
# cite — 앵커가 어느 판본에 붙었는가
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["live", "archive"])
def test_cite_reports_the_source_of_the_version_it_anchored(tmp_path, wayback, kind):
    base_url, state = wayback
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        fetched = anchor.fetch(f"{base_url}/article")
        if kind == "archive":
            _kill_origin_and_publish_snapshot(state, snapshot_html=article_html())
            fetched = anchor.fetch(f"{base_url}/article", max_age=0)
            assert fetched.source == "archive"
        result = anchor.cite(fetched.document_id, QUOTE)
        assert result.source == kind, (
            "앵커가 원본에 붙었는지 아카이브 스냅샷에 붙었는지를 응답이 말해야 한다"
        )


# ---------------------------------------------------------------------------
# verify — 대조 상대가 원본이었는가 아카이브였는가
# ---------------------------------------------------------------------------


def test_all_intact_report_still_discloses_archive_comparison(tmp_path, wayback):
    """attention이 비어 있어도 출처는 남아야 한다.

    이것이 D-093의 핵심이다 — 조치가 필요한 항목에만 출처를 달면, "원본은
    죽었고 아카이브만 봤다"는 사실은 **정상 보고에서 통째로 사라진다**.
    """
    base_url, state = wayback
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        fetched = anchor.fetch(f"{base_url}/article")
        anchor.cite(fetched.document_id, QUOTE)

        report = anchor.verify()
        assert report.summary["INTACT"] == 1
        assert report.sources == {"live": 1, "archive": 0, "none": 0}

        _kill_origin_and_publish_snapshot(state, snapshot_html=article_html())
        archived = anchor.verify()
        assert archived.summary["INTACT"] == 1
        assert archived.attention == ()
        assert archived.sources["archive"] == 1, (
            "원본 404 · 아카이브 스냅샷 대조인데 보고서에 그 사실이 없다"
        )
        assert sum(archived.sources.values()) == archived.checked


def test_attention_item_carries_the_source_it_was_compared_against(tmp_path, wayback):
    """개정이 잡힌 항목에서도 '무엇과 비교했는가'가 붙어야 한다."""
    base_url, state = wayback
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        fetched = anchor.fetch(f"{base_url}/article")
        anchor.cite(fetched.document_id, QUOTE)
        _kill_origin_and_publish_snapshot(
            state,
            snapshot_html=article_html().replace(
                "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다.",
                "링크는 살아 있지만 내용이 조금 바뀌는 인용 표류가 제일 위험하다.",
            ),
        )
        report = anchor.verify()
        (item,) = report.attention
        assert item.state == "ALTERED"
        assert item.source == "archive", (
            "아카이브 스냅샷과의 차이를 원본의 개정으로 읽게 해서는 안 된다"
        )


def test_unreachable_items_report_no_comparison_source(tmp_path, fixture_server):
    """대조 자체가 없었던 항목은 출처를 지어내지 않는다 — none으로 센다."""
    base_url, state = fixture_server
    config = Config(
        db_path=tmp_path / "s.db", rate_limit_rps=1000.0, retry_backoff_base=0.01
    )
    with Anchor(db_path=tmp_path / "s.db", config=config) as anchor:
        fetched = anchor.fetch(f"{base_url}/article")
        anchor.cite(fetched.document_id, QUOTE)
        state.status_override = 404  # 폴백 비활성 — 구제 없음
        report = anchor.verify()
        assert report.summary["GONE"] == 1
        assert report.sources == {"live": 0, "archive": 0, "none": 1}
        (item,) = report.attention
        assert item.source is None


# ---------------------------------------------------------------------------
# 진입점 — MCP 도구와 CLI
# ---------------------------------------------------------------------------


async def test_mcp_cite_and_verify_payloads_carry_source(tmp_path, wayback):
    base_url, state = wayback
    server, service = build_server(db_path=tmp_path / "mcp.db", config=_config(tmp_path, "mcp.db"))
    try:
        async with Client(server) as client:
            fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
            document_id = fetched.structured_content["document_id"]
            _kill_origin_and_publish_snapshot(state, snapshot_html=article_html())
            refetched = await client.call_tool(
                "fetch_document", {"url": f"{base_url}/article", "max_age": 0}
            )
            assert refetched.structured_content["source"] == "archive"

            cited = await client.call_tool(
                "cite", {"document_id": document_id, "quote": QUOTE}
            )
            assert cited.structured_content["source"] == "archive"

            verified = await client.call_tool("verify_citations", {})
            payload = verified.structured_content
            assert payload["sources"]["archive"] == 1, (
                "MCP 응답에 출처 집계가 없으면 클라이언트는 원본을 본 줄 안다"
            )
    finally:
        server.anchor_tasks.shutdown()
        service.close()


def test_cli_verify_output_names_the_archive(tmp_path, wayback, monkeypatch):
    """CLI 사람용 출력에서도 '아카이브와 대조했다'가 보여야 한다."""
    base_url, state = wayback
    db = str(tmp_path / "cli.db")
    runner = CliRunner()

    # CLI는 매 호출이 새 프로세스 격이라 폴백 설정을 환경변수로 준다.
    monkeypatch.setenv("ANCHOR_ARCHIVE_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("ANCHOR_RATE_LIMIT_RPS", "1000")

    fetched = runner.invoke(app, ["fetch", f"{base_url}/article", "--db", db, "--json"])
    assert fetched.exit_code == 0, fetched.output
    document_id = _json_object(fetched.output)["document_id"]
    cited = runner.invoke(app, ["cite", document_id, QUOTE, "--db", db, "--json"])
    assert cited.exit_code == 0, cited.output
    assert _json_object(cited.output)["source"] == "live"

    _kill_origin_and_publish_snapshot(state, snapshot_html=article_html())
    verified = runner.invoke(app, ["verify", "--db", db])
    assert verified.exit_code == 0, verified.output
    assert "archive" in verified.output or "아카이브" in verified.output, (
        f"출처가 사람용 출력에 없다: {verified.output!r}"
    )
