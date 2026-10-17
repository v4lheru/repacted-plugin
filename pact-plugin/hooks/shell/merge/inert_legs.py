"""
Location: pact-plugin/hooks/shell/merge/inert_legs.py
Summary: Carrier 10: read-verb values, wrapper grammar, inert positional and help legs.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: _SHELL_STRING_EXECUTORS, _EXEC_WRAPPERS_RECURSE, _EXEC_WRAPPERS_COARSE, _HTTP_CLIENT_HEADS, _TOOL_HEADS, _ReadVerbSpec, _GIT_GREP_DENY_RE, _READ_VERB_SPECS, _MAX_WRAPPER_DEPTH, _WrapperGrammar, _WRAPPER_GRAMMAR, _wrapper_nested_command, _is_preserve_leg, _preserve_tail, _strip_quoted_positionals, _GIT_GLOBAL_VALUE_FLAGS, _GIT_GLOBAL_BOOL_FLAGS, _resolve_git_subcommand, _strip_read_verb_values, _maybe_strip_leg, _strip_inert_positional_legs, _is_inert_help_leg, _neutralize_inert_help_legs.
"""

from __future__ import annotations

import os
import re
from typing import Callable, NamedTuple, Optional

from shell.merge.arms import (_GH_API_PREFIX, _GH_PR_VALUE_TAKING_FLAGS, _INERT_HELP_EXTRA_VALUE_FLAGS)
from shell.merge.views import (_COMPOUND_OPS_RE, _FD_REDIRECT_RE, _PROCSUB_MARKER_RE, _has_command_substitution, _keep_carrier_value, _leg_command_word, _leg_token_spans, _mask_shell_quotes, _shell_tokenize, _slice_stripped_leg_spans, _strip_flag_values, _strip_inert_dq_value, _strip_quoted_strings, _strip_surrounding_quotes)

# Carrier 10's last check asks the classifier whether a masked leg still carries a bare danger
# token. classify.py registers _stripped_surface_danger here when it loads, so this module
# imports nothing above it (tests/contract/test_shell_package.py C5). Unwired, every leg is
# preserved: the fail-safe direction (nothing is stripped that the classifier never saw).
_surface_danger: Optional[Callable[[str], bool]] = None

# --- Inert positional strip (carrier 10): a leg with an unrecognised head has argv-inert quoted
# positionals, stripped unless the head runs a string (executor or modelled wrapper, recursed),
# is an http client, or the masked leg is itself dangerous ---

# (a) Shell-string executors and interpreters, matched by token equality on the basename.
_SHELL_STRING_EXECUTORS = re.compile(
    r"^(?:"
    r"sh|bash|zsh|dash|ash|fish|csh|tcsh"                # POSIX + common shells
    r"|ksh(?:93)?|rksh|mksh"                             # ksh family (ksh93 != ksh)
    r"|hush|lash|msh"                                   # busybox exec-shell applets (run a -c string)
    r"|eval|expect|watch"                               # string runners
    r"|ssh|rsh|remsh|slogin|su"                         # remote / user-switch (-c / a command)
    r"|python[0-9.]*|perl[0-9.]*|ruby[0-9.]*|nodejs|node|php|awk|gawk"  # interpreters (-c/-e/-r)
    r")$"
)

# (a) Modelled exec wrappers whose nested command's quoted arg executes. RECURSE: the nested
# command is found and checked; COARSE: preserved whole.
_EXEC_WRAPPERS_RECURSE = frozenset({
    "nohup", "setsid", "nice", "doas", "sudo", "timeout", "xargs", "env",
    # Nameable exec prefixes, all recursed, so `<prefix> bash -c "x"` stays caught.
    "time", "exec", "command", "chrt", "taskset", "ionice", "unbuffer",
    "proxychains", "torsocks", "catchsegv", "nocache", "eatmydata", "rlwrap",
    # busybox (applet is token 1) and stdbuf recurse.
    "busybox", "stdbuf",
})
_EXEC_WRAPPERS_COARSE = frozenset({
    # Coarse members: the nested-command boundary is not reliably skippable (find -exec, flock -c).
    "find", "flock",
})

# (b) http clients: the target is a quoted URL that masking blanks, so preserve.
_HTTP_CLIENT_HEADS = frozenset({"curl", "wget"})

