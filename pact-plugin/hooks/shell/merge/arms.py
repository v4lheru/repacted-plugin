"""
Location: pact-plugin/hooks/shell/merge/arms.py
Summary: DATA ONLY: the merge guard's literal arms, dangerous-pattern regexes, privileged
         flags and per-leg operation table. No function reads disk or state here.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: RULE_IDS, OP_RULE_IDS, MASK_RULE_IDS, DATA_PROGRAMS, AWK_PROGRAMS, _AWK_EXECUTES, _AWK_DATA_OPTIONS, _MAX_GLOBAL_FLAG_TOKENS, _GH_GLOBAL_FLAGS, _GH_FLAG_TOKENS, _GIT_GLOBAL_FLAGS, _GH_PREFIX, _GIT_PREFIX, _GH_API_PREFIX, _GLAB_PREFIX, _GLAB_API_PREFIX, _VALUE_TOKEN, _VERB_MSG_BODY, _MSG_FLAG_ANCHOR, _GH_PR_MERGE_RE, _GH_PR_CLOSE_RE, _GLAB_MR_MERGE_RE, _GLAB_MR_CLOSE_RE, _GLAB_MR_DELETE_RE, _FORCE_PUSH_LITERAL_ARMS, _CLOSE_LITERAL_ARMS, _API_LITERAL_ARMS, _BRANCH_DELETE_LITERAL_ARMS, _GH_PR_VALUE_TAKING_FLAGS, _INERT_HELP_EXTRA_VALUE_FLAGS, _MR_IMPLICIT_SENTINEL, _GLAB_MR_VALUE_FLAGS, _GLAB_MR_VERBS, _GITLAB_MR_URL_RE, _SHELL_OPERATOR_TOKENS, _REST_API_VALUE_FLAGS, _REST_API_FIELD_FLAGS, _UNMINTABLE_RUNNERS, _UNMINTABLE_SHELLS, _GRAPHQL_DESTRUCTIVE_FIELD_RE, _GRAPHQL_MUTATION_RE, _BODY_SENSITIVE_ENDPOINT_RE, _RAW_HTTP_METHOD_FLAGS, _RAW_HTTP_BODY_FLAGS, _RAW_HTTP_FILE_FLAGS, _GITLAB_MR_RE, _GITLAB_BRANCH_RE, _GITHUB_PR_RE, PRIVILEGED_FLAGS, DANGEROUS_PATTERNS, _PUSH_MAIN_DST_RE, _PUSH_OPEN_DST_RE, _PER_LEG_OPS.
"""

from __future__ import annotations

import re


# --- Regex prefix constants, shared by the read-side patterns and the mint classifier ---

# Bound on flag tokens in a CLI flag region (global flags before the subcommand, push flags
# before the refspec); keeps the regex walk linear.
_MAX_GLOBAL_FLAG_TOKENS = 32

# Optional global flags between CLI tool and subcommand — BOUNDED (was `*`).
_GH_GLOBAL_FLAGS  = r"(?:\S+\s+){0,%d}" % _MAX_GLOBAL_FLAG_TOKENS
# Tight flag walk for PR-number extraction. A flag's optional value must not start with `-`:
# that makes the split of a flag run unambiguous, which is what keeps this linear. With a
# `\S+` value it went exponential, and a hook that times out lets the tool call proceed.
_GH_FLAG_TOKENS   = r"(?:-\S*(?:\s+[^-\s]\S*)?\s+)*"
_GIT_GLOBAL_FLAGS = r"(?:\S+\s+){0,%d}" % _MAX_GLOBAL_FLAG_TOKENS

# Composed prefixes for DRY usage across all patterns.
_GH_PREFIX = r"\bgh\s+" + _GH_GLOBAL_FLAGS
_GIT_PREFIX = r"\bgit\s+" + _GIT_GLOBAL_FLAGS
_GH_API_PREFIX = _GH_PREFIX + r"api\b"

# glab (the GitLab CLI) shares gh's global-flag walk shape (`glab -R g/p mr merge 5`).
# `\bglab` never matches inside `gitlab` (no word boundary before its `lab`).
_GLAB_PREFIX = r"\bglab\s+" + _GH_GLOBAL_FLAGS
_GLAB_API_PREFIX = _GLAB_PREFIX + r"api\b"

