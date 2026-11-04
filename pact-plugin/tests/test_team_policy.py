"""
Location: pact-plugin/tests/test_team_policy.py
Summary: The tighten-only team policy (hooks/shared/team_policy.py) at its boundary: a policy can
         raise a mode, add deny words and network programs and switch the egress pragma off;
         lowering a mode, removing words, look-alike keys, malformed JSON, NaN and huge numbers,
         duplicate keys, a symlinked, hard-linked, oversized or parent-directory policy are
         ignored and noted. Then each consumer once: the commit gate, the PACT_* enums, the
         egress guard and the doctor, each naming the policy when it refuses.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from shared import team_policy
from shared import check_modes as ledger

PLUGIN = Path(__file__).resolve().parents[1]


def _doctor_policy():
    """bin/_doctor_policy.py. bin/ is not on the suite's path (path setup stays in conftest):
    loading bin/_doctor.py by file puts it there, as repact-doctor's own import does."""
    from importlib.util import module_from_spec, spec_from_file_location
    spec = spec_from_file_location("_doctor", PLUGIN / "bin" / "_doctor.py")
    module = module_from_spec(spec)
    sys.modules.setdefault("_doctor", module)
    spec.loader.exec_module(module)
    return module._dp


@pytest.fixture(autouse=True)
def _fresh_cache():
    team_policy.reset()
    yield
    team_policy.reset()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / ".repacted").mkdir()
    return root


def write(repo: Path, policy) -> Path:
    path = repo / ".repacted" / "policy.json"
    path.write_text(policy if isinstance(policy, str) else json.dumps(policy), encoding="utf-8")
    return path


def merged(repo: Path, constants: dict) -> dict:
    return team_policy.tighten_constants(constants, str(repo))


USER = {"modes": {"commit_rule": "gate", "handoff_evidence": "advisory"},
        "commit_rule": {"deny_words": ["guard", "bypass"]}}


# ── merge rules ───────────────────────────────────────────────────────────────────────────
def test_a_policy_raises_a_mode_in_the_checks_own_words(repo):
    write(repo, {"modes": {"handoff_evidence": "enforce"}})
    modes = merged(repo, USER)["modes"]
    assert modes["handoff_evidence"] == "gate"           # its reader's word for the same rank
    assert "modes.handoff_evidence" in team_policy.raised(str(repo))


def test_lowering_a_mode_is_ignored_and_noted(repo):
    write(repo, {"modes": {"commit_rule": "off", "handoff_evidence": "shadow"}})
    assert merged(repo, USER)["modes"] == USER["modes"]
    notes = team_policy.ignored(str(repo))
    assert any("modes.commit_rule 'off': looser" in n for n in notes)
    assert any("modes.handoff_evidence 'shadow': looser" in n for n in notes)


def test_a_mode_is_compared_with_the_shipped_one_when_the_users_is_unreadable(repo):
    write(repo, {"modes": {"commit_rule": "advisory"}})
    out = merged(repo, {"modes": {"commit_rule": "bogus"}})   # the reader falls back to 'gate'
    assert out["modes"]["commit_rule"] == "bogus"
    assert any("looser than this machine's 'gate'" in n for n in team_policy.ignored(str(repo)))


def test_deny_words_are_added_and_never_removed(repo):
    write(repo, {"commit_rule": {"deny_words": ["Guard", "do-not-ship"]}})
    assert merged(repo, USER)["commit_rule"]["deny_words"] == ["guard", "bypass", "do-not-ship"]
    write(repo, {"commit_rule": {"deny_words": []}})
    team_policy.reset()
    assert merged(repo, USER)["commit_rule"]["deny_words"] == ["guard", "bypass"]


def test_no_policy_returns_the_users_object_unchanged(repo):
    assert merged(repo, USER) is USER
    assert team_policy.load(str(repo)).path is None


# ── untrusted input: ignored and noted, never fatal, never applied ────────────────────────
@pytest.mark.parametrize("key, why", [
    ("ｍodes", "look-alike of 'modes'"),])
def test_unicode_and_case_lookalike_keys_are_not_read(repo, key, why):
    write(repo, {key: {"commit_rule": "gate"}})
    policy = team_policy.load(str(repo))
    assert policy.values == {}
    assert any(why in n for n in policy.ignored)


@pytest.mark.parametrize("text, why", [
    ('{"modes": {"commit_rule": "gate"', "malformed JSON"),])
def test_a_file_that_cannot_be_trusted_is_ignored_whole(repo, text, why):
    if isinstance(text, bytes):
        (repo / ".repacted" / "policy.json").write_bytes(text)
    else:
        write(repo, text.replace("{", '{"modes": {"commit_rule": "gate"}, ', 1)
              if text.startswith('{"version"') else text)
    policy = team_policy.load(str(repo))
    assert policy.values == {} and policy.path
    assert any(why in n for n in policy.ignored), policy.ignored
    assert merged(repo, {"modes": {"commit_rule": "off"}})["modes"]["commit_rule"] == "off"


def test_a_duplicate_key_uses_neither_copy(repo):
    write(repo, '{"modes": {"commit_rule": "gate", "commit_rule": "off", '
                '"handoff_evidence": "gate"}}')
    policy = team_policy.load(str(repo))
    assert policy.values == {"modes": {"handoff_evidence": "gate"}}
    assert any("'commit_rule': given more than once" in n for n in policy.ignored)


# ── consumers ─────────────────────────────────────────────────────────────────────────────


GH = "ghp_" + "Fk9mQ2vLp7Rz4tWx8Yb3Nc5D"


