"""Comprehensive concurrent-write integrity tests for the working_memory
CLAUDE.md sync sites — the TEST-phase extension of the CODE-phase
verification suite in test_working_memory_concurrency.py.

Location: pact-plugin/tests/test_working_memory_concurrency_comprehensive.py

Summary: The verification-level companion file proves the lock serializes two
concurrent writers at the FIRST sync site (sync_to_claude_md). This file
extends that to the gaps a comprehensive TEST phase owns:

  - N>2 barrier-synchronized writers at the first site (lock contention scale).
  - The SECOND site (sync_retrieved_to_claude_md) lost-update under real
    concurrency — the verification file only exercises the first site.
  - Cross-SITE interleave: a Working Memory writer and a Retrieved Context
    writer contend on the SAME file/sidecar simultaneously and must each land
    their entry in their own section (no cross-section clobber).
  - No-regression of the section semantics the lock now wraps: rolling-window
    trim (MAX_*_MEMORIES), idempotent re-sync, PACT-marker survival under lock.

Used by: pytest (the suite's comprehensive working_memory concurrency gate).

NON-VACUITY (the load-bearing methodology)
------------------------------------------
Every lost-update assertion below is a barrier-forced deterministic interleave,
not a timing-hopeful race: all writers rendezvous at a multiprocessing Barrier
BEFORE any sync call, so each reads the same base before any writes back. A
write-only lock (or no lock) would let later writers clobber earlier ones; the
full-read-modify-write lock serializes them so every entry survives.

These assertions are independently FALSIFIED via source-only revert of
working_memory.py to its pre-lock parent in an isolated detached worktree (see
the module docstring of the verification companion + the TEST-phase HANDOFF for
the measured {N fail / M pass} cardinalities). PASS here reflects real
serialization; the revert proves the test detects the unlocked regression.

EXECUTION SHAPE / FLAKE AVOIDANCE
--------------------------------
Workers are module-level functions (picklable for the ``spawn`` start method,
macOS's default and the most portable). Each worker rebuilds its own sys.path +
CLAUDE_PROJECT_DIR (neither crosses a spawn boundary) and resolves the same tmp
.claude/CLAUDE.md, so all writers contend on one sidecar inode. join() carries a
timeout so a deadlock surfaces as a failure rather than an indefinite hang.
"""

import multiprocessing as mp
import os
import re
import sys
from pathlib import Path

import pytest

# spawn is macOS's default and the most portable start method; pin it explicitly
# so the suite behaves identically wherever it runs (fork inherits parent
# interpreter state and can be flaky under pytest).
_MP = mp.get_context("spawn")

# The scripts dir holding working_memory.py — re-derived in each spawned child
# because sys.path does not cross the spawn boundary.
_SCRIPTS_DIR = str(
    Path(__file__).parent.parent / "skills" / "pact-memory" / "scripts"
)

# Join guard: a deadlocked worker (e.g. a lock that never serializes and hangs)
# must fail the test, not wedge the suite.
_JOIN_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Module-level workers (picklable for the spawn start method).
# ---------------------------------------------------------------------------

def _bootstrap(home):
    """Per-child setup: sys.path + CLAUDE_PROJECT_DIR (neither crosses spawn)."""
    if _SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, _SCRIPTS_DIR)
    # _get_claude_md_path checks CLAUDE_PROJECT_DIR first, so every writer
    # resolves the same tmp .claude/CLAUDE.md (and thus the same sidecar inode).
    os.environ["CLAUDE_PROJECT_DIR"] = home


def _wm_worker(args):
    """Drive the REAL sync_to_claude_md once, under a barrier so all writers
    enter their read-modify-write window simultaneously and contend for the
    lock. Each writer commits one uniquely-marked Working Memory entry."""
    home, writer_id, barrier = args
    _bootstrap(home)
    from scripts import working_memory as wm

    barrier.wait()  # rendezvous: everyone reads the same base before any write
    wm.sync_to_claude_md(
        {"context": f"WM-WRITER-{writer_id}", "goal": f"goal-{writer_id}"},
        None,
        f"wm-id-{writer_id}",
    )


def _retrieved_worker(args):
    """Drive the REAL sync_retrieved_to_claude_md once under a barrier. Each
    writer commits one uniquely-marked Retrieved Context entry."""
    home, writer_id, barrier = args
    _bootstrap(home)
    from scripts import working_memory as wm

    barrier.wait()
    wm.sync_retrieved_to_claude_md(
        [{"context": f"RC-WRITER-{writer_id}", "goal": f"rc-goal-{writer_id}"}],
        query=f"query-{writer_id}",
        scores=[0.9],
        memory_ids=[f"rc-id-{writer_id}"],
    )


