"""
Location: pact-plugin/bin/_front/inbox.py
Summary: `repact decide` (formerly bin/repact-inbox): every question
parked on disk for the user, in one numbered list. It reads four sources, each with the reader its
own command uses, and writes nothing to them:

  task log  the ASK flags of `.repacted/tasks.jsonl` (reference/work/task-log.md step 3): drift flags from
            hooks/shared/project_tasks.py, open tasks whose platform task is completed (the
            acceptance bridge), tasks proposed from HANDOFFs that wait on the user's say, and rows
            parked for a human (the `parked` tag: an unattended waiver);
  parked    platform tasks of this project's sessions that carry metadata.parked (an unattended
            `repact accept --waive`, or the rejection limit) and are not completed, unless a
            parked task-log row already asks about them;
  halts     unattended halts (bin/repact-unattended) of this project's sessions whose mode is
            still ON (a halt resumes when the owner turns the mode off; reference/unattended-runs.md),
            unless `repact-autopilot record <ref> halted` parked the task-log row, which asks it;
  kb        held distill items of the KB queue, which only the user decides at their own
            terminal (reference/wrap-up/kb-queue.md section 1).

  repact decide [--json] [--count] [--session-dir DIR]
  repact decide <n | T-ref> set|done|drop|dep|reopen [args]

`decide <n|T-ref> <verb>` answers a task-log row by running bin/repact-task with the row's ref (its
validation, lock and append; no storage of its own). A number names the item the last listing
showed, not whatever is nth now (the stable-ref rule): each listing saves a snapshot,
{n: source, ref, digest of the question}, to the per-user state dir keyed by the project
(${XDG_STATE_HOME:-~/.local/state}/repacted/decide/), never to a tracked file. A number is refused
when there is no snapshot, it is over SNAPSHOT_MAX_AGE old, or the item it names is gone or its
question changed: list again. A halt or a KB item is refused with the command the user runs
instead: the KB terminal rule is never weakened here. Question text is scrubbed of credential
values; a model-written KB lesson is never printed, only its id and hold reasons. An unreadable
source is one `note:` line, never a crash. reference/work/task-log.md and the wrap-up report surface the
count. Telegram delivery is out of scope.
Used by: bin/_repact_ops.py (`repact decide`), bin/repact-autopilot (task_rows, halt_rows),
bin/_front/status.py (parked_rows).

Exit codes: 0 ok; 2 usage or a refused number; 3 refused (a halt, a parked task or a KB item);
decide passes repact-task's own code.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import BIN_DIR  # noqa: E402  (sets the import path)

from shared import session_journal, unattended  # noqa: E402
from shared.paths import get_claude_config_dir  # noqa: E402
from shared.pact_context import project_slug  # noqa: E402
from shared.scrub import scrub_text  # noqa: E402

TASK = BIN_DIR / "repact-task"
UNATTENDED = BIN_DIR / "repact-unattended"
VERBS = ("set", "done", "drop", "dep", "reopen")
MAX_TEXT = 200
EMPTY = "inbox: empty, no question waits"
LABEL = {"task-log": "task log", "parked": "parked", "halt": "halt", "kb": "kb"}
PARKED_ANSWER = ("a human reviews it, then with unattended mode off: repact accept {id} --waive \"<why>\" "
                 "closes it, or repact reject {id} --reason \"<why>\" sends it back")
# (flag pattern, the question put to the user, the recommended answer or None): task-log.md step 3.
ASK_FLAGS = (
    (re.compile(r"relation names unknown (T-[0-9a-f]+)"), "a relation names unknown {0}: what was meant?", None),
    (re.compile(r"stuck \(depends on a dropped task\)"),
     "it depends on a dropped task: remove the dependency, or drop this task too?",
     "remove it: repact decide {n} dep --on {dropped} --remove"),
    (re.compile(r"active together with exclusive (T-[0-9a-f]+)"),
     "it is active together with exclusive {0}: pause one?",
     "pause one: repact decide {n} set --status planned"),
    (re.compile(r"active and untouched"), "active and untouched for over 14 days: is it still live?",
     "confirm it: repact decide {n} set --status active"),
    (re.compile(r"planned, unranked and untouched"),
     "planned, unranked and untouched for over 14 days: rank it, or is it still intended?",
     "rank it: repact decide {n} set --rank <n>"),
    (re.compile(r"accepted without evidence"), "accepted without evidence: which commit, tag or run closes it?",
     "close it: repact decide {n} done --evidence commit:<sha>"),
    (re.compile(r"concurrent edit: (\w+) (.+)"), "two values were written for {0} ({1}): which one stands?",
     "pick one: repact decide {n} set --{0} <value>"),
)


def one_line(text) -> str:
    """Printable, single-line, credential values scrubbed, at most MAX_TEXT characters."""
    flat = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return scrub_text(flat)[:MAX_TEXT]


def row(source: str, task: str, question: str, advice: Optional[str], waits: List[str],
        answer: str) -> dict:
    return {"source": source, "task": task, "question": one_line(question),
            "recommended": one_line(advice) if advice else None, "waits": waits, "answer": answer}


# --- the task log ------------------------------------------------------------------------------

def task_rows(start: Path) -> Tuple[List[dict], List[str], Optional[Path]]:
    """(rows, notes, the main checkout). No task log is no source; any other failure is a note."""
    from shared.task_log import write as W
    PT = W.PT
    try:
        store = W.Store.locate(start)
        state = store.state()
    except W.NoLog:
        return [], [], None
    except Exception as error:  # noqa: BLE001 - one source failing never stops the others
        return [], [f"note: task log not read ({type(error).__name__}: {one_line(error)})"], None
    rows = []

    def add(ref: str, question: str, advice: Optional[str]) -> None:
        if ref not in state.tasks:
            return
        waits = sorted(other for other, item in state.tasks.items()
                       if ref in item["depends_on"] and item["status"] not in PT.SETTLED)
        title = PT.one_line(state.tasks[ref]["title"], 60)
        rows.append(row("task-log", ref, f'"{title}": {question}', advice, waits, ""))

    for flag in PT.drift_flags(state):
        ref, _, text = flag.partition(": ")
        for pattern, question, advice in ASK_FLAGS:
            found = pattern.search(text) if ref.startswith("T-") else None
            if found:
                dropped = next((dep for dep in state.tasks[ref]["depends_on"]
                                if state.tasks.get(dep, {}).get("status") == "dropped"), "<ref>") \
                    if ref in state.tasks else "<ref>"
                add(ref, question.format(*found.groups()),
                    advice and advice.replace("{dropped}", dropped).replace("{0}", (found.groups() or ("",))[0]))
                break
    try:
        from shared.task_log import bridge
        linked = bridge.completion_view(start, state=state).get("linked", {})
    except Exception:  # noqa: BLE001 - platform task files are a hint, as in repact-task next
        linked = {}
    for ref in sorted(linked):
        add(ref, "open, but its platform task is completed: close it, or drop it?",
            "close it: repact decide {n} done --evidence <target>, or decide {n} drop")
    for ref, item in sorted(state.tasks.items()):
        if item["owner"] == "user" and "proposed" in item["tags"] and item["status"] not in PT.SETTLED:
            add(ref, "proposed from an accepted HANDOFF: take it, or drop it?",
                "take it: repact decide {n} set --owner <name>, or decide {n} drop")
        if W.PARKED_TAG in item["tags"] and item["status"] == "blocked":
            question = (item["note"] or "").replace(W.PARKED_NOTE, "", 1) or "parked for a human: decide it"
            add(ref, question, "after a review: repact decide {n} set --status active to retry, "
                               "or decide {n} drop")
    return rows, [], store.main


def _parked_items(start: Path) -> dict:
    """The task log's rows parked for a human, by ref ({} with no log)."""
    from shared.task_log import write as W
    try:
        state = W.Store.locate(start).state()
    except Exception:  # noqa: BLE001 - no log, no parked rows
        return {}
    return {ref: item for ref, item in state.tasks.items()
            if W.PARKED_TAG in item["tags"] and item["status"] == "blocked"}


