"""The two host lines repact-doctor prints after its checks: the execution mode and the
tested-host compat line.

Location: pact-plugin/bin/_doctor_host.py. Used by bin/repact-doctor (report only:
these lines are notes, never a check state, so they never reach the cache or the exit code).
Stdlib only.

  execution: subagents (default)                      work runs as subagents
  execution: teams (REPACT_EXECUTION=teams)           work runs as Agent Teams
  host 2.1.302: tested (last tested 2.1.302); python 3.9.6 (min 3.9); os macos: tested

The mode is shared/execution.decide over the environment laid on the `env` block of the config
root's settings.json (the environment wins, as in a session): REPACT_EXECUTION, then the team
policy, then the default; teams also needs CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS. Either mode is
valid, not a fault.
The host version is `claude --version`; a missing or silent binary prints `host unknown`.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

TEAMS_FLAG = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"
COMPAT = Path(__file__).resolve().parents[1] / "reference" / "compat.json"
HOOKS = Path(__file__).resolve().parents[1] / "hooks"
VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
VERSION_TIMEOUT = 10


def _flag_on(value) -> bool:
    return str(value).strip().lower() not in ("", "0", "false", "no", "off", "none")


def _session_env(ctx) -> dict:
    """The env a session here starts with: the settings.json `env` block under the environment."""
    try:
        settings = json.loads((ctx.config_root / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = None
    block = settings.get("env") if isinstance(settings, dict) else None
    seeded = {str(k): str(v) for k, v in block.items()} if isinstance(block, dict) else {}
    return {**seeded, **ctx.env}


def teams_on(ctx) -> bool:
    """The Agent Teams flag from the environment, else the settings.json `env` block."""
    env = _session_env(ctx)
    return TEAMS_FLAG in env and _flag_on(env[TEAMS_FLAG])


def mode_line(ctx) -> str:
    """The execution mode a new session here takes, and why."""
    try:
        if str(HOOKS) not in sys.path:   # bin -> shared, the allowed direction
            sys.path.insert(1, str(HOOKS))
        from shared import execution
        repo = str(ctx.repo_root) if ctx.repo_root else None
        chosen = execution.decide(_session_env(ctx), cwd=repo)
    except Exception:  # noqa: BLE001 - a note, never a check state
        return "execution: unknown (the execution rules could not be read)"
    return f"execution: {chosen['mode']} ({chosen['why']})"


def _key(version: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def host_version(ctx) -> Optional[str]:
    """`claude --version` on the context's PATH; None when absent, slow or unparseable."""
    binary = shutil.which("claude", path=ctx.env.get("PATH"))
    if not binary:
        return None
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True,
                              timeout=VERSION_TIMEOUT, env=ctx.env)
    except (OSError, subprocess.SubprocessError):
        return None
    match = VERSION_RE.search(proc.stdout or "")
    return match.group(0) if proc.returncode == 0 and match else None


def _os_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux" and "microsoft" in platform.release().lower():
        return "wsl"
    return system or "unknown"


def compat_line(ctx, compat_path: Path = COMPAT) -> str:
    """`host <ver>: tested|untested (last tested <ver>)`, then the Python and OS parts."""
    try:
        compat = json.loads(compat_path.read_text(encoding="utf-8"))
        tested: List[str] = [v for v in compat["claude_code"]["tested"] if VERSION_RE.fullmatch(v)]
        python_min = str(compat["python"]["min"])
        os_tested = list(compat["os"]["tested"])
    except (OSError, ValueError, KeyError, TypeError):
        return f"host: no readable compat matrix at {compat_path.name}"
    last = max(tested, key=_key) if tested else "none"
    version = host_version(ctx)
    host = f"host {version or 'unknown'}: {'tested' if version in tested else 'untested'} (last tested {last})"
    py = platform.python_version()
    below = VERSION_RE.fullmatch(python_min) and _key(VERSION_RE.match(py).group(0)) < _key(python_min)
    os_name = _os_name()
    return (f"{host}; python {py} ({'below the ' if below else ''}min {python_min}); "
            f"os {os_name}: {'tested' if os_name in os_tested else 'untested'}")
