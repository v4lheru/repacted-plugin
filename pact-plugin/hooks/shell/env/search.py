"""
Location: pact-plugin/hooks/shell/env/search.py
Summary: Recursive-search verdicts: dumps_environment, grep/rg filters, recursive_search,
         first_protected_file and the search roots, the git names-only cut, and the
         protected words an egress client sends (_sent_words).
Used by: hooks/env_guard.py (the entry hook and façade) and the other shell.env
         modules per the imports below. Carved from env_guard.py at M2; every
         decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
Map: _deny, _PRINTS, SCRATCH_HINT, _named_message, _file_keys, dumps_environment, _positionals, _option_values, _option_positions, _standing_excludes, _GrepFilters, _grep_is_recursive, _pattern_by_option, recursive_search, _sees_hidden, _excluded, _included, _skipped, _TooLarge, first_protected_file, BRACE_ROOTS, _glob_walk, _search_roots, _recurse_verdict, _cut_lists_names_only, _git_verdict, _SENDS, _REMOTE, _sent_words.
"""

from __future__ import annotations

import fnmatch
import glob as globbing
import os
import re
from shell.env.names import (CLASS_SAMPLES, _POSIX_CLASSES, _absolute, _brace_expansions, _python_glob, file_is_protected, is_protected_name, mentions_class, token_protected)
from shell.env.programs import (CLOUD_COPIES, CREDENTIAL_SUGGESTION, GIT_PATCH_OPTIONS, GIT_SAFE, GREP_PROGRAMS, IDENTITY_OPTIONS, LITERAL_FIELD_OPTIONS, RG_PROGRAMS, SUGGESTION, WALK_LIMIT, WALK_SKIP_DIRS, rule_of, _DUMP_RUNNERS, _GIT_VALUE_OPTIONS, _GREP_VALUE_OPTIONS, _RG_VALUE_OPTIONS)
from shell.env.script import (_SUB_MARK)

def _deny(reason: str) -> dict:
    """The deny envelope. The reason gains its rule-id tail `[rule: <id>]` here, the one
    place every env deny passes (programs.RULE_MARKS); the text before the tail is the
    message as written."""
    rule = rule_of(reason)
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": f"{reason} [rule: {rule}]" if rule else reason,
    }}


_PRINTS = "print the contents of {path} (a .env-class file) into the transcript"
SCRATCH_HINT = (" A dummy or scratch file is read freely under a name outside the class "
                "(env-sample.txt).")


def _named_message(path: str, what: str = _PRINTS) -> str:
    """The deny for a named protected file. A name outside the .env class is a credential
    file (names.is_credential_path): env-get does not apply to it, so the way through is
    to hand its path to the program that uses it."""
    if not mentions_class(path):
        what = what.replace("(a .env-class file)", "(a credential file)")
        return f"Blocked: this would {what.format(path=path)}; {CREDENTIAL_SUGGESTION}."
    return (f"Blocked: this would {what.format(path=path)}; {SUGGESTION}."
            + (SCRATCH_HINT if what is _PRINTS else ""))


def _file_keys(path: str, cwd: str | None) -> set[str] | None:
    """The key names a protected file assigns (never its values), or None if unreadable."""
    absolute = _absolute(path, cwd)
    if absolute is None:
        return None
    try:
        with open(absolute, encoding="utf-8", errors="replace") as handle:
            text = handle.read(1_000_000)
    except OSError:
        return None
    return set(re.findall(r"^[ \t]*(?:export[ \t]+)?([A-Za-z_]\w*)[ \t]*=", text, re.M))


# ── Bash ──────────────────────────────────────────────────────────────────────────────
def dumps_environment(words: list[str]) -> bool:
    """env/printenv with nothing to run or name, export/declare/typeset listing, bare set."""
    i = 0
    while i < len(words) and words[i] in _DUMP_RUNNERS:
        i += 1
    if i == len(words):
        return False
    program, args = words[i].rsplit("/", 1)[-1], words[i + 1:]
    if program == "env":
        j = 0
        while j < len(args):
            if args[j] in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string"):
                j += 2
            elif args[j].startswith("-") or "=" in args[j]:
                j += 1
            else:
                return False                              # a command follows: env runs it
        return True
    if program == "printenv":
        return all(arg.startswith("-") for arg in args)
    if program == "export":
        return all(arg.startswith("-") for arg in args)
    if program in ("declare", "typeset"):
        return all(arg.startswith("-") for arg in args) and (
            not args or any("p" in arg or "x" in arg for arg in args))
    if program == "set":
        return not args
    return False


