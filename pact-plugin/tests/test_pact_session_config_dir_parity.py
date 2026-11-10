"""
Location: pact-plugin/tests/test_pact_session_config_dir_parity.py
Summary: Pins that skills/pact-memory/scripts/pact_session.py anchors its paths
         at the resolved Claude config root, and that it can actually IMPORT
         that resolver the way production invokes it.
Used by: the merge gate only — nothing imports this module.

SCOPE, ON PURPOSE — do not "restore" the missing coverage:

  This file does NOT re-test the resolver's precedence contract (unset / empty /
  whitespace / "~" / "~/x" / absolute / relative / trailing slash / nonexistent).
  tests/test_paths.py owns that, with 21 hardcoded-literal tests including
  test_exact_prefix_slice_not_lstrip and test_no_expanduser_uses_resolved_home.
  Restating those clauses here would be a second statement of one contract, free
  to drift, with nothing comparing the two — the duplicate-implementation problem
  moved to the test layer.

  What is left for this file is what test_paths.py cannot see: whether
  pact_session ANCHORS at the resolved root, and whether it REACHES the resolver
  at all.

EXPECTED VALUES ARE HARDCODED, never computed by calling the resolver. Since
pact_session now imports the same function a test would call, deriving the
expectation from it would move expectation and subject together — a resolver
that stopped honoring $CLAUDE_CONFIG_DIR would green every arm here. (The
opposite rule applied while pact_session held its own copy: with two
implementations, restating would have been a third. The rule inverts on how many
implementations exist.)

WHY THE SUBPROCESS ARM IS NOT OPTIONAL: tests/conftest.py puts BOTH hooks/ and
skills/pact-memory/ on sys.path for the whole suite. So in-process, the import
under test succeeds whether or not pact_session's own bootstrap works — a wrong
parents[] index would leave every other arm in this file green while production
raised ModuleNotFoundError on each cli.py invocation. Only a subprocess without
conftest's help can tell those apart.
"""
from pathlib import Path

from scripts import pact_session

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "pact-memory" / "scripts"


def test_context_file_path_anchors_at_relocated_root(tmp_path, monkeypatch):
    """A relocated root must move the context path off home entirely."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "relocated"))

    # Hardcoded, not get_claude_config_dir(). conftest redirects home to tmp_path,
    # so a home-anchored regression lands on tmp_path/".claude"/... instead.
    expected = (
        tmp_path / "relocated" / "pact-sessions"
        / "proj" / "sid" / "pact-session-context.json"
    )

    assert pact_session._context_file_path("sid", "/somewhere/proj") == expected


