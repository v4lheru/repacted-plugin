#!/usr/bin/env python3
"""PreToolUse hook (matcher Agent|Task|Read|Bash): denies agent dispatch until the session's
bootstrap-complete marker exists and verifies.

Valid marker, non-PACT session or teammate -> suppressOutput. Agent/Task before bootstrap ->
deny (except the canonical secretary spawn). Every other tool -> allow (1.4.1). Read and Bash
also settle staged compaction summaries. Module-load and runtime failures fail CLOSED, except
verified read-only tools, which get a degraded-mode warning with "defer" (or "ask" for
outbound tools), never "allow".

Map: _emit_load_failure_deny, _read_stdin_tool_name, _bounded_error_text,
_emit_degraded_warning, _degraded_decision, _secretary_in_members,
_is_canonical_secretary_spawn, is_marker_set, _team_on_disk, _restamp_stale_marker,
_secretary_spawn_advisory, _check_tool_allowed, _settle_before_summary_read, main.
Rationale: the design notes
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_deny BEFORE wrapped imports) ───
import json
import os
import sys
from functools import partial
from typing import NoReturn

# The one fail-closed emitter (hooks/_fail_closed.py, stdlib only); bound to this check's name.
from _fail_closed import _ERROR_TEXT_MAX, bounded_error_text as _bounded_error_text  # noqa: F401  # re-export: tests pin the twin renderer and its cap here
from _fail_closed import emit_load_failure_deny

_emit_load_failure_deny = partial(emit_load_failure_deny, "bootstrap_gate")


# Tools that degraded mode routes onward with a warning ("defer", or "ask" for
# _DEGRADED_ASK_TOOLS), never "allow"; everything else denies. Pinned: disjoint from
# _BLOCKED_TOOLS and allowed on every healthy branch. Bash and mcp__* are deliberately absent
# (a degraded gate cannot tell diagnostic from mutating calls).
_READ_ONLY_TOOLS = frozenset({
    "Read", "Glob", "Grep",          # pure file read/search
    "ToolSearch", "Skill",           # context loading only (incident-proven denied)
    "TaskList", "TaskGet",           # read-only task views (TaskCreate/TaskUpdate excluded)
    "WebFetch", "WebSearch",         # outbound read (healthy path already allows)
    "AskUserQuestion", "ExitPlanMode",  # user-interaction channel for diagnosis
})


def _read_stdin_tool_name() -> "str | None":
    """Stdlib-only tool_name parse for the degraded path; None on any failure (caller denies)."""
    try:
        data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
        name = data.get("tool_name")
        return name if isinstance(name, str) and name else None
    except Exception:
        return None


# Outbound members of _READ_ONLY_TOOLS: "ask" under a degraded gate. Pinned: a subset.
_DEGRADED_ASK_TOOLS = frozenset({"WebFetch", "WebSearch"})

# Cap on every stdin read here: bounds memory only (an over-cap frame fails to parse).
# MUST equal task_lifecycle_gate._STDIN_READ_MAX.
_STDIN_READ_MAX = 8 * 1024 * 1024  # 8 MB


def _emit_degraded_warning(stage: str, error: BaseException, tool_name: str) -> NoReturn:
    """Warn without granting: "defer" (normal permission flow) for local read-only tools, "ask" for
    outbound ones; never "allow". MUST exit 0 (stdout JSON is ignored otherwise).
    """
    decision = "ask" if tool_name in _DEGRADED_ASK_TOOLS else "defer"
    routed = (
        "escalated to your explicit approval"
        if decision == "ask"
        else "deferred to the normal permission flow"
    )
    # Read CLAUDE_PLUGIN_ROOT from os.environ, not shared.paths: this runs because an import
    # failed, and a raising resolver here would void the defer/ask decision.
    config_root = os.environ.get("CLAUDE_CONFIG_DIR") or "$HOME/.claude"
    plugin_location = os.environ.get("CLAUDE_PLUGIN_ROOT") or f"{config_root}/plugins/cache/"
    warning = (
        f"PACT bootstrap_gate is DEGRADED ({stage} failure — "
        f"{_bounded_error_text(error)}). Read-only tool '{tool_name}' is "
        f"{routed} so the failure can be diagnosed; nothing is "
        "auto-allowed while the gate is degraded. Agent dispatch and Bash "
        "remain blocked "
        "fail-closed. Surface this to the user: PACT hooks are failing — "
        "check Python version compatibility and the plugin at "
        f"{plugin_location}. Bootstrap cannot complete until the "
        "hook loads cleanly."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": warning,
            "additionalContext": warning,
        },
        "systemMessage": (
            f"PACT bootstrap_gate degraded ({stage} failure): read-only "
            "tools routed to the normal permission flow with a warning; "
            "mutating tools blocked."
        ),
    }))
    # Guarded full-text rendering: this line runs AFTER the decision JSON
    # printed, but a raise here would exit nonzero — and stdout JSON is only
    # honored on exit 0, voiding the defer/ask decision retroactively.
    try:
        error_full = f"{error}"
    except BaseException:  # noqa: BLE001 — hostile __str__; keep the exit-0 path
        error_full = "<exception str() raised>"
    try:
        print(
            f"Hook degraded-{decision} (bootstrap_gate / {stage}): {tool_name} — {error_full}",
            file=sys.stderr,
        )
    except BaseException:  # noqa: BLE001 — a diagnostic-write raise must not flip the exit code
        pass
    sys.exit(0)


def _degraded_decision(stage: str, error: BaseException, tool_name: "str | None") -> NoReturn:
    """Single decision point for BOTH degraded stages (import + runtime).
    Membership ⇒ warn-without-granting (defer / ask, never allow);
    everything else (incl. tool_name=None from unparseable stdin —
    fail-CLOSED) ⇒ the unchanged deny emitter."""
    if tool_name is not None and tool_name in _READ_ONLY_TOOLS:
        _emit_degraded_warning(stage, error, tool_name)
    _emit_load_failure_deny(stage, error)


# ─── fail-closed wrapper around cross-package imports + risky module work ─────
try:
    import stat
    from pathlib import Path

    import shared.pact_context as pact_context
    from shared.constants import BOOTSTRAP_MARKER_NAME
    from shared.marker_schema import (
        MARKER_MAX_BYTES,
        MARKER_SCHEMA_VERSION,
        expected_marker_signature,
        is_marker_set,
    )
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    # Degraded mode (#942): read-only tools defer/ask with a warning; everything else denies.
    _degraded_decision("module imports", _module_load_error, _read_stdin_tool_name())


_SUPPRESS_OUTPUT = json.dumps({
    "suppressOutput": True,
    "hookSpecificOutput": {"hookEventName": "PreToolUse"},
})

# Agent dispatch is blocked until bootstrap. Bash stays allowed (the ritual needs it); a
# touched or echoed marker is defeated by is_marker_set's content check.
_BLOCKED_TOOLS = frozenset({
    "Agent",
    "Task",
})

# Canonical secretary spawn identity; drift re-introduces the bootstrap deadlock. Mirrors
# bootstrap_marker_writer._SECRETARY_NAME and commands/bootstrap.md Step 2.
_SECRETARY_NAME = "secretary"
_SECRETARY_AGENT_TYPE = "pact-secretary"

# Marker schema, signature and the verifier is_marker_set live in shared/marker_schema.py
# (shared with the writer and with session_init_resume, which never imports this gate).

# The trailing clause is a conditioned pointer, not a diagnosis, and an absolute URL on
# purpose (the shipped README lacks that section). Mirror any change into
# _CANONICAL_DENY_REASON_LITERAL in tests/test_bootstrap_gate.py.
_DENY_REASON = (
    "PACT bootstrap required. Invoke Skill(\"repacted:bootstrap\") first. "
    "Agent dispatch (Agent) is blocked until bootstrap completes. "
    "Every other tool is available. "
    "If bootstrap cannot complete because the task-management tools are "
    "unavailable, see "
    "https://github.com/v4lheru/repacted-plugin#if-specialist-agents-will-not-spawn"
)


def _secretary_in_members(team_name: str) -> bool:
    """Has the secretary JOINED members[]? (a join witness; never the inbox, see docs, #1023).

    Never raises: a broad except covers the Path.home() RuntimeError that escapes _iter_members;
    False lets the carve-out fire, the safe direction.
    """
    try:
        return any(
            member.get("name") == _SECRETARY_NAME
            for member in pact_context._iter_members(team_name)
        )
    except Exception:  # noqa: BLE001 — broad by design: Path.home() RuntimeError seam (see docstring)
        return False


def _is_canonical_secretary_spawn(input_data: dict) -> bool:
    """True iff this is the canonical bootstrap-secretary spawn (#789).

    Bindings: (1) tool_name == "Agent"; (2) subagent_type is pact-secretary, bare or with one
    plugin namespace; (3) name == "secretary"; (5) the secretary has not joined members[] of the
    SSOT team. Binding 4 (team_name arg) was dropped for #979. One-shot comes from the marker
    fast path in _check_tool_allowed, not from binding 5 (Desktop has no members[]).
    Any disk-read exception returns False (the canonical deny follows). See docs.
    """
    try:
        if input_data.get("tool_name") not in _BLOCKED_TOOLS:
            return False
        tool_input = input_data.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return False
        # One leading plugin namespace is stripped, case-sensitively.
        subagent_type = tool_input.get("subagent_type")
        if not isinstance(subagent_type, str):
            return False
        if pact_context.strip_pact_namespace(subagent_type) != _SECRETARY_AGENT_TYPE:
            return False
        if tool_input.get("name") != _SECRETARY_NAME:
            return False
        # Binding 4 dropped (#979): no comparison against the platform-ignored
        # tool_input.team_name. The empty-team fail-closed is RETAINED because
        # binding 5 below reads teams/{expected_team}/config.json.
        expected_team = pact_context.get_team_name()
        if not expected_team:
            return False
        # Binding 5: members[]-only join witness (#1023), not the marker writer's dispatch witness.
        return not _secretary_in_members(expected_team)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        return False


_SIG_HEX_DIGITS = frozenset("0123456789abcdef")


def _team_on_disk(team_name: str) -> bool:
    """Gate-local mirror of bootstrap_marker_writer._team_is_identified (the
    gate imports nothing from the writer, see TestImportDiscipline): the team
    config lists a member with a non-empty name, or, config-less, the team's
    inboxes/ dir exists. An empty team name is never on disk."""
    if not team_name:
        return False
    for member in pact_context._iter_members(team_name):
        name = member.get("name")
        if isinstance(name, str) and name.strip():
            return True
    teams_dir = pact_context.get_claude_config_dir() / "teams"
    return (teams_dir / team_name / "inboxes").is_dir()


def _restamp_stale_marker(session_dir: Path) -> bool:
    """Re-stamp a marker this session holds from an earlier plugin version.

    An in-place plugin upgrade changes the version, so the stamp stops verifying and an
    unattended lead stays denied. True only if the marker passes every check but the signature,
    the writer's preconditions hold now, and the new marker verifies. Known limit: the old
    signature cannot be checked. Journals and logs every re-stamp; never raises.
    """
    try:
        if session_dir.resolve(strict=False) != session_dir.absolute():
            return False
        marker_path = session_dir / BOOTSTRAP_MARKER_NAME
        st = os.lstat(str(marker_path))
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= MARKER_MAX_BYTES:
            return False
        parsed = json.loads(marker_path.read_text(encoding="utf-8").strip())
        if not isinstance(parsed, dict) or set(parsed.keys()) != {"v", "sid", "sig"}:
            return False
        if type(parsed["v"]) is not int or parsed["v"] != MARKER_SCHEMA_VERSION:
            return False
        if not isinstance(parsed["sid"], str) or parsed["sid"] != session_dir.name:
            return False
        sig = parsed["sig"]
        if not isinstance(sig, str) or len(sig) != 64 or not set(sig) <= _SIG_HEX_DIGITS:
            return False

        if not _team_on_disk(pact_context.get_team_name()):
            return False
        plugin_root = pact_context.get_plugin_root()
        if not plugin_root:
            return False
        plugin_version = json.loads(
            (Path(plugin_root) / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        ).get("version", "")
        if not isinstance(plugin_version, str) or not plugin_version:
            return False

        body = json.dumps({
            "v": MARKER_SCHEMA_VERSION,
            "sid": session_dir.name,
            "sig": expected_marker_signature(
                session_dir.name, plugin_root, plugin_version, MARKER_SCHEMA_VERSION
            ),
        }).encode("utf-8")
        import tempfile

        fd, tmp_path = tempfile.mkstemp(
            dir=str(session_dir), prefix=".bootstrap-complete-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, str(marker_path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        if not is_marker_set(session_dir):
            return False
    except Exception:  # noqa: BLE001 — any failure keeps the deny
        return False
    try:
        print(
            "PACT bootstrap_gate: re-stamped the bootstrap marker of session "
            f"{session_dir.name} for plugin version {plugin_version}",
            file=sys.stderr,
        )
        from shared.session_journal import append_event, make_event
        append_event(
            make_event(
                "bootstrap_marker_restamp",
                session_id=session_dir.name,
                plugin_version=plugin_version,
            ),
            str(session_dir),
        )
    except Exception:  # noqa: BLE001 — the audit record must not undo the decision
        pass
    return True


# On-demand secretary (m7): TaskUpdate(owner="secretary") with no secretary member sets a
# pending flag (not denied: rule 8 needs the owner before the spawn); the lead's next
# non-task-list call gets the spawn advisory once.
_SECRETARY_PENDING_FILENAME = "secretary-spawn-pending"
_TASK_LIST_TOOLS = frozenset({"TaskCreate", "TaskUpdate", "TaskGet", "TaskList"})
_SECRETARY_SPAWN_ADVISORY = (
    "PACT: the secretary is not on the team yet. Spawn it now, before any "
    "SendMessage to it (a send to a member that does not exist is not "
    "delivered): Agent(name=\"secretary\", subagent_type=\"pact-secretary\", "
    "description=\"Spawn session secretary\", prompt=\"YOUR PACT ROLE: teammate "
    "(secretary).\\n\\nYou are joining team {team}. As your FIRST action, "
    "Invoke Skill(\\\"repacted:teammate\\\") to record your "
    "identity. Then check `TaskList` for tasks assigned to you.\"). See "
    "/repacted:bootstrap, On-demand secretary."
)


def _secretary_spawn_advisory(input_data: dict) -> str | None:
    """Spawn-now advisory for a lead that assigned the secretary and did something else; else None.
    Never raises.
    """
    try:
        if not pact_context.is_lead(input_data):
            return None
        session_dir = pact_context.get_session_dir()
        if not session_dir:
            return None
        pending = Path(session_dir) / _SECRETARY_PENDING_FILENAME
        tool_name = input_data.get("tool_name")
        tool_input = input_data.get("tool_input")
        if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
            return None
        if tool_name == "TaskUpdate" and tool_input.get("owner") == _SECRETARY_NAME:
            team = pact_context.get_team_name()
            if team and not _secretary_in_members(team):
                pending.touch(mode=0o600)
            return None
        if tool_name in _TASK_LIST_TOOLS or not pending.exists():
            return None
        pending.unlink(missing_ok=True)
        if tool_name in _BLOCKED_TOOLS and tool_input.get("name") == _SECRETARY_NAME:
            return None
        team = pact_context.get_team_name()
        if not team or _secretary_in_members(team):
            return None
        return _SECRETARY_SPAWN_ADVISORY.replace("{team}", team)
    except Exception:  # noqa: BLE001 — advisory only; never affects the decision
        return None


def _check_tool_allowed(input_data: dict) -> str | None:
    """Return the deny reason, or None to allow."""
    pact_context.init(input_data)

    # Fast path: marker exists (as a properly-stamped regular file with
    # valid content fingerprint) → allow everything. See `is_marker_set`
    # for symlink and bypass defense rationale.
    session_dir = pact_context.get_session_dir()
    if not session_dir:
        return None

    if is_marker_set(Path(session_dir)):
        return None

    # Lead-role gate (#878): only the lead's pre-bootstrap calls are gated; is_lead keys on the
    # harness agent_type and is total.
    if not pact_context.is_lead(input_data):
        return None

    # Lead session, no marker — check tool classification
    tool_name = input_data.get("tool_name", "")

    # Canonical secretary carve-out (#789): without it the gate denies the only spawn that can
    # satisfy the marker writer (deadlock).
    if _is_canonical_secretary_spawn(input_data):
        return None

    # MCP tools always allowed (external integrations)
    if isinstance(tool_name, str) and tool_name.startswith("mcp__"):
        return None

    # Blocked implementation tools
    # frozenset membership is type-safe — no isinstance guard needed
    if tool_name in _BLOCKED_TOOLS:
        # A marker this session stamped under an earlier plugin version:
        # re-check the writer's pre-conditions and re-stamp instead of denying,
        # so an unattended lead keeps working across an in-place advance.
        if _restamp_stale_marker(Path(session_dir)):
            return None
        return _DENY_REASON

    # Every other tool, editing tools included, is allowed before bootstrap
    return None


# The tools a read of the compaction summary arrives as: the lead's recovery
# Read, and the secretary's Read or Bash.
_SUMMARY_READ_TOOLS = frozenset({"Read", "Bash"})


def _settle_before_summary_read(input_data: dict) -> None:
    """Settle staged compaction summaries before a Read or Bash that names one.

    Other calls pay one string test; the import is inside the try, so a broken module never
    reaches the decision. Never raises; never changes the decision.
    """
    try:
        if input_data.get("tool_name") not in _SUMMARY_READ_TOOLS:
            return
        if "compact-summary" not in json.dumps(input_data.get("tool_input")):
            return
        from shared import compaction_owner

        session_dir = pact_context.get_session_dir()
        if session_dir:
            compaction_owner.settle(session_dir, wait_s=compaction_owner.READ_WAIT_S)
    except Exception:
        pass


def main():
    try:
        input_data = json.loads(sys.stdin.read(_STDIN_READ_MAX))
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → fail-OPEN (input-side failure is harness's domain).
        # Cannot evaluate without input; cannot DENY meaningfully.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    try:
        deny_reason = _check_tool_allowed(input_data)
    except Exception as e:
        # Runtime fail-CLOSED (#658) with the #942 degraded carve-out; reuse the parsed tool_name
        # (stdin is consumed) and guard a non-dict frame.
        _tool = input_data.get("tool_name") if isinstance(input_data, dict) else None
        _degraded_decision(
            "runtime", e, _tool if isinstance(_tool, str) and _tool else None
        )

    _settle_before_summary_read(input_data)

    if deny_reason:
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
        print(json.dumps(output))
        sys.exit(2)

    advisory = _secretary_spawn_advisory(input_data)
    if advisory:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": advisory,  # advisory, NOT a permissionDecision
            }
        }))
        sys.exit(0)

    print(_SUPPRESS_OUTPUT)
    sys.exit(0)


if __name__ == "__main__":
    main()
