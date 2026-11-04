"""
Location: pact-plugin/tests/test_clock_shift_env.py
Summary: Pins carry_clock_shift, which carries only the three clock-shift values
         into a built child env and changes nothing when the run is not shifted.
Used by: the suite.
"""
import os

from clock_shift.clock_shift_env import LEDGER_ENV, SHIFT_ENV, SHIM_DIR, carry_clock_shift


def test_an_unshifted_run_returns_the_env_untouched(monkeypatch):
    """GUARD. MUTANT: carry the values whether or not the run is shifted."""
    monkeypatch.delenv(SHIFT_ENV, raising=False)
    env = {"PATH": "/usr/bin"}
    assert (carry_clock_shift(env) is env, env) == (True, {"PATH": "/usr/bin"})


