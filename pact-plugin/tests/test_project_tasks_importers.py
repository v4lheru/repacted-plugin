"""
D10 P4a importers (the design notes section 8, section 10 as amended
by the lead's gate-model ruling on #186): import-md, import-gates and import-pact-backlog. Each is
a dry run by default and writes nothing without --apply; credential-bearing items are withheld by
line number; every link an importer creates passes D26; `legacy:` links are the importer's alone;
and render(import(release-gates.json)) parses under D5's read_gates to the same gate states.
Every repo is a temp git repo; the live release-gates.json is only READ, as a copy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from project_tasks_support import INERT_MARKER, PLUGIN_ROOT, git, load, new_repo, run_cli

VIEWS_PATH = PLUGIN_ROOT / "bin" / "_tasks" / "views.py"
IMPORTERS_PATH = PLUGIN_ROOT / "bin" / "_tasks" / "importers.py"
DOCTOR_PATH = PLUGIN_ROOT / "bin" / "_doctor.py"
LIVE_GATES = PLUGIN_ROOT.parent.parent / "docs" / "release-gates.json"

BACKLOG_MD = f"""# Project backlog

- **Vault ambient recall redesign.** The per-term floor cannot separate answerable prompts.
- **n8n write-guard hardening (USER-OWNED).** Defense in depth, not required.
- Clear the run ledger before cutover, then archive it: it holds builder runs.
- ~~**Old finished thing.**~~ shipped long ago.
- **Rotate the key.** The value is {INERT_MARKER} and must never be copied.

## From EVENTS (m8a)

- **session_state feature-level variety:** adopt latest_variety_by_task.
  A continuation line that belongs to the bullet above.
- **Merged work DONE.** already in main.
"""

PROGRESS_MD = """# Progress checkpoint

## In flight

| Task | Owner | What | Branch / state | Next step |
|---|---|---|---|---|
| #180 | up-builder | P2 BRIDGE: the acceptance bridge | `repacted/d10-p2` @ `abc1234` | HANDOFF |
| #97 | test-rehearsal | launcher live gate: ALL PASS | done | — |

## Release gates (must pass before the final release)

- The unit 14 gate and the final MEASURE pass.

## Waiting on the user

1. **Rotate the four access tokens** that the desktop app passes on the command line.
2. **Approve the push** of main and tags when asked.

## Resuming after a break

