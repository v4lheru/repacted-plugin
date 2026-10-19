"""
Location: pact-plugin/hooks/shared/repact_shim.py
Summary: The `repact` command on PATH: a small shim at ~/.local/bin/repact that runs the
    installed plugin's bin/repact with the user's arguments, written at session start.
Used by: shared/session_init_common.py (refresh_environment: ensure_shim on every SessionStart)
    and bin/_doctor_state.py (check_launcher: the doctor's `repact command` line).

WHY THE SHIM RESOLVES THE ROOT AT RUN TIME: the plugin lives in a versioned cache dir that an
update replaces. A shim holding that path would point at a deleted dir between the update and
the next session start. So the shim holds no path: it reads <config>/plugins/installed_plugins.json
each time it runs (RESOLVER below, the same record bin/_agentlinks.py reads) and stays
byte-identical across updates; a session start rewrites it only when this template changes.

Which install it picks: keys whose plugin name is exactly
`repacted` (never `repacted-n8n@...`); user scope first, then any scope but project, then
project; several left: the one enabled in settings.json enabledPlugins; still several: refuse
and list them.

Safety: a `repact` file without MARKER (or any symlink or non-file there) is never written over
or deleted; the doctor names it. A new shim is created with link(2), which fails rather than
replace a file that appeared meanwhile; a refresh of our own shim is tmp + os.replace. The
shim adds no flags. PACT_REPACT_SHIM (pact_config, default on) turns it off: the next session
start then deletes our own shim (MARKER present) and writes nothing.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

OPTION = "PACT_REPACT_SHIM"
MARKER = "# rePACTed: repact launcher shim"
_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})  # pact_config's exact-membership parse

# Run by the shim as `python3 -I -c '<this>' <config root> <args...>`: it must stay free of
# single quotes (shim_text checks). The JSON is only ever data to it, never code.
RESOLVER = r"""
import json, os, sys


def _load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def resolve(config):
    registry = os.path.join(config, "plugins", "installed_plugins.json")
    data = _load(registry)
    if data is None:
        return None, "cannot read " + registry + "; install the repacted plugin with /plugin"
    plugins = data.get("plugins")
    found = []
    for key, entries in (plugins.items() if isinstance(plugins, dict) else ()):
        if not isinstance(key, str) or key.split("@", 1)[0] != "repacted":
            continue
        for entry in (entries if isinstance(entries, list) else ()):
            path = entry.get("installPath") if isinstance(entry, dict) else None
            if isinstance(path, str) and path:
                found.append((key, entry.get("scope"), path))
    if not found:
        return None, "no repacted plugin in " + registry + "; install it with /plugin"
    chosen = ([f for f in found if f[1] == "user"]
              or [f for f in found if f[1] != "project"] or found)
    if len(chosen) > 1:
        settings = _load(os.path.join(config, "settings.json")) or {}
        enabled = settings.get("enabledPlugins")
        enabled = enabled if isinstance(enabled, dict) else {}
        chosen = [f for f in chosen if enabled.get(f[0]) is True] or chosen
    if len(chosen) > 1:
        listed = ", ".join(key + " (" + str(scope) + ") at " + path for key, scope, path in chosen)
        return None, ("several repacted installs and no single one enabled in settings.json: "
                      + listed + "; uninstall the extra one with /plugin")
    launcher = os.path.join(chosen[0][2], "bin", "repact")
    if not (os.path.isfile(launcher) and os.access(launcher, os.X_OK)):
        return None, (launcher + " is missing; the plugin was updated or removed: run claude "
                      "once, or reinstall the repacted plugin with /plugin")
    return chosen[0][2], None


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("repact: the shim passed no config root\n")
        return 2
    root, why = resolve(argv[1])
    if root is None:
        sys.stderr.write("repact: " + why + "\n")
        return 1
    launcher = os.path.join(root, "bin", "repact")
    os.execv(launcher, [launcher] + argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))
"""

_SHIM = """#!/bin/sh
{marker}. Written by the repacted plugin at session start, and
# rewritten only while the line above is here. It reads installed_plugins.json each time, so a
# plugin update needs no rewrite, then runs the bin/repact of that plugin with your arguments as given.
# To stop it: set PACT_REPACT_SHIM=0 in the env block of settings.json; the next session start
# deletes this file. Deleting it alone lasts until the next session start.
if ! command -v python3 >/dev/null 2>&1; then
  echo "repact: python3 is needed and is not on PATH" >&2
  exit 127
fi
{config_line}
exec python3 -I -c '{resolver}' "$config" "$@"
"""


# Claude Code's own rule for the config root, applied by the shell each time the shim runs (the
# session that wrote the shim may have had another CLAUDE_CONFIG_DIR than the terminal has).
_CONFIG_LINE = 'config="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"'


def shim_text() -> str:
    """The shim's exact bytes (as text). Raises ValueError if RESOLVER gained a single quote."""
    if "'" in RESOLVER:
        raise ValueError("RESOLVER must not contain a single quote: the shim single-quotes it")
    return _SHIM.format(marker=MARKER, config_line=_CONFIG_LINE, resolver=RESOLVER.strip("\n"))


