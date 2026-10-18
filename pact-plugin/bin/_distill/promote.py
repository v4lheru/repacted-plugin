"""Per-repo promote: copy KB lessons a human accepted into a repository, one confirmed item
at a time, so collaborators get them.

Location: pact-plugin/bin/_distill/promote.py. `repact-distill promote` calls
add_parser() and cli(); tests call run() with a fake terminal and a fake git.

Source: the sealed KB-GATE decided records (<kb>/_kb/queue/decided/YYYY-MM/<id>.json) whose seal
verifies and whose decision is `accepted` or `edited` by a named human; the auto-accept bar is
never a source. The records carry no repository binding, by design: the human's choice at the
prompt, item by item, is the selection.
Landing: <repo>/.repacted/kb/<id>.md, one file per lesson, named by the record id (never by the
title). Each file says the text is model-written and human-reviewed, names its origin, and holds
the lesson as escaped <recalled-lesson> data elements, so nothing in it reads as an instruction.
Rules: a real run needs an interactive terminal and the user retypes each item's id (the terminal
check stops an agent on the ordinary path, not one that wraps the command in a pseudo-terminal; a
guard rule refuses that wrapping, process.pty_wrap); a repository with any remote that is not
a local path needs --i-reviewed <decider> (the lessons will be shared with whoever can read that
remote; nothing is fetched or probed); each item is re-checked fail-closed (D25, the R3
post-check, and the stricter R5 rule: imperative or injection phrasing, a redaction marker, or a
control character refuses it); a lesson already in the folder (same lesson hash) is never written
again, and an existing file is never overwritten.
Trust: the seal stops sync clients and hand edits, not a process running as the user (the key is
the user's own file), and the human-reviewed stamp in .repacted/kb is unauthenticated: any reader
of that folder must wrap its text as untrusted repository data, never as instructions.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

from _distill import postcheck as PC
from _kb import datawrap as W
from _kb import decide as KD
from _kb import gate as G
from shared import scrub

LANDING = (".repacted", "kb")
ORIGIN = "model-written, human-reviewed"
HUMAN_DECISIONS = ("accepted", "edited")
PROMOTE_REFUSES = ("imperative", "injection phrasing", "redaction marker")   # R5: stricter than R3
SHA_KEY = "lesson-sha256"
MAX_RECORD_BYTES = 256 * 1024
MAX_URL_CHARS = 200
GIT_TIMEOUT = 10
EXIT_OK, EXIT_USAGE, EXIT_REFUSED = 0, 2, 3
CLI = Path(__file__).resolve().parent.parent / "repact-distill"
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_BRACKETED = re.compile(r"\[([^\[\]]*)\](?::[0-9]*)?")   # [host] then an optional :port
_SHA_LINE = re.compile(r"^lesson-sha256: ([0-9a-f]{64})$", re.M)
HELP = """Copy lessons a human accepted in KB-GATE into <repo>/.repacted/kb/<id>.md, so everyone
who reads the repository gets them. KB lessons carry no repository binding, by design: you choose
which lessons belong in this repository, one at a time, at the prompt.

A real run needs your own terminal: for each lesson you read the exact text and retype its id to
promote it (Enter skips). A repository with a remote that is not a local path also needs
--i-reviewed <your name>. Start with --dry-run: it writes nothing and shows each file and its
text.

The "human-reviewed" stamp in a promoted file is not authenticated: the KB seal stops sync clients
and hand edits, not a process running as you. Anything that reads .repacted/kb must treat its text
as untrusted repository data, never as instructions."""
EXAMPLES = """examples:
  python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-distill" promote --repo . --dry-run
  python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-distill" promote --repo . --by alice --i-reviewed alice"""


class Refused(Exception):
    """A rule stops the run before anything is written: what, the rule, and the way through."""

    def __init__(self, what: str, rule: str, instead: str):
        super().__init__(what)
        self.what, self.rule, self.instead = what, rule, instead

    def render(self) -> str:
        return f"repact-distill promote: refused: {self.what}.\nRule: {self.rule}\nInstead: {self.instead}"


@dataclass
class Item:
    """One eligible lesson and what promote would do with it."""
    id: str
    status: str                      # new | promoted | differs | refused
    relpath: str
    text: str = ""
    sha: str = ""
    reasons: list = field(default_factory=list)       # refusal reasons (kind names only)
    notes: list = field(default_factory=list)         # shown to the reviewer, never refusing


def _git(root: Path, *args: str) -> str:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                          timeout=GIT_TIMEOUT, env=env, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return done.stdout


