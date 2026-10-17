"""
Location: pact-plugin/hooks/shared/ref_dispositions.py (1.5.0, the task-system review)
Summary: The project-task refs a team task carries, and the dispositions a HANDOFF owes them.
         Pure rules, stdlib only, no I/O: the gate (hooks/amendment_gate.py) and the acceptance
         bridge (hooks/shared/task_log/bridge.py) both read them, so one write and one read agree.
Used by: hooks/amendment_gate.py, hooks/shared/task_log/bridge.py, bin/repact-task.

Where a ref is machine-readable (the HARD gate, lead ruling amend_1 on #135):
  - the task log: a project task whose `ptask:<team>/<id>` link names this team task, written
    by `repact-task start <ref> --ptask <team>/<id>` (the dispatch boundary write);
  - the team task's metadata: `project_task` (one ref, the bridge's link key) or `refs`
    (a list), when a lead writes them at dispatch.
A ref that only appears in the task's description or subject is a MENTION: it gets a note, never
a return (fuzzy checks measured as noise in 1.4.1 U11).

A disposition: handoff["refs"][ref] = "done: <evidence>" | "partial: <what is left>"
                                      | "deferred: <why>" | "dropped: <why>"
`done` alone is accepted; the other verbs need a reason. The verb is read in any case after NFKC
(`DONE`, `Done: x`), and the reason may follow `:`, a spaced dash (`done - x`) or a full stop
(`done.`); `complete` and `completed` read as `done`; a lookalike letter (Cyrillic о) is not a verb. The bridge closes a `done` ref on the
lead's acceptance, and only when the lead bound it (task_log/bridge.py bound_refs); the others
stay open and are surfaced to the lead with the acceptance.
"""

from __future__ import annotations

import re
import unicodedata

REF_RE = re.compile(r"T-[0-9a-f]{8}")
_HEAD = re.compile(r"([A-Za-z]+)(.*)\Z", re.S)
_DASHES = "-–—"
REF_IN_TEXT = re.compile(r"\bT-[0-9a-f]{8}\b")
VERBS = ("done", "partial", "deferred", "dropped")
ALIASES = {"complete": "done", "completed": "done"}
REFS_KEY = "refs"
LINK_KEY = "project_task"
HANDOFF_KEY = "refs"

DISPOSE_HINT = ('Dispose of each project task in your HANDOFF: handoff.refs = {"T-<ref>": "done: '
                '<evidence>" | "partial: <what is left>" | "deferred: <why>" | "dropped: <why>"}.')


def is_ref(value) -> bool:
    return isinstance(value, str) and bool(REF_RE.fullmatch(value))


def _reason(rest: str) -> str | None:
    """What follows the verb: "" for none, the reason after a separator, None for anything else
    (`done-ish`, `done done`)."""
    tail = rest.strip()
    if tail in ("", "."):
        return ""
    if tail[0] == ":":
        return tail[1:].strip()
    if tail[0] in _DASHES and rest[0].isspace():
        return tail.lstrip(_DASHES).strip()
    if tail[0] == "." and tail[1].isspace():
        return tail[1:].strip()
    return None


def verb(value) -> str | None:
    """The disposition verb of `value`, or None when it is not a disposition."""
    if not isinstance(value, str):
        return None
    found = _HEAD.match(unicodedata.normalize("NFKC", value).strip())
    head = found.group(1).lower() if found else ""
    head = ALIASES.get(head, head)
    if head not in VERBS:
        return None
    reason = _reason(found.group(2))
    if reason is None:
        return None
    return head if reason or head == "done" else None


def is_disposition(value) -> bool:
    return verb(value) is not None


def metadata_refs(metadata) -> list[str]:
    """The refs the team task's metadata names: project_task, then refs, well-formed only."""
    if not isinstance(metadata, dict):
        return []
    found = [metadata.get(LINK_KEY)]
    listed = metadata.get(REFS_KEY)
    if isinstance(listed, list):
        found += listed
    return list(dict.fromkeys(ref for ref in found if is_ref(ref)))


def mentioned_refs(task) -> list[str]:
    """Refs written in the task's subject or description (the plan's refs, pasted), in order."""
    if not isinstance(task, dict):
        return []
    text = " ".join(str(task.get(key) or "") for key in ("subject", "description"))
    return list(dict.fromkeys(REF_IN_TEXT.findall(text)))


def dispositions(handoff) -> dict[str, str]:
    """{ref: disposition} from handoff.refs, keeping only well-formed refs with a disposition."""
    given = handoff.get(HANDOFF_KEY) if isinstance(handoff, dict) else None
    if not isinstance(given, dict):
        return {}
    return {ref: value.strip() for ref, value in given.items() if is_ref(ref) and is_disposition(value)}


def missing(refs: list[str], handoff) -> list[str]:
    """The refs among `refs` the HANDOFF does not dispose of, in order, each once."""
    given = dispositions(handoff)
    return [ref for ref in dict.fromkeys(refs) if ref not in given]


def malformed(handoff) -> list[str]:
    """The handoff.refs entries that are not dispositions (a bad ref or a bad value): the writer
    meant to dispose of them, so they are named."""
    given = handoff.get(HANDOFF_KEY) if isinstance(handoff, dict) else None
    if not isinstance(given, dict):
        return []
    return [str(ref)[:20] for ref, value in given.items() if not (is_ref(ref) and is_disposition(value))]
