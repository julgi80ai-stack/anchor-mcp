-- SPDX-License-Identifier: Apache-2.0
-- v7 → v8: 저장 본문이 원본 문서의 얼마를 담고 있는지 기록한다 (D-239).
--
-- 추출기가 본문으로 보지 않는 영역의 개정은 normalized_text에 나타나지
-- 않는다 — text_hash가 같고, 판정은 `unchanged`이며, 그 문서의 앵커가 전부
-- `INTACT`로 남는다. 실측: RFC 9110은 산문의 3.6%만 저장되고 <aside> 32개가
-- 전부 사라진다. 그 안에 정정 고지가 있어도 저장 본문에는 없다.
--
-- 이 열들은 판정을 바꾸지 않는다. 판정이 문서의 얼마를 보고 내려진 것인지를
-- 함께 남길 뿐이다. 저장하는 이유는 `cite`다 — cite는 **어떤 경우에도
-- 네트워크에 나가지 않으므로**(D-230), 매번 재계산한다는 선택지가 없다.
-- 인용을 거는 순간이 사용자가 그 본문을 신뢰하기로 결정하는 순간이다.
--
-- 기존 행은 전부 NULL이다 — 그때 얼마나 봤는지는 잰 적이 없고, 되짚어 잴
-- 원본 HTML도 남아 있지 않다(저장하는 것은 정규화 본문뿐이다). 1.0으로
-- 채우면 모르는 것을 "전부 봤다"고 단정하는 것이 된다 (v7 occurrences와
-- 같은 판단). 그 행들은 다음 관측 때 채워진다.
ALTER TABLE versions ADD COLUMN coverage_basis TEXT;
ALTER TABLE versions ADD COLUMN coverage_prose_chars INTEGER;
ALTER TABLE versions ADD COLUMN coverage_captured_chars INTEGER;
ALTER TABLE versions ADD COLUMN coverage_dropped TEXT;