def _positionals(args: list[str], value_options: frozenset) -> list[str]:
    out, i = [], 0
    while i < len(args):
        word = args[i]
        if word == "--":
            out.extend(args[i + 1:])
            break
        if word.startswith("-") and word != "-":
            i += 2 if word in value_options else 1
            continue
        out.append(word)
        i += 1
    return out


def _option_values(args: list[str], names: tuple[str, ...]) -> list[str]:
    values = []
    for i, word in enumerate(args):
        for name in names:
            if word == name and i + 1 < len(args):
                values.append(args[i + 1])
            elif word.startswith(name + "="):
                values.append(word[len(name) + 1:])
    return values


def _option_positions(args: list[str], names: tuple[str, ...]) -> list[tuple[int, str, str]]:
    """(index, option name, value) of each of these options, in command-line order."""
    found = []
    for i, word in enumerate(args):
        for name in names:
            if word == name and i + 1 < len(args):
                found.append((i, name, args[i + 1]))
            elif word.startswith(name + "="):
                found.append((i, name, word[len(name) + 1:]))
    return found


def _standing_excludes(excludes: list[tuple[int, str]], includes: list[int]) -> list[str]:
    """The exclusions no later inclusion can undo. rg lets the last matching glob win, so
    `-g '!.env*' -g '*'` reads .env: an exclusion counts here only when no inclusion follows
    it."""
    return [value for at, value in excludes if not any(later > at for later in includes)]


class _GrepFilters(list):
    """grep's --exclude patterns (the list), with every --exclude and --include in command
    order. The two greps read them differently, and a file is skipped only when both skip
    it: GNU lets the last matching filter win and, where none matches, skips only when the
    first filter is an --include; BSD lets any matching --include win, then any matching
    --exclude, and skips the rest when there is an --include."""

    def __init__(self, order: list[tuple[int, str, str]]):
        super().__init__(value for _, name, value in order if name == "--exclude")
        self.order = order

    def skips(self, name: str) -> bool:
        hits = [option for _, option, value in self.order if
                (_excluded if option == "--exclude" else _included)(name, [value])]
        gnu = hits[-1] == "--exclude" if hits else self.order[0][1] == "--include"
        bsd = "--include" not in hits and ("--exclude" in hits or any(
            option == "--include" for _, option, _ in self.order))
        return gnu and bsd


def _grep_is_recursive(args: list[str]) -> bool:
    for i, word in enumerate(args):
        if word in ("--recursive", "--dereference-recursive", "--directories=recurse"):
            return True
        if word in ("-d", "--directories") and i + 1 < len(args) and args[i + 1] == "recurse":
            return True
        if word.startswith("-") and not word.startswith("--"):
            for letter in word[1:]:
                if letter in "rR":
                    return True
                if letter in "efmABCdD":
                    break
    return False


def _pattern_by_option(args: list[str], value_letters: str = "efmABCdD") -> bool:
    """True when an option gives the search its pattern (-e, -f, --regexp, --file; attached
    as -eKEY or in a cluster as -rne too), so that no positional word is the pattern."""
    for word in args:
        if word == "--":
            break
        if word.split("=", 1)[0] in ("--regexp", "--file"):
            return True
        if word.startswith("-") and not word.startswith("--"):
            for letter in word[1:]:
                if letter in "ef":
                    return True
                if letter in value_letters:
                    break
    return False


