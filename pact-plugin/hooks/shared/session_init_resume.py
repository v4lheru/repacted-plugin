"""
Location: pact-plugin/hooks/shared/session_init_resume.py
Summary: SessionStart resume and compaction: the previous session's dir and
    start time, the kept start and team of a compaction, the stale compact
    summary clears, the resume pointer to this session's own summary, and the
    post-compaction directive.
Used by: hooks/session_init.py, shared/session_init_lead.py

Moved out of session_init.py unchanged.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared import compaction_owner, summary_head
from shared.constants import BOOTSTRAP_MARKER_NAME
from shared.pact_context import _build_session_path as build_session_path, project_slug
from shared.claude_md_manager import file_lock, resolve_project_claude_md_path
from shared.constants import (
    COMPACT_SUMMARY_ARCHIVE_PREFIX,
    COMPACT_SUMMARY_HEAD_NAME,
    COMPACT_SUMMARY_NAME,
    COMPACT_SUMMARY_ORPHAN_NAME,
    get_compact_summary_path,
)
from shared.paths import get_claude_config_dir
from shared.session_init_common import _SECRETARY_ON_DEMAND_CLAUSE
from shared.session_resume import has_unspent_refresh
from shared.task_utils import (
    build_post_compaction_checkpoint,
    find_active_agents,
    find_blockers,
    find_current_phase,
    find_feature_task,
)


def _validate_under_pact_sessions(path: str) -> str | None:
    """Reject extracted session paths that escape the pact-sessions root.

    Defense-in-depth against tampered CLAUDE.md content. The Session dir / Resume
    lines are user-editable text, so a malicious or accidentally corrupted file
    could point _extract_prev_session_dir at any filesystem location (e.g.
    /etc, /var, a sibling project's secrets). Callers consume the returned path
    to read journal events; an attacker who controlled the path could exfiltrate
    or trigger reads outside the PACT sessions tree.

    The check calls ``Path.resolve(strict=False)`` on both the candidate AND the
    sessions root so ``..`` segments are collapsed and symlinks followed before
    the containment check. A naive string-prefix comparison against
    ``str(Path(path))`` is NOT sufficient: ``Path()`` normalizes redundant
    slashes but leaves ``..`` segments intact, so ``~/.claude/pact-sessions/../../etc/passwd``
    would textually start with the prefix yet resolve outside the tree once the
    filesystem is asked to dereference it. ``resolve(strict=False)`` does the
    canonicalization explicitly and does NOT require the path to exist.

    The containment check uses ``Path`` comparison semantics
    (``candidate == sessions_root or sessions_root in candidate.parents``)
    instead of string prefix + ``os.sep``. This eliminates the sibling-prefix
    collision class (``pact-sessions-evil`` vs ``pact-sessions``) by design,
    rather than relying on an explicit separator guard.

    Returns the original string on success and None on rejection (silent
    fail-closed — callers already treat None as "no previous session").
    """
    try:
        sessions_root = (get_claude_config_dir() / "pact-sessions").resolve()
        candidate = Path(path).resolve(strict=False)
        if candidate == sessions_root or sessions_root in candidate.parents:
            return path
    except (TypeError, ValueError, OSError):
        pass
    return None


def _extract_prev_session_dir(project_dir: str) -> str | None:
    """
    Extract the previous session's directory path from the project CLAUDE.md.

    Reads the "## Current Session" block written by update_session_info()
    and extracts the session dir from lines like
    "- Session dir: `~/.claude/pact-sessions/PACT-Plugin/abc12345-...`".

    Honors both supported project CLAUDE.md locations
    ($project_dir/.claude/CLAUDE.md preferred, $project_dir/CLAUDE.md legacy).

    Falls back to deriving the path from the Resume line's session_id +
    the resolved project slug if the Session dir line is absent (backward
    compat with sessions that wrote team name but not session dir) or names
    a directory that is no longer there (the line was written before the
    directory moved to the resolved slug).

    Both extracted paths (primary and fallback) are validated against the
    canonical pact-sessions prefix via _validate_under_pact_sessions before
    being returned. Defense-in-depth against tampered CLAUDE.md content.

    This is used to locate the previous session's journal for resume context
    and pause state detection. Returns None if neither CLAUDE.md exists, the
    session dir can't be extracted, or the extracted path is outside the
    pact-sessions tree.

    Args:
        project_dir: CLAUDE_PROJECT_DIR path

    Returns:
        Previous session directory path string, or None if not found
    """
    if not project_dir:
        return None

    try:
        claude_md, source = resolve_project_claude_md_path(project_dir)
        # source == "new_default" means neither location exists -- nothing to read
        if source == "new_default":
            return None

        # Acquire the same sidecar file_lock that update_session_info
        # uses for its read-mutate-write pass. A concurrent write (e.g.,
        # from another session_init invocation racing the WRITE step at
        # L1148) could otherwise produce a torn read here, surfacing as
        # either a corrupted Session-dir match or a fallback-regex hit
        # on a half-written SESSION_START block. The lock serializes
        # against the writer. Re-entrancy is safe: this read at step 5a
        # runs BEFORE update_session_info (step 5b) acquires its own
        # lock. No nesting; fail-open on TimeoutError per file_lock
        # contract.
        try:
            with file_lock(claude_md):
                content = claude_md.read_text(encoding="utf-8")
        except TimeoutError:
            return None

        # Resume line format: "- Resume: `claude --resume <session_id>`"
        resume_match = re.search(
            r'- Resume:\s*`claude --resume\s+([0-9a-f-]+)`', content
        )

        # Primary: match "- Session dir: `<path>`" in the Current Session block.
        match = re.search(r'- Session dir:\s*`([^`]+)`', content)
        if match:
            raw = match.group(1)
            # Expand ~ to actual home directory
            if raw.startswith("~/"):
                expanded = str(Path.home() / raw[2:])
            else:
                expanded = raw
            validated = _validate_under_pact_sessions(expanded)
            # A validated line naming a directory that is gone falls through
            # to the derivation below; a rejected line still returns None.
            if validated is None or Path(validated).is_dir():
                return validated
        elif resume_match:
            # The primary regex missed on a session block that WAS written
            # (it carries a Resume line). This is usually benign (older
            # sessions wrote only the Resume line, not the Session dir line —
            # handled by the fallback just below), but it is also how a
            # silent format regression would present. Log a one-line stderr
            # warning so future drift in the SESSION_START block surfaces
            # during testing instead of silently degrading to the fallback.
            # SILENT ON AN EMPTY BLOCK: the managed template is created
            # earlier in this same run with an empty Current Session block,
            # and a teammate never fills it, so warning on any miss printed
            # this line on every first start and every teammate start.
            print(
                "session_init: _extract_prev_session_dir regex failed on "
                "existing CLAUDE.md, falling back to Resume-line; file may "
                "have unexpected format",
                file=sys.stderr,
            )

        # Fallback: derive from Resume line session_id + project root basename.
        if resume_match:
            session_id = resume_match.group(1)
            # Same slug derivation and sanitisation as every session path,
            # so the fallback lands on the directory the writers used.
            derived = str(
                build_session_path(project_slug(project_dir), session_id)
            )
            return _validate_under_pact_sessions(derived)

    except (IOError, OSError):
        pass
    return None


# The "Started" line update_session_info writes, in its exact timestamp shape.
_SESSION_STARTED_RE = re.compile(
    r"^- Started: (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)$", re.MULTILINE
)


def _extract_session_started(project_dir: str) -> str | None:
    """The "Started" value in the project CLAUDE.md's Current Session block.

    None when there is no CLAUDE.md, no such line, or the lock times out. Only
    the exact shape update_session_info writes is returned, so no other text
    from the file can reach the block it rewrites.
    """
    if not project_dir:
        return None
    try:
        claude_md, source = resolve_project_claude_md_path(project_dir)
        if source == "new_default":
            return None
        with file_lock(claude_md):
            content = claude_md.read_text(encoding="utf-8")
    except (OSError, TimeoutError):
        return None
    match = _SESSION_STARTED_RE.search(content)
    return match.group(1) if match else None


def _kept_started_at(session_id: str, project_dir: str) -> str | None:
    """The started_at already in this session's context file, or None.

    Reads the file build_context_cache writes. None when it is missing,
    unreadable, or not a timezone-aware ISO-8601 time.
    """
    try:
        path = (
            build_session_path(project_slug(project_dir), str(session_id))
            / "pact-session-context.json"
        )
        value = json.loads(path.read_text(encoding="utf-8")).get("started_at")
        return value if datetime.fromisoformat(value).tzinfo else None
    except Exception:
        return None


def _kept_team(session_id: str, project_dir: str) -> str | None:
    """The team_name already in this session's context file, or None.

    Read before build_context_cache replaces it. The caller keeps it only
    while compaction_keeps_team proves it.
    """
    try:
        path = (
            build_session_path(project_slug(project_dir), str(session_id))
            / "pact-session-context.json"
        )
        value = json.loads(path.read_text(encoding="utf-8")).get("team_name")
        return value if isinstance(value, str) and value else None
    except Exception:
        return None


def _bootstrap_already_ran(session_dir: str) -> bool:
    """True when the bootstrap gate's own marker check passes for session_dir.

    shared.marker_schema.is_marker_set is the function the gate enforces, not a
    copy: a touched or unsigned marker does not count. It is imported at call
    time, and anything raised reads as not set, which keeps the bootstrap
    directive.
    """
    if not session_dir:
        return False
    try:
        from shared.marker_schema import is_marker_set
        return is_marker_set(Path(session_dir)) is True
    except BaseException:
        return False


def _clear_bootstrap_marker(session_path: Path) -> None:
    """Unlink the bootstrap-complete marker at ``session_path``.

    Scope is intentionally narrow: ONLY the marker file is removed. The
    team config (``~/.claude/teams/{team_name}/config.json``) is NOT
    touched here and persists across ``/clear``. Consequence: the
    ``bootstrap_marker_writer`` UserPromptSubmit hook re-creates the
    marker on the next prompt without orchestrator intervention, because
    the writer's pre-conditions (team config + secretary in members[])
    are still observable on disk.

    Fail-open: any ``OSError`` is swallowed so session init does not
    block on cleanup.
    """
    try:
        (session_path / BOOTSTRAP_MARKER_NAME).unlink(missing_ok=True)
    except OSError:
        pass  # Fail-open: don't block session init for marker cleanup


def _adopt_old_slug_session_dir(session_id: str, project_dir: str) -> bool:
    """Move a session dir keyed under the UNRESOLVED project basename to the
    slug every session path now derives, so a session launched through a
    symlink keeps its journal across the slug change.

    Runs before any writer creates the new-slug dir. Adopts only when the
    two slugs differ, the old path is a real directory (not a symlink), and
    the new path is absent or an empty directory. An existing non-empty
    directory, file or symlink at the new path is left alone, never
    clobbered or merged: the state there is what every reader already
    trusts, and the old dir stays where the old readers can still find it.
    Fail-open: any OSError leaves both paths as they were. Returns True iff
    the directory moved.
    """
    if not session_id or not project_dir:
        return False
    old_slug = Path(project_dir).name
    if not old_slug:
        return False
    old = build_session_path(old_slug, str(session_id))
    new = build_session_path(project_slug(project_dir), str(session_id))
    if old == new:
        return False
    try:
        if old.is_symlink() or not old.is_dir():
            return False
        if new.is_symlink():
            return False
        if new.exists() and not (new.is_dir() and not any(new.iterdir())):
            return False
        new.parent.mkdir(parents=True, exist_ok=True)
        os.rename(old, new)
    except OSError:
        return False
    print(f"session_init: adopted session dir {old} -> {new}", file=sys.stderr)
    return True


# Root-drained artifact prefix: what _archive_stale_compact_summary names
# the moved ROOT-singleton bytes when it drains them into a session dir.
# Distinct from every real archive name the plugin writes, but KEEPING the
# compact-summary stem so globbing consumers (the legacy-drain tests) still
# find the drained artifact as a compact-summary file. The resume pointer's
# strict stamp shape (_ARCHIVE_STAMP_SHAPE_RE) still never matches it — the
# segment after the stem is "root-drained-", not a timestamp — so drained
# bytes can never be named as this session's own compaction output.
_ROOT_DRAINED_SUMMARY_PREFIX = "compact-summary-root-drained-"


def _stale_summary_destination(session_id: str, project_dir: str) -> Path:
    """Where a stale compact summary goes when this hook clears the path.

    Session-scoped when the session can be identified, which is the normal
    case — but under a DISTINCT root-drained prefix, NOT the archive
    convention the session's own compactions produce: the drained bytes are
    ROOT-singleton bytes (degraded or legacy writes with no attributable
    producer), so the resume pointer's strict-shape selector must never name
    them as if this session had compacted. A distinct prefix makes that
    exclusion structural — the selector's shape admits only plugin-written
    archive names, and this name is not one by construction, no deny-list.
    Falls back to a single fixed-name slot in the sessions root, NEVER a
    timestamped one — see COMPACT_SUMMARY_ORPHAN_NAME for the bound and the
    trade it accepts.

    build_session_path is used rather than get_session_dir() because the
    context cache is not built until later in main(); the bootstrap-marker
    block below resolves a session path the same way, in the same window.
    """
    if session_id and project_dir:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        return build_session_path(project_slug(project_dir), str(session_id)) / (
            f"{_ROOT_DRAINED_SUMMARY_PREFIX}{stamp}.txt"
        )
    return get_compact_summary_path().parent / COMPACT_SUMMARY_ORPHAN_NAME


def _archive_stale_compact_summary(session_id: str, project_dir: str) -> None:
    """Clear the ROOT compact-summary singleton BY MOVING IT, never deleting.

    Since #1504 the writer scopes its file to the session that produced it,
    so the root singleton is fed only by DEGRADED writes (unidentifiable
    frames) and pre-upgrade legacy bytes. This sweep is the one-time drain
    for both: a MOVE empties the root path, so nothing can process the bytes
    twice, and only unattributable writes can ever re-feed it.

    The path must end up clear: the summary is single-use, and a copy left
    there is processed a second time by the next briefing in the same session.
    The bytes must survive: the writer leaves no second copy anywhere, so an
    unlink destroys the only copy of anything the secretary did not archive —
    which is precisely the secretary's documented fallback branch, the one
    case where no archive was ever made. A move satisfies both in ONE
    operation, so it cannot half-fail into the state this repairs.

    Fail-open, and the direction is deliberate: on any OSError the bytes stay
    where they are. That risks a summary processed twice, which costs a
    duplicate paragraph. Clearing the path on a failed move would risk losing
    it, which is unrecoverable.
    """
    try:
        summary = get_compact_summary_path()
        if not summary.exists():
            return
        destination = _stale_summary_destination(session_id, project_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        summary.replace(destination)
        summary_head.discard(summary.parent)
    except OSError:
        pass  # Fail-open: keep the bytes; never block session init for cleanup


def _settle_staged_summaries(session_id: str, project_dir: str) -> None:
    """Settle the compaction summaries postcompact_archive staged for this session.

    Runs before either clear below, so a lead summary still waiting to be
    promoted is promoted first and then cleared with the rest. Never raises.
    """
    if not (session_id and project_dir):
        return
    try:
        compaction_owner.settle(
            str(build_session_path(project_slug(project_dir), str(session_id)))
        )
    except Exception:
        pass


def _archive_own_dir_stale_summary(session_id: str, project_dir: str) -> None:
    """Clear THIS session's stale compact summary BY MOVING IT in place.

    Session-scoped twin of _archive_stale_compact_summary (#1504): a
    compaction followed by a context reset leaves the file in the session's
    own directory. Moving it to the timestamped archive convention of
    COMPACT_SUMMARY_ARCHIVE_PREFIX in the SAME directory empties the
    single-use slot and keeps the bytes where its own session can still
    find them.

    Composes build_session_path + COMPACT_SUMMARY_NAME DIRECTLY. The resolver
    (pact_context.resolve_compact_summary_path) is deliberately NOT used: its
    degradation leg retargets the root singleton, which the OTHER clear above
    already serves — routing through it would double-drain one object and
    never touch the other.

    Fail-open, same direction as the root clear: on OSError the bytes stay
    put. A summary processed twice costs a duplicate paragraph; a summary
    lost is unrecoverable.
    """
    if not (session_id and project_dir):
        return
    try:
        summary = (
            build_session_path(project_slug(project_dir), str(session_id))
            / COMPACT_SUMMARY_NAME
        )
        if not summary.exists():
            return
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        destination = summary.parent / f"{COMPACT_SUMMARY_ARCHIVE_PREFIX}{stamp}.txt"
        summary.replace(destination)
        summary_head.discard(summary.parent)
    except OSError:
        pass  # Fail-open: keep the bytes; never block session init for cleanup


# Strict plugin-written archive shape: compact-summary-YYYY-MM-DDTHH-MM-SS.txt.
# Every archive writer produces exactly this (both clears below via
# datetime.now().strftime("%Y-%m-%dT%H-%M-%S"), the secretary's archive step
# via its instructed <YYYY-MM-DDTHH-MM-SS> rename). Selection admits nothing
# else because the ARCHIVE FILENAME is attacker-controlled bytes reaching a
# render sink: the glob's * matches newlines, so a crafted name embedding a
# forged directive after "compact-summary-x" and a newline would carry its
# bytes into the SessionStart instruction channel (F-SEC-1, PR #1527 review).
# Skipping a nonconforming name only costs the clause — fail-safe, no pointer.
_ARCHIVE_STAMP_SHAPE_RE = re.compile(
    r"compact-summary-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.txt"
)

# First-surface gate (F-ARCH-1): session_start sources that CONSUME a
# surfaced summary. A resume/startup/clear start after a candidate's mtime
# means a later start already had the pointer available — re-naming at every
# subsequent start would re-surface consumed state. compact is the PRODUCER
# (a post-summary compact writes a NEWER summary, it does not consume this
# one), and absent/unknown-source old-era events are non-consuming too: the
# costs are asymmetric (a false suppression kills the pointer's primary
# purpose; a false naming costs one sentence), so ambiguity fails toward
# naming.
_FIRST_SURFACE_CONSUMING_SOURCES = frozenset({"resume", "startup", "clear"})


def _latest_consuming_start_ts(session_dir: str) -> float | None:
    """Epoch seconds of the NEWEST consuming session_start in this session's
    journal, or None when there is none (or the journal is missing,
    unreadable, or has no parseable consuming event — every fail-open path
    returns None, which the gate reads as "not suppressed").

    The ts format is the journal's write format (UTC ISO-8601 with a Z
    suffix); unparseable stamps are skipped, never fatal.
    """
    try:
        from shared.session_journal import read_events_from

        latest: float | None = None
        for event in read_events_from(session_dir, event_type="session_start"):
            event_source = event.get("source")
            if (
                not isinstance(event_source, str)
                or event_source not in _FIRST_SURFACE_CONSUMING_SOURCES
            ):
                continue
            raw_ts = str(event.get("ts", ""))
            try:
                parsed = datetime.fromisoformat(
                    raw_ts.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                continue
            if latest is None or parsed > latest:
                latest = parsed
        return latest
    except Exception:
        # Fail-open: the gate is an upgrade path, never a new raise source.
        return None


def _resume_own_summary_clause(session_dir: str) -> str:
    """One sentence naming this session's own compact summary, or "".

    The resume-limb read-side counterpart of the clears above: the summary
    produced by an earlier compaction of THIS session sits in the session's
    own dir, and the non-compact clears above have usually already MOVED the
    live slot to a timestamped archive by the time the source limbs render —
    so name the canonical file when it still exists, else the NEWEST archive
    by mtime. Read-only: a probe, never a move.

    FIRST-SURFACE GATE (F-ARCH-1): a candidate (canonical or archive,
    uniformly) is SUPPRESSED when a consuming session_start event
    (resume/startup/clear — see _FIRST_SURFACE_CONSUMING_SOURCES) has a ts
    STRICTLY after the candidate's mtime: that later start already had the
    pointer available, so naming again would re-surface consumed state at
    every subsequent start. Among archives, the NEWEST UNSUPPRESSED
    shape-conforming one wins. Fail-open toward naming: a missing/unreadable/
    empty journal is no consuming event (today's behavior). The conservative
    edge this accepts: compact then clear then resume names nothing, because
    the clear consumed.

    Both probes require a regular FILE (is_file): a directory named like
    either slot is not a readable summary, and naming it would send the lead
    to a path whose read can only error. Archives are additionally selected
    by the exact stamp shape (_ARCHIVE_STAMP_SHAPE_RE) — hostile names never
    render rather than rendering stripped, and root-drained artifacts
    (_ROOT_DRAINED_SUMMARY_PREFIX) never match the shape by construction.

    Ownership-neutral on purpose ("from an earlier point of this session"):
    the clause carries information, not an attribution claim, and the
    session-scoped path it names embeds this session's id.

    Fail-open like the clears: on OSError the sentence is "" (no clause) —
    a dropped pointer costs a manual rediscovery, a raised one would break
    session init. Empty session_dir (unknown-* sentinel path) also yields ""
    — there is no own dir to name.
    """
    if not session_dir:
        return ""
    try:
        gate_ts = _latest_consuming_start_ts(session_dir)
        base = Path(session_dir)
        canonical = base / COMPACT_SUMMARY_NAME
        if canonical.is_file() and not (
            gate_ts is not None and gate_ts > canonical.stat().st_mtime
        ):
            target = str(canonical)
        else:
            archives = [
                p for p in base.glob(f"{COMPACT_SUMMARY_ARCHIVE_PREFIX}*.txt")
                if p.is_file() and _ARCHIVE_STAMP_SHAPE_RE.fullmatch(p.name)
                and not (
                    gate_ts is not None
                    and gate_ts > p.stat().st_mtime
                )
            ]
            if not archives:
                return ""
            target = str(max(archives, key=lambda p: p.stat().st_mtime))
    except OSError:
        return ""
    return (
        f' A compact summary from an earlier point of this session is '
        f'available at {target}.'
    )


def clear_stale_state(
    input_data: dict, project_dir: str, source: str, is_marker_reset: bool,
) -> None:
    """Before any writer runs: adopt an old-slug session dir, settle staged
    summaries, move a stale compact summary aside, and on /clear drop the
    bootstrap marker."""
    # Clear a stale compact-summary — BY MOVING IT, in BOTH of its homes.
    # Only "compact" source keeps either in place (postcompact_archive just
    # wrote it, and this session is about to read it).
    #
    # The ROOT singleton is the degradation + legacy drain; the session's
    # OWN DIR holds the writer's scoped file (#1504). Two move-not-delete
    # objects, no once-flag: the filesystem is the state, and the MOVE is
    # what empties it. This used to unlink. The path still has to be
    # cleared, for the same reason as before, but the previous code cleared
    # it BY DESTROYING the bytes, and those are the only copy. See
    # _archive_stale_compact_summary and _archive_own_dir_stale_summary.
    # Adopt a session dir written under the unresolved project basename
    # BEFORE any writer below can create the resolved-slug dir.
    _adopt_old_slug_session_dir(input_data.get("session_id", ""), project_dir)

    _settle_staged_summaries(input_data.get("session_id", ""), project_dir)

    if source != "compact":
        _archive_stale_compact_summary(
            input_data.get("session_id", ""), project_dir
        )
        _archive_own_dir_stale_summary(
            input_data.get("session_id", ""), project_dir
        )

    # Clear bootstrap-complete marker on user-initiated clear only (#414).
    #
    # Cannot use get_session_dir() here because the context module
    # hasn't been initialized yet (build_context_cache() runs at step 5a
    # below). Uses build_session_path() directly — it has its own
    # path traversal guard (Path.parents containment check).
    #
    # Scope: ONLY the marker is removed; team config persists. The
    # writer hook self-heals the marker on the next prompt as long as
    # team config + secretary remain on disk. See _clear_bootstrap_marker.
    if is_marker_reset:
        reset_session_id = input_data.get("session_id", "")
        if reset_session_id and project_dir:
            slug = project_slug(project_dir)
            session_path = build_session_path(slug, str(reset_session_id))
            _clear_bootstrap_marker(session_path)


def compact_directive(
    context_parts: list, tasks, frame_is_lead: bool, session_dir: str,
    plugin_root: str, *, _role_line: str, _ran_sentence: str, _team_line: str,
    _substitutions: str, _team_directive: str,
) -> None:
    """The source == "compact" limb of the lead directive ladder."""
    # Post-compaction: bootstrap directive subsumes "recover state"
    # guidance; keep concrete task-resumption bullets for the
    # orchestrator's next actions.
    # Refresh-awareness (presentation-only): an unspent
    # session_refreshed event means /repacted:refresh stopped the
    # teammates before this compact — a SendMessage to a stopped
    # teammate's name silently RESUMES its stale pre-refresh
    # transcript, so the "Re-engage secretary" instruction must
    # be suppressed until bootstrap respawns it. The FULL refresh
    # prompt comes only from check_resume_state at step 8; this
    # signal adjusts wording. When False, every byte below is
    # identical to the non-refresh-aware text (fail-safe default
    # — a non-lead or errored read keeps today's wording).
    refresh_pending = frame_is_lead and has_unspent_refresh(session_dir)
    if refresh_pending:
        _secretary_clause = (
            "Teammates were shut down by /repacted:refresh. Do NOT "
            "message any pre-refresh teammate name (a send "
            "RESUMES its stale transcript). Run /repacted:bootstrap "
            "first; spawn a fresh secretary only when memory is needed."
        )
    else:
        _secretary_clause = _SECRETARY_ON_DEMAND_CLAUSE
    # The compact-summary path is single-use: the secretary ARCHIVES
    # it after processing, so a lead that reads even slightly later
    # finds nothing there. Naming only the canonical path sends the
    # lead to a location it is expected to vacate. Name the archive
    # too, so the read survives the move without waiting for the
    # briefing to arrive.
    #
    # The READ TARGET tracks the writer (#1504): session_dir present
    # -> the writer scoped the file to this session, so name the
    # session-scoped path. session_dir absent -> the writer DEGRADED
    # to the root singleton, so name the root path: the degenerate
    # branch must follow the degraded write, not the happy path.
    #
    # session_dir is "" when session_id was missing (the ternary at
    # the top of this function). Interpolating it blind would emit an
    # instruction naming an empty directory — no error, just a
    # confidently wrong sentence — so fall back to the briefing.
    #
    # A session-scoped summary that no transcript attributes to this
    # session is parked and never written to that path, so the file
    # can also be absent without an archive. The lead then continues
    # from the summary in its own context. The instruction never
    # names a parked file: it may hold a teammate's summary. The root
    # singleton is written directly, so only an archive empties it.
    #
    # The read names the capped head beside the summary (D8): the
    # summary is 22-44 KB, the head at most 8 KB, pointers first.
    # The head is written with a lead summary and discarded with it,
    # so its absence means the same as the summary's.
    if session_dir:
        _summary_path = Path(session_dir) / COMPACT_SUMMARY_NAME
        _archive_clause = (
            f'(if it is absent, either the secretary archived the summary into '
            f'{session_dir} as compact-summary-<timestamp>.txt, or it was '
            f'not attributed to this session and you continue from the '
            f'summary already in your context)'
        )
    else:
        _summary_path = get_compact_summary_path()
        _archive_clause = (
            '(if it is absent, the secretary archived the summary into the '
            'session directory and names the path in its briefing)'
        )
    _head_path = _summary_path.parent / COMPACT_SUMMARY_HEAD_NAME
    # Bootstrap is complete by the gate's own marker check, so the
    # directive stops asking for it. A pending refresh keeps the
    # invoke text, because its secretary clause says to run
    # /repacted:bootstrap, and a refresh does not clear the marker.
    if not refresh_pending and _bootstrap_already_ran(session_dir):
        _directive = f'{_role_line}{_ran_sentence}{_team_line}{_substitutions}'
        _recover = 'Recover session state: '
    else:
        _directive = _team_directive
        _recover = 'After bootstrap, recover session state: '
    # The platform re-attaches each invoked skill cut to 20,000
    # characters, and drops the oldest once its budget is full, so
    # a long-running workflow's command can come back cut short or
    # not at all. An empty plugin_root would render a Read of
    # /commands/<name>.md, so it names the file without the root.
    _command_file = (
        f'`{plugin_root}/commands/<name>.md`' if plugin_root
        else "the PACT plugin's `commands/<name>.md`"
    )
    context_parts.insert(0, (
        f'{_directive} '
        f'{_recover}'
        f'(1) Read {_head_path} for prior context: open tasks and '
        f'worktrees rebuilt from disk, then the start of {_summary_path}; '
        f'read that full file only when the head lacks what you need '
        f'{_archive_clause}, '
        f'(2) Run TaskList to find in-progress work, '
        f'(3) read the task files of in-progress tasks for details (TaskGet does not surface metadata). '
        f'(4) If a PACT workflow you started is still in progress in TaskList, and its copy '
        f'among the re-attached skills above is cut short or missing, Read {_command_file} '
        f'in full before you continue it, where `repacted:<name>` is that workflow. '
        f'If the Read reports a partial view, read the remaining pages. '
        f'Do not invoke the workflow again: that starts it over. '
        f'If the file shows `$ARGUMENTS` where the task it was started for belongs, '
        f'take that task from its re-attached copy or from your summary. '
        f'{_secretary_clause}'
    ))
    # Secondary-layer (#444): append POST-COMPACTION CHECKPOINT block
    # when tasks in_progress. Consumes the hoisted `tasks` variable
    # (single source of truth).
    if tasks:
        _in_progress = [
            t for t in tasks
            if t.get("status") == "in_progress"
        ]
        if _in_progress:
            _checkpoint_block = build_post_compaction_checkpoint(
                feature=find_feature_task(tasks),
                phase=find_current_phase(tasks),
                agents=find_active_agents(tasks),
                blockers=find_blockers(tasks),
            )
            if refresh_pending:
                # Post-refresh re-label: the compaction was the
                # user's manual /compact (indistinguishable from
                # auto-compact downstream — neutral wording), and
                # the "active" agents are STOPPED pre-refresh
                # names whose task entries survive. Names still
                # come from live task data; only labels change.
                _checkpoint_block = _checkpoint_block.replace(
                    "Prior conversation auto-compacted.",
                    "Context was compacted.",
                    1,
                ).replace(
                    "Active Agents",
                    "Pre-refresh agents (STOPPED — respawn "
                    "before messaging)",
                    1,
                )
            context_parts.append(_checkpoint_block)
