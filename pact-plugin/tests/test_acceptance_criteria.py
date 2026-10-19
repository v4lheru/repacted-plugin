"""
Executable acceptance criteria.

  1. AC ids (`AC-1`..`AC-9999`, ASCII only) are refs of their own: a coder's work task names the ones
     it owns in metadata.refs, the HANDOFF disposes of each in handoff.refs, and `done` must cite a
     recorded run (a tcount log stem). They never reach the task-log bridge (T- refs only).
  2. `repact accept` refuses a HANDOFF that leaves a pinned AC undisposed or closes one with a run
     that does not pass the pinned command (shared/ops/accept.py ac_problems, tests/test_ops_flow.py);
     the judging itself (acceptance.judge, same test selection) is tested here.
  3. `repact inspect --acceptance <acceptance.md>` prints each AC with its latest run: the HANDOFF's cited
     run binds it; the command-token match is display only; no match reads `none` with the reason.
Everything runs in temp dirs through the World of the bridge tests.
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from project_tasks_support import PLUGIN_ROOT, load
from test_project_tasks_bridge import TASK_B, TEAM, World

RULES = PLUGIN_ROOT / "hooks" / "shared" / "ref_dispositions.py"
ACCEPT = PLUGIN_ROOT / "bin" / "repact"
GREEN = "2026-10-18T120000Z-4812"
RED = "2026-10-18T110000Z-4811"
HANDOFF = {"rev": 1, "amendments": {}, "produced": ["x"], "decisions": [], "uncertainty": [],
           "integration": [], "open_questions": []}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


@pytest.fixture()
def rules():
    return load("ref_dispositions_ac_under_test", RULES)


def plant(world, stem, passed, failed, cmd="tcount pytest -q tests/test_a.py", ts="2026-10-18T12:00:00+00:00",
          cwd=None):
    """One hook row for a tcount run with log `<stem>-pytest.log`, run in the world's repo."""
    runs = world.data / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    row = {"v": 1, "ts": ts, "team": TEAM, "task_id": TASK_B, "cwd": cwd or str(world.repo), "cmd": cmd,
           "runner": "pytest", "count_parsed": True, "log_path": str(runs / f"{stem}-pytest.log"),
           "collected": passed + failed, "passed": passed, "failed": failed, "errors": 0, "skipped": 0,
           "exit_code": 1 if failed else 0, "source": "hook"}
    with open(runs / "ledger.jsonl", "a") as handle:
        handle.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------------------------
# 1. the AC grammar
# ---------------------------------------------------------------------------------------------

class TestGrammar:
    def test_an_ac_id_is_ascii_ac_dash_one_to_four_digits_without_a_leading_zero(self, rules):
        assert all(rules.is_ac(ok) for ok in ("AC-1", "AC-12", "AC-9999"))
        odd = ["AC-0", "AC-01", "AC-10000", "AC-" + "9" * 5000, "ac-1", "AC1", "AC-1 ", "AC-1\n", "",
               "ＡＣ-1", "AC-１", "АС-1", "AC-٣", "AC-1​", None, 3, ["AC-1"]]
        assert not any(rules.is_ac(bad) for bad in odd)

    def test_acs_are_carried_by_metadata_refs_and_never_reach_the_t_ref_readers(self, rules):
        metadata = {"refs": ["AC-2", "T-0123abcd", "AC-2", "AC-0", None, "AC-3"]}
        assert rules.metadata_acs(metadata) == ["AC-2", "AC-3"]
        assert rules.metadata_refs(metadata) == ["T-0123abcd"]
        handoff = {"refs": {"AC-2": f"done: {GREEN}", "T-0123abcd": "done"}}
        assert rules.dispositions(handoff) == {"T-0123abcd": "done"}   # the bridge never sees an AC
        assert rules.ac_dispositions(handoff) == {"AC-2": f"done: {GREEN}"}
        assert rules.malformed(handoff) == []

    def test_done_needs_a_run_id_and_the_other_verbs_need_a_reason(self, rules):
        handoff = {"refs": {"AC-1": "done", "AC-2": "done: tests pass", "AC-3": f"done: run {GREEN}",
                            "AC-4": f"done: {RED}-pytest.log then {GREEN}", "AC-5": "partial",
                            "AC-6": "deferred: needs the owner's key", "AC-7": 7}}
        problems = rules.ac_problems(handoff)
        assert sorted(problems) == ["AC-1", "AC-2", "AC-5", "AC-7"]
        assert "run id" in problems["AC-1"] and "run id" in problems["AC-2"]
        assert rules.ac_run_ids(handoff["refs"]["AC-4"]) == [RED, GREEN]
        assert rules.ac_run_ids("done: log 4812") == []          # a bare pid names no run

    def test_a_huge_value_is_read_quickly_and_names_no_run(self, rules):
        assert rules.ac_run_ids("done: " + "1" * 200_000) == []
        assert "AC-1" in rules.ac_problems({"refs": {"AC-1": "done: " + "x" * 200_000}})


