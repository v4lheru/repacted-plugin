"""The agent-memory head budget is stated once in prose and once in code, and the two agree.

The prose site is the index-upkeep rule in the teammate skill (references/teammate-memory.md); the code site is check-index's
bare constants (HEAD_MAX, HEAD_LINES, LINE_MAX), which enforce it. Every other loaded surface
points at the rule instead of restating the figures, so a correction is one prose edit and
one constant, never a sweep. This is a different limit from the platform's index cut, which
tests/test_agent_memory_cap_single_source.py pins separately.
"""

import importlib.machinery
import importlib.util
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PROSE_SITE = PLUGIN_ROOT / "skills" / "teammate" / "references" / "teammate-memory.md"
LOADED_SURFACES = ("agents", "commands", "protocols", "skills")
BUDGET_RE = re.compile(
    r"at most ([\d,]+) bytes, (\d+) lines and (\d+) characters per line", re.IGNORECASE
)


def _check_index():
    loader = importlib.machinery.SourceFileLoader(
        "check_index_for_budget_test", str(PLUGIN_ROOT / "bin" / "check-index"))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def _sites():
    return [
        (path, match)
        for surface in LOADED_SURFACES
        for path in sorted((PLUGIN_ROOT / surface).rglob("*.md"))
        for match in BUDGET_RE.finditer(path.read_text(encoding="utf-8"))
    ]


def test_the_budget_is_stated_in_exactly_one_prose_site():
    sites = _sites()
    assert [path for path, _ in sites] == [PROSE_SITE], (
        "the head budget must be stated once, in the index-upkeep rule of "
        f"the teammate skill's memory reference; found {[str(p.relative_to(PLUGIN_ROOT)) for p, _ in sites]}"
    )


def test_the_prose_figures_equal_check_index_constants():
    (_, match), = _sites()
    size, lines, width = (int(group.replace(",", "")) for group in match.groups())
    check_index = _check_index()
    assert (size, lines, width) == (
        check_index.HEAD_MAX, check_index.HEAD_LINES, check_index.LINE_MAX
    ), "the prose budget and check-index's constants disagree; change both together"


def test_the_budget_sits_inside_the_index_upkeep_rule():
    text = PROSE_SITE.read_text(encoding="utf-8")
    rule = text.index("**Index upkeep")
    budget = BUDGET_RE.search(text).start()
    reachability = text.index("which reports every leaf no index still points at")
    assert rule < budget < reachability, "the budget left the index-upkeep rule"
