# SPDX-License-Identifier: Apache-2.0
"""Task 수명주기 회귀 (D-034 / D-035 / D-036).

감사 실증: ① 백그라운드 task 중 서버 종료 시 SIGSEGV ② `tasks/cancel`이
상태만 바꾸고 작업은 계속 진행 ③ 취소된 task의 결과를 영원히 회수 불가.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from mcp.client.client import Client
from mcp_types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    GetTaskPayloadRequest,
    GetTaskPayloadRequestParams,
    GetTaskRequest,
    GetTaskRequestParams,
    GetTaskResult,
    TaskMetadata,
)

from anchor.config import Config
from anchor.server import build_server

pytestmark = pytest.mark.anyio

SRC = str(Path(__file__).resolve().parents[2] / "src")
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
        fetched = await client.call_tool("fetch_document", {"url": f"{base_url}/article?doc={index}"})
        await client.call_tool(
            "cite", {"document_id": fetched.structured_content["document_id"], "quote": QUOTE}
        )


async def _wait_terminal(client, task_id: str, timeout: float = 30.0) -> str:
    import anyio

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = await client.session.send_request(
            GetTaskRequest(params=GetTaskRequestParams(task_id=task_id)), GetTaskResult
        )
        if got.status in ("completed", "failed", "cancelled"):
            return got.status
        await anyio.sleep(0.05)
    raise AssertionError("task가 종료 상태에 도달하지 않았다")


async def _start_task(client, arguments: dict | None = None) -> str:
    created = await client.session.send_request(
        CallToolRequest(
            params=CallToolRequestParams(
                name="verify_citations", arguments=arguments or {}, task=TaskMetadata(ttl=60000)
            )
        ),
        CallToolResult,
    )
    return created.structured_content["task"]["taskId"]


async def test_cancel_actually_stops_remaining_work(fixture_server, task_server):
    """D-035: 취소 후에는 남은 문서에 네트워크 요청이 나가지 않아야 한다."""
    base_url, state = fixture_server
    server, _service = task_server
    async with Client(server) as client:
        await _seed(client, base_url, 6)
        state.response_delay = 0.35  # 문서마다 지연을 줘 취소 창을 만든다

        import anyio
        from mcp_types import CancelTaskRequest, CancelTaskRequestParams, CancelTaskResult

        task_id = await _start_task(client)
        await anyio.sleep(0.5)
        requests_at_cancel = len(state.requests)

        await client.session.send_request(
            CancelTaskRequest(params=CancelTaskRequestParams(task_id=task_id)), CancelTaskResult
        )
        status = await _wait_terminal(client, task_id)
        state.response_delay = 0.0

        after_cancel = len(state.requests) - requests_at_cancel
        assert status == "cancelled"
        # 진행 중이던 문서 하나는 끝날 수 있으나, 남은 문서 전부를 돌면 안 된다.
        assert after_cancel <= 2, f"취소 후에도 {after_cancel}건을 더 요청했다"


async def test_cancelled_task_result_is_recoverable(fixture_server, task_server):
    """D-036: 취소된 task도 부분 결과를 회수할 수 있어야 한다."""
    base_url, state = fixture_server
    server, _service = task_server
    async with Client(server) as client:
        await _seed(client, base_url, 3)
        state.response_delay = 0.3

        import anyio
        from mcp_types import CancelTaskRequest, CancelTaskRequestParams, CancelTaskResult

        task_id = await _start_task(client)
        await anyio.sleep(0.4)
        await client.session.send_request(
            CancelTaskRequest(params=CancelTaskRequestParams(task_id=task_id)), CancelTaskResult
        )
        await _wait_terminal(client, task_id)
        state.response_delay = 0.0

        payload = await client.session.send_request(
            GetTaskPayloadRequest(params=GetTaskPayloadRequestParams(task_id=task_id)),
            CallToolResult,
        )
        assert payload.structured_content["stopped_early"] is True
        assert payload.structured_content["checked"] < 3


def test_shutdown_during_background_task_does_not_crash(tmp_path):
    """D-034: 워커가 도는 중 종료해도 프로세스가 죽지 않아야 한다."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import threading, time
            from anchor.config import Config
            from anchor.server import build_server

            config = Config(db_path={str(tmp_path / "shutdown.db")!r})
            server, service = build_server(db_path=config.db_path, config=config)
            extension = server.anchor_tasks

            # 저장소를 계속 두드리는 워커를 8개 띄운 뒤 곧바로 종료 절차를 밟는다.
            def busy(stop):
                while not stop():
                    service._repository.log_fetch(
                        document_id="d", requested_at="2026-08-17T00:00:00Z",
                        outcome="cache_hit", http_status=200, bytes_down=0, elapsed_ms=1,
                    )

            from anchor.server import _TaskEntry
            from mcp_types import Task
            for index in range(8):
                entry = _TaskEntry(task=Task(
                    task_id=f"t{{index}}", status="working",
                    created_at="2026-08-17T00:00:00Z", last_updated_at="2026-08-17T00:00:00Z",
                    ttl=None,
                ))
                entry.worker = threading.Thread(target=busy, args=(entry.cancel.is_set,))
                extension._entries[entry.task.task_id] = entry
                entry.worker.start()

            time.sleep(0.3)
            extension.shutdown()      # 워커 정리가 먼저
            service.close()           # 그 다음 커넥션 해제
            print("clean shutdown")
        """)],
        capture_output=True, text=True,
        env={"PYTHONPATH": SRC, "PATH": "/usr/bin:/bin"}, timeout=180,
    )
    assert result.returncode == 0, (
        f"종료 중 프로세스가 죽었다 (rc={result.returncode}, segfault=-11): {result.stderr[-500:]}"
    )
    assert "clean shutdown" in result.stdout
