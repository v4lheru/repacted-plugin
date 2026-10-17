"""Vault retriever (D9.3, D9.7): the KB folder only, through vault-find, never its own ranking.

Location: pact-plugin/bin/_recall/vault.py. Read-only.
- `search()` runs `vault-find --json` as a subprocess (its ranking unchanged; D9.7(2)); a hit's
  best-lesson anchor (KB-REACH) becomes the candidate's `anchor` and `line`, for the pointer text.
- `t2()` looks a touch up in a cached copy of `vault-find --keys-map` (whole `wiki-topic-keys`
  phrases, D9.7(3)), so T2 stays a dictionary lookup inside D9.5's budget. The cache is
  rebuilt when vault-find's own index file changes.
vault-find already applies the scope pin (D26) and withholds credential notes (D25); every
candidate here still passes the S3 gate on the text vault-find returned.
Superseded pages stay candidates, undemoted: D9.7 allows only a demotion, behind the P4 gate.
A KB line is surfaced as DATA (data_line): one <recalled-lesson> element with its provenance
(source, origin) in attributes and the text escaped, so a lesson can neither close the element
nor read as an instruction to the agent. Lessons written by repact-distill carry the provenance
ctx `distill` (title tag "(from distill, <date>)") and are marked origin="distill". bin/_distill/sources.py strips
these elements from every note it reads, so recalled text never comes back as a new source.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from . import BIN_DIR, GENERIC_PATH_PARTS, MAX_CANDIDATES, TITLE_MAX, cut, make_candidate, tokens
from _kb import datawrap as W            # bin/ is on sys.path (_recall/__init__.py)

VAULT_FIND = BIN_DIR / "vault-find"
DATA_TAG, DATA_USE = W.DATA_TAG, W.DATA_USE      # re-exported: the shared wrapper's names
DISTILL_CTX = W.DISTILL          # the provenance ctx repact-distill gives its lessons
SEARCH_TIMEOUT = 5.0
KEYS_CACHE = "vault-keys.json"


def _run_vault_find(args: list[str], timeout: float = SEARCH_TIMEOUT) -> dict:
    """vault-find's JSON output, or {} on any failure. With the KB off (no paths.kb) or
    misconfigured it returns {} at once: no subprocess, no output (the KB is silent then)."""
    from _repact_common import VaultScopeError, vault_scope_root
    try:
        vault_scope_root()
    except VaultScopeError:
        return {}
    try:
        run = subprocess.run([sys.executable, str(VAULT_FIND), *args], capture_output=True,
                             text=True, timeout=timeout)
        return json.loads(run.stdout) if run.returncode in (0, 1) and run.stdout.strip() else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def search(query: str, k: int = MAX_CANDIDATES) -> tuple[list[dict], list[str]]:
    """(candidates, withheld keys) from vault-find's JSON; fail-open to nothing."""
    found = _run_vault_find(["--k", str(k), "--json", "--", query])
    candidates, withheld = [], []
    for hit in found.get("hits") or []:
        lesson = hit.get("lesson") or {}
        text = f"{hit.get('title', '')}\n{hit.get('summary', '')}\n{lesson.get('title', '')}"
        cand, reason = make_candidate("vault", hit["relpath"], hit.get("title") or hit["relpath"],
                                      hit.get("summary", ""), float(hit.get("score", 0)), text,
                                      f"vault/{hit['relpath']}")
        if cand:
            cand["key_match"] = bool(hit.get("key_match"))
            if lesson.get("anchor"):        # KB-REACH: the pointer names the lesson, not the whole page
                cand["anchor"] = cut(lesson["anchor"], TITLE_MAX)
                cand["line"] = int(lesson.get("line") or 0)
            if DISTILL_CTX in (lesson.get("ctx"), hit.get("origin")):
                cand["origin"] = DISTILL_CTX
            candidates.append(cand)
        else:
            withheld.append(f"vault:{hit['relpath']}")
    withheld += [f"vault:{w['path']}" for w in found.get("withheld") or []]
    return candidates, withheld


