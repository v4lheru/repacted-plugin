"""The text the checks write: note and flag lines for the sender, the gate-note lines,
and the acceptance message the lead sees.

Location: pact-plugin/hooks/shared/evidence/notes.py, split out of the package's
__init__, which re-exports every name here; callers import shared.evidence.
"""
from __future__ import annotations

from typing import Optional

from shared.clock import utc_now_iso
from shared.scrub import scrub_text
from shared.evidence.claims import (DISPOSITION, claim_key, counts_sig, gated_fields, handoff_items,
                        uncertainty_items, unparsed_only)
from shared.evidence.ledger import _write_gate_state, load_task, read_gate_state, task_path
from shared.evidence.files import RED_FILE

NOTE_TEXT_CHARS = 60            # how much of an item's text a lead line quotes


def _flag(where: str, text: str, what: str) -> dict:
    """One flagged item: keyed on its claim, shown by its place and the start of its text. sig
    is its counts alone: the same counts at the same place are the same claim reworded."""
    shown = scrub_text(text)        # the key is the claim as written; what is printed is scrubbed
    return {"key": claim_key(text), "sig": counts_sig(text), "where": where, "text": shown[:NOTE_TEXT_CHARS],
            "line": f"{where}: {what}: {shown[:80]}"}


def _unread(item: dict) -> str:
    """'; <why a cited result file backs nothing>' for a return line, or ''."""
    return f"; {item['file_notes'][0]}" if item.get("file_notes") else ""


def _mismatch(item: dict) -> str:
    if item.get("stale"):
        return ("claims the counts of an older run: a later run of the same command in the same tree has other "
                "counts (MISMATCH: report the latest run, or cite the run you mean by its log)")
    return f"claims counts none of its cited or in-scope runs has (MISMATCH{_unread(item)})"


def _undecided(item: dict) -> str:
    return ("states a count the check cannot decide, not returned "
            f"(UNDECIDED: {'; '.join(item.get('file_notes', [])[:2]) or 'cited file not read'})")


def uncertainty_gate(handoff: dict) -> dict:
    """The uncertainty_disposition check for a HANDOFF about to be written: {"flagged"}.

    An item with no leading 'fixed:', 'known-limit:' or 'not-applicable:' is flagged. Flagged
    items are a NOTE since 1.4.1 U11, never a return (checks.json _notes has the measurement).
    A labelled item is DISPOSED: it is never noted as open.
    """
    flagged = [_flag(f"uncertainty[{idx}]", text, "no disposition")
               for idx, text in enumerate(uncertainty_items(handoff)) if not DISPOSITION.match(text)]
    return {"flagged": flagged}


def uncertainty_note(task_id: str, flagged: list[dict]) -> Optional[str]:
    """The writer's ONE line for undisposed uncertainty items of a HANDOFF that is stored anyway
    (1.4.1 U11): how many, where, and how to dispose of them. None when nothing was flagged.
    Places only, never item text, so the line stays one line whatever the items hold."""
    if not flagged:
        return None
    places = list(dict.fromkeys(str(f.get("where") or "?") for f in flagged))
    shown = ", ".join(places[:MAX_FLAG_LINES]) + (f", +{len(places) - MAX_FLAG_LINES} more"
                                                   if len(places) > MAX_FLAG_LINES else "")
    return (f"Note on #{task_id}: {len(flagged)} uncertainty item(s) with no disposition ({shown}). "
            "The HANDOFF is not returned for this; the lead reads these lines at acceptance. Open each "
            "line with 'fixed:', 'known-limit:' (written into the PR) or 'not-applicable:' in your next "
            "HANDOFF write.")


def set_gate_notes(team: Optional[str], task_id: str, use: str, notes: list[str]) -> None:
    """Record a use's notes for the lead's acceptance lines (gate_note_lines): the latest
    write's notes REPLACE this use's earlier ones, so a rewrite never prints an item twice
    (e1baab9f #7, #16): handoff_gate's flagged items and advisories, the undisposed uncertainty
    items and amendments. None of them is returned; they reach the lead here."""
    state = read_gate_state(team, task_id)
    kept = [n for n in state["notes"] if n.get("use") != use]
    if not notes and len(kept) == len(state["notes"]):
        return
    state["notes"] = kept + [{"ts": utc_now_iso(), "use": use, "line": n} for n in dict.fromkeys(notes)]
    try:
        _write_gate_state(team, task_id, state)
    except OSError:
        pass


