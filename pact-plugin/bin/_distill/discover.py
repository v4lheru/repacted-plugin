"""Find the LITE distiller sources under the Claude config dir (KB lite, ruling R11).

Location: pact-plugin/bin/_distill/discover.py. Used by bin/repact-distill, which passes
the list to sources.read_source(). Lite sources are text a model already wrote; raw session
transcripts are never a source here:
  memory_inbox     <config>/agent-memory/**/MEMORY-inbox.md
  handoff          agent_handoff events in <config>/pact-sessions/<project>/<session>/
                   session-journal.jsonl (task files are not read: they are garbage-collected)
  pact_memory      the pact-memory store, read only through its own CLI (read verbs); the
                   store file is never opened here
  compact_summary  compact-summary.txt and compact-summary-<ts>.txt in a session dir

Paths and sizes only: no file is read here. Every skipped candidate is counted with a reason.
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

KIND_MEMORY_INBOX = "memory_inbox"
KIND_HANDOFF = "handoff"
KIND_PACT_MEMORY = "pact_memory"
KIND_COMPACT_SUMMARY = "compact_summary"
# Lower runs first: a capped run spends its budget on the best material (design note §4.2).
PRIORITY = {KIND_MEMORY_INBOX: 1, KIND_HANDOFF: 1, KIND_PACT_MEMORY: 1, KIND_COMPACT_SUMMARY: 2}
KINDS = tuple(PRIORITY)

INBOX_FILE = "MEMORY-inbox.md"
JOURNAL_FILE = "session-journal.jsonl"
SUMMARY_FILE = "compact-summary.txt"
# compact-summary-YYYY-MM-DDTHH-MM-SS.txt: an archived earlier summary, the plugin-written shape
# (hooks/shared/session_init_resume.py). The .head, .pending-, .tmp-, .teammate-,
# .unattributed- and -root-drained- variants are not read.
SUMMARY_ARCHIVE_RE = re.compile(r"^compact-summary-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}\.txt$")
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MEMORY_CLI = PLUGIN_ROOT / "skills" / "pact-memory" / "scripts" / "cli.py"


@dataclass(frozen=True)
class Source:
    """One place to read. `session` groups chunks for the sessions cap; `project` is the
    pact-sessions project dir name, or "" when the source belongs to no project."""
    kind: str
    path: Path
    project: str
    session: str
    mtime: float
    size: int


@dataclass
class Discovery:
    sources: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)   # reason -> count

    def skip(self, reason: str, count: int = 1) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + count


def project_allowed(project: str, include: Iterable[str], exclude: Iterable[str]) -> bool:
    """True when `project` passes the profile's distill.projects globs. Exclude wins. A source
    with no project ("") is kept: the globs name projects, and such a source has none."""
    if not project:
        return True
    if any(fnmatch.fnmatchcase(project, pattern) for pattern in exclude):
        return False
    include = list(include)
    return not include or any(fnmatch.fnmatchcase(project, pattern) for pattern in include)


def discover(config_dir: Path, include: Iterable[str] = (), exclude: Iterable[str] = (),
             memory_cli: Optional[Path] = None) -> Discovery:
    """Every lite source under `config_dir`, filtered by the project globs."""
    found = Discovery()
    include, exclude = list(include), list(exclude)
    _inboxes(config_dir / "agent-memory", found)
    _session_dirs(config_dir / "pact-sessions", include, exclude, found)
    cli = memory_cli if memory_cli is not None else DEFAULT_MEMORY_CLI
    if cli.is_file():
        found.sources.append(Source(KIND_PACT_MEMORY, cli, "", "pact-memory", 0.0, 0))
    else:
        found.skip("pact_memory: the pact-memory CLI is not installed")
    return found


def _stat_source(kind: str, path: Path, project: str, session: str, found: Discovery) -> None:
    try:
        info = path.stat()
    except OSError:
        found.skip(f"{kind}: unreadable file")
        return
    found.sources.append(Source(kind, path, project, session, info.st_mtime, info.st_size))


def _inboxes(root: Path, found: Discovery) -> None:
    if not root.is_dir():
        return
    for inbox in sorted(root.rglob(INBOX_FILE)):
        if inbox.is_symlink() or not inbox.is_file():
            found.skip(f"{KIND_MEMORY_INBOX}: not a regular file")
            continue
        _stat_source(KIND_MEMORY_INBOX, inbox, "", inbox.parent.relative_to(root).as_posix(),
                     found)


def _session_dirs(root: Path, include: list, exclude: list, found: Discovery) -> None:
    if not root.is_dir():
        return
    for project_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        if not project_allowed(project_dir.name, include, exclude):
            found.skip("project excluded by distill.projects")
            continue
        for session_dir in sorted(p for p in project_dir.iterdir() if p.is_dir()):
            _one_session(session_dir, project_dir.name, found)


def _one_session(session_dir: Path, project: str, found: Discovery) -> None:
    journal = session_dir / JOURNAL_FILE
    if journal.is_file() and not journal.is_symlink():
        _stat_source(KIND_HANDOFF, journal, project, session_dir.name, found)
    for path in sorted(session_dir.iterdir()):
        if path.name != SUMMARY_FILE and not SUMMARY_ARCHIVE_RE.match(path.name):
            continue
        if path.is_symlink() or not path.is_file():
            found.skip(f"{KIND_COMPACT_SUMMARY}: not a regular file")
            continue
        _stat_source(KIND_COMPACT_SUMMARY, path, project, session_dir.name, found)
