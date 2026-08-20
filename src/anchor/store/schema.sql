-- SPDX-License-Identifier: Apache-2.0
-- Anchor 스키마 v10 — 신규 DB용 전체 스키마 (SPEC §4.1 + robots 캐시).
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
    captured_at      TEXT NOT NULL,               -- Memento-Datetime 대응 (처음 캡처된 때)
    byte_size        INTEGER NOT NULL,
    char_count       INTEGER NOT NULL,
    content_blob     BLOB NOT NULL,               -- zstd(normalized_text)
    http_status      INTEGER NOT NULL,
    source           TEXT NOT NULL DEFAULT 'live',-- live | archive
    source_uri       TEXT,                        -- 아카이브에서 온 경우 URI-M
    -- 이 본문이 원문에서 **마지막으로 관측된** 때와 그 순서 (v6). 본문 해시로
    -- 중복을 제거하면 관측의 시간축이 접히므로, 되돌림에서 "직전에 서빙되던
    -- 판본"을 이 값으로만 알 수 있다. captured_at은 Memento-Datetime이라 못
    -- 바꾼다. 시각은 사람에게 답하는 사실이고 순번은 기계에 답하는 순서다 —
    -- 시각을 순서로 쓰면 같은 초 안의 두 관측이 갈리지 않는다.
    last_observed_at  TEXT NOT NULL,
    last_observed_seq INTEGER NOT NULL,
    -- 이 본문이 원본 문서의 얼마를 담고 있는가 (v8, D-239). 추출기가 본문으로
    -- 보지 않는 영역의 개정은 text_hash에 나타나지 않으므로, 판정이 문서의
    -- 얼마를 보고 내려진 것인지를 함께 남긴다. **판정을 바꾸지 않는다.**
    -- NULL은 v8 이전에 만들어져 잰 적이 없다는 뜻이다 — 1.0으로 채우면
    -- 모르는 것이 확신으로 둔갑한다.
    coverage_basis          TEXT,     -- html-prose | whole-document | no-prose | not-measurable
    coverage_prose_chars    INTEGER,  -- 원본에서 센 산문 단위의 총 문자수
    coverage_captured_chars INTEGER,  -- 그중 저장 본문에서 발견된 문자수
    coverage_dropped        TEXT,     -- "aside:32 dd:554" — 미포착 블록의 구조별 개수
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
    created_at        TEXT NOT NULL,
    -- 생성 시점에 인용문이 원문에 몇 번 나왔는가 (v7). 앵커는 첫 출현에
    -- 묶이므로 2 이상이면 어느 인스턴스가 "그" 인용인지 모호하다. NULL은
    -- v7 이전에 만들어져 **모르는** 것이다 — 1로 채우면 단정이 된다.
    occurrences       INTEGER,
    -- 이 앵커가 **인용한 URL** (v9, D-246). 생성 시점 문서의 original_url이다.
    -- 문서를 경유해서는 알 수 없다 — 병합은 source의 original_url을 버리고
    -- documents 행을 지우므로, 옮겨간 앵커는 target의 정체성을 물려받는다.
    -- NULL은 v9 이전에 만들어져 **모르는** 것이다(내보내기는 그때만 문서의
    -- original_url로 물러선다).
    cited_url         TEXT
);

-- 재검증 이력.
CREATE TABLE verifications (
    id                TEXT PRIMARY KEY,
    anchor_id         TEXT NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
    -- 이 검증이 **대조한 버전**. GONE/UNREACHABLE이면 처음부터 NULL이고,
    -- 그 버전이 회수되면 NULL이 된다 (v10, D-249). 이 열이 버전을 붙잡으면
    -- 재검증하는 순간 그 버전은 영구 회수 불가가 되어, 정기적으로 verify하는
    -- 권장 워크플로에서 gc가 영원히 0건을 지운다. 우리가 약속한 것은 **인용
    -- 당시**의 원문을 되살리는 것이지(§1.2) 지나가며 본 모든 판본의 영구
    -- 보관이 아니다. 관측 기록("T에 검증했다·결과는 무엇")은 남고 그때 본
    -- 버전 참조만 사라진다 — NULL은 "어느 버전을 봤는지 모른다"이므로
    -- D-084는 보수적으로 "재검증 필요"로 읽는다.
    checked_version   TEXT REFERENCES versions(id) ON DELETE SET NULL,
    checked_at        TEXT NOT NULL,
    state             TEXT NOT NULL,           -- SPEC §6.3의 7종
    match_score       REAL,                    -- 0.0 ~ 1.0
    edit_distance     INTEGER,
    found_offset      INTEGER,
    found_text        TEXT,                    -- 변형되었을 경우 실제 발견된 문자열
    elapsed_ms        INTEGER NOT NULL
);

CREATE INDEX idx_versions_doc  ON versions(document_id, captured_at DESC);
CREATE INDEX idx_versions_observed ON versions(document_id, last_observed_seq DESC);
CREATE INDEX idx_fetchlog_time ON fetch_log(requested_at DESC);
CREATE INDEX idx_anchors_doc   ON anchors(document_id);
CREATE INDEX idx_verif_anchor  ON verifications(anchor_id, checked_at DESC);
CREATE INDEX idx_aliases_document ON document_aliases(document_id);

-- 버전을 붙잡는 **계약**을 후보마다 되묻는다 (v10, D-248). 없으면 gc의 후보
-- 선정이 후보 행마다 이 표들을 전체 훑어 2차 곡선이 된다(18,000버전 27.8초
-- 실측). `verifications(checked_version)`은 조회가 아니라 삭제를 위한 것이다 —
-- 인덱스가 없으면 SQLite가 `ON DELETE SET NULL`을 자식 표 전체 훑기로 처리한다.
CREATE INDEX idx_anchors_created_version   ON anchors(created_version);
CREATE INDEX idx_documents_current_version ON documents(current_version);
CREATE INDEX idx_verif_version             ON verifications(checked_version);
