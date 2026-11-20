"""bin/repact-replay: the corpus is private and holds no home path, a tree against itself gives
no difference, a changed decision is one row that never echoes the frame, a dispatcher tree
compares at event level, and a run leaves the live directories byte-identical.

Every test builds its world in a temporary directory: a synthetic config dir (task files, a
session journal), a synthetic plugin data dir (a run ledger), and small plugin trees whose
hooks are written here. Nothing reads the live config dir, and no real hook runs.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

# Slow tier: deselected by the default full run, included by `run-tests --full --all`.
pytestmark = pytest.mark.slow

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPLAY = PLUGIN_ROOT / "bin" / "repact-replay"
TEAM = "session-aaaa1111"
SESSION = "11111111-2222-3333-4444-555555555555"
TASK_TEXT = "MARKER-TASK-TEXT-that-must-not-be-printed"
COMMAND_TEXT = "MARKER-COMMAND-TEXT-that-must-not-be-printed"
JOB_TEXT = "background run"       # the description of the built-in scenario's job

READ_FRAME = """import json, sys
try:
    frame = json.load(sys.stdin)
except Exception:
    frame = {}
frame = frame if isinstance(frame, dict) else {}
"""
ALLOW = READ_FRAME + 'print(json.dumps({"suppressOutput": True}))\n'
BLOCK_ECHOING_FRAME = READ_FRAME + """import datetime
print(json.dumps({"decision": "block", "reason": "refused at " + datetime.datetime.now().isoformat()
                  + " over " + json.dumps(frame.get("background_tasks"))}))