def recursive_search(program: str, args: list[str]) -> tuple[list[str], list[str], list[str], bool] | None:
    """(search roots, exclusion patterns, inclusion patterns, sees hidden files) for a
    recursive search, or None when the command is not one. Inclusion patterns are a
    whitelist: when there are any, a file matching none of them is never read."""
    if program in GREP_PROGRAMS:
        if not _grep_is_recursive(args):
            return None
        positionals = _positionals(args, _GREP_VALUE_OPTIONS)
        if not _pattern_by_option(args):
            positionals = positionals[1:]
        filters = _GrepFilters(_option_positions(args, ("--exclude", "--include")))
        return (positionals or ["."], filters,
                [value for _, name, value in filters.order if name == "--include"], True)
    if program in RG_PROGRAMS:
        positionals = _positionals(args, _RG_VALUE_OPTIONS)
        if not _pattern_by_option(args, "efgtTmABCMjdrE"):
            positionals = positionals[1:]
        # --iglob matches without regard to case: compared in lower case, as the names are.
        globs = [(at, value.casefold() if name == "--iglob" else value) for at, name, value
                 in _option_positions(args, ("-g", "--glob", "--iglob"))]
        excludes = _standing_excludes([(at, g[1:]) for at, g in globs if g.startswith("!")],
                                      [at for at, g in globs if g and not g.startswith("!")])
        excludes += _option_values(args, ("--ignore",))
        includes = [g for _, g in globs if g and not g.startswith("!")]
        return positionals or ["."], excludes, includes, _sees_hidden(program, args)
    if program == "git":
        rest = _positionals(args, _GIT_VALUE_OPTIONS)
        if not rest or rest[0] != "grep":
            return None
        if not any(word in ("--untracked", "--no-index") for word in args):
            return None                                   # tracked files only
        grep_args = args[args.index("grep") + 1:]
        positionals = _positionals(grep_args, frozenset({"-e", "-f", "-m", "-A", "-B", "-C"}))
        if not _pattern_by_option(grep_args, "efmABC"):
            positionals = positionals[1:]
        roots, excludes = [], []
        for word in positionals:
            for prefix in (":!", ":^", ":(exclude)"):
                if word.startswith(prefix):
                    excludes.append(word[len(prefix):])
                    break
            else:
                roots.append(word)
        return roots or ["."], excludes, [], True
    return None


def _sees_hidden(program: str, args: list[str]) -> bool:
    """True when the search reads dotfiles: --hidden or -., or enough unrestricted levels
    (two for rg, as -uu or -u -u; one for ag and ack)."""
    levels = 0
    for word in args:
        if word in ("--hidden", "-a"):
            return True
        if word == "--unrestricted":
            levels += 1
        elif word.startswith("-") and not word.startswith("--"):
            if "." in word:
                return True
            levels += word.count("u")
    return levels >= (2 if program == "rg" else 1)


def _excluded(name: str, excludes: list[str]) -> bool:
    """True when an exclusion pattern removes a file of this name wherever it sits. Matched
    as grep and rg match, case-sensitively; a pattern tied to a path (src/.env*, /.env)
    removes it in one place only, so only `**/` may come before the base name."""
    for pattern in excludes:
        head, slash, base = pattern.rpartition("/")
        if slash and any(part != "**" for part in head.split("/")):
            continue
        if base and fnmatch.fnmatchcase(name, base):
            return True
    return False


def _included(name: str, includes: list[str]) -> bool:
    """True when a whitelist pattern can admit a file of this name. A pattern that names
    a directory (conf/, src/**) admits any name under it."""
    for pattern in includes:
        base = pattern.rpartition("/")[2]
        if base in ("", "**") or fnmatch.fnmatchcase(name, base):
            return True
    return False


def _skipped(name: str, excludes: list[str], includes: list[str]) -> bool:
    """True when the search never reads a file of this name: it is excluded, or an
    inclusion whitelist exists and it matches none of the whitelist (for grep, as both
    greps read their filters, see _GrepFilters)."""
    if isinstance(excludes, _GrepFilters):
        return bool(excludes.order) and excludes.skips(name)
    return _excluded(name, excludes) or bool(includes) and not _included(name, includes)


class _TooLarge(Exception):
    pass


def first_protected_file(roots: list[str], cwd: str, excludes: list[str], hidden: bool,
                         includes: list[str] = (), skip_dirs: list[str] = ()) -> str | None:
    """The first protected, non-excluded file under the roots, as a path relative to the
    shell. Sub-directories matching `skip_dirs` are not entered (the search skips them too);
    a root is always entered. Raises _TooLarge past WALK_LIMIT entries: an unchecked tree
    is not a clean one."""
    seen = 0
    for root in roots:
        top = _absolute(root, cwd)
        if top is None or not os.path.isdir(top):
            name = os.path.basename(top or "")     # a file a glob root matched (_search_roots)
            if top and (is_protected_name(name) or os.path.islink(top)) \
                    and file_is_protected(name, top) and not _skipped(name, excludes, list(includes)):
                return root
            continue
        for directory, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if d not in WALK_SKIP_DIRS and (hidden or not d.startswith("."))
                       and not any(fnmatch.fnmatchcase(d, pattern) for pattern in skip_dirs)]
            seen += len(dirs) + len(files)
            if seen > WALK_LIMIT:
                raise _TooLarge
            for name in files:
                if not hidden and name.startswith("."):
                    continue
                path = os.path.join(directory, name)
                if (is_protected_name(name) or os.path.islink(path)) \
                        and file_is_protected(name, path) \
                        and not _skipped(name, excludes, list(includes)):
                    return os.path.join(root, os.path.relpath(path, top))
    return None


