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
])
def test_zsh_precommand_modifiers_are_prefix_words(words, command):
    assert shell_words.strip_prefix(words) == command


def _env_guard_denies(command: str, cwd) -> bool:
    output = env_guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    return bool(output) and output["hookSpecificOutput"].get("permissionDecision") == "deny"


@pytest.mark.parametrize("command", [
    "noglob cat .env",
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
    ("git \"pu\\\nsh\" origin main", ["git", "push", "origin", "main"]),
    ("echo \"\\$(git push)\"", ["echo", "$(git push)"]),
    ("cd x && git push origin main", ["cd", "x", "&&", "git", "push", "origin", "main"]),
    ("git push \"origin", None),
    ("git push origin\\", None),
    ("git $'\\x70u\\163h' origin main", ["git", "push", "origin", "main"]),
    (f"git{NBSP}push origin", [f"git{NBSP}push", "origin"]),
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


@pytest.mark.parametrize("command", [
    "git \"pu\"'sh' --force origin main",
])
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
])
def test_leakfix_runner_options_come_from_one_per_runner_table(command, expected):
    assert shell_words.strip_prefix(command.split()) == expected


@pytest.mark.parametrize("words, expected", [
    (["-C", "dir"], ("-C", "dir", 2)),
])
def test_leakfix_runner_option_reads_value_shapes(words, expected):
    assert shell_words.runner_option("env", words, 0) == expected


def test_leakfix_egress_reads_the_same_table(tmp_path):
    words = "env -C sub xargs -a args.txt -I R curl x".split()
    program, runners = egress_uploads._program(words)
    assert (program, runners.chdir, runners.arg_files, runners.fed) == (["curl", "x"], "sub", ["args.txt"], "R")


# ── a runner's script, read with its quotes kept ──────────────────────────────────────────
# The quote-aware readers hand `bash -c 'X=1 cat f'` over as ["bash", "-c", "X=1 cat f"]. The
# walk read that one word as a leading assignment (or as a number: `1; cat f`) and skipped it,
# so no guard saw the script. A word segments() would have cut is the script; the segment
# form, where the script arrives as its own words, keeps its old reading.
S = "X=1; cat f"
SCRIPT_WORDS = [
    ["bash", "-c", "X=1 cat f"],
    ["bash", "-c", S],
    ["bash", "-c", "X=1;cat f"],
    ["bash", "-c", "X=1 Y=2 cat f"],
    ["bash", "-c", 'X=1 Y="a b" cat f'],
    ["bash", "-c", "X=1\ncat f"],
    ["bash", "-c", "X=1\ncat\tf"],                      # no space at all
    ["bash", "-c", "X=1\tcat\tf"],
    ["bash", "-c", "X=1&&cat f"],
    ["bash", "-c", "X=1;cat<f"],
    ["bash", "-c", "X=1<f"],
    ["bash", "-c", "X=`cat f`"],
    ["bash", "-c", "X=$(cat f)"],
    ["bash", "-c", "1; cat f"],                         # a number first
    ["bash", "-c", "2>/dev/null cat f"],
    ["sh", "-ec", S],
    ["bash", "-lc", S],
    ["bash", "-xc", S],
    ["zsh", "-c", S],
    ["dash", "-c", S],
    ["ksh", "-c", S],
    ["fish", "-c", S],
    ["bash", "-c", "-e", S],                            # options after -c are still options
    ["bash", "--norc", "-c", S],
    ["bash", "-c", "--", S],
    ["bash", "-c", "--", "-n; cat f"],                  # after --, a script may start with -
    ["sudo", "bash", "-c", S],
    ["sudo", "-u", "root", "env", "-i", "bash", "-c", S],
    ["env", "bash", "-c", S],
    ["timeout", "5", "bash", "-c", S],
    ["nice", "-n", "5", "sh", "-c", S],
    ["command", "bash", "-c", S],
    ["nohup", "busybox", "sh", "-c", S],
    ["xargs", "sh", "-c", S],
    ["watch", S],                                       # watch runs its line through sh -c
    ["watch", "-n", "5", S],
    ["flock", "/tmp/l", "-c", S],
    ["flock", "-w", "5", "/tmp/l", "-c", S],
    ["flock", "/tmp/l", "--command", S],
]


