#!/usr/bin/env python3
"""
Location: pact-plugin/hooks/egress_guard.py
Summary: PreToolUse(mcp__.*|WebFetch|Artifact|Bash) hook that denies a credential on its way
         off the machine (decision D29: the machine is the boundary; local files, transcripts
         and model context are inside it, anything a tool sends elsewhere is not).
           mcp__*    every string in the tool input (keys included, JSON-in-a-string parsed and
                     escaped newlines unescaped), since the MCP server forwards all of it.
           WebFetch  the url, percent-decoded (the url and query go to an arbitrary host).
           Artifact  the strings in the input, plus the text of every file it publishes or
                     uploads (file_path, file_paths): the page goes to claude.ai.
           Bash      only commands that run a network or publish client (curl, wget, http, gh,
                     glab, ssh, scp, rsync, nc, pbcopy, osascript). Then the WHOLE command is
                     scanned, because the secret often sits in another segment of the pipe
                     (`printf ... | pbcopy`), and so is every local file the client uploads by
                     path (curl -d @f / -F k=@f / -T f, scp and rsync sources, `< f`; see
                     egress_uploads.py). A file that cannot be read fails closed. git push is
                     handled by the git hooks (task 225).
         Loopback is inside the boundary: a curl/wget/http command whose every URL in the
         text and every target its clients are aimed at (scheme-less `localhost:3000/api` too,
         read as the shell passes it) is localhost/127.x/::1, and a local-browser MCP call
         (playwright) whose every URL is loopback, are not egress. A proxy or redirecting
         option, a *proxy= variable, a config or URL-list file, or xargs feeding the client
         makes the target unknown, so the command is scanned. A local-browser SCRIPT (browser_evaluate) that names no
         remote URL and calls no network API (fetch, XMLHttpRequest, sendBeacon, WebSocket,
         navigation) runs in the local browser, so it is not egress either (the measured
         test-cookie false positives, the hardening pass). A URL held in a variable ($U) is unknown, so
         it is scanned.
         The detector is the shared one (_repact_common.secret_kind: every secret class the KB
         scrubber uses), so a new pattern there reaches this guard with no copy here.
         A deny names the KIND only, never the value, and points at env-get: a value passed
         through `env-get FILE KEY -- <cmd>` never appears in tool input, so that path works.
Used by: hooks.json PreToolUse entry. Tested by tests/test_egress_guard.py.

Team policy (.repacted/policy.json, shared/team_policy.py): `egress.allow_pragma: false` makes
every pragma line scanned, and `egress.network_programs` adds programs whose commands are
scanned like curl's (the whole command; their upload arguments are not parsed). A deny then
names the policy file and the key.

Fail-closed for egress only: if the detector raises on an egress input, the call is denied.
A Bash command with no network client never loads the detector at all.

Honest limit: it reads what the tool call carries plus the files a client names for upload. A
value built at run time (`-d "$(cat .env)"`), a file a script opens itself, and binary files
are not visible here. A cookie a local browser script sets reaches whatever origin the page is
on, which the input does not name.

Output: deny = the PreToolUse deny envelope on stdout, the reason on stderr, exit 2;
        allow = {"suppressOutput": true}, exit 0.
"""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import egress_uploads
from shell_words import segments, strip_prefix

_ALLOW_OUTPUT = json.dumps({"suppressOutput": True})

NETWORK_PROGRAMS = frozenset({
    "curl", "wget", "http", "https", "gh", "glab", "ssh", "scp", "rsync", "nc", "ncat",
    "netcat", "pbcopy", "osascript", "xh", "xhs", "curlie",
})
# Clients whose target is a URL on the command line: aimed only at loopback, nothing leaves.
URL_CLIENTS = frozenset({"curl", "wget", "http", "https", "xh", "xhs", "curlie"})
# MCP servers that are a browser on this machine: loopback pages stay local too.
LOCAL_BROWSER_SERVERS = ("plugin_playwright_playwright", "Claude_Browser")
BROWSER_SCRIPT_TOOLS = ("browser_evaluate", "browser_run_code_unsafe")
# What a browser script calls to reach the network or leave the page (a remote URL is checked
# separately): any of these makes the script egress.
BROWSER_NETWORK_CALLS = ("fetch(", "XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource",
                         "location", ".href", "window.open", ".submit(", "import(", "goto(",
                         "request.", ".src")
