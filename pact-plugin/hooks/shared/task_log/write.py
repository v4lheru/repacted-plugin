"""Locked, validated appends to the project task log (D10 P1; design sections 3.2, 4 and 9).

Location: pact-plugin/hooks/shared/task_log/write.py. Used by bin/repact-task, the bin/_tasks
importers, views and atlas, the acceptance bridge and hooks/amendment_gate.py. Every write:
  1. takes flock(LOCK_EX) on `.repacted/tasks.lock`;
  2. replays the log and validates the new events against that state: cycles in depends_on and
     parent, unique refs, the status and evidence rules, credential text (D25) and paths (D26);
  3. writes one `\\n` first when the file does not end in one, so a partial line left by a crashed
     writer stays isolated as its own corrupt line;
  4. appends each event as ONE O_APPEND write() of one line of 4 KB or less;
  5. releases the lock and refreshes `.repacted/cache/summary.json` (best-effort).
A refusal raises Refused and writes nothing. Nothing is ever deleted or rewritten.

REPACT_TASKS_TEST_HOLD_S (tests only) sleeps that many seconds between validation and the append,
while the lock is held, so the concurrency tests can widen the race window.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import importlib.util
import json
import os
import re
import secrets
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional

HOOKS_DIR = Path(__file__).resolve().parents[2]
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
from shared import plugin_paths, scrub, vault_scope  # noqa: E402
from shared.task_log import evidence as _evidence  # noqa: E402
from shared.task_log.evidence import RUN_NAME_RE, run_name_ok  # noqa: E402,F401  re-export: callers name W.run_name_ok


def _load_reader():
    existing = sys.modules.get("project_tasks")
    if existing is not None:
        return existing
    path = HOOKS_DIR / "shared" / "project_tasks.py"
    spec = importlib.util.spec_from_file_location("project_tasks", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["project_tasks"] = module
    spec.loader.exec_module(module)
    return module


PT = _load_reader()

LOCK_RELPATH = Path(".repacted") / "tasks.lock"
MAX_LINE_BYTES = 4096
UNION_LINE = ".repacted/tasks.jsonl merge=union"
IGNORE_LINES = (".repacted/tasks.lock", ".repacted/cache/")
TAG_RE = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
PTASK_RE = re.compile(r"^[A-Za-z0-9_.-]+/\d+$")
PARKED_TAG = "parked"                   # a row parked for a human; repact decide lists it
PARKED_NOTE = "parked for a human: "
DECISION_RE = re.compile(r"^D\d+[a-z]?$")
LEGACY_RE = re.compile(r"^[0-9a-f]{4}$")      # a legacy pact-backlog item id
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")       # never a glob pattern or a path
TEXT_PATH = "repacted-task"          # the path argument credential_reason checks titles against


class TaskError(Exception):
    code = 65


class Refused(TaskError):
    """The write was refused; nothing was written."""
    code = 65


class Usage(TaskError):
    code = 64


class NoLog(TaskError):
    code = 66


class Unreadable(TaskError):
    code = 74


# ---------------------------------------------------------------------------------------------
# Stamps
# ---------------------------------------------------------------------------------------------

def _stamp(after: Optional[str]) -> str:
    """A UTC millisecond timestamp, strictly after `after` so one machine's events keep order."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if after:
        floor = datetime.datetime.strptime(after, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=datetime.timezone.utc) + datetime.timedelta(milliseconds=1)
        now = max(now, floor)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _eid(ts: str) -> str:
    compact = ts.replace("-", "").replace(":", "").replace(".", "")
    return f"{compact}-{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------------------------
# Write-time rules
# ---------------------------------------------------------------------------------------------

def _flatten(text: str) -> str:
    return " ".join(str(text).split())


def check_text(*texts: str) -> None:
    """D25: refuse credential text, naming only its kind (writers withhold, never mask)."""
    for text in texts:
        if not isinstance(text, str) or not text:
            continue
        kind = scrub.credential_reason(TEXT_PATH, text)
        if kind:
            raise Refused(f"task text carries a credential value ({kind}); nothing written")


