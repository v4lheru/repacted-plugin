"""
Location: pact-plugin/tests/test_unattended_binding.py
Summary: Regression tests for the unattended binding: unattended is read from the
caller's own session and stays on once on (an env value, a removed or rewritten flag, or a
made-up session dir never turns it off); the ops CLI acts on the caller's own session and team
only; an unattended reject, a repact-task change or `repact-unattended off` never clears a park or
the mode from inside the run; and the lead's frame cannot hand in a coder's work, by the launcher
word or by the ops script's path. Each test was red before the fix (the bypass worked).
Used by: the pytest suite. Positive controls: the attended waiver
(test_waiver_park.py::test_attended_waive_completes_and_the_row_closes_with_the_waiver), the
backstop relay (test_task_write_guard.py::test_backstop_relays_a_valid_block), and the rows below
marked "positive".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

import ops_frame_guard
import shared.session_init_lead as session_init_lead
from fixtures.ops_env import SESSION_ID, TEAM, World, handoff, ops_env, teachback  # noqa: F401  # fixture
from shared import owner_terminal
from shared import unattended
from shared.ops import OpError, accept, frame, records, reject, submit
from shared.ops import evidence as EV
from shared.task_log import close_gate as CG
from shared.task_log import write as W

BIN = Path(__file__).resolve().parent.parent / "bin"
WHY = "looks fine to me"
AT = "2026-10-19T00:00:00Z"
FAILING = {"problems": ["no passing tcount run of the task since dispatch"], "notes": []}


@pytest.fixture
def world(ops_env: World, monkeypatch, tmp_path):
    """A dispatched task with its HANDOFF in, a task-log row bound to it, and a failing check."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    store = W.init(ops_env.repo)
    ref = store.add("Fix the empty-input parser bug")
    task_id = ops_env.dispatch(refs=[ref])
    submit.teachback(ops_env.coder(), task_id, teachback())
    submit.handoff(ops_env.coder(), task_id, handoff(ops_env.worktree(task_id), refs={ref: "done: parser.py"}))
    monkeypatch.setattr(EV, "verdict", lambda *a, **k: dict(FAILING))
    return ops_env, ref, task_id


def _ops(world_: World, *args, env=None, cwd=None):
    return subprocess.run([sys.executable, str(BIN / "_repact_ops.py"), *args], cwd=str(cwd or world_.repo),
                          capture_output=True, text=True, timeout=120, env=env or dict(os.environ))


def _load(name: str):
    loader = SourceFileLoader(f"_{name.replace('-', '_')}_under_test", str(BIN / name))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def _launched_unattended(world_: World, monkeypatch) -> None:
    """The owner started the session with REPACT_UNATTENDED=1 (no flag file): session start runs."""
    monkeypatch.setenv(unattended.ENV_NAME, "1")
    monkeypatch.setattr(session_init_lead, "reconstruct_session_dir", lambda *_: str(world_.session_dir))
    assert session_init_lead.unattended_context(SESSION_ID, str(world_.repo), "startup")


def _parked(world_: World, task_id: str) -> bool:
    task = world_.task(task_id)
    return task["status"] == "in_progress" and "parked" in task["metadata"] \
        and "evidence_waiver" not in task["metadata"]


# --- sticky once on ------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["0", "", "False", " 0", "no"])
def test_a_session_launched_unattended_stays_so_whatever_the_env_says_later(world, monkeypatch, value):
    w, _ref, task_id = world
    _launched_unattended(w, monkeypatch)
    monkeypatch.setenv(unattended.ENV_NAME, value)          # the REPACT_UNATTENDED=0 prefix
    assert "parked" in accept.run(w.ctx(), task_id, waive=WHY).lines[0]
    assert _parked(w, task_id)


def test_the_env_prefix_through_the_cli_still_parks(world, monkeypatch):
    w, _ref, task_id = world
    _launched_unattended(w, monkeypatch)
    done = _ops(w, "accept", task_id, "--waive", WHY, env={**os.environ, unattended.ENV_NAME: "0"})
    assert _parked(w, task_id), (done.stdout, done.stderr)