1. Run git worktree list.
"""


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    I = load("tasks_importers_under_test", IMPORTERS_PATH)
    repo = new_repo(tmp_path / "repo")
    store = I.W.init(repo)
    return I, store, repo


def _snapshot(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts or not path.is_file():
            continue
        stat = path.stat()
        out[str(path.relative_to(root))] = (path.read_bytes(), stat.st_mtime_ns)
    return out


def _tasks(I, repo):
    return I.W.PT.load(repo).tasks


def _by_title(I, repo, fragment):
    return next(task for task in _tasks(I, repo).values() if fragment in task["title"])


def _write(repo: Path, rel: str, text: str) -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------------------------
# import-md
# ---------------------------------------------------------------------------------------------

class TestImportMdDryRun:
    def test_the_default_is_a_dry_run_that_writes_nothing(self, world):
        I, store, repo = world
        _write(repo, "docs/backlog.md", BACKLOG_MD)
        before = _snapshot(repo)
        result = run_cli(repo, "import-md", "docs/backlog.md")
        assert result.returncode == 0, result.stderr
        assert _snapshot(repo) == before
        assert _tasks(I, repo) == {}

    def test_the_proposal_table_is_one_line_per_item(self, world):
        I, store, repo = world
        _write(repo, "docs/backlog.md", BACKLOG_MD)
        lines = run_cli(repo, "import-md", "docs/backlog.md").stdout.splitlines()
        rows = [line for line in lines if line.startswith("L")]
        assert [row.split()[0] for row in rows] == ["L3", "L4", "L5", "L6", "L7", "L11", "L13"]
        assert rows[0].split()[1:4] == ["planned", "-", "-"]

    def test_a_credential_bullet_is_withheld_by_line_number_only(self, world):
        I, store, repo = world
        _write(repo, "docs/backlog.md", BACKLOG_MD)
        result = run_cli(repo, "import-md", "docs/backlog.md")
        assert INERT_MARKER not in result.stdout + result.stderr
        row = next(line for line in result.stdout.splitlines() if line.startswith("L7 "))
        assert "WITHHELD" in row and "Rotate" not in row
        applied = run_cli(repo, "import-md", "docs/backlog.md", "--apply")
        assert applied.returncode == 0, applied.stderr
        assert INERT_MARKER not in applied.stdout + applied.stderr
        assert INERT_MARKER not in (repo / ".repacted" / "tasks.jsonl").read_text()
        assert not any("Rotate the key" in task["title"] for task in _tasks(I, repo).values())


class TestImportMdMapping:
    def test_titles_tags_owner_status_and_the_archive_reference(self, world):
        I, store, repo = world
        proposal = I.parse_md(BACKLOG_MD, archive="docs/archive/backlog.md")
        items = {item["line"]: item for item in proposal["items"]}
        assert items[3]["title"] == "Vault ambient recall redesign"
        assert items[3]["note"].startswith("The per-term floor")
        assert items[4]["owner"] == "user" and "USER-OWNED" not in items[4]["title"]
        assert items[5]["title"] == "Clear the run ledger before cutover, then archive it"
        assert items[6]["status"] == "done" and items[6]["title"] == "Old finished thing"
        assert items[11]["tags"] == ["from-events-m8a"] and items[3]["tags"] == []
        assert "continuation line" in items[11]["note"]
        assert items[13]["status"] == "done"
        assert {"kind": "reference", "target": "doc:docs/archive/backlog.md#L3"} in items[3]["links"]
        assert [w["line"] for w in proposal["withheld"]] == [7]

    def test_a_long_first_clause_is_cut_to_the_title_limit(self, world):
        I, store, repo = world
        text = "- " + "word " * 40 + "\n"
        title = I.parse_md(text, archive="docs/archive/x.md")["items"][0]["title"]
        assert len(title) <= I.W.PT.TITLE_MAX and title.endswith("…")

    def test_progress_sections_map_to_active_owner_and_user_items(self, world):
        I, store, repo = world
        proposal = I.parse_md(PROGRESS_MD, archive="docs/archive/progress.md", team="session-1")
        items = {item["title"]: item for item in proposal["items"]}
        bridge = items["P2 BRIDGE: the acceptance bridge"]
        assert bridge["status"] == "active" and bridge["owner"] == "up-builder"
        targets = {link["target"] for link in bridge["links"]}
        assert {"ptask:session-1/180", "branch:repacted/d10-p2"} <= targets
        assert items["launcher live gate: ALL PASS"]["status"] == "done"
        assert items["Rotate the four access tokens"]["owner"] == "user"
        assert not any("MEASURE" in title or "worktree list" in title for title in items)
        assert {s["reason"] for s in proposal["skipped"]} >= {"release gates come from import-gates",
                                                              "static block in the generated view"}

    def test_without_team_a_platform_task_number_is_reported_not_linked(self, world):
        I, store, repo = world
        proposal = I.parse_md(PROGRESS_MD, archive="docs/archive/progress.md")
        bridge = next(i for i in proposal["items"] if i["title"].startswith("P2 BRIDGE"))
        assert not any(link["target"].startswith("ptask:") for link in bridge["links"])
        assert any("--team" in note for note in proposal["notes"])


class TestImportMdApply:
    def test_apply_creates_the_tasks_and_a_second_apply_writes_nothing(self, world):
        I, store, repo = world
        _write(repo, "docs/backlog.md", BACKLOG_MD)
        assert run_cli(repo, "import-md", "docs/backlog.md", "--apply").returncode == 0
        user = _by_title(I, repo, "n8n write-guard hardening")
        assert user["owner"] == "user" and user["status"] == "planned"
        log = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        again = run_cli(repo, "import-md", "docs/backlog.md", "--apply")
        assert again.returncode == 0 and (repo / ".repacted" / "tasks.jsonl").read_bytes() == log

    def test_done_items_close_on_the_committed_archive_copy(self, world):
        I, store, repo = world
        _write(repo, "docs/archive/backlog.md", BACKLOG_MD)
        git(repo, "add", "docs/archive/backlog.md")
        git(repo, "commit", "-q", "-m", "archive")
        result = run_cli(repo, "import-md", "docs/archive/backlog.md", "--apply")
        assert result.returncode == 0, result.stderr
        done = _by_title(I, repo, "Old finished thing")
        assert done["status"] == "done" and done["evidence"][0]["verified"] is True
        assert done["evidence"][0]["target"].startswith("doc:docs/archive/backlog.md#L6@")

    def test_done_items_import_open_and_listed_when_the_archive_is_not_committed(self, world):
        I, store, repo = world
        _write(repo, "docs/backlog.md", BACKLOG_MD)
        result = run_cli(repo, "import-md", "docs/backlog.md", "--apply")
        assert _by_title(I, repo, "Old finished thing")["status"] == "planned"
        assert "L6: imported open" in result.stdout

    def test_an_edited_proposal_is_what_apply_writes(self, world):
        I, store, repo = world
        _write(repo, "docs/backlog.md", BACKLOG_MD)
        proposal = json.loads(run_cli(repo, "import-md", "docs/backlog.md", "--json").stdout)
        proposal["items"] = [item for item in proposal["items"] if item["line"] == 3]
        proposal["items"][0]["title"] = "Recall redesign, as the lead worded it"
        edited = Path(str(repo) + "-proposal.json")
        edited.write_text(json.dumps(proposal))
        result = run_cli(repo, "import-md", "--apply", "--proposal", str(edited))
        assert result.returncode == 0, result.stderr
        assert [t["title"] for t in _tasks(I, repo).values()] == ["Recall redesign, as the lead worded it"]

    def test_an_edited_proposal_still_passes_every_write_rule(self, world):
        I, store, repo = world
        proposal = I.parse_md(BACKLOG_MD, archive="docs/archive/backlog.md")
        proposal["items"][0]["title"] = f"sneaked {INERT_MARKER}"
        before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        with pytest.raises(I.W.Refused):
            I.apply(store, proposal)
        proposal = I.parse_md(BACKLOG_MD, archive="docs/archive/backlog.md")
        proposal["items"][0]["links"].append({"kind": "reference", "target": "doc:../../escape.md"})
        with pytest.raises(I.W.Refused):
            I.apply(store, proposal)
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before


class TestImportMdD26:
    @pytest.mark.parametrize("archive", ["../archive/x.md", "/abs/archive.md", "~/archive.md"])
    def test_an_archive_path_outside_the_repo_is_refused(self, world, archive):
        I, store, repo = world
        _write(repo, "docs/backlog.md", BACKLOG_MD)
        result = run_cli(repo, "import-md", "docs/backlog.md", "--archive-as", archive)
        assert result.returncode == 65

    def test_a_source_outside_the_repo_is_refused(self, world, tmp_path):
        I, store, repo = world
        outside = tmp_path / "elsewhere.md"
        outside.write_text(BACKLOG_MD)
        result = run_cli(repo, "import-md", str(outside))
        assert result.returncode == 65 and "outside" in result.stderr


# ---------------------------------------------------------------------------------------------
# legacy: links are the importer's alone
# ---------------------------------------------------------------------------------------------

class TestLegacyAllowance:
    def test_a_non_importer_write_refuses_a_legacy_link(self, world):
        I, store, repo = world
        ref = store.add("x")
        with pytest.raises(I.W.Refused):
            store.link(ref, "reference", "legacy:ab12")
        assert run_cli(repo, "link", ref, "--reference", "legacy:ab12").returncode == 65

    def test_the_importer_may_write_one_and_only_in_the_legacy_shape(self, world):
        I, store, repo = world
        I.W.check_target(repo, "legacy:ab12", importer=True)
        with pytest.raises(I.W.Refused):
            I.W.check_target(repo, "legacy:../x", importer=True)


# ---------------------------------------------------------------------------------------------
# import-gates and the round trip
# ---------------------------------------------------------------------------------------------

def _gates_file(repo: Path, gates: dict) -> str:
    _write(repo, "docs/release-gates.json", json.dumps({"_doc": "x", "gates": gates}, indent=2))
    return "docs/release-gates.json"


def _states(gates: dict) -> dict:
    """The gate states check 9 acts on: per tag, each entry's task_id, status and evidence presence."""
    return {tag: [(e.get("task_id"), e.get("status"), bool(str(e.get("evidence") or "").strip()))
                  for e in entries] for tag, entries in gates.items()}


