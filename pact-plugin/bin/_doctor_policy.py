"""The team policy's doctor line and `repact-doctor --policy` (hooks/shared/team_policy.py).

Location: pact-plugin/bin/_doctor_policy.py. Used by bin/_doctor.py (run_checks: the
`policy` check) and bin/repact-doctor (--policy). Stdlib only, plus the hooks' shared package.
The work tree is the one the doctor runs in, as for the hooks: a linked worktree reads its own
checked-out `.repacted/policy.json`.

Map, in file order: effective (each value, where it came from), check_policy, render.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from _doctor_result import Result, _capped
from shared import team_policy
from shared.pact_config import _env_enum

POLICY = str(team_policy.POLICY_PATH)


def _user_constants(plugin_root: Path) -> dict:
    """bin/checks.json as this machine has it, before the policy; {} when unreadable."""
    try:
        constants = json.loads((plugin_root / "bin" / "checks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return constants if isinstance(constants, dict) else {}


def effective(plugin_root: Path, cwd: Optional[str] = None) -> List[Tuple[str, str, str]]:
    """(key, effective value, source) for every value a policy can tighten. Source is
    'policy (this machine: ...)', 'checks.json', 'env' or 'shipped'."""
    user = _user_constants(plugin_root)
    merged = team_policy.tighten_constants(user, cwd)
    raised = team_policy.raised(cwd)
    user_modes = user.get("modes") if isinstance(user.get("modes"), dict) else {}
    merged_modes = merged.get("modes") if isinstance(merged.get("modes"), dict) else {}
    rows = []
    for check in team_policy.CHECK_MODES:
        key = f"modes.{check}"
        value = team_policy.consumer_mode(check, merged_modes.get(check))
        if key in raised:
            source = "policy, raised from " + raised[key].split("this machine: ")[-1].rstrip(")")
        else:
            readable = team_policy.readable_mode(check, user_modes.get(check)) is not None
            source = "checks.json" if readable else "shipped"
        rows.append((key, value, source))
    rows.append(_deny_words_row(user, merged, raised))
    for name in team_policy.OPTIONS:
        resolved = _env_enum(name)
        value = team_policy.tighten_option(name, resolved, cwd)
        key = f"pact_options.{name}"
        source = (f"policy, raised from {resolved}" if value != resolved
                  else "env" if os.environ.get(name) is not None else "shipped")
        rows.append((key, value, source))
    allow, programs = team_policy.egress_settings(cwd)
    rows.append(("egress.allow_pragma", "true" if allow else "false",
                 "shipped" if allow else "policy"))
    rows.append(("egress.network_programs", ", ".join(sorted(programs)) or "(none added)",
                 "policy" if programs else "shipped"))
    scope = team_policy.project_scope(cwd)
    rows.append(("agents.project_scope", scope, "policy" if scope == "off" else "shipped"))
    return rows


def _deny_words_row(user: dict, merged: dict, raised: Dict[str, str]) -> Tuple[str, str, str]:
    rule = user.get("commit_rule") if isinstance(user.get("commit_rule"), dict) else {}
    words = (merged.get("commit_rule") or {}).get("deny_words")
    if not isinstance(words, list):
        words = rule.get("deny_words")
    count = len(words) if isinstance(words, list) else 0
    base = "checks.json" if isinstance(rule.get("deny_words"), list) else "shipped"
    extra = raised.get("commit_rule.deny_words")
    return ("commit_rule.deny_words", f"{count} words" if count else "the shipped words",
            f"{base} + policy {extra}" if extra else base)


def check_policy(ctx, cwd: Optional[str] = None) -> Result:
    """GREEN with the path when a policy applies cleanly or there is none; AMBER when anything
    in it was ignored, each item in the detail; SKIP outside a git work tree. ctx is the
    doctor's Context (its plugin_root)."""
    root = team_policy.work_tree_root(cwd)
    if root is None:
        return Result("policy", "team policy", "skip", "not in a git work tree")
    policy = team_policy.load(cwd)
    if not policy.path:
        return Result("policy", "team policy", "green", f"none ({POLICY} absent in {root})")
    effective(ctx.plugin_root, cwd)
    raised = team_policy.raised(cwd)
    skipped = team_policy.ignored(cwd)
    summary = f"{team_policy.sources(policy)}: tightens {len(raised)} value(s)"
    if skipped:
        summary += f", ignores {len(skipped)}; repact-doctor --policy lists them"
    detail = [f"ignored: {line}" for line in skipped] + [f"{k}: {v}" for k, v in raised.items()]
    return Result("policy", "team policy", "amber" if skipped else "green", summary,
                  _capped(detail))


def render(plugin_root: Path, cwd: Optional[str] = None) -> List[str]:
    """The `repact-doctor --policy` report: the file, each value with its source, what was
    ignored."""
    root = team_policy.work_tree_root(cwd)
    if root is None:
        head = "team policy: not in a git work tree; every value below is this machine's own"
    else:
        policy = team_policy.load(cwd)
        head = (f"team policy: {team_policy.sources(policy)}" if policy.path
                else f"team policy: none ({root / POLICY} does not exist); every value below "
                     "is this machine's own")
    rows = effective(plugin_root, cwd)
    width = max(len(key) for key, _, _ in rows)
    lines = [head, ""] + [f"  {key:<{width}}  {value}  [{source}]" for key, value, source in rows]
    skipped = team_policy.ignored(cwd)
    if skipped:
        lines += ["", f"ignored ({len(skipped)}), never applied:"] + [f"  - {s}" for s in skipped]
    return lines
