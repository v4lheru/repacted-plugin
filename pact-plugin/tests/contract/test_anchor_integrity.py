"""Contract 2, ANCHOR INTEGRITY: every model-facing hook string that names a file, section or row resolves.

repacted-fork.md §8.2. A hook message that sends the model to "orchestrator persona §11" or to
"teammate skill Common mistakes row 2" is an instruction; if a package renames the heading,
moves the file or renumbers the table, the hook keeps pointing at nothing and no behaviour test
notices. This scan reads every string literal in hooks/**/*.py (docstrings and comments excluded:
they are for maintainers, not the model) and resolves four reference shapes:

  1. a plugin file, by path (`protocols/pact-communication-charter.md`) or by bare name
     (`completion.md`, `peer-review.md`);
  2. `orchestrator persona §N` -> a `## N.` heading in agents/pact-orchestrator.md;
  3. `<skill> skill Common mistakes row N` -> row N of that SKILL.md's Common mistakes table;
  4. `<file>.md §Heading` and `<file>.md <Title Case Heading>` -> a heading in that file.

A moved file leaves a stub at its old path until SWEEP (build plan §4), so a stubbed path still
resolves; the stub must name the new home, which is checked by the owning package's gate.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.contract._surfaces import ORCHESTRATOR, PLUGIN_ROOT, headings, read

HOOKS = PLUGIN_ROOT / "hooks"
SEARCH_DIRS = ("agents", "commands", "protocols", "skills", "reference", "templates")

# Names that are not plugin files: the user's/project's CLAUDE.md, and platform doc pages that the
# hook cites by name (code.claude.com). Each entry needs a reason.
NOT_PLUGIN_FILES = {
    "CLAUDE.md": "the project/user memory file, not a plugin surface",
    "SKILL.md": "the generic entry-file name, used without a skill dir",
    "README.md": "the platform/plugin README, not a loaded surface",
    "MEMORY-inbox.md": "an agent-memory inbox under the config dir, matched by name in file_tracker",
    "decisions.md": "the project's docs/decisions.md a task-log decision: link names (shared/task_log/write.py)",
    "map.md": "an example KB index page name in the kb.index_page profile error (shared/vault_scope.py)",
}

FILE_REF = re.compile(r"(?<![\w/<*{-])((?:[\w.-]+/)*[\w-]+\.md)\b")
PERSONA_SECTION = re.compile(r"orchestrator persona §(\d+)")
SKILL_ROW = re.compile(r"([\w-]+) skill Common mistakes row (\d+)")
FILE_SECTION = re.compile(r"([\w-]+\.md) §([A-Z][\w-]*(?: [A-Za-z][\w-]*)*)")
FILE_TITLE = re.compile(r"see ([\w-]+\.md) ((?:[A-Z][\w-]*)(?: [A-Z][\w-]*)+)\)")


def model_facing_strings(path: Path) -> list[tuple[int, str]]:
    """String literals in a hook module, minus docstrings (f-string parts included)."""
    tree = ast.parse(read(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings]


def all_hook_strings(hooks: Path = HOOKS) -> list[tuple[str, int, str]]:
    out = []
    for path in sorted(hooks.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for line, text in model_facing_strings(path):
            out.append((path.relative_to(hooks.parent).as_posix(), line, text))
    return out


def resolve_file(ref: str, root: Path = PLUGIN_ROOT) -> Path | None:
    ref = ref.removeprefix("pact-plugin/")
    if "/" in ref:
        candidate = root / ref
        return candidate if candidate.is_file() else None
    for top in SEARCH_DIRS:
        for hit in (root / top).rglob(ref):
            if hit.is_file():
                return hit
    return None


def heading_numbers(text: str) -> set[str]:
    return {m.group(1) for h in headings(text) if (m := re.match(r"#{1,6} (\d+)\.", h))}


def common_mistakes_rows(text: str) -> set[str]:
    rows, inside = set(), False
    for line in text.splitlines():
        if re.match(r"#{2,6} Common mistakes", line.strip()):
            inside = True
            continue
        if inside and re.match(r"#{1,6} ", line.strip()):
            break
        if inside and (m := re.match(r"\|\s*(\d+)\s*\|", line.strip())):
            rows.add(m.group(1))
    return rows


def has_heading(text: str, title: str) -> bool:
    return any(title in h for h in headings(text))


def references(strings) -> list[tuple[str, str]]:
    """(where, reference) pairs: every reference found, for the parametrized checks."""
    out = []
    for where, line, text in strings:
        loc = f"{where}:{line}"
        for m in FILE_REF.finditer(text):
            name = m.group(1)
            if name.rsplit("/", 1)[-1] not in NOT_PLUGIN_FILES:
                out.append((loc, f"file:{name}"))
        for m in PERSONA_SECTION.finditer(text):
            out.append((loc, f"persona:{m.group(1)}"))
        for m in SKILL_ROW.finditer(text):
            out.append((loc, f"row:{m.group(1)}:{m.group(2)}"))
        for m in FILE_SECTION.finditer(text):
            out.append((loc, f"section:{m.group(1)}:{m.group(2)}"))
        for m in FILE_TITLE.finditer(text):
            out.append((loc, f"section:{m.group(1)}:{m.group(2)}"))
    return sorted(set(out))


def unresolved(ref: str, root: Path = PLUGIN_ROOT) -> str | None:
    """None when the reference resolves; otherwise why it does not."""
    kind, _, rest = ref.partition(":")
    if kind == "file":
        return None if resolve_file(rest, root) else f"no plugin file {rest!r}"
    if kind == "persona":
        persona = root / ORCHESTRATOR.relative_to(PLUGIN_ROOT)
        return None if rest in heading_numbers(read(persona)) else f"persona has no '## {rest}.' heading"
    if kind == "row":
        skill, row = rest.split(":")
        path = root / "skills" / skill / "SKILL.md"
        if not path.is_file():
            return f"no skill {skill!r}"
        return None if row in common_mistakes_rows(read(path)) else f"{skill} has no Common mistakes row {row}"
    if kind == "section":
        name, title = rest.split(":", 1)
        path = resolve_file(name, root)
        if path is None:
            return f"no plugin file {name!r}"
        return None if has_heading(read(path), title) else f"{name} has no heading containing {title!r}"
    return f"unknown reference kind {kind!r}"


REFERENCES = references(all_hook_strings())


def test_the_scan_finds_the_known_references():
    """Guards the scanner itself: an empty parse would make every check below vacuous."""
    found = {ref for _, ref in REFERENCES}
    # The skill-row and section citations left with the hooks 2.1.0 retired; the file and
    # persona kinds remain.
    assert "persona:11" in found
    assert "file:skills/teammate/references/teammate-messaging.md" in found
    assert len(REFERENCES) >= 10, len(REFERENCES)


@pytest.mark.parametrize("where, ref", REFERENCES, ids=[f"{w}|{r}" for w, r in REFERENCES])
def test_hook_reference_resolves(where, ref):
    problem = unresolved(ref)
    assert problem is None, f"{where}: {ref} -> {problem}"


# --- the current release's known limits -------------------------------------------------------

CHANGELOG = PLUGIN_ROOT / "CHANGELOG.md"
REPO_ROOT = PLUGIN_ROOT.parent.parent
KNOWN_LIMIT_MAX_CHARS = 200
LIMITS_LINK = re.compile(r"docs/architecture/([\w./-]+\.md)#limits")


def current_release_known_limits(text: str) -> list[str]:
    """The bullets of the first `Known limits` block after the first release heading."""
    first = text.index("\n## ")
    start = text.index("Known limits", first)
    end = text.index("\n## ", start)
    return [line for line in text[start:end].splitlines() if line.startswith("- ")]


