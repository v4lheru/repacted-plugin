"""Team audience: share KB lessons a human accepted with a team, through a git repository
the team owns, after a review for the team and a push the human confirms at their own terminal.

Location: pact-plugin/bin/_distill/team.py. `repact-distill team stage|push` calls
add_parser() and cli(); tests call stage() and push() with fake terminals and local bare repos.

The team location is a local clone of the team's repository (profile team.repo, or --team-repo).
Lessons land in <clone>/lessons/<id>.md, in promote's format (bin/_distill/promote.py render).
stage: promote's source (sealed KB-GATE decisions by a named human; never the auto-accept bar),
checked against the team list too. The user reads each lesson at their own terminal and retypes
its id; the confirmed files are committed in the clone (those paths only). Nothing leaves the disk.
push: the outward act. Every commit between the upstream and HEAD is re-checked: no merge, each
commit only adds lessons/<id>.md files, each commit message passes the checks, and each file
byte-equals a fresh render of its sealed record from the user's KB, so a file or a record edited
after review, or a file no human accepted, refuses the whole push. No URL of the push remote may
carry a credential; a remote that is not a local path needs --i-reviewed <decider> (as promote);
the user retypes the head commit's short id at their own terminal (decide.terminal_confirms:
stdin and stderr). The verified commit itself is pushed to the upstream branch: no force, no
tags, no submodules.
What was checked is what is sent: the upstream must be an ancestor of the head, and
a real push reads the remote branch's tip first and refuses unless it is that upstream (the local
tracking ref can be moved); every git run ignores replace refs; the push runs no hook of the clone
and pins the transports to file, ssh and https; clone config (local and worktree scope, includes
followed) that runs code or moves the push refuses; each commit carries only tree, parent, author
and committer headers, in git's order, its idents pass the name and secret checks, its message
every check. After the retype the push URLs and config are read again and must be those checked;
the push goes to those URLs, not the remote name. Every other git run has no
fsmonitor program and no clone hook.
Team list: profile team.denylist or <config>/repacted/team-denylist.txt, one name per line, added
to the personal lists. It is required: missing, empty or unreadable refuses (fail closed).
Trust: as promote. The seal and the re-render stop sync clients and hand edits, not a process
running as the user; the terminal check stops an agent on the ordinary path, and a guard rule
refuses a pseudo-terminal wrapper around stage or push (process.pty_wrap).
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

import _repact_common as common
from _distill import postcheck as PC
from _distill import promote as P
from _distill import redact
from _kb import decide as KD
from _kb import gate as G
from shared import scrub

LANDING = ("lessons",)
TEAM_LIST = "team-denylist.txt"
SHORT = 12                       # the retyped commit id
MAX_PROBLEMS = 20
PUSH_TIMEOUT = 300
EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_REFUSED = 0, 1, 2, 3
_LESSON_PATH = re.compile(r"lessons/(\d{8}T\d{6}Z-[0-9a-f]{8})\.md", re.ASCII)
_HEADER = re.compile(r"^([a-z0-9-]+): (.*)$")
_SHA = re.compile(r"[0-9a-f]{64}", re.ASCII)
_GIT_SHA = re.compile(r"[0-9a-f]{40}([0-9a-f]{24})?", re.ASCII)
_TRANSPORT = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)(::|://)")
_TRANSPORTS = frozenset({"file", "ssh", "https", "git+ssh", "ssh+git"})
# Clone config that runs a program, hands a credential to one, or changes where or what the push
# sends (keys as `git config --list` prints them: section and name lower case).
_RISKY_CONFIG = re.compile(r"core\.(sshcommand|askpass|fsmonitor)|push\.pushoption|protocol\..*"
                           r"|http\..*|credential\.(.*\.)?helper|url\..*\.(insteadof|pushinsteadof)"
                           r"|remote\..*\.(receivepack|uploadpack|vcs|mirror|proxy)", re.DOTALL)
_CLONE_SCOPES = ("local", "worktree")
# On every push and remote read: no hook of the clone, no signing program, and only the file, ssh
# and https transports (a command-line -c outranks the clone's config).
_PINS = ("-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", "-c", "push.gpgSign=false",
         "-c", "protocol.allow=never", "-c", "protocol.file.allow=always",
         "-c", "protocol.ssh.allow=always", "-c", "protocol.https.allow=always",
         "-c", "protocol.ext.allow=never", "-c", "protocol.fd.allow=never")
# On every other git run (reads, and stage's add and commit): no fsmonitor program and no hook
# of the clone (a clone's core.fsmonitor would run in the push's diff-tree read).
_READ_PINS = ("-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}")
CLI = P.CLI
HELP = """Share KB lessons a human accepted with your team, through a git repository the team owns.
Point --team-repo (or profile team.repo) at your local clone of it.

