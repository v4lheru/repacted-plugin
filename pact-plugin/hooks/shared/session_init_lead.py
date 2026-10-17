"""
Location: pact-plugin/hooks/shared/session_init_lead.py
Summary: SessionStart for the lead (and the unknown-role primary frame): the
    runtime config and unattended block, the session context and team
    alignment, the session_start journal anchor, the bootstrap directive
    ladder, the Current Session block, resumption, the resume claim and the
    hook briefing.
Used by: hooks/session_init.py

Moved out of session_init.py unchanged; the step numbers in the comments are
the ones main()'s docstring lists.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Optional

from shared import parts_ledger, task_migration, unattended
from shared.pact_config import llm_options
from shared.pact_context import (
    _is_unknown_or_missing_session,
    _resolve_aligned_team_name,
    build_context_cache,
    compaction_keeps_team,
    persist_context,
    reconstruct_session_dir,
    resume_adopts_team,
    sessionstart_team_is_own,
)
from shared.paths import get_claude_config_dir
from shared.plugin_manifest import plugin_version
from shared.session_init_common import _SECRETARY_ON_DEMAND_CLAUSE, _UNKNOWN_ROLE_NOTICE
from shared.session_init_resume import (
    _extract_prev_session_dir,
    _extract_session_started,
    _kept_started_at,
    _kept_team,
    compact_directive,
)
from shared.session_journal import append_event, make_event, read_events
from shared.session_resume import (
    build_session_briefing,
    check_resume_state,
    check_resumption_context,
    check_unpaused_exit,
    restore_last_session,
    start_working_memory_rebuild,
    update_session_info,
    working_memory_rebuild_outcome,
)


# ─── PACT Runtime Config injection (SessionStart LLM bridge) ────────────────

# Fixed label table for the injected "PACT Runtime Config" block. This is the
# ONLY source of option names + human labels emitted into the block: the
# composer never interpolates a raw env value or a raw dict key (F1
# prompt-injection defense, mirroring the `source` canonicalization in main()
# that keeps untrusted stdin text out of additionalContext). Tuple order is the
# block's display order.
_PACT_RUNTIME_CONFIG_LABELS = (
    ("PACT_PR_GREEDY_FIX", "PR greedy-fix"),
    ("PACT_AUTONOMOUS_SCOPE_DETECTION", "Autonomous scope detection"),
)


def format_pact_runtime_config(options: dict) -> str:
    """Compose the "PACT Runtime Config" additionalContext block.

    Takes the RESOLVED options dict from ``pact_config.llm_options()`` (bool
    values) and emits a multi-line block whose ONLY variable content is a
    per-option "ON"/"OFF" derived from ``options.get(name) is True``. Option
    names and labels come from the fixed ``_PACT_RUNTIME_CONFIG_LABELS`` table,
    NEVER from the dict keys or from any env string — so a poisoned resolver
    value cannot inject text into the block (F1 canonical-value composition,
    the prompt-injection choke-point).

    The block LEADS with a newline so that, after main() joins ``context_parts``
    with ``" | "``, the ``## `` heading still lands at line-start and renders as
    a header rather than mid-line text.

    Consumers (peer-review.md, orchestrate.md / workflows.md §Scope Detection)
    pattern-match the literal heading; absence of the block == every option at
    its default (OFF).
    """
    lines = ["", "## PACT Runtime Config (resolved at session start)"]
    for env_name, label in _PACT_RUNTIME_CONFIG_LABELS:
        state = "ON" if options.get(env_name) is True else "OFF"
        lines.append(f"- {label}: {state} ({env_name})")
    return "\n".join(lines)


def unattended_context(session_id: object, project_dir: str, source: str) -> Optional[str]:
    """The unattended-mode block for additionalContext, or None while the mode is OFF.

    ON prints the mode and its three rules on every source, so a compaction
    states them again; OFF adds nothing. The flag in the session directory wins
    over REPACT_UNATTENDED (shared/unattended.py). One journal row when the
    environment turns the mode on (startup and resume only), and one when the
    flag cannot be read or parsed, which reads OFF.
    """
    session_dir = (
        reconstruct_session_dir(project_dir, session_id)
        if isinstance(session_id, str) and not _is_unknown_or_missing_session(session_id)
        else ""
    )
    mode = unattended.read_mode(session_dir)
    row = None
    if mode.cause in unattended.INVALID_CAUSES:
        row = make_event("unattended_mode", state="off", cause=mode.cause)
    elif mode.cause == "env" and source in ("startup", "resume"):
        row = make_event("unattended_mode", state="on", cause="env")
    if row and session_dir:
        append_event(row, session_dir=session_dir)
    return unattended.session_rules(mode.cause) if mode.on else None


def check_settings_well_formed() -> Optional[str]:
    """Return a warning if the user settings.json exists but is malformed JSON.

    Claude Code silently drops a malformed settings.json WHOLESALE in headless
    mode — taking the entire ``env`` block with it, so every PACT_* option
    persisted there is silently NOT applied. Surfacing the malformation tells
    the user why their env-block config had no effect. Returns None when the
    file is absent or valid. Total: never raises (it runs on the SessionStart
    hot path, where an uncaught exception would break bootstrap).
    """
    try:
        settings_path = get_claude_config_dir() / "settings.json"
        if not settings_path.exists():
            return None
        try:
            json.loads(settings_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return (
                f"PACT: {settings_path} is not valid JSON — Claude Code "
                "silently ignores a malformed settings.json (including its `env` "
                "block), so any PACT_* options set there are NOT applied. Fix the "
                "JSON syntax to restore them."
            )
        return None
    except Exception:  # noqa: BLE001 — total contract on the SessionStart hot path
        return None


def runtime_config(
    input_data: dict, project_dir: str, source: str, context_parts: list,
    system_messages: list,
) -> None:
    """Steps 4d-4f, for every frame that is not a teammate."""
    # 4d. Warn if settings.json is malformed — Claude Code drops it
    # WHOLESALE in headless mode, silently taking the `env` block (and
    # every PACT_* option) with it. User-facing config-health notice →
    # system_messages. Helper is total.
    settings_warn = check_settings_well_formed()
    if settings_warn:
        system_messages.append(settings_warn)
    # 4e. Inject the resolved "PACT Runtime Config" block so markdown
    # flows (which cannot read env vars) can honor the LLM-consumed
    # options. Tier-0 additionalContext, appended (a config statement,
    # not a top-priority alert). Fail-open: any failure emits no
    # directive — and absence of the block == every option at its
    # default (OFF), the same safe state the consumers already assume.
    try:
        context_parts.append(format_pact_runtime_config(llm_options()))
    except Exception:  # noqa: BLE001 — fail-open: no block == all OFF
        pass
    # 4f. Unattended mode: the mode and its three rules while the switch
    # is ON, nothing while it is OFF. Fail-open: an error adds nothing,
    # and the unattended_gate still refuses the question tool.
    try:
        unattended_block = unattended_context(
            input_data.get("session_id", ""), project_dir, source
        )
        if unattended_block:
            parts_ledger.note("unattended", unattended_block)
            context_parts.append(unattended_block)
    except Exception:  # noqa: BLE001 — fail-open: no block
        pass


def team_born_at_this_resume() -> Optional[str]:
    """The one team whose lead joined at this resume and that no peer session can own
    (resume_adopts_team), else None.

    The host mints a team on every plain resume. After a crash the earlier life's config still
    stands and names this session, so the resolver's identity match keeps the old name and its
    census stays off. Two such teams prove nothing. Never raises.
    """
    try:
        born = [entry.name for entry in sorted((get_claude_config_dir() / "teams").iterdir())
                if resume_adopts_team(entry.name)]
    except Exception:  # noqa: BLE001 - an unlistable teams dir adopts nothing
        return None
    return born[0] if len(born) == 1 else None


def establish_session_context(
    team_name: str, session_id: str, session_id_was_missing: bool,
    project_dir: str, plugin_root: str, source: str, frame_is_lead: bool,
) -> str:
    """Step 5a: build the context cache, align the team, persist it for a lead.

    Returns the team name, aligned when the alignment proved it."""
    # Oscillation convergence (#989 detect-and-align). session_init writes
    # team_name to BOTH the context cache (below) and CLAUDE.md (step 5b)
    # on EVERY SessionStart, including compact/clear re-fires. The
    # bootstrap_marker_writer self-heals to the IDENTITY-MATCHED name per
    # prompt. If session_init kept writing the raw COMPUTED name while the
    # marker writer wrote the aligned name, the two would flip-flop forever
    # whenever they differ (the divergent full-UUID launch case). FIX:
    # resolve the aligned name at step 5a (right after the context cache
    # is built, below) and persist THAT, so both writers
    # converge on one value. The freshly-computed `team_name` is threaded
    # as the resolver's explicit `default` — critical for cold start: at
    # SessionStart the real team dir is ~38s unborn, so the identity match
    # MISSES and the resolver returns the default; on a first-ever cold
    # start the persisted context is empty, so the computed name (not "")
    # must be the default. Gated on a valid session_id (the sentinel path
    # skips all persistence anyway). Once the dir is born, a later
    # SessionStart (or the marker writer) resolves the aligned name and
    # both writers agree → the per-prompt write-back becomes a true no-op.
    if not session_id_was_missing:
        try:
            # SEAM (#877): compose the two halves directly. ALWAYS build +
            # cache (every frame gets the in-process context so
            # get_session_dir() and append_event's path-resolution behave
            # identically), then persist to disk ONLY for a lead frame so a
            # teammate/plain frame never clobbers the lead's on-disk
            # session-context file (or creates a phantom session dir).
            # build_context_cache is the sole owner of _cache; persist_context
            # is the is_lead-gated best-effort disk side-effect. See the
            # build_context_cache / persist_context docstrings.
            # A compaction is not a session start, so it keeps the recorded
            # start. That also leaves the file byte-identical when an
            # in-process teammate's compaction, which arrives lead-shaped,
            # rewrites it.
            _started_at = (
                _kept_started_at(session_id, project_dir)
                if source == "compact" else None
            )
            # A compaction does not change the team either. Without this
            # each compaction wrote the computed session-<id8> back over the
            # team a resumed session had adopted.
            _kept = _kept_team(session_id, project_dir) if source == "compact" else None
            _ctx_result = build_context_cache(
                team_name, session_id, project_dir, plugin_root,
                started_at=_started_at,
            )
            if not (_kept and compaction_keeps_team(_kept)):
                _kept = None
            # Align AFTER the cache exists: the resume census reads this session's journal
            # (its lived-session witness and end marker) through get_session_dir(), which
            # derives from the cache. Aligning before it announced the stale
            # session-<old id8> while the re-provisioned team already existed.
            # A census winner is announced only when it is provably this
            # session's own team, never a live peer's (review F1). A kept
            # team is the resolver's default: its config exists, so the
            # census stays off and only an identity match outranks it.
            _aligned = _resolve_aligned_team_name(session_id, default=_kept or team_name)
            # A live peer session made sessionstart_team_is_own refuse every census
            # winner, so a resume persisted the computed name for its whole life
            #. The compaction keep-rule's ownership proof, with this
            # resume as the start of the life, refuses only a team the peer owns.
            if not (_aligned != team_name and (
                _aligned == _kept or sessionstart_team_is_own(_aligned, session_id)
                or (source == "resume" and resume_adopts_team(_aligned))
            )):
                # The resolver named no team this resume can prove: look for the one the
                # host minted for it. The resolver then keeps it over the old identity match.
                _aligned = team_born_at_this_resume() if source == "resume" else None
            if _aligned and _aligned != team_name:
                team_name = _aligned
                _ctx_result = build_context_cache(
                    team_name, session_id, project_dir, plugin_root,
                    started_at=_started_at,
                )
            if frame_is_lead and _ctx_result is not None:
                persist_context(*_ctx_result)
        except Exception as e:
            # Fail-open: context file is best-effort; hooks fall back to empty strings
            print(f"session_init: could not write context file: {e}", file=sys.stderr)
    return team_name


def emit_migration_row(row: dict) -> bool:
    """TEMPORARY (task-system slice 1, unit U5): writes the migration's journal row with the
    existing writer. The task_event composer replaces this function, and only this one;
    task_migration takes the writer as a parameter and never imports the journal."""
    return bool(append_event(make_event("task_event", **row)))


def read_migration_rows() -> list:
    """The `migrated` rows emit_migration_row wrote into this session's journal: the
    migration's only proof that an earlier run copied a task (task_migration._earlier_run)."""
    return [row for row in read_events("task_event") if row.get("kind") == "migrated"]


