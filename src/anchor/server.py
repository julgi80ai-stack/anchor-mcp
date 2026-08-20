# SPDX-License-Identifier: Apache-2.0
"""MCP 서버 (SPEC §7). 도구 9종 + Tasks 확장.

- 도구 로직은 전부 service(Anchor 파사드)에 있고, 여기는 노출·검증만 한다.
- 동기 도구는 SDK가 스레드 풀에서 실행한다. 직렬화 책임은 서버가 아니라
  저장소와 서비스(URL 단위 락)에 있다 (SPEC §10 동시성 격리, D-038).
- Tasks 확장: task 메타데이터가 붙은 `verify_citations` 호출은 백그라운드로
  실행하고 task 기술자를 인밴드로 반환한다. task 없이 부르면 일반 동기 호출 —
  Tasks를 모르는 클라이언트(현 Claude Desktop)와의 호환 경로다.
"""

from __future__ import annotations

import base64
import json
import sys
import threading
import time
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
    GetTaskPayloadRequestParams,
    GetTaskRequestParams,
    PaginatedRequestParams,
    Task,
    TextContent,
)

from anchor import __version__
from anchor.anchoring import approx
from anchor.config import SERVER_TRANSPORTS, Config, load_config
from anchor.errors import AnchorError
from anchor.models import parse_iso, utcnow_iso, uuid7
from anchor.service import Anchor

_INVALID_PARAMS = -32602
_TASK_NOT_FOUND = -32001

TASKABLE_TOOLS = frozenset({"verify_citations"})

# ttl 정책 (D-116 / D-123). Task.ttl은 required-nullable이라 None을 저장하면
# exclude_none 직렬화 경로에서 키가 사라져 표준 클라이언트의 스키마 검증이
# 응답을 거부하고, prune이 건너뛰어 항목이 무한히 쌓인다(D-037의 재발).
# 보존 기간을 항상 유한한 실제 값으로 정해 두 문제를 원천에서 없앤다.
_TASK_DEFAULT_TTL_MS = 30 * 60 * 1000  # ttl 미지정 시 기본 보존 30분
_TASK_MAX_TTL_MS = 24 * 60 * 60 * 1000  # 요청 ttl 상한 24시간

# tasks/list 한 페이지의 크기 (D-054). 장기 실행 서버에서 전량 반환은 응답이
# 목록 크기만큼 커진다 — MCP의 커서 페이지네이션이 있는 이유다. 항목 하나가
# 수백 바이트이므로 50건이면 응답은 수십 KB에 머물고, 대부분의 클라이언트는
# 첫 페이지만으로 끝난다.
_TASK_PAGE_SIZE = 50
_CURSOR_PREFIX = "tasks/list:"

# shutdown이 "등록됐지만 스레드가 시작되지 않은" 항목을 기다려 주는 시간.
# 정상 경로에서 이 상태는 등록→start() 사이의 찰나지만, start()가 실패하면
# (스레드 한도 등) 영구히 남는다 — 무한히 기다리면 서버가 영영 안 꺼진다.
_TASK_START_PATIENCE_S = 2.0


# ---------------------------------------------------------------------------
# Tasks 확장 (MCP Tasks, SPEC §7.0)
# ---------------------------------------------------------------------------


def _encode_cursor(task_id: str) -> str:
    """불투명 커서. 클라이언트가 해석하지 않도록 인코딩한다 (MCP: opaque)."""
    return base64.urlsafe_b64encode(f"{_CURSOR_PREFIX}{task_id}".encode()).decode()


def _decode_cursor(cursor: str | None) -> str | None:
    """커서를 '이 id 다음부터'로 되돌린다. 우리가 발급한 것이 아니면 거부한다 —
    조용히 처음부터 주면 클라이언트는 같은 항목을 두 번 읽고도 모른다."""
    if cursor is None:
        return None
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, UnicodeDecodeError) as error:
        raise MCPError(_INVALID_PARAMS, f"invalid cursor — 잘못된 커서: {cursor!r}") from error
    if not decoded.startswith(_CURSOR_PREFIX):
        raise MCPError(_INVALID_PARAMS, f"invalid cursor — 잘못된 커서: {cursor!r}")
    return decoded[len(_CURSOR_PREFIX):]


