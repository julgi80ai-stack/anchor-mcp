# SPDX-License-Identifier: Apache-2.0
"""저장소가 계속 살아 있는가 (2차 감사 군집 9, D-080·081·082·124~127·159).

이 층위의 결함은 "판정이 틀린다"가 아니라 **저장소가 일을 멈춘다**는 형태다.
gc가 아무것도 회수하지 못해 디스크가 무한히 늘고, 한 번의 COMMIT 실패로 이후
모든 쓰기가 막히고, 한 호스트의 예절 대기가 무관한 호스트를 세운다.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

from anchor.errors import AnchorError
from anchor.fetcher.ratelimit import HostRateLimiter
from anchor.models import utcnow_iso
from anchor.store.repository import Repository


def _bulk(size: int) -> str:
    """압축이 잘 되지 않는 본문. 잘 압축되면 디스크 조건에 도달하지 못한다."""
    import secrets

    return secrets.token_hex(size // 2)


def _seed(
    repository: Repository,
    url: str,
    versions: int,
    body: str | None = None,
    source: str = "live",
) -> tuple[str, list[str]]:
    now = utcnow_iso()
    document = repository.create_document(
        url=url, original_url=url, title=None, now=now
    )
    ids = []
    for index in range(versions):
        version = repository.insert_version(
            document_id=document.id,
            text_hash=f"b3:{url}-{index}",
            raw_hash=f"b3:raw-{index}",
            pipeline_version="test/1",
            captured_at=f"2026-08-{index + 1:02d}T00:00:00Z",
            byte_size=100,
            normalized_text=body if body is not None else f"본문 {index} " * 200,
            http_status=200,
            source=source,
        )
        ids.append(version.id)
    return document.id, ids


# -- D-080: gc가 "현재 본문" 포인터를 보호하는가 -----------------------------


def test_gc_keeps_the_version_the_document_currently_serves(tmp_path):
    """되돌림·아카이브로 포인터가 옛 버전을 가리켜도 gc가 지우면 안 된다."""
    repository = Repository(tmp_path / "gc.db")
    try:
        document_id, version_ids = _seed(repository, "https://e.test/a", 5)
        repository.observe_version(document_id, version_ids[0], utcnow_iso())  # 가장 오래된 것

        deleted, _ = repository.collect_garbage_versions(keep=2)

        remaining = {version.id for version in repository.list_versions(document_id)}
        assert version_ids[0] in remaining, "현재 서빙 중인 본문이 지워졌다"
        assert deleted >= 1, "회수 가능한 고아 버전이 있는데 아무것도 지우지 않았다"
        assert repository.current_version(document_id) is not None
    finally:
        repository.close()


def test_one_reverted_document_does_not_block_collection_everywhere(tmp_path):
    """되돌림 문서 하나가 저장소 전체의 gc를 마비시키면 안 된다.

    삭제가 한 번의 `executemany`라, 보호 대상 하나가 FK에 걸리면 같이 지우려던
    정상 문서의 고아 버전까지 통째로 롤백된다 — 디스크가 무한히 는다.
    """
    repository = Repository(tmp_path / "gc-many.db")
    try:
        normal_id, _ = _seed(repository, "https://e.test/normal", 6)
        reverted_id, reverted_versions = _seed(repository, "https://e.test/reverted", 6)
        repository.observe_version(reverted_id, reverted_versions[0], utcnow_iso())

        deleted, _ = repository.collect_garbage_versions(keep=2)

        assert deleted > 0
        assert len(repository.list_versions(normal_id)) == 2, "정상 문서가 회수되지 않았다"
        assert reverted_versions[0] in {
            version.id for version in repository.list_versions(reverted_id)
        }
    finally:
        repository.close()


def test_gc_is_repeatable(tmp_path):
    """한 번 성공한 뒤에도 계속 돌 수 있어야 한다."""
    repository = Repository(tmp_path / "gc-twice.db")
    try:
        document_id, version_ids = _seed(repository, "https://e.test/a", 5)
        repository.observe_version(document_id, version_ids[1], utcnow_iso())
        repository.collect_garbage_versions(keep=2)
        repository.collect_garbage_versions(keep=2)  # 예외 없이
        assert repository.current_version(document_id) is not None
    finally:
        repository.close()


# -- D-081/D-082: gc와 쓰기가 겹칠 때 ----------------------------------------


def test_concurrent_writes_and_gc_do_not_raise(tmp_path):
    """대상 선정과 삭제가 한 트랜잭션이 아니면 그 틈에 참조가 생겨 둘 다 실패한다.

    `insert_version`이 커밋 뒤 락 밖에서 재조회하는 것도 같은 틈이다 — 그 사이
    gc가 지우면 맨 `AssertionError`가 호출자에게 올라간다.
    """
    repository = Repository(tmp_path / "race.db")
    errors: list[str] = []
    try:
        document_id, version_ids = _seed(repository, "https://e.test/a", 30)
        repository.observe_version(document_id, version_ids[-1], utcnow_iso())
        stop = threading.Event()

        def writer(tag: str) -> None:
            index = 0
            while not stop.is_set():
                try:
                    repository.insert_version(
                        document_id=document_id,
                        text_hash=f"b3:w-{tag}-{index}",
                        raw_hash="b3:raw",
                        pipeline_version="test/1",
                        captured_at=utcnow_iso(),
                        byte_size=10,
                        normalized_text="본문",
                        http_status=200,
                    )
                except Exception as error:  # noqa: BLE001
                    errors.append(f"writer {type(error).__name__}: {error}")
                index += 1

        def collector() -> None:
            while not stop.is_set():
                try:
                    repository.collect_garbage_versions(keep=2)
                except Exception as error:  # noqa: BLE001
                    errors.append(f"gc {type(error).__name__}: {error}")

        def referencer() -> None:
            """`cite`가 하는 것과 같은 일 — **현재 버전**에 앵커를 단다.

            gc는 현재 버전을 보호해야 하므로 이 경로는 절대 실패하면 안 된다.
            (가장 오래된 버전을 노리는 것은 설계가 약속하지 않는다 — gc가 지울
            권한이 있는 대상이다.)
            """
            index = 0
            while not stop.is_set():
                try:
                    target = repository.current_version(document_id)
                    if target is not None:
                        repository.insert_anchor(
                            document_id=document_id,
                            created_version=target.id,
                            exact=f"인용문 {index} 가 여기에 있다",
                            prefix="앞", suffix="뒤", position_hint=0,
                            exact_hash=f"b3:e-{index}", quality="ok", note=None,
                            created_at=utcnow_iso(),
                        )
                except Exception as error:  # noqa: BLE001
                    errors.append(f"ref {type(error).__name__}: {error}")
                index += 1

        threads = [threading.Thread(target=writer, args=(str(n),)) for n in range(4)]
        threads += [threading.Thread(target=collector) for _ in range(2)]
        threads += [threading.Thread(target=referencer) for _ in range(2)]
        for thread in threads:
            thread.start()
        time.sleep(3.0)
        stop.set()
        for thread in threads:
            thread.join()
    finally:
        repository.close()
    assert not errors, f"동시 접근에서 예외: {errors[:5]}"


# -- D-124/D-127: 트랜잭션 종료 경로 -----------------------------------------


def test_failed_commit_does_not_leave_the_connection_unusable(tmp_path):
    """COMMIT이 실패해도 트랜잭션이 남으면 안 된다.

    남으면 이후 모든 `BEGIN IMMEDIATE`가 "cannot start a transaction within a
    transaction"으로 죽어, 프로세스가 사는 동안 **쓰기가 영구히 막힌다**.
    읽기는 계속 되므로 조용히 "저장은 안 되는데 조회는 되는" 상태가 된다.
    """
    repository = Repository(tmp_path / "commit.db")
    try:
        raw = repository._connection._connection

        class FlakyCommit:
            """COMMIT을 한 번만 실패시키는 프록시 (`Connection.execute`는 대입 불가)."""

            def __init__(self, inner):
                self._inner = inner
                self.fail = True

            def execute(self, sql, *args):
                if sql == "COMMIT" and self.fail:
                    self.fail = False
                    raise sqlite3.OperationalError("database is locked")
                return self._inner.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        proxy = FlakyCommit(raw)
        repository._connection._connection = proxy  # type: ignore[assignment]
        with pytest.raises(sqlite3.OperationalError):
            _seed(repository, "https://e.test/a", 1)
        repository._connection._connection = raw  # type: ignore[assignment]

        assert not raw.in_transaction, "트랜잭션이 열린 채 남았다"
        _seed(repository, "https://e.test/b", 1)  # 이후 쓰기가 살아 있어야 한다
        assert len(repository.list_documents()) >= 1
    finally:
        repository.close()


def test_disk_full_error_is_not_masked_by_the_rollback(tmp_path):
    """SQLITE_FULL이면 SQLite가 트랜잭션을 스스로 폐기한다.

    그 상태에서 무조건 ROLLBACK을 내면 "no transaction is active"가 원래 예외를
    덮어써, 디스크가 찼다는 사실이 사용자에게 도달하지 않는다.
    """
    repository = Repository(tmp_path / "full.db")
    try:
        repository._connection.execute("PRAGMA max_page_count = 40")
        with pytest.raises(sqlite3.Error) as caught:
            _seed(repository, "https://e.test/big", 6, body=_bulk(200_000))
        message = str(caught.value).lower()
        assert "full" in message or "too large" in message, f"원래 원인이 가려졌다: {message}"
    finally:
        repository.close()


# -- D-125: 레이트 제한이 무관한 호스트를 막는가 -----------------------------


def test_rate_limit_does_not_serialize_unrelated_hosts():
    """버킷은 호스트별인데 락이 전역이면, 한 호스트의 대기가 전부를 세운다.

    SPEC §10 "한 문서의 작업이 다른 문서를 막지 않을 것"에 직접 걸린다.
    """
    limiter = HostRateLimiter(rate=4.0, burst=1)
    hosts = [f"h{index}.test" for index in range(6)]
    rounds = 3

    def hammer(host: str) -> None:
        for _ in range(rounds):
            limiter.acquire(host)

    started = time.monotonic()
    threads = [threading.Thread(target=hammer, args=(host,)) for host in hosts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started

    # 호스트별로 독립이면 약 0.5초(3회 × 0.25초). 전역 락이면 6배로 늘어난다.
    assert elapsed < 1.5, f"호스트들이 직렬화됐다 — {elapsed:.2f}초 (기대 약 0.5초)"


def test_rate_limit_still_paces_a_single_host():
    limiter = HostRateLimiter(rate=2.0, burst=1)
    limiter.acquire("one.test")
    started = time.monotonic()
    limiter.acquire("one.test")
    assert time.monotonic() - started >= 0.4, "같은 호스트의 간격이 지켜지지 않았다"


# -- D-126/D-159: VACUUM ------------------------------------------------------


def test_gc_reclaims_free_pages_left_by_a_table_rebuild(tmp_path):
    """마이그레이션의 표 재작성이 남긴 빈 페이지를 gc가 회수해야 한다.

    지울 행이 없으면 VACUUM 전에 반환하던 탓에, 업그레이드 직후 파일이 약 2배로
    부푼 채 `anchor gc`를 돌려도 그대로였다.
    """
    path = tmp_path / "bloat.db"
    repository = Repository(path)
    try:
        document_id, version_ids = _seed(repository, "https://e.test/a", 40, body=_bulk(20_000))
        repository.observe_version(document_id, version_ids[-1], utcnow_iso())
        # gc를 거치지 않고 직접 지워 빈 페이지를 만든다 (표 재작성이 남기는 상태)
        repository._connection.execute("DELETE FROM versions WHERE id != ?", (version_ids[-1],))
        (free_before,) = repository._connection.execute("PRAGMA freelist_count").fetchone()
        repository.collect_garbage_versions(keep=1)
        (free_after,) = repository._connection.execute("PRAGMA freelist_count").fetchone()
    finally:
        repository.close()
    assert free_before > 0, "픽스처가 빈 페이지를 만들지 못했다"
    assert free_after < free_before, f"빈 페이지가 회수되지 않았다 ({free_before} → {free_after})"


def test_vacuum_runs_on_its_own_connection(tmp_path, monkeypatch):
    """VACUUM은 저장소 락 **밖**에서 돌아야 한다 (D-126).

    시간으로 재려면 수백 MB짜리 DB가 필요해 단위 테스트로는 잡히지 않는다
    (840MB에서 27초 정지를 감사가 실측했다). 그래서 결정 자체를 고정한다 —
    별도 커넥션을 열지 않으면 이 테스트가 먼저 빨개진다.
    """
    import anchor.store.repository as repository_module

    opened: list[str] = []
    original = repository_module.sqlite3.connect

    def spy(path, *args, **kwargs):
        opened.append(str(path))
        return original(path, *args, **kwargs)

    repository = Repository(tmp_path / "vacuum-conn.db")
    try:
        document_id, version_ids = _seed(
            repository, "https://e.test/a", 40, body=_bulk(20_000)
        )
        repository.observe_version(document_id, version_ids[-1], utcnow_iso())
        monkeypatch.setattr(repository_module.sqlite3, "connect", spy)
        repository.collect_garbage_versions(keep=1)
    finally:
        repository.close()
    assert opened, "VACUUM이 저장소 커넥션에서 돌았다 — 같은 프로세스의 읽기가 멈춘다"


def test_reference_created_between_select_and_delete_is_respected(tmp_path):
    """조회와 삭제 사이에 생긴 참조를 존중해야 한다 (D-081).

    삭제 시점에 보존 조건을 다시 확인하지 않으면, 그 사이에 누군가 그 버전을
    인용하는 순간 **삭제 전체가 FK 위반으로 롤백**된다 — 되돌림 문서 하나가
    저장소 전체의 gc를 마비시켰던 것과 같은 형태다.

    경합을 기다리지 않고 창을 직접 연다: 대상 조회가 끝난 직후, 후보 하나에
    앵커를 달아 놓고 삭제를 진행시킨다.
    """
    repository = Repository(tmp_path / "window.db")
    try:
        document_id, version_ids = _seed(repository, "https://e.test/a", 8)
        repository.observe_version(document_id, version_ids[-1], utcnow_iso())

        wrapper = repository._connection
        original = wrapper.execute
        state = {"hooked": False}

        def hook(sql, params=()):
            rows = original(sql, params)
            if not state["hooked"] and "rank_in_document" in sql:
                state["hooked"] = True
                victim = rows.fetchall()[0][0]
                repository.insert_anchor(
                    document_id=document_id,
                    created_version=victim,
                    exact="조회와 삭제 사이에 생긴 인용이다",
                    prefix="앞", suffix="뒤", position_hint=0,
                    exact_hash="b3:e", quality="ok", note=None,
                    created_at=utcnow_iso(),
                )
                state["victim"] = victim
            return rows

        wrapper.execute = hook  # type: ignore[method-assign]
        try:
            deleted, _ = repository.collect_garbage_versions(keep=2)
        finally:
            wrapper.execute = original  # type: ignore[method-assign]

        assert state.get("victim"), "창이 열리지 않았다 — 픽스처가 조회를 가로채지 못했다"
        remaining = {version.id for version in repository.list_versions(document_id)}
        assert state["victim"] in remaining, "그 사이 인용된 버전이 지워졌다"
        assert deleted > 0, "참조 하나 때문에 삭제 전체가 무산됐다"
    finally:
        repository.close()


@pytest.mark.parametrize("source", ["live", "archive"])
def test_reuse_and_point_is_atomic(tmp_path, source):
    """되돌림 재사용은 **찾기와 가리키기가 한 트랜잭션**이어야 한다 (D-177).

    갈라져 있으면 그 사이 다른 프로세스의 `anchor gc`가 그 행을 지울 수 있고,
    `UPDATE`가 FK로 죽어 맨 `sqlite3.IntegrityError`가 MCP 호출자에게 올라간다.
    창은 **별도 커넥션**으로 연다 — 같은 커넥션은 재진입 락이라 충실하지 않다.
    """
    path = tmp_path / "reuse.db"
    repository = Repository(path)
    other = Repository(path)
    try:
        document_id, version_ids = _seed(repository, "https://e.test/a", 3, source=source)
        repository.observe_version(document_id, version_ids[-1], utcnow_iso())
        target = repository.get_version(version_ids[0])
        assert target is not None

        deleted = threading.Event()

        def racer() -> None:
            """찾기와 가리키기 사이를 노려 다른 커넥션에서 지운다."""
            try:
                with other._connection as connection:
                    connection.execute("DELETE FROM versions WHERE id = ?", (target.id,))
                deleted.set()
            except Exception:  # noqa: BLE001 — 원자적이면 여기서 막힌다
                pass

        wrapper = repository._connection
        original = wrapper.execute
        state = {"raced": False}

        def hook(sql, params=()):
            rows = original(sql, params)
            if not state["raced"] and "WHERE document_id = ? AND text_hash = ?" in sql:
                state["raced"] = True
                thread = threading.Thread(target=racer)
                thread.start()
                thread.join(timeout=1.0)
            return rows

        wrapper.execute = hook  # type: ignore[method-assign]
        try:
            result = repository.reuse_and_point(document_id, target.text_hash, source)
        finally:
            wrapper.execute = original  # type: ignore[method-assign]

        assert state["raced"], "창이 열리지 않았다 — 픽스처가 조회를 가로채지 못했다"
        assert result is not None, "재사용 대상이 조회와 갱신 사이에 사라졌다"
        assert repository.current_version(document_id).id == target.id
    finally:
        repository.close()
        other.close()


def test_observing_a_vanished_version_raises_an_anchor_error(tmp_path):
    """가리킬 대상이 사라졌을 때 맨 `sqlite3.IntegrityError`가 새면 안 된다 (D-182).

    `AnchorError`가 아니면 `verify()`가 잡지 못해 **문서 하나 때문에 검증
    보고서 전체가 사라진다**. 아카이브 버전은 `captured_at`이 과거 Memento
    시각이라 gc 회수 순위가 낮아, 저장 직후 아무도 참조하지 않는 그 틈이
    특히 벌어지기 쉽다.
    """
    path = tmp_path / "vanish.db"
    repository = Repository(path)
    try:
        document_id, version_ids = _seed(repository, "https://e.test/a", 2)
        victim = version_ids[0]
        with repository._connection as connection:
            connection.execute("DELETE FROM versions WHERE id = ?", (victim,))
        with pytest.raises(AnchorError):
            repository.observe_version(document_id, victim, "2026-08-18T00:00:00Z")
    finally:
        repository.close()


def test_creating_the_same_document_twice_converges(tmp_path):
    """같은 URL을 두 곳에서 만들어도 **한 행으로 수렴**해야 한다 (D-101).

    서비스의 스트라이프 락은 **입력 URL**로 잡히는데 문서는 **최종 URL**로
    만들어진다. 서로 다른 두 URL이 같은 목적지로 리다이렉트되면(캐노니컬
    리다이렉트 — 링크 부패 도구가 가장 흔히 만나는 형태) 두 호출이 다른
    스트라이프를 잡고 나란히 "문서 없음"으로 판단한다.

    스레드 경합에 기대면 창이 열리지 않은 채 초록이 나온다(실제로 그랬다).
    창을 직접 연다 — 두 저장소가 순서대로 같은 URL을 만들게 한다.
    """
    path = tmp_path / "converge.db"
    first, second = Repository(path), Repository(path)
    try:
        url = "https://e.test/canonical"
        now = utcnow_iso()
        a = first.create_document(url=url, original_url="https://e.test/one", title=None, now=now)
        b = second.create_document(url=url, original_url="https://e.test/two", title=None, now=now)
        assert a.id == b.id, "같은 정본이 두 문서로 갈렸다"
        assert len(first.list_documents()) == 1
    finally:
        first.close()
        second.close()


def test_inserting_the_same_version_twice_converges(tmp_path):
    """같은 본문을 두 곳에서 넣어도 한 행으로 수렴해야 한다 (D-101).

    문서 생성이 한 행으로 모인 직후 두 호출이 **같은 본문**을 나란히 넣으려
    해서 `UNIQUE(document_id, text_hash, source)`에 걸린다 — 문서 경합을
    막고 나서야 드러난 두 번째 층이다.
    """
    path = tmp_path / "converge-v.db"
    first, second = Repository(path), Repository(path)
    try:
        now = utcnow_iso()
        document = first.create_document(
            url="https://e.test/d", original_url="https://e.test/d", title=None, now=now
        )
        kwargs = dict(
            document_id=document.id,
            text_hash="b3:same",
            raw_hash="b3:raw",
            pipeline_version="test/1",
            captured_at=now,
            byte_size=10,
            normalized_text="같은 본문이다.",
            http_status=200,
        )
        a = first.insert_version(**kwargs)
        b = second.insert_version(**kwargs)
        assert a.id == b.id, "같은 본문이 두 버전으로 갈렸다"
        assert len(first.list_versions(document.id)) == 1
    finally:
        first.close()
        second.close()


def _seed_document(repository: Repository, url: str, body: str) -> str:
    now = utcnow_iso()
    document = repository.create_document(url=url, original_url=url, title=None, now=now)
    version = repository.insert_version(
        document_id=document.id,
        text_hash=f"b3:{url}",
        raw_hash=f"b3:raw-{url}",
        pipeline_version="test/1",
        captured_at=now,
        byte_size=len(body),
        normalized_text=body,
        http_status=200,
    )
    repository.observe_version(document.id, version.id, now)
    return document.id


def test_inserting_an_anchor_after_a_merge_is_a_domain_error(tmp_path):
    """문서 행이 병합으로 사라진 뒤의 앵커 삽입은 **도메인 오류**여야 한다 (D-185).

    `merge_document`는 이 코드베이스에서 `documents` 행을 지우는 유일한
    경로다. CLI와 MCP 서버가 같은 DB를 공유하므로, 한쪽의 페치가 병합을
    일으키면 다른 쪽의 `cite`가 그 사이에 있던 문서 id로 삽입을 시도한다 —
    맨 `sqlite3.IntegrityError`가 새면 MCP 호출자의 배치가 통째로 죽는다.
    """
    path = tmp_path / "merge-cite.db"
    repository, other = Repository(path), Repository(path)
    try:
        source = _seed_document(repository, "https://e.test/a", "A 본문이다. " * 30)
        target = _seed_document(repository, "https://e.test/b", "B 본문이다. " * 30)
        version = repository.current_version(source)
        assert version is not None

        other.merge_document(source, target)  # 다른 프로세스의 페치가 병합을 일으켰다

        with pytest.raises(AnchorError):
            repository.insert_anchor(
                document_id=source,
                created_version=version.id,
                exact="A 본문이다.",
                prefix="",
                suffix="",
                position_hint=0,
                exact_hash="b3:q",
                quality="ok",
                note=None,
                created_at=utcnow_iso(),
            )
    finally:
        repository.close()
        other.close()


def test_merge_moves_the_accounting_rows(tmp_path):
    """병합은 회계(fetch_log)도 함께 옮겨야 한다 (D-195).

    남겨 두면 고아 행이 되고 `bytes_saved_estimate`가 그만큼 절감량을
    축소 보고한다.
    """
    path = tmp_path / "merge-log.db"
    repository = Repository(path)
    try:
        source = _seed_document(repository, "https://e.test/a", "A 본문이다. " * 30)
        target = _seed_document(repository, "https://e.test/b", "B 본문이다. " * 30)
        for _ in range(3):
            repository.log_fetch(
                document_id=source, url=f"https://example.test/{source}",
                outcome="cache_hit", error_kind=None, http_status=None,
                bytes_down=0, elapsed_ms=1, requested_at=utcnow_iso(),
            )
        repository.merge_document(source, target)
        orphans = repository._connection.execute(
            "SELECT COUNT(*) AS n FROM fetch_log WHERE document_id = ?", (source,)
        ).fetchone()["n"]
        moved = repository._connection.execute(
            "SELECT COUNT(*) AS n FROM fetch_log WHERE document_id = ?", (target,)
        ).fetchone()["n"]
    finally:
        repository.close()
    assert orphans == 0, f"고아 회계 행 {orphans}건"
    assert moved == 3
