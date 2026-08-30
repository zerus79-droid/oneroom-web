-- 002_sukum01_indexes.sql
-- 목적: 건물 20개(15개 추가)로 늘어날 때를 대비해 수금(sukum01) 조회 인덱스 미리 추가.
-- 지금(수금 약 8천 건)은 없어도 체감이 크지 않지만, 4배(3만 건대)로 늘기 전에
-- 미리 넣어두는 마이그레이션. 001과 동일하게 기존 코드/데이터는 건드리지 않음.
--
-- 적용 방법:
--   mysql -u root -p sinbee < migrations/002_sukum01_indexes.sql

-- 1) hosu 정규화 가상 컬럼 + 인덱스 (bd03_det에 했던 것과 동일한 이유)
--    jungsan_engine.py 등에서 UPPER(TRIM(hosu))=%s 로 비교하는 패턴이 반복됨.
ALTER TABLE sukum01
  ADD COLUMN hosu_norm VARCHAR(3)
    GENERATED ALWAYS AS (UPPER(TRIM(hosu))) VIRTUAL AFTER hosu,
  ADD INDEX idx_hosu_norm (hosu_norm);

-- 2) 실제 조회 패턴 기준 복합 인덱스
--    거의 모든 쿼리가 "이 건물/호실/계약순번의 특정 기간 수금" 형태
--    (bunji1=%s AND bunji2=%s AND hosu_norm=%s AND ipju_seq=%s AND sukum_dt BETWEEN ...)
ALTER TABLE sukum01
  ADD INDEX idx_sukum_lookup (bunji1, bunji2, hosu_norm, ipju_seq, sukum_dt);

-- 3) sukum_char(수금 종류 구분)로도 자주 필터링되어 별도 인덱스 추가
ALTER TABLE sukum01
  ADD INDEX idx_sukum_char (sukum_char);

-- 적용 후 코드에서 UPPER(TRIM(hosu))=%s 를 hosu_norm=%s 로 바꾸면 인덱스를 직접 탑니다.
-- (당장 안 바꿔도 동작은 그대로, 나중에 여유될 때 search.py처럼 교체 권장)

-- 적용 확인:
--   SHOW INDEX FROM sukum01;
