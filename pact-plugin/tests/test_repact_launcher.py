"""bin/repact, driven end to end with a stub `claude` on PATH and a temp config root.

The stub records its argv and environment as JSON, and can hold until the test releases it,
so each test can look at the memory dir DURING the session as well as after it. A `ps` shim
controls what the running-claude check sees. Nothing here touches the live ~/.claude.

CORE-ROUTE: the rig also carries a global <config>/CLAUDE.md, a slim-core source and its
staging dir, so the tests prove the session gets the core while the global file keeps its
bytes, inode and mtime throughout.

The live sequential gate (pact -> repact -> pact in a sandbox) is test-rehearsal's; see the
launcher HANDOFF for its commands.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = PLUGIN_ROOT / "bin"
REPACT = BIN_DIR / "repact"
_spec = importlib.util.spec_from_file_location("_memdir", BIN_DIR / "_memdir.py")
_memdir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_memdir)  # by path: path setup is conftest-owned (test_path_setup_pin)

LEGACY, REPACTED = _memdir.LEGACY_NAME, _memdir.DEFAULT_NAME
AGENT = ["--agent", "repacted:pact-orchestrator"]
DEFAULT_SETTINGS = '{"enabledPlugins":{"PACT@pact-marketplace":false}}'
AGENT_NAMES = sorted(p.name for p in (PLUGIN_ROOT / "agents").glob("pact-*.md"))
BASHES = sorted({"/bin/bash", *(p for p in [shutil.which("bash")] if p)})

STUB_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
memory = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "agent-memory"
links = sorted(Path(os.environ["CLAUDE_CONFIG_DIR"]).joinpath("agents").glob("pact-*.md")) + [
    Path(os.environ["CLAUDE_CONFIG_DIR"]) / "protocols" / "pact-plugin",
    Path(os.environ["HOME"]) / ".claude" / "protocols" / "pact-plugin"]
glob_md = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "CLAUDE.md"
record = {"argv": sys.argv[1:], "launch": os.environ.get("REPACTED_LAUNCH"),
          "execution": os.environ.get("REPACT_EXECUTION"), "routed": os.environ.get("REPACT_ROUTED"),
          "additional_md": os.environ.get("CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD"),
          "global_md": glob_md.read_text() if glob_md.exists() else None,
          "memory": sorted(p.name for p in memory.iterdir()) if memory.is_dir() else [],
          "links": {str(p): os.path.realpath(p) for p in links}}
Path(os.environ["STUB_LOG"]).write_text(json.dumps(record))
hold = os.environ.get("STUB_HOLD")
if hold:
    Path(hold + ".started").write_text(str(os.getpid()))
    while os.path.exists(hold):
        time.sleep(0.02)
    Path(hold + ".exited").write_text("")
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
'''


def _default_int_and_quit():
    """A suite started as an async job (`pytest ... &` in a script) inherits SIGINT and SIGQUIT
    ignored, and bash cannot trap a signal ignored on entry; the launcher starts as from a terminal."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)


