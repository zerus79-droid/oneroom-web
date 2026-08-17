# oneroom-web — 다른 AI는 이 파일부터

로컬 Flask 원룸 관리. MariaDB `sinbee`. 레거시 XP 화면을 웹으로 옮긴 것.
주인: 한국어로 짧게 말함. 리밋 자주 걸림 → **이 파일만 읽고 바로 작업**. 구조 재설계·대규모 리팩터 금지.

**역할 (2026-08-17 확정)**: Claude Code가 메인 개발 AI, Grok(+로컬 Ollama)은 보조.
큰 판단·직접 코드 수정은 Claude Code가 하고, Grok은 크레딧 절약용 보조 작업(아래
"크레딧 절약" 절 참고)에만 쓸 것. 단, Claude Code 사용 한도가 다 떨어지면 그동안은
Grok이 메인 역할을 대신함 — 그 경우에도 이 파일의 규칙(절대 금지 항목 등)은 동일하게 적용.

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
| `payments.py` | 수금 현황/등록, `/api/building` `/api/current_tenant` `/api/payments/delete` |
| `checkout.py` | 퇴실 정산, 목록, 계약해지 인쇄 |
| `repair.py` | 수리 |
| `misu.py` | 미수금 |
| `jungsan.py` | 월정산 |
| `jungke.py` | 중개수수료 |
| `templates/` | 화면 HTML (이름 = 화면) |
| `static/css/style.css` | 스타일. 입주 폼 간격은 여기 `.tr-*` |

새 화면 = 전용 `xxx.py` + `app.py`에 `import xxx as xxx_routes  # noqa: F401`.
공통 헬퍼만 `utils.py`. 화면끼리 서로 import 하지 말 것.

## 절대 금지

