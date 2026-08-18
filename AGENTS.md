# oneroom-web — 다른 AI는 이 파일부터

로컬 Flask 원룸 관리. MariaDB `sinbee`. 레거시 XP 화면을 웹으로 옮긴 것.
주인: 한국어로 짧게 말함. 리밋 자주 걸림 → **이 파일만 읽고 바로 작업**. 구조 재설계·대규모 리팩터 금지.

**역할**: Claude Code가 메인 개발 AI, Grok(+로컬 Ollama)은 보조 (크레딧 절약용, 자세히: `docs/local-ai.md`).
Claude Code 사용 한도가 다 떨어지면 그동안은 Grok이 메인 역할을 대신함 — 그 경우에도 이 파일의 규칙(절대 금지 항목 등)은 동일하게 적용.

## 더 필요하면

- **할 일 목록**: `docs/TODO.md` (작업 시작 전에만)
- **인터페이스 현대화 배경/결정사항**: `docs/modernization.md` (현대화 작업할 때만)
- **로컬 AI(Grok/Ollama/Groq)로 크레딧 아끼기**: `docs/local-ai.md`

## 30초 요약

- 실행: `start.bat` 또는 `python app.py` → http://127.0.0.1:5000
- 로그인: `sawon_m` (예: 사번 10001 + 기존 비번). DB 설정은 `config.py`.
- `app` 객체는 `app_instance.py` 하나. 화면 모듈은 `from app_instance import app` 후 `@app.route`.
- `app.py`는 기동·로그인/홈 + 모듈 import만. **화면 로직을 app.py에 다시 넣지 말 것.**

## 파일 지도 (화면 고칠 때 여기)

| 파일 | 담당 |
|---|---|
| `app.py` | 기동, `/` `/home` `/login` `/logout`, Jinja 필터, 모듈 등록 |
| `app_instance.py` | Flask `app` 생성 (순환 import 방지) |
| `nav.py` | 상단 메뉴 강조 |
| `utils.py` | 포맷/마스킹/로그인/미수계산/`build_pager`/권한 데코레이터 |
| `db.py` `config.py` | DB 풀, 접속정보 |
| `building.py` | 건물·호실·공실 |
| `tenants.py` | 입주 이력 등록/수정/삭제 + `/api/next_ipju_seq` `/api/tenant_load` |
| `search.py` | 입주자 이력 조회 |
| `users.py` | 사용자관리, 비밀번호변경 |
| `payments.py` | 수금현황 목록/검색 (`/payments`) |
| `payment_register.py` | 수금(대체) 등록 (`/payments/new`) |
| `payments_api.py` | 공용 API `/api/building` `/api/current_tenant` `/api/payments/delete` (다른 화면도 씀) |
| `checkout.py` | 퇴실 정산, 목록, 계약해지 인쇄 |
| `repair.py` | 수리 |
| `misu.py` | 미수금 |
| `jungsan.py` | 월정산 (책임관리만 지원, 일반관리는 `docs/TODO.md` 참고) |
| `jungke.py` | 중개수수료 |
| `sukum_import.py` | 입금파일 자동반영 (은행 .xls/.xlsx 업로드 → 계좌번호/이름/호수 매칭 → sukum01 등록), 남은 이슈는 `docs/TODO.md` |
| `docs.py` | 서식·자료 게시판 (도움말 메뉴, `/docs`) |
| `templates/` | 화면 HTML (이름 = 화면) |
| `static/css/style.css` | 스타일. 입주 폼 간격은 여기 `.tr-*` |

새 화면 = 전용 `xxx.py` + `app.py`에 `import xxx as xxx_routes  # noqa: F401`.
공통 헬퍼만 `utils.py`. 화면끼리 서로 import 하지 말 것.

## 절대 금지

