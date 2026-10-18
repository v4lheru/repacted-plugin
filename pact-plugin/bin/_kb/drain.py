"""KB-GATE drain: `vault-note drain`, one decision at a time or a page of up to 20 in one pass.

Location: pact-plugin/bin/_kb/drain.py. Split from bin/vault-note for its byte ceiling;
vault-note's `drain` calls cmd_drain(). It imports gate, decide and views, never the other way round.

Batch mode (`drain --batch [--page N]`) prints one page of queue cards (the views.card of each
item, model-written text wrapped as there). `drain --batch --page N --decisions FILE --by WHO
[--accept-all]` decides that page: FILE (or `-` for stdin) is a JSON list of
{"id", "decision", "reason"?, "edit"?, "blind"?}. Every entry is checked before anything is
written (each id on that page as the queue stands now, no id twice, a known decision, a reason
with deferred, an edit object with edited); one bad entry refuses the whole batch. Each item is
then sealed by decide.decide(), the single-decision path, so the record, the bar bookkeeping and
the held-item rule are the same code: a held item is accepted or edited only when the user
retypes THAT item's id at a terminal, else that item alone is refused and stays queued.
`--accept-all` accepts the page's undecided items except held, inferred, forced-queue
(`--no-auto-accept`), unmarked machine-proposed and this-session items, which stay queued.
An interrupt stops between items: what was sealed stays sealed, the rest stays queued.
With no session id (the user's own terminal) the user confirms the page once by retyping its
number at an interactive terminal; without one the batch is refused before a write.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from _kb import decide as D
from _kb import gate as G
from _kb import views as V

PAGE_SIZE = 20
MAX_INPUT_BYTES = 1_000_000
ENTRY_KEYS = frozenset({"id", "decision", "reason", "edit", "blind"})


def page_items(vault: Path, page_no: int) -> tuple[list[dict], int]:
    """The items on page `page_no` (1-based) in queue order, and the number of pages."""
    items = G.pending(vault)
    pages = max(1, -(-len(items) // PAGE_SIZE))
    return items[(page_no - 1) * PAGE_SIZE:page_no * PAGE_SIZE], pages


def page_lines(vault: Path, page_no: int) -> list[str]:
    items, pages = page_items(vault, page_no)
    lines = [line for item in items for line in V.card(item)]
    held = sum(1 for item in items if D.held_reasons(item))
    lines.append(f"page {page_no} of {pages}: {len(items)} item(s)"
                 + (f", {held} held for the user" if held else "")
                 + (f"; decide them: vault-note drain --batch --page {page_no} --decisions FILE --by WHO"
                    " [--accept-all]" if items else ""))
    return lines


def accept_all_skip(item: dict) -> Optional[str]:
    """Why --accept-all leaves this item queued, or None when it may accept it. A machine-proposed
    item counts only when it was sealed with no_auto false (items queued before the mark carry none)."""
    if D.held_reasons(item):
        return "held"
    if (item.get("lesson") or {}).get("evidence", {}).get("class") == "inferred":
        return "inferred"
    if item.get("no_auto") is True or (G.proposer_class(item) and item.get("no_auto") is not False):
        return "no-auto-accept"
    if V.proposed_here(item):
        return "this session"
    return None


def parse_batch(raw: str, page_ids: list[str], by: str, accept_all: bool) -> list[dict]:
    """The checked entries of one batch. Any bad entry refuses the batch before a write; an entry
    is named by its position, never by echoing its text."""
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_INPUT_BYTES:
        raise G.Refusal("usage", f"the decisions file is larger than {MAX_INPUT_BYTES} bytes")
    try:
        entries = json.loads(raw)
    except ValueError:
        raise G.Refusal("usage", "the decisions file is not JSON: a list of {id, decision} objects")
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise G.Refusal("usage", "the decisions file is a JSON list of {id, decision} objects")
    if not entries and not accept_all:
        raise G.Refusal("usage", "no decisions: list some, or pass --accept-all")
    D.check_by(by)
    seen = set()
    for n, entry in enumerate(entries, 1):
        _check_entry(n, entry, page_ids, seen, by)
    return entries


def _check_entry(n: int, entry: dict, page_ids: list[str], seen: set, by: str) -> None:
    item_id, decision = entry.get("id"), entry.get("decision")
    if set(entry) - ENTRY_KEYS:
        raise G.Refusal("usage", f"entry {n}: unknown key(s); an entry has {', '.join(sorted(ENTRY_KEYS))}")
    if not isinstance(item_id, str) or not G.ID_RE.fullmatch(item_id):
        raise G.Refusal("usage", f"entry {n}: not a queue item id")
    if item_id not in page_ids:
        raise G.Refusal("usage", f"entry {n}: {item_id} is not on this page as the queue stands now; "
                        "list the page again")
    if item_id in seen:
        raise G.Refusal("usage", f"entry {n}: {item_id} is decided twice in this batch")
    seen.add(item_id)
    if not isinstance(decision, str) or decision not in G.DECISIONS:
        raise G.Refusal("usage", f"entry {n}: decision must be one of {', '.join(G.DECISIONS)}")
    blind = entry.get("blind")
    if blind is not None and not isinstance(blind, str):
        raise G.Refusal("usage", f"entry {n}: blind must be one of {', '.join(G.BLIND)}")
    D._check_args(item_id, decision, by, blind)
    if (decision == "deferred") != (entry.get("reason") is not None):
        raise G.Refusal("usage", f"entry {n}: deferred needs a reason, and only deferred takes one")
    D.check_reason(entry.get("reason"))
    if decision == "edited" and not isinstance(entry.get("edit"), dict):
        raise G.Refusal("usage", f"entry {n}: edited needs an edit object of corrected fields")
    if decision != "edited" and "edit" in entry:
        raise G.Refusal("usage", f"entry {n}: only edited takes an edit object")


def _confirm_page(page_no: int, count: int) -> bool:
    """With no session id the user confirms the page once at an interactive terminal (stdin and
    stderr): True then, False with a session id; refused otherwise. A held item still needs its
    own id retyped (decide._check_held)."""
    if G.caller()["session"]:
        return False
    prompt = (f"no Claude Code session id, so these decisions on page {page_no} ({count} item(s)) are "
              "taken from this terminal. Type the page number to confirm: ")
    if D.terminal_confirms(str(page_no), prompt):
        return True
    raise G.Refusal("decider", "with no session id (CLAUDE_CODE_SESSION_ID is not set) only the user "
                    "decides, at an interactive terminal: run the batch in your own terminal with "
                    "--decisions FILE (not -) and retype the page number when asked")


def run_batch(vault: Path, page_no: int, raw: str, by: str, accept_all: bool) -> dict:
    """Decide one page. Returns {"sealed": [(id, decision)], "refused": [(id, Refusal)],
    "left": [(id, why)], "interrupted": the id being decided when Ctrl-C came, or None}.
    decide() seals each item whole (decide.uninterrupted), so that item is either sealed or
    still queued, and every later item is still queued."""
    items, _pages = page_items(vault, page_no)
    entries = parse_batch(raw, [item["id"] for item in items], by, accept_all)
    page_confirmed = _confirm_page(page_no, len(items))
    plan = {entry["id"]: entry for entry in entries}
    out = {"sealed": [], "refused": [], "left": [], "interrupted": None}
    current = None
    try:
        for item in items:
            current, entry = item["id"], plan.get(item["id"])
            if entry is None:
                why = accept_all_skip(item) if accept_all else "no decision"
                if why:
                    out["left"].append((item["id"], why))
                    continue
                entry = {"id": item["id"], "decision": "accepted"}
            try:
                result = D.decide(vault, item["id"], entry["decision"], by, entry.get("blind"),
                                  entry.get("edit"), reason=entry.get("reason"),
                                  page_confirmed=page_confirmed)
                out["sealed"].append((result["id"], result["decision"]))
            except G.Refusal as refusal:
                out["refused"].append((item["id"], refusal))
    except KeyboardInterrupt:
        out["interrupted"] = current
    return out


def _report(out: dict) -> int:
    for item_id, decision in out["sealed"]:
        print(f"{item_id}: {decision}")
    for item_id, why in out["left"]:
        print(f"{item_id}: left queued ({why})")
    for item_id, refusal in out["refused"]:
        print(f"vault-note: {item_id} refused ({refusal.check}): {refusal}", file=sys.stderr)
    print(f"batch: {len(out['sealed'])} sealed, {len(out['refused'])} refused, {len(out['left'])} left queued"
          + (f"; interrupted at {out['interrupted']}: it is sealed or still queued, and the rest stay "
             "queued (vault-note queue)" if out["interrupted"] else ""))
    if out["interrupted"]:
        return 130
    return max((refusal.code for _id, refusal in out["refused"]), default=0)


def cmd_batch(args, vault: Path) -> int:
    page_no = args.page or 1
    if page_no < 1:
        print("vault-note: --page counts from 1", file=sys.stderr)
        return 2
    if not args.decisions:
        if args.accept_all:
            print("vault-note: --accept-all goes with --decisions FILE (an empty list [] is fine)", file=sys.stderr)
            return 2
        print("\n".join(page_lines(vault, page_no)))
        return 0
    try:
        raw = sys.stdin.read() if args.decisions == "-" else Path(args.decisions).read_text(encoding="utf-8")
        out = run_batch(vault, page_no, raw, args.by, args.accept_all)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"vault-note: cannot read --decisions ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    except G.Refusal as refusal:
        print(f"vault-note: refused ({refusal.check}): {refusal}; nothing decided", file=sys.stderr)
        return refusal.code
    return _report(out)


def cmd_drain(args, vault: Path) -> int:
    if args.blind_brief:
        print("\n".join(V.blind_brief(vault)))
        return 0
    if args.batch:
        return cmd_batch(args, vault)
    if args.page or args.decisions or args.accept_all:
        print("vault-note: --page, --decisions and --accept-all go with --batch", file=sys.stderr)
        return 2
    if not args.decide:
        print("\n".join(V.queue_lines(vault)))
        return 0
    item_id, decision = args.decide
    try:
        edit = json.loads(Path(args.edit).read_text(encoding="utf-8")) if args.edit else None
        result = D.decide(vault, item_id, decision, args.by, args.blind, edit, reason=args.reason)
    except (OSError, ValueError) as exc:
        print(f"vault-note: cannot read --edit ({exc.__class__.__name__})", file=sys.stderr)
        return 2
    except G.Refusal as refusal:
        print(f"vault-note: refused ({refusal.check}): {refusal}", file=sys.stderr)
        return refusal.code
    print(f"{result['id']}: {result['decision']}")
    return 0