stage: for each accepted lesson you read the exact text at your own terminal and retype its id;
the confirmed lessons are written to <clone>/lessons/<id>.md and committed there. Nothing is sent.
push: checks every commit that would leave (only added lesson files, each identical to what you
reviewed, every check passed again), shows them, and pushes after you retype the head commit's
short id at your own terminal. A remote that is not a local path also needs --i-reviewed <your
name>; a remote URL that carries a credential is refused.

The team list (profile team.denylist, or team-denylist.txt beside profile.json) is required: one
client, project or person name per line that must never reach the team repository. Start with
--dry-run: it writes and sends nothing."""
EXAMPLES = """examples:
  python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-distill" team stage --team-repo ~/src/team-kb --dry-run
  python3 "${CLAUDE_PLUGIN_ROOT}/bin/repact-distill" team push --team-repo ~/src/team-kb --by alice --i-reviewed alice"""


def _env(**extra: str) -> dict:
    """git's environment for this module: replace refs ignored, so a read sees what push sends."""
    return dict(os.environ, GIT_NO_REPLACE_OBJECTS="1", **extra)


def _git(root: Path, *args: str) -> str:
    """promote's git runner, decoding UTF-8 strictly whatever the locale (paths and lesson text),
    with _READ_PINS."""
    env = _env(GIT_TERMINAL_PROMPT="0")
    try:
        done = subprocess.run(["git", *_READ_PINS, "-C", str(root), *args], capture_output=True,
                              encoding="utf-8", timeout=P.GIT_TIMEOUT, env=env, check=False)
    except UnicodeDecodeError:
        raise RuntimeError(f"git {args[0]} printed text that is not UTF-8") from None
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return done.stdout


class Refused(P.Refused):
    """A rule stops the run before anything is written or sent."""

    def render(self) -> str:
        return render_refusal(self)


def render_refusal(exc: P.Refused) -> str:
    return f"repact-distill team: refused: {exc.what}.\nRule: {exc.rule}\nInstead: {exc.instead}"


# --- the team list -------------------------------------------------------------------------------

def team_list_path(profile: dict) -> Path:
    team = profile.get("team") if isinstance(profile.get("team"), dict) else {}
    named = team.get("denylist")
    if isinstance(named, str) and named.strip():
        return Path(named.strip()).expanduser()
    return common.profile_path().parent / TEAM_LIST


def team_lists(profile: dict) -> redact.Lists:
    """The personal lists plus the team list. Raises redact.ListsBroken when the team list is
    missing, unreadable or empty, or a personal list is broken (callers fail closed)."""
    personal = redact.load_lists()
    path = team_list_path(profile)
    if not path.is_file():
        raise redact.ListsBroken("the team list is missing")
    try:
        names = scrub.load_denylist(path, strict=True)
    except (OSError, UnicodeDecodeError) as exc:
        raise redact.ListsBroken(f"the team list is unreadable ({type(exc).__name__})") from None
    if not names:
        raise redact.ListsBroken("the team list is empty")
    return redact.Lists(redact.STATE_LOADED, redact._norm(personal.literals + tuple(names)),
                        personal.words, f"team list {len(names)} entries")


def verdicts_for(profile: dict) -> Callable:
    """A promote.plan `verdicts` function: the R3 post-check over the personal and team lists."""
    def verdicts(candidates: list) -> list:
        try:
            lists, allow = team_lists(profile), PC.profile_allow_names()
        except redact.ListsBroken as exc:
            reason = f"team check unavailable: {exc}"
            return [(c, PC.Verdict(PC.REFUSE, (reason,))) for c in candidates]
        except Exception as exc:  # fail closed for the whole batch
            reason = f"team check unavailable ({type(exc).__name__})"
            return [(c, PC.Verdict(PC.REFUSE, (reason,))) for c in candidates]
        return [(c, PC.check_candidate(c, lists, allow)) for c in candidates]
    return verdicts


