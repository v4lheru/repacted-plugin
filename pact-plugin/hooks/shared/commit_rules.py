"""The commit-message rules the commit_rule check returns on (stdlib only).

Location: pact-plugin/hooks/shared/commit_rules.py. Used by hooks/write_checks.py (PreToolUse
Bash). The default rule: a commit message is one sentence, with no body, no AI attribution,
and no word naming security tooling, guards, scanners or bypasses. These checks cannot be
wrong about the text they read, so they may return a commit.

commit_message() reads the message from the command itself (-m/--message, -F/--file, a
`$(cat <<'EOF' ... EOF)` heredoc, --trailer lines). A message it cannot read (an editor commit,
--no-edit, quoting the shared shell reader refuses) is None: the gate then passes (fail-open).
A message the shell builds at run time ($(printf ..), backticks, $NAME) is flagged by
built_at_run_time(), and the gate asks for a literal message.
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

from shell.words import token_spans  # the reader every guard shares

GIT_COMMIT = re.compile(r"\bgit\b[^;&|\n]*?\bcommit(?![-\w])")
HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n(.*?)\n[ \t]*\2[ \t]*(?:\n|\)|$)", re.S)
SEPARATORS = {"&&", "||", ";", "|", "&"}
ATTRIBUTION = re.compile(r"co-authored-by\s*:|generated (?:with|by) \[?claude|noreply@anthropic\.com|"
                         r"\N{ROBOT FACE}", re.I)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
DEFAULT_DENY_WORDS = ("guard", "guards", "scanner", "scanners", "bypass", "bypassed", "bypasses",
                      "bypassing", "secret-scan", "secret scanning", "gitleaks", "trufflehog",
                      "semgrep", "sast", "--no-verify")


# Look-alikes folded before the attribution and word checks (1.4.1 U12): NFKC first (fullwidth
# letters, ligatures), then format characters (zero-width, soft hyphen, bidi marks) are dropped,
# dash-like characters become '-', and Cyrillic and Greek letters that look Latin become Latin,
# so "Cо‐Authored‐By:" (a Cyrillic о, U+2010 hyphens) is read as the line it imitates.
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−⁃﹘﹣－"), "-")
_LOOKALIKES = str.maketrans({**_DASHES, **dict(zip(
    map(ord, "АВЕКМНОРСТХаеорсухіјѕԁԛԝΑΒΕΖΗΙΚΜΝΟΡΤΥΧαοτυνκ"),
    "ABEKMHOPCTXaeopcyxijsdqwABEZHIKMNOPTYXaotuvk"))})


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf").translate(_LOOKALIKES)


def _heredoc_body(value: str) -> Optional[str]:
    match = HEREDOC.search(value)
    return match.group(3) if match else None


def _expands(typed: str) -> bool:
    """Whether a word, as typed, holds something the shell expands at run time: `$(..)`, a
    backtick, `$NAME` or `${..}` outside single quotes and not escaped."""
    i, quote = 0, None
    while i < len(typed):
        char = typed[i]
        if quote == "'":
            quote = None if char == "'" else quote
        elif char == "\\":
            i += 1
        elif char == "'" and quote is None:
            quote = "'"
        elif char == '"':
            quote = None if quote == '"' else '"'
        elif char == "`" or char == "$" and re.match(r"[({\w@*#?!-]", typed[i + 1:i + 2]):
            return True
        i += 1
    return False


def _read_message(command: str, cwd: Optional[str]) -> tuple[Optional[str], bool]:
    """(the message a `git commit` command records or None when it cannot be read, whether a
    message word is built at run time). Trailers (--trailer) are message lines too."""
    if not command or not GIT_COMMIT.search(command):
        return None, False
    spans = token_spans(command)
    if spans is None:
        return None, False
    tokens = [token for _, _, token in spans]
    start = next((i for i, tok in enumerate(tokens) if tok == "commit"
                  and any(os.path.basename(t) == "git" for t in tokens[:i])), None)
    if start is None:
        return None, False
    parts: list[str] = []
    runtime = False
    index = start + 1
    while index < len(tokens) and tokens[index] not in SEPARATORS:
        tok, nxt = tokens[index], tokens[index + 1] if index + 1 < len(tokens) else None
        value, path, trailer, at = None, None, False, index
        if tok in ("-m", "--message") or (tok.startswith("-") and not tok.startswith("--") and tok.endswith("m")):
            value, index, at = nxt, index + 1, index + 1
        elif tok.startswith("--message="):
            value = tok.split("=", 1)[1]
        elif tok.startswith("-m") and len(tok) > 2:
            value = tok[2:]
        elif tok in ("-F", "--file"):
            path, index, at = nxt, index + 1, index + 1
        elif tok.startswith("--file="):
            path = tok.split("=", 1)[1]
        elif tok == "--trailer":
            value, index, at, trailer = nxt, index + 1, index + 1, True
        elif tok.startswith("--trailer="):
            value, trailer = tok.split("=", 1)[1], True
        typed = command[spans[at][0]:spans[at][1]] if at < len(spans) else ""
        if value is not None:
            # A heredoc's body is the message; any other expansion is built at run time, unless
            # it was typed escaped or single-quoted, which is text.
            body = _heredoc_body(value) if value.lstrip().startswith("$(") else None
            if body is not None:
                parts.append(body)
            elif _expands(typed):
                runtime = True
            else:
                # git reads `token=value` in a --trailer as `token: value`
                parts.append(re.sub(r"^\s*([\w-]+)\s*=\s*", r"\1: ", value) if trailer else value)
        if path and path != "-":
            if _expands(typed):
                runtime = True
            else:
                try:
                    with open(os.path.join(cwd or os.getcwd(), os.path.expanduser(path)), encoding="utf-8") as handle:
                        parts.append(handle.read())
                except OSError:
                    return None, runtime
        index += 1
    return ("\n\n".join(parts) if parts else None), runtime


def commit_message(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """The message a `git commit` Bash command would record, or None when it cannot be read."""
    return _read_message(command, cwd)[0]


def built_at_run_time(command: str, cwd: Optional[str] = None) -> bool:
    """Whether a `git commit` command's message (-m, -F, --trailer) is built by the shell at run
    time ($(..), backticks, $NAME), so its text cannot be checked before it runs."""
    return _read_message(command, cwd)[1]


def problems(message: Optional[str], deny_words=DEFAULT_DENY_WORDS) -> list[str]:
    """What the message breaks, one short phrase each; [] when it is fine or unreadable."""
    if not message:
        return []
    lines = [line for line in message.splitlines() if line.strip() and not line.startswith("#")]
    folded = _fold(message)
    found = []
    if ATTRIBUTION.search(folded):
        found.append("it carries AI attribution")
    if len(lines) > 1:
        found.append("it has a body (more than one line)")
    elif lines and len(SENTENCE_BREAK.split(lines[0].strip())) > 1:
        found.append("it is more than one sentence")
    words = [w for w in deny_words if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", folded, re.I)]
    if words:
        found.append(f"it names {', '.join(repr(w) for w in words[:3])}")
    return found