def evidence_note(task_id: str, flagged: list[dict]) -> Optional[str]:
    """The writer's ONE line for the run-evidence items of a HANDOFF that is stored anyway
    (handoff_evidence is never returned since 1.4.1): how many, where, and that the
    lead reads them at acceptance. None when nothing was flagged. Places only, never item text,
    so the line stays one line whatever the items hold."""
    if not flagged:
        return None
    places = list(dict.fromkeys(str(f.get("where") or "?") for f in flagged))
    shown = ", ".join(places[:MAX_FLAG_LINES]) + (f", +{len(places) - MAX_FLAG_LINES} more"
                                                   if len(places) > MAX_FLAG_LINES else "")
    return (f"Note on #{task_id}: {len(flagged)} HANDOFF item(s) claim a run no recorded run backs "
            f"(NO-RUN, MISMATCH or EXIT: {shown}). The HANDOFF is not returned for this; the lead reads "
            "these lines at acceptance. To clear them, cite the tcount line or log path, or write 'not "
            "run', in your next HANDOFF write; do not re-run anything only to get a ledger entry.")


def _named(records: list[dict]) -> str:
    """'handoff:produced[0] "the start of its text" x3; ...': each item once, by its place and
    the start of its text, so the lead need not re-read the raw metadata. A record
    with no place (an amendment, a commit, an older state file) shows its key."""
    seen: dict[str, int] = {}
    for record in records:
        label = f"{str(record.get('use') or '').split('_')[0]}:{record.get('where') or record.get('key')}"
        if record.get("text"):
            label += f' "{record["text"][:NOTE_TEXT_CHARS]}"'
        seen[label] = seen.get(label, 0) + 1
    return "; ".join(label + (f" x{count}" if count > 1 else "") for label, count in seen.items())


HANDOFF_USES = ("handoff_evidence", "uncertainty_disposition")
# The gate-state notes key of handoff_gate's advisories, apart from the
# flagged run-evidence items under "handoff_evidence", so each list is replaced on its own.
HANDOFF_ADVISORY = "handoff_advisory"


def _on_disk(team: Optional[str], task_id: str) -> Optional[tuple[set[str], set[tuple[str, str]]]]:
    """(claim keys, (place, counts) pairs) of the HANDOFF in the task file now; None when the
    task file holds no HANDOFF to read."""
    task = load_task(task_path(team, task_id)) if team else None
    metadata = task.get("metadata") if task else None
    handoff = metadata.get("handoff") if isinstance(metadata, dict) else None
    if not isinstance(handoff, dict):
        return None
    placed = [(f"{item['field']}[{item['idx']}]", item["text"])
              for item in handoff_items(handoff, gated_fields(handoff))]
    placed += [(f"uncertainty[{idx}]", text) for idx, text in enumerate(uncertainty_items(handoff))]
    return {claim_key(text) for _, text in placed}, {(where, sig) for where, text in placed if (sig := counts_sig(text))}


def _standing(records: list[dict], disk: Optional[tuple[set[str], set[tuple[str, str]]]]) -> list[dict]:
    """The gate records the lead's line names. A returned HANDOFF item counts only while its
    claim is still in the HANDOFF on disk (the same text, or the same counts at the same place),
    and one record per field entry, the latest: a write that was returned and then fixed is not a
    finding, and three rewrites of one entry are one (the
    field line read 'returned 3 item(s)' for one integration[0] that was right on disk). With no
    HANDOFF on disk every record stands; a record with no place (an amendment, a commit, a
    teachback) always does."""
    kept: dict = {}
    for record in records:
        where = record.get("where")
        if disk is None or where is None or record.get("use") not in HANDOFF_USES:
            kept[id(record)] = record
        elif record.get("key") in disk[0] or (where, record.get("sig")) in disk[1]:
            kept[(record.get("use"), where)] = record
    return list(kept.values())


def gate_note_lines(team: Optional[str], task_id: str) -> list[str]:
    """The lead's one line per check outcome on a task: returns, repeats passed, notes."""
    state = read_gate_state(team, task_id)
    disk = _on_disk(team, task_id) if state["returns"] or state["repeats"] else None
    returns, repeats = _standing(state["returns"], disk), _standing(state["repeats"], disk)
    lines = []
    if returns:
        lines.append(f"Gate #{task_id}: returned {len(returns)} item(s) once ({_named(returns)})")
    if repeats:
        lines.append(f"Gate #{task_id}: {len(repeats)} repeat(s) passed on the return-once "
                     f"rail ({_named(repeats)}); accepting is your override")
    lines += [f"Note #{task_id}: {n['line']}" for n in state["notes"]]
    return lines


# ---------------------------------------------------------------------------
# Flag lines (the CLI prints them)
# ---------------------------------------------------------------------------

MAX_FLAG_LINES = 5
REASONS = {"MISMATCH": ("claims counts that differ from the recorded run", "cite the run (tcount output)"),
           "UNREADABLE": ("states a count that cannot be checked exactly", "write it in digits as tcount printed it"),
           "EXIT": ("claims a clean count over a failed run", "say what failed, or cite the run that exited 0"),
           "NO-FILE": ("cites a result file that does not exist", "cite the file that holds the counts"),
           "UNDECIDED": ("states a count the check cannot decide from what it cites",
                         "read the cited result yourself"),
           "NO-RUN": ("claims a run-time result with no recorded run", "cite the run or say not run"),
           "NO-COUNT": ("reports a test run with no count to check", "cite the tcount line"),
           "NO-LEDGER": ("claims a run-time result with no executed output", "cite the run or say not run")}
