"""
Location: pact-plugin/hooks/shell/merge/strip.py
Summary: _strip_non_executable_content: carriers 1-9 that blank argv-inert text before the
         shape pass, and its substitution-preserving helpers.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: _strip_non_executable_content, _strip_heredoc_bodies, _has_eval_with_heredoc,
     _split_into_legs (heredoc reading: shell.merge.heredocs).
"""

from __future__ import annotations

import json
import os
import re
import sys

from shell.merge.arms import (AWK_PROGRAMS, DATA_PROGRAMS, _AWK_DATA_OPTIONS, _AWK_EXECUTES)
from shell.inline_code import code_option, runs_inline_code
from shell.merge.inert_legs import (_EXEC_WRAPPERS_COARSE, _EXEC_WRAPPERS_RECURSE, _SHELL_STRING_EXECUTORS, _preserve_tail)
from shell.merge.views import (_COMPOUND_OPS_RE, _FD_REDIRECT_RE, _SHELL_PROGRAMS, _leg_command_word, _leg_token_spans, _mask_shell_quotes, _read_long_once, _shell_tokenize, _strip_quoted_strings)
from shell.words import is_double_quoted, quoted_inner
from shell.merge.arms import (_GH_API_PREFIX, _MAX_GLOBAL_FLAG_TOKENS, _MSG_FLAG_ANCHOR, _VERB_MSG_BODY)
from shell.merge.inert_legs import (_strip_inert_positional_legs)
from shell.merge.views import (_executed_surface_view, _has_command_substitution, _has_eval_or_source, _has_pipe_to_shell, _has_process_substitution_to_shell, _keep_carrier_value, _preserve_substitution_spans, _procsub_anchor_view, _slice_stripped_legs, _strip_flag_values, _var_is_expanded)
from shell.merge.heredocs import (_HEREDOC_OP, _heredoc_close, _heredoc_delimiter, _heredocs,  # noqa: F401
                                  _expands, _join_line_continuations, _runs_body)
from shell.words import normalize_line_continuations as _normalize_line_continuations

