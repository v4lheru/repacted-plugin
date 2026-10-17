#!/usr/bin/env python3
"""
Location: pact-plugin/tests/tools/capture_decisions.py
Summary: Captures (or verifies) the merge-guard certification decision tables,
         tests/fixtures/shell/decisions/base_<sha8>.jsonl. It runs the certification
         files that load a base through tests/merge_guard_baseline_loader.py with the
         loader in capture mode, so every call and attribute read on every base is
         recorded and written at exit; `--verify` runs them in verify mode instead,
         where each lookup is compared with the vendored module and a difference fails.
         Both modes need the vendored bytes under tests/fixtures/merge_guard_baseline/,
         which the tree no longer carries: restore each with
         `git show <blob_id> > tests/fixtures/merge_guard_baseline/<name>` using the
         _VENDORED rows of the loader.
Used by: the maintainer, by hand, when a base joins the table or a cert adds a row.
         Run from pact-plugin/:  python3 tests/tools/capture_decisions.py [--verify]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent.parent
TESTS = PLUGIN / "tests"
LOADER_IMPORT = re.compile(r"^\s*(from|import)\s+merge_guard_baseline_loader\b", re.M)


def consumers() -> list[Path]:
    """Every test file that imports the loader (the certs and the row files)."""
    return sorted(p for p in TESTS.glob("test_*.py")
                  if p.name != "test_merge_guard_vendored_baselines.py"
                  and LOADER_IMPORT.search(p.read_text(encoding="utf-8")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--verify", action="store_true",
                        help="compare every lookup with the vendored module instead of recording")
    args = parser.parse_args(argv)
    files = consumers()
    if not files:
        print("no consumer of merge_guard_baseline_loader found under tests/", file=sys.stderr)
        return 2
    env = dict(os.environ)
    env.pop("PACT_CAPTURE_DECISIONS", None)
    env.pop("PACT_DECISIONS_VERIFY", None)
    if args.verify:
        env["PACT_DECISIONS_VERIFY"] = "1"
    else:
        env["PACT_CAPTURE_DECISIONS"] = "1"
        env["PACT_CAPTURE_COMMIT"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PLUGIN, capture_output=True,
            text=True, check=False).stdout.strip() or "unknown"
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
           *(str(p.relative_to(PLUGIN)) for p in files)]
    print(("verify" if args.verify else "capture") + ": " + " ".join(p.name for p in files))
    return subprocess.run(cmd, cwd=PLUGIN, env=env, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
