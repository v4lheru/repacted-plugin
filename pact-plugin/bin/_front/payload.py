"""
Location: pact-plugin/bin/_front/payload.py
Summary: `repact inspect <id> --key teachback|handoff|rejection|amend|amend_<n> [--digest D]` and
`repact inspect --relayed-handoff FILE` (formerly bin/repact-payload).
Read-only. The header gives rev and digest, computed by the same function as the stored line
(hooks/shared/payload_digest.py) over the object on disk; the body is that object scrubbed with
the scrub helper (secret values and denylist literals replaced). --digest compares a notify's
digest with the stored one: a match means the reader saw what was stored; a mismatch means the
object changed after the notify.
A task file that is gone (the store drains completed tasks mid-session) is read from
the session journal instead for teachback and handoff: the HANDOFF from its latest agent_handoff
row, which holds it whole, and the teachback from its latest task_metadata_snapshot row. The
header then names that source, says so when the journal kept only the head of an oversized value,
and, when task ids restarted after a drain, says how many tasks used the id.
rejection prints every *_rejection that hooks/shared/amendments.open_rejection holds open; amend
prints every amend_<n> that handoff.amendments does not name, in numeric order; amend_<n> prints
that one amendment, disposed or not. rejection and amend are the tests bin/repact-wait returns
on, so its next: line never names a reader that finds nothing. There is no journal fallback for
these: nothing open, or a task file that is gone or unreadable, is exit 4.
--relayed-handoff FILE reads a `claude -p` teammate's final output saved to FILE: it
prints the last fenced ```json block that parses to an object as {"handoff": {..., "relayed":
true}}; a warning on stderr names missing HANDOFF keys and relative produced paths.
Used by: bin/_repact_ops.py (`repact inspect`).
Exit codes: 0 printed (and the digest matched, when given); 2 usage error; 3 digest mismatch;
4 task or payload not found.
"""
from __future__ import annotations

import json
import os
import re
import sys

from shared import task_files as ev  # noqa: E402
from _repact_common import denylist_paths, load_denylist, scrub_value  # noqa: E402
from shared.amendments import open_rejection  # noqa: E402
from shared.payload_digest import DIGEST_LEN, payload_digest, stored_teachback  # noqa: E402

KEYS = {"handoff": "handoff", "teachback": "teachback_submit", "rejection": "rejection",
        "amend": "amend"}
DIRECTIVES = ("rejection", "amend")     # read from the task file only, several objects each
REJECTION_SUFFIX = "_rejection"
_AMEND_KEY = re.compile(r"amend_\d+")   # bin/repact-wait's rule, kept equal by test
# Above the largest rejection the shape gate stores (a 2000-character reason and 20 corrections
# of 4000), so a well-formed one is never cut.
DIRECTIVE_BODY_CHARS = 100_000
# A team or task id names one file under tasks/: no separators, no leading dot.
_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{%d}" % DIGEST_LEN)
EXAMPLE = "repact inspect 88 --key handoff --digest 3f9a0c1b2d4e"