@pytest.mark.parametrize("words", SCRIPT_WORDS, ids=[" ".join(w)[:40] for w in SCRIPT_WORDS])
def test_a_script_handed_to_a_shell_is_the_command(words):
    assert shell_words.strip_prefix(words) == words[-1:]


@pytest.mark.parametrize("words", [
    ["script", "-c", S, "/dev/null"],
    ["script", "-q", "-c", S, "/dev/null"],
    ["script", "--command", S, "/dev/null"],
])
def test_scripts_command_is_the_command_and_its_file_follows(words):
    assert shell_words.strip_prefix(words) == [S, "/dev/null"]


def test_the_options_before_the_script_are_still_recorded():
    assert shell_words.runner_prefix(["sudo", "bash", "-c", "-e", S]) == (4, [
        ("sudo", "", None), ("bash", "", None), ("bash", "-c", None), ("bash", "-e", None)])


@pytest.mark.parametrize("words, command", [
    (["bash", "-c", "X=1", "cat", "f"], ["cat", "f"]),             # segments() form
    (["bash", "-c", "1", "cat", "f"], ["cat", "f"]),
    (["sh", "-ec", "X=1", "Y=2", "cat", "f"], ["cat", "f"]),
    (["watch", "X=1", "cat", "f"], ["cat", "f"]),
    (["watch", "X=1", "cat f"], ["cat f"]),                         # watch joins its words
    (["flock", "/tmp/l", "-c", "X=1", "cat", "f"], ["cat", "f"]),
    (["X=a b", "cat", "f"], ["cat", "f"]),                          # no runner: an assignment
    (["env", "X=a b", "cat", "f"], ["cat", "f"]),
    (["sudo", "X=a b", "cat", "f"], ["cat", "f"]),
    (["timeout", "5", "cat", "f"], ["cat", "f"]),
    (["nice", "X=a b", "cat", "f"], ["cat", "f"]),
    (["flock", "my lock", "cat", "f"], ["cat", "f"]),               # an operand, not a script
    (["bash", "-c", "cat"], ["cat"]),
])
def test_a_word_segments_would_not_cut_keeps_the_old_walk(words, command):
    assert shell_words.strip_prefix(words) == command


def test_no_word_segments_returns_holds_a_cut_character():
    """The rule rests on this: segments() cuts or deletes each character of SEGMENT_CUTS and
    splits at every blank, so a word that holds one was read with its quotes kept."""
    command = "bash -c 'X=1 Y=\"a b\"\tcat f' | (sudo flock l -c `x` && {watch <a >b}) \\\n\\x"
    found = [word for words in shell_words.segments(command) for word in words]
    assert found and not [word for word in found
                          if any(c in shell_words.SEGMENT_CUTS or c.isspace() for c in word)]
    assert set("|;&()`{}<>'\"\\") == shell_words.SEGMENT_CUTS


