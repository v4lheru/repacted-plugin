"""
Location: pact-plugin/hooks/shared/payload_digest.py
Summary: THE digest of a stored metadata.teachback_submit or metadata.handoff (1.5.0, D2).
         The writer's stored line prints it (stored_line.after_write, from the task file
         read back), the teammate's notify carries it in place of the verbatim payload, and
         `repact inspect <id> --key` prints it beside the object it reads for the lead. One function
         on both sides, so the two can only disagree when the stored object changed.
Used by: hooks/shared/stored_line.py, bin/_front/payload.py; stored_teachback by
         hooks/shared/workflow_state.py, hooks/shared/task_migration.py, hooks/teammate_idle.py
         and bin/_front/payload.py.

digest = the first 12 hex characters of sha256 over canonical_json.canonical_bytes of the
object with every null-valued key dropped. canonical_bytes sorts keys, so key order never
changes the digest; nulls are dropped because a key written as null and a key the store
dropped are the same write (stored_line._without_nulls). 48 bits: an identity check between
two reads of one task, not a security boundary against a chosen collision.
Pure: stdlib and the canonical_json leaf only, no I/O; never raises.
"""

from __future__ import annotations

import hashlib

from shared.canonical_json import canonical_bytes

DIGEST_LEN = 12


def without_nulls(value):
    """value with every null-valued object key dropped, at any depth."""
    if isinstance(value, dict):
        return {key: without_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [without_nulls(item) for item in value]
    return value


def stored_teachback(metadata):
    """The teammate's stored teachback, or None. `repact submit` writes it under
    metadata.teachback.submit (2.1.0); a live Agent Teams writer still stores the flat
    metadata.teachback_submit, read second."""
    if not isinstance(metadata, dict):
        return None
    record = metadata.get("teachback")
    if isinstance(record, dict) and record.get("submit") is not None:
        return record["submit"]
    return metadata.get("teachback_submit")


def payload_digest(value) -> str | None:
    """The 12-hex digest of a stored object, or None when it cannot be serialised (a value
    that is not JSON, or nested past the serialiser's depth)."""
    try:
        data = canonical_bytes(without_nulls(value))
    except (TypeError, ValueError, RecursionError):
        return None
    return hashlib.sha256(data).hexdigest()[:DIGEST_LEN]
