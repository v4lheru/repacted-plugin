"""The census instrument's own check, run against a SYNTHETIC corpus.

NO ARM HERE PINS A REAL-WORLD COUNT. The journal population includes the
handoffs written while measuring it -- it moved by 3 events between this
arc's own runs -- so any test asserting a number from the live roots rots by
the next session. These arms build a fixture corpus under tmp_path, where the
expected counts are known by construction, and assert the instrument reports
what was planted.

The one arm that touches the shipped code rather than the fixture is
test_key_set_reconstruction_is_faithful: it guards the adaptation the journal
arm depends on.
"""

from __future__ import annotations

import json
from pathlib import Path

import handoff_census as hc  # noqa: E402

CANONICAL = {
    "produced": ["a"], "decisions": ["b"], "reasoning_chain": "c",
    "uncertainty": [], "integration": [], "open_questions": [],
}
NO_REASONING = {k: v for k, v in CANONICAL.items() if k != "reasoning_chain"}
LEGACY = {**NO_REASONING, "key_decisions": ["x"]}
del LEGACY["decisions"]


def _corpus(home: Path) -> None:
    """Plant a two-root corpus with known-by-construction shapes."""
    journal = home / ".claude" / "pact-sessions" / "proj" / "sess"
    journal.mkdir(parents=True)
    events = [
        {"type": "agent_handoff", "handoff": list(CANONICAL)},
        {"type": "agent_handoff", "handoff": list(NO_REASONING)},
        {"type": "agent_handoff", "handoff": list(LEGACY)},
        # Skipped on the cheap substring pre-filter alone.
        {"type": "session_start", "handoff": ["wrong event type"]},
        # THE DECOY, and it is the one that matters. The reader's cheap
        # pre-filter tests for the QUOTED string "agent_handoff", so the decoy
        # must carry it as a VALUE -- a prose mention of agent_handoff does not
        # contain the quotes and is rejected one step earlier, by the filter
        # this row is not aiming at. Measured both ways: with the quoted form
        # below, deleting the type check reds this module; with an unquoted
        # prose mention it stays GREEN and the row proves nothing.
        {"type": "session_start", "supersedes": "agent_handoff",
         "handoff": ["decoy"]},
    ]
    (journal / "session-journal.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\nNOT JSON\n", encoding="utf-8"
    )

    tasks = home / ".claude-other" / "tasks" / "team"
    tasks.mkdir(parents=True)
    for name, handoff in [("1", CANONICAL), ("2", LEGACY), ("3", "not a dict")]:
        (tasks / f"{name}.json").write_text(
            json.dumps({"metadata": {"handoff": handoff}}), encoding="utf-8"
        )
    (tasks / "4.json").write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    (tasks / "5.json").write_text("{ broken", encoding="utf-8")


def test_key_set_reconstruction_is_faithful():
    """The journal arm stands a key list in for the handoff. That is exact
    only while the validator is presence-only, and this is the guard that
    fails loudly rather than letting the census drift into a wrong answer."""
    hc.assert_presence_only()  # raises SystemExit if the validator inspects values


