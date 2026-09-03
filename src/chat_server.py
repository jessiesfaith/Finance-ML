"""
Finance-ML chat page — the assistant in a browser window beside Power BI.

Serves a single local page (127.0.0.1 only — nothing leaves your
machine except the Claude API calls the terminal assistant already
makes) with the red/green flag feed pinned on top and the grounded
chat below. Same brain and guardrails as src/chat_assistant.py; this
file only adds the local web surface.

Run from the repo root (or double-click start_chat.bat):

    python src/chat_server.py            # starts + opens your browser
    python src/chat_server.py --port 9000 --no-browser

Tip for a clean pinned window (no tabs/address bar), after the server
is up:  start msedge --app=http://127.0.0.1:8547

The flag feed and its Rebuild button work with no API key. Chat needs
`pip install anthropic` + ANTHROPIC_API_KEY (console.anthropic.com)
and each question costs a few cents — the page says so when the key
is missing instead of failing silently.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

import chat_assistant  # noqa: E402

DEFAULT_PORT = 8547
PAGE = SRC / "chat_page.html"


class ChatState:
    """One local user, one conversation, guarded by a lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.history: list[dict] = []
        self.client = None
        self.tools = None
        self.system_blocks = None
        self.chat_ready = False
        self.chat_error = ""

    def init_chat(self) -> None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            self.chat_error = (
                "ANTHROPIC_API_KEY is not set - chat is disabled. Flags "
                "still work. Set the key (console.anthropic.com) with "
                'setx ANTHROPIC_API_KEY "sk-ant-..." then relaunch from a '
                "NEW terminal.")
            return
        try:
            self.client, self.tools = chat_assistant.make_client_and_tools()
        except SystemExit:
            self.chat_error = ("The anthropic package is not installed - "
                               "chat is disabled. Run: pip install -r "
                               "requirements.txt and relaunch.")
            return
        self.system_blocks = [
            {"type": "text", "text": chat_assistant.SYSTEM_PROMPT},
            {"type": "text", "text": chat_assistant.build_bundle(),
             "cache_control": {"type": "ephemeral"}},
        ]
        self.chat_ready = True

    def ask(self, question: str) -> str:
        with self.lock:
            self.history.append({"role": "user", "content": question})
            reply = chat_assistant.answer(
                self.client, self.tools, self.system_blocks, self.history)
            self.history.append({"role": "assistant", "content": reply})
            return reply

    def reset(self) -> None:
        with self.lock:
            self.history = []


STATE = ChatState()


def read_flags() -> dict:
    path = chat_assistant.REPORTS / "client_fs_flags.csv"
    rows: list[dict] = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    counts = {c: sum(1 for r in rows if r["color"] == c)
              for c in ("RED", "YELLOW", "GREEN")}
    return {"rows": rows, "counts": counts}


def rebuild_flags() -> dict:
    from financials.flags import REPORTS, build_flags
    flags = build_flags()
    flags.to_csv(REPORTS / "client_fs_flags.csv", index=False)
    return read_flags()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/flags":
            self._json(read_flags())
        elif self.path == "/api/status":
            self._json({"chat_ready": STATE.chat_ready,
                        "chat_error": STATE.chat_error,
                        "model": chat_assistant.MODEL})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad JSON"}, 400)
            return
        if self.path == "/api/ask":
            if not STATE.chat_ready:
                self._json({"reply": STATE.chat_error})
                return
            question = str(body.get("question", "")).strip()
            if not question:
                self._json({"error": "empty question"}, 400)
                return
            self._json({"reply": STATE.ask(question)})
        elif self.path == "/api/reset":
            STATE.reset()
            self._json({"ok": True})
        elif self.path == "/api/rebuild-flags":
            try:
                self._json(rebuild_flags())
            except Exception as exc:  # surface builder errors to the page
                self._json({"error": f"rebuild failed: {exc}"}, 500)
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, fmt: str, *args) -> None:
        pass  # keep the console quiet; errors still raise


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Local chat page for the Finance-ML report")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true",
                    help="do not auto-open the page")
    args = ap.parse_args()

    STATE.init_chat()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Finance-ML chat page: {url}  (Ctrl+C to stop)")
    if STATE.chat_ready:
        print(f"Chat ready - model {chat_assistant.MODEL}; questions cost "
              "a few cents each. Flags are free.")
    else:
        print(STATE.chat_error)
    print(f"Pinned-window tip:  start msedge --app={url}")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
