"""
Tests for merge guard hooks — merge_guard_post.py and merge_guard_pre.py.

Folded 2026-10-04 (refactor shell lane): its row tests (a recorded decision function on a
literal command) are decision-table rows now (tests/fixtures/shell/decisions/fold_manifest.json
names the rows of each); the mechanism, view and structure tests stay here.

Tests cover:
1. merge_guard_post: keyword detection in AskUserQuestion text
2. merge_guard_post: affirmative answer detection
3. merge_guard_post: token file creation with correct structure and permissions
4. merge_guard_post: context extraction (PR numbers, branch names)
5. merge_guard_post: non-merge questions are ignored
6. merge_guard_post: negative answers don't create tokens
7. merge_guard_pre: dangerous command detection
8. merge_guard_pre: valid token allows commands
9. merge_guard_pre: missing token blocks commands
10. merge_guard_pre: expired token blocks commands and gets cleaned up
11. merge_guard_pre: safe commands pass through
12. main() entry points: stdin JSON, exit codes, output format
13. Adversarial: command obfuscation, shell escaping, multi-command strings
14. Edge cases: boundary TTL, malformed tokens, missing fields, empty inputs
15. Security: token permissions, token content validation, write failures
16. hooks.json: merge guard registration and sync flag validation
17. Integration: full main() flows, multiple tokens, token consumption
"""

from __future__ import annotations

import io
import json
import os
import stat
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture (amend_3 on #141)
from merge_guard_repo_fixture import minted_context  # noqa: E402  (amend_3 on #141: mint-side repo binding)
from merge_guard_repo_fixture import git_bound  # noqa: E402  (1.4.1 U13: git checkout/remote binding)


# =============================================================================
# merge_guard_post.py tests
# =============================================================================


class TestIsMergeQuestion:
    """Tests for merge_guard_post.is_merge_question()."""

    def test_merge_command_detected_not_bare_keyword(self):
        from merge_guard_post import is_merge_question

        # KD-9: is_merge_question is now a command-driven COARSE HINT — it fires
        # on an embedded destructive COMMAND, not on a bare prose keyword.
        assert is_merge_question("Do you want to run `gh pr merge 5`?")
        assert not is_merge_question("Do you want to merge this PR?")

    def test_force_push_command_detected_not_prose(self):
        from merge_guard_post import is_merge_question

        assert is_merge_question("Should I run `git push --force origin main`?")
        assert not is_merge_question("Should I force push to main?")

# (removed class TestIsAffirmative — merge_guard_post.is_affirmative and its
#  AFFIRMATIVE_PATTERNS regex were removed as production-orphaned dead code:
#  OPTION-mode approval is an exact non-decline label match, never a free-text
#  word allowlist, so the affirmative prefix matcher had no remaining call site.)

# (removed class TestExtractContext — superseded by the command-anchored
#  bidirectional suite in test_merge_guard_auth_symmetry.py;
#  it exercised the dropped prose classifier extract_context.)

class TestWriteToken:
    """Tests for merge_guard_post.write_token().

    Per the sparse-context guard, write_token requires the context dict
    to carry at least one concrete anchor (pr_number OR operation_type).
    Tests that exercise the file-write side use a minimal-but-valid
    context (operation_type alone is sufficient).
    """

    # Minimal context that satisfies the sparse-context guard (one anchor).
    _MIN_CTX = {"operation_type": "merge"}

    def test_creates_token_file(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token({"test": True, **self._MIN_CTX}, token_dir=tmp_path)
        assert result is not None
        assert Path(result).exists()

    def test_token_file_permissions(self, tmp_path):
        from merge_guard_post import write_token

        result = write_token(dict(self._MIN_CTX), token_dir=tmp_path)
        mode = os.stat(result).st_mode & 0o777
        assert mode == 0o600

class TestWriteTokenSparseContextGuard:
    """Pin the F-2 sparse-context refusal at the write boundary.

    Refusing to write tokens whose context lacks BOTH pr_number AND
    operation_type prevents wildcard-permissive matches in
    `_token_matches_command`: a `{pr_number: None, operation_type: None}`
    token would otherwise be authorized against ANY destructive command
    via the ladder's "ambiguous-permissive" fallback.
    """

    def test_write_token_rejects_no_pr_number_no_op_type(self, tmp_path, capsys):
        from merge_guard_post import write_token

        result = write_token({}, token_dir=tmp_path)
        assert result is None
        # No file should have been created
        assert list(Path(tmp_path).glob("merge-authorized-*")) == []
        # Stderr warning emitted (forensic visibility)
        captured = capsys.readouterr()
        assert "[security]" in captured.err
        assert "sparse context" in captured.err

    def test_write_token_rejects_pr_number_alone(self, tmp_path, capsys):
        """FAIL-CLOSED (never-mint-op_type=None): a pr_number WITHOUT an
        operation_type is refused — an untyped token can never positively match a
        command on the read side, so it is never written."""
        from merge_guard_post import write_token

        result = write_token({"pr_number": "663"}, token_dir=tmp_path)
        assert result is None
        assert list(Path(tmp_path).glob("merge-authorized-*")) == []
        captured = capsys.readouterr()
        assert "[security]" in captured.err

    def test_write_token_rejects_non_dict_context(self, tmp_path, capsys):
        from merge_guard_post import write_token

        result = write_token("not_a_dict", token_dir=tmp_path)
        assert result is None
        captured = capsys.readouterr()
        assert "[security]" in captured.err
        assert "non-dict" in captured.err

class TestPostMainEntryPoint:
    """Tests for merge_guard_post.main() stdin/exit behavior."""

    def test_main_exits_0_on_merge_approval(self, tmp_path, capsys):
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{
                "question": "Merge the PR?",
                "options": [
                    {"label": "Yes, merge", "description": "Run `gh pr merge 42`"},
                    {"label": "Cancel", "description": "Abort"},
                ],
            }]},
            "tool_response": {"answers": {"Merge the PR?": "Yes, merge"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}
        # Verify token was created
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1

    def test_main_exits_0_on_non_merge_question(self, tmp_path):
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I add logging?"}]},
            "tool_response": {"answers": {"Should I add logging?": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # No token should be created
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 0

    def test_main_rejects_string_tool_response(self, tmp_path):
        """Non-dict tool_response exits early — no token created."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_response": "yes",
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("merge_guard_post.write_token") as mock_write_token:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        # Non-dict tool_response rejected — only dict format trusted
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
        # Cause-of-no-token: malformed tool_response triggered early-exit
        # before the merge-question branch could reach write_token. Without
        # this, the test would still pass if a silent regression caused
        # tool_response reading to degrade to empty-dict + happy-path skip.
        mock_write_token.assert_not_called()

# =============================================================================
# merge_guard_pre.py tests
# =============================================================================


class TestFindValidToken:
    """Tests for merge_guard_pre.find_valid_token()."""

    def test_finds_valid_token(self, tmp_path):
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {"test": True},
        }
        token_file = tmp_path / "merge-authorized-12345"
        token_file.write_text(json.dumps(token_data))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None
        assert result["context"]["test"] is True
        assert path is not None
        assert "merge-authorized-12345" in path

    def test_returns_none_when_no_tokens(self, tmp_path):
        from merge_guard_pre import find_valid_token

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None

class TestCheckMergeAuthorization:
    """Tests for merge_guard_pre.check_merge_authorization()."""

    def test_allows_safe_commands(self, tmp_path):
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("git push origin feature/my-branch", token_dir=tmp_path)
        assert result is None

    def test_blocks_dangerous_without_token(self, tmp_path):
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

class TestPreMainEntryPoint:
    """Tests for merge_guard_pre.main() stdin/exit behavior."""

    def test_main_exits_0_on_safe_command(self, capsys):
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "git status"}
        })

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

    def test_main_exits_2_on_dangerous_without_token(self, tmp_path, capsys):
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "AskUserQuestion" in output["hookSpecificOutput"]["permissionDecisionReason"]
        # Issue #658: hookEventName is required by the harness schema; missing
        # it causes silent rejection and the deny fails open.
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

# =============================================================================
# Integration: post writes token, pre reads it
# =============================================================================


class TestIntegration:
    """End-to-end: post hook writes token, pre hook reads it."""

    def test_approval_flow(self, tmp_path):
        """Full flow: user approves merge (command-anchored), then the matching
        dangerous command is allowed."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Simulate post hook: mint a token from the approved command.
        context = minted_context("gh pr merge 42")
        token_path = write_token(context, token_dir=tmp_path)
        assert token_path is not None

        # Simulate pre hook: dangerous command should be allowed
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is None  # Allowed

    def test_no_approval_flow(self, tmp_path):
        """Without approval, dangerous commands are blocked."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None  # Blocked
        assert "AskUserQuestion" in result

# =============================================================================
# Single-use token tests
# =============================================================================


class TestNUseBoundedToken:
    """N-use bounded token semantics (#720 Bug C, MAX_USES=2).

    A single AskUserQuestion approval now authorizes up to MAX_USES
    identical-context retries within TOKEN_TTL via per-use slot markers.
    The (MAX_USES + 1)-th identical-context command requires a fresh
    AskUserQuestion approval, preserving the "stop and reconsider"
    checkpoint.
    """

    def test_first_use_succeeds(self, tmp_path):
        """First identical-context use is authorized; slot-1 marker created;
        token still present (terminal rename happens only on last slot)."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        assert token_path is not None
        assert Path(token_path).exists()

        # First command: allowed; slot-1 claimed; token not yet .consumed
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is None  # Allowed
        assert Path(token_path).exists()  # Token still on disk (budget remaining)
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert not Path(token_path + ".consumed").exists()

    def test_second_use_within_budget_succeeds(self, tmp_path):
        """Second identical-context use IS authorized under MAX_USES=2.
        After this, BOTH use-1 and use-2 markers exist and the token has
        been terminally renamed to .consumed."""
        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        token_path = write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        result1 = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result1 is None

        # Second command within MAX_USES budget: allowed
        result2 = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result2 is None
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert Path(token_path + USE_MARKER_SUFFIX + "2").exists()
        # Terminal rename on last slot
        assert not Path(token_path).exists()
        assert Path(token_path + ".consumed").exists()

    def test_third_use_blocked_after_budget_exhausted(self, tmp_path):
        """Third identical-context use is blocked — budget exhausted,
        requires fresh AskUserQuestion approval."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        # Burn through both slots
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None

        # Third: blocked
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

class TestNUseBackwardCompat:
    """Legacy tokens missing the max_uses field are treated as N=1 (#720 Bug C).

    Tokens written by pre-#720 merge_guard_post lack max_uses /
    uses_remaining fields. _consume_token defaults them to single-use
    semantics — the first consume renames directly to .consumed (no slot
    markers), preserving prior behavior for in-flight legacy tokens.
    """

    def _write_legacy_token(self, tmp_path) -> str:
        """Write a token in the pre-#720 schema (no max_uses field)."""
        now = time.time()
        token_path = tmp_path / "merge-authorized-99999"
        token_path.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"repo": "o/r", "host": "github.com", "pr_number": "42", "operation_type": "merge"},
        }))
        return str(token_path)

    def test_legacy_token_second_use_blocked(self, tmp_path):
        """Second consume of a legacy token is the idempotent-recognize path
        (preserves the prior FileNotFoundError → .consumed-exists semantics)."""
        from merge_guard_pre import _consume_token

        token_path = self._write_legacy_token(tmp_path)
        assert _consume_token(token_path) is True

        # Second call: original gone, .consumed present → idempotent True
        # (this preserves pre-#720 behavior; a legacy token has no slot
        # markers so the n-use exhausted path is not triggered).
        assert _consume_token(token_path) is True

class TestNUseAuditEmit:
    """Per-consume stderr audit emit (#720 Bug C).

    Each successful slot claim emits a [security] line to stderr in the
    format `[security] merge-authorized token consumed (slot N/MAX):
    <basename>`. Format is invariant under MAX_USES changes — `slot N/MAX`
    reads correctly regardless of the current MAX_USES value.
    """

    def test_audit_emitted_for_each_slot(self, tmp_path, capfd):
        """Each consume emits exactly one [security] line."""
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)

        # Drain prior stderr (write_token also emits)
        capfd.readouterr()

        for slot in range(1, MAX_USES + 1):
            assert _consume_token(token_path) is True
            captured = capfd.readouterr()
            assert (
                f"[security] merge-authorized token consumed "
                f"(slot {slot}/{MAX_USES})" in captured.err
            )
            assert os.path.basename(token_path) in captured.err

    def test_no_audit_emit_when_slot_unclaimable(self, tmp_path, capfd):
        """When every slot is already claimed, _consume_token returns False
        and emits no [security] consume line for that invocation."""
        from shared.merge_guard_common import MAX_USES
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        for _ in range(MAX_USES):
            _consume_token(token_path)

        capfd.readouterr()  # Drain prior emits
        assert _consume_token(token_path) is False
        captured = capfd.readouterr()
        assert "[security] merge-authorized token consumed" not in captured.err


class TestNUseSlotMarkerCleanup:
    """`.use-N` markers are cleaned up alongside `.consumed` files (#720 Bug C).

    The cleanup_consumed_tokens helper now reaps both `.consumed` files
    and `.use-N` markers older than TOKEN_TTL.
    """

    def test_stale_use_markers_cleaned_up(self, tmp_path):
        """`.use-N` markers older than TOKEN_TTL are removed by cleanup."""
        from shared.merge_guard_common import (
            MAX_USES,
            TOKEN_TTL,
            USE_MARKER_SUFFIX,
            cleanup_consumed_tokens,
        )
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42"}, token_dir=tmp_path)
        for _ in range(MAX_USES):
            _consume_token(token_path)

        # Slot markers exist
        for slot in range(1, MAX_USES + 1):
            assert Path(token_path + USE_MARKER_SUFFIX + str(slot)).exists()

        # Age them past TTL
        old_mtime = time.time() - (2 * TOKEN_TTL)
        for marker in tmp_path.glob("merge-authorized-*.use-*"):
            os.utime(marker, (old_mtime, old_mtime))
        # Age the .consumed file too so it doesn't interfere
        os.utime(token_path + ".consumed", (old_mtime, old_mtime))

        cleanup_consumed_tokens(tmp_path)

        # All slot markers reaped
        remaining = list(tmp_path.glob("merge-authorized-*.use-*"))
        assert remaining == []

# =============================================================================
# Adversarial: command bypass attempts
# =============================================================================


# =============================================================================
# False-positive prevention: _strip_non_executable_content()
# =============================================================================


# =============================================================================
# Bypass vector prevention: execution-via-indirection
# =============================================================================


# =============================================================================
# Bare variable expansion bypass prevention
# =============================================================================


# =============================================================================
# Heredoc <<- with tab-indented closing marker
# =============================================================================


# =============================================================================
# git commit -m false positive prevention
# =============================================================================


# =============================================================================
# Edge cases: merge_guard_post
# =============================================================================


    # (removed the is_affirmative test methods — merge_guard_post.is_affirmative
    #  and its AFFIRMATIVE_PATTERNS regex were removed as production-orphaned dead
    #  code; OPTION-mode approval is an exact non-decline label match, not a
    #  free-text word allowlist. is_merge_question coverage stays above.)

    # (removed test_extract_pull_request_text / test_extract_branch_with_dots /
    #  test_extract_branch_with_underscores / test_extract_quoted_branch — they
    #  exercised the dropped prose extractor merge_guard_post.extract_context.
    #  Command-side target extraction is covered by extract_command_context in
    #  the bidirectional suite test_merge_guard_auth_symmetry.py.)


# =============================================================================
# Edge cases: token validation
# =============================================================================


class TestTokenEdgeCases:
    """Edge cases for token creation and validation."""

    def test_token_negative_expiry(self, tmp_path):
        """Token with negative expires_at is invalid."""
        from merge_guard_pre import find_valid_token

        token_data = {
            "created_at": time.time(),
            "expires_at": -1,
            "context": {},
        }
        (tmp_path / "merge-authorized-22222").write_text(json.dumps(token_data))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        assert not (tmp_path / "merge-authorized-22222").exists()

    def test_token_expiry_exactly_at_now(self, tmp_path):
        """Token with expires_at exactly equal to now is expired (< now)."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now - 300,
            "expires_at": now,  # Exactly now
            "context": {},
        }
        (tmp_path / "merge-authorized-44444").write_text(json.dumps(token_data))

        # expires_at < now may or may not be true due to time passing between
        # writing and reading; test both paths by mocking
        with patch("merge_guard_pre.time") as mock_time:
            mock_time.time.return_value = now + 0.001  # Just past expiry
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None

    def test_token_missing_expires_at(self, tmp_path):
        """Token with no expires_at field is treated as invalid."""
        from merge_guard_pre import find_valid_token

        token_data = {"created_at": time.time(), "context": {}}
        (tmp_path / "merge-authorized-11111").write_text(json.dumps(token_data))

        result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        assert path is None
        # Default expires_at=0 triggers the <= 0 check, token is cleaned up
        assert not (tmp_path / "merge-authorized-11111").exists()

# =============================================================================
# Edge cases: main() entry points
# =============================================================================


class TestPostMainEdgeCases:
    """Edge cases for merge_guard_post.main()."""

    def test_tool_response_none(self, tmp_path):
        """tool_response as None — non-dict, exits early, no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge?"}]},
            "tool_response": None,
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)), \
             patch("merge_guard_post.write_token") as mock_write_token:
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0
        # Cause-of-no-token: non-dict tool_response triggered isinstance early-exit
        # before the merge-question branch could reach write_token.
        mock_write_token.assert_not_called()

    def test_empty_stdin(self, tmp_path):
        """Empty stdin — exits 0 without error."""
        from merge_guard_post import main

        with patch("sys.stdin", io.StringIO("")):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0

