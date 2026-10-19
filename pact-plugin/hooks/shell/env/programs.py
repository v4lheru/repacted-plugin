"""
Location: pact-plugin/hooks/shell/env/programs.py
Summary: DATA ONLY: the env guard's program classes (readers, printers, egress, loaders,
         git options, grep and rg value options) and its message constants.
Used by: hooks/env_guard.py (the entry hook and façade) and the other shell.env
         modules per the imports below. Carved from env_guard.py at M2; every
         decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
Map: RULES, RULE_MARKS, rule_of, _ALLOW_OUTPUT, SUGGESTION, DUMP_MESSAGE, PARSE_MESSAGE, UNREADABLE_MESSAGE, GREP_EXCLUSION, GREP_HINT, READERS, OPERAND_READERS, SILENT_STDIN, PRINTERS, EGRESS_PROGRAMS, LOADERS, FIND_ACTIONS, GIT_SAFE, GIT_PATCH_OPTIONS, _GIT_VALUE_OPTIONS, NAME_CONSUMERS, _DUMP_RUNNERS, GREP_PROGRAMS, RG_PROGRAMS, _GREP_VALUE_OPTIONS, _RG_VALUE_OPTIONS, _FILTER_OPTIONS, WALK_LIMIT, WALK_SKIP_DIRS.
"""

from __future__ import annotations

import json

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})

SUGGESTION = ("use `env-get FILE KEY -- <cmd>` to pass a value to a command, "
              "or `env-get --keys FILE` to list its key names")
# A credential file (names.is_credential_path) is no KEY=value file: env-get does not
# apply, and nothing needs its bytes in the transcript.
CREDENTIAL_SUGGESTION = ("pass its path to the program that uses it (`ssh -i`, `kubectl "
                         "--kubeconfig`, `AWS_SHARED_CREDENTIALS_FILE`) instead of printing, "
                         "copying or sending it; a public key (*.pub) stays readable")
DUMP_MESSAGE = ("Blocked: this prints every environment variable, which can include "
                "credentials; print the one variable you need by name, or " + SUGGESTION + ".")
PARSE_MESSAGE = ("Blocked: this command mentions a .env-class name and could not be parsed "
                 "to check it; " + SUGGESTION + ".")
# Not tied to a mention of the class: text this guard cannot read may name a protected
# file in a way it cannot see either (a variable, a glob, a link).
UNREADABLE_MESSAGE = ("Blocked: this command holds {what}, which this check cannot be sure it "
                      "reads the way the shell will, so it cannot tell whether a .env-class "
                      "file would be printed, sent or staged; {instead}, then rerun.")

# Case-insensitive character classes: macOS reads .ENV as .env, but rg globs are case-sensitive.
GREP_EXCLUSION = "!.[eE][nN][vV] !.[eE][nN][vV].* !*.[eE][nN][vV] !.[eE][nN][vV][rR][cC] !.[nN][eE][tT][rR][cC]"
GREP_HINT = ("env_guard: .env-class files are excluded from Grep; list a file's key names "
             "with `env-get --keys FILE`.")

# Programs that print a file operand's contents (grep, rg, cut, git, find and the
# interpreters have their own rules). Any other program handed a protected path passes.
READERS = frozenset({
    "cat", "tac", "head", "tail", "less", "more", "most", "bat", "batcat", "nl", "sed", "gsed",
    "awk", "gawk", "mawk", "nawk", "strings", "xxd", "od", "hexdump", "hd", "base64", "base32",
    "uuencode", "sort", "uniq", "paste", "column", "fold", "fmt", "pr", "expand", "unexpand",
    "rev", "iconv", "jq", "yq", "diff", "sdiff", "diff3", "comm", "join", "zcat", "dd",
    "envsubst",
})
# Programs that turn a file operand into bytes on stdout or into a copy (tar cf - .env, gzip -c,
# openssl base64 -in): read like READERS, but never a heredoc's data consumers.
OPERAND_READERS = frozenset({"tar", "gzip", "gunzip", "bzip2", "xz", "zstd", "lz4", "zip",
                             "openssl"})
