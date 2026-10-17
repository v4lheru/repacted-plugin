"""
Location: pact-plugin/hooks/shared/amendments.py
Summary: The amend_<n> metadata convention: one top-level task-metadata key per lead
         amendment, and the checks the HANDOFF gate applies to it (every amendment has a
         disposition; handoff.rev pins revisions; a rejection names the rev it reviewed).
Used by: hooks/amendment_gate.py (PreToolUse TaskUpdate), hooks/amendment_relay.py
         (PostToolUse, imported only when a task carries an amendment not yet relayed),
         hooks/teammate_idle.py (the unread-key re-prompt), bin/repact-amend, bin/repact-wait
         and bin/repact-payload (open_rejection). Pure logic: stdlib only, no I/O.

Why top-level keys and not a list: TaskUpdate merges metadata only at the top level, so a
list would be replaced whole on every append. Separate keys cannot collide.

An amendment:  metadata["amend_3"] = {"text": "...", "at": "<utc>", "from": "team-lead",
                                      "kind": "ruling|correction|premise|addendum|note|scope|commit|halt"}
               plus "sha" (7-40 hex) when kind is "commit": the lead's commit confirmation
               or metadata["amend_3"] = "..." (text only: no author, so never shown as the lead's)
A rejection:   metadata["handoff_rejection"] or metadata["teachback_rejection"] =
               {"reason": str, "corrections": [str, ...], "since": "<utc>", "revision_number": int,
                "rev": int}. rejection_shape_problem is the shape rule the gate returns a write on;
               open_rejection says whether the owner has answered it (bin/repact-wait and
               bin/repact-payload share it); closed_on_arrival returns a write that would store
               an answered one; unread_lines hands an open one to its owner.
A wake:        wake_line(): the one line a lead sends after a directive is on disk (id and
               digest, never a value). The protocol descriptor's WAKE_LINE matches it.
A disposition: handoff["amendments"]["amend_3"] = "applied: <where>" | "declined: <why>"
                                                  | "disputed: <why>" | "partial: <what is left>"
                                                  | "ack" | "ack: <note>"
A grant:       metadata["docker_grant"] = <any value but null, false or "">: the lead's answer
               to a request for a shared resource, on the task of the teammate that asked. A
               message sent while that teammate is mid-turn can go unread, so the
               relay and the idle hook hand the key over from the task file. No disposition.
The rev checks (rev_problem, rejection_problem) feed the gate's audit log only: a lead ruling
on task 103 made them non-denying.
"""

from __future__ import annotations

import datetime
import json
import re

AMEND_PREFIX = "amend_"
KINDS = ("ruling", "correction", "premise", "addendum", "note", "scope", "commit", "halt")
COMMIT = "commit"
DISPOSITION_VERBS = ("applied", "declined", "disputed")
ACK = "ack"
# Accepted beside the three verbs: "partial: <what is left>" needs
# its text like they do, and "ack" may carry a note after a colon. Kept out of DISPOSITION_VERBS,
# which the protocol descriptor prints.
PARTIAL = "partial"
# The word the retired directive_reflection mode used for enforce; read by amendment_gate's
# gate_mode and the other way round by shared/evidence/ledger.py use_mode ('enforce' reads 'gate').
ALIASES = {"gate": "enforce"}
PREVIEW_CHARS = 100
RELAY_TEXT_CHARS = 2000
RELAY_LABEL_CHARS = 40
LEAD = "team-lead"
GRANT_SUFFIX = "_grant"
GRANT_TEXT_CHARS = 300
REJECTION_SUFFIX = "_rejection"
REJECTION_KEYS = ("teachback_rejection", "handoff_rejection")
REJECTION_REASON_MAX = 2000
REJECTION_CORRECTIONS_MAX = 20
REJECTION_CORRECTION_MAX = 4000
REJECTION_SINCE_MAX = 64
REJECTION_TEXT_CHARS = 300
# The task store holds a number as a JS number, exact only up to 2^53-1 (stored_line.REV_MAX).
INT_MAX = 2**53 - 1
WAKE_VERBS = ("accepted", "rejected", "amended")

_AMEND_KEY = re.compile(r"amend_([1-9][0-9]*)")
_GRANT_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}_grant")
_LINE_BREAKS = re.compile(r"[\x00-\x1f\x7f\x85\u2028\u2029]+")
_SHA = re.compile(r"[0-9a-f]{7,40}")


