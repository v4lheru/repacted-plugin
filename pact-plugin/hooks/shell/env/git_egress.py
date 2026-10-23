"""
Location: pact-plugin/hooks/shell/env/git_egress.py
Summary: git_egress_pass and git_egress_verdict: add, commit, show and push checks, the
         credential-file and protected-kind classifiers, inline aliases.
Used by: hooks/env_guard.py (the entry hook and façade) and the other shell.env
         modules per the imports below. Carved from env_guard.py at M2; every
         decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
Map: git_egress_pass, _known_directories, _git_pass, CREDENTIAL_NAMES, CREDENTIAL_SUFFIXES, GIT_TIMEOUT_S, PUSH_TIMEOUT_S, _GIT_SHOW_PATCH, _GIT_DIFF_NAMES_ONLY, _GIT_ADD_VALUE_OPTIONS, _GIT_COMMIT_VALUE_OPTIONS, _GIT_COMMIT_VALUE_LETTERS, _GIT_PUSH_VALUE_OPTIONS, UNCHECKED_STAGING, UNKNOWN_DIRECTORY, CREATED_REPO, UNVERIFIED_PUSH, _Unverifiable, Notice, is_credential_file, git_protected_kind, _run_git, _git_names, _in_repo, _first_protected, _git_split, _stage_message, _unknown_pathspecs, _check_add, _commit_flags, _check_commit, _env_only, _check_show, _push_sources, _EXPANDED_WORD, EXPANDED_PUSH, _resolves_none, _check_push, EXPANDED_GIT, _inline_alias, git_egress_verdict.
"""

from __future__ import annotations

import os
import re

from shell import git_alias
from shell.words import strip_prefix
from shell.env.names import (is_protected_name, is_template_name)
from shell.env.places import (_Directory)
from shell.env.programs import (EGRESS_PROGRAMS, _GIT_VALUE_OPTIONS)
from shell.env.script import (SHELLS, _IGNORE_WRITE, _SUB_KEY, _State, _UNSEEN_IGNORE, _command_steps, _computed, _pull_substitutions, expand_literal_variables)
from shell.env.search import (_positionals)

def git_egress_pass(command: str, cwd: str) -> str | None:
    """Run the git egress checks over each simple command, in the directory git runs in
    (see _Directory and expand_literal_variables)."""
    if "git" not in command and "gh" not in command:
        return None
    reason = _git_pass(expand_literal_variables(command), cwd)
    if reason and not isinstance(reason, Notice) and "would stage" in reason \
            and _IGNORE_WRITE.search(command):
        return reason + _UNSEEN_IGNORE
    return reason


def _known_directories(args: list[str], state: _State, repo: str | None) -> list[str]:
    """git's words with a `-C` value that is only `$(pwd)` or `$(git rev-parse
    --show-toplevel)` spelled as the directory it names, when the directory git runs in is
    known: any other computed value stays unknown (see _git_split)."""
    out = list(args)
    for i in range(len(out) - 1):
        body = state.subs.get(out[i + 1], "").split() if out[i] == "-C" and repo else []
        if body in (["pwd"], ["pwd", "-L"], ["pwd", "-P"]):
            out[i + 1] = os.path.realpath(repo) if body[-1] == "-P" else repo
        elif body == ["git", "rev-parse", "--show-toplevel"]:
            try:
                done = _run_git(repo, ["rev-parse", "--show-toplevel"], GIT_TIMEOUT_S)
            except _Unverifiable:
                continue
            top = done.stdout.decode("utf-8", "replace").strip()
            if done.returncode == 0 and top:
                out[i + 1] = top
    return out


