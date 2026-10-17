"""Agent-memory retriever (D9.3): the memory files' own index entries (frontmatter `name` and
`description`, which is what an index line carries), matched on distinctive words.

Location: pact-plugin/bin/_recall/agent_memory.py. Read-only.
Scope: every agent's memory dir (agent memory is cross-project by nature) plus the CURRENT
project's memory dir; other projects' memory dirs are never read. Index, satellite and archive
files are not items. The entries are cached in the plugin data dir and rebuilt when a memory
dir changes, so a lookup reads one JSON file. A file carrying a credential never enters that
index (D25): only its path is kept, in a separate withheld list.

A word counts toward a match only when it is distinctive: it names at most DISTINCTIVE_DF
items. The cutoff was set a priori (before any D9a moment was replayed), so common words such
as a project's generic vocabulary cannot fire recall on their own.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Optional

from _repact_common import credential_reason, detector_fingerprint

from . import MAX_CANDIDATES, make_candidate, tokens

INDEX_CACHE = "agent-memory-index.json"
INDEX_VERSION = 2           # 2: items carry their own key phrases (T2v2). The cache also stores the
                            # credential detector's fingerprint, so a detector change rebuilds it.
HEAD_BYTES = 2500
DISTINCTIVE_DF = 20
T2_MIN_SHARED = 2           # T2v2 (D9.4 point 6): one shared word no longer surfaces anything
KEY_FIELDS = ("keys", "wiki-topic-keys")
SKIP_PREFIXES = ("INDEX_", "ARCHIVE_", "MEMORY-")


def _front(text: str, key: str) -> str:
    match = re.search(rf"^{key}:\s*[\"']?([^\"'\n]+)", text[:HEAD_BYTES], re.M)
    return match.group(1).strip() if match else ""


def _front_list(text: str, key: str) -> list[str]:
    """A frontmatter list field, inline (`key: [a, b]`) or as a block of `- item` lines."""
    head = text[:HEAD_BYTES]
    if not head.startswith("---"):
        return []
    block = head.split("---", 2)[1] if head.count("---") >= 2 else head
    match = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", block, re.M)
    if not match:
        return []
    inline = match.group(1).strip()
    if inline.startswith("["):
        values = inline.strip("[]").split(",")
    elif inline:
        values = [inline]
    else:
        values = []
        for line in block[match.end():].splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if not stripped.startswith("- "):
                break
            values.append(stripped[2:])
    return [v.strip().strip("'\"").lower() for v in values if v.strip().strip("'\"")]


def item_key_match(item: dict, text: str) -> bool:
    """True when a whole phrase from the item's OWN key frontmatter occurs in `text` (T2v2 (c))."""
    for phrase in item.get("keys") or []:
        words = tokens(phrase)
        if words and re.search(rf"(?<![a-z0-9]){re.escape(' '.join(words))}(?![a-z0-9])", text):
            return True
    return False


def project_memory_dir(cwd: Optional[str], home: Path) -> Optional[Path]:
    """~/.claude/projects/<escaped cwd>/memory, the harness's own naming."""
    if not cwd:
        return None
    escaped = re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(cwd))
    path = home / "projects" / escaped / "memory"
    return path if path.is_dir() else None


def memory_files(home: Path, project_dir: Optional[Path]) -> list[Path]:
    files = sorted((home / "agent-memory").glob("*/*.md"))
    if project_dir:
        files += sorted(project_dir.glob("*.md"))
    return [f for f in files if f.name != "MEMORY.md" and not f.name.startswith(SKIP_PREFIXES)
            and not f.is_symlink()]


def _signature(home: Path, project_dir: Optional[Path]) -> list:
    dirs = sorted((home / "agent-memory").glob("*")) + ([project_dir] if project_dir else [])
    return [str(project_dir or ""), max((d.stat().st_mtime for d in dirs if d.is_dir()), default=0), len(dirs)]


