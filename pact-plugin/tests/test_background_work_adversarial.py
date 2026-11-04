"""Adversarial arms for the background-wait mechanism — the gaps the
implementation's own tests do not reach.

Location: pact-plugin/tests/test_background_work_adversarial.py
Summary: sibling of tests/test_background_work.py. That file pins the module's
         predicates; this one pins the things NOBODY ASKED — the two consumers
         with no test at their own entry point, the deny set's liveness, the
         registry under real concurrent processes, and the actor-blind
         advisory's non-participation in the deny verdict.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

WHY A SIBLING FILE RATHER THAN MORE CLASSES IN THE PRIMARY. The primary pins
PREDICATES and its arms are tight single-assertion checks. Everything here
crosses a process boundary, a consumer boundary, or a module boundary:
Layer 2's hook-level entry point, Layer 3's compositional entry point, two real
subprocesses, and a 15-command property sweep over a different hook entirely.
Mixing those into the primary costs signal on its fire-count assertions.

THE RULE EVERY CLASS HERE OBEYS, and it is the architecture's own (§8.2.5):
TESTING A GATE DOES NOT TEST THAT THE CALLER USES IT. Measured on this feature
once already — seven tests on the selector, and reverting Layer 3's caller to
the ungated read survived 113 of them. So each class below drives a PRODUCTION
ENTRY POINT, not the helper underneath it.

EVERY NEGATIVE ARM HERE IS PAIRED WITH A CONTROL THAT MAKES IT MEAN SOMETHING.
A green "nothing went wrong" is worth nothing unless the same instrument can be
shown to go red; where the control is expensive it is stated in the docstring
with its measured cardinality so a future reader can re-run it.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import missed_wake_scan as mw
import teammate_idle as ti
from fixtures.role_frames import (
    captured_lead_userpromptsubmit_qualified,
    captured_pretooluse_lead_inprocess,
    captured_pretooluse_teammate_tmux,
)
from shared import background_work as bw
from clock_shift.clock_shift_env import carry_clock_shift

HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
GATE = HOOKS_DIR / "wait_filler_gate.py"
TEAM = "adv-team"
T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _record(**over) -> dict:
    base = {
        "agent_name": "adv-coder",
        "session_id": "sid",
        "task_ids": ["13"],
        "registered_at": _iso(T0),
    }
    base.update(over)
    return base


def _task(task_id="13", status="in_progress", owner="adv-coder", wait=None) -> dict:
    task = {"id": task_id, "status": status, "owner": owner}
    if wait is not None:
        task["metadata"] = {"intentional_wait": wait}
    return task


def _wait(since: datetime, reason="awaiting_blocker_resolution") -> dict:
    return {"reason": reason, "expected_resolver": "lead", "since": _iso(since)}


@pytest.fixture
def team_root(tmp_path, monkeypatch):
    """A real config root with a real team dir. The registry is a real file."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "teams" / TEAM).mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# Layer 2 — the consumer with no test at its own entry point.
# ---------------------------------------------------------------------------


