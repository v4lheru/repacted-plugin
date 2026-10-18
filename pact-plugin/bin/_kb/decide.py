"""KB-GATE decide: one named decision on one queued item, and the held-item rule.

Location: pact-plugin/bin/_kb/decide.py. Split from _kb/gate.py for its byte ceiling
(it imports gate, never the other way round); vault-note's `drain --decide` calls decide().

A HELD item is a distill item the post-check held (any reason: proper noun, imperative,
injection phrasing, redaction marker); its verdict is sealed into the item (`postcheck`). An
agent may reject or defer it. Accepting or editing it needs the user at an interactive
terminal (stdin and stderr) who retypes the item id: the terminal check stops an agent on the
ordinary path (the drain refuses and names the command for the user), not one that wraps the
command in a pseudo-terminal, which a guard rule refuses (process.pty_wrap, a brake on habit, not
a boundary). A held item's decisions never count toward the distill auto-accept bar (gate.bar).
An edit of a machine item is re-checked by the post-check and its new verdict sealed: a refused
edit decides nothing, and an edit that newly holds the item needs the user like any held item.
A decision with no session id (CLAUDE_CODE_SESSION_ID unset, as in the user's own terminal) is
the user's: it needs the same interactive terminal and the item id retyped, and its record
carries via "terminal". Without that the proposing session could drop its id to evade
the K2 same-session rule; one retype also covers a held item.
The seal stops sync clients and hand edits, not a process running as the user (the key is the
user's own file).
"""
from __future__ import annotations

import contextlib
import datetime
import json
import signal
import sys
from pathlib import Path
from typing import Optional

from _kb import gate as G

NEEDS_USER = ("accepted", "edited")
VAULT_NOTE = Path(__file__).resolve().parent.parent / "vault-note"
# A machine proposer's provenance stays as sealed through an edit: the distill tag drives the
# feedback filter and the recall origin mark, and the evidence class the never-auto rule. A human
# item's edit may still correct both (and is re-checked).
SEALED_LESSON_FIELDS = ("ctx",)
REASON_MAX = 200
VIA_TERMINAL = "terminal"     # a decided record's `via` when the user decided with no session id


@contextlib.contextmanager
def uninterrupted():
    """Hold SIGINT and SIGTERM while one decision is sealed: the decided record is written and the
    queue file removed as one step, and a held signal lands after it (POSIX; elsewhere a no-op)."""
    if not hasattr(signal, "pthread_sigmask"):
        yield
        return
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)


def check_reason(reason) -> Optional[str]:
    """A deferral's reason: one line of at most REASON_MAX characters, never a credential."""
    if reason is None:
        return None
    if not isinstance(reason, str) or not reason.split():
        raise G.Refusal("usage", "a reason is a non-empty line of text")
    reason = " ".join(reason.split())
    if len(reason) > REASON_MAX:
        raise G.Refusal("usage", f"a reason is at most {REASON_MAX} characters")
    G.check_d25_raw(reason, "the reason")
    return reason


def held_reasons(item: dict) -> Optional[list]:
    """The post-check hold reasons of a machine-proposed item, or None when it is not held."""
    verdict = item.get("postcheck") or {}
    if G.proposer_class(item) and verdict.get("action") == "hold":
        return [str(reason) for reason in verdict.get("reasons") or []] or ["held"]
    return None


def user_confirms(item_id: str, stream=None, prompt: Optional[str] = None) -> bool:
    """True only when stdin is an interactive terminal and the user retypes `item_id`."""
    stream = stream or sys.stdin
    try:
        if not stream.isatty():
            return False
        print(prompt or f"{item_id} was held by the distill post-check. Type its id to confirm: ",
              end="", file=sys.stderr, flush=True)
        return stream.readline().strip() == item_id
    except (OSError, ValueError):
        return False


# How a refusal says what terminal_confirms needs: the prompt goes to stderr, so a redirect fails it.
TERMINAL_STREAMS = "with stdin and stderr on the terminal (no 2>log, no 2>&1 | tee)"