# ---------------------------------------------------------------------------------------------
# 2. the gate
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# 3. repact inspect --acceptance
# ---------------------------------------------------------------------------------------------

def accept(world, *args):
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": str(world.data), "CLAUDE_CONFIG_DIR": str(world.home)}
    verb, path, *rest = args                     # check|pin <file> [...] -> repact inspect --acceptance
    argv = ["inspect", "--acceptance", path, *rest] + (["--pin"] if verb == "pin" else [])
    return subprocess.run(["bash", str(ACCEPT), *argv], capture_output=True, text=True,
                          env=env, cwd=str(world.repo), timeout=60)


def criteria(world, text):
    path = world.repo / "docs" / "feat" / "acceptance.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestCheck:
    TEXT = ("# Acceptance: feat\n\n"
            "- AC-1: the parser rejects a bad id. `pytest -q tests/test_a.py`\n"
            "- AC-2: the writer keeps order. `pytest -q tests/test_b.py::test_order`\n"
            "- AC-3: the docs say so. `repact-docs-gen --check`\n"
            "- AC-4: nothing ran yet. `pytest -q tests/test_c.py`\n")

    def lines(self, result):
        return {line.split()[0]: line for line in result.stdout.splitlines() if line.startswith("AC-")}

    def test_each_ac_reads_green_red_or_none_by_its_command(self, world):
        plant(world, RED, 3, 2, ts="2026-10-18T11:00:00+00:00")
        plant(world, GREEN, 5, 0)
        plant(world, "2026-10-18T120500Z-4813", 1, 1, cmd="nice -n 19 tcount pytest -q tests/test_b.py::test_order")
        result = accept(world, "check", criteria(world, self.TEXT))
        rows = self.lines(result)
        assert result.returncode == 1, result.stdout + result.stderr
        assert " green " in rows["AC-1"] and GREEN in rows["AC-1"] and "red-first: yes" in rows["AC-1"]
        assert " red " in rows["AC-2"]
        assert " none " in rows["AC-3"] and "repact-docs-gen --check" in rows["AC-3"]
        assert " none " in rows["AC-4"] and "tests/test_c.py" in rows["AC-4"]

    def test_the_handoffs_cited_run_binds_the_ac_over_the_command_match(self, world):
        plant(world, RED, 3, 2, ts="2026-10-18T11:00:00+00:00")
        plant(world, GREEN, 5, 0)
        world.task(status="in_progress", refs=["AC-1"],
                   handoff={**HANDOFF, "refs": {"AC-1": f"done: {RED}"}})
        result = accept(world, "check", criteria(world, self.TEXT), "--team", TEAM, "--task", TASK_B)
        row = self.lines(result)["AC-1"]
        assert " red " in row and RED in row and f"#{TASK_B}" in row

    def test_a_cited_run_the_ledger_lacks_reads_missing(self, world):
        plant(world, GREEN, 5, 0)
        world.task(status="in_progress", handoff={**HANDOFF, "refs": {"AC-1": "done: 2026-10-18T130000Z-9999"}})
        result = accept(world, "check", criteria(world, self.TEXT), "--team", TEAM, "--task", TASK_B)
        assert " missing " in self.lines(result)["AC-1"]

    def test_all_green_exits_zero(self, world):
        plant(world, GREEN, 5, 0)
        path = criteria(world, "AC-1: one. `pytest -q tests/test_a.py`\n")
        result = accept(world, "check", path)
        assert result.returncode == 0 and " green " in self.lines(result)["AC-1"]

    def test_a_missing_file_or_a_file_without_criteria_is_a_usage_error(self, world):
        assert accept(world, "check", str(world.repo / "nope.md")).returncode == 2
        assert accept(world, "check", criteria(world, "# nothing here\nAC-0: zero. `x`\n")).returncode == 2

    def test_a_duplicate_ac_id_is_flagged(self, world):
        path = criteria(world, "AC-1: one. `pytest tests/test_a.py`\nAC-1: again. `pytest tests/test_b.py`\n")
        result = accept(world, "check", path)
        assert result.returncode == 1 and "duplicate" in result.stdout


# ---------------------------------------------------------------------------------------------
# 4. a done AC is bound to its criterion, refs cannot be shed, and an
#    AC-like ref that is not an AC id is named
# ---------------------------------------------------------------------------------------------

