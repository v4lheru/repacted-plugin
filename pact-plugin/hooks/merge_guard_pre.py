#!/usr/bin/env python3
"""PreToolUse hook (matcher Bash): blocks dangerous git/gh/glab operations unless a valid
authorization token exists.

The read side of the merge guard: merge_guard_post.py mints the token when the operator approves
the command in AskUserQuestion. A token is only the use ledger (one approval, MAX_USES, retired
after success): the consent is the user's answer in the session transcript the host writes
(shared/approval_record.py), re-read here through the mint rules and matched to this exact
command. A token with no such answer, an answer older than TOKEN_TTL, or a subagent or teammate
frame authorizes nothing. Gated: merge, PR close, glab mr merge/close/delete, force push,
branch delete, push to main/master, remote ref and mass delete, and API calls (gh api, curl,
wget, glab api) that merge, delete refs, write contents or weaken branch protection.
Classifiers live in shared/merge_guard_common.py.

Map (file order): _emit_load_failure_deny; find_valid_token; find_valid_tokens; _safe_remove;
_consume_token; _both_present_equal; _token_matches_command; _approved_on_record; _git_quiet;
_deletes_only_disposable_branches; _alias_danger; _unresolved_repo_hint; check_merge_authorization; main. Design notes: the design notes.
"""

from __future__ import annotations

from functools import partial
import glob
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:   # the annotation's name only; the call site imports it lazily
    import subprocess

# The one fail-closed emitter (hooks/_fail_closed.py, stdlib only): imported before the
# gauntlet so a failed import below still denies.
from _fail_closed import emit_load_failure_deny

# Fail closed on any module-load failure (imports, regex compiles): an empty stdout is an allow.
try:
    import shared.pact_context as pact_context
    from shared.pact_context import get_session_id
    # Called as approval_record.answer_on_record: tests/conftest.py patches it there.
    from shared import approval_record

    # Shared constants and cleanup — single source of truth for both hooks
    sys.path.insert(0, str(Path(__file__).parent))
    # Names this hook uses directly.
    from shared.merge_guard_common import (
        OP_RULE_IDS,
        bound_long_runs,
        canonical_spelling,
        detect_command_operation_type,
        is_dangerous_command,
        is_compound_destructive_command,
        TOKEN_DIR,
        TOKEN_PREFIX,
        USE_MARKER_SUFFIX,
        cleanup_consumed_tokens as _cleanup_consumed_tokens,
        cleanup_orphan_tokens as _cleanup_orphan_tokens,
        extract_command_context,
        command_repo_binding,
        flag_flood,
        holds_second_operation,
        wrapper_binding,
        approval_binding,
        unbindable_reason,
        set_invocation_cwd,
        unmintable_danger,
        _mask_shell_quotes as mask_quotes,
        _single_destructive_leg,
        _single_detectable_leg,
        _slice_stripped_legs,
        _normalize_line_continuations,
    )
    from shell.merge.classify import _leg_payloads, _leg_words
    from shell import git_alias
    # Re-exports consumed by the test suite through this module's namespace
    # (module-level seam: tests import these via merge_guard_pre).
    # Read-floor danger predicates promoted to shared (GAP1/GAP5) — re-imported here.
    from shared.merge_guard_common import (  # noqa: F401  # re-export: test-suite seam
        _strip_non_executable_content,
        _has_pipe_to_shell,
        _has_process_substitution_to_shell,
        _has_eval_or_source,
        _var_is_expanded,
        _has_command_substitution,
        _has_eval_with_heredoc,
        TOKEN_TTL,
        # PR-number extraction relocated to shared (the SSOT extract_command_context
        # owns command-context derivation). _GH_PR_NUMBER_RE and _extract_pr_number
        # are re-exported here for the existing test imports.
        _GH_PR_NUMBER_RE,
        _extract_pr_number,
    )
except BaseException as _module_load_error:  # noqa: BLE001 — fail-closed catch-all
    emit_load_failure_deny("merge_guard_pre", "module imports", _module_load_error)

# Prefix constants and the PR regexes are imported from shared.merge_guard_common.

# Pre-serialized JSON for allow-path output: tells Claude Code UI to suppress
# the hook display instead of showing "hook error (No output)".
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


_emit_load_failure_deny = partial(emit_load_failure_deny, "merge_guard_pre")


