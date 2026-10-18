"""The unattended CI template stops at a draft pull request and gives the model no write token.

Location: pact-plugin/tests/test_unattended_ci_template.py. Subject:
templates/ci/unattended-compact.yml (recipe: docs/recipes/unattended-ci.md).

Two halves. The structural half reads the YAML: triggers, permissions, pins, timeouts,
concurrency, and no `${{ }}` in a `run:`. The run half executes the template's own `run:`
steps with tests/workflow_runner_fixture.py on a scratch copy of the toy repo, with stub `gh`,
`git push` and `claude` on PATH, and checks what each job did. No network, no real gh, no model.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from workflow_runner_fixture import calls, load, run_job, write_stubs

PLUGIN = Path(__file__).resolve().parent.parent
TEMPLATE = PLUGIN / "templates" / "ci" / "unattended-compact.yml"
COLLECT = PLUGIN / "templates" / "ci" / "collect_change.py"
TOY = PLUGIN / "examples" / "toy"
DOC = load(TEMPLATE)
SHA = re.compile(r"[0-9a-f]{40}")
ALLOWED_ACTIONS = {"actions/checkout", "actions/upload-artifact", "actions/download-artifact"}
# Events a stranger can fire from a fork, a comment or a review on a public repository.
STRANGER_EVENTS = {"pull_request", "pull_request_target", "pull_request_review",
                   "pull_request_review_comment", "issue_comment", "discussion",
                   "discussion_comment", "fork", "watch", "workflow_run", "push", "create",
                   "delete", "check_run", "check_suite", "status", "merge_group"}
WRITES = {"publish": {"contents", "pull-requests"}}


def steps_of(job: str) -> list[dict]:
    return DOC["jobs"][job]["steps"]


def run_blocks():
    for job_id, job in DOC["jobs"].items():
        for step in job["steps"]:
            if "run" in step:
                yield job_id, step.get("name", ""), step["run"]


# --- structure --------------------------------------------------------------------------

def test_triggers_are_a_maintainer_label_and_a_manual_start_only():
    assert set(DOC["on"]) == {"issues", "workflow_dispatch"}
    assert DOC["on"]["issues"] == {"types": ["labeled"]}


def test_no_event_a_fork_comment_or_review_can_fire_starts_it():
    assert not STRANGER_EVENTS & set(DOC["on"])


def test_permissions_are_explicit_least_and_never_admin():
    assert DOC["permissions"] == {}
    for job_id, job in DOC["jobs"].items():
        scopes = job["permissions"]
        assert isinstance(scopes, dict), f"{job_id}: permissions must be a map, not {scopes!r}"
        assert set(scopes.values()) <= {"read", "write", "none"}, job_id
        written = {scope for scope, level in scopes.items() if level == "write"}
        assert written == WRITES.get(job_id, set()), job_id
        assert "administration" not in scopes, job_id


def test_every_action_is_an_allowed_one_pinned_by_full_commit_sha():
    for job_id, job in DOC["jobs"].items():
        for step in job["steps"]:
            if "uses" in step:
                name, _, ref = step["uses"].partition("@")
                assert name in ALLOWED_ACTIONS, step["uses"]
                assert SHA.fullmatch(ref), f"{job_id}: {step['uses']} is not pinned by sha"


def test_every_job_has_a_timeout_and_runs_in_one_concurrency_group():
    for job_id, job in DOC["jobs"].items():
        assert isinstance(job.get("timeout-minutes"), int), job_id
        assert 0 < job["timeout-minutes"] <= 60, job_id
    assert DOC["concurrency"]["group"].startswith("unattended-compact-")


def test_no_run_block_holds_an_expression_so_event_text_goes_through_env_only():
    for job_id, name, script in run_blocks():
        assert "${{" not in script, f"{job_id}/{name}: an expression inside run:"


def test_the_model_job_has_no_write_scope_no_token_and_no_persisted_credential():
    work = DOC["jobs"]["work"]
    assert "write" not in work["permissions"].values()
    for step in steps_of("work"):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
    model = next(step for step in steps_of("work") if "claude -p" in step.get("run", ""))
    rendered = str(model)
    assert "github.token" not in rendered and "GH_TOKEN" not in rendered
    assert model["env"]["REPACT_UNATTENDED"] == "1"
    assert "--max-budget-usd" in model["run"]


def test_the_claude_key_reaches_the_model_step_only():
    holders = [(job_id, step.get("name")) for job_id, job in DOC["jobs"].items()
               for step in job["steps"] if "secrets." in str(step)]
    assert holders == [("work", "Run comPACT unattended")]
    assert "secrets." not in str({key: DOC[key] for key in DOC if key != "jobs"})


def model_step() -> dict:
    return next(step for step in steps_of("work") if "claude -p" in step.get("run", ""))


# Flags that would hand the container a host namespace, a device, a capability or the daemon.
HOST_REACH = ("--privileged", "--pid", "--network", "--net", "--ipc", "--uts", "--userns",
              "--cgroupns", "--device", "--cap-add", "--volumes-from", "--mount")


def test_the_model_runs_in_a_pinned_unprivileged_container_with_only_the_checkout():
    model = model_step()
    words = shlex.split(model["run"].replace("\\\n", " "))
    words = words[words.index("docker"):]
    assert words[:2] == ["docker", "run"] and "$MODEL_IMAGE" in words
    assert re.fullmatch(r"node:[\w.-]+@sha256:[0-9a-f]{64}", DOC["env"]["MODEL_IMAGE"])
    flags = words[:words.index("$MODEL_IMAGE")]
    pairs = list(zip(flags, flags[1:]))
    assert ("--user", "$(id -u):$(id -g)") in pairs
    assert ("--cap-drop", "ALL") in pairs
    assert ("--security-opt", "no-new-privileges") in pairs
    for word in flags:
        assert not word.startswith(HOST_REACH) and "docker.sock" not in word, word
    mounts = [value for flag, value in pairs if flag == "-v"]
    # .git stays writable: comPACT commits and makes worktrees. The host never reads it.
    assert mounts == ["$PWD:$PWD", "$plugin:$plugin:ro",
                      "$RUNNER_TEMP/prompt.md:$RUNNER_TEMP/prompt.md:ro"]
    assert 'plugin="$GITHUB_WORKSPACE/repacted"' in model["run"]
    passed = [value for flag, value in pairs if flag == "-e"]
    assert "ANTHROPIC_API_KEY" in passed, "pass the key by name, so its value is not in argv"
    assert not [value for value in passed if "TOKEN" in value or "ANTHROPIC_API_KEY=" in value]
    # The CLI is installed and run inside the container only, pinned to the exact version.
    hosts = [script for job_id, name, script in run_blocks() if script is not model["run"]]
    assert not [script for script in hosts if "npm " in script or "claude " in script]
    assert '"@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION"' in model["run"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", DOC["env"]["CLAUDE_CODE_VERSION"])


def docker_flags(script: str) -> list[tuple[str, str]]:
    words = shlex.split(script.replace("\\\n", " "))
    words = words[words.index("docker"):]
    flags = words[:words.index("$MODEL_IMAGE")]
    return list(zip(flags, flags[1:]))


def test_the_result_is_exported_by_a_keyless_offline_container_and_collected_without_its_git():
    export = next(step for step in steps_of("work")
                  if step.get("name") == "Export the result as plain files")
    pairs = docker_flags(export["run"])
    assert ("--network", "none") in pairs and ("--cap-drop", "ALL") in pairs
    assert not [value for flag, value in pairs if flag == "-e" and "ANTHROPIC" in value]
    assert "secrets." not in str(export)
    mounts = [value for flag, value in pairs if flag == "-v"]
    assert mounts == ["$PWD:$PWD", "$plugin:$plugin:ro", "$out:$out"]
    collect = next(step for step in steps_of("work")
                   if step.get("name") == "Collect the change as a patch")
    # The host step never enters the model's checkout and runs no git of its own there.
    assert "working-directory" not in collect and "target" not in collect["run"]
    assert not [line for line in collect["run"].splitlines() if line.strip().startswith("git ")]
    assert "collect_change.py\" collect" in collect["run"]
    names = [step.get("name") or step["with"].get("path") for step in steps_of("work")]
    assert names.index("pristine") < names.index("Collect the change as a patch")


def test_no_step_merges_approves_or_force_pushes():
    for job_id, name, script in run_blocks():
        for word in ("gh pr merge", "gh pr review", "--force", "--admin", "git push"):
            assert word not in script, f"{job_id}/{name}: {word}"


# --- runs -------------------------------------------------------------------------------

def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def world(tmp_path):
    """A scratch origin of the toy repo, a workspace, a runner temp and stubs."""
    origin = tmp_path / "origin"
    shutil.copytree(TOY, origin, ignore=shutil.ignore_patterns("fixtures", "__pycache__"))
    git(origin, "init", "-q", "-b", "main")
    git(origin, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "add", "-A")
    git(origin, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
        "commit", "-q", "-m", "base")
    temp = tmp_path / "temp"
    (temp / "home").mkdir(parents=True)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    doc = copy.deepcopy(DOC)
    doc["env"]["REPACTED_SHA"] = "a" * 40
    return {"origin": origin, "temp": temp, "ws": workspace, "doc": doc,
            "sha": git(origin, "rev-parse", "HEAD"), "log": temp / "calls.jsonl"}


def context(world, *, event_name="issues", label="repacted-run", sender="maintainer",
            sender_type="User", ref="refs/heads/main", title="wordcount miscounts words",
            needs=None):
    event = {"sender": {"login": sender, "type": sender_type},
             "repository": {"default_branch": "main"}, "label": {"name": label},
             "issue": {"number": 7, "title": title, "body": "Two spaces count as a word."}}
    if event_name == "workflow_dispatch":
        event.pop("issue")
        event.pop("label")
    return {"github": {"event_name": event_name, "ref": ref, "sha": world["sha"],
                       "repository": "o/r", "run_id": 123, "token": "ghs_stub", "event": event},
            "inputs": {"issue": 7} if event_name == "workflow_dispatch" else {},
            "secrets": {"ANTHROPIC_API_KEY": "sk-stub"}, "vars": {},
            "runner": {"temp": str(world["temp"])}, "needs": needs or {}}


def uses_handler(world, workspace):
    def handle(step, values, ctx):
        action = step["uses"].split("@")[0]
        if action == "actions/checkout":
            dest = workspace / values["path"]
            if values.get("repository"):
                dest.symlink_to(PLUGIN.parent, target_is_directory=True)
            else:
                subprocess.run(["git", "clone", "-q", str(world["origin"]), str(dest)], check=True)
                git(dest, "checkout", "-q", "--detach", values["ref"])
        elif action == "actions/upload-artifact":
            store = world["temp"] / "artifacts" / values["name"]
            store.mkdir(parents=True, exist_ok=True)
            shutil.copy(values["path"], store)
        elif action == "actions/download-artifact":
            Path(values["path"]).mkdir(parents=True, exist_ok=True)
            for item in (world["temp"] / "artifacts" / values["name"]).iterdir():
                shutil.copy(item, values["path"])
    return handle


def job(world, job_id, ctx, *, claude="", role="write", **env):
    """Each job gets its own workspace, as each gets its own runner on GitHub."""
    workspace = world["ws"] / job_id
    workspace.mkdir(exist_ok=True)
    stubs = write_stubs(world["temp"] / f"stubs-{job_id}", claude)
    return run_job(world["doc"], job_id, ctx, workspace=workspace, temp=world["temp"],
                   stubs=stubs, uses=uses_handler(world, workspace),
                   extra_env={"STUB_ROLE": role, **env})


def test_a_write_user_adding_the_label_is_authorized(world):
    result = job(world, "authorize", context(world))
    assert result.ok, result.logs
    assert result.outputs == {"issue": "7", "issue_updated_at": ""}


@pytest.mark.parametrize("role", ["triage", "read", "none"])
def test_a_label_added_without_write_access_is_refused(world, role):
    result = job(world, "authorize", context(world), role=role)
    assert result.ran and result.failed_step, role


def test_another_label_skips_the_run(world):
    assert not job(world, "authorize", context(world, label="bug")).ran


@pytest.mark.parametrize("event_name", ["pull_request_target", "pull_request", "issue_comment"])
def test_a_fork_or_comment_event_is_refused_even_with_the_label(world, event_name):
    result = job(world, "authorize", context(world, event_name=event_name))
    assert result.ran and result.failed_step
    assert "never starts this workflow" in result.logs[-1][2]


def test_a_bot_sender_is_refused(world):
    result = job(world, "authorize", context(world, sender="x[bot]", sender_type="Bot"))
    assert result.failed_step


def test_a_manual_start_from_another_branch_is_refused(world):
    result = job(world, "authorize", context(world, event_name="workflow_dispatch",
                                              ref="refs/heads/feature"))
    assert result.failed_step


def test_the_shipped_placeholder_sha_refuses_until_a_release_is_pinned(world):
    world["doc"]["env"]["REPACTED_SHA"] = DOC["env"]["REPACTED_SHA"]
    result = job(world, "authorize", context(world))
    assert result.failed_step and "REPACTED_SHA" in result.logs[-1][2]


CAPTURE_PROMPT = """#!/usr/bin/env python3
import os, sys
open(os.path.join(os.environ["STUB_TEMP"], "seen-prompt.md"), "w").write(sys.stdin.read())
"""


def test_an_issue_title_with_shell_syntax_stays_text(world):
    title = 'x $(touch "$RUNNER_TEMP/pwned1") `touch pwned2` "; touch pwned3; echo "'
    ctx = context(world, title=title, needs={"authorize": {"outputs": {"issue": "7"}}})
    result = job(world, "work", ctx, claude=CAPTURE_PROMPT)
    assert result.ok, result.logs
    prompt = (world["temp"] / "seen-prompt.md").read_text()
    assert f"Title: {title}" in prompt
    leftovers = [path for path in world["temp"].parent.rglob("pwned*")]
    assert leftovers == []


def test_a_work_job_given_no_claude_body_never_reaches_a_real_claude(world):
    ctx = context(world, needs={"authorize": {"outputs": {"issue": "7"}}})
    result = job(world, "work", ctx)
    assert result.failed_step == "Run comPACT unattended", result.logs
    _, code, _, stderr = result.logs[-1]
    assert code == 97 and "passed no claude body" in stderr


MODEL = """#!/usr/bin/env python3
import json, os, subprocess
seen = {"GH_TOKEN": bool(os.environ.get("GH_TOKEN"))}
open(os.path.join(os.environ["STUB_TEMP"], "model-env.json"), "w").write(json.dumps(seen))
path = "wordcount.py"
text = open(path).read().replace('len(text.split(" "))', "len(text.split())")
open(path, "w").write(text)
for attempt in (["gh", "pr", "merge", "1", "--admin"],
                ["git", "push", "--force", "origin", "HEAD:main"]):
    subprocess.run(attempt)
