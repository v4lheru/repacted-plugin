"""The project atlas: vault pages generated one-way from each repo's task log (D10 §15, D27).

Location: pact-plugin/bin/_tasks/atlas.py. Design: the design notes §15.
Used by: `repact-task init` (the project.json), `repact-task project`, `repact-task atlas` and
`repact-task commit`.

This module only BUILDS the region text. The one vault writer is `vault-note project-page`
(D9.7): it owns the scope pin to <kb_root>/Projects, the marker merge, the backup and the atomic
replace. Discovery is registry-only (bin/_tasks/registry.py): nothing scans the disk for repos.
Every generated line is checked for credentials and withheld whole when it trips (D25); then the
whole region is scrubbed as defence in depth.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import _repact_common as common
from _tasks import registry as R
from shared.task_log import write as W

PT = W.PT
BIN_DIR = Path(__file__).resolve().parent.parent
PROJECT_RELPATH = Path(".repacted") / "project.json"
DECISIONS_RELPATH = Path("docs") / "decisions.md"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MAX = 40
WHAT_MAX = 160
NAME_MAX = 80
STACK_MAX = 20
PROJECT_STATUSES = ("active", "paused", "done", "archived")
PROJECT_KEYS = ("id", "slug", "name", "what", "stack", "status", "key_decisions", "atlas")
PAGE_REGIONS = ("overview", "stack", "decisions", "next", "blocked", "kb-links")
WITHHELD = "[withheld: credential-bearing text; see the repo]"
# A marker word inside generated text would forge or break a region (architect R1): any case, any
# spacing around the colon. HTML comment brackets are defused too, so no line can open a comment.
MARKER_WORD = re.compile(r"(repacted)\s*:\s*(begin|end)", re.I)
TEXT_PATH = "repacted-atlas"        # the neutral path credential_reason checks lines against
NEXT_LIMIT = 10
# vault-note's exit for --restore on a page that exists: the one refusal that is a usage error (64);
# every other refusal (marker word, marker doubt, collision, scope) is refused data (65).
VAULT_NOTE_NOTHING_TO_RESTORE = 5


class AtlasError(W.TaskError):
    """The atlas refused, or vault-note refused its write: nothing was written."""
    code = 65


# ---------------------------------------------------------------------------------------------
# .repacted/project.json
# ---------------------------------------------------------------------------------------------

def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:SLUG_MAX].strip("-")
    return slug or "project"


def check_slug(slug: str) -> str:
    if not SLUG_RE.match(slug or "") or len(slug) > SLUG_MAX:
        raise W.Usage(f"slug {slug!r}: use [a-z0-9-], {SLUG_MAX} characters or fewer")
    return slug


def _problem(data) -> Optional[str]:
    if not isinstance(data, dict) or set(data) != set(PROJECT_KEYS):
        return f"keys must be exactly {', '.join(PROJECT_KEYS)}"
    try:
        uuid.UUID(str(data["id"]))
    except ValueError:
        return "id is not a UUID"
    if not isinstance(data["slug"], str) or not SLUG_RE.match(data["slug"]) or len(data["slug"]) > SLUG_MAX:
        return "slug is not [a-z0-9-] of 40 characters or fewer"
    if not isinstance(data["name"], str) or not isinstance(data["what"], str) or len(data["what"]) > WHAT_MAX:
        return f"name and what must be text (what: {WHAT_MAX} characters or fewer)"
    if not isinstance(data["stack"], list) or not all(isinstance(s, str) for s in data["stack"]):
        return "stack must be a list of text"
    if data["status"] not in PROJECT_STATUSES:
        return f"status must be one of {', '.join(PROJECT_STATUSES)}"
    if not isinstance(data["key_decisions"], list) or \
            not all(isinstance(d, str) and W.DECISION_RE.match(d) for d in data["key_decisions"]):
        return "key_decisions must be Dn ids"
    if not isinstance(data["atlas"], bool):
        return "atlas must be true or false"
    return None


def read_project(main: Path) -> Optional[dict]:
    """The project's metadata, or None when `.repacted/project.json` does not exist.

    Raises:
        W.Unreadable: the file exists but is not a valid project.json.
    """
    path = Path(main) / PROJECT_RELPATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as err:
        raise W.Unreadable(f"cannot read {path}: {err}") from err
    problem = _problem(data)
    if problem:
        raise W.Unreadable(f"{path} is not a valid project.json: {problem}")
    return data


def _write_project(main: Path, data: dict) -> None:
    problem = _problem(data)
    if problem:
        raise W.Usage(f"project.json: {problem}")
    W.check_text(data["name"], data["what"], *data["stack"])
    path = Path(main) / PROJECT_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({key: data[key] for key in PROJECT_KEYS}, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def ensure_project(main: Path) -> Tuple[dict, bool]:
    """(the project, whether it was created now). An existing project.json is kept as it is."""
    existing = read_project(main)
    if existing is not None:
        return existing, False
    name = Path(main).name
    data = {"id": str(uuid.uuid4()), "slug": slugify(name), "name": name[:NAME_MAX], "what": "",
            "stack": [], "status": "active", "key_decisions": [], "atlas": True}
    _write_project(main, data)
    return data, True


def edit_project(main: Path, what=None, stack=None, status=None, add_decisions=(),
                 drop_decisions=(), slug=None, name=None, atlas=None) -> dict:
    data = read_project(main)
    if data is None:
        raise W.NoLog(f"no {PROJECT_RELPATH} in {main}; run `repact-task init`")
    if what is not None:
        data["what"] = " ".join(what.split())
        if len(data["what"]) > WHAT_MAX:
            raise W.Usage(f"--what is {len(data['what'])} characters; the limit is {WHAT_MAX}")
    if stack is not None:
        data["stack"] = [item.strip() for item in stack.split(",") if item.strip()][:STACK_MAX]
    if status is not None:
        data["status"] = status
    for decision in add_decisions:
        if not W.DECISION_RE.match(decision):
            raise W.Usage(f"--decision {decision!r}: a decision id like D27")
        if decision not in data["key_decisions"]:
            data["key_decisions"].append(decision)
    data["key_decisions"] = [d for d in data["key_decisions"] if d not in set(drop_decisions)]
    if slug is not None:
        data["slug"] = check_slug(slug)
    if name is not None:
        data["name"] = " ".join(name.split())[:NAME_MAX]
    if atlas is not None:
        data["atlas"] = atlas
    _write_project(main, data)
    return data


def describe(data: dict) -> List[str]:
    return [f"{key}: {', '.join(value) if isinstance(value, list) else value}"
            for key, value in data.items()]


# ---------------------------------------------------------------------------------------------
# Region content
# ---------------------------------------------------------------------------------------------

def neutralize(line: str) -> str:
    """`repacted:end` -> `repacted end`, `<!--` -> `<!- -`: generated text can never be a marker."""
    return MARKER_WORD.sub(r"\1 \2", line).replace("<!--", "<!- -").replace("-->", "- ->")


def safe_region(lines: Iterable[str]) -> str:
    """Every generated line is neutralized (R1); a line that carries a credential is withheld
    whole (D25); then the region is scrubbed."""
    kept = [neutralize(line) for line in lines]
    kept = [WITHHELD if common.credential_reason(TEXT_PATH, line) else line for line in kept]
    return common.scrub_text("\n".join(kept))


def safe_text(text: str, fallback: str) -> str:
    return fallback if common.credential_reason(TEXT_PATH, text) else neutralize(text)


def _state(main: Path):
    try:
        parsed = PT.read_log(PT.log_path(main))
    except (PT.LogMissing, PT.LogUnreadable):
        return None, None
    stamps = [event.get("ts") for event in parsed.events if isinstance(event.get("ts"), str)]
    return PT.replay(parsed.events, parsed.corrupt), (max(stamps) if stamps else None)


def last_active(main: Path) -> Optional[str]:
    return _state(main)[1]


def decision_texts(main: Path) -> dict:
    """{Dn: one-line text} from the repo's docs/decisions.md table, when it exists."""
    path = Path(main) / DECISIONS_RELPATH
    if path.is_symlink() or not path.is_file() or not common.is_within(path, main):
        return {}
    texts = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and W.DECISION_RE.match(cells[0]):
            texts[cells[0]] = PT.one_line("|".join(cells[2:-1]).replace("**", ""), WHAT_MAX)
    return texts