class Rig:
    """A temp config root, project dir, managed dir and stub bin, plus helpers."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.config = tmp_path / "config"
        self.memory = self.config / "agent-memory"
        self.project = tmp_path / "project"
        self.managed = tmp_path / "managed"
        self.stub_bin = tmp_path / "stub-bin"
        self.log = tmp_path / "claude-argv.json"
        self.hold = tmp_path / "hold"
        for d in (self.memory, self.project, self.managed, self.stub_bin, tmp_path / "home"):
            d.mkdir(parents=True)
        # CORE-ROUTE: the user's global memory file, the slim core and its staging dir.
        self.global_md = self.config / "CLAUDE.md"
        self.global_md.write_text("# the user's global instructions\n@RTK.md\n", encoding="utf-8")
        self.core_src = tmp_path / "user-layer" / "CLAUDE.core.md"
        self.core_src.parent.mkdir()
        self.core_src.write_text("# slim core\n", encoding="utf-8")
        self.core_dir = tmp_path / "core-cache"
        self._script("claude", STUB_CLAUDE)
        self._script("ps", '#!/bin/sh\nprintf "%s\\n" "${STUB_PS:-  1 /bin/zsh}"\n')
        (self.stub_bin / "python3").symlink_to(sys.executable)
        self.pact_root = tmp_path / "cache" / "pact-marketplace" / "PACT" / "4.8.1"
        (self.pact_root / "protocols").mkdir(parents=True)
        (self.pact_root / "agents").mkdir()
        for agent in AGENT_NAMES:
            (self.pact_root / "agents" / agent).write_text("PACT body\n", encoding="utf-8")
        self.write_json(self.config / "plugins" / "installed_plugins.json", {"version": 2, "plugins": {
            "PACT@pact-marketplace": [{"scope": "user", "installPath": str(self.pact_root),
                                       "version": "4.8.1"}]}})
        # The state a PACT session leaves: every shared link points at PACT's root.
        for agent in AGENT_NAMES:
            self.link(self.config / "agents" / agent, self.pact_root / "agents" / agent)
        for link in self.protocol_links():
            self.link(link, self.pact_root / "protocols")

    @staticmethod
    def link(path: Path, target: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)

    def protocol_links(self):
        return [self.config / "protocols" / "pact-plugin",
                self.tmp / "home" / ".claude" / "protocols" / "pact-plugin"]

    def all_links(self):
        return [self.config / "agents" / a for a in AGENT_NAMES] + self.protocol_links()

    def link_inodes(self):
        """A re-point replaces the link, so an unchanged inode proves it was never touched."""
        return [os.lstat(link).st_ino for link in self.all_links()]

    def owners(self, links=None) -> set:
        """Which root each link resolves into: {'pact'}, {'repacted'} or a mix."""
        roots = {"pact": os.path.realpath(self.pact_root), "repacted": os.path.realpath(PLUGIN_ROOT)}
        found = set()
        for link in links if links is not None else self.all_links():
            real = os.path.realpath(link)
            found |= {k for k, r in roots.items() if real == r or real.startswith(r + os.sep)} or {"other"}
        return found

    def _script(self, name, text):
        path = self.stub_bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    def env(self, **extra):
        env = {"HOME": str(self.tmp / "home"), "CLAUDE_CONFIG_DIR": str(self.config),
               "REPACTED_MANAGED_SETTINGS_DIR": str(self.managed),
               "PATH": f"{self.stub_bin}{os.pathsep}/usr/bin{os.pathsep}/bin",
               "STUB_LOG": str(self.log), "REPACTED_CORE_SOURCE": str(self.core_src),
               "REPACTED_CORE_DIR": str(self.core_dir)}
        env.update({k: v for k, v in extra.items() if v is not None})
        return env

    def seed(self, name=LEGACY) -> tuple:
        d = self.memory / name
        (d / "topics").mkdir(parents=True)
        (d / "MEMORY.md").write_text("# orchestrator head\n", encoding="utf-8")
        (d / "topics" / "lesson.md").write_text("a lesson\n", encoding="utf-8")
        (d / "link.md").symlink_to("MEMORY.md")
        return _memdir.tree_fingerprint(d)

    def names(self):
        return sorted(p.name for p in self.memory.iterdir())

    def fingerprint(self, name):
        return _memdir.tree_fingerprint(self.memory / name)

    def run(self, *args, bash="/bin/bash", script=REPACT, **env):
        return subprocess.run([bash, str(script), *args], cwd=self.project, env=self.env(**env),
                              capture_output=True, text=True, timeout=60)

    def start(self, *args, bash="/bin/bash", **env):
        """Launch with the stub holding; returns once the stub reports it started."""
        self.hold.write_text("")
        proc = subprocess.Popen([bash, str(REPACT), *args], cwd=self.project,
                                env=self.env(STUB_HOLD=str(self.hold), **env),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                start_new_session=True, preexec_fn=_default_int_and_quit)
        self.wait_for(Path(str(self.hold) + ".started"))
        return proc

    def release(self):
        self.hold.unlink()
        self.wait_for(Path(str(self.hold) + ".exited"))

    @staticmethod
    def wait_for(path: Path, timeout=20.0):
        deadline = time.monotonic() + timeout
        while not path.exists():
            assert time.monotonic() < deadline, f"timed out waiting for {path}"
            time.sleep(0.02)

    def stub_record(self):
        return json.loads(self.log.read_text(encoding="utf-8")) if self.log.exists() else None

    def write_json(self, path: Path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def _core_deny_rules(core_dir):
    """--add-dir makes the served dir a workspace dir, so acceptEdits could write it; the
    session gets deny rules for every editing tool over the dir as written and as resolved."""
    spellings = sorted({str(core_dir), os.path.realpath(core_dir)})
    return [f"{tool}(/{path}/**)" for path in spellings for tool in ("Edit", "Write", "NotebookEdit")]


def _alternate_argv(rig, *tail, settings=None, core=True):
    """The argv bin/repact gives claude in alternate mode. With the core (the default), the
    --settings JSON also excludes the global CLAUDE.md and --add-dir serves the core."""
    if settings is None:
        payload = json.loads(DEFAULT_SETTINGS)
        if core:
            payload["claudeMdExcludes"] = sorted({str(rig.global_md), os.path.realpath(rig.global_md)})
            payload["permissions"] = {"deny": _core_deny_rules(rig.core_dir)}
        settings = json.dumps(payload, separators=(",", ":"))
    extra = ["--add-dir", str(rig.core_dir)] if core else []
    return ["--plugin-dir", str(PLUGIN_ROOT), "--settings", settings, *extra, *AGENT, *tail]


# ---------- the two modes ----------

@pytest.mark.parametrize("bash", BASHES)
def test_alternate_mode_swaps_the_dir_in_for_the_session_and_back_after(rig, bash):
    original = rig.seed()
    proc = rig.start("-c", bash=bash)

    assert rig.names() == [REPACTED]                    # during the session
    assert rig.fingerprint(REPACTED) == original
    record = rig.stub_record()
    assert record["argv"] == _alternate_argv(rig, "-c")
    assert record["launch"] == "alternate" and record["memory"] == [REPACTED]
    assert len(record["links"]) == len(AGENT_NAMES) + 2
    assert rig.owners([Path(p) for p in record["links"]]) == {"repacted"}  # at claude's start

    rig.release()
    out, err = proc.communicate(timeout=20)
    assert proc.returncode == 0, err
    assert rig.names() == [LEGACY]                      # after it
    assert rig.fingerprint(LEGACY) == original
    assert rig.owners() == {"pact"}
    assert os.readlink(rig.config / "agents" / AGENT_NAMES[0]) == str(
        rig.pact_root / "agents" / AGENT_NAMES[0])
    assert out == "" and err == ""


@pytest.mark.parametrize("key", ["repacted@repacted-local", "repacted@repacted"],
                         ids=["this-mac", "github"])
def test_installed_mode_execs_claude_with_no_swap(rig, key):
    original = rig.seed()
    rig.write_json(rig.config / "settings.json",
                   {"enabledPlugins": {key: True, "PACT@pact-marketplace": False}})
    result = rig.run("--resume", "abc")
    assert result.returncode == 0, result.stderr
    record = rig.stub_record()
    assert record["argv"] == [*AGENT, "--resume", "abc"]
    assert record["launch"] is None and record["memory"] == [LEGACY]
    assert rig.names() == [LEGACY] and rig.fingerprint(LEGACY) == original
    assert rig.owners([Path(p) for p in record["links"]]) == {"pact"}


def test_a_first_launch_with_no_memory_dir_yet_works(rig):
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert rig.stub_record()["argv"] == _alternate_argv(rig)
    assert rig.names() == []


# ---------- the three refusals ----------

@pytest.mark.parametrize("where", ["file", "drop-in", "plist"])
def test_refuses_when_a_managed_source_enables_pact(rig, where):
    import plistlib

    original = rig.seed()
    payload = {"enabledPlugins": {"PACT@pact-marketplace": True}}
    if where == "file":
        rig.write_json(rig.managed / "managed-settings.json", payload)
    elif where == "drop-in":
        rig.write_json(rig.managed / "managed-settings.d" / "20-plugins.json", payload)
    else:
        (rig.managed / "com.anthropic.claudecode.plist").write_bytes(plistlib.dumps(payload))
    result = rig.run("-c")
    assert result.returncode == 2
    assert "managed settings enable PACT@pact-marketplace" in result.stderr
    assert rig.stub_record() is None
    assert rig.names() == [LEGACY] and rig.fingerprint(LEGACY) == original
    assert rig.owners() == {"pact"}


def test_a_later_managed_drop_in_that_disables_pact_wins(rig):
    rig.write_json(rig.managed / "managed-settings.json", {"enabledPlugins": {"PACT@pact-marketplace": True}})
    rig.write_json(rig.managed / "managed-settings.d" / "10-off.json",
                   {"enabledPlugins": {"PACT@pact-marketplace": False}})
    assert rig.run().returncode == 0


def test_refuses_a_root_without_a_plugin_manifest(rig, tmp_path):
    fake_bin = tmp_path / "not-a-plugin" / "bin"
    fake_bin.mkdir(parents=True)
    for name in ("repact", "repacted-memdir", "_memdir.py", "_repact_launch.py", "_agentlinks.py"):
        shutil.copy2(BIN_DIR / name, fake_bin / name)
    fake_shared = fake_bin.parent / "hooks" / "shared"      # the launcher reads managed settings from shared/
    fake_shared.mkdir(parents=True)
    for name in ("__init__.py", "coexistence.py", "paths.py"):
        shutil.copy2(PLUGIN_ROOT / "hooks" / "shared" / name, fake_shared / name)
    original = rig.seed()
    result = rig.run(script=fake_bin / "repact")
    assert result.returncode == 2 and "no .claude-plugin/plugin.json" in result.stderr
    assert rig.stub_record() is None and rig.fingerprint(LEGACY) == original


def test_refuses_a_split_and_touches_neither_dir(rig):
    a, b = rig.seed(LEGACY), rig.seed(REPACTED)
    inodes = rig.link_inodes()
    result = rig.run("-c")
    assert result.returncode == 2
    assert "SPLIT" in result.stderr and "repacted-memdir --merge-split --confirm-live" in result.stderr
    assert rig.stub_record() is None
    assert (rig.fingerprint(LEGACY), rig.fingerprint(REPACTED)) == (a, b)
    assert rig.link_inodes() == inodes                  # never re-pointed, not even round-trip


def test_refuses_a_nested_launch_inside_a_repact_session(rig):
    original = rig.seed()
    inodes = rig.link_inodes()
    result = rig.run(REPACTED_LAUNCH="alternate")
    assert result.returncode == 2 and "inside a repact session" in result.stderr
    assert rig.stub_record() is None and rig.fingerprint(LEGACY) == original
    assert rig.link_inodes() == inodes


# ---------- arguments ----------

@pytest.mark.parametrize("args", [
    ["--resume", "x"],
    ["-c"],
    ["a prompt with  two spaces, 'single', \"double\", $HOME and a\nnewline"],
    ["--model", "opus", "-p", "--", "--repact-status"],
], ids=["resume", "continue", "prompt-with-spaces", "status-flag-not-first"])
def test_every_argument_passes_through_byte_exact(rig, args):
    rig.seed()
    result = rig.run(*args)
    assert result.returncode == 0, result.stderr
    assert rig.stub_record()["argv"] == _alternate_argv(rig, *args)


def test_every_enabled_pact_key_is_disabled_for_the_session(rig):
    rig.write_json(rig.config / "settings.json", {"enabledPlugins": {"PACT@pact-marketplace": True}})
    rig.write_json(rig.project / ".claude" / "settings.local.json",
                   {"enabledPlugins": {"PACT@another-market": True, "PACTX@x": True}})
    assert rig.run().returncode == 0
    settings = json.loads(rig.stub_record()["argv"][3])
    assert settings["enabledPlugins"] == {"PACT@another-market": False, "PACT@pact-marketplace": False}


def test_claudes_exit_code_is_kept_and_the_dir_still_goes_back(rig):
    original = rig.seed()
    result = rig.run("-p", "hi", STUB_EXIT="7")
    assert result.returncode == 7
    assert rig.names() == [LEGACY] and rig.fingerprint(LEGACY) == original


@pytest.mark.parametrize("args", [["--repact-status"],
                                  ["--dangerously-skip-permissions", "--repact-status"]],
                         ids=["alone", "after-the-shell-function-flags"])
def test_repact_status_reports_and_never_starts_claude(rig, args):
    rig.seed()
    result = rig.run(*args)
    assert result.returncode == 0, result.stderr
    assert "mode: alternate" in result.stdout and f"plugin root: {PLUGIN_ROOT}" in result.stdout
    assert f"{LEGACY}: dir" in result.stdout and "split: no" in result.stdout
    assert f"core: {rig.core_src}" in result.stdout and "excluded" in result.stdout
    assert rig.stub_record() is None


# ---------- another claude running: warn once, proceed ----------

def test_another_claude_process_prints_one_warning_and_proceeds(rig):
    rig.seed()
    result = rig.run("-c", STUB_PS="  4242 /opt/homebrew/bin/claude\n  7 /bin/zsh")
    assert result.returncode == 0
    assert result.stderr.count("\n") == 1
    assert "warning" in result.stderr and "4242" in result.stderr
    assert rig.stub_record()["argv"] == _alternate_argv(rig, "-c")


def test_the_desktop_app_is_not_a_claude_process(rig):
    rig.seed()
    result = rig.run(STUB_PS="  55 /Applications/Claude.app/Contents/MacOS/Claude")
    assert result.returncode == 0 and result.stderr == ""


# ---------- signals: the trap ----------

def test_term_to_the_process_group_ends_claude_and_the_trap_renames_back(rig):
    """The live gate's TERM shape (a terminal close / group kill)."""
    original = rig.seed()
    proc = rig.start("-c")
    assert rig.names() == [REPACTED]
    os.killpg(proc.pid, signal.SIGTERM)
    proc.communicate(timeout=20)
    assert proc.returncode == 143
    assert rig.names() == [LEGACY] and rig.fingerprint(LEGACY) == original
    assert rig.owners() == {"pact"}