def _strip_non_executable_content(command: str) -> str:
    """Strip shell content that is clearly non-executable before pattern matching."""
    result = command

    # Output routing flags: when output is piped or process-substituted into a shell, stripped
    # content would execute, so the carriers below skip stripping. Computed on the executed-surface
    # view so data inside quotes or heredoc bodies cannot switch them off. They only add detection.
    piped_to_shell = _has_pipe_to_shell(_executed_surface_view(command))
    process_sub_to_shell = _has_process_substitution_to_shell(_procsub_anchor_view(command))

    # 1. Heredoc bodies (kept when a shell reads the heredoc).
    if not piped_to_shell and not process_sub_to_shell:
        result = _strip_heredoc_bodies(result)

    # 2. Strip comments: # to end of line
    #    Only strip when # appears at start of line or after whitespace/semicolon
    #    (not inside words like issue#42 or URLs with #fragment).
    result = re.sub(r"(?:^|(?<=\s)|(?<=;))\#.*$", "", result, flags=re.MULTILINE)

    # 3. echo/printf quoted arguments (kept when output reaches a shell).
    if not piped_to_shell and not process_sub_to_shell:
        # echo/printf print their args and never run them: strip every quoted arg, keep $() spans.
        def _strip_echo_dq_arg(m: re.Match) -> str:
            # One double-quoted arg. Inert -> bareword STRIPPED; a value with $()/backtick is
            # span-scoped (inert literal stripped, executing spans preserved in the SAME dq
            # context as base so detection is identical); fail-safe preserves whole.
            if not _has_command_substitution(m.group(0)):
                return "STRIPPED"
            transformed = _preserve_substitution_spans(m.group(0))
            return m.group(0) if transformed is None else transformed

        def _strip_echo_span(span_match: re.Match) -> str:
            # Each quoted string as shell.words pairs it (an escaped quote opens nothing).
            span = span_match.group(0)
            masked = _mask_shell_quotes(span)
            if "$(" in masked or "`" in masked:
                return span                     # an unquoted substitution runs its text
            return _strip_quoted_strings(span, _strip_echo_dq_arg)

        # Span = verb + the shared quote-aware body that STOPS at the first UNQUOTED
        # ;/&&/|/newline, so an executing tail (`echo "x" && git branch -D y`) stays OUTSIDE the
        # span and is caught (leg-locality). Keep the `\s+` so a bare `echo` token never matches.
        result = re.sub(
            r"\b(echo|printf)\s+" + _VERB_MSG_BODY,
            _strip_echo_span,
            result,
        )

    # 4. Variable assignment values (kept when eval/source is present or the value is expanded).
    has_eval = _has_eval_or_source(command)
    if not has_eval:
        # Double-quoted: guard against command substitution and bare expansion
        def _strip_var_dq(match: re.Match) -> str:
            var_name = match.group(1)
            # ORDER LOAD-BEARING: a bare $VAR/${VAR} expansion word-splits and EXECUTES the
            # WHOLE value (the inert literal INCLUDED, not just a substitution), so an expanded
            # var preserves WHOLE — this MUST precede the substitution span-scope below.
            if _var_is_expanded(var_name, command):
                return match.group(0)  # Preserve — $VAR executes the whole value
            if _has_command_substitution(match.group(0)):
                # `git -c key=value` values can be executable config (core.pager, alias.*): keep them whole.
                if match.start() > 0 and match.string[match.start() - 1] == ".":
                    return match.group(0)  # config injection — status-quo preserve
                # Strip the inert literal but keep genuine $()/backtick spans; they execute.
                value = match.group(0)[len(var_name) + 1:]  # text after `NAME=`
                transformed = _preserve_substitution_spans(value)
                if transformed is None:
                    return match.group(0)  # FAIL-SAFE: preserve whole (exotic span)
                return var_name + "=" + transformed
            return var_name + "=STRIPPED"

        result = re.sub(
            r'\b([A-Za-z_][A-Za-z0-9_]*)="(?:[^"\\]|\\.)*"',
            _strip_var_dq,
            result,
        )

        # Single-quoted: guard against bare expansion
        def _strip_var_sq(match: re.Match) -> str:
            var_name = match.group(1)
            if _var_is_expanded(var_name, command):
                return match.group(0)  # Preserve — $VAR executes
            return var_name + "=STRIPPED"

        result = re.sub(
            r"\b([A-Za-z_][A-Za-z0-9_]*)='[^']*'",
            _strip_var_sq,
            result,
        )

    # 5. git commit message: span-bounded and anchored on -m/--message, via the shared quote-balanced
    # strip.
    if not piped_to_shell and not process_sub_to_shell:
        _git_commit_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}commit\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_commit_span,
            lambda mm: _strip_flag_values(
                mm.group(0),
                # --m* abbreviations are safe on git commit: its only --m* option is --message.
                r"((?:--m(?:e(?:s(?:s(?:a(?:g(?:e)?)?)?)?)?)?|--trailer|(?<!\S)-[a-ln-zA-Z]*m)\s*)",
                _keep_carrier_value,
            ),
            result,
        )

    # 6. Here-string arguments (kept when a shell reads the here-string).
    if not piped_to_shell and not process_sub_to_shell:
        def _strip_herestring_dq(match: re.Match) -> str:
            # Check what command precedes the <<< (shell-interpreter guard stays FIRST).
            start = match.start()
            preceding = command[:start].rstrip()
            if re.search(r"\b(?:bash|sh|zsh)\s*$", preceding):
                return match.group(0)  # Preserve — a shell reads the here-string as stdin
            if not _has_command_substitution(match.group(0)):
                return "<<<STRIPPED"                  # no-$() unchanged (bareword)
            # Span-scope the value: strip the inert literal, keep the executing spans.
            transformed = _preserve_substitution_spans(match.group(1))
            if transformed is None:
                return match.group(0)                 # fail-safe: preserve whole
            return "<<<" + transformed

        result = re.sub(
            r'<<<\s*("(?:[^"\\]|\\.)*")',
            _strip_herestring_dq,
            result,
        )

        def _strip_herestring_sq(match: re.Match) -> str:
            # Check what command precedes the <<<
            start = match.start()
            preceding = command[:start].rstrip()
            if re.search(r"\b(?:bash|sh|zsh)\s*$", preceding):
                return match.group(0)  # Preserve — content executes
            return "<<<STRIPPED"

        result = re.sub(
            r"<<<\s*'[^']*'",
            _strip_herestring_sq,
            result,
        )

    # 6b. find -name/-path values are match patterns, never executed.
    if not piped_to_shell and not process_sub_to_shell:
        _find_exec_primary_re = re.compile(r"(?<!\S)-(?:exec|execdir|ok|okdir)(?!\S)")
        _find_span = r"\bfind\s+" + _VERB_MSG_BODY

        def _strip_find_span(m: "re.Match") -> str:
            span = m.group(0)
            if _find_exec_primary_re.search(span):
                return span                   # -exec/-ok present => preserve whole span
            return _strip_flag_values(
                span,
                r"((?<!\S)-(?:name|path|iname|ipath|regex|iregex)\s+)",
                _keep_carrier_value,
            )

        result = re.sub(_find_span, _strip_find_span, result)

    # 7. gh issue/pr create/edit/comment --title/--body values: prose sent to the API, never run.
    if not piped_to_shell and not process_sub_to_shell:
        # Match the carrier span first, then strip every title/body value inside it.
        _gh_carrier_span = (
            r"gh\s+(?:issue\s+(?:create|edit|comment)|pr\s+(?:create|comment|edit))\b"
            + _VERB_MSG_BODY
        )

        def _strip_gh_carrier_span(span_match: re.Match) -> str:
            # Shared quote-balanced _strip_flag_values (dq kept, sq stripped, bare value token kept).
            return _strip_flag_values(
                span_match.group(0),
                r"((?:--title|--body|--assignee|--add-assignee|--remove-assignee|-t|-b|-a)\s+)",
                _keep_carrier_value,
            )

        result = re.sub(_gh_carrier_span, _strip_gh_carrier_span, result)

        # 7b. gh release create/edit --notes/--title prose (not --notes-file, not the booleans).
        _gh_release_span = (
            r"gh\s+release\s+(?:create|edit)\b"
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _gh_release_span,
            lambda m: _strip_flag_values(
                m.group(0), r"((?:--notes|-n|--title|-t)\s+)", _keep_carrier_value
            ),
            result,
        )

        # 7c. gh gist create/edit --desc prose (-d is a value only here, a boolean on release).
        _gh_gist_span = (
            r"gh\s+gist\s+(?:create|edit)\b"
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _gh_gist_span,
            lambda m: _strip_flag_values(
                m.group(0), r"((?:--desc|-d)\s+)", _keep_carrier_value
            ),
            result,
        )

        # 7d. git tag -m/--message: span-bounded and flag-anchored, so the destructive `git tag -d`
        # sharing the verb is never stripped.
        _git_tag_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}tag\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_tag_span,
            lambda mm: _strip_flag_values(
                mm.group(0),
                # Long arm bounded to --mes...--message: git tag also has --merged/--no-merged.
                r"((?:--mes(?:s(?:a(?:g(?:e)?)?)?)?|-[a-ln-zA-Z]*m)\s*)",
                _keep_carrier_value,
            ),
            result,
        )

        # 7e. git merge, git stash push/store/save and git notes add/append messages, same machinery.
        _git_merge_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}?merge\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_merge_span,
            lambda mm: _strip_flag_values(
                mm.group(0), _MSG_FLAG_ANCHOR, _keep_carrier_value
            ),
            result,
        )
        # git stash push / store — both take -m/--message.
        _git_stash_flag_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}stash\s+(?:push|store)\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_stash_flag_span,
            lambda mm: _strip_flag_values(
                mm.group(0), _MSG_FLAG_ANCHOR, _keep_carrier_value
            ),
            result,
        )
        # git stash save <message>: the message is positional; the other args are boolean flags.
        _git_stash_save_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}stash\s+save\b" % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_stash_save_span,
            lambda mm: _strip_flag_values(
                mm.group(0), r"(save(?:\s+-[-\w]+)*\s+)", _keep_carrier_value
            ),
            result,
        )
        # git notes add / append (-m/--message; an optional `--ref <ref>` global may
        # precede the verb).
        _git_notes_span = (
            r"\bgit\s+(?:[^;&|\n\s]+\s+){0,%d}notes\s+(?:--ref(?:=|\s+)\S+\s+)?(?:add|append)\b"
            % _MAX_GLOBAL_FLAG_TOKENS
            + _VERB_MSG_BODY
        )
        result = re.sub(
            _git_notes_span,
            lambda mm: _strip_flag_values(
                mm.group(0), _MSG_FLAG_ANCHOR, _keep_carrier_value
            ),
            result,
        )

    # 8. HTTP-client request-body values (curl, wget, gh api): a destructive path inside a body is
    # data, not the target.
    if not piped_to_shell and not process_sub_to_shell:
        # HTTP-client span up to the first unquoted separator; quote-aware and linear.
        _http_client_span = (
            r"(?:\bcurl\b|\bwget\b|" + _GH_API_PREFIX + r")"
            + _VERB_MSG_BODY
        )
        # Direct-value body flags (form a).
        _data_flag = r"(?:--data(?:-(?:raw|binary|ascii|urlencode))?|--body-data|--post-data|-d)"
        # KEY=VALUE field flags (form b). The `<key>=` requirement is what makes the
        # strip surface-aware (curl's boolean -f never has a key= after it).
        _field_flag = r"(?:--field|--raw-field|-F|-f)"

        def _strip_http_body_span(span_match: re.Match) -> str:
            span = span_match.group(0)

            # Contents API exception: its target branch is in the body, so the span is kept whole.
            if re.search(r"contents/", span, re.IGNORECASE):
                return span

            def _keep_flag_dq(m: re.Match) -> str:
                # group(1) = the flag (+ key= for form b) up to the opening quote.
                if not _has_command_substitution(m.group(0)):
                    return m.group(1) + "'STRIPPED'"  # no-$() unchanged (single-quoted)
                # Span-scope the value after the flag: strip the inert literal, keep the executing spans.
                flag = m.group(1)
                transformed = _preserve_substitution_spans(m.group(0)[len(flag):])
                if transformed is None:
                    return m.group(0)                 # fail-safe: preserve whole
                return flag + transformed

            # Body flag values through the shared quote-safe strip.
            span = _strip_flag_values(span, r"(" + _data_flag + r"\s+)", _keep_flag_dq)
            span = _strip_flag_values(
                span, r"(" + _field_flag + r"\s+[\w.\-]+=)", _keep_flag_dq
            )
            return span

        result = re.sub(_http_client_span, _strip_http_body_span, result)

    # 9. gh api output-selector and other non-target flag values (--jq, --template, ...).
    if not piped_to_shell and not process_sub_to_shell:
        # gh api span up to the first unquoted separator; quote-aware and linear.
        _gh_api_selector_span = (
            r"(?:" + _GH_API_PREFIX + r")"
            + _VERB_MSG_BODY
        )
        # Form-aware separator: space, =long, =short and attached-short values.
        _selector_flagsep = (
            r"(?<!\S)(?:"
            r"(?:--jq|--template|--header|--input|--hostname|--preview)(?:\s+|=)"
            r"|(?:-q|-t|-H|-p)(?:\s+|=)?"
            r")"
        )

        def _keep_selector_dq(m: re.Match) -> str:
            # group(1) = the flag(+separator) captured by the wrapped _selector_flagsep.
            # Same shape as carrier 8's _keep_flag_dq; both are keep_fn to _strip_flag_values.
            if not _has_command_substitution(m.group(0)):
                return m.group(1) + "'STRIPPED'"      # no-$() unchanged (single-quoted)
            # Span-scope the value after the flag: strip the inert literal, keep the executing spans.
            flag = m.group(1)
            transformed = _preserve_substitution_spans(m.group(0)[len(flag):])
            if transformed is None:
                return m.group(0)                     # fail-safe: preserve whole
            return flag + transformed

        def _strip_gh_api_selectors(span_match: re.Match) -> str:
            span = span_match.group(0)
            # Contents API exception, as in carrier 8.
            if re.search(r"contents/", span, re.IGNORECASE):
                return span
            # Selector flag values through the shared quote-safe strip.
            span = _strip_flag_values(
                span, r"(" + _selector_flagsep + r")", _keep_selector_dq
            )
            return span

        result = re.sub(_gh_api_selector_span, _strip_gh_api_selectors, result)

    # 11-12. Data-program arguments and JSON literals (refactor §3.7): per leg, before the
    # general strip, so an awk program or a JSON argument to an interpreter is masked too.
    if not piped_to_shell and not process_sub_to_shell:
        result = _map_legs(result, _strip_data_leg)

    # 10. General inert positional strip: legs whose head is not an executor, wrapper, http client
    # or git/gh tool have their quoted positionals stripped.
    if not piped_to_shell and not process_sub_to_shell:
        result = _strip_inert_positional_legs(result)

    return result