def _git_pass(command: str, cwd: str | None) -> str | None:
    """A substitution is pulled out first (a quote inside $(...) must not break the
    tokenizer) and judged in the directory of the command that carries it."""
    state = _State()
    steps, exact = _command_steps(_pull_substitutions(command, state))
    where = _Directory(cwd, exact)
    notices: list[str] = []
    created, judged = False, set()
    for step in steps:
        words, places = step.words, where.step(step)
        repo = places[0] if len(places) == 1 else None   # more than one: not known
        reasons = []
        command_words = strip_prefix(words)
        inner = _shell_body(words, command_words)
        if inner is not None and "git" in inner:          # bash -c 'git push', eval 'git push'
            reasons.append(_git_pass(inner, repo))
        program = command_words[0].rsplit("/", 1)[-1] if command_words else ""
        if program == "gh":
            reasons.append(_gh_push(command_words[1:], repo))
        if program == "git":
            reasons.append(_history_sent(command_words[1:], steps, steps.index(step)))
        if program == "git":
            args = _known_directories(command_words[1:], state, repo)
            # Quote-deleted words: a -C path may have been cut at a space.
            reasons.append(git_egress_verdict(args, None if not exact and "-C" in args else repo,
                                              created))
            sub, sub_args, _ = _git_split(args, None)
            created = created or sub in ("init", "clone") or sub == "worktree" and "add" in sub_args
        for key in (key for word in words for key in _SUB_KEY.findall(word)):
            if key not in judged and key in state.subs:
                judged.add(key)
                body = state.subs[key]
                reasons.append(_git_pass(body, repo) if "git" in body else None)
        for reason in reasons:
            if isinstance(reason, Notice):
                notices.append(reason)
            elif reason:
                return reason
    for key, body in state.subs.items():                 # carried by a redirect target
        if key not in judged and "git" in body:
            reason = _git_pass(body, None)
            if reason and not isinstance(reason, Notice):
                return reason
    return Notice("; ".join(notices)) if notices else None


def _shell_body(words: list[str], command: list[str]) -> str | None:
    """The script a runner prefix hands to a shell: `bash -c S`, `eval S`."""
    leading = words[:len(words) - len(command)]
    names = [word.rsplit("/", 1)[-1] for word in leading]
    if "eval" in names:
        return " ".join(words[names.index("eval") + 1:])
    if command and any(name in SHELLS for name in names) and any(
            word.startswith("-") and not word.startswith("--") and "c" in word
            for word in leading):
        return command[0]
    return None


def _gh_push(args: list[str], repo: str | None) -> str | None:
    """`gh repo create NAME --source DIR --push` pushes DIR's commits: checked as a push of
    HEAD from DIR."""
    if args[:2] != ["repo", "create"] or "--push" not in args:
        return None
    source = "."
    for i, word in enumerate(args):
        if word == "--source" and i + 1 < len(args):
            source = args[i + 1]
        elif word.startswith("--source="):
            source = word.split("=", 1)[1]
    if repo is None or _EXPANDED_WORD.search(source):
        return UNVERIFIED_PUSH.format(why="the directory it pushes from is not known here")
    return _check_push(["origin", "HEAD"], os.path.join(repo, os.path.expanduser(source)))


# git subcommands that write commits' contents to stdout.
_HISTORY_WRITERS = frozenset({"format-patch", "bundle", "archive"})
HISTORY_SENT = ("Blocked: this pipes git history ({sub}) into {program}, which sends it off "
                "this machine without the push check reading it; push the branch instead (a "
                "push is checked for secrets), or send it from your own terminal.")


def _history_sent(args: list[str], steps: list, index: int) -> str | None:
    """`git format-patch --stdout | curl -T -`: history piped into a program that sends it."""
    sub = _git_split(args, None)[0]
    if sub not in _HISTORY_WRITERS:
        return None
    for later in steps[index + 1:]:
        if not _PIPE_BEFORE.search(later.before):
            break
        program = (strip_prefix(later.words)[:1] or [""])[0].rsplit("/", 1)[-1]
        if program in EGRESS_PROGRAMS:
            return HISTORY_SENT.format(sub=sub, program=program)
    return None


_PIPE_BEFORE = re.compile(r"(?<!\|)\|(?!\|)")


# ── egress: git (ENV-GUARD-2, decision D29) ─────────────────────────────────────────────
# Only a credential that LEAVES the machine matters (D29), and git is the realistic way out:
# a staged file is one commit and one push from a remote. So git may not stage, commit or push
# an env-class file or a credential file, nor show a tracked or staged env-class file. The
# checks run git itself (bounded), load lazily, and cost nothing for any other command.
# Staging and push fail CLOSED when they cannot run; the show forms fail OPEN (not egress).

# An env template (GIT_TEMPLATE_SUFFIXES) is made to be committed.
CREDENTIAL_NAMES = frozenset({".npmrc", ".pypirc", ".git-credentials", "id_rsa", "id_ed25519",
                              "id_ecdsa", "id_dsa", "application_default_credentials.json"})
