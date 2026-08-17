-- SPDX-License-Identifier: Apache-2.0
-- v2 → v3: "현재 원문 버전"을 캡처 시각 최대값과 분리한다 (D-011/D-012/D-024).
--
-- 지금까지는 `latest_version()`(= captured_at 최대)을 "원문의 현재 모습"으로
-- 썼는데, 두 경우에 어긋난다:
--   ① 아카이브 폴백으로 과거 시각 스냅샷을 받은 뒤 — 최신은 여전히 옛 live
--      버전이라 verify가 앵커 생성에 쓴 본문에 자기 자신을 대조하게 된다.
--   ② 본문이 이전 값으로 되돌아온 뒤 — 옛 버전 행을 재사용하므로 최신은
--      계속 그 사이의 버전을 가리킨다.
-- 관측할 때마다 갱신하는 포인터를 따로 둔다. TimeMap의 시간순 열거는
-- 여전히 captured_at을 쓴다.

ALTER TABLE documents ADD COLUMN current_version TEXT REFERENCES versions(id);

-- 기존 문서는 캡처 시각이 가장 늦은 버전을 현재로 본다(종전 동작과 동일).
UPDATE documents SET current_version = (
    SELECT id FROM versions v
    WHERE v.document_id = documents.id
    ORDER BY v.captured_at DESC, v.id DESC
    LIMIT 1
);
