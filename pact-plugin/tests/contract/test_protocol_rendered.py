"""Contract, PROTOCOL RENDERED: the teachback / HANDOFF / wait contract has ONE source.

hooks/shared/protocol_descriptor.py imports the field names, enums, threshold and vocabulary from
the validating modules and renders every model-read restatement into a marked region
(`<!-- protocol:NAME -->` ... `<!-- /protocol -->`). Three arms:

1. every committed region equals the rendered text byte for byte (the failure names the command);
2. the descriptor's field tuples equal the validators' tuples, so the descriptor cannot drift from
   the gate either, and the schema echoes name every descriptor field;
3. the region parser refuses the shapes that would let a region drift unseen (adversarial rows).
"""
from __future__ import annotations

import re

import pytest

from shared import protocol_descriptor as pd
from shared.amendments import WAKE_VERBS, wake_line
from shared.handoff_schema import (
    HANDOFF_CANONICAL_FIELDS,
    HANDOFF_RECOMMENDED_FIELDS,
    HANDOFF_SCHEMA_ECHO,
    REV_MAX,
)
from shared.intentional_wait import KNOWN_REASONS, KNOWN_RESOLVERS
from shared.marked_regions import RegionError, find_regions, replace_region
from shared.stored_line import REV_MAX as STORED_REV_MAX
from shared.teachback_schema import (
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
    TEACHBACK_REQUIRED_FIELDS,
    TEACHBACK_REQUIRED_SUBKEYS,
    TEACHBACK_SCHEMA_ECHO,
    TEACHBACK_VARIETY_ACK_VALID_VALUES,
)

from tests.contract._surfaces import PLUGIN_ROOT, read

RENDER_COMMAND = "python3 dev/protocol/render.py --write"


# --- 1. regions ---------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(sorted(pd.REGIONS))[:1])
def test_region_equals_the_rendered_text(name: str):
    rel, render = pd.REGIONS[name]
    regions = find_regions(read(PLUGIN_ROOT / rel))
    assert name in regions, f"{rel} has no region {name!r}; add the markers, then run: {RENDER_COMMAND}"
    assert regions[name].body == render(), (
        f"{rel} region {name!r} differs from protocol_descriptor; run: {RENDER_COMMAND}")


def test_every_region_file_parses_cleanly():
    for rel in {rel for rel, _ in pd.REGIONS.values()}:
        find_regions(read(PLUGIN_ROOT / rel))   # raises RegionError on a malformed file


# History, not contract: these describe past payload shapes and are exempt from the hand-copy rule.
HISTORY = {
    "skills/multi-agent-collaboration/refinement-log.md",
    "skills/pact-field-notes/references/hooks-and-harness.md",
    "skills/pact-field-notes/references/task-store-and-harvest.md",
    "skills/pact-field-notes/protocols/live-reprobe-checklist.md",
}


def model_read_files() -> list:
    """Every loaded surface plus every reference a surface points at (skills/**, reference/**)."""
    paths = [*PLUGIN_ROOT.glob("agents/*.md"), *PLUGIN_ROOT.glob("commands/*.md"),
             *PLUGIN_ROOT.glob("protocols/*.md"), *PLUGIN_ROOT.rglob("reference/**/*.md"),
             *PLUGIN_ROOT.rglob("skills/**/*.md")]
    return sorted(p for p in paths if p.is_file() and p.relative_to(PLUGIN_ROOT).as_posix() not in HISTORY)


def test_no_hand_copy_of_the_contract_outside_a_region():
    """A line naming CONTRACT_KEYS_MAX+ contract keys outside a region is a restatement the
    descriptor does not own; make it a pointer, or move it into a region."""
    offenders = []
    for path in model_read_files():
        text = read(path)
        inside = {i for r in find_regions(text).values() for i in range(r.start, r.end)}
        for i, line in enumerate(text.split("\n")):
            if i not in inside and sum(1 for k in pd.CONTRACT_KEYS if k in line) >= pd.CONTRACT_KEYS_MAX:
                offenders.append(f"{path.relative_to(PLUGIN_ROOT).as_posix()}:{i + 1}")
    assert not offenders, "hand copies of the contract outside a rendered region:\n  " + "\n  ".join(offenders)


# --- 1b. one channel: a wake names a task and a digest; the task file holds the value -----------

WAKE_VERB_SLOT = "<" + "|".join(WAKE_VERBS) + ">"
LEAD_TEMPLATE = re.compile(r"\[team-lead→<(\w+)>\] [^`\"]{0,90}")
ID_SLOTS = ("<id>", "<A_id>")
# Every file that teaches the wake. A carrier that loses its line fails the second test below.
WAKE_CARRIERS = ("skills/teammate/references/teammate-waiting.md",)
# Wording of the retired second channel, lower case, matched on text with backticks and line
# wraps removed so a re-wrap or an inline-code rendering cannot hide one.
RETIRED_CHANNEL = ("rejection-payload", "payload verbatim", "carrying the rejection payload",
                   "carrying the same payload", "content-carrying", "dual-channel delivery",
                   "corrections ride the message", "redundant confirm")


def _flat(text: str) -> str:
    return " ".join(text.replace("`", "").split())


@pytest.mark.parametrize("verb", list(WAKE_VERBS)[:1])
def test_wake_line_equals_what_amendments_builds(verb: str):
    shown = (pd.WAKE_LINE.replace("<teammate>", "mate").replace("<id>", "7")
             .replace(WAKE_VERB_SLOT, verb).replace("<d>", "0123456789ab"))
    assert WAKE_VERB_SLOT in pd.WAKE_LINE and shown == wake_line("mate", "7", verb, "0123456789ab")


def test_every_wake_line_in_prose_is_the_descriptor_line():
    """A lead message template addressed to a placeholder is the wake line, whole, or it is a
    second channel: a value after the task id, another verb, or a missing digest all fail."""
    allowed = {pd.WAKE_LINE.replace("<id>", slot).replace(WAKE_VERB_SLOT, verb)
               for slot in ID_SLOTS for verb in (WAKE_VERB_SLOT, *WAKE_VERBS)}
    offenders, carriers = [], set()
    for path in model_read_files():
        rel = path.relative_to(PLUGIN_ROOT).as_posix()
        for hit in LEAD_TEMPLATE.finditer(" ".join(read(path).split())):
            line = hit.group(0).replace(f"<{hit.group(1)}>", "<teammate>", 1)
            if any(line.startswith(wake) for wake in allowed):
                carriers.add(rel)
            else:
                offenders.append(f"{rel}: {hit.group(0)}")
    assert not offenders, "lead message templates that are not the wake line:\n  " + "\n  ".join(offenders)
    assert carriers == set(WAKE_CARRIERS), sorted(carriers ^ set(WAKE_CARRIERS))


def test_no_prose_tells_the_lead_to_carry_a_value_in_a_message():
    files = model_read_files()
    assert len(files) >= 100, f"the sweep reached only {len(files)} files"
    assert any("on disk, digest" in _flat(read(p)) for p in files), "the sweep matched nothing it should"
    offenders = [f"{p.relative_to(PLUGIN_ROOT).as_posix()}: {phrase}" for p in files
                 for phrase in RETIRED_CHANNEL if phrase in _flat(read(p)).lower()]
    assert not offenders, "retired second-channel wording:\n  " + "\n  ".join(offenders)


# --- 2. descriptor vs validators ----------------------------------------------------------------


# --- 3. the region parser -----------------------------------------------------------------------

GOOD = "a\n<!-- protocol:x -->\nbody\n<!-- /protocol -->\nz"


