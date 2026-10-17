"""
Location: pact-plugin/hooks/shell/merge/targets.py
Summary: PR, MR and API target extractors (gh pr, glab mr, REST and GraphQL legs).
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: _GH_PR_NUMBER_RE, _QUOTED_COMMAND_RE, _BARE_COMMAND_RE, _extract_pr_number, _GH_PULL_URL_RE, _GH_CLOSE_VALUE_LONG, _GH_CLOSE_VALUE_SHORT, _gh_close_positionals, _classify_pr_target, _extract_close_target, _GH_MERGE_VALUE_LONG, _GH_MERGE_VALUE_SHORT, _gh_merge_positionals, _extract_merge_target, _is_bare_cli_merge, _API_MERGE_GH_VALUE_FLAGS, _PULLS_MERGE_PR_RE, _api_merge_leg_endpoint, _extract_api_merge_pr, _classify_mr_selector, _glab_mr_leg_target, _rest_api_leg_parts, _field_is, _gitlab_api_leg_op, _gh_api_close_leg_pr, _uses_raw_http_client, _api_ref_identity, _gh_ref_segment, _GH_REF_PATH_RE, _gh_rest_ref_identity, _GH_PROTECTION_PATH_RE, _gh_rest_protection_identity, _gh_rest_pr_identity.
"""

from __future__ import annotations

import os
import re

from shell.merge import remotes as _remotes  # REBOUND name, read live
from shell.merge.arms import (_GH_API_PREFIX, _GH_FLAG_TOKENS, _GH_PR_MERGE_RE, _GH_PR_VALUE_TAKING_FLAGS, _GITLAB_MR_URL_RE, _GLAB_API_PREFIX, _GLAB_MR_VALUE_FLAGS, _GLAB_MR_VERBS, _INERT_HELP_EXTRA_VALUE_FLAGS, _MR_IMPLICIT_SENTINEL, _REST_API_FIELD_FLAGS, _REST_API_VALUE_FLAGS, _SHELL_OPERATOR_TOKENS)
from shell.merge.remotes import (_current_branch, _gh_repo_identity, _gitlab_branch_segment, _gitlab_project_identity)
from shell.merge.strip import (_split_into_legs, _strip_non_executable_content)
from shell.merge.views import (_executable_prefix, _executed_surface_view, _shell_tokenize, _strip_surrounding_quotes)

# --- Command-context extraction: the (op, target) SSOT both hooks call on a command string ---

# PR-number positional; both flag walks use the tight _GH_FLAG_TOKENS so the walk cannot run
# past a gh pr substring inside a quoted value.
_GH_PR_NUMBER_RE = re.compile(
    r"\bgh\s+" + _GH_FLAG_TOKENS + r"pr\s+(?:merge|close)\s+"
    + _GH_FLAG_TOKENS + r"(\d+)(?![\w-])"
)

# A quoted command region in prose: backticks, then single, then double quotes.
_QUOTED_COMMAND_RE = re.compile(
    r"`([^`]+)`"        # backticks
    r"|'([^']+)'"       # single quotes
    r'|"([^"]+)"'       # double quotes
)

# A bare gh/git/glab span up to a separator or quote; spans with an op but no target drop out.
_BARE_COMMAND_RE = re.compile(r"\b(?:gh|git|glab)\s+[^`'\";|&\n]+")


def _extract_pr_number(command: str) -> str | None:
    """Extract the PR number positional from a `gh pr merge|close` command."""
    match = _GH_PR_NUMBER_RE.search(command)
    if not match:
        return None
    pr_pos = match.start(1)
    # Inspect the immediately-preceding token for a known value-taking
    # long-form flag; if present, the captured digit is its value, not the PR.
    preceding = command[:pr_pos].rstrip()
    flag_match = re.search(r"(--[\w-]+)$", preceding)
    if flag_match and flag_match.group(1) in _GH_PR_VALUE_TAKING_FLAGS:
        return None
    return match.group(1)


# PR URL, anchored on the literal /pull/ segment so digits in owner/repo are never the PR.
_GH_PULL_URL_RE = re.compile(
    r"https?://([\w.@:-]+)/([\w.-]+/[\w.-]+)/pull/(\d+)(?![\w-])"
)

# gh pr close value flags (-R/--repo, -c/--comment); their values are removed before the
# positional scan so a repo or comment value is never read as the target.
_GH_CLOSE_VALUE_LONG = frozenset({"--repo", "--comment"})
_GH_CLOSE_VALUE_SHORT = frozenset("Rc")


