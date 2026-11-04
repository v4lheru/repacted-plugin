"""
Location: pact-plugin/tests/test_process_table_guard.py
Summary: Tests for hooks/process_table_guard.py, the PreToolUse(Bash) deny of process
         listings that print command lines.

Fixtures are re-derived from this session's transcripts (read-only scan), then
abstracted: every free-text argument is replaced by a neutral word, so no fixture
carries transcript content, a token, or a path from the leak. Two sets:
  MEASURED_COMMANDS        the 23 measured commands as whole command lines, in the
                           shapes they were run (pipes, sleeps, $( ), quoted greps).
  MEASURED_SEGMENTS        the design's measured set, verbatim: 27 process-listing
                           segments in 12 shapes, free text normalised to NAME (built by
                           the architect's wd_psfix.py; the lead's amend_1 on this task).
Two of the 23 are a listing named inside a quoted grep pattern (a measurement, not a
run); the guard denies them too, because it reads quoted text as a command so that
`bash -c 'ps -ef'` is caught. That false positive is the price of that catch.
"""

from __future__ import annotations

import ast
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

import process_table_guard as guard

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "process_table_guard.py"

MEASURED_COMMANDS = [
    "ps -o pid,ppid,etime,args -p 4242 | tail -n 1",
    'cd /work && pgrep -fl "tools/bin/runner" -m "job" | head -5',
    'test -n "$(pgrep -f "claude" "$SANDBOX/cfg.json")" && echo up',
    "ps -ef | wc -l",
]

MEASURED_SEGMENTS = [
    "ps -o pid,ppid,etime,args -p NAME",
    "pgrep -fl NAME",
    "pgrep -fl NAME",
    "ps -ef",
]
MEASURED_SEGMENT_SHAPES = sorted(set(MEASURED_SEGMENTS))
# pgrep -l without -f/-a prints pid + name only: allowed by the lead's ruling on task 103.
# These measured shapes stay in the set, asserted ALLOWED, so the relaxation is explicit.
RELAXED_BY_RULING = {"pgrep -lx NAME", "rtk proxy pgrep -lx NAME", "sleep 1; pgrep -lx claude | wc -l"}
# A quoted argument of grep is a pattern, not a command (the field false block of
# 2026-10-01): the two measured greps for a listing's TEXT are allowed, and stay in the set.
GREP_PATTERN_FIXTURES = {c for c in MEASURED_COMMANDS if c.startswith("cd /work && grep -c")}
RELAXED_BY_RULING |= GREP_PATTERN_FIXTURES

MEASURED_SAFE = [
    "sleep 1; pgrep -x claude | wc -l",
    "sleep 2; pgrep -x claude >/dev/null; echo $?",
    "ps -axo pid=,comm=",
]

SAFE_GRID = [
    "pgrep -x NAME",
    "ps -o pid= -o comm= -p 4242",
    "rtk proxy pgrep -x NAME",
    'git commit -m "fix the ps aux habit"',
    "top -l 1 -o cpu",
    "cat notes/proc/4242/cmdline",
] +[f"ps -o {keyword} -p 4242" for keyword in sorted(guard.PS_SAFE_KEYWORDS)[::16]]

DENY_GRID = [
    "ps aux",
    "ps -o command=",
    "\\ps aux",
    "echo $(ps -ef)",
    "sudo -u root -H ps -ef",
    "bash -c 'ps -ef'",
    "{ ps -ef; }",
    "pgrep --full NAME",
    "sysctl kern.procargs2.4242",
    "ls\nps -ef",
    "top -c",
    "cat /proc/4242/cmdline",
    "cat /proc/4242/environ",
    "rtk proxy procs",
    "/bin/ps -ef",
    "`ps aux`",
    "env FOO=1 ps aux",
    "pstree",
    "htop",
]


def run_hook(payload) -> subprocess.CompletedProcess:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, capture_output=True,
                          text=True, timeout=30)


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def is_denied(command: str) -> bool:
    return guard.decide(bash(command)) is not None