# git/gh/glab legs are preserved and left to their own carriers and the danger floor.
_TOOL_HEADS = frozenset({"git", "gh", "glab"})


# --- Read-verb value strip: git/gh read verbs (log, show, shortlog, grep, issue/pr list, search)
# never execute their filter values, so those values are stripped. Keys are full verb paths
# (exact tokens); the verb set is closed ---

class _ReadVerbSpec(NamedTuple):
    """Per-(tool, verb…) read-verb strip spec for _strip_read_verb_values."""
    flag_arm: "re.Pattern | None"   # ONE capturing group: the flag(+separator) to keep;
                                    # value consumed via _strip_flag_values' 3 arms
    deny_re: "re.Pattern | None"    # executing-flag deny — ANY token match => whole-leg preserve
    strip_positionals: bool         # grep/search only: strip remaining quoted spans


# git grep's ONLY executing flag family: -O[<pager>] / --open-files-in-pager[=<pager>]
# (attached-only optional value, EXECUTES it). Matched against quote-stripped shlex
# tokens (shell semantics: a quoted '-O' still reaches git as the flag).
_GIT_GREP_DENY_RE = re.compile(r"(?<!\S)-[a-zA-Z]*O|(?<!\S)--open-files-in-pager(?:=|\b)")

_READ_VERB_SPECS: "dict[tuple[str, ...], _ReadVerbSpec]" = {
    ("git", "log"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--grep-reflog|--grep|--author|--committer)\s+"
            # bundled-cluster pickaxe short (#1195 OBS-A2): a dash cluster ENDING in the
            # uppercase pickaxe flag S/G (`-nS'…'`, `-wG'…'`). [SG] uppercase-only so it
            # never mis-binds `-s`/lowercase; the value is an inert pickaxe string.
            r"|(?<!\S)-[a-zA-Z]*[SG]\s*)"
        ),
        None, False,
    ),
    ("git", "show"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--grep|--author|--committer)\s+"
            r"|(?<!\S)-[a-zA-Z]*[SG]\s*)"
        ),
        None, False,
    ),
    # NO shorts on shortlog: its `-e` is the boolean `--email` (the cross-verb
    # collision that mandates per-verb keying).
    ("git", "shortlog"): _ReadVerbSpec(
        re.compile(r"((?<!\S)(?:--grep|--author|--committer)\s+)"),
        None, False,
    ),
    ("git", "grep"): _ReadVerbSpec(
        re.compile(r"((?<!\S)-[ef]\s*)"),
        _GIT_GREP_DENY_RE, True,
    ),
    ("gh", "issue", "list"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--search|--author|--app|--assignee|--label|--milestone"
            r"|--mention|--type|--state|--limit|--repo)\s+"
            r"|(?<!\S)-[SAalmsLR]\s*)"
        ),
        None, False,
    ),
    ("gh", "pr", "list"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--search|--author|--app|--assignee|--label|--base"
            r"|--head|--state|--limit|--repo)\s+"
            r"|(?<!\S)-[SAalBHsLR]\s*)"
        ),
        None, False,
    ),
    # gh search value flags are per subcommand; longest first.
    ("gh", "search", "prs"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--app|--assignee|--author|--base|--checks|--closed"
            r"|--commenter|--comments|--created|--head|--interactions|--involves"
            r"|--label|--language|--limit|--match|--mentions|--merged-at"
            r"|--milestone|--order|--owner|--project|--reactions|--repo"
            r"|--review-requested|--reviewed-by|--review|--sort|--state"
            r"|--team-mentions|--updated|--visibility)\s+"
            r"|(?<!\S)-[BHLR]\s*)"
        ),
        None, True,
    ),
    ("gh", "search", "issues"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--app|--assignee|--author|--closed|--commenter"
            r"|--comments|--created|--interactions|--involves|--label"
            r"|--language|--limit|--match|--mentions|--milestone|--order"
            r"|--owner|--project|--reactions|--repo|--sort|--state"
            r"|--team-mentions|--updated|--visibility)\s+"
            r"|(?<!\S)-[LR]\s*)"
        ),
        None, True,
    ),
    ("gh", "search", "commits"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--author-date|--author-email|--author-name|--author"
            r"|--committer-date|--committer-email|--committer-name|--committer"
            r"|--hash|--limit|--order|--owner|--parent|--repo|--sort|--tree"
            r"|--visibility)\s+"
            r"|(?<!\S)-[LR]\s*)"
        ),
        None, True,
    ),
    ("gh", "search", "repos"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--created|--followers|--forks|--good-first-issues"
            r"|--help-wanted-issues|--include-forks|--language|--license"
            r"|--limit|--match|--number-topics|--order|--owner|--size|--sort"
            r"|--stars|--topic|--updated|--visibility)\s+"
            r"|(?<!\S)-L\s*)"
        ),
        None, True,
    ),
    ("gh", "search", "code"): _ReadVerbSpec(
        re.compile(
            r"((?<!\S)(?:--extension|--filename|--language|--limit|--match"
            r"|--owner|--repo|--size)\s+"
            r"|(?<!\S)-[LR]\s*)"
        ),
        None, True,
    ),
}


