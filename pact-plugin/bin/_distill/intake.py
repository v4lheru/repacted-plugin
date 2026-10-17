"""What happens to a backend result before anything is kept: schema, post-check, spool, KB-GATE.

Location: pact-plugin/bin/_distill/intake.py. bin/repact-distill calls take() once per
chunk the backend answered, with the REDACTED chunk (the text the model saw) and the raw one: a
`--- ref` header present in both is the provenance a candidate may cite.

Order, each step fail closed:
  1. schema.validate_output(): an invalid candidate is dropped and counted by reason;
  2. postcheck.filter_candidates(): `refuse` drops, `hold` forces review (never auto-accepted;
     the verdict and its reasons are sealed into the queue item, and only the user accepts it);
  3. the run spool gets every kept candidate with its verdict, before any proposal;
  4. KB-GATE: propose() as proposer id `distill` (hold, `inferred` or --no-auto-accept never
     auto-accept), or with --no-propose the gate's read-only check(), counted would-queue or
     would-refuse. Nothing here prints or returns candidate text; counts are kind names.
"""
from __future__ import annotations

import datetime
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

from _distill import ledger as L
from _distill import schema

ACCEPT, HOLD, REFUSE = "accept", "hold", "refuse"     # postcheck's verdicts (loaded lazily)

PROPOSER_ID = "distill"
CTX = "distill"          # the lesson's provenance tag: "(from distill, <date>)"
EVIDENCE_CLASS = {"verified": "observed", "stated": "documented", "inferred": "inferred"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Tally:
    """Counts for the run report (kind names only)."""
    valid: int = 0
    invalid: Counter = field(default_factory=Counter)      # reason -> candidates
    verdicts: Counter = field(default_factory=Counter)     # accept | hold | refuse
    reasons: Counter = field(default_factory=Counter)      # post-check reason -> candidates
    gate: Counter = field(default_factory=Counter)         # queued | would-queue | refused: <check>


@dataclass(frozen=True)
class GateMode:
    """How kept candidates reach KB-GATE. vault None = the KB is off: spool only."""
    vault: object = None
    propose: bool = True
    no_auto: bool = False


def to_proposal(candidate: dict, today: str) -> dict:
    """A validated candidate as a KB-GATE proposal (section 4.2 grammar). The evidence stays a
    pointer list; the date is the newest evidence date from the chunk headers."""
    refs = ", ".join(item["ref"] for item in candidate["evidence"])
    versions = candidate["versions"]
    dates = sorted(item["ts"][:10] for item in candidate["evidence"] if DATE_RE.match(item["ts"][:10]))
    how = f"pointers: {refs}" + (f"; versions: {', '.join(versions)}" if versions else "")
    return {"title": candidate["title"], "claim": candidate["claim"], "why": candidate["why"],
            "apply": candidate["apply"], "summary": candidate["summary"],
            "evidence": {"class": EVIDENCE_CLASS[candidate["confidence"]], "how": how},
            "ctx": CTX, "date": dates[-1] if dates else today, "kind": candidate["kind"],
            "keys": list(candidate["applies_to"]), "links": []}


def _gate():
    from _kb import gate      # lazy: a dry run and a spool-only run never load vault-note
    return gate


def _to_gate(proposal: dict, verdict, confidence: str, mode: GateMode, tally: Tally) -> None:
    """`verdict` is the post-check Verdict; it is sealed into the queue item with its reasons, so
    the card shows why an item was held and the drain leaves a held item to the user."""
    G = _gate()
    try:
        if mode.propose:
            no_auto = mode.no_auto or verdict.action == HOLD or confidence == "inferred"
            result = G.propose(mode.vault, proposal, proposer_id=PROPOSER_ID, no_auto=no_auto,
                               postcheck={"action": verdict.action, "reasons": list(verdict.reasons)})
            tally.gate[result["status"]] += 1
        else:
            G.check_d25_raw(proposal)
            G.check(G.normalise_proposal(proposal), mode.vault)
            tally.gate["would-queue"] += 1
    except G.Refusal as exc:
        prefix = "refused" if mode.propose else "would-refuse"
        tally.gate[f"{prefix}: {exc.check}"] += 1
    except Exception as exc:  # a gate error is counted, never a pass
        tally.gate[f"gate error ({type(exc).__name__})"] += 1


def _filter(candidates: list) -> list:
    from _distill import postcheck      # lazy: a dry run never loads the redaction layer
    return postcheck.filter_candidates(candidates)


def take(result, chunk, state_dir, run_id: str, mode: GateMode, tally: Tally,
         checker: Callable = _filter, today: Optional[str] = None, *, raw) -> int:
    """Run one ChunkResult through the steps above; return how many candidates were kept.
    `raw` is the chunk before redaction: a ref must be a header of both (schema.chunk_refs)."""
    valid, invalid = schema.validate_output({"candidates": result.candidates}, chunk.text, raw.text)
    tally.valid += len(valid)
    tally.invalid.update(invalid)
    kept = []
    for candidate, verdict in checker(valid):
        tally.verdicts[verdict.action] += 1
        tally.reasons.update(verdict.reasons)
        if verdict.action != REFUSE:
            kept.append((candidate, verdict))
    L.spool_candidates(state_dir, run_id, chunk.chunk_key, [c for c, _ in kept],
                       verdicts=[{"action": v.action, "reasons": list(v.reasons)} for _, v in kept])
    if mode.vault is None:
        tally.gate["kb off: spooled only"] += len(kept)
        return len(kept)
    today = today or datetime.date.today().isoformat()
    for candidate, verdict in kept:
        _to_gate(to_proposal(candidate, today), verdict, candidate["confidence"], mode, tally)
    return len(kept)