def kb_link_line(kb: Path, target: str) -> str:
    """`[[wikilink]]` only when the kb: target resolves to a page inside the KB root (§15.7)."""
    relpath = target[len("kb:"):]
    page = kb / relpath
    if relpath.endswith(".md") and common.is_within(page, kb) and not page.is_symlink() and page.is_file():
        return f"- [[{relpath[:-3]}]]"
    return f"- {relpath} (not a page in the KB)"


def page_regions(main: Path, project: dict, kb: Optional[Path]) -> dict:
    state, active_at = _state(main)
    tasks = state.tasks.values() if state else ()
    overview = [project["what"] or "(no description yet: repact-task project --what \"...\")", "",
                f"- status: {project['status']}", f"- repo: `{main}`",
                f"- last active: {active_at or 'never'}"]
    stack = [f"- {item}" for item in project["stack"]] or ["- (none recorded)"]
    texts = decision_texts(main)
    decisions = [f"- {d}: {texts[d]}" if d in texts else f"- {d}" for d in project["key_decisions"]] \
        or ["- (none recorded)"]
    if state is None:
        nxt = blocked = ["- (no task log)"]
    else:
        nxt = [f"- {PT.render_line(state, task)}" for task in PT.ready(state)[:NEXT_LIMIT]] \
            or ["- (nothing ready)"]
        blocked = [f"- {PT.render_line(state, task)}  {kind}" + (f": {', '.join(who)}" if who else "")
                   for task, kind, who in PT.blocked(state)] or ["- (nothing blocked)"]
    targets = sorted({link["target"] for task in tasks for link in task["links"]
                      if str(link.get("target", "")).startswith("kb:")})
    kb_links = [kb_link_line(kb, target) for target in targets] if kb else []
    regions = {"overview": overview, "stack": stack, "decisions": decisions, "next": nxt,
               "blocked": blocked, "kb-links": kb_links or ["- (none)"]}
    return {name: safe_region(regions[name]) for name in PAGE_REGIONS}