def amendment_number(key: str) -> int | None:
    """n for a well-formed key "amend_<n>" (n >= 1, no leading zero, nothing after the
    digits: a trailing line break is not part of a key), else None."""
    match = _AMEND_KEY.fullmatch(key) if isinstance(key, str) else None
    return int(match.group(1)) if match else None


def amendments(metadata: dict | None) -> dict[str, dict]:
    """The task's amendments in numeric order: {key: value} for well-formed amend_<n> keys
    whose value is an object carrying a text, or a non-blank string, which is read as
    {"text": <the string>} (a lead writes the key by hand as plain text: the six amend keys
    left on one field session's task files were all strings, and the HANDOFF gate saw none
    of them). Anything else under the prefix is ignored."""
    if not isinstance(metadata, dict):
        return {}
    found = [(amendment_number(key), key, {"text": value} if isinstance(value, str) and value.strip() else value)
             for key, value in metadata.items()]
    return {key: value for number, key, value in sorted(found, key=lambda item: item[0] or 0)
            if number is not None and isinstance(value, dict) and isinstance(value.get("text"), str)}


def next_number(metadata: dict | None) -> int:
    """The next free n: one past the highest used key, so a gap is never refilled."""
    used = [amendment_number(key) for key in (metadata or {})]
    return max([number for number in used if number is not None], default=0) + 1


def sha_problem(kind: str, sha) -> str | None:
    """None when sha fits the kind: 7 to 40 hex characters with "commit", absent with any
    other kind. Else the one-line reason."""
    if kind != COMMIT:
        return None if sha is None else "--sha goes with --kind commit only"
    if sha is None:
        return "--kind commit needs --sha <7-40 hex characters>"
    if not isinstance(sha, str) or not _SHA.fullmatch(sha.lower()):
        return "--sha must be 7 to 40 hex characters"
    return None


def make_amendment(text: str, kind: str, at: str, author: str = "team-lead", sha: str | None = None) -> dict:
    """One amendment value. Raises ValueError on an empty text, an unknown kind, or a sha
    that does not fit the kind (sha_problem)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("an amendment needs a non-empty text")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    problem = sha_problem(kind, sha)
    if problem:
        raise ValueError(problem)
    value = {"text": text.strip(), "at": at, "from": author, "kind": kind}
    return dict(value, sha=sha.lower()) if kind == COMMIT else value


def is_disposition(value) -> bool:
    """True for "ack", "ack: <note>" or "<applied|declined|disputed|partial>: <non-empty reason>"."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    verb, separator, reason = value.partition(":")
    if verb.strip() == ACK:
        return True
    return bool(separator) and verb.strip() in (*DISPOSITION_VERBS, PARTIAL) and bool(reason.strip())


def missing_dispositions(task_amendments: dict[str, dict], handoff: dict) -> list[tuple[str, str]]:
    """(key, first PREVIEW_CHARS of its text) for every amendment the HANDOFF does not dispose."""
    given = handoff.get("amendments") if isinstance(handoff, dict) else None
    given = given if isinstance(given, dict) else {}
    return [(key, value["text"][:PREVIEW_CHARS]) for key, value in task_amendments.items()
            if not is_disposition(given.get(key))]


def handoff_rev(handoff) -> int | None:
    """handoff.rev when it is a positive int (bool excluded), else None."""
    rev = handoff.get("rev") if isinstance(handoff, dict) else None
    return rev if isinstance(rev, int) and not isinstance(rev, bool) and rev >= 1 else None


def expected_rev(previous_handoff) -> int:
    """1 for a first HANDOFF (or one written before revs existed), else previous rev + 1."""
    previous = handoff_rev(previous_handoff)
    return previous + 1 if previous is not None else 1


def rev_problem(previous_handoff, new_handoff: dict) -> str | None:
    """None when new_handoff.rev is the expected next revision, else a one-line reason."""
    expected = expected_rev(previous_handoff)
    given = new_handoff.get("rev") if isinstance(new_handoff, dict) else None
    if handoff_rev(new_handoff) == expected:
        return None
    return f"handoff.rev must be {expected} (given: {given!r})"


