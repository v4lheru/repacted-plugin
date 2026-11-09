"""
Location: pact-plugin/tests/test_hook_write_cut.py
Summary: A PostToolUse leg killed mid-write (CC-20: since Claude Code 2.1.319 a PostToolUse hook
    on a call that finishes after an interrupt gets at most 10 s) leaves no corrupt state.
    Each case runs the real writer in a child process that SIGKILLs itself halfway through the
    write, then checks the file from the parent.
    - merge token (hooks/merge_guard_post.write_token): no half-written token under a live name;
      the earlier token still parses.
    - session journal (shared/session_journal.append_event): the reader skips the cut trailing
      line and keeps the earlier rows, and the next append starts on its own line instead of
      joining the fragment.
    - the hidden token temp is refused to a shell write like the token itself, and a
      filesystem without hard links still gets a token.
Used by: pytest (tests/conftest.py puts hooks/ on sys.path).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from shared.journal_reader import read_events_from
from shared.merge_guard_common import TOKEN_PREFIX
from shared.session_journal import append_event, make_event

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


@pytest.fixture(autouse=True)
def _drop_imported_hook_modules():
    """Forget the hook modules a test imported (as dispatch's _Isolation does between checks):
    shell.settings pulls in shell.env.*, and with those left in sys.modules a later in-process
    env_guard replay (test_hook_decision_tables) denied its push rows as unverified."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        path = getattr(sys.modules.get(name), "__file__", None) or ""
        if path.startswith(str(HOOKS_DIR)):
            sys.modules.pop(name, None)

# Writes half of what json.dump / os.write was given, then dies the way the host's kill does:
# no except or finally block runs.
_CUT_PRELUDE = textwrap.dedent("""
    import json, os, signal, sys
    sys.path.insert(0, sys.argv[1])
    def die():
        os.kill(os.getpid(), signal.SIGKILL)
""")


def _run_child(body: str, home: Path, *args: str) -> int:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home),
           "CLAUDE_CONFIG_DIR": str(home / ".claude")}
    done = subprocess.run([sys.executable, "-c", _CUT_PRELUDE + textwrap.dedent(body),
                           str(HOOKS_DIR), *args], capture_output=True, timeout=60, env=env)
    return done.returncode


def test_merge_token_cut_mid_write_leaves_no_partial_token(tmp_path):
    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    code = _run_child("""
        from pathlib import Path
        import merge_guard_post as post
        folder = Path(sys.argv[2])
        assert post.write_token({"operation_type": "merge", "pr_number": "1"}, folder)
        def cut(obj, handle, **kwargs):
            text = json.dumps(obj, **kwargs)
            handle.write(text[: len(text) // 2])
            handle.flush()
            die()
        json.dump = cut
        post.write_token({"operation_type": "merge", "pr_number": "2"}, folder)
    """, tmp_path, str(token_dir))
    assert code == -9, "the child must die inside the second write"
    live = sorted(token_dir.glob(f"{TOKEN_PREFIX}*"))
    assert len(live) == 1, [p.name for p in live]
    first = json.loads(live[0].read_text(encoding="utf-8"))
    assert first["context"]["pr_number"] == "1"


def _check_failure(name: str) -> dict:
    return make_event("check_failure", check=name, event="PostToolUse", exc_class="Cut")


def test_journal_row_cut_mid_write_keeps_earlier_rows_and_the_next_row(tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    code = _run_child("""
        from shared.session_journal import append_event, make_event
        folder = sys.argv[2]
        def row(name):
            return make_event("check_failure", check=name, event="PostToolUse", exc_class="Cut")
        assert append_event(row("first"), session_dir=folder)
        real_write = os.write
        def cut(fd, data):
            data = bytes(data)
            real_write(fd, data[: len(data) // 2])
            die()
        os.write = cut
        append_event(row("second"), session_dir=folder)
    """, tmp_path, str(session_dir))
    assert code == -9, "the child must die inside the second append"
    journal = session_dir / "session-journal.jsonl"
    assert not journal.read_bytes().endswith(b"\n"), "the cut must leave a fragment"

    # The reader skips the cut trailing line and keeps the earlier row.
    assert [e["check"] for e in read_events_from(str(session_dir))] == ["first"]

    # The next row is written whole, on its own line.
    assert append_event(_check_failure("third"), session_dir=str(session_dir))
    assert [e["check"] for e in read_events_from(str(session_dir))] == ["first", "third"]


def test_command_writing_the_hidden_token_temp_is_refused_like_the_token(tmp_path, monkeypatch):
    # The temp is hard-linked into place, so it is the live token's inode until it is dropped:
    # a write through its name (or an fd opened on it) rewrites the token. The command check
    # must refuse it as it refuses the token name itself.
    import merge_guard_post as post
    from shell.settings import APPROVALS, written_place

    config = tmp_path / "config"
    config.mkdir()
    seen: list[str] = []
    real_link = os.link

    def record(src, dst, **kwargs):
        seen.append(str(src))
        return real_link(src, dst, **kwargs)

    monkeypatch.setattr(post.os, "link", record)
    assert post.write_token({"operation_type": "merge", "pr_number": "3"}, config)
    assert len(seen) == 1 and os.path.dirname(seen[0]) == str(config)
    environ = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(config), "PATH": "/usr/bin:/bin"}
    assert written_place(f"printf x > {seen[0]}", str(tmp_path), environ) == APPROVALS


def test_dotted_names_that_are_not_the_token_temp_stay_writable(tmp_path):
    from shell.settings import APPROVALS, written_place

    config = tmp_path / "config"
    config.mkdir()
    environ = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(config), "PATH": "/usr/bin:/bin"}
    for name in (".merge-notes", ".merge-authorizedx", "notes-merge-authorized-1"):
        assert written_place(f"printf x > {config}/{name}", str(tmp_path), environ) != APPROVALS
    temp_via_variable = 'printf x > "$D/.merge-authorized-1.2.tmp"'
    assert written_place(temp_via_variable, str(tmp_path), environ) == APPROVALS


@pytest.mark.parametrize("errno_name", ["EPERM", "ENOTSUP", "EOPNOTSUPP", "EXDEV"])
def test_filesystem_without_hard_links_still_mints_a_token(tmp_path, monkeypatch, errno_name):
    import errno

    import merge_guard_post as post

    def no_links(src, dst, **kwargs):
        raise OSError(getattr(errno, errno_name), "no hard links here")

    monkeypatch.setattr(post.os, "link", no_links)
    path = post.write_token({"operation_type": "merge", "pr_number": "4"}, tmp_path)
    assert path and os.path.basename(path).startswith(TOKEN_PREFIX)
    assert json.loads(Path(path).read_text(encoding="utf-8"))["context"]["pr_number"] == "4"
    assert [p.name for p in tmp_path.iterdir()] == [os.path.basename(path)]


def test_other_link_errors_mint_no_token(tmp_path, monkeypatch):
    import errno

    import merge_guard_post as post

    def denied(src, dst, **kwargs):
        raise OSError(errno.EACCES, "denied")

    monkeypatch.setattr(post.os, "link", denied)
    assert post.write_token({"operation_type": "merge", "pr_number": "5"}, tmp_path) is None
    assert list(tmp_path.glob(f"{TOKEN_PREFIX}*")) == []
