"""
Location: pact-plugin/tests/test_env_guard_git.py (ENV-GUARD-2, task 225)
Summary: The egress checks in hooks/env_guard.py (decision D29: only a credential LEAVING the
         machine matters). Git must not stage, commit or push an env-class file or a
         credential file, and it must not show one that is tracked or staged.
           STAGING   git add -A/./:/, add <dir>, add -u, add -f, add <name>, commit -a,
                     commit <pathspec>, and commit with a protected file staged. Fails CLOSED
                     when the check cannot run.
           SHOW      git diff --cached, show, grep, log -p over a tracked or staged env-class
                     file. Fails OPEN.
           PUSH      the outgoing commits (those on no remote ref): a protected file NAME, or
                     an ADDED line in the shared detector's secret classes, denies with the path
                     and kind only. Fails CLOSED.
         `cd X &&`, `(cd X && ...)` and `git -C X` choose the repo the checks inspect.
Used by: the plugin suite. The transcript-side rules are in tests/test_env_guard.py.

Every fixture repo is a temp repo holding inert files: env files hold MARKER, and the secret
line holds INERT_MARKER, which the detector classifies as `test-secret`. No real env or
credential file is opened. Every deny row asserts neither marker reaches the reason.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import env_guard as guard
from project_tasks_support import GIT_ENV, INERT_MARKER

MARKER = "inert-marker-5e1d"


@pytest.fixture(autouse=True)
def _git_env(monkeypatch):
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


def write(repo: Path, rel: str, text: str = f"DEMO_KEY={MARKER}\n") -> Path:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commit_all(repo: Path, message: str = "c", *force: str) -> None:
    git(repo, "add", "-A")
    for rel in force:
        git(repo, "add", "-f", rel)
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    write(path, "README.md", "readme\n")
    commit_all(path, "root")
    return path


@pytest.fixture
def pushed(repo, tmp_path) -> Path:
    """`repo` with a bare remote `origin` that already has main."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def verdict(command: str, cwd: Path) -> dict | None:
    return guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})


def reason(command: str, cwd: Path) -> str | None:
    output = verdict(command, cwd)
    if not output or output["hookSpecificOutput"].get("permissionDecision") != "deny":
        return None
    text = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert MARKER not in text and INERT_MARKER not in text, "a value reached the reason"
    return text


def denied(command: str, cwd: Path) -> bool:
    return reason(command, cwd) is not None


# ── the credential class (names only) ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "id_rsa", "id_ed25519", ".npmrc", ".pypirc", ".git-credentials", "certs/server.key",
    "keys/deploy.pem", "client_secret_123-abc.apps.googleusercontent.com.json",
    ".gcp-credentials.json", ".config/gcloud/credentials.json",
    ".config/gcloud/application_default_credentials.json"])
def test_a_credential_file_is_in_the_class(path):
    assert guard.is_credential_file(path)


@pytest.mark.parametrize("path", [
    "id_rsa.pub", "id_ed25519.pub", "keyboard.py", "src/credentials.py", "credentials.md",
    "docs/credentials.json", "README.md", "package.json"])
def test_an_ordinary_file_is_not_in_the_class(path):
    assert not guard.is_credential_file(path)


@pytest.mark.parametrize("name", [".env.example", ".env.local.example", ".env.sample",
                                  ".env.template", "app.env.dist"])
def test_an_env_template_is_not_an_egress_file(name):
    assert guard.git_protected_kind(name) is None


def test_a_real_env_file_is_an_egress_file():
    assert guard.git_protected_kind(".env") == "env file"
    assert guard.git_protected_kind("config/app.env") == "env file"
    assert guard.git_protected_kind("id_rsa") == "credential file"


# ── STAGING ──────────────────────────────────────────────────────────────────────────────

