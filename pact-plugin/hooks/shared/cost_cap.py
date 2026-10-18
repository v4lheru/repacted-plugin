"""
Location: pact-plugin/hooks/shared/cost_cap.py
Summary: The session cost cap. The cap is the lower of the user's PACT_SESSION_COST_CAP_USD
         (shared/pact_config.get_usd) and the team policy's session.cost_cap_usd
         (shared/team_policy.session_cost_cap, tighten-only). When the session's estimated
         spend (shared/session_spend.py) is at or past the cap, a new dispatch (any agent type but
         dispatch_gate.COST_CAP_EXEMPT) is refused with rule dispatch_gate.session_cost_cap and a
         way through.
Used by: hooks/dispatch_gate.py (_apply_cost_cap, after rules 1-9) and bin/_cost_preview.py
         (resolve_cap, lead_transcript).
Doc: reference/config.md, section "Session cost cap".

FAIL-OPEN, LOUDLY. The cap is a budget control, not a security boundary. When the spend
cannot be read (no lead transcript, an unreadable file, a reader error) or the user's setting
cannot be read, the dispatch goes ahead and the lead is told once per session (on every
check when there is no session dir to record that it was told), in the dispatch's additional
context, and a cost_cap_note row goes to the session journal. Blocking
every dispatch on a measurement fault would cost more than a missed cap; saying nothing would
hide that the cap is off. A partial read (the byte budget ran out) is a lower bound: it still
refuses when that bound is at or past the cap, and otherwise lets the next dispatch read on.
The read keeps its place in the session dir, or with no session dir in the user's state dir
(_cache_path), so a later dispatch always continues the read.

Example:
    verdict = check(input_data, session_dir, cwd)
    if verdict.action == "deny": refuse with verdict.message
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from shared import pact_config, plugin_paths, session_spend, team_policy

CAP_OPTION = "PACT_SESSION_COST_CAP_USD"
RULE_ID = "dispatch_gate.session_cost_cap"
NOTE_ID = "cost_cap_unenforced"           # the journal id of a note, never a refusal
NOTES_FILE = "cost-cap-notes.json"        # the note kinds already shown in this session
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
ONCE = " (Shown once per session.)"


@dataclass
class Verdict:
    """allow (message empty), deny (message is the refusal) or note (message is shown)."""
    action: str = "allow"
    message: str = ""
    kind: str = ""


def resolve_cap(cwd: Optional[str] = None) -> Tuple[Optional[float], str, str]:
    """(cap in dollars or None, who set it, the problem with the user's setting or "")."""
    user, problem = pact_config.get_usd(CAP_OPTION)
    cap, source = team_policy.session_cost_cap(user, cwd)
    return cap, source, problem


def lead_transcript(input_data: dict) -> Tuple[Optional[Path], str]:
    """(the lead transcript of the hook's session, "") or (None, why not). The hook's
    transcript_path is used when it is that session's own file, else
    <config>/projects/*/<session id>.jsonl."""
    session = input_data.get("session_id") if isinstance(input_data, dict) else None
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        return None, "the hook input names no session id"
    given = input_data.get("transcript_path")
    if isinstance(given, str) and given:
        path = Path(given).expanduser()
        if path.name == f"{session}.jsonl" and path.is_file():
            return path, ""
    try:
        hits = sorted((plugin_paths.claude_config_dir() / "projects").glob(f"*/{session}.jsonl"))
    except OSError:
        hits = []
    return (hits[0], "") if hits else (None, "no transcript of this session was found")


def _noted(session_dir: str, kind: str) -> bool:
    """True when `kind` was already shown in this session; otherwise record it and return
    False. With no session dir there is nowhere to record it, so it is shown each time."""
    if not session_dir:
        return False
    path = Path(session_dir) / NOTES_FILE
    try:
        kinds = json.loads(path.read_text(encoding="utf-8")).get("kinds")
        kinds = [k for k in kinds if isinstance(k, str)] if isinstance(kinds, list) else []
    except (OSError, ValueError, AttributeError):
        kinds = []
    if kind in kinds:
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{NOTES_FILE}.{os.getpid()}.tmp")
        temp.write_text(json.dumps({"kinds": kinds + [kind]}), encoding="utf-8")
        os.replace(temp, path)
    except OSError:
        pass
    return False


def _journal_note(kind: str, message: str, session_dir: str) -> None:
    try:
        from shared.session_journal import append_event_checked, make_event
        append_event_checked(make_event("cost_cap_note", kind=kind, rule=NOTE_ID,
                                        reason=message[:400]),
                             "cost_cap_note", session_dir=session_dir or None)
    except Exception:  # noqa: BLE001 - the journal is best-effort; the note still shows
        pass


def note_once(kind: str, message: str, session_dir: str) -> Verdict:
    """A note shown the first time `kind` occurs in the session (and journaled), else allow."""
    if _noted(session_dir, kind):
        return Verdict()
    _journal_note(kind, message, session_dir)
    return Verdict("note", message + (ONCE if session_dir else ""), kind)


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _refusal(spend: session_spend.Spend, cap: float, source: str, cwd: Optional[str],
             extra: str = "") -> str:
    """The refusal sentence; the `[rule: ...]` tail stays last (hooks/dispatch_rules.py)."""
    bound = "at least " if spend.partial else "about "
    unknown = ""
    if spend.unknown_models:
        names = ", ".join(spend.unknown_models[:3])
        unknown = f"; models not in the price table ({names}) are priced at its highest rates"
    return (f"PACT dispatch_gate: this session has spent {bound}{_money(spend.usd)} "
            f"({session_spend.LABEL}; {spend.tokens:,} tokens over {spend.files} transcripts"
            f"{unknown}), at or past its cost cap of {_money(cap)} set by {source}, so no new "
            "specialist is spawned."
            + team_policy.refusal_note(("session.",), cwd)
            + extra
            + f" [rule: {RULE_ID}]")


def check(input_data: dict, session_dir: str, cwd: Optional[str] = None,
          byte_budget: int = session_spend.DEFAULT_BYTE_BUDGET) -> Verdict:
    """The cap's verdict on one specialist dispatch. Never raises: a fault is a note."""
    try:
        return _check(input_data, session_dir, cwd, byte_budget)
    except Exception as error:  # noqa: BLE001 - fail open, loudly (module docstring)
        return note_once("error", f"PACT cost cap: the cap could not be checked "
                         f"({type(error).__name__}), so this dispatch goes ahead.", session_dir)


def _check(input_data: dict, session_dir: str, cwd: Optional[str], byte_budget: int) -> Verdict:
    cap, source, problem = resolve_cap(cwd)
    pending = Verdict()
    if problem:
        fallback = (f"the team policy cap of {_money(cap)} applies" if cap is not None
                    else "no cap applies")
        pending = note_once("setting", f"PACT cost cap: {problem}, so it is ignored and "
                            f"{fallback}. Set it to a number of dollars, or unset it.", session_dir)
    if cap is None:
        return pending
    return _spend_verdict(input_data, session_dir, cwd, byte_budget, cap, source, pending)


def _cache_path(session_dir: str, input_data: dict) -> Optional[Path]:
    """Where the spend read keeps its place between checks: the session dir, or with none the
    user's state dir, ${XDG_STATE_HOME:-~/.local/state}/repacted/spend/<session id>.json, never
    the project. None when the session id is not one (each check then reads from the start, so
    a large spend stays a lower bound)."""
    if session_dir:
        return Path(session_dir) / session_spend.CACHE_FILE
    session = input_data.get("session_id") if isinstance(input_data, dict) else None
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        return None
    state = os.environ.get("XDG_STATE_HOME", "")
    if not os.path.isabs(state):
        state = str(Path.home() / ".local" / "state")
    folder = Path(state) / "repacted" / "spend"
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        return None
    return folder / f"{session}.json"


def _spend_verdict(input_data: dict, session_dir: str, cwd: Optional[str], byte_budget: int,
                   cap: float, source: str, pending: Verdict) -> Verdict:
    """Deny past the cap; otherwise the pending note, or a note for a spend that cannot be
    read (fully)."""
    lead, why = lead_transcript(input_data)
    cache = _cache_path(session_dir, input_data) if lead is not None else None
    try:
        if lead is None:
            raise session_spend.SpendUnreadable(why)
        spend = session_spend.session_spend(lead, cache, byte_budget)
    except session_spend.SpendUnreadable as error:
        return pending if pending.action == "note" else note_once(
            "unreadable", f"PACT cost cap: this session's spend could not be read ({error}), "
            f"so the {_money(cap)} cap set by {source} is not enforced; this dispatch goes "
            "ahead.", session_dir)
    if spend.usd >= cap:
        extra = f" {pending.message}" if pending.action == "note" else ""
        return Verdict("deny", _refusal(spend, cap, source, cwd, extra))
    if pending.action == "note" or not spend.unreadable:
        return pending
    return note_once("partial", f"PACT cost cap: {spend.unreadable} of this session's "
                     f"transcripts could not be read, so the spend of at least "
                     f"{_money(spend.usd)} against the {_money(cap)} cap is a lower bound.",
                     session_dir)