def _repoint_passed_evidence(repo: Path, gates: dict) -> int:
    """Re-point every passed gate's evidence at an object that exists in `repo`: a doc keeps its path
    (committed here ONCE per distinct path, and every gate citing that path reuses the sha, since a
    second commit of an unchanged file is "nothing to commit"); any other kind becomes this repo's
    HEAD commit. Returns how many gates were passed."""
    count, doc_sha = 0, {}
    for entries in gates.values():
        for entry in entries:
            if entry.get("status") != "passed":
                continue
            count += 1
            evidence = str(entry.get("evidence") or "")
            if evidence.startswith("doc:") and "@" in evidence:
                path = evidence[len("doc:"):].rsplit("@", 1)[0]
                if path not in doc_sha:
                    _write(repo, path, "evidence\n")
                    git(repo, "add", path)
                    git(repo, "commit", "-q", "-m", "evidence")
                    doc_sha[path] = git(repo, "rev-parse", "HEAD").stdout.strip()
                entry["evidence"] = f"doc:{path}@{doc_sha[path]}"
            else:
                entry["evidence"] = "commit:" + git(repo, "rev-parse", "HEAD").stdout.strip()
    return count


def _round_trip(world, live: dict, label: str) -> None:
    """Import a copy of a live-shaped gate file into the temp repo, render it back, and require the
    same gate states, tags and gate names."""
    I, store, repo = world
    for tag in list(live["gates"])[:4]:              # some tags cut, the rest declared ahead of the cut
        git(repo, "tag", tag)
    assert _repoint_passed_evidence(repo, live["gates"]) >= 1
    rel = _write(repo, "docs/release-gates.json", json.dumps(live, indent=2))
    assert run_cli(repo, "import-gates", "docs/release-gates.json", "--team", "session-5a318e14",
                   "--apply").returncode == 0
    V = load(f"tasks_views_{label}", VIEWS_PATH)
    rel.write_text(V.render_text("release_gates", V.W.PT.load(repo)))
    rendered, problems = load(f"_doctor_{label}", DOCTOR_PATH).read_gates(repo)
    original = live["gates"]
    assert problems == []
    assert _states(rendered) == _states(original)
    assert list(rendered) == list(original)
    assert {tag: [e["gate"] for e in entries] for tag, entries in rendered.items()} == \
        {tag: [e["gate"] for e in entries] for tag, entries in original.items()}


