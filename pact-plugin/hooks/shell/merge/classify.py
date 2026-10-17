"""
Location: pact-plugin/hooks/shell/merge/classify.py
Summary: The merge guard's judges: detect_command_operation_type, is_dangerous_command,
         is_compound_destructive_command and the command-region locator the mint side uses.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: _LONG_RUN_MIN, _LONG_RUN_KEEP, _RUN_CHAR, _LONG_RUN_RE, _RUN_KEYWORD_RE, _shorten_run, bound_long_runs, _FLOOD_VERBS, _FLOOD_VALUE_FLAGS, flag_flood, detect_command_operation_type, _detect_op_pass, _LEG_OPENER_RE, _HERE_STRING_READERS, _STRING_RUNNERS, _OWN_ARM_PROGRAMS, _HEREDOC_MARK_RE, _ARRAY_OPEN_RE, _WRAPPED, _BRACED, _BRACES_UNREAD, _BRACE_PROGRAMS, _here_string, _shell_strings, _leg_words, _leg_payloads, _open_groups, _with_heredocs, _substitution_readings, _wrapped_readings, unmintable_danger, locate_command_regions, locate_command_region, _flag_condition_danger_op, _stripped_surface_danger, is_dangerous_command, _RM_HEAD_RE, _leg_is_destructive, _single_destructive_leg, _single_detectable_leg, is_compound_destructive_command.
"""

from __future__ import annotations

import os
import re

from shell.merge.braces import expand_braces
from shell.merge.arms import (DANGEROUS_PATTERNS, _API_LITERAL_ARMS, _BRANCH_DELETE_LITERAL_ARMS, _CLOSE_LITERAL_ARMS, _FORCE_PUSH_LITERAL_ARMS, _GH_API_PREFIX, _GH_PR_CLOSE_RE, _GH_PR_MERGE_RE, _GIT_PREFIX, _GLAB_MR_CLOSE_RE, _GLAB_MR_DELETE_RE, _GLAB_MR_MERGE_RE, _MAX_GLOBAL_FLAG_TOKENS, _PER_LEG_OPS, _PUSH_MAIN_DST_RE, _PUSH_OPEN_DST_RE, _UNMINTABLE_SHELLS)
from shell.merge.checkout import (_parse_push_args, _push_is_dry_run, implicit_push_to_main, push_option_merge)
from shell.merge.flags import (_normalized_flags)
from shell.merge import inert_legs as _inert_legs
from shell.merge.inert_legs import (_neutralize_inert_help_legs)
from shell.merge.strip import (_has_eval_with_heredoc, _join_line_continuations, _split_into_legs, _strip_non_executable_content)
from shell.merge.targets import (_BARE_COMMAND_RE, _QUOTED_COMMAND_RE, _api_merge_leg_endpoint, _gh_api_close_leg_pr, _gitlab_api_leg_op)
from shell.merge.targets_git import (_extract_mass_delete_target, _extract_remote_ref_delete_target, _push_positionals)
from shell.merge.unmintable import (_api_body_danger, _flag_before_verb_danger, _inline_alias_danger, _raw_http_danger)
from shell.merge.views import (_executable_prefix, _executed_surface_view, _has_command_substitution, _leg_program_tokens, _mask_shell_quotes, _read_long_once, _shell_tokenize, _slice_stripped_legs, _strip_surrounding_quotes, canonical_spelling)
from shell.words import (HEREDOC_MARK, Unreadable,
                         normalize_line_continuations as _normalize_line_continuations, read_script, substitution_spans)

# Long runs: the regex arms are quadratic in one long non-space run (a 100 KB blob held a call
# for minutes). Runs that cannot carry a command are shortened before matching.
_LONG_RUN_MIN = 512
_LONG_RUN_KEEP = 64
_RUN_CHAR = r"[^\s'\"`$\\;&|<>(){}]"
_LONG_RUN_RE = re.compile(r"(?<!%s)%s{%d,}" % (_RUN_CHAR, _RUN_CHAR, _LONG_RUN_MIN))
_RUN_KEYWORD_RE = re.compile(
    r"merge|close|delet|refs|protect|branch|pull|push|force|state|graphql|mutation"
    r"|content|main|master|head|api|git|glab",
    re.IGNORECASE,
)