def _require_team_list(profile: dict) -> redact.Lists:
    try:
        return team_lists(profile)
    except redact.ListsBroken as exc:
        raise Refused(f"the team check cannot run: {exc}", "a lesson reaches the team repository "
                      "only after the team list (names that must never reach it) is checked",
                      f"put one name per line in {P.safe(str(team_list_path(profile)))} (or set "
                      "team.denylist in profile.json), then run again") from None


# --- the remote ----------------------------------------------------------------------------------

def carries_credential(url: str) -> bool:
    """True when a remote URL holds (or may hold) a password or token: a user:password part, a
    query or fragment, any detected secret, or a URL promote's display withholds. Fail closed."""
    url = url.strip()
    if not url or scrub.secret_kind(url) or scrub.credential_reason("remote", url):
        return True
    if P.display_url(url).startswith("<"):
        return True
    if P._SCHEME.match(url):
        try:
            parts = urlsplit(url)
        except ValueError:
            return True
        return ":" in parts.netloc.rpartition("@")[0] or bool(parts.query or parts.fragment)
    colon = url.rfind(":")
    cut = url.rfind("@", 0, colon) if colon != -1 else url.rfind("@")
    return cut != -1 and ":" in url[:cut]


def shown_url(url: str) -> str:
    """A remote URL for the terminal: a local path as it is, any other through promote's
    credential-stripping display."""
    return P.safe(url)[:P.MAX_URL_CHARS] if P.is_local_url(url) else P.display_url(url)


def _remote_urls(root: Path, name: str, git: Callable) -> list:
    try:
        urls = [url for flags in (("--all",), ("--push", "--all"))
                for url in git(root, "remote", "get-url", *flags, name).split("\n") if url.strip()]
    except (OSError, RuntimeError, subprocess.SubprocessError):
        urls = []
    if not urls:
        raise Refused(f"the URL of the team remote {P.safe(name)} could not be read", "a push "
                      "goes only to a remote whose every URL was shown and checked",
                      "fix `git remote -v` in the clone, then run again")
    return list(dict.fromkeys(url.strip() for url in urls))


def _push_urls(root: Path, name: str, git: Callable) -> list:
    """The URLs a push to the remote goes to (pushInsteadOf applied), in git's order."""
    urls = [url.strip() for url in git(root, "remote", "get-url", "--push", "--all", name).split("\n")]
    return list(dict.fromkeys(url for url in urls if url))


def remote_problems(root: Path, urls: list, git: Callable) -> list:
    """Why the push may not use this clone's remote setup: a transport other than file, ssh or
    https, or clone config that runs code or moves the push. Names keys, never values."""
    problems = []
    for url in urls:
        match = _TRANSPORT.match(url)
        if match and (match.group(2) == "::" or match.group(1).lower() not in _TRANSPORTS):
            problems.append(f"a remote URL uses the {P.safe(match.group(1))[:20]} transport: a team "
                            "push goes over file, ssh or https only")
    try:
        fields = git(root, "config", "--list", "--show-scope", "-z").split("\0")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return problems + ["the clone's git config could not be read"]
    risky = []
    for scope, entry in zip(fields[0::2], fields[1::2]):
        key = entry.split("\n", 1)[0]
        if scope in _CLONE_SCOPES and _RISKY_CONFIG.fullmatch(key) and key not in risky:
            risky.append(key)
    problems += [f"the clone's config sets {P.safe(key)[:80]}: it can run a program or move the push "
                 "during your push" for key in risky]
    return problems


def upstream(root: Path, git: Callable) -> tuple:
    """(remote name, destination ref, upstream sha) of the current branch."""
    try:
        branch = git(root, "symbolic-ref", "--short", "HEAD").strip()
        remote = git(root, "config", "--get", f"branch.{branch}.remote").strip()
        merge = git(root, "config", "--get", f"branch.{branch}.merge").strip()
        base = git(root, "rev-parse", "--verify", "--quiet", "@{upstream}").strip()
    except (OSError, RuntimeError, subprocess.SubprocessError):
        remote = merge = base = ""
    if not remote or remote == "." or not merge.startswith("refs/heads/") or not _GIT_SHA.fullmatch(base):
        raise Refused("the clone's current branch has no upstream on a remote", "a team push "
                      "sends the commits after the branch's upstream, to that upstream only",
                      "check out the team branch and set its upstream (git push -u, once, "
                      "yourself), then run again")
    return remote, merge, base


