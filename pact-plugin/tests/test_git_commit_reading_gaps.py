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
    # a value-taking option read as a flag hid the target: closed tables per verb
    ("main", f"{P} merge -q --message --quit leak", SECRET),
    ("main", f"{P} merge -q --message=--quit leak", SECRET),
    ("main", f"{P} merge -q -m --quit leak", SECRET),
    ("main", f"{P} merge -qm --quit leak", SECRET),
    ("main", f"{P} merge -q --file --abort leak", SECRET),
    ("main", f"{P} merge -q --into-name --quit leak", SECRET),
    ("main", f"{P} merge -q -X --quit leak", SECRET),
    ("main", f"{P} merge -q --message {'x' * 100_000} leak", SECRET),
    ("main", f"{P} merge -q --mess --quit leak", NO_OPTION),         # an abbreviation
    ("main", f"{P} merge -q --MESSAGE --quit leak", NO_OPTION),
    ("main", f"{P} merge -q --no-such-flag leak", NO_OPTION),
    ("main", f"{P} merge -qZ leak", NO_OPTION),
    ("main", f"{P} pull -q --refmap x . leak", SECRET),
    ("main", f"{P} pull -q --refmap=x . leak", SECRET),
    ("main", f"{P} pull -q --negotiation-tip main . leak", SECRET),
    ("main", f"{P} pull -q --negotiation-tip=main . leak", SECRET),
    ("main", f"{P} pull -q --upload-pack x . leak", SECRET),
    ("main", f"{P} pull -q -o x . leak", SECRET),
    ("main", f"{P} pull -q --refm x . leak", NO_OPTION),
    # an option of git itself before the verb
    ("main", f"{P} --attr-source HEAD merge -q --ff-only leak", SECRET),
    ("main", f"{P} --attr-source=HEAD merge -q --ff-only leak", SECRET),
    ("main", "git --attr-source HEAD -C repo merge -q --ff-only leak", SECRET),
    ("main", f"{P} --exec-path=/nowhere merge -q --ff-only leak", SECRET),
    ("main", f"{P} --ATTR-SOURCE HEAD merge -q --ff-only leak", NO_OPTION),
    ("main", f"{P} --no-such-option merge -q --ff-only leak", NO_OPTION),
    ("staged", f"{P} --attr-source HEAD {COMMIT[4:]}", SECRET),
    ("staged", f"{P} --attr-source=HEAD {COMMIT[4:]}", SECRET),
    ("staged", f"{P} --namespace x {COMMIT[4:]}", SECRET),
    # a name an earlier step of the same command moves
    ("main", f"{P} branch -f feat leak && {P} merge -q --ff-only feat", MOVED_NAME),
    ("topic", f"{P} worktree add -q ../wt -B feat leak && {P} merge -q --ff-only feat", MOVED_NAME),
    ("main", f"{P} branch -f FEAT leak && {P} merge -q --ff-only feat", MOVED_NAME),
    ("main", f"{P} branch -f feat leak && {P} merge -q --ff-only FEAT", MOVED_NAME),
    ("main", f"{P} branch -f feat leak && {P} merge -q refs/heads/feat", MOVED_NAME),
    ("main", f"{P} branch -f feat leak && {P} merge -q feat~0", MOVED_NAME),
    ("main", f"{P} branch -f feat leak && {P} merge -q 'feat^{{commit}}'", MOVED_NAME),
    ("main", f"{P} branch --force feat leak; {P} cherry-pick main..feat", MOVED_NAME),
    ("main", f"{P} branch -f feat leak && {P} rebase feat", MOVED_NAME),
    ("main", f"{P} branch -f feat leak && {P} pull . feat", MOVED_NAME),
    ("main", f"{P} update-ref refs/heads/feat leak && {P} merge -q feat", MOVED_NAME),
    ("main", f"{P} tag -f feat leak && {P} merge -q feat", MOVED_NAME),
    ("main", f"{P} checkout -q -B feat leak && {P} checkout -q main && {P} merge feat", MOVED_NAME),
    ("main", f"{P} switch -q -C feat leak && {P} switch -q main && {P} merge feat", MOVED_NAME),
    ("topic", f"{P} fetch -q . leak:feat && {P} merge feat", MOVED_NAME),
    ("topic", f"{P} fetch -q . +leak:feat && {P} merge feat", MOVED_NAME),
    ("topic", f"{P} push -q . leak:feat -f && {P} merge feat", MOVED_NAME),
    # the program word in another letter case (a case-blind disk runs it), and its runners
    ("main", "GIT -C repo merge -q --ff-only leak", SECRET),
    ("main", "Git -C repo merge -q --ff-only leak", SECRET),
    ("main", "/usr/bin/GIT -C repo merge -q --ff-only leak", SECRET),
    ("main", "ENV GIT -C repo merge -q --ff-only leak", SECRET),
    ("main", "/usr/bin/env GIT -C repo merge -q --ff-only leak", SECRET),
    ("main", "/usr/bin/ENV git -C repo merge -q --ff-only leak", SECRET),
    ("main", "command GIT -C repo merge -q --ff-only leak", SECRET),
    ("main", "RTK GIT -C repo merge -q --ff-only leak", SECRET),
    ("staged", f"GIT -C repo {COMMIT[4:]}", SECRET),
    # unreadable text that names git and a history verb or a commit
    ("main", f"{P} m\\erge -q --ff-only leak #'", SECRET),
    ("main", f"{P} merge -q --ff-only leak #'", SECRET),
    ("staged", f"cd repo && {COMMIT} #'", SECRET),
    ("staged", f"cd repo && {COMMIT} # it's", SECRET),
    ("staged", f"cd repo && {COMMIT[:-3]}'add", UNREAD),
    ("main", "cd repo && git merge -q leak \\", SECRET),
    # a comment or a here-document hides nothing that runs
    ("main", "cd repo && git merge -q leak # it's", SECRET),
    ("main", "cd repo && echo a#b && git merge -q leak", SECRET),
    ("main", "cd repo && echo a #b\ngit merge -q leak", SECRET),
    ("main", "cd repo && echo 'a #' && git merge -q leak", SECRET),
    ("main", "cd repo && echo $# && git merge -q leak", SECRET),
    ("main", "cd repo && echo ${#x} && git merge -q leak", SECRET),
    ("main", "cd repo && bash <<'EOF'\ngit merge -q leak\nEOF", SECRET),
    ("main", "cd repo && bash <<EOF\ngit merge -q leak\nEOF", SECRET),
    ("main", "cd repo && bash <<-EOF\n\tgit merge -q leak\n\tEOF", SECRET),
    ("staged", f"cd repo && bash <<'EOF'\n{COMMIT}\nEOF", SECRET),
    ("staged", f"cd repo && cat <<'EOF' && {COMMIT}\nit's a note\nEOF", SECRET),
]
IN_REPO_REFUSED = [
    # an alias is expanded as git expands it
    ("alias", "git mg --ff-only leak", SECRET),
    ("alias", "git mgl", SECRET),
    ("alias", "GIT MGL", SECRET),                                   # alias names are case-blind
    ("alias", "git sh", SECRET),                                    # `!script`
    ("alias", "git shc leak", ""),
    ("alias", "git a1", SECRET),                                    # an alias of an alias
    ("alias", "git loop", "8 deep"),
    ("alias-staged", "git ci -q -m add", SECRET),
    ("main", "git -c alias.x='merge leak' x", SECRET),
    ("main", "git -c alias.x=merge x leak", SECRET),
    ("main", "git -c ALIAS.X=merge x leak", SECRET),
    ("main", "git -c alias.x='!git merge leak' x", SECRET),
    ("main", "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.x GIT_CONFIG_VALUE_0='merge leak' git x", SECRET),
    ("main", "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.x GIT_CONFIG_VALUE_0='merge leak'; git x",
     SECRET),
    ("alias", "GIT_CONFIG_GLOBAL=../al git x leak", SECRET),
    # an alias the same command defines cannot be asked for yet
    ("main", "git config alias.x 'merge leak' && git x", ALIAS_STEP),
    ("main", "git config --local alias.x merge && git x leak", ALIAS_STEP),
    ("main", "git config ALIAS.X merge && git x leak", ALIAS_STEP),
    ("main", "git config --add alias.x merge; git x leak", ALIAS_STEP),
    ("main", "git config include.path ../al && git x leak", ALIAS_STEP),
    # HEAD moved by an earlier step: the scan reads against where HEAD will be
    ("leak", "git checkout main && git merge leak", SECRET),
    ("leak", "git checkout -q main && git merge -q --ff-only leak", SECRET),
    ("leak", "git switch main && git merge leak", SECRET),
    ("leak", "git checkout main && git merge -", SECRET),
    ("leak", "git checkout main && git merge @{-1}", SECRET),
    ("leak", "git checkout main && git merge --squash leak", SECRET),
    ("leak", "git checkout main && git rebase leak", SECRET),
    ("leak", "git checkout main && git pull . leak", SECRET),
    ("leak", "git checkout main; git merge leak", SECRET),
    ("leak", "git checkout main\ngit merge leak", SECRET),
    ("leak", "git checkout --detach main && git merge leak", SECRET),
    ("leak", "git checkout main~0 && git merge leak", SECRET),
    ("leak", "git checkout -b new main && git merge leak", SECRET),
    ("leak", "git switch -c new main && git merge leak", SECRET),
    ("leak", "git stash && git checkout main && git merge leak", SECRET),
    ("leak", "git -C ../repo checkout main && git merge leak", SECRET),
    ("leak", "git --git-dir=.git --work-tree=. checkout main && git merge leak", SECRET),
    ("leak", "git checkout main && GIT_DIR=.git git merge leak", SECRET),
    ("leak", "git checkout main && sh -c 'git merge leak'", SECRET),
    ("leak", "sh -c 'git checkout main' && git merge leak", SECRET),
    ("leak", "GIT checkout main && GIT merge leak", SECRET),
    ("leak", "git checkout main && git cherry-pick ..leak", SECRET),
    ("leak", "git checkout main && git cherry-pick HEAD..leak", SECRET),
    ("leak", "git checkout main && git cherry-pick @..leak", SECRET),
    ("leak", "git checkout main && git cherry-pick main...leak", SECRET),
    ("leak", "git checkout main && git cherry-pick leak ^HEAD", SECRET),
    ("leak", "git checkout main && git cherry-pick HEAD@{1}..leak", MOVED_HEAD),
    ("leak", "git reset --hard main && git merge leak@{1}", MOVED_HEAD),
    ("leak", "git reset -q --hard main && git merge HEAD@{1}", MOVED_HEAD),
    ("leak", "git reset --hard main && git merge ORIG_HEAD", MOVED_HEAD),
    ("leak", "git checkout main && git merge", MOVED_HEAD),             # the upstream of main
    ("leak", "git checkout main && git rebase", MOVED_HEAD),
    ("leak", "git symbolic-ref HEAD refs/heads/main && git merge leak", MOVED_HEAD),
    ("leak", "git update-ref HEAD main && git merge leak", MOVED_HEAD),
    ("leak", "git bisect start && git merge leak", MOVED_HEAD),
    ("leak", "git checkout --no-such-flag main && git merge leak", MOVED_HEAD),
    ("main", "git checkout nosuch && git merge feat", MOVED_HEAD),      # a branch it would make
    # reset keeps the index or the files: the commit takes them against the new HEAD
    ("leak", "git reset --soft main && " + COMMIT, SECRET),
    ("leak", "git reset --soft HEAD~1 && " + COMMIT, SECRET),
    ("leak", "git reset main && " + COMMIT[:-6] + "-a -m add", SECRET),
    # an unborn branch: the commit is a root commit, so the whole index is what it takes
    ("leak", "git checkout --orphan fresh && " + COMMIT, SECRET),
    ("main", "git checkout --orphan fresh leak && " + COMMIT, SECRET),  # the start point's tree
    ("main", "git checkout --orphan fresh && git merge feat", MOVED_HEAD),
    # a rebase of a named branch checks it out: its new base and the branch are places
    ("leak", "git rebase -q main topic && git merge leak", SECRET),
    ("leak", "git rebase --onto main leak~1 topic 2>/dev/null; git merge leak", SECRET),
    ("leak", "git rebase -q main topic && git cherry-pick HEAD~1", "names HEAD"),
    ("leak", "git rebase -q --keep-base main topic && git merge feat", MOVED_HEAD),
    # bisect reset goes back to the commit it names, or to the start the repository records
    ("leak", "git bisect reset main; git merge leak", SECRET),
    ("bisecting", "git bisect reset; git merge leak", SECRET),
    # a step that moves HEAD only adds a place to scan against: the shell may never run it
    ("main", "false && git checkout -q leak ; git merge leak", SECRET),
    ("main", "if false; then git checkout -q leak; fi; git merge leak", SECRET),
    ("main", "true || git checkout -q leak ; git merge leak", SECRET),
    ("main", "false && git checkout -q leak ; git rebase leak", SECRET),
    ("main", "false && git checkout -q leak ; git merge --squash leak", SECRET),
    ("main", "cat <<EOF\ngit checkout -q leak\nEOF\ngit merge leak", SECRET),
    ("main", "grep -q x <<EOF\ngit reset --hard leak\nEOF\ngit merge leak", SECRET),
    ("main", "git reset --hard leak && git merge leak", SECRET),
    ("leak", "git checkout -q main; false && git checkout -q leak; git merge leak", SECRET),
    ("staged", "false && git reset --soft leak ; " + COMMIT, SECRET),
    # `-` is feat, or leak where the shell skips the first checkout: both are scanned
    ("leak", "git checkout feat && git checkout main && git merge -", SECRET),
    ("feat", "git revert --no-edit --prety=oneline HEAD", "is not an option"),
    ("feat", "git rebase --allow-empty-mesage main", "is not an option"),
    # paths copied out of another tree, then committed: that tree's version of them is scanned
    ("main", f"git checkout leak -- c.py && {COMMIT}", SECRET),
    ("main", f"git checkout leak -- . && {COMMIT}", SECRET),
    ("main", f"git checkout leak c.py && {COMMIT}", SECRET),
    ("main", f"git restore --source leak --staged --worktree c.py && {COMMIT}", SECRET),
    ("main", f"git restore -s leak -S c.py && {COMMIT}", SECRET),
    ("main", f"git reset -q leak -- c.py && {COMMIT}", SECRET),
    # an orphan commit takes the index the steps before it left; --orph is --orphan to git
    ("main", f"git checkout -q --detach leak && git checkout --orphan pages && {COMMIT}", SECRET),
    ("main", f"git checkout leak --orphan pages && {COMMIT}", SECRET),
    ("main", f"git checkout --orph pages leak && {COMMIT}", SECRET),
    ("leak", f"git checkout --orph pages && {COMMIT}", SECRET),
    ("main", f"git checkout --orphan pages && git checkout leak -- . && {COMMIT}", SECRET),
    # a HEAD-moving step in a directory the reader cannot expand leaves every place unknown
    ("leak", "git -C $PWD checkout -q main && git merge leak", MOVED_HEAD),
    ("leak", 'git -C "$PWD" checkout -q main && git merge leak', MOVED_HEAD),
    ("leak", "git -C ${PWD} checkout -q main && git merge leak", MOVED_HEAD),
    ("leak", "git -C ~+ checkout -q main && git merge leak", MOVED_HEAD),
    ("leak", "git -C $(pwd) checkout -q main && git merge leak", MOVED_HEAD),
    ("leak", "D=$PWD; git -C $D checkout -q main && git merge leak", MOVED_HEAD),
    ("leak", "GIT_DIR=$PWD/.git git checkout -q main && git merge leak", MOVED_HEAD),
    ("leak", "ln -s repo ../r2 && git -C ../r2 checkout -q main && git merge leak", MOVED_HEAD),
    # a name that is a branch and a tag: checkout takes the branch
    ("tagged", "git checkout -q topic && git merge leak", SECRET),
    ("tagged", "git switch -q topic && git merge leak", SECRET),
    ("tagged-bisect", "git bisect reset; git merge leak", SECRET),
    ("tagged-bisect", "git bisect reset topic; git merge leak", SECRET),
    # --root: the first word is the branch
    ("leak", "git rebase --root --onto leak topic", SECRET),
    ("leak", "git rebase --onto leak --root topic", SECRET),
    ("leak", "git rebase --root --onto=leak feat", SECRET),
    # state the command itself changes before a step reads it
    ("tagged-bisect", "printf 'main\\n' > .git/BISECT_START; git bisect reset; git merge leak",
     MOVED_HEAD),
    ("worktree", "pushd ../wt >/dev/null && git merge leak", SECRET),
    ("stopped", "git rebase --abort && git merge leak", MOVED_HEAD),
    ("stopped", "git rebase --continue && git merge leak", MOVED_HEAD),
    ("stopped", "git rebase --skip && git merge leak", MOVED_HEAD),
    ("stopped", "git rebase --quit && git merge leak", MOVED_HEAD),
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
    ("main", f"{P} merge -q --message --quit feat"),                # the message is `--quit`
    ("main", f"{P} merge -q --quit"),
    ("main", f"{P} pull -q --refmap x . feat"),
    ("main", f"{P} --attr-source HEAD merge -q --ff-only feat"),
    ("main", f"{P} --exec-path=/nowhere merge -q --ff-only feat"),
    ("main", "git --version merge leak"),                           # git prints and runs no verb
    ("main", "git --list-cmds=main merge leak"),
    ("main", f"{P} branch -f other main && {P} merge -q --ff-only feat"),
    ("main", f"{P} branch -f feat-2 main && {P} merge -q --ff-only feat"),
    ("main", f"{P} branch -d topic && {P} merge -q --ff-only feat"),
    ("topic", f"{P} fetch origin && {P} rebase origin/main"),
    ("main", "GIT -C repo merge -q --ff-only feat"),
    ("main", "cd repo && git merge -q feat # it's"),
    ("main", "cd repo && echo a #b && git merge -q leak"),          # the merge is in the comment
    ("main", "cd repo && cat <<'EOF' >notes.txt\nit's what we merged\nEOF"),
    ("main", "git " * 20_000 + "'"),                                # unreadable, names no verb
]
IN_REPO_PASSES = [
    ("alias", "git mg --ff-only feat"),
    ("alias", "git st"),
    ("alias", "git config user.name me && git st"),
    ("main", "git config user.name me && git merge -q feat"),
    ("main", "git config alias.x merge && git status"),
    ("main", "git -c alias.status='merge leak' status"),            # git never aliases a builtin
    ("leak", "git checkout main && git merge feat"),
    ("main", "git rebase -q main topic && git merge feat"),
    ("leak", "git rebase -q main topic && git merge feat"),
    ("main", "git rebase main && git merge feat"),                  # no branch word: HEAD stays
    ("main", "git bisect reset; git merge feat"),                   # no bisect in progress
    ("main", "git checkout --orphan pages && git " + "commit --allow-empty -m init"),
    ("feat", "git checkout main && git merge feat"),
    ("topic", "git checkout main && git merge feat"),
    ("topic", "git checkout -qf main && git merge feat"),
    ("topic", "git checkout feat && git checkout main && git merge -"),  # `-` is feat, or topic
    ("topic", "git checkout main && git checkout leak && git merge feat"),
    ("topic", "git checkout main && git merge feat --no-ff -m 'Merge HEAD of feat'"),
    ("leak", "git checkout main && git pull"),
    ("leak", "git checkout nosuch && git pull"),
    ("main", "git checkout a.py && git merge feat"),                # a file: HEAD stays
    ("topic", "git checkout main -- a.py && git merge feat"),
    ("topic", "git reset --hard origin/main && git merge feat"),
    ("topic", "git reset HEAD a.py && git merge feat"),
    ("leak", "git reset --hard main && git merge feat"),
    ("leak", "git checkout main && git revert HEAD"),               # HEAD is main: nothing flagged
    ("feat", "git reset --soft HEAD~1 && " + COMMIT),               # the squash idiom, clean
    ("leak", "git checkout main && " + COMMIT),                     # nothing staged
    ("main", "false && git checkout -q feat ; git merge feat"),
    ("main", "if false; then git checkout -q feat; fi; git rebase feat"),
    ("main", "cat <<EOF\ngit checkout -q feat\nEOF\ngit merge feat"),
    ("main", "git reset --hard feat && git merge feat"),
    ("main", "false && git checkout -q feat ; git merge --squash feat"),
    # real options of git 2.54 the closed tables lacked
    ("feat", "git revert --no-edit --pretty=oneline HEAD"),
    ("feat", "git rebase --allow-empty-message main"),
    # paths from a tree that holds nothing flagged, or from the index or HEAD
    ("feat", f"git checkout main -- a.py && {COMMIT}"),
    ("feat", f"git checkout -- a.py && {COMMIT}"),
    ("feat", f"git restore --staged a.py && {COMMIT}"),
    ("feat", f"git reset -q HEAD -- a.py && {COMMIT}"),
    ("main", "git checkout leak -- c.py"),                          # no commit follows
    ("main", f"git checkout --orphan pages feat && {COMMIT}"),
    ("main", "git bisect log; git merge -q feat"),
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
    f'{C} -m "fix: handle empty input"', f"{C} -q" + HEREDOC, f'{C} -q -m "it\'s done" # don\'t amend',
    f'git add a.py && {C} -q -m "update a"  # stage, then commit', f"{C} --amend --no-edit",
    f'{C} --amend -m "reword the subject"', f'{C} -S -m "signed"', f'{C} --signoff -m "with trailer"',
    f'{C} --no-verify -m "wip"', f'{C} -am "update a"', f'{C} --allow-empty -m "trigger ci"',
    f'{C} -m "subject" -m "body paragraph"', f"{C} --fixup HEAD", f'git -c user.name=Someone commit -m "x"',
    "git merge feat", "git merge --no-edit feat", "git merge --no-edit feat # don't squash",
    "git merge --no-ff feat", "git merge --no-ff -m 'Merge feat into topic' feat",
    "git merge --ff-only feat", "git merge --squash feat", "git merge -X theirs feat",
    "git merge -Xours feat", "git merge --strategy-option=theirs feat", "git merge -s ort feat",
    "git merge --allow-unrelated-histories feat", "git merge --no-verify feat",
    "git merge --no-commit --no-ff feat", "git merge -S feat", "git merge --signoff feat",
    "git merge --autostash feat", "git merge --abort", "git merge --continue", "git merge --quit",
    "git merge origin/main", "git merge --no-ff --log feat", "git merge feat --no-ff",
    "git merge -m \"Merge branch 'feat'\" feat", "git merge -q feat", "git merge --stat feat",
    "git merge --no-stat feat", "git merge -e feat", "git merge --no-squash feat",
    "git merge -F msg.txt feat", "git merge --cleanup=strip feat", "git merge --log=20 feat",
    "git merge --gpg-sign=ABCD1234 feat", "git merge --no-gpg-sign feat", "git merge -",
    "git pull", "git pull # sync", "git pull --rebase", "git pull --rebase origin main",
    "git pull --ff-only", "git pull --ff-only origin main", "git pull --no-rebase origin main",
    "git pull --autostash --rebase", "git pull origin main --allow-unrelated-histories",
    "git pull --no-edit", "git pull --prune", "git pull --tags", "git pull -q",
    "git pull --rebase=interactive", "git pull --no-ff", "git pull --squash origin main",
    "git pull --recurse-submodules", "git pull --all", "git pull --depth 1", "git pull --depth=1",
    "git pull -X theirs origin main", "git pull --no-commit origin main", "git pull --verbose",
    "git pull --force", "git pull --no-tags", "git pull origin main --rebase",
    "git push", "git push -u origin topic", "git push origin topic  # publish",
    "git push --force-with-lease origin topic", "git push --tags", "git push --set-upstream origin topic",
    "git push origin HEAD", "git push --no-verify origin topic",
    "git rebase main", "git rebase -i main", "git rebase --onto main feat topic # move it",
    "git rebase --continue", "git rebase --abort", "git rebase --skip", "git rebase --autostash main",
    "git rebase -i --autosquash main", "git rebase origin/main", "git rebase --no-verify main",
    "git rebase --rebase-merges main", "git rebase -r main", "git rebase --root",
    "git rebase --update-refs main", "git rebase -X theirs main", "git rebase --exec 'make test' main",
    "git rebase --keep-base main", "git rebase main --autostash",
    "GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash main", "GIT_EDITOR=true git rebase --continue",
    "git cherry-pick feat", "git cherry-pick -x feat", "git cherry-pick --no-commit feat",
    "git cherry-pick -n feat", "git cherry-pick --continue", "git cherry-pick --abort",
    "git cherry-pick --skip", "git cherry-pick main..feat", "git cherry-pick -m 1 feat",
    "git cherry-pick --mainline 1 feat", "git cherry-pick -e feat", "git cherry-pick -s feat",
    "git cherry-pick --allow-empty feat", "git cherry-pick -X theirs feat",
    "git cherry-pick --strategy=recursive feat", "git cherry-pick -S feat", "git cherry-pick --quit",
    "git revert HEAD", "git revert --no-edit HEAD", "git revert -n HEAD", "git revert --no-commit HEAD",
    "git revert --abort", "git revert --continue", "git revert -m 1 HEAD", "git revert -s HEAD",
    "git fetch", "git fetch origin", "git fetch --all --prune", "git fetch origin main",
    "git fetch --tags", "git fetch origin && git rebase origin/main",
    "git fetch origin && git merge origin/main", "git fetch -p && git pull --rebase",
    "git worktree add ../wt -b side main", "git worktree add ../wt2 feat",
    "git worktree add -q ../wt2 feat && git -C ../wt2 status # look", "git worktree list",
    "git worktree add --detach ../wt3 main",
    "git status # it's fine", "git log --oneline -5 | head -3 && git merge --no-edit feat",
    "git stash push -u -m wip-a && git pull --rebase", "git checkout feat && git merge main",
    "git switch feat && git merge main", f"git checkout -b fresh && {C} -m 'start fresh'",
    f"git switch -c fresh && {C} -m 'start fresh'", "git checkout feat && git pull",
    "git checkout feat && git rebase main", "git --no-pager log -3", "git am --abort",
    "git am 0001-feat.patch", "git am -3 0001-feat.patch", "git am --3way --signoff 0001-feat.patch",
    "git am --continue", "git reset --hard HEAD~1", "git reset --hard origin/main",
    f"{C} -m 'one' && git push origin HEAD", "git branch -d old && git merge feat",
    "git merge feat && git branch -d feat", "cd . && git merge feat", "git -C . merge feat",
    "env GIT_EDITOR=true git merge feat",
]


