"""The KB catalog: one machine-readable table of every KB page and lesson (KB-CATALOG;
the design notes section 6, the "control matrix").

Location: pact-plugin/bin/_kb/catalog.py. Built by bin/vault-find's refresh pass (one
walker, one parser, two outputs) and read by `vault-find --catalog` and the doctor's `kb` check.
Stdlib only.

What it writes, beside vault-index.json in ${CLAUDE_PLUGIN_DATA}:
  - kb-catalog.json: `pages` (section 6.2 fields; the type is derived per section 2, never
    stored in a page), `graph` (orphans, supersession chains, clusters by project), `health`
    (section 6.4) and `source_sig`. It is generated one way and never hand-edited; the pages stay
    the only source of truth. The first key, `build_key`, lets a refresh skip an unchanged build
    by reading 128 bytes.
  - kb-catalog.cache.json: the per-page parse, keyed by (mtime, size), so touching one page
    re-parses only that page. The link graph, chains and health are recomputed from it on every
    build, which makes a warm build and a cold build byte-identical.

D25: a page the index withholds keeps its path, reason, type and dates here, and nothing else.
Its title, summary, keys, lessons and links are never read into either file, and a date is copied
only when it IS a date. D26: every page comes from vault-find's own walk of the KB root, and a
re-read is checked again against symlinks and the root.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from _kb import lessons as kb_lessons
from _repact_common import credential_reason, is_within, kb_index_page, scrub_text

CATALOG_NAME = "kb-catalog.json"
CACHE_NAME = "kb-catalog.cache.json"
CATALOG_VERSION = 2              # 2: wiki-summary kept whole (1 capped it at TEXT_MAX)
CONTRACT_DATE = "2026-09-28"    # a page CREATED on or after it is held to the contract (lead ruling;
                                # touching a legacy page, as the curation apply does, keeps it legacy)
ARCHIVE_DIR = "_Archive"
PROJECTS_FILE = Path("_kb") / "projects.json"
TEXT_MAX = 200
VIEWS = ("health", "withheld", "broken", "orphans", "chains", "lint", "json")
LESSON_TYPES = ("topic", "collection")
WIKI_STATUSES = ("current", "dated", "superseded", "archived")
DATE_FIELDS = (("created", "created"), ("modified", "modified"), ("first_seen", "wiki-first-seen"),
               ("last_reviewed", "wiki-last-reviewed"), ("status_since", "wiki-status-since"))
ATTACHMENTS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "pdf", "mp4", "mov", "canvas", "excalidraw",
               "csv", "json", "txt", "zip", "html"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SERIES_PART = re.compile(r"-\d+$")
WIKILINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
INLINE_CODE = re.compile(r"`[^`\n]*`")
HEAD = re.compile(rb'^\{"build_key":"([0-9a-f]{64})"')

_VAULT_NOTE = None


def _vault_note():
    """bin/vault-note as a module (page_type, check_page, and its vault-find helpers). Loaded
    only when a page must be parsed, so an unchanged refresh never pays for it."""
    global _VAULT_NOTE
    if _VAULT_NOTE is None:
        path = Path(__file__).resolve().parent.parent / "vault-note"
        loader = importlib.machinery.SourceFileLoader("_kb_vault_note", str(path))
        spec = importlib.util.spec_from_loader("_kb_vault_note", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        _VAULT_NOTE = module
    return _VAULT_NOTE


# ---------------------------------------------------------------------------
# Per-page parse (cached by signature)
# ---------------------------------------------------------------------------

def _cap(text: str) -> str:
    return text if len(text) <= TEXT_MAX else text[: TEXT_MAX - 1].rstrip() + "…"


def _one_line(text) -> str:
    return " ".join(str(text or "").split())


def _clean(text) -> str:
    """One line, scrubbed BEFORE it is cut, capped."""
    return _cap(scrub_text(_one_line(text)))


def _scrub_all(items: List[str]) -> List[str]:
    """scrub_text over many one-line strings in ONE scrub pass (a pass per string cost more than the
    rest of the catalog). A redaction that swallowed a line break would shift the lines, so a
    count mismatch falls back to one pass per string."""
    lines = [_one_line(item) for item in items]
    if not lines:
        return []
    scrubbed = scrub_text("\n".join(lines)).split("\n")
    if len(scrubbed) != len(lines):
        scrubbed = [scrub_text(line) for line in lines]
    return scrubbed


def _date(value) -> Optional[str]:
    return value if isinstance(value, str) and DATE.match(value) else None


def _dates(meta: dict) -> dict:
    return {field: _date(meta.get(key)) for field, key in DATE_FIELDS}


def normalise_target(raw: str) -> Optional[str]:
    """The page a `[[target|alias]]` or `[[target#heading]]` names, or None for a same-page
    heading link or an attachment."""
    target = raw.split("|", 1)[0].split("#", 1)[0].strip().rstrip("\\").strip()
    if not target:
        return None
    if target.lower().endswith(".md"):
        return target[:-3]
    suffix = target.rsplit("/", 1)[-1].rsplit(".", 1)
    return None if len(suffix) == 2 and suffix[1].lower() in ATTACHMENTS else target


def link_name(value) -> Optional[str]:
    """The page a frontmatter link value names (`[[stem]]`, quoted or parsed as a list)."""
    items = value if isinstance(value, list) else [value] if value else []
    text = " ".join(str(item) for item in items).strip().strip("'\"")
    match = WIKILINK.search(text)
    return normalise_target(match.group(1) if match else text.strip("[] "))


def body_links(body: str) -> List[str]:
    """Wikilink targets in the body; fenced code and inline code are not links."""
    targets, fence = set(), None
    for line in body.split("\n"):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else fence or marker
            continue
        if fence or "[[" not in line:
            continue
        for match in WIKILINK.finditer(INLINE_CODE.sub("", line)):
            target = normalise_target(match.group(1))
            if target:
                targets.add(target)
    return sorted(targets)


def page_kind(relpath: str) -> Tuple[str, bool]:
    """(section 2 type, archived). An archived page keeps the type its path would have had."""
    parts = relpath.split("/")
    archived = ARCHIVE_DIR in parts[:-1]
    kind = _vault_note().page_type("/".join(p for p in parts if p != ARCHIVE_DIR))
    return ("collection" if kind in ("control", "archived") else kind), archived


def _origin(kind: str, stem: str, meta: dict) -> Optional[str]:
    if kind != "collection":
        return None
    if SERIES_PART.search(stem):
        return "series-part"
    return "series-base" if meta.get("wiki-source") == "auto" else "hand"


def _reached(text: str, found: list, body_chars: Optional[int]) -> int:
    """Lessons whose first indexed line starts inside vault-find's body window: the non-heading
    body lines, joined, cut at body_chars (section 8.1 U-reach; the measured definition)."""
    if body_chars is None:
        return len(found)
    lines = text.split("\n")
    offsets, position = {}, 0
    for number in range(kb_lessons._body_start(lines), len(lines)):
        if not lines[number].startswith("#"):
            offsets[number + 1] = position
            position += len(lines[number]) + 1
    count = 0
    for lesson in found:
        first = next((n for n in range(lesson.start, lesson.end + 1) if n in offsets), None)
        count += first is not None and offsets[first] < body_chars
    return count


def parse_page(text: str, relpath: str, kind: str, archived: bool, body_chars: Optional[int]) -> dict:
    """The cached parse of one readable (not withheld) page."""
    vn = _vault_note()
    meta, body = vn.VF.split_frontmatter(text)
    title = next((line[2:] for line in body.split("\n") if line.startswith("# ")), "")
    status = meta.get("wiki-status")
    found = kb_lessons.lessons(text, relpath) if kind in LESSON_TYPES else []
    lint = None
    if kind in LESSON_TYPES and not archived:
        errors, warnings = vn.check_page(text, relpath)
        lint = [len(errors), len(warnings)]
    keys = [k.lower() for k in vn.VF.as_list(meta.get("wiki-topic-keys")) if k.strip()]
    text_of = lambda key: meta.get(key) if isinstance(meta.get(key), str) else ""
    # Section 6.2 scrubs the page's title, summary and keys. A lesson's title and provenance are
    # only normalised: the page passed credential_reason over its WHOLE text (else it would be a
    # withheld stub), the same guarantee the index relies on for the text it stores.
    # The summary is kept WHOLE: section 6.5 compares it to the MOC line, and its length rule is
    # vault-note check's alone (the lint above), never a cap of the catalog's own.
    clean = iter(_scrub_all([title, text_of("wiki-summary"), text_of("status")] + keys))
    title, summary, legacy_status = _cap(next(clean)), next(clean), _cap(next(clean))
    keys = [_cap(next(clean)) for _ in keys]
    titles = [_cap(_one_line(lesson.title)) for lesson in found]
    contexts = [_cap(_one_line(lesson.ctx)) or None for lesson in found]
    return {
        "type": kind, "archived": archived, "origin": _origin(kind, Path(relpath).stem, meta),
        "title": title, "summary": summary, "status": legacy_status,
        "wiki_status": status if status in WIKI_STATUSES else ("archived" if archived else "current"),
        "superseded_by": link_name(meta.get("wiki-superseded-by")),
        **_dates(meta),
        "keys": keys,
        "declared_projects": sorted({p for p in vn.VF.as_list(meta.get("wiki-projects")) if SLUG.match(p)}),
        "split_from": [n for n in (link_name(v) for v in vn.VF.as_list(meta.get("wiki-split-from"))) if n],
        "split_into": [n for n in (link_name(v) for v in vn.VF.as_list(meta.get("wiki-split-into"))) if n],
        "words": len(body.split()),
        "targets": body_links(body),
        "lessons": [{"id": l.id, "title": t, "state": l.state, "flags": list(l.flags), "ctx": c, "date": l.date}
                    for l, t, c in zip(found, titles, contexts)],
        "reached": _reached(text, found, body_chars),
        "lint": lint,
        "new": (_date(meta.get("created")) or "") >= CONTRACT_DATE,
    }


def withheld_stub(text: str, kind: str, archived: bool) -> dict:
    """D25: the dates only. Nothing else in the text is kept."""
    meta, _ = _vault_note().VF.split_frontmatter(text)
    return {"type": kind, "archived": archived, **_dates(meta)}


# ---------------------------------------------------------------------------
# Assembly: links, chains, clusters, health (recomputed on every build)
# ---------------------------------------------------------------------------

class Resolver:
    """Obsidian-style: a path-qualified target by its path, otherwise by stem; a live page
    wins over an archived one with the same stem."""

    def __init__(self, parsed: Dict[str, dict]):
        self.paths = {rel[:-3].lower(): rel for rel in parsed}
        stems: Dict[str, List[str]] = defaultdict(list)
        for rel in sorted(parsed, key=lambda r: (parsed[r]["page"].get("archived", False), r)):
            stems[Path(rel).stem.lower()].append(rel)
        self.stems = stems

    def __call__(self, target: Optional[str]) -> Optional[str]:
        if not target:
            return None
        key = target.lower().lstrip("/")
        if "/" in key and key in self.paths:
            return self.paths[key]
        found = self.stems.get(key.rsplit("/", 1)[-1])
        return found[0] if found else None


def _chain(start: str, pages: dict, resolve: Resolver) -> dict:
    path, state, end = [start], "broken", None
    current = start
    while True:
        successor = pages[current].get("superseded_by")
        if not successor:
            if pages[current].get("wiki_status") != "superseded":
                state, end = "ok", current
            break
        target = resolve(successor)
        if target is None or pages[target].get("archived"):
            break
        if target in path:
            state = "cycle"
            break
        path.append(target)
        if pages[target].get("withheld"):
            state, end = "ok", target
            break
        current = target
    return {"from": start, "path": path, "end": end, "state": state}


def _project(ctx: Optional[str], projects: dict) -> Optional[str]:
    if not ctx:
        return None
    return projects.get(ctx, projects.get(ctx.lower()))


def assemble(parsed: Dict[str, dict], projects: dict, projects_state: str, source_sig: str,
             build_key: str) -> dict:
    resolve = Resolver(parsed)
    pages: Dict[str, dict] = {}
    links_in: Dict[str, set] = defaultdict(set)
    for rel, cached in sorted(parsed.items()):
        page = cached["page"]
        if cached["withheld"]:
            pages[rel] = {**page, "withheld": cached["withheld"]}
            continue
        out, broken = set(), set()
        for target in page["targets"]:
            found = resolve(target)
            if found is None:
                broken.add(target)
            elif found != rel:
                out.add(found)
        for target in out:
            links_in[target].add(rel)
        lessons = [{**lesson, "project": _project(lesson["ctx"], projects)} for lesson in page["lessons"]]
        derived = {lesson["project"] for lesson in lessons if lesson["project"]}
        entry = {key: value for key, value in page.items()
                 if key not in ("targets", "declared_projects", "reached", "new", "lessons")}
        pages[rel] = {**entry, "withheld": None, "lessons": lessons, "links_out": sorted(out),
                      "broken_links": sorted(_clean(t) for t in broken), "chain_end": None,
                      "projects": sorted(derived | set(page["declared_projects"]))}
    for rel, entry in pages.items():
        if not entry["withheld"]:
            entry["links_in"] = sorted(links_in.get(rel, ()))
    chains = [_chain(rel, pages, resolve) for rel in sorted(pages)
              if not pages[rel]["withheld"] and (pages[rel].get("superseded_by")
                                                  or pages[rel].get("wiki_status") == "superseded")]
    for chain in chains:
        pages[chain["from"]]["chain_end"] = chain["end"] if chain["state"] == "ok" else None
    live = [rel for rel, e in sorted(pages.items()) if e["type"] in LESSON_TYPES and not e["archived"]]
    moc_name = kb_index_page()
    orphans = [rel for rel in live if not (links_in.get(rel, set()) - {moc_name, rel})]
    clusters: Dict[str, List[str]] = defaultdict(list)
    for rel in live:
        for slug in pages[rel].get("projects") or []:
            clusters[slug].append(rel)
    graph = {"orphans": orphans, "chains": chains, "clusters_by_project": dict(sorted(clusters.items()))}
    return {"build_key": build_key, "version": CATALOG_VERSION, "source_sig": source_sig, "pages": pages,
            "graph": graph, "health": health(pages, parsed, live, graph, projects_state)}


def health(pages: dict, parsed: dict, live: List[str], graph: dict, projects_state: str) -> dict:
    """Section 6.4's metrics, as counts (and the withheld paths the user acts on)."""
    types = Counter(entry["type"] for entry in pages.values())
    readable = [rel for rel in live if not pages[rel]["withheld"]]
    lessons = [lesson for rel in readable for lesson in pages[rel]["lessons"]]
    states = Counter(lesson["state"] for lesson in lessons)
    flags = Counter(flag for lesson in lessons for flag in lesson["flags"])
    new_errors = [(rel, pages[rel]["lint"][0]) for rel in readable
                  if pages[rel]["lint"] and pages[rel]["lint"][0] and parsed[rel]["page"]["new"]]
    candidates = [rel for rel in readable if pages[rel]["lessons"] and 2 * sum(
        lesson["state"] in ("dated", "superseded") for lesson in pages[rel]["lessons"]) > len(pages[rel]["lessons"])]
    withheld = sorted(rel for rel, entry in pages.items() if entry["withheld"])
    broken = {rel: len(pages[rel]["broken_links"]) for rel in readable if pages[rel]["broken_links"]}
    chains = graph["chains"]
    return {
        "pages": {**{kind: types.get(kind, 0) for kind in ("topic", "collection", "project", "index")},
                  "archived": sum(1 for entry in pages.values() if entry["archived"]), "total": len(pages)},
        "lessons": {"total": len(lessons), **{s: states.get(s, 0) for s in ("current", "dated", "superseded")},
                    **{f: flags.get(f, 0) for f in ("conflicts", "unverified")}},
        "withheld": {"count": len(withheld), "paths": withheld},
        "broken_links": {"count": sum(broken.values()), "pages": len(broken)},
        "chains": {"count": len(chains), "broken": sum(c["state"] == "broken" for c in chains),
                   "cyclic": sum(c["state"] == "cycle" for c in chains)},
        "orphans": len(graph["orphans"]),
        "key_coverage": {"with_keys": sum(1 for rel in readable if pages[rel]["keys"]), "pages": len(live)},
        "lesson_reach": {"reached": sum(parsed[rel]["page"]["reached"] for rel in readable),
                         "lessons": len(lessons)},
        "schema": {"new_pages_with_errors": len(new_errors), "new_errors": sum(n for _, n in new_errors),
                   "legacy_errors": sum(pages[rel]["lint"][0] for rel in readable if pages[rel]["lint"]
                                        and not parsed[rel]["page"]["new"]),
                   "warnings": sum(pages[rel]["lint"][1] for rel in readable if pages[rel]["lint"])},
        "curation_candidates": len(candidates),
        "projects_map": projects_state,
    }


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def _sig(path: Path) -> Optional[list]:
    try:
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            return [stat.st_mtime, stat.st_size]
    except OSError:
        pass
    return None


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def _head(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            match = HEAD.match(handle.read(128))
    except OSError:
        return None
    return match.group(1).decode() if match else None


def _read(vault: Path, relpath: str) -> Optional[str]:
    path = vault / relpath
    if path.is_symlink() or not is_within(path, vault):
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def load_projects(vault: Path) -> Tuple[dict, str]:
    """({provenance context: slug or None}, "absent" | "ok" | "invalid") from _kb/projects.json."""
    path = vault / PROJECTS_FILE
    if _sig(path) is None or not is_within(path, vault):
        return {}, "absent"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "invalid"
    if not isinstance(raw, dict):
        return {}, "invalid"
    # The live file nests the map under "contexts" beside its own metadata (_doc, version,
    # confirmed); a flat {context: slug} file is still read as it is.
    if isinstance(raw.get("contexts"), dict):
        raw = raw["contexts"]
    return {str(k): v for k, v in raw.items() if v is None or (isinstance(v, str) and SLUG.match(v))}, "ok"


def _load_cache(path: Path, vault: Path, body_chars: Optional[int]) -> dict:
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if cache.get("version") != CATALOG_VERSION or cache.get("vault") != str(vault) \
            or cache.get("body_chars") != body_chars:
        return {}
    return cache.get("pages") or {}


def _write(path: Path, value: dict) -> None:
    """Atomic replace; sorted keys and no clock, so the same pages give the same bytes."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    os.replace(tmp, path)


def refresh(vault: Path, catalog_path: Path, index: dict, texts: Optional[Dict[str, str]] = None,
            body_chars: Optional[int] = None, exclude: bool = True) -> bool:
    """Bring the catalog up to date with the index vault-find just refreshed. Returns whether it
    was rebuilt. `texts` holds pages the refresh pass read (so they are not read twice)."""
    vault, texts = Path(vault), texts or {}
    entries = {rel: (page["sig"], None) for rel, page in index.get("pages", {}).items()}
    entries.update({rel: (entry["sig"], entry["reason"]) for rel, entry in (index.get("withheld") or {}).items()})
    moc_sig, moc_name = index.get("moc_sig"), kb_index_page()
    if moc_sig and moc_name:
        entries[moc_name] = (moc_sig, "?")      # decided below: the index never checks the MOC
    projects_sig = _sig(vault / PROJECTS_FILE)
    detector = index.get("detector")    # the index re-decides every page when the detector changes
    build_key = _digest([CATALOG_VERSION, str(vault), body_chars, exclude, projects_sig, detector,
                         sorted([rel, sig, reason] for rel, (sig, reason) in entries.items())])
    if _head(catalog_path) == build_key:
        return False
    cache_path = catalog_path.with_name(CACHE_NAME)
    cache = _load_cache(cache_path, vault, body_chars)
    parsed: Dict[str, dict] = {}
    for rel, (sig, reason) in sorted(entries.items()):
        old = cache.get(rel)
        if old and old["sig"] == sig and (old.get("detector") == detector if reason == "?"
                                          else old["withheld"] == reason):
            parsed[rel] = old
            continue
        text = texts.get(rel)
        text = _read(vault, rel) if text is None else text
        if text is None:
            continue
        if reason == "?":
            reason = credential_reason(rel, text) if exclude else None
        kind, archived = ("index", False) if rel == moc_name else page_kind(rel)
        page = withheld_stub(text, kind, archived) if reason else parse_page(text, rel, kind, archived, body_chars)
        parsed[rel] = {"sig": sig, "withheld": reason, "page": page}
        if rel == moc_name:
            parsed[rel]["detector"] = detector       # the MOC is decided here, not by the index
    projects, projects_state = load_projects(vault)
    source_sig = _digest(sorted([rel, *entry["sig"]] for rel, entry in parsed.items()))
    catalog = assemble(parsed, projects, projects_state, source_sig, build_key)
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    _write(cache_path, {"version": CATALOG_VERSION, "vault": str(vault), "body_chars": body_chars,
                        "pages": parsed})
    _write(catalog_path, catalog)
    return True


def load(catalog_path: Path) -> dict:
    return json.loads(Path(catalog_path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# `vault-find --catalog [VIEW]`: counts and paths, never note text
# ---------------------------------------------------------------------------

def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def render(catalog: dict, view: str = "health") -> List[str]:
    pages, graph, h = catalog["pages"], catalog["graph"], catalog["health"]
    if view == "json":
        return [json.dumps(catalog, sort_keys=True, ensure_ascii=False)]
    if view == "withheld":
        return [f"{rel}  ({pages[rel]['withheld']})" for rel in h["withheld"]["paths"]]
    if view == "broken":
        return [f"{rel} -> {target}" for rel, entry in sorted(pages.items()) if not entry["withheld"]
                and not entry["archived"] for target in entry["broken_links"]]
    if view == "orphans":
        return list(graph["orphans"])
    if view == "chains":
        return [f"{c['from']} -> {c['end'] or '-'}  ({c['state']}; {' > '.join(c['path'])})" for c in graph["chains"]]
    if view == "lint":
        return [f"{rel}: {e['lint'][0]} error(s), {e['lint'][1]} warning(s)"
                f"{'' if (e['created'] or '') >= CONTRACT_DATE else ' (legacy)'}  (vault-note check {rel})"
                for rel, e in sorted(pages.items()) if not e["withheld"] and e.get("lint") and e["lint"][0]]
    p, l, s = h["pages"], h["lessons"], h["schema"]
    return [
        f"pages {p['total']}: topic {p['topic']}, collection {p['collection']}, project {p['project']}, "
        f"index {p['index']} (archived {p['archived']}); withheld {h['withheld']['count']}",
        f"lessons {l['total']}: current {l['current']}, dated {l['dated']}, superseded {l['superseded']}; "
        f"conflicts {l['conflicts']}, unverified {l['unverified']}",
        f"lesson reach {h['lesson_reach']['reached']}/{h['lesson_reach']['lessons']} "
        f"({_percent(h['lesson_reach']['reached'], h['lesson_reach']['lessons'])}) inside the index window",
        f"key coverage {h['key_coverage']['with_keys']}/{h['key_coverage']['pages']} pages",
        f"broken links {h['broken_links']['count']} on {h['broken_links']['pages']} page(s); orphans {h['orphans']}",
        f"chains {h['chains']['count']}: broken {h['chains']['broken']}, cyclic {h['chains']['cyclic']}",
        f"schema errors {s['new_errors']} on {s['new_pages_with_errors']} page(s) created since {CONTRACT_DATE}; "
        f"legacy errors {s['legacy_errors']}, warnings {s['warnings']}",
        f"curation candidates {h['curation_candidates']} (over half the lessons dated or superseded); "
        f"projects map {h['projects_map']}",
    ]
