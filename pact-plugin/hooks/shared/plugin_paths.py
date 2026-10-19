"""
Location: pact-plugin/hooks/shared/plugin_paths.py
Summary: Where the plugin keeps its data: plugin root, the config dir, the plugin data dir
         (mode 700), the run ledger and check-log paths, the read-only profile, and the
         TaskStore handle for a config root.
Used by: hooks/docker_lock_gate.py, hooks/session_ledger.py, bin/_doctor*.py, bin/_replay.py,
         bin/repact-lock, bin/_recall/*, shared/check_modes.py, shared/task_files.py and the bin/_repact_common shim.
Map (file order): PLUGIN_DATA_DIRNAME, DATA_DIR_MODE, plugin_root, plugin_name, claude_config_dir,
task_store, data_dirname, default_data_dir, _exported_data_dir, plugin_data_dir, data_subdir, ledger_path, checks_dir, profile_path, profile,
owner_identity.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

from .paths import get_claude_config_dir


PLUGIN_DATA_DIRNAME = "repacted-repacted"   # data_dirname()'s fallback outside an install
DATA_DIR_MODE = 0o700
EXPORTED_DATA_ENV = "REPACTED_PLUGIN_DATA"        # the hooks' data dir, exported to the Bash tool


def plugin_root() -> Path:
    """The plugin root (the parent of bin/ and hooks/; this module sits in hooks/shared/)."""
    return Path(__file__).resolve().parents[2]


def plugin_name() -> str:
    """The plugin's name from its own .claude-plugin/plugin.json ("repacted" when unreadable): the
    prefix the harness gives this plugin's agents, e.g. "repacted:pact-backend-coder"."""
    try:
        name = json.loads((plugin_root() / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return "repacted"
    return name if isinstance(name, str) and name.replace("-", "").replace("_", "").isalnum() else "repacted"


def claude_config_dir() -> Path:
    """$CLAUDE_CONFIG_DIR, else ~/.claude: the one resolver, shared.paths.get_claude_config_dir."""
    return get_claude_config_dir()


def task_store(config_root: Optional[Path] = None):
    """The host task store (hooks/shared/task_store.TaskStore) of a config root, by default
    this process's. The one way bin/ reaches a task file: no bin module spells the path."""
    hooks = str(plugin_root() / "hooks")
    if hooks not in sys.path:
        sys.path.append(hooks)
    from shared.task_store import TaskStore
    return TaskStore.for_config(config_root if config_root is not None else claude_config_dir())


def data_dirname(root: Optional[Path] = None) -> str:
    """The loader's data dir name, <plugin>-<marketplace>, for the plugin copy at `root`.

    An installed copy sits at <cfg>/plugins/cache/<marketplace>/<plugin>/<version>/, so the name
    comes from that path and holds for any marketplace. Any other root (a checkout loaded with
    --plugin-dir, a test) keeps PLUGIN_DATA_DIRNAME.
    """
    parts = (root if root is not None else plugin_root()).parts
    if len(parts) >= 5 and parts[-5:-3] == ("plugins", "cache"):
        return f"{parts[-2]}-{parts[-3]}"
    return PLUGIN_DATA_DIRNAME


def default_data_dir() -> Path:
    """<cfg>/plugins/data/<data_dirname()>: where the loader keeps this copy's data. The one
    spelling of the fallback; no other module builds it."""
    return claude_config_dir() / "plugins" / "data" / data_dirname()


def _exported_data_dir() -> Optional[Path]:
    """$REPACTED_PLUGIN_DATA when it is an absolute path, else None (a relative one would name a
    different directory from every cwd)."""
    value = os.environ.get(EXPORTED_DATA_ENV)
    path = Path(value).expanduser() if value else None
    return path if path is not None and path.is_absolute() else None


def plugin_data_dir(create: bool = True) -> Path:
    """$CLAUDE_PLUGIN_DATA when the harness sets it (a hook), else $REPACTED_PLUGIN_DATA (a Bash
    tool: SessionStart exports the hooks' value through $CLAUDE_ENV_FILE), else the loader's
    default location.

    The export matters under `claude -p --plugin-dir`, where hooks get <name>-inline but the
    default is <name>-<marketplace>: without it tcount and the run-ledger hook wrote two ledgers.
    The default still serves a test, a script or the user's own terminal.
    """
    env_value = os.environ.get("CLAUDE_PLUGIN_DATA")
    path = Path(env_value).expanduser() if env_value else (_exported_data_dir() or default_data_dir())
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=DATA_DIR_MODE)
    return path


def data_subdir(*parts: str, create: bool = True) -> Path:
    """A directory under the plugin data dir, e.g. data_subdir("checks", "gates")."""
    path = plugin_data_dir(create).joinpath(*parts)
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=DATA_DIR_MODE)
    return path


def ledger_path(create_dir: bool = True) -> Path:
    """${CLAUDE_PLUGIN_DATA}/runs/ledger.jsonl"""
    return data_subdir("runs", create=create_dir) / "ledger.jsonl"


def checks_dir(create: bool = True) -> Path:
    """${CLAUDE_PLUGIN_DATA}/checks: the check log (shadow.jsonl) and the gate state (gates/)."""
    return data_subdir("checks", create=create)


def profile_path() -> Path:
    """${XDG_CONFIG_HOME:-~/.config}/repacted/profile.json (the D8a per-user profile)."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "repacted" / "profile.json"


def _refuse_duplicate_keys(pairs: list) -> dict:
    """json object_pairs_hook: a repeated key raises (json's default keeps the last silently)."""
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate key in profile.json")
    return dict(pairs)


def profile() -> dict:
    """The per-user profile, or {} when it is missing, unreadable or holds a duplicate key
    (read-only here). A duplicate is refused, not resolved: with "last wins" a second
    identity.name could silently replace the name every scan checks for."""
    try:
        data = json.loads(profile_path().read_text(encoding="utf-8"),
                          object_pairs_hook=_refuse_duplicate_keys)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# (profile path, its mtime, the identity object) of the last read; the scrub patterns ask on
# every candidate match, and a stat is cheap where a JSON parse is not.
_IDENTITY_CACHE: Optional[tuple] = None


def owner_identity() -> dict:
    """The profile's `identity` object (name, email, slack_id; D8a), string values only, or {}
    when the profile or the object is missing. No default: an unconfigured identity means the
    owner-specific scrub exemptions simply do not fire."""
    global _IDENTITY_CACHE
    path = profile_path()
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return {}
    if _IDENTITY_CACHE and _IDENTITY_CACHE[0] == str(path) and _IDENTITY_CACHE[1] == stamp:
        return _IDENTITY_CACHE[2]
    raw = profile().get("identity")
    identity = ({key: value for key, value in raw.items() if isinstance(value, str) and value}
                if isinstance(raw, dict) else {})
    _IDENTITY_CACHE = (str(path), stamp, identity)
    return identity