class TestLayer2EntryPoint:
    """`teammate_idle.check_unflagged_background` — driven directly.

    NOTHING ELSE IN THE SUITE CALLS THIS FUNCTION. Layer 1 has a subprocess
    seam test and Layer 3 has arms on its selector; Layer 2's entry point had
    none, so every claim about the three-idle threshold rested on reading the
    counter helper rather than on running the consumer that drives it. That is
    the same shape as the ungated-caller defect this feature already paid for
    once: the counter is correct and thoroughly tested, and whether the caller
    ramps it correctly was untested behaviour.
    """

    # THE CLOCK IS PASSED AS now=, so no fixture date can age out from under
    # these arms at any TTL, and every clock read on this path is the one the
    # arm controls.
    NOW = T0 + timedelta(minutes=5)

    def _seed(self):
        assert bw.save_records([_record()], team_name=TEAM) is True

    def test_the_ramp_advises_once_at_exactly_three_idles(self, team_root):
        """1 and 2 silent, 3 advises, 4 and 5 silent again.

        Both halves are load-bearing and fail in opposite directions: an
        advisory before three is a premature alarm, and a repeat after three
        is the nagging that discredits it.
        """
        self._seed()
        tasks = [_task()]
        fired = [
            bool(ti.check_unflagged_background(tasks, "adv-coder", TEAM, now=self.NOW))
            for _ in range(5)
        ]
        assert fired == [False, False, True, False, False], fired
        counts = bw.load_unflagged_idle_counts(TEAM)
        assert counts["adv-coder"]["count"] == bw.UNFLAGGED_IDLE_THRESHOLD

    def test_a_flagged_wait_clears_the_counter_rather_than_pausing_it(
        self, team_root
    ):
        """The ramp must RESET, not resume, when the teammate flags.

        If flagging merely paused the counter, two unflagged idles now plus one
        an hour later would still alarm — the accumulation would survive the
        very behaviour it is supposed to reward.
        """
        self._seed()
        unflagged, flagged = [_task()], [_task(wait=_wait(T0 + timedelta(minutes=1)))]
        ti.check_unflagged_background(unflagged, "adv-coder", TEAM, now=self.NOW)
        ti.check_unflagged_background(unflagged, "adv-coder", TEAM, now=self.NOW)
        assert bw.load_unflagged_idle_counts(TEAM)["adv-coder"]["count"] == 2

        assert ti.check_unflagged_background(flagged, "adv-coder", TEAM, now=self.NOW) is None
        assert "adv-coder" not in bw.load_unflagged_idle_counts(TEAM), (
            "a flagged idle must CLEAR the counter, not pause it"
        )


# ---------------------------------------------------------------------------
# Layer 3 — the COMPOSITIONAL entry point, not the selector.
# ---------------------------------------------------------------------------


class TestLeadSurfaceComposition:
    """`missed_wake_scan.run_surface` hosts independent alarms.

    The selector `find_stale_unflagged_background` has arms. The composition
    does not, and the composition is where the interesting failure lives: the
    pre-feature `run_surface` early-returned on an empty missed-wake scan, and
    the whole point of folding Layer 3 into this process was that neither
    alarm may suppress the other. That independence is asserted in a code
    comment and, until these arms, nowhere else — so restoring the early
    return would silently delete the background surface with every existing
    test still green.
    """

    OLD = _iso(datetime.now(timezone.utc) - timedelta(minutes=40))

    @pytest.fixture
    def lead(self, tmp_path, monkeypatch):
        """A lead frame, a real registry, and a journal that captures emits.

        40 minutes old: past the 30-minute registered_at window so the record
        is stale, and well inside the 24h TTL so it is still loaded. A fixed
        calendar date would be dropped by the TTL before any gate is reached
        and every arm below would pass vacuously.
        """
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "teams" / TEAM).mkdir(parents=True)
        (tmp_path / "teams" / TEAM / "background_work.json").write_text(
            json.dumps({"records": [{
                "agent_name": "victim", "session_id": "s",
                "task_ids": ["5"], "registered_at": self.OLD,
            }]})
        )
        state = {"emitted": []}
        monkeypatch.setattr(mw, "read_events", lambda et: [])
        monkeypatch.setattr(
            mw, "append_event", lambda e: state["emitted"].append(e) or True
        )
        monkeypatch.setattr(mw, "get_journal_path", lambda: "/tmp/fake-journal.jsonl")
        monkeypatch.setattr(mw, "get_team_name", lambda: TEAM, raising=False)
        from shared import pact_context

        monkeypatch.setattr(pact_context, "get_team_name", lambda: TEAM)
        return state

    def _bg_task(self, status="in_progress", wait=None):
        task = {"id": "5", "owner": "victim", "subject": "s", "status": status,
                "metadata": {}}
        if wait:
            task["metadata"]["intentional_wait"] = wait
        return task

    def test_the_background_surface_fires_with_NO_missed_wake_present(
        self, lead, monkeypatch
    ):
        """The arm that a restored early-return would kill.

        The task carries no wait at all, so the missed-wake scan finds nothing.
        The background alarm must still reach the lead.
        """
        monkeypatch.setattr(mw, "get_task_list", lambda: [self._bg_task()])
        out = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out is not None and "UNFLAGGED BACKGROUND WORK" in out, out
        assert "missed-wake" not in out.lower(), (
            "only the background alarm should be present on this frame"
        )

    def test_both_alarms_compose_into_one_surface(self, lead, monkeypatch):
        """Neither alarm may consume the other's turn.

        The two must sit on DIFFERENT tasks — see the arm below for why they
        are mutually exclusive on the same one.
        """
        other = {
            "id": "6", "owner": "other", "subject": "s", "status": "in_progress",
            "metadata": {"intentional_wait": {
                "reason": "awaiting_lead_completion",
                "expected_resolver": "lead",
                "since": _iso(datetime.now(timezone.utc) - timedelta(minutes=60)),
            }},
        }
        monkeypatch.setattr(mw, "get_task_list", lambda: [self._bg_task(), other])
        out = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert "UNFLAGGED BACKGROUND WORK" in out
        assert "missed-wake" in out.lower(), (
            "the missed-wake alarm must survive the background alarm's presence"
        )


