"""bin/repact-doctor: every check replays a planted fault, and a clean world
is all green.

Each test builds a throwaway world in tmp_path: HOME and CLAUDE_CONFIG_DIR, the plugin-data dir,
a repacted profile whose vault KB folder is a tmp dir, a fake PACT install, a git repo with a
release worktree, tags, a backlog and docs/release-gates.json, and a stub scope_mcp. Nothing
reads or writes the live ~/.claude, the real vault or the real repo. The doctor runs as a
subprocess; states are read from the cache it writes.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pwd
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DOCTOR = PLUGIN_ROOT / "bin" / "repact-doctor"
FAKE_KEY = "REPACT_TEST_SECRET_ABCDEFGH1234"
PREAMBLE = "`MEMORY-*.md` and `INDEX_*.md` roll up a topic; `ARCHIVE_*.md` holds retired entries."
CLEAN_HEAD = f"# Memory Index\n\n{PREAMBLE}\n\n- [Inbox](MEMORY-inbox.md) — new entries\n"
STUB_SCOPE = """import json, sys, pathlib
out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1])
runs = pathlib.Path(__file__).with_suffix(".runs")
runs.write_text(str(int(runs.read_text() or 0) + 1) if runs.exists() else "1")
servers = json.loads(pathlib.Path(__file__).with_suffix(".servers").read_text())
out.write_text(json.dumps({"servers": servers}))
"""


def load(name: str, path: Path):
    """Import a file by path (bin/ is not on the suite's sys.path, and path setup stays in conftest)."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


def init_task_log(repo: Path) -> None:
    """`repact-task init` in the scratch repo, by path (bin/ is not on the suite's sys.path)."""
    load("repact_doctor_task_writer", PLUGIN_ROOT / "hooks" / "shared" / "task_log" / "write.py").init(repo)


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                          env=env).stdout.strip()


class World:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.home = tmp / "home"
        self.config = self.home / ".claude"
        self.data = tmp / "data"
        self.kb = tmp / "vault" / "05-Education" / "Coding-Notes"
        self.pact = tmp / "pact-install"
        self.repo = tmp / "repo"
        self.scope = tmp / "scope_mcp.py"
        self.env = {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.config),
                    "CLAUDE_PLUGIN_DATA": str(self.data), "XDG_CONFIG_HOME": str(tmp / "xdg"),
                    "XDG_CACHE_HOME": str(tmp / "cache"), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "REPACT_DOCTOR_REPO": str(self.repo), "REPACT_DOCTOR_SCOPE_MCP": str(self.scope)}
        self._build()

    def write_profile(self, data: dict) -> None:
        (self.tmp / "xdg" / "repacted" / "profile.json").write_text(json.dumps(data))

    def _build(self):
        (self.tmp / "xdg" / "repacted").mkdir(parents=True)
        self.write_profile({"identity": {"name": "Doctor World", "email": "owner@example.com"},
                            "paths": {"vault": str(self.kb)}})
        self.kb.mkdir(parents=True)
        (self.kb / "note.md").write_text("# A note\nnothing secret here\n")
        for sub in ("agents", "protocols"):
            (self.pact / sub).mkdir(parents=True)
        (self.pact / "agents" / "pact-architect.md").write_text("agent\n")
        (self.config / "plugins").mkdir(parents=True)
        (self.config / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"plugins": {"PACT@pact-marketplace": [{"scope": "user", "installPath": str(self.pact)}]}}))
        (self.config / "agents").mkdir()
        (self.config / "agents" / "pact-architect.md").symlink_to(self.pact / "agents" / "pact-architect.md")
        (self.config / "protocols").mkdir()
        (self.config / "protocols" / "pact-plugin").symlink_to(self.pact / "protocols")
        (self.config / "CLAUDE.md").write_text("# the user's own global file\n")
        head = self.config / "agent-memory" / "pact-architect"
        head.mkdir(parents=True)
        (head / "MEMORY.md").write_text(CLEAN_HEAD)
        (head / "MEMORY-inbox.md").write_text("")
        (self.home / "notes").mkdir()
        (self.home / "notes" / "ref.md").write_text("x")
        (head / "fact.md").write_text("See `~/notes/ref.md` for details.\n")
        db = self.config / "pact-memory" / "memory.db"
        db.parent.mkdir()
        con = sqlite3.connect(db)
        con.execute("create table memories (id TEXT, context TEXT, lessons_learned TEXT)")
        con.execute("insert into memories values ('m1', 'a plain context', 'a plain lesson')")
        con.commit()
        con.close()
        self.scope.write_text(STUB_SCOPE)
        self.set_mcp([])
        self._build_repo()

    def _build_repo(self):
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "backlog.md").write_text("# Backlog\n\n- **A normal item.** Nothing for the user.\n")
        self.write_gates({})
        init_task_log(self.repo)                          # D10 P3: a committed, clean task log
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "one")
        git(self.repo, "tag", "repacted-v1")
        git(self.repo, "worktree", "add", "-q", "--detach", str(self.repo / ".worktrees" / "release"), "repacted-v1")

    def write_gates(self, gates: dict):
        (self.repo / "docs" / "release-gates.json").write_text(json.dumps({"gates": gates}))

    def new_tag(self, tag: str):
        (self.repo / f"{tag}.txt").write_text(tag)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", tag)
        git(self.repo, "tag", tag)

    def set_mcp(self, servers: list):
        self.scope.with_suffix(".servers").write_text(json.dumps(servers))

    def add_task(self, team: str, task_id: str, subject: str, status: str = "pending", **metadata):
        path = self.config / "tasks" / team / f"{task_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"id": task_id, "subject": subject, "status": status, "metadata": metadata}))

    def run(self, *args: str, **env_extra) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(DOCTOR), *args], env={**self.env, **env_extra},
                              capture_output=True, text=True, timeout=300)

    def states(self, *args: str, **env_extra) -> dict:
        result = self.run(*args, **env_extra)
        assert result.returncode in (0, 1, 2), result.stderr
        cache = json.loads((self.data / "doctor.json").read_text())
        return {c["id"]: c for c in cache["checks"]}


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def only_changed(states: dict, check_id: str, state: str):
    assert states[check_id]["state"] == state, states[check_id]
    others = {k: v["state"] for k, v in states.items() if k != check_id and v["state"] not in ("green",) and (k, v["state"]) != ("state", "skip")}
    assert others == {}, others