# Bounded recursion depth for nested wrappers (`timeout nice bash -c …`). Beyond this →
# fail-safe PRESERVE. 3 covers realistic nesting; the guard prevents pathological recursion.
_MAX_WRAPPER_DEPTH = 3

class _WrapperGrammar(NamedTuple):
    """Per-wrapper own-arg grammar for the recursion walk."""
    bool_flags: "frozenset[str]"
    value_flags: "frozenset[str]"
    shell_flags: "frozenset[str]"
    positionals: int


_WRAPPER_GRAMMAR: "dict[str, _WrapperGrammar]" = {
    "nohup":   _WrapperGrammar(frozenset(), frozenset(), frozenset(), 0),
    "setsid":  _WrapperGrammar(frozenset({"-c", "-f", "-w", "--ctty", "--fork", "--wait"}), frozenset(), frozenset(), 0),
    "nice":    _WrapperGrammar(frozenset(), frozenset({"-n", "--adjustment"}), frozenset(), 0),
    "doas":    _WrapperGrammar(frozenset({"-L", "-n"}), frozenset({"-C", "-u"}), frozenset({"-s"}), 0),
    "timeout": _WrapperGrammar(frozenset({"--preserve-status", "--foreground", "-v", "--verbose"}), frozenset({"-s", "--signal", "-k", "--kill-after"}), frozenset(), 1),
    "xargs":   _WrapperGrammar(frozenset({"-0", "--null", "-r", "--no-run-if-empty", "-t", "--verbose", "-p", "--interactive", "-x", "--exit", "-o", "--open-tty"}), frozenset({"-I", "-i", "-n", "--max-args", "-L", "--max-lines", "-P", "--max-procs", "-s", "--max-chars", "-d", "--delimiter", "-E", "-e", "-a", "--arg-file"}), frozenset(), 0),
    "env":     _WrapperGrammar(frozenset({"-i", "--ignore-environment", "-0", "--null", "-v"}), frozenset({"-u", "--unset", "-C", "--chdir"}), frozenset({"-S", "--split-string"}), 0),
    # sudo: -i/-s/-e preserve; -h omitted (help vs host) so it preserves.
    "sudo":    _WrapperGrammar(frozenset({"-A", "--askpass", "-B", "--bell", "-b", "--background", "-E", "--preserve-env", "-H", "--set-home", "-K", "--remove-timestamp", "-k", "--reset-timestamp", "-l", "--list", "-n", "--non-interactive", "-P", "--preserve-groups", "-S", "--stdin", "-V", "--version", "-v", "--validate"}), frozenset({"-C", "--close-from", "-D", "--chdir", "-g", "--group", "-p", "--prompt", "-R", "--chroot", "-r", "--role", "-T", "--command-timeout", "-t", "--type", "-U", "--other-user", "-u", "--user"}), frozenset({"-i", "--login", "-s", "--shell", "-e", "--edit"}), 0),
    # Nameable exec prefixes; unlisted flags are unrecognised and preserve.
    "time":        _WrapperGrammar(frozenset({"-a", "-p", "-q", "-v", "-V", "--append", "--portability", "--quiet", "--verbose", "--version", "--help"}), frozenset({"-o", "-f", "--output", "--format"}), frozenset(), 0),
    "exec":        _WrapperGrammar(frozenset({"-c", "-l"}), frozenset({"-a"}), frozenset(), 0),
    "command":     _WrapperGrammar(frozenset({"-p", "-V", "-v"}), frozenset(), frozenset(), 0),
    "chrt":        _WrapperGrammar(frozenset({"-f", "-b", "-o", "-r", "-i", "-d", "-R", "-m", "-a", "-v", "--fifo", "--batch", "--other", "--rr", "--idle", "--deadline", "--reset-on-fork", "--all-tasks", "--verbose"}), frozenset(), frozenset({"-p", "--pid"}), 1),
    "taskset":     _WrapperGrammar(frozenset({"-a", "--all-tasks", "-c", "--cpu-list"}), frozenset(), frozenset({"-p", "--pid"}), 1),
    "ionice":      _WrapperGrammar(frozenset({"-t", "--ignore"}), frozenset({"-c", "--class", "-n", "--classdata"}), frozenset({"-p", "--pid"}), 0),
    "unbuffer":    _WrapperGrammar(frozenset({"-p"}), frozenset(), frozenset(), 0),
    "proxychains": _WrapperGrammar(frozenset({"-q", "--quiet"}), frozenset({"-f", "--config"}), frozenset(), 0),
    "torsocks":    _WrapperGrammar(frozenset({"-h", "-q", "-i", "--isolate", "-H", "-V", "--version", "--help", "--shell"}), frozenset({"-d", "--debug", "-a", "--address", "-p", "--port", "-P", "--control-port", "-u", "--user", "-s", "--socks5"}), frozenset(), 0),
    "catchsegv":   _WrapperGrammar(frozenset(), frozenset(), frozenset(), 0),
    "nocache":     _WrapperGrammar(frozenset(), frozenset({"-n"}), frozenset(), 0),
    "eatmydata":   _WrapperGrammar(frozenset(), frozenset(), frozenset(), 0),
    "rlwrap":      _WrapperGrammar(frozenset({"-A", "-c", "-h", "-i", "-I", "-n", "-N", "-o", "-r", "-R", "-U", "-W", "-x", "-a", "-m", "-p", "--always-readline", "--complete-filenames", "--case-insensitive", "--no-children", "--one-shot", "--remember", "--quiet", "--help"}), frozenset({"-b", "-C", "-D", "-e", "-f", "-g", "-H", "-l", "-O", "-P", "-q", "-s", "-S", "-t", "-w", "-z", "--break-chars", "--command-name", "--history-no-dupes", "--forget-matching", "--file", "--history-filename", "--logfile", "--substitute-prompt", "--prompt", "--quote-characters", "--histsize", "--set-terminal-name", "--wait-before-prompt", "--filter"}), frozenset(), 0),
    # busybox has no own flags (applet is token 1); stdbuf has its -i/-o/-e values.
    "busybox":     _WrapperGrammar(frozenset(), frozenset(), frozenset(), 0),
    "stdbuf":      _WrapperGrammar(frozenset(), frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}), frozenset(), 0),
}


