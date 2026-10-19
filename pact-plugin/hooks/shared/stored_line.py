"""
Location: pact-plugin/hooks/shared/stored_line.py
Summary: The one line a writer gets about its own metadata.handoff or metadata.teachback_submit
         write, so it knows whether to send the notify:
           - "HANDOFF stored on #<id> (owner <o>): rev N. ..."   the task file holds the write;
           - "NOT STORED: ... was refused ..."                   a PreToolUse check returned it;
           - "NOT STORED: ... was allowed, but ..."              the task file does not hold it.
         Field cause: a teammate notified "HANDOFF stored" on a write a check had returned, and
         the lead accepted a payload with no copy on disk.
         A lead's directive write (a *_rejection or an amend_<n>) gets directive_line() the same
         way: "Rejection stored on #<id> (owner <o>): revision N, digest <d>" or "Amendment
         amend_<n> stored on #<id> (owner <o>): digest <d>". The digest is what the one-line
         wake carries. WHAT and written_key are NOT extended for these: three gates read
         written_key as "a gated payload write".
Used by: shared/amendments.py, shared/payload_digest.py, shared/handoff_schema.py and
         shared/protocol_descriptor.py. refused() and after_write() lost their hook callers
         when 2.1.0 moved the writes onto the `repact` commands.
         Pure logic: no I/O. Every value it prints comes from a tool call or a task file, so
         each is bounded and printed on one line. The stored line carries the digest of the
         object on disk (shared/payload_digest.py): the notify names it instead of repeating
         the payload, and `repact inspect <id> --key` prints the same digest for the lead.
"""

from __future__ import annotations

from shared import amendments
from shared.payload_digest import payload_digest, without_nulls as _without_nulls

NOT_STORED = "NOT STORED"
WHAT = {"handoff": "HANDOFF", "teachback_submit": "teachback"}
# The task store holds rev as a JS number, exact only up to 2^53-1 (handoff_ordering_gate._REV_MAX).
REV_MAX = 2**53 - 1
RENDER_MAX = 40
ID_MAX = 32
_ABSENT = object()


def written_key(tool_input) -> str | None:
    """The gated key a TaskUpdate writes ("handoff" first), or None when it writes neither."""
    metadata = tool_input.get("metadata") if isinstance(tool_input, dict) else None
    if not isinstance(metadata, dict):
        return None
    return next((key for key in WHAT if metadata.get(key)), None)


def shown_id(task_id) -> str:
    """The task id as printed: id characters only, bounded."""
    return "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "_-")[:ID_MAX] or "?"


def _render(value) -> str:
    """Bounded repr of a value a task file or a tool call supplied."""
    try:
        text = repr(value)
    except Exception:   # noqa: BLE001 - a value that cannot be printed is named as such
        return "<unprintable>"
    return text if len(text) <= RENDER_MAX else text[:RENDER_MAX] + "..."


def valid_rev(rev) -> bool:
    """True for a positive integer the task store holds exactly (bool and float excluded)."""
    return type(rev) is int and 1 <= rev <= REV_MAX


def _owner(task: dict) -> str:
    owner = task.get("owner")
    name = "".join(ch for ch in owner if ch.isalnum() or ch in "_-.@")[:RENDER_MAX] if isinstance(owner, str) else ""
    return f"owner {name}" if name else "no owner"


def _normalised(key: str, sent):
    """sent as handoff_ordering_gate repaired it before the write (shared/handoff_schema.py,
    shared/teachback_schema.py), or sent itself when the repair cannot be loaded. Whether
    PostToolUse carries the input as sent or as repaired, the disk holds one of the two."""
    try:
        if key == "handoff":
            from shared.handoff_schema import normalise_handoff as normalise
        else:
            from shared.teachback_schema import normalise_teachback as normalise
        return normalise(sent)[0]
    except Exception:   # noqa: BLE001 - the comparison falls back to the input as sent
        return sent


def _rewrite(what: str, shown: str) -> str:
    return (f"write the full {what} again and notify only after the line "
            f"\"{what[0].upper()}{what[1:]} stored on #{shown}\".")


def refused(key: str, task_id=None) -> str:
    """The line a PreToolUse check appends to the reason it refuses the write with."""
    what = WHAT.get(key, key)
    shown = shown_id(task_id) if task_id else "?"
    return (f"{NOT_STORED}: the {what} write on #{shown} was refused, so the task file holds no copy "
            f"of it. Do not notify. Fix what is named above, then {_rewrite(what, shown)}")


