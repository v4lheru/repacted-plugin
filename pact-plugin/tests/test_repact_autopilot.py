"""repact-autopilot: the model-free mechanics of /repacted:autopilot, one test per behaviour.

Location: pact-plugin/tests/test_repact_autopilot.py. bin/repact-autopilot keeps the run in
<session dir>/autopilot.jsonl (hooks/shared/autopilot_run.py): `start` records the limits, `next`
prints the top ready task of the task log or a stop reason, `begin` is the per-task checkpoint,
`record` an outcome, `report` the morning summary. Each test builds a temp repo with its own task
log, a temp CLAUDE_CONFIG_DIR with one session dir and, where spend matters, a lead transcript.
Run: tcount pytest -q tests/test_repact_autopilot.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from shared import autopilot_run, session_journal, unattended, workflow_state

PLUGIN = Path(__file__).resolve().parent.parent
BIN_DIR = PLUGIN / "bin"
SESSION = "11111111-2222-3333-4444-555555555555"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
REF = re.compile(r"T-[0-9a-f]{8}")
# 1,000,000 output tokens of Opus 5.5 cost $20 at the price table's rate (tests/test_session_cost_cap.py).
TWENTY_USD = json.dumps({"type": "assistant", "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {
    "input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    "output_tokens": 1_000_000}}}) + "\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A temp git repo with a task log, a session dir in unattended mode, an empty transcript."""
    repo = tmp_path / "proj"
    repo.mkdir()
    for args in (("init", "-q", "-b", "main"), ("commit", "-q", "--allow-empty", "-m", "root")):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
    config = tmp_path / "claude"
    session_dir = config / "pact-sessions" / "proj" / SESSION
    session_dir.mkdir(parents=True)
    transcript = config / "projects" / "-proj" / f"{SESSION}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("")
    for name in ("PACT_SESSION_COST_CAP_USD", "REPACT_UNATTENDED", "REPACT_AUTOPILOT_NOW",
                 "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    unattended.write_flag(str(session_dir), True, "2026-10-18T10:00:00Z")
    run_bin("repact-task", "init", repo=repo)
    return {"repo": repo, "session_dir": session_dir, "transcript": transcript}


def run_bin(name: str, *args: str, repo: Path, env: dict = None, check: bool = True):
    done = subprocess.run([sys.executable, str(BIN_DIR / name), *args], capture_output=True, text=True,
                          cwd=repo, env={**os.environ, **GIT_ENV, **(env or {})}, timeout=60)
    if check:
        assert done.returncode == 0, done.stderr
    return done


def add(world, title: str, *extra: str) -> str:
    out = run_bin("repact-task", "add", title, *(extra or ("--priority", "P2")), repo=world["repo"]).stdout
    return REF.search(out).group(0)


def pilot(world, *args: str, env: dict = None) -> subprocess.CompletedProcess:
    return run_bin("repact-autopilot", *args, "--session-dir", str(world["session_dir"]),
                   repo=world["repo"], env=env, check=False)


def next_task(world, env: dict = None) -> dict:
    done = pilot(world, "next", env=env)
    assert done.returncode in (0, 1), done.stderr
    return json.loads(done.stdout)


def test_next_picks_the_top_ready_task_and_never_a_waiting_one(world):
    low = add(world, "Tidy the logs", "--priority", "P3")
    top = add(world, "Fix the login bug", "--priority", "P1")
    add(world, "Ship the login fix", "--priority", "P0", "--depends", top)
    assert pilot(world, "start").returncode == 0
    picked = next_task(world)
    assert picked["ref"] == top and picked["title"] == "Fix the login bug" and picked["resume"] is False
    assert low != top


def test_next_skips_a_halted_task_and_its_dependants(world):
    halted = add(world, "Pick the cache backend", "--priority", "P0")
    add(world, "Warm the cache", "--priority", "P0", "--depends", halted)
    asked = add(world, "Rename the module", "--priority", "P1")
    other = add(world, "Write the docs", "--priority", "P3")
    assert pilot(world, "start").returncode == 0
    assert pilot(world, "begin", halted).returncode == 0
    assert pilot(world, "record", halted, "halted", "--reason", "High variety").returncode == 0
    assert session_journal.append_event(session_journal.make_event(
        "unattended_halt", task_id=asked, question="Merge the branch"), session_dir=str(world["session_dir"]))
    assert next_task(world)["ref"] == other


@pytest.mark.parametrize("reason", ["queue_empty", "past_until", "max_tasks", "unattended_off",
                                    "max_usd", "cost_cap", "spend_unknown"])
def test_each_stop_reason_stops_the_loop(world, reason):
    first = add(world, "Fix the login bug")
    add(world, "Write the docs")
    start, env = ["start"], {}
    if reason == "queue_empty":
        for ref in REF.findall(run_bin("repact-task", "next", "--all", repo=world["repo"]).stdout):
            run_bin("repact-task", "drop", ref, repo=world["repo"])
    if reason == "past_until":
        start += ["--until", "07:00"]
        env["REPACT_AUTOPILOT_NOW"] = "2026-10-18T06:00:00+00:00"
    if reason == "max_tasks":
        start += ["--max-tasks", "1"]
    if reason in ("max_usd", "spend_unknown"):
        start += ["--max-usd", "5"]
    assert pilot(world, *start, env=env).returncode == 0
    if reason == "past_until":
        env["REPACT_AUTOPILOT_NOW"] = "2026-10-20T06:00:00+00:00"
    if reason == "max_tasks":
        assert pilot(world, "record", first, "done", "--branch", "feat/login").returncode == 0
    if reason == "unattended_off":
        unattended.write_flag(str(world["session_dir"]), False, "2026-10-18T11:00:00Z")
    if reason in ("max_usd", "cost_cap"):
        world["transcript"].write_text(TWENTY_USD)
        env["PACT_SESSION_COST_CAP_USD"] = "10" if reason == "cost_cap" else ""
    if reason == "spend_unknown":
        world["transcript"].unlink()
    done = pilot(world, "next", env=env)
    assert done.returncode == 1, done.stdout + done.stderr
    assert json.loads(done.stdout)["stop"] == reason
    again = pilot(world, "next", env=env)
    assert again.returncode == 1 and json.loads(again.stdout)["stop"] == reason


def test_start_refuses_a_dollar_limit_it_cannot_measure(world):
    world["transcript"].unlink()
    refused = pilot(world, "start", "--max-usd", "5")
    assert refused.returncode == 3 and "spend" in refused.stderr
    assert not (world["session_dir"] / "autopilot.jsonl").exists()


def test_start_refuses_when_unattended_mode_is_off(world):
    unattended.write_flag(str(world["session_dir"]), False, "2026-10-18T11:00:00Z")
    refused = pilot(world, "start")
    assert refused.returncode == 3 and "unattended" in refused.stderr
    command = (PLUGIN / "commands" / "autopilot.md").read_text(encoding="utf-8")
    assert "repact-autopilot" in command and "exit 3" in command


def test_a_begun_task_with_no_record_is_resumed_after_a_compaction(world):
    ref = add(world, "Fix the login bug", "--priority", "P1")
    add(world, "Write the docs", "--priority", "P3")
    assert pilot(world, "start", "--max-tasks", "5").returncode == 0
    assert pilot(world, "begin", ref).returncode == 0
    run_bin("repact-task", "start", ref, repo=world["repo"])
    picked = next_task(world)
    assert picked["ref"] == ref and picked["resume"] is True
    state = {"built_at": "2026-10-18T12:00:00Z", "team": "t", "tasks": {}}
    screen = workflow_state.render(state, "fresh", str(world["session_dir"]))
    assert re.search(rf"Autopilot: .*{ref} in progress.*/repacted:autopilot", screen)


def test_record_and_report_round_trip(world):
    done_ref = add(world, "Fix the login bug", "--priority", "P1")
    halted_ref = add(world, "Pick the cache backend", "--priority", "P2")
    assert pilot(world, "start", "--pr").returncode == 0
    assert pilot(world, "record", done_ref, "done", "--branch", "feat/login",
                 "--pr", "https://github.com/o/r/pull/7").returncode == 0
    assert session_journal.append_event(session_journal.make_event(
        "unattended_halt", task_id=halted_ref, question="Which backend"), session_dir=str(world["session_dir"]))
    assert pilot(world, "record", halted_ref, "halted").returncode == 0
    text = pilot(world, "report").stdout
    assert re.search(rf"done \(1\):\n  {done_ref} \"Fix the login bug\" branch feat/login, "
                     r"PR https://github.com/o/r/pull/7", text)
    assert re.search(rf"halted \(1\):\n  {halted_ref} \"Pick the cache backend\" inbox #\d+", text)
    assert "spend this run: $0.00" in text
    report = json.loads(pilot(world, "report", "--json").stdout)
    assert [row["ref"] for row in report["done"]] == [done_ref] and report["pr"] is True
    bad = pilot(world, "record", done_ref, "done", "--pr", "javascript:alert(1)")
    assert bad.returncode == 2


def decide_items(world) -> list:
    done = subprocess.run(["bash", str(BIN_DIR / "repact"), "decide", "--json", "--session-dir",
                           str(world["session_dir"])], cwd=world["repo"], capture_output=True, text=True,
                          timeout=60, env={**os.environ, **GIT_ENV})
    assert done.returncode == 0, done.stderr
    return [item for item in json.loads(done.stdout)["items"] if item["source"] != "kb"]


def test_a_halt_with_a_question_parks_its_row_for_the_owner_in_decide(world):
    """The halt lived only in the session journal, `repact decide` said 'inbox:
    empty' once the mode was off, and the task log still offered the row as next."""
    asked = add(world, "Push README word counts", "--priority", "P0")
    other = add(world, "Write the docs", "--priority", "P3")
    assert pilot(world, "start").returncode == 0
    assert pilot(world, "begin", asked).returncode == 0
    question = "May this run read METRICS_API_TOKEN and POST the counts?"
    assert run_bin("repact-unattended", "halt", "--task", asked, "--question", question,
                   "--session-dir", str(world["session_dir"]), repo=world["repo"]).returncode == 0
    done = pilot(world, "record", asked, "halted")
    assert done.returncode == 0 and "parked for a human" in done.stdout, done.stderr
    assert "blocked" in run_bin("repact-task", "show", asked, repo=world["repo"]).stdout
    # the owner turns the mode off: the journal halt is answered, the parked row still asks
    unattended.write_flag(str(world["session_dir"]), False, "2026-10-18T11:00:00Z")
    items = decide_items(world)
    assert [(item["source"], item["task"]) for item in items] == [("task-log", asked)], items
    assert question in items[0]["question"]
    offered = run_bin("repact-task", "next", repo=world["repo"], check=False).stdout
    assert other in offered and asked not in offered, offered    # a later session never takes it again
    # answered the way a parked row is: by the owner, at their own terminal (task_log.parked_for_owner)
    answered = subprocess.run(["bash", str(BIN_DIR / "repact"), "decide", asked, "set", "--status", "active"],
                              cwd=world["repo"], capture_output=True, text=True, timeout=60,
                              env={**os.environ, **GIT_ENV})
    assert answered.returncode == 65 and "[rule: task_log.parked_for_owner]" in answered.stderr


def test_a_halt_while_the_mode_is_on_is_listed_once(world):
    asked = add(world, "Push README word counts", "--priority", "P0")
    assert pilot(world, "start").returncode == 0
    assert pilot(world, "record", asked, "halted", "--reason", "needs the owner's token").returncode == 0
    assert session_journal.append_event(session_journal.make_event(
        "unattended_halt", task_id=asked, question="Which token"), session_dir=str(world["session_dir"]))
    assert [item["task"] for item in decide_items(world)] == [asked]


def test_record_halted_with_no_reason_and_no_question_is_refused(world):
    """`record halted` once left no reason or question anywhere."""
    ref = add(world, "Refuse a negative --top", "--priority", "P0")
    assert pilot(world, "start").returncode == 0
    done = pilot(world, "record", ref, "halted")
    assert done.returncode == 3 and "--reason" in done.stderr and "repact-unattended halt" in done.stderr
    assert "blocked" not in run_bin("repact-task", "show", ref, repo=world["repo"]).stdout
    assert json.loads(pilot(world, "report", "--json").stdout)["halted"] == []
    assert pilot(world, "record", ref, "failed", "--reason", "x").returncode == 3


def test_a_task_recorded_done_with_a_waiver_is_parked_and_never_picked_again_in_the_run(world):
    closed = add(world, "Fix the login bug", "--priority", "P0")
    held = add(world, "Write the docs", "--priority", "P1")
    last = add(world, "Tidy the logs", "--priority", "P3")
    subprocess.run(["git", "-C", str(world["repo"]), "branch", "feat/login"], check=True,
                   capture_output=True, env={**os.environ, **GIT_ENV})
    head = subprocess.run(["git", "-C", str(world["repo"]), "rev-parse", "feat/login"], check=True,
                          capture_output=True, text=True).stdout.strip()
    assert pilot(world, "start").returncode == 0
    assert next_task(world)["ref"] == closed
    # The row links no platform task, so the close gate (tests/test_close_gate.py) refuses it, and
    # unattended a waiver parks the row for a human instead of closing it.
    parked = pilot(world, "record", closed, "done", "--branch", "feat/login",
                   "--waive", "no platform task in this fixture")
    assert parked.returncode == 0 and "parked for a human" in parked.stdout
    shown = run_bin("repact-task", "show", closed, repo=world["repo"]).stdout
    assert "blocked" in shown and f"commit:{head}" not in shown
    assert next_task(world)["ref"] == held
    # A PR URL alone is no task-log evidence: the row stays open, held for the run, and said so.
    assert pilot(world, "record", held, "done", "--pr", "https://github.com/o/r/pull/8").returncode == 0
    assert next_task(world)["ref"] == last
    report = json.loads(pilot(world, "report", "--json").stdout)
    assert [(row["ref"], row["closed"]) for row in report["done"]] == [(closed, False), (held, False)]
    assert re.search(rf"{held} \"Write the docs\" PR \S+ \(recorded done, task still open: ",
                     pilot(world, "report").stdout)


def test_a_done_task_whose_acceptance_found_no_evidence_stays_open_and_the_report_says_why(world):
    ref = add(world, "Fix the login bug", "--priority", "P0")
    assert pilot(world, "start").returncode == 0
    run_id = autopilot_run.current_run(autopilot_run.read_rows(str(world["session_dir"])))["start"]["id"]
    assert autopilot_run.append(str(world["session_dir"]), {"kind": "evidence_hold", "run": run_id, "ref": ref,
                                                           "why": "no evidence in an unattended run"})
    subprocess.run(["git", "-C", str(world["repo"]), "branch", "feat/login"], check=True,
                   capture_output=True, env={**os.environ, **GIT_ENV})
    done = pilot(world, "record", ref, "done", "--branch", "feat/login")
    assert done.returncode == 0 and "task-log row left open: no evidence" in done.stdout
    assert "done" not in run_bin("repact-task", "show", ref, repo=world["repo"]).stdout
    report = json.loads(pilot(world, "report", "--json").stdout)
    assert [(row["ref"], row["closed"]) for row in report["done"]] == [(ref, False)]
    assert "no evidence in an unattended run" in pilot(world, "report").stdout


def test_report_shows_titles_after_the_rows_close_from_any_directory(world, tmp_path):
    ref = add(world, "Fix the login bug", "--priority", "P1")
    assert pilot(world, "start").returncode == 0
    assert pilot(world, "record", ref, "done").returncode == 0
    run_bin("repact-task", "done", ref, "--evidence", "commit:" + subprocess.run(
        ["git", "-C", str(world["repo"]), "rev-parse", "HEAD"], check=True, capture_output=True,
        text=True).stdout.strip(), repo=world["repo"])
    elsewhere = run_bin("repact-autopilot", "report", "--session-dir", str(world["session_dir"]),
                        repo=tmp_path)
    assert f'{ref} "Fix the login bug"' in elsewhere.stdout


def test_the_command_keeps_every_task_on_the_team_path_and_runs_literal_commands():
    text = (PLUGIN / "commands" / "autopilot.md").read_text(encoding="utf-8")
    assert "direct-edit lane does not apply" in text and "metadata.project_task" in text
    assert "metadata.acceptance" in text and "never through a shell function or variable" in text
    assert not re.search(r"\bA=|\$A [a-z]", text)
    assert "outside `--max-usd`" in text


def test_a_report_before_next_stops_the_run_names_the_task_left(world):
    """The lead ended the run after one halted task with ready tasks queued.
    Only `next` ends a run; a report taken earlier says what `next` would take."""
    first = add(world, "Refuse a negative --top", "--priority", "P0")
    second = add(world, "Count several files", "--priority", "P1")
    assert pilot(world, "start").returncode == 0
    assert next_task(world)["ref"] == first
    assert pilot(world, "record", first, "halted", "--reason", "blocked by a guard").returncode == 0
    text = pilot(world, "report").stdout
    assert f"still running: `next` would take {second}" in text, text
    assert json.loads(pilot(world, "report", "--json").stdout)["next"] == {"ref": second}
    assert next_task(world)["ref"] == second
    assert pilot(world, "record", second, "failed").returncode == 0
    assert next_task(world)["stop"] == "queue_empty"
    text = pilot(world, "report").stdout
    assert "stopped (queue_empty" in text and "would take" not in text


def test_a_recorded_outcome_leaves_the_main_checkout_clean(world):
    """After a run the main checkout held an uncommitted task log, the ledger's
    union line in .gitattributes and an untracked .repacted/ledger.jsonl (the acceptance bridge
    writes both at accept, before `record`)."""
    repo = world["repo"]
    git = ["git", "-C", str(repo)]
    env = {**os.environ, **GIT_ENV}
    subprocess.run([*git, "add", "-A"], check=True, capture_output=True, env=env)
    subprocess.run([*git, "commit", "-q", "-m", "Set up the task log."], check=True, capture_output=True, env=env)
    ref = add(world, "Push README word counts", "--priority", "P0")
    assert pilot(world, "start").returncode == 0
    assert pilot(world, "begin", ref).returncode == 0
    (repo / ".repacted" / "ledger.jsonl").write_text('{"v": 1}\n')       # what the bridge appends
    with (repo / ".gitattributes").open("a") as handle:
        handle.write(".repacted/ledger.jsonl merge=union\n")
    done = pilot(world, "record", ref, "halted", "--reason", "needs the owner's token")
    assert done.returncode == 0, done.stderr
    dirty = subprocess.run([*git, "status", "--porcelain", "--", ".repacted", ".gitattributes"],
                           capture_output=True, text=True, env=env).stdout
    assert dirty == "", dirty
