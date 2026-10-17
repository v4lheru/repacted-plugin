"""
Location: pact-plugin/hooks/shell/merge/checkout.py
Summary: git checkout state and binding: push argument parsing, implicit push to main, push
         options that merge, git config reads, the git binding of a leg, command_repo_binding.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: command_repo_binding, _MAIN_BRANCHES, _PUSH_IMPLICIT_SENTINEL, _PUSH_OPTION_MR_SENTINEL, _UNREADABLE, _GIT_VALUE_GLOBALS, _GIT_OPAQUE_GLOBALS, _GIT_ENV_REDIRECT_RE, _PUSH_LONG_VALUE_FLAGS, _MERGE_PUSH_OPTION_RE, _PUSH_PUSHOPTION_CONFIG_RE, _ungroup, _git_call, _parse_push_args, _push_is_dry_run, _implicit_push_shape, _merging_push_options, _paired_legs, _switched_branch, _git_leg_states, _branch_of, implicit_push_to_main, push_option_merge, _first_push_args, _implicit_push_identity, _push_option_mr_identity, _git_config, _push_remote_identity, _GIT_BOUND_OPS, _git_binding.
"""

from __future__ import annotations

import os
import re

from shell.merge import remotes as _remotes  # REBOUND name, read live
from shell.merge.arms import (_MR_IMPLICIT_SENTINEL)
from shell.merge.flags import (_normalized_flags)
from shell.merge.remotes import (_cli_repo_scope, _current_branch, _git_dir_of, _is_cli_mr_leg, _rest_host)
from shell.merge.strip import (_strip_non_executable_content)
from shell.merge.targets_git import (_canonical_join)
from shell.merge.views import (_executable_prefix, _leg_program_tokens, _shell_tokenize, _slice_stripped_legs)
from shell.words import normalize_line_continuations as _normalize_line_continuations

def command_repo_binding(context: dict, command: str) -> "dict | None":
    """Bind `context` to the repo and host `command` acts on, or None when they cannot be
    resolved (the mint then refuses, and no token authorizes the command).
    """
    start = _remotes._INVOCATION_CWD or os.getcwd()
    if any(isinstance(v, str) and v.startswith("api:") for v in context.values()):
        host = _rest_host(command, start)
        return None if host is None else {**context, "host": host}
    op = context.get("operation_type")
    if op in _GIT_BOUND_OPS or str(context.get("mr_target", "")).startswith(_PUSH_OPTION_MR_SENTINEL):
        return _git_binding(context, command, start)
    if op not in ("merge", "close", "mr-delete"):
        return context
    target = context.get("pr_number") or context.get("mr_target") or context.get("merge_implicit")
    if target is None or str(target).startswith(("api:", "url:")):
        return context
    target = str(target)
    if not _is_cli_mr_leg(command):
        return context              # not a CLI MR/PR command (a REST call binds its own repo)
    scope = _cli_repo_scope(command, start)
    if scope is None:
        return None
    repo, directory, source, host = scope
    bound = {**context, "host": host}
    implicit_target = context.get("merge_implicit") is not None or target == _MR_IMPLICIT_SENTINEL
    if source == "flag" and not implicit_target:
        # -R/--repo already binds the repo through the privileged-flag set (--repo=<value>,
        # set-equality on both sides), so an explicit-target command needs no second key.
        return bound
    if implicit_target:
        branch = _current_branch(directory)
        if branch:
            repo = repo + "@" + branch
    bound["repo"] = repo
    return bound


# --- Git checkout state: pushes to the checked-out branch, merging push options, and the
# checkout + remote binding of every git approval ---
_MAIN_BRANCHES = frozenset({"main", "master"})
_PUSH_IMPLICIT_SENTINEL = "\x00implicit-push"
_PUSH_OPTION_MR_SENTINEL = "\x00push-option-mr"
_UNREADABLE = "\x00unreadable"
_GIT_VALUE_GLOBALS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                                "--config-env", "--super-prefix"})
_GIT_OPAQUE_GLOBALS = frozenset({"--git-dir", "--work-tree", "--config-env"})
_GIT_ENV_REDIRECT_RE = re.compile(r"(?:^|\s)GIT_(?:DIR|WORK_TREE|COMMON_DIR|CONFIG\w*)\+?=")
_PUSH_LONG_VALUE_FLAGS = frozenset({"--push-option", "--receive-pack", "--exec", "--repo"})
_MERGE_PUSH_OPTION_RE = re.compile(
    r"\s*merge_request\.(?:merge_when_pipeline_succeeds|auto_merge)(?![\w.])", re.IGNORECASE)