# Carrier 1: `<<` and `<<-`, never the here-string `<<<`.
def _strip_heredoc_bodies(command: str) -> str:
    """Carrier 1: each heredoc body and its closing delimiter become `<<HEREDOC_STRIPPED`;
    the rest of the operator line (`&& next`) stays, since it runs. Only a real operator
    counts (_scan: not one in a comment, a quote or `$((1<<2))`), and a body the command
    runs as a script (_runs_body) is kept."""
    out, pos = [], 0
    for match, parsed, line_end, close, _bare in _heredocs(command):
        if close is None or _runs_body(command, match.start(), line_end):
            continue
        out.append(command[pos:match.start()] + "<<HEREDOC_STRIPPED" + command[parsed[1]:line_end])
        pos = close
    out.append(command[pos:])
    return "".join(out)


def _has_eval_with_heredoc(command: str) -> bool:
    """Detect eval (or backtick) command-substitution that wraps a heredoc."""
    # eval $(...) / eval "$(...)" with a heredoc anywhere within the substitution
    if re.search(r"\beval\s+[\"']?\$\(", command) and "<<" in command:
        return True
    # eval `...` (backtick) wrapping a heredoc
    if re.search(r"\beval\s+`[^`]*<<", command):
        return True
    return False


def _tracing() -> bool:
    """True under PACT_MASK_TRACE=1: every strip then writes its own gate_mask rows."""
    return os.environ.get("PACT_MASK_TRACE") == "1"


