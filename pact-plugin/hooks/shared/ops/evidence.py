"""
Location: pact-plugin/hooks/shared/ops/evidence.py
Summary: The one adapter between the ops and the evidence check:
`verdict` runs shared/evidence_check.check on the task, `undisposed` reads the uncertainty
prefixes (shared/handoff_schema), and brief_line tells the coder what the check reads.
Used by: shared/ops/submit.py (preview, undisposed), accept.py (verdict), brief.py (brief_line).
"""

from __future__ import annotations

from typing import Optional

from shared.handoff_schema import undisposed  # noqa: F401  # re-export: submit reads it from here

# What the brief tells the coder about tests: accept reads the run ledger, not the HANDOFF text.
# `tcount` is this plugin's own, quoted (brief.tcount_command): a bare one can resolve on PATH to
# another install, whose runs land in that install's ledger.
def brief_line(tcount: str) -> str:
    return (f"Run tests with `{tcount} <cmd>` (this plugin's tcount: a bare `tcount` can be another "
            "install, whose runs accept never sees) in your worktree after your last edit: accept "
            "looks for a passing run on the files as they are then. For a fix or a feature, first run "
            "the new test and see it fail: that red run must NAME the test file (or node id) in its "
            "command, not run the whole suite, and must come before any passing run on the same files. "
            "Commit your work before you submit: accept refuses an input file that is not committed.")

def verdict(task: dict, team: Optional[str] = None, handoff: Optional[dict] = None,
            worktree: Optional[str] = None, session_id: Optional[str] = None) -> dict:
    """{"problems": [line], "notes": [line], "statuses", "runs", "tree"} for the task: the check
    reads the dispatch record and the ledger, never the HANDOFF text (the other arguments stay
    for the callers' shape). A check that raises is a problem, never a pass: accept must not
    close a task the check could not judge."""
    try:
        from shared.evidence_check import check

        out = check(task)
    except Exception as error:  # noqa: BLE001 - any failure of the check refuses the close
        return {"problems": [f"the evidence check failed ({type(error).__name__}); rerun accept "
                             "[rule: evidence.needs_human]"], "notes": []}
    problems = [f"{item['line']} [rule: {item['rule']}]" for item in out["problems"]]
    return dict(out, problems=problems)
