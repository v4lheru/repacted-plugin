#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/dispatch.py
Summary: One process per hook event. Reads stdin once, runs the event's checks from
    dispatch_registry.REGISTRY in-process, in their hooks.json order, and prints one
    combined result the way the host combines separate hooks.
Used by: pact-plugin/hooks/hooks.json, synchronous entries that the host matches at most
    one of per call:
    python3 "${CLAUDE_PLUGIN_ROOT}/hooks/dispatch.py" <event> [<group>]
    A non-tool event has one entry. A tool event has one entry per tool group
    (dispatch_registry.dispatch_entries): an exact matcher and <group> naming its tools
    ("Edit,Write"), plus one entry, with no <group>, for every tool no group names.

Each script check runs as `python3 <script>` ran it: as __main__, with its own stdin
copy, argv, sys.path[0], and fds 1 and 2 on its own capture files (a child process it
starts writes there too). After it, argv,
sys.path, os.environ, the cwd, the umask, the std streams and every plugin module it
imported are restored or dropped, so the next check starts as a fresh process did.
The interpreter and the standard library are shared.

A check is matched as the host matched its hooks.json entry: a matcher of letters,
digits, `_`, `-`, spaces, `,` and `|` is a list of exact names; any other matcher is an
unanchored regular expression; none matches everything. With <group>, the host already
matched the tool, so the group selects the checks whatever stdin holds: on stdin that is
not JSON, or names no tool, exactly the checks that ran for that tool before run, and each
handles the bad input as it did. Without <group>, stdin's tool_name selects; when stdin
gives none, the checks that run for every tool run and the security checks whose matcher
is an expression.

A `teams_only` check runs only in the `teams` execution mode; an unreadable
mode runs every check.

Combination (the host runs separate hooks in parallel and documents none of the order):
  - Every matched check runs, also after one refused: they all ran before.
  - A check's stdout is JSON when, stripped, it starts with { and ends with }; else it is
    plain text, which reaches the model only on UserPromptSubmit and SessionStart.
  - Exit 2 is a block whatever stdout says. Another non-zero exit is a non-blocking
    error; a JSON object on its stdout still counts. A timed-out check is discarded, or a
    deny when the registry says `on_timeout="deny"` (the security checks): exit 2,
    the reason names the check and ends `[rule: <check>.timeout]`. A wall clock backs the
    alarm, so a check that masks SIGALRM is denied once it returns. One that raises is a
    deny too, `[rule: <check>.error]`.
  - Only one check with an effect: its stdout, stderr and exit code pass through
    byte for byte. That is the common case, and it makes the result identical. The one
    change: a non-blocking error's stderr starts with "<check>: ", since the host's
    hook-error line names the command it ran, which is dispatch.py.
  - Several: PreToolUse decisions rank deny (and exit 2) > ask > allow > defer. The winning
    rank decides; the reasons of every check at that rank, in hooks.json order, are joined
    by " | " (the host shows every blocking hook). The dispatcher exits 2 if any check
    did; its stderr is that check's stderr when it is the only refusal, else the joined
    texts (stderr of an exit 2, the reason of a JSON refusal). Every additionalContext is kept, in order, joined by a newline; plain
    stdout joins them on the two events where it is context. systemMessage values are
    joined by a newline; the first updatedInput, the first continue:false with its
    stopReason and the first value of any other key are kept. A top-level
    decision:block (Stop, SubagentStop, PostToolUse, UserPromptSubmit) is the first one.
    A check's non-blocking error (another non-zero exit with no JSON object) becomes a
    systemMessage line "<check> hook error: Failed with non-blocking status code: <first
    stderr line>", the notice the host showed for it alone, when the combined exit is 0.
    On a combined exit 2 the host reads only stderr, so neither that notice nor another
    check's additionalContext is carried (the host's handling of it is undocumented).

Journal rows (the dispatcher writes them after printing its result, best effort):
  - check_failure: one per check that raised, timed out or could not load:
    check, event, exc_class. Never a message, argv or payload.
  - gate_refusal: one per check that refused (deny, ask, block) unless the check writes
    its own: hook, event, tool_name, verdict, rule, channel, code, target, frame,
    plugin_version. rule is the check's stable dotted id: a native Result.rule, or the
    `[rule: <id>]` tail a script leaves on its reason or stderr, else `<check>.<channel>`;
    channel is how it refused (exit_2, deny, ask, decision_block). The refusal line the
    host shows is normalised to `<check>: <reason> [rule: <id>]` (shared/rules.py explains
    an id; `repact inspect --refusals` reads the rows). code and target are the first 12 hex
    digits of a sha256 over the reason text and
    over the command, file path or task id. No text, command line or payload.
  A row goes to the session journal; when the shared package cannot load or the session
  cannot be named, the row (check_failure or gate_refusal) goes to
  <config>/pact-sessions/_dispatch_failures.jsonl instead (standard library only).

