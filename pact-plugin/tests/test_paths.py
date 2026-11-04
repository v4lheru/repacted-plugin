"""
L1 resolver-unit tests for shared/paths.py::get_claude_config_dir().

Asserts the resolution contract directly (§A.2 of the architecture spec):
fail-loud env honoring, empty/whitespace==unset, exact-prefix ~ slicing with
NO expanduser (monkeypatch-safe), unresolved return, single-path.

Two drive modes are exercised:
  - DI mode (env=/home=): direct contract assertions (allowed at L1).
  - live-globals mode (monkeypatch.setenv + Path.home redirect): proves the
    real seam consumers depend on works without DI.
"""
from pathlib import Path

from shared.paths import get_claude_config_dir


FAKE_HOME = Path("/fake/home")


# --- DI mode: env unset --------------------------------------------------

def test_unset_falls_back_to_home_dotclaude():
    assert get_claude_config_dir(env={}, home=FAKE_HOME) == FAKE_HOME / ".claude"


def test_empty_string_is_unset():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": ""}, home=FAKE_HOME) == FAKE_HOME / ".claude"


def test_whitespace_only_is_unset():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "   "}, home=FAKE_HOME) == FAKE_HOME / ".claude"


def test_whitespace_trimmed_then_honored():
    # leading/trailing whitespace stripped, the remainder honored
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "  /abs  "}, home=FAKE_HOME) == Path("/abs")


# --- DI mode: env set ----------------------------------------------------

def test_absolute_path_honored():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "/opt/cfg"}, home=FAKE_HOME) == Path("/opt/cfg")


def test_tilde_alone_maps_to_home():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "~"}, home=FAKE_HOME) == FAKE_HOME


def test_tilde_slash_prefix_exact_slice():
    assert get_claude_config_dir(env={"CLAUDE_CONFIG_DIR": "~/x"}, home=FAKE_HOME) == FAKE_HOME / "x"


def test_tilde_slash_nested():
    assert get_claude_config_dir(
        env={"CLAUDE_CONFIG_DIR": "~/.claude-kimi"}, home=FAKE_HOME
    ) == FAKE_HOME / ".claude-kimi"


# --- live-globals mode: the no-DI seam consumers use ---------------------


def _code_tokens(py_path: Path) -> str:
    """Return source CODE only (docstrings + comments stripped via tokenize).

    Lets the verbatim contract docstring legitimately MENTION the forbidden
    idioms ("NO expanduser/lstrip/removeprefix") while still catching any
    real CODE use of them.
    """
    import io
    import tokenize

    text = py_path.read_text(encoding="utf-8")
    pieces = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in (tokenize.STRING, tokenize.COMMENT):
            continue
        pieces.append(tok.string)
    return " ".join(pieces)


# ---------------------------------------------------------------------------
# C4 — the B1 accessors FOLLOW $CLAUDE_CONFIG_DIR at call time (proves the
# import-time freeze is gone). env-set → config root; env-unset → home/.claude.
# ---------------------------------------------------------------------------