"""


def test_a_run_ends_at_one_draft_pull_request_on_a_new_branch(world):
    authorize = job(world, "authorize", context(world))
    needs = {"authorize": {"outputs": authorize.outputs}}
    before_work = len(calls(world["log"]))
    work = job(world, "work", context(world, needs=needs), claude=MODEL)
    assert work.ok, work.logs
    seen = (world["temp"] / "model-env.json").read_text()
    assert seen == '{"GH_TOKEN": false}'
    model_calls = calls(world["log"])[before_work:]
    publish = job(world, "publish", context(world, needs=needs))
    assert publish.ok, publish.logs
    with_token = [call for call in calls(world["log"])[before_work + len(model_calls):] if call["token"]]
    assert [call["tool"] for call in with_token] == ["git-push", "gh"]
    push, create = with_token
    assert push["argv"][-2:] == ["origin", "HEAD:refs/heads/repacted/issue-7-run-123"]
    assert not {"--force", "-f", "--force-with-lease", "--mirror"} & set(push["argv"])
    assert create["argv"][:2] == ["pr", "create"] and "--draft" in create["argv"]
    assert create["argv"][create["argv"].index("--base") + 1] == "main"
    # The model's own merge and force-push reached the stubs with no token, and gh refused.
    attempts = [call["argv"][:2] for call in model_calls if call["tool"] not in ("npm", "docker")]
    assert attempts == [["pr", "merge"], ["push", "--force"]]
    assert all(not call["token"] for call in model_calls)
    target = world["ws"] / "publish" / "target"
    assert "len(text.split())" in git(target, "show", "HEAD:wordcount.py")
    assert git(target, "rev-parse", "HEAD~1") == world["sha"]


LEAVE_A_SYMLINK = """#!/usr/bin/env python3
import os
os.symlink("/etc/hosts", "hosts-link")
"""


def test_a_symlink_the_model_leaves_is_refused_before_it_becomes_a_patch(world):
    ctx = context(world, needs={"authorize": {"outputs": {"issue": "7"}}})
    result = job(world, "work", ctx, claude=LEAVE_A_SYMLINK)
    assert result.failed_step == "Collect the change as a patch", result.logs
    assert not (world["temp"] / "out" / "change.patch").exists()


def issue_json(updated_at: str) -> str:
    return json.dumps({"number": 7, "title": "wordcount miscounts words",
                       "body": "Two spaces count as a word.", "updated_at": updated_at})


def test_a_manual_start_refuses_an_issue_edited_after_authorize_read_it(world):
    read = issue_json("2026-10-18T01:00:00Z")
    authorize = job(world, "authorize", context(world, event_name="workflow_dispatch"),
                    STUB_ISSUE_JSON=read)
    assert authorize.outputs == {"issue": "7", "issue_updated_at": "2026-10-18T01:00:00Z"}
    ctx = context(world, event_name="workflow_dispatch",
                  needs={"authorize": {"outputs": authorize.outputs}})
    edited = job(world, "work", ctx, claude=CAPTURE_PROMPT,
                 STUB_ISSUE_JSON=issue_json("2026-10-18T01:05:00Z"))
    assert edited.failed_step == "Write the prompt" and "edited" in edited.logs[-1][3]
    shutil.rmtree(world["ws"] / "work")   # a fresh runner for the second run
    same = job(world, "work", ctx, claude=CAPTURE_PROMPT, STUB_ISSUE_JSON=read)
    assert same.ok, same.logs


# The model's .git is writable in the container, so it can hold any config. On the old design
# the runner's git read that checkout, and a clean filter ran on the host. Each command only
# marks when it runs where RUNNER_TEMP is set, which is the runner, never inside a container.
HOSTILE_GIT = """#!/usr/bin/env python3
import os, subprocess
mark = 'test -z "$RUNNER_TEMP" || touch "$RUNNER_TEMP/ran-on-the-host"'
for key, value in (("core.fsmonitor", mark), ("filter.x.clean", mark + "; cat"),
                   ("filter.x.smudge", "cat"), ("diff.x.textconv", mark + "; cat"),
                   ("core.hooksPath", ".git/hooks")):
    subprocess.run(["git", "config", key, value], check=True)