Then, best effort, two session-dir files, never journal rows (hooks read the journal
whole): context-cost.json, the bytes put before the model (shared/context_ledger.py, which
writes the advisory _advised() adds), and hook-latency.ndjson, the run's time from STARTED
and each check's (shared/hook_latency.py).

Frame memo: before its check loop, dispatch() puts an empty module FRAME_MEMO in
sys.modules (no __file__, so _Isolation keeps it) and removes it after. shared/frame.py
stores each frame's resolved identity there, so the checks of one run resolve who is
writing once (shared/frame.py says what a frame holds).

Deep stdin: JSON nested DEEP_JSON (256) or more levels runs each check in its own
`python3 <script>` process (run_apart), so a parse that hits the recursion limit fails in
each check exactly as it did alone (git_commit_check and merge_guard_pre deny), never at a
depth shifted by this process's own stack. No real frame nests that deep.

Fail direction: a check fails as it failed alone; a security check fails closed. The dispatcher's own error after the
checks ran prints the first refusing check's output verbatim, or nothing.
"""

from __future__ import annotations

import time

STARTED = time.monotonic()  # a run's latency counts from here

import hashlib
import importlib
import io
import json
import os
import re
import runpy
import signal
import subprocess
import sys
import tempfile
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HOOKS_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = HOOKS_DIR.parent
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

import dispatch_registry as registry  # noqa: E402
import dispatch_rules as rules  # noqa: E402

# The frame memo (shared/frame.py): a module with no __file__, so _Isolation keeps it
# across checks. Created per dispatch, removed after; this file never imports shared.
FRAME_MEMO = "pact_frame_memo"

_RANK = {"defer": 1, "allow": 2, "ask": 3, "deny": 4, "block": 4}
REASON_SEPARATOR = " | "
_BLOCK_DECISION_EVENTS = frozenset({"Stop", "SubagentStop", "PostToolUse", "UserPromptSubmit"})
_PLAIN_CONTEXT_EVENTS = frozenset({"UserPromptSubmit", "SessionStart"})
_LEAD_AGENT_TYPES = frozenset({"repacted:pact-orchestrator", "pact-orchestrator"})
_HANDLED_HSO = frozenset({"hookEventName", "permissionDecision", "permissionDecisionReason",
                          "additionalContext", "updatedInput"})
_HANDLED_TOP = frozenset({"hookSpecificOutput", "decision", "reason", "systemMessage",
                          "continue", "stopReason", "suppressOutput"})
# stdin nested this deep runs each check in its own process (run_apart). Far below any
# parser's limit (about 990 on Python 3.9, about 10,000 on 3.12); no real frame nests so deep.
DEEP_JSON = 256
_JSON_STRING = re.compile(rb'"(?:[^"\\]|\\.)*"', re.DOTALL)
_BRACKETS = re.compile(rb"[\[\]{}]")


class CheckTimeout(BaseException):
    """Raised inside a check that ran past its timeout. A BaseException, so the check's
    own `except Exception` cannot turn it into a decision."""


@dataclass
class Outcome:
    """What one check produced, as the host would have seen it from a separate process."""

    check: registry.Check
    exit_code: int = 0
    stdout: bytes = b""
    stderr: bytes = b""
    failure: str = ""
    discarded: bool = False
    rule: str = ""
    journaled: bool = False
    elapsed_s: float = 0.0


@dataclass
class Parsed:
    """An outcome read the way the host reads a hook's output."""

    outcome: Outcome
    obj: Optional[dict] = None
    plain: Optional[str] = None
    decision: Optional[str] = None
    reason: str = ""
    effect: bool = False
    contexts: list = field(default_factory=list)
    rule: str = ""      # the stable id the check named (Result.rule or a `[rule:]` tail)
    channel: str = ""   # how it refused: exit_2, deny, ask or decision_block


# --- matching ------------------------------------------------------------------------

def matches(matcher: Optional[str], tool_name) -> bool:
    """True iff the host would run an entry with this matcher for this tool name. A tool
    name that stdin does not give matches only a check that runs for every tool."""
    if not isinstance(tool_name, str) or not tool_name:
        return matcher in (None, "", "*")
    return registry.host_matches(matcher, tool_name)