_PUSH_PUSHOPTION_CONFIG_RE = re.compile(r"\s*push\.pushoption=(.*)$", re.IGNORECASE | re.DOTALL)
def _ungroup(leg: str) -> str:
    """`leg` without the subshell/group brackets around it (`(cd x && git push)`,
    `{ git push; }`): a leading `(`/`{` and a trailing `)`/`}` the leg does not open itself
    (a `$(...)` or `${...}` keeps its own)."""
    s = re.sub(r"^[\s({]+", "", leg).rstrip()
    while s[-1:] in (")", "}") and s.count(s[-1]) > s.count("(" if s[-1] == ")" else "{"):
        s = s[:-1].rstrip()
    return s


def _git_call(leg: str) -> "tuple | None":
    """(subcommand, args, -C values, -c values, opaque) of the git invocation `leg` runs
    (assignments, redirects and runner words skipped), or None when it does not run git.
    """
    leg = _ungroup(leg)
    prefix = _executable_prefix(leg)
    tokens = _leg_program_tokens(prefix) if prefix else None
    if not tokens:
        if prefix is None and re.search(r"\bgit\b.*\bpush\b", leg):
            return "push", [], [], [], True
        return None
    if os.path.basename(tokens[0]) != "git":
        return None
    chdirs, configs = [], []
    opaque = bool(_GIT_ENV_REDIRECT_RE.search(prefix))
    i = 1
    while i < len(tokens) and tokens[i].startswith("-"):
        name, eq, value = tokens[i].partition("=")
        if name in _GIT_VALUE_GLOBALS and not eq:
            value = tokens[i + 1] if i + 1 < len(tokens) else ""
            i += 1
        opaque = opaque or name in _GIT_OPAQUE_GLOBALS
        if name == "-C":
            chdirs.append(value)
        elif name == "-c":
            configs.append(value)
        i += 1
    if i >= len(tokens):
        return "", [], chdirs, configs, opaque
    return tokens[i], tokens[i + 1:], chdirs, configs, opaque


def _parse_push_args(args: "list[str]") -> "tuple":
    """(positionals, push-option values, long flag names, short flag letters, --repo value)
    of the arguments after `push`. A value-taking option's value is never a positional or a
    flag: `-o x`, `-ox`, `-vo x` (o ends a short cluster), `--push-option[=]x`, `--repo[=]x`,
    `--receive-pack`, `--exec`. Everything after `--` is positional."""
    positionals, options, longs, shorts, repo = [], [], set(), set(), None
    i, n = 0, len(args)
    while i < n:
        tok = args[i]
        if tok == "--":
            positionals.extend(args[i + 1:])
            break
        if tok.startswith("--"):
            name, eq, value = tok.partition("=")
            longs.add(name)
            if name in _PUSH_LONG_VALUE_FLAGS and not eq:
                value = args[i + 1] if i + 1 < n else ""
                i += 1
            if name == "--push-option":
                options.append(value)
            elif name == "--repo":
                repo = value
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            for j in range(1, len(tok)):
                if tok[j] == "o":
                    value = tok[j + 1:]
                    if not value:
                        value = args[i + 1] if i + 1 < n else ""
                        i += 1
                    options.append(value)
                    break
                shorts.add(tok[j])
            i += 1
            continue
        positionals.append(tok)
        i += 1
    return positionals, options, longs, shorts, repo


def _push_is_dry_run(longs: set, shorts: set) -> bool:
    return "--dry-run" in longs or "n" in shorts


def _implicit_push_shape(args: "list[str]") -> "str | None":
    """"scope" for a push of every branch (`--all`, `--branches`, the matching refspec `:`),
    "current" for a push of the checked-out branch (no refspec, or a HEAD/@ refspec), else
    None (explicit refspecs only, a dry run, a delete or mirror, tags only)."""
    positionals, _options, longs, shorts, _repo = _parse_push_args(args)
    if (_push_is_dry_run(longs, shorts) or longs & {"--delete", "--mirror", "--prune", "--help"}
            or shorts & {"d", "h"}):
        return None                     # a delete/mirror/prune is the mass/ref-delete arms' op
    refspecs = positionals[1:]
    if longs & {"--all", "--branches"} or ":" in refspecs:
        return "scope"
    if not refspecs:
        return None if "--tags" in longs else "current"
    return "current" if any(r in ("HEAD", "@") for r in refspecs) else None


def _merging_push_options(args: "list[str]", configs: "list[str]") -> bool:
    """True when a push sends a GitLab push option that merges its merge request
    (`merge_request.merge_when_pipeline_succeeds`, `merge_request.auto_merge`), on the
    command line or as `git -c push.pushOption=...`. A dry run sends nothing."""
    _positionals, options, longs, shorts, _repo = _parse_push_args(args)
    if _push_is_dry_run(longs, shorts) or "--help" in longs or "h" in shorts:
        return False
    for value in configs:
        m = _PUSH_PUSHOPTION_CONFIG_RE.match(value)
        if m:
            options.append(m.group(1))
    return any(_MERGE_PUSH_OPTION_RE.match(value) for value in options)


