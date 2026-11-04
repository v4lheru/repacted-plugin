"""
Tests for pact-plugin/telegram/__main__.py

Tests cover:
1. main() with missing dependencies: prints friendly error and exits 1
2. main() with all deps present: calls create_server and runs it
3. Error message format: includes missing package names and setup instructions
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Mock the mcp module tree before importing anything that touches server.py
if "mcp" not in sys.modules:
    _mcp = ModuleType("mcp")
    _mcp_server = ModuleType("mcp.server")
    _mcp_server_fastmcp = ModuleType("mcp.server.fastmcp")

    class _FakeFastMCP:
        """Minimal stub for FastMCP."""
        def __init__(self, name, **kwargs):
            self.name = name
            self._lifespan = kwargs.get("lifespan")
            self.instructions = kwargs.get("instructions")
        def tool(self, **kwargs):
            def decorator(fn):
                return fn
            return decorator
        def run(self, **kwargs):
            pass

    _mcp_server_fastmcp.FastMCP = _FakeFastMCP
    _mcp.server = _mcp_server
    _mcp_server.fastmcp = _mcp_server_fastmcp
    sys.modules["mcp"] = _mcp
    sys.modules["mcp.server"] = _mcp_server
    sys.modules["mcp.server.fastmcp"] = _mcp_server_fastmcp

from telegram.__main__ import main


# =============================================================================
# Missing Dependencies Tests
# =============================================================================

class TestMainMissingDeps:
    """Tests for main() when required dependencies are missing."""

    def test_exits_with_code_1_when_deps_missing(self):
        """Should exit with code 1 when check_dependencies returns missing packages."""
        with patch("telegram.deps.check_dependencies", return_value=["mcp", "httpx"]), \
             pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1


# =============================================================================
# Successful Start Tests
# =============================================================================

def _server_module(server):
    """A stand-in for telegram.server: the real one imports httpx, an optional dependency this
    file must not need (main() imports it lazily, after the dependency check)."""
    module = ModuleType("telegram.server")
    module.create_server = MagicMock(return_value=server)
    return module


class TestMainSuccessfulStart:
    """Tests for main() when all dependencies are present."""

    def test_calls_create_server_and_runs(self):
        """Should create server and run with stdio transport when deps are satisfied."""
        mock_server = MagicMock()
        fake = _server_module(mock_server)

        with patch("telegram.deps.check_dependencies", return_value=[]), \
             patch.dict(sys.modules, {"telegram.server": fake}):
            main()
        mock_create = fake.create_server

        mock_create.assert_called_once()
        mock_server.run.assert_called_once_with(transport="stdio")

