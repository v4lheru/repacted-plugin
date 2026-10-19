"""
Location: pact-plugin/hooks/shared/ops/frame.py
Summary: Who may run which operation. An explicit Caller goes into
every op; `check` refuses the lead's verbs in a coder's frame and a submit by anyone but the
task's dispatched coder (or the SubagentStop backstop).
Used by: shared/ops/{dispatch,submit,accept,reject,amend}.py; bin/_repact_ops.py builds the
Caller with `from_cli`.

The CLI is a Bash child: it sees no hook payload, so it cannot learn its frame from the host.
`from_cli` is a SELF-CHECK only: `--as <name>` or REPACT_AGENT_NAME names a coder, otherwise the
caller is the lead. A coder that follows its brief is refused the lead's verbs; one that lies is
not. hooks/ops_frame_guard.py is the host-frame check: a PreToolUse Bash check that judges the
host payload's frame, never --as or REPACT_AGENT_NAME, and refuses the lead's verbs to an agent.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from typing import Optional

from . import OpError

LEAD, CODER, BACKSTOP = "lead", "coder", "backstop"
LEAD_VERBS = ("dispatch", "accept", "reject", "amend")
AGENT_ENV = "REPACT_AGENT_NAME"


@dataclass(frozen=True)
class Caller:
    kind: str          # lead | coder | backstop
    name: str = ""     # the coder's dispatched name; "" for the lead and the backstop


def from_cli(as_name: Optional[str]) -> Caller:
    """The CLI's caller: a coder when `--as` or REPACT_AGENT_NAME names one, else the lead."""
    name = (as_name or os.environ.get(AGENT_ENV) or "").strip()
    return Caller(CODER, name) if name else Caller(LEAD)


def _same_name(left: str, right: str) -> bool:
    fold = lambda text: unicodedata.normalize("NFKC", text).casefold().strip()
    return bool(left) and fold(left) == fold(right)


def check(op: str, caller: Caller, task: Optional[dict] = None) -> None:
    """Raise OpError when `caller` may not run `op` on `task` (the dispatched work task)."""
    if op in LEAD_VERBS:
        if caller.kind != LEAD:
            raise OpError("ops.lead_only", [f"`repact {op}` is the lead's; this caller is "
                                            f"{caller.kind} {caller.name!r}".rstrip()])
        return
    if op != "submit" or caller.kind == BACKSTOP:
        return
    dispatched = ((task or {}).get("metadata") or {}).get("dispatch") or {}
    owner = dispatched.get("name") if isinstance(dispatched.get("name"), str) else ""
    if caller.kind != CODER or not _same_name(caller.name, owner):
        who = "the lead" if caller.kind == LEAD else repr(caller.name)
        raise OpError("ops.not_owner", [f"task is dispatched to {owner!r}; {who} cannot hand in its work"])