def _paired_legs(command: str) -> "list[tuple[str, str]]":
    """(stripped leg, leg to read) pairs. The stripped surface decides what runs (heredocs,
    comments and echo text removed); the raw leg keeps a quoted `cd "/a b"` path the strip
    blanks. When stripping changed the leg count, the raw legs cannot be lined up and the
    stripped legs are read (a quoted `cd` path then reads as unknowable)."""
    normalized = _normalize_line_continuations(command)
    stripped = _slice_stripped_legs(_strip_non_executable_content(normalized))
    raw = _slice_stripped_legs(normalized)
    if len(raw) != len(stripped):
        raw = stripped
    return list(zip(stripped, raw))


def _switched_branch(sub: str, args: "list[str]") -> "str | None":
    """The branch a `git checkout|switch` leaves checked out: the new branch of
    -b/-B/-c/-C/--orphan/--create/--force-create, else the one positional; None when the
    command switches nothing (`git checkout -- path`, no argument); _UNREADABLE when it cannot
    be told (`-`, --detach, two positionals)."""
    creating = {"-b", "-B", "--orphan"} if sub == "checkout" else {"-c", "-C", "--orphan", "--create",
                                                                   "--force-create"}
    positionals, i = [], 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            return None if sub == "checkout" else _UNREADABLE
        name, eq, value = tok.partition("=")
        if name in creating:
            return (value if eq else (args[i + 1] if i + 1 < len(args) else "")) or _UNREADABLE
        if tok == "--detach" or (sub == "switch" and tok == "-d"):
            return _UNREADABLE
        if not tok.startswith("-"):
            positionals.append(tok)
        i += 1
    if not positionals:
        return None
    if len(positionals) > 1 or positionals[0] == "-":
        return _UNREADABLE
    return positionals[0]


def _git_leg_states(command: str, start: str):
    """Yield (call, directory, branch) for each git leg of `command`, in order."""
    directory, switched, lost_switch = start, {}, False
    for stripped, leg in _paired_legs(command):
        closes_subshell = _ungroup(leg) != leg.strip() and leg.rstrip().endswith(")")
        tokens = _shell_tokenize(_ungroup(leg))
        call = _git_call(stripped)
        if tokens is None:
            if call is not None:
                yield call, None, _UNREADABLE
            directory = None
            continue
        head = tokens[0] if tokens else ""
        if call is None:
            if head in ("cd", "pushd"):
                arg = tokens[1] if len(tokens) > 1 else "~"
                if directory is None or arg.startswith("-") or "$" in arg or "`" in arg:
                    directory = None
                else:
                    directory = os.path.normpath(os.path.join(directory, os.path.expanduser(arg)))
                    if not os.path.isdir(directory):
                        directory = None
            elif head == "popd" or "cd" in tokens[1:] or "pushd" in tokens[1:]:
                directory = None
            if closes_subshell:
                directory = None
            continue
        sub, args, chdirs, _configs, opaque = call
        here = directory
        for step in chdirs:
            if here is None or not step or "$" in step or "`" in step:
                here = None
                break
            here = os.path.normpath(os.path.join(here, os.path.expanduser(step)))
        if opaque:
            here = None
        git_dir = _git_dir_of(here) if here is not None else None
        branch = _UNREADABLE if lost_switch else switched.get(str(git_dir) if git_dir else here)
        yield call, here, branch
        if sub in ("checkout", "switch"):
            new = _switched_branch(sub, args)
            if new is not None:
                if here is None:
                    lost_switch = True
                else:
                    switched[str(git_dir) if git_dir else here] = new
        if closes_subshell:
            directory = None


def _branch_of(directory: "str | None", switched: "str | None") -> str:
    """The branch a push from `directory` pushes: what an earlier leg switched to, else HEAD;
    _UNREADABLE when neither can be read (detached HEAD, no repo, unknowable directory)."""
    if switched is not None:
        return switched
    return (_current_branch(directory) if directory is not None else None) or _UNREADABLE


def implicit_push_to_main(command: str) -> bool:
    """True when a git push leg of `command` pushes main or master without naming it: a push
    of every branch, or a push of the checked-out branch while that is main/master or
    unreadable (see GIT CHECKOUT STATE)."""
    if "push" not in command:
        return False
    start = _remotes._INVOCATION_CWD or os.getcwd()
    for (sub, args, *_rest), directory, branch in _git_leg_states(command, start):
        if sub != "push":
            continue
        shape = _implicit_push_shape(args)
        if shape == "scope":
            return True
        if shape == "current":
            name = _branch_of(directory, branch)
            if name == _UNREADABLE or name in _MAIN_BRANCHES:
                return True
    return False