CREDENTIAL_SUFFIXES = (".key", ".pem")
GIT_TIMEOUT_S = 3.0            # each staging, commit or show inspection
PUSH_TIMEOUT_S = 10.0          # the whole outgoing-range scan
_GIT_SHOW_PATCH = frozenset({"-p", "--patch", "-u", "--patch-with-stat", "--patch-with-raw",
                             "--full-diff", "-L"})
_GIT_DIFF_NAMES_ONLY = frozenset({"--name-only", "--name-status", "--stat", "--numstat",
                                  "--shortstat", "--summary", "--dirstat", "--quiet"})
_GIT_ADD_VALUE_OPTIONS = frozenset({"--chmod", "--pathspec-from-file"})
_GIT_COMMIT_VALUE_OPTIONS = frozenset({"-m", "--message", "-F", "--file", "-C", "--reuse-message",
                                       "-c", "--reedit-message", "--author", "--date", "--fixup",
                                       "--squash", "-t", "--template", "--cleanup", "--trailer"})
_GIT_COMMIT_VALUE_LETTERS = "mFCct"
_GIT_PUSH_VALUE_OPTIONS = frozenset({"--repo", "-o", "--push-option", "--receive-pack",
                                     "--exec"})
UNCHECKED_STAGING = ("Blocked: could not check what this git command would stage or commit "
                     "({why}); stage files by name, or run it from your own terminal.")
UNKNOWN_DIRECTORY = ("Blocked: could not check what this git command would stage or commit "
                     "(the directory it runs in is not known here); rerun it with the path "
                     "written out, as `git -C <path> ...` or after a plain `cd <path> &&`, "
                     "or run it from your own terminal.")
CREATED_REPO = ("Blocked: this command creates a repository (git init, clone or worktree add) "
                "and stages into it in the same call, so what it would stage cannot be listed "
                "beforehand; create it in its own command, then stage.")
UNVERIFIED_PUSH = ("Blocked: could not verify what this push sends ({why}); push from your "
                   "own terminal if you're sure.")


class _Unverifiable(Exception):
    """A git inspection that could not finish (timeout, git missing)."""


class Notice(str):
    """An ALLOW that must be seen: printed to the user and the model, never a deny (the
    pragma skip count on a push, lead ruling on #225)."""


def is_credential_file(path: str) -> bool:
    """A credential file by name alone (no file is opened): SSH private keys, *.key and *.pem,
    registry and git credential files, Google client secrets and service credentials."""
    parts = path.replace("\\", "/").split("/")
    name = parts[-1].casefold()
    if name in CREDENTIAL_NAMES or name.endswith(CREDENTIAL_SUFFIXES):
        return True
    if name.startswith("client_secret") and name.endswith(".json"):
        return True
    return "credentials" in name and name.endswith(".json") and (
        name.startswith(".") or any(p.startswith(".") and p not in (".", "..") for p in parts[:-1]))