def _gh_close_positionals(tokens: "list[str]") -> "list[str] | None":
    """The positional args of a ``gh pr close`` token list, with value-taking flags AND their
    values removed (cobra semantics: clustered shorts, ``=``-joined and attached values all
    handled).
    """
    try:
        i = tokens.index("close") + 1
    except ValueError:
        return None
    positionals: "list[str]" = []
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.startswith("--"):
            flag, has_eq, _v = tok.partition("=")
            # `--repo value` / `--comment value` consume the NEXT token; the
            # `=`-joined and boolean/unknown long forms are self-contained.
            i += 2 if (flag in _GH_CLOSE_VALUE_LONG and not has_eq) else 1
            continue
        if tok.startswith("-") and tok != "-":
            # Short cluster (cobra): a value-taking short consumes the REST of the
            # cluster as its attached value, or — if it is the last char — the NEXT
            # token. Booleans before it (`-dR value` = -d + -R value) don't matter.
            body = tok[1:]
            consumes_next = False
            for j, ch in enumerate(body):
                if ch in _GH_CLOSE_VALUE_SHORT:
                    consumes_next = j == len(body) - 1   # `-R value`; else `-Rvalue`
                    break
            i += 2 if consumes_next else 1
            continue
        positionals.append(tok)
        i += 1
    return positionals


def _classify_pr_target(target: str) -> str | None:
    """Classify a single ALREADY-QUOTE-STRIPPED gh-pr positional into one of FOUR
    disjoint identity namespaces — the SHARED SSOT (#1203 M1) for BOTH the close and
    the merge target identity, so the two can never diverge:
    """
    if not target:
        return None  # empty/degenerate positional -> abstain, never a garbage id
    if target.isdigit():
        return target  # bare PR number — gh's number precedence, repo-implicit
    url = _GH_PULL_URL_RE.search(target)
    if url is not None:
        return "url:" + url.group(1) + "/" + url.group(2) + "#" + url.group(3)
    if "://" in target:
        return None  # unrecognized URL form -> abstain, never a garbage branch id
    return "branch:" + target


def _extract_close_target(command: str) -> str | None:
    """The close-target identity for a faithful ``gh pr close`` command, across
    all three positional forms gh accepts (``{<number> | <url> | <branch>}``).
    """
    prefix = _executable_prefix(command)
    if prefix is None:
        return None  # unbalanced quote / procsub -> abstain (existing safe posture)
    tokens = _shell_tokenize(prefix)
    if tokens is None:
        return None  # unparseable -> abstain
    positionals = _gh_close_positionals(tokens)
    # Exactly ONE positional is a single-target close. 0 (the no-arg form gh
    # refuses) or >=2 (ambiguous / an unlisted value-flag's value survived) ->
    # abstain; the >=2 case is the over-block-safe fail direction.
    if positionals is None or len(positionals) != 1:
        return None
    target = _strip_surrounding_quotes(positionals[0])
    # Shared _classify_pr_target keeps the close and merge identities from diverging.
    return _classify_pr_target(target)


# Merge walk strips the merge value flags (long SSOT, --repo, short aliases), not close's set.
_GH_MERGE_VALUE_LONG = _GH_PR_VALUE_TAKING_FLAGS | {"--repo"}
_GH_MERGE_VALUE_SHORT = frozenset(
    f[1] for f in _INERT_HELP_EXTRA_VALUE_FLAGS if len(f) == 2 and f[0] == "-"
)


def _gh_merge_positionals(tokens: "list[str]") -> "list[str] | None":
    """The positional args of a ``gh pr merge`` token list, with MERGE value-taking flags AND their
    values removed (mirrors _gh_close_positionals's cobra walk, but over the MERGE value-flag
    set — NOT close's {--repo,--comment}: a merge form carries --subject/--body/-t/… so close's
    walk would mis-read a value as the target, reviving M1 one layer down).
    """
    try:
        i = tokens.index("merge") + 1
    except ValueError:
        return None
    positionals: "list[str]" = []
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok.startswith("--"):
            flag, has_eq, _v = tok.partition("=")
            i += 2 if (flag in _GH_MERGE_VALUE_LONG and not has_eq) else 1
            continue
        if tok.startswith("-") and tok != "-":
            body = tok[1:]
            consumes_next = False
            for j, ch in enumerate(body):
                if ch in _GH_MERGE_VALUE_SHORT:
                    consumes_next = j == len(body) - 1   # `-R value`; else `-Rvalue`
                    break
            i += 2 if consumes_next else 1
            continue
        positionals.append(tok)
        i += 1
    return positionals