def push_option_merge(command: str) -> bool:
    """True when a git push leg of `command` asks GitLab to merge its merge request."""
    if "merge_request" not in command.lower():
        return False
    return any(sub == "push" and _merging_push_options(args, configs)
               for (sub, args, _chdirs, configs, _opaque), _dir, _branch
               in _git_leg_states(command, _remotes._INVOCATION_CWD or os.getcwd()))


def _first_push_args(command: str) -> "list[str] | None":
    for (sub, args, *_rest), _dir, _branch in _git_leg_states(command, _remotes._INVOCATION_CWD or os.getcwd()):
        if sub == "push":
            return args
    return None


def _implicit_push_identity(command: str) -> "str | None":
    """The `push_implicit` identity of a push-to-main that names no main ref: its repository
    positional (or none) and its scope flags. The branch and the remote it reaches are bound
    by _git_binding."""
    args = _first_push_args(command)
    if args is None or _implicit_push_shape(args) is None:
        return None
    positionals, _options, longs, _shorts, _repo = _parse_push_args(args)
    return _PUSH_IMPLICIT_SENTINEL + _canonical_join(positionals + sorted(longs & {"--all", "--branches"}))


def _push_option_mr_identity(command: str) -> "str | None":
    """The `mr_target` identity of a push that merges by push option: every positional and
    every danger flag the push carries (a force or a delete), so the approval of a plain push
    never covers a forced one."""
    args = _first_push_args(command)
    if args is None:
        return None
    positionals, _options, _longs, _shorts, _repo = _parse_push_args(args)
    flags = sorted(_normalized_flags(args, "git"))
    return _PUSH_OPTION_MR_SENTINEL + _canonical_join(positionals + flags)


def _git_config(directory: str) -> "dict[tuple[str, str, str], str]":
    """{(section, subsection, key): value} from the config file of the repo containing
    `directory` (sections and keys lowercased, subsections kept; the first value wins).
    Empty outside a repo or when unreadable."""
    git_dir = _git_dir_of(directory)
    if git_dir is None:
        return {}
    common = git_dir
    try:
        if (git_dir / "commondir").is_file():
            common = (git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()).resolve()
        text = (common / "config").read_text(encoding="utf-8")
    except OSError:
        return {}
    entries: dict = {}
    section, subsection = "", ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = re.match(r'^\[\s*([\w.-]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*\]', line)
        if m:
            section, subsection = m.group(1).lower(), (m.group(2) or "").replace('\\"', '"')
            continue
        key, eq, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        entries.setdefault((section, subsection, key.strip().lower()), value if eq else "true")
    return entries


def _push_remote_identity(args: "list[str]", directory: "str | None", branch: str) -> str:
    """Where a push goes: the configured push URL (else URL) of the remote it names, or the
    named repository itself (a URL or a path) when no remote has that name. With no named
    repository: branch.<b>.pushRemote, remote.pushDefault, branch.<b>.remote, then origin."""
    positionals, _options, _longs, _shorts, repo = _parse_push_args(args)
    name = positionals[0] if positionals else repo
    if directory is None:
        return _UNREADABLE + ":" + (name or "")
    config = _git_config(directory)
    if not name:
        name = (config.get(("branch", branch, "pushremote")) or config.get(("remote", "", "pushdefault"))
                or config.get(("branch", branch, "remote")) or "origin")
    return config.get(("remote", name, "pushurl")) or config.get(("remote", name, "url")) or name


_GIT_BOUND_OPS = frozenset({"push-to-main", "force-push", "branch-delete", "remote-ref-delete",
                            "remote-mass-delete"})


def _git_binding(context: dict, command: str, start: str) -> dict:
    """`context` bound to the checkout the git leg runs in (`repo`: its git dir; a push of the
    checked-out branch adds `@<branch>`) and, for a push, the remote it reaches (`remote`).
    """
    want = "branch" if context.get("operation_type") == "branch-delete" else "push"
    for (sub, args, *_rest), directory, switched in _git_leg_states(command, start):
        if sub == want:
            break
    else:
        return context
    git_dir = _git_dir_of(directory) if directory is not None else None
    if directory is None:
        checkout = _UNREADABLE
    else:
        checkout = str(git_dir) if git_dir else "no-repo:" + os.path.realpath(directory)
    branch = _branch_of(directory, switched)
    bound = dict(context)
    if want == "push":
        if _implicit_push_shape(args) == "current" or context.get("operation_type") == "merge":
            checkout += "@" + branch
        bound["remote"] = _push_remote_identity(args, directory, branch)
    bound["repo"] = checkout
    return bound
