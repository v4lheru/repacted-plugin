"""
Location: pact-plugin/tests/test_merge_guard_150_pushfix.py
Summary: review #118 items M1, M2 and the destination half of H1, against real scratch repos
         with a local bare remote (no real remote is ever reached):
         - a push destination git DWIMs to main (`heads/main`), a glob refspec, or a
           destination written as a shell expansion gates like a push to main, and asks with
           a reason that says to write the branch out;
         - `gh api` with an attached method (`-XPUT`, `--method=PUT`) gates like `-X PUT`;
         - `gh pr --repo o/r merge 5` (a flag before the verb) and a git subcommand defined by
           `-c alias.*` gate, and are refused as unbindable (the user runs them).
         Every newly gated push and API shape also MINTS: the approval of its own text
         authorizes it.
"""
import pytest

import merge_guard_pre as guard
import shared.merge_guard_common as mgc
from test_merge_guard_141_u13 import _decide, _git, _mint, _mint_refusal, repos  # noqa: F401 - fixture


# --- M1 + H1 destination: refspecs that can update main -----------------------------------

OPEN_OR_DWIM_MAIN = [
    "git push origin HEAD:heads/main",
    "git push origin HEAD:heads/master",
    "git push origin feature:refs/heads/main",
    "git push origin 'refs/heads/*:refs/heads/*'",
    "git push origin '*:*'",
    "git push origin 'feature:refs/heads/*'",
    'git push origin "$(echo main)"',
    "git push origin $(echo main)",
    "git push origin ${B:-main}",
    'git push origin HEAD:"$B"',
    "git push origin HEAD:$(echo main)",
    "git push origin HEAD:`echo main`",
    'git push origin HEAD:"$(echo $(echo main))"',
    "git push origin HEAD:$'\\x6dain'$X",
    'cd . && git push origin HEAD:"$B"',
]


@pytest.mark.parametrize("command", OPEN_OR_DWIM_MAIN)
def test_a_destination_that_can_be_main_asks_from_a_feature_branch(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is not None


# A destination spelled with a glob mints against its own text. One spelled with a shell
# expansion mints nothing: what it expands to is not known when it is approved, so the
# approval could not be bound to the command (its reason says to write the branch out).
@pytest.mark.parametrize("command", [c for c in OPEN_OR_DWIM_MAIN
                                     if not c.startswith("cd") and "`" not in c])
def test_each_such_push_mints_against_its_own_text(repos, command):
    if "$" in command:
        assert _mint_refusal(command) == "unbindable_shape"
        return
    ctx = _mint(command)
    assert ctx["operation_type"] == "push-to-main"
    assert guard._token_matches_command({"context": ctx}, command) is True


def test_an_expanded_destination_says_to_write_the_branch_out(repos):
    _git(repos["work"], "checkout", "-q", "feature")
    text = _decide(repos, 'git push origin "$(git branch --show-current)"')
    assert "write the branch name out literally" in text


@pytest.mark.parametrize("command", [
    "git push origin feature",
    "git push -u origin HEAD",
    "git push origin HEAD:feature",
    "git push origin HEAD:heads/feature",
    "git push origin HEAD:refs/heads/main-release",
    "git push origin HEAD:heads/mainline",
    "git push origin 'feature'",
    "git push origin feature -o 'ci.variable=X=$Y'",              # a push-option value
])
def test_a_literal_feature_destination_runs(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is None


# --- M2: gh api with an attached method, a flag before the verb, an inline alias ---------

@pytest.mark.parametrize("command", [
    "gh api -XPUT repos/o/r/pulls/5/merge",
    "gh api -X PUT repos/o/r/pulls/5/merge",
    "gh api --method PUT repos/o/r/pulls/5/merge",
    "gh api --method=PUT repos/o/r/pulls/5/merge",
    "gh api -Xput repos/o/r/pulls/5/merge",
    "gh api repos/o/r/pulls/5/merge -XPUT",
    "gh api -X 'PUT' repos/o/r/pulls/5/merge",
])
def test_an_api_merge_with_any_method_spelling_asks_and_mints(repos, command):
    assert _decide(repos, command) is not None
    ctx = _mint(command)
    assert ctx["operation_type"] == "merge" and ctx["pr_number"] == "api:o/r#5"


@pytest.mark.parametrize("command", [
    "gh api -XDELETE repos/o/r/git/refs/heads/feature",
    "gh api --method=PATCH repos/o/r/git/refs/heads/main",
    "gh api -XDELETE repos/o/r/branches/main/protection",
    "gh api --method=PUT repos/o/r/contents/a.txt -f branch=main",
])
def test_the_other_api_arms_read_an_attached_method(repos, command):
    assert _decide(repos, command) is not None


@pytest.mark.parametrize("command", [
    "gh api -XGET repos/o/r/pulls/5/merge",
    "gh api --method=GET repos/o/r/pulls/5/merge",
    "gh api repos/o/r/pulls/5",
])
def test_an_api_read_runs(repos, command):
    assert _decide(repos, command) is None


@pytest.mark.parametrize("command", [
    "gh pr --repo o/r merge 5",
    "gh pr -R o/r merge 5",
    "gh pr --repo=o/r close 5",
    "gh -R o/r pr --repo o/r merge 5",
    "glab mr -R g/p merge 5",
    "glab mr --repo g/p accept 5",
    "git -c alias.p=push p -f origin feature",
    "git -c alias.p='!git push -f' p origin feature",
    "git -c alias.bd='branch -D' bd feature",
    "git -c alias.P=push P origin main",
    "git -c alias.p=q -c alias.q=push p origin main",
    "git -C . -c alias.p=push p origin main",
])
def test_a_shape_the_arms_cannot_read_is_refused_for_the_user(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    text = _decide(repos, command)
    assert text is not None and "`! <command>`" in text
    assert mgc.unmintable_danger(command) is not None


@pytest.mark.parametrize("command", [
    "gh pr --repo o/r view 5",
    "gh pr --repo o/r list",
    "glab mr -R g/p view 5",
    "git -c alias.s=status s",
    "git -c alias.p=push status",                                  # defined, not used
    "git -c alias.lg='log --oneline' lg",
])
def test_a_harmless_flag_or_alias_runs(repos, command):
    _git(repos["work"], "checkout", "-q", "feature")
    assert _decide(repos, command) is None
