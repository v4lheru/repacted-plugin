"""
Location: pact-plugin/tests/test_lint_check_files_mode.py
Summary: Subprocess contract tests for lint-check.sh --files mode (the
         consumer-facing tier of the import-hygiene ladder): the three
         verdict outcomes and their exit codes, the crash-honesty guard
         (a checker crash degrades to SKIPPED, never a phantom FINDINGS),
         and the missing-path pre-filter.
Used by: pytest suite. The predicate these rungs call is covered in
         test_check_unused_imports.py; the dev-repo strict-tier gate lives
         in test_import_hygiene.py. THIS file pins the shell contract that
         coder dispatch prose relies on: the LAST stdout line is exactly one
         verdict, and exit 1 means real findings — nothing else.

Scope boundary — --files mode ONLY:
    The legacy whole-tree directory mode (reached by passing a directory
    instead of --files) is a separate mode with separate ownership and is
    deliberately NOT pinned here. Everything below invokes the script with
    --files as the first argument, which returns before the legacy mode's
    `set -e` is ever enabled.

Determinism across environments:
    Rows that expect findings use a module-level unused import, which every
    rung of the ladder (ruff, pyflakes, flake8, stdlib fallback) reports as
    exactly one path:line-format line — so the verdict and count assertions
    hold no matter which rung wins on the host. The crash-guard row controls
    the ladder explicitly through PATH: a fake ruff that passes its execution
    probe and then crashes, plus a fake python3 that fails every probe, so
    no real rung can run and the fail-open degradation path is forced.
"""

import os
import stat
import subprocess
from pathlib import Path
import pytest

_SCRIPT = (
    Path(__file__).parent.parent
    / "skills"
    / "pact-coding-standards"
    / "scripts"
    / "lint-check.sh"
)

def _run(*argv, env=None):
    return subprocess.run(
        ["bash", str(_SCRIPT), "--files", *argv],
        capture_output=True,
        text=True,
        env=env,
    )


def _last_stdout_line(proc):
    lines = [line for line in proc.stdout.splitlines() if line]
    assert lines, f"no stdout at all (stderr: {proc.stderr!r})"
    return lines[-1]


_UNRESOLVED = "IMPORT-HYGIENE: SKIPPED (arguments given but none is a checkable .py file)"
_NO_ARGS = "IMPORT-HYGIENE: SKIPPED (no arguments given)"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class TestVerdictContract:
    """The three verdict outcomes: every invocation ends in exactly one
    verdict as the LAST stdout line, with the documented exit code."""

    def test_clean_file_pass_exit_zero(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("import os\nprint(os.sep)\n", encoding="utf-8")
        proc = _run(str(f))
        assert proc.returncode == 0
        assert _last_stdout_line(proc) == "IMPORT-HYGIENE: PASS"


class TestUnresolvedPathsVerdict:
    """Every argument-derived SKIPPED state must stay DISTINCT from the others.

    Collapsing any two is how a malformed invocation came to read as a clean
    check: a caller whose shell did not word-split a quoted file list passes
    ONE argument, "a.py b.py c.py". It ends in .py so the *.py filter matches,
    then fails the existence test, leaving nothing to check — and the verdict
    claimed nothing had been given at all, which was false and pass-shaped.

    Each case below reaches its verdict for a DIFFERENT reason, named beside
    it. A mutation merging two branches reddens at least one of them, and no
    two cases share the set of mutations that redden them -- that separation,
    not any fixed count, is what keeps the states distinct. Arms are named by
    PREFIX rather than in full, so a rename does not strand this list:
      (a) no arguments at all          -> test_zero_arguments_*
      (b) an argument that is not .py  -> test_non_py_argument_* (one arm here,
                                          one in TestVerdictContract above)
      (c) .py-shaped arguments that do
          not resolve to a regular file -> the remaining arms in this class

    The fourth SKIPPED state, no usable checker, is not argument-derived and
    is pinned by TestCrashHonestyGuard instead.
    """

    def test_zero_arguments_reports_no_arguments_given(self):
        # Reason: nothing was passed, so the loop body never runs and no
        # counter moves. This is the ONLY state that reaches this verdict —
        # every argument increments exactly one of the three counters, and
        # the `*)` arm matches even an empty string. The verdict therefore
        # names the condition (no arguments) rather than the wider property
        # it used to claim (no Python files), which was true of states that
        # now take the other branch.
        proc = _run()
        assert proc.returncode == 0
        assert _last_stdout_line(proc) == _NO_ARGS


class TestOnlyNamedFilesAreChecked:
    """A directory named `*.py` must not smuggle its contents into the check.

    The header contract is that only the files named on the command line are
    checked. A directory passes the `*.py` suffix filter, and an existence test
    that accepts any directory entry lets the checker recurse into it, so a
    finding surfaces from a file the caller never named -- with a path the
    caller cannot account for. A regular-file test is what keeps the contract
    and the behaviour the same thing.
    """

    def test_a_directory_named_like_a_module_yields_no_findings(self, tmp_path):
        package = tmp_path / "notamodule.py"
        package.mkdir()
        # An unused import: every rung of the ladder reports this one.
        (package / "inner.py").write_text("import os\n", encoding="utf-8")

        proc = _run(str(package))

        assert proc.returncode == 0, (
            f"a directory named like a module produced exit {proc.returncode}. "
            f"Exit 1 means the checker recursed into it and reported findings "
            f"from a file that was never named. stdout={proc.stdout!r}"
        )
        assert "FINDINGS" not in proc.stdout, (
            f"findings surfaced from inside a directory that was merely named "
            f"on the command line: {proc.stdout!r}"
        )