@dataclass
class _TaskEntry:
    task: Task
    result: CallToolResult | None = None
    worker: threading.Thread | None = None
    cancel: threading.Event = field(default_factory=threading.Event)


@dataclass
class AnchorTasksExtension(Extension):
    """`tasks/get`·`tasks/result`·`tasks/cancel`·`tasks/list`를 서빙한다."""

    identifier = "dev.julgi.anchor/tasks"

    run_tool: Any = None  # Callable[[str, dict, Callable[[], bool]], dict] — 서버가 주입
    _entries: dict[str, _TaskEntry] = field(default_factory=dict)
    _entries_lock: threading.RLock = field(default_factory=threading.RLock)

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
        if params.task is None:
            return await call_next(ctx)
        if params.name not in TASKABLE_TOOLS:
            # 조용히 동기 실행하면 클라이언트는 task 기술자도 거부 사유도
            # 받지 못한다 (D-041). 무엇이 task로 실행 가능한지 알려준다.
            raise MCPError(
                _INVALID_PARAMS,
                f"{params.name} cannot run as a task; taskable tools: "
                f"{', '.join(sorted(TASKABLE_TOOLS))} — 이 도구는 task로 실행할 수 없습니다",
            )

        requested_ttl = params.task.ttl
        if requested_ttl is None:
            ttl = _TASK_DEFAULT_TTL_MS
        elif requested_ttl <= 0:
            # 0·음수 ttl은 첫 prune에서 항목을 즉시 지워 결과 회수를 원천
            # 불가능하게 만든다 — 조용히 받지 않고 거부한다 (D-123).
            raise MCPError(
                _INVALID_PARAMS,
                f"task ttl must be positive — ttl은 양수여야 합니다: {requested_ttl}",
            )
        else:
            ttl = min(requested_ttl, _TASK_MAX_TTL_MS)

        now = utcnow_iso()
        task = Task(
            task_id=uuid7(),
            status="working",
            created_at=now,
            last_updated_at=now,
            ttl=ttl,
            poll_interval=500,
        )
        self.prune()
        entry = _TaskEntry(task=task)

        tool_name = params.name
        arguments = params.arguments or {}

        def _run() -> None:
            # 추적 가능한 스레드에서 돈다. anyio.to_thread의 워커는 join할 수
            # 없어 종료 시 커넥션 해제와 겹치면 SIGSEGV가 났다 (D-034).
            # 배경 워커의 매칭 루프는 전경 도구 경로에 GIL을 양보한다 (D-120).
            approx.set_thread_yields(True)
            try:
                payload = self.run_tool(tool_name, arguments, entry.cancel.is_set)
                result = CallToolResult(
                    content=[
                        TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))
                    ],
                    structured_content=payload,
                )
                status = "cancelled" if entry.cancel.is_set() else "completed"
                self._finish(entry, result, status)
            except Exception as error:  # 실패도 Task 상태로 보고한다
                self._finish(
                    entry,
                    CallToolResult(
                        content=[TextContent(type="text", text=str(error))], is_error=True
                    ),
                    "failed",
                    str(error),
                )

        # 스레드 배정 → 등록 → start 순서. 등록과 start 사이에 await 지점이
        # 없어, 등록된 working 항목이 "워커 없음" 상태로 관측될 수 없다.
        entry.worker = threading.Thread(target=_run, name=f"anchor-task-{task.task_id[:8]}")
        with self._entries_lock:
            self._entries[task.task_id] = entry
        try:
            entry.worker.start()
        except Exception as error:
            # 시작 실패를 종결로 기록하지 않으면 task가 영원히 working으로
            # 남아 shutdown이 배정을 무한히 기다린다.
            self._finish(
                entry,
                CallToolResult(
                    content=[TextContent(type="text", text=str(error))], is_error=True
                ),
                "failed",
                f"worker start failed — 워커 시작 실패: {error}",
            )
            raise
        # SDK 2.0의 와이어 게이트는 2026-07-28에서 tools/call 응답으로
        # CreateTaskResult를 허용하지 않는다 (tasks는 2025-11-25 실험 리비전
        # 전용). task 기술자를 CallToolResult에 인밴드로 담아 반환하고,
        # 폴링·결과 회수는 본 확장의 tasks/* 메서드가 맡는다.
        task_payload = self._wire_task(task)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"task": task_payload}))],
            structured_content={"task": task_payload},
        )

    @staticmethod
    def _wire_task(task: Task) -> dict[str, Any]:
        """Task의 와이어 표현. `Task.ttl`은 required-nullable이라 exclude_none
        직렬화가 ttl=None의 키를 지우면 표준 클라이언트의 스키마 검증이 응답을
        통째로 거부한다 (D-116) — ttl 키는 항상 명시적으로 싣는다."""
        payload = task.model_dump(by_alias=True, exclude_none=True)
        payload["ttl"] = task.ttl
        return payload

    def _finish(
        self,
        entry: _TaskEntry,
        result: CallToolResult,
        status: str,
        message: str | None = None,
    ) -> None:
        """워커 스레드에서 종결 상태를 기록한다. 취소된 task도 결과를 남겨
        `tasks/result`가 영원히 '아직 안 끝남'을 반환하지 않게 한다 (D-036)."""
        with self._entries_lock:
            entry.result = result
            now = utcnow_iso()
            update: dict[str, Any] = {"status": status, "last_updated_at": now}
            if message is not None:
                update["status_message"] = message
            if entry.task.ttl is not None:
                # 보존 기간은 생성 시점부터 센다(프로토콜 정의, D-123). 실행이
                # 요청 ttl보다 오래 걸리면 결과가 종결과 동시에 소멸하므로,
                # 종결 시점에 실제 보존 기간(경과 + 요청 ttl)으로 갱신해
                # 보고한다 — Task.ttl은 '실제' 보존 기간이라 이 갱신은
                # 프로토콜이 예정한 서버 재량이다. 따라서 상한(_TASK_MAX_TTL_MS)
                # 은 **종결 후 보존분**에 대한 것이고, 실행이 길면 생성 기준
                # 총 보존은 상한을 넘을 수 있다 (D-200, SPEC §7.0).
                elapsed_ms = int(
                    (parse_iso(now) - parse_iso(entry.task.created_at)).total_seconds() * 1000
                )
                update["ttl"] = elapsed_ms + entry.task.ttl
            entry.task = entry.task.model_copy(update=update)

    def prune(self) -> None:
        """ttl이 지난 종결 task를 정리한다 (D-037).

        `_entries`에 삭제 경로가 없어 장기 실행 서버에서 무한히 쌓였다.
        각 항목이 verify 리포트 전문을 붙들고 있어 비용이 작지 않다.
        """
        now = utcnow_iso()
        with self._entries_lock:
            for task_id, entry in list(self._entries.items()):
                if entry.task.status == "working" or entry.task.ttl is None:
                    continue
                # 프로토콜 정의대로 생성 시점부터 센다 (D-123). 종결이 늦은
                # task는 _finish가 ttl을 실제 보존 기간으로 늘려 두었다.
                age_ms = (
                    parse_iso(now) - parse_iso(entry.task.created_at)
                ).total_seconds() * 1000
                if age_ms >= entry.task.ttl:
                    del self._entries[task_id]

    def shutdown(self, grace: float = 10.0) -> None:
        """진행 중인 task에 중단을 알리고 **워커가 전부 끝난 뒤에만** 반환한다.

        반환 후에는 어떤 워커도 저장소를 건드리지 않는다 — 그때부터 저장소를
        닫아도 안전하다는 것이 이 메서드의 계약이다 (D-034). 이전에는 유예가
        지나면 워커가 살아 있어도 조용히 반환했고, 호출자가 곧바로 닫은
        저장소 밑에서 워커가 죽어 부분 결과가 통째로 사라졌다 (D-117).

        워커는 문서·앵커 사이마다 중단 신호를 확인한다(D-119). 대기의
        상한은 앵커 하나의 예산 + **진행 중인 한 문서의 페치 전체**(robots·
        리다이렉트 홉·재시도·아카이브 폴백 — 요청 여러 건, D-206)다 —
        네트워크 대기 중에는 신호를 확인하지 않으므로, 기본 설정 조합에서는
        분 단위가 될 수 있다 (D-197). grace를 넘기면
        stderr로 알리고 계속 기다린다 —
        인터프리터도 어차피 non-daemon 스레드의 종료를 기다린다: 같은 대기를
        저장소가 열린 채로 할 뿐이다.
        """
        deadline = time.monotonic() + grace
        start_patience = time.monotonic() + _TASK_START_PATIENCE_S
        warned = False
        while True:
            with self._entries_lock:
                entries = list(self._entries.values())
            for entry in entries:
                entry.cancel.set()

            pending: list[_TaskEntry] = []
            for entry in entries:
                worker = entry.worker
                if worker is not None and worker.is_alive():
                    pending.append(entry)
                elif entry.task.status == "working" and (
                    worker is None or worker.ident is None
                ):
                    # 등록은 됐지만 스레드가 시작되지 않은 항목. 정상 경로의
                    # 찰나일 수 있어 잠시 기다리되, start() 실패의 고아라면
                    # 영원히 오지 않는다 — 유예가 지나면 실패로 종결하고
                    # 진행한다 (기다리면 서버가 영영 안 꺼진다).
                    if time.monotonic() >= start_patience:
                        self._finish(
                            entry,
                            CallToolResult(
                                content=[
                                    TextContent(
                                        type="text",
                                        text="worker never started — 워커가 시작되지 않았습니다",
                                    )
                                ],
                                is_error=True,
                            ),
                            "failed",
                            "worker never started — 워커가 시작되지 않았습니다",
                        )
                    else:
                        pending.append(entry)

            if not pending:
                return
            if not warned and time.monotonic() >= deadline:
                names = ", ".join(entry.task.task_id for entry in pending)
                print(
                    f"anchor: waiting for background task(s) {names} to stop before "
                    "closing the store — 저장소를 닫기 전에 백그라운드 task 종료를 "
                    "기다리는 중",
                    file=sys.stderr,
                )
                warned = True
            for entry in pending:
                worker = entry.worker
                if worker is not None and worker.is_alive():
                    worker.join(timeout=0.5)
                else:
                    time.sleep(0.01)

    def _entry(self, task_id: str) -> _TaskEntry:
        with self._entries_lock:
            entry = self._entries.get(task_id)
        if entry is None:
            raise MCPError(_TASK_NOT_FOUND, f"task not found — task 없음: {task_id}")
        return entry

    async def _on_get(self, ctx, params: GetTaskRequestParams) -> dict[str, Any]:
        # dict로 반환한다 — 모델로 반환하면 SDK가 exclude_none으로 직렬화해
        # ttl=None의 키를 지우고, 응답이 클라이언트 검증에서 죽는다 (D-116).
        return self._wire_task(self._entry(params.task_id).task)

    async def _on_result(self, ctx, params: GetTaskPayloadRequestParams) -> CallToolResult:
        entry = self._entry(params.task_id)
        if entry.result is None:
            raise MCPError(
                _INVALID_PARAMS,
                f"task has not finished yet — task가 아직 종료되지 않았습니다 (status={entry.task.status})",
            )
        return entry.result

    async def _on_cancel(self, ctx, params: CancelTaskRequestParams) -> dict[str, Any]:
        entry = self._entry(params.task_id)
        if entry.task.status == "working":
            # 중단 신호만 세운다. 워커는 문서·앵커 사이에서 이를 확인하고
            # 남은 작업을 건드리지 않는다 (D-035/D-119). 실제 종결 상태는
            # 워커가 멈춘 뒤 _finish가 기록한다.
            entry.cancel.set()
            with self._entries_lock:
                entry.task = entry.task.model_copy(
                    update={
                        "status_message": (
                            "cancellation requested — 취소 요청됨 (진행 중 작업을 정리하는 중)"
                        ),
                        "last_updated_at": utcnow_iso(),
                    }
                )
            if entry.worker is not None:
                await anyio.to_thread.run_sync(lambda: entry.worker.join(timeout=5.0))
        # dict 반환 이유는 _on_get과 같다 (D-116).
        return self._wire_task(entry.task)

    async def _on_list(self, ctx, params: PaginatedRequestParams | None) -> dict[str, Any]:
        """커서 기반 페이지네이션 (MCP §pagination, D-054).

        정렬 기준은 task id다 — uuid7이라 사전순이 곧 생성 시각순이므로,
        별도 정렬 키 없이 안정적이고 클라이언트가 읽는 순서도 자연스럽다.
        커서는 **위치가 아니라 항목**을 가리킨다("이 id 다음부터") — 페이지
        사이에서 prune이 항목을 지워도 남은 것을 건너뛰지 않는다.
        """
        self.prune()
        after = _decode_cursor(params.cursor if params is not None else None)
        with self._entries_lock:
            ordered = sorted(self._entries.values(), key=lambda entry: entry.task.task_id)
        if after is not None:
            ordered = [entry for entry in ordered if entry.task.task_id > after]
        page = ordered[:_TASK_PAGE_SIZE]
        # dict 반환 이유는 _on_get과 같다 (D-116) — ttl 키가 빠진 항목 하나가
        # tasks/list 전체를 영구히 파싱 불가로 만들었다.
        payload: dict[str, Any] = {"tasks": [self._wire_task(entry.task) for entry in page]}
        if len(ordered) > len(page):
            payload["nextCursor"] = _encode_cursor(page[-1].task.task_id)
        return payload


