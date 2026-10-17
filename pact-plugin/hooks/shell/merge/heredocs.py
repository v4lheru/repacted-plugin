"""
Location: pact-plugin/hooks/shell/merge/heredocs.py
Summary: Where the heredocs of a command are, read the way the shell reads them: _scan walks
         the quote, substitution, comment and arithmetic context so `<<` counts only as an
         operator; _heredocs yields each operator with its delimiter and the end of its body;
         _runs_body says whether the command runs that body as a script;
         _join_line_continuations joins backslash-newlines except inside a quoted-delimiter
         body or a comment (_verbatim_spans), where the shell does not join them.
Used by: shell/merge/strip.py (carrier 1, _split_into_legs), shell/merge/views.py
         (canonical_spelling leaves a body that is not run alone), shell/merge/classify.py and
         shell/merge/checkout.py (the join before the strip). Below views and strip, so both
         may import it.
Map: _SHELL_PROGRAMS, _HEREDOC_OP, _heredoc_delimiter, _heredoc_close, _OPENERS, _DQ_OPENERS,
     _COMMENT_AFTER, _BODY_SCRIPT_RUNNERS, _STDIN_FILES, _scan, _heredocs, _runs_body, _expands,
     _verbatim_spans, _join_line_continuations.
"""

from __future__ import annotations

import re

from shell.words import _DELIMITER_EXTRA, _WORD_END
from shell.words import mask_quotes as _mask_shell_quotes
from shell.words import normalize_line_continuations as _normalize_line_continuations
from shell.words import words as _words

_SHELL_PROGRAMS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "mksh", "ash"})
_HEREDOC_OP = re.compile(r"(?<!<)<<(?!<)-?[ \t]*")


def _heredoc_delimiter(text: str, j: int) -> tuple[str, int] | None:
    """(the delimiter word at j with its quoting removed, where the word ends), or None when
    it is not one plain word (shell.words._heredoc's rule): `<<E\\OF` and `<<'E'OF` close
    on `EOF`, not on `E`."""
    word, k, n = [], j, len(text)
    while k < n and text[k] not in _WORD_END:
        char = text[k]
        if char == "\\":
            word.append(text[k + 1:k + 2])
            k += 2
        elif char in "'\"":
            end = text.find(char, k + 1)
            if end < 0:
                return None
            word.append(text[k + 1:end])
            k = end + 1
        else:
            word.append(char)
            k += 1
    delimiter = "".join(word)
    if not delimiter or not all(char.isascii() and (char.isalnum() or char in _DELIMITER_EXTRA)
                                for char in delimiter):
        return None
    return delimiter, k


def _heredoc_close(text: str, i: int, delimiter: str, bare: bool) -> int | None:
    """Where the closing line of a heredoc body starting at i ends its delimiter, or None.
    A line closes when, past its leading tabs, it starts with the delimiter and no word
    character follows: never later than the shell closes, so a body is never over-read.
    Under a bare delimiter a backslash-newline joins the next line first, as in the shell
    (`EO\\` then `F` closes <<EOF); a joined closing line is taken whole."""
    n = len(text)
    while i < n:
        end = text.find("\n", i)
        line, stop = (text[i:], n) if end < 0 else (text[i:end], end)
        joined = False
        while bare and end >= 0 and (len(line) - len(line.rstrip("\\"))) % 2:
            end = text.find("\n", stop + 1)
            line, stop = (line[:-1] + text[stop + 1:], n) if end < 0 \
                else (line[:-1] + text[stop + 1:end], end)
            joined = True
        body = line.lstrip("\t")
        after = body[len(delimiter):len(delimiter) + 1]
        if body.startswith(delimiter) and not (after.isalnum() or after == "_"):
            return stop if joined else stop - (len(body) - len(delimiter))
        i = stop + 1
    return None


# Openers the scanner knows, longest first, and the context each one pushes.
_OPENERS = (("$((", "A"), ("((", "A"), ("$(", "$("), ("${", "{"), ("$'", "$'"), ("(", "("),
            ("'", "'"), ('"', '"'), ("`", "`"))
_DQ_OPENERS = (("$((", "A"), ("$(", "$("), ("${", "{"), ("`", "`"))       # inside "..."
_COMMENT_AFTER = " \t\n;|&()"
_NEXT = {"'": re.compile("'"), "$'": re.compile(r"[\\']"), '"': re.compile(r'[\\"$`]')}
_NEXT_CODE = re.compile(r"[\\'\"`$(){}<#]")
# Words on a heredoc's command that run its body as a script or as arguments.
_BODY_SCRIPT_RUNNERS = _SHELL_PROGRAMS | {"fish", "csh", "tcsh", "xargs"}
_STDIN_FILES = ("/dev/stdin", "/dev/fd/0")


