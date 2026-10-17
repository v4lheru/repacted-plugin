"""Contract, NO DUPLICATE PROSE: every paragraph the model reads has one home.

A line of 90+ characters that appears verbatim in two or more model-read files is a second copy of
a rule, and the two copies drift. Fenced code is exempt: a dispatch string or a command template is
copied by design and the hooks match it literally. HTML-comment markers (`<!-- ... -->`) are
machine-read, not prose, and a refinement log is append-only history, so both are skipped. Pointer
or attribution lines that several files share on purpose are listed in ALLOWED with the reason.
Baseline before this check: 97 lines (refactor surface design §1); the target is zero outside ALLOWED.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from tests.contract._surfaces import PLUGIN_ROOT, read

MIN_CHARS = 90
HISTORY_FILES = {"refinement-log.md"}

# Lines several files carry on purpose: a pointer, or an attribution, and nothing else.
ALLOWED: dict[str, str] = {
    "See also: [Communication Charter](../../protocols/pact-communication-charter.md) for full plain English, anti-sycophancy, and constructive challenge norms.":
        "the reference files end with the same pointer to the charter",
    "Run the shared protocol [protocols/self-refine.md](../../../protocols/self-refine.md); its step 5 is the list below. Everything else (mission, log entry, validation, report) is shared.":
        "every skill's self-refine file points at the shared protocol",
    "At the end of a session that used this skill, run [self-refine](protocols/self-refine.md) and record what it changed.":
        "the skill routers end with the same pointer to their self-refine protocol",
    "For agent stall detection and recovery, see [Agent Stall Detection](../../commands/orchestrate.md#agent-stall-detection).":
        "comPACT and the nested cycle point at orchestrate's stall-detection section",
    'Ensure the secretary first: see the on-demand secretary step in [bootstrap.md](bootstrap.md#step-2--on-demand-secretary) (TaskCreate → TaskUpdate(owner="secretary") → spawn if absent → SendMessage).':
        "peer-review and wrap-up point at bootstrap's on-demand secretary step",
    "Entry file: [vd-c2a-fail-open-and-unknown-states.md](vd-c2a-fail-open-and-unknown-states.md).":
        "continuation files point back at their entry file",
}


def model_read_files() -> list[Path]:
    paths = [*PLUGIN_ROOT.glob("agents/*.md"), *PLUGIN_ROOT.glob("commands/*.md"),
             *PLUGIN_ROOT.glob("protocols/*.md"), *PLUGIN_ROOT.rglob("reference/**/*.md"),
             *PLUGIN_ROOT.rglob("skills/**/*.md")]
    return sorted(p for p in paths if p.is_file() and p.name not in HISTORY_FILES)


def prose_lines(text: str):
    """Stripped lines outside fenced code and HTML comments, trailing spaces and a lone backslash removed."""
    fenced = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and not stripped.startswith("<!--"):
            yield stripped.rstrip("\\").strip()


def duplicated_lines() -> dict[str, set[str]]:
    homes: dict[str, set[str]] = defaultdict(set)
    for path in model_read_files():
        rel = path.relative_to(PLUGIN_ROOT).as_posix()
        for line in prose_lines(read(path)):
            if len(line) >= MIN_CHARS and line not in ALLOWED:
                homes[line].add(rel)
    return {line: files for line, files in homes.items() if len(files) >= 2}


def test_no_prose_line_has_two_homes():
    dups = duplicated_lines()
    report = [f"{sorted(files)}: {line[:80]}..." for line, files in sorted(dups.items(), key=lambda kv: -len(kv[1]))]
    assert not dups, f"{len(dups)} line(s) of 90+ chars live in two or more files:\n  " + "\n  ".join(report)


def test_allowlist_entries_are_pointers_still_in_use():
    """An ALLOWED line that no two files carry any more is dead weight; drop it."""
    counts = defaultdict(int)
    for path in model_read_files():
        for line in prose_lines(read(path)):
            if line in ALLOWED:
                counts[line] += 1
    unused = [line[:60] for line in ALLOWED if counts[line] < 2]
    assert not unused, f"ALLOWED lines no longer shared by two files: {unused}"


def test_normaliser_rows():
    text = ("```\nx" + "y" * 100 + "\n```\ntrailing" + " " * 5 + "z" * 90 + "   \n`" + "b" * 95
            + "\n<!-- marker: " + "m" * 90 + " -->")
    lines = list(prose_lines(text))
    assert lines[0].startswith("trailing") and not lines[0].endswith(" ")   # trailing spaces stripped
    assert "x" + "y" * 100 not in lines                                       # fenced line skipped
    assert lines[1] == "`" + "b" * 95                                         # a backtick-led line is not a fence
    assert len(lines) == 2                                                    # the HTML comment is skipped
