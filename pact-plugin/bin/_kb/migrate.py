"""KB-MIGRATE: the deterministic page migration (the design notes section 9.2,
step 2). Frontmatter-only and add-only.

Location: pact-plugin/bin/_kb/migrate.py. Run once on the KB, before the curation apply:
  python3 migrate.py plan --out PLAN.json [--archive-pages DIR]   read-only; the plan and its counts
  python3 migrate.py apply --plan PLAN.json                        writes the planned lines
  python3 migrate.py projects --out DRAFT.json                     the _kb/projects.json draft

What it adds, per page (never changing an existing key, a body byte or a line ending):
  - `wiki-summary`: the page's effective MOC line, VERBATIM (the later line wins, exactly as
    vault-find's parse_moc reads it), so KB-MOC's pre-flight equality (section 6.5) holds;
  - `wiki-first-seen` on topic pages (those with `wiki-split-from`): the earliest `created` among
    the split sources, read from the live KB or, for sources a curation archived, from the archive.
It skips and reports: a page with no MOC line (no summary is invented); a page D25 withholds; a
summary that trips credential_reason or scrub_text; a summary no quoting can carry exactly; a page
that already has the key; a page whose bytes changed between the plan and the apply.

THE QUOTING RULE (the parser contract KB-MOC and the curation apply must share). A `wiki-summary`
value is always quoted, and must read back EXACTLY through both vault-find's split_frontmatter
(which strips quote characters from both ends of a value) and yaml.safe_load (Obsidian):
  1. a summary that starts or ends with `"` or `'` cannot be carried: skip;
  2. no `"` and no backslash: write "<summary>" (YAML double quotes);
  3. otherwise, no `'`: write '<summary>' (YAML single quotes, where a backslash is literal);
  4. otherwise (both quote kinds): skip and report.
Every candidate is round-tripped through both parsers before it is planned; a mismatch skips.

Scope (D26): the KB root comes from the one resolver (vault-find's vault_root); nothing outside
it is read or written, except the optional read-only archive of pages a curation removed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

BIN_DIR = Path(__file__).resolve().parent.parent
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
from _kb import lessons as L  # noqa: E402

try:
    import yaml
except ImportError:            # the apply refuses without it; the plan reports it
    yaml = None

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
WIKILINK_STEM = re.compile(r"^\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]$")
SUMMARY_KEY, FIRST_SEEN_KEY = "wiki-summary", "wiki-first-seen"
COUNT_KEYS = ("pages", "summarised", "summary_over_160", "kept_existing_summary", "topic_pages", "first_seen",
              "first_seen_used_archive", "kept_existing_first_seen")


def load_vault_find():
    loader = importlib.machinery.SourceFileLoader("vault_find_for_migrate", str(BIN_DIR / "vault-find"))
    spec = importlib.util.spec_from_loader("vault_find_for_migrate", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


VF = load_vault_find()


class MigrateError(Exception):
    """The plan cannot be built or applied safely; nothing was written."""


# ---------------------------------------------------------------------------------------------
# Reading a page without touching its bytes
# ---------------------------------------------------------------------------------------------

def read(path: Path) -> tuple[bytes, str]:
    data = path.read_bytes()
    return data, data.decode("utf-8")


def frontmatter_close(lines: list[str]) -> Optional[int]:
    """The index of the closing `---` line (lines split on "\\n"; a CRLF line keeps its "\\r")."""
    if not lines or lines[0].rstrip("\r") != "---":
        return None
    return next((i for i in range(1, len(lines)) if lines[i].rstrip("\r") == "---"), None)


def meta_of(text: str) -> dict:
    return VF.split_frontmatter(text.replace("\r\n", "\n"))[0]


def has_key(lines: list[str], close: int, key: str) -> bool:
    return any(line.startswith(f"{key}:") for line in lines[1:close])


def stem_of(link: str) -> Optional[str]:
    match = WIKILINK_STEM.match(link.strip())
    return match.group(1).strip() if match else (link.strip() or None)


# ---------------------------------------------------------------------------------------------
# The quoting rule
# ---------------------------------------------------------------------------------------------

def quote(summary: str) -> Optional[str]:
    """The frontmatter value that reads back as exactly `summary` in both parsers, or None."""
    if not summary or summary[0] in "\"'" or summary[-1] in "\"'" or "\n" in summary or "\r" in summary:
        return None
    if '"' not in summary and "\\" not in summary:
        value = f'"{summary}"'
    elif "'" not in summary:
        value = f"'{summary}'"
    else:
        return None
    return value if round_trips(summary, value) else None


def round_trips(summary: str, value: str) -> bool:
    line = f"{SUMMARY_KEY}: {value}"
    if VF.split_frontmatter(f"---\n{line}\n---\n")[0].get(SUMMARY_KEY) != summary:
        return False
    if yaml is None:
        return False
    try:
        return yaml.safe_load(line).get(SUMMARY_KEY) == summary
    except yaml.YAMLError:
        return False


# ---------------------------------------------------------------------------------------------
# Plan (read-only)
# ---------------------------------------------------------------------------------------------

def _created(path: Path) -> Optional[str]:
    try:
        value = meta_of(path.read_bytes().decode("utf-8", errors="replace")).get("created")
    except OSError:
        return None
    return value if isinstance(value, str) and DATE.match(value) else None


def _archive_index(archive_pages: Optional[Path]) -> dict:
    if not archive_pages or not Path(archive_pages).is_dir():
        return {}
    return {path.stem: path for path in Path(archive_pages).rglob("*.md") if path.is_file() and not path.is_symlink()}


def first_seen(meta: dict, live: dict, archived: dict) -> tuple[Optional[str], list[dict]]:
    """(the earliest `created` among the split sources, the per-source record)."""
    sources = []
    for link in VF.as_list(meta.get("wiki-split-from")):
        stem = stem_of(link)
        if not stem:
            continue
        where = "live" if stem in live else ("archive" if stem in archived else "missing")
        created = _created(live[stem] if where == "live" else archived[stem]) if where != "missing" else None
        sources.append({"stem": stem, "from": where, "created": created})
    dates = [source["created"] for source in sources if source["created"]]
    return (min(dates) if dates else None), sources


def plan(vault: Optional[Path] = None, archive_pages: Optional[Path] = None) -> dict:
    vault = Path(vault) if vault else VF.vault_root()
    moc = VF.parse_moc(vault)
    pages = [(rel, path) for rel, path, _ in VF.iter_pages(vault)]
    live = {path.stem: path for _, path in pages}
    archived = _archive_index(archive_pages)
    result = {"vault": str(vault), "yaml_available": yaml is not None, "pages": {},
              "no_moc_line": [], "withheld": [], "summary_withheld": [], "unquotable": [],
              "first_seen_underivable": [], "no_frontmatter": []}
    counts = defaultdict(int, {key: 0 for key in COUNT_KEYS})
    for rel, path in pages:
        counts["pages"] += 1
        data, text = read(path)
        lines = text.split("\n")
        close = frontmatter_close(lines)
        if close is None:
            result["no_frontmatter"].append(rel)
            continue
        if VF.credential_reason(rel, text):               # D25: the page is withheld; never touched
            result["withheld"].append(rel)
            continue
        meta, add, entry = meta_of(text), [], {}
        summary = moc.get(path.stem)
        if has_key(lines, close, SUMMARY_KEY):
            counts["kept_existing_summary"] += 1
        elif summary is None:
            result["no_moc_line"].append(rel)
        elif VF.credential_reason(rel, summary) or VF.scrub_text(summary) != summary:
            result["summary_withheld"].append(rel)
        else:
            value = quote(summary)
            if value is None:
                result["unquotable"].append(rel)
            else:
                add.append(f"{SUMMARY_KEY}: {value}")
                counts["summarised"] += 1
                counts["summary_over_160"] += len(summary) > 160
        if meta.get("wiki-split-from"):
            counts["topic_pages"] += 1
            if has_key(lines, close, FIRST_SEEN_KEY):
                counts["kept_existing_first_seen"] += 1
            else:
                date, sources = first_seen(meta, live, archived)
                entry["first_seen_sources"] = sources
                if date:
                    add.append(f"{FIRST_SEEN_KEY}: {date}")
                    counts["first_seen"] += 1
                    counts["first_seen_used_archive"] += any(s["from"] == "archive" for s in sources)
                else:
                    result["first_seen_underivable"].append(rel)
        if add:
            entry.update({"sha256": hashlib.sha256(data).hexdigest(), "add": add})
            result["pages"][rel] = entry
        elif entry:
            result["pages"][rel] = entry
    for key in ("no_moc_line", "withheld", "summary_withheld", "unquotable", "first_seen_underivable",
                "no_frontmatter"):
        counts[key] = len(result[key])
    counts["pages_to_write"] = sum(1 for entry in result["pages"].values() if entry.get("add"))
    result["counts"] = dict(counts)
    return result


# ---------------------------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------------------------

def migrated(text: str, add: list[str]) -> str:
    """`text` with `add` inserted just before the closing frontmatter line, in the page's own
    line ending. Verified: the old lines survive in order and the body is byte-identical."""
    lines = text.split("\n")
    close = frontmatter_close(lines)
    if close is None:
        raise MigrateError("no frontmatter")
    cr = "\r" if lines[0].endswith("\r") else ""
    new_lines = lines[:close] + [line + cr for line in add] + lines[close:]
    new = "\n".join(new_lines)
    if new_lines[:close] != lines[:close] or new_lines[close + len(add):] != lines[close:]:
        raise MigrateError("the insert would move an existing line")
    if "\n".join(new_lines[close + len(add):]) != "\n".join(lines[close:]):
        raise MigrateError("the body would change")
    if [unit_key(u) for u in L.lessons(new, "p")] != [unit_key(u) for u in L.lessons(text, "p")]:
        raise MigrateError("the lessons would change")
    return new


def unit_key(unit) -> tuple:
    return (unit.id, unit.title, unit.ctx, unit.state)


def apply(vault: Optional[Path] = None, the_plan: Optional[dict] = None, originals: Optional[Path] = None) -> dict:
    if yaml is None:
        raise MigrateError("PyYAML is not importable, so the quoting cannot be verified; nothing written")
    vault = Path(vault) if vault else VF.vault_root()
    if the_plan.get("vault") and os.path.realpath(the_plan["vault"]) != os.path.realpath(vault):
        raise MigrateError(f"the plan is for {the_plan['vault']}, not {vault}; nothing written")
    result = {"written": 0, "changed_since_plan": [], "refused": [], "applied": {}}
    for rel, entry in sorted(the_plan["pages"].items()):
        add = entry.get("add") or []
        if not add:
            continue
        path = vault / rel
        if path.is_symlink() or not VF.is_within(path, vault) or not path.is_file():
            result["refused"].append(rel)
            continue
        data, text = read(path)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            result["changed_since_plan"].append(rel)
            continue
        try:
            new = migrated(text, add)
        except MigrateError as err:
            result["refused"].append(f"{rel}: {err}")
            continue
        if originals is not None:
            keep = Path(originals) / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_bytes(data)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.migrate.tmp")
        tmp.write_bytes(new.encode("utf-8"))
        os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        if path.read_bytes() != data:                      # re-read right before the swap
            tmp.unlink()
            result["changed_since_plan"].append(rel)
            continue
        os.replace(tmp, path)
        result["written"] += 1
        result["applied"][rel] = {"before": entry["sha256"], "after": hashlib.sha256(new.encode()).hexdigest()}
    return result


# ---------------------------------------------------------------------------------------------
# The _kb/projects.json draft (section 4.4): a script draft; the lead does the judgement pass
# ---------------------------------------------------------------------------------------------

NON_PROJECT = re.compile(r"^(\.|general$|misc$|unknown$|various$|agent$)", re.I)
PATH_PREFIX = re.compile(r"^(?:users?-[^-]+-)?(?:documents-)?(?:.*-projects?-)?", re.I)


def context_slug(context: str) -> Optional[str]:
    if NON_PROJECT.match(context.strip()):
        return None
    flat = re.sub(r"[^a-z0-9]+", "-", context.strip().lower()).strip("-")
    flat = PATH_PREFIX.sub("", flat).strip("-") if "-" in flat else flat
    return flat or None


def draft_projects(vault: Optional[Path] = None) -> dict:
    vault = Path(vault) if vault else VF.vault_root()
    counts: dict = defaultdict(int)
    for rel, path, _ in VF.iter_pages(vault):
        text = path.read_bytes().decode("utf-8", errors="replace")
        if VF.credential_reason(rel, text):
            continue
        for unit in L.lessons(text, rel):
            if unit.ctx:
                counts[unit.ctx] += 1
    contexts = {ctx: {"slug": context_slug(ctx), "lessons": n} for ctx, n in sorted(counts.items())}
    slugs = defaultdict(list)
    for ctx, row in contexts.items():
        if row["slug"]:
            slugs[row["slug"]].append(ctx)
    return {"note": "DRAFT for the lead's one judgement pass (schema section 4.4); not written to the KB",
            "contexts": contexts, "merged": {s: c for s, c in sorted(slugs.items()) if len(c) > 1},
            "counts": {"contexts": len(contexts), "slugs": len(slugs),
                       "null": sum(1 for row in contexts.values() if row["slug"] is None)}}


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="migrate.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--out", required=True)
    p.add_argument("--archive-pages")
    a = sub.add_parser("apply")
    a.add_argument("--plan", required=True)
    a.add_argument("--originals", help="keep each page's previous bytes here, for rollback")
    a.add_argument("--result")
    j = sub.add_parser("projects")
    j.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            out = plan(archive_pages=Path(args.archive_pages) if args.archive_pages else None)
        elif args.command == "apply":
            out = apply(the_plan=json.loads(Path(args.plan).read_text()),
                        originals=Path(args.originals) if args.originals else None)
        else:
            out = draft_projects()
    except (MigrateError, VF.VaultScopeError) as err:
        print(f"migrate: {err}", file=sys.stderr)
        return 1
    target = getattr(args, "out", None) or getattr(args, "result", None)
    if target:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out.get("counts") or {k: v for k, v in out.items() if k != "applied"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