def _wrapper_nested_command(leg: str, head: str) -> "str | None":
    """For a RECURSE-set wrapper leg, return the ORIGINAL-string substring of the nested command
    (quotes intact) for ``_is_preserve_leg`` to recurse on, or None → FAIL-SAFE PRESERVE.
    """
    g = _WRAPPER_GRAMMAR[head]
    spans = _leg_token_spans(leg)    # spans[0] is the wrapper head (quoted or not)
    i, n = 1, len(spans)
    # Phase 1: the wrapper's own flags, stopping at the first non-flag or `--`.
    while i < n:
        start, end = spans[i]
        tok = leg[start:end]
        if tok == "--":
            i += 1
            break                                # end-of-options; rest is positionals + command
        if tok.startswith("-") and tok != "-":
            flagname = tok.split("=", 1)[0]
            if flagname in g.shell_flags:
                return None                      # shell-invocation form → preserve
            if flagname in g.value_flags:
                i += 1 if "=" in tok else 2      # --flag=value one token; --flag value two
                continue
            if flagname in g.bool_flags:
                i += 1
                continue
            # Attached short value (-oL) for a declared short value flag.
            if len(tok) > 2 and tok[1] != "-" and tok[:2] in g.value_flags:
                i += 1
                continue
            return None                          # unrecognized dash-flag → preserve
        break                                    # first non-flag token → positionals / command
    # PHASE 2 — the wrapper's leading POSITIONALS (timeout DURATION, chrt PRIORITY, taskset MASK).
    for _ in range(g.positionals):
        if i >= n:
            return None                          # ran out (positional missing) → preserve
        i += 1
    # PHASE 3 — env NAME=VALUE assignments (env has positionals=0; they precede the command).
    if head == "env":
        while i < n and re.match(r"^[A-Za-z_]\w*=", leg[spans[i][0]:spans[i][1]]):
            i += 1
    # PHASE 4 — the nested command head.
    if i >= n:
        return None                              # no command → preserve
    cmd_start, cmd_end = spans[i]
    if leg[cmd_start:cmd_end].startswith("-"):
        return None                              # a bare dash-flag is never a real command → preserve
    return leg[cmd_start:]                        # nested command head (original, quotes intact)


