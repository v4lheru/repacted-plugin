"""
Location: pact-plugin/tests/fixtures/journal_parity/record_m0.py (repacted EVENTS, m8a)
Summary: Records the event-parity fixture journal FROM THE COMMAND BLOCKS AT repacted-m0.
         No m0 fixture journal existed (wave-b-briefs.md §6), so this builds one: for every
         fenced bash block at tag repacted-m0 that writes variety_assessed, agent_dispatch or
         phase_transition, it substitutes the block's placeholders with one fixed scenario and
         runs m0's own session_journal.py `write` on it. The rows m0's writer produced are the
         spec tests/test_journal_emitter.py replays the hook against.
Used by: run by hand when the scenario changes; its output is m0_blocks.json (committed).
         python3 tests/fixtures/journal_parity/record_m0.py   (from repacted/pact-plugin, in a
         git worktree that has the repacted-m0 tag)
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TAG = "repacted-m0"
PLUGIN = "repacted/pact-plugin"
FILES = ("commands/orchestrate.md", "commands/comPACT.md", "commands/peer-review.md",
         "commands/plan-mode.md", "commands/rePACT.md")
TYPES = ("variety_assessed", "agent_dispatch", "phase_transition")
HERE = Path(__file__).resolve().parent

# One scenario: which task, agent, phase and numbers each block's placeholders stand for,
# keyed by (file, n-th write of that file). The hook replay in the test builds the same
# task store and tool calls.
SCENARIO = {
    ("commands/orchestrate.md", 0): {"feature_task_id": "1", "N": [2, 3, 2, 3, 10]},
    ("commands/orchestrate.md", 1): {"PHASE": "PREPARE"},
    ("commands/orchestrate.md", 2): {"PHASE": "PREPARE"},
    ("commands/orchestrate.md", 3): {"PHASE": "ARCHITECT", "reason": "structured_gate_passed"},
    ("commands/orchestrate.md", 4): {"taskId": "7", "N": [1, 2, 2, 1, 6]},
    ("commands/orchestrate.md", 5): {"taskId": "7"},
    ("commands/orchestrate.md", 6): {"taskId": "9", "N": [2, 2, 3, 2, 9]},
    ("commands/orchestrate.md", 7): {"taskId": "9"},
    ("commands/orchestrate.md", 8): {"taskId": "11", "N": [2, 3, 2, 3, 10]},
    ("commands/orchestrate.md", 9): {"taskId": "11", "coder-name": "backend-coder",
                                     "assigned_paths": "src/app.py"},
    ("commands/orchestrate.md", 10): {"taskId": "13", "N": [1, 2, 1, 2, 6]},
    ("commands/orchestrate.md", 11): {"taskId": "13"},
    ("commands/peer-review.md", 0): {"taskId": "15", "N": [1, 1, 2, 1, 5]},
    ("commands/plan-mode.md", 0): {"taskId": "17", "N": [3, 3, 3, 2, 11]},
    ("commands/comPACT.md", 0): {"feature_task_id": "20", "N": [7]},
    ("commands/comPACT.md", 1): {"taskId": "22", "N": [2, 2, 2, 2, 8]},
    ("commands/comPACT.md", 2): {"taskId": "22", "specialist-name": "backend-coder-2",
                                 "assigned_paths": "src/app.py"},
    ("commands/comPACT.md", 3): {"taskId": "24", "N": [1, 2, 1, 1, 5]},
    ("commands/comPACT.md", 4): {"taskId": "24", "specialist-name": "frontend-coder"},
    ("commands/comPACT.md", 5): {},
    ("commands/rePACT.md", 0): {"sub_feature_task_id": "30", "N": [8]},
    ("commands/rePACT.md", 1): {"taskId": "32", "N": [2, 1, 2, 2, 7]},
}


def blocks(path: str):
    """(line, type, payload template) for each journal write of the 3 types at TAG."""
    text = subprocess.run(["git", "show", f"{TAG}:{PLUGIN}/{path}"], capture_output=True,
                          text=True, check=True).stdout.split("\n")
    for i, line in enumerate(text):
        match = re.search(r"--type\s+(\w+)", line)
        if not match or match.group(1) not in TYPES or "<<'JSON'" not in line:
            continue
        body = []
        for follow in text[i + 1:]:
            if follow.lstrip("> ").strip() == "JSON":
                break
            body.append(follow.lstrip("> "))
        yield i + 1, match.group(1), "\n".join(body).strip()


def substitute(template: str, values: dict) -> str:
    numbers = iter(values.get("N", []))
    out = re.sub(r"\bN\b", lambda _m: str(next(numbers)), template)
    if "assigned_paths" in values:
        out = out.replace('"{assigned_paths}"', json.dumps(values["assigned_paths"]))
    for key, value in values.items():
        if key not in ("N", "assigned_paths"):
            out = out.replace("{" + key + "}", value)
    return out


def main() -> int:
    writer = Path(tempfile.mkdtemp()) / "session_journal_m0.py"
    writer.write_text(subprocess.run(["git", "show", f"{TAG}:{PLUGIN}/hooks/shared/session_journal.py"],
                                     capture_output=True, text=True, check=True).stdout)
    session = Path(tempfile.mkdtemp())
    rows = []
    for path in FILES:
        for n, (line, event_type, template) in enumerate(blocks(path)):
            payload = substitute(template, SCENARIO[(path, n)])
            subprocess.run([sys.executable, str(writer), "write", "--type", event_type,
                            "--session-dir", str(session), "--stdin"], input=payload, text=True,
                           check=True)
            written = json.loads((session / "session-journal.jsonl").read_text().splitlines()[-1])
            written["ts"] = "<m0-write-time>"
            rows.append({"block": f"{path}:{line}", "event": written})
    (HERE / "m0_blocks.json").write_text(json.dumps({"tag": TAG, "rows": rows}, indent=1) + "\n")
    print(f"recorded {len(rows)} rows from {TAG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
