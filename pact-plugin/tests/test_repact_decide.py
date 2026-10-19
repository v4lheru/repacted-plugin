"""repact decide: one numbered list of every parked question, and decide for task-log rows only.

Location: pact-plugin/tests/test_repact_decide.py. `repact decide` (bin/_front/inbox.py) reads its sources with their
own readers: the task log's ASK flags (hooks/shared/project_tasks.py), the unattended halts of this
project's sessions whose mode is still ON (shared.unattended, shared.session_journal) and the held
distill items of the KB queue (_kb.gate, _kb.decide). A number answers the item the last listing
showed under it (a per-user snapshot under $XDG_STATE_HOME); a missing, stale, gone or changed
item is refused. Each test builds a temp repo, a temp CLAUDE_CONFIG_DIR, a temp state dir and a
temp KB chosen through a temp repacted profile; nothing touches the real ones.
Run: tcount pytest -q tests/test_repact_decide.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from shared import session_journal, unattended

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"


def _load_front():
    """bin/_front as a package; its __init__ puts bin/ and hooks/ on the import path."""
    if "_front" not in sys.modules:
        init = BIN_DIR / "_front" / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            "_front", init, submodule_search_locations=[str(init.parent)])
        module = importlib.util.module_from_spec(spec)
        sys.modules["_front"] = module
        spec.loader.exec_module(module)
    return sys.modules["_front"]


_load_front()
from _kb import gate as G  # noqa: E402  # bin/ is on the import path once _front loaded

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
INERT = "REPACT_TEST_SECRET_" + "ABCDEFGH12"
REF = re.compile(r"T-[0-9a-f]{8}")
LESSON = {"title": "Wombat API drops rows with a negative balance",
          "claim": "The wombat API returns 200 but silently drops a row whose balance is negative.",
          "why": "the importer filters negatives before the write and never reports it",
          "apply": "check the returned row count against the sent count on every import",
          "evidence": {"class": "documented", "how": "pointers: task:7@s"},
          "ctx": "distill", "date": "2026-10-01", "kind": "gotcha",
          "keys": ["wombat api", "negative balance", "drops rows"],
          "summary": "The wombat API returns 200 while dropping negative-balance rows.",
          "proposer": {"session": "s1", "agent": "tester", "repo": None}}


@pytest.fixture
def kb(tmp_path, monkeypatch) -> Path:
    """An empty temp KB selected through the repacted profile, and no denylist files."""
    root = tmp_path / "kb"
    root.mkdir()
    (tmp_path / "xdg" / "repacted").mkdir(parents=True)
    (tmp_path / "xdg" / "repacted" / "profile.json").write_text(json.dumps({"paths": {"kb": str(root)}}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("REPACT_SCRUB_DENYLIST", str(tmp_path / "no-denylist.txt"))
    monkeypatch.setenv("REPACT_SCRUB_DENYLIST_WORDS", str(tmp_path / "no-denylist-words.txt"))
    return root


@pytest.fixture
def repo(kb, tmp_path, monkeypatch):
    """A temp git repo as the project, with its own Claude config dir (for session dirs)."""
    path = tmp_path / "proj"
    path.mkdir()
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "root")):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(path))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return path


def held_item(kb, monkeypatch) -> dict:
    """One distill item the post-check held, proposed by another session."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-proposer")
    queued = G.propose(kb, dict(LESSON), proposer_id="distill", no_auto=True,
                       postcheck={"action": "hold", "reasons": ["proper noun"]})
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    return next(item for item in G.pending(kb) if item["id"] == queued["id"])


def run_bin(name: str, *args: str, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BIN_DIR / name), *args], capture_output=True, text=True,
                          cwd=cwd, env={**os.environ, **env}, timeout=60)


def task(repo, *args) -> subprocess.CompletedProcess:
    done = run_bin("repact-task", *args, cwd=repo, env={**GIT_ENV, "CLAUDE_CODE_SESSION_ID": ""})
    assert done.returncode == 0, done.stderr
    return done


def add(repo, title: str, *extra: str) -> str:
    return REF.search(task(repo, "add", title, "--priority", "P2", *extra).stdout).group(0)


def stuck_task(repo) -> dict:
    """B depends on A, which is dropped (an ASK flag); C depends on B, so C waits on the answer."""
    task(repo, "init")
    a = add(repo, "Pick the cache backend")
    b = add(repo, "Warm the cache on deploy", "--depends", a)
    c = add(repo, "Ship the deploy script", "--depends", b)
    task(repo, "drop", a)
    return {"a": a, "b": b, "c": c}


def halt(repo, question: str = "Merge the branch", task_id: str = "35") -> str:
    session = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "pact-sessions", repo.resolve().name, "s-1")
    os.makedirs(session)
    unattended.write_flag(session, True, "2026-10-18T10:00:00Z")
    assert session_journal.append_event(session_journal.make_event(
        "unattended_halt", task_id=task_id, question=question), session_dir=session)
    return session


def inbox(repo, *args) -> subprocess.CompletedProcess:
    """`repact decide [args]` through the real launcher."""
    return subprocess.run(["bash", str(BIN_DIR / "repact"), "decide", *args], capture_output=True, text=True,
                          cwd=repo, env={**os.environ, **GIT_ENV}, timeout=60)