def test_term_to_the_launcher_alone_waits_for_claude_then_renames_back(rig):
    """Bash defers a trapped signal until its foreground child exits, so the dir stays with
    the live session and moves back exactly when claude ends."""
    original = rig.seed()
    proc = rig.start("-c")
    os.kill(proc.pid, signal.SIGTERM)
    time.sleep(0.5)
    assert proc.poll() is None                          # the launcher is still waiting
    assert rig.names() == [REPACTED]                    # the session still holds the dir
    assert rig.owners() == {"repacted"}                 # and the links stay with it
    rig.release()
    proc.communicate(timeout=20)
    assert proc.returncode == 143
    assert rig.names() == [LEGACY] and rig.fingerprint(LEGACY) == original
    assert rig.owners() == {"pact"}


@pytest.mark.parametrize("sig, code", [(signal.SIGHUP, 129), (signal.SIGINT, 130)])
def test_hup_and_int_to_the_group_also_rename_back(rig, sig, code):
    original = rig.seed()
    proc = rig.start()
    os.killpg(proc.pid, sig)
    proc.communicate(timeout=20)
    assert proc.returncode == code
    assert rig.names() == [LEGACY] and rig.fingerprint(LEGACY) == original
    assert rig.owners() == {"pact"}


def test_kill_9_then_a_pact_session_is_detected_as_a_split_and_merged_without_loss(rig):
    original = rig.seed()
    proc = rig.start("-c")
    os.kill(proc.pid, signal.SIGKILL)                   # no trap runs
    proc.wait(timeout=20)                               # not communicate: the orphan holds the pipes
    rig.release()                                       # the orphaned session ends later
    proc.communicate(timeout=20)
    assert rig.names() == [REPACTED] and rig.fingerprint(REPACTED) == original
    assert rig.owners() == {"repacted"}                 # the trap never ran

    # The next `pact` session starts with no PACT-named dir and writes a fresh one.
    stray = rig.memory / LEGACY
    stray.mkdir()
    (stray / "MEMORY.md").write_text("# a fresh head\n", encoding="utf-8")
    (stray / "new-lesson.md").write_text("learned while split\n", encoding="utf-8")
    both = _memdir.content_fingerprint([rig.memory / REPACTED, stray])
    split_state = (rig.fingerprint(LEGACY), rig.fingerprint(REPACTED))

    rig.log.unlink()
    refused = rig.run("-c")
    assert refused.returncode == 2 and "SPLIT" in refused.stderr
    assert rig.stub_record() is None
    assert (rig.fingerprint(LEGACY), rig.fingerprint(REPACTED)) == split_state

    merge = subprocess.run([sys.executable, str(BIN_DIR / "repacted-memdir"), "--merge-split",
                            "--confirm-live"], env=rig.env(), capture_output=True, text=True,
                           timeout=60)
    assert merge.returncode == 0, merge.stderr
    assert "merged the split: 1 file(s) moved, 1 collision(s)" in merge.stdout
    assert rig.owners() == {"pact"}                     # the repair restores the links too
    assert rig.names() == [LEGACY]
    assert _memdir.content_fingerprint([rig.memory / LEGACY]) == both
    assert (stray / "MEMORY.md").read_text(encoding="utf-8") == "# orchestrator head\n"

    relaunch = rig.run("-c")
    assert relaunch.returncode == 0
    assert relaunch.stderr.count("\n") == 1 and "1 *.split-* file(s)" in relaunch.stderr
    assert rig.names() == [LEGACY]
    assert _memdir.content_fingerprint([rig.memory / LEGACY]) == both