def _extract_merge_target(command: str) -> str | None:
    """The EXPLICIT merge-target identity for a faithful ``gh pr merge <branch>`` / ``<url>`` (or
    ``<number>``, though _extract_pr_number owns that first), or None (#1203 M1).
    """
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    tokens = _shell_tokenize(prefix)
    if tokens is None:
        return None
    positionals = _gh_merge_positionals(tokens)
    if positionals is None or len(positionals) != 1:
        return None
    return _classify_pr_target(_strip_surrounding_quotes(positionals[0]))


def _is_bare_cli_merge(command: str) -> bool:
    """True iff ``command`` is a TRULY-BARE CLI ``gh pr merge`` — the CLI merge shape
    (_GH_PR_MERGE_RE on the quote-masked view, which excludes an API merge that always carries
    an explicit endpoint PR) with ZERO real positional target (flags-only).
    """
    if not _GH_PR_MERGE_RE.search(_executed_surface_view(command)):
        return False
    prefix = _executable_prefix(command)
    if prefix is None:
        return False
    tokens = _shell_tokenize(prefix)
    if tokens is None:
        return False
    return _gh_merge_positionals(tokens) == []


# Value flags skipped in the gh api merge endpoint walk (gh api only).
_API_MERGE_GH_VALUE_FLAGS = frozenset({
    "-X", "--method", "-f", "--field", "-F", "--raw-field", "--input",
    "-H", "--header", "--hostname", "-q", "--jq", "-t", "--template",
    "--cache", "-R", "--repo",
})

_PULLS_MERGE_PR_RE = re.compile(r"pulls/(\d+)/merge\b")


def _api_merge_leg_endpoint(leg: str) -> str | None:
    """Return the ENDPOINT PR of a genuine per-leg API merge, or None (#1096)."""
    client_gh = re.search(_GH_API_PREFIX, leg, re.IGNORECASE)
    if not client_gh:
        # curl/wget API merges classify None: still gated by the read arms, never mintable.
        return None
    # Recognition uses this exact literal through re.search; a non-vacuity test patches it.
    if not re.search(r"pulls/\d+/merge\b", leg):
        return None
    if not re.search(r"(?:\b|(?<=-X))(?:PUT|PATCH|POST)\b", leg, re.IGNORECASE):
        return None
    # Recognized. Extract the ENDPOINT-position PR.
    stripped = _strip_non_executable_content(leg)
    tokens = _shell_tokenize(stripped)
    if tokens is None:
        # Tokenizer failure (unbalanced quotes): NEVER None for a recognized leg
        # (over-block-safe). Prefer the body-stripped surface (avoids body decoys);
        # the raw leg is guaranteed to match (recognition required it).
        m = _PULLS_MERGE_PR_RE.search(stripped) or _PULLS_MERGE_PR_RE.search(leg)
        return m.group(1) if m else None
    value_flags = _API_MERGE_GH_VALUE_FLAGS
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            base = tok.split("=", 1)[0]
            if "=" not in tok and base in value_flags:
                i += 1  # bare space-separated value-flag: skip its value token too
            # attached-value (`--method=PUT`, `-XPUT`) and booleans consume no extra token
            i += 1
            continue
        # A positional: the endpoint is the FIRST pulls/<N>/merge positional (the URL
        # argument; for gh api the endpoint is the first positional by REST convention).
        mm = _PULLS_MERGE_PR_RE.search(tok)
        if mm:
            return mm.group(1)
        i += 1
    # Recognised, but the only pulls/<N>/merge was a skipped flag value: no endpoint, no mint.
    return None


