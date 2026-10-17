"""Importers into the project task log (D10 P4a; design section 8, section 10 as amended by the
lead's gate-model ruling amend_1 on #186).

Location: pact-plugin/bin/_tasks/importers.py. Used by bin/repact-task.
  import-md            a markdown file (backlog.md, progress.md) -> a PROPOSAL: one item per
                       top-level bullet or table row; credential-bearing items are withheld by
                       line number only (D25); each item keeps a reference link to its archived
                       line (doc:docs/archive/<file>#L<n>), so no prose is lost.
  import-gates         docs/release-gates.json -> a release task per tag (tags release and
                       release.<tag>) with each gate a child task tagged gate. A task_id becomes
                       a ptask:<team>/<id> link (needs --team); a passed gate imports as done only
                       when its evidence verifies, otherwise the entry is refused by line.
  import-pact-backlog  a legacy ~/.claude/pact-backlog/<project>.json -> tasks with a legacy:<id>
                       link. The source is opened read-only and never moved or edited.
Every importer is a DRY RUN until apply(): parse_* only reads, table() only formats. apply()
re-validates every item through the writer's own rules (a proposal can be hand-edited), then
writes everything in ONE locked append, skipping items already imported (so a second apply
writes nothing). Every link an importer creates passes D26 (check_target), and the importers are
the only writers allowed a legacy: link.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional

BIN_DIR = Path(__file__).resolve().parent.parent
for _path in (BIN_DIR, BIN_DIR.parent / "hooks"):   # bin -> shared, the allowed direction
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
import _repact_common as common  # noqa: E402
from shared.task_log import write as W  # noqa: E402
from shared.task_log import evidence as _evidence  # noqa: E402

PT = W.PT

ARCHIVE_DIR = "docs/archive"
LEGACY_DIR = Path(".claude") / "pact-backlog"
TEAM_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
BULLET_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
CLAUSE_END = re.compile(r"(?<=\S)(?:\.\s|:\s|;\s)")
SECTION_KINDS = (("in flight", "inflight"), ("waiting on the user", "user"),
                 ("release gates", "gates"), ("resuming after a break", "resume"))
SKIP_REASONS = {"gates": "release gates come from import-gates",
                "resume": "static block in the generated view"}
DONE_STATES = ("done", "merged")
# A blockquoted bullet (`> - ...`) is a line of a log or status digest: it becomes a NOTE naming its
# line, never a task, unless it is a queue line. A queue line (lead "Next", "Next queue", "Queue",
# "Up next"; quoted or not) lists work in order, so each item between top-level , and ; is a task.
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
QUEUE_LEAD = re.compile(r"^(?:next(?:\s+(?:queue|up))?|queue|up\s+next)$", re.IGNORECASE)
LOG_NOTE = "a blockquoted log line, kept in the archive (not a task)"
# A bold lead that only states a fact is not work: `<name> is at <ref>`, or a short lead (at most
# STATUS_MAX_WORDS words) ending in a completion or state word ("Done and merged", "Measured",
# "The importer's scheduled jobs are DISABLED"). A bullet with a DONE marker is done WORK, never status.
STATE_WORDS = frozenset({"done", "merged", "measured", "committed", "accepted", "shipped", "landed",
                         "released", "deployed", "applied", "disabled", "enabled", "passed",
                         "completed", "finished"})
STATUS_MAX_WORDS = 6
STATUS_REASON = "a status line: it states a fact, not a work item"
# `DONE (<label>, <refs>)` or `<subject>: DONE (<refs>)`: the subject is the title, each sha or tag
# in the parentheses is evidence, and the label goes to the note (a label is not an evidence target).
DONE_LEAD = re.compile(r"^(?:(?P<subject>.+?):?\s+)?DONE\s*\((?P<refs>[^)]*)\)[:.]?$")
# The dry run warns when the file's LAST heading (level 2+) scopes at least this share of the items:
# that is the shape of items appended at the end of a file under an unrelated heading, which the
# text alone cannot tell from a genuinely large last section (lead ruling (B) on #236).
LAST_SECTION_WARN_FRACTION = Fraction(1, 3)
SHA_TOKEN = re.compile(r"^[0-9a-f]{7,40}$")
TAG_TOKEN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9.]+)+$")
RELEASE_PREFIX = "release."
LEGACY_DONE_NOTE = "done in the legacy backlog; no evidence carried over"


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def _title(text: str) -> str:
    title = _flat(text).replace("(USER-OWNED)", "").replace("**", "").strip(" .:;")
    return PT.one_line(title, PT.TITLE_MAX)


def _slug(heading: Optional[str]) -> List[str]:
    if not heading:
        return []
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")[:40].strip("-")
    return [slug] if slug and W.TAG_RE.match(slug) else []


def _section_kind(heading: str) -> Optional[str]:
    lowered = heading.lower()
    return next((kind for words, kind in SECTION_KINDS if words in lowered), None)


def _item(line: int, key: str, title: str, **fields) -> dict:
    item = {"line": line, "key": key, "title": title, "status": "planned", "priority": "P2",
            "rank": None, "owner": None, "tags": [], "note": None, "links": [], "evidence": [],
            "parent_key": None, "depends_keys": []}
    item.update(fields)
    return item


# ---------------------------------------------------------------------------------------------
# import-md
# ---------------------------------------------------------------------------------------------

def _split_top(text: str, separators) -> List[str]:
    """`text` cut at each separator that sits outside parentheses, brackets and backticks."""
    parts, depth, tick, start, index = [], 0, False, 0, 0
    while index < len(text):
        char = text[index]
        if char == "`":
            tick = not tick
        elif not tick and char in "([":
            depth += 1
        elif not tick and char in ")]":
            depth = max(0, depth - 1)
        elif not tick and depth == 0:
            sep = next((s for s in separators if text.startswith(s, index)), None)
            if sep:
                parts.append(text[start:index])
                start = index + len(sep)
                index = start
                continue
        index += 1
    parts.append(text[start:])
    return [part.strip() for part in parts]


def _lead(full: str):
    """(lead, rest) of a bullet: the bold lead when there is one, else the first clause."""
    bold = re.match(r"^\*\*(.+?)\*\*\s*(.*)$", full)
    if bold:
        return bold.group(1), bold.group(2), True
    pieces = CLAUSE_END.split(full, maxsplit=1)
    return pieces[0], (pieces[1] if len(pieces) > 1 else ""), False


def _is_queue_line(parts: List[str]) -> bool:
    lead, _, _ = _lead(_flat(" ".join(parts)))
    return bool(QUEUE_LEAD.match(lead.strip(" .:;")))


def _is_status_line(lead: str, rest: str) -> bool:
    words = _flat(lead).strip(" .:;").split()
    if not words:
        return False
    if len(words) <= 2 and re.match(r"^(?:is|are)\s+at\b", rest.strip(), re.IGNORECASE):
        return True
    return len(words) <= STATUS_MAX_WORDS and words[-1].lower().strip(".,;:()") in STATE_WORDS


def _done_refs(refs: str):
    """(label, evidence targets) from the parentheses of a DONE marker."""
    label, targets = None, []
    for position, part in enumerate(p.strip() for p in refs.split(",") if p.strip()):
        tokens = [token.strip("`") for token in part.split()]
        found = []
        for index, token in enumerate(tokens):
            if SHA_TOKEN.match(token):
                found.append(f"commit:{token}")
            elif TAG_TOKEN.match(token) and (len(tokens) == 1 or tokens[index - 1].lower() == "tag"):
                found.append(f"tag:{token}")
        if position == 0 and not found:
            label = part
        targets += found
    return label, targets


def _queue_items(line: int, parts: List[str], heading: Optional[str], kind: Optional[str],
                 archive: str, out: dict) -> None:
    full = _flat(" ".join(parts))
    credential = common.credential_reason(W.TEXT_PATH, full)
    if credential:
        out["withheld"].append({"line": line, "kind": credential})
        return
    _, rest, _ = _lead(full)
    key = f"doc:{archive}#L{line}"
    for part in _split_top(rest, (",", ";")):
        title = _title(part)
        if title:
            out["items"].append(_item(
                line, f"queue:{key}|{title}", title, owner="user" if kind == "user" else None,
                tags=_slug(heading), links=[{"kind": "reference", "target": key}]))


def _bullet_item(line: int, parts: List[str], heading: Optional[str], kind: Optional[str],
                 archive: str, out: dict) -> None:
    full = _flat(" ".join(parts))
    credential = common.credential_reason(W.TEXT_PATH, full)
    if credential:
        out["withheld"].append({"line": line, "kind": credential})
        return
    body, struck = full, False
    if body.startswith("~~"):
        struck, body = True, body[2:]
        close = body.find("~~")
        body = body[:close] + body[close + 2:] if close >= 0 else body
        body = body.strip()
    head, rest, bold = _lead(body)
    rest = rest.replace("(USER-OWNED)", "").strip()
    marked = bool(re.search(r"\bDONE\b", _flat(parts[0])))
    if bold and not marked and _is_status_line(head, rest):
        out["skipped"].append({"line": line, "reason": STATUS_REASON})
        return
    done = struck or marked
    key = f"doc:{archive}#L{line}"
    evidence = [key] if done else []
    marker = DONE_LEAD.match(_flat(head)) if bold else None
    if marker:
        label, targets = _done_refs(marker.group("refs"))
        subject = marker.group("subject")
        if not subject:
            sentences = _split_top(rest.replace("~~", ""), (". ", "; "))
            subject, rest = sentences[0], "; ".join(s for s in sentences[1:] if s)
        if _title(subject):
            head = subject
            evidence = targets + [key]
            if label:
                rest = f"DONE ({label})" + (f". {rest}" if rest else "")
    out["items"].append(_item(
        line, "link:" + key, _title(head), status="done" if done else "planned",
        owner="user" if "USER-OWNED" in full or kind == "user" else None, tags=_slug(heading),
        note=PT.one_line(rest, PT.NOTE_MAX) if rest else None,
        links=[{"kind": "reference", "target": key}], evidence=evidence))


def _table_item(line: int, cells: List[str], header: List[str], heading: Optional[str],
                kind: Optional[str], archive: str, team: Optional[str], out: dict) -> None:
    row = _flat(" | ".join(cells))
    credential = common.credential_reason(W.TEXT_PATH, row)
    if credential:
        out["withheld"].append({"line": line, "kind": credential})
        return

    def column(*names):
        for index, name in enumerate(header):
            if index < len(cells) and any(word in name for word in names):
                return index
        return None

    task_col, owner_col = column("task"), column("owner")
    title_col = column("what", "title", "item")
    if title_col is None:
        title_col = next((i for i in range(len(cells)) if i not in (task_col, owner_col)), 0)
    state_col = column("branch", "state", "status")
    title_cell = cells[title_col] if title_col < len(cells) else ""
    if not _flat(title_cell):
        return
    key = f"doc:{archive}#L{line}"
    links = [{"kind": "reference", "target": key}]
    owner = cells[owner_col].strip() if owner_col is not None else ""
    owner = owner if PT.NAME_RE.match(owner) else None
    number = re.search(r"#(\d+)", cells[task_col]) if task_col is not None else None
    if number and team:
        links.append({"kind": "reference", "target": f"ptask:{team}/{number.group(1)}"})
    elif number:
        out["notes"].append(f"L{line}: #{number.group(1)} is not linked (pass --team <team> to "
                            "link platform tasks)")
    state = cells[state_col] if state_col is not None and state_col < len(cells) else ""
    branch = next((token for token in re.findall(r"`([^`\s]+)`", state) if "/" in token), None)
    if branch:
        links.append({"kind": "reference", "target": f"branch:{branch}"})
    done = _flat(state).lower() in DONE_STATES or bool(re.search(r"\bDONE\b", title_cell))
    used = {task_col, owner_col, title_col, state_col}
    rest = "; ".join(f"{header[i]}: {_flat(cells[i])}" for i in range(min(len(cells), len(header)))
                     if i not in used and _flat(cells[i]) not in ("", "—", "-"))
    status = "done" if done else ("active" if kind == "inflight" else "planned")
    out["items"].append(_item(
        line, "link:" + key, _title(title_cell), status=status,
        owner="user" if kind == "user" else owner, tags=_slug(heading),
        note=PT.one_line(rest, PT.NOTE_MAX) if rest else None, links=links,
        evidence=[key] if done else []))


def parse_md(text: str, archive: str, team: Optional[str] = None, source: str = "") -> dict:
    """A proposal from markdown. Reads nothing but `text`; writes nothing."""
    out = {"kind": "md", "source": source, "archive": archive, "items": [], "withheld": [],
           "skipped": [], "notes": []}
    heading, kind, header, current, in_code = None, None, None, None, False
    last_heading = None                               # (line, level) of the latest heading seen

    def finish():
        nonlocal current
        if current:
            number, parts, quoted = current
            if _is_queue_line(parts):
                _queue_items(number, parts, heading, kind, archive, out)
            elif quoted:
                out["notes"].append(f"L{number}: {LOG_NOTE}")
            else:
                _bullet_item(number, parts, heading, kind, archive, out)
        current = None

    for number, raw in enumerate(text.splitlines(), 1):
        if raw.lstrip().startswith("```"):
            finish()
            in_code = not in_code
            continue
        if in_code:
            continue
        quote = QUOTE_RE.match(raw)
        if quote:
            inner = quote.group(1)
            quoted_bullet = BULLET_RE.match(inner)
            if quoted_bullet:
                finish()
                if kind in SKIP_REASONS:
                    out["skipped"].append({"line": number, "reason": SKIP_REASONS[kind]})
                    continue
                current = (number, [quoted_bullet.group(1)], True)
            elif inner.strip() and inner[:1].isspace() and current and current[2]:
                current[1].append(inner.strip())     # a nested line of the quoted bullet above
            else:
                finish()                             # a quoted label or blank line
            continue
        head = HEADING_RE.match(raw)
        if head:
            finish()
            level, name = len(head.group(1)), head.group(2)
            last_heading = (number, level)
            heading, kind = (None, None) if level == 1 else (name, _section_kind(name))
            credential = common.credential_reason(W.TEXT_PATH, name)
            if credential:
                heading = None                       # the raw heading never becomes a tag
                out["notes"].append(f"L{number}: the heading carries a credential value ({credential}); "
                                    "its items carry no tag")
            header = None
            continue
        bullet = BULLET_RE.match(raw)
        if raw.startswith("|"):
            finish()
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", raw.strip().strip("|"))]
            if all(SEPARATOR_CELL.match(cell) for cell in cells if cell):
                continue
            if header is None:
                credential = common.credential_reason(W.TEXT_PATH, " | ".join(cells))
                header = [cell.lower() for cell in cells] if not credential else \
                    [f"column {index + 1}" for index in range(len(cells))]
                if credential:
                    out["notes"].append(f"L{number}: the table header carries a credential value "
                                        f"({credential}); its columns are unnamed")
                continue
            if kind in SKIP_REASONS:
                out["skipped"].append({"line": number, "reason": SKIP_REASONS[kind]})
                continue
            _table_item(number, cells, header, heading, kind, archive, team, out)
            continue
        if bullet:
            finish()
            if kind in SKIP_REASONS:
                out["skipped"].append({"line": number, "reason": SKIP_REASONS[kind]})
                continue
            current = (number, [bullet.group(1)], False)
            continue
        if not raw.strip():
            finish()
            continue
        if current:
            current[1].append(raw.strip())
    finish()
    out["items"].sort(key=lambda item: item["line"])
    _warn_on_a_heavy_last_section(out, last_heading)
    return out


def _warn_on_a_heavy_last_section(out: dict, last_heading) -> None:
    """A note when the file's last level-2+ heading scopes at least LAST_SECTION_WARN_FRACTION of
    the items. It names the line and the counts, never the heading text."""
    total = len(out["items"])
    if not total or not last_heading or last_heading[1] < 2:
        return
    line = last_heading[0]
    under = sum(1 for item in out["items"] if item["line"] > line)
    if Fraction(under, total) >= LAST_SECTION_WARN_FRACTION:
        out["notes"].append(
            f"L{line}: the file's last heading scopes {under} of {total} items (at least "
            f"{LAST_SECTION_WARN_FRACTION}); check each belongs under it, or add a heading above "
            "the ones that do not")


# ---------------------------------------------------------------------------------------------
# import-gates
# ---------------------------------------------------------------------------------------------

def _line_of(lines: List[str], needle: str) -> int:
    return next((index for index, line in enumerate(lines, 1) if needle in line), 0)


def _gate_evidence(main: Path, evidence) -> Optional[str]:
    """The evidence string when it is ONE target that verifies, else None."""
    if not isinstance(evidence, str) or not evidence.strip() or len(evidence.split()) != 1:
        return None
    target = evidence.strip()
    try:
        W.check_target(main, target, evidence=True)
    except W.Refused:
        return None
    return target if _evidence.verify(main, target)[0] else None


def parse_gates(text: str, main: Path, team: Optional[str] = None, source: str = "") -> dict:
    """A proposal from release-gates.json. Reads git (is the tag cut? does evidence verify?);
    writes nothing."""
    out = {"kind": "gates", "source": source, "items": [], "refused": [], "notes": []}
    try:
        gates = json.loads(text).get("gates")
    except (ValueError, AttributeError) as err:
        raise W.Refused(f"not a release-gates JSON object: {err}") from err
    if not isinstance(gates, dict):
        raise W.Refused("release-gates JSON has no `gates` object")
    lines = text.splitlines()
    for tag, entries in gates.items():
        tag_line = _line_of(lines, json.dumps(tag) + ":")
        name = RELEASE_PREFIX + str(tag)
        if not W.TAG_RE.match(name) or not isinstance(entries, list):
            out["refused"].append({"line": tag_line, "where": str(tag),
                                   "reason": "the tag cannot be held as a task tag (<=32 characters of "
                                             "letters, digits, . _ -), or its entries are not a list"})
            continue
        release_key = f"tag:{name}"
        cut = _evidence.verify(main, f"tag:{tag}")[0] if _evidence.TAG_RE.match(str(tag)) else False
        links = [{"kind": "reference", "target": f"tag:{tag}"}] if cut else []
        release = _item(tag_line, release_key, _title(f"Close release {tag}"), tags=["release", name],
                        links=links, cut=cut)
        out["items"].append(release)
        open_gates = 0
        for index, entry in enumerate(entries):
            gate = entry.get("gate") if isinstance(entry, dict) else None
            line = _line_of(lines, json.dumps(gate, ensure_ascii=False)) if isinstance(gate, str) else tag_line
            where = f"{tag}[{index}]"
            if not isinstance(gate, str) or not _flat(gate):
                out["refused"].append({"line": line, "where": where, "reason": "the entry has no gate text"})
                continue
            if common.credential_reason(W.TEXT_PATH, gate):
                out["refused"].append({"line": line, "where": where,
                                       "reason": "the gate text carries a credential value (withheld)"})
                continue
            gate_links, task_id = [], entry.get("task_id")
            if task_id is not None:
                if not isinstance(task_id, int) or isinstance(task_id, bool):
                    out["refused"].append({"line": line, "where": where, "reason": "task_id is not a number"})
                    continue
                if not team:
                    out["refused"].append({"line": line, "where": where,
                                           "reason": f"task_id {task_id} needs --team <team> for its ptask: link"})
                    continue
                gate_links.append({"kind": "reference", "target": f"ptask:{team}/{task_id}"})
            status, evidence = entry.get("status"), []
            if status == "passed":
                target = _gate_evidence(main, entry.get("evidence"))
                if target is None:
                    out["refused"].append({"line": line, "where": where,
                                           "reason": "a passed gate needs ONE evidence target that verifies "
                                                     "(commit:, merge:, tag: or doc:<path>@<sha>)"})
                    continue
                evidence = [target]
            elif status != "pending":
                out["refused"].append({"line": line, "where": where,
                                       "reason": f"status {status!r} is not pending or passed"})
                continue
            else:
                open_gates += 1
            title = _title(gate)
            note = PT.one_line(gate, PT.NOTE_MAX) if _flat(gate) != title else None
            out["items"].append(_item(line, f"child:{release_key}|{title}", title,
                                      status="done" if evidence else "planned", tags=["gate"],
                                      note=note, links=gate_links, evidence=evidence,
                                      parent_key=release_key))
        if cut and not open_gates and len(entries) == sum(
                1 for item in out["items"] if item["parent_key"] == release_key):
            release["status"], release["evidence"] = "done", [f"tag:{tag}"]   # a container: closes on its tag
    return out


# ---------------------------------------------------------------------------------------------
# import-pact-backlog
# ---------------------------------------------------------------------------------------------

def _strings(value):
    """Every string inside a legacy row (title, note, ref, plan, each memory id, ...)."""
    if isinstance(value, str):
        if value:
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def find_legacy(main: Path) -> Path:
    """The legacy backlog whose `roots` lists this main checkout (read-only discovery)."""
    folder = Path.home() / LEGACY_DIR
    target = os.path.realpath(main)
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            roots = json.loads(path.read_bytes()).get("roots") or []
        except (OSError, ValueError, AttributeError):
            continue
        if any(isinstance(root, str) and os.path.realpath(root) == target for root in roots):
            return path
    raise W.NoLog(f"no legacy backlog in ~/{LEGACY_DIR} lists this checkout; name the file")


def _checked_link(main: Path, kind: str, target: str, where: str, out: dict) -> Optional[dict]:
    try:
        W.check_target(main, target, importer=True)
    except W.Refused as err:
        out["notes"].append(f"{where}: {kind} link {target.split(':', 1)[0]}: not imported (D26 or link "
                            f"rule: {err})")
        return None
    return {"kind": kind, "target": target}


def parse_pact_backlog(doc: dict, main: Path, source: str = "") -> dict:
    """A proposal from a legacy backlog document. Reads nothing but `doc` (and git for link
    rules); writes nothing."""
    out = {"kind": "pact-backlog", "source": source, "items": [], "withheld": [], "notes": []}
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise W.Refused("not a legacy backlog document (no items list)")
    rows = [row for row in list(doc["items"]) + list(doc.get("archive") or []) if isinstance(row, dict)]
    ids = {row.get("id") for row in rows}
    if any(row.get("blocked_by") for row in rows):
        out["notes"].append("blocked_by is now depends_on: a dependency that is done clears itself")
    for index, row in enumerate(rows, 1):
        legacy = row.get("id")
        if not isinstance(legacy, str) or not W.LEGACY_RE.match(legacy):
            out["notes"].append(f"item {index}: id {legacy!r} is not 4 hex characters; not imported")
            continue
        credential = next((kind for kind in (common.credential_reason(W.TEXT_PATH, text)
                                             for text in _strings(row)) if kind), None)
        if credential:
            out["withheld"].append({"line": index, "where": legacy, "kind": credential})
            continue
        status = row.get("status") if row.get("status") in PT.STATUSES else "planned"
        note = row.get("note") if isinstance(row.get("note"), str) else None
        if status == "done":
            status, note = "dropped", PT.one_line(f"{LEGACY_DONE_NOTE}. {note or ''}", PT.NOTE_MAX)
        links = [{"kind": "reference", "target": f"legacy:{legacy}"}]
        candidates = []
        if isinstance(row.get("ref"), str) and row["ref"]:
            candidates.append(("reference", f"issue:{row['ref']}"))
        if isinstance(row.get("plan"), str) and row["plan"]:
            candidates.append(("input", f"doc:{row['plan']}"))
        for memory in row.get("memory") or []:
            if isinstance(memory, str) and memory:
                candidates.append(("reference", f"memory:{memory}"))
        for kind, target in candidates:
            link = _checked_link(main, kind, target, legacy, out)
            if link:
                links.append(link)
        rank = row.get("rank")
        rank = int(rank) if isinstance(rank, (int, float)) and not isinstance(rank, bool) and rank >= 0 else None
        blocked_by = [b for b in row.get("blocked_by") or [] if isinstance(b, str)]
        for missing in (b for b in blocked_by if b not in ids):
            out["notes"].append(f"{legacy}: blocked_by {missing} names no legacy item; dropped")
        out["items"].append(_item(
            index, f"link:legacy:{legacy}", _title(row.get("title") or f"legacy {legacy}"),
            status=status, rank=rank, note=note, links=links, legacy=legacy,
            depends_keys=[f"link:legacy:{b}" for b in blocked_by if b in ids],
            batch_keys=[f"link:legacy:{b}" for b in row.get("batch_with") or [] if b in ids],
            exclusive_keys=[f"link:legacy:{b}" for b in row.get("exclusive_with") or [] if b in ids]))
    return out


# ---------------------------------------------------------------------------------------------
# the proposal table
# ---------------------------------------------------------------------------------------------

def table(proposal: dict) -> List[str]:
    """One line per item, for the lead's review. Withheld items show their line and kind only."""
    rows = []
    for item in proposal.get("items", []):
        status = item["status"]
        if proposal.get("kind") == "pact-backlog" and item.get("note", "") and \
                str(item.get("note")).startswith(LEGACY_DONE_NOTE):
            status = "done->dropped"
        who = item["owner"] or "-"
        tags = ",".join(item["tags"]) or "-"
        extra = ""
        if proposal.get("kind") == "gates":
            extra = " (cut)" if item.get("cut") else (" (uncut)" if item["parent_key"] is None else "")
        rows.append((item["line"], 0, f"L{item['line']}  {status}  {who}  {tags}  {item['title']}{extra}"))
    for held in proposal.get("withheld", []):
        rows.append((held["line"], 1, f"L{held['line']}  WITHHELD ({held['kind']})"))
    lines = [row for _, _, row in sorted(rows, key=lambda r: (r[0], r[1]))]
    for refused in proposal.get("refused", []):
        lines.append(f"REFUSED line {refused['line']}: {refused['where']}: {refused['reason']}")
    for skipped in proposal.get("skipped", []):
        lines.append(f"skipped line {skipped['line']}: {skipped['reason']}")
    lines += [f"note: {note}" for note in proposal.get("notes", [])]
    count = len(proposal.get("items", []))
    held = len(proposal.get("withheld", []))
    lines.append(f"{count} item(s) proposed, {held} withheld, {len(proposal.get('refused', []))} refused; "
                 "nothing written (dry run). Re-run with --apply to write.")
    return lines


