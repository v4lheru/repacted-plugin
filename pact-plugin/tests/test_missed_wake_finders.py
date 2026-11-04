"""Behavioural pins for the lead-side finders and their ENTRY POINT.

Location: pact-plugin/tests/test_missed_wake_finders.py
Summary: pins `find_mutual_waits`, `find_unanchored_waits`,
         `build_unanchored_surface`, and that every finder in FINDERS is
         reached from `run_surface` — the registered entry point — rather
         than only from a test calling them directly.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

🔴 WHY THE ENTRY-POINT ARMS EXIST, and it is the reason this file is not just
finder arms. Five non-vacuity arms were run against `find_unanchored_waits`
and all five called it DIRECTLY. **Every one would have passed with the caller
broken** — and the caller really was broken at one point, by a use-before-
assignment inside a bare `except Exception: pass`, which would have made the
alarm silently never fire while every finder arm stayed green. Testing a finder
is not testing that anything calls it.

STRUCTURAL REACHABILITY IS NOT ENOUGH ON ITS OWN AND IS PAIRED HERE
DELIBERATELY. An AST reachability check was measured earlier to PASS against
code that raises: the call is textually present, so the walk finds it, while
execution never arrives. Structure catches *nothing calls it*; behaviour
catches *the call is never reached*. Neither subsumes the other, so both are
below.

PROOF STANDARD FOR THIS FILE: revert-to-pre-fix mutation, never a total break.
Measured on a sibling surface — destroying a function killed nine pre-existing
arms while reverting it to the previous behaviour killed zero of 124.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import missed_wake_scan as mw
from fixtures.role_frames import captured_lead_userpromptsubmit_qualified

SOURCE = Path(__file__).resolve().parents[1] / "hooks" / "missed_wake_scan.py"
ENTRY_POINT = "run_surface"
FINDERS = ("find_stale_missed_wakes",)

AGED_MIN = 90     # past the 30-minute staleness threshold
FRESH_MIN = 2     # well inside it


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _task(task_id, owner, resolver="peer", since=AGED_MIN, anchor=None,
          status="in_progress") -> dict:
    wait = {"reason": "awaiting_blocker_resolution",
            "expected_resolver": resolver, "since": _ago(since)}
    if anchor is not None:
        wait["covers_since"] = _ago(anchor)
    return {"id": task_id, "owner": owner, "subject": "s", "status": status,
            "metadata": {"intentional_wait": wait}}


class TestMutualWaitsNeedsTwoDistinctPeersAndAge:
    """Two or more agents each waiting on a peer, none of them working.

    WHAT IT DETECTS IS A CANDIDATE, NOT A PROVEN CYCLE. `expected_resolver`
    records the KIND of resolver and never which one, so the task store cannot
    say X waits on Y and Y waits on X. Two agents independently waiting on a
    third looks identical from outside. Both are worth a look; only one is a
    deadlock, and the surface must not claim otherwise.
    """

    def test_two_distinct_peers_both_aged_are_DETECTED(self):
        assert mw.find_mutual_waits(
            [_task("1", "alice"), _task("2", "bob")]) != []

    def test_a_RESTAMPED_PAIR_is_still_detected(self):
        """🔴 THE CASE THIS ARC ACTUALLY HIT, and the reason the age gate reads
        the ANCHOR rather than `since`.

        Both waits were re-stamped two minutes ago and both anchors are ninety
        minutes old. Under a `since`-based gate this pair is ZERO stale and is
        never detected — and re-stamping is BOTH what makes a mutual wait
        dangerous and what makes it look fresh. A `since` gate therefore shows
        exactly the population that is fine and hides the one that is not.
        """
        pair = [_task("1", "alice", since=FRESH_MIN, anchor=AGED_MIN),
                _task("2", "bob", since=FRESH_MIN, anchor=AGED_MIN)]
        assert mw.find_mutual_waits(pair) != [], (
            "a re-stamped pair must still age via its ANCHOR; gating on "
            "`since` inverts the signal and hides every dangerous case"
        )


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """A real team-scoped registry on disk, returning a seeder.

    Not a mock of the loader: the finder reaches the registry through the same
    team-path resolution production uses, so a seam regression reddens here.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    team = "finder-team"
    (tmp_path / "teams" / team).mkdir(parents=True)
    path = tmp_path / "teams" / team / "background_work.json"

    def seed(launched_minutes_ago):
        if launched_minutes_ago is None:
            path.write_text(json.dumps({"records": []}))
            return team
        path.write_text(json.dumps({"records": [{
            "agent_name": "alice", "session_id": "s", "task_ids": ["7"],
            "registered_at": _ago(launched_minutes_ago)}]}))
        return team

    return seed


