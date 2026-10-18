"""
Location: pact-plugin/hooks/shared/project_agents.py
Summary: Specialists a project brings itself: `.claude/agents/**/pact-*.md` files read from the
         repository, the one strict reader of those files, and the verdict on a spawn type. A
         project file becomes a PACT specialist only when its frontmatter `name` is a plain
         `pact-<role>` equal to its file name and its body carries the teammate binding
         (BINDING). Every file the host could load is also read for the names it can CLAIM, so
         a file that would shadow a shipped agent (the host prefers a project agent to a plugin
         agent of the same name) is caught whatever its file name. A file whose name this
         reader cannot read with certainty claims every name its frontmatter text could spell,
         and every name at all when that text has an escape, a tag or an anchor (fail closed).
         The frontmatter is also cut the way the host cuts it, and a file where the two cuts
         differ is uncertain. A file the host cannot load (a dangling link, a FIFO, a file it
         may not open) claims nothing.
Used by: shared/dispatch_helpers.py (the specialist registry), hooks/dispatch_gate.py (the
         verdict and its refusals) and bin/repact-agent (`check` and `new`).
Doc: reference/project-agents.md.

Host facts this follows (code.claude.com/docs/en/sub-agents, "Choose the subagent scope"):
identity comes only from the frontmatter `name`; `.claude/agents/` is scanned recursively and
every `.claude/agents/` from the working directory up to the repository root is read; a
project agent wins over a user or plugin agent of the same name. A plugin agent spelled with
its plugin namespace (`repacted:pact-architect`) is never a project agent. Claude Code 2.1.302
(read from its binary) cuts the frontmatter with /^---\\s*\\n([\\s\\S]*?)---\\s*\\n?/
after stripping one byte-order mark (HOST_FRONTMATTER below), so its closing `---` may sit
mid-line, carry trailing text or be `----`, and its opener may carry a tab; it skips a file it
cannot open or that is not a regular file.

Map, in file order: constants; AgentFile and Scan; reading one file (read_agent_file and the
frontmatter subset); scanning (agent_dirs, scan); the verdict (resolve).

Stdlib only, plus shared.plugin_identity and shared.paths (both leaves).
"""
from __future__ import annotations

import errno
import os
import re
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from shared.paths import get_claude_config_dir
from shared.plugin_identity import NAMESPACE

MAX_AGENT_BYTES = 64 * 1024   # a longer file is not a specialist; its head is still read
MAX_FILES = 200               # Markdown files in all scanned trees together
MAX_DEPTH = 8                 # directory levels below one .claude/agents
NAME_MAX = 64                 # the spawn name limit of dispatch_gate, applied to the type too
BINDING = f'Skill("{NAMESPACE}teammate")'
# No `---` in a name: the host ends the frontmatter at the first `---`, even mid-line.
SPECIALIST_NAME = re.compile(r"pact-[a-z0-9]+(?:--?[a-z0-9]+)*")
ANY_NAME = "*"                # the claim of a file whose name could be anything

# The frontmatter subset this reader trusts: top-level `key: value` lines, indented
# continuation lines, list items, comments and blanks. Anything else (a flow mapping, a quoted
# or complex key, a merge key, a tab) leaves the name uncertain, as does a `name:` value that
# is not a plain one-line scalar.
_TOP_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)[ ]*:(?:[ ]+(.*))?$")
# What lets YAML build text the file does not spell: an escape (backslash), a tag (`!`, which
# !!binary can turn into any bytes) or an anchor (`&`, so an alias can carry it). An alias
# (`*`) with no anchor is a YAML error the host does not load, so `*` alone does not count,
# and Markdown `**bold**` in a description stays harmless.
_BUILDS_TEXT = re.compile(r"\\|(?:^|[\s\[{,])[!&](?=\S)")
_YAML_INDICATORS = tuple("&*!|>[]{}%@`?,")
_FENCE = re.compile(r"^[ ]{0,3}(```|~~~)")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# The host's frontmatter cut, with JavaScript's \s spelled out (Python's \s differs from it).
_JS_SPACE = "\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
HOST_FRONTMATTER = re.compile(f"---[{_JS_SPACE}]*\n([\\s\\S]*?)---")
_JS_BLANK = re.compile(f"[{_JS_SPACE}]*")
# Open errors the host meets too, so it never loads the file: missing (a dangling link), not
# permitted, a link loop, a device without a reader.
_HOST_SKIPS = frozenset({errno.ENOENT, errno.EACCES, errno.EPERM, errno.ELOOP, errno.ENOTDIR,
                         errno.ENXIO})


