# oneroom-web

**먼저 `AGENTS.md`를 읽어라.** 프로젝트 규칙·파일 지도·금지사항이 전부 거기 있다.

한 줄: 로컬 Flask 원룸관리(MariaDB `sinbee`). 화면 로직은 `app.py`가 아니라 `building.py` `tenants.py` `search.py` `users.py` `payments.py` `checkout.py` `repair.py` `misu.py` `jungsan.py` `jungke.py`. `app`은 `app_instance.py`에서만 생성.

만지지 말 것: 입주 이력 등록 간격 (`tenant_form.html`, CSS `.tr-*`). 다른 폼은 `.xp-form`.
