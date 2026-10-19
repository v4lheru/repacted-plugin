"""The commit-message rule (shared/commit_rules.py, hooks/write_checks.py commit_gate) and the mode
reader it runs under (shared/check_modes use_mode): trailers, a message built at run time, mode
words and an unreadable checks.json."""
import importlib.util
import json
from pathlib import Path

import pytest

import write_checks

PLUGIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("l4_commit_rules", PLUGIN / "hooks" / "shared" / "commit_rules.py")
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)

ev = write_checks.load_evidence()      # shared.check_modes: use_mode and load_constants look their names up here

# Joined at run time so this file's own commit is not returned by the rule it tests.
ATTRIBUTION = "Co-" + "authored-by"
BOT_ADDRESS = "noreply" + "@" + "anthropic.com"


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """commit_gate under a checks.json the test writes (commit_rule mode `gate` by default)."""
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({"modes": {"commit_rule": "gate"}}))
    monkeypatch.setattr(ev, "CHECKS_FILE", checks)
    monkeypatch.setattr(ev, "shadow_log", lambda event: None)
    monkeypatch.setattr(ev, "_mode_noted", set())

    def run(command):
        return write_checks.commit_gate({"tool_input": {"command": command}, "cwd": str(tmp_path)}, ev)[0]
    run.checks = checks
    return run


@pytest.mark.parametrize("command", [
    f"git commit -m 'Add x.' --trailer '{ATTRIBUTION}: Claude <{BOT_ADDRESS}>'",
    f"git commit -m 'Add x.' --trailer='{ATTRIBUTION}=Claude'",
    f"git commit --trailer \"{ATTRIBUTION} = Claude\" -m 'Add x.'",
    "git commit -m 'Add x.' --trailer 'Cо-аuthored-by: Claude'",      # Cyrillic look-alikes
])
def test_a_trailer_is_read_as_a_message_line(gate, command):
    reason = gate(command)
    assert reason and "AI attribution" in reason


def test_a_plain_trailer_is_a_body_line():
    assert rules.commit_message("git commit -m 'Add x.' --trailer 'Reviewed-by=Ann'") == \
        "Add x.\n\nReviewed-by: Ann"


@pytest.mark.parametrize("command", [
    f"git commit -m \"$(printf 'Add x.\\n\\n{ATTRIBUTION}: Claude')\"",
    "git commit -m \"`cat msg`\"",
    "git commit -m \"Fix $HOME handling.\"",
    "git commit -m ${MSG}",
    "git commit -m 'Add x.' -m \"$(date)\"",
    "git commit -F \"$MSG_FILE\"",
    "git commit -m 'Add x.' --trailer \"$T\"",
    "git -C repo commit -am \"$(echo x)\"",
])
def test_a_message_built_at_run_time_is_returned(gate, command):
    assert rules.built_at_run_time(command)
    reason = gate(command)
    assert reason and "built by the shell at run time" in reason


@pytest.mark.parametrize("command", [
    "git commit -m 'Costs $5 less.'",
    "git commit -m \"Costs \\$5 less.\"",
    "git commit -m \"$(cat <<'EOF'\nAdd the parser.\nEOF\n)\"",
    "git commit -m 'Add the parser.' && echo \"$(date)\"",
    "git commit --no-edit",
])
def test_a_literal_message_is_not_flagged(gate, command):
    assert not rules.built_at_run_time(command)
    assert gate(command) is None


@pytest.mark.parametrize("word, expected", [
    ("gate", "gate"), ("Gate", "gate"), (" ENFORCE ", "gate"), ("enforce", "gate"),
    ("off", "off"), ("Shadow", "shadow"), ("ADVISORY", "advisory"),
])
def test_mode_words_are_read_without_regard_to_case(gate, word, expected, capsys):
    gate.checks.write_text(json.dumps({"modes": {"commit_rule": word}}))
    assert ev.use_mode("commit_rule") == expected
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("content", [
    None, "{not json", "[]", '{"modes": ["gate"]}', '{"modes": {}}', '{"modes": {"commit_rule": "gaet"}}',
    '{"modes": {"commit_rule": 7}}',
])
def test_an_unreadable_or_malformed_file_uses_the_shipped_mode(gate, content, capsys):
    if content is None:
        gate.checks.unlink()
    else:
        gate.checks.write_text(content)
    assert ev.use_mode("commit_rule") == "gate"
    assert ev.use_mode("handoff_evidence") == "gate"                  # shipped as gate
    assert ev.use_mode("commit_rule") == "gate"
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 2 and "shipped mode 'gate'" in err[0]           # one note per check
    assert gate(f"git commit -m 'Add x.' -m '{ATTRIBUTION}: Claude'")  # the rule still returns




def test_an_empty_deny_words_list_checks_no_words_and_a_missing_one_the_shipped_words(gate):
    message = "git commit -m 'Fix the " + "scanner" + " path.'"
    assert gate(message)                                               # no commit_rule key
    gate.checks.write_text(json.dumps({"modes": {"commit_rule": "gate"},
                                       "commit_rule": {"deny_words": []}}))
    assert gate(message) is None
    gate.checks.write_text(json.dumps({"modes": {"commit_rule": "gate"},
                                       "commit_rule": {"deny_words": "scanner"}}))
    assert gate(message)                                               # malformed: shipped words


# Moved from tests/user_layer/test_write_checks.py when the TaskUpdate checks left write_checks.
def test_a_rule_breaking_commit_is_returned_every_time_it_is_issued(gate):
    """The return-once rail let the same commit through on its second try."""
    heredoc = "git commit -m \"$(cat <<'EOF'\nAdd the parser.\n\n" + ATTRIBUTION + ": Claude\nEOF\n)\""
    for _ in range(3):
        reason = gate(heredoc)
        assert "AI attribution" in reason and "a body" in reason
    assert gate("git commit -m 'Add the parser.'") is None


@pytest.mark.parametrize("command, problem", [
    ("git commit --amend -m 'Add the parser.' -m '" + ATTRIBUTION + ": Claude'", "AI attribution"),
    ("git commit -F- <<'EOF'\nAdd the parser.\n\nMore text.\nEOF", None),           # stdin: unreadable, fail-open
    ("git commit -m \"$(cat <<-EOF\n\tAdd the parser.\n\tBody line.\n\tEOF\n)\"", "a body"),
    ("git commit -m 'Add the parser. \uff23o-Authored-By: Claude'", "AI attribution"),  # fullwidth C
    ("git commit -m 'Add the parser co\u200b-authored-by: Claude'", "AI attribution"),  # zero-width space
    ("git commit -m 'Skip the gu\u00adard in CI.'", "'guard'"),                        # soft hyphen
    ("git commit -m 'Add the caf\u00e9 parser for na\u00efve dates.'", None),          # real accents pass
    ("git commit -m 'Add the guardian angel feature.'", None),                         # a word, not the deny word
])
def test_commit_rule_boundary_rows(command, problem, gate):
    reason = gate(command)
    if problem is None:
        assert reason is None, command
    else:
        assert reason and problem in reason, command
        assert gate(command) == reason