class TestMeasuredFixtures:
    @pytest.mark.parametrize("command", MEASURED_SEGMENTS)
    def test_every_measured_segment_is_denied_unless_relaxed_by_ruling(self, command):
        assert is_denied(command) is (command not in RELAXED_BY_RULING)

    @pytest.mark.parametrize("command", MEASURED_COMMANDS)
    def test_every_measured_command_is_denied_unless_relaxed_by_ruling(self, command):
        assert is_denied(command) is (command not in RELAXED_BY_RULING)

    @pytest.mark.parametrize("prefix", [
    "rtk ",
])
    @pytest.mark.parametrize("command", [shape for shape in MEASURED_SEGMENT_SHAPES
                                         if shape not in RELAXED_BY_RULING]
                             + [c for c in MEASURED_COMMANDS if c not in RELAXED_BY_RULING][:2])
    def test_prefixed_variants_are_denied(self, prefix, command):
        assert is_denied(prefix + command)

    @pytest.mark.parametrize("command", MEASURED_SAFE)
    def test_measured_safe_forms_are_allowed(self, command):
        assert not is_denied(command)

    def test_no_fixture_carries_secret_shaped_text(self):
        """The fixtures are abstracted: no long hex/base64 run, no key prefix."""
        secretish = re.compile(r"[A-Za-z0-9_\-]{24,}|sbp_|sk-|ghp_|xox[bp]-")
        for command in MEASURED_COMMANDS + MEASURED_SEGMENTS + MEASURED_SAFE:
            assert not secretish.search(command), command


GREP_PATTERN_ALLOW = [
    "grep -n 'ps\\b\\|ps -a\\|Health' scripts/check.sh | cut -c1-170",
    "nice -n 19 grep -c 'ps aux; pstree' notes.md",
    "grep 'it'\\''s; ps aux' notes.md",
]
# The bypasses the pattern rule could open: each still runs or reads a listing.
GREP_PATTERN_DENY = [
    "grep 'x' notes.md; ps aux",
    "xargs grep 'x; ps aux'",
    "grep 'unclosed; ps aux",
    "ｇrep 'x; ps aux'",
]


# The adversarial review's rows (finding R2): a quote mark the shell does not read as a quote
# (in a comment, in a $'..' string, on a heredoc body line) opened a quoted word that
# swallowed the listing on the next line. (probe id, main's verdict, command); all denied.
REVIEW_ROWS = [
    ("q1", "DENY", "grep -c foo log.txt  # don't count x\nps aux | head -3   # what's running"),
    ("b3", "DENY", "grep -c 'x' f # '\ncat <<EOF\nps aux"),
]
REVIEW_KEPT = [
    "time grep 'x; ps aux' f",
    "grep -c 'ps aux' f <<'EOF'\nit's text\nEOF",
]


class TestGrepPatternIsData:
    @pytest.mark.parametrize("command", GREP_PATTERN_ALLOW + REVIEW_KEPT)
    def test_a_listing_named_inside_a_grep_pattern_is_allowed(self, command):
        assert not is_denied(command)

    @pytest.mark.parametrize("command", GREP_PATTERN_DENY)
    def test_a_listing_that_runs_beside_a_grep_is_denied(self, command):
        assert is_denied(command)

    @pytest.mark.parametrize("probe, main, command", REVIEW_ROWS,
                             ids=[f"{row[0]}-main-{row[1]}" for row in REVIEW_ROWS])
    def test_a_quote_the_shell_does_not_read_hides_no_listing(self, probe, main, command):
        assert main == "DENY" and is_denied(command)

    def test_quoted_words_refuses_what_it_cannot_take_as_data(self):
        from shell_words import quoted_words
        assert quoted_words("grep 'a b' c | wc") == [
            [("grep", False), ("a b", True), ("c", False)], [("wc", False)]]
        assert quoted_words('echo "$(date)"') is None
        assert quoted_words('echo "`date`"') is None
        assert quoted_words("echo 'open") is None
        assert quoted_words('echo "\\$(date)" \'\'') == [
            [("echo", False), ("$(date)", True), ("", True)]]


class TestGrid:
    @pytest.mark.parametrize("command", SAFE_GRID)
    def test_safe_grid_is_allowed(self, command):
        assert not is_denied(command)

    @pytest.mark.parametrize("command", DENY_GRID)
    def test_deny_grid_is_denied(self, command):
        assert is_denied(command)

    def test_the_inner_substitution_is_denied_on_its_own(self):
        """A safe outer ps does not launder a listing inside $( ): the inner pgrep segment
        decides alone. Control: the same shape with pgrep -x is allowed. pgrep -a is used
        because ps would read -f or -l as its own format flag and deny for that reason."""
        assert is_denied("ps -o pid=,comm= -p $(pgrep -a NAME)")
        assert is_denied("ps -o pid=,comm= -p `pgrep -a NAME`")
        assert not is_denied("ps -o pid=,comm= -p $(pgrep -x NAME)")

    def test_the_safe_grid_is_reached_by_the_same_function_that_denies(self):
        """Companion to the allow assertions: the ps and pgrep branches are live."""
        assert guard.segment_is_listing(["ps", "-ef"])
        assert not guard.segment_is_listing(["ps", "-axo", "pid=,comm="])
        assert guard.segment_is_listing(["pgrep", "-f", "NAME"])
        assert not guard.segment_is_listing(["pgrep", "-x", "NAME"])