def _title(value: str) -> str:
    title = _flatten(value)
    if not title:
        raise Refused("a task needs a title")
    if len(title) > PT.TITLE_MAX:
        raise Refused(f"the title is {len(title)} characters; the limit is {PT.TITLE_MAX}")
    return title


def open_twin(state, title: str) -> Optional[str]:
    """The ref of an open (not done, not dropped) task whose title equals `title` once case and
    whitespace are normalised, else None."""
    wanted = _flatten(title).casefold()
    for ref, task in state.tasks.items():
        if task["status"] not in PT.SETTLED and _flatten(task.get("title") or "").casefold() == wanted:
            return ref
    return None


def _field_value(name: str, value):
    """Validate one settable field's value; returns the value to record."""
    if name == "title":
        return _title(value)
    if name == "note":
        if value is not None and len(value) > PT.NOTE_MAX:
            raise Refused(f"the note is {len(value)} characters; the limit is {PT.NOTE_MAX}")
        return value
    if name == "priority":
        if value not in PT.PRIORITIES:
            raise Refused(f"priority must be one of {', '.join(PT.PRIORITIES)}")
        return value
    if name == "rank":
        if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
            raise Refused("rank must be a whole number, 0 or more")
        return value
    if name == "parent":
        if value is not None and not PT.REF_RE.match(value):
            raise Refused("parent must be a task ref")
        return value
    if name == "owner":
        if value is not None and not PT.NAME_RE.match(value):
            raise Refused("owner must be an agent name, lead or user")
        return value
    if name in PT.LIST_FIELDS:
        values = list(value)
        if name == "tags":
            bad = [tag for tag in values if not TAG_RE.match(tag)]
        else:
            bad = [ref for ref in values if not PT.REF_RE.match(ref)]
        if bad:
            raise Refused(f"{name} has invalid entries: {len(bad)}")
        return values
    raise Usage(f"unknown field {name}")


def _path_ok(path: str) -> bool:
    return _evidence.safe_relpath(path)


def check_target(main: Path, target: str, evidence: bool = False, importer: bool = False) -> None:
    """D26 and the per-kind rules of design section 4.4, for a link or (evidence=True) an
    evidence item. Only a LINK to commit:/merge:/tag: must already exist; evidence is verified
    separately and may be recorded unverified. `legacy:<4 hex>` is accepted only from the
    importers (importer=True). Raises Refused; nothing is written."""
    prefix, _, rest = target.partition(":")
    if not rest:
        raise Refused(f"target {target!r} has no kind prefix")
    if evidence and prefix not in _evidence.EVIDENCE_KINDS:
        raise Refused(f"{prefix}: is not an evidence kind (use {', '.join(_evidence.EVIDENCE_KINDS)})")
    if prefix == "kb":
        if not _path_ok(rest):
            raise Refused("a kb: link must be a path relative to the KB root, with no `..`")
        try:
            root = vault_scope.kb_root()
        except vault_scope.VaultScopeError as err:
            raise Refused(f"the KB root is not configured: {err}") from err
        if not vault_scope.is_within(root / rest, root):
            raise Refused("that kb: link resolves outside the KB root")
    elif prefix == "doc":
        path = rest.rpartition("@")[0] if evidence else rest.split("#", 1)[0]
        if not _path_ok(path.split("#", 1)[0]):
            raise Refused("a doc: target must be repo-relative, with no absolute path and no `..`")
    elif prefix == "decision":
        if not DECISION_RE.match(rest):
            raise Refused("a decision: link names a decision id like D12")
        decisions = main / "docs" / "decisions.md"
        if decisions.exists() and not re.search(rf"\b{re.escape(rest)}\b", decisions.read_text(
                encoding="utf-8", errors="replace")):
            raise Refused(f"{rest} is not in docs/decisions.md")
    elif prefix in ("commit", "merge", "tag"):
        pattern = _evidence.TAG_RE if prefix == "tag" else _evidence.SHA_RE
        if not pattern.match(rest):
            raise Refused(f"{target!r} is not a valid {prefix} reference")
        if not evidence:
            verified, reason = _evidence.verify(main, target)
            if not verified:
                raise Refused(f"{target}: {reason}")
    elif prefix == "ptask":
        if not PTASK_RE.match(rest):
            raise Refused("a ptask: link is <team>/<task id>")
    elif prefix == "task":
        if not PT.REF_RE.match(rest):
            raise Refused("a task: link names a task ref like T-1a2b3c4d")
    elif prefix == "run":
        if not run_name_ok(rest):
            raise Refused("a run: target is a bare log file name (no `/`, `..` or `~`)")
    elif prefix in ("branch", "issue", "memory"):
        if any(ch.isspace() for ch in rest):
            raise Refused(f"a {prefix}: link has no spaces")
    elif prefix == "legacy":
        if not importer:
            raise Refused("legacy: links are written only by the importer")
        if not LEGACY_RE.match(rest):
            raise Refused("a legacy: link names a legacy backlog id (4 hex characters)")
    else:
        raise Refused(f"unknown link kind {prefix!r}")


