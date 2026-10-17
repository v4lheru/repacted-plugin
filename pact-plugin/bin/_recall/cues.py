"""Moment cues (D9.2): what a tool call offers T1 and T2 to match on.

Location: pact-plugin/bin/_recall/cues.py. Used by dispatch.handle and bin/recall.
Pure functions over (tool_name, tool_input); no store access.
"""
from __future__ import annotations

import os
import re
from typing import Optional
from urllib.parse import urlparse

from . import GENERIC_PATH_PARTS, tokens

T1_TOOLS = frozenset(("Bash", "Edit", "Write", "WebFetch"))
T2_TOOLS = frozenset(("Read", "Edit", "Write", "Bash"))
URL = re.compile(r"https?://[^\s'\"`<>)]+")
ABS_PATH = re.compile(r"(?<![\w$])(/(?:[\w.@+-]+/)+[\w.@+-]+\.[a-z0-9]{1,6})\b", re.I)


def hosts_in(text: str) -> list[str]:
    """Distinct URL hosts, lowercased, in order of first appearance."""
    seen: list[str] = []
    for url in URL.findall(text or ""):
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:      # e.g. an unclosed IPv6 bracket; skip the URL, keep the moment
            continue
        if host and host not in seen:
            seen.append(host)
    return seen


def t1_cue(tool_name: str, tool_input: dict) -> Optional[dict]:
    """{command, hosts, path} for a T1 tool event, or None."""
    if tool_name not in T1_TOOLS or not isinstance(tool_input, dict):
        return None
    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        return {"command": command, "hosts": hosts_in(command), "path": ""}
    if tool_name == "WebFetch":
        return {"command": "", "hosts": hosts_in(str(tool_input.get("url") or "")), "path": ""}
    return {"command": "", "hosts": [], "path": str(tool_input.get("file_path") or "")}


def t2_touches(tool_name: str, tool_input: dict) -> list[str]:
    """The paths and hosts this tool call touches: a Read/Edit/Write file path, or the URL
    hosts and absolute file paths a Bash command names. A touch is `path:<p>` or `host:<h>`."""
    if tool_name not in T2_TOOLS or not isinstance(tool_input, dict):
        return []
    if tool_name == "Bash":
        command = str(tool_input.get("command") or "")
        found = [f"host:{h}" for h in hosts_in(command)]
        found += [f"path:{p}" for p in dict.fromkeys(ABS_PATH.findall(command))]
        return found
    path = str(tool_input.get("file_path") or "")
    return [f"path:{path}"] if path else []


def touch_terms(touch: str) -> list[str]:
    """Distinctive words of a touch: path parts and file stem, or host labels, minus words
    that name no subject (src, docs, com, ...)."""
    kind, _, value = touch.partition(":")
    if kind == "host":
        labels = [label for label in value.split(".") if label]
        words = tokens(" ".join(labels))
    else:
        stem = os.path.splitext(os.path.basename(value))[0]
        parts = value.replace("\\", "/").split("/")[-4:-1] + [stem]
        words = tokens(" ".join(p.replace("_", " ").replace("-", " ") for p in parts))
    return [w for w in dict.fromkeys(words) if w not in GENERIC_PATH_PARTS]