def git_protected_kind(path: str) -> str | None:
    """'env file' or 'credential file' when git must not stage, commit or push `path`."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if is_protected_name(name) and not is_template_name(name):
        return "env file"
    return "credential file" if is_credential_file(path) else None


def _run_git(repo: str, args: list[str], timeout: float):
    import subprocess
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=timeout,
                              env=env)
    except subprocess.TimeoutExpired as error:
        raise _Unverifiable("timed out") from error
    except OSError as error:
        raise _Unverifiable("git did not run") from error


def _git_names(repo: str, args: list[str], timeout: float) -> list[str] | None:
    """The NUL- or newline-separated names a git listing prints, or None when git refused."""
    done = _run_git(repo, args, timeout)
    if done.returncode != 0:
        return None
    text = done.stdout.decode("utf-8", "replace")
    return [name for name in re.split(r"[\0\n]", text) if name.strip()]


def _in_repo(repo: str, timeout: float) -> bool:
    return _run_git(repo, ["rev-parse", "--git-dir"], timeout).returncode == 0


def _first_protected(names) -> tuple[str, str] | None:
    for name in names or ():
        kind = git_protected_kind(name)
        if kind:
            return name, kind
    return None


def _git_split(args: list[str], repo: str | None) -> tuple[str, list[str], str | None]:
    """(subcommand, its arguments, the repo after any -C) for the words after `git`."""
    i = 0
    while i < len(args):
        word = args[i]
        if word == "-C" and i + 1 < len(args):
            # A path the shell computes or expands ($(pwd), $W, a glob, a brace list) is
            # not known here: staging and push then fail closed.
            repo = os.path.normpath(os.path.join(repo, os.path.expanduser(args[i + 1]))) \
                if repo is not None and not _computed(args[i + 1]) \
                and not any(char in args[i + 1] for char in "*?[{") else None
            i += 2
        elif word in _GIT_VALUE_OPTIONS and i + 1 < len(args):
            i += 2
        elif word.startswith("-"):
            i += 1
        else:
            return word, args[i + 1:], repo
    return "", [], repo


def _stage_message(path: str, kind: str, verb: str = "stage") -> str:
    return (f"Blocked: this would {verb} {path} ({kind}); credentials must never be committed. "
            f"Add it to .gitignore or unstage it with `git rm --cached {path}`, then stage files "
            "by name.")


def _unknown_pathspecs(pathspecs: list[str], options: list[str]) -> bool:
    """True when the paths are not all written out: one the shell computes or expands, or
    --pathspec-from-file (any unambiguous start of it)."""
    return any(_computed(p) or "{" in p for p in pathspecs) or any(
        len(name) > 2 and "--pathspec-from-file".startswith(name)
        for name in (word.split("=", 1)[0] for word in options if word.startswith("--")))


def _check_add(args: list[str], repo: str, created: bool = False) -> str | None:
    """`created`: an earlier part of the same command makes a repository, so the tree git
    would list now is not the one this add stages from."""
    options = args[:args.index("--")] if "--" in args else args
    # git add takes no value after a short option, so every letter of -Av is a flag.
    letters = {letter for word in options if word.startswith("-") and not word.startswith("--")
               for letter in word[1:]}
    if "n" in letters or "--dry-run" in options:
        return None                                           # names only, nothing staged
    pathspecs = _positionals(args, _GIT_ADD_VALUE_OPTIONS)
    named = _first_protected(p for p in pathspecs if not any(c in p for c in "*?[:"))
    if named:
        return _stage_message(*named)
    all_mode = "A" in letters or any(word in ("--all", "--no-ignore-removal") for word in options)
    update = "u" in letters or "--update" in options
    force = "f" in letters or "--force" in options
    # A pathspec the shell computes ("$f", $(echo .env), {a,b}) or read from a file or stdin
    # can be any path: the whole tree is checked, as for `git add -A`.
    unknown = _unknown_pathspecs(pathspecs, options)
    broad = [p for p in pathspecs if p in (".", ":/") or p.startswith(":")
             or any(c in p for c in "*?[") or os.path.isdir(os.path.join(repo, p))]
    if not (all_mode or update or broad or unknown or (force and pathspecs)):
        return None                                           # plain named files, checked above
    if created:
        return CREATED_REPO
    if not _in_repo(repo, GIT_TIMEOUT_S):
        return None
    scope = [":/"] if unknown else pathspecs or ([":/"] if all_mode or update else [])
    listing = ["ls-files", "-z", "-m"] + ([] if update else ["-o"])
    if not update and not force:
        listing.append("--exclude-standard")
    hit = _first_protected(_git_names(repo, listing + ["--", *scope], GIT_TIMEOUT_S))
    if hit:
        return _stage_message(*hit)
    # A TRACKED env file (amend_1: "would be staged or is tracked") blocks the broad forms even
    # unchanged. Credential files are held to the would-stage test only: many repositories
    # track a harmless .npmrc, which the tracked test would block on every `git add -A`.
    tracked = _env_only(_git_names(repo, ["ls-files", "-z", "-c", "--", *scope], GIT_TIMEOUT_S))
    if tracked:
        return (f"Blocked: {tracked[0]} ({tracked[1]}) is tracked in this repo, so broad staging "
                f"keeps committing it; untrack it with `git rm --cached {tracked[0]}` and "
                "add it to .gitignore, then stage files by name.")
    return None


def _commit_flags(args: list[str]) -> tuple[bool, list[str]]:
    """(stages every tracked change, the pathspecs) of a git commit."""
    all_mode, pathspecs, i = False, [], 0
    while i < len(args):
        word = args[i]
        if word == "--":
            pathspecs += args[i + 1:]
            break
        if word in ("-a", "--all"):
            all_mode = True
        elif word.split("=", 1)[0] in _GIT_COMMIT_VALUE_OPTIONS and "=" not in word:
            i += 1                                            # its value is the next word
        elif word.startswith("-") and not word.startswith("--") and len(word) > 2:
            for position, letter in enumerate(word[1:]):
                if letter == "a":
                    all_mode = True
                if letter in _GIT_COMMIT_VALUE_LETTERS:
                    if position == len(word) - 2:
                        i += 1                                # `-am MSG`: the value follows
                    break
        elif not word.startswith("-"):
            pathspecs.append(word)
        i += 1
    return all_mode, pathspecs


def _check_commit(args: list[str], repo: str) -> str | None:
    all_mode, pathspecs = _commit_flags(args)
    named = _first_protected(p for p in pathspecs if not any(c in p for c in "*?[:"))
    if named:
        return _stage_message(*named, verb="commit")
    if not _in_repo(repo, GIT_TIMEOUT_S):
        return None
    staged = _first_protected(_git_names(repo, ["diff", "--cached", "--name-only", "-z"],
                                         GIT_TIMEOUT_S))
    if staged:
        return (f"Blocked: {staged[0]} ({staged[1]}) is staged, so this commit would record it; "
                f"unstage it with `git rm --cached {staged[0]}` first.")
    unknown = _unknown_pathspecs(pathspecs, args)
    if all_mode or pathspecs or unknown:
        scope = ["--", *pathspecs] if pathspecs and not unknown else ["--", ":/"]
        hit = _first_protected(_git_names(repo, ["ls-files", "-z", "-m", *scope], GIT_TIMEOUT_S))
        if hit:
            return _stage_message(*hit, verb="commit")
    return None


def _env_only(names) -> tuple[str, str] | None:
    """The first env-class name (templates excluded): the show forms guard the transcript
    class, while F8's credential class applies to staging and push (amend_1)."""
    for name in names or ():
        if git_protected_kind(name) == "env file":
            return name, "env file"
    return None


