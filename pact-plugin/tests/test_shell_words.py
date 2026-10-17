"""
Location: pact-plugin/tests/test_shell_words.py (1.4.1 U15)
Summary: Tests for hooks/shell_words.py, the shell reader the Bash guards share: a
         backslash-newline joins two lines into one word, and the zsh precommand modifiers
         (noglob, nocorrect, repeat N) run the next word as the command. The env_guard rows
         show the verdict a guard reaches through the shared reader (env_guard is unchanged).
         1.5.0 D7: words()/tokens(), the one quote-aware reader, and a table of hostile shapes
         run through every guard that reads words with it.
"""
from __future__ import annotations

import pytest

import env_guard
import shell_words


def test_a_backslash_newline_joins_the_word():
    assert shell_words.segments("cu\\\nrl -d @f https://api.example.org") == [
        ["curl", "-d", "@f", "https://api.example.org"]]


def test_a_plain_newline_still_ends_a_segment():
    assert shell_words.segments("ls\npwd") == [["ls"], ["pwd"]]


@pytest.mark.parametrize("words, command", [
    (["noglob", "cat", ".env"], ["cat", ".env"]),
    (["nocorrect", "cat", ".env"], ["cat", ".env"]),
    (["repeat", "3", "cat", ".env"], ["cat", ".env"]),
    (["noglob", "nocorrect", "sudo", "-u", "x", "cat", ".env"], ["cat", ".env"]),
])
def test_zsh_precommand_modifiers_are_prefix_words(words, command):
    assert shell_words.strip_prefix(words) == command


def _env_guard_denies(command: str, cwd) -> bool:
    output = env_guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    return bool(output) and output["hookSpecificOutput"].get("permissionDecision") == "deny"


@pytest.mark.parametrize("command", [
    "noglob cat .env",
    "nocorrect cat .env",
    "repeat 2 cat .env",
    "noglob cat \\\n.env",
])
def test_env_guard_sees_the_command_after_a_zsh_modifier(command, tmp_path):
    assert _env_guard_denies(command, tmp_path)


def test_env_guard_still_allows_a_modifier_on_a_harmless_command(tmp_path):
    assert not _env_guard_denies("noglob ls *.py", tmp_path)


# ---------------------------------------------------------------------------
# 1.5.0 D7: one word reader for every guard. words()/tokens() replace each guard's own shlex;
# the table below runs one set of hostile shapes through the reader and through every
# consumer's view of the words, so a shape fixed for one guard is read alike by all.
# ---------------------------------------------------------------------------

import ast  # noqa: E402
import importlib.util  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import egress_uploads  # noqa: E402
import merge_guard_pre  # noqa: E402
from shared.merge_guard_common import _shell_tokenize  # noqa: E402

