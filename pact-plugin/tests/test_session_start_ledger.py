"""The session-start byte ledger (hooks/shared/parts_ledger.py): one test per behaviour.

A start names its parts and writes them beside dispatch.py's counters; the report prints them.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from shared import parts_ledger
from shared.pact_context import reconstruct_session_dir
from shared.session_init_common import NOTICE_MARKER, _INPROCESS_MODE_NOTICE
from shared.session_init_lead import SNAPSHOT_MAX_BYTES, cap_snapshot
from tests.test_session_init import TestInprocessModeNoticeIntegration, _run_session_init_for_path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
INJECTION = json.loads((PLUGIN_ROOT / "tests" / "contract" / "budgets.json").read_text(encoding="utf-8"))["injection"]


def _transcripts():
    spec = importlib.util.spec_from_file_location("_transcripts_ledger_test", PLUGIN_ROOT / "hooks" / "shared" / "transcripts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_notes_name_their_bytes_and_account_names_the_rest():
    parts_ledger.reset()
    context = ["abc"]
    parts_ledger.account("environment", context)
    context.insert(0, "directive!")
    parts_ledger.account("directive", context)
    parts_ledger.note("snapshot", "snap")
    context.append("snap")
    context.append("xy")
    parts_ledger.account("finish", context)
    assert parts_ledger.ledger() == {"environment": 3, "directive": 10, "snapshot": 4, "finish": 2}
    text, changed, _ = parts_ledger.merge('{"v": 1, "checks": {"g": {"runs": 1}}}',
                                          {"source": "startup", "parts": parts_ledger.ledger()})
    assert changed and json.loads(text)["checks"] == {"g": {"runs": 1}}
    assert json.loads(text)["session_init"]["parts"]["directive"] == 10


def test_a_startup_frame_writes_the_ledger_beside_the_counters(monkeypatch, tmp_path):
    additional, _, _ = _run_session_init_for_path(monkeypatch, tmp_path, "startup", team_exists=True)
    files = list(tmp_path.rglob("context-cost.json"))
    assert len(files) == 1, files
    entry = json.loads(files[0].read_text(encoding="utf-8"))["session_init"]
    assert entry["source"] == "startup" and entry["frame"] == "lead"
    assert entry["context"] == len(additional.encode("utf-8"))
    assert entry["parts"]["directive"] > 0
    assert sum(entry["parts"].values()) <= entry["context"]


def test_the_report_prints_the_last_start_and_its_top_parts():
    tr = _transcripts()
    cost = {"total": {"runs": 0, "context": 0, "system": 0, "writes": 0, "written": 0}, "sessions": {},
            "top_checks": {}, "frames": {}, "agents": {},
            "session_start": {"sess": {"source": "resume", "context": 12288, "system": 512,
                                       "parts": {"directive": 2048, "snapshot": 6144, "briefing": 1024,
                                                 "environment": 100, "resumption_tasks": 3072}}}}
    text = "\n".join(tr.render_cost(cost))
    assert ("session start        resume 12.0 KB context, 0.5 KB system: snapshot 6.0 KB, "
            "resumption_tasks 3.0 KB, directive 2.0 KB, briefing 1.0 KB") in text


def test_an_oversized_snapshot_is_cut_at_a_line_and_names_the_journal(tmp_path):
    text = "\n".join(f"line {n:04d} " + "x" * 60 for n in range(80))
    capped = cap_snapshot(text, str(tmp_path))
    kept, trailer = capped.rsplit("\n", 1)
    assert len(kept.encode("utf-8")) <= SNAPSHOT_MAX_BYTES < len(text.encode("utf-8"))
    assert kept.endswith("x" * 60), "the cut lands on a line boundary"
    assert trailer == f"(cut at {SNAPSHOT_MAX_BYTES} B; the full journal is {tmp_path}/session-journal.jsonl)"
    assert cap_snapshot("short", str(tmp_path)) == "short"


def test_the_static_startup_frame_fits_the_injection_budget(monkeypatch, tmp_path):
    additional, _, _ = _run_session_init_for_path(monkeypatch, tmp_path, "startup", team_exists=True)
    size = len(additional.encode("utf-8"))
    assert size <= INJECTION["session_init_static"] <= INJECTION["session_init_startup"], size


def test_the_unattended_notice_is_told_once_per_session_and_mode(monkeypatch, tmp_path):
    # The harness drives an unknown frame, which journals nothing; a lead's session dir exists
    # by the time the notice runs, so plant it where get_session_dir() will point.
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    session_dir = Path(reconstruct_session_dir(str(tmp_path / "project"), "aabb1122-0000-0000-0000-000000000000"))
    session_dir.mkdir(parents=True)
    runner = TestInprocessModeNoticeIntegration()
    first = runner._run_main_capture_systemmessage(monkeypatch, tmp_path, "startup", "in-process")
    assert _INPROCESS_MODE_NOTICE in first
    assert json.loads((session_dir / NOTICE_MARKER).read_text(encoding="utf-8")) == {"mode": "in-process"}
    second = runner._run_main_capture_systemmessage(monkeypatch, tmp_path, "resume", "in-process")
    assert _INPROCESS_MODE_NOTICE not in second, "the same session and mode hears the notice once"
    changed = runner._run_main_capture_systemmessage(monkeypatch, tmp_path, "resume", None)
    assert _INPROCESS_MODE_NOTICE in changed, "a changed mode re-tells it"
