"""red_first for `node --test` and `cargo test` runs, at the boundary of evidence_check.covers().

Location: pact-plugin/tests/test_evidence_check_node_cargo.py. Real git repos (a main checkout and a
linked worktree under a path with spaces), tcount ledger rows built here. First the replay of task
#104 (two node reds, one from the root and one from packages/ui, then the expanded packages/ui
suite and the browser check), which must be accepted with no waiver. Then each command shape a
green may take and still cover the red, and the refusals that must hold: a false "covers" would
accept work with no red-first evidence.
Run: tcount uv run --with pytest pytest -q tests/test_evidence_check_node_cargo.py (from the plugin root)
"""
from __future__ import annotations

import datetime
import itertools
import shlex
import subprocess

import pytest

from shared import content_tree as ct
from shared import evidence_check as ec

_pid = itertools.count(1000)

BROWSER = "node --test e2e/scripts/wide-layout.browser.mjs"
OVERFLOW = "node --import tsx --test src/layout/panel-overflow.test.tsx"
# The packages/ui suite as tcount records it (the shell expanded the globs).
UI_SUITE = (
    "node --import tsx --test src/render/markdown.test.ts src/render/sanitize.test.ts "
    "src/render/CodeBlock.test.tsx src/render/links.test.tsx src/render/stream.test.tsx "
    "src/layout/AppFrame.scroll.test.tsx src/layout/AppFrame.test.tsx src/layout/Header.test.tsx "
    "src/layout/Sidebar.test.tsx src/layout/Footer.test.tsx src/layout/panel-overflow.test.tsx "
    "src/layout/Toolbar.test.tsx src/forms/LoginForm.test.tsx src/forms/SearchBox.test.tsx "
    "src/forms/UploadDialog.test.tsx src/settings/Profile.test.tsx src/settings/Theme.test.tsx "
    "src/settings/Notifications.test.tsx")
