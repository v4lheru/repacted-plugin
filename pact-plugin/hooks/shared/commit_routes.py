"""What a commit-creating git verb other than `git commit` brings into the branch, as -U0 patches
for git_commit_check to scan: cherry-pick and revert (each named commit against its parent, a
revert reversed), merge, rebase and `pull .` (every commit the other side brings, one by one; a
--squash merge its net diff), am (the patch files, mail encodings decoded) and commit-tree (the
tree against its first parent). Each --continue commits the index and is read as a plain commit
by the caller (`continues`). Why per verb: the design notes.

It also holds how git_commit_check reads the command around those verbs: script_words (the shell
words with comments gone and each here-document body kept as a script of its own), the closed
option tables of each verb and of git itself (git_globals, _words; a word in no table is
refused), alias (what git expands a subcommand to), moved_names and refuse_moved (a route that
names a ref an earlier step of the same command points elsewhere is refused), head_after,
follow_head and head_of (every commit a checkout, switch or reset of the same command may leave
HEAD at, per git directory: a later route is read against HEAD as the hook finds it and against
each of them; a step that copies paths out of a tree is read as a `paths` call), shell_step
(cd, pushd and popd, and the steps the follower cannot see into), commit_scope and commit_ranges (which of the index and the working tree a `git
commit` takes, and the diffs that hold it). Every refusal raises Unreadable with its reason.
"""
from __future__ import annotations

import email
import email.policy
import os
import re
import stat
import unicodedata
from typing import Callable, Optional

from shell.words import Unreadable as _ShellUnreadable, read_script, tokens

ROUTE_VERBS = frozenset(("cherry-pick", "revert", "merge", "rebase", "am", "commit-tree", "pull"))
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_CONTROL = frozenset(("--abort", "--skip", "--quit", "--edit-todo", "--show-current-patch"))
_STRATEGY = "cleanup strategy strategy-option"
_PICK = "abort commit continue edit gpg-sign quit rerere-autoupdate signoff skip"


def _table(flags: str, valued: str, short_flags: str, short_valued: str) -> tuple:
    return frozenset(flags.split()) | {"help"}, frozenset(valued.split()), short_flags + "hS", short_valued


# Every option each verb takes (the usage text of git 2.54), closed: (long flags, long options
# whose value is the next word, short flags, short letters whose value is the rest of the cluster
# or the next word). An optional value is always attached (--log=3, -Skey), so those are flags.
# `--no-<name>` is read for every name. A word in no table is refused, never guessed: an
# abbreviation (--mess) or a newer option would otherwise turn a value into a flag or a target.
_OPTIONS = {
    "merge": _table("abort allow-unrelated-histories autostash commit compact-summary continue edit "
                    "ff ff-only gpg-sign log overwrite-ignore progress quiet quit rerere-autoupdate "
                    "signoff squash stat summary verbose verify verify-signatures",
                    _STRATEGY + " file into-name message", "enqv", "FXms"),
    "pull": _table("all allow-unrelated-histories append atomic autostash commit compact-summary "
                   "dry-run edit ff ff-only force gpg-sign ipv4 ipv6 jobs keep log progress prune "
                   "quiet rebase recurse-submodules set-upstream show-forced-updates signoff squash "
                   "stat summary tags unshallow update-shallow verbose verify verify-signatures",
                   _STRATEGY + " deepen depth negotiation-tip refmap server-option shallow-exclude "
                   "shallow-since upload-pack", "46afjknpqrtv", "Xos"),
    "rebase": _table("abort allow-empty-message apply autosquash autostash "
                     "committer-date-is-author-date continue edit-todo ff force-rebase fork-point "
                     "gpg-sign ignore-date ignore-whitespace interactive keep-base keep-empty merge "
                     "quiet quit reapply-cherry-picks rebase-merges rerere-autoupdate "
                     "reschedule-failed-exec reset-author-date root show-current-patch signoff "
                     "skip stat update-refs verbose verify",
                     "empty exec onto strategy strategy-option trailer whitespace", "fimnqrv", "CXsx"),
    "cherry-pick": _table(_PICK + " allow-empty allow-empty-message ff keep-redundant-commits stdin",
                          _STRATEGY + " empty mainline", "enrsx", "Xm"),
    "revert": _table(_PICK + " pretty reference stdin", _STRATEGY + " mainline", "ens", "Xm"),
    "am": _table("3way abort allow-empty committer-date-is-author-date continue gpg-sign ignore-date "
                 "ignore-space-change ignore-whitespace interactive keep keep-cr keep-non-patch "
                 "message-id quiet quit reject rerere-autoupdate resolved retry scissors "
                 "show-current-patch signoff skip utf8 verify",
                 "directory empty exclude include patch-format quoted-cr resolvemsg whitespace",
                 "3bcikmnqrsu", "Cp"),
    "commit-tree": _table("gpg-sign", "", "", "pmF"),
}
# Options of git itself, before the verb (closed the same way). _GLOBAL_EXITS: git prints and
# runs no verb.
_GLOBAL_VALUED = frozenset(("-C", "-c", "--namespace", "--config-env", "--super-prefix",
                            "--attr-source", "--git-dir", "--work-tree"))
_GLOBAL_FLAGS = frozenset(("-p", "--paginate", "-P", "--no-pager", "--no-replace-objects",
                           "--no-lazy-fetch", "--no-optional-locks", "--no-advice", "--bare",
                           "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
                           "--icase-pathspecs"))
