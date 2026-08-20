-- SPDX-License-Identifier: Apache-2.0
-- v9 → v10: 버전을 붙잡는 것에서 **관측 로그**를 덜어낸다 (D-249) + 보호 여부를
-- 되묻는 조회에 인덱스를 붙인다 (D-248).
--
-- `verifications.checked_version`이 FK로 버전을 붙잡아, **재검증하는 순간 그
-- 버전은 영구 회수 불가**가 됐다. 그런데 "정기적으로 verify"는 이 프로젝트가
-- 권장하는 워크플로다. 실측: 1문서·41버전·매회 verify → `collect_garbage(keep=5)`
-- 가 0건을 지웠고, 300문서·18,000버전에서도 0건이었다. SPEC §4.2가 "문서당 최근
-- N개 보존"을 약속해 놓고 그 규칙에 영영 도달하지 못한다.
--
-- 버전을 붙잡는 것에는 **계약**과 **이력**이 섞여 있었다.
--   anchors.created_version    = 인용 당시 원문. §1.2가 약속한 것. 절대 못 지운다.
--   documents.current_version  = 지금 서빙되는 본문. 못 지운다.
--   verifications.checked_version = "T에 앵커 A를 버전 V와 대조했다"는 관측 로그.
-- 셋째를 앞의 둘과 같은 무게로 두는 것이 결함이었다. 우리가 약속한 것은 **인용
-- 당시**의 원문을 되살린다이지 지나가며 본 모든 판본의 영구 보관이 아니다.
--
-- `ON DELETE SET NULL`이면 관측 기록("T에 검증했다·결과는 무엇")은 남고 그때 본
-- 버전 참조만 사라진다. NULL은 "어느 버전을 봤는지 **모른다**"이고, D-084는
-- 그것을 보수적으로 "재검증 필요"로 읽는다 — "검증됨"으로 읽히지 않는다.
--
-- SQLite는 FK를 ALTER로 바꿀 수 없어 표를 다시 만든다 (0005와 같은 공식 절차).

CREATE TABLE verifications_new (
    id                TEXT PRIMARY KEY,
    anchor_id         TEXT NOT NULL REFERENCES anchors(id) ON DELETE CASCADE,
    checked_version   TEXT REFERENCES versions(id) ON DELETE SET NULL,
    checked_at        TEXT NOT NULL,
    state             TEXT NOT NULL,
    match_score       REAL,
    edit_distance     INTEGER,
    found_offset      INTEGER,
    found_text        TEXT,
    elapsed_ms        INTEGER NOT NULL
);

INSERT INTO verifications_new SELECT
    id, anchor_id, checked_version, checked_at, state, match_score,
    edit_distance, found_offset, found_text, elapsed_ms
FROM verifications;

DROP TABLE verifications;

ALTER TABLE verifications_new RENAME TO verifications;

CREATE INDEX idx_verif_anchor ON verifications(anchor_id, checked_at DESC);

-- 보호 여부를 후보 행마다 되묻는 조회가 이 표들을 전체 훑고 있었다 (D-248).
-- `EXPLAIN QUERY PLAN`이 SCAN a·SCAN d를 냈고, 곡선은 명확한 2차였다
-- (4,500버전 1.2s → 9,000 6.1s → 18,000 27.8s). `verifications(checked_version)`은
-- 조회가 아니라 **삭제**를 위한 것이다 — 인덱스가 없으면 SQLite가 위의
-- `ON DELETE SET NULL`을 지우는 버전마다 자식 표 전체 훑기로 처리한다.
CREATE INDEX idx_verif_version             ON verifications(checked_version);
CREATE INDEX idx_anchors_created_version   ON anchors(created_version);
CREATE INDEX idx_documents_current_version ON documents(current_version);