os.makedirs(".git/info", exist_ok=True)
open(".git/info/attributes", "w").write("* filter=x diff=x\\n")
for hook in ("post-index-change", "pre-commit", "post-checkout"):
    path = os.path.join(".git", "hooks", hook)
    open(path, "w").write("#!/bin/sh\\n" + mark + "\\n")
    os.chmod(path, 0o755)
text = open("wordcount.py").read().replace('len(text.split(" "))', "len(text.split())")
open("wordcount.py", "w").write(text)
"""


def test_a_hostile_git_config_and_hooks_in_the_model_checkout_never_run_on_the_host(world):
    ctx = context(world, needs={"authorize": {"outputs": {"issue": "7"}}})
    result = job(world, "work", ctx, claude=HOSTILE_GIT)
    assert result.ok, result.logs
    assert not (world["temp"] / "ran-on-the-host").exists()
    patch = (world["temp"] / "out" / "change.patch").read_text()
    assert "len(text.split())" in patch and ".git/" not in patch


# What /repacted:worktree-setup does: a worktree under .worktrees/, its branch, a .gitignore
# line in the main checkout, and the work committed in the worktree.
IN_A_WORKTREE = """#!/usr/bin/env python3
import os, subprocess
def git(*args, cwd="."):
    subprocess.run(["git", *args], cwd=cwd, check=True)