# The whole word: `--exec-path=<dir>` names a directory and git goes on to run the verb.
_GLOBAL_EXITS = frozenset(("--version", "-v", "--help", "-h", "--html-path", "--man-path",
                           "--info-path", "--exec-path"))
_REDIRECT = re.compile(r"^(\d*)(<|>>?|>&|<&)(.*)$")
_PATCH = ("-U0", "--no-color", "--no-ext-diff", "--diff-filter=d")
_INERT_EDITORS = {":", "true"}
# Names that read the stream git am is handed, whatever the hook's own stdin is.
_STREAM = re.compile(r"^(-|/dev/stdin|/dev/fd/\d+|/proc/(self|thread-self|\d+)/fd/\d+)$")
_MARK = re.compile(r"<<__repact_heredoc_(\d+)__")
BODY = "\0"                                     # leads a heredoc body in script_words' output
# Earlier steps of a command that point a name somewhere else: every word they name (_ALL), or
# the value of the option that names the branch (_VALUE).
_MOVER_ALL = frozenset(("branch", "tag", "update-ref", "symbolic-ref"))
_MOVER_VALUE = {"checkout": ("-b", "-B", "--orphan"), "worktree": ("-b", "-B"),
                "switch": ("-c", "-C", "--create", "--force-create", "--orphan")}
_REF_HOME = re.compile(r"^\+?(?:refs/)?(?:heads/|tags/)?")
UPSTREAM = "@{upstream}"
ALIASED = "\0alias"                             # in the moved names: a step wrote alias config
MOVED = "\0moved"                               # a HEAD an earlier step leaves where git cannot say yet
RAN = "\0ran"                                   # in the moved names: a step that is not git ran
OPEN = "\0open"                                 # in the moved names: `git -C $(...)`, its verb unread
# Steps that move HEAD (checkout, switch, reset) or copy paths out of a tree (those and restore).
# Options whose value is the next word (so it is no branch or commit), the ones that name a new
# branch, and the flags; any other option leaves HEAD unknown (MOVED).
_HEAD_VALUED = frozenset(("--conflict", "--pathspec-from-file", "-U", "--unified",
                          "--inter-hunk-context", "--orphan", "--source", "-s"))
_HEAD_NEW = frozenset(("-b", "-B", "-c", "-C", "--create", "--force-create"))
_HEAD_FLAGS = frozenset(
    "-q --quiet -f --force --discard-changes -m --merge -d --detach -l -t --track --guess "
    "--progress --ignore-other-worktrees --overwrite-ignore --recurse-submodules --overlay "
    "--ignore-skip-worktree-bits --soft --mixed --hard --keep -N --intent-to-add --refresh "
    "--auto-advance --pathspec-file-nul --ignore-unmerged -S --staged -W --worktree".split())
_HEAD_PATHS = frozenset(("-p", "--patch", "-2", "--ours", "-3", "--theirs", "--pathspec-from-file"))
# Every long option of those four verbs (the usage text of git 2.54), for _long.
_HEAD_LONG = tuple(
    "auto-advance conflict create detach discard-changes force force-create guess hard "
    "ignore-other-worktrees ignore-skip-worktree-bits ignore-unmerged intent-to-add "
    "inter-hunk-context keep merge mixed orphan ours overlay overwrite-ignore patch "
    "pathspec-file-nul pathspec-from-file progress quiet recurse-submodules refresh soft source "
    "staged theirs track unified worktree".split())
_HEAD_LONG += tuple("no-" + name for name in _HEAD_LONG)
_HEAD_MOVERS = frozenset(("checkout", "switch", "reset", "rebase", "bisect", "symbolic-ref",
                          "update-ref"))
_BRANCH_NAME = re.compile(r"[^-~^:@{\s][^~^:@{\s]*")    # a word that can name a branch, no more
_SHELL_EXPANDS = re.compile(r"[$`{]|^~")
# A word of a route that reads HEAD or what git rewrites as HEAD moves (ORIG_HEAD, a reflog
# entry such as x@{1}, @{u}, `..x`).
_HEAD_WORD = re.compile(r"(?<![\w/-])(?<![\w/-]\.)(?:[A-Z_]*HEAD|@)(?![\w/-])(?!\.[^.])|@\{|^\.\.|\.\.$")
_HEAD_NAME = re.compile(r"(?<![\w/-])(?<![\w/-]\.)(?:HEAD|@)(?![\w/@{-])(?!\.[^.])")


class Unreadable(Exception):
    """What the verb brings in cannot be read ahead (stdin, a mailbox directory, a pipe, an exec
    step that runs git): it is refused."""


def script_words(command: str, operators: str) -> Optional[list]:
    """The words of `command` as the shell splits them, comments gone, or None when they cannot
    be read. A heredoc body inside a quoted word stays in that word (a commit message); any other
    body follows its command as one word led by BODY, for the caller to read as a script. Where
    read_script is not sure how the shells read the text, every word is read as typed, comments
    and bodies included: more words, never fewer."""
    try:
        clean, docs = read_script(command)
    except _ShellUnreadable:
        clean, docs = command, []
    words = tokens(clean, operators)
    if words is None and clean.endswith("\\"):          # the shells run the line without it
        words = tokens(clean[:-1], operators)
    if words is None:
        return None
    out, held = [], []
    for word in words + [";"]:
        if set(word) <= set(operators) and held:
            for body in held:
                out += [";", BODY + body]
            held = []
        marks = [int(n) for n in _MARK.findall(word)]
        if any(n >= len(docs) for n in marks):
            return None
        if marks and re.search(r"\s", word):
            out.append(_MARK.sub(lambda mark: "\n" + docs[int(mark.group(1))][1] + "\n", word))
        else:
            held += [docs[n][1] for n in marks]
            out += [_MARK.sub("", word)] if _MARK.sub("", word) else []
    return out[:-1]