class TestPreMainEdgeCases:
    """Edge cases for merge_guard_pre.main()."""

    def test_tool_input_as_string(self):
        """tool_input as a string instead of dict — fails closed (exit 2)."""
        from merge_guard_pre import main

        input_data = json.dumps({"tool_input": "not a dict"})

        with patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        # str.get() raises AttributeError → caught by outer except → fail closed
        assert exc_info.value.code == 2

    def test_deny_output_format(self, tmp_path, capsys):
        """Verify the exact JSON structure of a deny response."""
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "git push --force origin main"}
        })

        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)

        # Validate complete structure
        assert "hookSpecificOutput" in output
        hook_output = output["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "deny"
        assert isinstance(hook_output["permissionDecisionReason"], str)
        assert len(hook_output["permissionDecisionReason"]) > 0
        # Issue #658: hookEventName is the load-bearing schema field; without
        # it the harness silently rejects the deny block and merges proceed.
        assert hook_output["hookEventName"] == "PreToolUse"


# =============================================================================
# Security tests
# =============================================================================


class TestTokenSecurity:
    """Security-focused tests for the merge guard token mechanism."""

    def test_token_file_not_world_readable(self, tmp_path):
        """Token file must be 0o600 — not readable by others."""
        from merge_guard_post import write_token

        result = write_token({"sensitive": True, "operation_type": "merge"}, token_dir=tmp_path)
        assert result is not None

        mode = os.stat(result).st_mode
        # No group or other read/write/execute bits
        assert mode & stat.S_IRGRP == 0
        assert mode & stat.S_IWGRP == 0
        assert mode & stat.S_IXGRP == 0
        assert mode & stat.S_IROTH == 0
        assert mode & stat.S_IWOTH == 0
        assert mode & stat.S_IXOTH == 0

    def test_post_hook_never_blocks(self, tmp_path):
        """Post hook always exits 0, even on internal errors."""
        from merge_guard_post import main

        # Force an error by making TOKEN_DIR a file instead of directory
        bad_dir = tmp_path / "not_a_dir"
        bad_dir.write_text("I am a file")

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Merge PR #1?"}]},
            "tool_response": {"answers": {"Merge PR #1?": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", bad_dir), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        # Observer hook must NEVER block, even on errors
        assert exc_info.value.code == 0

# =============================================================================
# hooks.json registration tests
# =============================================================================


HOOKS_DIR = Path(__file__).parent.parent / "hooks"


# =============================================================================
# API bypass pattern detection
# =============================================================================


# =============================================================================
# API-based branch deletion detection (gh api + curl DELETE git/refs)
# =============================================================================


# =============================================================================
# API-based ref mutation / force push detection (PATCH/POST/PUT to git/refs)
# =============================================================================


# =============================================================================
# API-based push to main/master detection (PATCH/POST/PUT to git/refs/heads/main|master)
# =============================================================================


# =============================================================================
# API bypass — false positive prevention via stripping pipeline
# =============================================================================


# =============================================================================
# API bypass — line continuation normalization
# =============================================================================


# =============================================================================
# API bypass — authorization flow integration
# =============================================================================


class TestAPIBypassAuthorizationFlow:
    """Integration tests for API bypass commands with the token authorization system."""

    def test_gh_api_delete_ref_blocked_without_token(self, tmp_path):
        """gh api DELETE to git/refs is blocked without authorization token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh api -X DELETE repos/owner/repo/git/refs/heads/feature",
            token_dir=tmp_path,
        )
        assert result is not None
        assert "approval" in result.lower() or "AskUserQuestion" in result

    def test_gh_api_patch_main_blocked_without_token(self, tmp_path):
        """gh api PATCH to main is blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh api -X PATCH repos/owner/repo/git/refs/heads/main -f sha=abc",
            token_dir=tmp_path,
        )
        assert result is not None
        assert "approval" in result.lower() or "AskUserQuestion" in result

    def test_curl_patch_ref_denied_even_with_token(self, tmp_path):
        """curl PATCH to git/refs is gated but never approvable (amend_7 on #141: no
        sound flag walk over curl's options, so its URL path names no identity): even a
        matching-looking token is refused, and the command always asks."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "force-push", "target_ref": "feature"},
                    token_dir=tmp_path)

        result = check_merge_authorization(
            "curl -X PATCH https://api.github.com/repos/owner/repo/git/refs/heads/feature",
            token_dir=tmp_path,
        )
        assert result is not None  # Refused: curl never mints

# =============================================================================
# gh api implicit POST detection (-f, -F, --field, --raw-field, --input)
# =============================================================================


# =============================================================================
# curl implicit POST detection (-d, --data, --data-raw, --data-binary)
# =============================================================================


# =============================================================================
# Contents API detection (write operations to /contents/ on main/master)
# =============================================================================


# =============================================================================
# wget and httpie detection (alternative HTTP clients)
# =============================================================================


# =============================================================================
# Tool-specific flag exclusion tests (document intentional flag specificity)
# =============================================================================


# =============================================================================
# Variable indirection bypass documentation
# =============================================================================


# =============================================================================
# Heredoc-to-shell tests for API patterns
# =============================================================================


# =============================================================================
# Multi-break line continuation tests for API patterns
# =============================================================================


# =============================================================================
# curl --request merge pattern fix (M2: previously only matched -X)
# =============================================================================


# =============================================================================
# Direct push to main/master detection
# =============================================================================


class TestDirectPushToDefaultBranch:
    """Tests for detecting regular pushes to main/master branches."""


    def test_git_push_origin_main_with_valid_token(self, tmp_path):
        """git push origin main is allowed with a valid authorization token."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token(git_bound({"operation_type": "push-to-main", "target_ref": "main"},
                              "git push origin main"), token_dir=tmp_path)

        result = check_merge_authorization("git push origin main", token_dir=tmp_path)
        assert result is None  # Allowed

    def test_git_push_origin_main_blocked_without_token(self, tmp_path):
        """git push origin main is blocked without a token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("git push origin main", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result


# =============================================================================
# Operation scoping (_token_matches_command)
# =============================================================================


class TestOperationScoping:
    """Tests for _token_matches_command — operation scoping validation."""

    def test_token_with_matching_pr_number(self):
        """A typed merge token matches a command with the same PR number."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42"}}
        assert _token_matches_command(token, "gh pr merge 42")

    def test_token_with_mismatched_pr_number(self):
        """A typed merge token does NOT match a different PR number (positive op,
        mismatched target -> REFUSE)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42"}}
        assert not _token_matches_command(token, "gh pr merge 99")

    def test_token_with_matching_branch(self):
        """A typed branch-delete token matches a branch -D command."""
        from merge_guard_pre import _token_matches_command

        token = {"context": git_bound({"operation_type": "branch-delete", "branch": "old-feature"}, "git branch -D old-feature")}
        assert _token_matches_command(token, "git branch -D old-feature")

    def test_untyped_branch_context_token_denies_any_command(self):
        """#1032 CLOSURE (was allow-through, A1/A2 read-floor): an UNTYPED token
        (branch present but NO operation_type) authorizes NOTHING. Op-LESS so it
        is C2-coupled (revert C2 -> the untyped token authorizes -> RED)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"branch": "old"}}
        assert not _token_matches_command(token, "gh pr merge 42")

    def test_untyped_pr_context_token_denies_any_command(self):
        """#1032 CLOSURE (was allow-through, A1/A2 read-floor): an UNTYPED token
        (pr_number present but NO operation_type) authorizes NOTHING. The old
        read fell open for op_type=None (skipped the typed cross-op guard, then
        terminal-allowed); the fix denies on the op-type axis. Op-LESS so it is
        genuinely C2-coupled (revert C2 -> the untyped token authorizes -> RED)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42"}}
        assert not _token_matches_command(token, "git push --force origin main")

    def test_empty_context_denies_all(self):
        """FAIL-CLOSED (was wildcard-allow): an empty context has no
        operation_type, so it authorizes NOTHING."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {}}
        assert not _token_matches_command(token, "gh pr merge 42")
        assert not _token_matches_command(token, "git branch -D anything")

    def test_malformed_context_denies(self):
        """FAIL-CLOSED (F-READ-1, was graceful-allow): a non-dict context proves
        nothing -> REFUSE."""
        from merge_guard_pre import _token_matches_command

        token = {"context": "not a dict"}
        assert not _token_matches_command(token, "gh pr merge 42")

    def test_branch_delete_multi_target_denied(self):
        """A single-branch token must NOT authorize a MULTI-target delete (the
        command also removes an UNAPPROVED branch). Revert-coupled to the #30
        _extract_branch_name multi-target fix (revert #30 -> RED)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "a"}}
        assert not _token_matches_command(token, "git branch -D a b")
        assert not _token_matches_command(token, "git branch --delete --force a b")

    def test_token_with_mismatched_branch(self):
        """A typed branch-delete token does NOT match a different branch."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "old-feature"}}
        assert not _token_matches_command(token, "git branch -D other-branch")

    # ── HALT #29 branch-delete multi-target: deny + single-target no-regression
    #    controls (architect addendum). The #30 fix refuses a multi-target
    #    `git branch -D a b` (extra unapproved branch); these controls guard
    #    against it over-correcting into a #1031 over-block on the legit single
    #    case. Mirrors the force-push target-axis pair. ──
# =============================================================================
# Session scoping (pact_context session_id)
# =============================================================================


class TestSessionScoping:
    """Tests for session-scoped token isolation."""

    def test_same_session_token_accepted(self, tmp_path):
        """Token from same session is accepted."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "session-abc",
        }
        (tmp_path / "merge-authorized-11111").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value="session-abc"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None

    def test_different_session_token_rejected(self, tmp_path):
        """Token from different session is skipped."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "session-other",
        }
        (tmp_path / "merge-authorized-22222").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value="session-abc"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        # Token NOT cleaned up — it may be valid for its own session
        assert (tmp_path / "merge-authorized-22222").exists()

    def test_no_session_id_reads_only_a_token_minted_without_one(self, tmp_path):
        """A reader with no session id took any session's token; it now reads only a token
        minted without one (rows: tests/test_merge_guard_pre.py)."""
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "session-xyz",
        }
        (tmp_path / "merge-authorized-33333").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value=""):
            result, path = find_valid_token(token_dir=tmp_path)
            assert result is None
            del token_data["session_id"]
            (tmp_path / "merge-authorized-33334").write_text(json.dumps(token_data))
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is not None and path.endswith("33334")

    def test_token_without_session_REJECTED_when_current_session_known(self, tmp_path):
        """SEC-S1 cycle-2 inversion: token without session_id is REJECTED
        when the current session is known.

        Pre-cycle-2 (cycle-1 short-circuit predicate
        `if current_session and token_session and current_session != token_session`):
        the AND-short-circuit on `not token_session` left tokens with empty
        session_id through — the bypass surface SEC-S1 closes.

        Cycle-2 revised asymmetric predicate at merge_guard_pre.py:635:
        `if current_session: if not token_session or current_session != token_session: continue`
        — when current_session IS populated, both an empty token_session AND
        a foreign token_session reject. Graceful-degradation preserved at
        :5068 (test_no_session_id_accepts_any_token): when current_session
        is empty, any valid token still accepted.

        See architect §3 (asymmetric predicate design) + Task #33 HANDOFF
        for the design rationale.

        # counter-test: remove the inner `if not token_session` half of the
        #               predicate at merge_guard_pre.py:645 → empty token_
        #               session falls through to acceptance (the cycle-1
        #               attack surface re-opens); assertion `result is None`
        #               FAILS because result is a valid token dict.
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
        }
        (tmp_path / "merge-authorized-44444").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value="session-abc"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None

    def test_sec_s1_rejects_empty_token_session_when_current_session_known(
        self, tmp_path
    ):
        """SEC-S1 cycle-2: explicit empty-token_session rejection.

        Parallel to test_token_without_session_REJECTED_when_current_session_known
        but uses an explicit empty `session_id` field (vs. the missing field
        in :5086). Pins the same fail-CLOSED behavior for both shapes:
          - {"session_id": ""}   (this test)
          - {} (no session_id)   (:5086)
        Both are bypass-surface attacker shapes under the cycle-1 predicate;
        both reject under cycle-2.

        # counter-test: revert merge_guard_pre.py:635-646 to the cycle-1
        #               short-circuit predicate
        #                   `if current_session and token_session and
        #                    current_session != token_session: continue`
        #               → empty token_session AND-short-circuits to NOT
        #               continue → token accepted; assertion FAILS.
        # NOTE: removing ONLY the inner `if not token_session` half is NOT
        # load-bearing for this fixture — `current_session != ""` is also
        # True so the surviving half still rejects. The cycle-1 short-
        # circuit revert is the discriminative mutation (verified during
        # TEST-phase cp-revert-test-restore sampling).
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "",  # Explicit empty string (attacker-written shape)
        }
        (tmp_path / "merge-authorized-55555").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value="real-session-abc"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        # Token NOT cleaned up — it may be valid for some other session
        # context (preserves the cycle-1 don't-cleanup-foreign convention)
        assert (tmp_path / "merge-authorized-55555").exists()

    def test_sec_s1_rejects_foreign_token_session_when_current_session_known(
        self, tmp_path
    ):
        """SEC-S1 cycle-2: foreign-session rejection under populated current.

        This is the cycle-1 behavior preserved verbatim (foreign session
        always rejected) but anchored under the new asymmetric predicate
        framing. Documents that the AND-tightening at
        merge_guard_pre.py:645 (`if not token_session OR current_session
        != token_session`) preserves the foreign-session rejection half
        exactly while ADDING the empty-token_session rejection half.

        # counter-test: remove the inner `current_session != token_session`
        #               half of the predicate at merge_guard_pre.py:645 →
        #               foreign-session token would fall through to
        #               acceptance; assertion FAILS.
        # expected RED cardinality: {1}
        """
        from merge_guard_pre import find_valid_token

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {},
            "session_id": "other-session-B",
        }
        (tmp_path / "merge-authorized-66666").write_text(json.dumps(token_data))

        with patch("merge_guard_pre.get_session_id", return_value="real-session-A"):
            result, path = find_valid_token(token_dir=tmp_path)
        assert result is None
        # Token NOT cleaned up — it may be valid for its own session
        assert (tmp_path / "merge-authorized-66666").exists()


# =============================================================================
# Fail-closed behavior
# =============================================================================


class TestFailClosed:
    """Tests for fail-closed behavior in the pre-hook."""

    def test_fail_closed_on_exception_in_main(self, capsys):
        """main() outputs deny JSON and exits 2 on unexpected exception."""
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        with patch("merge_guard_pre.check_merge_authorization",
                   side_effect=RuntimeError("boom")), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        # Issue #658: fail-closed deny must include hookEventName.
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_fail_closed_even_if_deny_output_fails(self):
        """If deny JSON output itself fails, exit 2 still happens."""
        from merge_guard_pre import main

        input_data = json.dumps({
            "tool_input": {"command": "gh pr merge 42"}
        })

        # Patch json.dumps to fail inside the except handler's try block,
        # which causes the inner except Exception: pass to trigger.
        # The sys.exit(2) still runs afterward.
        original_dumps = json.dumps
        call_count = [0]

        def failing_dumps(*args, **kwargs):
            call_count[0] += 1
            # First call is from json.load succeeding (not dumps).
            # The except handler calls json.dumps(output) — fail that one.
            if call_count[0] >= 1:
                raise TypeError("simulated dumps failure")
            return original_dumps(*args, **kwargs)

        with patch("merge_guard_pre.check_merge_authorization",
                   side_effect=RuntimeError("boom")), \
             patch("merge_guard_pre.json.dumps", side_effect=failing_dumps), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 2


# =============================================================================
# Token write inner exception handler
# =============================================================================


