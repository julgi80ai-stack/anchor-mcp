# SPDX-License-Identifier: Apache-2.0
"""Task ttl 축 회귀 (D-116 / D-123).

축: **ttl을 지정하지 않는 클라이언트** — 프로토콜의 기본 호출 형태다.
기존 task 테스트 3곳이 전부 ttl=60000을 하드코딩해 이 경로가 한 번도
시험되지 않았고, 그 틈에서 D-116이 살아남았다: `Task.ttl`은
required-nullable이라 exclude_none 직렬화가 ttl 키를 지우면 표준
클라이언트의 스키마 검증이 응답 전부(인밴드 기술자·tasks/get·tasks/list·
tasks/cancel)를 거부한다.

보조 축: ttl 경계값(0·음수·10^30)과 prune 기준 시각(D-123).
"""

from __future__ import annotations

import time

import anyio
import pytest
from mcp.client.client import Client
from mcp.shared.exceptions import MCPError
from mcp_types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    CancelTaskRequest,
    CancelTaskRequestParams,
    CancelTaskResult,
    GetTaskPayloadRequest,
    GetTaskPayloadRequestParams,
    GetTaskRequest,
    GetTaskRequestParams,
    GetTaskResult,
    ListTasksRequest,
    ListTasksResult,
    Task,
    TaskMetadata,
)

from anchor.config import Config
from anchor.server import _TASK_DEFAULT_TTL_MS, _TASK_MAX_TTL_MS, build_server

pytestmark = pytest.mark.anyio

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def task_server(fixture_server, tmp_path):
    config = Config(
        db_path=tmp_path / "tasks.db", rate_limit_rps=1000.0, retry_backoff_base=0.01
    )
    server, service = build_server(db_path=config.db_path, config=config)
    yield server, service
    server.anchor_tasks.shutdown()
    service.close()


async def _seed(client, base_url, count: int) -> None:
    for index in range(count):
        fetched = await client.call_tool(
            "fetch_document", {"url": f"{base_url}/article?doc={index}"}
        )
        await client.call_tool(
            "cite", {"document_id": fetched.structured_content["document_id"], "quote": QUOTE}
        )


async def _create_task(client, metadata: TaskMetadata) -> dict:
    created = await client.session.send_request(
        CallToolRequest(
            params=CallToolRequestParams(
                name="verify_citations", arguments={}, task=metadata
            )
        ),
        CallToolResult,
    )
    return created.structured_content["task"]


async def _wait_terminal(client, task_id: str, timeout: float = 30.0) -> GetTaskResult:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = await client.session.send_request(
            GetTaskRequest(params=GetTaskRequestParams(task_id=task_id)), GetTaskResult
        )
        if got.status in ("completed", "failed", "cancelled"):
            return got
        await anyio.sleep(0.05)
    raise AssertionError("task가 종료 상태에 도달하지 않았다")


async def test_default_call_without_ttl_parses_everywhere(fixture_server, task_server):
    """D-116: ttl을 생략한 기본 호출의 전 응답이 표준 스키마 검증을 통과해야 한다.

    인밴드 기술자는 여기서 직접 `Task.model_validate`로 검증하고,
    tasks/get·tasks/list·tasks/cancel은 클라이언트 SDK의 응답 모델 검증이
    곧 단언이다(ttl 키가 빠지면 ValidationError로 즉사한다).
    """
    base_url, _state = fixture_server
    server, _service = task_server
    async with Client(server) as client:
        await _seed(client, base_url, 1)

        raw = await _create_task(client, TaskMetadata())  # ttl 미지정
        descriptor = Task.model_validate(raw)  # 인밴드 기술자 자체가 스키마 유효해야 한다
        assert descriptor.ttl == _TASK_DEFAULT_TTL_MS, (
            "ttl 미지정 시 서버 기본 보존 기간이 부여되고 응답에 명시돼야 한다"
        )

        got = await client.session.send_request(
            GetTaskRequest(params=GetTaskRequestParams(task_id=descriptor.task_id)),
            GetTaskResult,
        )
        assert got.ttl is not None

        listed = await client.session.send_request(ListTasksRequest(), ListTasksResult)
        assert descriptor.task_id in {task.task_id for task in listed.tasks}

        await _wait_terminal(client, descriptor.task_id)
        payload = await client.session.send_request(
            GetTaskPayloadRequest(
                params=GetTaskPayloadRequestParams(task_id=descriptor.task_id)
            ),
            CallToolResult,
        )
        assert payload.structured_content["checked"] >= 1


async def test_cancel_response_of_default_ttl_task_parses(fixture_server, task_server):
    """D-116: 취소 응답(CancelTaskResult)도 ttl 미지정 task에서 스키마 유효해야 한다."""
    base_url, state = fixture_server
    server, _service = task_server
    async with Client(server) as client:
        await _seed(client, base_url, 2)
        state.response_delay = 0.3  # 취소 창을 만든다
        try:
            raw = await _create_task(client, TaskMetadata())
            cancelled = await client.session.send_request(
                CancelTaskRequest(params=CancelTaskRequestParams(task_id=raw["taskId"])),
                CancelTaskResult,
            )
            assert cancelled.ttl is not None
        finally:
            state.response_delay = 0.0
        await _wait_terminal(client, raw["taskId"])


