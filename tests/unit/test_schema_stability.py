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


# 마이그레이션 경로에만 남는, **알고 허용하는** 차이 (D-256).
#
# `(표, 열) → 마이그레이션 DB의 기본값`. SQLite는 행이 든 표에 NOT NULL 열을
# `ALTER TABLE ADD COLUMN`으로 붙일 때 **기본값을 요구한다** — 0006이 그래서
# `DEFAULT ''`/`DEFAULT 0`을 달았다. 신규 DB(schema.sql)에는 기본값이 없다.
#
# 이 차이를 없애려면 `versions` 표를 통째로 다시 써야 한다(blob 전부 복사).
# 그 대가에 비해 얻는 것은 없다: 삽입 경로는 두 값을 **항상** 채우므로
# (아래 시험이 그 사실을 고정한다) 기본값이 쓰이는 경로가 없다. 목록에 없는
# 차이는 전부 실패다 — 새로 생기는 어긋남은 여기서 빨개져야 한다.
KNOWN_DEFAULT_DIFFERENCES = {
    ("versions", "last_observed_at"): "''",
    ("versions", "last_observed_seq"): "0",
}


def introspect(db_path: Path, *, allow_known_defaults: bool = False) -> dict:
    """스키마의 **약속되는 부분** 전부. 이름·타입·NOT NULL·PK만 보면
    "어떤 경로로 왔든 같은 스키마"가 성립하지 않는다 (D-256) — 기본값과 FK
    동작은 다음 표 재작성에서 조용히 갈리는 자리다."""
    connection = sqlite3.connect(db_path)
    try:
        tables = {}
        foreign_keys = {}
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        for (table,) in rows:
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            described = []
            for column in columns:
                name, type_, notnull, default, pk = column[1], column[2], column[3], column[4], column[5]
                if allow_known_defaults and KNOWN_DEFAULT_DIFFERENCES.get((table, name)) == default:
                    default = None
                described.append((name, type_.upper(), notnull, default, pk))
            tables[table] = described
            # (참조 표, 자식 열, 부모 열, ON UPDATE, ON DELETE). 삭제 동작이
            # 갈리면 gc·병합의 계약이 DB마다 달라진다.
            foreign_keys[table] = sorted(
                (r[2], r[3], r[4], r[5], r[6])
                for r in connection.execute(f"PRAGMA foreign_key_list({table})")
            )
        indexes = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        return {"tables": tables, "foreign_keys": foreign_keys, "indexes": indexes}
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

    assert introspect(migrated_path, allow_known_defaults=True) == introspect(fresh_path)

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


def test_every_upgrade_path_lands_on_the_same_schema(tmp_path):
    """v1~v9 어느 판본에서 올라왔든 신규 DB와 같아야 한다 (D-256).

    v1만 재현하면 중간 판본에서 갈린 차이가 보이지 않는다 — 마이그레이션은
    한 줄씩 쌓이므로 시작점이 축이다.
    """
    from tests.unit.test_migration_with_data import build_old_db

    fresh_path = tmp_path / "fresh.db"
    Repository(fresh_path).close()
    expected = introspect(fresh_path)

    for start in range(1, SCHEMA_VERSION):
        path = tmp_path / f"from_v{start}.db"
        build_old_db(path, start)
        Repository(path).close()
        assert introspect(path, allow_known_defaults=True) == expected, (
            f"v{start}에서 올라온 DB가 신규 DB와 다르다"
        )


def test_the_migrated_default_is_never_the_value_that_gets_stored(tmp_path):
    """허용 목록의 근거를 시험으로 고정한다 (D-256).

    기본값 차이를 허용하는 유일한 이유는 **그 기본값이 쓰이는 경로가 없다**는
    것이다. 삽입 경로가 언젠가 이 열을 비우면 그 순간 마이그레이션 DB만
    조용히 빈 문자열을 저장한다 — 근거가 사라지면 허용도 사라져야 한다.
    """
    from tests.unit.test_migration_with_data import build_old_db

    path = tmp_path / "migrated.db"
    build_old_db(path, 1)
    repository = Repository(path)
    try:
        document = repository.create_document(
            url="https://example.test/a",
            original_url="https://example.test/a",
            title="t",
            now="2026-08-20T00:00:00Z",
        )
        version = repository.insert_version(
            document_id=document.id,
            text_hash="h",
            raw_hash="r",
            pipeline_version="p",
            captured_at="2026-08-20T00:00:00Z",
            byte_size=3,
            normalized_text="abc",
            http_status=200,
            observe=True,
        )
        row = sqlite3.connect(path).execute(
            "SELECT last_observed_at, last_observed_seq FROM versions WHERE id = ?",
            (version.id,),
        ).fetchone()
    finally:
        repository.close()
    assert row[0] == "2026-08-20T00:00:00Z", "기본값 ''가 실제로 저장됐다"
    assert row[1] >= 1, "기본값 0이 실제로 저장됐다"


def test_the_allowance_is_exactly_the_two_known_columns(tmp_path):
    """허용 목록이 다른 어긋남까지 덮고 있지 않은지 확인한다 (D-256).

    넓힌 `introspect`가 실제로 판별력을 가진다는 증거이기도 하다 — 허용을
    끄면 **정확히 그 두 열에서만** 갈려야 한다.
    """
    from tests.unit.test_migration_with_data import build_old_db

    fresh_path = tmp_path / "fresh.db"
    Repository(fresh_path).close()
    migrated_path = tmp_path / "migrated.db"
    build_old_db(migrated_path, 1)
    Repository(migrated_path).close()

    strict_fresh = introspect(fresh_path)
    strict_migrated = introspect(migrated_path)
    assert strict_migrated != strict_fresh, "넓힌 검사에 판별력이 없다"

    differing = {
        (table, fresh_column[0])
        for table, fresh_columns in strict_fresh["tables"].items()
        for fresh_column, migrated_column in zip(fresh_columns, strict_migrated["tables"][table])
        if fresh_column != migrated_column
    }
    assert differing == set(KNOWN_DEFAULT_DIFFERENCES)
    assert strict_migrated["foreign_keys"] == strict_fresh["foreign_keys"]
    assert strict_migrated["indexes"] == strict_fresh["indexes"]
