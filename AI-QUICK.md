# 1.5k AI용

Flask 원룸관리(MariaDB `sinbee`). **전체 프로젝트·`AGENTS.md`는 읽지 말고 요청 관련 파일만 1~3개 읽는다.**

## 금지·필수

- `app.py`는 기동/로그인/import만. 새 화면은 루트 `xxx.py` + `app.py` import. Blueprint, 새 폴더, SQLAlchemy 금지.
- `templates/tenant_form.html`, CSS `.tr-*`/`.tenant-reg-*` 간격 절대 수정 금지. XP 화면 배치도 임의 변경 금지.
- 쓰기(등록/수정/삭제)는 `@require_write_access`, 사용자관리는 `@require_admin`.
- 요청 범위만 최소 수정. 커밋 금지. 끝나면 `python -m py_compile <수정.py>`.

## 파일 지도

- 건물·호실·공실: `building.py`, `templates/building*.html`
- 입주: `tenants.py`, `templates/tenant*.html` / 검색: `search.py`
- 수금 목록: `payments.py` / 등록: `payment_register.py` / 공용 API: `payments_api.py`
- 퇴실: `checkout.py`; 수리·미수·월정산·중개: 같은 이름 `.py`와 템플릿
- 메뉴: `nav.py`; 권한·번지·페이징: `utils.py`; 스타일: `static/css/style.css`의 필요한 부분만

## 규칙

현재 입주: `out_dt IS NULL OR out_dt < '1000-01-01'`. 번지는 `parse_bunji_input`/`pad_bunji` 사용. 목록은 20건, 6페이지 블록(`utils.build_pager`). 모르면 관련 함수/라우트만 찾아보고 추측 수정하지 않는다.
