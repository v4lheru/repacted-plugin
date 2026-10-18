"""Skills retriever (D9.3): T1 exact triggers from recall/triggers.json plus the recall.d
fragments of installed skill packs (packs.py), and a lexical search over core skill
descriptions for the text moments D9c adds.

Location: pact-plugin/bin/_recall/skills.py. Read-only.
A candidate points at the skill (`skill:repacted:<name>/SKILL.md`, D9a's id form, or
`skill:<pack>:<name>/SKILL.md` for a pack's) and its summary is the trigger's `why` (the pointer
names the Skill tool call; D9.3). A pack candidate also carries `pack` and `skill`, so its
pointer wraps the pack's text as data (merge.pointer_line).
"""
from __future__ import annotations

import fnmatch
import json
import math
import re
from pathlib import Path
from typing import Optional

from _repact_common import claude_config_dir

from . import BIN_DIR, MAX_CANDIDATES, make_candidate, packs, tokens

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


def all_triggers(cwd: Optional[str] = None) -> dict:
    """Core's triggers with every installed pack's added (packs.merge); core's own dict when
    no pack adds one."""
    return packs.merge(load_triggers(), claude_config_dir(), cwd)[0]


def matched_skills(cue: dict, triggers: Optional[dict] = None) -> list[tuple[str, int]]:
    """[(skill, number of patterns that matched)], strongest first. A pack's patterns see at
    most packs.SUBJECT_MAX characters of the command, host or path (packs.bounded)."""
    triggers = all_triggers() if triggers is None else triggers
    bounded, found = None, []
    for name, spec in triggers.items():
        if spec.get("pack"):
            bounded = bounded or packs.bounded(cue)
        subject = bounded if spec.get("pack") else cue
        command, path = subject["command"], subject["path"]
        hits = sum(1 for rx in spec["commands"] if command and rx.search(command))
        hits += sum(1 for rx in spec["hosts"] for host in subject["hosts"] if rx.search(host))
        hits += sum(1 for glob in spec["globs"] if path and _glob(path, glob))
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
    triggers = all_triggers() if triggers is None else triggers
    candidates, withheld = [], []
    for name, hits in matched_skills(cue, triggers)[:MAX_CANDIDATES]:
        spec = triggers[name]
        if spec.get("pack"):
            text = packs.skill_text(spec)               # None: larger than SKILL_MD_MAX, withheld
            cand, reason = (None, "too large") if text is None else make_candidate(
                "skill", f"{name}/SKILL.md", name, spec["why"], float(hits), text,
                f"{spec['pack']}/skills/{spec['skill']}/SKILL.md")
            if cand:
                cand.update(pack=spec["pack"], skill=spec["skill"])
            item = f"skill:{name}/SKILL.md"
        else:
            cand, reason = make_candidate("skill", f"repacted:{name}/SKILL.md", f"repacted:{name}",
                                          spec["why"], float(hits), skill_text(name),
                                          f"skills/{name}/SKILL.md")
            item = f"skill:repacted:{name}/SKILL.md"
        (candidates.append(cand) if cand else withheld.append(item))
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
