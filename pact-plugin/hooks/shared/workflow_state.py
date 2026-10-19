"""
Location: pact-plugin/hooks/shared/workflow_state.py
Summary: The lead's workflow state on disk: <session dir>/workflow-state.json, kept by hooks
         on the task events they already see, and the one-screen resume rendered from it.
         The file holds the feature and phase (folded from the session journal), the
         worktrees and branches of the project, the open work tasks with owner and wait
         state, open blocker and algedonic signals, the last accepted HANDOFFs (id, rev,
         digest), undisposed amend_<n> keys and the session's cost so far when the cost cap
         has already counted it.
Used by: bin/_front/status.py (`repact inspect` and `repact resume`: the screen, --json, --rebuild) and
         hooks/shared/session_init_resume.py + session_init_lead.py (the screen in the
         post-compaction, resume and clear context).

DERIVED STATE. Every field comes from the task files, the journal or the git directory, so
rebuild() from those sources gives the same file as the incremental updates did; the one
test that pins it compares the two minus `built_at` and `cost` (the cost cache moves on its
own). Both paths run the same projection (project_task) and the same journal fold (fold),
so they cannot drift apart. Never any model text beyond task subjects, which are cut to one
line of SUBJECT_CHARS.

FRESH OR STALE. The file records the journal byte offset it was built from. It is stale
when the journal holds a state-relevant event (RELEVANT_EVENTS) past that offset: some
writer moved on without this file, so the reader falls back to the recovery procedure.

Bounded: at most MAX_TASKS open tasks, MAX_ACCEPTED accepted HANDOFFs (highest task ids),
MAX_WORKTREES worktrees and MAX_FEATURES feature candidates. Writes take the sidecar lock
of shared/state_file.locked_update and replace the file atomically. Every public function
is fail-open: a hook never fails a tool call because this file could not be kept.

Example:
    state = update(session_dir, team, project_dir, ["12"])
    print(render(load(session_dir), freshness(session_dir, load(session_dir))))
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .amendments import amendments, handoff_rev, missing_dispositions, open_rejection
from .payload_digest import payload_digest, stored_teachback

STATE_NAME = "workflow-state.json"
JOURNAL_NAME = "session-journal.jsonl"
VERSION = 1
MAX_TASKS = 60
MAX_ACCEPTED = 10
MAX_WORKTREES = 8
MAX_FEATURES = 20
SUBJECT_CHARS = 80
SCREEN_LINES = 40
RELEVANT_EVENTS = frozenset({"phase_transition", "variety_assessed", "metadata_write",
                             "agent_handoff", "agent_dispatch"})
VOLATILE_KEYS = ("built_at", "cost")      # not derived from tasks or journal: rebuild may differ

_OPEN = ("pending", "in_progress")
_PHASE_PREFIXES = ("PREPARE:", "ARCHITECT:", "CODE:", "TEST:", "Review:")   # task_utils
_SIGNAL_TYPES = ("blocker", "algedonic")                                     # task_utils
_PLAN_SUBJECT = re.compile(r"^\s*plan\b", re.IGNORECASE)
_TEACHBACK_SUBJECT = re.compile(r"^[a-z0-9-]+: TEACHBACK\b")                 # task_utils
_CONTROL = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")


def _clean(value, limit: int = SUBJECT_CHARS) -> str:
    """One line of agent-written text, at most `limit` characters."""
    return _CONTROL.sub(" ", str(value)).strip()[:limit] if value is not None else ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _id_key(task_id: str):
    return (len(task_id), task_id)


# ---------------------------------------------------------------- the task projection

def project_task(task: dict) -> "tuple[str, Optional[dict]]":
    """(kind, entry) for one task file: kind "open", "signal", "accepted" or "" (not shown)."""
    if not isinstance(task, dict):
        return "", None
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    subject = str(task.get("subject") or "")
    status = task.get("status")
    base = {"subject": _clean(subject), "owner": _clean(task.get("owner"), 40)}
    if metadata.get("type") in _SIGNAL_TYPES:
        if status in _OPEN:
            return "signal", dict(base, level=_clean(metadata.get("level"), 20))
        return "", None
    if subject.startswith(_PHASE_PREFIXES) or _PLAN_SUBJECT.match(subject):
        return "", None
    handoff = metadata.get("handoff") if isinstance(metadata.get("handoff"), dict) else None
    if status == "completed":
        if handoff is None:
            return "", None
        return "accepted", dict(base, rev=handoff_rev(handoff), digest=payload_digest(handoff))
    if status not in _OPEN:
        return "", None
    entry = dict(base, status=status, wait=_wait(subject, status, metadata, handoff))
    stored = stored_teachback(metadata) if _TEACHBACK_SUBJECT.match(subject) else handoff
    if isinstance(stored, dict):
        entry["digest"] = payload_digest(stored)
        if stored is handoff:
            entry["rev"] = handoff_rev(handoff)
    undisposed = [key for key, _ in missing_dispositions(amendments(metadata), handoff or {})]
    if undisposed:
        entry["amends"] = undisposed
    return "open", entry


def _wait(subject: str, status: str, metadata: dict, handoff) -> str:
    """The task's wait state in a few words, for the lead."""
    if _TEACHBACK_SUBJECT.match(subject):
        if status == "pending":
            return "teachback not claimed"
        if metadata.get("teachback_rejection") and open_rejection(
                "teachback_rejection", metadata["teachback_rejection"], metadata):
            return "teachback rejected, being rewritten"
        if isinstance(stored_teachback(metadata), dict):
            return "teachback awaiting your acceptance"
        return "writing teachback"
    if status == "pending":
        return "pending"
    if metadata.get("handoff_rejection") and open_rejection(
            "handoff_rejection", metadata["handoff_rejection"], metadata):
        return "HANDOFF rejected, rework"
    if handoff is not None:
        return "HANDOFF stored, awaiting your acceptance"
    wait = metadata.get("intentional_wait")
    if isinstance(wait, dict) and wait.get("reason"):
        return "waiting: " + _clean(wait.get("reason"), 40)
    return "working"


