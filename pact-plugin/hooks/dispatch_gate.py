#!/usr/bin/env python3
"""PreToolUse hook (matcher='Agent'): validates PACT specialist spawns.

Rules, cheapest first, short-circuit on the first non-ALLOW: (1) SOLO_EXEMPT carve-out,
(2) non-pact-* carve-out, (3) name present, (4) name length/NFKC/regex/reserved,
(5) plugin agents/ + specialist registry (shipped agents, then the project's own:
shared/project_agents.py), (6) session team from the SSOT (never the
platform-ignored team_name arg), (7) member-name uniqueness, (8) a task dispatched to the name
(metadata.dispatch.name, written by `repact dispatch`),
(8b) team-config-absent advisory. Rule (6)'s teammate refusal, (7) and (8b) are Agent Teams
rules: in subagents mode (shared/execution.py) there is no team on disk and no teammate, so
they are skipped; (6) still needs the session team, which (8) uses as a path segment.
(9) inline-mission heuristic (PACT_DISPATCH_INLINE_MISSION_MODE warn|deny|shadow; the brief
prompt dispatch prints is exempt), then
(10) the session cost cap on a spawn (1)-(9) let through, of any type but COST_CAP_EXEMPT
(shared/cost_cap.py; a spend that cannot be read lets the spawn through with a
once-per-session note, never a silent pass).
Fail-closed on load failure, on a runtime exception and on a timeout (the registry runs it
with on_timeout="deny", so a hang past its alarm refuses the spawn); only the cost cap's own
read is fail-open, loudly, as (10) says. DENY -> exit 2; ALLOW -> exit 0;
WARN -> additionalContext. Every decision is journaled with the prompt redacted.

Map: _emit_load_failure_deny, _redact, _team_member_names, _resolves_a_registered_team,
_lead_identity_refusal, evaluate_dispatch, _apply_cost_cap, _journal_decision,
_augment_deny_with_stale_diagnosis, _compose_deny_diagnosis, main.
Rationale: the design notes
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_deny BEFORE wrapped imports) ─
import json
import re
import sys
from functools import partial

# The one fail-closed emitter (hooks/_fail_closed.py, stdlib only); bound to this check's name.
from _fail_closed import emit_load_failure_deny

_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})

_emit_load_failure_deny = partial(emit_load_failure_deny, "dispatch_gate")


# ─── fail-closed wrapper on cross-package imports ──────────────────────────
try:
    import re
    import unicodedata
    from pathlib import Path

    import shared.pact_context as pact_context
    import shared.pact_config as pact_config
    from shared import project_agents
    from shared.dispatch_helpers import (
        SOLO_EXEMPT,
        has_task_assigned,
        project_dir,
        project_scan,
        project_scope_allowed,
        shipped_specialists,
    )
    from shared.ops.brief import BRIEF_PROMPT
    from shared.session_journal import append_event_checked, make_event
    from shared.paths import get_claude_config_dir
    from shared.stale_session import detect_stale_session_block
    from shared.task_store import HALT_RULE, halt_sentence, read_probe_marker
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("module imports", _module_load_error)


# ─── constants ─────────────────────────────────────────────────────────────

# Name validation: length cap -> NFKC (defeats fullwidth lookalikes) -> regex -> reserved.
# The regex needs one alphanumeric and forbids leading/trailing hyphens.
NAME_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
NAME_MAX_LENGTH = 64

# Reserved: routing literals and schema actor types ("team-lead" would shadow lead routing;
# lead/peer/user/external are KNOWN_RESOLVERS values).
RESERVED_NAMES = frozenset({
    "team-lead",
    "lead",
    "user",
    "external",
    "peer",
    "unknown",
    "solo",
})
# "secretary" is deliberately NOT reserved (the ritual spawns it by that name). The
# self-completion carve-out keys on team-config agentType, not owner name, so name spoofing
# cannot bypass it (shared/intentional_wait.is_self_complete_exempt).

# Inline-mission heuristic. The 800-char threshold is also stated in
# agents/pact-orchestrator.md; change both together.
PROMPT_MAX_LENGTH = 800
# Also listed in agents/pact-orchestrator.md; change both together.
TASK_REFERENCE_PHRASES = (
    "TaskList",
    "task list",
    "tasks assigned",
    "check your tasks",
)

# Inline-mission mode, resolved once by shared.pact_config.get_enum (total; unknown values
# fall back to "warn" so a typo never disables the other rules). Imported inside the
# fail-closed import block, so a resolver-load failure denies.
INLINE_MISSION_MODE = pact_config.get_enum("PACT_DISPATCH_INLINE_MISSION_MODE")

# Credential redaction patterns. Applied to the journal-written prompt
# only; the in-memory ``permissionDecisionReason`` keeps the verbatim
# prompt for the dispatcher's debugging.
REDACTION_PATTERNS = (
    # Anthropic API keys, including the sk-ant-api03-... family. Matched
    # before the generic sk- prefix so the longer, more specific shape
    # is captured cleanly.
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    # OpenAI-style sk- keys.
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"xoxb-[A-Za-z0-9-]{20,}"),
    # GitHub tokens: personal-access (ghp_), OAuth (gho_), user-server
    # (ghu_), server-to-server (ghs_), refresh (ghr_).
    re.compile(r"gh[oprsu]_[A-Za-z0-9]{20,}"),
    # AWS access key id.
    re.compile(r"AKIA[A-Z0-9]{16}"),
    # Google API keys (39-char total: AIza prefix + 35 chars).
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),
    # JWT shape: three base64url segments joined with dots.
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    # PEM private-key blocks of any flavour; DOTALL, non-greedy to the first END line.
    re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
)


# Project-agent refusals (rule 5): hooks/shared/rules.py holds each sentence and way through.
PROJECT_AGENT_SHADOW_RULE = "dispatch_gate.project_agent_shadows_plugin"
PROJECT_AGENT_UNBOUND_RULE = "dispatch_gate.project_agent_unbound"
PROJECT_AGENT_INVALID_RULE = "dispatch_gate.project_agent_invalid"
PROJECT_AGENT_AMBIGUOUS_RULE = "dispatch_gate.project_agent_ambiguous"
PROJECT_AGENTS_UNREADABLE_RULE = "dispatch_gate.project_agents_unreadable"
PROJECT_AGENTS_OFF_RULE = "dispatch_gate.project_agents_disabled"
_SHOWN_FILES = 3   # files a refusal names before "and N more"


# ─── helpers ───────────────────────────────────────────────────────────────

def _redact(prompt: str) -> str:
    """Scrub credential patterns before the journal write (the user-facing reason stays verbatim)."""
    if not isinstance(prompt, str):
        return ""
    redacted = prompt
    for pat in REDACTION_PATTERNS:
        redacted = pat.sub("[REDACTED]", redacted)
    return redacted


def _team_member_names(team_name: str) -> set[str]:
    """Live member names from the team config.json; any error (or no config) returns set(), so
    the uniqueness rule fails open.
    """
    cfg_path = get_claude_config_dir() / "teams" / team_name / "config.json"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    members = data.get("members") if isinstance(data, dict) else None
    if not isinstance(members, list):
        return set()
    names: set[str] = set()
    for entry in members:
        if isinstance(entry, dict):
            n = entry.get("name")
            if isinstance(n, str) and n:
                names.add(n)
    return names


_PLAIN_SEGMENT = re.compile(r"(?!\.\.?$)[A-Za-z0-9_.-]{1,128}")
TEAM_CONFIG_ABSENT_RULE = "team_config_absent"


def _team_config_absent(team_name: str) -> bool:
    """True when teams/<team>/config.json is not a file: the config-less substrate (Desktop Code
    tab and SDK: teams/<full uuid>/ holds inboxes/ and no config.json, and the teammate was seen
    to get no Task tools there), or a session context naming a team that is gone. A name that is not one plain path segment is never looked up (False)."""
    if not isinstance(team_name, str) or not _PLAIN_SEGMENT.fullmatch(team_name):
        return False
    try:
        return not (get_claude_config_dir() / "teams" / team_name / "config.json").is_file()
    except (OSError, ValueError):
        return False


def _team_config_absent_advisory(team_name: str, name: str) -> str:
    return (f"PACT dispatch_gate: team {team_name!r} has no teams/{team_name}/config.json on "
            f"disk. A teammate spawned into a team the host never wrote gets no TaskList, TaskGet "
            f"or TaskUpdate and cannot reach team-lead by name, so {name!r} will not see its "
            "tasks. Brief it without them: put the whole mission in the spawn prompt (or "
            "SendMessage it after the spawn), and ask for the HANDOFF as its final message "
            "(Produced, Key decisions, next steps). Do not wait on its task file; record the "
            "result on the task yourself. If the session was resumed, the context may name an "
            "old team: run /repacted:bootstrap first.")


# ─── pure rule-eval composition (testable without stdin/stdout) ────────────

# Rule ⑥'s refusal in a registered teammate's own process. That process never has
# session context, so the context-failure suffix and the stale-session hint
# are withheld: both would send its reader to repair the lead's state.
_TEAMMATE_SPAWN_REFUSAL = (
    "PACT dispatch_gate: PACT specialists are spawned by the team-lead. "
    "This session is not the team-lead's, so this spawn is refused; ask "
    "the team-lead to spawn it."
)


def _resolves_a_registered_team(input_data: dict) -> bool:
    """True iff the frame's own session resolves a team membership."""
    try:
        from shared.background_work import frame_team_and_name

        team, _name = frame_team_and_name(input_data)
        return bool(team)
    except Exception:
        return False


