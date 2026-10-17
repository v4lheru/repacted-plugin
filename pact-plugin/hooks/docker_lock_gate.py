#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/docker_lock_gate.py
Summary: PreToolUse(Bash) hook. Refuses a command that starts Docker containers for tests
         unless the caller holds the lock named 'docker' in bin/repact-lock (records read through
         hooks/shared/lock_records.py, the module the command uses), so two agents
         on one machine never run container suites at once. The refusal names the exact
         acquire command, and a hold that ran past its ttl is refused as "your hold expired".
Used by: hooks/dispatch_registry.py (PreToolUse, matcher "Bash", next to git_commit_check;
         the dispatcher calls dispatch_check()). Tested by tests/test_docker_lock_gate.py.

What needs the lock (detect()):
  - docker [global options] run|create|start|restart, docker container run|create|start|
    restart, and docker compose / docker-compose [options] up|run|start|create|restart.
    Every other docker command (ps, images, logs, inspect, exec, build, pull, stop, down,
    ...) is never refused, nor is one that asks for --help.
  - a test runner (dotnet test, pytest, python -m pytest, go test, gotestsum, cargo test,
    npm|pnpm|yarn|bun test, pnpm|yarn|bun jest|vitest|..., node --test, playwright test,
    hatch test, deno test, tox, mvn and gradle test goals, vitest, jest, mocha) when the
    command line
    says "testcontainers" anywhere (a filter, a TESTCONTAINERS_* variable) OR the project
    it runs in references Testcontainers (project_marker(): build and dependency manifests
    from the start directory up to the git root and a few levels down, size-capped).
  The command is read with shell_words: comments and quoted text are data, runner words
  (sudo, env, nice, timeout, gtimeout, arch, flock FILE, tcount, rtk, env-get .. --, VAR=x,
  a full path) and unquoted shell keywords (if, then, do, while, !, coproc, ...) are
  unwrapped, and the text of sh|bash|zsh -c [--], flock -c, eval, a $(...), and a heredoc,
  a here-string or echo|printf text fed to a shell is read as commands too.

The caller is "<agent_type>@<team>": agent_type is an in-process teammate's member name,
and the team is the lead session's (both the way hooks/amendment_relay.py measured them).
A lead frame is the team config's lead name. No hook reads agent_id to decide anything
(tests/test_lead_discriminator_invariant.py), so this cannot tell apart two Agent-tool
subagents of one type in one session, and the owner a hold names is whatever the acquirer
typed: a coordination brake, not a security boundary.

Fail direction: allow. A command it cannot read, a caller it cannot name, a project scan
that fails, and any internal error allow the call and write one `gate_doubt` journal row.
PACT_DOCKER_LOCK=advise turns every refusal into advice; PACT_DOCKER_LOCK=off turns the
check off. Each refusal (or advice) writes one `gate_refusal` row: hook, event, tool_name,
verdict, rule (the lock state) and code (what needed the lock); never the command line.
This hook never runs or probes docker.

Honest limits: a command assembled in a variable, $'..' escapes that spell the program,
a script file that runs docker, and `make test` are not seen.

Input: JSON on stdin; reads tool_name, tool_input.command, cwd, session_id, agent_type.
Output: deny = the PreToolUse deny envelope on stdout, the reason on stderr, exit 2;
        advice = an additionalContext envelope, exit 0; allow = {"suppressOutput": true}.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import NamedTuple, Optional

_hooks_dir = Path(__file__).resolve().parent
if str(_hooks_dir) not in sys.path:
    sys.path.insert(0, str(_hooks_dir))

from shell_words import (  # noqa: E402
    SHELL_KEYWORDS, Unreadable, quoted_words, read_script, substitution_spans,
)

GATE_NAME = "docker_lock_gate"
LOCK_NAME = "docker"
MODE_ENV = "PACT_DOCKER_LOCK"
MODE_ENFORCE, MODE_ADVISE, MODE_OFF = "enforce", "advise", "off"
MARKER = "testcontainers"
MAX_COMMAND_CHARS = 200_000
MAX_DEPTH = 4
ACQUIRE_WAIT_S = 600
_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})
_LOCK_CLI = _hooks_dir.parent / "bin" / "repact-lock"
_SPLICE = " __repact_sub__ "
# Words that must appear (quotes and backslashes removed, case folded) for any rule to apply.
_PREFILTER = ("docker", "test", "mvn", "gradle", "jest", "mocha", "tox")

