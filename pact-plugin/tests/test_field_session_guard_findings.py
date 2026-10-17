"""
Location: pact-plugin/tests/test_field_session_guard_findings.py
Summary: Regression and boundary tests for the guard findings of the acme 60b548dc field
         session (docs/reviews/2026-10-06-session/acme-60b548dc-findings.md):
           1   one AskUserQuestion that approves several merges mints one approval each;
           8   a merge from a directory that is no checkout says so, with the -R form, on
               the first deny;
           3   `git worktree add && git merge --ff-only` from a non-repo directory is not
               denied with "git log exited 128";
           31  awk program text (`{print $6,$7,$8}`) is not read as a computed file name;
           34  `export R=<dir>; cd "$R" && git add` is read like `R=<dir>; cd "$R" && ...`.
         Each loosening carries the rows that must stay closed beside it.
Used by: the plugin suite. Every secret-shaped value is assembled at run time.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import env_guard
import merge_guard_post as post
import merge_guard_pre as pre
import shared.merge_guard_common as mgc
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture
from project_tasks_support import GIT_ENV

HOOKS = Path(__file__).parent.parent / "hooks"
MERGE = "gh pr merge"
YES = "Yes, merge"


# ── merge guard: findings 1 and 8 ────────────────────────────────────────────────────────
def _question(text: str, command: str, multi: bool = False) -> dict:
    return {"question": text, "multiSelect": multi, "options": [
        {"label": YES, "description": f"Run `{command}` to merge this PR"},
        {"label": "Not yet", "description": "Hold the merge"}]}


def _approve(monkeypatch, token_dir: Path, questions: list, answers: dict) -> None:
    """Run the post hook's main() on one answered AskUserQuestion bundle."""
    payload = {"tool_name": "AskUserQuestion", "session_id": "acme-test",
               "tool_input": {"questions": questions}, "tool_response": {"answers": answers}}
    monkeypatch.setattr(post, "TOKEN_DIR", token_dir)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as done:
        post.main()
    assert done.value.code == 0


def _live(token_dir: Path) -> list:
    return [p for p in token_dir.iterdir()
            if not p.name.endswith(".consumed") and mgc.USE_MARKER_SUFFIX not in p.name]


def _allowed(command: str, token_dir: Path) -> bool:
    return pre.check_merge_authorization(command, token_dir=token_dir) is None


def test_one_bundle_approving_two_merges_mints_an_approval_for_each(monkeypatch, tmp_path):
    questions = [_question("Merge PR 172?", f"{MERGE} 172"),
                 _question("Merge PR 185?", f"{MERGE} 185"),
                 {"question": "Ship the CTA copy as drafted?", "multiSelect": False,
                  "options": [{"label": "Yes", "description": "Use the draft"},
                              {"label": "No", "description": "Rewrite it"}]}]
    _approve(monkeypatch, tmp_path, questions, {
        "Merge PR 172?": YES, "Merge PR 185?": YES, "Ship the CTA copy as drafted?": "Yes"})
    assert len(_live(tmp_path)) == 2
    assert not _allowed(f"{MERGE} 190", tmp_path)
    assert _allowed(f"{MERGE} 172", tmp_path)
    assert _allowed(f"{MERGE} 185", tmp_path)