def _pending(live: dict) -> list:
    return [e for entries in live["gates"].values() for e in entries if e.get("status") == "pending"]


def _with_pending(live: dict, count: int) -> list:
    """At least `count` pending gates, adding probe gates under their own tag when the live file
    has fewer: the live file's state (every gate passed, say) must never break these tests."""
    missing = count - len(_pending(live))
    if missing > 0:
        probes = live["gates"].setdefault("repacted-roundtrip-probe", [])
        for i in range(missing):
            probes.append({"gate": f"round-trip probe gate {len(probes) + 1}", "task_id": 7,
                           "status": "pending", "evidence": None})
    return _pending(live)


def _all_passed(live: dict) -> dict:
    """The live file as it is once every gate is marked passed (as at cd89fa7b)."""
    for entries in live["gates"].values():
        for e in entries:
            if e.get("status") == "pending":
                e.update(status="passed", evidence="doc:docs/progress.md@02f2de4a")
    return live


def _ensure_a_passed_gate(live: dict) -> None:
    """When the live file has no passed gate, mark one pending gate passed with the atlas gate's
    evidence shape, so the passed path always runs; when it has one, change nothing."""
    if not any(e.get("status") == "passed" for entries in live["gates"].values() for e in entries):
        _with_pending(live, 1)[0].update(status="passed", evidence="doc:docs/progress.md@02f2de4a")