# --- reading the command ---------------------------------------------------------------------

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_SHELL_VALUE_OPTIONS = frozenset({"-o", "+o", "-O", "+O"})
# Words that run the next word as the command, with their options that take a value.
WRAPPERS = {
    "sudo": {"-u", "-g", "-C", "-h", "-p", "-r", "-t", "-U", "-D"},
    "env": {"-u", "-C", "-S", "-P"},
    "nice": {"-n"}, "ionice": {"-c", "-n", "-p"}, "renice": {"-n"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "gtimeout": {"-s", "-k", "--signal", "--kill-after"}, "arch": {"-arch"},
    "nohup": set(), "time": set(), "exec": {"-a"}, "builtin": set(), "command": set(),
    "xargs": {"-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a"}, "watch": {"-n", "-d"},
    "caffeinate": {"-t", "-w"}, "chronic": set(), "unbuffer": set(), "stdbuf": set(),
    "tcount": set(), "rtk": set(), "npx": {"-p", "--package"}, "bunx": set(),
}
# `<tool> run|exec|dlx <program>`: the program after the verb is the command.
RUN_VERBS = {"uv": {"run"}, "poetry": {"run"}, "pipenv": {"run"}, "hatch": {"run"},
             "pdm": {"run"}, "rye": {"run"}, "pnpm": {"exec", "dlx"}, "yarn": {"exec", "dlx"}}
_COUNT = re.compile(r"^\d+(\.\d+)?[smhd]?$")
# Reserved words that put the next word in command position: `then docker run x`.
KEYWORDS = SHELL_KEYWORDS | {"coproc"}
# `flock [options] FILE COMMAND` or `flock [options] FILE -c 'script'`.
FLOCK_VALUE_OPTIONS = frozenset({"-w", "--timeout", "-E", "--conflict-exit-code"})
# Programs whose arguments become a shell script when piped into `sh`/`bash`.
ECHOES = frozenset({"echo", "printf"})

DOCKER_VALUE_OPTIONS = frozenset({"-H", "--host", "-c", "--context", "--config", "-l",
                                  "--log-level", "--tlscacert", "--tlscert", "--tlskey"})
COMPOSE_VALUE_OPTIONS = frozenset({"-f", "--file", "-p", "--project-name", "--profile",
                                   "--env-file", "--project-directory", "--ansi", "--parallel",
                                   "--progress"})
DOCKER_STARTS = frozenset({"run", "create", "start", "restart"})
COMPOSE_STARTS = frozenset({"up", "run", "start", "create", "restart"})
MAVEN_GOALS = frozenset({"test", "verify", "integration-test", "install", "package", "deploy"})
JS_PACKAGE_MANAGERS = frozenset({"npm", "pnpm", "yarn", "bun"})
JS_VALUE_OPTIONS = frozenset({"--prefix", "-C", "--dir", "--cwd", "--filter", "-F", "--workspace",
                              "-w"})
BARE_RUNNERS = {"pytest": "pytest", "py.test": "pytest", "vitest": "vitest", "jest": "jest",
                "mocha": "mocha", "tox": "tox", "gotestsum": "gotestsum"}


class Finding(NamedTuple):
    starts: Optional[str]       # a container-starting docker command: its code
    runner: Optional[str]       # a test runner: its code
    marker: bool                # "testcontainers" on the command line
    cd_target: Optional[str]    # the literal target of a leading cd
    doubt: Optional[str]        # why the command could not be read; it is then allowed


class _Walk:
    def __init__(self):
        self.starts: Optional[str] = None
        self.runner: Optional[str] = None
        self.cd_target: Optional[str] = None


def _is_assignment(word: str) -> bool:
    return "=" in word and word.split("=", 1)[0].isidentifier()


def _base(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _positional(args: list[str], value_options: frozenset) -> list[str]:
    """`args` without options: --x=y alone, a value option with its value, any other -x alone."""
    out, i = [], 0
    while i < len(args):
        word = args[i]
        if word.startswith("-") and len(word) > 1:
            i += 2 if word in value_options else 1
        else:
            out.append(word)
            i += 1
    return out


def _docker_code(base: str, args: list[str]) -> Optional[str]:
    if "--help" in args or (base == "docker-compose" and "-h" in args):
        return None
    if base == "docker":
        words = _positional(args, DOCKER_VALUE_OPTIONS)
        if not words:
            return None
        sub = words[0]
        if sub in DOCKER_STARTS:
            return f"docker_{sub}"
        if sub == "container" and len(words) > 1 and words[1] in DOCKER_STARTS:
            return f"docker_{words[1]}"
        if sub != "compose":
            return None
        args = args[args.index("compose") + 1:]
    words = _positional(args, COMPOSE_VALUE_OPTIONS)
    return f"compose_{words[0]}" if words and words[0] in COMPOSE_STARTS else None


def _runner_code(base: str, args: list[str]) -> Optional[str]:
    words = _positional(args, JS_VALUE_OPTIONS if base in JS_PACKAGE_MANAGERS else frozenset())
    first = words[0] if words else ""
    if base in BARE_RUNNERS:
        return BARE_RUNNERS[base]
    if base == "dotnet" and first in ("test", "vstest"):
        return "dotnet_test"
    if re.fullmatch(r"python(\d(\.\d+)?)?", base) and "-m" in args:
        module = args[args.index("-m") + 1:args.index("-m") + 2]
        return "pytest" if module and module[0] in ("pytest", "unittest") else None
    if base == "node" and "--test" in args or base == "playwright" and first == "test":
        return f"{base}_test"
    if base == "go" and first == "test":
        return "go_test"
    if base in ("hatch", "deno") and first == "test":
        return f"{base}_test"
    if base in JS_PACKAGE_MANAGERS and base != "npm" and first in BARE_RUNNERS:
        return BARE_RUNNERS[first]              # yarn jest, pnpm vitest run: a package's bin
    if base == "cargo" and first in ("test", "nextest"):
        return "cargo_test"
    if base in JS_PACKAGE_MANAGERS:
        script = words[1] if first in ("run", "run-script") and len(words) > 1 else first
        # npm runs a script only through `run` or its own `test`/`t`; the others run any.
        bare = first in ("test", "t") if base == "npm" else first.startswith("test")
        return f"{base}_test" if bare or script != first and script.startswith("test") else None
    if base in ("mvn", "mvnw"):
        return "maven" if any(w in MAVEN_GOALS or w.endswith(":test") for w in words) else None
    if base in ("gradle", "gradlew"):
        return "gradle" if any("test" in w.lower() or w in ("check", "build") for w in words) \
            else None
    return None


def _command_words(words: list[str], walk: _Walk, depth: int) -> bool:
    """Classify one simple command into `walk`. True when it is a shell reading stdin."""
    i = 0
    while i < len(words):
        word = words[i]
        base = _base(word)
        if _is_assignment(word):
            i += 1
            continue
        if base in SHELLS:
            j = i + 1
            while j < len(words) and words[j][:1] in "-+" and len(words[j]) > 1:
                flag = words[j]
                if flag in _SHELL_VALUE_OPTIONS:
                    j += 2
                    continue
                if not flag.startswith("--") and "c" in flag[1:]:
                    script = j + 2 if words[j + 1:j + 2] == ["--"] else j + 1
                    if script < len(words):
                        _walk_script(words[script], walk, depth + 1)
                    return False
                j += 1
            return j >= len(words)              # no script file: the shell reads stdin
        if base == "eval":
            _walk_script(" ".join(words[i + 1:]), walk, depth + 1)
            return False
        if base == "command" and any(w in ("-v", "-V") for w in words[i + 1:i + 3]):
            return False                        # a lookup: nothing runs
        if base in WRAPPERS:
            value_options = WRAPPERS[base]
            i += 1
            if base == "rtk" and i < len(words) and words[i] == "proxy":
                i += 1
            while i < len(words) and (words[i].startswith("-") and len(words[i]) > 1
                                      or _COUNT.match(words[i])):
                i += 2 if words[i] in value_options else 1
            continue
        if base == "flock":
            i += 1
            while i < len(words) and words[i].startswith("-") and len(words[i]) > 1:
                i += 2 if words[i] in FLOCK_VALUE_OPTIONS else 1
            i += 1                              # the lock file
            if words[i:i + 1] in (["-c"], ["--command"]):
                if i + 1 < len(words):
                    _walk_script(words[i + 1], walk, depth + 1)
                return False
            continue
        if base == "env-get" and "--" in words[i + 1:]:
            i = words.index("--", i + 1) + 1    # env-get FILE KEY -- <command>
            continue
        if base in RUN_VERBS and i + 1 < len(words) and words[i + 1] in RUN_VERBS[base]:
            i += 2
            while i < len(words) and words[i].startswith("-") and len(words[i]) > 1:
                i += 1
            continue
        args = words[i + 1:]
        if base in ("docker", "docker-compose"):
            walk.starts = walk.starts or _docker_code(base, args)
        else:
            walk.runner = walk.runner or _runner_code(base, args)
        return False
    return False


def _walk_fed_text(pieces: list, index: int, walk: _Walk, depth: int) -> None:
    """A shell at `pieces[index]` reads stdin: read as commands the echo or printf text just
    before it (`echo 'docker run x' | bash`) and the word just after it (`bash <<< '..'`).
    The operator between pieces is not kept, so `echo ..; bash` is read the same way. Text
    that does not read as a script is data here: it adds nothing and doubts nothing."""
    texts = []
    if index > 0:
        before = [word for word, _ in pieces[index - 1]]
        if before and _base(before[0]) in ECHOES:
            text = " ".join(word for word in before[1:] if not word.startswith("-"))
            texts.append(text.replace("\\n", "\n"))     # printf and echo -e end lines with \n
    if index + 1 < len(pieces):
        texts.append(pieces[index + 1][0][0])
    for text in texts:
        try:
            _walk_script(text, walk, depth + 1)
        except Unreadable:
            pass


def _walk_script(command: str, walk: _Walk, depth: int, top: bool = False) -> None:
    """Read `command` as the shell would and classify each simple command in it."""
    if depth > MAX_DEPTH:
        raise Unreadable("commands nested too deep", "run them one at a time")
    text, heredocs = read_script(command)
    spans = substitution_spans(text)
    flat, cursor = [], 0
    for start, end, _ in spans:
        flat.append(text[cursor:start])
        flat.append(_SPLICE)
        cursor = end
    flat.append(text[cursor:])
    pieces = quoted_words("".join(flat))
    if pieces is None:
        raise Unreadable("a quoted string the shell may run", "split the command")
    reads_stdin = False
    for index, piece in enumerate(pieces):
        start = 0                               # `then docker run x`; a quoted 'then' is a word
        while start < len(piece) and not piece[start][1] and piece[start][0] in KEYWORDS:
            start += 1
        words = [word for word, _ in piece[start:]]
        if top and index == 0 and len(words) >= 2 and words[0] in ("cd", "pushd"):
            target = words[2] if words[1] == "--" and len(words) > 2 else words[1]
            if "__repact_" not in target and "$" not in target:
                walk.cd_target = target
        if _command_words(words, walk, depth):
            reads_stdin = True
            _walk_fed_text(pieces, index, walk, depth)
    for _, _, inner in spans:
        _walk_script(inner, walk, depth + 1)
    if reads_stdin:
        for _, body in heredocs:
            _walk_script(body, walk, depth + 1)


def detect(command: object) -> Finding:
    """What in `command` needs the docker lock. Pure: reads nothing but its argument."""
    if not isinstance(command, str) or not command.strip():
        return Finding(None, None, False, None, None if command is None else "not_a_string")
    plain = command.translate({ord("\\"): None, ord('"'): None, ord("'"): None}).casefold()
    if not any(word in plain for word in _PREFILTER):
        return Finding(None, None, False, None, None)
    if len(command) > MAX_COMMAND_CHARS:
        return Finding(None, None, False, None, "too_long")
    walk = _Walk()
    try:
        _walk_script(command, walk, 0, top=True)
    except Unreadable:
        return Finding(walk.starts, walk.runner, MARKER in plain, walk.cd_target, "unreadable")
    except RecursionError:
        return Finding(None, None, False, None, "unreadable")
    return Finding(walk.starts, walk.runner, MARKER in plain, walk.cd_target, None)


# --- the project scan ------------------------------------------------------------------------

MANIFEST_NAMES = frozenset({"Directory.Packages.props", "Directory.Build.props", "package.json",
                            "pyproject.toml", "pom.xml", "go.mod"})
MANIFEST_SUFFIXES = (".csproj", ".fsproj", ".vbproj")
MANIFEST_PATTERNS = (re.compile(r"^requirements.*\.txt$"), re.compile(r"^build\.gradle.*$"))
SKIP_DIRS = frozenset({"node_modules", "bin", "obj", "target", "build", "dist", "out", "vendor",
                       "venv", "__pycache__", "coverage"})
MAX_READ_BYTES = 512 * 1024
MAX_UP = 12
MAX_DOWN_DEPTH = 3
MAX_DIRS = 400
MAX_MANIFESTS = 300
_scan_cache: dict = {}


def is_manifest(name: str) -> bool:
    return name in MANIFEST_NAMES or name.endswith(MANIFEST_SUFFIXES) \
        or any(pattern.match(name) for pattern in MANIFEST_PATTERNS)


def _manifests_in(directory: Path, errors: list) -> list[Path]:
    try:
        return [entry for entry in directory.iterdir() if is_manifest(entry.name) and entry.is_file()]
    except OSError as error:
        errors.append(type(error).__name__)
        return []


def _candidate_manifests(start: Path, errors: list) -> list[Path]:
    """Manifests in `start` and each parent up to the git root, then a few levels below."""
    found, directory = [], start
    for _ in range(MAX_UP):
        found += _manifests_in(directory, errors)
        if (directory / ".git").exists() or directory.parent == directory:
            break
        directory = directory.parent
    level, seen = [start], 0
    for _ in range(MAX_DOWN_DEPTH):
        below = []
        for directory in level:
            try:
                children = [entry for entry in directory.iterdir()
                            if entry.is_dir() and not entry.is_symlink()
                            and not entry.name.startswith(".") and entry.name not in SKIP_DIRS]
            except OSError as error:
                errors.append(type(error).__name__)
                continue
            for child in sorted(children):
                seen += 1
                if seen > MAX_DIRS:
                    return found
                found += _manifests_in(child, errors)
                below.append(child)
        level = below
    return found


def project_marker(start: str) -> tuple[bool, Optional[str]]:
    """(the project at `start` references Testcontainers, the scan error if any). Cached."""
    if start in _scan_cache:
        return _scan_cache[start]
    errors: list = []
    hit = False
    try:
        for path in _candidate_manifests(Path(start), errors)[:MAX_MANIFESTS]:
            try:
                with open(path, "rb") as handle:
                    head = handle.read(MAX_READ_BYTES)
            except OSError as error:
                errors.append(type(error).__name__)
                continue
            if MARKER.encode() in head.lower():
                hit = True
                break
    except Exception as error:  # noqa: BLE001 - a scan that fails found no marker
        errors.append(type(error).__name__)
    result = (hit, None if hit or not errors else errors[0])
    _scan_cache[start] = result
    return result


def scan_start(finding: Finding, cwd: object) -> Optional[str]:
    """The directory the command runs its tests in: a leading cd's target, else cwd."""
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        return None
    if finding.cd_target:
        target = os.path.join(cwd, os.path.expanduser(finding.cd_target))
        if os.path.isdir(target):
            return os.path.normpath(target)
    return cwd


# --- the decision ----------------------------------------------------------------------------

HELD, EXPIRED, OTHER, FREE = "held", "hold_expired", "held_by_other", "lock_free"


class Hold(NamedTuple):
    state: str                  # HELD, EXPIRED, OTHER or FREE
    owner: Optional[str] = None
    age_s: float = 0.0
    left_s: float = 0.0


class Result(NamedTuple):
    verdict: str                # "deny", "advise" or "allow"
    code: str                   # what needed the lock, or the doubt
    rule: str                   # the lock state, or "doubt"
    text: str


def hold_state(records: list, caller: str, now: float, same_owner) -> Hold:
    """The caller's standing from the lock records read (each a dict or None). A live hold of
    the caller wins over every other record; an expired record of another owner is free."""
    mine_expired, other = None, None
    for record in records:
        if not isinstance(record, dict):
            continue
        age = now - record["acquired"]
        left = record["ttl_s"] - age
        if same_owner(record["owner"], caller):
            if left > 0:
                return Hold(HELD, record["owner"], age, left)
            mine_expired = Hold(EXPIRED, record["owner"], age, left)
        elif left > 0:
            other = other or Hold(OTHER, record["owner"], age, left)
    return mine_expired or other or Hold(FREE)


def _duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    if whole < 3600:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole // 3600}h{whole % 3600 // 60:02d}m"


def acquire_command(caller: str) -> str:
    cli = f"python3 {shlex.quote(str(_LOCK_CLI))}"
    owner = shlex.quote(caller)
    return f"{cli} acquire {LOCK_NAME} --owner {owner} --wait {ACQUIRE_WAIT_S}"


def decide(need: Optional[str], caller: Optional[str], hold: Optional[Hold], mode: str) -> Optional[Result]:
    """The verdict on one command. Pure. None allows without a journal row."""
    if need is None or mode == MODE_OFF:
        return None
    if not caller:
        return Result("allow", need, "caller_unknown",
                      "docker_lock_gate: allowed; the caller could not be named")
    if hold is None or hold.state == HELD:
        return None
    take = acquire_command(caller)
    release = f"python3 {shlex.quote(str(_LOCK_CLI))} release {LOCK_NAME} --owner {shlex.quote(caller)}"
    if hold.state == EXPIRED:
        why = (f"your hold expired: your lock on {LOCK_NAME} ran out {_duration(-hold.left_s)} ago "
               "and another agent may take it now.")
    elif hold.state == OTHER:
        why = (f"{hold.owner} holds the {LOCK_NAME} lock (for {_duration(hold.age_s)}, expires in "
               f"{_duration(hold.left_s)}); two container test runs on one machine fail each other.")
    else:
        why = (f"this command starts containers ({need}) and you do not hold the {LOCK_NAME} lock; "
               "two container test runs on one machine fail each other.")
    text = (f"Docker lock: {why} Run `{take}` first (it waits up to {ACQUIRE_WAIT_S // 60} minutes "
            f"for the holder; add --ttl MINUTES for a run longer than 30), then this command again. "
            f"Release with `{release}` when the run ends.")
    return Result("advise" if mode == MODE_ADVISE else "deny", need, hold.state, text)


def read_mode(environ=os.environ) -> str:
    value = str(environ.get(MODE_ENV, "")).strip().lower()
    return value if value in (MODE_ADVISE, MODE_OFF) else MODE_ENFORCE


# --- reading the world (everything below does I/O) -------------------------------------------

def lock_dirs() -> list[Path]:
    """Where a lock may live: the hook's $CLAUDE_PLUGIN_DATA and the default the command uses
    when a Bash call has no such variable. Both are read so the two cannot disagree."""
    from shared import plugin_paths as common
    from shared.lock_records import LOCKS_DIRNAME

    dirs = [common.plugin_data_dir(create=False) / LOCKS_DIRNAME,
            common.default_data_dir() / LOCKS_DIRNAME]
    return list(dict.fromkeys(dirs))


def read_hold(caller: str) -> Hold:
    from shared.lock_records import lock_path, read_lock, same_owner

    records = [read_lock(lock_path(directory, LOCK_NAME))[0]
               for directory in lock_dirs() if directory.is_dir()]
    return hold_state(records, caller, time.time(), same_owner)


def resolve_caller(payload: dict) -> Optional[str]:
    """'<name>@<team>' of this frame, or None when it cannot be named."""
    from shared import pact_context
    from shared.frame import is_lead_frame

    pact_context.init(payload)
    team = pact_context.get_team_name()
    agent_type = payload.get("agent_type")
    untyped = agent_type is None or agent_type == ""
    if team and is_lead_frame(payload):
        lead = _lead_name(team, payload.get("session_id"))
        if lead:
            return f"{lead}@{team}"
    elif team and not untyped:
        if not isinstance(agent_type, str) or "@" in agent_type or not agent_type.strip():
            return None
        return f"{pact_context.strip_pact_namespace(agent_type.strip())}@{team}"
    # No team context, or no agent_type outside the lead's session: a separate-process
    # teammate. Its own session id names it in the registry.
    if untyped or not team:
        return _registered_name(payload)
    return None


def _registered_name(payload: dict) -> Optional[str]:
    """'<name>@<team>' the session registry holds for a frame with no agent_type outside
    the lead's session (a separate-process teammate, whose own process has no team
    context), or None. `session_registry.resolve` has checked the name against the team."""
    from shared.frame import resolve as resolve_frame

    name, _, team = resolve_frame(payload).registry_name.partition("@")
    return f"{name}@{team}" if name and team else None


def _lead_name(team: str, session_id: object) -> Optional[str]:
    """The team's lead name for a frame of the lead's own session, else None: a frame with no
    agent_type in ANOTHER session (a tmux teammate) is not the lead and cannot be named."""
    from shared.paths import get_claude_config_dir

    config_root = get_claude_config_dir()
    try:
        config = json.loads((config_root / "teams" / team / "config.json").read_text("utf-8"))
    except (OSError, ValueError):
        return "team-lead"
    if not isinstance(config, dict):
        return "team-lead"
    lead_session = config.get("leadSessionId")
    if isinstance(lead_session, str) and lead_session and lead_session != session_id:
        return None
    name = str(config.get("leadAgentId", "")).split("@")[0]
    return name or "team-lead"


def _session_dir(payload: dict) -> str:
    session_id = payload.get("session_id")
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not isinstance(session_id, str) or not session_id or not project_dir:
        return ""
    from shared.pact_context import reconstruct_session_dir

    return reconstruct_session_dir(project_dir, session_id)


def _journal(payload: dict, event_type: str, **fields) -> None:
    try:
        session_dir = _session_dir(payload)
        if not session_dir:
            return
        from shared.session_journal import append_event, make_event

        append_event(make_event(event_type, hook=GATE_NAME, event="PreToolUse",
                                tool_name="Bash", **fields), session_dir=session_dir)
    except Exception:  # noqa: BLE001 - a row that cannot be written changes no verdict
        pass


def check(payload: dict, ctx=None) -> Optional[Result]:
    """The verdict on one PreToolUse payload, or None to allow silently. `ctx` is unused; it
    keeps the signature the one-process hook runner calls."""
    if payload.get("tool_name") != "Bash":
        return None
    mode = read_mode()
    if mode == MODE_OFF:
        return None
    tool_input = payload.get("tool_input")
    finding = detect(tool_input.get("command") if isinstance(tool_input, dict) else None)
    need, doubt = finding.starts, finding.doubt
    if need is None and finding.runner:
        if finding.marker:
            need = finding.runner
        else:
            start = scan_start(finding, payload.get("cwd"))
            hit, error = project_marker(start) if start else (False, "no_cwd")
            need = finding.runner if hit else None
            doubt = doubt or (None if hit else error)
    if need is None:
        if doubt:
            _journal(payload, "gate_doubt", verdict="allow", rule=doubt, code=finding.runner or "command")
        return None
    caller = resolve_caller(payload)
    result = decide(need, caller, read_hold(caller) if caller else None, mode)
    if result is not None:
        event_type = "gate_doubt" if result.verdict == "allow" else "gate_refusal"
        _journal(payload, event_type, verdict=result.verdict, rule=result.rule, code=result.code)
    return result


def dispatch_check(payload: dict, ctx=None):
    """check() in the dispatcher's Result shape (hooks/dispatch_registry.py), with main()'s
    outputs: deny is a block (the deny envelope, the reason on stderr, exit 2), advice is
    additionalContext, and an internal error allows. The registry marks this check
    journals_refusals, so the dispatcher writes no second gate_refusal row."""
    import dispatch_registry as registry

    try:
        result = check(payload, ctx)
    except Exception as error:  # noqa: BLE001 - a check that cannot run decided nothing
        print(f"docker_lock_gate: internal error, allowing: {type(error).__name__}", file=sys.stderr)
        return None
    if result is None or result.verdict == "allow":
        return None
    if result.verdict == "advise":
        return registry.Result(context=result.text, rule=result.rule)
    return registry.Result(decision="block", reason=result.text, rule=result.rule)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        result = check(payload) if isinstance(payload, dict) else None
    except Exception as error:  # noqa: BLE001 - a check that cannot run decided nothing
        print(f"docker_lock_gate: internal error, allowing: {type(error).__name__}", file=sys.stderr)
        return 0
    if result is None or result.verdict == "allow":
        print(_ALLOW_OUTPUT)
        return 0
    if result.verdict == "advise":
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "additionalContext": result.text}}))
        return 0
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": result.text,
    }}))
    print(result.text, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