def _lead_identity_refusal(name) -> tuple[str, str, str] | None:
    """The deny for a spawn name that is a lead identity, else None."""
    if not isinstance(name, str) or not name:
        return None
    folded = unicodedata.normalize("NFKC", name).casefold()
    lead_types = {t.casefold() for t in pact_context.LEAD_AGENT_TYPES}
    if (folded in lead_types or folded == "team-lead"
            or folded.rsplit(":", 1)[-1] in {t.rsplit(":", 1)[-1] for t in lead_types}):
        return ("DENY",
                f"PACT dispatch_gate: name {name!r} is a lead identity (reserved-token set: "
                "the lead's agent types and its member name). A teammate with it would be "
                "taken for the lead. Choose a role-descriptive name.",
                "name_lead_identity")
    return None


def _files(paths) -> str:
    """The agent files a refusal names: ascii() without quotes, so a file name from the
    repository cannot put a newline or control character into agent context."""
    shown = ", ".join(ascii(str(p))[1:-1] for p in paths[:_SHOWN_FILES])
    return shown + (f" and {len(paths) - _SHOWN_FILES} more" if len(paths) > _SHOWN_FILES else "")


def _registry_refusal(subagent_type: str, scoped: bool,
                      plugin_root: str) -> tuple[str, str, str] | None:
    """Rule 5's registry check, or None to go on. A namespaced spelling names the plugin's own
    agent, which no project file can shadow; a bare one is what the host resolves project
    first (code.claude.com/docs/en/sub-agents), so the project's agent files decide it."""
    shipped = shipped_specialists(plugin_root)
    not_registered = ("DENY",
                      f"PACT dispatch_gate: subagent_type {subagent_type!r} is not a registered "
                      "PACT specialist (no matching agents/pact-*.md in the plugin, and no "
                      "project agent of that name in .claude/agents).",
                      "specialist_not_registered")
    if not shipped:
        return not_registered
    if scoped:
        return None if subagent_type in shipped else not_registered
    directory = project_dir()
    if subagent_type not in shipped and not project_scope_allowed(directory):
        from shared.team_policy import refusal_note
        return ("DENY",
                f"PACT dispatch_gate: {subagent_type!r} is not a shipped specialist and the "
                "team policy turns project-scope specialists off."
                + refusal_note(("agents.",), directory or None)
                + f" [rule: {PROJECT_AGENTS_OFF_RULE}]",
                "project_agents_disabled")
    verdict = project_agents.resolve(subagent_type, shipped, project_scan(plugin_root, directory))
    if verdict.kind in ("shipped", "project"):
        return None
    if verdict.kind == "none":
        return not_registered
    return _project_refusal(subagent_type, verdict)


