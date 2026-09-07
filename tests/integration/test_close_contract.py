# SPDX-License-Identifier: Apache-2.0
"""close() 계약 (D-129, SPEC §8).

라이브러리 직접 사용 경로에는 방어가 없었다: `Anchor.__exit__`가 진행 중인
호출보다 먼저 끝나면 그 호출이 `Bad file descriptor`로 죽고, 이후 모든 호출이
생 `sqlite3.ProgrammingError`로 샜다. 서버 경로만 D-034가 막고 있었다.
"""

from __future__ import annotations

import threading
import time

import pytest

from anchor.config import Config
from anchor.errors import StorageError
from anchor.service import Anchor

QUOTE = "링크는 살아 있지만 내용이 바뀌는 인용 표류가 가장 위험하다."


def make_config(tmp_path, **kwargs):
    return Config(
        db_path=tmp_path / "close.db",
        rate_limit_rps=1000.0,
        retry_backoff_base=0.01,
        **kwargs,
    )


def test_calls_after_close_raise_storage_error(fixture_server, tmp_path):
    """닫힌 저장소를 쓰면 도메인 예외다 — 생 sqlite3 예외가 아니다."""
    base_url, _state = fixture_server
    anchor = Anchor(db_path=tmp_path / "close.db", config=make_config(tmp_path))
    fetched = anchor.fetch(f"{base_url}/article")
    document_id = fetched.document_id
    version_id = fetched.version_id
    anchor.close()

    calls = {
        "fetch": lambda: anchor.fetch(f"{base_url}/article"),
        "list_documents": anchor.list_documents,
        "cache_stats": anchor.cache_stats,
        "cite": lambda: anchor.cite(document_id, QUOTE),
        "verify": lambda: anchor.verify(),
        "get_version": lambda: anchor.get_version(version_id),
        "get_timemap": lambda: anchor.get_timemap(document_id),
        "collect_garbage": anchor.collect_garbage,
    }
    for name, call in calls.items():
        with pytest.raises(StorageError) as caught:
            call()
        assert "closed" in str(caught.value), f"{name}: 메시지가 사유를 말하지 않는다"


def test_double_close_is_idempotent(fixture_server, tmp_path):
    base_url, _state = fixture_server
    anchor = Anchor(db_path=tmp_path / "close.db", config=make_config(tmp_path))
    anchor.fetch(f"{base_url}/article")
    anchor.close()
    anchor.close()  # 두 번째는 무해해야 한다
    with Anchor(db_path=tmp_path / "close.db", config=make_config(tmp_path)) as reopened:
        assert len(reopened.list_documents().documents) == 1  # 닫기가 데이터를 해치지 않았다


def test_close_waits_for_in_flight_call(fixture_server, tmp_path):
    """close()는 진행 중인 공개 API 호출이 끝난 뒤에 닫는다.

    기제로 잰다: 페치를 서버 핸들러에 붙잡아 둔 채 다른 스레드에서 close를
    부르고, close가 실제로 **진입**한 것을 확인한 뒤에야 게이트를 연다.
    ① 붙잡혀 있던 페치가 성공으로 끝나고 ② 완료 순서가 페치 → close다.
    """
    base_url, state = fixture_server
    path = "/article?doc=inflight"
    state.arrived[path] = threading.Event()
    gate = threading.Event()
    state.gates[path] = gate

    anchor = Anchor(db_path=tmp_path / "close.db", config=make_config(tmp_path))
    order: list[str] = []
    failures: list[BaseException] = []

    def fetcher() -> None:
        try:
            anchor.fetch(f"{base_url}{path}", max_age=0)
            order.append("fetch")
        except BaseException as error:  # noqa: BLE001
            failures.append(error)
            order.append("fetch-failed")

    def closer() -> None:
        anchor.close()
        order.append("close")

    worker = threading.Thread(target=fetcher)
    worker.start()
    assert state.arrived[path].wait(30), "페치가 서버에 도착하지 않았다"

    closing_thread = threading.Thread(target=closer)
    closing_thread.start()

    # close가 진입했음을 관측한 뒤에야 게이트를 연다. 두 판별식은 각각
    # 고친 뒤(닫는 중 표시)와 고치기 전(이미 다 닫아 버림)의 진입 관측점이다 —
    # 되돌림 검증에서도 이 시험이 판별력을 잃지 않게 둘 다 본다.
    def close_entered() -> bool:
        gate_state = getattr(anchor, "_calls", None)
        return bool(getattr(gate_state, "closing", False)) or anchor._client.is_closed

    deadline = time.monotonic() + 30.0
    while not close_entered() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert close_entered(), "close가 진입하지 않았다"

    # 닫는 중에 들어온 **새** 호출은 거절된다 (진행 중 호출은 그대로 끝난다).
    with pytest.raises(StorageError):
        anchor.list_documents()

    gate.set()
    worker.join(timeout=60)
    closing_thread.join(timeout=60)
    assert not worker.is_alive() and not closing_thread.is_alive()

    assert not failures, f"진행 중이던 페치가 죽었다: {failures[:1]}"
    assert order == ["fetch", "close"], f"close가 기다리지 않았다: {order}"


def test_concurrent_close_returns_only_after_the_store_is_closed(fixture_server, tmp_path):
    """두 스레드가 동시에 닫아도 "close가 반환했으면 닫혔다"가 참이어야 한다.

    먼저 온 close가 진행 중 호출을 기다리는 동안 뒤에 온 close가 곧바로
    반환하면, 그 호출자는 아직 열려 있는 저장소를 닫힌 것으로 알고 다음
    일을 한다. 단언은 시간이 아니라 **반환 시점에 관측한 커넥션 상태**다.
    """
    base_url, state = fixture_server
    path = "/article?doc=twoclosers"
    state.arrived[path] = threading.Event()
    gate = threading.Event()
    state.gates[path] = gate

    anchor = Anchor(db_path=tmp_path / "close.db", config=make_config(tmp_path))
    observed: list[bool] = []

    def fetcher() -> None:
        anchor.fetch(f"{base_url}{path}", max_age=0)

    def first_closer() -> None:
        anchor.close()

    def second_closer() -> None:
        anchor.close()
        observed.append(anchor._repository._connection._closed)

    worker = threading.Thread(target=fetcher)
    worker.start()
    assert state.arrived[path].wait(30), "페치가 서버에 도착하지 않았다"

    first = threading.Thread(target=first_closer)
    first.start()
    deadline = time.monotonic() + 30.0
    while not anchor._calls.closing and time.monotonic() < deadline:
        time.sleep(0.01)
    assert anchor._calls.closing, "첫 close가 진입하지 않았다"

    second = threading.Thread(target=second_closer)
    second.start()
    # 두 번째 close가 게이트보다 먼저 돌아오면 그 자체가 결함이다 — 그때
    # 관측한 값이 아래 단언에서 사실을 말한다.
    second.join(timeout=1.0)

    gate.set()
    for thread in (worker, first, second):
        thread.join(timeout=60)
        assert not thread.is_alive(), "스레드가 끝나지 않았다"

    assert observed == [True], (
        f"두 번째 close가 저장소가 닫히기 전에 반환했다 (관측: {observed})"
    )
