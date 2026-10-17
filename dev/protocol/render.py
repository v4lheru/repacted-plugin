#!/usr/bin/env python3
"""Render the teachback / HANDOFF / wait contract into its marked regions, or check them.

  python3 dev/protocol/render.py --check     # exit 1 with a unified diff per stale region
  python3 dev/protocol/render.py --write     # rewrite every region in place

The descriptor (pact-plugin/hooks/shared/protocol_descriptor.py) is the one source; this script
only moves its rendered text into the committed files listed in its REGIONS map. The regions are
committed, so nothing runs at install time; tests/contract/test_protocol_rendered.py runs the
same check and names this command when a region differs.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

REPACTED = Path(__file__).resolve().parents[2]
PLUGIN = REPACTED / "pact-plugin"
sys.path.insert(0, str(PLUGIN / "hooks"))

from shared.marked_regions import find_regions, replace_region  # noqa: E402
from shared.protocol_descriptor import REGIONS  # noqa: E402


def rendered_files() -> dict[Path, str]:
    """Each target file's text with every region re-rendered."""
    out: dict[Path, str] = {}
    for name, (rel, render) in REGIONS.items():
        path = PLUGIN / rel
        text = out.get(path) or path.read_text(encoding="utf-8")
        out[path] = replace_region(text, name, render())
    return out


def check() -> int:
    stale = 0
    for path, text in rendered_files().items():
        current = path.read_text(encoding="utf-8")
        if current == text:
            continue
        stale += 1
        rel = path.relative_to(REPACTED.parent).as_posix()
        sys.stdout.writelines(difflib.unified_diff(
            current.splitlines(keepends=True), text.splitlines(keepends=True),
            fromfile=f"a/{rel}", tofile=f"b/{rel}"))
    if stale:
        print(f"{stale} file(s) differ from the descriptor; run: python3 dev/protocol/render.py --write")
        return 1
    print(f"{len(REGIONS)} regions match the descriptor")
    return 0


def write() -> int:
    for path, text in rendered_files().items():
        if path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            print(f"rendered {path.relative_to(REPACTED.parent).as_posix()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="exit 1 with a diff when a region is stale")
    mode.add_argument("--write", action="store_true", help="rewrite every region in place")
    args = parser.parse_args(argv)
    for name, (rel, _) in REGIONS.items():
        if name not in find_regions((PLUGIN / rel).read_text(encoding="utf-8")):
            print(f"{rel} has no region {name!r}", file=sys.stderr)
            return 2
    return check() if args.check else write()


if __name__ == "__main__":
    sys.exit(main())
