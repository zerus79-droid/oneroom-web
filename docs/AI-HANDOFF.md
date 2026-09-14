# 다른 AI 작업 인수인계

이 문서는 작업 중인 AI가 바뀌어도 현재 상태와 변경 이유를 빠르게 파악하기 위한 기록이다.

## 2026-09-14: 순차 해결 1번 — 월정산 저장 안전성 (DB 전환 대기)

- 사용자 요청: 문제를 목록으로 관리하고 하나씩 해결. 우선 저장 중 자료 손실 문제만 보완한다. 퇴실 계산식, 인쇄 규격, 저장본 재계산은 후속 항목이다(`docs/TODO.md`).
- 확인: 현재 `fix/checkout-settlement`의 기존 변경은 5개 커밋(`21a0b05`~`f90f4a1`)으로 원격 브랜치에 존재하며 master는 `dc45749`다. 전체 master 반영은 보류 상태.
- DB에서 `information_schema.TABLES`를 읽어 확인한 결과 `jungsan_m`, `jungsan_det`, `bd01` 모두 MyISAM이다. 따라서 기존 코드에 begin/rollback만 추가하면 보호되지 않는다.
- 준비한 코드: `_jungsan_write_snapshot`이 두 정산 테이블의 InnoDB 여부를 먼저 검사한다. 아니면 데이터 변경 전에 저장을 거부한다. 건물·월별 `GET_LOCK`을 얻고 하나의 연결/트랜잭션으로 헤더 조회, 합계 저장, 상세 교체를 실행한다. 실패하면 rollback하고 잠금/연결을 해제한다. `/jungsan/save`는 실패 시 저장 성공 표시를 하지 않는다.
- 동시 저장 잠금은 이 웹 저장 함수를 사용하는 요청 사이에만 적용된다. 같은 잠금을 사용하지 않는 XP/직접 SQL의 동시 쓰기까지 막는 기능은 아니다.
- `004_jungsan_transaction_tables.sql`은 **아직 운영 DB에 실행하지 않았다**. 정산 테이블 2개만 전환한다. `bd01` 등 다른 테이블은 변환하지 않는다. 실제 전환 전까지 새 코드의 월정산 저장은 안내와 함께 거부되며 조회는 가능하다.
- 실제 전환 절차: (1) 웹·XP 등 DB 쓰기를 중단할 시간을 사용자에게 확인 (2) 두 테이블의 스키마/데이터를 `backups/` 등 Git 제외 위치에 파일로 백업하고 복원 가능 여부 확인(MyISAM이므로 `--single-transaction`만으로는 부족) (3) 전후 행수·합계 기준값 기록 (4) SQL 실행 (5) 두 엔진과 행수/합계/스키마를 비교하고 격리된 테스트 자료로 저장/실패 복구를 확인한 후 재개. 전환 중 잠금이 발생할 수 있고 ALTER 자체는 rollback되지 않는다. 중간 실패 시 부분 전환 상태에서 저장을 재개하지 말 것.
- 주의: 저장 중 rollback과 과거 저장 이력 보존은 별개다. 기존 저장본 조회가 최신 자료를 섞는 문제, 성공 저장 시 이전 이력을 남기는 문제는 2번에서 따로 해결해야 한다. 보호됐다고 보고하지 말 것.
- 검증: 필수 Python 컴파일 통과. `JUNGSAN_TXN_DB_TEST=1`로 `tests.test_jungsan_snapshot`, `tests.test_tenant_form`, `tests.test_settlement_calendar`를 함께 실행해 47개 통과. 새 테스트 8개는 같은 연결 사용, 전체 rollback, 신규 저장 실패, MyISAM/부분 전환 거부, 잠금 대기 실패, commit 실패, 실패 응답, 실제 임시 InnoDB 테이블의 합계·상세 복구를 확인한다. 임시 테이블은 별도 이름으로 빈 스키마만 복제하고 테스트 후 삭제하며 업무 자료는 수정하지 않는다.
- 화면 확인: 로그인 test client로 AGENTS.md의 GET 14곳 모두 HTTP 200. 일반 `db.execute` 쓰기와 POST 미리보기를 mock한 상태에서 실제 MyISAM 저장 거부도 확인(302, 성공 표시 없음). 실제 브라우저 입력 검증·운영 저장·서버 재시작은 하지 않았다.
- 실행 환경: `.venv`에는 `flask_caching` 등 일부 의존성이 없어 import 검증에 사용할 수 없었다. 기존 패키지가 설치된 `C:\Users\someb\AppData\Local\Programs\Python\Python312\python.exe`로 테스트했으며 패키지/환경 설정은 변경하지 않았다.

## 2026-09-10: 공용=수리공용, 수금 임+관, 월정산 대체 XP