def _project_refusal(subagent_type: str, verdict) -> tuple[str, str, str]:
    files = _files(list(verdict.files))
    detail = ascii(verdict.detail)[1:-1]   # it can carry a directory path from the repository
    head = f"PACT dispatch_gate: subagent_type {subagent_type!r}: "
    if verdict.kind == "shadowed":
        return ("DENY", head + f"a project agent file ({files}) claims this shipped name, and "
                "the host starts a project agent before the plugin's. "
                f"[rule: {PROJECT_AGENT_SHADOW_RULE}]", "project_agent_shadows_plugin")
    if verdict.kind == "unbound":
        return ("DENY", head + f"the project agent file {files} has no teammate binding "
                f"({project_agents.BINDING} in its body). [rule: {PROJECT_AGENT_UNBOUND_RULE}]",
                "project_agent_unbound")
    if verdict.kind == "ambiguous":
        return ("DENY", head + f"the name cannot be tied to one project agent file ({files}"
                f"{': ' + detail if detail else ''}). "
                f"[rule: {PROJECT_AGENT_AMBIGUOUS_RULE}]", "project_agent_ambiguous")
    if verdict.kind == "incomplete":
        return ("DENY", head + f"the project's agent files could not all be read "
                f"({detail}), so a file shadowing this name cannot be ruled out. "
                f"[rule: {PROJECT_AGENTS_UNREADABLE_RULE}]", "project_agents_unreadable")
    return ("DENY", head + f"the project agent file {files} is not a valid specialist "
            f"({detail}). [rule: {PROJECT_AGENT_INVALID_RULE}]", "project_agent_invalid")