def git_globals(words: list) -> tuple:
    """(index of the verb in a `git ...` word list, or None when git runs none; [(option,
    value)] before it). Unreadable for an option of git this reader does not know."""
    pairs, i = [], 1
    while i < len(words) and words[i].startswith("-"):
        name, attached, value = words[i].partition("=")
        if words[i] in _GLOBAL_EXITS or name == "--list-cmds" and attached:
            return None, pairs
        if name in _GLOBAL_VALUED and (attached and name.startswith("--") or i + 1 < len(words)):
            if not attached:
                value, i = words[i + 1], i + 1
            pairs.append((name, value))
        elif words[i] not in _GLOBAL_FLAGS and not (name == "--exec-path" and attached):
            raise Unreadable(f"`git {words[i]}` is not an option of git this check reads")
        i += 1
    return (i if i < len(words) else None), pairs


def _fold(name: str) -> str:
    """A ref name as a case-blind, normalising disk stores it."""
    return unicodedata.normalize("NFC", _REF_HOME.sub("", name)).casefold()


def moved_names(verb: str, args: list, repo: Optional[str]) -> set:
    """The names (folded) a step that is not a commit or a route points somewhere else:
    `branch -f feat x`, `worktree add -B feat`, `fetch . x:feat`, `branch -u x` (UPSTREAM)."""
    plain = [word for word in args if not word.startswith("-")]
    names: set = set()
    if verb == "config" and re.search(r"alias|include", " ".join(args), re.I):
        return {ALIASED}
    if verb in _MOVER_ALL:
        names |= set(plain)
        if verb == "branch" and any(word.startswith(("-u", "--set-upstream", "--unset-upstream",
                                                     "-t", "--track")) for word in args):
            names.add(UPSTREAM)
    options = _MOVER_VALUE.get(verb, ())
    for i, word in enumerate(args if options else ()):
        name, attached, value = word.partition("=")
        name = _long(name) if verb != "worktree" else name
        if name in options:
            names.add(value if attached else "".join(args[i + 1:i + 2]))
        elif name[:2] in options and name[1] != "-":    # -b<name>
            names.add(word[2:].lstrip("="))
        elif name[:2] == "--" and name != "--" and verb != "worktree" and name[2:] not in _HEAD_LONG:
            names |= set(args[i + 1:i + 2])             # an option in no table: it may name one
    if verb in ("fetch", "push", "pull"):
        names |= {word.rpartition(":")[2] for word in plain if ":" in word and "://" not in word}
        source = plain[0] if plain else ""
        if verb == "fetch" and (source == "." or source.startswith("file:") or (source and os.path.isdir(
                os.path.join(repo or os.getcwd(), os.path.expanduser(source))))):
            names.add("FETCH_HEAD")
    return {_fold(name) for name in names if name}


def moved_word(verb: str, args: list, moved: set) -> Optional[str]:
    """The word of a route call that names something an earlier step of the same command
    moves (so git, asked now, would answer for the old place), or None."""
    if not moved:
        return None
    if _fold(UPSTREAM) in moved and (any("@{u" in word.lower() for word in args) or (
            verb in ("merge", "rebase", "pull") and not _words(verb, args)[0])):
        return UPSTREAM
    for word in args:
        folded = unicodedata.normalize("NFC", word).casefold()
        for name in moved:
            if re.search(r"(?:(?<![\w/-])|(?<=heads/)|(?<=tags/))%s(?![\w/-])" % re.escape(name), folded):
                return word
    return None


class _Rebased(str):
    """A commit a rebase builds the new HEAD from (its branch, its new base): right to read a
    later route against, never the commit a later word that names HEAD means. Never equal to
    the plain commit, so a checkout of the same commit keeps its own place."""

    def __eq__(self, other) -> bool:
        return isinstance(other, _Rebased) and str(self) == str(other)

    def __ne__(self, other) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return hash((_Rebased, str(self)))


class _Unborn(str):
    """The place of an unborn branch (`--orphan`): as text the empty tree, so a commit there is
    read as a root commit of the index. `trees`: the commits whose tree that index may hold (the
    start point; without one, every place HEAD held before the step, set by follow_head). Equal
    only to an unborn place with the same trees."""
    trees: Optional[tuple] = None

    def __eq__(self, other) -> bool:
        return isinstance(other, _Unborn) and self.trees == other.trees

    def __ne__(self, other) -> bool:
        return not self == other

    def __hash__(self) -> int:
        return hash((_Unborn, self.trees))


class _FromTree(list):
    """[tree, *paths] of a step that copies paths out of a tree into the index or the files
    (`checkout <tree> -- <paths>`, `reset <tree> -- <paths>`, `restore --source <tree>`); no
    paths: every path. The tree is MOVED when an earlier step of the command moves its name."""


def _long(word: str) -> str:
    """A long option of checkout, switch, reset or restore as git reads it: `--orph` is the one
    name it abbreviates. A word that abbreviates none or several comes back as typed, which no
    table holds."""
    if word[:2] != "--" or word[2:] in _HEAD_LONG:
        return word
    found = [name for name in _HEAD_LONG if name.startswith(word[2:])] if word[2:] else []
    return "--" + found[0] if len(found) == 1 else word