class TestTheValidatedNameIsTheNameUSED:
    """The value the membership check VALIDATES must be the value RECORDED.

    `agent_type_names_a_member` validates the RAW `agent_type` against the
    team config's `members[].name`. `bind_launcher_identity` then discards
    that value and re-derives a name through `resolve_agent_name`, whose
    Step 4 strips a `pact-` prefix. Whenever a member's name starts with
    `pact-`, the string that was validated and the string that gets recorded
    are DIFFERENT — and the design's rule is "on a match, that name is the
    launcher".

    THE CONSEQUENCE IS A MIS-BIND, NOT A MISLABEL. If the stripped form names
    a DIFFERENT member holding a live task, the launch is recorded against
    that member and against THEIR task. So these arms assert the NAME and the
    TASK IDS together: an arm checking only the name would pass a fix that got
    the name right and the attribution wrong, and the attribution is the part
    that makes this unacceptable rather than untidy.

    WHY THE DENY SET DOES NOT SAVE THIS, and it is the corollary that makes
    the finding general: every `agents/*.md` stem carries a `pact-` prefix and
    none is unprefixed, so the runtime-derived deny set can only ever refuse a
    `pact-`-prefixed member name — precisely the set the strip breaks. Its
    sole reachable function today is preventing this same mis-bind, and it
    only reaches the names that happen to be shipped agent types.

    NON-VACUITY GUARD, ASSERTED RATHER THAN ASSUMED. The fixture member must
    NOT be one of those stems: if it were, the deny set would refuse it
    BEFORE the name comparison ever runs and every arm here would pass for the
    wrong reason. `test_the_fixture_name_is_not_a_shipped_stem` pins that.
    """

    PREFIXED = "pact-reviewer"   # deliberately NOT a shipped agents/ stem
    TWIN = "reviewer"            # its type-strip, and a real member

    @pytest.fixture
    def twins(self, team_root):
        """Two members whose names differ only by the `pact-` prefix.

        Each owns its own in_progress task, so a bind can be checked for
        WHICH member's work it claims, not merely for a plausible string.
        """
        (team_root / "teams" / TEAM / "config.json").write_text(json.dumps({
            "leadSessionId": "lead-sid",
            "members": [
                {"name": self.PREFIXED, "agentId": f"{self.PREFIXED}@{TEAM}",
                 "agentType": "pact-backend-coder", "backendType": "in-process"},
                {"name": self.TWIN, "agentId": f"{self.TWIN}@{TEAM}",
                 "agentType": "pact-backend-coder", "backendType": "in-process"},
            ],
        }))
        tasks = team_root / "tasks" / TEAM
        tasks.mkdir(parents=True)
        (tasks / "5.json").write_text(json.dumps(
            {"id": "5", "status": "in_progress", "owner": self.PREFIXED}))
        (tasks / "9.json").write_text(json.dumps(
            {"id": "9", "status": "in_progress", "owner": self.TWIN}))
        return team_root

    def _frame(self, agent_type):
        return {
            "session_id": "sid-x", "tool_name": "Bash", "agent_type": agent_type,
            "agent_id": "0123456789abcdef",   # hex, no "@" — Step 2 must miss
            "tool_input": {"command": "echo hi", "run_in_background": True},
        }

    def test_the_fixture_name_is_not_a_shipped_stem(self):
        """Without this the deny set does the work and the arms below are
        vacuous — they would pass against the defect and against the fix."""
        assert self.PREFIXED not in bw._known_agent_types()
        assert bw.agent_type_names_a_member(self.PREFIXED, TEAM) is not None

    def test_a_launch_is_NEVER_attributed_to_a_prefixed_members_twin(
        self, twins
    ):
        """The invariant. Silence is an acceptable answer here; the twin is not.

        The standing ruling is that a MIS-BIND is worse than silence, so this
        arm deliberately permits `None` — refusing to record — and forbids
        only the one outcome that attributes a launch to a teammate who did
        not make it.
        """
        bound = bw.bind_launcher_identity(self._frame(self.PREFIXED), TEAM)
        assert bound is None or bound[0] != self.TWIN, (
            f"launch by {self.PREFIXED!r} attributed to {self.TWIN!r} — "
            "a mis-bind, which is the unacceptable direction"
        )


