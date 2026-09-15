-- 월정산 저장 시 화면/인쇄 값을 함께 보존한다. 기존 저장자료는 보정하지 않는다.
-- 적용 전 jungsan_m 백업 및 InnoDB 여부 확인 필요 (004 참조).
-- 자동 실행하지 않음. 미적용 DB는 조회 가능하며 새 저장은 안내 후 거부한다.
-- 2026-09-15 로컬 sinbee 적용 완료. 기존 6,581행 해시/컬럼/인덱스 유지 확인.
ALTER TABLE jungsan_m
  ADD COLUMN IF NOT EXISTS snapshot_json LONGTEXT NULL
  COMMENT '월정산 저장 당시 화면/인쇄 데이터, version 포함 JSON';
