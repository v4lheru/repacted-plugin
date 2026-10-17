"""
Location: pact-plugin/tests/test_merge_guard_141_u13.py
Summary: 1.4.1 unit U13, merge guard gaps from the 1.4.1 bug audit, against real scratch
         repos with a local bare remote (no real remote is ever reached):
         - a push whose destination is the checked-out branch (`git push`, `git push origin`,
           `git push origin HEAD`, `--all`, the matching refspec `:`, `--repo=<r>`, a branch
           switched earlier in the same command) gates when that branch is main or master,
           and asks when the branch cannot be read (detached HEAD, a directory the guard
           cannot follow);
         - a push, force-push or branch-delete approval binds the checkout and the remote;
         - a GitLab push option that merges the merge request gates as a merge;
         - more than 32 flags refuse; a push-to-main approval binds --no-verify; a dry run
           and a lone --force-if-includes do not gate.
         Every newly gated shape also MINTS: the approval of its own text authorizes it.
"""
import subprocess

import pytest

import merge_guard_pre as guard
import shared.merge_guard_common as mgc
from merge_guard_post import _mint_context_from_bundle
from project_tasks_support import GIT_ENV


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    """work: on main, origin -> remote.git, upstream -> up.git, a `feature` branch.
    other: a second clone of remote.git, on main. Both with a commit."""
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)
    for bare in ("remote.git", "up.git"):
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", bare)
    work = tmp_path / "work"
    _git(tmp_path, "init", "-q", "-b", "main", "work")
    _git(work, "commit", "-q", "--allow-empty", "-m", "root")
    _git(work, "remote", "add", "origin", str(tmp_path / "remote.git"))
    _git(work, "remote", "add", "upstream", str(tmp_path / "up.git"))
    _git(work, "branch", "feature")
    other = tmp_path / "other"
    _git(tmp_path, "init", "-q", "-b", "main", "other")
    _git(other, "commit", "-q", "--allow-empty", "-m", "root")
    _git(other, "remote", "add", "origin", str(tmp_path / "remote.git"))
    mgc.set_invocation_cwd(str(work))
    yield {"tmp": tmp_path, "work": work, "other": other}
    mgc.set_invocation_cwd(None)


def _decide(repos, command, cwd=None):
    """None = allowed; a string = refused (the approval request)."""
    where = str(cwd or repos["work"])
    mgc.set_invocation_cwd(where)
    return guard.check_merge_authorization(command, token_dir=repos["tmp"] / "tokens", cwd=where)


def _mint(command):
    question = {"question": "Proceed?", "multiSelect": False,
                "options": [{"label": "Yes", "description": "Run `%s` now" % command}]}
    ctx, refusal = _mint_context_from_bundle([question], {"Proceed?": "Yes"})
    assert ctx is not None, "gated but unmintable (%s): %r" % (refusal, command)
    return ctx


def _mint_refusal(command):
    """Why the click of `command` mints nothing (None when it mints)."""
    question = {"question": "Proceed?", "multiSelect": False,
                "options": [{"label": "Yes", "description": "Run `%s` now" % command}]}
    return _mint_context_from_bundle([question], {"Proceed?": "Yes"})[1]


# --- item 1: the implicit push to main ------------------------------------------------

IMPLICIT_ON_MAIN = [
    "git push",
    "git push origin",
    "git push origin HEAD",
    "git push origin @",
    "git push --repo=origin",
    "git push --repo origin",
    "git push -u",
    "git push --force-with-lease",
    "git push --follow-tags origin",
    "git push -vo ci.skip origin",
    "git push 2>&1 | tail -3",
]
SCOPE_ANY_BRANCH = [
    "git push --all origin",
    "git push --branches origin",
    "git push origin :",
]


@pytest.mark.parametrize("command", IMPLICIT_ON_MAIN + SCOPE_ANY_BRANCH)
def test_an_implicit_push_on_main_asks(repos, command):
    assert _decide(repos, command) is not None