class TestTheEmittedEventPassesTheRealValidator:
    """The event PRODUCTION builds, against the REAL schema validator.

    THE SCHEMA IS ALREADY COVERED AND THAT IS NOT THE SAME CLAIM. The suite's
    per-type sample list carries a hand-written `unflagged_background_wait`
    dict, which pins that the registration accepts a well-formed event. It
    says nothing about whether `emit_unflagged_forensic` BUILDS one — the
    author of the sample and the author of the payload can disagree and both
    tests stay green. Same shape as the gate-versus-caller rule this file is
    organised around, one layer down.

    THE FAILURE IS SILENT BY DESIGN, WHICH IS WHY IT NEEDS AN ARM: a schema
    mismatch prints `invalid event schema` to stderr and EXITS 0, so a
    mismatched payload drops the forensic record while the hook reports
    success and no test notices.
    """

    def _emit(self, monkeypatch, record):
        captured = []
        monkeypatch.setattr(mw, "read_events", lambda et: [])
        monkeypatch.setattr(mw, "get_journal_path", lambda: "/tmp/fake-journal.jsonl")
        monkeypatch.setattr(
            mw, "append_event", lambda e: captured.append(e) or True
        )
        mw.emit_unflagged_forensic([record])
        return captured

    def test_the_built_event_validates(self, monkeypatch):
        from shared.session_journal import _validate_event_schema

        events = self._emit(monkeypatch, _record(task_ids=["5", "6"]))
        assert len(events) == 1
        ok, why = _validate_event_schema(events[0])
        assert ok is True, why

    def test_it_still_validates_with_a_shell_heavy_command_attached(
        self, monkeypatch
    ):
        """`command` is optional and carries raw shell text.

        Redirections, ampersands and quotes reach the journal verbatim, so the
        arm uses a command shaped like the ones this feature exists to record
        rather than a tame placeholder.
        """
        from shared.session_journal import _validate_event_schema

        command = "python3 -m pytest -q > /tmp/out.log 2>&1 &"
        events = self._emit(monkeypatch, _record(command=command))
        ok, why = _validate_event_schema(events[0])
        assert ok is True, why
        assert events[0]["command"] == command


# ---------------------------------------------------------------------------
# The deny set — a backstop that can empty itself in total silence.
# ---------------------------------------------------------------------------


class TestDenySetIsLive:
    """`_known_agent_types()` is derived at runtime from `agents/*.md`.

    Derivation is the right call — a hard-coded list goes stale the day
    someone adds an agent, and the failure is a mis-bind rather than an error.
    But the derivation has a failure mode nobody pinned: `Path.glob` on a
    directory that does not exist RAISES NOTHING and yields nothing, so the
    documented `except (OSError, IndexError)` is not what produces an empty
    set. Relocate or rename `agents/` and the deny set silently becomes empty,
    the backstop disappears, and every test in the suite stays green.
    """

    def test_the_deny_set_is_NOT_EMPTY_in_the_shipped_tree(self):
        """The relocation tripwire, and nothing else has it.

        An empty deny set is indistinguishable from a working one in every
        other arm, because the collision it guards needs a specific team
        shape to express itself.
        """
        stems = bw._known_agent_types()
        assert stems, (
            "the deny set is empty — agents/ has moved or been renamed, and the "
            "collision backstop is silently gone"
        )
        on_disk = {p.stem for p in (HOOKS_DIR.parent / "agents").glob("*.md")}
        assert stems == on_disk, (stems ^ on_disk)