@pytest.mark.parametrize("how", ["remove", "rewrite"])
def test_a_removed_or_rewritten_flag_still_reads_on(world, how):
    w, _ref, task_id = world
    unattended.write_flag(str(w.session_dir), True, AT)
    flag = Path(unattended.flag_path(str(w.session_dir)))
    if how == "remove":
        flag.unlink()
    else:
        flag.write_text(json.dumps({"v": 1, "unattended": False, "at": AT, "by": "command"}) + "\n")
    assert unattended.controls_on(str(w.session_dir)).on
    assert "parked" in accept.run(w.ctx(), task_id, waive=WHY).lines[0]


def test_the_close_gate_with_no_session_dir_parks_a_waiver_unless_the_owner_proved_it(world):
    w, ref, _task_id = world
    store = W.Store.locate(w.repo)
    row = dict(store.state().tasks[ref], tags=["autopilot"])
    unknown = CG.check(store.main, row, "", waiver=WHY)
    assert unknown.park and not unknown.waiver
    owner = CG.check(store.main, row, "", waiver=WHY, owner=True)    # positive: the owner's terminal
    assert owner.waiver and not owner.park


# --- the caller's own session only ---------------------------------------------------------

def _bound(team=TEAM, session_dir=None, session_id=None):
    return records.resolve(team, session_dir, session_id, frame.Caller(frame.LEAD), bind=True)


def _refused(**kwargs) -> None:
    with pytest.raises(OpError) as caught:
        _bound(**kwargs)
    assert caught.value.rule == "ops.not_own_session"


def test_a_made_up_session_dir_on_the_cli_is_refused_and_nothing_closes(world, tmp_path):
    """The review probe: the owner turned the mode on, the lead names a decoy dir with no flag."""
    w, _ref, task_id = world
    unattended.write_flag(str(w.session_dir), True, AT)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    done = _ops(w, "accept", task_id, "--waive", WHY, "--session-dir", str(decoy), "--team", TEAM)
    assert done.returncode != 0 and "ops.not_own_session" in done.stderr + done.stdout
    assert w.task(task_id)["status"] == "in_progress"


def test_the_lead_accept_on_its_own_session_through_the_cli_works(world):
    """Positive: no --session-dir/--team, the CLI finds the caller's own session."""
    w, _ref, task_id = world
    done = _ops(w, "accept", task_id, "--waive", WHY)
    task = w.task(task_id)
    assert task["status"] == "completed", (done.stdout, done.stderr)
    assert task["metadata"]["evidence_waiver"]["attended"] is True


def test_other_dirs_ids_and_team_spellings_are_refused(ops_env, tmp_path):
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (tmp_path / "link").symlink_to(decoy)
    other = ops_env.session_dir.parent / "sess-other"      # a real session of the same team
    other.mkdir()
    (other / records.CONTEXT_FILE).write_text(json.dumps({"team_name": TEAM, "session_id": "sess-other"}))
    for session_dir in (decoy, tmp_path / "link", other, f"{ops_env.session_dir}/../sess-other"):
        _refused(session_dir=str(session_dir))
    _refused(session_id="sess-other")
    for team in (TEAM.upper(), "Team-ops", "ｔeam-ops", "tеam-ops", TEAM + " ",
                 unicodedata.normalize("NFD", "téam-ops")):
        _refused(team=team)


def test_the_own_session_dir_by_dotdot_or_symlink_is_allowed(ops_env, tmp_path):
    """Positive: the same dir spelled another way resolves to the caller's own."""
    (tmp_path / "own").symlink_to(ops_env.session_dir)
    for spelling in (f"{ops_env.session_dir}/../{SESSION_ID}", str(tmp_path / "own"), str(ops_env.session_dir)):
        assert _bound(session_dir=spelling).session_dir == ops_env.session_dir.resolve()


