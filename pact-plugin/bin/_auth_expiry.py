"""The claude.ai sign-in expiry warning given when an unattended run starts.

Location: pact-plugin/bin/_auth_expiry.py. Used by bin/repact-unattended (`on`),
bin/_repact_launch.py (a launch with REPACT_UNATTENDED on) and bin/_repact_ops.py
(`repact work --unattended`). Stdlib only.

An unattended run on a sign-in that ends overnight dies with nobody there. Claude Code
2.1.318+ prints the end of a stored claude.ai sign-in in `claude auth status` (JSON by
default) as `loginExpiresAt`, an ISO 8601 time such as 2026-11-24T00:24:00.294Z, or null.
warning() returns one line when that time is within WARN_WITHIN of now or already past.

Fail soft, always: no `claude` on PATH, a run slower than TIMEOUT_S, a non-zero exit,
output that is not a JSON object, a missing or null field, or a time that does not parse
all give None (no warning, no error). Only unattended starts call it: the user switches
accounts often, so a check on every start would be noise.

Usage: `line = warning()`; print it to the owner's terminal when it is not None. Tests
inject `now`, `run` (subprocess.run's shape) and `which` (shutil.which's).
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from typing import Callable, Mapping, Optional

WARN_WITHIN = datetime.timedelta(hours=48)
TIMEOUT_S = 5
MAX_OUTPUT = 64 * 1024
FIELD = "loginExpiresAt"
# Date, T, time with optional fraction, then Z or an offset. ASCII digits only, so no
# other script's digits reach fromisoformat; a time with no zone is refused, not guessed.
ISO_UTC = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{1,9})?"
                     r"(Z|[+-]\d{2}:\d{2})", re.ASCII)


def parse_expiry(value: object) -> Optional[datetime.datetime]:
    """The aware UTC time of a loginExpiresAt value, or None for anything else."""
    if not isinstance(value, str) or len(value) > 40:
        return None
    match = ISO_UTC.fullmatch(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    micro = int((fraction or ".0")[1:7].ljust(6, "0"))
    try:
        offset = datetime.timedelta(0) if zone == "Z" else (
            (1 if zone[0] == "+" else -1)
            * datetime.timedelta(hours=int(zone[1:3]), minutes=int(zone[4:6])))
        moment = datetime.datetime(int(year), int(month), int(day), int(hour), int(minute),
                                   int(second), micro, tzinfo=datetime.timezone(offset))
        return moment.astimezone(datetime.timezone.utc)
    except (ValueError, OverflowError):
        return None


def read_expiry(env: Optional[Mapping[str, str]] = None, run: Callable = subprocess.run,
                which: Callable = shutil.which) -> Optional[datetime.datetime]:
    """loginExpiresAt from `claude auth status --json`, or None on any failure."""
    try:
        binary = which("claude", path=env.get("PATH") if env is not None else None)
        if not binary:
            return None
        proc = run([binary, "auth", "status", "--json"], capture_output=True, text=True,
                   timeout=TIMEOUT_S, env=dict(env) if env is not None else None)
        if proc.returncode != 0 or not isinstance(proc.stdout, str) or len(proc.stdout) > MAX_OUTPUT:
            return None
        status = json.loads(proc.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, RecursionError):
        return None
    return parse_expiry(status.get(FIELD)) if isinstance(status, dict) else None


def describe(expiry: datetime.datetime, now: datetime.datetime) -> Optional[str]:
    """The warning for an expiry seen at `now`, or None when it is more than WARN_WITHIN away."""
    left = expiry - now
    when = expiry.strftime("%Y-%m-%d %H:%M UTC")
    fix = "run `claude auth login` (or `claude setup-token`) before you leave the run"
    if left <= datetime.timedelta(0):
        return f"warning: the claude.ai sign-in ended at {when}; this unattended run will fail; {fix}."
    if left > WARN_WITHIN:
        return None
    hours = max(1, int(left.total_seconds() // 3600))
    return (f"warning: the claude.ai sign-in ends at {when} (in about {hours} h), within 48 h; "
            f"an unattended run that outlives it fails; {fix}.")


def warning(now: Optional[datetime.datetime] = None, env: Optional[Mapping[str, str]] = None,
            run: Callable = subprocess.run, which: Callable = shutil.which) -> Optional[str]:
    """One warning line, or None (also on every failure). Never raises."""
    try:
        expiry = read_expiry(env, run, which)
        if expiry is None:
            return None
        return describe(expiry, now or datetime.datetime.now(datetime.timezone.utc))
    except Exception:  # noqa: BLE001 - fail soft: a check that cannot run warns of nothing
        return None
