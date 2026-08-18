# SPDX-License-Identifier: Apache-2.0
"""종료·취소의 협조적 중단 (D-117 / D-118 / D-119).

축: **실제 규모의 종료** — 한 문서 안의 많은 앵커. 기존 회귀 픽스처는
매 루프 stop()을 확인하는 인공 워커(D-034 테스트)라 "앵커 사이에는 확인이
없다"는 실제 워커의 실패 모드를 재현하지 못했고, 그 틈에서 D-117(부분 결과
통째 소실)과 D-119(취소 반응이 앵커 수에 비례)가 살아남았다.

여기서는 실제 verify 워커를 쓴다: 문서 하나에 앵커 여럿을 심고 본문을
갈아치워 모든 앵커가 근사 검색(앵커당 예산 소진)을 타게 만든다 — 앵커당
비용이 예산으로 위에서 묶이므로 단언은 타이밍이 아니라 기제(부분 결과
보존·종결 상태·확인 횟수)에 건다.
"""

from __future__ import annotations

import sqlite3
import time

import anyio
import pytest
from mcp.client.client import Client
from mcp_types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    CancelTaskRequest,
    CancelTaskRequestParams,
    CancelTaskResult,
    TaskMetadata,
)
from typer.testing import CliRunner

from anchor.config import Config
from anchor.server import build_server

pytestmark = pytest.mark.anyio

ANCHOR_COUNT = 40
PER_ANCHOR_BUDGET_MS = 300


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _sentences(count: int) -> list[str]:
    # 길이 축을 연다: 패딩을 달리해 55~180자 사이로 흩는다. 전부 같은 길이의
    # 한글 문장이면 길이 축이 닫힌다 (D-178이 살아남은 이유).
    out = []
    for i in range(count):
        pad = " 그리고 이어지는 서술은 문장마다 길이를 달리한다" * (i % 5)
        out.append(
            f"근거 {i}번은 서로 다른 사실을 담고 숫자 {i * 37}과 영어 조각 fragment-{i}alpha 를 함께 품는다{pad}."
        )
    return out


def _filler(paragraphs: int) -> str:
    return "\n".join(
        f"<p>채움 문단 {j}: 앵커의 어떤 문장과도 겹치지 않는 서술이 길게 이어진다. "
        f"숫자 {j * 13}이 등장하고 뒤이어 또 다른 설명이 계속된다.</p>"
        for j in range(paragraphs)
    )


def _page(body_html: str) -> str:
    return (
        "<html><head><title>규모 축 문서</title></head>"
        f"<body><article>{body_html}</article></body></html>"
    )


def _count_verifications(db_path) -> int:
    # 워커와 독립된 커넥션으로 관측한다 — 진행 상태의 유일한 외부 창.
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
        return connection.execute("SELECT COUNT(*) FROM verifications").fetchone()[0]


async def _seed_scale_document(client, base_url, state) -> str:
    """문서 하나에 ANCHOR_COUNT개의 앵커를 심고, 본문을 갈아치워 모든 앵커가
    근사 검색을 타게 만든다. 반환: document_id."""
    path = "/scale-doc"
    sentences = _sentences(ANCHOR_COUNT)
    state.bodies[path] = _page(
        "\n".join(f"<p>{s}</p>" for s in sentences) + _filler(60)
    )
    state.etags[path] = '"scale-v1"'

    fetched = await client.call_tool("fetch_document", {"url": f"{base_url}{path}"})
    document_id = fetched.structured_content["document_id"]
    for sentence in sentences:
        cited = await client.call_tool(
            "cite", {"document_id": document_id, "quote": sentence}
        )
        assert not cited.is_error, cited.content

    # 본문 교체: 앵커 문장이 전부 사라진 큰 본문 — 앵커마다 검색이 예산을
    # 소진한다. 검증자도 바꿔 304 지름길(원본 그대로 → 즉시 INTACT)을 막는다.
    state.bodies[path] = _page(_filler(900))
    state.etags[path] = '"scale-v2"'
    return document_id


async def _start_scale_task(client, document_id: str) -> str:
    created = await client.session.send_request(
        CallToolRequest(
            params=CallToolRequestParams(
                name="verify_citations",
                arguments={
                    "document_ids": [document_id],
                    "time_budget_ms": PER_ANCHOR_BUDGET_MS,
                },
                task=TaskMetadata(),
            )
        ),
        CallToolResult,
    )
    return created.structured_content["task"]["taskId"]


