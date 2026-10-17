"""
Location: pact-plugin/hooks/shell/merge/context.py
Summary: extract_command_context: the SSOT of a command's mintable identity.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: extract_command_context, drop_trailing_output_redirects.
"""

from __future__ import annotations

import re

from shell.merge.checkout import (_implicit_push_identity, _push_option_mr_identity, push_option_merge)
from shell.merge.classify import (detect_command_operation_type)
from shell.merge.flags import (extract_privileged_flags)
from shell.merge.targets import (_extract_api_merge_pr, _extract_close_target, _extract_merge_target, _extract_pr_number, _is_bare_cli_merge)
from shell.merge.targets_git import (_MERGE_IMPLICIT_SENTINEL, _extract_api_close_pr, _extract_branch_delete_set, _extract_branch_name, _extract_force_push_set, _extract_force_push_target_ref, _extract_mass_delete_target, _extract_protection_branch, _extract_push_to_main_set, _extract_remote_ref_delete_target, _gitlab_context_target, _implicit_force_push_identity)
from shell.words import normalize_line_continuations as _normalize_line_continuations
from shell.words import token_spans as _token_spans

# A trailing output redirect, as typed (no quote, escape, expansion or glob can match):
# `N>&M`, or `[N]>`, `[N]>>`, `&>` and one plain file word. ASCII digits only: the shell reads
# any other digit as a word, which is one more argument of the command.
_FD_COPY_RE = re.compile(r"[0-9]*>&[0-9]+")
_REDIRECT_OPERATOR = r"(?:[0-9]*>>?|&>)"
_REDIRECT_OPERATOR_RE = re.compile(_REDIRECT_OPERATOR)
_REDIRECT_FILE = r"[A-Za-z0-9_./+:@%,=-]+"
_REDIRECT_FILE_RE = re.compile(_REDIRECT_FILE)
_REDIRECT_ATTACHED_RE = re.compile(f"{_REDIRECT_OPERATOR}({_REDIRECT_FILE})")


def _plain_redirect_file(word: str) -> bool:
    """True for a file word an output redirect may name here: no path component other than
    `.` starts with a dot, so `.git/config`, `..` and a dot-file of the user's home (a client's
    own configuration, which says where a remote or a host points) keep the command refused."""
    return all(part == "." or not part.startswith(".") for part in word.split("/"))


def drop_trailing_output_redirects(leg: str) -> str:
    """`leg` without the run of output redirects at its very end (`2>&1`, `> merge.log`,
    `&>out`, `>>log 2>&1`); `leg` itself when there is none or the shell words cannot be read.

    An output redirect only says where the output goes: it changes no operation, target,
    flag, repository or host. Anything else stays in place and is read as a word, which
    refuses: a redirect that is not at the end, an input redirect, `>|`, a file word that is
    quoted, expanded, globbed or names a dot path, and an fd glued to a word (`x2>f`)."""
    spans = _token_spans(leg)
    if not spans:
        return leg
    raw = [leg[start:end] for start, end, _token in spans]
    keep = len(raw)
    while keep > 1:
        last = raw[keep - 1]
        attached = _REDIRECT_ATTACHED_RE.fullmatch(last)
        if _FD_COPY_RE.fullmatch(last) or (attached and _plain_redirect_file(attached.group(1))):
            keep -= 1
        elif (keep > 2 and _REDIRECT_OPERATOR_RE.fullmatch(raw[keep - 2])
                and _REDIRECT_FILE_RE.fullmatch(last) and _plain_redirect_file(last)):
            keep -= 2
        else:
            break
    return leg if keep == len(raw) else leg[:spans[keep - 1][1]]

