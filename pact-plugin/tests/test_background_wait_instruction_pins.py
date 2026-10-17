"""Background-wait instructions outside the orchestrator persona.

Location: pact-plugin/tests/test_background_wait_instruction_pins.py
Summary: pins the text that tells a teammate to flag a wait after backgrounding
         work, where a consultant flags it, which lead turns the missed-wake
         scan runs on, and what tmux teammate mode does not cover. The
         orchestrator's own rules are pinned section by section in
         test_wait_discipline_pins.py.
Used by: the suite. No module-level sys.path.insert — path setup is
         conftest-owned; see tests/test_path_setup_pin.py.

PRESENCE PINS, NORMALISED as in that file: backticks stripped and whitespace
collapsed on both sides, so a re-wrap passes and a re-word fails.

THE STARTUP NOTICE IS READ FROM THE EVALUATED CONSTANT. It is built from
adjacent string literals, so a phrase can be split across two of them, and no
search of the source text finds it.
"""

from __future__ import annotations

from pathlib import Path


import session_init

PLUGIN = Path(__file__).resolve().parents[1]


def _phrase(text: str) -> str:
    return " ".join(text.replace("`", "").split())




def test_the_in_process_notice_says_tmux_does_not_cover_an_unwatched_background_job():
    notice = _phrase(session_init._INPROCESS_MODE_NOTICE)
    claim = ("`--teammate-mode tmux` makes teammate wake delivery reliable; it does "
             "NOT cover a background job that finishes with nobody watching")
    assert _phrase(claim) in notice, (
        "the in-process startup notice no longer says what tmux mode does not "
        f"cover, so a lead may relaunch in tmux and expect background jobs to wake it: {notice!r}"
    )


def test_a_teammate_is_told_to_run_long_commands_in_the_foreground():
    """2026-09-29: two in-process coders backgrounded the full suite and idled about 40 minutes,
    because the job's completion notice never reaches the teammate (and, of 17 measured, 16
    never reached the lead either; T-63eac115)."""
    from shared import turn_end_gate

    skill = _phrase((PLUGIN / "skills/teammate/SKILL.md").read_text(encoding="utf-8"))
    assert _phrase("Run long commands in the foreground, never `run_in_background`") in skill
    assert "wait for the result in the foreground" in _phrase(turn_end_gate.TEAMMATE_BLOCK_TEXT)
    assert "the lead gets its notice" not in skill
    assert "it goes to the lead" not in _phrase(turn_end_gate.TEAMMATE_BLOCK_TEXT)