class TestStaging:
    def test_add_all_denies_an_untracked_env_file(self, repo):
        write(repo, ".env")
        text = reason("git add -A", repo)
        assert text and ".env" in text
        assert denied("git add --all", repo) and denied("git add :/", repo)

    def test_add_dot_denies_an_untracked_credential_file(self, repo):
        write(repo, "id_rsa", "fake-key-body\n")
        assert denied("git add .", repo)

    def test_add_a_directory_denies_a_credential_file_inside_it(self, repo):
        write(repo, "src/main.py", "print(1)\n")
        write(repo, "src/client_secret_1.apps.googleusercontent.com.json", "{}\n")
        assert denied("git add src", repo)
        assert not denied("git add src/main.py", repo)

    def test_an_ignored_env_file_does_not_block_add_all_but_add_force_does(self, repo):
        write(repo, ".gitignore", ".env\n")
        write(repo, ".env")
        assert not denied("git add -A", repo)
        assert denied("git add -f .", repo)

    def test_an_env_template_does_not_block_add_all(self, repo):
        write(repo, ".env.example", "DEMO_KEY=\n")
        assert not denied("git add -A", repo)

    def test_a_tracked_unchanged_credential_file_does_not_block_but_a_change_does(self, repo):
        write(repo, ".npmrc", "engine-strict=true\n")
        commit_all(repo, "npmrc")
        assert not denied("git add -A", repo)
        write(repo, ".npmrc", "engine-strict=false\n")
        assert denied("git add -u", repo)

    def test_a_tracked_env_file_blocks_the_broad_add_forms_even_unchanged(self, repo):
        write(repo, ".env")
        commit_all(repo, "env", ".env")
        text = reason("git add -A", repo)
        assert text and ".env" in text and "tracked" in text
        assert denied("git add .", repo)
        assert not denied("git add README.md", repo)          # a named ordinary file

    def test_a_tracked_env_template_does_not_block_the_broad_add_forms(self, repo):
        write(repo, ".env.example", "DEMO_KEY=\n")
        commit_all(repo, "template")
        assert not denied("git add -A", repo)

    def test_add_naming_a_credential_file_is_denied(self, repo):
        write(repo, "id_rsa", "fake-key-body\n")
        write(repo, "id_rsa.pub", "ssh-ed25519 AAAA fake\n")
        assert denied("git add id_rsa", repo)
        assert not denied("git add id_rsa.pub", repo)

    def test_cd_subshell_and_dash_c_choose_the_repo(self, repo, tmp_path):
        write(repo, ".env")
        assert denied(f"cd {repo} && git add -A", tmp_path)
        assert denied(f"cd {repo}; git add .", tmp_path)
        assert denied(f"(cd {repo} && git add -A)", tmp_path)
        assert denied(f"git -C {repo} add -A", tmp_path)
        assert not denied("git add -A", tmp_path)          # tmp_path itself is not a repo

    def test_an_unresolvable_cd_fails_closed_for_staging(self, repo, tmp_path):
        text = reason("cd $REPO_DIR && git add -A", tmp_path)
        assert text and "could not check" in text

    def test_a_staging_check_that_times_out_fails_closed(self, repo, monkeypatch):
        monkeypatch.setattr(guard, "GIT_TIMEOUT_S", 1e-6)
        text = reason("git add -A", repo)
        assert text and "could not check" in text


class TestCommit:
    def test_commit_naming_an_env_file_is_denied(self, repo):
        write(repo, ".env")
        git(repo, "add", "-f", ".env")
        git(repo, "commit", "-q", "-m", "tracked")
        write(repo, ".env", f"DEMO_KEY={MARKER}2\n")
        assert denied("git commit .env -m x", repo)
        assert denied("git commit -m x -- .env", repo)

    def test_commit_with_a_staged_env_file_is_denied(self, repo):
        write(repo, ".env")
        git(repo, "add", "-f", ".env")
        text = reason("git commit -m 'add config'", repo)
        assert text and ".env" in text

    def test_commit_all_with_a_modified_tracked_credential_file_is_denied(self, repo):
        write(repo, ".npmrc", "engine-strict=true\n")
        commit_all(repo, "npmrc")
        write(repo, ".npmrc", "engine-strict=false\n")
        assert denied("git commit -am x", repo)
        assert denied("git commit --all -m x", repo)

    def test_a_clean_commit_is_allowed(self, repo):
        write(repo, "notes.md", "n\n")
        git(repo, "add", "notes.md")
        assert not denied("git commit -m 'ignore .env files'", repo)


