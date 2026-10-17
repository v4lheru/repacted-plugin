"""bin/repact-journal-write replaces the journal ERR trap the command files used to carry.

One regression test per behaviour: a write lands in the journal through the wrapper; a failed
write prints the `[JOURNAL WRITE FAILED] <site>` line and exits with the inner code; and the five
command files that used the trap carry only wrapper calls now (an absence rule: a trap block
reappearing beside the wrapper is the duplication the wrapper removed).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WRAPPER = PLUGIN_ROOT / "bin" / "repact-journal-write"
DETRAPPED = ("orchestrate.md", "comPACT.md", "peer-review.md", "plan-mode.md", "wrap-up.md")


def _run(session_dir: Path, stdin: str, event_type: str = "checkpoint") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(WRAPPER), "--site", "orchestrate.md", "--type", event_type,
                           "--session-dir", str(session_dir), "--stdin"],
                          input=stdin, capture_output=True, text=True)


def test_a_write_through_the_wrapper_lands_in_the_journal(tmp_path):
    result = _run(tmp_path, '{"phase": "CODE", "safe_to_retry": true}\n')
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in (tmp_path / "session-journal.jsonl").read_text().splitlines()]
    assert [(e["type"], e["phase"]) for e in events] == [("checkpoint", "CODE")]


def test_a_failed_write_prints_the_trap_line_and_keeps_the_exit_code(tmp_path):
    result = _run(tmp_path, "not json\n")
    assert result.returncode == 1
    assert "[JOURNAL WRITE FAILED] orchestrate.md:" in result.stderr and "exit=1" in result.stderr
    assert not (tmp_path / "session-journal.jsonl").exists()


def test_help_prints_the_usage_and_exits_zero_without_writing(tmp_path):
    result = subprocess.run([sys.executable, str(WRAPPER), "--help"], capture_output=True, text=True,
                            cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: repact-journal-write --site")
    assert list(tmp_path.iterdir()) == []


def test_the_command_files_carry_wrapper_calls_and_no_trap():
    for name in DETRAPPED:
        text = (PLUGIN_ROOT / "commands" / name).read_text(encoding="utf-8")
        assert "[JOURNAL WRITE FAILED]" not in text and "trap 'rc=$?" not in text, name
        assert f"repact-journal-write\" --site {name} " in text, name
