"""
Location: pact-plugin/tests/test_env_guard_review_rows.py
Summary: The repro inputs of the adversarial review of hooks/env_guard.py, as table rows,
         and the boundary of the reader they led to (hooks/shell_words.py read_script).
           ROWS     each command the review found allowed that reads, searches or stages a
                    protected file. A row is (finding, probe id, directory, main's verdict,
                    command); every one must be denied now. "DENY" rows are shapes main
                    refused and the relaxed checks let through; "ALLOW" rows got through
                    on main as well.
           KEPT     the wrongly blocked shapes the relaxed checks let through, which closing
                    the rows above must not block again.
           READER   what read_script returns, what it refuses, and that it refuses fast.
Used by: the plugin suite. The rules themselves are tabled in test_env_guard_relaxations.py;
         the process-listing rows of the same review are in test_process_table_guard.py.

The fixture tree holds inert files only (MARKER). No real env or credential file is opened.
"""
from __future__ import annotations

import os
import random
import subprocess
import time

import pytest

import env_guard as guard
import process_table_guard
import shell_words
from project_tasks_support import GIT_ENV

MARKER = "inert-marker-7c2a"
SECRET = f"KEY={MARKER}\nDEMO_KEY={MARKER}\n"


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """home/ and w/: proj (env files at its root, under conf/ and deep/a/, and link.txt, a
    link to .env), clean (none), other (one at its root and one under x/), repo (a repository
    with an untracked env file), cleanrepo (a repository without one), `sp ace` (one)."""
    root = tmp_path_factory.mktemp("review").resolve()

    def put(rel: str, text: str = "KEY plain text\n") -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    for name in ("home/.env", "w/proj/.env", "w/proj/.env.production", "w/proj/deep/a/.env.local",
                 "w/proj/conf/app.env", "w/other/.env", "w/other/x/.env", "w/repo/.env",
                 "w/sp ace/.env"):
        put(name, SECRET)
    for name in ("w/proj/notes.txt", "w/proj/src/code.txt", "w/proj/src/sub/x.txt",
                 "w/proj/.env.example", "w/proj/.env.sample", "w/clean/a.txt", "w/clean/sub/b.txt",
                 "w/clean/x/c.txt", "w/repo/README.md", "w/repo/ok.txt", "w/cleanrepo/README.md",
                 "w/cleanrepo/new.txt", "w/sp ace/a.txt"):
        put(name)
    (root / "w/proj/link.txt").symlink_to(".env")
    for repo in ("w/repo", "w/cleanrepo"):
        for args in (["init", "-q", "-b", "main"], ["add", "README.md"],
                     ["commit", "-q", "-m", "root"]):
            subprocess.run(["git", "-C", str(root / repo), *args], check=True,
                           capture_output=True, env={**os.environ, **GIT_ENV})
    return root


@pytest.fixture(autouse=True)
def session(tree, monkeypatch):
    monkeypatch.setenv("HOME", str(tree / "home"))
    monkeypatch.delenv("CDPATH", raising=False)


def reason(command: str, tree, where: str = "proj") -> str | None:
    payload = {"tool_name": "Bash", "tool_input": {"command": command.replace("{T}", str(tree))},
               "cwd": str(tree / "w" / where)}
    decision = (guard.decide(payload) or {}).get("hookSpecificOutput", {})
    if decision.get("permissionDecision") != "deny":
        return None
    assert MARKER not in decision["permissionDecisionReason"], "a value reached the reason"
    return decision["permissionDecisionReason"]


def nest(depth: int, inner: str, opener: str = "$(") -> str:
    return "echo " + opener * depth + inner + ")" * depth


ROWS = [
    ("R1", "h1", "proj", "DENY", "echo $((1<<2))\ncat .env"),
    ("R6", "s8", "proj", "DENY", 'cd "$NOWHERE_SET" 2>/dev/null; cat .env.p*'),
    ("R10", "d42b", "proj", "DENY", "cd {T}/w/clean; repeat 1 cd {T}/w/proj; grep -rn KEY ."),
    ("P2", "s2", "clean", "ALLOW", "grep -rn KEY $PWD" ":h/other"),
    ("P3", "m41", "repo", "ALLOW", "git add -Af"),
]

KEPT = [
    ("bang-body", "proj", "cat <<!\ncat .env\n!\nls"),
    ("a28", "clean", "[[ -f a.txt ]] && echo yes || echo no"),
]