class TestImportGates:
    def test_a_task_id_needs_a_team(self, world):
        I, store, repo = world
        rel = _gates_file(repo, {"repacted-x": [{"gate": "g", "task_id": 7, "status": "pending",
                                                 "evidence": None}]})
        result = run_cli(repo, "import-gates", rel, "--apply")
        assert result.returncode == 65 and "--team" in result.stderr
        assert _tasks(I, repo) == {}

    def test_a_passed_gate_whose_evidence_does_not_verify_is_refused_by_line(self, world):
        I, store, repo = world
        rel = _gates_file(repo, {"repacted-x": [{"gate": "g", "task_id": None, "status": "passed",
                                                 "evidence": "it passed, trust me"}]})
        dry = run_cli(repo, "import-gates", rel)
        assert "REFUSED" in dry.stdout and "line" in dry.stdout
        result = run_cli(repo, "import-gates", rel, "--apply")
        assert result.returncode == 65
        assert _tasks(I, repo) == {}

    def test_releases_and_gates_become_a_tree_and_an_uncut_tag_has_no_tag_link(self, world):
        I, store, repo = world
        git(repo, "tag", "repacted-cut")
        rel = _gates_file(repo, {"repacted-cut": [{"gate": "g1", "task_id": 3, "status": "pending",
                                                   "evidence": None}],
                                 "repacted-uncut": []})
        result = run_cli(repo, "import-gates", rel, "--team", "session-1", "--apply")
        assert result.returncode == 0, result.stderr
        cut = _by_title(I, repo, "release repacted-cut")
        uncut = _by_title(I, repo, "release repacted-uncut")
        gate = _by_title(I, repo, "g1")
        assert "release.repacted-cut" in cut["tags"] and gate["parent"] == cut["ref"]
        assert {"kind": "reference", "target": "tag:repacted-cut"} in cut["links"]
        assert not any(link["target"].startswith("tag:") for link in uncut["links"])
        assert {"kind": "reference", "target": "ptask:session-1/3"} in gate["links"]

    def test_a_cut_tag_with_no_pending_gate_closes_its_release_on_the_tag(self, world):
        """A release is a container, not work: once its tag exists and nothing gates it, it is
        done with the tag as verified evidence, so it leaves the ready queue."""
        I, store, repo = world
        git(repo, "tag", "repacted-shipped")
        git(repo, "tag", "repacted-gated")
        rel = _gates_file(repo, {"repacted-shipped": [],
                                 "repacted-gated": [{"gate": "g", "task_id": None, "status": "pending",
                                                     "evidence": None}],
                                 "repacted-ahead": []})
        assert run_cli(repo, "import-gates", rel, "--apply").returncode == 0
        shipped = _by_title(I, repo, "release repacted-shipped")
        assert shipped["status"] == "done"
        assert shipped["evidence"] == [{"target": "tag:repacted-shipped", "verified": True}]
        assert _by_title(I, repo, "release repacted-gated")["status"] == "planned"
        assert _by_title(I, repo, "release repacted-ahead")["status"] == "planned"
        ready = [task["title"] for task in I.W.PT.ready(I.W.PT.load(repo))]
        assert not any("repacted-shipped" in title for title in ready)

    def test_an_edited_proposal_cannot_close_a_parent_over_an_open_child(self, world):
        I, store, repo = world
        git(repo, "tag", "repacted-cut")
        text = json.dumps({"gates": {"repacted-cut": [{"gate": "g", "task_id": None, "status": "pending",
                                                       "evidence": None}]}})
        proposal = I.parse_gates(text, repo)
        release = next(item for item in proposal["items"] if item["parent_key"] is None)
        release["status"], release["evidence"] = "done", ["tag:repacted-cut"]
        before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        with pytest.raises(I.W.Refused) as err:
            I.apply(store, proposal)
        assert "open children" in str(err.value)
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before

    def test_a_release_cannot_close_while_a_gate_is_open(self, world):
        I, store, repo = world
        git(repo, "tag", "repacted-cut")
        rel = _gates_file(repo, {"repacted-cut": [{"gate": "g1", "task_id": None, "status": "pending",
                                                   "evidence": None}]})
        assert run_cli(repo, "import-gates", rel, "--apply").returncode == 0
        release = _by_title(I, repo, "release repacted-cut")["ref"]
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(I.W.Refused):
            store.done(release, ["commit:" + sha])


    def test_the_atlas_shaped_evidence_is_still_refused_when_it_does_not_verify_here(self, world):
        """Outside the round-trip fixture nothing is re-pointed: a passed gate whose doc evidence names
        a commit this repo lacks stays refused by line, and --apply writes no task."""
        I, store, repo = world
        rel = _gates_file(repo, {"repacted-x": [{"gate": "first-write", "task_id": None, "status": "passed",
                                                 "evidence": "doc:docs/progress.md@02f2de4a"}]})
        assert "REFUSED" in run_cli(repo, "import-gates", rel).stdout
        assert run_cli(repo, "import-gates", rel, "--apply").returncode == 65
        assert _tasks(I, repo) == {}

    def test_the_round_trip_keeps_a_passed_gate_with_evidence(self, world):
        I, store, repo = world
        git(repo, "tag", "repacted-done")
        rel = _gates_file(repo, {"repacted-done": [{"gate": "g", "task_id": 9, "status": "passed",
                                                    "evidence": "tag:repacted-done"}],
                                 "repacted-open": [{"gate": "h", "task_id": 9, "status": "pending",
                                                    "evidence": None}]})
        original = json.loads((repo / rel).read_text())["gates"]
        assert run_cli(repo, "import-gates", rel, "--team", "s", "--apply").returncode == 0
        V = load("tasks_views_roundtrip2", VIEWS_PATH)
        (repo / rel).write_text(V.render_text("release_gates", V.W.PT.load(repo)))
        rendered, problems = load("_doctor_roundtrip2", DOCTOR_PATH).read_gates(repo)
        assert problems == [] and _states(rendered) == _states(original)

    def test_import_gates_is_a_dry_run_by_default(self, world):
        I, store, repo = world
        rel = _gates_file(repo, {"repacted-x": []})
        before = _snapshot(repo)
        result = run_cli(repo, "import-gates", rel)
        assert result.returncode == 0 and _snapshot(repo) == before