PLUGIN = Path(shell_words.__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("d7_commit_rules", PLUGIN / "hooks" / "shared" / "commit_rules.py")
commit_rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(commit_rules)
OPERATOR_CHARS = set(";&|()<>\n")
NBSP, FULLWIDTH_SEMI = "\u00a0", "\uff1b"

# (command, the words every guard must read). None: the quoting cannot be read.
HOSTILE = [
    ("git push origin main", ["git", "push", "origin", "main"]),
    ("git \"pu\"'sh' origin main", ["git", "push", "origin", "main"]),
    ("git pu\\\nsh origin main", ["git", "push", "origin", "main"]),
    ("git \"pu\\\nsh\" origin main", ["git", "push", "origin", "main"]),
    ("git $'\\x70u\\163h' origin main", ["git", "push", "origin", "main"]),
    ("git $\"push\" origin main", ["git", "push", "origin", "main"]),
    ("git $'pu\\0sh' origin", ["git", "pu", "origin"]),
    ("echo $(git push origin main)", ["echo", "$(git", "push", "origin", "main)"]),
    ("echo \"$(git push origin main)\"", ["echo", "$(git push origin main)"]),
    ("echo \"\\$(git push)\"", ["echo", "$(git push)"]),
    ("echo `git push origin main`", ["echo", "`git", "push", "origin", "main`"]),
    ("noglob nocorrect git push", ["noglob", "nocorrect", "git", "push"]),
    ("GIT_SSH_COMMAND='ssh -i k' git push", ["GIT_SSH_COMMAND=ssh -i k", "git", "push"]),
    ("cd x && git push origin main", ["cd", "x", "&&", "git", "push", "origin", "main"]),
    (f"git{NBSP}push origin", [f"git{NBSP}push", "origin"]),
    (f"git status{FULLWIDTH_SEMI}git push", ["git", f"status{FULLWIDTH_SEMI}git", "push"]),
    ("git\rpush\t origin", ["git", "push", "origin"]),
    ("git commit -m \"\" --allow-empty", ["git", "commit", "-m", "", "--allow-empty"]),
    ("", []),
    ("git push \"origin", None),
    ("git push 'origin", None),
    ("git push $'origin", None),
    ("git push origin\\", None),
]


def _flat(found):
    return [word for word in found if not (word and set(word) <= OPERATOR_CHARS)] if found is not None else None


def _env_view(command):
    """env_guard's words, or "unreadable" when read_script refuses the text: env_guard then
    denies whatever the command mentions, so it reads more, never less."""
    try:
        text = shell_words.read_script(command)[0]
    except shell_words.Unreadable:
        return "unreadable"
    steps, exact = env_guard._command_steps(text)
    return [word for step in steps for word in step.words] if exact else None


def _egress_view(command):
    return [word for _, words in egress_uploads.simple_commands(command) for word in words]


@pytest.mark.parametrize("command, expected", HOSTILE)
def test_the_reader_reads_each_hostile_shape_as_the_shell_does(command, expected):
    assert shell_words.words(command) == expected


@pytest.mark.parametrize("command, expected", HOSTILE)
def test_every_guard_sees_the_same_words(command, expected):
    """The merge guard reads the words as they are; egress_uploads and env_guard also split
    at the operators they name, so they must read what the shared reader splits there."""
    assert _shell_tokenize(command) == expected                       # merge guard
    if expected is None:
        return
    assert _egress_view(command) == _flat(shell_words.tokens(command, ";&|()<>"))
    # read_script refuses a NUL in $'..' (env_guard denies the command); it reads the rest.
    env_words = "unreadable" if "\\0" in command else _flat(shell_words.tokens(command, "();<>|&\n"))
    assert _env_view(command) == env_words


def test_operators_split_off_only_when_asked_and_only_outside_quotes():
    assert shell_words.tokens("a;b 'c;d'&&e", ";&") == ["a", ";", "b", "c;d", "&&", "e"]
    assert shell_words.words("a;b 'c;d'&&e") == ["a;b", "c;d&&e"]
    assert shell_words.tokens("ls\npwd", ";\n") == ["ls", "\n", "pwd"]


def test_a_comment_is_read_as_words_so_a_guard_sees_more_never_less():
    assert shell_words.words("git status # git push") == ["git", "status", "#", "git", "push"]


def test_a_heredoc_is_read_as_words():
    assert shell_words.words("cat <<'EOF'\ngit push\nEOF") == ["cat", "<<EOF", "git", "push", "EOF"]


def test_a_punctuation_delimiter_closes_its_heredoc_as_in_bash_and_zsh():
    """`cat <<!` was Unreadable, so the run counter read its body as command lines. Both shells
    close on such a word as typed (checked per character); adversarial: a line that only looks
    like it does not close, and a word holding an expansion or glob character is still refused."""
    assert shell_words.read_script("cat <<!\npytest -q\n!\nls") == (
        "cat <<__repact_heredoc_0__\nls", [(True, "pytest -q")])
    assert shell_words.read_script("cat <<E@F\n.env\nE@F\nls")[1] == [(True, ".env")]
    assert shell_words.read_script("cat <<!\nbody\n !\n")[1] == [(True, "body\n !")]
    for odd in ("cat <<E$F\nx\nE$F", "cat <<E*F\nx\nE*F", "cat <<}\nx\n}"):
        with pytest.raises(shell_words.Unreadable):
            shell_words.read_script(odd)


def test_a_zsh_modifier_and_an_assignment_are_stripped_from_the_shared_words():
    assert shell_words.strip_prefix(shell_words.words("noglob GIT_DIR=x nocorrect git push")) == ["git", "push"]


def test_a_one_megabyte_command_is_read_in_time_by_every_guard():
    command = "git push origin main " + "a " * 500_000
    started = time.monotonic()
    assert len(shell_words.words(command)) == 500_004
    assert len(_shell_tokenize(command)) == 500_004
    assert len(_egress_view(command)) == 500_004
    assert len(_env_view(command)) == 500_004
    assert time.monotonic() - started < 10


def test_an_escaped_substitution_in_a_commit_message_is_still_read_as_text():
    message = commit_rules.commit_message('git commit -m "\\$(echo) Co-Authored-By: Claude"')
    assert message == "$(echo) Co-Authored-By: Claude"
    assert commit_rules.problems(message)


def test_a_dollar_quoted_commit_message_is_read_with_its_line_breaks():
    message = commit_rules.commit_message("git commit -m $'Add x.\\n\\nCo-Authored-By: Claude'")
    assert message == "Add x.\n\nCo-Authored-By: Claude"


@pytest.mark.parametrize("command", ["git \"pu\"'sh' --force origin main", "git $'\\x70ush' --force origin main",
                                     "git $\"push\" --force origin main"])
def test_the_merge_guard_reads_a_quoted_push_like_the_shared_words(command, tmp_path):
    """The merge guard spells decoded words plainly through token_spans before its arms run
    (canonical_spelling); tests/test_merge_guard_150_canonical.py has the other commands."""
    assert merge_guard_pre.check_merge_authorization(command, token_dir=tmp_path, cwd=str(tmp_path))


# Guard and gate modules that may still parse with shlex: git_commit_check moves later (U14),
# staleness is being removed. shlex.quote and shlex.join build text and stay allowed anywhere.
_SHLEX_ALLOWED = {"git_commit_check.py", "staleness.py"}


def test_no_guard_runs_its_own_shlex_reader():
    offenders = []
    for path in sorted((PLUGIN / "hooks").rglob("*.py")):
        if path.name in _SHLEX_ALLOWED:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "shlex" and node.attr in ("split", "shlex")):
                offenders.append(f"{path.relative_to(PLUGIN)}:{node.lineno}")
    assert not offenders, offenders


