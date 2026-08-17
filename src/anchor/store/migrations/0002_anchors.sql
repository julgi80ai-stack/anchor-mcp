-- SPDX-License-Identifier: Apache-2.0
-- v1 → v2: 앵커 엔진 도입 (SPEC §4.1 anchors / verifications).

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

CREATE INDEX idx_anchors_doc  ON anchors(document_id);
CREATE INDEX idx_verif_anchor ON verifications(anchor_id, checked_at DESC);
