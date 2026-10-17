"""dispatch_site spawn leg — the Agent-spawn write that catches owners written
BEFORE their spawn.

REGRESSION (acme e1baab9f, repacted 1.3.2): 7 agent_dispatch events, 2
dispatch_site, 0 journal_emit_skipped. The lead wrote every Task-B owner a few
seconds before the Agent spawn; at that owner write the name was not yet a
team-config member, so the owner-write leg in task_lifecycle_gate could not
resolve it and returned without a trace. The spawn leg in journal_emitter
writes the site once the spawn names the specialist, under the same O_EXCL
marker, and records a skip for a specialist spawn that owns no work task.

Real team config, task store, marker root and journal file; only the spawn
payload is synthetic.
"""
import json
from pathlib import Path

import pytest

import journal_emitter as emitter
import task_lifecycle_gate as tlg
import shared.session_journal as sj
from shared import dispatch_site

TEAM = "session-spawnleg"
LEAD = "repacted:pact-orchestrator"
STAMP = {"novelty": 2, "novelty_rationale": "x", "scope": 2, "uncertainty": 2,
         "risk": 2, "total": 8}


@pytest.fixture
def env(tmp_path, monkeypatch, pact_context):
    import shared.dispatch_helpers as dh
    import shared.pact_context as ctx

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    plugin_root = tmp_path / "plugin"
    (plugin_root / "agents").mkdir(parents=True)
    for at in ("pact-backend-coder", "pact-secretary"):
        (plugin_root / "agents" / f"{at}.md").write_text("---\nname: x\n---\n", "utf-8")
    monkeypatch.setattr(ctx, "get_plugin_root", lambda: str(plugin_root))
    dh._specialist_registry.cache_clear()
    set_members(tmp_path, [])            # lead-only roster: nobody spawned yet
    pact_context(team_name=TEAM, session_id="s1", project_dir=str(tmp_path))
    return tmp_path


def set_members(root, members, team=TEAM):
    cfg = root / ".claude" / "teams" / team / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps({"leadSessionId": "s1", "members": [
        {"name": "team-lead", "agentType": "team-lead"}, *members]}), "utf-8")


def seed(root, task_id, subject, owner, status="pending", metadata=None, team=TEAM):
    d = root / ".claude" / "tasks" / team
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps({
        "id": task_id, "subject": subject, "owner": owner, "status": status,
        "metadata": metadata or {}}), "utf-8")


def owner_write(task_id, owner):
    return {"tool_name": "TaskUpdate", "agent_type": LEAD,
            "tool_input": {"taskId": task_id, "owner": owner}}


def spawn(name, subagent_type="pact-backend-coder"):
    ti = {"name": name, "team_name": TEAM}
    if subagent_type is not None:
        ti["subagent_type"] = subagent_type
    return ti


def journal(type_):
    p = Path(sj.get_journal_path())
    if not p.is_file():
        return []
    return [e for e in (json.loads(l) for l in p.read_text("utf-8").splitlines() if l.strip())
            if e.get("type") == type_]


def seed_pair(root, name="logging-coder", a="8", b="9", metadata=None):
    seed(root, a, f"backend-coder: TEACHBACK for {name}", name)
    seed(root, b, f"backend-coder: implement {name}", name, metadata=metadata)


class TestOwnerBeforeSpawnRegression:
    def test_owner_written_before_spawn_still_yields_one_site(self, env):
        """The live order: owner writes, then the spawn. Pre-fix: zero sites."""
        seed_pair(env, metadata={"variety": STAMP})
        tlg.evaluate_lifecycle(owner_write("8", "logging-coder"))
        tlg.evaluate_lifecycle(owner_write("9", "logging-coder"))
        assert journal("dispatch_site") == [], "control: the owner cannot resolve yet"

        set_members(env, [{"name": "logging-coder", "agentType": "repacted:pact-backend-coder"}])
        emitter.emit_spawn_dispatch_sites(spawn("logging-coder", "repacted:pact-backend-coder"), TEAM)
        [site] = journal("dispatch_site")
        assert site["task_id"] == "9"
        assert site["variety"] == {"novelty": 2, "scope": 2, "uncertainty": 2, "risk": 2, "total": 8}

        # A later owner-bearing write (the teammate's claim) must not add a second site.
        tlg.evaluate_lifecycle(owner_write("9", "logging-coder"))
        emitter.emit_spawn_dispatch_sites(spawn("logging-coder"), TEAM)
        assert len(journal("dispatch_site")) == 1
        assert journal("journal_emit_skipped") == []

    def test_site_written_at_owner_write_is_not_repeated_at_spawn(self, env):
        set_members(env, [{"name": "logging-coder", "agentType": "pact-backend-coder"}])
        seed_pair(env)
        tlg.evaluate_lifecycle(owner_write("9", "logging-coder"))
        emitter.emit_spawn_dispatch_sites(spawn("logging-coder"), TEAM)
        assert [e["task_id"] for e in journal("dispatch_site")] == ["9"]

    def test_through_the_hook_process(self, env, monkeypatch):
        """main() on a real Agent payload writes the site to the real journal."""
        import io
        seed(env, "3", "CODE: x", "", status="in_progress")
        seed_pair(env)
        payload = {"tool_name": "Agent", "agent_type": LEAD, "session_id": "s1",
                   "tool_input": spawn("logging-coder")}
        monkeypatch.setattr(emitter.pact_context, "init", lambda _d: None)
        monkeypatch.setattr(emitter.pact_context, "is_canonical_journal_frame", lambda _d: True)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        assert emitter.main() == 0
        assert [e["task_id"] for e in journal("dispatch_site")] == ["9"]


