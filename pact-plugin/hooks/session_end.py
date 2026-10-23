#!/usr/bin/env python3
"""SessionEnd hook: writes the session_end journal event and reaps stale state.

Actions: session_end event (with an unpaused-PR warning when one applies); dual-TTL cleanup
of session dirs (30 days, 180 for paused/refreshed) that keeps any dir whose journal holds
un-harvested HANDOFFs; 30-day reapers for ~/.claude/teams (pact-* names only) and
~/.claude/tasks (skip-set guarded); prune of the teammate registry. Best-effort: never
raises, never blocks termination, never touches project files.

Map: get_project_slug, check_unpaused_pr, _is_paused_session, _is_checkpointed_session,
_journal_carries_unharvested_handoffs, cleanup_old_sessions, _reap_slug_dir,
_dir_max_child_mtime, cleanup_old_teams, cleanup_old_tasks, _assemble_tasks_skip_set,
_is_safe_team_segment, _prune_registry_dead_teams, main.
Rationale: the design notes
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import time
from pathlib import Path

# Add hooks directory to path for shared package imports
_hooks_dir = Path(__file__).parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shared.error_output import hook_error_json
from shared.gh_helpers import check_pr_state
import shared.pact_context as pact_context
from shared.pact_context import (
    _UNSAFE_SLUG_CHARS_RE,
    get_project_dir,
    get_session_id,
    get_team_name,
    project_slug,
)
from shared.session_journal import (
    append_event,
    make_event,
    read_events,
    read_last_event_from,
)

from shared.session_state import is_safe_path_component
from shared.session_registry import get_registry_path as _get_registry_path
from shared.session_registry import registry_lock as _registry_lock
from shared.paths import get_claude_config_dir
from shared.task_utils import get_task_list

# Suppress false "hook error" display in Claude Code UI on bare exit paths
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def get_project_slug() -> str:
    """Derive project slug from session context (resolved basename of
    project_dir, via the shared derivation every session path uses)."""
    return project_slug(get_project_dir())


def check_unpaused_pr(
    tasks: list[dict] | None,
    project_slug: str,
) -> str | None:
    """Warning string when the current PR was dispatched after the last pause (or never paused)
    and is not merged/closed; else None. Detection only.
    """
    if not project_slug:
        return None

    # Fix B (#453): wrap-up/pause consolidation in this session silences the warning (cheapest
    # check first; an unreadable journal falls through).
    if read_events("session_consolidated"):
        return None

    paused_events = read_events("session_paused")
    review_events = read_events("review_dispatch")

    # A pause covers a PR only at or after its dispatch; `>=` biases ties toward silence.
    if paused_events and review_events:
        last_pause_ts = paused_events[-1].get("ts", "")
        last_review_ts = review_events[-1].get("ts", "")
        if last_pause_ts >= last_review_ts:
            return None  # Most recent PR was paused; safe.
        # else fall through — current PR is unpaused
    elif paused_events:
        return None  # Paused, no PRs at all — safe.

    # Check journal for PR creation
    pr_number = None
    if review_events:
        # Use the most recent review_dispatch event's PR number
        pr_number = review_events[-1].get("pr_number")

    # Fallback: scan task metadata for PR indicators (safety net for PRs
    # not tracked through the review workflow journal events)
    if not pr_number and tasks:
        for task in tasks:
            metadata = task.get("metadata") or {}
            if metadata.get("pr_number") is not None:
                pr_number = metadata["pr_number"]
                break
            handoff = metadata.get("handoff") or {}
            for value in handoff.values():
                if isinstance(value, str):
                    match = re.search(r'github\.com/[^/]+/[^/]+/pull/(\d+)', value)
                    if match:
                        pr_number = match.group(1)
                        break
            if pr_number:
                break

    if not pr_number:
        return None

    # Fix A (#453): live PR state, last and only when cheaper signals fell through; "" (gh
    # unavailable) falls through to the warning.
    pr_state = check_pr_state(pr_number)
    if pr_state in ("MERGED", "CLOSED"):
        return None

    return (
        f"Session ended without memory consolidation. "
        f"PR #{pr_number} may still be open but pause-mode was not run. "
        f"Run /repacted:pause or /repacted:wrap-up in next session."
    )


# \Z, not $: $ also matches before a trailing newline, which would admit a crafted name.
_UUID_PATTERN = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z'
)

# Teams reaper stays ^pact- scoped: the platform owns teardown of session-* dirs, so widening
# would rmtree live platform dirs. Other names belong to other tooling. \Z as above.
_TEAM_NAME_PATTERN = re.compile(r'^pact-[a-f0-9-]+\Z')

# Default threshold for active (non-paused) session directory cleanup.
# 30 days balances disk usage (~50KB × 30 sessions = ~1.5MB) against
# cross-session recovery value.
_SESSION_MAX_AGE_DAYS = 30

# Longer TTL for paused sessions (unconsolidated work), still finite.
_PAUSED_SESSION_MAX_AGE_DAYS = 180


def _is_paused_session(session_dir: str) -> bool:
    """True iff the journal has ever recorded session_paused (existence, no ts comparison).

    A timestamp comparison deleted paused state on the pause->quit race and on equal-second
    ties. Unreadable journal -> False (standard TTL).
    """
    return read_last_event_from(session_dir, "session_paused") is not None


def _is_checkpointed_session(session_dir: str) -> bool:
    """True iff the journal ever recorded session_paused or session_refreshed (existence only;
    refresh->quit has the same race). Protects the journal only, not tasks/teams.
    """
    # Composed on _is_paused_session (not inlined) so there is exactly ONE
    # paused-existence predicate — the two can never drift.
    return (_is_paused_session(session_dir)
            or read_last_event_from(session_dir, "session_refreshed") is not None)


def _journal_carries_unharvested_handoffs(session_dir: str) -> bool | None:
    """Tri-state: does this journal hold an agent_handoff after its last session_consolidated?

    True: yes (or no consolidation at all). False: no, or no journal, or no line parses
    (accepted bound: a file whose only line is torn is removed). None: the read raised, or a torn
    line beside a well-formed event (caller must refuse). Opens the file itself (not
    read_last_event_from, which conflates absent and unreadable) and catches OSError only.
    """
    journal = Path(session_dir) / "session-journal.jsonl"
    try:
        if not journal.exists():
            return False
        has_handoff = False
        saw_event = False
        saw_torn_line = False
        # utf-8-sig strips a BOM that would make the first line unparseable (and could erase a journal).
        with journal.open(encoding="utf-8-sig", errors="replace") as fh:
            for line in fh:
                # Parse every line; no substring pre-filter (an escaped `type` would be missed and the dir removed).
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError, RecursionError):
                    # A torn line is not an absent event (crash mid-write); decide at the end of the file.
                    if line.strip():
                        saw_torn_line = True
                    continue
                if not isinstance(event, dict):
                    continue
                saw_event = True
                event_type = event.get("type")
                if event_type == "session_consolidated":
                    # Reset, not return: work continues after a refresh in the same file.
                    has_handoff = False
                elif event_type == "agent_handoff":
                    has_handoff = True
        if has_handoff:
            return True
        if saw_torn_line and saw_event:
            # A journal with an unreadable line: un-evaluable, refuse.
            return None
        return False
    except OSError:
        return None


def cleanup_old_sessions(
    project_slug: str,
    current_session_id: str,
    sessions_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
    paused_max_age_days: int = _PAUSED_SESSION_MAX_AGE_DAYS,
    old_slug: str | None = None,
) -> None:
    """Remove stale session dirs under project_slug (and old_slug when it differs) with a dual TTL.

    A dir's age runs from the newer of its own mtime and its newest direct child's; a dir whose
    children cannot be observed is kept.
    Checkpointed sessions use paused_max_age_days (180), others max_age_days (30). A dir past
    its TTL is removed only when _journal_carries_unharvested_handoffs is False (True and None
    keep it). Slugs go through the writers' sanitiser. Skips the current session and non-UUID
    names. Never raises.
    """
    if not project_slug or not current_session_id:
        return

    if sessions_dir is None:
        sessions_dir = str(get_claude_config_dir() / "pact-sessions")

    slugs = [_UNSAFE_SLUG_CHARS_RE.sub("_", project_slug)]
    if old_slug:
        safe_old = _UNSAFE_SLUG_CHARS_RE.sub("_", old_slug)
        if safe_old != slugs[0]:
            slugs.append(safe_old)
    for slug in slugs:
        _reap_slug_dir(
            Path(sessions_dir) / slug,
            current_session_id,
            max_age_days,
            paused_max_age_days,
        )


def _reap_slug_dir(
    slug_dir: Path,
    current_session_id: str,
    max_age_days: int,
    paused_max_age_days: int,
) -> None:
    """Sweep one slug directory on the cleanup_old_sessions contract. Never
    raises."""
    if not slug_dir.exists():
        return

    try:
        for entry in slug_dir.iterdir():
            # Skip symlinks (lstat): a planted link must not pin a dir alive or leak its target's mtime.
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                continue
            if not _UUID_PATTERN.match(entry.name):
                continue
            if entry.name == current_session_id:
                continue
            try:
                # Age by the newer of the dir's own mtime and its newest child's: a journal
                # append or in-place rewrite does not move the dir's mtime, so a live session
                # would otherwise age out. None means no child could be observed: keep the dir.
                child_mtime = _dir_max_child_mtime(entry, glob="*")
                if child_mtime is None:
                    continue
                newest = max(entry.stat().st_mtime, child_mtime)
                age_days = (time.time() - newest) / 86400
                # Select TTL per entry: checkpointed sessions (paused OR
                # refreshed) get the extended threshold; active sessions
                # get the standard one.
                threshold = (
                    paused_max_age_days
                    if _is_checkpointed_session(str(entry))
                    else max_age_days
                )
                if age_days > threshold:
                    # `is False` is load-bearing: True and None must each keep the directory.
                    if _journal_carries_unharvested_handoffs(
                        str(entry)
                    ) is False:
                        shutil.rmtree(entry, ignore_errors=True)
            except Exception:
                # Broad catch per entry (e.g. RecursionError from nested JSON): skip it, keep the loop going.
                continue
    except OSError:
        pass


def _dir_max_child_mtime(entry: Path, glob: str = "*.json") -> float | None:
    """Max mtime of children of `entry` matching `glob`; the parent mtime for an empty dir; None
    when age cannot be observed (callers skip). In-place rewrites do not bump the parent's
    mtime, hence the child walk. Uses lstat; never raises.
    """
    latest = None
    saw_any_child = False
    try:
        for child in entry.glob(glob):
            saw_any_child = True
            try:
                # lstat: a symlinked child must not pin the parent's age to its target.
                mtime = child.lstat().st_mtime
            except OSError:
                continue
            latest = mtime if latest is None else max(latest, mtime)
    except OSError:
        pass
    # Any observed child answers, even an mtime at or below 0 (1970 or earlier).
    if latest is not None:
        return latest
    # Empty dir -> parent mtime; children seen but none stat-able -> None (do not false-reap).
    if saw_any_child:
        return None
    try:
        # lstat so the helper stays safe if a caller forgets the symlink filter.
        return entry.lstat().st_mtime
    except OSError:
        # Can neither observe children nor the parent — sentinel.
        return None


def cleanup_old_teams(
    current_team_name: str,
    teams_base_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
) -> tuple[int, int]:
    """Remove stale ~/.claude/teams dirs (#412 Fix B). Returns (reaped, skipped).

    Guards: ^pact- names only (never platform session-* dirs), skip current_team_name, and an
    empty current_team_name reaps nothing. Age is the max child mtime. `reaped` counts attempted
    rmtree calls (ignore_errors=True). Never raises.
    """
    if not current_team_name:
        return 0, 0

    if teams_base_dir is None:
        teams_base_dir = str(get_claude_config_dir() / "teams")
    base = Path(teams_base_dir)
    if not base.exists():
        return 0, 0

    reaped = 0
    skipped = 0
    try:
        for entry in base.iterdir():
            # Skip symlinks (lstat): a planted link must not pin a dir alive or leak its target's mtime.
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                continue
            # Only pact- names; never widen to the platform's session-* dirs.
            if not _TEAM_NAME_PATTERN.match(entry.name):
                continue
            # Case-insensitive skip so producer case drift cannot reap the current team.
            if entry.name.lower() == current_team_name.lower():
                continue
            try:
                mtime = _dir_max_child_mtime(entry, glob="*")
                # None: age unobservable -> skipped.
                if mtime is None:
                    skipped += 1
                    continue
                age_days = (time.time() - mtime) / 86400
                if age_days > max_age_days:
                    shutil.rmtree(entry, ignore_errors=True)
                    reaped += 1
            except OSError:
                skipped += 1
                continue
    except OSError:
        pass
    return reaped, skipped


def cleanup_old_tasks(
    skip_names: set[str],
    tasks_base_dir: str | None = None,
    max_age_days: int = _SESSION_MAX_AGE_DAYS,
) -> tuple[int, int]:
    """Remove stale ~/.claude/tasks dirs not in skip_names (#412 Fix B). Returns (reaped, skipped).

    Fails closed: an empty or all-blank skip_names reaps nothing. Age is the max *.json mtime.
    Never raises.
    """
    if not skip_names or all(not n for n in skip_names):
        return 0, 0

    if tasks_base_dir is None:
        from shared.task_store import TaskStore
        tasks_base_dir = str(TaskStore().root)
    base = Path(tasks_base_dir)
    if not base.exists():
        return 0, 0

    reaped = 0
    skipped = 0
    try:
        for entry in base.iterdir():
            # Skip symlinks (lstat): a planted link must not pin a dir alive or leak its target's mtime.
            if entry.is_symlink():
                continue
            if not entry.is_dir():
                continue
            if entry.name in skip_names:
                continue
            try:
                mtime = _dir_max_child_mtime(entry, glob="*.json")
                # Cycle-5 sentinel check: `None` means the helper couldn't
                # determine the dir's effective age. Skip rather than
                # false-reap under a permission regression.
                if mtime is None:
                    skipped += 1
                    continue
                age_days = (time.time() - mtime) / 86400
                if age_days > max_age_days:
                    shutil.rmtree(entry, ignore_errors=True)
                    reaped += 1
            except OSError:
                skipped += 1
                continue
    except OSError:
        pass
    return reaped, skipped


def _assemble_tasks_skip_set(
    team_name: str,
    task_list_id: str,
    session_id: str,
) -> set[str]:
    """Skip-set for cleanup_old_tasks from team_name, CLAUDE_CODE_TASK_LIST_ID and session_id.

    Every channel passes the same positive allowlist (a crafted value cannot slip a separator or
    line terminator into a path key); a failing value is dropped and "" is discarded. An empty
    result means: do not run the tasks reaper.
    """
    safe_team_name = team_name if is_safe_path_component(team_name) else ""
    safe_task_list_id = (
        task_list_id if is_safe_path_component(task_list_id) else ""
    )
    safe_session_id = session_id if is_safe_path_component(session_id) else ""
    skip_names = {safe_team_name, safe_task_list_id, safe_session_id}
    skip_names.discard("")
    return skip_names


def _is_safe_team_segment(team: str) -> bool:
    """True iff `team` is one safe path component (no NUL/C0/DEL, no separator, not . or ..).
    The registry's @team is self-asserted. Never raises.
    """
    if not team:
        return False
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in team):
        return False
    if "/" in team or "\\" in team:
        return False
    if team in (".", ".."):
        return False
    return True


def _prune_registry_dead_teams(
    registry_path: Path | None = None,
    teams_dir: Path | None = None,
) -> int:
    """Drop teammate-registry lines whose @team has no live dir under teams_dir.

    Keeps the registry bounded. Malformed lines, lines without @, and unsafe @team values are
    dropped. Never raises; rewrites via a 0600 temp renamed into place. Six numbered safety
    guarantees below; see the design notes. Returns the number pruned.
    """
    if registry_path is None:
        registry_path = _get_registry_path()
    if teams_dir is None:
        teams_dir = get_claude_config_dir() / "teams"
    # SAFETY GUARANTEE 1 of 6 — an unobservable teams root prunes nothing. os.stat, not
    # is_dir(): is_dir() swallows OSError and would call every live team dead.
    try:
        st = os.stat(teams_dir)
    except OSError:
        return 0  # cannot observe the teams root → prune nothing
    # SAFETY GUARANTEE 2 of 6 — a root that is not a directory is refused BEFORE the registry
    # is read (guarantee 6 also catches ENOTDIR per entry; this one holds the ordering).
    if not stat.S_ISDIR(st.st_mode):
        return 0  # not a directory → the per-entry probes below are meaningless

    # The registry lock spans read to rename: register() appends under the
    # same lock, so a line appended mid-prune is never left in the replaced
    # inode.
    with _registry_lock(registry_path):
        try:
            # SAFETY GUARANTEE 4 of 6 — refuse a symlink at the registry path (os.replace would
            # silently swap a deliberate link for a regular file).
            if not registry_path.exists() or registry_path.is_symlink():
                return 0
            raw = registry_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # SAFETY GUARANTEE 5 of 6 — invalid UTF-8 prunes nothing. UnicodeDecodeError is a
            # ValueError, not OSError; do not widen to ValueError.
            return 0

        kept_lines: list[str] = []
        pruned = 0
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            keep = False
            try:
                obj = json.loads(stripped)
                value = obj.get("value") if isinstance(obj, dict) else None
                if isinstance(value, str) and "@" in value:
                    team = value.partition("@")[2]
                    # Validate the self-asserted @team before building any path.
                    if _is_safe_team_segment(team):
                        try:
                            os.stat(teams_dir / team)
                            keep = True
                        except FileNotFoundError:
                            keep = False  # verified dead under this root
                        except OSError:
                            # SAFETY GUARANTEE 6 of 6 — an unobservable TEAM (PermissionError, NotADirectoryError) bails
                            # out pruning nothing; it must never reach the handler that drops the line.
                            return 0
            except (ValueError, RecursionError):
                # Malformed JSON only (a line nested too deep to parse included). Do NOT widen this back to OSError: that is
                # the route by which an unobservable team becomes a dropped line.
                keep = False
            if keep:
                kept_lines.append(stripped)
            else:
                pruned += 1

        if pruned == 0:
            return 0  # nothing stale → leave the file untouched (no needless rewrite)

        # Temp file + rename: the registry is never open for writing (an in-place O_TRUNC write could
        # leave 0 bytes on ENOSPC).
        # SAFETY GUARANTEE 3 of 6 — explicit fchmod 0600: os.replace does not keep the destination
        # mode and O_CREAT's mode is umask-masked. O_CREAT|O_EXCL refuses a planted symlink.
        tmp_path = registry_path.with_name(f"{registry_path.name}.{os.getpid()}.tmp")
        try:
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.fchmod(fd, 0o600)
                payload = ("\n".join(kept_lines) + "\n") if kept_lines else ""
                os.write(fd, payload.encode("utf-8"))
            finally:
                os.close(fd)
            os.replace(tmp_path, registry_path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass  # cleanup is best-effort; the registry is intact either way
            return 0  # never raise; the original file is untouched

        return pruned


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            input_data = {}

        pact_context.init(input_data)
        project_slug = get_project_slug()
        current_session_id = get_session_id()

        # Safety-net: warn if open PR detected but pause-mode wasn't run.
        # Returns a warning string (or None) so we can emit a single
        # session_end event with an optional `warning=` field.
        tasks = get_task_list()
        warning = check_unpaused_pr(
            tasks=tasks,
            project_slug=project_slug,
        )

        # Write a single session_end event to the journal (best-effort).
        # Wrapped in its own try/except so a journal failure does not skip
        # the cleanup steps that follow.
        try:
            event_kwargs = {"warning": warning} if warning else {}
            append_event(make_event("session_end", **event_kwargs))
        except Exception as e:
            print(f"Hook warning (session_end journal): {e}", file=sys.stderr)

        # Clean up stale session directories (dual TTL: 30d active, 180d paused)
        cleanup_old_sessions(
            project_slug=project_slug,
            current_session_id=current_session_id,
            old_slug=Path(get_project_dir()).name,
        )

        # Clean up stale ~/.claude/teams/ and ~/.claude/tasks/ (#412 Fix B).
        # Callsite short-circuit on empty team_name is the belt-and-suspenders
        # layer around the internal fail-closed guard.
        current_team_name = get_team_name()

        # Prune the teammate registry AFTER the teams reaper, so teams reaped now are pruned too.

        teams_r, teams_s = 0, 0
        tasks_r, tasks_s = 0, 0
        teams_reaper_ran = False
        tasks_reaper_ran = False
        if current_team_name:
            teams_r, teams_s = cleanup_old_teams(
                current_team_name=current_team_name,
            )
            teams_reaper_ran = True

        _prune_registry_dead_teams()

        # Skip-set from the three platform-key channels (see _assemble_tasks_skip_set).
        skip_names = _assemble_tasks_skip_set(
            team_name=current_team_name,
            task_list_id=os.environ.get("CLAUDE_CODE_TASK_LIST_ID", ""),
            session_id=current_session_id or "",
        )
        if skip_names:
            tasks_r, tasks_s = cleanup_old_tasks(
                skip_names=skip_names,
            )
            tasks_reaper_ran = True

        # Best-effort audit row. Per-reaper ran flags and TTLs tell "ran, found nothing" from
        # "short-circuited".
        try:
            append_event(make_event(
                "cleanup_summary",
                teams_reaped=teams_r,
                teams_skipped=teams_s,
                tasks_reaped=tasks_r,
                tasks_skipped=tasks_s,
                teams_ttl_days=_SESSION_MAX_AGE_DAYS,
                tasks_ttl_days=_SESSION_MAX_AGE_DAYS,
                teams_ran=teams_reaper_ran,
                tasks_ran=tasks_reaper_ran,
            ))
        except Exception as e:
            print(f"Hook warning (cleanup_summary journal): {e}", file=sys.stderr)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        print(f"Hook warning (session_end): {e}", file=sys.stderr)
        print(hook_error_json("session_end", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
