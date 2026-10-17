"""
Location: pact-plugin/tests/test_git_commit_routes.py
Summary: The commit check scans what the other commit-creating git verbs bring in
         (hooks/shared/commit_routes.py): cherry-pick and revert per named commit, merge,
         rebase and `pull .` per commit since the merge base (a --squash merge its net diff), am
         from its patch files with mail encodings decoded, commit-tree against its parent, and
         each --continue as a plain commit. A same-line `pragma: allowlist secret` passes,
         as for a commit. Boundary rows: quoting, -C, env-located git dirs, nested shells,
         redirects, option-shaped revs, an empty diff, a hostile file name.
Used by: the plugin suite. Plain-commit rows live in tests/test_git_commit_egress.py.
"""
from __future__ import annotations

import base64
import json
import os
import quopri
import subprocess
import sys
from pathlib import Path

import pytest

from project_tasks_support import GIT_ENV, PLUGIN_ROOT

HOOK = PLUGIN_ROOT / "hooks" / "git_commit_check.py"
FAKE = "ghp_" + "Fk9mQ2vLp7Rz4tXk9mQ2vLp7Rz4tXk9mQ2vL"          # github-token shape, fake
PRAGMA = "pragma: allowlist secret"


@pytest.fixture(autouse=True)
def _git_env(monkeypatch):
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


def commit_file(repo: Path, rel: str, text: str, message: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", "-f", "--", rel)
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path) -> Path:
    """main (one root commit) and side (one commit adding the fake token); main checked out."""
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    commit_file(path, ".gitignore", ".env\n", "root")
    git(path, "checkout", "-q", "-b", "side")
    commit_file(path, "leak.py", f'TOKEN = "{FAKE}"\n', "leak")
    git(path, "checkout", "-q", "main")
    return path


def run(repo: Path, command: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd or repo)}
    done = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload),
                          capture_output=True, text=True, timeout=60)
    assert FAKE not in done.stdout + done.stderr, "a value reached the hook output"
    return done


def refused(done: subprocess.CompletedProcess, *needles: str) -> None:
    assert done.returncode == 2, (done.returncode, done.stdout, done.stderr)
    for needle in needles:
        assert needle in done.stdout + done.stderr


def allowed(done: subprocess.CompletedProcess) -> None:
    assert done.returncode == 0, (done.returncode, done.stdout, done.stderr)


@pytest.mark.parametrize("command", [
    "git cherry-pick side",
    "git cherry-pick -n side",
    "git cherry-pick --no-commit -x side",
    "git cherry-pick main..side",
    "git merge side",
    "git merge --no-ff -m 'merge side' side",
    "git merge --squash side",
    "git merge --no-commit side 2>&1",
    "git rebase side",
    "git rebase --onto side main",
    "git commit-tree side^{tree} -p main -m x",
    "git cherry-pick side && git commit -m x",
    "git status; git merge side",
])
def test_route_bringing_a_secret_is_refused_naming_the_line_and_the_marker(repo, command):
    refused(run(repo, command), "leak.py line 1", PRAGMA)


def test_marked_fixture_passes_and_history_is_read_per_commit(repo):
    git(repo, "checkout", "-q", "side")
    commit_file(repo, "leak.py", f'TOKEN = "{FAKE}"  # {PRAGMA}\n', "mark the fixture")
    git(repo, "checkout", "-q", "main")
    allowed(run(repo, "git cherry-pick side"))       # the marking commit adds only the marked line
    allowed(run(repo, "git merge --squash side"))    # a squash commits the net change only
    refused(run(repo, "git merge side"), "leak.py line 1")   # the unmarked commit comes along


def test_merge_reads_a_token_added_then_removed(repo):
    git(repo, "checkout", "-q", "-b", "addrm", "side")
    git(repo, "rm", "-q", "leak.py")
    git(repo, "commit", "-q", "-m", "drop it")
    git(repo, "checkout", "-q", "main")
    refused(run(repo, "git merge --no-ff addrm"), "leak.py line 1")
    allowed(run(repo, "git merge --squash addrm"))


