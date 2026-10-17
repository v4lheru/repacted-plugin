"""
Location: pact-plugin/tests/fixtures/plugin_data.py
Summary: Where the LIVE machine-local project registry (${CLAUDE_PLUGIN_DATA}/projects.json, D10 §15.3,
         the file the real atlas renders _index.md from) sits for a process this suite starts, and a
         byte snapshot of it. conftest's `_isolate_plugin_data_to_tmp` uses both: every test gets its own
         CLAUDE_PLUGIN_DATA, and the teardown fails the test if a live registry gained a path in the
         run's tmp tree (`leaked`; another session's write to the same file is not the test's).
Used by: tests/conftest.py (the autouse fixture) and tests/test_plugin_data_isolation.py (which pins
         that these paths are the ones a child process really writes).

The paths are computed ONCE, at import, from the environment the suite was started with, because
per-test fixtures later pop CLAUDE_CONFIG_DIR and patch Path.home in-process. A child a test starts
does not see that patch: it resolves the real HOME. So both the config-dir form and the HOME form are
watched, plus any CLAUDE_PLUGIN_DATA the harness exported.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Dict, Iterable, Optional, Set, Tuple

_COMMON = Path(__file__).resolve().parent.parent.parent / "bin" / "_repact_common.py"
REGISTRY_FILENAME = "projects.json"


def _data_dirname() -> str:
    """bin/_repact_common.PLUGIN_DATA_DIRNAME, read from the module itself so the name has one source."""
    name = "repact_common_for_plugin_data_guard"
    spec = importlib.util.spec_from_file_location(name, _COMMON)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module                     # its dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module.PLUGIN_DATA_DIRNAME


def live_registry_paths(environ=os.environ) -> Tuple[Path, ...]:
    """Every projects.json a process started from this environment writes when it has no
    CLAUDE_PLUGIN_DATA of its own (plus the inherited one, when the harness exported it)."""
    dirname = _data_dirname()
    roots = [Path(os.path.expanduser("~")) / ".claude"]
    if environ.get("CLAUDE_CONFIG_DIR"):
        roots.append(Path(environ["CLAUDE_CONFIG_DIR"]).expanduser())
    paths = [root / "plugins" / "data" / dirname / REGISTRY_FILENAME for root in roots]
    if environ.get("CLAUDE_PLUGIN_DATA"):
        paths.append(Path(environ["CLAUDE_PLUGIN_DATA"]).expanduser() / REGISTRY_FILENAME)
    return tuple(dict.fromkeys(paths))


def snapshot(paths: Tuple[Path, ...]) -> Dict[Path, Optional[bytes]]:
    """Each path's bytes, or None when it does not exist."""
    out: Dict[Path, Optional[bytes]] = {}
    for path in paths:
        try:
            out[path] = path.read_bytes()
        except FileNotFoundError:
            out[path] = None
    return out


def changed(before: Dict[Path, Optional[bytes]]) -> Tuple[str, ...]:
    """The paths whose bytes differ from `before` (created, removed or rewritten)."""
    after = snapshot(tuple(before))
    return tuple(str(path) for path in before if after[path] != before[path])


def _paths_in(data: bytes) -> Set[str]:
    """Every absolute path a registry holds, as a key or a string value, at any depth."""
    found: Set[str] = set()
    stack = [json.loads(data)]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            found.update(k for k in node if k.startswith("/"))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str) and node.startswith("/"):
            found.add(node)
    return found


def leaked(before: Dict[Path, Optional[bytes]], roots: Iterable[Path]) -> Tuple[str, ...]:
    """The registries that gained, since `before`, a path under one of `roots` (the run's tmp tree).

    T-1f0a70f2: the live registry is also written by other live sessions (the atlas's `set_known`,
    `repact-task init` and its bootstrap answer, `vault-note apply`), mid-run. Those writes change the
    bytes but add only real checkouts, so a byte diff alone failed whichever test was running. A
    changed registry that no longer parses is reported too: every real writer replaces it atomically.
    """
    prefixes = {str(form) for root in roots for form in (Path(root), Path(os.path.realpath(root)))}
    after = snapshot(tuple(before))
    out = []
    for path, old in before.items():
        new = after[path]
        if new == old or new is None:
            continue
        try:
            added = _paths_in(new)
        except ValueError:
            out.append(str(path))
            continue
        try:
            added -= _paths_in(old) if old is not None else set()
        except ValueError:
            pass
        if any(p == pre or p.startswith(pre.rstrip("/") + "/") for p in added for pre in prefixes):
            out.append(str(path))
    return tuple(out)


LIVE_REGISTRIES = live_registry_paths()
