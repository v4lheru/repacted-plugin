"""The one dispatcher's logic (D9.2, D9.3): `handle` runs one PreToolUse payload end to end
(the body of the former hooks/recall.py, folded in here by the refactor layering pass once the
hook had no registration), and `bin/recall --replay` measures exactly what `handle` does.

Location: pact-plugin/bin/_recall/dispatch.py.
- T1: a tool event matching recall/triggers.json or an installed pack's recall.d fragments
  (packs.py) -> skill pointers (deterministic, no judge). A pack conflict or skipped fragment is
  logged once a session as a `packs` record; the doctor's `packs` line shows it to the user.
- T2: the FIRST touch in a session of a path or host -> pact-memory `files` rows, vault
  key_match pages and distinctive agent-memory entries (deterministic lookups, no judge).
  One tool call is one T2 moment: the union over its first touches.
- Pulls: the agent reading a store item itself (Read of a memory file, a KB page or a SKILL.md,
  or a Skill call) is logged for D3's agreement/miss signals.
Everything here is SHADOW: it returns records; nothing is ever injected. A moment carries its
matched trigger names (T1) or its touches (T2), never the command text; the hook logs touches only
as digests.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Optional

from _repact_common import VaultScopeError, claude_config_dir, kb_root

from . import agent_memory, cues, merge, pact_memory, packs, shadow, skills, vault

T2_TOUCHES_MAX = 3
# T2v2 (D9.4 point 6): the top 3 fill in this store order, each store by score, never round-robin.
# Fixed from D9b's union grades (share of surfaced lines not graded NO): pact-memory is an exact
# identity join, agent memory 0.35, vault 0.15. None restores the old interleave (tests only).
T2_STORE_ORDER: Optional[tuple[str, ...]] = ("pact-memory", "agent-memory", "vault")


def _now_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def t1_moment(tool_name: str, tool_input: dict, cwd: Optional[str] = None) -> Optional[dict]:
    """`cwd` is the session's directory: it selects the project-scoped packs."""
    started = time.perf_counter()
    cue = cues.t1_cue(tool_name, tool_input)
    if cue is None:
        return None
    every = skills.all_triggers(cwd)
    candidates, withheld = skills.t1(cue, every)
    if not candidates and not withheld:
        return None
    triggers = [name for name, _ in skills.matched_skills(cue, every)]
    return {"type": "T1", "triggers": triggers, "candidates": candidates, "withheld": withheld, "notes": [],
            "latency_ms": _now_ms(started)}


def t1k_moment(tool_name: str, tool_input: dict, data_dir: Optional[Path],
               cwd: Optional[str] = None) -> Optional[dict]:
    """T1k (d9v3-prereg §7): T1's skill pointers first, then KB pages whose whole key phrase is in the
    tool event's own words (vault.t1k), from vault-find's cached key map."""
    started = time.perf_counter()
    cue = cues.t1_cue(tool_name, tool_input)
    if cue is None:
        return None
    every = skills.all_triggers(cwd)
    skill_cands, skill_withheld = skills.t1(cue, every)
    vault_cands, vault_withheld = vault.t1k(cue, vault.keys_map(data_dir))
    if not (skill_cands or vault_cands or skill_withheld or vault_withheld):
        return None
    triggers = [name for name, _ in skills.matched_skills(cue, every)]
    return {"type": "T1k", "triggers": triggers, "candidates": (skill_cands + vault_cands)[:merge.UNION_MAX],
            "withheld": skill_withheld + vault_withheld, "notes": [], "latency_ms": _now_ms(started)}


def t2_moment(tool_name: str, tool_input: dict, first_touch: Callable[[str], bool], data_dir: Path,
              home: Path, project_dir: Optional[Path],
              keep: Optional[Callable[[dict], bool]] = None) -> Optional[dict]:
    """`first_touch(touch)` is the ledger (True once per touch per session). `keep` filters
    candidates before the cut (replay passes the item-dated-before-the-moment rule)."""
    started = time.perf_counter()
    touches = [t for t in cues.t2_touches(tool_name, tool_input) if first_touch(t)][:T2_TOUCHES_MAX]
    if not touches:
        return None
    groups, withheld, notes = [], [], []
    for touch in touches:
        words = cues.touch_terms(touch)
        pm, pw, note = pact_memory.t2(touch)
        vc, vw = vault.t2(words, data_dir)
        ac, aw = agent_memory.t2(words, data_dir, home, project_dir)
        groups += [pm, vc, ac]
        withheld += pw + vw + aw
        if note and note not in notes and note not in ("ro", "ro-immutable"):
            notes.append(note)
    candidates = merge.union(*groups) if T2_STORE_ORDER is None else _in_store_order(groups)
    if keep is not None:
        candidates = [c for c in candidates if keep(c)]
    if not candidates and not withheld:
        return None
    return {"type": "T2", "touches": touches, "candidates": candidates, "withheld": withheld,
            "notes": notes, "latency_ms": _now_ms(started)}


