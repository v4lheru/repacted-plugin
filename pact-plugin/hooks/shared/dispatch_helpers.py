#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/shared/dispatch_helpers.py
Summary: Shared helpers for dispatch_gate.py.

Exposes:
  - is_registered_pact_specialist(subagent_type) — registry check (one
    of the dispatch_gate rules; rejects unregistered pact-* spawns): the
    plugin's agents/pact-*.md plus the project's valid specialists
    (shared.project_agents) unless team policy agents.project_scope is off
  - shipped_specialists(plugin_root) / project_scan() / project_scope_allowed()
    — the parts the dispatch gate composes its project-agent refusals from
  - has_task_assigned(team_name, name) — task-dispatched check (dispatch_gate
    rule 8; rejects a spawn with no `repact dispatch` task for the name)
  - trustworthy_actor_name(input_data, team_name) — actor resolution for
    shared/actor.py and shared/frame.py
  - SOLO_EXEMPT — research/exploration agents that bypass dispatch_gate

Module-load discipline (architect §5.6):
  The fail-closed emitter (hooks/_fail_closed.py, stdlib only) is bound to
  _emit_load_failure_deny BEFORE the wrapped try/except BaseException block. The wrapped block catches
  cross-package import failures (functools/re/pathlib/shared.pact_context)
  and emits a structured DENY with hookEventName=PreToolUse so any
  importer (dispatch_gate, shared/actor) inherits the fail-closed
  contract automatically: if THIS module fails to load, the importer's
  own wrapped import block fires its own _emit_load_failure_deny via the
  re-raised BaseException.
