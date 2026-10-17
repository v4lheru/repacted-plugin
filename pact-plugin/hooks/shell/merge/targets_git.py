"""
Location: pact-plugin/hooks/shell/merge/targets_git.py
Summary: git ref targets: branch names and sets, force-push and push-to-main sets, remote
         ref and mass-delete targets, implicit force-push identity.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: _gitlab_context_target, _extract_api_close_pr, _extract_protection_branch, _extract_branch_name, _canonical_join, _extract_branch_delete_set, _extract_force_push_target_ref, _extract_push_to_main_set, _FORCE_PUSH_NEUTRAL_LONG, _FORCE_PUSH_NEUTRAL_SHORT, _resolve_neutral_force_tail, _extract_force_push_set, _GIT_PUSH_VALUE_FLAGS, _push_positionals, _tokens_after_push, _FORCE_PUSH_IMPLICIT_SENTINEL, _MERGE_IMPLICIT_SENTINEL, _implicit_force_push_identity, _extract_remote_ref_delete_target, _IMPLICIT_REMOTE_MARKER, _extract_mass_delete_target.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from shell.merge.arms import (_GH_API_PREFIX, _GIT_PREFIX, _GLAB_MR_CLOSE_RE, _GLAB_MR_DELETE_RE, _GLAB_MR_MERGE_RE, _MAX_GLOBAL_FLAG_TOKENS)
from shell.merge.flags import (_normalized_flags)
from shell.merge.strip import (_split_into_legs)
from shell.merge.targets import (_api_ref_identity, _gh_api_close_leg_pr, _gh_rest_pr_identity, _gh_rest_protection_identity, _gitlab_api_leg_op, _glab_mr_leg_target, _rest_api_leg_parts, _uses_raw_http_client)
from shell.merge.views import (_executable_prefix, _executed_surface_view, _shell_tokenize, _slice_stripped_legs, _strip_surrounding_quotes)
from shell.words import normalize_line_continuations as _normalize_line_continuations

def _gitlab_context_target(command: str, op_type: str) -> "tuple[str, str | None] | None":
    """(context key, target or None) when `command` is a GitLab form of `op_type`
    (glab CLI or `glab api`), else None (not a GitLab form: the gh/git paths apply).
    Leg-local: the first leg that is a glab MR call or a GitLab REST call decides."""
    for leg in _split_into_legs(command):
        if op_type in ("merge", "close", "mr-delete"):
            verb_re = {"merge": _GLAB_MR_MERGE_RE, "close": _GLAB_MR_CLOSE_RE,
                       "mr-delete": _GLAB_MR_DELETE_RE}[op_type]
            if verb_re.search(_executed_surface_view(leg)) or verb_re.search(leg):
                is_glab, target = _glab_mr_leg_target(leg)
                if is_glab:
                    return "mr_target", target
        hit = _gitlab_api_leg_op(leg)
        if hit is not None and hit[0] == op_type:
            return hit[1], hit[2]           # target None: unresolvable project, no mint
    return None


def _extract_api_close_pr(command: str) -> str | None:
    """Command-level REST PR close identity, repo-qualified (`api:o/r#N`, amend_2), from
    the first close leg (FIX-MERGE f3). Raw legs, like its detect arm: the deciding
    `state=closed` is a body field the strip removes. None -> recognized but not approvable."""
    for _leg in _slice_stripped_legs(_normalize_line_continuations(command)):
        pr = _gh_api_close_leg_pr(_leg)
        if pr is not None:
            parts = _rest_api_leg_parts(_leg, "gh")
            return _gh_rest_pr_identity(parts[1], pr) if parts else None
    return None


def _extract_protection_branch(command: str) -> str | None:
    """The protected-branch identity of a branch-protection API command (#1063): the branch is
    PATH-resident (`branches/<branch>/protection[/...]`; the non-greedy parse keeps a slashed
    `feature/x`).
    """
    if _uses_raw_http_client(command):
        return None
    if re.search(_GH_API_PREFIX, command, re.IGNORECASE):
        return _gh_rest_protection_identity(command)
    return None


def _extract_branch_name(command: str) -> str | None:
    """Extract the SINGLE branch name targeted by a branch-delete command."""
    is_api, api_ref = _api_ref_identity(command)
    if is_api:
        return api_ref
    # CLI branch delete: exactly one positional name, so a one-branch token never authorizes a
    # multi-branch delete.
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    branch_match = re.search(_GIT_PREFIX + r"branch\b(.*)$", prefix)
    if not branch_match:
        return None
    positionals = [t for t in branch_match.group(1).split() if not t.startswith("-")]
    if len(positionals) != 1:
        return None
    return _strip_surrounding_quotes(positionals[0])


def _canonical_join(items: Sequence[str]) -> str:
    """Netstring/length-prefix encode a sequence of strings into ONE canonical identity STRING,
    injective by construction (a left-inverse decoder exists), so distinct sequences never
    collide REGARDLESS of item content — incl.
    """
    return "".join(f"{len(i)}:{i}" for i in items)


def _extract_branch_delete_set(command: str) -> str | None:
    """Extract the canonical MULTI-branch identity of a force-delete command."""
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    branch_match = re.search(_GIT_PREFIX + r"branch\b(.*)$", prefix)
    if not branch_match:
        return None
    positionals = [t for t in branch_match.group(1).split() if not t.startswith("-")]
    if len(positionals) < 2:
        return None
    names = sorted({_strip_surrounding_quotes(t) for t in positionals})
    # Netstring framing (_canonical_join) keeps the set identity injective whatever the names hold.
    return _canonical_join(names) or None


def _extract_force_push_target_ref(command: str) -> str | None:
    """Conservative force-push destination-ref parse (KD-6) — refuse on ambiguity."""
    # API ref-mutation: the destination ref is in the git/refs/<ref> path (amend_6/7 on
    # #141: repo-qualified for gh api, never minted for curl/wget).
    is_api, api_ref = _api_ref_identity(command)
    if is_api:
        return api_ref

    # CLI push: exactly remote + refspec; 0, 1 or more than 2 positionals are ambiguous and refuse.
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    push_match = re.search(_GIT_PREFIX + r"push\b(.*)$", prefix)
    if not push_match:
        return None
    after_push = _shell_tokenize(push_match.group(1))
    if after_push is None:                       # unbalanced quote -> abstain (safe over-block)
        return None
    positionals = _push_positionals(after_push)
    if len(positionals) != 2:
        return None
    refspec = _strip_surrounding_quotes(positionals[1])
    if ":" in refspec:
        return refspec.rsplit(":", 1)[1] or None
    return refspec or None


def _extract_push_to_main_set(command: str) -> str | None:
    """MULTI-ref sibling of the SCALAR push-to-main target (_extract_force_push_target_ref)."""
    prefix = _executable_prefix(command)
    if prefix is None:
        return None                     # fail-safe (unparseable / procsub)
    _pm = re.search(_GIT_PREFIX + r"push\s(.*)$", prefix)
    if _pm is None:
        return None                     # well-formed push only (excludes `push--force` glue)
    _tail = _pm.group(1).split()
    if sum(1 for t in _tail if t.startswith("-")) > _MAX_GLOBAL_FLAG_TOKENS:
        return None                     # perf bound (mirrors the OBS-D/E flag walk)
    refspecs = [_strip_surrounding_quotes(r) for r in _push_positionals(_tail)[1:]]
    if len(refspecs) < 2:
        return None                     # <2 -> the scalar target_ref path owns it (boundary)
    return _canonical_join(sorted(set(refspecs)))   # sort+dedup+canonical (branch_set mirror)


# Ref-scope-neutral flags for force_push_set: a security-owned allowlist; a test pins it.
_FORCE_PUSH_NEUTRAL_LONG = frozenset({
    "--force",                     # the op itself + the forced-form trigger
    "--no-verify",                 # bound via force-push PRIVILEGED_FLAGS (a2 discriminates)
    "--verbose", "--quiet", "--progress", "--no-progress", "--porcelain",  # output-only
    "--set-upstream",              # local tracking config
    "--atomic",                    # transaction semantics, same ref set
    "--ipv4", "--ipv6", "--thin", "--no-thin",                             # transport
    "--signed", "--no-signed",     # transport signing
    "--push-option",               # server-side message (value inert to ref scope)
})
_FORCE_PUSH_NEUTRAL_SHORT = frozenset("fvqu46")   # -f -v -q -u -4 -6 (cluster letters)


def _resolve_neutral_force_tail(_tail: list) -> "tuple[list, bool] | None":
    """ONE raw-token walk over the post-`push` tail (#1195 OBS-I): resolves git's getopt-style
    value-taking `-o` in ANY cluster position, runs the ref-scope-neutrality census, and detects
    the class-wide force flag — returning (resolved_tail, force) or None when ANY flag token is
    non-neutral, unknown, or malformed (fail-safe: gated-but-unmintable, never an over-broad
    token).
    """
    resolved: list = []
    force = False
    i, n = 0, len(_tail)
    while i < n:
        tok = _tail[i]
        if not tok.startswith("-") or tok == "-":
            resolved.append(tok)             # positional (or the bare '-' token)
            i += 1
            continue
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name not in _FORCE_PUSH_NEUTRAL_LONG:
                return None                  # incl. bare `--` + unknown/future flags
            if name == "--force":
                force = True
            if name == "--push-option" and "=" not in tok:
                if i + 1 >= n:
                    return None              # separate-value form missing its value
                i += 2                       # skip the value token
                continue
            i += 1
            continue
        # Short cluster: boolean letters until a value-`o`; at `o` the rest of the
        # token (or the next token when `o` is last) is its VALUE — never flags.
        consumed_next = False
        cluster_bad = False
        cluster_force = False
        for j in range(1, len(tok)):
            ch = tok[j]
            if ch == "o":
                if j == len(tok) - 1:        # `-...o` last: next token is the value
                    if i + 1 >= n:
                        cluster_bad = True   # missing value (malformed) — fail-safe
                    else:
                        consumed_next = True
                elif not cluster_force:
                    # o-first / no-f-before-o GLUED value (`-ofv`, `-vox`): git's
                    # parse and the cluster-blind detection diverge on WHAT THE
                    # OPERATION IS — ambiguous, fail SAFE to None (lead ruling).
                    cluster_bad = True
                break                        # rest-of-token (if any) is the value
            if ch not in _FORCE_PUSH_NEUTRAL_SHORT:
                cluster_bad = True
                break
            if ch == "f":
                force = True
                cluster_force = True
        if cluster_bad:
            return None
        i += 2 if consumed_next else 1
    return resolved, force


def _extract_force_push_set(command: str) -> str | None:
    """MULTI-ref sibling of the SCALAR force-push target — the force-push analog of
    _extract_push_to_main_set (#1195 OBS-I, reversing the OBS-G scope boundary under its own
    ratified proof).
    """
    prefix = _executable_prefix(command)
    if prefix is None:
        return None                     # fail-safe (unparseable / procsub)
    _pm = re.search(_GIT_PREFIX + r"push\s(.*)$", prefix)
    if _pm is None:
        return None                     # well-formed push only (excludes glue)
    _tail = _pm.group(1).split()
    if sum(1 for t in _tail if t.startswith("-")) > _MAX_GLOBAL_FLAG_TOKENS:
        return None                     # perf bound (mirrors the OBS-D/E flag walk)
    _resolved = _resolve_neutral_force_tail(_tail)
    if _resolved is None:
        return None                     # non-neutral / unknown / malformed flag
    _clean_tail, _force = _resolved
    refspecs = [_strip_surrounding_quotes(r) for r in _push_positionals(_clean_tail)[1:]]
    if len(refspecs) < 2:
        return None                     # <2 -> the scalar target_ref path owns it
    if _force:
        refspecs = [r if r.startswith("+") else "+" + r for r in refspecs]
    if any(r.lstrip("+").startswith(":") for r in refspecs):
        return None                     # delete-refspec guard (fail-safe)
    return _canonical_join(sorted(set(refspecs)))


# git push value options whose value is skipped when counting refspecs (else `-o ':x'` fakes a
# delete refspec).
_GIT_PUSH_VALUE_FLAGS = frozenset(
    {"-o", "--push-option", "--receive-pack", "--exec", "--repo"}
)


def _push_positionals(after_push: list[str]) -> list[str]:
    """Positional (non-flag) tokens after the `push` subcommand, skipping the value token consumed
    by a git-push value-taking option flag (`-o opt`, `--repo url`, …).
    """
    positionals: list[str] = []
    i, n = 0, len(after_push)
    while i < n:
        tok = after_push[i]
        if tok.startswith("-") and tok != "-":
            flag = tok.split("=", 1)[0]
            if flag in _GIT_PUSH_VALUE_FLAGS and "=" not in tok and i + 1 < n:
                i += 2  # consume the option flag's separate value token
                continue
            i += 1  # a flag (boolean, op-trigger, or inline-value) — not a positional
            continue
        positionals.append(tok)
        i += 1
    return positionals


def _tokens_after_push(command: str) -> list[str] | None:
    """The quote-aware token list AFTER the first `push` token, on the executable prefix of
    `command`, or None when the command is unparseable / has no `push` token.
    """
    prefix = _executable_prefix(command)
    if prefix is None:
        return None
    toks = _shell_tokenize(prefix)
    if toks is None:
        return None
    for idx, tok in enumerate(toks):
        if tok == "push":
            return toks[idx + 1:]
    return None


# Target-blind sentinels for implicit forms whose target is runtime state (bare force-push,
# bare gh pr merge); distinct from every explicit identity.
_FORCE_PUSH_IMPLICIT_SENTINEL = "\x00implicit-force-push"
_MERGE_IMPLICIT_SENTINEL = "\x00implicit-merge"


def _implicit_force_push_identity(command: str) -> str | None:
    """The `force_push_implicit` mint identity for a CLEAN implicit-current-branch force-push (SET
    A), or None (multi-ref / unparseable).
    """
    after = _tokens_after_push(command)
    if after is None:
        return None                               # no push token / unbalanced → abstain
    positionals = _push_positionals(after)
    if len(positionals) == 0:
        return _FORCE_PUSH_IMPLICIT_SENTINEL      # bare — remote ALSO runtime-unknowable
    if len(positionals) == 1:
        remote = _strip_surrounding_quotes(positionals[0])
        if not remote.strip():
            return None                           # degenerate empty/whitespace → abstain
        # A lone positional is the <repository> (remote, URL or refspec-shaped token), bound injectively.
        return _FORCE_PUSH_IMPLICIT_SENTINEL + _canonical_join([remote])
    return None                                   # >=2 positional → explicit-ref path owns it


def _extract_remote_ref_delete_target(command: str) -> str | None:
    """Extract the SINGLE remote ref a `git push` delete would remove (#1062a), or None when the
    form is implicit-current/multi-ref/ambiguous (→ the caller defers: not recognized as
    remote-ref-delete; a mass form is picked up by `_extract_mass_delete_target` instead).
    """
    after = _tokens_after_push(command)
    if after is None:
        return None
    positionals = _push_positionals(after)
    # Flags come from the same leg as the positionals, so a flag in a benign later leg cannot leak in.
    gf = _normalized_flags(after, "git")  # git surface maps -d → --delete
    if "--delete" in gf:
        # git grammar: 1 positional = the refspec (implicit remote); 2 = <repo>
        # <refspec>; 0 or ≥3 = ambiguous/multi → defer (mass handles ≥3).
        if len(positionals) == 1:
            return _strip_surrounding_quotes(positionals[0]) or None
        if len(positionals) == 2:
            return _strip_surrounding_quotes(positionals[1]) or None
        return None
    # No --delete flag: an empty-source colon refspec (`:dst` / `+:dst`) is a delete.
    # EXACTLY ONE → its dst; ≠1 → defer (0 = not a delete; ≥2 = multi → mass).
    colon_dsts = [p for p in positionals if re.match(r"^\+?:", p)]
    if len(colon_dsts) == 1:
        return _strip_surrounding_quotes(colon_dsts[0]).rsplit(":", 1)[1] or None
    return None


# Implicit-remote marker for `git push --mirror` with no remote; NUL never appears in a name.
_IMPLICIT_REMOTE_MARKER = "\x00implicit"


def _extract_mass_delete_target(command: str) -> str | None:
    """Extract a READABLE normalized per-invocation tuple binding the destructive IDENTITY of a
    `git push` MASS-delete form (#1062b — `--mirror`/`--prune`/multi-ref delete), or None when
    the command is not such a form.
    """
    if not re.search(_GIT_PREFIX + r"push\b", command):
        return None
    # Single-ref delete is the OTHER op-class — defer to it (the boundary discriminator).
    if _extract_remote_ref_delete_target(command) is not None:
        return None
    after = _tokens_after_push(command)
    if after is None:
        return None
    # Flags come from the same leg as the positionals, so a flag in a benign later leg cannot leak in.
    gf = _normalized_flags(after, "git")  # needs --mirror/--prune in _FLAG_SPEC (added #1062b)
    mass_flags = sorted(f for f in ("--mirror", "--prune") if f in gf)
    positionals = _push_positionals(after)
    colon_dsts = [p for p in positionals if re.match(r"^\+?:", p)]

    if mass_flags:
        # --mirror/--prune: remote = first positional (git grammar) or implicit;
        # explicit refspecs (if any) are the remaining positionals.
        remote = _strip_surrounding_quotes(positionals[0]) if positionals else _IMPLICIT_REMOTE_MARKER
        refspecs = sorted(_strip_surrounding_quotes(p) for p in positionals[1:])
        flags_part = ",".join(mass_flags)
    elif "--delete" in gf:
        # multi-ref --delete (the single-ref case already deferred above): git grammar
        # repo = first positional, the rest are the deleted refs. Need >=2 refs to be mass.
        if len(positionals) < 3:
            return None
        remote = _strip_surrounding_quotes(positionals[0])
        refspecs = sorted(_strip_surrounding_quotes(p) for p in positionals[1:])
        flags_part = "--delete"
    elif len(colon_dsts) >= 2:
        # multi empty-source colon delete (`origin :a :b`): remote = first non-colon
        # positional (or implicit); refs = the colon dsts.
        non_colon = [p for p in positionals if not re.match(r"^\+?:", p)]
        remote = _strip_surrounding_quotes(non_colon[0]) if non_colon else _IMPLICIT_REMOTE_MARKER
        refspecs = sorted(
            _strip_surrounding_quotes(p).rsplit(":", 1)[1] for p in colon_dsts
        )
        flags_part = "--delete"
    else:
        return None

    # Netstring framing keeps the identity injective for any field content.
    return _canonical_join([flags_part, remote, *sorted(set(refspecs))])
