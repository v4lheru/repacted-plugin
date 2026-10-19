#!/usr/bin/env python3
"""
Location: pact-plugin/telegram/__main__.py
Summary: Entry point for running the pact-telegram MCP server via `python -m telegram`.
Used by: Claude Code plugin system (via .mcp.json) to spawn the server process.

Performs dependency checking before starting the MCP server. If required
dependencies (mcp, httpx) are missing, prints one stderr line naming the setup
command (no traceback) and exits with code 1. Otherwise, imports and starts the
MCP server via stdio transport. The private dependency directory
(PACT_TELEGRAM_DEPS or ${CLAUDE_PLUGIN_DATA}/telegram-deps) is put on sys.path
first when it exists.

Usage:
    python -m telegram
    # Or as a script (the opt-in user-scope registration, see telegram/deps.py):
    python3 <config>/protocols/pact-plugin/../telegram/__main__.py
"""

import sys
from pathlib import Path


def _use_plugin_root_when_run_as_script() -> None:
    """
    Run as a file, sys.path[0] is this package's own directory: swap it for the
    plugin root so `import telegram` resolves and no sibling module (config,
    server, tools) can shadow a top-level name. A no-op under `python -m`.
    """
    if __package__:
        return
    here = Path(__file__).resolve().parent
    if sys.path and Path(sys.path[0]).resolve() == here:
        sys.path[0] = str(here.parent)
    else:
        sys.path.insert(0, str(here.parent))


def main() -> None:
    """
    Check dependencies and start the MCP server.

    Validates that required packages (mcp, httpx) are installed before
    attempting to start the server. This avoids confusing ImportError
    tracebacks for users who haven't run the setup command yet.
    """
    from telegram.deps import activate_deps_dir, check_dependencies

    activate_deps_dir()
    missing = check_dependencies()
    if missing:
        print(
            f"pact-telegram: not set up (missing {', '.join(missing)}); "
            f"run /repacted:init telegram or pip install {' '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)

    from telegram.server import create_server

    server = create_server()
    server.run(transport="stdio")


if __name__ == "__main__":
    _use_plugin_root_when_run_as_script()
    main()