"""

from __future__ import annotations

import re
from functools import partial


# The one fail-closed emitter (hooks/_fail_closed.py, stdlib only); bound to this check's name.
from _fail_closed import emit_load_failure_deny

_emit_load_failure_deny = partial(emit_load_failure_deny, "dispatch_helpers")


# ─── fail-closed wrapper around cross-package imports ─────────────────────
try:
    import functools
    from pathlib import Path
    import os
    import shared.pact_context as pact_context
    from shared import project_agents, team_policy
    from shared.task_utils import iter_team_task_jsons
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


# ─── constants ─────────────────────────────────────────────────────────────

# Research/exploration agents that legitimately spawn WITHOUT name/team_name
# (by design). These bypass the
# dispatch_gate entirely. The specialist registry stays simple — these are caught at
# step ① of evaluate_dispatch in dispatch_gate.py.
SOLO_EXEMPT = frozenset({"general-purpose", "Explore", "Plan"})

# ─── specialist registry ───────────────────────────────────────────────────

def _glob_specialists(plugin_root: str) -> frozenset[str]:
    """Glob the specialist stems at ``{plugin_root}/agents/pact-*.md``.

    Uncached, pure-of-cache: the caller supplies plugin_root explicitly. Empty
    plugin_root or missing agents/ directory → empty frozenset (registry
    fail-closed); OSError → empty frozenset. ``pact-orchestrator.md`` IS in the
    glob set; the "orchestrator is the persona, not a dispatchable specialist"
    semantic is enforced at a different layer (system-prompt --agent flag at
    session start), not at the registry.

    Extracted from ``_specialist_registry`` (#878) so the cached registry and
    an explicit-root caller (the session_init startup notice, which runs BEFORE
    the pact_context cache is populated and so must pass an env-resolved root)
    share ONE glob implementation without parameterizing the lru_cache.
    """
    if not plugin_root:
        return frozenset()
    agents_dir = Path(plugin_root) / "agents"
    try:
        return frozenset(p.stem for p in agents_dir.glob("pact-*.md"))
    except OSError:
        return frozenset()


def shipped_specialists(plugin_root: str) -> frozenset[str]:
    """The plugin's own specialist names (the agents/pact-*.md glob)."""
    return _glob_specialists(plugin_root)


def project_dir() -> str:
    """The project the session runs in: the context's project_dir, else CLAUDE_PROJECT_DIR."""
    return pact_context.get_project_dir() or os.environ.get("CLAUDE_PROJECT_DIR", "")


def project_scope_allowed(directory: str = "") -> bool:
    """False when the team policy of the project's work tree sets agents.project_scope off."""
    return team_policy.project_scope(directory or None) == "allow"


def project_scan(plugin_root: str, directory: str = "") -> "project_agents.Scan":
    """Every agent file the host reads for the project (shared.project_agents.scan)."""
    return project_agents.scan(directory or project_dir(), plugin_root)


def _registry_for(plugin_root: str, directory: str) -> frozenset[str]:
    """Shipped names plus the project's valid specialists (when policy allows). A shipped
    name stays registered even when a project file shadows it: dispatch_gate refuses that
    bare spawn, and every other consumer only adds checks for a registered type."""
    shipped = _glob_specialists(plugin_root)
    if not shipped or not directory or not project_scope_allowed(directory):
        return shipped
    found = project_agents.scan(directory, plugin_root)
    return shipped | project_agents.project_specialists(shipped, found)


@functools.lru_cache(maxsize=1)
def _specialist_registry() -> frozenset[str]:
    """The registry once per hook subprocess (cached, arg-less).

    Hook subprocesses are short-lived (per-tool-call); the cache is rebuilt
    on every dispatch evaluation. Resolves plugin_root and the project dir from
    the pact_context cache. Deliberately ARG-LESS so the lru_cache holds exactly
    one entry per subprocess (the hot dispatch path); the explicit-root path the
    startup notice needs pre-cache goes through ``_registry_for`` directly,
    never through this cache. Empty plugin_root / missing agents/ → empty
    registry (fail-closed), project agents included: a project specialist never
    registers without the plugin's own.
    """
    return _registry_for(pact_context.get_plugin_root(), project_dir())


def is_registered_pact_specialist(
    subagent_type: str, plugin_root: str = ""
) -> bool:
    """True iff subagent_type is a shipped specialist (``agents/pact-*.md``) or a
    valid project specialist (``shared.project_agents``, policy permitting).

    ``plugin_root`` (#878, additive + backward-compatible): when a non-empty
    plugin_root is passed, resolve the registry against it directly via
    ``_glob_specialists`` (the uncached path) — required by the session_init
    startup notice, which runs BEFORE the pact_context cache is populated, so
    the cached ``_specialist_registry()`` would see an empty plugin_root and
    wrongly report every type as unregistered there. When omitted/empty (every
    existing caller, e.g. the dispatch self-completion gate), fall back to the
    cached ``_specialist_registry()`` — identical behavior to before this param
    existed.
    """
    if not isinstance(subagent_type, str) or not subagent_type:
        return False
    registry = (
        _registry_for(plugin_root, os.environ.get("CLAUDE_PROJECT_DIR", ""))
        if plugin_root else _specialist_registry()
    )
    return subagent_type in registry


# ─── dispatch-wiring recognition ───────────────────────────────────────────

# ─── task-assigned check ───────────────────────────────────────────────────

def has_task_assigned(team_name: str, name: str) -> bool:
    """True iff at least one task in ``team_name``'s task store was dispatched to ``name``
    by ``repact dispatch`` (``metadata.dispatch.name == name``) and is still open
    (``status in {"pending", "in_progress"}``). The owner field alone does not count: a
    hand-made TaskCreate is not a dispatch.

    Delegates path construction and per-file reading to
    ``shared.task_utils.iter_team_task_jsons``, the single source of truth for per-team
    task iteration (canonical layout, path-traversal and symlink-escape defenses).
    """
    if not isinstance(team_name, str) or not team_name:
        return False
    if not isinstance(name, str) or not name:
        return False
    for data in iter_team_task_jsons(team_name):
        metadata = data.get("metadata")
        dispatch = metadata.get("dispatch") if isinstance(metadata, dict) else None
        if not isinstance(dispatch, dict) or dispatch.get("name") != name:
            continue
        if data.get("status") in ("pending", "in_progress"):
            return True
    return False


# ─── actor resolution (for the self-completion rule) ──────────────────────

def trustworthy_actor_name(input_data: dict, team_name: str | None = None) -> str | None:
    """Extract actor name from harness-trustworthy ``agent_id`` ONLY.

    Bypasses ``resolve_agent_name``'s Step 1 (agent_name) and Step 4
    (agent_type) fallbacks because they are not strong enough trust
    signals for the self-completion check (architect §5.3 / PREPARE
    §10).

    Trust contract: ``agent_id`` is harness-set and lives at the top
    level of the hook stdin JSON, not inside ``tool_input`` — a teammate
    cannot collide with it via crafted tool arguments. Two shapes name an
    actor:
      - ``"name@team_name"`` returns ``name``.
      - An in-process teammate's ``"a" + agent_type + "-" + 16 hex`` returns
        ``agent_type`` when ``team_name`` is given and ``agent_type`` names a
        member of it (``background_work.agent_type_names_a_member``, which
        refuses the bare ``"a" + 16 hex`` Agent-tool subagent id). The id is
        built from ``agent_type`` and compared whole, so a frame whose type and
        id disagree names no one.

    Returns None for anything else. Callers (shared/actor.py, shared/frame.py)
    treat None as "actor unresolvable" and grant it no authority.

    No exceptions. Reads the team config only for the in-process shape.
    """
    if not isinstance(input_data, dict):
        return None
    agent_id = input_data.get("agent_id")
    if not isinstance(agent_id, str):
        return None
    if "@" in agent_id:
        name, _, team = agent_id.partition("@")
        if not name or not team:
            return None
        return name
    agent_type = input_data.get("agent_type")
    if not isinstance(agent_type, str) or not agent_type or not team_name:
        return None
    if not re.fullmatch(re.escape(f"a{agent_type}-") + "[0-9a-f]{16}", agent_id):
        return None
    try:
        from .background_work import agent_type_names_a_member

        if agent_type_names_a_member(agent_type, team_name, agent_id=input_data.get("agent_id")):
            return agent_type
    except Exception:
        pass
    return None
