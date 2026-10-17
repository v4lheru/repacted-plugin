"""
Location: pact-plugin/hooks/shared/key_file.py
Summary: Parse KEY=value entries of a key file without shell evaluation and never expose a
         value: parse_key_file, read_key, is_world_readable (refuses a world-readable file).
Used by: bin/env-get (via the bin/_repact_common shim).
Map (file order): KEY_LINE_RE, KeyFileError, is_world_readable, _unquote, parse_key_file, read_key.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Iterable, Optional


KEY_LINE_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


class KeyFileError(ValueError):
    """A key file is missing, unreadable, too open, or lacks a key.

    Messages name the file and the key, never a value.
    """


def is_world_readable(path: Path) -> bool:
    """True when the file's mode grants read permission to 'other'."""
    return bool(os.stat(path).st_mode & stat.S_IROTH)


def _unquote(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_key_file(path: os.PathLike | str, keys: Optional[Iterable[str]] = None,
                   refuse_world_readable: bool = True) -> dict[str, str]:
    """Parse KEY=value lines from a dotenv-style file without evaluating it.

    Only entries matching ``^[A-Z_][A-Z0-9_]*=`` count. There is no shell expansion, no
    ``export`` handling (an ``export KEY=`` line is ignored), and matching outer quotes
    are stripped. The last occurrence of a key wins.

    Args:
        path: the key file.
        keys: when given, only these keys are returned, and a missing one raises.
        refuse_world_readable: raise when the file is readable by 'other'.

    Returns:
        {key: value} for the requested keys (or every parsed key when keys is None).

    Raises:
        KeyFileError: the file is missing/unreadable, too open, or lacks a key.
    """
    file_path = Path(path).expanduser()
    try:
        if refuse_world_readable and is_world_readable(file_path):
            raise KeyFileError(f"{file_path}: file is world-readable; run chmod 600 on it")
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KeyFileError(f"{file_path}: cannot read key file ({exc.__class__.__name__})") from None
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        match = KEY_LINE_RE.match(line)
        if match:
            parsed[match.group(1)] = _unquote(match.group(2))
    if keys is None:
        return parsed
    wanted = list(keys)
    missing = [key for key in wanted if not parsed.get(key)]
    if missing:
        raise KeyFileError(f"{file_path}: key(s) not set: {', '.join(missing)}")
    return {key: parsed[key] for key in wanted}


def read_key(path: os.PathLike | str, key: str, refuse_world_readable: bool = True) -> str:
    """Return one key's value from a key file (see parse_key_file for the rules)."""
    return parse_key_file(path, [key], refuse_world_readable)[key]


