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
    f"git commit -m 'Add x.' --trailer '{ATTRIBUTION}: Claude <{BOT_ADDRESS}>'",])
def test_a_trailer_is_read_as_a_message_line(gate, command):
    reason = gate(command)
    assert reason and "AI attribution" in reason


def test_a_plain_trailer_is_a_body_line():
    assert rules.commit_message("git commit -m 'Add x.' --trailer 'Reviewed-by=Ann'") == \
        "Add x.\n\nReviewed-by: Ann"


@pytest.mark.parametrize("command", [
    f"git commit -m \"$(printf 'Add x.\\n\\n{ATTRIBUTION}: Claude')\"",])
def test_a_message_built_at_run_time_is_returned(gate, command):
    assert rules.built_at_run_time(command)
    reason = gate(command)
    assert reason and "built by the shell at run time" in reason


# Moved from tests/user_layer/test_write_checks.py when the TaskUpdate checks left write_checks.


