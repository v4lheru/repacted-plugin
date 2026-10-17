"""Merge (D9.3): the union of every retriever's candidates, and the pointer lines recall
would surface. The merge only orders, dedupes and caps.

Location: pact-plugin/bin/_recall/merge.py.
Pointers, never bodies: at most SURFACE_MAX lines within OUTPUT_CAP bytes, each the item's
own (already scrubbed) summary, so nothing is generated.
"""
from __future__ import annotations

from . import cut, vault

UNION_MAX = 20
SURFACE_MAX = 3
OUTPUT_CAP = 600
STORE_RANK = {"skill": 0, "pact-memory": 1, "vault": 2, "agent-memory": 3, "records": 4}


def union(*groups: list[dict]) -> list[dict]:
    """Deduped by key; each group keeps its own order; groups interleave by rank."""
    seen: dict[str, dict] = {}
    for rank in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if rank < len(group) and group[rank]["key"] not in seen:
                seen[group[rank]["key"]] = group[rank]
    return list(seen.values())[:UNION_MAX]


def pointer_line(cand: dict) -> str:
    if cand["store"] == "skill":
        name = cand["title"]
        return cut(f"recall: skill {name} — {cand['summary']} (invoke Skill({name}))", OUTPUT_CAP)
    if cand["store"] == "vault":         # KB text is surfaced as data with provenance, whole
        return f"recall: {vault.data_line(cand)}"
    where = f"{cand['anchor']} (line {cand['line']})" if cand.get("anchor") and cand.get("line") \
        else cand.get("anchor") or cand["id"]
    return cut(f"recall: {cand['store']} {where} — {cand['summary']}", OUTPUT_CAP)


def surface(candidates: list[dict], already_fired: set[str]) -> tuple[list[dict], list[str]]:
    """(surfaced candidates, their lines): the top SURFACE_MAX not yet fired this session,
    inside the byte cap."""
    chosen, lines, used = [], [], 0
    for cand in candidates:
        if cand["key"] in already_fired or len(chosen) == SURFACE_MAX:
            continue
        line = pointer_line(cand)
        if used + len(line.encode()) + 1 > OUTPUT_CAP:
            break
        chosen.append(cand)
        lines.append(line)
        used += len(line.encode()) + 1
    return chosen, lines