ARTIFACT_FILE_KEYS = ("file_path", "file_paths")
MAX_FILE_BYTES = 16 * 1024 * 1024
# The escape shared with the git commit/push scan (task 225): a line carrying this pragma is
# skipped, and every skip is counted in the hook's output.
PRAGMA = "pragma: allowlist secret"
FIX = ("pass a secret through `env-get FILE KEY -- <cmd>` so it never appears in tool input, "
       "or ask the user to enter it themselves")


def shared_detector():
    """shared.scrub.secret_kind, imported on first use (the Bash fast path never needs it)."""
    from shared.scrub import secret_kind
    return secret_kind


def detect(text: str):
    return shared_detector()(text)


def unescaped(text: str) -> str:
    """A string that carries escaped JSON or code: turn \\n, \\t and \\" back into what they
    stand for, so line-anchored classes see real lines."""
    if "\\" not in text:
        return text
    return text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\'", "'")


def strings_in(value):
    """Every string in a JSON-shaped value, keys included. A string that is itself JSON is
    parsed and walked too (n8n workflows travel as JSON text)."""
    if isinstance(value, str):
        yield value
        stripped = value.strip()
        if stripped[:1] in ("{", "[") and len(stripped) > 1:
            try:
                parsed = json.loads(stripped)
            except ValueError:
                return
            yield from strings_in(parsed)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from strings_in(item)


def without_pragma_lines(text: str, skipped: list, allow_pragma: bool = True) -> str:
    """The text without the lines its pragma covers (shared.scrub.pragma_covers: one secret
    value per pragma line), each counted; unchanged when a team policy set egress.allow_pragma
    to false (every line is then scanned)."""
    if not allow_pragma or PRAGMA not in text:
        return text
    from shared.scrub import pragma_covers
    kept = []
    for line in text.split("\n"):
        if PRAGMA in line and pragma_covers(line, PRAGMA):
            skipped.append(1)
        else:
            kept.append(line)
    return "\n".join(kept)


def first_kind(texts, skipped: list | None = None, allow_pragma: bool = True) -> str | None:
    skipped = [] if skipped is None else skipped
    for text in texts:
        for candidate in (without_pragma_lines(text, skipped, allow_pragma),
                          without_pragma_lines(unescaped(text), [], allow_pragma)):
            kind = detect(candidate)
            if kind:
                return kind
    return None


def file_text(path: str, cwd: str) -> str:
    """The text of a file the tool is about to publish, or "" when it is missing, binary or huge."""
    full = Path(cwd, os.path.expanduser(path))
    try:
        if not full.is_file() or full.stat().st_size > MAX_FILE_BYTES:
            return ""
        data = full.read_bytes()
    except OSError:
        return ""
    if b"\x00" in data[:8192]:
        return ""
    return data.decode("utf-8", errors="ignore")


def artifact_texts(tool_input: dict, cwd: str):
    yield from strings_in(tool_input)
    for key in ARTIFACT_FILE_KEYS:
        paths = tool_input.get(key)
        for path in ([paths] if isinstance(paths, str) else paths or []):
            if isinstance(path, str):
                yield file_text(path, cwd)


def network_clients(parts: list[list[str]], extra: frozenset = frozenset()) -> set[str]:
    """The network programs the command runs; `extra` adds a team policy's programs."""
    programs = NETWORK_PROGRAMS | extra
    found = set()
    for words in parts:
        command = strip_prefix(words)
        # find -exec runs the command after it, as a runner word runs the next one.
        runs = [command] + [strip_prefix(words[i + 1:]) for i, word in enumerate(words[:-1])
                            if word in egress_uploads.FIND_EXEC]
        found |= {run[0].rsplit("/", 1)[-1] for run in runs
                  if run and run[0].rsplit("/", 1)[-1] in programs}
        # A client run by a word the shared runner table does not know (strace -f curl,
        # proxychains curl): egress_uploads reads it from the client word on.
        found |= {word.split("=", 1)[-1].rsplit("/", 1)[-1] for word in words
                  if word.split("=", 1)[-1].rsplit("/", 1)[-1] in programs}
    return found