def _check_show(sub: str, args: list[str], repo: str) -> str | None:
    """Fails OPEN: any inspection error or timeout allows."""
    try:
        if sub == "diff":
            # The working tree's changes print too (git diff, git diff HEAD), not only the
            # staged ones; a names-only form prints no line of a file.
            if "--no-index" in args or any(
                    word.split("=", 1)[0] in _GIT_DIFF_NAMES_ONLY for word in args) \
                    and not any(word in _GIT_SHOW_PATCH for word in args):
                return None
            before = args[:args.index("--")] if "--" in args else args
            paths = args[args.index("--") + 1:] if "--" in args else []
            staged = ["--cached"] if any(word in ("--cached", "--staged") for word in before) \
                else []
            revs = [word for word in before if not word.startswith("-")]
            names = _git_names(repo, ["diff", "--name-only", "-z", "--no-ext-diff", *staged,
                                      *revs, "--", *paths], GIT_TIMEOUT_S)
        elif sub in ("show", "log"):
            if sub == "log" and not any(arg in _GIT_SHOW_PATCH or arg.startswith("--word-diff")
                                        for arg in args):
                return None
            before = args[:args.index("--")] if "--" in args else args
            revs = [word for word in before if not word.startswith("-") and ":" not in word]
            paths = args[args.index("--") + 1:] if "--" in args else []
            listing = ["show", "--name-only", "--format=", "--no-color"] if sub == "show" else \
                ["log", "--name-only", "--format=", "--no-color"]
            names = _git_names(repo, listing + revs + ["--", *paths], GIT_TIMEOUT_S)
        elif sub == "grep":
            paths = args[args.index("--") + 1:] if "--" in args else []
            names = _git_names(repo, ["ls-files", "-z", "-c", "--", *paths], GIT_TIMEOUT_S)
        else:
            return None
    except _Unverifiable:
        return None
    hit = _env_only(names)
    return (f"Blocked: {hit[0]} ({hit[1]}) is tracked or staged here, so this git command can "
            "print it; name the paths you need and leave it out.") if hit else None


