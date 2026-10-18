"""Batch mode for repact-distill: one Message Batch per run, recorded before the wait.

Location: pact-plugin/bin/_distill/batchrun.py. bin/repact-distill calls execute()
instead of its per-chunk loop when the backend has `batch` set (backend_api.py, --batch).

  1. Collect first: every batch in batches.jsonl with no `closed` record is waited for and its
     results recorded, so a crashed or interrupted run is collected, never resubmitted. Its
     chunks are not sent again in this run.
  2. Select: walk the remaining chunks with a CapMeter on the ESTIMATE (batch price), redact
     each, and stop with `capped` at the first one a cap would break. The real cost is known only
     when the batch ends, so in batch mode the caps apply to the pre-submit estimate (the report
     says so on every batch run).
  3. Submit one batch with custom_id = chunk_key, append {batch_id, custom_ids} to batches.jsonl
     (0600) BEFORE the first poll, then collect it as in 1.
A succeeded result goes through the same parse, schema and intake path as a direct call; an
errored, expired or canceled one, or a missing one, is a `failed` row, which the next run tries
again. A result whose custom_id is not in the recorded batch, or repeats one, is refused and
counted. A batch still running after batch_max_wait_s stays open for the next run.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import _repact_common as common
from _distill import backend_api as A
from _distill import estimate as E
from _distill import intake as I
from _distill import ledger as L
from _distill.backend import BackendError

BATCH_FILE = "batches.jsonl"
CAPS_NOTE = ("batch mode: the caps apply to the pre-submit estimate; the real cost is known only "
             "when the batch ends")
CUSTOM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
RESULT_KINDS = ("succeeded", "errored", "expired", "canceled")


def record(state_dir: Path, event: dict) -> None:
    L.append_json_line(Path(state_dir) / BATCH_FILE, dict(event, at=common.utc_now_iso()))


def open_batches(state_dir: Path) -> list:
    """Submitted batches with no `closed` record, oldest first. A torn line is ignored."""
    try:
        lines = (Path(state_dir) / BATCH_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    submitted, closed = {}, set()
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or not isinstance(event.get("batch_id"), str):
            continue
        ids = event.get("custom_ids")
        if event.get("event") == "submitted" and isinstance(ids, list) \
                and all(isinstance(i, str) for i in ids):
            submitted.setdefault(event["batch_id"], event)
        elif event.get("event") == "closed":
            closed.add(event["batch_id"])
    return [event for batch_id, event in submitted.items() if batch_id not in closed]


@dataclass
class Run:
    settings: object
    table: object
    redactor: object
    backend: object
    state_dir: Path
    summary: object
    gate_mode: object
    row: Callable          # row(chunk, status, run_id, **counts) -> ledger row dict
    run_id: str = ""

    def put(self, chunk, status: str, failure: str = "", **counts) -> None:
        L.append_row(self.state_dir, self.row(chunk, status, self.run_id, **counts))
        self.summary.statuses[status] += 1
        if failure:
            self.count(failure)

    def count(self, reason: str) -> None:
        self.summary.failures[reason] = self.summary.failures.get(reason, 0) + 1

    def redact(self, chunk):
        try:
            return self.redactor.redact_chunk(chunk)
        except Exception:  # a detector that raises must skip, never send
            return None


def _wait(run: Run, batch_id: str) -> bool:
    deadline = time.monotonic() + run.backend.max_wait_s
    while run.backend.status(batch_id) != "ended":
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(run.backend.poll_s, left))
    return True


def _settle(run: Run, chunk, kind, message, clean) -> None:
    if kind != "succeeded":
        kind = kind if kind in RESULT_KINDS else "unknown"
        return run.put(chunk, L.STATUS_FAILED, f"backend: batch {kind}")
    clean = clean if clean is not None else run.redact(chunk)
    if clean is None:
        return run.put(chunk, L.STATUS_SKIPPED_REDACTION)
    try:
        candidates, tokens_in, tokens_out = A.parse_message(message)
    except BackendError as exc:
        return run.put(chunk, L.STATUS_FAILED, f"backend: {exc}")
    cost = E.cost_of(tokens_in, tokens_out, run.settings.model, run.table)
    cost = cost * run.backend.price_factor if cost is not None else None
    problem = E.usage_problem(cost, tokens_in, tokens_out)
    if problem:
        return run.put(chunk, L.STATUS_FAILED, f"backend: {problem}")
    result = E.ChunkResult(chunk.chunk_key, candidates, tokens_in, tokens_out, cost)
    kept = I.take(result, clean, run.state_dir, run.run_id, run.gate_mode, run.summary.tally,
                  raw=chunk)
    run.put(chunk, L.STATUS_DONE, candidates=kept, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_estimate=cost)


def collect(run: Run, batch: dict, by_key: dict, cleans: dict) -> None:
    """Wait for one recorded batch and record each of its results; close it when all are in."""
    batch_id, wanted, seen = batch["batch_id"], set(batch["custom_ids"]), set()
    try:
        if not _wait(run, batch_id):
            run.summary.notes.append(f"batch {batch_id} is still processing: run again to collect it")
            return
        for custom_id, kind, message in run.backend.results(batch_id):
            if custom_id not in wanted or custom_id in seen:
                run.count("batch: a result for a chunk not in this batch" if custom_id not in wanted
                          else "batch: a duplicate result")
                continue
            seen.add(custom_id)
            if custom_id in by_key:          # else done already or no longer a source
                _settle(run, by_key[custom_id], kind, message, cleans.get(custom_id))
    except BackendError as exc:              # the batch stays open: the next run collects it
        run.count(f"backend: {exc}")
        return
    for custom_id in sorted(wanted - seen):
        if custom_id in by_key:
            run.put(by_key[custom_id], L.STATUS_FAILED, "backend: batch no result")
    record(run.state_dir, {"event": "closed", "batch_id": batch_id})


def _select(run: Run, chunks: list) -> list:
    """(chunk, redacted chunk) pairs within the caps, on the estimate at the batch price."""
    settings, meter, picked = run.settings, E.CapMeter(run.settings.caps), []
    for index, chunk in enumerate(chunks):
        est_in, est_out, est_cost = E.chunk_estimate(chunk, settings.model, run.table,
                                                     settings.bytes_per_token,
                                                     settings.input_multiplier)
        est_cost = est_cost * run.backend.price_factor if est_cost is not None else None
        hit = meter.would_exceed(est_in + est_out, est_cost, chunk.session)
        if hit:
            run.put(chunk, L.STATUS_CAPPED, tokens_in=est_in, tokens_out=est_out,
                    cost_estimate=est_cost)
            summary = run.summary
            summary.cap_hit, summary.chunks_capped, summary.status = hit, len(chunks) - index, "capped"
            break
        if not CUSTOM_ID_RE.match(chunk.chunk_key):
            run.put(chunk, L.STATUS_FAILED, "batch: the chunk key is not a valid custom_id")
            continue
        clean = run.redact(chunk)
        if clean is None:
            run.put(chunk, L.STATUS_SKIPPED_REDACTION)
            continue
        meter.add(est_in + est_out, est_cost, chunk.session)
        picked.append((chunk, clean))
    return picked


def execute(run: Run, chunks: list) -> None:
    """Collect open batches, then submit and collect one new batch for the rest."""
    run.run_id = run.run_id or L.new_run_id()
    run.summary.notes.append(CAPS_NOTE)
    by_key = {chunk.chunk_key: chunk for chunk in chunks}
    held = set()
    for batch in open_batches(run.state_dir):
        held.update(batch["custom_ids"])
        collect(run, batch, by_key, {})
    picked = _select(run, [chunk for chunk in chunks if chunk.chunk_key not in held])
    if not picked:
        return
    try:
        batch_id = run.backend.submit([(chunk.chunk_key, clean.text) for chunk, clean in picked])
    except BackendError as exc:
        for chunk, _ in picked:
            run.put(chunk, L.STATUS_FAILED, f"backend: {exc}")
        return
    batch = {"event": "submitted", "batch_id": batch_id, "run_id": run.run_id,
             "model": run.settings.model, "custom_ids": [chunk.chunk_key for chunk, _ in picked]}
    record(run.state_dir, batch)
    collect(run, batch, by_key, {chunk.chunk_key: clean for chunk, clean in picked})