def parked_links(start: Path) -> set:
    """The ptask links ("<team>/<id>") of the task log's parked rows: their question is asked there."""
    return {link["target"][len("ptask:"):] for item in _parked_items(start).values()
            for link in item["links"] if str(link.get("target", "")).startswith("ptask:")}


def parked_rows(explicit: Optional[str], main: Optional[Path], start: Path) -> Tuple[List[dict], List[str]]:
    """One row per parked platform task (metadata.parked, not completed) of this project's sessions."""
    try:
        from shared import task_files
        from shared.plugin_paths import task_store
        from shared.task_log import bridge
        folders = [Path(explicit).expanduser()] if explicit else (bridge._session_dirs(main) if main else [])
        asked = parked_links(main or start)
        rows = []
        for folder in folders:
            team = bridge._team_of(folder)
            source = task_store().team_dir(team) if team else None
            for path in sorted(source.glob("*.json")) if source is not None and source.is_dir() else []:
                task = task_files.load_task(path) or {}
                parked = (task.get("metadata") or {}).get("parked") if isinstance(task.get("metadata"), dict) else None
                task_id = str(task.get("id") or path.stem)
                if not isinstance(parked, dict) or task.get("status") == "completed" or f"{team}/{task_id}" in asked:
                    continue
                rows.append(row("parked", f"#{task_id}", parked.get("question") or parked.get("why") or "parked",
                                None, [f"task #{task_id} stays open"], PARKED_ANSWER.format(id=task_id)))
        return rows, []
    except Exception as error:  # noqa: BLE001 - one source failing never stops the others
        return [], [f"note: parked tasks not read ({type(error).__name__}: {one_line(error)})"]