def migrate_on_resume(prior_team: object, team_name: str, frame_is_lead: bool) -> Optional[str]:
    """Move the earlier team's open tasks into this team (shared/task_migration) and return
    the note for the lead. None when the frame is not the lead's: only the lead's
    SessionStart writes into the task store. When the team did not change and was not born
    at this resume, the plugin is still on the earlier life's team and the note says so
    (unplaced_note). Never raises.
    """
    try:
        if not frame_is_lead or not isinstance(prior_team, str) or not prior_team:
            return None
        if prior_team == team_name:
            return None if resume_adopts_team(team_name) else task_migration.unplaced_note(team_name)
        return task_migration.resume_tasks_note(
            task_migration.migrate_for_session(prior_team, team_name, emit_migration_row,
                                               copied=read_migration_rows))
    except Exception:  # noqa: BLE001 - never a reason to fail SessionStart
        return None


def record_session_start(
    session_id: str, session_id_was_missing: bool, team_name: str,
    project_dir: str, source: str, frame_is_lead: bool,
) -> None:
    """The lead's session_start journal anchor."""
    if not session_id_was_missing:
        # Write session_start event to journal (after build_context_cache so
        # path is available). Lead-only (#877): the journal session_start anchor is a
        # lead-only write — a teammate/plain frame would append a phantom
        # anchor to (or create) a session journal it does not own.
        # `source` is the already-normalized value from the `_VALID_SOURCES`
        # check above — one of {startup, resume, compact, clear, unknown}.
        # Persisting it here gives downstream triage direct attribution for
        # marker-wipe and other source-conditioned behavior, instead of
        # forcing triangulation from timing clusters (#414 R2).
        if frame_is_lead:
            append_event(
                make_event(
                    "session_start",
                    team=team_name,
                    session_id=session_id,
                    project_dir=project_dir,
                    worktree="",  # Not yet created at this point
                    source=source,
                    plugin_version=plugin_version(),
                ),
            )


