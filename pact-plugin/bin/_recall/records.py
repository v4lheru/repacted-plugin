"""Project-records retriever (D9.3): rows of docs/decisions.md and docs/backlog.md in the
current repo. No labels exist for this store (D9a: none outside rePACT), so it ships in
shadow only until rePACT-era sessions supply moments.

Location: pact-plugin/bin/_recall/records.py. Read-only.
"""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Optional

from . import MAX_CANDIDATES, make_candidate, tokens

RECORD_FILES = ("docs/decisions.md", "docs/backlog.md")
ROW_ID = re.compile(r"^\|\s*([A-Z]?\d+[a-z]?)\s*\|")


def repo_root(cwd: Optional[str]) -> Optional[Path]:
    if not cwd:
        return None
    try:
        run = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(run.stdout.strip()) if run.returncode == 0 and run.stdout.strip() else None


def rows(root: Path) -> list[dict]:
    """One item per table row (keyed by its id cell) or top-level bullet."""
    items = []
    for rel in RECORD_FILES:
        try:
            lines = (root / rel).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            match = ROW_ID.match(line)
            if match:
                items.append({"id": f"{rel}#{match.group(1)}", "text": line})
            elif line.startswith("- ") and len(line) > 20:
                items.append({"id": f"{rel}#L{number}", "text": line})
    return items


def search(query: str, cwd: Optional[str], k: int = MAX_CANDIDATES) -> tuple[list[dict], list[str]]:
    root = repo_root(cwd)
    terms = set(tokens(query))
    if root is None or not terms:
        return [], []
    items = rows(root)
    bags = [(item, set(tokens(item["text"]))) for item in items]
    df = {t: sum(1 for _, bag in bags if t in bag) for t in terms}
    scored = sorted(((sum(math.log(1 + len(bags) / (1 + df[t])) for t in terms & bag), item)
                     for item, bag in bags if terms & bag), key=lambda s: (-s[0], s[1]["id"]))
    candidates, withheld = [], []
    for score, item in scored[:k]:
        cells = [c.strip() for c in item["text"].strip("|- ").split("|") if c.strip()]
        summary = max(cells, key=len) if cells else item["text"]
        cand, reason = make_candidate("records", item["id"], item["id"], summary, score, item["text"],
                                      f"records/{item['id']}")
        (candidates.append(cand) if cand else withheld.append(f"records:{item['id']}"))
    return candidates, withheld