# Patterns for dangerous commands
try:
    # DANGEROUS_PATTERNS relocated to shared.merge_guard_common (GAP1); imported above.
    pass

    # The PR regexes live in shared.merge_guard_common and are imported above.
except BaseException as _pattern_compile_error:  # noqa: BLE001 — fail-closed catch-all
    _emit_load_failure_deny("pattern compilation", _pattern_compile_error)


# The danger predicates and compound regexes are the SSOT in shared.merge_guard_common.


def find_valid_token(token_dir: Path | None = None) -> tuple[dict, str] | tuple[None, None]:
    """A valid (unexpired) authorization token for the current session: the last one
    find_valid_tokens lists, or (None, None)."""
    tokens = find_valid_tokens(token_dir)
    return tokens[-1] if tokens else (None, None)


def find_valid_tokens(token_dir: Path | None = None) -> list[tuple[dict, str]]:
    """Every valid (unexpired, not used up) authorization token of the current session, as
    (token, path). One bundle can approve several commands, each with its own token; the
    caller picks the one whose full binding matches the command. Expired and malformed
    tokens are removed on the way."""
    if token_dir is None:
        token_dir = TOKEN_DIR

    current_session = get_session_id()

    now = time.time()
    valid: list[tuple[dict, str]] = []
    token_pattern = str(token_dir / f"{TOKEN_PREFIX}*")

    for token_path in glob.glob(token_pattern):
        # Skip consumed tokens — they were already used by a prior invocation
        if token_path.endswith(".consumed"):
            continue
        # Skip .use-N slot markers: they claim a use slot and are not tokens.
        if USE_MARKER_SUFFIX in os.path.basename(token_path):
            continue

        try:
            with open(token_path, "r") as f:
                token_data = json.load(f)

            expires_at = token_data.get("expires_at", 0)

            if not isinstance(expires_at, (int, float)) or expires_at <= 0:
                # Invalid token data — clean up
                _safe_remove(token_path)
                continue

            if expires_at < now:
                # Expired — clean up
                _safe_remove(token_path)
                continue

            # Session scoping: a token belongs to the session that minted it. A session with
            # no id (no context file) reads only tokens minted without one, as the mint
            # writes them; it never reads another session's.
            if (token_data.get("session_id") or "") != (current_session or ""):
                continue

            # All use slots claimed means consumed, even if the final .consumed rename was lost.
            token_max_uses = token_data.get("max_uses", 1)
            if isinstance(token_max_uses, int) and token_max_uses > 1:
                all_claimed = all(
                    os.path.exists(f"{token_path}{USE_MARKER_SUFFIX}{slot}")
                    for slot in range(1, token_max_uses + 1)
                )
                if all_claimed:
                    # Race-recover: attempt the missed terminal rename;
                    # ignore failure (the next scan will retry or the
                    # cleanup loop will eventually reap the markers).
                    try:
                        os.rename(token_path, token_path + ".consumed")
                    except OSError:
                        pass
                    continue

            # Valid token found
            valid.append((token_data, token_path))

        except (json.JSONDecodeError, OSError, KeyError, AttributeError, TypeError):
            # Corrupted or malformed token — clean up
            _safe_remove(token_path)

    # Clean up stale consumed tokens while we're scanning
    _cleanup_consumed_tokens(token_dir)

    # Reap orphan tokens on every dangerous-command check (disk hygiene, fail-open).
    _cleanup_orphan_tokens(token_dir)

    return valid


