"""The two free online signals D3's report takes from the shadow log (D9.4 point 4).

Location: pact-plugin/bin/_recall/signals.py. D3's `repact-report` is not built yet,
so it calls `session_signals()` (or `bin/recall --signals`) when it lands.
- agreement: of the items recall would have injected, the share the agent later pulled itself
  in the same session (a lower bound on precision);
- miss: of the items the agent pulled, the share recall had not predicted earlier in the same
  session (a lower bound on the miss rate, i.e. 1 - recall).
A type is re-scored against the bar once its agreement over 2 real sessions is >= 0.6.
"""
from __future__ import annotations

from typing import Optional


def session_signals(events: list[dict], session: Optional[str] = None) -> dict:
    """{session: {agreement, miss, predicted, pulled, by_type}} from shadow-log events."""
    sessions: dict[str, dict] = {}
    for event in events:
        sid = event.get("session") or "no-session"
        if session and sid != session:
            continue
        state = sessions.setdefault(sid, {"predicted": {}, "pulled": []})
        if event.get("event") == "moment":
            for key in event.get("would_inject_keys") or []:
                state["predicted"].setdefault(key, event.get("type"))
        elif event.get("event") == "pull":
            key = event.get("key")
            if key:
                state["pulled"].append((key, key in state["predicted"]))
    out = {}
    for sid, state in sessions.items():
        predicted, pulled = state["predicted"], state["pulled"]
        pulled_keys = {k for k, _ in pulled}
        agreed = [k for k in predicted if k in pulled_keys]
        missed = [k for k, was_predicted in pulled if not was_predicted]
        by_type: dict[str, dict] = {}
        for key, moment_type in predicted.items():
            row = by_type.setdefault(moment_type or "?", {"predicted": 0, "agreed": 0})
            row["predicted"] += 1
            row["agreed"] += key in pulled_keys
        out[sid] = {"predicted": len(predicted), "pulled": len(pulled_keys),
                    "agreement": round(len(agreed) / len(predicted), 3) if predicted else None,
                    "miss": round(len(set(missed)) / len(pulled_keys), 3) if pulled_keys else None,
                    "by_type": by_type}
    return out