def load_index(data_dir: Path, home: Path, project_dir: Optional[Path]) -> dict:
    """{"items": [{key, path, title, hook, words}], "withheld": [{key, path, words}]}, cached by the
    memory dirs' signature.

    D25: a file whose FULL text carries a credential never enters the index. Only its path goes
    to the separate `withheld` list, with words from its file name alone, so a lookup can still
    say "withheld: <file>" without the index ever holding its title, hook or body.
    The cache is keyed on the memory dirs' mtimes, so a new file triggers a rebuild. A file edited
    in place keeps its entry until then, but that entry holds only the text it had when it passed
    the check, and every candidate re-reads its file in full before it leaves (make_candidate).
    """
    cache = data_dir / INDEX_CACHE
    sig = _signature(home, project_dir)
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if cached.get("sig") == sig and cached.get("version") == INDEX_VERSION \
                and cached.get("detector") == detector_fingerprint():
            return cached
    except (OSError, ValueError, KeyError):
        pass
    items, withheld = [], []
    for path in memory_files(home, project_dir):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(home)).replace(os.sep, "/")
        if credential_reason(path, text):
            withheld.append({"key": f"agent-memory:{rel}", "path": str(path),
                             "words": sorted(set(tokens(path.stem.replace("_", " ").replace("-", " "))))})
            continue
        head = text[:HEAD_BYTES]
        name, desc = _front(head, "name") or path.stem, _front(head, "description")
        words = sorted(set(tokens(f"{name} {desc} {path.stem}".replace("_", " ").replace("-", " "))))
        keys = [phrase for field in KEY_FIELDS for phrase in _front_list(head, field)]
        items.append({"key": f"agent-memory:{rel}", "path": str(path), "title": name, "hook": desc,
                      "words": words, "keys": keys})
    index = {"version": INDEX_VERSION, "detector": detector_fingerprint(), "sig": sig, "items": items,
             "withheld": withheld}
    try:
        tmp = cache.with_name(f".{cache.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(index), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass
    return index


def _score(items: list[dict], terms: set[str], distinctive_only: bool, min_shared: int = 1,
           key_text: Optional[str] = None) -> list[tuple[float, dict]]:
    """Items sharing at least `min_shared` usable words with `terms`, or (when `key_text` is given)
    whose own key phrase occurs whole in it; scored by the IDF of the shared words."""
    df: dict[str, int] = {}
    for item in items:
        for word in terms.intersection(item["words"]):
            df[word] = df.get(word, 0) + 1
    usable = {t for t in terms if df.get(t) and (not distinctive_only or df[t] <= DISTINCTIVE_DF)}
    scored = []
    for item in items:
        shared = usable.intersection(item["words"])
        if len(shared) >= max(min_shared, 1) or (key_text is not None and item_key_match(item, key_text)):
            scored.append((sum(math.log(1 + len(items) / df[t]) for t in shared), item))
    return sorted(scored, key=lambda s: (-s[0], s[1]["key"]))


def _candidates(scored: list[tuple[float, dict]], k: int) -> tuple[list[dict], list[str]]:
    candidates, withheld = [], []
    for score, item in scored[:k]:
        try:
            text = Path(item["path"]).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        item_id = item["key"].split(":", 1)[1]
        cand, reason = make_candidate("agent-memory", item_id, item["title"], item["hook"] or item["title"],
                                      score, text, item["path"])
        (candidates.append(cand) if cand else withheld.append(item["key"]))
    return candidates, withheld


def _withheld_matches(index: dict, terms: set[str]) -> list[str]:
    return [entry["key"] for entry in index["withheld"] if terms.intersection(entry["words"])]


def t2(touch_words: list[str], data_dir: Path, home: Path,
       project_dir: Optional[Path]) -> tuple[list[dict], list[str]]:
    """T2v2 (D9.4 point 6): entries sharing >= T2_MIN_SHARED DISTINCTIVE words with the touched
    path or host, or whose own keys / wiki-topic-keys phrase occurs whole among its words."""
    if not touch_words:
        return [], []
    index = load_index(data_dir, home, project_dir)
    scored = _score(index["items"], set(touch_words), distinctive_only=True, min_shared=T2_MIN_SHARED,
                    key_text=" ".join(touch_words))
    candidates, withheld = _candidates(scored, MAX_CANDIDATES)
    return candidates, list(dict.fromkeys(withheld + _withheld_matches(index, set(touch_words))))


def search(query: str, data_dir: Path, home: Path, project_dir: Optional[Path],
           k: int = MAX_CANDIDATES) -> tuple[list[dict], list[str]]:
    index = load_index(data_dir, home, project_dir)
    terms = set(tokens(query))
    candidates, withheld = _candidates(_score(index["items"], terms, distinctive_only=False), k)
    return candidates, list(dict.fromkeys(withheld + _withheld_matches(index, terms)))
