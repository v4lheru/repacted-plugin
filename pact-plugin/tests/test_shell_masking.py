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
    ("echo \"$(cat <<'EOF'\n" + M + "\nEOF\n)\"", 1),
]
BYPASS = [
    ("echo '{\"c\":\"" + M + "\"}' | sh", "mg.merge.explicit"),
    ("node -e \"require('child_process').execSync('" + M + "')\"", "mg.merge.explicit"),
    ("gawk '@load \"filefuncs\"; BEGIN{print \"" + M + "\"}'", "mg.merge.explicit"),
    ("perl -E '[\"@{[`" + M + "`]}\"]'", "mg.merge.explicit"),
    ("python3 -W ignore -c \"import os,sys;os.system(sys.argv[1][2:-2])\" '[\"" + M + "\"]'",
     "mg.merge.explicit"),
    ("awk '{print}' $(eval '" + M + "')", "mg.merge.explicit"),
    ("# write it with cat <<EOF\n" + M + "\ncat <<EOF\nx\nEOF", "mg.merge.explicit"),
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
])
def test_a_json_literal_fed_to_a_program_is_data(tree, command):
    assert env_rule(command, tree) is None


@pytest.mark.parametrize("command, rule", [
    ("python3 -c \"import os;os.system('" + E + "')\"", "env.read.operand"),
])
def test_a_json_shaped_body_that_reaches_a_shell_or_a_reader_is_still_refused(tree, command, rule):
    assert env_rule(command, tree) == rule


AWK_OPENS = "awk '{gsub(/[][\\\"]/,\"\"); while ((getline l < $0) > 0) print l}'"
NAMED = "[\".env\"]"


@pytest.mark.parametrize("command", [
    "cat <<'EOF' | " + AWK_OPENS + "\n" + NAMED + "\nEOF",
])
def test_a_json_literal_fed_to_a_program_that_acts_on_its_input_is_read(tree, command):
    # Regression: the JSON-literal exemption skipped the body-names check for awk and yq.
    assert env_rule(command, tree) == "env.read.operand"


@pytest.mark.parametrize("command, rule", [
    ("..; grep -rn KEY \"$(pwd)\"", "env.search.unknown_root"),
])
def test_pwd_resolves_only_while_the_directory_is_known(tree, command, rule):
    # Regression: $(pwd) resolved to the frame cwd after a move the tracker follows but the
    # text check missed, so the search walked the narrower tree.
    (tree / "sub").mkdir()
    assert env_rule(command, tree / "sub") == rule
