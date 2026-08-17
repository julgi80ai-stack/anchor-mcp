# SPDX-License-Identifier: Apache-2.0
"""MCP 서버 프로토콜 레벨 통합 테스트 — SDK 인메모리 전송으로 실제
initialize/tools·call/tasks 왕복을 검증한다."""

from __future__ import annotations

import anyio
import pytest
from mcp.client.client import Client
from mcp_types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    GetTaskRequest,
    GetTaskRequestParams,
    GetTaskResult,
    GetTaskPayloadRequest,
    GetTaskPayloadRequestParams,
    TaskMetadata,
)

from anchor.config import Config
from anchor.server import build_server
from tests.integration.conftest import article_html

pytestmark = pytest.mark.anyio

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."

EXPECTED_TOOLS = {
    "fetch_document",
    "cite",
    "verify_citations",
    "diff_versions",
    "get_version",
    "list_documents",
    "cache_stats",
    "get_timemap",
    "export_robust_links",
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mcp_server(fixture_server, tmp_path):
    config = Config(
        db_path=tmp_path / "store.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
    )
    server, service = build_server(db_path=config.db_path, config=config)
    yield server
    service.close()


async def test_exposes_nine_tools(mcp_server):
    async with Client(mcp_server) as client:
        result = await client.list_tools()
    names = {tool.name for tool in result.tools}
    assert names == EXPECTED_TOOLS


async def test_fetch_document_cache_hit_zero_bytes(fixture_server, mcp_server):
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        first = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        assert first.structured_content["outcome"] == "created"
        assert first.structured_content["network"]["bytes_down"] > 0

        second = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        assert second.structured_content["outcome"] == "cache_hit"
        assert second.structured_content["network"]["bytes_down"] == 0


async def test_fetch_document_chunked_reading(fixture_server, mcp_server):
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        first = await client.call_tool(
            "fetch_document", {"url": f"{base_url}/article", "max_length": 50}
        )
        payload = first.structured_content
        assert len(payload["content"]) == 50
        assert payload["content_truncated"] is True

        rest = await client.call_tool(
            "fetch_document",
            {
                "url": f"{base_url}/article",
                "start_index": payload["next_start_index"],
                "max_length": 0,
            },
        )
        combined = payload["content"] + rest.structured_content["content"]
        assert combined.startswith("# 재페치 낭비 보고서")
        assert rest.structured_content.get("content_truncated") is False


async def test_cite_and_verify_sync(fixture_server, mcp_server):
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        document_id = fetched.structured_content["document_id"]

        cited = await client.call_tool("cite", {"document_id": document_id, "quote": QUOTE})
        assert cited.structured_content["quality"] == "ok"

        report = await client.call_tool("verify_citations", {})
        assert report.structured_content["summary"]["INTACT"] == 1
        assert report.structured_content["attention"] == []


async def test_cite_missing_quote_is_tool_error(fixture_server, mcp_server):
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        result = await client.call_tool(
            "cite",
            {
                "document_id": fetched.structured_content["document_id"],
                "quote": "원문에 존재하지 않는 문장이다, 확실히 아니다.",
            },
        )
        assert result.is_error


async def test_diff_versions_after_change(fixture_server, mcp_server):
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        document_id = fetched.structured_content["document_id"]

        state.html = article_html(extra_sentence=" 새로 추가된 결론 문장이다.")
        state.etag = '"v2"'
        await client.call_tool("fetch_document", {"url": f"{base_url}/article", "max_age": 0})

        diff = await client.call_tool("diff_versions", {"document_id": document_id})
        body = diff.structured_content["diff"]
        assert "+" in body and "새로 추가된 결론 문장이다" in body


async def test_get_version_and_timemap(fixture_server, mcp_server):
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        document_id = fetched.structured_content["document_id"]

        version = await client.call_tool("get_version", {"document_id": document_id})
        assert version.structured_content["version_id"] == fetched.structured_content["version_id"]
        assert QUOTE in version.structured_content["content"]

        tm = await client.call_tool("get_timemap", {"document_id": document_id})
        assert tm.structured_content["content_type"] == "application/link-format"
        assert 'rel="original"' in tm.structured_content["body"]
        assert 'rel="first last memento"' in tm.structured_content["body"]


async def test_robust_links_and_stats_and_list(fixture_server, mcp_server):
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        document_id = fetched.structured_content["document_id"]
        cited = await client.call_tool("cite", {"document_id": document_id, "quote": QUOTE})

        links = await client.call_tool(
            "export_robust_links",
            {"anchor_ids": [cited.structured_content["anchor_id"]], "format": "html"},
        )
        (item,) = links.structured_content["items"]
        assert "data-versiondate=" in item["html"]

        stats = await client.call_tool("cache_stats", {})
        payload = stats.structured_content
        assert payload["documents"] == 1
        assert payload["anchors"] == 1
        assert payload["last_30d"]["requests"] >= 1

        listed = await client.call_tool("list_documents", {"status": "live"})
        assert len(listed.structured_content["documents"]) == 1


async def test_verify_citations_as_task(fixture_server, mcp_server):
    """Tasks 확장: task 메타데이터가 붙으면 백그라운드 실행 + 폴링 + 결과 회수."""
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        await client.call_tool(
            "cite", {"document_id": fetched.structured_content["document_id"], "quote": QUOTE}
        )

        created = await client.session.send_request(
            CallToolRequest(
                params=CallToolRequestParams(
                    name="verify_citations", arguments={}, task=TaskMetadata(ttl=60000)
                )
            ),
            CallToolResult,
        )
        task_descriptor = created.structured_content["task"]
        task_id = task_descriptor["taskId"]
        assert task_descriptor["status"] == "working"

        status = task_descriptor["status"]
        for _ in range(100):
            got = await client.session.send_request(
                GetTaskRequest(params=GetTaskRequestParams(task_id=task_id)),
                GetTaskResult,
            )
            status = got.status
            if status in ("completed", "failed", "cancelled"):
                break
            await anyio.sleep(0.05)
        assert status == "completed"

        payload = await client.session.send_request(
            GetTaskPayloadRequest(params=GetTaskPayloadRequestParams(task_id=task_id)),
            CallToolResult,
        )
        assert payload.structured_content["summary"]["INTACT"] == 1