def test_a_clean_world_is_green_everywhere(world):
    states = world.states("--full")
    assert {k: v["state"] for k, v in states.items()} == {k: "skip" if k == "state" else "green" for k in states}, states
    assert len(states) == 16   # 9 + kb (KB-CATALOG) + tasks (D10 P3) + state + profile (refactor) + distill
    #                            + policy + packs


def test_the_kb_check_runs_right_after_the_index_check(world):
    world.run("--full")
    ids = [c["id"] for c in json.loads((world.data / "doctor.json").read_text())["checks"]]
    assert ids.index("kb") == ids.index("index") + 1


def test_a_profile_without_identity_is_one_amber_line_naming_the_key(world):
    """The layering pass took the user-specific literals out of the core: an upgrade must say so. The
    KB key is optional (the KB stays off without it), so it is never named as missing."""
    world.write_profile({"identity": {"name": "Doctor World"}})
    states = world.states("--full")
    assert states["profile"]["state"] == "amber"
    assert states["profile"]["summary"].endswith("profile.json lacks identity.email")
    assert "profile.json" in " ".join(states["profile"]["detail"])


def test_a_dangling_link_is_red(world):
    (world.config / "agents" / "pact-gone.md").symlink_to(world.pact / "agents" / "pact-gone.md")
    only_changed(world.states("--full"), "links", "red")


def test_links_at_repacted_outside_a_repact_session_are_amber(world):
    link = world.config / "agents" / "pact-architect.md"
    link.unlink()
    link.symlink_to(PLUGIN_ROOT / "agents" / "pact-architect.md")
    (world.config / "protocols" / "pact-plugin").unlink()
    (world.config / "protocols" / "pact-plugin").symlink_to(PLUGIN_ROOT / "protocols")
    states = world.states("--full")
    only_changed(states, "links", "amber")
    assert "expects pact" in " ".join(states["links"]["detail"])




def test_a_split_memory_dir_is_red(world):
    for name in ("PACT-pact-orchestrator", "repacted-pact-orchestrator"):
        (world.config / "agent-memory" / name).mkdir()
    only_changed(world.states("--full"), "split", "red")


def test_an_over_budget_index_is_amber(world):
    head = world.config / "agent-memory" / "pact-architect" / "MEMORY.md"
    head.write_text(CLEAN_HEAD + "".join(f"- line {n}\n" for n in range(60)))
    states = world.states("--full")
    only_changed(states, "index", "amber")
    assert "pact-architect/MEMORY.md" in states["index"]["detail"][0]


def plant_key(world, where):
    if where == "memory":
        (world.config / "agent-memory" / "pact-architect" / "keys.md").write_text(f"key = {FAKE_KEY}\n")
    elif where == "vault":
        (world.kb / "creds.md").write_text(f"# Notes\n{FAKE_KEY}\n")
    else:
        con = sqlite3.connect(world.config / "pact-memory" / "memory.db")
        con.execute("insert into memories values ('m2', ?, 'x')", (f"token {FAKE_KEY}",))
        con.commit()
        con.close()


@pytest.mark.parametrize("where, state", [("memory", "red"), ("db", "red"), ("vault", "amber")])
def test_a_planted_fake_key_is_flagged_and_its_value_is_never_printed(world, where, state):
    """Memory and the pact-memory DB are RED; a vault KB note is AMBER, the user's own note that
    D25 already withholds from recall (lead ruling)."""
    plant_key(world, where)
    result = world.run("--full")
    states = world.states("--full")
    only_changed(states, "secrets", state)
    assert FAKE_KEY not in result.stdout + result.stderr
    assert FAKE_KEY not in (world.data / "doctor.json").read_text()
    detail = " ".join(states["secrets"]["detail"])
    if where == "vault":
        assert states["secrets"]["summary"] == "1 KB note(s) withheld from recall (D25)"
        assert "KB (withheld from recall):" in detail and "test-secret" in detail
    else:
        assert ("memory.db:memories#" if where == "db" else "test-secret") in detail


