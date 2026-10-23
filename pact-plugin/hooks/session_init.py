#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/session_init.py
Summary: SessionStart hook that initializes PACT environment.
Used by: Claude Code settings.json SessionStart hook

Performs PACT environment initialization:
0. Checks if ~/.claude/teams is in additionalDirectories (emits setup tip if not configured)
0b. Emits a one-time in-process teammateMode notice recommending tmux for unattended runs (startup/resume only)
1. Creates plugin symlinks for @reference resolution
3. Ensures project CLAUDE.md exists with memory sections
3b. One-time migration: wraps existing project CLAUDE.md in PACT_MANAGED boundary (#404)
3d. Strips obsolete PACT_START/PACT_END kernel block from ~/.claude/CLAUDE.md (sunsets before v5.0.0)
5. Generates session-unique PACT team name and writes it to the session context (the platform pre-creates the team)
5b. Writes session resume info (resume command, team, timestamp) to project CLAUDE.md
6. Checks for in_progress Tasks (resumption context via Task integration)
7. Restores last session snapshot for cross-session continuity
8. Checks for paused or refreshed work from a previous /repacted:pause (or its checkpoint mode),
   and records session_resumption_surfaced when a resume claim surfaces

Note: Plan detection (scanning docs/plans/) was removed from session startup
to reduce latency. Plan detection is deferred to /repacted:orchestrate, which
checks docs/plans/ when it actually needs plan context.

Note: Memory-related initialization (dependency installation, embedding
migration, pending embedding catch-up) is now lazy-loaded on first memory
operation via pact-memory/scripts/memory_init.py. This reduces startup
cost for non-memory users.

Input: JSON from stdin with session context
Output: JSON with `hookSpecificOutput.additionalContext` for status
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

# main() runs the frame phases; the code lives in four modules (A4b):
#   shared/session_init_common.py   notices, environment, directives, safety net
#   shared/session_init_lead.py     runtime config, context, ladder, steps 5a-9
#   shared/session_init_teammate.py separate-process teammate peer context
#   shared/session_init_resume.py   previous session, summaries, compact limb
from shared.task_utils import get_task_list
from shared.session_state import SESSION_ID_CONTROL_CHARS_RE
from shared.pact_context import (
    _is_unknown_or_missing_session,
    classify_session_role,
    generate_team_name,
    get_session_dir,
    is_lead,
)
from shared.failure_log import append_failure
from shared.team_name import session_team_name
from shared import parts_ledger
from shared.session_init_common import (
    _build_safety_net_context,
    _persist_plugin_data_env,
    _persist_project_dir_env,
    _record_worktree_identity,
    build_directives,
    launch_notices,
    mark_notice_told,
    refresh_environment,
)
from shared.session_init_lead import (
    migrate_on_resume,
    establish_session_context,
    finish_session_start,
    lead_directive,
    record_session_start,
    runtime_config,
    settle_execution,
)
from shared.session_init_resume import _resume_own_summary_clause, clear_stale_state
from shared.session_init_teammate import inject_peer_context

# Re-exported: tests import these names from session_init.
from shared.session_init_common import (  # noqa: F401
    _INPROCESS_MODE_NOTICE,
    _SECRETARY_ON_DEMAND_CLAUSE,
    _SYMLINK_REPOINT_NOTICE,
    _UNKNOWN_ROLE_NOTICE,
    _should_warn_unknown_role,
    check_additional_directories,
)
from shared.session_init_lead import (  # noqa: F401
    _PACT_RUNTIME_CONFIG_LABELS,
    check_settings_well_formed,
    format_pact_runtime_config,
    unattended_context,
)
from shared.session_init_resume import (  # noqa: F401
    _ARCHIVE_STAMP_SHAPE_RE,
    _FIRST_SURFACE_CONSUMING_SOURCES,
    _ROOT_DRAINED_SUMMARY_PREFIX,
    _SESSION_STARTED_RE,
    _adopt_old_slug_session_dir,
    _archive_own_dir_stale_summary,
    _archive_stale_compact_summary,
    _bootstrap_already_ran,
    _clear_bootstrap_marker,
    _extract_prev_session_dir,
    _extract_session_started,
    _kept_started_at,
    _kept_team,
    _latest_consuming_start_ts,
    _settle_staged_summaries,
    _stale_summary_destination,
    _validate_under_pact_sessions,
)

# Render-hostile characters that, present anywhere in a session_id, render
# the id unsafe for use in single-line textual contexts like the CLAUDE.md
# Resume line. Covers C0 controls (0x00-0x1f, includes \n 0x0a, \r 0x0d),
# DEL (0x7f), NEL (U+0085), LINE SEPARATOR (U+2028), and PARAGRAPH
# SEPARATOR (U+2029) — every character `str.splitlines()` or an LLM
# tokenizer may treat as a line break. A crafted id containing any of
# these (e.g. "\n- Team: malicious") would break out of the Resume line
# and forge a teammate-routing line under the session-managed block,
# causing the next session_init to read a corrupted Resume payload.
# Symmetric with `shared.session_state._RENDER_STRIP_RE` — asymmetric
# strip sets across interpolation sinks become the attacker's entry point.
_SESSION_ID_CONTROL_CHARS_RE = SESSION_ID_CONTROL_CHARS_RE

# _is_unknown_or_missing_session — the single canonical session-id validity
# predicate — now lives in shared.pact_context (imported above), where the
# context self-heal gate consumes it alongside this module's persistence and
# CLAUDE.md-write gates. One definition, three call sites: the gates can
# never drift.


def start_execution(session_dir: str, project_dir: str, plugin_root: str) -> str:
    """Record the lead's execution mode; in subagents mode also write the bootstrap marker."""
    mode = settle_execution(session_dir, project_dir)
    if mode == "subagents":
        try:
            import bootstrap_marker_writer
            bootstrap_marker_writer.write_at_session_start(Path(session_dir), plugin_root)
        except BaseException:  # noqa: BLE001 - its load-failure path exits; keep init alive
            pass
    return mode


def main():
    """
    Main entry point for the SessionStart hook.

    Performs PACT environment initialization:
    0. Checks if ~/.claude/teams is in additionalDirectories (emits setup tip if not configured)
    0b. Emits a one-time in-process teammateMode notice recommending tmux for unattended runs (startup/resume only)
    1. Creates plugin symlinks for @reference resolution
    3. Ensures project CLAUDE.md exists with memory sections
    3b. One-time migration: wraps existing project CLAUDE.md in PACT_MANAGED boundary (#404)
    3d. Strips obsolete PACT_START/PACT_END kernel block from ~/.claude/CLAUDE.md (sunsets before v5.0.0)
    5. Generates session-unique PACT team name and writes it to the session context (the platform pre-creates the team)
    5b. Writes session resume info (resume command, team, timestamp) to project CLAUDE.md
    6. Checks for in_progress Tasks (resumption context via Task integration)
    7. Restores last session snapshot for cross-session continuity
    8. Checks for paused or refreshed work from a previous /repacted:pause (or its checkpoint mode)

    Note: Plan detection (scanning docs/plans/) was removed from session startup
    to reduce latency. Plan detection is deferred to /repacted:orchestrate, which
    checks docs/plans/ when it actually needs plan context.

    Note: Memory-related initialization (dependency installation, embedding
    migration, pending embedding catch-up) is now lazy-loaded on first memory
    operation via pact-memory/scripts/memory_init.py. This reduces startup
    cost for non-memory users.
    """
    # Pre-declare team_name so the outer except block can reference whatever
    # was captured before the exception fired. The assignment inside the try
    # at step 5 (team_name = generate_team_name(...)) rebinds this local; if
    # the exception fires before step 5, team_name stays None and the safety
    # net falls through to the "NOT GENERATED" branch.
    team_name = None
    # #888: role captured pre-assembly so the except-block safety net can pick
    # a role-appropriate "YOUR PACT ROLE:" marker. Stays None until the early
    # capture just after the stdin/source parse below; a frame that fails BEFORE
    # that capture keeps None, which selects the orchestrator marker — identical
    # to the pre-#888 behavior. That early-failure window is a KNOWN
    # no-regression default (a teammate failing before the capture is mis-marked
    # orchestrator), not a misroute introduced by this change.
    frame_role = None
    # The SessionStart source, captured with frame_role for the safety net.
    source = None
    # Track whether stdin JSON parsing failed, so the R3 malformed-stdin
    # gate below can distinguish "stdin was malformed JSON" from "stdin
    # parsed but session_id was missing/blank". Both paths fall through
    # to the same `unknown-{hex}` sentinel, but the failure_log ring
    # buffer captures them under different classifications so post-hoc
    # debugging can tell them apart.
    stdin_json_error: str | None = None
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            input_data = {}
            stdin_json_error = str(exc)

        # Resolve-once: the env value verbatim when the platform delivers it,
        # else the absolute cwd (os.getcwd() is always absolute and physical —
        # matching the platform's own resolved CLAUDE_PROJECT_DIR). This single
        # value feeds BOTH the session-context record and the env-file export,
        # so the exported == recorded invariant holds by construction. The
        # former "." default is gone: env-absent frames now record the absolute
        # cwd.
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        # Env-file export gate: only frames where the platform delivered BOTH
        # the env-file channel and the project dir append — an env-absent
        # frame never reaches the append, so the cwd fallback is never
        # exported (the structural guard).
        if os.environ.get("CLAUDE_ENV_FILE") and os.environ.get("CLAUDE_PROJECT_DIR"):
            _persist_project_dir_env(project_dir)
        _persist_plugin_data_env()
        context_parts = []
        system_messages = []
        parts_ledger.reset()

        # Detect session source: startup, resume, compact, clear
        # Default to "startup" if missing (backwards compat with older Claude Code).
        # Validate against the known set — an unrecognized source is surfaced
        # as "unknown" so it cannot inject arbitrary text into additionalContext.
        # isinstance(str) guard short-circuits the `in _VALID_SOURCES` test for
        # unhashable inputs (list, dict) that would otherwise raise TypeError,
        # bubble to the outer safety-net, and skip the session_start journal
        # write — breaking #414 R2's fail-open contract.
        _VALID_SOURCES = {"startup", "resume", "compact", "clear"}
        raw_source = input_data.get("source", "startup")
        source = (
            raw_source
            if isinstance(raw_source, str) and raw_source in _VALID_SOURCES
            else "unknown"
        )
        is_context_reset = source in ("compact", "clear")
        # Marker deletion uses a narrower guard: only user-initiated clear
        # triggers it. Compact is involuntary (auto-compaction under context
        # pressure) and the orchestrator is still mid-work — wiping the marker
        # on compact re-engages the bootstrap gate mid-task, blocking
        # Edit/Write/Agent when the orchestrator needs them most (#414).
        is_marker_reset = source == "clear"

        # Frame role, captured EARLY — right after the stdin/source parse, where
        # input_data is a PROVEN dict (it survived the .get() calls above; a
        # non-dict would have raised at raw_source and bubbled to the outer
        # safety net). classify_session_role does input_data.get(...) so it is
        # total only on a dict — capturing here, NEVER in the except, preserves
        # the safety net's never-raise contract. One capture serves these sites:
        #   - the lead-only runtime-config block below (steps 4d/4e);
        #   - the teammate peer-context branch (the `== "teammate"` gate, m3);
        #   - the role-aware exception safety net (_build_safety_net_context).
        # If the exception fires before this point, frame_role stays None and
        # the safety net emits the orchestrator marker — identical to prior
        # behavior, a KNOWN no-regression default, not a misroute.
        frame_role = classify_session_role(input_data)

        clear_stale_state(input_data, project_dir, source, is_marker_reset)

        launch_notices(input_data, project_dir, source, is_context_reset, system_messages)

        refresh_environment(source, is_context_reset, context_parts, system_messages, frame_role)
        parts_ledger.account("environment", context_parts)

        # 4d/4e. PACT runtime config: settings-health self-check + inject the
        # resolved LLM-consumed options. Lead-only via the fail-safe
        # `frame_role != "teammate"` idiom:
        # the consumers (peer-review, orchestrate/workflows.md §Scope Detection) are all
        # lead/orchestrator flows; an unknown/solo frame still receives it
        # (harmless if unconsumed). Not enumerated as a numbered step in the
        # docstring — like 4c, it is a context surfacing within the config
        # region, so the module<->main() docstring parity is untouched.
        if frame_role != "teammate":
            runtime_config(input_data, project_dir, source, context_parts, system_messages)
        parts_ledger.account("runtime_config", context_parts)

        # 5. Remind orchestrator to identify the session-unique PACT team (platform-provisioned)
        team_name = session_team_name(input_data, minted=generate_team_name(input_data))

        # 5a. Build the session context FIRST so get_session_dir() works for
        # subsequent journal writes. build_context_cache() populates the _cache
        # immediately (for every frame), enabling append_event() to derive the
        # journal path; persist_context() then writes the file (lead frames only).
        # Defensive substitution: the RA1+RG2 schema validator (commit 2d6448c)
        # rejects empty strings for str-typed required fields, so an empty
        # session_id would cause append_event() to silently drop the
        # session_start event. Substitute a non-empty per-process-unique
        # sentinel so downstream code paths that require a non-empty string
        # (e.g., team name derivation, log formatting) still function.
        # Reachable in production via the malformed-stdin fallback above
        # (input_data = {} on JSONDecodeError); latent otherwise because
        # Claude Code reliably provides session_id.
        #
        # R3 (MEDIUM, 2026-04-06): The sentinel must NOT touch disk. The
        # per-process unique suffix (`unknown-{token_hex(4)}`) means every
        # malformed-stdin session generates a unique path like
        # `~/.claude/pact-sessions/{slug}/unknown-a3f9b2c4/`. session_end's
        # cleanup_old_sessions filters by strict _UUID_PATTERN, which
        # "unknown-*" never matches — so these directories accumulate
        # indefinitely. Gate BOTH persistence call sites (the
        # build_context_cache/persist_context pair and append_event) on
        # session_id_was_missing to prevent the leak. The
        # existing CLAUDE.md guard at step 5b handles its own persistence.
        # The session_start journal anchor event is intentionally dropped on
        # the malformed-stdin path: without a valid session_id, we cannot
        # durably record the session, and creating an orphaned journal file
        # in an unreapable directory is worse than the missing anchor.
        #
        # DESIGN DECISION (2026-04-06, user-authorized): on the malformed-stdin
        # path, BOTH the journal session_start anchor AND the CLAUDE.md
        # Current Session block are intentionally skipped. This reverses the
        # earlier "Finding A" priority that preserved the anchor in the
        # journal for visibility. The reversal was authorized after the
        # trade-off was surfaced explicitly: R3 (silent unbounded disk leak
        # from the unreapable `unknown-{hex}/` directory) is a strictly worse
        # failure mode than Finding A (visible-in-stderr dropped anchor). The
        # two are mutually exclusive because append_event() is what creates
        # the leaked directory in the first place — preserving the anchor
        # IS what causes the leak. The dropped-anchor outcome is observable
        # via the stderr warning emitted below, so the loss of visibility
        # is bounded; the disk leak is not.
        raw_id = input_data.get("session_id")
        # Single canonical predicate (R-1+R-2): rejects None, non-strings,
        # empty strings, whitespace-only strings, and any "unknown-*" sentinel.
        # The CLAUDE.md write gate at step 5b consults the same helper so the
        # two predicates can never drift.
        session_id_was_missing = _is_unknown_or_missing_session(raw_id)
        if not session_id_was_missing:
            session_id = str(raw_id)
        else:
            session_id = f"unknown-{secrets.token_hex(4)}"
            # Issue #399: record this failure in the global ring buffer log
            # BEFORE emitting the stderr warning. The ring buffer is the
            # only observability surface that survives across sessions and
            # aggregates across both team-lead and teammate sessions — stderr
            # output from hooks is not visible to users, and the single-
            # instance safety net only reaches the team-lead's first-message
            # context. Defense in depth: append_failure fails-open
            # internally, but we also wrap the call in its own try/except
            # so a future refactor weakening that contract cannot crash
            # session_init. The classification distinguishes the three
            # main failure kinds so post-hoc analysis can see the shape
            # of the problem.
            # Classification ladder — order matters. Each branch isolates a
            # distinct upstream failure kind so post-hoc diagnosis can tell
            # them apart. The ladder mirrors the branches of
            # _is_unknown_or_missing_session() plus the malformed_json case
            # that funnels through the JSONDecodeError fallback at the top
            # of main(). The control_char_session_id branch must run BEFORE
            # the sentinel check because an attacker could craft an id with
            # an embedded newline + injected directive that would otherwise
            # be classified as a plain sentinel, losing the signal that an
            # injection was attempted.
            if stdin_json_error is not None:
                _classification = "malformed_json"
                _error_detail = stdin_json_error
            elif raw_id is None:
                _classification = "missing_session_id"
                _error_detail = "session_id key absent from stdin payload"
            elif not isinstance(raw_id, str):
                _classification = "non_string_session_id"
                _error_detail = f"session_id was {type(raw_id).__name__}: {raw_id!r}"
            elif not raw_id.strip():
                _classification = "empty_session_id"
                _error_detail = f"session_id was empty/whitespace: {raw_id!r}"
            elif _SESSION_ID_CONTROL_CHARS_RE.search(raw_id):
                # Newlines, NUL, BEL, ESC, DEL, etc. anywhere in the id.
                # Flags the CLAUDE.md routing-marker injection attack class
                # explicitly so failure_log entries identify the smell.
                _classification = "control_char_session_id"
                _error_detail = f"session_id contained C0/DEL control char: {raw_id!r}"
            elif raw_id.strip().startswith("unknown-"):
                # Matches _is_unknown_or_missing_session which uses
                # "unknown-" (with hyphen) to match only the sentinel format
                # "unknown-{hex}" without false-positiving on unrelated ids.
                _classification = "sentinel_session_id"
                _error_detail = f"session_id already an unknown-* sentinel: {raw_id!r}"
            else:
                # Terminal catchall — reached only if a future change to
                # _is_unknown_or_missing_session adds a rejection branch
                # that this ladder does not cover yet.
                _classification = "other"
                _error_detail = f"session_id rejected by predicate: {raw_id!r}"
            try:
                append_failure(
                    classification=_classification,
                    error=_error_detail,
                    cwd=os.getcwd(),
                    source=source,
                )
            except Exception:
                # Belt-and-suspenders: append_failure already fails-open
                # internally, but the R3 gate MUST NEVER raise. Swallow
                # any exception that escapes the ring buffer logic.
                pass
            print(
                f"session_init: missing session_id in stdin payload; "
                f"using fallback {session_id} (no disk persistence)",
                file=sys.stderr,
            )
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")

        # #989 detect-and-align: see establish_session_context.

        if not session_id_was_missing:
            _record_worktree_identity(session_id, project_dir)

        # Lead-role gate (#877). is_lead is total (never raises) and reads only
        # the harness-set agent_type. Computed once and reused for both Class-A
        # writes below so the disk-write split and the journal-anchor gate share
        # one verdict.
        frame_is_lead = is_lead(input_data)
        # Read before establish_session_context replaces it.
        prior_team = (
            _kept_team(session_id, project_dir)
            if source == "resume" and not session_id_was_missing else None
        )
        team_name = establish_session_context(
            team_name, session_id, session_id_was_missing,
            project_dir, plugin_root, source, frame_is_lead,
        )
        # Resolve session_dir early so substitution instructions can include it.
        # get_session_dir() works here because build_context_cache() populated _cache above.
        # Suppress session_dir for the unknown-* sentinel so the literal
        # `.../unknown-xxxx/` path never leaks into the substitution instructions
        # block — otherwise the orchestrator would obediently mkdir that path
        # for any command that uses {session_dir}, bypassing the CLAUDE.md guard
        # below.
        # HOISTED above the session_start journal append below (F-ARCH-1): the
        # resume-limb summary probe must read the journal BEFORE this run's own
        # anchor lands in it — see the probe comment right after the append.
        session_dir = get_session_dir() if not session_id_was_missing else ""

        # Resume-limb summary probe (first-surface gate, F-ARCH-1): computed
        # HERE — after session_dir resolves, BEFORE the session_start journal
        # append below — as a STRUCTURAL INVARIANT: the journal never contains
        # the current run's anchor at probe time. A post-append probe would
        # compare this run's own anchor ts (now) against every candidate mtime
        # (always older) and suppress permanently; the gate would defeat
        # itself on the very first resume. Gated on source == "resume" only:
        # the clause is consumed by exactly that limb, and other sources pay
        # nothing for the probe.
        resume_summary_clause = (
            _resume_own_summary_clause(session_dir)
            if source == "resume"
            else ""
        )

        record_session_start(
            session_id, session_id_was_missing, team_name, project_dir, source,
            frame_is_lead,
        )

        # After the session_start anchor, so the migration's journal rows belong to this
        # life and not to the one that ended.
        if frame_role != "teammate":
            earlier_note = migrate_on_resume(prior_team, team_name, frame_is_lead)
            if earlier_note:
                context_parts.append(earlier_note)
            parts_ledger.account("earlier_team", context_parts)

        directives = build_directives(team_name, session_dir, plugin_root, source)

        # Hoist get_task_list() above the source-branch dispatch so both the
        # compact-branch checkpoint (below) and step 6 resumption (line ~885)
        # consume the SAME `tasks` variable. Before hoisting, the two call
        # sites produced an asymmetric fail-open shape: a raise at the
        # compact-branch site fell through to _build_safety_net_context
        # (directive only, no checkpoint); a raise at step 6 left directive +
        # checkpoint + no-resumption. Single call site means identical
        # fallback shape on either failure.
        #
        # Fail-open layering (defense in depth):
        #   1. Primary: get_task_list() has its own internal try/except
        #      (shared/task_utils.py:52-61) that returns None on any
        #      filesystem or JSON parse error. Callers never see a raise
        #      from a corrupted tasks dir.
        #   2. Belt-and-suspenders: main()'s outer try/except catches
        #      unexpected exceptions in the downstream checkpoint-
        #      construction helpers (find_feature_task, find_current_phase,
        #      find_active_agents, find_blockers, build_post_compaction_
        #      checkpoint) — these do NOT have internal exception guards.
        #      A raise there drops the whole compact branch and falls
        #      through to _build_safety_net_context, which still carries
        #      the bootstrap directive.
        tasks = get_task_list()

        # A separate-process teammate gets the peer-context body; a lead and an
        # unknown-role frame get the bootstrap directive ladder.
        if frame_role == "teammate":  # m3: reuse the role captured at the early seam (was a recompute)
            inject_peer_context(input_data, team_name, context_parts)
            parts_ledger.account("peer_context", context_parts)
        else:
            lead_directive(
                context_parts, tasks, source, frame_role, frame_is_lead,
                session_dir, plugin_root, resume_summary_clause, directives,
            )
            parts_ledger.account("directive", context_parts)

        finish_session_start(
            context_parts, system_messages, tasks, source, project_dir,
            session_id, team_name, session_dir, plugin_root, frame_is_lead,
        )
        parts_ledger.account("finish", context_parts)
        # The execution mode, once per session, after the directive was built from the
        # marker state this start found. In subagents mode
        # the Teams-only marker writer never runs, so the bootstrap marker is written here.
        if frame_is_lead and session_dir:
            start_execution(session_dir, project_dir, plugin_root)
        parts_ledger.record(session_dir, source, frame_role or "lead", context_parts, system_messages)
        mark_notice_told(session_dir, system_messages)

        # Build output
        output = {}

        if context_parts or system_messages:
            # hookEventName is required by the harness; missing it silently fails open
            output["hookSpecificOutput"] = {
                "hookEventName": "SessionStart",
                "additionalContext": " | ".join(context_parts) if context_parts else "Success"
            }

        if system_messages:
            output["systemMessage"] = " | ".join(system_messages)

        # context_parts is guaranteed non-empty on the happy path: the
        # team-identification instruction is always insert(0, ...)'d
        # earlier in main(), so `output["hookSpecificOutput"]` is always
        # populated by this point. The exception safety net at the bottom
        # of main() builds its own output and never falls through here.
        print(json.dumps(output))

        sys.exit(0)

    except Exception as e:
        # Safety net: even when main() throws before building the normal
        # output, the team-lead still needs the governance delivery chain.
        # Emit a minimal PACT ROLE marker + bootstrap skill directive in
        # additionalContext, alongside the error in systemMessage. Claude
        # Code's hook-output schema supports both fields in the same JSON.
        print(f"Hook warning (session_init): {str(e)[:200]}", file=sys.stderr)
        safety_net_context = _build_safety_net_context(team_name, frame_role, source)
        # hookEventName is required by the harness; missing it silently fails open
        output = {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": safety_net_context,
            },
            "systemMessage": f"PACT hook warning (session_init): {str(e)[:100]}",
        }
        print(json.dumps(output))
        sys.exit(0)


if __name__ == "__main__":
    main()