@pytest.mark.parametrize("command", IMPLICIT_ON_MAIN)
def test_the_same_push_on_a_feature_branch_runs(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is None


@pytest.mark.parametrize("command", SCOPE_ANY_BRANCH)
def test_a_push_of_every_branch_asks_from_any_branch(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is not None


def test_master_counts_like_main(repos):
    _git(repos["work"], "branch", "-m", "main", "master")
    assert _decide(repos, "git push") is not None


@pytest.mark.parametrize("command", [
    "git checkout main && git merge feature && git push",
    "git switch main && git merge --no-ff feature && git push origin",
    "git checkout -q main; git push origin HEAD",
])
def test_switching_to_main_earlier_in_the_command_asks(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is not None


def test_switching_away_from_main_earlier_in_the_command_runs(repos):
    assert _decide(repos, "git checkout feature && git push") is None


def test_a_cd_or_dash_c_into_a_checkout_on_main_asks(repos):
    _git(repos["other"], "checkout", "-q", "-b", "topic")
    work = repos["work"]
    assert _decide(repos, "cd %s && git push" % work, cwd=repos["other"]) is not None
    assert _decide(repos, "git -C %s push" % work, cwd=repos["other"]) is not None
    # and the reverse: from a checkout on main into one on a topic branch
    assert _decide(repos, "cd %s && git push" % repos["other"]) is None


@pytest.mark.parametrize("command", [
    'cd "$REPO" && git push',
    "GIT_DIR=/elsewhere/.git git push",
    "git --git-dir=/elsewhere/.git push origin",
])
def test_a_directory_the_guard_cannot_follow_asks(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is not None


@pytest.mark.parametrize("command", ["git push", "git push origin", "git push origin HEAD"])
def test_a_detached_head_asks(repos, command):
    _git(repos["work"], "checkout", "-q", "--detach")
    assert _decide(repos, command) is not None


def test_a_repo_with_no_origin_on_main_asks(repos):
    _git(repos["work"], "remote", "remove", "origin")
    assert _decide(repos, "git push") is not None


@pytest.mark.parametrize("branch", ["mäin", "mainline", "domain", "main-release", "maintenance"])
def test_a_branch_that_only_looks_like_main_runs(repos, branch):
    _git(repos["work"], "checkout", "-q", "-b", branch)
    assert _decide(repos, "git push") is None
    assert _decide(repos, "git push origin HEAD") is None


@pytest.mark.parametrize("command", [
    "git push --tags origin",
    "git push origin feature",
    "git push --repo=origin origin feature",
    "echo git push",
    "git push --help",
])
def test_an_explicit_non_main_push_on_main_runs(repos, command):
    assert _decide(repos, command) is None


def test_repo_flag_never_turns_a_positional_into_a_refspec(repos):
    # git: a positional repository wins over --repo, so `feature` here is the REPOSITORY and
    # the checked-out main is what gets pushed (probe: `fatal: 'main' does not appear to be a
    # git repository` for `git push --repo=origin main`).
    assert _decide(repos, "git push --repo=origin feature") is not None


@pytest.mark.parametrize("command, gated", [
    ("git push origin HEAD:main", True),
    ("git push origin +main", True),
    ("git push %s main", True),
    ("git push %s", False),
])
def test_explicit_main_forms_and_url_remotes_from_a_feature_branch(repos, command, gated):
    command = command.replace("%s", str(repos["tmp"] / "remote.git"))
    _git(repos["work"], "checkout", "-q", "feature")
    assert (_decide(repos, command) is not None) is gated


def test_a_url_remote_push_on_main_asks(repos):
    assert _decide(repos, "git push %s" % (repos["tmp"] / "remote.git")) is not None


# --- item 16: a push option that merges the merge request ------------------------------

MERGE_OPTIONS = [
    "git push -o merge_request.create -o merge_request.merge_when_pipeline_succeeds origin feature",
    "git push --push-option=merge_request.auto_merge origin feature",
    "git push --push-option merge_request.merge_when_pipeline_succeeds origin feature",
    "git push -omerge_request.auto_merge origin feature",
    "git push origin feature -o merge_request.auto_merge",
    "git -c push.pushOption=merge_request.auto_merge push origin feature",
]


@pytest.mark.parametrize("command", MERGE_OPTIONS)
def test_a_merging_push_option_asks(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is not None


@pytest.mark.parametrize("command", [
    "git push -o merge_request.create origin feature",
    "git push -o ci.skip origin feature",
    "git push --dry-run -o merge_request.auto_merge origin feature",
])
def test_a_non_merging_push_option_runs(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is None


def test_a_merge_option_approval_does_not_cover_a_forced_push(repos):
    ctx = _mint("git push -o merge_request.auto_merge origin feature")
    assert guard._token_matches_command(
        {"context": ctx}, "git push --force -o merge_request.auto_merge origin feature") is False


# --- the low items ---------------------------------------------------------------------

@pytest.mark.parametrize("command", [
    "git push " + "-v " * 33 + "origin main",
    "git push " + "-o a " * 33 + "origin main",
    "git " + "-c a=b " * 33 + "push origin main",
    "gh " + "-R o/r " * 33 + "pr merge 1",
])
def test_more_flags_than_the_guard_reads_refuse(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is not None


def test_thirty_two_flags_still_read_normally(repos):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, "git push " + "-v " * 32 + "origin feature") is None


def test_a_push_to_main_approval_does_not_cover_no_verify(repos):
    ctx = _mint("git push origin main")
    assert guard._token_matches_command({"context": ctx}, "git push origin main") is True
    assert guard._token_matches_command({"context": ctx}, "git push origin main --no-verify") is False


@pytest.mark.parametrize("command", [
    "git push --dry-run origin main",
    "git push -n origin main",
    "git push -vn origin main",
    "git push -n",
])
def test_a_dry_run_push_runs(repos, command):
    assert _decide(repos, command) is None


def test_a_push_option_value_is_not_a_dry_run_flag(repos):
    assert _decide(repos, "git push -o -n origin main") is not None


@pytest.mark.parametrize("command, gated", [
    ("git push --force-if-includes origin feature", False),
    ("git push --force-with-lease --force-if-includes origin feature", False),
    ("git push --force --force-if-includes origin feature", True),
])
def test_force_if_includes_alone_is_not_a_force_push(repos, command, gated):
    _git(repos["work"], "checkout", "-q", "feature")
    assert (_decide(repos, command) is not None) is gated


# --- item 15: an approval binds the checkout and the remote -----------------------------

@pytest.mark.parametrize("command", [
    "git push origin main",
    "git push --force origin feature",
    "git branch -D feature",
    "git push origin --delete feature",
])
def test_an_approval_in_one_checkout_does_not_cover_another(repos, command):
    _git(repos["other"], "branch", "feature")
    ctx = _mint(command)
    assert guard._token_matches_command({"context": ctx}, command) is True
    mgc.set_invocation_cwd(str(repos["other"]))
    assert guard._token_matches_command({"context": ctx}, command) is False
    mgc.set_invocation_cwd(str(repos["work"]))
    assert guard._token_matches_command(
        {"context": ctx}, "cd %s && %s" % (repos["other"], command)) is False


def test_an_approval_for_one_remote_does_not_cover_another(repos):
    ctx = _mint("git push origin main")
    assert guard._token_matches_command({"context": ctx}, "git push upstream main") is False
    assert guard._token_matches_command(
        {"context": ctx}, "git push %s main" % (repos["tmp"] / "up.git")) is False
    # the remote's own URL is the same destination, and another word than the one approved
    assert guard._token_matches_command(
        {"context": ctx}, "git push %s main" % (repos["tmp"] / "remote.git")) is False
    assert guard._token_matches_command({"context": ctx}, "git push origin main") is True


def test_an_implicit_push_approval_binds_the_branch(repos):
    ctx = _mint("git push")
    assert guard._token_matches_command({"context": ctx}, "git push") is True
    _git(repos["work"], "branch", "-m", "main", "master")
    assert guard._token_matches_command({"context": ctx}, "git push") is False


# --- ruling 3: every newly gated shape mints --------------------------------------------

@pytest.mark.parametrize("command", IMPLICIT_ON_MAIN + SCOPE_ANY_BRANCH + MERGE_OPTIONS + [
    "cd %(work)s && git push",
    "git -C %(work)s push",
    "git checkout main && git push",
])
def test_every_newly_gated_shape_mints_and_authorizes_itself(repos, command):
    command = command % {"work": repos["work"]} if "%(" in command else command
    if "git checkout main" in command or "merge_request" in command:
        _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is not None
    ctx = _mint(command)
    assert guard._token_matches_command({"context": ctx}, command) is True


@pytest.mark.parametrize("setup", ["detach", "unfollowable"])
def test_the_asking_shapes_mint_too(repos, setup):
    """A push whose branch or directory cannot be read is refused, and no approval can be
    bound to it: two values that cannot be read are never equal, so the user runs it."""
    if setup == "detach":
        _git(repos["work"], "checkout", "-q", "--detach")
        command = "git push origin"
    else:
        command = 'cd "$REPO" && git push'
    assert _decide(repos, command) is not None
    assert _mint_refusal(command) == "unbindable_shape"