# ---------------------------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------------------------

def _head(main: Path) -> Optional[str]:
    result = subprocess.run(["git", "-C", str(main), "rev-parse", "HEAD"], capture_output=True,
                            text=True, timeout=15)
    return result.stdout.strip() if result.returncode == 0 else None


def _prepare(main: Path, item: dict, head: Optional[str], legacy_links: bool) -> dict:
    """Re-validate one (possibly hand-edited) item through the writer's rules; raises Refused."""
    where = f"L{item.get('line', '?')}"
    try:
        title = W._title(item.get("title") or "")
        note = W._field_value("note", item.get("note"))
        tags = W._field_value("tags", item.get("tags") or [])
        owner = W._field_value("owner", item.get("owner"))
        priority = W._field_value("priority", item.get("priority") or "P2")
        rank = W._field_value("rank", item.get("rank"))
        W.check_text(title, note or "", *tags)
        status = item.get("status", "planned")
        if status not in PT.STATUSES:
            raise W.Refused(f"status {status!r} is not one of {', '.join(PT.STATUSES)}")
        links = []
        for link in item.get("links") or []:
            if link.get("kind") not in PT.LINK_KINDS:
                raise W.Refused(f"link kind {link.get('kind')!r} is not one of {', '.join(PT.LINK_KINDS)}")
            W.check_text(link.get("target") or "")
            W.check_target(main, link.get("target") or "", importer=legacy_links)
            links.append({"kind": link["kind"], "target": link["target"]})
        evidence, reasons = [], []
        for target in item.get("evidence") or []:
            if target.startswith("doc:") and "@" not in target and head:
                target = f"{target}@{head}"
            W.check_target(main, target, evidence=True)
            verified, reason = _evidence.verify(main, target)
            evidence.append({"target": target, "verified": verified})
            if not verified:
                reasons.append(reason)
    except W.Refused as err:
        raise W.Refused(f"{where}: {err}") from err
    return {**item, "title": title, "note": note, "tags": tags, "owner": owner, "priority": priority,
            "rank": rank, "status": status, "links": links, "evidence": evidence, "reasons": reasons}


