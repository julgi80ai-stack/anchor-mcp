-- SPDX-License-Identifier: Apache-2.0
-- v5 → v6: 버전이 **마지막으로 관측된 때와 그 순서**를 기록한다 (D-083).
--
-- `latest`는 포인터(현재 원문이 서빙하는 본문)를 따르고 `latest~N`은
-- 캡처 시각을 따라, 되돌림에서 두 좌표계가 갈렸다. A→B→A에서 latest~1이
-- latest와 같은 행을 가리켜 기본 diff가 비었고, A→B→A→C→A에서는 **일어난
-- 적 없는 전이(B→A)** 를 보여줬으며 중간 판본 B는 어떤 ref로도 도달할 수
-- 없었다.
--
-- 뿌리는 본문 해시로 중복을 제거하면서 **관측의 시간축이 접힌 것**이다.
-- captured_at(처음 캡처된 때)은 Memento-Datetime이라 바꿀 수 없으므로,
-- 마지막 관측을 따로 둔다. 기존 행은 캡처 시각을 그대로 쓴다 — 그때까지는
-- 둘이 같았다.
--
-- 두 컬럼인 이유: 시각은 사람에게 답하는 사실("이 본문이 마지막으로 살아
-- 있던 때")이고, 순번은 기계에 답하는 순서다. 시각을 순서로 쓰면 같은 초
-- 안의 두 관측이 갈리지 않고 시계가 뒤로 밀리면 순서가 뒤집힌다.

ALTER TABLE versions ADD COLUMN last_observed_at TEXT NOT NULL DEFAULT '';
ALTER TABLE versions ADD COLUMN last_observed_seq INTEGER NOT NULL DEFAULT 0;

UPDATE versions SET last_observed_at = captured_at WHERE last_observed_at = '';

-- 기존 행의 순번은 캡처 시각 순으로 매긴다. 그때까지 알 수 있는 최선이며,
-- 되돌림이 없었던 문서에서는 실제 관측 순서와 같다.
UPDATE versions SET last_observed_seq = (
    SELECT COUNT(*) FROM versions AS earlier
    WHERE earlier.document_id = versions.document_id
      AND (earlier.captured_at < versions.captured_at
           OR (earlier.captured_at = versions.captured_at AND earlier.id <= versions.id))
);

CREATE INDEX idx_versions_observed ON versions(document_id, last_observed_seq DESC);