# --- unattended halts ----------------------------------------------------------------------------

def session_dirs(explicit: Optional[str], start: Path) -> List[Path]:
    """--session-dir, else every session dir of this project (the slug of CLAUDE_PROJECT_DIR, or
    of the checkout) that holds an unattended flag."""
    if explicit:
        return [Path(explicit).expanduser()]
    project = os.environ.get("CLAUDE_PROJECT_DIR") or str(start)
    root = get_claude_config_dir() / "pact-sessions" / project_slug(project)
    if not root.is_dir():
        return []
    return sorted(child for child in root.iterdir()
                  if child.is_dir() and os.path.isfile(unattended.flag_path(str(child))))


def halt_rows(explicit: Optional[str], start: Path) -> Tuple[List[dict], List[str]]:
    rows, notes = [], []
    try:
        folders = session_dirs(explicit, start)
    except OSError as error:
        return [], [f"note: sessions not read ({type(error).__name__})"]
    for folder in folders:
        if not unattended.read_mode(str(folder), environ={}).on:
            continue                        # the owner turned the mode off: the halts are answered
        try:
            halts = session_journal.read_events_from(str(folder), "unattended_halt")
        except Exception as error:  # noqa: BLE001
            notes.append(f"note: halts of {folder.name} not read ({type(error).__name__})")
            continue
        for halt in halts:
            task = one_line(halt.get("task_id", "?"))
            rows.append(row("halt", task, halt.get("question", "?"), None, [f"task {task} stops"],
                            f"repact-unattended off --session-dir {folder}, then the lead asks again"))
    return rows, notes


# --- held KB items -----------------------------------------------------------------------------

def kb_rows() -> Tuple[List[dict], List[str]]:
    try:
        from _repact_common import vault_scope_root
        from shared.vault_scope import KBNotConfigured
        try:
            vault = vault_scope_root()
        except KBNotConfigured:
            return [], []
        from _kb import gate as G
        from _kb.decide import VAULT_NOTE, held_reasons
        items = G.pending(vault) if vault.is_dir() else []
    except Exception as error:  # noqa: BLE001
        return [], [f"note: KB queue not read ({type(error).__name__}: {one_line(error)})"]
    rows = []
    for item in items:
        reasons = held_reasons(item)
        if reasons:                         # the model-written lesson stays in the queue card
            rows.append(row("kb", str(item["id"]), f"held ({', '.join(reasons)}): accept, edit, reject or defer?",
                            None, ["the lesson waits to reach the KB"],
                            f'in your own terminal: python3 "{VAULT_NOTE}" queue, then drain --decide '
                            f"{item['id']} <decision> --by <you>"))
    return rows, []


# --- the list -----------------------------------------------------------------------------------

def collect(session_dir: Optional[str]) -> Tuple[List[dict], List[str], Optional[Path]]:
    start = Path.cwd()
    tasks, notes, main = task_rows(start)
    parked, parked_notes = parked_rows(session_dir, main, start)
    halts, halt_notes = halt_rows(session_dir, main or start)
    asked = set(_parked_items(main or start)) if halts else set()
    halts = [item for item in halts if item["task"] not in asked]   # `record halted` parked its row: asked there
    held, kb_notes = kb_rows()
    rows = tasks + parked + halts + held
    for n, item in enumerate(rows, 1):
        item["n"] = n
        if item["recommended"]:
            item["recommended"] = item["recommended"].replace("{n}", item["task"])
    return rows, notes + parked_notes + halt_notes + kb_notes, main


def head(rows: List[dict]) -> str:
    if not rows:
        return EMPTY
    counts = ", ".join(f"{LABEL[source]} {sum(r['source'] == source for r in rows)}"
                       for source in LABEL if any(r["source"] == source for r in rows))
    return f"inbox: {len(rows)} question(s) ({counts}): repact decide lists them"


def lines(rows: List[dict], notes: List[str]) -> List[str]:
    out = [head(rows)]
    for item in rows:
        asker = f"task {item['task']}" if item["source"] == "halt" else item["task"]
        out.append(f"  {item['n']}. [{LABEL[item['source']]}] {asker}: {item['question']}")
        if item["recommended"]:
            out.append(f"     recommended: {item['recommended']}")
        if item["waits"]:
            out.append(f"     waits: {', '.join(item['waits'])}")
        if item["answer"]:
            out.append(f"     answer: {item['answer']}")
    return out + notes