@dataclass(frozen=True)
class AgentFile:
    """One agent file. `names` are the identities the host could give it (ANY_NAME when they
    could be anything); `spellable`, set when the name is uncertain but cannot be built, is
    the folded frontmatter text, and the file claims every name that text contains;
    `problem` says why it cannot be a PACT specialist ("" when it can); `unbound` marks the
    one problem with its own refusal: no teammate binding."""
    path: str
    names: Tuple[str, ...]
    problem: str = ""
    unbound: bool = False
    spellable: str = ""

    def claims(self, folded: str) -> bool:
        return (ANY_NAME in self.names or any(fold(n) == folded for n in self.names)
                or bool(folded and folded in self.spellable))


@dataclass(frozen=True)
class Scan:
    """Every agent file under the scanned directories; `incomplete` says why the scan could
    not see everything ("" when it did), in which case any name may be claimed."""
    files: Tuple[AgentFile, ...] = ()
    incomplete: str = ""


def fold(name: str) -> str:
    """The comparison form of a name: NFKC, case-folded, stripped (look-alikes meet here)."""
    return unicodedata.normalize("NFKC", name).casefold().strip()


# ---------------------------------------------------------------------------
# Reading one file
# ---------------------------------------------------------------------------

def _read_head(path: str, follow: bool) -> Tuple[Optional[bytes], int, str, bool]:
    """(the first MAX_AGENT_BYTES + 1 bytes, the file size, why unreadable, whether the host
    skips the file too). Never blocks on a FIFO or device: opened non-blocking and refused
    unless fstat says regular file."""
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if not follow:
        flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        return None, 0, f"unreadable ({type(error).__name__})", error.errno in _HOST_SKIPS
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, 0, "not a regular file", True
        chunks, remaining = [], MAX_AGENT_BYTES + 1
        while remaining > 0:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks), info.st_size, "", False
    except OSError as error:
        return None, 0, f"unreadable ({type(error).__name__})", False
    finally:
        os.close(fd)


def _plain_value(raw: Optional[str]) -> Optional[str]:
    """The scalar a `name:` value means, or None when it is not a plain one-line scalar (a
    block, an anchor, a tag, a flow value, an escape): those leave the name uncertain. So
    does a tab or trailing whitespace anywhere in the value, and a plain value with `#` after
    whitespace: YAML ends a plain scalar at a comment after a tab as after a space,
    and which whitespace counts is the host's call, not this reader's."""
    raw = raw or ""
    if "\t" in raw or raw != raw.rstrip():
        return None
    value = raw.strip()
    if not value:
        return None
    if value[0] == "'":
        end = value.rfind("'")
        if end <= 0 or value[end + 1:].strip()[:1] not in ("", "#"):
            return None
        return value[1:end].replace("''", "'")
    if value[0] == '"':
        end = value.rfind('"')
        inner = value[1:end] if end > 0 else ""
        if end <= 0 or "\\" in inner or value[end + 1:].strip()[:1] not in ("", "#"):
            return None
        return inner
    if value.startswith(_YAML_INDICATORS) or value.startswith(("- ", "#")):
        return None
    return None if re.search(r"[\s\ufeff]#", value) or ": " in value else value


def _frontmatter(lines: List[str]) -> Tuple[Optional[int], List[str], bool]:
    """(index of the closing `---`, the names given, whether the name is uncertain), with
    end None when there is no frontmatter. Only the name decides: unusual syntax in another
    key's value (a block scalar, indented lines) leaves the name certain, while a `name:` that
    is not a plain one-line scalar, or any top-level line outside the trusted subset (which
    could itself be or hide a `name`), makes it uncertain."""
    if not lines or lines[0].rstrip(" ") != "---":
        return None, [], False
    end = next((i for i in range(1, len(lines)) if lines[i].rstrip(" ") == "---"), None)
    if end is None:
        return None, [], False
    names: List[str] = []
    uncertain = in_name = False
    for line in lines[1:end]:
        if not line.strip() or line.startswith(("#", " ", "- ")) or line == "-":
            # a line under `name:` continues its value (a multi-line scalar or a list)
            uncertain = uncertain or (in_name and bool(line.strip()) and not line.startswith("#"))
            continue
        match = _TOP_KEY.match(line)
        in_name = bool(match) and match.group(1) == "name"
        if not match:
            uncertain = True
        elif in_name:
            value = _plain_value(match.group(2))
            if value is None:
                uncertain = True
            else:
                names.append(value)
    return end, names, uncertain


def _host_region(text: str) -> Optional[str]:
    """The frontmatter text the host parses (line ends made `\\n`), or None when it finds none."""
    match = HOST_FRONTMATTER.match(text)
    if match is None:
        return None
    return match.group(1).replace("\r\n", "\n").replace("\r", "\n")


