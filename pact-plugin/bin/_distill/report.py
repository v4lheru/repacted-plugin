"""Plain-text and JSON reports for repact-distill: counts, tokens and cost only, never content.

Location: pact-plugin/bin/_distill/report.py. Used by bin/repact-distill to print the
dry run and the outcome of a real run. Project names are printed (they are the user's own, and
the dry run is where they decide what to exclude); source text, refs and lessons are not.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RunSummary:
    """What a run (or a dry run) found and did. Filled by bin/repact-distill."""
    mode: str                                  # "dry-run" | "run"
    files: Counter = field(default_factory=Counter)       # source kind -> files read
    segments: Counter = field(default_factory=Counter)    # source kind -> segments kept
    chunks_by_kind: Counter = field(default_factory=Counter)
    projects: Counter = field(default_factory=Counter)    # project -> chunks
    skipped: dict = field(default_factory=dict)           # reason -> count
    chunks_total: int = 0
    chunks_done_before: int = 0
    statuses: Counter = field(default_factory=Counter)    # ledger status -> chunks this run
    cap_hit: Optional[str] = None
    chunks_capped: int = 0
    status: str = "ok"                                    # "ok" | "capped"
    estimate: object = None                               # estimate.Estimate of the work left
    bytes_per_token: float = 0.0
    input_multiplier: float = 1.0
    caps: object = None
    denylist: Optional[str] = None                        # redact.denylist_status(), run only
    failures: dict = field(default_factory=dict)          # backend failure reason -> chunks
    tally: object = None                                  # intake.Tally, run only


def _counts(counts) -> str:
    return ", ".join(f"{key} {value}" for key, value in sorted(dict(counts).items())) or "none"


def _candidate_lines(summary: RunSummary) -> list:
    tally = summary.tally
    if summary.mode != "run" or tally is None:
        return []
    lines = [f"candidates: {tally.valid} valid, {sum(tally.invalid.values())} invalid "
             f"({_counts(tally.invalid)})",
             f"post-check: {_counts(tally.verdicts)}; reasons: {_counts(tally.reasons)}",
             f"kb-gate: {_counts(tally.gate)}"]
    if summary.failures:
        lines.append(f"backend failures: {_counts(summary.failures)}")
    return lines


def _cost_line(est) -> str:
    if est is None:
        return "cost: not computed"
    if est.cost is None:
        return f"cost: not computed ({est.note})"
    return (f"cost: {est.cost:.4f} {est.currency} for model {est.model} "
            f"(prices as of {est.price_asof})")


def _caps_line(caps) -> str:
    if caps is None:
        return "caps: none"
    parts = [f"{name} {getattr(caps, name)}" for name in ("usd", "tokens", "sessions")
             if getattr(caps, name) is not None]
    return "caps: " + (", ".join(parts) if parts else "none")


def render_text(summary: RunSummary) -> str:
    header = ("repact-distill dry run: nothing is sent and nothing is written"
              if summary.mode == "dry-run" else "repact-distill run")
    lines = [header, "sources:"]
    for kind in sorted(set(summary.files) | set(summary.segments)):
        lines.append(f"  {kind:<16} {summary.files[kind]:>5} files  "
                     f"{summary.segments[kind]:>6} segments  {summary.chunks_by_kind[kind]:>5} chunks")
    for reason, count in sorted(summary.skipped.items()):
        lines.append(f"  skipped: {reason} ({count})")
    if summary.projects:
        lines.append("projects: " + ", ".join(f"{name} ({n})" for name, n in
                                              sorted(summary.projects.items())))
    lines.append(f"chunks: {summary.chunks_total} ({summary.chunks_done_before} already done, "
                 f"{summary.chunks_total - summary.chunks_done_before} to run)")
    est = summary.estimate
    if est is not None:
        lines.append(f"tokens: ~{est.tokens_in:,} in, ~{est.tokens_out:,} out over {est.chunks} "
                     f"chunks (estimate: {summary.bytes_per_token} bytes/token plus a fixed "
                     f"overhead per call, times {summary.input_multiplier} for the JSON schema, "
                     f"measured on one call)")
    lines.append(_cost_line(est))
    lines.append(_caps_line(summary.caps))
    if summary.denylist is not None:
        lines.append(f"denylist: {summary.denylist}")
    if summary.statuses:
        lines.append("ledger: " + ", ".join(f"{k} {v}" for k, v in sorted(summary.statuses.items())))
    lines.extend(_candidate_lines(summary))
    if summary.cap_hit:
        verb = "would stop" if summary.mode == "dry-run" else "stopped"
        lines.append(f"capped: {verb} at the {summary.cap_hit} cap; {summary.chunks_capped} chunks "
                     f"left for a later run")
    lines.append(f"status: {summary.status}")
    return "\n".join(lines)


def _tally_json(tally) -> Optional[dict]:
    if tally is None:
        return None
    return {"valid": tally.valid, "invalid": dict(tally.invalid), "verdicts": dict(tally.verdicts),
            "reasons": dict(tally.reasons), "gate": dict(tally.gate)}


def render_json(summary: RunSummary) -> dict:
    est = summary.estimate
    return {"mode": summary.mode, "status": summary.status, "files": dict(summary.files),
            "segments": dict(summary.segments), "chunks_by_kind": dict(summary.chunks_by_kind),
            "projects": dict(summary.projects), "skipped": dict(summary.skipped),
            "chunks": summary.chunks_total, "chunks_done_before": summary.chunks_done_before,
            "statuses": dict(summary.statuses), "cap_hit": summary.cap_hit,
            "chunks_capped": summary.chunks_capped, "bytes_per_token": summary.bytes_per_token,
            "input_multiplier": summary.input_multiplier,
            "denylist": summary.denylist, "failures": dict(summary.failures),
            "candidates": _tally_json(summary.tally) if summary.mode == "run" else None,
            "estimate": None if est is None else {
                "chunks": est.chunks, "tokens_in": est.tokens_in, "tokens_out": est.tokens_out,
                "cost": est.cost, "currency": est.currency, "price_asof": est.price_asof,
                "model": est.model, "note": est.note}}
