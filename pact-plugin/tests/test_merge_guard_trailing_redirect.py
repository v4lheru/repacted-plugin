"""
Location: pact-plugin/tests/test_merge_guard_trailing_redirect.py
Summary: An output redirect at the end of an approved glab merge (`2>&1`, `> merge.log`,
         `2>&1 | tail -5`) no longer hides the target, so the approval of the bare command
         authorizes it. Everything an approval binds stays refused with a redirect
         appended: a bound flag, another repository, host or target, a second operation;
         so does a redirect that is not a plain trailing output redirect.

         Destructive verbs are assembled at runtime so this file stays inert to a live
         guard that reads the transcript.
"""

import pytest

from merge_guard_post import _mint_contexts_from_bundle, write_token
from merge_guard_pre import check_merge_authorization
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture (hermetic cwd + env)
from shared.merge_guard_common import extract_command_context
from shell.merge.context import drop_trailing_output_redirects

MERGE = "mer" + "ge"
GLAB_MERGE = "gl" + f"ab mr {MERGE} 13"
GH_MERGE = f"gh pr {MERGE} 13"


def _approve(command: str, token_dir) -> None:
    """The user's click on `Yes, merge` for `command`, minted and written as the hooks do."""
    question = {"question": "Ship it?", "multiSelect": False, "options": [
        {"label": f"Yes, {MERGE}", "description": f"Run `{command}`"},
        {"label": "Not yet", "description": "wait"}]}
    contexts, refusals = _mint_contexts_from_bundle([question], {"Ship it?": f"Yes, {MERGE}"})
    assert len(contexts) == 1, refusals
    assert write_token(contexts[0], token_dir=token_dir) is not None


FREE_SUFFIXES = [" 2>&1", " > merge.log", " 2>&1 | tail -5", " >>merge.log 2>&1", " 2> /dev/null",
                 " &>out.txt", " --squash 2>&1 | tail -5"]


@pytest.mark.parametrize("base", [GLAB_MERGE, GH_MERGE], ids=["glab", "gh"])
@pytest.mark.parametrize("suffix", FREE_SUFFIXES)
def test_trailing_output_redirect_keeps_the_approval(base, suffix, tmp_path):
    _approve(base, tmp_path)
    assert check_merge_authorization(base + suffix, token_dir=tmp_path) is None


def test_approval_shown_with_the_redirect_covers_the_bare_command(tmp_path):
    _approve(GLAB_MERGE + " 2>&1 | tail -5", tmp_path)
    assert check_merge_authorization(GLAB_MERGE, token_dir=tmp_path) is None


STAYS_REFUSED = {
    # what an approval binds, with a redirect appended
    "admin": GLAB_MERGE + " --admin 2>&1",
    "delete-branch-short": GLAB_MERGE + " -d 2>&1",
    "remove-source-branch": GLAB_MERGE + " --remove-source-branch > merge.log",
    "other-repo": GLAB_MERGE + " -R g/other 2>&1",
    "sha": GLAB_MERGE + " --sha 0123abc 2>&1",
    "host-variable": "GITLAB_HOST=gitlab.other.example " + GLAB_MERGE + " 2>&1",
    "other-target": GLAB_MERGE.replace("13", "14") + " 2>&1",
    "second-target-word": GLAB_MERGE + " 14 2>&1",
    "second-operation": GLAB_MERGE + " 2>&1 && " + GLAB_MERGE.replace("13", "14"),
    # not a plain trailing output redirect
    "expanded-file": GLAB_MERGE + ' > "$F"',
    "unquoted-expansion": GLAB_MERGE + " > $F",
    "substitution-file": GLAB_MERGE + " > $(mktemp)",
    "git-config": GLAB_MERGE + " > .git/config",
    "git-config-upper": GLAB_MERGE + " > .GIT/config",
    "nested-git-dir": GLAB_MERGE + " > ../other/.git/config",
    "client-config": GLAB_MERGE + " > /srv/app/.config/glab-cli/config.yml",
    "home-tilde": GLAB_MERGE + " > ~/merge.log",
    "redirect-not-trailing": GLAB_MERGE + " >x 14",
    "redirect-before-flag": GLAB_MERGE + " 2>&1 --yes",
    "input-redirect": GLAB_MERGE + " < file",
    "input-then-output": GLAB_MERGE + " < file 2>&1",
    "clobber": GLAB_MERGE + " >| merge.log",
    "quoted-file": GLAB_MERGE + " > 'merge log'",
    "glob-file": GLAB_MERGE + " > merge*.log",
    "two-file-words": GLAB_MERGE + " > a b",
    "fd-to-word": GLAB_MERGE + " >&merge.log",
    "process-substitution": GLAB_MERGE + " > >(tee x)",
    "unicode-digit-fd": GLAB_MERGE + " ２>&1",
}


@pytest.mark.parametrize("name", STAYS_REFUSED)
def test_stays_refused_after_approving_the_bare_merge(name, tmp_path):
    _approve(GLAB_MERGE, tmp_path)
    assert check_merge_authorization(STAYS_REFUSED[name], token_dir=tmp_path) is not None


BOUND_LONG_FLAGS = {
    "gh-delete-branch": (GH_MERGE, " --delete-branch 2>&1"),
    "gh-repo": (GH_MERGE, " --repo o/other > merge.log"),
    "gh-match-head-commit": (GH_MERGE, " --match-head-commit 0123abc 2>&1"),
    "glab-repo": (GLAB_MERGE, " --repo g/other 2>&1"),
}


@pytest.mark.parametrize("name", BOUND_LONG_FLAGS)
def test_a_bound_long_flag_with_a_redirect_stays_refused(name, tmp_path):
    base, suffix = BOUND_LONG_FLAGS[name]
    _approve(base, tmp_path)
    assert check_merge_authorization(base + suffix, token_dir=tmp_path) is not None


@pytest.mark.parametrize("leg", [
    "gl" + f"ab mr {MERGE} feature2>x",        # the shell reads fd 2 only after a space
    "gl" + f"ab mr {MERGE} 13 -m 'done 2>&1'",  # inside a quote it is text
    "gl" + f"ab mr {MERGE} 13 -m \"done 2>&1",  # a quote left open cannot be read
    "gl" + f"ab mr {MERGE} 13 \\> x",
    "2>&1",                                     # nothing but a redirect
    "",
])
def test_only_a_real_trailing_redirect_is_dropped(leg):
    assert drop_trailing_output_redirects(leg) == leg


def test_the_redirect_changes_no_other_part_of_the_identity():
    bare = extract_command_context(GLAB_MERGE + " --admin")
    assert extract_command_context(GLAB_MERGE + " --admin 2>&1 > merge.log") == bare


def test_mismatch_message_says_what_binds(tmp_path):
    _approve(GLAB_MERGE, tmp_path)
    reason = check_merge_authorization(GLAB_MERGE.replace("13", "14"), token_dir=tmp_path)
    assert "must be identical" not in reason
    for part in ("operation", "target", "repository", "host", "--admin", "--remove-source-branch",
                 "--sha", "output redirect"):
        assert part in reason, part
