"""The /repacted:rePACT stub is user-only (disable-model-invocation), so no model-facing line may
still tell the lead to invoke it: every nested cycle, ATOMIZE's per-scope executor included, runs
by following reference/rePACT/nested-cycle.md directly.

Model-facing = every markdown file under agents/, commands/ (except the stub), protocols/,
reference/ and skills/, and every hook module (hooks emit text into the model's context).
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
STUB = PLUGIN_ROOT / "commands" / "rePACT.md"
NESTED_CYCLE = PLUGIN_ROOT / "reference" / "rePACT" / "nested-cycle.md"
WORKFLOWS = PLUGIN_ROOT / "protocols" / "workflows.md"
INVOCATION = re.compile(r"/(?:repacted|PACT):rePACT\b")


def model_facing_files() -> list[Path]:
    files = [p for d in ("agents", "commands", "protocols", "reference", "skills")
             for p in (PLUGIN_ROOT / d).rglob("*.md")]
    files += list((PLUGIN_ROOT / "hooks").rglob("*.py"))
    return sorted(p for p in files if p != STUB)


def frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, "no frontmatter"
    return dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)


def test_the_stub_is_user_invocable_only():
    fm = frontmatter(STUB.read_text(encoding="utf-8"))
    assert fm.get("disable-model-invocation", "").strip() == "true"


def test_no_model_facing_line_invokes_the_stub():
    hits = [f"{p.relative_to(PLUGIN_ROOT)}:{n}: {line.strip()[:100]}"
            for p in model_facing_files()
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if INVOCATION.search(line)]
    assert not hits, "model-facing lines still invoke the user-only stub:\n" + "\n".join(hits)


def test_the_scan_reaches_the_files_that_held_the_call_sites():
    """Guards the scan above against going vacuous: the files that held the call sites are in it."""
    scanned = {p.relative_to(PLUGIN_ROOT).as_posix() for p in model_facing_files()}
    assert {"protocols/workflows.md", "reference/rePACT/nested-cycle.md",
            "reference/orchestrate/scoped-orchestration.md"} <= scanned


def test_atomize_runs_each_scope_as_a_nested_cycle_from_its_reference():
    atomize = [line for line in WORKFLOWS.read_text(encoding="utf-8").splitlines()
               if line.startswith("**ATOMIZE.**")]
    assert len(atomize) == 1
    assert "(../reference/rePACT/nested-cycle.md)" in atomize[0]
    assert NESTED_CYCLE.is_file()


def test_the_stub_still_resolves_to_the_nested_cycle_for_a_user_who_types_it():
    assert "(../reference/rePACT/nested-cycle.md)" in STUB.read_text(encoding="utf-8")