def resolve_root(config_root: Path) -> Tuple[Optional[Path], Optional[str]]:
    """(plugin root, None) or (None, why): the very code the shim runs, run in-process."""
    namespace: Dict[str, object] = {"__name__": "repact_shim_resolver"}
    exec(compile(RESOLVER, "<repact shim resolver>", "exec"), namespace)  # noqa: S102 - our own constant
    root, why = namespace["resolve"](str(config_root))  # type: ignore[operator]
    return (Path(root), None) if root else (None, why)


def shim_path(home: Path) -> Path:
    return home / ".local" / "bin" / "repact"


def enabled(env: Mapping[str, str], config_root: Path) -> bool:
    """PACT_REPACT_SHIM from env, else from settings.json's env block (a terminal running the
    doctor has no merged settings env); unset is on; set, only 1/true/yes/on keep it on."""
    raw = env.get(OPTION)
    if raw is None:
        try:
            settings = json.loads((config_root / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = None
        block = settings.get("env") if isinstance(settings, dict) else None
        raw = block.get(OPTION) if isinstance(block, dict) else None
        if raw is None:
            return True
    return str(raw).strip().lower() in _BOOL_TRUE


def classify(path: Path) -> str:
    """'missing', 'foreign' (no MARKER, a symlink, or not a regular file), 'current' (our exact
    bytes) or 'stale' (ours, other bytes). Never follows a symlink."""
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return "foreign"
        data = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "foreign"
    if MARKER.encode() not in b"\n".join(data.split(b"\n")[:3]):
        return "foreign"
    return "current" if data == shim_text().encode() else "stale"


def on_path(bin_dir: Path, path_env: Optional[str], home: Path) -> bool:
    for entry in (path_env or "").split(os.pathsep):
        if entry and Path(os.path.expanduser(entry.replace("$HOME", str(home)))) == bin_dir:
            return True
    return False


def path_line(shell: Optional[str]) -> str:
    """The exact line that puts ~/.local/bin on PATH for the user's login shell."""
    name = os.path.basename(shell or "")
    export = "export PATH=\"$HOME/.local/bin:$PATH\""
    if name == "zsh":
        return f"add to ~/.zshrc: {export}"
    if name == "bash":
        return f"add to ~/.bashrc (~/.bash_profile on macOS): {export}"
    if name == "fish":
        return "run once in fish: fish_add_path ~/.local/bin"
    return f"add to your shell's startup file: {export}"


def _create(path: Path, data: bytes, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".repact.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o755)
        if replace:
            os.replace(tmp, path)
        else:
            os.link(tmp, path)  # FileExistsError if anything appeared there meanwhile
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def ensure_shim(home: Path, config_root: Path, env: Mapping[str, str]) -> Optional[str]:
    """Bring ~/.local/bin/repact in line; return a line for the user when something was
    created or removed, else None (unchanged, refreshed, foreign, or no install to point at).
    Raises OSError when the write itself fails."""
    path = shim_path(home)
    state = classify(path)
    if not enabled(env, config_root):
        if state in ("current", "stale"):
            path.unlink()
            return f"Removed {path}: {OPTION} is off"
        return None
    if state in ("foreign", "current"):
        return None
    if resolve_root(config_root)[0] is None:
        return None  # nothing installed to run (a --plugin-dir session): write nothing
    data = shim_text().encode()
    if state == "stale":
        _create(path, data, replace=True)
        return None
    try:
        _create(path, data, replace=False)
    except FileExistsError:
        return None
    note = f"Wrote {path}: type `repact` in a terminal to start a rePACTed session"
    if not on_path(path.parent, env.get("PATH"), home):
        note += f" ({path.parent} is not on PATH; repact-doctor shows the line to add)"
    return note


def session_started(home: Path, config_root: Path) -> bool:
    """Whether a session start has run here: its first step (shared/symlinks.py) links
    protocols/pact-plugin under <home>/.claude and <config>, just before ensure_shim runs."""
    return any(os.path.lexists(root / "protocols" / "pact-plugin")
               for root in (home / ".claude", config_root))


def doctor_lines(home: Path, config_root: Path, env: Mapping[str, str]) -> Tuple[str, str, List[str]]:
    """(state, summary, detail) for the doctor's `repact command` line. A missing shim before
    any session start is skip, not amber: a fresh install has not had the chance to write it."""
    path = shim_path(home)
    state = classify(path)
    if not enabled(env, config_root):
        return "skip", f"off ({OPTION} is not on); nothing is written to {path}", []
    if state == "foreign":
        return ("amber", f"{path} is not rePACTed's shim; it is left alone",
                ["rename or remove it to get the shim at the next session start, or set "
                 f"{OPTION}=0 in settings.json env to stop this line"])
    root, why = resolve_root(config_root)
    if root is None and state == "missing":
        return "skip", f"no installed repacted plugin to point {path} at ({why})", []
    if root is None:
        return "amber", f"`repact` cannot start: {why}", []
    if state == "missing" and not session_started(home, config_root):
        return "skip", f"{path} is written at the first session start; start one Claude Code session", []
    if state == "missing":
        return "amber", f"{path} is not written yet; start one Claude Code session", []
    if state == "stale":
        return "amber", f"{path} is out of date; the next session start rewrites it", []
    if not on_path(path.parent, env.get("PATH"), home):
        return "amber", f"{path.parent} is not on PATH; {path_line(env.get('SHELL'))}", []
    return "green", f"{path} runs {root / 'bin' / 'repact'}", []
