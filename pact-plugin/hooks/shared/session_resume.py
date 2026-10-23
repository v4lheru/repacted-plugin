"""Session resume and briefing for cross-session continuity (called by session_init).

Map, in file order: update_session_info (Current Session block in project CLAUDE.md),
restore_last_session / _build_journal_resume (previous journal summary),
check_resumption_context, check_paused_state and the paused interpreter,
check_resume_state (the one seam session_init step 8 calls), check_unpaused_exit
and its helpers, prompt sanitizers, the refreshed interpreter, _refresh_is_spent /
_pause_is_spent, _arbitrate, has_unspent_refresh, the memory briefing
(build_session_briefing) and the detached Working Memory rebuild.

Doc: the design notes
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MANAGED_TITLE,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    RETRIEVED_CONTEXT_COMMENT,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
    WORKING_MEMORY_COMMENT,
    ContainmentError,
    _atomic_write_text,
    _read_replaced,
    ensure_dot_claude_parent,
    file_lock,
    resolve_project_claude_md_path,
)
from shared.failure_cause import failure_cause
from shared.handoff_schema import resolve_handoff_field
from shared.session_journal import (
    _parse_ts,
    _ts_supersedes,
    read_events_from,
    read_last_event_from,
)

# Maximum characters for decision summaries in journal resume output
_DECISION_TRUNCATION_LIMIT = 80

# Past this many hours a refresh prompt gets a STALE prefix; downgrade only, never suppression.
_REFRESH_STALE_HOURS = 48

# Bounds for event fields in the refreshed prompt (the journal is plain JSONL on disk).
_REFRESH_FIELD_TRUNCATION_LIMIT = 200
_REFRESH_PATH_TRUNCATION_LIMIT = 512
# The paused event's free-text resume_hint, as shown in the resume prompt.
_PAUSE_HINT_LIMIT = 500
_PAUSE_HINT_FOLD = str.maketrans({"<": "(", ">": ")", "'": "’", "`": "’"})

# IDENTIFIER bound: unused here, shared vocabulary with skills/pact-memory/scripts/working_memory.py
# (TestSanitizePromptFieldTwinCopyDrift); change both together.
_REFRESH_IDENTIFIER_TRUNCATION_LIMIT = 64

# Control chars stripped from prompt fields (C0, DEL, C1, U+2028/9). The `+` matters: a run
# becomes one space. Do not merge with session_state.SESSION_ID_CONTROL_CHARS_RE (a narrower detector).
_PROMPT_CONTROL_CHARS_RE = re.compile("[\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]+")

# Bound for phase strings in resume output.
_PHASE_TRUNCATION_LIMIT = 80

_RETRIEVED_CONTEXT_HEADING = re.compile(r"## Retrieved Context\s*$")


def _content_span(doc, first: int, last: int) -> tuple[int, int]:
    """(start, end) in the original text of rows first..last of the parsed `doc`, without the
    last row's terminator, which stays put."""
    return doc.row_start(first), doc.row_start(last) + len(doc.lines[last].content)


def _session_block_refusal(reason: str) -> str:
    """The status for a file whose Current Session block is not rewritten. The word
    `skipped` routes it to the user-visible surface."""
    return (
        f"Session info skipped: {reason}. The Current Session block in "
        "CLAUDE.md was left unchanged and is now stale."
    )


def _session_block_insertion(doc) -> tuple[int | None, str | None]:
    """Where a new Current Session block goes: (offset, or None for end of file; refusal or None).

    Never inside PACT_MEMORY: (a) with the managed and memory start markers found, before the
    memory start marker's row; (b) a memory block with no managed region: before its start
    row; (c) no memory block: before a column-0 `## Retrieved Context` heading; (d) else at
    end of file. A marker or heading the parser cannot place refuses.
    """
    from shared.claude_md_markers import State

    managed = doc.find_marker(MANAGED_START_MARKER)
    memory_start = doc.find_marker(MEMORY_START_MARKER)
    for located in (managed, memory_start):
        if located.state in (State.DUPLICATE, State.MALFORMED):
            return None, located.reason
    if managed.state is State.FOUND and memory_start.state is State.FOUND:
        return doc.row_start(memory_start.spans[0][0]), None
    memory = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER)
    if memory.state is State.FOUND:
        return doc.row_start(memory.spans[0][0]), None
    if memory.state is not State.ABSENT:
        return None, memory.reason
    heading = doc.find_section(_RETRIEVED_CONTEXT_HEADING, None)
    if heading.state is State.FOUND:
        return doc.row_start(heading.spans[0][0]), None
    if heading.state is State.ABSENT:
        return None, None
    return None, heading.reason


def _plan_session_block(content: str, session_block: str) -> tuple[str | None, str | None]:
    """Plan the Current Session write for `content`: (new_content, status). Pure.

    new_content is None when nothing is written: the block already holds these values (status
    None), or the file is left alone and status says why. Every location comes from the
    fence-aware parser, and the write must read back as one block exactly where it was put.
    """
    from shared.claude_md_markers import State, parse

    doc = parse(content)
    block = doc.find_block(SESSION_START_MARKER, SESSION_END_MARKER)
    if block.state is State.FOUND:
        # Plain slicing, so nothing in session_block is interpreted (a backslash stays one).
        start, end = _content_span(doc, *block.spans[0])
        new_content = content[:start] + session_block + content[end:]
        if new_content == content:
            return None, None
        status = "Session info updated in project CLAUDE.md"
    elif block.state is State.ABSENT:
        at, refusal = _session_block_insertion(doc)
        if refusal is not None:
            return None, _session_block_refusal(refusal)
        if at is None:
            base = content if content.endswith("\n") else content + "\n"
            start = len(base) + 1
            new_content = base + "\n" + session_block + "\n"
        else:
            start = at
            new_content = content[:at] + session_block + "\n\n" + content[at:]
        status = "Session info added to project CLAUDE.md"
    else:
        return None, _session_block_refusal(block.reason)
    written = parse(new_content)
    check = written.find_block(SESSION_START_MARKER, SESSION_END_MARKER)
    if check.state is State.FOUND and (
        _content_span(written, *check.spans[0]) == (start, start + len(session_block))
    ):
        return new_content, status
    return None, _session_block_refusal(
        "the rewritten Current Session block did not read back as one block where it was "
        f"written{': ' + check.reason if check.reason else ''}")