def _cross_site_worker(args):
    """Cross-site interleave: ``site`` selects which sync function this writer
    drives. A Working Memory writer and a Retrieved Context writer contend on
    the SAME file/sidecar; each must land its entry in its own section."""
    home, site, barrier = args
    _bootstrap(home)
    from scripts import working_memory as wm

    barrier.wait()
    if site == "wm":
        wm.sync_to_claude_md(
            {"context": "CROSS-WM", "goal": "cross-wm-goal"}, None, "cross-wm-id"
        )
    else:
        wm.sync_retrieved_to_claude_md(
            [{"context": "CROSS-RC", "goal": "cross-rc-goal"}],
            query="cross-query",
            scores=[0.9],
            memory_ids=["cross-rc-id"],
        )


# ---------------------------------------------------------------------------
# Seeding helpers.
# ---------------------------------------------------------------------------

def _seed_claude_md(home: Path, *, with_retrieved: bool = False) -> Path:
    """Create a minimal project CLAUDE.md.

    The Working Memory section is always present. When ``with_retrieved`` is set
    a Retrieved Context section is seeded too — exercising the section-replace
    branch rather than the section-create branch at the second site.
    """
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    claude_md = claude_dir / "CLAUDE.md"
    parts = ["# Project\n\n"]
    if with_retrieved:
        parts.append(
            "## Retrieved Context\n"
            "<!-- Auto-managed by pact-memory skill. -->\n\n"
        )
    parts.append(
        "## Working Memory\n"
        "<!-- Auto-managed by pact-memory skill. -->\n\n"
    )
    claude_md.write_text("".join(parts), encoding="utf-8")
    return claude_md


def _run_barrier_procs(target, arg_tuples):
    """Spawn one process per arg-tuple, all gated on a single Barrier, and
    join with a timeout. Asserts every worker exited cleanly (exitcode 0)."""
    procs = [_MP.Process(target=target, args=(t,)) for t in arg_tuples]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=_JOIN_TIMEOUT)
    for p in procs:
        assert p.exitcode == 0, f"worker exited with {p.exitcode} (deadlock?)"


# ---------------------------------------------------------------------------
# N>2 writers at the first site.
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestNWriterLostUpdate:
    """Lock contention at scale: N>2 barrier-synchronized writers. The
    verification companion proves N=2; this proves the lock serializes an
    arbitrary fan-in into a well-formed rolling window, not just a pair."""

    @pytest.mark.parametrize("n_writers", [4,])
    def test_n_concurrent_writers_fill_rolling_window(self, n_writers, tmp_path):
        """N real processes that enter their read-modify-write window
        simultaneously must serialize into a well-formed rolling window.

        What is verified (and why it is non-vacuous):
        MAX_WORKING_MEMORIES caps the *displayed* window, so for N>MAX only the
        most-recent MAX writers persist BY DESIGN — "all N survive" is false for
        N>MAX. The lost-update bug is orthogonal: a 2nd writer reading a stale
        base re-writes a file missing the 1st writer's entry, collapsing the
        section toward a SINGLE lone survivor. So the discriminating assertions
        are:
          (1) exactly min(N, MAX) distinct survivors — a clobber collapses this
              toward 1 (the unlocked signature; RED-on-revert measured 1);
          (2) every survivor marker appears EXACTLY ONCE — no duplicate or
              interleaved entry, which a torn read-modify-write could produce;
          (3) the survivor-marker count equals the ``###`` entry-header count in
              the section — no torn/partial entries (every header has its body);
          (4) the survivors are extracted IN FILE ORDER (newest-first, since the
              SUT prepends) and are all distinct — a real positional read, NOT
              the old ``present == sorted(present)`` check which was vacuous
              (that list was built by iterating ``range(N)`` ascending, so it
              was sorted by construction and could never fail).

        Barrier scheduling is non-deterministic, so WHICH min(N, MAX) writers
        win the lock is not predictable; we therefore assert the shape of a
        serialized rolling window, not a specific writer-id suffix.
        """
        from importlib import import_module
        sys.path.insert(0, _SCRIPTS_DIR)
        wm = import_module("scripts.working_memory")
        max_wm = wm.MAX_WORKING_MEMORIES

        claude_md = _seed_claude_md(tmp_path)
        barrier = _MP.Barrier(n_writers)
        _run_barrier_procs(
            _wm_worker,
            [(str(tmp_path), wid, barrier) for wid in range(n_writers)],
        )

        final = claude_md.read_text(encoding="utf-8")
        section = final[final.find("## Working Memory"):]

        # Extract survivors IN FILE ORDER (top = newest, the SUT prepends) — a
        # real positional read of the rendered section, not a range() scan.
        survivors_in_file_order = re.findall(r"WM-WRITER-(\d+)", section)
        survivor_set = set(survivors_in_file_order)
        entry_headers = section.count("### ")

        # (1) Clobber discriminator: a lost update collapses toward 1 survivor.
        assert len(survivors_in_file_order) == min(n_writers, max_wm), (
            f"Expected {min(n_writers, max_wm)} survivors (rolling window of "
            f"{max_wm}), got {len(survivors_in_file_order)}: "
            f"{survivors_in_file_order}. Collapse toward a single survivor is "
            "the lost-update signature — the lock did not serialize the "
            f"read-modify-write window.\n{section}"
        )
        # (2) No duplicate/interleaved markers — each survivor appears once.
        assert len(survivor_set) == len(survivors_in_file_order), (
            "A survivor marker appears more than once — a torn or interleaved "
            f"read-modify-write duplicated an entry: {survivors_in_file_order}"
        )
        # (3) No torn/partial entries: one marker per rendered entry header.
        assert entry_headers == len(survivors_in_file_order), (
            f"Entry-header count ({entry_headers}) != survivor-marker count "
            f"({len(survivors_in_file_order)}) — a partial/torn entry was "
            f"written under contention.\n{section}"
        )