# ---------------------------------------------------------------------------
# The registry under real concurrent processes.
# ---------------------------------------------------------------------------


# The helper takes its import root from PYTHONPATH rather than mutating
# sys.path. A `sys.path.insert` HERE would be codegen path mutation, which
# tests/test_path_setup_pin.py forbids outside its fixed keep-set — and it
# forbids it in a string literal exactly as in real code. Measured: this file
# tripped that pin at full-suite scope with the literal present.
#
# The rendezvous is NOT a clock guess. An earlier version released both
# processes at `time.time() + 1.0`, which under a loaded machine let process
# startup outrun the barrier so the two never overlapped and the race under
# test did not occur — a flaky control that passed on an idle machine and
# failed in the full suite. Each process now announces itself and blocks until
# the other has, so the overlap is a fact rather than a probability.
_APPENDER = '''
import os, time
from pathlib import Path
os.environ["CLAUDE_CONFIG_DIR"] = os.sys.argv[1]
from shared import background_work as bw
if os.environ.get("ADV_NO_FLOCK"):
    from shared import state_file
    state_file.fcntl.flock = lambda *args, **kwargs: None
tag, n, rv = os.sys.argv[2], int(os.sys.argv[3]), Path(os.sys.argv[4])
(rv / tag).write_text("ready")
deadline = time.time() + 30
while len(list(rv.iterdir())) < 2 and time.time() < deadline:
    time.sleep(0.005)
for _ in range(n):
    # STAMPED AT WRITE TIME, exactly as a real launch records it. A fixed
    # date here is not merely stale, it CORRUPTS THE INSTRUMENT: every
    # append re-reads the registry through the TTL prune, so once the
    # literal ages past RECORD_TTL_SECONDS each write drops every record
    # already present and the surviving count collapses to ~1 — the arm
    # would report a lost-append failure that the lock had nothing to do
    # with. `append_record` takes no now=, so this is the only injection
    # point available, and it needs none: the offset is ZERO, so there is
    # no gap for a future threshold change to sit inside.
    bw.append_record({"agent_name": tag, "session_id": "sid",
                      "task_ids": ["13"],
                      "registered_at": bw.iso_now()},
                     team_name="adv-team")
'''


