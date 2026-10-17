"""The model backend for repact-distill: `claude -p` on the user's own login, isolated by flags.

Location: pact-plugin/bin/_distill/backend.py. bin/repact-distill loads it as the seam
`make_backend(settings) -> Backend` (estimate.Backend) and calls extract() one chunk at a time.

The command line is the U5.1 spike's (the design notes), isolation flags
verbatim; only the output format is stream-json (lead ruling on #106) so the init event can be
checked: a run whose init event lists any MCP server, or any tool but the CLI's own
StructuredOutput reply tool (which --json-schema adds; measured on CLI 2.1.302), is aborted.
  - argv list, never a shell; the chunk goes on stdin, never on argv (argv shows in ps).
  - a timeout kills the whole process group; stdout is read up to MAX_STDOUT_BYTES and every
    line must parse as JSON; stderr is discarded (it is never printed or stored).
  - the result must be a success event with `structured_output`: no text fallback.
Any breach raises BackendError, which the run loop records as a `failed` chunk.

Settings (profile `distill` block, plus `model` from the CLI): model (default sonnet),
timeout_s (default 300), claude_bin (default: `claude` on PATH).
"""
from __future__ import annotations

import json
import os
import re
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from typing import Iterator

from _distill import estimate as E
from _distill import prompts, schema

NAME = "claude-cli"
DEFAULT_MODEL = "sonnet"
DEFAULT_TIMEOUT_S = 300.0
MAX_TIMEOUT_S = 3600.0
MAX_STDOUT_BYTES = 4 * 1024 * 1024
READ_BLOCK = 65536
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,63}$")   # never starts with '-'
# The spike's isolation flags, verbatim (U5.1 RECOMMENDED LINE). Do not edit without a new spike.
ISOLATION_FLAGS = ("--tools", "", "--strict-mcp-config", "--setting-sources", "",
                   "--safe-mode", "--disable-slash-commands", "--no-session-persistence")
OUTPUT_FLAGS = ("--output-format", "stream-json", "--verbose")
ALLOWED_TOOLS = ("StructuredOutput",)      # the schema reply tool --json-schema adds; nothing else
USAGE_INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


class BackendError(RuntimeError):
    """The backend call failed or broke a rule; the message is a reason kind, never output text."""


def _setting_model(settings: dict) -> str:
    model = settings.get("model") or DEFAULT_MODEL
    if not isinstance(model, str) or not MODEL_RE.match(model):
        raise ValueError("distill.model must be a model name (letters, digits, . _ : - [ ])")
    return model


def _setting_timeout(settings: dict) -> float:
    value = settings.get("timeout_s", DEFAULT_TIMEOUT_S)
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not 0 < value <= MAX_TIMEOUT_S:
        raise ValueError(f"distill.timeout_s must be a number of seconds, 0 < t <= {MAX_TIMEOUT_S:g}")
    return float(value)


def _setting_bin(settings: dict) -> str:
    configured = settings.get("claude_bin")
    if configured is not None and not isinstance(configured, str):
        raise ValueError("distill.claude_bin must be a path to the claude executable")
    found = shutil.which(os.path.expanduser(configured or "claude"))
    if not found:
        raise ValueError("the claude executable was not found (set distill.claude_bin)")
    return found


def command(binary: str, model: str) -> list:
    """The argv for one extraction call (the chunk goes on stdin)."""
    return [binary, "-p", "--model", model, *OUTPUT_FLAGS, *ISOLATION_FLAGS,
            "--system-prompt", prompts.SYSTEM_PROMPT,
            "--json-schema", json.dumps(schema.OUTPUT_SCHEMA, separators=(",", ":"))]


def _feed(pipe, data: bytes) -> None:
    try:
        pipe.write(data)
    except OSError:          # the child exited early; the exit code tells the story
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
    proc.wait()


def run_bounded(argv: list, data: bytes, timeout: float, cap: int = MAX_STDOUT_BYTES) -> bytes:
    """stdout of `argv` fed `data` on stdin, or BackendError on a timeout, an oversize output or
    a non-zero exit. The child runs in its own process group so a timeout kills all of it, with
    a fresh empty temp dir as its cwd (removed afterwards)."""
    with tempfile.TemporaryDirectory(prefix="repact-distill-") as cwd:
        return _run_in(argv, data, timeout, cap, cwd)


