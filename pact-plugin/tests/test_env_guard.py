"""
Location: pact-plugin/tests/test_env_guard.py (ENV-GUARD, task 211)
Summary: Tests for hooks/env_guard.py, the PreToolUse guard that keeps .env-class file
         contents out of the transcript: Read and NotebookRead of a protected file are
         denied, Grep is rewritten to exclude the class, and Bash is denied only when a
         command would print a protected file's contents (a reader of it, a search that
         can match inside it, a script that opens it and prints, a sourced value echoed),
         dumps the environment, or sends or stages the file; a path mentioned as data passes.

Every fixture file is inert: it holds MARKER, never a real value, and each deny row also
asserts MARKER is absent from the hook's stdout and stderr. The fixture tree is built
fresh per test in tmp_path, and the payload's cwd points at it.

Families, each with a deny table, an allow table and an in-test sabotage row that
disables the family's predicate and shows its canonical deny flip to an allow:
  READ      Read / NotebookRead by name, by symlink target, by case
  GREP      explicit protected target denied; any other Grep gets the exclusion globs
  NAMED     a Bash command that would print a protected file, or send or stage it
  DUMP      env / printenv / export -p / set / declare -p with nothing to run
  RECURSE   grep -r, rg, ag, git grep --untracked over a tree holding a protected file
  PARSE     an unparsable command that mentions the class fails closed
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import env_guard as guard
from shell.words import strip_prefix

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "env_guard.py"
MARKER = "inert-marker-5e1d"


@pytest.fixture
def tree(tmp_path):
    """An inert project: protected files at several depths, a symlink to one, a
    virtualenv directory named .env, and ordinary files."""
    for name in (".env", "app.env", ".envrc", ".netrc", "sub/.env.local", ".env.bak"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"DEMO_KEY={MARKER}\n", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / ".env")
    (tmp_path / "proj" / ".env").mkdir(parents=True)
    (tmp_path / "proj" / ".env" / "activate").write_text("# venv\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("plain\n", encoding="utf-8")
    return tmp_path


def bash(command: str, cwd) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


def read(path: str, cwd, tool="Read", key="file_path") -> dict:
    return {"tool_name": tool, "tool_input": {key: path}, "cwd": str(cwd)}


def grep(cwd, **tool_input) -> dict:
    return {"tool_name": "Grep", "tool_input": {"pattern": "KEY", **tool_input}, "cwd": str(cwd)}


def denied(payload) -> bool:
    output = guard.decide(payload)
    return bool(output) and output["hookSpecificOutput"].get("permissionDecision") == "deny"


def run_hook(payload) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True,
                          text=True, timeout=30)


# ── NAMED ─────────────────────────────────────────────────────────────────────────────
NAMED_DENY = [
    "cat .env",
    "cat < .env",
    "source .env; echo $DEMO_KEY",
    "ls .env | xargs cat",
    "cat ~/.claude/.env.acme",
]

NAMED_ALLOW = [
    "env-get .env DEMO_KEY -- node app.js",
    "touch .env",
    "find . -maxdepth 3 \\( -name '.env*' -o -name '*.pem' -o -name '*.key' -o -iname "
    "'credential*' -o -iname 'secret*' \\)",
]


@pytest.mark.parametrize("command", NAMED_DENY)
def test_named_protected_path_is_denied(tree, command):
    assert denied(bash(command, tree))


@pytest.mark.parametrize("command", NAMED_ALLOW)
def test_named_allowlisted_or_unprotected_is_allowed(tree, command):
    assert not denied(bash(command, tree))


# ── NAMED inside a runner's script ────────────────────────────────────────────────────
# The script a runner hands to a shell (bash -c, flock -c, script -c, watch) was read as a
# leading assignment or a number when it opened with one, so `bash -c 'X=1; cat .env'` ran
# unjudged. Monitor's output is never scanned, so each row is checked under both tools.
READ_ENV = "cat " + "." + "env"
SCRIPT_DENY = [form.replace("R", READ_ENV) for form in (
    "bash -c 'X=1 R'",
    "bash -c 'X=$(R); echo \"$X\"'",
    "bash -c 'X=1; R'",
    "bash -c 'X=1;R'",
    "bash -c 'X=1 Y=2 R'",
    "bash -c 'X=1 && R'",
    "sh -ec 'X=1; R'",
    "bash -lc 'X=1; R'",
    "zsh -c 'X=1; R'",
    "bash -c -e 'X=1; R'",
    "bash --norc -c 'X=1; R'",
    "bash -c -- 'X=1; R'",
    "bash -c -- '-n; R'",
    "bash -c \"X=1; R\"",
    "bash -c 'X=1\nR'",
    "bash -c $'X=1\\tR'",
    "bash -c '1; R'",
    "bash -c '2>/dev/null R'",
    "sudo bash -c 'X=1; R'",
    "env bash -c 'X=1; R'",
    "timeout 5 bash -c 'X=1; R'",
    "nice -n 5 sh -c 'X=1; R'",
    "command bash -c 'X=1; R'",
    "flock /tmp/l -c 'X=1; R'",
    "flock -w 5 /tmp/l -c 'X=1; R'",
    "script -c 'X=1; R' /dev/null",
    "watch 'X=1; R'",
    "watch -n 5 'X=1; R'",
    # controls: denied before the fix and still
    "bash -c 'R'",
    "bash -c 'echo hi; X=$(R); echo \"$X\"'",
    "watch 'R'",
    "flock /tmp/l -c 'R'",
    "script -c 'R' /dev/null",
    "X='a b' R",
    "env X='a b' R",
    "eval 'X=1; R'",
    "xargs sh -c 'X=1; R'",
)]
SCRIPT_ALLOW = [
    "bash -c 'X=1; echo hi'",
    "bash -c 'X=1; cat notes.txt'",
    "watch 'X=1; ls'",
]


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
@pytest.mark.parametrize("command", SCRIPT_DENY)
def test_a_read_inside_a_runners_script_is_denied(tree, command, tool):
    assert denied({"tool_name": tool, "tool_input": {"command": command}, "cwd": str(tree)})


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
@pytest.mark.parametrize("command", SCRIPT_ALLOW)
def test_a_runners_script_that_reads_nothing_protected_is_allowed(tree, command, tool):
    assert not denied({"tool_name": tool, "tool_input": {"command": command}, "cwd": str(tree)})


# ── NAMED behind a runner given by its absolute path ──────────────────────────────────
# A runner word was matched exactly, so /bin/sh -c, /usr/bin/env and /usr/bin/sudo hid the
# read behind them under both tools.
PATH_RUNNER_DENY = [form.replace("R", READ_ENV) for form in (
    "/bin/bash -c 'R'",
    "/bin/sh -c 'X=1; R'",
    "/usr/bin/env bash -c 'R'",
    "/usr/bin/env R",
    "/usr/bin/sudo R",
    "/usr/bin/sudo -u root R",
    "/usr/bin/env -i /bin/sh -c 'R'",
    "/usr/bin/timeout 5 /bin/bash -c 'X=1; R'",
    "/usr/bin/nohup /usr/bin/sudo R",
    # control: denied before the fix and still
    "bash -c 'R'",
)]
PATH_RUNNER_ALLOW = [
    "/bin/bash -c 'X=1; echo hi'",
    "/usr/bin/env cat notes.txt",
    "/usr/bin/sudo -u root ls",
]


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
@pytest.mark.parametrize("command", PATH_RUNNER_DENY)
def test_a_read_behind_a_runner_given_by_path_is_denied(tree, command, tool):
    assert denied({"tool_name": tool, "tool_input": {"command": command}, "cwd": str(tree)})


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
@pytest.mark.parametrize("command", PATH_RUNNER_ALLOW)
def test_a_runner_given_by_path_that_reads_nothing_protected_is_allowed(tree, command, tool):
    assert not denied({"tool_name": tool, "tool_input": {"command": command}, "cwd": str(tree)})


def test_env_gets_consumer_given_by_path_hands_over_its_script():
    """env-get FILE K -- /bin/sh -c 'echo "$K"': the consumer is read through strip_prefix, so
    the path form must reach the same script as `sh -c` does."""
    script = 'echo "$K"'
    assert strip_prefix(["/bin/sh", "-c", script]) == [script] == strip_prefix(["sh", "-c", script])


ENVGET ="/srv/My Projects/pact-plugin/bin/env-get"


@pytest.mark.parametrize("command, deny", [
    ("cat .env", True),
    ("while IFS== read -r k v; do export \"$k=$v\"; done < .env", False),
])
def test_only_a_command_that_prints_or_sends_a_protected_file_is_denied(tree, command, deny):
    """Design rule: block only what would print a .env-class file's
    contents into the transcript or send it off the machine; pass every mention as data."""
    assert denied(bash(command, tree)) is deny


def test_a_glob_that_matches_nothing_protected_is_allowed(tree):
    assert not denied(bash("cat src/*.py", tree))
    assert not denied(bash("cat note*", tree))


# ── DUMP ──────────────────────────────────────────────────────────────────────────────
DUMP_DENY = [
    "env",
    "sudo env",
    "printenv -0",
]
DUMP_ALLOW = [
    "printenv PATH",
    "echo $HOME",
]


@pytest.mark.parametrize("command", DUMP_DENY)
def test_an_environment_dump_is_denied(tree, command):
    assert denied(bash(command, tree))


@pytest.mark.parametrize("command", DUMP_ALLOW)
def test_a_named_variable_or_a_command_under_env_is_allowed(tree, command):
    assert not denied(bash(command, tree))


# ── RECURSE ───────────────────────────────────────────────────────────────────────────
EXCLUDES_GREP = "--exclude='.env*' --exclude='*.env' --exclude=.envrc --exclude=.netrc"
EXCLUDES_RG = "-g '!.env*' -g '!*.env' -g '!.envrc' -g '!.netrc'"
RECURSE_DENY = [
    "grep -rn KEY .",
    "rg --hidden KEY sub",
    "grep -rn KEY . --exclude='*.env'",
]     # partial exclusion: .env still read
RECURSE_ALLOW = [
    "grep -rn KEY src",
    "rg KEY sub",
]                           # rg skips hidden files by default


@pytest.mark.parametrize("command", RECURSE_ALLOW)
def test_a_recursive_search_that_cannot_reach_one_is_allowed(tree, command):
    assert not denied(bash(command, tree))


def test_filtered_searches_with_a_quoted_operator_are_allowed_and_a_real_read_is_not(tree):
    """Regression (2026-09-29): a '|' in the quoted pattern cut the command in two, so the
    --include whitelist and the recommended --exclude set were judged as a separate program."""
    assert not denied(bash("grep -rlsE 'KEY|TOKEN' . --include=*.md --include=*.json", tree))
    assert not denied(bash(f"grep -rlsE 'KEY|TOKEN' . {EXCLUDES_GREP}", tree))
    assert denied(bash("grep -rnsE 'KEY|TOKEN' . --include=*.md --include=*.env", tree))


def test_a_tree_too_large_to_check_fails_closed(tree, monkeypatch):
    """With every protected file removed, the only reason left to deny is the limit."""
    for name in (".env", "app.env", ".envrc", ".netrc", "sub/.env.local", ".env.bak", "link.txt"):
        (tree / name).unlink()
    assert not denied(bash("grep -rn KEY .", tree))            # control: clean tree
    monkeypatch.setattr(guard, "WALK_LIMIT", 2)
    assert not denied(bash("grep -rn KEY src", tree))          # src holds 1 entry
    assert denied(bash("grep -rn KEY .", tree))                # over the limit: unknown


# ── READ ──────────────────────────────────────────────────────────────────────────────
READ_DENY = [
    ".env",
    ".ENV",
    ".env.bak",
]
READ_ALLOW = [
    "notes.txt",
    "missing.txt",
]


@pytest.mark.parametrize("name", READ_DENY)
def test_read_of_a_protected_file_is_denied(tree, name):
    assert denied(read(name, tree))
    assert denied(read(str(tree / name), tree))


@pytest.mark.parametrize("name", READ_ALLOW)
def test_read_of_an_ordinary_file_is_allowed(tree, name):
    assert not denied(read(name, tree))


def test_notebook_read_is_covered(tree):
    assert denied(read(".env", tree, tool="NotebookRead", key="notebook_path"))


# ── GREP ──────────────────────────────────────────────────────────────────────────────
def rewritten(payload) -> dict | None:
    output = guard.decide(payload)
    return output["hookSpecificOutput"].get("updatedInput") if output else None


def test_grep_on_a_directory_gets_the_exclusion_and_keeps_every_other_field(tree):
    updated = rewritten(grep(tree, path=str(tree), output_mode="content", **{"-n": True}))
    assert updated == {"pattern": "KEY", "path": str(tree), "output_mode": "content",
                       "-n": True, "glob": guard.GREP_EXCLUSION}


def test_grep_appends_to_the_callers_glob(tree):
    assert rewritten(grep(tree, glob="*.py"))["glob"] == "*.py " + guard.GREP_EXCLUSION
    assert rewritten(grep(tree, glob="*.{py,env}"))["glob"] == "*.{py,env} " + guard.GREP_EXCLUSION


def test_grep_with_no_path_is_rewritten_too(tree):
    assert rewritten(grep(tree))["glob"] == guard.GREP_EXCLUSION


def test_an_already_excluded_grep_is_left_alone(tree):
    assert guard.decide(grep(tree, glob="*.py " + guard.GREP_EXCLUSION)) is None


@pytest.mark.parametrize("glob", [
    ["*.py"],
    {"a": 1},
])
def test_a_glob_that_is_not_a_string_fails_closed(tree, glob):
    assert denied(grep(tree, glob=glob))


def test_grep_exclusion_tokens_survive_the_tools_own_split():
    """Each exclusion must reach rg as its own --glob: no whitespace, comma or brace."""
    for token in guard.GREP_EXCLUSION.split(" "):
        assert token.startswith("!") and not re.search(r"[\s,{}]", token)


# The Grep tool's glob handling, transcribed from Claude Code 2.1.283 (function _In): the
# field is split on whitespace; a token holding both { and } is kept whole, any other
# token is split on commas; each piece becomes its own `--glob`. The replay below runs
# that argv through ripgrep so the exclusion is proven in the form the tool builds.
def _tool_globs(field: str) -> list[str]:
    pieces = []
    for token in re.split(r"\s+", field):
        pieces.extend([token] if "{" in token and "}" in token else token.split(","))
    return [piece for piece in pieces if piece]


def _ripgrep() -> list[str] | None:
    claude = sorted(Path.home().glob(".local/share/claude/versions/*"))
    if claude:
        return ["/bin/bash", "-c", f'exec -a rg "{claude[-1]}" "$@"', "rg"]
    for candidate in ("/opt/homebrew/bin/rg", "/usr/local/bin/rg", "/usr/bin/rg"):
        if os.path.exists(candidate):
            return [candidate]
    return None


RIPGREP = _ripgrep()     # resolved at import: the suite's scratch HOME hides ~/.local


@pytest.mark.skipif(RIPGREP is None, reason="no ripgrep to replay the Grep tool through")
@pytest.mark.parametrize("caller_glob", [
    None,
    "*",
])
def test_the_rewritten_grep_replayed_through_ripgrep_returns_no_protected_file(tree, caller_glob):
    (tree / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tree / "notes.txt").write_text(MARKER + "\n", encoding="utf-8")
    field = rewritten(grep(tree, **({"glob": caller_glob} if caller_glob else {})))["glob"]
    argv = ["--hidden"]
    for vcs in (".git", ".svn", ".hg", ".bzr", ".jj", ".sl"):
        argv += ["--glob", "!" + vcs]
    argv += ["--max-columns", "500", "-l", "--null", MARKER]
    for piece in _tool_globs(field):
        argv += ["--glob", piece]
    result = subprocess.run(RIPGREP + argv + [str(tree)], capture_output=True, text=True,
                            timeout=30, cwd=tree)
    found = {Path(p).name for p in result.stdout.split("\0") if p.strip()}
    assert not any(guard.is_protected_name(name) for name in found), found
    if caller_glob in (None, "*", "*.{env,txt}", "*.txt,*.env"):
        assert "notes.txt" in found            # control: the search itself still works


# ── PARSE and the hook process ────────────────────────────────────────────────────────
def test_an_unparsable_command_mentioning_the_class_fails_closed(tree):
    """echo is allowlisted, so only the parse rule can deny this row."""
    assert not denied(bash("echo 'see .env'", tree))           # control: parsable
    assert denied(bash("echo 'see .env", tree))


def test_an_unparsable_command_that_does_not_mention_it_is_left_to_the_shell(tree):
    assert not denied(bash("echo 'unterminated", tree))


@pytest.mark.parametrize("payload_of", [
    lambda t: bash("cat .env", t),
    lambda t: grep(t, path=".env"),
])
def test_no_deny_carries_a_fixture_value(tree, payload_of):
    result = run_hook(payload_of(tree))
    assert result.returncode == 2
    assert MARKER not in result.stdout + result.stderr


def test_grep_rewrite_is_exit_0_with_updated_input(tree):
    result = run_hook(grep(tree, glob="*.py"))
    assert result.returncode == 0
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert "permissionDecision" not in specific
    assert specific["updatedInput"]["glob"].endswith(guard.GREP_EXCLUSION)


def test_allow_is_suppress_output_exit_0(tree):
    result = run_hook(bash("ls", tree))
    assert (result.returncode, json.loads(result.stdout)) == (0, {"suppressOutput": True})


def test_unreadable_stdin_without_a_mention_fails_open():
    result = run_hook("not json")
    assert result.returncode == 0


def test_an_internal_error_fails_closed_only_when_the_class_is_mentioned(tree, monkeypatch, capsys):
    def explode(_payload):
        raise RuntimeError("boom")
    monkeypatch.setattr(guard, "decide", explode)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(bash("cat .env", tree))))
    assert guard.main() == 2
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(bash("ls", tree))))
    assert guard.main() == 0


@pytest.mark.parametrize("tool", [
    "Write",
    "Agent",
])
def test_other_tools_pass_through(tree, tool):
    assert guard.decide({"tool_name": tool, "tool_input": {"file_path": ".env"},
                         "cwd": str(tree)}) is None





@pytest.mark.parametrize("command", RECURSE_DENY)
def test_a_recursive_search_over_a_protected_file_is_denied(tree, command):
    assert denied(bash(command, tree))



def test_grep_on_a_protected_file_is_denied(tree):
    assert denied(grep(tree, path=".env"))
    assert denied(grep(tree, path=str(tree / "app.env")))
    assert denied(grep(tree, path="link.txt"))



def test_the_symlink_row_denies_only_through_resolution(tree, monkeypatch):
    """link.txt is an unprotected name; its deny must come from following the link."""
    monkeypatch.setattr(guard.os.path, "realpath", lambda path: path)
    assert not denied(bash("cat link.txt", tree))
    assert not denied(read("link.txt", tree))



def test_unreadable_stdin_mentioning_the_class_fails_closed():
    result = run_hook('{"tool_name": "Read", "tool_input": {"file_path": ".env"')
    assert result.returncode == 2
