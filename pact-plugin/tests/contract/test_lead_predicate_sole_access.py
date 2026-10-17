"""tests/contract/test_lead_predicate_sole_access.py: no gate branches on the pure
`pact_context.is_lead` (agent_type only). A gate's lead decision goes through shared.actor
(the predicates) or shared.frame.is_lead_frame (the two-leg predicate under it), so a lead
launched without --agent is the lead everywhere a gate asks.

The walk is an AST walk over every `hooks/*_gate.py` and `hooks/shared/turn_end_gate.py`,
counting Call nodes on a name or attribute `is_lead` (docstrings and comments cannot match).
ALLOWED is the shrinking allowlist: the two bootstrap gates key on the harness agent_type
by design (a session is driven into bootstrap only when the harness says it is the lead;
tests/test_session_init_primary_frame_keeps_the_ladder.py pins the SessionStart ladder).
"""

from __future__ import annotations

import ast
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
ALLOWED = {"hooks/bootstrap_gate.py": 2, "hooks/bootstrap_prompt_gate.py": 1}


def _gate_files() -> list[Path]:
    hooks = PLUGIN_ROOT / "hooks"
    return sorted(hooks.glob("*_gate.py")) + [hooks / "shared" / "turn_end_gate.py"]


def _pure_is_lead_calls(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name == "is_lead":
            count += 1
    return count


def test_gates_do_not_call_the_pure_lead_predicate():
    found = {}
    for path in _gate_files():
        count = _pure_is_lead_calls(path)
        if count:
            found[path.relative_to(PLUGIN_ROOT).as_posix()] = count
    assert found == ALLOWED


def test_the_allowlist_holds_no_stale_entry():
    for rel, count in ALLOWED.items():
        assert _pure_is_lead_calls(PLUGIN_ROOT / rel) == count, rel