BRACE_ROOTS = 16                                   # words one brace-list root may expand to


def _glob_walk(pattern: str) -> list[str]:
    """What an absolute glob holding `**` matches, `**` standing for any number of
    directories (zsh, bash globstar; without globstar bash reads it as `*`, which matches
    less). A dotfile matches too, as under dotglob or GLOB_DOTS. .git and node_modules are
    not entered (see first_protected_file). Raises _TooLarge past WALK_LIMIT entries."""
    parts = pattern.split("/")
    first = next(i for i, part in enumerate(parts) if any(char in part for char in "*?["))
    found, seen = [], [0]

    def visit(path: str, rest: list[str]) -> None:
        if not rest:
            found.append(path)
            return
        part, tail = rest[0], rest[1:]
        if not any(char in part for char in "*?["):
            if os.path.lexists(os.path.join(path, part)):
                visit(os.path.join(path, part), tail)
            return
        try:
            entries = list(os.scandir(path))
        except OSError:
            return
        seen[0] += len(entries)
        if seen[0] > WALK_LIMIT:
            raise _TooLarge
        if part == "**":
            visit(path, tail)                          # no directory at all
        for entry in entries:
            if part == "**" and entry.is_dir(follow_symlinks=False) \
                    and entry.name not in WALK_SKIP_DIRS:
                visit(entry.path, rest)                # one directory more
            elif part == "**" and not tail or part != "**" \
                    and fnmatch.fnmatchcase(entry.name, _python_glob(part)):
                visit(entry.path, tail)

    visit("/".join(parts[:first]) or "/", parts[first:])
    return sorted(set(found))


def _search_roots(roots: list[str], cwd: str | None) -> list[str] | None:
    """The roots of a recursive search as the shell hands them over: $HOME and $PWD spelled,
    a brace list expanded (kept as written too: quoted, the shell passes it so), a glob
    replaced by what it matches, `**` by a bounded walk (_glob_walk raises _TooLarge). None
    when one cannot be known here: a relative root where the directory is not known, any
    other variable, a substitution, ~- or ~+, a brace list of more than BRACE_ROOTS words or
    with a non-ASCII letter (a lookalike), a glob that matches nothing here. An unknown root
    is an unchecked tree, never a clean one."""
    found: list[str] = []
    for root in roots:
        for name, value in (("HOME", os.path.expanduser("~")), ("PWD", cwd)):
            if value is not None:               # not $PWD:h or $PWD[1,3]: zsh rewrites those
                root = re.sub(rf"\$(?:\{{{name}\}}|{name}(?![\w:\[]))", lambda _: value, root)
        listed = _brace_expansions(root) if "$" not in root else [root]
        if listed != [root]:
            more = _search_roots(listed, cwd) \
                if len(listed) <= BRACE_ROOTS and root.isascii() else None
            if more is None:
                return None
            found += more + [root]
            continue
        path = _absolute(root, cwd)
        if path is None or os.path.expanduser(root).startswith("~") \
                or any(mark in root for mark in ("$", "`", "{", _SUB_MARK)):
            return None
        if any(char in root for char in "*?[") and not os.path.isdir(path):
            # No match here may be a match in the shell (a glob form Python lacks).
            matches = [] if any(mark in root for mark in _POSIX_CLASSES) \
                else _glob_walk(path) if "**" in root else globbing.glob(_python_glob(path))
            if not matches:
                return None
            found += matches
        else:
            found.append(root)
    return found


