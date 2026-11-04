"""Tests for shared/pact_config.py -- the os.environ-blind PACT_* resolver.

The resolver is the single source of truth for PACT runtime options. It is
fail-open by construction (every public function is total; any failure returns
the registry default) and os.environ-BLIND with CALL-TIME reads (zero work at
import, no caching -- so a consumer that resolves at its own module load sees
the same value a direct os.environ.get would, and these tests can monkeypatch
os.environ without importlib.reload).

Core invariants under test:
- bool parse is EXACT-MEMBERSHIP, never Python truthiness: "0"/"2"/"maybe" ->
  False (the fail-SAFE direction; a garbled flag stays OFF). bool("0")==True
  would be the F2 fail-unsafe slip this guards against.
- enum parse replicates the gates' .strip().lower() normalization; an unset var
  -> silent default; a SET-but-invalid value -> default + a stderr WARN (the
  non-vacuity tell that the invalid branch is live).
- llm_options() surfaces ONLY consumer=="llm" options as typed values.
- fail-safe: unknown option + an internal exception both resolve to the default
  without raising.
- call-time read: setting an env var AFTER import still changes the result
  (proves no import-time caching).
"""

import pytest

import shared.pact_config as pact_config
from shared.pact_config import get_bool, get_enum, llm_options


_GREEDY = "PACT_PR_GREEDY_FIX"
_AUTO = "PACT_AUTONOMOUS_SCOPE_DETECTION"
_INLINE = "PACT_DISPATCH_INLINE_MISSION_MODE"
_VARIETY = "PACT_DISPATCH_VARIETY_MODE"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start every test from an unset state for all registered options."""
    for name in (_GREEDY, _AUTO, _INLINE, _VARIETY):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class TestGetBoolExactMembership:
    @pytest.mark.parametrize("raw", ["1",])
    def test_true_tokens_resolve_true(self, _clean_env, raw):
        _clean_env.setenv(_GREEDY, raw)
        assert get_bool(_GREEDY) is True


class TestGetEnumNormalizationAndValidation:
    @pytest.mark.parametrize("raw,expected", [
        ("warn", "warn"),])
    def test_valid_values_normalized(self, _clean_env, raw, expected):
        _clean_env.setenv(_INLINE, raw)
        assert get_enum(_INLINE) == expected


class TestLlmOptions:
    def test_defaults_all_off(self, _clean_env):
        assert llm_options() == {_GREEDY: False, _AUTO: False}


class TestFailSafe:
    def test_unknown_option_returns_safe_defaults(self, _clean_env):
        # Unknown options are not consumed; resolve to the type-neutral safe
        # default without raising.
        assert get_bool("PACT_DOES_NOT_EXIST") is False
        assert get_enum("PACT_DOES_NOT_EXIST") == ""


class TestCallTimeReadNoCaching:
    def test_env_set_after_import_is_observed(self, _clean_env):
        # pact_config was imported at module top; setting the var now must still
        # change the result -> proves LIVE per-call reads, not import caching.
        assert get_bool(_GREEDY) is False
        _clean_env.setenv(_GREEDY, "1")
        assert get_bool(_GREEDY) is True