def _is_preserve_leg(leg: str, _depth: int = 0) -> bool:
    """True iff this single leg's quoted-arg values must be PRESERVED (not stripped) by the #1178
    general inert-default strip — see the catalog block above for clauses (a)/(b)/(c).
    """
    if _shell_tokenize(leg) is None:
        return True                                      # unbalanced quote → fail-safe preserve
    # (d1) A quoted assignment RHS that survived carrier 4 is expanded: preserve.
    if re.search(r"""[A-Za-z_]\w*=["']""", leg):
        return True
    # HEAD-DISPLACEMENT guard (#1178 SEC): compute the head clauses on the TRUE command word
    # (skip leading env-assignment / redirection prefixes) — else a leading `LC_ALL=C` / bare
    # `FOO=bar` / `2>/dev/null` displaces tokens[0] and the real executor is missed (under-block).
    cmd_leg = _leg_command_word(leg)
    if cmd_leg is not None:
        tokens = _shell_tokenize(cmd_leg)
        if tokens:
            head = os.path.basename(tokens[0])
            if head in _TOOL_HEADS:                      # (d2) git/gh → defer to carriers 5/7/8/9
                return True
            if _SHELL_STRING_EXECUTORS.match(head):      # (a) executor / interpreter
                return True
            if head in _HTTP_CLIENT_HEADS:               # (b) curl/wget (quoted head via shlex)
                return True
            if head in _EXEC_WRAPPERS_COARSE:            # (a) wrapper, boundary not skippable
                return True
            if head in _EXEC_WRAPPERS_RECURSE:           # (a) wrapper, recurse to nested command
                if _depth >= _MAX_WRAPPER_DEPTH:
                    return True                          # depth guard → fail-safe preserve
                nested = _wrapper_nested_command(cmd_leg, head)
                if nested is None:
                    return True                          # uncertain boundary → fail-safe preserve
                return _is_preserve_leg(nested, _depth + 1)
    return _preserve_tail(leg)


def _preserve_tail(leg: str) -> bool:
    """The text clauses of _is_preserve_leg that do not depend on the head: an unbalanced
    quote, a quoted assignment (d1), a process substitution (d3), an unquoted command
    substitution (d4), `gh api` and bare-token danger (c). Carriers 11-12 honour these too (strip._strip_data_leg)."""
    if _shell_tokenize(leg) is None:
        return True                                      # unbalanced quote → fail-safe preserve
    if re.search(r"""[A-Za-z_]\w*=["']""", leg):        # (d1), as above
        return True
    masked = _mask_shell_quotes(leg)                     # tail checks on the WHOLE leg (conservative)
    # (d3) an unquoted process substitution `<(cmd)` / `>(cmd)` EXECUTES cmd, so its quoted args
    # may execute — preserve. Masking blanks a quoted `"<("`, so only a REAL procsub triggers.
    if _PROCSUB_MARKER_RE.search(masked):
        return True
    # (d4) an unquoted command substitution `$(cmd)` / `` `cmd` `` runs cmd: preserve.
    if "$(" in masked or "`" in masked:
        return True
    if re.search(_GH_API_PREFIX, masked):                # (b) gh api (unquoted `gh api`)
        return True
    if _surface_danger is None:
        return True                                      # classifier not loaded → preserve
    return _surface_danger(masked)                       # (c) bare-token danger (defense-in-depth)