class TestTokenWriteExceptionHandling:
    """Tests for write_token's exception handling during file operations."""

    def test_double_collision_returns_none(self, tmp_path):
        """When both primary and fallback filenames exist, returns None.

        Layer 5 (I-1) retires unused tokens before O_EXCL, so under
        normal use the primary O_EXCL cannot collide with a pre-existing
        unused token. This test pins the double-collision returns-None
        behavior independently of Layer 5 by stubbing cleanup so the
        collision survives to O_EXCL (mirroring a transient cleanup
        failure or a concurrent same-microsecond race).
        """
        import merge_guard_post
        from merge_guard_post import write_token

        now = time.time()
        timestamp = int(now)

        # Pre-create the primary and every fallback name write_token tries
        (tmp_path / f"merge-authorized-{timestamp}").write_text("taken")
        for n in range(1, merge_guard_post._MAX_NAME_TRIES):
            (tmp_path / f"merge-authorized-{timestamp}-{n}").write_text("taken")

        with patch.object(merge_guard_post, "_cleanup_unused_tokens", lambda *_a: None), \
             patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = now
            result = write_token({"test": True, "operation_type": "merge"}, token_dir=tmp_path)

        assert result is None

    def test_fdopen_failure_cleans_up_primary(self, tmp_path):
        """If fdopen/json.dump fails after os.open, the created file is removed."""
        from merge_guard_post import write_token

        original_fdopen = os.fdopen

        call_count = [0]

        def failing_fdopen(fd, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Close the fd before raising to avoid resource leak
                os.close(fd)
                raise OSError("simulated fdopen failure")
            return original_fdopen(fd, *args, **kwargs)

        with patch("merge_guard_post.os.fdopen", side_effect=failing_fdopen):
            result = write_token({"test": True, "operation_type": "merge"}, token_dir=tmp_path)

        # Should return None because the primary write failed and the retry
        # path (FileExistsError) is not triggered (different exception type)
        assert result is None

# =============================================================================
# AskUserQuestion schema extraction edge cases (issue #253)
# =============================================================================


class TestQuestionExtractionEdgeCases:
    """Tests for isinstance guards on the questions array extraction path.

    The fix extracts question from tool_input["questions"][0]["question"] with
    isinstance guards at each level. Every malformed input must result in
    question="" which prevents token creation (fail-closed).
    """

    def test_questions_is_string_not_list(self, tmp_path):
        """questions is a string instead of a list — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": "Should I merge?"},
            "tool_response": {"answers": {"Should I merge?": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_questions_first_element_is_nested_list(self, tmp_path):
        """questions[0] is a nested list instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [["nested", "list"]]},
            "tool_response": {"answers": {"q": "yes"}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

class TestAnswerExtractionEdgeCases:
    """Tests for isinstance guards on the answers dict extraction path.

    The fix extracts answer from tool_response["answers"] dict using
    next(iter(values())). Every malformed input must result in
    answer="" which prevents token creation (fail-closed).
    """

    def test_answers_is_list_not_dict(self, tmp_path):
        """answers is a list instead of dict — no token."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_response": {"answers": ["yes"]},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_answers_value_is_boolean_true(self, tmp_path):
        """answers value is boolean True — str(True) = 'True', not affirmative."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {"questions": [{"question": "Should I merge?"}]},
            "tool_response": {"answers": {"Should I merge?": True}},
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

class TestSchemaFixEndToEnd:
    """End-to-end tests using exact AskUserQuestion schema format.

    These tests verify the complete flow: post-hook creates token from
    correctly-formatted AskUserQuestion data, pre-hook reads and consumes it.
    """

    def test_exact_session_log_format(self, tmp_path):
        """Test with the exact format observed in session logs (issue #253).

        This is the canonical AskUserQuestion format with full question
        structure including header, options, and multiSelect.
        """
        from merge_guard_post import main as post_main
        from merge_guard_pre import main as pre_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{
                    "question": "Confirm merge of PR #252 to main?",
                    "header": "Merge",
                    "options": [
                        {"label": "Yes, merge", "description": "Run `gh pr merge 252`"},
                        {"label": "Cancel", "description": "Abort the merge"},
                    ],
                    "multiSelect": False,
                }]
            },
            "tool_response": {
                "questions": [{
                    "question": "Confirm merge of PR #252 to main?",
                    "header": "Merge",
                    "options": [
                        {"label": "Yes, merge", "description": "Run `gh pr merge 252`"},
                        {"label": "Cancel", "description": "Abort the merge"},
                    ],
                    "multiSelect": False,
                }],
                "answers": {
                    "Confirm merge of PR #252 to main?": "Yes, merge",
                },
            },
        })

        # Post hook: create token
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)):
            with pytest.raises(SystemExit) as exc_info:
                post_main()
        assert exc_info.value.code == 0

        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1

        # Verify token content
        token_data = json.loads(tokens[0].read_text())
        assert "created_at" in token_data
        assert "expires_at" in token_data
        assert token_data["expires_at"] > token_data["created_at"]
        assert "context" in token_data
        assert token_data["context"]["pr_number"] == "252"

        # Pre hook: consume token MAX_USES times (terminal rename happens
        # on the final slot under #720 Bug C).
        from shared.merge_guard_common import MAX_USES, USE_MARKER_SUFFIX

        for _ in range(MAX_USES):
            # #1042: execution flags must match the approval (`gh pr merge 252`,
            # no privileged flags). --squash is unbound; --delete-branch (a bound
            # merge side-effect) would now correctly mismatch an unflagged approval.
            pre_input = json.dumps({
                "tool_input": {"command": "gh pr merge 252 --squash"}
            })
            with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
                 patch("sys.stdin", io.StringIO(pre_input)):
                with pytest.raises(SystemExit) as exc_info:
                    pre_main()
            assert exc_info.value.code == 0

        # Token should be terminally consumed after MAX_USES uses.
        active_tokens = [
            p for p in tmp_path.glob("merge-authorized-*")
            if not p.name.endswith(".consumed")
            and USE_MARKER_SUFFIX not in p.name
        ]
        assert len(active_tokens) == 0
        consumed_tokens = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed_tokens) == 1

    def test_force_push_approval_flow(self, tmp_path):
        """Full flow for force push approval."""
        from merge_guard_post import main as post_main
        from merge_guard_pre import main as pre_main

        post_input = json.dumps({
            "tool_input": {
                "questions": [{
                    "question": "Force push to main? This will overwrite remote history.",
                    "options": [
                        {"label": "Yes, force-push", "description": "Run `git push --force origin main`"},
                        {"label": "Cancel", "description": "Abort"},
                    ],
                }]
            },
            "tool_response": {
                "answers": {
                    "Force push to main? This will overwrite remote history.": "Yes, force-push",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(post_input)):
            with pytest.raises(SystemExit):
                post_main()

        assert len(list(tmp_path.glob("merge-authorized-*"))) == 1

        pre_input = json.dumps({
            "tool_input": {"command": "git push --force origin main"}
        })
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(pre_input)):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 0

    def test_multi_question_answer_mismatch_no_spurious_token(self, tmp_path):
        """Merge question denied but other question affirmed — no token.

        Regression test: when questions[0] is merge-related but the user
        denied it, and a different question's affirmative answer appears
        first in the answers dict, no token should be created. The fix
        uses answers.get(question) for explicit key lookup instead of
        next(iter(answers.values())).
        """
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [
                    {"question": "Merge PR #42 to main?"},
                    {"question": "Update the changelog?"},
                ]
            },
            "tool_response": {
                "answers": {
                    "Update the changelog?": "yes",
                    "Merge PR #42 to main?": "no",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        # No token — user denied the merge question, even though
        # "yes" from the changelog question appeared first in the dict
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

    def test_multi_question_command_in_question_prose_only_refuses(self, tmp_path):
        """#32 F-REVIEW-1: when the command is in QUESTION PROSE only (the clicked
        option carries NO command), the mint REFUSES — the operator clicked a
        generic option, never the command. This is the option-anchoring closure
        (sibling of the mints-from-clicked-option case above)."""
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [
                    {"question": "Should I run `gh pr merge 42`?", "options": [
                        {"label": "Yes, proceed", "description": "go ahead"},
                        {"label": "Cancel", "description": "Abort"},
                    ]},
                    {"question": "Also update the changelog?", "options": [
                        {"label": "Yes", "description": "Update it"},
                    ]},
                ]
            },
            "tool_response": {
                "answers": {
                    "Should I run `gh pr merge 42`?": "Yes, proceed",
                    "Also update the changelog?": "Yes",
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        assert list(tmp_path.glob("merge-authorized-*")) == []

    def test_question_key_mismatch_does_not_fall_back(self, tmp_path):
        """KD-12 KILLED the next(iter(answers.values())) fallback: when the
        answers key does not EXACTLY match the question text (e.g. a trailing
        space), that question has NO answer — there is no fallback to the first
        dict value — so NO token mints, even though the question embeds a command.
        """
        from merge_guard_post import main

        input_data = json.dumps({
            "tool_input": {
                "questions": [{"question": "Run `gh pr merge 42`?"}]
            },
            "tool_response": {
                "answers": {
                    "Run `gh pr merge 42`? ": "yes",  # trailing space — key mismatch
                },
            },
        })

        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit):
                main()

        # No fallback: the question's answer is absent (key mismatch) → no mint.
        assert len(list(tmp_path.glob("merge-authorized-*"))) == 0

# =============================================================================
# Process substitution bypass prevention
# =============================================================================


class TestProcessSubstitutionBypass:
    """Verify that process substitution fed to a shell interpreter is NOT
    stripped and remains detectable as dangerous.

    ``bash <(echo 'gh pr merge 42')`` executes the echo output via the
    shell, so echo content must not be stripped.
    """


    def test_strip_helper_process_sub(self):
        """_strip_non_executable_content preserves echo args when process sub to shell."""
        from merge_guard_pre import _strip_non_executable_content

        result = _strip_non_executable_content("bash <(echo 'gh pr merge 42')")
        assert "gh pr merge" in result


# =============================================================================
# xargs piping bypass prevention
# =============================================================================


# =============================================================================
# Here-string false positive prevention
# =============================================================================


# =============================================================================
# Idempotent Token Consumption — comprehensive tests
# =============================================================================


class TestIdempotentTokenConsumption:
    """Comprehensive tests for the rename-to-.consumed idempotent consumption mechanism.

    Covers: _consume_token(), _cleanup_consumed_tokens() (both hooks),
    find_valid_token() skipping .consumed files, check_merge_authorization()
    integration with _consume_token(), and edge cases.
    """

    # -------------------------------------------------------------------------
    # _consume_token() unit tests
    # -------------------------------------------------------------------------

    def test_consume_token_successful_rename(self, tmp_path):
        """_consume_token renames the file to .consumed and returns True."""
        from merge_guard_pre import _consume_token

        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text('{"test": true}')

        result = _consume_token(str(token_file))

        assert result is True
        assert not token_file.exists()
        assert (tmp_path / "merge-authorized-99999.consumed").exists()

    def test_consume_token_idempotent_when_already_consumed(self, tmp_path):
        """_consume_token returns True when .consumed file already exists (concurrent invocation)."""
        from merge_guard_pre import _consume_token

        token_path = str(tmp_path / "merge-authorized-99999")
        # Simulate prior consumption: only .consumed exists
        consumed_file = tmp_path / "merge-authorized-99999.consumed"
        consumed_file.write_text('{"test": true}')

        result = _consume_token(token_path)

        assert result is True

    def test_consume_token_fails_closed_on_generic_oserror(self, tmp_path):
        """_consume_token returns False on generic OSError (e.g., I/O error)."""
        from merge_guard_pre import _consume_token

        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text('{"test": true}')

        with patch("merge_guard_pre.os.rename", side_effect=OSError(5, "I/O error")):
            result = _consume_token(str(token_file))

        assert result is False

    # -------------------------------------------------------------------------
    # _cleanup_consumed_tokens() tests (pre-hook version)
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Post-hook _cleanup_consumed_tokens() tests
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # write_token() triggers cleanup
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # find_valid_token() skips .consumed files
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # check_merge_authorization() + _consume_token() integration
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Edge cases
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Full lifecycle integration
    # -------------------------------------------------------------------------

# =============================================================================
# gh pr close: dangerous command detection
# =============================================================================


class TestGhPrCloseDetection:
    """Tests for gh pr close detection in merge guard pre-hook.

    Every 'gh pr close' is dangerous (FIX-MERGE f3, amend_1 a: PR close is on the S5
    irreversible list). The --delete-branch variant stays a distinct approval through
    the close op's bound --delete-branch flag.

    Covers: --delete-branch position variants, bare close (safe), flags,
    chained commands, false positive prevention, and edge cases.
    """

    # -------------------------------------------------------------------------
    # Bare gh pr close (no --delete-branch) is GATED (FIX-MERGE f3)
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # gh pr close --delete-branch IS dangerous
    # -------------------------------------------------------------------------


    def test_gh_pr_close_delete_branch_parenthesized_comment_not_overblocked(self, tmp_path):
        """REGRESSION GUARD — the marquee faithful-click over-block: a close carrying a
        PARENTHESIZED comment value, `gh pr close 7 --comment "(done)" --delete-branch`,
        must be GATED (is_dangerous) yet classified as a SINGLE op (not >=2-compound) and
        AUTHORIZE end-to-end on a faithful click. The `(`/`)` inside the quoted comment
        are exactly what the now-removed metachar fail-closed suppressor / read
        deny-outright over-blocked; this pins the invariant that a faithful
        single-command click always mints and executes.

        NON-VACUITY: the end-to-end authorize is the load-bearing assertion — a
        re-introduced metachar suppressor / deny-outright would refuse this faithful
        click despite a valid token, flipping the final assert RED. The metachar-free
        sibling test_gh_pr_close_delete_branch_with_comment ('done') does NOT exercise
        the metachar path, so this guard is not redundant with it.
        """
        from merge_guard_pre import (
            is_dangerous_command,
            is_compound_destructive_command,
            check_merge_authorization,
        )
        from merge_guard_post import write_token

        cmd = 'gh pr close 7 --comment "(done)" --delete-branch'
        # Gated as a single destructive op — NOT refused-outright as >=2-compound.
        assert is_dangerous_command(cmd) is True
        assert is_compound_destructive_command(cmd) is False
        # Faithful click: the clicked option mints a token for this exact command and
        # the read side AUTHORIZES it (returns None) — no metachar over-block.
        ctx = minted_context(cmd)
        assert write_token(ctx, token_dir=tmp_path) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None


    # -------------------------------------------------------------------------
    # Whitespace and formatting edge cases
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # Chained commands with --delete-branch
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # Bare close in chained commands is SAFE
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # False positive prevention (stripping logic) with --delete-branch
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # Bypass vector prevention with --delete-branch
    # -------------------------------------------------------------------------


    # -------------------------------------------------------------------------
    # Negative tests: similar but safe commands
    # -------------------------------------------------------------------------


# =============================================================================
# gh pr close: merge_guard_post keyword detection
# =============================================================================


class TestGhPrClosePostHook:
    """Tests for gh pr close keyword detection in merge_guard_post.

    Ensures the post-hook creates authorization tokens when
    AskUserQuestion text mentions closing PRs.
    """

    def test_close_pr_command_detected_not_prose(self):
        """KD-9 command-driven: a `gh pr close` command fires the hint; bare
        'close PR' prose without a command does NOT."""
        from merge_guard_post import is_merge_question

        assert is_merge_question("Should I run `gh pr close 42`?")
        assert not is_merge_question("Should I close PR #42?")

    def test_pr_close_prose_alone_not_detected(self):
        """'PR close' prose with no command is NOT detected."""
        from merge_guard_post import is_merge_question

        assert not is_merge_question("PR close requested for #42")

# =============================================================================
# gh pr close: token context matching
# =============================================================================


class TestGhPrCloseTokenMatching:
    """Tests for _token_matches_command with gh pr close commands."""

    def test_token_matches_gh_pr_close_same_pr(self):
        """Token with PR number matches gh pr close for same PR."""
        from merge_guard_pre import _token_matches_command

        # GAP2: a faithful close --delete-branch token binds --delete-branch, so it
        # set-matches the --delete-branch exec (bare-close→delete escalation is the
        # NEW escalation-denied counter-test below).
        token = {"context": {"repo": "o/r", "host": "github.com", "pr_number": "42", "operation_type": "close", "bound_flags": ["--delete-branch"]}}
        assert _token_matches_command(token, "gh pr close 42 --delete-branch")

    def test_token_rejects_gh_pr_close_different_pr(self):
        """Token with PR number rejects gh pr close for different PR."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"repo": "o/r", "host": "github.com", "pr_number": "42", "operation_type": "close"}}
        assert not _token_matches_command(token, "gh pr close 99 --delete-branch")

    def test_token_without_operation_type_denies_any(self):
        """FAIL-CLOSED (was backward-compat allow-any): an untyped token
        (operation_type absent) authorizes NOTHING — no close, no merge."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"pr_number": "42"}}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")
        assert not _token_matches_command(token, "gh pr merge 42")

    def test_token_branch_context_denies_gh_pr_close(self):
        """FAIL-CLOSED (was ambiguous-permissive): a branch-only context (no
        operation_type) does NOT authorize a gh pr close."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"branch": "feat/old"}}
        assert not _token_matches_command(token, "gh pr close 42 --delete-branch")

# =============================================================================
# gh pr close: full authorization flow
# =============================================================================


class TestGhPrCloseAuthorization:
    """Integration tests for gh pr close --delete-branch through check_merge_authorization."""

    def test_gh_pr_close_delete_branch_blocked_without_token(self, tmp_path):
        """gh pr close --delete-branch is blocked when no token exists."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None
        assert "approval" in result.lower()

    def test_gh_pr_close_bare_denied_without_token(self, tmp_path):
        """FIX-MERGE f3 (amend_1 a): a bare close now gates like any close. It needs an approval token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization("gh pr close 42", token_dir=tmp_path)
        assert result is not None

    def test_bare_close_token_does_not_authorize_delete_branch_escalation(self, tmp_path):
        """GAP2 escalation-denied: a bare-close token (bound_flags=[]) does NOT
        authorize `gh pr close N --delete-branch`. PR + op MATCH, so the deny is
        attributable SOLELY to the unbound --delete-branch (not a PR/op mismatch).
        Non-vacuity: revert the GAP2 bind (drop --delete-branch from
        PRIVILEGED_FLAGS['close']) → both bound_flags become [] → match → flips to allow."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            # MATCHING pr+op, NO --delete-branch bound → the bare-close token.
            "context": {"repo": "o/r", "host": "github.com", "pr_number": "42", "operation_type": "close", "bound_flags": []},
        }))

        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is not None  # DENIED — a bare-close token cannot authorize the --delete-branch escalation

    def test_close_token_cannot_authorize_merge(self, tmp_path):
        """A close token cannot authorize a merge."""
        import time

        from merge_guard_pre import check_merge_authorization

        now = time.time()
        token_file = tmp_path / "merge-authorized-99999"
        token_file.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + 300,
            "context": {"repo": "o/r", "host": "github.com", "pr_number": "42", "operation_type": "close"},
        }))

        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()

# =============================================================================
# gh pr close: adversarial bypass vector tests (TEST phase)
# =============================================================================


# =============================================================================
# gh pr close: additional edge cases (TEST phase)
# =============================================================================


# =============================================================================
# gh pr close: regression tests for existing patterns (TEST phase)
# =============================================================================


# =============================================================================
# gh pr close: pre-hook main() entry point E2E (TEST phase)
# =============================================================================


