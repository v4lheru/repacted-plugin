"""Behaviour pins for shared/background_work.py.

Location: pact-plugin/tests/test_background_work.py
Summary: pins the registry's record schema, the R5 task_ids list, the Layer 3
         two-threshold clock, the identity bind (including the validated
         agent_type route and the collisions it must refuse), and the
         acknowledgment discharge.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

THESE PIN BEHAVIOUR, NOT FRAME FIELDS. Identity may arrive by `agent_name`,
by an `@`-bearing `agent_id`, or by a validated `agent_type`, and no test
here asserts WHICH field carried it, because that is a property of the
harness rather than of this code. What is pinned is the OUTCOME: a frame
carrying an identity by any accepted route produces a record; a frame
carrying none produces nothing.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared.background_work import (
    LEAD_STALE_MINUTES,
    LEAD_UNIDLED_STALE_MINUTES,
    _sanitize_record,
    agent_type_names_a_member,
    bind_launcher_identity,
    classify_wait,
    discharge_acknowledged_for_owner,
    effective_since,
    is_shell_backgrounded_bash,
    lead_stale,
    load_records_for_discharge,
    matching_outstanding,
    record_task_ids,
    outstanding_unflagged,
    unflagged_fire,
    wait_covers_record,
)

TEAM = "probe-team"
HEX16 = "0123456789abcdef"
T0 = datetime(2026, 9, 11, 12, 0, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _record(**over):
    base = {
        "agent_name": "probe-coder",
        "session_id": "sid",
        "task_ids": ["13"],
        "registered_at": _iso(T0),
    }
    base.update(over)
    return base


def _task(task_id="13", status="in_progress", wait=None, **over):
    task = {"id": task_id, "status": status, "owner": "probe-coder"}
    if wait is not None:
        task["metadata"] = {"intentional_wait": wait}
    task.update(over)
    return task


def _wait(since: datetime, reason="awaiting_blocker_resolution"):
    return {"reason": reason, "expected_resolver": "lead", "since": _iso(since)}


# ---------------------------------------------------------------- schema (R5)


class TestRecordSchema:
    def test_task_ids_list_is_accepted(self):
        assert _sanitize_record(_record())["task_ids"] == ["13"]

    def test_two_task_ids_survive(self):
        assert _sanitize_record(_record(task_ids=["13", "14"]))["task_ids"] == [
            "13",
            "14",
        ]


class TestTheOwnerMarkerOnTheReadPath:
    """What a written `owner_role` becomes by the time a reader sees the row.

    The sanitiser's comment says only the exact value "subagent" survives, and
    that any other value is not kept: a row carrying task ids is then read as a
    teammate's, and a row without them is dropped whole. Each case writes the
    raw row straight to the registry file, as any writer could, and reads it
    back through the loader the readers use, so the sanitiser runs where it
    runs in use. `save_records` is not used to seed: it sanitises on the way
    in, and would drop the task-less row before the read path ever saw it.
    """

    @pytest.fixture(autouse=True)
    def _isolated_team(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        self.registry = tmp_path / "teams" / TEAM / "background_work.json"
        self.registry.parent.mkdir(parents=True)

    @pytest.mark.parametrize(
        "owner_role, task_ids, expected",
        [
            pytest.param("Subagent", ["13"], [{"task_ids": ["13"]}],
                         id="non-exact-with-task-ids-is-a-teammate-row"),],
    )
    def test_a_written_owner_role_reaches_readers_only_as_the_comment_says(
        self, owner_role, task_ids, expected
    ):
        import json

        written = _record(owner_role=owner_role, task_ids=task_ids)
        self.registry.write_text(json.dumps({"records": [written]}))

        loaded = load_records_for_discharge(TEAM, now=T0)

        base = {k: written[k] for k in ("agent_name", "session_id", "registered_at")}
        assert loaded == [dict(base, **e) for e in expected], (
            f"owner_role {owner_role!r} with task_ids {task_ids!r} reached the "
            f"readers as {loaded!r}"
        )


class TestMatchingByList:
    def test_matches_any_listed_id(self):
        rec = _sanitize_record(_record(task_ids=["13", "14"]))
        assert matching_outstanding(_task("14"), records=[rec]) == rec

    def test_unlisted_id_does_not_match(self):
        rec = _sanitize_record(_record(task_ids=["13"]))
        assert matching_outstanding(_task("99"), records=[rec]) is None


# -------------------------------------------------- Layer 3's two-clock rule


class TestEffectiveSince:
    def test_idled_at_present_uses_the_short_window(self):
        rec = _record(idled_at=_iso(T0))
        since, minutes = effective_since(_sanitize_record(rec))
        assert minutes == LEAD_STALE_MINUTES
        assert since == T0

    def test_idled_at_absent_falls_back_to_registered_at(self):
        """Without this, a missed TeammateIdle disables Layer 3 entirely.

        `stamp_idled_at` is the only writer of `idled_at`, so keying the lead
        surface on that field alone made Layer 3 a consumer of Layer 2 rather
        than a backstop for it.
        """
        since, minutes = effective_since(_sanitize_record(_record()))
        assert minutes == LEAD_UNIDLED_STALE_MINUTES
        assert since == T0


# ------------------------------------------------------------ the fire predicate


class TestUnflaggedFire:
    def test_fires_when_in_progress_recorded_and_unflagged(self):
        rec = _sanitize_record(_record())
        fire, klass, got = unflagged_fire(_task(), records=[rec])
        assert fire is True and klass == "missing" and got == rec

    def test_does_not_fire_without_a_record(self):
        assert unflagged_fire(_task(), records=[])[0] is False


# ------------------------------------------- the acknowledgment discharge (P1a)


class TestWaitCoversRecord:
    """The discharge predicate. Pure over (task, record) — no session needed."""

    def test_wait_after_the_launch_acknowledges_it(self):
        rec = _sanitize_record(_record())
        assert wait_covers_record(_task(wait=_wait(T0 + timedelta(minutes=1))), rec)

    def test_wait_exactly_at_the_launch_acknowledges_it(self):
        rec = _sanitize_record(_record())
        assert wait_covers_record(_task(wait=_wait(T0)), rec)


class TestDischargeSequences:
    """The sequences that decide whether Layers 2 and 3 may ship.

    Each is a full sequence rather than a point check, because the defect
    being pinned only exists ACROSS steps: a record that outlives its own
    acknowledgment, and is then cited by a later advisory.

    WHAT IS BEING PREVENTED, AT ITS ACTUAL SIZE: a MISATTRIBUTED advisory,
    not one that fires at a compliant teammate. The threshold counter is
    cleared on every tick where the fire predicate is false, so a teammate
    that flags never accumulates toward it. When the advisory does fire the
    teammate is genuinely idling unflagged — the advice is right and only the
    CITED CAUSE is stale. Sequence 1 pins that the discharge happens; it does
    not pin a false alarm that was never reachable.
    """

    @pytest.fixture(autouse=True)
    def _isolated_team(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "teams" / TEAM).mkdir(parents=True)

    def _seed(self, *records: dict):
        """Seed the registry in ONE clock-free write.

        🔴 `save_records` WRITES DIRECTLY AND NEVER READS, so no TTL prune can
        touch the fixture on the way in. `append_record` READS FIRST — through
        `_parse_records_text`, which drops any record older than
        RECORD_TTL_SECONDS against the REAL clock — so two successive appends
        of a fixed-date record silently lose the first one once the fixture
        ages past 24h. The append still returns True, so the loss is invisible
        at seed time and surfaces later as a count that is short by one.

        Seeding is not the subject of any arm in this class, so it must not be
        able to fail. Where `append_record` IS the subject — the concurrent
        two-writer arm — it stays, and its fixture is made time-independent a
        different way.
        """
        from shared.background_work import save_records

        assert save_records(
            [_record(**over) for over in (records or ({},))], team_name=TEAM
        ) is True

    def test_1_flag_then_idle_then_clear_then_idle_DOES_NOT_FIRE(self):
        """The whole point. A teammate that did everything right stays silent."""
        self._seed()
        flagged = _task(wait=_wait(T0 + timedelta(minutes=1)))
        assert discharge_acknowledged_for_owner([flagged], "probe-coder", team_name=TEAM, now=T0) == 1
        assert load_records_for_discharge(TEAM, now=T0) == []
        # later it clears the wait and keeps working the same task
        assert unflagged_fire(_task(), team_name=TEAM, now=T0)[0] is False

    def test_2_never_flag_then_idle_FIRES(self):
        """The target case must be untouched by the discharge."""
        self._seed()
        assert discharge_acknowledged_for_owner([_task()], "probe-coder", team_name=TEAM, now=T0) == 0
        assert unflagged_fire(_task(), team_name=TEAM, now=T0)[0] is True, (
            "a teammate that never flagged a wait must still draw the advisory: "
            "a discharge with nothing to acknowledge leaves the record firing"
        )


class TestSuppressionIsTemporaryNotPermanent:
    """What an OPEN wait does to a job launched after it.

    THE DESIGN DOCUMENT SAYS THIS FIRES. IT DOES NOT, AND THE CODE IS RIGHT.
    §6.2's discharge table reads "job 2's registered_at > the wait's since,
    test fails, job 2 not discharged -> fires". Only the first clause is true.
    The record does survive the discharge — and then nothing surfaces it,
    because the task carries a valid wait.

    THE SUPPRESSION IS OVER-DETERMINED, AND SAYING WHICH GATE DOES IT WOULD BE
    WRONG. `unflagged_fire` consults `any_listed_task_flagged` first and
    `classify_wait` second, and on this input EITHER ALONE suffices: MEASURED,
    disabling `any_listed_task_flagged` entirely leaves the job-2 arms below
    green, because `classify_wait` refuses the same frame. So these arms pin
    an OUTCOME with two independent causes, and no arm here should be read as
    evidence about which gate is load-bearing. The R5 silencing gate is
    isolated by `test_a_flag_on_EITHER_held_task_silences_the_advisory`, which
    puts the flag on a DIFFERENT task than the one in hand — the only shape
    where the two gates disagree.

    WHAT THE JOB-2 ARMS UNIQUELY PIN is the discharge's `since` scoping:
    job 2's record SURVIVES an acknowledgment aimed at job 1, and fires once
    the wait closes. MEASURED — the job-2 arms die when `since >= registered`
    is widened to a blanket amnesty and when it is narrowed to never
    discharge. Suppression is temporary; discharge is permanent.

    AND FIRING WOULD BE WORSE THAN A MISSED TICK. The advisory's own text says
    "no flagged wait" — literally false at a teammate holding one open. A
    discredited alarm never recovers, which is the standing posture and what
    decides this.

    Pinned as a pair — suppressed while open, fires once cleared — because
    either arm alone reads as the other's bug.
    """

    @pytest.fixture(autouse=True)
    def _isolated_team(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        (tmp_path / "teams" / TEAM).mkdir(parents=True)

    def _two_jobs(self):
        """Two launches, seeded in ONE clock-free write. See the sibling
        class's `_seed` for why this is not two `append_record` calls: the
        second append RE-READS the registry and the TTL prune drops the first
        record once the fixture ages past 24h, so the arm would be measuring
        one job while its name and its assertions claim two."""
        from shared.background_work import save_records

        assert save_records(
            [_record(registered_at=_iso(T0)),
             _record(registered_at=_iso(T0 + timedelta(minutes=10)))],
            team_name=TEAM,
        ) is True
        flagged = _task(wait=_wait(T0 + timedelta(minutes=1)))
        assert discharge_acknowledged_for_owner([flagged], "probe-coder", team_name=TEAM, now=T0) == 1
        return flagged

    def test_job2_is_SILENT_while_the_job1_wait_is_still_open(self):
        flagged = self._two_jobs()
        fire, wait_class, record = unflagged_fire(
            flagged, team_name=TEAM, now=T0, tasks=[flagged]
        )
        assert fire is False, (
            "an advisory here would tell a teammate it has no flagged wait "
            "while it holds one open"
        )
        assert wait_class is None
        assert record is not None, "and the record must SURVIVE, not be discharged"
        assert record["registered_at"] == _iso(T0 + timedelta(minutes=10))

    def test_BOTH_gates_independently_refuse_so_NEITHER_reads_AS_DEAD(self):
        """States the over-determination, so nobody has to re-derive it.

        `unflagged_fire` refuses THIS frame twice over: `any_listed_task_flagged`
        returns True and `classify_wait` returns None, and either alone
        suffices. Nothing else asserts that, because every other arm checks the
        combined outcome, which cannot distinguish one gate from two.

        WHAT THIS ARM IS *NOT*. It is not closing a coverage gap. Both gates
        were ALREADY pinned before it existed — MEASURED at primary +
        adversarial scope, with this arm removed: disabling
        `any_listed_task_flagged` kills 5 arms
        (`test_a_flag_on_EITHER_held_task_silences_the_advisory`,
        `..._suppresses_the_surface`, `test_a_FLAGGED_task_is_NOT_surfaced`,
        `test_the_discharge_read_stays_RAW_so_it_can_still_see_a_flag`,
        `test_the_two_alarms_are_EXCLUSIVE_on_one_task`) and disabling
        `classify_wait`'s validation kills 14. Neither gate can be deleted on a
        green suite.

        That correction matters more than the arm. The belief that neither gate
        was pinned came from a TRUE but NARROW measurement — disabling one gate
        left the two suppression arms in this class green — generalised to the
        suite without re-measuring. The arms that pin each gate use a fixture
        where the two gates DISAGREE (flag on a different task than the one in
        hand); this class uses one where they agree, so of course it could not
        see them.

        What is left for this arm to do is name the property: these two agree
        here and disagree elsewhere, so neither is redundant, and a reader who
        mutates one in THIS shape and sees nothing should not conclude it is
        dead.
        """
        from shared.background_work import any_listed_task_flagged

        flagged = self._two_jobs()
        record = matching_outstanding(
            flagged, records=load_records_for_discharge(TEAM, now=T0)
        )
        assert record is not None

        assert any_listed_task_flagged(record, [flagged]) is True, (
            "gate 1 (R5 silencing) no longer refuses this frame"
        )
        assert classify_wait(flagged) is None, (
            "gate 2 (valid-wait) no longer refuses this frame"
        )
        # and gate 2 alone still suppresses, with no task list for gate 1 to read
        assert unflagged_fire(flagged, team_name=TEAM, now=T0, tasks=None)[0] is False


# ------------------------------------------------ the Layer 3 read path (gates)


class TestOutstandingUnflagged:
    """The gated selector every surface-read path must go through.

    WHY THIS CLASS EXISTS. Layer 2 reached records through `unflagged_fire`,
    which applies the task-status and flagged-wait gates. Layer 3's lead-side
    selector read `_load_records` directly and applied NEITHER, so it surfaced
    records for completed tasks and for correctly-flagged waits — while the
    lead-facing text asserts "outstanding launches and no flagged wait".
    MEASURED on one 40-minute-old record: `lead_stale` True (surfaced) against
    `unflagged_fire` False on both gates (refused). One gated consumer, one
    ungated.

    The earlier coverage pinned the gates only on the path that already had
    them — coverage that looks like coverage and is not.
    """

    def test_a_completed_task_is_NOT_surfaced(self):
        rec = _sanitize_record(_record())
        tasks = [_task(status="completed")]
        assert outstanding_unflagged(tasks, records=[rec]) == []

    def test_a_FLAGGED_task_is_NOT_surfaced(self):
        """The clause the lead-facing text asserts and the old path never checked."""
        rec = _sanitize_record(_record())
        tasks = [_task(wait=_wait(T0 + timedelta(minutes=1)))]
        assert outstanding_unflagged(tasks, records=[rec]) == []


# ------------------------------------------------------------------ identity


class TestBindLauncherIdentity:
    @pytest.fixture(autouse=True)
    def _team_config(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        team_dir = tmp_path / "teams" / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps(
                {
                    "leadSessionId": "lead-sid",
                    "members": [
                        {"name": "probe-coder", "agentId": f"probe-coder@{TEAM}",
                         "agentType": "pact-backend-coder"},
                        {"name": "preparer", "agentId": f"preparer@{TEAM}",
                         "agentType": "pact-preparer"},
                    ],
                }
            )
        )
        tasks = tmp_path / "tasks" / TEAM
        tasks.mkdir(parents=True)
        (tasks / "13.json").write_text(
            json.dumps({"id": "13", "status": "in_progress", "owner": "probe-coder"})
        )

    def _frame(self, **over):
        frame = {
            "session_id": "sid",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hi", "run_in_background": True},
        }
        frame.update(over)
        return frame

    def test_step2_route_with_NO_agent_name_key(self):
        """Exercises the `@`-bearing agent_id route on its own.

        Every fixture that supplies `agent_name` passes through Step 1, so
        without this arm the Step-2 route would ship entirely unexecuted —
        and nobody has been able to separate the two on live data.
        """
        frame = self._frame(agent_id=f"probe-coder@{TEAM}")
        assert "agent_name" not in frame
        bound = bind_launcher_identity(frame, TEAM)
        assert bound is not None and bound[0] == "probe-coder"

    def test_step1_route(self):
        bound = bind_launcher_identity(self._frame(agent_name="probe-coder"), TEAM)
        assert bound is not None and bound[0] == "probe-coder"


    # --- the exposure we accepted rather than eliminated -------------------


class TestTheAgentIdShapeDecidesBeforeTheTypeChecks:
    """`agent_type_names_a_member(..., agent_id=)` reads the frame's id shape first.

    An in-process teammate's frame carries "a" + agent_type + "-" + 16 lowercase
    hex, and an Agent-tool subagent's carries "a" + 16 lowercase hex. A
    subagent-shaped id is never a member. A teammate-shaped id goes to
    membership without the deny set. Any other id, or none, keeps the
    type-based checks, which the arms above pin.
    """

    MEMBERS = ("pact-backend-coder", "general-purpose", "claude", "probe-work-coder")

    @pytest.fixture(autouse=True)
    def _team_config(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        team_dir = tmp_path / "teams" / TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps(
            {"leadSessionId": "lead-sid", "members": [{"name": n} for n in self.MEMBERS]}
        ))
        tasks = tmp_path / "tasks" / TEAM
        tasks.mkdir(parents=True)
        (tasks / "13.json").write_text(
            json.dumps({"id": "13", "status": "in_progress", "owner": "claude"})
        )
        self.root = tmp_path

    def test_a_teammate_shaped_id_admits_a_member_named_after_a_shipped_stem(self):
        """REVERT PROOF. Without the id the deny set refuses this member on its
        own frame, so it never gets its peer block or its launch record."""
        from shared.background_work import _known_agent_types

        assert "pact-backend-coder" in _known_agent_types(), "the arm needs a shipped stem"
        assert agent_type_names_a_member(
            "pact-backend-coder", TEAM, agent_id=f"apact-backend-coder-{HEX16}"
        ) is True
        assert agent_type_names_a_member("pact-backend-coder", TEAM) is False

    def test_a_teammate_shaped_id_admits_a_member_named_after_a_platform_type(self):
        assert agent_type_names_a_member(
            "general-purpose", TEAM, agent_id=f"ageneral-purpose-{HEX16}"
        ) is True
        assert agent_type_names_a_member("general-purpose", TEAM) is False


class TestShellBackgroundedLaunchPopulation:
    """`is_shell_backgrounded_bash` — the population the detector ADDS.

    🔴 WHAT THIS CLASS DOES NOT SHOW, STATED FIRST BECAUSE IT IS THE TRAP.
    Every arm here tests the PREDICATE IN ISOLATION. All of them pass whether
    or not `record_background_launch` ever calls it, so none of them can tell
    you the widening is wired in. That is not a defect in these arms; it is
    what a predicate test is. The wiring is pinned by a POSITIVE-RECORD arm
    through the real seam, in test_track_files_background_integration.py, and
    the general rule is worth stating once: only an arm asserting that a
    record WAS written can detect a wiring break, because an unwired gate
    fails CLOSED and a silent refusal is observationally identical to a
    correct one. Every negative-asserting arm below is structurally incapable
    of catching it.

    THE POPULATION IS EXACTLY "a command ENDING in a bare `&`", and nothing
    here may be read as broader. A mid-line background, a subshell and a
    disowned job all miss, and they are pinned as such below rather than left
    to a docstring — the prose describing this feature has already been wrong
    twice in the overclaiming direction.
    """

    @staticmethod
    def _frame(command, tool_name="Bash"):
        return {"tool_name": tool_name, "tool_input": {"command": command}}

    # MEASURED against the predicate before being written down, not predicted.
    LAUNCHES = [
        "nohup ./gate.sh &",
        "sleep 720 &",
        "python3 -m pytest -q > out.log 2>&1 &",
        "./run.sh --flag &   ",          # trailing whitespace, rstrip'd
        "nohup bash -c 'a; b' &",
    ]
    NOT_BACKGROUNDING = [
        "a && b",                        # conjunction
        "make -j4 &&",                   # ENDS in `&&` — the exclusion's own case
        "echo x 2>&1",                   # fd redirect
        "echo 'a & b'",                  # quoted
        "cmd & wait",                    # backgrounds, then waits: not a launch
        "echo done \\&",                 # escaped literal
        "echo plain",                    # no ampersand at all
        "cat <<'EOF'\nbody &\nEOF",      # `&` inside a heredoc body
    ]
    DOCUMENTED_MISSES = [
        "nohup ./gate.sh & echo started",
        "( ./gate.sh & )",
        "./gate.sh & sleep 1",
        "setsid ./gate.sh & disown",
    ]

    @pytest.mark.parametrize("command", list(LAUNCHES)[:1])
    def test_a_command_ENDING_in_a_bare_ampersand_is_a_launch(self, command):
        """ARM 1. The shapes the widening exists to catch.

        MUTANT that reddens this arm: drop the `.rstrip()` — the trailing-
        whitespace case then stops matching, which is the realistic edit
        because the strip looks redundant until you have a command built by
        string concatenation.
        """
        assert is_shell_backgrounded_bash(self._frame(command)) is True, command

    @pytest.mark.parametrize("command", list(NOT_BACKGROUNDING)[:1])
    def test_an_ampersand_that_does_not_background_is_NOT_a_launch(self, command):
        """ARM 2. The over-fire direction.

        An over-fire here costs one spurious registry row, which is visible
        and dischargeable — so this is the CHEAP direction and the arm exists
        to keep it cheap rather than to prevent a catastrophe. The expensive
        direction is a miss, which is silent.

        MUTANT that reddens this arm: drop the `&&` / `\\&` exclusions, and
        `a && b` and `echo done \\&` start recording.
        """
        assert is_shell_backgrounded_bash(self._frame(command)) is False, command


# The row fields a reader can match on, named ONCE here and asserted TWICE
# below: against the schema the module actually emits, and against the module
# docstring that names them as the three key types. A rename reddens the schema
# arm; fixing that moves this tuple; the docstring arm then reddens until the
# prose follows. It pins TOKENS, not wording, so a rewrap or a rewrite of that
# paragraph costs nothing and only the event that makes the docstring FALSE
# fires it.
ROW_KEY_TOKENS = ("harness_task_id", "agent_name", "task_ids")


class TestTheRowKeyTokensAreRealAndDocumented:
    """Structural guard: the docstring's taxonomy must name real fields.

    The module docstring tells the next person adding a row shape that every
    reader matches on one of three keys. That sentence is only useful if the
    names in it are the names the code uses, and only SAFE if it cannot go
    quietly stale — a docstring that is confidently wrong about a field name is
    worse than one that says nothing, because it is read as settled.
    """

    def test_each_token_is_a_key_the_row_schema_emits(self):
        """REGRESSION GUARD — passes before and after; it protects the claim
        that these names are the row's own schema rather than prose."""
        record = _sanitize_record(_record(harness_task_id="bg-1"))

        assert record is not None
        missing = [token for token in ROW_KEY_TOKENS if token not in record]
        assert missing == [], (
            "these names are no longer keys the row schema emits: %s. The "
            "module docstring names them as the keys a reader matches on, so "
            "it is now wrong; move this tuple and the docstring together."
            % (missing,)
        )

    def test_the_module_docstring_names_each_one(self):
        """BASE-RED when the docstring's taxonomy is absent or renames a field.

        Asserts PRESENCE of each token, never any surrounding phrasing, so the
        paragraph can be rewritten freely.
        """
        from shared import background_work

        doc = background_work.__doc__ or ""
        missing = [token for token in ROW_KEY_TOKENS if token not in doc]
        assert missing == [], (
            "the module docstring does not name these row keys: %s. It states "
            "the taxonomy a reader uses to decide whether a new row shape is "
            "safe, and a key it fails to name is a key the next author will "
            "not check against." % (missing,)
        )


