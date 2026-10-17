"""D7 PREMISE's existence lint, the deterministic half of D9's T4 brief moment.

Location: pact-plugin/bin/_recall/premise_lint.py. UNWIRED (lead ruling on #153,
amend_1): T4 itself is D9c, which wires this into dispatch.handle. It never denies: a brief
may name what it will create, so the output is advisory text only.

It extracts from a brief: repo-relative paths, `bin/<tool>` names, `~/.config/...` and
`~/.claude/...` paths, and `#NNN` task ids, then checks each exists.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Optional

REPO_PATH = re.compile(r"(?<![\w/~.-])((?:[\w.-]+/)+[\w.-]+\.[a-z0-9]{1,6})\b", re.I)
BIN_TOOL = re.compile(r"(?<![\w/-])bin/([a-z][a-z0-9_-]+)\b")
HOME_PATH = re.compile(r"(~/\.(?:config|claude)/[\w./-]+[\w-])")
TASK_ID = re.compile(r"(?<![\w&])#(\d{2,4})\b")


def extract(brief: str) -> dict[str, list[str]]:
    return {
        "paths": list(dict.fromkeys(m for m in REPO_PATH.findall(brief or "") if "://" not in m)),
        "bins": list(dict.fromkeys(BIN_TOOL.findall(brief or ""))),
        "home": list(dict.fromkeys(HOME_PATH.findall(brief or ""))),
        "tasks": list(dict.fromkeys(TASK_ID.findall(brief or ""))),
    }


def missing(brief: str, repo_root: Path, bin_dir: Path, home: Path,
            task_exists: Optional[Callable[[str], bool]] = None) -> list[str]:
    """The named things that do not exist, in the order the brief names them."""
    found = extract(brief)
    absent = [p for p in found["paths"] if not (repo_root / p).exists()]
    absent += [f"bin/{b}" for b in found["bins"] if not (bin_dir / b).exists()]
    absent += [h for h in found["home"] if not Path(os.path.expanduser(h.replace("~", str(home), 1))).exists()]
    if task_exists is not None:
        absent += [f"#{t}" for t in found["tasks"] if not task_exists(t)]
    return absent


def advisory(absent: list[str]) -> Optional[str]:
    if not absent:
        return None
    shown = ", ".join(absent[:5]) + (f" (+{len(absent) - 5} more)" if len(absent) > 5 else "")
    return f"brief names {len(absent)} thing(s) that do not exist: {shown} (fine if the task builds them)"