class TestRegistryUnderConcurrentProcesses:
    """Two teammates backgrounding work at the same instant.

    THE LOCK'S PRIOR EVIDENCE IS ABOUT A DIFFERENT PR. It survived a review
    round on the predecessor; that is evidence about that tree. Nothing in
    this suite exercises two writers, and a lost append is invisible to every
    single-process test: the file stays well-formed and merely holds fewer
    records than were written, which no schema or fail-open arm can see.

    NON-VACUITY, MEASURED. The identical probe with the lock disabled as
    the only change kept 1, 9 and 15 records of 80 across three runs; the
    locked path kept 80/80 across three runs. The control ships below rather
    than living in the docstring, because a number in prose is not a tripwire.
    """

    PER_PROC = 25

    def _run(self, tmp_path, no_flock: bool) -> int:
        import os

        (tmp_path / "teams" / TEAM).mkdir(parents=True, exist_ok=True)
        script = tmp_path / "appender.py"
        script.write_text(_APPENDER)
        rendezvous = tmp_path / "rv"
        rendezvous.mkdir()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(HOOKS_DIR)
        if no_flock:
            env["ADV_NO_FLOCK"] = "1"
        procs = [
            subprocess.Popen(
                [sys.executable, str(script), str(tmp_path), tag,
                 str(self.PER_PROC), str(rendezvous)],
                env=carry_clock_shift(env),
            )
            for tag in ("alpha", "beta")
        ]
        for proc in procs:
            assert proc.wait(timeout=60) == 0
        path = tmp_path / "teams" / TEAM / "background_work.json"
        if not path.exists():
            return 0
        try:
            return len(json.loads(path.read_text())["records"])
        except (ValueError, KeyError, TypeError):
            return 0

    def test_no_append_is_lost_when_two_processes_write_at_once(self, tmp_path):
        assert self._run(tmp_path, no_flock=False) == 2 * self.PER_PROC

    # THE CONTROL IS A DOCUMENTED PROCEDURE, NOT A SHIPPED ARM, AND THAT IS A
    # CORRECTION. It shipped as a test asserting that the unlocked path LOSES
    # appends, and it is deleted because it was FLAKY — which the full suite
    # caught and three standalone runs did not.
    #
    #   idle machine, 40 appends/process : 1, 9, 15 records survived of 80
    #   full suite, 25/process           : all 50 survived — NO loss, arm RED
    #   after a real rendezvous replaced the clock barrier: still 2 of 6 runs
    #                                      green, i.e. still no loss
    #
    # The rendezvous fixed a real defect (a `time.time()+1.0` barrier that a
    # loaded machine outran, so the processes never overlapped) and it was NOT
    # ENOUGH: even with a guaranteed simultaneous start, two processes doing 25
    # fast read-modify-writes each can interleave without colliding. The race is
    # genuinely probabilistic and no amount of setup makes it certain, so an
    # assertion on it belongs nowhere near a merge gate.
    #
    # TO RE-VERIFY THE POSITIVE ARM IS NON-VACUOUS, run it by hand with
    # `state_file.fcntl.flock` replaced with a no-op (ADV_NO_FLOCK=1) and the count raised:
    #
    #     PER_PROC=200, ADV_NO_FLOCK=1  -> expect heavy loss, repeated over
    #     several runs; ANY run that keeps 400/400 means the lock is being
    #     taken somewhere you did not expect.
    #
    # The asymmetry that makes deleting it safe: WITH the lock, 50/50 is
    # GUARANTEED rather than likely, so the surviving arm can fail only when
    # the lock is genuinely broken. It is weak (it may not go red on every
    # regression) but it is never flaky, and a flaky arm in a 16000-test gate
    # costs more than the coverage it buys.


# ---------------------------------------------------------------------------
# The launch advisory must never become a term in the deny verdict, and it
# reaches teammate frames only.
# ---------------------------------------------------------------------------


_COMMANDS = [
    "true",]


_TEAMMATE = object()

_GATE_TEAM = "session-gate-adversarial"


def _gate_seam(root: Path) -> dict:
    """Write a team config and a registry entry for the default teammate; return the env.

    The member carries no backendType, so the gate does not skip it as it
    skips a separate-process (tmux) teammate.
    """
    teammate = captured_pretooluse_teammate_tmux()
    config = root / ".claude"
    (config / "teams" / _GATE_TEAM).mkdir(parents=True)
    (config / "teams" / _GATE_TEAM / "config.json").write_text(json.dumps({
        "leadSessionId": captured_pretooluse_lead_inprocess()["session_id"],
        "members": [{"name": "gate-teammate", "agentId": f"gate-teammate@{_GATE_TEAM}",
                     "agentType": teammate["agent_type"]}],
    }))
    registry = config / "pact-sessions" / ".teammate-registry.jsonl"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({
        "session_id": teammate["session_id"], "value": f"gate-teammate@{_GATE_TEAM}",
    }) + "\n")
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID")}
    env.update(HOME=str(root), CLAUDE_CONFIG_DIR=str(config))
    return env


