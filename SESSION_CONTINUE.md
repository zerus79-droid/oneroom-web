# oneroom-web · 이어하기

**다른 AI / 새 세션은 `AGENTS.md`만 읽으면 됨.**

- 폴더: `C:\Users\someb\oneroom-web`
- 실행: `start.bat` 또는 `python app.py` → http://127.0.0.1:5000
- 구조 분리 끝. 큰 리팩터 금지. `payments.py`/`checkout.py` 함수 분리는 됨. 다음은 `tenants.py`.
- 크레딧 아끼려면 로컬 `deepseek-r1:8b` (`ask_local.py`, Grok은 `/model deepseek-local`).

```bat
cd /d C:\Users\someb\oneroom-web
"%USERPROFILE%\.grok\bin\grok.exe" -c
```