def shown(value, limit: int = 64) -> str:
    """A task-file value as printed in the header: printable, one line, bounded."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    return text if len(text) <= limit else text[:limit] + "..."


def journal_task(team: str | None, task_id: str, session_id: str | None):
    """(task, team) from the session journal when the task file is gone, else (None, team).
    Without --team the team is this session's own, never another session's."""
    team = team or ev.team_from_session(session_id)
    if not team or not _NAME.fullmatch(team):
        return None, team
    return ev.task_from_journal(team, task_id, with_metadata=True), team


def cut_in_journal(payload) -> bool:
    """True for the marker the journal writes in place of a value over its size cap."""
    return isinstance(payload, dict) and payload.get("_truncated") is True and "original_bytes" in payload


def _amend_order(key: str):
    """Numeric order; a suffix int() refuses (over its digit limit) sorts last, by name."""
    try:
        return 0, int(key[len("amend_"):]), key
    except ValueError:
        return 1, 0, key


def open_directives(kind: str, metadata: dict) -> list[tuple[str, object]]:
    """(key, value) of what the lead wrote and the owner has not answered, by the tests
    bin/repact-wait returns on: every truthy *_rejection that open_rejection holds open, or
    every amend_<n> that handoff.amendments does not name; for one amend_<n>, that key alone."""
    if _AMEND_KEY.fullmatch(kind):
        return [(kind, metadata[kind])] if metadata.get(kind) is not None else []
    if kind == "rejection":
        return sorted(((key, value) for key, value in metadata.items()
                       if isinstance(key, str) and key.endswith(REJECTION_SUFFIX) and value
                       and open_rejection(key, value, metadata)), key=lambda item: item[0])
    handoff = metadata.get("handoff") if isinstance(metadata.get("handoff"), dict) else {}
    disposed = handoff.get("amendments") if isinstance(handoff.get("amendments"), dict) else {}
    keys = [key for key in metadata
            if isinstance(key, str) and _AMEND_KEY.fullmatch(key) and key not in disposed]
    return [(key, metadata[key]) for key in sorted(keys, key=_amend_order)]


def directive_body(value, denylists) -> str:
    """One directive's value as printed: scrubbed, bounded, and never a reason to raise (the
    value is disk state: any type, any size, any depth, lone surrogates)."""
    try:
        text = json.dumps(scrub_value(value, *denylists), indent=1, ensure_ascii=False)
    except (RecursionError, TypeError, ValueError):
        return "[not printable: read the task file]"
    text = text.encode("utf-8", "replace").decode("utf-8")
    if len(text) > DIRECTIVE_BODY_CHARS:
        text = (f"{text[:DIRECTIVE_BODY_CHARS]}\n[cut: {len(text)} characters stored, the first "
                f"{DIRECTIVE_BODY_CHARS} shown]")
    return text


def print_directives(kind: str, task: dict, task_id: str, team: str, expected: str | None) -> int:
    """Print every open rejection or undisposed amendment of the task; the exit code."""
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    found = open_directives(kind, metadata)
    if not found:
        what = {"rejection": "open rejection", "amend": "undisposed amendment"}.get(kind, kind)
        print(f"repact inspect: no {what} on #{task_id} (team {team})", file=sys.stderr)
        return 4
    where = (f"team {team}, owner {shown(task.get('owner') or 'none')}, "
             f"status {shown(task.get('status'))}")
    paths = denylist_paths()
    denylists = (load_denylist(paths[0]), load_denylist(paths[1]))
    digests = []
    for key, value in found:
        digests.append(payload_digest(value) or "unavailable")
        facts = ""
        if kind == "rejection" and isinstance(value, dict):
            facts = (f"revision_number {shown(value.get('revision_number'), 24)}, "
                     f"rev {shown(value.get('rev'), 24)}, ")
        print(f"{shown(key)} on #{task_id} ({where}): {facts}digest {digests[-1]}")
        print(directive_body(value, denylists))
    if expected is None:
        return 0
    if expected in digests:
        print(f"digest matches the wake ({expected})")
        return 0
    print(f"DIGEST MISMATCH: the wake named {expected}, the task file holds {', '.join(digests)}. "
          "The stored object changed after the wake; what is on disk is printed above.",
          file=sys.stderr)
    return 3


RELAY_MAX_BYTES = 1024 * 1024
_FENCE = re.compile(r"```json[ \t]*\r?\n(.*?)\r?\n[ \t]*```", re.DOTALL)
HANDOFF_KEYS = ("rev", "amendments", "produced", "decisions", "uncertainty", "integration", "open_questions")


def relayed_handoff(path: str) -> int:
    """Print the metadata that stores a relayed HANDOFF: the last fenced json object of the output."""
    try:
        with open(path, "rb") as handle:
            text = handle.read(RELAY_MAX_BYTES + 1).decode("utf-8", errors="replace")
    except OSError as err:
        print(f"repact inspect: cannot read {path}: {err.strerror}", file=sys.stderr)
        return 4
    if len(text) > RELAY_MAX_BYTES:
        print(f"repact inspect: {path} is over {RELAY_MAX_BYTES} bytes; save only the final output", file=sys.stderr)
        return 2
    for block in reversed(_FENCE.findall(text)):
        try:
            body = json.loads(block)
        except (ValueError, RecursionError):
            continue
        if isinstance(body, dict) and isinstance(body.get("handoff"), dict):
            body = body["handoff"]
        if not isinstance(body, dict):
            continue
        missing = [key for key in HANDOFF_KEYS if key not in body]
        produced = body.get("produced") if isinstance(body.get("produced"), list) else []
        relative = [str(item)[:80] for item in produced if not str(item).startswith("/")]
        if missing or relative:
            print("repact inspect: warning: " + "; ".join(filter(None, [
                missing and f"missing keys {', '.join(missing)}",
                relative and f"relative produced paths (the gate finds no runs for them): {relative[:3]}"])),
                file=sys.stderr)
        print(json.dumps({"handoff": {**body, "relayed": True}}, ensure_ascii=False))
        return 0
    print(f"repact inspect: no fenced ```json block holding an object in {path}", file=sys.stderr)
    return 4


def read(task_id: str, key: str, team: str | None = None, digest: str | None = None) -> int:
    """Print the stored object `key` of task `task_id` (KEYS, or one amend_<n>); the exit code."""
    task_id = str(task_id)
    if not _NAME.fullmatch(task_id) or (team and not _NAME.fullmatch(team)):
        return _usage("a task id or team name is letters, digits, '_', '-' and '.' only")
    if key not in KEYS and not _AMEND_KEY.fullmatch(key or ""):
        return _usage(f"--key is one of {', '.join(KEYS)} or amend_<n>, not {shown(key, 40)!r}")
    expected = digest.strip().lower() if digest else None
    if expected is not None and not _DIGEST.fullmatch(expected):
        return _usage(f"--digest is {DIGEST_LEN} hex characters, as the stored line printed it")
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    team = team or ev.resolve_team(session_id, task_id)
    task = ev.load_task(ev.task_path(team, task_id)) if team else None
    directive = key in DIRECTIVES or bool(_AMEND_KEY.fullmatch(key))
    if task is None and not directive:
        task, team = journal_task(team, task_id, session_id)
    if task is None:
        print(f"repact inspect: task {task_id} not found{' in team ' + team if team else ''}",
              file=sys.stderr)
        return 4
    if directive:
        return print_directives(key, task, task_id, team, expected)
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    key = KEYS[key]
    # A teachback is read where `repact submit` stores it first, then the Teams writer's key.
    payload = stored_teachback(metadata) if key == "teachback_submit" else metadata.get(key)
    if payload is None:
        print(f"repact inspect: no {key} stored on #{task_id} (team {team})", file=sys.stderr)
        return 4
    digest = payload_digest(payload) or "unavailable"
    rev = f"rev {shown(payload.get('rev'), 24)}, " if key == "handoff" and isinstance(payload, dict) else ""
    status = f"status {shown(task.get('status'))}"
    if task.get("source") == "journal":
        status = "task file gone, source: session journal"
        if isinstance(task.get("arcs"), int) and task["arcs"] > 1:
            status += f", latest of {task['arcs']} tasks that used this id: {shown(task.get('subject'))}"
        if cut_in_journal(payload):
            status += f", only the head of {shown(payload.get('original_bytes'), 24)} bytes was kept"
    print(f"{key} on #{task_id} (team {team}, owner {shown(task.get('owner') or 'none')}, "
          f"{status}): {rev}digest {digest}")
    if expected is not None:
        if expected != digest:
            print(f"DIGEST MISMATCH: the notify named {expected}, the task file holds {digest}. "
                  "The stored object changed after the notify; read it below before accepting.",
                  file=sys.stderr)
        else:
            print(f"digest matches the notify ({expected})")
    paths = denylist_paths()
    print(json.dumps(scrub_value(payload, load_denylist(paths[0]), load_denylist(paths[1])),
                     indent=1, ensure_ascii=False))
    return 3 if expected is not None and expected != digest else 0


def _usage(message: str) -> int:
    print(f"repact inspect: error: {message}\n\n  {EXAMPLE}", file=sys.stderr)
    return 2