def _teams_mode() -> bool:
    """True when the session runs Agent Teams (shared/execution.py). Anything that fails
    reads as teams, which keeps every rule, as before the mode existed."""
    try:
        from shared import execution
        return execution.current(pact_context.get_session_dir()) == execution.TEAMS
    except Exception:  # noqa: BLE001 - an unreadable mode keeps the Teams rules
        return True


def evaluate_dispatch(
    tool_input: dict, input_data: dict | None = None,
) -> tuple[str, str | None, str | None]:
    """Compose rules (1)-(9). Returns (decision, reason, rule); decision is ALLOW, DENY or WARN.

    Pure: no stdin/stdout or writes. Exceptions escape to main(), which denies (fail-closed).
    """
    if not isinstance(tool_input, dict):
        tool_input = {}

    subagent_type = tool_input.get("subagent_type", "") or ""
    # The namespaced spelling (`repacted:pact-architect`) is checked as its bare
    # stem by every rule below. The journal keeps the spelling the caller used.
    if isinstance(subagent_type, str):
        subagent_type = pact_context.strip_pact_namespace(subagent_type)
    name = tool_input.get("name", "") or ""
    prompt = tool_input.get("prompt", "") or ""
    # The spawn-arg team_name is ignored by the platform, so it is never used as a path here
    # (only journaled); the team comes from get_team_name() at (6)-(8).

    # ⓪ A lead-identity name is refused for every spawn, before the carve-outs below allow
    # research and non-pact-* types through (architect-pact R1 on CLEANUP-B).
    refusal = _lead_identity_refusal(name)
    if refusal:
        return refusal

    # ① Carve-outs — sub-microsecond. SOLO_EXEMPT covers research agents
    # (general-purpose / Explore / Plan) that legitimately spawn without
    # name/team_name by design.
    if subagent_type in SOLO_EXEMPT:
        return ("ALLOW", None, None)
    # ② Non-pact-* spawns are not this gate's business — fall through.
    if not isinstance(subagent_type, str) or not subagent_type.startswith("pact-"):
        return ("ALLOW", None, None)

    # (3) Name presence. Name validation (4) stays: it is an injection/reserved-token defense.
    if not isinstance(name, str) or not name:
        return ("DENY",
                "PACT dispatch_gate: name= parameter is required for "
                "pact-* specialist spawns. See orchestrator persona §11.",
                "name_required")

    if len(name) > NAME_MAX_LENGTH:
        return ("DENY",
                f"PACT dispatch_gate: name length {len(name)} exceeds "
                f"limit {NAME_MAX_LENGTH}.",
                "name_too_long")
    normalized_name = unicodedata.normalize("NFKC", name)
    if not NAME_REGEX.match(normalized_name):
        return ("DENY",
                f"PACT dispatch_gate: name {name!r} must match "
                r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$ "
                "(lowercase alphanumerics, with hyphens permitted only "
                "between alphanumerics, checked after NFKC normalization).",
                "name_invalid_regex")
    if normalized_name in RESERVED_NAMES:
        return ("DENY",
                f"PACT dispatch_gate: name {name!r} is in the "
                "reserved-token set (would collide with a PACT routing "
                "literal or schema resolver type). Choose a unique "
                "role-descriptive name.",
                "name_reserved_token")

    # (5a) An empty plugin_root means context file AND env both missing: name that cause, not a
    # broken plugin install, and check it before the agents/ stat.
    plugin_root = pact_context.get_plugin_root()
    if not plugin_root:
        return ("DENY",
                "PACT dispatch_gate: plugin_root is unavailable. "
                + (pact_context.describe_context_failure()
                   or "pact-session-context.json has an empty plugin_root "
                      "and CLAUDE_PLUGIN_ROOT is not exported."),
                "plugin_root_unavailable")
    # ⑤b Plugin agents/ presence (cheap stat). Caught BEFORE the registry
    # check so a missing plugin install gets the more actionable
    # "plugin broken" message rather than "specialist not registered".
    if not (Path(plugin_root) / "agents").is_dir():
        return ("DENY",
                "PACT dispatch_gate: plugin agents/ directory is "
                "unavailable. Plugin install may be broken; check "
                "pact-session-context.json plugin_root field.",
                "plugin_agents_missing")
    # ⑤c The type is a shipped specialist no project file shadows, or one valid project
    # specialist. An empty shipped registry is fail-closed here too.
    scoped = tool_input.get("subagent_type") != subagent_type
    refusal = _registry_refusal(subagent_type, scoped, plugin_root)
    if refusal:
        return refusal

    # (6) Session team from the SSOT; empty fails closed because (7)/(8) use it as a path segment.
    session_team = pact_context.get_team_name()
    teams = _teams_mode()
    if not session_team:
        if (teams and isinstance(input_data, dict)
                and pact_context.classify_session_role(input_data) == "teammate"
                and _resolves_a_registered_team(input_data)):
            return ("DENY", _TEAMMATE_SPAWN_REFUSAL, "team_name_unavailable")
        message = ("PACT dispatch_gate: session team_name is unavailable "
                   "(pact-session-context.json missing or unreadable). "
                   "Re-run /repacted:bootstrap to restore session context.")
        # Append the shared context diagnosis (non-empty only when the
        # context file is underivable/absent) so the deny names the real
        # root cause instead of leaving recovery to guesswork.
        diagnosis = pact_context.describe_context_failure()
        if diagnosis:
            message += " " + diagnosis
        return ("DENY", message, "team_name_unavailable")

    # ⑦ Name uniqueness against live team members (resolved via the SSOT
    # session team, not the platform-ignored spawn arg).
    members = _team_member_names(session_team) if teams else set()
    if name in members:
        return ("DENY",
                f"PACT dispatch_gate: name {name!r} is already a live "
                f"member of team {session_team!r}. Use a unique name "
                "(append a numeric suffix or role-descriptor variant).",
                "name_not_unique")

    # ⑧ Task assignment: `repact dispatch` must precede the spawn, so the agent has a
    # dispatched task on arrival (metadata.dispatch.name; resolved via the SSOT session team).
    if not has_task_assigned(session_team, name):
        return ("DENY",
                f"PACT dispatch_gate: no task dispatched to name={name!r} "
                f"in team {session_team!r}. Run `repact dispatch --name {name} ...` "
                "first and make the Agent call it prints.",
                "no_task_assigned")

    # (8b) The team is not on disk: the teammate will have no Task tools. Advisory, never a deny
    # (the spawn still works as a prompt-briefed agent), and it replaces (9): here the mission
    # belongs in the prompt.
    if teams and _team_config_absent(session_team):
        return ("WARN", _team_config_absent_advisory(session_team, name), TEAM_CONFIG_ABSENT_RULE)

    # (9) Inline-mission heuristic; shadow mode journals and allows. The prompt
    # `repact dispatch` prints (the mission is in the brief file) is exempt.
    if BRIEF_PROMPT.fullmatch(prompt):
        return ("ALLOW", None, None)
    if (len(prompt) > PROMPT_MAX_LENGTH
            or not any(phrase in prompt for phrase in TASK_REFERENCE_PHRASES)):
        msg = (f"PACT dispatch_gate: prompt is long ({len(prompt)} "
               f"chars, threshold {PROMPT_MAX_LENGTH}) or lacks a "
               "TaskList reference. Mission belongs in the Task "
               "description, not the spawn prompt. WARN means STOP and "
               "re-dispatch correctly: put the mission in "
               "TaskCreate(description=...) and let the teammate read "
               "it via TaskList/TaskGet. See orchestrator persona §11.")
        if INLINE_MISSION_MODE == "deny":
            from shared.team_policy import refusal_note
            return ("DENY", msg + refusal_note(("pact_options.PACT_DISPATCH_INLINE_MISSION_MODE",)),
                    "long_inline_mission")
        if INLINE_MISSION_MODE == "shadow":
            # Journal sees the rule fired; caller treats as ALLOW (no advisory).
            return ("ALLOW", msg, "long_inline_mission")
        return ("WARN", msg, "long_inline_mission")

    return ("ALLOW", None, None)


