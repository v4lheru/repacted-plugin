"""One shared dispatch reference (CLEANUP-B, backlog: 'one shared dispatch-template reference').

reference/dispatch/teachback-gated-dispatch.md holds the ONE copy of the Task A/B skeleton, the
variety stamp, the wiring and the canonical spawn form (orchestrate's superset, lead ruling
amend_2 on #192). Each consumer command keeps its mission fields inline plus the trigger line,
and bound-approval prompts stay inline in their commands.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
COMMANDS = PLUGIN_ROOT / "commands"
REFERENCE = PLUGIN_ROOT / "reference" / "dispatch" / "teachback-gated-dispatch.md"
TRIGGER = "Read reference/dispatch/teachback-gated-dispatch.md before your first dispatch this session."
CONSUMERS = ["orchestrate", "comPACT", "peer-review", "plan-mode"]

# The superset lines every dispatch now carries. Before CLEANUP-B, comPACT, peer-review and
# plan-mode each lacked some of them; the ruling made the shorter skeletons inherit them.
SUPERSET = [
    "# ORDER: Task A and Task B may be created in either order",
    "understanding, most_likely_wrong, least_confident_item, first_action, variety_acknowledgment (an OBJECT)",
    "send the notify SendMessage with the task id and the digest the stored line printed, never the teachback itself (teammate skill, Teachback step 5)",
    "SET intentional_wait{reason=awaiting_lead_completion, expected_resolver=lead, since=<canonical_since() output>, covers_since=<the same value as since>}",
    "wake; read it with repact-payload <id> --key rejection and revise on this same task.",
    "identified by its subject (the '{name}: {mission}' pattern)",
    "FIRST claim this task (TaskUpdate status=in_progress) before any implementation tool-use",
    "DO NOT mark this task completed yourself.",
    "send notify SendMessage with task id, rev and digest (teammate skill, HANDOFF step 6)",
    "Upstream: TEACHBACK Task #{A_id}.",
    '"novelty_rationale":     "<1-sentence: the shape of this dispatch\'s novelty — never what you expect to find>"',
    "WITHHELD: ignorance-dependent dispatch",
    'TaskUpdate(B_id, owner="{teammate-name}", addBlockedBy=[A_id])',
    'TaskUpdate(A_id, owner="{teammate-name}")',
    'prompt="YOUR PACT ROLE: teammate ({teammate-name}).\\n\\nYou are joining team {team_name}. As your FIRST action, Invoke Skill(\\"repacted:teammate\\")',
    "**Auditor signal-tasks**",
    "**Secretary**",
    "**imPACT force-termination**",
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("line", SUPERSET, ids=[s[:40] for s in SUPERSET])
def test_the_reference_holds_each_superset_line(line):
    assert line in read(REFERENCE), line


def test_the_reference_holds_one_skeleton_and_one_spawn_form():
    text = read(REFERENCE)
    assert text.count("A_id = TaskCreate(") == 1 and text.count("B_id = TaskCreate(") == 1
    assert text.count('prompt="YOUR PACT ROLE: teammate (') == 1


@pytest.mark.parametrize("name", CONSUMERS)
def test_each_consumer_carries_the_trigger_line_once(name):
    assert read(COMMANDS / f"{name}.md").count(TRIGGER) == 1


@pytest.mark.parametrize("name", CONSUMERS)
def test_no_consumer_keeps_its_own_copy_of_the_skeleton_or_spawn_form(name):
    text = read(COMMANDS / f"{name}.md")
    assert "A_id = TaskCreate(" not in text and "B_id = TaskCreate(" not in text
    assert 'prompt="YOUR PACT ROLE: teammate (' not in text
    assert "# A-FIRST ORDERING" not in text


@pytest.mark.parametrize("name", CONSUMERS)
def test_each_dispatch_site_keeps_its_mission_fields(name):
    """Every `follow the steps for [Teachback-Gated Dispatch]` site names its teammate and type."""
    sites = [line for line in read(COMMANDS / f"{name}.md").splitlines()
             if "follow the steps for [Teachback-Gated Dispatch]" in line]
    assert sites
    for line in sites:
        assert re.search(r'`name="[^"]+"`, `subagent_type="pact-[^"]+"`', line), line


def test_bound_approval_prompts_stay_in_their_commands():
    """Merge authorization stays inline (condition c): the reference carries none of it."""
    assert "AskUserQuestion" not in read(REFERENCE)
    for name in ("orchestrate", "peer-review"):
        assert re.search(r"AskUserQuestion.*merge authorization", read(COMMANDS / f"{name}.md"))


def test_every_relative_link_in_the_reference_resolves():
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", read(REFERENCE)):
        assert (REFERENCE.parent / target).resolve().is_file(), target


# --- rev 2 (architect-pact S1): the mission fields are now the only dispatch text left inline ---
# Each command's dispatch site must still carry the field that gives it teeth. The anchor must sit
# INSIDE a dispatch site: from a `follow the steps for [Teachback-Gated Dispatch]` lead-in to the
# next heading or the next site.
MISSION_FIELDS = {
    "plan-mode": "PLANNING CONSULTATION ONLY",   # the no-implementation line
    "peer-review": "Review this PR",
    "comPACT": "boundary context",
    "orchestrate": "research {feature}",
}
LEAD_IN = "follow the steps for [Teachback-Gated Dispatch]"


def site_regions(text: str) -> list[str]:
    lines = text.splitlines()
    regions = []
    for i, line in enumerate(lines):
        if LEAD_IN in line:
            end = next((j for j in range(i + 1, len(lines))
                        if lines[j].startswith("#") or LEAD_IN in lines[j]), len(lines))
            regions.append("\n".join(lines[i:end]))
    return regions


def mission_field_problems(name: str, text: str) -> list[str]:
    anchor = MISSION_FIELDS[name]
    if not any(anchor in region for region in site_regions(text)):
        return [f"{name}: no dispatch site carries its mission field {anchor!r}"]
    return []


@pytest.mark.parametrize("name", sorted(MISSION_FIELDS))
def test_each_dispatch_site_keeps_its_mission_field(name):
    assert mission_field_problems(name, read(COMMANDS / f"{name}.md")) == []


@pytest.mark.parametrize("name", sorted(MISSION_FIELDS))
def test_sabotage_the_check_fails_when_the_mission_field_is_removed(name):
    """Sabotage row: the same text with the field deleted must be caught."""
    text = read(COMMANDS / f"{name}.md").replace(MISSION_FIELDS[name], "")
    assert mission_field_problems(name, text) != []


@pytest.mark.parametrize("name", sorted(MISSION_FIELDS))
def test_sabotage_the_field_outside_a_site_does_not_count(name):
    """Sabotage row: the field moved out of every dispatch site (to the file's end) must be caught."""
    text = read(COMMANDS / f"{name}.md").replace(MISSION_FIELDS[name], "")
    text += f"\n\n## Elsewhere\n\n{MISSION_FIELDS[name]}\n"
    assert mission_field_problems(name, text) != []


# --- rev 2 (architect-pact N1): the per-site steps use the skeleton's own wiring, in its order ---

def _wiring_calls(text: str) -> list[str]:
    return re.findall(r"TaskUpdate\((?:A_id|B_id), [^)]*\)", text)


def test_the_site_steps_wire_exactly_as_the_skeleton_does():
    text = read(REFERENCE)
    skeleton = text.split("## Dispatch sequence", 1)[1].split("## Steps at each dispatch site", 1)[0]
    steps = text.split("## Steps at each dispatch site", 1)[1].split("```", 1)[0]
    assert _wiring_calls(steps) == _wiring_calls(skeleton), (_wiring_calls(steps), _wiring_calls(skeleton))


def test_task_a_gets_its_owner_only_and_task_b_keeps_the_dispatch_edge():
    """The lifecycle gate writes A.blocks=[B] (D4), so Task A's write is owner-only. Task B keeps
    owner + addBlockedBy in one write: the variety deny and the dispatch journal row key on it."""
    calls = _wiring_calls(read(REFERENCE))
    on_a = [c for c in calls if c.startswith("TaskUpdate(A_id,")]
    on_b = [c for c in calls if c.startswith("TaskUpdate(B_id,") and "owner=" in c]
    assert on_a and all("owner=" in c and "addBlocks" not in c for c in on_a), on_a
    assert on_b and all("addBlockedBy=[A_id]" in c for c in on_b), on_b
    persona = read(PLUGIN_ROOT / "agents" / "pact-orchestrator.md")
    assert 'TaskUpdate(A_id, owner="{name}")' in persona and "addBlocks=[B_id]" not in persona
    assert 'TaskUpdate(B_id, owner="{name}", addBlockedBy=[A_id])' in persona
    assert "end user will read names the reader, the register and a length cap" in read(REFERENCE)
    assert "end user will read names the reader, the register and a length cap" in read(REFERENCE)