def _scan(text: str, i: int, stop: int, stack: list[str], find: str = "",
          comments: list | None = None) -> int:
    """Advance the context `stack` over text[i:stop] and return the index of the first
    character `find` asks for, else `stop`: "<" a heredoc operator that stands outside
    quotes, comments, `${..}` and arithmetic; "$`" a `$` or backtick outside single quotes
    (text the shell computes). Comment spans go to `comments` when it is given. Lenient where bash is strict, and biased to masking: an
    operator it misses only keeps a body, so more is read. Known limit: a `case` pattern's
    `)` inside `$(..)` closes the substitution early."""
    while i < stop:
        top = stack[-1] if stack else ""
        found = _NEXT.get(top, _NEXT_CODE).search(text, i, stop)     # jump to the next char that matters
        if found is None:
            return stop
        i, char = found.start(), found.group()
        if top == "'" or top == "$'" and char != "\\":
            if char == "'":
                stack.pop()
            i += 1
            continue
        if char == "\\":
            i += 2
            continue
        if char in find and char in "$`":
            return i
        if top == '"':
            if char == '"':
                stack.pop()
                i += 1
                continue
            opener = next((o for o in _DQ_OPENERS if text.startswith(o[0], i)), None)
            if opener:
                stack.append(opener[1])
            i += len(opener[0]) if opener else 1
            continue
        if top == "A" and text.startswith("))", i) or top == "{" and char == "}" \
                or char == ")" and top in ("$(", "(") or char == "`" and top == "`":
            stack.pop()
            i += 2 if top == "A" else 1
            continue
        if char in find and char == "<" and "A" not in stack and "{" not in stack \
                and _HEREDOC_OP.match(text, i):
            return i
        if char == "#" and top in ("", "$(", "`", "(") and (i == 0 or text[i - 1] in _COMMENT_AFTER):
            end = text.find("\n", i, stop)
            if comments is not None:
                comments.append((i, stop if end < 0 else end + 1))
            i = stop if end < 0 else end
            continue
        opener = next((o for o in _OPENERS if text.startswith(o[0], i)), None)
        if opener:
            stack.append(opener[1])
        i += len(opener[0]) if opener else 1
    return stop


def _heredocs(command: str, comments: list | None = None):
    """Each heredoc operator _scan finds, in order: (its match, the parsed delimiter or None,
    the end of its line, where its body closes or None, whether the delimiter is bare). An
    operator whose line leaves a quote or a bracket open, or ends in a backslash, has no
    body here: it is not one plain heredoc."""
    if "<<" not in command and comments is None:
        return
    stack, i, n, unclosed = [], 0, len(command), set()
    while True:
        i = _scan(command, i, n, stack, "<", comments)
        if i >= n:
            return
        match = _HEREDOC_OP.match(command, i)
        parsed = _heredoc_delimiter(command, match.end())
        line_end = command.find("\n", match.end())
        close, bare = None, True
        if parsed is not None and line_end >= 0 and command[line_end - 1] != "\\":
            bare = not re.search(r"['\"\\]", command[match.end():parsed[1]])
            after = list(stack)
            _scan(command, parsed[1], line_end, after, "", comments)
            # A delimiter with no closing line after an earlier operator has none after a
            # later one either: not scanned again, so many open heredocs stay linear.
            if after == stack and (parsed[0], bare) not in unclosed:
                close = _heredoc_close(command, line_end + 1, parsed[0], bare)
                if close is None:
                    unclosed.add((parsed[0], bare))
        yield match, parsed, line_end, close, bare
        i = match.end() if close is None else close


def _runs_body(command: str, at: int, line_end: int) -> bool:
    """True when the simple command holding the operator at `at` runs its body: a shell or
    xargs word in it (`bash -s <<EOF`, `<<EOF sh`), or a /dev/stdin operand (`source
    /dev/stdin`, `. /dev/stdin`). Read on the quote-masked line, so more words count."""
    line_start = command.rfind("\n", 0, at) + 1
    masked = _mask_shell_quotes(command[line_start:line_end])
    cut = at - line_start
    left = max(masked.rfind(sep, 0, cut) for sep in ";|&(") + 1
    right = min([k for k in (masked.find(sep, cut) for sep in ";|&)") if k >= 0] or [len(masked)])
    found = _words(re.sub(r"<<-?", " ", command[line_start + left:line_start + right]))
    if found is None:
        return True                                      # unreadable: keep the body
    names = {word.rsplit("/", 1)[-1] for word in found}
    return bool(names & _BODY_SCRIPT_RUNNERS) or any(word.endswith(_STDIN_FILES) for word in found)


def _expands(text: str) -> bool:
    """True when `text` holds a `$` or a backtick outside single quotes: text the shell
    computes, so what a program receives is not what is written."""
    return _scan(text, 0, len(text), [], "$`") < len(text)


def _verbatim_spans(command: str) -> list[tuple[int, int]]:
    r"""Where a backslash-newline is not a line continuation: the body of a quoted-delimiter
    heredoc (<<'EOF'), which the shell reads verbatim, and a comment with its newline (a
    comment ending in `\` does not continue onto the next line), in order."""
    if "\\\n" not in command:
        return []
    comments: list[tuple[int, int]] = []
    bodies = [(line_end + 1, close) for _, _, line_end, close, bare in _heredocs(command, comments)
              if close is not None and not bare]
    return sorted(bodies + comments)


def _join_line_continuations(command: str) -> str:
    r"""shell.words.normalize_line_continuations outside _verbatim_spans: `x\` then `EOF`
    still closes a quoted body, and the line after `# note \` still runs. classify judges
    this reading beside the plainly joined one."""
    out, last = [], 0
    for start, end in _verbatim_spans(command):
        if start >= last:
            out += [_normalize_line_continuations(command[last:start]), command[start:end]]
            last = end
    out.append(_normalize_line_continuations(command[last:]))
    return "".join(out)
