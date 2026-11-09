"""
Location: pact-plugin/hooks/shell/tools.py
Summary: The tool names whose tool_input.command is a shell command the shell guards judge.
Used by: process_table_guard.py, env_guard.py, settings_guard.py, egress_guard.py,
    write_checks.py, docker_lock_gate.py (each judges `shell_command(payload)` where it
    tested `tool_name == "Bash"`), and dispatch.py (MONITOR_TOOL, for the rewrite hold).

Claude Code 2.1.314 runs the PreToolUse hooks that match Bash on the shell commands the
Monitor tool starts, with tool_name "Monitor" and the command in tool_input.command (the
Monitor input schema: command, description, timeout_ms, or a `ws` source with no shell).
Those hooks may deny or ask; their allow and updatedInput are not applied. A guard that
returned nothing for any tool but "Bash" let a Monitor command through unjudged.

Deliberately not here: wait_filler_gate and background_test_gate judge how a Bash call
waits (sleep fillers, harness-background test runs); a Monitor command is the sanctioned
long-running watch and its poll loop sleeps by design. The PostToolUse checks see Bash
output; Monitor's output arrives as notifications, not a tool result, so no output scan
redacts it. env_guard covers that for env-get: under Monitor the keys it hands a command
count as loaded, and a command that prints one is refused. An opaque program env-get runs
(a watcher script) can still print a value, and nothing redacts it there.

Standard library only, imports nothing: it is loaded before every Bash call.
"""

from __future__ import annotations

MONITOR_TOOL = "Monitor"
# A tuple, not a set: `in` compares by equality, so a tool_name a payload gives as a list
# or a dict is simply not a shell tool instead of a TypeError.
SHELL_TOOLS = ("Bash", MONITOR_TOOL)


def shell_command(payload) -> str | None:
    """The shell command a payload carries: its tool_input.command when that is a string,
    whatever tool_name says, else None. A guard calls it after the branches for the other
    tools it judges by name (Read, Grep, WebFetch, the file tools): the host chose the guard
    by its Bash matcher, so a renamed, misspelled or missing tool_name changes no verdict."""
    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    return command if isinstance(command, str) else None
