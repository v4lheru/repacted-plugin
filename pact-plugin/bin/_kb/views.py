"""KB-GATE drain views: the blind brief and the queue card (never lesson bodies beyond the
<= 5-line card).

Location: pact-plugin/bin/_kb/views.py. Used by bin/vault-note's `queue` and
`drain --blind-brief`; split from _kb/gate.py (which it reads, never the other way round).
A card names a machine proposer (`[proposer: distill]`) so a reviewer knows the lesson was
model-extracted, and shows that model-written text (title, claim, keys) only inside the data
wrapper (_kb/datawrap.py), as recall does; a human item's card is unchanged.
"""
from __future__ import annotations

from pathlib import Path

from _kb import datawrap as W
from _kb import gate as G
from _kb.decide import VAULT_NOTE, held_reasons

BLIND_QUESTION = "What is true here, and what should one do?"


def blind_brief(vault: Path) -> list[str]:
    """The situations for the drain's ONE blind answerer (contract section 7.4): each NEW item's
    title plus one fixed question, and nothing else. Never the claim, the notes, the target or the
    vault. An item is new until a blind verdict has been recorded for it (a deferral may carry one).
    Items this session proposed are left out: a later session decides them (K2).
    The trailing map lines tie Q-numbers back to queue ids for the drain; ids carry no lesson text."""
    fresh = [item for item in G.pending(vault)
             if (item.get("value") or {}).get("blind") == "pending"
             and not any(d.get("blind") for d in item.get("deferrals") or [])
             and not proposed_here(item)]
    if not fresh:
        return ["no new items: no blind check is needed this drain"]
    lines = [f"Q{n}. {shown(item, 'title', item['lesson']['title'])}. {BLIND_QUESTION}"
             for n, item in enumerate(fresh, 1)]
    return lines + ["", "# map for the drain (do not send): " + ", ".join(
        f"Q{n}={item['id']}" for n, item in enumerate(fresh, 1))]


def proposed_here(item: dict) -> bool:
    """True when this session proposed the item, so only a later session can decide it."""
    session = (item.get("proposer") or {}).get("session")
    return bool(session) and session == G.caller()["session"]


def shown(item: dict, field: str, text) -> str:
    """A field of the item as the drain sees it: a machine proposer's text inside the data
    wrapper (its source is the queue id), a human proposer's text as written."""
    if not G.proposer_class(item):
        return str(text)
    return W.element(f"kb-queue/{item['id']}", field, W.DISTILL, text)


def card(item: dict) -> list[str]:
    lesson, routing = item["lesson"], item["routing"]
    checks = ", ".join(f"{k} {v}" for k, v in item["checks"].items())
    marks = ("  [audit sample]" if item.get("audit_sample") else "") \
        + ("  [this session: decide later]" if proposed_here(item) else "") \
        + (f"  [proposer: {G.proposer_class(item)}]" if G.proposer_class(item) else "") \
        + (f"  [held: {', '.join(held_reasons(item))}; the user decides]" if held_reasons(item) else "")
    keys = routing.get("key_matches")[:3]
    lines = [f"{item['id']}  {item['action']} -> {item['target']}{marks}",
             f"  {shown(item, 'title', lesson['title'])} ({lesson['evidence']['class']}, {lesson['ctx']}, {lesson['date']})",
             f"  claim: {shown(item, 'claim', lesson['claim'][:200])}",
             f"  routing: keys {shown(item, 'keys', ', '.join(keys)) if G.proposer_class(item) else keys}; "
             f"lesson {routing.get('lesson_hits')}"
             + (f"; not its topic {routing['topic_mismatch']}" if routing.get("topic_mismatch") else ""),
             f"  checks: {checks}"]
    return lines[:5]


def queue_lines(vault: Path) -> list[str]:
    """`vault-note queue`: every card, the corrupt files, then one summary line with the bars, and
    one line for the user when held distill items wait."""
    items = G.pending(vault)
    lines = [line for item in items for line in card(item)]
    health = G.queue_health(vault)
    lines += [f"CORRUPT {name}  (unreadable, or not written by the gate: inspect it; it is never decided or applied)"
              for name in health["corrupt_files"]]
    bars = "; ".join(f"{who + ' bar' if who else 'bar'}: " + ", ".join(
        f"{a} {b['accepted_unedited']}/{b['decisions']}{' AUTO' if b['on'] else ''}"
        for a, b in ((a, G.bar(vault, a, who)) for a in G.ACTIONS)) for who in (None, *G.PROPOSER_IDS))
    lines.append(f"queue: {health['count']} item(s), oldest {health['oldest_days']} day(s)"
                 + (f", {health['corrupt']} CORRUPT" if health["corrupt"] else "") + f"; {bars}")
    held = [item["id"] for item in items if held_reasons(item)]
    if held:
        lines.append(f"held for the user: {len(held)} distill item(s) ({', '.join(held[:3])}"
                     f"{', ...' if len(held) > 3 else ''}). An agent may reject or defer them; to accept or "
                     f"edit, the user runs python3 \"{VAULT_NOTE}\" queue in their own terminal")
    return lines
