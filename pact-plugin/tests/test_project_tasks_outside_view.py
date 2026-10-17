"""
The task-system build-now items of the outside-view review (docs/reviews/
task-system-outside-view-2026-10-04.md, sections 3 to 5), each red before its fix:
  D1/P-C  two branches that both wrote the log merge without a conflict on the markdown views
          (merge=union); docs/release-gates.json is never left as invalid JSON (merge=binary keeps
          one valid side); `next` and `fsck` say when the views drifted from the log.
  D3/P-H  a run item recorded before the digest, whose log is not on this machine, is a note
          ("unverifiable here") in fsck, not a failure.
  D4      `drop` (and `set --status dropped`) refuses a parent with open children unless
          `--with-children`, which drops the open descendants in the same write.
  P-B     `by` carries the session's team: lead@<team>, bridge@<team>.
Everything runs in temp repos, a temp config root and a temp plugin data dir.
"""
from __future__ import annotations

import json

import pytest

from project_tasks_support import git, new_repo, run_cli, writer
from test_project_tasks_bridge import SID, TASK_B, TEAM, World, lead_accepts, run_hook

DRIFT = "views drifted from the log"
HANDOFF = {"rev": 1, "amendments": {}, "produced": ["x"], "decisions": [], "uncertainty": [],
           "integration": [], "open_questions": []}


def ok(result):
    assert result.returncode == 0, result.stderr
    return result


def ref_of(result) -> str:
    return result.stdout.split()[0]


# ---------------------------------------------------------------------------------------------
# D1 / P-C: the generated views across a merge of two writer branches
# ---------------------------------------------------------------------------------------------