class TestUnanchoredWaitsGateOnACoveredRecord:
    """The surface fires only where the missing anchor has actually DECIDED
    something — i.e. where a record exists that the fallback covers.

    An unanchored wait still works; what is missing is the field pinning
    WHICH launches it covers. Agents write that field on every SET, so its
    absence means the wait predates the field, came from an older instruction
    or a template that omits it, or lost the field on a re-SET. With no covered
    record there is nothing for the absence to have affected and nothing to
    tell the lead.
    """

    def test_an_unanchored_wait_covering_a_record_FIRES(self, registry):
        team = registry(100)
        assert mw.find_unanchored_waits([_task("7", "alice")], team) != []

    def test_an_ANCHORED_wait_is_silent(self, registry):
        team = registry(100)
        assert mw.find_unanchored_waits(
            [_task("7", "alice", anchor=AGED_MIN)], team) == []


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    """Module-scoped, and a plain function rather than a class instance method:
    a class-scoped fixture defined as a method is deprecated and would add a
    third PytestRemovedIn10Warning to the suite's standing two."""
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


class TestBothFindersAreReachedFromTheRegisteredEntryPoint:
    """STRUCTURE AND BEHAVIOUR, PAIRED — neither is sufficient alone."""

    @pytest.mark.parametrize("finder", FINDERS)
    def test_STRUCTURAL_each_finder_is_called_from_run_surface(self, tree, finder):
        """Catches a finder being ORPHANED — no caller at all.

        Does NOT catch the call being unreachable at runtime: an AST walk finds
        a call that a raise above it never arrives at. The behavioural arm
        below is what covers that, and removing either leaves a real hole.
        """
        entry = next(n for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == ENTRY_POINT)
        called = {n.func.id for n in ast.walk(entry)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert finder in called, (
            f"{finder} is no longer called from {ENTRY_POINT}; a finder nothing "
            "reaches is an alarm that cannot fire, however green its own arms are"
        )

    def test_BEHAVIOURAL_run_surface_actually_produces_the_unanchored_text(
        self, registry, monkeypatch
    ):
        """The arm that a raise inside `run_surface` would redden and the
        structural one would not.

        Drives the registered entry point with a lead frame and asserts the
        composed output, so a use-before-assignment swallowed by the bare
        `except` — which really happened — cannot present as correct silence.
        """
        team = registry(100)
        task = _task("7", "alice")
        monkeypatch.setattr(mw, "get_task_list", lambda: [task])
        monkeypatch.setattr(mw, "read_events", lambda et: [])
        monkeypatch.setattr(mw, "append_event", lambda e: True)
        monkeypatch.setattr(mw, "get_journal_path", lambda: "/tmp/fake.jsonl")
        from shared import pact_context
        monkeypatch.setattr(pact_context, "get_team_name", lambda: team)

        out = mw.run_surface(captured_lead_userpromptsubmit_qualified())
        assert out is not None and "FALLBACK ANCHOR" in out, (
            "the unanchored alarm did not reach the lead from the registered "
            "entry point. A finder arm cannot see this: the finder works and "
            "nothing calls it, or the call is never reached."
        )


class TestTheMutualWaitSurfaceReachesTheLead:
    """The mutual-wait alarm, driven through the registered entry point.

    The structural arm sees the finder CALLED. It cannot see the finder's
    result dropped before it reaches the output, and it cannot see a raise
    that stops the call being reached. Only the composed text shows either.
    """

    @pytest.fixture
    def surface(self, tmp_path, monkeypatch):
        """Run `run_surface` as a lead on a given task list.

        No team name, so the registry-backed alarms stay out of the output and
        nothing reads a config root outside tmp_path. The journal is stubbed so
        no alarm can write anywhere.
        """
        from shared import pact_context

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(pact_context, "get_team_name", lambda: None)
        monkeypatch.setattr(mw, "read_events", lambda et: [])
        monkeypatch.setattr(mw, "append_event", lambda e: True)
        monkeypatch.setattr(mw, "get_journal_path", lambda: str(tmp_path / "journal.jsonl"))

        def run(tasks):
            monkeypatch.setattr(mw, "get_task_list", lambda: tasks)
            return mw.run_surface(captured_lead_userpromptsubmit_qualified()) or ""

        return run

    def test_an_aged_peer_pair_reaches_the_lead_as_a_MUTUAL_WAIT(self, surface):
        out = surface([_task("1", "alice"), _task("2", "bob")])
        assert "POSSIBLE MUTUAL WAIT" in out, (
            "two distinct agents each idling on a peer past the threshold did "
            "not reach the lead. A finder can be called and its result still "
            "dropped, and only the output shows that: %r" % (out,)
        )
        assert "- Task #1 (alice" in out and "- Task #2 (bob" in out, out


class TestATeammateIdleOnAFinishedRunReachesTheLead:
    """2026-10-03: teammates backgrounded their own test runs and idled for hours with a
    wait set; the lead found them by matching the run ledger against the waits by hand.
    Driven through `run_surface` with a real ledger file and an in-memory journal."""

    @pytest.fixture
    def surface(self, tmp_path, monkeypatch):
        monkeypatch.syspath_prepend(str(SOURCE.parents[1] / "bin"))
        from _repact_common import ledger_append
        from shared import pact_context

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(pact_context, "get_team_name", lambda: None)
        journal: list = []
        monkeypatch.setattr(mw, "read_events", lambda et: [e for e in journal if e.get("type") == et])
        monkeypatch.setattr(mw, "append_event", lambda e: journal.append(e) or True)
        monkeypatch.setattr(mw, "get_journal_path", lambda: str(tmp_path / "journal.jsonl"))
        tree = tmp_path / "worktrees" / "unit-a"
        wait_since = datetime.now(timezone.utc) - timedelta(minutes=40)
        task = {"id": "66", "owner": "u3", "subject": "s", "status": "in_progress",
                "description": f"comPACT mode.\nWorktree: {tree} (branch repacted/unit-a, at head).\n",
                "metadata": {"intentional_wait": {
                    "reason": "awaiting_lead_completion", "expected_resolver": "lead",
                    "since": wait_since.isoformat(), "covers_since": wait_since.isoformat()}}}

        def run(cwd, description=None, owner_call_dirs=None):
            if description is not None:
                task["description"] = description
            if owner_call_dirs:
                ledger_append({"ts": wait_since.isoformat(), "agent_name": "u3", "source": "hook",
                               "call_dirs": [str(path) for path in owner_call_dirs], "log_path": None})
            ledger_append({"ts": (wait_since + timedelta(minutes=20)).isoformat(), "cwd": str(cwd),
                           "runner": "pytest", "passed": 12, "failed": 1, "errors": 0,
                           "exit_code": 1, "count_parsed": True, "source": "tcount",
                           "log_path": str(tmp_path / "runs" / "2026-10-03T143200Z-7-pytest.log")})
            monkeypatch.setattr(mw, "get_task_list", lambda: [task])
            return [mw.run_surface(captured_lead_userpromptsubmit_qualified()) or "" for _ in range(2)]

        return tree, run

    def test_a_run_from_the_worktree_that_ended_after_the_wait_is_shown_ONCE(self, surface):
        tree, run = surface
        first, second = run(tree / "repacted" / "pact-plugin")
        assert ("u3 (#66) is idle on a finished run: 2026-10-03T143200Z-7-pytest.log, "
                "12 passed, 1 failed, 0 errors, exit 1; nudge it") in first, first
        assert "idle on a finished run" not in second, second


