# 다른 AI 작업 인수인계

이 문서는 작업 중인 AI가 바뀌어도 현재 상태와 변경 이유를 빠르게 파악하기 위한 기록이다.

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