def _push_sources(args: list[str]) -> list[str] | None:
    """The revisions a push sends, or None when it only deletes."""
    if any(word in ("-n", "--dry-run") for word in args):
        return None
    deleting = any(word in ("-d", "--delete") for word in args)
    sources = []
    if any(word in ("--all", "--mirror", "--branches") for word in args):
        sources.append("--branches")
    if any(word in ("--tags", "--mirror") for word in args):
        sources.append("--tags")
    positionals = _positionals(args, _GIT_PUSH_VALUE_OPTIONS)
    for refspec in positionals[1:]:
        refspec = refspec.lstrip("+")
        source = refspec.split(":", 1)[0]
        if source and not deleting:
            sources.append(source)
    if sources:
        return sources
    if deleting or any(refspec.startswith(":") for refspec in positionals[1:]):
        return None
    return ["HEAD"]


# A push word the shell rewrites before git sees it, and may split into more words: a
# substitution (pulled out as a __repact_ placeholder), a variable, a glob, a leading ~,
# or a brace list or range.
_EXPANDED_WORD = re.compile(r"__repact_|[$`*?\[]|^~|\{[^}]*(?:,|\.\.)")
EXPANDED_PUSH = ("Blocked: this push names what it sends with a shell expansion ($(...), ${...}, "
                 "$VAR, backticks or a glob), so the guard cannot check it for secrets; write "
                 "the branch name out literally and run it again.")


def _resolves_none(repo: str, sources: list[str], deadline: float) -> bool:
    """True when no source of a push names a commit here (a repository with no commit yet,
    a branch that does not exist): such a push fails in git before it sends anything."""
    import time
    for source in sources:
        if source.startswith("--"):
            return False                                      # --branches, --tags: not checked
        done = _run_git(repo, ["rev-parse", "--verify", "--quiet", source + "^{commit}"],
                        max(deadline - time.monotonic(), 1e-6))
        if done.returncode == 0:
            return False
    return True


# The commit check's secret detector, handed in by the entry: hooks/env_guard.py sets
# load_detector to a function returning (ALLOWLIST_PRAGMA, pragma_notice, scan_added_lines)
# from shared.scrub, loaded only when a push is checked. Nothing under hooks/shell imports the
# shared package (tests/contract/test_shell_package.py C5). Unwired, every push is UNVERIFIED.
load_detector = None            # () -> (pragma, pragma_notice, scan_added_lines), or None


def _check_push(args: list[str], repo: str) -> str | None:
    """Fails CLOSED: an outgoing range that cannot be scanned in time is denied."""
    import time
    sources = _push_sources(args)
    if sources is None:
        return None
    if any(_EXPANDED_WORD.search(word) for word in args):
        return EXPANDED_PUSH        # git would see other words than the shell sends
    deadline = time.monotonic() + PUSH_TIMEOUT_S
    try:
        if not _in_repo(repo, max(deadline - time.monotonic(), 1e-6)):
            return None
        outgoing = [*sources, "--not", "--remotes"]
        names = _git_names(repo, ["log", "--format=", "--name-only", "--no-color",
                                  "--diff-filter=ACMRT", *outgoing],
                           max(deadline - time.monotonic(), 1e-6))
        if names is None and not _resolves_none(repo, sources, deadline):
            return UNVERIFIED_PUSH.format(why="git could not list the outgoing commits")
        if names is None:
            return None             # no source names a commit: git refuses, nothing is sent
        hit = _first_protected(names)
        if hit:
            return (f"Blocked: this push sends {hit[0]} ({hit[1]}); remove it from the outgoing "
                    "commits (git rm --cached, then amend or rebase) before pushing.")
        patch = _run_git(repo, ["log", "-p", "-U0", "--format=", "--no-color", "--no-ext-diff",
                                "--diff-filter=ACMRT", *outgoing],
                         max(deadline - time.monotonic(), 1e-6))
        if patch.returncode != 0:
            return UNVERIFIED_PUSH.format(why="git could not read the outgoing changes")
    except _Unverifiable as error:
        return UNVERIFIED_PUSH.format(why=str(error))
    try:
        if load_detector is None:
            raise RuntimeError("no secret detector wired by the entry")
        pragma, pragma_notice, scan_added_lines = load_detector()
        hits, skipped = scan_added_lines(patch.stdout.decode("utf-8", "replace"))
    except Exception:                                         # noqa: BLE001 - fail closed
        return UNVERIFIED_PUSH.format(why="the secret detector did not load or failed")
    if hits:
        path, kind, _line = hits[0]
        return (f"Blocked: this push adds a {kind} value in {path}; remove it from the outgoing "
                f"commits before pushing. If you believe it is a test fixture or doc example, "
                f"ask the user to confirm the value is not real before adding `{pragma}` on "
                "that line; a known dummy already committed: the user pushes from their own "
                "terminal.")
    notice = pragma_notice(skipped)
    return Notice(notice) if notice else None


