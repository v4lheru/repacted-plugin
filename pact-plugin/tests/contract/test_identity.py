"""Contract 5, IDENTITY: one plugin name, no stray legacy namespace, one spelling of each CLAUDE.md marker.

repacted-fork.md §8.5. Behaviour at each namespace-comparing site is tested in
tests/test_plugin_identity.py; this contract guards the TEXT around them:
  - PLUGIN_NAME equals the manifest name;
  - `PACT:` used as a namespace (`PACT:<name>`, `PACT:pact-x`, `` `PACT:` ``) appears only where a
    file handles or documents the legacy spelling. Every other use is a stale M1 miss: a model
    reading it would look for a `PACT:` command that this plugin does not register. Messages that
    merely start with the word (`"PACT: this session ..."`) are not namespace uses;
  - every CLAUDE.md managed-block marker written anywhere in the plugin is byte-identical to the
    constant in hooks/shared/claude_md_manager.py, which is what the parser matches.

KNOWN_STALE lists the misses present when the contract was seeded, with the package that owns the
file (build plan §4). The owner removes its row when it fixes the text; a NEW stale use fails.
"""
from __future__ import annotations

import json
import re
from collections import Counter

import pytest

from shared import claude_md_manager as cmm
from shared.plugin_identity import LEGACY_NAMESPACES, PLUGIN_NAME
from tests.contract._surfaces import PLUGIN_ROOT, read, rel

LEGACY = LEGACY_NAMESPACES[0]
NAMESPACE_USE = re.compile(r"(?<![A-Za-z])" + re.escape(LEGACY) + r"(?=[a-z<{`])")
SCANNED_SUFFIXES = {".md", ".py", ".json", ".sh"}

# Files whose job is the legacy spelling: the identity module, the readers that strip it, and the
# docs that record the rename. Uses here are correct and unlimited.
LEGACY_HANDLING = {
    "hooks/shared/plugin_identity.py": "the single source of LEGACY_NAMESPACES",
    "hooks/shared/pact_context.py": "strip_pact_namespace accepts the pre-fork spelling",
    "hooks/bootstrap_gate.py": "the secretary matcher accepts the pre-fork spelling",
    "hooks/shared/HOOK_STDIN_DISCRIMINATORS.md": "documents the legacy discriminator",
    "CHANGELOG.md": "records the rename",
    "NOTICE.md": "records the fork origin",
    "README.md": "names upstream PACT in prose (\"beside PACT: both plugins' hooks\")",
}

# Stale uses present at seeding: (file) -> (count, owner). The owner fixes the text and drops the row.
KNOWN_STALE = {
}


def namespace_uses(root=PLUGIN_ROOT) -> Counter:
    counts: Counter = Counter()
    for path in sorted(root.rglob("*")):
        if (not path.is_file() or path.suffix not in SCANNED_SUFFIXES
                or {"tests", "__pycache__", ".pytest_cache"} & set(path.relative_to(root).parts)
                or path.name.startswith("test_")):
            continue
        hits = len(NAMESPACE_USE.findall(read(path)))
        if hits:
            counts[rel(path, root)] += hits
    return counts


def test_plugin_name_is_the_manifest_name():
    manifest = json.loads(read(PLUGIN_ROOT / ".claude-plugin" / "plugin.json"))
    assert PLUGIN_NAME == manifest["name"]


def test_the_scan_sees_the_legacy_handling_sites():
    """Guards the regex: the namespace strip documents `PACT:pact-<name>`, so it must register."""
    assert namespace_uses()["hooks/shared/pact_context.py"] >= 1


def test_no_new_legacy_namespace_use():
    unexpected = {}
    for path, count in namespace_uses().items():
        if path in LEGACY_HANDLING:
            continue
        allowed = KNOWN_STALE.get(path, (0, ""))[0]
        if count > allowed:
            unexpected[path] = f"{count} use(s), {allowed} allowed"
    assert not unexpected, f"`{LEGACY}` used as a namespace; write `{PLUGIN_NAME}:` instead: {unexpected}"


@pytest.mark.parametrize("path", sorted(KNOWN_STALE))
def test_known_stale_rows_are_still_needed(path):
    """A fixed file must drop its row, so the allowance cannot be silently reused later."""
    count = namespace_uses().get(path, 0)
    assert count == KNOWN_STALE[path][0], (
        f"{path} now has {count} stale use(s); set its KNOWN_STALE row to match or delete it")


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
