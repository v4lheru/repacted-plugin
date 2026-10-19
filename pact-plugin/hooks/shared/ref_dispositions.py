"""
Location: pact-plugin/hooks/shared/ref_dispositions.py (1.5.0, the task-system review)
Summary: The project-task refs a team task carries, and the dispositions a HANDOFF owes them.
         Pure rules, stdlib only, no I/O: `repact submit` (hooks/shared/ops/submit.py) and the acceptance
         bridge (hooks/shared/task_log/bridge.py) both read them, so one write and one read agree.
Used by: hooks/shared/ops/submit.py, hooks/shared/task_log/bridge.py, bin/repact-task,
         bin/_front/criteria.py.

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

Acceptance criteria: `AC-1`..`AC-9999` (ASCII, no leading zero), named in
docs/<feature>/acceptance.md and carried in the team task's metadata.refs. They take the same
dispositions, but `done` must cite a recorded run by its tcount log stem
(`2026-10-18T120000Z-4812`, the shape the evidence ledger reads). They are kept apart from T- refs:
metadata_refs and dispositions never return one, so the task-log bridge never sees an AC.
"""

from __future__ import annotations

import re
import unicodedata

REF_RE = re.compile(r"T-[0-9a-f]{8}")
_HEAD = re.compile(r"([A-Za-z]+)(.*)\Z", re.S)
_DASHES = "-–—"
AC_RE = re.compile(r"AC-[1-9][0-9]{0,3}")     # ASCII classes: a fullwidth or Arabic digit is not one
REF_IN_TEXT = re.compile(r"\bT-[0-9a-f]{8}\b")
VERBS = ("done", "partial", "deferred", "dropped")
ALIASES = {"complete": "done", "completed": "done"}
REFS_KEY = "refs"
LINK_KEY = "project_task"
HANDOFF_KEY = "refs"

DISPOSE_HINT = ('Dispose of each project task in your HANDOFF: handoff.refs = {"T-<ref>": "done: '
                '<evidence>" | "partial: <what is left>" | "deferred: <why>" | "dropped: <why>"}.')

AC_HINT = ('Close each acceptance criterion in handoff.refs: {"AC-<n>": "done: <run log stem, e.g. '
           '2026-10-18T120000Z-4812>"}, a passing run in this machine\'s ledger (`repact inspect '
           '--acceptance <acceptance.md>` lists them), else "partial: ...", "deferred: ..." or "dropped: ...".')


def is_ref(value) -> bool:
    return isinstance(value, str) and bool(REF_RE.fullmatch(value))


def is_ac(value) -> bool:
    return isinstance(value, str) and bool(AC_RE.fullmatch(value))


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
    return [str(ref)[:20] for ref, value in given.items()
            if not ((is_ref(ref) or is_ac(ref)) and is_disposition(value))]


def metadata_acs(metadata) -> list[str]:
    """The AC ids the team task's metadata.refs names, well-formed only, in order, each once."""
    listed = metadata.get(REFS_KEY) if isinstance(metadata, dict) else None
    return list(dict.fromkeys(ac for ac in listed if is_ac(ac))) if isinstance(listed, list) else []


_LOOKALIKE = str.maketrans({"\u0410": "A", "\u0391": "A", "\u0421": "C", "\u03f9": "C"})
_AC_LIKE = re.compile(r"AC\W{0,2}\d")


def ac_lookalikes(metadata) -> list[str]:
    """The metadata.refs entries that read as an AC id but are not one (AC-01, AC-0, a fullwidth
    digit, a Cyrillic letter, lower case), each once and cut to 20 characters: a lead's typo there
    would otherwise drop the criterion with no word."""
    listed = metadata.get(REFS_KEY) if isinstance(metadata, dict) else None
    if not isinstance(listed, list):
        return []
    odd = [value for value in listed if isinstance(value, str) and not is_ac(value)
           and _AC_LIKE.match(unicodedata.normalize("NFKC", value[:64]).upper().translate(_LOOKALIKE))]
    return list(dict.fromkeys(value[:20] for value in odd))


def ac_run_ids(value) -> list[str]:
    """The runs a disposition cites, by log stem (the evidence ledger's RUN_STEM), each once."""
    if not isinstance(value, str):
        return []
    from shared.ledger import RUN_STEM
    return list(dict.fromkeys(match.group(0) for match in RUN_STEM.finditer(value[:4096])))


def ac_dispositions(handoff) -> dict[str, str]:
    """{AC id: disposition} from handoff.refs, well-formed ids with a disposition only."""
    given = handoff.get(HANDOFF_KEY) if isinstance(handoff, dict) else None
    if not isinstance(given, dict):
        return {}
    return {ac: value.strip() for ac, value in given.items() if is_ac(ac) and is_disposition(value)}


def ac_problems(handoff) -> dict[str, str]:
    """{AC id: why} for each AC handoff.refs disposes of badly: not a disposition, or `done` with
    no run id. Shape only; whether a cited run exists and passed is the gate's ledger read."""
    given = handoff.get(HANDOFF_KEY) if isinstance(handoff, dict) else None
    problems = {}
    for ac, value in (given.items() if isinstance(given, dict) else ()):
        if not is_ac(ac):
            continue
        if not is_disposition(value):
            problems[ac] = "not a disposition"
        elif verb(value) == "done" and not ac_run_ids(value):
            problems[ac] = "done names no run id (cite the tcount log stem, e.g. 2026-10-18T120000Z-4812)"
    return problems