def check_link(main: Path, target: str) -> None:
    check_target(main, target)


def _dependency_path(state, start: str, goal: str) -> Optional[List[str]]:
    """A depends_on path from start to goal, or None."""
    stack, seen = [(start, [start])], set()
    while stack:
        ref, path = stack.pop()
        if ref == goal:
            return path
        if ref in seen:
            continue
        seen.add(ref)
        for dep in state.tasks.get(ref, {}).get("depends_on", []):
            stack.append((dep, path + [dep]))
    return None


def _task(state, ref: str) -> dict:
    task = state.tasks.get(ref)
    if task is None:
        raise Refused(f"no task {ref}")
    return task


def check_parent(state, parent: str) -> None:
    """A settled (done or dropped) task takes no new child: its closure would silently stop
    meaning 'all its work is done'. Raises Refused."""
    status = _task(state, parent)["status"]
    if status in PT.SETTLED:
        raise Refused(f"{parent} is {status}; a settled task takes no new child (reopen it first)")


def open_descendants(state, ref: str) -> List[str]:
    """Every open task under `ref` (children, their children, ...), sorted."""
    out, frontier = set(), [ref]
    while frontier:
        parent = frontier.pop()
        for child, row in state.tasks.items():
            if row["parent"] == parent and child not in out and row["status"] not in PT.SETTLED:
                out.add(child)
                frontier.append(child)
    return sorted(out)


def with_team(name: str, session_id: Optional[str] = None) -> str:
    """`name@<team>` when the session's pact-session-context.json names a team the `by` rule
    accepts, else `name`. The session is `session_id`, else $CLAUDE_CODE_SESSION_ID."""
    if session_id is None:
        session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        return name
    for context in (plugin_paths.claude_config_dir() / "pact-sessions").glob(f"*/{session_id}/pact-session-context.json"):
        try:
            team = json.loads(context.read_text(encoding="utf-8")).get("team_name")
        except (OSError, ValueError, AttributeError):
            return name
        label = f"{name}@{team}" if isinstance(team, str) and team else name
        return label if PT.NAME_RE.match(label) else name
    return name


# ---------------------------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------------------------

