"""Arms for the closed-vocabulary failure-cause convention.

THE DEFECT THESE ARMS CLOSE. Six producers return a status string that
session_init routes into `system_messages` on a substring test. Five of them
interpolated a cut of the caught exception, and an OSError renders as
`[Errno NN] <strerror>: '<path>'`, so the cut kept the LEADING characters
and emitted the absolute path with the home directory and the user name.

TWO PROPERTIES ARE UNDER TEST AND THEY FAIL DIFFERENTLY.
  1. NO PATH FRAGMENT reaches a routed status string.
  2. THE ROUTING TOKEN SURVIVES. `failed` (or `skipped`) inside the prose is
     a MACHINE CONTRACT: a reworded producer keeps the human signal and
     silently moves the message from `system_messages` to ordinary context.
A repair that closed property 1 by deleting the message would satisfy an
absence-only arm, so every absence assertion below is paired with a POSITIVE
cause assertion and with the routing token.

ARM LABELS. Each arm says PRODUCT or HARNESS in its own docstring. A HARNESS
arm injects at a seam and exercises the handler; it is NOT evidence that a
natural failure reaches that handler. A later reader must not read one as the
other.
"""

import ast
import errno
import os
from pathlib import Path
from unittest.mock import patch

import pytest

# A path fragment no rendering may carry. Distinctive so a partial leak is
# still caught by the `/` assertion below.
_LEAK_PATH = "/srv/probe-user/secret-dir/CLAUDE.md"


def _oserror(code=errno.EACCES, message="Permission denied"):
    return OSError(code, message, _LEAK_PATH)


def _assert_convention(result, expected_cause, *, routed_token="failed"):
    """The three claims every routed failure message must satisfy.

    Absence, PRESENCE OF THE CAUSE, and the routing token. The cause
    assertion is what stops this helper passing on a message emitted by a
    DIFFERENT handler: the sibling lock-failure arms also carry no "/".
    """
    assert result is not None, "producer returned None; handler not reached"
    assert expected_cause in result, (
        f"cause token absent, so a different handler answered: {result!r}"
    )
    assert "/" not in result, f"path fragment leaked: {result!r}"
    assert _LEAK_PATH not in result
    assert os.path.expanduser("~") not in result
    assert routed_token in result.lower(), (
        "session_init routes on this substring; without it the message "
        f"lands in ordinary context, not system_messages: {result!r}"
    )


class TestFailureCauseHelper:
    """Unit arms on the shared helper itself."""

    def test_closed_vocabulary_table(self):
        """PRODUCT ARM (unit). The rendering comes from a CLOSED VOCABULARY.

        ROW 4 IS THE LOAD-BEARING ONE. `OSError("... path ...")` carries
        `filename=None` and `errno=None` while its `str()` carries a path, so
        no filter keyed on an exception ATTRIBUTE reaches it. That row is why
        the helper reads the caller's message not at all.

        MUTANT: add a `str(exc)` fallback when the symbol is absent. Rows 4
        and 6 redden. That fallback is the likely shortcut, because the two
        Unicode members and an unmapped code carry no errno.

        Row 6 builds its code from `max(errno.errorcode) + 1` and NOT from a
        literal: 122 is unmapped on darwin and maps to EDQUOT on linux.
        """
        from shared.failure_cause import failure_cause

        try:
            b"\xff".decode("utf-8")
        except UnicodeDecodeError as decode_error:
            unicode_exc = decode_error

        rows = [
            (PermissionError(13, "Permission denied", _LEAK_PATH),
             "PermissionError (EACCES)"),
            (IsADirectoryError(21, "Is a directory", _LEAK_PATH),
             "IsADirectoryError (EISDIR)"),
            (OSError(errno.ENOSPC, "No space left on device"),
             "OSError (ENOSPC)"),
            (OSError(f"bare message with {_LEAK_PATH} in it"), "OSError"),
            (unicode_exc, "UnicodeDecodeError"),
            (OSError(max(errno.errorcode) + 1, "unmapped"), "OSError"),
        ]
        for exc, expected in rows:
            rendered = failure_cause(exc)
            assert rendered == expected, f"{exc!r} rendered {rendered!r}"
            assert "/" not in rendered


class TestRoutedProducersEmitNoPath:
    """One arm for each routed producer that carried the leak."""

    def test_ensure_project_memory_md_inner(self, tmp_path, monkeypatch):
        """HARNESS ARM. Injects at `_atomic_write_text`.

        REACHABILITY: the handler is reachable when the create write fails.
        The injection drives it directly and is NOT evidence about how often
        a natural write failure occurs.
        MUTANT: restore `{str(e)[:50]}`.
        """
        from shared import claude_md_manager as cmm

        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
        with patch.object(cmm, "_atomic_write_text", side_effect=_oserror()):
            result = cmm.ensure_project_memory_md()
        _assert_convention(result, "PermissionError (EACCES)")
        assert result.startswith("Project CLAUDE.md failed:")