DEFAULT_REASON = ("claims a run-time result with no executed output", "cite the run or say not run")
# A NO-RUN whose runs ARE in the ledger, none with a parsed count: the run happened, its count
# cannot be checked. Said as such, with the log to read, never as "no recorded run", and to the
# LEAD only, every such item in one line (unparsed_line): the sender cannot fix it.
UNPARSED_REASON = ("run recorded, count not parsed", "check the counts against the logs")
UNPARSED_SHOWN = 3      # logs named per item (item_evidence keeps this many) and per grouped line


def _exited(item: dict) -> str:
    if not item.get("exited") and RED_FILE in (item.get("file_notes") or []):
        return RED_FILE
    return "run exited " + ", ".join(str(code) for code in item.get("exited") or ["non-zero"])


def unparsed_line(items: list[dict]) -> Optional[str]:
    """One line for every item whose only problem is a run recorded with no parsed count: how
    many claims, where, which runner, and the logs to read. None when there is no such item."""
    held = [item for item in items if unparsed_only(item)]
    if not held:
        return None
    where = ", ".join(f"{item['field']}[{item['idx']}]" for item in held[:MAX_FLAG_LINES])
    runners = sorted({runner for item in held for runner in item.get("unparsed_runners") or []})
    logs = list(dict.fromkeys(path for item in held for path in item["unparsed"]))
    return (f"{len(held)} claim(s) with a {UNPARSED_REASON[0]} ({where}{', ...' if len(held) > MAX_FLAG_LINES else ''}; "
            f"runner {', '.join(runners) or 'unknown'}; logs: {', '.join(logs[:UNPARSED_SHOWN])}"
            f"{f', +{len(logs) - UNPARSED_SHOWN} more' if len(logs) > UNPARSED_SHOWN else ''}); "
            f"not returned to the sender, {UNPARSED_REASON[1]}")


def handoff_flag_lines(verdict: dict) -> list[str]:
    """One line per flagged HANDOFF item, with the start of its text: 'Evidence #9: decisions[0]
    claims ...; cite the run or say not run: "ran: 36 passed"'. Every item whose only problem is
    an unparsed count shares ONE line, first (unparsed_line)."""
    flagged = [item for item in verdict.get("items", []) if item.get("flag")]
    grouped = unparsed_line(flagged)
    lines = [f"Evidence #{verdict['task_id']}: {grouped}"] if grouped else []
    for item in flagged:
        if unparsed_only(item):
            continue
        what, fix = REASONS.get(item.get("run_check"), DEFAULT_REASON)
        if item.get("run_check") == "EXIT":
            what = f"{what} ({_exited(item)})"
        elif item.get("run_check") == "MISMATCH" and item.get("stale"):
            what, fix = "claims the counts of an older run of the same command", "report the latest run or cite the log"
        elif item.get("missing") and item.get("run_check") != "MISMATCH":
            what, fix = f"cites a run the ledger does not hold ({', '.join(item['missing'][:3])})", \
                "cite the tcount log path"
        quoted = f': "{str(item["text"])[:NOTE_TEXT_CHARS]}"' if item.get("text") else ""
        lines.append(f"Evidence #{verdict['task_id']}: {item['field']}[{item['idx']}] {what}; {fix}{quoted}")
    return lines


CHECKS_TAIL = "These lines come from the write-time checks, which ask no model."


def _cap(lines: list[str], verdict: dict) -> Optional[str]:
    if not lines:
        return None
    shown, task_id = lines[:MAX_FLAG_LINES], verdict.get("task_id")
    if len(lines) > MAX_FLAG_LINES:
        shown.append(f"Evidence #{task_id}: +{len(lines) - MAX_FLAG_LINES} more "
                     f"(handoff-evidence handoff --team {verdict.get('team')} --task {task_id})")
    return "\n".join(shown)


def flag_message(verdict: dict) -> Optional[str]:
    """Every flag in a HANDOFF verdict (the CLI's view); None when none."""
    if not verdict or verdict.get("stop"):
        return None
    return _cap(handoff_flag_lines(verdict), verdict)


def acceptance_message(team: Optional[str], task_id: str) -> Optional[str]:
    """What the lead sees on accepting: one line per check outcome on the task (returns,
    repeats passed, notes), then CHECKS_TAIL; nothing at all when the checks found nothing."""
    found = gate_note_lines(team, task_id)
    return "\n".join([*found, CHECKS_TAIL]) if found else None
