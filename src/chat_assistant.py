"""
Finance-ML chat assistant — Q&A over the pipeline's own files.

A command-line analyst that answers questions grounded in THIS repo:
the curated reports/*.csv the Power BI tabs read, the Python engine,
the SQL, and the docs. It also surfaces the red/green flag feed.

Three ways to run it (from the repo root, C:\\dev\\Finance-ML):

    python src/chat_assistant.py --flags          # notifications only — no API, no key
    python src/chat_assistant.py --ask "why is PROJ-004 rejected?"
    python src/chat_assistant.py                  # interactive chat

Guardrails (same policy as agents/financial_review_agent.py):
    READ · ANALYZE · EXPLAIN — the assistant reads repo files and
    explains them. It never writes, never approves, and is instructed
    to quote numbers only from the files, labeling assumptions.

Requirements & cost: chat modes call the Claude API (model
claude-opus-5) through the official `anthropic` SDK — you need
`pip install anthropic` and an ANTHROPIC_API_KEY in your environment
(console.anthropic.com). Each question costs real money (roughly a few
cents with caching; the first question of a session is the priciest
because it uploads the data bundle). `--flags` is free and offline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

MODEL = "claude-opus-5"

# Curated exports small enough to hand the model whole; the big market
# histories are summarized (header + newest rows) and fetchable in full
# through the read tool.
INLINE_CSVS = [
    "client_fs_flags.csv", "client_fs_statements.csv", "client_fs_ufcf.csv",
    "finance_scenario_report.csv", "client_fs_projects.csv",
    "client_fs_option_verdicts.csv", "client_fs_option_sensitivity.csv",
    "client_fs_option_sizing.csv", "client_fs_sensitivity.csv",
    "client_fs_income_walk.csv", "client_fs_valuation_inputs.csv",
    "client_fs_controls.csv", "client_fs_review.csv", "market_rf_policy.csv",
]
HEAD_CSVS = ["market_history_rolling24.csv", "market_history_windows.csv",
             "market_history_long.csv"]
DOC_FILES = ["docs/PAGE_FLOW.md", "docs/LEARNING_GUIDE.md",
             "docs/SIGN_CONVENTION.md"]
INVENTORY_GLOBS = ["src/*.py", "src/financials/*.py", "agents/*.py",
                   "sql/*.sql", "src/*.sql", "tests/*.py", "docs/*.md"]

SYSTEM_PROMPT = """\
You are the Finance-ML analyst assistant, answering questions about a
teaching-grade valuation pipeline and its Power BI report (tabs:
1 Client Financials, 2 Current Position, 3 Market & Cost of Capital,
4 Macro History, 5 Options, 6 Valuation & Recommendation,
6b Flags & Alerts, 7 Math Reference, 8 Calc Build-Out, 9 Legacy).

Rules — these mirror the repo's agent guardrails and are absolute:
1. GROUND EVERY NUMBER. Quote figures only from the data bundle below
   or from files you read with the tool. Name the file (and tab, when
   one shows it). If a number is not in the files, say so — never
   estimate or invent one.
2. LABEL ASSUMPTIONS. Terminal growth 2.5%, hurdle premium 2pts,
   target P/E 15x, 65% recurring share are stated ASSUMPTIONS — say so
   whenever you use them.
3. READ-ONLY. You explain and flag; you never change files, approve
   decisions, or promise actions. Decisions belong to the human.
4. SYNTHETIC honesty. Market history is labeled SYNTHETIC seed data
   with registered live sources; placeholder equity slots T1-T5/A1-A5
   are NOT real companies. Never attribute synthetic history to a real
   firm.
5. TEACH. The user is learning Python, ML and finance — walk the math
   the way the Math Reference tab does: intuition first, then formula,
   then the fixture's numbers.
6. FLAGS. reports/client_fs_flags.csv is the notification feed
   (RED act / YELLOW review / GREEN confirmed). When a question
   touches a flagged area, point at the flag and its recommended
   action.

