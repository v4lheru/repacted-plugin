"""
Location: pact-plugin/tests/contract/test_cert_retention.py
Summary: the certification-file retention rule (refactor shell lane, design §3.9). A
         `tests/test_*_cert.py` file certifies one fix against a base commit; it earns
         its keep for two releases, then its rows belong in the decision tables
         (tests/fixtures/shell/decisions/) and the file goes. The rule: every cert file
         names its base commit (a module-level sha constant or a load_baseline*/
         load_vendored literal), and the base must not predate the second-newest
         `repacted-v*` tag. A base git cannot resolve counts as old: the fold is the
         only route that keeps the verdicts. The rule covers the shell-guard certs
         (merge guard, env guard, shell words), the files whose rows a decision table
         can hold; a cert elsewhere (tests/test_snapshot_*_cert.py) has no table to
         fold into and is out of scope.
Used by: the contract gate (tests/contract/). The 2026-10-04 fold left no cert file,
         so the file-level test is vacuous until one regrows; the rule itself is
         exercised on synthetic modules.
"""
import ast
import re
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
TESTS = PLUGIN / "tests"
SHA = re.compile(r"^[0-9a-f]{7,40}$")
LOADERS = ("load_baseline", "load_vendored")
CERT_GLOBS = ("test_merge_guard_*_cert.py", "test_env_guard*_cert.py", "test_shell*_cert.py")


def cert_files():
    return sorted(p for g in CERT_GLOBS for p in TESTS.glob(g))


def _git(*args):
    proc = subprocess.run(["git", "-C", str(PLUGIN), *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def release_tags():
    """repacted-v* tags, oldest first by version."""
    _, out = _git("tag", "--list", "repacted-v*")
    tags = []
    for tag in out.split():
        m = re.fullmatch(r"repacted-v(\d+)\.(\d+)\.(\d+)", tag)
        if m:
            tags.append((tuple(int(x) for x in m.groups()), tag))
    return [tag for _, tag in sorted(tags)]


def base_commits(source: str) -> set:
    """Every base sha a cert module names: `X_SHA = "<sha>"` constants and literal
    arguments of load_baseline*/load_vendored calls."""
    tree = ast.parse(source)
    shas = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str) and SHA.match(node.value.value):
            shas.add(node.value.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id.startswith(LOADERS):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and SHA.match(arg.value):
                    shas.add(arg.value)
    return shas


def predates(sha: str, tag: str) -> bool:
    """True when the base is older than the tag (an ancestor of it) or unknown to git."""
    if _git("cat-file", "-e", f"{sha}^{{commit}}")[0] != 0:
        return True
    return _git("merge-base", "--is-ancestor", sha, tag)[0] == 0


def retention_failures(path: Path, source: str, cutoff: str) -> list:
    shas = base_commits(source)
    if not shas:
        return [f"{path.name}: names no base commit (a sha constant or load_* literal)"]
    return [
        f"{path.name}: base {sha} predates {cutoff}: fold into decisions/{sha[:8]}.jsonl and delete"
        for sha in sorted(shas) if predates(sha, cutoff)
    ]


def _cutoff():
    tags = release_tags()
    if len(tags) < 2:
        pytest.skip("fewer than two repacted-v* tags in this clone")
    return tags[-2]


def test_no_cert_file_outlives_two_releases():
    cutoff = _cutoff()
    failures = []
    for path in cert_files():
        failures += retention_failures(path, path.read_text(encoding="utf-8"), cutoff)
    assert not failures, "\n".join(failures)


def test_the_rule_folds_an_old_base_and_keeps_a_fresh_one():
    cutoff = _cutoff()
    old = _git("rev-parse", f"{cutoff}^")[1]
    fresh = _git("rev-parse", "HEAD")[1]
    if predates(fresh, cutoff):
        pytest.skip("HEAD is itself behind the second-newest tag")
    stale = retention_failures(Path("test_x_cert.py"), f'_BASE_SHA = "{old[:8]}"\n', cutoff)
    assert stale == [f"test_x_cert.py: base {old[:8]} predates {cutoff}: fold into decisions/{old[:8]}.jsonl and delete"]
    assert retention_failures(Path("test_y_cert.py"), f'_B = load_vendored("{fresh}")\n', cutoff) == []
    assert retention_failures(Path("test_z_cert.py"), 'D = 1\n', cutoff) == ["test_z_cert.py: names no base commit (a sha constant or load_* literal)"]
    assert predates("0000000000000000000000000000000000000000", cutoff), "an unknown base is old"
