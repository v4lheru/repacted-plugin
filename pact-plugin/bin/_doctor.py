"""The checks behind bin/repact-doctor (the design notes D5).

Used by bin/repact-doctor, which writes the result cache hooks/doctor_brief.py reads at
SessionStart. Stdlib only. Each check returns a Result (green, amber, red, or skip when it
cannot run). Rules: never print a secret value; never read a process command line; scan
the vault only inside the KB folder; a check that cannot run says skip, never green.
The slow checks (secrets, MCP scoping) reuse cached results for SLOW_MAX_AGE.

Map, in file order: Result/Context, 1 links, 2 memory split, 3 index budgets, kb, 4 secrets,
5 stale references, 6 release position and 9 gates, 10 project task logs, 7 MCP scoping,
8 user-owned actions, then running, caching and rendering.
See the design notes
"""
from __future__ import annotations

import datetime
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
if str(Path(__file__).resolve().parents[1] / "hooks") not in sys.path:   # bin -> shared, the allowed direction
    sys.path.insert(1, str(Path(__file__).resolve().parents[1] / "hooks"))
import _agentlinks  # noqa: E402
import _doctor_state as _ds  # noqa: E402
from _doctor_result import MAX_DETAIL, Result, _capped  # noqa: E402,F401  re-export: callers and tests name _doctor.Result
import _memdir  # noqa: E402
import _repact_launch  # noqa: E402
from _repact_common import (SCRUB_PATTERNS, SECRET_ONLY_KINDS, VaultScopeError,  # noqa: E402
                            _hit, kb_root, plugin_data_dir, task_store, vault_scope_root)
from shared.project_tasks import find_main_checkout  # noqa: E402
from shared.user_actions import is_open_user_action  # noqa: E402

CACHE_NAME = "doctor.json"
LOCK_NAME = "doctor.lock"
CACHE_VERSION = 1
SLOW_MAX_AGE = datetime.timedelta(days=7)
SLOW_CHECKS = ("secrets", "mcp")
MAINTAINER_ONLY = ("release", "gates", "state")  # only a checkout of the plugin's source repo
STATES = ("green", "amber", "red", "skip")
TEXT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml"}
RELEASE_WORKTREE = "release"
TAG_GLOB = "repacted-*"
GATES_FILE = Path("docs") / "release-gates.json"
BACKLOG_FILE = Path("docs") / "backlog.md"
SCOPE_MCP = Path("tools") / "apply" / "scope_mcp.py"
USER_OWNED = "USER-OWNED"
KB_BASELINE_NAME = "kb-baseline.json"
# Absolute or home paths a memory cites. Scratch and temp locations are ephemeral by design,
# and anything with a placeholder or glob is not a concrete path, so neither is a finding.
PATH_RE = re.compile(r"(?<![\w/.~-])((?:~|/Users|/opt|/usr/local|/Library|/Applications)/[^\s`'\"()<>\[\]{}|,;*$]+)")
# Historical by nature: temp and scratch space, per-session task/team/session dirs, worktrees and
# the versioned plugin cache. A memory naming one records history, not a live dependency.
EPHEMERAL = ("/tmp/", "/private/", "/var/folders/", "/scratchpad/", "/.claude/tasks/", "/.claude/teams/",
             "/.claude/pact-sessions/", "/.worktrees/", "/.claude/worktrees/", "/.claude/plugins/cache/")
GLOB_OR_SLOT = "*?<{["            # a cited path followed by one of these is a pattern, not a file
VERSION_PLACEHOLDER = re.compile(r"X\.Y|@X\b")
# Memory hygiene marks a vanished path instead of deleting the sentence (class d); the marker must
# follow the cited path directly (a closing quote or backtick may sit between them).
GONE_MARKER = re.compile(r"^[`'\"]?\s*\(path gone as of \d{4}-\d{2}-\d{2}\)")
# A cite that names a file on another machine (a VM's ~/extraction.log) is not a local path. Write
# "(remote host)" or "(not a local path)" ANYWHERE on its line (a shell comment works inside a code
# block): every cite on that line is then skipped.
NOT_LOCAL_MARKER = re.compile(r"\((?:remote host|not a local path)\)", re.IGNORECASE)
# A quoted cite on a line that says it was cut short quotes a fragment, not a file.
TRUNCATION_CUE = re.compile(r"truncat|cut off|cut short|cut at", re.IGNORECASE)
QUOTE_CHARS = "`'\""
MAX_SPACED = 8
# Illustrative paths in prose ("/Users/me/path/file.json", "~/.foo/config.json") cite nothing.
PLACEHOLDER_PARTS = {"path", "to", "foo", "bar", "baz", "example", "myapp", "your", "you", "me", "x", "xxx", "user"}
SPACED_STOP = re.compile(r"[`'\"()<>\[\]{}|,;*$]")
BIN_RE = re.compile(r"(?:\$\{CLAUDE_PLUGIN_ROOT\}|pact-plugin)/bin/([A-Za-z0-9_][A-Za-z0-9_.-]*)")


