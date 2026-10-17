"""M2 correctness fixes (pact-slim-routers PR-0), each sabotage-checked at M2.

DEFECT 1: a real local agent file silently shadows the plugin's agent.
setup_plugin_symlinks never replaces a real file at an agent's destination, and
the platform resolves the bare name to that copy, so a stale local copy wins
every bare-name dispatch with no signal. The agents loop now names differing
copies ("shadowed"), session_init routes that like "failed" (to the user), and
only on launch events.

DEFECT 2: the team-config agentType readers compared the raw value, so a
member recorded under its plugin namespace (`repacted:pact-secretary`, or the
legacy `PACT:pact-secretary`) was not recognised. pact_context.member_agent_type
strips the namespace at the four readers.
"""

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

SPELLINGS = ["pact-secretary", "repacted:pact-secretary", "PACT:pact-secretary"]
CODER_SPELLINGS = ["pact-backend-coder", "repacted:pact-backend-coder", "PACT:pact-backend-coder"]


# =============================================================================
# Defect 1: shadowed agents
# =============================================================================

def _plugin_and_config(tmp_path, monkeypatch, agents=("pact-alpha", "pact-beta")):
    plugin_root = tmp_path / "plugin"
    (plugin_root / "agents").mkdir(parents=True)
    for name in agents:
        (plugin_root / "agents" / f"{name}.md").write_text(f"plugin body of {name}\n")
    agents_dst = tmp_path / ".claude" / "agents"
    agents_dst.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin_root))
    return plugin_root, agents_dst


