"""tests/test_dispatch_timeout.py: a timed-out security check is a deny.

The dispatcher used to discard every timed-out check (an allow). With `on_timeout="deny"`
the timeout is the refusal: exit 2, a reason that names the check and ends with
`[rule: <check>.timeout]`, a check_failure row and a gate_refusal row. The hostile rows
cover a check that catches the timeout and prints an allow, and a check that masks SIGALRM.
A security check that raises (at import or mid-call, in process or run apart) is a deny with
`[rule: <check>.error]`; an advisory check that raises still decides nothing.
The registry contract pins the four security checks' alarms and the host entry margin.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time

import pytest

from test_dispatch import HOOKS_DIR, _rows, env  # noqa: F401  (fixture re-export)
import dispatch_registry as registry  # noqa: E402

DRIVER = textwrap.dedent(f"""
    import json, sys
    sys.path.insert(0, {str(HOOKS_DIR)!r})
    import dispatch, dispatch_registry as r
    spec = json.loads(sys.argv[2])
    checks = [r.Check(name=c["name"], script=c.get("script"), matcher=c.get("matcher"),
                      timeout_s=c.get("timeout_s", 600), on_timeout=c.get("on_timeout", "discard"))
              for c in spec]
    sys.exit(dispatch.dispatch(sys.argv[1], sys.stdin.buffer.read(), checks=checks))
""")

ALLOW = ("{'hookSpecificOutput': {'hookEventName': 'PreToolUse', "
         "'permissionDecision': 'allow'}}")
SCRIPTS = {
    "hangs": "import time\ntime.sleep(60)\n",
    # Hostile: catches the BaseException the alarm raises and answers with an allow.
    "swallows_allow": ("import json, sys, time\ntry:\n    time.sleep(30)\n"
                       f"except BaseException:\n    print(json.dumps({ALLOW}))\n    sys.exit(0)\n"),
    # Hostile: masks the alarm, so no exception can ever fire; only the wall clock sees it.
    "masks_alarm": ("import signal, time, json\nsignal.signal(signal.SIGALRM, signal.SIG_IGN)\n"
                    f"time.sleep(3)\nprint(json.dumps({ALLOW}))\n"),
    # A security check whose import fails (env_guard imports shell.env at module load).
    "import_fails": "import json\nimport shell_env_that_is_not_there\n",
    # A security check that raises mid-call, after reading its stdin.
    "call_raises": "import sys\nsys.stdin.read()\nraise RuntimeError('boom')\n",
    # A security check that dies by a signal, or exits oddly with no decision.
    "killed": "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n",
    "exits_3": "import sys\nsys.exit(3)\n",
    "allow_ctx": ("import json\nprint(json.dumps({'hookSpecificOutput': {'hookEventName': "
                  "'PreToolUse', 'additionalContext': 'ran'}}))\n"),
}
SECURITY = ("env_guard", "merge_guard_pre", "egress_guard", "git_commit_check", "worktree_guard",
            "write_checks", "task_write_guard", "ops_frame_guard")


@pytest.fixture
def run(tmp_path, env):
    scripts = tmp_path / "checks"
    scripts.mkdir()
    for name, body in SCRIPTS.items():
        (scripts / f"{name}.py").write_text(body, encoding="utf-8")

    def _run(names, stdin=b'{"tool_name": "Bash", "session_id": "s1"}', **per_check):
        spec = []
        for name in names:
            item = {"name": name, "script": str(scripts / f"{name}.py")}
            item.update(per_check.get(name, {}))
            spec.append(item)
        started = time.monotonic()
        done = subprocess.run([sys.executable, "-c", DRIVER, "PreToolUse", json.dumps(spec)],
                              input=stdin, capture_output=True, env=env,
                              cwd=env["CLAUDE_PROJECT_DIR"], timeout=60)
        done.elapsed = time.monotonic() - started
        return done
    return _run


def _refusal(done, name: str, kind: str = "timeout") -> dict:
    """Assert the deny shape and return the envelope's hookSpecificOutput."""
    assert done.returncode == 2, (done.stdout, done.stderr)
    reason = done.stderr.decode().strip()
    assert reason.startswith(f"{name}: ") and reason.endswith(f"[rule: {name}.{kind}]"), reason
    hso = json.loads(done.stdout)["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and hso["permissionDecisionReason"] == reason
    return hso


def _journal(tmp_path, name: str) -> tuple:
    rows = _rows(tmp_path)
    failures = [(r["check"], r["exc_class"]) for r in rows if r["type"] == "check_failure"]
    refusals = [(r["hook"], r["verdict"], r["rule"]) for r in rows if r["type"] == "gate_refusal"]
    return failures, refusals


def test_a_hung_security_check_is_a_deny_with_both_journal_rows(run, tmp_path):
    done = run(["hangs", "allow_ctx"], hangs={"timeout_s": 1, "on_timeout": "deny"})
    _refusal(done, "hangs")
    assert done.elapsed < 15
    assert _journal(tmp_path, "hangs") == ([("hangs", "CheckTimeout")],
                                           [("hangs", "deny", "hangs.timeout")])


def test_a_hung_check_with_discard_is_still_an_allow(run, tmp_path):
    done = run(["hangs", "allow_ctx"], hangs={"timeout_s": 1})
    assert done.returncode == 0
    assert json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] == "ran"
    assert _journal(tmp_path, "hangs") == ([("hangs", "CheckTimeout")], [])


