# SPDX-License-Identifier: Apache-2.0
"""유일한 SQL 접근 지점 (SPEC §3.1). 비즈니스 로직은 service가 갖는다."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Sequence

import zstandard

from anchor.models import AnchorRecord, Document, Version, uuid7

SCHEMA_VERSION = 3
ZSTD_LEVEL = 6


class _Rows:
    """락을 놓기 전에 구체화한 조회 결과. 커서를 밖으로 내보내지 않는다."""

    __slots__ = ("_rows",)

    def __init__(self, rows: list[sqlite3.Row]) -> None:
        self._rows = rows

    def fetchone(self) -> sqlite3.Row | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[sqlite3.Row]:
        return self._rows


class _SerializedConnection:
    """모든 SQL 실행을 재진입 락으로 직렬화하는 커넥션 래퍼 (D-020/D-023).

    `sqlite3.threadsafety == 3`은 커넥션 자체를 보호하지만 두 가지를 보호하지
    않는다: ① `with connection:` 트랜잭션은 커넥션 전역이라 스레드별 격리가
    없어 한 스레드의 롤백이 다른 스레드의 커밋에 무효화된다 ② 커서를 밖으로
    돌려주면 다음 스레드가 그 위를 덮어쓴다. 여기서 트랜잭션을 명시적으로
    열고(`isolation_level=None`), 결과를 락 안에서 구체화한다.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock = threading.RLock()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Rows:
        with self._lock:
            return _Rows(self._connection.execute(sql, params).fetchall())

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> None:
        with self._lock:
            self._connection.executemany(sql, seq)

    def __enter__(self) -> _SerializedConnection:
        self._lock.acquire()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            self._connection.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self._lock.release()
        return False

    def close(self) -> None:
        with self._lock:
            self._connection.close()

# 증분 마이그레이션: {목표 버전: SQL 파일}. 신규 DB는 schema.sql 전체를 쓴다.
MIGRATION_FILES: dict[int, str] = {
    2: "migrations/0002_anchors.sql",
    3: "migrations/0003_current_version.sql",
}


@dataclass(frozen=True)
class RobotsEntry:
    origin: str
    body: str
    fetch_status: int
    fetched_at: str


def _load_schema() -> str:
    return resources.files("anchor.store").joinpath("schema.sql").read_text("utf-8")


