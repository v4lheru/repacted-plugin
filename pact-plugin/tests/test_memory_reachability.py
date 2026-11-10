"""Verification arms for the agent-memory reachability checker.

Not the comprehensive suite. Each arm here is tied to one load-bearing clause of
the reachability rule, and every fixture is synthetic — the repo
holds no agent-memory-shaped material and a test against a real tree would
assert a number that moves between sessions.
"""
import subprocess
import sys


import memory_reachability as mr  # noqa: E402


def _tree(root):
    """One index, one named satellite that names a second, one named archive.

    Seven leaves, each reachable by a DIFFERENT key or not at all, so an arm
    that drops one key reddens rather than passing on a sibling's evidence.

    THE POINTER TEXT MUST NOT CONTAIN ANY OTHER KEY OF THE LEAF IT NAMES. An
    earlier version pointed at the frontmatter leaf with prose containing the
    bare word `frontmatter`, which is that same leaf's prefix-stripped key -- so
    the frontmatter key had no coverage through `scan()` at all, and the
    prefix-strip arm was passing on the sibling's evidence this docstring warns
    about. Both now have their own leaf and their own non-overlapping pointer.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "MEMORY.md").write_text(
        "# Index\n"
        "> Satellites: `MEMORY-topics.md`; retired in `MEMORY-archive-old.md`.\n"
        "\n"
        "## Rules\n"
        "- [By filename](feedback_by_filename.md)\n"
        "- [[by_wikilink_stem]]\n",
        encoding="utf-8",
    )
    # The new pointer goes in a SATELLITE, not the index: anchor arms depend on
    # the index's last entry line, so adding one there moves their evidence.
    (root / "MEMORY-topics.md").write_text(
        "see also `MEMORY-second-hop.md`\nalso stripped_prefix\n"
        "- [[by-hyphen-spelling]]\n", encoding="utf-8")
    (root / "MEMORY-second-hop.md").write_text(
        "reached only at the second hop: a leaf named in its own header\n", encoding="utf-8")
    (root / "MEMORY-archive-old.md").write_text(
        "- [Retired](feedback_archived_only.md)\n", encoding="utf-8")

    (root / "feedback_by_filename.md").write_text("x", encoding="utf-8")
    (root / "feedback_by_wikilink_stem.md").write_text("x", encoding="utf-8")
    (root / "feedback_by_hyphen_spelling.md").write_text("x", encoding="utf-8")
    # Reached ONLY by its frontmatter name: neither its filename, its stem, nor
    # its prefix-stripped stem appears in any root.
    (root / "feedback_frontmatter.md").write_text(
        "---\nname: a leaf named in its own header\n---\nx", encoding="utf-8")
    # Reached ONLY by its prefix-stripped stem.
    (root / "feedback_stripped_prefix.md").write_text("x", encoding="utf-8")
    (root / "feedback_archived_only.md").write_text("x", encoding="utf-8")
    (root / "feedback_planted_orphan.md").write_text(
        "---\nname: nobody_points_here\n---\nx", encoding="utf-8")
    return root


def test_planted_orphan_is_the_only_unreachable(tmp_path):
    """The acceptance bar: it must find a planted orphan and nothing else."""
    result = mr.scan(_tree(tmp_path / "agent"))
    assert [p.name for p in result.unreachable] == ["feedback_planted_orphan.md"]


def test_archive_only_is_not_reported_as_an_orphan(tmp_path):
    """Collapsing the tiers condemns compaction for working correctly."""
    result = mr.scan(_tree(tmp_path / "agent"))
    assert [p.name for p in result.archive_only] == ["feedback_archived_only.md"]
    assert "feedback_archived_only.md" not in [p.name for p in result.unreachable]


def test_root_discovery_reaches_a_fixed_point(tmp_path):
    """A satellite naming another satellite. One level is not enough."""
    result = mr.scan(_tree(tmp_path / "agent"))
    assert "MEMORY-second-hop.md" in [p.name for p in result.roots]
    # And the leaf that only that satellite names is live, not an orphan.
    assert "feedback_frontmatter.md" in [p.name for p in result.live_reachable]


def test_an_unnamed_satellite_and_its_exclusive_leaf_are_both_orphans(tmp_path):
    """Globbing roots would silently promote it and certify its targets."""
    root = _tree(tmp_path / "agent")
    (root / "MEMORY-unnamed.md").write_text("- [Y](feedback_only_here.md)\n", encoding="utf-8")
    (root / "feedback_only_here.md").write_text("x", encoding="utf-8")
    names = [p.name for p in mr.scan(root).unreachable]
    assert "MEMORY-unnamed.md" in names and "feedback_only_here.md" in names


def test_a_pointer_past_the_cut_is_reported(tmp_path):
    """In the file and out of context is its own tier, invisible to a whole-file read."""
    root = tmp_path / "agent"
    root.mkdir(parents=True)
    filler = "\n".join("- filler line {0}".format(i) for i in range(mr.MAX_LINES + 5))
    (root / "MEMORY.md").write_text(
        "# Index\n" + filler + "\n- [Late](feedback_past_the_cut.md)\n", encoding="utf-8")
    (root / "feedback_past_the_cut.md").write_text("x", encoding="utf-8")
    result = mr.scan(root)
    assert [p.name for p in result.past_the_cut] == ["feedback_past_the_cut.md"]


def test_a_directory_with_no_index_is_not_applicable(tmp_path):
    """Not an error, and not a fully orphaned directory."""
    root = tmp_path / "empty"
    root.mkdir()
    (root / "feedback_stray.md").write_text("x", encoding="utf-8")
    result = mr.scan(root)
    assert result.not_applicable and result.unreachable == ()


def test_utf16_units_are_not_code_points(tmp_path):
    """`len()` undercounts an astral character by exactly one per occurrence."""
    assert mr.utf16_units("\N{KEY}") == 2
    assert len("\N{KEY}") == 1


def test_the_emitted_anchor_is_a_byte_exact_slice_of_the_raw_index(tmp_path):
    """The matching haystack is normalised; an anchor taken from it never matches.

    NARROWED once the anchor became a slice of `raw`: NO INPUT can make a
    byte-exact anchor fail this, so it no longer asserts "the guard catches a bad
    anchor from a real file". It asserts "an anchor built from the wrong SOURCE
    is caught" -- reachable only by mutating the construction, not the fixture.
    The first reading is the one everyone assumes; this is the second.
    """
    root = _tree(tmp_path / "agent")
    block = mr.emit_edit(mr.scan(root))
    raw = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert block is not None and not block.startswith("REFUSED")
    anchor = block.split("old_string:\n", 1)[1].split("\n\nnew_string:", 1)[0]
    assert anchor in raw, "anchor must be a raw slice, not normalised text"
    # The NEIGHBOUR, not the heading: the last entry line, so placement is exact.
    assert anchor.strip().startswith("-")


def test_a_named_section_anchors_inside_that_section(tmp_path):
    """The agent names the topic; the tool computes the anchor."""
    root = _tree(tmp_path / "agent")
    anchor, _ = _emitted(mr.emit_edit(mr.scan(root), "## Rules"))
    assert anchor.strip() == "- [[by_wikilink_stem]]"


def test_an_empty_named_section_anchors_on_its_heading(tmp_path):
    """With no entries to land in front of, heading and section-end coincide."""
    root = _tree(tmp_path / "agent")
    index = root / "MEMORY.md"
    index.write_text(index.read_text(encoding="utf-8") + "\n## Fresh\n", encoding="utf-8")
    anchor, _ = _emitted(mr.emit_edit(mr.scan(root), "## Fresh"))
    assert anchor.strip() == "## Fresh"


def test_a_section_past_the_cut_is_refused(tmp_path):
    """Placing a pointer there would create the condition this tool reports."""
    root = tmp_path / "agent"
    root.mkdir(parents=True)
    filler = "\n".join("- filler line {0}".format(i) for i in range(mr.MAX_LINES + 5))
    (root / "MEMORY.md").write_text("# Index\n" + filler + "\n## Late\n", encoding="utf-8")
    (root / "feedback_orphan.md").write_text("x", encoding="utf-8")
    block = mr.emit_edit(mr.scan(root), "## Late")
    assert block.startswith("REFUSED") and "past the loaded prefix" in block


def test_an_unknown_heading_is_refused(tmp_path):
    root = _tree(tmp_path / "agent")
    assert mr.emit_edit(mr.scan(root), "## Nope").startswith("REFUSED")


def test_emit_is_silent_on_a_clean_tree(tmp_path):
    root = _tree(tmp_path / "agent")
    (root / "feedback_planted_orphan.md").unlink()
    assert mr.emit_edit(mr.scan(root)) is None


# ---------------------------------------------------------------------------
# Predicate-level arms.
#
# Every arm above drives scan() end to end, so a predicate detail no fixture
# happens to exercise is invisible to all of them. These four call the predicate
# directly. Measured against the 35 arms in this file: deleting re.escape,
# deleting re.IGNORECASE, widening the boundary classes to \b, dropping the
# frontmatter key, and dropping the prefix strip are EACH killed, every one by
# the arm written for it.
#
# State the arm count beside any such result. An earlier version of this block
# reported a 16-arm measurement as current fact, and it was still being read as
# current after the suite had grown past it.
#
# Mutating this module: disable bytecode caching (PYTHONDONTWRITEBYTECODE=1). A
# mutation that preserves the file's byte length, written in a fast loop, lands
# in the same mtime second and is served stale from __pycache__ -- reporting
# SURVIVED with the mutation sitting on disk. Kills are safe; survivors lie.


def _emitted(block):
    # Assert the shape before splitting on it. A REFUSED block, or None, would
    # otherwise raise IndexError here -- which reddens the arm without telling
    # you whether the SUBJECT failed or this helper did. Measured: the
    # normalised-anchor mutant killed the round-trip arm by IndexError, and a
    # kill by the wrong mechanism is indistinguishable from a real one in a
    # pass/fail summary.
    assert block is not None and not block.startswith("REFUSED"), block
    assert "old_string:\n" in block and "\n\nnew_string:\n" in block, block
    anchor = block.split("old_string:\n", 1)[1].split("\n\nnew_string:\n", 1)[0]
    new = block.split("\n\nnew_string:\n", 1)[1].split("\n\nThe file may", 1)[0]
    return anchor, new


def _crlf_tree(root, duplicated):
    """A CRLF index whose last section entry is unique, or duplicated downstream."""
    root.mkdir(parents=True, exist_ok=True)
    tail = "## Other\r\n- [dup](feedback_dup.md)\r\n" if duplicated else ""
    (root / "MEMORY.md").write_bytes(
        ("# I\r\n## Rules\r\n- [a](feedback_a.md)\r\n- [dup](feedback_dup.md)\r\n"
         + tail).encode("utf-8"))
    for name in ("feedback_a.md", "feedback_dup.md", "feedback_orphan.md"):
        (root / name).write_text("x", encoding="utf-8")
    return root


def _older_interpreter():
    """The FIRST candidate older than the one running the suite, or None.

    First, not oldest and not the declared floor: the walk returns as soon as a
    candidate is below the running version, so which interpreter a caller gets
    depends on what the host has installed.
    """
    for candidate in ("/usr/bin/python3", "python3.9", "python3.10", "python3.11"):
        try:
            out = subprocess.run(
                [candidate, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0:
            found = tuple(int(n) for n in out.stdout.split())
            if found < sys.version_info[:2]:
                return candidate, found
    return None, None


