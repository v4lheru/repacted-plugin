"""
Location: pact-plugin/hooks/shared/session_init_common.py
Summary: SessionStart pieces every frame shares: the notice literals, the
    launch notices (steps 0-0d), the environment refresh (steps 1-4c), the
    session placeholder and bootstrap directive text, and the exception
    safety net.
Used by: hooks/session_init.py, shared/session_init_lead.py,
    shared/session_init_resume.py

Moved out of session_init.py unchanged; the step numbers in the comments are
the ones main()'s docstring lists.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from shared import state_file
from shared.pact_context import _build_session_path as build_session_path, project_slug
from shared.claude_md_manager import (
    ensure_project_memory_md,
    migrate_to_managed_structure,
    strip_orphan_kernel_block,
)
from shared.coexistence import coexistence_warning
from shared.constants import COMPACTION_TEAMMATE_CLAUSE
from shared.dispatch_helpers import is_registered_pact_specialist
from shared.merge_guard_common import (
    TOKEN_DIR,
    cleanup_orphan_tokens as _cleanup_orphan_tokens,
)
from shared.pact_context import (
    classify_session_role, is_lead, reconstruct_session_dir, strip_pact_namespace,
)
from shared.paths import get_claude_config_dir
from shared.plugin_manifest import format_plugin_banner
from shared.project_scope import WORKTREE_IDENTITY_FILE, _rev_parse_path
from shared.repact_shim import ensure_shim
from shared.symlinks import SYMLINKS_VERIFIED_MESSAGE, setup_plugin_symlinks

# The secretary spawns on demand (build plan §3 SECRETARY), and the session
# briefing it used to deliver at spawn is computed by build_session_briefing
# and appended to this hook's context. So a context reset no longer asks a
# secretary for a briefing; it points at the hook briefing instead.
_SECRETARY_ON_DEMAND_CLAUSE = (
    "The session briefing below is computed by this hook; message the "
    "secretary only for memory it holds, and spawn it first if it is not on "
    "the team (/repacted:bootstrap, On-demand secretary)."
)


# One-time startup notice about unattended-run stalls, emitted when the
# effective teammateMode is not positively "tmux". Emitted via system_messages
# (user-facing) by main() step 0b. Lives HERE (presentation layer) rather than
# in shared/teammate_mode.py (resolution layer) per SRP.
# Pure literal (no interpolation) so tests can pin the exact substring.
#
# THE tmux CLAIM IS SCOPED ON PURPOSE AND MUST STAY SCOPED. An unattended run
# stalls on two independent channels: a teammate wake not being delivered, and
# a background job finishing with nobody listening. Switching teammate mode
# addresses the FIRST ONLY — the second never uses the message path. An earlier
# version of this notice recommended tmux without that bound, so a reader could
# follow it, switch modes, and still stall on the failure the notice appears to
# warn about. Do not restore an unqualified "relaunch with tmux for hands-off
# runs": that sentence is the defect, not a simplification of it.
_INPROCESS_MODE_NOTICE = (
    "PACT: unattended runs may stall in in-process teammate mode "
    "(the lead can sit idle awaiting a wake that needs a manual nudge). "
    "`--teammate-mode tmux` makes teammate wake delivery reliable; it does "
    "NOT cover a background job that finishes with nobody watching "
    "— see reference/unattended-runs.md."
)

# Unknown-role startup warning. The lead-only writes below are gated
# behind is_lead, which keys on the harness-set agent_type field. A session
# launched WITHOUT `--agent` (or with a non-PACT agent_type) carries no
# recognizable role — classify_session_role() returns "unknown" — so its
# session_init silently performs none of the lead-only writes. That is the
# intended fail-toward-teammate direction, but it is invisible to an operator
# who MEANT to launch the orchestrator and forgot the flag. This notice makes
# that case observable. IT USES TWO CHANNELS ACROSS THREE EMISSION SITES,
# AND EACH FRAME GETS EXACTLY ONE OF THEM. Do not state one population for
# the pair.
#   additionalContext: gated by `frame_role == "unknown"`, which needs an
#     ABSENT agent_type, because a truthy agent_type classifies as a
#     teammate. Every source, so a frame after a compact keeps it.
#   systemMessage: only for the frames the first channel never reaches. The
#     source must be a launch event (`startup` or `resume`), the frame must
#     NOT classify "unknown", AND _should_warn_unknown_role must pass: an
#     agent_type that is present but is not the lead and is not a
#     registered specialist. So a typo such as `--agent pact-architct`
#     classifies as a teammate and reaches this channel ON A LAUNCH, and on
#     a compact or a clear it reaches no channel at all.
# Pure literal so tests can pin the exact substring.
_UNKNOWN_ROLE_NOTICE = (
    "PACT: this session has no recognized agent role (no `--agent` flag, or an "
    "unrecognized agent_type), so lead-only session setup was skipped. If you "
    "meant to drive PACT as the orchestrator, relaunch with "
    "`--agent repacted:pact-orchestrator`."
)


# Caveat appended to the symlink refresh status when the refresh MOVED
# something. It names the subject of the repair and the one thing the repair
# does NOT reach.
#
# EMITTED ONLY WHEN A LINK MOVED, and that is a correctness property rather
# than a matter of taste. The refresh makes the resolution SURFACE current and
# it does NOT make a LOADED body current, because an agent keeps the body it
# got at spawn. A notice that spoke on a quiet session start would report
# "surface current" almost always, and a reader would take that as "the bodies
# are fresh". ONE-DIRECTIONAL EMISSION removes that reading: the notice emits
# no green, so no green can be misread.
_SYMLINK_REPOINT_NOTICE = (
    "The PACT agent and protocol links now point at the current plugin root. "
    "This does NOT refresh an agent that is live: an agent keeps the body it "
    "got at spawn, and a new spawn gets the current body."
)


def _should_warn_unknown_role(input_data: dict) -> bool:
    """Decide whether the unknown-role startup notice should fire.

    Fires when the frame has NO recognized PACT role:
      classify_session_role == "unknown"  (agent_type absent)
        OR
      agent_type is present AND NOT is_lead AND NOT a recognized specialist.

    The "present-but-unrecognized" arm catches a mis-launched / typo'd
    agent_type (e.g. ``--agent pact-architct``) that the absent-only check
    misses. Recognized = the live ``agents/pact-*.md`` registry (SSOT), tested
    via ``is_registered_pact_specialist``.

    ORDERING IS LOAD-BEARING — do NOT reorder (security-engineer ruling):
    ``is_lead`` is checked BEFORE the registry. ``pact-orchestrator.md`` IS in
    the glob set, so the registry would recognize the unqualified lead spelling
    as a "specialist" — but is_lead short-circuits first, so a genuine lead is
    never mis-bucketed and a registered-lead-spelling edge can't suppress the
    notice for a frame that should get it.

    plugin_root is read from the ENV (``CLAUDE_PLUGIN_ROOT``), NOT the cache:
    this notice fires BEFORE build_context_cache populates the pact_context
    cache, so a cache-backed registry lookup would see an empty plugin_root →
    empty registry → every teammate would false-fire the notice. The env is the
    authoritative pre-cache source (it is the same value the cache later copies).

    SPELLING-SYMMETRY: strip a leading namespace before the membership test, so
    a qualified specialist spelling (``repacted:pact-backend-coder``) is recognized
    just as is_lead accepts both qualified and unqualified lead spellings.

    FAIL-OPEN residual: when even the env plugin_root is empty/unresolvable, the
    registry is empty and a present-but-non-lead frame fires the notice. That is
    correct — an install with no resolvable plugin_root is broken, and a
    spurious advisory notice is harmless (the notice never DENIES).
    """
    if classify_session_role(input_data) == "unknown":
        return True
    if is_lead(input_data):
        return False
    agent_type = input_data.get("agent_type")
    if not isinstance(agent_type, str):
        # Present-but-non-string (unhashable/odd) agent_type: not lead, not a
        # resolvable specialist spelling → treat as unrecognized → fire.
        return True
    stripped = strip_pact_namespace(agent_type)
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    return not is_registered_pact_specialist(stripped, plugin_root=plugin_root)


def check_additional_directories() -> str | None:
    """
    Check if required PACT directories are in additionalDirectories in settings.json.

    Checks for both ~/.claude/teams and ~/.claude/pact-sessions.
    Returns a tip message listing whichever directories are missing,
    or None if all are already present.
    Fail-open: returns None on any error (file missing, malformed JSON, etc.).
    """
    try:
        settings_path = get_claude_config_dir() / "settings.json"
        if not settings_path.exists():
            return None  # No settings file — nothing to check

        settings = json.loads(settings_path.read_text(encoding="utf-8"))

        additional_dirs = settings.get("permissions", {}).get(
            "additionalDirectories", []
        )
        if not isinstance(additional_dirs, list):
            return None  # Unexpected type — fail-open

        # Resolve all configured paths for comparison
        configured: set[Path] = set()
        for entry in additional_dirs:
            if not isinstance(entry, str):
                continue
            # Expand ~ using Path.home() (not expanduser which bypasses monkeypatch)
            if entry.startswith("~/"):
                expanded = (Path.home() / entry[2:]).resolve()
            else:
                expanded = Path(entry).resolve()
            configured.add(expanded)

        # Check which required directories are missing. The path DISPLAYED is
        # the same object the membership test ran on — deliberately not a
        # separate display literal. A tilde literal here would name a directory
        # this check never looked at, so on a non-default config root the user
        # would add a path that still does not satisfy the test, and following
        # the tip would not silence the tip.
        required = [
            (get_claude_config_dir() / "teams").resolve(),
            (get_claude_config_dir() / "pact-sessions").resolve(),
        ]
        missing = [path for path in required if path not in configured]

        if not missing:
            return None  # All required directories configured

        dirs_list = ", ".join(f"`{d}`" for d in missing)
        return (
            f"PACT tip: Add {dirs_list} to `additionalDirectories` in your "
            f"`{settings_path}` to avoid permission prompts for team and "
            "session file operations."
        )
    except Exception:
        return None  # Fail-open: never block session start


def _build_safety_net_context(
    team_name: str | None, frame_role: str | None = None, source: str | None = None
) -> str:
    """
    Build a minimal governance-delivery additionalContext string for the
    exception safety net in main().

    The returned string MUST start with the role-appropriate
    "YOUR PACT ROLE: <role>." marker at byte 0 (line-anchored). For a lead /
    unknown / unclassified frame (the default) the marker is
    "YOUR PACT ROLE: orchestrator." and the string includes the
    `Skill("repacted:bootstrap")` invocation so the team-lead still loads its
    operating instructions, governance policy, and workflow protocols even
    when main() failed before building the normal team-identification
    string. For a teammate frame (frame_role == "teammate") the marker is
    "YOUR PACT ROLE: teammate." and the body is a minimal TaskList directive —
    a teammate MUST NOT be handed the orchestrator-only bootstrap directive.

    frame_role is captured in main() BEFORE the risky assembly (alongside
    team_name). THE THREE VALUES AND None ARE FOUR CASES. "teammate" gets the
    teammate marker. "lead" and "unknown" BOTH get the orchestrator marker.
    None means the classifier DID NOT RUN, so nothing is known, and it gets a
    role-free failure note.

    The ruling behind the four cases (why "unknown" and None keep the
    bootstrap ladder) is in the design notes.

    This helper is deliberately zero-risk: only string literals, a single
    f-string interpolation of team_name (which is either None or a validated
    team name from generate_team_name), and a pure equality branch on
    frame_role. No file I/O, no subprocess, no classify call, no imports that
    might fail — and it never raises.

    Args:
        team_name: Team name captured before the exception, or None if the
                   exception fired before generate_team_name() ran.
        frame_role: Session role ("lead" / "teammate" / "unknown") captured
                    before the exception, or None if the exception fired before
                    the capture. Four cases: "teammate" selects the teammate
                    marker, None selects a role-free note that claims no role
                    and carries no bootstrap directive, and every other value
                    ("lead" and "unknown" today) selects the orchestrator
                    marker, with "unknown" also receiving the operator notice.
        source: The SessionStart source captured before the exception, or None.
                On "compact" the orchestrator prelude carries the teammate
                clause, as the normal compact directive does.

    Returns:
        Minimal additionalContext string suitable for the except-block
        safety net. Leads with the role-appropriate "YOUR PACT ROLE: <role>."
        marker at byte 0.
    """
    if frame_role == "teammate":
        # Teammate fail-open: byte-0 teammate marker + a minimal directive to
        # find assigned work. Deliberately NO Skill("repacted:bootstrap") (that is
        # the lead-only governance entrypoint) and NO team_name echo (in a
        # teammate frame team_name is the frame's OWN session-derived name, not
        # the lead's team — echoing it would mislead).
        return (
            'YOUR PACT ROLE: teammate.\n\n'
            'session_init partially failed — check systemMessage for details. '
            'Check TaskList for tasks assigned to you.'
        )
    prelude = (
        'YOUR PACT ROLE: orchestrator.\n\n'
        + (f'{COMPACTION_TEAMMATE_CLAUSE}\n\n' if source == "compact" else '')
        + 'Invoke Skill("repacted:bootstrap") immediately, without waiting for user input. '
        'Do this before anything else. '
        'Do not evaluate whether it is needed. '
        'You must invoke Skill("repacted:bootstrap") on every session start.'
    )
    # The cue for "unknown" and None: the design notes.
    if frame_role == "unknown":
        cue = f'\n\n{_UNKNOWN_ROLE_NOTICE}'
    elif frame_role is None:
        cue = (
            '\n\nNote: session_init failed before the session role was '
            'resolved, so this frame was not classified. If you are not '
            'driving PACT as the orchestrator, ignore the instructions above.'
        )
    else:
        cue = ''
    if team_name:
        return (
            f'{prelude}\n\n'
            f'Session team: `{team_name}` (session_init partially failed — '
            f'check systemMessage for details). '
            f'Run TaskList to check current state.{cue}'
        )
    return (
        f'{prelude}\n\n'
        'Session team: NOT GENERATED (session_init failed early — check '
        f'systemMessage for details). The platform auto-creates the session team.{cue}'
    )


def _persist_project_dir_env(project_dir: str) -> None:
    """Append `export CLAUDE_PROJECT_DIR=<value>` to the platform's CLAUDE_ENV_FILE.

    CLAUDE_ENV_FILE is the platform's sanctioned SessionStart channel for
    persisting env vars into subsequent Bash-tool environments; the plugin has
    no other route to make CLAUDE_PROJECT_DIR ambient for skill-spawned CLIs
    (the platform delivers it to hook processes only). Called from main() only
    when BOTH $CLAUDE_ENV_FILE and $CLAUDE_PROJECT_DIR are present — the
    both-present condition is the structural guard that keeps an env-absent
    frame's cwd-fallback value out of the export. The checks below are the
    defensive layer so the helper stays total when called directly in tests.

    Producer-side shlex.quote: the env file is sourced by a shell, so a path
    containing spaces or `$` must arrive quoted. Dedupe matches the TERMINATED
    full logical line against the raw file text (not splitlines() membership):
    a quoted value can itself contain a newline, and only the raw-text match
    self-matches on re-fire. An unterminated foreign last line gets its
    newline written first, so the export starts on its own line instead of
    gluing onto it.

    Never raises: SessionStart hot path (same total contract as the outer
    safety net). A failed append fails open to the pre-fix status quo —
    OSError AND UnicodeError (a non-UTF-8 env file) both fail open locally
    rather than escaping into main()'s outer net and degrading the frame.
    """
    if not os.environ.get("CLAUDE_PROJECT_DIR"):
        return
    _append_env_export("CLAUDE_PROJECT_DIR", project_dir)


def _persist_plugin_data_env() -> None:
    """Append `export REPACTED_PLUGIN_DATA=<$CLAUDE_PLUGIN_DATA>` to CLAUDE_ENV_FILE.

    The harness gives hooks CLAUDE_PLUGIN_DATA but the Bash tool none, so under
    `claude -p --plugin-dir` (hooks: <name>-inline) tcount and the other bin tools
    fell back to <name>-<marketplace> and wrote a second run ledger.
    shared.plugin_paths.plugin_data_dir() reads this export after CLAUDE_PLUGIN_DATA.
    Our own name, not CLAUDE_PLUGIN_DATA: that one would reach every other plugin's
    Bash tools too. Shown live: the export reaches the Bash tool and a subagent's Bash
    under `claude -p`. Only an absolute value is exported; same contract as above.
    """
    value = os.environ.get("CLAUDE_PLUGIN_DATA")
    if value:
        _append_env_export("REPACTED_PLUGIN_DATA", value)


def _append_env_export(name: str, value: str) -> None:
    """Append `export NAME=<quoted value>` to $CLAUDE_ENV_FILE once; never raises."""
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if not env_file or not os.path.isabs(value):
        return
    line = f"export {name}={shlex.quote(value)}"
    try:
        try:
            existing = Path(env_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            existing = ""
        if f"{line}\n" in existing:
            return
        payload = line + "\n"
        if existing and not existing.endswith("\n"):
            payload = "\n" + payload
        with open(env_file, "a", encoding="utf-8") as fh:
            fh.write(payload)
    except (OSError, UnicodeError):
        pass


def _record_worktree_identity(session_id: str, project_dir: str) -> None:
    """Record which repository this session's linked worktree belongs to.

    Writes `<session_dir>/worktree-identity.json` when `project_dir` lies inside
    a linked worktree (its git dir and common dir differ), including a
    subdirectory of one. The working-memory write guard reads it back once that
    worktree is removed and git can no longer say which repository the declared
    directory was in. Runs for every role, so a separate-process teammate
    records its own session.

    Fail-open: any error leaves no record.
    """
    try:
        directory = Path(project_dir)
        if not directory.is_dir():
            return
        git_dir = _rev_parse_path(directory, "--git-dir")
        common_dir = _rev_parse_path(directory, "--git-common-dir")
        if git_dir is None or common_dir is None or git_dir == common_dir:
            return
        worktree = _rev_parse_path(directory, "--show-toplevel")
        if worktree is None:
            return
        record = {
            "session_id": session_id,
            "declared": os.path.realpath(project_dir),
            "worktree": str(worktree),
            "common_dir": str(common_dir),
        }
        state_file.write_text(
            build_session_path(project_slug(project_dir), session_id)
            / WORKTREE_IDENTITY_FILE,
            json.dumps(record),
            root=get_claude_config_dir() / "pact-sessions",
        )
    except Exception:
        return


NOTICE_MARKER = "unattended-notice.told"


def _effective_mode() -> str:
    try:
        from shared.teammate_mode import resolve_effective_teammate_mode
        return str(resolve_effective_teammate_mode())
    except Exception:  # noqa: BLE001 — an unreadable mode is its own value
        return "unknown"


def notice_told(input_data: dict, project_dir: str) -> bool:
    """True when this session's marker records the notice for the current mode. Never raises."""
    try:
        session_id = input_data.get("session_id") if isinstance(input_data, dict) else None
        if not isinstance(session_id, str) or not session_id:
            return False
        marker = os.path.join(reconstruct_session_dir(project_dir, session_id), NOTICE_MARKER)
        with open(marker, encoding="utf-8") as handle:
            told = json.load(handle)
        return isinstance(told, dict) and told.get("mode") == _effective_mode()
    except Exception:  # noqa: BLE001 — no marker, or an unreadable one, means emit
        return False