def _link_text(text: str) -> str:
    return re.sub(r"[\[\]|]", " ", text).strip()


def index_region(rows: List[dict]) -> str:
    lines = []
    for row in sorted(rows, key=lambda r: (r["name"].lower(), r["slug"] or "")):
        stack = ", ".join(row["stack"]) or "-"
        lines.append(f"- [[Projects/{row['slug']}/index|{_link_text(row['name']) or row['slug']}]]"
                     f" · {row['status']} · {row['what'] or '-'} · stack: {stack}"
                     f" · repo: `{row['repo_path']}` · last active: {row['last_active'] or 'never'}")
    return safe_region(lines or ["- (no projects registered)"])


# ---------------------------------------------------------------------------------------------
# Discovery (registry only) and the vault-note hand-off
# ---------------------------------------------------------------------------------------------

def _known(main: Path, project: dict) -> dict:
    return {"name": project["name"], "what": project["what"], "stack": list(project["stack"]),
            "status": project["status"], "last_active": last_active(main), "atlas": project["atlas"]}


def _other_project(main: Path) -> Tuple[Optional[dict], str]:
    """(project, status) for a registered checkout other than the current one."""
    if not Path(main).is_dir():
        return None, "missing"
    try:
        project = read_project(Path(main))
    except W.Unreadable:
        return None, "unreadable"
    return project, ("missing" if project is None else project["status"])