def _shorten_run(match: "re.Match[str]") -> str:
    run = match.group(0)
    keep = [(0, _LONG_RUN_KEEP), (len(run) - _LONG_RUN_KEEP, len(run))]
    keep += [(max(0, hit.start() - _LONG_RUN_KEEP), hit.end() + _LONG_RUN_KEEP)
             for hit in _RUN_KEYWORD_RE.finditer(run)]
    pieces, cursor = [], 0
    for start, stop in sorted(keep):
        if start > cursor:
            pieces.append("~")
        start = max(start, cursor)
        if stop > start:
            pieces.append(run[start:stop])
            cursor = stop
    return "".join(pieces)


def bound_long_runs(command: str) -> str:
    """`command` with each long run that cannot carry a command shortened (see LONG
    RUNS above); equal to `command` when it has none. Linear in len(command)."""
    if len(command) < _LONG_RUN_MIN:
        return command
    return _LONG_RUN_RE.sub(_shorten_run, command)


_FLOOD_VERBS = {"git": frozenset({"push", "branch"}), "gh": frozenset({"pr", "api"}),
                "glab": frozenset({"mr", "api"})}
_FLOOD_VALUE_FLAGS = {"git": frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace",
                                        "--config-env", "--super-prefix"}),
                      "gh": frozenset({"-R", "--repo", "--hostname"}),
                      "glab": frozenset({"-R", "--repo", "--hostname"})}


def flag_flood(command: str) -> bool:
    """True when a git/gh/glab leg carries more words before its subcommand, or a git push
    more flags after `push`, than the bounded walks read (_MAX_GLOBAL_FLAG_TOKENS). Past that
    bound the arms stop recognizing the command, so the guard refuses it outright, like a long
    run (1.4.1 U13), rather than read it as safe. Linear in the command's length."""
    for leg in _split_into_legs(command):
        words = leg.split()
        for i, word in enumerate(words):
            tool = os.path.basename(word)
            if tool not in _FLOOD_VERBS:
                continue
            j = i + 1
            while j < len(words) and words[j].startswith("-"):
                j += 2 if words[j] in _FLOOD_VALUE_FLAGS[tool] else 1
            if j >= len(words) or words[j] not in _FLOOD_VERBS[tool]:
                break
            if j - i - 1 > _MAX_GLOBAL_FLAG_TOKENS:
                return True
            if words[j] == "push" and sum(
                    1 for w in words[j + 1:] if w.startswith("-")) > _MAX_GLOBAL_FLAG_TOKENS:
                return True
            break
    return False


def detect_command_operation_type(command: str) -> str | None:
    """Detect the operation type of a destructive command."""
    # Two passes: the executed-surface view first (quoted prose masked, so an op named inside a
    # --comment does not classify), then the raw command.
    if push_option_merge(command):
        return "merge"
    view = _executed_surface_view(command)
    legs = _split_into_legs(command)
    op = (
        _detect_op_pass(command, view, legs)      # view pass (gh-pr prose arms on view)
        or _detect_op_pass(command, command, legs)  # raw fallback == pre-view classifier
    )
    if op is not None:
        return op
    # Per-leg arms: the union arm is first-leg anchored, so ops in later legs need a per-leg call,
    # made only after both passes return None.
    for _leg in legs:
        _lop = _flag_condition_danger_op(_leg)
        if _lop in _PER_LEG_OPS:
            return _lop
    if implicit_push_to_main(command):
        return "push-to-main"       # GIT CHECKOUT STATE: a push of main that names no main ref
    return None