def mark_notice_told(session_dir: str, system_messages: list) -> None:
    """Write the marker when this start emitted the notice and the session dir exists."""
    try:
        if session_dir and os.path.isdir(session_dir) and _INPROCESS_MODE_NOTICE in system_messages:
            with open(os.path.join(session_dir, NOTICE_MARKER), "w", encoding="utf-8") as handle:
                json.dump({"mode": _effective_mode()}, handle)
    except Exception:  # noqa: BLE001 — a marker that cannot be written repeats one line
        pass


def _teams_asked(project_dir: str) -> bool:
    """True unless the execution rules say subagents; an error counts as teams (emit)."""
    try:
        from shared import execution
        return execution.decide(cwd=project_dir or None)["mode"] == execution.TEAMS
    except Exception:  # noqa: BLE001 - uncertainty keeps the notice
        return True


def launch_notices(
    input_data: dict, project_dir: str, source: str, is_context_reset: bool,
    system_messages: list,
) -> None:
    """Steps 0-0d: the one-time tips and warnings, user-facing only."""
    # 0. Check required PACT dirs are in additionalDirectories (one-time tip)
    # Only check on fresh startup — resumed/compacted sessions already had the check
    if not is_context_reset:
        dirs_tip = check_additional_directories()
        if dirs_tip:
            system_messages.append(dirs_tip)

    # 0b. One-time in-process teammateMode notice (#864 Phase 1, ADDITIVE).
    # Warn that unattended runs may stall in in-process mode and recommend
    # `--teammate-mode tmux`. User-facing recommendation (the model cannot
    # relaunch itself) → system_messages channel, mirroring the step-0
    # additionalDirectories tip.
    #
    # WHEN: emit only on session-LAUNCH events (startup + resume). A
    # resumed session is the walk-away/unattended case worth re-warning,
    # and each launch fires SessionStart exactly once for that source — so
    # NO marker file is needed to stay once-per-launch. `compact` and
    # `clear` are mid-launch context-reset events that CAN re-fire within a
    # single launch; they are SUPPRESSED so the notice is never repeated.
    # An unrecognized source (normalized to "unknown") is also suppressed.
    #
    # Fail-safe: should_emit_inprocess_notice() is total (never raises) and
    # returns True on any read/parse uncertainty. The belt-and-suspenders
    # try/except ALSO emits on any unexpected escape (e.g. an import
    # failure) — "emit on uncertainty" is the protected direction — and it
    # MUST NOT raise out of the SessionStart hot path.
    #
    # ALLOWLIST MAINTENANCE: a future Claude Code launch-like source not in
    # this tuple normalizes to "unknown" (see source-normalization above)
    # and is SUPPRESSED — update this allowlist if such a launch source is
    # added upstream.
    # Once per session: `unattended-notice.told` in the session dir records the mode the
    # notice was given for; a resume of the same session with the same mode says nothing,
    # a changed mode says it again. main() writes the marker after the dir exists.
    # Teams only: a subagents session has no teammates to stall.
    if (source in ("startup", "resume") and _teams_asked(project_dir)
            and not notice_told(input_data, project_dir)):
        try:
            from shared.teammate_mode import should_emit_inprocess_notice
            if should_emit_inprocess_notice():
                system_messages.append(_INPROCESS_MODE_NOTICE)
        except Exception:  # noqa: BLE001 — fail-safe → emit; never block init
            system_messages.append(_INPROCESS_MODE_NOTICE)

    # 0c. Unknown-role startup warning. The lead-only writes in
    # steps 5a/5b/8 are gated behind is_lead below; a frame with NO
    # recognized role (no `--agent` flag, OR a present-but-unrecognized /
    # typo'd agent_type) silently performs none of them. Surface that so a
    # mis-launched orchestrator is observable. Conditional emission mirroring
    # the 0b notice shape (NOT a new numbered init step — keeps clear of the
    # module/main() docstring-parity convention). Launch events only
    # (startup/resume): a mid-launch compact/clear context-reset must not
    # re-fire it. The unknown-role decision (incl. the is_lead-first ordering,
    # the live specialist-registry check against env plugin_root, and the
    # namespace strip) lives in _should_warn_unknown_role — total (never raises),
    # so no try/except is needed at the call site.
    #
    # This literal is emitted at TWO other sites: the unknown-role limb of
    # the frame-role gate below, and _build_safety_net_context on the
    # exception path. The gating decision of each site, and its cause, are
    # recorded at that site.
    #
    # ONE CHANNEL PER FRAME: a frame with NO agent_type gets the notice
    # from the additionalContext limb below on every source, so this site
    # skips it; only the present-but-unrecognized agent_type (a typo),
    # which that limb never reaches, gets it here.
    if (source in ("startup", "resume")
            and classify_session_role(input_data) != "unknown"
            and _should_warn_unknown_role(input_data)):
        system_messages.append(_UNKNOWN_ROLE_NOTICE)

    # 0d. Coexistence warning: the upstream PACT plugin enabled beside
    # repacted runs a second copy of every gate. Read from the settings
    # files (the only deterministic signal). Launch events only, like 0c;
    # coexistence_warning never raises.
    if source in ("startup", "resume"):
        coexistence = coexistence_warning(project_dir)
        if coexistence:
            system_messages.append(coexistence)


