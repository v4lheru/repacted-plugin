"""The commit-message rule (shared/commit_rules.py, hooks/write_checks.py commit_gate) and the mode
reader it runs under (shared/evidence use_mode): review findings L4 (trailers, a message built
at run time) and L5 (mode words, an unreadable checks.json)."""
import importlib.util
import json
from pathlib import Path

import pytest

import amendment_gate
import write_checks

PLUGIN = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("l4_commit_rules", PLUGIN / "hooks" / "shared" / "commit_rules.py")
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)

ev = write_checks.load_evidence()
from shared.evidence import ledger as _ev_ledger  # noqa: E402  (use_mode and load_constants look their names up here)

# Joined at run time so this file's own commit is not returned by the rule it tests.
ATTRIBUTION = "Co-" + "authored-by"
BOT_ADDRESS = "noreply" + "@" + "anthropic.com"


@pytest.fixture
def gate(monkeypatch, tmp_path):
    """commit_gate under a checks.json the test writes (commit_rule mode `gate` by default)."""
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({"modes": {"commit_rule": "gate"}}))
    monkeypatch.setattr(_ev_ledger, "CHECKS_FILE", checks)
    monkeypatch.setattr(ev, "shadow_log", lambda event: None)
    monkeypatch.setattr(_ev_ledger, "_mode_noted", set())

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
    assert ev.use_mode("handoff_evidence") == "advisory"
    assert ev.use_mode("commit_rule") == "gate"
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 2 and "shipped mode 'gate'" in err[0]           # one note per check
    assert gate(f"git commit -m 'Add x.' -m '{ATTRIBUTION}: Claude'")  # the rule still returns


def test_amendment_gate_reads_its_mode_without_regard_to_case(gate):
    gate.checks.write_text(json.dumps({"modes": {"amendment_gate": "Gate"}}))
    assert amendment_gate.gate_mode(ev) == "enforce"
    gate.checks.write_text(json.dumps({"modes": {"amendment_gate": "NOTE"}}))
    assert amendment_gate.gate_mode(ev) == "note"


def test_an_empty_deny_words_list_checks_no_words_and_a_missing_one_the_shipped_words(gate):
    message = "git commit -m 'Fix the " + "scanner" + " path.'"
    assert gate(message)                                               # no commit_rule key
    gate.checks.write_text(json.dumps({"modes": {"commit_rule": "gate"},
                                       "commit_rule": {"deny_words": []}}))
    assert gate(message) is None
    gate.checks.write_text(json.dumps({"modes": {"commit_rule": "gate"},
                                       "commit_rule": {"deny_words": "scanner"}}))
    assert gate(message)                                               # malformed: shipped words
