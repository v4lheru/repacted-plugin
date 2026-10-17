"""Pins bin/repact-docs-gen: the troubleshooting anchors match the rule registry, and --check
fails on a page that drifted from a fresh render.

Location: pact-plugin/tests/test_docsgen.py. Runs the CLI in a subprocess (the generator
executes the registry under a private module name; this test imports it as shared.rules).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from shared.rules import RULES, anchor

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
GEN = PLUGIN_ROOT / "bin" / "repact-docs-gen"
ANCHOR = re.compile(r'<a id="([^"]+)"></a>')


def _gen(*args):
    return subprocess.run([sys.executable, str(GEN), *map(str, args)],
                          capture_output=True, text=True, check=False)


def test_every_rule_id_has_one_anchor_and_every_anchor_a_live_id(tmp_path):
    assert _gen(tmp_path).returncode == 0
    expected = sorted(anchor(rule_id) for rule_id in RULES)
    for page in (tmp_path / "docs" / "troubleshooting.md",
                 PLUGIN_ROOT / "docs" / "troubleshooting.md"):
        assert sorted(ANCHOR.findall(page.read_text(encoding="utf-8"))) == expected, page


def test_check_fails_on_drift_and_regeneration_keeps_the_prose(tmp_path):
    assert _gen(tmp_path).returncode == 0
    assert _gen("--check", tmp_path).returncode == 0
    assert _gen("--check", "--pages", "troubleshooting", PLUGIN_ROOT).returncode == 0
    page = tmp_path / "docs" / "reference" / "commands.md"
    text = page.read_text(encoding="utf-8")
    drifted = text.replace("<!-- /generated -->", "| `/stale-command` | stale |  |\n"
                           "<!-- /generated -->").replace("# Commands", "# Commands\n\nLocal note.")
    page.write_text(drifted, encoding="utf-8")
    done = _gen("--check", tmp_path)
    assert done.returncode == 1 and "differs: docs/reference/commands.md" in done.stdout
    assert _gen(tmp_path).returncode == 0
    regenerated = page.read_text(encoding="utf-8")
    assert "/stale-command" not in regenerated and "Local note." in regenerated