def test_the_list_numbers_every_source_with_its_task_its_advice_and_what_waits(repo, kb, monkeypatch):
    refs = stuck_task(repo)
    halt(repo, question=f"Merge the branch with {INERT}")
    item = held_item(kb, monkeypatch)
    done = inbox(repo)
    assert done.returncode == 0, done.stderr
    text = done.stdout
    assert text.splitlines()[0].startswith("inbox: 3 question(s)")
    assert re.search(rf"1\. \[task log\] {refs['b']}.*depends on a dropped task", text)
    assert f"decide {refs['b']} dep --on {refs['a']} --remove" in text and f"waits: {refs['c']}" in text
    assert re.search(r"2\. \[halt\] task 35: Merge the branch with \[REDACTED:test-secret\]", text)
    assert INERT not in text
    assert re.search(rf"3\. \[kb\] {item['id']}: held", text)
    assert item["lesson"]["claim"][:40] not in text          # model-written text never printed
    rows = json.loads(inbox(repo, "--json").stdout)["items"]
    assert [(row["n"], row["source"]) for row in rows] == [(1, "task-log"), (2, "halt"), (3, "kb")]
    assert rows[0]["task"] == refs["b"] and rows[0]["waits"] == [refs["c"]]


def test_an_empty_inbox_prints_one_line(repo):
    done = inbox(repo)
    assert done.returncode == 0 and done.stdout.splitlines() == ["inbox: empty, no question waits"]


def test_decide_writes_a_task_log_answer_through_repact_task(repo):
    refs = stuck_task(repo)
    done = inbox(repo, refs["b"], "dep", "--on", refs["a"], "--remove")
    assert done.returncode == 0, done.stderr
    shown = task(repo, "show", refs["b"]).stdout
    assert refs["a"] not in shown and "stuck" not in shown
    assert inbox(repo).stdout.splitlines() == ["inbox: empty, no question waits"]


def test_decide_refuses_a_halt_and_a_kb_item_and_prints_the_users_command(repo, kb, monkeypatch):
    session = halt(repo)
    item = held_item(kb, monkeypatch)
    assert inbox(repo).returncode == 0
    refused = inbox(repo, "1", "set", "--status", "active")
    assert refused.returncode == 3 and f"repact-unattended off --session-dir {session}" in refused.stderr
    refused = inbox(repo, "2", "set", "--status", "active")
    assert refused.returncode == 3 and "vault-note" in refused.stderr and "own terminal" in refused.stderr
    assert unattended.read_mode(session, environ={}).on
    assert [queued["id"] for queued in G.pending(kb)] == [item["id"]]


@pytest.mark.parametrize("answer", [[], ["init"], ["commit"], ["--repo", "/tmp", "set"], ["show"]])
def test_decide_takes_only_a_writing_verb_for_the_row(repo, answer):
    refs = stuck_task(repo)
    before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
    done = inbox(repo, refs["b"], *answer)
    assert done.returncode == 2 and "set, done, drop, dep or reopen" in done.stderr
    assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before and refs


def test_an_unreadable_source_is_one_note_line_and_the_rest_still_list(repo):
    stuck_task(repo)
    halt(repo)
    log = repo / ".repacted" / "tasks.jsonl"
    log.chmod(0)
    try:
        done = inbox(repo)
    finally:
        log.chmod(0o644)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[0].startswith("inbox: 1 question(s)")
    assert "1. [halt] task 35: Merge the branch" in done.stdout
    assert len([line for line in done.stdout.splitlines() if line.startswith("note: task log")]) == 1


# --- a number answers the item the listing showed, or nothing ------------

def test_a_number_answers_the_row_the_last_listing_showed(repo, tmp_path):
    refs = stuck_task(repo)
    assert inbox(repo).returncode == 0
    done = inbox(repo, "1", "dep", "--on", refs["a"], "--remove")
    assert done.returncode == 0, done.stderr
    assert refs["a"] not in task(repo, "show", refs["b"]).stdout
    snapshot = _snapshot(tmp_path).name                 # per-user state, never a file in the repo
    assert not [p for p in repo.rglob("*") if p.name == snapshot or "decide" in p.name]


def _snapshot(tmp_path) -> Path:
    found = list((tmp_path / "state" / "repacted" / "decide").glob("*.json"))
    assert len(found) == 1
    return found[0]


@pytest.mark.parametrize("case", ["no-listing", "stale", "gone", "changed"])
def test_a_number_is_refused_without_a_fresh_listing_of_the_same_item(repo, tmp_path, case):
    refs = stuck_task(repo)
    if case != "no-listing":
        assert inbox(repo).returncode == 0
        path = _snapshot(tmp_path)
        body = json.loads(path.read_text())
        if case == "stale":
            body["at"] -= 2 * 24 * 3600
        if case == "changed":
            body["items"][0]["digest"] = "0" * 16
        path.write_text(json.dumps(body))
        if case == "gone":
            assert inbox(repo, refs["b"], "dep", "--on", refs["a"], "--remove").returncode == 0
    before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
    done = inbox(repo, "1", "set", "--status", "planned")
    assert done.returncode == 2, done.stderr
    assert {"no-listing": "run `repact decide` first", "stale": "over a day old",
            "gone": "no longer open", "changed": "changed since the listing"}[case] in done.stderr
    assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before
