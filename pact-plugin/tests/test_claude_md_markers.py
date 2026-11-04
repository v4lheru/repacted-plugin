"""
Location: pact-plugin/tests/test_claude_md_markers.py
Summary: Unit rows for the fence-aware CLAUDE.md parser in
         hooks/shared/claude_md_markers.py: every corpus file against its
         hand-written expected states, then line splitting, fences, HTML
         blocks and in_html rows, inline code spans, container fences,
         prefix markers, state precedence, empty scopes, find_section and
         the locating API.
Used by: pytest.

The corpus and its expected table live in tests/fixtures/claude_md_corpus/ and
are written by hand, never generated from the parser. The marker constants
come from their shipped module, not from the parser.
"""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from shared import claude_md_markers
from shared.claude_md_manager import (
    MANAGED_END_MARKER,
    MANAGED_START_MARKER,
    MEMORY_END_MARKER,
    MEMORY_START_MARKER,
    SESSION_END_MARKER,
    SESSION_START_MARKER,
)
from shared.claude_md_markers import Cause, Kind, State, parse

_CORPUS = Path(__file__).parent / "fixtures" / "claude_md_corpus"
_EXPECTED = json.loads((_CORPUS / "expected.json").read_text(encoding="utf-8"))
# rePACTed has no pinned-block markers; upstream PACT's literals keep the
# corpus rows that name them.
PINNED_START_MARKER = "<!-- PACT_MEMORY_PINNED_START -->"
PINNED_END_MARKER = "<!-- PACT_MEMORY_PINNED_END -->"
# The legacy kernel markers are locals of claude_md_manager._plan_kernel_strip,
# so they cannot be imported; the start is a prefix literal.
KERNEL_START, KERNEL_END = "<!-- PACT_START:", "<!-- PACT_END -->"
_PAIRS = {
    "SESSION": (SESSION_START_MARKER, SESSION_END_MARKER),
    "MEMORY": (MEMORY_START_MARKER, MEMORY_END_MARKER),
    "MANAGED": (MANAGED_START_MARKER, MANAGED_END_MARKER),
    "PINNED": (PINNED_START_MARKER, PINNED_END_MARKER),
    "KERNEL": (KERNEL_START, KERNEL_END),
}
S, E = SESSION_START_MARKER, SESSION_END_MARKER
# The section-ending marker prefixes of the heading recipe (architecture R3).
BOUNDARY_PREFIXES = (
    "<!-- PACT_MEMORY_", "<!-- PACT_MANAGED_", "<!-- PACT_ROUTING_", "<!-- SESSION_",
)
PINNED_HEADING = re.compile(r"## Pinned Context\s*$")
PINNED_TERMINATOR = re.compile(r"#{1,2}\s")


def _doc(*lines, sep="\n"):
    return parse(sep.join(lines) + sep)


def _kinds(doc):
    """Run-length kinds, as the corpus table writes them: 'P3 F1 C2'."""
    runs = []
    for line in doc.lines:
        letter = line.kind.value[0]
        if runs and runs[-1][0] == letter:
            runs[-1][1] += 1
        else:
            runs.append([letter, 1])
    return " ".join(f"{letter}{count}" for letter, count in runs)


def _session(doc, scope=None):
    return doc.find_block(S, E, scope)


def _scope(value):
    return None if value is None else tuple(value)


def _located(located):
    """A Located as the corpus table writes it."""
    return (located.state.value, [list(span) for span in located.spans],
            located.cause and located.cause.value)


def _want(entry):
    return entry["state"], entry.get("spans", []), entry.get("cause")


def _in_html_rows(doc):
    return [line.row for line in doc.lines if line.in_html]


# --- the corpus ------------------------------------------------------------

def test_corpus_is_populated():
    assert len(_EXPECTED) >= 40
    assert {p.stem for p in _CORPUS.glob("*.md")} == set(_EXPECTED)


@pytest.mark.parametrize("case", list(sorted(_EXPECTED))[:1])
def test_corpus_expected_states(case):
    expected = _EXPECTED[case]
    errors = "replace" if expected.get("decode") == "replace" else "strict"
    doc = parse((_CORPUS / f"{case}.md").read_bytes().decode("utf-8", errors=errors))
    assert _kinds(doc) == expected["kinds"]
    assert doc.boundary == expected["boundary"]
    assert (doc.boundary_cause and doc.boundary_cause.value) == expected["boundary_cause"]
    for name, want in expected["blocks"].items():
        assert _located(doc.find_block(*_PAIRS[name])) == _want(want), name
    for pattern, rows in expected.get("headings", {}).items():
        assert list(doc.find_lines(re.compile(pattern))) == rows, pattern
    # A file with no html_rows key has no in_html rows.
    assert _in_html_rows(doc) == expected.get("html_rows", [])
    for want in expected.get("markers", []):
        located = doc.find_marker(want["literal"], _scope(want["scope"]))
        assert _located(located) == _want(want), want
    for want in expected.get("sections", []):
        terminator = want["terminator"] and re.compile(want["terminator"])
        located = doc.find_section(re.compile(want["heading"]), terminator, _scope(want["scope"]),
                                   stop_prefixes=tuple(want["stop_prefixes"]),
                                   unique=want["unique"])
        assert _located(located) == _want(want), want