# ---------------------------------------------------------------------------
# 서버 조립
# ---------------------------------------------------------------------------


def build_server(
    db_path: Path | str | None = None, config: Config | None = None
) -> tuple[MCPServer, Anchor]:
    config = config or load_config()
    # 전역 락을 두지 않는다 (D-038). 저장소는 내부적으로 직렬화되고
    # (`_SerializedConnection`), 문서 생성 경합은 서비스가 URL 단위 락으로
    # 막는다. 전역 락은 백그라운드 verify가 도는 동안 나머지 도구 전부를
    # 멈추게 했다 — Tasks 확장의 목적과 정면으로 어긋난다.
    service = Anchor(db_path=db_path, config=config)

    def _verify_payload(
        arguments: dict[str, Any], should_stop: Any = None
    ) -> dict[str, Any]:
        report = service.verify(
            anchor_ids=arguments.get("anchor_ids"),
            document_ids=arguments.get("document_ids"),
            older_than=arguments.get("older_than"),
            time_budget_ms=arguments.get("time_budget_ms"),
            should_stop=should_stop,
        )
        payload = asdict(report)
        payload["network"] = {
            "requests": report.requests,
            "not_modified": report.not_modified,
            "bytes_down": report.bytes_down,
        }
        del payload["requests"], payload["bytes_down"], payload["not_modified"]
        return payload

    def _run_tool(name: str, arguments: dict[str, Any], should_stop: Any) -> dict[str, Any]:
        assert name == "verify_citations", name
        return _verify_payload(arguments, should_stop)

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
            "exact quote, verify_citations to re-check quotes against their "
            "current source (the live origin, or an archive snapshot when the "
            "original is unreachable). 403/404 are reported truthfully, never "
            "bypassed. // "
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

        Besides the content, the response states what this tool does NOT know,
        so you can decide whether to check further. `outcome` is the verdict
        (cache_hit/not_modified/unchanged/changed/renormalized/created/archive);
        the fields below never change it, they only qualify it.

        - `coverage`: how much of the page's prose the stored body actually
          contains (`ratio` null = not measurable). A low ratio means a
          revision outside the stored body would leave `outcome` unchanged.
        - `notes`: plain-sentence caveats about that blind spot, when there
          are any.
        - `raw_changed`: the origin's raw bytes differ from the previous
          observation while the extracted body does not — a change happened
          outside what we store.
        - `redirect`: the redirect actually followed on this call
          (`to`, `permanent`). Reported as fact; whether it means the document
          moved or vanished is yours to judge.
        - `source`: `live` (the origin) or `archive` (a snapshot).

        문서를 가져오거나 캐시에서 반환한다. URL 본문 열람에는 일반 fetch 도구
        대신 이 도구를 우선 사용 — 같은 일을 하되 캐시·버전·출처가 붙는다.
        본문 외에 coverage(포착 범위)·notes(사각지대 고지)·raw_changed(직전
        관측 대비 원본 바이트 변화)·redirect(이번에 따라간 리다이렉트)·source
        (live|archive)가 함께 온다. 이 값들은 outcome을 바꾸지 않는다.
        """
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
            # 원본 바이트는 달라졌는데 추출 본문은 같았다 (D-232). 판정
            # (outcome)은 이 값과 무관하다 — 사실을 하나 더할 뿐이다.
            "raw_changed": result.raw_changed,
            # 저장 본문이 이 문서의 얼마를 담고 있는가 (D-239). `outcome`은
            # 이 값과 무관하다 — 판정이 문서의 얼마를 보고 내려진 것인지를
            # 함께 밝힐 뿐이다. `ratio`가 null이면 재지 못했다는 뜻이다.
            "coverage": result.coverage.as_payload(),
            "notes": list(result.notes),
            "network": {
                "bytes_down": result.network.bytes_down,
                "elapsed_ms": result.network.elapsed_ms,
            },
        }
        if result.redirect is not None:
            # 이번 호출에서 실제로 따라간 리다이렉트다 (D-247). **사실만
            # 싣는다** — 이것이 soft-404인지, 인용이 아직 유효한지는 판정하지
            # 않는다. 영구 리다이렉트 뒤 그 문서의 앵커가 전부 MISSING이면
            # 그것이 soft-404의 모양이고, 두 신호를 잇는 것은 호출자의 일이다.
            payload["redirect"] = {
                "to": result.redirect.to,
                "permanent": result.redirect.permanent,
            }
        if include_content and result.content is not None:
            if start_index < 0 or max_length < 0:
                raise ValueError(
                    f"start_index and max_length must be >= 0, got {start_index}/{max_length}"
                    " — 음수는 허용되지 않습니다"
                )
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
        result = service.cite(document_id, quote, note=note)
        payload = asdict(result)
        # `ratio`는 파생값이라 asdict에 담기지 않는다 (D-240).
        payload["coverage"] = result.coverage.as_payload()
        return payload

    @server.tool(name="verify_citations")
    def verify_citations(
        anchor_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        older_than: str | None = None,
        time_budget_ms: int | None = None,
    ) -> dict[str, Any]:
        """Re-verify anchored quotes against their current source — the live
        origin, or an archive snapshot when the original is unreachable; each
        item and the `sources` totals say which. With no filters, verifies
        everything. older_than is an ISO 8601 duration (e.g. P7D) — anchors
        verified within it are skipped. `attention` lists only items needing
        action (ALTERED/MISSING/GONE/UNREACHABLE/UNRESOLVED). Attach task
        metadata to run large batches as a background task.

        앵커들을 현재 원문(또는 원본에 닿지 못하면 아카이브 스냅샷) 대비
        재검증한다. 조건이 없으면 전체. attention에는 조치가 필요한 항목만
        담긴다.
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
            # 이 본문이 원본 문서의 얼마였는가 (D-239). 원문이 사라진 뒤에
            # 꺼내 보는 자리이므로 더욱 필요하다.
            "coverage": version.coverage.as_payload(),
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
        return service.cache_stats()

    @server.tool(name="get_timemap")
    def get_timemap(document_id: str, format: str = "link") -> dict[str, Any]:
        """Export a document's version history as an RFC 7089 TimeMap
        (link | json), readable by external Memento clients.

        문서의 버전 목록을 RFC 7089 TimeMap으로 내보낸다."""
        return service.get_timemap(document_id, fmt=format)

    @server.tool(name="export_robust_links")
    def export_robust_links(anchor_ids: list[str], format: str = "html") -> dict[str, Any]:
        """Export anchors as Robust Links markup (html | markdown | bibtex_note)
        so readers without Anchor can still see when a source was cited.

        앵커들을 Robust Links 표기로 내보낸다 — 상호운용 출력."""
        return {"items": service.export_robust_links(anchor_ids, fmt=format)}

    server.anchor_tasks = tasks_extension
    return server, service