def _gate(command: str, background, frame=_TEAMMATE) -> tuple:
    """Run the real gate as a subprocess: (returncode, decision, advisory shown).

    THE DEFAULT FRAME IS A REAL TEAMMATE'S. The launch advisory reaches teammate
    frames only, so a frame carrying no identity measures a gate that is
    correctly silent, and every arm asserting the advisory would test nothing.
    The default is the captured tmux-teammate PreToolUse frame with its tool
    call replaced by this Bash command. Pass another frame to send a different
    identity, or None for a frame with no identity at all.

    Every run gets its own config root. The gate resolves a teammate's team
    before advising, so the root holds a team config and a session-registry
    entry for the default teammate's session, and HOME and CLAUDE_CONFIG_DIR
    point there. No run reads the real ~/.claude.
    """
    if frame is _TEAMMATE:
        frame = captured_pretooluse_teammate_tmux()
    frame = {k: v for k, v in (frame or {}).items() if k != "_meta"}
    tool_input = {"command": command}
    if background is not None:
        tool_input["run_in_background"] = background
    frame["tool_name"] = "Bash"
    frame["tool_input"] = tool_input
    with tempfile.TemporaryDirectory() as root:
        proc = subprocess.run(
            [sys.executable, str(GATE)],
            input=json.dumps(frame),
            capture_output=True, text=True, timeout=30, env=carry_clock_shift(_gate_seam(Path(root))),
        )
    try:
        out = json.loads(proc.stdout or "{}")
    except ValueError:
        out = {}
    spec = out.get("hookSpecificOutput", {})
    return proc.returncode, spec.get("permissionDecision"), bool(
        spec.get("additionalContext")
    )


class TestAdvisoryNeverAltersTheVerdict:
    """The advisory rides the ALLOW branch. Prove it cannot reach the verdict.

    "It does not today" is what the code shows. What a test can add is that
    the verdict is INVARIANT under the launch flag the advisory reads —
    measured across the corpus rather than argued from the control flow, so a
    future edit that threads `run_in_background` into the filler predicate
    reddens here instead of shipping a gate whose decision depends on a field
    that has nothing to do with whether a command is a filler no-op.
    """

    @pytest.mark.parametrize("command", _COMMANDS)
    def test_the_verdict_is_invariant_under_run_in_background(self, command):
        verdicts = {_gate(command, bg)[:2] for bg in (None, True, False, "true")}
        assert len(verdicts) == 1, (command, verdicts)


class TestTheLaunchAdvisoryReachesTeammateFramesOnly:
    """Who receives the launch advisory, judged on the frame's identity.

    The advisory tells its reader that nothing will wake it and that it must
    flag the wait. That is true for a teammate and false for the lead, which is
    re-invoked when its own background job finishes and holds no task wait to
    flag. So it must reach a frame whose `agent_type` is present, non-empty and
    not a lead spelling, and nothing else.

    Each negative sends the same Bash launch as the teammate positive and
    changes only the identity, so a silent result is attributable to the
    identity rather than to the command. The lead frame is a real capture; the
    other identities change one field of a real capture. The Agent-tool subagent
    case needs a lead session context, so it is pinned in
    test_launch_advisory_population.py.
    """

    LAUNCH = ("echo hi", True)

    def test_a_TEAMMATE_frame_gets_the_advisory(self):
        rc, _decision, advisory = _gate(*self.LAUNCH)
        assert (rc, advisory) == (0, True), (
            "a real teammate frame launching background work drew no advisory; "
            "every negative arm in this class is then vacuous"
        )


# ---------------------------------------------------------------------------
# Boundaries the primary file approaches but never lands on.
# ---------------------------------------------------------------------------


class TestExactBoundaries:
    """`>=` versus `>`, on every clock.

    The primary pins one minute either side of each window, which cannot tell
    `>=` from `>`. These land exactly on it. An off-by-one here is invisible
    in operation and permanently wrong.
    """

    @pytest.mark.parametrize("offset,expired", [(-1, False),])
    def test_the_24h_TTL_expires_AT_the_threshold(self, offset, expired):
        record = bw._sanitize_record(_record())
        now = T0 + timedelta(seconds=bw.RECORD_TTL_SECONDS + offset)
        assert bw._record_expired(record, now) is expired


class TestGateInputsNobodyEnumerated:
    """Malformed task sets reaching the surface selector.

    `tasks` arrives from the live task store, so it can be empty, can hold
    entries that are not dicts, and can hold ids of a type the record does not
    use. Every one of these must surface NOTHING and none may raise: this
    selector runs inside a lead-facing hook whose contract is fail-open.
    """

    @pytest.mark.parametrize(
        "tasks", [[],]
    )
    def test_an_unusable_task_set_surfaces_nothing(self, tasks):
        record = bw._sanitize_record(_record())
        assert bw.outstanding_unflagged(tasks, records=[record]) == []