def _check_remote(root: Path, remote: str, reviewed: Optional[str], git: Callable) -> list:
    urls = _remote_urls(root, remote, git)
    if any(carries_credential(url) for url in urls):
        raise Refused(f"the team remote {P.safe(remote)} has a URL that carries a credential",
                      "a password or token in a remote URL sits in plain text in .git/config and "
                      "in git's output; a team push never uses one", "remove it with `git remote "
                      "set-url`, use a credential helper or an SSH key, then run again")
    shown = [shown_url(url) for url in urls]
    if not reviewed and any(not P.is_local_url(url) for url in urls):
        raise Refused(f"pushing to {', '.join(shown)}", "the team remote is not a local path; "
                      "the lessons will be shared with whoever can read it, so the human who "
                      "reviewed them is named", f'python3 "{CLI}" team push --team-repo '
                      f'"{P.safe(str(root))}" --by <your name> --i-reviewed <your name>')
    return shown


def _require_terminal(stdin, command: str) -> None:
    try:
        interactive = stdin.isatty() and sys.stderr.isatty()
    except (OSError, ValueError, AttributeError):
        interactive = False
    if not interactive:
        raise Refused(f"a team {command} without an interactive terminal", "every lesson and "
                      "every push is confirmed by a human at a terminal (stdin and stderr); the "
                      "check stops an agent on the ordinary path, and a guard rule refuses a "
                      "pseudo-terminal wrapper", f"ask the user to run, in their own terminal: "
                      f'python3 "{CLI}" team {command} --team-repo <clone> --by <your name> '
                      "(an agent may run --dry-run)")


# --- stage ---------------------------------------------------------------------------------------

def stage(kb: Path, repo: Path, profile: dict, *, dry_run: bool, ids: tuple = (),
          by: Optional[str] = None, reviewed: Optional[str] = None, stdin=None, out=None,
          moment: Optional[datetime.datetime] = None, git: Callable = _git) -> int:
    """Review each eligible lesson for the team and commit the confirmed ones in the clone."""
    stdin, out = stdin or sys.stdin, out or sys.stdout
    by, reviewed = P._checked_names(dry_run, by, reviewed)
    if any(not G.ID_RE.fullmatch(i) for i in ids):
        raise G.Refusal("usage", "--id takes a KB-GATE item id such as 20261017T120000Z-0123abcd")
    root = P.git_root(repo, git)
    _require_team_list(profile)
    remotes = P.shared_remotes(root, git)
    if not dry_run:
        if remotes and not reviewed:
            raise Refused(f"staging into {P.safe(str(root))}", f"the clone has a remote that is "
                          f"not a local path ({', '.join(remotes)})", f'python3 "{CLI}" team stage '
                          f'--team-repo "{P.safe(str(root))}" --by <your name> --i-reviewed <your name>')
        _require_terminal(stdin, "stage")
    at = G._iso(moment or G.now_utc())
    items, skipped = P.plan(Path(kb), root, tuple(ids), by, reviewed, at, landing=LANDING,
                            verdicts=verdicts_for(profile))
    P._report(items, skipped, remotes, reviewed, out)
    new = [item for item in items if item.status == "new"]
    if dry_run:
        for item in new:
            print(f"\n=== would write {item.relpath} ===\n{P.safe(item.text)}", file=out)
        return EXIT_OK
    written = []
    for item in new:
        notes = f"note: {', '.join(item.notes)} (check the text)\n" if item.notes else ""
        print(f"\n=== {item.relpath} ===\n{P.safe(item.text)}{notes}", file=sys.stderr)
        prompt = f"Type {item.id} to stage this lesson for the team, or press Enter to skip: "
        if not KD.terminal_confirms(item.id, prompt, stdin):
            print(f"- {item.id}: skipped", file=out)
        elif P.write_item(root, item, LANDING):
            written.append(item.relpath)
            print(f"- {item.id}: written to {item.relpath}", file=out)
        else:
            print(f"- {item.id}: {item.relpath} appeared meanwhile; not overwritten", file=out)
    return _commit(root, written, out, git)


