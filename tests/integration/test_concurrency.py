# SPDX-License-Identifier: Apache-2.0
"""동시성 회귀 (D-038 + D-020/D-023 사후 확인).

전역 락을 URL 단위 락으로 좁혔다. 두 가지를 동시에 만족해야 한다:
① 백그라운드 verify가 도는 동안 다른 도구가 멈추지 않는다
② 같은 URL을 동시에 페치해도 문서가 중복 생성되지 않는다
"""

from __future__ import annotations

import threading
import time

import pytest
from mcp.client.client import Client
from mcp_types import (
    CallToolRequest,
    CallToolRequestParams,
    CallToolResult,
    TaskMetadata,
)

from anchor.config import Config
from anchor.fetcher.urlnorm import normalize_url
from anchor.server import build_server
from anchor.service import Anchor

pytestmark = pytest.mark.anyio

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


def make_config(tmp_path, **kwargs):
    return Config(
        db_path=tmp_path / "conc.db", rate_limit_rps=1000.0, retry_backoff_base=0.01, **kwargs
    )


# -- 안전성: 같은 URL 동시 페치 ---------------------------------------------


def test_same_url_concurrent_fetch_creates_one_document(fixture_server, tmp_path):
    base_url, state = fixture_server
    errors: list[BaseException] = []

    with Anchor(db_path=tmp_path / "same.db", config=make_config(tmp_path)) as anchor:
        def worker():
            try:
                anchor.fetch(f"{base_url}/article", max_age=0)
            except BaseException as error:  # noqa: BLE001 — 무엇이든 기록한다
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        documents = anchor.list_documents().documents

    assert not errors, f"동시 페치에서 오류: {errors[:3]}"
    assert len(documents) == 1, f"문서가 중복 생성됐다: {len(documents)}건"


def test_different_urls_fetch_in_parallel(fixture_server, tmp_path):
    """서로 무관한 문서는 나란히 진행되어야 한다."""
    base_url, state = fixture_server
    state.response_delay = 0.3
    results: list[float] = []

    with Anchor(db_path=tmp_path / "par.db", config=make_config(tmp_path)) as anchor:
        def worker(index: int):
            anchor.fetch(f"{base_url}/article?doc={index}", max_age=0)

        started = time.monotonic()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        elapsed = time.monotonic() - started

    state.response_delay = 0.0
    # 직렬이면 4 × 0.3초 = 1.2초 이상. 병렬이면 그보다 확연히 짧다.
    assert elapsed < 1.0, f"서로 다른 URL이 직렬화됐다 ({elapsed:.2f}s)"


# -- D-038: 백그라운드 task가 다른 도구를 막지 않는다 -------------------------


async def test_read_only_tools_respond_during_background_verify(fixture_server, tmp_path):
    base_url, state = fixture_server
    server, service = build_server(
        db_path=tmp_path / "task.db", config=make_config(tmp_path)
    )
    try:
        async with Client(server) as client:
            for index in range(5):
                fetched = await client.call_tool(
                    "fetch_document", {"url": f"{base_url}/article?doc={index}"}
                )
                await client.call_tool(
                    "cite",
                    {"document_id": fetched.structured_content["document_id"], "quote": QUOTE},
                )

            state.response_delay = 0.4  # verify가 최소 2초는 돌게 한다
            await client.session.send_request(
                CallToolRequest(
                    params=CallToolRequestParams(
                        name="verify_citations", arguments={}, task=TaskMetadata()
                    )
                ),
                CallToolResult,
            )

            import anyio

            await anyio.sleep(0.3)  # 백그라운드 작업이 확실히 진행 중일 때
            started = time.monotonic()
            stats = await client.call_tool("cache_stats", {})
            elapsed = time.monotonic() - started

            assert stats.structured_content["documents"] == 5
            assert elapsed < 1.0, f"백그라운드 verify가 cache_stats를 {elapsed:.2f}s 막았다"
    finally:
        state.response_delay = 0.0
        server.anchor_tasks.shutdown()
        service.close()


# -- D-128: 서로 다른 URL의 거짓 공유 ---------------------------------------


def _legacy_stripe(url: str) -> int:
    """구 구현의 스트라이프 함수(`hash(url) % 64`).

    거짓 공유는 "어느 두 URL이 같은 락에 배정되는가"로만 관측된다. 임의의
    URL 쌍을 쓰면 64분의 63의 확률로 통과하는 시험이 되므로, 구 구현이
    **반드시 붙였을** 쌍을 골라 온다 — 스트라이프가 되살아나면 이 쌍이
    다시 붙고 시험이 즉시 빨강이 된다. (`hash`는 프로세스마다 시드가
    달라 쌍을 미리 적어 둘 수 없다.)
    """
    return hash(url) % 64


