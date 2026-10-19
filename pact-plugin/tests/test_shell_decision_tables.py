"""
Location: pact-plugin/tests/test_shell_decision_tables.py
Summary: The parity instrument of the shell lane (the design notes §3.8,
         §5). Two kinds of table live under tests/fixtures/shell/decisions/:
           live.jsonl          every decision the live classifiers gave over the command
                               universe of the lane's suites (tests/tools/live_capture.py);
                               replayed here against the live module on every run. A
                               decision that differs is a failure unless a flips file
                               (expected_flips_*.json) declares it with a rule id, and
                               every declared flip must be observed, so a stale
                               declaration fails too.
           base_<sha8>.jsonl   a certification base's recorded calls (the loader serves
                               them); here their integrity is pinned.
           closures.jsonl      every base row whose result the live module no longer
                               gives (tests/tools/closures.py): the certification files'
                               "the fix closed this" assertions as one table. A base row
                               that agrees with live must not be listed; one that differs
                               must be, so a regression (live falls back to the base's
                               verdict) and an undeclared flip both fail.
Used by: run-tests QUICK; every carve step of the lane (a move commit changes no row).
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

import merge_guard_baseline_loader as loader

TESTS = Path(__file__).resolve().parent
DECISIONS = TESTS / "fixtures" / "shell" / "decisions"
LIVE = DECISIONS / "live.jsonl"
CLOSURES = DECISIONS / "closures.jsonl"
MODULES = {"merge": "shared.merge_guard_common", "env": "env_guard"}


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, TESTS / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_capture():
    return _tool("live_capture")


def _read(path):
    header_line, _, body = path.read_text(encoding="utf-8").partition("\n")
    header = json.loads(header_line)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert digest == header["sha256"], f"{path.name}: body drifted from its header digest"
    return header, body


def _declared_flips():
    """{call key: {"to": result, "rule": id}} over every expected_flips_*.json."""
    flips = {}
    for path in sorted(DECISIONS.glob("expected_flips_*.json")):
        for row in json.loads(path.read_text(encoding="utf-8")):
            assert row.get("rule"), f"{path.name}: a flip without a rule id: {row}"
            flips[json.dumps([row["fn"], row["args"]], ensure_ascii=False)] = row
    return flips


def _replay(fn, args):
    alias, name = fn.split(".", 1)
    live = getattr(importlib.import_module(MODULES[alias]), name)
    try:
        return loader.encode(live(*args), fn)
    except Exception as error:  # noqa: BLE001 — a raise is a decision too
        return loader.encode(error)


def test_live_table_replays_against_the_live_module():
    header, body = _read(LIVE)
    rows = list(_live_capture().rows_of(body))
    assert len(rows) == header["rows"], "live.jsonl row count differs from its header"
    declared = _declared_flips()
    unexpected, observed = [], set()
    for fn, args, result in rows:
        got = _replay(fn, args)
        if got == result:
            continue
        key = json.dumps([fn, args], ensure_ascii=False)
        if key in declared and declared[key]["to"] == got:
            observed.add(key)
            continue
        unexpected.append((fn, args, result, got))
    assert not unexpected, (
        f"{len(unexpected)} decision(s) differ from live.jsonl with no declared flip; first: "
        f"{unexpected[0]}. A move changes no row; a behaviour change declares its flips with "
        "a rule id in expected_flips_<step>.json"
    )
    stale = sorted(set(declared) - observed)
    assert not stale, f"declared flips not observed (stale declaration): {stale[:3]}"


@pytest.mark.parametrize("sha8", sorted(loader._VENDORED))
def test_each_base_table_is_intact(sha8):
    path = DECISIONS / f"base_{sha8}.jsonl"
    assert path.is_file(), f"no decision table for base {sha8}"
    header, body = _read(path)
    assert header["base"] == sha8
    assert header["blob_id"] == loader._VENDORED[sha8][1], (
        f"{path.name} was captured from blob {header['blob_id']}, the loader pins "
        f"{loader._VENDORED[sha8][1]}")
    assert header["rows"] == len(body.splitlines())


def test_base_rows_agree_with_live_except_the_declared_closures():
    """Bidirectional: a listed closure still differs from live (the fix holds), an unlisted
    base row still agrees (no undeclared flip against a base). Both directions name the
    row; the remedy for an intended change is `python3 tests/tools/closures.py`."""
    tool = _tool("closures")
    header, body = _read(CLOSURES)
    declared = {}
    for line in body.splitlines():
        row = json.loads(line)
        declared[(row["base"], row["fn"], json.dumps(row["args"], ensure_ascii=False),
                  json.dumps(row.get("kwargs", {}), sort_keys=True))] = row["was"]
    assert len(declared) == header["rows"], "closures.jsonl row count differs from its header"
    regressed, undeclared, replayed = [], [], 0
    for sha8 in sorted(loader._VENDORED):
        for name, args, kwargs, was in tool.replayable_rows(sha8):
            replayed += 1
            key = (sha8, name, json.dumps(args, ensure_ascii=False),
                   json.dumps(kwargs, sort_keys=True))
            differs = tool.live_result(name, args, kwargs) != was
            if key in declared and not differs:
                regressed.append(key)
            elif differs and key not in declared:
                undeclared.append(key)
    assert replayed == header["replayed"], (
        f"{replayed} replayable base rows, the table was built over {header['replayed']}")
    assert not regressed, (
        f"{len(regressed)} closure(s) no longer hold: live gives the base's verdict again; "
        f"first: {regressed[0]}")
    assert not undeclared, (
        f"{len(undeclared)} base row(s) differ from live with no closure declared; first: "
        f"{undeclared[0]}. An intended change re-runs tests/tools/closures.py and names its "
        "rule id in the commit")


def test_the_loader_fails_loudly_on_an_uncaptured_row():
    base = loader.load_vendored(sorted(loader._VENDORED)[0])
    with pytest.raises(pytest.fail.Exception, match="row not captured"):
        base.is_dangerous_command("a command no certification file ever fed this base")


def test_the_loader_fails_loudly_on_a_drifted_table(tmp_path, monkeypatch):
    sha8 = sorted(loader._VENDORED)[0]
    source = DECISIONS / f"base_{sha8}.jsonl"
    header, body = source.read_text(encoding="utf-8").split("\n", 1)
    (tmp_path / source.name).write_text(header + "\n" + body.replace("true", "false", 1),
                                        encoding="utf-8")
    monkeypatch.setattr(loader, "_DECISIONS_DIR", tmp_path)
    monkeypatch.setattr(loader, "_cached_vendored", {})
    with pytest.raises(pytest.fail.Exception, match="drifted"):
        loader.load_vendored(sha8)


def test_live_table_holds_no_line_the_commit_check_would_refuse():
    """A merge scans every commit's own patch, so one secret-shaped row refuses the merge."""
    capture = _live_capture()
    _, body = _read(LIVE)
    flagged = [n for n, line in enumerate(body.splitlines(), 2) if capture.secret_row(line)]
    assert not flagged, f"live.jsonl lines the commit secret detector flags: {flagged}"


def test_capture_screens_a_secret_shaped_row_at_write(tmp_path, monkeypatch):
    capture = _live_capture()
    secret_shaped = 'psql "password=Zq8vL2mN4p dbname=x"'  # pragma: allowlist secret
    assert capture.secret_row(secret_shaped), "the fixture no longer matches the detector"
    monkeypatch.setattr(capture, "TABLE", tmp_path / "live.jsonl")
    monkeypatch.setattr(capture, "_by_command", {
        "git status": {"merge.is_dangerous_command": False},
        secret_shaped: {"merge.is_dangerous_command": False}})
    capture._write()
    header, body = capture.TABLE.read_text(encoding="utf-8").split("\n", 1)
    assert "psql" not in body and json.loads(body.splitlines()[0])[0] == "git status"
    assert json.loads(header)["screened_secret_shaped"] == 1
    assert (json.loads(header)["rows"], json.loads(header)["commands"]) == (1, 1)