def rejection_problem(current_handoff, rejection, written: str) -> str | None:
    """None when the rejection names the HANDOFF rev it reviewed, else the re-review message.
    A HANDOFF without a rev predates the convention and is not checked."""
    current = handoff_rev(current_handoff)
    if current is None:
        return None
    given = rejection.get("rev") if isinstance(rejection, dict) else None
    if given == current and not isinstance(given, bool):
        return None
    return f"HANDOFF is now rev {current} (written {written}); re-review before rejecting."


def _exact_int(value) -> bool:
    """True for a positive integer the task store holds exactly (bool and float excluded):
    the rule of stored_line.valid_rev, kept here so this module stays free of imports."""
    return type(value) is int and 1 <= value <= INT_MAX


def is_rejection_key(key) -> bool:
    """True for a key the shape gate checks: anything that ends with "_rejection" once its
    case and surrounding white space are folded. Wider on purpose than the test bin/repact-wait
    wakes on (key.endswith("_rejection")), so no spelling the waiter reads is unchecked."""
    return isinstance(key, str) and key.strip().casefold().endswith(REJECTION_SUFFIX)


def _zoned(value) -> bool:
    """True for a bounded string datetime.fromisoformat parses with a zone."""
    if not isinstance(value, str) or not 0 < len(value) <= REJECTION_SINCE_MAX:
        return False
    if value.endswith("Z"):     # fromisoformat reads "Z" only from Python 3.11
        value = value[:-1] + "+00:00"
    try:
        return datetime.datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _field_problem(key: str, value: dict, current_handoff) -> str | None:
    """The first field of a rejection object that breaks its rule, else None."""
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return "reason must be a non-empty string"
    if len(reason) > REJECTION_REASON_MAX:
        return f"reason is over {REJECTION_REASON_MAX} characters"
    corrections = value.get("corrections")
    if not isinstance(corrections, list) or not 1 <= len(corrections) <= REJECTION_CORRECTIONS_MAX:
        return f"corrections must be a list of 1 to {REJECTION_CORRECTIONS_MAX} non-empty strings"
    for index, item in enumerate(corrections):
        if not isinstance(item, str) or not item.strip():
            return f"corrections[{index}] must be a non-empty string"
        if len(item) > REJECTION_CORRECTION_MAX:
            return f"corrections[{index}] is over {REJECTION_CORRECTION_MAX} characters"
    if not _zoned(value.get("since")):
        return "since must be an ISO time with a zone (date -u +%Y-%m-%dT%H:%M:%S+00:00)"
    if not _exact_int(value.get("revision_number")):
        return f"revision_number must be an integer from 1 to {INT_MAX}"
    rev = value.get("rev")
    stored_has_rev = isinstance(current_handoff, dict) and current_handoff.get("rev") is not None
    if rev is None:
        return "rev is missing: name the HANDOFF rev you reviewed" \
            if key == "handoff_rejection" and stored_has_rev else None
    return None if _exact_int(rev) else f"rev must be an integer from 1 to {INT_MAX}"


def rejection_shape_problem(key: str, value, current_handoff) -> str | None:
    """None for a well-formed rejection under `key` and for None (the lead clearing one), else
    one line naming the key and the field. current_handoff is the HANDOFF the task file
    holds, or None when the task could not be read: only the rev-required rule needs it.
    Never raises, and never prints a value: the reason names fields only. Extra fields pass."""
    if value is None:
        return None
    shown = _one_line(key, RELAY_LABEL_CHARS) if isinstance(key, str) else "?"
    if key not in REJECTION_KEYS:
        return (f"unknown rejection key {shown!r}: a rejection goes under "
                f"{' or '.join(REJECTION_KEYS)}")
    if not isinstance(value, dict):
        return f"{key} must be an object {{reason, corrections, since, revision_number, rev}}"
    problem = _field_problem(key, value, current_handoff)
    return f"{key}.{problem}" if problem else None


def _int(value, default: int) -> int | None:
    """An int field, `default` when absent, None when present but not an int."""
    if value is None:
        return default
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _judged(key: str, record: dict, metadata: dict) -> tuple[str, int | None, int | None]:
    """(the field a rejection is judged by, its number, the task's number). A handoff_rejection
    is judged by rev when it and the stored HANDOFF both carry one, else by revision_number
    (1 when absent). A number is None when it is present but not an int."""
    handoff = metadata.get("handoff") if isinstance(metadata.get("handoff"), dict) else {}
    if key == "handoff_rejection" and "rev" in record and "rev" in handoff:
        return "rev", _int(record.get("rev"), 0), _int(handoff.get("rev"), 0)
    return "revision_number", _int(record.get("revision_number"), 1), _int(metadata.get("revision_number"), 1)