class TestGhPrClosePreHookE2E:
    """End-to-end tests for the pre-hook main() with gh pr close commands."""

    def test_pre_hook_blocks_gh_pr_close_delete_branch(self, tmp_path, capsys):
        """Pre-hook main() blocks gh pr close --delete-branch and exits 2."""
        from merge_guard_pre import main

        input_data = {
            "tool_input": {"command": "gh pr close 42 --delete-branch"}
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_pre.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 2

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        # Issue #658: deny-emit must include hookEventName uniformity polish.
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"

    def test_pre_hook_denies_bare_gh_pr_close(self, tmp_path, capsys):
        """FIX-MERGE f3 (amend_1 a): a bare close now gates like any close. Pre-hook main() denies it without a token."""
        from merge_guard_pre import main

        input_data = {
            "tool_input": {"command": "gh pr close 42"}
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_pre.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit):
                main()

        captured = capsys.readouterr()
        assert json.loads(captured.out)["hookSpecificOutput"]["permissionDecision"] == "deny"

# =============================================================================
# gh pr close: post-hook main() E2E (TEST phase)
# =============================================================================


class TestGhPrClosePostHookE2E:
    """End-to-end tests for the post-hook main() with gh pr close questions."""

    def test_post_hook_writes_token_for_gh_pr_close_question(self, tmp_path, capsys):
        """Post-hook main() creates token for a DANGEROUS 'gh pr close' question.
        (Under the GAP1 is_dangerous-gate a BARE `gh pr close 99` is reversible →
        mints NO token; the close must carry --delete-branch to be gated + mint.)"""
        from merge_guard_post import main

        input_data = {
            "tool_input": {
                "questions": [{
                    "question": "Close the PR?",
                    "options": [
                        {"label": "Yes, close", "description": "Run `gh pr close 99 --delete-branch`"},
                        {"label": "Cancel", "description": "Abort"},
                    ],
                }]
            },
            "tool_response": {
                "answers": {"Close the PR?": "Yes, close"}
            },
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_post.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        token_files = list(tmp_path.glob("merge-authorized-*"))
        assert len(token_files) == 1

    def test_post_hook_no_token_for_negative_close_answer(self, tmp_path, capsys):
        """Post-hook does NOT create token when user declines closing a PR."""
        from merge_guard_post import main

        input_data = {
            "tool_input": {
                "questions": [{"question": "Should I close PR #42?"}]
            },
            "tool_response": {
                "answers": {"Should I close PR #42?": "no"}
            },
        }
        stdin = io.StringIO(json.dumps(input_data))

        with (
            patch("sys.stdin", stdin),
            patch("merge_guard_post.TOKEN_DIR", tmp_path),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

        token_files = list(tmp_path.glob("merge-authorized-*"))
        assert len(token_files) == 0

# =============================================================================
# gh pr close: token matching edge cases (TEST phase)
# =============================================================================


# =============================================================================
# gh pr close: full pre-to-post integration (TEST phase)
# =============================================================================


class TestGhPrCloseFullIntegration:
    """Integration tests for the complete close flow: question -> token -> command.

    Tests the full pipeline: post-hook writes token on approval,
    pre-hook reads token and authorizes the matching command.
    """

    def test_close_flow_approve_then_execute(self, tmp_path):
        """Full flow: user approves a command-bearing close question → token
        minted from the command → matching command allowed."""
        from merge_guard_post import is_merge_question, write_token
        from merge_guard_pre import check_merge_authorization

        # Step 1: the command-driven hint fires on the embedded close command.
        question = "Should I run `gh pr close 42 --delete-branch`?"
        assert is_merge_question(question)

        # Step 2: mint a token from the command (the command-anchored SSOT).
        context = minted_context("gh pr close 42 --delete-branch")
        assert context["pr_number"] == "42"
        assert context["operation_type"] == "close"
        token_path = write_token(context, token_dir=tmp_path)
        assert token_path is not None

        # Step 3: pre-hook authorizes the matching close --delete-branch command.
        result = check_merge_authorization("gh pr close 42 --delete-branch", token_dir=tmp_path)
        assert result is None

    def test_close_flow_token_rejects_wrong_pr(self, tmp_path):
        """Token for PR #42 does not authorize closing PR #99."""
        from shared.merge_guard_common import extract_command_context
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        context = extract_command_context("gh pr close 42 --delete-branch")
        write_token(context, token_dir=tmp_path)

        result = check_merge_authorization("gh pr close 99 --delete-branch", token_dir=tmp_path)
        assert result is not None
        assert "does not match" in result.lower()

# =============================================================================
# detect_command_operation_type unit tests
# =============================================================================


# =============================================================================
# Line continuation normalization tests
# =============================================================================


# =============================================================================
# gh --repo / -R / --hostname flag bypass tests (#267)
# =============================================================================


class TestGhGlobalFlagBypass:
    """Tests that gh global flags (--repo, -R, --hostname) between 'gh' and
    subcommand don't bypass dangerous pattern detection.

    Issue #267: gh CLI allows global flags before the subcommand, e.g.,
    'gh --repo owner/repo pr merge 42'. All gh-prefixed patterns must account
    for optional flags between 'gh' and the subcommand.
    """

    # --- gh pr merge with global flags ---


    # --- gh pr close --delete-branch with global flags ---


    # --- bare gh pr close with global flags is SAFE ---


    # --- gh api with global flags ---


    # --- detect_command_operation_type with global flags ---


    # --- _token_matches_command with global flags ---

    def test_token_pr_mismatch_with_repo_flag(self):
        """Typed merge token PR-number mismatch detected past a --repo flag.

        The token carries the SAME bound flags the command mints (--repo=owner/repo)
        so the #1042 set-equality flag axis MATCHES — leaving the PR-number axis
        (42 vs 99) as the sole load-bearing REFUSE reason. Without the matching
        bound_flags the refusal would ride the flag axis (token []) and this test
        would stay green even if PR-mismatch detection regressed (vacuous).
        """
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--repo=owner/repo"],
            }
        }
        assert not _token_matches_command(token, "gh --repo owner/repo pr merge 99")

    # --- Full authorization flow with global flags ---

    def test_merge_with_repo_flag_blocked_without_token(self, tmp_path):
        """'gh --repo owner/repo pr merge 42' blocked without token."""
        from merge_guard_pre import check_merge_authorization

        result = check_merge_authorization(
            "gh --repo owner/repo pr merge 42", token_dir=tmp_path
        )
        assert result is not None
        assert "approval" in result.lower()

    # --- Line continuation combined with global flags ---


# =============================================================================
# gh global flag bypass — comprehensive edge cases, adversarial, integration
# =============================================================================


class TestGhGlobalFlagBypassEdgeCases:
    """Comprehensive edge case, adversarial, and integration tests for the
    gh global flag bypass fix (issue #267).

    Supplements TestGhGlobalFlagBypass (coder smoke tests) with:
    - Unusual flag values (dots, slashes, special chars, long values)
    - Greedy matching concerns (over-match into unrelated commands)
    - Stripping helper interactions (echo, var assign, heredoc, comment)
    - Adversarial bypass vectors (creative flag placement, obfuscation)
    - Token matching with global flags (PR extraction, operation type)
    """

    # --- Unusual flag values ---


    # --- Many flags chained ---


    # --- Greedy matching: false positive defense ---
    # (?:\S+\s+)* can over-match. These verify the KNOWN behavior:
    # commands with 'pr merge' or 'pr close' appearing as arguments to
    # other gh subcommands will match. This is acceptable (false positive >
    # missed threat per the codebase philosophy), but we document it.


    # --- Stripping helper interactions ---


    # --- Stripping helpers that PRESERVE dangerous content ---


    # --- Chained commands with flags ---


    # --- Line continuation combined with global flags (extended) ---


    # --- Adversarial: creative bypass attempts ---


    # --- Token matching: PR number extraction with global flags ---

    def test_token_pr_extraction_with_equals_syntax(self):
        """PR number extracted correctly with --repo=value syntax (typed token)."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "host": "github.com", "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--repo=owner/repo"],  # #1042: --repo=value form
            }
        }
        assert _token_matches_command(
            token, "gh --repo=owner/repo pr merge 42"
        )

    # --- Full authorization flow with flags and valid tokens ---

    def test_flagged_close_blocked_by_merge_token(self, tmp_path):
        """Flagged close command blocked when only merge token exists."""
        from merge_guard_pre import check_merge_authorization, TOKEN_PREFIX
        import time, json, os

        token_data = {
            "expires_at": time.time() + 300,
            "context": {"operation_type": "merge"},
        }
        token_path = tmp_path / f"{TOKEN_PREFIX}test"
        token_path.write_text(json.dumps(token_data))
        os.chmod(token_path, 0o600)

        result = check_merge_authorization(
            "gh --repo owner/repo pr close 42 --delete-branch",
            token_dir=tmp_path,
        )
        assert result is not None  # Blocked — wrong operation type

# =============================================================================
# Review remediation: reversed --delete-branch pattern with flag variants (#269)
# =============================================================================


# =============================================================================
# Review remediation: --hostname=host.com equals syntax (#269)
# =============================================================================


# =============================================================================
# Pre-existing bypass: subcommand flags before PR number (#269 item 6)
# =============================================================================


class TestSubcommandFlagsBeforePrNumber:
    """Tests that subcommand flags between merge/close and the PR number
    don't break PR number extraction in _token_matches_command.

    e.g., 'gh pr merge --admin 42' should still extract PR number 42.
    """

    def test_merge_multiple_flags_before_pr_number(self):
        """'gh pr merge --squash --delete-branch 42' — PR number extracted."""
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "repo": "o/r", "host": "github.com", "operation_type": "merge",
                "pr_number": 42,
                # #1042: --delete-branch on MERGE is a bound side-effect; --squash
                # is a merge-method flag (unbound).
                "bound_flags": ["--delete-branch"],
            }
        }
        assert _token_matches_command(
            token, "gh pr merge --squash --delete-branch 42"
        )

    def test_merge_admin_flag_pr_number_mismatch(self):
        """'gh pr merge --admin 99' — mismatch with token for PR 42.

        The token carries the bound --admin so the #1042 flag axis matches and the
        PR-number axis (42 vs 99) is the sole load-bearing mismatch — non-vacuous
        on PR detection (without the matching flag the refusal would ride the flag
        axis and mask a PR-detection regression).
        """
        from merge_guard_pre import _token_matches_command

        token = {
            "context": {
                "repo": "o/r", "host": "github.com", "operation_type": "merge",
                "pr_number": 42,
                "bound_flags": ["--admin"],
            }
        }
        assert not _token_matches_command(token, "gh pr merge --admin 99")

# =============================================================================
# Pre-existing bypass: git -C /path flag bypass (#269 item 7)
# =============================================================================


class TestGitGlobalFlagBypass:
    """Tests that git global flags (e.g., -C /path, -c key=val) between 'git'
    and the subcommand don't bypass dangerous pattern detection.

    git allows global options before the subcommand:
    - git -C /path push --force origin main
    - git -c user.name=x push --force origin main
    - git --git-dir=/path/.git branch -D feature
    """

    # --- Force push with git global flags ---


    # --- Branch delete with git global flags ---


    # --- Push to main/master with git global flags ---


    # --- Safe commands with git global flags ---


    # --- Token matching: branch delete with git global flags ---

    def test_token_branch_match_with_C_flag(self):
        """Typed branch-delete token matching works past a git -C flag."""
        from merge_guard_pre import _token_matches_command

        cmd = "git -C /tmp/repo branch -D feature"
        token = {"context": git_bound({"operation_type": "branch-delete", "branch": "feature"}, cmd)}
        assert _token_matches_command(token, cmd)

    def test_token_branch_mismatch_with_C_flag(self):
        """Typed branch-delete token mismatch detected past a git -C flag."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "branch-delete", "branch": "feature"}}
        assert not _token_matches_command(
            token, "git -C /tmp/repo branch -D other-branch"
        )

# =============================================================================
# Module-load fail-closed wrapper (Issue #658 / PR #660 Future #5)
# =============================================================================


class TestModuleLoadFailClosed:
    """Tests for the module-load fail-closed wrapper in merge_guard_pre.

    Verifies that if module-level imports or pattern compilations fail at
    import time, the harness sees a structured deny output (with the
    required `hookEventName`) on stdout BEFORE the process exits — instead
    of an empty stdout that would fail open.
    """

    def _reload_with_broken_import(self, broken_module_name, monkeypatch, capsys):
        """Helper: reload merge_guard_pre with a forced ImportError on
        ``broken_module_name``. Returns (exit_code, stdout, stderr).

        Pops only ``merge_guard_pre`` from sys.modules (so its body re-runs)
        and restores it on teardown. Does NOT pop ``shared.*`` modules: the
        patched ``builtins.__import__`` raises on the broken name regardless
        of sys.modules cache state, and popping ``shared.*`` would orphan
        function references already imported by other test modules
        (e.g. ``shared.task_utils`` keeps a stale ``get_session_id`` ref to
        a popped-and-replaced ``shared.pact_context`` — observed as
        cross-file test pollution).
        """
        import importlib

        _missing = object()
        original_mgp = sys.modules.get("merge_guard_pre", _missing)
        sys.modules.pop("merge_guard_pre", None)

        real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

        def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == broken_module_name or name.startswith(broken_module_name + "."):
                raise ImportError(f"simulated load failure for {broken_module_name}")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.setattr("builtins.__import__", fake_import)

        try:
            with pytest.raises(SystemExit) as exc_info:
                importlib.import_module("merge_guard_pre")
            captured = capsys.readouterr()
            return exc_info.value.code, captured.out, captured.err
        finally:
            sys.modules.pop("merge_guard_pre", None)
            if original_mgp is not _missing:
                sys.modules["merge_guard_pre"] = original_mgp

    def test_module_load_failure_emits_deny_with_hookEventName(self, monkeypatch, capsys):
        """If shared.pact_context fails to import, the wrapper emits a
        deny output with `hookEventName: PreToolUse` and exits 2.

        Issue #658 audit anchor: hookEventName must be present in any deny
        output, including the module-load fail-closed path. Without this,
        the harness silently fails open on a broken module.
        """
        exit_code, stdout, stderr = self._reload_with_broken_import(
            "shared.pact_context", monkeypatch, capsys,
        )

        assert exit_code == 2
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "PACT merge_guard_pre module imports failure" in output["hookSpecificOutput"]["permissionDecisionReason"]
        # Stderr must explain the cause for operator debugging.
        assert "merge_guard_pre" in stderr
        assert "simulated load failure" in stderr

    def test_module_load_failure_in_merge_guard_common_emits_deny(self, monkeypatch, capsys):
        """If shared.merge_guard_common fails to import, the wrapper still
        emits a fail-closed deny with hookEventName.
        """
        exit_code, stdout, stderr = self._reload_with_broken_import(
            "shared.merge_guard_common", monkeypatch, capsys,
        )

        assert exit_code == 2
        output = json.loads(stdout)
        assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

class TestCompoundDestructiveCommandRejection:
    """Pin the compound-command rejection that closes the chained-bypass class.

    A single AskUserQuestion approval can authorize ONE destructive op.
    Chained shapes (``&&``, ``||``, ``;``, ``|``, newline) hide secondary
    destructive ops past the operator's headline-command review of the
    AskUserQuestion text. Categorically reject compound destructive
    shapes; force the operator to run one destructive op per checkpoint.
    """

    def test_compound_with_destructive_denied(self, tmp_path):
        """gh pr merge 100 && gh pr merge 999 → denied even with token for 100."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Token for the headline op exists
        write_token(
            {"repo": "o/r", "host": "github.com", "pr_number": "100", "operation_type": "merge"},
            token_dir=tmp_path,
        )
        cmd = "gh pr merge 100 && gh pr merge 999 --admin"
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None, "compound destructive must be denied"
        assert "Compound destructive" in result
        assert "&&" in result or "||" in result or ";" in result

    def test_pipe_with_destructive_denied(self, tmp_path):
        """Honest-mistake ≥2-narrowing: `gh pr merge 100 | tee` is ONE destructive
        leg + a benign pipe → NO LONGER compound. But the single merge op is STILL
        is_dangerous-gated, so with no token it is DENIED — via the normal single-op
        approval gate, NOT the compound gate (no under-gate; the op is still caught)."""
        from merge_guard_pre import check_merge_authorization, is_compound_destructive_command, is_dangerous_command

        cmd = "gh pr merge 100 | tee /tmp/out"
        assert not is_compound_destructive_command(cmd)   # single destructive leg
        assert is_dangerous_command(cmd)                  # the merge op is still gated
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None                         # denied (single op, no token)
        assert "Compound destructive" not in result       # NOT via the compound gate

    def test_newline_with_destructive_denied(self, tmp_path):
        """`ls\\ngh pr merge 99` — ONE destructive leg (merge) + a benign leg → not
        compound, but the merge is is_dangerous-gated → still DENIED via the single-op
        gate (not compound)."""
        from merge_guard_pre import check_merge_authorization, is_compound_destructive_command, is_dangerous_command

        cmd = "ls\ngh pr merge 99"
        assert not is_compound_destructive_command(cmd)
        assert is_dangerous_command(cmd)
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None
        assert "Compound destructive" not in result

    # New negatives — these previously triggered the compound predicate
    # via the loose `[&;|\n]` character class. The tightened pattern
    # excludes file-descriptor redirects and clobber redirects.
class TestBenignContinuationGuarantee:
    """Pin the 'single destructive op + benign continuation' idiom.

    A faithful agent appends a benign viewer/filter/redirect/background to a
    single approved destructive op to confirm the result without a separate API
    query (e.g. ``gh pr merge 5 --squash | tail``). Under the honest-mistake
    model that is ONE destructive leg — NOT a >=2-destructive compound — so it
    must mint a token and must not be refused as compound. This class pins the
    whole continuation family so a future hardening change cannot silently
    re-break the idiom.

    The guarantee is UNIVERSAL across the recognized destructive ops — gh pr
    merge, gh pr close, git force-push, AND git branch-delete: a single such op
    plus any benign continuation mints a token AND the read side AUTHORIZES the
    continued command. Each op's target parser re-derives its target from the
    single destructive leg regardless of trailing continuation / redirect tokens
    (the positional extractors truncate at the first benign terminator on the
    quote-masked view), so the minted single-op token still matches.

    Non-vacuity is anchored:
      * the ``| bash`` pipe-to-shell boundary — a benign continuation is bounded:
        ``_has_pipe_to_shell`` recognizes ``| bash`` / ``| sh`` as dangerous
        indirection while the benign viewers (``| tail`` / ``| less``) are NOT,
        so 'benign continuation' is not a blanket pipe pass;
      * the >=2-destructive compound contrast — a genuine multi-destructive chain
        is still classified compound and refused even with a token;
      * the wrong-target / privileged-flag-absent negatives — a continuation can
        never authorize a MISMATCHED target or a token missing a bound flag.
    """

    # The benign continuation family: pipe-to-viewer, output redirect,
    # and background / benign chain. Each is appended to a single destructive op;
    # none is a second destructive leg.
    _BENIGN_CONTINUATIONS = [
    "| tail",
    "&",
]

    # All recognized destructive ops: (label, base destructive command, mint
    # context). The read side AUTHORIZES the continued command for EVERY one —
    # the force-push / branch-delete target parsers are now continuation-tolerant
    # (they truncate at the first benign terminator before counting positionals),
    # so they join merge / close in this guarantee. ``close`` carries
    # ``--delete-branch`` (its danger trigger), so its token context MUST bind
    # that flag — the read-side set-equality flag gate refuses otherwise — exactly
    # as the mint side extracts it from the approval text. ``merge`` /
    # ``force-push`` / ``branch-delete`` carry no privileged flag.
    _AUTHORIZED_OPS = [
        ("merge", "gh pr merge 5", {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5"}),
        (
            "close",
            "gh pr close 7 --delete-branch",
            {"repo": "o/r", "host": "github.com", "operation_type": "close", "pr_number": "7",
             "bound_flags": ["--delete-branch"]},
        ),
        (
            "force-push",
            "git push --force origin main",
            {"operation_type": "force-push", "target_ref": "main"},
        ),
        (
            "branch-delete",
            "git branch -D victim",
            {"operation_type": "branch-delete", "branch": "victim"},
        ),
    ]

    @pytest.mark.parametrize("cont", _BENIGN_CONTINUATIONS)
    @pytest.mark.parametrize("op_label,base,ctx", _AUTHORIZED_OPS)
    def test_single_destructive_op_continuation_authorizes(
        self, tmp_path, op_label, base, ctx, cont
    ):
        """A single destructive op + a benign continuation mints AND authorizes.

        One destructive leg (dangerous, NOT compound); the single-op approval
        token authorizes the continued command. Holds for EVERY recognized op
        (merge / close / force-push / branch-delete) — the guaranteed idiom.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import (
            check_merge_authorization,
            is_compound_destructive_command,
            is_dangerous_command,
        )

        cmd = f"{base} {cont}"
        assert is_dangerous_command(cmd) is True
        assert is_compound_destructive_command(cmd) is False
        assert write_token(git_bound(dict(ctx), cmd), token_dir=tmp_path) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None


    @pytest.mark.parametrize(
        "cmd",
        [
    "gh pr merge 5 && gh pr close 6 --delete-branch",
],
    )
    def test_two_destructive_compound_still_refused(self, tmp_path, cmd):
        """Non-vacuity contrast: a genuine >=2-destructive compound is still
        is_compound_destructive_command=True and REFUSED via the compound gate even
        with a valid token for the headline op. Proves the >=2-narrowing that lets
        arm (a) through has NOT become a blanket pass.

        (Note: ``gh pr close <N>`` WITHOUT ``--delete-branch`` is not destructive,
        so the second leg must carry its danger trigger to be a genuine compound.)
        """
        from merge_guard_post import write_token
        from merge_guard_pre import (
            check_merge_authorization,
            is_compound_destructive_command,
        )

        assert is_compound_destructive_command(cmd) is True
        write_token(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5"}, token_dir=tmp_path
        )
        result = check_merge_authorization(cmd, token_dir=tmp_path)
        assert result is not None
        assert "Compound destructive" in result

    # A few representative continuation forms (one per family: pipe-to-viewer,
    # benign-chain, output-redirect) for the negative refuse-direction asserts
    # below — the point is to pin the refuse UNDER a continuation, not to re-sweep
    # the whole family.
    _REFUSE_CONTINUATIONS = ["| tail", "&& echo done", "> out.log"]

    @pytest.mark.parametrize("cont", _REFUSE_CONTINUATIONS)
    def test_wrong_pr_under_continuation_refuses(self, tmp_path, cont):
        """UNDER-BLOCK GUARD: arm-(a)'s authorize is target-BOUND, not a blanket
        continuation pass. A token approved for ONE PR must NOT authorize a
        DIFFERENT PR's merge even with a benign continuation appended — the read
        side re-derives the PR-number target from the command and the mismatch
        refuses.

        Non-vacuity (verified by in-memory mutation, reported in the HANDOFF, not
        encoded here): if the read side dropped the PR-number equality check, this
        wrong-PR command would AUTHORIZE and this assert would flip RED.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Approval is for PR 5; the executed command targets PR 99.
        write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "5"}, token_dir=tmp_path)
        cmd = f"gh pr merge 99 {cont}"
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None

    @pytest.mark.parametrize("cont", _REFUSE_CONTINUATIONS)
    def test_privileged_flag_absent_from_token_under_continuation_refuses(
        self, tmp_path, cont
    ):
        """UNDER-BLOCK GUARD: the privileged-flag bind survives a benign
        continuation. A `gh pr close <N> --delete-branch` command must NOT be
        authorized by a token whose context lacks that bound flag, even with a
        benign continuation appended. op-type and PR number match here, so the
        ONLY axis that differs is the bound flag — a continuation must not erode
        the never-escalate flag bind.

        Non-vacuity (verified by in-memory mutation, reported in the HANDOFF, not
        encoded here): if the read side dropped the bound-flag set-equality check,
        this command would AUTHORIZE on the flag-less token and this assert would
        flip RED.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        # Token for the close op WITHOUT the --delete-branch bound flag.
        write_token({"repo": "o/r", "host": "github.com", "operation_type": "close", "pr_number": "7"}, token_dir=tmp_path)
        cmd = f"gh pr close 7 --delete-branch {cont}"
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None


class TestEvalHeredocRejection:
    """Pin the eval+heredoc detection that closes the strip-pipeline-bypass class.

    The strip pipeline removes heredoc bodies BEFORE the regex-match phase.
    An eval-wrapped destructive command inside a heredoc body becomes
    invisible to DANGEROUS_PATTERNS by the time matching runs. Treat
    eval+heredoc shapes as categorically dangerous via a pre-strip check.
    """

    def test_eval_dollar_paren_heredoc_denied(self, tmp_path):
        """eval $(cat <<HEREDOC ... HEREDOC) form denied as dangerous."""
        from merge_guard_pre import is_dangerous_command

        cmd = "eval $(cat <<HEREDOC\ngh pr merge 999 --admin\nHEREDOC\n)"
        assert is_dangerous_command(cmd) is True

    def test_eval_backtick_heredoc_denied(self, tmp_path):
        """eval `cat <<HEREDOC ... HEREDOC` (legacy backtick form) denied."""
        from merge_guard_pre import is_dangerous_command

        cmd = "eval `cat <<HEREDOC\ngh pr merge 999\nHEREDOC\n`"
        assert is_dangerous_command(cmd) is True


class TestCrossOperationAuthorizationDenied:
    """Pin the F-1 cross-operation authorization closure.

    Cycle-2 added force-push and branch-delete to extract_context() (the
    write side). Cycle-3 mirrors the symmetric coverage in
    detect_command_operation_type (the read side) and tightens the
    cross-op comparison so a typed token CANNOT authorize a command of a
    different operation class. Pre-fix, the read-side detector returned
    None for force-push/branch-delete, and the comparison fell through
    permissively (cmd_op_type=None → no comparison), allowing any typed
    token to authorize any destructive command in those classes.
    """

    def test_merge_token_does_not_authorize_force_push(self, tmp_path):
        """The empirical bypass: merge-token + force-push command → deny.

        Pre-fix: cmd_op_type was None for `git push --force`, so the
        token's `operation_type=merge` comparison was skipped, and the
        merge token authorized the force-push. Post-fix: cmd_op_type is
        `force-push`, the comparison fires, and the typed-mismatch denies.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "100"}, token_dir=tmp_path)
        result = check_merge_authorization(
            "git push --force origin main", token_dir=tmp_path
        )
        assert result is not None
        assert "does not match" in result.lower() or "approval" in result.lower()

    def test_force_push_token_does_not_authorize_merge(self, tmp_path):
        """Symmetric inverse: force-push token + gh pr merge → deny."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "force-push"}, token_dir=tmp_path)
        result = check_merge_authorization(
            "gh pr merge 42 --squash", token_dir=tmp_path
        )
        assert result is not None
        assert "does not match" in result.lower() or "approval" in result.lower()

    def test_branch_delete_token_does_not_authorize_close(self, tmp_path):
        """branch-delete token + gh pr close → deny.

        gh pr close (without --delete-branch) is the close op-class;
        a branch-delete token cannot authorize it.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "branch-delete"}, token_dir=tmp_path)
        # bare close is not a DANGEROUS_PATTERNS hit; use --delete-branch
        # which IS dangerous and is classified as "close" per the
        # extract_context precedence. The token op_type=branch-delete
        # mismatches the cmd op_type=close.
        result = check_merge_authorization(
            "gh pr close 42 --delete-branch", token_dir=tmp_path
        )
        assert result is not None
        assert "does not match" in result.lower() or "approval" in result.lower()

    def test_unknown_op_command_with_typed_token_denied(self, tmp_path):
        """Typed token + dangerous-but-unrecognized command shape → deny.

        The strict-match semantic: if the token has an operation_type but
        the command's shape doesn't match any of the known op classes, the
        comparison fails closed rather than falling through permissively.
        """
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        # gh api PATCH on /merge endpoint is dangerous (DANGEROUS_PATTERNS L142)
        # but my detector classifies it as None (only git/refs API forms map
        # to force-push/branch-delete). Typed token vs untyped cmd → deny.
        result = check_merge_authorization(
            "gh api -X PATCH repos/o/r/merges -f base=main -f head=feature",
            token_dir=tmp_path,
        )
        assert result is not None

    def test_matching_op_type_still_authorizes(self, tmp_path):
        """Regression-protection: matching op_type still allows execution."""
        from merge_guard_post import write_token
        from merge_guard_pre import check_merge_authorization

        write_token(git_bound({"operation_type": "force-push", "target_ref": "feature"},
                              "git push --force origin feature"), token_dir=tmp_path)
        result = check_merge_authorization(
            "git push --force origin feature", token_dir=tmp_path
        )
        assert result is None, "matching force-push token must authorize git push --force"


# (removed class TestSymmetryContract — superseded by the command-anchored
#  bidirectional suite in test_merge_guard_auth_symmetry.py;
#  it exercised the dropped prose classifier extract_context.)

class TestConcurrentConsumptionThreaded:
    """Real-thread race tests discharging the architect's per-slot O_EXCL
    atomicity proof (#720 Bug C).

    The verification-test suite simulates concurrency via back-to-back
    synchronous calls. These tests use threading.Barrier to release N
    real threads simultaneously at the os.open(O_CREAT|O_EXCL) syscall.
    Python's GIL serializes bytecode, but the syscall releases the GIL,
    so the kernel-level race window is genuine.

    The architect §4.3 invariants under verification:
    - Each concurrent invocation claims a distinct slot.
    - No two invocations claim the same slot.
    - Total successful claims ≤ MAX_USES.
    - Terminal rename happens at most once.
    """

    def _drain_token(self, token_dir, pr_number):
        """Helper: write a fresh token, return its path."""
        from merge_guard_post import write_token

        return write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": pr_number}, token_dir=token_dir)

    def test_two_threads_race_on_n2_token_distinct_slots(self, tmp_path):
        """2 threads racing on a N=2 token: each claims a distinct slot.

        Architect §4.3 proof discharge — explicit threading race rather
        than synchronous back-to-back calls.
        """
        import threading

        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_pre import _consume_token

        token_path = self._drain_token(tmp_path, "42")

        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()  # release both threads at the same instant
            results.append(_consume_token(token_path))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Both threads succeeded
        assert results.count(True) == 2
        assert results.count(False) == 0

        # Each slot was claimed exactly once (distinct slots)
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert Path(token_path + USE_MARKER_SUFFIX + "2").exists()

        # Terminal rename happened
        assert not Path(token_path).exists()
        assert Path(token_path + ".consumed").exists()

    def test_three_threads_race_on_n2_token_one_denied(self, tmp_path):
        """3 threads racing on N=2 token: 2 win distinct slots, 1 denied.

        Pins the over-subscription failure mode: budget is hard-bounded;
        excess concurrent claimants get False, not phantom slot-3.
        """
        import threading

        from shared.merge_guard_common import USE_MARKER_SUFFIX
        from merge_guard_pre import _consume_token

        token_path = self._drain_token(tmp_path, "42")

        results = []
        results_lock = threading.Lock()
        barrier = threading.Barrier(3)

        def worker():
            barrier.wait()
            outcome = _consume_token(token_path)
            with results_lock:
                results.append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 2 wins, 1 denial
        assert results.count(True) == 2
        assert results.count(False) == 1

        # Only the 2 valid slot markers exist; no phantom slot-3
        assert Path(token_path + USE_MARKER_SUFFIX + "1").exists()
        assert Path(token_path + USE_MARKER_SUFFIX + "2").exists()
        assert not Path(token_path + USE_MARKER_SUFFIX + "3").exists()
        assert Path(token_path + ".consumed").exists()

class TestLegacyTokenBoundary:
    """Adversarial coverage for the legacy-token (max_uses missing) path
    (#720 Bug C, backend-coder uncertainty item #1).

    The verification-tests pin individual legacy behaviors. These tests
    cover the boundary cases: legacy tokens approaching TTL, legacy +
    N=2 tokens coexisting in the same session, and the end-to-end
    behavior of legacy tokens against check_merge_authorization (not
    just the _consume_token unit surface).
    """

    def _write_legacy_token(self, tmp_path, pr_number="42", ttl_offset=300):
        """Write a legacy token with no max_uses field; offset controls TTL."""
        now = time.time()
        token_path = tmp_path / f"merge-authorized-legacy-{pr_number}"
        token_path.write_text(json.dumps({
            "created_at": now,
            "expires_at": now + ttl_offset,
            "context": {"repo": "o/r", "host": "github.com", "pr_number": pr_number, "operation_type": "merge"},
        }))
        return str(token_path)

    def test_legacy_token_blocks_second_command_end_to_end(self, tmp_path):
        """Legacy token authorizes 1 command via check_merge_authorization;
        second identical command is blocked (no N-use budget for legacy)."""
        from merge_guard_pre import check_merge_authorization

        self._write_legacy_token(tmp_path, "42")

        # First: allowed
        assert check_merge_authorization("gh pr merge 42", token_dir=tmp_path) is None

        # Second: blocked — no remaining token (legacy single-use exhausted)
        result = check_merge_authorization("gh pr merge 42", token_dir=tmp_path)
        assert result is not None
        assert "AskUserQuestion" in result

class TestAuditEmitFormatAdversarial:
    """Bug C audit-emit format invariance under adversarial conditions
    (#720 Bug C, backend-coder open question #3 implicit).

    Verification-tests pin the format under the default MAX_USES. These
    tests cover format invariance under unicode basenames, when MAX_USES
    is monkeypatched to other integers, and the absence of audit emit
    on the unhappy paths.
    """

    def test_audit_basename_with_special_chars_safe(self, tmp_path, capfd):
        """Token basename containing special chars (hyphen, digits) is
        emitted verbatim. Pins that the emitter doesn't quote/escape."""
        from merge_guard_post import write_token
        from merge_guard_pre import _consume_token

        token_path = write_token(
            {"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "123"},
            token_dir=tmp_path,
        )
        capfd.readouterr()
        _consume_token(token_path)
        out = capfd.readouterr()

        basename = os.path.basename(token_path)
        # Basename appears in the audit line as-is
        assert basename in out.err
        # And the line is a single [security] entry on consume
        consume_lines = [l for l in out.err.splitlines()
                         if "[security] merge-authorized token consumed" in l]
        assert len(consume_lines) == 1


class TestEnvelopeIntegration:
    """Envelope-level (PreToolUse stdin → main() → JSON stdout)
    integration tests for the 3 bug surfaces (#720).

    Verification-tests target the check_merge_authorization function
    directly. These supplement with full main()-shape invocations to
    catch any drift between the function-level contract and the JSON
    serialization layer at the hook's external boundary.

    Scope: 3-5 envelope tests, one per bug surface plus the end-to-end
    flow. Not exhaustive — that's the function-level layer's job.
    """

    def _pre_envelope(self, command: str) -> str:
        """Build a PreToolUse stdin envelope (Bash matcher)."""
        return json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "session_id": "test-envelope-session",
        })

    def _post_envelope(self, question: str, answer: str = "yes",
                       options: list | None = None) -> str:
        """Build a PostToolUse stdin envelope (AskUserQuestion matcher). Post-#32
        the mint is OPTION-ANCHORED, so callers pass an `options` list with the
        command in the clicked option's description + an `answer` matching that
        option's label."""
        q: dict = {"question": question}
        if options is not None:
            q["options"] = options
        return json.dumps({
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [q]},
            "tool_response": {"answers": {question: answer}},
            "session_id": "test-envelope-session",
        })

    def _invoke_pre(self, command: str, tmp_path):
        """Invoke merge_guard_pre.main() with a built envelope.
        Returns (exit_code, stdout_text, stderr_text)."""
        from merge_guard_pre import main as pre_main

        envelope = self._pre_envelope(command)
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        return exc_info.value.code, stdout_buf.getvalue()

    def _invoke_post(self, question: str, tmp_path, answer: str = "yes",
                     options: list | None = None):
        """Invoke merge_guard_post.main() with a built envelope."""
        from merge_guard_post import main as post_main

        envelope = self._post_envelope(question, answer, options)
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                post_main()
        return exc_info.value.code

    def test_bug_a_envelope_fd_redirect_authorizes(self, tmp_path):
        """Full envelope: Bug A surface, post over-block-tokenizer fix. The approval
        mints a push-to-main token, and `git push origin main 2>&1` now AUTHORIZES:
        `_extract_force_push_target_ref` truncates at the `2>&1` redirect before the
        positional count, so the destination re-derives to `main` and matches the
        token. The redirect filename is structurally outside the positional window,
        so this is a faithful-click authorize, NOT a #1032 under-block.
        """
        # Step 1: approve via post hook — option-anchored (#32): the command
        # lives in the CLICKED option's description.
        post_code = self._invoke_post(
            "Authorize the force-push?", tmp_path, answer="Yes, force-push",
            options=[
                {"label": "Yes, force-push", "description": "Run `git push origin main`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        )
        assert post_code == 0
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1

        # Step 2: run the FD-redirect command through pre hook → now AUTHORIZES
        # (the redirect is truncated before the 2-positional ref parse, so the
        # destination re-derives to `main` and matches the minted token).
        pre_code, _pre_stdout = self._invoke_pre(
            "git push origin main 2>&1", tmp_path
        )
        assert pre_code == 0  # Authorized (faithful click; redirect re-derived)

    def test_bug_b_envelope_joes_bare_push_authorizes(self, tmp_path):
        """Full envelope: Bug B surface. AskUserQuestion question with quoted-command
        `git push origin main` (Joe's case) → token writes push-to-main op_type (GAP3:
        a PLAIN push to main is a DISTINCT op from --force force-push); pre hook
        recognizes bare `git push origin main` as push-to-main and authorizes."""
        post_code = self._invoke_post(
            "Confirm the push to main?", tmp_path, answer="Yes, push",
            options=[
                {"label": "Yes, push", "description": "Run `git push origin main`"},
                {"label": "Cancel", "description": "Abort"},
            ],
        )
        assert post_code == 0

        # Verify token has push-to-main op_type (symmetric classifier worked)
        token_files = list(tmp_path.glob("merge-authorized-*"))
        assert len(token_files) == 1
        token_data = json.loads(token_files[0].read_text())
        assert token_data["context"]["operation_type"] == "push-to-main"

        # Run the matching command — should be authorized
        pre_code, pre_stdout = self._invoke_pre(
            "git push origin main", tmp_path
        )
        assert pre_code == 0
        assert '"permissionDecision": "deny"' not in pre_stdout

    def test_envelope_with_malformed_tool_input_fails_closed(self, tmp_path):
        """tool_input is a non-dict (e.g., a string). Defensive contract:
        hook fails closed via the catch-all exception handler (exit 2),
        does NOT crash, does NOT auto-allow."""
        from merge_guard_pre import main as pre_main

        # tool_input is a string instead of a dict — .get() on a string
        # raises AttributeError → catch-all → fail-closed deny.
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": "git push origin main",  # type-error shape
            "session_id": "test-envelope-session",
        })
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        assert exc_info.value.code == 2  # fail-closed
        # Output is the well-formed deny payload from the catch-all.
        payload = json.loads(stdout_buf.getvalue())
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    # -----------------------------------------------------------------------
    # Envelope-shape adversarial coverage. The non-envelope tests at lines
    # 462 / 820 / 831 cover the same logical paths via direct main()
    # invocation, but a future restructure of stdin parsing could regress
    # envelope-shape behavior while those tests stay green. These tests
    # pin behavior through the envelope-fixture interface specifically.

class TestLeaseToDefaultGateAndMint:
    """Lease-push-to-default fold + presence-bind (#1064).

    `git push --force-with-lease origin main` was gated by the read floor
    (push-to-main arm accepts any dash token) but excluded from the MINT
    classifier's push-to-main arm by a negative lookahead -> detect=None ->
    unmintable -> a faithful single-command click was PERMANENTLY blocked
    (gated-but-unmintable over-block). The fix removes the mint-arm lookahead
    (flag-walk now byte-identical to the read arm's) and separates plain-push
    vs lease-push token identities via the --force-with-lease PRESENCE bind
    in PRIVILEGED_FLAGS (the close/--delete-branch precedent).

    These tests certify gate+mint symmetry END-TO-END through the real hook
    mains (post mint -> pre authorize/refuse). The #1042 set-equality refusal
    matrix (hand-built token contexts against _token_matches_command) lives in
    test_merge_guard_privileged_flags.py — that suite owns the bind invariant;
    the read-leg refusals here exercise the DIFFERENT envelope layer (minted
    token -> pre main JSON deny), not the same assertion.
    """

    LEASE = "git push --force-with-lease origin main"
    LEASE_VALUE = "git push --force-with-lease=main:abc123 origin main"
    PLAIN = "git push origin main"

    # Envelope drivers are inline (the idiom used by every class outside
    # TestEnvelopeIntegration); the mint is option-anchored — the command
    # lives in the CLICKED option's description.

    def _mint(self, cmd: str, tmp_path) -> dict:
        """Drive the REAL post hook main() with an approval whose clicked option
        embeds `cmd`; assert exactly one token minted; return its context."""
        from merge_guard_post import main as post_main

        envelope = json.dumps({
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{
                "question": "Push to the default branch?",
                "options": [
                    {"label": "Yes, push", "description": f"Run `{cmd}`"},
                    {"label": "Cancel", "description": "Abort"},
                ],
            }]},
            "tool_response": {"answers": {"Push to the default branch?": "Yes, push"}},
            "session_id": "test-lease-session",
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                post_main()
        assert exc_info.value.code == 0
        tokens = list(tmp_path.glob("merge-authorized-*"))
        assert len(tokens) == 1, "approval did not mint exactly one token"
        return json.loads(tokens[0].read_text())["context"]

    def _pre(self, cmd: str, tmp_path):
        """Run `cmd` through the REAL pre hook main(); return (exit_code, stdout)."""
        from merge_guard_pre import main as pre_main

        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": cmd},
            "session_id": "test-lease-session",
        })
        stdout_buf = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)), \
             patch("sys.stdout", stdout_buf):
            with pytest.raises(SystemExit) as exc_info:
                pre_main()
        return exc_info.value.code, stdout_buf.getvalue()


    def test_lease_approval_mints_and_authorizes_byte_identical(self, tmp_path):
        """The #1064 DENY->ALLOW flip: a faithful lease click mints a push-to-main
        token carrying the presence bind, and the byte-identical execution
        AUTHORIZES (this exact flow was permanently blocked pre-fix)."""
        ctx = self._mint(self.LEASE, tmp_path)
        assert ctx["operation_type"] == "push-to-main"
        assert ctx["target_ref"] == "main"
        assert ctx["bound_flags"] == ["--force-with-lease"]
        code, _out = self._pre(self.LEASE, tmp_path)
        assert code == 0

    def test_lease_equals_value_approval_mints_and_authorizes_byte_identical(
            self, tmp_path):
        """The =<ref>:<expect> spelling rides the fix: same op-class, same canonical
        bare bound flag (the boolean bind drops the inline value), byte-identical
        approve/execute authorizes."""
        ctx = self._mint(self.LEASE_VALUE, tmp_path)
        assert ctx["operation_type"] == "push-to-main"
        assert ctx["target_ref"] == "main"
        assert ctx["bound_flags"] == ["--force-with-lease"]
        code, _out = self._pre(self.LEASE_VALUE, tmp_path)
        assert code == 0

