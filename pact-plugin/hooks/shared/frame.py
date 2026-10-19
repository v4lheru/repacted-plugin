"""
Location: pact-plugin/hooks/shared/frame.py
Summary: One answer to "who is writing in this hook frame": lead or not, the
    acting member's name, the team, the session dir. Resolved once per
    dispatcher run and read by every check that needs it.
Used by: docker_lock_gate.py and background_test_gate.py; hooks/dispatch.py
    opens the memo. write_checks.py does not read it (its commit rule needs no
    actor).

THE LEAD PREDICATE. `is_lead_frame` is True for a frame whose `agent_type` is a
lead spelling (`pact_context.is_lead`, which stays pure), and for a frame with
NO `agent_type` whose `session_id` is the team's `leadSessionId`: a lead
launched without `--agent`. An in-process teammate shares the lead's session id
but always carries an `agent_type` (its own name), and an Agent-tool subagent
carries its type, so neither passes the second leg. A present `agent_type` of
any other value, string or not, is never a lead. Coordination, not
authorization: every frame runs as the same OS user.

THE MEMO. hooks/dispatch.py runs each check with the plugin modules it imported
dropped afterwards, so a module-level cache here would die with each check.
The memo therefore lives in `sys.modules[MEMO_MODULE]`, a module object with no
`__file__` that the dispatcher creates before its check loop and removes after
it (dispatch.py does not import this file: importing `shared` would keep the
package's dozen modules alive across checks). With no memo present (a check
run on its own, a test) every call resolves afresh. A Frame holds identity
only, never task contents: a check may rewrite a task file mid-run.

Never raises: each property falls back to "" / False / None.
"""

from __future__ import annotations

import os
import sys
from functools import cached_property
from typing import Any

# The name dispatch.py gives the memo module. tests/test_frame_identity.py
# asserts that dispatch.py spells it the same way.
MEMO_MODULE = "pact_frame_memo"

# The payload fields (and the one env var pact_context.init reads) that decide
# every property below. Two payloads equal on all of them get one Frame.
_KEY_FIELDS = (
    "hook_event_name", "session_id", "agent_id", "agent_type", "agent_name",
    "teammate_name", "team_name", "cwd",
)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


class Frame:
    """The identity of one hook frame. Properties resolve lazily, once each."""

    def __init__(self, payload: Any):
        self.payload = payload if isinstance(payload, dict) else {}

    @cached_property
    def session_id(self) -> str:
        return _text(self.payload.get("session_id"))

    @cached_property
    def agent_type(self) -> str:
        """The raw `agent_type` when it is a string, else ""."""
        return _text(self.payload.get("agent_type"))

    @cached_property
    def has_agent_type(self) -> bool:
        """Whether the frame carries any `agent_type` at all (a non-string counts)."""
        return self.payload.get("agent_type") not in (None, "")

    @cached_property
    def team(self) -> str:
        """The session's team: `pact_context.get_team_name()` after `init`."""
        try:
            from . import pact_context

            pact_context.init(self.payload)
            return pact_context.get_team_name() or ""
        except Exception:
            return ""

    @cached_property
    def context_team(self) -> str:
        """The session context's persisted team_name as written, NOT aligned or
        lowercased (journal_emitter's reading); `team` is the aligned one."""
        try:
            from . import pact_context

            pact_context.init(self.payload)
            team = pact_context.get_pact_context().get("team_name", "")
            return team if isinstance(team, str) else ""
        except Exception:
            return ""

    def task_team(self, task_id: Any) -> str | None:
        """The team of this session that holds `task_id`'s file (shared/task_files'
        `resolve_team`, never another session's team), memoised per task id. Which
        file to read, not authority. None when no team of this session holds it."""
        key = task_id if isinstance(task_id, str) else repr(task_id)
        cache = self.__dict__.setdefault("_task_teams", {})
        if key not in cache:
            try:
                from shared import task_files   # a test patches resolve_team there

                cache[key] = task_files.resolve_team(self.payload.get("session_id"), task_id)
            except Exception:
                cache[key] = None
        return cache[key]

    @cached_property
    def session_dir(self) -> str:
        try:
            from . import pact_context

            pact_context.init(self.payload)
            return pact_context.get_session_dir() or ""
        except Exception:
            return ""

    @cached_property
    def lead_session_id(self) -> str:
        try:
            from .pact_context import _read_lead_session_id

            return _read_lead_session_id(self.team) if self.team else ""
        except Exception:
            return ""

    @cached_property
    def is_lead(self) -> bool:
        try:
            from .pact_context import is_lead

            if is_lead(self.payload):
                return True
        except Exception:
            return False
        # The second leg needs no identity field at all: an in-process teammate always
        # carries its agent_type, and a harness-set agent_id names someone else.
        if self.has_agent_type or not self.session_id or _text(self.payload.get("agent_id")):
            return False
        if self.lead_session_id and self.session_id == self.lead_session_id:
            return True
        # After a resume the team config still names the old session (measured 2026-10-04),
        # so the plugin's own SessionStart record decides: session_init persists a team for a
        # session only when it is that team's lead (sessionstart_team_is_own, the resume
        # census), and the record sits in the session's own directory.
        return bool(self.team) and self.session_id == self.context_session_id

    @cached_property
    def context_session_id(self) -> str:
        """The session_id session_init wrote into pact-session-context.json, or ""."""
        try:
            from . import pact_context

            pact_context.init(self.payload)
            return _text(pact_context.get_pact_context().get("session_id"))
        except Exception:
            return ""

    @cached_property
    def role(self) -> str:
        """lead, teammate (any other agent_type) or unknown (none and not the lead)."""
        if self.is_lead:
            return "lead"
        return "teammate" if self.has_agent_type else "unknown"

    @cached_property
    def actor(self) -> str | None:
        """The acting member's name from a harness-set `agent_id` only, for authority
        checks (self-completion). `name@team` gives `name`. An in-process teammate's
        `a<agent_type>-<16 hex>` gives `agent_type` when that names a member of the
        session's team. Anything else, including a bare `a<16 hex>` subagent id and
        every self-declared field, is None."""
        try:
            from .dispatch_helpers import trustworthy_actor_name

            return trustworthy_actor_name(self.payload, self.team)
        except Exception:
            return None

    @cached_property
    def agent_name(self) -> str:
        """The display name (`pact_context.resolve_agent_name`); self-declared, never
        an authority signal."""
        try:
            from .pact_context import resolve_agent_name

            return resolve_agent_name(self.payload, self.team or None)
        except Exception:
            return ""

    @cached_property
    def registry_name(self) -> str:
        """`name@team` from the session registry for this session id, or ""."""
        if not self.session_id:
            return ""
        try:
            from .session_registry import resolve

            return resolve(self.session_id) or ""
        except Exception:
            return ""


def _key(payload: dict) -> tuple:
    return tuple(repr(payload.get(field)) for field in _KEY_FIELDS) + (
        os.environ.get("CLAUDE_PROJECT_DIR", ""),
    )


def resolve(payload: Any) -> Frame:
    """The Frame for `payload`: the memo's copy inside a dispatcher run, else a new one."""
    if not isinstance(payload, dict):
        return Frame({})
    memo = getattr(sys.modules.get(MEMO_MODULE), "frames", None)
    if not isinstance(memo, dict):
        return Frame(payload)
    key = _key(payload)
    frame = memo.get(key)
    if frame is None:
        frame = memo[key] = Frame(payload)
    return frame


def is_lead_frame(payload: Any) -> bool:
    """The shared lead predicate (see the module docstring)."""
    return resolve(payload).is_lead