# Quote-balanced value token: a run of bash quoted/escaped word pieces up to unquoted
# whitespace (consumed by _strip_flag_values).
_VALUE_TOKEN = (
    r"""(?:\\.|\$'(?:[^'\\]|\\.)*'|\$?"(?:[^"\\]|\\.)*"|'[^']*'|[^\s'"$\\]|\$)+"""
)

# Verb-message span body: words from the verb to the first unquoted ;/&&/|/newline, modelling
# every bash quoting form so a quote never pairs across a separator.
_VERB_MSG_BODY = (
    r"""(?:\\.|\$'(?:[^'\\]|\\.)*'|\$?"(?:[^"\\]|\\.)*"|'[^']*'|[^&|;\n"'$\\]+|\$)*"""
)

# Message-flag anchor for git merge, stash push/store, notes add/append: -m, or --message in
# any unambiguous abbreviation.
_MSG_FLAG_ANCHOR = r"((?:--m(?:e(?:s(?:s(?:a(?:g(?:e)?)?)?)?)?)?|(?<!\S)-[a-ln-zA-Z]*m)\s*)"

# Pre-compiled patterns for the operation-type classifier (consistent with
# DANGEROUS_PATTERNS style).
_GH_PR_MERGE_RE = re.compile(_GH_PREFIX + r"pr\s+merge\b")
_GH_PR_CLOSE_RE = re.compile(_GH_PREFIX + r"pr\s+close\b")
# GitLab merge requests via glab: merge, close, and delete. Aliases, from glab 1.106's
# own help: `accept` for merge, `del` for delete; close has none, and glab does no
# prefix matching of verbs (`glab mr merg` prints the mr help).
_GLAB_MR_MERGE_RE = re.compile(_GLAB_PREFIX + r"mr\s+(?:merge|accept)\b")
_GLAB_MR_CLOSE_RE = re.compile(_GLAB_PREFIX + r"mr\s+close\b")
_GLAB_MR_DELETE_RE = re.compile(_GLAB_PREFIX + r"mr\s+(?:delete|del)\b")

# Literal force-push arms: one list for the read floor and the mint classifier, matched per leg.
_FORCE_PUSH_LITERAL_ARMS = (
    # --force-if-includes alone is a no-op (it only refines --force-with-lease), so it is
    # not a force spelling (1.4.1 U13).
    re.compile(_GIT_PREFIX + r"push\s+.*--force(?!-with-lease|-if-includes)\b"),
    re.compile(_GIT_PREFIX + r"push\s+.*-f\b"),
    re.compile(_GIT_PREFIX + r"push\s+-[a-zA-Z]*f"),
)

# Close arms, matched per leg so a --delete-branch in a benign continuation leg does not fire.
_CLOSE_LITERAL_ARMS = (
    re.compile(_GH_PREFIX + r"pr\s+close\b(?=.*--delete-branch)"),   # forward
    re.compile(r"--delete-branch.*" + _GH_PREFIX + r"pr\s+close\b"),  # reversed
)

