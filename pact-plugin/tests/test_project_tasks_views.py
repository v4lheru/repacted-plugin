"""
D10 P4a, test plan item 10 plus the lead's rules (the design notes
sections 8 and 10): the generated views (backlog.md, progress.md, release-gates.json), their
GENERATED header and render hash, D5's parsers reading them unchanged, the D25 line scrub, and the
path-limited `repact-task commit`. Every repo is a temp git repo; nothing touches this repo's docs.
"""
from __future__ import annotations

import json
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

    @pytest.mark.parametrize("bad", ["../outside.md",])
    def test_a_view_path_outside_the_repo_is_refused(self, world, bad):
        V, store, repo = world
        _configure(repo, {"views": {"backlog": bad}})
        result = run_cli(repo, "render")
        assert result.returncode == 65, result.stderr
        assert not (repo.parent / "outside.md").exists()


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