# ── The review of the reader itself: what it still let through, and what it newly blocked ─
SYSTEM = "awk '{system($0)}'"
BODY = " <<EOF\ncat .env\nEOF"
# (finding, id, directory, command): allowed before the fix, denied now. Each prints the
# fixture's inert value when run in bash 3.2 or zsh, unless its note says otherwise.
THIRD = [
    ("T1", "b1", "proj", SYSTEM + BODY),
    ("T1", "piped", "proj", "cat <<EOF | " + SYSTEM + "\ncat .env\nEOF"),
    ("T1", "pager", "proj", "LESSOPEN='||-sh' less" + BODY),
    ("T4", "u2", "proj", "echo \"$(echo a # b\\\n) '\ncat .env #'\n)\""),
]

# Denied before the fix and still: the neighbours a fix could have let go.
STILL = [
    ("xargs", "proj", "xargs " + SYSTEM + BODY),
    ("class-eval", "proj", 'eval "cat .e[[:lower:]]v"'),
    ("u3", "proj", "echo $(echo a # )\ncat .env\n)"),
]

# Allowed now. Rows marked `was denied` are the false blocks the fix removes; the rest are
# the shapes it must not start to block.
ALLOWED = [
    ("f1", "proj", "jq '[.a]|length' notes.txt"),
    ("search-root", "proj", 'W="{T}/w/clean"; cd "$W/sub" && grep -rn KEY . ../x | head -5'),
]


@pytest.mark.parametrize("finding, probe, where, main, command", ROWS,
                         ids=[f"{row[0]}-{row[1]}-main-{row[3]}" for row in ROWS])
def test_a_shape_the_review_found_allowed_is_denied(tree, finding, probe, where, main, command):
    assert main in ("DENY", "ALLOW")
    assert reason(command, tree, where)


@pytest.mark.parametrize("probe, where, command", KEPT, ids=[row[0] for row in KEPT])
def test_a_relaxed_shape_is_still_allowed(tree, probe, where, command):
    assert reason(command, tree, where) is None


@pytest.mark.parametrize("finding, probe, where, command", THIRD,
                         ids=[f"{row[0]}-{row[1]}" for row in THIRD])
def test_a_shape_the_reader_review_found_allowed_is_denied(tree, finding, probe, where, command):
    assert reason(command, tree, where)


@pytest.mark.parametrize("probe, where, command", STILL, ids=[row[0] for row in STILL])
def test_a_neighbour_of_a_reader_fix_is_still_denied(tree, probe, where, command):
    assert reason(command, tree, where)


@pytest.mark.parametrize("probe, where, command", ALLOWED, ids=[row[0] for row in ALLOWED])
def test_a_shape_a_reader_fix_must_not_block_is_allowed(tree, probe, where, command):
    assert reason(command, tree, where) is None


def test_a_bracket_word_of_any_shape_is_answered_fast(tree):
    """Odd and hostile words: each gets a verdict, and none costs its square."""
    for word in ("[" * 20_000, "[[:" * 20_000, ".e" + "[[:alpha:]" * 5_000, "[]" * 20_000,
                 "[é✓]", "[", "[]", "[!]", "[[:]", "[[:alpha:]", ".e[[:nope:]]v", "x[\0]"):
        start = time.perf_counter()
        verdict = guard.bash_verdict("cat " + word, str(tree / "w" / "proj"))
        assert time.perf_counter() - start < 1.0, word[:20]
        assert verdict is None or isinstance(verdict, str)
    assert reason("cat .e[[:nope:]]v", tree) and reason("cat " + ".e" + "[[:alpha:]" * 5_000, tree)
    assert reason("jq '" + "[.a]|" * 2_000 + ".' notes.txt", tree) is None


# ── what a construct the reader cannot read gets ────────────────────────────────────────
def test_an_unreadable_construct_denies_with_the_form_to_write_instead(tree):
    """Denied whatever it mentions: neither command names a protected file."""
    odd = reason("cat <<EOF+1\nx\nEOF+1\nls", tree, "clean")
    assert "heredoc delimiter" in odd and "<<'EOF'" in odd and "then rerun" in odd
    deep = reason(nest(9, "date"), tree, "clean")
    assert "nested more than 8 deep" in deep and "variables" in deep and "\n" not in deep
    assert "NUL" in reason("echo a\0b", tree, "clean")