"""
BLOCK = READ_FRAME + 'print(json.dumps({"decision": "block", "reason": "a job is running"}))\n'
NOTE = READ_FRAME + 'print(json.dumps({"systemMessage": "one note"}))\n'
DISPATCH = READ_FRAME + ('print(json.dumps({"decision": "block", "reason": "a job is running", '
                         '"systemMessage": "one note"}))\n')
CRASH_ON_ODD = "import json, sys\nprint(json.dumps({'reason': json.load(sys.stdin)['session_id'].upper()}))\n"


def load_engine():
    spec = importlib.util.spec_from_file_location("_replay_under_test", PLUGIN_ROOT / "bin" / "_replay.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def replay(*args, env=None, cwd=None):
    return subprocess.run([sys.executable, str(REPLAY), *map(str, args)], capture_output=True,
                          text=True, env=env, cwd=cwd, timeout=300)


def make_tree(root: Path, stop_hooks: dict, commands: list | None = None) -> Path:
    """A plugin root whose Stop event runs the given {script name: source}, in order."""
    hooks = root / "hooks"
    hooks.mkdir(parents=True)
    entries = []
    for name, source in stop_hooks.items():
        (hooks / name).write_text(source, encoding="utf-8")
        entries.append({"type": "command", "command": f'python3 "${{CLAUDE_PLUGIN_ROOT}}/hooks/{name}"'})
    entries += [{"type": "command", "command": command} for command in commands or []]
    (hooks / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": entries}]}}))
    return root


def tree_digest(*roots: Path) -> dict:
    """{path: (mode, sha256)} for every file and directory under the roots."""
    found = {}
    for root in roots:
        for path in sorted([root, *root.rglob("*")]):
            info = path.lstat()
            body = hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else ""
            found[str(path)] = (stat.S_IMODE(info.st_mode), body)
    return found


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """A synthetic config dir and data dir, and the corpus built from them."""
    base = tmp_path_factory.mktemp("replay")
    home = base / "home"
    config = home / ".claude"
    tasks = config / "tasks" / TEAM
    tasks.mkdir(parents=True)
    wait = {"reason": "awaiting_lead_completion", "expected_resolver": "lead",
            "since": "2026-01-01T00:00:00+00:00", "covers_since": "2026-01-01T00:00:00+00:00"}
    (tasks / "1.json").write_text(json.dumps({
        "id": "1", "subject": TASK_TEXT, "description": f"see {home}/work/notes", "status": "completed",
        "owner": "coder-1", "blocks": [], "blockedBy": [],
        "metadata": {"handoff": {"produced": ["a.py"]}, "intentional_wait": wait, "amend_1": "x"}}))
    (tasks / "2.json").write_text("{ not json")
    session = config / "pact-sessions" / "proj" / SESSION
    (session / "task-files-backup").mkdir(parents=True)
    (session / "task-files-backup" / "7.json").write_text(json.dumps(
        {"id": "7", "subject": "old", "status": "in_progress", "owner": "coder-2", "metadata": {}}))
    (session / "session-journal.jsonl").write_text("".join(json.dumps(row) + "\n" for row in (
        {"type": "session_start", "team": TEAM, "session_id": SESSION},
        {"type": "background_stop_gate", "role": "lead", "verdict": "block", "running": 2,
         "ids": ["job-real-1"]})))
    data = base / "data"
    (data / "runs").mkdir(parents=True)
    (data / "runs" / "ledger.jsonl").write_text(json.dumps(
        {"cmd": f"cd {home}/work && pytest -q {COMMAND_TEXT}", "cwd": f"{home}/work",
         "session_id": SESSION, "team": TEAM, "agent_name": "coder-1", "exit_code": 0}) + "\n")
    corpus = base / "corpus"
    env = {**os.environ, "HOME": str(home)}
    built = replay("corpus", "--out", corpus, "--config-dir", config, "--data-dir", data, env=env)
    assert built.returncode == 0, built.stderr
    return {"base": base, "home": home, "config": config, "data": data, "corpus": corpus,
            "built": built.stdout, "env": env}


def test_corpus_is_private_counts_every_source_and_stores_no_home_path(world, tmp_path):
    corpus = world["corpus"]
    manifest = json.loads((corpus / "manifest.json").read_text())
    assert all(manifest["frames"][source] > 0 for source in
               ("tasks", "backup", "journal", "ledger", "odd", "scenario")), manifest
    assert stat.S_IMODE(corpus.stat().st_mode) == 0o700
    assert stat.S_IMODE((corpus / "frames.jsonl").stat().st_mode) == 0o600
    assert TASK_TEXT not in world["built"] and COMMAND_TEXT not in world["built"]
    stored = (corpus / "frames.jsonl").read_text() + "".join(
        p.read_text() for p in (corpus / "states").rglob("*.json"))
    assert COMMAND_TEXT in stored and TASK_TEXT in stored
    assert str(world["home"]) not in stored and "@HOME@/work/notes" in stored
    events = {(json.loads(line)["source"], json.loads(line)["event"])
              for line in (corpus / "frames.jsonl").read_text().splitlines()}
    assert {("tasks", "PreToolUse"), ("tasks", "PostToolUse"), ("tasks", "TaskCompleted"),
            ("tasks", "Stop"), ("tasks", "SubagentStop"), ("tasks", "SessionStart"),
            ("journal", "Stop"), ("ledger", "PreToolUse"), ("ledger", "PostToolUse")} <= events

    checkout = tmp_path / "repo"
    (checkout / ".git").mkdir(parents=True)
    refused = replay("corpus", "--out", checkout / "corpus", "--config-dir", world["config"],
                     "--data-dir", world["data"], env=world["env"])
    assert refused.returncode == 64 and "git checkout" in refused.stderr
    assert not (checkout / "corpus").exists()


def test_same_tree_gives_zero_and_a_changed_decision_is_a_row_without_frame_text(world, tmp_path):
    blocking = make_tree(tmp_path / "a", {"gate.py": BLOCK_ECHOING_FRAME})
    allowing = make_tree(tmp_path / "b", {"gate.py": ALLOW})
    frames = ["--frame", "scenario-00001", "--frame", "scenario-00002"]

    same = replay("run", "--a", blocking, "--b", blocking, "--corpus", world["corpus"], *frames)
    assert same.returncode == 0, same.stdout + same.stderr
    assert "differences: 0 in 0 of 2 frames (4 hook runs)" in same.stdout

    changed = replay("run", "--a", blocking, "--b", allowing, "--corpus", world["corpus"], *frames)
    assert changed.returncode == 1, changed.stdout + changed.stderr
    rows = [line.split("\t") for line in changed.stdout.splitlines() if "\t" in line]
    assert ["scenario-00001", "Stop", "gate.py", "decision", "block", "-"] in rows
    assert ["scenario-00001", "Stop", "event", "blocked", "yes", "no"] in rows
    reason = next(row for row in rows if row[:4] == ["scenario-00001", "Stop", "gate.py", "reason"])
    assert reason[4].startswith("refused at <TS> over ") and "<frame>" in reason[4]
    assert JOB_TEXT not in changed.stdout and "sleep 600" not in changed.stdout
    assert all(len(value) <= 80 for row in rows for value in row[4:])
    assert "by event: Stop " in changed.stdout and "by hook: event " in changed.stdout


def test_one_dispatcher_entry_equals_two_entries_at_event_level(world, tmp_path):
    two = make_tree(tmp_path / "two", {"gate.py": BLOCK, "note.py": NOTE})
    one = make_tree(tmp_path / "one", {"dispatch.py": DISPATCH})
    args = ["run", "--a", two, "--b", one, "--corpus", world["corpus"], "--frame", "scenario-00001"]

    by_event = replay(*args, "--level", "event")
    assert by_event.returncode == 0, by_event.stdout + by_event.stderr

    by_hook = replay(*args)
    assert by_hook.returncode == 1
    assert "scenario-00001\tStop\tdispatch.py\tregistered\tno\tyes" in by_hook.stdout
    assert "\tevent\t" not in by_hook.stdout


def test_an_async_hook_differs_only_by_what_reaches_the_model(world, tmp_path):
    def async_tree(name: str, source: str) -> Path:
        tree = make_tree(tmp_path / name, {"gate.py": ALLOW, "index.py": source})
        hooks = json.loads((tree / "hooks" / "hooks.json").read_text())
        hooks["hooks"]["Stop"][0]["hooks"][1]["async"] = True
        (tree / "hooks" / "hooks.json").write_text(json.dumps(hooks))
        return tree

    quiet = async_tree("quiet", READ_FRAME)
    # A cache written, another exit code: what load changes in a background job.
    busy = async_tree("busy", READ_FRAME + "import os\ndata = os.environ['CLAUDE_PLUGIN_DATA']\n"
                      "os.makedirs(data, exist_ok=True)\n"
                      "open(data + '/cache.json', 'w').write('{}')\nsys.exit(3)\n")
    crashing = async_tree("crashing", READ_FRAME + "raise SystemError('index')\n")
    frame = ["--frame", "scenario-00001"]

    same = replay("run", "--a", quiet, "--b", busy, "--corpus", world["corpus"], *frame)
    assert same.returncode == 0, same.stdout + same.stderr
    crashed = replay("run", "--a", quiet, "--b", crashing, "--corpus", world["corpus"], *frame)
    assert crashed.returncode == 1
    assert "scenario-00001\tStop\tindex.py\ttraceback\tno\tyes" in crashed.stdout


