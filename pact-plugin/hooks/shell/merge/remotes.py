"""
Location: pact-plugin/hooks/shell/merge/remotes.py
Summary: Repository identity: the invocation cwd, origin remotes, GitHub and GitLab identity,
         CLI repo scope and REST host.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: _INVOCATION_CWD, set_invocation_cwd, _origin_remote_path, _origin_remote, _origin_url_from_config, _remote_url_path, _remote_url_host, _GLAB_PLACEHOLDER_RE, _gitlab_project_identity, _gitlab_branch_segment, _gh_repo_identity, _git_dir_of, _current_branch, _CLI_MR_OP_RES, _SHELL_C_HEADS, _MAX_REPO_WRAP_DEPTH, _split_repo_ref, _walk_to_leg, _is_cli_mr_leg, _REPO_VARS, _HOST_VARS, _DEFAULT_HOST, _REPO_REDIRECT_VARS, _HOST_VAR_NAMES, _CONFIG_DIR_VARS, _exported, _inline_vars, _cli_repo_scope, _REST_PLACEHOLDER_RE, _is_rest_leg, _hostname_flag, _rest_endpoint, _rest_host.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from pathlib import Path

from shell.merge.arms import (_GH_API_PREFIX, _GH_PR_CLOSE_RE, _GH_PR_MERGE_RE, _GLAB_API_PREFIX, _GLAB_MR_CLOSE_RE, _GLAB_MR_DELETE_RE, _GLAB_MR_MERGE_RE, _REST_API_VALUE_FLAGS, _SHELL_OPERATOR_TOKENS)
from shell.merge.views import (_shell_tokenize, _slice_stripped_legs)
from shell.words import normalize_line_continuations as _normalize_line_continuations

# --- GitLab targets: glab MRs, GitLab REST via glab api, GitHub REST close; every gated arm
# has an extractor ---

# REST identities are project-qualified: !42 in one project never authorizes !42 in another.
# A placeholder project ({owner}/{repo}, :fullpath) means the current repo.
_INVOCATION_CWD: "str | None" = None


def set_invocation_cwd(path: "str | None") -> None:
    """Record the directory REST placeholders resolve against (the hook payload's cwd).
    None restores the default, the process cwd."""
    global _INVOCATION_CWD
    _INVOCATION_CWD = path or None


def _origin_remote_path(start: "str | None" = None) -> "str | None":
    """The `owner/repo` (GitHub) or `group/.../project` (GitLab) path of the origin remote
    of the git repo containing `start` (default: the invocation cwd). None when there is no
    repo, no origin, or an unparseable url: the caller then refuses to mint."""
    origin = _origin_remote(start)
    return origin[1] if origin else None


def _origin_remote(start: "str | None" = None) -> "tuple[str, str] | None":
    """(host, path) of the origin remote of the git repo containing `start` (default: the
    invocation cwd), read from the git config on disk (worktree `.git` files and
    `commondir` honoured). None when there is no repo, no origin, or an unparseable url."""
    try:
        here = Path(start or _INVOCATION_CWD or os.getcwd()).resolve()
    except OSError:
        return None
    for directory in (here, *here.parents):
        dot_git = directory / ".git"
        if dot_git.is_dir():
            git_dir = dot_git
        elif dot_git.is_file():
            try:
                line = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not line.startswith("gitdir:"):
                return None
            git_dir = (directory / line.split(":", 1)[1].strip()).resolve()
        else:
            continue
        common = git_dir
        commondir = git_dir / "commondir"
        if commondir.is_file():
            try:
                common = (git_dir / commondir.read_text(encoding="utf-8").strip()).resolve()
            except OSError:
                return None
        try:
            config = (common / "config").read_text(encoding="utf-8")
        except OSError:
            return None
        url = _origin_url_from_config(config)
        host, path = _remote_url_host(url), _remote_url_path(url)
        return (host, path) if host and path else None
    return None


def _origin_url_from_config(config: str) -> "str | None":
    in_origin = False
    for raw in config.splitlines():
        line = raw.strip()
        if line.startswith("["):
            in_origin = line.replace(" ", "") in ('[remote"origin"]',)
            continue
        if in_origin and "=" in line:
            key, _, value = line.partition("=")
            if key.strip() == "url":
                return value.strip()
    return None


def _remote_url_path(url: "str | None") -> "str | None":
    """`git@host:g/p.git` / `https://host/g/p.git` / `ssh://git@host/g/p` -> `g/p`."""
    if not url:
        return None
    if "://" in url:
        rest = url.split("://", 1)[1]
        path = rest.split("/", 1)[1] if "/" in rest else ""
    elif ":" in url:
        path = url.split(":", 1)[1]
    else:
        return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path if "/" in path else None


def _remote_url_host(url: "str | None") -> "str | None":
    """`git@host:g/p.git` / `https://user@host:443/g/p.git` / `ssh://git@host/g/p` -> `host`."""
    if not url:
        return None
    if "://" in url:
        authority = url.split("://", 1)[1].split("/", 1)[0]
    elif ":" in url:
        authority = url.split(":", 1)[0]
    else:
        return None
    host = authority.rsplit("@", 1)[-1].split(":", 1)[0].lower()
    return host or None


# glab expands these in an `api` endpoint as written (a `%3A` spelling is not expanded).
_GLAB_PLACEHOLDER_RE = re.compile(r":(?:fullpath|group|id|namespace|repo|user|username|branch)\b")


def _gitlab_project_identity(segment: str, repo_flag: "str | None") -> "str | None":
    """The project half of a GitLab REST identity: a URL-encoded path (`g%2Fp` -> `g/p`),
    a numeric id (`5` -> `id:5`, a different namespace: an id and a path never match),
    or `:fullpath` resolved to the current repo. Any other placeholder -> None."""
    if segment == ":fullpath":
        return repo_flag or _origin_remote_path()
    if segment.startswith(":") or not segment or _GLAB_PLACEHOLDER_RE.search(segment):
        return None
    if segment.isdigit():
        return "id:" + segment
    return urllib.parse.unquote(segment)


def _gitlab_branch_segment(segment: str) -> "str | None":
    """The branch half of a GitLab REST branch identity: `:branch` is the current branch
    (None when it is unreadable, e.g. a detached HEAD); any other placeholder -> None; else
    the URL-decoded name (`%3Abranch` is a literal name, glab does not expand it)."""
    if segment == ":branch":
        return _current_branch(_INVOCATION_CWD or os.getcwd())
    if _GLAB_PLACEHOLDER_RE.search(segment):
        return None
    return urllib.parse.unquote(segment)


def _gh_repo_identity(owner: str, repo: str) -> "str | None":
    """The repo half of a GitHub REST identity: `o/r`, or `{owner}/{repo}` resolved to the
    current repo's origin. A lone placeholder, or an unresolvable one, -> None."""
    if owner == "{owner}" and repo == "{repo}":
        return _origin_remote_path()
    if "{" in owner or "{" in repo:
        return None
    return owner + "/" + repo


def _git_dir_of(start: str) -> "Path | None":
    """The per-worktree git dir of the repo containing `start` (None outside a repo)."""
    try:
        here = Path(start).resolve()
    except OSError:
        return None
    for directory in (here, *here.parents):
        dot_git = directory / ".git"
        if dot_git.is_dir():
            return dot_git
        if dot_git.is_file():
            try:
                line = dot_git.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if not line.startswith("gitdir:"):
                return None
            return (directory / line.split(":", 1)[1].strip()).resolve()
    return None


def _current_branch(start: str) -> "str | None":
    """The checked-out branch of the repo containing `start`, from HEAD (None if detached)."""
    git_dir = _git_dir_of(start)
    if git_dir is None:
        return None
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    return head[len(prefix):] if head.startswith(prefix) else None


# --- CLI repo binding: a CLI merge/close/mr-delete binds its repo (cwd, -R/--repo, GH_REPO) ---
_CLI_MR_OP_RES = (_GH_PR_MERGE_RE, _GH_PR_CLOSE_RE, _GLAB_MR_MERGE_RE, _GLAB_MR_CLOSE_RE, _GLAB_MR_DELETE_RE)
_SHELL_C_HEADS = frozenset({"bash", "sh", "zsh", "dash"})
_MAX_REPO_WRAP_DEPTH = 3


def _split_repo_ref(ref: str) -> "tuple[str | None, str] | None":
    """(host or None, path) of a repo reference: `o/r` -> (None, `o/r`); `host/o/r`,
    `https://host/o/r(.git)` and `git@host:o/r` -> (`host`, `o/r`). A GitLab
    `group/sub/project` keeps every path segment. None when it is not a repo reference."""
    ref = ref.strip().strip("'\"")
    if "://" in ref or ref.startswith("git@"):
        host, path = _remote_url_host(ref), _remote_url_path(ref)
        return (host, path) if host and path else None
    ref = ref.strip("/")
    if ref.endswith(".git"):
        ref = ref[:-4]
    parts = ref.split("/")
    host = None
    if len(parts) >= 3 and "." in parts[0]:
        host, parts = parts[0].lower(), parts[1:]
    return (host, "/".join(parts)) if len(parts) >= 2 and all(parts) else None


def _walk_to_leg(command: str, start: str, is_target_leg, _depth: int = 0) -> "tuple | None":
    """(directory, tokens, k) of the first leg of `command` that `is_target_leg` accepts: the
    directory the `cd`/`pushd` legs before it reach from `start`, its tokens, and the index of
    its command word after any `VAR=value` prefixes.
    """
    legs = _slice_stripped_legs(_normalize_line_continuations(command))
    directory = start
    for leg in legs:
        tokens = _shell_tokenize(leg.strip())
        if tokens is None:
            return None
        if not tokens:
            continue
        if is_target_leg(leg):
            k = 0
            while k < len(tokens) and re.match(r"^[A-Za-z_]\w*=", tokens[k]):
                k += 1
            head = os.path.basename(tokens[k]) if k < len(tokens) else ""
            if head in _SHELL_C_HEADS and k + 2 < len(tokens) and tokens[k + 1] == "-c":
                if _depth >= _MAX_REPO_WRAP_DEPTH:
                    return None
                return _walk_to_leg(tokens[k + 2], directory, is_target_leg, _depth + 1)
            return directory, tokens, k
        head = tokens[0]
        if head in ("cd", "pushd"):
            if len(tokens) == 1:
                directory = os.path.expanduser("~")
                continue
            arg = tokens[1]
            if arg.startswith("-") or "$" in arg or "`" in arg:
                return None
            directory = os.path.normpath(os.path.join(directory, os.path.expanduser(arg)))
            if not os.path.isdir(directory):
                return None
        elif head in ("popd",) or head.startswith("(") or "cd" in tokens[1:] or "pushd" in tokens[1:]:
            return None
    return None


def _is_cli_mr_leg(leg: str) -> bool:
    return any(verb.search(leg) for verb in _CLI_MR_OP_RES)


# amend_5 (#141): the variables gh/glab read to pick a repo or a host. Inline on the leg
# they are refused (a host, or a repo variable other than gh's own GH_REPO); exported in
# the hook's environment they are bound, or refused when they disagree with the checkout.
_REPO_VARS = {"gh": ("GH_REPO",), "glab": ("GITLAB_REPO", "GL_REPO")}
_HOST_VARS = {"gh": ("GH_HOST",), "glab": ("GITLAB_HOST", "GL_HOST")}
_DEFAULT_HOST = {"gh": "github.com", "glab": "gitlab.com"}
_REPO_REDIRECT_VARS = frozenset(v for names in (*_REPO_VARS.values(), *_HOST_VARS.values()) for v in names)
_HOST_VAR_NAMES = frozenset(v for names in _HOST_VARS.values() for v in names)
# An inline config dir switches the auth config, and with it the default host (S1, rev 5).
_CONFIG_DIR_VARS = frozenset({"GH_CONFIG_DIR", "GLAB_CONFIG_DIR"})


def _exported(names: "tuple[str, ...]") -> "str | None":
    return next((os.environ[n] for n in names if os.environ.get(n)), None)


def _inline_vars(tokens: "list[str]", k: int) -> "dict[str, str]":
    return dict(a.split("=", 1) for a in tokens[:k])


def _cli_repo_scope(command: str, start: str) -> "tuple[str, str, str, str] | None":
    """(repo path, directory, source, host) the CLI MR/PR op in `command` acts on."""
    walked = _walk_to_leg(command, start, _is_cli_mr_leg)
    if walked is None:
        return None
    directory, tokens, k = walked
    tool = os.path.basename(tokens[k])
    if tool not in ("gh", "glab"):
        return None
    inline = _inline_vars(tokens, k)
    if any((name in _REPO_REDIRECT_VARS or name in _CONFIG_DIR_VARS)
           and not (tool == "gh" and name == "GH_REPO") for name in inline):
        return None
    flag_repo = None
    for j in range(k + 1, len(tokens)):
        if tokens[j] in ("-R", "--repo") and j + 1 < len(tokens):
            flag_repo = tokens[j + 1]
        elif tokens[j].startswith("--repo="):
            flag_repo = tokens[j].split("=", 1)[1]
        elif tokens[j].startswith("-R") and len(tokens[j]) > 2 and not tokens[j].startswith("--"):
            flag_repo = tokens[j][2:].lstrip("=")
    env_host = _exported(_HOST_VARS[tool])
    origin = _origin_remote(directory)

    def _host(own: "str | None") -> str:
        return own or env_host or (origin[0] if origin else None) or _DEFAULT_HOST[tool]

    named, source = None, None
    if flag_repo is not None:
        named, source = flag_repo, "flag"
    elif "GH_REPO" in inline:
        named, source = inline["GH_REPO"], "env"
    if named is not None:
        ref = _split_repo_ref(named)
        return (ref[1], directory, source, _host(ref[0])) if ref else None
    exported_repo = _exported(_REPO_VARS[tool])
    if exported_repo is not None:
        ref = _split_repo_ref(exported_repo)
        if ref is None or (origin and (origin[1] != ref[1] or (ref[0] and ref[0] != origin[0]))):
            return None
        return ref[1], directory, "env", _host(ref[0])
    if origin is None or (env_host and env_host != origin[0]):
        return None
    return origin[1], directory, "cwd", origin[0]


# A REST placeholder expands against the repo/branch the call RUNS in.
_REST_PLACEHOLDER_RE = re.compile(_GLAB_PLACEHOLDER_RE.pattern + r"|\{(?:owner|repo|branch)\}")


def _is_rest_leg(leg: str) -> bool:
    return bool(re.search(_GLAB_API_PREFIX, leg, re.IGNORECASE) or re.search(_GH_API_PREFIX, leg, re.IGNORECASE))


def _hostname_flag(tokens: "list[str]", k: int) -> "str | None":
    for j in range(k + 1, len(tokens)):
        if tokens[j] == "--hostname" and j + 1 < len(tokens):
            return tokens[j + 1].lower()
        if tokens[j].startswith("--hostname="):
            return tokens[j].split("=", 1)[1].lower()
    return None


def _rest_endpoint(tokens: "list[str]", k: int) -> "str | None":
    """The endpoint positional of a `gh|glab [globals] api ...` leg (the first argument
    after `api` that is neither a flag nor a flag's value), or None."""
    try:
        i = tokens.index("api", k + 1) + 1
    except ValueError:
        return None
    while i < len(tokens) and tokens[i] not in _SHELL_OPERATOR_TOKENS:
        tok = tokens[i]
        if tok.startswith("-") and tok != "-":
            i += 2 if (tok in _REST_API_VALUE_FLAGS) else 1
            continue
        return tok
    return None


def _rest_host(command: str, start: str) -> "str | None":
    """The host a REST identity binds (amend_5), or None when the call cannot be pinned."""
    walked = _walk_to_leg(command, start, _is_rest_leg)
    if walked is None:
        return None
    directory, tokens, k = walked
    tool = os.path.basename(tokens[k])
    if tool not in ("gh", "glab"):
        return None
    inline = _inline_vars(tokens, k)
    if any(name in _HOST_VAR_NAMES or name in _CONFIG_DIR_VARS for name in inline):
        return None
    env_host = _exported(_HOST_VARS[tool])
    if _REST_PLACEHOLDER_RE.search(command):
        if any(name in _REPO_REDIRECT_VARS for name in inline):
            return None
        origin = _origin_remote(start)
        if os.path.normpath(directory) != os.path.normpath(start):
            here = (origin, _current_branch(start))
            if origin is None or here != (_origin_remote(directory), _current_branch(directory)):
                return None
        exported_repo = _exported(_REPO_VARS[tool])
        if exported_repo is not None:
            ref = _split_repo_ref(exported_repo)
            if ref is None or origin is None or ref[1] != origin[1] or (ref[0] and ref[0] != origin[0]):
                return None
        if env_host and origin and env_host != origin[0]:
            return None
    hostname = _hostname_flag(tokens, k)
    endpoint = _rest_endpoint(tokens, k)
    if endpoint is not None and "://" in endpoint:
        url_host = urllib.parse.urlsplit(endpoint).hostname
        if not url_host or (hostname and hostname != url_host):
            return None
        return url_host
    reached = _origin_remote(directory)
    return hostname or env_host or (reached[0] if reached else None) or _DEFAULT_HOST[tool]
