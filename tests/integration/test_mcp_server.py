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
                    name="verify_citations", arguments={}, task=TaskMetadata()
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


# -- 8단계-가: 새 사실이 MCP 응답에도 실린다 (D-227·D-229·D-230~D-232·D-235) --


async def test_mcp_responses_carry_the_new_facts(fixture_server, mcp_server):
    """같은 결함은 다른 진입점에도 있다 — 라이브러리·CLI에서 고친 사실이
    MCP 응답에서 빠지면 도구를 쓰는 클라이언트만 여전히 모른다."""
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        document_id = fetched.structured_content["document_id"]
        assert fetched.structured_content["outcome"] == "created"
        assert fetched.structured_content["raw_changed"] is False   # D-232

        cited = await client.call_tool("cite", {"document_id": document_id, "quote": QUOTE})
        # D-230: 어느 시점 판본에 닻을 내렸는가
        assert cited.structured_content["captured_at"]
        assert cited.structured_content["last_checked_at"]

        # D-232: <script> 안만 달라진 재수신 — 판정은 unchanged, 사실은 남는다
        state.html = article_html(nonce="n1")
        state.etag = '"v2"'
        again = await client.call_tool(
            "fetch_document", {"url": f"{base_url}/article", "max_age": 0}
        )
        assert again.structured_content["outcome"] == "unchanged"
        assert again.structured_content["raw_changed"] is True

        report = await client.call_tool("verify_citations", {})
        # D-231·D-235: 판정 뒤의 사실이 보고서 수준에 있다
        assert report.structured_content["ambiguous"] == 0
        assert report.structured_content["pipeline_changed"] == 0

        stats = await client.call_tool("cache_stats", {})
        window = stats.structured_content["last_30d"]
        # D-227: 첫 페치가 "바뀌었다"로 계상되지 않는다
        assert window["created"] == 1 and window["changed"] == 0
        detail = sum(
            window[name]
            for name in (
                "cache_hits", "not_modified", "unchanged", "created",
                "changed", "renormalized", "archive", "errors",
            )
        )
        assert detail == window["requests"]


async def test_mcp_verify_puts_unreachable_in_attention(fixture_server, mcp_server):
    """도구 설명이 "attention에는 조치가 필요한 항목만"이라고 안내하므로,
    아무것도 검증하지 못한 배치가 빈 attention이면 "이상 없음"으로 읽힌다 (D-229)."""
    base_url, state = fixture_server
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        await client.call_tool(
            "cite", {"document_id": fetched.structured_content["document_id"], "quote": QUOTE}
        )
        state.status_override = 403
        report = await client.call_tool("verify_citations", {})
    assert report.structured_content["summary"]["UNREACHABLE"] == 1
    assert [item["state"] for item in report.structured_content["attention"]] == ["UNREACHABLE"]


# -- 8단계-나: 포착 범위가 MCP 응답에도 실린다 (D-239~D-242) ------------------


async def test_mcp_responses_carry_the_coverage_facts(fixture_server, mcp_server):
    """라이브러리·CLI에서 고친 사실이 MCP 응답에서 빠지면, 도구를 쓰는
    클라이언트(=읽는 AI)만 여전히 자기가 얼마를 보는지 모른다."""
    from pathlib import Path

    structure = Path(__file__).parent.parent / "fixtures" / "structure"
    spec_html = (structure / "sidebar-spec.html").read_text("utf-8")
    quote = "This section introduces message framing and the vocabulary used throughout."

    base_url, state = fixture_server
    state.html = spec_html
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article"})
        payload = fetched.structured_content
        document_id = payload["document_id"]
        coverage = payload["coverage"]
        assert coverage["basis"] == "html-prose"
        assert coverage["ratio"] < 1 / 3
        assert any(item["structure"] == "aside" for item in coverage["dropped"])
        assert payload["notes"], "MCP 응답이 포착 범위에 대해 침묵한다"

        cited = await client.call_tool("cite", {"document_id": document_id, "quote": quote})
        assert cited.structured_content["coverage"]["ratio"] < 1 / 3
        assert cited.structured_content["warnings"]

        version = await client.call_tool(
            "get_version", {"document_id": document_id, "ref": "latest"}
        )
        assert version.structured_content["coverage"]["basis"] == "html-prose"

        # 판정은 그대로다 — 사각지대의 개정은 여전히 unchanged/INTACT다.
        state.html = spec_html.replace("MUST NOT", "MAY")
        state.etag = '"v2"'
        again = await client.call_tool(
            "fetch_document", {"url": f"{base_url}/article", "max_age": 0}
        )
        assert again.structured_content["outcome"] == "unchanged"
        assert again.structured_content["raw_changed"] is True
        assert len(again.structured_content["notes"]) == 2

        report = await client.call_tool("verify_citations", {})
        assert report.structured_content["summary"]["INTACT"] == 1
        assert report.structured_content["low_coverage"] == 1


async def test_fetch_document_discloses_a_redirect_without_judging_it(
    fixture_server, mcp_server
):
    """리다이렉트는 사실이고, soft-404 판정은 우리 일이 아니다 (D-247).

    영구 리다이렉트 직후 그 문서의 앵커가 전부 MISSING이면 그것이 soft-404의
    모양이다 — 두 신호 중 하나를 우리가 말하지 않으면 에이전트는 이을 수 없다.
    """
    base_url, state = fixture_server
    state.redirects = {"/moved": f"{base_url}/article"}
    state.redirect_status = 301
    async with Client(mcp_server) as client:
        moved = await client.call_tool("fetch_document", {"url": f"{base_url}/moved"})
        assert moved.structured_content["redirect"] == {
            "to": f"{base_url}/article",
            "permanent": True,
        }
        direct = await client.call_tool(
            "fetch_document", {"url": f"{base_url}/article", "max_age": 0}
        )
        assert "redirect" not in direct.structured_content


async def test_export_robust_links_names_the_url_the_caller_cited(
    fixture_server, mcp_server
):
    """MCP 경로에서도 내보내기가 정본 URL이 아니라 **인용한 URL**을 찍는다 (D-244)."""
    base_url, state = fixture_server
    state.redirects = {"/moved": f"{base_url}/article"}
    state.redirect_status = 301
    async with Client(mcp_server) as client:
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/moved"})
        cited = await client.call_tool(
            "cite", {"document_id": fetched.structured_content["document_id"], "quote": QUOTE}
        )
        exported = await client.call_tool(
            "export_robust_links",
            {"anchor_ids": [cited.structured_content["anchor_id"]]},
        )
        item = exported.structured_content["items"][0]["html"]
        assert f'data-originalurl="{base_url}/moved"' in item, item
        assert f'href="{base_url}/article"' in item, item

        timemap = await client.call_tool(
            "get_timemap", {"document_id": fetched.structured_content["document_id"]}
        )
        assert f'<{base_url}/moved>; rel="original"' in timemap.structured_content["body"]