# Spawn types the session cost cap never refuses: the read-only research agents and the
# secretary. general-purpose is capped: it has every tool, so it would be the way around.
COST_CAP_EXEMPT = frozenset({"Explore", "Plan", "pact-secretary"})


def _is_capped_spawn(tool_input: dict) -> bool:
    """The spawns rule (10) judges: every Agent spawn but COST_CAP_EXEMPT, whatever its
    plugin (built-in, another plugin's, a project's own). A spawn with no type starts
    general-purpose, so it is capped too. Rules (3)-(9) keep their own pact-* scope."""
    subagent_type = tool_input.get("subagent_type") if isinstance(tool_input, dict) else None
    if not isinstance(subagent_type, str) or not subagent_type:
        return True
    return pact_context.strip_pact_namespace(subagent_type) not in COST_CAP_EXEMPT


def _apply_cost_cap(decision: str, reason: str | None, rule: str | None, tool_input: dict,
                    input_data: dict) -> tuple[str, str | None, str | None]:
    """(10) The session cost cap on a spawn (1)-(9) did not refuse (_is_capped_spawn): a DENY past
    the cap; a note (shown as WARN additional context) when the cap cannot be enforced;
    otherwise the decision unchanged. Never raises: a fault here is a note, not a refusal."""
    if decision == "DENY" or not _is_capped_spawn(tool_input):
        return decision, reason, rule
    try:
        from shared import cost_cap
        verdict = cost_cap.check(input_data, pact_context.get_session_dir(), project_dir() or None)
        action, message, note_rule = verdict.action, verdict.message, cost_cap.NOTE_ID
    except Exception as error:  # noqa: BLE001 - fail open, loudly (shared/cost_cap.py)
        action, note_rule = "note", "cost_cap_unenforced"
        message = (f"PACT cost cap: the cap could not be checked ({type(error).__name__}), so "
                   "this dispatch goes ahead.")
    if action == "deny":
        return "DENY", message, "session_cost_cap"
    if action != "note":
        return decision, reason, rule
    if decision == "WARN":
        return "WARN", f"{reason}\n\n{message}", rule
    return "WARN", message, note_rule