# API danger arms, matched per leg so a method or body flag in a benign later leg does not fire.
_API_LITERAL_ARMS = (
    # API-based merge bypasses (require mutating HTTP method to avoid blocking reads)
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))(?:PUT|PATCH|POST)\b).*merge", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X\s*|--request\s+)(?:PUT|PATCH|POST)\b).*api.*merge", re.IGNORECASE),
    # Branch delete via DELETE on git/refs; host-agnostic so an Enterprise or proxy URL is caught.
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))DELETE\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X\s*|--request\s+)DELETE\b).*git/refs", re.IGNORECASE),
    # API-based ref mutation / force push via mutating method to git/refs endpoint
    # (any mutating operation on git refs via API is inherently dangerous). Curl arm is
    # host-agnostic (#1061) — see the DELETE arm note above.
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))(?:PATCH|POST|PUT)\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X\s*|--request\s+)(?:PATCH|POST|PUT)\b).*git/refs", re.IGNORECASE),
    # Protection mutation: DELETE/PUT/PATCH on branches/<b>/protection weakens it; POST
    # strengthens and is not gated.
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))(?:DELETE|PUT|PATCH)\b).*branches/.*/protection", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X\s*|--request\s+)(?:DELETE|PUT|PATCH)\b).*branches/.*/protection", re.IGNORECASE),
    re.compile(r"\bwget\b(?=.*--method=(?:DELETE|PUT|PATCH)\b).*branches/.*/protection", re.IGNORECASE),
    # gh api implicit POST: body param flags (-f, -F, --field, --raw-field, --input)
    # cause gh api to default to POST. Dangerous when targeting git/refs or merge.
    # Negative lookahead excludes explicit GET (which overrides implicit POST).
    re.compile(_GH_API_PREFIX + r"(?!.*(?:-X\s*|--method(?:=|\s+))GET\b)(?=.*(?:-f|-F|--field|--raw-field|--input)\s).*git/refs", re.IGNORECASE),
    re.compile(_GH_API_PREFIX + r"(?!.*(?:-X\s*|--method(?:=|\s+))GET\b)(?=.*(?:-f|-F|--field|--raw-field|--input)\s).*merge", re.IGNORECASE),
    # curl implicit POST: --data/-d/--data-raw/--data-binary flags cause curl to
    # default to POST. Dangerous when targeting git/refs or merge API endpoints.
    # Negative lookahead excludes explicit GET (which overrides implicit POST).
    re.compile(r"\bcurl\b(?!.*(?:-X\s*|--request\s+)GET\b)(?=.*(?:--data(?:-(?:raw|binary))?|-d)\s).*git/refs", re.IGNORECASE),
    re.compile(r"\bcurl\b(?!.*(?:-X\s*|--request\s+)GET\b)(?=.*(?:--data(?:-(?:raw|binary))?|-d)\s).*api.*merge", re.IGNORECASE),
    # Contents API: write operations (PUT/PATCH/POST) to /contents/ endpoint
    # targeting main or master branch. Flags any mutating /contents/ call that
    # mentions main or master anywhere in the command (acceptable false positive).
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))(?:PUT|PATCH|POST)\b).*contents/.*(?:main|master)", re.IGNORECASE),
    re.compile(r"\bcurl\b(?=.*(?:-X\s*|--request\s+)(?:PUT|PATCH|POST)\b).*api.*contents/.*(?:main|master)", re.IGNORECASE),
    # Alternative HTTP clients: wget with --method flag
    re.compile(r"\bwget\b(?=.*--method=(?:DELETE|PATCH|POST|PUT)\b).*git/refs", re.IGNORECASE),
    re.compile(r"\bwget\b(?=.*--method=(?:DELETE|PATCH|POST|PUT)\b).*merge", re.IGNORECASE),
    # GitLab REST via glab api: explicit-method arms only; each has an extractor in
    # _gitlab_api_leg_op so every gated spelling can mint.
    re.compile(_GLAB_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))(?:PUT|POST)\b).*merge_requests/\d+/merge\b", re.IGNORECASE),
    re.compile(_GLAB_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))DELETE\b).*merge_requests/\d+(?![\w/])", re.IGNORECASE),
    re.compile(_GLAB_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))PUT\b)(?=.*state_event=close\b).*merge_requests/\d+(?![\w/])", re.IGNORECASE),
    re.compile(_GLAB_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))DELETE\b).*repository/branches/\S", re.IGNORECASE),
    re.compile(_GLAB_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))DELETE\b).*protected_branches/\S", re.IGNORECASE),
    # Closing a GitHub PR through the REST API: PATCH pulls/<N> with state=closed (the
    # API spelling of `gh pr close`, which gates since FIX-MERGE f3).
    re.compile(_GH_API_PREFIX + r"(?=.*(?:-X\s*|--method(?:=|\s+))PATCH\b)(?=.*state=closed\b).*pulls/\d+(?![\w/])", re.IGNORECASE),
    # Known API detection gaps (defense-in-depth, not a security boundary):
    # - GraphQL mutations: gh api graphql -f query='mutation { ... }' bypasses REST-path matching
    # - gh alias: aliases can hide API calls (tracked in #270)
)

# Branch-delete arms, matched per leg so a -D in a benign continuation leg does not fire.
_BRANCH_DELETE_LITERAL_ARMS = (
    re.compile(_GIT_PREFIX + r"branch\s+.*-D\b"),
    re.compile(_GIT_PREFIX + r"branch\s+.*--delete\s+--force\b"),
    re.compile(_GIT_PREFIX + r"branch\s+--force\s+--delete\b"),
)

# gh pr merge|close long flags that take a value: a digit after one is the value, not the PR.
_GH_PR_VALUE_TAKING_FLAGS = frozenset({
    "--body",
    "--body-file",
    "--subject",
    "--author-email",
    "--match-head-commit",
    "--comment",
    "--max-retries",
    "--retry-count",
    "--timeout",
})

