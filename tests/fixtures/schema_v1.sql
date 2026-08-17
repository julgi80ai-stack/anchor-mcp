-- SPDX-License-Identifier: Apache-2.0
-- Anchor 스키마 v1 (v0.1 부분집합: SPEC §4.1의 documents/versions/fetch_log
-- + robots 캐시. anchors/verifications는 v0.2에서 마이그레이션으로 추가).

-- 논리적 문서 (Memento: Original Resource / URI-R). URL 정규화 후 유일.
CREATE TABLE documents (
    id              TEXT PRIMARY KEY,          -- uuid7
    url             TEXT NOT NULL UNIQUE,      -- 정규화된 URL
    original_url    TEXT NOT NULL,             -- 리다이렉트 이전 원본
    title           TEXT,
    first_seen_at   TEXT NOT NULL,             -- ISO 8601 UTC
    last_checked_at TEXT NOT NULL,
    status          TEXT NOT NULL,             -- live | gone | forbidden | paywalled
    etag            TEXT,
    last_modified   TEXT,
    robots_allowed  INTEGER NOT NULL DEFAULT 1
);

-- 본문 스냅샷 (Memento: URI-M). text_hash가 같으면 새 버전을 만들지 않는다.
-- pipeline_version은 text_hash의 숨은 입력(추출기+정규화 규칙 버전)이다.
CREATE TABLE versions (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    text_hash        TEXT NOT NULL,               -- blake3(normalized_text)
    raw_hash         TEXT NOT NULL,               -- blake3(원본 바이트)
    pipeline_version TEXT NOT NULL,
    captured_at      TEXT NOT NULL,               -- Memento-Datetime 대응
    byte_size        INTEGER NOT NULL,
    char_count       INTEGER NOT NULL,
    content_blob     BLOB NOT NULL,               -- zstd(normalized_text)
    http_status      INTEGER NOT NULL,
    source           TEXT NOT NULL DEFAULT 'live',-- live | archive
    source_uri       TEXT,                        -- 아카이브에서 온 경우 URI-M
    UNIQUE (document_id, text_hash)
);

-- 네트워크 회계. 절감 효과 측정용.
CREATE TABLE fetch_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   TEXT NOT NULL,
    requested_at  TEXT NOT NULL,
    outcome       TEXT NOT NULL,  -- cache_hit | not_modified | unchanged | changed
                                  -- | renormalized | created | archive | error
    http_status   INTEGER,
    bytes_down    INTEGER NOT NULL DEFAULT 0,
    elapsed_ms    INTEGER NOT NULL
);

-- robots.txt 캐시 (호스트 origin별 24h). CLI 프로세스 간 공유를 위해 영속화.
CREATE TABLE robots_cache (
    origin       TEXT PRIMARY KEY,   -- 예: https://example.com
    body         TEXT NOT NULL,
    fetch_status INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL
);

CREATE INDEX idx_versions_doc  ON versions(document_id, captured_at DESC);
CREATE INDEX idx_fetchlog_time ON fetch_log(requested_at DESC);
