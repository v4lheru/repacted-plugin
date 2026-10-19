"""tests/test_settings_guard.py

hooks/settings_guard.py: an agent may not write Claude Code settings or the running plugin's
hooks (settings.write). The path rows are the bypass spellings shell/settings.py was built
against (each must refuse) and the neighbours that must still pass; the frame rows pin who is
refused and who is asked: any agent marker denies, the lead or a session with no marker is
asked, and unattended mode turns the ask into a deny. The script rows prove the wiring in
worktree_guard (before its PACT_WORKTREE_PATH early exit) and env_guard (Bash). Every path
lives under tmp_path.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import settings_guard as guard

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
ROOT = HOOKS.parent
LEAD = "repacted:pact-orchestrator"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / ".claude" / "agents").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("REPACT_UNATTENDED", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return home


def _bash(command: str, cwd: Path, **frame) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd), **frame}


def _decision(output: dict | None) -> str | None:
    return output["hookSpecificOutput"]["permissionDecision"] if output else None


def _bypass_rows(home: Path) -> list[str]:
    h = str(home)
    return [
        "echo '{}' > ~/.claude/settings.json",
        "echo x > $HOME/.claude/settings.json",
        "echo x > ${CLAUDE_CONFIG_DIR}/settings.json",
        "cd ~/.claude && echo x > settings.json",
        "tee ~/.claude/Settings.JSON < /dev/null",
        "sed -i '' s/a/b/ ~/.claude/settings.json",
        "python3 -c \"open('%s/.claude/settings.json','w')\"" % h,
        "env X=1 tee .claude/settings.local.json",
        "command tee ~/.claude/settings.json",
        "echo $(tee ~/.claude/settings.json)",
        "bash <<EOF\necho x > ~/.claude/settings.json\nEOF",
        "bash -c 'echo x > ~/.claude/settings.json'",
        "eval 'echo x > ~/.claude/settings.json'",
        "echo x > ~/.claude/{settings,foo}.json",
        "echo x > ~/.claude/settings*.json",
        "rm -rf ~/.claude",
        "echo x >> '%s/hooks/hooks.json'" % ROOT,
        "cp /tmp/settings.json ~/.claude/",
        "cp -t ~/.claude /tmp/settings.json",
        "python3 <<'EOF'\nopen('%s/.claude/settings.json','w')\nEOF" % h,
        "mv ~/.claude/settings.json /tmp/s",
        "ln -sf /tmp/evil ~/.claude/settings.json",
        "ln ~/.claude/settings.json /tmp/h",                   # a hard link
        "ln -s ~/.claude/settings.json /tmp/l && echo x > /tmp/l",
        "ruff format '%s/hooks'" % ROOT,                        # ruff rewrites; not a reader
    ]


NEIGHBOURS = [
    "cat ~/.claude/settings.json",
    "jq . ~/.claude/settings.json",
    "jq . ~/.claude/settings.json > /tmp/out.json",
    "echo x > .claude/agents/x.md",
    "cp ~/.claude/settings.json /tmp/backup.json",
    "cp /tmp/x ~/.claude/",                  # lands as ~/.claude/x, not a settings file
    "python3 '%s/hooks/shared/session_registry.py' register --name a@b" % ROOT,
    "cat > /tmp/x <<EOF\n~/.claude/settings.json\nEOF",
    "echo x > /tmp/elsewhere/pact-plugin/hooks/x.py",
    "bash -c 'cat ~/.claude/settings.json'",
    "git status",
    "pytest '%s/hooks'" % ROOT,                          # test and lint readers
    "mypy '%s/hooks'" % ROOT,
]


def test_every_bypass_spelling_is_refused_to_an_agent(home):
    for command in _bypass_rows(home):
        output = guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore"))
        assert _decision(output) == "deny", command
        assert output["hookSpecificOutput"]["permissionDecisionReason"].endswith(
            "[rule: settings.write]"), command


@pytest.mark.parametrize("command", NEIGHBOURS)
def test_the_neighbours_still_pass(home, command):
    assert guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore")) is None


def test_repact_init_yes_passes(home, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT))
    command = 'python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-init" --yes'
    assert guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore")) is None


def test_an_unreadable_command_is_refused_only_when_it_mentions_a_protected_word(home):
    agent = {"agent_id": "a1b2c3", "agent_type": "Explore"}
    assert _decision(guard.decide(_bash("echo 'x > ~/.claude/settings.json", home, **agent))) == "deny"
    assert guard.decide(_bash("echo 'unterminated", home, **agent)) is None


@pytest.mark.parametrize("tool,key,relative", [
    ("Write", "file_path", ".claude/settings.json"),
    ("Edit", "file_path", ".claude/settings.json"),
    ("MultiEdit", "file_path", ".claude/settings.json"),
    ("Write", "file_path", "proj/.claude/settings.local.json"),
    ("NotebookEdit", "notebook_path", ".claude/Settings.Local.JSON"),
])
def test_a_file_tool_write_of_settings_is_refused_to_an_agent(home, tool, key, relative):
    payload = {"tool_name": tool, "tool_input": {key: str(home / relative)}, "cwd": str(home),
               "agent_id": "a1b2c3", "agent_type": "general-purpose"}
    assert _decision(guard.decide(payload)) == "deny"


def test_a_file_tool_write_of_the_running_hooks_is_refused(home):
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(HOOKS / "hooks.json")},
               "cwd": str(home), "agent_id": "a1b2c3"}
    assert _decision(guard.decide(payload)) == "deny"


@pytest.mark.parametrize("path", [".claude/agents/x.md", ".claude/settings.json.bak",
                                  "proj/settings.json", "elsewhere/pact-plugin/hooks/x.py"])
def test_a_file_tool_write_beside_settings_passes(home, path):
    payload = {"tool_name": "Write", "tool_input": {"file_path": str(home / path)},
               "cwd": str(home), "agent_id": "a1b2c3"}
    assert guard.decide(payload) is None


# --- the frame: who is refused and who is asked -------------------------------------------

WRITE = "echo x > ~/.claude/settings.json"


@pytest.mark.parametrize("frame", [
    {"agent_id": "a1b2c3", "agent_type": "Explore"},             # a subagent
    {"agent_id": "aguards3-0123456789abcdef", "agent_type": "guards3"},   # an in-process teammate
    {"agent_id": "a1b2c3"},                                        # an id alone
    {"agent_id": "a1b2c3", "agent_type": LEAD},                    # a lead spelling with an id
    {"agent_type": "general-purpose"},                             # a type that is not the lead
    {"agent_type": ["not", "a", "string"]},
])
def test_any_agent_marker_is_denied(home, frame):
    assert _decision(guard.decide(_bash(WRITE, home, **frame))) == "deny"


@pytest.mark.parametrize("frame", [{}, {"agent_type": LEAD}, {"agent_type": "pact-orchestrator"}])
def test_the_lead_or_a_session_with_no_marker_is_asked(home, frame):
    output = guard.decide(_bash(WRITE, home, **frame))
    assert _decision(output) == "ask"
    assert "/repacted:init" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_registry_named_teammate_is_denied(home, monkeypatch):
    from shared import actor

    monkeypatch.setattr(actor, "resolve_actor", lambda payload, team=None: actor.Actor(
        agent_id="", name="guards3", source="registry", is_lead=False, team="t"))
    assert _decision(guard.decide(_bash(WRITE, home))) == "deny"


@pytest.mark.parametrize("frame", [{}, {"agent_type": LEAD}])
def test_unattended_mode_turns_the_ask_into_a_deny(home, monkeypatch, frame):
    monkeypatch.setenv("REPACT_UNATTENDED", "1")
    assert _decision(guard.decide(_bash(WRITE, home, **frame))) == "deny"


def test_the_unattended_flag_file_turns_the_ask_into_a_deny(home, monkeypatch, tmp_path):
    from shared import pact_context

    session_dir = tmp_path / "session"
    session_dir.mkdir()
    (session_dir / "unattended.json").write_text('{"unattended": true}', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(pact_context, "reconstruct_session_dir", lambda p, s: str(session_dir))
    assert _decision(guard.decide(_bash(WRITE, home, session_id="s1"))) == "deny"


def test_a_frame_that_cannot_be_read_is_denied(home, monkeypatch):
    def boom(payload, team=None):
        raise RuntimeError("unreadable frame")

    from shared import actor

    monkeypatch.setattr(actor, "resolve_actor", boom)
    assert _decision(guard.decide(_bash(WRITE, home))) == "deny"


# --- the wiring ---------------------------------------------------------------------------

def _run(script: str, payload: dict, home: Path, **env) -> subprocess.CompletedProcess:
    environ = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_CONFIG_DIR", "REPACT_UNATTENDED", "PACT_WORKTREE_PATH",
                            "CLAUDE_PROJECT_DIR")}
    environ.update(HOME=str(home), **env)
    return subprocess.run([sys.executable, str(HOOKS / script)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=60, env=environ, cwd=str(HOOKS))


@pytest.mark.parametrize("worktree", [None, "wt"])
def test_worktree_guard_refuses_before_its_worktree_exit(home, tmp_path, worktree):
    env = {"PACT_WORKTREE_PATH": str(tmp_path / worktree)} if worktree else {}
    payload = {"tool_name": "Write", "cwd": str(home), "agent_id": "a1b2c3",
               "tool_input": {"file_path": str(home / ".claude" / "settings.json")}}
    run = _run("worktree_guard.py", payload, home, **env)
    assert run.returncode == 2
    assert json.loads(run.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
    del payload["agent_id"]
    run = _run("worktree_guard.py", payload, home, **env)
    assert run.returncode == 0
    assert json.loads(run.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_env_guard_refuses_a_bash_write_and_asks_the_lead(home):
    run = _run("env_guard.py", _bash(WRITE, home, agent_id="a1b2c3"), home)
    assert run.returncode == 2 and "[rule: settings.write]" in run.stdout
    run = _run("env_guard.py", _bash(WRITE, home, agent_type=LEAD), home)
    assert run.returncode == 0
    assert json.loads(run.stdout)["hookSpecificOutput"]["permissionDecision"] == "ask"
    run = _run("env_guard.py", _bash("cat ~/.claude/settings.json", home, agent_id="a1"), home)
    assert run.returncode == 0 and "permissionDecision" not in run.stdout


def test_one_call_reaches_the_settings_check_through_one_registry_entry():
    """A Bash call meets the check only in env_guard and a file write only in worktree_guard,
    so one call yields at most one ask from it."""
    from dispatch_registry import REGISTRY

    callers = {"env_guard", "worktree_guard"}
    for check in REGISTRY["PreToolUse"]:
        source = HOOKS / f"{check.name}.py"
        if check.name not in callers and source.exists():
            assert "settings_guard" not in source.read_text(encoding="utf-8"), check.name
    matchers = {c.name: c.matcher for c in REGISTRY["PreToolUse"] if c.name in callers}
    for tool in ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        hits = [name for name, m in matchers.items() if re.fullmatch(m, tool)]
        assert len(hits) == 1, (tool, hits)


# --- `..` after a symlink, and the plugin loader state and gate inputs ---

def _linked_protocols(home: Path) -> Path:
    """The install's own symlink: ~/.claude/protocols/pact-plugin -> <root>/protocols."""
    link = home / ".claude" / "protocols" / "pact-plugin"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(ROOT / "protocols", target_is_directory=True)
    return link