def _commit(root: Path, paths: list, out, git: Callable) -> int:
    if not paths:
        print("staged 0 lessons; nothing committed", file=out)
        return EXIT_OK
    try:
        git(root, "add", "--", *paths)
        git(root, "commit", "--quiet", "--no-gpg-sign", "-m",
            f"Add {len(paths)} reviewed lesson{'s' if len(paths) > 1 else ''} from the knowledge base.",
            "--", *paths)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print(f"staged {len(paths)} lessons, but the commit failed; commit {'/'.join(LANDING)} "
              "yourself, then run team push", file=out)
        return EXIT_FAILED
    print(f"staged and committed {len(paths)} lessons; nothing was sent. Next: team push", file=out)
    return EXIT_OK


# --- push ----------------------------------------------------------------------------------------

def _header(text: str) -> dict:
    """The front matter of a lesson file as {key: value}; {} when it is not shaped like one."""
    lines = text.split("\n")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return {}
    found = {}
    for line in lines[1:lines.index("---", 1)]:
        match = _HEADER.match(line)
        if not match or match.group(1) in found:
            return {}
        found[match.group(1)] = match.group(2)
    return found


def verify_file(kb: Path, item_id: str, text: str, verdicts: Callable) -> Optional[str]:
    """Why a lesson file may not be pushed, or None: it must byte-equal a fresh render of the
    sealed human decision it names, and pass the checks again."""
    head = _header(text)
    by, at, reviewed = head.get("promoted-by"), head.get("promoted-at"), head.get("shared-remote-reviewed-by")
    if head.get("kb-gate-id") != item_id or not isinstance(by, str) or not G.DECIDER_RE.fullmatch(by) \
            or not isinstance(at, str) or not G.AT_RE.fullmatch(at) \
            or (reviewed is not None and not G.DECIDER_RE.fullmatch(reviewed)) \
            or not _SHA.fullmatch(head.get(P.SHA_KEY, "")):
        return "not a staged lesson file"
    records, _skipped = P.eligible_records(kb, (item_id,))
    if not records:
        return "no sealed human decision in your KB"
    record, fields = records[0], P.lesson_fields(records[0])
    if fields is None or P.header_problem(record):
        return "malformed record"
    sha = P.lesson_sha(fields)
    reasons, _notes = P.refusal_reasons(fields, verdicts([fields])[0][1])
    if reasons:
        return ", ".join(reasons)
    if sha != head[P.SHA_KEY] or P.render(record, fields, sha, by, at, reviewed) != text:
        return "edited after review"
    return None


def _outgoing(root: Path, base: str, head: str, git: Callable) -> tuple:
    """([commit sha], [problem]) for base..head; each problem names a commit, never its text."""
    commits = [c for c in git(root, "rev-list", "--reverse", f"{base}..{head}").split() if c]
    problems = [f"commit {c[:SHORT]} is a merge" for c in
                git(root, "rev-list", "--merges", f"{base}..{head}").split() if c]
    try:
        git(root, "merge-base", "--is-ancestor", base, head)
    except RuntimeError:
        problems.append(f"the upstream {base[:SHORT]} is not an ancestor of head {head[:SHORT]}")
    return commits, problems


def _commit_files(root: Path, commit: str, git: Callable) -> tuple:
    """([(item id, path)], [problem]) of one commit: only added lessons/<id>.md files pass."""
    fields = git(root, "diff-tree", "-r", "--root", "--no-commit-id", "--name-status",
                 "--no-renames", "-z", commit).split("\0")
    pairs = list(zip(fields[0::2], fields[1::2]))
    files, problems = [], []
    for status, path in pairs:
        match = _LESSON_PATH.fullmatch(path)
        if status != "A" or not match:
            problems.append(f"commit {commit[:SHORT]} {'adds' if status == 'A' else 'changes'} "
                            f"{P.safe(path)[:120]} ({status}): a team push carries only added lesson files")
        else:
            files.append((match.group(1), path))
    return files, problems


def _ident_kinds(ident: str, lists: redact.Lists) -> list:
    """An author or committer line: listed names and secrets (an email address is expected)."""
    kinds = ["secret"] if scrub.secret_kind(ident) or scrub.credential_reason("ident", ident) else []
    return kinds + (["denylist"] if redact._name_hit(ident, lists) else [])