# ─── main ──────────────────────────────────────────────────────────────────

def _journal_decision(decision: str, reason: str | None, rule: str | None,
                       tool_input: dict) -> None:
    """Journal one dispatch_decision row per decision (prompt redacted); errors are swallowed."""
    try:
        prompt = tool_input.get("prompt", "") if isinstance(tool_input, dict) else ""
        event = make_event(
            "dispatch_decision",
            decision=decision,
            rule=rule,
            subagent_type=tool_input.get("subagent_type") if isinstance(tool_input, dict) else None,
            name=tool_input.get("name") if isinstance(tool_input, dict) else None,
            team_name=tool_input.get("team_name") if isinstance(tool_input, dict) else None,
            reason=reason,
            prompt_redacted=_redact(prompt)[:1024],
        )
        # append_event_checked records journal_emit_skipped if the write fails. Nothing reads
        # dispatch_decision today; do not justify downstream logic on it being watched.
        append_event_checked(event, "dispatch_decision")
    except Exception:
        # Journal is best-effort; gate decision stands regardless.
        pass


# Deny rules a restart-induced stale team/store can cause; only these get the stale note.
_STALE_DIAGNOSABLE_RULES = frozenset({"team_name_unavailable", "no_task_assigned"})

# Remedy is the manual repoint: the bootstrap ritual does not rewrite these records.
_STALE_REALIGN_HINT = (
    " This dispatch denial is likely a STALE-TEAM/STORE MISMATCH, not a "
    "genuinely missing task: after a Claude Code restart/fork the platform "
    "minted a new team for the live session while PACT's persisted team_name "
    "went stale, so this gate read an orphaned task store. Working recovery "
    "(measured): edit pact-session-context.json in the OLD session's "
    "directory (the one named for the OLD session id) and set team_name to "
    "the LIVE team (the newest teams/session-*/config.json under your "
    "Claude config dir); leave session_id and the session journal dir "
    "untouched (journal continuity), then re-dispatch."
)