@pytest.mark.parametrize("command", EVERYDAY, ids=lambda v: str(v)[:60])
def test_everyday_line_stays_allowed(scene, tmp_path, command):
    for hook in ("git_commit_check.py", "merge_guard_pre.py"):
        done = run(hook, command, scene("work") / "repo", tmp_path)
        assert not denied(done), (hook, command, done.stdout, done.stderr)


def test_the_everyday_table_is_wide_enough():
    assert len(set(EVERYDAY)) >= 40


NFC, NFD = unicodedata.normalize("NFC", "café"), unicodedata.normalize("NFD", "café")


@pytest.mark.parametrize("mover,args,word", [
    ("branch", ["-f", NFC, "leak"], NFD),                         # one name on a normalising disk
    ("branch", ["-f", NFD, "leak"], NFC),
    ("branch", ["-f", "Straße", "leak"], "STRASSE"),
    ("checkout", ["-B", "feat", "leak"], "refs/heads/FEAT"),
    ("switch", ["--force-create=feat", "leak"], "feat@{0}"),
    ("worktree", ["add", "-Bfeat", "../wt"], "main..feat"),
    ("fetch", [".", "+leak:refs/heads/feat"], "feat"),
    ("update-ref", ["refs/heads/feat", "leak"], "heads/feat^2"),
])
def test_a_moved_name_is_found_in_any_spelling(mover, args, word):
    moved = routes.moved_names(mover, args, None)
    assert routes.moved_word("merge", ["-q", word], moved) == word


