"""
Location: pact-plugin/hooks/shell/merge/views.py
Summary: Pure views of a command: executed surface, procsub anchors, pipes to a shell,
         canonical spelling, the shell tokeniser wrapper, leg slicing.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: _read_long_once, _strip_surrounding_quotes, _leg_program_tokens, _COMPOUND_OPS_RE, _FD_REDIRECT_RE, _has_pipe_to_shell, _PROCSUB_SHELL, _has_process_substitution_to_shell, _HEREDOC_BODY_RE, _excise_heredoc_bodies_for_routing_scan, _delimiter_is_unescaped, _excise_comments_view, _excise_and_mask, _executed_surface_view, _procsub_anchor_view, _has_eval_or_source, _var_is_expanded, _has_command_substitution, _extract_dollar_paren, _extract_backtick, _preserve_substitution_spans, _keep_carrier_value, _strip_inert_dq_value, _strip_quoted_strings, _strip_flag_values, _shell_tokenize, _INERT_WORD_RE, _ASSIGNMENT_SHAPE_RE, _RESERVED_SHELL_WORDS, _SHELL_PROGRAMS, _SHELL_C_FLAG_RE, _CANONICAL_OPERATORS, _RESPELL_TRIGGERS, _single_quoted, canonical_spelling, _BENIGN_TERMINATOR_RE, _PROCSUB_MARKER_RE, _executable_prefix, _leg_command_word, _slice_stripped_legs, _slice_stripped_leg_spans.
"""

from __future__ import annotations

import os
import re

from shell.merge.arms import (_UNMINTABLE_RUNNERS, _VALUE_TOKEN)
from shell.merge.heredocs import (_SHELL_PROGRAMS, _verbatim_spans)
from shell.words import (MAX_NESTING, PREFIX_WORDS, SHELL_KEYWORDS,
                         leg_token_spans as _leg_token_spans,
                         is_double_quoted, mask_quotes as _mask_shell_quotes, quote_spans,
                         substitution_spans, token_spans, words)

_LONG_TEXT = 1 << 11


def _fresh(found):
    """A kept result as handed out: a list, also one inside a tuple, is copied, so no
    caller's edit reaches the next reader."""
    if isinstance(found, tuple):
        return tuple(list(part) if isinstance(part, list) else part for part in found)
    return list(found) if isinstance(found, list) else found


def _read_long_once(read, unless=None):
    """`read` with its result kept for a text of _LONG_TEXT (2 KB) and over (8 kept): the
    carriers, the classifier and the binding read the same long legs many times, and a 1 MB
    command must stay inside the hook's budget. An everyday command is read as before.
    Only for a reader whose result depends on its arguments alone (no checkout, directory,
    environment or clock); `unless()` true reads afresh, for a reader that also reports."""
    kept: dict = {}

    def reader(text, *rest):
        if len(text) < _LONG_TEXT or (unless is not None and unless()):
            return read(text, *rest)
        key = (text, *rest)
        if key not in kept:
            if len(kept) >= 8:
                kept.clear()
            kept[key] = read(text, *rest)
        return _fresh(kept[key])
    reader.__name__, reader.__doc__, reader.__wrapped__ = read.__name__, read.__doc__, read
    return reader


_leg_token_spans, _mask_shell_quotes, words = map(_read_long_once, (_leg_token_spans, _mask_shell_quotes, words))


def _strip_surrounding_quotes(token: str) -> str:
    """Strip one layer of matching surrounding quotes from a captured CLI token."""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def _leg_program_tokens(leg: str) -> "list[str] | None":
    """The leg's tokens from its true program word on (assignments, redirects and runner words
    such as env/sudo/rtk/nice -n N dropped), or None when it does not tokenize."""
    head = _leg_command_word(leg)
    tokens = _shell_tokenize(head) if head else None
    if not tokens:
        return None
    i = 0
    while i < len(tokens):
        word = os.path.basename(tokens[i])
        if word in _UNMINTABLE_RUNNERS:
            i += 1
            while i < len(tokens) and (tokens[i].startswith("-") or tokens[i][:1].isdigit()
                                       or re.match(r"^[A-Za-z_]\w*=", tokens[i])):
                i += 2 if tokens[i] in ("-n", "-u", "-g", "-s", "-k") else 1
            continue
        if re.match(r"^[A-Za-z_]\w*=", tokens[i]):
            i += 1
            continue
        break
    return tokens[i:] or None