def _safe_remove(path: str):
    """Remove a file, ignoring errors if it doesn't exist."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _consume_token(token_path: str, max_uses_default: int = 1) -> bool:
    """Consume one use of a token via per-use marker O_EXCL claim (#720 Bug C)."""
    max_uses = max_uses_default
    n_use_token = False  # Set True when we observe N>1 from JSON OR filesystem
    try:
        with open(token_path, "r") as f:
            token_data = json.load(f)
            field_value = token_data.get("max_uses", max_uses_default)
            if isinstance(field_value, int) and field_value > 0:
                max_uses = field_value
                if max_uses > 1:
                    n_use_token = True
    except (json.JSONDecodeError, OSError, KeyError, AttributeError, TypeError):
        # Read failed but .use-N markers exist: an N-use token mid-rename, so fail closed.
        if os.path.exists(f"{token_path}{USE_MARKER_SUFFIX}1"):
            return False
        max_uses = max_uses_default

    consumed_path = token_path + ".consumed"

    # Single-use path: rename to .consumed; a lost race is fine if .consumed already exists.
    if max_uses <= 1 and not n_use_token:
        try:
            os.rename(token_path, consumed_path)
            return True
        except FileNotFoundError:
            try:
                fd = os.open(consumed_path, os.O_RDONLY)
                os.close(fd)
                return True
            except FileNotFoundError:
                return False
            except OSError:
                return False
        except OSError:
            return False

    # N-use path: claim slots via O_EXCL. The first slot we can claim is
    # this invocation's use; the last slot also triggers terminal rename.
    for slot in range(1, max_uses + 1):
        marker_path = f"{token_path}{USE_MARKER_SUFFIX}{slot}"
        try:
            fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Slot already claimed by a prior or concurrent invocation —
            # try the next slot.
            continue
        except OSError:
            # Transient FS error claiming this slot — try the next slot
            # rather than fail closed, since another slot may still be
            # available.
            continue

        # Slot claimed: write the audit body; on failure unlink the marker so no orphan slot blocks uses.
        fdopen_took_ownership = False
        try:
            try:
                file_obj = os.fdopen(fd, "w")
            except Exception:
                # fdopen failed before taking ownership — fd still ours.
                raise
            fdopen_took_ownership = True
            with file_obj as f:
                f.write(json.dumps({
                    "consumed_at": time.time(),
                    "slot": slot,
                }))
        except Exception:
            if not fdopen_took_ownership:
                try:
                    os.close(fd)
                except OSError:
                    pass
            try:
                os.unlink(marker_path)
            except OSError:
                pass
            continue

        # Operator-visible audit line; CR/LF in the basename are sanitised against log injection.
        safe_basename = (
            os.path.basename(token_path)
            .replace("\n", " ")
            .replace("\r", " ")
        )
        print(
            f"[security] merge-authorized token consumed "
            f"(slot {slot}/{max_uses}): {safe_basename}",
            file=sys.stderr,
        )

        # Terminal rename when the final slot is claimed. Rename failure is
        # non-fatal: find_valid_token will see all slots claimed on the
        # next scan and race-recover the rename then.
        if slot == max_uses:
            try:
                os.rename(token_path, consumed_path)
            except OSError:
                pass
        return True

    # Every slot was already claimed before we got there — token is fully
    # consumed. Caller fails closed.
    return False


# _GH_PR_VALUE_TAKING_FLAGS and _extract_pr_number relocated to
# shared.merge_guard_common (the shared extract_command_context owns PR-number
# extraction); _extract_pr_number is imported back at the top of this module.


def _both_present_equal(token_val: object, cmd_val: object) -> bool:
    """True iff BOTH values are present (not None) and string-equal."""
    if token_val is None or cmd_val is None:
        return False
    return str(token_val) == str(cmd_val)


def _token_matches_command(token: dict, command: str) -> bool:
    """Check that a token's context POSITIVELY matches the command being executed."""
    context = token.get("context", {})
    if not isinstance(context, dict):
        return False  # F-READ-1: a non-dict context proves nothing — REFUSE

    token_op = context.get("operation_type")
    # Derive (op, target, bound_flags) from the single destructive leg, so a benign neighbour leg's
    # flags or target never enter the binding.
    cmd = extract_command_context(
        _single_destructive_leg(command)
        or _single_detectable_leg(command)
        or command
    )
    cmd_op = cmd.get("operation_type")
    # amend_3 (#141): bind the repo the command acts on, resolved from the FULL command (a
    # leading `cd other &&` changes it). Unresolvable -> no token can authorize the command.
    bound = command_repo_binding(cmd, command)
    if bound is None:
        return False
    # A command no approval can be bound to (an unresolvable directory, an expansion in the
    # guarded git leg, an earlier leg that moves a remote, a loop) matches no token: two
    # values that cannot be read are never equal.
    if unbindable_reason(command, bound) is not None:
        return False
    # The guarded leg is bound word for word (`typed`); a wrapped operation is bound to the
    # whole command as typed and the directory it runs from (`wrapper`, `cwd`).
    cmd = {**bound, **approval_binding(command)}

    # (a) Operation type: both present and equal, else refuse (an untyped token never authorizes).
    if token_op is None or cmd_op is None or token_op != cmd_op:
        return False

    # (a2) Flags: executed bound flags must equal the approved set; an added privilege or a dropped
    # constraint refuses.
    if set(context.get("bound_flags", [])) != set(cmd.get("bound_flags", [])):
        return False

    # (a3) Repo, host, remote, wrapper, its directory and the typed leg: when either side
    # carries one, both must carry it and agree.
    for axis in ("repo", "host", "remote", "wrapper", "cwd", "typed"):
        if (context.get(axis) is not None or cmd.get(axis) is not None) and not \
                _both_present_equal(context.get(axis), cmd.get(axis)):
            return False

    # (b) Target axis, per op-class — require a POSITIVE, command-anchored target
    # match. Unextractable or mismatched target -> REFUSE (over-block is the safe
    # #1031 direction; the read side never under-blocks #1032).
    if token_op in ("merge", "close"):
        # Bare gh pr merge binds the target-blind merge_implicit sentinel (merge only).
        return (
            _both_present_equal(context.get("pr_number"), cmd.get("pr_number"))
            or _both_present_equal(context.get("mr_target"), cmd.get("mr_target"))
            or (
                token_op == "merge"
                and _both_present_equal(
                    context.get("merge_implicit"), cmd.get("merge_implicit")
                )
            )
        )
    if token_op == "mr-delete":
        # FIX-MERGE f3: GitLab merge-request deletion binds on `mr_target`. op-type-first
        # (above) keeps a close token from ever authorizing a delete of the same MR.
        return _both_present_equal(context.get("mr_target"), cmd.get("mr_target"))
    if token_op == "branch-delete":
        # Single branch (`branch`) or branch set (`branch_set`): a scalar never authorizes a set.
        return (
            _both_present_equal(context.get("branch"), cmd.get("branch"))
            or _both_present_equal(context.get("branch_set"), cmd.get("branch_set"))
        )
    if token_op == "push-to-main":
        # Single ref (`target_ref`) or multi-ref `push_set`, compared by set equality.
        return (
            _both_present_equal(context.get("target_ref"), cmd.get("target_ref"))
            or _both_present_equal(context.get("push_set"), cmd.get("push_set"))
            or _both_present_equal(context.get("push_implicit"), cmd.get("push_implicit"))
        )
    if token_op == "force-push":
        # Single ref (`target_ref`) or multi-ref `force_push_set`, compared by set equality.
        return (
            _both_present_equal(context.get("target_ref"), cmd.get("target_ref"))
            or _both_present_equal(context.get("force_push_set"), cmd.get("force_push_set"))
            or _both_present_equal(
                context.get("force_push_implicit"), cmd.get("force_push_implicit")
            )
        )
    if token_op == "remote-ref-delete":
        # The destination ref must match explicitly, so a delete-feature approval never deletes main.
        return _both_present_equal(context.get("target_ref"), cmd.get("target_ref"))
    if token_op == "remote-mass-delete":
        # Bind the mass_target tuple: a --prune token does not authorize --mirror.
        return _both_present_equal(context.get("mass_target"), cmd.get("mass_target"))
    if token_op == "branch-protection":
        # Bind the protected branch; the op type keeps protection and branch-delete tokens apart.
        return _both_present_equal(context.get("protected_branch"), cmd.get("protected_branch"))

    # Unknown op-class (a typed token whose op is not one of the handled classes)
    # — REFUSE. No terminal allow exists on the read path.
    return False


def _approved_on_record(command: str, hook_input: dict | None) -> bool:
    """True when the session transcript holds the user's answer, within TOKEN_TTL, whose
    clicked option the mint rules (merge_guard_post) bind to exactly this command. A matching
    token proves only that the mint ran, which the model can do itself; this is the consent."""
    def approves(questions: list, answers: dict) -> bool:
        try:
            from merge_guard_post import _mint_contexts_from_bundle
        except BaseException:   # noqa: BLE001 - the mint rules unreadable: no approval
            return False
        contexts, _refusals = _mint_contexts_from_bundle(questions, answers)
        return any(_token_matches_command({"context": context}, command) for context in contexts)

    return approval_record.answer_on_record(hook_input, approves, TOKEN_TTL)


_BRANCH_DELETE_FLAGS = frozenset({"--delete", "--force"})
_GIT_CHECK_TIMEOUT_S = 3.0


def _git_quiet(cwd: str, *args: str) -> "subprocess.CompletedProcess[str] | None":
    import subprocess
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=_GIT_CHECK_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None


def _deletes_only_disposable_branches(command: str, cwd: str | None) -> bool:
    """True for a bare `git branch -D name...` whose every branch is local, was never pushed
    (no upstream, no remote-tracking ref of that name) and holds no commit that another
    branch, remote ref or tag does not also hold: deleting it loses nothing. Any other shape,
    or any git error, is False and the normal approval rule applies."""
    from shell_words import words as shell_words
    words = shell_words(command)
    if words is None:
        return False
    if words[:2] != ["git", "branch"]:
        return False
    flags = [w for w in words[2:] if w.startswith("-")]
    names = [w for w in words[2:] if not w.startswith("-")]
    if not names or any(not (f in _BRANCH_DELETE_FLAGS or (f[1:] and set(f[1:]) <= set("dDf")
                                                           and not f.startswith("--")))
                        for f in flags):
        return False
    if any(ch in name for name in names for ch in "*?[\\:"):
        return False
    cwd = cwd or os.getcwd()
    for name in names:
        head = _git_quiet(cwd, "rev-parse", "--verify", "-q", f"refs/heads/{name}")
        upstream = _git_quiet(cwd, "config", "--get", f"branch.{name}.remote")
        remote = _git_quiet(cwd, "for-each-ref", "--count=1", "--format=%(refname)",
                            f"refs/remotes/*/{name}")
        unique = _git_quiet(cwd, "rev-list", "-n", "1", f"refs/heads/{name}", "--not",
                            f"--exclude={name}", "--branches", "--remotes", "--tags")
        if None in (head, upstream, remote, unique):
            return False
        if head.returncode != 0 or upstream.returncode != 1 or remote.returncode != 0 \
                or remote.stdout.strip() or unique.returncode != 0 or unique.stdout.strip():
            return False
    return True


# The refusal's rule id, by the marker its text carries (first match wins); the plain
# "requires user approval" refusal takes the id of the operation type the arms detected.
_REFUSAL_MARKS = (
    ("mg.unmintable.flag_flood", "carries more than 32 flags"),
    ("mg.unmintable.long_run", "carries a run of 512 or more characters"),
    ("mg.compound.destructive", "Compound destructive command rejected"),
    ("mg.compound.destructive", "holds a second operation that needs approval"),
    ("mg.unmintable.shape", "it cannot be bound to an approval"),
    ("mg.token.consume_failed", "could not consume authorization token"),
    ("mg.token.mismatch", "An authorization token exists but its approved operation"),
    ("mg.token.no_record", "holds no answer from the user approving it"),
    ("mg.push.to_main.expanded", "This push names its destination with a shell expansion"),
)


_HINT_COMMAND_LIMIT = 200


def _alias_danger(command: str, cwd: str | None, depth: int = 0) -> str | None:
    """Why a git leg of `command` reaches a gated act through a git alias, else None: a call
    that runs an alias (`-c alias.*` or the repo's or the user's git config) whose expansion
    the merge check refuses, or a `git config ... alias.<name> <value>` that sets one. The
    arms read `git push`, never `git fp`, and an approval binds the literal command, so
    either is refused as a shape no approval can bind. A leg's shell -c or eval string is
    read too, two levels deep."""
    def gated(text: str) -> bool:
        return bool(is_dangerous_command(text) or unmintable_danger(text))

    for leg in _slice_stripped_legs(_normalize_line_continuations(command)):
        words = _leg_words(leg)
        if not words:
            continue
        if depth < 2:
            for inner, _wrapper in _leg_payloads(words):
                reason = _alias_danger(inner, cwd, depth + 1)
                if reason:
                    return reason
        if os.path.basename(words[0]) != "git":
            continue
        args = words[1:]
        for value in git_alias.defined_values(args):
            if gated(value[1:] if value.startswith("!") else f"git {value}"):
                return "a git config call that sets an alias running a gated git act"
        expanded, script = git_alias.expand(args, cwd)
        if script is not None and (gated(script) or depth < 2
                                   and _alias_danger(script, cwd, depth + 1)):
            return "a git command run through a shell alias that runs a gated git act"
        if script is None and expanded != args and gated(shlex.join(["git", *expanded])):
            return "a git command run through an alias that runs a gated git act"
    return None


def _leg_binding(command: str) -> tuple[dict, dict | None]:
    """(the context of the leg the token match reads, its repository binding: None when the
    leg has no operation type or its repository cannot be resolved). One check reads it
    once and hands it to the two readers below."""
    context = extract_command_context(
        _single_destructive_leg(command) or _single_detectable_leg(command) or command)
    return context, (command_repo_binding(context, command) if context.get("operation_type") else None)


def _unresolved_repo_hint(command: str, read: tuple[dict, dict | None] | None = None) -> str | None:
    """The refusal for a dangerous command whose repository cannot be resolved (so no
    approval could bind it), naming the form that can be approved; None when it resolves.
    It never allows: the caller returns it as the deny. `read` is its _leg_binding."""
    context, bound = read or _leg_binding(command)
    if not context.get("operation_type") or bound is not None:
        return None
    shown = " ".join(command.split())
    cli = re.match(r"(?:\S*/)?(gh|glab)\s", shown)
    if cli and len(shown) <= _HINT_COMMAND_LIMIT and not re.search(r"[;&|<>`$()]", shown) \
            and not re.search(r"(?:^|\s)(?:-R|--repo)(?:[=\s]|$)", shown):
        slug = "OWNER/REPO" if cli.group(1) == "gh" else "GROUP/PROJECT"
        form = f"`{shown} -R {slug}` (with the real {slug})"
    else:
        form = ("the same command with the repository spelled out (`-R OWNER/REPO` for gh and "
                "glab, `git -C <checkout> ...` for git)")
    return (
        "This command requires user approval, and no approval can be bound to it yet: the "
        "repository it acts on cannot be determined (the session directory is not a git "
        "checkout with an origin remote, and the command names no repository). Run it as "
        f"{form}, and embed that literal command in the selected AskUserQuestion option."
    )


def _unbindable_reason_of(command: str, read: tuple[dict, dict | None] | None = None,
                          wrapped: bool | None = None) -> str | None:
    """unbindable_reason of `command` with its repository binding, read from the same leg
    the token match reads. `read` is its _leg_binding and `wrapped` whether wrapper_binding
    binds it, when the caller has them."""
    context, bound = read or _leg_binding(command)
    if not context.get("operation_type"):
        return None
    return unbindable_reason(command, bound, wrapped)


def _unmintable_refusal(reason: str) -> str:
    """The run-it-yourself refusal of a command no approval can be bound to."""
    return (
        f"This command is {reason}; it cannot be bound to an approval, so the guard "
        "cannot let it run. Show the user the exact command and ask them to run it "
        "themselves with `! <command>`."
    )


def _approval_can_mint(command: str) -> bool:
    """True when approving this command's own text would mint a token that passes it, asked
    of the post hook's mint itself (one decision, no second list). An unreadable answer
    keeps the ordinary approval text."""
    import contextlib
    import io
    try:
        # The post hook reports its own load failure on stdout and stderr and exits: neither
        # may reach this hook's output, where a stray line would spoil the deny.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from merge_guard_post import _mint_context_from_texts
    except (Exception, SystemExit):  # noqa: BLE001 - the refusal text must not depend on it
        return True
    for text in (f"`{command}`", command):
        context = _mint_context_from_texts([f"Yes, run {text}"]).context
        if context is not None and _token_matches_command({"context": context}, command):
            return True
    return False


def _rule_of_refusal(reason: str, command: str) -> str:
    for rule, mark in _REFUSAL_MARKS:
        if mark in reason:
            return rule
    return OP_RULE_IDS[extract_command_context(command).get("operation_type")]


def check_merge_authorization(command: str, token_dir: Path | None = None,
                              cwd: str | None = None,
                              hook_input: dict | None = None) -> str | None:
    """Check if a dangerous command is authorized: None, or the refusal with its rule-id
    tail `[rule: <id>]` (RULE_IDS in shell.merge.arms; the text before the tail is the
    refusal as written). `hook_input` is the PreToolUse payload: its transcript and
    frame decide whether the user's answer is on record; None means it is not."""
    reason = _refusal(command, token_dir, cwd, hook_input)
    if reason is None:
        return None
    return f"{reason} [rule: {_rule_of_refusal(reason, command)}]"


def _refusal(command: str, token_dir: Path | None = None,
             cwd: str | None = None, hook_input: dict | None = None) -> str | None:
    """The refusal text of a dangerous command that is not authorized, else None."""
    # Words the shell decodes to plain text are spelled plainly first (CANONICAL SPELLING in
    # merge_guard_common): `git "pu"sh` meets the arms that gate `git push`, and the post
    # hook mints from the same spelling.
    command = canonical_spelling(command)
    # A long run that cannot carry a command is shortened first, so the arms stay fast
    # (LONG RUNS in merge_guard_common). A dangerous command that was shortened is
    # refused: its approval would bind the shortened text, not the command.
    probe = bound_long_runs(command)
    # More flags than the arms read hide what the command does (1.4.1 U13): refuse, like a
    # long run, instead of reading the command as safe.
    if flag_flood(probe):
        return (
            "This git/gh/glab command carries more than 32 flags; the guard cannot read what "
            "it does. Show the user the exact command and ask them to run it themselves with "
            "`! <command>`, or drop the repeated flags and run the command again."
        )
    # A git alias hides the act from the arms: its expansion is judged before the plain read
    # can let the command through.
    alias = _alias_danger(probe, cwd)
    if alias is not None:
        return _unmintable_refusal(alias)
    if not is_dangerous_command(probe):
        return None
    if probe != command:
        return (
            "This command carries a run of 512 or more characters with no space or quote; "
            "the guard cannot bind it to an approval. Show the user the exact command and "
            "ask them to run it themselves with `! <command>`, or move the long value to a "
            "file and run the command again."
        )
    # An empty branch that never left this machine: deleting it loses nothing.
    if _deletes_only_disposable_branches(command, cwd):
        return None

    # Compound destructive commands are always denied, before the token lookup: one token, one op.
    if is_compound_destructive_command(command):
        return (
            "Compound destructive command rejected — `&&`, `||`, `;`, `|`, and "
            "newlines cannot be authorized atomically. A single AskUserQuestion "
            "approval can only authorize ONE destructive operation. Run each "
            "destructive op separately with its own approval."
        )

    # the hardening pass: a shape no approval can bind (a GraphQL mutation, a body read from a file or
    # stdin, a raw HTTP MR/branch delete or close, a shell -c or eval wrapper) is never authorized
    # here, and no token is consumed for it: the user runs it.
    reason = unmintable_danger(command)
    # A push to a protected branch that stands only inside a wrapper's quoted payload is such a
    # shape too: an approval of that text mints nothing, so the refusal must not ask for one.
    operation = extract_command_context(command).get("operation_type")
    if reason is not None and (not operation or operation == "push-to-main"
                               and detect_command_operation_type(mask_quotes(command)) is None):
        return _unmintable_refusal(reason)

    # One approval covers one operation: a second one anywhere in the command (a substitution
    # in an argument, a redirect target, a here-string or a heredoc body, a script a leg runs)
    # is refused before the token lookup, so it never rides on the approval of the first.
    if holds_second_operation(command):
        return (
            "This command holds a second operation that needs approval, inside a "
            "substitution, a redirect target, a here-string, a heredoc or a wrapped script. "
            "A single AskUserQuestion approval can only authorize ONE destructive operation. "
            "Run each one as its own command with its own approval."
        )

    # No approval can bind a command whose repository is unknown (a session directory that
    # is no checkout, no -R): say so on this first deny, before the user is asked.
    read = _leg_binding(command)
    hint = _unresolved_repo_hint(command, read)
    if hint is not None:
        return hint

    tokens = find_valid_tokens(token_dir)
    for token, token_path in tokens:
        # The token whose FULL binding (op, target, flags, repo) matches this command; a
        # token for another PR, repo or flag set never authorizes it.
        if _token_matches_command(token, command):
            # The token is not the consent: the user's answer in the transcript is. Checked
            # before the use is claimed, so a token with no answer behind it stays unused.
            if not _approved_on_record(command, hook_input):
                return (
                    "An approval token matches this command, but this session's transcript "
                    "holds no answer from the user approving it in the last 15 minutes. A "
                    "token alone is not the user's consent, and a subagent or teammate never "
                    "has an answer on record: gated git acts are the lead's. Ask the user with "
                    "AskUserQuestion, the literal command in the chosen option, then run it "
                    "unchanged; from a subagent or teammate, hand the command to the lead."
                )
            # Consume the token — one approval = one operation
            # Uses rename for idempotent consumption: if a concurrent
            # invocation already consumed it, that's the success case.
            if _consume_token(token_path):
                return None
            # Consumption failed for unexpected reason — fail closed
            return (
                "Merge guard internal error — could not consume authorization token. "
                "Use AskUserQuestion to get approval again."
            )
    # A wrapped operation whose own text mints nothing (the mint reads no target or no command
    # in it) must not be sent round the approval loop: the user runs it.
    wrapper = wrapper_binding(command)
    if wrapper is not None and not _approval_can_mint(command):
        return _unmintable_refusal(
            reason or "a destructive command inside a subshell, a substitution or a compound "
                      "statement that no approval can name")
    context = extract_command_context(command)
    expanded_push = context.get("operation_type") == "push-to-main" and re.search(
        r"[$`*]", str(context.get("target_ref") or context.get("push_set") or ""))
    # A shape the mint refuses (unbindable_reason) is not sent round the approval loop
    # either; the expanded push to main keeps its own advice below.
    unbindable = _unbindable_reason_of(command, read, wrapper is not None)
    if unbindable is not None and not expanded_push:
        return _unmintable_refusal(unbindable)
    if tokens:
        # Tokens exist for other ops: keep them, block, and ask for a re-approval embedding the
        # literal command (never suggest running the bare command).
        return (
            "An authorization token exists but its approved operation does "
            "not match this command. An approval binds the operation, its "
            "target, the repository and the host, and these flags when "
            "present: --admin, -d/--remove-source-branch/--delete-branch, "
            "-R/--repo, --sha/--match-head-commit. Free to differ: a "
            "strategy flag (--squash, --rebase), a trailing `&& cmd`, "
            "`| tail` and an output redirect (`2>&1`, `> file`). Re-approve "
            "via AskUserQuestion with the literal command embedded in the "
            "selected option (e.g. `gh pr merge <N>`, the real PR number)."
        )

    # No token: require approval with the literal command embedded. Headless sessions approve
    # interactively.
    if expanded_push:
        return (
            "This push names its destination with a shell expansion or a glob, so it may "
            "update main and requires user approval. For a feature branch, write the branch "
            "name out literally and run it again; to push main, re-approve via "
            "AskUserQuestion with the literal command embedded in the selected option."
        )
    return (
        "Merge/close/force-push/branch-delete requires user approval. Re-approve "
        "via AskUserQuestion with the literal command embedded in the selected "
        "option (e.g. `gh pr merge <N>`, the real PR number) so the guard can "
        "bind the approval to this exact command. Label that option \"Yes\", "
        "\"Yes, merge\", \"Yes, close\", \"Yes, push\", \"Yes, force-push\", "
        "\"Yes, delete local branch\" or \"Yes, delete remote branch\": no other label "
        "approves anything, whatever its description says. In a channel/headless session "
        "where AskUserQuestion is unavailable, approve the operation interactively."
    )


def main():
    try:
        try:
            input_data = json.load(sys.stdin)
        except json.JSONDecodeError:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        pact_context.init(input_data)
        # amend_2 (#141): REST placeholder projects resolve against the payload cwd.
        # Set only when the payload carries one: each hook is a fresh process, so the default
        # (the process cwd) already applies otherwise.
        if isinstance(input_data.get("cwd"), str) and input_data.get("cwd"):
            set_invocation_cwd(input_data["cwd"])
        tool_input = input_data.get("tool_input", {})
        command = tool_input.get("command", "")

        if not command:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        cwd = input_data.get("cwd") if isinstance(input_data.get("cwd"), str) else None
        error = check_merge_authorization(command, cwd=cwd, hook_input=input_data)

        if error:
            # hookEventName is required by the harness; missing it silently fails open
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": error,
                }
            }
            print(json.dumps(output))
            sys.exit(2)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Security guard fails closed — block on unexpected errors
        print(f"Hook error (merge_guard_pre): {e}", file=sys.stderr)
        try:
            # hookEventName is required by the harness; missing it silently fails open
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "Merge guard internal error — blocking for safety. "
                        "If this persists, check the merge guard hooks."
                    ),
                }
            }
            print(json.dumps(output))
        except Exception:
            pass  # If even the deny output fails, fail silently
        sys.exit(2)


if __name__ == "__main__":
    main()