def terminal_confirms(token: str, prompt: Optional[str] = None, stream=None) -> bool:
    """The user's-terminal check, for a held item and for a decision with no session id: stdin AND
    stderr (where the prompt goes) are interactive terminals and the user retypes `token`. An
    agent's shell has neither; a pseudo-terminal wrap is refused by a guard rule (process.pty_wrap),
    a brake on habit, not a boundary."""
    try:
        if not sys.stderr.isatty():
            return False
    except (OSError, ValueError):
        return False
    return user_confirms(token, stream, prompt)


def _check_held(item: dict, item_id: str, decision: str, retyped: bool = False) -> None:
    reasons = held_reasons(item)
    if reasons is None or decision not in NEEDS_USER or retyped or terminal_confirms(item_id):
        return
    edit = " --edit <file>" if decision == "edited" else ""
    raise G.Refusal("held", f"{item_id} was held by the distill post-check ({', '.join(reasons)}): an "
                    "agent may reject or defer it, and only the user accepts or edits it. Ask the user "
                    f"to run, in their own terminal: python3 \"{VAULT_NOTE}\" drain --decide {item_id} "
                    f"{decision} --by <their name>{edit} and retype the id when asked, {TERMINAL_STREAMS}")


def _check_decider(item: dict, item_id: str, decision: str, by: str, who: dict,
                   page_confirmed: bool = False) -> tuple[Optional[str], bool]:
    """K2: a decision comes from a named decider who did not propose the item: a session that has
    an id other than the proposer's (a later session judges; null never matches null), or, with no
    session id, the user at an interactive terminal (terminal_confirms; a batch page confirms once).
    Returns (the record's `via`, whether the user retyped this item's id)."""
    proposer = item.get("proposer") or {}
    if proposer.get("agent") and proposer["agent"] in (by, who["agent"]):
        raise G.Refusal("decider", "the proposing agent cannot decide its own item")
    if not who["session"]:
        if page_confirmed:
            return VIA_TERMINAL, False
        held = " was held by the distill post-check;" if held_reasons(item) else ":"
        prompt = (f"{item_id}{held} no Claude Code session id, so this decision ({decision}) is "
                  "taken from this terminal. Type the item id to confirm: ")
        if terminal_confirms(item_id, prompt):
            return VIA_TERMINAL, True
        raise G.Refusal("decider", "with no session id (CLAUDE_CODE_SESSION_ID is not set) only the user "
                        "decides, at an interactive terminal. Run in your own terminal: python3 "
                        f"\"{VAULT_NOTE}\" drain --decide {item_id} {decision} --by <your name> and "
                        f"retype the id when asked, {TERMINAL_STREAMS}")
    if proposer.get("session") and proposer["session"] == who["session"]:
        raise G.Refusal("decider", "this session proposed the item; decide it in a later session")
    return None, False


def check_by(by) -> str:
    """The decider's name, checked: letters, digits and . _ @ - only, never a credential."""
    by = str(by or "").strip()
    if not by:
        raise G.Refusal("usage", "--by names who decided")
    if not G.DECIDER_RE.match(by):
        raise G.Refusal("usage", "--by takes a name: letters, digits and . _ @ - only")
    G.check_d25_raw(by, "--by")
    return by


def _check_args(item_id: str, decision: str, by: str, blind: Optional[str]) -> str:
    if decision not in G.DECISIONS:
        raise G.Refusal("usage", f"decision must be one of {', '.join(G.DECISIONS)}")
    by = check_by(by)
    if blind is not None and blind not in G.BLIND:
        raise G.Refusal("usage", f"--blind must be one of {', '.join(G.BLIND)}")
    if not G.ID_RE.fullmatch(item_id):
        raise G.Refusal("usage", f"no queued item {item_id}")
    return by


def _load(vault: Path, item_id: str) -> tuple[Path, dict]:
    path = G._queue_file(vault, f"{item_id}.json", create=False)
    if not path.is_file():
        raise G.Refusal("usage", f"no queued item {item_id}")
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise G.Refusal("integrity", f"{item_id} is corrupt; nothing decided")
    if not G.verify_seal(item) or item.get("id") != item_id:
        raise G.Refusal("integrity", f"{item_id} was not written by the gate (its seal does not match); nothing decided")
    return path, item