# ── a runner given by its absolute path ───────────────────────────────────────────────────
# The walk matched a runner word exactly, so /bin/sh, /usr/bin/env and /usr/bin/sudo were read
# as the command and the script or command behind them was never judged. A word that starts
# with "/" is read by its last component; a keyword is not (/bin/if is a program).
PATH_RUNNERS = [
    (["/bin/sh", "-c", "X=1 cat f"], ["X=1 cat f"]),                # quote-aware: the script
    (["/bin/bash", "-c", S], [S]),
    (["/bin/bash", "-c", "cat f"], ["cat f"]),
    (["/bin/sh", "-c", "cat", "f"], ["cat", "f"]),                  # segments() form
    (["/bin/sh", "-c", "X=1", "cat", "f"], ["cat", "f"]),
    (["/usr/bin/env", "cat", "f"], ["cat", "f"]),
    (["/usr/bin/env", "X=1", "cat", "f"], ["cat", "f"]),             # with an assignment
    (["/usr/bin/env", "-i", "cat", "f"], ["cat", "f"]),              # with options
    (["/usr/bin/env", "-u", "HOME", "-C", "sub", "cat", "f"], ["cat", "f"]),
    (["/usr/bin/env", "bash", "-c", "cat f"], ["cat f"]),
    (["/usr/bin/env", "/bin/bash", "-c", S], [S]),
    (["/usr/bin/sudo", "cat", "f"], ["cat", "f"]),
    (["/usr/bin/sudo", "-u", "root", "cat", "f"], ["cat", "f"]),     # sudo's option table
    (["sudo", "/usr/bin/env", "-i", "/bin/sh", "-c", S], [S]),
    (["/usr/bin/xargs", "-I", "R", "cat", "R"], ["cat", "R"]),
    (["/usr/bin/timeout", "5", "/bin/sh", "-c", S], [S]),
    (["/usr/bin/flock", "/tmp/l", "-c", S], [S]),                    # its operand, then -c
    (["/usr/bin/watch", S], [S]),                                    # watch runs its line
    (["/usr/bin/nohup", "/usr/bin/sudo", "/bin/sh", "-c", "cat", "f"], ["cat", "f"]),
]


@pytest.mark.parametrize("words, command", PATH_RUNNERS,
                         ids=[" ".join(w)[:40] for w, _ in PATH_RUNNERS])
def test_a_runner_given_by_its_absolute_path_is_that_runner(words, command):
    assert shell_words.strip_prefix(words) == command


def test_a_runner_given_by_path_is_named_by_its_base_name():
    assert shell_words.runner_prefix(["/usr/bin/sudo", "-u", "root", "/bin/bash", "-c", S]) == (
        5, [("sudo", "", None), ("sudo", "-u", "root"), ("bash", "", None), ("bash", "-c", None)])


@pytest.mark.parametrize("shell", ["bash", "/bin/bash"])
def test_a_kept_runner_given_by_path_is_returned_as_the_command(shell):
    keep = frozenset({"bash"})
    assert shell_words.runner_prefix([shell, "-c", S], keep) == (0, [])
    assert shell_words.runner_prefix(["/usr/bin/env", shell, "-c", S], keep) == (
        1, [("env", "", None)])


@pytest.mark.parametrize("words", [
    ["./sh", "-c", S],                                  # a relative path stays the command
    ["sh/x", "-c", S],
    ["/opt/tool", "cat", "f"],
    ["/bin/if", "cat", "f"],                            # a keyword is matched exactly
    ["/", "cat", "f"],
    ["/bin/", "cat", "f"],
])
def test_a_word_that_names_no_runner_by_path_stays_the_command(words):
    assert shell_words.strip_prefix(words) == words


# ── a whole script is never a program name; the script a runner hands to a shell ──────────
# Callers took the program from the command word (its last path part), so the script of
# `bash -c 'X=1; ...; /bin/echo'` was read as echo and `dash -c 'X=1;...;/bin/grep'` as grep.
# runner_script names the script itself, for watch, flock -c, script -c and fish's command
# options too, so each reader hands it to its own script reader.
@pytest.mark.parametrize("word", ["X=1; cat f", "cat f", "a\tb", "x;y", "a|b", "a>b", "a(b",
                                  "a'b", 'a"b', "a\\b", "a`b", "{a"])
def test_a_word_holding_a_blank_or_a_cut_character_is_a_script(word):
    assert shell_words.script_word(word)


@pytest.mark.parametrize("word", ["cat", "/bin/echo", "X=1", "-c", "", "/usr/bin/grep", "a.b"])
def test_a_plain_word_is_no_script(word):
    assert not shell_words.script_word(word)