def project_claude_md_upkeep(
    frame_role: str | None, context_parts: list, system_messages: list,
) -> None:
    """Steps 3 and 3b: create the project CLAUDE.md, or migrate it to the managed structure.

    Skipped for a teammate frame (upstream PACT #1650): a separate-process teammate starts
    inside a worktree, and its start planted a managed CLAUDE.md there that every project-file
    resolver then picked up. A lead, and an unknown frame (a lead launched without --agent),
    keep both steps.
    """
    if frame_role == "teammate":
        return
    # 3. Ensure project has CLAUDE.md with memory sections
    project_md_msg = ensure_project_memory_md()
    if project_md_msg:
        if "failed" in project_md_msg.lower() or "skipped" in project_md_msg.lower():
            system_messages.append(project_md_msg)
        else:
            context_parts.append(project_md_msg)

    # 3b. One-time migration: wrap existing project CLAUDE.md in
    # PACT_MANAGED boundary and add PACT_MEMORY markers (#404).
    # Runs after ensure_project_memory_md() so newly created files
    # already have the new structure.
    # Idempotent no-op when PACT_MANAGED_START marker is already present.
    migration_msg = migrate_to_managed_structure()
    if migration_msg:
        if "failed" in migration_msg.lower() or "skipped" in migration_msg.lower():
            system_messages.append(migration_msg)
        else:
            context_parts.append(migration_msg)