@pytest.fixture()
def base(tmp_path):
    repo = new_repo(tmp_path / "repo")
    ok(run_cli(repo, "init"))
    ok(run_cli(repo, "render", "--configure"))
    release = ref_of(ok(run_cli(repo, "add", "Release v9 — ünïcode", "--priority", "P1",
                                "--tag", "release", "--tag", "release.v9")))
    ok(run_cli(repo, "commit"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base", check=False)
    git(repo, "branch", "-q", "exp-base")
    return repo, release


def branch_add(repo, branch, *add_args):
    git(repo, "checkout", "-q", "exp-base")
    git(repo, "checkout", "-q", "-b", branch)
    ok(run_cli(repo, "add", *add_args))
    ok(run_cli(repo, "commit"))


class TestViewsAcrossAMerge:
    def test_two_writer_branches_merge_without_a_conflict_and_the_drift_line_names_render(self, base):
        repo, _ = base
        assert "docs/backlog.md merge=union" in (repo / ".gitattributes").read_text()
        branch_add(repo, "exp-a", "Experiment A task", "--priority", "P3")
        branch_add(repo, "exp-b", "Experiment B task — 日本", "--priority", "P3")
        merged = git(repo, "merge", "-q", "--no-edit", "exp-a", check=False)
        assert merged.returncode == 0, merged.stdout + merged.stderr
        assert git(repo, "status", "--porcelain").stdout.strip() == ""
        for command in ("next", "fsck"):
            out = ok(run_cli(repo, command)).stdout
            assert DRIFT in out and "repact-task render" in out, (command, out)
        ok(run_cli(repo, "render"))
        assert DRIFT not in ok(run_cli(repo, "next")).stdout
        assert ok(run_cli(repo, "fsck")).stdout.strip() == "fsck: clean"

    def test_both_sides_changing_the_gates_never_leave_invalid_json(self, base):
        repo, release = base
        branch_add(repo, "exp-a", "Gate A", "--priority", "P1", "--parent", release, "--tag", "gate")
        branch_add(repo, "exp-b", "Gate B", "--priority", "P1", "--parent", release, "--tag", "gate")
        git(repo, "merge", "-q", "--no-edit", "exp-a", check=False)
        unmerged = git(repo, "diff", "--name-only", "--diff-filter=U").stdout.split()
        assert unmerged == ["docs/release-gates.json"]            # the markdown views unioned
        json.loads((repo / "docs" / "release-gates.json").read_text())   # one side, whole
        assert DRIFT in ok(run_cli(repo, "next")).stdout
        ok(run_cli(repo, "render"))
        gates = json.loads((repo / "docs" / "release-gates.json").read_text())["gates"]["v9"]
        assert sorted(entry["gate"] for entry in gates) == ["Gate A", "Gate B"]

    def test_an_empty_log_and_a_corrupt_line_do_not_break_the_drift_check(self, tmp_path):
        repo = new_repo(tmp_path / "repo")
        ok(run_cli(repo, "init"))
        assert DRIFT not in ok(run_cli(repo, "next")).stdout       # no views configured
        ok(run_cli(repo, "render", "--configure"))
        assert DRIFT not in ok(run_cli(repo, "next")).stdout
        with (repo / ".repacted" / "tasks.jsonl").open("a") as log:
            log.write('{"v": 1, "op": "create", broken\n')
        out = ok(run_cli(repo, "fsck")).stdout
        assert "corrupt" in out and DRIFT not in out


# ---------------------------------------------------------------------------------------------
# D3 / P-H: a digest-less run on a machine without its log
# ---------------------------------------------------------------------------------------------

@pytest.fixture()
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def cli(world, *args):
    return run_cli(world.repo, *args, env={"CLAUDE_PLUGIN_DATA": str(world.data)})


def strip_digests(world):
    log = world.repo / ".repacted" / "tasks.jsonl"
    lines = []
    for line in log.read_text().splitlines():
        event = json.loads(line)
        for item in event.get("evidence", []):
            item.pop("sha256", None)
            item.pop("counts", None)
        lines.append(json.dumps(event))
    log.write_text("\n".join(lines) + "\n")


class TestRunsWithoutADigest:
    def test_a_digest_less_run_whose_log_is_elsewhere_is_unverifiable_not_a_failure(self, world):
        name = world.plant_run(passed=12)
        assert cli(world, "done", world.ref, "--evidence", f"run:{name}").returncode == 0
        strip_digests(world)
        assert cli(world, "fsck").stdout.strip() == "fsck: clean"     # the log is here: it verifies
        (world.data / "runs" / name).unlink()                           # a second machine
        out = cli(world, "fsck").stdout
        assert "no longer verifies" not in out
        assert out.splitlines()[0] == "fsck: clean"
        assert f"unverifiable here" in out and world.ref in out

    def test_a_digest_less_run_whose_log_is_here_and_fails_still_fails(self, world):
        name = world.plant_run(passed=12)
        cli(world, "done", world.ref, "--evidence", f"run:{name}")
        strip_digests(world)
        (world.data / "runs" / name).write_text("============ 3 passed, 9 failed in 0.50s ====\n")
        assert "no longer verifies" in cli(world, "fsck").stdout


# ---------------------------------------------------------------------------------------------
# D4: drop and open children
# ---------------------------------------------------------------------------------------------

class TestDropAParent:
    def family(self, world):
        parent = world.ref
        done_child = world.store.add("Done child", parent=parent)
        open_child = world.store.add("Open child — ü", parent=parent)
        grandchild = world.store.add("Grandchild", parent=open_child)
        world.store.done(done_child, ["commit:" + world.head])
        return parent, done_child, open_child, grandchild

    def test_drop_refuses_a_parent_with_open_children_and_names_them(self, world):
        parent, _, open_child, _ = self.family(world)
        for args in (("drop", parent), ("set", parent, "--status", "dropped")):
            result = cli(world, *args)
            assert result.returncode == 65 and open_child in result.stderr and "--with-children" in result.stderr
        assert world.state().tasks[parent]["status"] != "dropped"

    def test_with_children_drops_the_open_descendants_and_keeps_the_done_one(self, world):
        parent, done_child, open_child, grandchild = self.family(world)
        ok(cli(world, "drop", parent, "--with-children"))
        tasks = world.state().tasks
        assert [tasks[r]["status"] for r in (parent, open_child, grandchild)] == ["dropped"] * 3
        assert tasks[done_child]["status"] == "done"
        assert "open child of settled parent" not in cli(world, "fsck").stdout

    def test_a_parent_without_open_children_drops_as_before(self, world):
        ok(cli(world, "drop", world.ref))
        assert world.state().tasks[world.ref]["status"] == "dropped"


# ---------------------------------------------------------------------------------------------
# P-B: the session's team in `by`
# ---------------------------------------------------------------------------------------------

def last_by(world) -> str:
    return json.loads(world.log_lines()[-1])["by"]


class TestByCarriesTheTeam:
    def test_the_lead_writes_as_lead_at_its_team(self, world):
        ok(run_cli(world.repo, "set", world.ref, "--note", "x",
                   env={"CLAUDE_PLUGIN_DATA": str(world.data), "CLAUDE_CODE_SESSION_ID": SID}))
        assert last_by(world) == f"lead@{TEAM}"

    def test_no_session_or_an_unknown_one_writes_plain_lead(self, world):
        for sid in ("", "5e551011-0000-4000-8000-0000000000ff"):
            ok(run_cli(world.repo, "set", world.ref, "--note", f"n{sid[-2:]}",
                       env={"CLAUDE_PLUGIN_DATA": str(world.data), "CLAUDE_CODE_SESSION_ID": sid}))
            assert last_by(world) == "lead"

    @pytest.mark.parametrize("sid", ["*", "../" + SID, SID + "/..", "?" * 36])
    def test_a_hostile_session_id_is_never_globbed(self, world, sid):
        ok(run_cli(world.repo, "set", world.ref, "--note", "x",
                   env={"CLAUDE_PLUGIN_DATA": str(world.data), "CLAUDE_CODE_SESSION_ID": sid}))
        assert last_by(world) == "lead"

    @pytest.mark.parametrize("team", ["", "a/b", "x" * 80, "téam", 7])
    def test_a_team_name_the_by_rule_refuses_falls_back_to_plain_lead(self, world, team):
        (world.session_dir / "pact-session-context.json").write_text(json.dumps({"team_name": team}))
        assert writer().with_team("lead", SID) == "lead"

    def test_an_explicit_by_wins(self, world):
        ok(run_cli(world.repo, "set", world.ref, "--note", "x",
                   env={"CLAUDE_PLUGIN_DATA": str(world.data), "CLAUDE_CODE_SESSION_ID": SID,
                        "REPACT_TASK_BY": "import"}))
        assert last_by(world) == "import"

    def test_the_bridge_writes_as_bridge_at_its_team(self, world):
        world.store.start(world.ref, ptask=f"{TEAM}/{TASK_B}")
        world.task(project_task=world.ref,
                   handoff={**HANDOFF, "evidence": {"head": world.head}, "refs": {world.ref: "done: x"}})
        run_hook(lead_accepts(world))
        assert world.project_task()["status"] == "done"
        assert last_by(world) == f"bridge@{TEAM}"