@pytest.mark.parametrize("word, name", [
    ("/usr/bin/sudo", "sudo"), ("/bin/sh", "sh"), ("sudo", "sudo"), ("/bin/if", "/bin/if"),
    ("./sudo", "./sudo"), ("bin/sudo", "bin/sudo"),
])
def test_a_runner_word_is_named_as_the_walk_names_it(word, name):
    assert shell_words.runner_name(word) == name


RUNNER_SCRIPTS = [
    (["bash", "-c", S], S),
    (["bash", "-c", S, "sh", "a"], S),                              # words after: positional
    (["bash", "-c", "ls"], "ls"),
    (["sudo", "/bin/sh", "-ec", S], S),
    (["watch", S], S),
    (["watch", "-n", "5", S], S),
    (["watch", "X=1;", "cat", "f"], "X=1; cat f"),                  # watch joins its words
    (["flock", "/tmp/l", "-c", S], S),
    (["flock", "-nc", S, "/tmp/l"], S),                             # a cluster holding c
    (["script", "-c", S, "/dev/null"], S),
    (["script", "-qc", S, "/dev/null"], S),
    (["script", "-q", "--command", S, "/dev/null"], S),
    (["script", "-qc" + S, "/dev/null"], S),                        # the rest of the word
    (["fish", "-c", S], S),
    (["fish", "--command", "X=1 cat f"], "X=1 cat f"),
    (["fish", "-C", S], S),
    (["fish", "--init-command", S], S),
    (["eval", S], S),                                               # eval joins its words too
    (["eval", "X=1;", "cat", "f"], "X=1; cat f"),
    (["watch", "echo", "a; cat f"], "echo a; cat f"),                # a script word later on
    (["eval", "echo", "a; cat f"], "echo a; cat f"),
]


@pytest.mark.parametrize("words, script", RUNNER_SCRIPTS,
                         ids=[" ".join(w)[:40] for w, _ in RUNNER_SCRIPTS])
def test_runner_script_is_the_script_a_runner_hands_to_a_shell(words, script):
    assert shell_words.runner_script(words) == script


@pytest.mark.parametrize("words", [
    ["cat", "f"],
    ["X=1; cat f"],                                     # no runner hands it over
    ["sudo", "cat", "f"],
    ["watch", "ls", "-l"],                              # plain words: read as the command
    ["eval", "cat", "f"],
    ["flock", "/tmp/l", "-c", "ls"],
    ["script", "-c", "ls", "/dev/null"],
    ["script", "-Ec", "ls", "/dev/null"],               # c is -E's value, not -c
    ["bash", "x.sh"],
    ["bash", "-c"],
    ["flock", "/tmp/l", S],                             # flock F CMD: no -c, no script
])
def test_runner_script_is_none_without_a_handed_script(words):
    assert shell_words.runner_script(words) is None


@pytest.mark.parametrize("words, command", [
    (["fish", "--command", "X=1 cat f"], ["X=1 cat f"]),
    (["fish", "-C", S], [S]),
    (["fish", "--init-command", S], [S]),
    (["script", "-qc", S, "/dev/null"], [S, "/dev/null"]),
    (["flock", "-nc", S, "/tmp/l"], [S, "/tmp/l"]),
    (["script", "-Ec", "/dev/null", "ls"], ["ls"]),                # -E takes c as its value
    (["eval", "X=1 cat f"], ["X=1 cat f"]),                         # not the assignment X
    (["eval", "X=1", "cat", "f"], ["cat", "f"]),                    # segments() form
])
def test_fish_command_options_and_c_clusters_take_the_script(words, command):
    assert shell_words.strip_prefix(words) == command