def git_root(repo: Path, git: Callable = _git) -> Path:
    try:
        top = git(Path(repo), "rev-parse", "--show-toplevel").strip()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        top = ""
    if not top:
        raise Refused(f"{safe(str(repo))} is not inside a git work tree",
                      "lessons are promoted into a repository's work tree only",
                      "pass --repo with a path inside the repository")
    return Path(top).resolve()


def remote_urls(root: Path, git: Callable = _git) -> list:
    """Every fetch and push URL of every remote, after git's insteadOf rewrites."""
    try:
        names = [name for name in git(root, "remote").split("\n") if name.strip()]
        urls = []
        for name in names:
            found = [url for flags in (("--all",), ("--push", "--all"))
                     for url in git(root, "remote", "get-url", *flags, name).split("\n") if url.strip()]
            urls += found or [""]          # a remote with no readable URL is not assumed local
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise Refused("the repository's remotes could not be read",
                      "a remote decides who will read the promoted lessons; one that cannot be "
                      "read is not assumed to be absent", "fix `git remote -v` in the repository, "
                      "then run again") from None
    return list(dict.fromkeys(url.strip() for url in urls))


def is_local_url(url: str) -> bool:
    """git's own rule: file:// or a filesystem path is local; a scheme or a scp-like host:path
    (a colon before any slash) is not. An empty URL is not local (fail closed)."""
    url = url.strip()
    if not url:
        return False
    if url.lower().startswith("file://"):
        return True
    if _SCHEME.match(url):
        return False
    colon, slash = url.find(":"), url.find("/")
    return colon == -1 or (slash != -1 and slash < colon)


def _bracketed_host_ok(netloc: str) -> bool:
    """True when the netloc has no bracket, or its host part (after the last '@') is one
    bracketed IPv6 address (a zone id allowed) with an optional numeric port. urlsplit checks
    this itself only from Python 3.11, so it is checked here on every version; IPvFuture, IPv4
    and a bracket in the userinfo (refused by 3.14, accepted before) are refused everywhere."""
    userinfo, _, hostport = netloc.rpartition("@")
    if "[" in userinfo or "]" in userinfo:
        return False
    if "[" not in hostport and "]" not in hostport:
        return True
    match = _BRACKETED.fullmatch(hostport)
    if not match:
        return False
    try:
        return isinstance(ipaddress.ip_address(match.group(1)), ipaddress.IPv6Address)
    except ValueError:
        return False


def display_url(url: str) -> str:
    """The URL with any user, password, query and fragment removed, control characters replaced
    and length capped; withheld whole when a credential still shows."""
    url = url.strip()
    if not url:
        return "(a remote with no URL)"
    if _SCHEME.match(url):
        try:
            parts = urlsplit(url)
        except ValueError:          # unbalanced brackets (every version) or a bad host (3.11+)
            parts = None
        if parts is None or not _bracketed_host_ok(parts.netloc):
            return "<a remote URL that could not be parsed; withheld>"
        host = parts.netloc.rsplit("@", 1)[-1]
        shown = f"{parts.scheme}://{host}{parts.path}"
    else:                           # scp-like [user[:password]@]host:path, or a one-slash scheme
        colon = url.rfind(":")
        cut = url.rfind("@", 0, colon) if colon != -1 else url.rfind("@")
        host, _, path = url[cut + 1:].partition(":")
        if "@" in path:             # a user:password@ the cut did not reach ('https:/u:p@h/x')
            return "<a remote URL that looks like it carries a credential; withheld>"
        shown = f"{host}:{path.split('?', 1)[0]}"
    shown = safe(shown)[:MAX_URL_CHARS]
    if scrub.secret_kind(shown) or scrub.credential_reason("remote", shown):
        return "<a remote URL that looks like it carries a credential; withheld>"
    return shown


def shared_remotes(root: Path, git: Callable = _git) -> list:
    """The displayed URLs of the remotes that share the repository beyond this disk."""
    return list(dict.fromkeys(display_url(url) for url in remote_urls(root, git)
                              if not is_local_url(url)))


def safe(text: str) -> str:
    """Text fit for a terminal: control and format characters (escapes, bidi) become '?'."""
    return "".join("?" if unicodedata.category(ch) in ("Cc", "Cf") and ch != "\n" else ch
                   for ch in str(text))


