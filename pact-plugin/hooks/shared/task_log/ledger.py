"""The project ledger: what accepted HANDOFFs said is limited, decided and left to do.

Location: pact-plugin/hooks/shared/task_log/ledger.py. Written by the acceptance bridge
(bridge.handle, on the lead's acceptance) and by `repact-task ledger --backfill`; read by
bin/_tasks/views.py, which renders docs/known-limits.md and docs/decision-log.md from it.

`.repacted/ledger.jsonl` sits beside the task log, under the same lock (`.repacted/tasks.lock`)
and the same opt-in: a repo without a task log gets nothing (Store.locate raises NoLog first).
One JSON object per line, appended, never rewritten, merged with git's union driver:
  {"v": 1, "eid", "ts", "by", "kind": "limit"|"decision"|"remaining", "ptask": "<team>/<id>",
   "rev": <int>, "key": <hex>, "text": <str>, "id": "DL<n>" (decision only),
   "t": "T-<hex>" (remaining only), "withheld": <credential kind> (only when withheld)}
  {"v": 1, "eid", "ts", "by", "kind": "retire", "target": <a limit's key>, "how": "manual"|"fixed",
   "text": <the reason, or the fixed: item>, "ptask", "rev" (fixed only)}
From a HANDOFF: every `uncertainty` item opening `known-limit:` is a limit; every `decisions` item
that is not only a test result (is_test_result) is a decision, numbered DL<n> in the ledger's own
sequence (never the user's D<n> rulings in docs/decisions.md); every
`open_questions` item opening `remaining:` is a remaining item AND a proposed task in the task
log, owned by `user` and tagged `proposed`, so it waits on the user's say instead of being planned
for an agent.

A limit is retired by an appended retire event, never by an edit: `repact-task ledger retire <id>
--reason <text>` (retire), or on its own when a later accepted HANDOFF of the same project task
(a ref bound to both platform tasks) or of the same platform task carries a `fixed:` item whose
normalised text (normalise) equals the limit's, or when any later accepted `fixed:` item names the
limit's printed id ("fixed: id 1a2b3c4d ..."). Retired limits leave docs/known-limits.md.

The text is model-written. It is flattened to one line and capped at TEXT_MAX_BYTES; an item that
carries a credential value is withheld whole and only its kind is kept (withhold, never
mask), through the same scrub.credential_reason every task-log write calls. `key` hashes the
kind, the platform task and the flattened text, so a re-accept or a second backfill writes
nothing twice.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

HOOKS_DIR = Path(__file__).resolve().parents[2]
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from shared import scrub  # noqa: E402
from shared.task_log import write as W  # noqa: E402

PT = W.PT
LEDGER_RELPATH = Path(".repacted") / "ledger.jsonl"
UNION_LINE = ".repacted/ledger.jsonl merge=union"
SCHEMA_VERSION = 1
KINDS = ("limit", "decision", "remaining")
TEXT_MAX_BYTES = 2048               # one line stays well under write.MAX_LINE_BYTES
PROPOSED_TAG = "proposed"
PROPOSED_OWNER = "user"
DECISION_PREFIX = "DL"
DECISION_ID_RE = re.compile(r"^DL([1-9][0-9]{0,8})$")
LIMIT_RE = re.compile(r"^\s*known-limit\s*:\s*", re.IGNORECASE)
REMAINING_RE = re.compile(r"^\s*remaining\s*:\s*", re.IGNORECASE)
FIXED_RE = re.compile(r"^\s*fixed\s*:\s*", re.IGNORECASE)
RETIRE = "retire"
RETIRE_HOW = ("manual", "fixed")
SHORT_ID_LEN = 8                    # the id docs/known-limits.md prints: the key's first 8 hex
MIN_ID_LEN = 6
FIXED_ID_RE = re.compile(r"\bid ([0-9a-f]{%d})\b" % SHORT_ID_LEN)     # on normalised text
# A decision that is only a test result: a pytest-style pass/fail count ("10 passed", "1 failed"),
# a tcount run stamp ("2026-10-18T104819Z-66084") or nothing but a .log path. A count of skipped
# tests alone is not enough: review findings cite those ("1 skipped") as part of a real point.
TEST_COUNT_RE = re.compile(r"\b\d[\d,]*\s+(?:passed|failed)\b", re.IGNORECASE)
RUN_STAMP_RE = re.compile(r"\b\d{4}-\d\d-\d\dT\d{6}Z-\d+\b")
LOG_PATH_RE = re.compile(r"^\S+\.log$", re.IGNORECASE)
KEY_RE = re.compile(r"^[0-9a-f]{24}$")
# C0/C1 controls (a terminal escape in a page someone cats) and the bidi overrides (text that
# reads differently from what it says); whitespace is flattened before this runs
CONTROL_RE = re.compile("[\x00-\x1f\x7f-\x9f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def ledger_path(main: Path) -> Path:
    return Path(main) / LEDGER_RELPATH


# ---------------------------------------------------------------------------------------------
# from a HANDOFF to items
# ---------------------------------------------------------------------------------------------

def _strings(value) -> List[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def is_test_result(text: str) -> bool:
    """True when a decisions item only reports a run: it carries a pass/fail count or a tcount run
    stamp, or it is a bare .log path. Such items stay out of the decision log."""
    flat = flatten(text)
    return bool(TEST_COUNT_RE.search(flat) or RUN_STAMP_RE.search(flat) or LOG_PATH_RE.match(flat))


def normalise(text: str) -> str:
    """The text a `fixed:` item is matched on: one line, case-folded, every run of punctuation
    and whitespace one space."""
    return " ".join(re.sub(r"[\W_]+", " ", flatten(text).casefold()).split())


def items(handoff) -> List[Tuple[str, str]]:
    """[(kind, raw text)] in HANDOFF order: limits, then decisions, then remaining items. An item
    whose text is empty after its prefix is dropped; a non-string item is ignored; a decision
    that is only a test result is dropped."""
    if not isinstance(handoff, dict):
        return []
    found = []
    for item in _strings(handoff.get("uncertainty")):
        if LIMIT_RE.match(item):
            found.append(("limit", LIMIT_RE.sub("", item, count=1)))
    found += [("decision", item) for item in _strings(handoff.get("decisions"))
              if not is_test_result(item)]
    for item in _strings(handoff.get("open_questions")):
        if REMAINING_RE.match(item):
            found.append(("remaining", REMAINING_RE.sub("", item, count=1)))
    return [(kind, text) for kind, text in found if text.strip()]


def fixed_items(handoff) -> List[str]:
    """The normalised text of each `uncertainty` item opening `fixed:` (empty ones dropped)."""
    if not isinstance(handoff, dict):
        return []
    found = [normalise(FIXED_RE.sub("", item, count=1))
             for item in _strings(handoff.get("uncertainty")) if FIXED_RE.match(item)]
    return [text for text in found if text]


def item_key(kind: str, ptask: str, text: str) -> str:
    flat = " ".join(text.split())
    return hashlib.sha256(f"{kind}\0{ptask}\0{flat}".encode("utf-8")).hexdigest()[:24]


def _cap(text: str, limit: int = TEXT_MAX_BYTES) -> str:
    """Cut to `limit` UTF-8 bytes on a character boundary, marking the cut with an ellipsis."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[: limit - len("…".encode("utf-8"))].decode("utf-8", "ignore") + "…"