class TestShadowedAgentReport:
    def test_a_differing_real_file_is_named_and_left_in_place(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        _, agents_dst = _plugin_and_config(tmp_path, monkeypatch)
        (agents_dst / "pact-alpha.md").write_text("a stale local copy\n")

        result = setup_plugin_symlinks()

        assert "1 agents shadowed by local copies that differ from the plugin: pact-alpha.md" in result
        assert "<!-- pact-user-override -->" in result
        assert (agents_dst / "pact-alpha.md").read_text() == "a stale local copy\n"
        assert not (agents_dst / "pact-alpha.md").is_symlink()
        assert str(tmp_path) not in result, "names only, never a path"

    def test_an_identical_real_file_is_not_reported(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        plugin_root, agents_dst = _plugin_and_config(tmp_path, monkeypatch)
        (agents_dst / "pact-alpha.md").write_bytes((plugin_root / "agents" / "pact-alpha.md").read_bytes())

        result = setup_plugin_symlinks()

        assert "shadowed" not in (result or "")

    def test_the_override_marker_on_its_own_line_silences_it(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        _, agents_dst = _plugin_and_config(tmp_path, monkeypatch)
        (agents_dst / "pact-alpha.md").write_text("my version\n  <!-- pact-user-override -->  \n")

        assert "shadowed" not in (setup_plugin_symlinks() or "")

    def test_the_marker_inside_other_text_does_not_count(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        _, agents_dst = _plugin_and_config(tmp_path, monkeypatch)
        (agents_dst / "pact-alpha.md").write_text("see <!-- pact-user-override --> above\n")

        assert "pact-alpha.md" in setup_plugin_symlinks()

    def test_several_are_counted_and_sorted(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        _, agents_dst = _plugin_and_config(tmp_path, monkeypatch)
        (agents_dst / "pact-beta.md").write_text("old\n")
        (agents_dst / "pact-alpha.md").write_text("old\n")

        assert "2 agents shadowed by local copies that differ from the plugin: pact-alpha.md, pact-beta.md" in setup_plugin_symlinks()

    def test_a_symlink_is_repointed_not_reported(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        plugin_root, agents_dst = _plugin_and_config(tmp_path, monkeypatch)
        elsewhere = tmp_path / "old-root" / "pact-alpha.md"
        elsewhere.parent.mkdir()
        elsewhere.write_text("old plugin body\n")
        (agents_dst / "pact-alpha.md").symlink_to(elsewhere)

        result = setup_plugin_symlinks()

        assert "shadowed" not in result
        assert (agents_dst / "pact-alpha.md").resolve() == (plugin_root / "agents" / "pact-alpha.md").resolve()

    def test_report_shadowed_false_suppresses_it(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        _, agents_dst = _plugin_and_config(tmp_path, monkeypatch)
        (agents_dst / "pact-alpha.md").write_text("a stale local copy\n")

        assert "shadowed" not in (setup_plugin_symlinks(report_shadowed=False) or "")

    def test_an_unreadable_copy_is_neither_shadowed_nor_failed(self, tmp_path, monkeypatch):
        from shared.symlinks import setup_plugin_symlinks

        _, agents_dst = _plugin_and_config(tmp_path, monkeypatch, agents=("pact-alpha",))
        (agents_dst / "pact-alpha.md").write_text("a stale local copy\n")

        with patch.object(Path, "read_bytes", side_effect=PermissionError("denied")):
            result = setup_plugin_symlinks()

        assert "shadowed" not in (result or "") and "failed" not in (result or "")


def _run_session_init(monkeypatch, tmp_path, source):
    from session_init import main

    project_dir = tmp_path / "project"
    project_dir.mkdir(exist_ok=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_dir))
    payload = {"session_id": "aabb1122-0000-0000-0000-0000000000d2", "source": source,
               "agent_type": "repacted:pact-orchestrator"}
    with patch("shared.session_init_common.ensure_project_memory_md", return_value=None), \
         patch("session_init.get_task_list", return_value=None), \
         patch("shared.session_resume._check_pr_state", return_value=""), \
         patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("sys.stdout", new_callable=io.StringIO) as stdout:
        with pytest.raises(SystemExit) as exit_info:
            main()
    assert exit_info.value.code == 0
    return json.loads(stdout.getvalue())


@pytest.mark.parametrize("source, reported", [("startup", True), ("resume", True), ("compact", False), ("clear", False)])
def test_session_init_sends_the_shadowed_notice_to_the_user_on_launch_only(monkeypatch, tmp_path, source, reported):
    _, agents_dst = _plugin_and_config(tmp_path, monkeypatch)
    (agents_dst / "pact-alpha.md").write_text("a stale local copy\n")

    output = _run_session_init(monkeypatch, tmp_path, source)

    system_message = output.get("systemMessage", "")
    additional = output.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert ("agents shadowed by local copies" in system_message) is reported
    assert "agents shadowed" not in additional


# =============================================================================
# Defect 2: namespaced agentType readers
# =============================================================================

@pytest.mark.parametrize("member, expected", [
    ({"agentType": "pact-secretary"}, "pact-secretary"),
    ({"agentType": "repacted:pact-secretary"}, "pact-secretary"),
    ({"agentType": "PACT:pact-secretary"}, "pact-secretary"),
    ({"agentType": "repacted:" "repacted:pact-secretary"}, "repacted:pact-secretary"),
    ({"agentType": "general-purpose"}, "general-purpose"),
    ({"agentType": 7}, None),
    ({}, None),
    ("not-a-dict", None),
])
def test_member_agent_type_strips_one_namespace(member, expected):
    from shared.pact_context import member_agent_type

    assert member_agent_type(member) == expected


def _team(tmp_path, members, team="t-m2"):
    team_dir = tmp_path / "teams" / team
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(json.dumps({"members": members}), encoding="utf-8")
    return team, str(tmp_path / "teams")


@pytest.mark.parametrize("agent_type", SPELLINGS)
def test_self_complete_exemption_resolves_every_spelling(tmp_path, agent_type):
    from shared.intentional_wait import is_self_complete_exempt

    team, teams_dir = _team(tmp_path, [{"name": "secretary", "agentType": agent_type}])
    assert is_self_complete_exempt({"owner": "secretary", "metadata": {}}, team, teams_dir) is True


@pytest.mark.parametrize("agent_type", SPELLINGS)
def test_teachback_exemption_resolves_every_spelling(tmp_path, agent_type):
    from shared.intentional_wait import is_teachback_exempt

    team, teams_dir = _team(tmp_path, [{"name": "secretary", "agentType": agent_type}])
    assert is_teachback_exempt("secretary", team, teams_dir) is True


@pytest.mark.parametrize("agent_type", CODER_SPELLINGS)
def test_a_coder_is_never_exempt_in_any_spelling(tmp_path, agent_type):
    from shared.intentional_wait import is_self_complete_exempt, is_teachback_exempt

    team, teams_dir = _team(tmp_path, [{"name": "backend-coder", "agentType": agent_type}])
    assert is_teachback_exempt("backend-coder", team, teams_dir) is False
    assert is_self_complete_exempt({"owner": "backend-coder", "metadata": {}}, team, teams_dir) is False


@pytest.mark.parametrize("agent_type", CODER_SPELLINGS)
def test_specialist_owner_resolves_every_spelling(tmp_path, monkeypatch, agent_type):
    import shared.dispatch_helpers as dispatch_helpers
    import shared.pact_context as pact_context
    from shared.dispatch_helpers import is_pact_specialist_owner

    plugin_root = tmp_path / "plugin"
    (plugin_root / "agents").mkdir(parents=True)
    (plugin_root / "agents" / "pact-backend-coder.md").write_text("---\nname: pact-backend-coder\n---\n")
    monkeypatch.setattr(pact_context, "get_plugin_root", lambda: str(plugin_root))
    dispatch_helpers._specialist_registry.cache_clear()
    team, teams_dir = _team(tmp_path, [{"name": "backend-coder", "agentType": agent_type},
                                       {"name": "explorer", "agentType": "general-purpose"}])
    try:
        assert is_pact_specialist_owner("backend-coder", team, teams_dir) is True
        assert is_pact_specialist_owner("explorer", team, teams_dir) is False
    finally:
        dispatch_helpers._specialist_registry.cache_clear()


@pytest.mark.parametrize("own_type", CODER_SPELLINGS)
@pytest.mark.parametrize("member_type", CODER_SPELLINGS)
def test_the_peer_fallback_filter_compares_both_sides_unqualified(tmp_path, own_type, member_type):
    from shared.peer_context import get_peer_context

    team, teams_dir = _team(tmp_path, [
        {"name": "coder-1", "agentType": member_type},
        {"name": "architect", "agentType": "repacted:pact-architect"},
    ])
    context = get_peer_context(own_type, team, agent_name="", teams_dir=teams_dir)

    assert "architect" in context
    assert "coder-1" not in context, "a same-type member must be filtered whatever its spelling"