# The classifier, the leg split and the binding each strip the same text.
_strip_non_executable_content = _read_long_once(_strip_non_executable_content, _tracing)


def _split_into_legs(command: str) -> list[str]:
    """Split a command into its shell-operator-separated legs (always >=1 leg)."""
    normalized = _normalize_line_continuations(command)
    stripped = _strip_non_executable_content(normalized)
    return _slice_stripped_legs(stripped)


# ── carriers 11-12 ───────────────────────────────────────────────────────────────────────

# Carrier 12 leaves a shell, an executor wrapper, an HTTP client (carrier 8 reads its body: a
# Contents API PUT names its target branch there) and the git/gh/glab tools (carriers 5-9) alone.
_NOT_DATA_HEADS = _SHELL_PROGRAMS | {"eval", "source", ".", "xargs", "exec", "command"} \
    | {"curl", "wget", "git", "gh", "glab"} | _EXEC_WRAPPERS_COARSE | _EXEC_WRAPPERS_RECURSE


def _trace(carrier: int, rule: str, span: str) -> None:
    """Attribution: under PACT_MASK_TRACE=1 each masked span is written to stderr as a
    gate_mask row (carrier, rule, the span's length), never its text."""
    if _tracing():
        chars = len(span)
        print(f"gate_mask carrier={carrier} rule={rule} chars={chars}", file=sys.stderr)