# Compound-operator and fd-redirect regexes, shared by the read and mint sides so both split on
# the same separators. Scanned on the quote-masked view; fd redirects are neutralised first.
_COMPOUND_OPS_RE = re.compile(r"&&|\|\||\|&|;|&|\||\n")
# `\d*[<>]&` neutralises every fd-dup, fd-close and >& redirect so the bare-& arm cannot misfire.
_FD_REDIRECT_RE = re.compile(r"\d*[<>]&|>\||&>>?")


def _has_pipe_to_shell(command: str) -> bool:
    """Check if command pipes output to a shell interpreter."""
    return bool(
        re.search(r"\|\s*(?:bash|sh|zsh)\b", command)
        or re.search(r"\|\s*xargs\s+(?:.*\s+)?(?:bash|sh|zsh)\b", command)
    )


# Path-qualified shell name, matched as a whole path leaf (not `basht`, not `bash/foo`).
_PROCSUB_SHELL = r"(?:[^\s)/]*/)*(?:bash|sh|zsh)(?![\w/])"


def _has_process_substitution_to_shell(command: str) -> bool:
    """Check if a command uses process substitution fed to a shell interpreter."""
    return bool(
        re.search(r"\b(?:bash|sh|zsh)\s+<\(", command)                       # input-side (unchanged)
        # Arm A — redirect TARGET, stdout-routing operators only (stderr excluded by construction):
        or re.search(r"(?:&>>?|>&(?![0-9])|>\||1>>?|(?<![0-9])>>?)\s*>\(\s*" + _PROCSUB_SHELL, command)
        # Arm B — procsub as a command ARGUMENT (tee-fanout & general): preceded by a NON-redirect token:
        or re.search(r"[\w\"')\]}]\s+>\(\s*" + _PROCSUB_SHELL, command)
    )


# Heredoc body excision for the routing view: the opener line and closing marker stay visible.
_HEREDOC_BODY_RE = re.compile(
    r"(<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n)"   # 1: operator+marker+opener TAIL (kept)
    r"(?:.*?\n)?"                            #    body (excised)
    r"(\t*\2(?![\w]))",                      # 3: closing marker (kept)
    re.DOTALL,
)


def _excise_heredoc_bodies_for_routing_scan(command: str) -> str:
    if "<<" not in command:          # cheap short-circuit (perf: common case)
        return command

    def _repl(m: "re.Match") -> str:
        preceding = command[: m.start()].rstrip()
        if re.search(r"\b(?:bash|sh|zsh)\s*$", preceding):
            return m.group(0)        # shell-fed heredoc: body executes — keep
        return m.group(1) + "HEREDOC_BODY_EXCISED\n" + m.group(3)

    return _HEREDOC_BODY_RE.sub(_repl, command)


def _delimiter_is_unescaped(excised: str, d: int) -> bool:
    """True iff the char at excised[d] is an UNescaped shell word-delimiter."""
    b = d - 1
    bs = 0
    while b >= 0 and excised[b] == "\\":
        bs += 1
        b -= 1
    return bs % 2 == 0


def _excise_comments_view(excised: str, view: str) -> str:
    """Step 3 of _excise_and_mask: view-only, SAME-LENGTH comment excision."""
    if "#" not in view:                       # cheap short-circuit
        return view
    out = list(view)
    i, n = 0, len(view)
    while i < n:
        if view[i] == "#" and (
            i == 0
            or (
                excised[i - 1] in " \t\n;&|"
                and _delimiter_is_unescaped(excised, i - 1)
            )
        ):
            j = view.find("\n", i)
            if j == -1:
                j = n
            for k in range(i, j):
                out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def _excise_and_mask(command: str) -> tuple[str, str]:
    """Shared prefix for the two routing-flag views (#1129 R3): excise heredoc bodies (opener line
    + closing marker kept; shell-fed bodies preserved), then space-mask every balanced quoted
    span via _mask_shell_quotes, then excise unquoted comments from the VIEW ONLY (a
    `#`-comment's `| sh` must not flip piped_to_shell and disable every carrier — the comment
    text never executes).
    """
    excised = _excise_heredoc_bodies_for_routing_scan(command)
    view = _mask_shell_quotes(excised)
    view = _excise_comments_view(excised, view)
    return excised, view


