"""
Location: pact-plugin/hooks/shell/settings.py
Summary: Which paths hold Claude Code settings or the running plugin's hooks, and which of
         them a Bash command would write. Protected: any `.claude/settings.json` and
         `.claude/settings.local.json`, `<config dir>/settings*.json`, the user config
         (`~/.claude.json` or `<config dir>/.claude.json`), the running plugin's
         hooks/ (hooks.json with it) and .claude-plugin/, the plugin cache under the
         config dir, and the workflow state the repact commands own: `<config dir>/tasks/`
         and `<config dir>/pact-sessions/` (TASK_STATE). A source checkout of the plugin anywhere else is not protected. Paths
         are compared after symlinks resolve, case-folded and NFC-normalised (APFS reads
         `Settings.JSON` as `settings.json`).
Used by: hooks/settings_guard.py (the verdict and the frame), tested by
         tests/test_settings_guard.py.
Map: protected_place, written_place, the places (_places, _within), word reading
     (_pieces, _braces, _expand, _candidates), globs (_glob, _could_be, _globbed), program
     classes (READERS, _writes, claude_cli_writes), _scan.

Standard library, shell.words and shell.env.script only (no `shared` import: it runs before
every Bash call; the launcher shim's text comes in from the caller, settings_guard).
A Bash command is read the way the env guard reads one: read_script, then the words of each
simple command, substitutions on their own. A word counts when it names a protected path,
also inside a longer word (`of=PATH`, a `-c` script, `open('PATH','w')`); the command
counts when a program that can write runs with it, or a redirect targets it. A word holding
* ? or [ is expanded the way bash would, whatever words it holds; when the walk
finds nothing or passes its bound, the pattern is compared with the protected names
themselves. A paren group glued to word text (zsh's `(a|b)` alternation, a `(N)` qualifier,
ksh's `@(a)`) is read as a glob at least as wide as the group (_paren_globs). The claude CLI's own writers (`claude mcp add`, `config set`, `plugin install`)
count as a write of the user config or the loader state.

Task state (TASK_STATE): the task files and the session dirs (ops.jsonl, unattended.json, the
journal) are written by the host's task tools and by the plugin's own processes, never by a
Bash word or a file tool. A program the plugin ships (a file under the running plugin's bin/, scripts/
or skills/, or the launcher shim on PATH whose bytes are exactly the shim text the caller
passes, shared.repact_shim.shim_text()) may name
them as operands (`repact-journal-write --session-dir <dir>`), except as an --out or --output
value; a redirect it runs with is judged as any other. Resolved by path, never by name
(plugin_tool).
Task state is read the way the shell runs the command: the names a
command opens with (`f=PATH;`, `export d=tasks &&`) are spelled out first through
shell.env.script.expand_literal_variables, so `$f`, `${d}` and a nested `b=$a/x` resolve; a
path through a symlink resolves to where it lands (_through_link); find -delete, -fprint and
an -exec that runs a writer count as writes of every root that holds or lies in the task or
session folders (_find_writes), and so does an xargs writer fed words that name or hold them
(_feeds_state). A written word that still holds a variable or a command's output it cannot
spell is refused as UNSPELLED when the command also names the config dir (in any spelling, or
by a cd into it) or pact-sessions; a bare `tasks` does not count,
since repos have their own tasks/ folders.

Honest limit: it reads the command as written. A script file that opens the path itself, a
name the shell assembles at run time from parts this reader does not expand (in a command that
never names the config dir), the file an xargs reads its words from, and a symlink made by a
program it does not see are not caught. Quotes are gone by the time a word is
judged, so a quoted glob, or a regex such as `.*` given to `sed -i` from the home directory,
is judged as a glob and may be refused.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import unicodedata
from pathlib import Path

from shell.env.script import expand_literal_variables
from shell.words import (Unreadable, quote_spans, read_script, runner_prefix, strip_prefix,
                         substitution_spans, tokens)

SETTINGS_NAMES = ("settings.json", "settings.local.json")
_OPERATORS = "<>|;&()\n"
_SEPARATORS = frozenset({"|", "||", "&", "&&", ";", ";;", "(", ")", "\n", "|&"})
_REDIRECTS = frozenset({">", ">>", ">|", "&>", "&>>", "<>", ">&"})
# Words that make a name in the command a candidate at all (after brace expansion).
_TRIGGERS = ("settings", "hooks", "claude-plugin", "plugins", ".claude", "checks.json", "tasks",
             "pact-sessions")
# Under <config>/plugins: the loader's records, whose installPath or source picks the code
# that runs. bin/checks.json (the gate modes) and plugins/data (the run ledger and gate
# state) are protected beside them; the plugin's own processes write there, not a Bash word.
LOADER_FILES = ("installed_plugins.json", "known_marketplaces.json")
# The user config (~/.claude.json, or .claude.json in CLAUDE_CONFIG_DIR): its user-scope
# mcpServers name commands the next session starts.
USER_CONFIG = ".claude.json"
# The workflow state under the config dir: what each place is called in a
# refusal. settings_guard denies these for every frame (rule settings.task_state).
TASK_FILES = "the task files (workflow state the repact commands own)"
SESSION_STATE = "the PACT session state (ops log, unattended flag, journal)"
# A written word the reader cannot spell, in a command that names the config dir.
UNSPELLED = ("a path held in a variable or a command's output this check cannot spell, in a "
             "command that names the Claude config dir or pact-sessions")
TASK_STATE = frozenset({TASK_FILES, SESSION_STATE, UNSPELLED})
_STATE_DIRS = (("tasks", "tasks", TASK_FILES), ("sessions", "pact-sessions", SESSION_STATE))
_TASK_FILE_TAIL = re.compile(r"/tasks/[^/]+/[^/]+\.json$")
# A plugin tool's option whose value is a file it writes: judged even for a plugin tool.
_OUTPUT_OPTIONS = ("--out", "--output", "-o")
PLUGIN_CODE = ("bin", "scripts", "skills")  # the plugin's own program dirs (plugin_tool)
_PIECE_SPLIT = re.compile(r"[\s=,:;()<>|&\x27\x22`]+")
_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_BRACE = re.compile(r"\{([^{}]*,[^{}]*)\}")
MAX_EXPANSIONS = 64
MAX_DEPTH = 8
# What a $( ) or `...` stands for in the command around it: a word with a variable in it.
SUBSTITUTED = "$_repact_substituted"
# A glob walk stops past this many directory entries read or paths matched; the pattern is
# then judged by the names it could match.
MAX_GLOB_ENTRIES = 4096
MAX_GLOB_MATCHES = 256
_MAGIC = re.compile(r"[*?\[]")
# A `(` right after one of these is shell syntax ($( ), <( ), a=( ), a subshell), not a glob;
# a `)` followed by one of these ends a word. A group stops at a character zsh would not keep
# inside one word.
_PAREN_SYNTAX_BEFORE = frozenset(" \t\n;&|<>()$=`{")
_PAREN_SYNTAX_AFTER = frozenset(" \t\n;&|<>()`")
_PAREN_STOP = frozenset(" \t\n;&<>`")
# What a glued group becomes: a glob at least as wide as any group, in a dotted and a plain
# form, since a wildcard never matches a leading dot.
PAREN_GLOB = "{.*,*}"
_EXTGLOB_PREFIX = frozenset("@!+*?")
# What a glob tail behind an unknown variable is compared with, and the tails too wide to
# judge (`$OUT/*`, `$OUT/*.json`): the literal settings name is refused on its own.
_TAIL_NAMES = SETTINGS_NAMES + ("hooks.json", USER_CONFIG) + LOADER_FILES
_WIDE_TAILS = frozenset({"", ".", ".json"})

# The claude CLI writes the user config (mcp, config) and the loader state (plugin) with no
# path word in the command. A verb under these groups that is not listed here as
# read-only is a write.
CLAUDE_READ_VERBS = {"mcp": frozenset({"list", "get", "serve", "help"}),
                     "config": frozenset({"get", "list", "help"}),
                     "plugin": frozenset({"list", "validate", "help"}),
                     "marketplace": frozenset({"list", "help"})}
CLAUDE_CLI = "the Claude Code user config or plugin state through the claude CLI"
_CLAUDE_PACKAGE = re.compile(r"(?:--package=)?@anthropic-ai/claude-code(?:@\S+)?")
_PACKAGE_RUNNERS = frozenset({"npx", "pnpx", "bunx"})
_PACKAGE_VERBS = {"pnpm": "dlx", "yarn": "dlx", "npm": "exec", "bun": "x"}
# An unreadable command that names a claude CLI writer group (settings_guard's fallback).
CLAUDE_CLI_WORDS = re.compile(r"\bclaude(?:-code)?\b[^\n|;&]*\b(?:mcp|config|plugin)\b")

# Programs that never write a file they name; a redirect is checked on its own.
READERS = frozenset({
    "cat", "bat", "less", "more", "head", "tail", "wc", "file", "stat", "ls", "du", "grep",
    "egrep", "fgrep", "rg", "ag", "jq", "diff", "cmp", "md5", "md5sum", "shasum", "sha1sum",
    "sha256sum", "cksum", "test", "[", "realpath", "readlink", "basename", "dirname", "echo",
    "printf", "true", ":", "cd", "pushd", "popd", "which", "type", "env-get", "pytest", "mypy",
})
# Writers that take an in-place flag: a reader without it.
IN_PLACE = frozenset({"sed", "gsed", "yq"})
FIND_WRITES = frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint",
                         "-fprint0", "-fprintf", "-fls"})
_FIND_RUNS = frozenset({"-exec", "-execdir", "-ok", "-okdir"})     # judged by the program run
_FIND_LEADING = frozenset({"-H", "-L", "-P", "-E", "-X", "-d", "-s", "-x"})   # before the roots
GIT_WRITES = frozenset({"checkout", "restore", "mv", "rm", "reset", "apply", "am", "clean",
                        "config", "stash", "switch", "worktree", "archive"})
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
# Copies: only the destination is written (the last operand, or a --target-directory), and
# it may be a directory the source lands in. mv also removes its sources (REMOVERS).
COPIERS = frozenset({"cp", "mv", "ln", "install", "rsync", "ditto", "gcp"})
_TARGET_OPTIONS = frozenset({"-t", "--target-directory"})
# Removers and movers: naming a directory that holds a protected place counts too.
REMOVERS = frozenset({"rm", "rmdir", "unlink", "trash", "mv", "chmod", "chown", "chflags"})
_SCRIPT_RUNNERS = re.compile(r"(?:ba|z|da|k|fi)?sh|eval|(?:python|pypy|perl|ruby|php|node|deno)[\d.]*")
# Interpreter options after which the next word is code, not a script file to run.
_CODE_OPTIONS = frozenset({"-c", "-e", "-E", "-m", "-p", "-r", "--eval", "--command", "-"})


def _named(program: str, args: list[str]) -> list[str]:
    """The words a writing program may write to. An interpreter's script operand is run,
    not written (`python3 "$CLAUDE_PLUGIN_ROOT/bin/repact-init"`), so it is left out."""
    if not _SCRIPT_RUNNERS.fullmatch(program):
        return args
    for i, word in enumerate(args):
        if word in _CODE_OPTIONS:
            return args
        if not word.startswith("-"):
            return args[:i] + args[i + 1:]
    return args


def _norm(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _places(environ) -> dict:
    """The protected places of this process, each as a normalised resolved path."""
    home = environ.get("HOME") or os.path.expanduser("~")
    config = environ.get("CLAUDE_CONFIG_DIR") or os.path.join(home, ".claude")
    root = Path(__file__).resolve().parents[2]
    resolved = {
        "config": os.path.realpath(os.path.expanduser(config)),
        "hooks": os.path.realpath(root / "hooks"),
        "manifest": os.path.realpath(root / ".claude-plugin"),
    }
    plugins = os.path.join(resolved["config"], "plugins")
    resolved["cache"] = os.path.join(plugins, "cache")
    resolved["data"] = os.path.join(plugins, "data")
    resolved["checks"] = os.path.realpath(root / "bin" / "checks.json")
    resolved["bin"] = os.path.realpath(root / "bin")
    resolved["scripts"] = os.path.realpath(root / "scripts")
    resolved["skills"] = os.path.realpath(root / "skills")
    for key, name, _what_it_is in _STATE_DIRS:
        resolved[key] = os.path.realpath(os.path.join(resolved["config"], name))
    files = {_norm(os.path.join(plugins, name)) for name in LOADER_FILES} | {_norm(resolved["checks"])}
    user_config = {_norm(os.path.realpath(os.path.join(d, USER_CONFIG)))
                   for d in (os.path.expanduser(home), resolved["config"])}
    own_data = environ.get("CLAUDE_PLUGIN_DATA")
    if own_data:
        resolved["own_data"] = os.path.realpath(os.path.expanduser(own_data))
    return {key: _norm(value) for key, value in resolved.items()} | {
        "home": home, "config_raw": config, "root": str(root), "files": files,
        "user_config": user_config, "listings": {}, "globbed": {}, "judged": {}, "links": {},
        "dirs_seen": [], "unspelled": False}


def _within(path: str, place: str) -> bool:
    return path == place or path.startswith(place.rstrip("/") + "/")


def protected_place(path: str, cwd: str, environ=None) -> str | None:
    """What `path` (absolute or relative to cwd) is, when it is protected: a settings file,
    the running plugin's hooks or manifest, or the plugin cache; else None. Both the typed
    path and its symlink-resolved form are checked."""
    places = _places(os.environ if environ is None else environ)
    return _what(path, cwd, places)


def _what(path: str, cwd: str, places: dict) -> str | None:
    joined = os.path.join(cwd, path)
    absolute = os.path.normpath(joined)
    # normpath drops `link/..` before the kernel would follow the link, so the raw join is
    # resolved too: protocols/pact-plugin/../hooks/X is the running hooks.
    for candidate in dict.fromkeys((_norm(absolute), _norm(os.path.realpath(absolute)),
                                    _norm(os.path.realpath(joined)))):
        parent, name = os.path.split(candidate)
        if name in SETTINGS_NAMES and os.path.basename(parent) == ".claude":
            return "a Claude Code settings file"
        if parent == places["config"] and name.startswith("settings") and name.endswith(".json"):
            return "a Claude Code settings file"
        if _within(candidate, places["hooks"]):
            return "the running plugin's hooks"
        if _within(candidate, places["manifest"]):
            return "the running plugin's manifest"
        if _within(candidate, places["cache"]):
            return "the plugin cache"
        if candidate in places["files"]:
            return "the plugin loader's state or the gate modes"
        if candidate in places["user_config"]:
            return "the Claude Code user config (its MCP servers)"
        if any(_within(candidate, places[key]) for key in ("data", "own_data") if key in places):
            return "the plugin data (run ledger and gate state)"
        for key, _name, what in _STATE_DIRS:
            if _within(candidate, places[key]):
                return what
    return None


def _holds_place(path: str, cwd: str, places: dict) -> bool:
    """A directory whose removal or move takes a protected place with it."""
    candidate = _norm(os.path.realpath(os.path.join(cwd, path)))
    if os.path.basename(candidate) == ".claude":
        return True
    return any(_within(places[key], candidate) for key in ("config", "hooks", "manifest", "data",
                                                            "checks", "own_data", "tasks", "sessions")
               if key in places)


def _braces(word: str) -> list[str]:
    out, pending = [], [word]
    while pending and len(out) + len(pending) <= MAX_EXPANSIONS:
        item = pending.pop()
        match = _BRACE.search(item)
        if match is None:
            out.append(item)
            continue
        pending += [item[:match.start()] + choice + item[match.end():]
                    for choice in match.group(1).split(",")]
    return out + pending


def _component_match(name: str, pattern: str, folded: bool = False) -> bool:
    """One path component against one glob component, as bash matches it: case-sensitive,
    and a wildcard never matches a leading dot. `folded` compares case-folded names."""
    if folded:
        name, pattern = _norm(name), _norm(pattern)
    if name.startswith(".") and not pattern.startswith("."):
        return False
    return fnmatch.fnmatchcase(name, pattern)


def _glob(pattern: str, cwd: str, places: dict) -> list[str] | None:
    """The existing paths a glob names, walked one component at a time from cwd; None when
    the walk reads more than MAX_GLOB_ENTRIES entries or keeps more than MAX_GLOB_MATCHES
    paths. Directory listings are cached for the one command being read."""
    absolute = os.path.join(cwd, pattern)
    current, read = ["/"], 0
    for part in absolute.split("/"):
        if not part:
            continue
        if not _MAGIC.search(part):
            current = [os.path.join(path, part) for path in current]
            continue
        found = []
        for path in current:
            listing = places["listings"].get(path)
            if listing is None:
                try:
                    listing = sorted(os.listdir(path))[:MAX_GLOB_ENTRIES + 1]
                except OSError:
                    listing = []
                places["listings"][path] = listing
            read += len(listing)
            if read > MAX_GLOB_ENTRIES:
                return None
            found += [os.path.join(path, name) for name in listing
                      if _component_match(name, part)]
        if len(found) > MAX_GLOB_MATCHES:
            return None
        current = found
    return [path for path in current if os.path.lexists(path)]


def _could_be(pattern: str, cwd: str, places: dict, holds: bool = False) -> str | None:
    """What protected place a glob could name, read from the names alone (no file on disk
    yet, or a walk past its bound). The literal head resolves like any path; the rest is
    matched component by component, case-folded. `holds` also counts a directory that
    holds a protected place (a remover's word)."""
    parts = os.path.normpath(os.path.join(cwd, pattern)).split("/")
    head = next((i for i, part in enumerate(parts) if _MAGIC.search(part)), len(parts))
    literal = _norm(os.path.realpath("/".join(parts[:head]) or "/")).rstrip("/")
    parts = literal.split("/") + parts[head:]

    def fits(place: str, within: bool) -> bool:
        target = place.split("/")
        if len(parts) < len(target) or (not within and len(parts) != len(target)):
            return False
        return all(_component_match(name, part, folded=True) for name, part in zip(target, parts))

    def ancestor(place: str) -> bool:
        target = place.split("/")
        return len(parts) < len(target) and all(
            _component_match(name, part, folded=True) for name, part in zip(target, parts))

    if len(parts) >= 2 and _component_match(".claude", parts[-2], folded=True) and any(
            _component_match(name, parts[-1], folded=True) for name in SETTINGS_NAMES):
        return "a Claude Code settings file"
    if any(fits(f"{places['config']}/{name}", False) for name in SETTINGS_NAMES):
        return "a Claude Code settings file"
    for key, what in (("hooks", "the running plugin's hooks"),
                      ("manifest", "the running plugin's manifest"),
                      ("cache", "the plugin cache"),
                      ("data", "the plugin data (run ledger and gate state)"),
                      ("own_data", "the plugin data (run ledger and gate state)"),
                      ("tasks", TASK_FILES), ("sessions", SESSION_STATE)):
        if key in places and fits(places[key], True):
            return what
    if any(fits(name, False) for name in places["files"]):
        return "the plugin loader's state or the gate modes"
    if any(fits(name, False) for name in places["user_config"]):
        return "the Claude Code user config (its MCP servers)"
    if holds and (_component_match(".claude", parts[-1], folded=True) or any(
            ancestor(places[key]) for key in ("config", "hooks", "manifest", "data", "checks",
                                              "own_data", "tasks", "sessions") if key in places)):
        return "a directory that holds settings or the running plugin"
    return None


def _globbed(pattern: str, cwd: str, places: dict, holds: bool = False) -> str | None:
    """What protected place a glob names in cwd: every match judged, and the names it could
    match when it matches nothing on disk or passes the walk's bound. Verdicts are
    kept for the one command being read: a script repeats `*` and `a[0]` often."""
    key = (pattern, cwd, holds)
    if key not in places["globbed"]:
        places["globbed"][key] = _judge_glob(pattern, cwd, places, holds)
    return places["globbed"][key]


def _judge_glob(pattern: str, cwd: str, places: dict, holds: bool) -> str | None:
    names = _glob(pattern, cwd, places)
    for name in names or ():
        if name not in places["judged"]:
            places["judged"][name] = _what(name, cwd, places) or (
                "a directory that holds settings or the running plugin"
                if _holds_place(name, cwd, places) else "")
        what = places["judged"][name]
        if what and (holds or not what.startswith("a directory that holds")):
            return what
    return None if names else _could_be(pattern, cwd, places, holds)


def _expand(piece: str, places: dict, cwd: str, environ) -> str:
    """~ and the variables the hook knows (HOME, PWD, CLAUDE_CONFIG_DIR, CLAUDE_PLUGIN_ROOT,
    CLAUDE_PROJECT_DIR) spelled out; any other variable is left as written."""
    if piece == "~" or piece.startswith("~/"):
        piece = places["home"] + piece[1:]
    known = {"HOME": places["home"], "PWD": cwd, "CLAUDE_CONFIG_DIR": places["config_raw"],
             "CLAUDE_PLUGIN_ROOT": environ.get("CLAUDE_PLUGIN_ROOT") or places["root"]}
    if environ.get("CLAUDE_PROJECT_DIR"):
        known["CLAUDE_PROJECT_DIR"] = environ["CLAUDE_PROJECT_DIR"]
    return _VARIABLE.sub(lambda m: known.get(m.group(1) or m.group(2), m.group(0)), piece)


def _pieces(word: str) -> list[str]:
    return [piece for piece in [word] + _PIECE_SPLIT.split(word) if piece]


def _unknown_tail(piece: str) -> str | None:
    """A name built from a variable the hook cannot spell, whose own tail is protected."""
    if "$" not in piece:
        return None
    tail = _norm(piece.rsplit("/", 1)[-1])
    lowered = _norm(piece)
    if "/pact-sessions/" in lowered or lowered.endswith("/pact-sessions"):
        return SESSION_STATE
    if _TASK_FILE_TAIL.search(lowered):
        return TASK_FILES
    if (tail.startswith("settings") and tail.endswith(".json")) or tail == "hooks.json" \
            or "/.claude-plugin" in lowered or "/plugins/cache" in lowered \
            or "/plugins/data/" in lowered or tail in LOADER_FILES or tail == USER_CONFIG:
        return "a settings or hook file named through a variable"
    if _MAGIC.search(tail) and re.sub(r"\[[^\]]*\]|[*?]", "", tail) not in _WIDE_TAILS \
            and any(_component_match(name, tail, folded=True) for name in _TAIL_NAMES):
        return "a settings or hook file named through a variable"
    return None


def _candidates(word: str, dirs: list[str], places: dict, environ,
                holds: bool = False) -> str | None:
    """What the first protected path `word` names is, in any directory the command runs in.
    `holds`: the word is a remover's, so a glob naming a directory that holds a protected
    place counts too."""
    inside = any(_what(".", d, places) or _within(_norm(os.path.realpath(d)), places["config"])
                 for d in dirs)
    for piece in _pieces(word):
        for spelled in _braces(piece):
            spelled = _expand(spelled, places, dirs[0], environ)
            unknown = _unknown_tail(spelled)
            if unknown:
                return unknown
            lowered = _norm(spelled)
            relative = not spelled.startswith("/")
            if _MAGIC.search(spelled):
                # A glob is judged by what it matches, whatever words it holds.
                for d in (dirs if relative else dirs[:1]):
                    what = _globbed(spelled, d, places, holds)
                    if what:
                        return what
                continue
            if not (any(t in lowered for t in _TRIGGERS) or (relative and inside)
                    or (len(spelled) < 1024 and os.path.islink(os.path.join(dirs[0], spelled)))
                    or _through_link(spelled, dirs[0], places)):
                continue
            for d in (dirs if relative else dirs[:1]):
                what = _what(spelled, d, places)
                if what:
                    return what
    return None


def _through_link(path: str, cwd: str, places: dict) -> bool:
    """Whether a directory on the way to `path` is a symlink, so the write lands somewhere the
    typed words do not say (`ln -s <config>/tasks/team l; echo > l/5.json`).
    Read once per directory for the one command being read."""
    if "/" not in path.rstrip("/") or len(path) >= 1024 or "$" in path:
        return False
    parent = os.path.dirname(os.path.normpath(os.path.join(cwd, path)))
    if parent not in places["links"]:
        places["links"][parent] = os.path.realpath(parent) != parent
    return places["links"][parent]


def _unspelled(word: str, places: dict, cwd: str, environ) -> bool:
    """Whether `word` still holds a variable or a command's output after the hook's own
    names are spelled: where it lands is not known here."""
    spelled = _expand(word, places, cwd, environ)
    return "$" in spelled or "`" in spelled


def _state_held(word: str, dirs: list[str], places: dict, environ) -> str | None:
    """TASK_FILES or SESSION_STATE when `word` (a find root, or a word fed to an xargs
    writer) lies in or holds the task or session folders, read through links."""
    spelled = _expand(word, places, dirs[0], environ)
    if not spelled or spelled.startswith("-") or "$" in spelled or len(spelled) >= 1024:
        return None
    for d in (dirs if not spelled.startswith("/") else dirs[:1]):
        resolved = _norm(os.path.realpath(os.path.join(d, spelled)))
        for key, _name, what in _STATE_DIRS:
            if _within(resolved, places[key]) or _within(places[key], resolved):
                return what
    return None


def _find_roots(args: list[str]) -> list[str]:
    """The paths find starts from: the words before its first expression word (`.` when none)."""
    roots = []
    for word in args:
        if word in _FIND_LEADING:
            continue
        if word.startswith(("-", "(", "!", ")")):
            break
        roots.append(word)
    return roots or ["."]


def _find_acts(args: list[str]) -> bool:
    """Whether a find writes: -delete, -fprint*, -fls, or an -exec/-ok that runs a program
    which may write (`-exec cat {} \\;` reads)."""
    for i, word in enumerate(args):
        if word in _FIND_RUNS:
            run = args[i + 1:]
            end = next((k for k, w in enumerate(run) if w in (";", "\\;", "+")), len(run))
            run = strip_prefix(run[:end])
            program = run[0].rsplit("/", 1)[-1] if run else ""
            if not run or _writes(program, run[1:]) or claude_cli_writes(program, run[1:]):
                return True
        elif word in FIND_WRITES:
            return True
    return False


def _find_writes(args: list[str], dirs: list[str], places: dict, environ) -> str | None:
    """What task state a writing find reaches from its roots."""
    for root in _find_roots(args):
        if _unspelled(root, places, dirs[0], environ):
            places["unspelled"] = True
        what = _state_held(root, dirs, places, environ)
        if what:
            return what
    return None


def _feeds_state(feed: list[str], dirs: list[str], places: dict, environ) -> str | None:
    """What protected place the words piped or redirected into an xargs writer name or hold
    (`find . -name 5.json | xargs sed -i ...`, `echo PATH | xargs rm`)."""
    for word in feed:
        if _unspelled(word, places, dirs[0], environ):
            places["unspelled"] = True
        what = _candidates(word, dirs, places, environ) or _state_held(word, dirs, places, environ)
        if what:
            return what
    return None


def _runs_under_xargs(words: list[str]) -> bool:
    return any(runner == "xargs" for runner, _option, _value in runner_prefix(words)[1])


def _names_state(command: str, cwd: str, places: dict, environ) -> bool:
    """Whether `command` names the config dir in any spelling (a path in it, ~/.claude,
    $CLAUDE_CONFIG_DIR, a cd into it) or pact-sessions. The
    running plugin and the plugin cache under it do not count; a bare `tasks` does not."""
    folded = _norm(command)
    if "pact-sessions" in folded or "claude_config_dir" in folded:
        return True
    plugin = _norm(os.path.realpath(places["root"]))

    def inside(path: str) -> bool:
        path = _norm(os.path.realpath(path))
        return _within(path, places["config"]) and not _within(path, places["cache"]) \
            and not _within(path, plugin)

    dirs = [cwd] + places["dirs_seen"]
    if any(inside(d) for d in dirs):
        return True
    base = os.path.basename(places["config"])
    pieces = list(dict.fromkeys(piece for piece in _PIECE_SPLIT.split(command) if piece))
    if len(pieces) > MAX_GLOB_ENTRIES:
        return True                                  # too long to read: fail closed
    for piece in pieces:
        head = re.split(r"[$`]", _expand(piece, places, cwd, environ), maxsplit=1)[0]
        if head and len(head) < 1024 and ("/" in head or _norm(head).rstrip("/") == base) \
                and any(inside(os.path.join(d, head)) for d in dirs):
            return True
    return False


def _git_writes(args: list[str]) -> bool:
    i = 0
    while i < len(args):
        word = args[i]
        if word in _GIT_VALUE_OPTIONS:
            i += 2
        elif word.startswith("-"):
            i += 1
        else:
            return word in GIT_WRITES
    return False


def _writes(program: str, args: list[str]) -> bool:
    """Whether this program may write a file it names (unknown programs may)."""
    if program in READERS:
        return False
    if program in IN_PLACE:
        return any(a.startswith("--in-place") or (a.startswith("-") and not a.startswith("--")
                                                   and "i" in a[1:]) for a in args)
    if program == "find":
        return _find_acts(args)
    if program == "git":
        return _git_writes(args)
    return True


def claude_cli_writes(program: str, args: list[str]) -> bool:
    """Whether this runs the claude CLI with a verb that writes the user config or the
    plugin loader state: `claude mcp add`, `config set`, `plugin install`,
    `plugin marketplace add`, also through npx, pnpm dlx, yarn dlx, npm exec or bunx.
    Read-only verbs, -h/--help, and a -p/--print run (its words are a prompt) pass."""
    if program in _PACKAGE_RUNNERS or (args[:1] == [_PACKAGE_VERBS.get(program)]):
        rest = args if program in _PACKAGE_RUNNERS else args[1:]
        for i, word in enumerate(rest):
            if _CLAUDE_PACKAGE.fullmatch(word):
                return claude_cli_writes("claude", rest[i + 1:])
            if not word.startswith("-"):
                return False
        return False
    own = args[:args.index("--")] if "--" in args else args     # after --: the server's words
    if program not in ("claude", "claude-code") or any(a in ("-h", "--help") for a in own):
        return False
    verbs: list[str] = []
    for word in own:
        if word in ("-p", "--print") and not verbs:
            return False
        # Words before the group are skipped, not trusted: an option value or a stray word
        # cannot hide `mcp add` behind it.
        if word.startswith("-") or (not verbs and word not in CLAUDE_READ_VERBS) \
                or (not verbs and word == "marketplace"):
            continue
        verbs.append(word)
        if verbs != ["plugin", "marketplace"] and len(verbs) >= 2:
            break
    if verbs[:2] == ["plugin", "marketplace"]:
        verbs = verbs[1:]
    return len(verbs) >= 2 and verbs[1] not in CLAUDE_READ_VERBS[verbs[0]]


def _is_plugin_file(word: str, cwd: str, places: dict, environ) -> bool:
    """Whether `word` (a program or script word) is a file of the running plugin's bin/,
    scripts/ or skills/, or the launcher shim on PATH: a bare name is looked up on PATH, then
    the file is resolved."""
    word = _expand(word, places, cwd, environ)
    if "/" in word:
        path = os.path.join(cwd, word)
    else:
        found = shutil.which(word, path=environ.get("PATH", os.defpath))
        if found is None:
            return False
        path = found
    real = os.path.realpath(path)
    if not os.path.isfile(real):
        return False
    if any(_within(_norm(real), places[key]) for key in PLUGIN_CODE):
        return True
    return os.path.basename(real) == "repact" and _is_launcher_shim(real, places.get("shim"))


def _is_launcher_shim(path: str, shim: str | None) -> bool:
    """The launcher shim, by its exact bytes (the caller's shim text), never by name."""
    if not shim:
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(len(shim) + 1) == shim
    except (OSError, UnicodeDecodeError):
        return False


def plugin_tool(command: list[str], cwd: str, places: dict, environ) -> bool:
    """Whether this simple command runs a program the plugin ships: its program word, or an
    interpreter's script operand (`python3 "$CLAUDE_PLUGIN_ROOT/bin/repact-task"`)."""
    if not command:
        return False
    program = command[0].rsplit("/", 1)[-1]
    if not _SCRIPT_RUNNERS.fullmatch(program):
        return _is_plugin_file(command[0], cwd, places, environ)
    for word in command[1:]:
        if word in _CODE_OPTIONS:
            return False
        if not word.startswith("-"):
            return _is_plugin_file(word, cwd, places, environ)
    return False


def _output_values(args: list[str]) -> set[int]:
    """The indexes of the words an --out/--output/-o option names (`--out X`, `--out=X`)."""
    found = set()
    for i, word in enumerate(args):
        if word in _OUTPUT_OPTIONS and i + 1 < len(args):
            found.add(i + 1)
        elif any(word.startswith(option + "=") for option in _OUTPUT_OPTIONS):
            found.add(i)
    return found


def _copy_words(args: list[str]) -> tuple[list[str], list[str]]:
    """(destination words, sources) of a copy: the --target-directory value, else the last
    operand; every option word with a value (`--backup-dir=X`) counts as a destination."""
    operands, targets, i = [], [], 0
    while i < len(args):
        word = args[i]
        if word in _TARGET_OPTIONS and i + 1 < len(args):
            targets.append(args[i + 1])
            i += 2
            continue
        if word.startswith("-"):
            targets += [word] if "=" in word else []
        else:
            operands.append(word)
        i += 1
    if targets and not any(t.startswith("-") for t in targets[:1]):
        return targets, operands
    return targets + operands[-1:], operands[:-1]


def _into_directory(destinations: list[str], sources: list[str], dirs: list[str],
                    places: dict, environ) -> str | None:
    """cp/mv/ln/install SRC... DIR: the path each source takes inside DIR."""
    for target in destinations:
        target = _expand(target, places, dirs[0], environ)
        if target.startswith("-") or not (target.endswith("/")
                                          or os.path.isdir(os.path.join(dirs[0], target))):
            continue
        for source in sources:
            name = os.path.basename(source.rstrip("/"))
            what = _what(os.path.join(target, name), dirs[0], places)
            if what:
                return what
    return None


def _group_end(text: str, start: int, quoted: dict) -> int | None:
    """The index of the `)` that closes the `(` at `start` within one word, else None.
    `quoted` maps each quoted string's start to its end (words.quote_spans)."""
    depth, i = 0, start
    while i < len(text):
        if i in quoted:
            i = quoted[i]
            continue
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c in _PAREN_STOP:
            return None
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _paren_globs(text: str) -> str:
    """The command with each unquoted, non-empty paren group glued to word text (`.cl(a)ude`,
    `x(a|b)`, `name(N)`, `@(a)`) spelled PAREN_GLOB: the Bash tool runs zsh, which globs such a
    group. Wider than the group, so the glob judgement fails closed. Parens that are
    shell syntax ($( ), <( ), a=( ), f(), a subshell, a case pattern) are left alone. Quotes are
    read by shell.words alone; a quote left open leaves the text as typed, so the
    caller still refuses it as unreadable."""
    if tokens(text, _OPERATORS) is None:
        return text
    quoted = dict(quote_spans(text))
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if i in quoted:
            out.append(text[i:quoted[i]])
            i = quoted[i]
            continue
        if c == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if c == "(":
            end = _group_end(text, i, quoted)
            before = text[i - 1] if i else " "
            after = text[end + 1] if end is not None and end + 1 < len(text) else " "
            if end is not None and end > i + 1 and (before not in _PAREN_SYNTAX_BEFORE
                                                    or after not in _PAREN_SYNTAX_AFTER):
                if out and out[-1] in _EXTGLOB_PREFIX:   # ksh's @( ), !( ), +( ): one glob
                    out.pop()
                out.append(PAREN_GLOB)
                i = end + 1
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _simple_commands(text: str) -> list[tuple[list[str], list[str]]]:
    """(words, redirect targets) of each simple command, paren globs spelled as globs first;
    None-safe."""
    words = tokens(_paren_globs(text), _OPERATORS)
    if words is None:
        raise Unreadable("a quote left open", "close it")
    found, current, targets, feed, i = [], [], [], [], 0
    while i < len(words):
        word = words[i]
        if word in _SEPARATORS:
            found.append((current, targets, feed))
            # a pipe feeds the next command every word of the commands before it
            feed = feed + current if word in ("|", "|&") else []
            current, targets = [], []
        elif word in _REDIRECTS or (word.endswith(">") and word.strip("0123456789&>|") == ""):
            if i + 1 < len(words) and words[i + 1] not in _SEPARATORS:
                targets.append(words[i + 1])
                i += 1
        elif word in ("<", "<<", "<<<", "<&"):
            if word in ("<", "<<<") and i + 1 < len(words) and words[i + 1] not in _SEPARATORS:
                feed = feed + [words[i + 1]]
            i += 1
        else:
            current.append(word)
        i += 1
    found.append((current, targets, feed))
    return found


def _scan(text: str, cwd: str, places: dict, environ, depth: int = 0) -> str | None:
    if depth > MAX_DEPTH:
        return "a command nested too deep to read"
    flat, bodies = read_script(text)
    if "$" in flat and "=" in flat:
        flat = expand_literal_variables(flat)       # `f=PATH; cp x $f`
    spans = substitution_spans(flat)
    for _start, _end, inner in spans:
        what = _scan(inner, cwd, places, environ, depth + 1)
        if what:
            return what
    # the outer command sees each substitution as one word this reader cannot spell
    # (`s=$(cat f); rm $s/...`), so a `)` inside it cannot end a word early
    for start, end, _inner in reversed(spans):
        flat = flat[:start] + SUBSTITUTED + flat[end:]
    dirs = [cwd]
    runs_scripts = False
    for words, targets, feed in _simple_commands(flat):
        for target in targets:
            what = _candidates(target, dirs, places, environ)
            if what:
                return what
            places["unspelled"] |= _unspelled(target, places, dirs[0], environ)
        command = strip_prefix(words)
        program = command[0].rsplit("/", 1)[-1] if command else ""
        args = command[1:]
        if program in ("cd", "pushd") and args:
            # a move to a place this reader cannot spell leaves every later relative word unknown
            places["unspelled"] |= _unspelled(args[-1], places, dirs[0], environ)
            dirs = [os.path.normpath(os.path.join(dirs[0], _expand(args[-1], places, dirs[0],
                                                                    environ)))] + dirs
            places["dirs_seen"].append(dirs[0])
            continue
        # code computed at run time (`eval "$(...)"`, `bash -c "$CMD"`, `$RUN x`) can write
        # anywhere: it counts as an unspelled write when the command names the config dir
        if command:
            places["unspelled"] |= _unspelled(" ".join(args) if program == "eval" else command[0],
                                              places, dirs[0], environ)
        line = " ".join(args) if program == "eval" else command[0] if len(command) == 1 else ""
        if line and any(c in line for c in " \t\n;|&<>"):
            what = _scan(line, dirs[0], places, environ, depth + 1)   # bash -c 'LINE', eval
            if what:
                return what
            continue
        runs_scripts = runs_scripts or any(_SCRIPT_RUNNERS.fullmatch(w.rsplit("/", 1)[-1])
                                           for w in words[:len(words) - len(command) + 1])
        if claude_cli_writes(program, args):
            return CLAUDE_CLI
        if not _writes(program, args):
            continue
        named = _named(program, args)
        own = plugin_tool(command, dirs[0], places, environ)
        outputs = {args[i] for i in _output_values(args)} if own else set()
        if program == "find":
            what = _find_writes(args, dirs, places, environ)
            if what:
                return what
        if command and _runs_under_xargs(words):
            what = _feeds_state(feed, dirs, places, environ)
            if what:
                return what
        if program in COPIERS:
            destinations, sources = _copy_words(args)
            what = _into_directory(destinations, sources, dirs, places, environ)
            if what:
                return what
            # mv removes its sources; ln links them, and a hard link to a protected file
            # is a second name for it that realpath cannot see.
            named = destinations + (sources if program in ("mv", "ln") else [])
        for word in named:
            what = _candidates(word, dirs, places, environ, holds=program in REMOVERS)
            if what and not (own and what in TASK_STATE and word not in outputs):
                return what
            if not own or word in outputs:
                places["unspelled"] |= _unspelled(word, places, dirs[0], environ)
        if program in REMOVERS and any(_holds_place(_expand(a, places, dirs[0], environ),
                                                    dirs[0], places)
                                       for a in args if not a.startswith("-")):
            return "a directory that holds settings or the running plugin"
    if runs_scripts:
        for _quoted, body in bodies:
            what = _candidates(body, [cwd], places, environ)
            if what:
                return what
    return None


def written_place(command: str, cwd: str, environ=None, shim: str | None = None) -> str | None:
    """What protected place this Bash command would write, else None. `shim` is the launcher
    shim's exact text (shared.repact_shim.shim_text), which marks it as a plugin program.
    Raises Unreadable when the command cannot be read the way the shell reads it."""
    environ = os.environ if environ is None else environ
    places = _places(environ)
    places["shim"] = shim
    what = _scan(command, cwd, places, environ)
    if what is None and places["unspelled"] and _names_state(command, cwd, places, environ):
        return UNSPELLED
    return what
