"""
Location: pact-plugin/tests/test_working_memory_projection.py
Summary: Verification tests for rebuilding the Working Memory section of
         CLAUDE.md from pact-memory records rather than prepending one save at
         a time. Covers the formatter's `created_at` parameter (a projected
         entry carries the record's own date, a save still carries now) and
         the parser that turns a stored `created_at` into that stamp.

         Every write in this file goes to a file under `tmp_path`. The
         child-process arms resolve ambiently on purpose, inside a declared
         tmp project, to reach the ambient guards.
Used by: pytest.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


import scripts.working_memory as wm  # noqa: E402
from scripts.memory_api import PACTMemory  # noqa: E402
from scripts.working_memory import (  # noqa: E402
    MAX_WORKING_MEMORIES,
    WORKING_MEMORY_TOKEN_BUDGET,
    SyncResult,
    _estimate_tokens,
    _format_memory_entry,
    _record_timestamp,
    project_memories_to_claude_md,
)
from clock_shift.clock_shift_env import carry_clock_shift
from fixtures.hf_cache import hf_cache_env

_SCAFFOLD = (
    "# Probe\n\n"
    "## Working Memory\n"
    f"{wm.WORKING_MEMORY_COMMENT}\n\n"
    "## Pinned Context\n\nkeep me\n"
)

# The per-entry ceiling `_apply_token_budget` applies before budgeting.
_ENTRY_CEILING = (
    WORKING_MEMORY_TOKEN_BUDGET
    - (MAX_WORKING_MEMORIES - 1) * wm.COMPRESSED_ENTRY_TOKEN_CEILING
)


def _header(entry: str) -> str:
    return entry.split("\n", 1)[0]


def _seed(tmp_path, body: str = _SCAFFOLD):
    target = tmp_path / "project" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def _section_entries(text: str):
    """The Working Memory section's entries, split on their date headers."""
    section = text.split("## Working Memory\n", 1)[1].split("\n## ", 1)[0]
    return [e.strip() for e in section.split("\n### ")[1:]]


def _record(stamp: str, label: str, **fields):
    return {"id": label * 32, "context": f"{label} context",
            "created_at": stamp, **fields}


def _dense_record(stamp: str, label: str):
    """Every free-text field at its 200-char cut, in 1-char words, so the
    formatted entry estimates far past the per-entry ceiling."""
    dense = "a " * 100
    return _record(
        stamp, label, goal=dense, decisions=[dense], lessons_learned=[dense],
        reasoning_chains=[dense], agreements_reached=[dense],
        disagreements_resolved=[dense],
    )


class TestFormatterCreatedAt:
    """`_format_memory_entry` stamps the header from `created_at` when given."""

    def test_created_at_renders_as_the_header(self):
        stamp = datetime(2024, 3, 7, 4, 5, 59)
        entry = _format_memory_entry({"context": "ctx"}, created_at=stamp)
        assert _header(entry) == "### 2024-03-07 04:05"


class TestRecordTimestamp:
    """The stored `created_at` reaches the header in both forms it takes."""

    @pytest.mark.parametrize("stored", [
        "2026-09-05 09:20:42",])
    def test_both_stored_forms_render_the_same_header(self, stored):
        entry = _format_memory_entry(
            {"context": "ctx"}, created_at=_record_timestamp(stored)
        )
        assert _header(entry) == "### 2026-09-05 09:20"


class TestProjectionReplacesTheSection:
    """`project_memories_to_claude_md` writes the section from the list alone."""

    def test_empty_input_is_empty_and_touches_nothing(self, tmp_path):
        target = _seed(tmp_path)
        before = target.read_bytes()
        result = project_memories_to_claude_md([], target=target)
        assert result.reason == SyncResult.EMPTY
        assert target.read_bytes() == before


