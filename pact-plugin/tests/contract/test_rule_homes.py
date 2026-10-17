"""Contract 6, RULE HOMES: every rule the retired prose pins guarded has exactly one home, or was dropped.

Build plan §3 (W0-INFRA). W0 retired the cross-surface prose-pin modules and inventoried every rule
they guarded (the design notes). rule_homes/current.json maps each home file
to its rule ids; `dropped` lists the rules a package retired with a reason (the reasons and the
verbatim anchors, retired in 1.5.0, are in git). Wording is not pinned: whether a pointer resolves is
contract 7 (test_pointers_exist.py) and contract 2 (test_anchor_integrity.py).

A package that moves a rule moves its id to the new home's list; one that drops a rule moves its id
to `dropped` and gives the reason in its HANDOFF.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from tests.contract._surfaces import PLUGIN_ROOT

CURRENT = Path(__file__).resolve().parent / "rule_homes" / "current.json"
MAP = json.loads(CURRENT.read_text(encoding="utf-8"))
IDS = [rid for ids in MAP["homes"].values() for rid in ids] + MAP["dropped"]


def test_each_rule_has_one_home():
    twice = sorted(rid for rid, n in Counter(IDS).items() if n > 1)
    assert not twice, f"rules listed under more than one home (or homed and dropped): {twice}"


def test_every_home_exists():
    missing = sorted(home for home in MAP["homes"] if not (PLUGIN_ROOT / home).is_file())
    assert not missing, f"homes that do not exist (move their rules): {missing}"


def test_the_retired_modules_are_gone():
    left = [m for m in MAP["retired_modules"] if (PLUGIN_ROOT / m).exists()]
    assert not left, f"retired modules still present: {left}"