def flatten(text) -> str:
    """One line with no control or bidi-override characters."""
    return " ".join(CONTROL_RE.sub("", " ".join(str(text).split())).split())


def clean_text(text: str) -> Tuple[str, Optional[str]]:
    """(text to store, withheld credential kind or None): one line, capped, a credential withheld whole."""
    flat = flatten(text)
    kind = scrub.credential_reason(W.TEXT_PATH, flat)
    if kind:
        return f"(withheld: this item carried a credential value ({kind}))", kind
    return _cap(flat), None


# ---------------------------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------------------------

def _retire_problem(entry: dict) -> Optional[str]:
    if not isinstance(entry.get("target"), str) or not KEY_RE.match(entry["target"]):
        return "bad target"
    if entry.get("how") not in RETIRE_HOW or not isinstance(entry.get("text"), str):
        return "bad retire"
    if entry["how"] == "fixed" and not (isinstance(entry.get("ptask"), str)
                                        and W.PTASK_RE.match(entry["ptask"])):
        return "bad ptask"
    return None


def entry_problem(entry) -> Optional[str]:
    """Why a parsed line is not a ledger entry, or None."""
    if not isinstance(entry, dict):
        return "not an object"
    if entry.get("kind") == RETIRE:
        return _retire_problem(entry)
    if entry.get("kind") not in KINDS:
        return "unknown kind"
    if not isinstance(entry.get("text"), str):
        return "bad text"
    if not isinstance(entry.get("ptask"), str) or not W.PTASK_RE.match(entry["ptask"]):
        return "bad ptask"
    rev = entry.get("rev")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
        return "bad rev"
    if not isinstance(entry.get("key"), str) or not KEY_RE.match(entry["key"]):
        return "bad key"
    if entry["kind"] == "decision" and not DECISION_ID_RE.match(str(entry.get("id") or "")):
        return "bad decision id"
    if "t" in entry and not (isinstance(entry["t"], str) and PT.REF_RE.match(entry["t"])):
        return "bad task ref"
    return None


