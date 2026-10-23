"""
Location: pact-plugin/hooks/dispatch_registry.py
Summary: The check interface of the hook dispatcher and the table of the checks it runs,
    per event, in the order hooks.json listed them as separate hooks.
Used by: hooks/dispatch.py (runs the table), tests (expand_hooks_json gives the
    per-script view of hooks.json that the registration tests check).

Standard library only, and imports nothing from the plugin: it is loaded before every
tool call.

A check is either a script (`script`, run in-process exactly as `python3 <script>` ran it
before) or a native function (`native`, "module:function" under hooks/). A native check
has the signature

    def check(payload: dict, ctx: CheckContext) -> Result | None

`payload` is the parsed stdin object ({} when stdin is not a JSON object). It returns None
for no opinion, or a Result. Result.decision is one of:

    None     no decision; `context` and `system_message` still reach the model / user
    "allow"  PreToolUse permissionDecision allow
    "defer"  PreToolUse permissionDecision defer (the normal permission flow)
    "ask"    PreToolUse permissionDecision ask
    "deny"   PreToolUse permissionDecision deny
    "block"  exit 2 with `reason` on stderr; on Stop, SubagentStop, PostToolUse and
             UserPromptSubmit the top-level {"decision": "block", "reason": ...} instead

`rule` is a short reason code for the journal (never text). `journaled` says the check
already wrote its own gate_refusal row, so the dispatcher writes none for it. A raise, a
timeout or an import error is caught by the dispatcher: one check_failure row, no
decision, and the next check runs; for a check with `on_timeout="deny"` it is also a deny
(`[rule: <name>.timeout]` or `[rule: <name>.error]`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# An advisory check's alarm (on_timeout="discard"): a hung advisory check decides nothing,
# so a long alarm only stalls the call; 60 s is still far above any measured latency. The
# host's own default (600 s) made the Bash PreToolUse entry wait about 4,410 s. The lower
# alarm on UserPromptSubmit is the host's default there.
DEFAULT_TIMEOUT_S = 60
USER_PROMPT_TIMEOUT_S = 30
# A security check gets its own short alarm and a deny when it fires (`on_timeout="deny"`);
# every number is about 100x the measured latency of the check. No security check may run
# longer than SECURITY_TIMEOUT_CAP_S: a contract test pins it. dispatch_gate keeps the 60 s
# it had as an advisory check: a hung spawn gate refuses the spawn instead of allowing it.
SECURITY_TIMEOUT_CAP_S = 90
SECURITY_TIMEOUTS_S = {"env_guard": 15, "merge_guard_pre": 15, "egress_guard": 60,
                       "git_commit_check": 90, "worktree_guard": 15, "write_checks": 60,
                       "task_write_guard": 15, "ops_frame_guard": 15, "dispatch_gate": 60}
# The host's entry timeout is the sum of the member alarms plus this margin, so the
# dispatcher's own alarm always fires before the host pre-empts the process.
ENTRY_MARGIN_S = 30

DISPATCH_SCRIPT = "hooks/dispatch.py"


@dataclass(frozen=True)
class Result:
    """What a native check returns. See the module docstring for each field."""

    decision: Optional[str] = None
    reason: str = ""
    context: str = ""
    system_message: str = ""
    rule: str = ""
    journaled: bool = False


@dataclass
class CheckContext:
    """What the dispatcher hands a native check besides the payload."""

    event: str
    tool_name: str
    raw: str
    journal: Callable[..., None] = field(default=lambda event_type, **fields: None)
    payload: dict = field(default_factory=dict)
    _actor: Any = field(default=None, repr=False)

    @property
    def actor(self):
        """The frame's Actor (shared.actor), resolved on first use and once per check.
        Imported lazily: dispatch.py keeps the shared package out of its own process."""
        if self._actor is None:
            from shared.actor import resolve_actor

            self._actor = resolve_actor(self.payload)
        return self._actor


@dataclass(frozen=True)
class Check:
    """One check of one event. `matcher` is the hooks.json matcher it had (None: all)."""

    name: str
    matcher: Optional[str] = None
    script: Optional[str] = None
    argv: tuple = ()
    native: Optional[str] = None
    timeout_s: int = DEFAULT_TIMEOUT_S
    journals_refusals: bool = False
    # "discard": a timed-out check decided nothing (one check_failure row, the next check
    # runs). "deny": the timeout itself is the refusal, exit 2 with `[rule: <name>.timeout]`.
    on_timeout: str = "discard"
    # Agent Teams machinery: dispatch.py skips it when the
    # session's execution mode is subagents (shared/execution.py).
    teams_only: bool = False

    def legacy_command(self) -> str:
        """The hooks.json command that ran this check as its own process (for a native
        check, the module that holds it)."""
        if self.script is None:
            module = (self.native or self.name).split(":", 1)[0]
            return f'python3 "${{CLAUDE_PLUGIN_ROOT}}/hooks/{module}.py"'
        args = "".join(f" {arg}" for arg in self.argv)
        return f'python3 "${{CLAUDE_PLUGIN_ROOT}}/{self.script}"{args}'


def _hook(name: str, matcher: Optional[str] = None, **kwargs) -> Check:
    return Check(name=name, matcher=matcher, script=f"hooks/{name}.py", **kwargs)


def _prompt_hook(name: str, **kwargs) -> Check:
    return _hook(name, timeout_s=USER_PROMPT_TIMEOUT_S, **kwargs)


def _teams_hook(name: str, matcher: Optional[str] = None, **kwargs) -> Check:
    """A check that only Agent Teams needs: skipped in subagents mode."""
    return _hook(name, matcher, teams_only=True, **kwargs)


def _security_hook(name: str, matcher: str) -> Check:
    """A security check: its own short alarm, and a deny when the alarm fires."""
    return _hook(name, matcher, timeout_s=SECURITY_TIMEOUTS_S[name], on_timeout="deny")


# Every synchronous hook, per event, in the order hooks.json listed it. The async hooks
# (file_tracker, session_end) stay their own hooks.json entries: an
# async hook runs in the background after the host has moved on, which one synchronous
# process cannot reproduce.
REGISTRY: dict[str, tuple[Check, ...]] = {
    "UserPromptSubmit": (
        _prompt_hook("bootstrap_marker_writer", teams_only=True),
        _prompt_hook("bootstrap_prompt_gate", teams_only=True),
        _prompt_hook("missed_wake_scan", teams_only=True),
    ),
    "SessionStart": (
        _hook("session_init"),
        _teams_hook("missed_wake_scan"),
        _hook("doctor_brief"),
    ),
    "PreCompact": (_hook("precompact_state_reminder"),),
    "PostCompact": (_hook("postcompact_archive"),),
    "PreToolUse": (
        _teams_hook("bootstrap_gate", "Agent|Task|Read|Bash"),
        _security_hook("git_commit_check", "Bash"),
        Check(name="docker_lock_gate", matcher="Bash", native="docker_lock_gate:dispatch_check",
              journals_refusals=True),
        _security_hook("merge_guard_pre", "Bash"),
        _teams_hook("wait_filler_gate", "Bash"),
        Check(name="background_test_gate", matcher="Bash",
              native="background_test_gate:dispatch_check", teams_only=True),
        _hook("process_table_guard", "Bash"),
        _security_hook("env_guard", "Read|NotebookRead|Grep|Bash"),
        _security_hook("egress_guard", "mcp__.*|WebFetch|Artifact|Bash"),
        _security_hook("dispatch_gate", "Agent"),
        _security_hook("worktree_guard", "Edit|Write|MultiEdit|NotebookEdit"),
        _security_hook("write_checks", "Bash"),
        # The workflow writes stay on the repact commands.
        Check(name="task_write_guard", matcher="TaskUpdate|TaskCreate",
              native="task_write_guard:dispatch_check",
              timeout_s=SECURITY_TIMEOUTS_S["task_write_guard"], on_timeout="deny"),
        Check(name="ops_frame_guard", matcher="Bash", native="ops_frame_guard:dispatch_check",
              timeout_s=SECURITY_TIMEOUTS_S["ops_frame_guard"], on_timeout="deny"),
        _hook("unattended_gate", "AskUserQuestion", journals_refusals=True),
        _hook("merge_question_gate", "AskUserQuestion"),
    ),
    "SubagentStop": (Check(name="subagent_stop", native="subagent_stop:dispatch_check"),
                     Check(name="teams_turn_end", native="teams_turn_end:dispatch_check",
                           teams_only=True)),
    "Stop": (_teams_hook("stop_background_gate"),),
    "SubagentStart": (_teams_hook("peer_inject"),),
    "SessionEnd": (_hook("session_ledger"),
                   Check(name="distill_enqueue", native="distill_enqueue:dispatch_check")),
    "TeammateIdle": (_teams_hook("teammate_idle"),),
    "PostToolUse": (
        _hook("track_files", "Edit|Write|Bash"),
        _hook("merge_guard_post", "AskUserQuestion|Bash"),
        Check(name="task_store_probe", matcher="TaskCreate|TaskUpdate",
              native="task_store_probe:dispatch_check"),
        # TaskCreate/TaskUpdate: under `claude -p` the one prompt fires before the team
        # exists, and the platform provisions it on the first TaskCreate.
        _teams_hook("bootstrap_marker_writer", "Agent|TaskCreate|TaskUpdate"),
        _hook("run_ledger", "Bash"),
        _teams_hook("amendment_relay", "Bash|Edit|Write|Read|TaskUpdate|MultiEdit|NotebookEdit"),
        # Fail-open by its own contract (output_secret_scan.py); the short alarm only
        # bounds the stall.
        _hook("output_secret_scan", "Bash", timeout_s=30),
    ),
    "PostToolUseFailure": (_hook("run_ledger", "Bash"),),
}

# Events whose stdin names a tool, so a check's matcher applies to `tool_name`.
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PostToolUseFailure"})


def checks_for(event: str) -> tuple[Check, ...]:
    return REGISTRY.get(event, ())


_EXACT_MATCHER = re.compile(r"[A-Za-z0-9_\- ,|]*")


def host_matches(matcher: Optional[str], tool_name: str) -> bool:
    """The host's matcher rule: letters, digits, `_`, `-`, spaces, `,` and `|` make a list
    of exact names; anything else is an unanchored regular expression; none matches all.
    A matcher that is not a valid expression matches (the check then decides)."""
    if matcher in (None, "", "*"):
        return True
    if _EXACT_MATCHER.fullmatch(matcher):
        return tool_name in {part.strip() for part in re.split(r"[|,]", matcher)}
    try:
        return re.search(matcher, tool_name) is not None
    except re.error:
        return True


def named_tools(event: str) -> list[str]:
    """The tool names the event's matchers spell out, in first-seen order: every name of an
    exact list, and every plain-name alternative of a regular expression."""
    names: list[str] = []
    for check in checks_for(event):
        if not check.matcher:
            continue
        if _EXACT_MATCHER.fullmatch(check.matcher):
            parts = [part.strip() for part in re.split(r"[|,]", check.matcher)]
        else:
            parts = [part for part in check.matcher.split("|") if part.isidentifier()]
        names += [part for part in parts if part and part not in names]
    return names


def checks_for_tool(event: str, tool_name: str) -> tuple[Check, ...]:
    return tuple(check for check in checks_for(event) if host_matches(check.matcher, tool_name))


def tool_groups(event: str) -> list[tuple[str, ...]]:
    """The named tools of a tool event, grouped by the checks they run (same checks, one
    group), in first-seen order. Each group is one hooks.json entry with an exact matcher."""
    groups: dict[tuple[str, ...], list[str]] = {}
    for name in named_tools(event):
        key = tuple(check.name for check in checks_for_tool(event, name))
        groups.setdefault(key, []).append(name)
    return [tuple(names) for names in groups.values()]


def fallback_matcher(event: str) -> Optional[str]:
    """The matcher of the entry for every tool no group names, or None when no check runs
    for such a tool. A regular expression the host and Python read alike. When every such
    check has a regular expression of its own, the entry matches only what one of them
    matches, so a tool no check runs for starts no process."""
    others = [check.matcher for check in checks_for(event)
              if check.matcher is None or not _EXACT_MATCHER.fullmatch(check.matcher)]
    if not others:
        return None
    names = "|".join(re.escape(name) for name in named_tools(event))
    if any(matcher in (None, "", "*") for matcher in others):
        return f"^(?!(?:{names})$).*"
    # Each check's own expression is unanchored, as the host reads it: .*? before it.
    return f"^(?!(?:{names})$)(?:" + "|".join(f".*?(?:{matcher})" for matcher in others) + ")"


def dispatch_entries(event: str) -> list[tuple[Optional[str], Optional[str], int]]:
    """(matcher, group argument, timeout) of each synchronous hooks.json entry of the event.
    A non-tool event has one entry. A tool event has one per tool group, whose argument
    tells the dispatcher which tools the host matched (stdin need not say), and one for
    every other tool. The host matches one entry per call, so one process runs."""
    checks = checks_for(event)
    if event not in TOOL_EVENTS:
        return [(None, None, _entry_timeout_of(checks))]
    entries = []
    for group in tool_groups(event):
        timeout = _entry_timeout_of(checks_for_tool(event, group[0]))
        entries.append(("|".join(group), ",".join(group), timeout))
    fallback = fallback_matcher(event)
    if fallback is not None:
        others = [check for check in checks if check.matcher is None
                  or not _EXACT_MATCHER.fullmatch(check.matcher)]
        entries.append((fallback, None, _entry_timeout_of(others)))
    return entries


def _entry_timeout_of(checks) -> int:
    """The host timeout of one entry: every member alarm, one after the other, plus the
    margin that keeps the dispatcher's alarm ahead of the host's."""
    return sum(check.timeout_s for check in checks) + ENTRY_MARGIN_S


def group_checks(event: str, group: str) -> Optional[tuple[Check, ...]]:
    """The checks of a group argument ("Edit,Write"), or None when it names no group."""
    for names in tool_groups(event):
        if ",".join(names) == group:
            return checks_for_tool(event, names[0])
    return None


def dispatch_command(event: str, group: Optional[str] = None) -> str:
    suffix = f" {group}" if group else ""
    return f'python3 "${{CLAUDE_PLUGIN_ROOT}}/{DISPATCH_SCRIPT}" {event}{suffix}'


def rebuild_hooks_json(config: dict) -> dict:
    """hooks.json with each event's dispatcher entries replaced, where the first stood, by
    dispatch_entries(event). Every other entry is kept. After a REGISTRY change, run in
    hooks/ (it reads hooks.json into a variable before open(p, "w") truncates the file):
    python3 -c 'import json,dispatch_registry as r; p="hooks.json"; c=r.rebuild_hooks_json(json.load(open(p))); open(p,"w").write(json.dumps(c, indent=2)+"\\n")'
    """
    rebuilt: dict = {**config, "hooks": {}}
    for event, entries in config.get("hooks", {}).items():
        out, placed = [], False
        for entry in entries:
            if not any(is_dispatch_command(hook.get("command", "")) for hook in entry.get("hooks", [])):
                out.append(entry)
                continue
            if placed:
                continue
            placed = True
            for matcher, group, timeout in dispatch_entries(event):
                hook = {"type": "command", "command": dispatch_command(event, group), "timeout": timeout}
                out.append({"matcher": matcher, "hooks": [hook]} if matcher is not None
                           else {"hooks": [hook]})
        rebuilt["hooks"][event] = out
    return rebuilt


def entry_timeout(event: str) -> int:
    """Seconds for the event's longest dispatcher entry: the checks one call can match, each
    at its own timeout, one after the other. A bound only; real checks take milliseconds."""
    return max(timeout for _matcher, _group, timeout in dispatch_entries(event))


def is_dispatch_command(command: str) -> Optional[str]:
    """The event a hooks.json command dispatches, or None for any other command."""
    marker = f'/{DISPATCH_SCRIPT}" '
    if marker not in command:
        return None
    rest = command.split(marker, 1)[1].split()
    return rest[0] if rest else None


def expand_hooks_json(config: dict) -> dict:
    """hooks.json with the dispatcher entries of an event replaced, at the first of them, by
    one entry per check, each with the matcher and command it had as a separate hook. Every
    other entry is kept."""
    expanded: dict = {"hooks": {}}
    for event, entries in config.get("hooks", {}).items():
        out = []
        done: set = set()
        for entry in entries:
            hooks = entry.get("hooks", [])
            events = [is_dispatch_command(h.get("command", "")) for h in hooks]
            if not any(events):
                out.append(entry)
                continue
            for hook, dispatched in zip(hooks, events):
                if not dispatched:
                    out.append({**entry, "hooks": [hook]})
                    continue
                if dispatched in done:
                    continue
                done.add(dispatched)
                for check in checks_for(dispatched):
                    legacy = {"hooks": [{"type": "command", "command": check.legacy_command()}]}
                    if check.matcher is not None:
                        legacy = {"matcher": check.matcher, **legacy}
                    out.append(legacy)
        expanded["hooks"][event] = out
    return expanded
