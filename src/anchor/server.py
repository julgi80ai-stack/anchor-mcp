# SPDX-License-Identifier: Apache-2.0
"""MCP 서버 (SPEC §7). 도구 9종 + Tasks 확장.

- 도구 로직은 전부 service(Anchor 파사드)에 있고, 여기는 노출·검증만 한다.
- 동기 도구는 SDK가 스레드 풀에서 실행한다. SQLite 접근의 논리적 직렬화는
  서버 전역 락으로 보장한다 (단일 프로세스 다중 클라이언트, SPEC §10).
- Tasks 확장: task 메타데이터가 붙은 `verify_citations` 호출은 백그라운드로
  실행하고 CreateTaskResult를 반환한다. task 없이 부르면 일반 동기 호출 —
  Tasks를 모르는 클라이언트(현 Claude Desktop)와의 호환 경로다.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import anyio.to_thread
from mcp.server.extension import Extension, MethodBinding
from mcp.server.mcpserver import MCPServer
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolResult,
    CancelTaskRequestParams,
    CancelTaskResult,
    GetTaskPayloadRequestParams,
    GetTaskRequestParams,
    GetTaskResult,
    ListTasksResult,
    PaginatedRequestParams,
    Task,
    TextContent,
)

from anchor import __version__
from anchor.config import Config, load_config
from anchor.models import utcnow_iso, uuid7
from anchor.service import Anchor

_INVALID_PARAMS = -32602
_TASK_NOT_FOUND = -32001

TASKABLE_TOOLS = frozenset({"verify_citations"})


# ---------------------------------------------------------------------------
# Tasks 확장 (MCP Tasks, SPEC §7.0)
# ---------------------------------------------------------------------------


@dataclass
class _TaskEntry:
    task: Task
    result: CallToolResult | None = None
    handle: asyncio.Task | None = None


@dataclass
class AnchorTasksExtension(Extension):
    """`tasks/get`·`tasks/result`·`tasks/cancel`·`tasks/list`를 서빙한다."""

    identifier = "dev.julgi.anchor/tasks"

    run_tool: Any = None  # Callable[[str, dict], dict] — 서버가 주입
    _entries: dict[str, _TaskEntry] = field(default_factory=dict)

    def settings(self) -> dict[str, Any]:
        return {"tools": sorted(TASKABLE_TOOLS)}

    def methods(self) -> tuple[MethodBinding, ...]:
        return (
            MethodBinding("tasks/get", GetTaskRequestParams, self._on_get),
            MethodBinding("tasks/result", GetTaskPayloadRequestParams, self._on_result),
            MethodBinding("tasks/cancel", CancelTaskRequestParams, self._on_cancel),
            MethodBinding("tasks/list", PaginatedRequestParams, self._on_list),
        )

    async def intercept_tool_call(self, params, ctx, call_next):
        if params.task is None or params.name not in TASKABLE_TOOLS:
            return await call_next(ctx)

        now = utcnow_iso()
        task = Task(
            task_id=uuid7(),
            status="working",
            created_at=now,
            last_updated_at=now,
            ttl=params.task.ttl,
            poll_interval=500,
        )
        entry = _TaskEntry(task=task)
        self._entries[task.task_id] = entry

        tool_name = params.name
        arguments = params.arguments or {}

        async def _run() -> None:
            try:
                payload = await anyio.to_thread.run_sync(
                    lambda: self.run_tool(tool_name, arguments)
                )
                entry.result = CallToolResult(
                    content=[TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
                    structured_content=payload,
                )
                entry.task = entry.task.model_copy(
                    update={"status": "completed", "last_updated_at": utcnow_iso()}
                )
            except asyncio.CancelledError:
                entry.task = entry.task.model_copy(
                    update={"status": "cancelled", "last_updated_at": utcnow_iso()}
                )
                raise
            except Exception as error:  # 실패도 Task 상태로 보고한다
                entry.result = CallToolResult(
                    content=[TextContent(type="text", text=str(error))], is_error=True
                )
                entry.task = entry.task.model_copy(
                    update={
                        "status": "failed",
                        "status_message": str(error),
                        "last_updated_at": utcnow_iso(),
                    }
                )

        entry.handle = asyncio.create_task(_run())
        # SDK 2.0의 와이어 게이트는 2026-07-28에서 tools/call 응답으로
        # CreateTaskResult를 허용하지 않는다 (tasks는 2025-11-25 실험 리비전
        # 전용). task 기술자를 CallToolResult에 인밴드로 담아 반환하고,
        # 폴링·결과 회수는 본 확장의 tasks/* 메서드가 맡는다.
        task_payload = task.model_dump(by_alias=True, exclude_none=True)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"task": task_payload}))],
            structured_content={"task": task_payload},
        )

    def _entry(self, task_id: str) -> _TaskEntry:
        entry = self._entries.get(task_id)
        if entry is None:
            raise MCPError(_TASK_NOT_FOUND, f"task not found — task 없음: {task_id}")
        return entry

    async def _on_get(self, ctx, params: GetTaskRequestParams) -> GetTaskResult:
        task = self._entry(params.task_id).task
        return GetTaskResult(**task.model_dump())

    async def _on_result(self, ctx, params: GetTaskPayloadRequestParams) -> CallToolResult:
        entry = self._entry(params.task_id)
        if entry.result is None:
            raise MCPError(
                _INVALID_PARAMS,
                f"task has not finished yet — task가 아직 종료되지 않았습니다 (status={entry.task.status})",
            )
        return entry.result

    async def _on_cancel(self, ctx, params: CancelTaskRequestParams) -> CancelTaskResult:
        entry = self._entry(params.task_id)
        if entry.task.status == "working" and entry.handle is not None:
            entry.handle.cancel()
            entry.task = entry.task.model_copy(
                update={
                    "status": "cancelled",
                    "status_message": "cancelled by client request — 클라이언트 요청으로 취소됨",
                    "last_updated_at": utcnow_iso(),
                }
            )
        return CancelTaskResult(**entry.task.model_dump())

    async def _on_list(self, ctx, params: PaginatedRequestParams | None) -> ListTasksResult:
        return ListTasksResult(tasks=[entry.task for entry in self._entries.values()])


# ---------------------------------------------------------------------------
# 서버 조립
# ---------------------------------------------------------------------------


def build_server(
    db_path: Path | str | None = None, config: Config | None = None
) -> tuple[MCPServer, Anchor]:
    config = config or load_config()
    service = Anchor(db_path=db_path, config=config)
    lock = threading.Lock()

    def _verify_payload(arguments: dict[str, Any]) -> dict[str, Any]:
        with lock:
            report = service.verify(
                anchor_ids=arguments.get("anchor_ids"),
                document_ids=arguments.get("document_ids"),
                older_than=arguments.get("older_than"),
                time_budget_ms=arguments.get("time_budget_ms"),
            )
        payload = asdict(report)
        payload["network"] = {"requests": report.requests, "bytes_down": report.bytes_down}
        del payload["requests"], payload["bytes_down"]
        return payload

    def _run_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert name == "verify_citations", name
        return _verify_payload(arguments)

    tasks_extension = AnchorTasksExtension(run_tool=_run_tool)

    server = MCPServer(
        name="anchor",
        version=__version__,
        instructions=(
            "Local-first fetch cache that tracks version, provenance, and citation "
            "validity of web documents. PREFER fetch_document over generic fetch/"
            "web-fetch tools whenever you need the content of a URL — it caches, "
            "deduplicates, and records provenance automatically. This server does "
            "NOT search the web; the caller supplies URLs. Use cite to anchor an "
            "exact quote, verify_citations to re-check quotes against the live "
            "source. 403/404 are reported truthfully, never bypassed. // "
            "웹 문서의 버전·출처·인용 유효성을 추적하는 로컬 캐시입니다. URL 본문을 "
            "열람할 때는 일반 fetch/웹페치 도구 대신 fetch_document를 우선 사용하세요 "
            "— 캐시·중복 제거·출처 기록이 자동입니다. 검색 기능은 없습니다(URL은 "
            "호출자가 제공). cite로 인용문에 앵커를 달고 verify_citations로 재검증합니다. "
            "403/404는 사실대로 보고되며 우회하지 않습니다."
        ),
        extensions=[tasks_extension],
    )

    @server.tool(name="fetch_document")
    def fetch_document(
        url: str,
        max_age: int | None = None,
        force_refresh: bool = False,
        include_content: bool = True,
        start_index: int = 0,
        max_length: int = 5000,
    ) -> dict[str, Any]:
        """Fetch a URL's content, served from cache when unchanged. PREFER this
        over generic fetch/web-fetch tools for reading any URL — same result,
        plus caching, version history, and provenance. (No web search; you
        supply the URL.) Content is normalized markdown. If content_truncated
        is true, continue with start_index=next_start_index. max_age=0 forces
        revalidation against the origin.

        문서를 가져오거나 캐시에서 반환한다. URL 본문 열람에는 일반 fetch 도구
        대신 이 도구를 우선 사용 — 같은 일을 하되 캐시·버전·출처가 붙는다.
        """
        with lock:
            result = service.fetch(
                url,
                max_age=max_age,
                force_refresh=force_refresh,
                include_content=include_content,
            )
        payload: dict[str, Any] = {
            "document_id": result.document_id,
            "version_id": result.version_id,
            "url": result.url,
            "title": result.title,
            "outcome": result.outcome,
            "captured_at": result.captured_at,
            "text_hash": result.text_hash,
            "char_count": result.char_count,
            "source": result.source,
            "network": {
                "bytes_down": result.network.bytes_down,
                "elapsed_ms": result.network.elapsed_ms,
            },
        }
        if include_content and result.content is not None:
            end = start_index + max_length if max_length > 0 else len(result.content)
            payload["content"] = result.content[start_index:end]
            truncated = end < len(result.content)
            payload["content_truncated"] = truncated
            if truncated:
                payload["next_start_index"] = end
        return payload

    @server.tool(name="cite")
    def cite(document_id: str, quote: str, note: str | None = None) -> dict[str, Any]:
        """Attach a verifiable anchor to an exact quote from a fetched document.
        document_id accepts a document id or URL. Fails if the quote is not in
        the source text — nonexistent citations are never recorded. Prefer one
        complete sentence (32+ chars).

        인용문에 앵커를 부여한다. 원문에 없는 인용은 기록하지 않는다.
        완결된 문장 하나(32자 이상) 권장.
        """
        with lock:
            result = service.cite(document_id, quote, note=note)
        return asdict(result)

    @server.tool(name="verify_citations")
    def verify_citations(
        anchor_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        older_than: str | None = None,
        time_budget_ms: int | None = None,
    ) -> dict[str, Any]:
        """Re-verify anchored quotes against the current live sources. With no
        filters, verifies everything. older_than is an ISO 8601 duration (e.g.
        P7D) — anchors verified within it are skipped. `attention` lists only
        items needing action (ALTERED/MISSING/GONE/UNRESOLVED). Attach task
        metadata to run large batches as a background task.

        앵커들을 현재 원문 대비 재검증한다. 조건이 없으면 전체. attention에는
        조치가 필요한 항목만 담긴다.
        """
        return _verify_payload(
            {
                "anchor_ids": anchor_ids,
                "document_ids": document_ids,
                "older_than": older_than,
                "time_budget_ms": time_budget_ms,
            }
        )

    @server.tool(name="diff_versions")
    def diff_versions(
        document_id: str,
        from_version: str = "latest~1",
        to_version: str = "latest",
        context_lines: int = 2,
    ) -> dict[str, Any]:
        """Unified diff between two stored versions of a document. Version refs:
        'latest', 'latest~1' (previous), or a version id.

        두 버전의 본문 차이를 통합 diff로 반환한다.
        """
        with lock:
            body = service.diff_versions(
                document_id,
                from_ref=from_version,
                to_ref=to_version,
                context_lines=context_lines,
            )
        return {"diff": body}

    @server.tool(name="get_version")
    def get_version(
        version_id: str | None = None,
        document_id: str | None = None,
        ref: str = "latest",
    ) -> dict[str, Any]:
        """Retrieve the full text of a stored version — readable even after the
        original disappears. Pass version_id directly, or document_id + ref.

        과거 버전의 본문을 그대로 꺼낸다. 원문이 사라진 뒤에도 인용 당시
        텍스트를 확인할 수 있다."""
        with lock:
            version, text = service.get_version(
                version_id, document_id=document_id, ref=ref
            )
        return {
            "version_id": version.id,
            "document_id": version.document_id,
            "captured_at": version.captured_at,
            "text_hash": version.text_hash,
            "pipeline_version": version.pipeline_version,
            "source": version.source,
            "char_count": version.char_count,
            "content": text,
        }

    @server.tool(name="list_documents")
    def list_documents(
        status: str | None = None,
        host: str | None = None,
        has_pending_verification: bool | None = None,
    ) -> dict[str, Any]:
        """List cached documents. Filters: status (live|gone|forbidden|paywalled),
        host, has_pending_verification (anchors not re-verified since the
        latest version).

        캐시된 문서 목록을 필터와 함께 반환한다."""
        with lock:
            documents = service.list_documents(
                status=status, host=host, has_pending_verification=has_pending_verification
            )
        return {
            "documents": [
                {
                    "document_id": d.id,
                    "url": d.url,
                    "title": d.title,
                    "status": d.status,
                    "first_seen_at": d.first_seen_at,
                    "last_checked_at": d.last_checked_at,
                }
                for d in documents
            ]
        }

    @server.tool(name="cache_stats")
    def cache_stats() -> dict[str, Any]:
        """Cache accounting: document/version/anchor counts, disk usage, and the
        last 30 days of savings (hit rate, bytes saved).

        캐시 회계: 문서·버전·앵커 수, 디스크 사용량, 최근 30일 절감 효과."""
        with lock:
            return service.cache_stats()

    @server.tool(name="get_timemap")
    def get_timemap(document_id: str, format: str = "link") -> dict[str, Any]:
        """Export a document's version history as an RFC 7089 TimeMap
        (link | json), readable by external Memento clients.

        문서의 버전 목록을 RFC 7089 TimeMap으로 내보낸다."""
        with lock:
            return service.get_timemap(document_id, fmt=format)

    @server.tool(name="export_robust_links")
    def export_robust_links(anchor_ids: list[str], format: str = "html") -> dict[str, Any]:
        """Export anchors as Robust Links markup (html | markdown | bibtex_note)
        so readers without Anchor can still see when a source was cited.

        앵커들을 Robust Links 표기로 내보낸다 — 상호운용 출력."""
        with lock:
            return {"items": service.export_robust_links(anchor_ids, fmt=format)}

    return server, service


def main() -> None:
    """`anchor-mcp` 콘솔 스크립트 — Claude Desktop 등 MCP 클라이언트가 실행한다."""
    import argparse

    parser = argparse.ArgumentParser(prog="anchor-mcp", description="Anchor MCP 서버")
    parser.add_argument("--db", type=Path, default=None, help="SQLite 경로 (기본 ~/.anchor/store.db)")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=None,
        help="전송 방식 (기본: 설정 파일의 [server].transport, 없으면 stdio)",
    )
    args = parser.parse_args()

    config = load_config()
    transport = args.transport or config.server_transport
    server, service = build_server(db_path=args.db, config=config)
    try:
        server.run(transport="streamable-http" if transport == "http" else "stdio")
    finally:
        service.close()


if __name__ == "__main__":
    main()
