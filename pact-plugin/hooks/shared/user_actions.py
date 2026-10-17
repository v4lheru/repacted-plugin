"""Open user-owned actions: the one predicate.

Location: pact-plugin/hooks/shared/user_actions.py. Stdlib only. The session
ledger (hooks/session_ledger.py) uses it now; D5 DOCTOR switches its own list to this module
at merge, so "what is waiting on the user" has one definition.

A task is an OPEN USER ACTION when it is not closed (completed or deleted) and either its
subject starts with ALERT, HALT, BLOCKER or RELEASE GATE as a whole word, or its
metadata.type is "algedonic".
"""

from __future__ import annotations

import re

CLOSED_STATUSES = frozenset({"completed", "deleted"})
SUBJECT_PREFIX = re.compile(r"^\s*(ALERT|HALT|BLOCKER|RELEASE GATE)\b")
SUBJECT_MAX = 120


def is_open_user_action(task) -> bool:
    if not isinstance(task, dict) or task.get("status") in CLOSED_STATUSES:
        return False
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    if metadata.get("type") == "algedonic":
        return True
    subject = task.get("subject")
    return isinstance(subject, str) and bool(SUBJECT_PREFIX.match(subject))


def open_user_actions(tasks) -> list:
    """[{id, subject}] for the open user actions among `tasks`, in the given order."""
    return [{"id": str(t.get("id")), "subject": str(t.get("subject") or "")[:SUBJECT_MAX]}
            for t in tasks or [] if is_open_user_action(t)]
