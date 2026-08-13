# 원룸 관리 웹 (1차)

MariaDB `sinbee` 데이터를 조회·수금 입력하는 로컬 웹 화면입니다.

**다른 AI가 이어받을 때:** 루트 `AGENTS.md` 를 먼저 읽으세요.

## 실행 방법

1. MariaDB가 켜져 있는지 확인
2. 터미널에서:

```bat
cd C:\Users\someb\oneroom-web
C:\Users\someb\AppData\Local\Programs\Python\Python312\python.exe app.py
```

3. 브라우저에서 http://127.0.0.1:5000 접속

## 로그인

`sawon_m` 직원 계정 (예: 사번 10001 + 기존 비밀번호)

## 1차 기능

- 로그인 / 로그아웃
- 건물 목록 · 호실 · 현재 세입자
- 세입자 검색
- 수금 목록 · 수금 입력

## 설정

`config.py` 에 DB 접속 정보가 있습니다.
