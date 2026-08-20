# SPDX-License-Identifier: Apache-2.0
"""`tasks/list` 페이지네이션 축 (D-054).

축: **목록의 크기** — 페이지 경계를 넘는 수. 항목이 한 줌뿐인 픽스처에는 이
축이 없고, 전량 반환과 페이지네이션이 똑같이 보인다. 장기 실행 서버에서
응답이 무한정 커지는 것이 이 결함의 실제 형태이므로, 경계를 넘는 수를 만들어
①커서를 읽는가 ②`nextCursor`를 주는가 ③커서를 따라가면 전 항목이 정확히
한 번씩 나오는가를 본다.

항목은 합성한다 — 워커 51개를 띄우면 시험이 스케줄러 타이밍에 얹힌다.
합성 항목에도 `_wire_task` 형식(D-116: ttl 키 생략 금지)은 그대로 요구한다.
"""

from __future__ import annotations

import pytest
from mcp.client.client import Client
from mcp.shared.exceptions import MCPError
from mcp_types import ListTasksRequest, ListTasksResult, PaginatedRequestParams, Task

from anchor.config import Config
from anchor.models import utcnow_iso, uuid7
from anchor.server import _TASK_DEFAULT_TTL_MS, _TASK_PAGE_SIZE, _TaskEntry, build_server

pytestmark = pytest.mark.anyio


@pytest.fixture
def task_server(tmp_path):
    config = Config(db_path=tmp_path / "tasks.db")
    server, service = build_server(db_path=config.db_path, config=config)
    yield server, service
    with server.anchor_tasks._entries_lock:
        server.anchor_tasks._entries.clear()  # 합성 항목에는 워커가 없다
    server.anchor_tasks.shutdown()
    service.close()


def _seed(extension, count: int) -> list[str]:
    """종결 상태의 합성 task를 count개 등록하고 id를 반환한다."""
    now = utcnow_iso()
    ids: list[str] = []
    with extension._entries_lock:
        for _ in range(count):
            task_id = uuid7()
            ids.append(task_id)
            extension._entries[task_id] = _TaskEntry(
                task=Task(
                    task_id=task_id,
                    status="completed",
                    created_at=now,
                    last_updated_at=now,
                    ttl=_TASK_DEFAULT_TTL_MS,
                )
            )
    return ids


async def _list(client, cursor: str | None = None) -> ListTasksResult:
    params = PaginatedRequestParams(cursor=cursor) if cursor is not None else None
    return await client.session.send_request(
        ListTasksRequest(params=params), ListTasksResult
    )


async def test_large_list_is_paged_and_cursor_enumerates_everything(task_server):
    server, _service = task_server
    total = 2 * _TASK_PAGE_SIZE + 3
    expected = sorted(_seed(server.anchor_tasks, total))

    async with Client(server) as client:
        seen: list[str] = []
        pages = 0
        cursor: str | None = None
        while True:
            result = await _list(client, cursor)
            pages += 1
            assert len(result.tasks) <= _TASK_PAGE_SIZE, (
                f"한 페이지에 {len(result.tasks)}건 — 응답이 목록 크기만큼 커진다"
            )
            seen.extend(task.task_id for task in result.tasks)
            cursor = result.next_cursor
            if cursor is None:
                break
            assert pages < 10, "커서가 진행하지 않는다"

        assert pages == 3
        assert seen == expected, "커서를 따라간 열거가 전 항목과 일치하지 않는다"
        assert len(set(seen)) == total, "중복 또는 누락이 있다"


async def test_small_list_has_no_next_cursor(task_server):
    server, _service = task_server
    _seed(server.anchor_tasks, 3)
    async with Client(server) as client:
        result = await _list(client)
        assert len(result.tasks) == 3
        assert result.next_cursor is None


async def test_paged_items_keep_the_ttl_key(task_server):
    """페이지네이션이 들어가도 D-116의 와이어 형식은 그대로여야 한다."""
    server, _service = task_server
    _seed(server.anchor_tasks, _TASK_PAGE_SIZE + 1)
    async with Client(server) as client:
        result = await _list(client)
        assert all(task.ttl is not None for task in result.tasks)


async def test_unknown_cursor_is_rejected(task_server):
    """MCP: 유효하지 않은 커서는 -32602. 조용히 처음부터 주면 클라이언트는
    같은 항목을 두 번 읽고도 모른다."""
    server, _service = task_server
    _seed(server.anchor_tasks, 2)
    async with Client(server) as client:
        with pytest.raises(MCPError) as excinfo:
            await _list(client, "not-a-cursor")
        assert excinfo.value.error.code == -32602


async def test_deleted_entries_do_not_shift_the_page(task_server):
    """커서는 위치가 아니라 항목을 가리켜야 한다 — 사이에서 prune이 일어나도
    남은 항목을 건너뛰지 않는다."""
    server, _service = task_server
    ids = sorted(_seed(server.anchor_tasks, _TASK_PAGE_SIZE + 5))
    extension = server.anchor_tasks
    async with Client(server) as client:
        first = await _list(client)
        assert first.next_cursor is not None
        # 첫 페이지의 항목 몇 개가 사라진다 (ttl 만료 prune과 같은 상황).
        with extension._entries_lock:
            for task_id in ids[:3]:
                del extension._entries[task_id]
        second = await _list(client, first.next_cursor)
        assert [task.task_id for task in second.tasks] == ids[_TASK_PAGE_SIZE:]