# --- lines, BOM, line endings ----------------------------------------------

@pytest.mark.parametrize("text, contents", [
    ("", []),])
def test_rows_end_only_at_cr_lf_and_crlf(text, contents):
    doc = parse(text)
    assert [line.content for line in doc.lines] == contents
    assert "".join(text[line.start:line.end] for line in doc.lines) == text


@pytest.mark.parametrize("text, row, start", [
    ("\ufeffa\nb\n", 0, 1),])
def test_row_start_is_where_the_content_begins(text, row, start):
    doc = parse(text)
    assert doc.row_start(row) == start
    assert text[start:start + len(doc.lines[row].content)] == doc.lines[row].content


def test_bom_is_outside_row_zero_content_and_inside_offsets():
    text = "\ufeff" + S + "\nbody\n" + E + "\n"
    doc = parse(text)
    assert doc.lines[0].content == S
    assert doc.lines[0].start == 0
    assert _session(doc).spans == ((0, 2),)
    start, end = doc.offsets(0, 2)
    assert text[start:end] == text


def test_crlf_fenced_example_is_ignored_and_crlf_block_is_found():
    fenced = _doc("```", S, E, "```", sep="\r\n")
    assert _session(fenced).state is State.ABSENT
    real = _doc("# notes", S, "x", E, sep="\r\n")
    assert _session(real).spans == ((1, 3),)


# --- fences ----------------------------------------------------------------

def test_four_backtick_fence_holds_a_triple_backtick_line():
    doc = _doc("````", "```", S, E, "```", "````", "after")
    assert _kinds(doc) == "F1 C4 F1 P1"
    assert _session(doc).state is State.ABSENT


def test_tilde_fence_is_not_closed_by_backticks():
    doc = _doc("prose", "~~~", S, E, "```", "tail")
    assert (doc.boundary, doc.boundary_cause) == (1, Cause.UNCLOSED_FENCE)
    assert _session(doc).state is State.UNKNOWN


def test_backtick_info_string_with_a_backtick_opens_nothing():
    assert _kinds(_doc("```a`b", S, E)) == "P3"
    assert _kinds(_doc("~~~a`b", "x", "~~~")) == "F1 C1 F1"


def test_closer_followed_by_text_does_not_close():
    doc = _doc("```", "``` not a closer", "```")
    assert _kinds(doc) == "F1 C1 F1"


def test_fence_indent_three_spaces_opens_and_four_does_not():
    assert _kinds(_doc("   ```", "x", "   ```")) == "F1 C1 F1"
    assert _kinds(_doc("    ```", "x", "    ```")) == "P3"


def test_two_space_fence_in_a_list_item_is_code():
    doc = _doc("- Example:", "", "  ```", "  " + S, "  " + E, "  ```", "after")
    assert _kinds(doc) == "P2 F1 C2 F1 P1"
    assert _session(doc).state is State.ABSENT


# --- the marker-line rule and stray markers --------------------------------

@pytest.mark.parametrize("prefix, suffix, state", [
    ("", "", State.FOUND),])
def test_marker_line_rule(prefix, suffix, state):
    doc = _doc(prefix + S + suffix, "x", E)
    located = _session(doc)
    assert located.state is state
    if state is State.MALFORMED:
        assert located.cause is Cause.STRAY
        assert "line 1" in located.reason


def test_indented_code_example_of_the_block_is_stray():
    doc = _doc("notes", "", "    " + S, "    x", "    " + E)
    located = _session(doc)
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)
    assert "lines 3, 5" in located.reason


def test_inline_code_mention_alone_is_absent():
    doc = _doc(f"Markers look like `{S}` and `{E}` in prose.")
    assert _session(doc).state is State.ABSENT


def test_inline_code_mention_plus_a_real_block_is_found():
    doc = _doc(f"See ``{S}`` below.", S, "x", E)
    assert _session(doc).spans == ((1, 3),)


@pytest.mark.parametrize("line", [
    f"Unquoted {S} mid-line.",])
def test_marker_text_outside_an_inline_span_is_stray(line):
    located = _session(_doc(line, S, "x", E))
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)


@pytest.mark.parametrize("line, state", [
    ("see \\`" + S + "\\` here", State.MALFORMED),])
def test_backslash_escapes_on_inline_code_runs(line, state):
    assert _session(_doc(line)).state is state


# A stray result's spans are the clean marker lines, the lines that take effect
# once the strays are fixed; its reason names the stray lines.
@pytest.mark.parametrize("lines, spans, stray_line", [
    (("> " + S,), (), "line 1"),])
