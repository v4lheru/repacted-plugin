#!/usr/bin/env python3
"""PostToolUse hook (matcher AskUserQuestion|Bash): mints and retires merge-guard tokens.

AskUserQuestion: when the clicked option carries exactly one dangerous command, write a
short-lived token bound to its (op, target, privileged flags); merge_guard_pre requires it.
Bash (Layer 1, invariant I-2): after a successful destructive command, retire the matching
token (.consumed). Observer-style: never blocks a tool call.

Map: _emit_load_failure_alert, is_merge_question, _selected_option_text, _target_value,
_extraction_surface, _collect_pairs, MintResult, _strip_command_wrapper,
_leg_bounded_flag_scan_surface, _has_options, _clicked_options, _mint_context_from_bundle,
_mint_contexts_from_bundle, _mint_context_from_texts, _all_ascii, _bundle_has_command,
_no_mint_advisory, write_token, _same_command_binding, _retire_token_for_command, main.
Rationale and accepted residuals: the design notes
"""

from __future__ import annotations

# ─── stdlib first (used by _emit_load_failure_alert BEFORE wrapped imports) ─
import glob
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple, NoReturn


def _emit_load_failure_alert(stage: str, error: BaseException) -> NoReturn:
    """Stdlib-only fail-LOUD alert for load failure. PostToolUse cannot deny, so the advisory goes
    to stderr with exit 2 (stdout JSON is not parsed on non-zero exit; exit 0 would hide the
    breakage). Both branches are skipped: no token retirement, no token minting.
    """
    message = (
        f"PACT merge_guard_post {stage} failure — merge-token write/retirement "
        f"SKIPPED this turn; a consumed token may remain live, and subsequent "
        f"merge approvals will also fail to record — expect merge_guard_pre "
        f"denials until fixed. {type(error).__name__}: {error}. Check hook "
        "installation and shared module availability."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }
    }))
    print(
        f"Hook load error (merge_guard_post / {stage}): {message}",
        file=sys.stderr,
    )
    sys.exit(2)


# ─── fail-loud wrapper on cross-package imports ─────────────────────────────
try:
    import shared.pact_context as pact_context
    from shared.pact_context import get_session_id

    # Shared constants and cleanup — single source of truth for both hooks
    sys.path.insert(0, str(Path(__file__).parent))
    from shared.error_output import hook_error_json

    from shared.merge_guard_common import (
        bound_long_runs,
        set_invocation_cwd,
        command_repo_binding,
        TOKEN_TTL,
        TOKEN_DIR,
        TOKEN_PREFIX,
        MAX_USES,
        USE_MARKER_SUFFIX,
        LAYER1_SUCCESS_STDOUT_PATTERNS,
        cleanup_consumed_tokens as _cleanup_consumed_tokens,
        cleanup_unused_tokens as _cleanup_unused_tokens,
        canonical_spelling,
        detect_command_operation_type,
        extract_command_context,
        locate_command_region,
        locate_command_regions,
        # GAP1/GAP5: the mint uses the read hook's own danger and compound predicates (mint is a
        # subset of read by construction).
        is_dangerous_command,
        is_compound_destructive_command,
        # One approval covers one operation, and a wrapped one is bound to its whole text.
        holds_second_operation,
        approval_binding,
        unbindable_reason,
        # #1083: the mint flag-scan window uses the read side's two-tier leg selection.
        _single_destructive_leg,
        _single_detectable_leg,
    )
    from shared.tool_response import extract_tool_response
except BaseException as _module_load_error:  # noqa: BLE001 — fail-loud catch-all
    _emit_load_failure_alert("module imports", _module_load_error)


# Command regions come from merge_guard_common (one locator for mint and read).

# When the hook allows a command (exits 0), output this JSON so the Claude Code
# UI suppresses the hook display instead of showing "hook error (No output)".
_SUPPRESS_OUTPUT = json.dumps({"suppressOutput": True})


def is_merge_question(question: str) -> bool:
    """Coarse hint (KD-9): True iff the text embeds a recognized destructive command region.
    Not the security gate.
    """
    return locate_command_region(question) is not None


