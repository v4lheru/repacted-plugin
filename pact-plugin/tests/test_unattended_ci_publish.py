"""The publish step of the unattended CI template, and the guard a CI run's model meets.

Location: pact-plugin/tests/test_unattended_ci_publish.py. Subjects:
templates/ci/publish_change.py (the `publish` job of templates/ci/unattended-compact.yml) and
the merge guard plus the unattended gate as a `claude -p` run with REPACT_UNATTENDED=1 meets
them through hooks/dispatch.py.

The patch is hostile input from a model: each refusal row checks that nothing was pushed, no
pull request was opened and nothing from the patch reached the working tree.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from workflow_runner_fixture import calls, write_stubs

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPT = PLUGIN / "templates" / "ci" / "publish_change.py"
DISPATCH = PLUGIN / "hooks" / "dispatch.py"
TOY = PLUGIN / "examples" / "toy"
IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@example.invalid")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *IDENTITY, "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repos(tmp_path):
    """(the base checkout publish works in, a model's checkout to make patches in)."""
    base = tmp_path / "base"
    shutil.copytree(TOY, base, ignore=shutil.ignore_patterns("fixtures", "__pycache__"))
    git(base, "init", "-q", "-b", "main")
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "base")
    model = tmp_path / "model"
    subprocess.run(["git", "clone", "-q", str(base), str(model)], check=True)
    return base, model


def patch_of(model: Path, tmp_path: Path) -> Path:
    git(model, "add", "-A")
    patch = tmp_path / "change.patch"
    patch.write_bytes(subprocess.run(
        ["git", "-C", str(model), "diff", "--cached", "--binary", "--no-renames", "HEAD"],
        check=True, capture_output=True).stdout)
    return patch


def publish(base: Path, patch: Path, tmp_path: Path, *extra: str, base_branch="main"):
    stubs = write_stubs(tmp_path / "stubs")
    env = {**os.environ, "PATH": f"{stubs}{os.pathsep}{os.environ['PATH']}",
           "STUB_LOG": str(tmp_path / "calls.jsonl"), "STUB_REAL_GIT": shutil.which("git"),
           "GH_TOKEN": "ghs_stub", "GITHUB_REPOSITORY": "o/r"}
    done = subprocess.run([sys.executable, str(SCRIPT), "--repo", str(base), "--patch",
                           str(patch), "--issue", "7", "--run-id", "123", "--base-branch",
                           base_branch, *extra], env=env, capture_output=True, text=True)
    return done, calls(tmp_path / "calls.jsonl")


def test_a_clean_patch_becomes_one_draft_pr_on_a_new_branch(repos, tmp_path):
    base, model = repos
    source = (model / "wordcount.py").read_text().replace('len(text.split(" "))', "len(text.split())")
    (model / "wordcount.py").write_text(source)
    done, made = publish(base, patch_of(model, tmp_path), tmp_path)
    assert done.returncode == 0, done.stderr
    assert [(call["tool"], call["argv"][-1]) for call in made][:1] == [
        ("git-push", "HEAD:refs/heads/repacted/issue-7-run-123")]
    assert "--force" not in made[0]["argv"]
    create = made[1]["argv"]
    assert create[:2] == ["pr", "create"] and "--draft" in create
    assert create[create.index("--base") + 1] == "main"
    assert "len(text.split())" in git(base, "show", "HEAD:wordcount.py")


def make_workflow(model: Path):
    (model / ".github" / "workflows").mkdir(parents=True)
    (model / ".github" / "workflows" / "evil.yml").write_text("on: push\n")


def make_agent_config(model: Path):
    (model / ".claude").mkdir()
    (model / ".claude" / "settings.json").write_text('{"hooks": {}}\n')


def make_mcp(model: Path):
    (model / ".mcp.json").write_text("{}\n")


def make_policy(model: Path):
    (model / ".repacted").mkdir()
    (model / ".repacted" / "policy.json").write_text("{}\n")


def make_symlink(model: Path):
    (model / "link").symlink_to("/etc/hosts")


def make_mode_change(model: Path):
    path = model / "wordcount.py"
    path.chmod(path.stat().st_mode | 0o111)


def make_submodule(model: Path):
    """An embedded repository: `git add -A` stages it as a gitlink (mode 160000)."""
    nested = model / "vendored"
    nested.mkdir()
    git(nested, "init", "-q")
    (nested / "x").write_text("x\n")
    git(nested, "add", "x")
    git(nested, "commit", "-q", "-m", "x")


@pytest.mark.parametrize("make", [make_workflow, make_agent_config, make_mcp, make_policy,
                                  make_symlink, make_mode_change, make_submodule],
                         ids=lambda make: make.__name__[5:])
def test_a_hostile_patch_is_refused_and_nothing_is_pushed(repos, tmp_path, make):
    base, model = repos
    make(model)
    (model / "README.md").write_text("an innocent edit beside it\n")
    done, made = publish(base, patch_of(model, tmp_path), tmp_path)
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert "refused, nothing pushed" in done.stderr
    assert made == []
    assert git(base, "status", "--porcelain", "--untracked-files=all") == ""
    assert not (base / ".github").exists() and not (base / "link").is_symlink()


def refused_without_a_push(base: Path, model: Path, tmp_path: Path, path: str, data: bytes):
    target = model / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    (model / "README.md").write_text("an innocent edit beside it\n")
    done, made = publish(base, patch_of(model, tmp_path), tmp_path)
    assert done.returncode == 1 and "refused, nothing pushed" in done.stderr, (path, done)
    assert made == []
    assert git(base, "status", "--porcelain", "--untracked-files=all") == ""