# Extra value-taking flags the inert-help check skips, so -h in a value slot is not a help request.
_INERT_HELP_EXTRA_VALUE_FLAGS = frozenset(
    {"-t", "-b", "-F", "-A", "-c", "-R", "--repo"}
)


# Target-blind sentinel for no-selector glab forms; distinct from every explicit identity.
_MR_IMPLICIT_SENTINEL = "\x00implicit-mr"

# glab mr value-taking flags (never the selector); booleans are absent so the selector survives.
_GLAB_MR_VALUE_FLAGS = frozenset({
    "-R", "--repo", "-m", "--message", "--squash-message", "--sha",
})
_GLAB_MR_VERBS = frozenset({"merge", "accept", "close", "delete", "del"})
_GITLAB_MR_URL_RE = re.compile(
    r"^https?://([^/\s]+)/(.+?)/-/merge_requests/(\d+)(?:[/?#].*)?$"
)
_SHELL_OPERATOR_TOKENS = frozenset({"&&", "||", ";", "|", "&", "|&"})


# `glab api` / `gh api` flags that take a value. The endpoint is the first positional.
_REST_API_VALUE_FLAGS = frozenset({
    "-X", "--method", "-f", "--field", "-F", "--raw-field", "-H", "--header",
    "--hostname", "--input", "-R", "--repo", "-q", "--jq", "-t", "--template", "--cache",
})
_REST_API_FIELD_FLAGS = frozenset({"-f", "--field", "-F", "--raw-field"})


# --- Shapes whose deciding value is off the command line or cannot be bound: gate, never mint ---
_UNMINTABLE_RUNNERS = frozenset({"env", "sudo", "rtk", "proxy", "command", "exec", "nohup", "time",
                                 "nice", "timeout", "builtin"})
_UNMINTABLE_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
# A GraphQL field that merges, closes, deletes, destroys or accepts (mergePullRequest,
# closePullRequest, deleteRef, mergeRequestAccept, destroyMergeRequest, ...).
_GRAPHQL_DESTRUCTIVE_FIELD_RE = re.compile(
    r"\b[A-Za-z_]*(?:[Mm]erge|[Cc]lose|[Dd]elete|[Dd]estroy|[Aa]ccept)[A-Za-z_]*\s*[({]")
_GRAPHQL_MUTATION_RE = re.compile(r"\bmutation\b")
# REST endpoints whose state a body can merge, close or delete.
_BODY_SENSITIVE_ENDPOINT_RE = re.compile(
    r"(?:^|/)(?:pulls|merge_requests|git/refs|branches|protected_branches)(?:/|$)|(?:^|/)merge$")
_RAW_HTTP_METHOD_FLAGS = frozenset({"-X", "--request", "--method"})
_RAW_HTTP_BODY_FLAGS = frozenset({"-d", "--data", "--data-binary", "--data-raw", "--data-ascii",
                                  "--data-urlencode", "--json", "-F", "--form", "--body-data",
                                  "--post-data", "--body-file", "--post-file", "-T", "--upload-file"})
_RAW_HTTP_FILE_FLAGS = frozenset({"--body-file", "--post-file", "-T", "--upload-file"})
_GITLAB_MR_RE = re.compile(r"/merge_requests/\d+$")
_GITLAB_BRANCH_RE = re.compile(r"/(?:repository/branches|protected_branches)/[^/]+$")
_GITHUB_PR_RE = re.compile(r"/repos/[^/]+/[^/]+/pulls/\d+$")