# ---------------------------------------------------------------------------
# Second site: sync_retrieved_to_claude_md under concurrency.
# ---------------------------------------------------------------------------

class TestRetrievedContextLostUpdate:
    """The SECOND sync site must serialize identically — the verification
    companion only covers the first site's lost-update path."""

    def test_two_concurrent_retrieved_writers_both_survive(self, tmp_path):
        """Two real processes driving sync_retrieved_to_claude_md under a
        barrier must BOTH land their Retrieved Context entry. RED on the
        unlocked source, GREEN with the lock (same read-under-lock property as
        the first site)."""
        claude_md = _seed_claude_md(tmp_path, with_retrieved=True)
        barrier = _MP.Barrier(2)
        _run_barrier_procs(
            _retrieved_worker,
            [(str(tmp_path), wid, barrier) for wid in (0, 1)],
        )

        final = claude_md.read_text(encoding="utf-8")
        assert "RC-WRITER-0" in final and "RC-WRITER-1" in final, (
            "A concurrent Retrieved Context writer's entry was lost — the lock "
            "did not serialize the second sync site's read-modify-write "
            f"window.\n{final[final.find('## Retrieved Context'):]}"
        )


# ---------------------------------------------------------------------------
# Cross-site interleave: WM writer vs RC writer on the same file.
# ---------------------------------------------------------------------------

class TestCrossSiteInterleave:
    """A Working Memory writer and a Retrieved Context writer contend on the
    SAME file/sidecar. Because both take the lock on the same resolved
    CLAUDE.md path, neither can clobber the other's section — the cross-section
    no-clobber property the single-site tests cannot show."""

    def test_wm_and_retrieved_writers_both_sections_survive(self, tmp_path):
        """One process writes Working Memory, another writes Retrieved Context,
        both gated on the same barrier. The final file must contain BOTH the WM
        entry and the RC entry — proving the two sync sites serialize against
        each other on the shared sidecar, not just same-site writers.

        Pre-fix (unlocked), the later writer reads a base missing the earlier
        writer's section-rebuild and overwrites the whole file, dropping the
        other section's just-committed entry."""
        claude_md = _seed_claude_md(tmp_path, with_retrieved=True)
        barrier = _MP.Barrier(2)
        _run_barrier_procs(
            _cross_site_worker,
            [(str(tmp_path), site, barrier) for site in ("wm", "rc")],
        )

        final = claude_md.read_text(encoding="utf-8")
        assert "CROSS-WM" in final, (
            "Working Memory entry was clobbered by the concurrent Retrieved "
            f"Context writer.\n{final}"
        )
        assert "CROSS-RC" in final, (
            "Retrieved Context entry was clobbered by the concurrent Working "
            f"Memory writer.\n{final}"
        )


# ---------------------------------------------------------------------------
# No-regression: the lock must not change section semantics.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Drift-test genuinely-fails counter-check.
# ---------------------------------------------------------------------------