def test_memory_and_kb_hits_together_are_red_and_name_both(world):
    plant_key(world, "memory")
    plant_key(world, "vault")
    states = world.states("--full")
    only_changed(states, "secrets", "red")
    assert states["secrets"]["summary"] == ("credential values in 1 memory/DB place(s); "
                                            "1 KB note(s) withheld from recall (D25)")


@pytest.mark.parametrize("text", [
    f"key = {FAKE_KEY}", "nothing here", "password: hunter2hunter2", "Authorization: Bearer abcdefghijklmnop1234",
    "SESSION_SECRET is an env var name", "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
])
def test_the_value_check_agrees_with_credential_reason_on_body_text(text):
    """One source of truth: on text with no credential-like title, the doctor's value kinds are
    non-empty exactly when credential_reason gives a reason."""
    doctor = load("_doctor_parity", PLUGIN_ROOT / "bin" / "_doctor.py")
    common = load("_repact_common_parity", PLUGIN_ROOT / "bin" / "_repact_common.py")
    assert bool(doctor._kinds(text)) == (common.credential_reason("plain-note.md", text) is not None)


def test_a_note_titled_as_a_key_store_but_holding_no_value_is_not_a_finding(world):
    (world.config / "agent-memory" / "pact-architect" / "api_keys.md").write_text("# API Keys\nrotate quarterly\n")
    assert world.states("--full")["secrets"]["state"] == "green"


def test_the_vault_outside_the_kb_folder_is_never_scanned(world):
    """D26: a key elsewhere in the vault is the user's private business, not a finding."""
    private = world.tmp / "vault" / "Private"
    private.mkdir()
    (private / "passwords.md").write_text(f"{FAKE_KEY}\n")
    assert world.states("--full")["secrets"]["state"] == "green"


def test_a_memory_citing_a_deleted_file_is_amber(world):
    doomed = world.home / "doomed.txt"
    doomed.write_text("x")
    (world.config / "agent-memory" / "pact-architect" / "cites.md").write_text("Read `~/doomed.txt` first.\n")
    assert world.states("--full")["stale_refs"]["state"] == "green"
    doomed.unlink()
    states = world.states("--full")
    only_changed(states, "stale_refs", "amber")
    assert str(doomed) in states["stale_refs"]["detail"][0]


def test_a_cited_path_with_spaces_is_not_a_false_alarm(world):
    spaced = world.home / "Work Projects" / "CSM - Demo Dashboard"
    spaced.mkdir(parents=True)
    memory = world.config / "agent-memory" / "pact-architect" / "spaced.md"
    memory.write_text("Repo: `~/Work Projects/CSM - Demo Dashboard/`\n")
    assert world.states("--full")["stale_refs"]["state"] == "green"
    spaced.rmdir()  # the control: the same citation, now missing, must be caught in full
    states = world.states("--full")
    assert states["stale_refs"]["state"] == "amber"
    assert states["stale_refs"]["detail"][0].endswith(f"{world.home}/Work Projects/CSM - Demo Dashboard/")


def test_private_vault_citations_are_counted_not_checked(world):
    """D26: a cited path inside the vault but outside the KB folder is never checked; a KB one is."""
    kb = world.home / "Obsidian Vault" / "05-Education" / "Coding-Notes"
    kb.mkdir(parents=True)
    world.write_profile({"identity": {"name": "Doctor World", "email": "owner@example.com"},
                         "paths": {"vault": str(kb)}})
    (world.config / "agent-memory" / "pact-architect" / "vault-cites.md").write_text(
        "Diary: `~/Obsidian Vault/Private/diary.md`\nKB: `~/Obsidian Vault/05-Education/Coding-Notes/gone.md`\n")
    states = world.states("--full")
    only_changed(states, "stale_refs", "amber")
    assert states["stale_refs"]["summary"] == ("1 cited path(s) no longer exist, in 1 memory file(s); "
                                               "1 private-vault citation(s) not checked (D26)")
    assert states["stale_refs"]["detail"][0].endswith("/Obsidian Vault/05-Education/Coding-Notes/gone.md")
    assert "diary" not in " ".join(states["stale_refs"]["detail"])


def test_a_private_vault_citation_is_never_stat_ed(world, monkeypatch):
    doctor = load("_doctor_nostat", PLUGIN_ROOT / "bin" / "_doctor.py")
    touched = []
    real = doctor.os.path.lexists
    monkeypatch.setattr(doctor.os.path, "lexists", lambda p: touched.append(str(p)) or real(p))
    ctx = doctor.Context(config_root=world.config, home=world.home, plugin_root=PLUGIN_ROOT, env=world.env)
    vault = (str(world.home / "Obsidian Vault"), str(world.home / "Obsidian Vault" / "05-Education" / "Coding-Notes"))
    missing, private = doctor._missing_citations(
        "see `~/Obsidian Vault/Private/diary.md` and `~/Obsidian Vault/Journal 2026/x.md`", ctx, vault)
    assert (missing, private) == ([], 2)
    assert not [t for t in touched if "Obsidian Vault" in t], touched