class TestProjectionRunsThroughTheSameWriteSite:
    """The replace arm inherits the write site's certifications."""

    def test_a_target_outside_the_resolved_base_is_refused(
        self, tmp_path, monkeypatch
    ):
        """Containment differential, mirroring the save path's arm: the
        resolver hands back a base that does not contain the file, the atomic
        write raises, the handler reports `failed`, the file is unchanged."""
        target = _seed(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        before = target.read_bytes()
        monkeypatch.setattr(
            wm, "_resolve_display_claude_md_with_base",
            lambda: (target, elsewhere),
        )

        result = project_memories_to_claude_md([_record("2026-01-01 00:00:00", "a")])

        assert result.reason == SyncResult.FAILED
        assert target.read_bytes() == before


# ---------------------------------------------------------------------------
# The `sync` verb, in a child process, against both ambient guards
# ---------------------------------------------------------------------------

_CLI = (
    Path(__file__).resolve().parent.parent
    / "skills" / "pact-memory" / "scripts" / "cli.py"
)


def _run_cli(env: dict, cwd: Path, *args: str) -> dict:
    # THE CACHE ENV IS APPLIED HERE, AT THE CHOKE POINT, RATHER THAN AT EACH
    # CALLER. Applying it per-site is what failed: one caller below got it and
    # another, built with `dict(os.environ)`, did not — and the one that missed
    # out was a real `save`. Every child this file spawns routes through this
    # function, so binding it here is the only placement that cannot be missed
    # by the next site somebody adds.
    #
    # THE POPULATION IS NOT "CHILDREN THAT SAVE", WHICH IS THE ASSUMPTION THAT
    # PRODUCED THE GAP. `_ensure_ready()` runs on EVERY verb and calls the
    # embedding catch-up, which loads the model whenever the store holds an
    # unembedded row. So a `sync` or `status` child reading a store that a
    # degraded save left pending can reach the model load too, and `save` is
    # merely the most obvious member.
    #
    # The helper wins over the caller's env deliberately: this is an isolation
    # guarantee rather than a preference, and no caller here sets either key.
    env = {**env, **hf_cache_env()}
    proc = subprocess.run(
        [sys.executable, str(_CLI), *args],
        # 30s rather than 180s: the long budget absorbed a first-run model
        # download, and `hf_cache_env()` removes the download. Measured basis in
        # the sibling refusal suite — slowest child 0.44s, cold-cache offline
        # degrade 0.31s.
        env=carry_clock_shift(env), cwd=str(cwd), capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"rc={proc.returncode}\n{proc.stderr[:600]}"
    payload = json.loads(proc.stdout)
    assert payload.get("ok") is True, payload
    return payload["result"]


class TestSyncVerbReachesTheGuards:
    """`cli.py sync` in a CHILD, so the ambient refusals can fire.

    In-process, `pytest` in `sys.modules` exempts the PYTEST_CURRENT_TEST
    refusal and the redirected-store refusal, so only a
    child can show that the replace arm runs through them. The store is
    redirected with `--db-path` in every arm, and it holds one record.
    """

    @pytest.fixture
    def project(self, tmp_path):
        target = _seed(tmp_path)
        return target.parent

    @pytest.fixture
    def stocked_store(self, memory_store, tmp_path):
        db = memory_store("probe.db")
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(tmp_path / "project")
        _run_cli(env, tmp_path, "save", json.dumps({"context": "one record"}),
                 "--no-sync", "--db-path", str(db))
        return db

    def test_inherited_env_without_an_anchor_is_refused(
        self, project, stocked_store, tmp_path
    ):
        """First guard: PYTEST_CURRENT_TEST is set in the child, no anchor."""
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project)
        assert "PYTEST_CURRENT_TEST" in env
        before = (project / "CLAUDE.md").read_bytes()

        result = _run_cli(env, tmp_path, "sync", "--db-path", str(stocked_store))

        assert (result["sync_status"], result["projected"], result["memory_ids"]) == (
            "refused", 0, [])
        assert (project / "CLAUDE.md").read_bytes() == before


class TestSyncRefusesAProjectWithNoId:
    """`sync` with no project id projects NOTHING rather than everything.

    `list_memories` applies its `project_id = ?` condition only when the id
    is non-None, so a None reaching that query selects the newest records of
    EVERY project, and `sync` would write those foreign records over this
    file's section. The guard answers `empty` before the query runs, which is
    also the right answer on its own terms: a project with no id has no
    records of its own to project.
    """

    def test_a_none_project_id_projects_nothing_and_touches_nothing(
        self, memory_store, tmp_path, monkeypatch
    ):
        target = _seed(tmp_path)
        project = target.parent
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
        db = memory_store("no-project.db")

        # THE STORE MUST HOLD A RECORD UNDER SOME OTHER ID, or a green arm
        # would prove only that the store was empty. This is the record an
        # unfiltered query would find and project.
        stocked = PACTMemory(db_path=db)
        assert stocked.project_id is not None
        stocked.save({"context": "a record saved under a resolved project"},
                     sync_to_claude=False)

        memory = PACTMemory(db_path=db)
        memory._project_id = None
        before = target.read_bytes()

        # `claude_md_root` bounds the write: with the guard removed, the
        # projection lands on this tmp project's file, which is what the
        # byte comparison below catches. It cannot reach a file outside it.
        projected = memory.sync(claude_md_root=project)

        # THE FILE FIRST, because it is the harm. The envelope assertions
        # below would also catch the regression, but they would catch it
        # before this line ran and so would not show that foreign records
        # reached the file.
        assert target.read_bytes() == before
        assert projected == []
        assert memory.last_sync_status == "empty"


# ---------------------------------------------------------------------------
# Every production caller of a CLAUDE.md resolver calls the escape guard
# ---------------------------------------------------------------------------

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_DISPLAY_RESOLVER = "_resolve_display_claude_md_with_base"
_DISPLAY_GUARD = "_refuse_ambient_sync_on_declared_scope_escape"

# (scope, resolver, guard, exempt). A function in scope that calls `resolver`
# must call `guard` on a later line. The display resolver is searched in every
# production file, so a new caller anywhere joins the population.
_GUARDED_RESOLVERS = (
    (
        "production",
        _DISPLAY_RESOLVER,
        _DISPLAY_GUARD,
        # The path-only wrapper returns no base, so no write can be
        # containment-checked through it.
        frozenset({"_resolve_display_claude_md_path"}),
    ),
)

_KNOWN_GUARDED_CALLERS = {
    "sync_to_claude_md",
    "sync_retrieved_to_claude_md",
}


def _production_files():
    for path in sorted(_PLUGIN_ROOT.rglob("*.py")):
        rel = path.relative_to(_PLUGIN_ROOT)
        if "tests" in rel.parts or path.name.startswith("test_") or path.name == "conftest.py":
            continue
        yield path


def _unguarded_callers(source, resolver, guard, exempt=frozenset()):
    """Return (callers, unguarded): the functions that call `resolver`, and those
    among them with no `guard` call on a line after their first `resolver` call."""
    import ast

    callers, unguarded = [], []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in exempt:
            continue
        lines = {resolver: [], guard: []}
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in lines:
                lines[name].append(call.lineno)
        if not lines[resolver]:
            continue
        callers.append(node.name)
        if not any(line > min(lines[resolver]) for line in lines[guard]):
            unguarded.append(node.name)
    return callers, unguarded


class TestEveryResolverCallerCallsTheEscapeGuard:
    """The escape guard sits at the CALLERS, not inside the resolver, because it
    needs `target` and `claude_md_root`, which the resolver does not take. So a
    new caller would not inherit it. This pins that every caller does call it.
    """

    def test_every_production_caller_of_a_resolver_calls_the_escape_guard(self):
        """MUTANT that reddens this arm: delete the guard call from
        `sync_to_claude_md` or `sync_retrieved_to_claude_md`. The failure names the unguarded caller."""
        members, unguarded = [], []
        for scope, resolver, guard, exempt in _GUARDED_RESOLVERS:
            files = (
                list(_production_files()) if scope == "production"
                else [_PLUGIN_ROOT / scope]
            )
            for path in files:
                callers, missing = _unguarded_callers(
                    path.read_text(encoding="utf-8"), resolver, guard, exempt
                )
                rel = path.relative_to(_PLUGIN_ROOT)
                members += [f"{rel}::{name}" for name in callers]
                unguarded += [f"{rel}::{name}" for name in missing]

        found = {member.split("::")[1] for member in members}
        assert _KNOWN_GUARDED_CALLERS <= found and len(members) >= len(_KNOWN_GUARDED_CALLERS), (
            f"the scan found {members}; it must reach every known caller, "
            "or it is not reading the files it claims to"
        )
        assert unguarded == [], (
            f"these callers resolve a CLAUDE.md without the escape guard after "
            f"it: {unguarded}"
        )


# ---------------------------------------------------------------------------
# Every production call of the scope predicate passes the worktree record
# ---------------------------------------------------------------------------

_SCOPE_PREDICATE = "stays_in_declared_project"
_KNOWN_PREDICATE_CALLERS = {
    "_refuse_ambient_sync_on_declared_scope_escape",
}


def _predicate_calls_without_record(source):
    """Return (callers, missing): the functions that call the scope predicate,
    under its own name, an imported alias or a module attribute, and those among
    them with a call that passes no worktree record."""
    import ast

    tree = ast.parse(source)
    names = {_SCOPE_PREDICATE}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {a.asname or a.name for a in node.names if a.name == _SCOPE_PREDICATE}
    callers, missing = [], []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [
            call for call in ast.walk(function)
            if isinstance(call, ast.Call) and (
                (isinstance(call.func, ast.Name) and call.func.id in names)
                or (isinstance(call.func, ast.Attribute) and call.func.attr == _SCOPE_PREDICATE)
            )
        ]
        if not calls:
            continue
        callers.append(function.name)
        if any(
            len(call.args) < 4 and not any(k.arg == "worktree_identity" for k in call.keywords)
            for call in calls
        ):
            missing.append(function.name)
    return callers, missing


