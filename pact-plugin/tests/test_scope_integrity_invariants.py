"""Scope, nesting, merge-authorization, task-hierarchy, worktree and scope-detection invariants,
ported from four retired bash canaries: scripts/verify-scope-integrity.sh and
verify-task-hierarchy.sh (CLEANUP-A), verify-worktree-protocol.sh and
verify-scope-detection-scenarios.sh (CLEANUP-B).

The scripts read the deleted protocols/pact-protocols.md and the pre-m8b command sections, so they
failed on every run, and no test or gate ran them. Each rule they checked that the current prose
still states is pinned here, at its current home, so the merge gate runs it. The number in each
id is the script section it came from (vsi = verify-scope-integrity, vth = verify-task-hierarchy,
vwp = verify-worktree-protocol, vsd = verify-scope-detection-scenarios).
Checks left behind, with the reason, are listed in DROPPED below.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN_ROOT.parent / "scripts"

# (id, home, regex, present). present=False means the pattern must NOT occur in the home.
PINS = [
    ("vsi-1 comPACT carries no scope_id metadata", "commands/comPACT.md", r'"scope_id"', False),
    ("vsi-1 comPACT carries no scope contract", "commands/comPACT.md", r"scope contract", False),
    ("vsi-2 the scope contract defines scope_id", "protocols/workflows.md", r"Identity: scope_id", True),
    ("vsi-2/6 the task hierarchy names scoped tasks [scope:]", "protocols/coordination.md", r"\[scope:", True),
    ("vsi-3 detection leads to scope contracts", "protocols/workflows.md", r"A or C → scope contracts, then ATOMIZE", True),
    ("vsi-5 a sub-scope HANDOFF appends Contract Fulfillment", "protocols/workflows.md", r"\*\*Contract Fulfillment\*\*", True),
    ("vsi-5 the nested cycle reports Contract Fulfillment", "reference/rePACT/nested-cycle.md", r"Contract Fulfillment", True),
    ("vsi-6 the nested cycle names scoped tasks [scope:]", "reference/rePACT/nested-cycle.md", r"\[scope:", True),
    ("vsi-7 orchestrate points ATOMIZE/CONSOLIDATE at the scoped-phases protocol", "commands/orchestrate.md", r"workflows\.md#scoped-phases", True),
    ("vsi-7 the ATOMIZE phase section exists", "reference/orchestrate/scoped-orchestration.md", r"ATOMIZE Phase \(Scoped Orchestration Only\)", True),
    ("vsi-7 the CONSOLIDATE phase section exists", "reference/orchestrate/scoped-orchestration.md", r"CONSOLIDATE Phase \(Scoped Orchestration Only\)", True),
    ("vsi-7b CONSOLIDATE has the architect verify contracts", "protocols/workflows.md", r"`pact-architect` verify cross-scope contract", True),
    ("vsi-7b CONSOLIDATE has the test engineer run cross-scope tests", "protocols/workflows.md", r"pact-test-engineer.*cross-scope", True),
    ("vsi-7b a failed consolidation routes through imPACT", "protocols/workflows.md", r"(?s)\*\*CONSOLIDATE\.\*\*.*imPACT", True),
    ("vsi-9 decomposition_active is a documented skip reason", "commands/orchestrate.md", r"decomposition_active", True),
    ("vsi-11 sub-scopes never re-run detection", "protocols/workflows.md", r"Sub-scopes never re-run detection", True),
    ("vsi-13 the nested cycle caps nesting at 1 level", "reference/rePACT/nested-cycle.md", r"Maximum nesting: 1 level", True),
    ("vsi-13 specialist nesting is limited to 1 level", "protocols/workflows.md", r"Nesting is limited to 1 level", True),
    ("vsi-14 orchestrate references worktree-setup", "commands/orchestrate.md", r"worktree-setup", True),
    ("vsi-14 comPACT references worktree-setup", "commands/comPACT.md", r"worktree-setup", True),
    ("vsi-14 peer-review delegates post-merge work to wrap-up", "commands/peer-review.md", r"wrap-up", True),
    ("vsi-14 orchestrate propagates worktree_path to agents", "commands/orchestrate.md", r"worktree_path", True),
    ("vsi-14 ATOMIZE creates a worktree per scope", "protocols/workflows.md", r"\*\*ATOMIZE\.\*\* Create a worktree per scope", True),
    ("vsi-18 decomposition threshold is score >= 3", "protocols/workflows.md", r"Decompose at \*\*score ≥ 3\*\*", True),
    ("vsi-18 the autonomous tier is documented", "protocols/workflows.md", r"Autonomous: only when BOTH strong signals fire", True),
    ("vsi-19 the skip gate counts 7 incompleteness signals", "reference/orchestrate/phase-skip-gates.md", r"7 incompleteness signals", True),
    ("vsi-22 algedonic: an irreversible act needs AskUserQuestion", "protocols/algedonic.md", r"Irreversible.*AskUserQuestion", True),
    ("vsi-22 peer-review merge uses AskUserQuestion", "commands/peer-review.md", r"AskUserQuestion.*merge authorization", True),
    ("vsi-22 orchestrate merge step uses AskUserQuestion", "commands/orchestrate.md", r"AskUserQuestion.*merge authorization", True),
    ("vsi-23 structured_gate_passed is a skip reason", "commands/orchestrate.md", r"structured_gate_passed", True),
    ("vsi-23 the Variety Hard Gates section exists", "reference/orchestrate/phase-skip-gates.md", r"Variety Hard Gates", True),
    ("vth phase headings: PREPARE", "commands/orchestrate.md", r"^### PREPARE Phase", True),
    ("vth phase headings: ARCHITECT", "commands/orchestrate.md", r"^### ARCHITECT Phase", True),
    ("vth phase headings: CODE", "commands/orchestrate.md", r"^### CODE Phase", True),
    ("vth phase headings: TEST", "commands/orchestrate.md", r"^### TEST Phase", True),
    ("vth nested cycle creates its sub-feature task", "reference/rePACT/nested-cycle.md", r"TaskCreate.*Sub-feature task", True),
    ("vth plan-mode creates its planning task", "reference/plan-mode/consultation-and-synthesis.md", r"TaskCreate.*Planning task", True),
    ("vth imPACT keeps the phase re-entry protocol", "commands/imPACT.md", r"Phase Re-Entry Task Protocol", True),
]
PINS += [(f"vth {cmd} session team is platform-provisioned", f"commands/{cmd}.md",
          r"provisioned automatically by the platform", True)
         for cmd in ("orchestrate", "comPACT", "peer-review", "plan-mode")]

SCOPED = "reference/orchestrate/scoped-orchestration.md"
NESTED = "reference/rePACT/nested-cycle.md"
PINS += [
    ("vwp-5 comPACT's next steps offer peer-review", "commands/comPACT.md",
     r"(?s)\*\*Next steps\*\*(?:(?!\*\*If blocker reported\*\*).)*/repacted:peer-review", True),
    ("vwp-6 wrap-up cleans up the worktree after merge", "commands/wrap-up.md", r"worktree-cleanup", True),
    ("vwp-7 ATOMIZE creates each scope's worktree with worktree-setup", "protocols/workflows.md",
     r"\*\*ATOMIZE\.\*\* Create a worktree per scope \(`/repacted:worktree-setup`", True),
    ("vwp-7 the scoped ATOMIZE step invokes worktree-setup on the suffix branch", SCOPED,
     r"invoke `/repacted:worktree-setup` with the suffix branch name", True),
    ("vwp-8/16 the scoped CONSOLIDATE section invokes worktree-cleanup", SCOPED,
     r"(?s)### CONSOLIDATE Phase \(Scoped Orchestration Only\)\n(?:(?!\n### ).)*`/repacted:worktree-cleanup`", True),
    ("vwp-9 a nested cycle operates in an isolated worktree on a suffix branch", NESTED,
     r"Operates in isolated worktree\*\* on a suffix branch", True),
    ("vwp-11 comPACT's mission tells the specialist its worktree path", "commands/comPACT.md", r"worktree_path|git worktree at \[", True),
    ("vwp-12 a nested cycle receives its worktree path from the parent", NESTED, r"Receives worktree path\*\* from the parent", True),
    ("vwp-14 worktree-setup has an Edge Cases section", "skills/worktree-setup/SKILL.md", r"^## Edge Cases", True),
    ("vwp-14 worktree-cleanup has an Edge Cases section", "skills/worktree-cleanup/SKILL.md", r"^## Edge Cases", True),
    ("vwp-15 imPACT keeps worktree context for phase re-entry", "commands/imPACT.md", r"worktree", True),
    ("vwp-13 plan-mode does not touch worktree skills", "commands/plan-mode.md", r"worktree-setup|worktree-cleanup", False),
    # vsd: the four detection preconditions are stated in BOTH homes (the protocol and the
    # orchestrate detail), so a change to one without the other fails.
    ("vsd-1 detection runs after PREPARE (protocol)", "protocols/workflows.md", r"After PREPARE \(or an approved plan", True),
    ("vsd-2 an approved plan stands in for PREPARE (protocol)", "protocols/workflows.md", r"approved plan's Preparation section", True),
    ("vsd-3 no input and Scope >= 3 forces PREPARE (protocol)", "protocols/workflows.md", r"Scope ≥ 3 forces PREPARE", True),
    ("vsd-4 no input and Scope < 3 skips detection (protocol)", "protocols/workflows.md", r"below 3 skips detection", True),
    ("vsd-1/2 detection needs PREPARE output or plan content (orchestrate detail)", SCOPED,
     r"Requires PREPARE output or plan content as input", True),
    ("vsd-3 no input and Scope >= 3 forces PREPARE (orchestrate detail)", SCOPED, r"Scope >= 3 → Force PREPARE", True),
    ("vsd-4 no input and Scope < 3 skips detection (orchestrate detail)", SCOPED, r"Scope < 3 → skip detection", True),
]

# The old, looser rules must not come back anywhere a model reads (vsi-13b, vsi-23).
ABSENT_EVERYWHERE = [
    ("vsi-13b no 2-level nesting limit", r"Max nesting: 2|2 levels maximum"),
    ("vsi-23 no deprecated skip reason", r"approved_plan_exists|requirements_explicit|trivial_change|existing_docs_cover_scope"),
]

DROPPED = {
    "vsi-2/4/6/13/13b/19 pact-protocols.md SSOT extracts": "the SSOT file is deleted (the protocol consolidation; each rule now has one home, pinned above",
    "vsi-4 flow wording 'Contracts generated'/'Consolidate phase'": "the lifecycle list was rewritten as prose; the order is pinned by vsi-3 and ATOMIZE/CONSOLIDATE",
    "vsi-10 scope contract <-> rePACT.md cross-links": "rePACT.md is a user-only stub; the executor is nested-cycle.md, linked from ATOMIZE (test_repact_stub_not_model_invoked)",
    "vsi-13 'Max nesting: 1 level' in every implementation agent": "TEAMMATE m6 cut agent bodies to 6 KB; the limit lives once in workflows.md §Specialist Autonomy (pinned above)",
    "vsi-15 memory hook functions exist": "code, not prose; the pin-memory hooks themselves went with the 1.5.0 pin-gate cut",
    "vsi-16 'Agent Teams' / 'Backend-agnostic' executor section": "removed from the scope contract in the protocol consolidation; no model-facing file states it",
    "vsi-18 'Strong (2 pts)' wording": "the weights are now a table in workflows.md §Scope Detection (the threshold is pinned above)",
    "vsi-22 pact-s5-policy.md 'verified interaction channel'": "pact-s5-policy.md is a moved-stub; the rule is algedonic.md's Irreversible row (pinned above)",
    "vth per-command '## Task Hierarchy' lifecycle phrases": "COMMANDS m8b moved the task hierarchies to reference/ and coordination.md with new wording; the hook-emitted lifecycle is tested by the task-lifecycle suites",
    "vth pact-protocols.md scoped hierarchy diagram": "the SSOT file is deleted; scoped task naming is pinned in coordination.md",
    "vwp-1/2 worktree skill files exist with frontmatter": "every SKILL.md's frontmatter is checked by test_skills_structure.py",
    "vwp-3/4/10 orchestrate and comPACT reference worktree-setup; orchestrate propagates worktree_path": "already pinned above as vsi-14",
    "vwp-7/8 ATOMIZE/CONSOLIDATE sections of pact-scope-phases.md": "a moved-stub since the protocol consolidation; the rules are pinned at workflows.md and scoped-orchestration.md",
    "vwp-9/12 rePACT.md suffix branch and worktree path": "rePACT.md is a user-only stub; the nested cycle's text is pinned in nested-cycle.md",
    "vwp-13 plan-mode's reference files": "the script read plan-mode.md only; that file is pinned, its references were never checked",
    "vsd marker-density count (>= 3 matches per file)": "a count of loose matches, not a rule; each of the four preconditions is pinned in both homes instead",
    "vsd pact-scope-detection.md as the second home": "a moved-stub since the protocol consolidation; the protocol home is workflows.md §Scope Detection",
}

MODEL_FACING = ("agents", "commands", "protocols", "reference", "skills")


def read(rel: str) -> str:
    return (PLUGIN_ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rule, home, pattern, present", PINS, ids=[p[0] for p in PINS])
def test_the_rule_holds_in_its_home(rule, home, pattern, present):
    found = re.search(pattern, read(home), re.M)
    assert bool(found) is present, f"{rule}: {pattern!r} {'missing from' if present else 'found in'} {home}"


@pytest.mark.parametrize("rule, pattern", ABSENT_EVERYWHERE, ids=[a[0] for a in ABSENT_EVERYWHERE])
def test_the_retired_rule_is_absent_everywhere(rule, pattern):
    hits = [str(p.relative_to(PLUGIN_ROOT)) for d in MODEL_FACING for p in (PLUGIN_ROOT / d).rglob("*.md")
            if re.search(pattern, p.read_text(encoding="utf-8"))]
    assert not hits, f"{rule}: {hits}"


def test_every_agent_keeps_user_scoped_memory():
    """vsi-17: each agent definition declares `memory: user` in its frontmatter."""
    missing = []
    for agent in sorted((PLUGIN_ROOT / "agents").glob("*.md")):
        head = agent.read_text(encoding="utf-8").split("---", 2)[1]
        if not re.search(r"^memory: user$", head, re.M):
            missing.append(agent.name)
    assert not missing


def test_the_plan_mode_checklist_matches_the_completeness_signals():
    """vsi-21: plan-mode's inline checklist has one item per completeness signal."""
    coordination = read("protocols/coordination.md")
    section = coordination.split("## Completeness Signals", 1)[1].split("\n## ", 1)[0]
    signals = re.findall(r"^\| \d+ \|", section, re.M)
    plan = read("reference/plan-mode/consultation-and-synthesis.md")
    block = plan.split("incompleteness signals:", 1)[1].split("For more details on incompleteness signals", 1)[0]
    items = re.findall(r"^\s*- \[ \]", block, re.M)
    assert len(signals) == 7 and len(items) == len(signals), (len(signals), len(items))