def _line_region(lines: List[str], end: Optional[int]) -> Optional[str]:
    """The line reader's frontmatter in the host's terms: the host's opener also eats the blank
    lines after it, and its region keeps the line end before the closing `---`."""
    if end is None:
        return None
    body = lines[1:end]
    while body and _JS_BLANK.fullmatch(body[0]):
        body = body[1:]
    return "\n".join(body) + "\n" if body else ""


def _uncertain(path: str, problems: List[str], regions: List[str]) -> AgentFile:
    """A file whose name is uncertain: it claims every name any of `regions` spells, or every
    name at all when one of them can build text."""
    if any(_BUILDS_TEXT.search(region) for region in regions):
        return AgentFile(path, (ANY_NAME,), "; ".join(problems))
    # NFKC can merge a character with the one after it, so the plain case-folded text is
    # kept too: a name the host reads verbatim is a substring of one of the two.
    spellable = "\n".join(f"{fold(region)}\n{region.casefold()}" for region in regions)
    return AgentFile(path, (), "; ".join(problems), spellable=spellable)


def _body_has_binding(body_lines: Iterable[str]) -> bool:
    """True when BINDING appears in the body outside fenced code blocks and HTML comments."""
    kept, fenced = [], False
    for line in body_lines:
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return BINDING in _COMMENT.sub("", "\n".join(kept))


def read_agent_file(path: str, is_link: bool = False) -> AgentFile:
    """Read one agent file strictly. Never raises. A symbolic link is followed only to learn
    the names it claims; it is never a specialist. A file the host cannot load claims
    nothing; one it may load but this reader cannot read claims every name."""
    stem = Path(path).stem
    head, size, unreadable, host_skips = _read_head(path, follow=is_link)
    if head is None:
        return AgentFile(path, () if host_skips else (ANY_NAME,), unreadable)
    problems = ["a symbolic link"] if is_link else []
    if size > MAX_AGENT_BYTES or len(head) > MAX_AGENT_BYTES:
        problems.append(f"larger than {MAX_AGENT_BYTES // 1024} KiB")
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        if size <= MAX_AGENT_BYTES:
            problems.append("not UTF-8")
    text = head[:MAX_AGENT_BYTES].decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        problems.append("starts with a byte-order mark")
        text = text[1:]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    end, names, uncertain = _frontmatter(lines)
    host = _host_region(text)
    if host != _line_region(lines, end):
        problems.append("the host ends its frontmatter elsewhere (a `---` inside a line, or a "
                        "`---` line with other characters)")
        own = ["\n".join(lines[1:end])] if end is not None else []
        return _uncertain(path, problems, own + ([host] if host is not None else []))
    if end is None:
        if len(head) > MAX_AGENT_BYTES:
            return AgentFile(path, (ANY_NAME,), "; ".join(problems + ["no frontmatter"]))
        return AgentFile(path, (stem,), "; ".join(problems + ["no frontmatter"]))
    if uncertain:
        problems.append("frontmatter outside the plain `key: value` form")
        return _uncertain(path, problems, ["\n".join(lines[1:end])])
    if not names:
        return AgentFile(path, (stem,), "; ".join(problems + ["no `name:` in frontmatter"]))
    if len(names) > 1:
        problems.append("`name:` given more than once")
    name = names[0]
    if not (SPECIALIST_NAME.fullmatch(name) and len(name) <= NAME_MAX):
        problems.append(f"name {ascii(name)} is not a plain pact-<role> name")
    elif name != stem:
        problems.append(f"name {name!r} differs from the file name {stem!r}")
    unbound = not _body_has_binding(lines[end + 1:])
    if unbound:
        problems.append(f"no teammate binding ({BINDING} in the body)")
    return AgentFile(path, tuple(names), "; ".join(problems),
                     unbound=unbound and len(problems) == 1)


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def agent_dirs(project_dir: str) -> List[Path]:
    """Every `.claude/agents` the host reads for this project: from project_dir up to the
    repository root (the nearest directory holding `.git`), or project_dir alone outside a
    repository. In a linked worktree the main checkout is read too (the host appears to read
    it; reading more can only refuse more). The user-scope directory (the
    config dir's `agents`) is never one of them, even when project_dir is home."""
    if not project_dir:
        return []
    try:
        start = Path(os.path.realpath(project_dir))
        chain = [start, *start.parents]
        root = next((i for i, d in enumerate(chain) if os.path.lexists(d / ".git")), None)
        chain = chain[: root + 1] if root is not None else [start]
        main = _main_checkout(chain[-1]) if root is not None else None
        if main is not None and main not in chain:
            chain.append(main)
        user_dir = os.path.realpath(get_claude_config_dir() / "agents")
    except (OSError, ValueError, RuntimeError):
        return []
    found = []
    for directory in chain:
        candidate = directory / ".claude" / "agents"
        if os.path.lexists(candidate) and os.path.realpath(candidate) != user_dir:
            found.append(candidate)
    return found