def test_at_a_terminal_with_no_session_id_only_a_real_session_dir_passes(ops_env, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    decoy = tmp_path / "config" / "pact-sessions" / "proj" / "decoy"   # under pact-sessions, no context
    decoy.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / records.CONTEXT_FILE).write_text(json.dumps({"team_name": TEAM}))
    for session_dir in (decoy, outside, tmp_path / "config" / "pact-sessions"):
        _refused(session_dir=str(session_dir))
    _refused(session_dir=str(ops_env.session_dir), team="other-team")
    assert _bound(session_dir=str(ops_env.session_dir)).team == TEAM   # positive: the owner names it


# --- an unattended reject keeps the park -----------------------------------------------------

def test_an_unattended_reject_of_a_parked_task_is_refused_and_the_park_stays(world, monkeypatch):
    w, _ref, task_id = world
    monkeypatch.setenv(unattended.ENV_NAME, "1")
    assert "parked" in accept.run(w.ctx(), task_id, waive=WHY).lines[0]
    with pytest.raises(OpError) as caught:
        reject.run(w.ctx(), task_id, "try again")
    assert caught.value.rule == "ops.wrong_state" and "repact decide" in caught.value.problems[0]
    assert "parked" in w.task(task_id)["metadata"]


# --- a row parked for the owner ------------------------------------------------------------

def _park_row(world_, monkeypatch, task_id) -> None:
    monkeypatch.setenv(unattended.ENV_NAME, "1")
    assert "parked" in accept.run(world_.ctx(), task_id, waive=WHY).lines[0]


@pytest.mark.parametrize("argv", [["drop"], ["done", "--evidence", "x"], ["reopen"], ["start"],
                                  ["set", "--status", "planned"], ["drop", "--with-children"]])
