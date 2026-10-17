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
    # team-lead
    "ps -o pid,ppid,etime,args -p 4242 | tail -n 1",
    "ps -ww -o args= -p 4242 | cut -c1-120",
    # architect (a listing inside a quoted grep pattern)
    "cd /work && grep -cE 'NAME|ps aux\\|NAME' session.jsonl",
    "cd /work && grep -c 'NAME\\|ps -o args' logs/a/*.jsonl logs/b/*.jsonl | sort -n",
    # fork-builder
    "sleep 1; pgrep -lx claude | wc -l",
    # preparer
    'cd /work && pgrep -fl "worker.py" | head -5',
    'cd /work && pgrep -fl "tools/bin/runner" -m "job" | head -5',
    'sleep 30; pgrep -fl "tools/bin/runner" -m "job" | wc -l',
    "sleep 60; pgrep -fl mutate.py",
    "sleep 5; pgrep -fl mutate.py | head -3",
    "sleep 5; pgrep -f mutate.py | wc -l",
    "sleep 5; pgrep -f mutate.py | head -1",
    # rehearsal
    'sleep 2; pgrep -fl "sandbox/[Cc]laude/home" | head',
    'sleep 2; pgrep -fl "sandbox/X/home" | head',
    'sleep 2; pgrep -fl "runner.py"; echo done',
    'sleep 2; pgrep -fl "sandbox/X/home bin" | head',
    'test -n "$(pgrep -f "claude" "$SANDBOX/cfg.json")" && echo up',
    'sleep 2; pgrep -fl "launcher.sh" | head',
    'sleep 2; pgrep -fl "launcher.sh home" | wc -l',
    'sleep 2; pgrep -fl "sandbox/[Cc]laude/bin" | head',
    # builder
    "ps -ef | grep -c NAME",
    "ps -ef | grep NAME | head -5",
    "ps -ef | wc -l",
]

MEASURED_SEGMENTS = [
    "ps -o pid,ppid,etime,args -p NAME", "ps -ww -o args= -p NAME", "rtk proxy ps -ww -o args= -p NAME",
    "ps NAME", "ps -o args' NAME NAME NAME NAME", "pgrep -lx NAME", "rtk proxy pgrep -lx NAME",
    "pgrep -fl NAME", "pgrep -fl NAME -m NAME", "pgrep -fl NAME", "pgrep -fl NAME -m NAME",
    "pgrep -fl NAME", "pgrep -fl NAME", "pgrep -f NAME", "pgrep -f NAME", "pgrep -fl NAME",
    "pgrep -fl NAME", "pgrep -fl NAME", "pgrep -fl NAME", "pgrep -fl NAME", "ps -o etime= -p NAME -f NAME NAME",
    "pgrep -fl NAME", "pgrep -fl NAME", "pgrep -fl NAME", "ps -ef", "ps -ef",
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
    "pgrep NAME", "pgrep -x NAME", "pgrep -u alfa NAME", "pgrep -P 4242", "pgrep -n NAME",
    "pkill NAME", "pkill -f NAME", "pkill -x NAME", "pkill -9 -f NAME",
    "ps -o pid= -o comm= -p 4242", "ps -eo pid,ppid,comm", "ps -A -o pid,stat,etime,comm",
    "ps -o %cpu,%mem,rss,vsz,comm -p 4242", "ps -axo pid,ppid,pgid,user,uid,tty,lstart,comm",
    "ps -o state=,time= -p 4242", "ps axo pid,comm", "ps -o rss= -p 4242 | awk '{print $1}'",
    "/bin/ps -axo pid=,comm=", "rtk ps -axo pid=,comm=", "rtk proxy pgrep -x NAME",
    "sysctl -n hw.ncpu", "psql -c 'select 1'", "man ps", "echo ps aux",
    'git commit -m "fix the ps aux habit"', "grep -c pgrep notes.md", "ls ps/",
    "true", "",
    "pgrep -l NAME", "pgrep -lx NAME", "pgrep --list-name NAME",
    "top -l 1 -o cpu", "top -b -n 1", "top -o mem -stats pid,rss",
    "cat /proc/meminfo", "cat /proc/4242/status", "ls /proc", "cat /proc/4242/cmdline.txt",
    "cat notes/proc/4242/cmdline",
] + [f"ps -o {keyword} -p 4242" for keyword in sorted(guard.PS_SAFE_KEYWORDS)]