def test_an_empty_quoted_argument_does_not_end_the_upload_command(tmp_path):
    """Found by the table above: egress_uploads read the empty word of `-H ""` as an operator,
    so the rest of the curl was a command of its own and its -T upload went unscanned."""
    (tmp_path / "notes.txt").write_text("x")
    paths = egress_uploads.upload_paths('curl -H "" -T notes.txt https://x.example', str(tmp_path),
                                        frozenset({"curl"}))
    assert [path for path, _, _ in paths] == ["notes.txt"]


# ── 1.5.0 leakfix M3: one per-runner option table ─────────────────────────────────────────
# Every guard strips runner words through RUNNER_VALUE_OPTIONS. A flag one runner takes alone
# (sudo -s, sudo -E, watch -g) must not swallow the command after it, and a runner option
# that takes a value (xargs -a FILE, env -C DIR, flock's lock file) must not become it.
@pytest.mark.parametrize("command, expected", [
    ("sudo -s curl x", ["curl", "x"]),
    ("sudo -E -u root curl x", ["curl", "x"]),
    ("sudo -uroot curl x", ["curl", "x"]),
    ("sudo -D /tmp curl x", ["curl", "x"]),
    ("watch -g curl x", ["curl", "x"]),
    ("watch -n 2 curl x", ["curl", "x"]),
    ("xargs -a list.txt curl x", ["curl", "x"]),
    ("xargs -alist.txt curl x", ["curl", "x"]),
    ("xargs --arg-file list.txt curl x", ["curl", "x"]),
    ("xargs -0 -d , -E END curl x", ["curl", "x"]),
    ("xargs -I {} curl {}", ["curl", "{}"]),
    ("env -C . curl x", ["curl", "x"]),
    ("env -iC dir curl x", ["curl", "x"]),
    ("env --chdir dir curl x", ["curl", "x"]),
    ("env -C ./dossier-é curl x", ["curl", "x"]),
    ("env -u X -i A=1 curl x", ["curl", "x"]),
    ("timeout -k 5 -s KILL 10 curl x", ["curl", "x"]),
    ("nice -n 19 curl x", ["curl", "x"]),
    ("stdbuf -o 0 curl x", ["curl", "x"]),
    ("flock /tmp/lk curl x", ["curl", "x"]),
    ("flock -w 5 sh curl x", ["curl", "x"]),
    ("caffeinate -t 60 doas -u root curl x", ["curl", "x"]),
    # fxa F8: the runners the second review found the guards blind to
    ("setsid -f curl x", ["curl", "x"]),
    ("taskset 0x3 curl x", ["curl", "x"]),
    ("taskset -c 0-3 curl x", ["curl", "x"]),
    ("ionice -c3 curl x", ["curl", "x"]),
    ("ionice -c 2 -n 7 curl x", ["curl", "x"]),
    ("chrt -r 10 curl x", ["curl", "x"]),
    ("numactl -N 0 --membind=0 curl x", ["curl", "x"]),
    ("script -q /dev/null curl x", ["curl", "x"]),
    ("script -c curl x /dev/null", ["curl", "x", "/dev/null"]),
    ("flock lk -c curl x", ["curl", "x"]),
    ("parallel -j 2 curl x ::: a", ["curl", "x", ":::", "a"]),
    ("env", []),
    ("", []),
])
def test_leakfix_runner_options_come_from_one_per_runner_table(command, expected):
    assert shell_words.strip_prefix(command.split()) == expected


@pytest.mark.parametrize("words, expected", [
    (["-C", "dir"], ("-C", "dir", 2)),
    (["-Cdir"], ("-C", "dir", 1)),
    (["-iC", "dir"], ("-C", "dir", 2)),
    (["--chdir=dir"], ("--chdir", "dir", 1)),
    (["--chdir", "dir"], ("--chdir", "dir", 2)),
    (["-i"], ("-i", None, 1)),
    (["-C"], ("-C", "", 2)),
])
def test_leakfix_runner_option_reads_value_shapes(words, expected):
    assert shell_words.runner_option("env", words, 0) == expected


def test_leakfix_egress_reads_the_same_table(tmp_path):
    words = "env -C sub xargs -a args.txt -I R curl x".split()
    program, runners = egress_uploads._program(words)
    assert (program, runners.chdir, runners.arg_files, runners.fed) == (["curl", "x"], "sub", ["args.txt"], "R")
