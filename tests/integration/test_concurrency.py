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
from anchor.server import build_server
from anchor.service import Anchor

pytestmark = pytest.mark.anyio

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


@pytest.fixture
def anyio_backend():
    return "asyncio"


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

        documents = anchor.list_documents()

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
                        name="verify_citations", arguments={}, task=TaskMetadata(ttl=60000)
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
