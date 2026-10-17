"""The persona's Integrity row routes user decisions and irreversible-action approvals
through AskUserQuestion (#138 PERSONA P8: after the m5 rewrite the row only linked to the
approval rule, and the lead asked "please confirm" in plain text in 3 of 3 runs)."""

from pathlib import Path

PERSONA = Path(__file__).resolve().parents[1] / "agents" / "pact-orchestrator.md"


def _integrity_row() -> str:
    rows = [line for line in PERSONA.read_text(encoding="utf-8").splitlines()
            if line.startswith("| **Integrity** |")]
    assert len(rows) == 1
    return rows[0]


def test_integrity_row_names_askuserquestion_for_irreversible_approvals():
    row = _integrity_row()
    assert "`AskUserQuestion`" in row
    for action in ("merge", "force push", "branch deletion", "PR close"):
        assert action in row


def test_integrity_row_forbids_a_plain_text_question_for_a_user_decision():
    assert "plain text" in _integrity_row()


def test_integrity_row_keeps_the_users_own_turn_order_as_approval():
    """D5 (P9): the user's own-turn instruction for the action IS the approval. A first fix
    for P8 that sent every merge through AskUserQuestion dropped P9 from 1.00 to 0.00."""
    assert "own-turn order approves" in _integrity_row()


def test_integrity_row_says_the_guard_may_still_deny_after_an_own_turn_order():
    """The merge guard cannot read the user's turn: after an explicit order it can still deny,
    and the answer is one AskUserQuestion with the literal command, not a retry."""
    row = _integrity_row()
    assert "The merge guard cannot see the user's turn" in row
    assert "ask once with `AskUserQuestion` carrying the literal command" in row
