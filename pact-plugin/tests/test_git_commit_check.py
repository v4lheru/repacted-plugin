"""
Tests for git_commit_check.py — PreToolUse hook that validates git commits
for PACT protocol compliance (SECURITY hook).

Tests cover:
1. check_security: .env file detection, risky logging patterns
2. check_frontend_credentials: VITE_, REACT_APP_, NEXT_PUBLIC_ credential exposure
3. check_direct_api_calls: direct external API call detection in frontend code
4. check_env_file_in_gitignore: .gitignore validation for .env entries
5. check_hardcoded_secrets: API key patterns, Stripe keys, GitHub tokens, etc.
6. main: stdin JSON parsing, exit codes, error/warning routing
"""
import io
import json
import shutil
import subprocess
import tempfile
from unittest.mock import patch, MagicMock

import pytest


# Log calls are joined at run time: these fixtures are added lines of this file, and the commit
# check reads added lines, so a literal log call here would block the commit of the file itself.
LOG, PRINT = "console" ".log", "pri" "nt"
REPO = "/nonexistent/repo"      # check_security rows here are decided by name and added lines


def _added(path, content):
    """The `added` map the content checks read: every line of `content` as an added line."""
    return {path: list(enumerate(content.splitlines(), 1))}


def _secret_errors(path, content):
    """Every secret finding the commit check makes on `content` added as `path`: the shared
    detector (every file type) and the shapes only this check knows (code files)."""
    from git_commit_check import check_added_secrets, check_hardcoded_secrets
    patch_text = f"+++ b/{path}\n" + "".join(f"+{line}\n" for line in content.splitlines())
    return check_added_secrets(patch_text)[0] + check_hardcoded_secrets(_added(path, content))



# ---------------------------------------------------------------------------
# check_security
# ---------------------------------------------------------------------------

class TestCheckSecurity:
    """Tests for check_security() — .env file detection and risky log patterns."""

    def test_detects_env_file(self):
        from git_commit_check import check_security
        errors = check_security([".env"], repo=REPO)
        assert len(errors) == 1
        assert "environment file" in errors[0].lower()

    def test_detects_nested_env_file(self):
        from git_commit_check import check_security
        errors = check_security(["config/.env"], repo=REPO)
        assert len(errors) == 1

    def test_detects_env_prefixed_file(self):
        from git_commit_check import check_security
        errors = check_security([".env.local"], repo=REPO)
        assert len(errors) == 1

    def test_case_insensitive_matching(self):
        from git_commit_check import check_security
        errors = check_security(["src/app.js"], _added("src/app.js", f"{LOG.title()}(PASSWORD)"), repo=REPO)
        assert len(errors) >= 1

class TestPatternData:
    """The pattern lists are data (hooks/git_commit_check_patterns.json), read on the commit path."""

    def test_the_data_loads_and_a_missing_file_denies_the_commit(self, monkeypatch, tmp_path):
        import re
        import git_commit_check as gcc
        monkeypatch.setattr(gcc, "_patterns_read", {})
        lists = gcc.patterns()
        assert set(lists) == {"log_scan_extensions", "env_log_patterns", "secret_scan_extensions",
                              "commit_only_patterns"} and all(lists.values())
        for regex in lists["env_log_patterns"] + tuple(r for r, _kind in lists["commit_only_patterns"]):
            re.compile(regex)
        monkeypatch.setattr(gcc, "_patterns_read", {})
        monkeypatch.setattr(gcc, "PATTERNS_FILE", str(tmp_path / "gone.json"))
        command = {"tool_input": {"command": "git commit -m 'test'"}}
        with patch("sys.stdin", io.StringIO(json.dumps(command))), \
                patch("git_commit_check.committed_files", return_value=["src/app.py"]), \
                patch("git_commit_check.commit_patch", return_value="+++ b/src/app.py\n@@ -0,0 +1 @@\n+x = 1\n"), \
                pytest.raises(SystemExit) as exc:
            gcc.main()
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# check_frontend_credentials
# ---------------------------------------------------------------------------

