"""
Location: pact-plugin/hooks/shared/check_modes.py
Summary: The check modes and words of bin/checks.json (tightened by team policy), and the check
         log every check writes its returns and notes to (checks/shadow.jsonl). Moved out of
         the evidence package when 2.1.0 replaced it with shared/evidence_check.py.
Used by: hooks/write_checks.py (commit_rule), shared/evidence_check.py (handoff_evidence),
         shared/task_log/close_gate.py (shadow_log), tests/test_commit_rules.py,
         tests/test_team_policy.py and the export's checks override test.
"""
from __future__ import annotations

import json
import os
import sys

from shared import team_policy
from shared.amendments import ALIASES
from shared.clock import utc_now_iso
from shared.plugin_paths import checks_dir, plugin_root

CHECKS_FILE = plugin_root() / "bin" / "checks.json"
MODES = ("off", "shadow", "advisory", "gate")
# The shipped mode of each check read through use_mode (checks.json "modes"): used when the file
# cannot be read, is malformed, or names no mode this reader knows for the check.
DEFAULT_MODES = {"handoff_evidence": "gate", "commit_rule": "gate"}
_mode_noted: set = set()


def load_constants() -> dict:
    """bin/checks.json: the check modes and the commit rule's words, tightened by the work
    tree's team policy (shared.team_policy: a mode only toward blocking, deny words only added).
    {} when unreadable or not a JSON object and no policy applies."""
    try:
        constants = json.loads(CHECKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        constants = {}
    return team_policy.tighten_constants(constants if isinstance(constants, dict) else {})


def use_mode(use: str) -> str:
    """'off' | 'shadow' | 'advisory' | 'gate' for a checks.json modes key, read without regard
    to case ('enforce' reads 'gate', amendments.ALIASES read the other way round). When
    checks.json cannot be read, is malformed or names no known mode, the check's shipped mode
    (DEFAULT_MODES; 'shadow' for any other key) is used and one note line goes to stderr."""
    shipped = DEFAULT_MODES.get(use, "shadow")
    modes = load_constants().get("modes")
    mode = str(modes.get(use, "")).strip().lower() if isinstance(modes, dict) else ""
    mode = {canonical: alias for alias, canonical in ALIASES.items()}.get(mode, mode)
    if mode in MODES:
        return mode
    if use not in _mode_noted:
        _mode_noted.add(use)
        print(f"repacted: bin/checks.json gives no readable mode for {use}; using the shipped "
              f"mode '{shipped}'.", file=sys.stderr)
    return shipped


def shadow_log(event: dict) -> None:
    """One JSON line per check event (a return, a repeat, a note): repact-report counts them."""
    line = (json.dumps(dict({"ts": utc_now_iso()}, **event), ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode("utf-8")
    try:
        fd = os.open(checks_dir() / "shadow.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError:
        pass