CLAIM = "2026-10-18T11:30:00+00:00"
BOUND_TEXT = ("- AC-1: the parser rejects a bad id. `pytest -q tests/test_a.py`\n"
              "- AC-2: the docs say so. `repact-docs-gen --check`\n")


def claimed(world, refs, acceptance=None, claim=CLAIM, **metadata):
    """A work task carrying `refs`, blocked by a task last written at `claim` (its claim time)."""
    import datetime
    if acceptance is not None:
        metadata["acceptance"] = acceptance
    body = world.task(status="in_progress", refs=refs, **metadata)
    if claim:
        teach = world.home / "tasks" / TEAM / "6.json"
        teach.write_text(json.dumps({"id": "6", "subject": "teachback", "status": "completed", "blockedBy": [],
                                     "blocks": [TASK_B], "metadata": {}}))
        stamp = datetime.datetime.fromisoformat(claim).timestamp()
        os.utime(teach, (stamp, stamp))
        body["blockedBy"] = ["6"]
        (world.home / "tasks" / TEAM / f"{TASK_B}.json").write_text(json.dumps(body))
    return body


class TestOptionValues:
    """A target named only as the value of --deselect, --ignore, --ignore-glob, -k or
    --exclude is not run, in the `--opt value` and the `--opt=value` form; and an option the AC's
    command carries must be in the run too (exact selection)."""

    @pytest.mark.parametrize("trick", ["--deselect tests/test_a.py", "--deselect=tests/test_a.py",
                                       "--ignore tests/test_a.py", "--ignore-glob=tests/test_a.py",
                                       "-k tests/test_a.py", "--exclude tests/test_a.py"])
    def test_a_target_named_only_as_an_option_value_does_not_close_the_ac(self, trick, tmp_path):
        why = TestSelection.judged("pytest -q tests/test_a.py", f"tcount pytest -q tests/test_trivial.py {trick}",
                                   tmp_path)
        assert "does not run the AC command `pytest -q tests/test_a.py`" in why, why

    @pytest.mark.parametrize("ran, closes", [("tests/test_a.py --ignore=tests/test_slow.py", True),
                                             ("tests/test_a.py", False)])
    def test_an_option_in_the_ac_command_must_be_in_the_run(self, ran, closes, tmp_path):
        why = TestSelection.judged("pytest -q tests/test_a.py --ignore tests/test_slow.py",
                                   f"tcount pytest -q {ran}", tmp_path)
        assert (why == "") is closes, why


PIN = {"AC-1": "pytest -q tests/test_a.py", "AC-2": "repact-docs-gen --check"}


class TestPin:
    """The lead pins each AC's command in metadata.acceptance_pin at dispatch; a
    coder who rewrites the acceptance file in its own checkout cannot move the binding."""

    def test_repact_accept_pin_prints_the_map_and_check_shows_a_mismatch(self, world):
        path = criteria(world, BOUND_TEXT)
        result = accept(world, "pin", path)
        assert result.returncode == 0 and json.loads(result.stdout) == PIN, result.stdout + result.stderr
        plant(world, GREEN, 5, 0, cmd="tcount pytest -q tests/test_trivial.py")
        criteria(world, BOUND_TEXT.replace("tests/test_a.py", "tests/test_trivial.py"))
        claimed(world, ["AC-1"], acceptance="docs/feat/acceptance.md", acceptance_pin=PIN,
                handoff={**HANDOFF, "refs": {"AC-1": f"done: {GREEN}"}})
        result = accept(world, "check", path, "--team", TEAM, "--task", TASK_B)
        row = [line for line in result.stdout.splitlines() if line.startswith("AC-1")][0]
        assert result.returncode == 1 and " red " in row and "the lead's pin says" in row, result.stdout


