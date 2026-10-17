"""
Location: pact-plugin/tests/test_merge_guard_glab.py
Summary: GitLab coverage for the merge guard (FIX-MERGE f3). glab and GitLab-API merges,
         closes, merge-request deletes, branch deletes and protection removals go through
         the SAME bound-approval flow as the gh/git verbs: the read floor gates them, a
         faithful AskUserQuestion click (single-select, the literal command in the
         selected option's description) mints a token bound to (op, target, flags), and
         that token authorizes exactly that command and nothing else.

         Also pins the gh parity rows ruled with it (amend_1 on task #141): a bare
         `gh pr close` gates like any close, and a `gh api` PATCH that closes a PR by
         `state=closed` is a close.

         Reads (`glab mr view/list/diff`, `glab api` GET, curl GET, `--help`) must pass
         silently: they are the negative rows.

         Destructive verbs are assembled at runtime so this file stays inert to a live
         guard that reads the transcript.
"""

import io
import json
from unittest.mock import patch

import pytest

from merge_guard_post import _mint_context_from_bundle, _target_value, write_token
from merge_guard_post import main as _post_main
from merge_guard_pre import (
    _token_matches_command,
    check_merge_authorization,
    main as _pre_main,
)
from shared.merge_guard_common import (
    TOKEN_PREFIX,
    detect_command_operation_type,
    extract_command_context,
    is_compound_destructive_command,
    is_dangerous_command,
    locate_command_regions,
)

MERGE = "mer" + "ge"
CLOSE = "clo" + "se"
DELETE = "DEL" + "ETE"
GLAB = "gl" + "ab"
ACCEPT = "acc" + "ept"   # glab's alias of mr merge (glab 1.106 help)

# (label, command, expected operation_type) — every row must gate, mint from a faithful
# click, and be authorized by the token it minted.
GATE_AND_MINT = [
    ("glab-merge-iid", f"{GLAB} mr {MERGE} 42", "merge"),
    ("glab-merge-flags", f"{GLAB} mr {MERGE} 42 --squash --yes", "merge"),
    ("glab-merge-remove-source", f"{GLAB} mr {MERGE} 42 --remove-source-branch", "merge"),
    ("glab-merge-bare", f"{GLAB} mr {MERGE}", "merge"),
    ("glab-merge-branch", f"{GLAB} mr {MERGE} feature/x", "merge"),
    ("glab-merge-url", f"{GLAB} mr {MERGE} https://gitlab.example.com/g/p/-/merge_requests/7", "merge"),
    ("glab-merge-repo-flag", f"{GLAB} -R g/p mr {MERGE} 42", "merge"),
    ("glab-merge-repo-after", f"{GLAB} mr {MERGE} 42 -R g/p", "merge"),
    # 1.4.0 review finding 1: `accept` is glab's alias of `mr merge`.
    ("glab-accept-iid", f"{GLAB} mr {ACCEPT} 42", "merge"),
    ("glab-accept-bang-iid", f"{GLAB} mr {ACCEPT} !42", "merge"),
    ("glab-accept-bare", f"{GLAB} mr {ACCEPT}", "merge"),
    ("glab-accept-flags", f"{GLAB} mr {ACCEPT} 42 --squash --yes -d", "merge"),
    ("glab-accept-repo-flag", f"{GLAB} -R g/p mr {ACCEPT} 42", "merge"),
    ("glab-accept-extra-spaces", f"{GLAB}   mr  {ACCEPT}   42", "merge"),
    ("glab-accept-full-path", f"/opt/homebrew/bin/{GLAB} mr {ACCEPT} 42", "merge"),
    ("glab-close-iid", f"{GLAB} mr {CLOSE} 42", "close"),
    ("glab-close-bare", f"{GLAB} mr {CLOSE}", "close"),
    ("glab-delete-iid", f"{GLAB} mr delete 42", "mr-delete"),
    ("glab-delete-alias", f"{GLAB} mr del 42", "mr-delete"),
    ("glab-delete-bare", f"{GLAB} mr delete", "mr-delete"),
    ("glab-api-merge-put", f"{GLAB} api -X PUT projects/g%2Fp/merge_requests/42/{MERGE}", "merge"),
    ("glab-api-merge-method", f"{GLAB} api --method PUT projects/5/merge_requests/42/{MERGE}", "merge"),
    ("glab-api-merge-quoted-url", f"{GLAB} api 'projects/5/merge_requests/42/{MERGE}' -X PUT", "merge"),
    ("glab-api-merge-attached-method", f"{GLAB} api -XPUT projects/5/merge_requests/42/{MERGE}", "merge"),
    ("glab-merge-quoted-branch", f'{GLAB} mr {MERGE} "feature/x"', "merge"),
    ("glab-api-mr-delete", f"{GLAB} api -X {DELETE} projects/g%2Fp/merge_requests/42", "mr-delete"),
    ("glab-api-branch-delete", f"{GLAB} api -X {DELETE} projects/5/repository/branches/feature", "branch-delete"),
    ("glab-api-close", f"{GLAB} api -X PUT projects/5/merge_requests/42 -f state_event={CLOSE}", "close"),
    ("glab-api-unprotect", f"{GLAB} api -X {DELETE} projects/5/protected_branches/main", "branch-protection"),
    # gh parity (amend_1 a): a bare close gates like any close.
    ("gh-close-number", f"gh pr {CLOSE} 42", "close"),
    ("gh-close-comment", f'gh pr {CLOSE} 42 --comment "superseded"', "close"),
    # gh gap: closing a PR through the REST API.
    ("gh-api-close", f"gh api -X PATCH repos/o/r/pulls/42 -f state={CLOSE}d", "close"),
]


def _click(cmd: str):
    question = {
        "question": "Proceed?",
        "options": [
            {"label": "Yes", "description": "Run `%s` now" % cmd},
            {"label": "No", "description": "Do not run it"},
        ],
        "multiSelect": False,
    }
    return _mint_context_from_bundle([question], {"Proceed?": "Yes"})


def _mint(cmd: str) -> dict:
    ctx, refusal = _click(cmd)
    assert ctx is not None, "faithful click did not mint (%s): %r" % (refusal, cmd)
    return ctx


class TestGitLabGatesAndMints:
    @pytest.mark.parametrize("label,cmd,op", GATE_AND_MINT, ids=[r[0] for r in GATE_AND_MINT])
    def test_read_floor_gates(self, label, cmd, op, tmp_path):
        assert is_dangerous_command(cmd), "not gated: %r" % cmd
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None

    @pytest.mark.parametrize("label,cmd,op", GATE_AND_MINT, ids=[r[0] for r in GATE_AND_MINT])
    def test_classified_with_a_target(self, label, cmd, op):
        ctx = extract_command_context(cmd)
        assert ctx.get("operation_type") == op, ctx
        assert _target_value(ctx) is not None, "no target, so it can never be approved: %r" % cmd

    @pytest.mark.parametrize("label,cmd,op", GATE_AND_MINT, ids=[r[0] for r in GATE_AND_MINT])
    def test_faithful_click_mints_and_authorizes(self, label, cmd, op, tmp_path):
        ctx = _mint(cmd)
        assert ctx["operation_type"] == op
        assert _token_matches_command({"context": ctx}, cmd) is True
        assert write_token(ctx, token_dir=tmp_path) is not None
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None

    def test_gh_close_without_a_target_gates_but_has_no_identity(self, tmp_path):
        """`gh pr close` REQUIRES a target, so the no-positional form never runs: it gates
        (a close) but binds no identity, the documented #1134 no-arg posture."""
        cmd = f"gh pr {CLOSE}"
        assert is_dangerous_command(cmd)
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None
        assert _target_value(extract_command_context(cmd)) is None

    def test_remove_source_branch_is_a_bound_flag(self):
        plain = extract_command_context(f"{GLAB} mr {MERGE} 42")
        removing = extract_command_context(f"{GLAB} mr {MERGE} 42 -d")
        long_form = extract_command_context(f"{GLAB} mr {MERGE} 42 --remove-source-branch")
        assert plain["bound_flags"] == []
        assert removing["bound_flags"] == long_form["bound_flags"] != []