def open_rejection(key: str, record, metadata: dict) -> bool:
    """True unless the teammate has visibly answered this rejection with a newer revision."""
    if not isinstance(record, dict):
        return bool(record)
    _, judged, current = _judged(key, record, metadata)
    return judged is None or current is None or judged >= current


def after_write(metadata: dict | None, update: dict) -> dict:
    """The task metadata as it stands once `update` is merged in: TaskUpdate merges at the
    top level only, and a null value removes its key."""
    merged = dict(metadata) if isinstance(metadata, dict) else {}
    for key, value in update.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def closed_on_arrival(key: str, value, metadata: dict) -> str | None:
    """One line naming the number to use when a rejection written under `key` would not be
    open (open_rejection, the predicate bin/repact-wait and bin/repact-payload share) against
    `metadata`, the task as it stands after the write (after_write): the waiter, the reader
    and the relay would all show nothing, so the wake would point at nothing. None for an
    open one, for a clear (None) and for a value rejection_shape_problem already returns."""
    if key not in REJECTION_KEYS or not isinstance(value, dict) or not isinstance(metadata, dict):
        return None
    if open_rejection(key, value, metadata):
        return None
    field, _, current = _judged(key, value, metadata)
    if field == "rev":
        return (f"{key}.rev is below the stored HANDOFF's rev {current}: the owner has answered that "
                f"revision, so this rejection would be closed as it lands. Review rev {current} and write "
                f'"rev": {current}')
    return (f"{key}.revision_number is below the task's metadata.revision_number ({current}): the owner "
            f'has answered that round, so this rejection would be closed as it lands. Write "revision_number": '
            f"{current}")


def rejection_token(key: str, record) -> str:
    """The relay stamp token of one rejection: "<key>@<revision_number>r<rev>" (0 for a value
    without a usable one), so a second rejection under the same key, in a new round or of a
    new HANDOFF rev, is handed over again. hooks/amendment_relay.py builds the same token
    without importing this module."""
    number = record.get("revision_number") if isinstance(record, dict) else None
    rev = record.get("rev") if isinstance(record, dict) else None
    return f"{key}@{number if type(number) is int else 0}r{rev if type(rev) is int else 0}"


def rejection_line(key: str, task_id: str, record) -> str | None:
    """The line that hands one rejection to its owner, or None for a value that is not a
    rejection object with a reason. Nothing in the record is trusted: the reason is cut at
    REJECTION_TEXT_CHARS and folded to one line, and the task id keeps its id characters."""
    if not isinstance(record, dict) or not isinstance(record.get("reason"), str):
        return None
    shown_id = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "_-")[:RELAY_LABEL_CHARS] or "?"
    return (f"REJECTION {key} on #{shown_id}: {_one_line(record['reason'], REJECTION_TEXT_CHARS)}; "
            f"run repact-payload {shown_id} --key rejection")


def wake_line(recipient: str, task_id: str, verb: str, digest: str) -> str:
    """The one line a lead sends after a directive is on disk:
    "[team-lead→<teammate>] Task #<id>: <accepted|rejected|amended> on disk, digest <d>."
    It never carries a value. Raises ValueError on an unknown verb."""
    if verb not in WAKE_VERBS:
        raise ValueError(f"verb must be one of {', '.join(WAKE_VERBS)}")
    name = "".join(ch for ch in str(recipient) if ch.isalnum() or ch in "_-.@")[:RELAY_LABEL_CHARS]
    shown_id = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "_-")[:RELAY_LABEL_CHARS]
    shown_digest = "".join(ch for ch in str(digest) if ch.isalnum() or ch in "<> _-")[:RELAY_LABEL_CHARS]
    return (f"[{LEAD}→{name or '<owner>'}] Task #{shown_id or '?'}: {verb} on disk, "
            f"digest {shown_digest or 'unavailable'}.")


