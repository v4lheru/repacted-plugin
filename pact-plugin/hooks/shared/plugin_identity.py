"""
Location: pact-plugin/hooks/shared/plugin_identity.py
Summary: The plugin's name and the namespace the platform prefixes to its
    agents, skills and commands. The single source for every logic site that
    compares a namespaced spelling.
Used by: shared/pact_context.py (LEAD_AGENT_TYPES, strip_pact_namespace), and,
    as the reference for its drift test, the stdlib-only hook that holds a
    local copy (wait_filler_gate._LEAD_AGENT_TYPES).

The platform spells a plugin's agent or skill `<plugin name>:<name>`, so
NAMESPACE follows plugin.json's `name`; tests/test_plugin_identity.py pins the
two equal. LEGACY_NAMESPACES are spellings from before the fork: a session
begun under PACT can still carry them (a resumed transcript, an older team
config), so readers that strip a namespace strip those too.

Stdlib only, and imports nothing but __future__, so any hook may import it cheaply.
"""

from __future__ import annotations

PLUGIN_NAME = "repacted"
NAMESPACE = PLUGIN_NAME + ":"
LEGACY_NAMESPACES = ("PACT:",)

# Current first: a value is stripped of at most one of these, the first match.
STRIPPABLE_NAMESPACES = (NAMESPACE,) + LEGACY_NAMESPACES