MODULE_SOURCE = Path(__file__).resolve().parents[1] / "hooks" / "shared" / "background_work.py"


def _module_functions():
    """Top-level functions of background_work.py, as AST nodes."""
    tree = ast.parse(MODULE_SOURCE.read_text(encoding="utf-8"))
    return [n for n in tree.body if isinstance(n, ast.FunctionDef)]


def _params(fn) -> list:
    return [a.arg for a in fn.args.args] + [a.arg for a in fn.args.kwonlyargs]


def _references(fn, name: str) -> bool:
    """True if `name` is READ as a value anywhere in the body.

    A keyword argument that merely CARRIES the name does not count. Counting it
    let `f(now=None)` pass as a use: that call names the parameter while
    discarding the clock it holds, which is exactly the decorative parameter
    this guard exists to catch. Forwarding the clock, `f(now=now)`, still
    counts, because the keyword's value is itself a read of `now`.
    """
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load):
            return True
    return False


class TestTheClockIsNotDecorative:
    """Structural guard: a `now=` parameter must mean something.

    🔴 WHY THIS EXISTS, and it is a guard against a well-intentioned edit
    rather than against a bug. An earlier dispatch on this module asked for
    the clock to be threaded through its write paths, and that instruction was
    too wide: some of these functions have no clock to thread. A `now=` on a
    function that never consults it PROMISES DETERMINISM WHERE THERE IS
    NOTHING TO DETERMINE — and a test written against that promise passes for
    no reason at all, which is strictly worse than no test, because it reports
    coverage of a behaviour that does not exist.

    The reader this stops is someone tidying up: they see `append_record(...,
    now=None)` beside `save_records(...)` without one, read it as an
    oversight, and "finish the job".
    """

    def test_a_now_parameter_is_never_DECORATIVE(self):
        """THE GENERAL INVARIANT, and it is derived rather than listed.

        Every function taking `now=` must actually REFERENCE it — in
        arithmetic, in a comparison, or by forwarding it onward. This is
        deliberately NOT 'must read a clock': `_record_expired` reads no clock
        and is correct, because it does the subtraction itself, and a
        reads-a-clock rule would have false-fired on it. Nor is it 'must
        forward': the same function forwards nothing.

        A LIST OF FUNCTION NAMES WOULD HAVE GONE STALE ALREADY. The set of
        clock-threaded functions on this module changed once during this
        branch, so this arm computes its own population from the source every
        run and cannot describe a module that has moved.
        """
        offenders = [
            fn.name for fn in _module_functions()
            if "now" in _params(fn) and not _references(fn, "now")
        ]
        assert offenders == [], (
            "these functions accept `now=` and never use it: %s. A clock "
            "parameter that is ignored advertises determinism the function "
            "does not provide, and any test written against it passes without "
            "exercising anything. Either use it or remove it." % (offenders,)
        )

    # MEASURED FROM SOURCE, NOT COPIED FROM A LIST. Every state-file write in
    # the module goes through `state_file.locked_update` (or its `write_text`),
    # the one open path that writes; `state_file.read_text` only reads. The
    # clockless writers are the functions in THIS module that reach that path
    # with no `now=` and no clock read. Writers that DO take a clock
    # (`_atomic_update_records`, `append_record`, `discharge_acknowledged_for_owner`,
    # `stamp_idled_at`, `record_background_launch`) are outside this list on
    # purpose. Listed as a DECISION rather than as an inventory: if the write
    # path is restructured, re-derive it.
    CLOCKLESS_WRITERS = (
        "save_records",
        "update_unflagged_idle_counts",
        "save_unflagged_idle_counts",
    )

    def test_the_deliberately_clockless_writers_take_no_now(self):
        """The writers in CLOCKLESS_WRITERS persist bytes and consult no clock, ON PURPOSE.

        They are pure I/O: given the content, they write it. None of them
        prunes, compares, stamps or expires anything, so there is no moment at
        which 'what time is it' could change what they do.

        IF YOU ARE HERE BECAUSE THIS WENT RED, the question to answer first is
        NOT 'how do I thread the clock' but 'what in this function now depends
        on the time'. If the honest answer is nothing, the `now=` should come
        back out. If something genuinely does, delete the name from the tuple
        above in the same commit and say what changed — the tuple is a record
        of a decision, and moving it is a decision too.
        """
        by_name = {fn.name: fn for fn in _module_functions()}
        missing = [n for n in self.CLOCKLESS_WRITERS if n not in by_name]
        assert missing == [], (
            "named clockless writers no longer exist in the module: %s. They "
            "were renamed or removed, so this guard is now pointing at "
            "nothing — re-derive the set rather than deleting the arm."
            % (missing,)
        )
        acquired = [n for n in self.CLOCKLESS_WRITERS if "now" in _params(by_name[n])]
        assert acquired == [], (
            "these writers acquired a `now=` parameter: %s. They read no clock "
            "and decide nothing from the time, so the parameter cannot change "
            "their behaviour — it only promises a determinism they do not "
            "have. If one genuinely became time-dependent, remove it from "
            "CLOCKLESS_WRITERS in the same commit and say what changed."
            % (acquired,)
        )