class TestGitLabEndToEnd:
    """The stable entry points: post-hook main mints, pre-hook main allows or denies."""

    @staticmethod
    def _post(cmd, tmp):
        envelope = json.dumps({
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [{
                "question": "Merge it?",
                "options": [{"label": "Yes", "description": "Run `%s`" % cmd},
                            {"label": "No", "description": "Leave it"}],
                "multiSelect": False,
            }]},
            "tool_response": {"answers": {"Merge it?": "Yes"}},
            "session_id": "test-glab",
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp), patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit):
                _post_main()

    @staticmethod
    def _pre(cmd, tmp):
        envelope = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                               "session_id": "test-glab"})
        out = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp), patch("sys.stdin", io.StringIO(envelope)), \
                patch("sys.stdout", out):
            with pytest.raises(SystemExit) as exc:
                _pre_main()
        return exc.value.code, out.getvalue()

    def test_denied_without_approval(self, tmp_path):
        code, out = self._pre(f"{GLAB} mr {MERGE} 42", tmp_path)
        assert '"permissionDecision": "deny"' in out, (code, out)

    def test_approved_click_allows_exactly_that_command(self, tmp_path):
        self._post(f"{GLAB} mr {MERGE} 42", tmp_path)
        assert list(tmp_path.glob(f"{TOKEN_PREFIX}*")), "the click minted no token"
        code, out = self._pre(f"{GLAB} mr {MERGE} 43", tmp_path)
        assert '"permissionDecision": "deny"' in out, "a token for !42 allowed !43"
        code, out = self._pre(f"{GLAB} mr {MERGE} 42", tmp_path)
        assert '"permissionDecision": "deny"' not in out, (code, out)


class TestNoCrossAuthorization:
    CASES = [
        ("gh-merge-token-vs-glab", f"gh pr {MERGE} 42", f"{GLAB} mr {MERGE} 42"),
        ("glab-merge-token-vs-gh", f"{GLAB} mr {MERGE} 42", f"gh pr {MERGE} 42"),
        ("glab-close-token-vs-delete", f"{GLAB} mr {CLOSE} 42", f"{GLAB} mr delete 42"),
        ("glab-delete-token-vs-close", f"{GLAB} mr delete 42", f"{GLAB} mr {CLOSE} 42"),
        ("glab-other-iid", f"{GLAB} mr {MERGE} 42", f"{GLAB} mr {MERGE} 43"),
        ("glab-merge-token-vs-accept-other-iid", f"{GLAB} mr {MERGE} 42", f"{GLAB} mr {ACCEPT} 43"),
        ("glab-accept-token-vs-close", f"{GLAB} mr {ACCEPT} 42", f"{GLAB} mr {CLOSE} 42"),
        ("glab-accept-flag-escalation", f"{GLAB} mr {ACCEPT} 42", f"{GLAB} mr {ACCEPT} 42 -d"),
        ("glab-flag-escalation", f"{GLAB} mr {MERGE} 42", f"{GLAB} mr {MERGE} 42 --remove-source-branch"),
        ("glab-implicit-vs-explicit", f"{GLAB} mr {MERGE}", f"{GLAB} mr {MERGE} 42"),
        ("glab-close-implicit-vs-explicit", f"{GLAB} mr {CLOSE}", f"{GLAB} mr {CLOSE} 42"),
        ("glab-close-token-vs-gh-close", f"{GLAB} mr {CLOSE} 42", f"gh pr {CLOSE} 42"),
        ("glab-branch-delete-other-branch",
         f"{GLAB} api -X {DELETE} projects/5/repository/branches/feature",
         f"{GLAB} api -X {DELETE} projects/5/repository/branches/main"),
    ]

    @pytest.mark.parametrize("label,approved,executed", CASES, ids=[c[0] for c in CASES])
    def test_token_does_not_authorize_a_different_command(self, label, approved, executed):
        ctx = _mint(approved)
        assert _token_matches_command({"context": ctx}, executed) is False


READS = [
    ("glab-mr-view", f"{GLAB} mr view 42"),
    ("glab-mr-list", f"{GLAB} mr list"),
    ("glab-mr-diff", f"{GLAB} mr diff 42"),
    ("glab-mr-note", f'{GLAB} mr note 42 -m "please {MERGE} after review"'),
    ("glab-merge-help", f"{GLAB} mr {MERGE} --help"),
    ("glab-accept-help", f"{GLAB} mr {ACCEPT} --help"),
    # glab verbs are case-sensitive and match no prefix: these print the mr help.
    ("glab-accept-upper", f"{GLAB} mr {ACCEPT.upper()} 42"),
    ("glab-accept-cyrillic-a", f"{GLAB} mr \u0430{ACCEPT[1:]} 42"),
    ("glab-api-get", f"{GLAB} api projects/5/merge_requests/42"),
    ("glab-api-get-merge-path", f"{GLAB} api -X GET projects/5/merge_requests/42/{MERGE}"),
    ("glab-api-branches", f"{GLAB} api projects/5/repository/branches"),
    ("glab-api-protected-get", f"{GLAB} api projects/5/protected_branches"),
    ("curl-get-mr", "curl -s https://gitlab.example.com/api/v4/projects/5/merge_requests/42"),
    ("gh-pr-view", "gh pr view 42"),
    ("gh-pr-list", "gh pr list"),
    ("gh-api-get-pull", "gh api repos/o/r/pulls/42"),
    ("echo-mention", f'echo "{GLAB} mr {MERGE} 42 is next"'),
]


class TestReadsPassSilently:
    @pytest.mark.parametrize("label,cmd", READS, ids=[r[0] for r in READS])
    def test_read_is_not_gated(self, label, cmd, tmp_path):
        assert not is_dangerous_command(cmd), "a read is gated: %r" % cmd
        assert check_merge_authorization(cmd, token_dir=tmp_path) is None


class TestAcceptIsMerge:
    """1.4.0 review finding 1: `glab mr accept` merged with no approval."""

    def test_denied_end_to_end_without_approval(self, tmp_path):
        code, out = TestGitLabEndToEnd._pre(f"{GLAB} mr {ACCEPT} 42", tmp_path)
        assert '"permissionDecision": "deny"' in out, (code, out)

    def test_a_merge_approval_covers_the_same_mr_by_its_alias(self):
        ctx = _mint(f"{GLAB} mr {MERGE} 42")
        assert _token_matches_command({"context": ctx}, f"{GLAB} mr {ACCEPT} 42") is True

    @pytest.mark.parametrize("cmd", [
        f"bash -c '{GLAB} mr {ACCEPT} 42'",
        f"cd /repo && {GLAB} mr {ACCEPT} 42",
        f"{GLAB} mr view 42; {GLAB} mr {ACCEPT} 42",
        f"{GLAB} mr {ACCEPT} 42 && {GLAB} mr delete 42",
    ], ids=["bash-c", "after-cd", "second-leg", "compound"])
    def test_wrapped_or_chained_accept_is_gated(self, cmd, tmp_path):
        assert is_dangerous_command(cmd), cmd
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None


class TestCurlParity:
    """curl merges stay gated-but-unmintable, the status quo the guard keeps for GitHub
    curl merges (#1096 Option B / sec #71: no sound value-flag walk over curl's flag space)."""

    def test_curl_gitlab_merge_is_gated(self, tmp_path):
        cmd = ("curl -X PUT -H 'PRIVATE-TOKEN: x' "
               f"https://gitlab.example.com/api/v4/projects/5/merge_requests/42/{MERGE}")
        assert is_dangerous_command(cmd)
        assert check_merge_authorization(cmd, token_dir=tmp_path) is not None


class TestApprovalTextAndCompounds:
    def test_bare_glab_command_in_option_text_is_located(self):
        regions = locate_command_regions(f"Run {GLAB} mr {MERGE} 42 now")
        assert any(r.startswith(f"{GLAB} mr {MERGE} 42") for r in regions), regions

    def test_two_glab_writes_are_a_compound(self):
        cmd = f"{GLAB} mr {MERGE} 42 && {GLAB} mr delete 42"
        assert is_compound_destructive_command(cmd)

    def test_detect_is_none_for_reads(self):
        # detect classifies on the raw text too, so an inert `--help` or an echoed
        # mention may classify; the read floor (above) is what keeps them un-gated.
        for label, cmd in READS:
            if label in ("glab-merge-help", "glab-accept-help", "echo-mention"):
                continue
            assert detect_command_operation_type(cmd) is None, cmd