def execution_mode(payload: dict) -> str:
    """shared/execution.for_payload in an _Isolation; a failure reads as "teams" (every check)."""
    try:
        with _Isolation():
            from shared import execution
            return execution.for_payload(payload)
    except Exception:  # noqa: BLE001 - an unreadable mode keeps every check
        return "teams"


def select(event: str, checks, payload: dict, group: Optional[str] = None) -> list:
    """The checks to run. With a group argument the host already matched the tool, so the
    group decides, whatever stdin holds; without one, stdin's tool_name does."""
    if event not in registry.TOOL_EVENTS:
        return list(checks)
    if group:
        tool_name = group.split(",", 1)[0]
        return [check for check in checks if registry.host_matches(check.matcher, tool_name)]
    tool_name = payload.get("tool_name")
    named = isinstance(tool_name, str) and bool(tool_name)
    # A security check the host may have matched fails closed.
    return [check for check in checks if matches(check.matcher, tool_name)
            or (not named and check.on_timeout == "deny"
                and not registry._EXACT_MATCHER.fullmatch(check.matcher or ""))]


# --- running one check ---------------------------------------------------------------

# Set by the alarm of the running check. A check can catch CheckTimeout (a fail-closed
# import gauntlet catches BaseException and denies), so the alarm repeats every second until
# the check returns, and a check whose alarm fired is discarded whatever it then did.
_timed_out = [False]


def _on_alarm(_signum, _frame):
    _timed_out[0] = True
    raise CheckTimeout()


def _exit_status(code) -> tuple[int, bytes]:
    """The exit status and stderr text the interpreter gives `sys.exit(code)`."""
    if code is None:
        return 0, b""
    if isinstance(code, int):
        return code & 0xFF, b""
    return 1, (str(code) + "\n").encode("utf-8", "backslashreplace")


def _flush_streams(*streams) -> None:
    for stream in streams:
        try:
            if stream is not None:
                stream.flush()
        except Exception:  # noqa: BLE001 - a closed or broken stream has nothing to flush
            pass


def _is_plugin_module(module) -> bool:
    path = getattr(module, "__file__", None)
    if not isinstance(path, str):
        return False
    try:
        return Path(path).resolve().is_relative_to(PLUGIN_ROOT)
    except (OSError, ValueError):
        return False


class _Isolation:
    """Restores the process state a check may change, and drops the plugin modules it
    imported, so the next check sees what a fresh process saw."""

    def __enter__(self):
        self.argv = list(sys.argv)
        self.path = list(sys.path)
        self.environ = dict(os.environ)
        try:
            self.cwd = os.getcwd()
        except OSError:
            self.cwd = None
        self.umask = os.umask(0o022)
        os.umask(self.umask)
        self.streams = (sys.stdin, sys.stdout, sys.stderr)
        self.modules = set(sys.modules)
        return self

    def __exit__(self, *_exc):
        _flush_streams(sys.stdout, sys.stderr, *self.streams[1:])
        sys.stdin, sys.stdout, sys.stderr = self.streams
        sys.argv = self.argv
        sys.path[:] = self.path
        if dict(os.environ) != self.environ:
            os.environ.clear()
            os.environ.update(self.environ)
        if self.cwd is not None:
            try:
                os.chdir(self.cwd)
            except OSError:
                pass
        os.umask(self.umask)
        for name in set(sys.modules) - self.modules:
            if _is_plugin_module(sys.modules.get(name)):
                sys.modules.pop(name, None)
        return False


def _stdin_for(raw: bytes, template) -> io.TextIOWrapper:
    encoding = getattr(template, "encoding", None) or "utf-8"
    errors = getattr(template, "errors", None) or "strict"
    return io.TextIOWrapper(io.BytesIO(raw), encoding=encoding, errors=errors)


def _script_body(check: registry.Check) -> Callable[[], None]:
    path = PLUGIN_ROOT / check.script

    def body() -> None:
        sys.argv = [str(path), *check.argv]
        sys.path.insert(0, str(path.parent))
        runpy.run_path(str(path), run_name="__main__")

    return body