def refresh_environment(
    source: str, is_context_reset: bool, context_parts: list, system_messages: list,
    frame_role: str | None = None,
) -> None:
    """Steps 1-4c: plugin links, project CLAUDE.md, token hygiene, banner."""
    # 1. Refresh the plugin symlinks (enables @~/.claude/protocols/pact-plugin/
    # references, and resolves an unprefixed agent name to the CURRENT root).
    #
    # NO SOURCE GATE ON THE CALL. It ran behind `if not is_context_reset:` on
    # the assumption that a context reset inherits the links of the original
    # session. THAT PREDICATE ANSWERS EXISTENCE AND THE CALLER ASKS CURRENCY:
    # a link can be present and out of date at one moment. An install that
    # landed mid-launch left each link at the prior root until the next
    # launch, so a compact or a clear now repairs them.
    #
    # THE GATE MOVES TO THE NO-CHANGE MESSAGE, where it is the correct
    # predicate. That message answers "did the user see this before", a
    # repetition question, and the gate answers repetition correctly.
    #
    # SHADOWED routes like FAILED, to the user: a local agent file that
    # differs from the plugin's silently wins the bare-name dispatch. It is
    # computed on launch events only, so a compact or clear never repeats it.
    symlink_result = setup_plugin_symlinks(
        report_shadowed=source in ("startup", "resume")
    )
    symlink_lowered = symlink_result.lower() if symlink_result else ""
    if "failed" in symlink_lowered or "shadowed" in symlink_lowered:
        system_messages.append(symlink_result)
    elif symlink_result == SYMLINKS_VERIFIED_MESSAGE:
        # Nothing moved. Suppress on a context reset, so a quiet compact
        # gains no new output.
        if not is_context_reset:
            context_parts.append(symlink_result)
    elif symlink_result:
        # A LINK MOVED. Report it on each source, with the caveat beside it.
        context_parts.append(f"{symlink_result}. {_SYMLINK_REPOINT_NOTICE}")

    # 1b. The `repact` command on PATH (shared/repact_shim.py): written once, refreshed only
    # when the template changes, never over a file that is not ours. A created or removed shim
    # is told to the user; a failed write only on a launch, so a compact stays quiet.
    try:
        shim_note = ensure_shim(Path.home(), get_claude_config_dir(), os.environ)
    except Exception as err:  # noqa: BLE001 - never block session start on the shim
        shim_note = (f"repact command not written: {err.__class__.__name__}: {err}"
                     if source in ("startup", "resume") else None)
    if shim_note:
        system_messages.append(shim_note)

    # 3, 3b. The project CLAUDE.md: created or migrated by a non-teammate frame only.
    project_claude_md_upkeep(frame_role, context_parts, system_messages)

    # Step 3c retired in v4.2.15 — orphan-stripper sunset; see git log for context.

    # 3d. SUNSET BEFORE v5.0.0: strip the obsolete PACT_START/PACT_END
    # kernel block from ~/.claude/CLAUDE.md (v3.x kernel-in-home-dir
    # architecture; replaced by --agent flag in v4.0). Idempotent no-op
    # once stripped. "Migration skipped: ..." status routes to
    # systemMessages so the user sees malformed-marker warnings.
    kernel_strip_msg = strip_orphan_kernel_block()
    if kernel_strip_msg:
        if "failed" in kernel_strip_msg.lower() or "skipped" in kernel_strip_msg.lower():
            system_messages.append(kernel_strip_msg)
        else:
            context_parts.append(kernel_strip_msg)

    # 3e. Layer 3 (cross-cutting disk hygiene per #797): reap
    # unconsumed merge-authorization tokens older than
    # ORPHAN_TOKEN_MAX_AGE_SECONDS (12x TOKEN_TTL). Secondary trigger
    # — eager cleanup at session start so orphans don't accumulate
    # across long sessions where no dangerous-Bash command is run
    # (the primary trigger in merge_guard_pre.find_valid_token only
    # fires on dangerous-Bash precheck). Fail-open: cleanup_orphan_tokens
    # swallows all OSError paths; this try/except is belt-and-suspenders
    # for any TOKEN_DIR resolution flake.
    try:
        _cleanup_orphan_tokens(TOKEN_DIR)
    except Exception:
        pass  # Fail-open: never block session init for disk hygiene.

    # 4c. Surface plugin manifest diagnostic (#500). Tier-0 additionalContext —
    # total-function banner; always emits, even on read/parse failure.
    # Lets both team-lead and teammate context readers cross-reference
    # worktree edits against the resolved installed-cache root at a
    # glance. Helper is total: no conditional append, no try/except
    # wrapper at the call site.
    context_parts.append(format_plugin_banner())


