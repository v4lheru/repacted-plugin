#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/output_secret_scan.py
Summary: PostToolUse(Bash) hook that finds a secret VALUE in a command's output, replaces it
         before the model sees it, and tells the model not to repeat it. env_guard reads only
         a tool's input, so a script that opens a .env itself could still print a value.
Used by: hooks.json PostToolUse entry (dispatch_registry, matcher "Bash"). Tested by
         tests/test_output_secret_scan.py.

What it looks for, in every string of tool_response (stdout, stderr, any other field):
  - the literal values of the keys in the project's own .env files: every file env_guard
    protects (`.env`, `.env.*`, `*.env`, `.envrc`) at the git toplevel of the payload cwd
    (the cwd itself outside git) and up to two directories below it (apps/.env,
    apps/api/.env; not inside .git, node_modules or another SKIPPED_DIRS name, not through a
    symlink, never deeper),
    except names with an `example`, `sample` or `template` part. A value counts when it has 8 or
    more characters, is no placeholder, and either its key names a secret (KEY, TOKEN,
    SECRET, PASS, ...) or it looks opaque (three character classes, or two and 20+
    characters; never a bare URL or path). A URL's password counts on its own. Each value is
    also matched URL-encoded and base64-encoded (unpadded, standard and URL-safe alphabets);
  - the shared detector's secret classes (_repact_common.scrub_text with SECRET_ONLY_KINDS).

Channel (measured on Claude Code 2.1.288, the installed binary's hook output schema, and the
hooks reference at code.claude.com/docs/en/hooks): hookSpecificOutput.updatedToolOutput
"Replaces the tool output before it is sent to the model" and works for every tool, but the
host checks it against the tool's output schema and keeps the original on a mismatch. So the
rewrite keeps every key and type of tool_response and changes only string contents.
additionalContext carries the notice: the kind and source (a file name and key NAME, or a
detector class), never the value.

Values live only in this process's memory. Nothing is written to disk or the journal, and
stdout carries only the redacted output and the notice; stderr stays empty.

Limits: a value wrapped across lines is named in the notice but not replaced (no single span
holds it); a value cut by other characters, or encoded in another way (hex, inside a longer
base64 blob, a multi-line quoted value), is not seen; strings past MAX_SCAN_CHARS are left as
they are. Only Bash output is scanned: env_guard keeps .env files out of Read and Grep.

Fail-open: any error, odd stdin or a timeout prints nothing, so the original output stands.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import quote

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = PLUGIN_ROOT / "hooks"

MAX_SCAN_CHARS = 1_000_000
MAX_ENV_FILE_BYTES = 256 * 1024
MAX_ENV_FILES = 20
MAX_ENV_VALUES = 200
MIN_VALUE_CHARS = 8
GIT_TIMEOUT_S = 2
SKIPPED_PARTS = frozenset({"example", "sample", "template"})
MAX_SUBDIRS = 200
ENV_DEPTH = 2                       # root, apps/, apps/api/
# Dependency, build and tool directories: a .env there is a package's, not the project's.
SKIPPED_DIRS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", ".tox",
                          ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
                          "target", "vendor", ".next"})
ENV_MARKER = "[REDACTED:env-value]"

_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=(.*)$")
_SECRET_KEY = re.compile(r"KEY|TOKEN|SECRET|PASS|PWD|AUTH|CRED|PRIVATE|SALT|COOKIE|SESSION"
                         r"|DSN|WEBHOOK|SIGNING", re.I)
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_URL_PASSWORD = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]*:([^/\s@]+)@")
_MARKER = re.compile(r"\[REDACTED:([a-z0-9-]+)\]")
_LINE_BREAKS = re.compile(r"\r?\n")