def _resolve(ask: Callable, name: str, branch: bool) -> Optional[tuple]:
    """The commits `name` may be now: as git reads the word and, for a word a checkout reads
    (`branch`), as the branch of that name, which checkout prefers to a tag of the same name.
    () when it names none, None when git does not answer."""
    out = []
    for word in ([f"refs/heads/{name}"] if branch and _BRANCH_NAME.fullmatch(name) else []) + [name]:
        found = ask("rev-parse", "--verify", "-q", "--end-of-options", word + "^{commit}")
        if found is None:
            return None
        if found.returncode == 0 and found.stdout.strip():
            out.append(found.stdout.strip())
    return tuple(dict.fromkeys(out))


def _ends(ask: Callable, name: str, branch: bool = False) -> tuple:
    """_resolve, or (MOVED,) when git cannot say or the name reads HEAD."""
    return (None if _HEAD_WORD.search(name) else _resolve(ask, name, branch)) or (MOVED,)


def _bisect_end(args: list, ask: Callable, moved: set) -> Optional[tuple]:
    """head_after for `git bisect`. Only `reset` is followed: to the commit it names, else to
    the start the repository records (BISECT_START; with no bisect in progress HEAD stays),
    unless a step that is not git ran first (it may have rewritten that record). The words that
    only print (log, view, visualize, terms) leave HEAD."""
    if args[:1] and args[0] in ("log", "view", "visualize", "terms"):
        return None
    if args[:1] != ["reset"] or len(args) > 2 or args[1:] and args[1].startswith("-"):
        return MOVED, True
    start = args[1] if args[1:] else ""
    if not start:
        found = None if RAN in moved else ask("rev-parse", "--absolute-git-dir")
        if found is None or found.returncode:
            return MOVED, True
        try:
            with open(os.path.join(found.stdout.rstrip("\n"), "BISECT_START"),
                      encoding="utf-8", errors="replace") as recorded:
                start = recorded.read().strip()
        except FileNotFoundError:
            return None
        except OSError:
            return MOVED, True
        if not start:
            return None
    return _ends(ask, start, True), False


def _route_end(verb: str, args: list, ask: Callable) -> Optional[tuple]:
    """head_after for a route. `git rebase [options] <upstream> <branch>` (`--root <branch>`)
    checks <branch> out and replays it, so the new HEAD holds the new base (--onto, else
    <upstream>) and what <branch> held: both are places. None without a branch word (HEAD stays
    the branch it is on). MOVED where the new base is not on the line, a step can rewrite the
    result, or the call takes HEAD back to where a stopped run began (--abort; for a rebase
    also --continue, --skip and --quit)."""
    try:
        plain, options, _ = _words(verb, args)
    except Unreadable:
        return None                                     # patches() refuses the call
    if verb in ("merge", "pull", "commit-tree"):        # they leave HEAD or only add to it
        return None
    if "--abort" in options or verb == "rebase" and {"--continue", "--skip", "--quit"} & set(options):
        return MOVED, False
    root = "--root" in options
    branch = plain[1 - root:]
    if verb != "rebase" or len(branch) != 1 or _CONTROL & set(options):
        return None
    base = options.get("--onto", [""])[-1] or ("" if root else plain[0])
    if not base or {"--keep-base", "-i", "--interactive", "-x", "--exec"} & set(options):
        return MOVED, False
    ends = _ends(ask, branch[0], True) + _ends(ask, base)
    return (MOVED if MOVED in ends else tuple(map(_Rebased, ends))), False


def _from_tree(verb: str, plain: list, paths: list, ask: Callable, where: Optional[str],
               moved: set) -> Optional[_FromTree]:
    """head_after for a checkout or reset that names paths (`paths`: the words after `--`).
    None when they come from the index or HEAD (no tree is named): nothing new."""
    if not plain:
        return None
    if not paths:                                       # no `--`: a tree first, or only paths
        found = ask("rev-parse", "--verify", "-q", "--end-of-options", plain[0] + "^{tree}")
        if found is not None and found.returncode and os.path.lexists(
                os.path.join(where or os.getcwd(), plain[0])):
            return None
    return _FromTree([MOVED if moved_word(verb, plain[:1], moved) else plain[0]] + plain[1:] + paths)