# ---------------------------------------------------------------------------------------------
# import-pact-backlog
# ---------------------------------------------------------------------------------------------

def _legacy(repo: Path, home: Path) -> Path:
    doc = {"version": 1, "project": "demo", "project_path": str(repo), "roots": [str(repo.resolve())],
           "items": [
               {"id": "ab12", "title": "Port the parser", "status": "planned", "rank": 2,
                "ref": "GH-12", "plan": "docs/plan.md", "memory": ["m-1", "m-2"],
                "note": "carry the edge cases", "added": "2026-01-01", "touched": "2026-01-02"},
               {"id": "cd34", "title": "Ship the parser", "status": "blocked", "blocked_by": ["ab12"],
                "added": "2026-01-01"},
               {"id": "ef56", "title": "Old finished item", "status": "done", "added": "2026-01-01"},
               {"id": "0a0b", "title": "Absolute plan", "status": "planned", "plan": "/abs/plan.md"}],
           "archive": [{"id": "9f9f", "title": "Dropped long ago", "status": "dropped"}]}
    path = home / ".claude" / "pact-backlog" / "demo.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(doc, indent=2))
    return path


class TestImportPactBacklog:
    def test_the_source_is_never_touched_and_the_dry_run_writes_nothing(self, world, tmp_path):
        I, store, repo = world
        source = _legacy(repo, tmp_path / "home")
        stamp = (source.read_bytes(), source.stat().st_mtime_ns, oct(source.stat().st_mode))
        before = _snapshot(repo)
        dry = run_cli(repo, "import-pact-backlog", env={"HOME": str(tmp_path / "home")})
        assert dry.returncode == 0, dry.stderr
        assert _snapshot(repo) == before
        applied = run_cli(repo, "import-pact-backlog", "--apply", env={"HOME": str(tmp_path / "home")})
        assert applied.returncode == 0, applied.stderr
        assert (source.read_bytes(), source.stat().st_mtime_ns, oct(source.stat().st_mode)) == stamp

    def test_the_legacy_fields_map_onto_the_graph(self, world, tmp_path):
        I, store, repo = world
        source = _legacy(repo, tmp_path / "home")
        assert run_cli(repo, "import-pact-backlog", str(source), "--apply").returncode == 0
        port = _by_title(I, repo, "Port the parser")
        ship = _by_title(I, repo, "Ship the parser")
        targets = {(link["kind"], link["target"]) for link in port["links"]}
        assert {("reference", "legacy:ab12"), ("reference", "issue:GH-12"), ("input", "doc:docs/plan.md"),
                ("reference", "memory:m-1"), ("reference", "memory:m-2")} <= targets
        assert port["rank"] == 2 and port["note"] == "carry the edge cases"
        assert ship["depends_on"] == [port["ref"]] and ship["status"] == "blocked"
        assert _by_title(I, repo, "Old finished item")["status"] == "dropped"
        assert _by_title(I, repo, "Dropped long ago")["status"] == "dropped"

    def test_an_absolute_plan_is_not_linked_and_is_reported(self, world, tmp_path):
        I, store, repo = world
        source = _legacy(repo, tmp_path / "home")
        result = run_cli(repo, "import-pact-backlog", str(source), "--apply")
        absolute = _by_title(I, repo, "Absolute plan")
        assert not any("/abs/" in link["target"] for link in absolute["links"])
        assert "0a0b" in result.stdout and "D26" in result.stdout

    def test_a_second_apply_writes_nothing(self, world, tmp_path):
        I, store, repo = world
        source = _legacy(repo, tmp_path / "home")
        assert run_cli(repo, "import-pact-backlog", str(source), "--apply").returncode == 0
        log = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        assert run_cli(repo, "import-pact-backlog", str(source), "--apply").returncode == 0
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == log

    def test_no_legacy_file_for_this_checkout_is_exit_66(self, world, tmp_path):
        I, store, repo = world
        (tmp_path / "home").mkdir(exist_ok=True)
        result = run_cli(repo, "import-pact-backlog", env={"HOME": str(tmp_path / "home")})
        assert result.returncode == 66