# =============================================================================
# Layer 4 (#797): TestTokenLifecycleInvariants
#
# Pins the 5 token-lifecycle invariants documented in
# shared/merge_guard_common.py module docstring. Each invariant has one
# alias test (I-1 through I-5) plus supporting structural pins for the
# NEW Layer 1/3/5 code paths.
#
# Counter-test cardinality target: {14 RED} on full Layer 1/3/5 revert.
# Each test carries an inline `# counter-test:` mutation recipe per
# CLAUDE.md PR #697 / §13.8 discipline so the load-bearingness of each
# pin is independently verifiable by a reviewer.
# =============================================================================


class TestTokenLifecycleInvariants:
    """Pin the 5 token-lifecycle invariants + new Layer 1/3/5 code paths.

    Real-fixture discipline: every test uses tmp_path (NOT mocked file
    I/O) so the on-disk lifecycle is exercised end-to-end.
    """

    # ------------------------------------------------------------------
    # Invariant alias tests (5)
    # ------------------------------------------------------------------

    def test_i1_at_most_one_unused_token(self, tmp_path):
        """I-1: at most one unused token at any time (Layer 5).

        Two write_token calls — the second invokes cleanup_unused_tokens
        before O_EXCL, retiring the first. Exactly one unused token
        remains; the other is .consumed.

        # counter-test: comment out _cleanup_unused_tokens(token_dir) in
        #               merge_guard_post.write_token before O_EXCL → two
        #               unused tokens coexist; len(unused) == 2 → RED.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        with patch("merge_guard_post.time") as mock_time:
            mock_time.time.return_value = time.time() + 1
            write_token({"operation_type": "merge"}, token_dir=tmp_path)

        all_files = list(tmp_path.glob("merge-authorized-*"))
        unused = [
            t for t in all_files
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused) == 1, f"I-1 violated: {len(unused)} unused tokens"

    def test_i2_successful_merge_immediately_retires_token(self, tmp_path):
        """I-2: successful gh pr merge retires the consuming token (Layer 1).

        Drive merge_guard_post.main() with a Bash envelope shaped like
        a successful gh pr merge. The unused token is retired to
        .consumed regardless of remaining MAX_USES slots.

        # counter-test: replace `_retire_token_for_command(command)` in
        #               merge_guard_post.main() Bash branch with `pass`
        #               → token stays unused; .consumed never created.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        before_unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(before_unused) == 1

        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        after_unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        after_consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(after_unused) == 0, "I-2 violated: token not retired"
        assert len(after_consumed) == 1, "I-2 violated: expected exactly one .consumed"

    # ------------------------------------------------------------------
    # Layer 5 supporting pins (3)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Layer 3 supporting pins (4)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Layer 1 supporting pins (2)
    # ------------------------------------------------------------------

# =============================================================================
# Layer 4 EXTENSIONS (#797): TestTokenLifecycleExtensions
#
# Phantom-green probes, audit-anchor structural pins, false-positive
# discriminators, envelope-divergence detection, deeper cross-session and
# retry regression coverage, and concurrent-race adversarial scenarios.
#
# Counter-test cardinality target on full Layer 1/3/5 revert combined with
# TestTokenLifecycleInvariants: aggregate scales to {14 base + this class
# additions} on full revert. Each test carries an inline `# counter-test:`
# mutation recipe with measured cardinality from the TEST-phase probe
# campaign (see HANDOFF for full data).
#
# Phantom-green discoveries codified here (from probe data):
#   - I-3 alias does NOT pin orphan-cleanup mechanism (only TTL-expiry).
#     New test pins orphan-mechanism MECHANISM independently.
#   - 2 Layer 1 supporting negative tests pass-for-wrong-reason under
#     full Bash-branch revert. New sentinel adds positive-discriminator
#     contrast so revert produces RED cardinality > 1.
#   - Layer 5 mechanism-no-op probe: 3 of 4 helper supporting tests
#     stay GREEN when helper is gutted (they pin "what NOT to happen").
#     New test exercises the integrated callsite via write_token so
#     mechanism revert is detected.
# =============================================================================