def _strip_quoted_positionals(leg: str) -> str:
    """Strip a leg's quoted positional values: dq span-scopes $()/backtick (via
    _strip_inert_dq_value); sq → bareword STRIPPED.
    """
    return _strip_quoted_strings(leg, _strip_inert_dq_value)


# git global options that PRECEDE the subcommand (for _resolve_git_subcommand, #1195 OBS-A1).
# VALUE-taking (skip the flag AND its value token):
_GIT_GLOBAL_VALUE_FLAGS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
})
# Boolean (skip the flag token only):
_GIT_GLOBAL_BOOL_FLAGS = frozenset({
    "--no-pager", "--paginate", "-p", "--bare", "--no-replace-objects",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs", "--no-optional-locks",
})


def _resolve_git_subcommand(tokens: "list[str]") -> "int | None":
    """tokens[0] == 'git'; skip leading git GLOBAL flags to return the SUBCOMMAND token index, or
    None if none is reached.
    """
    i, n = 1, len(tokens)
    while i < n:
        t = tokens[i]
        if t in _GIT_GLOBAL_VALUE_FLAGS:
            i += 2                                   # flag + its value token
            continue
        if "=" in t and t.split("=", 1)[0] in _GIT_GLOBAL_VALUE_FLAGS:
            i += 1                                   # --flag=value (one token)
            continue
        if t in _GIT_GLOBAL_BOOL_FLAGS:
            i += 1
            continue
        if t.startswith("-"):
            return None                              # unknown global flag -> fail-safe preserve
        return i                                     # first non-flag token = subcommand
    return None


def _strip_read_verb_values(leg: str, _depth: int = 0) -> "str | None":
    """READ-VERB value carve-out (pre-d2): strip the inert search/filter VALUES of a
    closed set of git/gh read verbs (_READ_VERB_SPECS) so a danger-looking quoted
    literal there (`git log --grep "…"`, `git grep '…'`, `gh pr list --search '…'`)
    no longer gates a faithful click via the d2 _TOOL_HEADS wholesale preserve.
    """
    # ---- guards: ANY hit => return None (fall through to _is_preserve_leg) ----
    if _shell_tokenize(leg) is None:                 # g1 unbalanced quote (fail-safe)
        return None
    if re.search(r"""[A-Za-z_]\w*=["']""", leg):     # g2 d1-replica: a NAME="…" survivor
        return None                                  #    is an expanded/eval'd value
    if _has_command_substitution(leg):               # g3 $()/backtick anywhere in the leg
        return None
    cmd_leg = _leg_command_word(leg)                 # g5a env/redirect prefix skip
    if cmd_leg is None:
        return None
    tokens = _shell_tokenize(cmd_leg)
    if not tokens:
        return None
    head = os.path.basename(tokens[0])
    if head == "git":
        si = _resolve_git_subcommand(tokens)         # A1: skip git globals to the real verb
        if si is None:                               #    (git -C x log …, git -c k=v log …)
            return None
        key = ("git", tokens[si])
    elif head == "gh" and len(tokens) >= 3:
        key = ("gh", tokens[1], tokens[2])
    elif head in _EXEC_WRAPPERS_RECURSE and _depth < _MAX_WRAPPER_DEPTH:
        # A read-only wrapper prefix: resolve the nested command and recurse.
        nested = _wrapper_nested_command(cmd_leg, head)
        if nested is None:
            return None
        rv = _strip_read_verb_values(nested, _depth + 1)
        if rv is None:
            return None
        mapped = leg[: len(leg) - len(nested)] + rv
        if _shell_tokenize(mapped) is None:          # g7 balance fail-safe on the mapped leg
            return leg
        return mapped
    else:
        return None
    spec = _READ_VERB_SPECS.get(key)                 # g5b exact-verb miss => None
    if spec is None:
        return None
    if _PROCSUB_MARKER_RE.search(_mask_shell_quotes(leg)):   # g4 procsub in leg (d3 replica)
        return None
    if spec.deny_re is not None and any(
        spec.deny_re.match(t) for t in tokens
    ):                                               # g6 executing-flag deny (shlex tokens —
        return None                                  #    a quoted '-O' is still the flag)
    # ---- strips (quote-balanced via the certified _strip_flag_values arms) ----
    out = leg
    if spec.flag_arm is not None:
        out = _strip_flag_values(out, spec.flag_arm.pattern, _keep_carrier_value)
    if spec.strip_positionals:
        out = _strip_quoted_positionals(out)
    if _shell_tokenize(out) is None:                 # g7 quote-balance fail-safe: NEVER
        return leg                                   #    emit a dangling quote (leg-merge)
    return out