def head_after(verb: str, args: list, ask: Callable, where: Optional[str],
               moved: set = frozenset()):
    """(the commit HEAD is at once `git verb args` has run, a tuple of them for a rebase or a
    name that is a branch and a tag, or MOVED when git cannot say now; True when the index is
    not rewritten to match) for a step that moves HEAD: checkout, switch, reset, a rebase of a
    named branch, bisect, a route that returns to where it began, a write to HEAD itself. A
    _FromTree for a step that copies paths out of a tree. None for a step that does neither.
    `ask(*git_args)` runs git where the step runs (CompletedProcess, or None on a timeout);
    `moved`: the names earlier steps of the command move."""
    if verb == "bisect":
        return _bisect_end(args, ask, moved)
    if verb in ROUTE_VERBS:
        return _route_end(verb, args, ask)
    if verb in ("symbolic-ref", "update-ref"):
        return (MOVED, True) if "HEAD" in args else None
    if verb not in ("checkout", "switch", "reset", "restore"):
        return None
    plain, paths, source, kept, i = [], [], None, verb == "reset", 0
    new = picks = orphan = False
    while i < len(args):
        name, attached, value = args[i].partition("=")
        name = _long(name)
        if name == "--":
            paths = args[i + 1:]
            break
        if verb == "restore" and name[:2] == "-s" and name[2:]:     # -s<tree>
            name, attached, value = "-s", "=", args[i][2:]
        if name in _HEAD_NEW or name in _HEAD_VALUED:
            if not attached:
                i += 1
                value = "".join(args[i:i + 1])
            new, orphan = new or name in _HEAD_NEW, orphan or name == "--orphan"
            picks = picks or name in _HEAD_PATHS
            source = value if name in ("--source", "-s") else source
        elif name in _HEAD_PATHS:
            picks = True
        elif name == "-" or not name.startswith("-"):
            plain.append("@{-1}" if name == "-" else args[i])
        elif name not in _HEAD_FLAGS and not (name[:5] == "--no-" and "--" + name[5:] in _HEAD_FLAGS) \
                and not (name[1] != "-" and set(name[1:]) <= set("qfmdltNSW")):
            return MOVED, kept                          # a cluster or an option not in the table
        i += 1
    if verb == "restore":                               # without --source: from the index or HEAD
        return None if source is None else _FromTree(
            [MOVED if moved_word(verb, [source], moved) else source] + plain + paths)
    if orphan:                                          # an unborn branch: a commit there is a
        unborn = _Unborn(EMPTY_TREE)                    # root commit of the index, which a start
        if plain:                                       # point rewrites
            unborn.trees = (MOVED,) if moved_word(verb, plain[:1], moved) else _ends(ask, plain[0], True)
        return unborn, True
    if verb != "switch" and (picks or paths or (len(plain) > 1 and not new)):
        return _from_tree(verb, plain, paths, ask, where, moved)
    if not plain or len(plain) > 1 and not new:         # nothing named
        return None
    target = plain[0]                                   # with -b <new>: its start point
    found = _resolve(ask, target, verb != "reset")
    if found:
        return (found if len(found) > 1 else found[0]), kept
    if found is not None and not new and verb != "switch" and os.path.lexists(
            os.path.join(where or os.getcwd(), target)):
        return None                                     # `git checkout <file>`, `git reset <file>`
    return MOVED, kept


def follow_head(verb: str, call, state: list, ask: Callable) -> None:
    """Carry HEAD through the steps of one command. `state` is read_commit's: the names earlier
    steps move, then `heads`, which maps a git directory to (index kept, [(a commit HEAD may be
    at after the steps so far, the commit `-` then names)]). A step that moves HEAD only adds
    places: the shell may not run it (after `&&` or `||`, in an `if`, a loop, a function, a
    here-document that is only printed), so no earlier place is dropped, and HEAD as the hook
    finds it stays one of them (patches, commit_ranges). A step whose git directory cannot be
    read now (`-C $PWD`, a directory the command makes) may run anywhere: its places, all MOVED,
    count for every directory. A route gets every place as `call.head`; a commit gets them only
    when the index was kept (reset --soft: it commits the index against the new HEAD). A step
    that copies paths out of a tree becomes a `paths` call with its places, for patches."""
    moved, heads = state[0], state[1]
    reads = verb == "commit" or verb in ROUTE_VERBS
    if verb == "commit" and not heads:
        return
    found = ask("rev-parse", "--absolute-git-dir")
    key = _same_directory(found.stdout.strip()) if found is not None and found.returncode == 0 else None
    kept, seen = heads.get(key, (False, []))
    if key is not None and None in heads:
        kept, seen = kept or heads[None][0], seen + [place for place in heads[None][1] if place not in seen]
    if reads and seen and verb == "commit" and kept:
        call.head = tuple(seen)
    elif reads and seen and verb != "commit":           # a route onto an unborn branch: not read
        call.head = tuple((MOVED if isinstance(head, _Unborn) else head, prior) for head, prior in seen)
    after = None if verb == "commit" else head_after(verb, call.args, ask, call.repo, moved)
    if isinstance(after, _FromTree):
        call.verb, call.args, call.head = "paths", list(after), tuple(seen) or None
    if not after or isinstance(after, _FromTree):
        return
    ends = after[0] if isinstance(after[0], tuple) else (after[0],)
    if key is None:
        ends = (MOVED,)
    for end in ends:
        if isinstance(end, _Unborn) and end.trees is None:
            end.trees = tuple(tree for head, _ in seen for tree in getattr(head, "trees", (head,)))
    if verb == "reset":                                 # `-` names what it named before
        priors = [None] + [prior for _, prior in seen]
    else:                                               # a checkout: `-` names where HEAD was
        was = ask("rev-parse", "--verify", "-q", "HEAD^{commit}")
        real = was.stdout.strip() if was is not None and was.returncode == 0 else ""
        priors = [real or MOVED] + [head for head, _ in seen]
    new = [(end, prior) for end in ends for prior in priors]
    heads[key] = (kept or after[1], seen + [pair for pair in dict.fromkeys(new) if pair not in seen])