def test_a_token_for_one_pr_never_approves_another_pr_or_another_repo(monkeypatch, tmp_path,
                                                                    tmp_path_factory):
    _approve(monkeypatch, tmp_path, [_question("Merge PR 172?", f"{MERGE} 172")],
             {"Merge PR 172?": YES})
    assert not _allowed(f"{MERGE} 185", tmp_path)
    assert not _allowed(f"{MERGE} 172 -R other/repo", tmp_path)
    assert not _allowed(f"{MERGE} 172 --admin", tmp_path)
    assert not _allowed(f"{MERGE} 172 && {MERGE} 185", tmp_path)
    elsewhere = tmp_path_factory.mktemp("repo-x-y")
    (elsewhere / ".git").mkdir()
    (elsewhere / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/x/y.git\n')
    (elsewhere / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    from shell.merge import remotes
    here = remotes._INVOCATION_CWD
    mgc.set_invocation_cwd(str(elsewhere))
    try:
        assert not _allowed(f"{MERGE} 172", tmp_path), "same PR number, another repo"
    finally:
        mgc.set_invocation_cwd(here)
    assert _allowed(f"{MERGE} 172", tmp_path)


def _two_commands():
    return [{"question": "Merge both?", "multiSelect": False, "options": [
        {"label": YES, "description": f"Run `{MERGE} 172` and `{MERGE} 173`"}]}], {"Merge both?": YES}


def _compound():
    return [_question("Merge both?", f"{MERGE} 172 && {MERGE} 173")], {"Merge both?": YES}


def _multi_select():
    return [_question("Merge PR 172?", f"{MERGE} 172", multi=True)], {"Merge PR 172?": YES}


def _free_text():
    return [_question("Merge PR 172?", f"{MERGE} 172")], {"Merge PR 172?": f"sure, {MERGE} 172"}


def _declined():
    return [_question("Merge PR 172?", f"{MERGE} 172")], {"Merge PR 172?": "Not yet"}


def _same_question_twice():
    return ([_question("Merge?", f"{MERGE} 172"), _question("Merge?", f"{MERGE} 173")],
            {"Merge?": YES})


def _fullwidth_digits():
    return [_question("Merge PR 172?", f"{MERGE} １７２")], {"Merge PR 172?": YES}


def _arabic_digits():
    return [_question("Merge PR 172?", f"{MERGE} ١٧٢")], {"Merge PR 172?": YES}


def _lookalike_repo():
    return ([_question("Merge PR 172?", f"{MERGE} 172 -R o/\u0433")],     # Cyrillic, not `r`
            {"Merge PR 172?": YES})


def _command_in_question_only():
    return ([{"question": f"Run `{MERGE} 172`?", "multiSelect": False,
              "options": [{"label": "Yes", "description": "Go ahead"}]}],
            {f"Run `{MERGE} 172`?": "Yes"})


@pytest.mark.parametrize("bundle", [
    _two_commands, _compound, _multi_select, _declined, _same_question_twice,
    _fullwidth_digits, _arabic_digits, _lookalike_repo, _command_in_question_only])
def test_an_option_that_does_not_name_exactly_one_command_mints_nothing(bundle, monkeypatch,
                                                                       tmp_path):
    """Beside a sibling question that does mint: the sibling's approval is all there is."""
    questions, answers = bundle()
    questions.append(_question("Merge PR 185?", f"{MERGE} 185"))
    answers["Merge PR 185?"] = YES
    _approve(monkeypatch, tmp_path, questions, answers)
    assert len(_live(tmp_path)) == 1
    assert not _allowed(f"{MERGE} 172", tmp_path)
    assert not _allowed(f"{MERGE} 173", tmp_path)
    assert _allowed(f"{MERGE} 185", tmp_path)


def test_empty_huge_and_oversized_bundles_mint_nothing(monkeypatch, tmp_path):
    five = [_question(f"Merge PR {n}?", f"{MERGE} {n}") for n in range(201, 206)]
    _approve(monkeypatch, tmp_path, five, {q["question"]: YES for q in five})
    assert _live(tmp_path) == [], "more approvals than one bundle can carry"
    _approve(monkeypatch, tmp_path, [], {})
    junk = [{"question": f"q{n}", "multiSelect": False, "options": [
        {"label": YES, "description": "x" * 2000 + f" {MERGE} " + "9" * 600}]} for n in range(300)]
    junk += [None, 7, {"question": None, "options": "no"}, {"options": [{"label": YES}]}]
    _approve(monkeypatch, tmp_path, junk, {f"q{n}": YES for n in range(300)})
    assert _live(tmp_path) == []


def test_a_re_ask_for_the_same_binding_replaces_its_token(monkeypatch, tmp_path):
    bundle = [_question("Merge PR 172?", f"{MERGE} 172")], {"Merge PR 172?": YES}
    _approve(monkeypatch, tmp_path, *bundle)
    _approve(monkeypatch, tmp_path, *bundle)
    assert len(_live(tmp_path)) == 1
    _approve(monkeypatch, tmp_path, [_question("Merge PR 185?", f"{MERGE} 185")],
             {"Merge PR 185?": YES})
    assert len(_live(tmp_path)) == 2, "another binding adds its own token"


def test_an_unused_sibling_token_expires_and_is_cleaned_up(monkeypatch, tmp_path):
    questions = [_question("Merge PR 172?", f"{MERGE} 172"),
                 _question("Merge PR 185?", f"{MERGE} 185")]
    _approve(monkeypatch, tmp_path, questions, {q["question"]: YES for q in questions})
    for path in _live(tmp_path):
        data = json.loads(path.read_text())
        if data["context"].get("pr_number") == "185":
            data["expires_at"] = time.time() - 1
            path.write_text(json.dumps(data))
            expired = path
    assert _allowed(f"{MERGE} 172", tmp_path)
    assert not expired.exists()
    assert not _allowed(f"{MERGE} 185", tmp_path)


def _expire(path: Path) -> None:
    data = json.loads(path.read_text())
    data["expires_at"] = time.time() - 1
    path.write_text(json.dumps(data))


def test_an_expired_token_never_validates_even_while_its_file_is_on_disk(monkeypatch, tmp_path):
    _approve(monkeypatch, tmp_path, [_question("Merge PR 172?", f"{MERGE} 172")],
             {"Merge PR 172?": YES})
    (expired,) = _live(tmp_path)
    _expire(expired)
    monkeypatch.setattr(pre, "_safe_remove", lambda path: None)      # the delete fails
    assert not _allowed(f"{MERGE} 172", tmp_path)
    assert expired.exists()


def test_the_next_mint_deletes_an_expired_token_and_keeps_a_live_sibling(monkeypatch, tmp_path):
    questions = [_question("Merge PR 172?", f"{MERGE} 172"),
                 _question("Merge PR 185?", f"{MERGE} 185")]
    _approve(monkeypatch, tmp_path, questions, {q["question"]: YES for q in questions})
    expired = next(p for p in _live(tmp_path) if '"172"' in p.read_text())
    _expire(expired)
    _approve(monkeypatch, tmp_path, [_question("Merge PR 190?", f"{MERGE} 190")],
             {"Merge PR 190?": YES})
    # (the new token may take the deleted file's name: read what is there, not the name)
    assert sorted(json.loads(p.read_text())["context"]["pr_number"]
                  for p in tmp_path.iterdir()) == ["185", "190"]
    assert _allowed(f"{MERGE} 185", tmp_path) and _allowed(f"{MERGE} 190", tmp_path)


def test_a_merge_outside_a_checkout_names_the_repo_form_on_the_first_deny(monkeypatch, tmp_path,
                                                                        tmp_path_factory):
    from shell.merge import remotes
    tokens = tmp_path_factory.mktemp("tokens")
    _approve(monkeypatch, tokens, [_question("Merge PR 185?", f"{MERGE} 185")],
             {"Merge PR 185?": YES})                     # approved inside o/r
    here = remotes._INVOCATION_CWD
    mgc.set_invocation_cwd(str(tmp_path))                # a parent folder, no checkout
    try:
        reason = pre.check_merge_authorization(f"{MERGE} 185", token_dir=tokens)
        assert reason and f"`{MERGE} 185 -R OWNER/REPO`" in reason
        named = pre.check_merge_authorization(f"{MERGE} 185 -R o/r", token_dir=tokens)
        # -R is part of the binding: the approval given without it does not cover this form
        assert named and "cannot be determined" not in named and "OWNER/REPO" not in named
        hidden = pre.check_merge_authorization(f"{MERGE} 185; {MERGE} 186", token_dir=tokens)
        assert hidden is not None
        _approve(monkeypatch, tokens, [_question("Merge PR 185?", f"{MERGE} 185 -R o/r")],
                 {"Merge PR 185?": YES})
        assert _allowed(f"{MERGE} 185 -R o/r", tokens)
        assert not _allowed(f"{MERGE} 185 -R o/other", tokens)
    finally:
        mgc.set_invocation_cwd(here)


# ── git_commit_check: finding 3 ──────────────────────────────────────────────────────────
def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV})


