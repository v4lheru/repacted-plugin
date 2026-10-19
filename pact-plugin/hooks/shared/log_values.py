"""
Location: pact-plugin/hooks/shared/log_values.py
Summary: Whether a print/console.log call on an added line may log a credential's value: a code
    word naming a token, password, secret or api key (identifier, f-string or template placeholder,
    lookup key) is a value; prose naming one (a prompt such as 'Enter password: ') is not.
Used by: hooks/git_commit_check.py (check_security, on the added lines of a commit). Pure: re only.
"""

from __future__ import annotations

import re

_LOG_CALL = re.compile(r'\b(?:print|console\.log)\s*\(', re.IGNORECASE)
_WORD = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
_KEY_LOOKUP_BEFORE = re.compile(r'(?:\[|(?:getenv|\.get|environ)\s*\()\s*$')
# Words that name a count or tokenizer tooling, never a credential. Every other word
# containing "token" is treated as a value, including plurals such as access_tokens.
_TOKEN_EXEMPT = re.compile(
    r'^(?:max|num|count|input|output|total|prompt|completion|cached|used|remaining'
    r'|cache_read|cache_creation)_tokens$'
    r'|^token_(?:count|len|limit|budget|usage)$|^\w*tokeniz\w*$')
_PROSE_TOKEN = re.compile(r'\btoken\b', re.IGNORECASE)


def _placeholders(body: str, opener: str) -> list:
    """The code inside `{...}` (f-string) or `${...}` (template literal) placeholders."""
    out, depth, start, i = [], 0, None, 0
    while i < len(body):
        if depth == 0 and body.startswith(opener, i):
            depth, start = 1, i + len(opener)
            i = start
            continue
        if depth and body[i] == "{":
            depth += 1
        elif depth and body[i] == "}":
            depth -= 1
            if depth == 0:
                out.append(body[start:i])
        i += 1
    return out


def _without_placeholders(body: str, opener: str) -> str:
    for code in _placeholders(body, opener):
        body = body.replace(opener + code + "}", " ", 1)
    return body


def _split_call(text: str) -> tuple:
    """(code_words, prose) for the arguments in `text`.

    Code words are the identifiers the code reads, including f-string and template
    placeholders. A string literal with no whitespace is a key and its words count
    as code (`getattr(creds, 'access_token')`, `cfg['api_token']`); so does a lookup
    key. A string with whitespace is prose and is returned separately.
    """
    words, prose, i, code_start = [], [], 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "'\"`":
            prefix = re.search(r'([A-Za-z]{0,2})$', text[code_start:i]).group(1).lower()
            words += _WORD.findall(text[code_start:i - len(prefix)])
            end = i + 1
            while end < len(text) and text[end] != ch:
                end += 2 if text[end] == "\\" else 1
            body = text[i + 1:end]
            opener = "${" if ch == "`" else ("{" if "f" in prefix else None)
            literal = body
            if opener:
                for code in _placeholders(body, opener):
                    code_words, code_prose = _split_call(code)
                    words += code_words
                    prose += code_prose
                literal = _without_placeholders(body, opener)
            if (literal.strip() and not re.search(r'\s', literal.strip())) \
                    or _KEY_LOOKUP_BEFORE.search(text[:i - len(prefix)]):
                words += _WORD.findall(literal)
            else:
                prose.append(literal)
            i = code_start = end + 1
            continue
        i += 1
    words += _WORD.findall(text[code_start:])
    return words, prose


def _snake(word: str) -> str:
    return re.sub(r'(?<=[a-z0-9])([A-Z])', r'_\1', word).lower()


def _exempt(word: str) -> bool:
    return bool(_TOKEN_EXEMPT.match(_snake(word)))


def logs_token_value(line: str) -> bool:
    """True when a print/console.log call on this line may log a token's value: a code word
    containing "token" not in _TOKEN_EXEMPT, or prose naming "token" beside any non-exempt code
    value. Rules in the notes."""
    for call in _LOG_CALL.finditer(line):
        words, prose = _split_call(line[call.end():])
        values = [w for w in words if not _exempt(w)]
        if any("token" in w.lower() for w in values):
            return True
        if values and any(_PROSE_TOKEN.search(p) for p in prose):
            return True
    return False



# A code word naming a password, a secret or an api key (read as snake_case), unless it names a
# property of one (its length, policy, field, label, name, path, ...) rather than the value.
_CREDENTIAL_WORD = re.compile(r'passw(?:or)?d|secret|api_?key')
_CREDENTIAL_EXEMPT = re.compile(
    r'_(?:length|len|min|max|policy|field|label|prompt|required|hint|rules?|strength|count'
    r'|names?|paths?|ids?|file|manager)$')


def _credential_value(word: str) -> bool:
    snake = _snake(word)
    return bool(_CREDENTIAL_WORD.search(snake)) and not _CREDENTIAL_EXEMPT.search(snake)


def logs_secret_value(line: str) -> bool:
    """True when a print/console.log call on this line may log a credential's value.

    Password, secret and api key: a code word (identifier, placeholder or lookup key) that names
    one is a value; prose naming one (a prompt such as 'Enter password: ') is not. Token keeps its own
    rule, logs_token_value."""
    if logs_token_value(line):
        return True
    for call in _LOG_CALL.finditer(line):
        words, _ = _split_call(line[call.end():])
        if any(_credential_value(word) for word in words):
            return True
    return False