def update_session_info(
    session_id: str,
    team_name: str,
    session_dir: str | None = None,
    plugin_root: str | None = None,
    started: str | None = None,
) -> str | None:
    """Write the Current Session section (SESSION_START/END markers) to the project's CLAUDE.md.

    `started` None writes now; session_init passes the existing value after a compaction.
    Returns a status message (the word "failed" routes it to system messages) or None.
    """
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return None

    # Existing .claude/CLAUDE.md, then ./CLAUDE.md, else create .claude/CLAUDE.md.
    target_file, _source = resolve_project_claude_md_path(project_dir)

    # Markers come from claude_md_manager, not re-spelled.
    SESSION_START = SESSION_START_MARKER
    SESSION_END = SESSION_END_MARKER

    timestamp = started or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Absolute path: command files read it single-quoted (no ~ expansion) and the journal CLI
    # needs it absolute. Sanitize the value, not the line: the trailing \n is the list separator.
    session_dir_line = ""
    if session_dir:
        cleaned_session_dir = _sanitize_prompt_field(
            str(session_dir), _REFRESH_PATH_TRUNCATION_LIMIT
        )
        session_dir_line = f"- Session dir: `{cleaned_session_dir}`\n"

    # Build plugin root line (no abbreviation — needs to be usable as-is in Bash)
    plugin_root_line = ""
    if plugin_root:
        cleaned_plugin_root = _sanitize_prompt_field(
            str(plugin_root), _REFRESH_PATH_TRUNCATION_LIMIT
        )
        plugin_root_line = f"- Plugin root: `{cleaned_plugin_root}`\n"

    # Sanitized at the value (free text, tight bound); backticks do not stop a newline.
    cleaned_session_id = _sanitize_prompt_field(str(session_id))
    cleaned_team_name = _sanitize_prompt_field(str(team_name))

    session_block = (
        f"{SESSION_START}\n"
        f"## Current Session\n"
        f"<!-- Auto-managed by session_init hook. Overwritten each session. -->\n"
        f"- Resume: `claude --resume {cleaned_session_id}`\n"
        f"- Team: `{cleaned_team_name}`\n"
        f"{session_dir_line}"
        f"{plugin_root_line}"
        f"- Started: {timestamp}\n"
        f"{SESSION_END}"
    )

    # Create .claude/ with 0o700 before file_lock, which would create it with the umask mode.
    ensure_dot_claude_parent(target_file)

    # Serialize read-mutate-write between concurrent session_init hooks; fail open on timeout.
    try:
        with file_lock(target_file):
            # Containment (in _atomic_write_text) catches a symlinked parent, inside the lock.
            try:
                # Case 0: no file; create the full managed structure (mirrors ensure_project_memory_md):
                # "# PACT Framework and Managed Project Memory", session block, PACT_MEMORY sections.
                if not target_file.exists():
                    new_content = (
                        f"{MANAGED_START_MARKER}\n"
                        f"{MANAGED_TITLE}\n"
                        "\n"
                        f"{session_block}\n"
                        "\n"
                        f"{MEMORY_START_MARKER}\n"
                        "## Retrieved Context\n"
                        f"{RETRIEVED_CONTEXT_COMMENT}\n"
                        "\n"
                        "## Pinned Context\n"
                        "\n"
                        "## Working Memory\n"
                        f"{WORKING_MEMORY_COMMENT}\n"
                        f"{MEMORY_END_MARKER}\n"
                        "\n"
                        f"{MANAGED_END_MARKER}\n"
                    )
                    _atomic_write_text(target_file, new_content, Path(project_dir))
                    return "Session info created in new project CLAUDE.md"

                content = target_file.read_text(encoding="utf-8")

                # Cases 1 and 2: the planner locates the block and its insertion point with the
                # fence-aware parser (upstream PACT #1656), so a fenced copy of a marker is never
                # written. A block it cannot place is left alone and the status names the line.
                new_content, status = _plan_session_block(content, session_block)
                if new_content is not None:
                    _atomic_write_text(target_file, new_content, Path(project_dir))
                return status

            except UnicodeDecodeError:
                # Never rewrite a file that is not valid UTF-8 (upstream PACT #1650); report it
                # only when the block was due to change.
                new_content, status = _plan_session_block(_read_replaced(target_file), session_block)
                if new_content is None and status is None:
                    return None
                return ("Session info skipped: the project CLAUDE.md is not valid UTF-8, so it "
                        "was left unchanged. The Current Session block in CLAUDE.md is now stale.")
            except ContainmentError:
                # Opaque skip, matching the removed is_symlink guard's message.
                return "Session info skipped: path precondition not met."
            except Exception as e:
                # File-layer backstop (read, write, decode, encode errors); fail open.
                # Keep the "Session info failed: " prefix byte-identical: session_init routes on "failed".
                # The cause token is a closed vocabulary (shared/failure_cause.py); the message is never read (path leak).
                return (
                    f"Session info failed: {failure_cause(e)}. "
                    "The Current Session block in CLAUDE.md is now stale."
                )
    except TimeoutError:
        return (
            "Failed to acquire lock on project CLAUDE.md within 5s "
            "(another session_init hook may be running concurrently). "
            "Session info update skipped; will retry on next session start."
        )
    except OSError:
        # Lock acquisition PermissionError skips like a timeout; opaque, no path.
        return (
            "Could not acquire lock on project CLAUDE.md "
            "(path precondition not met); session info update skipped."
        )


def restore_last_session(
    prev_session_dir: str | None = None,
) -> str | None:
    """
    Restore the last session context for cross-session continuity.
    """
    if not prev_session_dir:
        return None

    return _build_journal_resume(prev_session_dir)


def _coerce_decision_summary(decisions: Any) -> str:
    """Short, bounded summary from a handoff's `decisions`; "" for any non-list or empty shape."""
    if not isinstance(decisions, list) or not decisions:
        return ""
    first = decisions[0]
    if isinstance(first, str):
        summary = first
    elif first is None:
        return ""
    else:
        # Best-effort stringify for dict/list/number/other — bounded by
        # truncation below so even a giant dict becomes a readable stub.
        summary = str(first)
    if len(summary) > _DECISION_TRUNCATION_LIMIT:
        summary = summary[:_DECISION_TRUNCATION_LIMIT - 3] + "..."
    return summary


def _coerce_phase_string(phase: Any) -> str:
    """Stringify and bound a `phase` value; None gives "", junk gives at most an 80-char stub."""
    if phase is None:
        return ""
    rendered = str(phase)
    if len(rendered) > _PHASE_TRUNCATION_LIMIT:
        rendered = rendered[:_PHASE_TRUNCATION_LIMIT - 3] + "..."
    return rendered