def colliding_paths(base_url: str) -> tuple[str, str]:
    seen: dict[int, str] = {}
    for index in range(4096):
        path = f"/article?doc={index}"
        stripe = _legacy_stripe(normalize_url(base_url + path))
        if stripe in seen:
            return seen[stripe], path
        seen[stripe] = path
    raise AssertionError("스트라이프 충돌 쌍을 찾지 못했다 — 픽스처가 무력하다")


def test_unrelated_urls_do_not_share_a_fetch_lock(fixture_server, tmp_path):
    """서로 무관한 두 문서는 네트워크 왕복을 겹칠 수 있어야 한다 (SPEC §10).

    구 구현에서는 두 URL이 같은 스트라이프에 걸리면 뒤의 요청이 앞의
    **네트워크 왕복 전체**(기본 30초 타임아웃 포함) 동안 락에 막혀 서버에
    도착조차 못 했다. 바리어는 그것을 사실로 만든다 — 상대가 도착하지
    못하면 만남이 깨진다.
    """
    base_url, state = fixture_server
    first, second = colliding_paths(base_url)
    state.arrival_barrier = threading.Barrier(2, timeout=15.0)
    errors: list[BaseException] = []

    with Anchor(db_path=tmp_path / "share.db", config=make_config(tmp_path)) as anchor:
        def worker(path: str) -> None:
            try:
                anchor.fetch(base_url + path, max_age=0)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        threads = [
            threading.Thread(target=worker, args=(path,)) for path in (first, second)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not any(thread.is_alive() for thread in threads), "페치가 끝나지 않았다"

    assert not errors, f"페치 오류: {errors[:2]}"
    assert not state.barrier_broken, (
        f"무관한 두 URL({first}, {second})이 같은 락에 막혀 서버에서 만나지 못했다"
    )
    assert state.max_inflight == 2, f"동시 진행이 {state.max_inflight}건에 그쳤다"


def test_same_url_stays_serialized_and_lock_registry_drains(fixture_server, tmp_path):
    """같은 URL의 직렬화는 의도다 — 거짓 공유를 없애며 함께 잃으면 안 된다.

    ① 앞선 페치가 왕복 중인 동안 뒤의 호출은 **그 URL의 락에서** 기다린다
    ② 그래서 같은 URL의 요청은 서버에서 겹치지 않는다
    ③ 아무도 쓰지 않게 된 락은 등록부에서 사라진다(무한 증가 금지)
    """
    base_url, state = fixture_server
    path = "/article?doc=serial"
    url = base_url + path
    norm = normalize_url(url)
    state.arrived[path] = threading.Event()
    gate = threading.Event()
    state.gates[path] = gate
    errors: list[BaseException] = []

    with Anchor(db_path=tmp_path / "serial.db", config=make_config(tmp_path)) as anchor:
        def worker() -> None:
            try:
                anchor.fetch(url, max_age=0)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        first = threading.Thread(target=worker)
        first.start()
        assert state.arrived[path].wait(30), "첫 페치가 서버에 도착하지 않았다"

        second = threading.Thread(target=worker)
        second.start()
        # 두 번째 호출이 **같은 URL의 락에서** 대기 중임을 기제로 확인한다.
        # 타이밍이 아니라 등록부의 참조 수를 본다: 보유 1 + 대기 1 = 2.
        deadline = time.monotonic() + 30.0
        while anchor._url_locks.refcount(norm) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert anchor._url_locks.refcount(norm) == 2, (
            "두 번째 호출이 같은 URL의 락에서 기다리지 않는다 — 직렬화가 사라졌다"
        )

        gate.set()
        for thread in (first, second):
            thread.join(timeout=60)
            assert not thread.is_alive(), "페치가 끝나지 않았다"

        assert not errors, f"페치 오류: {errors[:2]}"
        assert state.max_inflight == 1, (
            f"같은 URL의 요청이 서버에서 겹쳤다 ({state.max_inflight}건 동시)"
        )
        assert len(anchor.list_documents().documents) == 1
        assert anchor._url_locks.refcount(norm) == 0
        assert anchor._url_locks.size() == 0, "쓰지 않는 URL 락이 등록부에 남았다"
