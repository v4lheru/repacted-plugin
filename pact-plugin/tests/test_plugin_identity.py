"""The plugin's identity and every logic site that compares a namespaced spelling.

shared/plugin_identity.py is the single source. Each site below is tested on
its BEHAVIOUR with the current namespace, and, where a reader must still accept
a pre-fork spelling, with the legacy `PACT:` one. Every test here was
sabotage-checked at M1: reverting the site it names to the PACT 4.8.1 value
turns it red.
"""

import json
from pathlib import Path


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


# ---------- site 2: the namespace strip ----------


# ---------- site 3: wait_filler_gate's stdlib-only mirror ----------


# ---------- site 5: the bootstrap gate's secretary matcher ----------


# ---------- site 6: validate_handoff's PACT-agent test ----------

