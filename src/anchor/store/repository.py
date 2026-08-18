# SPDX-License-Identifier: Apache-2.0
"""유일한 SQL 접근 지점 (SPEC §3.1). 비즈니스 로직은 service가 갖는다."""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Sequence

import zstandard

from anchor.models import AnchorRecord, Document, Version, uuid7

SCHEMA_VERSION = 6
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
            if self._connection.in_transaction:
                # 앞선 실패가 트랜잭션을 남겼다(롤백 자체가 실패한 경우).
                # 정리하지 않으면 이후 모든 쓰기가 "cannot start a transaction
                # within a transaction"으로 조용히 막힌다 — D-124가 고치려던
                # 바로 그 상태가 다른 경로로 재현된다.
                self._rollback_quietly()
            self._connection.execute("BEGIN IMMEDIATE")
        except BaseException:
            self._lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is not None:
                # 원래 예외를 덮지 않는다. SQLITE_FULL이면 SQLite가 트랜잭션을
                # 스스로 폐기하므로 ROLLBACK이 "no transaction is active"로
                # 실패하는데, 그것이 "디스크가 찼다"를 가려서는 안 된다 (D-127).
                self._rollback_quietly()
            else:
                try:
                    self._connection.execute("COMMIT")
                except BaseException:
                    # COMMIT이 실패하면 트랜잭션이 열린 채 남는다. 그대로 두면
                    # 이후 모든 BEGIN IMMEDIATE가 죽어 프로세스가 사는 동안
                    # **쓰기가 영구히 막힌다** — 읽기는 되므로 조용하다 (D-124).
                    self._rollback_quietly()
                    raise
        finally:
            self._lock.release()
        return False

    def _rollback_quietly(self) -> None:
        if not self._connection.in_transaction:
            return
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def close(self) -> None:
        with self._lock:
            self._connection.close()

# 증분 마이그레이션: {목표 버전: SQL 파일}. 신규 DB는 schema.sql 전체를 쓴다.
MIGRATION_FILES: dict[int, str] = {
    2: "migrations/0002_anchors.sql",
    3: "migrations/0003_current_version.sql",
    4: "migrations/0004_document_aliases.sql",
    5: "migrations/0005_version_source_unique.sql",
    6: "migrations/0006_version_observation.sql",
}


@dataclass(frozen=True)
class RobotsEntry:
    origin: str
    body: str
    fetch_status: int
    fetched_at: str


def _load_schema() -> str:
    return resources.files("anchor.store").joinpath("schema.sql").read_text("utf-8")


_WAL_ATTEMPTS = 10
_WAL_RETRY_SECONDS = 0.05
# 잠금 대기 상한. 큰 DB의 최초 마이그레이션을 견딜 만큼 넉넉해야 한다.
_BUSY_TIMEOUT_SECONDS = 60.0
# 이보다 적게 남은 빈 페이지는 회수 비용이 이득보다 크다.
_VACUUM_MIN_FREE_PAGES = 16
# 한 번에 지우는 개수. SQLite의 바인딩 변수 상한보다 넉넉히 아래로 둔다.
_DELETE_BATCH = 400

# gc가 지워도 되는 버전의 조건. **조회와 삭제 양쪽에서 같은 조건을 쓴다** —
# 삭제 시점에 다시 확인해야 그 사이에 생긴 참조를 존중할 수 있다.
_VERSION_IS_UNREFERENCED = """
    NOT EXISTS (SELECT 1 FROM anchors a WHERE a.created_version = {ref})
    AND NOT EXISTS (SELECT 1 FROM verifications f WHERE f.checked_version = {ref})
    -- 원문이 **지금 서빙하는** 본문은 캡처 시각 최대값이 아닐 수 있다
    -- (되돌림·아카이브). 보호하지 않으면 되돌림 문서 하나가 저장소 전체의
    -- gc를 마비시킨다 (D-080).
    AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.current_version = {ref})
"""