def test_text_the_shell_cannot_read_either_is_judged_with_its_quotes_deleted(tree):
    assert reason("echo 'never closed", tree, "clean") is None      # nothing named: the shell's
    assert "could not be parsed" in reason("cat .env; echo 'never closed", tree)
    assert reason("cat link.txt; echo 'never closed", tree)          # the command before it runs


# ── R5: the time a nest of substitutions takes ──────────────────────────────────────────
NESTS = {
    "link": lambda depth: nest(depth, "cat link.txt"),
    "named": lambda depth: nest(depth, "cat .env"),
    "echo-link": lambda depth: nest(depth, "$(cat link.txt)", "$(echo "),
    "clean": lambda depth: nest(depth, "hi", "$(echo "),
    "then-named": lambda depth: nest(depth, "hi", "$(echo ") + "; cat .env",
    "then-add": lambda depth: nest(depth, "hi", "$(echo ") + "; git add -A",
}


@pytest.mark.parametrize("shape", sorted(NESTS))
def test_sixteen_levels_of_substitution_are_answered_in_under_a_second(tree, shape):
    start = time.perf_counter()
    text = reason(NESTS[shape](16), tree)
    assert time.perf_counter() - start < 1.0
    assert "nested more than 8 deep" in text


@pytest.mark.parametrize("shape", sorted(NESTS))
def test_the_deepest_nest_that_is_read_is_judged_and_fast(tree, shape):
    start = time.perf_counter()
    text = reason(NESTS[shape](7), tree)
    assert time.perf_counter() - start < 1.0
    assert bool(text) is (shape not in ("clean", "then-add"))


def test_many_heredocs_or_substitutions_do_not_cost_their_square(tree):
    # Growth, not a wall-clock limit: doubling the input must roughly double the time
    # (linear gives ~2, quadratic ~4). Best of three per size; one generous ceiling.
    def best(command):
        times = []
        for _ in range(3):
            start = time.perf_counter()
            assert reason(command, tree, "clean") is None
            times.append(time.perf_counter() - start)
        return min(times)

    for unit, n in (("cat <<E\nx\nE\n", 1000), ("echo $(date) ", 1000), ("echo a; ", 2000)):
        small, large = best(unit * n), best(unit * 2 * n)
        assert large / max(small, 0.02) < 3.2, (unit, small, large)
        assert large < 15.0, (unit, large)


# ── READER: shell_words.read_script ─────────────────────────────────────────────────────
READ = [
    ("echo a # it's\nls -la", "echo a \nls -la", []),
    ("\n\n", "\n\n", []),
    ("x=$(echo a # )\nls\n)", "x=$(echo a \nls\n)", []),
]
# (text, the shell cannot read it either)
REFUSED = [
    ("echo 'open", True),
    ("cat <<EOF+1\nx\nEOF+1", False),
    ('echo "$(echo a # b\\\\\n) x\n)"', False),
]


@pytest.mark.parametrize("text, flat, bodies", READ)
def test_the_reader_returns_the_text_the_shell_runs_and_the_bodies_apart(text, flat, bodies):
    assert shell_words.read_script(text) == (flat, bodies)


@pytest.mark.parametrize("text, broken", REFUSED)
def test_the_reader_refuses_what_it_cannot_read_and_says_what_to_write(text, broken):
    start = time.perf_counter()
    with pytest.raises(shell_words.Unreadable) as refused:
        shell_words.read_script(text)
    assert time.perf_counter() - start < 1.0
    assert refused.value.broken is broken and refused.value.what and refused.value.instead


def test_a_substitution_is_found_where_the_shell_runs_one_and_nowhere_else():
    spans = shell_words.substitution_spans
    assert [body for _, _, body in spans("a $(b 'c)' d) `e` '$(f)' \"$(g)\" \\$(h) $((1+2))")] \
        == ["b 'c)' d", "e", "g"]
    assert [body for _, _, body in spans("'$(a)' \"it's $(b)\"", quoting=False)] == ["a", "b"]


FUZZ_PIECES = [
    "cat", ".env", "link.txt", " ", " ", "\n", "'", '"', "`", "$(", ")", "$((", "))", "((", "${",
    "}", "<<", "<<-", "<<<", "EOF", "\\", "#", ";", "&&", "||", "|", "&", ">", "<", "(", "{", "x=",
    "$x", "$'", "bash", "eval", "cd", "..", "-c", "git", "add", "-A", "grep", "-rn", "KEY", ".",
    "*", "[", "]", "?", "\t", "\r", "\0", "é", "__repact_sub_0__", "__repact_body_0__",
    "__repact_heredoc_0__", "case", "esac", "in", "a)", ";;", "ps", "aux", "source", "read",
    "export", "trap", "for", "do", "done", "if", "then", "fi", "env", "-C", ">(", "<(", "xargs",
]