def _maybe_strip_leg(leg: str) -> str:
    """Strip a leg's quoted positional VALUES iff it is inert (not preserved); else verbatim."""
    rv = _strip_read_verb_values(leg)
    if rv is not None:
        return rv                                    # already balance-checked inside
    if _is_preserve_leg(leg):
        return leg
    stripped = _strip_quoted_positionals(leg)
    # Quote-balance fail-safe: an unbalanced stripped leg falls back to the unstripped leg.
    if _shell_tokenize(stripped) is None:
        return leg
    return stripped


def _strip_inert_positional_legs(result: str) -> str:
    """Carrier-10 core (#1178): slice `result` into shell-operator legs — the SAME masked +
    FD-neutralize substrate as ``_slice_stripped_legs``, but RETAINING the original separators —
    and strip each INERT (non-preserved) leg's quoted positional values, emitting preserve legs
    verbatim.
    """
    view = _FD_REDIRECT_RE.sub(
        lambda m: " " * len(m.group()), _mask_shell_quotes(result)
    )
    out, last = [], 0
    for m in _COMPOUND_OPS_RE.finditer(view):
        out.append(_maybe_strip_leg(result[last:m.start()]))
        out.append(result[m.start():m.end()])            # ORIGINAL separator, verbatim
        last = m.end()
    out.append(_maybe_strip_leg(result[last:]))
    return "".join(out)


def _is_inert_help_leg(leg: str) -> bool:
    """True iff `leg` is an INERT help invocation that mutates nothing — a `--help` / `-h` help
    request, or a `gh|git help <sub>` head form.
    """
    if _shell_tokenize(leg) is None:
        return False                              # unbalanced quotes → abstain (gate)
    tokens = [leg[s:e] for s, e in _leg_token_spans(leg)]
    # Raw quote-stripped stream, so a global value option (-C <dir>) cannot shift the anchors.
    raw = [_strip_surrounding_quotes(t) for t in tokens]
    # Command head past leading NAME=value env-assignments (`FOO=bar git help push`
    # still resolves git as the head).
    head = 0
    while head < len(raw) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", raw[head]):
        head += 1
    is_git_gh_head = head < len(raw) and raw[head] in ("gh", "git")
    # Arm 1: `git|gh help <sub>` is inert only when help is the token right after the head.
    if is_git_gh_head and head + 1 < len(raw) and raw[head + 1] == "help":
        return True
    # Arm 2: -h/--help counts only at or after the subcommand (the first non-dash token).
    anchor = 0
    if is_git_gh_head:
        anchor = head + 1
        while anchor < len(raw) and raw[anchor].startswith("-") and raw[anchor] != "-":
            anchor += 1
    # Skip gh pr value-flag values so a value -h/--help is never read as a help request.
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        flagname = tok.split("=", 1)[0]
        is_value_flag = (
            flagname in _GH_PR_VALUE_TAKING_FLAGS
            or flagname in _INERT_HELP_EXTRA_VALUE_FLAGS
        ) and "=" not in tok
        if is_value_flag:
            i += 2
            continue
        if tok in ("--help", "-h") and i >= anchor:
            return True
        i += 1
    return False


def _neutralize_inert_help_legs(stripped: str) -> str:
    """Blank every INERT help leg (`_is_inert_help_leg`) in an already-stripped surface with an
    EQUAL-LENGTH run of spaces, leaving separators + non-inert legs byte-untouched.
    """
    spans = _slice_stripped_leg_spans(stripped)
    result = list(stripped)
    for start, end in spans:
        if _is_inert_help_leg(stripped[start:end]):
            for i in range(start, end):
                result[i] = " "
    return "".join(result)