def shell_step(words: list, base: Optional[str], dirs: list, state: list) -> Optional[str]:
    """The directory the steps after `words` (one command of the script) run in: `cd <dir>` and
    `pushd <dir>` move there, `popd` goes back to where its pushd left (`dirs`). It also notes
    in `state` (follow_head's) what the follower cannot see otherwise: a step that is not git
    (RAN: it may rewrite what a later `git bisect reset` reads), and a git call whose words stop
    inside a substitution before its verb (`git -C $(pwd) checkout x`), whose HEAD-moving verb,
    read as the next plain step, leaves every place MOVED."""
    moved, heads, first = state[0], state[1], words[0]
    if first in ("cd", "pushd") and len(words) > 1:
        dirs += [base] if first == "pushd" else []
        return os.path.normpath(os.path.join(base or os.getcwd(), os.path.expanduser(words[1])))
    if first == "popd":
        return dirs.pop() if dirs else base
    if os.path.basename(first).lower() == "git":
        try:
            if git_globals(words)[0] is None and words[-1].endswith("$"):
                moved.add(OPEN)
        except Unreadable:
            pass                                        # read_commit refuses the call itself
    elif first in _HEAD_MOVERS and OPEN in moved:
        heads[None] = (heads.get(None, (False, []))[0], [(MOVED, MOVED)])
    elif first != "export" and not (len(words) == 1 and re.search(r"\s", first)):
        moved.add(RAN)
    return base


def _same_directory(path: str):
    """One key per git directory however the command spells it (letter case on a case-blind
    disk, a symlink): the directory's identity, or its text when it cannot be read."""
    try:
        found = os.stat(path)
    except OSError:
        return path
    return found.st_dev, found.st_ino


def head_of(call, default: bool = False) -> str:
    """HEAD as `call` will find it: the commit an earlier step of the command leaves it at.
    Unreadable when that step moves it where git cannot say ahead, or (`default`) when the call
    falls back on the upstream of a branch HEAD is not on yet."""
    head = (getattr(call, "head", None) or (None,))[0]
    if head == MOVED or (head and default):
        raise Unreadable("an earlier step of the same command moves HEAD, and this step reads "
                         "HEAD or its upstream (run this step as its own command)")
    return UPSTREAM if default else head or "HEAD"


def _at_head(call, words: list) -> list:
    """`words` (revisions) as they read once HEAD has moved: HEAD, @ and an open range end
    become the commit it moves to and `-` the commit it leaves; a word that reads HEAD any
    other way (ORIG_HEAD, @{u}, a reflog entry) is refused."""
    head, prior = getattr(call, "head", None) or (None, None)
    out = []
    for word in words if head else ():
        if word == "@{-1}" and prior == MOVED:          # `-` after a step git cannot follow
            head_of(call, default=True)
        word = prior if word == "@{-1}" and prior else word
        if head != MOVED:
            named = _HEAD_NAME.sub(head, word)
            named = (head if named.startswith("..") else "") + named + (head if named.endswith("..") else "")
            if named != word and isinstance(head, _Rebased):
                raise Unreadable("an earlier `git rebase` of the same command rewrites HEAD, and "
                                 "this step names HEAD (run this step as its own command)")
            word = named
        if _HEAD_WORD.search(word):
            head_of(call, default=True)
        out.append(word)
    return out if head else words


def refuse_moved(verb: str, args: list, moved: set) -> None:
    """Unreadable when a route call names something an earlier step of the command moves."""
    word = moved_word(verb, args, moved) if verb != "commit" else None
    if word:
        raise Unreadable(f"`{word}` in `git {verb}` is a name an earlier step of the same command "
                         "moves, and git can only be asked where it points now. Run this step as "
                         "its own command")


def alias(verb: str, run: Callable) -> Optional[str]:
    """What git expands `verb` to when it is an alias, else None. `run(*git_args)` runs git where
    the call runs (CompletedProcess, or None on a timeout); Unreadable when git cannot say."""
    result = run("config", "--get", f"alias.{verb}")
    if result is None or result.returncode not in (0, 1):
        raise Unreadable(f"git could not say whether `{verb}` is an alias")
    return result.stdout.strip() if result.returncode == 0 else None


def _words(verb: str, args: list) -> tuple:
    """(positional words, {option: [values]}, the file a `<` redirect feeds) for one call. A
    short cluster is read letter by letter (`-qm text`). Unreadable for a word in no table."""
    flags, valued, short_flags, short_valued = _OPTIONS[verb]
    plain, options, stdin, i = [], {}, None, 0

    def value_of(attached: str) -> str:
        nonlocal i
        if attached or i + 1 >= len(args):
            return attached
        i += 1
        return args[i]

    while i < len(args):
        word, redirect = args[i], _REDIRECT.match(args[i])
        if redirect:
            target = redirect.group(3) or (args[i + 1] if i + 1 < len(args) else "")
            i += 1 if redirect.group(3) else 2
            if redirect.group(2) == "<" and redirect.group(1) in ("", "0"):
                stdin = target
            continue
        if word == "--":
            plain += args[i + 1:]
            break
        if word.startswith("--"):
            name, eq, value = word.partition("=")
            bare = name[2:]
            if bare in valued:
                value = value if eq else value_of("")
            elif not (bare in flags or bare[:3] == "no-" and bare[3:] in flags | valued):
                raise Unreadable(f"`{name}` is not an option of `git {verb}` this check reads "
                                 "(write option names in full)")
            options.setdefault(name, []).append(value)
        elif word.startswith("-") and word != "-":
            for at, letter in enumerate(word[1:], 2):
                if letter in short_valued:
                    options.setdefault("-" + letter, []).append(value_of(word[at:]))
                    break
                if letter not in short_flags and not (letter.isdigit() and at > 2):
                    raise Unreadable(f"`{word}` is not an option of `git {verb}` this check reads")
                options.setdefault("-" + letter, []).append("")
                if letter == "S":                       # -S<keyid>: the rest is its value
                    break
        else:
            plain.append("@{-1}" if word == "-" and verb != "am" else word)
        i += 1
    return plain, options, stdin


