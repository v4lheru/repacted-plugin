"""
Equivalence of the dispatcher with the separate hooks it replaced.

For each input in the table, the checks hooks.json matched for it run two ways, each in a
fresh temporary config root and project dir:
  old  each matched script as its own `python3 <script>` process, in hooks.json order;
  new  the one dispatcher entry of hooks.json the host matches for the call's tool, run as
       its `python3 hooks/dispatch.py <event> [<group>]` process.
The two are compared on the decision, the reason text and the exit code, and every
additionalContext of the old run must reach the model in the new one. A refusal's text is
the old hook's own, labelled as the dispatcher shows it by design: `<check>: <reason>
[rule: <id>]`. The oracle below is written apart from dispatch.combine so a combiner
error cannot hide.

Every hooks.json entry the dispatcher took over is matched by at least one input
(test_every_check_is_exercised). Paths of the temporary roots and timestamps are
normalised before text is compared. One designed difference: a security check that dies of
an uncaught exception is a deny with `[rule: <check>.error]` (_fail_closed).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = PLUGIN_ROOT / "hooks"

import dispatch_registry as registry  # noqa: E402  (hooks/ is on the path: tests/conftest.py)
from shared.rules import RULES  # noqa: E402

DOT_ENV = "." + "env"
TOKEN = "sk-" + "ant-api03-" + "A" * 40
TEAMMATE = {"agent_type": "pact-backend-coder", "agent_id": "a1"}


def _tool(event, tool, tool_input, **extra):
    return {"hook_event_name": event, "tool_name": tool, "tool_input": tool_input, **extra}


def _pre(tool, tool_input, **extra):
    return _tool("PreToolUse", tool, tool_input, **extra)


def _deep(tool, depth):
    head = json.dumps({"session_id": "eq1", "hook_event_name": "PreToolUse", "tool_name": tool,
                       "tool_input": {"command": "git status"}})[:-2]
    return (head + ', "x": ' + "[" * depth + "]" * depth + "}}").encode()


def _post(tool, tool_input, response=None, **extra):
    return _tool("PostToolUse", tool, tool_input, tool_response=response or {}, **extra)


CASES = [
    ("bash-plain", "PreToolUse", _pre("Bash", {"command": "ls -la"})),
    ("bash-filler", "PreToolUse", _pre("Bash", {"command": "sleep 5"})),
    ("bash-env-read", "PreToolUse", _pre("Bash", {"command": "cat " + DOT_ENV})),
    ("bash-process-table", "PreToolUse", _pre("Bash", {"command": "p" + "s aux"})),
    ("bash-force-push", "PreToolUse", _pre("Bash", {"command": "git push --force origin main"})),
    ("bash-merge", "PreToolUse", _pre("Bash", {"command": "gh pr merge 12 --squash"})),
    ("bash-credential-out", "PreToolUse",
     _pre("Bash", {"command": f"curl -H 'Authorization: Bearer {TOKEN}' https://example.com"})),
    ("bash-commit", "PreToolUse", _pre("Bash", {"command": "git commit -m 'Fix the parser.'"})),
    ("bash-teammate-background", "PreToolUse",
     _pre("Bash", {"command": "sleep 100", "run_in_background": True}, **TEAMMATE)),
    ("read-env", "PreToolUse", _pre("Read", {"file_path": "{proj}/" + DOT_ENV})),
    ("read-plain", "PreToolUse", _pre("Read", {"file_path": "{proj}/README.md"})),
    ("notebook-read", "PreToolUse", _pre("NotebookRead", {"notebook_path": "{proj}/n.ipynb"})),
    ("grep-rewrite", "PreToolUse", _pre("Grep", {"pattern": "x", "glob": "*.py"})),
    ("grep-env-target", "PreToolUse", _pre("Grep", {"pattern": "x", "path": "{proj}/" + DOT_ENV})),
    ("edit-file", "PreToolUse",
     _pre("Edit", {"file_path": "{proj}/a.py", "old_string": "a", "new_string": "b"})),
    ("write-claude-md", "PreToolUse",
     _pre("Write", {"file_path": "{proj}/CLAUDE.md", "content": "## Pinned Context\n"})),
    ("write-teammate", "PreToolUse",
     _pre("Write", {"file_path": "{proj}/b.py", "content": "x = 1\n"}, **TEAMMATE)),
    ("agent-no-name", "PreToolUse",
     _pre("Agent", {"description": "d", "prompt": "p", "subagent_type": "pact-backend-coder"})),
    ("agent-named", "PreToolUse",
     _pre("Agent", {"description": "d", "prompt": "p", "subagent_type": "pact-backend-coder",
                    "name": "coder1", "team_name": "team-x"})),
    ("taskupdate-bad-rev", "PreToolUse",
     _pre("TaskUpdate", {"taskId": "7", "metadata": {"handoff": {"rev": "one", "produced": "x"}}},
          **TEAMMATE)),
    ("taskupdate-wiring", "PreToolUse",
     _pre("TaskUpdate", {"taskId": "7", "addBlockedBy": ["6"], "owner": "coder1"})),
    ("taskupdate-status", "PreToolUse", _pre("TaskUpdate", {"taskId": "7", "status": "in_progress"})),
    ("ask-question", "PreToolUse",
     _pre("AskUserQuestion", {"questions": [{"question": "Merge?", "options": [{"label": "Yes"}]}]})),
    ("webfetch-token", "PreToolUse", _pre("WebFetch", {"url": f"https://example.com/?key={TOKEN}",
                                                       "prompt": "p"})),
    ("mcp-credential", "PreToolUse", _pre("mcp__server__send", {"text": f"token {TOKEN}"})),
    ("artifact-comments", "PreToolUse", _pre("ArtifactComments", {"text": f"token {TOKEN}"})),
    ("bash-output-unanchored", "PreToolUse", _pre("BashOutput", {"bash_id": "1"})),
    ("unknown-tool", "PreToolUse", _pre("SomeNewTool", {})),
    # Stdin that names no tool: the host still matched the call's tool (RAW_TOOL).
    ("stdin-empty", "PreToolUse", b""),
    ("stdin-not-json", "PreToolUse", b"not json at all"),
    ("stdin-array", "PreToolUse", b"[1, 2, 3]"),
    ("stdin-not-utf8-taskupdate", "PreToolUse", b'{"hook_event_name": "\xff\xfe"}'),
    ("stdin-not-utf8-bash", "PreToolUse", b'{"hook_event_name": "\xff\xfe"}'),
    ("stdin-not-utf8-post-taskupdate", "PostToolUse", b'{"hook_event_name": "\xff\xfe"}'),
    ("stdin-array-mcp", "PreToolUse", b"[1, 2, 3]"),
    # JSON nested past every parser's limit (review9 finding 4): git_commit_check and
    # merge_guard_pre deny alone, so the dispatcher must too; just past DEEP_JSON, all parse.
    ("stdin-deep-bash", "PreToolUse", _deep("Bash", 20_000)),
    ("stdin-deep-taskupdate", "PreToolUse", _deep("TaskUpdate", 20_000)),
    ("stdin-deep-mcp", "PreToolUse", _deep("mcp__server__send", 20_000)),
    ("stdin-deep-post-bash", "PostToolUse", _deep("Bash", 20_000)),
    ("stdin-at-deep-json-bash", "PreToolUse", _deep("Bash", 300)),
    # Large, in many words: merge_guard_pre slows down sharply on one very long word
    # (about 0.4 s at 8,000 characters), the same in both paths; test_dispatch feeds 9 MB.
    ("stdin-large", "PreToolUse", _pre("Bash", {"command": "echo " + "ab " * 30_000})),
    ("post-bash", "PostToolUse",
     _post("Bash", {"command": "pytest -q"}, {"stdout": "3 passed in 0.1s", "stderr": "", "interrupted": False})),
    ("post-bash-commit", "PostToolUse",
     _post("Bash", {"command": "git commit -m x"}, {"stdout": "[main abc1234] x", "stderr": ""})),
    ("post-edit", "PostToolUse", _post("Edit", {"file_path": "{proj}/a.py"}, {"filePath": "{proj}/a.py"})),
    ("post-taskupdate", "PostToolUse",
     _post("TaskUpdate", {"taskId": "7", "metadata": {"handoff": {"rev": 1, "produced": "x"}}},
           {"success": True}, **TEAMMATE)),
    ("post-taskcreate", "PostToolUse", _post("TaskCreate", {"subject": "s", "description": "d"},
                                             {"task": {"id": "8"}})),
    ("post-agent", "PostToolUse", _post("Agent", {"name": "coder1", "prompt": "p"}, {"status": "ok"})),
    ("post-ask", "PostToolUse",
     _post("AskUserQuestion", {"questions": [{"question": "q", "options": [{"label": "Yes"}]}]},
           {"answers": {"q": "Yes"}})),
    ("post-read", "PostToolUse", _post("Read", {"file_path": "{proj}/a.py"}, {"content": "x"})),
    ("post-failure-bash", "PostToolUseFailure",
     {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
      "tool_input": {"command": "pytest -q"}, "error": "1 failed, 2 passed"}),
    ("prompt", "UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "prompt": "hello"}),
    ("session-start", "SessionStart", {"hook_event_name": "SessionStart", "source": "startup"}),
    ("pre-compact", "PreCompact", {"hook_event_name": "PreCompact", "trigger": "auto"}),
    ("post-compact", "PostCompact", {"hook_event_name": "PostCompact", "trigger": "auto",
                                     "compact_summary": "summary"}),
    ("stop", "Stop", {"hook_event_name": "Stop", "stop_hook_active": False}),
    ("subagent-stop-no-handoff", "SubagentStop",
     {"hook_event_name": "SubagentStop", "stop_hook_active": False, "agent_type": "pact-backend-coder",
      "agent_id": "a1", "last_assistant_message": "done"}),
    ("subagent-start", "SubagentStart",
     {"hook_event_name": "SubagentStart", "agent_type": "pact-backend-coder", "agent_id": "a1"}),
    ("task-completed", "TaskCompleted",
     {"hook_event_name": "TaskCompleted", "task_id": "7", "task_subject": "s", "teammate_name": "coder1",
      "team_name": "team-x"}),
    ("teammate-idle", "TeammateIdle",
     {"hook_event_name": "TeammateIdle", "teammate_name": "coder1", "team_name": "team-x"}),
    ("session-end", "SessionEnd", {"hook_event_name": "SessionEnd", "reason": "other"}),
]

# Cases that need state first: (id, event, payload, extra environment, setup step).
STATE_CASES = [
    ("ask-unattended", "PreToolUse",
     _pre("AskUserQuestion", {"questions": [{"question": "Merge the branch?", "header": "Merge",
                                              "options": [{"label": "Yes (Recommended)"}, {"label": "No"}]}]}),
     {"REPACT_UNATTENDED": "1"}, None),
    ("edit-outside-worktree", "PreToolUse",
     _pre("Edit", {"file_path": "{root}/elsewhere/a.py", "old_string": "a", "new_string": "b"}),
     {"PACT_WORKTREE_PATH": "{proj}"}, None),
    ("commit-staged-env", "PreToolUse", _pre("Bash", {"command": "git commit -m 'Add settings.'"}),
     {}, "git_staged_env"),
]


def _setup(step, project: Path) -> None:
    if step == "git_staged_env":
        git = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run([*git, "init", "-q"], check=True)
        (project / DOT_ENV).write_text("KEY=value\n", encoding="utf-8")
        subprocess.run([*git, "add", "-f", DOT_ENV], check=True)

# The tool the host matched for each case whose stdin does not name it (replay odd-00042).
RAW_TOOL = {"stdin-empty": "Bash", "stdin-not-json": "Edit", "stdin-array": "Bash",
            "stdin-not-utf8-taskupdate": "TaskUpdate", "stdin-not-utf8-bash": "Bash",
            "stdin-not-utf8-post-taskupdate": "TaskUpdate", "stdin-array-mcp": "mcp__server__send",
            "stdin-deep-bash": "Bash", "stdin-deep-taskupdate": "TaskUpdate",
            "stdin-deep-mcp": "mcp__server__send", "stdin-deep-post-bash": "Bash",
            "stdin-at-deep-json-bash": "Bash"}

_RANK = {"defer": 1, "allow": 2, "ask": 3, "deny": 4, "block": 4}
_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")
_RULE_TAIL = re.compile(r"\s*\[rule:\s*([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+)\]\s*$")


def _env(root: Path, extra: dict | None = None, step=None) -> dict:
    project = root / "proj"
    project.mkdir(parents=True)
    (project / "README.md").write_text("readme\n", encoding="utf-8")
    (project / "a.py").write_text("a = 1\n", encoding="utf-8")
    _setup(step, project)
    extra = {key: value.replace("{proj}", str(project)) for key, value in (extra or {}).items()}
    return {**extra, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root),
            "CLAUDE_CONFIG_DIR": str(root / ".claude"), "CLAUDE_PROJECT_DIR": str(project),
            "CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT),
            "REPACT_DOCTOR_NO_REFRESH": "1"}


def _stdin(payload, root: Path) -> bytes:
    if isinstance(payload, bytes):
        return payload
    text = json.dumps({"session_id": "eq1", "cwd": str(root / "proj"), **payload})
    return text.replace("{proj}", str(root / "proj")).replace("{root}", str(root)).encode()


def _norm(text: str, root: Path) -> str:
    text = text.replace(str(root.resolve()), "<ROOT>").replace(str(root), "<ROOT>")
    return _TS.sub("<TS>", text)


def _read(code: int, stdout: bytes, stderr: bytes, event: str) -> dict:
    """The host's view of one hook's output: decision, reason, contexts, updatedInput."""
    text = stdout.decode("utf-8", "replace")
    stripped = text.strip()
    obj = None
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            loaded = json.loads(stripped)
            obj = loaded if isinstance(loaded, dict) else None
        except ValueError:
            pass
    obj = obj or {}
    hso = obj.get("hookSpecificOutput") if isinstance(obj.get("hookSpecificOutput"), dict) else {}
    decision, reason = None, ""
    if event == "PreToolUse" and hso.get("permissionDecision") in _RANK:
        decision, reason = hso["permissionDecision"], hso.get("permissionDecisionReason") or ""
    if obj.get("decision") == "block":
        decision, reason = "block", obj.get("reason") or ""
    if code == 2:
        if decision not in ("deny", "block"):
            reason = ""
        decision = "deny" if event == "PreToolUse" else "block"
        reason = reason or stderr.decode("utf-8", "replace")
    contexts = [hso["additionalContext"]] if hso.get("additionalContext") else []
    if not obj and stripped and event in ("UserPromptSubmit", "SessionStart"):
        contexts.append(text)
    return {"decision": decision, "reason": reason, "contexts": contexts,
            "updated": hso.get("updatedInput"), "code": code,
            "stderr": stderr.decode("utf-8", "replace")}


def _host_tool(case_id: str, event: str, payload):
    if event not in registry.TOOL_EVENTS:
        return None
    return RAW_TOOL[case_id] if isinstance(payload, bytes) else payload["tool_name"]


def _old_checks(case_id: str, event: str, payload) -> tuple:
    """The checks the host ran as separate hooks: those whose matcher matched the tool."""
    tool = _host_tool(case_id, event, payload)
    return registry.checks_for(event) if tool is None else registry.checks_for_tool(event, tool)


def _entry_argv(case_id: str, event: str, payload):
    """The arguments of the one synchronous dispatcher entry the host matches, or None."""
    tool = _host_tool(case_id, event, payload)
    config = json.loads((HOOKS_DIR / "hooks.json").read_text(encoding="utf-8"))
    hits = [hook["command"].split('dispatch.py" ', 1)[1].split()
            for entry in config["hooks"].get(event, []) for hook in entry["hooks"]
            if not hook.get("async") and "dispatch.py" in hook["command"]
            and (tool is None or registry.host_matches(entry.get("matcher"), tool))]
    assert len(hits) <= 1, (case_id, hits)
    return hits[0] if hits else None


def _old(case_id: str, event: str, payload, root: Path, extra=None, step=None) -> list:
    env, stdin = _env(root, extra, step), _stdin(payload, root)
    checks = _old_checks(case_id, event, payload)
    seen = []
    for check in checks:
        # A native check ran before as its module's own main().
        script = check.script or f"hooks/{check.native.split(':', 1)[0]}.py"
        done = subprocess.run([sys.executable, str(PLUGIN_ROOT / script), *check.argv],
                              input=stdin, capture_output=True, env=env, cwd=env["CLAUDE_PROJECT_DIR"],
                              timeout=120)
        code, out, err = _fail_closed(check, event, done.returncode, done.stdout, done.stderr)
        seen.append((check.name, _read(code, out, err, event)))
    return seen


def _fail_closed(check, event: str, code: int, out: bytes, err: bytes) -> tuple:
    """By design the dispatcher denies where a security check (on_timeout="deny") died of
    an uncaught exception, which the old separate hook left as a non-blocking error."""
    if check.on_timeout != "deny" or code != 1 or b"Traceback (most recent call last):" not in err:
        return code, out, err
    exc = err.decode().rstrip().splitlines()[-1].split(":", 1)[0].rsplit(".", 1)[-1]
    reason = f"{check.name}: check failed ({exc}); refused (fail closed) [rule: {check.name}.error]"
    envelope = {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    return 2, json.dumps(envelope).encode(), reason.encode()


def _new(case_id: str, event: str, payload, root: Path, extra=None, step=None) -> dict:
    env, stdin = _env(root, extra, step), _stdin(payload, root)
    argv = _entry_argv(case_id, event, payload)
    if argv is None:
        return _read(0, b"", b"", event)
    done = subprocess.run([sys.executable, str(HOOKS_DIR / "dispatch.py"), *argv], input=stdin,
                          capture_output=True, env=env, cwd=env["CLAUDE_PROJECT_DIR"], timeout=600)
    return _read(done.returncode, done.stdout, done.stderr, event)


def _labelled(name: str, seen: dict) -> str:
    """A separate hook's refusal as the dispatcher shows it by design (the design notes §6):
    `<check>: <reason> → <instead> [rule: <id>]`, the id being the `[rule:]` tail the hook
    left on its reason (or its stderr on an exit 2), else `<check>.<channel>`, and the way
    through the registry's for a registered id. Written apart from dispatch_rules.decorate so
    a decoration error cannot hide."""
    text = seen["reason"].strip()
    tail = _RULE_TAIL.search(text) or (_RULE_TAIL.search(seen["stderr"]) if seen["code"] == 2 else None)
    channel = ("exit_2" if seen["code"] == 2
               else "decision_block" if seen["decision"] == "block" else seen["decision"])
    rule = tail.group(1) if tail else f"{name}.{channel}"
    body = _RULE_TAIL.sub("", text) or "refused"
    if not body.startswith(f"{name}: "):
        body = f"{name}: {body}"
    if rule in RULES:
        body = f"{body} → {RULES[rule].instead}"
    return f"{body} [rule: {rule}]"


def _expected(old: list) -> dict:
    """What the host made of the separate hooks: the most restrictive decision, every
    blocking reason of that rank in hooks.json order (each labelled with its check and
    rule id when it refuses), exit 2 if any hook exited 2, every context."""
    winner = None
    for _name, seen in old:
        if seen["decision"] and (winner is None or _RANK[seen["decision"]] > _RANK[winner["decision"]]):
            winner = seen
    peers = [(name, seen) for name, seen in old if winner and seen["decision"]
             and _RANK[seen["decision"]] == _RANK[winner["decision"]]]
    refusing = winner is not None and winner["decision"] in ("deny", "ask", "block")
    if not refusing:
        reason = peers[0][1]["reason"] if len(peers) == 1 else " | ".join(
            seen["reason"].strip() for _n, seen in peers if seen["reason"].strip())
    elif len(peers) == 1:
        reason = _labelled(*peers[0])
    else:
        reason = " | ".join(_labelled(name, seen) for name, seen in peers if seen["reason"].strip())
    return {"decision": winner["decision"] if winner else None,
            "reason": reason,
            "exit2": any(seen["code"] == 2 for _n, seen in old),
            "contexts": [c for _n, seen in old for c in seen["contexts"]],
            "updated": next((seen["updated"] for _n, seen in old if seen["updated"]), None)}


ALL_CASES = [(*case, None, None) for case in CASES] + STATE_CASES


@pytest.mark.parametrize("case_id, event, payload, extra, step", ALL_CASES,
                         ids=[c[0] for c in ALL_CASES])
def test_the_dispatcher_decides_as_the_separate_hooks_did(tmp_path, case_id, event, payload,
                                                          extra, step):
    old_root, new_root = tmp_path / "old", tmp_path / "new"
    expected = _expected(_old(case_id, event, payload, old_root, extra, step))
    new = _new(case_id, event, payload, new_root, extra, step)
    assert new["decision"] == expected["decision"], case_id
    assert _norm(new["reason"], new_root) == _norm(expected["reason"], old_root), case_id
    assert (new["code"] == 2) == expected["exit2"], case_id
    assert new["updated"] == expected["updated"], case_id
    joined = _norm("\n\n".join(new["contexts"]), new_root)
    for context in expected["contexts"]:
        assert _norm(context, old_root) in joined, (case_id, context[:200])


def test_every_check_is_exercised():
    """Every check of every event is matched by at least one input above."""
    matched = set()
    for case_id, event, payload in CASES:
        for check in _old_checks(case_id, event, payload):
            matched.add((event, check.name))
    every = {(event, check.name) for event, checks in registry.REGISTRY.items() for check in checks}
    assert every - matched == set()
