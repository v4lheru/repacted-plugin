"""
Location: pact-plugin/hooks/shell/merge/braces.py
Summary: Brace expansion as bash and zsh do it before a command runs: an unquoted `{a,b}`
         list or `{1..3}` / `{a..c}` sequence is spelled out, so `git push origin {main,x}`
         reads as the `git push origin main x` the shell runs. Quoted and escaped text,
         `${name}` and a brace pair with no comma and no sequence (`stash@{0}`) stay as typed.
Used by: hooks/shell/merge/classify.py (unmintable_danger judges the expanded command, and
         refuses a git/gh/glab leg whose braces this module cannot spell out).
Map: MAX_WORDS, MAX_DEPTH, _SEQUENCE_RE, _BREAKS, Unexpandable, _opaque, _sequence, _list_at, _expand_word, _spelled, expand_braces.
"""

from __future__ import annotations

import re

from shell.words import leg_token_spans, mask_quotes

MAX_WORDS = 64                  # words one typed word may expand to
MAX_DEPTH = 8                   # lists inside lists
_SEQUENCE_RE = re.compile(r"(-?\d+|[A-Za-z])\.\.(-?\d+|[A-Za-z])(?:\.\.(-?\d+))?")
_BREAKS = ";|&<>()`"            # an unquoted one inside a list: the shell's reading is not modelled


class Unexpandable(ValueError):
    """A brace list this module cannot spell out: too many words, too deep, or broken by an
    operator or a substitution the shell reads before the braces."""


def _opaque(word: str) -> "list[bool]":
    """Per character of `word` (one whitespace-delimited token): True when it is quoted or
    escaped, so it opens, closes and separates nothing."""
    masked = mask_quotes(word)
    opaque = [masked[k] != word[k] or word[k].isspace() for k in range(len(word))]
    k = 0
    while k < len(word):
        if word[k] == "\\" and not opaque[k]:
            opaque[k:k + 2] = [True] * len(opaque[k:k + 2])
            k += 2
        else:
            k += 1
    return opaque


def _sequence(text: str) -> "list[str] | None":
    """The words of a `{x..y}` or `{x..y..step}` sequence, or None when `text` is not one."""
    match = _SEQUENCE_RE.fullmatch(text)
    if match is None:
        return None
    first, last, step = match.groups()
    step = abs(int(step or 1)) or 1
    if first.lstrip("-").isdigit() != last.lstrip("-").isdigit():
        return None
    numeric = first.lstrip("-").isdigit()
    low, high = (int(first), int(last)) if numeric else (ord(first), ord(last))
    if abs(high - low) // step + 1 > MAX_WORDS:
        raise Unexpandable(text)
    values = range(low, high + (1 if high >= low else -1), step if high >= low else -step)
    if not numeric:
        return [chr(value) for value in values]
    padded = any(len(side.lstrip("-")) > 1 and side.lstrip("-")[0] == "0" for side in (first, last))
    width = max(len(first), len(last)) if padded else 0
    return [("-" if value < 0 else "") + str(abs(value)).zfill(width - (value < 0)) for value in values]


def _list_at(word: str, opaque: "list[bool]", i: int) -> "tuple[int, list[tuple[int, int]]] | None":
    """For the unquoted `{` at word[i]: (index of its `}`, the (start, end) of each alternative,
    none for a pair that holds no top-level comma), or None when the brace is never closed."""
    depth, cuts, j = 0, [i], i + 1
    while j < len(word):
        char = "" if opaque[j] else word[j]
        if char == "$" and word[j + 1:j + 2] == "{" and not opaque[j + 1]:
            close = word.find("}", j)                   # ${name}: its braces are not a list
            if close < 0:
                return None
            j = close
        elif char and char in _BREAKS:
            raise Unexpandable(word)
        elif char == "{":
            depth += 1
        elif char == "}":
            if not depth:
                cuts.append(j)
                return j, ([(cuts[k] + 1, cuts[k + 1]) for k in range(len(cuts) - 1)]
                           if len(cuts) > 2 else [])
            depth -= 1
        elif char == "," and not depth:
            cuts.append(j)
        j += 1
    return None


def _expand_word(word: str, opaque: "list[bool]", depth: int = 0) -> "list[str]":
    """Every word the shell makes of one typed word, in the shell's order."""
    if depth > MAX_DEPTH:
        raise Unexpandable(word)
    for i, char in enumerate(word):
        if char != "{" or opaque[i] or word[i - 1:i] == "$" and not opaque[i - 1]:
            continue
        found = _list_at(word, opaque, i)
        if found is None:
            continue
        close, parts = found
        heads = [head for start, end in parts
                 for head in _expand_word(word[start:end], opaque[start:end], depth + 1)]
        if not parts:
            heads = None if any(opaque[i:close]) else _sequence(word[i + 1:close])
            if heads is None:
                continue                                # `{x}`, `@{0}`: as typed
        tails = _expand_word(word[close + 1:], opaque[close + 1:], depth + 1)
        if len(heads) * len(tails) > MAX_WORDS:
            raise Unexpandable(word)
        return [word[:i] + head + tail for head in heads for tail in tails]
    return [word]


def _spelled(word: str) -> "list[str]":
    """The non-empty words `word` expands to. Unexpandable when the words do not read back as
    themselves (a quote left open in `word` would pair with its own copy)."""
    made = [piece for piece in _expand_word(word, _opaque(word)) if piece]
    joined = " ".join(made)
    if made != [word] and ([joined[start:end] for start, end in leg_token_spans(joined)] != made
                           or any(_expand_word(piece, _opaque(piece)) != [piece] for piece in made)):
        raise Unexpandable(word)
    return made


def expand_braces(text: str) -> "tuple[str, bool]":
    """(`text` with each brace list and sequence spelled out, True when some word holds
    braces that could not be spelled out and is left as typed). A word that expands to
    nothing is dropped, as the shell drops it; text with no list comes back unchanged."""
    if "{" not in text:
        return text, False
    out, last, unread = [], 0, False
    for start, end in leg_token_spans(text):
        word = text[start:end]
        if "{" not in word:
            continue
        try:
            made = _spelled(word)
        except Unexpandable:
            unread = True
            continue
        if made != [word]:
            out += [text[last:start], " ".join(made)]
            last = end
    return "".join(out) + text[last:], unread
