"""The slim evidence check `repact accept` runs (hooks/shared/evidence_check.py).

Location: pact-plugin/tests/test_evidence_check.py. A real git repo with a linked worktree per
test; the runs are tcount ledger rows built here and passed to check(), and one test reads them back
from a ledger file. Each status is shown red and green: inputs_changed, run_recorded,
inputs_unchanged, checks_passed, red_first and needs_human. Then the boundary: tree equality across
two worktrees, a config edit not named anywhere makes the run stale, red_first for fix
and feature only (an unknown kind is judged as feature), the tree-id cap, and the
freshness tests ported from the old code gate.
Run: tcount pytest -q tests/test_evidence_check.py (from the plugin root)
"""
from __future__ import annotations

import datetime
import itertools
import json
import subprocess

import pytest

from shared import content_tree as ct
from shared import evidence_check as ec
from shared.ledger import ledger_append

TEST_ID = "tests/test_app.py::test_fix"
_pid = itertools.count(1000)
REAL_UNCOMMITTED = getattr(ec, "uncommitted_inputs", None)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def commit(tree, message="change"):
    git(tree, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
    git(tree, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", message)


@pytest.fixture
def repo(tmp_path):
    """A repo with one commit: src/app.py and its test."""
    main = tmp_path / "repo"
    (main / "src").mkdir(parents=True)
    (main / "tests").mkdir()
    (main / "src" / "app.py").write_text("def f():\n    return 1\n")
    (main / "tests" / "test_app.py").write_text("def test_fix():\n    pass\n")
    git(tmp_path, "init", "-q", "-b", "main", str(main))
    commit(main, "base")
    return main


@pytest.fixture
def worktree(repo, tmp_path):
    """The teammate's linked worktree on its own branch."""
    tree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "feat", str(tree))
    return tree


def stamp(minutes_ago):
    moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def row(tree, minutes_ago, cmd, passed=3, failed=0, tree_id=None):
    """One tcount ledger row taken in `tree`, with the content tree it saw (now, unless given)."""
    return {"source": "tcount", "cmd": cmd, "cwd": str(tree), "ts": stamp(minutes_ago),
            "collected": passed + failed, "passed": passed, "failed": failed, "errors": 0,
            "skipped": 0, "exit_code": 1 if failed else 0, "count_parsed": True,
            "log_path": f"runs/2026-10-19T{next(_pid) % 1000000:06d}Z-{next(_pid)}-pytest.log",
            "tree": tree_id if tree_id is not None else ct.content_tree(str(tree))}


def task(tree, kind="fix", base=None, minutes_ago=60, **dispatch):
    """A task as dispatch stores it: the worktree, its tree id then, the time and the kind."""
    record = {"worktree": str(tree), "kind": kind, "dispatched_at": stamp(minutes_ago),
              "base_tree": base if base is not None else ct.content_tree(str(tree))}
    record.update(dispatch)
    return {"id": "5", "metadata": {"dispatch": record}}


def edit(tree, text="def f():\n    return 2\n"):
    (tree / "src" / "app.py").write_text(text)


def red_then_green(tree):
    """The fix workflow: a failing run of the new test, the fix, then a passing run."""
    red = row(tree, 9, f"pytest -q {TEST_ID}", passed=0, failed=1)
    edit(tree)
    return [red, row(tree, 5, "pytest -q tests/test_app.py")]


def state(verdict, name):
    return verdict["statuses"][name]["state"]


def rules(verdict):
    return [problem["rule"] for problem in verdict["problems"]]


@pytest.fixture(autouse=True)
def gate_mode(monkeypatch):
    """Judge in the shipped 'gate' mode whatever the local checks.json says, and with every input
    committed: the rows below edit without committing, and the uncommitted-input rows (real_commit_check) judge
    the uncommitted-input check itself."""
    monkeypatch.setattr(ec, "use_mode", lambda use: "gate")
    monkeypatch.setattr(ec, "uncommitted_inputs", lambda worktree: [], raising=False)


# --- the happy path ---

def test_red_then_green_on_the_current_inputs_passes_every_status(worktree):
    dispatched = task(worktree)
    verdict = ec.check(dispatched, red_then_green(worktree))
    assert verdict["problems"] == []
    assert [state(verdict, name) for name in ("inputs_changed", "run_recorded", "inputs_unchanged",
                                              "checks_passed", "red_first")] == \
        ["yes", "pass", "pass", "not_required", "pass"]
    assert verdict["tree"] == ct.content_tree(str(worktree)) and len(verdict["runs"]) == 1


# --- inputs_changed ---

def test_inputs_changed_names_the_changed_file_and_is_no_on_the_dispatch_tree(worktree):
    dispatched = task(worktree, kind="docs")
    assert state(ec.check(dispatched, []), "inputs_changed") == "no"
    edit(worktree)
    verdict = ec.check(dispatched, [])
    assert state(verdict, "inputs_changed") == "yes"
    assert "src/app.py" in verdict["statuses"]["inputs_changed"]["line"]


def test_a_docs_task_with_no_input_change_is_accepted_as_not_verified(worktree):
    (worktree / "notes.md").write_text("# notes\n")      # *.md is not an input file
    verdict = ec.check(task(worktree, kind="docs", base=ct.content_tree(str(worktree))), [])
    assert verdict["problems"] == []
    assert state(verdict, "run_recorded") == state(verdict, "inputs_unchanged") == "not_required"
    assert "nothing was verified" in verdict["statuses"]["run_recorded"]["line"]
    assert state(verdict, "red_first") == "not_required"


def test_a_missing_base_tree_counts_as_changed(worktree):
    dispatched = task(worktree, kind="docs")
    dispatched["metadata"]["dispatch"]["base_tree"] = None
    verdict = ec.check(dispatched, [])
    assert state(verdict, "inputs_changed") == "yes" and rules(verdict) == [ec.NO_RUN]


# --- run_recorded ---

def test_a_code_change_with_no_run_is_refused_and_a_run_clears_it(worktree):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    verdict = ec.check(dispatched, [])
    assert state(verdict, "run_recorded") == "fail" and rules(verdict) == [ec.NO_RUN]
    verdict = ec.check(dispatched, [row(worktree, 2, "pytest -q")])
    assert state(verdict, "run_recorded") == "pass" and verdict["problems"] == []


@pytest.mark.parametrize("bad", [
    {"failed": 1}, {"exit_code": 1}, {"passed": 0}, {"count_parsed": False}, {"tree": None},
    {"tree": "not-a-tree-id"}, {"ts": "yesterday"},
])
def test_a_row_that_is_not_a_passing_counted_run_is_no_run(worktree, bad):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    run = dict(row(worktree, 2, "pytest -q"), **bad)
    assert rules(ec.check(dispatched, [run])) == [ec.NO_RUN]


def test_a_run_from_before_dispatch_backs_nothing(worktree):
    dispatched = task(worktree, kind="refactor", minutes_ago=10)
    edit(worktree)
    assert rules(ec.check(dispatched, [row(worktree, 20, "pytest -q")])) == [ec.NO_RUN]


# --- inputs_unchanged (stale runs) ---

def test_a_config_edit_after_the_run_named_nowhere_makes_it_stale(worktree):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    run = row(worktree, 5, "pytest -q")
    (worktree / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = '-x'\n")
    verdict = ec.check(dispatched, [run])
    assert state(verdict, "inputs_unchanged") == "fail" and rules(verdict) == [ec.STALE_RUN]
    assert "pyproject.toml" in verdict["problems"][0]["line"]
    assert ec.check(dispatched, [run, row(worktree, 1, "pytest -q")])["problems"] == []


def test_a_commit_does_not_make_a_run_stale(worktree):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    run = row(worktree, 5, "pytest -q")
    commit(worktree)
    assert ec.check(dispatched, [run])["problems"] == []


def test_a_stale_line_lists_five_files_and_counts_the_rest(worktree):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    run = row(worktree, 5, "pytest -q")
    for index in range(7):
        (worktree / "src" / f"m{index}.py").write_text("x = 1\n")
    line = ec.check(dispatched, [run])["problems"][0]["line"]
    assert "and 2 more" in line


# --- tree equality across worktrees ---

def test_a_run_in_another_worktree_with_the_same_inputs_counts(repo, worktree, tmp_path):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-q", "-b", "other", str(other))
    edit(other)
    assert ct.content_tree(str(other)) == ct.content_tree(str(worktree))
    verdict = ec.check(dispatched, [row(other, 2, "pytest -q")])
    assert verdict["problems"] == [] and state(verdict, "inputs_unchanged") == "pass"


def test_a_run_in_another_worktree_with_other_inputs_backs_nothing(repo, worktree, tmp_path):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-q", "-b", "other", str(other))
    edit(other, "def f():\n    return 3\n")
    assert rules(ec.check(dispatched, [row(other, 2, "pytest -q")])) == [ec.NO_RUN]


# --- checks_passed (pinned acceptance criteria) ---

def test_a_pinned_criterion_needs_a_run_of_the_same_selection_on_the_current_inputs(worktree):
    dispatched = task(worktree, kind="refactor", acceptance_pin={"AC-1": "pytest tests/test_app.py"})
    edit(worktree)
    verdict = ec.check(dispatched, [row(worktree, 3, "pytest -q tests/test_other.py")])
    assert state(verdict, "checks_passed") == "fail" and rules(verdict) == [ec.SELECTION_MISMATCH]
    verdict = ec.check(dispatched, [row(worktree, 3, "pytest -q tests/test_app.py")])
    assert state(verdict, "checks_passed") == "pass" and verdict["problems"] == []
    assert "AC-1" in verdict["statuses"]["checks_passed"]["line"]


def test_a_pinned_criterion_whose_run_is_stale_is_refused_as_stale(worktree):
    dispatched = task(worktree, kind="refactor", acceptance_pin={"AC-1": "pytest tests/test_app.py"})
    edit(worktree)
    run = row(worktree, 5, "pytest tests/test_app.py")
    edit(worktree, "def f():\n    return 4\n")
    verdict = ec.check(dispatched, [run, row(worktree, 2, "pytest -q tests/test_other.py")])
    assert rules(verdict) == [ec.STALE_RUN]


def test_no_pin_says_so_and_a_malformed_pin_needs_a_human(worktree):
    verdict = ec.check(task(worktree, kind="docs"), [])
    assert state(verdict, "checks_passed") == "not_required"
    assert "no acceptance criterion" in verdict["statuses"]["checks_passed"]["line"]
    verdict = ec.check(task(worktree, kind="docs", acceptance_pin=["pytest"]), [])
    assert rules(verdict) == [ec.NEEDS_HUMAN]


# --- red_first ---

@pytest.mark.parametrize("kind", ["fix", "feature", None, "bogus"])
def test_a_fix_or_feature_needs_a_red_run_first(worktree, kind):
    dispatched = task(worktree, kind=kind)
    edit(worktree)
    verdict = ec.check(dispatched, [row(worktree, 2, "pytest -q tests/test_app.py")])
    assert state(verdict, "red_first") == "fail" and rules(verdict) == [ec.RED_FIRST]


@pytest.mark.parametrize("kind", ["refactor", "config", "docs"])
def test_other_kinds_need_no_red_run(worktree, kind):
    dispatched = task(worktree, kind=kind)
    edit(worktree)
    verdict = ec.check(dispatched, [row(worktree, 2, "pytest -q tests/test_app.py")])
    assert state(verdict, "red_first") == "not_required" and verdict["problems"] == []


def test_a_red_run_after_the_first_green_is_no_red_first(worktree):
    dispatched = task(worktree)
    edit(worktree)
    green = row(worktree, 9, "pytest -q tests/test_app.py")
    red = row(worktree, 5, f"pytest -q {TEST_ID}", passed=0, failed=1)
    verdict = ec.check(dispatched, [green, red])
    assert rules(verdict) == [ec.RED_FIRST]
    # the refusal names the way through, not a cosmetic edit for fresh inputs
    line = verdict["problems"][0]["line"]
    assert "temporary revert commit" in line and "--waive" in line, line


def test_a_red_run_of_a_test_the_green_run_does_not_cover_is_no_red_first(worktree):
    dispatched = task(worktree)
    red = row(worktree, 9, "pytest -q tests/test_other.py", passed=0, failed=1)
    edit(worktree)
    green = row(worktree, 5, "pytest -q tests/test_app.py")
    assert rules(ec.check(dispatched, [red, green])) == [ec.RED_FIRST]


# --- needs_human ---

def test_a_tree_over_the_changed_file_cap_needs_a_human(worktree, monkeypatch):
    dispatched = task(worktree)
    monkeypatch.setattr(ct, "CHANGED_MAX", 2)
    for index in range(3):
        (worktree / "src" / f"m{index}.py").write_text("x = 1\n")
    verdict = ec.check(dispatched, [])
    assert verdict["tree"] is None and rules(verdict) == [ec.NEEDS_HUMAN]
    assert "no id" in verdict["problems"][0]["line"]


def test_a_worktree_that_is_not_git_or_not_absolute_needs_a_human(tmp_path):
    for place in (tmp_path, "relative/path", None):
        dispatched = {"metadata": {"dispatch": {"worktree": str(place) if place else None,
                                                "dispatched_at": stamp(5), "kind": "docs"}}}
        assert rules(ec.check(dispatched, [])) == [ec.NEEDS_HUMAN]


def test_an_unreadable_dispatch_time_needs_a_human(worktree):
    dispatched = task(worktree, kind="docs")
    dispatched["metadata"]["dispatch"]["dispatched_at"] = "soon"
    assert rules(ec.check(dispatched, [])) == [ec.NEEDS_HUMAN]


@pytest.mark.parametrize("malformed", [None, "text", {}, {"metadata": "x"}, {"metadata": {"dispatch": []}}])
def test_a_malformed_task_needs_a_human_and_never_raises(malformed):
    assert rules(ec.check(malformed, [])) == [ec.NEEDS_HUMAN]


def test_a_config_change_with_no_run_needs_a_human(worktree):
    dispatched = task(worktree, kind="config")
    (worktree / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = '-q'\n")
    verdict = ec.check(dispatched, [])
    assert state(verdict, "run_recorded") == "fail" and rules(verdict) == [ec.NEEDS_HUMAN]


# --- modes and the ledger ---

def test_below_gate_every_problem_becomes_a_note(worktree, monkeypatch):
    monkeypatch.setattr(ec, "use_mode", lambda use: "advisory")
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    verdict = ec.check(dispatched, [])
    assert verdict["problems"] == [] and f"[rule: {ec.NO_RUN}]" in verdict["notes"][0]


def test_the_runs_are_read_from_the_ledger_by_default(worktree, tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setattr(ec, "ledger_path", lambda create_dir=False: ledger)
    dispatched = task(worktree)
    for run in red_then_green(worktree):
        fields = {key: value for key, value in run.items() if key != "count_parsed"}
        assert ledger_append(fields, ledger)
    assert ec.check(dispatched)["problems"] == []


# --- the tree covers every input file a run can depend on, not only code ---

def test_a_config_edit_changes_the_tree(worktree):
    (worktree / "package.json").write_text('{"scripts":{"test":"real-test"}}\n')
    commit(worktree)
    before = ct.content_tree(str(worktree))
    (worktree / "package.json").write_text('{"scripts":{"test":"different-test"}}\n')
    after = ct.content_tree(str(worktree))
    assert before != after and ct.code_diff(str(worktree), before, after) == ["package.json"]


@pytest.mark.parametrize("name, counts", [
    ("pyproject.toml", True), ("Dockerfile", True), ("conf/policy.yaml", True), ("web/style.css", True),
    ("my config.json", True), (".env", False), (".env.local", False), ("docs/notes.md", False),
    ("node_modules/x/index.js", False), (".pytest_cache/v/cache/lastfailed", False),
    ("build/out.js", False), ("src/__pycache__/app.cpython-39.pyc", False), (".repacted/state.json", False),
])
def test_which_files_count_toward_freshness(worktree, name, counts):
    before = ct.content_tree(str(worktree))
    path = worktree / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n")
    after = ct.content_tree(str(worktree))
    assert (ct.code_diff(str(worktree), before, after) == [name]) is counts
    assert (before != after) is counts


def test_a_deleted_config_file_changes_the_tree(worktree):
    (worktree / "setup.cfg").write_text("[tool:pytest]\n")
    commit(worktree)
    before = ct.content_tree(str(worktree))
    (worktree / "setup.cfg").unlink()
    assert ct.code_diff(str(worktree), before, ct.content_tree(str(worktree))) == ["setup.cfg"]


def test_the_content_tree_never_writes_a_dotenv_blob_or_the_real_index(worktree):
    (worktree / ".env").write_text("placeholder dotenv body\n")
    index = git(worktree, "rev-parse", "--git-path", "index").strip()
    before = (worktree / index).read_bytes() if not index.startswith("/") else open(index, "rb").read()
    assert ct.content_tree(str(worktree))
    after = (worktree / index).read_bytes() if not index.startswith("/") else open(index, "rb").read()
    assert before == after
    blob = git(worktree, "hash-object", ".env").strip()
    probe = subprocess.run(["git", "-C", str(worktree), "cat-file", "-e", blob], capture_output=True)
    assert probe.returncode != 0


# --- uncommitted inputs, what a run loaded, red_first cover, excluded paths ---

@pytest.fixture
def real_commit_check(monkeypatch):
    """The uncommitted-input rows judge the real uncommitted-input check the module fixture stubs out."""
    monkeypatch.setattr(ec, "uncommitted_inputs", REAL_UNCOMMITTED)


@pytest.mark.parametrize("how", ["unstaged", "staged", "untracked"])
def test_r1_f1_an_uncommitted_input_file_is_refused_until_committed(worktree, real_commit_check, how):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    if how == "staged":
        git(worktree, "add", "src/app.py")
    if how == "untracked":
        commit(worktree)
        (worktree / "src" / "helper.py").write_text("x = 1\n")
    run = row(worktree, 2, "pytest -q")
    verdict = ec.check(dispatched, [run])
    assert rules(verdict) == [ec.UNCOMMITTED] and state(verdict, "inputs_committed") == "fail"
    assert "commit your work, then rerun tcount" in verdict["problems"][0]["line"]
    commit(worktree)
    assert ec.check(dispatched, [run])["problems"] == []


def test_r1_f1_an_uncommitted_doc_or_secret_file_is_not_refused(worktree, real_commit_check):
    dispatched = task(worktree, kind="refactor")
    edit(worktree)
    commit(worktree)
    (worktree / "notes.md").write_text("# notes\n")
    (worktree / ".env").write_text("A=1\n")
    verdict = ec.check(dispatched, [row(worktree, 2, "pytest -q")])
    assert verdict["problems"] == [] and state(verdict, "inputs_committed") == "pass"


@pytest.mark.parametrize("cmd", [
    "pytest -q {other}/tests/test_app.py", "pytest -q ../other/tests/test_app.py",
    "pytest -q --rootdir={other} tests/test_app.py", "pytest -q -c {other}/pytest.ini tests/test_app.py",
    "pytest -q --confcutdir {other} tests/test_app.py",
    "uv run --directory {other} pytest -q tests/test_app.py",
    # a shell runs what its -c string says; an ini override of a loaded path
    "sh -c 'cd {other} && pytest -q tests/test_app.py'", "bash -lc 'pytest -q tests/test_app.py'",
    "env -S 'pytest -q tests/test_app.py'",
    # an interpreter runs inline code, which is not a test runner the words show
    "python3 -c 'import os, pytest; os.chdir(\"{other}\"); pytest.main([\"tests/test_app.py\"])'",
    "node -e 'require(\"child_process\")' --test tests/test_app.py",
    "pytest -q -o pythonpath={other}/src tests/test_app.py",
    "pytest -q --override-ini=rootdir={other} tests/test_app.py", "pytest -q -otestpaths={other} tests/test_app.py",
])
def test_r1_f2a_a_run_that_loads_from_outside_its_git_root_backs_nothing(worktree, tmp_path, cmd):
    pin = {"AC-1": "pytest -q tests/test_app.py"}
    dispatched = task(worktree, kind="refactor", acceptance_pin=pin)
    edit(worktree)
    run = row(worktree, 2, cmd.format(other=tmp_path / "other"))
    verdict = ec.check(dispatched, [run])
    assert ec.NO_RUN in rules(verdict) and state(verdict, "checks_passed") == "fail"
    assert any("does not count" in note for note in verdict["notes"])


def test_r1_f2a_an_absolute_target_inside_the_worktree_still_counts(worktree):
    dispatched = task(worktree, kind="refactor", acceptance_pin={"AC-1": "pytest -q tests/test_app.py"})
    edit(worktree)
    run = row(worktree, 2, f"pytest -q {worktree}/tests/test_app.py")
    assert ec.check(dispatched, [run])["problems"] == []


@pytest.mark.parametrize("env, counts", [
    ({"PYTEST_ADDOPTS": "--deselect tests/test_app.py::test_fix"}, False),
    ({"NODE_OPTIONS": "--require /x/preload.js"}, False),
    ({"PYTHONPATH": "/elsewhere/src"}, False),
    ({"PYTHONPATH": "{wt}/src:src"}, True),
])
def test_r1_f2c_a_run_under_a_selecting_environment_does_not_count(worktree, env, counts):
    dispatched = task(worktree, kind="refactor", acceptance_pin={"AC-1": "pytest -q tests/test_app.py"})
    edit(worktree)
    env = {key: value.format(wt=worktree) for key, value in env.items()}
    verdict = ec.check(dispatched, [dict(row(worktree, 2, "pytest -q tests/test_app.py"), env=env)])
    assert (verdict["problems"] == []) is counts
    if counts:
        assert any("PYTHONPATH" in note for note in verdict["notes"])


def test_r1_f2c_a_joined_hook_row_carries_tcount_s_environment(tmp_path):
    from shared.ledger import read_runs
    ledger = tmp_path / "ledger.jsonl"
    log = str(tmp_path / "runs" / "2026-10-19T101010Z-77-pytest.log")
    base = {"cmd": "pytest -q", "cwd": str(tmp_path), "log_path": log, "passed": 1, "failed": 0,
            "errors": 0, "exit_code": 0}
    assert ledger_append({**base, "source": "tcount", "env": {"PYTEST_ADDOPTS": "-k x"}}, ledger)
    assert ledger_append({**base, "source": "hook"}, ledger)
    runs = read_runs(ledger)[0]
    assert len(runs) == 1 and runs[0]["run_env"] == {"PYTEST_ADDOPTS": "-k x"}


def test_r1_f2c_tcount_records_the_selecting_environment(tmp_path, monkeypatch):
    from importlib.machinery import SourceFileLoader
    from project_tasks_support import PLUGIN_ROOT
    tcount = SourceFileLoader("tcount_env_under_test", str(PLUGIN_ROOT / "bin" / "tcount")).load_module()
    rows = []
    monkeypatch.setattr(tcount, "ledger_append", rows.append)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k smoke")
    monkeypatch.delenv("NODE_OPTIONS", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    tcount.record_run(["pytest", "-q"], "pytest", None, 0, tmp_path / "x.log")
    assert rows[0]["env"] == {"PYTEST_ADDOPTS": "-k smoke"}


@pytest.mark.parametrize("green, where", [
    ("pytest -q -k test_other", ""), ("pytest -q tests -k test_other", ""),
    ("pytest -q tests tests/test_app.py::test_x", ""), ("pytest -q", "src"),
    # naming the red file but selecting a sibling test inside it
    ("pytest -q tests/test_new.py::test_helper", ""), ("pytest -q tests/test_new.py -k test_helper", ""),
    ("pytest -q -k x tests/test_new.py", ""),
])
def test_r1_f3_a_narrowed_or_elsewhere_green_does_not_cover_the_red_file(worktree, green, where):
    dispatched = task(worktree, kind="feature")
    (worktree / "tests" / "test_new.py").write_text("def test_new():\n    assert False\n")
    red = row(worktree, 9, "pytest -q tests/test_new.py", passed=0, failed=1)
    edit(worktree)
    run = row(worktree / where if where else worktree, 5, green)
    assert rules(ec.check(dispatched, [red, run])) == [ec.RED_FIRST]


@pytest.mark.parametrize("red_target, green", [
    ("tests/test_new.py", "pytest -q"), ("tests/test_new.py", "pytest -q tests"),
    ("tests/test_new.py", "pytest -q ./tests/test_new.py"),
    ("tests/test_new.py::test_new", "pytest -q tests/test_new.py::test_new"),
    ("tests/test_new.py::test_new", "pytest -q tests/test_new.py"),
])
def test_r1_f3_a_whole_suite_or_the_named_file_still_covers_it(worktree, red_target, green):
    dispatched = task(worktree, kind="feature")
    (worktree / "tests" / "test_new.py").write_text("def test_new():\n    assert False\n")
    red = row(worktree, 9, f"pytest -q {red_target}", passed=0, failed=1)
    edit(worktree)
    assert ec.check(dispatched, [red, row(worktree, 5, green)])["problems"] == []


def test_r1_f4_staged_unstaged_and_committed_excluded_files_keep_the_tree_id(worktree):
    (worktree / "NOTES.md").write_text("a\n")
    commit(worktree)
    before = ct.content_tree(str(worktree))
    (worktree / "NOTES.md").write_text("b\n")
    assert ct.content_tree(str(worktree)) == before
    git(worktree, "add", "NOTES.md")
    assert ct.content_tree(str(worktree)) == before
    commit(worktree)
    assert ct.content_tree(str(worktree)) == before


def test_r1_f4_a_stale_line_with_no_input_change_does_not_blame_ignored_paths(worktree):
    first = git(worktree, "write-tree").strip()
    (worktree / "NOTES.md").write_text("a\n")
    git(worktree, "add", "NOTES.md")
    line = ec._changed_since(str(worktree), first, git(worktree, "write-tree").strip())
    assert "ignored paths" not in line and "rerun tcount" in line


# --- a red and a green ended in the same second (one-second ledger stamps) ---

BASE = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0) - datetime.timedelta(minutes=5)


def second(offset):
    """An ISO stamp `offset` seconds after one whole second fixed at import (five minutes before)."""
    return (BASE + datetime.timedelta(seconds=offset)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def hooked(run, ts):
    """The run-ledger hook's row for a tcount run: appended after the whole Bash call ends."""
    return {"source": "hook", "log_path": run["log_path"], "cmd": "tcount " + run["cmd"], "ts": ts,
            "cwd": run["cwd"], **{key: run[key] for key in ("collected", "passed", "failed", "errors",
                                                             "skipped", "exit_code", "count_parsed")}}


def judged(worktree, tmp_path, monkeypatch, lines, dispatched=None):
    """ec.check over a ledger file holding `lines` in this order (the reader's own path)."""
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("".join(json.dumps(line) + "\n" for line in lines))
    monkeypatch.setattr(ec, "ledger_path", lambda create_dir=False: ledger)
    return ec.check(dispatched or task(worktree))


def tied(worktree, red_at=0, green_at=0):
    """A named red of the old inputs and a green of the fixed ones, stamped red_at and green_at."""
    red = dict(row(worktree, 0, f"pytest -q {TEST_ID}", passed=0, failed=1), ts=second(red_at))
    edit(worktree)
    return red, dict(row(worktree, 0, "pytest -q tests/test_app.py"), ts=second(green_at))


@pytest.mark.parametrize("with_hooks", [False, True])
def test_n6_a_red_then_a_green_in_the_same_second_counts_in_ledger_order(worktree, tmp_path, monkeypatch,
                                                                          with_hooks):
    red, green = tied(worktree)
    # the hook rows land after the Bash call, here in the other order: tcount's own rows decide
    lines = [red, green] + ([hooked(green, second(1)), hooked(red, second(1))] if with_hooks else [])
    verdict = judged(worktree, tmp_path, monkeypatch, lines)
    assert verdict["problems"] == [] and state(verdict, "red_first") == "pass"


@pytest.mark.parametrize("with_hooks", [False, True])
def test_n6_a_green_then_a_red_in_the_same_second_does_not_count(worktree, tmp_path, monkeypatch, with_hooks):
    red, green = tied(worktree)
    red["run_seq"] = -1         # a position written into a row is never read: the reader sets it
    lines = [green, red] + ([hooked(red, second(1)), hooked(green, second(1))] if with_hooks else [])
    assert rules(judged(worktree, tmp_path, monkeypatch, lines)) == [ec.RED_FIRST]


@pytest.mark.parametrize("red_line_first", [False, True])
def test_n6_a_red_stamped_a_later_second_than_the_green_does_not_count(worktree, tmp_path, monkeypatch,
                                                                       red_line_first):
    red, green = tied(worktree, red_at=1, green_at=0)
    lines = [red, green] if red_line_first else [green, red]
    assert rules(judged(worktree, tmp_path, monkeypatch, lines)) == [ec.RED_FIRST]


def test_n6_a_tie_with_a_red_from_before_dispatch_does_not_count(worktree, tmp_path, monkeypatch):
    red, green = tied(worktree)
    dispatched = task(worktree, minutes_ago=0)
    dispatched["metadata"]["dispatch"]["dispatched_at"] = second(1)
    # the green's own end ties the red's, but only its hook row is stamped after dispatch
    lines = [red, green, hooked(green, second(2))]
    assert rules(judged(worktree, tmp_path, monkeypatch, lines, dispatched)) == [ec.RED_FIRST]


@pytest.mark.parametrize("ended", [None, "", "not a time", 1792394853, "2026-13-45T99:99:99Z"])
def test_n6_a_red_whose_tcount_stamp_is_missing_or_malformed_uses_its_later_hook_stamp(
        worktree, tmp_path, monkeypatch, ended):
    red, green = tied(worktree)
    red_hook = hooked(red, second(1))
    red["ts"] = ended
    lines = [red, green, hooked(green, second(1)), red_hook]
    assert rules(judged(worktree, tmp_path, monkeypatch, lines)) == [ec.RED_FIRST]


# --- the same-second tie-break changes only a same-second red appended before the green ---

@pytest.mark.parametrize("with_hooks", [False, True])
@pytest.mark.parametrize("red_at, red_line_first, counts", [
    (-1, True, True), (-1, False, True),        # an earlier second counts, whatever the line order
    (0, True, True), (0, False, False),         # the same second: tcount's line order decides
    (1, True, False), (1, False, False),        # a later second never counts
])
def test_rev4_the_tie_break_keeps_the_old_boundary_outside_one_second(worktree, tmp_path, monkeypatch,
                                                                      red_at, red_line_first, counts,
                                                                      with_hooks):
    red, green = tied(worktree, red_at=red_at)
    lines = [red, green] if red_line_first else [green, red]
    lines += [hooked(green, second(2)), hooked(red, second(2))] if with_hooks else []
    verdict = judged(worktree, tmp_path, monkeypatch, lines)
    assert rules(verdict) == ([] if counts else [ec.RED_FIRST])


def test_rev4_a_run_seq_planted_on_the_hook_rows_is_ignored(worktree, tmp_path, monkeypatch):
    red, green = tied(worktree)
    lines = [green, red, dict(hooked(red, second(1)), run_seq=-1), dict(hooked(green, second(1)), run_seq=10**9)]
    assert rules(judged(worktree, tmp_path, monkeypatch, lines)) == [ec.RED_FIRST]


def test_rev4_a_tied_red_from_another_worktree_with_other_inputs_does_not_count(repo, worktree, tmp_path,
                                                                                monkeypatch):
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-q", "-b", "other", str(other))
    edit(other, "def f():\n    return 3\n")
    red = dict(row(other, 0, f"pytest -q {TEST_ID}", passed=0, failed=1), ts=second(0))
    edit(worktree)
    green = dict(row(worktree, 0, "pytest -q tests/test_app.py"), ts=second(0))
    assert rules(judged(worktree, tmp_path, monkeypatch, [red, green])) == [ec.RED_FIRST]


def test_rev4_a_tied_hook_row_with_no_tcount_row_is_no_red(worktree, tmp_path, monkeypatch):
    red, green = tied(worktree)
    bare = {key: value for key, value in hooked(red, second(0)).items() if key != "log_path"}
    assert rules(judged(worktree, tmp_path, monkeypatch, [bare, green])) == [ec.RED_FIRST]


@pytest.mark.parametrize("red_first", [True, False])
def test_rev4_runs_given_without_run_seq_are_ordered_by_list_position(worktree, red_first):
    red, green = tied(worktree)
    verdict = ec.check(task(worktree), [red, green] if red_first else [green, red])
    assert rules(verdict) == ([] if red_first else [ec.RED_FIRST])


def test_rev4_a_caller_passing_read_runs_gets_the_same_verdict(worktree, tmp_path, monkeypatch):
    """receipt._statuses passes read_runs output to check()."""
    from shared.ledger import read_runs
    red, green = tied(worktree)
    verdict = judged(worktree, tmp_path, monkeypatch, [red, green, hooked(green, second(1)), hooked(red, second(1))])
    assert verdict["problems"] == []
    assert ec.check(task(worktree), read_runs(tmp_path / "ledger.jsonl")[0])["problems"] == []