def _recurse_verdict(program: str, args: list[str], cwd: str | None) -> str | None:
    search = recursive_search(program, args)
    if search is None:
        return None
    roots, excludes, includes, hidden = search
    if all(_skipped(sample, excludes, includes) for sample in CLASS_SAMPLES):
        return None
    # grep does not enter a sub-directory whose base name matches --exclude-dir.
    skip_dirs = [pattern for pattern in _option_values(args, ("--exclude-dir",))
                 if "/" not in pattern] if program in GREP_PROGRAMS else []
    how = ("add --exclude='.env*' --exclude='*.env' --exclude=.envrc --exclude=.netrc "
           "(grep) or -g '!.env*' -g '!*.env' -g '!.envrc' -g '!.netrc' (rg)")
    if any(arg.startswith("$") for arg in args):
        how += (", written out in the command (flags held in a shell variable reach the "
                "search as one word under zsh)")
    try:
        roots = _search_roots(roots, cwd)
        found = None if roots is None else \
            first_protected_file(roots, cwd, excludes, hidden, includes, skip_dirs)
    except _TooLarge:
        return (f"Blocked: this recursive search covers more than {WALK_LIMIT} entries, too "
                f"many to check for .env-class files; {how}.")
    if roots is None:
        return ("Blocked: this recursive search starts from a path that is not known here (a "
                "relative path where the directory was changed by more than a plain "
                "`cd <path>`, or a root the shell computes from a substitution, `read` or a "
                "loop over anything but plain words), so it cannot be checked for .env-class "
                "files; write the paths that value holds out in the command (absolute, or "
                f"relative after a plain `cd <path> &&`), or {how}.")
    if found is None:
        return None
    return (f"Blocked: this recursive search would read {found} (.env class) and print its "
            f"contents; {how}, or {SUGGESTION}.")


def _cut_lists_names_only(args: list[str], cwd: str) -> bool:
    rest = [arg for arg in args if not token_protected(arg, cwd)]
    delimiter = field = False
    i = 0
    while i < len(rest):
        word = rest[i]
        if word in ("-d=", "--delimiter=="):
            delimiter = True
        elif word == "-d" and i + 1 < len(rest) and rest[i + 1] == "=":
            delimiter, i = True, i + 1
        elif word in ("-f1", "--fields=1"):
            field = True
        elif word == "-f" and i + 1 < len(rest) and rest[i + 1] == "1":
            field, i = True, i + 1
        elif word != "-s":
            return False
        i += 1
    return delimiter and field


def _git_verdict(args: list[str]) -> bool:
    """True when this git command, naming a protected path, prints no file contents."""
    rest = _positionals(args, _GIT_VALUE_OPTIONS)
    sub = rest[0] if rest else ""
    if sub in GIT_SAFE:
        return True
    if sub == "log":
        return not any(arg in GIT_PATCH_OPTIONS or arg.startswith("--word-diff") for arg in args)
    return False


_SENDS = "send {path} (a .env-class file) off the machine"
_REMOTE = re.compile(r"[a-z][\w+.-]*://|[\w-]+:(?!//)", re.I)


def _sent_words(program: str, args: list[str], cwd: str | None) -> list[str]:
    """The protected words an egress client would send. Not sent: the value of an option
    the client reads to authenticate (scp -i ~/.ssh/id_rsa, curl --key client.key) when it
    is a credential file rather than a .env-class one; the text of gh/glab api -f, sent as
    written even when it starts with @; the local destination of
    a cloud copy whose source is remote (aws s3 cp s3://b/x ./creds), a download."""
    identity = IDENTITY_OPTIONS.get(program, frozenset())
    held = set()
    for i, word in enumerate(args):
        option, glued, value = word.partition("=")
        if word in identity and i + 1 < len(args) and not mentions_class(args[i + 1]):
            held.add(i + 1)
        elif glued and option in identity and not mentions_class(value):
            held.add(i)
        elif program in ("gh", "glab") and (word in LITERAL_FIELD_OPTIONS and i + 1 < len(args)):
            held.add(i + 1)
        elif program in ("gh", "glab") and glued and option in LITERAL_FIELD_OPTIONS:
            held.add(i)
    plain = [i for i, word in enumerate(args) if not word.startswith("-")]
    if plain and set(args[i] for i in plain) & CLOUD_COPIES.get(program, frozenset()) and any(
            _REMOTE.match(args[i]) for i in plain[:-1]) and not _REMOTE.match(args[plain[-1]]):
        held.add(plain[-1])
    return [word for i, word in enumerate(args) if i not in held and token_protected(word, cwd)]