def read(main: Path) -> Tuple[List[dict], int]:
    """(entries in file order, count of lines that are not entries). An absent ledger is ([], 0)."""
    path = ledger_path(main)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], 0
    entries, corrupt = [], 0
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            entry = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            corrupt += 1
            continue
        if entry_problem(entry):
            corrupt += 1
            continue
        entries.append(entry)
    return entries, corrupt


def retired(entries: Iterable[dict]) -> Dict[str, dict]:
    """{limit key: the first retire event naming it}."""
    found: Dict[str, dict] = {}
    for entry in entries:
        if entry["kind"] == RETIRE:
            found.setdefault(entry["target"], entry)
    return found


def active_limits(entries: List[dict]) -> List[dict]:
    """The limits no retire event names, in file order."""
    gone = retired(entries)
    return [entry for entry in entries if entry["kind"] == "limit" and entry["key"] not in gone]


def short_id(entry: dict) -> str:
    return entry["key"][:SHORT_ID_LEN]


def project_refs(state, ptask: str) -> List[str]:
    """The project tasks bound to platform task `ptask` (their ptask: links), by ref."""
    team, _, task_id = ptask.rpartition("/")
    return [task["ref"] for task in PT.tasks_for_ptask(state, team, task_id)]


def _next_decision(entries: Iterable[dict]) -> int:
    numbers = [int(DECISION_ID_RE.match(entry["id"]).group(1)) for entry in entries
               if entry["kind"] == "decision"]
    return max(numbers, default=0) + 1


# ---------------------------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------------------------

def _proposed_title(text: str) -> str:
    return PT.one_line(text, PT.TITLE_MAX)


def _proposed_note(text: str, ptask: str, rev: int) -> str:
    return PT.one_line(f"Proposed from the HANDOFF of {ptask} (rev {rev}): {text}", PT.NOTE_MAX)


def _propose(store, remaining: List[dict], ptask: str, rev: int) -> None:
    """Give each remaining entry `t`: an open task with the same title, or a new proposed task
    owned by the user. The caller holds the lock."""
    def build(state):
        events, titles = [], {}
        for entry in remaining:
            title = _proposed_title(entry["text"])
            folded = " ".join(title.split()).casefold()
            ref = titles.get(folded) or W.open_twin(state, title)
            if ref:
                entry["t"] = ref
                continue
            ref = "T-" + secrets.token_hex(4)
            while ref in state.tasks or ref in titles.values():
                ref = "T-" + secrets.token_hex(4)
            note = _proposed_note(entry["text"], ptask, rev)
            W.check_text(title, note)
            events.append({"op": "create", "t": ref, "title": W._field_value("title", title),
                           "status": "planned", "priority": "P2", "owner": PROPOSED_OWNER,
                           "tags": [PROPOSED_TAG], "note": W._field_value("note", note)})
            titles[folded] = ref
            entry["t"] = ref
        return events

    store.append_locked(build)


