"""The two host lines repact-doctor prints after its checks: the mode (V1) and the tested-host
compat line (V9).

Location: pact-plugin/bin/_doctor_host.py. Used by bin/repact-doctor (report only:
these lines are notes, never a check state, so they never reach the cache or the exit code).
Stdlib only.

  mode: guards-only (Agent Teams flag off)   the guards and ledgers run in any session
  mode: full (Agent Teams on)                orchestration is available as well
  host 2.1.302: tested (last tested 2.1.302); python 3.9.6 (min 3.9); os macos: tested

The flag is CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS, read from the environment, then from the
`env` block of the config root's settings.json. An absent flag is a valid mode, not a fault.
The host version is `claude --version`; a missing or silent binary prints `host unknown`.
"""
from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

TEAMS_FLAG = "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"
COMPAT = Path(__file__).resolve().parents[1] / "reference" / "compat.json"
VERSION_RE = re.compile(r"\d+(?:\.\d+)+")
VERSION_TIMEOUT = 10


def _flag_on(value) -> bool:
    return str(value).strip().lower() not in ("", "0", "false", "no", "off", "none")


def teams_on(ctx) -> bool:
    """The Agent Teams flag from the environment, else the settings.json `env` block."""
    if TEAMS_FLAG in ctx.env:
        return _flag_on(ctx.env[TEAMS_FLAG])
    try:
        settings = json.loads((ctx.config_root / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    block = settings.get("env") if isinstance(settings, dict) else None
    return isinstance(block, dict) and TEAMS_FLAG in block and _flag_on(block[TEAMS_FLAG])


def mode_line(ctx) -> str:
    return "mode: full (Agent Teams on)" if teams_on(ctx) else "mode: guards-only (Agent Teams flag off)"


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