def _executed_surface_view(command: str) -> str:
    """Routing-flag scan surface (#1129 R3): the command with non-executed data removed — heredoc
    BODIES excised (opener line + closing marker kept; shell-fed bodies preserved), then every
    balanced quoted span masked to spaces via _mask_shell_quotes (defined later in this module;
    resolved at call time).
    """
    return _excise_and_mask(command)[1]


def _procsub_anchor_view(command: str) -> str:
    """OUTPUT-SIDE process-substitution routing view (#1129 R3-fix)."""
    excised, view = _excise_and_mask(command)        # excise + space-mask; offsets aligned
    if ">(" not in view:                             # cheap short-circuit (common case)
        return view
    out = list(view)
    for m in re.finditer(r">\(", view):              # each SURVIVING >( on the view
        i = m.start()
        k = i - 1
        # skip a genuine UNMASKED bash blank (space OR tab): view==excised at an unmasked position
        while k >= 0 and view[k] == excised[k] and excised[k] in " \t":
            k -= 1
        # A closing quote in raw text ends a quoted writer token here: reveal it so the anchor matches.
        if k >= 0 and view[k] == " " and excised[k] in "\"'":
            out[k] = excised[k]
    return "".join(out)


def _has_eval_or_source(command: str) -> bool:
    """Check if command contains eval or source that could execute variable values."""
    return bool(re.search(r"\b(?:eval|source)\b", command))


def _var_is_expanded(var_name: str, command: str) -> bool:
    """Check if a variable is expanded (used) elsewhere in the command."""
    # Match $VAR (word boundary) or ${VAR}
    return bool(re.search(r"\$\{?" + re.escape(var_name) + r"\b", command))


def _has_command_substitution(quoted_content: str) -> bool:
    """Check if double-quoted content contains command substitution."""
    return "$(" in quoted_content or "`" in quoted_content


# Span-scoped substitution preserve: keep the executing $()/backtick spans, strip the inert rest.
def _extract_dollar_paren(c, i):
    """Return the balanced ``$(...)`` span starting at c[i] (c[i]=='$', c[i+1]=='(')
    and the index just past it, via a quote-aware + escape-aware depth counter: a ``)``
    inside a NESTED ``$(...)`` or inside a quoted span does NOT close the outer span.
    Returns (None, len(c)) when the span is unterminated."""
    n = len(c); depth = 0; j = i
    while j < n:
        ch = c[j]
        if ch == "\\": j += 2; continue
        if ch in "\"'":
            q = ch; j += 1
            while j < n:
                if c[j] == "\\" and q == '"': j += 2; continue
                if c[j] == q: j += 1; break
                j += 1
            continue
        if ch == "(": depth += 1; j += 1; continue
        if ch == ")":
            depth -= 1; j += 1
            if depth == 0: return c[i:j], j
            continue
        j += 1
    return None, n   # unterminated


def _extract_backtick(c, i):
    """Return the balanced backtick span starting at c[i] (c[i]=='`') and the index
    just past it. Returns (None, len(c)) when the span is unterminated."""
    n = len(c); j = i + 1
    while j < n:
        if c[j] == "\\": j += 2; continue
        if c[j] == "`": return c[i:j+1], j+1
        j += 1
    return None, n


