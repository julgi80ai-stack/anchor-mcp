# SPDX-License-Identifier: Apache-2.0
"""필터·예산 인자 검증 (D-121 / D-122).

축: **열거값 밖 문자열·대소문자**(status)와 **0·음수**(time_budget_ms),
그리고 같은 인자가 들어오는 **세 경로 전부**(MCP 동기 도구·task 경로·CLI).
경로마다 검증이 다르면 어느 한 경로의 사용자는 조용히 틀린 답을 받는다 —
status는 오류 없이 빈 목록("캐시가 비었다"로 읽힌다), 예산 0은 매칭
3·4단계를 건너뛰어 실제 개정 인용문을 ALTERED 대신 UNRESOLVED로 만든다.
"""

from __future__ import annotations

import pytest
from mcp.client.client import Client
from mcp_types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    TaskMetadata,
)
from typer.testing import CliRunner

from anchor.cli import app
from anchor.config import Config
from anchor.server import build_server
from anchor.service import Anchor

pytestmark = pytest.mark.anyio

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _config(tmp_path) -> Config:
    return Config(db_path=tmp_path / "store.db", rate_limit_rps=1000.0, retry_backoff_base=0.01)


# ---------------------------------------------------------------------------
# D-121 — list_documents(status=...)
# ---------------------------------------------------------------------------


def test_unknown_status_is_rejected_not_empty(tmp_path, fixture_server):
    """열거값 밖 status는 빈 목록이 아니라 명확한 거부여야 한다 — 같은 서버의
    format 인자들이 이미 그렇게 한다."""
    base_url, _state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base_url}/article")
        with pytest.raises(ValueError) as excinfo:
            anchor.list_documents(status="alive")
        # 허용값 목록이 메시지에 있어야 사용자가 스스로 고칠 수 있다
        assert "live" in str(excinfo.value)


def test_status_filter_is_case_insensitive(tmp_path, fixture_server):
    """'LIVE'가 0건을 돌려주면 모델은 캐시가 비었다고 읽는다 — 대소문자는
    뜻이 아니다."""
    base_url, _state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        anchor.fetch(f"{base_url}/article")
        assert len(anchor.list_documents(status="LIVE")) == 1


async def test_mcp_list_documents_unknown_status_returns_tool_error(tmp_path, fixture_server):
    base_url, _state = fixture_server
    server, service = build_server(db_path=tmp_path / "mcp.db", config=_config(tmp_path))
    try:
        async with Client(server) as client:
            await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
            result = await client.call_tool("list_documents", {"status": "alive"})
            assert result.is_error, (
                "열거값 밖 status가 오류 없이 응답됐다 — 빈 목록은 '캐시 없음'으로 "
                "읽힌다"
            )
    finally:
        server.anchor_tasks.shutdown()
        service.close()


# ---------------------------------------------------------------------------
# D-122 — time_budget_ms 0·음수, 세 경로 전부
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_budget", [0, -100])
def test_service_rejects_nonpositive_budget(tmp_path, fixture_server, bad_budget):
    """예산 0·음수는 매칭 3·4단계를 조용히 건너뛰게 만든다 — 실제 개정
    인용문이 ALTERED 대신 UNRESOLVED가 된다. 서비스 층(세 경로의 공통
    길목)에서 거부한다."""
    base_url, _state = fixture_server
    with Anchor(db_path=tmp_path / "s.db", config=_config(tmp_path)) as anchor:
        fetched = anchor.fetch(f"{base_url}/article")
        anchor.cite(fetched.document_id, QUOTE)
        with pytest.raises(ValueError):
            anchor.verify(time_budget_ms=bad_budget)


async def test_mcp_sync_verify_rejects_nonpositive_budget(tmp_path, fixture_server):
    base_url, _state = fixture_server
    server, service = build_server(db_path=tmp_path / "mcp.db", config=_config(tmp_path))
    try:
        async with Client(server) as client:
            fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
            await client.call_tool(
                "cite",
                {"document_id": fetched.structured_content["document_id"], "quote": QUOTE},
            )
            result = await client.call_tool("verify_citations", {"time_budget_ms": 0})
            assert result.is_error
    finally:
        server.anchor_tasks.shutdown()
        service.close()


async def test_task_verify_rejects_nonpositive_budget(tmp_path, fixture_server):
    """task 경로도 같은 검증을 받아야 한다 — 실패로 종결되고 사유가 남는다."""
    import anyio

    base_url, _state = fixture_server
    server, service = build_server(db_path=tmp_path / "task.db", config=_config(tmp_path))
    try:
        async with Client(server) as client:
            fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
            await client.call_tool(
                "cite",
                {"document_id": fetched.structured_content["document_id"], "quote": QUOTE},
            )
            created = await client.session.send_request(
                CallToolRequest(
                    params=CallToolRequestParams(
                        name="verify_citations",
                        arguments={"time_budget_ms": -5},
                        task=TaskMetadata(),
                    )
                ),
                CallToolResult,
            )
            task_id = created.structured_content["task"]["taskId"]

            import time as _time

            from mcp_types import GetTaskRequest, GetTaskRequestParams, GetTaskResult

            deadline = _time.monotonic() + 10
            while _time.monotonic() < deadline:
                got = await client.session.send_request(
                    GetTaskRequest(params=GetTaskRequestParams(task_id=task_id)),
                    GetTaskResult,
                )
                if got.status in ("completed", "failed", "cancelled"):
                    break
                await anyio.sleep(0.05)
            assert got.status == "failed", (
                f"예산 -5의 task가 {got.status}로 끝났다 — 조용한 UNRESOLVED 전락"
            )
    finally:
        server.anchor_tasks.shutdown()
        service.close()


def test_cli_rejects_nonpositive_budget_without_traceback(tmp_path, fixture_server):
    base_url, _state = fixture_server
    db = str(tmp_path / "cli.db")
    runner = CliRunner()
    fetched = runner.invoke(app, ["fetch", f"{base_url}/article", "--db", db])
    assert fetched.exit_code == 0, fetched.output
    result = runner.invoke(app, ["verify", "--budget-ms", "0", "--db", db])
    assert result.exit_code == 1, (
        f"exit={result.exit_code} — 트레이스백이 아니라 거부 메시지여야 한다: "
        f"{result.output[:300]}"
    )
    assert "양수" in result.output or "positive" in result.output