# ── every script fish runs; watch keeps its line past a later runner word ──────────────────
# fish runs every -C (first) and every -c, so the first alone left the rest unread; and a
# runner word after watch cleared its script, so `watch nice 'X=1; ...'` skipped the script
# as an assignment and nothing read it.
FISH_SCRIPTS = [
    (["fish", "-C", "echo hi", "-c", S], "echo hi\n" + S),
    (["fish", "-c", "echo hi", "-c", S], "echo hi\n" + S),
    (["fish", "-c", "true", "-c", S], "true\n" + S),                # a plain word first
    (["fish", "-c", S, "-C", "echo hi"], "echo hi\n" + S),          # -C runs before -c
    (["fish", "--init-command", "echo hi", "--command", S], "echo hi\n" + S),
    (["fish", "--init-command=echo hi", "--command=" + S], "echo hi\n" + S),
    (["fish", "-cecho hi", "-c", S], "echo hi\n" + S),              # glued to its option
    (["fish", "-i", "-c", "true", "-o", "/tmp/log", "-c", S], "true\n" + S),  # -o's value
    (["fish", "--comm", S], S),                                     # a long option's prefix
    (["/usr/local/bin/fish", "-C", "true", "-c", S], "true\n" + S),
    (["sudo", "fish", "-C", "true", "-c", S], "true\n" + S),
    (["fish", "-c", "true", "a", "-c", S], "true"),                 # after `a`: $argv
    (["fish", "-c", S, "f"], S),
]


@pytest.mark.parametrize("words, script", FISH_SCRIPTS,
                         ids=[" ".join(w)[:40] for w, _ in FISH_SCRIPTS])
def test_runner_script_holds_every_script_fish_runs_in_its_order(words, script):
    assert shell_words.runner_script(words) == script


LINE_KEPT = [
    (["watch", "nice", S], "nice " + S),
    (["watch", "nice", "-n", "5", S], "nice -n 5 " + S),
    (["watch", "env", "X=1", "cat f | tr a b"], "env X=1 cat f | tr a b"),
    (["watch", "-n", "5", "sudo", "-u", "root", S], "sudo -u root " + S),
    (["watch", "time", "nohup", S], "time nohup " + S),
    (["/usr/bin/watch", "/usr/bin/nice", S], "/usr/bin/nice " + S),
    (["eval", "nice", S], "nice " + S),
    (["watch", "nice", "bash", "-c", S], S),                        # a later shell -c takes it
    (["watch", "flock", "/tmp/l", "-c", S], S),
    (["watch", "-x", "sh", "-c", S], S),
]


@pytest.mark.parametrize("words, script", LINE_KEPT,
                         ids=[" ".join(w)[:40] for w, _ in LINE_KEPT])
def test_watch_keeps_its_line_past_a_later_runner_word(words, script):
    assert shell_words.runner_script(words) == script


@pytest.mark.parametrize("words, command", [
    (["watch", "nice", S], [S]),                        # the script, not skipped as X=1
    (["watch", "env", "X=1", S], [S]),
    (["eval", "sudo", S], [S]),
    (["watch", "nice", "ls"], ["ls"]),                  # plain words: still the command
    (["watch", "nice", "X=1", "cat", "f"], ["cat", "f"]),  # segments() form, as before
])
def test_a_script_after_watch_and_a_runner_word_is_the_command(words, command):
    assert shell_words.strip_prefix(words) == command


@pytest.mark.parametrize("words", [["watch", "nice", "ls"], ["watch", "sudo", "cat", "f"],
                                   ["fish", "x.fish", "-c", S], ["fish", "-c"]])
def test_no_line_without_a_script_word_after_watch_and_a_runner(words):
    assert shell_words.runner_script(words) is None


