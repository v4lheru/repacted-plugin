"""D3 STATUS golden replays on the real session the design notes §1 measured.

Opt-in: set REPACT_D3_GOLDEN_TRANSCRIPT to that session's lead transcript
(<config>/projects/<slug>/5a318e14-c851-4a08-a12d-e779a43d3ec8.jsonl). The transcripts hold
user content, so they are never committed; this file commits only the numbers §1 printed.

Each count is reproduced EXACTLY at an --until cutoff. The §1 measurement scripts
(#99, wd_*.py) ran at different minutes, and their file times agree with the cutoffs:
  22:08:00Z  §1.5 spend (wd_tokens.py, 22:08) and §1.1 wakes (wd_inbound*.py, 22:06).
             The per-role call counts pin the cutoff to [22:07:55.8, 22:08:23.9).
  22:16:00Z  §1.2 delivery delays (wd_delay.py, 22:16).
  22:12:00Z  §1.3's 13 UNFLAGGED surfaces (13 for any cutoff in [22:11:06.8, 22:15:04.4)).
  22:03:00Z  §D3's status example: fork-builder busy in a foreground wait.
"""
import json
import os
import re
from pathlib import Path

import pytest

def _load_transcripts():
    """hooks/shared/transcripts.py by file path (tests never edit sys.path; conftest owns path setup)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_transcripts", Path(__file__).resolve().parent.parent / "hooks" / "shared" / "transcripts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tr = _load_transcripts()


GOLDEN = os.environ.get("REPACT_D3_GOLDEN_TRANSCRIPT")
pytestmark = pytest.mark.skipif(not GOLDEN or not Path(GOLDEN).is_file(),
                                reason="set REPACT_D3_GOLDEN_TRANSCRIPT to replay the real session")
SURFACE_NAMES = re.compile(r"no flagged wait: (.*?)\. The job may")


@pytest.fixture(scope="module")
def lead():
    return Path(GOLDEN)


@pytest.fixture(scope="module")
def files(lead):
    return tr.session_files(lead)


def test_spend_by_role_matches_section_1_5(files):
    spend = tr.spend_by_role(files, tr.parse_ts("2026-09-26T22:08:00Z"))
    assert {r: (v["transcripts"], v["calls"]) for r, v in spend.items()} == {
        "lead": (1, 779), "teammate": (29, 5924), "subagent": (56, 1472)}
    assert {r: round(v["input_side"] / 1e6, 1) for r, v in spend.items()} == {
        "lead": 399.7, "teammate": 2538.1, "subagent": 282.9}
    assert round(sum(v["input_side"] for v in spend.values()) / 1e6, 1) == 3220.8
    assert round(sum(v["output"] for v in spend.values()) / 1e6, 2) == 2.03


def test_wakes_match_section_1_1(lead):
    wakes = tr.wakes(list(tr.iter_entries(lead, tr.parse_ts("2026-09-26T22:08:00Z"))))
    assert wakes == {
        "teammate_entries": 330, "idle_notifications": 282, "text_messages": 229,
        "idle_only_wakes": 145, "idle_only_calls": 165, "idle_only_cache_read": 96_223_374,
        "idle_only_output": 24_195, "idle_only_no_tool": 129, "double_wakes": 121,
        "double_wake_median_gap_s": 17,
    }


def test_delivery_matches_section_1_2(lead, files):
    until = tr.parse_ts("2026-09-26T22:16:00Z")
    assert tr.delivery(list(tr.iter_entries(lead, until)), files, until) == {
        "messages": 284, "located": 277, "late": 111, "late_median_min": 10.7}


def _surfaces(lead, until):
    """(time, [teammates named]) for each UNFLAGGED surface in the lead's context."""
    out = []
    for entry in tr.iter_entries(lead, until):
        attachment = entry.get("attachment") or {}
        if entry.get("type") != "attachment" or attachment.get("type") != "hook_additional_context":
            continue
        text = json.dumps(attachment)
        if tr.UNFLAGGED_PREFIX in text:
            names = [p.split(" (", 1)[0] for p in SURFACE_NAMES.search(text).group(1).split("; ")]
            out.append((tr.parse_ts(entry["timestamp"]), list(dict.fromkeys(names))))
    return out


def _still_alarmed(files, surfaces):
    """Replay the new rule at each surface: a named teammate stays only if its transcript,
    read up to that moment, does not show it working."""
    rules = tr.activity_rules
    paths = {f["name"]: f["path"] for f in files if f["role"] == "teammate"}
    cache = {}
    kept = []
    for moment, names in surfaces:
        still = []
        for name in names:
            if name not in cache:
                cache[name] = [(tr.parse_ts(e.get("timestamp")), e) for e in tr.iter_entries(paths[name])]
            entries = [e for t, e in cache[name] if t and t <= moment]
            if not (entries and rules.is_active(rules.activity(entries), moment)):
                still.append(name)
        kept.append(still)
    return kept


def test_thirteen_unflagged_surfaces_and_none_survives_the_fix(lead, files):
    """All 16 named teammates were mid-turn with a recent write at their surface."""
    surfaces = _surfaces(lead, tr.parse_ts("2026-09-26T22:12:00Z"))
    assert len(surfaces) == 13
    assert sum(len(names) for _, names in surfaces) == 16
    assert _still_alarmed(files, surfaces) == [[]] * 13


def test_sabotage_without_the_activity_rule_every_entry_stays(lead, files, monkeypatch):
    """The fix, not the data, empties the alarm: with a zero window and no pending-call
    signal every one of the 16 teammate entries is kept."""
    monkeypatch.setattr(tr.activity_rules, "ACTIVE_WINDOW_SECONDS", -1)
    monkeypatch.setattr(tr.activity_rules, "PENDING_MAX_SECONDS", -1)
    surfaces = _surfaces(lead, tr.parse_ts("2026-09-26T22:12:00Z"))
    assert sum(len(s) for s in _still_alarmed(files, surfaces)) == 16


def test_status_at_22_03_shows_fork_builder_busy_in_a_wait(lead):
    lines = dict(l.split("  ", 1) for l in tr.status_lines(lead, until=tr.parse_ts("2026-09-26T22:03:00Z")))
    line = lines["fork-builder"]
    assert line.startswith('busy  Bash fg "Wait') and "bg:1" in line
