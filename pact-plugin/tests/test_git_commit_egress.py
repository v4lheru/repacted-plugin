"""
Location: pact-plugin/tests/test_git_commit_egress.py (ENV-GUARD-2 amend_2, task 225)
Summary: The commit check (hooks/git_commit_check.py) on the shared secret detector. It checks
         the ADDED lines of EVERY staged file type (not 9 code extensions) and the commit
         MESSAGE, reports path and kind only (never a value), and fails CLOSED. An added line
         that itself carries `pragma: allowlist secret` is skipped, and the skip is always
         printed as a count with paths. The E1 rows are sec-review's egress-audit probes (#238),
         rebuilt with fake values.
Used by: the plugin suite. The older compliance rules are in tests/test_git_commit_check.py;
         the push scan's pragma rows are in tests/test_env_guard_git.py.
"""
from __future__ import annotations

import gc
import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from project_tasks_support import GIT_ENV, PLUGIN_ROOT, load, writer

HOOK = PLUGIN_ROOT / "hooks" / "git_commit_check.py"
FAKE = "ghp_" + "Fk9mQ2vLp7Rz4tXk9mQ2vLp7Rz4tXk9mQ2vL"          # github-token shape, fake
PRAGMA = "pragma: allowlist secret"


@pytest.fixture(autouse=True)
def _git_env(monkeypatch):
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def repo(tmp_path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (path / "README.md").write_text("readme\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "root")
    return path


def stage(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "-f", rel)


def run(repo: Path, command: str = "git commit -m 'add config'") -> subprocess.CompletedProcess:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo)}
    done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=60)
    assert FAKE not in done.stdout + done.stderr, "a value reached the hook output"
    return done


def denial(done: subprocess.CompletedProcess) -> str:
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    return done.stdout + done.stderr


E1_FILES = {
    "config.yml": f"github_token: {FAKE}\n",
    "settings.json": f'{{\n  "githubToken": "{FAKE}"\n}}\n',
    "README.md": f"readme\nUse the token {FAKE} to call the API.\n",
    "deploy.sh": f"#!/bin/sh\nexport GH_TOKEN={FAKE}\n",
    "docker-compose.yml": "services:\n  db:\n    environment:\n      DB_PASS" + "WORD: Xk9mQ2vLp7Rz4tQ8\n",
    "notebook.ipynb": f'{{"cells": [{{"source": ["token = \'{FAKE}\'\\n"]}}]}}\n',
    ".netrc": f"machine api.github.com login bot password {FAKE}\n",
    "credentials.json": ('{\n  "type": "service_account",\n  "private_key": "-----BEGIN PRIVATE ' + 'KEY-----'  # pragma: allowlist secret
                         '\\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\\n-----END PRIVATE ' + 'KEY-----\\n"\n}\n'),
}


class TestEveryFileType:
    @pytest.mark.parametrize("rel", sorted(E1_FILES))
    def test_a_secret_in_any_staged_file_type_blocks_the_commit(self, repo, rel):
        stage(repo, rel, E1_FILES[rel])
        text = denial(run(repo))
        assert rel in text

    def test_the_reason_names_the_kind(self, repo):
        stage(repo, "config.yml", E1_FILES["config.yml"])
        assert "github-token" in denial(run(repo))

    def test_only_added_lines_count(self, repo):
        (repo / "docs.md").write_text(f"example: {FAKE}\n", encoding="utf-8")
        git(repo, "add", "docs.md")
        git(repo, "commit", "-q", "--no-verify", "-m", "old example")   # committed before this check
        (repo / "docs.md").write_text(f"example: {FAKE}\nmore prose\n", encoding="utf-8")
        git(repo, "add", "docs.md")
        assert run(repo).returncode == 0

    def test_removing_a_secret_line_is_allowed(self, repo):
        """A deleted line sends nothing new: the cleanup commit must not be blocked."""
        (repo / "docs.md").write_text(f"intro\nexample: {FAKE}\n", encoding="utf-8")
        git(repo, "add", "docs.md")
        git(repo, "commit", "-q", "-m", "old example")
        (repo / "docs.md").write_text("intro\n", encoding="utf-8")
        git(repo, "add", "docs.md")
        assert run(repo, "git commit -m 'remove the example'").returncode == 0

    def test_a_clean_commit_is_allowed_quietly(self, repo):
        stage(repo, "notes.md", "just notes\n")
        done = run(repo)
        assert done.returncode == 0 and json.loads(done.stdout) == {"suppressOutput": True}