def _preserve_substitution_spans(value):
    """Replace literal (inert) text with 'STRIPPED'; preserve $(...)/`...` spans VERBATIM."""
    def _scan(c, dq_inner):
        out = []; lit = False; i = 0; n = len(c)
        def flush():
            nonlocal lit
            if lit: out.append("STRIPPED"); lit = False
        while i < n:
            ch = c[i]
            if ch == "\\": lit = True; i += 2 if i+1 < n else 1; continue
            if ch == "$" and i+1 < n and c[i+1] == "(":
                span, j = _extract_dollar_paren(c, i)
                if span is None or '"' in span or "'" in span: return None
                flush(); out.append(span); i = j; continue
            if ch == "`":
                span, j = _extract_backtick(c, i)
                if span is None or '"' in span or "'" in span: return None
                flush(); out.append(span); i = j; continue
            if ch == '"': return None              # structural dq terminator -> fail-safe (malformed in dq-inner)
            if ch == "'" and not dq_inner: return None   # unquoted: ' is structural -> fail-safe
            lit = True; i += 1                     # dq-inner ' falls through -> LITERAL
        flush()
        return "".join(out)
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        inner = _scan(value[1:-1], True)
        return None if inner is None else '"' + inner + '"'
    return _scan(value, False)                     # unquoted VALUE-TOKEN


def _keep_carrier_value(m: "re.Match") -> str:
    """Shared value replacer for the gh/git prose-carrier strips (#1129 R2): issue/pr
    create|edit|comment, release create|edit, gist create|edit, git commit/tag -m, and (new)
    merge/stash/notes.
    """
    if not _has_command_substitution(m.group(0)):
        return m.group(1) + "STRIPPED"          # inert prose -> strip (unchanged)
    flag = m.group(1); value = m.group(0)[len(flag):]
    transformed = _preserve_substitution_spans(value)
    if transformed is None:
        return m.group(0)                       # FAIL-SAFE: preserve whole value
    return flag + transformed


def _strip_inert_dq_value(m: "re.Match") -> str:
    """Replace an inert double-quoted positional value with the bareword ``STRIPPED``; span-scope a
    value that contains ``$()``/backtick (executing spans preserved VERBATIM in the SAME dq
    context, surrounding inert literal stripped); FAIL-SAFE preserve-whole on a span the scanner
    cannot resolve.
    """
    if not _has_command_substitution(m.group(0)):
        return "STRIPPED"
    transformed = _preserve_substitution_spans(m.group(0))
    return m.group(0) if transformed is None else transformed


def _strip_quoted_strings(text: str, dq_value) -> str:
    """`text` with each quoted string replaced: a double-quoted one by dq_value(its match), any
    other by the bareword STRIPPED. shell.words pairs the quotes, so an escaped quote or the
    quote inside a $'..' string is never left over to pair with one past a separator."""
    out, last = [], 0
    for start, end in quote_spans(text):
        value = text[start:end]
        out += [text[last:start],
                dq_value(re.fullmatch(r"(?s).*", value)) if is_double_quoted(value) else "STRIPPED"]
        last = end
    return "".join(out) + text[last:]


def _strip_flag_values(span: str, flag_sep_regex: str, keep_fn) -> str:
    """Quote-safe value strip for a flag family within a single command span (#1118 re-model)."""
    span = re.sub(flag_sep_regex + r'"(?:[^"\\]|\\.)*"', keep_fn, span)     # 1
    span = re.sub(flag_sep_regex + r"'[^']*'", r"\1'STRIPPED'", span)       # 2
    span = re.sub(flag_sep_regex + _VALUE_TOKEN, keep_fn, span)             # 3
    return span


def _shell_tokenize(command: str) -> list[str] | None:
    """P1: quote-aware shell-word tokenizer, the reader every guard shares (shell_words.words:
    shlex.split's rules, posix, no comments, plus the shell's backslash-newline, `$'..'` and
    double-quote escapes) — strips single/double quotes, processes escapes, keeps a quoted-value
    span as ONE token.
    """
    return words(command)                                # shell.words: the reader every guard shares