def _misplaced_header(lines: list) -> Optional[str]:
    """The first header line out of git's own order, or None. The order is one `tree <sha>`, then
    `parent <sha>` lines, then one author and one committer, and nothing after them (a
    second tree or parent line, which git ignores, would go unscanned)."""
    if len(lines) < 3:
        return "a missing"
    wanted = ["tree"] + ["parent"] * (len(lines) - 3) + ["author", "committer"]
    for line, want in zip(lines, wanted):
        key, _, value = line.partition(" ")
        if key != want or (want in ("tree", "parent") and not _GIT_SHA.fullmatch(value)):
            return f"a {P.safe(key)[:40] or 'continuation'}"
    return None


def _commit_problem(root: Path, commit: str, lists: redact.Lists, git: Callable) -> Optional[str]:
    """Why one commit object may not leave: headers other than git's tree, parents, author and
    committer in that order (a signature, an encoding, any extra header), or a check that its
    idents or its message fail. The whole raw object is read, as push sends it."""
    raw = git(root, "cat-file", "commit", commit)
    headers, blank, message = raw.partition("\n\n")
    lines = headers.split("\n")
    misplaced = _misplaced_header(lines) if blank else "a missing"
    if misplaced:
        return (f"commit {commit[:SHORT]} has {misplaced} header: a team push carries only one "
                "tree, its parents, one author and one committer, in that order (commit unsigned)")
    found = {}                   # part -> kinds
    try:
        for line in lines[-2:]:
            key, _, value = line.partition(" ")
            if _ident_kinds(value, lists):
                found[key] = _ident_kinds(value, lists)
        kinds = redact.residual_kinds(message, lists)
        if scrub.credential_reason("commit message", message) and "secret" not in kinds:
            kinds.append("secret")
        if kinds:
            found["message"] = kinds
    except Exception as exc:  # fail closed: a check that cannot run refuses
        found = {"check error": [type(exc).__name__]}
    parts = [f"{part} ({', '.join(kinds)})" for part, kinds in found.items()]
    return f"commit {commit[:SHORT]} {'; '.join(parts)}" if parts else None


def plan_push(kb: Path, root: Path, base: str, head: str, profile: dict, git: Callable) -> tuple:
    """([(item id, path, text)], [problem]): what a push would send, and why it may not."""
    lists, verdicts = _require_team_list(profile), verdicts_for(profile)
    commits, problems = _outgoing(root, base, head, git)
    files = []
    for commit in commits:
        added, found = _commit_files(root, commit, git)
        problems += found
        problem = _commit_problem(root, commit, lists, git)
        problems += [problem] if problem else []
        files += added
    lessons = []
    for item_id, path in files:
        try:
            text = git(root, "show", f"{head}:{path}")
        except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeDecodeError):
            problems.append(f"{item_id}: unreadable")
            continue
        problem = "too large" if len(text) > P.MAX_RECORD_BYTES else verify_file(kb, item_id, text, verdicts)
        if problem:
            problems.append(f"{item_id}: {problem}")
        lessons.append((item_id, path, text))
    return commits, lessons, problems