def plan(start: Path, forget: Optional[str] = None, restore: Optional[str] = None) -> dict:
    """Everything one atlas run would hand to vault-note, computed without writing anything."""
    main = PT.find_main_checkout(start)
    project = read_project(main) if main else None
    current = R.key(main) if project else None
    checkouts = {k: v for k, v in R.load()["checkouts"].items() if not forget or v.get("slug") != forget}
    if current and current not in checkouts:
        checkouts[current] = {"repo_path": current, "project_id": project["id"], "slug": None,
                              "last_known": None}
    rows, known, owners = [], {}, {}
    for key, row in sorted(checkouts.items()):
        if not row.get("project_id"):
            continue                              # the bootstrap offer was declined here
        repo = Path(row["repo_path"])
        found, status = (project, project["status"]) if key == current else _other_project(repo)
        if found is not None:
            if not found["atlas"]:
                continue
            known[key] = _known(repo, found)
            slug = found["slug"] if key == current else (row.get("slug") or found["slug"])
            values = dict(known[key], status=status)
        else:
            values = dict(row.get("last_known") or {}, status=status)
            if values.get("atlas") is False:
                continue
            slug = row.get("slug")
        if not slug:
            continue
        pid = found["id"] if found else row["project_id"]
        other = owners.setdefault(slug, (pid, row["repo_path"]))
        if other[0] != pid:
            raise AtlasError(f"slug {slug} is claimed by two projects: {other[1]} and {row['repo_path']}; "
                             "nothing written. Rename one with `repact-task project --slug <new>`")
        rows.append({"slug": slug, "name": safe_text(values.get("name") or slug, slug),
                     "what": values.get("what") or "", "stack": values.get("stack") or [],
                     "status": values["status"], "repo_path": row["repo_path"],
                     "last_active": values.get("last_active")})
    request = {"pages": [], "index": {"regions": {"projects": index_region(rows)}}}
    if project and project["atlas"]:
        kb = common.vault_scope_root()
        previous = checkouts[current].get("slug")
        request["pages"].append({
            "slug": project["slug"], "project_id": project["id"],
            "title": safe_text(project["name"], project["slug"]),
            "regions": page_regions(main, project, kb),
            "rename_from": previous if previous and previous != project["slug"] else None})
    if restore:
        pages = [page["slug"] for page in request["pages"]] + [R.INDEX_PAGE]
        if restore not in pages:
            raise W.Usage(f"--restore {restore}: run it from that project's checkout "
                          f"(here: {', '.join(pages)})")
        request["restore"] = [restore]
    return {"main": main, "project": project, "current": current, "known": known,
            "request": request, "forget": forget}


def _vault_note(request: dict, dry_run: bool) -> List[str]:
    command = [sys.executable, str(BIN_DIR / "vault-note"), "project-page"] + (["--dry-run"] if dry_run else [])
    done = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True)
    lines = done.stdout.splitlines()
    if done.returncode != 0:
        reason = " ".join(done.stderr.split()) or f"vault-note exited {done.returncode}"
        error = W.Usage if done.returncode == VAULT_NOTE_NOTHING_TO_RESTORE else AtlasError
        raise error("\n".join(lines + [f"atlas not written: {reason}"]))
    return lines


def run(start: Path, dry_run: bool = False, forget: Optional[str] = None,
        restore: Optional[str] = None) -> List[str]:
    """Render the current project's page (when there is one) and _index.md through vault-note."""
    try:
        work = plan(start, forget, restore)
    except R.RegistryError as err:
        raise W.Unreadable(str(err)) from err
    except common.VaultScopeError as err:
        raise AtlasError(f"{err}; nothing written") from err
    lines = _vault_note(work["request"], dry_run)
    if dry_run:
        return lines + ([f"dry-run: would forget {forget}"] if forget else [])
    if forget:
        lines.append(f"forgot {R.forget(forget)} checkout(s) registered as {forget}")
    main, project, current = work["main"], work["project"], work["current"]

    def remember(data):
        for key, values in work["known"].items():
            row = data["checkouts"].get(key)
            if row is not None:
                row["last_known"] = values
        if current:
            row = R.checkout_row(data, main)
            row["project_id"] = project["id"]
            row["init_at"] = row.get("init_at") or R.now()
            row["slug"] = project["slug"] if project["atlas"] else row.get("slug")
            row["last_known"] = _known(main, project)
    R.update(remember)
    return lines


def after_commit(main: Path) -> List[str]:
    """`repact-task commit`'s atlas step. It runs only once the atlas has written on this machine
    (the first write is deliberate: `repact-task atlas --dry-run`, then `repact-task atlas`), and a
    refusal is reported, never allowed to undo the commit."""
    try:
        project = read_project(main)
        if project is None or not project["atlas"]:
            return []
        if R.backup_record() is None:
            return ["atlas: not written yet on this machine; preview with `repact-task atlas --dry-run`, "
                    "then run `repact-task atlas` once"]
        return [f"atlas: {line}" for line in run(main)]
    except (W.TaskError, R.RegistryError, OSError, common.VaultScopeError) as err:
        return [f"atlas: not written: {' '.join(str(err).split())}"]