def test_a_private_citation_through_a_symlinked_home_is_still_never_stat_ed(world, monkeypatch):
    """amend_3: /Users/alice is a symlink to /Users/bob, and memories spell both. The
    citation's home prefix is canonicalised (never the vault part) before the private test."""
    doctor = load("_doctor_alias", PLUGIN_ROOT / "bin" / "_doctor.py")
    alias = world.tmp / "alias-home"
    alias.symlink_to(world.home)
    real_home = os.path.realpath(world.home)
    vault = (f"{real_home}/Obsidian Vault", f"{real_home}/Obsidian Vault/05-Education/Coding-Notes")
    touched = []
    real = doctor.os.path.lexists
    monkeypatch.setattr(doctor.os.path, "lexists", lambda p: touched.append(str(p)) or real(p))
    ctx = doctor.Context(config_root=world.config, home=alias, plugin_root=PLUGIN_ROOT, env=world.env)
    missing, private = doctor._missing_citations("see `~/Obsidian Vault/Private/diary.md`", ctx, vault)
    assert (missing, private) == ([], 1)
    assert not [t for t in touched if "Obsidian Vault" in t], touched


def citations(world, text):
    doctor = load("_doctor_citations", PLUGIN_ROOT / "bin" / "_doctor.py")
    ctx = doctor.Context(config_root=world.config, home=world.home, plugin_root=PLUGIN_ROOT, env=world.env)
    return doctor._missing_citations(text, ctx, None)[0]


@pytest.mark.parametrize("cited", [
    "`~/.claude/tasks/session-0d45cf39/`", "`~/.claude/teams/session-f3bfde25/`", "`~/.claude/tasks/pact-ad43b47b`",
    "`~/work/app/.worktrees/feature/okta/docs/x.md`", "`~/.claude/worktrees/devops-pull`",
    "`~/.claude/pact-sessions/compact-summary.txt`",
    "`~/.claude/plugins/cache/pact-marketplace/PACT/4.4.40/hooks/shared/session_registry.py`",
])
def test_ephemeral_session_and_worktree_locations_are_not_citations(world, cited):
    """Class (a): session task/team dirs, worktrees, pact-session files and the versioned plugin
    cache are historical by nature; a memory naming one is not a stale citation."""
    assert citations(world, f"see {cited} for details") == []


@pytest.mark.parametrize("cited", [
    "`ls -d /opt/homebrew/Cellar/python@3.*`", "`~/.claude/backups/.claude.json.backup.*`",
    "`~/.claude/.env.*`", "`~/output/extracted_data_*.csv`", "`~/data/pact-<id>/`", "`~/data/pact-{session_hash}/`",
])
def test_glob_and_template_fragments_are_not_citations(world, cited):
    """Class (b): a path pattern (glob or placeholder slot) names no single file."""
    assert citations(world, f"run {cited} first") == []


@pytest.mark.parametrize("cited", ["`/opt/homebrew/opt/python@X.Y/bin/pythonX.Y`", "`/Users/me/code`"])
def test_version_and_user_placeholders_are_not_citations(world, cited):
    assert citations(world, f"e.g. {cited}") == []


def test_an_ephemeral_marker_after_a_space_in_the_path_still_counts(world):
    """'Work Projects/App - Dashboard/.worktrees/...': the marker only appears once the spaced
    extension joins the words, so the ephemeral test must see the extended candidate too."""
    assert citations(world, "see `~/Code/Some Projects/App - Dashboard/.worktrees/feature/x/docs/a.md`") == []


def test_a_citation_marked_gone_is_resolved_and_an_unmarked_one_is_not(world):
    """Class (d): memory hygiene appends ' (path gone as of <date>)' after a citation instead of
    deleting the sentence; the doctor must read that as resolved, and only right after the path."""
    assert citations(world, "Repo `~/Code/Proj/gone-build` (path gone as of 2026-09-27), branch x") == []
    assert citations(world, "repo `~/Code/Some Projects/gone-sheet` (path gone as of 2026-09-27)") == []
    assert citations(world, "Repo `~/Code/Proj/gone-build`, see (path gone as of 2026-09-27) below") == [
        f"{world.home}/Code/Proj/gone-build"]


def test_remote_marked_and_quoted_truncated_cites_are_skipped_and_a_real_one_is_not(world):
    """Regression (2026-09-29): a VM path, awk's cut of a spaced name, and a quoted fixture
    fragment were reported as stale."""
    (world.home / "Work" / "Some Projects").mkdir(parents=True, exist_ok=True)
    assert citations(world, "- **Log**: `~/extraction.log` on the VM (remote host)") == []
    assert citations(world, 'ssh vm --command="tail ~/extraction.log"  # (not a local path)') == []
    assert citations(world, "awk leaves `~/Work/Some`, and every `git -C` fails") == []
    assert citations(world, "fails via a TRUNCATION (`'~/Sites/secret-project/fil'` cut off)") == []
    assert citations(world, "the log is `~/extraction.log`, see `~/Work/Gone`") == [
        f"{world.home}/extraction.log", f"{world.home}/Work/Gone"]


