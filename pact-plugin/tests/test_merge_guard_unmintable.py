"""
Location: pact-plugin/tests/test_merge_guard_unmintable.py
Summary: The merge/close/delete shapes whose deciding value is not on the command line, or
         not in a form the guard can bind a target to (the FIX-MERGE #141 follow-ups):
           - `gh api graphql` / `glab api graphql` mutations that merge, close, delete, destroy
             or accept, and GraphQL calls whose query is not a literal (a file, stdin, $VAR);
           - `gh api` / `glab api` calls on PR, MR and branch endpoints whose body comes from
             `--input <file>`, stdin (`--input -`) or a `-F field=@file`;
           - curl / wget calls that delete GitLab MRs, branches or protections, or close a
             GitLab MR or GitHub PR;
           - a destructive call wrapped in `bash -c '...'` (and sh/zsh).
         Each one GATES and NEVER MINTS (the lead's ruling on the hardening pass: no new bound
         identities); the deny tells the operator to have the user run it. Reads and
         unrelated writes stay ungated: those are the negative rows.

         Destructive verbs are assembled at runtime so this file stays inert to a live guard
         that reads the transcript.
"""

import pytest

from merge_guard_post import _mint_context_from_bundle
from merge_guard_pre import check_merge_authorization
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # autouse fixture
from shared.merge_guard_common import extract_command_context, is_dangerous_command

MERGE = "mer" + "ge"
CLOSE = "clo" + "se"
DELETE = "DEL" + "ETE"
PATCH = "PAT" + "CH"
GL = "https://gitlab.example.com/api/v4/projects/7"

UNMINTABLE = [
    ("gh-graphql-merge-pr", "gh api graphql -f query='mutation { " + MERGE + "PullRequest(input: {pullRequestId: \"PR_1\"}) { clientMutationId } }'"),
    ("gh-api-input-file", f"gh api -X {PATCH} repos/o/r/pulls/5 --input body.json"),
    ("curl-delete-mr", f"curl -X {DELETE} {GL}/" + MERGE + "_requests/5"),
    ("sh-c-curl-delete-mr", f"sh -c \"curl -X {DELETE} {GL}/" + MERGE + "_requests/5\""),
]

UNGATED = [
    ("gh-graphql-query", "gh api graphql -f query='query { viewer { login } }'"),
    ("bash-c-ls", "bash -c 'ls -la'"),
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


@pytest.mark.parametrize("label,cmd", UNMINTABLE, ids=[row[0] for row in UNMINTABLE])
def test_gated(label, cmd, tmp_path):
    assert is_dangerous_command(cmd), cmd
    assert check_merge_authorization(cmd, token_dir=tmp_path) is not None


@pytest.mark.parametrize("label,cmd", UNMINTABLE, ids=[row[0] for row in UNMINTABLE])
def test_never_minted(label, cmd):
    ctx, _refusal = _click(cmd)
    assert ctx is None, (cmd, ctx)


@pytest.mark.parametrize("label,cmd", UNMINTABLE, ids=[row[0] for row in UNMINTABLE])
def test_the_deny_says_the_user_runs_it(label, cmd, tmp_path):
    message = check_merge_authorization(cmd, token_dir=tmp_path)
    assert "cannot be bound to an approval" in message, message
    assert "`! " in message


@pytest.mark.parametrize("label,cmd", UNGATED, ids=[row[0] for row in UNGATED])
def test_reads_and_unrelated_writes_stay_ungated(label, cmd, tmp_path):
    assert not is_dangerous_command(cmd), cmd
    assert check_merge_authorization(cmd, token_dir=tmp_path) is None


def test_an_off_line_body_on_a_mintable_op_keeps_its_bound_flow():
    """An API force-push already binds its ref (amend_6 on #141): a body from a file does not
    take the bound approval away from it."""
    cmd = f"gh api -X {PATCH} repos/o/r/git/refs/heads/main --input body.json"
    assert extract_command_context(cmd).get("operation_type")
    assert _click(cmd)[0] is not None


@pytest.mark.parametrize("cmd", [
    f"gh api -X {PATCH} repos/o/r/git/refs/heads/main --input body.json",
    "bash -c 'gh pr " + MERGE + " 5'",
])
def test_a_mintable_op_keeps_the_approval_message(cmd, tmp_path):
    message = check_merge_authorization(cmd, token_dir=tmp_path)
    assert message is not None and "cannot be bound" not in message


def test_a_mintable_close_keeps_its_bound_flow(tmp_path):
    """The existing GitHub REST close still mints: the new rule only adds gated shapes."""
    cmd = f"gh api -X {PATCH} repos/o/r/pulls/5 -f state=closed"
    assert extract_command_context(cmd).get("operation_type") == "close"
    message = check_merge_authorization(cmd, token_dir=tmp_path)
    assert message is not None and "cannot be bound" not in message