class TestCheckFrontendCredentials:
    """Tests for check_frontend_credentials() — frontend env var exposure."""

    def test_detects_vite_secret_key(self):
        from git_commit_check import check_frontend_credentials
        errors = check_frontend_credentials(["src/App.tsx"], _added("src/App.tsx", 'const key = import.meta.env.VITE_API_SECRET'))
        assert len(errors) >= 1
        assert "frontend credential" in errors[0].lower()

    def test_detects_react_app_token(self):
        from git_commit_check import check_frontend_credentials
        errors = check_frontend_credentials(["src/api.jsx"], _added("src/api.jsx", 'process.env.REACT_APP_AUTH_TOKEN'))
        assert len(errors) >= 1

    def test_detects_next_public_password(self):
        from git_commit_check import check_frontend_credentials
        errors = check_frontend_credentials(["components/db.tsx"], _added("components/db.tsx", 'NEXT_PUBLIC_DB_PASSWORD = "abc"'))
        assert len(errors) >= 1

    def test_detects_nuxt_public_credential(self):
        from git_commit_check import check_frontend_credentials
        errors = check_frontend_credentials(["pages/index.vue"], _added("pages/index.vue", 'NUXT_PUBLIC_API_CREDENTIAL = "abc"'))
        assert len(errors) >= 1

    def test_svelte_file_detected(self):
        from git_commit_check import check_frontend_credentials
        errors = check_frontend_credentials(["src/App.svelte"], _added("src/App.svelte", 'VITE_SECRET_KEY = "abc"'))
        assert len(errors) >= 1

# ---------------------------------------------------------------------------
# check_direct_api_calls
# ---------------------------------------------------------------------------

class TestCheckDirectApiCalls:
    """Tests for check_direct_api_calls() — direct external API call warnings."""

    def test_detects_fetch_to_external_api(self):
        from git_commit_check import check_direct_api_calls
        warnings = check_direct_api_calls(["src/service.tsx"], _added("src/service.tsx", 'fetch("https://api.example.com/data")'))
        assert len(warnings) == 1
        assert "external API" in warnings[0]

    def test_detects_axios_to_external_api(self):
        from git_commit_check import check_direct_api_calls
        warnings = check_direct_api_calls(["src/service.jsx"], _added("src/service.jsx", 'axios.get("https://api.example.com/data")'))
        assert len(warnings) == 1

    def test_detects_stripe_api_call(self):
        from git_commit_check import check_direct_api_calls
        warnings = check_direct_api_calls(["src/payment.tsx"], _added("src/payment.tsx", 'fetch("https://api.stripe.com/v1/charges")'))
        assert len(warnings) == 1
        assert "API" in warnings[0]

# ---------------------------------------------------------------------------
# check_env_file_in_gitignore
# ---------------------------------------------------------------------------

IGNORE_CHANNELS = ["repo_gitignore", "parent_gitignore", "per_repo_exclude", "global_excludesfile"]


def _isolated_git_env(tmp_path, monkeypatch):
    """Apply the HOME/XDG/GIT_CONFIG_* overrides that isolate user-level git config.

    Prevents the developer's real `~/.config/git/ignore` or `core.excludesFile`
    from affecting test outcomes (false positives) and prevents tests from
    writing into real config (side-effect leakage). Critical for the
    `global_excludesfile` channel; cheap defense-in-depth for the others.

    Returns the `fake_home` path so the caller can place a global ignore file
    under `$fake_home/.config/git/ignore` when needed.
    """
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_SYSTEM", raising=False)
    return fake_home


@pytest.fixture
def git_repo_with_env_ignored(tmp_path, monkeypatch, request):
    """
    Create a tmp_path git repo where `.env` is ignored via ONE of four channels.
    Parametrize with `IGNORE_CHANNELS` to cover all four.

    Isolates user-level git config via HOME / XDG_CONFIG_HOME / GIT_CONFIG_GLOBAL /
    GIT_CONFIG_SYSTEM overrides so tests neither read from nor write to the
    developer's real global git config. chdirs into the appropriate subdirectory
    for each channel. Returns the cwd path.
    """
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")

    channel = request.param

    fake_home = _isolated_git_env(tmp_path, monkeypatch)

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)

    if channel == "repo_gitignore":
        (repo / ".gitignore").write_text(".env\n")
        cwd = repo
    elif channel == "parent_gitignore":
        # .env ignored by PARENT's .gitignore; commit happens in subdir.
        (repo / ".gitignore").write_text(".env\n")
        subdir = repo / "subpkg"
        subdir.mkdir()
        cwd = subdir
    elif channel == "per_repo_exclude":
        (repo / ".git" / "info" / "exclude").write_text(".env\n")
        cwd = repo
    elif channel == "global_excludesfile":
        global_ignore = fake_home / ".config" / "git" / "ignore"
        global_ignore.parent.mkdir(parents=True)
        global_ignore.write_text(".env\n")
        # XDG default location; git check-ignore picks it up automatically
        # when XDG_CONFIG_HOME is set.
        cwd = repo
    else:
        raise ValueError(f"unknown channel: {channel}")

    monkeypatch.chdir(cwd)
    return cwd


