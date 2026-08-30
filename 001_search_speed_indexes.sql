-- 001_search_speed_indexes.sql
-- 목적: bd03_det 검색/조회 속도 개선 (PK 외 인덱스가 전혀 없던 문제)
-- 안전성: 기존 데이터/코드를 변경하지 않는 ADD COLUMN(VIRTUAL) + ADD INDEX만 사용.
--         적용 전 반드시 백업을 떠두세요. (이미 backups/ 폴더에 최근 백업 있음)
--
-- 적용 방법:
--   mysql -u root -p sinbee < migrations/001_search_speed_indexes.sql
-- 또는 Navicat/DBeaver/HeidiSQL 등에서 그대로 실행.

-- 1) hosu 정규화 가상 컬럼 + 인덱스
--    기존 코드가 어디서든 UPPER(TRIM(hosu))=%s 로 비교하던 것을,
--    이 인덱스 컬럼(hosu_norm)으로 비교하면 인덱스를 그대로 탑니다.
--    데이터 저장 로직은 전혀 바꿀 필요 없음 (VIRTUAL이라 자동 계산됨).
ALTER TABLE bd03_det
  ADD COLUMN hosu_norm VARCHAR(3)
    GENERATED ALWAYS AS (UPPER(TRIM(hosu))) VIRTUAL AFTER hosu,
  ADD INDEX idx_hosu_norm (hosu_norm);

-- 2) 자주 단독 필터링되는 컬럼 인덱스
--    out_dt: 현재/과거 세입자 구분 필터에 항상 걸림
--    ipju_seq: 계약 순번 단독 검색에 걸림
ALTER TABLE bd03_det
  ADD INDEX idx_out_dt (out_dt),
  ADD INDEX idx_ipju_seq (ipju_seq);

-- 3) (선택, 효과 큼) 이름 부분검색 가속: ngram 전문검색 인덱스
--    한글은 띄어쓰기 기준 기본 파서가 잘 안 맞아서 ngram 파서를 씁니다.
--    주의: DB 서버(my.cnf/my.ini)에 아래 설정 후 "서버 재시작" 필요:
--      [mysqld]
--      ngram_token_size=2
--    재시작이 부담스러우면 이 블록은 생략하고 1), 2)번만 적용해도
--    체감 속도는 크게 개선됩니다 (건물/호실 기준 조회, 페이지네이션 전반).
--
-- ALTER TABLE bd03_det
--   ADD FULLTEXT INDEX ft_ipju_nm (ipju_nm) WITH PARSER ngram;

-- 적용 확인:
--   SHOW INDEX FROM bd03_det;
