"""Every agent definition routes its role's most frequent triggers to repacted skills.

The block is the user layer's "Skills for this role" list (the design notes
in the rePACT repo), placed verbatim by the UL-integrate milestone. It must stay small, since
the agent body loads into every spawn: 2-5 lines, each at most 90 bytes. Every skill it names
must exist in this plugin. The prelude plus the backend coder's definition is the spawn-cost
benchmark, capped at 10,000 bytes.
"""

import json
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
AGENTS = sorted((PLUGIN_ROOT / "agents").glob("pact-*.md"))
BLOCK_RE = re.compile(r"\*\*Skills for this role:\*\*\n((?:- .*\n)+)")
LINE_RE = re.compile(r"^- (?P<trigger>.+): repacted:(?P<skill>[a-z0-9-]+)$")
MAX_LINE_BYTES = 90
SPAWN_BUDGET_BYTES = 10_000


def _block_lines(path: Path) -> list[str]:
    match = BLOCK_RE.search(path.read_text(encoding="utf-8"))
    assert match, f"{path.name} has no 'Skills for this role' block"
    return match.group(1).splitlines()


def test_every_agent_definition_is_covered():
    # The set comes from the tree, not a count: the public export ships fewer agents.
    manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    registered = sorted(Path(entry).name for entry in manifest["agents"])
    assert AGENTS, "no agent definitions found"
    assert registered == [p.name for p in AGENTS], "plugin.json agents and agents/ differ"


@pytest.mark.parametrize("path", AGENTS, ids=[p.stem for p in AGENTS])
def test_the_block_is_small_and_names_real_skills(path):
    lines = _block_lines(path)
    assert 2 <= len(lines) <= 5, f"{path.name}: {len(lines)} lines"
    for line in lines:
        assert len(line.encode("utf-8")) <= MAX_LINE_BYTES, f"{path.name}: {line!r}"
        match = LINE_RE.match(line)
        assert match, f"{path.name}: not '- <trigger>: repacted:<skill>': {line!r}"
        skill = PLUGIN_ROOT / "skills" / match["skill"] / "SKILL.md"
        assert skill.is_file(), f"{path.name} routes to a missing skill: {match['skill']}"


def test_the_block_appears_once_per_definition():
    for path in AGENTS:
        assert path.read_text(encoding="utf-8").count("**Skills for this role:**") == 1, path.name


def test_prelude_plus_backend_coder_fits_the_spawn_budget(tmp_path):
    from shared.peer_context import get_peer_context

    team_dir = tmp_path / "teams" / "session-budget"
    team_dir.mkdir(parents=True)
    (team_dir / "config.json").write_text(json.dumps({"members": [
        {"name": "team-lead", "agentType": "repacted:pact-orchestrator"},
        {"name": "backend-coder", "agentType": "pact-backend-coder"},
        {"name": "secretary", "agentType": "pact-secretary"},
    ]}), encoding="utf-8")
    prelude = get_peer_context("pact-backend-coder", "session-budget",
                               agent_name="backend-coder", teams_dir=str(tmp_path / "teams"))
    assert prelude, "the prelude came back empty, so the measurement has no subject"
    definition = (PLUGIN_ROOT / "agents" / "pact-backend-coder.md").read_bytes()
    total = len(prelude.encode("utf-8")) + len(definition)
    assert total <= SPAWN_BUDGET_BYTES, f"prelude + pact-backend-coder = {total} B"