def _enable_wal(connection: sqlite3.Connection) -> None:
    """WAL로 전환한다 (D-078/D-155).

    저널 모드 전환은 짧게 배타 잠금을 요구해, 같은 DB를 **동시에 처음 여는**
    클라이언트가 여럿이면 한쪽이 스키마 생성 트랜잭션을 쥔 사이 다른 쪽이
    `database is locked`로 죽는다(실측 확인). 저널 모드는 DB 헤더에 영속되므로
    먼저 성공한 쪽의 결과를 곧 보게 된다 — 잠깐 기다렸다 다시 물으면 된다.

    **재시도는 잠금 경합에만 한다.** 읽기 전용 DB처럼 다른 이유로 실패하는
    경우까지 재시도하면 진짜 원인을 몇 초 늦게 보여줄 뿐이고, WAL을 지원하지
    않는 저장소에서는 열 때마다 그 대기를 문다. PRAGMA가 예외 없이 다른 모드를
    돌려주면 그 환경의 사실로 받아들이고 그대로 진행한다.
    """
    for attempt in range(_WAL_ATTEMPTS):
        try:
            row = connection.execute("PRAGMA journal_mode = WAL").fetchone()
        except sqlite3.OperationalError as error:
            if "locked" not in str(error) and "busy" not in str(error).lower():
                raise  # 경합이 아닌 실패는 그대로 보고한다 (예: 읽기 전용 DB)
            time.sleep(_WAL_RETRY_SECONDS * (attempt + 1))
            continue
        if row is not None and row[0] != "wal":
            return  # 이 저장소가 WAL을 지원하지 않는다 — 재시도해도 같다
        return


def _split_statements(sql: str) -> list[str]:
    """SQL 스크립트를 실행 가능한 문장으로 나눈다 (D-079).

    `sql.split(";")`는 문자열 리터럴·`--` 주석·트리거 본문(`BEGIN … END;`)
    안의 세미콜론에서 문장을 깨뜨린다. 이 저장소는 `-- live | gone | forbidden`
    같은 열거 주석을 관행적으로 쓰므로, 거기에 `;`가 하나 들어가는 순간
    마이그레이션이 통째로 실패한다.

    판정은 `sqlite3.complete_statement`(sqlite3_complete())에 맡긴다 — 셋을
    모두 안다. **`;`를 만날 때마다** 물어보므로 한 줄에 문장이 여럿이어도
    쪼갠다(줄 단위로 물으면 `execute`가 "one statement at a time"으로 거절한다).
    """
    statements: list[str] = []
    start = 0
    for index, char in enumerate(sql):
        if char != ";":
            continue
        candidate = sql[start : index + 1]
        if sqlite3.complete_statement(candidate):
            statements.append(candidate.strip())
            start = index + 1
    tail = sql[start:].strip()
    if tail:  # 마지막 문장 뒤에 남은 주석 등 — 실행해도 무해하다
        statements.append(tail)
    return [statement for statement in statements if statement]