@pytest.mark.parametrize("bad_ttl", [0, -5])
async def test_zero_and_negative_ttl_rejected(fixture_server, task_server, bad_ttl):
    """D-123: 결과 회수가 원천 불가능한 ttl(0·음수)은 조용히 받지 않고 거부한다.

    기존 동작: 음수 ttl을 그대로 반향하고 첫 prune에서 항목을 즉시 삭제 —
    클라이언트는 '만들었다'는 응답을 받고도 결과를 영원히 회수할 수 없었다.
    """
    base_url, _state = fixture_server
    server, _service = task_server
    async with Client(server) as client:
        await _seed(client, base_url, 1)
        with pytest.raises(MCPError) as excinfo:
            await _create_task(client, TaskMetadata(ttl=bad_ttl))
        assert excinfo.value.error.code == -32602


async def test_huge_ttl_clamped_and_reported(fixture_server, task_server):
    """D-123: 터무니없는 ttl(10^30)은 상한으로 클램프하고, Task.ttl에 실제
    보존 기간을 보고한다(프로토콜: ttl은 '실제' 보존 기간이다)."""
    base_url, _state = fixture_server
    server, _service = task_server
    async with Client(server) as client:
        await _seed(client, base_url, 1)
        raw = await _create_task(client, TaskMetadata(ttl=10**30))
        descriptor = Task.model_validate(raw)
        assert descriptor.ttl == _TASK_MAX_TTL_MS


async def test_result_survives_run_longer_than_ttl(fixture_server, task_server):
    """실행 시간이 요청 ttl을 넘겨도 종결 직후의 결과는 회수 가능해야 한다.

    보존 기간을 '생성 시점부터'로 세는 프로토콜 정의(D-123)를 순진하게
    적용하면 오래 걸린 task의 결과가 종결과 동시에 소멸한다. 서버는 종결
    시점에 실제 보존 기간(경과 + 요청 ttl)을 Task.ttl로 보고해 이를 막는다.
    """
    base_url, state = fixture_server
    server, _service = task_server
    async with Client(server) as client:
        await _seed(client, base_url, 3)
        state.response_delay = 0.5  # 3개 문서 × 0.5s ≥ 1.5s > ttl 1s
        try:
            raw = await _create_task(client, TaskMetadata(ttl=1000))
            terminal = await _wait_terminal(client, raw["taskId"])
        finally:
            state.response_delay = 0.0

        server.anchor_tasks.prune()
        payload = await client.session.send_request(
            GetTaskPayloadRequest(params=GetTaskPayloadRequestParams(task_id=raw["taskId"])),
            CallToolResult,
        )
        assert payload.structured_content["checked"] == 3
        assert terminal.ttl is not None and terminal.ttl > 1000, (
            "종결 시점의 Task.ttl은 실제 보존 기간(경과+요청)을 보고해야 한다"
        )


def test_prune_measures_age_from_creation(tmp_path):
    """D-123 부수: prune은 last_updated_at이 아니라 created_at 기준이다
    (프로토콜: 'retention duration from creation'). 시각은 전부 합성 —
    타이밍 비의존."""
    from anchor.models import utcnow_iso
    from anchor.server import _TaskEntry

    config = Config(db_path=tmp_path / "prune.db")
    server, service = build_server(db_path=config.db_path, config=config)
    try:
        extension = server.anchor_tasks
        old = "2026-08-17T00:00:00+00:00"  # 지금으로부터 충분히 과거
        recent = utcnow_iso()

        expired = _TaskEntry(
            task=Task(
                task_id="expired", status="completed", created_at=old,
                last_updated_at=recent, ttl=5000,
            )
        )
        alive = _TaskEntry(
            task=Task(
                task_id="alive", status="completed", created_at=recent,
                last_updated_at=recent, ttl=_TASK_DEFAULT_TTL_MS,
            )
        )
        working = _TaskEntry(
            task=Task(
                task_id="working", status="working", created_at=old,
                last_updated_at=old, ttl=5000,
            )
        )
        with extension._entries_lock:
            for entry in (expired, alive, working):
                extension._entries[entry.task.task_id] = entry

        extension.prune()

        with extension._entries_lock:
            remaining = set(extension._entries)
        assert "expired" not in remaining, "created_at 기준으로 만료됐어야 한다"
        assert "alive" in remaining
        assert "working" in remaining, "진행 중인 task는 prune 대상이 아니다"
    finally:
        # 합성 항목에는 실제 워커가 없다 — shutdown이 존재하지 않는 워커의
        # 종료를 기다리지 않도록 치운다.
        with extension._entries_lock:
            extension._entries.clear()
        server.anchor_tasks.shutdown()
        service.close()