def _main_checkout(root: Path) -> Optional[Path]:
    """The main checkout of a linked worktree at root (its `.git` is a `gitdir:` file whose
    `commondir` is the main `.git`), else None. Never raises; reads at most 4 KiB a file."""
    try:
        gitfile = root / ".git"
        if not gitfile.is_file():
            return None
        with open(gitfile, encoding="utf-8", errors="replace") as handle:
            line = handle.read(4096).strip()
        if not line.startswith("gitdir:"):
            return None
        gitdir = Path(root, line[len("gitdir:"):].strip())
        with open(gitdir / "commondir", encoding="utf-8", errors="replace") as handle:
            common = Path(os.path.realpath(gitdir / handle.read(4096).strip()))
    except (OSError, ValueError):
        return None
    return common.parent if common.name == ".git" else None


def _same_file(path: str, plugin_agents: str) -> bool:
    """True for a link whose target is the plugin's own agent file of the same name: the
    owner's link set, not a second definition."""
    try:
        target = os.path.realpath(path)
        return (os.path.dirname(target) == plugin_agents
                and Path(target).name == Path(path).name and os.path.isfile(target))
    except (OSError, ValueError):
        return False


def scan(project_dir: str, plugin_root: str = "") -> Scan:
    """Read every Markdown file under agent_dirs(project_dir). Never raises. Directory links
    are followed (the host may follow them); MAX_DEPTH and MAX_FILES bound a link cycle, and
    reaching either leaves the scan incomplete rather than partial-but-trusted."""
    try:
        return _scan(project_dir, plugin_root)
    except (OSError, ValueError, RuntimeError) as error:
        return Scan((), f"the agent directories could not be read ({type(error).__name__})")


def _scan(project_dir: str, plugin_root: str) -> Scan:
    plugin_agents = os.path.realpath(Path(plugin_root) / "agents") if plugin_root else ""
    files: List[AgentFile] = []
    for top in agent_dirs(project_dir):
        if not os.path.isdir(top):
            return Scan(tuple(files), f"{top} is not a directory")
        for current, dirs, names in os.walk(top, followlinks=True):
            depth = len(Path(current).relative_to(top).parts)
            if depth >= MAX_DEPTH and dirs:
                return Scan(tuple(files), f"{current} is nested deeper than {MAX_DEPTH} levels")
            for name in sorted(n for n in names if n.lower().endswith(".md")):
                path = os.path.join(current, name)
                is_link = os.path.islink(path)
                if is_link and plugin_agents and _same_file(path, plugin_agents):
                    continue
                if len(files) >= MAX_FILES:
                    return Scan(tuple(files), f"more than {MAX_FILES} agent files")
                files.append(read_agent_file(path, is_link))
    return Scan(tuple(files))


# ---------------------------------------------------------------------------
# The verdict on one spawn type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Verdict:
    """kind: shipped | project | none | shadowed | ambiguous | unbound | invalid |
    incomplete. `files` are the paths involved; `detail` is the reason to show."""
    kind: str
    files: Tuple[str, ...] = ()
    detail: str = ""


def resolve(subagent_type: str, shipped: frozenset, found: Scan) -> Verdict:
    """What a bare spawn of subagent_type would start, given the plugin's shipped names and
    the project scan. A shipped name any project file claims is `shadowed`; a project name
    is `project` only for exactly one claiming file with no problem."""
    folded = fold(subagent_type)
    claimants = [f for f in found.files if f.claims(folded)]
    paths = tuple(f.path for f in claimants)
    if folded in {fold(s) for s in shipped}:
        if claimants:
            return Verdict("shadowed", paths, "a project agent file claims a shipped name")
        if found.incomplete:
            return Verdict("incomplete", (), found.incomplete)
        return Verdict("shipped" if subagent_type in shipped else "none")
    if not claimants:
        return Verdict("incomplete", (), found.incomplete) if found.incomplete else Verdict("none")
    if len(claimants) > 1 or found.incomplete:
        return Verdict("ambiguous", paths, found.incomplete or "defined in more than one file")
    only = claimants[0]
    if only.spellable:
        return Verdict("invalid", paths, only.problem)
    if subagent_type not in only.names:
        return Verdict("invalid", paths, "the file's name differs from the spawn type by case "
                                         "or Unicode form")
    if only.problem:
        return Verdict("unbound" if only.unbound else "invalid", paths, only.problem)
    return Verdict("project", paths)


def project_specialists(shipped: frozenset, found: Scan) -> frozenset:
    """The names a bare spawn would start as a valid project specialist."""
    names = {n for f in found.files for n in f.names if n != ANY_NAME}
    return frozenset(n for n in names if resolve(n, shipped, found).kind == "project")