@pytest.mark.parametrize("word", ["feat-2", "my/feat", "feature", "ｆeat", "xfeat", "feat/x"])
def test_another_name_is_not_taken_for_the_moved_one(word):
    moved = routes.moved_names("branch", ["-f", "feat", "leak"], None)
    assert routes.moved_word("merge", [word], moved) is None


@pytest.mark.parametrize("words,verb", [
    (["git", "--exec-path=/x", "merge", "leak"], "merge"),
    (["git", "-C", "repo", "--attr-source", "HEAD", "merge"], "merge"),
    (["git", "--git-dir=.git", "--no-pager", "-c", "a.b=c", "pull"], "pull"),
    (["git", "--exec-path"], None), (["git", "--version", "merge"], None),
    (["git", "--list-cmds=main", "merge"], None), (["git", "-h"], None), (["git"], None),
])
def test_git_options_before_the_verb(words, verb):
    at, _ = routes.git_globals(words)
    assert (words[at] if at is not None else None) == verb


@pytest.mark.parametrize("option", ["--Exec-path=/x", "--version=1", "-cfoo", "--list-cmds",
                                    "-Ｃ", "--work-tree x", "-" + "q" * 70_000])
def test_an_unknown_option_of_git_is_refused(option):
    with pytest.raises(routes.Unreadable):
        routes.git_globals(["git", option, "merge", "leak"])