# ---------------------------------------------------------------------------------------
# amend_2 (#141 rev 2): the project / repo is part of every REST MR/PR identity, so an
# approval for !42 (or #42) in project A never authorizes the same number in project B.
# A REST placeholder project (`{owner}/{repo}`, `:fullpath`) resolves to the current repo
# (glab -R, else the invocation cwd's git remote); an unresolvable one mints nothing.
# ---------------------------------------------------------------------------------------

import shared.merge_guard_common as mgc  # noqa: E402
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: E402,F401  # re-export: autouse fixture (hermetic cwd + env, amend_5 on #141)


def _git_repo(root, url, worktree=False):
    """A minimal on-disk repo: `.git/config` with an origin url (or a worktree `.git` file
    pointing at a gitdir whose `commondir` holds the config). No git binary needed."""
    common = root / "common.git" if worktree else root / ".git"
    common.mkdir(parents=True)
    (common / "config").write_text('[core]\n\tbare = false\n[remote "origin"]\n\turl = %s\n' % url)
    if worktree:
        gitdir = common / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        (gitdir / "commondir").write_text("../..\n")
        (root / ".git").write_text("gitdir: %s\n" % gitdir)
    return root


@pytest.fixture
def invocation_cwd():
    """Set the directory the hooks resolve REST placeholders against; always restored."""
    def _set(path):
        mgc.set_invocation_cwd(str(path) if path is not None else None)
    yield _set
    mgc.set_invocation_cwd(None)


class TestRestIdentityBindsTheProject:
    CROSS_PROJECT = [
        ("glab-merge", f"{GLAB} api -X PUT projects/g%2Fa/merge_requests/42/{MERGE}",
         f"{GLAB} api -X PUT projects/g%2Fb/merge_requests/42/{MERGE}"),
        ("glab-mr-delete", f"{GLAB} api -X {DELETE} projects/g%2Fa/merge_requests/42",
         f"{GLAB} api -X {DELETE} projects/g%2Fb/merge_requests/42"),
        ("glab-close", f"{GLAB} api -X PUT projects/g%2Fa/merge_requests/42 -f state_event={CLOSE}",
         f"{GLAB} api -X PUT projects/g%2Fb/merge_requests/42 -f state_event={CLOSE}"),
        ("glab-branch-delete", f"{GLAB} api -X {DELETE} projects/g%2Fa/repository/branches/feature",
         f"{GLAB} api -X {DELETE} projects/g%2Fb/repository/branches/feature"),
        ("glab-unprotect", f"{GLAB} api -X {DELETE} projects/g%2Fa/protected_branches/main",
         f"{GLAB} api -X {DELETE} projects/g%2Fb/protected_branches/main"),
        ("glab-numeric-id-vs-path", f"{GLAB} api -X PUT projects/5/merge_requests/42/{MERGE}",
         f"{GLAB} api -X PUT projects/g%2Fa/merge_requests/42/{MERGE}"),
        ("gh-api-merge", f"gh api -X PUT repos/o/a/pulls/42/{MERGE}",
         f"gh api -X PUT repos/o/b/pulls/42/{MERGE}"),
        ("gh-api-merge-owner", f"gh api -X PUT repos/o/a/pulls/42/{MERGE}",
         f"gh api -X PUT repos/x/a/pulls/42/{MERGE}"),
        ("gh-api-close", f"gh api -X PATCH repos/o/a/pulls/42 -f state={CLOSE}d",
         f"gh api -X PATCH repos/o/b/pulls/42 -f state={CLOSE}d"),
        # CLI (repo implicit or -R) and REST (explicit project) never cross-authorize.
        ("glab-cli-vs-rest", f"{GLAB} mr {MERGE} 42", f"{GLAB} api -X PUT projects/g%2Fa/merge_requests/42/{MERGE}"),
        ("glab-rest-vs-cli", f"{GLAB} api -X PUT projects/g%2Fa/merge_requests/42/{MERGE}", f"{GLAB} mr {MERGE} 42"),
        ("gh-cli-vs-rest", f"gh pr {MERGE} 42", f"gh api -X PUT repos/o/a/pulls/42/{MERGE}"),
        ("gh-rest-vs-cli", f"gh api -X PUT repos/o/a/pulls/42/{MERGE}", f"gh pr {MERGE} 42"),
    ]

    @pytest.mark.parametrize("label,approved,executed", CROSS_PROJECT, ids=[c[0] for c in CROSS_PROJECT])
    def test_other_project_is_refused(self, label, approved, executed):
        ctx = _mint(approved)
        assert _token_matches_command({"context": ctx}, executed) is False

    def test_rest_identities_carry_the_project(self):
        assert extract_command_context(
            f"{GLAB} api -X PUT projects/g%2Fp/merge_requests/42/{MERGE}")["mr_target"] == "api:g/p!42"
        assert extract_command_context(
            f"{GLAB} api -X PUT projects/5/merge_requests/42/{MERGE}")["mr_target"] == "api:id:5!42"
        assert extract_command_context(
            f"{GLAB} api -X {DELETE} projects/g%2Fp/repository/branches/feat%2Fx")["branch"] == "api:g/p:feat/x"
        assert extract_command_context(
            f"gh api -X PUT repos/o/r/pulls/42/{MERGE}")["pr_number"] == "api:o/r#42"
        assert extract_command_context(
            f"gh api -X PATCH repos/o/r/pulls/42 -f state={CLOSE}d")["pr_number"] == "api:o/r#42"


class TestRestPlaceholderResolvesToTheCurrentRepo:
    def test_glab_fullpath_resolves_from_the_cwd_remote(self, tmp_path, invocation_cwd):
        invocation_cwd(_git_repo(tmp_path / "p", "git@gitlab.example.com:g/p.git"))
        placeholder = f"{GLAB} api -X PUT projects/:fullpath/merge_requests/42/{MERGE}"
        assert extract_command_context(placeholder)["mr_target"] == "api:g/p!42"
        explicit = f"{GLAB} api -X PUT projects/g%2Fp/merge_requests/42/{MERGE}"
        other = f"{GLAB} api -X PUT projects/g%2Fq/merge_requests/42/{MERGE}"
        ctx = _mint(placeholder)
        assert _token_matches_command({"context": ctx}, explicit) is True
        assert _token_matches_command({"context": ctx}, other) is False

    def test_glab_repo_flag_wins_over_the_cwd(self, tmp_path, invocation_cwd):
        invocation_cwd(_git_repo(tmp_path / "p", "https://gitlab.example.com/g/p.git"))
        cmd = f"{GLAB} api -R g/other -X PUT projects/:fullpath/merge_requests/42/{MERGE}"
        assert extract_command_context(cmd)["mr_target"] == "api:g/other!42"

    def test_gh_owner_repo_resolves_from_a_worktree_remote(self, tmp_path, invocation_cwd):
        repo = _git_repo(tmp_path / "wt", "https://github.com/o/r.git", worktree=True)
        (repo / "sub").mkdir()
        invocation_cwd(repo / "sub")
        cmd = f"gh api -X PUT repos/{{owner}}/{{repo}}/pulls/42/{MERGE}"
        assert extract_command_context(cmd)["pr_number"] == "api:o/r#42"

    def test_unresolvable_placeholder_is_gated_but_mints_nothing(self, tmp_path, invocation_cwd):
        invocation_cwd(tmp_path)          # not a git repo, no -R
        for cmd in (f"{GLAB} api -X PUT projects/:fullpath/merge_requests/42/{MERGE}",
                    f"gh api -X PUT repos/{{owner}}/{{repo}}/pulls/42/{MERGE}",
                    f"{GLAB} api -X PUT projects/:id/merge_requests/42/{MERGE}"):
            assert is_dangerous_command(cmd), cmd
            assert _target_value(extract_command_context(cmd)) is None, cmd
            ctx, refusal = _click(cmd)
            assert ctx is None, (cmd, ctx)


