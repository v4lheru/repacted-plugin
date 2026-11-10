"""
Executable tripwires over the Q5 EXEMPTION POLICY.

The denominator is now hook-emitted and therefore author-proof: no command
file can move it by writing prose. What CAN still move it is the exemption
sets — they decide which dispatches are sites at all. This file converts the
prose rules guarding them into assertions that can fail.

Each pin below names, in its own docstring, the MUTATION that reddens it. A
pin whose mutation cannot be named is not a pin; it is coverage-shaped
decoration, and this file exists because that failure has already shipped
twice in this area.

NOT PINNED FOR THE DENOMINATOR — the auditor's `metadata.type` state. The
denominator keys on `is_teachback_exempt`, which has no metadata surface, so
that fact is irrelevant to it. It IS pinned lower down for a different
consumer: the harvest propagation rule keeps the Working Memory block away
from an auditor that has not reported, which is only meaningful while auditors
stay non-exempt, which depends on that dispatch carrying no `type`.
"""
import ast
import json
import re
from pathlib import Path

import pytest

from shared.intentional_wait import (  # noqa: E402
    SELF_COMPLETE_EXEMPT_AGENT_TYPES,
    TEACHBACK_EXEMPT_AGENT_TYPES,
)


INTENTIONAL_WAIT_SRC = (
    Path(__file__).parent.parent / "hooks" / "shared" / "intentional_wait.py"
)


def _assigned_value_node(source, target_name):
    """Return the AST value node assigned to a module-level `target_name`.

    Handles both `X = ...` and the annotated `X: frozenset = ...` form.
    """
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == target_name:
                return node.value
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == target_name:
                    return node.value
    raise AssertionError(
        f"no module-level assignment to {target_name!r} found -- the constant "
        f"was renamed or moved, which this file's pins depend on"
    )


def _write_team_config(teams_dir, team_name, members):
    team_dir = Path(teams_dir) / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    (team_dir / "config.json").write_text(
        json.dumps({"team_name": team_name, "members": members}),
        encoding="utf-8",
    )
    return str(teams_dir)


@pytest.fixture
def teams_dir(tmp_path):
    d = tmp_path / "teams"
    d.mkdir()
    return str(d)


class TestTheTwoExemptionSetsAreNotAliased:
    """The source comment says: "DO NOT recouple by ALIASING to the prior
    constant." Aliasing is an IDENTITY claim, so the test must be one.

    MUTATION THAT REDDENS: in `shared/intentional_wait.py`, replace the
    `TEACHBACK_EXEMPT_AGENT_TYPES = frozenset({...})` literal with
    `TEACHBACK_EXEMPT_AGENT_TYPES = SELF_COMPLETE_EXEMPT_AGENT_TYPES`.

    WHY IDENTITY AND NOT CONTENTS. The two frozensets hold identical
    contents today, so a content-shaped pin (`T == S`, or comparing either
    against a literal) stays GREEN on an aliased pair and detects nothing.
    `is not` is the only form that separates "two sets that happen to
    match" from "one set under two names".

    AND WHY NOT A PIN ON THE CONTENTS BEING EQUAL TODAY. That would redden
    on a change the module explicitly invites -- its docstring supports
    future divergence, a rote-only agentType joining one set with a one-line
    change. A test that fires on an intended edit is worse than no test, so
    the content relationship is recorded in this paragraph and asserted
    nowhere. A previous test did assert it, by binding a local alias and
    comparing that local against the constant it had just been assigned
    from; both arms were true by the assignment and no change to this module
    could redden either. Do not re-add that shape.

    TWO PINS, TWO DIFFERENT OBSERVATIONS -- neither subsumes the other:
    - `test_the_two_sets_are_distinct_objects` reads the IMPORTED objects
      and catches `T = S`.
    - `test_the_teachback_set_is_not_derived_from_the_self_complete_set`
      reads the SOURCE and additionally catches `T = frozenset({*S})`,
      which the identity pin cannot see.
    """

    def test_the_two_sets_are_distinct_objects(self):
        assert TEACHBACK_EXEMPT_AGENT_TYPES is not SELF_COMPLETE_EXEMPT_AGENT_TYPES, (
            "TEACHBACK_EXEMPT_AGENT_TYPES has been ALIASED to "
            "SELF_COMPLETE_EXEMPT_AGENT_TYPES. These are two policy surfaces "
            "answering different questions -- 'should this owner be dispatched "
            "through a teachback gate?' vs 'may this owner self-complete?' -- "
            "and aliasing them means a future change to either silently moves "
            "the other, including the Q5 denominator."
        )


ORCHESTRATOR_PERSONA = Path(__file__).parent.parent / "agents" / "pact-orchestrator.md"
COMMANDS_DIR = Path(__file__).parent.parent / "commands"


def _metadata_keys(literal):
    """KEY NAMES of a metadata literal, read TEXTUALLY rather than parsed.

    The only question asked of these literals is "is a `type` key present", and
    answering it does not require the literal to be valid JSON. `json.loads`
    used to answer it, and RAISED on two ordinary spellings: a single-quoted
    dict, and the `"halt"|"alert"` alternation this repo already uses to
    document an algedonic dispatch. A crash inside a test that scans OTHER files
    diagnoses the scanner rather than the file that broke it -- and swallowing
    that crash would have converted it into a silent blind spot, which is worse
    than the crash. Reading the text answers the question on all four spellings
    without needing either.

    Keys are anchored to `{` or `,` so a `type` substring inside a VALUE is not
    read as a key: `{"note": "see type: below"}` yields `note` alone. Without
    that anchor this would redden on a correct dispatch, and a guard that fires
    on correct work is deleted by the first person it annoys.

    Newlines are ordinary whitespace to this pattern, so a literal the caller
    read across several lines needs no special casing here.
    """
    return set(re.findall(r'[{,]\s*["\']?(\w+)["\']?\s*:', literal))