def _detect_op_pass(
    command: str,
    surface: str,
    raw_legs: "list[str]",
) -> str | None:
    """One precedence-ordered classification pass, parameterized by surface."""
    # Order matters: close --delete-branch is a close first, as in extract_command_context.
    if _GH_PR_MERGE_RE.search(surface):
        return "merge"
    if _GH_PR_CLOSE_RE.search(surface):
        # gh pr close --delete-branch is a close-type operation per the
        # write-side classifier. Branch-delete-via-pr-close is folded into
        # the close class on both sides for symmetric authorization.
        return "close"
    # GitLab merge requests via glab (FIX-MERGE f3): the same presence-detector shape as
    # the gh-pr prose arms above, so they consult the same surface.
    if _GLAB_MR_MERGE_RE.search(surface):
        return "merge"
    if _GLAB_MR_CLOSE_RE.search(surface):
        return "close"
    if _GLAB_MR_DELETE_RE.search(surface):
        return "mr-delete"
    # Force push (not --force-with-lease), per leg over _FORCE_PUSH_LITERAL_ARMS.
    for _leg in raw_legs:
        if any(arm.search(_leg) for arm in _FORCE_PUSH_LITERAL_ARMS):
            return "force-push"
    # Push to main/master (plain or lease) is recognised by the refspec predicate in
    # _flag_condition_danger_op, reached through the union-arm fallback below.
    _is_api_form = (
        re.search(_GH_API_PREFIX, command, re.IGNORECASE)
        or re.search(r"\b(?:curl|wget)\b", command, re.IGNORECASE)
    )
    if _is_api_form and "git/refs" in command:
        if re.search(r"(?:\b|(?<=-X))DELETE\b", command, re.IGNORECASE):
            return "branch-delete"
        if re.search(r"(?:\b|(?<=-X))(?:PATCH|POST|PUT)\b", command, re.IGNORECASE):
            return "force-push"
    # Protection mutation via API (DELETE/PUT/PATCH on branches/<b>/protection) is its own op.
    if _is_api_form and re.search(r"branches/.*/protection", command):
        if re.search(r"(?:\b|(?<=-X))(?:DELETE|PUT|PATCH)\b", command, re.IGNORECASE):
            return "branch-protection"
    # API merge, gh api only: a mutating method on pulls/<N>/merge. curl/wget merges stay gated
    # but unmintable (their value-flag space cannot be modelled).
    for _leg in raw_legs:
        if _api_merge_leg_endpoint(_leg) is not None:
            return "merge"
    # GitLab REST via `glab api`, and the GitHub REST PR close (FIX-MERGE f3). One helper
    # per form recognizes AND extracts, so a leg classifies IFF its target is extractable.
    for _leg in raw_legs:
        _gl = _gitlab_api_leg_op(_leg)
        if _gl is not None:
            return _gl[0]
    # The REST close is decided by a BODY field (`state=closed`), which the executed-
    # surface strip removes by design, so this form is read on the RAW legs (quote-aware
    # shlex inside the helper keeps a decoy value like `-f title="state=closed"` inert).
    for _leg in _slice_stripped_legs(command):
        if _gh_api_close_leg_pr(_leg) is not None:
            return "close"
    # Branch delete, per leg over _BRANCH_DELETE_LITERAL_ARMS.
    for _leg in raw_legs:
        if any(arm.search(_leg) for arm in _BRANCH_DELETE_LITERAL_ARMS):
            return "branch-delete"
    # Additive quote-aware flag fallback: clustered spellings (-Df, -fD, --delete -f) the literal
    # arms miss. It can only add recognition, never suppress it.
    return _flag_condition_danger_op(command)


# Words a command may follow inside its leg (`then eval ...`, `{ sh -c ...`, `function f {`,
# `coproc eval ...`, `coproc NAME {`, zsh's `noglob eval ...`): the program is the word after them.
_LEG_OPENER_RE = re.compile(
    r"\s*(?:(?:if|then|else|elif|do|while|until|!|\{|\(|noglob|nocorrect|coproc(?:\s+\w+(?=\s+\{))?"
    r"|function\s+[^\s;(){}]+(?:\s*\(\s*\))?)(?:\s+|$))*")