class TestHooksResolvePlaceholdersAgainstThePayloadCwd:
    """End to end: each hook's main() resolves a REST placeholder against its own payload
    cwd, so an approval given in project A does not authorize the same placeholder call
    run from project B."""

    @staticmethod
    def _post(cmd, cwd, tmp):
        envelope = json.dumps({
            "tool_name": "AskUserQuestion", "cwd": str(cwd),
            "tool_input": {"questions": [{
                "question": "Merge it?",
                "options": [{"label": "Yes", "description": "Run `%s`" % cmd},
                            {"label": "No", "description": "Leave it"}],
                "multiSelect": False}]},
            "tool_response": {"answers": {"Merge it?": "Yes"}},
            "session_id": "test-glab",
        })
        with patch("merge_guard_post.TOKEN_DIR", tmp), patch("sys.stdin", io.StringIO(envelope)):
            with pytest.raises(SystemExit):
                _post_main()

    @staticmethod
    def _pre(cmd, cwd, tmp):
        envelope = json.dumps({"tool_name": "Bash", "cwd": str(cwd),
                               "tool_input": {"command": cmd}, "session_id": "test-glab"})
        out = io.StringIO()
        with patch("merge_guard_pre.TOKEN_DIR", tmp), patch("sys.stdin", io.StringIO(envelope)), \
                patch("sys.stdout", out):
            with pytest.raises(SystemExit):
                _pre_main()
        return out.getvalue()

    def test_placeholder_approval_is_bound_to_the_payload_project(self, tmp_path, invocation_cwd):
        repo_a = _git_repo(tmp_path / "a", "git@gitlab.example.com:g/a.git")
        repo_b = _git_repo(tmp_path / "b", "git@gitlab.example.com:g/b.git")
        tokens = tmp_path / "tokens"
        tokens.mkdir()
        cmd = f"{GLAB} api -X PUT projects/:fullpath/merge_requests/42/{MERGE}"
        self._post(cmd, repo_a, tokens)
        assert list(tokens.glob(f"{TOKEN_PREFIX}*")), "the click minted no token"
        assert '"permissionDecision": "deny"' in self._pre(cmd, repo_b, tokens)
        assert '"permissionDecision": "deny"' not in self._pre(cmd, repo_a, tokens)


# ---------------------------------------------------------------------------------------
# amend_3 (#141 rev 3): CLI identities bind the repo too. A CLI merge/close/delete binds the
# repo named by -R/--repo (or GH_REPO=), else the origin of the directory the command runs
# in: the hook cwd, followed through any leading `cd`/`pushd` legs. The target-blind
# no-argument forms also bind the current branch. Unresolvable -> nothing mints.
# ---------------------------------------------------------------------------------------


def _approve_in(cwd, cmd, invocation_cwd):
    invocation_cwd(cwd)
    return _mint(cmd)


def _authorizes_in(cwd, ctx, cmd, invocation_cwd):
    invocation_cwd(cwd)
    return _token_matches_command({"context": ctx}, cmd)


def _two_repos(tmp_path, host="github.com"):
    a = _git_repo(tmp_path / "a", f"git@{host}:o/a.git")
    b = _git_repo(tmp_path / "b", f"git@{host}:o/b.git")
    return a, b