# ── SHOW ─────────────────────────────────────────────────────────────────────────────────

class TestShow:
    def test_diff_cached_with_a_staged_env_file_is_denied(self, repo):
        write(repo, ".env")
        git(repo, "add", "-f", ".env")
        assert denied("git diff --cached", repo) and denied("git diff --staged", repo)

    def test_show_of_a_commit_touching_an_env_file_is_denied(self, repo):
        write(repo, ".env")
        commit_all(repo, "env", ".env")
        assert denied("git show", repo)
        assert not denied("git show HEAD~1", repo)

    def test_grep_over_a_tracked_env_file_is_denied_but_not_over_a_template(self, repo, tmp_path):
        write(repo, ".env")
        commit_all(repo, "env", ".env")
        assert denied("git grep DEMO_KEY", repo)
        other = tmp_path / "other"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        write(other, ".env.example", "DEMO_KEY=\n")
        commit_all(other, "template")
        assert not denied("git grep DEMO_KEY", other)

    def test_log_patch_over_history_with_an_env_file_is_denied(self, repo):
        write(repo, ".env")
        commit_all(repo, "env", ".env")
        assert denied("git log -p", repo)
        assert not denied("git log --oneline", repo)

    def test_a_show_check_that_times_out_fails_open(self, repo, monkeypatch):
        write(repo, ".env")
        git(repo, "add", "-f", ".env")
        monkeypatch.setattr(guard, "GIT_TIMEOUT_S", 1e-6)
        assert not denied("git diff --cached", repo)


# ── PUSH ─────────────────────────────────────────────────────────────────────────────────

class TestPush:
    def test_push_denies_an_outgoing_env_file(self, pushed):
        write(pushed, ".env")
        commit_all(pushed, "env", ".env")
        text = reason("git push", pushed)
        assert text and ".env" in text

    def test_push_denies_an_outgoing_credential_file(self, pushed):
        write(pushed, "id_ed25519", "fake-key-body\n")
        commit_all(pushed, "key")
        assert denied("git push origin main", pushed)

    def test_push_denies_an_added_secret_line_by_kind_and_path_only(self, pushed):
        write(pushed, "config.py", f"API_KEY = '{INERT_MARKER}'\n")
        commit_all(pushed, "config")
        text = reason("git push origin main", pushed)
        assert text and "test-secret" in text and "config.py" in text

    def test_the_push_refusal_asks_the_user_before_offering_the_pragma(self, pushed):
        """The pragma is not a first resort: a real secret must not be tagged as a fixture."""
        from shared.rules import RULES
        write(pushed, "config.py", f"API_KEY = '{INERT_MARKER}'\n")
        commit_all(pushed, "config")
        text = reason("git push origin main", pushed)
        for way in (text, RULES["env.git.push_secret"].instead):
            assert "ask the user to confirm the value is not real" in way
            low = way.lower()
            assert low.index("remove") < low.index("ask the user") < low.index("pragma")

    def test_push_skips_a_line_carrying_the_pragma_and_says_so(self, pushed):
        write(pushed, "tests/fixture.py", f"KEY = '{INERT_MARKER}'  # pragma: allowlist secret\n")
        commit_all(pushed, "fixture")
        output = verdict("git push", pushed)
        assert output and output["hookSpecificOutput"].get("permissionDecision") != "deny"
        notice = output["systemMessage"]
        assert "1 added line(s) skipped by `pragma: allowlist secret`" in notice
        assert "tests/fixture.py" in notice and INERT_MARKER not in notice
        assert output["hookSpecificOutput"]["additionalContext"] == notice

    def test_a_pragma_on_the_line_above_does_not_count_for_push(self, pushed):
        write(pushed, "config.py", f"# pragma: allowlist secret\nKEY = '{INERT_MARKER}'\n")
        commit_all(pushed, "config")
        assert denied("git push", pushed)

    def test_push_allows_a_clean_outgoing_range(self, pushed):
        write(pushed, "notes.md", "n\n")
        commit_all(pushed, "notes")
        assert not denied("git push", pushed)

    def test_a_redirect_and_pipe_tail_is_not_read_as_a_refspec(self, pushed):
        write(pushed, "notes.md", "n\n")
        commit_all(pushed, "notes")
        assert not denied("git push -u origin HEAD 2>&1 | tail -3", pushed)
        write(pushed, "config.py", f"API_KEY = '{INERT_MARKER}'\n")
        commit_all(pushed, "config")
        assert denied("git push -u origin HEAD 2>&1 | tail -3", pushed)

    def test_push_scans_only_what_leaves(self, pushed):
        write(pushed, ".env")
        commit_all(pushed, "env", ".env")
        git(pushed, "push", "-q", "origin", "main")        # already on the remote
        write(pushed, "notes.md", "n\n")
        commit_all(pushed, "notes")
        assert not denied("git push", pushed)

    def test_a_branch_with_no_upstream_scans_the_commits_on_no_remote(self, pushed):
        git(pushed, "checkout", "-q", "-b", "feature")
        write(pushed, ".env")
        commit_all(pushed, "env", ".env")
        assert denied("git push -u origin feature", pushed)

    def test_a_deleting_push_sends_nothing_and_is_allowed(self, pushed):
        assert not denied("git push origin :old", pushed)
        assert not denied("git push --delete origin old", pushed)

    def test_a_push_check_that_times_out_fails_closed(self, pushed, monkeypatch):
        write(pushed, "notes.md", "n\n")
        commit_all(pushed, "notes")
        monkeypatch.setattr(guard, "PUSH_TIMEOUT_S", 1e-6)
        text = reason("git push", pushed)
        assert text and "could not verify" in text

    def test_push_outside_a_repo_is_allowed(self, tmp_path):
        assert not denied("git push", tmp_path)


