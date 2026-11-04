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


class TestImportMdD26:
    @pytest.mark.parametrize("archive", ["../archive/x.md",])
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


class TestRev3LegacyAllowanceNarrowed:
    def test_a_legacy_link_in_an_import_md_proposal_is_refused(self, world):
        I, store, repo = world
        proposal = I.parse_md(BACKLOG_MD, archive="docs/archive/backlog.md")
        proposal["items"][0]["links"].append({"kind": "reference", "target": "legacy:ab12"})
        before = (repo / ".repacted" / "tasks.jsonl").read_bytes()
        with pytest.raises(I.W.Refused):
            I.apply(store, proposal)
        assert (repo / ".repacted" / "tasks.jsonl").read_bytes() == before


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


def test_every_importer_names_itself_in_help():
    result = run_cli(Path(os.getcwd()), "--help")
    for verb in ("render", "commit", "import-md", "import-gates", "import-pact-backlog"):
        assert f"repact-task {verb}" in result.stdout