def lead_directive(
    context_parts: list, tasks, source: str, frame_role, frame_is_lead: bool,
    session_dir: str, plugin_root: str, resume_summary_clause: str, directives: dict,
) -> None:
    """The bootstrap directive ladder for a lead or an unknown-role frame."""
    _team_directive = directives["_team_directive"]
    # "lead" AND "unknown" BOTH take this branch, and the "unknown"
    # half is the load-bearing part. DO NOT SPLIT IT OUT AGAIN WITHOUT
    # READING THIS. "unknown" means agent_type was ABSENT, which is a
    # non-PACT / no-`--agent` PRIMARY frame: an ordinary user who typed
    # plain `claude`. Withholding the ladder from that frame withholds
    # the bootstrap directive, so the bootstrap marker is never
    # stamped, so bootstrap_gate (PreToolUse, no matcher key, every
    # tool call) denies Edit, Write and Agent. THE USER READS THAT AS A
    # TOTAL TOOL-LOAD FAILURE, AND IT WAS REPORTED FROM THE FIELD. The
    # `clear` source is the worst cell: `is_marker_reset` above ERASES
    # the marker on that path, so a `clear` frame with no ladder loses
    # the marker AND the instruction that would rebuild it.
    #
    # A PRIOR SUPPRESSION HERE CITED A CENSUS THAT DID NOT MEASURE
    # THIS POPULATION. Re-measured: of 166 files matching
    # subagents/*.jsonl for one team session, 13 carry a SessionStart
    # record, holding 70 records between them. All 70 have
    # type == "attachment" and carry the LEAD session id, and the set
    # of distinct session ids across the 70 has exactly ONE member.
    # They are the LEAD's own hook output, attached by the platform
    # into the transcripts of the live sidechains. Those frames
    # classify "lead", so a gate keyed on "unknown" cannot change one
    # byte of them, and 150 of the 166 files carry no SessionStart
    # record at all. NO SUBAGENT FRAME REACHES THIS HOOK.
    #
    # AND THE ERROR THAT LET IT SHIP WAS A MODEL, NOT A MISSING CHECK,
    # WHICH IS WHY REVIEW DID NOT CATCH IT. The comment above this
    # module's _UNKNOWN_ROLE_NOTICE describes the no-`--agent` frame as
    # an operator "who MEANT to launch the orchestrator and forgot the
    # flag", and the emitted notice says the same. THAT NAMES THE FRAME
    # AFTER A MISTAKE. Nobody wrote down that it is also the ORDINARY
    # way a user starts Claude Code, so a branch that withheld the
    # ladder from it read as harmless to each reviewer, because each
    # reviewer held the same model. A missing check is caught by
    # reading the branch. A wrong model is not.
    #
    # The team always exists (the platform pre-creates it), so the
    # directive is source-agnostic; the per-source branches differ only
    # in the recovery/state guidance appended after it.
    if source == "compact":
        compact_directive(
            context_parts, tasks, frame_is_lead, session_dir, plugin_root,
            **directives,
        )
    elif source == "clear":
        # Context cleared via /clear: no compact-summary, but team and tasks survive
        context_parts.insert(0, (
            f'{_team_directive} '
            f'CONTEXT CLEARED: Your context was cleared via /clear. '
            f'State recovery: '
            f'(1) TaskList for current tasks, '
            f'(2) read the task files of in-progress tasks (TaskGet does not surface metadata). '
            f'{_SECRETARY_ON_DEMAND_CLAUSE}'
        ))
    elif source == "resume":
        # Normal resume: model retains context, team exists. A compact
        # summary from an earlier compaction of this session may sit
        # in the session's own dir — usually archived-in-place, since
        # the non-compact clears above run BEFORE this limb renders.
        # The pointer sentence is PRECOMPUTED above (probe hoisted
        # before the session_start journal append — the F-ARCH-1
        # first-surface invariant) and is PROBE-CONDITIONAL (empty
        # string when no unsuppressed summary exists), so the limb
        # stays one contiguous block.
        context_parts.insert(0, (
            f'{_team_directive} '
            f'Check session journal for paused state from /repacted:pause.'
            f'{resume_summary_clause}'
        ))
    elif source == "startup":
        # Fresh session: bare directive (no extra recovery guidance)
        context_parts.insert(0, _team_directive)
    else:
        # Unrecognized lifecycle source value. The team still exists
        # (platform pre-creates it), so there is no "no team" case to
        # handle — only an observability concern: surface the malformed
        # `source` in additionalContext AND on stderr so debug logs
        # capture the unexpected-stdin signal.
        print(
            f"session_init: unknown source value: {source!r}",
            file=sys.stderr,
        )
        context_parts.insert(0, (
            f'{_team_directive} '
            f'Note: unrecognized session source "{source}". '
            f'Run TaskList to check current state.'
        ))

    # THE OPERATOR CUE, AND IT IS ADDITIVE. A frame with no agent_type
    # keeps the whole ladder above and ALSO gets told that no role was
    # recognized, so an operator who MEANT to launch the orchestrator
    # and forgot the flag still sees it. INDEX 1, immediately after the
    # ladder each source limb just wrote at index 0: the role message
    # must stay first, because this gate runs LATE and the banner and
    # the pin surfacings are already in context_parts. An append would
    # leave the cue below diagnostics a reader meets first, and an
    # insert at 0 would displace the marker the line-anchored readers
    # key on. NO SOURCE GATE: this limb writes to context_parts, which
    # a frame after a compact does not carry over from before, so a
    # source gate would remove the only copy that reader gets. The
    # sibling emission into system_messages does gate on source,
    # because that one answers a REPETITION question about a reader
    # that remembers.
    if frame_role == "unknown":
        context_parts.insert(1, _UNKNOWN_ROLE_NOTICE)