def _build_journal_resume(session_dir: str) -> str | None:
    """Build resume context from a previous session's journal; never raises (returns None).

    A raise here would replace session_init's whole SessionStart output with an error.
    """
    try:
        return _build_journal_resume_inner(session_dir)
    except Exception as e:
        # Last-resort catch so one malformed event cannot nuke session_init's
        # hook output. Log so the bug is visible but fail-open.
        print(
            f"session_resume: _build_journal_resume failed "
            f"(fail-open, returning None): {e}",
            file=sys.stderr,
        )
        return None


def _build_journal_resume_inner(session_dir: str) -> str | None:
    """
    Inner implementation of _build_journal_resume.
    """
    all_events = read_events_from(session_dir)
    if not all_events:
        return None

    lines = ["Previous session summary (from journal -- read-only reference):", ""]

    # Every field is guarded; decisions[0] was the old crash site (BugF1).
    handoffs = [e for e in all_events if e.get("type") == "agent_handoff"]
    # One bullet per task: the latest handoff wins. The selection rule lives in
    # skills/pact-handoff-harvest/SKILL.md (Step 3); change both together.
    latest = {}
    for h in handoffs:
        key = (h.get("agent", "unknown"), h.get("task_subject", ""))
        # `>=` keeps the LATER journal line on an equal ts. Same tie-break.
        if key not in latest or h.get("ts", "") >= latest[key].get("ts", ""):
            latest[key] = h
    handoffs = list(latest.values())
    if handoffs:
        lines.append("## Completed Work")
        for h in handoffs:
            agent = h.get("agent", "unknown")
            subject = h.get("task_subject", "")
            handoff_data = h.get("handoff")
            if not isinstance(handoff_data, dict):
                handoff_data = {}
            # Canonical key first, legacy spelling as fallback (old journal rows stay on disk).
            summary = _coerce_decision_summary(
                resolve_handoff_field(handoff_data, "decisions")
            )
            if summary:
                lines.append(f"- {agent}: {subject} -> {summary}")
            else:
                lines.append(f"- {agent}: {subject}")
        lines.append("")

    # Only non-empty str phases, sorted by ts (BugF1 primary crash site).
    phases = sorted(
        [e for e in all_events if e.get("type") == "phase_transition"],
        key=lambda e: e.get("ts", ""),
    )
    if phases:
        completed = [
            phase
            for p in phases
            if p.get("status") == "completed"
            and (phase := p.get("phase")) and isinstance(phase, str)
        ]

        # A phase is active only if its latest transition is `started`.
        latest_per_phase: dict[str, tuple[str, str]] = {}
        for p in phases:
            name = p.get("phase")
            status = p.get("status")
            ts = p.get("ts", "")
            if isinstance(name, str) and name and isinstance(status, str):
                prev = latest_per_phase.get(name)
                # `>=`: on equal ts the later-seen event wins, so started+completed is not left active.
                if prev is None or ts >= prev[0]:
                    latest_per_phase[name] = (ts, status)
        # Pick the active phase by max ts, not dict order.
        active_entries = [
            (ts, name)
            for name, (ts, status) in latest_per_phase.items()
            if status == "started"
        ]

        if completed:
            lines.append(
                "Completed phases: "
                + ", ".join(_coerce_phase_string(c) for c in completed)
            )
        if active_entries:
            latest_active = max(active_entries)[1]
            lines.append(
                f"Last active phase: {_coerce_phase_string(latest_active)}"
            )
        lines.append("")

    # Check for warnings in session_end events
    end_events = [e for e in all_events if e.get("type") == "session_end"]
    for end_event in end_events:
        warning = end_event.get("warning")
        if warning:
            lines.append(f"**Warning**: {warning}")
            lines.append("")

    # Minimal output check
    if len(lines) <= 2:
        return None

    return "\n".join(lines)


def check_resumption_context(tasks: list[dict[str, Any]]) -> str | None:
    """
    Check if there are in_progress Tasks indicating work to resume.
    """
    in_progress = [t for t in tasks if t.get("status") == "in_progress"]
    pending = [t for t in tasks if t.get("status") == "pending"]

    if not in_progress and not pending:
        return None

    # Count by type
    feature_tasks = []
    phase_tasks = []
    agent_tasks = []
    blocker_tasks = []

    for task in in_progress:
        subject = task.get("subject", "")
        metadata = task.get("metadata") or {}

        if metadata.get("type") in ("blocker", "algedonic"):
            blocker_tasks.append(task)
        elif any(subject.startswith(p) for p in ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:")):
            phase_tasks.append(task)
        elif any(subject.lower().startswith(p) for p in ("pact-",)):
            agent_tasks.append(task)
        else:
            # Assume it's a feature task
            feature_tasks.append(task)

    parts = []

    if feature_tasks:
        names = [t.get("subject", "unknown")[:30] for t in feature_tasks[:2]]
        if len(feature_tasks) > 2:
            parts.append(f"Features: {', '.join(names)} (+{len(feature_tasks)-2} more)")
        else:
            parts.append(f"Features: {', '.join(names)}")

    if phase_tasks:
        phases = [t.get("subject", "").split(":")[0] for t in phase_tasks]
        parts.append(f"Phases: {', '.join(phases)}")

    if agent_tasks:
        parts.append(f"Active agents: {len(agent_tasks)}")

    if blocker_tasks:
        parts.append(f"**Blockers: {len(blocker_tasks)}**")

    if parts:
        summary = f"Resumption context: {' | '.join(parts)}"
        if pending:
            summary += f" ({len(pending)} pending)"
        return summary

    return None


def check_paused_state(
    prev_session_dir: str | None = None,
) -> str | None:
    """Detect paused work (session_paused) in the previous session's journal.

    TTL 14 days, then a `gh pr view` check; MERGED/CLOSED returns info. The journal is never edited.
    """
    if not prev_session_dir:
        return None

    return _check_journal_paused_state(prev_session_dir)


def _check_journal_paused_state(session_dir: str) -> str | None:
    """Check for paused state in the previous session's journal."""
    event = read_last_event_from(session_dir, "session_paused")
    if not event:
        return None
    return _interpret_paused_event(event)


def _compose_pause_key(event: dict) -> str:
    """Compose the ` pause_ts=` consumption key for a session_paused event (always non-empty).

    Shared by the interpreter and _arbitrate so every surfacing path carries the key
    bootstrap copies to retire the claim. NOT sanitized: _pause_is_spent compares it
    exactly, so a ts with control characters renders UNAVAILABLE instead.
    """
    ts = event.get("ts", "")
    if (
        isinstance(ts, str)
        and ts.strip()
        and not _PROMPT_CONTROL_CHARS_RE.search(ts)
    ):
        return f" pause_ts={ts}"
    return (
        " pause_ts=UNAVAILABLE — consumption cannot be recorded; "
        "prompt may re-surface once."
    )


def _interpret_paused_event(event: dict) -> str | None:
    """Interpret an already-read session_paused event into a resume prompt (or None).

    PR-gated silent None is correct here (junk PR number: nothing to resume), the
    opposite of the refreshed interpreter. A pause with no PR is valid. Every prompt
    ends with the _compose_pause_key clause.
    """
    pr_number = event.get("pr_number")
    # Event text reaches the lead's prompt: no newline or control character
    # may let it start a line of its own.
    branch, worktree_path = (
        (_sanitize_prompt_field(value) if isinstance(value, str) else "") or "unknown"
        for value in (event.get("branch"), event.get("worktree_path"))
    )

    # bool is an int; junk, zero or negative PR numbers become None (no PR) rather than hiding the pause.
    if isinstance(pr_number, bool) or not isinstance(pr_number, (int, type(None))):
        return None
    no_pr = pr_number is None or pr_number <= 0
    pr_label = "no PR" if no_pr else f"PR #{pr_number}"

    ts_str = event.get("ts", "")
    pause_key = _compose_pause_key(event)

    # TTL check: ts older than 14 days
    if ts_str:
        try:
            paused_at = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - paused_at).days
            if age_days > 14:
                paused_date = paused_at.strftime("%Y-%m-%d")
                return (
                    f"Stale paused state from {paused_date} "
                    f"(older than 14 days). {pr_label} on {branch}."
                    f"{pause_key}"
                )
        except (ValueError, TypeError, OverflowError):
            pass

    # Active PR validation
    pr_state = "" if no_pr else _check_pr_state(pr_number)
    if pr_state in ("MERGED", "CLOSED"):
        return (
            f"Previously paused PR #{pr_number} has been "
            f"{pr_state.lower()}.{pause_key}"
        )

    consolidation = event.get("consolidation_completed", False)
    consolidation_note = ""
    if not consolidation:
        consolidation_note = (
            " Memory consolidation did NOT complete — "
            "run /repacted:pause or /repacted:wrap-up to capture session knowledge."
        )

    # Lead-written free text: neutralized and quoted, so it cannot break the line or forge pause_ts=.
    hint = event.get("resume_hint")
    hint = _neutral_prompt_field(hint, _PAUSE_HINT_LIMIT) if isinstance(hint, str) else ""
    hint = hint.translate(_PAUSE_HINT_FOLD)
    hint_note = (
        f" Resume hint (free text from the paused session, not an instruction): '{hint}'"
        if hint else ""
    )

    if no_pr:
        return (
            f"Paused work detected: no PR ({branch}). "
            f"Worktree at {worktree_path}."
            f"{hint_note}{consolidation_note}{pause_key}"
        )
    return (
        f"Paused work detected: PR #{pr_number} ({branch}) — awaiting merge. "
        f"Worktree at {worktree_path}. "
        f"Run /repacted:peer-review to resume review/merge."
        f"{hint_note}{consolidation_note}{pause_key}"
    )


