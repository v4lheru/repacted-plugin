"""The plugin's identity and every logic site that compares a namespaced spelling.

shared/plugin_identity.py is the single source. Each site below is tested on
its BEHAVIOUR with the current namespace, and, where a reader must still accept
a pre-fork spelling, with the legacy `PACT:` one. Every test here was
sabotage-checked at M1: reverting the site it names to the PACT 4.8.1 value
turns it red.
"""

import json
from pathlib import Path

import pytest

from shared.plugin_identity import (
    LEGACY_NAMESPACES,
    NAMESPACE,
    PLUGIN_NAME,
    STRIPPABLE_NAMESPACES,
)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGIN_ROOT.parent


# ---------- the identity itself ----------

def test_plugin_name_is_the_manifest_name():
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert PLUGIN_NAME == manifest["name"]


def test_the_marketplace_distributes_the_plugin_under_its_name():
    """Core comes first under the plugin name from ./pact-plugin; any further entry is a pack
    (the public tree's n8n pack, U8.6) named <plugin>-<pack> from ./packs/<pack>, and its own
    manifest carries that name."""
    marketplace = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    core, *packs = marketplace["plugins"]
    assert (core["name"], core["source"]) == (PLUGIN_NAME, "./pact-plugin")
    for pack in packs:
        folder = pack["source"].removeprefix("./packs/")
        assert pack["source"] == "./packs/" + folder and folder and "/" not in folder, pack["source"]
        assert pack["name"] == f"{PLUGIN_NAME}-{folder}"
        manifest = REPO_ROOT / "packs" / folder / ".claude-plugin" / "plugin.json"
        assert json.loads(manifest.read_text(encoding="utf-8"))["name"] == pack["name"]


def test_the_namespace_is_the_plugin_name_and_a_colon():
    assert NAMESPACE == PLUGIN_NAME + ":"
    assert STRIPPABLE_NAMESPACES[0] == NAMESPACE
    assert LEGACY_NAMESPACES == ("PACT:",)


# ---------- site 1: the lead's agent_type spellings ----------

def test_a_namespaced_orchestrator_is_the_lead():
    from shared.pact_context import LEAD_AGENT_TYPES, is_lead

    assert LEAD_AGENT_TYPES == frozenset({NAMESPACE + "pact-orchestrator", "pact-orchestrator"})
    assert is_lead({"agent_type": "repacted:pact-orchestrator"}) is True
    assert is_lead({"agent_type": "pact-orchestrator"}) is True
    assert is_lead({"agent_type": "repacted:pact-backend-coder"}) is False


# ---------- site 2: the namespace strip ----------

@pytest.mark.parametrize("value, expected", [
    ("repacted:pact-secretary", "pact-secretary"),
    ("PACT:pact-secretary", "pact-secretary"),
    ("repacted:" "repacted:" "pact-x", "repacted:" "pact-x"),
    ("PACT:repacted:" "pact-x", "repacted:" "pact-x"),
    ("repacted:", ""),
    ("Repacted:pact-x", "Repacted:pact-x"),
    ("pact:pact-x", "pact:pact-x"),
    ("xrepacted:" "pact-x", "xrepacted:" "pact-x"),
    ("pact-secretary", "pact-secretary"),
], ids=["current", "legacy", "current-once", "legacy-once", "bare-current",
        "case-sensitive", "lowercase-legacy-kept", "not-a-prefix", "unqualified"])
def test_strip_removes_one_current_or_legacy_namespace(value, expected):
    from shared.pact_context import strip_pact_namespace

    assert strip_pact_namespace(value) == expected


def test_the_pact_namespace_alias_is_the_current_namespace():
    from shared.pact_context import PACT_NAMESPACE

    assert PACT_NAMESPACE == NAMESPACE


# ---------- site 3: wait_filler_gate's stdlib-only mirror ----------

def test_wait_filler_gate_treats_the_namespaced_orchestrator_as_the_lead():
    import wait_filler_gate

    assert wait_filler_gate.is_teammate_frame({"agent_type": "repacted:pact-orchestrator"}) is False
    assert wait_filler_gate.is_teammate_frame({"agent_type": "repacted:pact-backend-coder"}) is True


# ---------- site 5: the bootstrap gate's secretary matcher ----------

@pytest.mark.parametrize("subagent_type", ["repacted:pact-secretary", "PACT:pact-secretary", "pact-secretary"])
def test_the_bootstrap_secretary_carve_out_accepts_every_namespace(monkeypatch, tmp_path, subagent_type):
    from tests.test_bootstrap_gate import _canonical_secretary_input, _setup_pact_session_with_team
    from bootstrap_gate import _check_tool_allowed

    _setup_pact_session_with_team(monkeypatch, tmp_path, team_name="t1", members=[])
    result = _check_tool_allowed(_canonical_secretary_input(
        team_name="t1", overrides={"subagent_type": subagent_type},
    ))
    assert result is None


# ---------- site 6: validate_handoff's PACT-agent test ----------