1. **입주 이력 등록 간격 수정 금지.** `templates/tenant_form.html` + CSS `.tr-*` / `.tenant-reg-*` 수치 건드리지 말 것. 다른 폼은 `.xp-form`만 사용.
2. **XP 화면 간격·배치를 "예쁘게" 바꾸지 말 것.** 레거시와 같아야 함.
3. **라우트를 Blueprint로 바꾸거나 `app.py`로 다시 합치지 말 것.**
4. **자동 음성 요약 하지 말 것.** (주인 요청으로 끔)
5. **커밋은 주인이 시키기 전엔 하지 말 것.** (이 인수인계 커밋은 예외로 이미 함)
6. **새 폴더·Blueprint 금지.** 화면별 파일 분리는 끝났음. SQL은 화면 파일에 유지.

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
python -m py_compile app.py app_instance.py users.py search.py payments.py checkout.py building.py tenants.py repair.py misu.py jungsan.py jungke.py utils.py nav.py
python -m unittest tests.test_tenant_form -v
```

로그인 세션으로 GET 스모크 (로그아웃을 먼저 치지 말 것):

```
/home /users /search /payments /payments/new /buildings /vacancies
/tenants/manage /repairs /misu /jungsan /jungke /checkout /checkout/list
```

브라우저 도구 있으면 고친 화면을 클릭·입력까지 확인. 없으면 test client.

## 지금 상태 (2026-08-17)

화면별 파일 분리 **끝**. `routes/` `services/` 폴더로 한 번에 재설계하지 말 것.

구조는 "나쁜 편 아님 / 더 정리할 여지는 있음".
약점: 큰 파일 안에 조회+등록+SQL이 같이 있음. SQL을 지금 빼지 말 것.

파일 크기(대략): `payments.py` 1093 · `checkout.py` 835 · `tenants.py` 748 · `building.py` 615 · `app.py` 125.

### 커밋 안 된 변경사항 (2026-08-17 기준, 주인이 커밋 지시 전까지 그대로 둠)

- 거의 모든 tracked `.py` / `templates/*.html` / `static/css/style.css` 가 수정 상태(uncommitted). 다른 AI가 작업 이어받을 때 `git diff`로 실제 변경 내용부터 확인할 것.
- untracked 신규 파일(아직 커밋 안 됨):
  - 배포/인프라: `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `.dockerignore`
  - SQL 백업/덤프: `restore_bd03m.sql`, `sinbee_mariadb.sql`
  - 임시/작업용 스크립트 (커밋 대상 아닐 가능성 높음, 삭제 전 주인 확인 필요): `fix_utils.py`, `migrate_tel.py`, `temp_query.py`, `replace_svg.py`, `_local_prompt.txt`, `path/`
  - 새 템플릿: `templates/_pager.html`, `templates/checkout_pays.html`, `templates/payments_list.html`, `templates/payments_result.html`
- 로그인 화면(`templates/login.html`, `app.py`의 `/login`)은 2026-08-17 세션에서 test client로 재검증 완료: GET 렌더, 빈 값/오류 비번 플래시, DB(`sawon_m`) 조회, 세션 생성 후 `/home` 접근, `/logout` 흐름 모두 정상. 코드 변경 없음(확인만 함).
- 주인 데스크톱의 `Claude Code.bat`은 `oneroom-web` 폴더로 `cd` 후 `claude --continue`로 실행하도록 수정함(이전엔 `D:\ai`로 이동 후 새 세션 시작).

### 완료된 구조 정리 (한 파일 안 함수 분리만)

1. **`payments.py` 완료 (2026-08-14)** — `payments()` 목록/이름검색/기간을 헬퍼로 올림. 등록 화면 렌더 중복 제거.
2. **`checkout.py` 완료 (2026-08-14)** — 폼 추출 + `_save_checkout` 분리.
3. **`users.py` 완료 (2026-08-15)** — 권한 시스템 데코레이터 추가. 사용자등급: U/A/B/C.
4. **`building.py` 완료 (2026-08-15)** — 공실 현황 페이징 추가, 호수 삭제 기능 추가.

## 크레딧 절약 · 로컬 모델

Grok 한도 아낄 때: 로컬 Ollama **`deepseek-r1:8b`**.

- 켜져 있는지: `http://127.0.0.1:11434/api/tags`
- 프로젝트에서: `python ask_local.py -p "질문" -f tenants.py -o LOCAL_PLAN.md`
- Grok TUI: `/model deepseek-local` (Ollama 켜 둔 상태)
- **로컬에 맡길 것:** 읽기, 함수 쪼개 계획, 중복 찾기
- **로컬에 맡기지 말 것:** 파일 직접 패치, 입주 폼 HTML/CSS `.tr-*`, DB 쓰기, 커밋

결과 파일이 있으면 Grok은 그걸 읽고 적용만 한다.

### Groq 질답형 에이전트 (`D:\ai\groq_agent.py`, 2026-08-18 추가)

Ollama와 달리 **파일을 직접 읽고 고칠 수 있는** 보조 에이전트. Claude Code 없이도
질문하면서 바로 수정까지 가능. `openai/gpt-oss-120b` 모델 사용 (무료, TPM/RPD 한도 있음).

```bat
python D:\ai\groq_agent.py
```
(기본 대상 폴더: `C:\Users\someb\oneroom-web`. 다른 폴더 쓰려면 인자로 경로 전달)

- 대화하면서 `read_file` / `list_dir` / `edit_file` / `write_file` 도구를 스스로 호출함
- **모든 수정(`edit_file`/`write_file`)은 diff를 먼저 보여주고 `(y/n)` 확인을 받은 뒤에만 적용** — 확인 없이 파일이 바뀌지 않음
- `tenant_form.html`은 도구 레벨에서 자동 거부(하드 블록) — "절대 금지" 규칙이 이 에이전트에도 그대로 적용됨
- 그래도 보조 AI다: 큰 판단이 필요하면 Claude Code로 가져올 것
- 순수 채팅만 필요하면 (파일 접근 없이) `D:\ai\groq_cli.py`를 대신 쓸 것