@pytest.mark.parametrize("text, cited", [
    ("Worktree (build\nphase): `~/Code/Some Projects/demo-build`.", "Code/Some Projects/demo-build"),
    ("**Gate suite. Worktree\n`~/Code/Some Projects/demo-sheet`, branch x", "Code/Some Projects/demo-sheet"),
    ("(tasks #3, worktree `~/Code/Some Projects/demo-sheet`, branch `feature/x`)",
     "Code/Some Projects/demo-sheet"),
])
def test_the_word_worktree_near_a_missing_path_does_not_hide_it(world, text, cited):
    """Prose is not evidence: only a .worktrees or .claude/worktrees path segment makes a
    citation ephemeral, so a missing path the memory merely calls a worktree stays a finding."""
    assert citations(world, text) == [f"{world.home}/{cited}"]


@pytest.mark.parametrize("cited", [
    "`~/work/scratchpad/notes.md`", "`~/work/scratchpad`", "`/private/tmp/x/scratchpad/venv/bin/python`",
])
def test_a_scratchpad_path_segment_is_ephemeral(world, cited):
    assert citations(world, f"see {cited}") == []


def test_scratchpad_inside_a_longer_name_is_a_real_citation(world):
    """'scratchpad-app' is a project, not a scratch dir: the marker is a whole path segment."""
    assert citations(world, "see `~/code/scratchpad-app/README.md`") == [
        f"{world.home}/code/scratchpad-app/README.md"]


def test_a_path_list_is_checked_part_by_part(world):
    (world.home / "a").mkdir()
    (world.home / "b").mkdir()
    assert citations(world, "`PATH=~/a:~/b`") == []
    assert citations(world, "`PATH=~/a:~/gone`") == [f"{world.home}/gone"]


def test_a_line_continuation_is_not_part_of_the_cited_path(world):
    assert citations(world, "~/gone-tool \\\n  --flag") == [f"{world.home}/gone-tool"]


def test_a_plain_missing_path_is_still_reported(world):
    """The control: the new skips must not swallow a real stale citation."""
    assert citations(world, "Repo `~/Code/Some Project/really-gone.md`, branch x") == [
        f"{world.home}/Code/Some Project/really-gone.md"]


def test_a_users_home_alias_is_canonicalised_but_the_rest_is_not(monkeypatch):
    doctor = load("_doctor_users_alias", PLUGIN_ROOT / "bin" / "_doctor.py")
    seen = []

    def fake_realpath(path):
        seen.append(path)
        return {"/Users/alice": "/Users/bob"}.get(path, path)

    monkeypatch.setattr(doctor.os.path, "realpath", fake_realpath)
    ctx = doctor.Context(config_root=Path("/nowhere"), home=Path("/Users/bob"), plugin_root=PLUGIN_ROOT, env={})
    canon = doctor._canon_home("/Users/alice/Code/Obsidian Vault - New/Private/x.md", ctx)
    assert canon == "/Users/bob/Code/Obsidian Vault - New/Private/x.md"
    assert seen == ["/Users/alice"], "only the home prefix may be resolved, never the vault part"


def test_maintainer_only_rows_hide_from_the_table_only_when_they_skip():
    """F7: a stranger's install shows no release/gates/state SKIP rows; an owner whose check
    applies still sees the row, and the cache keeps every row either way."""
    doctor = load("_doctor_maintainer_rows", PLUGIN_ROOT / "bin" / "_doctor.py")
    Result = doctor.Result
    results = [Result("index", "index budgets", "green", "ok"),
               Result("release", "release position", "skip", "no repo or no release worktree"),
               Result("gates", "release gates", "amber", "1 pending"),
               Result("state", "state ownership", "skip", "no state-ownership table shipped here")]
    table = "\n".join(doctor.render_report(results))
    assert "release position" not in table and "state ownership" not in table
    assert "AMBER 2. release gates" in table and "GREEN 1. index budgets" in table
    assert len(results) == 4


def test_a_lagging_release_tag_is_amber_and_an_undeclared_tag_too(world):
    world.new_tag("repacted-m1")
    states = world.states("--full")
    assert states["release"]["state"] == "amber" and "1 newer tag" in states["release"]["summary"]
    assert states["gates"]["state"] == "amber" and "undeclared" in states["gates"]["summary"]


def gate(status, evidence=None, name="probes"):
    return {"gate": name, "task_id": 138, "status": status, "evidence": evidence}


def test_a_pending_gate_shows_and_a_passed_one_with_evidence_clears(world):
    world.new_tag("repacted-m1")
    world.write_gates({"repacted-m1": [gate("pending")]})
    states = world.states("--full")
    assert states["gates"]["state"] == "amber"
    assert "waiting on gate: repacted-m1 (probes, task #138)" in states["gates"]["detail"]
    world.write_gates({"repacted-m1": [gate("passed", "#138 k=3 run 2026-09-29, report docs/test/persona.md")]})
    assert world.states("--full")["gates"]["state"] == "green"


@pytest.mark.parametrize("evidence", [None, "", "   "])
def test_a_gate_passed_without_evidence_is_amber(world, evidence):
    world.new_tag("repacted-m1")
    world.write_gates({"repacted-m1": [gate("passed", evidence)]})
    states = world.states("--full")
    assert states["gates"]["state"] == "amber"
    assert "passed WITHOUT evidence: repacted-m1 (probes, task #138)" in states["gates"]["detail"]


