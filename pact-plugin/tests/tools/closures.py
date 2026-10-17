"""
Location: pact-plugin/tests/tools/closures.py
Summary: Builds tests/fixtures/shell/decisions/closures.jsonl: every call row of every
         base_<sha8>.jsonl table whose result the LIVE module no longer gives. Each row is
         a closure the certification files once asserted one by one (`load_vendored(sha).fn(cmd)`
         differs from the live `fn(cmd)`); the table makes the same assertion mechanically,
         so the cert files can go (the design notes §3.8, §5 step 18). Rows naming a scratch
         or a personal path are not written. Run once after a behaviour change that flips a
         base row (`python3 tests/tools/closures.py`); the test that reads the table says when.
Used by: tests/test_shell_decision_tables.py (replay); the maintainer at step 18.
Map: SCRATCH_MARKS, replayable_rows, build, main.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent / "hooks"))

import merge_guard_baseline_loader as loader  # noqa: E402

TABLE = TESTS / "fixtures" / "shell" / "decisions" / "closures.jsonl"
LIVE_MODULE = "shared.merge_guard_common"
SCRATCH_MARKS = ("/pytest-of-", "/var/folders/", "/tmp/tmp", "/Users/", "/home/")


def replayable_rows(sha8: str):
    """(fn, args, kwargs, base result) for every call row of a base whose function the live
    module still has; rows with a scratch or personal path in their key are left out."""
    table = loader._load_table(sha8)
    live = importlib.import_module(LIVE_MODULE)
    for key, result in table["calls"].items():
        if any(mark in key for mark in SCRATCH_MARKS):
            continue
        name, args, kwargs = json.loads(key)
        if not callable(getattr(live, name, None)):
            continue
        yield name, args, kwargs, result


def live_result(name: str, args, kwargs):
    live = importlib.import_module(LIVE_MODULE)
    try:
        return loader.encode(getattr(live, name)(*loader.decode(args), **loader.decode(kwargs)), name)
    except Exception as error:  # noqa: BLE001 — a raise is a decision too
        return loader.encode(error)


def build() -> tuple[str, dict]:
    """(body, header) of the closures table over every base the loader knows."""
    lines, replayed = [], 0
    for sha8 in sorted(loader._VENDORED):
        for name, args, kwargs, was in replayable_rows(sha8):
            replayed += 1
            if live_result(name, args, kwargs) != was:
                row = {"base": sha8, "fn": name, "args": args, "was": was}
                if kwargs:
                    row["kwargs"] = kwargs
                lines.append(json.dumps(row, sort_keys=True, ensure_ascii=False))
    body = "\n".join(lines) + ("\n" if lines else "")
    header = {"replayed": replayed, "rows": len(lines),
              "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
    return body, header


def main() -> int:
    body, header = build()
    TABLE.write_text(json.dumps(header, sort_keys=True) + "\n" + body, encoding="utf-8")
    print(f"closures: {header['rows']} of {header['replayed']} replayable base rows differ "
          f"from live -> {TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
