"""
Location: pact-plugin/hooks/shell/words.py
Summary: The shell-command tokenizer shared by the Bash PreToolUse guards: split a command
         into one word list per simple command, and strip the runner words (rtk, env,
         sudo, bash -c, ...) so the first word left is the program that actually runs.
         words()/tokens() is the one quote-aware word reader: no guard runs shlex itself.
         token_spans() is the same reader with where each word stands, for the merge
         guard's canonical spelling. mask_quotes(), normalize_line_continuations() and
         leg_token_spans() are the merge guard's lenient scanners, moved here verbatim:
         an open quote fails TOWARD UNMASKED and the output keeps the input's length,
         where token_spans() abstains (None); the strip carriers depend on that.
Used by: hooks/shell_words.py (the façade the guards still import: process_table_guard.py,
         egress_guard.py, egress_uploads.py, docker_lock_gate.py, background_test_gate.py,
         merge_guard_pre.py, git_commit_check.py), and directly by hooks/shell/merge/* and
         hooks/shell/env/*, run_ledger.py and hooks/shared/ (merge_guard_common.py,
         commit_rules.py, runcounts.py, evidence/files.py). Tested through them and
         tests/test_shell_words.py, whose cross-guard table runs one set of hostile command
         shapes through every consumer.

Lives in the `shell` package, whose __init__ imports nothing, not in `shared`: importing
`shared` runs its __init__, which loads about twenty modules, and both guards run before
every Bash call. This module imports nothing, which tests/test_process_table_guard.py pins.

String matching only: quotes are deleted, not parsed, so a quoted operator splits a
segment. That errs toward reading more commands, never fewer. quoted_words() is the one
reader that honours quotes, for a caller that has a rule about which quoted text is data.

read_script() is the one place that decides what is a comment, a quoted string, a
substitution and a heredoc body, so that every later pass of a guard reads the same text.
It reads only what bash and zsh read alike and raises Unreadable for the rest: a guard
that cannot be sure how the shell reads a line must not guess.
"""

from __future__ import annotations

MAX_NESTING = 8                 # substitutions inside substitutions
HEREDOC_MARK = "__repact_heredoc_{}__"
_RESERVED = "__repact_"         # the guards' own placeholder words
_WORD_END = " \t\n;|&()<>"
_ANSI = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
         "e": "\x1b", "E": "\x1b", "\\": "\\", "'": "'", '"': '"', "?": "?"}
_DELIMITER_EXTRA = "_.-!,/:@%"    # each closes a heredoc as typed in bash 3.2, zsh and sh (`cat <<!`)
_ARITHMETIC_BUDGET = 20_000     # characters looked at while matching `((` brackets


class Unreadable(ValueError):
    """Text read_script cannot be sure it reads as the shell will. `what` names the
    construct and `instead` the form to write, for the caller's message. `broken`: the
    shell cannot read it either (something is left open), so it stops there with a syntax
    error after running the complete commands before it."""

    def __init__(self, what: str, instead: str, broken: bool = False):
        super().__init__(what)
        self.what, self.instead, self.broken = what, instead, broken


_OPEN = ("a quote, a substitution or a `${` left open", "close it", True)
_HEREDOC_LINE = ("a quoted line break or a substitution that spans lines on the line of a "
                 "heredoc operator", "put the heredoc operator last on its line")
_DEEP = (f"substitutions nested more than {MAX_NESTING} deep",
         "run the inner commands first and pass their results in variables")
_COMMENT_JOINED = ("a comment that ends in a backslash inside a substitution",
                   "drop the backslash, or move the comment out of the $(...)")
_TAB_JOINED = ("a `<<-` heredoc line that reaches its closing delimiter through a backslash "
               "and a tab", "write the closing delimiter on a line of its own")


def _unescape_backticks(raw: str) -> str:
    """The command inside backticks: one level of escaping removed from ` $ and \\."""
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and raw[i + 1:i + 2] in ("`", "$", "\\"):
            i += 1
        out.append(raw[i:i + 1])
        i += 1
    return "".join(out)


