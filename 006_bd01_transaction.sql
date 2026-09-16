-- 월정산 저장과 건물 최초보증금 갱신을 하나의 트랜잭션으로 처리한다.
-- 앱 시작/조회 시 자동 실행하지 않는다. 사용자 승인 및 DB 쓰기 중단 후 적용.
-- 적용 전 bd01 전체 스키마/자료 백업과 임시 테이블 복원 검증이 필요하다.
-- 적용 후 전체 자료·컬럼·인덱스가 동일하고 ENGINE만 바뀌었는지 확인한다.
-- ALTER 자체는 implicit commit이므로 rollback되지 않는다.
-- 2026-09-16 로컬 sinbee 적용 완료: 119행 전체 해시·컬럼·인덱스 일치.
SET SESSION lock_wait_timeout = 15;
ALTER TABLE bd01 ENGINE=InnoDB;
