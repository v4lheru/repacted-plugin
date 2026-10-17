"""
Location: pact-plugin/hooks/dispatch_rules.py
Summary: The rule-id carrier of a refusal: the `[rule: <id>]` tail a check may leave on its
    reason or stderr, the fallback id `<check>.<channel>`, and the decorated line
    `<check>: <reason> → <instead> [rule: <id>]` the host shows, `→ <instead>` being the way
    through shared/rules.py names for a registered id (none for a fallback id). Duck-typed
    over dispatch.py's Parsed (rule, channel, decision, outcome.check.name, outcome.exit_code,
    outcome.stderr).
Used by: hooks/dispatch.py only (parse, combine, journal_rows). A sibling of the dispatcher,
    not a `shared` module: the dispatcher keeps the shared package out of its own process, so
    shared/rules.py (a leaf) is loaded by path under a private name, never as `shared.rules`.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path


def _text(data) -> str:
    return data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else str(data or "")


# A stable rule id: dotted, at least two segments, nothing a path or a shell could read.
RULE_TAIL = re.compile(r"\s*\[rule:\s*([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)+)\]\s*$")
REFUSALS = frozenset({"deny", "ask", "block"})


def channel_of(outcome, decision) -> str:
    """How a check refused: exit_2, deny, ask or decision_block; '' for no refusal."""
    if decision not in REFUSALS:
        return ""
    if outcome.exit_code == 2:
        return "exit_2"
    return "decision_block" if decision == "block" else str(decision)


def rule_of(item) -> str:
    """The id a refusal is journaled and shown under: the check's own, else `<check>.<channel>`."""
    return item.rule or f"{item.outcome.check.name}.{item.channel}"


_RULES_FILE = Path(__file__).resolve().parent / "shared" / "rules.py"
_REGISTRY_NAME = "_pact_dispatch_rules_registry"
_registry: list = []  # [RULES dict], or [{}] when the file could not load; filled once


def instead_of(rule_id: str) -> str:
    """The way through shared/rules.py names for an id, '' for a fallback or unknown id or
    when the registry cannot load (the refusal still stands; only its text is shorter)."""
    if not _registry:
        try:
            spec = importlib.util.spec_from_file_location(_REGISTRY_NAME, _RULES_FILE)
            module = importlib.util.module_from_spec(spec)
            sys.modules[_REGISTRY_NAME] = module  # a dataclass resolves its module by name
            spec.loader.exec_module(module)
            _registry.append(module.RULES)
        except Exception:  # noqa: BLE001 - a missing or broken registry costs the hint only
            _registry.append({})
    rule = _registry[0].get(rule_id)
    return getattr(rule, "instead", "") or ""


def decorate(item, text: str) -> str:
    """`<check>: <text> → <instead> [rule: <id>]`; each part is added only when the check
    left it out, and the way through only for an id the registry knows."""
    name = item.outcome.check.name
    rule_id = rule_of(item)
    body = RULE_TAIL.sub("", text.strip()) or "refused"
    if not body.startswith(f"{name}: "):
        body = f"{name}: {body}"
    instead = instead_of(rule_id)
    if instead and not body.endswith(f"→ {instead}"):
        body = f"{body} → {instead}"
    return f"{body} [rule: {rule_id}]"


def decorated_single(item) -> tuple[bytes, bytes]:
    """The one refusing outcome's (stdout, stderr) with its reason lines normalised: the
    JSON's own reason field is rewritten in place (no new field), stderr on an exit 2."""
    outcome = item.outcome
    stdout, stderr = outcome.stdout, outcome.stderr
    if item.obj is not None:
        obj = json.loads(json.dumps(item.obj))
        hso = obj.get("hookSpecificOutput")
        if isinstance(hso, dict) and "permissionDecisionReason" in hso:
            hso["permissionDecisionReason"] = decorate(item, str(hso["permissionDecisionReason"] or ""))
        if obj.get("decision") == "block":
            obj["reason"] = decorate(item, str(obj.get("reason") or ""))
        stdout = json.dumps(obj).encode("utf-8")
    if outcome.exit_code == 2:
        stderr = (decorate(item, _text(stderr)) + "\n").encode("utf-8")
    return stdout, stderr