def test_clean_routes_and_nothing_to_read_ahead_are_allowed(repo):
    git(repo, "checkout", "-q", "-b", "clean", "main")
    commit_file(repo, "notes.md", "plain text\n", "notes")
    git(repo, "checkout", "-q", "main")
    for command in ("git merge clean", "git cherry-pick clean", "git rebase clean",
                    "git merge --abort", "git cherry-pick --quit", "git rebase --skip",
                    "git pull", "git stash", "git revert side", "git rebase --root"):
        allowed(run(repo, command))


def test_empty_diff_merge_of_an_already_merged_branch_is_allowed(repo):
    git(repo, "checkout", "-q", "-b", "older", "main")
    git(repo, "checkout", "-q", "main")
    allowed(run(repo, "git merge older"))


def test_revert_scans_the_reversed_diff(repo):
    git(repo, "checkout", "-q", "side")
    git(repo, "rm", "-q", "leak.py")
    git(repo, "commit", "-q", "-m", "drop the token")
    done = run(repo, "git revert HEAD")                             # the revert adds it back
    refused(done, "in leak.py line 1")
    assert "a/leak.py" not in done.stdout + done.stderr
    allowed(run(repo, "git revert HEAD~1"))                         # removing it adds nothing


def test_am_scans_patch_files_and_refuses_standard_input(repo, tmp_path):
    patch_dir = tmp_path / "patches"
    git(repo, "format-patch", "-q", "-1", "side", "-o", str(patch_dir))
    patch = next(patch_dir.glob("*.patch"))
    refused(run(repo, f"git am '{patch}'"), "leak.py")
    refused(run(repo, f"git am -3 < '{patch}'"), "leak.py")
    refused(run(repo, "git am"), "standard input")
    refused(run(repo, f"git am '{patch_dir}'"), "mailbox directory")
    refused(run(repo, "git am missing.patch"), "could not be read")
    refused(run(repo, "git cherry-pick --stdin"), "at run time")
    fifo = tmp_path / "pipe.patch"
    os.mkfifo(fifo)
    refused(run(repo, f"git am '{fifo}'"), "not a regular file")    # never blocks in open()


@pytest.mark.parametrize("name", ["/dev/stdin", "/dev/fd/0", "/proc/self/fd/0", "-"])
def test_am_refuses_standard_input_by_name_when_the_event_comes_from_a_file(repo, tmp_path, name):
    event = tmp_path / "event.json"                 # the hook's stdin is a regular file here
    event.write_text(json.dumps({"tool_name": "Bash", "cwd": str(repo),
                                 "tool_input": {"command": f"git am {name}"}}), encoding="utf-8")
    with event.open("rb") as handle:
        done = subprocess.run([sys.executable, str(HOOK)], stdin=handle, capture_output=True,
                              text=True, timeout=60)
    refused(done, "standard input")


@pytest.mark.parametrize("encoding", ["base64", "quoted-printable"])
def test_am_decodes_mail_bodies(repo, tmp_path, encoding):
    body = git(repo, "format-patch", "-1", "side", "--stdout").split("\n\n", 1)[1]
    encoded = (base64.encodebytes(body.encode()).decode() if encoding == "base64"
               else quopri.encodestring(body.encode()).decode().replace(FAKE[:9], FAKE[:9] + "=\n"))
    mail = (f"From 0 Mon Sep 17 00:00:00 2001\nFrom: t <t@t>\nSubject: [PATCH] leak\n"
            f"MIME-Version: 1.0\nContent-Type: text/plain; charset=UTF-8\n"
            f"Content-Transfer-Encoding: {encoding}\n\n{encoded}")
    patch = tmp_path / "encoded.patch"
    patch.write_text(mail, encoding="utf-8")
    assert FAKE not in mail
    refused(run(repo, f"git am '{patch}'"), "leak.py")