class TestTokenLifecycleExtensions:
    """Extension coverage for #797 token-lifecycle hardening.

    Complementary to TestTokenLifecycleInvariants — pins surfaces that the
    base invariant aliases leave exposed:
      - structural audit anchors (docstring labels, counter-test counts)
      - phantom-green discriminators (orphan-mechanism, retire-mechanism)
      - false-positive sentinels (dry-run, command-shape boundary)
      - envelope-divergence (string, empty-dict, None-stdout shapes)
      - deeper cross-session + retry-preservation regression
      - concurrent races on shared token-dir state

    Real-fixture discipline preserved (tmp_path; mocking limited to
    time/session_id/TOKEN_DIR + controlled os.rename interpose).
    """

    # ------------------------------------------------------------------
    # Audit-anchor structural pins (scope item 6)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Duplicate-class shadow audit (scope item 9 / memory 631891d7)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phantom-green discriminator: orphan-cleanup MECHANISM (scope item 1)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Phantom-green discriminator: Layer 5 mechanism (integrated callsite)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # False-positive sentinel: gh pr merge --dry-run (scope item 7)
    # ------------------------------------------------------------------

    def test_dry_run_merge_does_not_retire_token(self, tmp_path):
        """Layer 1 Block 3 sentinel: `gh pr merge --dry-run` emits
        "would merge" framing, NOT "Merged pull request". The substring
        match must reject this to prevent false-positive retirement on a
        no-op preview command.

        Tests the semantic specificity of the stdout-pattern substring
        beyond what backend-coder's "merge" → "Merged pull request"
        analysis covers. Closes the architect §10 LOW-risk surface.

        # counter-test: change `"Merged pull request" not in stdout_text`
        #               to `"merge" not in stdout_text` (over-broad) →
        #               substring still rejects "would merge" because the
        #               capital-M asymmetry happens to hold, BUT change to
        #               `"would merge" in stdout_text` (inverted) → token
        #               retired falsely. Use single-char `"M"` mutation
        #               for the canonical over-broad probe.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import write_token, main

        write_token({"operation_type": "merge"}, token_dir=tmp_path)
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash --dry-run"},
            "tool_response": {
                "stdout": "would merge pull request #42 (dry-run; no changes)",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        unused = [
            t for t in tmp_path.glob("merge-authorized-*")
            if not str(t).endswith(".consumed") and ".use-" not in t.name
        ]
        assert len(unused) == 1, (
            "Layer 1 Block-3 violated: --dry-run retired token "
            "(false-positive on 'would merge' stdout framing)"
        )

    # ------------------------------------------------------------------
    # Envelope-divergence detection (scope item 8 / §13.6)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Cross-session deeper coverage via Layer 1 path (scope item 4)
    # ------------------------------------------------------------------

    def test_layer1_bash_branch_skips_foreign_session_token(self, tmp_path):
        """I-5 deeper coverage via Layer 1: a foreign-session token must
        NOT be retired by this session's `gh pr merge` PostToolUse, even
        when the command + stdout would otherwise match.

        The base I-5 alias covers the find_valid_token path; this pins
        the same scope-check inside _retire_token_for_command.

        # counter-test: remove the `current_session != token_session`
        #               check in merge_guard_post._retire_token_for_command
        #               → foreign-session token would be retired by this
        #               session's PostToolUse (cross-session breach).
        # expected RED cardinality: {1}
        """
        from merge_guard_post import main

        # Write a token tagged with a foreign session_id directly
        # (bypass write_token to avoid auto-tagging with current session).
        foreign_session = "foreign-session-xyz"
        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": "merge"},
            "session_id": foreign_session,
            "max_uses": 2,
            "uses_remaining": 2,
        }
        foreign_token = tmp_path / "merge-authorized-foreign-bash"
        foreign_token.write_text(json.dumps(token_data))

        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "gh pr merge 42 --squash"},
            "tool_response": {
                "stdout": "Merged pull request #42",
                "stderr": "",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id",
                   return_value="local-session-abc"), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        # Token still unused — foreign-session retire skipped.
        assert foreign_token.exists(), (
            "I-5 violated in Layer 1: foreign-session token retired by "
            "local-session PostToolUse"
        )
        consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed) == 0, (
            "I-5 violated in Layer 1: produced spurious .consumed of foreign "
            "token"
        )

    # ------------------------------------------------------------------
    # Retry-preservation deeper coverage (scope item 5)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Concurrent races (scope item 3)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Block-1 false-positive sentinel (closes negative-test phantom-green)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # SEC-S2 op_type symmetry (#797 cycle-2)
    #
    # Extends Layer 1 token retirement from merge-only to op_type-symmetric
    # via LAYER1_SUCCESS_STDOUT_PATTERNS lookup table at
    # shared/merge_guard_common.py. Each new op_type (close, branch-delete,
    # force-push) has a positive test + a pair-revert sentinel per F2
    # phantom-green mitigation discipline. force-push uses Block 3 = None
    # (2-block degradation); its Block 2 structural defense is pinned via
    # the 7th test. The SSOT-pin test (test_sec_s2_lookup_table_ssot_pin)
    # enforces atomic-coupling between table mutations and this test
    # corpus per CLAUDE.md "Coupling-via-substring-count: atomic-commit
    # rule" applied to dict-key cardinality.
    # ------------------------------------------------------------------

    def _write_session_token(self, tmp_path, op_type, session_id="test-session"):
        """Helper: directly write a token with the given op_type +
        session_id, bypassing write_token's sparse-context guard.

        SEC-S2 fixtures need to test op_type-symmetric retirement across
        merge / close / branch-delete / force-push, but write_token's
        sparse-context guard accepts any one of {pr_number, branch,
        operation_type}. Direct write ensures the test fixture matches
        exactly the op_type under test without relying on write_token's
        classification path.
        """
        from shared.merge_guard_common import MAX_USES

        now = time.time()
        token_data = {
            "created_at": now,
            "expires_at": now + 300,
            "context": {"operation_type": op_type},
            "session_id": session_id,
            "max_uses": MAX_USES,
            "uses_remaining": MAX_USES,
        }
        token_path = tmp_path / f"merge-authorized-{op_type}-token"
        token_path.write_text(json.dumps(token_data))
        return token_path

    def test_sec_s2_force_push_op_retirement_positive(self, tmp_path):
        """SEC-S2: git push --force success retires force-push-typed token
        via 2-block predicate (Block 3 skipped because table value is None).

        Force-push uses Block 3 = None — git push --force emits primarily
        to stderr, not stdout, so substring-matching stdout is structurally
        fragile. Block 2's platform-success implication carries the
        retirement decision for force-push.

        # counter-test: change LAYER1_SUCCESS_STDOUT_PATTERNS["force-push"]
        #               from None to a fixed string (e.g., "Everything
        #               up-to-date") that does NOT appear in the empty
        #               stdout → Block 3 rejects; token NOT retired.
        # expected RED cardinality: {1}
        """
        from merge_guard_post import main

        token = self._write_session_token(
            tmp_path, "force-push", session_id="s-fp"
        )
        envelope = json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git push --force origin feat/x"},
            "tool_response": {
                # Empty stdout (force-push output goes to stderr).
                "stdout": "",
                "stderr": "+ abc1234...def5678 feat/x -> feat/x (forced update)",
                "interrupted": False,
            },
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp_path), \
             patch("merge_guard_post.get_session_id", return_value="s-fp"), \
             patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

        assert not token.exists(), (
            "SEC-S2: force-push token not retired via 2-block predicate"
        )
        consumed = list(tmp_path.glob("merge-authorized-*.consumed"))
        assert len(consumed) == 1

# =============================================================================
# #933 — merge_guard auth-token + dangerous-pattern defect fixes.
#
# Adversarial TEST CONTRACT (the architecture doc's §8) for the four fix
# surfaces:
#   D1-writer   — extract_context branch regex is flag-agnostic (the captured
#                 group is the branch NAME, never the delete FLAG).
#   D1-matcher  — _token_matches_command normalizes surrounding quotes at
#                 compare time, without widening the regex (no false-negative).
#   D2          — a 7th strip carrier exempts the non-executing quoted argument
#                 of a gh issue/pr CREATION verb; the carve-out and the
#                 compound pairing keep every real destructive op caught.
#   D3          — the post-hook keyword ladder recognizes a direct push to a
#                 default branch (main/master) as force-push.
#
# CARDINAL INVARIANT: never weaken detection of a real destructive op — over-
# block (false-positive) is acceptable; under-block (false-negative) is a
# security hole. Every fix carries a NEGATIVE that fails if the fix is mutated
# toward the forbidden behavior it guards. The mutation that proves each
# security negative non-vacuous is named per-test in a `# non-vacuity:` line.
#
# Dangerous literals live INSIDE these test-file string literals (never on a
# Bash command line), so this source file never trips merge_guard_pre.
# =============================================================================


# (removed class TestD1WriterBranchExtraction — superseded by the command-anchored
#  bidirectional suite in test_merge_guard_auth_symmetry.py;
#  it exercised the dropped prose classifier extract_context.)


class TestTargetAwareRetirement:
    """#1097 target-aware Layer-1 token retirement — the bidirectional cert.

    Pre-fix, `_retire_token_for_command` matched on op-type + session only, so a
    SUCCESSFUL unrelated same-op command (`gh pr close 43`) retired the operator's
    approved token for a DIFFERENT target (`close/42`) — an OVER-BLOCK: the
    faithful re-execution the operator approved then re-prompted. The fix computes
    `cmd_target = _target_value(extract_command_context(command))` once and skips
    any token whose own non-None target differs (`token_target is not None and
    cmd_target != token_target -> continue`). DEFENSIVE, not strict: a target-LESS
    token (degenerate/legacy shape) keeps the pre-fix op+session fallback, so
    nothing becomes un-retirable and no under-block opens.

    Priority per the governing principle: the over-block-cured direction
    (survival rows) is PRIMARY/inviolable; the still-retires direction is the
    secondary no-new-under-block sweep. ATTRIBUTION NOTE for main()-level rows:
    these tests call `_retire_token_for_command` DIRECTLY, below Block 3's stdout
    gate — an API-merge retiring command at main() level is Block-3-SKIPPED
    (its JSON success output does not contain LAYER1_SUCCESS_STDOUT_PATTERNS
    ["merge"]) and never reaches this predicate; the target predicate is
    load-bearing for CLI merge/close retiring commands, which DO emit the
    matching stdout. The live-probe covers the main()-level attribution split."""

    def _live(self, tmp_path):
        return [t for t in tmp_path.glob("merge-authorized-*")
                if not t.name.endswith(".consumed") and ".use-" not in t.name]

    # --- PRIMARY (inviolable): unrelated same-op does NOT retire a targeted token ---

    @pytest.mark.parametrize(
        "token_ctx,unrelated_cmd,op",
        [
    ({"repo": "o/r", "host": "github.com", "operation_type": "close", "pr_number": "42",
              "bound_flags": ["--delete-branch"]}, "gh pr close 43", "close"),
],
    )
    def test_unrelated_same_op_success_does_not_retire_targeted_token(
        self, tmp_path, token_ctx, unrelated_cmd, op
    ):
        """#1097 CURED (the over-block direction): a successful unrelated same-op
        command must NOT burn the operator's approved token for a different
        target. Pre-fix this retired (op+session match); post-fix the non-None
        token target discriminates."""
        from merge_guard_post import write_token, _retire_token_for_command

        write_token(token_ctx, token_dir=tmp_path)
        retired = _retire_token_for_command(unrelated_cmd, op, token_dir=tmp_path)
        assert retired is False, (
            f"OVER-BLOCK regressed: unrelated {unrelated_cmd!r} retired the "
            f"{token_ctx['operation_type']}/{token_ctx['pr_number']} token"
        )
        assert len(self._live(tmp_path)) == 1, "the protected token must survive"

    def test_no_target_command_does_not_retire_targeted_token(self, tmp_path):
        """Safe-direction edge: a retiring command with NO extractable target
        (bare `gh pr merge`) retires nothing against a targeted token —
        cmd_target None != token_target '42'."""
        from merge_guard_post import write_token, _retire_token_for_command

        write_token({"repo": "o/r", "host": "github.com", "operation_type": "merge", "pr_number": "42",
                     "bound_flags": []}, token_dir=tmp_path)
        retired = _retire_token_for_command("gh pr merge", "merge", token_dir=tmp_path)
        assert retired is False
        assert len(self._live(tmp_path)) == 1

    # --- SECONDARY (no-new-under-block): genuine self-consume STILL retires ---

    # --- target-completeness (the by-construction claim, verified) ---

    # --- non-vacuity: the survival rows are coupled to the target predicate ---


class TestFlagAwareRetirement:
    """#1100 flag-aware Layer-1 token retirement — the bidirectional cert.

    #1097 raised the retirement match key to op+target; #1100 raises it once
    more to op+target+bound_flags so a successful BARE same-op/same-target
    command no longer retires an operator's approved ESCALATED (flag-carrying)
    token — the bounded-friction OVER-BLOCK. `_retire_token_for_command`
    reuses the mint-stored `bound_flags` and the SAME `extract_privileged_flags`
    SSOT the read arm (merge_guard_pre) set-compares, adding an unconditional
    `set(ctx.bound_flags) != cmd_flags` skip AFTER the existing target check.

    Governing principle (task-level SACROSANCT): retirement is a PostToolUse
    OBSERVER — it can only fail-to-retire, never over-block a faithful click.
    The flag axis is a pure ADDITIONAL conjunctive skip, so:
      - PRIMARY (inviolable): an escalated token survives a bare same-target
        command (the #1100 cure) — RED on pre-#1100 main, GREEN after.
      - SECONDARY (no-new-under-block): a genuine self-consume (matching
        flags, INCLUDING the flagless case) STILL retires.

    Tokens are minted THROUGH `extract_command_context` (the production SSOT)
    so their stored `bound_flags` are scanner-derived, not hand-picked
    literals — this is what lets the row-4 neuter collapse BOTH the command-
    side and token-side flag sets symmetrically. In-memory only (write_token /
    _retire_token_for_command with token_dir=tmp_path); NO git mutation in the
    shared worktree (#1097 precedent)."""

    def _live(self, tmp_path):
        return [t for t in tmp_path.glob("merge-authorized-*")
                if not t.name.endswith(".consumed") and ".use-" not in t.name]

    @staticmethod
    def _mint_from(cmd, tmp_path):
        """Mint a token whose context (incl. bound_flags) is derived by the
        production SSOT extract_command_context — faithful to what a real
        approval of `cmd` would store, so the flag set is scanner-derived
        rather than a hand-picked literal."""
        from merge_guard_post import write_token, extract_command_context

        path = write_token(extract_command_context(cmd), token_dir=tmp_path)
        assert path is not None, f"mint failed for {cmd!r}"
        return path

    # --- SECONDARY (no-new-under-block): matching-flag self-consume STILL retires ---

    @pytest.mark.parametrize(
        "mint_cmd,retire_cmd,op",
        [
    ("gh pr merge 42 --admin", "gh pr merge 42 --admin", "merge"),
],
        ids=[
    "merge_admin",
],
    )
    def test_matching_flag_self_consume_still_retires(
        self, tmp_path, mint_cmd, retire_cmd, op
    ):
        """No-new-under-block: an execution whose flag-set EQUALS the approved
        token's (incl. the empty set) retires its own token — the
        one-approval-one-operation discipline holds across every op-class."""
        from merge_guard_post import _retire_token_for_command

        self._mint_from(mint_cmd, tmp_path)
        retired = _retire_token_for_command(retire_cmd, op, token_dir=tmp_path)
        assert retired is True, (
            f"NEW UNDER-BLOCK: self-consume stopped retiring for {op} ({retire_cmd!r})"
        )
        assert len(self._live(tmp_path)) == 0

    # --- PRIMARY (inviolable): flag mismatch at same op+target SURVIVES (#1100 cure) ---

    @pytest.mark.parametrize(
        "mint_cmd,retire_cmd,op",
        [
    ("gh pr close 5 --delete-branch", "gh pr close 5", "close"),
],
        ids=[
    "escalated_survives_bare_close",
],
    )
    def test_flag_mismatch_same_target_survives(
        self, tmp_path, mint_cmd, retire_cmd, op
    ):
        """#1100 CURED (RED on pre-#1100 main): same op + same target but a
        DIFFERENT flag-set must NOT retire — the bare and escalated forms are
        distinct token identities. Pre-#1100 retirement ignored flags, so a
        bare command over-retired the escalated token (the bounded friction)."""
        from merge_guard_post import _retire_token_for_command

        self._mint_from(mint_cmd, tmp_path)
        retired = _retire_token_for_command(retire_cmd, op, token_dir=tmp_path)
        assert retired is False, (
            f"#1100 OVER-BLOCK: {retire_cmd!r} retired the differently-flagged "
            f"{mint_cmd!r} token"
        )
        assert len(self._live(tmp_path)) == 1, "the escalated/bare token must survive"

    # --- flag-normalization symmetry: equal NORMALIZED sets retire (reuse of
    #     extract_privileged_flags, not a literal-key compare) ---

    # --- non-vacuity: the survival rows are coupled to the flag predicate ---

    # --- both-modes: identical flag-axis behavior across session states ---

class TestMalformedTokenRetirementRobustness:
    """#1100 follow-up (Copilot PR review): a token whose stored `bound_flags`
    is malformed must never crash `_retire_token_for_command` — the retirement
    observer is fail-safe (never raises, never blocks the caller). Two malformed
    classes both made `set(token_flags)` raise: (1) a NON-list value (JSON null ->
    None, or int/float/bool/str) -> `set(None)` TypeError; (2) a LIST containing a
    non-string / unhashable element (`[{}]`, `[["x"]]`) -> `unhashable type`. The
    token-side read is hardened with `isinstance(token_flags, list) and
    all(isinstance(x, str) for x in token_flags)`; a malformed token is SKIPPED,
    and — critically — a malformed token must not poison the rest of the
    retirement loop, so a VALID token in the same directory still retires.

    Tokens are written to disk DIRECTLY (bypassing write_token) so a malformed
    `bound_flags` the production mint path would never emit can be injected, and
    so two tokens can coexist (write_token's Layer-5 cleanup retires priors).
    In-memory only; no git mutation in the shared worktree."""

    def _live(self, tmp_path):
        return [t for t in tmp_path.glob("merge-authorized-*")
                if not t.name.endswith(".consumed") and ".use-" not in t.name]

    @staticmethod
    def _write_token_json(tmp_path, context, name="merge-authorized-1000"):
        """Write a token file directly with an arbitrary context (so a malformed
        `bound_flags` can be injected). Mirrors write_token's on-disk shape."""
        now = time.time()
        data = {"created_at": now, "expires_at": now + 300,
                "context": context, "max_uses": 2, "uses_remaining": 2}
        path = tmp_path / name
        path.write_text(json.dumps(data))
        return path

    @pytest.mark.parametrize(
        "bad_flags",
        [
    None,
],
        ids=[
    "none",
],
    )
    def test_malformed_bound_flags_does_not_crash_and_is_skipped(
        self, tmp_path, bad_flags
    ):
        """A token whose bound_flags is malformed — either NOT a list (None/int/
        float/bool/string) OR a list containing a non-string/unhashable element
        (`[{}]`, `[["x"]]`) — must NOT crash the retirement observer and must NOT
        be retired. Driven with a NON-empty command flag-set (`--delete-branch`)
        so the malformed token cannot match whether the guard SKIPS it or coerces
        bound_flags to [] — robust to the guard's exact shape. Without the guard
        the non-list crashers AND the unhashable-element lists raise `set(...)`
        TypeError (closed by the isinstance-list clause and the
        all(isinstance(x, str)) element clause respectively); the string case
        never crashes (char-set) but is still skipped."""
        from merge_guard_post import _retire_token_for_command

        self._write_token_json(
            tmp_path,
            {"repo": "o/r", "host": "github.com", "operation_type": "close", "pr_number": "5", "bound_flags": bad_flags},
        )
        try:
            retired = _retire_token_for_command(
                "gh pr close 5 --delete-branch", "close", token_dir=tmp_path)
        except Exception as exc:  # noqa: BLE001 — the observer contract is never-raise
            pytest.fail(
                f"malformed bound_flags ({bad_flags!r}) crashed the retirement "
                f"observer: {type(exc).__name__}: {exc}"
            )
        assert retired is False, "a malformed-flags token must not be retired"
        assert len(self._live(tmp_path)) == 1, (
            "the malformed token is skipped (survives), not consumed"
        )

    @pytest.mark.parametrize(
        "bad_flags", [
    None,
], ids=[
    "non_list",
]
    )
    def test_malformed_token_does_not_poison_valid_retirement(
        self, tmp_path, monkeypatch, bad_flags
    ):
        """RESILIENCE (the core of the finding): a malformed token encountered
        BEFORE a valid matching token must not abort the loop — the valid token
        still retires. `_retire_token_for_command` returns on the FIRST match and
        glob order is filesystem-arbitrary, so we FORCE the malformed token first
        via a monkeypatched glob to make the hazard deterministic (otherwise the
        test could pass by luck of iteration order). Covers BOTH crash surfaces
        (non-list and unhashable-element list). RED before the guard: the
        malformed token raises and the valid token is never reached."""
        import merge_guard_post as mgp
        from merge_guard_post import _retire_token_for_command

        malformed = self._write_token_json(
            tmp_path,
            {"repo": "o/r", "host": "github.com", "operation_type": "close", "pr_number": "5", "bound_flags": bad_flags},
            name="merge-authorized-1000",
        )
        valid = self._write_token_json(
            tmp_path,
            {"repo": "o/r", "host": "github.com", "operation_type": "close", "pr_number": "5",
             "bound_flags": ["--delete-branch"]},
            name="merge-authorized-2000",
        )
        # Visit the malformed token FIRST (deterministic hazard ordering).
        monkeypatch.setattr(
            mgp.glob, "glob", lambda pattern: [str(malformed), str(valid)])
        try:
            retired = _retire_token_for_command(
                "gh pr close 5 --delete-branch", "close", token_dir=tmp_path)
        except Exception as exc:  # noqa: BLE001 — the observer contract is never-raise
            pytest.fail(
                f"a malformed token poisoned the retirement loop: "
                f"{type(exc).__name__}: {exc}"
            )
        assert retired is True, (
            "the valid token must still retire after the malformed one is skipped"
        )
        live = self._live(tmp_path)
        assert len(live) == 1 and live[0].name == "merge-authorized-1000", (
            "the malformed token is skipped (survives); the valid token is consumed"
        )


class TestD1MatcherQuoteNormalization:
    """D1-matcher (#933): _token_matches_command normalizes surrounding quotes
    on the captured branch name at compare time, so a token branch `feat/x`
    matches a command that quotes the branch argument — WITHOUT widening the
    regex (the capture stays `(\\S+)`; only the comparison normalizes), so no
    mismatched branch is newly accepted.
    """

    @staticmethod
    def _tok(branch):
        # bound to this checkout like a minted approval (1.4.1 U13; the branch name does not
        # enter a branch-delete's checkout binding)
        return {"context": git_bound({"branch": branch, "operation_type": "branch-delete"},
                                     "git branch -D x")}

    def test_matches_single_quoted_branch(self):
        from merge_guard_pre import _token_matches_command

        assert _token_matches_command(self._tok("feat/x"), "git branch -D 'feat/x'")

    def test_matches_double_quoted_branch(self):
        from merge_guard_pre import _token_matches_command

        assert _token_matches_command(self._tok("feat/x"), 'git branch -D "feat/x"')

    def test_mismatched_quoted_branch_rejected(self):
        """SECURITY NEGATIVE: a DIFFERENT branch, quoted, is still rejected —
        proving the quote-strip normalized the value without widening which
        branches match.

        # non-vacuity: replace the buggy/un-widened behavior by mutating the
        #   matcher to compare AFTER stripping BOTH operands' quotes-and-more
        #   (or to substring-match) — any widening makes this pass-through.
        #   The committed guard against widening: this asserts a real
        #   non-match survives the quote-strip. A regex/compare that widened
        #   to accept any quoted token would FAIL this.
        """
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(self._tok("feat/x"), "git branch -D 'other'")

    def test_substring_quoted_branch_rejected(self):
        """SECURITY NEGATIVE: a SUPERSTRING branch (`feat/x-extra`), quoted,
        is rejected — the quote-strip yields `feat/x-extra` which is != the
        token `feat/x` under exact comparison. Proves no substring widening.

        # non-vacuity: a matcher that did `branch in stripped` (substring)
        #   instead of `stripped == branch` would ACCEPT `feat/x-extra` for a
        #   `feat/x` token → this assertion FAILS. Pins exact-equality.
        """
        from merge_guard_pre import _token_matches_command

        assert not _token_matches_command(
            self._tok("feat/x"), "git branch -D 'feat/x-extra'"
        )

    # ---- NEGATIVE / revert-proving ----

class TestD2GhCarrierStrip:
    """D2 (#933): the 7th strip carrier exempts the non-executing quoted
    argument of a gh issue/pr CREATION verb (`gh issue create|edit`,
    `gh pr create`) — so a dangerous-op literal named inside a `--title`/
    `--body`/`-t`/`-b` value no longer trips DANGEROUS_PATTERNS.

    Positive cases pin the false-positive FIX. The deviation pins (two-value
    strip) lock the AS-BUILT carrier-span behavior, which differs from the
    architecture doc's single-re.sub literal (the per-span inner-strip strips
    BOTH a --title and a --body on one command).
    """

    # ---- POSITIVE: benign carrier no longer over-blocks ----


    # ---- DEVIATION PIN: two-value strip (the as-built carrier-span shape) ----


    # ---- SECURITY NEGATIVES / revert-proving (the highest-value tests) ----


    def test_pr_close_delete_branch_still_dangerous_carveout(self):
        """SECURITY NEGATIVE (the carve-out): `gh pr close --delete-branch` is
        NEVER exempted — `close` is absent from the carrier verb alternation,
        so the command is not stripped and DANGEROUS_PATTERNS still fires.

        Two assertions, because the carve-out has two independent protective
        layers and the behavioral one alone is VACUOUS w.r.t. the carrier:

        1. Behavioral: the command stays dangerous. NOTE this is robust even
           if `close` were wrongly added to the carrier verbs — the deny
           trigger is the bare `--delete-branch` flag (DANGEROUS_PATTERNS
           `pr close (?=.*--delete-branch)`), NOT a quoted value, so the
           quoted-value-only inner strip can never neutralize it. So this
           line does NOT, by itself, prove the verb exclusion is load-bearing.
        2. Mechanism-level: on a `gh pr close` command that ALSO carries a
           quoted value, the value SURVIVES the strip (close is not a carrier).
           THIS is the assertion that isolates the verb exclusion.

        # non-vacuity: a removal-revert of the 7th carrier does NOT prove the
        #   carve-out (close was never a carrier verb → both lines stay GREEN
        #   == phantom-green). Prove it via the EXCLUSION-GUARD pattern:
        #   BROADEN the verb alternation to include `close`
        #   (`pr\\s+(?:create|close)`) in an isolated edit → the quoted value
        #   is stripped → assertion (2) FAILS (verified RED). Assertion (1)
        #   correctly stays green under that mutation (the `--delete-branch`
        #   deny trigger is not a quoted value) — which is exactly why (2) is
        #   the load-bearing pin, not (1). Restore after.
        """
        from merge_guard_pre import (
            _strip_non_executable_content,
            is_dangerous_command,
        )

        # (1) Behavioral — stays dangerous (robust, but vacuous w.r.t. carrier).
        assert is_dangerous_command("gh pr close 42 --delete-branch")

        # (2) Mechanism-level — the quoted value on a close command is NOT
        #     stripped, because `close` is excluded from the carrier verbs.
        close_cmd = 'gh pr close 42 --delete-branch --body "git branch -D x"'
        assert '"git branch -D x"' in _strip_non_executable_content(close_cmd)


    # ---- NEGATIVE: non-carrier gh verbs are NOT exempted ----


    # ---- NEGATIVE: an unquoted dangerous token inside the span survives ----


    # ---- REGRESSION GUARD: existing carrier 5 unchanged ----


class TestD3LadderPushToMain:
    """D3 (#933) symmetry sanity. The prose keyword-ladder that classified
    direct-push-to-main from QUESTION PROSE was DROPPED — the post hook now
    mints only from an embedded COMMAND (covered by the command-anchored
    bidirectional suite in test_merge_guard_auth_symmetry.py). What remains here
    is the read-side over-block sanity: an op-only force-push token never
    authorizes a cross-op command.
    """

    # (removed 5 methods that exercised the dropped prose extractor
    #  merge_guard_post.extract_context — direct-push/push-local/push-master
    #  prose classification, the merge-into-main negative, and the prose ladder
    #  precedence. Force-push detection from a COMMAND is covered by
    #  extract_command_context in test_merge_guard_auth_symmetry.py.)

    def test_force_push_token_does_not_authorize_non_force_push_command(self):
        """SYMMETRY SANITY: an over-matched force-push token (minted from
        direct-push prose) must NOT authorize a non-force-push command — the
        op_type guard at the PRE side blocks it. Proves the deliberately-broad
        D3 prose arm is harmless (over-match → over-block, never under-block).
        """
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "force-push"}}
        assert not _token_matches_command(token, "gh pr merge 42")