- **공용**: `bd01_common_cost` 드롭. 마스터는 `bd05_suri` 호수 `공용` + `YEAR=1000`. 월정산/목록에서 그달 말일 행이 없으면 복사. 수리 합·목록은 `YEAR(suri_dt)>1000`.
- **수금**: 목록·인쇄·엑셀 헤더를 XP처럼. 종류 01은 `rent_manage_disp`=실입+대체 전액. `_split_char01_payment`는 테스트만 남김(화면 미사용).
- **월정산**: 책임관리 입금액 due=월세. 대체 호 `(대체)`. `imdae_dache = misu_tot`. 대체 버튼 대상은 여전히 월세+관리비 부족분.
- 검증: `tests.test_settlement_calendar`, `tests.test_payments_print`, 508-88 2018-09 인쇄·2026-08 화면 test client.
- 남은 정산 이슈는 `docs/TODO.md` (퇴실월 예외, `jungsan_det.manage_amt` 복사 버그, `calc_misu_amt` 과거단가, 일반관리 화면).

## 2026-09-09: 수금 수정 시 거래 시간 보존

- 대상: `payment_register.py`, `/payments/new` 수정 저장 분기(PR #68 후속 수정).
- 원인: 금액만 수정해도 `sukum_dt`를 수금일의 `00:00:00`으로 덮어썼다.
- 수정: UPDATE의 CASE 조건으로 날짜가 같으면 DB의 기존 `sukum_dt`를 보존한다. 사용자가 날짜를 변경한 경우에만 변경 날짜의 자정으로 저장하는 기존 동작을 유지한다.
- 검증: 저장 호출을 mock 처리한 POST 흐름에서 SQL과 인자 개수를 확인하고, 실제 SET 식을 MariaDB의 읽기 전용 SELECT로 평가했다. 금액 수정, 같은 날짜의 호실 변경은 `14:23:45` 보존, 날짜 변경은 새 날짜 자정으로 확인했다. Python 컴파일 및 `tests.test_tenant_form` 2개 통과.
- 실제 수금자료 UPDATE는 실행하지 않았다. 이미 과거 수정으로 소실된 거래 시간은 이번 변경으로 복구되지 않는다.
- 사용자 지시: 검증이 끝난 변경은 바로 커밋하고 인수인계 기록을 남긴다.

## 2026-09-09: 월별 정산서 조회 속도 확인

- 대상 URL: `/jungsan/list?q=1&year=2026&month=5`
- 현재 코드에서 test client로 3회 측정: `0.246초`, `0.203초`, `0.189초` (모두 HTTP 200).
- 따라서 월별 정산서 목록은 현재 약 0.2초 수준으로 개선되어 있다. 이전에 측정된 50~60초 결과는 개선 전 코드 또는 오래 실행된 서버 프로세스 기준으로 판단한다.
- 페이지 단위 계산 최적화 관련 커밋이 이미 반영되어 있으므로, 이후 성능 확인 시 반드시 최신 서버를 재시작하고 같은 URL로 재측정한다.

## 현재 작업: 입금파일 자동반영 CSRF 안정화

입금파일 자동반영 화면은 `sukum_import.py` 라우트와 다음 템플릿을 사용한다.

- `templates/payments_import.html`: 파일 업로드, 자동반영, 빠른 제외
- `templates/payments_import_matches.html`: 입금자/계좌 매칭 규칙 등록·삭제
- `templates/payments_import_exclude.html`: 제외 키워드 등록·삭제

앱은 Flask-WTF가 아니라 `app.py`의 자체 CSRF 검증을 사용한다. 모든 POST 요청은 세션의 `csrf_token`과 폼 또는 `X-CSRF-Token` 헤더의 값이 일치해야 한다. 템플릿에서는 `{{ csrf_token }}` 변수를 사용한다. `form.hidden_tag()`나 `csrf_token()` 함수는 사용하지 않는다.

## 이번 변경

간헐적인 `CSRF token validation failed`를 막기 위해 동적·인라인 POST 폼에도 토큰을 직접 넣었다.

- `templates/payments_import.html`
  - `exclude-quick-form`에 토큰 추가
  - `apply-form`에 토큰 추가
- `templates/payments_import_matches.html`
  - 매칭 규칙 등록/수정 폼에 토큰 추가
  - 목록의 삭제 폼에 토큰 추가
- `templates/payments_import_exclude.html`
  - 제외 키워드 등록/수정 폼에 토큰 추가
  - 목록의 삭제 폼에 토큰 추가

기본 업로드 폼에는 이미 토큰이 있었으므로 중복 추가하지 않았다. `base.html`에도 POST 폼 자동 주입이 있지만, 화면 일부가 동적으로 교체되는 경우를 고려해 각 폼에 직접 토큰을 넣는 방식으로 보강했다.

## 아직 남은 입금파일 TODO

- 한현승처럼 동일 이름으로 여러 호실을 쓰는 입금 분류 기준 확정
- 김호진/행복요양병원 적요가 잘리는 원인 확인
- 세입자명과 입금자명 불일치 사유 표시
- 농협 외 은행 파일 형식 지원
- 반영 완료 자료 잠금
- 1139-4 외 건물 실사용 테스트

## 작업 규칙

- 커밋은 사용자가 명시적으로 요청할 때만 한다.
- 인쇄 전용 템플릿/레이아웃은 별도 요청 없이는 변경하지 않는다.
- 변경 후 전체 Python 컴파일과 관련 템플릿 로드를 확인한다.
- `_check202.py`는 입금파일 데이터를 확인하기 위한 임시 진단 파일이며 기능 코드가 아니다.