def test_continue_scans_the_index_like_a_commit(repo):
    (repo / "fix.py").write_text(f'TOKEN = "{FAKE}"\n', encoding="utf-8")
    git(repo, "add", "fix.py")
    for verb in ("cherry-pick", "revert", "merge", "rebase", "am"):
        refused(run(repo, f"git {verb} --continue"), "fix.py line 1")
    refused(run(repo, "git am -r"), "fix.py line 1")
    git(repo, "reset", "-q")
    refused(run(repo, "git add fix.py && git cherry-pick --continue"), "fix.py line 1")


@pytest.mark.parametrize("command", [
    "git 'cherry-pick' side",
    'git "merge" side',
    "git cherry\\-pick side",
    "git -c core.pager=cat merge side",
    "bash -c 'git merge side'",
    "sh -c \"cd . && git cherry-pick side\"",
    "env git rebase side",
    "{ git status; git merge side; }",
])
def test_quoted_wrapped_and_nested_verbs_are_read(repo, command):
    refused(run(repo, command), "leak.py")


@pytest.mark.parametrize("command", [
    "git rebase -r side",                    # --rebase-merges, not --continue
    "git merge -m -r side",                  # -r is the message
    "git merge -m --continue side",          # so is --continue
    "git commit --allow-empty -m x && git cherry-pick side",
    "git pull . side",
    "git pull --no-rebase . side:main",
])
def test_option_values_and_later_calls_are_read(repo, command):
    refused(run(repo, command), "leak.py line 1")


def test_second_commit_and_pull_from_a_local_path_are_refused(repo, tmp_path):
    refused(run(repo, "git commit -m a && git commit -m b"), "second commit")
    refused(run(repo, f"git pull '{repo}' side", cwd=tmp_path), "finds no repository")
    refused(run(repo, f"git pull '{repo}' side"), "not fetched yet")


@pytest.mark.parametrize("command", [
    "git rebase -x 'git add u.txt && git commit -m x' --root",
    "git rebase --exec='git commit --amend --no-edit' main",
    "GIT_SEQUENCE_EDITOR='sed -i s/^pick/edit/' git rebase -i main",
])
def test_rebase_steps_that_run_git_are_refused(repo, command):
    refused(run(repo, command))


def test_inert_interactive_rebase_is_read_like_a_rebase(repo):
    refused(run(repo, "GIT_SEQUENCE_EDITOR=: git rebase -i side"), "leak.py line 1")
    allowed(run(repo, "GIT_SEQUENCE_EDITOR=: git rebase -i --autosquash main"))


def test_git_location_by_option_and_environment(repo, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    refused(run(repo, f"git -C '{repo}' cherry-pick side", cwd=elsewhere), "leak.py")
    refused(run(repo, f"cd '{repo}' && git merge side", cwd=elsewhere), "leak.py")
    refused(run(repo, f"GIT_DIR='{repo}/.git' GIT_WORK_TREE='{repo}' git cherry-pick side",
                cwd=elsewhere), "leak.py")
    refused(run(repo, f"git --git-dir='{repo}/.git' merge side", cwd=elsewhere), "leak.py")
    refused(run(repo, f"GIT_DIR='{repo}/.git' GIT_WORK_TREE='{repo}' bash -c 'git merge side'",
                cwd=elsewhere), "leak.py")
    refused(run(repo, f"export GIT_DIR='{repo}/.git'; bash -c 'git merge side'", cwd=elsewhere),
            "leak.py")


def test_option_shaped_rev_is_never_passed_to_git_as_an_option(repo, tmp_path):
    target = tmp_path / "written"
    done = run(repo, f"git cherry-pick -- --output={target}")
    assert not target.exists()
    refused(done)                       # an unresolvable rev denies (fail closed), it never runs


def test_hostile_file_name_is_still_scanned(repo):
    git(repo, "checkout", "-q", "-b", "odd", "main")
    commit_file(repo, "dir with space/ü -n;$(x).py", f'TOKEN = "{FAKE}"\n', "odd name")
    git(repo, "checkout", "-q", "main")
    refused(run(repo, "git cherry-pick odd"), "line 1")