# Consent is read from the clicked LABEL alone; the description may only name the command.
# A closed set: any label outside it mints nothing, whatever it says.
_APPROVAL_LABEL = re.compile(
    r"yes(?:, (?:merge|close|push|force-push|delete local branch|delete remote branch))?")
APPROVAL_LABELS = (
    'one of these labels exactly (letter case aside): "Yes", "Yes, merge", "Yes, close", '
    '"Yes, push", "Yes, force-push", "Yes, delete local branch", "Yes, delete remote branch"')


def is_approval_label(label: object) -> bool:
    """True when a clicked option's LABEL is one of APPROVAL_LABELS: ASCII only, spaces
    trimmed at both ends, letter case folded. Any other label never mints, whatever it or
    its description says (an added word, a command, a look-alike letter)."""
    return isinstance(label, str) and label.isascii() \
        and _APPROVAL_LABEL.fullmatch(label.strip(" ").lower()) is not None


def _selected_option_text(options: object, answer: object) -> str | None:
    """Return "<label> <description>" of the option whose label EXACTLY matches
    the answer (option mode), or None when there are no options, no exact-label
    match, or the inputs are malformed. Labels are unique within a question (AUQ
    contract); the byte-equal match is the D3 source-guarantee (no fuzzy match)."""
    if not isinstance(options, list) or not options:
        return None
    if not isinstance(answer, str):
        return None
    for opt in options:
        if not isinstance(opt, dict):
            continue
        label = opt.get("label")
        if not isinstance(label, str) or label != answer:
            continue
        description = opt.get("description")
        description = description if isinstance(description, str) else ""
        return (label + " " + description).strip()
    return None


def _target_value(cmd_ctx: dict) -> str | None:
    """Target value of a command context for the op's context key, or None (no pair without one).

    Every op-class with a new context key MUST be listed here, or it gates on the read side but
    can never mint (#1064). See docs for the key per op.
    """
    return (
        cmd_ctx.get("pr_number")
        or cmd_ctx.get("mr_target")          # FIX-MERGE f3: GitLab merge requests
        or cmd_ctx.get("merge_implicit")
        or cmd_ctx.get("branch")
        or cmd_ctx.get("branch_set")
        or cmd_ctx.get("target_ref")
        or cmd_ctx.get("push_set")
        or cmd_ctx.get("push_implicit")     # 1.4.1 U13: a push of main naming no main ref
        or cmd_ctx.get("force_push_set")
        or cmd_ctx.get("force_push_implicit")
        or cmd_ctx.get("mass_target")
        or cmd_ctx.get("protected_branch")
    )


def _extraction_surface(region: str) -> str:
    """The surface extract_command_context reads for a region (#1195 OBS-H): the read side's
    three-tier expression (unique dangerous leg, else unique detectable leg, else the whole
    region). Regions stay whole for the GAP1/GAP5 checks.
    """
    return (
        _single_destructive_leg(region)
        or _single_detectable_leg(region)
        or region
    )


def _collect_pairs(texts: list) -> dict:
    """Map each complete (op_type, target) pair in `texts` to the whole region that produced it."""
    pairs: dict = {}
    for text in texts:
        for region in locate_command_regions(text):
            cmd_ctx = extract_command_context(_extraction_surface(canonical_spelling(region)))
            op_type = cmd_ctx.get("operation_type")
            target = _target_value(cmd_ctx)
            if op_type is not None and target is not None:
                pairs.setdefault((op_type, target), region)
    return pairs


class MintResult(NamedTuple):
    """Result of _mint_context_from_bundle (#1052): exactly one of context / refusal_reason is
    set. refusal_reason feeds only the no-mint advisory, never authorization.
    """

    context: dict | None
    refusal_reason: str | None