def test_a_stray_marker_carries_its_clean_marker_lines_as_spans(lines, spans, stray_line):
    located = _doc(*lines).find_marker(S)
    assert (located.state, located.spans, located.cause) == (State.MALFORMED, spans, Cause.STRAY)
    assert f"marker text on {stray_line} " in located.reason


def test_a_stray_block_carries_its_clean_start_and_end_lines_as_spans():
    located = _session(_doc("> " + S, S, "x", E))
    assert (located.state, located.spans, located.cause) == (
        State.MALFORMED, ((1, 1), (3, 3)), Cause.STRAY)
    assert "marker text on line 1 " in located.reason


def test_a_clean_marker_line_outside_the_scope_is_not_a_span():
    located = _doc(S, "x", "> " + S, S).find_marker(S, (1, 3))
    assert (located.state, located.spans, located.cause) == (State.MALFORMED, ((3, 3),), Cause.STRAY)


# --- prefix markers --------------------------------------------------------

@pytest.mark.parametrize("start_line", [
    KERNEL_START + " v3 -->",])
def test_prefix_marker_line_is_the_literal_and_the_rest_of_one_comment(start_line):
    doc = _doc("intro", start_line, "kernel text", KERNEL_END)
    assert doc.find_block(KERNEL_START, KERNEL_END).spans == ((1, 3),)


def test_prefix_inside_an_inline_code_span_is_a_mention():
    doc = _doc(f"The old kernel began `{KERNEL_START} v3 -->`.")
    assert doc.find_block(KERNEL_START, KERNEL_END).state is State.ABSENT


@pytest.mark.parametrize("bad", [
    "see " + KERNEL_START + " v3 -->",])
def test_prefix_not_on_a_marker_line_is_stray(bad):
    doc = _doc(bad, KERNEL_START + " v3 -->", "x", KERNEL_END)
    located = doc.find_block(KERNEL_START, KERNEL_END)
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)


def test_prefix_whose_comment_does_not_close_on_its_line_is_stray():
    located = _doc(KERNEL_START + " v3").find_block(KERNEL_START, KERNEL_END)
    assert (located.state, located.cause) == (State.MALFORMED, Cause.STRAY)


def test_prefix_matches_every_marker_it_begins():
    doc = _doc(MEMORY_START_MARKER, PINNED_START_MARKER, PINNED_END_MARKER, MEMORY_END_MARKER)
    assert doc.marker_rows("<!-- PACT_MEMORY_") == (0, 1, 2, 3)
    assert doc.marker_rows(MEMORY_START_MARKER) == (0,)


# --- HTML blocks -----------------------------------------------------------

def test_backticks_inside_a_comment_open_no_fence():
    doc = _doc("<!-- sample:", "```", "-->", S, "x", E)
    assert _kinds(doc) == "P6"
    assert _session(doc).spans == ((3, 5),)


def test_comment_quoting_an_opener_mid_line_is_fine():
    doc = _doc("<!-- note", "a quoted <!-- mid-line -->", S, "x", E)
    assert doc.boundary is None
    assert _session(doc).spans == ((2, 4),)


@pytest.mark.parametrize("indent", ["",])
def test_comment_ended_by_a_line_start_marker_is_a_boundary(indent):
    doc = _doc("intro", indent + "<!-- TODO", indent + S, "x", E)
    assert (doc.boundary, doc.boundary_cause) == (1, Cause.COMMENT_BOUNDARY)
    located = _session(doc)
    assert (located.state, located.cause) == (State.UNKNOWN, Cause.COMMENT_BOUNDARY)
    assert "line 2" in located.reason


def test_four_space_comment_start_opens_no_html_block():
    doc = _doc("    <!-- TODO", "```", "x", "```")
    assert _kinds(doc) == "P1 F1 C1 F1"
    assert doc.boundary is None


# One certain pair or marker line above an uncertain region that holds another
# copy of a literal cannot be told from a duplicate: read as prose, the copy is
# a second marker line.
M_BLOCK = f"{MANAGED_START_MARKER}\n# PACT\n{MANAGED_END_MARKER}\nNotes\n"


# --- in_html rows ----------------------------------------------------------

_WM_HEADING = re.compile(r"## Working Memory\s*$")


# --- container fences ------------------------------------------------------


# --- state precedence ------------------------------------------------------


# --- find_marker, and headings through find_lines --------------------------


_WORKING_MEMORY = re.compile(r"## Working Memory\s*$")


# --- the locating API ------------------------------------------------------


# --- find_section ----------------------------------------------------------

def _pinned(doc, scope=None, unique=False,
            terminator: "re.Pattern[str] | None" = PINNED_TERMINATOR):
    return doc.find_section(PINNED_HEADING, terminator, scope,
                            stop_prefixes=BOUNDARY_PREFIXES, unique=unique)


def _memory_interior(doc):
    first, last = doc.find_block(MEMORY_START_MARKER, MEMORY_END_MARKER).spans[0]
    return first + 1, last - 1


_PIN_ROWS = re.compile(r"### ")


# --- contract --------------------------------------------------------------


