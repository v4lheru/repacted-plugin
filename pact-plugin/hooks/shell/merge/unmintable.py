"""
Location: pact-plugin/hooks/shell/merge/unmintable.py
Summary: unmintable_danger: shapes that are gated but can never mint a token (raw HTTP,
         GraphQL mutations, aliases, flag-before-verb).
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: _api_call_parts, _graphql_danger, _api_body_danger, _raw_http_danger, _GROUP_VERBS, _ALIAS_DANGER_WORDS, _flag_before_verb_danger, _inline_alias_danger.
"""

from __future__ import annotations

import re
import urllib.parse

from shell.merge.arms import (_BODY_SENSITIVE_ENDPOINT_RE, _GITHUB_PR_RE, _GITLAB_BRANCH_RE, _GITLAB_MR_RE, _GRAPHQL_DESTRUCTIVE_FIELD_RE, _GRAPHQL_MUTATION_RE, _RAW_HTTP_BODY_FLAGS, _RAW_HTTP_FILE_FLAGS, _RAW_HTTP_METHOD_FLAGS, _REST_API_FIELD_FLAGS, _REST_API_VALUE_FLAGS)

def _api_call_parts(tokens: "list[str]") -> "tuple[str | None, str | None, list[str], list[str]]":
    """(method, endpoint, field values, --input values) of a `gh|glab ... api ...` token list."""
    method, endpoint, fields, inputs = None, None, [], []
    try:
        i = tokens.index("api") + 1
    except ValueError:
        return None, None, [], []
    while i < len(tokens):
        tok = tokens[i]
        name, eq, value = tok.partition("=")
        if tok in ("-X", "--method") and i + 1 < len(tokens):
            method, i = tokens[i + 1], i + 2
            continue
        if tok.startswith("-X") and len(tok) > 2:
            method = tok[2:]
        elif name == "--method" and eq:
            method = value
        elif tok in _REST_API_FIELD_FLAGS and i + 1 < len(tokens):
            fields.append(tokens[i + 1])
            i += 2
            continue
        elif name in _REST_API_FIELD_FLAGS and eq and tok.startswith("--"):
            fields.append(value)
        elif tok[:2] in ("-f", "-F") and len(tok) > 2 and not tok.startswith("--"):
            fields.append(tok[2:])
        elif tok == "--input" and i + 1 < len(tokens):
            inputs.append(tokens[i + 1])
            i += 2
            continue
        elif name == "--input" and eq:
            inputs.append(value)
        elif tok in _REST_API_VALUE_FLAGS and i + 1 < len(tokens):
            i += 2
            continue
        elif not tok.startswith("-") and endpoint is None:
            endpoint = tok
        i += 1
    return (method.upper() if method else None), endpoint, fields, inputs


def _graphql_danger(fields: "list[str]", inputs: "list[str]") -> "str | None":
    if inputs:
        return "a GraphQL request whose body is read from a file or stdin"
    for field in fields:
        key, _, value = field.partition("=")
        if key != "query":
            continue
        if value.startswith("@") or "$" in value or "`" in value:
            return "a GraphQL request whose query is not on the command line"
        if _GRAPHQL_MUTATION_RE.search(value) and _GRAPHQL_DESTRUCTIVE_FIELD_RE.search(value):
            return "a GraphQL mutation that merges, closes or deletes"
    return None


def _api_body_danger(tokens: "list[str]") -> "str | None":
    method, endpoint, fields, inputs = _api_call_parts(tokens)
    if endpoint is None:
        return None
    if endpoint.rstrip("/").split("?", 1)[0].endswith("graphql"):
        return _graphql_danger(fields, inputs)
    off_line = inputs or any("=@" in field for field in fields)
    if not off_line or method in ("GET", "HEAD"):
        return None
    path = endpoint.split("?", 1)[0].rstrip("/")
    if "$" in path or _BODY_SENSITIVE_ENDPOINT_RE.search(path):
        return "an API call on a pull request, merge request or branch whose body is not on the command line"
    return None