def directive_refused(what: str, task_id=None) -> str:
    """The line a PreToolUse check appends when it returns a lead's directive write (what is
    "rejection" or "amendment")."""
    shown = shown_id(task_id) if task_id else "?"
    return (f"{NOT_STORED}: the {what} write on #{shown} was refused, so the task file holds no copy "
            f"of it. Do not send the wake. Fix what is named above, write the full {what} again, and "
            f"send the wake only after the line \"{what[0].upper()}{what[1:]} stored on #{shown}\".")


def directive_kind(key) -> str | None:
    """"rejection" for the two rejection keys, "amendment" for a well-formed amend_<n>, else None."""
    if key in ("handoff_rejection", "teachback_rejection"):
        return "rejection"
    digits = key[len("amend_"):] if isinstance(key, str) and key.startswith("amend_") else ""
    well_formed = digits.isascii() and digits.isdigit() and not digits.startswith("0") and len(digits) <= ID_MAX
    return "amendment" if well_formed else None


def directive_line(task_id, task, key: str) -> str:
    """The line for a lead's directive write under key, read from the task file as it stands
    after the write (task; None when it is missing or cannot be parsed). Stored: the digest
    the wake carries. Not on disk: NOT STORED, so no wake goes out for a value nobody can read."""
    what = directive_kind(key) or "directive"
    shown = shown_id(task_id)
    metadata = task.get("metadata") if isinstance(task, dict) and isinstance(task.get("metadata"), dict) else {}
    on_disk = metadata.get(key)
    if on_disk is None:
        return (f"{NOT_STORED}: the {what} write on #{shown} was allowed, but the task file does not hold "
                f"{_render(key)}. Do not send the wake. Read the task file, then write it again.")
    digest = payload_digest(on_disk) or "unavailable"
    send = "Send the one-line wake with the digest; never repeat the value in a message."
    if what == "rejection":
        closed = amendments.closed_on_arrival(key, on_disk, metadata)
        if closed:      # the write-time check could not read the task, or runs in note mode
            return (f"{NOT_STORED} AS OPEN: the rejection on #{shown} is on disk, but its owner will not be "
                    f"shown it: {closed}. Do not send the wake. Write it again.")
        number = on_disk.get("revision_number") if isinstance(on_disk, dict) else None
        revision = number if valid_rev(number) else _render(number)
        return f"Rejection stored on #{shown} ({_owner(task)}): revision {revision}, digest {digest}. {send}"
    return f"Amendment {key} stored on #{shown} ({_owner(task)}): digest {digest}. {send}"


def after_write(key: str, task_id, sent, task) -> str:
    """The line for a write the PreToolUse checks allowed. sent is the value the call wrote
    under key; task is the task file read back after the write, None when it is missing or
    cannot be parsed."""
    what = WHAT.get(key, key)
    shown = shown_id(task_id)

    def not_stored(reason: str) -> str:
        return (f"{NOT_STORED}: the {what} write on #{shown} was allowed, but {reason}. Do not notify. "
                f"Read the task file, then {_rewrite(what, shown)}")

    if not isinstance(task, dict):
        return not_stored("the task file is missing or cannot be read")
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    on_disk = metadata.get(key, _ABSENT)
    if on_disk is _ABSENT or on_disk is None:
        return not_stored(f"the task file holds no {what}")
    if not isinstance(on_disk, dict):
        return not_stored(f"the task file holds a {what} that is not an object")
    stored = _without_nulls(on_disk)
    if stored != _without_nulls(sent) and stored != _without_nulls(_normalised(key, sent)):
        other = f" (rev {_render(on_disk.get('rev'))})" if key == "handoff" and "rev" in on_disk else ""
        return not_stored(f"the task file holds a different {what}{other}")
    head = f"{what[0].upper()}{what[1:]} stored on #{shown} ({_owner(task)})"
    digest = payload_digest(on_disk) or "unavailable"
    if key != "handoff":
        return f"{head}, digest {digest}. Notify now with the digest; do not repeat the teachback."
    if "rev" not in on_disk:
        return (f"{head}: rev missing, digest {digest}. Notify now with the digest; set "
                "handoff.rev on the next write.")
    rev = on_disk["rev"]
    if valid_rev(rev):
        return (f"{head}: rev {rev}, digest {digest}. Notify now with rev {rev} and the digest; "
                "do not repeat the HANDOFF.")
    return (f"{head}: rev {_render(rev)} (not a positive integer). Do not notify yet: write the full "
            "HANDOFF again with rev as a positive integer.")