class TestSelection:
    """A run closes an AC only when it selects the same tests as the AC's command: the
    same runner, targets and selection options (-k, -m, --deselect, -c ...), compared exactly once
    the wrappers (tcount, nice, uv run, python -m) and the output-only options are set aside."""

    @staticmethod
    def judged(pinned, ran, tmp_path):
        from shared import acceptance as AC
        root = os.path.realpath(tmp_path)
        record = {"v": 1, "ts": "2026-10-18T12:00:00+00:00", "cwd": root, "run_cwd": root, "cmd": ran,
                  "run_cmd": ran, "runner": "pytest", "count_parsed": True,
                  "log_path": os.path.join(root, f"{GREEN}-pytest.log"), "collected": 1, "passed": 1,
                  "failed": 0, "errors": 0, "skipped": 0, "exit_code": 0, "source": "hook"}
        return AC.judge([GREEN], [record], pinned, root, 0.0)[1]

    def test_a_run_with_another_k_expression_does_not_close_the_ac(self, tmp_path):
        why = self.judged("pytest -q tests/test_auth.py -k expired_token",
                          "pytest -q tests/test_auth.py -k smoke", tmp_path)
        assert "does not run" in why and "expired_token" in why

    @pytest.mark.parametrize("pinned, ran", [
        ("pytest tests/test_a.py -k 'expired and token'", "pytest tests/test_a.py -k expired"),
        ("pytest tests/test_a.py -k 'a or b'", "pytest tests/test_a.py -k 'b or a'"),
        ("pytest tests/test_a.py::test_x", "pytest tests/test_a.py"),
        ("pytest tests/test_a.py", "pytest tests/test_a.py::test_x"),
        ("pytest tests/test_a.py", "pytest tests/test_a.py --deselect tests/test_a.py::test_slow"),
        ("pytest tests/test_a.py", "pytest tests/test_a.py tests/test_b.py"),
        ("pytest -m slow tests/", "pytest -m 'not slow' tests/"),
        ("pytest -c ci.ini tests/", "pytest tests/"),
        ("pytest --rootdir=sub tests/", "pytest tests/"),
        ("pytest tests/test_a.py", "jest tests/test_a.py"),
        ("pytest tests/test_a.py -k x", "pytest tests/test_a.py -k 'x"),
    ])
    def test_another_selection_is_a_mismatch(self, pinned, ran, tmp_path):
        assert "does not run" in self.judged(pinned, ran, tmp_path)

    @pytest.mark.parametrize("ran", [
        "tcount pytest -q tests/test_auth.py -k 'expired and token'",
        "nice -n 19 tcount uv run python -m pytest -v tests/test_auth.py -k 'expired and token'",
        "uv run --python 3.9 pytest -x --tb=short -k 'expired and token' ./tests/test_auth.py",
        "tcount python3.9 -m pytest -k 'expired and token' {root}/tests/test_auth.py",
    ])
    def test_a_wrapper_or_an_output_option_still_matches(self, ran, tmp_path):
        ran = ran.format(root=os.path.realpath(tmp_path))
        assert self.judged('pytest -q tests/test_auth.py -k "expired and token"', ran, tmp_path) == ""

    @pytest.mark.parametrize("command", [
        "uv run --directory /other pytest tests/test_a.py", "uv run --directory=/other pytest tests/test_a.py",
        "uv run --project /other pytest tests/test_a.py", "uv run --project=/other pytest tests/test_a.py",
        "uv run --with-editable /other pytest tests/test_a.py", "uv run --with-requirements r.txt pytest tests/test_a.py",
        "uv run --env-file vars.txt pytest tests/test_a.py",
        "env PYTEST_ADDOPTS=-x pytest tests/test_a.py", "env -i pytest tests/test_a.py",
    ])
    def test_r1_f2b_a_wrapper_that_changes_what_runs_cannot_be_read(self, command):
        from shared import acceptance as AC
        assert AC.selection(command) is None

    def test_r1_f2b_uv_with_and_no_project_are_environment_not_selection(self, tmp_path):
        ran = "uv run --no-project --with pytest --with pyyaml python -m pytest -q tests/test_x.py"
        assert self.judged("python -m pytest -q tests/test_x.py", ran, tmp_path) == ""

    def test_r1_f2b_r_is_an_output_option_for_pytest_only_and_env_is_a_wrapper(self):
        from shared import acceptance as AC
        assert AC.selection("pytest -rfE tests/test_a.py") == AC.selection("pytest -r fE tests/test_a.py") \
            == AC.selection("env A=1 pytest tests/test_a.py") == ("pytest", [], ["tests/test_a.py"])
        assert AC.selection("node --test -r ./preload.js tests/a.test.js") == \
            ("node", [("--test", ""), ("-r", "./preload.js")], ["tests/a.test.js"])

    def test_tcount_records_the_command_with_its_quoting(self, tmp_path, monkeypatch):
        from importlib.machinery import SourceFileLoader
        tcount = SourceFileLoader("tcount_under_test", str(PLUGIN_ROOT / "bin" / "tcount")).load_module()
        rows = []
        monkeypatch.setattr(tcount, "ledger_append", rows.append)
        monkeypatch.chdir(tmp_path)
        tcount.record_run(["pytest", "-k", "a or b", "tests/x y.py"], "pytest", None, 0, tmp_path / "x.log")
        assert rows[0]["cmd"] == "pytest -k 'a or b' 'tests/x y.py'"

    def test_no_ledger_marks_the_ac_unverified(self, world):
        out = accept(world, "check", criteria(world, "AC-1: one. `pytest -q tests/test_a.py`\n"), "--ledger",
                     str(world.data / "nope.jsonl"))
        assert out.stdout.splitlines()[-1].split()[:2] == ["AC-1", "unverified"], out.stdout