class TestSkipIsCounted:
    def test_specialist_spawn_owning_no_work_task_records_a_skip(self, env):
        seed(env, "8", "backend-coder: TEACHBACK for x", "orphan")   # teachback only
        emitter.emit_spawn_dispatch_sites(spawn("orphan"), TEAM)
        [skip] = journal("journal_emit_skipped")
        assert skip["skipped_type"] == "dispatch_site"
        assert skip["cause"] == dispatch_site.SKIP_CAUSE_NO_OWNED_WORK_TASK
        assert skip["agent"] == "orphan"
        assert journal("dispatch_site") == []

    def test_a_failed_write_records_a_skip_and_releases_the_marker(self, env, monkeypatch):
        seed_pair(env)
        real = sj.append_event
        monkeypatch.setattr(sj, "append_event",
                            lambda e, **kw: False if e["type"] == "dispatch_site" else real(e, **kw))
        emitter.emit_spawn_dispatch_sites(spawn("logging-coder"), TEAM)
        [skip] = journal("journal_emit_skipped")
        assert (skip["skipped_type"], skip["task_id"]) == ("dispatch_site", "9")
        monkeypatch.setattr(sj, "append_event", real)
        emitter.emit_spawn_dispatch_sites(spawn("logging-coder"), TEAM)
        assert [e["task_id"] for e in journal("dispatch_site")] == ["9"], \
            "a released marker must let the next spawn write the site"


class TestBoundary:
    @pytest.mark.parametrize("tool_input", [
        {}, {"name": None}, {"name": ""}, {"name": "   "}, {"name": 7},
        {"name": ["logging-coder"]}, {"name": "logging-coder", "subagent_type": "general-purpose"},
        {"name": "logging-coder", "subagent_type": "pact-nonexistent"},
        {"name": "logging-coder", "subagent_type": "pact-backend-coder\u200b"},
    ])
    def test_malformed_or_foreign_spawns_write_nothing(self, env, tool_input):
        seed_pair(env)
        emitter.emit_spawn_dispatch_sites(tool_input, TEAM)
        assert journal("dispatch_site") == [] and journal("journal_emit_skipped") == []

    def test_non_str_subagent_type_falls_back_to_the_roster(self, env):
        set_members(env, [{"name": "logging-coder", "agentType": "pact-backend-coder"}])
        seed_pair(env)
        emitter.emit_spawn_dispatch_sites({"name": "logging-coder", "subagent_type": 42}, TEAM)
        assert [e["task_id"] for e in journal("dispatch_site")] == ["9"]

    def test_the_secretary_is_outside_the_population(self, env):
        seed(env, "18", "secretary: harvest", "secretary")
        emitter.emit_spawn_dispatch_sites(spawn("secretary", "pact-secretary"), TEAM)
        assert journal("dispatch_site") == [] and journal("journal_emit_skipped") == []

    def test_partial_and_malformed_task_files_are_ignored(self, env):
        seed_pair(env)
        d = env / ".claude" / "tasks" / TEAM
        (d / "20.json").write_text("{not json", "utf-8")
        (d / "21.json").write_text(json.dumps(["a list"]), "utf-8")
        (d / "22.json").write_text(json.dumps({"subject": "backend-coder: no id",
                                               "owner": "logging-coder", "status": "pending"}), "utf-8")
        seed(env, "23", "backend-coder: done", "logging-coder", status="completed")
        emitter.emit_spawn_dispatch_sites(spawn("logging-coder"), TEAM)
        assert [e["task_id"] for e in journal("dispatch_site")] == ["9"]

    def test_a_second_session_in_the_same_project_keeps_its_own_markers(self, env, pact_context):
        """Task ids restart per session team; a first session's marker must not
        suppress the second session's site for the same task id."""
        seed_pair(env)
        emitter.emit_spawn_dispatch_sites(spawn("logging-coder"), TEAM)
        other = "session-spawnleg2"
        seed(env, "8", "backend-coder: TEACHBACK for y", "logging-coder", team=other)
        seed(env, "9", "backend-coder: implement y", "logging-coder", team=other)
        pact_context(team_name=other, session_id="s2", project_dir=str(env))
        emitter.emit_spawn_dispatch_sites(spawn("logging-coder"), other)
        assert [e["task_id"] for e in journal("dispatch_site")] == ["9"], \
            "the second session writes its own site into its own journal"

    def test_skip_event_with_a_missing_field_is_rejected_by_the_schema(self):
        ok, _ = sj._validate_event_schema(sj.make_event(
            "journal_emit_skipped", skipped_type="dispatch_site", agent="x"))
        assert not ok
        ok, _ = sj._validate_event_schema(sj.make_event(
            "journal_emit_skipped", skipped_type="dispatch_site", cause="c", agent=""))
        assert not ok, "an empty agent is as useless as a missing one"