class TestMessage:
    def test_a_secret_in_the_message_blocks_the_commit(self, repo):
        stage(repo, "notes.md", "n\n")
        text = denial(run(repo, f"git commit -m 'deploy with {FAKE}'"))
        assert "message" in text and "github-token" in text

    def test_every_message_form_is_read(self, repo):
        stage(repo, "notes.md", "n\n")
        assert run(repo, f"git commit --message='deploy {FAKE}'").returncode == 2
        assert run(repo, f"git commit -am 'deploy {FAKE}'").returncode == 2
        assert run(repo, f"git commit -m 'first' -m 'second {FAKE}'").returncode == 2
        heredoc = f"git commit -m \"$(cat <<'EOF'\nsubject\n\ntoken {FAKE}\nEOF\n)\""
        assert run(repo, heredoc).returncode == 2
        (repo / "msg.txt").write_text(f"subject\n{FAKE}\n", encoding="utf-8")
        assert run(repo, "git commit -F msg.txt").returncode == 2

    def test_an_amend_with_nothing_staged_still_has_its_message_read(self, repo):
        assert run(repo, f"git commit --amend -m 'redo {FAKE}'").returncode == 2

    def test_a_message_that_only_names_a_credential_is_allowed(self, repo):
        stage(repo, "notes.md", "n\n")
        assert run(repo, "git commit -m 'Fix the password reset flow and rotate keys'").returncode == 0


class TestPragma:
    def test_a_line_carrying_the_pragma_is_skipped_and_the_skip_is_printed(self, repo):
        stage(repo, "tests/fixture.py", f"TOKEN = '{FAKE}'  # {PRAGMA}\n")
        done = run(repo)
        assert done.returncode == 0
        shown = json.loads(done.stdout)
        notice = shown["systemMessage"]
        assert "1 added line(s) skipped by `pragma: allowlist secret`" in notice
        assert "tests/fixture.py" in notice
        assert shown["hookSpecificOutput"]["additionalContext"] == notice

    def test_a_pragma_on_the_line_above_does_not_count(self, repo):
        stage(repo, "tests/fixture.yml", f"# {PRAGMA}\ntoken: {FAKE}\n")
        assert denial(run(repo))

    def test_the_pragma_never_covers_the_message(self, repo):
        stage(repo, "notes.md", "n\n")
        assert run(repo, f"git commit -m 'x {FAKE} {PRAGMA}'").returncode == 2