def runs_network_client(parts: list[list[str]]) -> bool:
    return bool(network_clients(parts))


def _is_loopback(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def url_hosts(text: str) -> list[str]:
    """The host of every scheme://... URL in the text (quotes and escapes stripped)."""
    hosts = []
    for word in text.replace("\\", " ").replace('"', " ").replace("'", " ").split():
        start = word.find("://")
        if start <= 0:
            continue
        begin = start
        while begin > 0 and (word[begin - 1].isalnum() or word[begin - 1] in "+.-"):
            begin -= 1                                  # DATABASE_URL=postgresql://... -> postgresql://
        try:
            host = urlsplit(word[begin:]).hostname
        except ValueError:
            host = None
        hosts.append(host or "")
    return hosts


def only_loopback(text: str) -> bool:
    """True when the text names at least one URL and every URL points at this machine."""
    hosts = url_hosts(text)
    return bool(hosts) and all(_is_loopback(host) for host in hosts)


def target_host(word: str) -> str:
    """The host a URL client sends to for one target word: a scheme URL, curl/wget's
    scheme-less `host[:port][/path]` (default http), or httpie's `:port/path` (localhost)."""
    if word.startswith(":"):
        word = "localhost" + word                       # :3000@host still goes to host
    try:
        return urlsplit(word if "://" in word else "http://" + word).hostname or ""
    except ValueError:
        return ""


def bash_only_loopback(command: str) -> bool:
    """True when every URL in the command text, and every target its URL clients are aimed at
    (read as the shell passes them, so `http://localhost'@'host` is host), is this machine."""
    if not all(_is_loopback(host) for host in url_hosts(command)):
        return False
    try:
        targets = egress_uploads.url_targets(command, URL_CLIENTS)
    except Exception:   # noqa: BLE001 - a reader failure means the targets are unknown: scan
        return False
    return targets is not None and all(_is_loopback(target_host(word)) for word in targets)


def local_browser_script(tool: str, tool_input: dict) -> bool:
    """A local-browser script that names no remote URL and calls no network API: it runs in the
    browser on this machine, so what it carries stays here."""
    parts = tool.split("__")
    if len(parts) < 3 or parts[1] not in LOCAL_BROWSER_SERVERS or parts[2] not in BROWSER_SCRIPT_TOOLS:
        return False
    text = json.dumps(tool_input)
    if any(not _is_loopback(host) for host in url_hosts(text)):
        return False
    return not any(call in text for call in BROWSER_NETWORK_CALLS)


def uploaded_texts(command: str, cwd: str) -> list[tuple[str, str]]:
    """(path as written, text) for every local file a network client in the command uploads by
    path. Raises egress_uploads.Unreadable when one cannot be read (the caller fails closed)."""
    found: list[tuple[str, str]] = []
    for path, here, excludes in egress_uploads.upload_paths(command, cwd, NETWORK_PROGRAMS):
        found += [(path, text) for text in egress_uploads.file_texts(path, here, excludes)]
    return found


def bash_scan_text(command: str, parts: list[list[str]]) -> str:
    """What to scan once a client runs: the whole command, since pipes, heredocs and
    substitutions carry the data from one segment into the client."""
    return command


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}


def _found(where: str, kind: str) -> dict:
    return _deny(f"Blocked: this {where} carries a credential ({kind}) and would send it off "
                 f"this machine; {FIX}.")


def policy_settings(cwd: str) -> tuple[bool, frozenset]:
    """(egress.allow_pragma, egress.network_programs) after the work tree's team policy, which
    can only switch the pragma off and add programs. A policy that cannot be read changes
    nothing: (True, frozenset())."""
    try:
        from shared import team_policy
        return team_policy.egress_settings(cwd)
    except Exception:   # noqa: BLE001 - the guard's own rules still run in full
        return True, frozenset()