# ── a shell's options with their values, and its + options ────────────────────────────────
# The shells had no value options and a word that starts with "+" was no option, so the walk
# stopped at the value of -o or -O (`pipefail` of bash -o pipefail -c S was taken for the
# command) or at +x, and the -c script was never read. As bash 3.2, /bin/sh, dash, zsh 5.9
# and ksh93 read them: bash, sh and dash take each o of a cluster (bash's O too) from the next
# word in turn and read the rest of the cluster on; zsh and ksh take the rest of the cluster
# or the next word; all five read + options and +c as -c; ksh reads -o c as -c; bash reads
# its long options (one dash or two, --rcfile F) only before its first short option. fish's
# value letters are its getopt string's; fish reads no + option.
SHELL_OPTION_SCRIPTS = [
    ["bash", "-o", "pipefail", "-c", S],
    ["bash", "-O", "extglob", "-c", S],
    ["sh", "-o", "errexit", "-c", S],
    ["bash", "-o", "xtrace", "-c", S],
    ["bash", "-c", "-o", "pipefail", S],                # after -c, before the script
    ["bash", "+o", "posix", "-c", S],
    ["bash", "+O", "extglob", "-c", S],
    ["sh", "+x", "-c", S],
    ["bash", "-eo", "pipefail", "-c", S],               # a cluster that ends in o
    ["zsh", "-o", "pipefail", "-c", S],
    ["dash", "-o", "errexit", "-c", S],
    ["ksh", "-o", "pipefail", "-c", S],
    ["/bin/bash", "-o", "pipefail", "-c", S],
    ["sudo", "bash", "-o", "pipefail", "-c", S],
    ["env", "sh", "+x", "-c", S],
    ["bash", "-o", "errexit", "-o", "nounset", "-c", S],
    ["bash", "-oc", "pipefail", S],                     # o's value is the next word, c is -c
    ["bash", "-Oc", "extglob", S],
    ["dash", "-oo", "errexit", "nounset", "-c", S],     # each o takes the next word in turn
    ["zsh", "-co", "pipefail", S],
    ["ksh", "-co", "pipefail", S],
    ["zsh", "-opipefail", "-c", S],                     # the rest of the cluster
    ["bash", "+c", S], ["sh", "+xc", S], ["dash", "+c", S], ["zsh", "+c", S], ["ksh", "+c", S],
    ["ksh", "-o", "c", S], ["ksh", "+o", "c", S], ["ksh", "-oc", S],
    ["bash", "--rcfile", "/dev/null", "-c", S],
    ["bash", "--init-file", "/dev/null", "-c", S],
    ["bash", "-rcfile", "/dev/null", "-c", S],           # a long option with one dash
    ["sh", "-init-file", "/dev/null", "-c", S],
    ["bash", "--norc", "-rcfile", "/dev/null", "-c", S],
    ["bash", "-norc", "-o", "errexit", "-c", S],         # -norc: a long option, not -n -o
    ["bash", "-x", "-posix", "errexit", "-c", S],        # after a short option: a cluster
    ["bash", "-x", "-rcfile", S],                        # -r -c -f -i -l -e
    ["fish", "-o", "/tmp/log", "-c", S],
    ["fish", "-p", "/tmp/prof", "-c", S],
    ["fish", "-f", "qmark-noglob", "-c", S],
    ["fish", "-io", "/tmp/log", "-c", S],                # a cluster that ends in a value letter
    ["fish", "--debug-output", "/tmp/log", "-c", S],
    ["fish", "--debug-o", "/tmp/log", "-c", S],          # a unique prefix of a long name
    ["fish", "--profile", "/tmp/prof", "--command", S],
    ["fish", "--features", "qmark-noglob", "-c", S],
]


@pytest.mark.parametrize("words", SHELL_OPTION_SCRIPTS,
                         ids=[" ".join(w)[:40] for w in SHELL_OPTION_SCRIPTS])
def test_a_shells_options_and_their_values_lead_to_its_script(words):
    assert shell_words.strip_prefix(words) == [S]
    assert shell_words.runner_script(words) == S


