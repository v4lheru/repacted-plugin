"""KB-GATE: the queue-first gate for new KB knowledge (the design notes
section 7).

Driven by vault-note's propose, new, queue, drain and apply; read by the doctor's kb check.
Flow: propose -> refusing checks (grammar, D25, links, key grounding) -> routing -> the class
auto-accept bar -> apply now or queue -> drain (a named decider) -> apply.
Invariants: D25 is fail-closed (a hit or a detector error refuses before any write); never
auto-reject; the bar is >= 0.9 accepted-unedited on >= 50 terminal decisions per class;
queue items and decided records are sealed (HMAC) and only sealed files are trusted.

Map, in file order: D25 checks, the proposal and its rendering, the catalog and key
grounding, routing, the queue / decided log / bar, propose / decide / apply. The drain views
(blind brief, card) are in _kb/views.py; decide and the held-item rule in _kb/decide.py. Doc: the design notes
"""
from __future__ import annotations

import datetime
import difflib
import hashlib
import hmac
import importlib.machinery
import importlib.util
import json
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Optional

import _repact_common as common
from _kb import lessons as L

QUEUE_RELPATH = Path("_kb") / "queue"
DECIDED = "decided"
ACTIONS = ("skip", "append", "supersede", "new_topic")
DECISIONS = ("accepted", "edited", "rejected", "deferred")
TERMINAL = ("accepted", "edited", "rejected")
BLIND = ("CLAUDE_WRONG", "CLAUDE_KNOWS", "UNSURE")
EVIDENCE_CLASSES = ("measured", "observed", "documented", "inferred")
TITLE_MAX, SUMMARY_MAX, SLUG_MAX = 90, 160, 60
KEYS_MIN, KEYS_MAX = 3, 8
BAR_MIN, BAR_SHARE, AUDIT_EVERY = 50, 0.9, 10
STALE_DAYS = 3
AUTO_DECIDER = "auto-accept bar"
WIKILINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
KEY_PHRASE = re.compile(r"^[a-z0-9][a-z0-9.+#/_-]*(?: [a-z0-9][a-z0-9.+#/_-]*){1,4}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MD_NOISE = re.compile(r"[*_`>#\[\]]")
CREDENTIAL_PATH = "kb-gate"
ID_RE = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{8}")
DECIDER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._@-]{0,63}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# A rendered bullet's own label ("- **Why:** "), stripped so a whole-line class sees the value.
LABEL_PREFIX = re.compile(r"^[ \t]*(?:[-*+>][ \t]+)*(?:[*_]{1,2}[^*_\n]{1,40}?[*_]{1,2}[ \t]*)?")
SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID")
AGENT_ENV = "REPACT_AGENT_NAME"
SEAL_KEY_FILE = "kb-gate.key"
WHOLE_CLAIM_WORDS = 8          # a shorter claim skips only on a whole-claim match (K6)
# Machine proposers (set by trusted code, never by a proposal's JSON). Each has its own
# auto-accept class: its bar counts only its own decided records, and the human bar excludes it.
# A `distill` item whose evidence class is `inferred` never auto-accepts.
PROPOSER_IDS = ("distill",)
NEVER_AUTO_EVIDENCE = {"distill": ("inferred",)}

_VN = None


class Refusal(Exception):
    """A deterministic check failed: the caller fixes it. Nothing was written."""

    def __init__(self, check: str, message: str, code: int = 2):
        super().__init__(message)
        self.check, self.code = check, code


def vault_note():
    """bin/vault-note as a module: its additive write primitives and vault-find helpers."""
    global _VN
    if _VN is None:
        main = sys.modules.get("__main__")
        if main is not None and getattr(main, "__file__", "") and Path(main.__file__).name == "vault-note":
            _VN = main
        else:
            path = Path(__file__).resolve().parent.parent / "vault-note"
            loader = importlib.machinery.SourceFileLoader("_kb_gate_vault_note", str(path))
            spec = importlib.util.spec_from_loader("_kb_gate_vault_note", loader)
            module = importlib.util.module_from_spec(spec)
            loader.exec_module(module)
            _VN = module
    return _VN


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(moment: datetime.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def credential_reason(where: str, text: str) -> Optional[str]:
    """The D25 detector (one place, so a test can make it raise)."""
    return vault_note().VF.credential_reason(where, text)


# ---------------------------------------------------------------------------------------------
# The proposal and its rendering (section 4.2)
# ---------------------------------------------------------------------------------------------

def _strings(value):
    """Every string inside a JSON-shaped value, keys included, exactly as sent (never json.dumps:
    escaping turns `"` into `\\"` and newlines into `\\n`, which hides values from the detector)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _d25_pieces(text: str) -> list[str]:
    """The whole text, each line, and each line without its bullet label: whole-line classes
    (credential-line) must see `pass: X` even when it sits behind `- **Why:** `."""
    pieces = [text]
    for line in text.splitlines():
        pieces.append(line)
        stripped = LABEL_PREFIX.sub("", line, count=1)
        if stripped != line:
            pieces.append(stripped)
    return list(dict.fromkeys(piece for piece in pieces if piece.strip()))


def _first_credential(texts) -> Optional[str]:
    for text in texts:
        for piece in _d25_pieces(text):
            kind = credential_reason(CREDENTIAL_PATH, piece)
            if kind:
                return kind
    return None


def check_d25_raw(raw, what: str = "the proposal") -> None:
    """D25 over every raw field, line by line, before any lowercasing, trimming or collapse."""
    try:
        kind = _first_credential(_strings(raw))
    except Exception as exc:  # noqa: BLE001 - a detector failure is a refusal, never a pass
        raise Refusal("d25", f"refused: the credential detector failed ({exc.__class__.__name__}); nothing written", 1)
    if kind:
        raise Refusal("d25", f"refused: {what} carries a credential ({kind}); nothing written", 1)


def normalise_proposal(raw) -> dict:
    if not isinstance(raw, dict):
        raise Refusal("grammar", "the proposal must be a JSON object")
    evidence = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}
    text = lambda value: " ".join(str(value).split()) if isinstance(value, str) else ""
    keys = raw.get("keys") if isinstance(raw.get("keys"), list) else []
    links = raw.get("links") if isinstance(raw.get("links"), list) else []
    proposer = raw.get("proposer") if isinstance(raw.get("proposer"), dict) else {}
    return {
        "title": text(raw.get("title")), "claim": text(raw.get("claim")), "why": text(raw.get("why")),
        "apply": text(raw.get("apply")),
        "evidence": {"class": text(evidence.get("class")), "how": text(evidence.get("how"))},
        "ctx": text(raw.get("ctx")), "date": text(raw.get("date")),
        "kind": text(raw.get("kind")) or "gotcha", "topic": text(raw.get("topic")),
        "tool": text(raw.get("tool")),
        "keys": [" ".join(str(k).split()).lower() for k in keys if isinstance(k, str) and k.strip()],
        "summary": text(raw.get("summary")),
        "links": [str(link) for link in links if isinstance(link, str)],
        "supersedes": text(raw.get("supersedes")) or None,
        "proposer": {"session": proposer.get("session"), "agent": proposer.get("agent"),
                     "repo": proposer.get("repo")},
    }


def lesson_block(p: dict) -> list[str]:
    """The section 4.2 grammar: title line with provenance and date, claim, Why, Apply, Evidence."""
    return [f"### {p['title']} *(from {p['ctx']}, {p['date']})*", p["claim"], f"- **Why:** {p['why']}",
            f"- **Apply:** {p['apply']}", f"- **Evidence:** ({p['evidence']['class']}) {p['evidence']['how']}"]


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(slug) > SLUG_MAX:
        slug = slug[:SLUG_MAX].rsplit("-", 1)[0]
    return slug


def kind_folders() -> dict:
    """{kind: folder} from vault-note's KIND_PATHS (one source for new topic paths)."""
    return {kind: folder for kind, (folder, _pattern) in vault_note().KIND_PATHS.items()}


def new_topic_relpath(p: dict) -> str:
    folder = kind_folders()[p["kind"]]
    base = p["topic"] or p["title"]
    slug = slugify(f"{p['tool']}-{base}" if p["tool"] and not base.lower().startswith(p["tool"].lower()) else base)
    return f"{folder}/topic-{slug}.md" if folder else f"topic-{slug}.md"


def check_grammar(p: dict) -> None:
    missing = [name for name in ("title", "claim", "why", "apply", "ctx", "date") if not p[name]]
    missing += [f"evidence.{name}" for name in ("class", "how") if not p["evidence"][name]]
    if missing:
        raise Refusal("grammar", f"missing field(s): {', '.join(missing)}")
    if len(p["title"]) > TITLE_MAX:
        raise Refusal("grammar", f"title is {len(p['title'])} characters; the limit is {TITLE_MAX}")
    if p["evidence"]["class"] not in EVIDENCE_CLASSES:
        raise Refusal("grammar", f"evidence.class must be one of {', '.join(EVIDENCE_CLASSES)}")
    if not DATE.match(p["date"]):
        raise Refusal("grammar", "date must be YYYY-MM-DD")
    if any(ch in p["ctx"] for ch in "()*\n") or "," in p["ctx"]:
        raise Refusal("grammar", "ctx must not contain parentheses, asterisks or commas")
    if any(ch in p["title"] for ch in "()*"):
        raise Refusal("grammar", "the title must not contain parentheses or asterisks (the provenance tag is added)")
    if p["kind"] not in kind_folders():
        raise Refusal("grammar", f"kind must be one of {', '.join(sorted(kind_folders()))}")
    if p["tool"] and not SLUG_RE.match(p["tool"]):
        raise Refusal("grammar", "tool must be a lowercase slug (letters, digits, hyphens)")
    if not L.BLOCK_RE.match(lesson_block(p)[0]):
        raise Refusal("grammar", "the rendered title line is not a lesson block")


def check_new_topic_fields(p: dict) -> None:
    if not KEYS_MIN <= len(p["keys"]) <= KEYS_MAX:
        raise Refusal("grammar", f"a new topic page needs {KEYS_MIN}-{KEYS_MAX} keys (got {len(p['keys'])})")
    if not p["summary"]:
        raise Refusal("grammar", "a new topic page needs a summary (one sentence)")
    if len(p["summary"]) > SUMMARY_MAX:
        raise Refusal("grammar", f"summary is {len(p['summary'])} characters; the limit is {SUMMARY_MAX}")
    if vault_note_quote(p["summary"]) is None:
        raise Refusal("grammar", "the summary cannot be stored exactly as a frontmatter value (quote characters)")


def page_tags(p: dict) -> list[str]:
    return ["wiki", p["kind"]] + ([p["tool"]] if p["tool"] else [])


def check_d25(p: dict, extra: tuple = ()) -> None:
    """Everything that would be stored or rendered (the fields below, plus `extra` such as a
    rendered page), each per line and without bullet labels. Fail-closed."""
    texts = [("lesson", "\n".join(lesson_block(p))), ("summary", p["summary"]),
             ("keys", "\n".join(p["keys"])), ("links", "\n".join(p["links"])),
             ("path", new_topic_relpath(p) if p["kind"] in kind_folders() else ""),
             ("topic", p["topic"]), ("tool", p["tool"]), ("tags", "\n".join(page_tags(p))),
             ("supersedes", p["supersedes"] or ""),
             ("proposer", "\n".join(str(v) for v in p["proposer"].values() if v)), *extra]
    try:
        for where, text in texts:
            kind = _first_credential([text]) if text else None
            if kind:
                raise Refusal("d25", f"refused: the {where} carries a credential ({kind}); nothing written", 1)
    except Refusal:
        raise
    except Exception as exc:  # noqa: BLE001 - a detector failure is a refusal, never a pass
        raise Refusal("d25", f"refused: the credential detector failed ({exc.__class__.__name__}); nothing written", 1)


def vault_note_quote(summary: str) -> Optional[str]:
    """The KB-MIGRATE quoting rule, so a summary reads back exactly in vault-find and YAML."""
    if not summary or summary[0] in "\"'" or summary[-1] in "\"'":
        return None
    if '"' not in summary and "\\" not in summary:
        return f'"{summary}"'
    if "'" not in summary:
        return f"'{summary}'"
    return None


# ---------------------------------------------------------------------------------------------
# The catalog (links, keys, lessons) and key grounding
# ---------------------------------------------------------------------------------------------

def load_catalog(vault: Path) -> dict:
    VF = vault_note().VF
    try:
        index_path = VF.default_index_path()
        VF.refresh_index(vault, index_path)
        return json.loads(VF.catalog_path(index_path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - without a catalog the links cannot be checked
        raise Refusal("catalog", f"the KB catalog is unavailable ({exc.__class__.__name__}); nothing written", 3)


def live_pages(catalog: dict) -> dict:
    return {rel: page for rel, page in catalog.get("pages", {}).items()
            if not page.get("withheld") and page.get("type") in ("topic", "collection") and not page.get("archived")}


def _stems(catalog: dict) -> dict:
    """{stem: relpath}; a live page wins over an archived twin with the same stem."""
    pages = catalog.get("pages", {})
    ordered = sorted(pages, key=lambda rel: (not (pages[rel].get("archived") or "/_Archive/" in f"/{rel}"), rel))
    return {Path(rel).stem: rel for rel in ordered}


def check_links(p: dict, catalog: dict) -> None:
    targets = WIKILINK.findall("\n".join([p["claim"], p["why"], p["apply"], p["evidence"]["how"]] + p["links"]))
    targets += [link.strip("[]") for link in p["links"] if not WIKILINK.search(link)]
    stems, pages = _stems(catalog), catalog.get("pages", {})
    for target in targets:
        name, _, heading = target.split("|", 1)[0].partition("#")
        rel = stems.get(Path(name.strip()).stem)
        if rel is None:
            near = difflib.get_close_matches(Path(name.strip()).stem, list(stems), n=3)
            raise Refusal("links", f"[[{target}]] is not a page in the KB; nearest: {', '.join(near) or 'none'}")
        page = pages[rel]
        if page.get("archived") or "/_Archive/" in f"/{rel}":
            end = page.get("chain_end")
            raise Refusal("links", f"[[{target}]] is archived; link its successor{' [[' + Path(end).stem + ']]' if end else ''}")
        if heading and heading.strip() not in {lesson["title"] for lesson in page.get("lessons") or []}:
            raise Refusal("links", f"[[{target}]]: no lesson titled {heading.strip()!r} on that page")


def check_keys(p: dict) -> None:
    """Each key: 2-5 lowercase words, found in the title or claim, not all stop words, not the bare
    tool name. (A key already owned by another topic is a queue note, not an error: see check.)"""
    stop = vault_note().VF.STOP_WORDS
    haystack = " ".join(f"{p['title']} {p['claim']}".lower().split())
    for key in p["keys"]:
        if not KEY_PHRASE.match(key):
            raise Refusal("keys", f"key {key!r} is not 2-5 lowercase words")
        if all(word in stop for word in key.split()):
            raise Refusal("keys", f"key {key!r} is only stop words")
        if p["tool"] and key == p["tool"].lower():
            raise Refusal("keys", f"key {key!r} is the bare tool name")
        if not re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack):
            raise Refusal("keys", f"key {key!r} does not occur in the title or claim")


# ---------------------------------------------------------------------------------------------
# Routing (section 7.3)
# ---------------------------------------------------------------------------------------------

def _norm(text: str) -> str:
    return " ".join(MD_NOISE.sub(" ", text.lower()).split())


def _page_text(vault: Path, rel: str) -> Optional[str]:
    VF = vault_note().VF
    path = vault / rel
    if path.is_symlink() or not VF.is_within(path, vault) or not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    return None if credential_reason(rel, text) else text


def lesson_texts(text: str, rel: str) -> list[tuple[str, str]]:
    lines = text.split("\n")
    return [(lesson.id, "\n".join(lines[lesson.start - 1:lesson.end])) for lesson in L.lessons(text, rel)
            if lesson.state != "superseded"]


def _key_hits(p: dict, pages: dict) -> list[tuple[int, str]]:
    VF = vault_note().VF
    query = f"{p['title']} {p['claim']}"
    hits = []
    for rel, page in pages.items():
        keys = page.get("keys") or []
        score = len(set(keys) & set(p["keys"])) + sum(1 for key in keys if VF.key_matches([key], query))
        if score:
            hits.append((score, rel))
    return sorted(hits, key=lambda item: (-item[0], item[1]))


def _same_claim(claim: str, body: str) -> bool:
    """A known claim (K6): a long claim may sit anywhere in the lesson; a short one must BE the
    lesson's claim line, or "every deploy" would match inside every Apply bullet that says it."""
    claim = _norm(claim).rstrip(".!;: ")
    if not claim:
        return False
    if len(claim.split()) >= WHOLE_CLAIM_WORDS:
        return claim in _norm(body)
    lines = body.split("\n")
    return len(lines) > 1 and claim == _norm(lines[1]).rstrip(".!;: ")


def _chain_end(rel: str, catalog: dict) -> str:
    page = catalog["pages"].get(rel) or {}
    return page.get("chain_end") or rel


def _phrase_in(phrase: str, text: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text))


def topic_match(p: dict, rel: str, page: dict) -> bool:
    """The note's subject is the page's topic: one of the note's keys occurs in the page's title,
    file name or keys, or one of the page's multi-word keys occurs in the note's title or claim.
    A one-word page key ("mcp") alone matched a note to an unrelated project's page, so it never
    routes an append on its own."""
    keys = [k for k in page.get("keys") or [] if isinstance(k, str)]
    page_text = " | ".join([_norm(str(page.get("title") or "")), Path(rel).stem.replace("-", " ")]
                           + [_norm(k) for k in keys])
    note_text = _norm(f"{p['title']} {p['claim']}")
    return any(_phrase_in(key, page_text) for key in p["keys"]) \
        or any(_phrase_in(_norm(key), note_text) for key in keys if len(key.split()) > 1)


def route(p: dict, vault: Path, catalog: dict) -> dict:
    pages = live_pages(catalog)
    key_hits = _key_hits(p, pages)
    VF = vault_note().VF
    try:
        search_hits = [h["relpath"] for h in VF.search(f"{p['title']} {p['claim']}", k=5, vault=vault)]
    except Exception:  # noqa: BLE001 - search is a candidate source, never a gate
        search_hits = []
    candidates = list(dict.fromkeys([rel for _, rel in key_hits] + [rel for rel in search_hits if rel in pages]))
    routing = {"key_matches": [rel for _, rel in key_hits][:5], "lesson_hits": [],
               "candidates": candidates[:3]}
    live_lessons = []
    for rel in candidates:
        text = _page_text(vault, rel)
        if text is not None:
            live_lessons += lesson_texts(text, rel)
    duplicate = next((lesson_id for lesson_id, body in live_lessons if _same_claim(p["claim"], body)), None)
    if duplicate:
        routing["lesson_hits"] = [[duplicate, 1.0]]
        return {"action": "skip", "target": duplicate.split("#", 1)[0], "supersedes": None, "routing": routing}
    key_topics = [rel for _, rel in key_hits if pages[rel]["type"] == "topic"]
    topic_hits = [rel for rel in key_topics if topic_match(p, rel, pages[rel])]
    routing["topic_mismatch"] = [rel for rel in key_topics if rel not in topic_hits][:3]
    if p["supersedes"]:
        old_page = p["supersedes"].split("#", 1)[0]
        known = {lesson["id"] for lesson in (catalog["pages"].get(old_page) or {}).get("lessons") or []}
        if p["supersedes"] not in known:
            raise Refusal("links", f"--supersedes {p['supersedes']} is not a live lesson id in the catalog")
        target = _chain_end(topic_hits[0], catalog) if topic_hits else new_topic_relpath(p)
        return {"action": "supersede", "target": target, "supersedes": p["supersedes"], "routing": routing,
                "new_page": not topic_hits}
    if topic_hits:
        return {"action": "append", "target": _chain_end(topic_hits[0], catalog), "supersedes": None,
                "routing": routing}
    related = [rel for _, rel in key_hits if pages[rel]["type"] == "collection"][:1]
    routing["related"] = related
    return {"action": "new_topic", "target": new_topic_relpath(p), "supersedes": None, "routing": routing}


# ---------------------------------------------------------------------------------------------
# The queue, the decided log and the bar
# ---------------------------------------------------------------------------------------------

def queue_dir(vault: Path) -> Path:
    return Path(vault) / QUEUE_RELPATH


def decided_dir(vault: Path) -> Path:
    return queue_dir(vault) / DECIDED


def _checked_dir(vault: Path, relative: Path, create: bool) -> Path:
    """A directory under the vault reached with no symlink on the way and inside the vault (K8);
    created one level at a time only after each level is checked. OutOfScope otherwise."""
    VN = vault_note()
    current = Path(vault)
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink() or (current.exists() and not current.is_dir()):
            raise VN.OutOfScope(f"{current.relative_to(vault)} is a symlink or not a directory; the KB queue stays inside the vault")
        if not current.exists():
            if not create:
                return current
            current.mkdir()
    if not VN.VF.is_within(current, vault):
        raise VN.OutOfScope(f"{relative} resolves outside the knowledge folder {vault}")
    return current


def _queue_file(vault: Path, *parts: str, create: bool = True) -> Path:
    """<vault>/_kb/queue/<parts...>: every directory level checked (and created) before use."""
    directory = _checked_dir(vault, QUEUE_RELPATH.joinpath(*parts[:-1]), create)
    return directory / parts[-1]


# The seal (K2, K7): an HMAC over the record, keyed by a file in the plugin data dir, outside the
# vault. Files a person or a sync client drops into _kb/queue carry no valid seal and are ignored.

def _seal_key() -> bytes:
    path = common.plugin_data_dir() / SEAL_KEY_FILE
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    key = secrets.token_bytes(32)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    return key


def _digest(record: dict) -> str:
    body = json.dumps({k: v for k, v in record.items() if k != "seal"}, sort_keys=True, ensure_ascii=False)
    return hmac.new(_seal_key(), body.encode("utf-8"), hashlib.sha256).hexdigest()


def seal_record(record: dict) -> dict:
    return dict(record, seal=_digest(record))


def verify_seal(record) -> bool:
    return isinstance(record, dict) and isinstance(record.get("seal"), str) \
        and hmac.compare_digest(record["seal"], _digest(record))


def _write_json(path: Path, value: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(seal_record(value), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _create_json(path: Path, value: dict) -> None:
    """O_EXCL: a second writer of the same name fails instead of overwriting (K6)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(seal_record(value), indent=1, sort_keys=True) + "\n")


def item_for(p: dict, routed: dict, moment: datetime.datetime, key_notes: list, nonce: str = "",
             postcheck: Optional[dict] = None) -> dict:
    """The queue item. A machine proposer's post-check verdict is sealed in it (`postcheck`)."""
    lesson = {name: p[name] for name in ("title", "claim", "why", "apply", "evidence", "ctx", "date")}
    page = {"keys": p["keys"], "summary": p["summary"], "kind": p["kind"], "links": p["links"],
            "topic": p["topic"], "tool": p["tool"]}
    body = {"lesson": lesson, "page": page, "action": routed["action"], "target": routed["target"],
            "supersedes": routed["supersedes"], "proposer": p["proposer"], "nonce": nonce or secrets.token_hex(4)}
    sha8 = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:8]
    return {"id": f"{moment.strftime('%Y%m%dT%H%M%SZ')}-{sha8}", "created": _iso(moment),
            "proposer": p["proposer"], "lesson": lesson, "page": page,
            "action": routed["action"], "target": routed["target"], "supersedes": routed["supersedes"],
            "routing": routed["routing"],
            "checks": {"grammar": "pass", "d25": "pass", "links": "pass", "keys_grounded": "pass"},
            "notes": key_notes, "value": {"blind": "pending"}, "decision": None,
            **({"postcheck": postcheck} if postcheck else {})}


def _scan(paths) -> tuple[list[dict], list[str], list[str]]:
    """(sealed records, corrupt file names, unsealed file names): nothing is dropped silently (K6)."""
    records, corrupt, unsealed = [], [], []
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            corrupt.append(path.name)
            continue
        if verify_seal(record):
            records.append(record)
        else:
            unsealed.append(path.name)
    return records, corrupt, unsealed


def scan_queue(vault: Path) -> dict:
    """The queue as it stands: sealed items, plus the files that could not be trusted."""
    root = queue_dir(vault)
    if root.is_symlink():
        return {"items": [], "corrupt": [f"{QUEUE_RELPATH} (a symlink)"], "unsealed": []}
    if not root.is_dir():
        return {"items": [], "corrupt": [], "unsealed": []}
    items, corrupt, unsealed = _scan(sorted(root.glob("*.json")))
    return {"items": items, "corrupt": corrupt, "unsealed": unsealed}


def decided_records(vault: Path) -> list[dict]:
    """Sealed decided records only: hand-written or sync-copied files never count (K2, K7)."""
    root = decided_dir(vault)
    if not root.is_dir() or queue_dir(vault).is_symlink() or root.is_symlink():
        return []
    return _scan(sorted(root.rglob("*.json")))[0]


def proposer_class(record: dict) -> Optional[str]:
    """The machine proposer id of a sealed record, or None for the human class."""
    value = (record.get("proposer") or {}).get("id")
    return value if value in PROPOSER_IDS else None


def bar(vault: Path, action: str, proposer_id: Optional[str] = None) -> dict:
    """The class auto-accept bar, from the sealed decided log: terminal human decisions only.
    The class is (action, proposer id): a machine proposer's decisions never count for the human
    class, nor the other way round, and a held item's decisions never count (_kb/decide.py)."""
    records = [r for r in decided_records(vault) if r.get("action") == action
               and proposer_class(r) == proposer_id and (r.get("postcheck") or {}).get("action") != "hold"]
    terminal = [r for r in records if (r.get("decision") or {}).get("decision") in TERMINAL]
    unedited = sum(1 for r in terminal if r["decision"]["decision"] == "accepted")
    share = unedited / len(terminal) if terminal else 0.0
    autos = sum(1 for r in records
                if (r.get("decision") or {}).get("decision") == "auto" or r.get("audit_sample"))
    autos += sum(1 for r in pending(vault) if r.get("action") == action and r.get("audit_sample")
                 and proposer_class(r) == proposer_id)
    return {"action": action, "proposer": proposer_id, "decisions": len(terminal),
            "accepted_unedited": unedited, "share": round(share, 4),
            "on": len(terminal) >= BAR_MIN and share >= BAR_SHARE, "auto_candidates": autos}


def pending(vault: Path) -> list[dict]:
    return scan_queue(vault)["items"]


def queue_health(vault: Path, moment: Optional[datetime.datetime] = None) -> dict:
    moment = moment or now_utc()
    state = scan_queue(vault)
    ages = []
    for item in state["items"]:
        try:
            created = datetime.datetime.strptime(item["created"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=datetime.timezone.utc)
        except (KeyError, ValueError):
            continue
        ages.append((moment - created).total_seconds() / 86400)
    oldest = max(ages) if ages else 0.0
    corrupt = len(state["corrupt"]) + len(state["unsealed"])
    return {"count": len(ages), "oldest_days": round(oldest, 1), "stale": oldest > STALE_DAYS,
            "corrupt": corrupt, "corrupt_files": state["corrupt"] + state["unsealed"]}


# ---------------------------------------------------------------------------------------------
# propose / decide / apply
# ---------------------------------------------------------------------------------------------

def caller() -> dict:
    """Who is running this command, from the environment only (never from a proposal's JSON)."""
    session = next((os.environ[name] for name in SESSION_ENV if os.environ.get(name)), None)
    return {"session": session, "agent": os.environ.get(AGENT_ENV) or None}


def check(p: dict, vault: Path) -> tuple[dict, dict, list]:
    """Every deterministic check, then routing. Writes nothing (the catalog refresh after D25
    touches only the plugin data dir, never the KB or the queue)."""
    check_grammar(p)
    check_d25(p)
    catalog = load_catalog(vault)
    check_links(p, catalog)
    check_keys(p)
    routed = route(p, vault, catalog)
    notes = [f"key {key!r} is also a key of {rel}" for rel, page in live_pages(catalog).items()
             if page.get("type") == "topic" and rel != routed["target"]
             for key in p["keys"] if key in (page.get("keys") or [])]
    if routed["action"] == "new_topic" or routed.get("new_page"):
        check_new_topic_fields(p)
        if (Path(vault) / routed["target"]).exists():
            raise Refusal("grammar", f"{routed['target']} exists; choose another topic")
    return catalog, routed, notes


def _new_item_file(vault: Path, make_item, where: tuple, value_of=lambda item: item) -> dict:
    """Create the item's file with O_EXCL, drawing a fresh id on the (rare) collision."""
    for _attempt in range(5):
        item = make_item()
        try:
            _create_json(_queue_file(vault, *where, f"{item['id']}.json"), value_of(item))
            return item
        except FileExistsError:
            continue
    raise Refusal("queue", "could not create a unique queue id; nothing written", 1)


def propose(vault: Path, raw, moment: Optional[datetime.datetime] = None, *,
            proposer_id: Optional[str] = None, no_auto: bool = False, postcheck: Optional[dict] = None) -> dict:
    """Check, route and queue (or auto-accept) one proposal. `proposer_id` names a machine
    proposer (PROPOSER_IDS) and comes from the calling code only; `no_auto` forces the queue;
    `postcheck` is that proposer's verdict {action, reasons}, sealed into the item."""
    moment = moment or now_utc()
    if proposer_id is not None and proposer_id not in PROPOSER_IDS:
        raise Refusal("usage", f"unknown proposer id {proposer_id!r}")
    check_d25_raw(raw)
    p = normalise_proposal(raw)
    p["proposer"] = dict(caller(), repo=p["proposer"].get("repo"))     # K2: stamped here, never taken from the JSON
    if proposer_id is not None:
        p["proposer"]["id"] = proposer_id
    _catalog, routed, notes = check(p, vault)
    make = lambda: item_for(p, routed, moment, notes, postcheck=postcheck if proposer_id else None)
    state = bar(vault, routed["action"], proposer_id)
    never = p["evidence"]["class"] in NEVER_AUTO_EVIDENCE.get(proposer_id, ())
    if state["on"] and not (no_auto or never) and (state["auto_candidates"] + 1) % AUDIT_EVERY != 0:
        decision = {"decision": "auto", "by": AUTO_DECIDER, "at": _iso(moment), "blind": None, "bar": state}
        item = _new_item_file(vault, make, (DECIDED, moment.strftime("%Y-%m")),
                              lambda it: dict(it, decision=decision, applied=False))
        done, failures = apply(vault, only=item["id"])        # the record exists first: a failure is retried by apply
        return {"status": "auto-applied" if done else "auto-accepted", "id": item["id"], "action": item["action"],
                "target": item["target"], "apply_error": failures[0]["error"] if failures else None}
    audit = state["on"] and not (no_auto or never)     # a forced-queue item is no audit sample
    item = _new_item_file(vault, make, (), lambda it: dict(it, audit_sample=True) if audit else it)
    return {"status": "queued", "id": item["id"], "action": item["action"], "target": item["target"],
            "audit_sample": audit, "candidates": routed["routing"]["candidates"]}


def validate_target(vault: Path, record: dict, p: dict) -> Path:
    """K8: the target is checked BEFORE any filesystem call. A new page must be exactly the path
    the gate derives from the lesson (<Folder>/topic-<slug>.md); an existing one must be a regular
    in-scope page. Relative, no '..', no symlinked directory on the way."""
    VN = vault_note()
    target = record.get("target")
    if not isinstance(target, str) or not target.endswith(".md") or "\\" in target or target.startswith("/") \
            or any(part in ("", ".", "..") for part in target.split("/")):
        raise Refusal("target", f"target {target!r} is not a relative page path inside the KB")
    path = Path(vault) / target
    current = Path(vault)
    for part in Path(target).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise VN.OutOfScope(f"{target} passes through a symlinked directory, outside the knowledge folder")
    if path.exists() or path.is_symlink():
        if not VN.in_scope_page(vault, path):
            raise VN.OutOfScope(f"{target} is not a page inside the knowledge folder")
    elif target != new_topic_relpath(p):
        raise Refusal("target", f"a new page must be {new_topic_relpath(p)}, not {target}")
    return path


def recheck(vault: Path, record: dict, catalog: dict) -> dict:
    """K7: everything propose checked, again, on exactly what apply is about to write."""
    p = normalise_proposal({**record["lesson"], **record["page"], "proposer": record.get("proposer") or {},
                            "supersedes": record.get("supersedes")})
    if record.get("action") not in ACTIONS:
        raise Refusal("grammar", f"unknown action {record.get('action')!r}")
    check_grammar(p)
    check_d25(p)
    check_links(p, catalog)
    check_keys(p)
    if record["action"] != "skip":
        validate_target(vault, record, p)
        if not (Path(vault) / record["target"]).exists():
            check_new_topic_fields(p)
        elif record["action"] == "append" and not topic_match(p, record["target"],
                                                              catalog["pages"].get(record["target"]) or {}):
            raise Refusal("topic", f"{record['target']} is not the topic of {p['title']!r}; nothing appended "
                                   "(decide it edited with keys that name its subject, or rejected)")
    return p


def apply_record(vault: Path, record: dict, catalog: Optional[dict] = None) -> dict:
    """Write one accepted (or auto) record through vault-note's additive primitives. Idempotent: a
    lesson already on the page is never written again, and a supersede whose old lesson is gone
    refuses before any write (K3)."""
    VN = vault_note()
    p = recheck(vault, record, catalog if catalog is not None else load_catalog(vault))
    block = lesson_block(p)
    action, target = record["action"], record["target"]
    if action == "skip":
        return dict(record, applied=True, applied_as="nothing written: already known")
    marker = None
    if action == "supersede":
        marker = (record["supersedes"], f"> Superseded {p['date']}: [[{Path(target).stem}#{p['title']}]]")
        _find_lesson(vault, record["supersedes"])                     # refuses now, before the lesson is written
    path = Path(vault) / target
    if not path.exists():
        write_new_topic(vault, target, p, record)
    else:
        old = path.read_text(encoding="utf-8")
        new = old if block[0] in old.split("\n") else insert_lesson(old, block)
        if marker and marker[0].split("#", 1)[0] == target:
            new = _with_marker(new, target, *marker)
            marker = None
        if new != old:
            VN.write_additive(path, old, VN.bump_modified(new), allow_changed=VN.ALLOW_CHANGED)
    if marker:
        add_marker(vault, *marker)
    return dict(record, applied=True)


def insert_lesson(text: str, block: list[str]) -> str:
    """The new lesson goes before the page's last `## Related` (the lessons end there)."""
    lines = text.rstrip("\n").split("\n")
    related = [n for n, line in enumerate(lines) if line.strip() == "## Related"]
    at = related[-1] if related else len(lines)
    while at > 0 and not lines[at - 1].strip():
        at -= 1
    lines[at:at] = ["", *block, ""]
    return "\n".join(lines) + "\n"


def _find_lesson(vault: Path, lesson_id: str):
    VN = vault_note()
    rel = lesson_id.split("#", 1)[0]
    path = Path(vault) / rel
    if not VN.in_scope_page(vault, path):
        raise VN.OutOfScope(f"{rel} is not a page inside the knowledge folder")
    found = next((lesson for lesson in L.lessons(path.read_text(encoding="utf-8"), rel) if lesson.id == lesson_id), None)
    if found is None:
        raise Refusal("links", f"lesson {lesson_id} no longer exists; nothing was written", 4)
    return found


def _with_marker(text: str, rel: str, lesson_id: str, marker: str) -> str:
    found = next((lesson for lesson in L.lessons(text, rel) if lesson.id == lesson_id), None)
    if found is None:
        raise Refusal("links", f"lesson {lesson_id} no longer exists; nothing was written", 4)
    lines = text.split("\n")
    if marker in lines[found.start:found.end]:
        return text                                            # already marked: idempotent
    lines.insert(found.start, marker)
    return "\n".join(lines)


def add_marker(vault: Path, lesson_id: str, marker: str) -> None:
    VN = vault_note()
    rel = lesson_id.split("#", 1)[0]
    _find_lesson(vault, lesson_id)
    path = Path(vault) / rel
    old = path.read_text(encoding="utf-8")
    new = _with_marker(old, rel, lesson_id, marker)
    if new != old:
        VN.write_additive(path, old, VN.bump_modified(new), allow_changed=VN.ALLOW_CHANGED)


def render_new_topic(p: dict, record: dict, today: str) -> str:
    related = (record.get("routing") or {}).get("related") or (record.get("routing") or {}).get("candidates") or []
    index = common.kb_index_page()
    links = ([f"- [[{Path(rel).stem}]]" for rel in related[:3]]
             or ([f"- [[{Path(index).stem}]]"] if index else []))
    lines = ["---", "type: resource", "tags:"] + [f"  - {tag}" for tag in page_tags(p)] + [
        "status: draft", f"created: {today}", f"modified: {today}", "area: work",
        f"wiki-source: {vault_note().NEW_PAGE_SOURCE}", "wiki-confidence: medium", "wiki-topic-keys:"] + [
        f"  - {key}" for key in p["keys"]] + [f"wiki-summary: {vault_note_quote(p['summary'])}",
                                              f"wiki-first-seen: {p['date']}", "---", "",
                                              f"# {p['title']}", "", p["summary"], "", *lesson_block(p), "",
                                              "## Related", "", *links, ""]
    return "\n".join(lines)


def write_new_topic(vault: Path, target: str, p: dict, record: dict) -> None:
    """The target was validated by recheck before this call (no mkdir happens before that)."""
    VN = vault_note()
    path = Path(vault) / target
    text = render_new_topic(p, record, VN.today())
    check_d25(p, extra=(("page", text),))                         # the page exactly as it will be written
    parent = path.parent
    if parent.is_symlink() or (parent.exists() and not VN.VF.is_within(parent, vault)):
        raise VN.OutOfScope(f"{target} resolves outside the knowledge folder {vault}")
    parent.mkdir(parents=True, exist_ok=True)
    if not VN.VF.is_within(parent, vault):
        raise VN.OutOfScope(f"{target} resolves outside the knowledge folder {vault}")
    with open(path, "x", encoding="utf-8") as handle:        # O_EXCL: never overwrite
        handle.write(text)
    section = VN.MOC_SECTIONS.get(p["kind"], "## Other")
    try:
        VN.add_moc_line(vault, path.stem, p["summary"], section)
    except VN.OutOfScope:
        pass                                                   # no MOC in this KB: the page stands alone


def apply(vault: Path, only: Optional[str] = None) -> tuple[list[dict], list[dict]]:
    """Every sealed, accepted (or auto) record not yet applied, each on its own: a stuck record is
    reported and the rest still apply (K3). Only files named exactly <id>.json count, once per id,
    so sync-conflict copies are ignored (K7). One index and catalog refresh at the end."""
    VN = vault_note()
    done, failures, seen = [], [], set()
    root = decided_dir(vault)
    if not root.is_dir() or root.is_symlink() or queue_dir(vault).is_symlink():
        return done, failures
    catalog = None
    for path in sorted(root.rglob("*.json")):
        if not ID_RE.fullmatch(path.stem) or path.stem in seen or (only and path.stem != only):
            continue
        seen.add(path.stem)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            failures.append({"id": path.stem, "error": "corrupt record; not applied"})
            continue
        if (record.get("decision") or {}).get("decision") not in ("accepted", "edited", "auto") or record.get("applied") is True:
            continue
        if not verify_seal(record) or record.get("id") != path.stem:
            failures.append({"id": path.stem, "error": "not written by the gate (its seal does not match); not applied"})
            continue
        try:
            catalog = catalog if catalog is not None else load_catalog(vault)
            record = apply_record(vault, record, catalog)
        except (Refusal, VN.OutOfScope, VN.NotAdditive, OSError) as exc:
            failures.append({"id": path.stem, "error": str(exc) or exc.__class__.__name__})
            continue
        _write_json(path, record)
        done.append({"id": record["id"], "action": record["action"], "target": record["target"]})
    if done:
        refresh(vault)
    return done, failures


def refresh(vault: Path) -> None:
    VF = vault_note().VF
    try:
        VF.refresh_index(vault, VF.default_index_path())
    except Exception:  # noqa: BLE001 - the catalog is derived; a failed refresh is re-run next time
        pass