def _map_legs(result: str, fn) -> str:
    """`fn` over each shell-operator leg of `result` (the quote-masked, FD-neutralised view
    finds the operators; the original separators are kept verbatim)."""
    view = _FD_REDIRECT_RE.sub(lambda m: " " * len(m.group()), _mask_shell_quotes(result))
    out, last = [], 0
    for m in _COMPOUND_OPS_RE.finditer(view):
        out.append(fn(result[last:m.start()]))
        out.append(result[m.start():m.end()])
        last = m.end()
    out.append(fn(result[last:]))
    return "".join(out)


def _json_literal(text: str) -> bool:
    """True when `text` is a JSON object or array: data to every program but a shell."""
    text = text.strip()
    if not text or text[0] not in "[{":
        return False
    try:
        return isinstance(json.loads(text), (dict, list))
    except ValueError:
        return False


def _awk_is_data(leg: str, tokens: list[str]) -> bool:
    """An allowlist, so it fails closed: an awk leg is data only when every option word is
    -F.., -v.., `-` or `--`, its text computes nothing ($, a backtick, outside single quotes)
    and the program cannot run a command (_AWK_EXECUTES). A program file (-f, --fil, -bf),
    a loaded library or a computed program keeps the leg."""
    options_ok = all(not word.startswith("-") or word in ("-", "--") or word[:2] in _AWK_DATA_OPTIONS
                     for word in tokens[1:])
    return options_ok and not _expands(leg) and not _AWK_EXECUTES.search(leg)