def _edited(vault: Path, item: dict, edit, moment: datetime.datetime) -> dict:
    """The item rebuilt from the edit, re-checked like a new proposal. The proposer and the
    post-check verdict stay as sealed, and so do a machine item's ctx and evidence class (L5)."""
    if not isinstance(edit, dict):
        raise G.Refusal("usage", "edited needs --edit FILE with the corrected proposal")
    G.check_d25_raw(edit, "the edit")
    merged = dict(item["lesson"], **{k: v for k, v in edit.items() if k in item["lesson"]})
    p = G.normalise_proposal({**merged, **{k: v for k, v in item["page"].items()},
                              **{k: v for k, v in edit.items() if k in ("keys", "summary", "links", "kind", "topic")},
                              "proposer": item["proposer"], "supersedes": item.get("supersedes")})
    p["proposer"] = dict(item["proposer"])      # sealed: keeps a machine proposer's id and class
    if G.proposer_class(item):
        for name in SEALED_LESSON_FIELDS:
            p[name] = item["lesson"][name]
        p["evidence"]["class"] = item["lesson"]["evidence"]["class"]
    _catalog, routed, _notes = G.check(p, vault)
    rebuilt = dict(G.item_for(p, routed, moment, []), id=item["id"], created=item["created"])
    if "postcheck" not in item:
        return rebuilt
    verdict = _postcheck(rebuilt)
    if verdict.action == "refuse":
        raise G.Refusal("postcheck", f"the edit was refused by the distill post-check "
                        f"({', '.join(verdict.reasons)}); nothing decided")
    return dict(rebuilt, postcheck={"action": verdict.action, "reasons": list(verdict.reasons)})


def _postcheck(item: dict):
    """The R3 verdict on an edited machine item's text: the sealed verdict was for the
    text before the edit."""
    from _distill import postcheck      # lazy: only an edit of a machine item loads the redaction layer
    lesson, page = item["lesson"], item.get("page") or {}
    candidate = {name: lesson.get(name) for name in ("title", "claim", "why", "apply")}
    candidate["summary"] = page.get("summary") or ""
    [(_candidate, verdict)] = postcheck.filter_candidates([candidate])
    return verdict


def decide(vault: Path, item_id: str, decision: str, by: str, blind: Optional[str] = None,
           edit: Optional[dict] = None, moment: Optional[datetime.datetime] = None,
           reason: Optional[str] = None, page_confirmed: bool = False) -> dict:
    """Seal one decision. `reason` (deferred only) is kept in the deferral record.
    `page_confirmed`: drain's batch had the user confirm the page at a terminal (no session id)."""
    moment = moment or G.now_utc()
    by = _check_args(item_id, decision, by, blind)
    reason = check_reason(reason)
    if reason is not None and decision != "deferred":
        raise G.Refusal("usage", "a reason goes with deferred only")
    path, item = _load(vault, item_id)
    who = G.caller()
    via, retyped = _check_decider(item, item_id, decision, by, who, page_confirmed)
    _check_held(item, item_id, decision, retyped)
    record = {"decision": decision, "by": by, "at": G._iso(moment), "blind": blind,
              "session": who["session"], "agent": who["agent"], **({"via": via} if via else {}),
              **({"reason": reason} if reason else {})}
    month = moment.strftime("%Y-%m")
    if decision == "deferred":
        item.setdefault("deferrals", []).append(record)
        with uninterrupted():
            G._write_json(G._queue_file(vault, G.DECIDED, month,
                                        f"{item_id}.deferred-{moment.strftime('%Y%m%dT%H%M%SZ')}.json"),
                          {"id": item_id, "action": item["action"], "decision": record, "deferral_of": item_id})
            G._write_json(path, item)
        return {"id": item_id, "decision": "deferred"}
    if decision == "edited":
        was_held = held_reasons(item) is not None
        item = _edited(vault, item, edit, moment)
        if not was_held:
            _check_held(item, item_id, decision, retyped)     # the edit may have made it a held item
    done = dict(item, decision=record, applied=False)
    with uninterrupted():
        G._write_json(G._queue_file(vault, G.DECIDED, month, f"{item_id}.json"), done)
        path.unlink()
    return {"id": item_id, "decision": decision}
