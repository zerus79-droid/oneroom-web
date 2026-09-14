-- 월정산 저장 안전성 1단계: 정산 합계/상세의 트랜잭션 지원.
-- 운영 DB에는 아직 적용하지 않음. 자동 실행하거나 앱 시작 시 실행하지 말 것.
-- 승인 후: 웹/XP를 포함한 DB 쓰기를 중단하고 두 테이블의 스키마+데이터를
-- 별도 파일로 백업해 복원 가능한지 확인한다. 상세 절차는 docs/AI-HANDOFF.md.
-- ALTER는 implicit commit이므로 두 전환 자체를 rollback할 수 없다.
-- 중간 실패 시 저장 코드는 두 테이블 모두 InnoDB가 될 때까지 저장을 거부한다.

SET SESSION lock_wait_timeout = 15;
ALTER TABLE jungsan_m ENGINE=InnoDB;
ALTER TABLE jungsan_det ENGINE=InnoDB;
