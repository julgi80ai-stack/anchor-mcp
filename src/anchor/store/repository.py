# SPDX-License-Identifier: Apache-2.0
"""유일한 SQL 접근 지점 (SPEC §3.1). 비즈니스 로직은 service가 갖는다."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import zstandard

from anchor.models import AnchorRecord, Document, Version, uuid7

SCHEMA_VERSION = 2
ZSTD_LEVEL = 6

# 증분 마이그레이션: {목표 버전: SQL 파일}. 신규 DB는 schema.sql 전체를 쓴다.
MIGRATION_FILES: dict[int, str] = {
    2: "migrations/0002_anchors.sql",
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
        self._connection = sqlite3.connect(db_path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._compressor = zstandard.ZstdCompressor(level=ZSTD_LEVEL)
        self._decompressor = zstandard.ZstdDecompressor()
        self._migrate()

    def close(self) -> None:
        self._connection.close()

    def _migrate(self) -> None:
        (current,) = self._connection.execute("PRAGMA user_version").fetchone()
        if current == 0:
            with self._connection:
                self._connection.executescript(_load_schema())
                self._connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
            with self._connection:
                self._connection.executescript(sql)
                self._connection.execute(f"PRAGMA user_version = {target}")

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

    def latest_version(self, document_id: str) -> Version | None:
        row = self._connection.execute(
            """SELECT * FROM versions WHERE document_id = ?
               ORDER BY captured_at DESC, id DESC LIMIT 1""",
            (document_id,),
        ).fetchone()
        return self._to_version(row) if row else None

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
        blob = self._compressor.compress(normalized_text.encode("utf-8"))
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
        version = self.latest_version(document_id)
        assert version is not None
        return version

    def get_version_text(self, version_id: str) -> str:
        row = self._connection.execute(
            "SELECT content_blob FROM versions WHERE id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"버전 없음: {version_id}")
        return self._decompressor.decompress(row["content_blob"]).decode("utf-8")

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