class _Reader:
    """One pass over a script. `out` collects the text as later passes read it: comments
    removed, `$'..'` strings spelled as plain quoted ones, each heredoc operator replaced
    by `<<` and a mark, its body moved to `heredocs` as [bare delimiter, body]."""

    def __init__(self, text: str, flat: bool = False):
        # flat: the text is read_script's own output, where `<<` and a mark is a heredoc
        # already taken. In any other text those words are a heredoc like every other.
        self.text, self.out, self.heredocs, self.flat = text, [], [], flat
        self.budget = _ARITHMETIC_BUDGET + len(text)

    def script(self, i: int, nested: bool, depth: int, held: bool,
               doubt: int | None = None) -> int:
        """Read commands from text[i:] to the end of the text, or (nested) to the
        parenthesis that closes a substitution; the index after it. `held`: a line outside
        this substitution still waits for a heredoc body, so no line may end in here.
        `doubt`: the bracket depth above which a `((` may be arithmetic to the shell though
        not to this reader, so a `<<` there may be a shift and is not read at all."""
        if depth > MAX_NESTING:
            raise Unreadable(*_DEEP)
        text, out, n = self.text, self.out, len(self.text)
        pending: list[tuple[int, bool, str]] = []
        parens, word, fresh = 0, "", True               # fresh: at the start of a word
        while i < n:
            char = text[i]
            waits = held or bool(pending)
            if char == "\\":
                if text[i + 1:i + 2] != "\n":           # backslash-newline joins two lines
                    out.append(text[i:i + 2])
                    fresh, word = False, word[:4] + "\\"
                i += 2
            elif char in "'\"`$":
                if char == "'":
                    i = self._single(i, waits)
                elif char == '"':
                    i = self._double(i + 1, depth, waits)
                elif char == "`":
                    i = self._backticks(i, depth, waits, False)
                else:
                    i = self._dollar(i, depth, waits)
                fresh, word = False, word[:4] + char
            elif char == "#" and fresh:
                if i and text[i - 1] in "(<>":
                    raise Unreadable("a `#` right after `(`, `<` or `>`",
                                     "put a space before the comment, or drop it")
                end = text.find("\n", i)                # the newline ends the command: kept
                # bash 3.2 joins the next line to a comment inside $(...); zsh does not.
                if nested and 0 < end and text[end - 1] == "\\":
                    raise Unreadable(*_COMMENT_JOINED)
                i = n if end < 0 else end
            elif char == "\n":
                if held:
                    raise Unreadable(*_HEREDOC_LINE)
                self._word(word, depth)
                out.append("\n")
                i = self._bodies(i + 1, pending) if pending else i + 1
                pending, fresh, word = [], True, ""
            elif text.startswith("<<", i):
                if text.startswith("<<<", i):           # a here-string: its word follows
                    out.append("<<<")
                    i += 3
                elif self.flat and text.startswith(_RESERVED, i + 2):
                    out.append("<<")
                    i += 2
                elif doubt is not None:
                    raise Unreadable("a `<<` inside `((` brackets that may be arithmetic",
                                     "write the shift or the heredoc in a statement of its own")
                else:
                    i = self._heredoc(i + 2, pending)
                fresh, word = True, ""
            else:
                if char == "(":
                    if fresh and text.startswith("((", i):
                        end = self._arithmetic(i + 2)
                        if end:
                            out.append(text[i:end])
                            i, fresh, word = end, False, ""
                            continue
                        doubt = parens if doubt is None else doubt
                    parens += 1
                elif char == ")":
                    if nested and not parens:
                        if pending:
                            raise Unreadable(*_HEREDOC_LINE)
                        self._word(word, depth)
                        return i + 1
                    parens -= 1
                    if doubt is not None and 0 <= parens <= doubt:
                        doubt = None
                out.append(char)
                i += 1
                if char in _WORD_END:
                    self._word(word, depth)
                    fresh, word = True, ""
                else:
                    fresh, word = False, word[:4] + char
        if nested:
            raise Unreadable(*_OPEN)
        return self._bodies(n, pending)

    @staticmethod
    def _word(word: str, depth: int) -> None:
        # `case x in a) ..` closes a substitution early for any reader that counts brackets.
        if depth and word in ("case", "esac"):
            raise Unreadable("a case statement inside a substitution",
                             "run it outside the $(...)")

    def _single(self, i: int, waits: bool) -> int:
        end = self.text.find("'", i + 1)
        if end < 0:
            raise Unreadable(*_OPEN)
        if waits and "\n" in self.text[i:end]:
            raise Unreadable(*_HEREDOC_LINE)
        self.out.append(self.text[i:end + 1])
        return end + 1

    def _double(self, i: int, depth: int, waits: bool) -> int:
        text, out, n = self.text, self.out, len(self.text)
        out.append('"')
        while i < n:
            char = text[i]
            if char == "\\":
                if text[i + 1:i + 2] != "\n":
                    out.append(text[i:i + 2])
                i += 2
            elif char == '"':
                out.append(char)
                return i + 1
            elif char == "`":
                i = self._backticks(i, depth, waits, True)
            elif char == "$" and text[i + 1:i + 2] in ("(", "{"):
                i = self._dollar(i, depth, waits)
            else:
                if char == "\n" and waits:
                    raise Unreadable(*_HEREDOC_LINE)
                out.append(char)
                i += 1
        raise Unreadable(*_OPEN)

    def _dollar(self, i: int, depth: int, waits: bool) -> int:
        """A `$` and what it opens: $'..', $((..)), $(..), ${..}, $[..], or nothing."""
        text, out = self.text, self.out
        opener = text[i + 1:i + 2]
        if opener == "'":
            return self._ansi(i + 2, waits)
        if opener == "(":
            end = self._arithmetic(i + 3) if text.startswith("$((", i) else 0
            if end:
                out.append(text[i:end])
                return end
            out.append("$(")
            end = self.script(i + 2, True, depth + 1, waits,
                              -1 if text.startswith("$((", i) else None)
            out.append(")")
            return end
        if opener == "{":
            return self._parameter(i, depth, waits)
        end = text.find("]", i) if opener == "[" else -1
        if end > 0 and not any(char in text[i:end] for char in "\n'\"`"):
            out.append(text[i:end + 1])                 # $[1<<2]: the old arithmetic form
            return end + 1
        out.append("$")
        return i + 1

    def _arithmetic(self, j: int) -> int:
        """The index after the `))` that closes an arithmetic expression whose text starts
        at j, or 0 when this reader cannot tell it from two nested brackets of commands
        (then it is read as those, in doubt). Both shells decide by the brackets alone."""
        text, depth = self.text, 0
        for k in range(j, len(text)):
            char = text[k]
            self.budget -= 1
            if self.budget < 0:
                raise Unreadable("more `((` brackets than this reader will follow",
                                 "split the command")
            if char in "\n'\"`\\" or char == "$" and text[k + 1:k + 2] == "(":
                return 0
            if char == "(":
                depth += 1
            elif char == ")":
                if not depth:
                    return k + 2 if text[k + 1:k + 2] == ")" else 0
                depth -= 1
        return 0

    def _parameter(self, i: int, depth: int, waits: bool) -> int:
        text, n = self.text, len(self.text)
        j, nest, start = i + 2, 1, i
        while j < n:
            char = text[j]
            if char == "\\":
                j += 2
            elif char in "'\"" and self._plain_pair(j):
                j = text.index(char, j + 1) + 1
            elif char in "'\"`\n" or text.startswith("<<", j):
                raise Unreadable("a backtick, a line break, `<<` or a quoted string that is "
                                 "not plain text inside ${...}",
                                 "work the value out in a statement of its own")
            elif char == "$" and text[j + 1:j + 2] == "{":
                nest, j = nest + 1, j + 2
            elif char == "$" and text[j + 1:j + 2] == "(":
                self.out.append(text[start:j])
                j = start = self._dollar(j, depth, waits)
            elif char == "}":
                nest, j = nest - 1, j + 1
                if not nest:
                    self.out.append(text[start:j])
                    return j
            else:
                j += 1
        raise Unreadable(*_OPEN)

    def _plain_pair(self, j: int) -> bool:
        """True when the quote at j opens a string inside ${...} that every shell reads to
        the same end whether or not it takes the quotes as quoting there (bash does not,
        for '..' inside "${..}"): no brace, quote, backtick, backslash or line break in it,
        nothing that runs, and inside '..' nothing that expands."""
        end = self.text.find(self.text[j], j + 1)
        inside = self.text[j + 1:max(end, j + 1)]
        return end > 0 and not any(char in inside for char in "{}()'\"`\\\n") \
            and not (self.text[j] == "'" and "$" in inside)

    def _backticks(self, i: int, depth: int, waits: bool, quoted: bool) -> int:
        text, n = self.text, len(self.text)
        j = i + 1
        while j < n and text[j] != "`":
            j += 2 if text[j] == "\\" else 1
        if j >= n:
            raise Unreadable(*_OPEN)
        body = _unescape_backticks(text[i + 1:j])
        inner = _Reader(body)
        inner.script(0, False, depth + 1, False)
        if "".join(inner.out) != body or waits and "\n" in body or quoted and '\\"' in body:
            raise Unreadable("a comment, a heredoc, a $'..' string or an escaped quote inside "
                             "backticks", "write the substitution as $(...)")
        self.out.append(text[i:j + 1])
        return j + 1

    def _ansi(self, i: int, waits: bool) -> int:
        """A $'..' string, spelled as plain single-quoted text holding its value."""
        text, n, value = self.text, len(self.text), []
        odd = Unreadable("a $'..' string with an escape this reader does not spell",
                         "write the characters out in plain quotes")
        while i < n:
            char = text[i]
            if char == "'":
                literal = "".join(value)
                if "\0" in literal:
                    raise odd
                if waits and "\n" in literal:
                    raise Unreadable(*_HEREDOC_LINE)
                self.out.append("'" + literal.replace("'", "'\\''") + "'")
                return i + 1
            if char != "\\":
                value.append(char)
                i += 1
                continue
            code = text[i + 1:i + 2]
            if code in _ANSI:
                value.append(_ANSI[code])
                i += 2
                continue
            most = {"x": 2, "u": 4, "U": 8}.get(code, 0)
            digits, j = "0123456789abcdefABCDEF", i + 2
            if not most:
                if not code or code not in "01234567":
                    raise odd
                most, digits, j = 3, "01234567", i + 1
            k = j
            while k < n and k - j < most and text[k] in digits:
                k += 1
            number = int(text[j:k], len(digits) if most == 3 else 16) if k > j else -1
            # \x and octal give bytes, not characters: only ASCII is the same in both.
            if not 0 < number < (0x80 if most < 4 else 0xD800):
                raise odd
            value.append(chr(number))
            i = k
        raise Unreadable(*_OPEN)

    def _heredoc(self, j: int, pending: list) -> int:
        """Note the heredoc whose operator ends at j. Its delimiter must be one plain word,
        bare or wholly quoted: any other form is read differently by some reader."""
        text, n = self.text, len(self.text)
        strip = text[j:j + 1] == "-"
        j += strip
        while text[j:j + 1] in (" ", "\t"):
            j += 1
        first = text[j:j + 1]
        if first in ("'", '"'):
            end = text.find(first, j + 1)
            word, j = (text[j + 1:end], end + 1) if end > 0 else ("", n)
        else:
            k = j = j + 1 if first == "\\" else j
            while k < n and text[k].isascii() and (text[k].isalnum() or text[k] in _DELIMITER_EXTRA):
                k += 1
            word, j = text[j:k], k
        plain = word and all(char.isascii() and (char.isalnum() or char in _DELIMITER_EXTRA)
                             for char in word)
        if not plain or j < n and text[j] not in _WORD_END:
            raise Unreadable("a heredoc delimiter that is not one plain word",
                             "use a plain delimiter such as <<'EOF'")
        self.heredocs.append([first not in ("'", '"', "\\"), ""])
        pending.append((len(self.heredocs) - 1, strip, word))
        self.out.append("<<" + HEREDOC_MARK.format(len(self.heredocs) - 1))
        return j

    def _bodies(self, i: int, pending: list) -> int:
        """Take the bodies of the heredocs opened on the line that ends before i. A body
        with no closing line runs to the end of the text, as it does in both shells. Under
        a bare delimiter both shells join a backslash-newline before they look for the
        closing line (`EO\\` then `F` closes <<EOF), so the body holds the joined lines;
        under a quoted one the lines are taken as typed."""
        text, n = self.text, len(self.text)
        for index, strip, delimiter in pending:
            bare, lines = self.heredocs[index][0], []
            while i < n:
                end = text.find("\n", i)
                line, i = (text[i:], n) if end < 0 else (text[i:end], end + 1)
                cut = None                              # where the first piece ends
                while bare and end >= 0 and (len(line) - len(line.rstrip("\\"))) % 2:
                    cut = len(line) - 1 if cut is None else cut
                    end = text.find("\n", i)
                    line, i = (line[:-1] + text[i:], n) if end < 0 \
                        else (line[:-1] + text[i:end], end + 1)
                closes = (line.lstrip("\t") if strip else line) == delimiter
                # bash strips the tabs of the joined line, zsh those of its first piece.
                if strip and cut is not None \
                        and closes != (line[:cut].lstrip("\t") + line[cut:] == delimiter):
                    raise Unreadable(*_TAB_JOINED)
                if closes:
                    break
                lines.append(line)
            self.heredocs[index][1] = "\n".join(lines)
        return i