# ---------------------------------------------------------------------------------------------
# rev 3 (architect-pact review on #186): D25 on every field, D26 on the legacy source, the legacy:
# allowance narrowed to the legacy importer, and releases as containers
# ---------------------------------------------------------------------------------------------

def _legacy_doc(repo: Path, items: list) -> dict:
    return {"version": 1, "project": "demo", "project_path": str(repo), "roots": [str(repo.resolve())],
            "items": items}


class TestRev3D25:
    def test_a_credential_in_a_legacy_ref_plan_or_memory_withholds_the_item(self, world, tmp_path):
        I, store, repo = world
        path = tmp_path / "home" / ".claude" / "pact-backlog" / "demo.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(_legacy_doc(repo, [
            {"id": "aa01", "title": "clean one", "status": "planned", "ref": f"GH-{INERT_MARKER}"},
            {"id": "aa02", "title": "clean two", "status": "planned", "plan": f"docs/{INERT_MARKER}.md"},
            {"id": "aa03", "title": "clean three", "status": "planned", "memory": ["m-1", INERT_MARKER]},
            {"id": "aa04", "title": "clean four", "status": "planned"}])))
        dry = run_cli(repo, "import-pact-backlog", "--json")
        assert dry.returncode == 0, dry.stderr
        assert INERT_MARKER not in dry.stdout + dry.stderr
        proposal = json.loads(dry.stdout)
        assert [item["legacy"] for item in proposal["items"]] == ["aa04"]
        assert sorted(held["where"] for held in proposal["withheld"]) == ["aa01", "aa02", "aa03"]
        applied = run_cli(repo, "import-pact-backlog", "--apply")
        assert applied.returncode == 0 and INERT_MARKER not in applied.stdout + applied.stderr
        assert INERT_MARKER not in (repo / ".repacted" / "tasks.jsonl").read_text()

    def test_a_credential_in_a_heading_gives_its_items_no_tag(self, world):
        I, store, repo = world
        _write(repo, "docs/h.md", f"# Title\n\n## Rotate {INERT_MARKER} now\n\n- **An item.** its body\n")
        dry = run_cli(repo, "import-md", "docs/h.md")
        assert dry.returncode == 0, dry.stderr
        seen = (dry.stdout + dry.stderr).lower()
        assert INERT_MARKER.lower() not in seen and "abcdefgh12" not in seen
        row = next(line for line in dry.stdout.splitlines() if line.startswith("L5 "))
        assert row.split()[3] == "-"
        assert "note: L3: the heading carries a credential value" in dry.stdout
        assert run_cli(repo, "import-md", "docs/h.md", "--apply").returncode == 0
        assert "abcdefgh12" not in (repo / ".repacted" / "tasks.jsonl").read_text().lower()


    def test_a_credential_in_a_table_header_never_reaches_a_note(self, world):
        I, store, repo = world
        _write(repo, "docs/t.md", f"## In flight\n\n| Task | {INERT_MARKER} |\n|---|---|\n| #1 | some next step |\n")
        dry = run_cli(repo, "import-md", "docs/t.md", "--json")
        assert dry.returncode == 0, dry.stderr
        assert "abcdefgh12" not in (dry.stdout + dry.stderr).lower()
        assert any("L3: the table header carries a credential value" in n for n in json.loads(dry.stdout)["notes"])