# ---------- the shared agent and protocols links (live gate #97, item c) ----------

@pytest.mark.parametrize("registry", ["missing", "no-pact-key", "two-pact-keys", "path-without-agents"])
def test_refuses_when_pacts_install_root_is_not_certain(rig, registry):
    path = rig.config / "plugins" / "installed_plugins.json"
    entry = [{"scope": "user", "installPath": str(rig.pact_root)}]
    if registry == "missing":
        path.unlink()
    elif registry == "no-pact-key":
        rig.write_json(path, {"version": 2, "plugins": {"other@x": entry}})
    elif registry == "two-pact-keys":
        rig.write_json(path, {"version": 2, "plugins": {"PACT@a": entry, "PACT@b": entry}})
    else:
        rig.write_json(path, {"version": 2, "plugins": {"PACT@pact-marketplace": [
            {"scope": "user", "installPath": str(rig.tmp / "nowhere")}]}})
    original = rig.seed()
    result = rig.run("-c")
    assert result.returncode == 2 and "PACT" in result.stderr and "REFUSED" in result.stderr
    assert rig.stub_record() is None
    assert rig.names() == [LEGACY] and rig.fingerprint(LEGACY) == original
    assert rig.owners() == {"pact"}


def test_a_real_agent_file_is_never_overwritten_and_the_rest_are_switched(rig):
    override = rig.config / "agents" / AGENT_NAMES[0]
    override.unlink()
    override.write_text("my local override\n", encoding="utf-8")
    rig.seed()
    proc = rig.start()
    assert rig.owners(rig.all_links()[1:]) == {"repacted"}
    rig.release()
    _, err = proc.communicate(timeout=20)
    assert proc.returncode == 0
    assert override.read_text(encoding="utf-8") == "my local override\n" and not override.is_symlink()
    assert rig.owners(rig.all_links()[1:]) == {"pact"}
    assert "real file" in err and AGENT_NAMES[0] in err


