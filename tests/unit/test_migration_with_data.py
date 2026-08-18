# SPDX-License-Identifier: Apache-2.0
"""데이터가 든 구버전 DB의 마이그레이션 (D-077/D-078/D-079).

기존 `test_schema_stability.py`는 **행이 하나도 없는** v1 DB만 올렸다. 그
픽스처로는 v5의 표 재작성(`DROP TABLE versions`)이 `documents.current_version`
FK를 위반한다는 사실이 드러나지 않는다 — 참조할 행이 없으니 위반도 없다.
실제 사용자의 DB에는 행이 있다.

여기서는 사용자가 v0.1~v1.0.x를 쓰며 쌓았을 법한 행을 실제로 채운 뒤 올린다.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from anchor.store.repository import (
    MIGRATION_FILES,
    SCHEMA_VERSION,
    Repository,
    _split_statements,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
V1_SCHEMA = FIXTURES / "schema_v1.sql"


def _migration_sql(version: int) -> str:
    from importlib import resources

    return (
        resources.files("anchor.store")
        .joinpath(MIGRATION_FILES[version])
        .read_text("utf-8")
    )


def _seed_v1_rows(connection: sqlite3.Connection) -> None:
    """v0.1 시절 사용자가 쌓았을 행 — 문서 2건, 버전 3건, 회계·robots 캐시."""
    connection.execute(
        """INSERT INTO documents
           (id, url, original_url, title, first_seen_at, last_checked_at,
            status, etag, last_modified, robots_allowed)
           VALUES ('doc-1', 'https://e.test/a', 'https://e.test/a', '문서 A',
                   '2026-08-01T00:00:00Z', '2026-08-02T00:00:00Z', 'live',
                   '"v1"', NULL, 1)"""
    )
    # 리다이렉트로 도달한 문서 — 0004가 별칭으로 옮기는 대상이다.
    connection.execute(
        """INSERT INTO documents
           (id, url, original_url, title, first_seen_at, last_checked_at,
            status, etag, last_modified, robots_allowed)
           VALUES ('doc-2', 'https://e.test/final', 'https://e.test/old', '문서 B',
                   '2026-08-01T00:00:00Z', '2026-08-03T00:00:00Z', 'live',
                   NULL, NULL, 1)"""
    )
    for version_id, document_id, captured_at in (
        ("ver-1", "doc-1", "2026-08-01T00:00:00Z"),
        ("ver-2", "doc-1", "2026-08-02T00:00:00Z"),
        ("ver-3", "doc-2", "2026-08-03T00:00:00Z"),
    ):
        connection.execute(
            """INSERT INTO versions
               (id, document_id, text_hash, raw_hash, pipeline_version, captured_at,
                byte_size, char_count, content_blob, http_status, source)
               VALUES (?, ?, ?, ?, 'trafilatura/2.2.0+norm/1', ?, 100, 50, X'00', 200, 'live')""",
            (version_id, document_id, f"b3:{version_id}", f"b3:raw-{version_id}", captured_at),
        )
    connection.execute(
        """INSERT INTO fetch_log (document_id, requested_at, outcome, http_status,
                                  bytes_down, elapsed_ms)
           VALUES ('doc-1', '2026-08-02T00:00:00Z', 'changed', 200, 100, 12)"""
    )
    connection.execute(
        """INSERT INTO robots_cache (origin, body, fetch_status, fetched_at)
           VALUES ('https://e.test', 'User-agent: *', 200, '2026-08-01T00:00:00Z')"""
    )


def _seed_anchor_rows(connection: sqlite3.Connection) -> None:
    """v2에서 표가 생긴 뒤에야 넣을 수 있는 행 — 앵커와 검증 이력.

    둘 다 `versions(id)`를 참조하므로 v5의 표 재작성에서 FK 대상이 된다.
    """
    connection.execute(
        """INSERT INTO anchors
           (id, document_id, created_version, exact, prefix, suffix,
            position_hint, exact_hash, quality, note, created_at)
           VALUES ('anc-1', 'doc-1', 'ver-1', '인용문 하나가 여기에 있다',
                   '앞 문맥', '뒤 문맥', 10, 'b3:exact', 'ok', NULL,
                   '2026-08-01T12:00:00Z')"""
    )
    connection.execute(
        """INSERT INTO verifications
           (id, anchor_id, checked_version, checked_at, state, match_score,
            edit_distance, found_offset, found_text, elapsed_ms)
           VALUES ('vrf-1', 'anc-1', 'ver-2', '2026-08-02T12:00:00Z', 'INTACT',
                   1.0, 0, 10, NULL, 3)"""
    )


def build_old_db(path: Path, target_version: int) -> None:
    """`target_version` 스키마에 행이 든 DB를 만든다.

    행을 먼저 넣고 마이그레이션을 적용한다 — 실제 사용자의 DB가 그렇게
    만들어지기 때문이다(0003의 `UPDATE`가 기존 버전에서 포인터를 채우고,
    0004의 `INSERT..SELECT`가 기존 original_url을 별칭으로 옮긴다).
    """
    connection = sqlite3.connect(path)
    try:
        connection.executescript(V1_SCHEMA.read_text("utf-8"))
        connection.execute("PRAGMA user_version = 1")
        _seed_v1_rows(connection)
        for version in range(2, target_version + 1):
            connection.executescript(_migration_sql(version))
            connection.execute(f"PRAGMA user_version = {version}")
            if version == 2:
                _seed_anchor_rows(connection)
        connection.commit()
    finally:
        connection.close()


def _snapshot(path: Path) -> dict:
    connection = sqlite3.connect(path)
    try:
        counts = {}
        for table in ("documents", "versions", "anchors", "verifications", "fetch_log"):
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
            ).fetchall()
            counts[table] = (
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if rows else 0
            )
        return counts
    finally:
        connection.close()


def _user_version(path: Path) -> int:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()


# -- D-077: 데이터가 든 구 DB의 마이그레이션 -------------------------------


@pytest.mark.parametrize("source_version", [1, 2, 3, 4, 5])
def test_migrates_old_db_that_has_rows(tmp_path, source_version):
    path = tmp_path / f"v{source_version}.db"
    build_old_db(path, source_version)
    before = _snapshot(path)

    Repository(path).close()  # 여는 순간 마이그레이션

    assert _user_version(path) == SCHEMA_VERSION
    after = _snapshot(path)
    for table in ("documents", "versions", "fetch_log"):
        assert after[table] == before[table], f"{table} 행이 유실됐다"
    if source_version >= 2:
        assert after["anchors"] == before["anchors"] == 1
        assert after["verifications"] == before["verifications"] == 1


@pytest.mark.parametrize("source_version", [1, 2, 3, 4, 5])
def test_migration_leaves_no_dangling_references(tmp_path, source_version):
    """표를 다시 만드는 v5 이후에도 참조가 살아 있어야 한다."""
    path = tmp_path / f"fk-v{source_version}.db"
    build_old_db(path, source_version)
    Repository(path).close()

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        # `current_version` 포인터가 실재하는 버전을 가리키는가 (0003이 채운 값)
        dangling = connection.execute(
            """SELECT d.id FROM documents d
               WHERE d.current_version IS NOT NULL
                 AND NOT EXISTS (SELECT 1 FROM versions v WHERE v.id = d.current_version)"""
        ).fetchall()
        assert dangling == []
        pointers = connection.execute(
            "SELECT COUNT(*) FROM documents WHERE current_version IS NOT NULL"
        ).fetchone()[0]
        assert pointers == 2, "0003이 채운 포인터가 v5 재작성에서 사라졌다"
    finally:
        connection.close()


def test_migrated_db_with_rows_is_usable(tmp_path):
    """열리는 것으로 끝나지 않는다 — 마이그레이션 후 실제로 쓸 수 있어야 한다."""
    path = tmp_path / "usable.db"
    build_old_db(path, 2)
    repository = Repository(path)
    try:
        document = repository.get_document("doc-1")
        assert document is not None
        assert repository.current_version(document.id) is not None
        anchor = repository.get_anchor("anc-1")
        assert anchor is not None and anchor.created_version == "ver-1"
        # 출처별 유일성(v5)이 실제로 적용됐는지 — 같은 본문의 archive 행이 들어가야 한다
        repository.insert_version(
            document_id="doc-1",
            text_hash="b3:ver-1",
            raw_hash="b3:raw",
            pipeline_version="test/1",
            captured_at="2026-08-04T00:00:00Z",
            byte_size=10,
            normalized_text="본문",
            http_status=200,
            source="archive",
            source_uri="https://archive.test/x",
        )
    finally:
        repository.close()


# -- D-078: 동시 오픈 경쟁 ---------------------------------------------------


def _open_concurrently(path: Path, workers: int) -> list[str]:
    errors: list[str] = []
    barrier = threading.Barrier(workers)

    def worker() -> None:
        barrier.wait()
        try:
            Repository(path).close()
        except Exception as error:  # noqa: BLE001 — 무엇이든 기록해야 한다
            errors.append(f"{type(error).__name__}: {error}")

    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_concurrent_first_open_of_new_db(tmp_path):
    """여러 클라이언트가 없는 DB를 동시에 처음 열어도 한 번만 생성돼야 한다."""
    for round_index in range(5):
        path = tmp_path / f"fresh-{round_index}.db"
        errors = _open_concurrently(path, workers=8)
        assert errors == [], f"라운드 {round_index}: {errors}"
        assert _user_version(path) == SCHEMA_VERSION


def test_concurrent_migration_of_old_db_with_rows(tmp_path):
    for round_index in range(3):
        path = tmp_path / f"old-{round_index}.db"
        build_old_db(path, 2)
        errors = _open_concurrently(path, workers=6)
        assert errors == [], f"라운드 {round_index}: {errors}"
        assert _user_version(path) == SCHEMA_VERSION
        assert _snapshot(path)["versions"] == 3


# -- D-079: 문장 분리 --------------------------------------------------------


def test_split_statements_respects_string_literals():
    statements = _split_statements(
        "CREATE TABLE t (a TEXT DEFAULT 'x;y');\nCREATE INDEX i ON t(a);"
    )
    assert len(statements) == 2
    assert "'x;y'" in statements[0]


def test_split_statements_respects_line_comments():
    """이 코드베이스는 `-- live | gone | forbidden` 같은 열거 주석을 관행적으로 쓴다."""
    statements = _split_statements(
        "CREATE TABLE t (\n  status TEXT  -- live; gone; forbidden\n);\nCREATE INDEX i ON t(status);"
    )
    assert len(statements) == 2
    assert "CREATE TABLE" in statements[0]


def test_split_statements_respects_trigger_bodies():
    statements = _split_statements(
        "CREATE TABLE t (a INTEGER);\n"
        "CREATE TRIGGER tr AFTER INSERT ON t BEGIN UPDATE t SET a = 1; END;\n"
    )
    assert len(statements) == 2
    assert statements[1].startswith("CREATE TRIGGER")
    assert statements[1].rstrip().endswith("END;")


def test_split_statements_handles_trailing_comment():
    statements = _split_statements("CREATE TABLE t (a INTEGER);\n-- 끝맺음 주석\n")
    assert [s for s in statements if "CREATE TABLE" in s]


def test_split_statements_separates_two_statements_on_one_line():
    """줄 단위로 물으면 한 줄에 든 두 문장을 쪼개지 못해 `execute`가 거절한다."""
    statements = _split_statements("CREATE TABLE a(x); CREATE TABLE b(y);")
    assert len(statements) == 2


def test_shipped_migrations_split_into_individually_executable_statements(tmp_path):
    """배포 SQL의 각 조각이 **혼자서 실행 가능한 한 문장**인지 검사한다.

    `complete_statement`만으로는 부족하다 — sqlite3_complete()는
    `CREATE …; CREATE …;`도 complete로 보므로, 쪼개지지 않은 덩어리가
    가드를 통과해 사용자 쪽에서 "one statement at a time"으로 터진다.
    조각을 다시 쪼개 1개가 나오는지(=더 쪼갤 수 없는지)로 판정한다.
    """
    sources = {name: _migration_sql(name) for name in MIGRATION_FILES}
    sources["schema"] = (
        __import__("importlib.resources", fromlist=["files"])
        .files("anchor.store")
        .joinpath("schema.sql")
        .read_text("utf-8")
    )
    probe = sqlite3.connect(":memory:")
    try:
        for name, sql in sources.items():
            statements = _split_statements(sql)
            assert statements, f"{name}: 분리 결과가 비었다"
            for statement in statements:
                assert len(_split_statements(statement)) == 1, (
                    f"{name}: 더 쪼갤 수 있는 덩어리가 남았다 — {statement[:60]!r}"
                )
                try:
                    probe.execute(statement)
                except sqlite3.ProgrammingError as error:  # 한 번에 한 문장만 허용
                    raise AssertionError(f"{name}: {error} — {statement[:60]!r}") from error
                except sqlite3.DatabaseError:
                    pass  # 문맥이 없어 나는 오류는 무관하다 (표 부재 등)
    finally:
        probe.close()


# -- 조치가 세운 전제를 지키는 회귀선 -----------------------------------------


def test_foreign_keys_are_on_after_open(tmp_path):
    """마이그레이션 구간에서만 FK를 내린다 — 열고 나면 반드시 켜져 있어야 한다.

    이 회귀선이 없으면 `_migrate`의 `finally`가 사라져도 아무도 모르고,
    저장소 전체가 FK 없이 돌아 매달린 참조가 조용히 쌓인다.
    """
    for source_version in (None, 1, 4):
        path = tmp_path / f"fk-on-{source_version}.db"
        if source_version is not None:
            build_old_db(path, source_version)
        repository = Repository(path)
        try:
            (enabled,) = repository._connection.execute("PRAGMA foreign_keys").fetchone()
            assert enabled == 1, f"v{source_version}: 마이그레이션 후 FK가 꺼진 채 남았다"
        finally:
            repository.close()


def test_foreign_keys_are_restored_when_migration_fails(tmp_path):
    """실패 경로에서도 FK가 복구돼야 한다."""
    path = tmp_path / "fk-restore.db"
    build_old_db(path, 4)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE documents SET current_version = '존재하지 않는 버전' WHERE id = 'doc-1'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="무결성"):
        Repository(path)

    # 같은 파일을 다시 열어도(정상화 후) FK가 켜져 있어야 한다
    connection = sqlite3.connect(path)
    connection.execute("UPDATE documents SET current_version = 'ver-1' WHERE id = 'doc-1'")
    connection.commit()
    connection.close()
    repository = Repository(path)
    try:
        (enabled,) = repository._connection.execute("PRAGMA foreign_keys").fetchone()
        assert enabled == 1
    finally:
        repository.close()


def test_migration_refuses_to_commit_dangling_references(tmp_path):
    """FK를 끈 구간의 안전망 — `foreign_key_check`가 없으면 조용히 커밋된다."""
    path = tmp_path / "dangling.db"
    build_old_db(path, 4)
    connection = sqlite3.connect(path)
    connection.execute(
        "UPDATE documents SET current_version = '없는-버전' WHERE id = 'doc-2'"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="무결성"):
        Repository(path)

    # 어중간하게 올라가지 않았는가 — 원인을 고치면 다시 올릴 수 있어야 한다
    assert _user_version(path) == 4


def test_migration_converges_when_another_client_advances_version(tmp_path):
    """마이그레이션 도중 다른 클라이언트가 버전을 올려도 수렴해야 한다."""
    path = tmp_path / "advance.db"
    build_old_db(path, 1)

    original = Repository._apply_sql_atomically
    state = {"jumped": False}

    def jump_after_first(self, sql, target_version, *, expected_version):
        original(self, sql, target_version, expected_version=expected_version)
        if not state["jumped"]:
            state["jumped"] = True
            # 다른 클라이언트가 나머지를 마저 올려놓은 상황
            other = Repository(path)
            other.close()

    Repository._apply_sql_atomically = jump_after_first  # type: ignore[method-assign]
    try:
        Repository(path).close()
    finally:
        Repository._apply_sql_atomically = original  # type: ignore[method-assign]

    assert _user_version(path) == SCHEMA_VERSION
    assert _snapshot(path)["versions"] == 3


def test_readonly_db_fails_fast_without_wal_retry(tmp_path):
    """경합이 아닌 실패는 즉시 보고해야 한다 (D-155 조치의 경계).

    잠금 경합이 아닌 오류까지 재시도하면 진짜 원인을 몇 초 늦게 보여줄 뿐이고,
    CLI는 명령마다 저장소를 열므로 그 대기를 매번 문다.
    """
    import os
    import time as _time

    path = tmp_path / "readonly.db"
    Repository(path).close()
    for suffix in ("-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()
    os.chmod(path, 0o444)
    os.chmod(tmp_path, 0o555)
    try:
        if os.access(path, os.W_OK):
            pytest.skip("이 환경에서는 읽기 전용이 강제되지 않는다 (root 등)")
        started = _time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            Repository(path)
        elapsed = _time.monotonic() - started
        assert elapsed < 1.0, f"경합이 아닌 실패에 {elapsed:.2f}초를 썼다"
    finally:
        os.chmod(tmp_path, 0o755)
        os.chmod(path, 0o644)


# -- D-083: 관측 시간축을 뒤늦게 도입할 때 -----------------------------------


@pytest.mark.parametrize("source_version", [1, 2, 3, 4, 5])
def test_observation_timeline_is_seeded_for_existing_rows(tmp_path, source_version):
    """v6 이전의 행에도 **관측 순서가 있어야** 한다 (D-083).

    없으면 전부 0으로 남아 `latest~N`이 임의의 행을 가리킨다. 그때까지
    알 수 있는 최선은 캡처 순서이고, 되돌림이 없었던 문서에서는 그것이
    실제 관측 순서와 같다.
    """
    path = tmp_path / f"obs-v{source_version}.db"
    build_old_db(path, source_version)
    Repository(path).close()

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT document_id, id, captured_at, last_observed_at, last_observed_seq
               FROM versions ORDER BY document_id, captured_at"""
        ).fetchall()
        assert rows, "픽스처에 버전이 없다"
        for row in rows:
            assert row["last_observed_at"] == row["captured_at"], "관측 시각이 비었다"
        per_document: dict[str, list[int]] = {}
        for row in rows:
            per_document.setdefault(row["document_id"], []).append(row["last_observed_seq"])
        for document_id, sequence in per_document.items():
            assert len(set(sequence)) == len(sequence), f"{document_id}: 순번이 겹친다"
            assert sequence == sorted(sequence), f"{document_id}: 캡처 순서와 어긋난다"
            assert min(sequence) >= 1, f"{document_id}: 0이 남았다"
    finally:
        connection.close()