def _apply_task(state: dict, task_id: str, task: Optional[dict]) -> None:
    """Put one task's projection into state (or take it out). `task` None: the file is gone."""
    kind, entry = project_task(task) if task else ("", None)
    tasks, signals = state.setdefault("tasks", {}), state.setdefault("signals", {})
    tasks.pop(task_id, None)
    signals.pop(task_id, None)
    accepted = [row for row in state.get("accepted", []) if row.get("id") != task_id]
    if kind == "open":
        tasks[task_id] = entry
    elif kind == "signal":
        signals[task_id] = entry
    elif kind == "accepted":
        accepted.append(dict(entry, id=task_id))
    accepted.sort(key=lambda row: _id_key(str(row.get("id"))), reverse=True)
    state["accepted"] = accepted[:MAX_ACCEPTED]
    if len(tasks) > MAX_TASKS:
        for key in sorted(tasks, key=_id_key)[: len(tasks) - MAX_TASKS]:
            tasks.pop(key)


# ---------------------------------------------------------------- the journal fold

def fold(state: dict, events: Iterable[dict]) -> None:
    """Fold journal events into state["fold"] and set state["phase"]; the feature is picked in _refresh."""
    folded = state.setdefault("fold", {"phases": {}, "features": []})
    phases, features = folded.setdefault("phases", {}), folded.setdefault("features", [])
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "phase_transition" and isinstance(event.get("phase"), str):
            phases[_clean(event["phase"], 20)] = [str(event.get("ts", "")),
                                                  _clean(event.get("status"), 20)]
        elif kind == "variety_assessed" and isinstance(event.get("task_id"), str):
            task_id = _clean(event["task_id"], 12)
            if task_id in features:
                features.remove(task_id)
            # A dispatch-scoped row marks a dispatched work task, which is never the feature
            # (shared/session_state._latest_feature_level_variety_event).
            if event.get("scope") != "dispatch":
                features.append(task_id)
    del features[:-MAX_FEATURES]
    started = [(ts, name) for name, (ts, status) in phases.items() if status == "started"]
    state["phase"] = max(started)[1] if started else None


def read_journal(session_dir: str, offset: int) -> "tuple[list, int]":
    """The events after byte `offset` and the offset of the end of the last whole line.
    A journal shorter than `offset` (rewritten) returns ([], -1): the caller rebuilds."""
    path = Path(session_dir) / JOURNAL_NAME
    try:
        size = path.stat().st_size
    except OSError:
        return [], 0
    if size < offset:
        return [], -1
    with open(path, "rb") as handle:
        handle.seek(offset)
        data = handle.read()
    end = data.rfind(b"\n") + 1
    events = []
    for line in data[:end].splitlines():
        with contextlib.suppress(ValueError):
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
    return events, offset + end


# ---------------------------------------------------------------- environment fields

def worktrees(project_dir: str) -> list:
    """[{path, branch}] of the project's git worktrees, read from the git directory with no
    subprocess, most recently used first (index mtime), at most MAX_WORKTREES."""
    common = _git_common_dir(project_dir)
    if common is None:
        return []
    found = []
    main = common.parent if common.name == ".git" else None
    if main is not None:
        found.append((main, common))
    with contextlib.suppress(OSError):
        for entry in common.joinpath("worktrees").iterdir():
            with contextlib.suppress(OSError):
                gitdir = entry.joinpath("gitdir").read_text(encoding="utf-8").strip()
                found.append((Path(gitdir).parent, entry))
    rows = []
    for path, admin in found:
        if not path.is_dir():
            continue
        with contextlib.suppress(OSError):
            head = admin.joinpath("HEAD").read_text(encoding="utf-8").strip()
            branch = head[len("ref: refs/heads/"):] if head.startswith("ref: refs/heads/") \
                else "detached"
            used = admin.joinpath("index").stat().st_mtime if admin.joinpath("index").exists() \
                else 0.0
            rows.append((used, _clean(str(path), 200), _clean(branch, 80)))
    rows.sort(key=lambda row: (-row[0], row[1]))
    return [{"path": path, "branch": branch} for _, path, branch in rows[:MAX_WORKTREES]]