def _append(path: Path, entries: List[dict]) -> None:
    raw = path.read_bytes() if path.exists() else b""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        if raw and not raw.endswith(b"\n"):
            os.write(fd, b"\n")
        for entry in entries:
            line = (json.dumps(entry, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
            if len(line) > W.MAX_LINE_BYTES:
                raise W.Refused(f"a ledger line is {len(line)} bytes; the limit is {W.MAX_LINE_BYTES}")
            os.write(fd, line)
    finally:
        os.close(fd)


def _stamped(store, entries: List[dict], latest: Optional[str]) -> List[dict]:
    out = []
    for entry in entries:
        ts = W._stamp(latest)
        latest = ts
        out.append({"v": SCHEMA_VERSION, "eid": W._eid(ts), "ts": ts, "by": store.by, **entry})
    return out


def _fixed_retires(store, entries: List[dict], ptask: str, rev: int, handoff) -> List[dict]:
    """Retire events for the active limits a `fixed:` item of this HANDOFF matches: the limit's
    printed id named as `id <8 hex>` (any task), or the same normalised text and the same
    platform task or a project task bound to both."""
    fixed = fixed_items(handoff)
    if not fixed:
        return []
    named = {match for text in fixed for match in FIXED_ID_RE.findall(text)}
    state = store.state()
    mine = set(project_refs(state, ptask))
    retires = []
    for limit in active_limits(entries):
        if short_id(limit) not in named:
            if normalise(limit["text"]) not in fixed or limit.get("withheld"):
                continue
            if limit["ptask"] != ptask and not (mine & set(project_refs(state, limit["ptask"]))):
                continue
        retires.append({"kind": RETIRE, "target": limit["key"], "how": "fixed", "ptask": ptask,
                        "rev": rev, "text": clean_text(f"fixed in {ptask} rev {rev}")[0]})
    return retires


def record(store, ptask: str, rev: int, handoff) -> Dict[str, List[str]]:
    """Append what `handoff` adds to the ledger for platform task `ptask` at revision `rev`, and
    retire the earlier limits its `fixed:` items match. Returns {"limits": [keys], "decisions":
    [DL ids], "proposed": [task refs], "retired": [limit keys]} for what was new; empty lists
    when everything was already there."""
    report: Dict[str, List[str]] = {"limits": [], "decisions": [], "proposed": [], "retired": []}
    found = items(handoff)
    if not (found or fixed_items(handoff)) or not W.PTASK_RE.match(ptask):
        return report
    path = ledger_path(store.main)
    with store._locked():
        entries, _ = read(store.main)
        seen = {entry["key"] for entry in entries if entry["kind"] != RETIRE}
        number = _next_decision(entries)
        latest = max((entry["ts"] for entry in entries if isinstance(entry.get("ts"), str)), default=None)
        new = []
        for kind, raw in found:
            key = item_key(kind, ptask, raw)
            if key in seen:
                continue
            seen.add(key)
            text, withheld = clean_text(raw)
            entry = {"kind": kind, "ptask": ptask, "rev": rev, "key": key, "text": text}
            if kind == "decision":
                entry["id"] = f"{DECISION_PREFIX}{number}"
                number += 1
            if withheld:
                entry["withheld"] = withheld
            new.append(entry)
        retires = _fixed_retires(store, entries, ptask, rev, handoff)
        if not (new or retires):
            return report
        remaining = [entry for entry in new if entry["kind"] == "remaining"]
        if remaining:
            _propose(store, remaining, ptask, rev)
        _append(path, _stamped(store, new + retires, latest))
    W._ensure_lines(store.main / ".gitattributes", [UNION_LINE])
    if remaining:
        store.refresh_cache()
    report["limits"] = [entry["key"] for entry in new if entry["kind"] == "limit"]
    report["decisions"] = [entry["id"] for entry in new if entry["kind"] == "decision"]
    report["proposed"] = list(dict.fromkeys(entry["t"] for entry in remaining))
    report["retired"] = [entry["target"] for entry in retires]
    return report


def resolve(entries: List[dict], ident: str) -> dict:
    """The active limit whose key starts with `ident` (at least MIN_ID_LEN hex). Usage on a bad,
    unknown, ambiguous or already retired id."""
    ident = ident.strip().lower() if isinstance(ident, str) else ""
    if not re.fullmatch(r"[0-9a-f]{%d,24}" % MIN_ID_LEN, ident):
        raise W.Usage(f"a limit id is {MIN_ID_LEN} to 24 hex characters, as the known-limits view "
                      "prints it (`id 1a2b3c4d`)")
    matches = [entry for entry in entries if entry["kind"] == "limit" and entry["key"].startswith(ident)]
    if not matches:
        raise W.Usage(f"no known limit has the id {ident}; `repact-task render` lists them in the "
                      "known-limits view")
    if len(matches) > 1:
        raise W.Usage(f"the id {ident} matches {len(matches)} limits; give more of it")
    if matches[0]["key"] in retired(entries):
        raise W.Usage(f"the known limit {ident} is already retired")
    return matches[0]


def retire(store, ident: str, reason: str) -> dict:
    """`repact-task ledger retire <id> --reason <text>`: append a manual retire event for one
    active limit. Returns the limit retired."""
    text, _ = clean_text(reason if isinstance(reason, str) else "")
    if not text.strip():
        raise W.Usage("--reason says why the limit no longer holds, in one line")
    with store._locked():
        entries, _ = read(store.main)
        limit = resolve(entries, ident)
        latest = max((entry["ts"] for entry in entries if isinstance(entry.get("ts"), str)), default=None)
        _append(ledger_path(store.main), _stamped(store, [
            {"kind": RETIRE, "target": limit["key"], "how": "manual", "text": text}], latest))
    return limit