class TestExpandedPush:
    """Review #118 H1: a push word the shell rewrites (a substitution, a variable, a glob, a
    brace list) names other text than git sees, so the 'names no commit, sends nothing'
    shortcut must not let it past the secret scan. 1.4.0 denied these; so does this."""

    @pytest.fixture
    def leak(self, pushed) -> Path:
        git(pushed, "checkout", "-q", "-b", "leak")
        write(pushed, "config.py", f"API_KEY = '{INERT_MARKER}'\n")
        commit_all(pushed, "config")
        git(pushed, "checkout", "-q", "main")
        return pushed

    @pytest.mark.parametrize("command", [
        'git push origin "$(echo leak)"',
        "git push origin $(echo leak)",
        "git push origin `echo leak`",
        "git push origin ${B:-leak}",
        'git push origin "${B:-leak}"',
        "git push origin $B",
        'git push origin "$(git rev-parse --abbrev-ref leak)"',
        'git push origin "$(echo $(echo leak))"',                  # nested
        "git push $(echo origin leak)",                            # splits into two words
        "git push origin HEAD:$(echo x leak)",                     # an unquoted dst splits too
        "git push origin {leak,main}",
        "git push origin 'refs/heads/*:refs/heads/*'",
        "git push origin le?k",
        "git push origin ~leak",
        "git push origin $'\\x6ceak'$X",
        "git $(echo push) origin leak",                            # the subcommand itself
        'git "$SUB" origin leak',
        "git -c alias.p='!git push' p origin leak",               # a shell alias runs anything
    ])
    def test_an_expanded_push_word_is_denied(self, leak, command):
        text = reason(command, leak)
        assert text and ("shell expansion" in text or "aws" in text or "secret" in text), text

    @pytest.mark.parametrize("command", [
        "git -c alias.p=push p origin leak",
        "git -c alias.P=push P origin leak",
        "git -c alias.p='push origin' p leak",
        "git -c alias.p=q -c alias.q=push p origin leak",          # an alias of an alias
    ])
    def test_an_inline_alias_for_push_is_scanned(self, leak, command):
        text = reason(command, leak)
        assert text and "test-secret" in text and "config.py" in text, text

    @pytest.mark.parametrize("command", [
        "git push origin leak", "git push origin 'le'ak", "git push origin le\\ak",
        "git push origin $'leak'", "git push origin leak:main",
    ])
    def test_the_literal_spellings_still_scan(self, leak, command):
        text = reason(command, leak)
        assert text and "test-secret" in text, text

    @pytest.mark.parametrize("command", [
        "git push origin no-such-branch", "git push origin lеak",  # Cyrillic e: names nothing
        "git push origin HEAD@{0}:feature", "git -c alias.s=status s",
    ])
    def test_a_literal_word_is_not_over_blocked(self, leak, command):
        assert not denied(command, leak)

    def test_the_reason_says_to_write_the_branch_out(self, leak):
        assert "write the branch name out literally" in reason('git push origin "$B"', leak)

    def test_cd_chooses_the_repo_for_push(self, pushed, tmp_path):
        write(pushed, ".env")
        commit_all(pushed, "env", ".env")
        assert denied(f"cd {pushed} && git push", tmp_path)


