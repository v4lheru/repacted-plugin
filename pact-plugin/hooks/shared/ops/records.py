"""
Location: pact-plugin/hooks/shared/ops/records.py
Summary: The write discipline every operation shares: where an op
runs (Context), its id, the intent log `<session dir>/ops.jsonl`, the one task write and the
side writes that run once per op id.
Used by: shared/ops/{dispatch,submit,accept,reject,amend}.py; bin/_repact_ops.py (resolve).

Order, for every op:
  1. intent  {op, op_id, task, args_digest, at} appended to ops.jsonl;
  2. ONE TaskStore.update of the task file, with metadata.last_op = op_id in the same write;
     skipped when the task already carries last_op == op_id (a rerun after a crash);
  3. side writes, each once: a {op_id, side} row is appended after it lands;
  4. {op_id, done: true}.
op_id hashes the op name, the task id and the input digest, so the same call gets the same
id. A crash after step 2 leaves no half state: the rerun skips the write and finishes 3-4.
A crash between a side write and its row repeats that one side write (each is harmless twice:
the journal row is the only one that shows, and it carries the op_id).

Own session: the CLI resolves with bind=True. Inside a Claude Code session (a
session id in the environment) the op runs on that session alone: a --session-dir, --session-id
or --team naming anything else is refused (ops.not_own_session). With no session id (the owner's
own terminal) an explicit --session-dir must be a real session dir under <config>/pact-sessions/
whose context names the --team, so a made-up dir cannot stand in for the session's unattended
state. In-process callers (the SubagentStop backstop, tests) resolve unbound.

Usage:
    ctx = resolve(team=None, session_dir=None, session_id=None, caller=frame.from_cli(None))
    op_id = execute(ctx, "reject", "12", digest, mutate, [("journal", write_row)])
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from shared.owner_terminal import host_session_id
from shared.paths import get_claude_config_dir, is_safe_path_component
from shared.task_store import TaskRef, TaskStore, is_safe_task_id

from . import OpError
from .frame import Caller

OPS_LOG = "ops.jsonl"
CONTEXT_FILE = "pact-session-context.json"


@dataclass
class Context:
    """Where an op runs: the task store and team, the session dir, and who is calling."""

    store: TaskStore
    team: str
    session_dir: Path
    session_id: str
    caller: Caller

    def ref(self, task_id: str) -> TaskRef:
        if not is_safe_task_id(task_id):
            raise OpError("ops.invalid_input", [f"task id {task_id!r} is not a task id"])
        return TaskRef(self.team, task_id)

    def task(self, task_id: str) -> dict:
        """The task, or OpError when it does not exist."""
        task = self.store.get(self.ref(task_id))
        if task is None:
            raise OpError("ops.not_dispatched", [f"no task #{task_id} in team {self.team}"])
        return task


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(value) -> str:
    """sha256 of the canonical JSON of `value` (sorted keys), first 16 hex."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def op_id_of(op: str, task_id: str, args_digest: str) -> str:
    return "op-" + hashlib.sha256(f"{op}\0{task_id}\0{args_digest}".encode()).hexdigest()[:16]


def metadata(task: Optional[dict]) -> dict:
    value = (task or {}).get("metadata")
    return value if isinstance(value, dict) else {}


def dispatched(task: dict) -> dict:
    """metadata.dispatch, or OpError for a task no `repact dispatch` made."""
    value = metadata(task).get("dispatch")
    if not isinstance(value, dict):
        raise OpError("ops.not_dispatched", [f"task #{task.get('id')} has no metadata.dispatch; "
                                             "only a task made by `repact dispatch` takes this command"])
    return value


# -- where the op runs ---------------------------------------------------------------------

def _session_context(session_id: str) -> Optional[Path]:
    if not session_id or not is_safe_path_component(session_id):
        return None
    for path in sorted((get_claude_config_dir() / "pact-sessions").glob(f"*/{session_id}/{CONTEXT_FILE}")):
        return path
    return None


def host_session_dir() -> Optional[Path]:
    """The calling Claude Code session's dir (its id from the environment, found under
    <config>/pact-sessions/), or None: no session id, or no dir for it."""
    context = _session_context(host_session_id())
    return context.parent if context else None


def _context_team(folder: Path) -> Optional[str]:
    try:
        team = json.loads((folder / CONTEXT_FILE).read_text(encoding="utf-8")).get("team_name")
    except (OSError, ValueError, AttributeError):
        return None
    return team if isinstance(team, str) else None


def _not_own(text: str) -> OpError:
    return OpError("ops.not_own_session", [text])


def _bind(team: Optional[str], session_dir: Optional[str], session_id: Optional[str]) -> None:
    """Refuse a session dir, session id or team that is not the caller's own."""
    host = host_session_id()
    if host:
        if session_id and session_id.strip() != host:
            raise _not_own(f"--session-id {session_id.strip()[:60]!r} is not this session's id")
        own = host_session_dir()
        if own is None:
            raise _not_own(f"this session ({host[:60]}) has no session dir under "
                           f"{get_claude_config_dir() / 'pact-sessions'}")
        if session_dir and os.path.realpath(os.path.expanduser(session_dir)) != os.path.realpath(own):
            raise _not_own(f"--session-dir {session_dir[:120]} is not this session's dir ({own})")
        if team and team != _context_team(own):
            raise _not_own(f"--team {team[:60]!r} is not this session's team ({_context_team(own)!r})")
        return
    if not session_dir:
        return
    folder = os.path.realpath(os.path.expanduser(session_dir))
    sessions = os.path.realpath(get_claude_config_dir() / "pact-sessions")
    if os.path.commonpath([folder, sessions]) != sessions or folder == sessions \
            or not os.path.isfile(os.path.join(folder, CONTEXT_FILE)):
        raise _not_own(f"--session-dir {session_dir[:120]} is not a session dir under {sessions}")
    if team and team != _context_team(Path(folder)):
        raise _not_own(f"--team {team[:60]!r} is not the team of {session_dir[:120]}")