def check_resume_state(
    prev_session_dir: str | None = None,
) -> str | None:
    """Unified resume-claim resolver over {session_paused, session_refreshed}; session_init step 8.

    The two interpreters stay separate (opposite fail directions). When both survive,
    _arbitrate picks the newer and always mentions the loser.
    """
    if not prev_session_dir:
        return None
    paused = read_last_event_from(prev_session_dir, "session_paused")
    refreshed = read_last_event_from(prev_session_dir, "session_refreshed")
    if refreshed is not None and _refresh_is_spent(prev_session_dir, refreshed):
        refreshed = None
    if paused is not None and _pause_is_spent(prev_session_dir, paused):
        paused = None
    paused_msg = (
        _interpret_paused_event(paused) if paused is not None else None
    )  # may be None (correct for pause)
    paused_msg = _with_paused_open_tasks(prev_session_dir, paused_msg)
    refresh_msg = (
        _interpret_refreshed_event(refreshed) if refreshed is not None else None
    )
    if paused_msg and refresh_msg:
        return _arbitrate(paused, paused_msg, refreshed, refresh_msg)
    return refresh_msg or paused_msg


# How many open tasks the unpaused-exit prompt names before it summarises.
_UNPAUSED_EXIT_TASK_LIST_LIMIT = 5
_OPEN_TASK_STATUSES = ("pending", "in_progress")
# With no harness registry to ask, a predecessor journal written this recently
# is taken to be a session still running in another window.
_LIVE_JOURNAL_WINDOW_S = 600


def check_unpaused_exit(prev_session_dir: str | None = None) -> str | None:
    """Resume prompt for a predecessor that ended WITHOUT /repacted:pause and left open work, or None.

    Signals: open task files, HANDOFFs after the last consolidation that no harvest
    recorded, and session_end's unpaused-PR warning; reported as recorded, not as now.
    None when it paused/refreshed after its start, is still live, or shows no signal.
    """
    if not prev_session_dir:
        return None
    try:
        return _check_unpaused_exit_inner(prev_session_dir)
    except Exception:
        return None


def _latest_ts(events: list) -> str:
    stamps = [e.get("ts") for e in events if isinstance(e, dict) and isinstance(e.get("ts"), str)]
    return max(stamps) if stamps else ""


def _open_tasks_of(starts: list) -> list:
    """Pending or in_progress task files of the team the latest session_start names, in id order.

    An empty team dir falls back to tasks/<CLAUDE_CODE_TASK_LIST_ID>.
    """
    team = starts[-1].get("team") if starts else None
    if not isinstance(team, str) or not team:
        return []
    from shared.task_utils import iter_team_task_jsons
    tasks = list(iter_team_task_jsons(team))
    list_id = os.environ.get("CLAUDE_CODE_TASK_LIST_ID", "")
    if not tasks and list_id and list_id != team:
        tasks = list(iter_team_task_jsons(list_id))
    return sorted(
        (t for t in tasks
         if isinstance(t, dict) and t.get("status") in _OPEN_TASK_STATUSES),
        key=lambda t: (len(str(t.get("id"))), str(t.get("id"))),
    )


def _neutral_prompt_field(value: str, limit: int) -> str:
    """_sanitize_prompt_field for task-authored text, NFKC-folded first so a
    fullwidth or small '=' is caught, then with every '=' and '"' replaced: a
    subject can neither close its quotes nor plant a key=value clause (a second
    pause_ts= above all, which the lead copies VERBATIM for the consumption
    write). "" on any failure, like the sanitizer."""
    try:
        value = unicodedata.normalize("NFKC", value).replace("=", " ").replace('"', "'")
    except Exception:
        return ""
    return _sanitize_prompt_field(value, limit)


