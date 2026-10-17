"""tests/contract/test_repact_common_shim.py: bin/_repact_common.py is a pure re-export.

The helpers moved to hooks/shared/ (the design notes §2.1); the
bin module stays for one release as the name external importers (telegram/, tools/,
skills/pact-memory, tests/user_layer) still use. This pins that it defines nothing of its own,
that every name it exports is the very object of its owning shared module (so a patch on the
owner is what every caller sees), and that the names external callers import today resolve.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SHIM = PLUGIN_ROOT / "bin" / "_repact_common.py"

# Names imported from the shim by callers outside bin/ (grep over telegram/, tools/,
# skills/pact-memory and tests/user_layer at the move); dropping one breaks an external caller.
EXTERNAL_NAMES = {
    "KeyFileError", "parse_key_file", "denylist_paths", "load_denylist", "scrub_text", "scan_text",
    "scrub_value", "credential_reason", "secret_kind", "SECRET_ONLY_KINDS", "SCRUB_PATTERNS",
    "ledger_append", "ledger_path", "read_ledger", "DEFAULT_DENYLIST", "DEFAULT_DENYLIST_WORDS",
    "teams_claiming_session", "LABELLED_PASSWORD_MIN", "claude_config_dir", "plugin_data_dir",
}


def _shim():
    """The shim by path (bin/ is not a conftest root); it puts hooks/ on sys.path itself."""
    existing = sys.modules.get("_repact_common")
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location("_repact_common", SHIM)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_repact_common"] = module
    spec.loader.exec_module(module)
    return module


def test_the_shim_defines_nothing():
    tree = ast.parse(SHIM.read_text("utf-8"))
    for node in tree.body:
        assert not isinstance(node, (ast.FunctionDef, ast.ClassDef)), ast.dump(node)[:80]
        if isinstance(node, ast.Assign):
            assert [t.id for t in node.targets if isinstance(t, ast.Name)] == ["__all__"]


def test_every_exported_name_is_the_owning_module_object():
    shim = _shim()
    assert shim.__all__
    for name in shim.__all__:
        obj = getattr(shim, name)
        module = getattr(obj, "__module__", None)
        if module and module.startswith("shared."):
            owner = importlib.import_module(module)
            assert getattr(owner, name) is obj, name


def test_external_callers_names_resolve():
    shim = _shim()
    missing = sorted(n for n in EXTERNAL_NAMES if not hasattr(shim, n))
    assert not missing, missing