def serve_forever(server: MCPServer, service: Anchor, transport: str | None) -> None:
    """서버를 돌리고, 어떤 진입점이든 같은 종료 순서를 지킨다: 워커 정리 →
    저장소 해제. 순서를 어기면 워커가 쓰는 커넥션을 닫아 부분 결과가 사라진다
    (D-034/D-117). 두 진입점(`anchor-mcp`, `anchor serve`)이 이 계약을 따로
    구현하다 한쪽만 지키는 상태가 D-118이었다 — 공유 경로 하나로 합친다."""
    # 백그라운드 verify 워커(순수 파이썬 CPU)와 지연 민감 도구 경로가 한
    # 프로세스에 공존하는 것은 서빙 동안뿐이다 (D-120). 매칭 루프는 스스로
    # 양보하지만(anchoring/approx의 _yield_gil), 정규화 등 나머지 CPU 구간의
    # 비자발 점유도 1ms로 상한을 건다. 전역 인터프리터 설정이므로 서빙
    # 구간에만 걸고 끝나면 되돌린다 — build_server(라이브러리 조립 함수)에서
    # 걸면 호스트 앱·테스트 프로세스 전체가 영향을 받는다 (D-199).
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.001)
    try:
        server.run(transport="streamable-http" if transport == "http" else "stdio")
    finally:
        sys.setswitchinterval(previous_interval)
        server.anchor_tasks.shutdown()
        service.close()