class TestTheLaunchPathThreadsOneClock:
    """`record_background_launch(now=)` stamps AND prunes on the clock it is
    given, never on the real one.

    The launch writes a new row and, in the same locked write, prunes rows
    older than the 24-hour TTL. If one step reads the injected clock and the
    other reads the real one, the launch stamps at one time and prunes at
    another, which no real clock can produce. The structural guard above
    cannot see that: dropping `now=` from one call leaves the function still
    reading `now` elsewhere.

    THE EXISTING ROW IS STAMPED IN THE YEAR 2000. Correct code never reads the
    real clock here, so that date cannot age out. Any real clock is more than
    24 hours later, so a step that falls back to the real clock prunes the row,
    and the arm sees it.
    """

    LAUNCHER = "clock-coder"
    SESSION_ID = "clock-arm-session"
    PROJECT_DIR = "/clock-arm/project"
    CLOCK_TEAM = "session-clockarm"
    OLD = datetime(2000, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    NOW = OLD + timedelta(hours=1)

    @pytest.fixture
    def rows_after_launch(self, tmp_path, monkeypatch):
        """Seed one row at OLD, launch at NOW, return the rows as written."""
        import json

        import shared.pact_context as pact_context
        from shared import background_work as bw
        from shared.pact_context import project_slug

        config = tmp_path / ".claude"
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", self.PROJECT_DIR)

        def write(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")

        write(config / "teams" / self.CLOCK_TEAM / "config.json", {
            "leadSessionId": self.SESSION_ID,
            "members": [{"name": self.LAUNCHER,
                         "agentId": f"{self.LAUNCHER}@{self.CLOCK_TEAM}",
                         "agentType": "pact-backend-coder",
                         "backendType": "in-process"}],
        })
        write(config / "pact-sessions" / project_slug(self.PROJECT_DIR)
              / self.SESSION_ID / "pact-session-context.json", {
            "session_id": self.SESSION_ID,
            "project_dir": self.PROJECT_DIR,
            "team_name": self.CLOCK_TEAM,
        })
        write(config / "tasks" / self.CLOCK_TEAM / "13.json",
              {"id": "13", "status": "in_progress", "owner": self.LAUNCHER})
        assert bw.save_records([{
            "agent_name": self.LAUNCHER,
            "session_id": self.SESSION_ID,
            "task_ids": ["13"],
            "registered_at": self.OLD.isoformat(),
        }], team_name=self.CLOCK_TEAM) is True

        frame = {
            "hook_event_name": "PostToolUse",
            "session_id": self.SESSION_ID,
            "tool_name": "Bash",
            "agent_type": self.LAUNCHER,
            "agent_id": "0123456789abcdef",
            "tool_input": {"command": "sleep 5", "run_in_background": True},
        }
        pact_context.init(frame)
        assert bw.record_background_launch(frame, now=self.NOW) is True
        registry = config / "teams" / self.CLOCK_TEAM / "background_work.json"
        return json.loads(registry.read_text(encoding="utf-8")).get("records", [])

    def test_the_launch_PRUNES_on_the_injected_clock(self, rows_after_launch):
        stamps = sorted(r.get("registered_at") for r in rows_after_launch)
        assert len(rows_after_launch) == 2, (
            "the launch pruned a row stamped one hour before the injected "
            "clock, so its prune ran on the real clock instead. Rows left: %r"
            % (stamps,)
        )


class TestOneDischargePassPerIdle:
    """An idle retires every acknowledged record in ONE registry update."""

    @pytest.fixture(autouse=True)
    def _isolated_team(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def test_an_idle_discharges_every_owned_task_in_one_registry_write(self, monkeypatch):
        """30 owned tasks, each with a covering wait and its own record: one write drops all 30."""
        from shared import background_work as bw
        from shared import state_file

        import teammate_idle

        owner = "probe-coder"
        tasks = [
            _task(task_id=str(i), wait=_wait(T0 + timedelta(minutes=1)))
            for i in range(30)
        ]
        assert bw.save_records(
            [_record(task_ids=[str(i)], registered_at=_iso(T0)) for i in range(30)],
            team_name=TEAM,
        ) is True
        registry = bw.registry_path(TEAM)
        registry_writes = []
        real_locked_update = state_file.locked_update

        def counting_locked_update(path, apply, root, *args, **kwargs):
            if Path(path) == registry:
                registry_writes.append(path)
            return real_locked_update(path, apply, root, *args, **kwargs)

        monkeypatch.setattr(state_file, "locked_update", counting_locked_update)
        teammate_idle.check_unflagged_background(tasks, owner, TEAM, now=T0)

        assert len(registry_writes) == 1, (
            f"the idle made {len(registry_writes)} registry updates for 30 owned "
            "tasks; the discharge must be one pass"
        )
        assert load_records_for_discharge(TEAM, now=T0) == []


# ---------------------------------------------------------- frame route


class TestFrameTeamAndNameReadsATeammateIdleFrame:
    """The frame route: a TeammateIdle frame's own `team_name` and
    `teammate_name`, trusted only when that name is a member of that team."""

    TEAM = "session-frameroute"
    MEMBER = "frame-coder"

    @pytest.fixture
    def config(self, tmp_path, monkeypatch):
        import json

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        team_dir = tmp_path / "teams" / self.TEAM
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(json.dumps({
            "leadSessionId": "frame-lead-session",
            "members": [{"name": self.MEMBER, "agentId": f"{self.MEMBER}@{self.TEAM}",
                         "agentType": "pact-backend-coder"}],
        }))
        return tmp_path

    def _frame(self, team=None, name=None, session_id="frame-idle-session"):
        return {"hook_event_name": "TeammateIdle", "session_id": session_id,
                "team_name": team if team is not None else self.TEAM,
                "teammate_name": name if name is not None else self.MEMBER}

    def test_frame_team_and_name_resolves_a_teammate_idle_frame_without_context(self, config):
        """REVERT PROOF. No context and no registry entry, so only the frame route
        can resolve it."""
        from shared.background_work import frame_team_and_name

        assert frame_team_and_name(self._frame()) == (self.TEAM, self.MEMBER)