def continues(verb: str, args: list) -> bool:
    """True when the call commits the resolved index (--continue; am also --resolved / -r),
    read from its parsed options, so an option's value never counts."""
    if verb not in ROUTE_VERBS:
        return False
    try:
        names = set(_words(verb, args)[1])
    except Unreadable:
        return False                                    # patches() refuses the call
    return verb in ROUTE_VERBS and ("--continue" in names
                                    or (verb == "am" and bool(names & {"--resolved", "-r"})))


def patches(call, run: Callable) -> list:
    """The -U0 patches of what `call` (a Commit whose verb is in ROUTE_VERBS) brings in; [] when
    it brings nothing to read ahead. `run(call, *git_args)` runs git in the call's repo and
    environment and raises when git fails. Unreadable when the content is not there to read.
    Read against HEAD as the hook finds it and, added to that, against every place an earlier
    step of the command may leave HEAD (follow_head): a place can add patches, never drop any."""
    seen, out = getattr(call, "head", None) or (), []
    read = _copied if call.verb == "paths" else _brought
    try:
        for place in (*seen, None):                     # a moved place first: its refusal says why
            call.head = place
            out += [patch for patch in read(call, run) if patch not in out]
    finally:
        call.head = seen or None
    return out


def _copied(call, run: Callable) -> list:
    """patches() for a `paths` call (follow_head: call.args is a tree, then the paths a step
    copies out of it) and one place of HEAD: what a later commit of the command takes from that
    tree, the whole content of the paths where HEAD is not known. Unreadable when the tree or a
    path is not there to read as git will."""
    tree, paths = call.args[0], call.args[1:]
    word = next((word for word in paths if _SHELL_EXPANDS.search(word)), None)
    if word:
        raise Unreadable(f"`{word}` in a step that copies paths out of a tree is expanded by the "
                         "shell at run time")
    place = (getattr(call, "head", None) or (None,))[0]
    tree = tree if tree == MOVED or place is None else _at_head(call, [tree])[0]
    found = None if tree == MOVED else call.ask(
        "rev-parse", "--verify", "-q", "--end-of-options", tree + "^{tree}")
    if found is None or found.returncode:
        raise Unreadable("an earlier step of the same command copies paths out of a tree git "
                         "cannot read now (run the commit as its own command)")
    return [_text(run(call, "diff", *_PATCH, "--end-of-options",
                      EMPTY_TREE if place == MOVED else place or "HEAD", tree, "--", *paths))]


def _brought(call, run: Callable) -> list:
    """patches() for one place of HEAD: `call.head` is None (HEAD as it is) or (HEAD, `-`)."""
    plain, options, stdin = _words(call.verb, call.args)
    if _CONTROL & set(options) or call.args == ["-h"]:  # -h alone: git prints its usage
        return []
    if call.verb != "am" and (call.verb != "pull" or plain[:1] == ["."]):
        plain = _at_head(call, plain)
        _at_head(call, options.get("--onto", []) + options.get("-p", []))
    if call.verb in ("cherry-pick", "revert"):
        if "--stdin" in options:
            raise Unreadable(f"`git {call.verb} --stdin` reads its commits at run time")
        reverse = ("-R", "--src-prefix=b/", "--dst-prefix=a/") if call.verb == "revert" else ()
        return [_log(call, run, plain, flags=(*reverse, "--no-walk"))] if plain else []
    if call.verb == "pull":
        return _pull(call, run, plain)
    if call.verb == "merge":
        targets, head = plain or [head_of(call, True)], head_of(call)
        if "--squash" in options:                       # only the net change is committed
            return [_text(run(call, "diff", *_PATCH, "--end-of-options", f"{head}...{target}"))
                    for target in targets]
        return [_log(call, run, targets, (head,))]
    if call.verb == "rebase":
        return _rebase(call, run, plain, options)
    if call.verb == "commit-tree":
        parent = (options.get("-p") or [EMPTY_TREE])[0]
        return [_text(run(call, "diff", *_PATCH, "--end-of-options", parent, f"{plain[0]}^{{tree}}"))
                ] if plain else []
    return _mailboxes(call, plain + ([stdin] if stdin else []))


def _log(call, run: Callable, revs: list, exclude: tuple = (), flags: tuple = ()) -> str:
    """Each commit's own patch (a merge against each parent), as the push scan reads them: the
    commits of `revs` not reachable from `exclude`. Revisions follow --end-of-options."""
    return _text(run(call, "log", "-p", "-m", "--format=", *flags, *_PATCH, "--end-of-options",
                     *revs, *(f"^{rev}" for rev in exclude)))


def _rebase(call, run: Callable, plain: list, options: dict) -> list:
    for step in options.get("-x", []) + options.get("--exec", []):
        if re.search(r"\bgit\b", step):
            raise Unreadable("`git rebase --exec` runs git for each commit, and what that commits "
                             "cannot be read ahead")
    editor = call.env.get("GIT_SEQUENCE_EDITOR")
    if {"-i", "--interactive"} & set(options) and editor is not None \
            and editor.strip() not in _INERT_EDITORS:
        raise Unreadable("`git rebase -i` with a scripted sequence editor can add steps that "
                         "cannot be read ahead")
    onto, root = options.get("--onto", [None])[-1], "--root" in options
    if root and not onto:
        return []
    branch = plain[1 - root:]                           # --root: the first word is the branch
    bases = _resolve(call.ask, branch[0], True) if branch else (head_of(call),)
    if not bases:
        raise Unreadable(f"git cannot say which commit `{branch[0]}` in `git rebase` is now")
    new = onto or (plain[0] if plain else head_of(call, True))
    return [_log(call, run, [new], (base,)) for base in bases]