# --- the listing snapshot (the stable-ref rule) -----------------------------------------------

SNAPSHOT_MAX_AGE = 24 * 3600
SNAPSHOT_VERSION = 1


def _digest(item: dict) -> str:
    text = "\x1f".join((item["source"], item["task"], item["question"]))
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def snapshot_path(main: Optional[Path]) -> Optional[Path]:
    """The per-user snapshot file of this project, or None when no state dir can be made."""
    state = os.environ.get("XDG_STATE_HOME", "")
    if not os.path.isabs(state):
        state = str(Path.home() / ".local" / "state")
    project = os.path.realpath(str(main or os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()))
    folder = Path(state) / "repacted" / "decide"
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return None
    return folder / (hashlib.sha256(project.encode("utf-8", "replace")).hexdigest()[:16] + ".json")


def save_snapshot(rows: List[dict], main: Optional[Path], now: Optional[float] = None) -> None:
    path = snapshot_path(main)
    if path is None:
        return
    body = {"v": SNAPSHOT_VERSION, "at": time.time() if now is None else now,
            "items": [{"n": r["n"], "source": r["source"], "task": r["task"], "digest": _digest(r)}
                      for r in rows]}
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(body), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()


def listed(n: str, main: Optional[Path]) -> Tuple[Optional[dict], str]:
    """(the snapshot's item n, "") or (None, why the number cannot be used)."""
    path = snapshot_path(main)
    try:
        body = json.loads(path.read_text(encoding="utf-8")) if path else None
    except (OSError, ValueError):
        body = None
    if not isinstance(body, dict) or body.get("v") != SNAPSHOT_VERSION or not isinstance(body.get("items"), list):
        return None, "no listing to number from: run `repact decide` first"
    age = time.time() - body.get("at", 0) if isinstance(body.get("at"), (int, float)) else SNAPSHOT_MAX_AGE + 1
    if age > SNAPSHOT_MAX_AGE:
        return None, "the listing is over a day old: run `repact decide` to list again"
    found = [item for item in body["items"] if isinstance(item, dict) and str(item.get("n")) == n]
    if not found:
        return None, f"the last listing has no item {n}: run `repact decide` to list again"
    return found[0], ""


# --- decide -------------------------------------------------------------------------------------

def decide(target: str, answer: List[str], session_dir: Optional[str]) -> int:
    if not answer or answer[0] not in VERBS:
        print("repact decide: give a verb for the row: set, done, drop, dep or reopen, then its "
              "arguments (repact-task --help); e.g. repact decide T-1a2b3c4d set --rank 2", file=sys.stderr)
        return 2
    rows, _, main = collect(session_dir)
    if target.isdigit():
        wanted, why = listed(target, main)
        if wanted is None:
            print(f"repact decide: refused, {why}", file=sys.stderr)
            return 2
        chosen = [r for r in rows if r["source"] == wanted.get("source") and r["task"] == wanted.get("task")]
        if not chosen:
            print(f"repact decide: refused, item {target} ({one_line(wanted.get('task'))[:40]}) is no longer "
                  "open: run `repact decide` to list again", file=sys.stderr)
            return 2
        if _digest(chosen[0]) != wanted.get("digest"):
            print(f"repact decide: refused, item {target} ({chosen[0]['task']}) changed since the listing: "
                  "run `repact decide` to list again", file=sys.stderr)
            return 2
    else:
        chosen = [r for r in rows if r["task"] == target]
        if not chosen:
            print(f"repact decide: no open question for {one_line(target)[:40]}; run `repact decide` to list them",
                  file=sys.stderr)
            return 2
    item = chosen[0]
    if item["source"] != "task-log":
        print(f"repact decide: refused, {LABEL[item['source']]} {item['task']} is not answered here: "
              f"{item['answer']}", file=sys.stderr)
        return 3
    print(f"deciding {target}: {item['task']}", file=sys.stderr)
    return subprocess.run([sys.executable, str(TASK), "--repo", str(main), answer[0], item["task"],
                           *answer[1:]]).returncode


def show(session_dir: Optional[str], as_json: bool = False, count: bool = False) -> int:
    """Print the numbered list (saving the snapshot `decide <n>` reads), its JSON, or the head line."""
    rows, notes, main = collect(session_dir)
    if count:
        print(head(rows))
        return 0
    save_snapshot(rows, main)
    if as_json:
        print(json.dumps({"items": rows, "notes": notes}, indent=1))
    else:
        print("\n".join(lines(rows, notes)))
    return 0