def decide(payload: dict) -> dict | None:
    """The hook's output for one payload: a deny object, or None to allow. A deny names the
    team policy when the policy changed this guard (shared.team_policy.refusal_note)."""
    output = _decide(payload)
    specific = (output or {}).get("hookSpecificOutput", {})
    if specific.get("permissionDecision") == "deny":
        try:
            from shared import team_policy
            specific["permissionDecisionReason"] += team_policy.refusal_note(("egress.",),
                                                                            payload.get("cwd"))
        except Exception:   # noqa: BLE001 - the deny stands without its note
            pass
    return output


def _decide(payload: dict) -> dict | None:
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool, str) or not isinstance(tool_input, dict):
        return None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else os.getcwd()
    allow_pragma, extra_programs = policy_settings(cwd)
    files: list[tuple[str, str]] = []
    if tool == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str):
            return None
        parts = segments(command)
        clients = network_clients(parts, extra_programs)
        if not clients or (clients <= URL_CLIENTS and bash_only_loopback(command)):
            return None
        try:
            files = uploaded_texts(command, cwd)
        except egress_uploads.Unreadable as error:
            return _deny(f"Blocked: this network command uploads {error.path}, which could not be "
                         f"checked for credentials ({error.why}); name a readable file, or {FIX}.")
        except Exception as error:   # noqa: BLE001 - fail closed: this command is leaving the machine
            return _deny(f"Blocked: the upload check on this network command failed "
                         f"({type(error).__name__}), so it was not sent; {FIX}.")
        where, texts = "network command", [bash_scan_text(command, parts)]
    elif tool.startswith("mcp__"):
        if tool.split("__")[1] in LOCAL_BROWSER_SERVERS and (
                only_loopback(json.dumps(tool_input)) or local_browser_script(tool, tool_input)):
            return None
        where, texts = f"{tool} input", strings_in(tool_input)
    elif tool == "WebFetch":
        url = tool_input.get("url")
        if not isinstance(url, str):
            return None
        where, texts = "WebFetch url", [url, unquote(url)]
    elif tool.startswith("Artifact"):   # ArtifactData, ArtifactComments: the same egress
        where = "Artifact publish" if tool == "Artifact" else f"{tool} input"
        texts = artifact_texts(tool_input, cwd)
    else:
        return None
    skipped: list = []
    try:
        kind = first_kind(texts, skipped, allow_pragma)
    except Exception as error:   # noqa: BLE001 - fail closed: this input is leaving the machine
        return _deny(f"Blocked: the credential check on this {where} failed "
                     f"({type(error).__name__}), so it was not sent; {FIX}.")
    if kind:
        return _found(where, kind)
    for path, text in files:
        try:
            kind = first_kind([text], skipped, allow_pragma)
        except Exception as error:   # noqa: BLE001 - fail closed, as above
            return _deny(f"Blocked: the credential check on {path} failed ({type(error).__name__}), "
                         f"so it was not uploaded; {FIX}.")
        if kind:
            return _deny(f"Blocked: this network command sends {path} or text read from it; the "
                         f"file carries a credential ({kind}) and the check reads the whole file, "
                         f"not the lines a filter selects; {FIX}.")
    if skipped:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                f"egress_guard: {len(skipped)} line(s) skipped by `{PRAGMA}` in this {where}."}}
    return None


def main() -> int:
    """Deny: the envelope on stdout, the reason on stderr, exit 2. Allow: exit 0. A payload
    that is not JSON names no tool, so it is allowed."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        print(_ALLOW_OUTPUT)
        return 0
    output = decide(payload) if isinstance(payload, dict) else None
    if not output:
        print(_ALLOW_OUTPUT)
        return 0
    print(json.dumps(output))
    specific = output["hookSpecificOutput"]
    if specific.get("permissionDecision") != "deny":
        return 0                                        # a counted pragma skip: allowed
    print(specific["permissionDecisionReason"], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
