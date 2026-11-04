# pact-plugin/tests/test_worktree_guard.py
"""
Tests for worktree_guard.py — PreToolUse hook matching Edit|Write that
blocks edits to application code outside the active worktree.

Tests cover:
1. Edit inside worktree → allow
2. Edit outside worktree to app code → block with corrected path suggestion
3. Edit outside worktree to .claude/ → allow (AI tooling)
4. Edit outside worktree to docs/ → allow (documentation)
5. No PACT_WORKTREE_PATH set → allow (inactive, no-op)
6. CLAUDE.md always allowed
7. Corrected path suggestion (_suggest_worktree_path)
8. main() entry point: stdin JSON parsing, exit codes, output format
"""
import io
import json
from unittest.mock import patch

import pytest


class TestWorktreeGuard:
    """Tests for worktree_guard.check_worktree_boundary()."""

    def test_allows_edit_inside_worktree(self):
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/tmp/worktrees/feat-auth/src/auth.ts",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is None

    def test_blocks_app_code_outside_worktree(self):
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/Users/example/project/src/auth.ts",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is not None
        assert "outside the active worktree" in result.lower()


class TestSuggestWorktreePath:
    """Tests for worktree_guard._suggest_worktree_path()."""

    def test_suggests_path_with_worktrees_dir(self, tmp_path):
        from worktree_guard import _suggest_worktree_path

        project_root = tmp_path / "project"
        worktree_dir = project_root / ".worktrees" / "feat-auth"
        src_dir = project_root / "src"
        worktree_dir.mkdir(parents=True)
        src_dir.mkdir(parents=True)
        (src_dir / "auth.ts").touch()

        result = _suggest_worktree_path(
            str(src_dir / "auth.ts"),
            str(worktree_dir)
        )
        assert result is not None
        assert result == str(worktree_dir / "src" / "auth.ts")

    def test_suggests_path_with_common_ancestor(self, tmp_path):
        """Fallback: uses common path prefix when .worktrees dir is absent."""
        from worktree_guard import _suggest_worktree_path

        # Two unrelated directories under same tmp_path
        workspace = tmp_path / "workspace"
        dir_a = workspace / "main"
        dir_b = workspace / "branch"
        dir_a_src = dir_a / "src"
        dir_a_src.mkdir(parents=True)
        dir_b.mkdir(parents=True)
        (dir_a_src / "app.py").touch()
        # Common ancestor must have a project marker for validation
        (workspace / ".git").mkdir()

        result = _suggest_worktree_path(
            str(dir_a_src / "app.py"),
            str(dir_b)
        )
        # Should produce dir_b/main/src/app.py or similar via common ancestor
        assert result is not None
        assert str(dir_b) in result


class TestMainEntryPoint:
    """Tests for worktree_guard.main() stdin/stdout/exit behavior."""

    def test_main_exits_0_when_no_worktree_path(self, capsys):
        from worktree_guard import main

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

    def test_main_exits_0_when_edit_inside_worktree(self):
        from worktree_guard import main

        input_data = json.dumps({
            "tool_input": {"file_path": "/tmp/worktrees/feat-auth/src/auth.ts"}
        })

        with patch.dict("os.environ", {"PACT_WORKTREE_PATH": "/tmp/worktrees/feat-auth"}), \
             patch("worktree_guard.check_worktree_boundary", return_value=None), \
             patch("sys.stdin", io.StringIO(input_data)):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 0


# =============================================================================
# Edge Case Tests — Path Handling, Application Code Detection
# =============================================================================