Use the read_project_file tool when the bundle is not enough — e.g. to
quote engine code (src/financials/*.py), SQL, tests, or full market
history. Keep answers concise and cite paths like src/financials/flags.py.
"""


# ----------------------------------------------------------------------
# offline: the notification feed
# ----------------------------------------------------------------------

def print_flags() -> int:
    import csv

    path = REPORTS / "client_fs_flags.csv"
    if not path.exists():
        print("No flags export found — run: python src/build_flags.py")
        return 1
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    icons = {"RED": "[RED]   ", "YELLOW": "[YELLOW]", "GREEN": "[GREEN] "}
    for color in ("RED", "YELLOW", "GREEN"):
        group = [r for r in rows if r["color"] == color]
        if not group:
            continue
        print(f"\n{color} — {len(group)} "
              f"{'to act on' if color == 'RED' else 'to review' if color == 'YELLOW' else 'confirmed healthy'}")
        for r in group:
            print(f"  {icons[color]} {r['headline']}")
            if color != "GREEN":
                print(f"           -> {r['recommended_action']} "
                      f"(see tab {r['source_tab']})")
    print(f"\n{len(rows)} checks total — details on the report's "
          "'6b · Flags & Alerts' tab or reports/client_fs_flags.csv")
    return 0


# ----------------------------------------------------------------------
# grounding bundle
# ----------------------------------------------------------------------

def _csv_head(path: Path, lines: int = 12) -> str:
    text = path.read_text(encoding="utf-8").splitlines()
    shown = text[:1] + text[-(lines - 1):] if len(text) > lines else text
    note = (f"... ({len(text) - 1} data rows total; header + newest rows "
            "shown — use read_project_file for the full file)")
    return "\n".join(text[:1] + [note] + shown[1:])


def build_bundle() -> str:
    parts = ["# DATA BUNDLE — curated exports (the same CSVs Power BI reads)"]
    for name in INLINE_CSVS:
        p = REPORTS / name
        if p.exists():
            parts.append(f"\n## reports/{name}\n{p.read_text(encoding='utf-8')}")
    for name in HEAD_CSVS:
        p = REPORTS / name
        if p.exists():
            parts.append(f"\n## reports/{name} (summarized)\n{_csv_head(p)}")
    parts.append("\n# KEY DOCS")
    for name in DOC_FILES:
        p = ROOT / name
        if p.exists():
            text = p.read_text(encoding="utf-8")
            if len(text) > 20000:
                text = text[:20000] + "\n... (truncated — read the file)"
            parts.append(f"\n## {name}\n{text}")
    parts.append("\n# FILE INVENTORY (fetch any of these with the tool)")
    for pattern in INVENTORY_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            rel = p.relative_to(ROOT).as_posix()
            first = ""
            for line in p.read_text(encoding="utf-8",
                                    errors="replace").splitlines()[:5]:
                line = line.strip().strip('"').strip("'#- ")
                if line:
                    first = line[:100]
                    break
            parts.append(f"- {rel} — {first}")
    return "\n".join(parts)


# ----------------------------------------------------------------------
# chat modes (Claude API)
# ----------------------------------------------------------------------

def make_client_and_tools():
    try:
        import anthropic
        from anthropic import beta_tool
    except ImportError:
        print("The chat modes need the Anthropic SDK:  pip install anthropic\n"
              "(--flags works without it.)")
        sys.exit(1)

    @beta_tool
    def read_project_file(path: str) -> str:
        """Read one file from the Finance-ML repository (read-only).

        Args:
            path: Repo-relative path, e.g. "src/financials/flags.py" or
                "reports/market_history_long.csv".
        """
        target = (ROOT / path).resolve()
        if not target.is_relative_to(ROOT) or ".git" in target.parts:
            return "DENIED: only files inside the Finance-ML repo are readable."
        if not target.is_file():
            return f"NOT FOUND: {path}"
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > 50000:
            text = text[:50000] + "\n... (truncated at 50KB)"
        return text

    return anthropic.Anthropic(), [read_project_file]


def answer(client, tools, system_blocks, messages) -> str:
    import anthropic

    try:
        runner = client.beta.messages.tool_runner(
            model=MODEL,
            max_tokens=16000,
            system=system_blocks,
            tools=tools,
            messages=messages,
        )
        final = runner.until_done()
        return "".join(b.text for b in final.content if b.type == "text")
    except anthropic.AuthenticationError:
        return ("No valid API key. Set ANTHROPIC_API_KEY "
                "(console.anthropic.com) and retry.")
    except anthropic.RateLimitError:
        return "Rate limited — wait a minute and ask again."
    except anthropic.APIStatusError as exc:
        return f"API error {exc.status_code}: {exc.message}"
    except anthropic.APIConnectionError:
        return "Network error — check your connection and retry."


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--flags", action="store_true",
                    help="print the red/green notification feed (offline)")
    ap.add_argument("--ask", metavar="QUESTION",
                    help="ask one question and exit")
    args = ap.parse_args()

    if args.flags:
        return print_flags()

    client, tools = make_client_and_tools()
    bundle = build_bundle()
    # One cached system prompt: guardrails + the full data bundle.
    # cache_control makes follow-up questions ~90% cheaper.
    system_blocks = [
        {"type": "text", "text": SYSTEM_PROMPT},
        {"type": "text", "text": bundle,
         "cache_control": {"type": "ephemeral"}},
    ]

    if args.ask:
        print(answer(client, tools, system_blocks,
                     [{"role": "user", "content": args.ask}]))
        return 0

    print("Finance-ML analyst assistant — grounded in this repo's files.\n"
          "Questions cost a few cents each (Claude API). Ctrl+C or 'quit' "
          "to exit.\nTip: 'python src/chat_assistant.py --flags' shows the "
          "notification feed for free.\n")
    history: list[dict] = []
    while True:
        try:
            question = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not question:
            continue
        if question.lower() in ("quit", "exit", "q"):
            return 0
        history.append({"role": "user", "content": question})
        reply = answer(client, tools, system_blocks, history)
        # Store only the final text turn; tool round-trips stay inside
        # the runner. Keeps history small and the cached prefix stable.
        history.append({"role": "assistant", "content": reply})
        print(f"\nanalyst> {reply}\n")


if __name__ == "__main__":
    sys.exit(main())