def extract_command_context(command: str, flag_scan_text: str | None = None) -> dict:
    """Extract operation context FROM A COMMAND STRING (never prose)."""
    context: dict = {}
    # Join line continuations on both inputs first, so the flag bind and op/target agree (mint == read).
    command = _normalize_line_continuations(command)
    if flag_scan_text is not None:
        flag_scan_text = _normalize_line_continuations(flag_scan_text)
    op_type = detect_command_operation_type(command)
    if op_type is None:
        return context
    context["operation_type"] = op_type
    # bound_flags is an attribute of the (op, target) pair, never part of its identity.
    context["bound_flags"] = extract_privileged_flags(
        flag_scan_text if flag_scan_text is not None else command, op_type
    )
    # GitLab forms bind distinct keys (mr_target, branch, protected_branch) so gh and glab tokens
    # never authorize each other.
    if op_type == "merge" and push_option_merge(command):
        mr_target = _push_option_mr_identity(command)
        if mr_target is not None:
            context["mr_target"] = mr_target
        return context
    # A glab leg reads every word after the verb as a selector, so a trailing `2>&1` or
    # `> merge.log` hid the target. Dropped here, in the one function mint and read share.
    gitlab = _gitlab_context_target(drop_trailing_output_redirects(command), op_type)
    if gitlab is not None:
        key, target = gitlab
        if target is not None:
            context[key] = target
        return context
    if op_type in ("merge", "close"):
        # close accepts {<number>|<url>|<branch>} — bind whichever the command
        # carries so EVERY faithful close form MINTS (not just the number form).
        # merge keeps the number-only extractor (+ the #1096 API pulls/<N> path).
        if op_type == "close":
            pr_number = _extract_close_target(command)
            if pr_number is None:
                pr_number = _extract_api_close_pr(command)   # REST close, pulls/<N>
        else:
            pr_number = _extract_pr_number(command)
            if pr_number is None:
                pr_number = _extract_api_merge_pr(command)   # #1096 API pulls/<N>/merge
            if pr_number is None:
                # Explicit gh pr merge <branch>/<url> target, via the shared _classify_pr_target.
                pr_number = _extract_merge_target(command)
        if pr_number is not None:
            context["pr_number"] = pr_number
        elif op_type == "merge" and _is_bare_cli_merge(command):
            # Target-blind sentinel only for the truly bare CLI merge (flags only): its target is runtime state.
            context["merge_implicit"] = _MERGE_IMPLICIT_SENTINEL
    elif op_type == "branch-delete":
        # Single-branch scalar path (BYTE-IDENTICAL): exactly ONE positional.
        branch = _extract_branch_name(command)
        if branch is not None:
            context["branch"] = branch
        else:
            # Multi-branch force delete binds the sorted, deduplicated branch-set identity.
            branch_set = _extract_branch_delete_set(command)
            if branch_set is not None:
                context["branch_set"] = branch_set
    elif op_type in ("force-push", "push-to-main"):
        # push-to-main reuses the force-push target parser: its target IS the
        # main/master ref that parser already returns for a plain push.
        target_ref = _extract_force_push_target_ref(command)
        if target_ref is not None:
            context["target_ref"] = target_ref
        elif op_type == "push-to-main":
            # Exactly one of target_ref / push_set / force_push_set is set per command.
            push_set = _extract_push_to_main_set(command)
            if push_set is not None:
                context["push_set"] = push_set
            else:
                # GIT CHECKOUT STATE: a push of main that names no main ref (`git push`,
                # `--all`, `:`) binds its own distinct key; the branch and remote it reaches
                # are bound by command_repo_binding.
                push_implicit = _implicit_push_identity(command)
                if push_implicit is not None:
                    context["push_implicit"] = push_implicit
        else:
            # Multi-ref force push binds a normalised set, gated by the ref-scope-neutral flag allowlist.
            force_push_set = _extract_force_push_set(command)
            if force_push_set is not None:
                context["force_push_set"] = force_push_set
            else:
                # Remote-qualified sentinel for the implicit current-branch force push.
                fpi = _implicit_force_push_identity(command)
                if fpi is not None:
                    context["force_push_implicit"] = fpi
    elif op_type == "remote-ref-delete":
        # Remote ref delete reuses target_ref; the op class, checked first, prevents cross-op matches.
        ref = _extract_remote_ref_delete_target(command)
        if ref is not None:
            context["target_ref"] = ref
    elif op_type == "remote-mass-delete":
        # Mass delete uses its own key mass_target (a tuple, not a ref).
        mass_target = _extract_mass_delete_target(command)
        if mass_target is not None:
            context["mass_target"] = mass_target
    elif op_type == "branch-protection":
        # #1063: the protected branch is PATH-resident (branches/<b>/protection).
        # Distinct key `protected_branch`; op-identity-first in the read switch keeps
        # it from cross-authorizing a branch-delete of the same branch name.
        protected_branch = _extract_protection_branch(command)
        if protected_branch is not None:
            context["protected_branch"] = protected_branch
    return context
