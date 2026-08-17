# SPDX-License-Identifier: Apache-2.0
"""저장소 원자성·스레드 안전성 회귀 (D-019 / D-020 / D-023).

이 세 건은 감사에서 각각 "DB 영구 접근 불능", "SIGSEGV", "롤백 무효화"로
실증됐다. 크래시는 pytest 안에서 잡을 수 없으므로 별도 프로세스로 돌린다.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest

from anchor.store.repository import SCHEMA_VERSION, Repository

SRC = str(Path(__file__).resolve().parents[2] / "src")


def run_in_subprocess(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": SRC, "PATH": "/usr/bin:/bin"},
        timeout=180,
    )


# -- D-019 원자적 마이그레이션 ------------------------------------------------


def test_interrupted_migration_leaves_db_reopenable(tmp_path):
    """마이그레이션 SQL이 중간에 실패해도 부분 적용이 남지 않아야 한다."""
    db_path = tmp_path / "broken.db"
    v1_sql = (Path(__file__).parent.parent / "fixtures" / "schema_v1.sql").read_text("utf-8")
    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.executescript(v1_sql)
    connection.execute("PRAGMA user_version = 1")
    connection.close()

    repository = Repository.__new__(Repository)  # __init__ 우회: 마이그레이션만 시험
    Repository.__init__(repository, db_path)
    repository.close()

    # 정상 경로 확인 후, 깨진 SQL로 마이그레이션을 강제해 롤백을 검증
    broken = Repository(db_path)
    with pytest.raises(sqlite3.Error):
        broken._apply_sql_atomically(
            "CREATE TABLE probe_ok (x INTEGER); THIS IS NOT SQL;", SCHEMA_VERSION + 5
        )
    tables = {
        row[0]
        for row in broken._connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    (version,) = broken._connection.execute("PRAGMA user_version").fetchone()
    broken.close()

    assert "probe_ok" not in tables, "실패한 마이그레이션의 테이블이 남았다"
    assert version == SCHEMA_VERSION, "버전이 어중간하게 올라갔다"
    Repository(db_path).close()  # 재개방 가능해야 한다


def test_fresh_schema_creation_is_all_or_nothing(tmp_path):
    """신규 DB 생성이 중단되면 테이블이 하나도 남지 않아야 한다."""
    db_path = tmp_path / "fresh.db"
    result = run_in_subprocess(f"""
        import sqlite3
        from anchor.store import repository as R
        original = R._load_schema
        # 스키마 끝에 실패하는 문장을 붙여 '중단'을 재현
        R._load_schema = lambda: original() + "\\n INVALID STATEMENT HERE;"
        try:
            R.Repository({str(db_path)!r})
        except Exception as error:
            print("raised:", type(error).__name__)
        c = sqlite3.connect({str(db_path)!r})
        print("tables:", sorted(r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")))
        print("user_version:", c.execute("PRAGMA user_version").fetchone()[0])
    """)
    assert "raised:" in result.stdout, result.stderr
    assert "tables: []" in result.stdout, f"부분 생성된 테이블이 남았다: {result.stdout}"
    assert "user_version: 0" in result.stdout

    Repository(db_path).close()  # 깨끗한 상태이므로 정상 생성되어야 한다
    assert Repository(db_path)._connection.execute("PRAGMA user_version").fetchone()[0] == (
        SCHEMA_VERSION
    )


# -- D-020 스레드 안전성 (별도 프로세스: segfault는 in-process로 못 잡는다) ----


def test_concurrent_inserts_do_not_crash(tmp_path):
    db_path = tmp_path / "threads.db"
    result = run_in_subprocess(f"""
        import threading
        from anchor.store.repository import Repository

        repository = Repository({str(db_path)!r})
        document = repository.create_document(
            url="https://example.invalid/a", original_url="https://example.invalid/a",
            title=None, now="2026-08-17T00:00:00Z",
        )
        errors = []

        def worker(offset):
            for index in range(150):
                try:
                    repository.insert_version(
                        document_id=document.id,
                        text_hash=f"b3:t{{offset}}-{{index}}",
                        raw_hash=f"b3:r{{offset}}-{{index}}",
                        pipeline_version="bench/1",
                        captured_at=f"2026-08-17T00:00:{{index % 60:02d}}Z",
                        byte_size=10,
                        normalized_text="본문 " * 50,
                        http_status=200,
                    )
                except Exception as error:
                    errors.append(repr(error))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        rows = repository._connection.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        repository.close()
        print("errors:", len(errors), errors[:3])
        print("versions:", rows)
    """)
    assert result.returncode == 0, f"프로세스가 죽었다 (segfault 등): rc={result.returncode}"
    assert "errors: 0" in result.stdout, result.stdout
    assert "versions: 600" in result.stdout, result.stdout


# -- D-023 트랜잭션 격리 ------------------------------------------------------


def test_rollback_is_not_defeated_by_concurrent_commit(tmp_path):
    """한 스레드의 롤백이 다른 스레드의 커밋에 무효화되면 안 된다."""
    repository = Repository(tmp_path / "iso.db")
    started = threading.Event()
    finish = threading.Event()

    def rolling_back():
        try:
            with repository._connection as connection:
                connection.execute(
                    "INSERT INTO fetch_log (document_id, requested_at, outcome,"
                    " http_status, bytes_down, elapsed_ms)"
                    " VALUES ('doc', '2026-08-17T00:00:00Z', 'should_rollback', 200, 0, 1)"
                )
                started.set()
                finish.wait(timeout=5)
                raise RuntimeError("의도적 실패")
        except RuntimeError:
            pass

    thread = threading.Thread(target=rolling_back)
    thread.start()
    started.wait(timeout=5)
    finish.set()
    thread.join(timeout=10)

    repository.log_fetch(
        document_id="doc", requested_at="2026-08-17T00:00:01Z", outcome="committed",
        http_status=200, bytes_down=0, elapsed_ms=1,
    )
    outcomes = [
        row[0]
        for row in repository._connection.execute("SELECT outcome FROM fetch_log").fetchall()
    ]
    repository.close()
    assert "should_rollback" not in outcomes, f"롤백된 행이 남았다: {outcomes}"
    assert "committed" in outcomes
