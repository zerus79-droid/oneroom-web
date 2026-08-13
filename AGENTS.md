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

구조 분리 **끝**. 남은 할 일 없음 — 다음 화면/버그는 주인 지시 대기.

최근 작업:
- Copilot: building/repair/misu/jungsan/jungke/tenants + nav/utils 분리 (커밋됨)
- Grok: 남은 users/search/payments/checkout 분리, 검색 페이징, `build_pager` → utils
- 검색 템플릿 `has_filter` 누락 수정, payments `fmt_bunji_pair` import 수정

## 주인 말투 / 작업 방식

- “딴넘이 구조 바꿨다 / 이어 받아” = git log + 이 파일 + `git status` 보고 이어서.
- 리밋 걸리면 다른 AI가 이 파일만 읽고 재개해야 함. 장황한 계획서 쓰지 말고 바로 고치기.
- 웹 UI 바꾸면 브라우저로 동작 확인.
