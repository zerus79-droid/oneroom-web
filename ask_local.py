"""로컬 Ollama(deepseek-r1:8b)에 짧은 일 맡기기. Grok 크레딧 절약용."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL = "deepseek-r1:8b"
OLLAMA = "http://127.0.0.1:11434/api/chat"


def chat(prompt: str, model: str = DEFAULT_MODEL, timeout: int = 300) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        OLLAMA,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise SystemExit(
            f"Ollama 연결 실패 ({e}). ollama serve 가 켜져 있는지 확인."
        ) from e
    return (data.get("message") or {}).get("content") or ""


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask local deepseek-r1:8b")
    parser.add_argument("-p", "--prompt", help="Prompt text")
    parser.add_argument("-f", "--file", help="Attach a project file after the prompt")
    parser.add_argument("-o", "--out", help="Write reply to this path")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    prompt = (args.prompt or "").strip()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        parser.error("prompt required (-p or stdin)")
    if args.file:
        path = ROOT / args.file
        prompt = f"{prompt}\n\n----- {path.name} -----\n{path.read_text(encoding='utf-8')}"
    reply = chat(prompt, model=args.model)
    if args.out:
        out = Path(args.out)
        if not out.is_absolute():
            out = ROOT / out
        out.write_text(reply.strip() + "\n", encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(reply)


if __name__ == "__main__":
    main()