def _run_in(argv: list, data: bytes, timeout: float, cap: int, cwd: str) -> bytes:
    """run_bounded in `cwd`, a fresh empty directory: the CLI finds no project memory, settings
    or CLAUDE.md of the caller's working tree there."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=cwd,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as exc:
        raise BackendError(f"the backend did not start ({type(exc).__name__})") from None
    threading.Thread(target=_feed, args=(proc.stdin, data), daemon=True).start()
    out, deadline, fd = bytearray(), time.monotonic() + timeout, proc.stdout.fileno()
    try:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise BackendError("timeout")
            ready, _, _ = select.select([fd], [], [], left)
            if not ready:
                continue
            block = os.read(fd, READ_BLOCK)
            if not block:
                break
            out += block
            if len(out) > cap:
                raise BackendError("output too large")
        try:
            code = proc.wait(timeout=max(deadline - time.monotonic(), 0.1))
        except subprocess.TimeoutExpired:
            raise BackendError("timeout") from None
        if code != 0:
            raise BackendError(f"exit {code}")
        return bytes(out)
    finally:
        _kill(proc)
        proc.stdout.close()


def _tool_uses(value, depth: int = 0) -> Iterator[str]:
    """The name of every tool_use block anywhere in an event (bounded depth)."""
    if depth > 8:
        return
    if isinstance(value, dict):
        if value.get("type") in ("tool_use", "server_tool_use"):
            yield str(value.get("name"))
        for item in value.values():
            yield from _tool_uses(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from _tool_uses(item, depth + 1)


def _check_isolation(events: list) -> None:
    """Every init event lists only ALLOWED_TOOLS and no MCP server, and every tool_use names an
    allowed tool: a second init or a tool call mid-stream breaks isolation as much as the first."""
    inits = [e for e in events if isinstance(e, dict) and e.get("type") == "system"
             and e.get("subtype") == "init"]
    if not inits:
        raise BackendError("no init event")
    for init in inits:
        tools = init.get("tools")
        if not isinstance(tools, list) or any(tool not in ALLOWED_TOOLS for tool in tools) \
                or init.get("mcp_servers") != []:
            raise BackendError("isolation broken: the session has tools or MCP servers")
    if any(name not in ALLOWED_TOOLS for event in events for name in _tool_uses(event)):
        raise BackendError("isolation broken: a tool other than StructuredOutput was called")


def parse_stream(raw: bytes) -> tuple:
    """(structured_output, usage dict, cost or None) from stream-json stdout. Fails closed: a line
    that is not JSON, a missing or non-empty-tools init event, a disallowed tool call, a result
    without structured_output, or a cost or usage count that is not finite and >= 0 raises
    BackendError."""
    try:
        events = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    except (UnicodeDecodeError, ValueError):
        raise BackendError("output is not JSON lines") from None
    _check_isolation(events)
    results = [e for e in events if isinstance(e, dict) and e.get("type") == "result"]
    if len(results) != 1:
        raise BackendError("not exactly one result event")
    result = results[0]
    if result.get("is_error") is not False or result.get("subtype") != "success":
        raise BackendError("the backend reported an error")
    output = result.get("structured_output")
    if not isinstance(output, dict):
        raise BackendError("no structured output")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    cost = result.get("total_cost_usd")
    cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    problem = E.usage_problem(cost, *(usage.get(f) for f in (*USAGE_INPUT_FIELDS, "output_tokens")
                                      if f in usage))
    if problem:
        raise BackendError(problem)
    return output, usage, cost


def _count(usage: dict, *fields: str) -> int:
    return sum(int(usage[f]) for f in fields
               if isinstance(usage.get(f), int) and not isinstance(usage.get(f), bool))


class ClaudeCliBackend:
    """estimate.Backend over `claude -p`. extract() yields one ChunkResult per chunk; its
    candidates are the model's raw list, validated by the run loop (schema, then postcheck)."""

    name = NAME

    def __init__(self, settings: dict, runner=run_bounded):
        self.model = _setting_model(settings)
        self.timeout = _setting_timeout(settings)
        self.binary = _setting_bin(settings)
        self._run = runner

    def estimate(self, chunks: list) -> E.Estimate:
        return E.estimate_chunks(chunks, self.model, None, E.DEFAULT_BYTES_PER_TOKEN)

    def extract(self, chunks: list) -> Iterator[E.ChunkResult]:
        for chunk in chunks:
            data = prompts.build_input(chunk.text).encode("utf-8")
            raw = self._run(command(self.binary, self.model), data, self.timeout)
            output, usage, cost = parse_stream(raw)
            if set(output) != {"candidates"} or not isinstance(output["candidates"], list):
                raise BackendError("output shape")
            yield E.ChunkResult(chunk.chunk_key, output["candidates"],
                                _count(usage, *USAGE_INPUT_FIELDS),
                                _count(usage, "output_tokens"), cost)


def make_backend(settings: dict) -> ClaudeCliBackend:
    """The seam bin/repact-distill loads. Raises ValueError on a bad setting (the run refuses)."""
    return ClaudeCliBackend(settings if isinstance(settings, dict) else {})