def test_no_text_makes_the_checks_raise(tree):
    """600 strings of shell pieces in a fixed random order: every answer is a verdict."""
    order = random.Random(20261001)
    cwd = str(tree / "w" / "proj")
    for _ in range(600):
        text = "".join(order.choice(FUZZ_PIECES) for _ in range(order.randint(1, 40)))
        verdict = guard.bash_verdict(text, cwd)
        assert verdict is None or isinstance(verdict, str), text
        assert process_table_guard.command_is_listing(text) in (True, False), text
        try:
            flat, _ = shell_words.read_script(text)
        except shell_words.Unreadable:
            continue
        shell_words.substitution_spans(flat)


# ── 1.4.1 unit U7: the filed shapes, the bug audit's items, the false blocks ─────────────
EXC = "--exclude='.env*' --exclude='*.env' --exclude=.envrc --exclude=.netrc"
FAKE = "printf '#!/bin/sh\\ncat \"$1\"\\n' > env-get; chmod +x env-get; "
QUOTED_BODY = "<<'EOF'\ncat .env\nEOF\n)"
# (source, id, directory, command): allowed before 1.4.1, denied now.
U7_DENIED = [
    ("T-10984eac", "s6", "proj", "find . -type f | xargs grep -n KEY"),
    ("aud-2", "grp3", "proj", "{ cat; } < .env"),
    ("aud-12", "r2", "proj", "tr a a < .env"),
    ("aud-13", "v1", "proj", "source .env; echo ${KEY:0:99}"),
    ("adv", "grep-order", "proj", "grep -rn --include='*.txt' --exclude='.env*' --include='.e*' KEY ."),
]
# (id, directory, command): must stay allowed (false blocks the fixes above could cause, and
# the two read-only test counts an earlier review saw refused).
U7_ALLOWED = [
    ("eval-agent", "clean", 'eval "$(ssh-agent -s)"'),
    ("api-httpie", "proj", 'source .env; http https://x "X:$KEY"'),
    ("unicode-sh", "clean", "echo 'ls ./ünï' | sh"),
]


@pytest.mark.parametrize("source, probe, where, command", U7_DENIED,
                         ids=[f"{row[0]}-{row[1]}" for row in U7_DENIED])
def test_a_shape_unit_u7_closes_is_denied(tree, source, probe, where, command):
    assert reason(command, tree, where)


@pytest.mark.parametrize("probe, where, command", U7_ALLOWED, ids=[row[0] for row in U7_ALLOWED])
def test_a_shape_unit_u7_must_not_block_is_allowed(tree, probe, where, command):
    assert reason(command, tree, where) is None


def test_a_push_of_a_branch_with_no_commit_is_left_to_the_merge_guard(tmp_path):
    """an earlier review: a force push to main in a repository with no commit got a second deny
    ("could not verify") beside merge_guard_pre's. Nothing can be sent: git refuses it."""
    repo = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True,
                   capture_output=True, env={**os.environ, **GIT_ENV})
    push = "git " + "pu" + "sh"
    for command in (push + " --force origin main", push + " origin HEAD", push + " --all origin"):
        assert guard.bash_verdict(command, str(repo)) is None, command


def test_git_diff_of_a_tracked_changed_env_file_is_denied(tmp_path):
    """aud-bugs low item: `git diff` printed both versions of a tracked, changed .env."""
    repo = tmp_path / "tracked"
    repo.mkdir()
    (repo / ".env").write_text(SECRET, encoding="utf-8")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-f", ".env", "README.md"],
                 ["commit", "-q", "-m", "root"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
    (repo / ".env").write_text(SECRET + "MORE=1\n", encoding="utf-8")
    (repo / "README.md").write_text("y\n", encoding="utf-8")
    for command in ("git diff", "git diff HEAD", "git diff -p --stat", "git -C . diff HEAD"):
        assert "tracked or staged" in guard.bash_verdict(command, str(repo)), command
    for command in ("git diff --stat", "git diff --name-only HEAD", "git diff -- README.md"):
        assert guard.bash_verdict(command, str(repo)) is None, command