# Programs a protected stdin reaches without printing it: a count, a test, a read into names.
# Any other program handed a protected stdin may print it (tee, tr, a group, a script).
SILENT_STDIN = frozenset({"wc", "test", "[", "[[", "true", ":", "read", "mapfile", "readarray",
                          "done"})
PRINTERS = frozenset({"echo", "printf", "print"})
# Programs that send a file operand off the machine.
EGRESS_PROGRAMS = frozenset({"curl", "wget", "http", "https", "xh", "scp", "sftp", "rsync",
                             "nc", "ncat", "ftp", "pbcopy", "gh", "glab", "aws", "gsutil",
                             "gcloud", "az", "azcopy", "rclone", "s3cmd"})
# Option values a client reads to authenticate or verify, never sends: a credential file
# named there is in use, not leaving (scp -i ~/.ssh/id_rsa, curl --key client.key).
_SSH_IDENTITY = frozenset({"-i", "-F", "-o"})
IDENTITY_OPTIONS = {
    "scp": _SSH_IDENTITY, "sftp": _SSH_IDENTITY, "rsync": frozenset(),
    "curl": frozenset({"--key", "--cert", "-E", "--cacert", "--capath", "--proxy-key",
                       "--proxy-cert", "--proxy-cacert"}),
    "wget": frozenset({"--private-key", "--certificate", "--ca-certificate"}),
    "gcloud": frozenset({"--key-file", "--cred-file"}),
}
# Cloud copy subcommands: their last operand is the destination, a local file when the
# source is remote (a download, nothing leaves).
CLOUD_COPIES = {
    "aws": frozenset({"cp", "mv", "sync"}), "gsutil": frozenset({"cp", "mv", "rsync"}),
    "gcloud": frozenset({"cp", "mv", "rsync"}), "s3cmd": frozenset({"get", "sync"}),
    "rclone": frozenset({"copy", "copyto", "sync", "move", "moveto"}),
    "azcopy": frozenset({"copy", "cp", "sync"}),
}
# gh/glab api options whose value is sent as literal text, even when it starts with @.
LITERAL_FIELD_OPTIONS = frozenset({"-f", "--raw-field"})
LOADERS = frozenset({"source", "."})
# find lists names; these actions hand each match to a program or write it out.
FIND_ACTIONS = frozenset({"-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
                          "-fprintf", "-fls"})
GIT_SAFE = frozenset({"rm", "check-ignore", "ls-files", "status", "mv", "commit"})
GIT_PATCH_OPTIONS = frozenset({"-p", "--patch", "-u", "--patch-with-stat", "--patch-with-raw",
                               "-L", "--full-diff"})
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
# Words that turn a listed name into a read by another program.
NAME_CONSUMERS = frozenset({"xargs", "parallel", "read", "mapfile", "readarray"})
_DUMP_RUNNERS = frozenset({"rtk", "proxy", "command", "builtin", "exec", "nohup", "time", "sudo"})

GREP_PROGRAMS = frozenset({"grep", "egrep", "fgrep"})
RG_PROGRAMS = frozenset({"rg", "ag", "ack", "ack-grep"})
_GREP_VALUE_OPTIONS = frozenset({"-e", "-f", "-m", "-A", "-B", "-C", "-d", "-D", "--include",
                                 "--exclude", "--exclude-dir", "--regexp", "--file",
                                 "--max-count", "--context", "--after-context",
                                 "--before-context", "--label", "--directories", "--devices"})
_RG_VALUE_OPTIONS = frozenset({"-e", "-f", "-g", "--glob", "--iglob", "-t", "-T", "--type",
                               "--type-not", "-m", "-A", "-B", "-C", "-M", "-j", "-d",
                               "--max-depth", "-r", "--replace", "--type-add", "--pre",
                               "--sort", "--sortr", "--max-columns", "--encoding", "-E",
                               "--ignore", "--ignore-dir", "-G", "--file-search-regex"})
_FILTER_OPTIONS = frozenset({"--exclude", "--include", "--exclude-dir", "-g", "--glob",
                             "--iglob", "--ignore", "--ignore-dir"})