def _raw_http_danger(tokens: "list[str]") -> "str | None":
    """curl/wget: delete a GitLab MR, branch or protection, or close a GitLab MR / GitHub PR."""
    method, urls, body, body_off_line = None, [], [], False
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        name, eq, value = tok.partition("=")
        if tok in _RAW_HTTP_METHOD_FLAGS and i + 1 < len(tokens):
            method, i = tokens[i + 1], i + 2
            continue
        if tok.startswith("-X") and len(tok) > 2:
            method = tok[2:]
        elif name in ("--request", "--method") and eq:
            method = value
        elif tok in _RAW_HTTP_BODY_FLAGS and i + 1 < len(tokens):
            value = tokens[i + 1]
            body.append(value)
            body_off_line |= tok in _RAW_HTTP_FILE_FLAGS or value.startswith("@") or "=@" in value
            i += 2
            continue
        elif name in _RAW_HTTP_BODY_FLAGS and eq and tok.startswith("--"):
            body.append(value)
            body_off_line |= name in _RAW_HTTP_FILE_FLAGS or value.startswith("@")
        elif tok[:2] in ("-d", "-F", "-T") and len(tok) > 2 and not tok.startswith("--"):
            body.append(tok[2:])
            body_off_line |= tok[:2] == "-T" or tok[2:].startswith("@")
        elif "://" in tok:
            urls.append(tok)
        i += 1
    method = (method or "").upper()
    for url in urls:
        parts = urllib.parse.urlsplit(url)
        path = parts.path.rstrip("/")                  # a branch segment stays URL-encoded
        text = " ".join(body) + " " + urllib.parse.unquote(parts.query)
        if method == "DELETE" and (_GITLAB_MR_RE.search(path) or _GITLAB_BRANCH_RE.search(path)):
            return "a raw HTTP call that deletes a merge request or branch"
        closing = re.search(r"state_event=close\b|\"state\"\s*:\s*\"closed\"|state=closed\b", text)
        if method == "PUT" and _GITLAB_MR_RE.search(path) and (closing or body_off_line):
            return "a raw HTTP call that closes a merge request"
        if method == "PATCH" and _GITHUB_PR_RE.search(path) and (closing or body_off_line):
            return "a raw HTTP call that closes a pull request"
    return None


# The verbs of `gh pr` / `glab mr` the arms gate, for a flag written before the verb.
_GROUP_VERBS = {"gh": ("pr", frozenset({"merge", "close"})),
                "glab": ("mr", frozenset({"merge", "accept", "close", "delete", "del"}))}
# git subcommands an inline alias can stand for that the arms gate.
_ALIAS_DANGER_WORDS = frozenset({"push", "branch", "update-ref"})


def _flag_before_verb_danger(program: str, tokens: "list[str]") -> "str | None":
    """`gh pr --repo o/r merge 5`: the CLI takes a flag between the group and its verb, which
    the arms (written `pr merge`) do not read."""
    group, verbs = _GROUP_VERBS[program]
    j = 1
    while j < len(tokens) and tokens[j].startswith("-"):     # global flags before the group
        j += 2 if tokens[j] in ("-R", "--repo") else 1
    if j + 1 >= len(tokens) or tokens[j] != group or not tokens[j + 1].startswith("-"):
        return None
    j += 1
    while j < len(tokens) and tokens[j].startswith("-"):
        j += 2 if tokens[j] in ("-R", "--repo") else 1
    if j < len(tokens) and tokens[j] in verbs:
        return f"a `{program} {group}` command with a flag before its verb"
    return None


def _inline_alias_danger(tokens: "list[str]") -> "str | None":
    """`git -c alias.p=push p -f origin x`: a subcommand defined in the same command, which
    the arms (written `git push`) do not read. A shell alias (`!...`) can run anything."""
    aliases, j = {}, 1
    while j < len(tokens) and tokens[j].startswith("-"):
        if tokens[j] == "-c" and j + 1 < len(tokens):
            name, _, value = tokens[j + 1].partition("=")
            if name.lower().startswith("alias."):
                aliases[name[6:].lower()] = value.strip()
            j += 2
        else:
            j += 2 if tokens[j] in ("-C", "--git-dir", "--work-tree", "--namespace") else 1
    word, seen = (tokens[j].lower() if j < len(tokens) else ""), set()
    while word in aliases and word not in seen:            # an alias may name another alias
        seen.add(word)
        value = aliases[word]
        if value.startswith("!"):
            return "a git command run through a shell alias defined with -c alias.*"
        word = value.split()[0].lower() if value.split() else ""
    if seen and word in _ALIAS_DANGER_WORDS:
        return "a git command run through an alias defined with -c alias.*"
    return None
