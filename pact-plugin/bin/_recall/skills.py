"""Skills retriever (D9.3): T1 exact triggers from recall/triggers.json, and a lexical search
over skill descriptions for the text moments D9c adds.

Location: pact-plugin/bin/_recall/skills.py. Read-only.
A candidate points at the skill (`skill:repacted:<name>/SKILL.md`, D9a's id form) and its
summary is the trigger's `why` (the pointer names the Skill tool call; D9.3).
"""
from __future__ import annotations

import fnmatch
import json
import math
import re
from pathlib import Path
from typing import Optional

from . import BIN_DIR, MAX_CANDIDATES, make_candidate, tokens

PLUGIN_ROOT = BIN_DIR.parent
TRIGGERS = PLUGIN_ROOT / "recall" / "triggers.json"
SKILLS_DIR = PLUGIN_ROOT / "skills"
_cache: dict = {}


def load_triggers(path: Optional[Path] = None) -> dict:
    """{skill: {why, commands[re], hosts[re], globs[]}}, cached by mtime; {} when unreadable."""
    path = path or TRIGGERS
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _cache.get("sig") != (str(path), mtime):
        try:
            raw = json.loads(path.read_text(encoding="utf-8")).get("skills") or {}
            compiled = {name: {"why": spec.get("why", ""),
                               "commands": [re.compile(p) for p in spec.get("commands") or []],
                               "hosts": [re.compile(p) for p in spec.get("hosts") or []],
                               "globs": list(spec.get("globs") or [])}
                        for name, spec in raw.items()}
        except (OSError, ValueError, re.error):
            compiled = {}
        _cache.update(sig=(str(path), mtime), triggers=compiled)
    return _cache["triggers"]


def matched_skills(cue: dict, triggers: Optional[dict] = None) -> list[tuple[str, int]]:
    """[(skill, number of patterns that matched)], strongest first."""
    triggers = load_triggers() if triggers is None else triggers
    found = []
    for name, spec in triggers.items():
        hits = sum(1 for rx in spec["commands"] if cue["command"] and rx.search(cue["command"]))
        hits += sum(1 for rx in spec["hosts"] for host in cue["hosts"] if rx.search(host))
        hits += sum(1 for glob in spec["globs"] if cue["path"] and _glob(cue["path"], glob))
        if hits:
            found.append((name, hits))
    return sorted(found, key=lambda item: (-item[1], item[0]))


def _glob(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.replace("**/", "*/"))


def skill_text(name: str) -> str:
    try:
        return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def t1(cue: dict, triggers: Optional[dict] = None) -> tuple[list[dict], list[str]]:
    """(candidates, withheld keys) for a T1 tool event. Deterministic: no judge (D9.3)."""
    triggers = load_triggers() if triggers is None else triggers
    candidates, withheld = [], []
    for name, hits in matched_skills(cue, triggers)[:MAX_CANDIDATES]:
        cand, reason = make_candidate("skill", f"repacted:{name}/SKILL.md", f"repacted:{name}",
                                      triggers[name]["why"], float(hits), skill_text(name),
                                      f"skills/{name}/SKILL.md")
        (candidates.append(cand) if cand else withheld.append(f"skill:repacted:{name}/SKILL.md"))
    return candidates, withheld


def search(query: str, k: int = MAX_CANDIDATES) -> tuple[list[dict], list[str]]:
    """Lexical search over each skill's name and description (for D9c's text moments)."""
    docs = {}
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^description:\s*(.+)$", text, re.M)
        docs[skill_md.parent.name] = (match.group(1).strip("'\" |") if match else "", text)
    terms = set(tokens(query))
    if not terms or not docs:
        return [], []
    df = {t: sum(1 for d, _ in docs.values() if t in set(tokens(d))) for t in terms}
    scored = []
    for name, (desc, text) in docs.items():
        words = set(tokens(name.replace("-", " ") + " " + desc))
        score = sum(math.log(1 + len(docs) / (1 + df[t])) for t in terms & words)
        if score > 0:
            scored.append((score, name, desc, text))
    candidates, withheld = [], []
    for score, name, desc, text in sorted(scored, key=lambda s: (-s[0], s[1]))[:k]:
        cand, reason = make_candidate("skill", f"repacted:{name}/SKILL.md", f"repacted:{name}", desc,
                                      score, text, f"skills/{name}/SKILL.md")
        (candidates.append(cand) if cand else withheld.append(f"skill:repacted:{name}/SKILL.md"))
    return candidates, withheld