SPACED = "src/my dir/a b.test.ts"


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def commit(tree, message="change"):
    git(tree, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
    git(tree, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)


@pytest.fixture
def worktree(tmp_path):
    """A monorepo checked out at a path with spaces, and the teammate's linked worktree inside it
    (.worktrees/overflow-fix, as in #104)."""
    main = tmp_path / "Work Folder" / "Demo Org" / "demo-app"
    files = ["e2e/scripts/wide-layout.browser.mjs", "packages/ui/src/layout/AppFrame.css",
             "packages/a/src/x.test.ts", "packages/b/src/x.test.ts", f"packages/ui/{SPACED}",
             "crates/core/src/lib.rs", "crates/other/src/lib.rs"]
    files += ["packages/ui/" + word for word in UI_SUITE.split()[4:]]
    for name in files:
        path = main / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("// base\n")
    (main / ".gitignore").write_text(".worktrees/\n")
    git(tmp_path, "init", "-q", "-b", "main", str(main))
    commit(main, "base")
    tree = main / ".worktrees" / "overflow-fix"
    git(main, "worktree", "add", "-q", "-b", "overflow-fix", str(tree))
    return tree


@pytest.fixture(autouse=True)
def gate_mode(monkeypatch):
    monkeypatch.setattr(ec, "use_mode", lambda use: "gate")
    monkeypatch.setattr(ec, "uncommitted_inputs", lambda worktree: [], raising=False)


def stamp(minutes_ago):
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def row(cwd, cmd, passed=1, failed=0, minutes_ago=5, tree_id=None, parsed=True, exit_code=None):
    """One tcount ledger row run in `cwd` (the tree it saw: the worktree's now, unless given)."""
    return {"source": "tcount", "cmd": cmd, "cwd": str(cwd), "ts": stamp(minutes_ago),
            "collected": passed + failed, "passed": passed, "failed": failed, "errors": 0, "skipped": 0,
            "exit_code": (1 if failed else 0) if exit_code is None else exit_code, "count_parsed": parsed,
            "log_path": f"runs/2026-11-04T{next(_pid):06d}Z-{next(_pid)}-node.log",
            "tree": tree_id if tree_id is not None else ct.content_tree(str(cwd))}


def task(tree):
    return {"id": "104", "metadata": {"dispatch": {
        "worktree": str(tree), "kind": "fix", "dispatched_at": stamp(60), "base_tree": ct.content_tree(str(tree))}}}


def fix(tree):
    (tree / "packages" / "ui" / "src" / "layout" / "AppFrame.css").write_text(".panel{min-width:0}\n")


def red_first(verdict):
    return verdict["statuses"]["red_first"]


def covered(tree, red_cwd, red_cmd, green_cwd, green_cmd):
    """Some test the red names is covered by the green, as _red_first judges it."""
    red, green = row(tree / red_cwd, red_cmd, passed=0, failed=1), row(tree / green_cwd, green_cmd)
    cache: dict = {}
    targets = ec.red_targets(red, cache)
    return any(ec.covers(green, target, str(tree), cache, red_run=red) for target in targets)


# --- 1. the #104 replay ---

def test_the_104_replay_is_accepted_with_no_waiver(worktree):
    dispatched, ui = task(worktree), worktree / "packages" / "ui"
    runs = [row(worktree, BROWSER, passed=0, failed=1, minutes_ago=30),
            row(ui, OVERFLOW, passed=1, failed=1, minutes_ago=28)]
    fix(worktree)
    runs += [row(ui, UI_SUITE, passed=213, minutes_ago=24), row(worktree, BROWSER, passed=1, minutes_ago=23)]
    verdict = ec.check(dispatched, runs)
    assert verdict["problems"] == []
    assert red_first(verdict)["state"] == "pass"
    assert "e2e/scripts/wide-layout.browser.mjs" in red_first(verdict)["line"]


def test_the_104_greens_with_no_red_are_refused(worktree):
    dispatched, ui = task(worktree), worktree / "packages" / "ui"
    fix(worktree)
    runs = [row(ui, UI_SUITE, passed=213, minutes_ago=24), row(worktree, BROWSER, passed=1, minutes_ago=23)]
    assert [p["rule"] for p in ec.check(dispatched, runs)["problems"]] == [ec.RED_FIRST]


def test_a_red_after_the_green_on_the_same_inputs_is_refused(worktree):
    dispatched, ui = task(worktree), worktree / "packages" / "ui"
    fix(worktree)
    runs = [row(ui, UI_SUITE, passed=213, minutes_ago=24), row(ui, OVERFLOW, passed=1, failed=1, minutes_ago=20)]
    assert [p["rule"] for p in ec.check(dispatched, runs)["problems"]] == [ec.RED_FIRST]


@pytest.mark.parametrize("parsed, failed, exit_code", [(False, 1, 1), (True, 0, 1)])
def test_a_red_that_crashed_before_any_test_ran_does_not_count(worktree, parsed, failed, exit_code):
    dispatched, ui = task(worktree), worktree / "packages" / "ui"
    runs = [row(ui, OVERFLOW, passed=0, failed=failed, parsed=parsed, exit_code=exit_code, minutes_ago=28)]
    fix(worktree)
    runs.append(row(ui, UI_SUITE, passed=213, minutes_ago=24))
    assert [p["rule"] for p in ec.check(dispatched, runs)["problems"]] == [ec.RED_FIRST]


# --- 2. the shapes a covering green may take ---

@pytest.mark.parametrize("red_cwd, red_cmd, green_cwd, green_cmd", [
    ("packages/ui", OVERFLOW, ".", "node --import tsx --test packages/ui/src/layout/panel-overflow.test.tsx"),
    (".", "node --import tsx --test packages/ui/src/layout/panel-overflow.test.tsx", "packages/ui", OVERFLOW),
    ("packages/ui/src", "node --import tsx --test layout/panel-overflow.test.tsx", "packages/ui", UI_SUITE),
    ("packages/ui", f"node --import tsx --test {shlex.quote(SPACED)}", ".",
     shlex.join(["node", "--import", "tsx", "--test", f"packages/ui/{SPACED}"])),
    ("packages/ui", OVERFLOW, "packages/ui", "node --import tsx --test 'src/layout/*.test.tsx'"),
    ("packages/ui", OVERFLOW, ".", "node --import tsx --test 'packages/**/*.test.tsx'"),
    ("packages/ui", OVERFLOW, "packages/ui",
     "node --import tsx --test --test-concurrency=1 --test-reporter=spec --test-reporter-destination=stdout "
     "--test-timeout=60000 --test-force-exit --no-warnings src/layout/panel-overflow.test.tsx"),
    ("packages/ui", OVERFLOW, "packages/ui",
     "node --test-concurrency 1 --test-reporter tap --import=tsx --test src/layout/panel-overflow.test.tsx"),
    ("packages/ui", "node -C browser --import tsx --test src/layout/panel-overflow.test.tsx", "packages/ui",
     "node --import tsx --conditions=browser --test src/layout/panel-overflow.test.tsx"),
    (".", BROWSER, ".", f"{BROWSER} --test-reporter=dot"),
    # a bare `node --test` at the root runs node's default patterns, so it covers those files
    ("packages/a", "node --test src/x.test.ts", ".", "node --test"),
    (".", "node --test test/unit/helpers.mjs", ".", "node --test"),
    (".", "node --test 'test/**'", ".", "node --test 'test/**'"),
    ("packages/ui", OVERFLOW, ".", "node --import tsx --test 'packages/ui/**/panel-overflow.test.tsx'"),
])
def test_a_green_covers_the_red(worktree, red_cwd, red_cmd, green_cwd, green_cmd):
    assert covered(worktree, red_cwd, red_cmd, green_cwd, green_cmd)


def test_a_contract_test_with_no_red_run_is_read_against_the_spaced_root(worktree):
    green = row(worktree, "node --import tsx --test packages/ui/src/layout/panel-overflow.test.tsx")
    assert ec.covers(green, "packages/ui/src/layout/panel-overflow.test.tsx", str(worktree))
    assert not ec.covers(green, "packages/ui/src/layout/AppFrame.test.tsx", str(worktree))


CARGO_RED = "cargo test --lib parse::tests::rejects_empty"


@pytest.mark.parametrize("green_cmd", [
    "cargo test --lib", "cargo test --lib parse", "cargo test parse::tests", "cargo test",
    "cargo test --lib -- --test-threads=1 --nocapture", "cargo test -q --lib parse::tests::rejects_empty",
    "cargo test --lib -- parse::tests::rejects_empty",
])
def test_a_cargo_green_with_a_wider_or_equal_filter_covers_the_red(worktree, green_cmd):
    assert covered(worktree, "crates/core", CARGO_RED, "crates/core", green_cmd)


# --- 3. the refusals that must hold ---

@pytest.mark.parametrize("red_cwd, red_cmd, green_cwd, green_cmd", [
    # another file, and a same-named file in another package
    ("packages/ui", OVERFLOW, "packages/ui", "node --import tsx --test src/layout/AppFrame.test.tsx"),
    ("packages/a", "node --test src/x.test.ts", "packages/b", "node --test src/x.test.ts"),
    ("packages/a", "node --test src/x.test.ts", ".", "node --test packages/b/src/x.test.ts"),
    ("packages/a", "node --test src/x.test.ts", "packages/b", "node --test x.test.ts"),
    ("packages/a", "node --test src/x.test.ts", "packages/b", "node --test 'src/*.test.ts'"),
    # narrowed
    ("packages/ui", OVERFLOW, "packages/ui", f"{OVERFLOW} --test-name-pattern=shrink"),
    ("packages/ui", OVERFLOW, "packages/ui", f"node --import tsx --test --test-name-pattern shrink {OVERFLOW.split()[-1]}"),
    ("packages/ui", OVERFLOW, "packages/ui", f"{OVERFLOW} --test-skip-pattern=shrink"),
    ("packages/ui", OVERFLOW, "packages/ui", f"{OVERFLOW} --test-only"),
    ("packages/ui", OVERFLOW, "packages/ui", f"{OVERFLOW} --test-shard=1/2"),
    ("packages/ui", OVERFLOW, "packages/ui", f"{OVERFLOW} --test-reporter=./quiet-reporter.mjs"),
    ("packages/ui", OVERFLOW, "packages/ui", f"{OVERFLOW} --env-file=.env.test"),
    # setup mismatch
    ("packages/ui", OVERFLOW, "packages/ui", "node --test src/layout/panel-overflow.test.tsx"),
    ("packages/ui", "node --test src/layout/panel-overflow.test.tsx", "packages/ui", OVERFLOW),
    ("packages/ui", f"{OVERFLOW} --conditions=browser", "packages/ui", f"{OVERFLOW} --conditions=node"),
    ("packages/ui", f"{OVERFLOW} -C browser", "packages/ui", OVERFLOW),
    ("packages/ui", OVERFLOW, "packages/ui", "node --import tsx --require ./setup.cjs --test src/layout/panel-overflow.test.tsx"),
    ("packages/ui", OVERFLOW, "packages/ui", f"{OVERFLOW} --experimental-test-module-mocks"),
    # another runner selecting by its own rules
    ("packages/ui", OVERFLOW, "packages/ui", "cargo test panel-overflow"),
    ("packages/a", "node --test src/x.test.ts", "packages/a", "pytest src/x.test.ts"),
    # modules load in command order
    ("packages/ui", "node --import tsx --import ./setup.mjs --test src/layout/panel-overflow.test.tsx", "packages/ui",
     "node --import ./setup.mjs --import tsx --test src/layout/panel-overflow.test.tsx"),
    # a bare `node --test` runs no file outside node's default patterns, nor one under a dot or
    # node_modules directory, nor one above its directory
    (".", "node --import tsx --test packages/ui/src/layout/panel-overflow.test.tsx", ".", "node --import tsx --test"),
    (".", "node --test packages/a/src/x.spec.js", ".", "node --test"),
    (".", "node --test node_modules/dep/a.test.js", ".", "node --test"),
    (".", "node --test .hidden/a.test.js", ".", "node --test"),
    (".", "node --test testdata/a.js", ".", "node --test"),
    ("packages/a", "node --test ../b/src/x.test.ts", "packages/a", "node --test"),
    # `**` is special only as a whole segment, and no wildcard matches a leading dot
    (".", "node --test packages/a/src/x.test.ts", ".", "node --test 'packages**.ts'"),
    (".", "node --test packages/a/src/x.test.ts", ".", "node --test 'packages/**src/x.test.ts'"),
    (".", "node --test packages/a/.src/x.test.ts", ".", "node --test 'packages/**/x.test.ts'"),
    (".", "node --test packages/a/src/.x.test.ts", ".", "node --test 'packages/a/src/*.test.ts'"),
    (".", "node --test 'packages/a/src/é ü.test.ts'", ".", "node --test 'packages/a/src/é.test.ts'"),
])
def test_a_green_does_not_cover_the_red(worktree, red_cwd, red_cmd, green_cwd, green_cmd):
    assert not covered(worktree, red_cwd, red_cmd, green_cwd, green_cmd)


@pytest.mark.parametrize("red_cmd, green_cmd", [
    (CARGO_RED, "cargo test --lib parse::tests::rejects_empty_input"),
    (CARGO_RED, "cargo test --lib -- --exact parse::tests::rejects_empty"),
    (CARGO_RED, "cargo test --lib -- --skip rejects"),
    (CARGO_RED, "cargo test --lib -- --ignored"),
    (CARGO_RED, "cargo test --lib --no-run"),
    (CARGO_RED, "cargo test --lib --features slow"),
    (CARGO_RED, "cargo test --tests parse::tests::rejects_empty"),
    (CARGO_RED, "cargo test -- --exact"),
    # plain `cargo test` runs no bench and no example tests, so a red there is not covered by it
    ("cargo test --bench speed parse", "cargo test parse"),
    ("cargo test --benches parse", "cargo test parse"),
    ("cargo test --example demo parse", "cargo test parse"),
    ("cargo test --examples parse", "cargo test parse"),
    ("cargo test --all-targets parse", "cargo test parse"),
    # the red's own narrowing must be the green's too
    ("cargo test parse -- --ignored", "cargo test parse"),
    ("cargo test parse -- --include-ignored", "cargo test parse -- --ignored"),
    ("cargo test parse -- --skip slow", "cargo test parse -- --skip fast"),
    ("cargo test parse -- --skip slow", "cargo test parse -- --skip slow --ignored"),
    ("cargo test --no-run parse", "cargo test --no-run parse"),
    ("cargo test parse -- --exotic", "cargo test parse -- --exotic"),
    # a libtest value is no filter, and a value that cannot be read names nothing
    ("cargo test -- --logfile out", "cargo test out"),
    ("cargo test -- --shuffle-seed 7", "cargo test 7"),
    ("cargo test parse -- --skip", "cargo test parse -- --skip"),
])
def test_a_cargo_green_that_selects_less_does_not_cover_the_red(worktree, red_cmd, green_cmd):
    assert not covered(worktree, "crates/core", red_cmd, "crates/core", green_cmd)


@pytest.mark.parametrize("red_cmd, green_cmd", [
    ("cargo test parse -- --ignored", "cargo test parse -- --ignored"),
    ("cargo test parse -- --include-ignored", "cargo test -- --include-ignored"),
    ("cargo test parse::a -- --skip slow --ignored", "cargo test parse -- --ignored --skip slow"),
    ("cargo test parse -- --logfile out", "cargo test parse"),
])
def test_a_cargo_green_with_the_reds_own_narrowing_covers_it(worktree, red_cmd, green_cmd):
    assert covered(worktree, "crates/core", red_cmd, "crates/core", green_cmd)


def test_a_cargo_red_in_a_nested_repository_is_not_covered_from_the_root_crate(worktree):
    nested = worktree / "vendor" / "crates" / "core"
    (nested / "src").mkdir(parents=True)
    (nested / "src" / "lib.rs").write_text("// nested\n")
    git(worktree, "init", "-q", str(worktree / "vendor"))
    assert not covered(worktree, "vendor/crates/core", CARGO_RED, "crates/core", "cargo test --lib")
    assert covered(worktree, "vendor/crates/core", CARGO_RED, "vendor/crates/core", "cargo test --lib")


def test_a_cargo_green_in_another_crate_does_not_cover_the_red(worktree):
    assert not covered(worktree, "crates/core", CARGO_RED, "crates/other", "cargo test --lib")


def test_a_red_or_green_outside_any_git_root_names_nothing(worktree, tmp_path):
    plain = tmp_path / "plain"
    (plain / "src").mkdir(parents=True)
    (plain / "src" / "x.test.ts").write_text("// not in git\n")
    target = str(worktree / "packages" / "a" / "src" / "x.test.ts")
    red, green = row(plain, "node --test src/x.test.ts", passed=0, failed=1, tree_id="0" * 40), \
        row(plain, "node --test src/x.test.ts", tree_id="0" * 40)
    assert not any(ec.covers(green, t, str(worktree), {}, red_run=red) for t in ec.red_targets(red, {}))
    in_repo_red = row(worktree / "packages" / "a", "node --test src/x.test.ts", passed=0, failed=1)
    assert not ec.covers(row(plain, f"node --test {shlex.quote(target)}", tree_id="0" * 40),
                         "src/x.test.ts", str(worktree), {}, red_run=in_repo_red)
    assert ec.covers(row(worktree, f"node --test {shlex.quote(target)}"), "src/x.test.ts", str(worktree), {},
                     red_run=in_repo_red)
