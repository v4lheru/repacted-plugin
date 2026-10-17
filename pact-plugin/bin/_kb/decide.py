"""KB-GATE decide: one named decision on one queued item, and the held-item rule.

Location: pact-plugin/bin/_kb/decide.py. Split from _kb/gate.py for its byte ceiling
(it imports gate, never the other way round); vault-note's `drain --decide` calls decide().

A HELD item is a distill item the post-check held (any reason: proper noun, imperative,
injection phrasing, redaction marker); its verdict is sealed into the item (`postcheck`). An
agent may reject or defer it. Accepting or editing it needs the user at an interactive
terminal who retypes the item id: an agent's shell has no terminal, so the drain refuses and
names the command for the user. There is no flag or variable that skips this. A held item's
decisions never count toward the distill auto-accept bar (gate.bar).
"""
from __future__ import annotations

import datetime
import json
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


def held_reasons(item: dict) -> Optional[list]:
    """The post-check hold reasons of a machine-proposed item, or None when it is not held."""
    verdict = item.get("postcheck") or {}
    if G.proposer_class(item) and verdict.get("action") == "hold":
        return [str(reason) for reason in verdict.get("reasons") or []] or ["held"]
    return None


def user_confirms(item_id: str, stream=None) -> bool:
    """True only when stdin is an interactive terminal and the user retypes the item id."""
    stream = stream or sys.stdin
    try:
        if not stream.isatty():
            return False
        print(f"{item_id} was held by the distill post-check. Type its id to confirm: ",
              end="", file=sys.stderr, flush=True)
        return stream.readline().strip() == item_id
    except (OSError, ValueError):
        return False


def _check_held(item: dict, item_id: str, decision: str) -> None:
    reasons = held_reasons(item)
    if reasons is None or decision not in NEEDS_USER or user_confirms(item_id):
        return
    edit = " --edit <file>" if decision == "edited" else ""
    raise G.Refusal("held", f"{item_id} was held by the distill post-check ({', '.join(reasons)}): an "
                    "agent may reject or defer it, and only the user accepts or edits it. Ask the user "
                    f"to run, in their own terminal: python3 \"{VAULT_NOTE}\" drain --decide {item_id} "
                    f"{decision} --by <their name>{edit} and retype the id when asked")


def _check_decider(item: dict, by: str, who: dict) -> None:
    """K2: a decision comes from a named decider in a session that has an id and did not propose
    the item (the lead's ruling: a later session judges; null never matches null)."""
    proposer = item.get("proposer") or {}
    if not who["session"]:
        raise G.Refusal("decider", "a decision needs a session id (CLAUDE_CODE_SESSION_ID is not set)")
    if proposer.get("session") and proposer["session"] == who["session"]:
        raise G.Refusal("decider", "this session proposed the item; decide it in a later session")
    if proposer.get("agent") and proposer["agent"] in (by, who["agent"]):
        raise G.Refusal("decider", "the proposing agent cannot decide its own item")


def _check_args(item_id: str, decision: str, by: str, blind: Optional[str]) -> str:
    if decision not in G.DECISIONS:
        raise G.Refusal("usage", f"decision must be one of {', '.join(G.DECISIONS)}")
    by = str(by or "").strip()
    if not by:
        raise G.Refusal("usage", "--by names who decided")
    if not G.DECIDER_RE.match(by):
        raise G.Refusal("usage", "--by takes a name: letters, digits and . _ @ - only")
    G.check_d25_raw(by, "--by")
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
    return dict(rebuilt, postcheck=item["postcheck"]) if "postcheck" in item else rebuilt


def decide(vault: Path, item_id: str, decision: str, by: str, blind: Optional[str] = None,
           edit: Optional[dict] = None, moment: Optional[datetime.datetime] = None) -> dict:
    moment = moment or G.now_utc()
    by = _check_args(item_id, decision, by, blind)
    path, item = _load(vault, item_id)
    who = G.caller()
    _check_decider(item, by, who)
    _check_held(item, item_id, decision)
    record = {"decision": decision, "by": by, "at": G._iso(moment), "blind": blind,
              "session": who["session"], "agent": who["agent"]}
    month = moment.strftime("%Y-%m")
    if decision == "deferred":
        item.setdefault("deferrals", []).append(record)
        G._write_json(G._queue_file(vault, G.DECIDED, month,
                                    f"{item_id}.deferred-{moment.strftime('%Y%m%dT%H%M%SZ')}.json"),
                      {"id": item_id, "action": item["action"], "decision": record, "deferral_of": item_id})
        G._write_json(path, item)
        return {"id": item_id, "decision": "deferred"}
    if decision == "edited":
        item = _edited(vault, item, edit, moment)
    done = dict(item, decision=record, applied=False)
    G._write_json(G._queue_file(vault, G.DECIDED, month, f"{item_id}.json"), done)
    path.unlink()
    return {"id": item_id, "decision": decision}