def _git_common_dir(project_dir: str) -> Optional[Path]:
    if not project_dir:
        return None
    dot_git = Path(project_dir) / ".git"
    if dot_git.is_dir():
        return dot_git
    with contextlib.suppress(OSError):
        text = dot_git.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir: "):
            admin = (Path(project_dir) / text[len("gitdir: "):]).resolve()
            common = admin.joinpath("commondir")
            if common.exists():
                return (admin / common.read_text(encoding="utf-8").strip()).resolve()
            return admin
    return None


def read_cost(session_dir: str) -> Optional[dict]:
    """{usd, at}: the sum the session cost cap's cache already holds, or None when the cap has
    not counted this session (no cache). An estimate at list API prices, not billing."""
    from .session_spend import CACHE_FILE, load_cache

    path = Path(session_dir) / CACHE_FILE
    files = load_cache(path)
    if not files:
        return None
    try:
        usd = sum(float(entry["usd"]) for entry in files.values())
        at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {"usd": round(usd, 2), "at": at}


def _refresh(state: dict, team: str, project_dir: str, with_cost: bool, session_dir: str) -> None:
    """The fields recomputed on every write: feature subject, worktrees, stamp (and cost)."""
    from .task_store import read_task_json
    from .task_utils import _is_feature_candidate

    state["feature"] = None
    # The newest journal candidate that is still the lead's own open task: the journal
    # can stamp a work task feature-level before its dispatch row (task_utils.find_feature_task).
    for feature_id in reversed(state.get("fold", {}).get("features", [])):
        feature = read_task_json(feature_id, team)
        if feature and _is_feature_candidate(feature):
            state["feature"] = {"id": feature_id, "subject": _clean(feature.get("subject"))}
            break
    state["worktrees"] = worktrees(project_dir)
    if with_cost:
        state["cost"] = read_cost(session_dir)
    state["built_at"] = _now()


# ---------------------------------------------------------------- build, update, load

def rebuild(session_dir: str, team: str, project_dir: str) -> dict:
    """The whole state from the task files and the journal, as a dict (not written)."""
    from .task_store import iter_team_task_jsons

    state = {"v": VERSION, "team": team, "tasks": {}, "signals": {}, "accepted": []}
    for task in iter_team_task_jsons(team):
        task_id = str(task.get("id") or "")
        if task_id:
            _apply_task(state, task_id, task)
    events, offset = read_journal(session_dir, 0)
    fold(state, events)
    state["journal_offset"] = max(offset, 0)
    _refresh(state, team, project_dir, True, session_dir)
    return state


def update(session_dir: str, team: str, project_dir: str, task_ids: Iterable[str],
           with_cost: bool = False) -> Optional[dict]:
    """Re-project `task_ids` and fold the journal tail into the file, under its lock.
    A missing, unreadable or foreign file is rebuilt whole. None on any failure."""
    from .state_file import locked_update
    from .task_store import read_task_json

    task_ids = [str(task_id) for task_id in task_ids if task_id]

    def apply(text: str):
        state = None
        with contextlib.suppress(ValueError):
            state = json.loads(text) if text else None
        if not (isinstance(state, dict) and state.get("v") == VERSION and state.get("team") == team):
            state = rebuild(session_dir, team, project_dir)
        else:
            events, offset = read_journal(session_dir, int(state.get("journal_offset") or 0))
            if offset < 0:
                state = rebuild(session_dir, team, project_dir)
            else:
                for task_id in task_ids:
                    _apply_task(state, task_id, read_task_json(task_id, team) or None)
                fold(state, events)
                state["journal_offset"] = offset
                _refresh(state, team, project_dir, with_cost, session_dir)
        return json.dumps(state, sort_keys=True, indent=1), True, state

    try:
        return locked_update(Path(session_dir) / STATE_NAME, apply, Path(session_dir))
    except Exception:  # noqa: BLE001 - fail-open: the state file is a convenience
        return None


def write(session_dir: str, state: dict) -> bool:
    """Replace the file with `state` (the --rebuild path). False on failure."""
    from .state_file import write_text

    try:
        write_text(Path(session_dir) / STATE_NAME, json.dumps(state, sort_keys=True, indent=1),
                   Path(session_dir))
        return True
    except Exception:  # noqa: BLE001
        return False


