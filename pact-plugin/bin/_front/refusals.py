"""
Location: pact-plugin/bin/_front/refusals.py
Summary: `repact inspect --refusals [last | <code>]` and `repact inspect --rule <id>` (formerly
bin/repact-explain): what the last refusals were and why, from the
session journal. Read-only, on demand. Prints one block per gate_refusal or check_failure row:
when, hook, verdict, rule, the fixed sentence for the rule (hooks/shared/rules.py), the way
through (`instead`), target and frame, and the troubleshooting anchor (docs/troubleshooting.md#<id>).
No new store: the journal rows carry hashes, never command text or reasons, so this shows the id
and its sentence, not the refused command. `--rule <id>` prints one rule's sentence, way through
and anchor with no journal read.
  last        the most recent row (the default is the last --n, 5)
  <code>      rows whose `code` (sha256[:12] of the reason) starts with this prefix
The session is $CLAUDE_CODE_SESSION_ID unless --session-id or --session-dir names another.
Used by: bin/_repact_ops.py (`repact inspect`).
Exit codes: 0 printed; 1 no matching row (or no such rule); 2 usage error; 3 no journal found.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import _repact_common as common  # noqa: E402
from shared import task_files as ev  # noqa: E402

EXAMPLE = "repact inspect --refusals last"
ROW_TYPES = ("gate_refusal", "check_failure")


def _rules():
    hooks = str(common.plugin_root() / "hooks")
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    from shared.rules import explain
    return explain


def journal_path(session: str | None, session_dir: str | None) -> Path | None:
    if session_dir:
        path = Path(session_dir).expanduser() / "session-journal.jsonl"
        return path if path.is_file() else None
    session_id = session or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    return ev.session_journal(session_id) if session_id else None


def read_rows(path: Path) -> list[dict]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("type") in ROW_TYPES:
            rows.append(row)
    return rows


def select(rows: list[dict], code: str | None, last: bool, n: int,
           frame: str | None, hook: str | None, agent: str | None = None) -> list[dict]:
    chosen = rows
    if code:
        chosen = [r for r in chosen if str(r.get("code", "")).startswith(code)]
    if frame:
        chosen = [r for r in chosen if r.get("frame") == frame]
    if agent:
        chosen = [r for r in chosen if r.get("agent") == agent]
    if hook:
        chosen = [r for r in chosen if (r.get("hook") or r.get("check")) == hook]
    return chosen[-1:] if last else chosen[-n:]


def render(row: dict, explain) -> list[str]:
    when = row.get("ts") or row.get("timestamp") or row.get("time") or ""
    hook = row.get("hook") or row.get("check") or ""
    if row.get("type") == "check_failure":
        return [f"{when}  {hook}  failed  {row.get('exc_class', '')}",
                "  the check raised, timed out or could not load; the dispatcher went on "
                "(a security check's timeout is journaled as a gate_refusal too)."]
    rule_id = str(row.get("rule", ""))
    rule = explain(rule_id)
    lines = [f"{when}  {hook}  {row.get('verdict', '')}  {rule_id}"]
    lines.append(f"  {rule.explain}" if rule else
                 f"  (no sentence on file for {rule_id}: a check that names no rule id; the "
                 f"channel was {row.get('channel') or rule_id.rsplit('.', 1)[-1]})")
    detail = [f"code {row.get('code', '')}", f"target {row.get('target', '') or '-'}",
              f"frame {row.get('frame', '') or '-'}", f"event {row.get('event', '') or '-'}"]
    if row.get("agent"):
        detail.append(f"agent {row['agent']}")
    if row.get("tool_name"):
        detail.append(f"tool {row['tool_name']}")
    if rule:
        lines.append(f"  instead: {rule.instead}")
    lines.append("  " + "  ".join(detail))
    if rule:
        lines.append(f"  doc {rule.doc}")
    return lines


def show(what: str | None, n: int = 5, frame: str | None = None, agent: str | None = None,
         hook: str | None = None, session: str | None = None, session_dir: str | None = None,
         as_json: bool = False) -> int:
    """Print the refusal rows `what` selects ('last', a code prefix, or None for the last n)."""
    if n < 1:
        return _usage("--n must be at least 1")
    last = what == "last"
    code = None if last or not what else what
    if code and not all(ch in "0123456789abcdef" for ch in code):
        return _usage(f"not a code prefix: {code!r}")
    path = journal_path(session, session_dir)
    if path is None:
        print("repact inspect: no session journal found (give --session-id or --session-dir)",
              file=sys.stderr)
        return 3
    rows = select(read_rows(path), code, last, n, frame, hook, agent)
    if not rows:
        print("repact inspect: no refusal or check failure matches", file=sys.stderr)
        return 1
    if as_json:
        print(json.dumps(rows, indent=1, sort_keys=True))
        return 0
    explain = _rules()
    print("\n".join(line for row in rows for line in render(row, explain)))
    return 0


def rule(rule_id: str) -> int:
    """Print one rule's sentence, its way through and its troubleshooting anchor."""
    found = _rules()(str(rule_id))
    if found is None:
        print(f"repact inspect: no rule {str(rule_id)[:80]!r} on file (hooks/shared/rules.py)", file=sys.stderr)
        return 1
    print(f"{rule_id}\n  {found.explain}\n  instead: {found.instead}\n  doc {found.doc}")
    return 0


def _usage(message: str) -> int:
    print(f"repact inspect: error: {message}\n\n  {EXAMPLE}", file=sys.stderr)
    return 2
