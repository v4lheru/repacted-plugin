"""
/repacted:wrap-up's KB queue drain step (KB-GATE phase 2; the design notes
sections 7.4 and 7.5). The step sits before the commit, drains the vault-note queue with ONE fresh
blind answerer that sees only each item's situation, and lets the lead (or the main agent with no
lead) decide; the user is never asked. The procedure lives in reference/wrap-up/kb-queue.md so the
command stays near its m8b budget.
"""
from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WRAP_UP = PLUGIN_ROOT / "commands" / "wrap-up.md"
REFERENCE = PLUGIN_ROOT / "reference" / "wrap-up" / "kb-queue.md"
DRAIN_HEADING = "## 7b. KB Queue Drain"
BRIEF_BEGIN, BRIEF_END = "<!-- blind-brief:begin -->", "<!-- blind-brief:end -->"


def section(text: str) -> str:
    start = text.index(DRAIN_HEADING)
    return text[start:text.index("\n## ", start + 1)]


def test_the_drain_step_comes_before_the_session_decision_and_its_commit():
    text = WRAP_UP.read_text(encoding="utf-8")
    drain = text.index(DRAIN_HEADING)
    assert drain < text.index("## 8. Session Decision") < text.index('bin/repact-task" commit')


def test_the_drain_step_names_each_move_and_who_drains():
    body = section(WRAP_UP.read_text(encoding="utf-8"))
    for needle in ('vault-note" queue', "--blind-brief", "drain --decide", 'vault-note" apply',
                   "kb-queue.md", "ONE fresh blind answerer", "never the notes or the KB",
                   "main agent drains", "Never ask the user", "earlier sessions"):
        assert needle in body, needle


def test_the_reference_carries_the_procedure():
    text = REFERENCE.read_text(encoding="utf-8")
    for needle in ("ONE fresh blind answerer", "CLAUDE_WRONG", "CLAUDE_KNOWS", "UNSURE",
                   "accepted", "edited", "rejected", "deferred", "--by", "--blind", 'vault-note" apply',
                   "advisory", "never gates", "audit sample", "Never ask the user",
                   "EARLIER sessions", "later session's drain", "[this session: decide later]"):
        assert needle in text, needle


def test_the_blind_brief_withholds_the_notes_and_the_vault():
    text = REFERENCE.read_text(encoding="utf-8")
    brief = text[text.index(BRIEF_BEGIN) + len(BRIEF_BEGIN):text.index(BRIEF_END)]
    assert "{situations}" in brief
    for forbidden in ("{claim}", "{why}", "{apply}", "{evidence}", "{target}", "Coding-Notes", "vault-find"):
        assert forbidden not in brief, forbidden
    for rule in ("Do not read any file", "do not search", "from what you already know"):
        assert rule in brief, rule
