"""A small local runner for the `run:` steps of a GitHub Actions workflow, for tests.

Location: pact-plugin/tests/workflow_runner_fixture.py. Used by
tests/test_unattended_ci_template.py to run templates/ci/unattended-compact.yml without a
GitHub runner: each `run:` step goes to `bash -e` with the step's env resolved, and each
`uses:` step goes to a handler the test supplies (a checkout becomes a copy, an artifact upload
a file copy). Stub `gh`, `git push`, `docker run` and `claude` programs on PATH record every
call to a log; with no claude body passed, the `claude` stub refuses (exit 97).

The expression evaluator covers what the template uses and nothing more: dotted context paths,
'string' literals, ==, !=, && and ||, with GitHub's result for a missing value ('').
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import yaml

EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
REAL_GIT = shutil.which("git") or "/usr/bin/git"

STUB_GH = r"""#!/usr/bin/env python3
import json, os, sys
log = os.environ["STUB_LOG"]
with open(log, "a") as handle:
    handle.write(json.dumps({"tool": "gh", "argv": sys.argv[1:],
                             "token": bool(os.environ.get("GH_TOKEN"))}) + "\n")
if not os.environ.get("GH_TOKEN"):
    sys.exit("gh: no GH_TOKEN; to authenticate, set it")
args = sys.argv[1:]
role, issue = os.environ.get("STUB_ROLE", "none"), os.environ.get("STUB_ISSUE_JSON", "{}")
if args[:1] == ["api"] and "permission" in args[1]:
    print(role)
elif args[:1] == ["api"] and "/issues/" in args[1] and "--jq" in args:
    print(json.loads(issue).get(args[args.index("--jq") + 1].lstrip("."), ""))
elif args[:1] == ["api"] and "/issues/" in args[1]:
    print(issue)
elif args[:2] == ["pr", "create"]:
    print("https://github.example/o/r/pull/1")
else:
    sys.exit("gh stub: refused " + " ".join(args))
"""

STUB_GIT = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
words, i = [], 0
while i < len(args):
    if args[i] in ("-c", "-C"):
        i += 2
        continue
    words.append(args[i])
    i += 1
if words[:1] == ["push"]:
    with open(os.environ["STUB_LOG"], "a") as handle:
        handle.write(json.dumps({"tool": "git-push", "argv": args,
                                 "token": bool(os.environ.get("GH_TOKEN"))}) + "\n")
    sys.exit(0)
os.execv(os.environ["STUB_REAL_GIT"], [os.environ["STUB_REAL_GIT"], *args])
"""

# The CLI install step: recorded, never run (no network in a test).
STUB_NPM = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_LOG"], "a") as handle:
    handle.write(json.dumps({"tool": "npm", "argv": sys.argv[1:],
                             "token": bool(os.environ.get("GH_TOKEN"))}) + "\n")
"""

# `claude` when a test passes no body: refuse, so a test never reaches the developer's real
# claude (the work job runs it with bypassPermissions).
STUB_CLAUDE_REFUSE = r"""#!/bin/sh
echo "claude stub: this test passed no claude body to write_stubs, so claude may not run" >&2
exit 97
"""

# `docker run`: recorded, then the command runs on the host with only the environment the -e
# flags pass in, as in a container. Every mount must keep its path, so the command sees the
# paths it would see inside the container. HOME moves to STUB_DOCKER_HOME, and PATH and the
# STUB_ variables pass through so the other stubs still work.
STUB_DOCKER = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as handle:
    handle.write(json.dumps({"tool": "docker", "argv": args,
                             "token": bool(os.environ.get("GH_TOKEN"))}) + "\n")
if args[:1] != ["run"]:
    sys.exit("docker stub: only run")
valued = {"-e", "-v", "-w", "--user", "--cap-drop", "--security-opt", "--network"}
env = {key: value for key, value in os.environ.items() if key.startswith("STUB_") or key == "PATH"}
cwd, i = None, 1
while args[i].startswith("-"):
    if args[i] not in valued:
        i += 1
        continue
    flag, value = args[i], args[i + 1]
    i += 2
    if flag == "-e":
        name, sep, given = value.partition("=")
        if sep or name in os.environ:
            env[name] = given if sep else os.environ[name]
    elif flag == "-v":
        source, target = value.split(":")[:2]
        if source != target:
            sys.exit("docker stub: the mount " + value + " changes the path")
    elif flag == "-w":
        cwd = value
env["HOME"] = os.environ["STUB_DOCKER_HOME"]
sys.exit(subprocess.run(args[i + 1:], cwd=cwd, env=env).returncode)
"""