def resolve(team: Optional[str], session_dir: Optional[str], session_id: Optional[str],
            caller: Caller, store: Optional[TaskStore] = None, bind: bool = False) -> Context:
    """The op's Context from explicit values first, then the session id (argument or env) and
    its pact-session-context.json. OpError when no session dir or team can be found; with
    `bind` (the CLI), ops.not_own_session for values that are not the caller's own (_bind)."""
    if bind:
        _bind(team, session_dir, session_id)
    session_id = (session_id or host_session_id()).strip()
    context = _session_context(session_id)
    folder = Path(session_dir).expanduser() if session_dir else (context.parent if context else None)
    if folder is None or not folder.is_dir():
        raise OpError("ops.no_session", ["no session dir: pass --session-dir, or run inside a "
                                         "rePACTed session (CLAUDE_CODE_SESSION_ID)"])
    if not team:
        team = _context_team(folder)
    if not is_safe_path_component(team):
        raise OpError("ops.no_session", ["no team: pass --team, or run inside a rePACTed session"])
    return Context(store or TaskStore(), team, folder.resolve(), session_id, caller)


def unattended(ctx: Context) -> bool:
    """Whether the evidence controls are on for this op: the op's session, or the caller's own
    host session when that is another dir. Sticky once on (shared/unattended)."""
    from shared.unattended import controls_on
    if controls_on(str(ctx.session_dir)).on:
        return True
    own = host_session_dir()
    return own is not None and own.resolve() != ctx.session_dir and controls_on(str(own)).on


# -- the intent log -------------------------------------------------------------------------

def _append(ctx: Context, row: dict) -> None:
    line = json.dumps(dict(row, at=now()), sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with open(ctx.session_dir / OPS_LOG, "a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError as error:
        raise OpError("ops.write_failed", [f"cannot append to {ctx.session_dir / OPS_LOG}: {error}"], 1)


def rows(ctx: Context) -> list:
    return read_log(ctx.session_dir)


def read_log(session_dir) -> list:
    """Every JSON object row of `<session_dir>/ops.jsonl` ([] when it cannot be read). The
    session dir is a protected place (shell/settings.py TASK_STATE), so its rows are the ops'
    own record: the close gate and the rejection count read them."""
    try:
        text = (Path(session_dir) / OPS_LOG).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return []
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def unfinished(ctx: Context) -> list:
    """Intent rows with no done row: ops a crash interrupted (rerun the same command)."""
    log = rows(ctx)
    finished = {row.get("op_id") for row in log if row.get("done") is True}
    return [row for row in log if "op" in row and row.get("op_id") not in finished]


# -- the one task write and the side writes --------------------------------------------------

def write_task(ctx: Context, task_id: str, op_id: str, mutate: Callable[[dict], None]) -> bool:
    """Step 2: one locked read-modify-write. `mutate(task)` changes the task in place (or raises
    OpError, and nothing is written); last_op lands in the same write. False when the task
    already carries this op id (the write landed before a crash)."""
    skipped = []

    def apply(task: dict) -> bool:
        meta = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        if meta.get("last_op") == op_id:
            skipped.append(True)
            return False
        mutate(task)
        task.setdefault("metadata", meta)
        task["metadata"]["last_op"] = op_id
        return True

    if ctx.store.update(ctx.ref(task_id), apply):
        return True
    if skipped:
        return False
    raise OpError("ops.write_failed", [f"the write to task #{task_id} did not land (lock, symlink "
                                       "or an unreadable task file); nothing changed, rerun"], 1)


def run_sides(ctx: Context, op_id: str, sides: list) -> None:
    """Step 3: each (name, fn) once per op id. A side write that raises is reported, not fatal:
    the task write already stands, and the rerun tries the side write again."""
    done = {row.get("side") for row in rows(ctx) if row.get("op_id") == op_id and "side" in row}
    for name, fn in sides:
        if name in done:
            continue
        fn()
        _append(ctx, {"op_id": op_id, "side": name})


def execute(ctx: Context, op: str, task_id: str, args_digest: str,
            mutate: Callable[[dict], None], sides: list) -> tuple:
    """Steps 1-4 for an op on an existing task. Returns (op_id, wrote)."""
    op_id = op_id_of(op, task_id, args_digest)
    _append(ctx, {"op": op, "op_id": op_id, "task": task_id, "args_digest": args_digest})
    wrote = write_task(ctx, task_id, op_id, mutate)
    run_sides(ctx, op_id, sides)
    _append(ctx, {"op_id": op_id, "done": True})
    return op_id, wrote


def begin(ctx: Context, op: str, task_id: str, args_digest: str) -> str:
    """Step 1 alone, for dispatch (its step 2 is a create)."""
    op_id = op_id_of(op, task_id, args_digest)
    _append(ctx, {"op": op, "op_id": op_id, "task": task_id, "args_digest": args_digest})
    return op_id


def finish(ctx: Context, op_id: str, sides: list) -> None:
    run_sides(ctx, op_id, sides)
    _append(ctx, {"op_id": op_id, "done": True})