def test_dot_dot_after_a_symlink_reaches_the_running_hooks(home):
    link = _linked_protocols(home)
    through = f"{link}/../hooks/env_guard.py"
    assert os.path.samefile(through, HOOKS / "env_guard.py")
    agent = {"agent_id": "a1b2c3", "agent_type": "Explore"}
    for command in (f"echo x >> {through}", f"cp /tmp/x {through}",
                    "echo x >> ~/.claude/protocols/pact-plugin/../hooks/env_guard.py"):
        assert _decision(guard.decide(_bash(command, home, **agent))) == "deny", command
    payload = {"tool_name": "Write", "tool_input": {"file_path": through}, "cwd": str(home), **agent}
    assert _decision(guard.decide(payload)) == "deny"
    # The same symlink to read a protocol file, or to run session_registry.py, still passes.
    assert guard.decide(_bash(f"cat {link}/algedonic.md", home, **agent)) is None
    assert guard.decide(_bash(f"python3 {link}/../hooks/shared/session_registry.py register "
                              "--name a@b", home, **agent)) is None


def _loader_rows(home: Path) -> list[str]:
    return [
        "echo x > ~/.claude/plugins/installed_plugins.json",
        "echo x > ~/.claude/plugins/known_marketplaces.json",
        "cd ~/.claude/plugins && echo x > installed_plugins.json",
        "echo '{}' >> ~/.claude/plugins/data/repacted-x/runs/ledger.jsonl",
        "tee ~/.claude/plugins/data/repacted-x/checks/gates/t/1.json < /dev/null",
        "rm -rf ~/.claude/plugins",
        "echo x > '%s/bin/checks.json'" % ROOT,
        'echo x > "$CLAUDE_PLUGIN_ROOT/bin/checks.json"',
        "cd '%s/bin' && sed -i '' s/a/b/ checks.json" % ROOT,
    ]


