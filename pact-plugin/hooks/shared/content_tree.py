"""
Location: pact-plugin/hooks/shared/content_tree.py
Summary: The content tree id of a working tree's input files, and which input files differ
         between two such ids. A run's tree id is the git tree its files would commit
         as, staged or not: `git update-index` of the changed input files into a COPY of the
         index (a throwaway GIT_INDEX_FILE, the real index is never written), then
         `git write-tree`. The copy holds input files only (every other index entry is
         force-removed from it), so the same input files give the same id whether
         they are committed, staged or only edited: a commit after a green run, of an input
         or an excluded file, does not make the run look stale, and an edit after it does
         (`code_diff`). uncommitted_inputs names the input files HEAD does not hold as they are.
         An input file (is_input_path) is any non-ignored project file a run can
         depend on, config and data included; never docs, generated or cache output, or a
         file of the `.env` or credential class (is_secret_path): a non-ignored `.env` leaves
         no new object behind. is_code_path, narrower, decides what makes a code task.
         Every git call has a deadline; any failure gives None.
Used by: bin/tcount (records `tree` on its ledger row before the run), hooks/shared/
         evidence_check.py (the input-change predicate, the stale-run check and the
         uncommitted-input refusal). Standard library only.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import PurePosixPath
from typing import Optional

GIT_SECONDS = 10
CHANGED_MAX = 5000              # changed input files hashed at most; more gives None
# Source and script files. A config, data or doc file (json, yaml, toml, md) is not code here:
# its change does not need a test run.
CODE_SUFFIXES = frozenset((
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs",
    ".java", ".kt", ".kts", ".scala", ".rb", ".php", ".cs", ".fs", ".c", ".h", ".cc", ".cpp",
    ".hpp", ".m", ".mm", ".swift", ".sh", ".bash", ".zsh", ".ps1", ".sql", ".lua", ".pl", ".ex",
    ".exs", ".erl", ".clj", ".dart", ".vue", ".svelte", ".r", ".jl", ".gs",
))
# An extensionless file in one of these directories is a script (bin/tcount, hooks/x).
SCRIPT_DIRS = frozenset(("bin", "hooks", "scripts"))
_TEST_NAME = re.compile(r"(?:^test_.+|.+_test|.+\.(?:test|spec))\.[A-Za-z0-9]+$|^conftest\.py$")
_TEST_DIRS = frozenset(("tests", "test", "__tests__", "spec"))
# The guards' .env class (shell/env/names.is_protected_name, a test pins they agree) and the
# credential files a key or token lives in.
_SECRET_NAME = re.compile(
    r"^(?:\.env(?:\..*)?|.*\.env|\.envrc|\.netrc|id_(?:rsa|dsa|ecdsa|ed25519)(?:\..*)?|credentials"
    r"|.*\.(?:pem|key|p12|pfx)|.*service-account.*\.json|secrets\..*)$", re.IGNORECASE)


def is_secret_path(path: str) -> bool:
    """True for a file of the `.env` class or a credential file, by its name."""
    return bool(_SECRET_NAME.match(PurePosixPath(str(path).replace("\\", "/")).name))


def is_code_path(path: str) -> bool:
    """True for a source or script file (CODE_SUFFIXES, or an extensionless file under bin/,
    hooks/ or scripts/), never for a secret-class file."""
    pure = PurePosixPath(str(path).replace("\\", "/"))
    if not pure.name or is_secret_path(pure.name):
        return False
    if pure.suffix.lower() in CODE_SUFFIXES:
        return True
    return not pure.suffix and not pure.name.startswith(".") and any(
        part in SCRIPT_DIRS for part in pure.parts[:-1])


# Not inputs of a run: docs, and the generated, vendored or cache output a build or a test run
# writes (a cache written by the run itself would make every run look stale).
_NOT_INPUT_DIRS = frozenset((".git", "node_modules", "dist", "build", "__pycache__", ".repacted",
                             ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".venv",
                             "htmlcov", ".hypothesis"))
_NOT_INPUT_NAME = re.compile(r".*\.(?:md|pyc)|\.coverage(?:\..*)?|\.DS_Store", re.IGNORECASE)


def is_input_path(path: str) -> bool:
    """True for a project file a run's result can depend on: every file but the secret class,
    docs (*.md) and generated, vendored or cache output (_NOT_INPUT_DIRS, *.pyc, .coverage)."""
    pure = PurePosixPath(str(path).replace("\\", "/"))
    if not pure.name or is_secret_path(pure.name) or _NOT_INPUT_NAME.fullmatch(pure.name):
        return False
    return not any(part in _NOT_INPUT_DIRS for part in pure.parts[:-1])


def is_test_path(path: str) -> bool:
    """True for a code file that is a test: test_x.py, x_test.go, x.test.ts, x.spec.js,
    conftest.py, or any code file under a tests/, test/, __tests__/ or spec/ directory."""
    pure = PurePosixPath(str(path).replace("\\", "/"))
    if not is_code_path(str(pure)):
        return False
    return bool(_TEST_NAME.match(pure.name)) or any(part in _TEST_DIRS for part in pure.parts[:-1])


def _env(index: Optional[str] = None) -> dict:
    """The caller's environment without the variables that would point git at another tree or
    index, plus no optional locks (a read never rewrites the real index)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                        "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES")}
    env["GIT_OPTIONAL_LOCKS"] = "0"
    if index:
        env["GIT_INDEX_FILE"] = index
    return env