def read_script(text: str) -> tuple[str, list[tuple[bool, str]]]:
    """(the script without its comments and heredoc bodies, [(bare delimiter, body)]).
    In the text each heredoc operator and delimiter is `<<` plus HEREDOC_MARK with the
    heredoc's index; a `$'..'` string is spelled as plain quoted text; a backslash-newline
    is gone. Substitutions are read through, to any depth up to MAX_NESTING. Raises
    Unreadable for what the shells may read differently from this reader."""
    if "\0" in text:
        raise Unreadable("a NUL character", "remove it")
    reader = _Reader(text)
    reader.script(0, False, 0, False)
    return "".join(reader.out), [(bare, body) for bare, body in reader.heredocs]


def substitution_spans(text: str, quoting: bool = True) -> list[tuple[int, int, str]]:
    """(start, end, command) of each outermost $(...) and `...` the shell would run in
    `text`: read_script's output, or with quoting off a heredoc body as typed, where quote
    characters are plain text. Outside single quotes a backslash makes the next character
    literal; inside backticks one level of escaping is removed from the command."""
    reader, found, i, quote, n = _Reader(text, flat=quoting), [], 0, "", len(text)
    while i < n:
        char = text[i]
        if char == "\\" and quote != "'":
            i += 2
        elif quoting and char == "'" and quote != '"':
            quote, i = "" if quote else "'", i + 1
        elif quoting and char == '"' and quote != "'":
            quote, i = "" if quote else '"', i + 1
        elif quote != "'" and text.startswith("$(", i):
            end = reader._arithmetic(i + 3) if text.startswith("$((", i) else 0
            if not end:
                end = reader.script(i + 2, True, 1, False)
                found.append((i, end, text[i + 2:end - 1]))
            i = end
        elif quote != "'" and char == "`":
            j = i + 1
            while j < n and text[j] != "`":
                j += 2 if text[j] == "\\" else 1
            found.append((i, min(j + 1, n), _unescape_backticks(text[i + 1:j])))
            i = j + 1
        else:
            i += 1
    return found