# --- Privileged-flag binding: the (op, target) pair drops dash-flags, so bound_flags adds them.
# PRIVILEGED_FLAGS is { op_type -> { canonical_long -> (aliases, value_taking) } }; approved and
# executed sets must be equal, so an added --admin re-prompts. Membership is pure data. ---
PRIVILEGED_FLAGS: dict[str, dict[str, tuple[tuple[str, ...], bool]]] = {
    "merge": {
        "--admin":         (("--admin",), False),               # bypass branch protection
        # side-effect: deletes the source branch; `--remove-source-branch` is glab's long
        # spelling (its short is the same `-d`), FIX-MERGE f3.
        "--delete-branch": (("-d", "--delete-branch", "--remove-source-branch"), False),
        "--repo":          (("-R", "--repo"), True),            # cross-repo redirect (value-carrying target)
        # Head-SHA pin: approving with it and running without it must refuse (glab --sha is the same).
        "--match-head-commit": (("--match-head-commit", "--sha"), True),
    },
    "mr-delete": {
        # GitLab merge-request deletion (FIX-MERGE f3): the op-trigger is the verb itself;
        # the repo selector is the one value-carrying redirect, bound like merge/close.
        "--repo":          (("-R", "--repo"), True),
    },
    "close": {
        "--repo":          (("-R", "--repo"), True),            # cross-repo redirect (value-carrying target)
        # --delete-branch makes close irreversible; binding it stops a bare-close token authorizing it.
        "--delete-branch": (("-d", "--delete-branch"), False),
    },
    "force-push": {
        "--no-verify":     (("--no-verify",), False),           # bypass pre-push hook
    },
    "push-to-main": {
        # Lease push can rewrite history, so its presence splits plain and lease push identities.
        "--force-with-lease": (("--force-with-lease",), False),
        # bypasses the pre-push hook — bound like force-push's, so a plain push-to-main
        # approval never covers `--no-verify` (1.4.1 U13)
        "--no-verify":     (("--no-verify",), False),
    },
    "branch-delete": {
        # No bound flags today: branch-delete's privileged effect is its op-trigger
        # (-D / --delete --force), already bound via op_type. Kept as an explicit
        # extension point so a future bound flag is a one-line data edit here.
    },
    "remote-ref-delete": {
        # These modify the delete trigger already bound by op_type, so they add a binding dimension.
        "--mirror":    (("--mirror",), False),
        "--prune":     (("--prune",), False),
        # bypasses the pre-push hook — a privilege, not a target change
        "--no-verify": (("--no-verify",), False),
    },
    "remote-mass-delete": {
        # Narrower on purpose: --mirror/--prune trigger this class, so they are not bound as modifiers.
        "--no-verify": (("--no-verify",), False),
    },
    "branch-protection": {
        # No bound flags: weakening protection is the op trigger itself, bound via op_type.
    },
}


# --- Read-floor danger predicates, shared so the read hook and the mint hook use the same ones ---

DANGEROUS_PATTERNS = [
# PR merge via gh CLI
re.compile(_GH_PREFIX + r"pr\s+merge\b"),
# PR close, bare or with --delete-branch: close is irreversible, so a bare close gates too.
_GH_PR_CLOSE_RE,
# GitLab merge requests via glab: merge, close, delete (FIX-MERGE f3).
_GLAB_MR_MERGE_RE,
_GLAB_MR_CLOSE_RE,
_GLAB_MR_DELETE_RE,
# The close + --delete-branch arms live in _CLOSE_LITERAL_ARMS and match per leg.
]


# Push-to-main predicate: a refspec whose destination is exactly main/master (optional src: and
# refs/heads/), matched as a complete ref.
_PUSH_MAIN_DST_RE = re.compile(
    r"(?:[^\s:+][^\s:]*:)?"   # optional <src>:  (src has no ':' and no leading '+')
    r"(?:refs/heads/|heads/)?"  # optional full-ref dst prefix (git DWIMs heads/main too)
    r"(?:main|master)"       # the DESTINATION branch
    r"(?![\w./@+-])"         # ... as a COMPLETE ref: no continuation char follows
)

# A refspec whose DESTINATION is not a literal ref: a glob (`refs/heads/*:refs/heads/*`
# pushes main too) or a shell expansion (`"$(echo main)"`, `${B:-main}`, `$B`, backticks)
# the guard cannot resolve. Either may update main, so it gates like a push to main.
_PUSH_OPEN_DST_RE = re.compile(r"(?:^|:)[^:]*[*$`]")

# Per-leg op filter: _flag_condition_danger_op is first-leg anchored, so callers rerun it on each
# leg for these classes (all six). Reached only after the pipeline returns None/False, so no
# existing verdict changes.
_PER_LEG_OPS = (
    "push-to-main",
    "force-push",
    "branch-delete",
    "close",
    "remote-ref-delete",
    "remote-mass-delete",
)