SNAPSHOT_MAX_BYTES = 2500   # the previous-session summary; the cut names the full journal


def cap_snapshot(text: str | None, session_dir: str | None, limit: int = SNAPSHOT_MAX_BYTES) -> str | None:
    """The summary cut at a line boundary under `limit` bytes, ending with the journal path."""
    if not text or len(text.encode("utf-8")) <= limit:
        return text
    kept = text.encode("utf-8")[:limit].decode("utf-8", "ignore")
    kept = kept[:kept.rfind("\n")] if "\n" in kept else kept
    journal = os.path.join(session_dir or "", "session-journal.jsonl")
    return f"{kept}\n(cut at {limit} B; the full journal is {journal})"


def finish_session_start(
    context_parts: list, system_messages: list, tasks, source: str,
    project_dir: str, session_id: str, team_name: str, session_dir: str,
    plugin_root: str, frame_is_lead: bool,
) -> None:
    """Steps 5a (previous dir) through 9: Current Session block, resumption,
    snapshot, resume claim and the hook briefing."""
    # 5a. Capture the PREVIOUS session's dir from project CLAUDE.md
    # before step 5b overwrites the Current Session block with THIS
    # session's info. READ-BEFORE-WRITE invariant: _extract_prev_session_dir
    # must run before update_session_info, otherwise it reads back the
    # just-written current session dir and silently breaks cross-session
    # resume (step 7) and paused/refreshed-work detection (step 8).
    prev_session_dir = _extract_prev_session_dir(project_dir)

    # 5b. Write session resume info to project CLAUDE.md
    # (session_dir already resolved above for substitution instructions)
    # Skip the CLAUDE.md write when session_id is an "unknown-*" sentinel
    # (bundle 5 fallback for missing stdin; per-process unique suffix).
    # On the malformed-stdin path BOTH the journal session_start event and
    # the CLAUDE.md Current Session block are skipped (see the gate above
    # around append_event and the rationale at step 5 intro): writing
    # `- Session dir: .../unknown-xxxx/` into CLAUDE.md pollutes state
    # recovery: session_resume.py:199 would feed `.../unknown-xxxx/` into
    # _extract_prev_session_dir, and session_end.py:cleanup_old_sessions
    # filters by _UUID_PATTERN (which "unknown-*" never matches), so the
    # directory would accumulate indefinitely.
    # Lead-only (#877): the CLAUDE.md "## Current Session" block is the true
    # CROSS-PROCESS CLOBBER — a teammate/plain frame writing it overwrites
    # the lead's session block in the shared project file. Gate on is_lead
    # in addition to the existing sentinel guard.
    if frame_is_lead and not _is_unknown_or_missing_session(session_id):
        session_msg = update_session_info(
            session_id, team_name, session_dir, plugin_root,
            started=_extract_session_started(project_dir) if source == "compact" else None,
        )
        if session_msg:
            if "failed" in session_msg.lower() or "skipped" in session_msg.lower():
                system_messages.append(session_msg)
            else:
                parts_ledger.note("session_info", session_msg)
                context_parts.append(session_msg)

    # 6. Check for in_progress Tasks (resumption context via Task
    # integration). Consumes the hoisted `tasks` variable (single
    # source of truth; #444 post-boundary dedup).
    if tasks:
        resumption_msg = check_resumption_context(tasks)
        if resumption_msg:
            # Blockers are critical - put in system message for visibility
            if "**Blockers:" in resumption_msg:
                system_messages.append(resumption_msg)
            else:
                parts_ledger.note("resumption_tasks", resumption_msg)
                context_parts.append(resumption_msg)

    # 7. Restore last session snapshot for cross-session continuity
    # (prev_session_dir was captured in step 5a, before step 5b overwrote
    # the Current Session block.)
    session_snapshot = cap_snapshot(restore_last_session(prev_session_dir=prev_session_dir), prev_session_dir)
    if session_snapshot:
        parts_ledger.note("snapshot", session_snapshot)
        context_parts.append(session_snapshot)

    # 8. Check for paused or refreshed work from a previous /repacted:pause
    # or /repacted:refresh. ONE unified resolver (check_resume_state) reads
    # both event types and arbitrates newest-wins — never two parallel
    # reader paths at this seam. Lead-only (#877): mechanically this is
    # a READ (it surfaces a resume prompt, not a write), but surfacing
    # the lead's resume claim is a lead-only operation — a teammate/plain
    # frame must not receive the lead's resume prompt. Gate the check
    # itself so a non-lead frame does no journal read either.
    claim_surfaced = False
    if frame_is_lead:
        resume_msg = check_resume_state(prev_session_dir=prev_session_dir)
        # No pause or refresh claim: a predecessor that ended on /exit (or
        # crashed) with open work surfaces the same continue-or-fresh
        # prompt. Fresh starts only, and only when the predecessor is a
        # DIFFERENT session dir: after compact or a same-session resume
        # prev_session_dir resolves to this session, whose own open work
        # is not a predecessor's.
        if (
            not resume_msg
            and source in ("startup", "clear")
            and prev_session_dir
            and session_dir
            and os.path.realpath(prev_session_dir) != os.path.realpath(session_dir)
        ):
            resume_msg = check_unpaused_exit(prev_session_dir=prev_session_dir)
        if resume_msg:
            claim_surfaced = True
            parts_ledger.note("resume_claim", resume_msg)
            context_parts.append(resume_msg)
            # Record that a resumption claim SURFACED, in THIS session's
            # journal. The Working Memory hold itself is decided in-process
            # from `claim_surfaced` at step 9 (the secretary rework: the rebuild
            # moved here from the secretary's spawn); the event is the
            # durable record of the claim for every later reader.
            #
            # WHAT THE MARKER MEANS IS EXACTLY THAT: A CLAIM SURFACED. It
            # does NOT mean an arc is still running, and wording it that
            # way would be false on branches this very call reaches. A
            # paused claim whose PR is MERGED or CLOSED surfaces — after
            # the `gh` probe inside the interpreter has just established
            # the arc is over — and so does one past its 14-day staleness
            # cutoff. Both are reachable in ordinary operation. (A
            # fieldless refreshed claim surfaces too, because that
            # interpreter is total by signature, but no writer in this
            # tree can produce one: the type's registration requires
            # fields, so the write path refuses it and only a hand-edited
            # or corrupt journal reaches the read path, which does not
            # re-validate. That is the read-path totality this module
            # wants, not a defect.)
            #
            # THE OVER-FIRING IS KEPT DELIBERATELY, ON FAIL DIRECTION. A
            # marker written when the arc is over costs one session of a
            # block that lags the store. A marker NOT written when the arc
            # is live costs the contamination this whole mechanism exists
            # to prevent. The asymmetry decides it, and it decides it the
            # same way whichever branch surfaced.
            #
            # WHAT THE SKIP GUARANTEES, AND WHAT IT DEPENDS ON. It
            # guarantees the block is not rewritten AT THIS BOUNDARY, so
            # the post-resumption cohort reads what the boundary left. It
            # does NOT by itself guarantee that what the boundary left is
            # what the arc's earlier agents read — that holds only while
            # every dispatch reachable before the boundary stays quiet. A
            # site that propagates earlier in the arc contaminates the
            # block before any freeze begins, and freezing then preserves
            # the contamination rather than preventing it. The quiet
            # default is what makes the guarantee whole; this write only
            # holds the boundary.
            #
            # KEYED ON THE VERDICT, NOT ON THE RAW CLAIM EVENTS. Writing
            # this from `session_refreshed` / `session_paused` directly
            # looks equivalent and is smaller, and it silently drops a
            # writer: `check_resume_state` already unifies every producer
            # of a pause-shaped claim, wrap-up's open-PR branch included.
            #
            # NO FIELDS — PRESENCE IS THE WHOLE SIGNAL. The consumer checks
            # only that the event exists. An earlier shape carried the
            # winning claim's type and timestamp; neither had a reader, and
            # `check_resume_state` returns a composed prompt that discards
            # which claim won, so supplying them would have meant either
            # splitting that resolver or re-applying its newest-wins rule
            # here — a second copy of an ordering rule with nothing
            # comparing the copies. The claim events stay in the journal
            # for anyone debugging, so nothing is lost.
            #
            # THE FAIL DIRECTION IS THE UNSAFE ONE AND CANNOT BE MADE SAFE.
            # No marker means the secretary rebuilds, which hands this
            # arc's own conclusions to the agents whose value is reaching
            # conclusions independently. The only safe alternative —
            # refusing to start the session — is worse than the defect. So
            # it is made LOUD instead: never raises, never blocks session
            # start, and says so when the write does not land. A hole that
            # announces itself is recoverable; a silent one is the defect.
            #
            # WHAT LOUD COVERS, AND WHAT IT DOES NOT. It covers a failed
            # marker WRITE, which is the only failure reachable from here.
            # It does not cover a claim that never SURFACES, and that path
            # reaches the identical end state — no marker, a rebuild, and
            # nothing on either channel. `check_resume_state` returns None
            # on its first line when `prev_session_dir` is falsy, so a
            # missing CLAUDE.md, an absent Session dir line whose fallback
            # also fails, or a path rejected by the under-pact-sessions
            # validation all end here silently, having never entered this
            # branch at all. Whether a session with no legitimate
            # predecessor SHOULD announce anything is a noise-versus-signal
            # question — most such sessions are simply the first in a
            # project — and it is deliberately not answered here. What is
            # answered is the narrower claim: this guard is loud about the
            # write, not about the absence.
            # TWO CHANNELS, TWO ACTORS, AND THE SECOND IS NOT A COPY OF
            # THE FIRST. systemMessage reaches the human, who can diagnose
            # this and little else; additionalContext reaches the lead,
            # which is the only actor that can PREVENT the consequence, by
            # carrying the skip to the secretary by hand. So the first is a
            # failure report in the channel every other failure in this
            # hook uses, and the second is a DIRECTIVE in the channel that
            # carries directives. Dropping either silently drops one actor.
            #
            # THE TWO CHANNELS ARE INDEPENDENT AT THIS SITE AND JOINT AT
            # THE EMISSION SITE, which is where the guarantee actually
            # rests. Here both appends sit inside this one guard with no
            # return or raise between them, and neither call can fail:
            # `append_event` is total and `make_event` with no fields
            # cannot raise. But neither list is emitted here. Both render
            # near the end of `main()` — `additionalContext` from
            # `context_parts`, `systemMessage` from `system_messages` —
            # and other context blocks run in between. Any
            # exception raised there reaches `main()`'s outer handler,
            # which builds a fresh output from `_build_safety_net_context`
            # and reads NEITHER list. So one raise between this append and
            # that render drops the human's report and the lead's
            # directive together, and the two-actor property becomes one
            # of totality in the code that sits between, not of anything
            # visible from here. No live defect: the intervening calls are
            # total today. But the invariant lives in another file with
            # nothing binding it to this claim, so a future raise between
            # these two points silences both actors at once.
            if not append_event(make_event("session_resumption_surfaced")):
                system_messages.append(
                    "Resumption marker not recorded: the "
                    "session_resumption_surfaced journal write failed. "
                    "This session's Working Memory rebuild was still held, "
                    "but nothing durable records the claim, so a later "
                    "rebuild request would not know to hold."
                )
                context_parts.append(
                    "RESUMPTION MARKER MISSING: this session resumes an "
                    "interrupted workstream, but the journal record of "
                    "that was not written. Do NOT ask the secretary to "
                    "rebuild or propagate into the Working Memory block "
                    "during this arc. A rebuild hands this arc's own "
                    "conclusions to the agents spawned to judge it."
                )

    # 9. Hook-computed session briefing: the pact-memory
    # facts the secretary used to deliver at spawn, <= 2 KB, plus the
    # on-demand secretary reminder while none is on the team. Lead-only,
    # like the resume claim above. build_session_briefing is total.
    # The Working Memory rebuild (amend_4) rides in the same briefing: a
    # session start rebuilds unless a claim surfaced at step 8; a later
    # boundary reports how that rebuild ended.
    if frame_is_lead:
        wm_line = start_working_memory_rebuild(
            source, claim_surfaced, project_dir, session_dir, plugin_root,
        ) or working_memory_rebuild_outcome(session_dir)
        briefing = build_session_briefing(
            project_dir, team_name, extra_lines=(wm_line,) if wm_line else (),
        )
        if briefing:
            parts_ledger.note("briefing", briefing)
            context_parts.append(briefing)