# ── cost ─────────────────────────────────────────────────────────────────────────────────

def test_a_command_without_an_egress_git_form_starts_no_git_process(repo, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a git process was started")
    monkeypatch.setattr(guard, "_run_git", refuse)
    for command in ("ls -la", "git status", "git log --oneline -3", "git add README.md",
                    "cat README.md", "python3 -c 'print(1)'", "git diff --stat",
                    "git checkout -b x"):
        verdict(command, repo)
    # A plain `git diff` prints the working tree's changes, a tracked env file's among them
    # (1.4.1): it lists the changed names first, so it is no longer free.


def test_the_egress_code_loads_lazily():
    """env_guard runs before every Bash call: the git and detector modules load only when a
    git egress form appears, so the top-level imports of the entry AND of every
    hooks/shell/env module it imports stay the stdlib set the guard had (plus the
    package itself and the façade's `types`)."""
    import ast
    sources = [Path(guard.__file__)] + sorted((Path(guard.__file__).parent / "shell" / "env").glob("*.py"))
    top = set()
    for source in sources:
        for node in ast.parse(source.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Import):
                top |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                top.add((node.module or "").split(".")[0])
    assert top == {"__future__", "fnmatch", "glob", "json", "os", "re", "shutil", "sys", "types", "shell"}


def test_a_fresh_process_that_imports_git_egress_first_allows_a_clean_push(pushed):
    """Regression (M3): git_egress loads the detectors from pact-plugin/bin by a path built
    from its own __file__; at the package depth a wrong path denied every push as
    UNVERIFIED, and the lane passed only because an earlier suite had already imported
    the detectors. A fresh interpreter imports the package module before the entry, so
    import order cannot mask it again."""
    hooks = Path(guard.__file__).resolve().parent
    code = (
        "import json, sys\n"
        f"sys.path.insert(0, {str(hooks)!r})\n"
        "import shell.env.git_egress\n"
        "import env_guard\n"
        "out = env_guard.decide({'tool_name': 'Bash', 'tool_input': {'command': 'git push'},"
        f" 'cwd': {str(pushed)!r}}})\n"
        "print(json.dumps(out))\n"
    )
    write(pushed, "notes.md", "n\n")
    commit_all(pushed, "notes")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          check=True, env=GIT_ENV, timeout=60)
    output = json.loads(done.stdout)
    assert not output or output["hookSpecificOutput"].get("permissionDecision") != "deny", output


_RULE_TAIL = re.compile(r" \[rule: ([a-z_]+(?:\.[a-z_]+)+)\]$")


@pytest.mark.parametrize("command, rule", [
    ("git push origin $B", "env.git.push_expanded"),
    ("git push origin envfile", "env.git.push_sends"),
    ("git push origin leak", "env.git.push_secret"),
])
def test_every_git_egress_deny_ends_with_its_rule_tail(pushed, command, rule):
    """The rule-id tails of the pushes that need a repository (tests/test_shell_rule_ids.py
    covers the stateless denies)."""
    git(pushed, "checkout", "-q", "-b", "leak")
    write(pushed, "config.py", f"API_KEY = '{INERT_MARKER}'\n")
    commit_all(pushed, "config")
    git(pushed, "checkout", "-q", "-b", "envfile", "main")
    write(pushed, ".env")
    commit_all(pushed, "env", ".env")
    git(pushed, "checkout", "-q", "main")
    text = reason(command, pushed)
    assert text is not None, command
    match = _RULE_TAIL.search(text)
    assert match and match.group(1) == rule, text