def test_plugin_loader_state_and_gate_inputs_are_refused_to_an_agent(home, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(ROOT))
    for command in _loader_rows(home):
        output = guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore"))
        assert _decision(output) == "deny", command
        assert _decision(guard.decide(_bash(command, home, agent_type=LEAD))) == "ask", command
    for path in (home / ".claude/plugins/installed_plugins.json",
                 home / ".claude/plugins/data/repacted-x/runs/ledger.jsonl", ROOT / "bin/checks.json"):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(path)}, "cwd": str(home),
                   "agent_id": "a1b2c3"}
        assert _decision(guard.decide(payload)) == "deny", path


@pytest.mark.parametrize("command", [
    "cat ~/.claude/plugins/installed_plugins.json",
    "jq . ~/.claude/plugins/data/repacted-x/runs/ledger.jsonl",
    "jq .metadata ~/.claude/plugins/data/repacted-x/checks/gates/t/1.json > /tmp/out.json",
    "cat '%s/bin/checks.json'" % ROOT,
    "nice -n 19 tcount python3 -m pytest -q tests/test_x.py",
    "echo x > ~/.claude/plugins/marketplaces/notes.txt",
])
def test_reads_of_plugin_state_and_tcount_still_pass(home, command):
    assert guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore")) is None