1. **입주 이력 등록 간격 수정 금지.** `templates/tenant_form.html` + CSS `.tr-*` / `.tenant-reg-*` 수치 건드리지 말 것. 다른 폼은 `.xp-form`만 사용.
2. **XP 화면 간격·배치를 "예쁘게" 바꾸지 말 것.** 레거시와 같아야 함.
3. **라우트를 Blueprint로 바꾸거나 `app.py`로 다시 합치지 말 것.**
4. **커밋은 주인이 시키기 전엔 하지 말 것.** (이 인수인계 커밋은 예외로 이미 함)
5. **새 폴더·Blueprint 금지.** 화면 파일이 여러 독립된 관심사(목록/등록/공용 API 등)를 섞고 있어서 AI가 작업 하나에 파일 전체를 읽어야 하는 경우엔, 같은 루트 폴더 안에서 flat한 `.py` 파일로 더 쪼갤 수 있음(2026-08-18, `payments.py`→`payments.py`+`payment_register.py`+`payments_api.py` 사례 참고). 단, 진짜 하나의 개념(계산 엔진+그걸 쓰는 라우트, 건물+호실처럼 계층적으로 묶인 도메인 등)을 억지로 쪼개진 말 것 — 목적은 토큰 절약이지 파일 개수 늘리기가 아님. Blueprint·서브폴더·SQLAlchemy는 여전히 금지.
   - **`checkout.py`(1193줄)는 분리 안 하기로 결정함 (2026-08-18)**: 4개 라우트가 계산/인쇄 엔진(~450줄)을 공유해서, 쪼개도 계산 로직 고칠 땐 결국 엔진 전체를 읽어야 함 — 목록/페이징 작업만 이득(1193→~650), 계산 작업엔 이득 없음. 효과가 작아서 그냥 유지하기로 함. 다시 제안하지 말 것.

## UX / 데이터 규칙

### 기본 데이터 규칙
- **번지**: 화면은 앞 0 제거, DB는 4자리 (`pad_bunji`). `508-88` 입력 → `parse_bunji_input`.
- **번지 연동**: 번지1 바꾸면 번지2 비움. 번지1·2 UI 통일.
- **수금 등록**: 수금일자 다음 줄이 ★번지1.
- **현재 입주**: `out_dt IS NULL OR out_dt < '1000-01-01'` (`CURRENT_TENANT_SQL`).

### 페이징 규칙
- **페이지당 건수**: 20건 (`utils.PAGE_SIZE = 20`)
- **페이지 블록**: 6페이지 (`utils.PAGE_BLOCK_SIZE = 6`)
- **페이징 위치**: "다음 ›"이 위, "‹ 이전"이 아래 (반전 순서)
- **페이징 함수**: `utils.build_pager`, `utils.make_pager`, `utils.paginate`

### 권한 시스템
- **등급**: U(무제한), A(최고관리자), B(일반관리자), C(조회전용)
- **데코레이터** (`utils.py`):
  - `@login_required`: 로그인 필요
  - `@require_admin`: 관리자 권한 (U, A만 가능) - 사용자관리 등
  - `@require_write_access`: 쓰기 권한 (C 등급 제외) - 수정/삭제 등
  - `@require_grade('U', 'A')`: 특정 등급만 접근 허용
- **적용 범위**:
  - 사용자관리: `@require_admin`
  - 건물/호수/입주/수금/퇴실/수리/중개수수료 등록/수정/삭제: `@require_write_access`

### 스타일 규칙
- **XP 호환**: 레거시 XP 화면과 동일한 간격·배치 유지
- **입주 폼**: `templates/tenant_form.html` + CSS `.tr-*` / `.tenant-reg-*` 간격 절대 수정 금지
- **다른 폼**: `.xp-form` 클래스 사용
- **버튼 스타일**: 캡션 액션 버튼은 카드 버튼 스타일 적용 (회색 배경/검은 글자)

### 메뉴 규칙
- **상단 메뉴**: `nav.py` 테이블에서 관리
- **새 endpoint 추가**: `nav.py`에도 추가 필요

## 검증 (화면 만졌으면 필수)

```bat
python -m py_compile app.py app_instance.py users.py search.py payments.py checkout.py building.py tenants.py repair.py misu.py jungsan.py jungke.py utils.py nav.py sukum_import.py
python -m unittest tests.test_tenant_form -v
```

로그인 세션으로 GET 스모크 (로그아웃을 먼저 치지 말 것):

```
/home /users /search /payments /payments/new /buildings /vacancies
/tenants/manage /repairs /misu /jungsan /jungke /checkout /checkout/list
```

브라우저 도구 있으면 고친 화면을 클릭·입력까지 확인. 없으면 test client.