EXPANDED_GIT = ("Blocked: this git command is named by a shell expansion or a shell alias, so "
                "the guard cannot tell what it stages, commits or pushes; write the git "
                "subcommand out and run it again.")


def _inline_alias(args: list[str], sub: str) -> str | None:
    """What a subcommand defined by `-c alias.<sub>=...` before it runs, an alias that names
    another alias followed (the last definition of a name winning), or None."""
    aliases = {}
    for flag, word in zip(args, args[1:]):
        name, _, text = word.partition("=")
        if flag == "-c" and name.lower().startswith("alias."):
            aliases[name[6:].lower()] = text.strip()
        if word == sub:
            break
    value, rest, seen = None, [], set()
    while sub.lower() in aliases and sub.lower() not in seen:
        seen.add(sub.lower())
        value = aliases[sub.lower()]
        if value.startswith("!") or not value.split():
            return value
        sub, rest = value.split()[0], value.split()[1:] + rest
    return None if value is None else " ".join([sub, *rest])


def git_egress_verdict(args: list[str], repo: str | None, created: bool = False,
                       _depth: int = 0) -> str | None:
    """A deny reason when this git command would stage, commit, push or show an egress file.
    An alias from git config is spelled out and judged as what it runs (a `!` alias: its
    script, read like a command in the repo)."""
    where = repo
    sub, sub_args, repo = _git_split(args, repo)
    if _EXPANDED_WORD.search(sub):
        return EXPANDED_GIT
    alias = _inline_alias(args, sub)
    if alias is not None:                                     # git -c alias.p=push p ...
        if alias.startswith("!"):
            return EXPANDED_GIT
        sub, sub_args = (alias.split() or [""])[0], alias.split()[1:] + sub_args
    elif sub and sub not in git_alias.GIT_COMMANDS and where is not None \
            and _depth < git_alias.MAX_DEPTH:                 # git config alias.p push; git p
        expanded, script = git_alias.expand(args, where)
        if script is not None:
            return _git_pass(script, repo)
        if expanded != args:
            return git_egress_verdict(expanded, where, created, _depth + 1)
    if sub == "submodule" and "foreach" in sub_args:          # runs its string in each one
        script = sub_args[sub_args.index("foreach") + 1:]
        while script[:1] and script[0] in ("--recursive", "-q", "--quiet"):
            script = script[1:]
        # Each submodule is its own repository, not known here: a push or commit there fails
        # closed, as any git command whose directory is unknown does.
        return _git_pass(" ".join(script), None) if script else None
    sub = "add" if sub == "stage" else sub                    # git stage is git add
    if sub == "send-pack":                                    # git send-pack URL REF: a push
        sub = "push"
    elif sub == "subtree" and sub_args[:1] == ["push"]:      # sends a split of HEAD's history
        remote = _positionals(sub_args[1:], frozenset({"--prefix", "-P", "-m", "--message"}))
        sub, sub_args = "push", [(remote or ["origin"])[0], "HEAD"]
    if sub not in ("add", "commit", "push", "diff", "show", "log", "grep"):
        return None
    if sub in ("diff", "show", "log", "grep"):
        return _check_show(sub, sub_args, repo) if repo is not None else None
    if sub != "push" and repo is not None and not os.path.isdir(repo):
        repo = None             # made later in the same command (mkdir, cp -r): not known here
    if repo is None:
        why = "the directory it runs in is not known here"
        return UNVERIFIED_PUSH.format(why=why) if sub == "push" else UNKNOWN_DIRECTORY
    if sub == "push":
        return _check_push(sub_args, repo)
    try:
        return _check_add(sub_args, repo, created) if sub == "add" \
            else _check_commit(sub_args, repo)
    except _Unverifiable as error:
        return UNCHECKED_STAGING.format(why=str(error))
