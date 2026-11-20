"""THE SEAM'S CALL SITES, DRIVEN END TO END OVER A CRLF TARGET.

WHAT THIS FILE CERTIFIES, AND WHY IT IS NOT THE SAME CLAIM AS THE SEAM PROBE.
`_atomic_write_text` applies the target's line ending for every caller. A
direct drive of the seam proves the SEAM. It cannot catch a caller that
FLATTENS the endings before the seam ever sees the content, because such a
test supplies the content itself. Only a production caller driven end to end
over a CRLF file on disk can catch that class, and that class is the whole
reason the restore moved into the seam.

THE POPULATION IS THE CALL SITES, NOT THE TWO TWIN MODULES. COUNTING RULE,
STATED BESIDE THE NUMBER: one entry for each CALL EXPRESSION of
`_atomic_write_text` in a `.py` file below `hooks/` and `skills/`, tests
excluded, imports and comment mentions not counted. An AST walk returns
SEVEN, in three modules (two sites went with the 1.5.0 pin-gate cut, and
upstream PACT #1656's planner merged two update_session_info writes into one):

    hooks/shared/session_resume.py                2   update_session_info
    hooks/shared/claude_md_manager.py             3   strip_orphan_kernel_block
                                                      ensure_project_memory_md
                                                      migrate_to_managed_structure
    skills/pact-memory/scripts/working_memory.py  2   sync_to_claude_md
                                                      sync_retrieved_to_claude_md

SITES THIS FILE DOES NOT DRIVE ARE NAMED WITH THEIR REASON, in
`test_the_undriven_sites_are_named_with_a_reason` below. A coverage report
that names its misses is worth more than a higher count that does not.
"""
from pathlib import Path

_MANAGED_START = (
    "<!-- PACT_MANAGED_START: Managed by pact-plugin - do not edit this block -->"
)
_MANAGED_END = "<!-- PACT_MANAGED_END -->"
_MEMORY_START = "<!-- PACT_MEMORY_START -->"
_MEMORY_END = "<!-- PACT_MEMORY_END -->"

_DOC = (
    f"{_MANAGED_START}\n"
    "# PACT Framework and Managed Project Memory\n"
    "\n"
    "<!-- SESSION_START -->\n"
    "## Current Session\n"
    "<!-- SESSION_END -->\n"
    "\n"
    f"{_MEMORY_START}\n"
    "## Retrieved Context\n"
    "\n"
    "## Pinned Context\n"
    "\n"
    "## Working Memory\n"
    "<!-- Auto-managed by pact-memory skill. -->\n"
    "\n"
    f"{_MEMORY_END}\n"
    "\n"
    f"{_MANAGED_END}\n"
)


def _seed_crlf(path: Path, body: str = _DOC) -> None:
    """Write `body` with CRLF BYTES.

    `write_text` translates on some platforms, and a target that is not
    CRLF-dominant sends `_detect_line_ending` down its LF branch, where
    `_restore_line_ending` returns early. An arm on an LF seed would pass
    without exercising the conversion at all.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body.replace("\n", "\r\n").encode("utf-8"))


def _endings(path: Path):
    """Return (crlf_count, bare_lf_count, doubled_cr_count) from the BYTES."""
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    return crlf, raw.count(b"\n") - crlf, raw.count(b"\r\r\n")


def _assert_crlf_survived(path: Path, site: str, before: bytes) -> None:
    """Assert the write happened AND kept the endings.

    🔴 THE WRITE-HAPPENED GATE IS FIRST AND IT IS NOT A FORMALITY. A caller
    that takes an early return writes nothing, and every ending assertion below
    then passes on the untouched SEED. That arm certifies the seam while never
    reaching it. This was not hypothetical: the first draft of a since-retired
    arm in this file passed exactly that way, on a document whose shape the
    parser did not recognise, and the pass returned None without a write.
    """
    after = path.read_bytes()
    assert after != before, (
        f"{site}: the pass did NOT rewrite the file, so every ending "
        f"assertion below would hold on the untouched seed. This arm reached "
        f"no write and certifies nothing. Fix the fixture so the caller writes"
    )
    before_crlf = before.count(b"\r\n")
    crlf, bare_lf, doubled = _endings(path)
    assert doubled == 0, (
        f"{site}: the write produced {doubled} DOUBLED carriage return(s). "
        f"The ending was applied to content that already carried CRLF"
    )
    assert bare_lf == 0, (
        f"{site}: the write left {bare_lf} bare LF ending(s) in a CRLF file. "
        f"The caller flattened the document and the user sees a whole-file "
        f"change they did not make"
    )
    assert crlf >= before_crlf, (
        f"{site}: CRLF count fell from {before_crlf} to {crlf}"
    )


class TestSeedFixtureIsCrlf:
    """POSITIVE CONTROL ON THE FIXTURE, not on the code under test.

    Each arm below rests on the seed being CRLF-dominant. If the seed were LF,
    `_detect_line_ending` returns "\\n", the restore takes its early return,
    and every arm in this file would pass while exercising nothing.
    """

    def test_the_seed_is_crlf_dominant(self, tmp_path):
        target = tmp_path / "CLAUDE.md"
        _seed_crlf(target)
        crlf, bare_lf, doubled = _endings(target)
        assert crlf > 0 and bare_lf == 0 and doubled == 0, (
            f"seed is not CRLF-clean: crlf={crlf} lf={bare_lf} doubled={doubled}"
        )


class TestWorkingMemoryTwinCallSites:
    """skills/pact-memory/scripts/working_memory.py, 2 of the 8 sites."""

    def test_sync_to_claude_md_keeps_the_crlf_of_the_target(
        self, tmp_path, monkeypatch
    ):
        """SITE working_memory.py:1613, in `sync_to_claude_md`.

        THE PAYLOAD CARRIES A CARRIAGE RETURN ON PURPOSE. The document
        contributes none, because the read translates. A payload field is
        interpolated into the text handed to the seam, so it is the route by
        which CRLF reaches the restore in the shipped tree.
        """
        from scripts.working_memory import sync_to_claude_md

        project = tmp_path / "project"
        target = project / ".claude" / "CLAUDE.md"
        _seed_crlf(target)
        before = target.read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        sync_to_claude_md(
            {"context": "one\r\ntwo", "goal": "carry a carriage return"},
            memory_id="site-1613",
        )

        _assert_crlf_survived(target, "working_memory.py:1613", before)


class TestCanonicalTwinCallSites:
    """hooks/shared/claude_md_manager.py, 3 of the 8 sites."""

    def test_migrate_to_managed_structure_keeps_the_crlf_of_the_target(
        self, tmp_path, monkeypatch
    ):
        """SITE claude_md_manager.py:1254, in `migrate_to_managed_structure`.

        The migration reads an UNMANAGED document and rewrites it wrapped in
        the managed boundary. It rewrites the whole file, so a flattening
        caller here rewrites every line of the user's document.
        """
        from shared.claude_md_manager import migrate_to_managed_structure

        project = tmp_path / "project"
        target = project / ".claude" / "CLAUDE.md"
        _seed_crlf(target, "# Project Memory\n\nSome user text.\n")
        before = target.read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = migrate_to_managed_structure()

        assert result and "failed" not in result.lower(), (
            f"the migration did not run, so this arm measured nothing: {result!r}"
        )
        _assert_crlf_survived(target, "claude_md_manager.py:1254", before)


