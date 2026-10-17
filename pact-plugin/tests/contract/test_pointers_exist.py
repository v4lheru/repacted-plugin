"""Contract 7, POINTERS: every skill, command, agent, protocol and reference file a text names exists.

1.3.8 deleted three stub skills and seventeen protocol stubs. A text that still names one sends its
reader to nothing. This check reads every markdown file under agents/, commands/, protocols/,
reference/ and skills/, and every .py and .json file under hooks/, and fails on:

  skill-call  Skill("repacted:<name>") where <name> is no skill and no command (both are invoked
              through the Skill tool; an agent type is not);
  name        `repacted:<name>` where <name> is no skill, command or agent type. A wildcard
              (`repacted:*`, or a name that ends in `-*`) or a placeholder (`repacted:<name>`)
              names nothing and is skipped;
  link        a markdown link to a .md file that does not exist from the linking file's directory;
  path        a `protocols/...md`, `reference/...md` or `references/...md` pointer that resolves
              nowhere: not from the file's directory, not from the plugin root, not in the file's
              own skill, and not in a skill named `repacted:<skill>` on the same line;
  file        a bare `pact-<name>.md` file name, or a `<name>.md §Section` pointer, when no file
              of that name is left in these directories.

A dead name is reported once: a Skill() call under skill-call only, a link under link only.

Whether a link lands on a "> Moved" stub is tests/test_no_stale_protocol_refs.py. A `repacted:<name>`
outside these six directories is the purity gate's dangling-ref class; that gate reads this file
too, so the fixtures below spell the namespace `@:` and `_ns` expands it.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from tests.contract._surfaces import PLUGIN_ROOT

SCOPE = ("agents", "commands", "protocols", "reference", "skills", "hooks")
KINDS = ("skill-call", "name", "link", "path", "file")

SKILL_CALL = re.compile(r"""Skill\(\s*\\*["']repacted:([A-Za-z0-9_-]+)""")
NAME = re.compile(r"repacted:([A-Za-z0-9_][A-Za-z0-9_-]*)(?![<{A-Za-z0-9_*-])")
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")
PATH = re.compile(r"(?<![\w./{}$-])(?<!\]\()((?:[\w.${}-]+/)*(?:protocols|reference|references)/[\w./{}<>*-]+?\.md)(?![\w-])")
BARE_FILE = re.compile(r"(?<![\w./{}*<>$-])(pact-[\w-]+\.md)(?![\w-])")
SECTION_OF = re.compile(r"(?<![\w.{}*<>$-])([A-Za-z][\w-]*\.md)`?,?\s+§")
PLACEHOLDER = re.compile(r"[{}<>*]")
PLUGIN_DIRS = tuple(f"{d}/" for d in SCOPE)

# Declared exceptions, each with its reason.
NOT_POINTERS = {
    "name": set(),
    # Design documents of the repository, kept outside the plugin and named by memory-hygiene.
    "file": {"repact-user-layer.md", "specialist-memory-inventory.md"},
}


def scoped_files(root: Path) -> list[Path]:
    found = []
    for directory in SCOPE:
        suffixes = (".md", ".py", ".json") if directory == "hooks" else (".md",)
        found += [p for p in (root / directory).rglob("*")
                  if p.is_file() and p.suffix in suffixes and "__pycache__" not in p.parts]
    return sorted(found)


def _skill_dir(path: Path, root: Path) -> Path | None:
    for parent in path.parents:
        if parent == root:
            return None
        if (parent / "SKILL.md").is_file():
            return parent
    return None


def _path_resolves(path: Path, token: str, line: str, root: Path) -> bool:
    candidates = [path.parent / token, root / token]
    for prefix in PLUGIN_DIRS:
        at = token.find(prefix)
        if at == 0 or (at > 0 and token[at - 1] == "/"):
            candidates.append(root / token[at:])
    at = token.find("references/")
    if at >= 0:
        own = _skill_dir(path, root)
        named = [root / "skills" / name for name in NAME.findall(line)]
        candidates += [skill / token[at:] for skill in ([own] if own else []) + named]
    return any(candidate.is_file() for candidate in candidates)


def scan(root: Path = PLUGIN_ROOT) -> tuple[list[str], Counter]:
    """(dead pointers as 'file:line: kind target', pointers checked per kind)."""
    skills = {p.parent.name for p in root.glob("skills/*/SKILL.md")}
    commands = {p.stem for p in root.glob("commands/*.md")}
    agents = {p.stem for p in root.glob("agents/*.md")}
    files = scoped_files(root)
    basenames = {p.name for p in files if p.suffix == ".md"}
    dead, checked = set(), Counter()

    def judge(kind: str, target: str, alive: bool, where: str) -> None:
        if target in NOT_POINTERS.get(kind, ()):
            return
        checked[kind] += 1
        if not alive:
            dead.add(f"{where}: {kind} {target}")

    for path in files:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            where = f"{path.relative_to(root).as_posix()}:{number}"
            called = set()
            for match in SKILL_CALL.finditer(line):
                called.add(match.start(1))
                judge("skill-call", match.group(1), match.group(1) in skills | commands, where)
            for match in NAME.finditer(line):
                if match.start(1) not in called:
                    judge("name", match.group(1), match.group(1) in skills | commands | agents, where)
            if path.suffix == ".md":
                for match in LINK.finditer(line):
                    target = match.group(1)
                    if not target.startswith(("http:", "https:")) and not PLACEHOLDER.search(target):
                        judge("link", target, (path.parent / target).resolve().is_file(), where)
            for match in PATH.finditer(line):
                if not PLACEHOLDER.search(match.group(1)):
                    judge("path", match.group(1), _path_resolves(path, match.group(1), line, root), where)
            for match in (*BARE_FILE.finditer(line), *SECTION_OF.finditer(line)):
                judge("file", match.group(1), match.group(1) in basenames, where)
    return sorted(dead), checked


def test_no_text_points_at_a_name_that_does_not_exist():
    dead, checked = scan()
    assert not dead, f"{len(dead)} pointer(s) to a name that does not exist:\n  " + "\n  ".join(dead)
    # Denominator: a scan that matched nothing would pass on any tree.
    assert all(checked[kind] >= 5 for kind in KINDS), dict(checked)


FIRST_ACTION = re.compile(r"""FIRST action,\s+Invoke Skill\(\\*["'](repacted:[A-Za-z0-9_-]+)""")
OLD_FIRST_SKILL = "repacted:" + "pact-team-registration"


def test_every_spawn_text_gives_the_teammate_skill_as_the_first_instruction():
    """One first instruction per teammate: the lead's spawn templates and the SubagentStart block
    name the same skill. The old skill is a stub nothing names, kept one release for lead sessions
    that started before 1.3.8. The template in bootstrap_gate.py is split over two source lines;
    tests/test_dispatch_gate.py reads it as the string the hook emits."""
    named, old_name_in = [], []
    for path in scoped_files(PLUGIN_ROOT):
        text = path.read_text(encoding="utf-8")
        named += [(path.relative_to(PLUGIN_ROOT).as_posix(), name) for name in FIRST_ACTION.findall(text)]
        if OLD_FIRST_SKILL in text:
            old_name_in.append(path.relative_to(PLUGIN_ROOT).as_posix())
    assert len(named) >= 4, named   # the persona, bootstrap and its detail file, the dispatch reference
    assert {name for _, name in named} == {"repacted:teammate"}, named
    assert old_name_in == [], old_name_in
    block = (PLUGIN_ROOT / "hooks" / "shared" / "peer_context.py").read_text(encoding="utf-8")
    assert 'invoke Skill(\\"repacted:teammate\\") before anything else' in block


def _ns(text: str) -> str:
    return text.replace("@:", "repacted:")


def _tree(root: Path, text: str) -> Path:
    for rel_path, body in {
        "skills/teammate/SKILL.md": "# Teammate\n",
        "skills/teammate/references/teammate-handoff.md": "# HANDOFF\n",
        "skills/other/SKILL.md": _ns(text),
        "commands/bootstrap.md": "# bootstrap\n",
        "agents/pact-architect.md": "# architect\n",
        "protocols/workflows.md": "# Workflows\n",
        "reference/dispatch/gate.md": "# Gate\n",
        "hooks/gate.py": '"""Nothing."""\n',
    }.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root


LIVE = "\n".join([
    'Invoke Skill("@:teammate"), then Skill(\\"@:bootstrap\\").',
    "Spawn @:pact-architect; see @:teammate (`references/teammate-handoff.md`).",
    "Every @:* skill, each @:n8n-* skill, any @:<name> and @:{name}.",
    "[flow](../../protocols/workflows.md#variety) and [gate](../../reference/dispatch/gate.md).",
    "Read `protocols/workflows.md`, then ${CLAUDE_PLUGIN_ROOT}/reference/dispatch/gate.md.",
    "See workflows.md §Variety and pact-architect.md; write `docs/reference/{name}.md`.",
])


def test_the_scan_passes_a_tree_whose_pointers_all_resolve(tmp_path):
    dead, checked = scan(_tree(tmp_path, LIVE))
    assert dead == []
    assert all(checked[kind] >= 1 for kind in KINDS), dict(checked)


DELETED = [
    ('Invoke Skill("@:pact-teachback").', "skill-call pact-teachback"),
    ('Invoke Skill(\\"@:request-more-context\\").', "skill-call request-more-context"),
    # An agent type is not invoked through the Skill tool.
    ('Invoke Skill("@:pact-architect").', "skill-call pact-architect"),
    ("The fields are in @:pact-agent-teams.", "name pact-agent-teams"),
    ("[audit](../../protocols/pact-audit.md#cycles)", "link ../../protocols/pact-audit.md"),
    ("Read `protocols/pact-s5-policy.md` first.", "path protocols/pact-s5-policy.md"),
    ("Long form: `reference/secretary/body-before-m7.md`.", "path reference/secretary/body-before-m7.md"),
    ("See `references/teammate-gone.md`.", "path references/teammate-gone.md"),
    ("per pact-variety.md", "file pact-variety.md"),
    ("See `vsm-glossary.md` §Terms.", "file vsm-glossary.md"),
]


def test_the_scan_reports_a_pointer_to_each_kind_of_deleted_name(tmp_path):
    text = LIVE + "\n" + "\n".join(line for line, _ in DELETED)
    dead, _ = scan(_tree(tmp_path, text))
    reported = {entry.split(": ", 1)[1] for entry in dead}
    expected = {report for _, report in DELETED}
    assert reported == expected, sorted(reported ^ expected)
    assert len(dead) == len(DELETED), dead   # each dead name once
    assert all(entry.startswith("skills/other/SKILL.md:") for entry in dead), dead