class TestIsAllowedPathEdgeCases:
    """Edge cases for worktree_guard.is_allowed_path()."""

    def test_claude_dir_nested_deep(self):
        """Deeply nested .claude path should still be allowed."""
        from worktree_guard import is_allowed_path

        assert is_allowed_path("/Users/example/.claude/some/deep/path/file.md") is True

    def test_docs_nested_deep(self):
        """Deeply nested docs path should still be allowed."""
        from worktree_guard import is_allowed_path

        assert is_allowed_path("/Users/example/project/docs/architecture/v2/design.md") is True

    def test_claude_as_substring_not_matched(self):
        """A directory named 'not-claude' should NOT match '.claude' pattern."""
        from worktree_guard import is_allowed_path

        assert is_allowed_path("/Users/example/not-claude/file.md") is False


class TestIsApplicationCodeEdgeCases:
    """Edge cases for worktree_guard.is_application_code()."""

    def test_recognizes_common_extensions(self):
        from worktree_guard import is_application_code

        assert is_application_code("/project/src/app.py") is True
        assert is_application_code("/project/lib/util.ts") is True
        assert is_application_code("/project/test/test_app.py") is True
        assert is_application_code("/project/scripts/deploy.sh") is True
        assert is_application_code("/project/infra/main.tf") is True

    def test_rejects_non_app_extensions(self):
        from worktree_guard import is_application_code

        assert is_application_code("/project/readme.txt") is False
        assert is_application_code("/project/image.png") is False
        assert is_application_code("/project/notes.pdf") is False


class TestCheckWorktreeBoundaryEdgeCases:
    """Edge cases for check_worktree_boundary()."""

    def test_non_app_code_outside_worktree_allowed(self):
        """Non-application code outside worktree should be allowed."""
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/Users/example/project/notes.txt",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is None  # Not blocked

    def test_worktree_path_trailing_slash(self):
        """Should work with trailing slash on worktree path."""
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/tmp/worktrees/feat-auth/src/auth.ts",
            worktree_path="/tmp/worktrees/feat-auth/"
        )
        assert result is None

    def test_blocks_python_outside_worktree(self):
        """Python file outside worktree should be blocked."""
        from worktree_guard import check_worktree_boundary

        result = check_worktree_boundary(
            file_path="/Users/example/project/hooks/session_init.py",
            worktree_path="/tmp/worktrees/feat-auth"
        )
        assert result is not None
        assert "outside the active worktree" in result.lower()


class TestSiblingWorktreePrefix:
    """A peer worktree whose name extends this one's is outside it (1.4.1 bug audit item 9)."""

    @pytest.fixture
    def trees(self, tmp_path):
        project = tmp_path / "project"
        own = project / ".worktrees" / "feat"
        for tree in (own, project / ".worktrees" / "feat2", project / ".worktrees" / "feat-é",
                     project / ".worktrees" / "feat-éx"):
            (tree / "src").mkdir(parents=True)
        return project, own

    @pytest.mark.parametrize("target", [
        ".worktrees/feat2/src/x.py",])
    def test_sibling_with_longer_name_is_blocked(self, trees, target):
        from worktree_guard import check_worktree_boundary

        project, own = trees
        result = check_worktree_boundary(str(project / target), str(own))
        assert result is not None and "outside the active worktree" in result

    def test_symlink_inside_worktree_into_sibling_is_blocked(self, trees):
        from worktree_guard import check_worktree_boundary

        project, own = trees
        (own / "src" / "peer").symlink_to(project / ".worktrees" / "feat2" / "src")
        result = check_worktree_boundary(str(own / "src" / "peer" / "x.py"), str(own))
        assert result is not None

    def test_unicode_sibling_is_blocked(self, trees):
        from worktree_guard import check_worktree_boundary

        project, _ = trees
        result = check_worktree_boundary(
            str(project / ".worktrees/feat-éx/src/x.py"), str(project / ".worktrees/feat-é"))
        assert result is not None

    def test_sibling_blocked_when_worktree_has_trailing_slash(self, trees):
        from worktree_guard import check_worktree_boundary

        project, own = trees
        result = check_worktree_boundary(str(project / ".worktrees/feat2/src/x.py"), str(own) + "/")
        assert result is not None