def _extract_api_merge_pr(command: str) -> str | None:
    """Command-level api-merge PR: walk the legs and return the FIRST recognized leg's
    ENDPOINT-position PR (#1096). Leg-scoping closes the cross-leg/echo decoy; the
    per-leg helper closes the same-leg flag-value decoy. Consumed by extract_command_context
    (mint + read whole-fallback + #1097 retirement cmd_target all heal at this one site)."""
    for _leg in _split_into_legs(command):
        pr = _api_merge_leg_endpoint(_leg)
        if pr is not None:
            # amend_2: qualify by repo from the SAME leg's endpoint positional; None when
            # the repo is absent/unresolvable (recognized, gated, not approvable).
            parts = _rest_api_leg_parts(_leg, "gh")
            if parts is not None and re.search(r"pulls/" + pr + r"/merge\b", parts[1]):
                return _gh_rest_pr_identity(parts[1], pr)
            m = re.search(r"\brepos/[^/\s'\"]+/[^/\s'\"]+/pulls/" + pr + r"/merge\b", _leg)
            return _gh_rest_pr_identity(m.group(0), pr) if m else None
    return None


def _classify_mr_selector(selector: str) -> str | None:
    """One glab MR selector -> a namespaced identity: bare iid (`42` or `!42`) -> `42`;
    a GitLab MR URL -> `url:<host>/<project>!<iid>` (host- and project-qualified, so it
    never set-equals a bare iid from another project); any other `://` token -> None
    (abstain); anything else -> `branch:<name>` (glab accepts a source branch)."""
    if not selector:
        return None
    bare = selector[1:] if selector.startswith("!") else selector
    if bare.isdigit():
        return bare
    url = _GITLAB_MR_URL_RE.match(selector)
    if url is not None:
        return "url:" + url.group(1) + "/" + url.group(2) + "!" + url.group(3)
    if "://" in selector:
        return None
    return "branch:" + selector


def _glab_mr_leg_target(leg: str) -> "tuple[bool, str | None]":
    """(is_glab_mr_leg, mr_target) for one leg. The selector is the single positional
    after `glab [globals] mr <verb>`: none -> the implicit sentinel, one -> its identity,
    two or more -> None (ambiguous; gated but not approvable, the gh >=2 posture)."""
    tokens = _shell_tokenize(leg)
    if tokens is None:
        return False, None
    try:
        start = next(i for i, tok in enumerate(tokens) if os.path.basename(tok) == "glab")
    except StopIteration:
        return False, None
    i, n = start + 1, len(tokens)
    while i < n and tokens[i] != "mr":          # global flags between glab and mr
        tok = tokens[i]
        i += 2 if (tok in ("-R", "--repo") and "=" not in tok) else 1
    if i + 1 >= n or tokens[i + 1] not in _GLAB_MR_VERBS:
        return False, None
    i += 2
    positionals: "list[str]" = []
    while i < n and tokens[i] not in _SHELL_OPERATOR_TOKENS:
        tok = tokens[i]
        if tok.startswith("-") and tok != "-":
            base = tok.split("=", 1)[0]
            i += 2 if (base in _GLAB_MR_VALUE_FLAGS and "=" not in tok) else 1
            continue
        positionals.append(_strip_surrounding_quotes(tok))
        i += 1
    if not positionals:
        return True, _MR_IMPLICIT_SENTINEL
    if len(positionals) > 1:
        return True, None
    return True, _classify_mr_selector(positionals[0])


def _rest_api_leg_parts(leg: str, client: str) -> "tuple[str, str, list[str], str | None] | None":
    """(METHOD, endpoint path, [field `k=v`, ...], -R value) of a `<client> api` call in one leg,
    or None when the leg is not one or carries no EXPLICIT method (-X/--method, as the
    read-floor arms require: without one the call is a GET or an implicit POST, neither
    of which merges, closes or deletes on these endpoints)."""
    tokens = _shell_tokenize(leg)
    if tokens is None:
        return None
    try:
        head = next(i for i, tok in enumerate(tokens) if os.path.basename(tok) == client)
        api = tokens.index("api", head + 1)
    except (StopIteration, ValueError):
        return None
    method, endpoint, fields, repo = None, None, [], None
    j = head + 1
    while j < api:                      # global flags between the client and `api`
        if tokens[j] in ("-R", "--repo") and j + 1 < api:
            repo = tokens[j + 1]
            j += 2
            continue
        if tokens[j].startswith("--repo="):
            repo = tokens[j].split("=", 1)[1]
        j += 1
    i, n = api + 1, len(tokens)
    while i < n and tokens[i] not in _SHELL_OPERATOR_TOKENS:
        tok = tokens[i]
        if tok.startswith("-X") and len(tok) > 2:
            method = tok[2:]
        elif tok.startswith("--method="):
            method = tok.split("=", 1)[1]
        elif tok.split("=", 1)[0] in _REST_API_FIELD_FLAGS and "=" in tok and tok.startswith("--"):
            fields.append(tok.split("=", 1)[1])
        elif tok[:2] in ("-f", "-F") and len(tok) > 2 and not tok.startswith("--"):
            fields.append(tok[2:])
        elif tok.startswith("-") and tok != "-":
            if tok.startswith("--repo="):
                repo = tok.split("=", 1)[1]
            if tok in _REST_API_VALUE_FLAGS and i + 1 < n:
                if tok in ("-X", "--method"):
                    method = tokens[i + 1]
                elif tok in _REST_API_FIELD_FLAGS:
                    fields.append(tokens[i + 1])
                elif tok in ("-R", "--repo"):
                    repo = tokens[i + 1]
                i += 2
                continue
        elif endpoint is None:
            endpoint = tok
        i += 1
    if method is None or endpoint is None:
        return None
    path = endpoint.split("?", 1)[0].rstrip("/")
    return method.upper(), path, fields, repo


