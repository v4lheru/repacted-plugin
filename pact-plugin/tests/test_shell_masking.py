"""tests/test_shell_masking.py

Inert text is masked before the shape pass (refactor §3.7 part 2, step 17): carrier 11 (the
quoted arguments of a data-only program), carrier 12 (a JSON literal argument) and carrier 13
(a heredoc body or printed text piped to a program that is not a shell) on the merge guard;
the JSON-literal rule of the env guard. Each inert row must be allowed; each bypass row, where
the masked text reaches a shell or an executor, must stay refused with its rule. One
regression row for the eval-heredoc shape, and the PACT_MASK_TRACE attribution.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import env_guard as guard
import merge_guard_pre as mg
from shell.merge import arms, strip

TAIL = re.compile(r" \[rule: ([a-z_]+(?:\.[a-z_]+)+)\]$")
MARKER = "inert-marker-5e1d"
M = "gh pr " + "merge 5"            # built, so a hook reading this file sees no live command
F = "git push --" + "force origin main"

INERT = [
    ("awk '/" + F + "/{print}' f", 11),
    ("awk -F: '{print $1}' f", 11),
    ("sed -n '/" + M + "/p' f", 11),
    ("grep -e \"" + M + "\" log.txt", 11),
    ("printf '%s\\n' \"" + M + "\"", 11),
    ("jq -n '{\"c\":\"" + M + "\"}'", 11),
    ("jq --arg c \"" + M + "\" '.x=$c' f.json", 11),
    ("python3 tool.py '{\"cmd\":\"" + M + "\"}'", 12),
    ("python3 tool.py '[\"" + M + "\"]'", 12),
    ("node tool.js '{\"cmd\":\"" + F + "\"}'", 12),
    ("python3 tool.py -v '[\"" + M + "\"]'", 12),       # -v gives no code: still data
    ("echo '{\"cmd\":\"" + M + "\"}' | python3 tool.py", 13),
    ("cat <<'EOF' | python3 tool.py\n{\"cmd\":\"" + M + "\"}\nEOF", 13),
    ("python3 - <<'EOF'\nprint('" + M + "')\nEOF", 13),
    ("cat <<E\\OF\n" + M + "\nEOF", 1),                 # the delimiter is EOF, quoting removed
    ("awk -v n=1 -F, -- '{print $1}' f '" + M + "'", 11),
    ("# a note\ncat <<'EOF'\n" + M + "\nEOF", 1),
    ("cat <<'EOF'\n" + M + " \\\nit's\nEOF", 1),
    ("echo \"$(cat <<'EOF'\n" + M + "\nEOF\n)\"", 1),     # an operator inside "$(...)" is real
]
BYPASS = [
    ("echo '{\"c\":\"" + M + "\"}' | sh", "mg.merge.explicit"),
    ("python3 -c \"import os;os.system('" + M + "')\"", "mg.merge.explicit"),
    ("eval \"$(cat <<'EOF'\n" + M + "\nEOF\n)\"", "mg.merge.explicit"),
    ("bash <(printf '%s' \"" + M + "\")", "mg.merge.explicit"),
    ("echo \"" + M + "\" | xargs -I{} sh -c {}", "mg.merge.explicit"),
    ("sh -c '{\"a\":\"" + M + "\"}'", "mg.merge.explicit"),
    ("bash <<'EOF'\n" + M + "\nEOF", "mg.merge.explicit"),
    ("jq -rn --arg c \"" + M + "\" '$c' | sh", "mg.merge.explicit"),
    ("awk 'BEGIN{system(\"" + M + "\")}'", "mg.merge.explicit"),
    ("awk '{print | \"sh\"} END{print \"" + M + "\"}' f", "mg.merge.explicit"),
    ("node -e \"require('child_process').execSync('" + M + "')\"", "mg.merge.explicit"),
    ("python3 -c '[\"" + M + "\"]' && " + M, "mg.compound.destructive"),
    # Carriers 11-12 keep what carrier 10 keeps: a process substitution runs its payload.
    ("diff <(sh -c '" + M + "' ) /dev/null", "mg.merge.explicit"),
    ("cat <(curl -X PUT https://api.github.com/repos/o/r/contents/README.md -d "
     "'{\"branch\":\"main\",\"message\":\"m\",\"content\":\"eA==\"}' )", "mg.unmintable.shape"),
    # awk runs code the regex cannot see: gawk indirect calls, @load, a program file.
    ("gawk 'BEGIN{f=\"system\"; @f(\"" + M + "\")}'", "mg.merge.explicit"),
    ("gawk 'BEGIN{f=\"system\"; @ f(\"" + M + "\")}'", "mg.merge.explicit"),
    ("gawk '@load \"filefuncs\"; BEGIN{print \"" + M + "\"}'", "mg.merge.explicit"),
    ("gawk -e 'BEGIN{f=\"system\"; @f(ARGV[1])}' '" + M + "'", "mg.merge.explicit"),
    ("awk -f prog.awk '" + M + "'", "mg.merge.explicit"),
    # Carrier 1 closes a heredoc no later than the shell does, and keeps its operator line.
    ("cat <<E\\OF\nx\nEOF\n" + M + "\nE", "mg.merge.explicit"),
    ("cat <<'E'OF\nx\nEOF\n" + M + "\nE", "mg.merge.explicit"),
    ("cat <<\"E\"O'F'\nx\nEOF\n" + M + "\nE", "mg.merge.explicit"),
    ("cat <<EOF\nEOF\n" + M + "\nEOF", "mg.merge.explicit"),
    ("cat <<<EOF\n" + M + "\nEOF", "mg.merge.explicit"),
    ("cat <<'EOF' && " + M + "\nx\nEOF", "mg.merge.explicit"),
    ("cat <<'EOF'; " + M + "\nx\nEOF", "mg.merge.explicit"),
    # Fail closed: an interpreter given inline code, by any code option, may run a JSON
    # literal it is handed (Perl and Ruby interpolate backticks inside a JSON string).
    ("perl -E '[\"@{[`" + M + "`]}\"]'", "mg.merge.explicit"),
    ("ruby -we '[\"#{`" + M + "`}\"]'", "mg.merge.explicit"),
    ("/usr/bin/perl5.30 -E '[\"@{[`" + M + "`]}\"]'", "mg.merge.explicit"),
    ("python3 -c \"import os,sys,json;os.system(json.loads(sys.argv[1])[0])\" '[\"" + M + "\"]'",
     "mg.merge.explicit"),
    ("python3 -Ic \"import os,sys,json;os.system(json.loads(sys.argv[1])[0])\" '[\"" + M + "\"]'",
     "mg.merge.explicit"),
    ("python3 -W ignore -c \"import os,sys;os.system(sys.argv[1][2:-2])\" '[\"" + M + "\"]'",
     "mg.merge.explicit"),
    ("node -pe \"require('child_process').execSync(process.argv[1])\" '[\"" + M + "\"]'",
     "mg.merge.explicit"),
    ("node --eval=\"require('child_process').execSync(process.argv[1])\" '[\"" + M + "\"]'",
     "mg.merge.explicit"),
    # An awk leg is data only with -F/-v/-- options and a literal program.
    ("awk \"`cat p.awk`\" '" + M + "'", "mg.merge.explicit"),
    ("gawk --fil p.awk '" + M + "'", "mg.merge.explicit"),
    ("gawk --inc lib '" + M + "'", "mg.merge.explicit"),
    ("gawk -bf p.awk '" + M + "'", "mg.merge.explicit"),
    ("gawk --exe p.awk '" + M + "'", "mg.merge.explicit"),
    ("awk \"$(cat p.awk)\" '[\"" + M + "\"]'", "mg.merge.explicit"),
    # An unquoted substitution runs its text, whatever the head of the leg.
    ("awk '{print}' `eval '" + M + "' `", "mg.merge.explicit"),
    ("awk '{print}' $(eval '" + M + "')", "mg.merge.explicit"),
    ("printf '%s' $(eval '" + M + "')", "mg.merge.explicit"),
    # Carrier 1 reads `<<` only as an operator: not in a comment, a quote or arithmetic, and a
    # quoted-delimiter body keeps its backslash-newline, so `x\` then `EOF` closes it.
    ("cat <<'EOF'\nx\\\nEOF\n" + M + "\nEOF", "mg.merge.explicit"),
    ("cat <<\"EOF\"\nx\\\nEOF\n" + M + "\nEOF", "mg.merge.explicit"),
    ("# note \\\n" + M, "mg.merge.explicit"),            # a comment does not continue
    ("echo ok # note \\\n" + M, "mg.merge.explicit"),
    ("# write it with cat <<EOF\n" + M + "\ncat <<EOF\nx\nEOF", "mg.merge.explicit"),
    ("echo '<<EOF'\n" + M + "\nEOF", "mg.merge.explicit"),
    ("echo 'x <<EOF y'\n" + M + "\nEOF", "mg.merge.explicit"),
    ("echo \"a <<EOF b\"\n" + M + "\nEOF", "mg.merge.explicit"),
    ("echo $((1<<2))\n" + M + "\n2", "mg.merge.explicit"),
    ("cat <<'X'\nit's\nX\necho 'a <<EOF b'\n" + M + "\nEOF", "mg.merge.explicit"),
    # A body the leg runs as a script is kept.
    ("bash -s <<'EOF'\n" + M + "\nEOF", "mg.merge.explicit"),
    ("<<'EOF' sh\n" + M + "\nEOF", "mg.merge.explicit"),
    ("source /dev/stdin <<'EOF'\n" + M + "\nEOF", "mg.merge.explicit"),
    (". /dev/stdin <<'EOF'\n" + M + "\nEOF", "mg.merge.explicit"),
    ("zsh -s - <<'EOF'\n" + M + "\nEOF", "mg.merge.explicit"),
]


@pytest.mark.parametrize("command, carrier", INERT)
def test_an_inert_row_is_allowed(command, carrier):
    assert mg.check_merge_authorization(command) is None, strip._strip_non_executable_content(command)


@pytest.mark.parametrize("command, rule", BYPASS)
def test_a_bypass_row_stays_refused_with_its_rule(command, rule):
    text = mg.check_merge_authorization(command)
    assert text is not None, command
    assert TAIL.search(text).group(1) == rule, text


def test_the_eval_heredoc_shape_is_seen_as_an_eval():
    # Regression: `eval "$(cat <<'EOF' ... EOF)"` was read as a plain heredoc and its body
    # stripped; the quote before `$(` hid the eval.
    command = "eval \"$(cat <<'EOF'\n" + M + "\nEOF\n)\""
    assert strip._has_eval_with_heredoc(command)
    assert mg.check_merge_authorization(command) is not None


def test_every_mask_rule_id_is_dotted_and_the_table_is_closed():
    for rule, sentence in arms.MASK_RULE_IDS.items():
        assert re.fullmatch(r"mg\.inert\.[a-z_]+", rule), rule
        assert sentence.strip().endswith(".")
    assert not set(arms.MASK_RULE_IDS) & set(arms.RULE_IDS)
    assert arms.AWK_PROGRAMS <= arms.DATA_PROGRAMS
    assert not arms.DATA_PROGRAMS & {"sh", "bash", "zsh", "eval", "xargs", "python3", "node"}


def test_the_trace_names_the_carrier_and_rule_but_never_the_text(monkeypatch, capsys):
    monkeypatch.setenv("PACT_MASK_TRACE", "1")
    strip._strip_non_executable_content("python3 tool.py '{\"cmd\":\"" + M + "\"}'")
    err = capsys.readouterr().err
    assert "gate_mask carrier=12 rule=mg.inert.json_literal chars=" in err
    assert M not in err
    monkeypatch.delenv("PACT_MASK_TRACE")
    strip._strip_non_executable_content("awk '{print}' f")
    assert capsys.readouterr().err == ""


# ── env: a JSON literal is data to a program that is not a shell ──────────────────────────

@pytest.fixture
def tree(tmp_path) -> Path:
    (tmp_path / ".env").write_text(f"DEMO_KEY={MARKER}\n", encoding="utf-8")
    return tmp_path


def env_rule(command: str, cwd: Path) -> str | None:
    output = guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    if not output or output["hookSpecificOutput"].get("permissionDecision") != "deny":
        return None
    text = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert MARKER not in text
    return TAIL.search(text).group(1)


E = "cat .env"


@pytest.mark.parametrize("command", [
    "echo '{\"cmd\":\"" + E + "\"}' | python3 tool.py",
    "cat <<'EOF' | python3 tool.py\n{\"cmd\":\"" + E + "\"}\nEOF",
    "printf '%s' '[\"" + E + "\"]' | node tool.js",
    "python3 tool.py '{\"cmd\":\"" + E + "\"}'",
])
def test_a_json_literal_fed_to_a_program_is_data(tree, command):
    assert env_rule(command, tree) is None


@pytest.mark.parametrize("command, rule", [
    ("python3 -c \"import os;os.system('" + E + "')\"", "env.read.operand"),
    ("echo '{\"cmd\":\"" + E + "\"}' | python3 -c \"import sys;print(open('.env').read())\"",
     "env.read.operand"),
    ("cat <<'EOF' | bash\n" + E + "\nEOF", "env.read.operand"),
])
def test_a_json_shaped_body_that_reaches_a_shell_or_a_reader_is_still_refused(tree, command, rule):
    assert env_rule(command, tree) == rule


AWK_OPENS = "awk '{gsub(/[][\\\"]/,\"\"); while ((getline l < $0) > 0) print l}'"
NAMED = "[\".env\"]"


@pytest.mark.parametrize("command", [
    "cat <<'EOF' | " + AWK_OPENS + "\n" + NAMED + "\nEOF",
    AWK_OPENS + " <<'EOF'\n" + NAMED + "\nEOF",
    "cat <<'EOF' | stdbuf -o0 " + AWK_OPENS + "\n" + NAMED + "\nEOF",
    "echo '" + NAMED + "' | " + AWK_OPENS,
    "printf '%s' '" + NAMED + "' | " + AWK_OPENS,
    "cat <<'EOF' | yq '.[] | load(.)'\n" + NAMED + "\nEOF",
    # Inline code by any code option, clustered or not, can open what the literal names.
    "perl -ne 'while(/\"([^\"]+)\"/g){open F,$1;print <F>}' <<'EOF'\n" + NAMED + "\nEOF",
    "cat <<'EOF' | perl -lne 'while(/\"([^\"]+)\"/g){open F,$1;print <F>}'\n" + NAMED + "\nEOF",
    "ruby -ne 'puts File.read($_[/\"(.+)\"/,1])' <<'EOF'\n" + NAMED + "\nEOF",
    "cat <<'EOF' | node -pe \"require('fs').readFileSync(JSON.parse(require('fs').readFileSync(0))[0],'utf8')\"\n"
    + NAMED + "\nEOF",
    "cat <<'EOF' | python3 -Ic \"import sys,json;print(open(json.load(sys.stdin)[0]).read())\"\n"
    + NAMED + "\nEOF",
])
def test_a_json_literal_fed_to_a_program_that_acts_on_its_input_is_read(tree, command):
    # Regression: the JSON-literal exemption skipped the body-names check for awk and yq.
    assert env_rule(command, tree) == "env.read.operand"


@pytest.mark.parametrize("command, rule", [
    ("..; grep -rn KEY \"$(pwd)\"", "env.search.unknown_root"),            # zsh autocd
    ("f() { :; }; grep -rn KEY \"$(pwd)\"", "env.search.unknown_root"),
    ("eval \"$X\"; grep -rn KEY \"$(pwd)\"", "env.search.unknown_root"),
    # A marker word typed in the command is not the guard's: nothing is placed.
    ("echo __repact_here_0__; ..; grep -rn KEY \"$(pwd)\"", "env.search.unknown_root"),
    ("echo __repact_here_00__; ..; grep -rn KEY \"$(pwd)\"", "env.search.unknown_root"),
    ("echo __repact_\"here\"_0__; ..; grep -rn KEY \"$(pwd)\"", "env.search.unknown_root"),
    ("grep -rn KEY \"$(pwd)\"", None),
    ("ls; grep -rn KEY \"$(pwd)\"", None),
])
def test_pwd_resolves_only_while_the_directory_is_known(tree, command, rule):
    # Regression: $(pwd) resolved to the frame cwd after a move the tracker follows but the
    # text check missed, so the search walked the narrower tree.
    (tree / "sub").mkdir()
    assert env_rule(command, tree / "sub") == rule