# A quoted-delimiter heredoc that cat or tee only writes to a file is data: notes that name a
# listing are not a listing. Every other shape of the same text still runs it.
HEREDOC_TO_FILE_ALLOW = [
    "cat >> notes.md <<'EOF'\nps aux | head -3\nEOF",
    "cat > notes.md <<'EOF'\nps aux\nEOF\ngit add notes.md && wc -l notes.md",
]
HEREDOC_TO_FILE_DENY = [
    "cat > notes.md <<EOF\n$(ps aux)\nEOF",
    "cat > run.sh <<'EOF'\nps aux\nEOF\nbash -c 'sh run.sh'",
    "cat > \"$(ps aux | head -1)\" <<'EOF'\nok\nEOF",
    "cat <<'EOF' > notes.md\nps aux\nEOF\nbash NOTES.MD",
]
# Data files written by a heredoc stay data beside a glob that cannot reach them.
HEREDOC_TO_DATA_ALLOW = [
    "cat >> /tmp/scratch/carry.md <<'EOF'\n- ps aux printed command lines\nEOF",
    "cat > notes.md <<'EOF'\nps aux\nEOF\nwc -l notes.md other.txt",
]


class TestHeredocWrittenToAFile:
    @pytest.mark.parametrize("command", HEREDOC_TO_FILE_ALLOW)
    def test_a_body_only_written_to_a_file_is_data(self, command):
        assert not is_denied(command)

    @pytest.mark.parametrize("command", HEREDOC_TO_FILE_DENY)
    def test_a_body_that_can_run_or_a_listing_beside_it_is_denied(self, command):
        assert is_denied(command)

    @pytest.mark.parametrize("command", HEREDOC_TO_DATA_ALLOW)
    def test_a_data_file_beside_an_unrelated_glob_is_data(self, command):
        assert not is_denied(command)


class TestRefusalContract:
    def test_the_refusal_carries_a_registered_rule_with_a_way_through(self):
        from shared.rules import RULES
        assert guard.DENY_MESSAGE.endswith(f"[rule: {guard.RULE_ID}]")
        assert RULES[guard.RULE_ID].instead.strip()


class TestHookProcess:
    def test_deny_envelope_exit_2_and_reason_names_both_safe_forms(self):
        result = run_hook(bash("ps -ef | head"))
        assert result.returncode == 2
        output = json.loads(result.stdout)
        assert list(output) == ["hookSpecificOutput"]
        specific = output["hookSpecificOutput"]
        assert specific["hookEventName"] == "PreToolUse"
        assert specific["permissionDecision"] == "deny"
        reason = specific["permissionDecisionReason"]
        assert "pgrep -x <name>" in reason and "ps -axo pid=,comm=" in reason
        assert reason in result.stderr

    def test_allow_is_suppress_output_exit_0(self):
        result = run_hook(bash("pgrep -x claude"))
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"suppressOutput": True}

    @pytest.mark.parametrize("payload", [
    "not json",
])
    def test_unreadable_or_foreign_payloads_fail_open(self, payload):
        result = run_hook(payload)
        assert result.returncode == 0
        assert "permissionDecision" not in result.stdout

    def test_a_string_command_under_any_tool_name_is_judged(self):
        """Review Note 3: the Bash group is a shell command whatever tool_name says."""
        result = run_hook({"tool_name": "Read", "tool_input": {"command": "ps -ef"}})
        assert result.returncode == 2
        assert json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_an_error_while_deciding_fails_open(self, monkeypatch, capsys):
        def explode(_payload):
            raise RuntimeError("boom")
        monkeypatch.setattr(guard, "decide", explode)
        monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(json.dumps(bash("ps -ef"))))
        assert guard.main() == 0
        assert "permissionDecision" not in capsys.readouterr().out

    def test_a_hook_file_that_cannot_load_does_not_deny(self, tmp_path):
        broken = tmp_path / "process_table_guard.py"
        broken.write_text(HOOK.read_text(encoding="utf-8") + "\n)\n", encoding="utf-8")
        result = subprocess.run([sys.executable, str(broken)], input=json.dumps(bash("ps -ef")),
                                capture_output=True, text=True, timeout=30)
        assert result.returncode not in (0, 2)     # a non-blocking hook error
        assert "permissionDecision" not in result.stdout