def _field_is(fields: "list[str]", key: str, value: str) -> bool:
    return any(f.split("=", 1) == [key, value] for f in fields)


def _gitlab_api_leg_op(leg: str) -> "tuple[str, str, str] | None":
    """(op, context key, target) of a destructive GitLab REST call made with
    `glab api` in one leg, else None. The same helper recognizes (detect) and
    extracts (extract_command_context), so a leg classifies IFF its target is
    extractable: no gated-but-unapprovable state."""
    if not re.search(_GLAB_API_PREFIX, leg, re.IGNORECASE):
        return None
    parts = _rest_api_leg_parts(leg, "glab")
    if parts is None:
        return None
    method, path, fields, repo = parts
    # amend_2: every target is qualified by its project (see _gitlab_project_identity);
    # an unresolvable project keeps the op (it still gates) with a None target (no mint).
    project = None
    pm = re.search(r"(?:^|/)projects/([^/]+)/", path)
    if pm:
        project = _gitlab_project_identity(pm.group(1), repo)

    def _q(sep: str, target: "str | None") -> "str | None":
        return None if project is None or target is None else "api:" + project + sep + target

    m = re.search(r"(?:^|/)merge_requests/(\d+)/merge$", path)
    if m and method in ("PUT", "POST"):
        return "merge", "mr_target", _q("!", m.group(1))
    m = re.search(r"(?:^|/)merge_requests/(\d+)$", path)
    if m and method == "DELETE":
        return "mr-delete", "mr_target", _q("!", m.group(1))
    if m and method == "PUT" and _field_is(fields, "state_event", "close"):
        return "close", "mr_target", _q("!", m.group(1))
    m = re.search(r"(?:^|/)repository/branches/(.+)$", path)
    if m and method == "DELETE":
        return "branch-delete", "branch", _q(":", _gitlab_branch_segment(m.group(1)))
    m = re.search(r"(?:^|/)protected_branches/(.+)$", path)
    if m and method == "DELETE":
        return "branch-protection", "protected_branch", _q(":", _gitlab_branch_segment(m.group(1)))
    return None


def _gh_api_close_leg_pr(leg: str) -> str | None:
    """The PR number of a GitHub REST PR close (`gh api -X PATCH .../pulls/<N> -f
    state=closed`) in one leg, else None. Recognition and extraction are one helper."""
    if not re.search(_GH_API_PREFIX, leg, re.IGNORECASE):
        return None
    parts = _rest_api_leg_parts(leg, "gh")
    if parts is None:
        return None
    method, path, fields, _repo = parts
    m = re.search(r"(?:^|/)pulls/(\d+)$", path)
    if m and method == "PATCH" and _field_is(fields, "state", "closed"):
        return m.group(1)
    return None


def _uses_raw_http_client(command: str) -> bool:
    """True when a leg of `command` runs curl or wget (amend_7 on #141: their REST ref and
    protection calls are gated but never minted, the existing curl-merge posture)."""
    tokens = _shell_tokenize(command)
    if tokens is None:
        return bool(re.search(r"(?:^|[\s;&|(])(?:\S*/)?(?:curl|wget)(?=\s|$)", command))
    return any(os.path.basename(tok) in ("curl", "wget") for tok in tokens)


