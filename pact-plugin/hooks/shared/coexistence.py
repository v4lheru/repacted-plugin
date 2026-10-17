"""
Location: pact-plugin/hooks/shared/coexistence.py
Summary: Detect the upstream PACT plugin enabled beside repacted, and word the
    user-visible warning.
Used by: hooks/session_init.py (startup and resume only); bin/_repact_launch.py imports the
    managed-settings reader from here, so bin/repact and the hook share one reader.

Both plugins register hooks for the same events, so with both enabled every
gate runs twice and the two can disagree. The enable flag in settings is the
only deterministic signal: the upstream hooks cannot be changed to announce
themselves. Three scopes are read, in the order Claude Code layers them: the
user settings, the project's shared settings, the project's local settings. The
managed sources are read too (the reader below, which bin/_repact_launch.py
imports for bin/repact's preflight).

Under `repact` in alternate mode (REPACTED_LAUNCH=alternate) the launcher passes
`--settings` disabling every PACT@ key those three scopes enable, and the flag
source beats them, so they are skipped. A managed entry still warns: managed
beats the flag, so PACT really runs.

Never raises: a missing, unreadable or malformed file is skipped.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import sys
from pathlib import Path
from typing import List, Mapping, Optional, Tuple

from .paths import get_claude_config_dir

# An enabledPlugins key is `<plugin>@<marketplace>`; the upstream plugin is `PACT`.
LEGACY_PLUGIN_KEY = re.compile(r"^PACT@")
LAUNCH_MODE_VAR = "REPACTED_LAUNCH"
MANAGED_DIR_OVERRIDE = "REPACTED_MANAGED_SETTINGS_DIR"  # tests point it at a temp dir
MANAGED_DIRS = {"darwin": "/Library/Application Support/ClaudeCode", "linux": "/etc/claude-code"}
MDM_PLIST = "/Library/Managed Preferences/com.anthropic.claudecode.plist"


def settings_files(config_dir: Path, project_dir: Optional[str]) -> List[Path]:
    """The settings files that can enable a plugin, user scope first."""
    files = [config_dir / "settings.json"]
    if project_dir:
        project = Path(project_dir) / ".claude"
        files += [project / "settings.json", project / "settings.local.json"]
    return files


def enabled_legacy_plugins(path: Path) -> List[str]:
    """The `PACT@…` keys set to true in one settings file; [] on any failure."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
        if not isinstance(plugins, dict):
            return []
        return sorted(
            key for key, enabled in plugins.items()
            if isinstance(key, str) and LEGACY_PLUGIN_KEY.match(key) and enabled is True
        )
    except (OSError, ValueError):
        return []


def find_legacy_enablement(config_dir: Path, project_dir: Optional[str]) -> List[Tuple[Path, str]]:
    """(file, key) for every `PACT@…` plugin enabled in any of the scopes."""
    return [
        (path, key)
        for path in settings_files(config_dir, project_dir)
        for key in enabled_legacy_plugins(path)
    ]


# ---------------------------------------------------------------------------
# Managed settings (never raise; a missing or malformed source is skipped)
# ---------------------------------------------------------------------------

def _enabled_plugins(data) -> dict:
    plugins = data.get("enabledPlugins") if isinstance(data, dict) else None
    return plugins if isinstance(plugins, dict) else {}


def _read_json(path: Path) -> dict:
    try:
        return _enabled_plugins(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _read_plist(path: Path) -> dict:
    try:
        with open(path, "rb") as handle:
            return _enabled_plugins(plistlib.load(handle))
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}


def legacy_keys(plugins: dict) -> List[str]:
    """The `PACT@…` keys set to exactly true."""
    return sorted(k for k, v in plugins.items()
                  if isinstance(k, str) and LEGACY_PLUGIN_KEY.match(k) and v is True)


def managed_settings_dir(env: Optional[Mapping[str, str]] = None) -> Optional[Path]:
    env = os.environ if env is None else env
    override = (env.get(MANAGED_DIR_OVERRIDE) or "").strip()
    if override:
        return Path(override)
    system = MANAGED_DIRS.get(sys.platform)
    return Path(system) if system else None


def managed_sources(env: Optional[Mapping[str, str]] = None) -> List[Tuple[Path, dict]]:
    """(source, enabledPlugins) per managed source Claude Code reads.

    The file tier is managed-settings.json merged with managed-settings.d/*.json in
    alphabetical order (hidden and non-.json files skipped), later keys winning, as the
    managed-settings docs describe; it is reported as one source. The macOS MDM profile is
    a second source. With the test override set, the plist is looked up in that dir too.
    """
    env = os.environ if env is None else env
    base = managed_settings_dir(env)
    if base is None:
        return []
    merged, touched = {}, False
    main_file = base / "managed-settings.json"
    drop_ins = sorted(p for p in (base / "managed-settings.d").glob("*.json")
                      if not p.name.startswith(".")) if (base / "managed-settings.d").is_dir() else []
    for path in [main_file] + drop_ins:
        if path.is_file():
            merged.update(_read_json(path))
            touched = True
    sources = [(main_file, merged)] if touched else []
    plist = base / Path(MDM_PLIST).name if env.get(MANAGED_DIR_OVERRIDE) else Path(MDM_PLIST)
    if plist.is_file():
        sources.append((plist, _read_plist(plist)))
    return sources


def managed_legacy_enablement(env: Optional[Mapping[str, str]] = None) -> List[Tuple[Path, str]]:
    """(source, key) for every `PACT@…` plugin a managed source enables; [] on any failure."""
    try:
        return [(source, key) for source, plugins in managed_sources(env)
                for key in legacy_keys(plugins)]
    except Exception:  # noqa: BLE001 — a launch helper and a SessionStart helper must never raise
        return []


def coexistence_warning(project_dir: Optional[str], config_dir: Optional[Path] = None,
                        env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The warning text when PACT is also enabled, else None. Never raises."""
    env = os.environ if env is None else env
    try:
        found = []
        if env.get(LAUNCH_MODE_VAR) != "alternate":
            root = config_dir if config_dir is not None else get_claude_config_dir()
            found = find_legacy_enablement(Path(root), project_dir)
        managed = managed_legacy_enablement(env)
    except Exception:  # noqa: BLE001 — a SessionStart helper must never raise
        return None
    if not found and not managed:
        return None
    where = ", ".join(str(path) for path, _ in found + managed)
    keys = sorted({key for _, key in found + managed})
    commands = "; ".join(f"claude plugin disable {key}" for key in keys)
    warning = (
        f"repacted: PACT is also enabled ({where}). "
        f"Both plugins' hooks run on every event: {commands}"
    )
    if managed:
        warning += (". A managed entry beats --settings and `claude plugin disable`: "
                    "only the administrator can remove it")
    return warning