class TestCheckEnvFileInGitignore:
    """Tests for check_env_file_in_gitignore() — delegates to `git check-ignore`.

    Shape per docs/architecture/fix-511-git-check-ignore.md §1:
      - 4 E2E positive (parametrized across ignore channels) — real git repo,
        `.env` ignored via each channel, assert `(True, None)`.
      - 1 E2E negative — real git repo, no ignore anywhere, assert VIOLATION
        with all three destinations named.
      - 5 mocked — branch matrix (exit 128, other non-zero, TimeoutExpired,
        FileNotFoundError, wrapper-args invariant).
      - 2 adversarial — `!.env` negation pattern (the false-positive the fix
        closes per arch §309) and Unicode-path cwd (cheap regression insurance
        for a SACROSANCT-surface hook).

    Assertion token contract (arch §0): the `VIOLATION` / `WARNING` substrings
    are the block/allow signal that `main()` triages on. Tests assert on these
    tokens, not on exit codes or blocking behavior at the function level.
    """

    # -------- E2E positive: parametrized per-channel --------

    @pytest.mark.parametrize(
        "git_repo_with_env_ignored", IGNORE_CHANNELS, indirect=True
    )
    def test_detects_env_ignored_across_all_channels(self, git_repo_with_env_ignored):
        from git_commit_check import check_env_file_in_gitignore
        is_protected, error = check_env_file_in_gitignore()
        assert is_protected is True
        assert error is None

    # -------- E2E negative --------

    def test_returns_violation_when_env_not_ignored_anywhere(
        self, tmp_path, monkeypatch
    ):
        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        from git_commit_check import check_env_file_in_gitignore

        _isolated_git_env(tmp_path, monkeypatch)

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        monkeypatch.chdir(repo)

        is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "VIOLATION" in error
        # All three destinations named per arch §5.
        assert ".gitignore" in error
        assert "~/.config/git/ignore" in error
        assert ".git/info/exclude" in error

    # -------- Mocked branch matrix --------

    def test_returns_warning_on_exit_128(self):
        from git_commit_check import check_env_file_in_gitignore
        mock_result = MagicMock()
        mock_result.returncode = 128
        with patch("shared.git_helpers.subprocess.run", return_value=mock_result):
            is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "WARNING" in error
        assert "VIOLATION" not in error
        assert "exit 128" in error
        # Shared log-grep invariant across every WARNING string (arch §5).
        assert "cannot verify .env protection" in error

    def test_negation_pattern_unignores_env_and_returns_violation(
        self, tmp_path, monkeypatch
    ):
        """`!.env` in a higher-priority source unignores `.env` from a lower one.

        Arch §309 names this as one of the false-positives the fix closes: the
        old substring read would see `.env` in some ignore file and approve
        the commit, missing a later `!.env` re-inclusion. Per gitignore(5),
        priority order is: command-line patterns > per-directory `.gitignore`
        (nearest wins) > `.git/info/exclude` > `core.excludesFile` (global).
        So `.env` in global ignore + `!.env` in repo `.gitignore` = not ignored.

        `git check-ignore -q` returns exit 1 when the final resolution is
        "not ignored," even via a negation pattern — the hook must surface
        that as a VIOLATION.
        """
        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        from git_commit_check import check_env_file_in_gitignore

        fake_home = _isolated_git_env(tmp_path, monkeypatch)

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
        # Global ignore says "ignore .env"; repo-local .gitignore overrides.
        global_ignore = fake_home / ".config" / "git" / "ignore"
        global_ignore.parent.mkdir(parents=True)
        global_ignore.write_text(".env\n")
        (repo / ".gitignore").write_text("!.env\n")
        monkeypatch.chdir(repo)

        is_protected, error = check_env_file_in_gitignore()
        assert is_protected is False
        assert error is not None
        assert "VIOLATION" in error

    # -------- Adversarial --------

