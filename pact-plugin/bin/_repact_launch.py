"""The launch-time decisions bin/repact delegates to Python.

Location: pact-plugin/bin/_repact_launch.py (owned by launcher; see
the design notes section 1.3). Used by: bin/repact (`preflight`, `mode`) and
hooks/shared/coexistence.py's managed-settings reader, imported from there. Stdlib and
shared/ only.

  python3 _repact_launch.py mode
      prints `installed` (a repacted@ plugin is enabled in the user settings) or `alternate`,
      and nothing else.
  python3 _repact_launch.py preflight --root <plugin root>
      prints the mode on line 1. In alternate mode it also refuses (exit 2, one message) when
      a managed settings source enables a PACT@ plugin (managed beats --settings, so PACT
      would really run beside repacted) or when the root has no .claude-plugin/plugin.json;
      PACT's install root cannot be read from installed_plugins.json (the exit trap needs it
      to point the agent links back); prints one warning line when a `claude` process already
      runs with no session activity; refuses when another alternate-mode repact holds the lock,
      or when another Claude session is live (a `claude` process outside this launcher's
      ancestry AND a teams/ or sessions/ file touched in the last 10 minutes) unless --force
      (then one warning); and prints on line 2 the
      --settings JSON that disables, for this session only, every PACT@ key the user,
      project or local settings enable (always including PACT@pact-marketplace). When the
      slim core (user-layer/CLAUDE.core.md, two levels above the plugin root) exists, the JSON
      also excludes the global <config>/CLAUDE.md with `claudeMdExcludes`, the core is staged
      as CLAUDE.md in its own cache dir, and line 3 names that dir: bin/repact passes it with
      --add-dir and sets CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1, so the session (and
      its subagents and in-process teammates) load the core as memory. The user's CLAUDE.md is
      never written, so pact keeps it byte-identical (CORE-ROUTE, measured on 2.1.283:
      17,156 -> 3,869 first-turn input tokens). With no user layer, the plugin's generic
      templates/CLAUDE.core.md is served the same way but added beside the global file, which
      is not excluded. Without any core, line 3 is empty, one warning
      is printed and the session loads the global file as before. Line 4 names the lock file
      bin/repact writes its pid to for the session (<config>/repacted-alternate.lock).
  python3 _repact_launch.py core-status --root <plugin root>
      one `core:` line for `repact --repact-status`.

No settings value is ever printed except the plugin keys in that JSON.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(Path(__file__).resolve().parents[1] / "hooks") not in sys.path:   # bin -> shared, the allowed direction
    sys.path.insert(1, str(Path(__file__).resolve().parents[1] / "hooks"))
import _agentlinks  # noqa: E402  PACT's install root, needed by the exit trap
import _memdir  # noqa: E402  resolve_root and the comm-only process check
from shared.coexistence import _read_json, legacy_keys, managed_legacy_enablement  # noqa: E402  the one managed-settings reader

INSTALLED_PREFIX = "repacted@"  # repacted@repacted-local here, repacted@repacted from GitHub
DEFAULT_LEGACY_KEY = "PACT@pact-marketplace"
EXIT_OK, EXIT_REFUSED, EXIT_USAGE = 0, 2, 64
CORE_SOURCE_OVERRIDE = "REPACTED_CORE_SOURCE"  # tests point it at a temp file (or a missing one)
CORE_DIR_OVERRIDE = "REPACTED_CORE_DIR"        # tests point it at a temp dir
TEMPLATE_CORE = Path("templates") / "CLAUDE.core.md"  # under the plugin root: the core with no user layer
LOCK_NAME = "repacted-alternate.lock"           # the running alternate-mode launcher's pid
SESSION_ACTIVITY_SECONDS = 600                  # runbook P1/F5: a live session touches these
SESSION_ACTIVITY_DIRS = ("teams", "sessions")


# ---------------------------------------------------------------------------
# Launch decisions
# ---------------------------------------------------------------------------

def launch_mode(config_root: Path) -> str:
    """`installed` when the user settings enable repacted from ANY marketplace, else `alternate`.

    A local install is repacted@repacted-local; a GitHub install is repacted@repacted (the root
    marketplace). Either one means repacted is the installed product and no swap is wanted.
    """
    plugins = _read_json(config_root / "settings.json")
    return "installed" if any(isinstance(k, str) and k.startswith(INSTALLED_PREFIX) and v is True
                              for k, v in plugins.items()) else "alternate"


def core_deny_rules(served: Path) -> List[str]:
    """Deny every editing tool over the served core dir, as written and as resolved.

    --add-dir makes that dir a workspace dir, so acceptEdits would let the session rewrite
    the core it runs on (measured in the #138 sandbox). `//` marks an absolute path.
    """
    spellings = sorted({str(served), os.path.realpath(served)})
    return [f"{tool}(/{path}/**)" for path in spellings for tool in ("Edit", "Write", "NotebookEdit")]


def disable_settings_json(config_root: Path, project_dir: Path,
                          excludes: Optional[List[str]] = None,
                          served: Optional[Path] = None) -> str:
    """--settings JSON turning off every PACT@ key enabled in user, project or local scope,
    plus `claudeMdExcludes` when the slim core replaces the global CLAUDE.md, and deny
    rules keeping the served core dir read-only."""
    files = [config_root / "settings.json", project_dir / ".claude" / "settings.json",
             project_dir / ".claude" / "settings.local.json"]
    keys = {DEFAULT_LEGACY_KEY}.union(*(legacy_keys(_read_json(path)) for path in files))
    payload = {"enabledPlugins": {key: False for key in sorted(keys)}}
    if excludes:
        payload["claudeMdExcludes"] = excludes
    if served:
        payload["permissions"] = {"deny": core_deny_rules(served)}
    return json.dumps(payload, separators=(",", ":"))


# ---------------------------------------------------------------------------
# The slim core (CORE-ROUTE): served per session, the user's file never touched
# ---------------------------------------------------------------------------

def core_source(root: Path, env: dict) -> Path:
    """The user layer's core; with no override and no user layer, the plugin's generic one."""
    override = (env.get(CORE_SOURCE_OVERRIDE) or "").strip()
    if override:
        return Path(override)
    user_core = root.parent.parent / "user-layer" / "CLAUDE.core.md"
    return user_core if user_core.is_file() else root / TEMPLATE_CORE


def core_replaces_global(root: Path, env: dict) -> bool:
    """Only the user layer's core replaces the global CLAUDE.md. The plugin's generic core is
    added beside it, so an install without a user layer keeps its own global instructions."""
    return core_source(root, env) != root / TEMPLATE_CORE


def core_dir(env: dict) -> Path:
    override = (env.get(CORE_DIR_OVERRIDE) or "").strip()
    if override:
        return Path(override)
    cache = (env.get("XDG_CACHE_HOME") or "").strip()
    return (Path(cache) if cache else Path(env.get("HOME", "~")).expanduser() / ".cache") / "repacted" / "core"


def global_memory_excludes(config_root: Path) -> List[str]:
    """The global CLAUDE.md as written and as resolved, so a symlinked config root matches."""
    path = config_root / "CLAUDE.md"
    return sorted({str(path), os.path.realpath(path)})


def stage_core(root: Path, env: dict) -> Optional[Path]:
    """Copy the core to <core dir>/CLAUDE.md (rewritten only when it differs); None if absent.

    The dir holds that one file, which is all --add-dir exposes. Never raises: a staging
    failure returns None and the session falls back to the global file.
    """
    try:
        data = core_source(root, env).read_bytes()
        target_dir = core_dir(env)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "CLAUDE.md"
        if not target.is_file() or target.read_bytes() != data:
            tmp = target_dir / ".CLAUDE.md.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, target)
        return target_dir
    except OSError:
        return None


def core_status(root: Path, env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    source = core_source(root, env)
    if not source.is_file():
        return f"core: not found at {source}; sessions load the global CLAUDE.md"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
    effect = ("the global CLAUDE.md is excluded for repact sessions" if core_replaces_global(root, env)
              else "the global CLAUDE.md still loads beside it")
    return (f"core: {source} ({source.stat().st_size} B, sha {digest}) served via --add-dir "
            f"{core_dir(env)}; {effect}")


def ancestor_pids() -> set:
    """This launcher's own ancestry (pid and ppid columns only), so a check never counts the
    process chain it runs in."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    parent = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parent[int(parts[0])] = int(parts[1])
    chain, pid = set(), os.getpid()
    while pid and pid not in chain:
        chain.add(pid)
        pid = parent.get(pid, 0)
    return chain


def session_activity(config_root: Path, now: Optional[float] = None) -> bool:
    """True when a teams/ or sessions/ file changed in the last 10 minutes: a live session,
    not an idle helper (the Desktop app or an IDE extension also runs `claude`)."""
    cutoff = (time.time() if now is None else now) - SESSION_ACTIVITY_SECONDS
    for name in SESSION_ACTIVITY_DIRS:
        for path in (config_root / name).rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime >= cutoff:
                    return True
            except OSError:
                continue
    return False


def other_alternate_launch(config_root: Path) -> Optional[int]:
    """The pid in the alternate-mode lock when that process is alive, else None."""
    try:
        pid = int((config_root / LOCK_NAME).read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def alone_check(config_root: Path, force: bool,
                detect=_memdir.running_claude_pids) -> Tuple[Optional[str], Optional[str]]:
    """(refusal, warning) for the rule that alternate mode runs alone (#253): no other
    alternate-mode repact ever (the first to exit would rename the memory dir back under the
    second), and no other live Claude session unless --force."""
    other = other_alternate_launch(config_root)
    if other is not None:
        return (f"repact: REFUSED: another alternate-mode repact is running (pid {other}); "
                "close it first (a stale lock clears itself when its pid is gone)"), None
    try:
        pids = [pid for pid in detect() if pid not in ancestor_pids()]
    except _memdir.Refusal as exc:
        return None, f"repact: warning: {exc}; proceeding"
    if not pids:
        return None, None
    listed = ", ".join(map(str, pids))
    if session_activity(config_root):
        what = f"{len(pids)} other Claude session(s) running (pid {listed})"
        if force:
            return None, f"repact: warning: --force: {what}; proceeding. Their memory dir is renamed under them."
        return f"repact: REFUSED: {what}: close other Claude sessions, or pass --force", None
    return None, (f"repact: warning: {len(pids)} other claude process(es) running (pid {listed}) "
                  "with no session activity in 10 minutes; proceeding. Never run pact beside repact.")


def running_claude_warning(detect=_memdir.running_claude_pids) -> Optional[str]:
    try:
        pids = list(detect())
    except _memdir.Refusal as exc:
        return f"repact: warning: {exc}; proceeding"
    if not pids:
        return None
    return (f"repact: warning: {len(pids)} other claude process(es) running "
            f"(pid {', '.join(map(str, pids))}); proceeding. Never run pact beside repact.")


def preflight(root: Path, env: Optional[dict] = None, project_dir: Optional[Path] = None,
              detect=_memdir.running_claude_pids, force: bool = False) -> Tuple[int, List[str], List[str]]:
    """(exit code, stdout lines, stderr lines)."""
    env = os.environ if env is None else env
    try:
        config_root, _ = _memdir.resolve_root(env)
    except _memdir.Refusal as exc:
        return EXIT_REFUSED, [], [f"repact: REFUSED: {exc}"]
    mode = launch_mode(config_root)
    if mode == "installed":
        return EXIT_OK, [mode], []
    managed = managed_legacy_enablement(env)
    if managed:
        where = ", ".join(sorted({str(source) for source, _ in managed}))
        keys = ", ".join(sorted({key for _, key in managed}))
        return EXIT_REFUSED, [], [
            f"repact: REFUSED: managed settings enable {keys} ({where}). A managed value beats "
            f"--settings, so PACT would run beside repacted; ask the administrator to remove it."]
    if not (root / ".claude-plugin" / "plugin.json").is_file():
        return EXIT_REFUSED, [], [f"repact: REFUSED: {root} has no .claude-plugin/plugin.json; "
                                  "it is not a plugin root"]
    try:
        _agentlinks.pact_install_root(config_root)
    except _memdir.Refusal as exc:
        return EXIT_REFUSED, [], [f"repact: REFUSED: {exc}. The exit trap must point the agent "
                                  "links back at PACT's root, so the launch stops here."]
    refusal, warning = alone_check(config_root, force, detect)
    if refusal:
        return EXIT_REFUSED, [], [refusal]
    warnings = [warning] if warning else []
    served = stage_core(root, env)
    if served is None:
        warnings.append(f"repact: warning: slim core not found or not stageable at "
                        f"{core_source(root, env)}; this session loads the global CLAUDE.md")
    replaces = served is not None and core_replaces_global(root, env)
    settings = disable_settings_json(config_root, project_dir or Path.cwd(),
                                     global_memory_excludes(config_root) if replaces else None,
                                     served)
    return EXIT_OK, [mode, settings, str(served) if served else "", str(config_root / LOCK_NAME)], warnings


def main(argv: Iterable[str]) -> int:
    argv = list(argv)
    if argv == ["mode"]:
        try:
            print(launch_mode(_memdir.resolve_root()[0]))
        except _memdir.Refusal as exc:
            print(f"repact: REFUSED: {exc}", file=sys.stderr)
            return EXIT_REFUSED
        return EXIT_OK
    if len(argv) == 3 and argv[:2] == ["core-status", "--root"]:
        print(core_status(Path(argv[2])))
        return EXIT_OK
    force = "--force" in argv[3:]
    if len(argv) in (3, 4) and argv[:2] == ["preflight", "--root"] and argv[3:] in ([], ["--force"]):
        code, out, err = preflight(Path(argv[2]), force=force)
        for line in err:
            print(line, file=sys.stderr)
        for line in out:
            print(line)
        return code
    print('usage: _repact_launch.py mode | preflight --root <plugin root> | '
          'core-status --root <plugin root>\n\n'
          '  python3 "_repact_launch.py" mode', file=sys.stderr)
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
