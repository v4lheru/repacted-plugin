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
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from shared import repact_shim

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
                    "XDG_CACHE_HOME": str(tmp / "cache"), "PATH": f"{self.home}/.local/bin{os.pathsep}" + os.environ.get("PATH", "/usr/bin:/bin"),
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
        repacted = self.tmp / "repacted-install"           # the `repact` shim's target (launcher)
        (repacted / "bin").mkdir(parents=True)
        (repacted / "bin" / "repact").write_text("#!/bin/sh\n")
        (repacted / "bin" / "repact").chmod(0o755)
        (self.config / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"plugins": {"PACT@pact-marketplace": [{"scope": "user", "installPath": str(self.pact)}],
                         "repacted@repacted": [{"scope": "user", "installPath": str(repacted)}]}}))
        shim = repact_shim.shim_path(self.home)
        shim.parent.mkdir(parents=True)
        shim.write_text(repact_shim.shim_text())
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


@pytest.mark.slow
def test_a_clean_world_is_green_everywhere(world):
    states = world.states("--full")
    assert {k: v["state"] for k, v in states.items()} == {k: "skip" if k == "state" else "green" for k in states}, states
    assert len(states) == 17   # 9 + kb (KB-CATALOG) + tasks + state + profile (refactor) + distill
    #                            + policy + packs + launcher


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


@pytest.mark.parametrize("where, state", [("memory", "red"),])
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


@pytest.mark.slow
def test_memory_and_kb_hits_together_are_red_and_name_both(world):
    plant_key(world, "memory")
    plant_key(world, "vault")
    states = world.states("--full")
    only_changed(states, "secrets", "red")
    assert states["secrets"]["summary"] == ("credential values in 1 memory/DB place(s); "
                                            "1 KB note(s) withheld from recall (D25)")


@pytest.mark.parametrize("text", [
    f"key = {FAKE_KEY}",])
def test_the_value_check_agrees_with_credential_reason_on_body_text(text):
    """One source of truth: on text with no credential-like title, the doctor's value kinds are
    non-empty exactly when credential_reason gives a reason."""
    doctor = load("_doctor_parity", PLUGIN_ROOT / "bin" / "_doctor.py")
    common = load("_repact_common_parity", PLUGIN_ROOT / "bin" / "_repact_common.py")
    assert bool(doctor._kinds(text)) == (common.credential_reason("plain-note.md", text) is not None)


def test_a_note_titled_as_a_key_store_but_holding_no_value_is_not_a_finding(world):
    (world.config / "agent-memory" / "pact-architect" / "api_keys.md").write_text("# API Keys\nrotate quarterly\n")
    assert world.states("--full")["secrets"]["state"] == "green"


@pytest.mark.slow
def test_the_vault_outside_the_kb_folder_is_never_scanned(world):
    """D26: a key elsewhere in the vault is the user's private business, not a finding."""
    private = world.tmp / "vault" / "Private"
    private.mkdir()
    (private / "passwords.md").write_text(f"{FAKE_KEY}\n")
    assert world.states("--full")["secrets"]["state"] == "green"


@pytest.mark.slow
def test_a_memory_citing_a_deleted_file_is_amber(world):
    doomed = world.home / "doomed.txt"
    doomed.write_text("x")
    (world.config / "agent-memory" / "pact-architect" / "cites.md").write_text("Read `~/doomed.txt` first.\n")
    assert world.states("--full")["stale_refs"]["state"] == "green"
    doomed.unlink()
    states = world.states("--full")
    only_changed(states, "stale_refs", "amber")
    assert str(doomed) in states["stale_refs"]["detail"][0]


@pytest.mark.slow
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


@pytest.mark.slow
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
    "`~/.claude/tasks/session-0d45cf39/`",])
def test_ephemeral_session_and_worktree_locations_are_not_citations(world, cited):
    """Class (a): session task/team dirs, worktrees, pact-session files and the versioned plugin
    cache are historical by nature; a memory naming one is not a stale citation."""
    assert citations(world, f"see {cited} for details") == []


@pytest.mark.parametrize("cited", [
    "`ls -d /opt/homebrew/Cellar/python@3.*`",])
def test_glob_and_template_fragments_are_not_citations(world, cited):
    """Class (b): a path pattern (glob or placeholder slot) names no single file."""
    assert citations(world, f"run {cited} first") == []


@pytest.mark.parametrize("cited", ["`/opt/homebrew/opt/python@X.Y/bin/pythonX.Y`",])
def test_version_and_user_placeholders_are_not_citations(world, cited):
    assert citations(world, f"e.g. {cited}") == []


def test_an_ephemeral_marker_after_a_space_in_the_path_still_counts(world):
    """'Work Projects/App - Dashboard/.worktrees/...': the marker only appears once the spaced
    extension joins the words, so the ephemeral test must see the extended candidate too."""
    assert citations(world, "see `~/Code/Some Projects/App - Dashboard/.worktrees/feature/x/docs/a.md`") == []


def gate(status, evidence=None, name="probes"):
    return {"gate": name, "task_id": 138, "status": status, "evidence": evidence}




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








# --- a stranger's install (U1.2, plan Contract 10) -----------------------------------------------

# The only checks a fresh install may leave amber: none of today's checks is on the list, which
# holds the WARNs Contract 10 allows (semantic-search extras, Telegram, no profile, KB off).
FRESH_AMBER_ALLOWED: set = set()


