"""
Location: pact-plugin/hooks/shared/clock.py
Summary: The one UTC clock reading for records: utc_now (aware datetime) and utc_now_iso
         (seconds precision, +00:00). A leaf: stdlib only.
Used by: shared/ledger.py, bin/repact-amend, bin/repact-unattended, shared/evidence/*, bin/_doctor.py,
         shared/background_work.py.
Map (file order): utc_now, utc_now_iso.
"""
from __future__ import annotations

import datetime


def utc_now() -> datetime.datetime:
    """The current UTC moment, timezone-aware."""
    return datetime.datetime.now(datetime.timezone.utc)


def utc_now_iso() -> str:
    """Current UTC time as ISO-8601 with an explicit offset, second precision."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