@pytest.mark.parametrize("session", ["inside", "no-id"])
def test_repact_task_refuses_to_clear_a_parked_row_without_the_owner(world, monkeypatch, argv, session):
    w, ref, task_id = world
    _park_row(w, monkeypatch, task_id)
    env = dict(os.environ)
    if session == "no-id":                 # env -u CLAUDE_CODE_SESSION_ID: still no terminal
        env.pop("CLAUDE_CODE_SESSION_ID", None)
    done = subprocess.run([sys.executable, str(BIN / "repact-task"), "--repo", str(w.repo), argv[0], ref, *argv[1:]],
                          capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert done.returncode == 65 and "[rule: task_log.parked_for_owner]" in done.stderr, done.stderr
    assert W.Store.locate(w.repo).state().tasks[ref]["status"] == "blocked"


def test_the_owner_at_their_terminal_clears_the_park(world, monkeypatch):
    """Positive: owner_confirms is the terminal proof; with it the owner's drop goes through."""
    w, ref, task_id = world
    _park_row(w, monkeypatch, task_id)
    task_cli = _load("repact-task")
    monkeypatch.setattr(task_cli, "owner_confirms", lambda *a, **k: True)
    assert task_cli.main(["--repo", str(w.repo), "drop", ref]) == 0
    assert W.Store.locate(w.repo).state().tasks[ref]["status"] == "dropped"


# --- `repact-unattended off` is the owner's ----------------------------------------

@pytest.mark.parametrize("session", ["inside", "no-id"])
def test_off_from_inside_the_run_is_refused_and_the_mode_stays_on(ops_env, session):
    folder = str(ops_env.session_dir)
    unattended.write_flag(folder, True, AT)
    env = dict(os.environ)
    if session == "no-id":
        env.pop("CLAUDE_CODE_SESSION_ID", None)
    done = subprocess.run([sys.executable, str(BIN / "repact-unattended"), "off", "--session-dir", folder],
                          capture_output=True, text=True, timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert done.returncode == 3 and "[rule: unattended.off_owner_only]" in done.stderr
    assert unattended.read_mode(folder).on and unattended.was_unattended(folder)


def test_the_owner_off_clears_the_mode_and_the_sticky_marker(ops_env, monkeypatch, capsys):
    """Positive: at the owner's terminal off turns the controls off; a new on starts again."""
    folder = str(ops_env.session_dir)
    unattended.write_flag(folder, True, AT)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    switch = _load("repact-unattended")
    monkeypatch.setattr(switch, "owner_confirms", lambda *a, **k: True)
    assert switch.main(["off", "--session-dir", folder]) == 0
    assert not unattended.controls_on(folder).on and not unattended.was_unattended(folder)


class _Terminal:
    def __init__(self, line: str, tty: bool = True):
        self.line, self.tty, self.written = line, tty, ""

    def isatty(self):
        return self.tty

    def readline(self):
        return self.line

    def write(self, text):
        self.written += text

    def flush(self):
        pass


def test_owner_confirms_needs_no_session_id_two_terminals_and_the_token():
    def confirms(stdin=None, stderr=None, environ=None):
        return owner_terminal.owner_confirms("off", "Type off: ", stdin or _Terminal("off\n"),
                                             stderr or _Terminal(""), {} if environ is None else environ)
    assert confirms()                                                  # positive
    assert not confirms(environ={"CLAUDE_CODE_SESSION_ID": "s"})
    assert not confirms(environ={"CLAUDE_SESSION_ID": "s"})
    assert not confirms(stdin=_Terminal("off\n", tty=False))
    assert not confirms(stderr=_Terminal("", tty=False))
    assert not confirms(stdin=_Terminal("of\n"))
    assert confirms(environ={"CLAUDE_CODE_SESSION_ID": "  "})          # a blank id is no session


# --- the lead's frame -----------------------------------------------------------------

class _Actor:
    def __init__(self, lead: bool, marked: bool, source: str = "unknown", name: str = ""):
        self.is_lead, self.marked, self.source, self.name = lead, marked, source, name

    def runs_lead_ops(self):
        return self.is_lead or (self.source == "unknown" and not self.marked)


LEAD = _Actor(lead=True, marked=False, source="host", name="lead")
CODER_FRAME = _Actor(lead=False, marked=True, source="host", name="coder-1")


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


@pytest.mark.parametrize("command", [
    "repact submit 5 --as coder-1 --handoff /tmp/h.json",
    "repact submit 5 --teachback /tmp/t.json",
    "REPACT_AGENT_NAME=coder-1 repact submit 5 --handoff h.json",
    "python3 bin/_repact_ops.py submit 5 --as coder-1 --teachback t.json",
    "/x/pact-plugin/bin/_repact_ops.py submit 5 --handoff h.json",
])
def test_the_lead_frame_cannot_hand_in_a_coders_work(command):
    assert ops_frame_guard.decide(_bash(command), LEAD).endswith("[rule: ops_frame_guard.lead_submit]")
    assert ops_frame_guard.decide(_bash(command), CODER_FRAME) is None      # positive: the coder submits


@pytest.mark.parametrize("command", ['grep "repact submit" notes.md', "echo repact submit 5", "repact accept 5"])
def test_the_lead_frame_keeps_its_own_ops_and_mentions(command):
    assert ops_frame_guard.decide(_bash(command), LEAD) is None


@pytest.mark.parametrize("command", [
    "./bin/_repact_ops.py accept 5",
    "/x/pact-plugin/bin/_repact_ops.py accept 5",
    '"$CLAUDE_PLUGIN_ROOT/bin/_repact_ops.py" accept 5',
    "cd bin && ./_repact_ops.py accept 5",
    "python3 bin/_repact_ops.py reject 5 --reason x",
    "python3 -u /x/bin/_repact_ops.py amend 5 --text y",
])
def test_a_coder_frame_cannot_run_a_lead_op_by_the_scripts_path(command):
    assert ops_frame_guard.decide(_bash(command), CODER_FRAME).endswith("[rule: ops_frame_guard.lead_only]")