def _native_body(check: registry.Check, raw: bytes, event: str, tool_name: str,
                 holder: list) -> Callable[[], None]:
    def body() -> None:
        module_name, _, func_name = (check.native or "").partition(":")
        func = getattr(importlib.import_module(module_name), func_name)
        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except (ValueError, RecursionError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        ctx = registry.CheckContext(event=event, tool_name=tool_name,
                                    raw=raw.decode("utf-8", "replace"), payload=payload,
                                    journal=lambda event_type, **fields: _journal_one(
                                        payload, event_type, fields))
        holder.append(func(payload, ctx))

    return body


def run_check(check: registry.Check, raw: bytes, event: str, tool_name: str = "") -> Outcome:
    """Run one check with its own stdin copy, captured fds 1 and 2, and a timeout."""
    outcome = Outcome(check=check)
    holder: list = []
    if check.script is not None:
        body = _script_body(check)
    else:
        body = _native_body(check, raw, event, tool_name, holder)
    out_file, err_file = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    _flush_streams(sys.stdout, sys.stderr)
    saved_out, saved_err = os.dup(1), os.dup(2)
    previous = signal.getsignal(signal.SIGALRM)
    exit_extra = b""
    _timed_out[0] = False
    started = time.monotonic()
    try:
        os.dup2(out_file.fileno(), 1)
        os.dup2(err_file.fileno(), 2)
        with _Isolation():
            sys.stdin = _stdin_for(raw, sys.stdin)
            try:
                signal.signal(signal.SIGALRM, _on_alarm)
                signal.setitimer(signal.ITIMER_REAL, max(check.timeout_s, 0.001), 1.0)
                try:
                    body()
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
            except SystemExit as stop:
                outcome.exit_code, exit_extra = _exit_status(stop.code)
            except CheckTimeout:
                outcome.failure, outcome.discarded = "CheckTimeout", True
            except BaseException as error:  # noqa: BLE001 - one check's error is not the event's
                outcome.failure, outcome.exit_code = type(error).__name__, 1
                try:
                    traceback.print_exception(type(error), error, error.__traceback__)
                except BaseException:  # noqa: BLE001
                    pass
    except CheckTimeout:
        outcome.failure, outcome.discarded = "CheckTimeout", True
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        elapsed = time.monotonic() - started
        signal.signal(signal.SIGALRM, previous)
        _flush_streams(sys.stdout, sys.stderr)
        os.dup2(saved_out, 1)
        os.dup2(saved_err, 2)
        os.close(saved_out)
        os.close(saved_err)
    out_file.seek(0)
    err_file.seek(0)
    outcome.stdout, outcome.stderr = out_file.read(), err_file.read() + exit_extra
    out_file.close()
    err_file.close()
    # The wall clock backs the alarm: a check that set SIGALRM to SIG_IGN, or never returned
    # to Python bytecode, raised nothing and is still past its timeout.
    if _timed_out[0] or elapsed > max(check.timeout_s, 0.001):
        outcome.failure, outcome.discarded = "CheckTimeout", True
    if outcome.discarded:
        outcome.stdout = outcome.stderr = b""
        outcome.exit_code = 0
        _deny_timed_out(outcome, event)
    elif check.script is None and not outcome.failure:
        _apply_native(outcome, holder[0] if holder else None, event)
    _deny_failed(outcome, event)
    return outcome


def timeout_rule(check: registry.Check) -> str:
    """The stable rule id of a security check's timeout deny."""
    return f"{check.name}.timeout"


def _deny_timed_out(outcome: Outcome, event: str) -> None:
    """A discarded (timed-out) outcome as the refusal the registry asks for; `failure`
    stays, so both the check_failure and the gate_refusal row are written."""
    check = outcome.check
    if not outcome.discarded or check.on_timeout != "deny":
        return
    _refuse(outcome, event, timeout_rule(check), f"check timed out after {check.timeout_s}s")


def _deny_failed(outcome: Outcome, event: str) -> None:
    """A security check that raised refuses; an advisory one decided nothing."""
    check = outcome.check
    if not outcome.failure or outcome.discarded or outcome.rule or check.on_timeout != "deny":
        return
    _refuse(outcome, event, f"{check.name}.error", f"check failed ({outcome.failure})")


def _refuse(outcome: Outcome, event: str, rule: str, why: str) -> None:
    reason = f"{outcome.check.name}: {why}; refused (fail closed) [rule: {rule}]"
    code, obj, stderr = render(registry.Result(decision="block", reason=reason), event)
    outcome.discarded = False
    outcome.rule = rule
    outcome.exit_code = code
    outcome.stdout = json.dumps(obj).encode("utf-8") if obj else b""
    outcome.stderr = (stderr or reason).encode("utf-8")


def nesting(raw: bytes) -> int:
    """The deepest [ or { nesting of `raw` outside JSON strings. Linear; the bracket loop
    runs only when the input holds at least DEEP_JSON brackets."""
    if raw.count(b"[") + raw.count(b"{") < DEEP_JSON:
        return 0
    depth = deepest = 0
    for bracket in _BRACKETS.findall(_JSON_STRING.sub(b"", raw)):
        depth += 1 if bracket in b"[{" else -1
        deepest = max(deepest, depth)
    return deepest


def run_apart(check: registry.Check, raw: bytes, event: str = "") -> Outcome:
    """Run one check in its own `python3 <script>` process, as the host ran it before: for
    stdin nested so deep that a parse inside this process's deeper stack could fail where
    the check's own process parsed it (or the reverse)."""
    outcome = Outcome(check=check)
    script = check.script or f"hooks/{(check.native or '').split(':', 1)[0]}.py"
    try:
        done = subprocess.run([sys.executable, str(PLUGIN_ROOT / script), *check.argv],
                              input=raw, capture_output=True, timeout=max(check.timeout_s, 0.001))
    except subprocess.TimeoutExpired:
        outcome.failure, outcome.discarded = "CheckTimeout", True
        _deny_timed_out(outcome, event)
        return outcome
    except Exception as error:  # noqa: BLE001 - a check that cannot start decided nothing
        outcome.failure, outcome.exit_code = type(error).__name__, 1
        _deny_failed(outcome, event)
        return outcome
    outcome.exit_code, outcome.stdout, outcome.stderr = done.returncode & 0xFF, done.stdout, done.stderr
    if check.on_timeout == "deny":
        outcome.failure = _died(done.returncode, done.stdout, done.stderr)
        _deny_failed(outcome, event)
    return outcome


def _died(code: int, out: bytes, err: bytes) -> str:
    """Why a run-apart check ended without a decision, else ""."""
    if code < 0:
        return f"signal {-code}"
    if code in (0, 2) or re.search(rb'"(?:permissionD|d)ecision"', out):
        return ""
    if b"Traceback (most recent" not in err:
        return f"exit {code}"
    match = re.match(rb"([A-Za-z_][\w.]*)(?::|$)", err.rstrip().splitlines()[-1])
    return match.group(1).decode().rsplit(".", 1)[-1] if match else "Exception"


def render(result: Optional[registry.Result], event: str) -> tuple[int, dict, str]:
    """A native Result as (exit code, stdout object, stderr text)."""
    if result is None:
        return 0, {}, ""
    obj: dict = {}
    hso: dict = {}
    code, stderr = 0, ""
    decision = result.decision
    if decision in ("allow", "defer", "ask", "deny") and event == "PreToolUse":
        hso["permissionDecision"] = decision
        if result.reason:
            hso["permissionDecisionReason"] = result.reason
    elif decision == "block" and event in _BLOCK_DECISION_EVENTS:
        obj["decision"] = "block"
        obj["reason"] = result.reason
    elif decision == "block":
        code, stderr = 2, result.reason
        if event == "PreToolUse":
            hso["permissionDecision"] = "deny"
            hso["permissionDecisionReason"] = result.reason
    elif decision is not None:
        raise ValueError(f"decision {decision!r} is not valid on {event}")
    if result.context:
        hso["additionalContext"] = result.context
    if hso:
        obj["hookSpecificOutput"] = {"hookEventName": event, **hso}
    if result.system_message:
        obj["systemMessage"] = result.system_message
    return code, obj, stderr


def _apply_native(outcome: Outcome, result, event: str) -> None:
    try:
        if result is not None and not isinstance(result, registry.Result):
            raise TypeError("a native check returns a Result or None")
        code, obj, stderr = render(result, event)
    except (TypeError, ValueError) as error:
        outcome.failure = type(error).__name__
        return
    if obj:
        outcome.stdout += json.dumps(obj).encode("utf-8")
    outcome.stderr += stderr.encode("utf-8")
    outcome.exit_code = code or outcome.exit_code
    if result is not None:
        outcome.rule, outcome.journaled = result.rule, result.journaled


# --- combining -----------------------------------------------------------------------

def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _is_quiet(obj: Optional[dict]) -> bool:
    """True for no JSON, or JSON that sets nothing but suppressOutput and the event name."""
    if obj is None:
        return True
    rest = {key: value for key, value in obj.items() if key != "suppressOutput"}
    if obj.get("suppressOutput", True) is not True:
        return False
    hso = rest.pop("hookSpecificOutput", None)
    if hso is not None and (not isinstance(hso, dict) or set(hso) - {"hookEventName"}):
        return False
    return not rest


def parse(outcome: Outcome, event: str) -> Parsed:
    """Read one outcome as the host reads a separate hook's output."""
    parsed = Parsed(outcome=outcome)
    if outcome.discarded:
        return parsed
    text = _text(outcome.stdout)
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            loaded = json.loads(stripped)
            if isinstance(loaded, dict):
                parsed.obj = loaded
        except ValueError:
            pass
    if parsed.obj is None and stripped:
        parsed.plain = text
    obj = parsed.obj or {}
    hso = obj.get("hookSpecificOutput") if isinstance(obj.get("hookSpecificOutput"), dict) else {}
    json_reason = ""
    if event == "PreToolUse" and hso.get("permissionDecision") in _RANK:
        parsed.decision = hso["permissionDecision"]
        json_reason = str(hso.get("permissionDecisionReason") or "")
    if obj.get("decision") == "block":
        parsed.decision = "block" if event != "PreToolUse" else "deny"
        json_reason = str(obj.get("reason") or "")
    if outcome.exit_code == 2:
        # The blocking message is the JSON's own reason only when the JSON refuses too.
        if parsed.decision not in ("deny", "block"):
            json_reason = ""
        parsed.decision = "deny" if event == "PreToolUse" else "block"
    parsed.reason = json_reason or (_text(outcome.stderr) if outcome.exit_code == 2 else "")
    parsed.channel = rules.channel_of(outcome, parsed.decision)
    tail = rules.RULE_TAIL.search(parsed.reason) or (
        rules.RULE_TAIL.search(_text(outcome.stderr)) if outcome.exit_code == 2 else None)
    parsed.rule = outcome.rule or (tail.group(1) if tail else "")
    context = hso.get("additionalContext")
    if isinstance(context, str) and context:
        parsed.contexts.append(context)
    if parsed.plain is not None and event in _PLAIN_CONTEXT_EVENTS:
        parsed.contexts.append(parsed.plain)
    trivial_obj = _is_quiet(parsed.obj)
    parsed.effect = (outcome.exit_code != 0 or not trivial_obj
                     or (parsed.plain is not None and event in _PLAIN_CONTEXT_EVENTS))
    return parsed


def _error_notice(outcome: Outcome) -> str:
    lines = _text(outcome.stderr).strip().splitlines()
    first = lines[0] if lines else "No stderr output"
    return f"{outcome.check.name} hook error: Failed with non-blocking status code: {first}"


def combine(event: str, outcomes: list) -> tuple[int, bytes, bytes, list]:
    """(exit code, stdout, stderr, parsed outcomes) for the event's outcomes."""
    parsed = [parse(outcome, event) for outcome in outcomes]
    effective = [item for item in parsed if item.effect]
    if not effective:
        quiet = next((item.outcome.stdout for item in parsed if item.obj is not None), b"")
        return 0, quiet, b"", parsed
    if len(effective) == 1:
        only = effective[0].outcome
        stdout, stderr = only.stdout, only.stderr
        if only.exit_code not in (0, 2):
            # The host's hook-error line names the command, dispatch.py; this names the check.
            stderr = f"{only.check.name}: ".encode("utf-8") + (stderr or b"No stderr output\n")
        elif effective[0].decision in rules.REFUSALS:
            stdout, stderr = rules.decorated_single(effective[0])
        return only.exit_code, stdout, stderr, parsed
    winner = None
    for item in effective:
        if item.decision and (winner is None or _RANK[item.decision] > _RANK[winner.decision]):
            winner = item
    exit_code = 2 if any(item.outcome.exit_code == 2 for item in effective) else 0
    # Every refusal of the winning rank is shown, as the host shows every blocking hook.
    peers = [item for item in effective if winner is not None and item.decision
             and _RANK[item.decision] == _RANK[winner.decision]]
    reason = REASON_SEPARATOR.join(rules.decorate(item, item.reason) for item in peers
                                   if item.reason.strip())
    envelope: dict = {}
    hso: dict = {}
    if winner is not None and event == "PreToolUse":
        hso["permissionDecision"] = "deny" if winner.decision == "block" else winner.decision
        if reason:
            hso["permissionDecisionReason"] = reason
    if winner is not None and winner.decision == "block" and event in _BLOCK_DECISION_EVENTS:
        envelope["decision"] = "block"
        envelope["reason"] = reason
    contexts = [context for item in effective for context in item.contexts]
    if contexts:
        hso["additionalContext"] = "\n".join(contexts)
    objects = [item.obj for item in effective if item.obj is not None]
    for obj in objects:
        inner = obj.get("hookSpecificOutput")
        if isinstance(inner, dict):
            if "updatedInput" in inner and "updatedInput" not in hso:
                hso["updatedInput"] = inner["updatedInput"]
            for key, value in inner.items():
                if key not in _HANDLED_HSO and key not in hso:
                    hso[key] = value
    messages = [str(obj["systemMessage"]) for obj in objects if obj.get("systemMessage")]
    if exit_code == 0:
        # The host showed a separate hook's non-blocking error (another non-zero exit, no
        # JSON object) as "<hook> hook error" plus stderr's first line; exit 0 shows none.
        messages += [_error_notice(item.outcome) for item in effective
                     if item.outcome.exit_code not in (0, 2) and item.obj is None]
    if messages:
        envelope["systemMessage"] = "\n".join(messages)
    stopper = next((obj for obj in objects if obj.get("continue") is False), None)
    if stopper is not None:
        envelope["continue"] = False
        if "stopReason" in stopper:
            envelope["stopReason"] = stopper["stopReason"]
    for obj in objects:
        for key, value in obj.items():
            if key not in _HANDLED_TOP and key not in envelope:
                envelope[key] = value
    if objects and all(obj.get("suppressOutput") is True for obj in objects):
        envelope["suppressOutput"] = True
    if hso:
        envelope["hookSpecificOutput"] = {"hookEventName": event, **hso}
    if exit_code == 2:
        refusers = [item for item in peers if item.outcome.exit_code == 2 or item.reason]
        texts = [rules.decorate(item, _text(item.outcome.stderr) if item.outcome.exit_code == 2
                          else item.reason) for item in refusers]
        stderr = REASON_SEPARATOR.join(texts).encode("utf-8")
    else:
        stderr = b"".join(item.outcome.stderr for item in effective)
    stdout = json.dumps(envelope).encode("utf-8") if envelope else b""
    return exit_code, stdout, stderr, parsed


# --- journal -------------------------------------------------------------------------

def _hash12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12] if text else ""


