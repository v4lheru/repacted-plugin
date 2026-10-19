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

@pytest.mark.parametrize("name", sorted(pd.REGIONS))
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


@pytest.mark.parametrize("verb", WAKE_VERBS)
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


def test_the_wait_and_the_readers_name_every_directive_carrier():
    assert pd.WAIT_COMMAND == "repact-wait <id> --until completed,rejection,amend"
    assert pd.REJECTION_READER == "repact inspect <id> --key rejection"
    assert pd.AMEND_READER == "repact inspect <id> --key amend_<n>"
    skill = read(PLUGIN_ROOT / "skills/teammate/SKILL.md")
    assert "repact submit" in skill and pd.REJECTION_READER in skill and pd.AMEND_READER in skill
    waiting = read(PLUGIN_ROOT / "skills/teammate/references/teammate-waiting.md")
    assert pd.WAIT_COMMAND in waiting  # Teams mode only: a subagent never waits


# --- 2. descriptor vs validators ----------------------------------------------------------------

def test_teachback_fields_equal_the_validator():
    tb = pd.PROTOCOL.teachback
    assert tb.required_names() == TEACHBACK_REQUIRED_FIELDS
    rr = next(f for f in tb.fields if f.name == "reasoning_reconstruction")
    assert tuple(k for k, _ in rr.subkeys) == TEACHBACK_REQUIRED_SUBKEYS
    assert str(TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN) in rr.required_when
    ack = next(f for f in tb.fields if f.name == "variety_acknowledgment")
    for value in TEACHBACK_VARIETY_ACK_VALID_VALUES:
        assert f'"{value}"' in ack.subkeys[0][1]
    for name in TEACHBACK_REQUIRED_FIELDS:
        assert name in TEACHBACK_SCHEMA_ECHO or name == "variety_acknowledgment"


def test_handoff_fields_equal_the_validator():
    ho = pd.PROTOCOL.handoff
    names = ho.field_names()
    assert names[:2] == ("rev", "amendments")
    assert names[2:2 + len(HANDOFF_CANONICAL_FIELDS)] == HANDOFF_CANONICAL_FIELDS
    assert tuple(f.name for f in ho.fields if not f.required) == HANDOFF_RECOMMENDED_FIELDS
    assert pd.HANDOFF_REV_MAX == REV_MAX == STORED_REV_MAX
    for name in HANDOFF_CANONICAL_FIELDS:
        assert name in HANDOFF_SCHEMA_ECHO


def test_wait_vocabulary_equals_the_validator():
    by_name = {f.name: f for f in pd.PROTOCOL.wait.fields}
    assert set(by_name["reason"].values) == KNOWN_REASONS
    assert set(by_name["expected_resolver"].values) == KNOWN_RESOLVERS
    assert pd.WAIT_REASON_DEFAULT in KNOWN_REASONS and pd.WAIT_RESOLVER_DEFAULT in KNOWN_RESOLVERS
    assert tuple(by_name) == ("reason", "expected_resolver", "since", "covers_since")


# --- 3. the region parser -----------------------------------------------------------------------

GOOD = "a\n<!-- protocol:x -->\nbody\n<!-- /protocol -->\nz"


def test_parser_reads_a_region_and_replaces_only_its_body():
    assert find_regions(GOOD)["x"].body == "body"
    assert replace_region(GOOD, "x", "new\nlines") == "a\n<!-- protocol:x -->\nnew\nlines\n<!-- /protocol -->\nz"


def test_parser_accepts_a_python_comment_marker():
    text = "# <!-- protocol:x -->\nX = 1\n# <!-- /protocol -->"
    assert find_regions(text)["x"].body == "X = 1"


@pytest.mark.parametrize("text", [
    "<!-- protocol:x -->\nbody",                                        # unterminated
    "<!-- protocol:x -->\n<!-- protocol:y -->\nb\n<!-- /protocol -->",   # nested
    "<!-- protocol:x -->\na\n<!-- /protocol -->\n<!-- protocol:x -->\nb\n<!-- /protocol -->",  # repeated
    "body\n<!-- /protocol -->",                                         # close without open
], ids=["unterminated", "nested", "repeated", "stray-close"])
def test_parser_refuses_malformed_markers(text: str):
    with pytest.raises(RegionError):
        find_regions(text)


def test_crlf_body_does_not_equal_lf_rendering():
    assert find_regions("<!-- protocol:x -->\r\nbody\r\n<!-- /protocol -->\r\n")["x"].body != "body"


def test_a_marker_inside_a_code_fence_is_still_a_marker():
    """A fenced example of the markers would open a region: the files must not carry one."""
    text = "```\n<!-- protocol:x -->\nbody\n<!-- /protocol -->\n```"
    assert "x" in find_regions(text)