def push(kb: Path, repo: Path, profile: dict, *, dry_run: bool, by: Optional[str] = None,
         reviewed: Optional[str] = None, stdin=None, out=None, git: Callable = _git,
         send: Optional[Callable] = None, tip: Optional[Callable] = None) -> int:
    """Re-check every outgoing commit, show it, and push it after the user's terminal proof.
    `send(root, url, refspec)` pushes to one checked URL and `tip(root, url, dest)` reads the
    remote branch's sha ("" when absent); tests pass fakes for a remote that is not a local path."""
    stdin, out = stdin or sys.stdin, out or sys.stdout
    by, reviewed = P._checked_names(dry_run, by, reviewed)
    root = P.git_root(repo, git)
    remote, dest, base = upstream(root, git)
    if dry_run:
        shown = _shown_for_dry_run(root, remote, git)
    else:
        shown = _check_remote(root, remote, reviewed, git)
        _require_terminal(stdin, "push")
    try:
        checked = _remote_state(root, remote, git)
        urls = checked[0]
        head = git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
        commits, lessons, problems = plan_push(Path(kb), root, base, head, profile, git)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        raise Refused("the outgoing commits could not be read", "a push sends only commits that "
                      "were read and checked", "fix the clone (git status, git log), then run "
                      "again") from None
    print(f"remote: {P.safe(remote)} ({', '.join(shown)}) -> {P.safe(dest)}", file=out)
    print(f"outgoing: {len(commits)} commit(s), {len(lessons)} lesson file(s); head {head[:SHORT]}",
          file=out)
    problems = remote_problems(root, urls, git) + problems
    if problems:
        for problem in problems[:MAX_PROBLEMS]:
            print(f"- {problem}", file=out)
        raise Refused(f"a push with {len(problems)} problem(s)", "a team push carries only lesson "
                      "files that are exactly what a human reviewed and that pass every check again",
                      "fix or drop the commits listed above (git reset or git revert in the clone), "
                      "then run team push again")
    if not commits:
        print("nothing to push", file=out)
        return EXIT_OK
    if dry_run:
        print(f"remote tip: not checked (a dry run never contacts the remote); a real push "
              f"refuses unless it is {base[:SHORT]}", file=out)
    else:
        _require_tip(root, urls, dest, base, tip or _remote_tip)
    for item_id, path, text in lessons:
        print(f"\n=== {path} ===\n{P.safe(text)}", file=out if dry_run else sys.stderr)
    if dry_run:
        print("\ndry run: nothing was sent; a real push needs your terminal and the head id", file=out)
        return EXIT_OK
    prompt = (f"Type {head[:SHORT]} to push {len(lessons)} lesson(s) to {', '.join(shown)}, "
              "or press Enter to cancel: ")
    if not KD.terminal_confirms(head[:SHORT], prompt, stdin):
        print("push cancelled; nothing was sent", file=out)
        return EXIT_OK
    _require_unchanged(root, remote, checked, git)
    for url in urls:
        code = (send or _send)(root, url, f"{head}:{dest}")
        if code != 0:
            print(f"git push to {shown_url(url)} failed (exit {code}); nothing else was done",
                  file=out)
            return EXIT_FAILED
    print("pushed", file=out)
    _move_tracking_ref(root, head, base, git, out)
    return EXIT_OK


def _remote_state(root: Path, remote: str, git: Callable) -> tuple:
    """(push URLs, the whole config listing): what the check reads and the send must still see."""
    return _push_urls(root, remote, git), git(root, "config", "--list", "--show-scope", "-z")


def _require_unchanged(root: Path, remote: str, checked: tuple, git: Callable) -> None:
    """Refuse unless the push URLs and the config after the retype are those that were checked,
    and pass the remote rules again (a URL or a receivepack set while the user
    confirmed moved the push)."""
    try:
        now = _remote_state(root, remote, git)
        problems = remote_problems(root, now[0], git)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        now, problems = None, ["unreadable"]
    if now != checked or problems:
        raise Refused("the clone's remote URLs or config changed while you confirmed", "a team "
                      "push goes only to the URLs, and with the config, that were checked and "
                      "shown", "look at `git remote -v` and `git config --list --show-origin` in "
                      "the clone, then run team push again")


def _move_tracking_ref(root: Path, head: str, base: str, git: Callable, out) -> None:
    """A push to a URL leaves the remote-tracking ref alone: move it, as a push by name does."""
    try:
        tracking = git(root, "rev-parse", "--symbolic-full-name", "@{upstream}").strip()
        git(root, "update-ref", "-m", "team push", tracking, head, base)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print("note: the remote-tracking ref was not moved; run `git fetch` in the clone", file=out)


def _shown_for_dry_run(root: Path, remote: str, git: Callable) -> list:
    """A dry run reports the remote rules instead of refusing on them."""
    urls = _remote_urls(root, remote, git)
    if any(carries_credential(url) for url in urls):
        return ["<a URL that carries a credential: a real push is refused>"]
    return [shown_url(url) for url in urls]


def _require_tip(root: Path, urls: list, dest: str, base: str, tip: Callable) -> None:
    """Refuse unless every push URL's branch is at the checked upstream: the commits between the
    remote's real tip and a moved local tracking ref were never checked."""
    for url in urls:
        try:
            found = tip(root, url, dest)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            found = None
        if found != base:
            where = "could not be read" if found is None else \
                f"is at {found[:SHORT] or 'nothing (no such branch)'}, not at {base[:SHORT]}"
            raise Refused(f"the remote branch {P.safe(dest)} at {shown_url(url)} {where}", "a "
                          "team push sends only the commits after the remote's real tip, and each "
                          "of them was checked", "run `git fetch` in the clone, look at what changed "
                          "(git log @{upstream}..HEAD), then run team push again")


