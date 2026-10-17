"""
Location: pact-plugin/tests/test_journal_emitter.py (repacted EVENTS, m8a)
Summary: Event parity for hooks/journal_emitter.py against the command blocks at
         repacted-m0, plus the emitter's derivations.

PARITY. tests/fixtures/journal_parity/m0_blocks.json holds the 22 rows m0's own
session_journal writer produced from the 22 fenced blocks that write variety_assessed,
agent_dispatch or phase_transition (record_m0.py; no m0 fixture journal existed).
The test replays each block's moment as the tool call the lead makes there, against a
task store for the same scenario. It asserts the hook writes the same row: type, and
every field except agent_dispatch.scope (the assigned paths, which cannot be derived
and which nothing reads).
Three blocks are RETAINED: comPACT's two agent_dispatch writes and its CODE
phase_transition. comPACT has no phase tasks, so the hook cannot derive them, never
guesses, and COMMANDS keeps those blocks. Counts: 22 write invocations in 22 fenced
blocks; 19 emitted by the hook; 3 retained.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import journal_emitter as emitter
from shared import dispatch_helpers, session_journal

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "journal_parity" / "m0_blocks.json"
TEAM = "session-parity01"
RETAINED = {"commands/comPACT.md:288", "commands/comPACT.md:359", "commands/comPACT.md:452"}

RATIONALES = {f"{d}_rationale": "shape" for d in ("novelty", "scope", "uncertainty", "risk")}

# The scenario record_m0.py substituted into the blocks, as a task store:
# id -> (subject, owner). Feature tasks carry no role prefix.
TASKS = {
    "1": ("implement widget", ""),
    "2": ("PREPARE: widget", ""), "3": ("ARCHITECT: widget", ""),
    "4": ("CODE: widget", ""), "5": ("TEST: widget", ""),
    "6": ("preparer: TEACHBACK for widget", "preparer"), "7": ("preparer: research widget", ""),
    "8": ("architect: TEACHBACK for widget", "architect"), "9": ("architect: design widget", ""),
    "10": ("backend-coder: TEACHBACK for widget", "backend-coder"),
    "11": ("backend-coder: implement widget", ""),
    "12": ("test-engineer: TEACHBACK for widget", "test-engineer"),
    "13": ("test-engineer: test widget", ""),
    "14": ("backend-coder-3: TEACHBACK for the finding", "backend-coder-3"),
    "15": ("backend-coder-3: fix the finding", ""),
    "16": ("architect-2: TEACHBACK for the plan", "architect-2"),
    "17": ("architect-2: plan widget", ""),
    "20": ("fix login", ""),
    "21": ("backend-coder-2: TEACHBACK for login", "backend-coder-2"),
    "22": ("backend-coder-2: fix login", ""),
    "23": ("frontend-coder: TEACHBACK for login", "frontend-coder"),
    "24": ("frontend-coder: fix login screen", ""),
    "30": ("refine auth", ""),
    "31": ("architect-3: TEACHBACK for auth", "architect-3"),
    "32": ("architect-3: design auth", ""),
}


SPECIALIST = "pact-backend-coder"


class Store:
    def __init__(self, root: Path):
        self.dir = root / ".claude" / "tasks" / TEAM
        self.dir.mkdir(parents=True)
        for task_id, (subject, owner) in TASKS.items():
            self.write(task_id, subject=subject, owner=owner, status="pending")
        # The team config the dispatch-wiring predicate resolves owners through.
        names = sorted({owner for _, owner in TASKS.values() if owner})
        config = root / ".claude" / "teams" / TEAM / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"name": TEAM, "members": [
            {"name": name, "agentType": SPECIALIST} for name in names]}))

    def write(self, task_id, **fields):
        path = self.dir / f"{task_id}.json"
        task = json.loads(path.read_text()) if path.exists() else {"id": task_id, "metadata": {}}
        task.update(fields)
        path.write_text(json.dumps(task))

    def phase(self, running):
        for task_id in ("2", "3", "4", "5"):
            self.write(task_id, status="in_progress" if task_id == running else "pending")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    # The registry of agents/pact-*.md comes from the plugin root in the session
    # context: point it at a plugin root holding the one specialist the members use.
    plugin_root = tmp_path / "plugin"
    (plugin_root / "agents").mkdir(parents=True)
    (plugin_root / "agents" / f"{SPECIALIST}.md").write_text("---\nname: x\n---\n")
    monkeypatch.setattr(dispatch_helpers.pact_context, "get_plugin_root", lambda: str(plugin_root))
    dispatch_helpers._specialist_registry.cache_clear()
    return Store(tmp_path)


def variety_of(row_event):
    return dict(row_event["variety"])


def create(task_id, variety):
    return {"tool_name": "TaskCreate", "tool_input": {"subject": TASKS[task_id][0],
            "metadata": {"variety": variety}}, "tool_response": {"task": {"id": task_id}}}


def update(task_id, **tool_input):
    return {"tool_name": "TaskUpdate", "tool_input": dict(taskId=task_id, **tool_input)}


def spawn(name):
    return {"tool_name": "Agent", "tool_input": {"name": name, "team_name": TEAM,
                                                 "subagent_type": "pact-backend-coder"}}


def replay(block: str, row_event: dict, store: Store) -> list[dict]:
    """Set the store to the block's moment, send the tool call the lead makes there,
    and return what the hook would write."""
    kind = row_event["type"]
    if kind == "variety_assessed":
        task_id = row_event["task_id"]
        variety = variety_of(row_event)
        if "scope" in row_event:                                   # a Task B stamp
            store.write(task_id, metadata={"variety": dict(variety, **RATIONALES)})
            payload = create(task_id, dict(variety, **RATIONALES))
        elif block.startswith("commands/orchestrate.md"):          # TaskUpdate(featureTaskId)
            store.write(task_id, metadata={"variety": variety})
            payload = update(task_id, metadata={"variety": variety})
        else:                                                      # feature TaskCreate
            store.write(task_id, metadata={"variety": variety})
            payload = create(task_id, variety)
    elif kind == "agent_dispatch":
        task_id, name = row_event["task_id"], row_event["agent"]
        teachback = str(int(task_id) - 1)
        store.write(teachback, status="completed")
        store.write(task_id, owner=name, blockedBy=[teachback])
        store.phase({"PREPARE": "2", "ARCHITECT": "3", "CODE": "4", "TEST": "5"}[row_event["phase"]]
                    if not block.startswith("commands/comPACT.md") else None)
        payload = spawn(name)
    else:
        phase_task = {"PREPARE": "2", "ARCHITECT": "3", "CODE": "4", "TEST": "5"}[row_event["phase"]]
        if block.startswith("commands/comPACT.md"):
            store.write("20", status="completed")
            return emitter.events_for(update("20", status="completed"), TEAM)
        if row_event["status"] == "started":
            store.write(phase_task, status="in_progress")
            payload = update(phase_task, status="in_progress")
        elif row_event["status"] == "completed":
            store.write(phase_task, status="completed")
            payload = update(phase_task, status="completed")
        else:
            meta = {"skipped": True, "skip_reason": row_event["skip_reason"]}
            store.write(phase_task, status="completed", metadata=meta)
            payload = update(phase_task, status="completed", metadata=meta)
    return emitter.events_for(payload, TEAM)


def comparable(event: dict) -> dict:
    drop = {"v", "ts"} | ({"scope"} if event.get("type") == "agent_dispatch" else set())
    return {k: v for k, v in event.items() if k not in drop}


ROWS = json.loads(FIXTURE.read_text())["rows"]


class TestEventParity:
    def test_the_fixture_is_the_22_m0_block_writes(self):
        assert json.loads(FIXTURE.read_text())["tag"] == "repacted-m0"
        assert len(ROWS) == 22
        counts = {}
        for row in ROWS:
            counts[row["event"]["type"]] = counts.get(row["event"]["type"], 0) + 1
        assert counts == {"variety_assessed": 12, "agent_dispatch": 6, "phase_transition": 4}

    @pytest.mark.parametrize("row", [r for r in ROWS if r["block"] not in RETAINED],
                             ids=lambda r: r["block"])
    def test_the_hook_writes_the_row_the_block_wrote(self, store, row):
        written = replay(row["block"], row["event"], store)
        assert [comparable(e) for e in written if e["type"] == row["event"]["type"]] == \
            [comparable(row["event"])]

    @pytest.mark.parametrize("row", [r for r in ROWS if r["block"] in RETAINED],
                             ids=lambda r: r["block"])
    def test_underivable_rows_are_omitted_never_guessed(self, store, row):
        written = replay(row["block"], row["event"], store)
        assert [e for e in written if e["type"] == row["event"]["type"]] == []

    @pytest.mark.parametrize("row", [r for r in ROWS if "scope" in r["event"]
                                     and r["event"]["type"] == "variety_assessed"],
                             ids=lambda r: r["block"])
    def test_the_authoritative_wiring_path_writes_the_same_dispatch_row(self, store, row):
        """The structural signal: TaskUpdate(B, owner=<specialist>, addBlockedBy=[A])."""
        task_id = row["event"]["task_id"]
        teachback = str(int(task_id) - 1)
        name = TASKS[teachback][1]
        store.write(task_id, blockedBy=[teachback],
                    metadata={"variety": dict(row["event"]["variety"], **RATIONALES)})
        written = emitter.events_for(update(task_id, owner=name, addBlockedBy=[teachback]), TEAM)
        assert [comparable(e) for e in written] == [comparable(row["event"])]

    def test_every_distinct_shape_is_covered(self):
        shapes = {(r["event"]["type"], "scope" in r["event"], r["block"].split(":")[0])
                  for r in ROWS if r["block"] not in RETAINED}
        assert {("variety_assessed", False, "commands/orchestrate.md"),     # feature, full dims
                ("variety_assessed", False, "commands/comPACT.md"),         # feature, total only
                ("variety_assessed", True, "commands/orchestrate.md"),      # dispatch
                ("agent_dispatch", True, "commands/orchestrate.md"),
                ("phase_transition", False, "commands/orchestrate.md")} <= shapes
        assert {r["event"]["status"] for r in ROWS if r["event"]["type"] == "phase_transition"
                and r["block"] not in RETAINED} == {"started", "completed", "skipped"}


class TestDerivations:
    def test_project_variety_drops_rationales(self):
        assert emitter.project_variety(dict(novelty=1, total=5, **RATIONALES)) == {"novelty": 1, "total": 5}
        assert emitter.project_variety("x") == {}

    def test_a_task_b_is_recognised_before_and_after_wiring(self, store):
        before = json.loads((store.dir / "7.json").read_text())
        tasks = [json.loads(p.read_text()) for p in store.dir.glob("*.json")]
        assert emitter.is_dispatch_task(before, tasks)                  # open TEACHBACK sibling
        store.write("6", status="completed")
        tasks = [json.loads(p.read_text()) for p in store.dir.glob("*.json")]
        assert not emitter.is_dispatch_task(before, tasks)              # gate closed, not wired
        wired = dict(before, blockedBy=["6"])
        assert emitter.is_dispatch_task(wired, tasks)                   # wired to the gate

    def test_feature_and_teachback_tasks_are_not_dispatch(self, store):
        tasks = [json.loads(p.read_text()) for p in store.dir.glob("*.json")]
        assert not emitter.is_dispatch_task({"id": "1", "subject": "implement widget"}, tasks)
        assert not emitter.is_dispatch_task({"id": "6", "subject": "preparer: TEACHBACK for widget"}, tasks)

    def test_primary_work_task_is_unique_or_nothing(self):
        tasks = [{"id": "6", "subject": "preparer: TEACHBACK for x", "owner": "p", "status": "pending"},
                 {"id": "7", "subject": "preparer: research x", "owner": "p", "status": "pending"}]
        assert emitter.primary_work_task("p", tasks)["id"] == "7"
        tasks.append({"id": "9", "subject": "preparer: more", "owner": "p", "status": "in_progress"})
        assert emitter.primary_work_task("p", tasks) is None            # ambiguous: omit
        assert emitter.primary_work_task("q", tasks) is None

    def test_no_running_phase_means_no_agent_dispatch(self, store):
        store.write("7", owner="preparer", blockedBy=["6"])
        store.phase(None)
        assert emitter.events_for(spawn("preparer"), TEAM) == []

    def test_a_non_phase_status_change_writes_no_transition(self, store):
        store.write("7", status="in_progress")
        assert [e for e in emitter.events_for(update("7", status="in_progress"), TEAM)
                if e["type"] == "phase_transition"] == []

    def test_the_wiring_update_re_emits_a_task_b_stamp(self, store):
        store.write("7", metadata={"variety": dict(novelty=1, scope=2, uncertainty=2, risk=1,
                                                   total=6, **RATIONALES)}, blockedBy=["6"])
        [event] = emitter.events_for(update("7", owner="preparer", addBlockedBy=["6"]), TEAM)
        assert event["scope"] == "dispatch" and event["variety"]["total"] == 6

    def test_the_wiring_update_of_a_non_task_b_writes_nothing(self, store):
        store.write("1", metadata={"variety": {"total": 7}})
        assert emitter.events_for(update("1", addBlockedBy=["2"]), TEAM) == []

    def test_the_wiring_decides_even_when_the_subject_breaks_the_convention(self, store):
        """The subject-prefix guess needs `name: ...`; the structural wiring does not.
        A Task B titled without the role prefix is still a dispatch once wired."""
        store.write("7", subject="research the widget", blockedBy=["6"],
                    metadata={"variety": {"total": 6}})
        [event] = emitter.events_for(update("7", owner="preparer", addBlockedBy=["6"]), TEAM)
        assert event["scope"] == "dispatch"

    def test_a_wiring_to_a_non_specialist_owner_is_not_a_dispatch(self, store):
        store.write("7", metadata={"variety": {"total": 6}}, blockedBy=["6"])
        store.write("6", status="completed")
        assert emitter.events_for(update("7", owner="not-a-member", addBlockedBy=["6"]), TEAM) == []

    def test_b_before_a_ends_with_the_dispatch_row_winning_for_readers(self, store, tmp_path):
        """A Task B stamped before its Task A reads as feature-level at create time (no
        gate visible yet); the wiring then writes the dispatch row. Readers resolve per
        task_id by the LATEST row (session_journal.latest_variety_by_task)."""
        session = tmp_path / "session"
        (store.dir / "6.json").unlink()                           # Task A does not exist yet
        variety = dict(novelty=1, scope=2, uncertainty=2, risk=1, total=6)
        store.write("7", metadata={"variety": dict(variety, **RATIONALES)})
        for event in emitter.events_for(create("7", dict(variety, **RATIONALES)), TEAM):
            session_journal.append_event(event, session_dir=str(session))
        store.write("6", subject="preparer: TEACHBACK for widget", status="pending")
        store.write("7", blockedBy=["6"])
        for event in emitter.events_for(update("7", owner="preparer", addBlockedBy=["6"]), TEAM):
            session_journal.append_event(event, session_dir=str(session))
        rows = session_journal.read_events_from(str(session), "variety_assessed")
        assert ["scope" in r for r in rows] == [False, True]        # stale feature, then dispatch
        latest = session_journal.latest_variety_by_task(rows)["7"]
        assert latest["scope"] == "dispatch" and latest["variety"] == variety

    def test_a_restamp_of_an_accepted_task_b_is_still_dispatch(self, store):
        store.write("6", status="completed")
        store.write("7", blockedBy=["6"], owner="preparer")
        [event] = emitter.events_for(update("7", metadata={"variety": {"total": 8}}), TEAM)
        assert event["scope"] == "dispatch" and event["variety"] == {"total": 8}


class TestHookProcess:
    def run_main(self, payload, *, canonical=True, team=TEAM):
        written = []
        with patch.object(emitter.pact_context, "init"), \
             patch.object(emitter.pact_context, "get_pact_context", return_value={"team_name": team}), \
             patch.object(emitter.pact_context, "is_canonical_journal_frame", return_value=canonical), \
             patch.object(emitter.session_journal, "append_event", side_effect=lambda e: written.append(e) or True), \
             patch("sys.stdin", io.StringIO(json.dumps(payload))):
            out = io.StringIO()
            with patch("sys.stdout", out):
                assert emitter.main() == 0
        assert json.loads(out.getvalue()) == {"suppressOutput": True}
        return written

    def test_main_writes_through_the_journal_writer(self, store):
        store.write("2", status="in_progress")
        [event] = self.run_main(update("2", status="in_progress"))
        assert event["type"] == "phase_transition" and event["status"] == "started"

    def test_a_non_canonical_frame_writes_nothing(self, store):
        store.write("2", status="in_progress")
        assert self.run_main(update("2", status="in_progress"), canonical=False) == []

    def test_no_team_writes_nothing(self, store):
        store.write("2", status="in_progress")
        assert self.run_main(update("2", status="in_progress"), team="") == []

    @pytest.mark.parametrize("stdin", ["not json", "[]", "{}"])
    def test_unreadable_payloads_are_silent(self, stdin):
        with patch("sys.stdin", io.StringIO(stdin)):
            out = io.StringIO()
            with patch("sys.stdout", out):
                assert emitter.main() == 0
        assert json.loads(out.getvalue()) == {"suppressOutput": True}

    def test_a_handoff_write_records_its_size_and_writer_never_its_text(self, store):
        store.write("11", owner="backend-coder", status="in_progress")
        handoff = {"rev": 1, "produced": ["ü.py"], "decisions": []}
        [event] = self.run_main(update("11", metadata={"handoff": handoff, "intentional_wait": None}))
        assert {k: event[k] for k in ("type", "task_id", "key", "agent", "bytes")} == {
            "type": "metadata_write", "task_id": "11", "key": "handoff", "agent": "backend-coder",
            "bytes": len(json.dumps(handoff, ensure_ascii=False).encode("utf-8"))}
        assert "ü.py" not in json.dumps(event)

    def test_an_error_is_swallowed(self, store, monkeypatch):
        monkeypatch.setattr(emitter, "events_for", lambda *a: 1 / 0)
        assert self.run_main(update("2", status="in_progress")) == []


class TestJournalRoundTrip:
    def test_hook_events_pass_the_journal_schema(self, store, tmp_path):
        session = tmp_path / "session"
        for row in [r for r in ROWS if r["block"] not in RETAINED]:
            for event in replay(row["block"], row["event"], store):
                assert session_journal.append_event(event, session_dir=str(session))
        assert len(session_journal.read_events_from(str(session))) == 19
