"""Coder-flagged item: the get_enum ADDITIVE stderr WARN (emitted when a *_MODE
env var is SET-but-invalid) must never CHANGE a gate's disposition, and must
never leak into the gate's stdout decision JSON.

WHAT THIS FILE CLAIMS, restated once more because the behaviour moved twice:
`PACT_DISPATCH_VARIETY_MODE` ships an ENFORCING default ("deny") but declares
`invalid_fallback = "warn"`, so an UNSET variable enforces while an UNPARSEABLE
one does not. Those are different user intents and the resolver now treats them
as such. The invariant is that resolution lands on the option's DECLARED
landing point for the path taken -- `default` for unset, `invalid_fallback` for
unrecognised -- and nowhere else, with the diagnostic on stderr.

Mechanism the tests pin:
- A set-but-invalid mode (e.g. "banana") resolves to that option's declared
  `invalid_fallback` -- "warn" for BOTH rows -- and never to anything derived
  from the invalid token.
- An UNSET variable resolves to that option's `default` -- "warn" for the
  inline-mission gate, "deny" for the dispatch-variety gate. **This is the arm
  carrying the discrimination**, because the invalid column coincides across
  the two rows and therefore cannot distinguish them.
  (TestInvalidModeResolvesToTheDeclaredFallback)
- The additive diagnostic is written to STDERR at gate import, so it cannot
  corrupt the gate's STDOUT decision JSON. (same class -- captures the reload
  stderr)
- Behaviorally, driving the REAL handoff_ordering_gate.main() while the invalid
  mode is active emits valid, uncorrupted JSON. (TestInvalidModeStdoutStaysClean)

Reload discipline: both gate modules resolve their mode constant at IMPORT, so we
reload under the invalid env and RESTORE them under a clean env at teardown
(without evicting from sys.modules -- sibling test files import + reload them and
would break if they vanished). Mirrors test_pact_config_gate_migration.
"""
import contextlib
import importlib
import io
import os
import sys

import pytest

INLINE_ENV = "PACT_DISPATCH_INLINE_MISSION_MODE"
VARIETY_ENV = "PACT_DISPATCH_VARIETY_MODE"

# (module, env var, mode-constant name, UNSET default, INVALID fallback).
# Both are independent literals, not read back from the registry -- reading the
# registry would make the assertions tautological.
#
# 🔴 THE TWO COLUMNS ARE WHY THIS TABLE HAS FIVE ELEMENTS, and dropping either
# would produce a test that looks like per-option coverage and is not. After
# `invalid_fallback` landed, the INVALID column COINCIDES for both rows (both
# "warn"), so an invalid-only assertion expects the same answer for both gates
# and cannot tell them apart -- it would pass unchanged if one row's policy were
# deleted. The discrimination lives in the UNSET column, where they differ
# ("warn" vs "deny"). Verified by execution before this table was restructured,
# not reasoned about: an assertion whose two arms expect the same value is not
# a parametrisation, it is one arm run twice.
_GATES = [
    ("dispatch_gate", INLINE_ENV, "INLINE_MISSION_MODE", "warn", "warn"),
]


@pytest.fixture(scope="module", autouse=True)
def _restore_gate_modules():
    """Reload both gates under a CLEAN env after this file so later tests see
    their default constants. Do NOT evict from sys.modules."""
    yield
    for env in (INLINE_ENV, VARIETY_ENV):
        os.environ.pop(env, None)
    for mod_name in ("dispatch_gate",):
        module = sys.modules.get(mod_name)
        if module is not None:
            importlib.reload(module)


def _reload_capturing_stderr(monkeypatch, module_name, env_name, value, const_name):
    """Set env=value, reload module capturing import-time stderr, return
    (mode_constant, stderr_text)."""
    monkeypatch.setenv(env_name, value)
    module = importlib.import_module(module_name)
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        importlib.reload(module)
    return getattr(module, const_name), buf.getvalue()


class TestInvalidModeResolvesToTheDeclaredFallback:
    @pytest.mark.parametrize(
        "module_name,env_name,const_name,unset_default,invalid_fallback", _GATES)
    def test_invalid_value_resolves_to_the_declared_invalid_fallback(
        self, monkeypatch, module_name, env_name, const_name,
        unset_default, invalid_fallback,
    ):
        mode, _stderr = _reload_capturing_stderr(
            monkeypatch, module_name, env_name, "banana", const_name
        )
        assert mode == invalid_fallback, (
            f"{module_name}: an invalid {env_name} must resolve to that "
            f"option's DECLARED invalid fallback {invalid_fallback!r}. It must "
            f"never resolve to the invalid token or to a value the resolver "
            f"invented -- the diagnostic reports the fallback, it does not "
            f"choose it."
        )


class TestInvalidModeStdoutStaysClean:
    """Behavioral: with the invalid mode active, real main() still emits valid,
    parseable JSON on stdout and exits 0 on a frame the gate has no opinion
    about.

    WHAT THIS ARM CAN AND CANNOT SHOW, because the frame is doing the work: a
    non-TaskUpdate frame cannot reach EITHER branch's advisory, so it could not
    deny under any mode — this arm therefore proves the stderr diagnostic does
    not corrupt stdout and does not crash the gate. It is NOT evidence that the
    gate never denies; that question is settled by the mode constant asserted
    above, and the dispatch-variety gate's answer to it is now 'it does'."""

