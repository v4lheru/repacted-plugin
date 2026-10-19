"""The per-project task log: the read side (D10 PROJECT-TASKS, package P1).

Location: pact-plugin/hooks/shared/project_tasks.py. Design:
the design notes sections 3, 4 and 7. Stdlib only, with no
subprocess, network or pact-memory in its import closure, so the session-start brief can replay a
log inline. Nothing here writes; the write side is hooks/shared/task_log/.

The log is `.repacted/tasks.jsonl` in the MAIN checkout of a repo: one JSON event per line,
appended, never rewritten. Reading is order-independent:
  - a line that is not JSON, fails the schema or has an unknown `v` is skipped and counted with
    its line number (never deleted);
  - events are deduped by `eid` (a byte-identical copy is dropped; one eid with two contents keeps
    the smallest canonical JSON and is flagged);
  - they are sorted by (ts, eid) and folded last-writer-wins per field, so a union merge in either
    direction replays to one state;
  - two `set`s of one field of one task with the same `base` and different values are flagged as a
    concurrent edit.
"""

from __future__ import annotations

import datetime
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

LOG_RELPATH = Path(".repacted") / "tasks.jsonl"
CACHE_RELPATH = Path(".repacted") / "cache" / "summary.json"
SCHEMA_VERSION = 1
STATUSES = ("planned", "active", "blocked", "done", "dropped")
SETTLED = frozenset({"done", "dropped"})
PRIORITIES = ("P0", "P1", "P2", "P3")
LINK_KINDS = ("input", "output", "reference")
REF_RE = re.compile(r"^T-[0-9a-f]{8}$")
EID_RE = re.compile(r"^\d{8}T\d{9}Z-[0-9a-f]{8}$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
LIST_FIELDS = ("tags", "batch_with", "exclusive_with")
WAIVER_KEYS = ("why", "by", "at")
SET_FIELDS = frozenset({"title", "status", "priority", "rank", "owner", "tags", "parent", "note",
                        "batch_with", "exclusive_with"})
OPS = frozenset({"create", "set", "dep_add", "dep_del", "link_add", "link_del", "done", "accept",
                 "reject", "reopen"})
TITLE_MAX = 80
NOTE_MAX = 500
SUMMARY_TITLE_MAX = 60
STALE_DAYS = 14


class LogMissing(Exception):
    """There is no task log here (run `repact-task init`)."""


class LogUnreadable(Exception):
    """The task log exists but could not be read."""


# ---------------------------------------------------------------------------------------------
# Where the log lives
# ---------------------------------------------------------------------------------------------

def _gitdir_of(checkout: Path, dot_git: Path) -> Optional[Path]:
    try:
        line = dot_git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    try:
        return (checkout / line.split(":", 1)[1].strip()).resolve()
    except OSError:
        return None


def _is_bare_repo(directory: Path) -> bool:
    return (directory / "HEAD").is_file() and (directory / "objects").is_dir() and \
        (directory / "refs").is_dir()


def find_main_checkout(start) -> Optional[Path]:
    """The MAIN checkout of the repo containing `start`, or None.

    Walks up to the nearest `.git`. A directory `.git` is a main checkout. A `.git` FILE names a
    gitdir: with a `commondir` it is a linked worktree, whose main checkout is the parent of the
    common dir; without one it is a submodule (or a separated gitdir), which is its own main
    checkout. A bare repo, a worktree of a bare repo, or no repo at all gives None: the caller
    refuses rather than guess.
    """
    try:
        here = Path(start).resolve()
    except OSError:
        return None
    if here.is_file():
        here = here.parent
    for directory in (here, *here.parents):
        dot_git = directory / ".git"
        if dot_git.is_dir():
            return directory
        if dot_git.is_file():
            gitdir = _gitdir_of(directory, dot_git)
            if gitdir is None:
                return None
            commondir = gitdir / "commondir"
            if not commondir.is_file():
                return directory
            try:
                common = (gitdir / commondir.read_text(encoding="utf-8").strip()).resolve()
            except OSError:
                return None
            return common.parent if common.name == ".git" else None
        if _is_bare_repo(directory):
            return None
    return None


def log_path(main: Path) -> Path:
    return Path(main) / LOG_RELPATH


def log_stamp(path: Path) -> Optional[Tuple[int, int]]:
    """(size, mtime_ns) of the log, or None when it cannot be stat'ed."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------

@dataclass
class Parsed:
    events: List[dict]
    corrupt: List[Tuple[int, str]]      # (1-based line number, reason)
    ends_with_newline: bool = True


def _is_ref(value) -> bool:
    return isinstance(value, str) and bool(REF_RE.match(value))


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _check_value(fieldname: str, value) -> Optional[str]:
    if fieldname == "title":
        return None if isinstance(value, str) and value else "title"
    if fieldname == "status":
        return None if value in STATUSES and value != "done" else "status"
    if fieldname == "priority":
        return None if value in PRIORITIES else "priority"
    if fieldname == "rank":
        return None if value is None or (isinstance(value, int) and not isinstance(value, bool)
                                          and value >= 0) else "rank"
    if fieldname in ("owner", "note"):
        return None if value is None or isinstance(value, str) else fieldname
    if fieldname == "parent":
        return None if value is None or _is_ref(value) else "parent"
    if fieldname in LIST_FIELDS:
        return None if _is_str_list(value) else fieldname
    return "field"


def _evidence_ok(items) -> bool:
    return isinstance(items, list) and all(
        isinstance(item, dict) and isinstance(item.get("target"), str)
        and isinstance(item.get("verified"), bool) for item in items)


def _any_verified(items) -> bool:
    return any(item["verified"] for item in items)


def _waiver_ok(event: dict) -> bool:
    """An absent waiver, or {why, by, at} as three non-empty strings."""
    if "waiver" not in event:
        return True
    waiver = event["waiver"]
    return isinstance(waiver, dict) and all(
        isinstance(waiver.get(key), str) and waiver[key].strip() for key in WAIVER_KEYS)


def event_problem(event) -> Optional[str]:
    """Why `event` fails the schema, or None when it is well-formed."""
    if not isinstance(event, dict):
        return "not an object"
    if event.get("v") != SCHEMA_VERSION:
        return "unknown v"
    for key, pattern in (("eid", EID_RE), ("ts", TS_RE)):
        if not isinstance(event.get(key), str) or not pattern.match(event[key]):
            return f"bad {key}"
    if not isinstance(event.get("by"), str) or not event["by"]:
        return "bad by"
    op = event.get("op")
    if op not in OPS:
        return "unknown op"
    if not _is_ref(event.get("t")):
        return "bad t"
    if event.get("base") is not None and not (isinstance(event["base"], str) and EID_RE.match(event["base"])):
        return "bad base"
    if op == "create":
        for key in ("title", "status", "priority", "rank", "owner", "parent", "note", *LIST_FIELDS):
            if key in event or key in ("title", "status", "priority"):
                problem = _check_value(key, event.get(key))
                if problem:
                    return f"bad {problem}"
    elif op == "set":
        if event.get("field") not in SET_FIELDS:
            return "bad field"
        problem = _check_value(event["field"], event.get("value"))
        if problem:
            return f"bad {problem}"
    elif op in ("dep_add", "dep_del"):
        if not _is_ref(event.get("on")):
            return "bad on"
    elif op in ("link_add", "link_del"):
        if event.get("kind") not in LINK_KINDS or not isinstance(event.get("target"), str) \
                or not event["target"]:
            return "bad link"
    elif op in ("done", "accept") and not _waiver_ok(event):
        return "bad waiver"
    elif op == "done":
        if not _evidence_ok(event.get("evidence")):
            return "bad evidence"
        if not _any_verified(event["evidence"]):
            return "done without verified evidence"   # a hand-written line cannot close a task
    elif op in ("accept", "reject"):
        if not isinstance(event.get("ptask"), str) or not isinstance(event.get("rev"), int):
            return "bad ptask"
        if op == "accept" and (not _evidence_ok(event.get("evidence"))
                               or not isinstance(event.get("verified"), bool)):
            return "bad evidence"
        if op == "accept" and event["verified"] and not _any_verified(event["evidence"]):
            return "verified accept without verified evidence"
    elif op == "reopen":
        if event.get("note") is not None and not isinstance(event["note"], str):
            return "bad note"
    return None


def parse_bytes(data: bytes) -> Parsed:
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    events, corrupt = [], []
    for number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            corrupt.append((number, "not JSON"))
            continue
        problem = event_problem(event)
        if problem:
            corrupt.append((number, problem))
        else:
            events.append(event)
    return Parsed(events, corrupt, ends_with_newline=not data or data.endswith(b"\n"))


def read_log(path: Path) -> Parsed:
    """Parse the log at `path`. Raises LogMissing when absent, LogUnreadable when unreadable."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError as err:
        raise LogMissing(str(path)) from err
    except OSError as err:
        raise LogUnreadable(f"{path}: {err.strerror or err}") from err
    return parse_bytes(data)


# ---------------------------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------------------------

@dataclass
class State:
    tasks: Dict[str, dict] = field(default_factory=dict)
    concurrent: List[dict] = field(default_factory=list)       # {ref, field, values}
    eid_conflicts: List[dict] = field(default_factory=list)    # {eid, count}
    unknown: List[dict] = field(default_factory=list)          # {eid, ref}: an event for no task
    ref_conflicts: List[dict] = field(default_factory=list)    # {ref, eid}: a second create
    corrupt: List[Tuple[int, str]] = field(default_factory=list)

    @property
    def corrupt_count(self) -> int:
        return len(self.corrupt)


def canonical(event: dict) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _dedupe(events: List[dict]) -> Tuple[List[dict], List[dict]]:
    by_eid: Dict[str, List[dict]] = {}
    for event in events:
        by_eid.setdefault(event["eid"], []).append(event)
    kept, conflicts = [], []
    for eid, group in by_eid.items():
        if len(group) == 1:                   # the common case: no canonical form needed
            kept.append(group[0])
            continue
        versions = {canonical(event): event for event in group}
        kept.append(versions[min(versions)])
        if len(versions) > 1:
            conflicts.append({"eid": eid, "count": len(versions)})
    return kept, sorted(conflicts, key=lambda item: item["eid"])


def _new_task(event: dict) -> dict:
    title = " ".join(str(event.get("title", "")).split())
    return {"ref": event["t"], "title": title, "status": event.get("status", "planned"),
            "priority": event.get("priority", "P2"), "rank": event.get("rank"),
            "owner": event.get("owner"), "tags": list(event.get("tags") or []),
            "parent": event.get("parent"), "depends_on": [],
            "batch_with": list(event.get("batch_with") or []),
            "exclusive_with": list(event.get("exclusive_with") or []), "note": event.get("note"),
            "links": [], "evidence": [], "prior_evidence": [], "accepts": [], "rejects": [],
            "created": event["ts"], "updated": event["ts"], "last_eid": event["eid"]}


def _apply(task: dict, event: dict) -> None:
    op = event["op"]
    if op == "set":
        value = event["value"]
        task[event["field"]] = " ".join(value.split()) if event["field"] == "title" else value
    elif op == "dep_add" and event["on"] not in task["depends_on"]:
        task["depends_on"].append(event["on"])
    elif op == "dep_del" and event["on"] in task["depends_on"]:
        task["depends_on"].remove(event["on"])
    elif op in ("link_add", "link_del"):
        link = {"kind": event["kind"], "target": event["target"]}
        if op == "link_add" and link not in task["links"]:
            task["links"].append(link)
        elif op == "link_del" and link in task["links"]:
            task["links"].remove(link)
    elif op == "done":
        task["status"] = "done"
        task["evidence"].extend(event["evidence"])
        if event.get("waiver"):
            task["waiver"] = event["waiver"]
    elif op == "accept":
        task["accepts"].append({"ptask": event["ptask"], "rev": event["rev"],
                                "verified": event["verified"], "ts": event["ts"]})
        if event["verified"] or event.get("waiver"):   # an attended waiver closes, and says so
            task["status"] = "done"
            task["evidence"].extend(event["evidence"])
        if event.get("waiver"):
            task["waiver"] = event["waiver"]
    elif op == "reject":
        task["rejects"].append({"ptask": event["ptask"], "rev": event["rev"], "ts": event["ts"]})
    elif op == "reopen":
        task["status"] = "planned" if task["status"] == "dropped" else "active"
        task["prior_evidence"].extend(task["evidence"])
        task["evidence"] = []
        task.pop("waiver", None)
        if event.get("note"):
            task["note"] = event["note"]
    task["updated"] = event["ts"]
    task["last_eid"] = event["eid"]


def _concurrent_edits(events: List[dict]) -> List[dict]:
    groups: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    for event in events:
        if event["op"] == "set" and event.get("base"):
            key = (event["t"], event["field"], event["base"])
            groups.setdefault(key, {})[json.dumps(event["value"], sort_keys=True)] = event["value"]
    found = [{"ref": ref, "field": name, "values": sorted(values.values(), key=json.dumps)}
             for (ref, name, _base), values in groups.items() if len(values) > 1]
    return sorted(found, key=lambda item: (item["ref"], item["field"], json.dumps(item["values"])))


def replay(events: List[dict], corrupt: Optional[List[Tuple[int, str]]] = None) -> State:
    """Fold `events` (in any order) into one State."""
    kept, conflicts = _dedupe(events)
    kept.sort(key=lambda event: (event["ts"], event["eid"]))
    state = State(eid_conflicts=conflicts, corrupt=list(corrupt or []))
    for event in kept:                                  # creates first: a set never precedes its task
        if event["op"] != "create":
            continue
        if event["t"] in state.tasks:                   # a cross-clone ref collision: flagged, never silent
            state.ref_conflicts.append({"ref": event["t"], "eid": event["eid"]})
        else:
            state.tasks[event["t"]] = _new_task(event)
    for event in kept:
        if event["op"] == "create":
            continue
        task = state.tasks.get(event["t"])
        if task is None:
            state.unknown.append({"eid": event["eid"], "ref": event["t"]})
            continue
        _apply(task, event)
    state.concurrent = _concurrent_edits(kept)
    return state


def load(main: Path) -> State:
    """Read and replay the log of the main checkout `main`."""
    parsed = read_log(log_path(main))
    return replay(parsed.events, parsed.corrupt)


def state_digest(state: State) -> str:
    """A canonical string of the replayed state, for equality checks."""
    return json.dumps({"tasks": state.tasks, "concurrent": state.concurrent,
                       "eid_conflicts": state.eid_conflicts, "unknown": state.unknown,
                       "ref_conflicts": state.ref_conflicts},
                      sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------------------------
# Derived states and queries (section 4.3)
# ---------------------------------------------------------------------------------------------

def ancestors(state: State, ref: str) -> List[str]:
    chain, seen, current = [], {ref}, state.tasks.get(ref, {}).get("parent")
    while current and current not in seen:
        chain.append(current)
        seen.add(current)
        current = state.tasks.get(current, {}).get("parent")
    return chain


def open_children(state: State) -> Dict[str, List[str]]:
    """{parent ref: [its open (unsettled) children]}; build once per query, pass to classify."""
    index: Dict[str, List[str]] = {}
    for ref, task in state.tasks.items():
        if task["parent"] and task["status"] not in SETTLED:
            index.setdefault(task["parent"], []).append(ref)
    return {parent: sorted(refs) for parent, refs in index.items()}


def classify(state: State, ref: str, children: Optional[Dict[str, List[str]]] = None) -> str:
    """ready, waiting or stuck for a planned task; otherwise its status. A parent with an open
    child waits for it: a container is never the next thing to do."""
    task = state.tasks[ref]
    if task["status"] != "planned":
        return task["status"]
    if any(state.tasks.get(up, {}).get("status") == "dropped" for up in ancestors(state, ref)):
        return "stuck"
    deps = [state.tasks.get(dep) for dep in task["depends_on"]]
    if any(dep is None or dep["status"] == "dropped" for dep in deps):
        return "stuck"
    if any(dep["status"] != "done" for dep in deps):
        return "waiting"
    if (open_children(state) if children is None else children).get(ref):
        return "waiting"
    return "ready"


def open_deps(state: State, ref: str) -> List[str]:
    return [dep for dep in state.tasks[ref]["depends_on"]
            if state.tasks.get(dep, {}).get("status") != "done"]


def _order(task: dict):
    rank = task["rank"] if task["rank"] is not None else math.inf
    return (PRIORITIES.index(task["priority"]), rank, task["created"], task["ref"])


def ready(state: State) -> List[dict]:
    children = open_children(state)
    return sorted((task for ref, task in state.tasks.items()
                   if classify(state, ref, children) == "ready"), key=_order)


def active(state: State) -> List[dict]:
    return sorted((task for task in state.tasks.values() if task["status"] == "active"), key=_order)


def ptasks_of(task: dict) -> List[str]:
    """The `<team>/<id>` of every ptask: link on `task`, in link order."""
    return [link["target"][len("ptask:"):] for link in task["links"]
            if link["kind"] == "reference" and link["target"].startswith("ptask:")]


def tasks_for_ptask(state: State, team: str, task_id: str) -> List[dict]:
    """Every task that links the platform task `<team>/<task_id>`, by ref. One unit of team work
    routinely carries several project tasks (the 1.4.1 session: 13 refs on one task), so the
    bridge and the views read the list; a single-ref caller uses task_for_ptask."""
    link = {"kind": "reference", "target": f"ptask:{team}/{task_id}"}
    return sorted((task for task in state.tasks.values() if link in task["links"]),
                  key=lambda task: task["ref"])


def task_for_ptask(state: State, team: str, task_id: str) -> Optional[dict]:
    """The one task that links the platform task `<team>/<task_id>`, or None when none or
    several do (a caller that can handle several uses tasks_for_ptask)."""
    found = tasks_for_ptask(state, team, task_id)
    return found[0] if len(found) == 1 else None


def blocked(state: State) -> List[Tuple[dict, str, List[str]]]:
    """(task, waiting|stuck|blocked, blockers) for every task that cannot start."""
    out, children = [], open_children(state)
    for ref, task in state.tasks.items():
        kind = classify(state, ref, children)
        if kind in ("waiting", "stuck"):
            blockers = [dep for dep in task["depends_on"]
                        if state.tasks.get(dep, {}).get("status") != "done"]
            blockers += children.get(ref, []) if kind == "waiting" else []
            blockers += [up for up in ancestors(state, ref)
                         if state.tasks.get(up, {}).get("status") == "dropped"]
            out.append((task, kind, blockers))
        elif kind == "blocked":
            out.append((task, kind, []))
    return sorted(out, key=lambda row: _order(row[0]))


def accepted_without_evidence(state: State) -> List[dict]:
    return [task for task in state.tasks.values() if task["status"] != "done"
            and any(not accept["verified"] for accept in task["accepts"])]


def _age_days(ts: str, now: datetime.datetime) -> float:
    then = datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc)
    return (now - then).total_seconds() / 86400


def drift_flags(state: State, now: Optional[datetime.datetime] = None) -> List[str]:
    """File-local drift, one line each (section 4.3)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    flags, children = [], open_children(state)          # built once: classify per task stays linear
    for ref, task in sorted(state.tasks.items()):
        related = task["depends_on"] + task["batch_with"] + task["exclusive_with"] + \
            ([task["parent"]] if task["parent"] else [])
        for other in related:
            if other not in state.tasks:
                flags.append(f"{ref}: relation names unknown {other}")
        if classify(state, ref, children) == "stuck":
            flags.append(f"{ref}: stuck (depends on a dropped task)")
        if task["status"] == "active" and _age_days(task["updated"], now) > STALE_DAYS:
            flags.append(f"{ref}: active and untouched for over {STALE_DAYS} days")
        if task["status"] == "planned" and task["rank"] is None \
                and _age_days(task["updated"], now) > STALE_DAYS:
            flags.append(f"{ref}: planned, unranked and untouched for over {STALE_DAYS} days")
        if task["status"] == "active":
            for other in task["exclusive_with"]:
                if ref < other and state.tasks.get(other, {}).get("status") == "active":
                    flags.append(f"{ref}: active together with exclusive {other}")
    flags += [f"{task['ref']}: accepted without evidence" for task in accepted_without_evidence(state)]
    flags += [f"{item['ref']}: concurrent edit: {item['field']} "
              + " vs ".join(json.dumps(value) for value in item["values"]) for item in state.concurrent]
    flags += [f"eid {item['eid']} has {item['count']} different contents" for item in state.eid_conflicts]
    flags += [f"{item['ref']}: second create ({item['eid']}) ignored; the ref collides"
              for item in state.ref_conflicts]
    flags += [f"line {line}: corrupt ({reason})" for line, reason in state.corrupt]
    return flags


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------

def one_line(text: str, limit: int) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def render_line(state: State, task: dict) -> str:
    """`T-1a2b3c4d  active  P1  owner  title  [deps 2 open]`"""
    line = f"{task['ref']}  {task['status']}  {task['priority']}  {task['owner'] or '-'}  " \
           f"{one_line(task['title'], TITLE_MAX)}"
    pending = open_deps(state, task["ref"])
    return line + (f"  [deps {len(pending)} open]" if pending else "")


def summary_line(state: State) -> str:
    """The session-start line (section 7), or "" when there is nothing to say."""
    queue, working = ready(state), active(state)
    stalled, unproven = blocked(state), accepted_without_evidence(state)
    if not (queue or working or stalled or unproven or state.corrupt):
        return ""
    parts = []
    if queue:
        head = f'next {queue[0]["ref"]} "{one_line(queue[0]["title"], SUMMARY_TITLE_MAX)}"'
        parts.append(head + (f" (+{len(queue) - 1} ready)" if len(queue) > 1 else ""))
    if working:
        parts.append(f"active {len(working)}")
    if stalled:
        parts.append(f"blocked {len(stalled)}")
    if unproven:
        parts.append(f"{len(unproven)} accepted without evidence")
    if state.corrupt:
        parts.append(f"corrupt {len(state.corrupt)}")
    return "tasks: " + " · ".join(parts)


def cached_summary(main: Path) -> Optional[str]:
    """The cached summary line when the cache's stamp equals the log's, else None. Never writes."""
    stamp = log_stamp(log_path(main))
    try:
        cache = json.loads((Path(main) / CACHE_RELPATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if stamp is None or not isinstance(cache, dict) or cache.get("stamp") != list(stamp):
        return None
    line = cache.get("line")
    return line if isinstance(line, str) else None