def load(session_dir: str) -> Optional[dict]:
    """The stored state, or None when absent or unreadable."""
    with contextlib.suppress(OSError, ValueError):
        state = json.loads((Path(session_dir) / STATE_NAME).read_text(encoding="utf-8"))
        if isinstance(state, dict) and state.get("v") == VERSION:
            return state
    return None


def freshness(session_dir: str, state: Optional[dict]) -> str:
    """"missing", "stale" (a state-relevant journal event past the stored offset) or "fresh"."""
    if state is None:
        return "missing"
    events, offset = read_journal(session_dir, int(state.get("journal_offset") or 0))
    if offset < 0 or any(event.get("type") in RELEVANT_EVENTS for event in events):
        return "stale"
    return "fresh"


def comparable(state: dict) -> dict:
    """state without the fields a rebuild may legitimately see differently."""
    return {key: value for key, value in state.items() if key not in VOLATILE_KEYS}


# ---------------------------------------------------------------- the screen

def render(state: dict, fresh: str = "fresh", session_dir: str = "") -> str:
    """The resume screen: at most SCREEN_LINES lines; with `session_dir`, the line of an
    active autopilot run (shared/autopilot_run.py) so a compaction picks the loop up."""
    lines = [f"[RESUME] Workflow state at {state.get('built_at', '?')} ({fresh}; "
             "task files on disk are authoritative)"]
    feature = state.get("feature") or {}
    lines.append(f"Team: {state.get('team') or '?'} | Phase: {state.get('phase') or 'none'} | "
                 + (f"Feature: #{feature.get('id')} {feature.get('subject', '')}" if feature
                    else "Feature: none recorded"))
    cost = state.get("cost")
    if cost:
        lines.append(f"Cost so far: ${cost['usd']:.2f} at {cost['at']} (estimate at list API prices)")
    lines += [line for line in [session_dir and _autopilot_line(session_dir)] if line]
    trees = state.get("worktrees") or []
    if trees:
        lines.append("Worktrees: " + "; ".join(f"{_short(t['path'])} [{t['branch']}]" for t in trees[:4])
                     + (f"; +{len(trees) - 4} more" if len(trees) > 4 else ""))
    signals = state.get("signals") or {}
    for task_id in sorted(signals, key=_id_key):
        row = signals[task_id]
        lines.append(f"BLOCKER #{task_id} {row.get('level') or ''}: {row.get('subject')}".replace("  ", " "))
    tasks = {key: row for key, row in (state.get("tasks") or {}).items() if key != feature.get("id")}
    ordered = sorted(tasks, key=_id_key)
    room = SCREEN_LINES - len(lines) - 4
    lines.append(f"Open tasks ({len(tasks)}):")
    for index, task_id in enumerate(ordered):
        if index >= room:
            lines.append(f"  ... {len(ordered) - index} more: run TaskList")
            break
        row = tasks[task_id]
        extra = (f" rev {row['rev']}" if row.get("rev") is not None else "") \
            + (f" digest {row['digest']}" if row.get("digest") else "") \
            + (f" undisposed {','.join(row['amends'])}" if row.get("amends") else "")
        lines.append(f"  #{task_id} {row.get('owner') or 'unowned'}: {row.get('wait')}{extra} "
                     f"| {row.get('subject', '')[:50]}")
    accepted = state.get("accepted") or []
    if accepted:
        lines.append("Last accepted: " + ", ".join(
            f"#{row['id']} rev {row.get('rev')} {row.get('digest')}" for row in accepted[:5]))
    lines.append("Next: " + _next_step(signals, tasks))
    return "\n".join(lines[:SCREEN_LINES])


def _autopilot_line(session_dir: str) -> Optional[str]:
    from .autopilot_run import run_line   # fail-open: None on any error
    return run_line(session_dir)


def _short(path: str) -> str:
    parts = Path(path).parts
    return str(Path(*parts[-2:])) if len(parts) > 2 else path


def _next_step(signals: dict, tasks: dict) -> str:
    if signals:
        return "address the blockers before anything else."
    waiting = [f"#{k}" for k in sorted(tasks, key=_id_key) if "awaiting your" in tasks[k].get("wait", "")]
    if waiting:
        return f"accept or reject {', '.join(waiting[:8])} (read each task file first)."
    if tasks:
        return "monitor the open tasks via TaskList; nothing waits on you."
    return "no open work: check TaskList and ask the user how to proceed."


def context_screen(session_dir: str) -> Optional[str]:
    """The screen for an injected context, only when the file is fresh; None otherwise, so the
    caller keeps the recovery procedure. Never raises."""
    try:
        state = load(session_dir) if session_dir else None
        if freshness(session_dir, state) != "fresh":
            return None
        return render(state, "fresh", session_dir)
    except Exception:  # noqa: BLE001
        return None