class Store:
    """The task log of one main checkout."""

    def __init__(self, main: Path, by: Optional[str] = None):
        self.main = Path(main)
        self._by = by or os.environ.get("REPACT_TASK_BY")
        self.log = PT.log_path(self.main)
        self.lock = self.main / LOCK_RELPATH
        self.cache = self.main / PT.CACHE_RELPATH

    @property
    def by(self) -> str:
        """Who writes: resolved at the first write, so a read never looks up the session."""
        if not self._by:
            self._by = with_team("lead")
        return self._by

    @classmethod
    def locate(cls, start, by: Optional[str] = None) -> "Store":
        main = PT.find_main_checkout(start)
        if main is None:
            raise NoLog("not inside a git checkout with a working tree; nothing to read")
        store = cls(main, by)
        if not store.log.exists():
            raise NoLog(f"no task log in {main}; run `repact-task init`")
        return store

    # -- reading ---------------------------------------------------------------------------------

    def _parse(self):
        try:
            return PT.read_log(self.log)
        except PT.LogMissing as err:
            raise NoLog(f"no task log in {self.main}; run `repact-task init`") from err
        except PT.LogUnreadable as err:
            raise Unreadable(f"the task log could not be read: {err}") from err

    def state(self):
        parsed = self._parse()
        return PT.replay(parsed.events, parsed.corrupt)

    # -- the locked append -------------------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self):
        self.lock.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _commit(self, build: Callable) -> List[dict]:
        """Replay and validate under the lock, then append. `build(state)` returns the events
        (without v/eid/ts/by/base) or raises Refused."""
        with self._locked():
            written = self.append_locked(build)
        if written:
            self.refresh_cache()
        return written

    def append_locked(self, build: Callable) -> List[dict]:
        """_commit's body, for a caller that already holds `_locked()` (the ledger writes its own
        file and the log's proposed tasks under one lock). The caller refreshes the cache."""
        parsed = self._parse()
        state = PT.replay(parsed.events, parsed.corrupt)
        drafts = build(state)
        if not drafts:
            return []
        latest = max((event["ts"] for event in parsed.events), default=None)
        bases = {ref: task["last_eid"] for ref, task in state.tasks.items()}
        lines, written = [], []
        for draft in drafts:
            ts = _stamp(latest)
            latest = ts
            event = {"v": PT.SCHEMA_VERSION, "eid": _eid(ts), "ts": ts, "by": self.by, **draft}
            if bases.get(draft["t"]):
                event["base"] = bases[draft["t"]]
            problem = PT.event_problem(event)
            if problem:
                raise Refused(f"the event would not parse ({problem}); nothing written")
            line = (json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
            if len(line) > MAX_LINE_BYTES:
                longest = max(draft, key=lambda key: len(json.dumps(draft[key])))
                raise Refused(f"the event is {len(line)} bytes; the limit is {MAX_LINE_BYTES} "
                              f"(longest field: {longest})")
            bases[draft["t"]] = event["eid"]
            lines.append(line)
            written.append(event)
        hold = float(os.environ.get("REPACT_TASKS_TEST_HOLD_S") or 0)
        if hold > 0:
            time.sleep(hold)
        fd = os.open(self.log, os.O_WRONLY | os.O_APPEND)
        try:
            if not parsed.ends_with_newline:
                os.write(fd, b"\n")
            for line in lines:
                os.write(fd, line)
        finally:
            os.close(fd)
        return written

    def refresh_cache(self) -> Optional[str]:
        """Rewrite .repacted/cache/summary.json from a fresh replay; best-effort."""
        try:
            stamp = PT.log_stamp(self.log)
            line = PT.summary_line(self.state())
            if stamp is None:
                return line
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", dir=self.cache.parent, delete=False,
                                             suffix=".tmp", encoding="utf-8") as tmp:
                json.dump({"stamp": list(stamp), "line": line}, tmp)
            os.replace(tmp.name, self.cache)
            return line
        except (OSError, TaskError):
            return None

    # -- operations ----------------------------------------------------------------------------------

    def add(self, title: str, priority: str = "P2", rank: Optional[int] = None,
            owner: Optional[str] = None, tags: Iterable[str] = (), parent: Optional[str] = None,
            depends: Iterable[str] = (), note: Optional[str] = None, force: bool = False) -> str:
        """A title equal (case and whitespace aside) to an open task's is refused unless `force`;
        the check runs under the write lock, so two racing adds cannot both pass it."""
        tags, depends = list(tags), list(depends)
        check_text(title, note or "", *tags)
        created = {}

        def build(state):
            twin = None if force else open_twin(state, title)
            if twin:
                raise Refused(f"{twin} is open with the same title; nothing written "
                              "(--force adds a second one)")
            ref = "T-" + secrets.token_hex(4)
            while ref in state.tasks:
                ref = "T-" + secrets.token_hex(4)
            create = {"op": "create", "t": ref, "title": _title(title), "status": "planned",
                      "priority": _field_value("priority", priority)}
            for key, value in (("rank", rank), ("owner", owner), ("note", note)):
                if value is not None:
                    create[key] = _field_value(key, value)
            if tags:
                create["tags"] = _field_value("tags", tags)
            if parent is not None:
                check_parent(state, parent)
                create["parent"] = parent
            events = [create]
            for dep in depends:
                _task(state, dep)
                events.append({"op": "dep_add", "t": ref, "on": dep})
            created["ref"] = ref
            return events

        self._commit(build)
        return created["ref"]

    def set(self, ref: str, name: str, value) -> None:
        if name == "status":
            if value == "done":
                raise Refused(f"status done needs evidence: use `repact-task done {ref} "
                              "--evidence <target>`")
            if value not in PT.STATUSES:
                raise Refused(f"status must be one of {', '.join(s for s in PT.STATUSES if s != 'done')}")
        if name not in PT.SET_FIELDS:
            raise Usage(f"unknown field {name}")
        texts = value if isinstance(value, list) else [value]
        check_text(*[text for text in texts if isinstance(text, str)])

        def build(state):
            task = _task(state, ref)
            if name == "status" and task["status"] == "done":
                raise Refused(f"{ref} is done; reopen it first")
            if name == "status" and value == "dropped":
                children = open_descendants(state, ref)
                if children:
                    raise Refused(f"{ref} has open children: {', '.join(children)}; settle them first, "
                                  f"or `repact-task drop {ref} --with-children`")
            recorded = value if name == "status" else _field_value(name, value)
            if name == "parent" and recorded is not None:
                check_parent(state, recorded)
                chain = [recorded] + PT.ancestors(state, recorded)
                if ref in chain:
                    cycle = [ref] + chain[: chain.index(ref) + 1]
                    raise Refused("parent cycle: " + " -> ".join(cycle))
            return [{"op": "set", "t": ref, "field": name, "value": recorded}]

        self._commit(build)

    def dep(self, ref: str, on: str, remove: bool = False) -> None:
        def build(state):
            _task(state, ref)
            _task(state, on)
            if remove:
                return [{"op": "dep_del", "t": ref, "on": on}] if on in state.tasks[ref]["depends_on"] else []
            if on == ref:
                raise Refused(f"cycle: {ref} -> {ref}")
            back = _dependency_path(state, on, ref)
            if back:
                raise Refused("cycle: " + " -> ".join([ref] + back))
            return [] if on in state.tasks[ref]["depends_on"] else [{"op": "dep_add", "t": ref, "on": on}]

        self._commit(build)

    def link(self, ref: str, kind: str, target: str, remove: bool = False) -> None:
        if kind not in PT.LINK_KINDS:
            raise Usage(f"link kind must be one of {', '.join(PT.LINK_KINDS)}")
        check_text(target)
        if not remove:
            check_link(self.main, target)

        def build(state):
            task = _task(state, ref)
            present = {"kind": kind, "target": target} in task["links"]
            if remove:
                return [{"op": "link_del", "t": ref, "kind": kind, "target": target}] if present else []
            return [] if present else [{"op": "link_add", "t": ref, "kind": kind, "target": target}]

        self._commit(build)

    def start(self, ref: str, ptask: Optional[str] = None) -> None:
        target = f"ptask:{ptask}" if ptask else None
        if target:
            check_link(self.main, target)

        def build(state):
            task = _task(state, ref)
            if task["status"] in PT.SETTLED:
                raise Refused(f"{ref} is {task['status']}; reopen it first")
            events = [] if task["status"] == "active" else \
                [{"op": "set", "t": ref, "field": "status", "value": "active"}]
            link = {"kind": "reference", "target": target}
            if target and link not in task["links"]:
                events.append({"op": "link_add", "t": ref, **link})
            return events

        self._commit(build)

    def _evidence_items(self, targets: List[str]) -> List[dict]:
        """D26 and the kind rules, then verification, BEFORE anything is recorded. A verified
        run carries its log's digest and counts (evidence.item), so it outlives the log."""
        check_text(*targets)
        for target in targets:
            check_target(self.main, target, evidence=True)
        return [_evidence.item(self.main, target) for target in targets]

    @staticmethod
    def _remainder(state, ref: str, title: str) -> List[dict]:
        """The events that carry what is left of `ref` into a new planned task: same priority,
        tags and owner, a note naming the source, and a `task:<ref>` link back (the split shape:
        a partly done item closes on its evidence and its remainder is a task of its own)."""
        task = state.tasks[ref]
        new = "T-" + secrets.token_hex(4)
        while new in state.tasks:
            new = "T-" + secrets.token_hex(4)
        create = {"op": "create", "t": new, "title": _title(title), "status": "planned",
                  "priority": task["priority"], "note": f"remainder of {ref}"}
        if task["tags"]:
            create["tags"] = list(task["tags"])
        if task["owner"]:
            create["owner"] = task["owner"]
        return [create, {"op": "link_add", "t": new, "kind": "reference", "target": f"task:{ref}"}]

    def done(self, ref: str, evidence: Iterable[str], remaining: Optional[str] = None,
             waiver: Optional[dict] = None) -> Optional[str]:
        """Close `ref` on verified evidence. With `remaining`, a title, the part not done becomes
        a new planned task in the same locked append; its ref is returned. A `waiver`
        ({why, by, at}, the close gate's attended waiver) is kept on the done row."""
        targets = list(evidence)
        if not targets:
            raise Refused(f"done needs evidence: `repact-task done {ref} --evidence commit:<sha>`")
        if remaining is not None:
            check_text(remaining)
            _title(remaining)
        results = self._evidence_items(targets)
        reasons = {target: _evidence.verify(self.main, target)[1]
                   for target in targets if not any(r["target"] == target and r["verified"] for r in results)}
        created = {}

        def build(state):
            task = _task(state, ref)
            if task["status"] == "done":
                raise Refused(f"{ref} is already done")
            children = sorted(child for child, row in state.tasks.items()
                              if row["parent"] == ref and row["status"] not in PT.SETTLED)
            if children:
                raise Refused(f"{ref} has open children: {', '.join(children)}")
            if not any(item["verified"] for item in results):
                raise Refused("no verified evidence; " + "; ".join(
                    f"unverified: {item['target']} ({reasons[item['target']]})" for item in results))
            events = [{"op": "done", "t": ref, "evidence": results, **({"waiver": waiver} if waiver else {})}]
            if remaining is not None:
                twin = open_twin(state, remaining)
                if twin:
                    raise Refused(f"{twin} is open with the same title as the remainder; nothing written")
                events += self._remainder(state, ref, remaining)
                created["ref"] = events[1]["t"]
            return events

        self._commit(build)
        return created.get("ref")

    def reopen(self, ref: str, note: Optional[str] = None) -> None:
        check_text(note or "")

        def build(state):
            task = _task(state, ref)
            if task["status"] not in PT.SETTLED:
                raise Refused(f"{ref} is {task['status']}, not done or dropped")
            event = {"op": "reopen", "t": ref}
            if note:
                event["note"] = _field_value("note", note)
            return [event]

        self._commit(build)

    def drop(self, ref: str, with_children: bool = False) -> List[str]:
        """Drop `ref`; a parent with open descendants is refused unless `with_children`, which
        drops them in the same write. Returns the descendants dropped."""
        if not with_children:
            self.set(ref, "status", "dropped")
            return []
        dropped: List[str] = []

        def build(state):
            task = _task(state, ref)
            if task["status"] == "done":
                raise Refused(f"{ref} is done; reopen it first")
            dropped[:] = open_descendants(state, ref)
            return [{"op": "set", "t": t, "field": "status", "value": "dropped"}
                    for t in dropped + ([] if task["status"] == "dropped" else [ref])]

        self._commit(build)
        return dropped

    # -- the acceptance bridge's writers (P2 calls these; they live with the store) -----------------

    @staticmethod
    def _check_ptask(ptask: str) -> None:
        if not isinstance(ptask, str) or not PTASK_RE.match(ptask):
            raise Refused("a ptask is <team>/<task id>")

    def accept(self, ref: str, ptask: str, rev: int, evidence: Iterable[str],
               waiver: Optional[dict] = None) -> bool:
        """Record the lead's acceptance of the platform task `ptask` at revision `rev`. Every
        target first passes the link rules (D26 and the kind), then git verification; the accept
        is verified when any item verifies, which closes the task. An attended `waiver`
        ({why, by, at}, metadata.evidence_waiver) closes it too and stays on the row.
        Idempotent on (ptask, rev): returns False, writing nothing, when that accept is already
        recorded."""
        self._check_ptask(ptask)
        items = self._evidence_items(list(evidence))
        verified = any(item["verified"] for item in items)
        closes = verified or bool(waiver)

        def build(state):
            task = _task(state, ref)
            if any(row["ptask"] == ptask and row["rev"] == rev for row in task["accepts"]):
                return []
            if closes:
                children = sorted(child for child, row in state.tasks.items()
                                  if row["parent"] == ref and row["status"] not in PT.SETTLED)
                if children:
                    raise Refused(f"{ref} has open children: {', '.join(children)}")
            return [{"op": "accept", "t": ref, "ptask": ptask, "rev": rev, "verified": verified,
                     "evidence": items, **({"waiver": waiver} if waiver else {})}]

        return bool(self._commit(build))

    def park(self, ref: str, why: str) -> bool:
        """Park an open `ref` for a human: status blocked, the `parked` tag and
        a note that names why, in one locked append. Returns False, writing nothing, for a
        settled row or one already parked with the same note."""
        note = _field_value("note", (PARKED_NOTE + " ".join(str(why).split()))[:PT.NOTE_MAX])
        check_text(note)

        def build(state):
            task = _task(state, ref)
            if task["status"] in PT.SETTLED or (task["status"] == "blocked" and task["note"] == note
                                                 and PARKED_TAG in task["tags"]):
                return []
            tags = task["tags"] if PARKED_TAG in task["tags"] else [*task["tags"], PARKED_TAG]
            return [{"op": "set", "t": ref, "field": "status", "value": "blocked"},
                    {"op": "set", "t": ref, "field": "tags", "value": tags},
                    {"op": "set", "t": ref, "field": "note", "value": note}]

        return bool(self._commit(build))

    def reject(self, ref: str, ptask: str, rev: int) -> bool:
        """Record a rejection of `ptask` at revision `rev`; the status is unchanged. Idempotent on
        (ptask, rev): returns False, writing nothing, when it is already recorded."""
        self._check_ptask(ptask)

        def build(state):
            task = _task(state, ref)
            if any(row["ptask"] == ptask and row["rev"] == rev for row in task["rejects"]):
                return []
            return [{"op": "reject", "t": ref, "ptask": ptask, "rev": rev}]

        return bool(self._commit(build))


# ---------------------------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------------------------

def _ensure_lines(path: Path, lines: Iterable[str]) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    have = set(existing.splitlines())
    missing = [line for line in lines if line not in have]
    if not missing:
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(prefix + "\n".join(missing) + "\n")


def init(start, by: Optional[str] = None) -> Store:
    """Create `.repacted/tasks.jsonl`, the union-merge attribute and the ignore lines (idempotent)."""
    main = PT.find_main_checkout(start)
    if main is None:
        raise NoLog("not inside a git checkout with a working tree; nothing written")
    store = Store(main, by)
    store.log.parent.mkdir(parents=True, exist_ok=True)
    if not store.log.exists():
        store.log.touch()
    _ensure_lines(main / ".gitattributes", [UNION_LINE])
    _ensure_lines(main / ".gitignore", IGNORE_LINES)
    return store