# Programs that run a `<<<` here-string as their script.
_HERE_STRING_READERS = _UNMINTABLE_SHELLS | {"source", "."}
_STRING_RUNNERS = frozenset({"ssh", "watch"})         # programs that run a quoted string as a script
_OWN_ARM_PROGRAMS = frozenset({"git", "gh", "glab", "curl", "wget"})
_HEREDOC_MARK_RE = re.compile("<<" + HEREDOC_MARK.format(r"(\d+)"))
_ARRAY_OPEN_RE = re.compile(r"[\w\]]\+?=$")          # `name=(` opens a list of words, not commands
_WRAPPED = "a destructive command inside a subshell, a substitution or a function body"
_BRACED = "a destructive command spelled with a brace list"
_BRACES_UNREAD = ("a git, gh or glab command with a brace list too long or too tangled to "
                  "spell out (write the words out)")
_BRACE_PROGRAMS = frozenset({"git", "gh", "glab"})


def _here_string(tokens: "list[str]") -> "str | None":
    """The `<<<` word of a leg's tokens (`<<< word` or `<<<word`), else None."""
    for k, tok in enumerate(tokens):
        if tok.startswith("<<<"):
            return tok[3:] or " ".join(tokens[k + 1:k + 2]) or None
    return None


def _shell_strings(tokens: "list[str]") -> "list[str]":
    """The string after each -c option that follows a shell word, anywhere in a leg's tokens
    (`sh -c S`, `docker exec c bash -lc S`, `uv run sh -e -c S`): the shell runs it as a script
    whatever program starts it. An option with `c` in its cluster counts; `--long` does not."""
    shell_at = next((k for k, tok in enumerate(tokens)
                     if os.path.basename(tok) in _UNMINTABLE_SHELLS), len(tokens))
    return [tokens[k + 1] for k in range(shell_at + 1, len(tokens) - 1)
            if tokens[k].startswith("-") and not tokens[k].startswith("--") and "c" in tokens[k]]


def _leg_words(leg: str) -> "list[str] | None":
    """The tokens of a raw leg from its program word on, past the words a command may follow
    (_LEG_OPENER_RE); a leading `<<< word` is moved behind the program it feeds."""
    body = leg[_LEG_OPENER_RE.match(leg).end():]
    tokens = _leg_program_tokens(body)
    if body.startswith("<<<"):                          # `<<< word program`: the word first
        lead = _shell_tokenize(body) or []
        skip = 2 if lead[:1] == ["<<<"] else 1
        if len(lead) > skip:
            tokens = lead[skip:] + ["<<<" + (_here_string(lead) or "")]
    return tokens


def _leg_payloads(tokens: "list[str]") -> "list[tuple[str, str]]":
    """(script, what carries it) for each string the leg's program runs as a script: eval's
    arguments, a shell's -c string, a here-string a shell or `source` reads, a quoted string
    ssh or watch runs. Empty for a git, gh, glab, curl or wget leg with no shell -c string."""
    program = os.path.basename(tokens[0])
    if program in _OWN_ARM_PROGRAMS and not _shell_strings(tokens):
        return []
    if program == "eval":
        # eval runs its arguments joined by spaces, so the payload is read the same way
        # whatever quoting (or none) each argument had.
        payloads = [(" ".join(tokens[2:] if tokens[1:2] == ["--"] else tokens[1:]),
                     "an eval string")]
    else:
        # A shell's -c string wherever the shell stands in the leg.
        payloads = [(inner, "a shell -c string") for inner in _shell_strings(tokens)]
        # The same for a here-string: a shell word anywhere in the leg reads it
        # (`uv run bash <<< S`), as `source` and `.` do when they are the program.
        if program in _HERE_STRING_READERS or any(
                os.path.basename(tok) in _UNMINTABLE_SHELLS for tok in tokens):
            payloads.append((_here_string(tokens), "a here-string"))
        if program in _STRING_RUNNERS:
            payloads += [(tok, f"a string that {program} runs") for tok in tokens[1:] if " " in tok]
    return [(inner, wrapper) for inner, wrapper in payloads if inner]


