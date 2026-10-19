"""The feature records: `<main checkout>/.repacted/features.jsonl`.

Location: pact-plugin/hooks/shared/task_log/features.py. Written by shared/ops/integrate.py
when `repact accept --feature <name>` passes on the merged tree; read by integrate (a rerun on the
same tree writes nothing new), shared/ops/receipt.py (`repact inspect --feature`) and
shared/task_log/close_gate.py (a row of a feature closes only once the feature is integrated).

One JSON object per line: {feature, tree, path, head, tasks, statuses, runs, at}. Append-only; a
line that is not a JSON object is skipped. Stdlib only.

Example:
    append(main, {"feature": "search", "tree": "<tree id>", ...})
    integrated(main, "search")   # -> the newest record of "search", or None
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

FILE = Path(".repacted") / "features.jsonl"
READ_MAX = 4 * 1024 * 1024      # a larger file is read from its last READ_MAX bytes


def path(main) -> Path:
    return Path(main) / FILE


def read(main) -> List[dict]:
    """Every feature record in file order; [] with no file."""
    try:
        with open(path(main), "rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - READ_MAX))
            raw = handle.read()
    except OSError:
        return []
    records = []
    for line in raw.split(b"\n"):
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
        if isinstance(record, dict) and isinstance(record.get("feature"), str):
            records.append(record)
    return records


def integrated(main, feature: str) -> Optional[dict]:
    """The newest record of `feature`, or None when it was never integrated."""
    found = [record for record in read(main) if record.get("feature") == feature]
    return found[-1] if found else None


def append(main, record: dict) -> None:
    """Add one record (OSError when the file cannot be written)."""
    target = path(main)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(line)
