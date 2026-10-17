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
    ("gh-graphql-close-pr", "gh api graphql -f query='mutation { " + CLOSE + "PullRequest(input: {pullRequestId: \"PR_1\"}) { clientMutationId } }'"),
    ("gh-graphql-delete-ref", "gh api graphql -f query='mutation { deleteRef(input: {refId: \"R_1\"}) { clientMutationId } }'"),
    ("glab-graphql-accept", "glab api graphql -f query='mutation { " + MERGE + "RequestAccept(input: {projectPath: \"g/p\", iid: \"5\"}) { errors } }'"),
    ("glab-graphql-update-closed", "glab api graphql -f query='mutation { " + MERGE + "RequestUpdate(input: {projectPath: \"g/p\", iid: \"5\", state: CLOSED}) { errors } }'"),
    ("glab-graphql-destroy", "glab api graphql -f query='mutation { destroy" + MERGE.title() + "Request(input: {id: \"gid://gitlab/MergeRequest/1\"}) { errors } }'"),
    ("gh-graphql-query-from-file", "gh api graphql -F query=@mutation.graphql"),
    ("gh-graphql-query-from-variable", 'gh api graphql -f query="$Q"'),
    ("gh-graphql-input", "gh api graphql --input body.json"),
    ("gh-api-input-file", f"gh api -X {PATCH} repos/o/r/pulls/5 --input body.json"),
    ("gh-api-input-stdin", f"echo '{{\"state\":\"closed\"}}' | gh api -X {PATCH} repos/o/r/pulls/5 --input -"),
    ("gh-api-input-no-method", "gh api repos/o/r/pulls/5/" + MERGE + " --input body.json"),
    ("gh-api-field-file", f"gh api -X {PATCH} repos/o/r/pulls/5 -F state=@state.txt"),
    ("glab-api-input", "glab api -X PUT projects/g%2Fp/" + MERGE + "_requests/5 --input body.json"),
    ("curl-delete-mr", f"curl -X {DELETE} {GL}/" + MERGE + "_requests/5"),
    ("curl-delete-mr-attached", f"curl -X{DELETE} -H 'PRIVATE-TOKEN: $T' {GL}/" + MERGE + "_requests/5"),
    ("curl-delete-branch", f"curl --request {DELETE} {GL}/repository/branches/feature%2Fx"),
    ("curl-delete-protection", f"curl -X {DELETE} {GL}/protected_branches/main"),
    ("curl-close-mr-query", f"curl -X PUT '{GL}/" + MERGE + "_requests/5?state_event=" + CLOSE + "'"),
    ("curl-close-mr-body-file", f"curl -X PUT {GL}/" + MERGE + "_requests/5 -d @close.json"),
    ("wget-delete-branch", f"wget --method={DELETE} {GL}/repository/branches/feature"),
    ("curl-close-gh-pr", f"curl -X {PATCH} https://api.github.com/repos/o/r/pulls/5 -d '{{\"state\":\"closed\"}}'"),
    ("bash-c-gh-api-close", f"bash -c 'gh api -X {PATCH} repos/o/r/pulls/5 -f state=closed'"),
    ("sh-c-curl-delete-mr", f"sh -c \"curl -X {DELETE} {GL}/" + MERGE + "_requests/5\""),
]

UNGATED = [
    ("gh-graphql-query", "gh api graphql -f query='query { viewer { login } }'"),
    ("gh-graphql-query-reads-merge-fields", "gh api graphql -f query='query { repository(owner: \"o\", name: \"r\") { pullRequest(number: 5) { " + MERGE + "Commit { oid } } } }'"),
    ("gh-graphql-mutation-other", "gh api graphql -f query='mutation { addStar(input: {starrableId: \"R_1\"}) { clientMutationId } }'"),
    ("gh-api-issue-input", "gh api -X POST repos/o/r/issues --input issue.json"),
    ("gh-api-pr-get-input", "gh api -X GET repos/o/r/pulls --input filter.json"),
    ("gh-api-pr-view", "gh api repos/o/r/pulls/5"),
    ("glab-api-mr-get", "glab api projects/g%2Fp/" + MERGE + "_requests/5"),
    ("curl-get-mr", f"curl -s {GL}/" + MERGE + "_requests/5"),
    ("curl-delete-note", f"curl -X {DELETE} {GL}/issues/1/notes/2"),
    ("echo-mentions-delete", f"echo 'curl -X {DELETE} {GL}/" + MERGE + "_requests/5'"),
    ("bash-c-read", "bash -c 'gh pr view 5'"),
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


def test_sabotage_the_raw_leg_rule_is_what_gates_them(monkeypatch):
    import shared.merge_guard_common as mgc

    cmd = UNMINTABLE[0][1]
    assert is_dangerous_command(cmd)
    monkeypatch.setattr(mgc, "unmintable_danger", lambda command, _depth=0: None)
    assert not is_dangerous_command(cmd)