class TestCliIdentityBindsTheRepo:
    CLI = [
        ("gh-merge", f"gh pr {MERGE} 42"),
        ("gh-merge-bare", f"gh pr {MERGE}"),
        ("gh-close", f"gh pr {CLOSE} 42"),
        ("gh-merge-branch", f"gh pr {MERGE} feature/x"),
        ("glab-merge", f"{GLAB} mr {MERGE} 42"),
        ("glab-merge-bare", f"{GLAB} mr {MERGE}"),
        ("glab-close-bare", f"{GLAB} mr {CLOSE}"),
        ("glab-delete-bare", f"{GLAB} mr delete"),
        ("glab-close", f"{GLAB} mr {CLOSE} 42"),
    ]

    @pytest.mark.parametrize("label,cmd", CLI, ids=[c[0] for c in CLI])
    def test_approval_in_repo_a_does_not_authorize_repo_b(self, label, cmd, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        ctx = _approve_in(a, cmd, invocation_cwd)
        assert _authorizes_in(a, ctx, cmd, invocation_cwd) is True
        assert _authorizes_in(b, ctx, cmd, invocation_cwd) is False

    @pytest.mark.parametrize("label,cmd", CLI, ids=[c[0] for c in CLI])
    def test_cd_to_another_repo_is_refused(self, label, cmd, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        ctx = _approve_in(a, cmd, invocation_cwd)
        assert _authorizes_in(a, ctx, f"cd {b} && {cmd}", invocation_cwd) is False
        assert _authorizes_in(a, ctx, f"cd {a} && {cmd}", invocation_cwd) is True

    def test_repo_flag_binds_the_named_repo(self, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        ctx = _approve_in(a, f"gh pr {MERGE} 42 -R o/b", invocation_cwd)
        assert _authorizes_in(b, ctx, f"gh pr {MERGE} 42", invocation_cwd) is False  # -R is still a bound flag
        assert _authorizes_in(a, ctx, f"gh pr {MERGE} 42 -R o/b", invocation_cwd) is True
        assert _authorizes_in(a, ctx, f"gh pr {MERGE} 42 -R o/c", invocation_cwd) is False

    def test_gh_repo_env_names_the_repo(self, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        ctx = _approve_in(b, f"gh pr {MERGE} 42", invocation_cwd)
        assert _authorizes_in(a, ctx, f"GH_REPO=o/b gh pr {MERGE} 42", invocation_cwd) is True
        assert _authorizes_in(b, ctx, f"GH_REPO=o/a gh pr {MERGE} 42", invocation_cwd) is False

    def test_implicit_form_binds_the_branch(self, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path)
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        ctx = _approve_in(a, f"{GLAB} mr {MERGE}", invocation_cwd)
        assert _authorizes_in(a, ctx, f"{GLAB} mr {MERGE}", invocation_cwd) is True
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-2\n")
        assert _authorizes_in(a, ctx, f"{GLAB} mr {MERGE}", invocation_cwd) is False

    def test_wrapped_command_follows_its_own_cd(self, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        ctx = _approve_in(a, f"gh pr {MERGE} 42", invocation_cwd)
        assert _authorizes_in(a, ctx, f"bash -c 'cd {b} && gh pr {MERGE} 42'", invocation_cwd) is False

    def test_wrapped_approval_binds_the_repo_its_cd_reaches(self, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        """A wrapped approval passes the wrapped command it was given for, and only that: the
        wrapper and the directory inside it are part of what was approved."""
        wrapped = f"bash -c 'cd {b} && gh pr {MERGE} 42'"
        ctx = _approve_in(a, wrapped, invocation_cwd)
        assert _authorizes_in(a, ctx, wrapped, invocation_cwd) is True
        assert _authorizes_in(b, ctx, f"gh pr {MERGE} 42", invocation_cwd) is False
        assert _authorizes_in(a, ctx, f"gh pr {MERGE} 42", invocation_cwd) is False
        assert _authorizes_in(a, ctx, f"bash -c 'cd {a} && gh pr {MERGE} 42'", invocation_cwd) is False

    def test_approval_binds_the_repo_its_leading_cd_reaches(self, tmp_path, invocation_cwd):
        """The click approved the whole region, `cd` included: the leg-isolated extraction
        surface drops the `cd`, so the repo must come from the full command."""
        a, b = _two_repos(tmp_path)
        ctx = _approve_in(a, f"cd {b} && gh pr {MERGE} 42", invocation_cwd)
        assert _authorizes_in(b, ctx, f"gh pr {MERGE} 42", invocation_cwd) is True
        assert _authorizes_in(a, ctx, f"gh pr {MERGE} 42", invocation_cwd) is False

    @pytest.mark.parametrize("hop", ["-", "$B", "$(pwd)"])
    def test_a_cd_the_hook_cannot_evaluate_mints_nothing(self, hop, tmp_path, invocation_cwd):
        """`cd -`, `cd $B` and a command substitution land where the shell says, not in a
        directory of that literal name, even when one exists and holds a repo."""
        base = tmp_path / "base"
        base.mkdir()
        _git_repo(base / hop, "git" "@github.com:o/decoy.git")
        invocation_cwd(base)
        ctx, refusal = _click(f"cd {hop} && gh pr {MERGE} 42")
        assert ctx is None and refusal == "repo_unresolvable", refusal

    def test_unresolvable_repo_mints_nothing(self, tmp_path, invocation_cwd):
        invocation_cwd(tmp_path)           # no repo, no -R
        for cmd in (f"gh pr {MERGE} 42", f"{GLAB} mr {MERGE}", f"cd /nonexistent/x && gh pr {MERGE} 42"):
            assert is_dangerous_command(cmd), cmd
            ctx, refusal = _click(cmd)
            assert ctx is None and refusal == "repo_unresolvable", (cmd, refusal)

    def test_url_target_needs_no_cwd_repo(self, tmp_path, invocation_cwd):
        """A URL target names its own repo, so it mints outside any repo checkout."""
        invocation_cwd(tmp_path)
        ctx = _mint(f"gh pr {MERGE} https://github.com/o/r/pull/7")
        assert _token_matches_command({"context": ctx}, f"gh pr {MERGE} https://github.com/o/r/pull/7") is True

    def test_token_without_repo_never_authorizes_a_cli_command(self, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path)
        invocation_cwd(a)
        forged = {"operation_type": "merge", "pr_number": "42", "bound_flags": []}
        assert _token_matches_command({"context": forged}, f"gh pr {MERGE} 42") is False


# ---------------------------------------------------------------------------------------
# amend_3 (#141 rev 3): a REST placeholder (`:fullpath`, `:branch`, `{owner}/{repo}`) names
# the repo and branch the call RUNS in, which a leading `cd` (or a `bash -c` wrapping one)
# or a repo-redirecting env var can change. A placeholder call that reaches another repo
# or branch mints nothing and is never authorized by a token minted where it started.
# ---------------------------------------------------------------------------------------

GL_MERGE_PH = f"{GLAB} api -X PUT projects/:fullpath/merge_requests/42/{MERGE}"
GH_MERGE_PH = f"gh api -X PUT repos/{{owner}}/{{repo}}/pulls/42/{MERGE}"
GL_BRANCH_PH = f"{GLAB} api -X {DELETE} projects/:fullpath/repository/branches/:branch"


class TestRestPlaceholderFollowsTheCommand:
    PH = [("glab-fullpath", GL_MERGE_PH, "gitlab.example.com"),
          ("gh-owner-repo", GH_MERGE_PH, "github.com")]

    @pytest.mark.parametrize("label,cmd,host", PH, ids=[p[0] for p in PH])
    def test_cd_to_another_repo_is_refused(self, label, cmd, host, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path, host)
        ctx = _approve_in(a, cmd, invocation_cwd)
        assert _authorizes_in(a, ctx, f"cd {b} && {cmd}", invocation_cwd) is False
        assert _authorizes_in(a, ctx, f"bash -c 'cd {b} && {cmd}'", invocation_cwd) is False
        assert _authorizes_in(a, ctx, f"cd {a} && {cmd}", invocation_cwd) is True

    @pytest.mark.parametrize("label,cmd,host", PH, ids=[p[0] for p in PH])
    def test_approval_that_moves_to_another_repo_mints_nothing(self, label, cmd, host, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path, host)
        invocation_cwd(a)
        ctx, refusal = _click(f"cd {b} && {cmd}")
        assert ctx is None and refusal == "repo_unresolvable", refusal

    @pytest.mark.parametrize("env", ["GH_REPO=o/b", "GL_REPO=o/b", "GITLAB_REPO=o/b", "GH_HOST=h.example"])
    def test_repo_redirecting_env_is_refused(self, env, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path)
        ctx = _approve_in(a, GH_MERGE_PH, invocation_cwd)
        assert _authorizes_in(a, ctx, f"{env} {GH_MERGE_PH}", invocation_cwd) is False
        ctx2, refusal = _click(f"{env} {GH_MERGE_PH}")
        assert ctx2 is None and refusal == "repo_unresolvable", refusal

    def test_an_explicit_project_path_ignores_the_cd(self, tmp_path, invocation_cwd):
        """The path names its project, so the directory the call runs in is irrelevant."""
        a, b = _two_repos(tmp_path, "gitlab.example.com")
        cmd = f"{GLAB} api -X PUT projects/o%2Fa/merge_requests/42/{MERGE}"
        ctx = _approve_in(a, cmd, invocation_cwd)
        assert _authorizes_in(a, ctx, f"cd {b} && {cmd}", invocation_cwd) is True

    def test_branch_placeholder_binds_the_current_branch(self, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path, "gitlab.example.com")
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        invocation_cwd(a)
        assert extract_command_context(GL_BRANCH_PH)["branch"] == "api:o/a:feature-1"
        ctx = _mint(GL_BRANCH_PH)
        assert _token_matches_command({"context": ctx}, GL_BRANCH_PH) is True
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-2\n")
        assert _token_matches_command({"context": ctx}, GL_BRANCH_PH) is False

    def test_branch_placeholder_on_a_detached_head_mints_nothing(self, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path, "gitlab.example.com")
        (a / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
        invocation_cwd(a)
        assert is_dangerous_command(GL_BRANCH_PH)
        ctx, _refusal = _click(GL_BRANCH_PH)
        assert ctx is None

    def test_an_encoded_colon_is_a_literal_branch_name(self, tmp_path, invocation_cwd):
        """glab expands `:branch` only as written; `%3Abranch` reaches GitLab as the name."""
        a, _b = _two_repos(tmp_path, "gitlab.example.com")
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        invocation_cwd(a)
        cmd = f"{GLAB} api -X {DELETE} projects/:fullpath/repository/branches/%3Abranch"
        assert extract_command_context(cmd)["branch"] == "api:o/a::branch"

    def test_repo_less_token_never_authorizes_an_unresolvable_cli_command(self, tmp_path, invocation_cwd):
        invocation_cwd(tmp_path)          # no repo: the command's repo cannot be resolved
        forged = {"operation_type": "merge", "pr_number": "42", "bound_flags": []}
        assert _token_matches_command({"context": forged}, f"gh pr {MERGE} 42") is False

    @pytest.mark.parametrize("path", [
        "projects/:fullpath/repository/branches/:user",          # glab expands it: not a name
        "projects/:fullpath/repository/branches/wip-:username",
        f"projects/o%2F:repo/merge_requests/42/{MERGE}",          # an embedded project placeholder
    ])
    def test_other_placeholders_mint_nothing(self, path, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path, "gitlab.example.com")
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        invocation_cwd(a)
        method = "PUT" if path.endswith(MERGE) else DELETE
        cmd = f"{GLAB} api -X {method} {path}"
        assert is_dangerous_command(cmd), cmd
        assert _target_value(extract_command_context(cmd)) is None, cmd

    def test_cd_to_a_checkout_of_the_same_repo_on_another_branch_is_refused(self, tmp_path, invocation_cwd):
        """Same origin, different branch (e.g. a second worktree): `:branch` expands to the
        branch there, not the one the approval resolved."""
        a = _git_repo(tmp_path / "a", "git@gitlab.example.com:o/a.git")
        a2 = _git_repo(tmp_path / "a2", "git@gitlab.example.com:o/a.git")
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        (a2 / ".git" / "HEAD").write_text("ref: refs/heads/feature-2\n")
        ctx = _approve_in(a, GL_BRANCH_PH, invocation_cwd)
        assert _authorizes_in(a, ctx, f"cd {a2} && {GL_BRANCH_PH}", invocation_cwd) is False
        (a2 / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        assert _authorizes_in(a, ctx, f"cd {a2} && {GL_BRANCH_PH}", invocation_cwd) is True


# ---------------------------------------------------------------------------------------
# amend_4 (#141 rev 4): the GitHub REST branch delete binds its repo like the GitLab one:
# `gh api -X DELETE repos/<o>/<r>/git/refs/heads/<x>` -> branch `api:<o>/<r>:<x>`.
# `{owner}/{repo}` resolves to the current repo and `heads/{branch}` to the checked-out
# branch (detached HEAD: no mint); both follow the rev-3 cd / bash -c / env rules.
# ---------------------------------------------------------------------------------------


def _gh_ref_delete(path: str) -> str:
    return f"gh api -X {DELETE} {path}"


class TestGhRestBranchDeleteBindsTheRepo:
    def test_identity_carries_the_repo(self):
        assert extract_command_context(_gh_ref_delete("repos/o/r/git/refs/heads/x"))["branch"] == "api:o/r:x"
        assert extract_command_context(_gh_ref_delete("/repos/o/r/git/refs/heads/feat/y"))["branch"] == "api:o/r:feat/y"
        assert extract_command_context(
            _gh_ref_delete("https://api.github.com/repos/o/r/git/refs/heads/x"))["branch"] == "api:o/r:x"

    NO_CROSS = [
        ("other-repo", "repos/o/a/git/refs/heads/x", "repos/o/b/git/refs/heads/x"),
        ("other-owner", "repos/o/a/git/refs/heads/x", "repos/p/a/git/refs/heads/x"),
        ("other-branch", "repos/o/a/git/refs/heads/x", "repos/o/a/git/refs/heads/y"),
    ]

    @pytest.mark.parametrize("label,approved,executed", NO_CROSS, ids=[c[0] for c in NO_CROSS])
    def test_another_repo_or_branch_is_refused(self, label, approved, executed):
        ctx = _mint(_gh_ref_delete(approved))
        assert _token_matches_command({"context": ctx}, _gh_ref_delete(approved)) is True
        assert _token_matches_command({"context": ctx}, _gh_ref_delete(executed)) is False

    def test_the_endpoint_not_a_field_value_names_the_ref(self):
        cmd = f"gh api -f note=x/repos/o/b/git/refs/heads/y -X {DELETE} repos/o/a/git/refs/heads/x"
        assert extract_command_context(cmd)["branch"] == "api:o/a:x"

    def test_cli_and_rest_deletes_never_cross_authorize(self):
        cli = "git branch -D x"
        rest = _gh_ref_delete("repos/o/r/git/refs/heads/x")
        assert _token_matches_command({"context": _mint(cli)}, rest) is False
        assert _token_matches_command({"context": _mint(rest)}, cli) is False

    def test_owner_repo_placeholder_resolves_to_the_current_repo(self, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path)
        invocation_cwd(a)
        cmd = _gh_ref_delete("repos/{owner}/{repo}/git/refs/heads/x")
        assert extract_command_context(cmd)["branch"] == "api:o/a:x"
        ctx = _mint(cmd)
        assert _token_matches_command({"context": ctx}, _gh_ref_delete("repos/o/a/git/refs/heads/x")) is True
        assert _token_matches_command({"context": ctx}, _gh_ref_delete("repos/o/b/git/refs/heads/x")) is False

    def test_branch_placeholder_binds_the_checked_out_branch(self, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path)
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        invocation_cwd(a)
        cmd = _gh_ref_delete("repos/{owner}/{repo}/git/refs/heads/{branch}")
        assert extract_command_context(cmd)["branch"] == "api:o/a:feature-1"
        ctx = _mint(cmd)
        assert _token_matches_command({"context": ctx}, cmd) is True
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-2\n")
        assert _token_matches_command({"context": ctx}, cmd) is False

    @pytest.mark.parametrize("path", [
        "repos/{owner}/{repo}/git/refs/heads/{branch}",     # detached HEAD
        "repos/{owner}/{repo}/git/refs/heads/wip-{branch}",  # gh expands it inside a name
        "repos/o/r/git/refs/{branch}",                       # not under heads/
    ])
    def test_unresolvable_branch_is_gated_but_mints_nothing(self, path, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path)
        (a / ".git" / "HEAD").write_text("0123456789abcdef0123456789abcdef01234567\n")
        invocation_cwd(a)
        cmd = _gh_ref_delete(path)
        assert is_dangerous_command(cmd), cmd
        assert _target_value(extract_command_context(cmd)) is None, cmd
        ctx, _refusal = _click(cmd)
        assert ctx is None, (cmd, ctx)

    def test_unresolvable_repo_is_gated_but_mints_nothing(self, tmp_path, invocation_cwd):
        invocation_cwd(tmp_path)          # no repo
        cmd = _gh_ref_delete("repos/{owner}/{repo}/git/refs/heads/x")
        assert is_dangerous_command(cmd)
        ctx, _refusal = _click(cmd)
        assert ctx is None

    def test_placeholders_follow_the_command_to_another_repo(self, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        cmd = _gh_ref_delete("repos/{owner}/{repo}/git/refs/heads/x")
        ctx = _approve_in(a, cmd, invocation_cwd)
        assert _authorizes_in(a, ctx, f"cd {b} && {cmd}", invocation_cwd) is False
        assert _authorizes_in(a, ctx, f"bash -c 'cd {b} && {cmd}'", invocation_cwd) is False
        assert _authorizes_in(a, ctx, f"GH_REPO=o/b {cmd}", invocation_cwd) is False
        invocation_cwd(a)
        refused, refusal = _click(f"cd {b} && {cmd}")
        assert refused is None and refusal == "repo_unresolvable", refusal

    def test_an_explicit_repo_path_ignores_the_cd(self, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        cmd = _gh_ref_delete("repos/o/a/git/refs/heads/x")
        ctx = _approve_in(a, cmd, invocation_cwd)
        assert _authorizes_in(a, ctx, f"cd {b} && {cmd}", invocation_cwd) is True


# ---------------------------------------------------------------------------------------
# amend_5 (#141 rev 4): every gh/glab identity carries its HOST, and the repo/host
# variables are read from the hook's own environment at mint and read.
# H1: github.com o/r#42 never authorizes the same command on another host; an inline
#     host variable (or a repo variable other than gh's GH_REPO) on the leg refuses.
# H2: an exported GH_REPO/GH_HOST (GITLAB_*/GL_* for glab) is bound, or refuses when it
#     disagrees with the origin of the checkout.
# ---------------------------------------------------------------------------------------


def _hosts(tmp_path, a_url, b_url):
    return _git_repo(tmp_path / "a", a_url), _git_repo(tmp_path / "b", b_url)


class TestHostIsPartOfEveryIdentity:
    SAME_PATH_OTHER_HOST = [
        ("gh-cli", f"gh pr {MERGE} 42", "git" "@github.com:o/r.git", "git" "@ghe.corp:o/r.git"),
        ("gh-cli-bare", f"gh pr {MERGE}", "git" "@github.com:o/r.git", "git" "@ghe.corp:o/r.git"),
        ("glab-cli", f"{GLAB} mr {MERGE} 42", "git" "@gitlab.com:g/p.git", "git" "@gitlab.corp:g/p.git"),
        ("gh-rest", f"gh api -X PUT repos/o/r/pulls/42/{MERGE}", "git" "@github.com:o/r.git", "git" "@ghe.corp:o/r.git"),
        ("glab-rest", f"{GLAB} api -X PUT projects/g%2Fp/merge_requests/42/{MERGE}",
         "git" "@gitlab.com:g/p.git", "git" "@gitlab.corp:g/p.git"),
        ("gh-rest-ref", _gh_ref_delete("repos/o/r/git/refs/heads/x"), "git" "@github.com:o/r.git", "git" "@ghe.corp:o/r.git"),
    ]

    @pytest.mark.parametrize("label,cmd,a_url,b_url", SAME_PATH_OTHER_HOST, ids=[c[0] for c in SAME_PATH_OTHER_HOST])
    def test_approval_on_one_host_does_not_authorize_another(self, label, cmd, a_url, b_url, tmp_path, invocation_cwd):
        a, b = _hosts(tmp_path, a_url, b_url)
        ctx = _approve_in(a, cmd, invocation_cwd)
        assert _authorizes_in(a, ctx, cmd, invocation_cwd) is True
        assert _authorizes_in(b, ctx, cmd, invocation_cwd) is False

    def test_identities_carry_the_host(self, tmp_path, invocation_cwd):
        invocation_cwd(_git_repo(tmp_path / "e", "https://ghe.corp/o/r.git"))
        assert _mint(f"gh pr {MERGE} 42")["host"] == "ghe.corp"
        assert _mint(f"gh api -X PUT repos/o/r/pulls/42/{MERGE}")["host"] == "ghe.corp"
        assert _mint(f"gh api --hostname h.example -X PUT repos/o/r/pulls/42/{MERGE}")["host"] == "h.example"
        assert _mint(f"gh pr {MERGE} 42 -R other.host/o/r")["host"] == "other.host"

    def test_hostname_flag_is_part_of_the_identity(self):
        ctx = _mint(f"gh api -X PUT repos/o/r/pulls/42/{MERGE}")
        assert _token_matches_command({"context": ctx}, f"gh api --hostname ghe.corp -X PUT repos/o/r/pulls/42/{MERGE}") is False

    @pytest.mark.parametrize("cmd", [
        f"GH_HOST=ghe.corp gh pr {MERGE} 42",
        f"GH_HOST=ghe.corp gh api -X PUT repos/o/r/pulls/42/{MERGE}",
        f"GL_HOST=gitlab.corp {GLAB} mr {MERGE} 42",
        f"GITLAB_HOST=gitlab.corp {GLAB} mr {MERGE} 42",
        f"GITLAB_REPO=g/other {GLAB} mr {MERGE} 42",
        f"GL_REPO=g/other {GLAB} mr {MERGE} 42",
    ])
    def test_inline_host_or_foreign_repo_variable_refuses(self, cmd):
        assert is_dangerous_command(cmd), cmd
        ctx, refusal = _click(cmd)
        assert ctx is None and refusal == "repo_unresolvable", (cmd, refusal)
        plain = cmd.split(" ", 1)[1]
        assert _token_matches_command({"context": _mint(plain)}, cmd) is False


class TestExportedRepoAndHostVariables:
    def test_exported_gh_repo_matching_the_checkout_is_bound(self, monkeypatch):
        monkeypatch.setenv("GH_REPO", "o/r")
        ctx = _mint(f"gh pr {MERGE} 42")
        assert _token_matches_command({"context": ctx}, f"gh pr {MERGE} 42") is True

    def test_exported_gh_repo_disagreeing_with_the_checkout_refuses(self, monkeypatch):
        ctx = _mint(f"gh pr {MERGE} 42")                  # approved without the export
        monkeypatch.setenv("GH_REPO", "o/other")
        refused, refusal = _click(f"gh pr {MERGE} 42")
        assert refused is None and refusal == "repo_unresolvable", refusal
        assert _token_matches_command({"context": ctx}, f"gh pr {MERGE} 42") is False

    def test_exported_gh_repo_outside_a_checkout_names_the_repo(self, tmp_path, invocation_cwd, monkeypatch):
        invocation_cwd(tmp_path)                           # no repo here
        monkeypatch.setenv("GH_REPO", "o/b")
        ctx = _mint(f"gh pr {MERGE} 42")
        assert ctx["repo"] == "o/b" and ctx["host"] == "github.com"
        monkeypatch.setenv("GH_REPO", "o/c")
        assert _token_matches_command({"context": ctx}, f"gh pr {MERGE} 42") is False

    def test_exported_gh_host_disagreeing_with_the_checkout_refuses(self, monkeypatch):
        monkeypatch.setenv("GH_HOST", "ghe.corp")         # checkout origin is github.com
        refused, refusal = _click(f"gh pr {MERGE} 42")
        assert refused is None and refusal == "repo_unresolvable", refusal

    def test_exported_gh_host_is_bound_on_an_explicit_rest_path(self, monkeypatch):
        cmd = f"gh api -X PUT repos/o/r/pulls/42/{MERGE}"
        monkeypatch.setenv("GH_HOST", "ghe.corp")
        ctx = _mint(cmd)
        assert ctx["host"] == "ghe.corp"
        monkeypatch.delenv("GH_HOST")
        assert _token_matches_command({"context": ctx}, cmd) is False

    def test_exported_gitlab_host_disagreeing_with_the_checkout_refuses(self, tmp_path, invocation_cwd, monkeypatch):
        invocation_cwd(_git_repo(tmp_path / "g", "git" "@gitlab.com:g/p.git"))
        monkeypatch.setenv("GITLAB_HOST", "gitlab.corp")
        refused, refusal = _click(f"{GLAB} mr {MERGE} 42")
        assert refused is None and refusal == "repo_unresolvable", refusal

    def test_exported_host_disagreeing_refuses_a_rest_placeholder(self, monkeypatch):
        """{owner}/{repo} comes from the github.com checkout; GH_HOST points elsewhere."""
        monkeypatch.setenv("GH_HOST", "ghe.corp")
        refused, refusal = _click(f"gh api -X PUT repos/{{owner}}/{{repo}}/pulls/42/{MERGE}")
        assert refused is None and refusal == "repo_unresolvable", refusal

    def test_exported_repo_variable_disagreeing_refuses_a_rest_placeholder(self, monkeypatch):
        monkeypatch.setenv("GH_REPO", "o/other")
        refused, refusal = _click(f"gh api -X PUT repos/{{owner}}/{{repo}}/pulls/42/{MERGE}")
        assert refused is None and refusal == "repo_unresolvable", refusal


# ---------------------------------------------------------------------------------------
# amend_6 (#141 rev 4): the other REST ref families bind their repo too.
# (1) API force-push (`gh api -X PATCH repos/o/r/git/refs/heads/<ref>`) -> target_ref
#     `api:<o>/<r>:<ref>`; CLI <-> API force-push parity is re-ruled to REFUSE.
# (2) gh api branch-protection removal -> protected_branch `api:<o>/<r>:<branch>`.
# amend_7: curl/wget ref delete/patch (and, by the same reasoning, curl/wget protection
#     removal) stay gated but never mint: no sound flag walk over curl's options.
# ---------------------------------------------------------------------------------------

PATCH_WORD = "PAT" + "CH"


def _gh_ref_patch(path: str) -> str:
    return f"gh api -X {PATCH_WORD} {path} -F force=true -f sha=abc123"


class TestApiForcePushBindsTheRepo:
    def test_identity_carries_the_repo(self):
        assert extract_command_context(_gh_ref_patch("repos/o/r/git/refs/heads/main"))["target_ref"] == "api:o/r:main"

    NO_CROSS = [
        ("other-repo", "repos/o/a/git/refs/heads/main", "repos/o/b/git/refs/heads/main"),
        ("other-ref", "repos/o/a/git/refs/heads/main", "repos/o/a/git/refs/heads/dev"),
    ]

    @pytest.mark.parametrize("label,approved,executed", NO_CROSS, ids=[c[0] for c in NO_CROSS])
    def test_another_repo_or_ref_is_refused(self, label, approved, executed):
        ctx = _mint(_gh_ref_patch(approved))
        assert _token_matches_command({"context": ctx}, _gh_ref_patch(approved)) is True
        assert _token_matches_command({"context": ctx}, _gh_ref_patch(executed)) is False

    def test_cli_and_api_force_push_never_cross_authorize(self):
        cli = "git push --for" + "ce origin main"
        api = _gh_ref_patch("repos/o/r/git/refs/heads/main")
        assert _token_matches_command({"context": _mint(cli)}, api) is False
        assert _token_matches_command({"context": _mint(api)}, cli) is False

    def test_placeholders_resolve_and_follow_the_command(self, tmp_path, invocation_cwd):
        a, b = _two_repos(tmp_path)
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        cmd = _gh_ref_patch("repos/{owner}/{repo}/git/refs/heads/{branch}")
        invocation_cwd(a)
        assert extract_command_context(cmd)["target_ref"] == "api:o/a:feature-1"
        ctx = _approve_in(a, cmd, invocation_cwd)
        assert _authorizes_in(a, ctx, f"cd {b} && {cmd}", invocation_cwd) is False


class TestApiBranchProtectionRemovalBindsTheRepo:
    SPELLINGS = [
        ("delete", f"gh api -X {DELETE} repos/o/r/branches/main/protection"),
        ("put", "gh api -X PUT repos/o/r/branches/main/protection -F enforce_admins=false"),
        ("sub-endpoint", f"gh api -X {DELETE} repos/o/r/branches/main/protection/enforce_admins"),
    ]

    @pytest.mark.parametrize("label,cmd", SPELLINGS, ids=[s[0] for s in SPELLINGS])
    def test_every_spelling_carries_the_repo(self, label, cmd):
        assert extract_command_context(cmd)["protected_branch"] == "api:o/r:main"

    @pytest.mark.parametrize("label,cmd", SPELLINGS, ids=[s[0] for s in SPELLINGS])
    def test_another_repo_is_refused_for_every_spelling(self, label, cmd):
        approved = _mint(cmd.replace("repos/o/r/", "repos/o/a/"))
        assert _token_matches_command({"context": approved}, cmd.replace("repos/o/r/", "repos/o/b/")) is False

    def test_placeholders_resolve(self, tmp_path, invocation_cwd):
        a, _b = _two_repos(tmp_path)
        (a / ".git" / "HEAD").write_text("ref: refs/heads/feature-1\n")
        invocation_cwd(a)
        cmd = f"gh api -X {DELETE} repos/{{owner}}/{{repo}}/branches/{{branch}}/protection"
        assert extract_command_context(cmd)["protected_branch"] == "api:o/a:feature-1"

    def test_other_placeholder_mints_nothing(self):
        cmd = f"gh api -X {DELETE} repos/o/r/branches/wip-{{branch}}/protection"
        assert is_dangerous_command(cmd)
        assert _target_value(extract_command_context(cmd)) is None


class TestCurlRefAndProtectionFormsNeverMint:
    FORMS = [
        ("curl-ref-delete", f"curl -X {DELETE} https://api.github.com/repos/o/r/git/refs/heads/x"),
        ("curl-ref-patch", f"curl -X {PATCH_WORD} https://api.github.com/repos/o/r/git/refs/heads/main -d '{{\"force\":true}}'"),
        ("wget-ref-delete", f"wget --method={DELETE} https://api.github.com/repos/o/r/git/refs/heads/x"),
        ("curl-protection", f"curl -X {DELETE} https://api.github.com/repos/o/r/branches/main/protection"),
        ("wget-protection", f"wget --method={DELETE} https://api.github.com/repos/o/r/branches/main/protection"),
        # the text `gh api` in a header does not make a curl call a gh call
        ("curl-protection-gh-text", f"curl -X {DELETE} https://api.github.com/repos/o/r/branches/main/protection -H 'X-Via: gh api'"),
        ("curl-ref-gh-text", f"curl -X {DELETE} https://api.github.com/repos/o/r/git/refs/heads/x -H 'X-Via: gh api'"),
    ]

    @pytest.mark.parametrize("label,cmd", FORMS, ids=[f[0] for f in FORMS])
    def test_gated_but_never_minted(self, label, cmd):
        assert is_dangerous_command(cmd), cmd
        assert _target_value(extract_command_context(cmd)) is None, cmd
        ctx, _refusal = _click(cmd)
        assert ctx is None, (cmd, ctx)

    def test_a_branch_named_like_a_client_is_still_a_gh_ref(self):
        cmd = _gh_ref_delete("repos/o/r/git/refs/heads/curl-fix")
        assert extract_command_context(cmd)["branch"] == "api:o/r:curl-fix"


# ---------------------------------------------------------------------------------------
# rev 5 (R1, architect-pact review of rev 4): gh api and glab api accept a FULL URL as the
# endpoint and connect to that host. The URL's host is the identity's host (lowercased,
# port dropped); a --hostname that disagrees refuses. S1: an inline GH_CONFIG_DIR /
# GLAB_CONFIG_DIR (another auth config and default host) refuses like the host variables.
# ---------------------------------------------------------------------------------------

URL_FAMILIES = [
    ("gh-ref-delete", f"gh api -X {DELETE} {{e}}", "repos/o/r/git/refs/heads/x", "api/v3"),
    ("gh-force-push", f"gh api -X {PATCH_WORD} {{e}} -F force=true -f sha=abc", "repos/o/r/git/refs/heads/main", "api/v3"),
    ("gh-protection", f"gh api -X {DELETE} {{e}}", "repos/o/r/branches/main/protection", "api/v3"),
    ("gh-merge", "gh api -X PUT {e}", f"repos/o/r/pulls/42/{MERGE}", "api/v3"),
    ("gh-close", f"gh api -X {PATCH_WORD} {{e}} -f state={CLOSE}d", "repos/o/r/pulls/42", "api/v3"),
    ("glab-merge", f"{GLAB} api -X PUT {{e}}", f"projects/g%2Fp/merge_requests/42/{MERGE}", "api/v4"),
    ("glab-mr-delete", f"{GLAB} api -X {DELETE} {{e}}", "projects/g%2Fp/merge_requests/42", "api/v4"),
    ("glab-close", f"{GLAB} api -X PUT {{e}} -f state_event={CLOSE}", "projects/g%2Fp/merge_requests/42", "api/v4"),
    ("glab-branch-delete", f"{GLAB} api -X {DELETE} {{e}}", "projects/g%2Fp/repository/branches/x", "api/v4"),
    ("glab-unprotect", f"{GLAB} api -X {DELETE} {{e}}", "projects/g%2Fp/protected_branches/main", "api/v4"),
]


def _url_form(template, path, prefix, host):
    return template.format(e=f"https://{host}/{prefix}/{path}")


class TestFullUrlEndpointBindsItsHost:
    @pytest.mark.parametrize("label,template,path,prefix", URL_FAMILIES, ids=[f[0] for f in URL_FAMILIES])
    def test_path_approval_does_not_authorize_the_url_form_on_another_host(self, label, template, path, prefix):
        ctx = _mint(template.format(e=path))
        assert _token_matches_command({"context": ctx}, _url_form(template, path, prefix, "ghe.corp")) is False

    @pytest.mark.parametrize("label,template,path,prefix", URL_FAMILIES, ids=[f[0] for f in URL_FAMILIES])
    def test_url_host_a_does_not_authorize_url_host_b(self, label, template, path, prefix):
        a = _url_form(template, path, prefix, "host-a.example")
        ctx = _mint(a)
        assert ctx["host"] == "host-a.example"
        assert _token_matches_command({"context": ctx}, a) is True
        assert _token_matches_command({"context": ctx}, _url_form(template, path, prefix, "host-b.example")) is False

    def test_url_host_is_lowercased_and_the_port_dropped(self):
        cmd = f"gh api -X PUT https://GHE.Corp:8443/api/v3/repos/o/r/pulls/42/{MERGE}"
        assert _mint(cmd)["host"] == "ghe.corp"

    def test_a_disagreeing_hostname_flag_refuses(self):
        cmd = f"gh api --hostname other.example -X PUT https://ghe.corp/api/v3/repos/o/r/pulls/42/{MERGE}"
        assert is_dangerous_command(cmd)
        ctx, refusal = _click(cmd)
        assert ctx is None and refusal == "repo_unresolvable", refusal

    def test_a_url_in_a_field_value_is_not_the_endpoint(self):
        cmd = f"gh api -X PUT repos/o/r/pulls/42/{MERGE} -f note=https://elsewhere.example/x"
        assert _mint(cmd)["host"] == "github.com"

    def test_an_agreeing_hostname_flag_mints(self):
        cmd = f"gh api --hostname ghe.corp -X PUT https://ghe.corp/api/v3/repos/o/r/pulls/42/{MERGE}"
        assert _mint(cmd)["host"] == "ghe.corp"


class TestInlineConfigDirRefuses:
    @pytest.mark.parametrize("cmd", [
        f"GH_CONFIG_DIR=/tmp/other gh pr {MERGE} 42",
        f"GH_CONFIG_DIR=/tmp/other gh api -X PUT repos/o/r/pulls/42/{MERGE}",
        f"GLAB_CONFIG_DIR=/tmp/other {GLAB} mr {MERGE} 42",
        f"GLAB_CONFIG_DIR=/tmp/other {GLAB} api -X PUT projects/g%2Fp/merge_requests/42/{MERGE}",
    ])
    def test_inline_config_dir_refuses(self, cmd):
        assert is_dangerous_command(cmd), cmd
        ctx, refusal = _click(cmd)
        assert ctx is None and refusal == "repo_unresolvable", (cmd, refusal)
        plain = cmd.split(" ", 1)[1]
        assert _token_matches_command({"context": _mint(plain)}, cmd) is False