async def _wait_for_verifications(db_path, minimum: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _count_verifications(db_path) >= minimum:
            return
        await anyio.sleep(0.05)
    raise AssertionError(f"검증 기록이 {minimum}건에 도달하지 않았다")


async def test_shutdown_preserves_partial_results_at_realistic_scale(fixture_server, tmp_path):
    """D-117: 앵커 루프 한가운데서 종료해도 — ① shutdown 반환 후 워커가
    살아 있으면 안 되고 ② task는 실패가 아니라 취소로 종결되고 ③ 그때까지의
    부분 결과가 회수 가능해야 한다.

    기존 동작: shutdown이 유예를 다 쓰고 워커 생존 채로 침묵 반환 →
    service.close() → 워커가 "Cannot operate on a closed database"로 죽고
    task=failed, 부분 결과 통째 소실 (D-036 계약 위반).
    """
    base_url, state = fixture_server
    db_path = tmp_path / "scale.db"
    config = Config(db_path=db_path, rate_limit_rps=1000.0, retry_backoff_base=0.01)
    server, service = build_server(db_path=db_path, config=config)
    extension = server.anchor_tasks
    try:
        async with Client(server) as client:
            document_id = await _seed_scale_document(client, base_url, state)
            task_id = await _start_scale_task(client, document_id)
            await _wait_for_verifications(db_path, minimum=2)

        extension.shutdown(0.5)

        entry = extension._entries[task_id]
        assert entry.worker is not None and not entry.worker.is_alive(), (
            "shutdown 반환 후 워커가 살아 있다 — 이 상태에서 저장소를 닫으면 "
            "부분 결과가 사라진다"
        )
        service.close()  # 계약: 워커 정리가 끝난 뒤에만 안전하다

        task = entry.task
        assert task.status == "cancelled", (
            f"종료로 중단된 task는 cancelled여야 한다 (실제: {task.status}, "
            f"message={task.status_message})"
        )
        assert entry.result is not None and not entry.result.is_error
        payload = entry.result.structured_content
        assert payload["stopped_early"] is True
        assert 2 <= payload["checked"] < ANCHOR_COUNT, (
            f"부분 결과가 보존돼야 한다 (checked={payload['checked']})"
        )
    finally:
        extension.shutdown()
        service.close()


async def test_cancel_terminal_within_one_roundtrip_on_many_anchor_document(
    fixture_server, tmp_path
):
    """D-119: 앵커 여럿인 문서 한가운데서의 취소가 — ① 취소 응답 자체가
    종결 상태(cancelled)여야 하고("취소 요청됨" 후 무한 폴링 금지) ② 남은
    앵커를 실제로 건너뛰어야 한다.

    기존 동작: should_stop을 문서 사이에서만 확인해 앵커 100개 문서에서
    신호 후 13.8초를 더 돌았고, tasks/cancel은 5초 join이 실패해 비종결
    상태(working)를 반환했다.
    """
    base_url, state = fixture_server
    db_path = tmp_path / "cancel-scale.db"
    config = Config(db_path=db_path, rate_limit_rps=1000.0, retry_backoff_base=0.01)
    server, service = build_server(db_path=db_path, config=config)
    try:
        async with Client(server) as client:
            document_id = await _seed_scale_document(client, base_url, state)
            task_id = await _start_scale_task(client, document_id)
            await _wait_for_verifications(db_path, minimum=2)

            cancelled = await client.session.send_request(
                CancelTaskRequest(params=CancelTaskRequestParams(task_id=task_id)),
                CancelTaskResult,
            )
            assert cancelled.status == "cancelled", (
                f"취소 응답이 종결 상태가 아니다: {cancelled.status} — 클라이언트가 "
                "계속 폴링해야 한다"
            )

            entry = server.anchor_tasks._entries[task_id]
            payload = entry.result.structured_content
            assert payload["stopped_early"] is True
            assert payload["checked"] < ANCHOR_COUNT, (
                f"취소 후에도 남은 앵커를 계속 돌았다 (checked={payload['checked']})"
            )
    finally:
        server.anchor_tasks.shutdown()
        service.close()


def test_both_entry_points_stop_workers_before_closing_store(monkeypatch, tmp_path):
    """D-118: 두 진입점(`anchor-mcp`, `anchor serve`) 모두 워커 정리 →
    저장소 해제 순서를 지켜야 한다. 기존에는 `anchor-mcp`만 지켰다 —
    같은 계약을 두 곳이 따로 구현했기 때문이다."""
    calls: list[str] = []

    class FakeTasks:
        def shutdown(self, *args, **kwargs):
            calls.append("shutdown")

    class FakeServer:
        anchor_tasks = FakeTasks()

        def run(self, transport):
            calls.append("run")

    class FakeService:
        def close(self):
            calls.append("close")

    import anchor.server as server_module

    fake_config = Config(db_path=tmp_path / "entry.db")
    monkeypatch.setattr(
        server_module, "build_server",
        lambda db_path=None, config=None: (FakeServer(), FakeService()),
    )
    monkeypatch.setattr(server_module, "load_config", lambda: fake_config)

    monkeypatch.setattr("sys.argv", ["anchor-mcp"])
    server_module.main()
    assert calls == ["run", "shutdown", "close"], f"anchor-mcp 진입점 순서: {calls}"

    calls.clear()
    import anchor.config as config_module

    monkeypatch.setattr(config_module, "load_config", lambda: fake_config)
    from anchor.cli import app

    result = CliRunner().invoke(app, ["serve"])
    assert result.exit_code == 0, result.output
    assert calls == ["run", "shutdown", "close"], f"anchor serve 진입점 순서: {calls}"


def test_shutdown_does_not_hang_on_never_started_worker(monkeypatch, tmp_path):
    """조치 코드 재감사에서 나온 결함: '등록됐지만 스레드가 시작되지 않은'
    항목(Thread.start 실패의 고아)을 shutdown이 배정 직전의 찰나로 해석해
    영원히 기다렸다 — 서버가 영영 꺼지지 않는다. 유예 후 실패로 종결하고
    반환해야 한다."""
    import anchor.server as server_module
    from mcp_types import Task

    from anchor.server import _TaskEntry

    monkeypatch.setattr(server_module, "_TASK_START_PATIENCE_S", 0.2)
    config = Config(db_path=tmp_path / "orphan.db")
    server, service = build_server(db_path=config.db_path, config=config)
    try:
        entry = _TaskEntry(
            task=Task(
                task_id="orphan", status="working",
                created_at="2026-08-18T00:00:00+00:00",
                last_updated_at="2026-08-18T00:00:00+00:00", ttl=60000,
            )
        )
        with server.anchor_tasks._entries_lock:
            server.anchor_tasks._entries["orphan"] = entry

        started = time.monotonic()
        server.anchor_tasks.shutdown(0.1)
        assert time.monotonic() - started < 10, "shutdown이 고아 항목에 걸려 있었다"
        assert entry.task.status == "failed"
        assert entry.result is not None and entry.result.is_error
    finally:
        service.close()


def test_switch_interval_is_scoped_to_serving(tmp_path):
    """D-199: 스위치 간격 1ms는 서버가 실제로 서빙하는 동안만 건다.

    build_server(라이브러리 조립 함수)가 프로세스 전역 설정을 바꾸고
    되돌리지 않으면, 같은 프로세스의 호스트 앱·테스트 전체가 영향을 받는다
    — "라이브러리는 호스트 앱의 설정을 건드리지 않는다"는 선언과 모순.
    """
    import sys as _sys

    from anchor.server import serve_forever

    baseline = _sys.getswitchinterval()
    observed = {}
    try:
        _sys.setswitchinterval(0.005)
        config = Config(db_path=tmp_path / "si.db")
        server, service = build_server(db_path=config.db_path, config=config)
        try:
            assert _sys.getswitchinterval() == pytest.approx(0.005), (
                "build_server가 전역 스위치 간격을 바꿨다"
            )
        finally:
            server.anchor_tasks.shutdown()
            service.close()

        class FakeTasks:
            def shutdown(self, *args, **kwargs):
                pass

        class FakeServer:
            anchor_tasks = FakeTasks()

            def run(self, transport):
                observed["during"] = _sys.getswitchinterval()

        class FakeService:
            def close(self):
                pass

        serve_forever(FakeServer(), FakeService(), "stdio")
        assert observed["during"] == pytest.approx(0.001), (
            "서빙 동안에는 1ms여야 한다 (D-120)"
        )
        assert _sys.getswitchinterval() == pytest.approx(0.005), (
            "서빙이 끝났는데 스위치 간격을 되돌리지 않았다"
        )
    finally:
        _sys.setswitchinterval(baseline)