DENY_GRID = [
    "ps", "ps -p 4242", "ps -A", "ps -e", "ps -axww", "ps aux", "ps auxww", "ps u -p 4242",
    "ps -O rss", "ps -o pid,comm -O rss", "ps -o args", "ps -o command=", "ps -o cmd", "ps -ocommand", "ps -o pid,args=",
    "ps --format pid,args", "ps --format=pid,cmd", "ps -eo pid,comm -f", "ps -l -o pid",
    "ps -o pid,wibble", "ps -o", "/bin/ps -ef", "\\ps aux", "`ps aux`", "echo $(ps -ef)",
    "FOO=1 ps aux", "env FOO=1 ps aux", "sudo ps -ef", "sudo -u root -H ps -ef",
    "nice -n 19 ps aux", "timeout -s KILL 5 pgrep -fl NAME", "watch -n 2 'ps aux'",
    "bash -c 'ps -ef'", "sh -c \"pgrep -f NAME\"", "xargs ps -o command= -p",
    "if pgrep -f NAME; then echo up; fi", "{ ps -ef; }", "true && ps aux || true",
    "pgrep -a NAME", "pgrep -lf NAME", "pgrep -fx NAME", "pgrep -u alfa -f NAME",
    "pgrep --list-full NAME", "pgrep --full NAME", "pkill -l NAME", "pkill -lf NAME",
    "sysctl kern.procargs2.4242", "sysctl -n kern.procargs", "ps -o etime= -p $(pgrep -f NAME)",
    "ls\nps -ef",
    # Lead ruling on task 103 (Q3): pstree, top -c, and reads of /proc/<pid>/cmdline.
    "pstree", "pstree -p 4242", "/usr/local/bin/pstree -w", "rtk proxy pstree",
    "top -c", "top -bc -n 1", "top -b -c -n 1", "sudo top -c",
    "cat /proc/4242/cmdline", "cat /proc/self/cmdline", "head -c 200 /proc/*/cmdline",
    "tail /proc/$PID/cmdline", "less /proc/4242/cmdline", "tr '\\0' ' ' < /proc/4242/cmdline",
    "xargs -0 < /proc/4242/cmdline", "strings /proc/4242/cmdline | head",
    "cat /proc/4242/environ",
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
    def test_the_measured_set_is_27_segments_in_12_shapes_and_23_commands(self):
        assert len(MEASURED_SEGMENTS) == 27
        assert len(MEASURED_SEGMENT_SHAPES) == 12
        assert len(MEASURED_COMMANDS) == 23

    @pytest.mark.parametrize("command", MEASURED_SEGMENTS)
    def test_every_measured_segment_is_denied_unless_relaxed_by_ruling(self, command):
        assert is_denied(command) is (command not in RELAXED_BY_RULING)

    @pytest.mark.parametrize("command", MEASURED_COMMANDS)
    def test_every_measured_command_is_denied_unless_relaxed_by_ruling(self, command):
        assert is_denied(command) is (command not in RELAXED_BY_RULING)

    def test_the_relaxation_covers_exactly_the_lx_and_grep_pattern_fixtures(self):
        denied = [c for c in MEASURED_SEGMENTS + MEASURED_COMMANDS if is_denied(c)]
        assert len(GREP_PATTERN_FIXTURES) == 2
        assert (len(denied), len(MEASURED_SEGMENTS + MEASURED_COMMANDS) - len(denied)) == (45, 5)

    @pytest.mark.parametrize("prefix", ["rtk ", "rtk proxy ", "command ", "env A=1 "])
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
    "cd \"/work/a b\" && /usr/bin/grep -n 'ps -ef\\|pgrep -f' scripts/check.sh",
    "rtk proxy grep -rn \"pgrep -fl\" docs",
    "nice -n 19 grep -c 'ps aux; pstree' notes.md",
    "if grep -q 'ps -ef | wc' notes.md; then echo found; fi",
    "egrep 'top -c|ps aux' notes.md",
    "grep 'it'\\''s; ps aux' notes.md",
]
# The bypasses the pattern rule could open: each still runs or reads a listing.
GREP_PATTERN_DENY = [
    "grep 'x' notes.md; ps aux",                      # a second command after the pattern
    "grep 'x' notes.md\nps aux",
    "grep 'x' notes.md \\\n; ps aux",
    "grep 'x' notes.md | xargs -I{} sh -c 'ps aux'",
    "grep \"$(ps aux)\" notes.md",                    # a live substitution inside the quotes
    "grep \"`ps aux`\" notes.md",
    "grep -e 'x' `ps aux`",
    "grep x <(ps aux)",
    "eval grep 'x; ps aux'",                          # a runner that parses the string again
    "watch grep 'x; ps aux'",
    "xargs grep 'x; ps aux'",
    "sudo grep 'x; ps aux' notes.md",
    "ssh host grep 'x; ps aux'",
    "'grep' 'x; ps aux'",                             # the program itself quoted
    "\"gr\"ep 'x; ps aux'",
    "rg 'x; ps aux' notes.md",                        # not grep: rg can run its --pre value
    "ｇrep 'x; ps aux'",                          # a look-alike name is not grep
    "grep 'x' /proc/1/cmdline",                       # grep reading the argv file itself
    "grep x '/proc/1/environ'",
    "bash -c \"grep x 'y'; ps aux\"",
    "grep 'unclosed; ps aux",
]