def data_line(cand: dict) -> str:
    """The candidate as one data element: provenance in attributes, the (already scrubbed and cut)
    text escaped, so nothing inside can close the element or forge another one."""
    where = cand.get("anchor") or cand["id"]
    if cand.get("anchor") and cand.get("line"):
        where = f"{where} (line {cand['line']})"
    origin = "distill" if cand.get("origin") == DISTILL_CTX else "kb"
    return W.element(f"vault/{cand['id']}", where, origin, cand.get("summary", ""))


def _index_file() -> Path:
    from _repact_common import plugin_data_dir
    return plugin_data_dir() / "vault-index.json"


def keys_map(data_dir: Path) -> dict:
    """The cached key map; rebuilt when vault-find's index file changed (mtime, size)."""
    cache = data_dir / KEYS_CACHE
    try:
        st = _index_file().stat()
        sig = [st.st_mtime, st.st_size]
    except OSError:
        sig = None
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if sig is not None and cached.get("sig") == sig:
            return cached
    except (OSError, ValueError):
        pass
    fresh = _run_vault_find(["--keys-map"], timeout=30.0)
    if not fresh:
        return {"keys": {}, "summaries": {}}
    try:
        st = _index_file().stat()
        fresh["sig"] = [st.st_mtime, st.st_size]
        tmp = cache.with_name(f".{cache.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(fresh), encoding="utf-8")
        os.replace(tmp, cache)
    except OSError:
        pass
    return fresh


# T1k (docs/test/d9v3-prereg.md §3.1): a whole key phrase in the tool event's OWN words. Paths are T2's
# cue, so every path-like token (anything with '/' or '~', or a file name with an extension) is removed
# first; a one-word key must name at most T1K_DISTINCT_MAX pages (T2v2's distinctive-word bound).
T1K_PATHLIKE = re.compile(r"\S*[/~]\S*|\S+\.[A-Za-z0-9]{1,6}\b")
T1K_DISTINCT_MAX = 20


def t1k_event_words(cue: dict) -> list[str]:
    command = T1K_PATHLIKE.sub(" ", cue.get("command") or "")
    words = tokens(command + " " + " ".join(cue.get("hosts") or []))
    return [w for w in dict.fromkeys(words) if w not in GENERIC_PATH_PARTS]


def t1k(cue: dict, mapping: dict) -> tuple[list[dict], list[str]]:
    """KB pages whose whole key phrase occurs in the tool event's own words, by summed key-word count."""
    text = " ".join(t1k_event_words(cue))
    if not text:
        return [], []
    scored: dict[str, float] = {}
    for key, pages in (mapping.get("keys") or {}).items():
        key_words = tokens(key)
        if not key_words or (len(key_words) < 2 and len(pages) > T1K_DISTINCT_MAX):
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(' '.join(key_words))}(?![a-z0-9])", text):
            for relpath in pages:
                scored[relpath] = scored.get(relpath, 0.0) + len(key_words)
    candidates, withheld = [], []
    for relpath, score in sorted(scored.items(), key=lambda item: (-item[1], item[0]))[:MAX_CANDIDATES]:
        summary = (mapping.get("summaries") or {}).get(relpath, "")
        cand, reason = make_candidate("vault", relpath, relpath, summary, score, summary, f"vault/{relpath}")
        (candidates.append(cand) if cand else withheld.append(f"vault:{relpath}"))
    return candidates, withheld


def t2(touch_words: list[str], data_dir: Path) -> tuple[list[dict], list[str]]:
    """Pages whose whole key phrase occurs in the touch's words (the key_match rule)."""
    if not touch_words:
        return [], []
    text = " ".join(touch_words)
    mapping = keys_map(data_dir)
    scored: dict[str, float] = {}
    for key, pages in (mapping.get("keys") or {}).items():
        key_words = tokens(key)
        if key_words and re.search(rf"(?<![a-z0-9]){re.escape(' '.join(key_words))}(?![a-z0-9])", text):
            for relpath in pages:
                scored[relpath] = scored.get(relpath, 0.0) + len(key_words)
    candidates, withheld = [], []
    for relpath, score in sorted(scored.items(), key=lambda item: (-item[1], item[0]))[:MAX_CANDIDATES]:
        summary = (mapping.get("summaries") or {}).get(relpath, "")
        cand, reason = make_candidate("vault", relpath, relpath, summary, score, summary,
                                      f"vault/{relpath}")
        (candidates.append(cand) if cand else withheld.append(f"vault:{relpath}"))
    return candidates, withheld
