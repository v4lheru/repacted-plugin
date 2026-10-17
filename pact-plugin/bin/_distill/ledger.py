"""The distiller's append-only ledger and candidate spool (KB lite key decision 12).

Location: pact-plugin/bin/_distill/ledger.py. Used by bin/repact-distill: done_keys()
lets a resumed run skip finished chunks, append_row() records every chunk attempt, and
spool_candidates() writes a chunk's candidates BEFORE its `done` row so a crash between the two
leaves the candidates on disk (design note §4.5).

Files live in the state dir, by default <plugin data dir>/distill/ (mode 0700, files 0600):
  ledger.jsonl                     one row per chunk attempt, the contract fields below
  runs/<run_id>/candidates.jsonl   {"chunk_key", "candidate"} per line
The ledger holds keys, counts and hashes only, never source text.
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Optional

import _repact_common as common

LEDGER_FILE = "ledger.jsonl"
FIELDS = ("chunk_key", "source", "view_sha256", "extractor_version", "detector_fingerprint",
          "backend", "model", "status", "candidates", "tokens_in", "tokens_out",
          "cost_estimate", "price_asof", "run_id", "at")
STATUS_DONE = "done"
STATUS_CAPPED = "capped"
STATUS_SKIPPED_REDACTION = "skipped_redaction"
STATUS_FAILED = "failed"
STATUSES = (STATUS_DONE, STATUS_SKIPPED_REDACTION, "skipped_lowsignal", STATUS_FAILED,
            STATUS_CAPPED)
DIR_MODE = 0o700
FILE_MODE = 0o600


def default_state_dir() -> Path:
    return common.data_subdir("distill", create=False)


def new_run_id() -> str:
    return common.utc_now_iso().replace(":", "").replace("-", "")[:15] + "-" + secrets.token_hex(3)


def make_row(chunk, status: str, run_id: str, *, extractor_version: str, backend: str = "",
             model: str = "", detector_fingerprint: Optional[str] = None, candidates: int = 0,
             tokens_in: int = 0, tokens_out: int = 0, cost_estimate: Optional[float] = None,
             price_asof: Optional[str] = None) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown ledger status {status!r}")
    return {"chunk_key": chunk.chunk_key, "source": chunk.source, "view_sha256": chunk.view_sha256,
            "extractor_version": extractor_version, "detector_fingerprint": detector_fingerprint,
            "backend": backend, "model": model, "status": status, "candidates": candidates,
            "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_estimate": cost_estimate,
            "price_asof": price_asof, "run_id": run_id, "at": common.utc_now_iso()}


def _make_dirs(directory: Path) -> None:
    """Create `directory` and any missing parent with DIR_MODE (mkdir's mode= sets the leaf only)."""
    missing = [d for d in (directory, *directory.parents) if not d.exists()]
    for d in reversed(missing):
        d.mkdir(mode=DIR_MODE, exist_ok=True)


def _append_line(path: Path, record: dict) -> None:
    _make_dirs(path.parent)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, FILE_MODE)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def append_row(state_dir: Path, row: dict) -> None:
    _append_line(Path(state_dir) / LEDGER_FILE, {key: row.get(key) for key in FIELDS})


def read_rows(state_dir: Path) -> list:
    """Every parseable ledger row; a torn or foreign line is ignored, not fatal."""
    try:
        text = (Path(state_dir) / LEDGER_FILE).read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("chunk_key"), str):
            rows.append(row)
    return rows


def done_keys(state_dir: Path) -> set:
    """Chunk keys with a `done` row: a resumed run skips these. Capped, failed and skipped
    chunks are tried again."""
    return {row["chunk_key"] for row in read_rows(state_dir) if row.get("status") == STATUS_DONE}


def spool_candidates(state_dir: Path, run_id: str, chunk_key: str, candidates: list,
                     verdicts: Optional[list] = None) -> None:
    """One line per candidate; with `verdicts` (the post-check's, same order) each line also
    carries {"verdict": {"action", "reasons"}}."""
    path = Path(state_dir) / "runs" / run_id / "candidates.jsonl"
    for index, candidate in enumerate(candidates):
        record = {"chunk_key": chunk_key, "candidate": candidate}
        if verdicts is not None:
            record["verdict"] = verdicts[index]
        _append_line(path, record)
