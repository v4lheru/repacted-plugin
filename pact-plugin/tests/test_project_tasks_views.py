"""
D10 P4a, test plan item 10 plus the lead's rules (the design notes
sections 8 and 10): the generated views (backlog.md, progress.md, release-gates.json), their
GENERATED header and render hash, D5's parsers reading them unchanged, the D25 line scrub, and the
path-limited `repact-task commit`. Every repo is a temp git repo; nothing touches this repo's docs.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from project_tasks_support import INERT_MARKER, PLUGIN_ROOT, git, load, new_repo, run_cli

VIEWS_PATH = PLUGIN_ROOT / "bin" / "_tasks" / "views.py"
DOCTOR_PATH = PLUGIN_ROOT / "bin" / "_doctor.py"
CONFIG = {"views": {"backlog": "docs/backlog.md", "progress": "docs/progress.md",
                    "release_gates": "docs/release-gates.json"}}


@pytest.fixture
def world(tmp_path):
    V = load("tasks_views_under_test", VIEWS_PATH)
    repo = new_repo(tmp_path / "repo")
    store = V.W.init(repo)
    return V, store, repo


def _configure(repo: Path, config: dict = CONFIG) -> None:
    (repo / ".repacted" / "config.json").write_text(json.dumps(config))


def _doctor():
    return load("_doctor_for_views", DOCTOR_PATH)


def _event(op, t, ts, eid_hex, **fields):
    compact = ts.replace("-", "").replace(":", "").replace(".", "")
    return {"v": 1, "eid": f"{compact}-{eid_hex}", "ts": ts, "by": "lead", "op": op, "t": t, **fields}


def _append_raw(repo: Path, *events) -> None:
    with (repo / ".repacted" / "tasks.jsonl").open("a") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def _gate_world(V, store, repo):
    """A cut tag with one open and one passed gate, an uncut tag with none, a gate-free cut tag."""
    git(repo, "tag", "repacted-a")
    git(repo, "tag", "repacted-b")
    sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    rel_a = store.add("release repacted-a", tags=["release", "release.repacted-a"])
    store.link(rel_a, "reference", "tag:repacted-a")
    open_gate = store.add("PERSONA probes in a sandbox", tags=["gate"], parent=rel_a)
    store.link(open_gate, "reference", "ptask:session-1/138")
    passed = store.add("TEAMMATE subset in a sandbox", tags=["gate"], parent=rel_a)
    store.done(passed, ["commit:" + sha])
    rel_b = store.add("release repacted-b", tags=["release", "release.repacted-b"])
    store.link(rel_b, "reference", "tag:repacted-b")
    store.add("release repacted-next", tags=["release", "release.repacted-next"])
    return sha


# ---------------------------------------------------------------------------------------------
# determinism, header and hash
# ---------------------------------------------------------------------------------------------

class TestDeterminismAndHash:
    def test_rendering_twice_gives_the_same_bytes(self, world):
        V, store, repo = world
        _configure(repo)
        _gate_world(V, store, repo)
        store.add("a user item", owner="user", tags=["ops"])
        V.render(repo)
        first = {name: (repo / path).read_bytes() for name, path in CONFIG["views"].items()}
        V.render(repo)
        assert {name: (repo / path).read_bytes() for name, path in CONFIG["views"].items()} == first

    def test_the_render_clock_is_the_log_not_the_wall(self, world):
        """A task done 20 days before the log's newest event is out of 'done in the last 14 days';
        one done 1 day before is in, whatever today is."""
        V, store, repo = world
        _append_raw(repo,
                    _event("create", "T-00000001", "2020-01-01T00:00:00.000Z", "00000001",
                           title="old finished work", status="dropped", priority="P2"),
                    _event("create", "T-00000002", "2020-01-20T00:00:00.000Z", "00000002",
                           title="recent finished work", status="dropped", priority="P2"),
                    _event("create", "T-00000003", "2020-01-21T00:00:00.000Z", "00000003",
                           title="newest open work", status="planned", priority="P2"))
        text = V.render_text("backlog", V.W.PT.load(repo))
        recent = text.split("## Done or dropped in the last 14 days", 1)[1]
        assert "recent finished work" in recent and "old finished work" not in text

    def test_each_markdown_view_opens_with_the_header_and_closes_with_its_hash(self, world):
        V, store, repo = world
        _configure(repo)
        store.add("x")
        V.render(repo)
        for name in ("backlog", "progress"):
            lines = (repo / CONFIG["views"][name]).read_text().splitlines()
            assert V.HEADER in lines[0]
            assert lines[-1].startswith("<!-- render-hash: sha256:")
            assert V.hand_edited(repo / CONFIG["views"][name]) is False

    def test_a_hand_edit_is_detected_in_markdown_and_json(self, world):
        V, store, repo = world
        _configure(repo)
        _gate_world(V, store, repo)
        V.render(repo)
        backlog = repo / "docs" / "backlog.md"
        backlog.write_text(backlog.read_text().replace("## gate", "## gate (edited)"))
        gates = repo / "docs" / "release-gates.json"
        doc = json.loads(gates.read_text())
        doc["gates"]["repacted-a"][0]["status"] = "passed"
        gates.write_text(json.dumps(doc, indent=2))
        assert V.hand_edited(backlog) is True
        assert V.hand_edited(gates) is True
        assert V.hand_edited(repo / "docs" / "progress.md") is False
        assert V.hand_edited(repo / "docs" / "nope.md") is None


# ---------------------------------------------------------------------------------------------
# D5 reads the views unchanged
# ---------------------------------------------------------------------------------------------

class TestD5ReadsTheViews:
    def test_release_gates_parse_under_read_gates_in_the_ruled_shape(self, world):
        V, store, repo = world
        _configure(repo)
        sha = _gate_world(V, store, repo)
        V.render(repo)
        gates, problems = _doctor().read_gates(repo)
        assert problems == []
        assert list(gates) == ["repacted-a", "repacted-b", "repacted-next"]
        assert gates["repacted-b"] == [] and gates["repacted-next"] == []
        pending, passed = gates["repacted-a"]
        assert pending == {"gate": "PERSONA probes in a sandbox", "task_id": 138, "status": "pending",
                           "evidence": None}
        assert passed["status"] == "passed" and passed["task_id"] is None
        assert passed["evidence"] == "commit:" + sha

    def test_a_dropped_gate_and_a_dropped_release_are_not_rendered(self, world):
        V, store, repo = world
        _gate_world(V, store, repo)
        state = V.W.PT.load(repo)
        gate = next(ref for ref, t in state.tasks.items() if t["title"] == "PERSONA probes in a sandbox")
        release = next(ref for ref, t in state.tasks.items() if t["title"] == "release repacted-b")
        store.drop(gate)
        store.drop(release)
        doc = json.loads(V.render_text("release_gates", V.W.PT.load(repo)))
        assert "repacted-b" not in doc["gates"]
        assert [e["gate"] for e in doc["gates"]["repacted-a"]] == ["TEAMMATE subset in a sandbox"]

    def test_backlog_user_owned_items_are_what_check_8_reads(self, world):
        V, store, repo = world
        _configure(repo)
        store.add("Rotate the four access tokens", owner="user", tags=["security"])
        store.add("Work the lead owns", owner="lead", tags=["security"])
        finished = store.add("Approve the push", owner="user")
        store.drop(finished)
        V.render(repo)
        actions = _doctor()._backlog_actions(types.SimpleNamespace(repo_root=repo))
        assert actions == ["Rotate the four access tokens"]

    def test_progress_lists_active_work_gates_user_items_and_ready_next(self, world):
        V, store, repo = world
        _gate_world(V, store, repo)
        busy = store.add("Build the bridge", owner="up-builder")
        store.start(busy, ptask="session-1/180")
        store.link(busy, "reference", "branch:repacted/d10-p2")
        store.add("Quit Obsidian for the vault write", owner="user")
        store.add("Plan the atlas", priority="P1")
        text = V.render_text("progress", V.W.PT.load(repo))
        in_flight = text.split("## In flight", 1)[1].split("##", 1)[0]
        assert "Build the bridge" in in_flight and "up-builder" in in_flight
        assert "repacted/d10-p2" in in_flight and "session-1/180" in in_flight
        assert "PERSONA probes in a sandbox" in text.split("## Release gates", 1)[1]
        assert "Quit Obsidian" in text.split("## Waiting on the user", 1)[1]
        assert "Plan the atlas" in text.split("## Ready next", 1)[1]
        assert "## Resuming after a break" in text


# ---------------------------------------------------------------------------------------------
# D25 on every view line
# ---------------------------------------------------------------------------------------------

def test_a_credential_that_reached_the_log_raw_is_withheld_from_every_view(world):
    """The writer refuses credential text; a raw line bypasses it, so the view is the second net."""
    V, store, repo = world
    _configure(repo)
    _append_raw(repo, _event("create", "T-0000000a", "2026-01-01T00:00:00.000Z", "0000000a",
                             title=f"leaked {INERT_MARKER} here", status="planned", priority="P2",
                             owner="user", tags=["gate"]))
    V.render(repo)
    for path in CONFIG["views"].values():
        text = (repo / path).read_text()
        assert INERT_MARKER not in text
    for path in ("docs/backlog.md", "docs/progress.md"):      # withheld, but still findable by ref
        held = [line for line in (repo / path).read_text().splitlines() if "withheld" in line]
        assert held and all("T-0000000a" in line for line in held), path


def test_a_credential_in_a_link_is_withheld_by_the_line_net(world):
    """Task text is checked per task; anything else on a view line (a branch link here) is
    caught only by the line-level net."""
    V, store, repo = world
    _configure(repo)
    _append_raw(repo,
                _event("create", "T-0000000b", "2026-01-01T00:00:00.000Z", "0000000b",
                       title="clean title", status="planned", priority="P2"),
                _event("set", "T-0000000b", "2026-01-01T00:00:00.001Z", "0000000c",
                       field="status", value="active"),
                _event("link_add", "T-0000000b", "2026-01-01T00:00:00.002Z", "0000000d",
                       kind="reference", target=f"branch:x-{INERT_MARKER}"))
    V.render(repo)
    progress = (repo / "docs" / "progress.md").read_text()
    assert INERT_MARKER not in progress
    held = [line for line in progress.splitlines() if "withheld" in line]
    assert held and all("T-0000000b" in line for line in held)


# ---------------------------------------------------------------------------------------------
# configuration, the CLI and commit
# ---------------------------------------------------------------------------------------------

class TestConfigAndCli:
    def test_a_write_renders_only_when_views_are_configured(self, world):
        V, store, repo = world
        assert run_cli(repo, "add", "first", "--priority", "P2").returncode == 0
        assert not (repo / "docs").exists()
        _configure(repo)
        assert run_cli(repo, "add", "second", "--priority", "P2").returncode == 0
        assert "second" in (repo / "docs" / "backlog.md").read_text()

    @pytest.mark.parametrize("bad", ["../outside.md", "/nonexistent-p4a-root/abs.md", "~/home.md"])
    def test_a_view_path_outside_the_repo_is_refused(self, world, bad):
        V, store, repo = world
        _configure(repo, {"views": {"backlog": bad}})
        result = run_cli(repo, "render")
        assert result.returncode == 65, result.stderr
        assert not (repo.parent / "outside.md").exists()

    @pytest.mark.parametrize("bad", [".repacted/tasks.jsonl", "./.repacted/tasks.jsonl",
                                     ".repacted/cache/summary.json", ".git/config",
                                     "docs/store/tasks.jsonl", ".REPACTED/tasks.jsonl"])
    def test_a_view_path_in_the_store_or_git_dir_is_refused_and_the_log_survives(self, world, bad):
        """A render replaces its path whole; a view aimed at the log once replaced every event with
        Markdown and exited 0. Every spelling is refused: a symlinked directory (docs/store points
        at .repacted) and, on a case-insensitive file system, a case variant."""
        V, store, repo = world
        (repo / "docs").mkdir()
        (repo / "docs" / "store").symlink_to(repo / ".repacted", target_is_directory=True)
        if bad.startswith(".REPACTED") and not (repo / ".REPACTED").exists():
            pytest.skip("case-sensitive file system: .REPACTED is a different directory")
        store.add("keep me")
        before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        git_config = (repo / ".git" / "config").read_bytes()
        _configure(repo, {"views": {"backlog": bad}})
        result = run_cli(repo, "render")
        assert result.returncode == 65 and "must not be inside" in result.stderr, result.stderr
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before
        assert (repo / ".git" / "config").read_bytes() == git_config

    @pytest.mark.parametrize("bad", [".worktrees/wt/.git", ".worktrees/wt/.repacted/tasks.jsonl",
                                     ".worktrees/wt/.GIT", "docs/wt/.repacted/tasks.jsonl"])
    def test_a_view_path_in_a_nested_worktree_git_file_or_store_is_refused(self, world, bad):
        """Regression: views render in the main checkout, so a nested worktree sits inside the
        repo. Rendering over its .git FILE broke the worktree (git status: invalid gitfile format),
        and its own .repacted/tasks.jsonl is a task log too. Any path part named .git or .repacted
        is refused, by any case and through a symlinked directory (docs/wt points at the worktree)."""
        V, store, repo = world
        worktree = repo / ".worktrees" / "wt"
        (worktree / ".repacted").mkdir(parents=True)
        (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
        (worktree / ".repacted" / "tasks.jsonl").write_text("{}\n")
        (repo / "docs").mkdir()
        (repo / "docs" / "wt").symlink_to(worktree, target_is_directory=True)
        before = {name: (worktree / name).read_bytes() for name in (".git", ".repacted/tasks.jsonl")}
        _configure(repo, {"views": {"backlog": bad}})
        result = run_cli(repo, "render")
        assert result.returncode == 65 and "must not be inside" in result.stderr, result.stderr
        assert {name: (worktree / name).read_bytes() for name in before} == before

    def test_a_view_named_like_git_but_not_a_git_part_is_still_rendered(self, world):
        V, store, repo = world
        _configure(repo, {"views": {"backlog": "docs/.github-backlog.md"}})
        store.add("keep me")
        result = run_cli(repo, "render")
        assert result.returncode == 0, result.stderr
        assert (repo / "docs" / ".github-backlog.md").exists()

    def test_render_without_configured_views_is_a_usage_error(self, world):
        V, store, repo = world
        result = run_cli(repo, "render")
        assert result.returncode == 64 and "config" in result.stderr

    def test_render_configure_writes_the_default_config_once(self, world):
        V, store, repo = world
        assert run_cli(repo, "render", "--configure").returncode == 0
        assert json.loads((repo / ".repacted" / "config.json").read_text()) == {"views": V.DEFAULT_VIEWS}
        assert (repo / "docs" / "release-gates.json").exists()


class TestCommit:
    def test_commit_takes_only_the_log_and_the_views_and_leaves_other_staged_files_staged(self, world):
        V, store, repo = world
        _configure(repo)
        (repo / "unrelated.txt").write_text("a peer's staged work\n")
        git(repo, "add", "unrelated.txt")
        store.add("x")
        result = run_cli(repo, "commit")
        assert result.returncode == 0, result.stderr
        files = set(git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split())
        assert files == {".repacted/tasks.jsonl", "docs/backlog.md", "docs/progress.md",
                         "docs/release-gates.json", ".repacted/config.json", ".gitattributes"}
        assert git(repo, "log", "-1", "--format=%B").stdout.strip() == V.COMMIT_MESSAGE
        assert git(repo, "diff", "--cached", "--name-only").stdout.split() == ["unrelated.txt"]

    def test_the_message_is_one_sentence_without_attribution(self, world):
        V, store, repo = world
        message = V.COMMIT_MESSAGE
        assert message.count(".") == 1 and message.endswith(".") and "\n" not in message
        assert "co-authored" not in message.lower() and "claude" not in message.lower()

    def test_commit_with_nothing_new_says_so_and_exits_0(self, world):
        V, store, repo = world
        _configure(repo)
        store.add("x")
        assert run_cli(repo, "commit").returncode == 0
        head = git(repo, "rev-parse", "HEAD").stdout
        again = run_cli(repo, "commit")
        assert again.returncode == 0 and "nothing to commit" in again.stdout
        assert git(repo, "rev-parse", "HEAD").stdout == head

    def test_commit_without_views_commits_the_log_alone(self, world):
        V, store, repo = world
        store.add("x")
        assert run_cli(repo, "commit").returncode == 0
        files = set(git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split())
        assert files == {".repacted/tasks.jsonl", ".gitattributes"}


# ---------------------------------------------------------------------------------------------
# D10 P5: progress.md alone is enough to resume from (lead ruling q2_resume_region on #251)
# ---------------------------------------------------------------------------------------------

def _section(text: str, heading: str) -> str:
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


class TestProgressIsEnoughToResume:
    def test_a_blocked_task_shows_its_reason(self, world):
        V, store, repo = world
        ref = store.add("Run the sandbox gates", note="the sandbox account is at its weekly limit")
        store.set(ref, "status", "blocked")
        blocked = _section(V.render_text("progress", V.W.PT.load(repo)), "Blocked")
        assert "Run the sandbox gates" in blocked and "weekly limit" in blocked

    def test_a_waiting_task_names_what_it_waits_on(self, world):
        V, store, repo = world
        first = store.add("Apply the curation")
        store.add("Regenerate the map", depends=[first])
        blocked = _section(V.render_text("progress", V.W.PT.load(repo)), "Blocked")
        assert "Regenerate the map" in blocked and first in blocked

    def test_recent_notes_are_newest_first_and_capped(self, world):
        V, store, repo = world
        for index in range(V.RECENT_SHOWN + 3):
            store.add(f"task {index:02d}", note=f"note {index:02d}")
        store.add("a task with no note")
        finished = store.add("finished work", note="a note that is history now")
        store.done(finished, ["commit:" + git(repo, "rev-parse", "HEAD").stdout.strip()])
        notes = _section(V.render_text("progress", V.W.PT.load(repo)), "Recent notes").splitlines()
        shown = [line for line in notes if line.startswith("- ")]
        assert len(shown) == V.RECENT_SHOWN
        assert "note 10" in shown[0] and "note 03" in shown[-1]
        assert not any("no note" in line or "history" in line for line in shown)

    def test_recent_decisions_come_from_decision_links(self, world):
        V, store, repo = world
        (repo / "docs").mkdir(exist_ok=True)
        (repo / "docs" / "decisions.md").write_text("| D28 | sharing |\n| D29 | egress scope |\n")
        ref = store.add("ENV-GUARD-2: stop git staging env files")
        store.link(ref, "reference", "decision:D29")
        store.add("Unrelated work")
        decisions = _section(V.render_text("progress", V.W.PT.load(repo)), "Recent decisions")
        assert "D29" in decisions and "ENV-GUARD-2" in decisions and "Unrelated" not in decisions

    def test_recently_done_lists_the_verified_evidence(self, world):
        V, store, repo = world
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        ref = store.add("the hardening pass: close the guard gaps")
        store.done(ref, ["commit:" + sha])
        done = _section(V.render_text("progress", V.W.PT.load(repo)), "Recently done")
        assert "the hardening pass" in done and sha in done

    def test_the_gate_section_counts_and_folds_gate_free_releases(self, world):
        V, store, repo = world
        _gate_world(V, store, repo)
        gates = _section(V.render_text("progress", V.W.PT.load(repo)), "Release gates")
        assert "1 of 2 gates passed" in gates
        no_gate = [line for line in gates.splitlines() if line.startswith("- no gate:")]
        assert len(no_gate) == 1 and "`repacted-b`" in no_gate[0] and "`repacted-next`" in no_gate[0]

    def test_the_resume_block_names_the_commands_that_replace_hand_edits(self, world):
        V, store, repo = world
        store.add("x")
        resume = _section(V.render_text("progress", V.W.PT.load(repo)), "Resuming after a break")
        for command in ("repact-task add", "repact-task done", "--evidence", "repact-task set",
                        "--note", "repact-task commit"):
            assert command in resume, command


# ---------------------------------------------------------------------------------------------
# D10 P5: a view that drifts from the log is caught
# ---------------------------------------------------------------------------------------------

class TestDrift:
    def test_views_rendered_from_the_log_do_not_drift(self, world):
        V, store, repo = world
        _configure(repo)
        store.add("x", note="y")
        V.render(repo)
        assert V.drifted(repo) == []

    def test_a_hand_edit_is_drift(self, world):
        V, store, repo = world
        _configure(repo)
        store.add("x")
        V.render(repo)
        progress = repo / "docs" / "progress.md"
        progress.write_text(progress.read_text().replace("## Ready next", "## Ready next (edited)"))
        assert V.drifted(repo) == ["progress"]

    def test_a_log_line_added_without_a_render_is_drift(self, world):
        """A consistent hash is not enough: the view must equal the render of the log beside it."""
        V, store, repo = world
        _configure(repo)
        store.add("x")
        V.render(repo)
        _append_raw(repo, _event("create", "T-0000000e", "2030-01-01T00:00:00.000Z", "0000000e",
                                 title="appended raw", status="planned", priority="P2"))
        assert set(V.drifted(repo)) == {"backlog", "progress"}

    def test_drift_reads_the_log_in_the_tree_it_is_given_not_the_main_checkout(self, world, tmp_path):
        """A worktree carries its own tracked copy of the log; its views are compared with THAT."""
        V, store, repo = world
        _configure(repo)
        store.add("x")
        V.render(repo)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "views")
        tree = tmp_path / "wt"
        git(repo, "worktree", "add", "-q", str(tree))
        store.add("only in main's working log")
        assert V.drifted(tree) == []

    def test_no_config_means_nothing_to_compare(self, world):
        V, store, repo = world
        store.add("x")
        assert V.drifted(repo) == []
