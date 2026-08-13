# oneroom-web — 다른 AI는 이 파일부터

로컬 Flask 원룸 관리. MariaDB `sinbee`. 레거시 XP 화면을 웹으로 옮긴 것.
주인: 한국어로 짧게 말함. 리밋 자주 걸림 → **이 파일만 읽고 바로 작업**. 구조 재설계·대규모 리팩터 금지.

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
| `utils.py` | 포맷/마스크/로그인/미수계산/`build_pager` |
| `db.py` `config.py` | DB 풀, 접속정보 |
| `building.py` | 건물·호실·공실 |
| `tenants.py` | 입주 이력 등록/수정/삭제 + `/api/next_ipju_seq` `/api/tenant_load` |
| `search.py` | 입주자 이력 조회 (30건 페이징) |
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
2. XP 화면 간격·배치를 “예쁘게” 바꾸지 말 것. 레거시와 같아야 함.
3. 라우트를 Blueprint로 바꾸거나 `app.py`로 다시 합치지 말 것.
4. 자동 음성 요약 하지 말 것. (주인 요청으로 끔)
5. 커밋은 주인이 시키기 전엔 하지 말 것. (이 인수인계 커밋은 예외로 이미 함)

## UX / 데이터 규칙

- 번지: 화면은 앞 0 제거, DB는 4자리 (`pad_bunji`). `508-88` 입력 → `parse_bunji_input`.
- 번지1 바꾸면 번지2 비움. 번지1·2 UI 통일.
- 수금 등록: 수금일자 다음 줄이 ★번지1.
- 현재 입주: `out_dt IS NULL OR out_dt < '1000-01-01'` (`CURRENT_TENANT_SQL`).
- 페이징: `utils.build_pager`, 블록 6페이지. 검색 30건/페이지.
- 상단 메뉴: `nav.py` 테이블. 새 endpoint 추가 시 여기도 추가.

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

## 지금 상태 (2026-08-14)

화면별 파일 분리 **끝**. `routes/` `services/` 폴더로 한 번에 재설계하지 말 것.

구조는 “나쁜 편 아님 / 더 정리할 여지는 있음”.
약점: 큰 파일 안에 조회+등록+SQL이 같이 있음. SQL을 지금 빼지 말 것.

파일 크기(대략): `payments.py` 1093 · `checkout.py` 835 · `tenants.py` 748 · `building.py` 615 · `app.py` 125.

### 소규모 구조 정리 (한 파일 안 함수 분리만)

새 폴더·Blueprint 금지. SQL은 화면 파일에 유지.

1. **`payments.py` 완료 (2026-08-14)** — `payments()` 목록/이름검색/기간을 헬퍼로 올림. 등록 화면 렌더 중복 제거.
2. **`checkout.py` 완료** — 폼 추출 + `_save_checkout` 분리.
3. 다음 후보: `tenants.py` (입주 등록 라우트가 큼). `.tr-*` CSS/간격은 건드리지 말 것.
4. `building.py`는 폼 헬퍼 이미 있음. `app.py`는 더 쪼갤 것 없음.

## 크레딧 절약 · 로컬 모델

Grok 한도 아낄 때: 로컬 Ollama **`deepseek-r1:8b`**.

- 켜져 있는지: `http://127.0.0.1:11434/api/tags`
- 프로젝트에서: `python ask_local.py -p "질문" -f tenants.py -o LOCAL_PLAN.md`
- Grok TUI: `/model deepseek-local` (Ollama 켜 둔 상태)
- **로컬에 맡길 것:** 읽기, 함수 쪼개 계획, 중복 찾기
- **로컬에 맡기지 말 것:** 파일 직접 패치, 입주 폼 HTML/CSS `.tr-*`, DB 쓰기, 커밋

결과 파일이 있으면 Grok은 그걸 읽고 적용만 한다.