def moved_stub(path: Path) -> bool:
    return bool(re.match(r"\A(?:---\n.*?\n---\n\s*)?> Moved: see ", path.read_text(encoding="utf-8"), re.S))


def test_no_script_names_a_plugin_file_that_does_not_exist():
    """The retired canaries rotted because they named deleted or moved files and nothing ran them.
    A script under repacted/scripts that names a plugin path must name a live file."""
    dirs = {"PROTOCOLS_DIR": "protocols", "COMMANDS_DIR": "commands", "SKILLS_DIR": "skills",
            "HOOKS_DIR": "hooks", "AGENTS_DIR": "agents"}
    missing = []
    for script in sorted(SCRIPTS.iterdir()):
        if not script.is_file():        # __pycache__, left by the tests that load run-tests
            continue
        text = script.read_text(encoding="utf-8")
        for var, sub in dirs.items():
            text = text.replace(f"${var}/", f"{sub}/").replace(f"${{{var}}}/", f"{sub}/")
        for rel in sorted(set(re.findall(r"\b(?:agents|commands|protocols|skills|hooks|reference)/[\w./-]+\.(?:md|py)\b", text))):
            target = PLUGIN_ROOT / rel
            if not target.exists() or (target.suffix == ".md" and moved_stub(target)):
                missing.append(f"{script.name}: {rel}")
    assert not missing, missing
