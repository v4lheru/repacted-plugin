"""tests/contract/test_task_store_sole_access.py: exactly one production module builds a
path under `<config>/tasks/`.

The grep covers every `.py` under hooks/ and bin/ and every `bin/repact-*` script, for the
three spellings a path join takes: `x / "tasks"`, `os.path.join(x, "tasks", ...)` and a
`"tasks/"` or `"/tasks"` literal. The adapter (hooks/shared/task_store.py) spells the
directory once, as STORE_DIRNAME, so it never matches. DIRECT_SITES is the shrinking
allowlist of callers not yet routed through the adapter; NOT_THE_STORE names joins on a
`tasks` directory that is not the host store (the replay corpus layout), by exact line.
"""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
JOIN = re.compile(r"""(/\s*|join\((?:[^()]|\([^()]*\))*?,\s*)["']tasks["']|["'](?:[^"']*/)?tasks/[^"']*["']""")
ADAPTER = "hooks/shared/task_store.py"

# Callers still building the path themselves, with the number of lines each may hold.
# The two fast-path hooks stay, each with a tested latency budget that importing the shared
# package (37 ms measured) would break: amendment_relay runs after most tool calls and imports
# json, os and sys only (p95 < 30 ms); doctor_brief runs at every SessionStart (< 25 ms over a
# no-op hook). Each reads the store with one stat or one read of a path it builds from a
# safe-named team and id.
DIRECT_SITES: dict[str, int] = {
    "hooks/amendment_relay.py": 1,
    "hooks/doctor_brief.py": 2,
}

# Joins on a directory called `tasks` that is NOT the host store, by exact stripped line.
NOT_THE_STORE = {
    "bin/_replay.py": {
        'tasks = _mkdir(root / "tasks")',  # the corpus's own state layout
        'files = [(p.name, _read_text(p) or "") for p in sorted((root / "tasks").glob("*.json"))]',
    },
    "hooks/shell/settings.py": {
        # The guard's reader of an unresolved task-file path, not a join.
        '_TASK_FILE_TAIL = re.compile(r"/tasks/[^/]+/[^/]+\\.json$")',
    },
    "hooks/shared/rules.py": {
        # The settings.task_state refusal text naming the store, not a join.
        '"(<config dir>/tasks/) or a PACT session dir (<config dir>/pact-sessions/: the "',
    },
    "bin/_doctor.py": {
        # A list of path prefixes the doctor treats as ephemeral, not a join.
        'EPHEMERAL = ("/tmp/", "/private/", "/var/folders/", "/scratchpad/", "/.claude/tasks/", "/.claude/teams/",',
    },
}


def _production_files() -> list[Path]:
    files = [p for p in (PLUGIN_ROOT / "hooks").rglob("*.py")]
    files += [p for p in (PLUGIN_ROOT / "bin").rglob("*.py")]
    files += [p for p in (PLUGIN_ROOT / "bin").glob("repact-*") if p.is_file()]
    return sorted(p for p in files if "/tests/" not in str(p))


def _code_lines(path: Path):
    """(lineno, stripped line) of every line that is not a comment or inside a docstring."""
    in_doc = False
    for number, raw in enumerate(path.read_text("utf-8", "replace").splitlines(), 1):
        line = raw.strip()
        quotes = line.count('"""') + line.count("'''")
        if in_doc:
            if quotes % 2:
                in_doc = False
            continue
        if quotes % 2:
            in_doc = True
            continue
        if line.startswith("#") or quotes == 2:
            continue
        yield number, line.split("  #", 1)[0]


def _hits() -> dict[str, list[tuple[int, str]]]:
    hits: dict[str, list[tuple[int, str]]] = {}
    for path in _production_files():
        rel = str(path.relative_to(PLUGIN_ROOT))
        if rel == ADAPTER:
            continue
        for number, line in _code_lines(path):
            if JOIN.search(line) and line not in NOT_THE_STORE.get(rel, set()):
                hits.setdefault(rel, []).append((number, line))
    return hits


def test_only_the_adapter_and_the_allowlisted_sites_touch_the_task_store():
    hits = _hits()
    unexpected = {rel: rows for rel, rows in hits.items() if rel not in DIRECT_SITES}
    assert not unexpected, unexpected
    for rel, allowed in DIRECT_SITES.items():
        assert len(hits.get(rel, [])) <= allowed, (rel, hits.get(rel))


def test_the_allowlist_holds_no_stale_entry():
    """An entry whose site was rerouted is removed, so the list only shrinks."""
    hits = _hits()
    for rel in DIRECT_SITES:
        assert hits.get(rel), f"{rel} no longer builds a tasks path: drop it from DIRECT_SITES"


def test_the_adapter_spells_the_directory_once():
    lines = [line for _, line in _code_lines(PLUGIN_ROOT / ADAPTER)]
    assert lines.count('STORE_DIRNAME = "tasks"') == 1
    assert not [line for line in lines if JOIN.search(line) and "STORE_DIRNAME" not in line]


MIGRATION = "hooks/shared/task_migration.py"
MIGRATION_WRITER = re.compile(r"\.(create|retire|remove_created)\(\s*(?:TaskRef|ref)\b")


def test_only_the_migration_calls_the_creating_writers():
    """TaskStore.create, retire and remove_created make, rename and remove task files. They
    exist for the resume migration and for TaskStore.create_next (repact dispatch's one
    create, shared/ops/dispatch); every other caller stays with `update`."""
    callers = {
        str(path.relative_to(PLUGIN_ROOT))
        for path in _production_files()
        if any(MIGRATION_WRITER.search(line) for _, line in _code_lines(path))
    }
    assert callers == {MIGRATION, "hooks/shared/task_store.py"}