def _existing_keys(state) -> Dict[str, str]:
    keys: Dict[str, str] = {}
    for ref, task in state.tasks.items():
        for link in task["links"]:
            keys.setdefault("link:" + link["target"], ref)
            keys.setdefault(f"queue:{link['target']}|{task['title']}", ref)   # one line, many tasks
        for tag in task["tags"]:
            if tag.startswith(RELEASE_PREFIX):
                keys.setdefault("tag:" + tag, ref)
    for ref, task in state.tasks.items():
        parent = state.tasks.get(task["parent"] or "")
        if parent:
            for tag in parent["tags"]:
                if tag.startswith(RELEASE_PREFIX):
                    keys.setdefault(f"child:tag:{tag}|{task['title']}", ref)
    return keys


def _cycle(edges: Dict[str, List[str]]) -> Optional[List[str]]:
    state, path = {}, []

    def visit(node):
        state[node] = 1
        path.append(node)
        for nxt in edges.get(node, []):
            if state.get(nxt) == 1:
                return path[path.index(nxt):] + [nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        state[node] = 2
        path.pop()
        return None

    for node in list(edges):
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def _refuse_open_children(state, fresh, events) -> None:
    """P1's rule, kept for batches: nothing closes while a child is open (design section 4.2)."""
    final = {ref: task["status"] for ref, task in state.tasks.items()}
    parents = {ref: task["parent"] for ref, task in state.tasks.items()}
    for event in events:
        if event["op"] == "create":
            final[event["t"]], parents[event["t"]] = "planned", event.get("parent")
        elif event["op"] == "set" and event["field"] == "status":
            final[event["t"]] = event["value"]
        elif event["op"] == "done":
            final[event["t"]] = "done"
    for ref, _item_ in fresh:
        if final.get(ref) != "done":
            continue
        children = sorted(child for child, parent in parents.items()
                          if parent == ref and final.get(child) not in PT.SETTLED)
        if children:
            raise W.Refused(f"L{_item_['line']}: {ref} has open children: {', '.join(children)}")


def apply(store, proposal: dict) -> List[str]:
    """Write the proposal in ONE locked append; items already imported are skipped."""
    refused = proposal.get("refused") or []
    if refused:
        raise W.Refused("refused entries, nothing written: " + "; ".join(
            f"line {r['line']} {r['where']}: {r['reason']}" for r in refused))
    head = _head(store.main)
    legacy_links = proposal.get("kind") == "pact-backlog"      # legacy: is the legacy importer's alone
    prepared = [_prepare(store.main, item, head, legacy_links) for item in proposal.get("items") or []]
    report: List[str] = []

    def build(state):
        report.clear()
        known = _existing_keys(state)
        refs: Dict[str, str] = {}
        taken = set(state.tasks)
        events: List[dict] = []
        fresh = []
        for item in prepared:
            if item["key"] in known or item["key"] in refs:
                refs.setdefault(item["key"], known.get(item["key"]))
                continue
            ref = "T-" + secrets.token_hex(4)
            while ref in taken:
                ref = "T-" + secrets.token_hex(4)
            taken.add(ref)
            refs[item["key"]] = ref
            fresh.append((ref, item))
        for ref, item in fresh:
            create = {"op": "create", "t": ref, "title": item["title"], "status": "planned",
                      "priority": item["priority"]}
            for name in ("rank", "owner", "note"):
                if item[name] is not None:
                    create[name] = item[name]
            if item["tags"]:
                create["tags"] = item["tags"]
            if item.get("parent_key"):
                parent = refs.get(item["parent_key"]) or known.get(item["parent_key"])
                if parent is None:
                    raise W.Refused(f"L{item['line']}: its parent {item['parent_key']} is not in the proposal")
                if parent in state.tasks:
                    try:
                        W.check_parent(state, parent)
                    except W.Refused as err:
                        raise W.Refused(f"L{item['line']}: {err}") from err
                create["parent"] = parent
            events.append(create)
            events += [{"op": "link_add", "t": ref, **link} for link in item["links"]]
            for field, keys in (("batch_with", item.get("batch_keys")), ("exclusive_with", item.get("exclusive_keys"))):
                others = [refs[k] for k in keys or [] if refs.get(k)]
                if others:
                    events.append({"op": "set", "t": ref, "field": field, "value": others})
            for key in item.get("depends_keys") or []:
                if refs.get(key):
                    events.append({"op": "dep_add", "t": ref, "on": refs[key]})
            status = item["status"]
            if status == "done":
                if any(entry["verified"] for entry in item["evidence"]):
                    events.append({"op": "done", "t": ref, "evidence": item["evidence"]})
                else:
                    report.append(f"L{item['line']}: imported open ({'; '.join(item['reasons']) or 'no evidence'})")
            elif status != "planned":
                events.append({"op": "set", "t": ref, "field": "status", "value": status})
        _refuse_open_children(state, fresh, events)
        edges = {ref: list(task["depends_on"]) for ref, task in state.tasks.items()}
        for event in events:
            if event["op"] == "dep_add":
                edges.setdefault(event["t"], []).append(event["on"])
        loop = _cycle(edges)
        if loop:
            raise W.Refused("the import would create a dependency cycle: " + " -> ".join(loop))
        report.insert(0, f"imported {len(fresh)} task(s), skipped {len(prepared) - len(fresh)} already imported")
        return events

    store._commit(build)
    return report + [f"note: {note}" for note in proposal.get("notes", [])]