def _remote_tip(root: Path, url: str, dest: str) -> str:
    """The remote branch's sha read from the push URL ("" when absent), on the user's terminal
    (git may ask for their credentials), with the push's pins."""
    done = subprocess.run(["git", *_PINS, "-C", str(root), "ls-remote", "--", url, dest],
                          stdout=subprocess.PIPE, encoding="utf-8", errors="replace",
                          timeout=PUSH_TIMEOUT, env=_env(), check=False)
    if done.returncode != 0:
        raise RuntimeError("git ls-remote failed")
    for line in done.stdout.split("\n"):
        sha, _, ref = line.partition("\t")
        if ref == dest and _GIT_SHA.fullmatch(sha):
            return sha
    return ""


def _send(root: Path, url: str, refspec: str) -> int:
    """The push itself to one checked URL (never the remote name, whose config is read again),
    on the user's terminal (git may ask for their credentials): no hook of the clone runs (an
    empty hooks path and --no-verify), replace refs are ignored, and the transports are pinned
    (_PINS)."""
    done = subprocess.run(["git", *_PINS, "-C", str(root), "push", "--no-verify", "--no-follow-tags",
                           "--recurse-submodules=no", "--", url, refspec],
                          timeout=PUSH_TIMEOUT, env=_env(), check=False)
    return done.returncode


# --- command line --------------------------------------------------------------------------------

def add_parser(sub) -> None:
    cmd = sub.add_parser("team", help="share accepted KB lessons with your team's repository, "
                         "reviewed and pushed at your own terminal", description=HELP,
                         epilog=EXAMPLES, formatter_class=argparse.RawDescriptionHelpFormatter)
    steps = cmd.add_subparsers(dest="team_command", required=True)
    for name, text in (("stage", "review lessons for the team and commit them in the clone"),
                       ("push", "check the outgoing commits and push them after your retype")):
        step = steps.add_parser(name, help=text, description=HELP, epilog=EXAMPLES,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
        step.add_argument("--team-repo", type=Path, help="your local clone of the team repository "
                          "(default: profile team.repo)")
        step.add_argument("--dry-run", action="store_true", help="show everything; write and send nothing")
        step.add_argument("--by", help="your name (required for a real run)")
        step.add_argument("--i-reviewed", dest="i_reviewed", metavar="DECIDER",
                          help="your name, required when the team remote is not a local path")
        if name == "stage":
            step.add_argument("--id", action="append", dest="ids", default=[],
                              help="only this KB-GATE item id (repeatable)")


def _team_repo(args, profile: dict) -> Path:
    team = profile.get("team") if isinstance(profile.get("team"), dict) else {}
    named = args.team_repo or (Path(team["repo"]).expanduser()
                               if isinstance(team.get("repo"), str) and team["repo"].strip() else None)
    if named is None:
        raise Refused("no team repository", "lessons are shared only through a clone of the "
                      "team's repository that you name", "pass --team-repo <your clone> or set "
                      "team.repo in profile.json")
    return Path(named)


def cli(args, kb_root: Callable, profile: Optional[dict] = None) -> int:
    """The `repact-distill team` entry: exit 0, 1 (the push failed), 2 (usage) or 3 (refused)."""
    profile = common.profile() if profile is None else profile
    try:
        kb = kb_root()
    except Exception:  # noqa: BLE001 - any KB seam failure means there is nothing to read
        print(render_refusal(Refused("team with no knowledge folder", "lessons come from the "
              "KB-GATE decisions in paths.kb", "set paths.kb in profile.json (see /repacted:init)")),
              file=sys.stderr)
        return EXIT_REFUSED
    try:
        repo = _team_repo(args, profile)
        if args.team_command == "stage":
            return stage(kb, repo, profile, dry_run=args.dry_run, ids=tuple(args.ids), by=args.by,
                         reviewed=args.i_reviewed)
        return push(kb, repo, profile, dry_run=args.dry_run, by=args.by, reviewed=args.i_reviewed)
    except P.Refused as exc:
        print(render_refusal(exc), file=sys.stderr)
        return EXIT_REFUSED
    except G.Refusal as exc:
        print(f"repact-distill team: {exc}\n\n{EXAMPLES}", file=sys.stderr)
        return EXIT_USAGE
