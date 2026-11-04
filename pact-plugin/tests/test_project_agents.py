"""
Location: pact-plugin/tests/test_project_agents.py
Summary: Specialists a project brings itself (hooks/shared/project_agents.py, the rule-5 project
         refusals of hooks/dispatch_gate.py, team policy agents.project_scope, bin/repact-agent).
         The reader parses repository-supplied files into a gate, so the rows here are the
         hostile ones: names that impersonate a shipped agent by frontmatter, case, Unicode
         form or YAML trick, files that are links, FIFOs, oversized or not UTF-8, bindings
         hidden where the model would not act on them, and the end-to-end row that a scaffolded
         agent passes the gate.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shared.plugin_identity import NAMESPACE

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CLI = PLUGIN_ROOT / "bin" / "repact-agent"
BOUND = '---\nname: {name}\ndescription: "x"\n---\n\nInvoke `Skill("repacted:teammate")` first.\n'
_TEAM = "pact-test"
_NAME = "mobile-1"


@pytest.fixture(autouse=True)
def _fresh_policy():
    from shared import team_policy

    team_policy.reset()
    yield
    team_policy.reset()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".claude" / "agents").mkdir(parents=True)
    return root


@pytest.fixture
def plugin(tmp_path):
    agents = tmp_path / "plugin" / "agents"
    agents.mkdir(parents=True)
    for stem in ("pact-architect", "pact-secretary", "pact-orchestrator"):
        (agents / f"{stem}.md").write_text(f"---\nname: {stem}\n---\n", encoding="utf-8")
    return agents.parent


def agent(project: Path, filename: str, text, sub: str = "") -> Path:
    path = project / ".claude" / "agents" / sub / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")
    return path


def verdict(project: Path, plugin: Path, subagent_type: str):
    from shared import project_agents as pa
    from shared.dispatch_helpers import shipped_specialists

    found = pa.scan(str(project), str(plugin))
    return pa.resolve(subagent_type, shipped_specialists(str(plugin)), found)


# --- the reader: what makes a file a specialist, and what a file can claim ---------------------

@pytest.mark.parametrize("text, why", [
    ('---\nname: pact-mobile\n---\n', "no binding at all"),])
def test_a_file_without_a_live_binding_is_unbound(project, plugin, text, why):
    agent(project, "pact-mobile.md", text)
    assert verdict(project, plugin, "pact-mobile").kind == "unbound", why


@pytest.mark.parametrize("filename, text, why", [
    ("pact-mobile.md", BOUND.format(name="pact-other"), "name differs from the file name"),])
def test_a_malformed_file_is_invalid_not_a_specialist(project, plugin, filename, text, why):
    agent(project, filename, text)
    found = verdict(project, plugin, "pact-mobile")
    assert found.kind in ("invalid", "none", "ambiguous") and found.kind != "project", why


def test_a_crlf_file_with_a_quoted_name_and_comment_is_a_specialist(project, plugin):
    text = BOUND.format(name="'pact-mobile' # the mobile role").replace("\n", "\r\n")
    agent(project, "pact-mobile.md", text)
    assert verdict(project, plugin, "pact-mobile").kind == "project"


@pytest.mark.parametrize("name_line, why", [
    ("name: !!str pact-secretary", "a YAML tag"),])
def test_a_name_yaml_could_build_claims_every_shipped_name(project, plugin, name_line, why):
    agent(project, "helper.md", f'---\n{name_line}\n---\nInvoke Skill("repacted:teammate").\n')
    assert verdict(project, plugin, "pact-architect").kind == "shadowed", why


@pytest.mark.parametrize("name_line, why", [
    ("name: >\n  pact-secretary", "a block scalar"),])
def test_an_uncertain_name_claims_only_the_shipped_names_its_frontmatter_spells(
        project, plugin, name_line, why):
    agent(project, "helper.md", f'---\n{name_line}\n---\nInvoke Skill("repacted:teammate").\n')
    assert verdict(project, plugin, "pact-secretary").kind == "shadowed", why
    assert verdict(project, plugin, "pact-architect").kind == "shipped", why


@pytest.mark.parametrize("frontmatter, why", [
    ("name: helper\ndescription: >-\n  Reviews code.\n\tTab-indented line.", "a tab continuation"),])
def test_a_plain_agent_with_unusual_syntax_does_not_block_shipped_spawns(project, plugin,
                                                                         frontmatter, why):
    agent(project, "helper.md", f"---\n{frontmatter}\n---\nMentions pact-architect here.\n")
    assert verdict(project, plugin, "pact-architect").kind == "shipped", why


def test_a_yaml_escape_that_spells_the_secretary_is_still_refused(project, plugin):
    agent(project, "helper.md", '---\ndescription: |\n\tx\nname: "pact-\\x73ecretary"\n---\n')
    assert verdict(project, plugin, "pact-secretary").kind == "shadowed"


def test_an_uncertain_name_is_never_a_specialist(project, plugin):
    agent(project, "pact-mobile.md", BOUND.format(name="pact-mobile\n  extra"))
    assert verdict(project, plugin, "pact-mobile").kind == "invalid"


@pytest.mark.parametrize("claimed, why", [
    ("pact-secretary", "the exact shipped name, under another file name"),])
def test_a_project_file_claiming_a_shipped_name_shadows_it(project, plugin, claimed, why):
    agent(project, "helper.md", BOUND.format(name=claimed))
    shipped = "pact-orchestrator" if "orchestrator" in claimed else "pact-secretary"
    assert verdict(project, plugin, shipped).kind == "shadowed", why


def test_a_homoglyph_project_name_is_never_a_specialist(project, plugin):
    lookalike = "pact-s\u0435cretary"   # Cyrillic e: not folded to the shipped name by NFKC
    agent(project, f"{lookalike}.md", BOUND.format(name=lookalike))
    assert verdict(project, plugin, lookalike).kind == "invalid"


_HOST_BODY = '\nYou are a helper.\n\nSkill("repacted:teammate")\n'


# --- team policy agents.project_scope ---------------------------------------------------------

def _policy(project: Path, document) -> None:
    (project / ".repacted").mkdir(exist_ok=True)
    (project / ".repacted" / "policy.json").write_text(json.dumps(document), encoding="utf-8")


# --- dispatch_gate end to end ------------------------------------------------------------------

def _session(monkeypatch, tmp_path, plugin: Path, project: Path):
    import shared.dispatch_helpers as dh
    import shared.pact_context as ctx

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    context = tmp_path / "pact-session-context.json"
    context.write_text(json.dumps({"team_name": _TEAM, "session_id": "s", "project_dir":
                                   str(project), "plugin_root": str(plugin)}), encoding="utf-8")
    monkeypatch.setattr(ctx, "_context_path", context)
    monkeypatch.setattr(ctx, "_cache", None)
    monkeypatch.setattr(ctx, "init", lambda input_data: None)
    dh._specialist_registry.cache_clear()
    team = tmp_path / ".claude" / "teams" / _TEAM
    team.mkdir(parents=True)
    (team / "config.json").write_text(json.dumps({"members": []}), encoding="utf-8")
    tasks = tmp_path / ".claude" / "tasks" / _TEAM
    tasks.mkdir(parents=True)
    (tasks / "1.json").write_text(json.dumps({"id": "1", "owner": _NAME, "status": "pending",
                                              "metadata": {"dispatch": {"name": _NAME}}}),
                                  encoding="utf-8")


def _dispatch(subagent_type: str):
    from dispatch_gate import evaluate_dispatch

    return evaluate_dispatch({"subagent_type": subagent_type, "name": _NAME,
                              "prompt": "Check TaskList for tasks assigned to you."})