# Config a maintainer's tools load or run, at any depth and in any case: an agent session,
# an editor or a shell on opening the folder, git itself, and the review routing.
@pytest.mark.parametrize("path", ["pkg/.claude/settings.json", "pkg/.Repacted/policy.json",
                                  "sub/.mcp.json", "CLAUDE.md", "docs/Claude.local.md",
                                  ".vscode/tasks.json", "app/.devcontainer/devcontainer.json",
                                  ".envrc", ".gitmodules", "CODEOWNERS", "docs/CODEOWNERS"])
def test_tool_config_at_any_depth_is_refused(repos, tmp_path, path):
    refused_without_a_push(*repos, tmp_path, path, b"{}\n")


# linguist-generated and -diff fold a file out of the review a human gives the PR.
@pytest.mark.parametrize("path", [".gitattributes", "src/.gitattributes"])
def test_a_gitattributes_file_at_any_depth_is_refused(repos, tmp_path, path):
    refused_without_a_push(*repos, tmp_path, path, b"*.py linguist-generated=true -diff\n")


def test_a_binary_change_is_refused(repos, tmp_path):
    refused_without_a_push(*repos, tmp_path, "tests/helper.so", b"\x7fELF\x00\x01\x02" * 50)


def test_other_dot_paths_are_listed_at_the_top_of_the_pr_body(repos, tmp_path):
    base, model = repos
    (model / ".eslintrc.json").write_text("{}\n")
    (model / "src").mkdir()
    (model / "src" / ".prettierrc").write_text("{}\n")
    done, made = publish(base, patch_of(model, tmp_path), tmp_path)
    assert done.returncode == 0, done.stderr
    create = made[1]["argv"]
    body = create[create.index("--body") + 1]
    first = body.split("\n\n")[0]
    assert "`.eslintrc.json`" in first and "`src/.prettierrc`" in first


def test_a_patch_that_does_not_apply_is_refused(repos, tmp_path):
    base, model = repos
    (model / "README.md").write_text("changed\n")
    patch = patch_of(model, tmp_path)
    (base / "README.md").write_text("moved on\n")
    git(base, "commit", "-qam", "moved")
    done, made = publish(base, patch, tmp_path)
    assert done.returncode == 1 and "git apply --check refused it" in done.stderr
    assert made == []


def test_an_oversized_patch_is_refused(repos, tmp_path):
    base, model = repos
    (model / "big.txt").write_text("x" * 5000)
    done, made = publish(base, patch_of(model, tmp_path), tmp_path, "--max-bytes", "1000")
    assert done.returncode == 1 and made == []


def test_an_empty_patch_opens_no_pull_request(repos, tmp_path):
    base, model = repos
    done, made = publish(base, patch_of(model, tmp_path), tmp_path)
    assert done.returncode == 0 and "changed nothing" in done.stdout
    assert made == []


@pytest.mark.parametrize("base_branch", ["-f", "main;rm -rf /", "a..b", ""])
def test_an_unusable_base_branch_is_a_usage_error(repos, tmp_path, base_branch):
    base, model = repos
    (model / "README.md").write_text("changed\n")
    done, made = publish(base, patch_of(model, tmp_path), tmp_path, base_branch=base_branch)
    assert done.returncode == 2 and made == []


# --- the guard the model meets in the work job ------------------------------------------

def hook(event_args, payload, env):
    return subprocess.run([sys.executable, str(DISPATCH), *event_args],
                          input=json.dumps(payload), capture_output=True, text=True, env=env,
                          timeout=120)


def decision(done) -> str:
    """'deny <reason>' from the hook's output, or ''."""
    for line in done.stdout.splitlines():
        if line.startswith("{"):
            output = json.loads(line).get("hookSpecificOutput") or {}
            if output.get("permissionDecision"):
                return f"{output['permissionDecision']} {output.get('permissionDecisionReason', '')}"
    return ""


@pytest.mark.parametrize("command", ["gh pr merge 1 --squash --admin",
                                     "git push --force origin repacted/issue-7-run-123",
                                     "git push origin HEAD:main"])
def test_in_unattended_mode_a_merge_or_force_or_default_push_is_refused(repos, tmp_path, command):
    base, _ = repos
    env = {key: value for key, value in os.environ.items() if not key.startswith("CLAUDE_")}
    env.update(HOME=str(tmp_path / "home"), CLAUDE_CONFIG_DIR=str(tmp_path / "config"),
               CLAUDE_PROJECT_DIR=str(base), REPACT_UNATTENDED="1")
    session = {"session_id": "ci-run", "cwd": str(base), "hook_event_name": "PreToolUse"}
    question = {"question": "Merge pull request 1 now?", "header": "Merge",
                "options": [{"label": "Yes (Recommended)", "description": "merge"},
                            {"label": "No", "description": "wait"}]}
    asked = hook(["PreToolUse", "AskUserQuestion"],
                 {**session, "tool_name": "AskUserQuestion",
                  "tool_input": {"questions": [question]}}, env)
    assert decision(asked).startswith("deny unattended_gate:"), asked.stdout
    # The approving question was refused, so no approval exists and the command stays refused.
    ran = hook(["PreToolUse", "Bash"],
               {**session, "tool_name": "Bash", "tool_input": {"command": command}}, env)
    assert decision(ran).startswith("deny merge_guard_pre:"), (ran.stdout, ran.stderr)
    plain = hook(["PreToolUse", "Bash"],
                 {**session, "tool_name": "Bash", "tool_input": {"command": "git status"}}, env)
    assert not decision(plain).startswith("deny"), plain.stdout