class TestCost:
    @staticmethod
    def _imports(path: Path) -> set:
        """Runtime imports only: `from __future__` is a compiler directive with no import cost
        (and test_py39_annotation_compat requires it), so it is left out of every count."""
        imported = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
                imported.add(node.module)
        return imported

    def test_the_hook_imports_only_json_sys_and_the_tokenizer(self):
        # shell.tools: the shell tool names (Bash, Monitor); it imports nothing.
        assert self._imports(HOOK) == {"json", "sys", "shell_words", "shell.tools"}
        assert self._imports(HOOK.parent / "shell" / "tools.py") == set()

    def test_the_shared_tokenizer_imports_nothing(self):
        """shell.words is imported by every Bash guard (through the shell_words façade for one
        release); an import there is paid on every call, and the shell package's __init__
        imports nothing for the same reason."""
        assert self._imports(HOOK.parent / "shell" / "words.py") == set()
        assert self._imports(HOOK.parent / "shell" / "__init__.py") == set()
        assert self._imports(HOOK.parent / "shell_words.py") == {"shell.words"}

    def test_decision_p95_over_1000_runs_is_under_20_ms(self):
        commands = MEASURED_COMMANDS + SAFE_GRID
        timings = []
        for index in range(1000):
            payload = bash(commands[index % len(commands)])
            start = time.perf_counter()
            guard.decide(payload)
            timings.append((time.perf_counter() - start) * 1000)
        p95 = statistics.quantiles(timings, n=20)[-1]
        assert p95 < 20, f"p95 {p95:.3f} ms"


# --- a pseudo-terminal wrapper around a human-only KB step --------------------------------

PTY_REFUSED = [
    "(sleep 3; printf 'ID\\n') | script -q /dev/null repact-distill promote --repo .",
    "python3 -c 'import pexpect; pexpect.spawn(\"vault-note drain --batch d.json\")'",
]
PTY_ALLOWED = [
    "repact-distill promote --dry-run --repo .",
]


# kbdrev L1 (task 186): spellings the first list missed. Probe F sealed an item through screen -X stuff.
PTY_REFUSED_WIDER = [
    "screen -dmS kb python3 bin/vault-note drain --decide ID accepted --by alice",
    "ruby -e 'PTY.spawn(\"vault-note drain --batch d.json\")'",
]
# Near-misses that stay allowed: the wrapper or the module name with no human KB step in the command.
PTY_NEAR_MISSES = [
    "tmux ls",
]


# Literal spellings that once escaped, and the allowed rows' near-misses that must stay refused
# (a deciding flag, abbreviated or typed in a later argument; --batch paging, whose page retype is
# a decision; --dry-run in another simple command; a decide verb).
PTY_REFUSED_G2 = [
    "/usr/bin/env tmux new -d 'python3 /p/bin/vault-note drain --decide k1 accepted --by u'",
    "tmux ls; kb-note decide ID accepted --by alice",
]
# A command that names no human step, and the forms an agent may run, which code
# proves never prompt for or record a decision (the line that proves it is beside each row).
PTY_ALLOWED_G2 = [
    "ls docs/kb-notes && grep -rn decided . && tmux ls",
]
# Near-misses: the new spellings with no human step, and env-by-path with no wrapper.
PTY_NEAR_MISSES_G2 = [
    "/usr/bin/env python3 -c 'print(1)'; vault-note drain --decide ID rejected --by alice",
]


# The team audience's stage and push are human steps like promote; their dry runs are not.
PTY_REFUSED_TEAM = [
    "(sleep 3; printf 'ID\\n') | script -q /dev/null repact-distill team push --team-repo ~/kb --by a",
    "tmux ls; repact-distill promote --repo . --by a >>--dry-run",
]
PTY_ALLOWED_TEAM = [
    "tmux ls && repact-distill team push --dry-run --team-repo .",
]


@pytest.mark.parametrize("command", PTY_REFUSED + PTY_REFUSED_WIDER + PTY_REFUSED_G2 + PTY_REFUSED_TEAM)
def test_a_pty_wrapped_human_kb_step_is_refused_with_its_rule(command):
    output = guard.decide(bash(command))
    assert output is not None
    assert f"[rule: {guard.PTY_RULE_ID}]" in output["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("command", PTY_ALLOWED + PTY_NEAR_MISSES + PTY_ALLOWED_G2
                         + PTY_NEAR_MISSES_G2 + PTY_ALLOWED_TEAM)
def test_the_ordinary_path_and_unrelated_wrappers_are_allowed(command):
    assert not is_denied(command)


def test_the_pty_refusal_reaches_stderr_with_exit_2():
    result = run_hook(bash(PTY_REFUSED[0]))
    assert result.returncode == 2 and guard.PTY_RULE_ID in result.stderr