def _render_open_tasks(open_tasks: list) -> str:
    named = []
    for task in open_tasks[:_UNPAUSED_EXIT_TASK_LIST_LIMIT]:
        task_id = _neutral_prompt_field(str(task.get("id")), 12)
        subject = task.get("subject")
        subject = _neutral_prompt_field(subject, 80) if isinstance(subject, str) else ""
        status = task.get("status")
        named.append(f'#{task_id} "{subject}" ({status})')
    more = len(open_tasks) - len(named)
    return (f"{len(open_tasks)} open task(s): " + ", ".join(named)
            + (f", and {more} more" if more > 0 else ""))


def _with_paused_open_tasks(session_dir: str, paused_msg: str | None) -> str | None:
    """The paused prompt with the paused session's open tasks named, inserted
    before the ` pause_ts=` key so that key stays the prompt's last clause.
    A new session has a new team task list, so this line is how the paused
    tasks reach the resuming lead. Unchanged on any read problem."""
    if not paused_msg:
        return paused_msg
    try:
        starts = [e for e in read_events_from(session_dir, "session_start") if isinstance(e, dict)]
        open_tasks = _open_tasks_of(starts)
        if not open_tasks:
            return paused_msg
        head, sep, key = paused_msg.rpartition(" pause_ts=")
        clause = f" Open tasks at pause: {_render_open_tasks(open_tasks)}."
        return f"{head}{clause}{sep}{key}" if sep else paused_msg + clause
    except Exception:
        return paused_msg


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True    # exists, owned by someone else
    except (OSError, OverflowError, ValueError):
        return False
    return True


def _harness_session_live(session_id: str) -> bool | None:
    """Whether the harness's per-process registry (<config>/sessions/<pid>.json,
    one file per running Claude Code process, naming its sessionId) shows
    session_id in a process that is still alive. None when the registry is
    absent or holds no readable entry, so the caller falls back; a stale file
    left by a crash names a dead pid and reads as not live."""
    from shared.paths import get_claude_config_dir
    readable = False
    for path in (get_claude_config_dir() / "sessions").glob("*.json"):
        try:
            entry = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        pid = entry.get("pid") if isinstance(entry, dict) else None
        sid = entry.get("sessionId") if isinstance(entry, dict) else None
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not isinstance(sid, str):
            continue   # pid 0 or below would signal a process group
        readable = True
        if sid == session_id and _pid_alive(pid):
            return True
    return False if readable else None


def _predecessor_is_live(session_dir: str, last_start: str) -> bool:
    """True when the predecessor has not exited (a session_end after its latest start means it did).

    Without one, the harness registry decides; with no registry, a recently written journal.
    """
    ends = read_events_from(session_dir, "session_end")
    if ends and _latest_ts(ends) >= last_start:
        return False
    live = _harness_session_live(os.path.basename(os.path.normpath(session_dir)))
    if live is not None:
        return live
    try:
        mtime = os.path.getmtime(os.path.join(session_dir, "session-journal.jsonl"))
    except OSError:
        return False
    return time.time() - mtime < _LIVE_JOURNAL_WINDOW_S


def _harvested_task_ids(session_dir: str) -> set:
    """Task ids a harvest recorded for this session dir in any
    <project>/processed-tasks/<team>.jsonl ledger (harvest.py record's format:
    one JSON line with session_dir and task_ids). Torn lines are skipped."""
    here = os.path.realpath(session_dir)
    ids: set = set()
    for ledger in (Path(here).parent / "processed-tasks").glob("*.jsonl"):
        try:
            lines = ledger.read_text("utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("session_dir"), str):
                continue
            task_ids = entry.get("task_ids")
            if os.path.realpath(entry["session_dir"]) == here and isinstance(task_ids, list):
                ids.update(str(i) for i in task_ids)
    return ids


def _check_unpaused_exit_inner(session_dir: str) -> str | None:
    starts = [e for e in read_events_from(session_dir, "session_start") if isinstance(e, dict)]
    last_start = _latest_ts(starts)
    for claim_type in ("session_paused", "session_refreshed"):
        claims = read_events_from(session_dir, claim_type)
        if claims and _latest_ts(claims) >= last_start:
            return None   # a declared pause or refresh: check_resume_state's claim
    if _predecessor_is_live(session_dir, last_start):
        return None

    open_tasks = _open_tasks_of(starts)

    pending_handoffs: list = []
    for event in read_events_from(session_dir):
        if not isinstance(event, dict):
            continue
        if event.get("type") == "session_consolidated":
            pending_handoffs = []
        elif event.get("type") == "agent_handoff":
            pending_handoffs.append(str(event.get("task_id")))
    harvested = _harvested_task_ids(session_dir) if pending_handoffs else set()
    unconsolidated = sum(1 for task_id in pending_handoffs if task_id not in harvested)

    ends = [e for e in read_events_from(session_dir, "session_end") if isinstance(e, dict)]
    recorded_pr = _recorded_pr_clause(ends[-1]) if ends else ""

    if not open_tasks and not unconsolidated and not recorded_pr:
        return None

    parts = []
    if open_tasks:
        parts.append(_render_open_tasks(open_tasks))
    if unconsolidated:
        parts.append(
            f"{unconsolidated} HANDOFF(s) in its journal with no consolidation "
            f"or harvest event after them"
        )
    if recorded_pr:
        parts.append(recorded_pr)
    return (
        "Unpaused exit detected: the previous session ended without "
        "/repacted:pause. As last recorded: " + "; ".join(parts) + ". "
        "This is the journal's last state, not the state now: before you act "
        "on it, check the remote (is the PR or MR merged, closed or open) and "
        "search memory for the work. "
        "Then ask the user: continue this work or start fresh. Never resume it silently."
    )


_WARNING_PR_NUMBER = re.compile(r"\bPR #([0-9]{1,10})\b")
# A journal `ts` as make_event writes it, with the offset and fraction forms
# an ISO writer may use. ASCII digits only: `\d` also matches fullwidth ones.
_EVENT_TS_TOKEN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})?"
)


def _event_ts_token(ts: Any) -> str:
    """The event's timestamp as one token, or "" when it is not one."""
    if not isinstance(ts, str):
        return ""
    token = _PROMPT_CONTROL_CHARS_RE.sub(" ", ts).split(maxsplit=1)[:1]
    return token[0] if token and _EVENT_TS_TOKEN.fullmatch(token[0]) else ""