class TestD3PushToMainAuthorizationEndToEnd:
    """D3 (#933): direct-push-to-main is now minted only from an embedded
    COMMAND (the prose-classification mint was dropped); the end-to-end
    command-anchored mint is covered by test_merge_guard_auth_symmetry.py. What
    remains here is the read-side cross-op over-block sanity.
    """

    # (removed test_direct_push_to_main_prose_mints_a_token — it minted from the
    #  dropped prose extractor merge_guard_post.extract_context.)

    def test_push_to_main_token_does_not_authorize_cross_op(self):
        """Paired negative: a force-push token minted from direct-push-to-main
        prose does NOT authorize a cross-op `git branch -D x` command (the
        op_type axis does not match the branch-delete axis)."""
        from merge_guard_pre import _token_matches_command

        token = {"context": {"operation_type": "force-push"}}
        assert not _token_matches_command(token, "git branch -D some-branch")


# =============================================================================
# #933 REMEDIATION (PR #1000 review findings) — M2/M3 quote-aware D2 span.
#
# The D2 carrier span body `[^&|;]*` was replaced with a quote-region-aware
# scanner:
#     gh<verb>  (?:[^&|;\n"']+ | "(?:[^"\\]|\\.)*" | '[^']*')*
# Three alternatives with DISJOINT first-character sets (bare run / balanced
# double-quote honoring \" / balanced single-quote, no escape). Effects:
#   M3 — a quoted value containing an internal ;/&/| is consumed ATOMICALLY
#        (the separator is inside the quote, not a span boundary) → the inner
#        strip sees the FULL value and strips it → benign title with internal
#        separators is no longer over-blocked.
#   M2 — a multi-line quoted title is consumed atomically (newline inside the
#        quote), while an UNQUOTED newline still terminates the span — fixing
#        the over-block a naive `[^&|;\n]*` body would have caused.
# The inner strip, the carve-out (close NOT a carrier verb), and the
# _has_command_substitution guard are UNCHANGED — INV-D2 (no real executing op
# is ever neutralized) is preserved: an unquoted separator/newline always
# terminates the span, and an UNBALANCED quote makes the span UNDER-consume
# (stop early) = over-block = safe, never under-block.
#
# NON-VACUITY uses OPPOSITE mutations for the two halves (the crux):
#   - 7.A multi-line/internal-sep POSITIVES are proven by an UNDER-CONSUME
#     mutation (span body → `[^&|;]*`): the value is truncated at its internal
#     separator → the inner strip misses it → is_dangerous flips True → FAIL.
#   - 7.B/7.C under-block + desync NEGATIVES are proven by an OVER-CONSUME
#     mutation (span body → `.*`): the span swallows past the unquoted
#     separator / unbalanced quote → the inner strip reaches the real op →
#     is_dangerous flips False → FAIL. (The old `[^&|;]*` body would NOT flip
#     these — it under-consumes too, so it keeps them dangerous == phantom-
#     green; that is why the negatives need the over-consume mutation, not the
#     `[^&|;]*` revert.)
# Each mutation + the RED set it produces is recorded per the `# non-vacuity:`
# convention; all mutations are restored byte-exact (git checkout HEAD --).
# =============================================================================


# --- Shared carrier analysis for the 7.E positional-safety pins below. --------
#
# WHY THIS IS VALUE-BASED, NOT SHAPE-BASED. An earlier form of these pins walked
# `ast.BinOp`/`Add` chains and asked whether a carrier NAME was the last operand.
# That question is only meaningful for ONE spelling of concatenation. A carrier
# composed with `%`, an f-string, `.format` or `str.join` produced no `Add` node
# at all, so it entered neither the discovery fixed point nor the offender scan
# and the pin passed while the span sat in a non-terminal position. Measured: a
# `%`-composed carrier with a trailing `\Z` took 7.1 SECONDS on a ~30-character
# command and the whole merge-guard suite stayed green.
#
# So we EVALUATE each carrier expression to its actual string instead of
# inspecting its syntax, with `_VERB_MSG_BODY` replaced by a sentinel, and then
# ask whether the result ENDS WITH that sentinel. Composition spelling stops
# mattering: `+`, `%`, f-string and `.join` all reduce to the same string.
#
# FAIL-SAFE, AND ON WHICH AXIS. On the SPELLING axis the claim holds: a
# composition this evaluator cannot value loses the sentinel, the carrier drops
# out of the population, and the count assertion in the terminality pin fires.
# On the POSITION axis it does NOT hold on its own — a carrier the population
# never enumerates is silent, not loud. That is why the population below is
# every BINDING of every name plus every UNBOUND carrier expression, and not
# one binding per name. Two shapes lived outside "one binding per name" and
# both were caught by the predecessor and missed here: the span handed straight
# to `re.sub(...)` with no name at all, and a name assigned twice where the
# second assignment moved the span off the end.
#
# WHICH DIRECTION IS WORSE HERE, WHICH IS THE OPPOSITE OF THE GUARD ITSELF.
# In `is_dangerous_command` an over-block is the cardinal sin: it refuses a real
# user's faithful click. This is a PIN OVER that control, and the blast radii
# are not the same. An over-block here reddens CI for a developer — loud,
# immediate, cheap, and it announces itself. An under-block here lets an edit
# that re-arms the exponential backtracking ship SILENTLY into the SACROSANCT
# control. So when a change to this analysis trades one direction for the
# other, prefer the false alarm. Do not carry the guard's severity ordering
# into the pin without re-deriving it; the subject changed underneath it.
_SPAN_SENTINEL = "\x00CARRIER_SPAN\x00"
_SPAN_SOURCE_NAME = "_VERB_MSG_BODY"


def _all_merge_guard_modules():
    """Every module that makes up the merge-guard implementation: the two hooks, the
    merge_guard_common façade and the hooks/shell/merge package it re-exports.

    The span is defined in `shell.merge.strip`, but a carrier can be built in any
    module that imports from it, and one there is exactly as dangerous. Both pins
    below run over this list rather than over the defining module alone.
    """
    import importlib
    import pkgutil

    import merge_guard_post
    import merge_guard_pre
    import shell.merge
    from shared import merge_guard_common

    package = tuple(importlib.import_module(f"shell.merge.{info.name}")
                    for info in pkgutil.iter_modules(shell.merge.__path__))
    assert len(package) >= 10, "the shell.merge package did not enumerate"
    return (merge_guard_common, merge_guard_pre, merge_guard_post, *package)


def _carrier_span_analysis(source=None, module=None):
    """Resolve every carrier in ONE module and locate the span in each.

    Returns (carriers, offenders, resolved, unbound) where `carriers` is the set
    of local names carrying the span, `offenders` is [(lineno, name, tail)] for
    carriers whose span is NOT terminal, `resolved` counts bindings we fully
    valued, and `unbound` counts carrier expressions that are never assigned to
    a name.

    `source` overrides the module text, so the safe/unsafe shape families below
    can drive this over synthetic carriers instead of only over whatever the
    shipped module happens to contain today.

    `module` selects WHICH module to read and whose globals resolve free names.
    The implementation is THREE files — `merge_guard_common` plus the two hooks
    that import from it — and a carrier in any of them is equally dangerous, so
    every caller here runs over all three (see `_ALL_MERGE_GUARD_MODULES`).
    Reading only the shared module was a latent gap: a carrier in
    `merge_guard_pre`/`merge_guard_post` was unreachable BY CONSTRUCTION, and no
    count assertion could notice because the population it counted was the one
    module it looked at.
    """
    import ast

    from shared import merge_guard_common

    if module is None:
        module = merge_guard_common
    if source is None:
        source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    # The span is DEFINED in merge_guard_common, so in a sibling module it only
    # ever arrives through an import — and an import may rename it. Resolve the
    # local aliases from the module's own import statements rather than assuming
    # the canonical name: `from ... import _VERB_MSG_BODY as _VMB` binds the span
    # to `_VMB`, and a scan keyed on the canonical spelling sees nothing at all.
    span_names = {_SPAN_SOURCE_NAME}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _SPAN_SOURCE_NAME:
                    span_names.add(alias.asname or alias.name)

    # ...and the OTHER way a sibling module can reach the span: bind the whole
    # module and go through it — `import shared.merge_guard_common as m` then
    # `m._VERB_MSG_BODY`. That is an ast.Attribute, not an ast.Name, so a scan
    # that only knows names walks straight past it and reports a clean zero.
    #
    # NOT hypothetical: `import shared.pact_context as pact_context` followed by
    # `pact_context.init(...)` is already present in BOTH merge_guard_pre.py and
    # merge_guard_post.py — the exact two modules this reaches. The idiom is one
    # substitution away from a pattern an editor of those files already uses,
    # and it fails toward MISSING a carrier, which is the unsafe direction.
    module_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1] == "merge_guard_common":
                    module_aliases.add(alias.asname or alias.name.split(".")[0])

    def _dotted(node):
        """`a.b.c` -> 'a.b.c' for a pure Name/Attribute chain, else None."""
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        parts.append(node.id)
        return ".".join(reversed(parts))

    def _is_span_ref(node):
        """True for ANY static spelling of a reference to the span itself.

        Three shapes reach here, and they are the complete set of ways Python
        can name a value from another module WITHOUT constructing the name at
        runtime: a bare local name (canonical, or bound by `from X import s as
        alias`), an attribute on a module alias (`import X as m` -> `m.s`), and
        an attribute on a DOTTED module path (`import X.Y` -> `X.Y.s`). The
        receiver is walked as a chain rather than required to be a bare Name,
        because the third shape's receiver is itself an Attribute.

        Dynamic spellings (`getattr`, `globals()[...]`, `importlib`) are NOT
        recognised here and deliberately so — see the module-level note on the
        two-layer design. They are caught by the use-site census instead.
        """
        if isinstance(node, ast.Name):
            return node.id in span_names
        if isinstance(node, ast.Attribute) and node.attr == _SPAN_SOURCE_NAME:
            path = _dotted(node.value)
            if path is None:
                return False
            return (
                path in module_aliases
                or path.split(".")[-1] == "merge_guard_common"
            )
        return False

    # Every single-target assignment in the module, by name. Carriers are locals
    # inside _strip_non_executable_content, so `ast.walk` (not module globals)
    # is what reaches them.
    #
    # A LIST per name, in source order — not one node per name. A name assigned
    # twice has two bindings and BOTH are carriers. Keeping only the first is
    # what let `_s = LIT + BODY` followed by `_s = _s + r"..."` pass: the second
    # binding moved the span off the end and was never looked at.
    assigns = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                assigns.setdefault(target.id, []).append(node)
    for _bindings in assigns.values():
        _bindings.sort(key=lambda n: n.lineno)

    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node

    class _Opaque(str):
        """A resolved-but-unknown fragment. Truthy and non-empty on purpose."""

    def value_of(node, seen, before=None):
        """Evaluate an expression node to a string. Never raises.

        `before` is the line the reference is made FROM. A name resolves to its
        nearest binding above that line, so the second half of `_s = _s + r"x"`
        reads the EARLIER `_s` rather than re-entering itself. `before` also
        decreases strictly on every hop, so the walk terminates on its own.
        """
        if isinstance(node, ast.Constant):
            return node.value if isinstance(node.value, str) else repr(node.value)
        # `m._VERB_MSG_BODY` — the module-alias spelling. Deliberately NARROW:
        # only this exact attribute on a name bound to merge_guard_common
        # resolves to the span. Every other Attribute keeps falling through to
        # the opaque marker below, so this widening cannot newly report an
        # unrelated attribute access as a carrier.
        if isinstance(node, ast.Attribute):
            if _is_span_ref(node):
                return _SPAN_SENTINEL
            return _Opaque("?")
        if isinstance(node, ast.Name):
            if node.id in span_names:
                return _SPAN_SENTINEL
            if node.id in seen:                       # cycle guard
                return _Opaque("?")
            bindings = assigns.get(node.id, [])
            earlier = [b for b in bindings if before is None or b.lineno < before]
            # Fall back to the first binding for a genuine forward reference (a
            # name defined below the function that reads it).
            src = earlier[-1] if earlier else (bindings[0] if bindings else None)
            if src is not None:
                return value_of(src.value, seen | {node.id}, src.lineno)
            # Module-level constant (e.g. _MAX_GLOBAL_FLAG_TOKENS, _GH_API_PREFIX).
            if hasattr(module, node.id):
                got = getattr(module, node.id)
                return got if isinstance(got, str) else repr(got)
            return _Opaque("?")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return value_of(node.left, seen, before) + value_of(node.right, seen, before)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            left = value_of(node.left, seen, before)
            right = value_of(node.right, seen, before)
            try:
                return left % right
            except (TypeError, ValueError):
                return left + _Opaque("?")
        if isinstance(node, ast.JoinedStr):            # f-string
            return "".join(value_of(v, seen, before) for v in node.values)
        if isinstance(node, ast.FormattedValue):
            return value_of(node.value, seen, before)
        if isinstance(node, ast.Call):
            func = node.func
            # `re.compile(X)` is PATTERN-PRESERVING: it does not move the span,
            # so it must resolve to X exactly. Appending an opaque tail here
            # would report a compiled-then-`sub`'d carrier as non-terminal —
            # an over-block in the guard itself, on a construct that is safe.
            # Compiled bindings are still collected as carriers below, because
            # the FULLMATCH pin does care about them.
            if (
                isinstance(func, ast.Attribute) and func.attr == "compile"
                and node.args
            ):
                return value_of(node.args[0], seen, before)
            # ONLY a string-COMPOSING call can pass the span through to its own
            # value. `re.sub(pattern, repl, text)` returns the SUBSTITUTED TEXT,
            # not its pattern argument. Resolving the arguments of an arbitrary
            # call would make every `result = re.sub(_carrier, ...)` line in the
            # strip pipeline render as a carrier with an opaque tail and report
            # the whole module — an over-block created by the very widening that
            # closed the terminality gap. Checking every binding of a name makes
            # this reachable, so the two changes have to land together.
            if not (
                isinstance(func, ast.Attribute) and func.attr in ("join", "format")
            ):
                return _Opaque("?")
            # `"".join([...])` and friends — resolve the pieces we can see so a
            # span inside them still registers, then mark the rest opaque.
            #
            # KNOWN AND DELIBERATE: this arm cannot VALUE a `.join`/`.format`
            # carrier, it can only notice one. `.join([...])` hands us an
            # ast.List, which is not in the filter below, so the span is lost
            # and the carrier drops out of `carriers` — the count assertion in
            # the terminality pin is what catches that, not this scan. A safe
            # `.format` carrier is likewise reported as an offender because the
            # opaque tail is unconditional. Both are OVER-blocks: loud, at CI
            # time, on an unusual spelling. Neither is in the safe-shape family
            # below for that reason. Widening this arm to value them is worth
            # doing; it is not worth doing at the same time as the terminality
            # repair, because the two changes fail in opposite directions.
            inner = "".join(
                value_of(a, seen, before) for a in node.args
                if isinstance(a, (ast.Constant, ast.Name, ast.BinOp, ast.JoinedStr))
            )
            return inner + _Opaque("?") if inner else _Opaque("?")
        return _Opaque("?")

    carriers, offenders, resolved = set(), [], 0
    for name, bindings in assigns.items():
        for node in bindings:
            rendered = value_of(node.value, frozenset(), node.lineno)
            if _SPAN_SENTINEL not in rendered:
                continue
            carriers.add(name)
            resolved += 1
            tail = rendered.rsplit(_SPAN_SENTINEL, 1)[1]
            if tail:
                offenders.append((node.lineno, name, tail[:80]))

    # Carrier expressions that are NEVER BOUND to a name — the span handed
    # straight to `re.sub(...)` as an argument. The loop above walks
    # assignments, so an unassigned expression has no entry to be found under
    # and was previously unreachable: appending a required element to the
    # echo/printf carrier re-armed the explosion in total silence.
    #
    # Walk UP from each span mention through the nodes that can compose a
    # string around it, and check the outermost one. Stopping at the composing
    # boundary is what keeps the enclosing `re.sub(...)` call out of it — its
    # attribute is `sub`, so it does not compose, and treating it as a carrier
    # would append an opaque tail to every clean call site and report the whole
    # module.
    _COMPOSING_CALLS = {"join", "format", "compile"}

    def _composes(node):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            return True
        if isinstance(node, (ast.JoinedStr, ast.FormattedValue)):
            return True
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return True          # the argument list of a `.join(...)`
        if isinstance(node, ast.Call):
            func = node.func
            return isinstance(func, ast.Attribute) and func.attr in _COMPOSING_CALLS
        return False

    assigned_values = {id(b.value) for bs in assigns.values() for b in bs}
    unbound, seen_exprs = 0, set()
    for node in ast.walk(tree):
        # Both spellings: a bare name, and `m._VERB_MSG_BODY` through a
        # module alias. Keying this scan on ast.Name alone was the hole.
        if not _is_span_ref(node):
            continue
        if not isinstance(node.ctx, ast.Load):        # skip the definition itself
            continue
        top = node
        while True:
            up = parents.get(id(top))
            if up is None or not _composes(up):
                break
            top = up
        if id(top) in assigned_values or id(top) in seen_exprs:
            continue                                   # already checked above
        seen_exprs.add(id(top))
        rendered = value_of(top, frozenset(), getattr(top, "lineno", None))
        if _SPAN_SENTINEL not in rendered:
            continue
        unbound += 1
        resolved += 1
        tail = rendered.rsplit(_SPAN_SENTINEL, 1)[1]
        if tail:
            offenders.append((top.lineno, "<unbound carrier expression>", tail[:80]))

    # Names bound to a COMPILED carrier (`_x_re = re.compile(_carrier_span)`).
    # Compiling does not move the span, so these are not terminality offenders —
    # but they ARE carriers for the fullmatch pin, which otherwise sees only a
    # literal carrier name at the call site and misses this module's dominant
    # compile-then-call idiom.
    for _ in range(8):
        grew = False
        for name, bindings in assigns.items():
            if name in carriers:
                continue
            for node in bindings:
                if not isinstance(node.value, ast.Call):
                    continue
                func = node.value.func
                if not (isinstance(func, ast.Attribute) and func.attr == "compile"):
                    continue
                if any(
                    isinstance(sub, ast.Name) and sub.id in carriers
                    for arg in node.value.args
                    for sub in ast.walk(arg)
                ):
                    carriers.add(name)
                    grew = True
                    break
        if not grew:
            break

    return carriers, offenders, resolved, unbound