open(".gitignore", "a").write(".worktrees/\\n")
for index, name in enumerate(os.environ.get("STUB_WORKTREES", "fix").split()):
    tree = os.path.join(".worktrees", name)
    git("worktree", "add", "-q", tree, "-b", name)
    path = os.path.join(tree, "wordcount.py")
    text = open(path).read().replace('len(text.split(" "))', "len(text.split())")
    open(path, "w").write(text + "# worktree %d\\n" % index)
    git("commit", "-q", "-am", "Count words split by any whitespace.", cwd=tree)
"""


def test_a_change_committed_in_a_worktree_is_collected_and_two_are_refused(world):
    ctx = context(world, needs={"authorize": {"outputs": {"issue": "7"}}})
    result = job(world, "work", ctx, claude=IN_A_WORKTREE)
    assert result.ok, result.logs
    patch = (world["temp"] / "out" / "change.patch").read_text()
    assert "len(text.split())" in patch and "# worktree 0" in patch
    assert ".gitignore" not in patch and ".worktrees" not in patch
    shutil.rmtree(world["ws"] / "work")
    shutil.rmtree(world["temp"] / "out")
    two = job(world, "work", ctx, claude=IN_A_WORKTREE, STUB_WORKTREES="a b")
    assert two.failed_step == "Export the result as plain files", two.logs
    assert not (world["temp"] / "out" / "change.patch").exists()


def special(kind: str, root: Path) -> None:
    if kind == "symlink":
        (root / "hosts-link").symlink_to("/etc/hosts")
    elif kind == "fifo":
        os.mkfifo(root / "pipe")
    elif kind == "worktrees":
        (root / ".worktrees" / "fix").mkdir(parents=True)
        (root / ".worktrees" / "fix" / "wordcount.py").write_text("x = 1\n")
    else:
        (root / "sub" / ".Git").mkdir(parents=True)
        (root / "sub" / ".Git" / "config").write_text("[core]\n\tfsmonitor = true\n")


@pytest.mark.parametrize("kind", ["symlink", "fifo", "worktrees", "git-dir"])
def test_the_host_refuses_an_export_holding_a_symlink_a_fifo_worktrees_or_a_git_dir(
        world, tmp_path, kind):
    export = tmp_path / "export"
    shutil.copytree(TOY, export, ignore=shutil.ignore_patterns("fixtures", "__pycache__"))
    special(kind, export)
    pristine = tmp_path / "pristine"
    subprocess.run(["git", "clone", "-q", str(world["origin"]), str(pristine)], check=True)
    patch = tmp_path / "change.patch"
    done = subprocess.run([sys.executable, str(COLLECT), "collect", "--export", str(export),
                           "--pristine", str(pristine), "--base", world["sha"],
                           "--patch", str(patch)], capture_output=True, text=True)
    assert done.returncode == 1 and "refused" in done.stderr, done.stderr
    assert not patch.exists()


# Found in the first real headless run: the plugin's SessionStart hook writes the project's
# .claude/CLAUDE.md, which publish refuses, so every run was refused. The export keeps the
# top-level .claude/ at the base, committed or not.
PLUGIN_SESSION_NOTES = """#!/usr/bin/env python3
import os, subprocess
os.makedirs(".claude", exist_ok=True)
open(".claude/CLAUDE.md", "w").write("# PACT Framework and Managed Project Memory\\n")
text = open("wordcount.py").read().replace('len(text.split(" "))', "len(text.split())")
open("wordcount.py", "w").write(text)
subprocess.run(["git", "add", "-A"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "Count words split by any whitespace."], check=True)
"""


def test_the_plugins_own_claude_md_is_left_out_so_publish_does_not_refuse_the_run(world):
    ctx = context(world, needs={"authorize": {"outputs": {"issue": "7"}}})
    result = job(world, "work", ctx, claude=PLUGIN_SESSION_NOTES)
    assert result.ok, result.logs
    patch = (world["temp"] / "out" / "change.patch").read_text()
    assert "len(text.split())" in patch and ".claude" not in patch
