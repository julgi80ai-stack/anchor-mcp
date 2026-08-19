# SPDX-License-Identifier: Apache-2.0
"""안정 스키마 (SPEC §13 v1.0): 어떤 경로로 왔든 같은 스키마여야 한다.

v0.1 실DB(스키마 v1)에서 마이그레이션으로 올라온 DB와 신규 생성 DB의
구조가 완전히 일치하는지 검증한다. 여기서 어긋나면 마이그레이션 체인이
schema.sql과 갈라진 것이다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from anchor.store.repository import SCHEMA_VERSION, Repository

V1_SCHEMA = Path(__file__).parent.parent / "fixtures" / "schema_v1.sql"


def introspect(db_path: Path) -> dict:
    connection = sqlite3.connect(db_path)
    try:
        tables = {}
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table,) in rows:
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            tables[table] = [(c[1], c[2].upper(), c[3], c[5]) for c in columns]  # 이름,타입,notnull,pk
        indexes = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        return {"tables": tables, "indexes": indexes}
    finally:
        connection.close()


def test_migrated_v1_db_equals_fresh_schema(tmp_path):
    fresh_path = tmp_path / "fresh.db"
    Repository(fresh_path).close()

    migrated_path = tmp_path / "migrated.db"
    connection = sqlite3.connect(migrated_path)
    connection.executescript(V1_SCHEMA.read_text("utf-8"))
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()
    Repository(migrated_path).close()  # 열면서 v1 → 현재 버전 마이그레이션

    assert introspect(migrated_path) == introspect(fresh_path)

    (version,) = sqlite3.connect(migrated_path).execute("PRAGMA user_version").fetchone()
    assert version == SCHEMA_VERSION


def test_future_schema_version_is_rejected(tmp_path):
    db_path = tmp_path / "future.db"
    Repository(db_path).close()
    connection = sqlite3.connect(db_path)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.commit()
    connection.close()

    import pytest

    from anchor.errors import StorageError

    # 버전 롤백은 사용자의 평범한 상황이지 "도구가 깨졌다"가 아니다 — 도메인
    # 예외라야 CLI가 트레이스백 대신 한 줄로 답한다 (D-233, SPEC §8).
    with pytest.raises(StorageError, match=str(SCHEMA_VERSION + 1)):
        Repository(db_path)