def test_a_slow_write_checks_is_denied_not_allowed(run, tmp_path):
    """The evidence gate's timeout is no allow: write_checks keeps the
    registry's on_timeout, here on a check that hangs past its alarm."""
    real = {check.name: check for check in registry.checks_for("PreToolUse")}["write_checks"]
    done = run(["hangs"], stdin=b'{"tool_name": "TaskUpdate", "session_id": "s1"}',
               hangs={"timeout_s": 1, "on_timeout": real.on_timeout})
    _refusal(done, "hangs")


def test_a_check_that_swallows_its_timeout_and_allows_is_still_denied(run, tmp_path):
    done = run(["swallows_allow"], swallows_allow={"timeout_s": 1, "on_timeout": "deny"})
    hso = _refusal(done, "swallows_allow")
    assert hso["permissionDecision"] != "allow"
    assert _journal(tmp_path, "swallows_allow")[1] == [
        ("swallows_allow", "deny", "swallows_allow.timeout")]


def test_a_check_that_masks_sigalrm_is_denied_by_the_wall_clock(run, tmp_path):
    done = run(["masks_alarm"], masks_alarm={"timeout_s": 1, "on_timeout": "deny"})
    _refusal(done, "masks_alarm")
    assert _journal(tmp_path, "masks_alarm") == ([("masks_alarm", "CheckTimeout")],
                                                 [("masks_alarm", "deny", "masks_alarm.timeout")])


def test_the_run_apart_path_denies_too(run, tmp_path):
    deep = (b'{"tool_name": "Bash", "session_id": "s1", "tool_input": {"x": '
            + b"[" * 300 + b"]" * 300 + b"}}")
    done = run(["hangs"], stdin=deep, hangs={"timeout_s": 1, "on_timeout": "deny"})
    _refusal(done, "hangs")
    assert done.elapsed < 15


DEEP = (b'{"tool_name": "Bash", "session_id": "s1", "tool_input": {"x": '
        + b"[" * 300 + b"]" * 300 + b"}}")


@pytest.mark.parametrize("name, exc, deep", [
    ("import_fails", "ModuleNotFoundError", False),
    ("call_raises", "RuntimeError", False),
    ("import_fails", "ModuleNotFoundError", True),   # the run-apart route around the check
    ("call_raises", "RuntimeError", True),
])
def test_a_security_check_that_raises_is_a_deny(run, tmp_path, name, exc, deep):
    stdin = DEEP if deep else b'{"tool_name": "Bash", "session_id": "s1"}'
    done = run([name, "allow_ctx"], stdin=stdin, **{name: {"on_timeout": "deny"}})
    _refusal(done, name, "error")
    assert _journal(tmp_path, name) == ([(name, exc)], [(name, "deny", f"{name}.error")])


def test_an_advisory_check_that_raises_still_decides_nothing(run, tmp_path):
    done = run(["import_fails", "allow_ctx"])
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"] == "ran"
    assert _journal(tmp_path, "import_fails") == ([("import_fails", "ModuleNotFoundError")], [])


class TestRegistryContract:
    def test_the_security_checks_deny_on_timeout_under_the_cap(self):
        by_name = {check.name: check for check in registry.checks_for("PreToolUse")}
        for name in SECURITY:
            check = by_name[name]
            assert check.on_timeout == "deny", name
            assert check.timeout_s == registry.SECURITY_TIMEOUTS_S[name], name
            assert check.timeout_s <= registry.SECURITY_TIMEOUT_CAP_S, name
        others = [check for event in registry.REGISTRY for check in registry.checks_for(event)
                  if check.name not in SECURITY]
        assert all(check.on_timeout == "discard" for check in others)

    def test_the_host_entry_timeout_exceeds_the_sum_of_its_member_alarms(self):
        for event in registry.REGISTRY:
            for matcher, group, timeout in registry.dispatch_entries(event):
                if event not in registry.TOOL_EVENTS:
                    members = registry.checks_for(event)
                elif group:
                    members = registry.group_checks(event, group)
                else:
                    members = [c for c in registry.checks_for(event) if c.matcher is None
                               or not registry._EXACT_MATCHER.fullmatch(c.matcher)]
                assert timeout > sum(c.timeout_s for c in members), (event, matcher)

    def test_an_advisory_check_waits_60s_so_the_bash_entry_is_585s(self):
        assert registry.DEFAULT_TIMEOUT_S == 60
        assert registry.entry_timeout("PreToolUse") == 585   # 5 x 60 + 255 security + 30 margin


@pytest.mark.parametrize("name, failure", [("killed", "signal 9"), ("exits_3", "exit 3")])
def test_a_run_apart_security_check_that_dies_without_deciding_is_a_deny(run, tmp_path, name,
                                                                         failure):
    done = run([name, "allow_ctx"], stdin=DEEP, **{name: {"on_timeout": "deny"}})
    _refusal(done, name, "error")
    assert _journal(tmp_path, name) == ([(name, failure)], [(name, "deny", f"{name}.error")])
    advisory = run([name, "allow_ctx"], stdin=DEEP)       # advisory: unchanged, no deny
    assert advisory.returncode != 2, advisory.stderr