def _augment_deny_with_stale_diagnosis(
    rule: str | None, message: str, input_data: dict,
) -> str:
    """Return message plus a stale-team self-diagnosis when a restart mismatch is detected.

    Message-only (never changes the decision), never raises (falls back to message), and runs
    regardless of the bootstrap marker (the marker can be stamped for the stale team).
    """
    try:
        if rule not in _STALE_DIAGNOSABLE_RULES:
            return message
        if not isinstance(input_data, dict):
            return message
        stale_block = detect_stale_session_block(input_data)
        if not stale_block:
            return message
        return message + stale_block + _STALE_REALIGN_HINT
    except Exception:
        # Fail-safe: any error in the diagnosis path → original deny message.
        # The deny itself is unaffected; only the optional self-diagnosis is
        # dropped. NEVER re-raise out of the dispatch path.
        return message


# Appended to the rule-(8) deny when no stale-team mismatch was detected. Enumerates the KNOWN
# causes and a positive-only self-check; see the design notes before
# rewording (the opening qualifier, the cause-(1) wording and the absolute URL are load-bearing).
_CAUSE_ENUMERATION = (
    "\n\n"
    "DIAGNOSIS — this gate did not OBSERVE a task dispatched to this name.\n"
    "That is not the same as one not existing.\n"
    "\n"
    "The instruction above resolves cause (1) ONLY. Under (2), (3) and (4)\n"
    "creating the tasks changes nothing and this denial repeats unchanged,\n"
    "so identify the cause before acting on that instruction.\n"
    "\n"
    "TO NARROW IT DOWN, confirm all four task tools are available to you:\n"
    "    ToolSearch \"select:TaskCreate,TaskUpdate,TaskList,TaskGet\"\n"
    "  Any missing      -> case (3) below. For the setting that restores\n"
    "                      them, see\n"
    "                      https://github.com/v4lheru/repacted-plugin#if-specialist-agents-will-not-spawn\n"
    "  All four present -> not case (3). That rules out one cause, not the rest.\n"
    "If listing the task store reports a PERMISSIONS ERROR, you are in case (4).\n"
    "\n"
    "KNOWN CAUSES:\n"
    " (1) No task was dispatched to this name — `repact dispatch` was not run,\n"
    "     or its task was since cleared. Run it, then make the Agent call it prints.\n"
    " (2) This session's recorded team no longer matches the live one, so this\n"
    "     gate reads a different task store than the one the task tools write.\n"
    "     Recovery: set team_name in the OLD session's pact-session-context.json\n"
    "     to the LIVE team (the newest teams/session-*/config.json), leaving\n"
    "     session_id and the journal dir untouched (journal continuity), then\n"
    "     re-dispatch.\n"
    " (3) The task-management tools are not available in this session at all.\n"
    "     They can be withheld by a server-controlled feature gate keyed on the\n"
    "     session's model — in which case (1) cannot be satisfied, because the\n"
    "     tool needed to create the task is itself absent.\n"
    " (4) The task store could not be READ — a permissions error, a relocated\n"
    "     or unreadable store directory, or an I/O fault. This gate cannot\n"
    "     distinguish an unreadable store from an empty one.\n"
)


