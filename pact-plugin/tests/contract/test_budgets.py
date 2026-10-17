"""Contract 1, BUDGETS: every loaded surface has a byte ceiling, and so do the agent bodies and the catalog.

repacted-fork.md §8.1. The ceilings live in budgets.json: CEILINGS, not ratchets. Lower a row when a
surface shrinks; raise one only with the reason in the commit message (git keeps the history).
`retired` lists deleted surfaces: each must stay gone.

Every plugin Python module (_surfaces.python_modules) is capped at `module_cap` bytes (audit A1/D13:
modules must not grow back after the prose moved to docs/architecture/). `modules` lists the ones
already over the cap, each at its size when the cap landed: lower a row as the module shrinks.

The TARGETS (persona <= 16,000 B, catalog <= 12,000 chars, agent body <= 6,000 B, ...) live in the
build plan §9 and in tests/user_layer/test_catalog_budget.py.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.contract._surfaces import (
    AGENTS, PLUGIN_ROOT, body, combined_catalog, loaded_surfaces, python_modules, read, rel,
)

BUDGETS = json.loads((Path(__file__).resolve().parent / "budgets.json").read_text(encoding="utf-8"))


def test_every_surface_fits_its_budget():
    surfaces, bodies, catalog = BUDGETS["surfaces"], BUDGETS["bodies"], BUDGETS["catalog"]
    problems = [f"no budget row: {rel(p)}" for p in loaded_surfaces() if rel(p) not in surfaces]
    problems += [f"no body budget row: {rel(p)}" for p in sorted(AGENTS.glob("*.md"))
                 if rel(p) not in bodies]
    problems += [f"retired but present: {path}" for path in BUDGETS["retired"]
                 if (PLUGIN_ROOT / path).exists()]
    for path, ceiling in surfaces.items():
        target = PLUGIN_ROOT / path
        if not target.is_file():
            problems.append(f"{path} has a budget but does not exist: move it to `retired`")
        elif (size := target.stat().st_size) > ceiling:
            problems.append(f"{path}: {size:,} B > ceiling {ceiling:,} B")
    for path, ceiling in bodies.items():
        size = len(body(read(PLUGIN_ROOT / path)).encode("utf-8"))
        if size > ceiling:
            problems.append(f"{path} body: {size:,} B > ceiling {ceiling:,} B")
    cap, modules = BUDGETS["module_cap"], BUDGETS["modules"]
    problems += [f"{path} has a module ceiling but does not exist: drop its row"
                 for path in modules if not (PLUGIN_ROOT / path).is_file()]
    for path in python_modules():
        ceiling = modules.get(rel(path), cap)
        if (size := path.stat().st_size) > ceiling:
            problems.append(f"{rel(path)}: {size:,} B > module ceiling {ceiling:,} B. Move the rationale to "
                            "docs/architecture/ or split the module; never raise the ceiling silently")
    measured = combined_catalog()
    problems += [f"catalog {part}: {measured[part]:,} chars > ceiling {ceiling:,}"
                 for part, ceiling in catalog.items() if measured[part] > ceiling]
    assert not problems, "\n".join(problems)