class TestRev3LegacySource:
    def test_an_explicit_legacy_file_outside_the_repo_and_the_backlog_dir_is_refused(self, world, tmp_path):
        I, store, repo = world
        outside = tmp_path / "elsewhere" / "demo.json"
        outside.parent.mkdir()
        outside.write_text(json.dumps(_legacy_doc(repo, [
            {"id": "bb01", "title": "Distinctive outside title", "status": "planned"}])))
        result = run_cli(repo, "import-pact-backlog", str(outside))
        assert result.returncode == 65
        assert "Distinctive" not in result.stdout + result.stderr

    def test_an_explicit_legacy_file_inside_the_repo_is_read(self, world):
        I, store, repo = world
        _write(repo, "legacy.json", json.dumps(_legacy_doc(repo, [
            {"id": "bb02", "title": "In-repo legacy item", "status": "planned"}])))
        result = run_cli(repo, "import-pact-backlog", "legacy.json")
        assert result.returncode == 0 and "In-repo legacy item" in result.stdout


class TestRev3LegacyAllowanceNarrowed:
    def test_a_legacy_link_in_an_import_md_proposal_is_refused(self, world):
        I, store, repo = world
        proposal = I.parse_md(BACKLOG_MD, archive="docs/archive/backlog.md")
        proposal["items"][0]["links"].append({"kind": "reference", "target": "legacy:ab12"})
        before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        with pytest.raises(I.W.Refused):
            I.apply(store, proposal)
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before

    def test_a_legacy_link_in_an_import_gates_proposal_is_refused(self, world):
        I, store, repo = world
        proposal = I.parse_gates(json.dumps({"gates": {"repacted-x": []}}), repo)
        proposal["items"][0]["links"].append({"kind": "reference", "target": "legacy:ab12"})
        with pytest.raises(I.W.Refused):
            I.apply(store, proposal)


class TestRev3ReleasesAreContainers:
    def test_a_release_leaves_next_until_its_last_gate_closes(self, world):
        I, store, repo = world
        git(repo, "tag", "repacted-cut")
        rel = _gates_file(repo, {"repacted-cut": [{"gate": "g1", "task_id": None, "status": "pending",
                                                   "evidence": None}]})
        assert run_cli(repo, "import-gates", rel, "--apply").returncode == 0
        queue = run_cli(repo, "next", "--all").stdout
        assert "g1" in queue and "release repacted-cut" not in queue
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        store.done(_by_title(I, repo, "g1")["ref"], ["commit:" + sha])
        assert "Close release repacted-cut" in run_cli(repo, "next", "--all").stdout

    def test_a_new_gate_under_a_closed_release_is_refused(self, world):
        I, store, repo = world
        git(repo, "tag", "repacted-cut")
        rel = _gates_file(repo, {"repacted-cut": []})
        assert run_cli(repo, "import-gates", rel, "--apply").returncode == 0
        before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        _gates_file(repo, {"repacted-cut": [{"gate": "late gate", "task_id": None, "status": "pending",
                                             "evidence": None}]})
        result = run_cli(repo, "import-gates", rel, "--apply")
        assert result.returncode == 65 and "settled" in result.stderr
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before


def test_every_importer_names_itself_in_help():
    result = run_cli(Path(os.getcwd()), "--help")
    for verb in ("render", "commit", "import-md", "import-gates", "import-pact-backlog"):
        assert f"repact-task {verb}" in result.stdout