class TestFailClosed:
    def test_an_internal_error_denies(self, capsys):
        check = load("git_commit_check_failclosed", HOOK)
        payload = json.dumps({"tool_input": {"command": "git commit -m 'test'"}})
        with patch("sys.stdin", io.StringIO(payload)), \
                patch.object(check, "committed_files", side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as exit_info:
                check.main()
        assert exit_info.value.code == 2
        output = json.loads(capsys.readouterr().out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_a_diff_that_cannot_be_read_denies(self, repo, monkeypatch):
        check = load("git_commit_check_nodiff", HOOK)
        stage(repo, "notes.md", "n\n")
        real = check.run_git

        def refuse_diff(args, *rest, **kw):
            return None if "-U0" in args else real(args, *rest, **kw)
        monkeypatch.setattr(check, "run_git", refuse_diff)
        with pytest.raises(check.Unverifiable):
            check.commit_patch(check.Commit(repo=str(repo)))


class TestSharedScan:
    def test_the_scan_reads_added_lines_per_path_and_counts_pragma_skips(self):
        common = load("repact_common_scan", PLUGIN_ROOT / "bin" / "_repact_common.py")
        patch_text = "\n".join([
            "+++ b/a.yml", f"+token: {FAKE}", "-old: line",
            "+++ b/b.py", f"+T = '{FAKE}'  # {PRAGMA}", "+plain = 1",
            "+++ /dev/null", "+ignored",
        ])
        hits, skipped = common.scan_added_lines(patch_text)
        assert [path for path, _, _ in hits] == ["a.yml"]    # which secret class wins is the detector's
        assert all(kind in common.SECRET_ONLY_KINDS for _, kind, _ in hits)
        assert skipped == ["b.py"]

    PW_FAKE = "Hx7#" + "kq92LmZ"                                  # a password-like value, fake
    DSN_FAKE = "postgres://admin:" + "Hx7kq92LmZw" + "@db.example.com/x"   # fake
    SECOND = "ghp_" + "Zq8wN3xRt6Yk2Hm5Jc9Vb4Lp7Df1Gs0Aa8Q"     # a second github-token shape, fake

    @pytest.mark.parametrize("line", [
        f"+A = '{FAKE}'; B = '{SECOND}'  # {PRAGMA}",
        f"+A = '{FAKE}'  # {PRAGMA}  B = '{SECOND}'",
        f"+password = '{PW_FAKE}'; token = '{FAKE}'  # {PRAGMA}",
        f'+secret = "{PRAGMA} {FAKE}"',
        f"+{PRAGMA} {FAKE} {SECOND}",
    ], ids=["two-tokens", "second-after-the-pragma", "password-and-token", "pragma-inside-the-value",
            "bare-pragma-two-tokens"])
    def test_a_pragma_covers_one_value_so_a_second_secret_on_its_line_is_a_hit(self, line):
        """Regression: the pragma skipped the whole added line, so a second real secret beside a
        reviewed fixture value passed the commit check and the push scan."""
        common = load("repact_common_scan", PLUGIN_ROOT / "bin" / "_repact_common.py")
        hits, skipped = common.scan_added_lines("\n".join(["+++ b/f.py", "@@ -0,0 +1 @@", line]))
        assert [(path, number) for path, _, number in hits] == [("f.py", 1)] and skipped == []

    @pytest.mark.parametrize("line", [
        f"+TOKEN = '{FAKE}'  # {PRAGMA}", f"+token: {FAKE}  # {PRAGMA}",
        f"+export API_KEY={FAKE} # {PRAGMA}", f"+curl -H 'Authorization: Bearer {FAKE}' # {PRAGMA}",
        f"+DSN = '{DSN_FAKE}'  # {PRAGMA}",
        f'+x = "{PRAGMA}"; TOKEN = "{FAKE}"',
    ], ids=["assignment", "yaml", "export", "bearer", "password-in-url", "pragma-in-another-string"])
    def test_a_pragma_on_a_line_with_one_value_still_covers_it(self, line):
        common = load("repact_common_scan", PLUGIN_ROOT / "bin" / "_repact_common.py")
        hits, skipped = common.scan_added_lines("\n".join(["+++ b/f.py", "@@ -0,0 +1 @@", line]))
        assert hits == [] and skipped == ["f.py"]

    BEGIN = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"                # a key block's first line, fake

    def test_a_pragma_never_covers_a_private_key_block(self):
        """Regression: a pragma on a key block's first line hid the whole key from the commit check
        and the push scan (its body lines match no class alone)."""
        common = load("repact_common_scan", PLUGIN_ROOT / "bin" / "_repact_common.py")
        patch = "\n".join(["+++ b/id_ed25519", "@@ -0,0 +1,3 @@", f"+{self.BEGIN}  # {PRAGMA}",
                           "+b3BlbnNzaC1rZXktdjE", "+-----END " + "OPENSSH PRIVATE KEY-----"])
        hits, skipped = common.scan_added_lines(patch)
        assert [(path, number) for path, _, number in hits] == [("id_ed25519", 1)] and skipped == []

    def test_a_headerless_hunk_line_takes_the_one_value_rule(self):
        """Regression: an added line before any hunk header still used the any-pragma rule."""
        common = load("repact_common_scan", PLUGIN_ROOT / "bin" / "_repact_common.py")
        two = common.scan_added_lines("\n".join(["+++ b/f.py", f"+A = '{FAKE}'; B = '{self.SECOND}'  # {PRAGMA}"]))
        one = common.scan_added_lines("\n".join(["+++ b/f.py", f"+A = '{FAKE}'  # {PRAGMA}"]))
        assert [(path, number) for path, _, number in two[0]] == [("f.py", 0)] and two[1] == []
        assert one == ([], ["f.py"])

    def test_counting_the_values_on_a_long_pragma_line_is_not_quadratic(self):
        """Regression: the value count compared every span with every other: 4000 values took
        0.5 s and 40000 about 54 s, past the hooks' timeout (which denies)."""
        load("repact_common_scan", PLUGIN_ROOT / "bin" / "_repact_common.py")
        from shared.scrub import pragma_covers          # importable once _repact_common loaded
        line = " ".join(f"{FAKE[:-5]}{i:05d}" for i in range(20000)) + f"  # {PRAGMA}"
        gc.collect()
        gc.disable()
        try:
            started = time.perf_counter()
            covered = pragma_covers(line)
            elapsed = time.perf_counter() - started
        finally:
            gc.enable()
        assert covered is False and elapsed < 1

    def test_the_commit_only_shapes_honour_the_same_one_value_rule(self):
        check = load("git_commit_check_pragma", HOOK)
        value = "sk_test_" + "a" * 50
        covered = check.check_hardcoded_secrets({"config.py": [(1, f'k = "{value}"  # {PRAGMA}')]})
        two = check.check_hardcoded_secrets({"config.py": [(1, f'k = "{value}"; j = "{value[:-1]}b"'
                                                               f'  # {PRAGMA}')]})
        assert covered == [] and two and "config.py" in two[0]

    def test_the_legacy_check_prints_no_value(self):
        check = load("git_commit_check_preview", HOOK)
        value = "sk_test_" + "a" * 50
        errors = check.check_hardcoded_secrets({"config.py": [(1, f'k = "{value}"')]})
        assert errors and "a" * 10 not in errors[0]


class TestWhereItMatched:
    """T-9d85a048: a block names the file, the line and the pattern, and never the value."""

    def test_first_and_last_line_matches_are_both_named(self, repo):
        stage(repo, "a.yml", f"first {FAKE}\nprose\nmore prose\nlast {FAKE}")   # no final newline
        text = denial(run(repo))
        assert "a.yml lines 1, 4 (the github-token secret pattern)" in text

    def test_a_line_after_unchanged_lines_gets_its_new_file_number(self, repo):
        (repo / "b.md").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
        git(repo, "add", "b.md")
        git(repo, "commit", "-q", "-m", "base")
        (repo / "b.md").write_text(f"one\ntwo\n\u00e9t\u00e9 \u2713 \U0001f511 {FAKE} \u00fc\nfour\n", encoding="utf-8")
        git(repo, "add", "b.md")
        text = denial(run(repo))
        assert "b.md line 3 " in text and FAKE[4:14] not in text

    def test_a_multi_line_key_names_its_header_line_and_prints_no_body(self, repo):
        body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7"
        stage(repo, "key.pem", "note\n-----BEGIN PRIVATE " + f"KEY-----\n{body}\n{body[::-1]}\n-----END PRIVATE KEY-----\n")
        text = denial(run(repo))
        assert "key.pem line" in text and body[:12] not in text and body[::-1][:12] not in text

    def test_an_added_line_that_starts_like_a_file_header_is_still_read(self, repo):
        stage(repo, "c.txt", f"intro\n++ {FAKE}\n")
        assert "c.txt line 2 " in denial(run(repo))

    def test_the_message_names_its_line(self, repo):
        stage(repo, "notes.md", "n\n")
        text = denial(run(repo, f"git commit -m 'subject' -m 'deploy with {FAKE}'"))
        assert "line 1 of the commit message part 2 of 2 " in text

    def test_the_legacy_check_names_the_lines_and_no_value(self):
        check = load("git_commit_check_lines", HOOK)
        key = "sk_test_" + "a" * 50
        errors = check.check_hardcoded_secrets({"config.py": [(1, "x = 1"), (2, f'k = "{key}"')]})
        assert errors == ["SACROSANCT VIOLATION: Potential Stripe test key in config.py line 2"]

    @pytest.mark.parametrize("separator", ["\x0c", "\x1e", "\x85", "\u2028", "\r"])
    def test_a_secret_after_a_non_newline_separator_is_read(self, repo, separator):
        stage(repo, "ff.txt", f"intro\nabc{separator}{FAKE}\n")
        assert "ff.txt line 2 " in denial(run(repo))

    def test_extra_separators_cannot_turn_a_later_line_into_a_devnull_header(self, repo):
        stage(repo, "g.txt", f"a\x0c+b\x0c+c\n++ /dev/null\nkey {FAKE}\n")
        assert "g.txt line 3 " in denial(run(repo))

    def test_a_spaced_non_ascii_path_is_printed_as_written(self, repo):
        stage(repo, "déjà vu.txt", f"t {FAKE}\n")
        text = denial(run(repo))
        assert "in déjà vu.txt line 1 " in text and "b/" not in text and "\t" not in text

    @pytest.mark.parametrize("header, shown", [
        ('"b/a\\nb.txt"', "a\\nb.txt"),                   # a newline in a name stays escaped
        ('"b/x\\342\\200\\256y"', "x\\u202ey"),           # so does a right-to-left override
        ('"b/q\\"t\\\\u"', 'q"t\\u'),
        ("b/sp ace.txt\t", "sp ace.txt"),
        ('"b/cut', "cut"),                                 # an unterminated quote ends the name
    ])
    def test_a_header_path_is_unquoted_but_never_prints_control_text(self, header, shown):
        common = load("repact_common_path", PLUGIN_ROOT / "bin" / "_repact_common.py")
        assert common._diff_path(header) == shown


# T-614cab82: a credential-named key whose value is an env read or a member access holds no secret,
# and a host:port userinfo (the open-redirect fixture shape) holds a port, not a password.
# The credential words are joined at run time, so this source never carries the shapes it tests.
WORDS = {"pw": "pass" "word", "Pw": "Pass" "word", "PW": "PASS" "WORD", "tok": "to" "ken",
         "Tok": "To" "ken", "sec": "sec" "ret", "Sec": "Sec" "ret", "SEC": "SEC" "RET"}
CODE_REFERENCE_LINES = [line.format(**WORDS) for line in [
    "    {pw}: process.env.BOARD_{PW},",
    "const demo{Pw} = process.env.DEMO_LOGIN_{PW};",
    "var backfill{Tok} = request.BackfillRequestId;",
    "string api{Sec} = req.RequestId;",
    "  {tok}: this.props.auth{Tok},",
    "db_{pw} = settings.DB_{PW}",
    "const api{Tok} = import.meta.env.SERVER_TOKEN_NAME  // read at build time",
    "\tapi{Tok} := cfg.Api{Tok}",                                  # Go: a member read
]]
OPEN_REDIRECT_LINES = [
    'expect(isSafe("http://localhost:3000@attacker.example/")).toBe(false);',  # pragma: allowlist secret
    "    ('https://localhost:443@evil.test/next', False),",  # pragma: allowlist secret
    "assert not safe_redirect('http://127.0.0.1:8080@evil.test')",  # pragma: allowlist secret
    "it('blocks https://trusted.example.com:443@evil.test', () => {",  # pragma: allowlist secret
]
STILL_SECRET_LINES = [line.format(**WORDS) for line in [
    '{pw} = "Hunter2Secret!"',                                      # a literal password
    "  {pw}: process.env.X + 'Fallback99!x',",                     # an env read concatenated with a literal
    'const session{Tok} = process.env.T || "Zq8vLm2pXr4tWs";',      # a literal fallback
    '{pw} = "process.env.DB_{PW}"',                                 # an env read inside a string
    '{pw}: decrypt("Hunter2Secret!")',                              # a password key with a non-env call
    "SESSION_{SEC}=Ab3x.Kq9z.Zz1v",                                 # a dotted literal in an env file
    "{PW}=request.Backfill9",                                       # a bare `=` assignment is a literal
    "{sec}: Zq8.vLm2pXr4tw;",                                       # a dotted run that is no identifier
    "postgres://app:" "Hunter2Secret@localhost:5432/db",            # userinfo with a colon and a password
    "http://admin:" "12345678@db.example.com/x",                    # digits after a user that is no host
    "https://evil.test:" "Pa55word@x.test/",                        # a host-like user, a non-port password
    "http://localhost:" "3000x@evil.test/",                         # not a port: letters after the digits
    '{pw}: process.env.X // was "Hunter2Secret!"',                 # a literal in the trailing comment
    '\tapi{Tok} := "Zq8vLm2pXr4tWs"',                              # Go: a short variable declaration
]]


class TestCodeReferencesAreNotSecrets:
    @pytest.mark.parametrize("line", CODE_REFERENCE_LINES + OPEN_REDIRECT_LINES)
    def test_every_scanner_passes_a_reference_or_a_port(self, line):
        """The commit and push scans (scan_added_lines), the message check (secret_kind) and the
        task-log text check (credential_reason via check_text) give one verdict: no secret."""
        common = load("repact_common_refs", PLUGIN_ROOT / "bin" / "_repact_common.py")
        assert common.secret_kind(line) is None
        assert common.scan_added_lines(f"+++ b/src/app.js\n+{line}\n") == ([], [])
        writer().check_text(line)                       # raises Refused on a credential value

    @pytest.mark.parametrize("line", STILL_SECRET_LINES)
    def test_a_literal_still_refuses(self, line):
        common = load("repact_common_lits", PLUGIN_ROOT / "bin" / "_repact_common.py")
        assert common.secret_kind(line) is not None
        assert common.scan_added_lines(f"+++ b/src/app.js\n+{line}\n")[0]

    def test_the_commit_check_and_the_push_scan_allow_the_fixtures(self, repo, tmp_path):
        stage(repo, "src/server.js", "\n".join(CODE_REFERENCE_LINES) + "\n")
        stage(repo, "tests/redirect.test.js", "\n".join(OPEN_REDIRECT_LINES) + "\n")
        assert run(repo).returncode == 0
        git(repo, "commit", "-q", "-m", "add the fixtures")
        remote = tmp_path / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
        git(repo, "remote", "add", "origin", str(remote))
        import env_guard

        def push_denied() -> bool:
            decision = env_guard.decide({"tool_name": "Bash", "tool_input": {"command": "git push origin main"},
                                         "cwd": str(repo)})
            return bool(decision) and decision["hookSpecificOutput"].get("permissionDecision") == "deny"
        assert not push_denied()
        stage(repo, "src/db.js", STILL_SECRET_LINES[0] + "\n")               # the scan does read them
        git(repo, "commit", "-q", "-m", "add a literal")
        assert push_denied()

    def test_the_commit_check_still_refuses_a_literal_password(self, repo):
        stage(repo, "src/db.js", STILL_SECRET_LINES[0] + "\n")
        assert "src/db.js line 1" in denial(run(repo))


# U14 (audit items 4, 5, 24): the scan reads what git will commit, never allows unscanned, and
# reads added lines only. Commands are read through shell_words, so every spelling is the commit.
def modify(repo: Path, rel: str, text: str) -> None:
    """Change a tracked file in the working tree and stage nothing."""
    (repo / rel).write_text(text, encoding="utf-8")


WORKTREE_COMMITS = [
    "git commit -am fix",
    "git commit --all --message fix",
    "git commit -qam fix",
    "git commit README.md -m fix",
    "git commit -m fix -- README.md",
    "git commit --only -m fix README.md",
    "git commit -i README.md -m fix",
    "git commit $'-a' -m fix",
    '"git" commit "--all" -m fix',
    "GIT_AUTHOR_NAME=x env git commit -a -m fix",
    "rtk proxy git commit -am fix",
    "bash -c 'git commit -am fix'",
    "true && git commit -am fix",
]


class TestWhatGitCommits:
    @pytest.mark.parametrize("command", WORKTREE_COMMITS)
    def test_a_working_tree_secret_the_commit_takes_is_scanned(self, repo, command):
        modify(repo, "README.md", f"readme\ntoken {FAKE}\n")
        assert "README.md line 2" in denial(run(repo, command))

    def test_a_plain_commit_leaves_an_unstaged_change_alone(self, repo):
        modify(repo, "README.md", f"readme\ntoken {FAKE}\n")
        stage(repo, "notes.md", "n\n")
        assert run(repo, "git commit -m fix").returncode == 0

    def test_git_dash_c_with_all_scans_the_named_repo(self, repo, tmp_path):
        modify(repo, "README.md", f"readme\ntoken {FAKE}\n")
        payload_cwd = tmp_path
        payload = {"tool_name": "Bash", "cwd": str(payload_cwd),
                   "tool_input": {"command": f"git -C '{repo}' commit -a -m fix"}}
        done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=60)
        assert done.returncode == 2 and FAKE not in done.stdout + done.stderr

    def test_a_spaced_unicode_pathspec_is_scanned(self, repo):
        (repo / "déjà vu.md").write_text("x\n", encoding="utf-8")
        git(repo, "add", "déjà vu.md")
        git(repo, "commit", "-q", "-m", "base")
        modify(repo, "déjà vu.md", f"x\nt {FAKE}\n")
        assert "déjà vu.md line 2" in denial(run(repo, "git commit 'déjà vu.md' -m fix"))

    def test_an_alternate_index_is_the_one_scanned(self, repo):
        (repo / "alt.md").write_text(f"t {FAKE}\n", encoding="utf-8")
        index = repo / ".git" / "alt-index"
        subprocess.run(["git", "-C", str(repo), "read-tree", "HEAD"], check=True,
                       env={**os.environ, "GIT_INDEX_FILE": str(index)})
        subprocess.run(["git", "-C", str(repo), "add", "alt.md"], check=True,
                       env={**os.environ, "GIT_INDEX_FILE": str(index)})
        assert denial(run(repo, f"GIT_INDEX_FILE='{index}' git commit -m fix"))

    @pytest.mark.parametrize("command", ["git commit -SKEYC -m fix", "git commit -uno -m fix x.md",
                                         "git commit --only -m fix nothing.md"])
    def test_no_option_reading_drops_the_index_from_the_scan(self, repo, command):
        stage(repo, "notes.md", f"t {FAKE}\n")
        assert "notes.md line 1" in denial(run(repo, command))

    def test_a_git_dir_option_or_an_exported_index_is_followed(self, repo, tmp_path):
        stage(repo, "notes.md", f"t {FAKE}\n")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        assert denial(run(elsewhere, f"git --git-dir='{repo}/.git' --work-tree='{repo}' commit -m fix"))
        (repo / "alt.md").write_text(f"t {FAKE}\n", encoding="utf-8")
        index = repo / ".git" / "alt-index"
        for args in (["read-tree", "HEAD"], ["add", "alt.md"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True,
                           env={**os.environ, "GIT_INDEX_FILE": str(index)})
        git(repo, "reset", "-q")                                     # the default index is clean
        assert "alt.md" in denial(run(repo, f"export GIT_INDEX_FILE='{index}'; git commit -m fix"))

    def test_a_pragma_line_staged_and_in_the_tree_is_counted_once(self, repo):
        modify(repo, "README.md", f"readme\nT = '{FAKE}'  # {PRAGMA}\n")
        git(repo, "add", "README.md")
        done = run(repo, "git commit -am fix")
        assert done.returncode == 0
        assert json.loads(done.stdout)["systemMessage"].startswith("1 added line(s) skipped")

    def test_an_empty_commit_is_allowed(self, repo):
        assert run(repo, "git commit --allow-empty -m empty").returncode == 0

    def test_a_huge_file_is_scanned_to_its_end(self, repo):
        modify(repo, "README.md", "filler line of prose\n" * 250_000 + f"t {FAKE}\n")
        assert "README.md line 250001" in denial(run(repo, "git commit -am big"))


class TestGitThatDoesNotAnswer:
    def test_a_git_slower_than_the_bound_denies_with_the_reason(self, repo, tmp_path, monkeypatch, capsys):
        stage(repo, "notes.md", f"t {FAKE}\n")
        shim = tmp_path / "shim"
        shim.mkdir()
        real = subprocess.run(["/usr/bin/env", "which", "git"], capture_output=True, text=True).stdout.strip()
        (shim / "git").write_text(f"#!/bin/sh\nsleep 3\nexec '{real}' \"$@\"\n", encoding="utf-8")
        (shim / "git").chmod(0o755)
        monkeypatch.setenv("PATH", f"{shim}:{os.environ['PATH']}")
        check = load("git_commit_check_slow", HOOK)
        monkeypatch.setattr(check, "GIT_TIMEOUT_S", 1)
        payload = json.dumps({"tool_input": {"command": "git commit -m fix"}, "cwd": str(repo)})
        with patch("sys.stdin", io.StringIO(payload)), pytest.raises(SystemExit) as exit_info:
            check.main()
        assert exit_info.value.code == 2
        reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
        assert "did not answer within 1 s" in reason and "not scanned" in reason


PRINT = "pri" "nt"                         # a log call joined at run time (see test_git_commit_check)
LEGACY_FALSE_POSITIVES = {                  # T-audit item 24: ordinary code, no value in any line
    "src/form.ts": "const rules = { " + WORDS["pw"] + ": '" + WORDS["Pw"] + " is required' };\n",
    "app/mask.py": WORDS["pw"] + " = '********'\n",
    "app/prompt.py": "print('Enter " + WORDS["pw"] + ": ')\n",
    "src/rotate.js": "console.log('" + WORDS["sec"] + "s rotated');\n",
}


class TestAddedLinesOnly:
    def test_ordinary_code_naming_a_credential_is_allowed(self, repo):
        for rel, text in LEGACY_FALSE_POSITIVES.items():
            stage(repo, rel, text)
        done = run(repo)
        assert done.returncode == 0, done.stderr

    def test_an_old_line_never_blocks_a_later_commit_of_the_file(self, repo):
        stage(repo, "app/log.py", f"{PRINT}(api_key)\n")
        git(repo, "commit", "-q", "-m", "old")                      # committed before this check
        stage(repo, "app/log.py", f"{PRINT}(api_key)\nx = 1\n")
        assert run(repo).returncode == 0

    def test_logging_a_credential_value_still_blocks(self, repo):
        stage(repo, "app/log.py", f"x = 1\n{PRINT}(api_key)\n")
        assert "app/log.py line 2" in denial(run(repo))