@pytest.mark.parametrize("words, record", [
    (["bash", "-eo", "pipefail", "-c", S],
     [("bash", "", None), ("bash", "-eo", "pipefail"), ("bash", "-c", None)]),
    (["bash", "+O", "extglob", "-c", S],
     [("bash", "", None), ("bash", "+O", "extglob"), ("bash", "-c", None)]),
    (["zsh", "-xopipefail", "-c", S],
     [("zsh", "", None), ("zsh", "-xo", "pipefail"), ("zsh", "-c", None)]),
    (["bash", "--rcfile", "/dev/null", "-c", S],
     [("bash", "", None), ("bash", "--rcfile", "/dev/null"), ("bash", "-c", None)]),
    (["bash", "-rcfile", "/dev/null", "-c", S],
     [("bash", "", None), ("bash", "--rcfile", "/dev/null"), ("bash", "-c", None)]),
])
def test_a_shells_option_is_recorded_with_its_value(words, record):
    assert shell_words.runner_prefix(words) == (len(words) - 1, record)


@pytest.mark.parametrize("words, command", [
    (["bash", "script.sh"], ["script.sh"]),
    (["bash", "-o", "pipefail", "script.sh"], ["script.sh"]),       # a file operand, no -c
    (["bash", "+x", "script.sh"], ["script.sh"]),
    (["bash", "-O", "nocaseglob", "script.sh"], ["script.sh"]),     # a value holding c
    (["bash", "-o", "noclobber", "script.sh"], ["script.sh"]),
    (["zsh", "-onoclobber", "script.sh"], ["script.sh"]),           # glued: the value's c
    (["zsh", "-oc", "script.sh"], ["script.sh"]),                   # zsh: option c, no -c
    (["bash", "-norc", "script.sh"], ["script.sh"]),                # as --norc reads
    (["bash", "-rcfile", "/dev/null", "script.sh"], ["script.sh"]),
    (["fish", "+x", "-c", S], ["+x", "-c", S]),                     # fish: +x is its file
    (["env", "+x", "cat", "f"], ["+x", "cat", "f"]),                # + is a shell's alone
    (["nice", "+5", "cat", "f"], ["+5", "cat", "f"]),
])
def test_a_shell_given_options_and_a_file_runs_no_script(words, command):
    assert shell_words.strip_prefix(words) == command
    assert shell_words.runner_script(words) is None


@pytest.mark.parametrize("words, script", [
    (["bash", "-c", S], S),
    (["bash", "-e", "-c", S], S),
    (["sh", "-c", S, "arg0", "arg1"], S),
    (["bash", "-c", "--", S], S),
    (["bash", "--norc", "-c", S], S),
])
def test_a_shells_plain_script_is_read_as_before(words, script):
    assert shell_words.runner_script(words) == script


@pytest.mark.parametrize("words", [["bash", "-o"], ["bash", "-oo", "errexit"], ["zsh", "-o"],
                                   ["bash", "--rcfile"], ["fish", "-o"], ["bash", "+"]])
def test_a_shell_option_missing_its_value_ends_the_words(words):
    assert shell_words.strip_prefix(words) == []
    assert shell_words.runner_script(words) is None