def _has_control(text: str) -> bool:
    return any(unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


def _read_record(path: Path) -> Optional[dict]:
    try:
        if path.is_symlink() or path.lstat().st_size > MAX_RECORD_BYTES:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not G.verify_seal(record) or record.get("id") != path.stem:
        return None
    return record


def header_problem(record: dict) -> Optional[str]:
    """Why a record's header values may not be copied into a promoted file's front matter, or
    None: each must match the gate's own pattern (a sealed record is not proof the gate wrote it)."""
    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    checks = (("id", G.ID_RE, record.get("id")), ("decided-by", G.DECIDER_RE, decision.get("by")),
              ("decision at", G.AT_RE, decision.get("at")))
    for name, pattern, value in checks:
        if not isinstance(value, str) or not pattern.fullmatch(value):
            return f"malformed record header ({name})"
    if decision.get("decision") not in HUMAN_DECISIONS:
        return "malformed record header (decision)"
    return None


def _is_human_decision(record: dict) -> bool:
    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    by = decision.get("by")
    return (decision.get("decision") in HUMAN_DECISIONS and isinstance(by, str)
            and by != G.AUTO_DECIDER and bool(G.DECIDER_RE.match(by)))


def eligible_records(kb: Path, ids: tuple = ()) -> tuple:
    """(records a human accepted or edited, count of files skipped as unsealed, corrupt, too
    large or symlinked). Directories are walked without following symlinks."""
    root = G.decided_dir(kb)
    if G.queue_dir(kb).is_symlink() or root.is_symlink() or not root.is_dir():
        return [], 0
    records, seen, skipped = [], set(), 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not (Path(folder) / d).is_symlink())
        for name in sorted(files):
            stem = name[:-5] if name.endswith(".json") else ""
            if not G.ID_RE.fullmatch(stem) or stem in seen or (ids and stem not in ids):
                continue
            seen.add(stem)
            record = _read_record(Path(folder) / name)
            if record is None:
                skipped += 1
            elif _is_human_decision(record):
                records.append(record)
    return sorted(records, key=lambda r: r["id"]), skipped


def lesson_fields(record: dict) -> Optional[dict]:
    """The shared text of a lesson: no evidence pointers (they name private sessions and notes) and
    no page summary (a lesson appended to an existing page has none)."""
    lesson = record.get("lesson") if isinstance(record.get("lesson"), dict) else {}
    evidence = lesson.get("evidence") if isinstance(lesson.get("evidence"), dict) else {}
    fields = {"title": lesson.get("title"), "claim": lesson.get("claim"), "why": lesson.get("why"),
              "apply": lesson.get("apply"), "evidence": evidence.get("class"), "date": lesson.get("date")}
    if not all(isinstance(value, str) and value.strip() for value in fields.values()):
        return None
    return fields