def test_tcount_still_records_a_run_in_the_protected_data_dir(home, tmp_path):
    """tcount writes the ledger from its own process, which the Bash scan never sees."""
    data = home / ".claude" / "plugins" / "data" / "repacted-x"
    command = f"{sys.executable} -c pass"
    assert guard.decide(_bash(f"tcount {command}", home, agent_id="a1b2c3")) is None
    environ = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR",)}
    environ.update(HOME=str(home), CLAUDE_PLUGIN_DATA=str(data))
    subprocess.run([sys.executable, str(ROOT / "bin" / "tcount"), sys.executable, "-c", "pass"],
                   capture_output=True, text=True, timeout=60, env=environ, cwd=str(tmp_path))
    rows = (data / "runs" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert rows and json.loads(rows[-1])["exit_code"] == 0


def test_the_user_config_with_mcp_servers_is_refused_to_an_agent(home, tmp_path, monkeypatch):
    """~/.claude.json holds user-scope mcpServers, a command the next session runs."""
    (home / ".claude.json").write_text("{}\n", encoding="utf-8")
    (home / "proj").mkdir()
    (home / "linked").symlink_to(home / ".claude.json")
    agent = {"agent_id": "a1b2c3", "agent_type": "Explore"}
    rows = ["echo x > ~/.claude.json", "echo x > $HOME/.claude.json", "tee ${HOME}/.claude.json",
            "echo x > ~/proj/../.claude.json", "cd ~/proj && echo x > ../.claude.json",
            "echo x > ~/linked", "jq . ~/.claude.json > ~/.claude.json",
            "python3 -c \"open('%s/.claude.json','w')\"" % home, "mv /tmp/x ~/.claude.json",
            "sed -i '' s/a/b/ ~/.CLAUDE.json"]
    for command in rows:
        assert _decision(guard.decide(_bash(command, home, **agent))) == "deny", command
        assert _decision(guard.decide(_bash(command, home, agent_type=LEAD))) == "ask", command
    for path in (home / ".claude.json", home / "proj" / ".." / ".claude.json", home / "linked"):
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(path)},
                   "cwd": str(home), **agent}
        assert _decision(guard.decide(payload)) == "deny", path
    for command in ("cat ~/.claude.json", "jq .mcpServers ~/.claude.json",
                    "echo x > ~/.claude.json.bak", "echo x > ~/proj/.claude.json"):
        assert guard.decide(_bash(command, home, **agent)) is None, command
    # With CLAUDE_CONFIG_DIR set, Claude Code reads the file from that dir.
    config = tmp_path / "cfg"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    for command in (f"echo x > {config}/.claude.json", "echo x > $CLAUDE_CONFIG_DIR/.claude.json"):
        assert _decision(guard.decide(_bash(command, home, **agent))) == "deny", command


# --- glob spellings of a protected path ------------------------------------