def _pull(call, run: Callable, plain: list) -> list:
    """`git pull . <ref>` merges a local ref: read it like a merge. A local repository path is
    refused (its content is not fetched yet); a remote's content already lives there."""
    if not plain:
        return []
    source, refs = plain[0], [ref.split(":")[0].lstrip("+") for ref in plain[1:]]
    if source == ".":
        return [_log(call, run, refs or ["FETCH_HEAD"], (head_of(call),))]
    path = os.path.join(call.repo or os.getcwd(), os.path.expanduser(source))
    if source.startswith("file://") or os.path.isdir(path):
        raise Unreadable(f"`git pull` from the local repository {source} brings content not "
                         "fetched yet")
    return []


def _mailboxes(call, files: list) -> list:
    """The text of each patch file `git am` applies, plus every mail part decoded (base64,
    quoted-printable), as git mailinfo would. Paths resolve against the call's directory."""
    if not files:
        raise Unreadable("`git am` reads its patches from standard input")
    texts = []
    for name in files:
        path = os.path.join(call.repo or os.getcwd(), os.path.expanduser(name))
        if _STREAM.match(name) or _STREAM.match(os.path.realpath(path)):
            raise Unreadable(f"`git am` patch {name} is standard input")
        try:
            mode = os.stat(path).st_mode
            if stat.S_ISDIR(mode):
                raise Unreadable(f"`git am` reads a mailbox directory ({name})")
            if not stat.S_ISREG(mode):
                raise Unreadable(f"`git am` patch {name} is not a regular file")
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as error:
            raise Unreadable(f"`git am` patch {name} could not be read ({error.strerror})") from error
        texts.append(raw.decode("utf-8", "replace"))
        texts += _decoded_parts(raw)
    return texts


def _decoded_parts(raw: bytes) -> list:
    """Every non-multipart part of every mail in `raw` (an mbox or one mail), transfer-decoded."""
    out = []
    for chunk in re.split(rb"(?m)^From .*\n", raw):
        for part in email.message_from_bytes(chunk, policy=email.policy.compat32).walk():
            payload = None if part.is_multipart() else part.get_payload(decode=True)
            if payload:
                try:
                    out.append(payload.decode(part.get_content_charset() or "utf-8", "replace"))
                except LookupError:
                    out.append(payload.decode("utf-8", "replace"))
    return out


def _text(result) -> str:
    out = result.stdout
    return out.decode("utf-8", "replace") if isinstance(out, bytes) else out


# Commit options whose value is the next word (or, for a short letter, the rest of its cluster).
_COMMIT_VALUE_SHORT = "mFcCt"
_COMMIT_VALUE_LONG = {"--message", "--file", "--author", "--date", "--template", "--reuse-message",
                      "--reedit-message", "--fixup", "--squash", "--cleanup", "--trailer"}


def commit_ranges(call) -> Optional[list]:
    """The diff argument lists whose union holds what a `git commit` takes: the index
    (`--cached`) and, for a working-tree commit, the working tree against HEAD (an empty tree
    before the first commit), limited to its pathspecs. Each is read against HEAD as the hook
    finds it and again against every place a reset earlier in the command may leave HEAD
    (follow_head). None when git does not answer; Unreadable for a place git cannot name yet."""
    working_tree, paths = commit_scope(call.args)
    heads = list(dict.fromkeys(["HEAD"] + [place[0] for place in getattr(call, "head", None) or ()]))
    trees = [tree for head in heads for tree in getattr(head, "trees", ())]
    if MOVED in heads + trees:
        raise Unreadable("an earlier step of the same command moves HEAD where git cannot say "
                         "ahead (run this step as its own command)")
    ranges = [[EMPTY_TREE, tree] for tree in dict.fromkeys(trees)]  # an unborn branch: all of it
    for head in heads:
        ranges.append(["--cached"] + [head] * (head != "HEAD"))
        if working_tree:
            found = call.ask("rev-parse", "--verify", "-q", head)
            if found is None:
                return None
            ranges.append([EMPTY_TREE if found.returncode else head] + (["--", *paths] if paths else []))
    return ranges


def commit_scope(args: Optional[list]) -> tuple:
    """(working_tree, paths): whether the commit takes working-tree content (-a, a pathspec, -p,
    --pathspec-from-file, or args it could not read), and the pathspecs it names (None for every
    tracked file)."""
    if args is None:
        return True, None
    every, paths, i = False, [], 0
    while i < len(args):
        word = args[i]
        if word == "--":
            paths += args[i + 1:]
            break
        if word in ("--all", "--patch", "--interactive") or word.startswith("--pathspec-from-file"):
            every = True
            i += 1 if word == "--pathspec-from-file" else 0
        elif word.startswith("--"):
            i += 1 if word in _COMMIT_VALUE_LONG else 0
        elif word.startswith("-") and len(word) > 1:
            for position, letter in enumerate(word[1:], 1):
                if letter in "ap":
                    every = True
                elif letter in "Su":
                    break                               # -S<keyid>, -u<mode>: an attached value only
                elif letter in _COMMIT_VALUE_SHORT:
                    i += 0 if word[position + 1:] else 1
                    break
        else:
            paths.append(word)
        i += 1
    if every:
        return True, None
    return bool(paths), paths or None
