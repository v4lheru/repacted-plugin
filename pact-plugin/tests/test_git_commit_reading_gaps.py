"""
Location: pact-plugin/tests/test_git_commit_reading_gaps.py
Summary: How the commit check reads a command (hooks/git_commit_check.py read_commit,
         hooks/shared/commit_routes.py): the closed option tables of git and of each history
         verb, GIT in capitals, aliases, a name or HEAD an earlier step of the same command
         moves, comments and here-documents. Each gap row brings a fake token into the branch
         and must be refused; its clean neighbour must pass, so no row passes by refusing
         everything. EVERYDAY holds the ordinary lines that must stay allowed in both the
         commit check and the merge guard.
Used by: the plugin suite. What each verb brings in: tests/test_git_commit_routes.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from project_tasks_support import GIT_ENV, PLUGIN_ROOT
from shared import commit_routes as routes

HOOKS = PLUGIN_ROOT / "hooks"
FAKE = "ghp_" + "Fk9mQ2vLp7Rz4tXk9mQ2vLp7Rz4tXk9mQ2vL"          # github-token shape, fake
COMMIT = "git " + "commit -q -m add"
SECRET, MOVED_NAME, MOVED_HEAD = "SECRET", "an earlier step of the same command moves", "moves HEAD"
NO_OPTION, ALIAS_STEP, UNREAD = "is not an option of", "may be an alias", "could not be read"


def git(where: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=where, capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT_ENV}).stdout


def _stage_fake(parent: Path, repo: Path) -> None:
    (repo / "d.py").write_text(f'TOKEN = "{FAKE}"\n')
    git(repo, "add", "d.py")


def _stage_clean(parent: Path, repo: Path) -> None:
    (repo / "a.py").write_text("x = 3\n")
    git(repo, "add", "a.py")
    (repo / "0001-feat.patch").write_text(git(repo, "format-patch", "-1", "--stdout", "feat"))


def _bisecting(parent: Path, repo: Path) -> None:
    git(repo, "bisect", "start")                                    # records main as the start
    git(repo, "checkout", "-q", "--detach", "leak")


def _aliases(parent: Path, repo: Path) -> None:
    for name, value in (("mg", "merge"), ("mgl", "merge --ff-only leak"), ("ci", "commit"),
                        ("sh", "!git merge --ff-only leak"), ("a1", "a2"), ("a2", "merge leak"),
                        ("loop", "loop"), ("st", "status"),
                        ("shc", '!f() { git merge --ff-only "$1"; }; f')):
        git(repo, "config", f"alias.{name}", value)
    (parent / "al").write_text("[alias]\n\tx = merge\n")


def _aliases_staged(parent: Path, repo: Path) -> None:
    _aliases(parent, repo)
    _stage_fake(parent, repo)


def _tagged(parent: Path, repo: Path) -> None:
    git(repo, "tag", "topic", "leak")                               # topic: a branch (main), a tag


def _tagged_bisect(parent: Path, repo: Path) -> None:
    _tagged(parent, repo)
    git(repo, "checkout", "-q", "--detach")
    (repo / ".git" / "BISECT_START").write_text("topic\n")


def _stopped(parent: Path, repo: Path) -> None:
    subprocess.run(["git", "rebase", "-q", "-x", "false", "leak", "feat"], cwd=repo,
                   capture_output=True, env={**os.environ, **GIT_ENV})  # stops at its first step


# scene: (branch checked out, extra setup). Every scene is read by the hook, never changed.
SCENES = {"main": ("main", None), "leak": ("leak", None), "feat": ("feat", None),
          "topic": ("topic", None), "staged": ("main", _stage_fake), "work": ("topic", _stage_clean),
          "alias": ("main", _aliases), "alias-staged": ("main", _aliases_staged),
          "bisecting": ("main", _bisecting), "tagged": ("leak", _tagged),
          "tagged-bisect": ("leak", _tagged_bisect), "stopped": ("leak", _stopped),
          "worktree": ("leak", lambda parent, repo: git(repo, "worktree", "add", "-q", "../wt", "main"))}


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    """scene(name) -> its parent directory. parent/repo holds main, leak (adds the fake token in
    c.py), feat (one clean commit on main), topic (at main) and origin, a bare copy."""
    built: dict = {}

    def build(name: str) -> Path:
        if name not in built:
            checkout, setup = SCENES[name]
            parent = Path(os.path.realpath(tmp_path_factory.mktemp(name.replace("-", "_"))))
            repo = parent / "repo"
            repo.mkdir()
            git(repo, "init", "-q", "-b", "main")
            (repo / ".gitignore").write_text(".env\n")
            (repo / "a.py").write_text("x = 1\n")
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "base")
            git(repo, "checkout", "-q", "-b", "leak")
            (repo / "c.py").write_text(f'TOKEN = "{FAKE}"\n')
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "leak")
            git(repo, "checkout", "-q", "-b", "feat", "main")
            (repo / "b.py").write_text("y = 2\n")
            git(repo, "add", "b.py")
            git(repo, "commit", "-q", "-m", "feat")
            git(repo, "branch", "topic", "main")
            git(parent, "clone", "-q", "--bare", "repo", "origin.git")
            git(repo, "remote", "add", "origin", "../origin.git")
            git(repo, "fetch", "-q", "origin")
            git(repo, "checkout", "-q", checkout)
            if setup:
                setup(parent, repo)
            built[name] = parent
        return built[name]
    return build


def run(hook: str, command: str, cwd: Path, home: Path) -> subprocess.CompletedProcess:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd),
               "session_id": "reading-gaps"}
    env = {**os.environ, **GIT_ENV, "HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude")}
    for name in ("GIT_DIR", "GIT_WORK_TREE", "CLAUDE_PROJECT_DIR"):
        env.pop(name, None)
    done = subprocess.run([sys.executable, str(HOOKS / hook)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=120, cwd=cwd, env=env)
    assert FAKE not in done.stdout + done.stderr, "a value reached the hook output"
    return done


def denied(done: subprocess.CompletedProcess) -> bool:
    assert done.returncode in (0, 2), (done.returncode, done.stderr)
    return done.returncode == 2 or '"deny"' in done.stdout


# (scene, command, a text the refusal carries). cwd is the parent of repo unless IN_REPO.
P = "git -C repo"
REFUSED = [
    ("main", f"{P} merge -q --message --quit leak", SECRET),
    ("main", f"{P} pull -q --upload-pack x . leak", SECRET),
    ("main", f"{P} branch -f feat leak && {P} merge -q --ff-only FEAT", MOVED_NAME),
    ("main", f"{P} tag -f feat leak && {P} merge -q feat", MOVED_NAME),
    ("main", f"{P} m\\erge -q --ff-only leak #'", SECRET),
    ("staged", f"cd repo && cat <<'EOF' && {COMMIT}\nit's a note\nEOF", SECRET),
]
IN_REPO_REFUSED = [
    ("alias", "git mg --ff-only leak", SECRET),
    ("leak", "git checkout -q main && git merge -q --ff-only leak", SECRET),
    ("leak", "git checkout main && git cherry-pick ..leak", SECRET),
    ("main", "git checkout --orphan fresh && git merge feat", MOVED_HEAD),
    ("main", "false && git checkout -q leak ; git rebase leak", SECRET),
    ("main", f"git checkout --orphan pages && git checkout leak -- . && {COMMIT}", SECRET),
    ("stopped", "git rebase --quit && git merge leak", MOVED_HEAD),
    ("main", "git -c alias.x=merge x leak", SECRET),
    ("main", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.x GIT_CONFIG_VALUE_0='merge leak' git x", SECRET),
    ("main", "git config include.path ../al && git x leak", ALIAS_STEP),
]


@pytest.mark.parametrize("name,command,needle", REFUSED, ids=lambda v: str(v)[:60])
def test_gap_form_is_refused(scene, tmp_path, name, command, needle):
    done = run("git_commit_check.py", command, scene(name), tmp_path)
    assert denied(done), (command, done.stdout, done.stderr)
    assert needle in done.stdout + done.stderr, (command, done.stderr)


@pytest.mark.parametrize("name,command,needle", IN_REPO_REFUSED, ids=lambda v: str(v)[:60])
def test_gap_form_inside_the_repository_is_refused(scene, tmp_path, name, command, needle):
    done = run("git_commit_check.py", command, scene(name) / "repo", tmp_path)
    assert denied(done), (command, done.stdout, done.stderr)
    assert needle in done.stdout + done.stderr, (command, done.stderr)


# The clean neighbour of each family: same form, nothing flagged brought in.
PASSES = [
    ("main", f"{P} merge -q --message 'merge' feat"),
    ("main", "git " * 20_000 + "'"),
]
IN_REPO_PASSES = [
    ("alias", "git mg --ff-only feat"),
    ("topic", "git checkout main -- a.py && git merge feat"),
    ("main", "pushd . >/dev/null && git merge -q feat && popd"),
]


@pytest.mark.parametrize("name,command", PASSES, ids=lambda v: str(v)[:60])
def test_clean_neighbour_passes(scene, tmp_path, name, command):
    done = run("git_commit_check.py", command, scene(name), tmp_path)
    assert not denied(done), (command[:200], done.stdout, done.stderr)


@pytest.mark.parametrize("name,command", IN_REPO_PASSES, ids=lambda v: str(v)[:60])
def test_clean_neighbour_inside_the_repository_passes(scene, tmp_path, name, command):
    done = run("git_commit_check.py", command, scene(name) / "repo", tmp_path)
    assert not denied(done), (command, done.stdout, done.stderr)


@pytest.mark.parametrize("mover", ["GIT_DIR=../REPO/.git git", "git --git-dir=../REPO/.git"])
def test_one_repository_spelled_in_another_letter_case_is_one_place(scene, tmp_path, mover):
    repo = scene("leak") / "repo"
    if not (repo.parent / "REPO").exists():
        pytest.skip("a case-sensitive disk: REPO is no spelling of repo")
    done = run("git_commit_check.py", f"{mover} checkout -q main && git merge leak", repo, tmp_path)
    assert denied(done) and SECRET in done.stdout + done.stderr, (mover, done.stdout, done.stderr)


def test_two_directories_whose_paths_differ_only_in_case_are_two_places(tmp_path, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir(), two.mkdir()
    link = tmp_path / "link"
    link.symlink_to(one)
    assert routes._same_directory(str(link)) == routes._same_directory(str(one))
    assert routes._same_directory(str(one)) != routes._same_directory(str(two))
    assert routes._same_directory(str(tmp_path / "gone")) == str(tmp_path / "gone")
    real = os.stat                                                  # a case-sensitive disk
    monkeypatch.setattr(routes.os, "stat", lambda path: real(two if path == "/x/REPO/.git" else one))
    assert routes._same_directory("/x/repo/.git") != routes._same_directory("/x/REPO/.git")


HEREDOC = " -m \"$(cat <<'EOF'\nfix: don't drop \"empty\" input\n\nBody line.\nEOF\n)\""
C = "git " + "commit"
# Ordinary lines, run on branch topic with a clean staged edit: allowed by both checks. The
# closed option tables refuse what they do not list, so an option people use belongs here.
EVERYDAY = [
    f'{C} -m "fix: handle empty input"',
    "git merge feat --no-ff",
    "git pull --force",
    "git rebase origin/main",
    "git revert -s HEAD",
    "env GIT_EDITOR=true git merge feat",
]


@pytest.mark.parametrize("command", EVERYDAY, ids=lambda v: str(v)[:60])
def test_everyday_line_stays_allowed(scene, tmp_path, command):
    for hook in ("git_commit_check.py", "merge_guard_pre.py"):
        done = run(hook, command, scene("work") / "repo", tmp_path)
        assert not denied(done), (hook, command, done.stdout, done.stderr)


NFC, NFD = unicodedata.normalize("NFC", "café"), unicodedata.normalize("NFD", "café")


@pytest.mark.parametrize("mover,args,word", [
    ("branch", ["-f", NFC, "leak"], NFD),
    ("update-ref", ["refs/heads/feat", "leak"], "heads/feat^2"),
])
def test_a_moved_name_is_found_in_any_spelling(mover, args, word):
    moved = routes.moved_names(mover, args, None)
    assert routes.moved_word("merge", ["-q", word], moved) == word


@pytest.mark.parametrize("word", [
    "feat-2",
    "feat/x",
])
def test_another_name_is_not_taken_for_the_moved_one(word):
    moved = routes.moved_names("branch", ["-f", "feat", "leak"], None)
    assert routes.moved_word("merge", [word], moved) is None


@pytest.mark.parametrize("words,verb", [
    (["git", "--exec-path=/x", "merge", "leak"], "merge"),
    (["git"], None),
])
def test_git_options_before_the_verb(words, verb):
    at, _ = routes.git_globals(words)
    assert (words[at] if at is not None else None) == verb


@pytest.mark.parametrize("option", [
    "--Exec-path=/x",
    "-" + "q" * 70_000,
    "-Ｃ",
])
def test_an_unknown_option_of_git_is_refused(option):
    with pytest.raises(routes.Unreadable):
        routes.git_globals(["git", option, "merge", "leak"])
