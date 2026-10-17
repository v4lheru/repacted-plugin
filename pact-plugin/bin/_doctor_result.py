"""One check's outcome for repact-doctor, and the detail cap every check applies.

Location: pact-plugin/bin/_doctor_result.py. Used by bin/_doctor.py (which re-exports
both names) and bin/_doctor_state.py. A leaf (stdlib only), so the doctor and its state checks
import it instead of each other: before it, _doctor_state imported Result from _doctor, which
imports _doctor_state, an import cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

MAX_DETAIL = 8


@dataclass
class Result:
    id: str
    name: str
    state: str
    summary: str
    detail: List[str] = field(default_factory=list)
    ran_at: str = ""


def _capped(lines: List[str]) -> List[str]:
    extra = len(lines) - MAX_DETAIL
    return lines[:MAX_DETAIL] + ([f"... and {extra} more"] if extra > 0 else [])