def test_a_failed_link_switch_rolls_the_rename_back_and_never_starts_claude(rig):
    original = rig.seed()
    agents = rig.config / "agents"
    agents.chmod(0o500)                                 # no new link can be written there
    try:
        result = rig.run("-c")
    finally:
        agents.chmod(0o700)
    assert result.returncode != 0 and "restoring PACT's name and links" in result.stderr
    assert rig.stub_record() is None
    assert rig.names() == [LEGACY] and rig.fingerprint(LEGACY) == original
    assert rig.owners() == {"pact"}


def test_the_links_switch_even_on_a_first_launch_with_no_memory_dir(rig):
    proc = rig.start()
    assert rig.owners([Path(p) for p in rig.stub_record()["links"]]) == {"repacted"}
    rig.release()
    proc.communicate(timeout=20)
    assert proc.returncode == 0 and rig.owners() == {"pact"} and rig.names() == []


def test_repact_status_reports_link_ownership(rig):
    rig.seed()
    result = rig.run("--repact-status")
    assert result.returncode == 0, result.stderr
    assert f"PACT root: {rig.pact_root}" in result.stdout
    assert f"links ({len(AGENT_NAMES) + 2}): {len(AGENT_NAMES) + 2} -> PACT" in result.stdout
    for link in rig.all_links()[:3]:
        link.unlink()
        link.symlink_to(PLUGIN_ROOT / "agents" / link.name)
    mixed = rig.run("--repact-status").stdout
    assert f"3 -> repacted" in mixed and f"{len(AGENT_NAMES) - 1} -> PACT" in mixed