# ─── Rule ids ─────────────────────────────────────────────────────────────────────
# Every merge-guard refusal ends with `[rule: <id>]` (merge_guard_pre.check_merge_authorization
# appends it); the dispatcher lifts the id into the journal row and
# docs/troubleshooting.md#<id-with-dashes> explains it. OP_RULE_IDS maps the operation
# type the arms detect to the id of the plain "requires user approval" refusal.
RULE_IDS = {
    "mg.merge.explicit": "A PR or MR merge requires an approval bound to this exact command.",
    "mg.close.explicit": "A PR or MR close requires an approval bound to this exact command.",
    "mg.force_push.explicit": "A force-push requires an approval bound to this exact command.",
    "mg.branch_delete.explicit": "A branch delete requires an approval bound to this exact command.",
    "mg.remote_ref_delete.explicit": "A remote ref delete requires an approval bound to this exact command.",
    "mg.mass_delete.explicit": "A remote mass delete requires an approval bound to this exact command.",
    "mg.protection.explicit": "A branch-protection change requires an approval bound to this exact command.",
    "mg.mr_delete.explicit": "An MR delete requires an approval bound to this exact command.",
    "mg.push.to_main.implicit": "A push that lands on main without naming it requires an approval.",
    "mg.push.to_main.expanded": "A push names its destination with a shell expansion or a glob.",
    "mg.danger.unbound": "A destructive shape the arms recognise but no operation type binds.",
    "mg.compound.destructive": "Several destructive operations in one command cannot share one approval.",
    "mg.unmintable.shape": "A shape no approval can bind (GraphQL mutation, body from a file, raw HTTP, or an operation no approval can name inside a shell -c or eval string, a here-string, a subshell, a substitution or a compound statement).",
    "mg.unmintable.flag_flood": "More than 32 flags hide what the command does.",
    "mg.unmintable.long_run": "A run of 512 or more characters cannot be bound to an approval.",
    "mg.token.mismatch": "The approved operation does not match this command.",
    "mg.token.consume_failed": "The authorization token could not be consumed.",
    "mg.token.no_record": "A matching approval token has no answer from the user on record in the session transcript.",
}
OP_RULE_IDS = {
    "merge": "mg.merge.explicit",
    "close": "mg.close.explicit",
    "force-push": "mg.force_push.explicit",
    "branch-delete": "mg.branch_delete.explicit",
    "remote-ref-delete": "mg.remote_ref_delete.explicit",
    "remote-mass-delete": "mg.mass_delete.explicit",
    "branch-protection": "mg.protection.explicit",
    "mr-delete": "mg.mr_delete.explicit",
    "push-to-main": "mg.push.to_main.implicit",
    None: "mg.danger.unbound",
}
assert set(OP_RULE_IDS.values()) <= set(RULE_IDS)

# ─── Inert-text masking (refactor §3.7 part 2) ─────────────────────────────────────────────
# Allow-side rules: the carrier that masked a span is journaled under PACT_MASK_TRACE=1 with
# one of these ids, so a false negative can be traced to its carrier.
MASK_RULE_IDS = {
    "mg.inert.data_program": "A quoted argument of a program that only reads or prints its "
                             "arguments (DATA_PROGRAMS) is data, not a command.",
    "mg.inert.json_literal": "A quoted argument that parses as a JSON object or array is data "
                             "to every program but a shell.",
    "mg.inert.heredoc_consumer": "A heredoc body or printed text piped to a program that is "
                                 "not a shell is data (carrier 1 with the pipe-to-shell view).",
}
# Carrier 11: programs whose quoted arguments are text they read, match or print, never a
# command they run. awk can run a command from its program text (`system()`, a pipe,
# `getline`); its arguments are data only when the text holds none of those. GNU sed's `e`
# is a known limit: carrier 10 already masks sed's positionals.
DATA_PROGRAMS = frozenset({
    "jq", "yq", "printf", "grep", "egrep", "fgrep", "rg", "sed", "cut", "tr", "sort", "uniq",
    "wc", "head", "tail", "column", "paste", "tee", "fold", "nl", "comm", "diff", "base64",
    "awk", "gawk", "mawk", "nawk",
})
AWK_PROGRAMS = frozenset({"awk", "gawk", "mawk", "nawk"})
# gawk also calls a built-in by name (`f="system"; @f(...)`) and loads code (`@load`,
# `@include`): any `@` before a name keeps the leg.
_AWK_EXECUTES = re.compile(r"system\s*\(|\||getline|@\s*[A-Za-z_]")
# The only awk options a data leg may carry (strip._awk_is_data): a field separator, a
# variable, `--` and `-` (stdin). Anything else (-f, --fil, -bf, -i, -l, -E, -W) may load code.
_AWK_DATA_OPTIONS = ("-F", "-v")

