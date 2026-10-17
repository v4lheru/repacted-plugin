"""Structural pin tests for the per-dispatch `variety_assessed` emission sites.

Every dispatch path that stamps `metadata.variety` on a Task B also writes a
`variety_assessed` journal event keyed on that task's id, carrying the four
dimension scores and the total, with the TOP-LEVEL `"scope": "dispatch"`
discriminator present. The FEATURE-level assessment event carries NO `scope`
field, and journal readers key on that difference (session_state latest-ts
feature-level derivation and the wrap-up arc start exclude dispatch-marked
events). A hand-written emission that omits the field degrades to
feature-level — the legacy behavior — so the discriminator's presence on
every per-dispatch site is load-bearing and pinned here at the block level.

THE SLICE RULE, stated beside the arms that use it: the guarded unit is a
fenced block that contains a `variety_assessed` journal write. A per-dispatch
block MUST carry the discriminator in its JSON body AND a trap label naming
its own file; a feature-level block MUST carry neither a discriminator NOR a
`scope` key at the top level of its JSON body. File-wide token counts would
let a discriminator sitting in some unrelated prose satisfy an arm, so every
assertion is block-scoped.

COUNTS ARE FLOORS AND THEY MOVE ONLY DELIBERATELY. The per-dispatch site
count per file reflects the dispatch shapes the file teaches: orchestrate
carries four (PREPARE/ARCHITECT/CODE/TEST), comPACT two (concurrent +
single), peer-review/plan-mode/rePACT one each. A repair that fixed one
site of a file would leave the others dark while a presence-only check
stayed green, so the floors are exact counts and refuse movement in either
direction.
"""
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
COMMANDS = PLUGIN_ROOT / "commands"

# The per-file census: (file name, per-dispatch site count, feature-level
# site count). orchestrate's feature-level emission predates this change and
# is pinned UNCHANGED — its block must carry no discriminator.
#
# COMMANDS (m8b) moved counts deliberately: where the EVENTS hook (m8a) now
# emits variety_assessed from the Task B stamp, the hand-written block was
# deleted and its row drops to 0. A 0 row is an ABSENCE rule: a hand-written
# block reappearing beside the hook would journal the event twice.
SITES = [
    ("orchestrate.md", 0, 0),
    ("comPACT.md", 0, 0),
    ("peer-review.md", 0, 0),
    ("plan-mode.md", 0, 0),
    ("rePACT.md", 0, 0),  # a stub; the nested cycle lives in reference/rePACT/
]

DISCRIMINATOR = '"scope": "dispatch"'
WRITE_FLAG = "--type variety_assessed"
TRAP_PREFIX = "[JOURNAL WRITE FAILED] "


def _fenced_blocks(text: str) -> list[str]:
    """Return the fenced ``` blocks of a markdown file, fence-stripped."""
    return re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.DOTALL)


def _variety_blocks(name: str) -> list[str]:
    """The fenced blocks of a command file that write variety_assessed."""
    text = (COMMANDS / name).read_text()
    return [b for b in _fenced_blocks(text) if WRITE_FLAG in b]


@pytest.mark.parametrize(
    "name,dispatch_count,feature_count", SITES,
    ids=[s[0] for s in SITES],
)
class TestEmissionSiteCensus:
    def test_block_census_matches(self, name, dispatch_count, feature_count):
        """The variety_assessed-writing blocks split exactly as declared:
        `dispatch_count` carrying the discriminator and `feature_count`
        without it. A new dispatch shape moves the census deliberately or
        fails here; a repair that fixes one site of a multi-site file fails
        here while the others still pass their own file."""
        blocks = _variety_blocks(name)
        marked = [b for b in blocks if DISCRIMINATOR in b]
        unmarked = [b for b in blocks if DISCRIMINATOR not in b]
        assert len(marked) == dispatch_count, (
            f"{name}: expected {dispatch_count} dispatch-marked "
            f"variety_assessed blocks, found {len(marked)}"
        )
        assert len(unmarked) == feature_count, (
            f"{name}: expected {feature_count} feature-level "
            f"variety_assessed blocks, found {len(unmarked)}"
        )

    # COMMANDS (m8b) RETIRED three per-block arms here (trap label, stamp-mirror
    # shape, no discriminator on a feature block). Every row above is now 0, so
    # they iterated over no blocks and passed on empty input, which is a false
    # all-clear rather than a check. Their subject moved: the hook
    # (hooks/journal_emitter.py) now writes these events, and
    # tests/test_journal_emitter.py pins the rows it writes against m0's own.


def test_no_command_hand_writes_variety_assessed():
    """Tree-wide absence, since the hook emits the event: a hand-written block
    in ANY command file, not only the five the census names, would journal it
    twice. Non-vacuous: the glob must reach the command files."""
    files = sorted(COMMANDS.glob("*.md"))
    assert len(files) >= 10, f"the command glob reached {len(files)} files"
    offenders = [p.name for p in files if _variety_blocks(p.name)]
    assert offenders == [], f"hand-written variety_assessed blocks in {offenders}"


class TestRecoveryReadLastUsesTheFilteredForm:
    """The post-compaction recovery read must exclude per-dispatch mirrors.

    orchestrate.md's state-recovery step reads the feature's
    `variety_assessed` via `read-last`, and per-dispatch mirrors land LATER
    in the journal than the feature-level assessment — without the
    `--exclude-scope dispatch` flag the reverse scan returns a Task-B
    dispatch total as the feature assessment, silently corrupting the
    variety-band gates rebuilt after compaction. The prose and the CLI
    flag are pinned against each other so neither can drift alone.
    """

    def test_recovery_read_carries_the_exclusion(self):
        text = (COMMANDS / "orchestrate.md").read_text()
        assert (
            "read-last --session-dir '{session_dir}' --type "
            "variety_assessed --exclude-scope dispatch" in text
        ), (
            "orchestrate.md's post-compaction variety recovery must use "
            "the --exclude-scope dispatch form; an unfiltered read-last "
            "returns a per-dispatch mirror as the feature assessment"
        )

    def test_the_flag_name_matches_the_cli_registration(self):
        """Prose/CLI spelling agreement: the flag the prose names is the
        flag the parser registers, so a rename on one side reddens here
        rather than silently stranding the other."""

        from shared import session_journal as sj

        parser = sj._build_cli()
        # argparse exposes each subparser's option strings; assert the
        # read-last subparser carries --exclude-scope.
        actions_by_command = {
            choice: sub
            for choice, sub in parser._subparsers._group_actions[0].choices.items()  # noqa: SLF001
        }
        last = actions_by_command["read-last"]
        option_strings = {
            opt for a in last._actions for opt in a.option_strings  # noqa: SLF001
        }
        assert "--exclude-scope" in option_strings


class TestDiscriminatorConstant:
    """The code-side spelling and the prose-side spelling agree. The
    consumers import VARIETY_ASSESSED_DISPATCH_SCOPE; the emission sites
    write the literal into journal events. A rename that moved one and not
    the other would silently degrade every per-dispatch event to
    feature-level — the exact corruption the discriminator exists to
    prevent — so the two spellings are pinned against each other."""

    def test_constant_value_matches_the_prose_literal(self):

        from shared.constants import VARIETY_ASSESSED_DISPATCH_SCOPE

        assert VARIETY_ASSESSED_DISPATCH_SCOPE == "dispatch"