# ---------- CORE-ROUTE: the slim core per session, the global CLAUDE.md never touched ----------
# Measured on claude 2.1.283 (isolated -p probes, hooks disabled): claudeMdExcludes in the
# --settings flag drops the global file AND its @imports (17,156 -> 2,512 first-turn input
# tokens); --add-dir <dir with the core as CLAUDE.md> + CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD
# loads the core (3,869 tokens) and a Task subagent sees it too; --append-system-prompt-file
# reaches the main agent only, so it is not used.

def _file_state(path: Path):
    st = path.stat()
    return path.read_bytes(), st.st_ino, st.st_mtime_ns


def test_a_repact_session_gets_the_core_and_excludes_the_global_file(rig):
    rig.seed()
    before = _file_state(rig.global_md)
    proc = rig.start("-c")
    record = rig.stub_record()
    assert record["additional_md"] == "1"
    argv = record["argv"]
    assert argv == _alternate_argv(rig, "-c")
    settings = json.loads(argv[argv.index("--settings") + 1])
    assert str(rig.global_md) in settings["claudeMdExcludes"]
    deny = settings["permissions"]["deny"]
    for tool in ("Edit", "Write", "NotebookEdit"):
        assert f"{tool}(/{os.path.realpath(rig.core_dir)}/**)" in deny
    served = Path(argv[argv.index("--add-dir") + 1])
    assert sorted(p.name for p in served.iterdir()) == ["CLAUDE.md"]  # nothing else is exposed
    assert (served / "CLAUDE.md").read_bytes() == rig.core_src.read_bytes()
    assert record["global_md"] == before[0].decode()    # during the session: untouched
    rig.release()
    proc.communicate(timeout=20)
    assert _file_state(rig.global_md) == before          # after it: same bytes, inode, mtime


def test_pact_after_repact_sees_the_global_file_byte_identical(rig):
    rig.seed()
    before = _file_state(rig.global_md)
    assert rig.run("-c").returncode == 0
    rig.log.unlink()
    pact = subprocess.run(["/bin/bash", str(BIN_DIR / "pact"), "-c"], cwd=rig.project,
                          env=rig.env(), capture_output=True, text=True, timeout=60)
    assert pact.returncode == 0, pact.stderr
    record = rig.stub_record()
    assert record["global_md"] == before[0].decode()
    assert "--add-dir" not in record["argv"] and "--settings" not in record["argv"]
    assert record["additional_md"] is None
    assert _file_state(rig.global_md) == before