# --- Carrier-shape families: the safe-shape negative control. -----------------
#
# The shipped module passing is a negative control for exactly the THREE shapes
# it happens to contain today (`LIT + BODY`, `(LIT % CONST) + BODY`,
# `LIT + NAME + LIT + BODY`). Every other spelling was unprotected: nothing here
# demonstrated that this analysis does not REFUSE safe code, and the
# compile-then-`sub` branch it leans on never fires on the real module, so a
# regression in it would redden nothing.
#
# The UNSAFE family is not decoration — it is what stops the safe family being
# vacuous. A safe-only family would also pass against an analysis that reported
# nothing at all, which is the failure this whole pin exists to prevent.
#
# Each snippet is parsed, never executed. `_VERB_MSG_BODY` resolves to a
# sentinel by name, so these need no imports and no module state.
_SAFE_CARRIER_SHAPES = (
    ("plain concatenation",
     '_s = r"\\bfind\\s+" + _VERB_MSG_BODY'),
    ("span rebound through an intermediate, still terminal",
     'import shared.merge_guard_common as _m\n'
     '_v = _m._VERB_MSG_BODY\n'
     '_s = r"\\bfind\\s+" + _v'),
)

_UNSAFE_CARRIER_SHAPES = (
    ("trailing literal after the span",
     '_s = r"\\bfind\\s+" + _VERB_MSG_BODY + r"\\s*x"'),
    ("re-concatenated into a new name",
     '_s = r"\\bfind\\s+" + _VERB_MSG_BODY\n_t = _s + r"\\s*x"'),
)

# Shapes this analysis cannot value, and therefore refuses. Listed so the limit
# is VISIBLE rather than discovered by whoever next writes one. See the Call
# branch of `value_of` for why each fails.
_KNOWN_REFUSED_CARRIER_SHAPES = (
    ("str.join", '_s = "".join([r"\\bfind\\s+", _VERB_MSG_BODY])'),
    ("str.format", '_s = r"\\bfind\\s+{}".format(_VERB_MSG_BODY)'),
    ("group-wrapped whole span",
     '_s = r"(?:" + r"\\bfind\\s+" + _VERB_MSG_BODY + r")"'),
)


class TestD2QuoteAwareSpanRemediation:
    """#933 M2/M3 — the quote-aware carrier span (design §7.A-E).

    Dangerous literals live inside test-file string literals (never on a Bash
    line). The original 40-test suite's carve-out / compound negatives remain
    in TestD2GhCarrierStrip; this class adds the quote-aware-span pins.
    """

    # ---- 7.A — M3 / multi-line POSITIVES (a title whose quoted value contains
    #            an internal separator AND a dangerous literal AFTER that
    #            separator strips fully → NOT dangerous). ----
    #
    # NON-VACUITY DESIGN (load-bearing): each positive embeds a DANGEROUS
    # LITERAL positioned AFTER the internal `;`/`&`/`|`/newline. This is what
    # makes the test non-vacuous against the UNDER-CONSUME mutation: with the
    # `[^&|;]*` body the span TRUNCATES at the internal separator, leaving the
    # post-separator dangerous literal OUTSIDE the (now-unterminated) quoted
    # value → the inner strip cannot match it → the literal survives →
    # is_dangerous flips True → FAIL. A benign title (no dangerous literal,
    # e.g. "a; b") would stay `safe` under BOTH spans == phantom-green, so it
    # is NOT used here.
    #
    # non-vacuity (7.A — verified per-test, TWO under-consume mutations):
    #   - internal-`;`, internal-`&|`, danger-before-`;`, two-value: revert the
    #     span body to `[^&|;]*` → the value truncates at its internal
    #     separator → the post-separator dangerous literal is exposed → flips
    #     RED. (This is the SAME `[^&|;]*` that keeps the 7.B/7.C negatives
    #     green — the opposite-mutation crux.)
    #   - multiline: `[^&|;]*` consumes ACROSS newlines (it excludes only
    #     `&|;`), so it does NOT truncate a multi-line value → that test STAYS
    #     GREEN under `[^&|;]*` (would be phantom-green there). Its faithful
    #     mutation is the NAIVE FIX `[^&|;\n]*` (adds `\n` to the exclusion —
    #     the exact regression the architect's §7.A calls out): the span stops
    #     at the first newline → the later-line dangerous literal is exposed →
    #     flips RED. Verified: `[^&|;]*` → 3 of the 4 non-multiline strip-tests
    #     RED (multiline green); `[^&|;\n]*` → all 4 RED.


    # ---- 7.B — UNDER-BLOCK NEGATIVES (INV-D2: a real op after an UNQUOTED
    #            separator MUST stay caught). ----
    #
    # non-vacuity (whole 7.B block): OVER-CONSUME whole-span over-strip —
    #   broaden the span body to `.*` AND blank the matched span → the span
    #   swallows the unquoted separator + the trailing op → is_dangerous flips
    #   False → FAIL. Verified RED. The `[^&|;]*` revert does NOT flip these
    #   (it also stops at the unquoted separator == phantom-green) — over-
    #   consume is required.
    #   NEWLINE caveat: `.*` does NOT cross a newline (no re.DOTALL), so the
    #   newline-tail case (test_under_block_newline_tail) needs the
    #   newline-crossing over-consume `[\s\S]*` to flip — verified RED under
    #   that mutation. (Plain `.*` leaves it green because the span still stops
    #   at the unquoted `\n`.)


    # ---- 7.C — DESYNC NEGATIVES (escaped/unbalanced/mismatched quotes MUST
    #            NOT smuggle an op past the span). ----
    #
    # non-vacuity (whole 7.C block): OVER-CONSUME-PAST-UNBALANCED-QUOTE
    #   whole-span over-strip — broaden the span body so it consumes past an
    #   unbalanced quote (body → `.*` AND blank the matched span) → the span
    #   swallows the trailing op → is_dangerous flips False → these FAIL.
    #   Verified RED. The as-built body stops at the unbalanced quote (under-
    #   consume = over-block = safe), so a real op after it always survives.
    #   NEWLINE caveat: the unbalanced-DQ-then-NEWLINE case
    #   (test_desync_unbalanced_dq_then_newline_op) needs the newline-crossing
    #   `[\s\S]*` over-consume to flip (plain `.*` stops at the `\n`) —
    #   verified RED under that mutation.


    # ---- 7.D — Carve-out REGRESSION GUARD (unchanged by the span change). ----


    def test_carveout_pr_close_value_survives_strip(self):
        """Mechanism-level: a quoted value on a `gh pr close` command is NOT
        stripped (close is excluded from the carrier verbs), even under the
        new quote-aware span.

        # non-vacuity: broaden the carrier verb alternation to include `close`
        #   (`pr\\s+(?:create|close)`) → the value is stripped → this assertion
        #   FAILS. (Exclusion-guard non-vacuity, carried over from the original
        #   carve-out pin.)
        """
        from merge_guard_pre import _strip_non_executable_content

        cmd = 'gh pr close 42 --delete-branch --body "git branch -D x"'
        assert '"git branch -D x"' in _strip_non_executable_content(cmd)


    # ---- 7.E — ReDoS: the span's safety is POSITIONAL, not intrinsic. ----

    def test_verb_msg_body_is_terminal_in_every_carrier(self):
        """`_VERB_MSG_BODY` IS catastrophically backtrackable. Only POSITION stops it.

        MEASURED, not reasoned. Give the span a required element after it that
        fails to match, and it is EXPONENTIAL — roughly 2x per added input
        character: n=12 0.1ms, n=20 34ms, n=24 539ms, n=2000 >5s. The shape is
        `(?:...|[^&|;\\n"'$\\\\]+|...)*` — a `+` arm inside an outer `*`, which is
        the textbook `(a+)*` explosion.

        WHAT ACTUALLY MAKES THE SHIPPED CODE SAFE IS POSITION. In every carrier
        the span is the LAST element, so nothing after it can fail, so the match
        always succeeds and the engine never backtracks into the span. The
        safety property lives at the CALL SITES, not in the pattern.

        WHAT BREAKS IT — each of these makes the guard exponentially
        backtrackable on ordinary input, with NO change to the regex at all:
          * append anything required after the span in a carrier — a closing
            token, a literal, another sub-pattern;
          * add an end anchor (`$`, `\\Z`) after it;
          * consume a carrier with `re.fullmatch` instead of `search`/`sub`
            (pinned by the sibling test below).

        WHAT THIS PIN COVERS, AND WHAT IT DOES NOT. It resolves each carrier to
        its VALUE, so the first two breakages are caught whatever spelling built
        the carrier: `+`, `%` and f-string all reduce to one string, and that
        is measured, not asserted — the shape families below run each spelling
        in both a safe and an unsafe form. `.join` and `.format` are the
        exceptions and are NOT valued: `.join` loses the span (its argument is
        an ast.List) and drops the carrier from the population, where the count
        assertion below catches it; a `.format` carrier always reports an
        opaque tail, safe or not. Both are OVER-blocks on unusual spellings,
        both are listed in the known-refused family below so the next reader
        meets them as a recorded limit rather than as a surprise.

        COVERAGE, STATED PLAINLY. Both pins read ALL THREE implementation
        modules — `merge_guard_common` plus the two hooks that import from it
        (see `_all_merge_guard_modules`). A carrier built in `merge_guard_pre.py`
        or `merge_guard_post.py` is as dangerous as one here and is checked the
        same way, including when the span arrives under an import alias or
        through a module attribute.

        THIS PIN IS NOT THE ONLY THING THAT COULD NOTICE, and the claim that it
        was is why the earlier form of it went unchallenged. A behavioural arm
        catches these breakages too, from the other side: it is agnostic to how
        the carrier was composed AND to which module holds it, but it only
        reaches the carriers its input traverses. The two are complements, not
        substitutes. Do not delete one because the other is green.

        The check is TRANSITIVE, in both of the ways a carrier can travel. A
        carrier bound to a local name can be re-concatenated into a NEW name
        (`_other = _git_commit_span + r"..."`), and it can be re-concatenated
        into the SAME name (`_s = _s + r"..."`). Following only
        `_VERB_MSG_BODY` misses the first; keeping one binding per name misses
        the second. The resolver collects every binding of every name, resolves
        each reference to its nearest PRECEDING binding, and checks all of
        them.
        """
        # ALL THREE modules, not just the shared one. A carrier in
        # merge_guard_pre/merge_guard_post is exactly as dangerous and was
        # previously unreachable by this pin.
        carriers, offenders, resolved, unbound = set(), [], 0, 0
        _use_sites = []
        import ast
        import re as _re

        for _mod in _all_merge_guard_modules():
            _c, _o, _r, _u = _carrier_span_analysis(module=_mod)
            _tag = Path(_mod.__file__).name
            carriers |= {f"{_tag}::{n}" for n in _c}
            offenders += [(_tag, *row) for row in _o]
            resolved += _r
            unbound += _u

            # Count use sites PER MODULE, against that module's own span
            # aliases — a sibling may import the span under another name, and a
            # census keyed on the canonical spelling would score it zero and
            # call the population complete.
            _src = Path(_mod.__file__).read_text(encoding="utf-8")
            _aliases = {_SPAN_SOURCE_NAME}
            for _ln in _src.splitlines():
                _m = _re.search(
                    r"import\s+.*\b%s\b\s+as\s+(\w+)" % _SPAN_SOURCE_NAME, _ln
                )
                if _m:
                    _aliases.add(_m.group(1))
            # Lines that NAME the span without using it: a parenthesised import
            # list (the façade re-exports it, the package modules import it) and
            # the module docstring's Map line. Found by AST, not by prefix, so a
            # continuation line of a multi-line import is skipped too.
            _tree = ast.parse(_src)
            _named_only = set()
            for _n in ast.walk(_tree):
                if isinstance(_n, (ast.Import, ast.ImportFrom)):
                    _named_only.update(range(_n.lineno, _n.end_lineno + 1))
            if _tree.body and isinstance(_tree.body[0], ast.Expr) and isinstance(
                    _tree.body[0].value, ast.Constant):
                _named_only.update(range(_tree.body[0].lineno, _tree.body[0].end_lineno + 1))
            for _no, _ln in enumerate(_src.splitlines(), 1):
                if _ln.lstrip().startswith("#") or _no in _named_only:
                    continue
                if not any(_a in _ln for _a in _aliases):
                    continue
                if _re.match(r"\s*%s\s*=" % _SPAN_SOURCE_NAME, _ln):
                    continue          # the definition itself
                if _re.match(r"\s*(from|import)\s", _ln.lstrip()):
                    continue          # the import that binds the alias
                _use_sites.append(f"{_tag}:{_ln.strip()[:60]}")
        assert resolved >= 1, (
            "no local name was found carrying _VERB_MSG_BODY — the carrier "
            "construction changed shape and this test is now vacuous. Re-point "
            "it at the new shape; do not delete it."
        )
        # Named carriers PLUS unbound carrier expressions must account for every
        # use site. The unbound term is derived, not a constant: it was formerly
        # a hard-coded `- 1` standing for the one inline `re.sub(r"..." +
        # _VERB_MSG_BODY, ...)`, which silently granted a second inline carrier
        # the same exemption. Both kinds are now checked for terminality, so
        # both are counted.
        assert len(carriers) + unbound >= len(_use_sites), (
            f"resolver found {len(carriers)} named carrier(s) + {unbound} "
            f"unbound carrier expression(s) but the source has "
            f"{len(_use_sites)} non-comment _VERB_MSG_BODY use site(s). A use "
            f"site the resolver cannot see is a carrier this pin does not "
            f"check. Re-point the resolver; do not lower this bound."
        )

        assert not offenders, (
            f"{len(offenders)} carrier(s) require something AFTER the span: "
            f"{offenders}. The span is exponentially backtrackable (measured "
            f"~2x per input character); it is safe ONLY because nothing after "
            f"it can fail, so the match always succeeds and the engine never "
            f"backtracks into it. Appending a required element re-arms that "
            f"explosion on ordinary input. Put the required element BEFORE the "
            f"span, or bound the span so it cannot backtrack ambiguously."
        )

    # ---- 7.F — the safe-shape negative control, and its non-vacuity twin. ----

# =============================================================================
# #933 REMEDIATION (PR #1000 finding M1) — D3 ladder ReDoS perf bound.
#
# The force-push ladder arm in extract_context (merge_guard_post.py) carried a
# nested-greedy shape `push\b(?:.*\bto\b)?.*\b(?:main|master)\b` — two `.*`
# runs separated by an optional `\bto\b` group — which catastrophically
# backtracks on an input full of "to" tokens that never reaches main/master.
# The fix replaces it with a single lazy `push\b.*?\b(?:main|master)\b` (and
# the sibling `direct\s+push.*?...`), eliminating the ambiguous nested
# quantifier so the match is linear.
# =============================================================================


# (removed class TestD3LadderReDoSPerfBound — superseded by the command-anchored
#  bidirectional suite in test_merge_guard_auth_symmetry.py;
#  it exercised the dropped prose classifier extract_context.)