def _open_groups(script: str) -> str:
    """`script`, same length, with each unquoted `(`, `)` and backtick spelled `;`: the words
    inside a subshell, a process substitution, a function body or a case arm are then delimited
    as the shell delimits them (`(git push origin main)` names `main`, not `main)`). A
    `name=( ... )` word list is left as typed."""
    masked, out, i = _mask_shell_quotes(script), list(script), 0
    while i < len(masked):
        char = masked[i]
        if char == "\\":
            i += 2
            continue
        if char == "(" and _ARRAY_OPEN_RE.search(masked[max(0, i - 80):i]):
            depth = 1
            while depth and i + 1 < len(masked):
                i += 1
                if masked[i] == "\\":                   # an escaped bracket opens and closes nothing
                    i += 1
                    continue
                depth += (masked[i] == "(") - (masked[i] == ")")
        elif char in "()`":
            out[i] = ";"
        i += 1
    return "".join(out)


def _with_heredocs(text: str, heredocs: "list[tuple[bool, str]]") -> str:
    """read_script's text with each heredoc body back under its operator's line, the delimiter
    quoted when it was typed quoted, so the body is read again as data or as script."""
    lines = []
    for line in text.split("\n"):
        found = [int(m.group(1)) for m in _HEREDOC_MARK_RE.finditer(line)]
        found = [n for n in found if n < len(heredocs)]
        lines.append(_HEREDOC_MARK_RE.sub(
            lambda m: ("<<REPACT_EOF_{}" if heredocs[int(m.group(1))][0] else "<<'REPACT_EOF_{}'")
            .format(m.group(1)) if int(m.group(1)) < len(heredocs) else m.group(), line))
        for n in found:
            lines += [heredocs[n][1], f"REPACT_EOF_{n}"]
    return "\n".join(lines)


def _substitution_readings(command: str) -> "tuple[str, list[tuple[bool, str]], list[str]]":
    """(read_script's text with each `$(...)` and backtick substitution spelled `$_`; its
    heredocs; the substitutions' commands, those of an unquoted-delimiter heredoc body
    included). Text inside single quotes, and inside double quotes outside a substitution, is
    data. Text read_script refuses is read as typed."""
    try:
        script, heredocs = read_script(command)
    except Unreadable:
        script, heredocs = command, []
    try:
        spans = substitution_spans(script) if _has_command_substitution(command) else []
        inner = [_with_heredocs(found, heredocs) for _start, _end, found in spans]
        for bare, body in heredocs:
            if bare:
                inner += [found for _start, _end, found in substitution_spans(body, quoting=False)]
    except (Unreadable, IndexError, RecursionError):
        spans, inner = [], []
    plain, last = [], 0
    for start, end, _found in spans:
        plain += [script[last:start], "$_"]
        last = end
    return "".join(plain) + script[last:], heredocs, inner


# The classifier and the operation count each read the groups and substitutions of a text.
_open_groups, _substitution_readings = map(_read_long_once, (_open_groups, _substitution_readings))


def _wrapped_readings(command: str) -> "tuple[str | None, list[str]]":
    """(the command with its groups opened, or None when it has none; the commands of its
    substitutions, as _substitution_readings reads them)."""
    plain, heredocs, inner = _substitution_readings(command)
    # A heredoc body is opened like the script: the strip then keeps it when a shell reads it
    # (`bash <<EOF`, `cat <<EOF | sh`, `source /dev/stdin <<EOF`) and drops it as data otherwise.
    flat, bodies = _open_groups(plain), [(bare, _open_groups(body)) for bare, body in heredocs]
    return (_with_heredocs(flat, bodies) if flat != plain or bodies != heredocs else None), inner