def load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if True in doc and "on" not in doc:   # YAML 1.1 reads the key `on` as True
        doc["on"] = doc.pop(True)
    return doc


def lookup(path: str, ctx: dict):
    value = ctx
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return ""
        value = value[part]
    return value


def operand(text: str, ctx: dict):
    text = text.strip()
    if text.startswith("'") and text.endswith("'"):
        return text[1:-1]
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if not re.fullmatch(r"[A-Za-z_][\w.-]*", text):
        raise ValueError(f"expression not supported by the test runner: {text!r}")
    return lookup(text, ctx)


def evaluate(expr: str, ctx: dict):
    for alternative in expr.split("||"):
        value = True
        for term in alternative.split("&&"):
            match = re.fullmatch(r"(.+?)\s*(==|!=)\s*(.+)", term.strip())
            if match:
                left, right = operand(match[1], ctx), operand(match[3], ctx)
                value = (left == right) if match[2] == "==" else (left != right)
            else:
                value = operand(term, ctx)
            if not value:
                break
        if value:
            return value
    return value


def render(text, ctx: dict) -> str:
    if not isinstance(text, str):
        return "" if text is None else str(text).lower() if isinstance(text, bool) else str(text)
    return EXPR.sub(lambda match: str(evaluate(match[1], ctx)), text)


def write_stubs(folder: Path, claude_body: str = "") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    scripts = {"gh": STUB_GH, "git": STUB_GIT, "npm": STUB_NPM, "docker": STUB_DOCKER,
               "claude": claude_body or STUB_CLAUDE_REFUSE}
    for name, body in scripts.items():
        path = folder / name
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return folder


def read_outputs(path: Path) -> dict:
    outputs = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                outputs[key] = value
    return outputs


def calls(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


class JobResult:
    def __init__(self, ran: bool, failed_step: str = "", outputs=None, logs=None):
        self.ran, self.failed_step = ran, failed_step
        self.outputs, self.logs = outputs or {}, logs or []

    @property
    def ok(self) -> bool:
        return self.ran and not self.failed_step


def run_job(doc: dict, job_id: str, ctx: dict, *, workspace: Path, temp: Path, stubs: Path,
            uses, extra_env: dict | None = None) -> JobResult:
    """Run one job; `uses(step, with_values, ctx)` handles every `uses:` step."""
    job = doc["jobs"][job_id]
    if "if" in job and not evaluate(job["if"], ctx):
        return JobResult(ran=False)
    steps: dict = {}
    ctx = {**ctx, "steps": steps, "env": {}}
    env = {}
    for layer in (doc.get("env") or {}, job.get("env") or {}):
        env.update({key: render(value, ctx) for key, value in layer.items()})
    ctx["env"] = env
    logs = []
    for index, step in enumerate(job["steps"]):
        name = step.get("name") or step.get("uses") or f"step {index}"
        if "uses" in step:
            values = {key: render(value, ctx) for key, value in (step.get("with") or {}).items()}
            uses(step, values, ctx)
            continue
        output_file = temp / f"output-{job_id}-{index}"
        step_env = {key: render(value, ctx) for key, value in (step.get("env") or {}).items()}
        process_env = {
            "PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(temp / "home"), "STUB_LOG": str(temp / "calls.jsonl"),
            "STUB_REAL_GIT": REAL_GIT, "STUB_TEMP": str(temp),
            "STUB_DOCKER_HOME": str(temp / "container-home"), "GITHUB_OUTPUT": str(output_file),
            "GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(temp),
            "GITHUB_REPOSITORY": lookup("github.repository", ctx),
            "GITHUB_REF": lookup("github.ref", ctx),
            "GITHUB_EVENT_NAME": lookup("github.event_name", ctx),
            **(extra_env or {}), **env, **step_env}
        cwd = workspace / step.get("working-directory", ".")
        # GitHub substitutes ${{ }} into the script text before the shell sees it; so does this.
        done = subprocess.run(["bash", "-e", "-c", render(step["run"], ctx)], cwd=cwd, env=process_env,
                              capture_output=True, text=True, timeout=120)
        logs.append((name, done.returncode, done.stdout, done.stderr))
        if "id" in step:
            steps[step["id"]] = {"outputs": read_outputs(output_file)}
        if done.returncode != 0:
            return JobResult(ran=True, failed_step=name, logs=logs)
    outputs = {key: render(value, ctx) for key, value in (job.get("outputs") or {}).items()}
    return JobResult(ran=True, outputs=outputs, logs=logs)
