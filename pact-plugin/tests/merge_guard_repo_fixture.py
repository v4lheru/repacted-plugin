"""
Location: pact-plugin/tests/merge_guard_repo_fixture.py
Summary: The synthetic checkout the merge-guard tests run from (amend_3 on #141). A CLI
         merge/close/mr-delete approval binds the repo the command runs in, so every
         merge-guard test module that exercises CLI approvals imports the autouse fixture
         below: the hooks then resolve that repo as `o/r` (origin scp-style github.com:o/r.git,
         branch main), and a hand-seeded CLI token carries "repo": "o/r" and
         "host": "github.com". The repo/host redirect variables are cleared so the
         session's own environment never leaks into an identity (amend_5).
         A hand-built context names an operation and its target only; the fixture completes
         it with the whole-command binding (`typed`, or `wrapper` and `cwd`) of the command it
         is matched against, so those tests keep failing or passing on the axis they name.
         A context that carries one of those keys (every minted one does) is left as it is;
         merge_guard_pre._token_matches_command.__wrapped__ is the match without completion.
"""
import functools

import pytest

REPO = "o/r"
HOST = "github.com"
REDIRECT_VARS = ("GH_REPO", "GH_HOST", "GL_REPO", "GL_HOST", "GITLAB_REPO", "GITLAB_HOST")
COMMAND_BINDING_KEYS = frozenset({"typed", "wrapper", "cwd"})


def with_command_binding(context: dict, command: str) -> dict:
    """`context` with the whole-command binding of `command`, when it is a hand-built one
    (a dict carrying none of COMMAND_BINDING_KEYS); else `context` as it is."""
    from shared.merge_guard_common import approval_binding

    if not isinstance(context, dict) or COMMAND_BINDING_KEYS & context.keys():
        return context
    return {**context, **approval_binding(command)}


def take_out_typed(monkeypatch) -> None:
    """Take the word-for-word `typed` axis out of mint, read and retire, for a known-bad test
    that removes one older binding and expects the escalation it closes to reopen: `typed`
    closes it as well, so the older binding is the attributable closer only without it."""
    from shell.merge import binding

    monkeypatch.setattr(binding, "typed_binding", lambda command: None)


def _loaded(name: str):
    """The hook module `name`, or None when it cannot be loaded here (a hook reports its
    own load failure and exits)."""
    import importlib

    try:
        return importlib.import_module(name)
    except (Exception, SystemExit):  # noqa: BLE001 - the tests of that failure own it
        return None


def _complete_hand_built_tokens(monkeypatch) -> None:
    """Make the pre hook's token match and the post hook's retire read a hand-built context
    as with_command_binding completes it."""
    pre, post = _loaded("merge_guard_pre"), _loaded("merge_guard_post")
    if pre is not None and not hasattr(pre._token_matches_command, "__wrapped__"):
        original = pre._token_matches_command

        @functools.wraps(original)
        def matches(token: dict, command: str) -> bool:
            if isinstance(token, dict) and isinstance(token.get("context"), dict):
                token = {**token, "context": with_command_binding(token["context"], command)}
            return original(token, command)

        monkeypatch.setattr(pre, "_token_matches_command", matches)
    if post is not None and not hasattr(post._same_command_binding, "__wrapped__"):
        same = post._same_command_binding

        @functools.wraps(same)
        def same_or_hand_built(ctx: dict, cmd_binding: dict) -> bool:
            return not COMMAND_BINDING_KEYS & ctx.keys() or same(ctx, cmd_binding)

        monkeypatch.setattr(post, "_same_command_binding", same_or_hand_built)


@pytest.fixture(autouse=True)
def cli_repo_is_o_r(tmp_path_factory, monkeypatch):
    import shared.merge_guard_common as mgc

    for name in REDIRECT_VARS:
        monkeypatch.delenv(name, raising=False)

    root = tmp_path_factory.mktemp("repo-o-r")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text('[remote "origin"]\n\turl = git' '@github.com:o/r.git\n')
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    mgc.set_invocation_cwd(str(root))
    _complete_hand_built_tokens(monkeypatch)
    yield root
    mgc.set_invocation_cwd(None)


def minted_context(command: str) -> dict:
    """The context the post hook mints for `command` (extract + the repo/host binding the
    mint applies), for tests that simulate a mint without an AskUserQuestion bundle."""
    from shared.merge_guard_common import command_repo_binding, extract_command_context

    ctx = extract_command_context(command)
    bound = command_repo_binding(ctx, command)
    assert bound is not None, "repo unresolvable for %r" % command
    return bound


def git_bound(context: dict, command: str) -> dict:
    """`context` with the checkout and remote binding the hooks give a git push or branch op
    of `command` (1.4.1 U13), for tests that hand-build the approval of that command. Any
    other context, or one that already names its checkout, is returned as it is."""
    from shared.merge_guard_common import _GIT_BOUND_OPS, command_repo_binding

    if not isinstance(context, dict) or context.get("operation_type") not in _GIT_BOUND_OPS \
            or "repo" in context:
        return context
    return command_repo_binding(context, command) or context