def _strip_data_leg(leg: str) -> str:
    """Carrier 11: every wholly quoted token after the head of a DATA_PROGRAMS leg is
    STRIPPED (an awk leg only when _awk_is_data). Carrier 12: a wholly quoted token that is
    a JSON literal is STRIPPED in any leg whose head is not a shell, an executor wrapper or
    an interpreter given inline code, and that is not the value of a code option. Fail
    closed: a leg not known to be data is left whole, as carrier 10 leaves it. A
    double-quoted value holding a substitution is kept: the shell runs it."""
    cmd_leg = _leg_command_word(leg)
    tokens = _shell_tokenize(cmd_leg) if cmd_leg else None
    if not tokens:
        return leg
    head = os.path.basename(tokens[0])
    # Whatever carrier 10 keeps whole (a process substitution, a command substitution, an
    # expanded quoted assignment, `gh api`, bare-token danger) is kept here too: these
    # carriers run first. So is an interpreter with inline code, and an awk not known as data.
    if _preserve_tail(leg) or runs_inline_code(tokens) \
            or head in AWK_PROGRAMS and not _awk_is_data(leg, tokens):
        return leg
    data_head = head in DATA_PROGRAMS
    spans = _leg_token_spans(leg)
    if not spans:
        return leg
    out = leg
    first = spans[0][0] + (len(leg) - len(leg.lstrip()))
    for i in range(len(spans) - 1, 0, -1):
        (start, end), (p_start, p_end) = spans[i], spans[i - 1]
        span, previous = leg[start:end], leg[p_start:p_end]
        inner = quoted_inner(span)
        if inner is None:
            continue
        if is_double_quoted(span) and ("$(" in inner or "`" in inner):
            continue
        # The words after a shell or interpreter word may run.
        word = os.path.basename(previous)
        if word != head and _SHELL_STRING_EXECUTORS.match(word):
            continue
        if data_head and start > first:
            _trace(11, "mg.inert.data_program", span)
            out = out[:start] + "STRIPPED" + out[end:]
        elif head not in _NOT_DATA_HEADS and not code_option(previous) and _json_literal(inner):
            _trace(12, "mg.inert.json_literal", span)
            out = out[:start] + "STRIPPED" + out[end:]
    return out

