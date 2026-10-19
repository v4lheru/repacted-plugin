"""
Location: pact-plugin/telegram/deps.py
Summary: Dependency checker and installer for the pact-telegram MCP server, and the
         opt-in registration helpers behind /repacted:init telegram.
Used by: __main__.py (pre-flight check), `/repacted:init telegram` (installation and
         registration, through `python3 -m telegram.deps <command>`).

Checks whether required Python packages (mcp, httpx) are importable and
optionally installs them from the bundled requirements.txt.

Design decisions:
- check_dependencies() is a pure check with no side effects
- install_dependencies() uses pip subprocess to install from requirements.txt
- Installation targets the user site-packages (--user) to avoid permission issues,
  or, for an opt-in install, a private directory under the plugin data dir
  (${CLAUDE_PLUGIN_DATA}/telegram-deps), which survives plugin updates
- activate_deps_dir() puts that directory first on sys.path; the server finds it
  through PACT_TELEGRAM_DEPS (set by the user-scope registration) or
  CLAUDE_PLUGIN_DATA. With neither set, or the directory absent, it changes nothing.
- A plugin whose .mcp.json declares the server keeps today's flow (toggle in /plugin).
  A plugin that declares none is the opt-in flow: the server is registered at user
  scope through <config>/protocols/pact-plugin, the link SessionStart repoints at the
  current plugin root, so the entry survives plugin updates. The launch path is a
  script path, not PYTHONPATH: Python collapses "pact-plugin/.." lexically in
  PYTHONPATH, while the OS opens a script path through the link.

Usage (from the setup command):
    PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m telegram.deps mode
    PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m telegram.deps install
    PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m telegram.deps register
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Required packages for the MCP server (not the Stop hook)
REQUIRED_PACKAGES = [
    ("mcp", "mcp"),      # MCP SDK (FastMCP)
    ("httpx", "httpx"),   # Async HTTP client for Telegram API
]

# Path to requirements.txt relative to this file
REQUIREMENTS_FILE = Path(__file__).parent / "requirements.txt"

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SERVER_NAME = "pact-telegram"
DEPS_ENV_VAR = "PACT_TELEGRAM_DEPS"
DEPS_DIRNAME = "telegram-deps"
# The link SessionStart keeps pointed at <current plugin root>/protocols.
STABLE_LINK = Path("protocols") / "pact-plugin"


def deps_dir() -> Path | None:
    """
    The private dependency directory, if the environment names one.

    Returns:
        $PACT_TELEGRAM_DEPS, else $CLAUDE_PLUGIN_DATA/telegram-deps, else None.
    """
    explicit = os.environ.get(DEPS_ENV_VAR)
    if explicit:
        return Path(explicit).expanduser()
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    return Path(data).expanduser() / DEPS_DIRNAME if data else None


def activate_deps_dir(path: Path | None = None) -> Path | None:
    """
    Put the private dependency directory first on sys.path, when it exists.

    Args:
        path: The directory; defaults to deps_dir().

    Returns:
        The directory added (or already present), or None when there is none.
    """
    path = path if path is not None else deps_dir()
    if path is None or not path.is_dir():
        return None
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)
        importlib.invalidate_caches()
    return path


def check_dependencies() -> list[str]:
    """
    Check if required packages are importable.

    Returns:
        List of missing package names (pip install names). Empty list if
        all dependencies are satisfied.
    """
    missing = []
    for import_name, pip_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
        except ImportError:
            missing.append(pip_name)
    return missing


def install_dependencies(
    quiet: bool = False, target: Path | None = None,
) -> tuple[bool, str]:
    """
    Install required dependencies from requirements.txt.

    Uses pip to install packages into the user's site-packages directory,
    or into `target` when given. Falls back to installing individual
    packages if requirements.txt is not found.

    Args:
        quiet: If True, suppress pip output (useful for automated setup).
        target: A private directory to install into (pip --target --upgrade),
            activated on sys.path before the post-install check.

    Returns:
        Tuple of (success: bool, message: str). The message contains
        pip output on failure or a success confirmation.
    """
    pip_args = [sys.executable, "-m", "pip", "install"]
    if target is not None:
        target.mkdir(parents=True, exist_ok=True)
        pip_args.extend(["--target", str(target), "--upgrade"])
    else:
        pip_args.append("--user")

    if quiet:
        pip_args.append("--quiet")

    # Prefer requirements.txt for pinned versions
    if REQUIREMENTS_FILE.exists():
        pip_args.extend(["-r", str(REQUIREMENTS_FILE)])
    else:
        # Fallback: install packages directly (no version pins)
        missing = check_dependencies()
        if not missing:
            return True, "All dependencies already installed."
        pip_args.extend(missing)

    try:
        result = subprocess.run(
            pip_args,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode == 0:
            if target is not None:
                activate_deps_dir(target)
            # Verify installation actually worked
            still_missing = check_dependencies()
            if still_missing:
                return False, (
                    f"pip succeeded but packages still not importable: "
                    f"{', '.join(still_missing)}. "
                    f"You may need to restart your Python environment."
                )
            return True, "Dependencies installed successfully."

        error_output = result.stderr.strip() or result.stdout.strip()
        return False, f"pip install failed:\n{error_output}"

    except subprocess.TimeoutExpired:
        return False, "pip install timed out after 120 seconds."
    except FileNotFoundError:
        return False, (
            "pip not found. Ensure Python and pip are installed and on PATH."
        )
    except Exception as e:
        return False, f"Unexpected error during installation: {e}"


def get_dependency_status() -> dict[str, bool]:
    """
    Get detailed status of each required dependency.

    Returns:
        Dict mapping package import name to whether it's importable.
        Example: {"mcp": True, "httpx": True}
    """
    status = {}
    for import_name, _ in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
            status[import_name] = True
        except ImportError:
            status[import_name] = False
    return status


def get_optional_dependency_status() -> dict[str, bool]:
    """
    Get status of optional dependencies (for enhanced features).

    Currently checks:
    - openai: Required for voice note transcription via Whisper API

    Returns:
        Dict mapping feature name to whether its dependency is available.
    """
    optional = {}

    # Voice transcription requires the openai package OR direct HTTP (we use httpx)
    # Since we use httpx directly for Whisper API, this just checks if an
    # OpenAI API key is configured (not a package dependency)
    try:
        from telegram.config import load_config
        config = load_config()
        optional["voice_transcription"] = bool(config.get("openai_api_key"))
    except Exception:
        optional["voice_transcription"] = False

    return optional


# =============================================================================
# Opt-in registration (the /repacted:init telegram command)
# =============================================================================

def plugin_declares_server(plugin_root: Path = PLUGIN_ROOT) -> bool:
    """
    Whether the plugin's own .mcp.json declares the pact-telegram server.

    True on an install that declares it (the host starts it; the setup toggles it in
    /plugin). False on a public install, whose .mcp.json declares no server.
    Accepts both the flat map and the {"mcpServers": {...}} shape.
    """
    try:
        data = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    servers = data.get("mcpServers", data)
    return isinstance(servers, dict) and SERVER_NAME in servers


def server_script_path(config_dir: Path) -> Path:
    """The server entry as a path through the stable link (see the module notes)."""
    return config_dir / STABLE_LINK / ".." / "telegram" / "__main__.py"


def user_scope_add_args(
    config_dir: Path, target: Path, python: str = sys.executable, claude: str = "claude",
) -> list[str]:
    """
    The `claude mcp add` argv that registers the server at user scope.

    Args:
        config_dir: The Claude config dir ($CLAUDE_CONFIG_DIR or ~/.claude).
        target: The private dependency directory, passed as PACT_TELEGRAM_DEPS.
        python: The interpreter that installed the dependencies.
        claude: The claude CLI.
    """
    return [
        claude, "mcp", "add", "--scope", "user", SERVER_NAME,
        "-e", f"{DEPS_ENV_VAR}={target}",
        "--", python, str(server_script_path(config_dir)),
    ]


def register_user_scope(config_dir: Path, target: Path) -> tuple[bool, str]:
    """
    Register (or re-register) the server at user scope.

    Removes an existing user-scope entry first, so a re-run replaces it.

    Returns:
        Tuple of (success, message). The message never holds a secret: the
        registration carries only the dependency path.
    """
    claude = shutil.which("claude")
    if claude is None:
        return False, "claude CLI not found on PATH; run: " + " ".join(
            user_scope_add_args(config_dir, target))
    if not (config_dir / STABLE_LINK).exists():
        return False, (f"{config_dir / STABLE_LINK} is missing; start a new Claude Code "
                       "session with the plugin enabled (SessionStart creates it), then rerun.")
    subprocess.run([claude, "mcp", "remove", SERVER_NAME, "-s", "user"],
                   capture_output=True, text=True, timeout=60)
    result = subprocess.run(user_scope_add_args(config_dir, target, claude=claude),
                            capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return False, f"claude mcp add failed: {(result.stderr or result.stdout).strip()}"
    return True, f"Registered {SERVER_NAME} at user scope; restart Claude Code to load it."


def _plugin_paths():
    """The plugin's one path resolver (hooks/shared/plugin_paths), imported on demand."""
    hooks = str(PLUGIN_ROOT / "hooks")
    if hooks not in sys.path:
        sys.path.append(hooks)
    from shared import plugin_paths
    return plugin_paths


def _cli(argv: list[str]) -> int:
    """`mode` prints plugin|user; `install` and `register` print one result line."""
    command = argv[0] if argv else ""
    if command == "mode":
        print("plugin" if plugin_declares_server() else "user")
        return 0
    if command == "install":
        if plugin_declares_server():
            if not check_dependencies():
                print("All dependencies already installed.")
                return 0
            ok, msg = install_dependencies(quiet=True)
        else:
            ok, msg = install_dependencies(quiet=True, target=_plugin_paths().plugin_data_dir() / DEPS_DIRNAME)
        print(msg)
        return 0 if ok else 1
    if command == "register":
        paths = _plugin_paths()
        ok, msg = register_user_scope(
            paths.claude_config_dir(), paths.plugin_data_dir() / DEPS_DIRNAME)
        print(msg)
        return 0 if ok else 1
    print("usage: python3 -m telegram.deps mode|install|register\n\n"
          'example: PYTHONPATH="${CLAUDE_PLUGIN_ROOT}" python3 -m telegram.deps mode',
          file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