# B2 (#1052): self-teaching diagnostic per refusal reason code, surfaced ONLY by
# the observer-style no-mint advisory in `main` (gated to command-bearing
# bundles). These NAME the failing gate; they never authorize.
_REFUSAL_DIAGNOSTICS = {
    "no_options": "the approval carried no clickable options (a free-text answer never mints)",
    "not_an_approval": (
        "the clicked option's label does not read as an approval, so it approves nothing even "
        "though the option names a command — an approving option needs " + APPROVAL_LABELS
    ),
    "label_mismatch": (
        "the answer did not exactly match an option label — if the command was "
        "in the option LABEL, move it into the option DESCRIPTION (a long "
        "command-as-label can fail to round-trip)"
    ),
    "no_command": "no recognized merge/close/force-push/branch-delete command was found in the selected option",
    "multiple_commands": "more than one distinct (operation, target) was present — the approval was ambiguous",
    "option_not_anchored": "the command appeared only in the question text, not in the selected option",
    "not_dangerous": "the selected command is not one that the merge guard gates — it runs without a token, so no authorization is needed",
    "compound_command": "the selected option chained multiple commands with a shell command separator — approve one command at a time",
    "duplicate_question": "two questions in the bundle carry the same text, so the answer cannot be bound to one of them — give each approval its own question text",
    "too_many_commands": "the bundle approved more than four commands — ask for them in smaller bundles",
    "non_ascii_target": "the PR number, branch, flag or repository in the selected command holds a non-ASCII character (a fullwidth digit or a look-alike letter) — spell the command in plain ASCII",
    "unbindable_shape": "the selected command cannot be bound to an approval: its directory, branch or remote is spelled with a shell expansion or cannot be resolved, an earlier part of the line changes where a remote points or sets a GIT_ variable, or a loop would run it more than once — show the user the exact command and ask them to run it themselves with `! <command>`",
    "repo_unresolvable": "the repository or branch the command would act on could not be determined (no -R/--repo and no readable origin remote where it runs, or a placeholder such as :fullpath / {owner}/{repo} / :branch that a leading cd, a wrapped shell or a GH_REPO-style variable would expand elsewhere) — name the repository with -R, or spell the project and branch out in the path",
}


# #1059: space-replace wrapper backticks/curly quotes in the mint scan surface so a wrapper
# char cannot glue onto a flag value; it can only remove characters, so set-equality stays
# exact (an extra exec-time flag still refuses).
_FLAG_SCAN_WRAPPER_TABLE = {ord(c): " " for c in "`‘’“”"}


def _strip_command_wrapper(flag_scan_text: str) -> str:
    """#1059: space-replace markdown command-wrapper delimiters (backtick + curly
    quotes) on the mint's flag-scan surface so its #1042 privileged-flag set matches
    the read side's on the bare command. See `_FLAG_SCAN_WRAPPER_TABLE` rationale."""
    return flag_scan_text.translate(_FLAG_SCAN_WRAPPER_TABLE)


def _leg_bounded_flag_scan_surface(flag_scan_text: str, the_command: str) -> str:
    """#1083: bound the privileged-flag scan to the minted command's own leg in the option text.

    Anchor at the command's region, then the read side's two-tier leg selection over the
    anchored suffix. Wider than the region (a flag after a quoted arg still binds), narrower
    than the text (a flag in a benign leg does not). Any failure falls back to the_command
    alone (fewer flags -> mismatch -> deny), never a wider surface.
    """
    idx = flag_scan_text.find(the_command)
    if idx == -1:
        return the_command
    suffix = flag_scan_text[idx:]
    return (
        _single_destructive_leg(suffix)
        or _single_detectable_leg(suffix)
        or the_command
    )


def _has_options(questions: list) -> bool:
    return any(
        isinstance(q, dict) and isinstance(q.get("options"), list) and q.get("options")
        for q in questions
    )