def _one_line(value, limit: int) -> str:
    """The first `limit` characters of value on one line: every line break and control
    character becomes a space, so relayed text cannot start a line of its own."""
    return _LINE_BREAKS.sub(" ", value[:limit]).strip()


def relay_line(key: str, task_id: str, value) -> str | None:
    """The additionalContext line for one relayed amendment, or None for a value that is
    not an amendment. The value is whatever a task file holds, so nothing in it is trusted:
    the text is cut at RELAY_TEXT_CHARS with the cut marked, kind, author and task id are
    cut at RELAY_LABEL_CHARS, all are folded to one line, the task id keeps its id
    characters only, and the LEAD label is printed only for an author that is exactly
    "team-lead"."""
    if not isinstance(value, dict) or not isinstance(value.get("text"), str):
        return None
    text = value["text"]
    body = _one_line(text, RELAY_TEXT_CHARS)
    if len(text) > RELAY_TEXT_CHARS:
        body += f" [cut: {len(text) - RELAY_TEXT_CHARS} more characters; read the task file]"
    author, kind = value.get("from"), value.get("kind")
    kind = _one_line(kind, RELAY_LABEL_CHARS) if isinstance(kind, str) else ""
    sha = value.get("sha")
    if kind == COMMIT and isinstance(sha, str) and _SHA.fullmatch(sha):
        body += f" [sha {sha}]"
    shown_id = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "_-")
    where = f"on #{shown_id[:RELAY_LABEL_CHARS] or '?'} ({kind or 'note'})"
    if author == LEAD:
        return f"LEAD AMENDMENT {key} {where}: {body}"
    author = _one_line(author, RELAY_LABEL_CHARS) if isinstance(author, str) else ""
    return f"AMENDMENT {key} (from: {author or 'unknown'}) {where}: {body}"


def grants(metadata: dict | None) -> dict[str, object]:
    """The task's grants in key order: {key: value} for keys shaped "<resource>_grant"
    (lower case, digits, "_" and "-") whose value is not null, false or blank, which is how
    a lead withdraws one."""
    if not isinstance(metadata, dict):
        return {}
    return {key: metadata[key] for key in sorted(k for k in metadata if isinstance(k, str))
            if _GRANT_KEY.fullmatch(key) and metadata[key] not in (None, False)
            and not (isinstance(metadata[key], str) and not metadata[key].strip())}


def grant_line(key: str, task_id: str, value) -> str:
    """The line that hands one grant to the teammate. The value is whatever the task file
    holds: it is printed as JSON unless it is a string, cut at GRANT_TEXT_CHARS and folded
    to one line, and the task id keeps its id characters only."""
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError, RecursionError):
            value = "(unreadable value: read the task file)"
    shown_id = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "_-")
    return (f"GRANT {key} on #{shown_id[:RELAY_LABEL_CHARS] or '?'}: "
            f"{_one_line(value, GRANT_TEXT_CHARS)} [it is on your task file: act on it now, "
            "a message about it may never reach you]")


def unread_lines(metadata: dict | None, task_id: str, already: set) -> tuple[list[str], set]:
    """(the relay lines for the amendments, grants and open rejections not in `already`,
    `already` plus their keys). One function for the relay and the idle hook, so both hand
    over the same keys in the same words. A rejection is recorded by rejection_token, not by
    its key, and is recorded whether or not it printed a line (answered, or not an object)."""
    lines, seen = [], set(already)
    for key, value in amendments(metadata).items():
        if key not in seen:
            line = relay_line(key, task_id, value)
            if line:
                lines.append(line)
            seen.add(key)
    for key, value in grants(metadata).items():
        if key not in seen:
            lines.append(grant_line(key, task_id, value))
            seen.add(key)
    for key in REJECTION_KEYS:
        record = metadata.get(key) if isinstance(metadata, dict) else None
        token = rejection_token(key, record)
        if not record or token in seen:
            continue
        line = rejection_line(key, task_id, record) if open_rejection(key, record, metadata) else None
        if line:
            lines.append(line)
        seen.add(token)
    return lines, seen


DISPOSE_HINT = ("Dispose of each amendment in your HANDOFF: handoff.amendments = "
                '{"amend_<n>": "applied: <where>" | "declined: <why>" | "disputed: <why>" | '
                '"partial: <what is left>" | "ack"}; '
                "also set handoff.rev for the audit trail (1 on the first write, previous + 1 after).")