def test_every_gate_of_a_tag_must_pass(world):
    world.new_tag("repacted-m1")
    world.write_gates({"repacted-m1": [gate("passed", "ok"), gate("pending", name="parity")]})
    states = world.states("--full")
    assert states["gates"]["state"] == "amber"
    assert states["gates"]["detail"] == ["waiting on gate: repacted-m1 (parity, task #138)"]




def test_an_empty_gate_list_declares_no_gate_needed(world):
    world.new_tag("repacted-f1")
    world.write_gates({"repacted-f1": []})
    assert world.states("--full")["gates"]["state"] == "green"


@pytest.mark.parametrize("entries", [{"gate": "x", "status": "passed"}, [gate("done")]])
def test_a_malformed_gate_record_is_amber(world, entries):
    world.new_tag("repacted-m1")
    world.write_gates({"repacted-m1": entries})
    states = world.states("--full")
    assert states["gates"]["state"] == "amber" and "malformed" in states["gates"]["summary"]


def test_a_pending_alert_task_is_red_and_a_completed_one_is_not_listed(world):
    world.add_task("team-a", "7", "ALERT: SECURITY: rotate the planted fixture token")
    world.add_task("team-a", "8", "ALERT: SECURITY: already handled", status="completed")
    world.add_task("team-b", "3", "[HALT] fixture", type="algedonic")
    states = world.states("--full")
    only_changed(states, "actions", "red")
    detail = " ".join(states["actions"]["detail"])
    assert "#7 ALERT: SECURITY: rotate the planted fixture token" in detail and "#3" in detail
    assert "already handled" not in detail


def test_check_8_lists_exactly_what_d3s_predicate_calls_open(world):
    """One definition of 'waiting on the user' (amend_2): the doctor uses D3's predicate."""
    ua = load("_user_actions_parity", PLUGIN_ROOT / "hooks" / "shared" / "user_actions.py")
    tasks = [("1", "ALERT: rotate", "pending", {}), ("2", "ALERTING the team", "pending", {}),
             ("3", "BLOCKER: token", "in_progress", {}), ("4", "RELEASE GATE: probes", "pending", {}),
             ("5", "HALT: data", "completed", {}), ("6", "[HALT] fixture", "pending", {"type": "algedonic"}),
             ("7", "alert: lowercase", "pending", {}), ("8", "plain work", "pending", {})]
    for task_id, subject, status, meta in tasks:
        world.add_task("team", task_id, subject, status, **meta)
    doctor = load("_doctor_parity8", PLUGIN_ROOT / "bin" / "_doctor.py")
    ctx = doctor.Context(config_root=world.config, home=world.home, plugin_root=PLUGIN_ROOT, env=world.env)
    listed = {line.split(" ", 1)[0].lstrip("#") for line in doctor._alert_tasks(ctx)}
    expected = {t[0] for t in tasks if ua.is_open_user_action(
        {"id": t[0], "subject": t[1], "status": t[2], "metadata": t[3]})}
    assert listed == expected == {"1", "3", "4", "6"}


def test_a_user_owned_backlog_line_is_amber(world):
    (world.repo / "docs" / "backlog.md").write_text("- **Rotate the fixture keys (USER-OWNED).** Only you can.\n")
    states = world.states("--full")
    only_changed(states, "actions", "amber")
    assert states["actions"]["detail"] == ["Rotate the fixture keys"]


def test_mcp_drift_is_amber(world):
    world.set_mcp([{"server": "fixture-server", "effective": "scope"}, {"server": "kept", "effective": "keep"}])
    states = world.states("--full")
    only_changed(states, "mcp", "amber")
    assert states["mcp"]["detail"] == ["would change: fixture-server"]


def test_slow_checks_reuse_their_cache_until_full(world):
    runs = world.scope.with_suffix(".runs")
    world.states()
    assert runs.read_text() == "1"
    world.states()
    assert runs.read_text() == "1", "a fresh slow result is reused, not re-run"
    world.states("--quick")
    assert runs.read_text() == "1", "--quick never runs a slow check"
    world.states("--full")
    assert runs.read_text() == "2"


def test_quick_with_no_cache_skips_the_slow_checks(world):
    states = world.states("--quick")
    assert states["secrets"]["state"] == "skip" and states["mcp"]["state"] == "skip"
    assert not world.scope.with_suffix(".runs").exists()


