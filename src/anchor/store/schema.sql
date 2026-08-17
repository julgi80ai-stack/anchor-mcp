-- SPDX-License-Identifier: Apache-2.0
-- Anchor 스키마 v5 — 신규 DB용 전체 스키마 (SPEC §4.1 + robots 캐시).
-- 기존 DB는 migrations/ 아래의 증분 SQL로 따라온다.

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
    robots_allowed  INTEGER NOT NULL DEFAULT 1,
    -- 원문이 지금 서빙하는 본문의 버전. captured_at 최대값과 다를 수 있다
    -- (아카이브 폴백, 본문 되돌림). v3에서 신설.
    current_version TEXT REFERENCES versions(id)
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
    -- 같은 본문이라도 출처가 다르면 별개의 memento다 (v5).
    UNIQUE (document_id, text_hash, source)
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

-- 리다이렉트 이전 URL → 문서. 사용자가 넘긴 URL로도 캐시를 찾게 한다 (v4).
CREATE TABLE document_aliases (
    url         TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE
);

-- robots.txt 캐시 (호스트 origin별 24h). CLI 프로세스 간 공유를 위해 영속화.
CREATE TABLE robots_cache (
    origin       TEXT PRIMARY KEY,   -- 예: https://example.com
    body         TEXT NOT NULL,
    fetch_status INTEGER NOT NULL,
    fetched_at   TEXT NOT NULL
);

-- 인용 단위 앵커 (W3C Web Annotation Selector).
CREATE TABLE anchors (
    id                TEXT PRIMARY KEY,
    document_id       TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_version   TEXT NOT NULL REFERENCES versions(id),
    exact             TEXT NOT NULL,           -- 인용문 원문 (정규화 본문 기준)
    prefix            TEXT NOT NULL,           -- 앞 컨텍스트 48자
    suffix            TEXT NOT NULL,           -- 뒤 컨텍스트 48자
    position_hint     INTEGER NOT NULL,        -- 생성 시점 문자 오프셋
    exact_hash        TEXT NOT NULL,
    quality           TEXT NOT NULL,           -- ok | short (32자 미만)
    note              TEXT,
    created_at        TEXT NOT NULL
);

-- 재검증 이력.
CREATE TABLE verifications (
    id                TEXT PRIMARY KEY,
    anchor_id         TEXT NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
    checked_version   TEXT REFERENCES versions(id),  -- GONE/UNREACHABLE이면 NULL
    checked_at        TEXT NOT NULL,
    state             TEXT NOT NULL,           -- SPEC §6.3의 7종
    match_score       REAL,                    -- 0.0 ~ 1.0
    edit_distance     INTEGER,
    found_offset      INTEGER,
    found_text        TEXT,                    -- 변형되었을 경우 실제 발견된 문자열
    elapsed_ms        INTEGER NOT NULL
);

CREATE INDEX idx_versions_doc  ON versions(document_id, captured_at DESC);
CREATE INDEX idx_fetchlog_time ON fetch_log(requested_at DESC);
CREATE INDEX idx_anchors_doc   ON anchors(document_id);
CREATE INDEX idx_verif_anchor  ON verifications(anchor_id, checked_at DESC);
CREATE INDEX idx_aliases_document ON document_aliases(document_id);