# Canonical spelling: arms read words from raw text, so shell-decoded spellings ("pu"'sh',
# $'\x70ush', pu\sh) are spelled canonically first.
_INERT_WORD_RE = re.compile(r"[A-Za-z0-9_./:@%+,=-]+")
_ASSIGNMENT_SHAPE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_RESERVED_SHELL_WORDS = frozenset({
    "if", "then", "else", "elif", "fi", "do", "done", "case", "esac", "while", "until",
    "for", "in", "function", "select", "time", "coproc", "repeat", "foreach", "end",
})
_SHELL_C_FLAG_RE = re.compile(r"-[A-Za-z]*c[A-Za-z]*")
_CANONICAL_OPERATORS = ";&|()<>"
_RESPELL_TRIGGERS = ("'", '"', "\\")


def _single_quoted(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


# A case-blind disk (macOS, Windows) runs GIT, Gh and /usr/bin/GIT as git and gh: the program
# and runner words are read in lower case, wherever a word ends on one. Shell keywords are
# left as typed (IF is a program name to the shell, not `if`), and so is a variable ($GIT).
# ASCII letters only: no disk folds a look-alike such as a dotted capital I onto git.
_PROGRAM_CASE_RE = re.compile(r"(?<![\w.$-])(?<!\$\{)(?:git|gh|glab|%s)(?![\w./:@%%+,=-])"
                              % "|".join(sorted(PREFIX_WORDS - set(SHELL_KEYWORDS))), re.I | re.A)


def canonical_spelling(command: str, _depth: int = 0) -> str:
    """`command` with each word the shell decodes to plain inert text spelled plainly (see
    CANONICAL SPELLING above), recursing into $(..), backticks and the payload of `sh -c` /
    `eval`. Program and runner words are lower-cased first (_PROGRAM_CASE_RE).
    """
    command = _PROGRAM_CASE_RE.sub(lambda word: word.group().lower(), command)
    if not any(trigger in command for trigger in _RESPELL_TRIGGERS):
        return command                                   # nothing quoted or escaped
    if _depth > MAX_NESTING:
        return command
    try:
        subs = substitution_spans(command)
    except (ValueError, IndexError, RecursionError):
        return command
    # Substitution bodies first (right to left, so earlier offsets stay valid). A backtick
    # body is spliced back as $(..): the reader already removed its one level of escaping.
    for start, end, body in reversed(subs):
        new_body = canonical_spelling(body, _depth + 1)
        if new_body != body:
            command = command[:start] + "$(" + new_body + ")" + command[end:]
    try:
        subs = substitution_spans(command)
    except (ValueError, IndexError, RecursionError):
        return command
    masked = list(command)
    for start, end, _ in subs:
        masked[start:end] = "_" * (end - start)
    found = token_spans("".join(masked), _CANONICAL_OPERATORS)
    if found is None:
        return command

    def inside_substitution(start: int, end: int) -> bool:
        return any(s_start < end and start < s_end for s_start, s_end, _ in subs)

    def is_operator(index: int) -> bool:
        start, end, value = found[index]
        return all(char in _CANONICAL_OPERATORS for char in value) and value == command[start:end]

    # In the body of a quoted-delimiter heredoc and in a comment a backslash-newline is kept
    # (is_dangerous_command judges the joined reading as well).
    verbatim = _verbatim_spans(command)

    edits: list[tuple[int, int, str]] = []
    for index, (start, end, value) in enumerate(found):
        typed = command[start:end]
        if typed == value or is_operator(index) or inside_substitution(start, end):
            continue
        if "\\\n" in typed and any(b_start < end and start < b_end for b_start, b_end in verbatim):
            continue
        previous = found[index - 1][2] if index else ""
        if index and is_operator(index - 1) and previous.startswith("<<") and previous != "<<<":
            continue                                     # a heredoc delimiter: quoting matters
        payload = (
            index >= 2 and not is_operator(index - 1) and not is_operator(index - 2)
            and _SHELL_C_FLAG_RE.fullmatch(found[index - 1][2]) is not None
            and found[index - 2][2].rsplit("/", 1)[-1] in _SHELL_PROGRAMS
        ) or any(
            found[k][2] == "eval" and not any(is_operator(m) for m in range(k + 1, index))
            for k in range(max(0, index - 8), index)
        )
        if payload and not any(char in value for char in "$`\\"):
            respelled = canonical_spelling(value, _depth + 1)
            if respelled != value:                       # a script: respell inside it
                edits.append((start, end, _single_quoted(respelled)))
                continue
        if (
            value
            and _INERT_WORD_RE.fullmatch(value)
            and not value.startswith("=")
            and value not in _RESERVED_SHELL_WORDS
            and not (value.isdigit() and command[end:end + 1] in ("<", ">"))  # not an fd
            and not (_ASSIGNMENT_SHAPE_RE.match(value)
                     and not typed.startswith(_ASSIGNMENT_SHAPE_RE.match(value).group(0)))
        ):
            edits.append((start, end, value))
    pieces, done = [], 0
    for start, end, value in edits:                      # in order; one join, not one copy each
        pieces += [command[done:start], value]
        done = end
    return "".join(pieces) + command[done:]


canonical_spelling = _read_long_once(canonical_spelling)    # the hooks and the mint each ask


# Benign terminator for positional extractors: a compound operator or a redirect ends the target.
_BENIGN_TERMINATOR_RE = re.compile(r"&&|\|\||\|&|;|&|\||\n|(?<!\S)\d+[<>]|[<>]")

# An unquoted process substitution is never an honest force-push/branch-delete form.
_PROCSUB_MARKER_RE = re.compile(r"[<>] ?\(")


def _executable_prefix(command: str) -> str | None:
    """The command truncated at the first UNQUOTED compound-op-or-redirect."""
    if _shell_tokenize(command) is None:  # unbalanced/unterminated quote -> abstain
        return None
    masked = _mask_shell_quotes(command)  # same-length; quoted metachars -> spaces
    # Abstain on an unquoted >( or <(: the target is ambiguous.
    if _PROCSUB_MARKER_RE.search(masked):
        return None
    terminator = _BENIGN_TERMINATOR_RE.search(masked)
    return command[: terminator.start()] if terminator else command


def _leg_command_word(leg: str) -> "str | None":
    """Return the leg SUBSTRING starting at the TRUE command word — skipping any LEADING
    env-assignment (`NAME=value`, quoted OR bare: `LC_ALL=C`, `FOO="a b"`) and redirection (`>`,
    `2>`, `&>`, `2>/dev/null`, `2>&1`, `< in`…) tokens that PRECEDE the command.
    """
    spans = _leg_token_spans(leg)
    k, n = 0, len(spans)
    while k < n:
        start, end = spans[k]
        tok = leg[start:end]
        # NAME=value / NAME+=value / NAME= assignments are skipped whole.
        if re.match(r"^[A-Za-z_]\w*\+?=", tok):
            k += 1
            continue
        if re.match(r"^(?:[0-9]*(?:&?[<>]|<>)|&>>?)", tok):   # redirection operator
            k += 1
            # A bare redirect operator consumes the next token; self-contained forms do not.
            if re.fullmatch(r"[0-9]*(?:[<>]{1,2}|[<>]&|>\|)|&>>?", tok):
                k += 1
            continue
        return leg[start:]                               # the true command word
    return None                                          # only assignments/redirects → no command


def _slice_stripped_legs(stripped: str) -> list[str]:
    """Slice an ALREADY-STRIPPED command into its shell-operator-separated legs (always >=1 leg)."""
    return [stripped[s:e] for s, e in _slice_stripped_leg_spans(stripped)]


def _slice_stripped_leg_spans(stripped: str) -> "list[tuple[int, int]]":
    """The (start, end) offset spans of each leg within `stripped` — the span form of
    `_slice_stripped_legs` (which is `[stripped[s:e] for s, e in ...]`, byte- identical output
    by construction).
    """
    view = _FD_REDIRECT_RE.sub(
        lambda m: " " * len(m.group()), _mask_shell_quotes(stripped)
    )
    spans, last = [], 0
    for m in _COMPOUND_OPS_RE.finditer(view):
        spans.append((last, m.start()))
        last = m.end()
    spans.append((last, len(stripped)))
    return spans