def test_a_concurrent_run_changes_nothing(world):
    import fcntl
    world.data.mkdir(parents=True, exist_ok=True)
    fd = os.open(world.data / "doctor.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        result = world.run("--full")
    finally:
        os.close(fd)
    assert result.returncode == 0 and "another run is in progress" in result.stdout
    assert not (world.data / "doctor.json").exists()


def test_exit_codes_follow_the_worst_state(world):
    assert world.run("--full").returncode == 0
    world.add_task("t", "1", "ALERT: fixture")
    assert world.run("--full").returncode == 2


# ---------------------------------------------------------------------------
# kb: the KB catalog's health (KB-CATALOG; the design notes section 6.4)
# ---------------------------------------------------------------------------

KB_FRONT = ("---\ntype: resource\ntags: [wiki]\nstatus: draft\ncreated: {created}\nmodified: {modified}\n"
            "area: work\nwiki-source: manual\nwiki-confidence: high\n{extra}---\n")


def kb_page(world, rel: str, body: str = "Body.", modified: str = "2026-01-01", extra: str = "",
            related: str = "- [[note]]", created: str = "2026-01-01") -> None:
    path = world.kb / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(KB_FRONT.format(created=created, modified=modified, extra=extra) + f"\n# {Path(rel).stem}\n\n{body}\n\n"
                    f"## Related\n\n{related}\n")


def test_kb_green_summary_carries_the_counts(world):
    kb_page(world, "topic-a.md")
    kb = world.states("--full")["kb"]
    assert kb["state"] == "green" and kb["detail"] == []
    assert "2 pages" in kb["summary"] and "withheld 0" in kb["summary"] and "orphans" in kb["summary"]


def test_a_supersession_cycle_is_red_and_names_the_listing_command(world):
    for me, other in (("cyc-a", "cyc-b"), ("cyc-b", "cyc-a")):
        kb_page(world, f"{me}.md", extra=f'wiki-status: superseded\nwiki-status-since: 2026-09-01\n'
                                         f'wiki-superseded-by: "[[{other}]]"\n')
    states = world.states("--full")
    only_changed(states, "kb", "red")
    assert any("2 supersession chain(s) broken or cyclic" in d and "vault-find --catalog chains" in d
               for d in states["kb"]["detail"])


def test_a_schema_error_on_a_page_created_since_the_contract_is_amber(world):
    kb_page(world, "topic-new.md", created="2026-09-28", modified="2026-09-28")   # a topic page without keys
    states = world.states("--full")
    only_changed(states, "kb", "amber")
    assert any("vault-find --catalog lint" in d for d in states["kb"]["detail"])


@pytest.mark.parametrize("modified", ["2026-01-01", "2026-09-28"])
def test_a_legacy_schema_error_stays_green_even_on_a_touched_page(world, modified):
    """The cutoff is `created` (lead ruling): the curation apply bumping `modified` on a legacy
    page must not turn the doctor amber for that page's old errors."""
    kb_page(world, "topic-old.md", modified=modified)
    assert world.states("--full")["kb"]["state"] == "green"


def test_broken_links_ratchet_amber_on_a_rise_and_green_after_the_fix(world):
    kb_page(world, "a.md", related="- [[gone-one]]")
    assert world.states("--full")["kb"]["state"] == "green"          # the first run sets the baseline
    kb_page(world, "b.md", related="- [[gone-two]]")
    states = world.states("--full")
    only_changed(states, "kb", "amber")
    assert any("broken link(s), above the baseline of 1" in d and "vault-find --catalog broken" in d
               for d in states["kb"]["detail"])
    (world.kb / "a.md").unlink()
    (world.kb / "b.md").unlink()
    assert world.states("--full")["kb"]["state"] == "green"
    kb_page(world, "c.md", related="- [[gone-three]]")
    assert world.states("--full")["kb"]["state"] == "amber"          # the baseline ratcheted down to 0


def test_a_withheld_page_is_the_secrets_checks_line_not_a_second_kb_action(world):
    plant_key(world, "vault")
    states = world.states("--full")
    only_changed(states, "secrets", "amber")
    assert "withheld 1" in states["kb"]["summary"]
    assert FAKE_KEY not in (world.data / "doctor.json").read_text()


def test_a_catalog_refresh_failure_is_amber_and_names_the_failure(world, monkeypatch):
    doctor = load("_doctor_kb_fail", PLUGIN_ROOT / "bin" / "_doctor.py")
    for key in ("HOME", "CLAUDE_PLUGIN_DATA", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(key, world.env[key])
    monkeypatch.delenv("REPACT_VAULT", raising=False)
    catalog = doctor._kb_catalog()

    def boom(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(catalog, "refresh", boom)
    ctx = doctor.Context(config_root=world.config, home=world.home, plugin_root=PLUGIN_ROOT, env=world.env)
    result = doctor.check_kb(ctx)
    assert result.state == "amber" and "OSError" in result.summary


def test_a_missing_kb_folder_is_skip(world):
    import shutil
    shutil.rmtree(world.kb)
    assert world.states("--full")["kb"]["state"] == "skip"


# --- 11 state ownership (docs/architecture/state-ownership.md) --------------------------------

def state_doc(world, writers) -> str:
    """A tmp ownership page naming `writers` for one store, read via REPACT_DOCTOR_STATE_DOC."""
    page = world.tmp / "state-ownership.md"
    block = json.dumps([{"id": "probe", "root": "config", "glob": "nothing/*.json", "writers": writers}])
    page.write_text(f"# State\n\n```json state-ownership\n{block}\n```\n")
    return str(page)


def plant_wiped_team(world, team="session-ab12cd34", lead="lead-session-1", rows=2) -> Path:
    (world.config / "teams" / team).mkdir(parents=True)
    (world.config / "teams" / team / "config.json").write_text(json.dumps({"leadSessionId": lead}))
    (world.config / "tasks" / team).mkdir(parents=True)                 # the directory stands, empty
    session = world.config / "pact-sessions" / "proj" / lead
    session.mkdir(parents=True)
    (session / "session-journal.jsonl").write_text("".join(
        json.dumps({"type": "agent_dispatch", "task_id": str(n), "agent": "x", "phase": "CODE"}) + "\n"
        for n in range(1, rows + 1)) + json.dumps({"type": "task_metadata_snapshot", "task_id": "1",
                                                   "metadata": {}}) + "\n")
    return session




def test_a_store_whose_writer_is_not_in_the_plugin_is_red(world):
    page = state_doc(world, ["host", "hooks/no_such_writer.py"])
    states = world.states("--full", REPACT_DOCTOR_STATE_DOC=page)
    only_changed(states, "state", "red")
    assert states["state"]["detail"][0] == "probe: writer hooks/no_such_writer.py is not in the plugin"






def test_clear_task_store_removes_this_sessions_halt_marker_only(world):
    from shared.task_store import PROBE_MARKER
    state = load("_doctor_state_marker_pin", PLUGIN_ROOT / "bin" / "_doctor_state.py")
    assert state.PROBE_MARKER == PROBE_MARKER
    session = plant_wiped_team(world)
    marker = session / PROBE_MARKER
    marker.write_text(json.dumps({"halt": True, "misses": 2}))
    other = session.parent / "other-session" / PROBE_MARKER
    other.parent.mkdir()
    other.write_text("{}")
    assert world.run("--clear", "task-store").returncode == 64          # no session named
    done = world.run("--clear", "task-store", CLAUDE_CODE_SESSION_ID="lead-session-1")
    assert done.returncode == 0 and str(marker) in done.stdout, done.stdout
    assert not marker.exists() and other.exists()
    assert "nothing to clear" in world.run("--clear", "task-store", CLAUDE_CODE_SESSION_ID="lead-session-1").stdout


# --- a stranger's install (U1.2, plan Contract 10) -----------------------------------------------

# The only checks a fresh install may leave amber: none of today's checks is on the list, which
# holds the WARNs Contract 10 allows (semantic-search extras, Telegram, no profile, KB off).
FRESH_AMBER_ALLOWED: set = set()


def test_a_fresh_home_passes_with_the_mode_and_host_lines(tmp_path):
    """env -i style: only HOME and a system PATH, no profile, user layer, vault, repo or teams
    flag. Exit 0, nothing red, no amber off the allow list, both host notes, and no skip that
    names a file only the private repo has."""
    home, cwd = tmp_path / "home", tmp_path / "work"
    (home / ".claude").mkdir(parents=True)
    cwd.mkdir()
    run = subprocess.run([sys.executable, str(DOCTOR), "--full"], cwd=cwd, capture_output=True,
                         text=True, timeout=300, env={"HOME": str(home), "PATH": "/usr/bin:/bin"})
    assert run.returncode == 0, run.stdout + run.stderr
    cache = json.loads(next((home / ".claude" / "plugins" / "data").glob("*/doctor.json")).read_text())
    states = {c["id"]: c for c in cache["checks"]}
    assert not [k for k, c in states.items() if c["state"] == "red"], states
    assert {k for k, c in states.items() if c["state"] == "amber"} <= FRESH_AMBER_ALLOWED, states
    assert states["profile"]["state"] == "green" and "optional" in states["profile"]["summary"]
    assert "mode: guards-only (Agent Teams flag off)" in run.stdout.splitlines()
    assert re.search(r"^host \S+: (tested|untested) \(last tested [\d.]+\)", run.stdout, re.M), run.stdout
    real_home = pwd.getpwuid(os.getuid()).pw_dir          # conftest points HOME at a tmp dir
    assert "scope_mcp" not in run.stdout and real_home not in run.stdout


def test_the_mode_reads_the_settings_flag_and_an_unlisted_host_is_untested(world):
    host = load("_doctor_host_mode", PLUGIN_ROOT / "bin" / "_doctor_host.py")
    (world.config / "settings.json").write_text(json.dumps({"env": {host.TEAMS_FLAG: "1"}}))
    fake = world.tmp / "fakebin" / "claude"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho '0.0.1 (Claude Code)'\n")
    fake.chmod(0o755)
    run = world.run(PATH=f"{fake.parent}:/usr/bin:/bin")
    assert "mode: full (Agent Teams on)" in run.stdout
    assert "host 0.0.1: untested (last tested " in run.stdout, run.stdout


@pytest.mark.parametrize("kb", ["/kb", "~/notes"])
def test_a_shallow_kb_has_no_private_vault_and_raises_nothing(world, monkeypatch, kb):
    """kb.parents[1] does not exist for /kb, and is /Users (above home) for ~/notes: neither may
    be a private vault, so no citation goes unchecked and nothing raises."""
    world.write_profile({"identity": {"email": "owner@example.com"}, "paths": {"kb": kb}})
    monkeypatch.setenv("XDG_CONFIG_HOME", world.env["XDG_CONFIG_HOME"])
    monkeypatch.setenv("HOME", str(world.home))
    doctor = load("_doctor_shallow", PLUGIN_ROOT / "bin" / "_doctor.py")
    ctx = doctor.Context(config_root=world.config, home=world.home, plugin_root=PLUGIN_ROOT, env=world.env)
    assert doctor.private_vault(ctx) is None
