-- 003_scale_indexes.sql
-- 100배 규모(건물/호실/입주·수금 이력)를 위한 읽기 인덱스.
-- 모든 변경은 ADD COLUMN/ADD INDEX만 수행하며, run_migration.py가 이미
-- 적용된 항목(1060/1061)은 건너뛴다.

-- 호실 번호는 레거시 공백/대소문자가 섞일 수 있으므로 정규화 키를 둔다.
ALTER TABLE bd03_m
  ADD COLUMN hosu_norm VARCHAR(3)
    GENERATED ALWAYS AS (UPPER(TRIM(hosu))) VIRTUAL AFTER hosu;
ALTER TABLE bd03_m
  ADD INDEX idx_bd03m_hosu_norm (bunji1,bunji2,hosu_norm);

-- 입주 이력: 건물·호실별 현재/최근 이력 조회와 정규화 조인.
ALTER TABLE bd03_det
  ADD INDEX idx_bd03det_building_hosu_seq (bunji1,bunji2,hosu_norm,ipju_seq);
ALTER TABLE bd03_det
  ADD INDEX idx_bd03det_current (bunji1,bunji2,hosu_norm,out_dt,ipju_seq);

-- 수금: 기간 집계, 특정 세입자 누계, 등록 화면의 오늘 목록을 각각 지원.
ALTER TABLE sukum01
  ADD INDEX idx_sukum_char_date_tenant
    (sukum_char,sukum_dt,bunji1,bunji2,hosu_norm,ipju_seq,del_yn);
ALTER TABLE sukum01
  ADD INDEX idx_sukum_sys_date (sys_dt,del_yn,sukum_dt,sukum_seq);
ALTER TABLE sukum01
  ADD INDEX idx_sukum_date_tenant
    (sukum_dt,bunji1,bunji2,hosu_norm,ipju_seq,sukum_seq,del_yn);

-- 수리/퇴실: 건물·호실 조회와 등록일 목록을 인덱스 범위로 제한.
ALTER TABLE bd05_suri
  ADD COLUMN hosu_norm VARCHAR(3)
    GENERATED ALWAYS AS (UPPER(TRIM(hosu))) VIRTUAL AFTER hosu;
ALTER TABLE bd05_suri
  ADD INDEX idx_bd05_tenant_date
    (bunji1,bunji2,hosu_norm,ipju_seq,suri_dt,suri_seq);
ALTER TABLE bd05_suri
  ADD INDEX idx_bd05_sys_date (sys_dt,suri_dt,suri_seq);

ALTER TABLE bd07_out
  ADD COLUMN hosu_norm VARCHAR(3)
    GENERATED ALWAYS AS (UPPER(TRIM(hosu))) VIRTUAL AFTER hosu;
ALTER TABLE bd07_out
  ADD INDEX idx_bd07_tenant_date
    (bunji1,bunji2,hosu_norm,ipju_seq,out_dt,out_seq);

ALTER TABLE sjungke01
  ADD INDEX idx_sjungke_tenant_date
    (bunji1,bunji2,hosu,jungke_dt,jungke_seq);

-- 월정산 테이블은 원본에 인덱스가 없어서 목록/상세 조인이 전체 스캔이었다.
ALTER TABLE jungsan_m
  ADD INDEX idx_jungsan_m_building_date
    (bunji1,bunji2,jungsan_dt,jungsan_seq);
ALTER TABLE jungsan_det
  ADD COLUMN hosu_norm VARCHAR(3)
    GENERATED ALWAYS AS (UPPER(TRIM(hosu))) VIRTUAL AFTER hosu;
ALTER TABLE jungsan_det
  ADD INDEX idx_jungsan_det_tenant_date
    (bunji1,bunji2,hosu_norm,ipju_seq,jungsan_dt,jungsan_seq);