# Fail-closed fallback for a non-str message (unreachable today). Without it the concat
# would raise outside the guarded try, exit 1 is non-blocking, and the spawn would proceed.
_MISSING_DENY_REASON = (
    "PACT dispatch_gate: dispatch DENIED, but the gate produced no reason "
    "text. That is an internal defect in the gate itself, not a problem with "
    "the dispatch. The denial stands."
)


def _compose_deny_diagnosis(
    rule: str | None, message: str | None, input_data: dict,
) -> str:
    """Deny text: teammate refusal verbatim, else the stale-team diagnosis, else the rule-(8)
    cause enumeration, else message. The two diagnoses are mutually exclusive by construction.

    Never raises; adds no new I/O (the CLAUDE.md read in the detector already ran on this path).
    "augmented != message" means the detector fired because its fire path only appends.
    A non-str message returns _MISSING_DENY_REASON (an unenforced precondition here fails open).
    """
    if not isinstance(message, str):
        return _MISSING_DENY_REASON
    if message == _TEAMMATE_SPAWN_REFUSAL:
        return message

    augmented = _augment_deny_with_stale_diagnosis(rule, message, input_data)
    if augmented != message:
        return augmented
    if rule == "no_task_assigned":
        return message + _CAUSE_ENUMERATION
    return message


def _task_store_halt() -> str | None:
    """The HALT sentence while task_store_probe's marker says the store is dark, else None.
    An unreadable marker is no marker (fail-open here: the probe, not this gate, decides)."""
    try:
        marker = read_probe_marker(pact_context.get_session_dir())
    except Exception:  # noqa: BLE001
        return None
    if marker.get("halt") is not True:
        return None
    return halt_sentence(str(marker.get("team") or "?"), str(marker.get("task_id") or "?"))


def main() -> None:
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        # Malformed stdin → fail-OPEN (input-side failure is the harness's
        # domain; cannot DENY meaningfully without parsed input). Mirrors
        # bootstrap_gate.py and the other PreToolUse gates.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    if not isinstance(input_data, dict):
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    if tool_name != "Agent":
        # Hook is registered under matcher='Agent' but defensive belt: if
        # something else routes here, no-op rather than misclassify.
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    pact_context.init(input_data)
    tool_input = input_data.get("tool_input", {}) or {}

    halted = _task_store_halt()
    if halted is not None:
        # The probe found the host task store dark: no spawn can be gated, waited on or
        # handed off, so none starts until a later probe finds a task file again.
        _journal_decision("DENY", halted, HALT_RULE, tool_input)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": "deny",
                                                 "permissionDecisionReason": halted}}))
        sys.exit(2)

    try:
        decision, reason, rule = evaluate_dispatch(tool_input, input_data)
        decision, reason, rule = _apply_cost_cap(decision, reason, rule, tool_input, input_data)
    except Exception as e:
        # Runtime fail-closed: a runtime exception in the rule logic is
        # the same defect class as #658 — must DENY, must include
        # hookEventName.
        _emit_load_failure_deny("runtime", e)

    _journal_decision(decision, reason, rule, tool_input)

    if decision == "ALLOW":
        print(_SUPPRESS_OUTPUT)
        sys.exit(0)
    if decision == "DENY":
        # Message-only diagnosis (stale-team or rule-(8) causes, never both); never raises.
        deny_message = _compose_deny_diagnosis(rule, reason, input_data)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_message,
            }
        }))
        sys.exit(2)
    # WARN: emit additionalContext, exit 0 (advisory; per architect §7(a)
    # the empirical injection-vs-silent-drop is validated post-merge).
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": reason,
        }
    }))
    sys.exit(0)


if __name__ == "__main__":
    main()
