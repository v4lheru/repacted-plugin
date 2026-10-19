"""Session context for PACT hooks: identity, team alignment, roles and the context file.

session_init writes pact-session-context.json once at SessionStart; every other hook calls
init() and reads it through the accessors. Path segments (slug, session_id, team_name) are
sanitized or rejected at each boundary.

Map: paths, init, get_pact_context, the resume census, SessionStart
team ownership, team resolution, accessors, session-id gate, agent names, roles,
team-config readers, context writers.
Doc: the design notes
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# session_registry is a leaf (no shared.* imports), so this cannot cycle.
from .session_registry import resolve as _registry_resolve
from .paths import SESSION_ID_CONTROL_CHARS_RE, get_claude_config_dir, is_safe_path_component
# constants is a leaf; the reverse edge would cycle, so the resolver lives here.
from .constants import COMPACT_SUMMARY_NAME, get_compact_summary_path
from .plugin_identity import NAMESPACE, STRIPPABLE_NAMESPACES

# Slug and session_id allowlist: the slug reaches a shell command in bootstrap.md (S3).
_UNSAFE_SLUG_CHARS_RE = re.compile(r"[^A-Za-z0-9_-]+")

# Set by init(); None means get_pact_context() returns _EMPTY_CONTEXT.
# pact_session.py builds the same path with project_slug from here.
_context_path: Path | None = None

# Populated on first get_pact_context(); each hook is a fresh process.
_cache: dict | None = None

# Per-process cache for get_team_name. COLD-START TRAP: never persist it; the platform team
# dir is born ~38 s after SessionStart, so an early value written to disk would bring back
# the bootstrap deadlock. None means not resolved; "" is a resolved empty value.
_aligned_cache: str | None = None

# Default context dict returned on any error
_EMPTY_CONTEXT = {
    "team_name": "",
    "session_id": "",
    "project_dir": "",
    "plugin_root": "",
    "started_at": "",
}

# team_name is re-validated at the read boundary with is_safe_path_component and rejected to ""
# (not sanitized), so a hand-edited context cannot leak a path-unsafe team name.


def reset_for_tests() -> None:
    """Reset the module's mutable caches (_cache, _context_path, _aligned_cache).

    Called by the autouse fixture in tests/conftest.py before and after each test. Lives here so
    a rename of a cache global must update it in the same module. Constants are not touched.
    """
    global _cache, _context_path, _aligned_cache
    _cache = None
    _context_path = None
    _aligned_cache = None


def _build_session_path(slug: str, session_id: str) -> Path:
    """Build the session-scoped directory path: ~/.claude/pact-sessions/{slug}/{session_id}/.

    Both segments are sanitize-substituted with _UNSAFE_SLUG_CHARS_RE before composition: raw
    stdin ids reach here, and the slug is interpolated into a shell command in bootstrap.md.
    The result must stay under pact-sessions (Path.parents containment); on escape, fall back to
    a sanitized basename; if the check raises, return a slug-only path (fail closed).
    """
    safe_slug = _UNSAFE_SLUG_CHARS_RE.sub("_", slug) if slug else slug
    session_id = _UNSAFE_SLUG_CHARS_RE.sub("_", session_id)
    sessions_root = get_claude_config_dir() / "pact-sessions"
    candidate = sessions_root / safe_slug / session_id
    try:
        sessions_root_resolved = sessions_root.resolve()
        resolved = candidate.resolve(strict=False)
        if resolved == sessions_root_resolved or sessions_root_resolved in resolved.parents:
            return candidate
        basename = Path(session_id).name
        if basename in ("", ".", "..") or "/" in basename:
            candidate = sessions_root / safe_slug
        else:
            candidate = sessions_root / safe_slug / basename
    except (OSError, ValueError):
        candidate = sessions_root / safe_slug
    return candidate


def project_slug(project_dir: str) -> str:
    """Return the session slug for ``project_dir``: the resolved basename of the directory.

    A symlinked project takes its target's name, as the pact-memory project id does; a
    worktree or subdirectory keeps its own slug. Empty input returns "" (not the CWD's name);
    if resolve() raises, the unresolved basename is used. Never raises.
    """
    if not project_dir:
        return ""
    try:
        return Path(project_dir).resolve().name
    except (OSError, RuntimeError):
        return Path(project_dir).name


def resolve_compact_summary_path(input_data: dict) -> Path:
    """Where this frame's compact summary is written. TOTAL.

    Session-scoped when ``session_id`` and CLAUDE_PROJECT_DIR are both present, else the root
    singleton (#1504): degradation re-homes the bytes, it never drops them. The root copy is
    drained by session_init on the next non-compact SessionStart.
    """
    session_id = input_data.get("session_id", "") if isinstance(input_data, dict) else ""
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if session_id and project_dir:
        return (
            _build_session_path(project_slug(project_dir), str(session_id))
            / COMPACT_SUMMARY_NAME
        )
    return get_compact_summary_path()


def _get_context_file_path() -> Path | None:
    """Return the session-scoped context file path, or None if init() not called."""
    return _context_path


def is_initialized() -> bool:
    """Return True iff init() (or write_context()) has set _context_path.

    False means reads derived from session context will fail open (empty values).
    """
    return _context_path is not None


def init(input_data: dict) -> None:
    """Initialize the module with the session-scoped context path.

    Path: ~/.claude/pact-sessions/{project_slug}/{session_id}/pact-session-context.json, from
    stdin session_id and CLAUDE_PROJECT_DIR. Either missing leaves _context_path None, and readers
    return _EMPTY_CONTEXT with no file I/O. No-op when a path is already set.
    """
    global _context_path, _cache, _aligned_cache

    # Already initialized (test fixtures pre-set _context_path)
    if _context_path is not None:
        return

    session_id = ""
    raw_id = input_data.get("session_id")
    if raw_id:
        # Same allowlist as the slug: the id reaches PACT_SESSION_DIR in a shell command body.
        session_id = _UNSAFE_SLUG_CHARS_RE.sub("_", str(raw_id))

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")

    if session_id and project_dir:
        slug = project_slug(project_dir)
        _context_path = (
            _build_session_path(slug, session_id) / "pact-session-context.json"
        )
        # _aligned_cache derives from the context, so a new path invalidates it.
        _cache = None
        _aligned_cache = None
    # else: _context_path stays None and readers return _EMPTY_CONTEXT


def get_pact_context() -> dict:
    """Read session context (team_name, session_id, project_dir, plugin_root, started_at).

    All values are strings; empty strings on any error. Cached for the process: the file is
    write-once/read-many.
    """
    global _cache
    if _cache is not None:
        return _cache

    ctx_path = _get_context_file_path()
    if ctx_path is None:
        # No path: normal before session_init writes the file; return empty context silently.
        _cache = dict(_EMPTY_CONTEXT)
        return _cache

    try:
        data = json.loads(ctx_path.read_text(encoding="utf-8"))
        # Read-boundary path-safety re-validation: an unsafe team_name becomes "".
        raw_team_name = str(data.get("team_name", ""))
        if raw_team_name and not is_safe_path_component(raw_team_name):
            print(
                f"pact_context: rejecting unsafe team_name "
                f"{raw_team_name!r} from context (using empty)",
                file=sys.stderr,
            )
            team_name = ""
        else:
            team_name = raw_team_name
        _cache = {
            "team_name": team_name,
            "session_id": str(data.get("session_id", "")),
            "project_dir": str(data.get("project_dir", "")),
            "plugin_root": str(data.get("plugin_root", "")),
            "started_at": str(data.get("started_at", "")),
        }
        return _cache
    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as e:
        print(
            f"pact_context: could not read context file: {e}",
            file=sys.stderr,
        )
        _cache = dict(_EMPTY_CONTEXT)
        return _cache


# Branch-3 census recency window. It narrows the candidate set only; widening it can reach the
# same-directory residual, so retune upward with care.
RESUME_CENSUS_RECENCY_SECONDS = 900

# dispatch_decision is excluded: denials are gate verdicts, not team activity (F1a, PR #1510).
_RESUME_CENSUS_ACTIVITY_EXACT_TYPES = frozenset(
    {"session_end", "session_consolidated"}
)


def _journal_has_team_activity() -> bool:
    """Branch-3 witness (#1507): has this session's journal any prior team activity?

    The cold-start guard: a fresh session has none, so its census cannot align to a concurrent
    session's team. Never raises (False on error). The journal import is function-local.
    """
    try:
        session_dir = get_session_dir()
        if not session_dir:
            return False
        from .journal_reader import read_events_from

        for event in read_events_from(session_dir):
            event_type = str(event.get("type", ""))
            if (
                event_type.startswith("task_")
                or event_type in _RESUME_CENSUS_ACTIVITY_EXACT_TYPES
            ):
                return True
        return False
    except Exception:
        # An unreadable journal proves no lived session: the census stays off.
        return False


def _own_journal_last_end_marker_ts() -> float | None:
    """Epoch seconds of this session's journal's last session_end/session_consolidated marker.

    None when there is none or the ts is unparseable. Never raises.
    """
    try:
        session_dir = get_session_dir()
        if not session_dir:
            return None
        from .journal_reader import read_events_from

        last_ts: float | None = None
        for event in read_events_from(session_dir):
            if str(event.get("type", "")) not in ("session_end",
                                                  "session_consolidated"):
                continue
            raw_ts = str(event.get("ts", ""))
            try:
                parsed = datetime.fromisoformat(
                    raw_ts.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                continue
            if last_ts is None or parsed > last_ts:
                last_ts = parsed
        return last_ts
    except Exception:
        return None


def _resume_census_candidate_corroborated(
    team_dir_name: str, config_data: dict
) -> bool:
    """Bind a census candidate to THIS session's platform identity (#1507 F1).

    Uniqueness proves nothing, so every candidate must pass all of:
    * LEAD ENTRY: the members[] entry whose agentId equals leadAgentId;
    * SELF-CONSISTENCY: that agentId ends with "@<team-dir-name>" (#1514);
    * SUBJECT ANCHOR: the lead's cwd raw-matches the live cwd or the persisted project_dir;
    * BIRTH ORDER: when the journal has an end marker, the lead's joinedAt is after it.
    Residual (pinned by test_f1_same_cwd_residual_is_pinned): a same-directory sibling born
    between the end marker and the resume. Never raises; any failure returns False (suppress).
    """
    try:
        lead_agent_id = str(config_data.get("leadAgentId", ""))
        if not lead_agent_id:
            return False
        # Suffix match (#1514): "@<dir>" must be the agentId's tail.
        if not lead_agent_id.endswith(f"@{team_dir_name}"):
            return False
        lead_entry = None
        for member in config_data.get("members") or []:
            if (isinstance(member, dict)
                    and str(member.get("agentId", "")) == lead_agent_id):
                lead_entry = member
                break
        if lead_entry is None:
            return False
        lead_cwd = str(lead_entry.get("cwd", ""))
        if not lead_cwd:
            return False
        if lead_cwd not in (os.getcwd(), get_project_dir()):
            return False
        end_marker_ts = _own_journal_last_end_marker_ts()
        if end_marker_ts is not None:
            joined_at = lead_entry.get("joinedAt")
            if not isinstance(joined_at, (int, float)):
                return False
            if (joined_at / 1000.0) <= end_marker_ts:
                return False
        return True
    except Exception:
        return False


def _own_tasks_store_fresh(teams_root: Path, session_id: str) -> bool:
    """#1509 guard input: was tasks/<session_id>/ modified within RESUME_CENSUS_RECENCY_SECONDS?

    Never raises; False only pushes toward suppressing branch-2, never toward a foreign team.
    """
    try:
        from shared.task_store import TaskStore
        tasks_dir = TaskStore.for_config(teams_root.parent).team_dir(session_id)
        if tasks_dir is None or not tasks_dir.is_dir():
            return False
        return tasks_dir.stat().st_mtime >= (
            time.time() - RESUME_CENSUS_RECENCY_SECONDS
        )
    except Exception:
        return False


def _lead_born_since(config_data: dict, cutoff: float) -> bool:
    """Did the config's lead entry join at or after ``cutoff`` (epoch seconds) and not in the
    future? False for a missing lead entry or a joinedAt that is not a finite number."""
    lead_agent_id = config_data.get("leadAgentId")
    if not isinstance(lead_agent_id, str) or not lead_agent_id:
        return False
    joined_at = next(
        (m.get("joinedAt") for m in config_data.get("members") or []
         if isinstance(m, dict) and m.get("agentId") == lead_agent_id),
        None,
    )
    if isinstance(joined_at, bool) or not isinstance(joined_at, (int, float)):
        return False
    return cutoff <= joined_at / 1000.0 <= time.time()  # NaN and inf -> False


def _resume_census_unique_candidate(teams_root: Path, session_id: str) -> str | None:
    """Branch-3 census (#1507): the one live re-provisioned team that corroborates as this session's.

    A candidate is a teams/*/config.json with a real string leadSessionId other than this
    session's id and a tasks store fresh within RESUME_CENSUS_RECENCY_SECONDS, or no tasks store
    yet and a lead that joined within that window. Every candidate is
    corroborated first, then survivors are counted: exactly one returns its dir name; zero or two
    or more (or any error) return None. Corroborate-then-count lets a resumed session realign
    while a peer session is live. Living here keeps the marker write-back from persisting an
    uncorroborated winner. FS-read-only, never raises; tasks root is teams_root.parent / "tasks".
    """
    try:
        from shared.task_store import TaskStore
        store = TaskStore.for_config(teams_root.parent)
        cutoff = time.time() - RESUME_CENSUS_RECENCY_SECONDS
        candidates: list[tuple[str, dict]] = []
        for entry in sorted(teams_root.iterdir()):
            try:
                if not entry.is_dir() or not (entry / "config.json").is_file():
                    continue
                data = json.loads(
                    (entry / "config.json").read_text(encoding="utf-8")
                )
                # M1: a candidate needs a real string leadSessionId different from ours; absence is not foreign.
                candidate_lead_sid = data.get("leadSessionId")
                if not isinstance(candidate_lead_sid, str):
                    continue
                if candidate_lead_sid == session_id:
                    continue  # this session's own team — identity-match owns it
                tasks_dir = store.team_dir(entry.name)
                if tasks_dir is None or not tasks_dir.is_dir():
                    # A resume provisions the team seconds before SessionStart and its
                    # task store only at the first TaskCreate: a lead
                    # born inside the window stands in for the store it has not made.
                    if tasks_dir is None or not _lead_born_since(data, cutoff):
                        continue  # no live task store under this name
                elif tasks_dir.stat().st_mtime < cutoff:
                    continue  # live-looking config, but an AGED task store
                # Path-check the dir name before accepting it.
                if not is_safe_path_component(entry.name):
                    continue
                candidates.append((entry.name, data))
            except (OSError, json.JSONDecodeError, ValueError, TypeError,
                    AttributeError):
                # Unreadable / malformed sibling — skip it, keep scanning.
                continue
        # Corroborate first, then count: a live peer session must not suppress the census.
        survivors = [
            name for name, config in candidates
            if _resume_census_candidate_corroborated(name, config)
        ]
        if len(survivors) != 1:
            # Zero or several survivors: the stale default.
            return None
        return survivors[0]
    except Exception:
        # Any error -> no candidate -> stale default.
        return None


# A resume re-provisions the team ~1 s before SessionStart (measured 2026-09-29).
SESSIONSTART_OWN_TEAM_MAX_AGE_SECONDS = 120
# compaction_keeps_team also takes a team born this long AFTER the resume's
# journal stamp: the stamp is in whole seconds and a birth may run late.
SESSIONSTART_LATE_BIRTH_SECONDS = 10


def sessionstart_team_is_own(team_name: str, session_id: str) -> bool:
    """May SessionStart announce and persist ``team_name`` for ``session_id``?

    Identity match and the branch-2 full-UUID dir are own. A branch-3 census winner is accepted
    only when its lead joined within SESSIONSTART_OWN_TEAM_MAX_AGE_SECONDS and no other PACT
    session of this project is live: once persisted it is sticky.
    Never raises: any error -> False (keep the default).
    """
    try:
        if team_name == session_id:
            return True
        config_path = get_claude_config_dir() / "teams" / team_name / "config.json"
        if not config_path.is_file():
            return False
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("leadSessionId") == session_id:
            return True
        lead_agent_id = config.get("leadAgentId")
        joined_at = next(
            (m.get("joinedAt") for m in config.get("members") or []
             if isinstance(m, dict) and m.get("agentId") == lead_agent_id),
            None,
        )
        if isinstance(joined_at, bool) or not isinstance(joined_at, (int, float)):
            return False
        age = time.time() - joined_at / 1000.0
        if not (0 <= age <= SESSIONSTART_OWN_TEAM_MAX_AGE_SECONDS):
            return False  # also rejects NaN
        own_dir = Path(get_session_dir())
        if not own_dir.name:
            return False
        cutoff = time.time() - RESUME_CENSUS_RECENCY_SECONDS
        for entry in own_dir.parent.iterdir():
            if entry.name == own_dir.name or not entry.is_dir():
                continue
            for name in ("pact-session-context.json", "session-journal.jsonl"):
                marker = entry / name
                if marker.exists() and marker.stat().st_mtime >= cutoff:
                    return False
        return True
    except Exception:
        return False


def compaction_keeps_team(team_name: object) -> bool:
    """May a compaction SessionStart keep ``team_name``, the team its context file already names?

    Keeps a name, never adopts one. Kept only when: the name is one safe segment
    with a regular config holding a string leadSessionId; the lead entry's resolved cwd EQUALS
    the resolved project dir (a worktree is a subdirectory, so subdirectories are refused); the
    lead joined within the window around this life's latest resume (review F2); and no other
    PACT session of this project owns it. Never raises; any doubt -> False.
    """
    resumed_at = _own_journal_last_resume_ts()
    return resumed_at is not None and _team_born_to_this_life(team_name, resumed_at)


def resume_adopts_team(team_name: object) -> bool:
    """May a resume SessionStart adopt the census winner ``team_name`` while a peer is live?

    The compaction keep-rule's proof with "now" as the start of the life. A live
    peer blocks only a team it owns or one its own latest resume explains (second review S1).
    Never raises; any doubt -> False.
    """
    return _team_born_to_this_life(team_name, time.time(), peer_resumes=True)


_NO_PEER = object()


def _peer_team(entry: Path) -> object:
    """The team a session dir's context file names, else _NO_PEER."""
    peer_context = entry / "pact-session-context.json"
    try:
        if peer_context.is_file():
            return json.loads(peer_context.read_text(encoding="utf-8")).get("team_name")
    except (OSError, ValueError, AttributeError, RecursionError):
        pass
    return _NO_PEER


def peer_session_names_team(team: str) -> bool:
    """True when a peer session dir names `team`; OSError when unlistable."""
    own = Path(get_session_dir())
    if not own.name:
        raise OSError("no session")
    return any(e.name != own.name and e.is_dir() and _peer_team(e) == team
               for e in own.parent.iterdir())


def _team_born_to_this_life(team_name: object, resumed_at: float, peer_resumes: bool = False,
                            since: float | None = None, teams_root: Path | None = None) -> bool:
    """compaction_keeps_team's proof against a given start of this life. With
    ``peer_resumes``, a peer whose latest resume the lead's birth also fits
    owns the team too, so it is not provably this session's. With ``since``
    (epoch ms): born at or after it, not in the future."""
    try:
        if not isinstance(team_name, str) or not is_safe_path_component(team_name):
            return False
        config_path = (teams_root or get_claude_config_dir() / "teams") / team_name / "config.json"
        if not config_path.is_file():
            return False
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            return False
        lead_session_id = config.get("leadSessionId")
        if not isinstance(lead_session_id, str) or not lead_session_id:
            return False
        lead_agent_id = config.get("leadAgentId")
        if not isinstance(lead_agent_id, str) or not lead_agent_id.endswith(
            f"@{team_name}"
        ):
            return False
        lead = next(
            (m for m in config.get("members") or []
             if isinstance(m, dict) and m.get("agentId") == lead_agent_id),
            None,
        )
        if lead is None:
            return False
        lead_cwd, project_dir = lead.get("cwd"), get_project_dir()
        if not isinstance(lead_cwd, str) or not os.path.isabs(lead_cwd):
            return False
        if not project_dir or Path(lead_cwd).resolve() != Path(project_dir).resolve():
            return False
        joined_at = lead.get("joinedAt")
        if isinstance(joined_at, bool) or not isinstance(joined_at, (int, float)):
            return False
        def born_with(resume_ts: float) -> bool:
            if since is not None:
                return since <= joined_at <= time.time() * 1000
            lead_age = resume_ts - joined_at / 1000.0
            return (-SESSIONSTART_LATE_BIRTH_SECONDS <= lead_age
                    <= SESSIONSTART_OWN_TEAM_MAX_AGE_SECONDS)  # NaN -> False

        if not born_with(resumed_at):
            return False
        own_dir = Path(get_session_dir())
        if not own_dir.name:
            return False
        for entry in own_dir.parent.iterdir():
            if entry.name == own_dir.name or not entry.is_dir():
                continue
            if entry.name == lead_session_id:
                return False
            named = _peer_team(entry)
            if named is _NO_PEER:
                continue
            if named == team_name:
                return False
            if peer_resumes:
                peer_resumed_at = _last_resume_ts(str(entry))
                if peer_resumed_at is not None and born_with(peer_resumed_at):
                    return False
        return True
    except Exception:
        return False


def _own_journal_last_resume_ts() -> float | None:
    """_last_resume_ts of this session's own dir: where the current life began."""
    return _last_resume_ts(get_session_dir())


def _last_resume_ts(session_dir: str) -> float | None:
    """Epoch seconds of the LATEST ``session_start`` of source ``resume`` in
    the journal of ``session_dir``. None when the journal has no such event,
    cannot be read, or that event's ts is not a timezone-aware ISO-8601 time
    (an older resume is never used in its place). Never raises.
    """
    try:
        if not session_dir:
            return None
        from .journal_reader import journal_path_from, read_events_from

        if not journal_path_from(session_dir).is_file():
            return None  # absent, or a FIFO that would hang the read
        resumes = [
            event for event in read_events_from(session_dir, "session_start")
            if event.get("source") == "resume"
        ]
        if not resumes:
            return None
        parsed = datetime.fromisoformat(
            str(resumes[-1].get("ts", "")).replace("Z", "+00:00")
        )
        return parsed.timestamp() if parsed.tzinfo else None
    except Exception:
        return None


def _yields_to(teams_root: Path, team: str, data: dict) -> bool:
    """Does the matched config ``data`` yield to the persisted ``team``? Only to a team born
    after ``data`` was created that passes resume_adopts_team's ownership proof: a crashed
    life's config still names this session."""
    made = data.get("createdAt")
    return (type(made) in (int, float) and made > 0
            and _team_born_to_this_life(team, 0, since=made, teams_root=teams_root))


def _resolve_aligned_team_name(
    session_id: str,
    teams_dir: str | None = None,
    default: str | None = None,
) -> str:
    """Resolve the REAL platform team name for ``session_id`` by identity match (#989).

    Branch 1: a teams/<dir>/config.json whose leadSessionId equals session_id (no dir-name
    shortcut, so an id8 collision cannot mis-resolve), unless it _yields_to the fallback. Branch 2: a config-less teams/<session_id>/
    substrate, guarded by #1509. Branch 3: the resume census (#1507), gated by journal activity
    and an absent fallback config. Anything else returns ``default`` (the persisted team_name
    when None): detection can only upgrade, never degrade. Matched names are path-checked.
    FS-read-only and never raises: the outer guard is a bare ``except Exception`` because
    Path.home() and Path(teams_dir) raise RuntimeError/TypeError that a typed tuple would leak.
    Memoized by get_team_name via _aligned_cache.
    """
    try:
        # The fallback first: every exit path returns a defined value.
        fallback = default if default is not None else get_pact_context().get(
            "team_name", ""
        )
        if not session_id:
            return fallback
        if teams_dir is not None:
            teams_root = Path(teams_dir)
        else:
            teams_root = get_claude_config_dir() / "teams"
        # Sorted: deterministic when two dirs claim the same leadSessionId.
        for entry in sorted(teams_root.iterdir()):
            try:
                if not entry.is_dir():
                    continue
                config_path = entry / "config.json"
                if not config_path.is_file():
                    continue  # absent, or a FIFO that would hang the read
                data = json.loads(config_path.read_text(encoding="utf-8"))
                if data.get("leadSessionId") != session_id:
                    continue
                # Path-check the matched dir name; a tampered config could name an unsafe dir.
                name = entry.name
                if not is_safe_path_component(name):
                    continue
                if name != fallback and _yields_to(teams_root, fallback, data):
                    continue
                return name
            except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
                # One unreadable sibling must not abort detection.
                continue
        # Branch 2: config-less teams/<session_id>/ (Desktop child / older CLI). is_safe_path_component
        # is the first conjunct, so an unsafe id never reaches a Path. #1509: a corroborated census
        # winner preempts a stale-looking own substrate only when the journal has an end marker.
        census_winner: str | None = None
        if (
            is_safe_path_component(session_id)
            and (teams_root / session_id).is_dir()
            and (
                (teams_root / session_id / "inboxes").is_dir()
                or (teams_root / session_id / "file-edits.json").exists()
            )
        ):
            if not _own_tasks_store_fresh(teams_root, session_id):
                # N1: only an ENDED substrate may be preempted; a live idle session has no end marker.
                if _own_journal_last_end_marker_ts() is not None:
                    census_winner = _resume_census_unique_candidate(
                        teams_root, session_id
                    )
            if census_winner is None:
                return session_id
        # Branch 3: resume census (#1507). Fires only when the fallback is non-empty and path-safe, its
        # own config is absent (inert after convergence), and this session's journal shows team
        # activity (the cold-start guard).
        if (
            fallback
            and is_safe_path_component(fallback)
            and not (teams_root / fallback / "config.json").exists()
            and _journal_has_team_activity()
        ):
            # Reuse the census result branch 2 may already have computed.
            if census_winner is None:
                census_winner = _resume_census_unique_candidate(
                    teams_root, session_id
                )
            if census_winner is not None:
                return census_winner
        return fallback
    except Exception:
        # TOTAL fail-safe: any error returns the default. get_team_name and the heal path rely on it.
        if default is not None:
            return default
        try:
            return get_pact_context().get("team_name", "")
        except Exception:
            return ""


def get_team_name() -> str:
    """Return the identity-matched team name, lowercased; "" on error. A pure read.

    EMPTY-SSOT SHORT-CIRCUIT, a security fail-closed gate (do not remove): an empty persisted
    team_name returns "" without identity match, because every consumer denies on an empty team
    (test_empty_ssot_team_fails_closed_both_modes). Cached in _aligned_cache per process.
    """
    global _aligned_cache
    if _aligned_cache is not None:
        return _aligned_cache
    # Empty SSOT fails closed before identity match. Branch 2 is reachable only through the
    # non-empty path, so session_init must keep persisting a non-empty name.
    ctx_team = get_pact_context().get("team_name", "")
    if not ctx_team:
        _aligned_cache = ""
        return _aligned_cache
    # Identity match may upgrade a non-empty SSOT to the real platform dir.
    resolved = _resolve_aligned_team_name(get_session_id(), default=ctx_team)
    _aligned_cache = resolved.lower()
    return _aligned_cache


def get_session_id() -> str:
    """Convenience: return session_id from context. Empty string on error."""
    return get_pact_context().get("session_id", "")


def get_project_dir() -> str:
    """Convenience: return project_dir from context. Empty string on error."""
    return get_pact_context().get("project_dir", "")


def get_session_dir() -> str:
    """Return ~/.claude/pact-sessions/{slug}/{session_id}/, or "" if either is unavailable.

    The path may not exist yet; callers create it before writing.
    """
    session_id = get_session_id()
    project_dir = get_project_dir()
    if not session_id or not project_dir:
        return ""
    slug = project_slug(project_dir)
    return str(_build_session_path(slug, session_id))


def reconstruct_session_dir(project_dir: str, session_id: str) -> str:
    """Reconstruct the session directory from raw context-file fields (off-lead readers).

    Routes the id through _UNSAFE_SLUG_CHARS_RE and the slug through _build_session_path, the
    writer's own derivation, so a dot or space cannot land on a different directory. Returns ""
    when either input is falsy.
    """
    if not project_dir or not session_id:
        return ""
    safe_id = _UNSAFE_SLUG_CHARS_RE.sub("_", str(session_id))
    slug = project_slug(project_dir)
    return str(_build_session_path(slug, safe_id))


def get_plugin_root() -> str:
    """Return plugin_root from context, else CLAUDE_PLUGIN_ROOT; "" when both are empty.

    The file value wins whenever it is non-empty.
    """
    # The env fallback feeds the bootstrap-marker signature; a wrong value fails the compare (closed).
    return get_pact_context().get("plugin_root", "") or os.environ.get(
        "CLAUDE_PLUGIN_ROOT", ""
    )


def generate_team_name(input_data: dict) -> str:
    """Generate the team name "session-<first 8 of session_id>", or a random 8-hex suffix."""
    # INVARIANT: every PACT-minted team name comes from here: lowercase [a-f0-9-] after "session-",
    # matching the platform's per-session team. The tasks skip-set allowlist relies on the charset.
    raw_id = input_data.get("session_id")
    session_id = str(raw_id) if raw_id else ""
    if session_id:
        suffix = re.sub(r"[^a-f0-9-]", "", session_id[:8]) or secrets.token_hex(4)
    else:
        suffix = secrets.token_hex(4)
    return f"session-{suffix}"


def _is_unknown_or_missing_session(raw_id: object) -> bool:
    """Return True if the session_id is missing, blank, a sentinel, or contains control chars.

    The single malformed-stdin predicate for session_init's persist gate, its CLAUDE.md write
    and heal_context_if_missing. Rejects None, non-strings, empty and whitespace-only strings,
    the `unknown-*` sentinel, and C0/DEL characters (a newline would inject a CLAUDE.md line).
    """
    if not raw_id:
        return True
    if not isinstance(raw_id, str):
        return True
    stripped = raw_id.strip()
    if not stripped:
        return True
    if SESSION_ID_CONTROL_CHARS_RE.search(raw_id):
        return True
    return stripped.startswith("unknown-")


def resolve_agent_name(
    input_data: dict,
    team_name: str | None = None,
    teams_dir: str | None = None,
) -> str:
    """Resolve the human-readable agent name from hook stdin; "" when unresolvable.

    Chain: 1 agent_name; 2 agent_id "name@team"; 3 agent_id looked up in the team config;
    3.5 the session registry row for session_id (only for a frame without agent_id);
    4 agent_type without its namespace and "pact-" prefix; 5 "".
    """
    agent_name = input_data.get("agent_name")
    if agent_name:
        return str(agent_name)

    # Step 2: agent_id "name@team" (no file I/O)
    agent_id = input_data.get("agent_id")
    if agent_id and "@" in str(agent_id):
        return str(agent_id).split("@")[0]

    # Step 3: agent_id looked up in the team config
    if agent_id:
        resolved_team = team_name if team_name else get_team_name()
        if resolved_team:
            name = _lookup_agent_in_team_config(
                str(agent_id), resolved_team, teams_dir
            )
            if name:
                return name

    # Step 3.5: the registry row for this session_id (tmux frames lack agent_name/agent_id).
    # Skipped when the frame has an agent_id: a shared session's row can name another member
    #. resolve() never raises; None falls through to Step 4.
    session_id = input_data.get("session_id")
    if session_id and not agent_id:
        resolved = _registry_resolve(str(session_id))
        if resolved and "@" in resolved:
            return resolved.split("@")[0]

    # Step 4: "repacted:pact-<name>" or the legacy "PACT:pact-<name>" resolves like "pact-<name>".
    agent_type = input_data.get("agent_type")
    if agent_type:
        type_str = str(agent_type)
        unqualified = strip_pact_namespace(type_str)
        if unqualified.startswith("pact-"):
            return unqualified[len("pact-"):]
        return type_str

    return ""


# Lead agent_type spellings, qualified and bare; case-sensitive. Not derived from the specialist
# registry, which would miss the qualified spelling.
LEAD_AGENT_TYPES = frozenset({NAMESPACE + "pact-orchestrator", "pact-orchestrator"})

# The platform spells a plugin agent `<plugin name>:<agent>`; the plugin's
# name lives in shared/plugin_identity.py.
PACT_NAMESPACE = NAMESPACE


def strip_pact_namespace(value: str) -> str:
    """``value`` without one leading namespace: the current one or a legacy
    one (``shared.plugin_identity.STRIPPABLE_NAMESPACES``). Case-sensitive;
    at most one is stripped, and no other namespace is."""
    for namespace in STRIPPABLE_NAMESPACES:
        if value.startswith(namespace):
            return value[len(namespace):]
    return value


def member_agent_type(member) -> str | None:
    """A team-config member's ``agentType`` without its plugin namespace, or None.

    Normalized at the reader, not in _iter_members, whose many callers read ``name``.
    """
    if not isinstance(member, dict):
        return None
    agent_type = member.get("agentType")
    if not isinstance(agent_type, str):
        return None
    return strip_pact_namespace(agent_type)


def is_lead(input_data: dict) -> bool:
    """Return True iff this hook frame belongs to the PACT team-lead.

    Reads ONLY the top-level ``agent_type`` and tests membership in LEAD_AGENT_TYPES (purity is
    a tested assertion). TOTAL for a dict input; a non-dict raises on purpose so each gate keeps
    its own fail posture. A coordination control, not a security boundary. Exception: an
    in-process teammate's compaction frames carry the lead's agent_type (see
    shared/compaction_owner.py). Captured frames: tests/fixtures/role_frames.py; truth table:
    hooks/shared/HOOK_STDIN_DISCRIMINATORS.md.
    """
    agent_type = input_data.get("agent_type")
    # isinstance keeps the predicate TOTAL: `x in frozenset` raises for an unhashable x.
    return isinstance(agent_type, str) and agent_type in LEAD_AGENT_TYPES


def classify_session_role(input_data: dict) -> str:
    """Classify the frame as "lead", "teammate" (agent_type set, not a lead) or "unknown" (absent).

    Same contract and caveat as is_lead.
    """
    # One definition of "lead": delegate to is_lead.
    if is_lead(input_data):
        return "lead"
    if input_data.get("agent_type"):
        return "teammate"
    return "unknown"


def _read_lead_session_id(team_name: str, teams_dir: str | None = None) -> str:
    """Read the top-level ``leadSessionId`` from teams/{team_name}/config.json; "" on any failure.

    The SSOT copy (task_claim_gate imports it). session_registry keeps an inline copy (its leaf
    rule forbids shared.* imports); a parity test keeps the two identical. The value can be stale
    after a resume, so consumers use it for coordination only. Never raises.
    """
    if not is_safe_path_component(team_name):
        return ""
    if teams_dir:
        config_path = Path(teams_dir) / team_name / "config.json"
    else:
        config_path = get_claude_config_dir() / "teams" / team_name / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        lead_session_id = data.get("leadSessionId")
    except (OSError, json.JSONDecodeError, ValueError, AttributeError, TypeError):
        return ""
    return lead_session_id if isinstance(lead_session_id, str) else ""


def _iter_members(
    team_name: str,
    teams_dir: str | None = None,
) -> list[dict]:
    """Return the dict entries of members[] from teams/{team_name}/config.json; [] on any error.

    Silent on error: callers treat [] as "team config not usable".
    """
    if not team_name:
        return []
    if teams_dir:
        config_path = Path(teams_dir) / team_name / "config.json"
    else:
        config_path = (
            get_claude_config_dir() / "teams" / team_name / "config.json"
        )
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        members = data.get("members")
    except (OSError, json.JSONDecodeError, ValueError, AttributeError, TypeError):
        return []
    if not isinstance(members, list):
        return []
    return [m for m in members if isinstance(m, dict)]


def _lookup_agent_in_team_config(
    agent_id: str,
    team_name: str,
    teams_dir: str | None = None,
) -> str:
    """Return the name of the team-config member whose id is ``agent_id``, else ""."""
    for member in _iter_members(team_name, teams_dir):
        if member.get("id") == agent_id:
            return str(member.get("name", ""))
    return ""


def build_context_cache(
    team_name: str,
    session_id: str,
    project_dir: str,
    plugin_root: str = "",
    started_at: str | None = None,
) -> tuple[Path, dict] | None:
    """Build the context dict and path and populate the in-process cache; no disk I/O.

    Returns ``(target, context)`` for persist_context, or None when the path is uncomputable.
    The sole owner of _cache/_context_path (#877): the cache is set for every frame, and a
    failed persist leaves it intact. ``started_at`` None records now; a compaction passes the
    value already on disk.
    """
    global _context_path, _cache, _aligned_cache

    context = {
        "team_name": team_name,
        "session_id": session_id,
        "project_dir": project_dir,
        "plugin_root": plugin_root,
        "started_at": started_at or datetime.now(timezone.utc).isoformat(),
    }

    # _context_path when set (init() or a test fixture), else computed.
    if _context_path is not None:
        target = _context_path
    elif session_id and project_dir:
        slug = project_slug(project_dir)
        target = (
            _build_session_path(slug, session_id) / "pact-session-context.json"
        )
    else:
        # No session-scoped path: readers fall back to _EMPTY_CONTEXT.
        print(
            "pact_context: skipping write — session_id or project_dir unavailable",
            file=sys.stderr,
        )
        return None

    # The cache is set for every frame; a context write invalidates _aligned_cache (#989).
    _context_path = target
    _cache = context
    _aligned_cache = None
    return target, context


def persist_context(target: Path, context: dict) -> None:
    """Atomically write ``context`` to ``target`` (mkstemp + rename, 0o600). Lead frames only (#877).

    Fail-open: errors are logged and swallowed. Never touches the cache.
    """
    context_dir = target.parent
    try:
        context_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

        # A temp file in the same directory: the rename is atomic.
        fd, tmp_path = tempfile.mkstemp(
            dir=str(context_dir),
            prefix=".pact-session-context-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(context, f)
            os.chmod(tmp_path, 0o600)
            os.rename(tmp_path, str(target))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception as e:
        print(
            f"pact_context: could not write context file: {e}",
            file=sys.stderr,
        )


def write_context(
    team_name: str,
    session_id: str,
    project_dir: str,
    plugin_root: str = "",
) -> None:
    """Write the session context file: build_context_cache then persist_context.

    Returns without writing when session_id or project_dir is missing. session_init composes
    the two halves itself so only a lead frame persists (#877).
    """
    result = build_context_cache(team_name, session_id, project_dir, plugin_root)
    if result is not None:
        persist_context(*result)


def describe_context_failure() -> str:
    """One-line diagnosis of why session context is empty, for deny messages; "" when healthy.

    No path -> underivable; path set and file absent -> names the path and the recovery;
    file present -> "". TOTAL: an OSError from exists() counts as absent.
    """
    if _context_path is None:
        return (
            "session context underivable (no session_id in hook stdin or "
            "CLAUDE_PROJECT_DIR unset)"
        )
    try:
        file_present = _context_path.exists()
    except OSError:
        file_present = False
    if not file_present:
        return (
            f"context file not found: {_context_path} — session_init may "
            "have failed at SessionStart; submit any message to trigger "
            "self-heal, or run /repacted:bootstrap"
        )
    return ""


def heal_context_if_missing(input_data: dict) -> bool:
    """Re-create a missing pact-session-context.json for a lead frame. Returns True iff healed.

    Fires only when init() derived a path, the file is ABSENT (a malformed file is evidence and
    is kept), is_lead(input_data), and the session_id is not missing or a sentinel (a random
    team name would create an unreapable dir). Writes the identity-aligned team name (#989)
    through write_context. Two healers on one prompt write equivalent content atomically.
    TOTAL: never raises (False on error).
    """
    try:
        if _context_path is None:
            return False
        if _context_path.exists():
            return False
        if not is_lead(input_data):
            return False
        raw_id = input_data.get("session_id")
        if _is_unknown_or_missing_session(raw_id):
            return False
        # #989: write the identity-aligned name; the computed name is the cold default.
        aligned_team = _resolve_aligned_team_name(
            str(raw_id), default=generate_team_name(input_data)
        )
        write_context(
            aligned_team,
            str(raw_id),
            os.environ.get("CLAUDE_PROJECT_DIR", ""),
            os.environ.get("CLAUDE_PLUGIN_ROOT", ""),
        )
        # persist is fail-open: verify on disk so "True iff healed" holds.
        return _context_path.exists()
    except Exception as e:
        print(f"pact_context: self-heal failed: {e}", file=sys.stderr)
        return False
