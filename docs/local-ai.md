# 크레딧 절약 · 로컬 모델

로컬 AI로 작업 이어받을 때만 읽으면 됨. 핵심 규칙은 루트 `AGENTS.md` 참고.

Grok 한도 아낄 때: 로컬 Ollama **`deepseek-r1:8b`**.

- 켜져 있는지: `http://127.0.0.1:11434/api/tags`
- 프로젝트에서: `python ask_local.py -p "질문" -f tenants.py -o LOCAL_PLAN.md`
- Grok TUI: `/model deepseek-local` (Ollama 켜 둔 상태)
- **로컬에 맡길 것:** 읽기, 함수 쪼개 계획, 중복 찾기
- **로컬에 맡기지 말 것:** 파일 직접 패치, 입주 폼 HTML/CSS `.tr-*`, DB 쓰기, 커밋

결과 파일이 있으면 Grok은 그걸 읽고 적용만 한다.

## Groq 질답형 에이전트 (`D:\ai\groq_agent.py`, 2026-08-18 추가)

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