# Shell operators end a segment; quotes and backslashes are deleted so that
# `bash -c 'ps aux'` and `"$(pgrep -f x)"` are read as the commands they run.
SEGMENT_TABLE = str.maketrans({
    "|": "\n", ";": "\n", "&": "\n", "(": "\n", ")": "\n", "`": "\n",
    "{": "\n", "}": "\n", "<": "\n", ">": "\n",
    "'": None, '"': None, "\\": None,
})

# Reserved words after which the shell reads the next word as a command (unquoted only).
SHELL_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!"})
# Leading words that run the next word as the command (noglob, nocorrect and repeat N are
# zsh precommand modifiers).
PREFIX_WORDS = {
    "rtk", "proxy", "command", "builtin", "exec", "nohup", "time", "env", "sudo", "doas", "nice",
    "timeout", "watch", "xargs", "eval", "sh", "bash", "zsh", "dash", "ksh", "fish", "noglob",
    "nocorrect", "repeat", "stdbuf", "caffeinate", "chronic", "unbuffer", "arch", "busybox",
    "flock", "setsid", "taskset", "ionice", "chrt", "numactl", "script", "parallel",
    *SHELL_KEYWORDS,
}
# The one table of runner options whose value is the next word (or the rest of a short
# cluster: -uroot, env -iC DIR), per runner, so a flag one runner takes alone never swallows
# the command after another (sudo -s curl, watch -g curl). Every guard reads it through
# runner_option().
RUNNER_VALUE_OPTIONS = {
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-P", "-L", "-U"}),
    "sudo": frozenset({"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C",
                       "--close-from", "-D", "--chdir", "-r", "--role", "-t", "--type", "-U",
                       "--other-user", "-T", "--command-timeout"}),
    "doas": frozenset({"-u", "-C"}),
    "exec": frozenset({"-a"}),
    "time": frozenset({"-f", "--format", "-o", "--output"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "watch": frozenset({"-n", "--interval"}),
    "xargs": frozenset({"-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-J", "-L", "-n",
                        "--max-args", "-P", "--max-procs", "-s", "--max-chars", "-R", "-S",
                        "--process-slot-var"}),
    "stdbuf": frozenset({"-i", "--input", "-o", "--output", "-e", "--error"}),
    "caffeinate": frozenset({"-t", "-w"}),
    "flock": frozenset({"-w", "--wait", "--timeout", "-E", "--conflict-exit-code"}),
    "ionice": frozenset({"-c", "--class", "-n", "--classdata", "-p", "--pid", "-P", "--pgid",
                         "-u", "--uid"}),
    "chrt": frozenset({"-T", "--sched-runtime", "-P", "--sched-period", "-D", "--sched-deadline"}),
    "numactl": frozenset({"-N", "--cpunodebind", "-m", "--membind", "-C", "--physcpubind",
                          "-i", "--interleave", "-p", "--preferred", "-w", "--weighted-interleave"}),
    "script": frozenset({"-E", "--echo", "-I", "--log-in", "-O", "--log-out", "-B", "--log-io",
                         "-T", "--log-timing", "-m", "--logging-format", "-o", "--output-limit",
                         "-t"}),
    "parallel": frozenset({"-j", "--jobs", "-P", "--max-procs", "-a", "--arg-file", "-I", "-n",
                           "--max-args", "-N", "--max-replace-args", "-d", "--delimiter",
                           "--colsep", "-S", "--sshlogin", "--joblog", "--results", "--tmpdir",
                           "-L", "--max-lines", "-E", "--eof", "--timeout", "--delay",
                           "--retries", "--env", "--workdir", "--wd"}),
}
# Runners that take operands before the command: flock LOCKFILE command, taskset MASK
# command, chrt PRIORITY command, and BSD script FILE command.
RUNNER_OPERANDS = {"flock": 1, "taskset": 1, "chrt": 1, "script": 1}
# Runner options whose value is a shell command line (flock -c 'curl ...', script -c '...'):
# after one, the next word is the command and no operand is read.
RUNNER_COMMAND_OPTIONS = {"flock": frozenset({"-c", "--command"}),
                          "script": frozenset({"-c", "--command"})}


def runner_option(runner: str, words: list[str], i: int) -> tuple[str, str | None, int]:
    """(option name, its value or None, words it spans) for the option words[i] given to
    `runner`: `-C dir`, `-Cdir`, `-iC dir`, `--chdir dir`, `--chdir=dir`. A short cluster
    takes a value at its first letter that takes one: the rest of the word, or the next word
    when that letter ends it."""
    word, options = words[i], RUNNER_VALUE_OPTIONS.get(runner, frozenset())
    following = words[i + 1] if i + 1 < len(words) else ""
    if word.startswith("--"):
        name, equals, value = word.partition("=")
        if equals:
            return name, value, 1
        return (name, following, 2) if name in options else (name, None, 1)
    for k, letter in enumerate(word[1:], start=1):
        if "-" + letter in options:
            if k == len(word) - 1:
                return "-" + letter, following, 2
            return "-" + letter, word[k + 1:], 1
    return word, None, 1


def runner_prefix(words: list[str], keep: frozenset = frozenset()
                  ) -> tuple[int, list[tuple[str, str, str | None]]]:
    """(index of the command word, [(runner, option, value)]) for the leading assignments and
    runner words of a simple command: one ("xargs", "", None) entry per runner word, then
    one per option it was given. A runner word in `keep` is returned as the command."""
    i, runner, operands, seen = 0, "", 0, []
    while i < len(words):
        word = words[i]
        if runner and word.startswith("-"):
            name, value, width = runner_option(runner, words, i)
            seen.append((runner, name, value))
            i += width
            if name in RUNNER_COMMAND_OPTIONS.get(runner, ()):
                operands = 0
        elif operands:                                  # flock's lock file, even one named sh
            operands -= 1
            i += 1
        elif word in PREFIX_WORDS and word not in keep:
            runner, operands = word, RUNNER_OPERANDS.get(word, 0)
            seen.append((word, "", None))
            i += 1
        elif "=" in word and word.split("=", 1)[0].isidentifier():
            i += 1
        elif runner and word[:1].isdigit():
            i += 1                                      # timeout 5, nice 10, repeat 3
        else:
            break
    return i, seen


def segments(command: str) -> list[list[str]]:
    """Split a shell command into word lists, one per simple command. A backslash-newline
    joins two lines into one word (`cu\\<newline>rl` runs curl)."""
    return [line.split() for line in command.replace("\\\n", "").translate(SEGMENT_TABLE).split("\n")]


def strip_prefix(words: list[str]) -> list[str]:
    """Drop leading assignments and runner words (rtk, env, sudo, bash -c, ...) with
    their options, so the word returned first is the command that actually runs."""
    return words[runner_prefix(words)[0]:]


_OPERATORS = "|;&()`{}<>\n"


def quoted_words(command: str) -> list[list[tuple[str, bool]]] | None:
    """Split a command at the operators that stand outside quotes: one list of
    (word, quoted) per simple command, the word with its quotes removed and `quoted` True
    when any part of it stood inside quotes. None when a quoted string cannot be taken as
    plain data: a quote left open, or a double-quoted string holding a live `$(` or
    backtick (the shell runs those)."""
    pieces: list[list[tuple[str, bool]]] = [[]]
    word, quoted, quote, i = "", False, "", 0
    text = command + "\n"
    while i < len(text):
        char = text[i]
        i += 1
        if quote == "'":
            if char == "'":
                quote = ""
            else:
                word += char
        elif quote:
            if char == '"':
                quote = ""
            elif char == "`" or char == "$" and text.startswith("(", i):
                return None
            elif char == "\\":
                word += text[i:i + 1]
                i += 1
            else:
                word += char
        elif char in "'\"":
            quote, quoted = char, True
        elif char == "\\":
            word += text[i:i + 1].strip("\n")            # backslash-newline joins two lines
            i += 1
        elif char in " \t\r" or char in _OPERATORS:
            if word or quoted:
                pieces[-1].append((word, quoted))
            word, quoted = "", False
            if char in _OPERATORS and pieces[-1]:
                pieces.append([])
        else:
            word += char
    return None if quote else [piece for piece in pieces if piece]


_BLANKS = " \t\r\n"
_DOUBLE_ESCAPES = "\"\\$`\n"                    # what a backslash escapes inside "..."


def _ansi_value(command: str, i: int) -> tuple[str, int] | None:
    """(the value of the $'..' string whose body starts at i, the index after its closing
    quote), or None when it is left open. An escape the shell does not know stays as typed;
    a NUL ends the value, as it does in bash."""
    value, n, cut = [], len(command), -1
    while i < n:
        char = command[i]
        if char == "'":
            return "".join(value[:cut] if cut >= 0 else value), i + 1
        if char != "\\":
            value.append(char)
            i += 1
            continue
        code = command[i + 1:i + 2]
        most = {"x": 2, "u": 4, "U": 8}.get(code, 0)
        digits, j = "0123456789abcdefABCDEF", i + 2
        if code and code in "01234567":
            most, digits, j = 3, "01234567", i + 1
        k = j
        while most and k < n and k - j < most and command[k] in digits:
            k += 1
        if k > j:
            number = int(command[j:k], 8 if most == 3 else 16)
            if number == 0 and cut < 0:
                cut = len(value)
            value.append(chr(number) if 0 < number < 0x110000 and not 0xD800 <= number < 0xE000
                         else "")
            i = k
        else:
            value.append(_ANSI.get(code, "\\" + code))
            i += 2
    return None


def tokens(command: str, operators: str = "") -> list[str] | None:
    """The words of a command as the shell splits them, with their quotes removed: what
    every guard reads. With `operators`, each unquoted run of those characters is a token
    of its own (`a;b` is a, ;, b); a quoted one stays inside its word. None when the shell
    cannot read the quoting: a quote left open or a backslash at the very end.

    The rules are shlex.split's (posix, no comments: a `#` word is read like any other, so
    a guard sees more, never less), corrected where the shell reads otherwise: a
    backslash-newline joins two lines, inside double quotes a backslash also escapes `$`,
    a backtick and a line break, `$'..'` is spelled as its value and `$"..."` as plain
    double quotes. `$(..)` and backticks are not read as one word: they split at their
    spaces, so the commands inside them show up as words."""
    found = token_spans(command, operators)
    return None if found is None else [token for _, _, token in found]


def token_spans(command: str, operators: str = "") -> list[tuple[int, int, str]] | None:
    """tokens() with where each token stands: (start, end, token), command[start:end]
    being the token as typed (an operator token is typed as it reads). The one reader
    behind tokens(); a caller that rewrites a word in place slices with these."""
    found: list[tuple[int, int, str]] = []
    word: list[str] = []
    started, start, i, n = False, 0, 0, len(command)
    while i < n:
        char = command[i]
        if char in operators or char in _BLANKS:
            if started:
                found.append((start, i, "".join(word)))
            word, started = [], False
            if char in operators:
                end = i
                while end < n and command[end] in operators:
                    end += 1
                found.append((i, end, command[i:end]))
                i = end
            else:
                i += 1
            continue
        if not started:
            start = i
        if char == "\\":
            if i + 1 == n:
                return None
            if command[i + 1] != "\n":                  # backslash-newline joins two lines
                word.append(command[i + 1])
                started = True
            i += 2
        elif char == "'":
            end = command.find("'", i + 1)
            if end < 0:
                return None
            word.append(command[i + 1:end])
            started, i = True, end + 1
        elif char == "$" and command[i + 1:i + 2] == "'":
            read = _ansi_value(command, i + 2)
            if read is None:
                return None
            word.append(read[0])
            started, i = True, read[1]
        elif char == '"' or char == "$" and command[i + 1:i + 2] == '"':
            i += 1 if char == '"' else 2
            while True:
                if i >= n:
                    return None
                char = command[i]
                if char == '"':
                    i += 1
                    break
                if char == "\\" and i + 1 < n and command[i + 1] in _DOUBLE_ESCAPES:
                    word.append(command[i + 1] if command[i + 1] != "\n" else "")
                    i += 2
                else:
                    word.append(char)
                    i += 1
            started = True
        else:
            word.append(char)
            started = True
            i += 1
    if started:
        found.append((start, n, "".join(word)))
    return found


def words(command: str) -> list[str] | None:
    """The words of a command, quotes removed, operators left inside the words around
    them (tokens() without operators): the shared stand-in for shlex.split. None when the
    quoting cannot be read."""
    return tokens(command)


# ─── What a typed word expands (shell/opaque.py) ─────────────────────────────────────────
def expanding_text(typed: str) -> str:
    """The part of a typed word the shell expands: each single-quoted string outside double
    quotes removed. A backslash keeps the character after it, and an open quote keeps the
    rest, so the text errs toward reading more expansions, never fewer."""
    out, i, n, double = [], 0, len(typed), False
    while i < n:
        char = typed[i]
        if char == "\\":
            out.append(typed[i:i + 2])
            i += 2
            continue
        if char == '"':
            double = not double
        elif char == "'" and not double:
            end = typed.find("'", i + 1)
            if end < 0:
                out.append(typed[i:])
                break
            i = end + 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


def unquoted_text(typed: str) -> str:
    """The characters of a typed word the shell may glob: quoted strings, escaped
    characters and ${...} expansions left out."""
    out, i, n = [], 0, len(typed)
    while i < n:
        char = typed[i]
        if char == "\\":
            i += 2
        elif char == "'":
            close = typed.find("'", i + 1)
            i = n if close < 0 else close + 1
        elif char == '"':
            i += 1
            while i < n and typed[i] != '"':
                i += 2 if typed[i] == "\\" else 1
            i += 1
        elif typed.startswith("${", i):
            close = typed.find("}", i)
            i = n if close < 0 else close + 1
        else:
            out.append(char)
            i += 1
    return "".join(out)


def is_single_quoted(typed: str) -> bool:
    """True for a typed word that is wholly one single-quoted string."""
    return len(typed) > 1 and typed[0] == "'" == typed[-1]


def _name_char(char: str, first: bool) -> bool:
    return char.isascii() and (char.isalpha() or char == "_" or not first and char.isdigit())


def _expansion_at(text: str, i: int) -> tuple[str, int] | None:
    """(name, end) of a plain `$NAME`, `${NAME}`, `$1`, `${12}`, `$@` or `$*` at text[i]."""
    j, n = i + 1, len(text)
    if j < n and text[j] == "{":
        close = text.find("}", j + 1)
        inner = text[j + 1:close] if close > 0 else ""
        plain = inner and _name_char(inner[0], True) and all(_name_char(c, False) for c in inner)
        if plain or inner.isdigit() or inner in ("@", "*"):
            return inner, close + 1
        return None
    if j < n and _name_char(text[j], True):
        k = j + 1
        while k < n and _name_char(text[k], False):
            k += 1
        return text[j:k], k
    if j < n and (text[j].isdigit() or text[j] in "@*"):
        return text[j], j + 1
    return None


def spell(text: str, values: dict) -> str:
    """`text` with each $NAME and ${NAME} outside single quotes written as values[NAME]."""
    if not values:
        return text
    out, i, quote, n = [], 0, "", len(text)
    while i < n:
        char = text[i]
        if quote == "'":
            out.append(char)
            quote = "" if char == "'" else quote
            i += 1
            continue
        if char == "\\":
            out.append(text[i:i + 2])
            i += 2
            continue
        if char == "$" and quote != '"' and text.startswith("$'", i):    # $'..' opens at its quote
            out.append("$")
            i += 1
            continue
        if char == "'" and quote != '"':
            quote = "'"
        elif char == '"':
            quote = "" if quote == '"' else '"'
        elif char == "$":
            found = _expansion_at(text, i)
            if found and found[0] in values:
                out.append(values[found[0]])
                i = found[1]
                continue
        out.append(char)
        i += 1
    return "".join(out)


# ─── The merge guard's lenient scanners (from shared/merge_guard_common.py at 0edd3594) ───
def quoted_inner(token: str) -> str | None:
    """The text inside a token that is wholly one quoted string ('..' or \"..\"), else None."""
    if len(token) >= 2 and token[0] in "'\"" and token[-1] == token[0]:
        return token[1:-1]
    return None


def is_double_quoted(token: str) -> bool:
    """True for a token that is wholly one double-quoted string."""
    return len(token) >= 2 and token[0] == '"' == token[-1]


def _quote_end(text: str, i: int) -> int:
    """The index after the quote that closes the one at text[i], or -1 when it is left open.
    A backslash escapes inside "..", and inside '..' when an unescaped `$` opens it ($'..')."""
    quote, j, n, k = text[i], i + 1, len(text), i
    while k and text[k - 1] == "$":
        k -= 1
    b = k
    while b and text[b - 1] == "\\":
        b -= 1
    escapes = quote == '"' or (i - k - (k - b) % 2) % 2 == 1
    while j < n:
        if text[j] == quote:
            return j + 1
        j += 2 if escapes and text[j] == "\\" else 1
    return -1


def quote_spans(command: str) -> "list[tuple[int, int]]":
    """(start, end) of each closed quoted string, in order. An open quote ends the list: the
    text from it on FAILS TOWARD UNQUOTED."""
    spans, i, n = [], 0, len(command)
    while i < n:
        c = command[i]
        if c == "\\":
            i += 2  # escaped char (outside a quote) — next char is literal, not a delim
            continue
        if c == "'" or c == '"':
            j = _quote_end(command, i)
            if j < 0:
                break
            spans.append((i, j))
            i = j
            continue
        i += 1
    return spans


def mask_quotes(command: str) -> str:
    """P2: bounded same-length quote-state scanner (each quote_spans string spelled as spaces)."""
    out = list(command)
    for i, j in quote_spans(command):
        out[i:j] = " " * (j - i)
    return "".join(out)


def normalize_line_continuations(command: str) -> str:
    """P0 (shell-semantic substrate SSOT): resolve bash line-continuations BEFORE tokenization."""
    return command.replace("\\\n", "")


def leg_token_spans(leg: str) -> "list[tuple[int, int]]":
    """Quote-aware shell-token spans (start, end) into `leg`: a token is a maximal run with NO
    UNQUOTED whitespace (a quoted or backslash-escaped whitespace stays INSIDE the token).
    """
    spans: list[tuple[int, int]] = []
    i, n = 0, len(leg)
    tok_start = -1
    while i < n:
        c = leg[i]
        if c.isspace():
            if tok_start >= 0:
                spans.append((tok_start, i))
                tok_start = -1
            i += 1
            continue
        if tok_start < 0:
            tok_start = i
        if c == "\\":
            i += 2
            continue
        if c == "'" or c == '"':
            i = _quote_end(leg, i)
            i = n if i < 0 else i
            continue
        i += 1
    if tok_start >= 0:
        spans.append((tok_start, n))
    return spans
