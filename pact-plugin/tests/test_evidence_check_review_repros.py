"""Review repros for the red-first check: greens that covers() accepts though they did not run the red's test.

Location: pact-plugin/tests/test_evidence_check_review_repros.py. Each test states the
runner's real behaviour in its docstring (node 24.15.0 probed by hand for the node rows) and asserts
the green must NOT cover the red; a red row here is a hole still open.
Run: tcount uv run --no-project --with pytest python -m pytest -q tests/test_evidence_check_review_repros.py
"""
from __future__ import annotations

from test_evidence_check_node_cargo import covered, gate_mode, git, worktree  # noqa: F401 (fixtures)


def test_a_bare_node_test_green_does_not_cover_a_file_outside_the_default_patterns(worktree):
    """`node --test` with no target runs only **/*.test.js, *-test.js, *_test.js, test-*.js, test.js
    and test/**; tests/foo_spec.js named explicitly in the red is never run by it."""
    assert not covered(worktree, ".", "node --test tests/foo_spec.js", ".", "node --test")


def test_a_double_star_inside_a_segment_does_not_cross_directories(worktree):
    """In a node glob `**` inside a segment acts as `*`: 'test/unit**.js' runs test/unit1.js only,
    never test/unit/a.test.js."""
    assert not covered(worktree, ".", "node --test test/unit/a.test.js", ".", "node --test 'test/unit**.js'")


def test_a_cargo_red_of_ignored_tests_is_not_covered_by_a_green_that_skips_them(worktree):
    """`-- --ignored` runs only #[ignore] tests; a plain `cargo test parse` skips them, so the failing
    ignored test never ran green."""
    assert not covered(worktree, "crates/core", "cargo test parse -- --ignored", "crates/core", "cargo test parse")
    assert not covered(worktree, "crates/core", "cargo test parse -- --include-ignored", "crates/core",
                       "cargo test parse")


def test_a_libtest_option_value_is_not_read_as_a_filter(worktree):
    """`--logfile` takes a value: the red ran every test with no filter, so a green filtered to
    `out` does not cover it."""
    assert not covered(worktree, "crates/core", "cargo test -- --logfile out", "crates/core", "cargo test out")


def test_a_red_in_a_nested_repository_is_not_covered_by_the_same_relative_path_at_the_root(worktree):
    """A nested git repo (a vendored checkout or a submodule) has its own root: its tests/a.py is
    vendor/lib/tests/a.py of the worktree, not the worktree's tests/a.py."""
    nested = worktree / "vendor" / "lib"
    (nested / "tests").mkdir(parents=True)
    (nested / "tests" / "a.py").write_text("def test_a():\n    assert False\n")
    git(worktree, "init", "-q", str(nested))
    assert not covered(worktree, "vendor/lib", "pytest tests/a.py", ".", "pytest tests/a.py")
