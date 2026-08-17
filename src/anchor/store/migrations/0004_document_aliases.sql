-- SPDX-License-Identifier: Apache-2.0
-- v3 → v4: 리다이렉트로 도달한 문서를 원래 URL로도 찾을 수 있게 한다 (D-007).
--
-- 문서는 리다이렉트 **최종** URL로 저장되는데 조회는 사용자가 넘긴 URL로
-- 하므로 둘이 영영 만나지 못했다. 그 결과 캐시가 항상 빗나가고
-- (조건부 요청도 무력화), 원래 URL로는 cite·get_timemap이 실패했다.
-- SPEC §5.1 5단계의 "리다이렉트 응답이 있으면 그것을 우선"을 이 표로 구현한다.

CREATE TABLE document_aliases (
    url         TEXT PRIMARY KEY,   -- 정규화된 입력 URL
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX idx_aliases_document ON document_aliases(document_id);

-- 기존 문서의 리다이렉트 이전 URL을 별칭으로 옮긴다.
INSERT OR IGNORE INTO document_aliases (url, document_id)
SELECT original_url, id FROM documents WHERE original_url <> url;