def unmintable_danger(command: str, _depth: int = 0, _wrapped: bool = True) -> "str | None":
    """Why `command` holds a merge/close/delete the guard gates but can never bind an approval
    to, or None. Checked on the RAW legs (see the block comment above). `_wrapped` off skips
    the reading of groups and substitutions (the text passed is already that reading)."""
    for leg in _slice_stripped_legs(_normalize_line_continuations(command)):
        tokens = _leg_words(leg)
        if not tokens:
            continue
        program = os.path.basename(tokens[0])
        if ("{" in leg and (program in _BRACE_PROGRAMS or "{" in tokens[0])
                and expand_braces(leg)[1]):
            return _BRACES_UNREAD
        if program not in _OWN_ARM_PROGRAMS or _shell_strings(tokens):
            for inner, wrapper in _leg_payloads(tokens):
                if _depth > 2:
                    # Nested past the depth this reads: the payload is unknown, so it is not passed.
                    return f"a command wrapped too deeply in {wrapper} to read"
                if unmintable_danger(inner, _depth + 1) or is_dangerous_command(inner):
                    return f"a destructive command wrapped in {wrapper}"
            if program not in _OWN_ARM_PROGRAMS:
                continue
        if program in ("gh", "glab") and "api" in tokens[1:4]:
            reason = _api_body_danger(tokens)
        elif program in ("gh", "glab"):
            reason = _flag_before_verb_danger(program, tokens)
        elif program == "git":
            reason = _inline_alias_danger(tokens)
        elif program in ("curl", "wget"):
            reason = _raw_http_danger(tokens)
        else:
            reason = None
        if reason:
            return reason
    if _wrapped:
        # Additive: the same judges over the words as the shell delimits them inside a group, and
        # over each substitution's command. It can add a refusal, never remove one.
        flat, inner = _wrapped_readings(command)
        if ((flat is not None and is_dangerous_command(flat, False) or any(map(is_dangerous_command, inner)))
                and not is_dangerous_command(command, False)):   # a refusal the plain reading lacks
            return _WRAPPED
        # Additive too: the command as the shell spells it after brace expansion.
        braced = expand_braces(command)[0]
        if braced != command and is_dangerous_command(braced) and not is_dangerous_command(command, False):
            return _BRACED
    return None



def locate_command_regions(text: str) -> list[str]:
    """Return ALL gh/git destructive-command regions in ONE string, in document
    order.
    """
    # Join line continuations before the region scans, as the read side does (mint == read).
    text = _normalize_line_continuations(text)
    regions: list[str] = []
    covered: list[tuple[int, int]] = []
    # Regions are classified in canonical spelling but returned as typed; quoted regions first.
    for match in _QUOTED_COMMAND_RE.finditer(text):
        candidate = match.group(1) or match.group(2) or match.group(3)
        if candidate and detect_command_operation_type(canonical_spelling(candidate)) is not None:
            covered.append((match.start(), match.end()))
            regions.append(candidate)
    # Bare spans are located on the quote-masked view, so a quoted argument mid-command does not
    # truncate the span and drop a trailing flag.
    masked = _mask_shell_quotes(text)
    for match in _BARE_COMMAND_RE.finditer(masked):
        if any(
            c_start <= match.start() and match.end() <= c_end
            for c_start, c_end in covered
        ):
            continue
        span = text[match.start():match.end()].strip()
        if detect_command_operation_type(canonical_spelling(span)) is not None:
            regions.append(span)
    return regions


def locate_command_region(text: str) -> str | None:
    """Convenience: the first command region in `text`, else None. SINGLE
    string arg (same D3 invariant as locate_command_regions)."""
    regions = locate_command_regions(text)
    return regions[0] if regions else None


def _drop_closing_quote(token: str) -> str:
    """`token` without one unpaired closing quote (`main"` reads `main`); any other token as is.
    The quote test is views._strip_surrounding_quotes, fed the last character as an opener."""
    paired = _strip_surrounding_quotes(token[-1:] + token)
    return paired if len(paired) < len(token) else token