def _secret_line() -> str:
    return "TOKEN = '" + "ghp_" + "a1B2c3D4e5" * 3 + "f6G7h8" + "'"


@pytest.fixture
def parent(tmp_path):
    """A non-repo parent folder holding the checkout `repo`: main has one commit; `feat` is
    one clean commit ahead; `leak` is one commit ahead that adds a secret-shaped line."""
    from shared.scrub import secret_kind
    assert secret_kind(_secret_line()), "the fixture line must be one the detector knows"
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitignore").write_text(".env\n")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "feat")
    (repo / "b.py").write_text("y = 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "feat")
    _git(repo, "checkout", "-q", "-b", "leak", "main")
    (repo / "c.py").write_text(_secret_line() + "\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "leak")
    _git(repo, "checkout", "-q", "main")
    return tmp_path


def _commit_check(command: str, cwd: Path) -> subprocess.CompletedProcess:
    payload = {"tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}
    return subprocess.run([sys.executable, str(HOOKS / "git_commit_check.py")],
                          input=json.dumps(payload), capture_output=True, text=True,
                          cwd=cwd, env={**os.environ, **GIT_ENV})


@pytest.mark.parametrize("command", [
    "git worktree add ../wt -b topic && git merge --ff-only feat",
    "git -C repo worktree add ../wt -b topic main && git -C wt merge --ff-only feat",
    "git -C repo worktree add ../wt -b topic main && cd wt && git merge --ff-only feat",
])
def test_worktree_add_then_merge_where_git_finds_no_repository_is_denied_with_the_form_to_use(
        command, parent):
    """The field case (a new worktree and a merge into it in one command) is no longer read:
    it is denied with a text that says what to run instead, not with `exited 128`."""
    done = _commit_check(command, parent)
    assert done.returncode == 2, done.stderr
    assert "as its own command" in done.stdout + done.stderr
    assert "exited 128" not in done.stdout + done.stderr


@pytest.mark.parametrize("command", [
    # the merge into the new worktree is still read, in the repository that adds it
    "git -C repo worktree add ../wt -b topic main && git -C wt merge --ff-only leak",
    "git -C repo worktree add ../wt -b topic main && cd wt && git merge leak",
    "git -C repo worktree add ../wt -b topic main && git -C wt cherry-pick leak",
    # nothing names what the new checkout would merge: not readable ahead
    "git -C repo worktree add ../wt -b topic main && git -C wt merge",
    # a directory made later by something else, or turned into a repository by the command
    "git -C wt merge --ff-only leak",
    "mkdir wt && git -C wt merge --ff-only leak",
    "git init && git merge --ff-only leak",
    "git clone repo wt && git -C wt merge --ff-only origin/leak",
    "git worktree add . leak; git merge --ff-only leak",
    # a second route hidden behind the allowed pair, in the real repository
    "git worktree add ../wt -b topic && git merge --ff-only feat; git -C repo merge leak",
])
def test_a_route_that_brings_a_secret_or_cannot_be_read_is_still_denied(command, parent):
    done = _commit_check(command, parent)
    assert done.returncode == 2, command


def test_a_real_commit_behind_the_worktree_pair_still_fails_closed(parent):
    repo = parent / "repo"
    (repo / "d.py").write_text(_secret_line() + "\n")
    _git(repo, "add", "d.py")
    done = _commit_check("git worktree add ../wt -b topic && git merge --ff-only feat "
                         "&& git commit -m 'add d'", repo)
    assert done.returncode == 2


# ── env_guard: findings 31 and 34 ────────────────────────────────────────────────────────
@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    _git(root, "init", "-q")
    (root / "src" / "a.py").write_text("x = 1\n")
    (root / ".gitignore").write_text(".env\n")
    return root


@pytest.mark.parametrize("command", [
    "ls -la /Users/someone/.claude/tasks/session-1/44.json | awk '{print $6,$7,$8}'",
    "ls -l tasks/44.json | awk -F' ' -v n=3 '{print $6,$7,$8}'",
    "awk '{print $6,$7,$8}' listing.txt",
])
def test_awk_field_references_are_not_a_computed_env_name(command, tmp_path):
    assert env_guard.bash_verdict(command, str(tmp_path)) is None


@pytest.mark.parametrize("command", [
    "awk '{print $6,$7,$8}' .env",
    "awk '{print $6,$7,$8}' \"$x.env\"",
    "awk '{print $6,$7,$8}' listing.txt \"${f}v\"",
    "awk -v n=3 '{print $6,$7,$8}' prod.env",
    "awk -f '{print $6,$7,$8}' \"$x.env\"",
    "awk -f prog.awk '{print $6,$7,$8}'",
    "awk --posix '{print $6,$7,$8}'",
    "awk -- '{print $6,$7,$8}' \"$x.env\"",
    "awk '{print $6,$7,$8}'; cat .env",
    "awk '{print $6,$7,$8}' && cat \"$x.env\"",
    "cat '{print $6,$7,$8}'",
    "sed '{print $6,$7,$8}'",
    "awk '{print $６}' .env",
    "awk '" + "{print $6,$7,$8};" * 20000 + "' .env",
])
def test_env_reads_behind_an_awk_program_are_still_blocked(command, tmp_path):
    assert env_guard.bash_verdict(command, str(tmp_path)), command[:80]


@pytest.mark.parametrize("groups", [40, 20000])
def test_an_awk_program_with_too_many_brace_lists_is_blocked_at_once(groups, tmp_path):
    """3**40 expansions cannot be followed: the answer is a block, not a hang."""
    command = "awk '" + "{print $6,$7,$8};" * groups + "' listing.txt"
    assert env_guard.bash_verdict(command, str(tmp_path))
    assert env_guard.bash_verdict("awk '" + "{print $6,$7,$8};" * 6 + "' listing.txt",
                                  str(tmp_path)) is None


@pytest.mark.parametrize("opening", [
    'export R="{root}"; ', 'export R={root} && ', "export R='{root}'\n", 'R="{root}"; '])
def test_staging_after_cd_to_a_literal_directory_variable_passes(opening, project, tmp_path):
    command = opening.format(root=project) + 'cd "$R" && git add src/a.py'
    assert env_guard.bash_verdict(command, str(tmp_path)) is None


@pytest.mark.parametrize("command", [
    'cd "$R" && git add src/a.py',                                  # set nowhere in the command
    'export R=$(echo {root}); cd "$R" && git add src/a.py',         # computed
    'export R={root}; R=/tmp; cd "$R" && git add src/a.py',         # set again
    'export R={root}; export R=/tmp; cd "$R" && git add src/a.py',
    'export R={root}; read R; cd "$R" && git add src/a.py',
    'export R={root}; eval "$Z"; cd "$R" && git add src/a.py',      # a setter this cannot see
    'export R={root} S=/tmp; cd "$S" && git add -A',                # not the one-name form
    'export -n R={root}; cd "$R" && git add src/a.py',
    'true; export R={root}; cd "$R" && git add src/a.py',           # not the command's opening
    'export R={root}; cd "$R" && git add .env',                     # stages an env file
    'export R={root}; cd "$R" && git add -A',                       # ... through -A
    'export R={root}; cd "$R" && cat .env',
    'export F=.env; cat "$F"',
    'export F=.en; cat "${{F}}v"',
    'export R={root}/‮src; cd "$R" && git add a.py',           # a directory that is not there
])
def test_unknown_directories_and_env_files_stay_blocked_behind_export(command, project, tmp_path):
    (project / ".env").write_text("inert-marker\n")
    if "-A" in command:
        (project / ".gitignore").unlink()                # nothing keeps -A from the env file
    assert env_guard.bash_verdict(command.format(root=project), str(tmp_path)), command


# ── review findings: approvals (FIX 2, NOTE 6, NOTE 7) ───────────────────────────────────
@pytest.mark.parametrize("bundle", [
    _free_text,
    lambda: ([_question("Merge PR 172?", f"{MERGE} 172")],
             {"Merge PR 172?": "no - stop, do not merge anything yet"})])
def test_a_typed_answer_anywhere_in_the_bundle_mints_nothing_at_all(bundle, monkeypatch, tmp_path):
    questions, answers = bundle()
    questions.append(_question("Merge PR 185?", f"{MERGE} 185"))
    answers["Merge PR 185?"] = YES
    _approve(monkeypatch, tmp_path, questions, answers)
    assert _live(tmp_path) == []
    assert not _allowed(f"{MERGE} 185", tmp_path) and not _allowed(f"{MERGE} 172", tmp_path)


@pytest.mark.parametrize("label, description, mints", [
    ("Not yet", f"Do NOT run `{MERGE} 172`", 0),
    ("No", f"Skip `{MERGE} 172`", 0),
    ("Cancel", f"Run `{MERGE} 172`", 0),
    ("Merge later", f"Run `{MERGE} 172`", 0),
    ("Approve after CI", f"Run `{MERGE} 172`", 0),
    ("Yes, but not yet", f"Run `{MERGE} 172`", 0),
    ("Don’t merge", f"Run `{MERGE} 172`", 0),
    ("Yеs", f"Run `{MERGE} 172`", 0),                 # Cyrillic e
    ("Yesterday's plan", f"Run `{MERGE} 172`", 0),
    ("Sure", f"Run `{MERGE} 172`", 0),
    (f"Do not run `{MERGE} 172`", "", 0),
    ("", f"Run `{MERGE} 172`", 0),
    ("Yes", f"Run `{MERGE} 172`", 1),
    ("yes, merge", f"Run `{MERGE} 172`", 1),
    # outside the closed label set since the second review: an approval word alone, the
    # command as the label
    ("Merge the PR", f"Run `{MERGE} 172`", 0),
    (f"Run `{MERGE} 172`", "", 0),
    (f"{MERGE} 172", "", 0),
])
def test_only_an_approving_label_mints_whatever_the_description_says(label, description, mints,
                                                                    monkeypatch, tmp_path):
    """Beside an approved sibling, which keeps its own approval either way."""
    questions = [{"question": "Merge PR 172?", "multiSelect": False, "options": [
        {"label": label, "description": description},
        {"label": "Other choice", "description": "x"}]}, _question("Merge PR 185?", f"{MERGE} 185")]
    _approve(monkeypatch, tmp_path, questions, {"Merge PR 172?": label, "Merge PR 185?": YES})
    assert len(_live(tmp_path)) == mints + 1
    assert _allowed(f"{MERGE} 172", tmp_path) is bool(mints)
    assert _allowed(f"{MERGE} 185", tmp_path)


@pytest.mark.parametrize("twin", [
    {"question": "Proceed?", "multiSelect": True, "options": [
        {"label": YES, "description": "ship the docs"}, {"label": "Not yet", "description": "x"}]},
    {"question": "Proceed?"},
    {"question": "Proceed?", "options": []}])
def test_a_question_text_repeated_by_any_kind_of_question_mints_nothing(twin, monkeypatch, tmp_path):
    _approve(monkeypatch, tmp_path, [twin, _question("Proceed?", f"{MERGE} 172")], {"Proceed?": YES})
    assert _live(tmp_path) == []


# ── review findings: commit routes (BLOCKER 1, NOTE 5, NOTE 8) ───────────────────────────
_LEAK = "git merge --ff-only leak"
_GIT_AT_REPO = "GIT_DIR=$PWD/repo/.git GIT_WORK_TREE=$PWD/repo"
# Each form really ends in `repo` (or points git at it) and lands the secret commit when run
# from the directory named: the non-repo parent, or `repo` itself for a `cd` that never runs.
_UNFOLLOWED = [(command, "") for command in (
    f"pushd repo >/dev/null && {_LEAK}",
    f"pushd repo >/dev/null; pushd .. >/dev/null; popd >/dev/null; {_LEAK}",
    "pushd repo >/dev/null && git cherry-pick leak",
    f"eval 'cd repo' && {_LEAK}",
    f"source ./go.sh && {_LEAK}",
    f". ./go.sh; {_LEAK}",
    f"go() {{ pushd repo; }}; go >/dev/null; {_LEAK}",
    f"function go {{ pushd repo; }}; go >/dev/null; {_LEAK}",
    f"declare -x {_GIT_AT_REPO}; {_LEAK}",
    f"typeset -x {_GIT_AT_REPO}; {_LEAK}",
    f"export {_GIT_AT_REPO}; {_LEAK}",
    f"export GIT_DIR GIT_WORK_TREE; GIT_DIR=$PWD/repo/.git; GIT_WORK_TREE=$PWD/repo; {_LEAK}",
    f"set -a; GIT_DIR=$PWD/repo/.git; GIT_WORK_TREE=$PWD/repo; {_LEAK}",
    f"{_GIT_AT_REPO} {_LEAK}",
    "git --git-dir=repo/.git --work-tree=repo merge --ff-only leak",
    f'cd "$(echo repo)" && {_LEAK}',
    f"cd `echo repo` && {_LEAK}",
    f"d=repo; cd $d && {_LEAK}",
    f"cd rep? && {_LEAK}",
    f"cd {{repo,}} && {_LEAK}",
    f"HOME=$PWD/repo; cd && {_LEAK}",
    f"trap 'cd repo' DEBUG; {_LEAK}",
    f"CDPATH=repo/..; cd plain/../repo && {_LEAK}",
    f"cd plain && cd - >/dev/null && cd repo && {_LEAK}",
    f"{{ cd repo; }} 2>/dev/null; {_LEAK}",
    f"exec 3>&1; cd repo >&3; {_LEAK}",
    f"echo 'gitdir: repo/.git' > .git; {_LEAK}",
    f"sh -c 'cd repo && {_LEAK}'",
    "echo repo | xargs -I{} git -C {} merge --ff-only leak",
)] + [(command, "repo") for command in (
    f"(cd ../plain); {_LEAK}",
    f"true || cd ../plain; {_LEAK}",
    f"false && cd ../plain; {_LEAK}",
    f"cd ../plain | true; {_LEAK}",
    f"cd ../plain & wait; {_LEAK}",
    f"echo ';' cd ../plain && {_LEAK}",
    f"echo \\; cd ../plain && {_LEAK}",
    f"echo x # ; cd ../plain &&\n{_LEAK}",
    f"if false; then cd ../plain; fi; {_LEAK}",
    f"cd '~/../plain' 2>/dev/null; {_LEAK}",
    f'cd ../plain; cd "$OLDPWD"; {_LEAK}',
    f"cat <<EOF\ncd ../plain\nEOF\n{_LEAK}",
    "ln -s ../plain l; git -C l/../repo merge --ff-only leak",
)]


@pytest.fixture
def moves(parent):
    (parent / "plain").mkdir()
    (parent / "go.sh").write_text("cd repo\n")
    return parent


@pytest.mark.parametrize("command, where", _UNFOLLOWED)
def test_a_route_behind_a_move_the_parser_does_not_follow_is_denied(command, where, moves):
    assert _commit_check(command, moves / where).returncode == 2, command


@pytest.mark.parametrize("command", [
    _LEAK,                                               # git would refuse it here itself
    f"cd plain && {_LEAK}",
    "git -C plain merge --ff-only leak; git -C plain status",
    "/usr/bin/git -C plain merge --ff-only leak",
    "git -C nowhere merge --ff-only leak",
    "git commit -m nothing",
    f"cd plain && {_LEAK} 2>&1 | tail -3",
    "git -C plain merge --ff-only leak >/dev/null 2>&1 || echo refused",
    'git commit -m "nothing; here"',
])
def test_a_commit_or_route_where_git_finds_no_repository_is_denied(command, moves):
    done = _commit_check(command, moves)
    assert done.returncode == 2, command
    assert "as its own command" in done.stdout + done.stderr


def test_a_commit_behind_a_pushd_is_scanned_where_it_lands(parent):
    """The follower reads `pushd <dir>` as it reads `cd <dir>`, so the deny names the staged
    secret itself, where it used to name the repository git could not find."""
    repo = parent / "repo"
    (repo / "d.py").write_text(_secret_line() + "\n")
    _git(repo, "add", "d.py")
    done = _commit_check("pushd repo >/dev/null && git commit -q -m add", parent)
    assert done.returncode == 2
    assert "SECRET" in done.stdout + done.stderr
    assert "Retry the commit" not in done.stdout + done.stderr


@pytest.mark.parametrize("command", [
    # read against `main`, where the new worktree starts, not against the adding repo's HEAD
    "git -C repo worktree add -q ../wt -b topic main && git -C wt merge --ff-only leak",
    # git picks the start itself: not readable from the command
    "git -C repo worktree add -q ../wt -b topic && git -C wt merge --ff-only leak",
    "git -C repo worktree add -q ../wt && git -C wt merge --ff-only leak",
    "git -C repo worktree add --orphan -b topic ../wt && git -C wt merge --ff-only leak",
])
def test_a_merge_into_a_new_worktree_is_read_against_its_start_point(command, parent):
    _git(parent / "repo", "checkout", "-q", "leak")
    assert _commit_check(command, parent).returncode == 2, command


# ── second and third review: no repository at hook time means deny ───────────────────────
_ADD = "git -C repo worktree add -q ../wt -b topic main"


def _staged(parent):
    (parent / "repo" / "d.py").write_text(_secret_line() + "\n")
    _git(parent / "repo", "add", "d.py")


def _loose(parent):
    (parent / "d.py").write_text(_secret_line() + "\n")


def _subs(parent):
    (parent / "sub").mkdir()
    (parent / "repo" / "sub").mkdir()


def _stale(parent):
    (parent / "wt").mkdir()
    (parent / "wt" / ".git").write_text(f"gitdir: {parent}/repo/.git/worktrees/wt\n")
    (parent / "other").mkdir()


def _tagged(parent):
    _git(parent / "repo", "tag", "main", "leak")


def _upstreams(parent):
    _git(parent / "repo", "branch", "-u", "leak", "feat")
    _git(parent / "repo", "branch", "quiet", "main")
    _git(parent / "repo", "branch", "-u", "quiet", "main")


def _revision_tag(parent):                               # git merges the tag, not the branch
    _git(parent / "repo", "tag", "feat", "leak")


def _empty_target(parent):
    (parent / "wt").mkdir()


def _head_branch(parent):
    _git(parent / "repo", "branch", "head", "main")


def _dirty(parent):
    (parent / "repo" / "a.py").write_text(_secret_line() + "\n")


_INIT = "git init -q"
_TAKE = "git commit -q -m"
# (command, set-up, the branch `repo` has checked out): each row of the second review's
# probe_commit2.py, the worktree rows of probe_over.py, the forms of the same class, and the
# forms the closed grammar allowed while it existed (clean merges and empty commits too).
_SECOND_REVIEW = [
    ("pushd repo >/dev/null && git merge --ff-only leak", None, "main"),
    ("echo 'gitdir: repo/.git'>.git; git merge --ff-only leak", None, "main"),
    (f"echo 'gitdir: repo/.git'>.git; {_TAKE} add", _staged, "main"),
    ('echo "$(cp -R repo/.git .git)"; git merge --ff-only leak', None, "main"),
    ("echo `cp -R repo/.git .git`; git merge --ff-only leak", None, "main"),
    (": ${CDPATH:=repo}; cd sub && git merge --ff-only leak", _subs, "main"),
    (f"{_INIT} && git add d.py && {_TAKE} add", _loose, "main"),
    (f"{_INIT}; git add -A; {_TAKE} init", _loose, "main"),
    ("git -C repo worktree add -q ../other/wt -b topic main && git -C wt merge --ff-only leak",
     _stale, "main"),
    ("git -C repo worktree add -q ../wt main && git -C wt merge --ff-only leak", _tagged, "leak"),
    (f"{_ADD} && git -C wt cherry-pick ..leak", None, "leak"),
    (f"{_ADD} && git -C wt rebase leak", None, "leak"),
    ("git -C repo worktree add -q ../wt feat && git -C wt merge --ff-only @{u}", _upstreams,
     "main"),
    (f"{_ADD} && git -C wt merge --ff-only leak", None, "leak"),
    # probe_over.py o14, o15, o16, o19: no named start, or a `..` path
    ("git -C repo worktree add -q ../wt -b topic && cd wt && git merge --no-edit feat", None,
     "main"),
    ("git -C repo worktree add -q ../wt -b topic && git -C wt cherry-pick feat", None, "main"),
    ("cd repo && git worktree add -q ../wt -b topic && cd ../wt && git merge --no-edit feat",
     None, "main"),
    ("cd repo && git worktree add -q ../wt -b topic main && cd ../wt && git merge --no-edit feat",
     None, "main"),
    # the same class: a name that is not one shared ref, a per-checkout name, a target or a
    # creator the reading cannot vouch for
    (f"{_ADD} && git -C wt merge --ff-only feat", _revision_tag, "main"),
    (f"{_ADD} && git -C wt merge --ff-only HEAD", None, "leak"),
    (f"{_ADD} && git -C wt merge --ff-only head", _head_branch, "leak"),
    (f"{_ADD} && git -C wt merge --ff-only ORIG_HEAD", None, "leak"),
    (f"{_ADD} && git -C wt merge --ff-only refs/heads/leak", None, "main"),
    ("git -C repo worktree add -q ../wt -b topic HEAD && git -C wt merge --ff-only feat", None,
     "leak"),
    (f"{_ADD} && git -C wt merge --ff-only feat", _empty_target, "main"),
    (f"{_ADD} && git -C repo worktree add -q ../wt leak && git -C wt merge --ff-only feat", None,
     "main"),
    (f"{_ADD} && git -C wt pull . leak", None, "main"),
    # a name moved between the hook's reading and the call that uses it
    (f"git -C repo branch -f feat leak && {_ADD} && git -C wt merge --ff-only feat", None, "main"),
    (f"git -C repo worktree add -q -B feat ../o leak && {_ADD} && git -C wt merge --ff-only feat",
     None, "main"),
    (f"git -C repo tag -f feat leak && {_ADD} && git -C wt merge --ff-only feat", None, "main"),
    (f"git -C repo push . leak:feat && {_ADD} && git -C wt merge --ff-only feat", None, "main"),
    (f"{_INIT} && {_TAKE} add", _empty_target, "main"),
]


@pytest.mark.parametrize("command, setup, branch", _SECOND_REVIEW)
def test_the_second_review_forms_are_denied(command, setup, branch, parent):
    if setup:
        setup(parent)
    _git(parent / "repo", "checkout", "-q", branch)
    assert _commit_check(command, parent).returncode == 2, command


@pytest.mark.parametrize("command", [
    f"{_ADD} && git -C wt merge --ff-only feat",
    f"{_ADD} && cd wt && git merge --no-edit feat",
    f"{_ADD} && git -C wt cherry-pick feat",
    f"{_ADD} && git -C wt commit -q --allow-empty -m x",                 # probe_over o13
    "git -C repo worktree add -q ../wt -b topic && git -C wt commit -q --allow-empty -m x",
    "git clone -q repo copy && cd copy && git commit -q --allow-empty -m x",   # probe_over o7
    "git init -q && git commit -q --allow-empty -m init",
    f"{_ADD} && git -C wt merge 0123456789abcdef0123456789abcdef01234567",
])
def test_a_clean_step_in_a_place_the_same_command_makes_is_denied_too(command, parent):
    done = _commit_check(command, parent)
    assert done.returncode == 2, command
    assert "as its own command" in done.stdout + done.stderr


@pytest.mark.parametrize("command", [
    "git -C repo merge --ff-only feat",
    "cd repo && git commit -m x",
    "git -C repo worktree add -q ../wt -b topic main",                    # no commit, no route
])
def test_a_step_in_a_repository_git_can_read_is_judged_as_before(command, parent):
    done = _commit_check(command, parent)
    assert done.returncode == 0, done.stdout + done.stderr


# (command, set-up, directory under the parent it runs from): sec-review-3's probe3.py rows A1 to
# A5 (a creator makes a plain directory below a repository, the commit then runs in that
# repository) and B1 (a worktree path spelled in another letter case).
_THIRD_REVIEW = [
    ("git init -q repo/sub/deeper && git -C repo/sub commit -q -m add", _staged, "."),
    ("git init -q sub/deeper && git -C sub commit -q -m add", _staged, "repo"),
    ("git init -q repo/sub/deeper && git -C repo/sub commit -q -a -m add", _dirty, "."),
    ("git clone -q repo repo/sub/deeper && git -C repo/sub commit -q -m add", _staged, "."),
    ("git -C repo worktree add -q sub/deeper -b t main && git -C repo/sub commit -q -m add",
     _staged, "."),
    ("git -C repo worktree add -q ../WT -b topic main && git -C wt merge --ff-only leak",
     _empty_target, "."),
    ("git -C repo worktree add -q ../wt -b topic main && git -C WT merge --ff-only leak", None,
     "."),
]


@pytest.mark.parametrize("command, setup, where", _THIRD_REVIEW)
def test_the_third_review_forms_are_denied(command, setup, where, parent):
    if setup:
        setup(parent)
    assert _commit_check(command, parent / where).returncode == 2, command


# ── second review: the closed set of approving labels ────────────────────────────────────
_REFUSED_LABELS = [
    f"{MERGE} 172 later", f"{MERGE} 172 - not now", f"{MERGE} 172 never", f"{MERGE} 172 # skip",
    f"{MERGE} 172, no", f"{MERGE} 172 after CI", f"{MERGE} 172", f"`{MERGE} 172`",
    "Merge once CI is green", "Merge when the checks pass", "Merge tomorrow",
    "Merge only if tests pass", "Yes, but run the tests first", "Yes, if CI is green",
    "Yes, tomorrow", "Yes, pending review", "Run the tests before merging",
    "Run more tests first", "Proceed with the review first", "Do it afterwards",
    "Push it to next week", "OK, postpone it", "Yes, defer", "OK to defer the merge",
    "OK, pause here", "Proceed to pause", "OK, cancelled", "Okay, skipping this one", "OK, nope",
    "Merge? Nope.", "Confirm cancellation", "Confirm decline", "Confirm: do nothing",
    "Approve nothing", "Run nothing", "Delete nothing", "Proceed without merging",
    "Close without merging", "Go ahead and reject it", "Yes, reject", "Yes, decline",
    "Merge rejected", "Merge is blocked", "Push back the merge", "Yes, I cannot approve this",
    "Yes — keep reviewing", "Merge a different PR", "Close this question",
    "Yes, nоt now", "Yes, n0", "ok, w8",                 # a Cyrillic o, a zero, digits
    # the same class: anything beside a label of the set
    "Yes, merge later", "Yes, merge?", "Yes,merge", "Yes,  merge", "Yes, merge.", "Yes, mеrge",
    "Yes\u00a0", "Yes\n", "\tYes", "Yes, delete", "Yes, delete branch", "Yes, force push",
    "Yes, merge, close", "yes, yes", "Yes!", "Ｙes", "OK", "Approve", "Merge", "Proceed",
]


@pytest.mark.parametrize("label", _REFUSED_LABELS)
def test_a_label_outside_the_closed_set_mints_nothing(label, monkeypatch, tmp_path):
    assert not post.is_approval_label(label)
    questions = [{"question": "Merge PR 172?", "multiSelect": False, "options": [
        {"label": label, "description": f"Run `{MERGE} 172` once that holds"},
        {"label": "Other choice", "description": "x"}]}]
    _approve(monkeypatch, tmp_path, questions, {"Merge PR 172?": label})
    assert _live(tmp_path) == []
    assert not _allowed(f"{MERGE} 172", tmp_path)


@pytest.mark.parametrize("label, description, command", [
    ("Yes", f"Run `{MERGE} 172`", f"{MERGE} 172"),
    ("Yes, merge", f"Run `{MERGE} 172` to merge this PR", f"{MERGE} 172"),
    ("Yes, delete local branch", "Run `git branch -D feat-x` to delete the merged local branch",
     "git branch -D feat-x"),
    ("Yes, delete remote branch",
     "Run `git push origin --delete feat-x` to delete the merged remote branch",
     "git push origin --delete feat-x"),
])
def test_each_documented_label_mints_and_redeems(label, description, command, monkeypatch,
                                                 tmp_path):
    questions = [{"question": "What should happen?", "multiSelect": False, "options": [
        {"label": label, "description": description},
        {"label": "Not yet", "description": "Leave everything as it is"}]}]
    _approve(monkeypatch, tmp_path, questions, {"What should happen?": label})
    assert len(_live(tmp_path)) == 1
    assert _allowed(command, tmp_path)


def test_the_label_set_is_the_seven_listed_and_both_messages_list_them(tmp_path):
    accepted = ["Yes", "Yes, merge", "Yes, close", "Yes, push", "Yes, force-push",
                "Yes, delete local branch", "Yes, delete remote branch"]
    deny = pre.check_merge_authorization(f"{MERGE} 172", token_dir=tmp_path)
    for label in accepted:
        assert post.is_approval_label(label) and post.is_approval_label(f"  {label.upper()} ")
        assert f'"{label}"' in post.APPROVAL_LABELS
        assert f'"{label}"' in deny
    assert not any(post.is_approval_label(value) for value in (None, 1, b"Yes", ["Yes"], ""))


# ── a command the tokenizer cannot read, which a shell may still run ─────────────────────
_IN_REPO = "git -C repo merge --ff-only leak"


@pytest.mark.parametrize("command", [
    _IN_REPO,                                            # the readable control: scanned
    f"{_IN_REPO} \\",
    f"{_IN_REPO} -\\",
    f"{_IN_REPO} && git -C repo status -\\",
    f"{_IN_REPO} && git -C repo status -'",
    f'{_IN_REPO} && git -C repo status -"',
    "cd repo && git merge --ff-only leak \\",
    f"rtk {_IN_REPO}",
    f"rtk proxy {_IN_REPO}",
    f"/usr/bin/{_IN_REPO}",
    f"{_IN_REPO} # don't stop",                          # both shells run these two
    f"{_IN_REPO}; cat <<EOF\nit's done\nEOF",
    f"sh -c '{_IN_REPO} \\'",
    f"git commit --allow-empty -m x && {_IN_REPO} \\",   # a route behind a readable-looking commit
    "git -C repo cherry-pick leak # don't",
    "git -C repo pull . leak # it's local",
])
def test_a_command_that_names_git_and_cannot_be_read_is_denied(command, parent):
    assert _commit_check(command, parent).returncode == 2, command


@pytest.mark.parametrize("command", [
    "cat <<EOF\nit's in the .git folder, see legit-notes and git-lfs\nEOF",
    "echo done # can't fail",
    "ls repo/.git \\",
    # names git, but no verb that brings history in after it
    "git -C repo status # it's fine",
    "cat <<EOF\nit's the git log\nEOF",
    "git -C repo log --format='%s' # don't",
    "echo merge first, git later # isn't one",
])
def test_an_unreadable_command_that_names_no_git_route_is_left_alone(command, parent):
    done = _commit_check(command, parent)
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("command, reason", [
    ("cd repo && git commit -q -m add", "SECRET"),                                    # control
    ("cd repo && git commit -q -m add; sh -c 'echo \"git commit'", "could not be read"),
    ("cd repo && git commit -q -m add; eval 'echo \"git commit'", "could not be read"),
    ("sh -c 'echo \"git commit' ; cd repo && git commit -q -m add", "could not be read"),
])
def test_an_unreadable_nested_script_denies_instead_of_scanning_the_payload_cwd(
        command, reason, parent):
    """The cwd is a clean repository of its own; the nested `repo` holds a staged secret."""
    (parent / "repo" / "d.py").write_text(_secret_line() + "\n")
    _git(parent / "repo", "add", "d.py")
    _git(parent, "init", "-q", "-b", "main")
    (parent / "readme.txt").write_text("outer\n")
    _git(parent, "add", "readme.txt")
    _git(parent, "commit", "-q", "-m", "outer")
    done = _commit_check(command, parent)
    assert done.returncode == 2 and reason in done.stdout + done.stderr, command + done.stdout


def test_the_unreadable_deny_names_the_form_to_use(parent):
    # an open quote: a trailing backslash is now read as the shell reads it, and scanned
    done = _commit_check(f"{_IN_REPO} && git -C repo status -'", parent)
    assert "could not be read" in done.stdout and "as its own plain command" in done.stdout