def _in_store_order(groups: list[list[dict]]) -> list[dict]:
    """Every group's candidates, deduped by key, ordered by T2_STORE_ORDER and then by score."""
    seen: dict[str, dict] = {}
    for group in groups:
        for cand in group:
            seen.setdefault(cand["key"], cand)
    rank = {store: position for position, store in enumerate(T2_STORE_ORDER or ())}
    ordered = sorted(seen.values(), key=lambda c: (rank.get(c["store"], len(rank)), -c["score"], c["key"]))
    return ordered[:merge.UNION_MAX]


def pull_key(tool_name: str, tool_input: dict, home: Path, kb_root: Optional[Path],
             pack_triggers: Optional[dict] = None) -> Optional[str]:
    """The store key of an item the agent is pulling itself, or None. `pack_triggers` (the
    merged triggers) makes a Skill call to a pack skill that has a trigger a pull too."""
    if tool_name == "Skill":
        name = str(tool_input.get("skill") or tool_input.get("name") or "")
        if name.startswith("repacted:") or (pack_triggers and (pack_triggers.get(name) or {}).get("pack")):
            return f"skill:{name}/SKILL.md"
        return None
    if tool_name != "Read":
        return None
    path = os.path.realpath(str(tool_input.get("file_path") or ""))
    claude = os.path.realpath(home)
    if path.startswith(claude + os.sep) and path.endswith(".md"):
        rel = path[len(claude) + 1:]
        if rel.startswith("agent-memory/") or (rel.startswith("projects/") and "/memory/" in rel):
            return f"agent-memory:{rel}"
    if kb_root and path.startswith(os.path.realpath(kb_root) + os.sep):
        return f"vault:{path[len(os.path.realpath(kb_root)) + 1:]}"
    skills_dir = os.path.realpath(skills.SKILLS_DIR)
    if path.startswith(skills_dir + os.sep) and path.endswith(".md"):
        return f"skill:repacted:{path[len(skills_dir) + 1:]}"
    return None


# ─── one tool call end to end (the former hook body) ──────────────────────────────────────

def handle(payload: dict) -> None:
    """One PreToolUse payload: log a store pull, then T1 and T2 under the session's ledger lock
    (a busy ledger skips the call rather than race it). Shadow only: records are written,
    nothing is returned or printed. Any error propagates; the caller decides fail-open."""
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    session = str(payload.get("session_id") or "")
    tool_use_id = str(payload.get("tool_use_id") or "")
    home = claude_config_dir()
    data_dir = shadow.recall_dir()
    try:
        kb = kb_root()
    except VaultScopeError:
        kb = None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    every = skills.all_triggers(cwd) if tool_name == "Skill" else None
    pulled = pull_key(tool_name, tool_input, home, kb, every)
    if pulled:
        shadow.append({"event": "pull", "session": session, "key": pulled})
    project_dir = agent_memory.project_memory_dir(cwd, home)
    try:
        with shadow.Ledger(session) as ledger:
            _pack_note(session, ledger, home, cwd)
            _moments(tool_name, tool_input, tool_use_id, session, ledger, data_dir, home, project_dir, cwd)
    except shadow.LedgerBusy:
        return


def _pack_note(session: str, ledger, home: Path, cwd: Optional[str]) -> None:
    """A pack conflict or skipped fragment, logged once a session (per distinct report)."""
    report = packs.merge(skills.load_triggers(), home, cwd)[1]
    lines = report.lines()
    if lines and ledger.first_touch("packs:" + "\n".join(lines)):
        shadow.append({"event": "packs", "session": session, "packs": report.packs, "notes": lines[:8]})


def _moments(tool_name, tool_input, tool_use_id, session, ledger, data_dir, home, project_dir,
             cwd=None) -> None:
    """T1 and T2 for one tool call, under the session's ledger lock."""
    moments = [t1_moment(tool_name, tool_input, cwd),
               t2_moment(tool_name, tool_input, ledger.first_touch, data_dir, home, project_dir)]
    for moment in filter(None, moments):
        chosen, lines = merge.surface(moment["candidates"], ledger.fired)
        capped = ledger.capped
        if not capped:
            ledger.record_fired([c["key"] for c in chosen], len(lines))
        shadow.append({"event": "moment", "session": session, "type": moment["type"], "tool": tool_name,
                       "tool_use_id": tool_use_id, "triggers": moment.get("triggers", []),
                       "touch_digests": [shadow.digest(t) for t in moment.get("touches", [])],
                       "candidates": moment["candidates"],
                       "would_inject": [] if capped else lines,
                       "would_inject_keys": [] if capped else [c["key"] for c in chosen],
                       "withheld": len(moment["withheld"]), "notes": moment["notes"], "capped": capped,
                       "latency_ms": moment["latency_ms"]})
    ledger.save()
