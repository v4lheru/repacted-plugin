"""Contract 5, IDENTITY: one plugin name and one spelling of each CLAUDE.md marker.

repacted-fork.md §8.5. Behaviour at each namespace-comparing site is tested in
tests/test_plugin_identity.py; this contract guards the TEXT around them:
  - PLUGIN_NAME equals the manifest name;
  - every CLAUDE.md managed-block marker written anywhere in the plugin is byte-identical to the
    constant in hooks/shared/claude_md_manager.py, which is what the parser matches;
  - no agent frontmatter declares `skills:`.
"""
from __future__ import annotations

import json
import re

import pytest

from shared import claude_md_manager as cmm
from shared.plugin_identity import PLUGIN_NAME
from tests.contract._surfaces import PLUGIN_ROOT, read, rel

SCANNED_SUFFIXES = {".md", ".py", ".json", ".sh"}


def test_plugin_name_is_the_manifest_name():
    manifest = json.loads(read(PLUGIN_ROOT / ".claude-plugin" / "plugin.json"))
    assert PLUGIN_NAME == manifest["name"]


MARKER_CONSTANTS = {
    cmm.MANAGED_START_MARKER, cmm.MANAGED_END_MARKER,
    cmm.MEMORY_START_MARKER, cmm.MEMORY_END_MARKER,
}  # the PINNED pair went with the 1.5.0 pin-gate cut; a PINNED spelling now reads as stray
MARKER_TEXT = re.compile(r"<!--\s*PACT_(?:MANAGED|MEMORY|MEMORY_PINNED)_(?:START|END)\b[^>]*-->")


def marker_spellings(root=PLUGIN_ROOT) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*")):
        if (not path.is_file() or path.suffix not in SCANNED_SUFFIXES
                or {"tests", "__pycache__"} & set(path.relative_to(root).parts)):
            continue
        for marker in MARKER_TEXT.findall(read(path)):
            out.setdefault(marker, set()).add(rel(path, root))
    return out


def test_the_marker_scan_finds_the_constants():
    assert MARKER_CONSTANTS <= set(marker_spellings())


def test_every_claude_md_marker_is_spelled_as_its_constant():
    stray = {m: sorted(files) for m, files in marker_spellings().items() if m not in MARKER_CONSTANTS}
    assert not stray, f"marker text that the CLAUDE.md parser would not match: {stray}"


# Agent frontmatter carries no `skills:` key (build plan §3 TEAMMATE, C10): a headless `claude -p`
# spawn injects every listed skill body, 25-65k per spawn, and interactive spawns ignore the key.
# The team protocol arrives through the prelude's `Skill("repacted:teammate")` line instead.
# SKILLS_EXEMPT names each agent file whose owner has not removed the key yet; the owner deletes
# its row when it does, and a row whose file no longer has the key fails (KNOWN_STALE pattern).
SKILLS_EXEMPT = {
}
FRONTMATTER_SKILLS = re.compile(r"(?m)^skills:")


def agent_frontmatter(path) -> str:
    text = read(path)
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[4:end] if end != -1 else ""


def agents_with_skills_key(root=PLUGIN_ROOT) -> set[str]:
    return {rel(p, root) for p in sorted((root / "agents").glob("*.md"))
            if FRONTMATTER_SKILLS.search(agent_frontmatter(p))}


def test_no_agent_frontmatter_declares_skills():
    unexpected = sorted(agents_with_skills_key() - set(SKILLS_EXEMPT))
    assert not unexpected, f"`skills:` in agent frontmatter (C10, preloads every body): {unexpected}"


@pytest.mark.parametrize("path", sorted(SKILLS_EXEMPT))
def test_skills_exemptions_are_still_needed(path):
    assert path in agents_with_skills_key(), (
        f"{path} no longer declares skills:; delete its SKILLS_EXEMPT row")


def test_the_skills_scan_reads_frontmatter_only(tmp_path):
    """Guards the scanner: a body line starting `skills:` is not frontmatter."""
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.md").write_text("---\nname: a\nskills:\n  - x\n---\nbody\n")
    (tmp_path / "agents" / "b.md").write_text("---\nname: b\n---\nskills: in prose\n")
    assert agents_with_skills_key(tmp_path) == {"agents/a.md"}