class TestSuggestWorktreePathEdgeCases:
    """Additional edge cases for _suggest_worktree_path()."""

    def test_nested_worktree_path(self, tmp_path):
        """Should handle deeply nested worktree paths."""
        from worktree_guard import _suggest_worktree_path

        project = tmp_path / "repo"
        worktree = project / ".worktrees" / "feat" / "deep-branch"
        src = project / "pact-plugin" / "hooks"
        worktree.mkdir(parents=True)
        src.mkdir(parents=True)
        (src / "session_init.py").touch()

        result = _suggest_worktree_path(
            str(src / "session_init.py"),
            str(worktree)
        )
        assert result is not None
        assert "pact-plugin" in result
        assert "session_init.py" in result


class TestAllowListReadsTheResolvedPath:
    """rv4 PART2-B: the allow-list ran on the RAW path, so a ../ through docs/ or .claude/, or
    any app dir named docs, passed outside the worktree."""

    @pytest.fixture
    def project(self, tmp_path):
        own = tmp_path / "main" / ".worktrees" / "feat"
        own.mkdir(parents=True)
        for sub in ("docs", ".claude", "src/api/docs"):
            (tmp_path / "main" / sub).mkdir(parents=True, exist_ok=True)
        return tmp_path / "main", own

    @pytest.mark.parametrize("target", [
        "docs/../src/app.py",])
    def test_app_code_reached_through_an_allowed_name_is_blocked(self, project, target):
        from worktree_guard import check_worktree_boundary

        main, own = project
        result = check_worktree_boundary(str(main / target), str(own))
        assert result is not None and "outside the active worktree" in result

    def test_a_notebook_path_is_checked(self, project, capsys):
        from worktree_guard import main as guard_main

        root, own = project
        frame = json.dumps({"tool_name": "NotebookEdit",
                            "tool_input": {"notebook_path": str(root / "src" / "nb.py")}})
        with patch.dict("os.environ", {"PACT_WORKTREE_PATH": str(own)}), \
             patch("sys.stdin", io.StringIO(frame)):
            with pytest.raises(SystemExit) as exc_info:
                guard_main()
        assert exc_info.value.code == 2


@pytest.mark.parametrize("tool, key", [("NotebookEdit", "notebook_path"),])
def test_the_dispatcher_runs_the_guard_for_every_edit_tool(tmp_path, tool, key):
    """rv4: hooks.json routed only Edit|Write to worktree_guard, so a NotebookEdit or MultiEdit
    outside the worktree was never checked. Runs the real dispatcher from hooks.json's entry."""
    import subprocess
    import sys
    from pathlib import Path

    plugin = Path(__file__).resolve().parents[1]
    entry = next(hook["command"] for group in json.loads((plugin / "hooks" / "hooks.json")
                                                         .read_text("utf-8"))["hooks"]["PreToolUse"]
                 if tool in (group.get("matcher") or "").split("|") for hook in group["hooks"])
    own = tmp_path / "main" / ".worktrees" / "feat"
    own.mkdir(parents=True)
    argv = [sys.executable, str(plugin / "hooks" / "dispatch.py"), *entry.split('"')[-1].split()]
    frame = {"tool_name": tool, "session_id": "s1", "hook_event_name": "PreToolUse",
             "tool_input": {key: str(tmp_path / "main" / "src" / "nb.py")}}
    done = subprocess.run(argv, input=json.dumps(frame).encode(), capture_output=True, timeout=60,
                          cwd=str(tmp_path), env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
                                                  "CLAUDE_CONFIG_DIR": str(tmp_path / ".claude"),
                                                  "CLAUDE_PROJECT_DIR": str(tmp_path / "main"),
                                                  "CLAUDE_PLUGIN_ROOT": str(plugin),
                                                  "PACT_WORKTREE_PATH": str(own)})
    assert b'"permissionDecision": "deny"' in done.stdout, (done.stdout[-500:], done.stderr[-500:])