def test_observation_sequence_continues_after_migration(tmp_path):
    """마이그레이션이 매긴 순번 **위로** 새 관측이 쌓여야 한다 (D-083).

    새 관측이 1부터 다시 시작하면 옛 판본이 최신으로 보인다.
    """
    path = tmp_path / "continue.db"
    build_old_db(path, 5)
    repository = Repository(path)
    try:
        document_id = repository.list_documents()[0].id
        before = max(v.last_observed_seq for v in repository.list_versions(document_id))
        oldest = repository.list_versions(document_id)[0]
        repository.observe_version(document_id, oldest.id, "2026-08-18T00:00:00Z")
        after = repository.get_version(oldest.id)
        assert after is not None
        assert after.last_observed_seq > before, "새 관측이 옛 순번 아래로 들어갔다"
        assert repository.list_versions_by_observation(document_id)[0].id == oldest.id
    finally:
        repository.close()


def test_migration_puts_the_current_body_newest(tmp_path):
    """마이그레이션은 **포인터가 가리키는 행**을 관측 순서 맨 앞에 둬야 한다 (D-180).

    `documents.current_version`은 이미 관측 순으로 옮겨져 있는데 순번을 캡처
    시각으로만 매기면, 되돌림·아카이브를 겪은 문서에서 `latest`(포인터)와
    `latest~0`(관측 순 0번)이 어긋난다 — `latest~1`이 latest와 같은 행을
    가리켜 기본 diff가 비고 중간 판본에 도달할 수 없다. 고치려던 증상 그대로다.
    아카이브 구제 문서는 원본이 죽어 재관측이 없으므로 **영구적**이다.
    """
    path = tmp_path / "pointer.db"
    build_old_db(path, 5)
    # 되돌림·아카이브를 흉내 낸다: 포인터를 캡처가 가장 오래된 판본으로 옮긴다.
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        for document in connection.execute("SELECT id FROM documents").fetchall():
            oldest = connection.execute(
                "SELECT id FROM versions WHERE document_id = ? ORDER BY captured_at ASC LIMIT 1",
                (document["id"],),
            ).fetchone()
            if oldest is not None:
                connection.execute(
                    "UPDATE documents SET current_version = ? WHERE id = ?",
                    (oldest["id"], document["id"]),
                )
        connection.commit()
    finally:
        connection.close()

    repository = Repository(path)
    try:
        for document in repository.list_documents():
            if document.current_version is None:
                continue
            ordered = repository.list_versions_by_observation(document.id)
            assert ordered[0].id == document.current_version, (
                "포인터가 가리키는 본문이 관측 순서 맨 앞이 아니다"
            )
            assert len({v.last_observed_seq for v in ordered}) == len(ordered)
    finally:
        repository.close()
