"""
Offline tests for the chat assistant: the flag feed and the grounding
bundle. The Claude-API chat modes are exercised on the owner's machine
(they need a key and cost money); nothing here touches the network.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import chat_assistant  # noqa: E402


def test_flags_mode_runs_offline_and_shows_all_colors(capsys):
    assert chat_assistant.print_flags() == 0
    out = capsys.readouterr().out
    assert "RED" in out and "YELLOW" in out and "GREEN" in out
    assert "6b · Flags & Alerts" in out


def test_bundle_carries_the_curated_exports_and_inventory():
    bundle = chat_assistant.build_bundle()
    assert "reports/client_fs_flags.csv" in bundle
    assert "reports/finance_scenario_report.csv" in bundle
    assert "implied_share_price" in bundle          # real data inline
    assert "src/financials/flags.py" in bundle      # file inventory
    assert "sql/macro.sql" in bundle
    assert "# KEY DOCS" in bundle
    # big histories are summarized, not inlined whole
    assert "newest rows shown" in bundle


def test_cli_flags_flag_via_subprocess():
    proc = subprocess.run(
        [sys.executable, "src/chat_assistant.py", "--flags"],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "checks total" in proc.stdout