def _clicked_options(questions: list, answers: dict) -> list:
    """One entry per single-select question with options, in order: the clicked option's
    "<label> <description>" (exact label match, and the label an approval), the refusal code
    "label_mismatch" for an answer that matches no label (incl. "Other"), "not_an_approval"
    for a clicked label that is none while its option names a command, or None for no answer
    or a plain decline. multiSelect and free-text questions contribute nothing: the click on
    an approving label is the consent, free text never mints.
    Entries are (question text, text | None, refusal | None)."""
    clicked = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        qtext = q.get("question", "")
        qtext = qtext if isinstance(qtext, str) else ""
        options = q.get("options", [])
        options = options if isinstance(options, list) else []
        # multiSelect never mints. Consent is the click on an approving LABEL (is_approval_label).
        if bool(q.get("multiSelect", False)) or not options:
            continue
        # KD-12: key the answer to its SPECIFIC question (no iter-fallback).
        answer = answers.get(qtext)
        # Mint source is the clicked option by exact label; no match (incl. "Other") refuses,
        # never falls back to free text.
        selected_text = _selected_option_text(options, answer)
        if selected_text is None:
            clicked.append((qtext, None,
                            "label_mismatch" if isinstance(answer, str) and answer else None))
        elif not is_approval_label(answer):
            clicked.append((qtext, None, "not_an_approval"
                            if locate_command_region(selected_text) is not None else None))
        else:
            clicked.append((qtext, selected_text, None))
    return clicked


def _mint_context_from_bundle(questions: list, answers: dict) -> MintResult:
    """Decide whether an AskUserQuestion bundle authorizes exactly ONE destructive command.

    Consent is the click on a command-bearing option; prose is never read to second-guess it.
    Steps: no options -> refuse; clicked option by exact label (multiSelect or no match ->
    refuse); exactly one distinct (op, target) among clicked options; compound refuse; danger
    write-gate; extract context and privileged flags. Accepted residuals (owner decision) and
    the two structural guarantees are in the design notes.
    """
    # No options -> never mint. Redundant with anchoring today; kept so the rule is stated here.
    if not _has_options(questions):
        return MintResult(None, "no_options")
    # The operator's ACTION SURFACE = the CLICKED option(s); free-text never mints.
    selected_option_texts: list[str] = []
    for _qtext, text, refusal in _clicked_options(questions, answers):
        if refusal is not None:
            return MintResult(None, refusal)
        if text is not None:
            selected_option_texts.append(text)
    return _mint_context_from_texts(selected_option_texts)


# A bundle never mints more approvals than AskUserQuestion can carry questions.
MAX_MINTS_PER_BUNDLE = 4


def _mint_contexts_from_bundle(questions: list, answers: dict) -> tuple:
    """(contexts, refusal codes): one approval per clicked option that names exactly ONE
    destructive command, each judged alone by the single-command rules (_mint_context_from_texts).

    An option naming no command, two commands or a compound mints nothing; so does a question
    whose text any other question in the bundle repeats (multiSelect and option-less ones
    too: its answer cannot be told apart). The whole bundle mints nothing when any answer
    matches no option label (a typed answer may object to its siblings) or when more than
    MAX_MINTS_PER_BUNDLE options approve. Two options naming the same binding yield one
    context.
    """
    if not _has_options(questions):
        return [], ["no_options"]
    clicked = _clicked_options(questions, answers)
    if any(refusal == "label_mismatch" for _qtext, _text, refusal in clicked):
        return [], ["label_mismatch"]
    texts = [q.get("question") if isinstance(q.get("question"), str) else ""
             for q in questions if isinstance(q, dict)]
    contexts: list = []
    refusals: list = []
    approving = 0
    for qtext, text, refusal in clicked:
        if refusal is None and text is not None and texts.count(qtext) > 1:
            refusal = "duplicate_question"
        if refusal is None and text is not None:
            context, refusal = _mint_context_from_texts([text])
            approving += context is not None
            if context is not None and context not in contexts:
                contexts.append(context)
        if refusal is not None:
            refusals.append(refusal)
    if approving > MAX_MINTS_PER_BUNDLE:
        return [], ["too_many_commands"]
    return contexts, refusals


