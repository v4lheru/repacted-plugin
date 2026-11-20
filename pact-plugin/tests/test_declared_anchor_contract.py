"""
Location: pact-plugin/tests/test_declared_anchor_contract.py
Summary: The declared containment anchor: that it is CONSULTED rather than
         re-derived, that omitting it changes nothing, and the module-level
         population bounds that two dated observations were resting on.

         WHY A DECLARED ANCHOR AT ALL. The CLAUDE.md target is resolved
         AMBIENTLY -- from CLAUDE_PROJECT_DIR, then two git anchors, then the
         working directory. An anchor COMPUTED from that same resolution agrees
         with the target by construction, so it cannot refuse anything. The
         anchor has to arrive from outside the resolution to mean anything, and
         these tests exist to prove it still does after a refactor that would
         quietly reintroduce a lookup.

         THE VERDICT IS THE OBSERVABLE, NEVER THE ANCHOR'S VALUE. An
         implementation that re-derived the anchor would often produce the same
         STRING as a correct one, so a value comparison passes while
         independence is gone. Every arm below asserts what the write DID.
Used by: pytest.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.working_memory import (  # noqa: E402
    ContainmentError,
    SyncResult,
    _atomic_write_text,
    sync_to_claude_md,
)
from fixtures.hf_cache import hf_cache_env


@pytest.fixture(autouse=True)
def _pin_the_model_cache(monkeypatch):
    """Point this module's children at the operator cache, and refuse network.

    SET ON THE PARENT PROCESS RATHER THAN ON A CHILD `env` DICT, because the
    children here inherit this process's environment through the CLI wiring
    below.

    MODULE-SCOPED AND NOT GLOBAL, deliberately. A conftest-wide autouse would
    also force offline on IN-PROCESS loads elsewhere in the suite, where a
    fixture loads a real model on purpose. That is a different decision with a
    different blast radius and it is not this one.

    Measured: this module's children attempted 2 model loads without it.
    """
    for _k, _v in hf_cache_env().items():
        monkeypatch.setenv(_k, _v)


_REPO = Path(__file__).resolve().parent.parent
_MEMORY_CLI = _REPO / "skills" / "pact-memory" / "scripts" / "cli.py"

SCAFFOLD = (
    "# {title}\n\n"
    "## Working Memory\n"
    "<!-- Auto-managed by pact-memory skill. -->\n"
)


# ---------------------------------------------------------------------------
# The control itself: a missing anchor is refused AT the control.
# ---------------------------------------------------------------------------

class TestMissingAnchorIsRefusedAtTheControl:
    """`_atomic_write_text` must not accept a None anchor from anyone.

    None is not reachable through either writer today. That is the point: the
    containment guarantee rested on two callers guarding correctly PLUS a
    resolver returning a PAIRED (None, None), and both of those guards test the
    TARGET rather than the anchor. A control whose safety depends on its
    callers' discipline is one refactor from being unguarded.

    AND THE OLD FAILURE WAS ACCIDENTAL. `os.stat(str(None))` stats the literal
    relative path "None" and raises -- until a directory named `None` exists in
    the working directory, at which point it resolves and that directory
    silently becomes the boundary every write is measured against.
    """

    def test_none_anchor_raises_containment_error(self, tmp_path):
        target = tmp_path / "CLAUDE.md"
        target.write_text("before", encoding="utf-8")

        with pytest.raises(ContainmentError):
            _atomic_write_text(target, "after", None)

        assert target.read_text(encoding="utf-8") == "before", (
            "the refused write still modified the file"
        )


# ---------------------------------------------------------------------------
# The anchor is additive: omitting it changes nothing.
# ---------------------------------------------------------------------------

class TestOmittingTheAnchorChangesNothing:
    """Additive with a default. Every existing caller omits it."""

    def test_omitted_anchor_writes_exactly_as_before(self, tmp_path, monkeypatch):
        md = tmp_path / "CLAUDE.md"
        md.write_text(SCAFFOLD.format(title="Ambient"), encoding="utf-8")
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

        result = sync_to_claude_md({"context": "no anchor given"}, memory_id="a" * 32)

        assert result.reason == SyncResult.WROTE
        assert "no anchor given" in md.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Item 5: enforceability THROUGH THE TEST WIRING, at the _run_memory_cli level.
# ---------------------------------------------------------------------------

class TestAnchorIsEnforceableThroughTheCliWiring:
    """Plumbing and containment THROUGH THE TEST WIRING. NOT a production replay.

    What these arms certify is that
    when a caller DOES declare an anchor through this wiring, the declaration
    is consulted at the write and can refuse it.

    THE PRE-EXISTING SUPPRESSION ARM CERTIFIES PLUMBING, NOT ENFORCEABILITY --
    its fixture creates a CLAUDE.md beside the declared directory, so the
    containment check passes trivially and would pass just as well against an
    anchor that had been re-derived from the resolution. These arms put the
    target OUTSIDE the declared root, which is the only shape in which a
    declared anchor and a derived one give different answers.

    THE VERDICT IS THE OBSERVABLE. `wrote` versus `failed` -- never a
    comparison of anchor strings, which a re-deriving implementation would
    often pass.
    """

    @pytest.fixture
    def nested(self, tmp_path):
        """A repo with a CLAUDE.md, and a sandbox nested inside it with none.

        The git anchor is what makes the target resolve OUT of the sandbox and
        INTO the repo; without it the resolver finds nothing and the arm would
        measure an unresolved target rather than a refused one.
        """
        repo = tmp_path / "repo"
        (repo / "sandbox").mkdir(parents=True)
        (repo / "CLAUDE.md").write_text(
            SCAFFOLD.format(title="Repo"), encoding="utf-8"
        )
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.email", "t@e.com"],
            ["git", "config", "user.name", "T"],
        ):
            subprocess.run(cmd, cwd=str(repo), capture_output=True, check=True)
        return repo


    def _save_through_wiring(self, cwd, db_path, tag):
        """Spawn the CLI `save` with `cwd` declared as `--claude-md-root`, the
        way a caller that knows its project supplies the anchor.

        `db_path` IS A STORE THAT IS PRESENT. `cli.main` refuses a
        `--db-path` naming a store that is absent, for each command other
        than `setup`, so callers pass the `memory_store` fixture result."""
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(cwd))
        proc = subprocess.run(
            [sys.executable, str(_MEMORY_CLI), "save", "--stdin",
             "--db-path", str(db_path), "--claude-md-root", str(cwd)],
            input=json.dumps({"context": tag}), capture_output=True,
            text=True, cwd=str(cwd), env=env, timeout=300,
        )
        assert proc.returncode == 0, f"CLI failed: {proc.stderr[:300]}"
        return json.loads(proc.stdout)["result"]

    def test_target_outside_the_declared_root_is_refused(
        self, nested, memory_store
    ):
        """The declared root is the sandbox; the target resolves to the repo."""
        repo_md = nested / "CLAUDE.md"
        before = repo_md.read_bytes()

        result = self._save_through_wiring(
            nested / "sandbox", memory_store("probe.db"), "must be refused"
        )

        assert result["sync_status"] == "failed", (
            f"a target outside the declared root was not refused: {result}"
        )
        assert repo_md.read_bytes() == before, "the repo CLAUDE.md was written"


# ---------------------------------------------------------------------------
# Item 4 + addendum: the population bounds two dated observations rested on.
# ---------------------------------------------------------------------------


class TestCliSearchDoesNotSync:
    """Item 4a. `cmd_search` passing `sync_to_claude=False` was UNPINNED.

    The only related test asserted the API DEFAULT is True -- the opposite
    direction -- so nothing held the CLI's behaviour in place. Several comments
    in the tree lean on it as a dated observation; this is what makes it a fact.
    """

    def test_cmd_search_suppresses_the_retrieved_context_sync(self, tmp_path):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from scripts import cli

        fake = MagicMock()
        fake.search.return_value = []
        with patch.object(cli, "PACTMemory", return_value=fake), \
             patch.object(cli, "_success"):
            cli.cmd_search(
                SimpleNamespace(query="q", limit=5, current_file=None,
                                claude_md_root=None),
                db_path=str(tmp_path / "x.db"),
            )

        assert fake.search.call_args.kwargs["sync_to_claude"] is False, (
            "cmd_search no longer suppresses its sync -- the CLI's search verb "
            "can now write to CLAUDE.md, which several comments assume it cannot"
        )