class Repository:
    def __init__(self, db_path: Path | str) -> None:
        db_path = Path(db_path).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        # 기본 busy timeout(5초)은 v5의 표 재작성처럼 오래 걸리는 마이그레이션을
        # 넘기지 못한다 — 큰 DB(실측 1.4GB에서 34초)를 동시에 열면 기다리던
        # 프로세스가 `database is locked`로 죽는다. 업그레이드는 한 번뿐이고
        # 그때는 기다리는 편이 옳다 (D-078).
        raw = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,
            timeout=_BUSY_TIMEOUT_SECONDS,
        )
        raw.row_factory = sqlite3.Row
        _enable_wal(raw)
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

    def _apply_sql_atomically(
        self, sql: str, target_version: int, *, expected_version: int
    ) -> None:
        """스키마 SQL과 버전 표시를 한 트랜잭션으로 적용한다 (D-019).

        `executescript`는 대기 중인 트랜잭션을 암시적으로 COMMIT하고 각 문장을
        autocommit으로 실행하므로 원자성을 주지 못한다. 적용 도중 중단되면
        테이블 일부만 생성된 채 `user_version`이 갱신되지 않아 이후 DB를 영영
        열 수 없게 되므로, 문장 단위로 나눠 명시적 트랜잭션 안에서 실행한다.
        `PRAGMA user_version`은 DB 헤더에 기록되며 트랜잭션에 포함된다(실측 확인).

        버전 확인을 트랜잭션 **안에서** 다시 한다 (D-078). 밖에서 읽고 안에서
        적용하면 두 클라이언트가 같은 버전을 보고 둘 다 적용을 시도해, 뒤늦은
        쪽이 이미 만들어진 객체를 다시 만들며 죽는다. 이미 올라가 있으면
        아무것도 하지 않는다 — 호출자가 다시 읽어 이어간다.
        """
        with self._connection as connection:
            (observed,) = connection.execute("PRAGMA user_version").fetchone()
            if observed != expected_version:
                return  # 다른 클라이언트가 먼저 적용했다
            for statement in _split_statements(sql):
                connection.execute(statement)
            # FK를 끈 구간이므로 커밋 전에 직접 검사한다 (SQLite 공식 ALTER 절차).
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"마이그레이션 v{target_version} 적용 후 참조 무결성 위반: "
                    f"{[tuple(row) for row in violations[:5]]}"
                )
            connection.execute(f"PRAGMA user_version = {target_version}")

    def _migrate(self) -> None:
        """스키마를 현재 버전까지 올린다.

        적용 구간에서만 FK를 내린다 (D-077). 표의 제약을 바꾸려면 표를 다시
        만들어야 하는데(0005), FK가 켜져 있으면 `DROP TABLE versions`가
        `documents.current_version`·`anchors.created_version`·
        `verifications.checked_version`을 즉시 위반해 **행이 하나라도 있는 모든
        구버전 DB가 영구히 열리지 않게 된다.** `PRAGMA foreign_keys`는
        트랜잭션 안에서 no-op이므로(실측 확인) 반드시 트랜잭션 밖에서 내리고,
        대신 커밋 직전에 `foreign_key_check`로 검사한다.
        """
        self._connection.execute("PRAGMA foreign_keys = OFF")
        try:
            # 동시 오픈 시 다른 클라이언트가 올려놓는 경우가 있어 매번 다시 읽는다.
            for _ in range(2 * SCHEMA_VERSION + 4):
                (current,) = self._connection.execute("PRAGMA user_version").fetchone()
                if current > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"DB 스키마 버전 {current}이 코드가 아는 버전 {SCHEMA_VERSION}보다 높습니다"
                    )
                if current == SCHEMA_VERSION:
                    return
                if current == 0:
                    self._apply_sql_atomically(
                        _load_schema(), SCHEMA_VERSION, expected_version=0
                    )
                    continue
                sql = (
                    resources.files("anchor.store")
                    .joinpath(MIGRATION_FILES[current + 1])
                    .read_text("utf-8")
                )
                self._apply_sql_atomically(sql, current + 1, expected_version=current)
            raise RuntimeError("스키마 마이그레이션이 진행되지 않았습니다")
        finally:
            self._connection.execute("PRAGMA foreign_keys = ON")

    # -- documents ---------------------------------------------------------

    def get_document_by_url(self, url: str) -> Document | None:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE url = ?", (url,)
        ).fetchone()
        return self._to_document(row) if row else None

    def get_document_by_any_url(self, url: str) -> Document | None:
        """정규화된 URL 또는 리다이렉트 이전 별칭으로 문서를 찾는다 (D-007)."""
        document = self.get_document_by_url(url)
        if document is not None:
            return document
        row = self._connection.execute(
            """SELECT d.* FROM document_aliases a JOIN documents d ON d.id = a.document_id
               WHERE a.url = ?""",
            (url,),
        ).fetchone()
        return self._to_document(row) if row else None

    def add_alias(self, url: str, document_id: str) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT OR REPLACE INTO document_aliases (url, document_id) VALUES (?, ?)",
                (url, document_id),
            )

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
            # 커밋 뒤 락 밖에서 재조회하면 그 틈에 gc가 지운 경우 `assert`가
            # 터져, 성공적으로 기록된 행이 실패로 보인다 (D-082).
            row = self._connection.execute(
                "SELECT * FROM documents WHERE id = ?", (document_id,)
            ).fetchone()
        return self._to_document(row)

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

    def clear_validators(self, document_id: str) -> None:
        """조건부 요청용 검증자를 버린다 (D-087).

        아카이브 판본을 현재 본문으로 삼는 동안 원본의 etag를 들고 있으면,
        원본이 되살아났을 때 그 etag로 304를 받아 아카이브에 고착된다.
        """
        with self._connection:
            self._connection.execute(
                "UPDATE documents SET etag = NULL, last_modified = NULL WHERE id = ?",
                (document_id,),
            )

    def reuse_and_point(self, document_id: str, text_hash: str, source: str = "live"):
        """같은 본문의 기존 버전을 찾아 **한 트랜잭션에서** 현재 버전으로 가리킨다.

        찾기와 가리키기가 갈라져 있으면 그 사이에 gc가 그 행을 지울 수 있고,
        `UPDATE`가 FK로 죽어 맨 `sqlite3.IntegrityError`가 호출자에게 올라간다
        (D-177). 사라졌으면 None을 돌려 호출자가 새로 넣게 한다.
        """
        with self._connection:
            row = self._connection.execute(
                "SELECT * FROM versions WHERE document_id = ? AND text_hash = ? AND source = ?",
                (document_id, text_hash, source),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "UPDATE documents SET current_version = ? WHERE id = ?",
                (row["id"], document_id),
            )
            return self._to_version(row)

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

    def observe_version(self, document_id: str, version_id: str, observed_at: str) -> None:
        """원문에서 이 본문을 관측했다 — 포인터와 **관측 시각**을 함께 남긴다.

        본문 해시로 중복을 제거하면 관측의 시간축이 접힌다. 되돌림에서
        "직전에 서빙되던 판본"은 이 값으로만 알 수 있다 (D-083). 포인터가
        그대로여도(같은 본문을 다시 관측) 시각은 갱신한다 — 관측은 일어났다.
        """
        with self._connection:
            self._connection.execute(
                """UPDATE versions SET last_observed_at = ?,
                       last_observed_seq = (SELECT COALESCE(MAX(last_observed_seq), 0) + 1
                                            FROM versions WHERE document_id = ?)
                   WHERE id = ?""",
                (observed_at, document_id, version_id),
            )
            self._connection.execute(
                "UPDATE documents SET current_version = ? WHERE id = ?",
                (version_id, document_id),
            )

    def list_versions_by_observation(self, document_id: str) -> list[Version]:
        """관측 최신순 — `latest~N`의 좌표계 (D-083).

        `list_versions`(캡처 시각 오름차순)는 TimeMap의 순서다. 둘은 다르며,
        섞으면 일어난 적 없는 전이를 보여준다.
        """
        rows = self._connection.execute(
            """SELECT * FROM versions WHERE document_id = ?
               ORDER BY last_observed_seq DESC, captured_at DESC, id DESC""",
            (document_id,),
        ).fetchall()
        return [self._to_version(row) for row in rows]

    def set_current_version(self, document_id: str, version_id: str) -> None:
        """포인터만 옮긴다 (마이그레이션·복구용). 관측은 observe_version이다."""
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

    def find_version_by_text_hash(
        self, document_id: str, text_hash: str, *, source: str = "live"
    ) -> Version | None:
        """본문 해시로 기존 버전을 찾는다. 출처가 다르면 별개의 memento다."""
        row = self._connection.execute(
            "SELECT * FROM versions WHERE document_id = ? AND text_hash = ? AND source = ?",
            (document_id, text_hash, source),
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
                    last_observed_at, last_observed_seq, byte_size, char_count,
                    content_blob, http_status, source, source_uri)
                   VALUES (?, ?, ?, ?, ?, ?, ?,
                           (SELECT COALESCE(MAX(last_observed_seq), 0) + 1 FROM versions
                            WHERE document_id = ?),
                           ?, ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    document_id,
                    text_hash,
                    raw_hash,
                    pipeline_version,
                    captured_at,
                    captured_at,
                    document_id,
                    byte_size,
                    len(normalized_text),
                    blob,
                    http_status,
                    source,
                    source_uri,
                ),
            )
            row = self._connection.execute(
                "SELECT * FROM versions WHERE id = ?", (version_id,)
            ).fetchone()
        return self._to_version(row)

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
        # 대상 선정은 **락 밖에서** 한다. 이 조회는 문서당 버전 수에 제곱이라
        # (16,000 버전에서 20초 실측), 트랜잭션 안에 두면 그 시간만큼 쓰기 락을
        # 붙잡아 MCP 서버의 모든 쓰기가 멈춘다 — SPEC §10 동시성 격리 위반.
        # 순위는 **윈도 함수**로 매긴다. 문서별 상관 서브쿼리는 버전 수에 제곱이라
        # (8,000 버전 한 문서에서 3.4초, 16,000에서 20초 실측) 그 시간만큼 저장소가
        # 붙잡혀 다른 도구가 전부 멈춘다.
        rows = self._connection.execute(
            f"""SELECT id, size FROM (
                  SELECT id, LENGTH(content_blob) AS size,
                         ROW_NUMBER() OVER (
                           PARTITION BY document_id ORDER BY captured_at DESC, id DESC
                         ) AS rank_in_document
                  FROM versions
                ) ranked
                WHERE rank_in_document > ?
                  AND {_VERSION_IS_UNREFERENCED.format(ref="ranked.id")}""",
            (keep,),
        ).fetchall()
        if not rows:
            self._vacuum_if_fragmented(deleted=0)
            return 0, 0

        # 삭제할 때 보존 조건을 **다시 확인**한다. 조회와 삭제 사이에 누군가
        # 그 버전을 참조하면(cite·현재 버전 갱신) 그 행만 조용히 빠질 뿐,
        # 삭제 전체가 FK 위반으로 롤백되지 않는다 (D-081).
        deleted = 0
        freed = 0
        for start in range(0, len(rows), _DELETE_BATCH):
            batch = rows[start : start + _DELETE_BATCH]
            placeholders = ",".join("?" * len(batch))
            with self._connection as connection:
                connection.execute(
                    f"""DELETE FROM versions
                        WHERE id IN ({placeholders})
                          AND {_VERSION_IS_UNREFERENCED.format(ref="versions.id")}""",
                    [row[0] for row in batch],
                )
                (changed,) = connection.execute("SELECT changes()").fetchone()
            deleted += changed
            if changed:
                freed += sum(row[1] for row in batch) * changed // len(batch)
        self._vacuum_if_fragmented(deleted=deleted)
        return deleted, freed

    def _vacuum_if_fragmented(self, *, deleted: int) -> None:
        """공간을 회수한다.

        VACUUM은 DB 전체를 다시 쓴다. 저장소 락을 쥔 채 돌리면 같은 프로세스의
        읽기 전용 도구가 그 시간만큼 멈추므로(840MB에서 27초 실측) **별도
        커넥션**에서 돌려 락 밖에 둔다 (D-126). 삭제가 0건이어도 확인한다 —
        마이그레이션의 표 재작성이 남긴 빈 페이지도 여기서 회수된다 (D-159).
        """
        (free_pages,) = self._connection.execute("PRAGMA freelist_count").fetchone()
        (total_pages,) = self._connection.execute("PRAGMA page_count").fetchone()
        # 실제로 지웠으면 회수한다 — 사용자가 명시적으로 `anchor gc`를 부른
        # 것이고, "회수 추정 N bytes"를 보고했는데 파일이 그대로면 보고와
        # 실제가 어긋난다. 삭제가 0건이어도 빈 페이지가 크게 쌓였으면(표
        # 재작성 직후) 정리한다 (D-159).
        fragmented = free_pages >= _VACUUM_MIN_FREE_PAGES and free_pages * 4 >= total_pages
        if not deleted and not fragmented:
            return
        connection = sqlite3.connect(
            self._db_path, timeout=_BUSY_TIMEOUT_SECONDS, isolation_level=None
        )
        try:
            connection.execute("VACUUM")
        except sqlite3.OperationalError:
            pass  # 다른 프로세스가 쓰는 중이면 다음 기회에
        finally:
            connection.close()

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

    def latest_verified_version(self, anchor_id: str) -> tuple[str, str | None] | None:
        """가장 최근 검증의 (시각, 검증한 버전 id). GONE/UNREACHABLE이면 버전은 None."""
        row = self._connection.execute(
            """SELECT checked_at, checked_version FROM verifications WHERE anchor_id = ?
               ORDER BY checked_at DESC, id DESC LIMIT 1""",
            (anchor_id,),
        ).fetchone()
        return (row["checked_at"], row["checked_version"]) if row else None

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
            row = self._connection.execute(
                "SELECT * FROM anchors WHERE id = ?", (anchor_id,)
            ).fetchone()
        return self._to_anchor(row)

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
        # 빈 리스트는 "선택 없음"이다. `None`(전체)과 구별하지 않으면 0건
        # 요청이 전건 재검증으로 증폭된다 (D-022).
        if anchor_ids is not None:
            if not anchor_ids:
                return []
            clauses.append(f"a.id IN ({','.join('?' * len(anchor_ids))})")
            params.extend(anchor_ids)
        if document_ids is not None:
            if not document_ids:
                return []
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
            last_observed_at=row["last_observed_at"],
            last_observed_seq=row["last_observed_seq"],
            byte_size=row["byte_size"],
            char_count=row["char_count"],
            http_status=row["http_status"],
            source=row["source"],
            source_uri=row["source_uri"],
        )