def test_kill_9_of_the_launcher_leaves_nothing_to_recover(rig):
    rig.seed()
    before = _file_state(rig.global_md)
    proc = rig.start("-c")
    os.kill(proc.pid, signal.SIGKILL)                    # no trap runs
    proc.wait(timeout=20)
    rig.release()
    proc.communicate(timeout=20)
    assert _file_state(rig.global_md) == before          # no swap, so nothing to restore
    rig.log.unlink()
    assert rig.run("-c").returncode != 2                 # the next launch is not refused over the core
    assert _file_state(rig.global_md) == before


def test_a_missing_core_falls_back_to_the_global_file_with_one_warning(rig, tmp_path):
    rig.seed()
    result = rig.run("-c", REPACTED_CORE_SOURCE=str(tmp_path / "absent.md"))
    assert result.returncode == 0
    assert result.stderr.count("\n") == 1 and "slim core not found" in result.stderr
    record = rig.stub_record()
    assert record["argv"] == _alternate_argv(rig, "-c", core=False)
    assert record["additional_md"] is None


def test_no_user_layer_serves_the_plugin_core_beside_the_global_file(rig, tmp_path):
    """A stranger's install has no user-layer/: the plugin's generic core is served, and their
    own global CLAUDE.md is NOT excluded (additive), unlike the user layer's core."""
    spec = importlib.util.spec_from_file_location("_repact_launch", BIN_DIR / "_repact_launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    root = tmp_path / "install" / "plugins" / "pact-plugin"   # root.parent.parent has no user-layer/
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (root / "templates").mkdir()
    shutil.copy(PLUGIN_ROOT / "templates" / "CLAUDE.core.md", root / "templates" / "CLAUDE.core.md")
    env = rig.env(REPACTED_CORE_SOURCE="")                     # no override: the real lookup
    code, out, err = launch.preflight(root, env, rig.project, detect=lambda: [])
    assert code == 0 and err == [], err
    assert out[2] == str(rig.core_dir)
    assert (rig.core_dir / "CLAUDE.md").read_bytes() == (root / "templates" / "CLAUDE.core.md").read_bytes()
    settings = json.loads(out[1])
    assert "claudeMdExcludes" not in settings
    assert settings["permissions"]["deny"] == _core_deny_rules(rig.core_dir)
    assert "still loads beside it" in launch.core_status(root, env)


def test_a_stale_staged_core_is_replaced_and_an_unchanged_one_is_left_alone(rig):
    rig.seed()
    rig.core_dir.mkdir()
    (rig.core_dir / "CLAUDE.md").write_text("# an older core\n", encoding="utf-8")
    assert rig.run().returncode == 0
    staged = rig.core_dir / "CLAUDE.md"
    assert staged.read_bytes() == rig.core_src.read_bytes()
    inode = staged.stat().st_ino
    rig.log.unlink()
    assert rig.run().returncode == 0
    assert staged.stat().st_ino == inode                 # identical content: not rewritten


def test_the_excludes_name_a_symlinked_config_root_both_ways(rig, tmp_path):
    spec = importlib.util.spec_from_file_location("_repact_launch", BIN_DIR / "_repact_launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    link = tmp_path / "config-link"
    link.symlink_to(rig.config)
    excludes = launch.global_memory_excludes(link)
    assert str(link / "CLAUDE.md") in excludes
    assert os.path.realpath(rig.global_md) in excludes


# ---------- REPACT-ALONE: alternate mode runs alone (#253) ----------

def _live_session(rig):
    """A claude process plus session activity on disk in the last 10 minutes."""
    teams = rig.config / "teams" / "session-x"
    teams.mkdir(parents=True)
    (teams / "config.json").write_text("{}", encoding="utf-8")
    return "  4242 /opt/homebrew/bin/claude\n  7 /bin/zsh"


def test_another_live_session_refuses_and_touches_nothing(rig):
    original = rig.seed()
    inodes = rig.link_inodes()
    result = rig.run("-c", STUB_PS=_live_session(rig))
    assert result.returncode == 2 and "1 other Claude session" in result.stderr
    assert "close other Claude sessions, or pass --force" in result.stderr
    assert rig.stub_record() is None and rig.fingerprint(LEGACY) == original
    assert rig.link_inodes() == inodes


def test_force_proceeds_with_a_warning_and_is_not_passed_to_claude(rig):
    rig.seed()
    result = rig.run("--force", "-c", STUB_PS=_live_session(rig))
    assert result.returncode == 0, result.stderr
    assert "warning" in result.stderr and "--force" in result.stderr
    assert rig.stub_record()["argv"] == _alternate_argv(rig, "-c")


def test_a_second_alternate_repact_refuses_even_with_force(rig):
    rig.seed()
    (rig.config / "repacted-alternate.lock").write_text(str(os.getpid()), encoding="utf-8")
    result = rig.run("--force")
    assert result.returncode == 2 and "another alternate-mode repact" in result.stderr
    assert rig.stub_record() is None


def test_installed_mode_has_no_alone_check_and_two_starts_change_nothing(rig):
    rig.seed()
    rig.write_json(rig.config / "settings.json",
                   {"enabledPlugins": {"repacted@repacted-local": True, "PACT@pact-marketplace": False}})
    inodes = rig.link_inodes()
    for _ in range(2):
        result = rig.run(STUB_PS=_live_session(rig) if not (rig.config / "teams").exists() else
                         "  4242 /opt/homebrew/bin/claude")
        assert result.returncode == 0 and result.stderr == "", result.stderr
    assert rig.names() == [LEGACY] and rig.link_inodes() == inodes
    assert not (rig.config / "repacted-alternate.lock").exists()


# ---------- the front door: work and resume launch through this path ----------

def _installed(rig):
    rig.write_json(rig.config / "settings.json",
                   {"enabledPlugins": {"repacted@repacted-local": True, "PACT@pact-marketplace": False}})


def test_bare_repact_still_launches_a_session(rig):
    _installed(rig)
    result = rig.run()
    assert result.returncode == 0, result.stderr
    assert rig.stub_record()["argv"] == AGENT


@pytest.mark.parametrize("args, prompt, execution", [
    (["work", "fix the parser", "on empty input"], "fix the parser on empty input", None),
    (["work", "inspect"], "inspect", None),
    (["work"], "/repacted:work", None),
    (["work", "--unattended", "--max-usd", "5", "--until", "18:30"],
     "/repacted:autopilot --until 18:30 --max-usd 5", "subagents"),
], ids=["ask", "ask-that-is-a-route-word", "next-row", "unattended"])
def test_work_launches_a_session_with_the_ask_the_next_row_or_autopilot(rig, args, prompt, execution):
    _installed(rig)
    result = rig.run(*args)
    assert result.returncode == 0, result.stderr
    record = rig.stub_record()
    assert record["argv"] == [*AGENT, prompt]
    assert record["execution"] == execution and record["routed"] is None


def test_work_refuses_inside_a_running_session_and_starts_nothing(rig):
    _installed(rig)
    result = rig.run("work", "fix it", CLAUDECODE="1")
    assert result.returncode == 2 and "inside a Claude Code session" in result.stderr
    assert rig.stub_record() is None


def test_resume_prints_the_screen_then_resumes_that_session(rig):
    _installed(rig)
    folder = rig.config / "pact-sessions" / "project" / "abcd1234-0000-4000-8000-000000000001"
    folder.mkdir(parents=True)
    rig.write_json(folder / "pact-session-context.json", {"team_name": "session-abcd1234",
                                                         "project_dir": str(rig.project)})
    rig.write_json(folder / "workflow-state.json", {"v": 1, "built_at": "2026-10-19T00:00:00+00:00",
                                                     "team": "session-abcd1234", "tasks": {}})
    result = rig.run("resume", "--session-dir", str(folder))
    assert result.returncode == 0, result.stderr
    assert "[RESUME]" in result.stdout
    assert rig.stub_record()["argv"] == [*AGENT, "--resume", folder.name]


# ---------- help and a wrong interpreter ----------

@pytest.mark.parametrize("word", ["--help", "help", "-h"])
def test_help_prints_repacts_own_jobs_and_never_starts_claude(rig, word):
    rig.seed()
    result = rig.run(word)
    assert result.returncode == 0, result.stderr
    for job in ("dispatch", "submit", "accept", "reject", "amend", "inspect", "decide", "resume",
                "work", "repact report", "repact --repact-status", "repact [claude arguments]"):
        assert job in result.stdout, job
    assert rig.stub_record() is None


def test_run_by_python_it_says_how_to_run_it_in_one_line(rig):
    result = subprocess.run([sys.executable, str(REPACT), "dispatch"], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 1 and result.stdout == ""
    assert result.stderr.strip().splitlines() == [
        'repact is a bash script: run it as "<plugin root>/bin/repact" <job>, not with python3 '
        "(repact --help lists the jobs)"]