def _recorded_pr_clause(end_event: dict) -> str:
    """What session_end recorded about a PR, as a dated record ("" when no warning)."""
    warning = end_event.get("warning")
    if not isinstance(warning, str):
        return ""
    at = _event_ts_token(end_event.get("ts"))
    when = f"at session end ({at})" if at else "at session end"
    number = _WARNING_PR_NUMBER.search(warning)
    if number:
        return (
            f"PR/MR #{number.group(1)} was not recorded as merged or closed {when}"
        )
    quoted = _neutral_prompt_field(warning, _REFRESH_FIELD_TRUNCATION_LIMIT)
    return f"a warning recorded {when}: '{quoted}'" if quoted else ""


def _sanitize_prompt_field(
    value: str,
    limit: int = _REFRESH_FIELD_TRUNCATION_LIMIT,
) -> str:
    """Sanitize an event field for a resume prompt: collapse control chars, strip, bound.

    Total: an internal failure returns "" so the caller drops that line, never the prompt.
    """
    try:
        cleaned = _PROMPT_CONTROL_CHARS_RE.sub(" ", value).strip()
        if len(cleaned) > limit:
            cleaned = cleaned[:limit - 3] + "..."
        return cleaned
    except Exception:
        return ""


def _compose_halt_line(event: dict) -> str | None:
    """Compose the HALT verify-line for a session_refreshed event, or None unless halt_active is True.

    Shared by the interpreter and _arbitrate; task ids are sanitized here, once.
    """
    if event.get("halt_active") is not True:
        return None
    halt_task_ids = event.get("halt_task_ids")
    ids = (
        [_sanitize_prompt_field(i) for i in halt_task_ids if isinstance(i, str)]
        if isinstance(halt_task_ids, list)
        else []
    )
    ids = [i for i in ids if i]
    id_note = f" (tasks: {', '.join(ids)})" if ids else ""
    return (
        f"A HALT/stop signal was ACTIVE at refresh{id_note} — "
        f"verify against TaskList before proceeding; do not assume it "
        f"resolved."
    )


def _interpret_refreshed_event(event: dict) -> str:
    """Interpret an already-read session_refreshed event into a prompt; total, never None.

    Malformed fields drop their line only; no PR gate, no gh probe. Past
    _REFRESH_STALE_HOURS a STALE prefix is added and nothing else changes.
    """
    ts = event.get("ts")
    # A ts with control characters is invalid: the key must echo verbatim, so render UNAVAILABLE.
    ts_valid = (
        isinstance(ts, str)
        and bool(ts.strip())
        and not _PROMPT_CONTROL_CHARS_RE.search(ts)
    )

    content: list[str] = []

    # String fields are sanitized; an emptied field drops its line only.
    feature_subject = event.get("feature_subject")
    feature_task_id = event.get("feature_task_id")
    subject = (
        _sanitize_prompt_field(feature_subject)
        if isinstance(feature_subject, str)
        else ""
    )
    task_id = (
        _sanitize_prompt_field(feature_task_id)
        if isinstance(feature_task_id, str)
        else ""
    )
    if subject and task_id:
        content.append(f"Feature: {subject} (task {task_id}).")
    elif subject:
        content.append(f"Feature: {subject}.")
    elif task_id:
        content.append(f"Feature task: {task_id}.")

    next_phase = event.get("next_phase")
    if isinstance(next_phase, str):
        phase = _sanitize_prompt_field(next_phase)
        if phase:
            content.append(f"Next phase: {phase}.")

    worktrees = event.get("worktrees")
    if isinstance(worktrees, list):
        # Paths sanitized with the wider bound; existence is checked at bootstrap.
        paths = [
            _sanitize_prompt_field(w, _REFRESH_PATH_TRUNCATION_LIMIT)
            for w in worktrees
            if isinstance(w, str)
        ]
        paths = [p for p in paths if p]
        if paths:
            content.append("Worktrees: " + ", ".join(paths) + ".")

    # HALT line present iff halt_active is True; never omit the prompt.
    halt_line = _compose_halt_line(event)
    if halt_line:
        content.append(halt_line)

    # Warn only on an explicit False (the field is required and bool-validated at write).
    if event.get("consolidation_completed") is False:
        content.append(
            "Memory consolidation did NOT complete — "
            "run /repacted:pause or /repacted:wrap-up to capture session knowledge."
        )

    # Degraded floor: a dict with no usable field at all still surfaces.
    if not content and not ts_valid:
        return (
            "Refresh detected — run TaskList to recover state, "
            "then /repacted:bootstrap."
        )

    header = (
        "Refreshed workstream detected — mid-flight resume, "
        "not a fresh start."
    )
    if ts_valid:
        try:
            refreshed_at = _parse_ts(ts)
            if refreshed_at.tzinfo is None:
                refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - refreshed_at
            if age > timedelta(hours=_REFRESH_STALE_HOURS):
                header = (
                    f"STALE checkpoint from "
                    f"{refreshed_at.strftime('%Y-%m-%d')} (older than "
                    f"{_REFRESH_STALE_HOURS}h). " + header
                )
        except (ValueError, TypeError):
            pass  # Unparseable ts ⇒ no downgrade — fail toward full surfacing.

    parts = [header]
    parts.extend(content)
    # Consumption key, ALWAYS when ts is a non-empty str — verbatim and
    # machine-copyable; bootstrap's consumption write substitutes this value.
    if ts_valid:
        parts.append(f"refresh_ts={ts}")
    else:
        parts.append(
            "refresh_ts=UNAVAILABLE — consumption cannot be recorded; "
            "prompt may re-surface once."
        )
    parts.append(
        "Run /repacted:bootstrap and resume; spawn a fresh secretary only "
        "when memory is needed. Do NOT message any pre-refresh teammate name."
    )
    return " ".join(parts)


def _refresh_is_spent(session_dir: str, refreshed: dict) -> bool:
    """True iff a session_refresh_consumed event retires this refresh claim.

    refresh_ts must equal the claim's ts exactly and be written at or after it; every
    failure path is UNSPENT (a duplicate prompt beats a wrongly suppressed one).
    """
    ts = refreshed.get("ts")
    if not isinstance(ts, str) or not ts:
        return False  # fail toward surfacing
    for consumption in read_events_from(session_dir, "session_refresh_consumed"):
        if consumption.get("refresh_ts") != ts:  # exact string match — the ts IS the claim id
            continue
        try:
            if _parse_ts(consumption.get("ts")) >= _parse_ts(ts):
                return True
        except Exception:
            continue  # fail toward surfacing
    return False


