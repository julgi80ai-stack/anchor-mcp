-- SPDX-License-Identifier: Apache-2.0
-- v4 → v5: 같은 본문이라도 출처가 다르면 별개의 버전으로 둔다 (D-013).
--
-- `UNIQUE (document_id, text_hash)` 때문에, 아카이브에서 되살린 본문이
-- 기존 live 버전과 같으면 그 행을 재사용할 수밖에 없었고 그 순간
-- source='archive'·source_uri·Memento 시각이 전부 사라졌다. Memento
-- 관점에서 둘은 서로 다른 스냅샷이므로 제약에 source를 포함한다.
--
-- SQLite는 제약을 직접 바꿀 수 없어 표를 다시 만든다 (공식 ALTER 절차).

CREATE TABLE versions_new (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    text_hash        TEXT NOT NULL,
    raw_hash         TEXT NOT NULL,
    pipeline_version TEXT NOT NULL,
    captured_at      TEXT NOT NULL,
    byte_size        INTEGER NOT NULL,
    char_count       INTEGER NOT NULL,
    content_blob     BLOB NOT NULL,
    http_status      INTEGER NOT NULL,
    source           TEXT NOT NULL DEFAULT 'live',
    source_uri       TEXT,
    UNIQUE (document_id, text_hash, source)
);

INSERT INTO versions_new SELECT
    id, document_id, text_hash, raw_hash, pipeline_version, captured_at,
    byte_size, char_count, content_blob, http_status, source, source_uri
FROM versions;

DROP TABLE versions;

ALTER TABLE versions_new RENAME TO versions;

CREATE INDEX idx_versions_doc ON versions(document_id, captured_at DESC);