def _plugin_version() -> str:
    try:
        manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text("utf-8"))
        version = manifest.get("version")
        return version if isinstance(version, str) else ""
    except (OSError, ValueError, AttributeError):
        return ""


def _target(payload: dict) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "notebook_path", "path", "taskId", "url"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return _hash12(" ".join(value.split()))
    return ""


def _frame(payload: dict) -> str:
    agent_type = payload.get("agent_type")
    if isinstance(agent_type, str) and agent_type and agent_type not in _LEAD_AGENT_TYPES:
        return "teammate"
    return "lead"


def _fallback_row(row: dict) -> None:
    """Standard-library append of a check_failure or gate_refusal row the session journal
    cannot take (no session id, no project dir, or no session dir yet)."""
    if row.get("type") not in ("check_failure", "gate_refusal"):
        return
    try:
        root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
        directory = os.path.join(root, "pact-sessions")
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(directory, "_dispatch_failures.jsonl"),
                     os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, (json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 - a row that cannot be written changes no decision
        pass


def _journal_one(payload: dict, event_type: str, fields: dict) -> None:
    """Append one row to the session journal, or to the fallback file."""
    try:
        from shared.pact_context import reconstruct_session_dir
        from shared.session_journal import append_event, make_event
    except Exception:  # noqa: BLE001 - the shared package itself may be what failed
        from datetime import datetime, timezone
        _fallback_row({"v": 1, "type": event_type, **fields,
                       "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
        return
    event = make_event(event_type, **fields)
    try:
        session_id = payload.get("session_id")
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
        session_dir = ""
        if isinstance(session_id, str) and session_id and project_dir:
            session_dir = reconstruct_session_dir(project_dir, session_id)
        written = bool(session_dir) and append_event(event, session_dir=session_dir)
    except Exception:  # noqa: BLE001
        written = False
    if not written:
        _fallback_row(event)


def _actor_name(payload: dict) -> str:
    """The resolved actor's name for a refusal row ("" when the frame names nobody)."""
    try:
        from shared.context_ledger import actor_name
        return actor_name(payload)
    except Exception:  # noqa: BLE001 - a row without a name is still a row
        return ""


def journal_rows(event: str, payload: dict, parsed: list) -> list:
    """The check_failure and gate_refusal rows for one dispatch, as (type, fields); a
    refusal row names the actor (`agent`) when the frame does, for `repact inspect --refusals --agent`."""
    rows = []
    tool_name = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
    agent = ""
    for item in parsed:
        outcome = item.outcome
        if outcome.failure:
            rows.append(("check_failure", {"check": outcome.check.name, "event": event,
                                           "exc_class": outcome.failure}))
        if item.decision not in ("deny", "ask", "block"):
            continue
        if outcome.check.journals_refusals or outcome.journaled:
            continue
        agent = agent or _actor_name(payload)
        rows.append(("gate_refusal", {
            "hook": outcome.check.name, "event": event, "tool_name": tool_name,
            "verdict": item.decision, "rule": rules.rule_of(item), "channel": item.channel,
            "code": _hash12(item.reason),
            "target": _target(payload), "frame": _frame(payload),
            "plugin_version": _plugin_version(), **({"agent": agent} if agent else {}),
        }))
    return rows


# --- context cost (shared/context_ledger.py keeps the file) ---------------------------

COST_FILE = "context-cost.json"


def run_cost(parsed: list) -> dict:
    from shared import context_ledger
    return context_ledger.run_cost(parsed)


def add_cost(text: str, event: str, frame: str, sizes: dict) -> tuple:
    from shared import context_ledger
    return context_ledger.add_cost(text, event, frame, sizes)


def record_cost(event: str, payload: dict, parsed: list) -> None:
    """Add one run to the session's counter file (injection tables, the actor's row) and
    journal the rows the ledger returns. Never raises: a count that cannot be written
    changes no decision."""
    try:
        from shared import context_ledger
        for event_type, fields in context_ledger.record(event, payload, parsed, _frame(payload)):
            _journal_one(payload, event_type, fields)
    except Exception:  # noqa: BLE001
        pass


def _advised(event: str, payload: dict, exit_code: int, stdout: bytes) -> bytes:
    """stdout with the context advisory owed to this frame, if any; stdout on any error."""
    try:
        from shared import context_ledger
        return context_ledger.advise(event, payload, exit_code, stdout, _frame(payload))
    except Exception:  # noqa: BLE001
        return stdout


# --- entry point ---------------------------------------------------------------------

def _read_stdin() -> bytes:
    try:
        return sys.stdin.buffer.read()
    except Exception:  # noqa: BLE001 - no stdin is an empty frame
        return b""


def _payload(raw: bytes) -> dict:
    """stdin as a dict, else {}. JSON nested past the parser's depth (RecursionError) is {}
    too: the checks still run on the raw stdin and each decides as it did alone."""
    try:
        loaded = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _emit(exit_code: int, stdout: bytes, stderr: bytes) -> int:
    _flush_streams(sys.stdout, sys.stderr)
    for fd, data in ((1, stdout), (2, stderr)):
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
    return exit_code


def dispatch(event: str, raw: bytes, checks=None, group: Optional[str] = None) -> int:
    """Run the event's checks over one stdin, print the combined result, return its code."""
    payload = _payload(raw)
    if checks is None:
        checks = registry.checks_for(event)
        if group and event in registry.TOOL_EVENTS and registry.group_checks(event, group) is None:
            _journal_one(payload, "check_failure",
                         {"check": "dispatch", "event": event, "exc_class": "UnknownGroup"})
            group = None
    if not checks:
        _journal_one(payload, "check_failure",
                     {"check": "dispatch", "event": event, "exc_class": "UnknownEvent"})
        return 0
    tool_name = payload.get("tool_name") if isinstance(payload.get("tool_name"), str) else ""
    deep = nesting(raw) >= DEEP_JSON
    memo = sys.modules[FRAME_MEMO] = types.ModuleType(FRAME_MEMO)
    memo.frames = {}
    try:
        outcomes = []
        selected = select(event, checks, payload, group)
        if any(check.teams_only for check in selected) and execution_mode(payload) != "teams":
            selected = [check for check in selected if not check.teams_only]
        for check in selected:
            begun = time.monotonic()
            outcomes.append(run_apart(check, raw, event) if deep else run_check(check, raw, event, tool_name))
            outcomes[-1].elapsed_s = time.monotonic() - begun
    finally:
        sys.modules.pop(FRAME_MEMO, None)
    try:
        exit_code, stdout, stderr, parsed = combine(event, outcomes)
    except Exception:  # noqa: BLE001 - never lose a refusal to a combiner error
        refused = next((o for o in outcomes if o.exit_code == 2), None)
        if refused is None:
            return 0
        return _emit(2, refused.stdout, refused.stderr)
    code = _emit(exit_code, _advised(event, payload, exit_code, stdout), stderr)
    try:
        for event_type, fields in journal_rows(event, payload, parsed):
            _journal_one(payload, event_type, fields)
    except Exception:  # noqa: BLE001 - a row that cannot be written changes no decision
        pass
    record_cost(event, payload, parsed)
    try:
        from shared import hook_latency
        hook_latency.record(payload, event, group, time.monotonic() - STARTED,
                            [(o.check.name, o.elapsed_s) for o in outcomes])
    except Exception:  # noqa: BLE001
        pass
    return code


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: dispatch.py <event> [<group>]", file=sys.stderr)
        return 0
    return dispatch(argv[0], _read_stdin(), group=argv[1] if len(argv) > 1 else None)


if __name__ == "__main__":
    sys.exit(main())