# A field tree of 66,232 entries passed the old 50,000 only as a false block; a walk takes
# about 0.2 s per 66,000 entries warm (1.4.1 probe) and 1.8 s cold.
WALK_LIMIT = 100_000
WALK_SKIP_DIRS = frozenset({".git", "node_modules"})


# ─── Rule ids ─────────────────────────────────────────────────────────────────────
# Every deny reason ends with `[rule: <id>]` (search._deny appends it); the dispatcher
# lifts the id into the journal row and docs/troubleshooting.md#<id-with-dashes>
# explains it. One id per deny shape, matched by the marker its message template carries
# (RULE_MARKS, first match wins); a message no marker matches gets no tail, and
# tests/test_shell_rule_ids.py fails on any deny without one.
RULES = {
    "env.send.file": "A command would send a .env-class or credential file off the machine.",
    "env.read.credential": "A command or tool would print or copy a credential file (ssh key, cloud or kube credential).",
    "env.read.operand": "A command names a .env-class file as an operand a reader would print.",
    "env.read.computed": "A command would print a file whose name the shell computes and that can be .env-class.",
    "env.read.loaded": "A command would print or send a value it loaded from a .env-class file.",
    "env.read.loop": "A shell loop would print lines it reads from a .env-class file.",
    "env.unreadable": "The command holds text this check cannot be sure it reads as the shell does.",
    "env.dump.environment": "The command prints every environment variable.",
    "env.parse_failed": "The command mentions a .env-class name and could not be parsed.",
    "env.search.unknown_root": "A recursive search starts from a path this check cannot resolve.",
    "env.search.too_wide": "A recursive search covers more entries than the walk limit.",
    "env.search.reaches_class": "A recursive search would read a .env-class file and print it.",
    "env.grep.glob_not_string": "Grep's glob is not a string, so the class cannot be excluded.",
    "env.git.stage": "What a git command would stage or commit could not be checked.",
    "env.git.unknown_dir": "The directory a git command works in is not known here.",
    "env.git.created_repo": "The command creates a repository whose contents are not checked.",
    "env.git.push_unverified": "What a push sends could not be verified.",
    "env.git.push_expanded": "A push names what it sends with a shell expansion.",
    "env.git.push_sends": "A push sends a .env-class or credential file.",
    "env.git.push_secret": "A push adds a line carrying a secret-shaped value.",
    "env.git.expanded": "A git command is named by a shell expansion or alias.",
}
RULE_MARKS = (
    ("env.send.file", "off the machine;"),
    ("env.read.computed", "(a name the shell computes"),
    ("env.read.loaded", "a value the same command loaded from"),
    ("env.read.loop", "this loop would print lines"),
    ("env.read.credential", "(a credential file)"),
    ("env.read.operand", "Blocked: this would print the contents of"),
    ("env.unreadable", "which this check cannot be sure it reads"),
    ("env.dump.environment", "this prints every environment variable"),
    ("env.parse_failed", "could not be parsed"),
    ("env.search.unknown_root", "this recursive search starts from a path that is not known"),
    ("env.search.too_wide", "this recursive search covers more than"),
    ("env.search.reaches_class", "this recursive search would read"),
    ("env.grep.glob_not_string", "Grep's glob is not a string"),
    ("env.git.unknown_dir", "would stage or commit (the directory"),
    ("env.git.stage", "could not check what this git command would stage or commit"),
    ("env.git.created_repo", "this command creates a repository"),
    ("env.git.push_unverified", "could not verify what this push sends"),
    ("env.git.push_expanded", "this push names what it sends with a shell expansion"),
    ("env.git.push_sends", "Blocked: this push sends"),
    ("env.git.push_secret", "Blocked: this push adds a"),
    ("env.git.expanded", "this git command is named by a shell expansion"),
)
assert {rule for rule, _ in RULE_MARKS} == set(RULES)


def rule_of(message: str) -> str | None:
    """The rule id of a deny message, by the first marker it carries, else None."""
    for rule, mark in RULE_MARKS:
        if mark in message:
            return rule
    return None