def _git(root: str, *args: str, index: Optional[str] = None, stdin: Optional[bytes] = None) -> Optional[bytes]:
    try:
        done = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", root, *args],
                              input=stdin, capture_output=True, timeout=GIT_SECONDS,
                              env=_env(index), stdin=None if stdin is not None else subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return done.stdout if done.returncode == 0 else None


def work_root(cwd: Optional[str]) -> Optional[str]:
    """The top of the git working tree a directory is in, or None."""
    if not isinstance(cwd, str) or not os.path.isdir(cwd):
        return None
    out = _git(cwd, "rev-parse", "--show-toplevel")
    return out.decode("utf-8", "replace").strip() or None if out else None


def _index_path(root: str) -> Optional[str]:
    out = _git(root, "rev-parse", "--git-path", "index")
    if not out:
        return None
    path = out.decode("utf-8", "replace").strip()
    return path if os.path.isabs(path) else os.path.join(root, path)


def content_tree(cwd: Optional[str]) -> Optional[str]:
    """The tree id of the working tree's input files as they are on disk now, or None (not a git
    tree, git missing or slow, an unmerged index, more than CHANGED_MAX changed input files).

    The copy of the index first loses every entry that is not an input file (docs, cache
    output, the secret class), so a staged or committed excluded file never reaches the tree.
    Then the changed, untracked and deleted files git reports (ls-files against the copy, so
    its stat cache saves rehashing) are filtered to input files and written into it. The copy
    is deleted on the way out."""
    root = work_root(cwd)
    if root is None:
        return None
    index = _index_path(root)
    fd, scratch = tempfile.mkstemp(prefix="repact-tree-", suffix=".index")
    os.close(fd)
    try:
        if index and os.path.isfile(index):
            # copy2 keeps the index file's mtime: git's racy-clean check compares it with each
            # entry's, so a file edited in the same second as the index was written, at the same
            # size, is still rehashed (a fresh mtime on the copy made it look unchanged).
            shutil.copy2(index, scratch)
        else:
            os.unlink(scratch)          # read-tree writes a fresh index file
            if _git(root, "read-tree", "HEAD", index=scratch) is None \
                    and _git(root, "read-tree", "--empty", index=scratch) is None:
                return None
        if not _drop_excluded(root, scratch):
            return None
        listed = _git(root, "ls-files", "-z", "-m", "-o", "-d", "--exclude-standard", index=scratch)
        if listed is None:
            return None
        paths = sorted({p for p in listed.decode("utf-8", "surrogateescape").split("\0")
                        if p and is_input_path(p)})
        if len(paths) > CHANGED_MAX:
            return None
        if paths:
            data = "\0".join(paths).encode("utf-8", "surrogateescape") + b"\0"
            if _git(root, "update-index", "-z", "--add", "--remove", "--stdin",
                    index=scratch, stdin=data) is None:
                return None
        tree = _git(root, "write-tree", index=scratch)
        return tree.decode("ascii", "replace").strip() or None if tree else None
    finally:
        try:
            os.unlink(scratch)
        except OSError:
            pass


def _drop_excluded(root: str, scratch: str) -> bool:
    """Force-remove every index entry of `scratch` that is not an input file; False on a git failure."""
    listed = _git(root, "ls-files", "-z", index=scratch)
    if listed is None:
        return False
    excluded = sorted({p for p in listed.decode("utf-8", "surrogateescape").split("\0")
                       if p and not is_input_path(p)})
    if not excluded:
        return True
    data = "\0".join(excluded).encode("utf-8", "surrogateescape") + b"\0"
    return _git(root, "update-index", "-z", "--force-remove", "--stdin", index=scratch, stdin=data) is not None


def uncommitted_inputs(cwd: Optional[str]) -> Optional[list[str]]:
    """The input files (is_input_path) whose working copy HEAD does not hold: staged, unstaged,
    untracked or deleted. [] when every input is committed, None when git cannot say."""
    root = work_root(cwd)
    if root is None:
        return None
    out = _git(root, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all")
    if out is None:
        return None
    return sorted({entry[3:] for entry in out.decode("utf-8", "surrogateescape").split("\0")
                   if len(entry) > 3 and is_input_path(entry[3:])})


def code_diff(cwd: Optional[str], tree_a: str, tree_b: str) -> Optional[list[str]]:
    """The input files (is_input_path) that differ between two tree ids, [] when none does, None
    when git cannot compare them (an id it no longer has, not a git tree)."""
    if tree_a == tree_b:
        return []
    root = work_root(cwd)
    if root is None or not all(re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", t or "") for t in (tree_a, tree_b)):
        return None
    out = _git(root, "diff-tree", "-r", "-z", "--name-only", "--no-renames", tree_a, tree_b)
    if out is None:
        return None
    return sorted(p for p in out.decode("utf-8", "surrogateescape").split("\0") if p and is_input_path(p))