def _mint_context_from_texts(selected_option_texts: list) -> MintResult:
    """The single-command decision over clicked option texts (see _mint_context_from_bundle)."""
    # GAP5: refuse a compound found in the extracted command regions (not the raw text, which
    # holds prose separators). A bare compound truncates at the separator here; the read side's
    # compound refuse is the backstop for that case.
    for _option_text in selected_option_texts:
        for _region in locate_command_regions(_option_text):
            _spelled = canonical_spelling(_region)
            # One approval, one operation: a second one riding in a substitution, a redirect
            # target, a here-string or a wrapped script refuses like a chained one.
            if is_compound_destructive_command(_spelled) or holds_second_operation(_spelled):
                return MintResult(None, "compound_command")

    # ── Step 3: distinct-(op,target) multiplicity over the CLICKED options ONLY
    # (pure-floor scope — the question prose is NOT cross-checked): ==1 mints, >1
    # refuses (a single click carrying 2 commands), ==0 yields no token. ──
    bundle_pairs = _collect_pairs(selected_option_texts)
    if len(bundle_pairs) != 1:
        return MintResult(
            None, "no_command" if len(bundle_pairs) == 0 else "multiple_commands"
        )
    (the_op, the_target), the_command = next(iter(bundle_pairs.items()))

    # 3b: the minted pair must come from a clicked option (true by construction; kept explicit).
    if (the_op, the_target) not in _collect_pairs(selected_option_texts):
        return MintResult(None, "option_not_anchored")

    # GAP1: mint only for a command the read floor gates (is_dangerous_command); a
    # not-dangerous command runs without a token anyway. Checks below read the canonical spelling.
    typed_command, the_command = the_command, canonical_spelling(the_command)
    if not is_dangerous_command(the_command):
        return MintResult(None, "not_dangerous")

    # 5: extract op/target from the_command and flags from its leg-bounded window (#1083).
    # Strip wrappers BEFORE the anchor find; order is load-bearing (#1059).
    context = extract_command_context(
        _extraction_surface(the_command),
        flag_scan_text=canonical_spelling(_leg_bounded_flag_scan_surface(
            _strip_command_wrapper(" ".join(selected_option_texts)), typed_command
        )),
    )
    # amend_3 (#141): bind the approval to the repo the command acts on, from the WHOLE
    # command region (a leading `cd other &&` is part of what the click approved).
    # Unresolvable -> refuse: the token could otherwise authorize the call in another repo.
    bound = command_repo_binding(context, the_command)
    if bound is None:
        return MintResult(None, "repo_unresolvable")
    # A binding spelled with anything but ASCII (fullwidth or other-script digits, look-alike
    # letters) reads as a target it is not: never mint for it.
    # A directory, branch or word that cannot be read, an earlier leg that moves a remote and
    # a loop around the operation bind nothing: the user runs such a command.
    if unbindable_reason(the_command, bound) is not None:
        return MintResult(None, "unbindable_shape")
    # The guarded leg is approved word for word (`typed`); a wrapped operation as the whole
    # command typed and the directory it runs from (`wrapper`, `cwd`): another refspec,
    # option, wrapper, host or directory is another command. The read side compares them.
    bound = {**bound, **approval_binding(the_command)}
    if not _all_ascii(bound):
        return MintResult(None, "non_ascii_target")
    return MintResult(bound, None)


