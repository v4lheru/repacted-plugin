"""Contract 3, DISPATCH INVARIANTS: every spawn template in a loaded surface passes the dispatch gate.

repacted-fork.md §8.3. A spawn template is an `Agent(...)` call written in prose (inline or in a
fence) that carries `subagent_type=`. The model copies these, so each one must:
  - carry `name=` and `team_name=` (dispatch_gate denies a specialist spawn without them);
  - have a `prompt=` no longer than dispatch_gate.PROMPT_MAX_LENGTH that contains one of
    dispatch_gate.TASK_REFERENCE_PHRASES (otherwise the gate's inline-mission heuristic fires);
  - name a `subagent_type` that is an agent file: a literal `pact-x` needs agents/pact-x.md, a
    placeholder `pact-{...}` is accepted.
The limits are imported from the gate, so the contract and the gate cannot disagree.

Snippets that only NAME the parameters (`Agent(subagent_type=...)` in a sentence) are not
templates: a template has a quoted subagent_type value.
"""
from __future__ import annotations

import re

import pytest

from dispatch_gate import PROMPT_MAX_LENGTH, TASK_REFERENCE_PHRASES
from tests.contract._surfaces import AGENTS, PLUGIN_ROOT, loaded_surfaces, read, rel

DISPATCH_DIR = PLUGIN_ROOT / "reference" / "dispatch"

SUBAGENT = re.compile(r'subagent_type\s*=\s*"([^"]+)"')
PROMPT = re.compile(r'prompt\s*=\s*"((?:[^"\\]|\\.)*)"', re.S)


def agent_calls(text: str) -> list[str]:
    """Every balanced `Agent( ... )` span in text (quote-aware), whether inline or fenced."""
    out = []
    for start in (m.start() for m in re.finditer(r"\bAgent\(", text)):
        depth, i, quote = 0, start + len("Agent"), None
        while i < len(text):
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch == '"':
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    out.append(text[start:i + 1])
                    break
            elif ch == "\n" and depth == 1 and text[i + 1:i + 2] == "\n":
                break          # a blank line ends a prose mention that never closed
            i += 1
    return out


def templates(paths=None) -> list[tuple[str, str]]:
    """Spawn templates in the loaded surfaces AND the shared dispatch reference (CLEANUP-B): the
    reference is read on demand, not loaded, but every command dispatch copies its template."""
    out = []
    for path in paths or [*loaded_surfaces(), *sorted(DISPATCH_DIR.glob("*.md"))]:
        for call in agent_calls(read(path)):
            if SUBAGENT.search(call):
                out.append((rel(path), call))
    return out


def violations(call: str, agents_dir=AGENTS) -> list[str]:
    problems = []
    for param in ("name", "team_name"):
        if not re.search(rf"\b{param}\s*=", call):
            problems.append(f"no {param}=")
    subagent = SUBAGENT.search(call).group(1)
    if "{" in subagent:
        if not subagent.startswith("pact-"):
            problems.append(f"placeholder subagent_type {subagent!r} is not pact-{{...}}")
    elif not (agents_dir / f"{subagent}.md").is_file():
        problems.append(f"subagent_type {subagent!r} has no agents/{subagent}.md")
    prompt = PROMPT.search(call)
    if prompt is None:
        problems.append("no prompt=")
    else:
        text = prompt.group(1).encode().decode("unicode_escape")
        if len(text) > PROMPT_MAX_LENGTH:
            problems.append(f"prompt is {len(text)} chars > {PROMPT_MAX_LENGTH}")
        if not any(phrase in text for phrase in TASK_REFERENCE_PHRASES):
            problems.append("prompt has no task-list phrase")
    return problems


TEMPLATES = templates()


def test_the_scan_finds_the_known_templates():
    """Guards the scanner: the canonical form in bootstrap.md (the persona's copy and
    the shared dispatch reference moved behind `repact dispatch`, which writes the brief)."""
    where = {w for w, _ in TEMPLATES}
    assert "commands/bootstrap.md" in where, where
    assert len(TEMPLATES) >= 1, len(TEMPLATES)


@pytest.mark.parametrize("where, call", TEMPLATES,
                         ids=[f"{w}#{i}" for i, (w, _) in enumerate(TEMPLATES)])
def test_spawn_template_satisfies_the_gate(where, call):
    problems = violations(call)
    assert not problems, f"{where}: {problems}\n{call[:300]}"