def _glob_rows(home: Path) -> list[str]:
    return [
        "sed -i '' s/a/b/ ~/.c[l]aude.json",
        "sed -i '' s/a/b/ ~/.cl*.json",
        "cd ~ && sed -i '' s/a/b/ .cl?ude.json",
        "cd ~ && perl -pi -e s/a/b/ .c*.json",
        "sed -i '' s/a/b/ $HOME/.cl*.json",
        'sed -i "" s/a/b/ "$HOME/.cl*.json"',             # quoted: judged like a bare glob
        "echo x > ~/.{c,d}l*.json",                        # braces, then a glob
        "cd proj && sed -i '' s/a/b/ .cl*/s*.json",        # project settings
        "echo x > ~/.c*/s[e]ttings.json",
        "rm -rf ~/.c[l]aude",                             # a remover naming the config dir
        "sed -i '' s/a/b/ $UNKNOWN/.cl*.json",             # a variable the hook cannot spell
        "cp /tmp/x \"%s\"/h*/s*_guard.py" % ROOT,          # the running hooks
    ]


def test_a_glob_spelling_of_a_protected_path_is_refused_to_an_agent(home):
    """The trigger-word filter ran before glob expansion, so these passed."""
    (home / ".claude.json").write_text("{}\n", encoding="utf-8")
    (home / "proj" / ".claude").mkdir(parents=True)
    (home / "proj" / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")
    for command in _glob_rows(home):
        output = guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore"))
        assert _decision(output) == "deny", command
        assert _decision(guard.decide(_bash(command, home, agent_type=LEAD))) == "ask", command


def test_a_glob_with_no_match_or_over_the_bound_is_judged_by_what_it_could_match(home, monkeypatch):
    """No file on disk yet (or a cwd the guard got wrong), or too many entries to walk: the
    pattern is compared with the protected names themselves."""
    from shell import settings

    agent = {"agent_id": "a1b2c3", "agent_type": "Explore"}
    (home / "proj").mkdir()
    for command in ("cd ~ && sed -i '' s/a/b/ .c[l]aude.json", "cd proj && echo x > .cl*/s*.json",
                    "echo x > ~/proj/.c*/settings.loc*.json"):
        assert _decision(guard.decide(_bash(command, home, **agent))) == "deny", command
    monkeypatch.setattr(settings, "MAX_GLOB_ENTRIES", 1)
    for name in ("a", "b", "c"):
        (home / "proj" / name).mkdir()
    assert _decision(guard.decide(_bash("echo x > ~/.*/s*.json", home, **agent))) == "deny"
    assert guard.decide(_bash("echo x > ~/proj/*/notes.txt", home, **agent)) is None


@pytest.mark.parametrize("command", [
    "sed -i '' s/a/b/ ~/proj/*.py",
    "echo x > /tmp/*.log",
    "rm -f ~/proj/*.txt",
    "cd ~ && chmod 644 *.json",                    # a bare * skips dot names, as bash does
    "cat ~/.cl*.json",
    "ls ~/.claude/*.json",
    "cp /tmp/x ~/.claude/agents/*.md",
    "echo x > ~/proj/.claude/agents/[a-z]*.md",
])
def test_a_glob_that_cannot_reach_a_protected_path_passes(home, command):
    (home / ".claude.json").write_text("{}\n", encoding="utf-8")
    (home / "proj").mkdir()
    (home / "proj" / "a.py").write_text("", encoding="utf-8")
    (home / "x.json").write_text("{}\n", encoding="utf-8")
    assert guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore")) is None


# --- a zsh paren group glued to a word is a glob ---------------------------

PAREN_ROWS = [
    "sed -i '' s/a/b/ ~/.cl(a)ude.json",
    "sed -i '' s/a/b/ ~/.cl(a|x)ude.json",
    "sed -i '' s/a/b/ ~/.claude.json(N)",                # a trailing glob qualifier
    "sed -i '' s/a/b/ ~/(.claude|x).json",               # a group at the start of a component
    "sed -i '' s/a/b/ ~/.cl((a))ude.json",               # nested
    "echo x > ~/.cl(a)ude.json",                         # a redirect target
    "cd ~ && perl -pi -e s/a/b/ .claude.(json|x)",
    "sed -i '' s/a/b/ ~/.claude/settings(.json|.x)",
    "sed -i '' s/a/b/ ~/.cl@(a)ude.json",                # ksh-style extglob
    "x=1; sed -i '' s/a/b/ ~/.cl(a)ude.json",
    "sed -i '' $'s/a\\'b/' ~/.cl(a)ude.json",            # $'..' escapes its quote (C3: words reads it)
]


def test_a_paren_glob_spelling_of_a_protected_path_is_refused_to_an_agent(home):
    """The guard read `(` as an operator, so zsh's `~/.cl(a)ude.json` passed."""
    (home / ".claude.json").write_text("{}\n", encoding="utf-8")
    for command in PAREN_ROWS:
        output = guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore"))
        assert _decision(output) == "deny", command


def test_a_paren_glob_with_nothing_on_disk_is_judged_by_the_names(home):
    for command in ("sed -i '' s/a/b/ ~/.cl(a)ude.json", "echo x > ~/.claude/set(t)ings.json"):
        output = guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore"))
        assert _decision(output) == "deny", command


@pytest.mark.parametrize("command", [
    "f() { echo hi; }; f",                                # a function definition
    "arr=(a b c); echo ${arr[1]}",                        # an array
    "(cd /tmp && ls)",                                     # a subshell
    "echo $((1 + 2)) > /tmp/n.txt",                        # arithmetic
    "diff <(ls) <(ls -a)",                                 # process substitution
    "sed -i '' 's/\\(a\\)/b/' ~/proj/x.py",                # quoted parens in a sed script
    "sed -i '' s/a/b/ ~/proj/notes(1).txt",               # a glob that cannot reach a protected path
    "case x in (a|b) echo y;; esac",
    "cat ~/.cl(a)ude.json",                                # a reader
])
def test_parens_that_cannot_reach_a_protected_path_pass(home, command):
    (home / ".claude.json").write_text("{}\n", encoding="utf-8")
    (home / "proj").mkdir()
    assert guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore")) is None


# --- the claude CLI writes the user config and plugin state ----------------

CLI_WRITES = [
    "claude mcp add evil -- sh -c id",
    "claude mcp add -s user evil -- sh -c id",
    "claude mcp add-json evil '{\"command\":\"sh\"}'",
    "claude mcp add-from-claude-desktop",
    "claude mcp remove evil",
    "claude mcp reset-project-choices",
    "claude config set -g foo bar",
    "claude config add allowedTools Bash",
    "claude config remove allowedTools Bash",
    "claude plugin marketplace add evil/repo",
    "claude plugin marketplace remove x",
    "claude plugin install x@y",
    "claude plugin uninstall x",
    "claude plugin enable x",
    "claude plugin disable x",
    "claude plugin update x",
    "claude --debug mcp add evil -- sh",
    "command claude mcp add evil -- sh",
    "/usr/local/bin/claude mcp add evil -- sh",
    "~/.local/bin/claude plugin install x",
    "env FOO=1 claude config set x y",
    "nice -n 19 claude plugin install x",
    "echo evil | xargs claude mcp remove",
    "bash -c 'claude mcp add evil -- sh'",
    "npx @anthropic-ai/claude-code mcp add evil -- sh",
    "npx -y @anthropic-ai/claude-code@1.0.0 plugin install x",
    "pnpm dlx @anthropic-ai/claude-code config set x y",
    "bunx @anthropic-ai/claude-code mcp add evil -- sh",
]

CLI_READS = [
    "claude mcp list",
    "claude mcp get x",
    "claude mcp",
    "claude config get x",
    "claude config list",
    "claude plugin list",
    "claude plugin marketplace list",
    "claude --version",
    "claude -p 'mcp add evil'",
    "claude -p hello",
    "claude --print mcp add",
    "claude mcp add --help",
    "npx @anthropic-ai/claude-code mcp list",
    "echo claude mcp add evil",
    "grep -n 'claude mcp add' notes.md",
    "npx prettier --write src/mcp.ts",
]


def test_a_claude_cli_write_of_the_user_config_or_plugin_state_is_refused_to_an_agent(home):
    """These write ~/.claude.json or the plugin loader files with no path word."""
    for command in CLI_WRITES:
        output = guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore"))
        assert _decision(output) == "deny", command
        assert "claude CLI" in output["hookSpecificOutput"]["permissionDecisionReason"], command
        assert _decision(guard.decide(_bash(command, home, agent_type=LEAD))) == "ask", command


@pytest.mark.parametrize("command", CLI_READS)
def test_a_read_only_claude_cli_call_passes(home, command):
    assert guard.decide(_bash(command, home, agent_id="a1b2c3", agent_type="Explore")) is None