def build_directives(
    team_name: str, session_dir: str, plugin_root: str, source: str,
) -> dict:
    """The placeholder sentence and the bootstrap directive pieces every
    source limb composes. Keys are the local names main() used."""
    # Build context message based on source (post-collapse: the team always
    # exists — the platform pre-creates exactly one team per session).
    # Session placeholder variable substitution instructions tell the orchestrator how to
    # replace {team_name}, {session_dir}, and {plugin_root} in command snippets.
    if session_dir:
        _substitutions = (
            f'Substitute before running commands: {{team_name}} = `{team_name}`, '
            f'{{session_dir}} = `{session_dir}`, {{plugin_root}} = `{plugin_root}`.'
        )
    else:
        _substitutions = (
            f'Substitute before running commands: {{team_name}} = `{team_name}`, '
            f'{{plugin_root}} = `{plugin_root}`. Session dir unavailable (session_id missing '
            f'from stdin): do not run commands that depend on {{session_dir}} until next clean start.'
        )
    # Single platform-managed directive for every session source. The
    # platform pre-creates exactly one team per session (Claude Code
    # v2.1.178+), so the team always exists by the time the orchestrator
    # acts — the directive's only job is to name the team and to block until
    # bootstrap completes, or say that it has. "(provided by the platform
    # for this session)" is correct for both fresh and resumed sessions, so
    # no team-existence discrimination is needed. The bootstrap-blocking
    # sentence aligns this guidance with the bootstrap_gate PreToolUse hook,
    # which mechanically blocks Edit/Write/Agent until the bootstrap marker
    # is stamped. It is dropped only on a compaction whose marker is already
    # stamped.
    # On compact, a teammate clause follows the marker line. An in-process
    # teammate's compaction arrives lead-shaped and receives this same
    # directive, and its system prompt, unlike its spawn prompt, survives
    # the compaction, so the clause keys on the system prompt. It tells a
    # teammate when to set aside "Do not evaluate whether it is needed."
    # The compact branch swaps the invoke sentences for _ran_sentence when
    # the bootstrap marker is already set; every other path uses this text.
    _compact_clause = (
        f'{COMPACTION_TEAMMATE_CLAUSE}\n\n'
        if source == "compact" else ''
    )
    _role_line = f'YOUR PACT ROLE: orchestrator.\n\n{_compact_clause}'
    _team_line = f'Your team is `{team_name}` (provided by the platform for this session). '
    _team_directive = (
        f'{_role_line}'
        f'Invoke Skill("repacted:bootstrap") immediately, without waiting for user input. '
        f'Do this before anything else. '
        f'Do not evaluate whether it is needed. '
        f'You must invoke Skill("repacted:bootstrap") on every session start.\n\n'
        f'{_team_line}'
        f'Do not read files, explore code, or respond to the user until bootstrap is complete. '
        f'{_substitutions}'
    )
    _ran_sentence = (
        'Bootstrap already ran in this session. '
        'Do not invoke Skill("repacted:bootstrap") again after this compaction.\n\n'
    )
    return {
        "_substitutions": _substitutions,
        "_role_line": _role_line,
        "_team_line": _team_line,
        "_team_directive": _team_directive,
        "_ran_sentence": _ran_sentence,
    }