def _flag_condition_danger_op(command: str) -> str | None:
    """P4 union arm: classify the FIRST EXECUTABLE LEG of `command` by a quote-aware
    NORMALIZED-FLAG danger CONDITION across every flag spelling, returning the op-class ("close"
    / "branch-delete" / "force-push" / "remote-ref-delete" / "remote-mass-delete" /
    "push-to-main") iff a condition fires, else None.
    """
    prefix = _executable_prefix(command)
    if prefix is None:
        # Unbalanced quote OR process substitution → abstain; the literal floor
        # decides. Procsub is never an honest destructive form (the helper's own
        # rationale) — an exotic procsub+cluster combo is an accepted under-block.
        return None
    tokens = _shell_tokenize(prefix)
    if tokens is None:
        return None  # unparseable → this arm abstains; the literal floor decides (honest-mistake: no metachar catch-all)
    # close --delete-branch — covers `-d`, clustered `-cd`, `--delete-branch`; the
    # literal floor matches ONLY the spelled-out `--delete-branch` (the #2 gap).
    if _GH_PR_CLOSE_RE.search(prefix):
        if "--delete-branch" in _normalized_flags(tokens, "gh"):
            return "close"
    # git branch force-delete — covers `-D`, `-Df`, `-fD`, `--delete -f`/`--force`
    # in any order; the literal floor matches ONLY `-D\b` / `--delete --force` /
    # `--force --delete` (the #4 gap).
    if re.search(_GIT_PREFIX + r"branch\b", prefix):
        gf = _normalized_flags(tokens, "git")
        if "-D" in gf or ("--delete" in gf and "--force" in gf):
            return "branch-delete"
    # git push --force — covers clustered short forms; `--force-with-lease` is the
    # SAFE exclusion (a non-history-rewriting push). Redundant with the literal floor
    # today (`-[a-zA-Z]*f` already catches the clusters) but kept for op-class parity.
    if re.search(_GIT_PREFIX + r"push\b", prefix):
        gf = _normalized_flags(tokens, "git")
        if "--force" in gf and "--force-with-lease" not in gf:
            return "force-push"
        # Remote ref delete: recognised iff a single deletable ref is extractable (recognition == mint).
        if _extract_remote_ref_delete_target(prefix) is not None:
            return "remote-ref-delete"
        # Mass delete (--mirror/--prune/multi-ref): recognised iff a mass-target tuple is extractable.
        if _extract_mass_delete_target(prefix) is not None:
            return "remote-mass-delete"
        # Push to main via the refspec destination predicate on the positionals after the remote.
        _pm = re.search(_GIT_PREFIX + r"push\s(.*)$", prefix)         # well-formed push only
        if _pm is not None:                                          #   (excludes push--force glue)
            _tail = _pm.group(1).split()
            if sum(1 for t in _tail if t.startswith("-")) <= _MAX_GLOBAL_FLAG_TOKENS:  # perf bound
                # A quoted payload (ssh host "git push origin main") leaves its closing quote on
                # the last refspec; it is dropped so the destination reads as the shell runs it.
                _refspecs = [_drop_closing_quote(_strip_surrounding_quotes(r))
                             for r in _push_positionals(_tail)[1:]]
                # A leading '+' on any refspec is git's force spelling: force push.
                if any(r.startswith("+") and len(r) > 1 for r in _refspecs):
                    return "force-push"
                _parsed = _parse_push_args(_tail)
                if _push_is_dry_run(_parsed[2], _parsed[3]):
                    return None                                       # a dry run updates nothing (1.4.1 U13)
                for _r in _refspecs:                                  # REFSPECS only (skip remote)
                    if _PUSH_MAIN_DST_RE.fullmatch(_r) or _PUSH_OPEN_DST_RE.search(_r):
                        return "push-to-main"
    return None


def _stripped_surface_danger(stripped: str) -> bool:
    """The literal danger battery over an ALREADY-STRIPPED (or MASKED) surface: the
    DANGEROUS_PATTERNS scan + the four per-leg literal-arm families + the additive whole-surface
    normalized-flag condition + the PER-LEG op loop (_flag_condition_danger_op per leg, filtered
    to _PER_LEG_OPS, for any recognized op in a NON-first leg).
    """
    for pattern in DANGEROUS_PATTERNS:
        if pattern.search(stripped):
            return True
    # Literal arms matched PER-LEG over the SAME surface (identical provenance to the read
    # floor). Legs computed ONCE and shared by all four arm loops.
    legs = _slice_stripped_legs(stripped)
    for _leg in legs:
        if any(arm.search(_leg) for arm in _FORCE_PUSH_LITERAL_ARMS):
            return True
    for _leg in legs:
        if any(arm.search(_leg) for arm in _CLOSE_LITERAL_ARMS):
            return True
    for _leg in legs:
        if any(arm.search(_leg) for arm in _API_LITERAL_ARMS):
            return True
    for _leg in legs:
        if any(arm.search(_leg) for arm in _BRANCH_DELETE_LITERAL_ARMS):
            return True
    if _flag_condition_danger_op(stripped) is not None:
        return True
    # Per-leg arms: rerun the first-leg-anchored union arm on each leg.
    for _leg in legs:
        if _flag_condition_danger_op(_leg) in _PER_LEG_OPS:
            return True
    return False