def _pause_is_spent(session_dir: str, paused: dict) -> bool:
    """True iff a session_pause_consumed event retires this paused claim.

    A deliberate mirror of _refresh_is_spent; the tests pin only that the two
    consumption streams never cross. Keep it in step with _refresh_is_spent,
    _compose_pause_key vs ts_valid, and the UNAVAILABLE clause (nothing compares them).
    """
    ts = paused.get("ts")
    if not isinstance(ts, str) or not ts:
        return False  # fail toward surfacing
    for consumption in read_events_from(session_dir, "session_pause_consumed"):
        if consumption.get("pause_ts") != ts:  # exact string match — the ts IS the claim id
            continue
        try:
            if _parse_ts(consumption.get("ts")) >= _parse_ts(ts):
                return True
        except Exception:
            continue  # fail toward surfacing
    return False


def _both_parse(*timestamps: Any) -> bool:
    """True iff every argument parses via _parse_ts without raising."""
    for value in timestamps:
        try:
            _parse_ts(value)
        except (ValueError, TypeError):
            return False
    return True


def _claim_date(ts: Any) -> str:
    """Render a claim timestamp as YYYY-MM-DD for the superseded-claim
    clause. Callers guarantee parseability via _both_parse."""
    return _parse_ts(ts).strftime("%Y-%m-%d")


def _arbitrate(
    paused_ev: dict,
    paused_msg: str,
    refreshed_ev: dict,
    refresh_msg: str,
) -> str:
    """Pick the newer of two surviving resume claims (ties go to the refreshed claim).

    The loser is always mentioned; unparseable ts surfaces both. A losing refresh keeps
    its HALT line, a losing pause keeps its pause_ts key.
    """
    p_ts, r_ts = paused_ev.get("ts"), refreshed_ev.get("ts")
    if not _both_parse(p_ts, r_ts):
        return (
            refresh_msg + " | " + paused_msg +
            " | CONFLICT: both a paused and a refreshed claim exist with "
            "unordered timestamps — verify via TaskList before resuming."
        )
    if _ts_supersedes(r_ts, p_ts):
        return refresh_msg + (
            f" (A stale paused claim from {_claim_date(p_ts)} also exists."
            f"{_compose_pause_key(paused_ev)})"
        )
    halt_line = _compose_halt_line(refreshed_ev)
    if halt_line:
        return paused_msg + (
            f" (A superseded refreshed claim from {_claim_date(r_ts)} also "
            f"exists.) {halt_line}"
        )
    return paused_msg + (
        f" (A stale refreshed claim from {_claim_date(r_ts)} also exists.)"
    )


def has_unspent_refresh(session_dir: str | None) -> bool:
    """True iff the dir's latest session_refreshed exists and is unconsumed (presentation only).

    Errors return False.
    """
    try:
        if not session_dir:
            return False
        refreshed = read_last_event_from(session_dir, "session_refreshed")
        if refreshed is None:
            return False
        return not _refresh_is_spent(session_dir, refreshed)
    except Exception:
        return False


def _check_pr_state(pr_number: int | str) -> str:
    """PR state via ``gh pr view`` (uppercase, "" on error); local so tests can patch it."""
    from shared.gh_helpers import check_pr_state

    return check_pr_state(pr_number)


# ─── Session briefing (hook-computed; the secretary spawns on demand) ───
# Reads only the pact-memory store, read-only: both sidecars absent, then mode=ro + immutable=1.

MEMORY_BRIEFING_MAX_BYTES = 2000
# Mirrors shared.variety_scorer.LEARNING_II_MIN_MATCHES (parity test); not imported to keep
# variety_scorer out of the bootstrap_marker_writer import closure.
_LEARNING_II_MIN_MATCHES = 5
_BRIEFING_MEMORY_LIMIT = 3
_BRIEFING_SUMMARY_LIMIT = 110
_SECRETARY_MEMBER_NAME = "secretary"
_MEMORY_DIR_ENV = "PACT_TEST_MEMORY_DIR"


def memory_db_path() -> Path:
    """Where the pact-memory store lives, mirroring skills/pact-memory config (test_session_briefing pins parity)."""
    override = os.environ.get(_MEMORY_DIR_ENV)
    base = Path(override).expanduser() if override else Path.home() / ".claude" / "pact-memory"
    return base / "memory.db"


def memory_project_key(project_dir: str) -> str:
    """The pact-memory project id for a project dir: main repo root's basename (parity-pinned); "" for empty."""
    if not project_dir:
        return ""
    try:
        declared_root = Path(project_dir).resolve()
    except (OSError, RuntimeError):
        declared_root = None
    main_root = None
    try:
        import subprocess
        from shared.project_scope import git_env_without_location
        result = subprocess.run(
            ["git", "-C", str(project_dir), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
            env=git_env_without_location(),
        )
        out = result.stdout.strip()
        if result.returncode == 0 and out:
            common = Path(out)
            if not common.is_absolute():
                common = Path(project_dir) / common
            main_root = common.resolve().parent
    except Exception:
        main_root = None
    if (main_root is not None and declared_root is not None
            and os.path.normcase(str(main_root)) != os.path.normcase(str(declared_root))):
        return main_root.name
    return (declared_root or Path(project_dir)).name


def team_has_secretary(team_name: str, teams_dir: str | None = None) -> bool:
    """True iff a member named `secretary` is on the team roster. Never raises."""
    try:
        from shared import pact_context
        for member in pact_context._iter_members(team_name, teams_dir):
            if member.get("name") == _SECRETARY_MEMBER_NAME:
                return True
    except Exception:
        return False
    return False


def _store_uri(db_path: Path) -> str:
    """The read-only, immutable open the store-access bar requires."""
    return f"file:{db_path}?mode=ro&immutable=1"


def _read_store(db_path: Path, project_key: str, limit: int) -> dict:
    """Read-only facts for the briefing. Returns {"error": ...} or the facts."""
    import sqlite3

    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            return {"error": f"not read ({sidecar.name} present)"}
    if not db_path.is_file():
        return {"error": "no store yet"}
    try:
        conn = sqlite3.connect(_store_uri(db_path), uri=True)
    except sqlite3.Error as exc:
        return {"error": f"not read ({type(exc).__name__})"}
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE project_id = ?", (project_key,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, goal, context, created_at, updated_at FROM memories "
            "WHERE project_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (project_key, limit),
        ).fetchall()
        calibration = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE project_id = ?2 AND EXISTS ("
            " SELECT 1 FROM json_each(COALESCE(memories.entities,'[]')) je"
            " WHERE (je.type='object' AND json_extract(je.value,'$.name') = ?1)"
            " OR (je.type='text' AND je.value = ?1))",
            ("orchestration_calibration", project_key),
        ).fetchone()[0]
        return {"total": total, "rows": rows, "calibration": calibration}
    except sqlite3.Error as exc:
        return {"error": f"not read ({type(exc).__name__})"}
    finally:
        conn.close()