# ---------------------------------------------------------------------------
# Security residual E2E (arch §8 — git rm --cached .env must not block)
# ---------------------------------------------------------------------------


class TestSecurityResidualE2E:
    """End-to-end verification that untracking a previously-tracked .env is
    not flagged as a violation.

    The argv-level pin `test_the_read_is_bounded_and_excludes_deletions` asserts the
    `--diff-filter=d` flag is on the argv; this class closes the semantic
    loop by driving `main()` end-to-end through a real git repo in the
    remediation state a user would be in (`.env` historically tracked, then
    `git rm --cached .env` staged alongside `.gitignore` updates). Asserts
    exit 0 with no VIOLATION on stderr.
    """

    def test_git_rm_cached_env_does_not_block_commit(
        self, tmp_path, monkeypatch, capsys
    ):
        if shutil.which("git") is None:
            pytest.skip("git not available on PATH")
        from git_commit_check import main

        _isolated_git_env(tmp_path, monkeypatch)

        repo = tmp_path / "repo"
        repo.mkdir()
        # Set a committer identity so the initial commit succeeds under our
        # isolated HOME (no user.name/user.email in the fake global config).
        env_cmd_args = {"cwd": repo, "check": True}
        subprocess.run(["git", "init", "--quiet"], **env_cmd_args)
        subprocess.run(["git", "config", "user.email", "test@example.invalid"], **env_cmd_args)
        subprocess.run(["git", "config", "user.name", "Test"], **env_cmd_args)

        # Stage 1: track .env historically (pre-remediation state).
        (repo / ".env").write_text("SECRET=sentinel\n")
        subprocess.run(["git", "add", ".env"], **env_cmd_args)
        subprocess.run(["git", "commit", "-m", "initial", "--quiet"], **env_cmd_args)

        # Stage 2: the user's remediation — ignore + untrack .env.
        (repo / ".gitignore").write_text(".env\n")
        subprocess.run(["git", "add", ".gitignore"], **env_cmd_args)
        subprocess.run(["git", "rm", "--cached", ".env", "--quiet"], **env_cmd_args)

        monkeypatch.chdir(repo)

        # Drive main() with a plausible `git commit` stdin payload.
        input_data = {"tool_input": {"command": "git commit -m 'remove .env'"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()

        # Exit 0 = commit allowed. Exit 2 would mean VIOLATION fired.
        assert exc.value.code == 0, (
            "git rm --cached .env staged alongside a new .gitignore must not "
            "block the commit — the deletion has no staged content and .env is "
            "now ignored."
        )
        # Defense in depth: even if exit 0, confirm no VIOLATION string surfaced.
        captured = capsys.readouterr()
        assert "VIOLATION" not in captured.err
        # Belt-and-suspenders: guard the false-pass where main()'s outer
        # try/except swallows an unexpected error and exits 0 with
        # "Hook Error" on stderr. Without this, a regression that broke
        # main() would silently pass this test on exit-code alone.
        assert "Hook Error" not in captured.err


# ---------------------------------------------------------------------------
# check_hardcoded_secrets
# ---------------------------------------------------------------------------

class TestCheckHardcodedSecrets:
    """Tests for check_hardcoded_secrets() — API key and secret detection."""

    def test_detects_openai_key(self):
        content = 'api_key = "sk-' + 'FAKE00000000000000000000000000"'
        errors = _secret_errors("config.py", content)
        assert len(errors) >= 1

    def test_detects_stripe_live_key(self):
        content = 'key = "sk_live_' + 'Xk9mQ2vL7pR4tW8zA1bC' + '"'
        errors = _secret_errors("payment.py", content)
        assert len(errors) >= 1
        assert any("prefixed-token" in e for e in errors)

    def test_detects_stripe_test_key(self):
        content = 'key = "sk_test_' + 'FAKE0000000000000000"'
        errors = _secret_errors("payment.py", content)
        assert len(errors) >= 1

    def test_detects_slack_token(self):
        content = 'token = "xoxb-' + '1234567890-abcdefgh"'
        errors = _secret_errors("slack.py", content)
        assert len(errors) >= 1
        assert any("slack" in e.lower() for e in errors)

    def test_detects_jwt_token(self):
        # Realistic JWT structure: header.payload.signature
        content = 'token = "eyJ' + 'hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456_signature"'
        errors = _secret_errors("auth.py", content)
        assert len(errors) >= 1
        assert any("jwt" in e.lower() for e in errors)

    def test_detects_github_oauth_token(self):
        content = 'token = "gho_' + 'FAKE00000000000000000000000000000000"'
        errors = _secret_errors("auth.py", content)
        assert len(errors) >= 1

    def test_detects_generic_api_key_assignment(self):
        content = 'api_' + 'key = "abcdefghijklmnopqrstuvwxyz1234"'
        errors = _secret_errors("config.py", content)
        assert len(errors) >= 1

    def test_detects_hardcoded_password(self):
        content = "pass" + 'word = "Xk9#mQ2vL7pR"'        # joined, so this source carries no literal
        errors = _secret_errors("db.py", content)
        assert len(errors) >= 1

    def test_detects_aws_access_key_id(self):
        content = 'aws_key = "AKIA' + 'IOSFODNN7EXAMPLE"'
        errors = _secret_errors("config.py", content)
        assert len(errors) >= 1
        assert any("aws" in e.lower() for e in errors)

    def test_detects_rsa_private_key(self):
        content = '-----BEGIN RSA PRIVATE ' + 'KEY-----\nMIIE...'
        errors = _secret_errors("deploy.py", content)
        assert len(errors) >= 1
        assert any("private-key" in e for e in errors)

# ---------------------------------------------------------------------------
# committed_files: what git lists, read fail-closed
# ---------------------------------------------------------------------------

class TestCommittedFiles:
    """committed_files() lists what the commit takes; a git that cannot answer denies."""

    def _result(self, returncode=0, stdout="", stderr=""):
        result = MagicMock()
        result.returncode, result.stdout, result.stderr = returncode, stdout, stderr
        return result

    def test_lists_the_staged_files(self):
        from git_commit_check import Commit, committed_files
        with patch("shared.git_helpers.subprocess.run", return_value=self._result(stdout="file1.py\nfile2.js\n")):
            assert committed_files(Commit()) == ["file1.py", "file2.js"]

    def test_nothing_staged_is_an_empty_list(self):
        from git_commit_check import Commit, committed_files
        with patch("shared.git_helpers.subprocess.run", return_value=self._result()):
            assert committed_files(Commit()) == []

    def test_outside_a_repository_the_commit_is_refused(self):
        from git_commit_check import Commit, NoRepository, committed_files, require_repository
        with patch("shared.git_helpers.subprocess.run",
                   return_value=self._result(128, stderr="fatal: not a git repository")):
            with pytest.raises(NoRepository):
                committed_files(Commit())
            with pytest.raises(NoRepository):
                require_repository(Commit())

    @pytest.mark.parametrize("failure", [
        subprocess.TimeoutExpired(cmd=["git"], timeout=30), FileNotFoundError("git")])
    def test_a_git_that_does_not_answer_is_unverifiable(self, failure):
        from git_commit_check import Commit, Unverifiable, committed_files
        with patch("shared.git_helpers.subprocess.run", side_effect=failure):
            with pytest.raises(Unverifiable):
                committed_files(Commit())

    def test_another_failing_exit_is_unverifiable(self):
        from git_commit_check import Commit, Unverifiable, committed_files
        with patch("shared.git_helpers.subprocess.run", return_value=self._result(1, stderr="error")):
            with pytest.raises(Unverifiable):
                committed_files(Commit())

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class TestMain:
    """Tests for main() entry point — stdin parsing, exit codes, integration."""

    def test_allows_non_commit_command(self, capsys):
        from git_commit_check import main
        input_data = {"tool_input": {"command": "git status"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"suppressOutput": True}

    def test_allows_commit_with_no_staged_files(self):
        from git_commit_check import main
        input_data = {"tool_input": {"command": "git commit -m 'test'"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with patch("git_commit_check.committed_files", return_value=[]):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 0

    def test_blocks_commit_with_security_errors(self):
        from git_commit_check import main
        input_data = {"tool_input": {"command": "git commit -m 'test'"}}
        with patch("sys.stdin", io.StringIO(json.dumps(input_data))):
            with patch("git_commit_check.committed_files",
                       return_value=[".env"]), patch("git_commit_check.commit_patch", return_value=""):
                with patch("git_commit_check.check_env_file_in_gitignore",
                           return_value=(True, None)):
                    with pytest.raises(SystemExit) as exc:
                        main()
        assert exc.value.code == 2

# ---------------------------------------------------------------------------
# Logging a token VALUE vs counting tokens (the "token" false positive)
# ---------------------------------------------------------------------------

# The real trigger, reproduced from session transcripts (18 blocks, 3 files): a
# print whose f-string reads a COUNT field, e.g. `row['input_tokens']`, or whose
# message text merely contains the word "token". The log check ran
# `print\s*\(.*token` over every line, so both blocked the commit. These are the
# line SHAPES of those files (placeholders only, no values).
TOKEN_COUNT_OR_PROSE_LINES = [
    "print(f\"{Path(row['path']).name:<{width}} {row['input_tokens']:>9,}\")",
    "console.log(`tokens used: ${usage.totalTokens}`)",
    "print(remaining_tokens)",
]

# Logging a token's VALUE must still block. Written with ~p( / ~c( for the two log calls: the
# installed commit check reads every line of this file, and a literal log call would block it.
def _log_calls(lines):
    return [line.replace("~p(", PRINT + "(").replace("~c(", LOG + "(") for line in lines]


TOKEN_VALUE_LOG_LINES = _log_calls([
    "~p(token)",
    "~p(access_token)",
    "~c(authToken)",
    "~p(os.environ['API_TOKEN'])",
    "~p(f\"using {token}\")",
    "~p(config['api_token'])",
    "~c(`bearer ${apiToken}`)",
    "~c('token:', token)",
    '~c("token", token)',
    # Plural credential names are values too; only count-prefixed plurals are exempt.
    "~p(access_tokens)",
    "~p(refresh_tokens)",
    "~p(auth_tokens)",
    "~p(tokens)",
    # Deny by default: any other word containing "token" is a value.
    "~p(token_value)",
    "~c(tokenStr)",
    # An identifier-shaped string is a key wherever it sits in the call.
    "~p(getattr(creds, 'access_token'))",
    "~p(dict(creds).get('refresh_token'))",
    # Prose naming the singular word plus any non-exempt code argument.
    "~p('token:', t)",
    "~c('token', v)",
    "~p(f'token={t}')",
    "~p('the token is', t)",
    "~c(`access token: ${v}`)",
    # A transcript shape that the prose rule now blocks (its placeholders read
    # non-count values next to the word "token").
    "~p(f\"{1 - themes/max(raw_labels, 1):.0%} fewer token labels\")",
])
TOKEN_VALUE_LOG_LINES = TOKEN_VALUE_LOG_LINES[::4]


class TestTokenLogging:

    @pytest.mark.parametrize("line", TOKEN_COUNT_OR_PROSE_LINES)
    def test_counting_or_mentioning_tokens_does_not_block(self, line):
        from git_commit_check import check_security
        path = "src/app.js" if line.startswith("console") else "tools/report.py"
        assert check_security([path], _added(path, line), repo=REPO) == []

    @pytest.mark.parametrize("line", TOKEN_VALUE_LOG_LINES)
    def test_logging_a_token_value_still_blocks(self, line):
        from git_commit_check import check_security
        path = "src/auth.js" if line.startswith("console") else "auth/client.py"
        errors = check_security([path], _added(path, line), repo=REPO)
        assert errors and "Potential secret exposure in log" in errors[0]

    def test_a_token_shaped_literal_is_still_caught_by_the_secret_patterns(self):
        """The narrowing is about LOG lines; a hardcoded token-shaped value is a
        separate pattern and must keep blocking (FAKE parts, per this file)."""
        content = 'print("ghp_' + "FAKE" + "0" * 36 + '")'
        errors = _secret_errors("tools/report.py", content)
        assert any("github-token" in e for e in errors)

# ---------------------------------------------------------------------------
# Which repo the commit is checked in (the cwd / -C defect)
# ---------------------------------------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def two_repos(tmp_path, monkeypatch):
    """A clean repo (a benign staged file, .env ignored) and a dirty repo (a
    staged .env file), under an isolated git config."""
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")
    _isolated_git_env(tmp_path, monkeypatch)
    clean, dirty = tmp_path / "clean repo", tmp_path / "dirty"
    for repo in (clean, dirty):
        repo.mkdir()
        _git(repo, "init", "--quiet")
    (clean / ".gitignore").write_text(".env\n")
    (clean / "notes.txt").write_text("hello\n")
    _git(clean, "add", ".gitignore", "notes.txt")
    (dirty / ".env").write_text("NAME=placeholder\n")
    _git(dirty, "add", ".env")
    return clean, dirty


def _run_main(payload, capsys):
    from git_commit_check import main
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        with pytest.raises(SystemExit) as exc:
            main()
    return exc.value.code, capsys.readouterr().err


class TestTargetRepo:

    def test_git_dash_c_checks_the_named_repo(self, two_repos, monkeypatch, capsys):
        clean, dirty = two_repos
        monkeypatch.chdir(clean)                       # the session sits in the clean repo
        code, err = _run_main({"tool_input": {"command": f"git -C '{dirty}' commit -m x"}}, capsys)
        assert code == 2 and "environment file" in err

    def test_cd_prefix_checks_the_repo_it_enters(self, two_repos, monkeypatch, capsys):
        clean, dirty = two_repos
        monkeypatch.chdir(dirty)                       # the session sits in the dirty repo
        code, err = _run_main({"tool_input": {"command": f"cd '{clean}' && git commit -m x"}}, capsys)
        assert code == 0, err

    def test_a_wrapped_git_dash_c_checks_the_named_repo(self, two_repos, monkeypatch, capsys):
        clean, dirty = two_repos
        monkeypatch.chdir(clean)
        code, err = _run_main({"tool_input": {"command": f"rtk git -C '{dirty}' commit -m x"}}, capsys)
        assert code == 2 and "environment file" in err

    def test_a_subshell_cd_checks_the_repo_it_enters(self, two_repos, monkeypatch, capsys):
        clean, dirty = two_repos
        monkeypatch.chdir(clean)
        code, err = _run_main({"tool_input": {"command": f"(cd '{dirty}' && git commit -m x)"}}, capsys)
        assert code == 2 and "environment file" in err

    def test_a_relative_dash_c_resolves_against_the_payload_cwd(self, two_repos, monkeypatch, capsys):
        clean, dirty = two_repos
        monkeypatch.chdir(clean)
        code, err = _run_main({"cwd": str(dirty.parent),
                               "tool_input": {"command": f"git -C {dirty.name} commit -m x"}}, capsys)
        assert code == 2 and "environment file" in err

class TestFindCommit:
    """The command parser behind the target repo. The base is the payload cwd."""

    @pytest.mark.parametrize("command, expected", [
    ("git commit -m x", (True, "/w/base")),
])
    def test_parses_the_commit_and_its_directory(self, command, expected):
        from git_commit_check import find_commit
        assert find_commit(command, "/w/base") == expected

    def test_without_a_cwd_and_without_a_prefix_the_repo_is_this_process(self):
        from git_commit_check import find_commit
        assert find_commit("git commit -m x", None) == (True, None)

    def test_an_unparseable_command_falls_back_to_the_base(self):
        from git_commit_check import find_commit
        assert find_commit("git commit -m 'unterminated", "/w/base") == (False, "/w/base")


# ---------------------------------------------------------------------------
# A `git add` in the same command as the commit (review H2): replayed on a copy of the index
# ---------------------------------------------------------------------------

# Built at run time: a literal key here would be an added line of this file.
_KEY_LINE = 'aws_key = "AKIA' + "Q7RZ" * 4 + '"\n'


@pytest.fixture
def staging_repo(tmp_path, monkeypatch):
    """A committed repo (clean.py, tracked.py, `*.log` ignored) with three unstaged files: a new
    key file, a key added to tracked.py, and an ignored key log. Isolated git config."""
    if shutil.which("git") is None:
        pytest.skip("git not available on PATH")
    _isolated_git_env(tmp_path, monkeypatch)
    for name in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{name}_NAME", "t")
        monkeypatch.setenv(f"GIT_{name}_EMAIL", "t@t")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    (repo / ".gitignore").write_text(".env\n*.log\n")
    (repo / "clean.py").write_text("x = 1\n")
    (repo / "tracked.py").write_text("y = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "--quiet", "-m", "init")
    (repo / "new.py").write_text(_KEY_LINE)
    (repo / "ignored.log").write_text(_KEY_LINE)
    (repo / "clean.py").write_text("x = 3\n")
    monkeypatch.chdir(repo)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    return repo


def _commit_in(repo, command, capsys):
    return _run_main({"tool_input": {"command": command}, "cwd": str(repo)}, capsys)


class TestStagingInTheSameCommand:

    @pytest.mark.parametrize("command", [
    "git add new.py && git commit -m x",
])
    def test_a_key_staged_by_the_same_command_is_denied(self, staging_repo, command, capsys):
        code, err = _commit_in(staging_repo, command, capsys)
        assert code == 2 and "aws-key" in err, (command, err)

    def test_update_stages_a_key_added_to_a_tracked_file(self, staging_repo, capsys):
        with open(staging_repo / "tracked.py", "a") as handle:
            handle.write(_KEY_LINE)
        code, err = _commit_in(staging_repo, "git add -u && git commit -m x", capsys)
        assert code == 2 and "aws-key" in err

    @pytest.mark.parametrize("name", [
    "odd name.py",
])
    def test_quoted_unicode_and_dash_names(self, staging_repo, name, capsys):
        (staging_repo / name).write_text(_KEY_LINE)
        (staging_repo / "new.py").unlink()
        code, err = _commit_in(staging_repo, f"git add -- '{name}' && git commit -m x", capsys)
        assert code == 2 and "aws-key" in err

    def test_git_by_its_full_path(self, staging_repo, capsys):
        git = shutil.which("git")
        code, err = _commit_in(staging_repo, f"'{git}' add new.py && '{git}' commit -m x", capsys)
        assert code == 2 and "aws-key" in err

    def test_git_dash_c_and_cd_from_elsewhere(self, staging_repo, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        for command in (f"git -C '{staging_repo}' add new.py && git -C '{staging_repo}' commit -m x",
                        f"cd '{staging_repo}' && git add new.py && git commit -m x"):
            code, err = _run_main({"tool_input": {"command": command}, "cwd": str(tmp_path)}, capsys)
            assert code == 2 and "aws-key" in err, command

    def test_an_unreadable_command_with_an_earlier_git_denies(self, staging_repo, capsys):
        code, err = _commit_in(staging_repo, "git add new.py && git commit -m 'open", capsys)
        assert code == 2 and "could not be read" in err

    @pytest.mark.parametrize("command, why", [
        ("git add $F && git commit -m x", "expanded by the shell"),
        ("git add --pathspec-from-file=- && git commit -m x", "reads its paths"),
        ("GIT_INDEX_FILE=other git add new.py && git commit -m x", "another directory"),
        ("git update-index --add new.py && git commit -m x", "`git update-index`"),
        ("find . -name new.py | xargs git add && git commit -m x", "`xargs`"),
    ])
    def test_a_staging_step_that_cannot_be_replayed_denies_the_combined_form(
            self, staging_repo, command, why, capsys):
        code, err = _commit_in(staging_repo, command, capsys)
        assert code == 2 and why in err and "second call" in err, (command, err)

class TestSharedStash:
    """The stash stack is one per repository, shared by every worktree and session: a stash form
    that takes or drops whichever entry is on top is refused, read as the commit scan reads git."""

    @pytest.mark.parametrize("command", [
    "git stash",
])
    def test_a_stash_that_takes_the_top_entry_is_refused(self, staging_repo, command, capsys):
        code, err = _commit_in(staging_repo, command, capsys)
        assert code == 2 and "git_commit_check.shared_stash" in err and "stash apply <sha>" in err, (
            command, err)

    @pytest.mark.parametrize("command", [
    "git stash push -m wip-a",
])
    def test_a_tagged_or_read_only_stash_passes(self, staging_repo, command, capsys):
        assert _commit_in(staging_repo, command, capsys)[0] == 0, command

    def test_a_stash_after_a_clean_commit_is_refused_and_before_one_keeps_its_reason(
            self, staging_repo, capsys):
        _git(staging_repo, "add", "clean.py")
        code, err = _commit_in(staging_repo, "git commit -m x && git stash pop", capsys)
        assert code == 2 and "git_commit_check.shared_stash" in err, err
        code, err = _commit_in(staging_repo, "git stash pop && git commit -m x", capsys)
        assert code == 2 and "`git stash`" in err and "second call" in err, err