def main() -> None:
    """`anchor-mcp` 콘솔 스크립트 — Claude Desktop 등 MCP 클라이언트가 실행한다."""
    import argparse

    parser = argparse.ArgumentParser(prog="anchor-mcp", description="Anchor MCP 서버")
    parser.add_argument("--db", type=Path, default=None, help="SQLite 경로 (기본 ~/.anchor/store.db)")
    parser.add_argument(
        "--transport",
        # 세 진입점이 **같은 사전**을 본다 (D-150).
        choices=list(SERVER_TRANSPORTS),
        default=None,
        help="전송 방식 (기본: 설정 파일의 [server].transport, 없으면 stdio)",
    )
    args = parser.parse_args()

    try:
        config = load_config()
        transport = args.transport or config.server_transport
        server, service = build_server(db_path=args.db, config=config)
    except AnchorError as error:
        # 설정 오류·저장소 오류로 서버가 못 뜨는 것은 정상적인 실패다.
        # 트레이스백을 stdio로 토해내면 MCP 클라이언트가 프로토콜 오류로
        # 읽는다 — 사실을 한 줄로 말하고 비정상 종료한다 (D-138·D-141).
        print(f"anchor-mcp 실패: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    serve_forever(server, service, transport)


if __name__ == "__main__":
    main()