# ── ksh's optional -o value, ksh's -T, zsh's --emulate ──────────────────────────────────────
# ksh93 takes the next word for the value of -o or +o only when that word does not start with
# - or + (-o alone lists the options and ksh reads on), so `ksh -o -c S` runs S while the walk
# took -c for -o's value and never read S. ksh's -T takes the next word whatever it holds
# ('', ' 5', -5), and zsh's --emulate the next word, read only before zsh's other options
# (one --emulate after another too); the walk read both with no value and took that value for
# the command. As ksh 93u+ and zsh 5.9 read them.
KSH_ZSH_OPTION_SCRIPTS = [
    ["ksh", "-o", "-c", S],
    ["ksh", "-xo", "-c", S],                            # a cluster that ends in o
    ["ksh", "-eo", "-c", S],
    ["ksh", "-o", "-xc", S],
    ["ksh", "-o", "-ec", S],
    ["ksh", "-o", "-o", "-c", S],
    ["ksh", "-o", "-e", "-c", S],
    ["ksh", "-co", "-x", S],                            # -c, then -o with no value
    ["ksh", "+o", "-c", S],
    ["ksh", "-o", "+c", S],
    ["ksh", "+o", "+c", S],
    ["ksh", "+o", "-xc", S],
    ["ksh", "-o", "pipefail", "+o", "-c", S],
    ["ksh", "-o", "", "-c", S],                         # an empty word is -o's value
    ["/bin/ksh", "-o", "-c", S],
    ["sudo", "ksh", "-o", "-c", S],
    ["env", "ksh", "-o", "-c", S],
    ["nice", "ksh", "-o", "-c", S],
    ["xargs", "ksh", "-o", "-c", S],
    ["timeout", "5", "ksh", "-o", "-c", S],
    ["flock", "/tmp/l", "ksh", "-o", "-c", S],
    ["ksh", "-T", "", "-c", S],
    ["ksh", "-T", " 5", "-c", S],
    ["ksh", "-T", "+5", "-c", S],
    ["ksh", "-T", "-5", "-c", S],                       # -T takes the next word whatever
    ["ksh", "-xT", "0", "-c", S],
    ["ksh", "-o", "-T", "0", "-c", S],
    ["zsh", "--emulate", "foo", "-c", S],
    ["zsh", "--emulate", "foo", "+c", S],
    ["zsh", "--emulate", "foo", "-xc", S],
    ["zsh", "--emulate", "foo", "-o", "pipefail", "-c", S],
    ["zsh", "--emulate", "foo", "--emulate", "bar", "-c", S],
    ["/bin/zsh", "--emulate", "foo", "-c", S],
    ["sudo", "zsh", "--emulate", "foo", "-c", S],
]


@pytest.mark.parametrize("words", KSH_ZSH_OPTION_SCRIPTS,
                         ids=[" ".join(w)[:40] for w in KSH_ZSH_OPTION_SCRIPTS])
def test_kshs_bare_o_its_t_and_zshs_emulate_lead_to_the_script(words):
    assert shell_words.strip_prefix(words) == [S]
    assert shell_words.runner_script(words) == S


@pytest.mark.parametrize("words, record", [
    (["ksh", "-o", "-c", S], [("ksh", "", None), ("ksh", "-o", None), ("ksh", "-c", None)]),
    (["ksh", "-xo", "+c", S], [("ksh", "", None), ("ksh", "-xo", None), ("ksh", "+c", None)]),
    (["ksh", "-T", "", "-c", S], [("ksh", "", None), ("ksh", "-T", ""), ("ksh", "-c", None)]),
    (["zsh", "--emulate", "sh", "-c", S],
     [("zsh", "", None), ("zsh", "--emulate", "sh"), ("zsh", "-c", None)]),
])
def test_kshs_bare_o_its_t_and_zshs_emulate_are_recorded(words, record):
    assert shell_words.runner_prefix(words) == (len(words) - 1, record)


@pytest.mark.parametrize("words, command", [
    (["ksh", "-o", "-x", "script.sh"], ["script.sh"]),             # -o lists, -x, a file
    (["ksh", "-o", "pipefail", "script.sh"], ["script.sh"]),
    (["ksh", "-xo", "pipefail", "script.sh"], ["script.sh"]),
    (["ksh", "-opipefail", "script.sh"], ["script.sh"]),           # glued: the value as before
    (["ksh", "-T", "5", "script.sh"], ["script.sh"]),
    (["ksh", "-R", "-c", "script.sh"], ["script.sh"]),             # -R takes the next word
    (["zsh", "--emulate", "sh", "script.sh"], ["script.sh"]),
    (["zsh", "-o", "-c", "script.sh"], ["script.sh"]),             # zsh's -o takes -c: no -c
])
def test_kshs_bare_o_its_t_and_zshs_emulate_before_a_file_run_no_script(words, command):
    assert shell_words.strip_prefix(words) == command
    assert shell_words.runner_script(words) is None