def is_dangerous_command(command: str, _wrapped: bool = True) -> bool:
    """Check if a bash command is a dangerous git operation (`_wrapped`: unmintable_danger's)."""
    # Pre-strip detection: eval+heredoc shape obscures destructive ops via
    # the heredoc-strip pipeline. Treat as dangerous before the strip runs.
    if _has_eval_with_heredoc(command):
        return True

    # Normalize bash line continuations (\<newline>) via the shared P0 SSOT before
    # any matching (so this floor + the substrate join lines identically). A quoted-delimiter
    # heredoc body keeps its backslash-newlines when a cat reads it and joins them when a
    # shell does, and which one reads it is not always known here: both readings are judged.
    joined = _normalize_line_continuations(command)
    verbatim = _join_line_continuations(command)
    return (_joined_is_dangerous(joined, _wrapped)
            or verbatim != joined and _joined_is_dangerous(verbatim, _wrapped))


def _joined_is_dangerous(command: str, _wrapped: bool = True) -> bool:
    """is_dangerous_command past the eval-heredoc check, on a command already joined."""
    stripped = _strip_non_executable_content(command)
    # Blank inert --help/-h/gh help legs before the danger battery (offset-preserving).
    stripped = _neutralize_inert_help_legs(stripped)
    # FIX-MERGE f3: the GitHub REST PR close is decided by a body field the strip removes,
    # so it is checked on the RAW legs (the same helper and slicer its detect arm uses).
    if any(_gh_api_close_leg_pr(_leg) is not None for _leg in _slice_stripped_legs(command)):
        return True
    # the hardening pass: the gated-but-unmintable shapes (GraphQL mutations, off-line bodies, raw HTTP
    # MR/branch deletes and closes, shell -c wrapping), also read on the RAW legs.
    if (unmintable_danger(command) if _wrapped else unmintable_danger(command, 0, False)) is not None:
        return True
    # The literal danger battery is _stripped_surface_danger, shared with the inert-strip preserve.
    return (_stripped_surface_danger(stripped) or push_option_merge(command)
            or implicit_push_to_main(command))


# A plain `rm` head at a leg start: rm-specific, used only by the compound-leg count.
_RM_HEAD_RE = re.compile(r"\s*rm(?=\s|$)")


def _leg_is_destructive(leg: str) -> bool:
    """Count a compound leg as destructive if it is a recognized git/gh-destructive op
    (``is_dangerous_command``) OR its head command is a plain ``rm`` (the rm-exception).
    """
    return is_dangerous_command(leg) or _RM_HEAD_RE.match(leg) is not None


def _single_destructive_leg(command: str) -> str | None:
    """The UNIQUE is_dangerous_command leg of a command, or None."""
    dangerous = [
        leg.strip()
        for leg in _split_into_legs(command)
        if is_dangerous_command(leg.strip())
    ]
    return dangerous[0] if len(dangerous) == 1 else None


def _single_detectable_leg(command: str) -> str | None:
    """#1083 emergent-danger bind fallback: the UNIQUE leg that detect_command_operation_type
    classifies non-None, or None.
    """
    detectable = [
        leg.strip()
        for leg in _split_into_legs(command)
        if detect_command_operation_type(leg.strip()) is not None
    ]
    return detectable[0] if len(detectable) == 1 else None


def is_compound_destructive_command(command: str) -> bool:
    """Detect an agent chaining MULTIPLE destructive operations into one command."""
    # Legs come from the shared _split_into_legs; no operator means one leg.
    legs = _split_into_legs(command)
    # Two or more destructive legs (a git/gh destructive op or a plain rm head) refuse.
    return sum(1 for leg in legs if _leg_is_destructive(leg)) >= 2


# Carrier 10 (inert_legs) asks this classifier about a masked leg; registering the function
# there keeps the package's import direction downward instead of a call-time import.
_inert_legs._surface_danger = _stripped_surface_danger