def _env_value(raw: str) -> str:
    """The value of a KEY=<raw> line: inside matching quotes, else up to an inline comment."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def _opaque(value: str) -> bool:
    if any(char.isspace() for char in value) or _URL.match(value) or value[0] in "/~.":
        return False
    classes = sum(1 for test in (str.islower, str.isupper, str.isdigit)
                  if any(test(char) for char in value))
    return classes >= 3 or (classes == 2 and len(value) >= 20)


def _worth_matching(key: str, value: str, is_placeholder) -> bool:
    if len(value) < MIN_VALUE_CHARS or is_placeholder(value):
        return False
    return bool(_SECRET_KEY.search(key)) or _opaque(value)


def project_root(cwd: str) -> Path | None:
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        done = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        top = done.stdout.strip()
        if done.returncode == 0 and top and os.path.isdir(top):
            return Path(top)
    except (OSError, subprocess.SubprocessError):
        pass
    return Path(cwd)


def _sorted_entries(directory: str) -> list:
    try:
        with os.scandir(directory) as entries:
            return sorted(entries, key=lambda entry: entry.name)
    except OSError:
        return []


def env_files(root: Path) -> list[Path]:
    """The .env-class files at root and up to ENV_DEPTH directories below it (apps/api/.env),
    by env_guard's own predicate: prod.env, .envrc and .env.local count, a template
    (.env.example) does not. .netrc holds no KEY=value lines. Shallower files come first. A
    subdirectory is read only when it is a real directory (not a symlink) outside
    SKIPPED_DIRS, at most MAX_SUBDIRS of them, level by level in name order."""
    if str(HOOKS_DIR) not in sys.path:
        sys.path.insert(0, str(HOOKS_DIR))
    from env_guard import file_is_protected

    def env_class(entry) -> bool:
        name = entry.name
        if name.casefold() == ".netrc" or not file_is_protected(name, entry.path):
            return False
        if SKIPPED_PARTS & {part.lower() for part in name.split(".")[1:]}:
            return False
        try:
            return entry.is_file()
        except OSError:
            return False

    found, level, read = [], [str(root)], 0
    for _depth in range(ENV_DEPTH + 1):
        below = []
        for directory in level:
            if read >= MAX_SUBDIRS + 1 or len(found) >= MAX_ENV_FILES:
                return found[:MAX_ENV_FILES]
            read += 1
            for entry in _sorted_entries(directory):
                if env_class(entry):
                    found.append(Path(entry.path))
                    continue
                try:
                    if entry.name not in SKIPPED_DIRS and entry.is_dir(follow_symlinks=False):
                        below.append(entry.path)
                except OSError:
                    continue
        level = below
    return found[:MAX_ENV_FILES]


def env_values(root: Path, is_placeholder) -> dict[str, str]:
    """{matchable form: "KEY from <file name>"} for the project's .env files. Unreadable files
    are skipped. The first file and line that name a form win."""
    forms: dict[str, str] = {}
    for path in env_files(root):
        try:
            with open(path, "rb") as handle:
                text = handle.read(MAX_ENV_FILE_BYTES).decode("utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = _ENV_LINE.match(line)
            if not match:
                continue
            key, value = match.group(1), _env_value(match.group(2))
            label = f"{key} from {path.relative_to(root).as_posix()}"
            candidates = [value]
            password = _URL_PASSWORD.match(value)
            if password:
                candidates.append(password.group(1))
            for candidate in candidates:
                if len(forms) >= 4 * MAX_ENV_VALUES:
                    return forms
                if _worth_matching(key, candidate, is_placeholder):
                    for form in _encodings(candidate):
                        forms.setdefault(form, label)
    return forms


def _encodings(value: str) -> list[str]:
    raw = value.encode("utf-8")
    return [value, quote(value, safe=""),
            base64.b64encode(raw).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")]


class Scanner:
    """Rewrites strings within one scan budget and records what it found (labels only)."""

    def __init__(self, forms: dict[str, str]):
        from shared.scrub import SECRET_ONLY_KINDS, scrub_text

        self.forms = forms
        self.regex = (re.compile("|".join(map(re.escape, sorted(forms, key=len, reverse=True))))
                      if forms else None)
        self.scrub = lambda text: scrub_text(text, kinds=SECRET_ONLY_KINDS)
        self.budget = MAX_SCAN_CHARS
        self.replaced: dict[str, None] = {}
        self.split: dict[str, None] = {}

    def text(self, value: str) -> str:
        if self.budget <= 0 or not value:
            return value
        head, tail = value[:self.budget], value[self.budget:]
        self.budget -= len(head)
        if self.regex is not None:
            head = self.regex.sub(self._env_hit, head)
        scrubbed = self.scrub(head)
        if scrubbed != head:
            new = Counter(_MARKER.findall(scrubbed)) - Counter(_MARKER.findall(head))
            for kind in sorted(new):
                self.replaced.setdefault(f"a {kind} value", None)
            head = scrubbed
        if self.regex is not None and "\n" in head:
            for match in self.regex.finditer(_LINE_BREAKS.sub("", head)):
                self.split.setdefault(self.forms[match.group(0)], None)
        return head + tail

    def _env_hit(self, match: re.Match) -> str:
        self.replaced.setdefault(f"the value of {self.forms[match.group(0)]}", None)
        return ENV_MARKER

    def walk(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.walk(item) for item in value]
        return value


def notice(replaced: list[str], split: list[str]) -> str:
    parts = []
    if replaced:
        parts.append(f"This Bash output carried a secret ({'; '.join(replaced[:6])}). It was "
                     "replaced with [REDACTED:<kind>] before it reached you.")
    if split:
        parts.append(f"A secret value split across lines ({'; '.join(split[:6])}) could not be "
                     "replaced and may still be visible above.")
    parts.append("Do not repeat, quote, log or send it, and do not try to recover it. To hand a "
                 "value to a command, use `env-get FILE KEY -- <cmd>` and keep the command "
                 "from printing it.")
    return "Secret-output check: " + " ".join(parts)


def check(payload: dict) -> dict | None:
    if payload.get("tool_name") != "Bash":
        return None
    response = payload.get("tool_response")
    if not isinstance(response, dict) or not response:
        return None
    from shared.scrub_values import is_placeholder_value

    cwd = payload.get("cwd")
    root = project_root(cwd) if isinstance(cwd, str) else None
    forms = env_values(root, is_placeholder_value) if root is not None else {}
    scanner = Scanner(forms)
    rewritten = scanner.walk(response)
    split = list(scanner.split)
    if not scanner.replaced and not split:
        return None
    output: dict = {"hookEventName": "PostToolUse",
                    "additionalContext": notice(list(scanner.replaced), split)}
    if rewritten != response:
        output["updatedToolOutput"] = rewritten
    return {"hookSpecificOutput": output}


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read() or b"null")
        result = check(payload) if isinstance(payload, dict) else None
        if result is not None:
            sys.stdout.write(json.dumps(result))
    except Exception:  # noqa: BLE001 - fail-open: the original output stands
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