class Repository:
    def __init__(self, db_path: Path | str) -> None:
        db_path = Path(db_path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        raw = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA journal_mode = WAL")
        raw.execute("PRAGMA foreign_keys = ON")
        # 압축기 인스턴스는 스레드 안전하지 않아 공유하면 segfault가 난다.
        # 호출마다 만든다 — 실측 비용 +0.016ms (D-020).
        self._connection = _SerializedConnection(raw)
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def disk_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self._db_path) + suffix)
            if candidate.exists():
                total += candidate.stat().st_size
        return total

    def _apply_sql_atomically(self, sql: str, target_version: int) -> None:
        """스키마 SQL과 버전 표시를 한 트랜잭션으로 적용한다 (D-019).

        `executescript`는 대기 중인 트랜잭션을 암시적으로 COMMIT하고 각 문장을
        autocommit으로 실행하므로 원자성을 주지 못한다. 적용 도중 중단되면
        테이블 일부만 생성된 채 `user_version`이 갱신되지 않아 이후 DB를 영영
        열 수 없게 되므로, 문장 단위로 나눠 명시적 트랜잭션 안에서 실행한다.
        `PRAGMA user_version`은 DB 헤더에 기록되며 트랜잭션에 포함된다(실측 확인).
        """
        statements = [part.strip() for part in sql.split(";") if part.strip()]
        with self._connection as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {target_version}")

    def _migrate(self) -> None:
        (current,) = self._connection.execute("PRAGMA user_version").fetchone()
        if current == 0:
            self._apply_sql_atomically(_load_schema(), SCHEMA_VERSION)
            return
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"DB 스키마 버전 {current}이 코드가 아는 버전 {SCHEMA_VERSION}보다 높습니다"
            )
        for target in range(current + 1, SCHEMA_VERSION + 1):
            sql = (
                resources.files("anchor.store")
                .joinpath(MIGRATION_FILES[target])
                .read_text("utf-8")
            )
            self._apply_sql_atomically(sql, target)

    # -- documents ---------------------------------------------------------

    def get_document_by_url(self, url: str) -> Document | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE url = ?", (url,)
        ).fetchone()
        return self._to_document(row) if row else None

    def get_document(self, document_id: str) -> Document | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        ).fetchone()
        return self._to_document(row) if row else None

    def create_document(
        self,
        *,
        url: str,
        original_url: str,
        title: str | None,
        now: str,
        status: str = "live",
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> Document:
        document_id = uuid7()
        with self._connection:
            self._connection.execute(
                """INSERT INTO documents
                   (id, url, original_url, title, first_seen_at, last_checked_at,
                    status, etag, last_modified, robots_allowed)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (document_id, url, original_url, title, now, now, status, etag, last_modified),
            )
        return self.get_document_by_url(url)  # type: ignore[return-value]

    def update_document_checked(
        self,
        document_id: str,
        *,
        now: str,
        status: str,
        etag: str | None,
        last_modified: str | None,
        title: str | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """UPDATE documents
                   SET last_checked_at = ?, status = ?, etag = ?, last_modified = ?,
                       title = COALESCE(?, title)
                   WHERE id = ?""",
                (now, status, etag, last_modified, title, document_id),
            )

    def set_document_status(self, document_id: str, status: str, now: str) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE documents SET status = ?, last_checked_at = ? WHERE id = ?",
                (status, now, document_id),
            )

    def set_robots_allowed(self, document_id: str, allowed: bool) -> None:
        with self._connection:
            self._connection.execute(
                "UPDATE documents SET robots_allowed = ? WHERE id = ?",
                (1 if allowed else 0, document_id),
            )

    def list_documents(self) -> list[Document]:
        rows = self._connection.execute(
            "SELECT * FROM documents ORDER BY last_checked_at DESC"
        ).fetchall()
        return [self._to_document(row) for row in rows]

    # -- versions ----------------------------------------------------------

    def set_current_version(self, document_id: str, version_id: str) -> None:
        """원문을 관측할 때마다 갱신한다 — "지금 서빙되는 본문"의 포인터."""
        with self._connection:
            self._connection.execute(
                "UPDATE documents SET current_version = ? WHERE id = ?",
                (version_id, document_id),
            )

    def current_version(self, document_id: str) -> Version | None:
        """원문의 현재 본문에 해당하는 버전 (D-011/D-012/D-024).

        포인터가 비어 있으면(구 데이터) 캡처 시각 최대값으로 물러선다.
        """
        row = self._connection.execute(
            """SELECT v.* FROM documents d JOIN versions v ON v.id = d.current_version
               WHERE d.id = ?""",
            (document_id,),
        ).fetchone()
        if row is not None:
            return self._to_version(row)
        return self.latest_version(document_id)

    def latest_version(self, document_id: str) -> Version | None:
        """캡처 시각이 가장 늦은 버전. TimeMap의 시간순 열거 기준이며,
        "현재 원문"과는 다를 수 있다 — 그쪽은 `current_version`을 쓴다."""
        row = self._connection.execute(
            """SELECT * FROM versions WHERE document_id = ?
               ORDER BY captured_at DESC, id DESC LIMIT 1""",
            (document_id,),
        ).fetchone()
        return self._to_version(row) if row else None

    def get_version(self, version_id: str) -> Version | None:
        row = self._connection.execute(
            "SELECT * FROM versions WHERE id = ?", (version_id,)
        ).fetchone()
        return self._to_version(row) if row else None

    def list_versions(self, document_id: str) -> list[Version]:
        """captured_at 오름차순 — TimeMap 직렬화 순서와 일치."""
        rows = self._connection.execute(
            "SELECT * FROM versions WHERE document_id = ? ORDER BY captured_at ASC, id ASC",
            (document_id,),
        ).fetchall()
        return [self._to_version(row) for row in rows]

    def find_version_by_text_hash(self, document_id: str, text_hash: str) -> Version | None:
        row = self._connection.execute(
            "SELECT * FROM versions WHERE document_id = ? AND text_hash = ?",
            (document_id, text_hash),
        ).fetchone()
        return self._to_version(row) if row else None

    def insert_version(
        self,
        *,
        document_id: str,
        text_hash: str,
        raw_hash: str,
        pipeline_version: str,
        captured_at: str,
        byte_size: int,
        normalized_text: str,
        http_status: int,
        source: str = "live",
        source_uri: str | None = None,
    ) -> Version:
        version_id = uuid7()
        blob = zstandard.ZstdCompressor(level=ZSTD_LEVEL).compress(
            normalized_text.encode("utf-8")
        )
        with self._connection:
            self._connection.execute(
                """INSERT INTO versions
                   (id, document_id, text_hash, raw_hash, pipeline_version, captured_at,
                    byte_size, char_count, content_blob, http_status, source, source_uri)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    document_id,
                    text_hash,
                    raw_hash,
                    pipeline_version,
                    captured_at,
                    byte_size,
                    len(normalized_text),
                    blob,
                    http_status,
                    source,
                    source_uri,
                ),
            )
        version = self.get_version(version_id)
        assert version is not None
        return version

    def get_version_text(self, version_id: str) -> str:
        row = self._connection.execute(
            "SELECT content_blob FROM versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"version not found — 버전 없음: {version_id}")
        return zstandard.ZstdDecompressor().decompress(row["content_blob"]).decode("utf-8")

    # -- fetch_log ---------------------------------------------------------

    def log_fetch(
        self,
        *,
        document_id: str,
        requested_at: str,
        outcome: str,
        http_status: int | None,
        bytes_down: int,
        elapsed_ms: int,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """INSERT INTO fetch_log
                   (document_id, requested_at, outcome, http_status, bytes_down, elapsed_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (document_id, requested_at, outcome, http_status, bytes_down, elapsed_ms),
            )

    # -- gc ----------------------------------------------------------------

    def collect_garbage_versions(self, *, keep: int = 20) -> tuple[int, int]:
        """문서당 최근 keep개를 넘는 고아 버전을 삭제한다 (SPEC §4.2).

        앵커가 가리키는 버전은 절대 삭제하지 않는다. 검증 이력이 참조하는
        버전도 FK 무결성과 감사 추적을 위해 보존한다 (스펙의 최소 보존
        규칙보다 넓게 남기는 것은 안전한 방향이다).

        반환: (삭제된 버전 수, 회수된 blob 바이트 추정치)
        """
        rows = self._connection.execute(
            """SELECT id, LENGTH(content_blob) FROM versions v
               WHERE (
                 SELECT COUNT(*) FROM versions newer
                 WHERE newer.document_id = v.document_id
                   AND (newer.captured_at > v.captured_at
                        OR (newer.captured_at = v.captured_at AND newer.id > v.id))
               ) >= ?
               AND NOT EXISTS (SELECT 1 FROM anchors a WHERE a.created_version = v.id)
               AND NOT EXISTS (SELECT 1 FROM verifications f WHERE f.checked_version = v.id)""",
            (keep,),
        ).fetchall()
        if not rows:
            return 0, 0
        ids = [row[0] for row in rows]
        freed = sum(row[1] for row in rows)
        with self._connection:
            self._connection.executemany("DELETE FROM versions WHERE id = ?", [(i,) for i in ids])
        self._connection.execute("VACUUM")
        return len(ids), freed

    # -- stats -------------------------------------------------------------

    def count_rows(self) -> dict[str, int]:
        counts = {}
        for table in ("documents", "versions", "anchors"):
            (counts[table],) = self._connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()
        return counts

    def fetch_stats_since(self, since_iso: str) -> dict[str, int]:
        """fetch_log 집계: outcome별 건수 + 총 다운로드 바이트."""
        rows = self._connection.execute(
            """SELECT outcome, COUNT(*), SUM(bytes_down) FROM fetch_log
               WHERE requested_at >= ? GROUP BY outcome""",
            (since_iso,),
        ).fetchall()
        stats: dict[str, int] = {"requests": 0, "bytes_down": 0}
        for outcome, count, bytes_down in rows:
            stats[outcome] = count
            stats["requests"] += count
            stats["bytes_down"] += bytes_down or 0
        return stats

    def bytes_saved_estimate_since(self, since_iso: str) -> int:
        """cache_hit·not_modified가 아니었다면 내려받았을 바이트의 추정치.

        각 이벤트 시점의 정확한 크기는 남아 있지 않으므로 해당 문서의
        최신 버전 byte_size로 근사한다.
        """
        (total,) = self._connection.execute(
            """SELECT COALESCE(SUM(latest.byte_size), 0)
               FROM fetch_log f
               JOIN (
                 SELECT document_id, byte_size,
                        ROW_NUMBER() OVER (
                          PARTITION BY document_id ORDER BY captured_at DESC, id DESC
                        ) AS rn
                 FROM versions
               ) latest ON latest.document_id = f.document_id AND latest.rn = 1
               WHERE f.requested_at >= ? AND f.outcome IN ('cache_hit', 'not_modified')""",
            (since_iso,),
        ).fetchone()
        return int(total)

    def latest_verification_time(self, anchor_id: str) -> str | None:
        row = self._connection.execute(
            """SELECT checked_at FROM verifications WHERE anchor_id = ?
               ORDER BY checked_at DESC LIMIT 1""",
            (anchor_id,),
        ).fetchone()
        return row["checked_at"] if row else None

    # -- anchors -----------------------------------------------------------

    def insert_anchor(
        self,
        *,
        document_id: str,
        created_version: str,
        exact: str,
        prefix: str,
        suffix: str,
        position_hint: int,
        exact_hash: str,
        quality: str,
        note: str | None,
        created_at: str,
    ) -> AnchorRecord:
        anchor_id = uuid7()
        with self._connection:
            self._connection.execute(
                """INSERT INTO anchors
                   (id, document_id, created_version, exact, prefix, suffix,
                    position_hint, exact_hash, quality, note, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    anchor_id,
                    document_id,
                    created_version,
                    exact,
                    prefix,
                    suffix,
                    position_hint,
                    exact_hash,
                    quality,
                    note,
                    created_at,
                ),
            )
        anchor = self.get_anchor(anchor_id)
        assert anchor is not None
        return anchor

    def get_anchor(self, anchor_id: str) -> AnchorRecord | None:
        row = self._connection.execute(
            "SELECT * FROM anchors WHERE id = ?", (anchor_id,)
        ).fetchone()
        return self._to_anchor(row) if row else None

    def select_anchors(
        self,
        *,
        anchor_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        not_verified_since: str | None = None,
    ) -> list[AnchorRecord]:
        """검증 대상 앵커를 고른다. 조건이 모두 None이면 전체.

        not_verified_since: 이 시각 이후의 검증 기록이 없는 앵커만
        (한 번도 검증되지 않은 앵커 포함).
        """
        clauses: list[str] = []
        params: list[object] = []
        if anchor_ids:
            clauses.append(f"a.id IN ({','.join('?' * len(anchor_ids))})")
            params.extend(anchor_ids)
        if document_ids:
            clauses.append(f"a.document_id IN ({','.join('?' * len(document_ids))})")
            params.extend(document_ids)
        if not_verified_since is not None:
            clauses.append(
                """NOT EXISTS (
                     SELECT 1 FROM verifications v
                     WHERE v.anchor_id = a.id AND v.checked_at > ?
                   )"""
            )
            params.append(not_verified_since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._connection.execute(
            f"SELECT a.* FROM anchors a {where} ORDER BY a.created_at", params
        ).fetchall()
        return [self._to_anchor(row) for row in rows]

    # -- verifications -----------------------------------------------------

    def insert_verification(
        self,
        *,
        anchor_id: str,
        checked_version: str | None,
        checked_at: str,
        state: str,
        match_score: float | None,
        edit_distance: int | None,
        found_offset: int | None,
        found_text: str | None,
        elapsed_ms: int,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """INSERT INTO verifications
                   (id, anchor_id, checked_version, checked_at, state, match_score,
                    edit_distance, found_offset, found_text, elapsed_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid7(),
                    anchor_id,
                    checked_version,
                    checked_at,
                    state,
                    match_score,
                    edit_distance,
                    found_offset,
                    found_text,
                    elapsed_ms,
                ),
            )

    # -- robots_cache ------------------------------------------------------

    def get_robots(self, origin: str) -> RobotsEntry | None:
        row = self._connection.execute(
            "SELECT * FROM robots_cache WHERE origin = ?", (origin,)
        ).fetchone()
        if row is None:
            return None
        return RobotsEntry(
            origin=row["origin"],
            body=row["body"],
            fetch_status=row["fetch_status"],
            fetched_at=row["fetched_at"],
        )

    def set_robots(self, origin: str, body: str, fetch_status: int, fetched_at: str) -> None:
        with self._connection:
            self._connection.execute(
                """INSERT INTO robots_cache (origin, body, fetch_status, fetched_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(origin) DO UPDATE SET
                     body = excluded.body,
                     fetch_status = excluded.fetch_status,
                     fetched_at = excluded.fetched_at""",
                (origin, body, fetch_status, fetched_at),
            )

    # -- row mapping -------------------------------------------------------

    @staticmethod
    def _to_document(row: sqlite3.Row) -> Document:
        return Document(
            id=row["id"],
            url=row["url"],
            original_url=row["original_url"],
            title=row["title"],
            first_seen_at=row["first_seen_at"],
            last_checked_at=row["last_checked_at"],
            status=row["status"],
            etag=row["etag"],
            last_modified=row["last_modified"],
            robots_allowed=bool(row["robots_allowed"]),
            current_version=row["current_version"],
        )

    @staticmethod
    def _to_anchor(row: sqlite3.Row) -> AnchorRecord:
        return AnchorRecord(
            id=row["id"],
            document_id=row["document_id"],
            created_version=row["created_version"],
            exact=row["exact"],
            prefix=row["prefix"],
            suffix=row["suffix"],
            position_hint=row["position_hint"],
            exact_hash=row["exact_hash"],
            quality=row["quality"],
            note=row["note"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _to_version(row: sqlite3.Row) -> Version:
        return Version(
            id=row["id"],
            document_id=row["document_id"],
            text_hash=row["text_hash"],
            raw_hash=row["raw_hash"],
            pipeline_version=row["pipeline_version"],
            captured_at=row["captured_at"],
            byte_size=row["byte_size"],
            char_count=row["char_count"],
            http_status=row["http_status"],
            source=row["source"],
            source_uri=row["source_uri"],
        )