@dataclass
class Context:
    config_root: Path
    home: Path
    plugin_root: Path
    env: Dict[str, str]
    repo_root: Optional[Path] = None

    @property
    def memory_dirs(self) -> List[Path]:
        dirs = [self.config_root / "agent-memory"]
        dirs += sorted((self.config_root / "projects").glob("*/memory"))
        return [d for d in dirs if d.is_dir()]


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def iso(moment: datetime.datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _main_checkout(start: Path) -> Optional[Path]:
    """shared.project_tasks.find_main_checkout; None when there is no repo (the repo checks skip)."""
    try:
        return find_main_checkout(start)
    except Exception:  # noqa: BLE001 - no repo is the safe answer: the repo checks skip
        return None


def context_from_env(env: Optional[Dict[str, str]] = None, cwd: Optional[Path] = None) -> Context:
    """The repo under check is the main checkout of the working directory (1.4.1): the
    plugin's own repo is not every project's, and its backlog must not reach them."""
    env = dict(os.environ if env is None else env)
    config_root, _ = _memdir.resolve_root(env)
    plugin_root = Path(__file__).resolve().parents[1]
    override = env.get("REPACT_DOCTOR_REPO")  # tests only
    repo = Path(override) if override else _main_checkout(cwd or Path.cwd())
    return Context(config_root=config_root, home=Path(env.get("HOME") or Path.home()),
                   plugin_root=plugin_root, env=env,
                   repo_root=repo if repo is not None and (repo / ".git").exists() else None)


def _sha(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 1. links (and the native core route of CORE-ROUTE)
# ---------------------------------------------------------------------------

def _link_paths(ctx: Context) -> List[Path]:
    links = sorted(p for p in (ctx.config_root / "agents").glob("pact-*.md") if p.is_symlink())
    roots = [ctx.home / ".claude"]
    if os.path.realpath(ctx.config_root) != os.path.realpath(ctx.home / ".claude"):
        roots.append(ctx.config_root)
    return links + [r / _agentlinks.PROTOCOLS_LINK for r in roots
                    if os.path.lexists(r / _agentlinks.PROTOCOLS_LINK)]


def _owner(link: Path, roots: Dict[str, Optional[Path]]) -> str:
    real = os.path.realpath(link)
    if not os.path.exists(real):
        return "dangling"
    for label, root in roots.items():
        base = os.path.realpath(root) if root else None
        if base and (real == base or real.startswith(base + os.sep)):
            return label
    return "other"


def _core_findings(ctx: Context, installed: bool) -> List[str]:
    """The native core route swaps nothing: the global file must never carry the core, and in
    a repact session the staged copy must match its source. Installed mode makes the core the
    global file on purpose (apply_core), so it is not checked there."""
    if installed:
        return []
    source = _repact_launch.core_source(ctx.plugin_root, ctx.env)
    source_sha = _sha(source)
    if source_sha is None:
        return []
    found = []
    if _sha(ctx.config_root / "CLAUDE.md") == source_sha:
        found.append(f"red: {ctx.config_root / 'CLAUDE.md'} IS the slim core; nothing should write "
                     "it (the core is served per session); restore the user's file")
    if ctx.env.get("REPACTED_LAUNCH") == "alternate":
        staged = _repact_launch.core_dir(ctx.env) / "CLAUDE.md"
        if _sha(staged) != source_sha:
            found.append(f"amber: the staged core {staged} differs from {source}; "
                         "the next repact launch restages it")
    return found


def check_links(ctx: Context) -> Result:
    installed = _repact_launch.launch_mode(ctx.config_root) == "installed"
    expected = "repacted" if installed or ctx.env.get("REPACTED_LAUNCH") == "alternate" else "pact"
    roots: Dict[str, Optional[Path]] = {"repacted": ctx.plugin_root, "pact": None}
    notes = []
    try:
        roots["pact"] = _agentlinks.pact_install_root(ctx.config_root)
    except _memdir.Refusal as exc:
        notes.append(f"PACT root unknown: {exc}")
    owners: Dict[str, List[Path]] = {}
    for link in _link_paths(ctx):
        owners.setdefault(_owner(link, roots), []).append(link)
    core = _core_findings(ctx, installed)
    detail = [f"{link}: dangling" for link in owners.get("dangling", [])]
    detail += [f"{link}: points outside both roots" for link in owners.get("other", [])]
    detail += [line.split(": ", 1)[1] for line in core] + notes
    counts = ", ".join(f"{len(v)} -> {k}" for k, v in sorted(owners.items())) or "no links"
    products = {k for k in owners if k in ("repacted", "pact")}
    if owners.get("dangling") or len(products) > 1 or any(c.startswith("red") for c in core):
        why = "mixed roots" if len(products) > 1 else "dangling link(s)" if owners.get("dangling") \
            else "the global CLAUDE.md carries the core"
        return Result("links", "links", "red", f"{why} ({counts})", _capped(detail))
    if products and expected not in products:
        detail.insert(0, f"links point at {products.pop()} but the launcher in use expects {expected}; "
                         "a crashed repact session? see repacted-memdir --status")
        return Result("links", "links", "amber", f"not at {expected} ({counts})", _capped(detail))
    if owners.get("other") or core or (expected == "pact" and roots["pact"] is None and owners):
        return Result("links", "links", "amber", f"check the details ({counts})", _capped(detail))
    return Result("links", "links", "green", f"{counts}; core served per session, not swapped")


# ---------------------------------------------------------------------------
# 2. memory split, 3. index budgets
# ---------------------------------------------------------------------------

def check_split(ctx: Context) -> Result:
    memory = ctx.config_root / "agent-memory"
    sides = _memdir.side_paths(memory, _memdir.DEFAULT_NAME)
    if all(p.is_dir() and not p.is_symlink() for p in sides.values()):
        return Result("split", "memory split", "red", "both orchestrator memory dirs exist",
                      [_memdir.split_message(memory, _memdir.DEFAULT_NAME)])
    leftovers = _memdir.split_files(memory, _memdir.DEFAULT_NAME)
    if leftovers:
        return Result("split", "memory split", "amber", f"{len(leftovers)} *.split-* file(s) to reconcile",
                      _capped([str(p) for p in leftovers]))
    return Result("split", "memory split", "green", "one orchestrator memory dir")


def check_index(ctx: Context) -> Result:
    root = ctx.config_root / "agent-memory"
    if not root.is_dir():
        return Result("index", "index budgets", "skip", f"no agent-memory dir at {root}")
    proc = subprocess.run([sys.executable, str(ctx.plugin_root / "bin" / "check-index"), "--all",
                           "--root", str(root), "--quiet"], capture_output=True, text=True, timeout=120)
    if proc.returncode == 3:
        return Result("index", "index budgets", "skip", "no memory index found")
    heads = sorted({line.split(": ", 1)[0] for line in proc.stdout.splitlines()
                    if line.split(": ", 1)[0].endswith("/MEMORY.md")})
    if proc.returncode not in (0, 1):
        return Result("index", "index budgets", "skip", f"check-index exited {proc.returncode}")
    if not heads:
        return Result("index", "index budgets", "green", "every memory index within budget")
    return Result("index", "index budgets", "amber", f"{len(heads)} index(es) over budget",
                  _capped([f"{h} (check-index {root / Path(h).parent})" for h in heads]))


# ---------------------------------------------------------------------------
# kb: the KB catalog's health
# ---------------------------------------------------------------------------

_VAULT_FIND = None


def _vault_find():
    """bin/vault-find as a module: its refresh pass builds the index and the catalog."""
    global _VAULT_FIND
    if _VAULT_FIND is None:
        loader = importlib.machinery.SourceFileLoader("_doctor_vault_find",
                                                      str(Path(__file__).resolve().parent / "vault-find"))
        spec = importlib.util.spec_from_loader("_doctor_vault_find", loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        _VAULT_FIND = module
    return _VAULT_FIND


def _kb_catalog():
    from _kb import catalog
    return catalog


def _broken_baseline(count: int) -> int:
    """The ratchet: the lowest broken-link count seen so far. The first run records the current
    count; a lower count lowers it; a higher one is reported against it."""
    path = plugin_data_dir() / KB_BASELINE_NAME
    try:
        baseline = int(json.loads(path.read_text(encoding="utf-8"))["broken_links"])
    except (OSError, ValueError, KeyError, TypeError):
        baseline = None
    if baseline is None or count < baseline:
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"broken_links": count}) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return count
    return baseline


def check_kb(ctx: Context) -> Result:
    try:
        vault = vault_scope_root()
    except VaultScopeError as exc:
        return Result("kb", "knowledge base", "skip", f"KB not checked: {exc}")
    if not vault.is_dir():
        return Result("kb", "knowledge base", "skip", f"KB folder {vault} not found")
    finder, catalog = _vault_find(), _kb_catalog()
    try:
        index_path = finder.default_index_path()
        index = finder.refresh_index(vault, index_path)
        path = finder.catalog_path(index_path)
        catalog.refresh(vault, path, index, None, finder.BODY_CHARS, finder.EXCLUDE_CREDENTIALS)
        health = catalog.load(path)["health"]
    except Exception as exc:  # noqa: BLE001 - a failed build is the finding
        return Result("kb", "knowledge base", "amber",
                      f"catalog stale: its refresh failed ({exc.__class__.__name__}: {exc})",
                      ["vault-find --catalog rebuilds it and shows the error"])
    reach, keys = health["lesson_reach"], health["key_coverage"]
    summary = (f"{health['pages']['total']} pages, {health['lessons']['total']} lessons, reach "
               f"{catalog._percent(reach['reached'], reach['lessons'])}, keys {keys['with_keys']}/{keys['pages']}, "
               f"orphans {health['orphans']}, broken links {health['broken_links']['count']}, "
               f"withheld {health['withheld']['count']}")
    red, amber = [], []
    chains = health["chains"]["broken"] + health["chains"]["cyclic"]
    if chains:
        red.append(f"{chains} supersession chain(s) broken or cyclic: vault-find --catalog chains")
    schema = health["schema"]
    if schema["new_errors"]:
        amber.append(f"{schema['new_errors']} schema error(s) on {schema['new_pages_with_errors']} page(s) "
                     f"created since {catalog.CONTRACT_DATE}: vault-find --catalog lint")
    broken = health["broken_links"]["count"]
    baseline = _broken_baseline(broken)
    if broken > baseline:
        amber.append(f"{broken} broken link(s), above the baseline of {baseline}: vault-find --catalog broken")
    if health["projects_map"] == "invalid":
        amber.append("_kb/projects.json is not a JSON object of slugs: vault-find --catalog")
    queue = _kb_queue(vault)            # KB-GATE: nothing waits unseen (contract section 7.5)
    if queue and queue["count"]:
        summary += f", queue {queue['count']}"
        if queue["stale"]:
            amber.append(f"KB queue: {queue['count']} item(s), the oldest {queue['oldest_days']} days old: "
                         "vault-note queue, then drain them at wrap-up")
    if queue and queue.get("corrupt"):
        amber.append(f"KB queue: {queue['corrupt']} corrupt or unsealed file(s), never decided or applied: "
                     "vault-note queue lists them")
    state = "red" if red else "amber" if amber else "green"
    return Result("kb", "knowledge base", state, summary, red + amber)


def _kb_queue(vault: Path) -> Optional[dict]:
    try:
        from _kb import gate as kb_gate
        return kb_gate.queue_health(vault)
    except Exception:  # noqa: BLE001 - the queue count is advice; the catalog result stands without it
        return None


# ---------------------------------------------------------------------------
# 4. secrets (slow): memory dirs, the pact-memory DB, the vault KB folder only
# ---------------------------------------------------------------------------

def _text_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() in TEXT_SUFFIXES and not path.is_symlink():
                yield path


def _kinds(text: str) -> List[str]:
    """Every credential VALUE kind in text: credential_reason's value pass (the one exclusion
    test, D9.12), without its title pass. A note merely titled 'API keys' holds no value, and
    this check reports values. scan_text is not used because it skips scan=False classes, the
    inert test marker among them. test_repact_doctor pins the parity with credential_reason."""
    kinds = set()
    for pattern in SCRUB_PATTERNS:
        if pattern.kind not in SECRET_ONLY_KINDS:
            continue
        for match in pattern.regex.finditer(text):
            if _hit(pattern, match):
                kinds.add(pattern.kind)
                break
    return sorted(kinds)


def _scan_db(db: Path) -> List[str]:
    """Hits in the TEXT columns of every ordinary table, as `db:table#rowid (kinds)`."""
    hits: List[str] = []
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tables = [n for n, sql in con.execute("select name, sql from sqlite_master where type='table'")
                  if sql and not sql.upper().startswith("CREATE VIRTUAL")]
        for table in tables:
            try:
                cols = [c[1] for c in con.execute(f'pragma table_info("{table}")') if "TEXT" in (c[2] or "").upper()]
                if not cols:
                    continue
                select = ", ".join(f'"{c}"' for c in cols)
                for row in con.execute(f'select rowid, {select} from "{table}"'):
                    kinds = _kinds("\n".join(v for v in row[1:] if isinstance(v, str)))
                    if kinds:
                        hits.append(f"{db}:{table}#{row[0]} ({', '.join(kinds)})")
            except sqlite3.Error:
                continue  # a table needing an unloaded module (vec0) is not text
    finally:
        con.close()
    return hits


def secret_scan_roots(ctx: Context) -> tuple:
    """(memory roots, the vault root or None, notes). The vault root is the KB folder or a
    narrowing of it, never wider (D26)."""
    notes = []
    vault = None
    try:
        candidate = vault_scope_root()
        if candidate.is_dir():
            vault = candidate
        else:
            notes.append(f"vault KB folder {candidate} not found; not scanned")
    except VaultScopeError as exc:
        notes.append(f"vault not scanned: {exc}")
    return list(ctx.memory_dirs), vault, notes


def _file_hits(root: Path) -> tuple:
    hits, scanned = [], 0
    for path in _text_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        kinds = _kinds(text)
        if kinds:
            hits.append(f"{path} ({', '.join(kinds)})")
    return hits, scanned


def check_secrets(ctx: Context) -> Result:
    """Memory-dir and pact-memory DB hits are RED: agent memory must never hold a credential.
    Vault KB hits are AMBER (lead ruling): under D26 they are the user's own notes, and D25
    already withholds each such note from recall, so they are reported, not alarmed."""
    memory_roots, vault, notes = secret_scan_roots(ctx)
    hits, kb_hits, scanned = [], [], 0
    for root in memory_roots:
        found, count = _file_hits(root)
        hits, scanned = hits + found, scanned + count
    if vault is not None:
        kb_hits, count = _file_hits(vault)
        scanned += count
    db = ctx.config_root / "pact-memory" / "memory.db"
    if db.is_file():
        try:
            hits += _scan_db(db)
            scanned += 1
        except sqlite3.Error as exc:
            notes.append(f"pact-memory DB not scanned: {exc.__class__.__name__}")
    if not scanned:
        return Result("secrets", "secrets", "skip", "nothing to scan", notes)
    kb_part = f"{len(kb_hits)} KB note(s) withheld from recall (D25)"
    kb_detail = [f"KB (withheld from recall): {h}" for h in kb_hits]
    if hits:
        summary = f"credential values in {len(hits)} memory/DB place(s)" + (f"; {kb_part}" if kb_hits else "")
        return Result("secrets", "secrets", "red", summary, _capped(hits + kb_detail) + notes)
    if kb_hits:
        return Result("secrets", "secrets", "amber", kb_part, _capped(kb_detail) + notes)
    return Result("secrets", "secrets", "amber" if notes else "green",
                  f"{scanned} file(s) and DB scanned, no credential value", notes)


# ---------------------------------------------------------------------------
# 5. stale references
# ---------------------------------------------------------------------------

def _expand(path: str, ctx: Context) -> str:
    return path.replace("~", str(ctx.home), 1) if path.startswith("~") else path


def private_vault(ctx: Context) -> Optional[tuple]:
    """(vault root, KB root) as strings, or None when the KB is off or has no enclosing vault.
    The vault is the KB folder's grandparent (<vault>/<area>/<kb>); a KB too shallow for one, or
    whose grandparent is the home folder or above it, has none (every citation is checked)."""
    try:
        kb = kb_root()
    except VaultScopeError:
        return None
    if len(kb.parents) < 2:
        return None
    vault, home = kb.parents[1], Path(os.path.realpath(ctx.home))
    return None if vault == home or vault in home.parents else (str(vault), str(kb))


HOME_PREFIX = re.compile(r"^(~|/Users/[^/]+)(?=/|$)")


def _canon_home(path: str, ctx: Context) -> str:
    """The citation with ONLY its home prefix ('~' or /Users/<name>) resolved through realpath:
    a home reached through a symlink is spelt both ways in memories. The rest of the
    path (the vault part) is never resolved, so no private-vault path is ever touched."""
    match = HOME_PREFIX.match(path)
    if not match:
        return path
    home = str(ctx.home) if match.group(1) == "~" else match.group(1)
    return os.path.realpath(home) + path[match.end(1):]


def _is_private(cited_tail: str, vault: Optional[tuple]) -> bool:
    """True for a citation inside the vault but outside the KB folder: the user's private space
    (D26), which is never stat-ed. Decided on the string alone, before any filesystem call."""
    if vault is None:
        return False
    root, kb = vault
    inside = lambda path, base: path == base or path.startswith(base.rstrip("/") + "/")
    return inside(cited_tail, root) and not inside(cited_tail, kb)


def _missing_citations(text: str, ctx: Context, vault: Optional[tuple] = None) -> tuple:
    """(cited paths that do not exist, count of private-vault citations not checked). A path
    with spaces ('Work Projects') is cut at the space by the pattern, so a missing candidate
    is retried with up to MAX_SPACED following words of its line; any existing extension means
    the citation is fine."""
    missing, private = [], 0
    for match in PATH_RE.finditer(text):
        cleaned = re.sub(r":\d+(?::\d+)?$", "", match.group(1).rstrip(".:"))
        rest = text[match.end():].split("\n", 1)[0]
        line = text[text.rfind("\n", 0, match.start()) + 1:match.end()] + rest
        if _not_a_citation(cleaned, rest) or NOT_LOCAL_MARKER.search(line):
            continue
        if ":" in cleaned:  # a PATH-style list: each element is its own citation
            for part in (p for p in cleaned.split(":") if p.startswith(("~", "/"))):
                if _is_private(_canon_home(part, ctx), vault):
                    private += 1
                elif not os.path.lexists(_expand(part, ctx)):
                    missing.append(_expand(part, ctx))
            continue
        if _is_private(_canon_home(cleaned + rest, ctx), vault):
            private += 1
            continue
        found, longest = _exists_with_spaces(cleaned, rest, ctx)
        tail = (cleaned + rest)[len(longest):] if (cleaned + rest).startswith(longest) else rest
        before = text[match.start() - 1:match.start()]
        if not found and not _is_ephemeral(longest) and not GONE_MARKER.match(tail) \
                and not _quoted_fragment(cleaned, before, rest, line, ctx):
            missing.append(_expand(longest, ctx))
    return missing, private


def _quoted_fragment(path: str, before: str, rest_of_line: str, line: str, ctx: Context) -> bool:
    """True for a cite quoted whole ('...', "..." or `...`) that is a fragment, not a file:
    its line says it was truncated or cut off, or it stops at a space inside an existing name
    (`/Users/you/Documents/Work` quoting what awk left of "Work Projects")."""
    if not before or before not in QUOTE_CHARS or not rest_of_line.startswith(before):
        return False
    if TRUNCATION_CUE.search(line):
        return True
    parent, _, stem = _expand(path, ctx).rstrip("/").rpartition("/")
    try:
        return bool(stem) and any(name.startswith(stem + " ") for name in os.listdir(parent or "/"))
    except OSError:
        return False


def _is_ephemeral(path: str) -> bool:
    """A whole ephemeral path segment, so '.../scratchpad' counts and '.../scratchpad-app' does not."""
    return any(e in path.rstrip("/") + "/" for e in EPHEMERAL)


def _not_a_citation(cleaned: str, rest_of_line: str) -> bool:
    """Elided, ephemeral, placeholder, version-slot or pattern text: it names no single file."""
    return ("..." in cleaned or "…" in cleaned or _is_ephemeral(cleaned)
            or any(part.lstrip(".").split(".")[0].lower() in PLACEHOLDER_PARTS for part in cleaned.split("/"))
            or bool(VERSION_PLACEHOLDER.search(cleaned))
            or (rest_of_line[:1] in GLOB_OR_SLOT and rest_of_line[:1] != ""))


def _exists_with_spaces(path: str, rest_of_line: str, ctx: Context) -> tuple:
    """(whether the path or a spaced extension of it exists, the longest candidate tried)."""
    if os.path.lexists(_expand(path, ctx)):
        return True, path
    extended = path
    if rest_of_line.startswith(" "):
        for word in rest_of_line.split(" ")[1:MAX_SPACED + 1]:
            piece = SPACED_STOP.split(word)[0]
            if not piece or piece == "\\":
                break  # nothing left, or a shell line-continuation (a lone ' - ' stays: it is in names)
            extended += " " + piece.rstrip(".:")
            if os.path.lexists(_expand(extended, ctx)):
                return True, extended
            if piece != word:
                break  # a quote or bracket closed the citation
    return False, extended


def check_stale_refs(ctx: Context) -> Result:
    stale: List[str] = []
    vault, private = private_vault(ctx), 0
    for root in ctx.memory_dirs:
        for path in sorted(root.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            cited, skipped = _missing_citations(text, ctx, vault)
            private += skipped
            missing = sorted(set(cited))
            missing += sorted({f"bin/{t}" for t in BIN_RE.findall(text)
                               if not (ctx.plugin_root / "bin" / t).exists()})
            stale += [f"{path}: {m}" for m in missing]
    if not ctx.memory_dirs:
        return Result("stale_refs", "stale references", "skip", "no memory dirs")
    not_checked = f"; {private} private-vault citation(s) not checked (D26)" if private else ""
    if stale:
        files = len({s.split(": ", 1)[0] for s in stale})
        return Result("stale_refs", "stale references", "amber",
                      f"{len(stale)} cited path(s) no longer exist, in {files} memory file(s){not_checked}",
                      _capped(stale))
    return Result("stale_refs", "stale references", "green", f"every checked cited path exists{not_checked}")


# ---------------------------------------------------------------------------
# 6. release position, 9. release gates
# ---------------------------------------------------------------------------

def _git(ctx: Context, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(["git", "-C", str(ctx.repo_root), *args], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def release_state(ctx: Context) -> Optional[dict]:
    """{release, head, ahead: [tags on main not in the release, oldest first]}, or None."""
    if ctx.repo_root is None:
        return None
    listing = _git(ctx, "worktree", "list", "--porcelain") or ""
    release = next((line.split(" ", 1)[1] for line in listing.splitlines()
                    if line.startswith("worktree ") and Path(line.split(" ", 1)[1]).name == RELEASE_WORKTREE), None)
    if release is None:
        return None
    head = _git(ctx, "-C", release, "rev-parse", "HEAD")
    tag = _git(ctx, "-C", release, "describe", "--tags", "--exact-match", "HEAD") if head else None
    ahead = (_git(ctx, "tag", "--list", TAG_GLOB, "--merged", "main", "--no-merged", head or "HEAD",
                  "--sort=creatordate") or "").split()
    return {"release": tag, "worktree": release, "head": head, "ahead": ahead}


def check_release(ctx: Context, state: Optional[dict]) -> Result:
    if state is None:
        return Result("release", "release position", "skip", "no repo or no release worktree")
    if state["release"] is None:
        return Result("release", "release position", "amber",
                      f"the release worktree {state['worktree']} is not on a tag")
    if state["ahead"]:
        return Result("release", "release position", "amber",
                      f"release at {state['release']}; main has {len(state['ahead'])} newer tag(s), "
                      f"latest {state['ahead'][-1]}")
    return Result("release", "release position", "green", f"release at {state['release']}, main's latest")


def read_gates(repo_root: Path) -> tuple:
    """({tag: [entry, ...]}, problems). The ruled shape: each tag maps to a LIST of
    {gate, task_id, status: pending|passed, evidence}; evidence is required once passed.
    An empty list declares that the tag needs no sandbox gate."""
    try:
        raw = json.loads((repo_root / GATES_FILE).read_text(encoding="utf-8")).get("gates", {})
    except (OSError, ValueError, AttributeError):
        return {}, [f"{GATES_FILE} missing or unreadable"]
    gates, problems = {}, []
    for tag, entries in (raw.items() if isinstance(raw, dict) else []):
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            problems.append(f"{tag}: not a list of gate entries")
            continue
        gates[tag] = entries
    return gates, problems


def _gate_label(tag: str, entry: dict) -> str:
    return f"{tag} ({entry.get('gate', '?')}, task #{entry.get('task_id', '?')})"


def check_gates(ctx: Context, state: Optional[dict]) -> Result:
    if state is None or state["release"] is None:
        return Result("gates", "release gates", "skip", "no release position to compare")
    gates, problems = read_gates(ctx.repo_root)
    pending, unproven, malformed = [], [], list(problems)
    for tag in state["ahead"]:
        for entry in gates.get(tag, []):
            status = entry.get("status")
            if status == "pending":
                pending.append(_gate_label(tag, entry))
            elif status == "passed" and not str(entry.get("evidence") or "").strip():
                unproven.append(_gate_label(tag, entry))
            elif status not in ("pending", "passed"):
                malformed.append(f"{_gate_label(tag, entry)}: status {status!r}")
    undeclared = [tag for tag in state["ahead"] if tag not in gates]
    uncut = [tag for tag, entries in gates.items() if tag not in state["ahead"] and tag != state["release"]
             and any(e.get("status") == "pending" for e in entries)]
    detail = [f"waiting on gate: {p}" for p in pending] + [f"passed WITHOUT evidence: {u}" for u in unproven]
    detail += [f"no gate declared: {t}" for t in undeclared] + [f"malformed: {m}" for m in malformed]
    detail += [f"declared, tag not cut yet: {t}" for t in uncut]
    parts = [f"{len(pending)} gate(s) pending"] if pending else []
    parts += [f"{len(unproven)} passed without evidence"] if unproven else []
    parts += [f"{len(undeclared)} tag(s) undeclared in {GATES_FILE}"] if undeclared else []
    parts += [f"{len(malformed)} malformed"] if malformed else []
    if parts:
        return Result("gates", "release gates", "amber", "; ".join(parts), _capped(detail))
    return Result("gates", "release gates", "green",
                  "every tag beyond the release has passed its gates, with evidence" if state["ahead"]
                  else "nothing beyond the release", _capped(detail))


# ---------------------------------------------------------------------------
# 10. The project task logs (D10 P3)
# ---------------------------------------------------------------------------

TASK_LOG = Path(".repacted") / "tasks.jsonl"
UNCOMMITTED_MAX_AGE = datetime.timedelta(hours=24)
LEGACY_BACKLOG = Path(".claude") / "pact-backlog"      # the retired home-pinned store, import only


def _task_modules():
    from _tasks import registry, views
    from shared.task_log import write
    return write, views, registry


def _registered_mains(registry) -> List[Path]:
    """Every checkout where `repact-task init` ran (a registry row with a project_id), from P6's
    machine registry; a registry that cannot be read contributes nothing (never a crash)."""
    try:
        rows = registry.load().get("checkouts", {})
    except registry.RegistryError:
        return []
    return [Path(row["repo_path"]) for row in rows.values()
            if isinstance(row, dict) and row.get("project_id") and isinstance(row.get("repo_path"), str)]


def _task_mains(ctx: Context, registry) -> List[Path]:
    """The repo under check, then every project registered with init run; each once."""
    candidates = ([ctx.repo_root] if ctx.repo_root else []) + _registered_mains(registry)
    mains, seen = [], set()
    for main in candidates:
        try:
            key = main.resolve()
        except OSError:
            continue
        if key not in seen and (key / TASK_LOG).is_file():
            seen.add(key)
            mains.append(key)
    return mains


def _committed_eids(main: Path) -> set:
    try:
        out = subprocess.run(["git", "-C", str(main), "show", f"HEAD:{TASK_LOG.as_posix()}"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return set()
    eids = set()
    for line in out.stdout.splitlines() if out.returncode == 0 else []:
        try:
            eid = json.loads(line).get("eid")
        except (ValueError, AttributeError):
            continue
        if isinstance(eid, str):
            eids.add(eid)
    return eids


def _oldest_uncommitted(main: Path, events: List[dict]) -> Optional[datetime.datetime]:
    committed = _committed_eids(main)
    stamps = [e["ts"] for e in events if e["eid"] not in committed]
    if not stamps:
        return None
    return datetime.datetime.strptime(min(stamps), "%Y-%m-%dT%H:%M:%S.%fZ").replace(
        tzinfo=datetime.timezone.utc)


def check_tasks(ctx: Context, now: Optional[datetime.datetime] = None) -> Result:
    """Corrupt lines (red); events uncommitted for over 24 h, hand-edited views and a legacy
    backlog store (amber); nothing to look at (skip)."""
    write, views, registry = _task_modules()
    now = now or utc_now()
    corrupt, stale, edited = [], [], []
    mains = _task_mains(ctx, registry)
    for main in mains:
        try:
            parsed = write.PT.read_log(main / TASK_LOG)
        except (write.PT.LogMissing, write.PT.LogUnreadable) as err:
            corrupt.append(f"{main}: the task log cannot be read ({err})")
            continue
        corrupt += [f"{main}: line {line}: corrupt ({reason})" for line, reason in parsed.corrupt]
        oldest = _oldest_uncommitted(main, parsed.events)
        if oldest is not None and now - oldest > UNCOMMITTED_MAX_AGE:
            hours = int((now - oldest).total_seconds() // 3600)
            stale.append(f"{main}: events uncommitted for {hours} h; run `repact-task commit`")
        try:
            configured = views.configured(main)
        except write.TaskError as err:
            edited.append(f"{main}: {err}")
            configured = {}
        edited += [f"{main}: {rel} was edited by hand; edits there are lost on the next render"
                   for name, rel in sorted(configured.items()) if views.hand_edited(main / rel)]
    legacy_dir = ctx.home / LEGACY_BACKLOG
    legacy = sorted(p.name for p in legacy_dir.glob("*.json")) if legacy_dir.is_dir() else []
    notes = [f"legacy backlog {legacy_dir / name}: run `repact-task import-pact-backlog "
             f"{legacy_dir / name}` in its project" for name in legacy]
    if not mains and not legacy:
        return Result("tasks", "project tasks", "skip", "no project task log here (repact-task init)")
    detail = corrupt + stale + edited + notes
    parts = [f"{len(corrupt)} corrupt line(s)"] if corrupt else []
    parts += [f"{len(stale)} log(s) with uncommitted events over 24 h"] if stale else []
    parts += [f"{len(edited)} view(s) edited by hand"] if edited else []
    parts += [f"{len(legacy)} legacy backlog store(s) to import"] if legacy else []
    if corrupt:
        return Result("tasks", "project tasks", "red", "; ".join(parts), _capped(detail))
    if parts:
        return Result("tasks", "project tasks", "amber", "; ".join(parts), _capped(detail))
    return Result("tasks", "project tasks", "green", f"{len(mains)} task log(s) clean and committed")


# ---------------------------------------------------------------------------
# 7. MCP scoping drift (slow)
# ---------------------------------------------------------------------------

def check_mcp(ctx: Context) -> Result:
    script = Path(ctx.env.get("REPACT_DOCTOR_SCOPE_MCP") or (ctx.repo_root / SCOPE_MCP if ctx.repo_root else ""))
    if not script or not script.is_file():
        return Result("mcp", "MCP scoping", "skip", "MCP scoping is not set up here")
    with tempfile.TemporaryDirectory(prefix="repact-doctor.") as tmp:
        out = Path(tmp) / "plan.json"
        cmd = [sys.executable, str(script), "--plan", "--out", str(out),
               "--config", str(ctx.home / ".claude.json"), "--projects", str(ctx.config_root / "projects"),
               "--settings", str(ctx.config_root / "settings.json")]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=ctx.env)
            plan = json.loads(out.read_text(encoding="utf-8")) if proc.returncode == 0 else None
        except (OSError, ValueError, subprocess.SubprocessError):
            plan = None
    if plan is None:
        return Result("mcp", "MCP scoping", "skip", "the scoping plan could not be computed")
    drift = [row["server"] for row in plan.get("servers", []) if row.get("effective") in ("scope", "remove")]
    unlisted = list(plan.get("unlisted_global_servers", []))
    if drift or unlisted:
        return Result("mcp", "MCP scoping", "amber",
                      f"{len(drift)} server(s) would move, {len(unlisted)} not in the scoping file",
                      _capped([f"would change: {s}" for s in drift] + [f"unlisted: {s}" for s in unlisted]))
    return Result("mcp", "MCP scoping", "green", "global MCP servers match the scoping file")


# ---------------------------------------------------------------------------
# 8. open user-owned actions
# ---------------------------------------------------------------------------

def _backlog_actions(ctx: Context) -> List[str]:
    if ctx.repo_root is None:
        return []
    try:
        lines = (ctx.repo_root / BACKLOG_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    actions = []
    for line in lines:
        if USER_OWNED not in line:
            continue
        bold = re.search(r"\*\*(.+?)\*\*", line)
        title = (bold.group(1) if bold else line.lstrip("-* ")).replace(f"({USER_OWNED})", "")
        actions.append(title.strip(" .:"))
    return actions


def _alert_tasks(ctx: Context) -> List[str]:
    """Open user-action TASKS, by D3's is_open_user_action, across every team's task dir."""
    is_open = is_open_user_action
    seen, actions = set(), []
    store = task_store(ctx.config_root)
    for team, task in ((team, task) for team in store.teams() for task in store.list(team)):
        if not is_open(task):
            continue
        subject = str(task.get("subject") or "")
        key = (team, str(task.get("id")))
        if key not in seen:
            seen.add(key)
            actions.append(f"#{task.get('id')} {subject} ({team})")
    return actions


def check_actions(ctx: Context) -> Result:
    alerts, backlog = _alert_tasks(ctx), _backlog_actions(ctx)
    items = alerts + backlog
    if not items:
        return Result("actions", "open user actions", "green", "none open")
    return Result("actions", "open user actions", "red" if alerts else "amber",
                  f"{len(items)} open ({len(alerts)} open user-action task(s), {len(backlog)} {USER_OWNED})", _capped(items))


# ---------------------------------------------------------------------------
# Running, caching, rendering
# ---------------------------------------------------------------------------

def cache_path() -> Path:
    return plugin_data_dir() / CACHE_NAME


def read_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get("version") == CACHE_VERSION else {}


def _cached_slow(cache: dict, check_id: str, now: datetime.datetime) -> tuple:
    """(the cached Result or None, whether it is younger than SLOW_MAX_AGE)."""
    for row in cache.get("checks", []):
        if row.get("id") == check_id and row.get("ran_at"):
            try:
                age = now - datetime.datetime.fromisoformat(row["ran_at"])
                fields = {k: row[k] for k in ("id", "name", "state", "summary", "detail", "ran_at")}
            except (ValueError, KeyError):
                return None, False
            return Result(**fields), age <= SLOW_MAX_AGE
    return None, False


def run_checks(ctx: Context, cache: dict, mode: str = "default",
               now: Optional[datetime.datetime] = None) -> List[Result]:
    """mode: 'default' (slow checks only when their cache is stale), 'full' (all), 'quick'
    (never the slow checks: their cached result is kept, or skip when there is none)."""
    now = now or utc_now()
    state = release_state(ctx)
    plan: List[tuple] = [("links", lambda: check_links(ctx)), ("split", lambda: check_split(ctx)),
                         ("index", lambda: check_index(ctx)), ("kb", lambda: check_kb(ctx)),
                         ("profile", lambda: _ds.check_profile(ctx)),
                         ("secrets", lambda: check_secrets(ctx)),
                         ("stale_refs", lambda: check_stale_refs(ctx)),
                         ("release", lambda: check_release(ctx, state)), ("mcp", lambda: check_mcp(ctx)),
                         ("actions", lambda: check_actions(ctx)), ("gates", lambda: check_gates(ctx, state)),
                         ("tasks", lambda: check_tasks(ctx, now)), ("state", lambda: _ds.check_state(ctx))]
    results = []
    for check_id, run in plan:
        if check_id in SLOW_CHECKS and mode != "full":
            cached, fresh = _cached_slow(cache, check_id, now)
            if mode == "quick" or fresh:
                results.append(cached or Result(check_id, check_id, "skip", "not run yet; run repact-doctor --full"))
                continue
        results.append(_guarded(check_id, run, now))
    return results


def _guarded(check_id: str, run: Callable[[], Result], now: datetime.datetime) -> Result:
    try:
        result = run()
    except Exception as exc:  # one broken check must not hide the others
        result = Result(check_id, check_id, "skip", f"check failed: {exc.__class__.__name__}: {exc}")
    result.ran_at = iso(now)
    return result


def write_cache(path: Path, results: List[Result], now: Optional[datetime.datetime] = None,
                repo: Optional[Path] = None) -> None:
    """`repo` is the repo the checks read: doctor_brief shows its repo-bound lines (backlog
    actions, release, gates) only in a session of that same repo."""
    payload = {"version": CACHE_VERSION, "generated": iso(now or utc_now()),
               "repo": str(repo) if repo is not None else None,
               "checks": [asdict(r) for r in results]}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def try_run_lock(data_dir: Path) -> Optional[int]:
    """An fd holding the doctor's run lock, or None when another doctor run holds it."""
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(data_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def render_report(results: List[Result]) -> List[str]:
    """The table; a maintainer-only row that does not apply is hidden (doctor.json keeps it)."""
    results = [r for r in results if not (r.state == "skip" and r.id in MAINTAINER_ONLY)]
    width = max((len(r.name) for r in results), default=0)
    lines = []
    for n, r in enumerate(results, 1):
        lines.append(f"{r.state.upper():5} {n}. {r.name:<{width}}  {r.summary}")
        lines += [f"        {d}" for d in r.detail] if r.state != "green" else []
    return lines