# The adversarial review's rows (finding R2): a quote mark the shell does not read as a quote
# (in a comment, in a $'..' string, on a heredoc body line) opened a quoted word that
# swallowed the listing on the next line. (probe id, main's verdict, command); all denied.
REVIEW_ROWS = [
    ("q1", "DENY", "grep -c foo log.txt  # don't count x\nps aux | head -3   # what's running"),
    ("q2", "DENY", "grep -c foo f # it's\nps -eo pid,args # isn't"),
    ("q3", "DENY", "grep -c foo f # \"\nps aux # \""),
    ("q4", "DENY", "grep $'a\\'b' f; ps aux # '"),
    ("q5", "DENY", "cat <<EOF\ngrep it's\nEOF\nps aux # '"),
    ("q6", "DENY", "grep -c foo f # '\necho \"$(ps aux)\" # '"),
    ("q7", "DENY", "grep -c foo f # '\npgrep -fl python # '"),
    ("q8", "DENY", "nice -n 19 grep -c foo f # '\nps aux # '"),
    ("q10", "DENY", "grep foo f \\\n # it's\nps aux # '"),
    # The same reader, other spellings: a body a shell runs, a typed word that looks like
    # the reader's own mark, a listing after a heredoc that never closes.
    ("b1", "DENY", "bash <<EOF\nps aux\nEOF"),
    ("b2", "DENY", "grep 'x' f <<__repact_heredoc_0__\nx\n__repact_heredoc_0__\nps aux"),
    ("b3", "DENY", "grep -c 'x' f # '\ncat <<EOF\nps aux"),
]
REVIEW_KEPT = [
    "time grep 'x; ps aux' f",                                   # r16: still a pattern
    "grep -c 'ps aux' f  # don't run it",
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
    'cat > notes.md <<"EOF"\npgrep -fl NAME\nEOF',
    "cat <<'EOF' > docs/notes.md\nps -ef | head\nEOF",
    "tee -a notes.md <<'EOF' >/dev/null\nps aux\nEOF",
    "cat > notes.md <<'EOF'\nps aux\nEOF\ngit add notes.md && wc -l notes.md",
]
HEREDOC_TO_FILE_DENY = [
    "cat > notes.md <<EOF\n$(ps aux)\nEOF",                      # a bare delimiter expands
    "cat > notes.md <<EOF\nps aux\nEOF",                          # bare: not taken as data
    "cat <<'EOF' | sh\nps aux\nEOF",                              # piped to a shell
    "tee notes.md <<'EOF' | bash\nps aux\nEOF",
    "cat <<'EOF' > >(sh)\nps aux\nEOF",                           # a process substitution
    "cat > run.sh <<'EOF'\nps aux\nEOF\nbash run.sh",             # the written file runs
    "cat > run.sh <<'EOF' && sh run.sh\nps aux\nEOF",
    "cat > run.sh <<'EOF'\nps aux\nEOF\n. run.sh",
    "cat > run.sh <<'EOF'\nps aux\nEOF\nsource ./run.sh",
    "cat > run.sh <<'EOF'\nps aux\nEOF\nchmod +x run.sh; ./run.sh",
    "cat > run.sh <<'EOF'\nps aux\nEOF\nsh < run.sh",
    "cat > run.sh <<'EOF'\nps aux\nEOF\nhead run.sh | sh",        # a reader piped onward
    "cat > run.sh <<'EOF'\nps aux\nEOF\ngit -c core.pager='sh run.sh' log",
    "cat > run.sh <<'EOF'\nps aux\nEOF\nbash -c 'sh run.sh'",
    "cat > run.sh <<'EOF'\nps aux\nEOF\nbash <<'X'\nsh run.sh\nX",  # another body runs it
    "cat > run.sh <<'EOF'\nps aux\nEOF\neval \"$(cat run.sh)\"",
    "python3 - <<'EOF' > out.txt\nimport os; os.system('ps aux')\nEOF",  # not a writer
    "sudo tee notes.md <<'EOF'\nps aux\nEOF",                     # a runner before the writer
    "cat notes.md - > out.md <<'EOF'\nps aux\nEOF",               # cat with operands
    "cat > notes.md <<'EOF'\nok\nEOF\nps aux",                    # the listing on the line
    "cat > \"$(ps aux | head -1)\" <<'EOF'\nok\nEOF",             # the listing in $(...)
    "cat > notes.md <<'EOF' && pgrep -fl NAME | wc -l\nok\nEOF",
    # A target a program runs without naming it, or a name reached by a glob or by case.
    "cat <<'EOF' > Makefile\nall:\n\tps aux\nEOF\nmake",
    "cat <<'EOF' > conftest.py\nimport os; os.system('ps aux')\nEOF\npytest -q",
    "cat <<'EOF' > .git/hooks/post-commit\nps aux\nEOF\ngit commit -qm x",
    "cat <<'EOF' > .git/notes.md\nps aux\nEOF",
    "cat <<'EOF' > package.json\nps aux\nEOF\nnpm test",
    "cat <<'EOF' > .pre-commit-config.yaml\nps aux\nEOF\npre-commit run",
    "cat <<'EOF' > run.sh\nps aux\nEOF\nbash r?n.sh",
    "cat <<'EOF' > notes.md\nps aux\nEOF\nbash n?tes.md",
    "cat <<'EOF' > notes.md\nps aux\nEOF\nsh *",
    "cat <<'EOF' > d/notes.md\nps aux\nEOF\ncd d* && sh notes.md",
    "cat <<'EOF' > notes.md\nps aux\nEOF\nbash {notes,x}.md",
    "cat <<'EOF' > notes.md\nps aux\nEOF\nbash NOTES.MD",
]
# Data files written by a heredoc stay data beside a glob that cannot reach them.
HEREDOC_TO_DATA_ALLOW = [
    "cat >> /tmp/scratch/carry.md <<'EOF'\n- ps aux printed command lines\nEOF",
    "cat > out.json <<'EOF'\n{\"cmd\": \"ps aux\"}\nEOF",
    "tee -a log.txt notes.yaml <<'EOF' >/dev/null\nps aux\nEOF",
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
        "not json", "[]", "{}", {"tool_name": "Read", "tool_input": {"command": "ps -ef"}},
        {"tool_name": "Bash"}, {"tool_name": "Bash", "tool_input": {"command": None}},
        {"tool_name": "Bash", "tool_input": "ps -ef"},
    ])
    def test_unreadable_or_foreign_payloads_fail_open(self, payload):
        result = run_hook(payload)
        assert result.returncode == 0
        assert "permissionDecision" not in result.stdout

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
        assert self._imports(HOOK) == {"json", "sys", "shell_words"}

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