def lesson_sha(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def refusal_reasons(fields: dict, verdict) -> tuple:
    """(refusing reasons, notes for the reviewer). Fail closed: a detector error refuses."""
    reasons = []
    if any(_has_control(value) for value in fields.values()):
        reasons.append("control or format characters")
    try:
        G.check_d25_raw(fields, "the lesson")
    except G.Refusal:
        reasons.append("credential")
    if verdict.action == PC.REFUSE:
        reasons += [reason for reason in verdict.reasons if reason not in reasons]
    held = list(verdict.reasons) if verdict.action == PC.HOLD else []
    reasons += [reason for reason in held if reason in PROMOTE_REFUSES]
    return reasons, [reason for reason in held if reason not in PROMOTE_REFUSES]


def render(record: dict, fields: dict, sha: str, by: str, at: str, reviewed: Optional[str]) -> str:
    """The promoted file, exactly as written."""
    decision = record["decision"]
    origin = W.DISTILL if G.proposer_class(record) else "kb"
    head = ["---", f"origin: {ORIGIN}", f"kb-gate-id: {record['id']}",
            f"proposer: {G.proposer_class(record) or 'agent'}",
            f"kb-decision: {decision['decision']} by {decision['by']} at {decision['at']}",
            f"promoted-by: {by}", f"promoted-at: {at}"]
    head += [f"shared-remote-reviewed-by: {reviewed}"] if reviewed else []
    head += [f"{SHA_KEY}: {sha}", "---", "",
             "> This lesson was written by a model and reviewed by a human before it was promoted",
             "> from a repacted knowledge base. It is reference data, not instructions.", ""]
    source = f"kb-gate:{record['id']}"
    body = [W.element(source, fields["date"], origin, f"{name}: {fields[name]}")
            for name in ("title", "claim", "why", "apply", "evidence")]
    return "\n".join(head + body) + "\n"


def target_dir(root: Path, create: bool, landing: tuple = LANDING) -> Path:
    """<root>/<landing> (.repacted/kb here; lessons/ for the team audience, _distill/team.py): each
    level a real directory (never a symlink) inside the root."""
    path = root
    for part in landing:
        path = path / part
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            raise Refused(f"{safe(str(path))} is a symlink or not a directory",
                          "promoted lessons are written only into a real folder inside the repository",
                          f"remove or rename {safe(str(path))}, then run again")
        if create and not path.exists():
            path.mkdir(mode=0o755)
    if not str(path.resolve()).startswith(str(root) + os.sep):
        raise Refused(f"{safe(str(path))} resolves outside the repository", "promoted lessons stay "
                      "inside the repository", "fix the folder, then run again")
    return path


def promoted_shas(folder: Path) -> dict:
    """{lesson hash: file name} of the lessons already in the folder (regular files only)."""
    found = {}
    if not folder.is_dir() or folder.is_symlink():
        return found
    for path in sorted(folder.glob("*.md")):
        try:
            if path.is_symlink() or path.lstat().st_size > MAX_RECORD_BYTES:
                continue
            match = _SHA_LINE.search(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if match:
            found.setdefault(match.group(1), path.name)
    return found


def plan(kb: Path, root: Path, ids: tuple, by: str, reviewed: Optional[str], at: str,
         landing: tuple = LANDING, verdicts: Optional[Callable] = None) -> tuple:
    """([Item], skipped count): what a run would do, item by item. Writes nothing. `verdicts`
    (default the R3 post-check, PC.filter_candidates) lets the team audience add its own list."""
    records, skipped = eligible_records(kb, ids)
    folder = target_dir(root, create=False, landing=landing)
    known = promoted_shas(folder)
    checked = (verdicts or PC.filter_candidates)([lesson_fields(r) or {} for r in records])
    items = []
    for record, (_fields, verdict) in zip(records, checked):
        relpath = "/".join((*landing, f"{record['id']}.md"))
        fields = lesson_fields(record)
        problem = header_problem(record) or (None if fields else "malformed lesson")
        if problem:
            items.append(Item(record["id"], "refused", relpath, reasons=[problem]))
            continue
        sha = lesson_sha(fields)
        reasons, notes = refusal_reasons(fields, verdict)
        status = "refused" if reasons else "promoted" if sha in known else \
            "differs" if (folder / f"{record['id']}.md").exists() else "new"
        text = render(record, fields, sha, by, at, reviewed) if status == "new" else ""
        items.append(Item(record["id"], status, relpath, text, sha, reasons, notes))
    return items, skipped


def confirm(item_id: str, stdin, err) -> bool:
    """True only when the user retypes the item id at the prompt (Enter or anything else skips)."""
    print(f"Type {item_id} to promote this lesson, or press Enter to skip: ", end="", file=err, flush=True)
    try:
        return stdin.readline().strip() == item_id
    except (OSError, ValueError):
        return False


def write_item(root: Path, item: Item, landing: tuple = LANDING) -> bool:
    """O_EXCL and O_NOFOLLOW: never overwrite, never follow a planted link. False when it exists."""
    folder = target_dir(root, create=True, landing=landing)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(folder / f"{item.id}.md", flags, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(item.text)
    return True


def _remote_line(remotes: list, reviewed: Optional[str]) -> str:
    if not remotes:
        return "remote: none that leaves this disk (local only)"
    who = f"reviewed by {reviewed}" if reviewed else "a real run needs --i-reviewed <decider>"
    return f"remote: shared through {', '.join(remotes)} ({who})"


def _report(items: list, skipped: int, remotes: list, reviewed: Optional[str], out) -> None:
    print(_remote_line(remotes, reviewed), file=out)
    counts = {status: sum(item.status == status for item in items)
              for status in ("new", "promoted", "differs", "refused")}
    print("lessons: " + ", ".join(f"{n} {status}" for status, n in counts.items())
          + (f"; {skipped} decided files skipped (unsealed, corrupt or too large)" if skipped else ""),
          file=out)
    for item in items:
        why = f" ({', '.join(item.reasons)})" if item.reasons else ""
        print(f"- {item.id}: {item.status}{why} -> {item.relpath}", file=out)


def _checked_names(dry_run: bool, by: Optional[str], reviewed: Optional[str]) -> tuple:
    if by is None and dry_run:
        by = "<--by>"
    else:
        by = KD.check_by(by)
    return by, (KD.check_by(reviewed) if reviewed is not None else None)


def _gate_real_run(root: Path, remotes: list, reviewed: Optional[str], stdin) -> None:
    again = f'python3 "{CLI}" promote --repo "{safe(str(root))}" --by <your name>'
    if remotes and not reviewed:
        raise Refused(f"promoting into {safe(str(root))}",
                      f"the repository has a remote that is not a local path ({', '.join(remotes)}); "
                      "promoted lessons will be shared with whoever can read it, so the human who "
                      "reviewed them is named", f"{again} --i-reviewed <your name> (see what would be "
                      "written first with --dry-run)")
    try:
        interactive = stdin.isatty()
    except (OSError, ValueError, AttributeError):
        interactive = False
    if not interactive:
        raise Refused("a promote without an interactive terminal",
                      "every lesson is read and confirmed by a human, item by item, at a terminal; "
                      "the terminal check stops an agent on the ordinary path (not one that wraps the "
                      "command in a pseudo-terminal, which a guard rule refuses)",
                      f"ask the user to run, in their own terminal: {again}"
                      f"{' --i-reviewed <your name>' if remotes else ''} (an agent may run --dry-run)")


def run(kb: Path, repo: Path, *, dry_run: bool, ids: tuple = (), by: Optional[str] = None,
        reviewed: Optional[str] = None, stdin=None, out=None, err=None,
        moment: Optional[datetime.datetime] = None, git: Callable = _git) -> int:
    """One promote pass. Raises Refused (a rule) or G.Refusal (a bad name)."""
    stdin, out, err = stdin or sys.stdin, out or sys.stdout, err or sys.stderr
    by, reviewed = _checked_names(dry_run, by, reviewed)
    bad = [i for i in ids if not G.ID_RE.fullmatch(i)]
    if bad:
        raise G.Refusal("usage", f"--id takes a KB-GATE item id such as 20261017T120000Z-0123abcd ({len(bad)} not)")
    root = git_root(repo, git)
    remotes = shared_remotes(root, git)
    if not dry_run:
        _gate_real_run(root, remotes, reviewed, stdin)
    at = G._iso(moment or G.now_utc())
    items, skipped = plan(Path(kb), root, tuple(ids), by, reviewed, at)
    _report(items, skipped, remotes, reviewed, out)
    if dry_run:
        for item in (i for i in items if i.status == "new"):
            print(f"\n=== would write {item.relpath} ===\n{safe(item.text)}", file=out)
        return EXIT_OK
    _promote_each(root, [i for i in items if i.status == "new"], stdin, out, err)
    return EXIT_OK


def _promote_each(root: Path, items: list, stdin, out, err) -> None:
    written = 0
    for item in items:
        notes = f"note: {', '.join(item.notes)} (check the text before you promote it)\n" if item.notes else ""
        print(f"\n=== {item.relpath} ===\n{safe(item.text)}{notes}", file=err)
        if not confirm(item.id, stdin, err):
            print(f"- {item.id}: skipped", file=out)
            continue
        if write_item(root, item):
            written += 1
            print(f"- {item.id}: written to {item.relpath}", file=out)
        else:
            print(f"- {item.id}: {item.relpath} appeared meanwhile; not overwritten", file=out)
    print(f"promoted {written} of {len(items)} new lessons; commit {'/'.join(LANDING)} when "
          "you are ready to share them", file=out)


def add_parser(sub) -> None:
    cmd = sub.add_parser("promote", help="copy KB lessons a human accepted into a repository, "
                         "one confirmed lesson at a time", description=HELP, epilog=EXAMPLES,
                         formatter_class=argparse.RawDescriptionHelpFormatter)
    cmd.add_argument("--repo", type=Path, required=True, help="a path inside the target repository")
    cmd.add_argument("--dry-run", action="store_true", help="show each file and its text; write nothing")
    cmd.add_argument("--id", action="append", dest="ids", default=[], help="only this KB-GATE item "
                     "id (repeatable)")
    cmd.add_argument("--by", help="who promotes (your name; required for a real run)")
    cmd.add_argument("--i-reviewed", dest="i_reviewed", metavar="DECIDER",
                     help="your name, required when the repository has a remote that is not a local path")


def cli(args, kb_root: Callable) -> int:
    """The `repact-distill promote` entry: exit 0, 2 (usage) or 3 (refused)."""
    try:
        kb = kb_root()
    except Exception:  # noqa: BLE001 - any KB seam failure means there is nothing to read
        print(Refused("promote with no knowledge folder", "lessons come from the KB-GATE decisions "
                      "in paths.kb", "set paths.kb in profile.json (see /repacted:init)").render(),
              file=sys.stderr)
        return EXIT_REFUSED
    try:
        return run(kb, args.repo, dry_run=args.dry_run, ids=tuple(args.ids), by=args.by,
                   reviewed=args.i_reviewed)
    except Refused as exc:
        print(exc.render(), file=sys.stderr)
        return EXIT_REFUSED
    except G.Refusal as exc:
        print(f"repact-distill promote: {exc}\n\n{EXAMPLES}", file=sys.stderr)
        return EXIT_USAGE