def _all_ascii(value) -> bool:
    """True when every string in a context value (nested lists and dicts included) is ASCII."""
    if isinstance(value, dict):
        return all(_all_ascii(k) and _all_ascii(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_all_ascii(item) for item in value)
    return value.isascii() if isinstance(value, str) else True


def _bundle_has_command(questions: list) -> bool:
    """True if a destructive command appears anywhere in the bundle; gates the no-mint advisory
    so ordinary questions stay silent.
    """
    for q in questions:
        if not isinstance(q, dict):
            continue
        qtext = q.get("question", "")
        if isinstance(qtext, str) and locate_command_region(qtext) is not None:
            return True
        options = q.get("options", [])
        if not isinstance(options, list):
            continue
        for opt in options:
            if not isinstance(opt, dict):
                continue
            blob = str(opt.get("label", "")) + " " + str(opt.get("description", ""))
            if locate_command_region(blob) is not None:
                return True
    return False


def _no_mint_advisory(refusal_reason: str, minted: int = 0) -> str:
    """Build the observer-style self-teaching no-mint advisory text (B2 / #1052):
    NAME the specific failing gate, then point at the canonical peer-review.md
    approval template. Pure string builder — emitted as `additionalContext` with
    exit 0; it NEVER writes a token or authorizes anything."""
    why = _REFUSAL_DIAGNOSTICS.get(
        refusal_reason, "the approval did not match the canonical form"
    )
    scope = "NO authorization token was minted" if not minted else (
        f"{minted} authorization token(s) were minted and one clicked option minted NONE")
    return (
        "PACT merge-guard: an AskUserQuestion approval was issued but "
        + scope + " — " + why + ". The merge is HELD until a "
        "matching approval is given. Re-approve via the canonical template in "
        "pact-plugin/commands/peer-review.md: a SINGLE-select question, a short "
        '"Yes, merge" affirmative label, and the command in THAT option\'s '
        "DESCRIPTION in backticks (e.g. Run `gh pr merge <N>` to merge this PR), "
        "with <N> the only number anywhere in the prompt. An option approves only under "
        + APPROVAL_LABELS + "."
    )


_MAX_NAME_TRIES = 32


def write_token(context: dict, token_dir: Path | None = None) -> str | None:
    """Write an authorization token file; returns its path, or None on failure or refusal."""
    # Refuse a context with none of pr_number, branch, operation_type: it would match any
    # command through the read side's permissive fallback (#700).
    if not isinstance(context, dict):
        print(
            "[security] sparse context: non-dict context, refusing token write",
            file=sys.stderr,
        )
        return None
    has_pr = bool(context.get("pr_number"))
    has_branch = bool(context.get("branch"))
    has_op = bool(context.get("operation_type"))
    if not (has_pr or has_branch or has_op):
        print(
            "[security] sparse context: AskUserQuestion text yielded no "
            "extractable pr_number, branch, or operation_type — refusing "
            "token write to avoid wildcard-allow against subsequent "
            "destructive commands.",
            file=sys.stderr,
        )
        return None

    # Never mint without operation_type (the read side denies such a token anyway).
    if not has_op:
        print(
            "[security] refusing token write: no operation_type — an untyped "
            "token cannot positively match any command on the fail-closed read "
            "side.",
            file=sys.stderr,
        )
        return None

    if token_dir is None:
        token_dir = TOKEN_DIR

    # Clean up stale .consumed token files from prior operations
    _cleanup_consumed_tokens(token_dir)

    now = time.time()
    timestamp = int(now)

    # The minting session's id scopes the token. Minted without one, it is honoured only
    # for a session that has no id either.
    session_id = get_session_id()

    token_data = {
        "created_at": now,
        "expires_at": now + TOKEN_TTL,
        "context": context,
        # N-use budget (#720): the reader claims one use-slot per call (O_EXCL); legacy tokens act
        # as max_uses=1.
        "max_uses": MAX_USES,
        "uses_remaining": MAX_USES,
    }
    if session_id:
        token_data["session_id"] = session_id

    # Layer 5 (I-1): retire a prior unused token for this SAME binding before the O_EXCL
    # create, so two unused tokens never coexist for one binding, and delete expired ones.
    # Live tokens for other bindings stay: one bundle may approve several commands.
    _cleanup_unused_tokens(token_dir, context, session_id)

    # One name per token: several can be minted in the same second (and a .consumed sibling
    # may hold the plain name), so a taken name moves on to the next suffix.
    names = [f"merge-authorized-{timestamp}"] + [
        f"merge-authorized-{timestamp}-{n}" for n in range(1, _MAX_NAME_TRIES)]
    for name in names:
        token_path = token_dir / name
        if os.path.lexists(str(token_path) + ".consumed"):
            continue
        try:
            # Write with secure permissions using os.open for atomic creation
            fd = os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        except OSError:
            return None
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(token_data, f, indent=2)
        except Exception:
            # fd is already closed by fdopen on failure, but file may exist
            try:
                token_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return str(token_path)
    return None


def _same_command_binding(ctx: dict, cmd_binding: dict) -> bool:
    """True when a token context and a command's approval_binding agree on the wrapper, its
    directory and the typed leg: each absent on both sides, or equal."""
    return all(ctx.get(axis) == cmd_binding.get(axis) for axis in ("wrapper", "cwd", "typed"))


def _retire_token_for_command(
    command: str,
    op_type: str,
    token_dir: Path | None = None,
) -> bool:
    """Retire (rename to .consumed) the token for a successful destructive command (I-2).

    Matches op_type AND target AND bound_flags AND session, all from one leg-isolated
    extract_command_context call, like mint and read. Retirement only removes tokens; the only
    residual is an under-retire, backstopped by TTL + MAX_USES. Never raises. Logs "direct" or
    "race-recover" with the op_type. True if a token was retired (by us or a racing path).
    """
    if token_dir is None:
        token_dir = TOKEN_DIR
    pattern = str(token_dir / f"{TOKEN_PREFIX}*")
    current_session = get_session_id()
    # #1097/#1100: retire only the token for this exact (op, target, flags), derived from the
    # same leg the mint bound; the `or command` tail is the shared fail-safe collapse.
    cmd_ctx = extract_command_context(
        _single_destructive_leg(command)
        or _single_detectable_leg(command)
        or command
    )
    cmd_target = _target_value(cmd_ctx)
    cmd_flags = set(cmd_ctx.get("bound_flags", []))
    # The repo, host and remote the command acted on: a sibling token for the same target in
    # another repo is not this command's. Unresolvable: op, target and flags decide, as before.
    cmd_bound = command_repo_binding(cmd_ctx, command) or {}
    cmd_binding = approval_binding(command)
    for path in glob.glob(pattern):
        basename = os.path.basename(path)
        # Skip terminal-rename siblings and per-use markers (mirrors the
        # scan-pattern in merge_guard_pre.find_valid_token).
        if path.endswith(".consumed"):
            continue
        if USE_MARKER_SUFFIX in basename:
            continue
        try:
            with open(path, "r") as f:
                token_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        ctx = token_data.get("context", {})
        if not isinstance(ctx, dict):
            continue
        # Op match + session scope is the minimum to retire.
        if ctx.get("operation_type") != op_type:
            continue
        # Target-aware only when the token has a target; a legacy target-less token falls back to
        # op+session (never the operator's protected token, which has a target).
        token_target = _target_value(ctx)
        if token_target is not None and cmd_target != token_target:
            continue
        # Flags must equal exactly, unconditionally ([] is a real flag set): a bare close must not
        # burn an approved {close, 5, [--delete-branch]} token. Can only retire fewer tokens.
        token_flags = ctx.get("bound_flags", [])
        if not isinstance(token_flags, list) or not all(isinstance(x, str) for x in token_flags):
            # Skip a malformed bound_flags (null, or a list holding a non-str) instead of crashing the
            # scan; valid tokens always hold a list of strings.
            continue
        if set(token_flags) != cmd_flags:
            continue
        if any(ctx.get(axis) is not None and cmd_bound.get(axis) is not None
               and str(ctx[axis]) != str(cmd_bound[axis]) for axis in ("repo", "host", "remote")):
            continue
        # The wrapper, its directory and the typed leg are compared as the read side compares
        # them: both absent, or both equal.
        if not _same_command_binding(ctx, cmd_binding):
            continue
        # Session scoping, as merge_guard_pre reads it: only the minting session's token (a
        # session with no id retires only tokens minted without one).
        if (token_data.get("session_id") or "") != (current_session or ""):
            continue
        try:
            os.rename(path, path + ".consumed")
            print(
                f"[security] merge-authorization token retired "
                f"(via direct, op_type={op_type}) on successful "
                f"{op_type} command",
                file=sys.stderr,
            )
            return True
        except (FileNotFoundError, OSError):
            # Another path retired or removed it first; the log line says race-recover.
            print(
                f"[security] merge-authorization token retired "
                f"(via race-recover, op_type={op_type}) on successful "
                f"{op_type} command",
                file=sys.stderr,
            )
            return True
    return False


def main():
    """Main entry point for the PostToolUse hook."""
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
        # Defense-in-depth via SSOT helper: prefers canonical `tool_response`,
        # falls back to legacy `tool_output` for envelope-rename robustness,
        # warns on dual-envelope payloads (envelope-confusion smell).
        tool_response = extract_tool_response(input_data)

        # Layer 1 Bash branch (I-2): retire the token after a successful destructive command. Three
        # independent reject blocks; always exit 0 / suppressOutput (observation, not permission).
        # A tool_response field mismatch degrades to no retirement (TTL/MAX_USES still bound it).
        tool_name = input_data.get("tool_name", "")
        if tool_name == "Bash":
            command = (
                tool_input.get("command", "") if isinstance(tool_input, dict) else ""
            )

            # Block 1: op_type must be a LAYER1_SUCCESS_STDOUT_PATTERNS key. A command with a long run
            # never held a token (the pre hook refuses it), so skip it here too.
            command = canonical_spelling(command)   # as the pre hook read it
            if bound_long_runs(command) != command:
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)
            op_type = detect_command_operation_type(command)
            if op_type not in LAYER1_SUCCESS_STDOUT_PATTERNS:
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)

            # Block 2: dict-shaped, non-interrupted tool_response (platform success).
            if not isinstance(tool_response, dict):
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)
            if tool_response.get("interrupted") is True:
                print(_SUPPRESS_OUTPUT)
                sys.exit(0)

            # Block 3: per-op stdout success substring; None (force-push writes to stderr) skips it.
            expected_substring = LAYER1_SUCCESS_STDOUT_PATTERNS[op_type]
            if expected_substring is not None:
                stdout_text = tool_response.get("stdout", "")
                if not isinstance(stdout_text, str):
                    print(_SUPPRESS_OUTPUT)
                    sys.exit(0)
                if expected_substring not in stdout_text:
                    print(_SUPPRESS_OUTPUT)
                    sys.exit(0)

            # All blocks passed: retire. The return value never gates the exit.
            _retire_token_for_command(command, op_type)
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        # Fall through to the AskUserQuestion mint path.

        # tool_input: {"questions": [{"question": "...", "options": [...], ...}]}
        if not isinstance(tool_input, dict):
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)
        questions = tool_input.get("questions", [])
        if not isinstance(questions, list) or not questions:
            print(_SUPPRESS_OUTPUT)
            sys.exit(0)

        # tool_response: {"answers": {"<question text>": "<selected label>"}, ...}
        answers = tool_response.get("answers", {}) if isinstance(tool_response, dict) else {}
        if not isinstance(answers, dict):
            answers = {}

        # Mint one approval per clicked option that names exactly one destructive command
        # (see _mint_contexts_from_bundle); a refusal carries a reason code for the advisory.
        contexts, refusals = _mint_contexts_from_bundle(questions, answers)
        for context in contexts:
            token_path = write_token(context)
            if token_path:
                # Strip CR/LF from the logged path (log-line injection parity with merge_guard_pre).
                safe_token_path = token_path.replace("\n", " ").replace("\r", " ")
                print(
                    f"Merge authorization token written: {safe_token_path}",
                    file=sys.stderr,
                )
        # An option that names no command (a "Not yet", a plain question) is not a failed
        # approval when another option minted; any other refusal is always reported.
        refusal_reason = next(
            (r for r in refusals if not (contexts and r in ("no_command", "not_dangerous"))), None)
        if refusal_reason is not None and _bundle_has_command(questions):
            # B2 (#1052): a command-bearing approval minted nothing. Observer advisory naming the failing
            # gate; never authorizes (the pre hook still denies).
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": _no_mint_advisory(refusal_reason, len(contexts)),
                }
            }))
            sys.exit(0)

        print(_SUPPRESS_OUTPUT)
        sys.exit(0)

    except Exception as e:
        # Never block on errors — this is an observer hook
        print(f"Hook warning (merge_guard_post): {e}", file=sys.stderr)
        print(hook_error_json("merge_guard_post", e))
        sys.exit(0)


if __name__ == "__main__":
    main()