def _api_ref_identity(command: str) -> "tuple[bool, str | None]":
    """(is an API ref form, its identity) for a `git/refs/<ref>` call. gh api binds
    `api:<owner>/<repo>:<ref>` (amend_4/amend_6 on #141); curl/wget are recognized but
    never minted (amend_7). (False, None) for anything else (a CLI form: its own parser)."""
    if "git/refs/" not in command:
        return False, None
    if _uses_raw_http_client(command):
        return True, None
    if re.search(_GH_API_PREFIX, command, re.IGNORECASE):
        return True, _gh_rest_ref_identity(command)
    return False, None


def _gh_ref_segment(raw: str) -> "str | None":
    """The ref half of a GitHub REST ref identity from the path after `git/refs/`: a
    leading `heads/` is dropped (the pre-existing ref shape), `heads/{branch}` is the
    checked-out branch (None when unreadable, e.g. a detached HEAD), and anything else
    outside the plain ref shape (another placeholder, a brace, a query) gives None."""
    if raw == "heads/{branch}":
        return _current_branch(_remotes._INVOCATION_CWD or os.getcwd())
    m = re.fullmatch(r"(?:heads/)?([A-Za-z0-9][A-Za-z0-9._/-]*)", raw)
    return m.group(1) if m else None


_GH_REF_PATH_RE = re.compile(r"(?:^|[/\s'\"])repos/([^/\s'\"]+)/([^/\s'\"]+)/git/refs/([^\s'\"?#]+)")


def _gh_rest_ref_identity(command: str) -> "str | None":
    """amend_4/amend_6: `api:<owner>/<repo>:<ref>` for a GitHub REST ref delete or
    mutation (`gh api -X DELETE|PATCH repos/o/r/git/refs/heads/x`), like the GitLab REST
    branch target; None (gated, nothing mints) when the repo or ref is an unresolvable
    placeholder or cannot be parsed."""
    # The parsed endpoint first: a field value that happens to hold another ref path
    # cannot stand in for it. The raw-text fallback serves forms the parser declines.
    parts = _rest_api_leg_parts(command, "gh")
    m = _GH_REF_PATH_RE.search(parts[1]) if parts is not None else None
    if m is None:
        m = _GH_REF_PATH_RE.search(command)
    if m is None:
        return None
    repo = _gh_repo_identity(m.group(1), m.group(2))
    ref = _gh_ref_segment(m.group(3))
    return None if repo is None or ref is None else "api:" + repo + ":" + ref


_GH_PROTECTION_PATH_RE = re.compile(
    r"(?:^|[/\s'\"])repos/([^/\s'\"]+)/([^/\s'\"]+)/branches/(.+?)/protection(?:/|$|(?=[\s'\"?#]))")


def _gh_rest_protection_identity(command: str) -> "str | None":
    """amend_6: `api:<owner>/<repo>:<branch>` for a GitHub REST protection removal
    (DELETE/PUT/PATCH on `repos/o/r/branches/<b>/protection[/...]`). `{owner}/{repo}` is
    the current repo and `{branch}` the checked-out branch; any other placeholder, or an
    unresolvable one, gives None (gated, nothing mints)."""
    parts = _rest_api_leg_parts(command, "gh")
    m = _GH_PROTECTION_PATH_RE.search(parts[1]) if parts is not None else None
    if m is None:
        m = _GH_PROTECTION_PATH_RE.search(command)
    if m is None:
        return None
    repo = _gh_repo_identity(m.group(1), m.group(2))
    raw = _strip_surrounding_quotes(m.group(3))
    if raw == "{branch}":
        branch = _current_branch(_remotes._INVOCATION_CWD or os.getcwd())
    elif "{" in raw or "}" in raw:
        branch = None
    else:
        branch = raw
    return None if repo is None or not branch else "api:" + repo + ":" + branch


def _gh_rest_pr_identity(path: str, pr: str) -> "str | None":
    """amend_2: `api:<owner>/<repo>#<N>` for a GitHub REST PR path (`repos/o/r/pulls/N...`),
    or None when the repo is absent or an unresolvable placeholder (then nothing mints)."""
    m = re.search(r"(?:^|/)repos/([^/\s]+)/([^/\s]+)/pulls/" + re.escape(pr) + r"(?:/|$)", path)
    if not m:
        return None
    repo = _gh_repo_identity(m.group(1), m.group(2))
    return None if repo is None else "api:" + repo + "#" + pr