_METADATA_START = re.compile(r"metadata\s*(?:=|:)\s*\{")


def _metadata_literals(text):
    """Yield (offset, literal) for every metadata literal in `text`.

    Brace-balanced and newline-tolerant, so a multiline `TaskCreate(...)` is
    read whole. Scanning the WHOLE FILE rather than pre-cut blocks is what makes
    that safe: a block boundary can never cut a literal in half, because the
    literal is found before any block is considered.

    `literal` is None when the braces never close. The caller must FAIL on that
    rather than skip it -- a literal that cannot be read must not pass as clean.
    """
    for m in _METADATA_START.finditer(text):
        i = text.index("{", m.start())
        depth = 0
        for j in range(i, len(text)):
            depth += (text[j] == "{") - (text[j] == "}")
            if depth == 0:
                yield m.start(), text[i:j + 1]
                break
        else:
            yield m.start(), None


def _enclosing_block(text, offset):
    """The blank-line-delimited block containing `offset`.

    This is the ASSOCIATION unit: a metadata literal belongs to the dispatch
    written around it. Line-scoping was a cruder proxy for the same idea, and it
    lost every multiline call -- which is the house style for substantial
    dispatches in the files this scans.
    """
    start = 0
    for m in re.finditer(r"\n[ \t]*\n", text):
        if m.start() > offset:
            return text[start:m.start()]
        start = m.end()
    return text[start:]


PROPAGATE_SENTENCE = "Then propagate the store into the Working Memory block."
PROPAGATION_PARAGRAPH_MARKER = "may appear only at a boundary"
PROPAGATING_COMMANDS = frozenset({"orchestrate", "wrap-up"})

# Any literal a command hands an agent. NOT narrowed to a harvest dispatch
# description -- see _propagating_sites.
_CARRIER_RE = re.compile(r'(?:description|message)="((?:[^"\\]|\\.)*)"', re.S)

_WRAP_UP = COMMANDS_DIR / "wrap-up.md"
# wrap-up's step-6 branches, in document order. The propagation decision is
# taken PER BRANCH, so these labels are the slice boundaries the branch arm
# reads. They are also the labels tests/test_wrapup_datasafety_contracts.py
# slices on, so a rename moves both files together or reddens both.
_WRAP_UP_BRANCHES = (
    "**A — PR is MERGED**",
    "**B — No PR exists**",
    "**C — PR exists but is not merged**",
)


def _propagating_sites():
    """Command stems that instruct a propagation, one entry per instructing
    site (a list, so the count is visible to the caller).

    THE UNIT IS THE SITE, NOT THE COMMAND, and that is a correction rather than
    a refinement. A command can hold several propagation sites and they need not
    agree: `wrap-up` propagates on the two step-6 branches that END the arc, and
    must not on the branch that preserves an open PR. While the unit was the
    command, membership of the set asserted `wrap-up` propagates FULL STOP --
    true of two branches, false of the third, and the third is the one that
    respawns reviewers. A command-granular container cannot hold a branch-level
    truth, so the container changed.

    THE CARRIER IS NOT PINNED TO A DISPATCH DESCRIPTION. The sentence IS the
    instruction; whatever literal carries it propagates. The earlier form also
    required `Follow the ... Harvest workflow` in the SAME literal, which made a
    propagate request sent to an already-running secretary invisible here -- and
    that request is exactly what a per-branch decision needs, because the branch
    is not known until long after the harvest is dispatched.
    """
    stems = []
    for path in sorted(COMMANDS_DIR.glob("*.md")):
        for literal in _CARRIER_RE.findall(path.read_text(encoding="utf-8")):
            if PROPAGATE_SENTENCE in literal:
                stems.append(path.stem)
    return stems


def _wrap_up_branch_slices():
    """wrap-up's three step-6 branches, keyed by label.

    Raises on a label that is absent, duplicated or out of order rather than
    returning a partial map. An absence assertion over an empty slice passes for
    the wrong reason, and a slice that ran to the wrong end reports a confident
    wrong answer -- both read as agreement.
    """
    text = _WRAP_UP.read_text(encoding="utf-8")
    starts = []
    for label in _WRAP_UP_BRANCHES:
        hits = [m.start() for m in re.finditer(re.escape(label), text)]
        assert len(hits) == 1, (
            f"expected exactly one {label!r} in {_WRAP_UP.name}, found "
            f"{len(hits)}. These labels are the branch arm's slice boundaries: "
            "re-anchor it on whatever now delimits the branches. Do not delete "
            "the arm and do not let the slice run to the wrong end."
        )
        starts.append(hits[0])
    assert starts == sorted(starts), (
        f"the step-6 branch labels are out of document order in "
        f"{_WRAP_UP.name}: {starts}"
    )
    slices = {}
    for i, (label, start) in enumerate(zip(_WRAP_UP_BRANCHES, starts)):
        # The last branch ends at the next top-level heading, not at EOF --
        # otherwise its slice swallows every later step and an absence
        # assertion over it is answering a question about the whole file.
        nxt = text.find("\n## ", start)
        end = starts[i + 1] if i + 1 < len(starts) else (
            nxt if nxt != -1 else len(text)
        )
        slices[label] = text[start:end]
    return slices