def _memory_line(row: tuple) -> str:
    memory_id, goal, context, created_at, updated_at = row
    text = goal if isinstance(goal, str) and goal.strip() else context
    summary = _sanitize_prompt_field(text if isinstance(text, str) else "",
                                     _BRIEFING_SUMMARY_LIMIT) or "(no summary)"
    stamp = updated_at if isinstance(updated_at, str) and updated_at else created_at
    day = stamp[:10] if isinstance(stamp, str) else "?"
    ident = memory_id[:8] if isinstance(memory_id, str) else "?"
    return f"{day} {summary} [{ident}]"


def build_session_briefing(
    project_dir: str,
    team_name: str,
    *,
    db_path: Path | None = None,
    teams_dir: str | None = None,
    limit: int = _BRIEFING_MEMORY_LIMIT,
    extra_lines: tuple[str, ...] = (),
) -> str:
    """The hook-computed session briefing, at most MEMORY_BRIEFING_MAX_BYTES; never raises.

    Lines: memory count and newest records, calibration gate state, `extra_lines`
    verbatim, and a secretary line when none is on the team.
    """
    LEARNING_II_MIN_MATCHES = _LEARNING_II_MIN_MATCHES
    key = memory_project_key(project_dir)
    try:
        facts = _read_store(db_path or memory_db_path(), key, limit) if key else {
            "error": "project unresolved"}
    except Exception:
        facts = {"error": "not read"}
    lines = ["Session briefing (hook):"]
    if "error" in facts:
        lines.append(f"- Memory ({key or '?'}): {facts['error']}.")
        lines.append("- Calibration (Learning II): not determined.")
    else:
        entries = [_memory_line(r) for r in facts["rows"]]
        if facts["total"]:
            lines.append(f"- Memory ({key}): {facts['total']} records; newest: "
                         + "; ".join(entries) + ".")
        else:
            lines.append(f"- Memory ({key}): no records for this project.")
        met = "met" if facts["calibration"] >= LEARNING_II_MIN_MATCHES else "not met"
        lines.append(f"- Calibration (Learning II): {facts['calibration']} "
                     f"orchestration_calibration records; gate {met} "
                     f"(threshold {LEARNING_II_MIN_MATCHES}).")
    lines.extend(line for line in extra_lines if line)
    if team_name and not team_has_secretary(team_name, teams_dir):
        lines.append("- Secretary: not spawned. Spawn it before the first memory "
                     "query or harvest (/repacted:bootstrap, On-demand secretary).")
    text = "\n".join(lines)
    while len(text.encode("utf-8")) > MEMORY_BRIEFING_MAX_BYTES and len(lines) > 1:
        # Shed the newest-entries detail first; the counts and the secretary
        # line are the parts a reader acts on.
        if "; newest: " in lines[1]:
            head, _, tail = lines[1].partition("; newest: ")
            parts = tail.rstrip(".").split("; ")
            lines[1] = head + ("; newest: " + "; ".join(parts[:-1]) + "." if len(parts) > 1 else ".")
        else:
            lines[1] = lines[1][:max(0, MEMORY_BRIEFING_MAX_BYTES // 2)]
        text = "\n".join(lines)
    return text.encode("utf-8")[:MEMORY_BRIEFING_MAX_BYTES].decode("utf-8", "ignore")


# ─── Working Memory rebuild at a session boundary ───
# Runs the pact-memory CLI `sync` detached (it may import or install the embedding backend);
# skipped when a resume claim surfaced. The envelope lands in the session dir and the next
# briefing reports it.

WORKING_MEMORY_SYNC_RESULT = "working-memory-sync.json"

# Sources that start a session. compact and clear continue one, and the
# secretary never rebuilt there either (it was re-engaged, not respawned).
_WORKING_MEMORY_REBUILD_SOURCES = frozenset({"startup", "resume"})


def _spawn_detached(command: list[str], cwd: str, out) -> None:
    subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                     stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)


# Captured at import so a test that replaces _spawn_detached is not mistaken
# for the real spawner by the pytest refusal below.
_REAL_SPAWN = _spawn_detached


def start_working_memory_rebuild(
    source: str,
    claim_surfaced: bool,
    project_dir: str,
    session_dir: str,
    plugin_root: str,
) -> str | None:
    """Start the session-boundary Working Memory rebuild, or hold it."""
    if source not in _WORKING_MEMORY_REBUILD_SOURCES:
        return None
    if claim_surfaced:
        return ("- Working Memory: not rebuilt, a resumption claim surfaced at "
                "session start (held for the arc).")
    if not session_dir:
        return "- Working Memory: not rebuilt, session directory unresolved."
    cli = Path(plugin_root or "") / "skills" / "pact-memory" / "scripts" / "cli.py"
    if not plugin_root or not cli.is_file():
        return "- Working Memory: not rebuilt, pact-memory CLI not found."
    spawn = _spawn_detached
    if spawn is _REAL_SPAWN and os.environ.get("PYTEST_CURRENT_TEST"):
        # ~50 test modules drive session_init.main(); each would start a real
        # sync child (embedding import, possible pip install). Tests inject.
        return "- Working Memory: not rebuilt, detached sync disabled under pytest."
    result = Path(session_dir) / WORKING_MEMORY_SYNC_RESULT
    try:
        with open(result, "w", encoding="utf-8") as out:
            spawn([sys.executable, str(cli), "sync"], project_dir or None, out)
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        return f"- Working Memory: not rebuilt, sync did not start ({type(exc).__name__})."
    return ("- Working Memory: rebuild started (pact-memory sync, background); "
            f"outcome in {result}.")


def _first_envelope(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict) and "ok" in value:
            return value
    return None


def working_memory_rebuild_outcome(session_dir: str) -> str | None:
    """The briefing line for this session's start rebuild, read back from its
    result file; None when no rebuild was started in this session. Total."""
    try:
        if not session_dir:
            return None
        path = Path(session_dir) / WORKING_MEMORY_SYNC_RESULT
        if not path.is_file():
            return None
        envelope = _first_envelope(path.read_text(encoding="utf-8", errors="replace")[:65536])
    except Exception:  # noqa: BLE001
        return None
    if envelope is None:
        return f"- Working Memory: the session-start rebuild left no result (still running or crashed; see {path})."
    if envelope.get("ok"):
        result = envelope.get("result") or {}
        return (f"- Working Memory: session-start rebuild {result.get('sync_status')}, "
                f"{result.get('projected')} projected (project {result.get('project_id')}).")
    message = str(envelope.get("message", ""))[:160]
    return f"- Working Memory: session-start rebuild failed: {envelope.get('error')}: {message}"
