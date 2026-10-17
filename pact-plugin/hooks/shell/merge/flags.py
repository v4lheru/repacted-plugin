"""
Location: pact-plugin/hooks/shell/merge/flags.py
Summary: Flag normalisation, per-operation flag conditions and extract_privileged_flags.
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         Posture: honest-mistake prevention, not an adversarial sandbox; a faithful
         single-command approval click must always mint. Threat model and design rules:
         the design notes.
Map: extract_privileged_flags, _FLAG_SPEC, _NEGATED_FLAG_VALUES, _normalized_flags.
"""

from __future__ import annotations


from shell.merge.arms import (PRIVILEGED_FLAGS)
from shell.merge.views import (_shell_tokenize)

def extract_privileged_flags(command: str, op_type: str | None) -> list[str]:
    """Scan a command for the privileged dash-flags bound on its op-class (#1042)."""
    denylist = PRIVILEGED_FLAGS.get(op_type) if op_type is not None else None
    if not denylist:
        # op_type is None, unknown, or carries no bound flags (e.g. branch-delete
        # today). An empty result binds the empty set — over-block-safe and the
        # correct outcome for the API/un-flagged classes.
        return []

    # Derive the lookup tables from the denylist ONCE per call. All small,
    # constant-size structures (the denylist has <=3 entries per op-class), so
    # the per-token work below stays O(1).
    alias_to_canonical: dict[str, str] = {}
    value_taking: set[str] = set()
    canonical_long_names: list[str] = []
    for canonical, (aliases, takes_value) in denylist.items():
        canonical_long_names.append(canonical)
        if takes_value:
            value_taking.add(canonical)
        for alias in aliases:
            alias_to_canonical[alias] = canonical
    # git expands unambiguous long-option abbreviations (gh does not), so only git surfaces expand.
    is_git_surface = op_type in ("force-push", "branch-delete", "push-to-main")

    # Quote-aware tokens: a quoted "--admin" still binds (a plain split kept the quotes and missed it).
    tokens = _shell_tokenize(command)
    if tokens is None:
        tokens = command.split()
    found: set[str] = set()
    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]
        # Non-flag tokens, the bare `-` (stdin) and `--` (end-of-options) marker
        # never bind. Skipping `--` is load-bearing: it must NOT prefix-match a
        # sole long flag in the abbreviation branch below.
        if not token.startswith("-") or token in ("-", "--"):
            i += 1
            continue

        if token.startswith("--"):
            # Long flag: exact denylist hit, or — git surface only — an
            # unambiguous prefix abbreviation. An inline `=value` is split off.
            flag_part, has_eq, inline_value = token.partition("=")
            canonical = alias_to_canonical.get(flag_part)
            if canonical is None and is_git_surface:
                prefix_matches = [
                    name for name in canonical_long_names if name.startswith(flag_part)
                ]
                # Exactly one match = unambiguous; >1 is ambiguous (git itself
                # rejects it, so the command never runs) and binds nothing.
                if len(prefix_matches) == 1:
                    canonical = prefix_matches[0]
            if canonical is None:
                i += 1
                continue
            if canonical in value_taking:
                if has_eq:                       # --repo=value
                    found.add(f"{canonical}={inline_value}")
                    i += 1
                elif i + 1 < n:                  # --repo value
                    found.add(f"{canonical}={tokens[i + 1]}")
                    i += 2
                else:                            # --repo (value missing; degenerate)
                    found.add(canonical)
                    i += 1
            else:
                # An explicit =false/=0/=no disables a boolean flag, so it must not bind as the flag.
                if not (has_eq and inline_value.lower() in _NEGATED_FLAG_VALUES):
                    found.add(canonical)
                i += 1
            continue

        # Short cluster walk: lone, combined, attached-value and mixed-order shorts in one loop.
        cluster = token[1:]
        consumed_next = False
        j = 0
        while j < len(cluster):
            canonical = alias_to_canonical.get("-" + cluster[j])
            if canonical is None:
                j += 1
                continue
            if canonical in value_taking:
                remainder = cluster[j + 1:]
                if remainder.startswith("="):    # `-R=value`
                    remainder = remainder[1:]
                if remainder:                    # `-Rvalue`
                    found.add(f"{canonical}={remainder}")
                elif i + 1 < n:                  # `-R value`
                    found.add(f"{canonical}={tokens[i + 1]}")
                    consumed_next = True
                else:                            # `-R` (value missing; degenerate)
                    found.add(canonical)
                break
            found.add(canonical)                 # boolean short; keep walking
            j += 1
        i += 2 if consumed_next else 1

    return sorted(found)


# --- Op-agnostic quote-aware flag normalizer and per-op danger conditions ---

# Per-surface flag spec: alias -> (canonical token, takes_value). A superset of
# PRIVILEGED_FLAGS that also carries the danger booleans (-D, --delete, --force, lease).
_FLAG_SPEC: dict[str, dict[str, tuple[str, bool]]] = {
    "gh": {
        "--admin": ("--admin", False),
        "-d": ("--delete-branch", False),
        "--delete-branch": ("--delete-branch", False),
        "-R": ("--repo", True),
        "--repo": ("--repo", True),
        "--match-head-commit": ("--match-head-commit", True),
    },
    "git": {
        "-D": ("-D", False),
        "-d": ("--delete", False),
        "--delete": ("--delete", False),
        "-f": ("--force", False),
        "--force": ("--force", False),
        "--force-with-lease": ("--force-with-lease", False),
        "--no-verify": ("--no-verify", False),
        # Mass-delete trigger booleans: visible to _normalized_flags, not bound as privileged flags.
        "--mirror": ("--mirror", False),
        "--prune": ("--prune", False),
    },
}

# Boolean-flag values that DISABLE the flag: `--admin=false` is the SAFE form, so
# it does NOT confer the privilege / satisfy a danger condition. Any OTHER value
# (or none) binds (fail-toward-binding on an unrecognized value = over-block-safe).
_NEGATED_FLAG_VALUES = frozenset({"false", "0", "no"})


def _normalized_flags(tokens: list[str], surface: str) -> set[str]:
    """P4: canonicalize a P1 token list into the SET of flags PRESENT, across every spelling (short
    / long / clustered / `=`-joined / attached-value), keyed by the tool SURFACE ('gh' / 'git').
    """
    spec = _FLAG_SPEC.get(surface, {})
    if not spec:
        return set()
    found: set[str] = set()
    i, n = 0, len(tokens)
    while i < n:
        token = tokens[i]
        if not token.startswith("-") or token in ("-", "--"):
            i += 1
            continue
        if token.startswith("--"):
            flag_part, has_eq, value = token.partition("=")
            entry = spec.get(flag_part)
            if entry is None:
                i += 1
                continue
            canonical, takes_value = entry
            if takes_value:
                if has_eq:                       # --repo=value
                    found.add(f"{canonical}={value}")
                    i += 1
                elif i + 1 < n:                  # --repo value
                    found.add(f"{canonical}={tokens[i + 1]}")
                    i += 2
                else:                            # --repo (value missing; degenerate)
                    found.add(canonical)
                    i += 1
            else:
                # boolean: an explicit `=false`/`=0`/`=no` DISABLES it → do not bind.
                if not (has_eq and value.lower() in _NEGATED_FLAG_VALUES):
                    found.add(canonical)
                i += 1
            continue
        # short cluster (single dash): a per-character walk matching the privileged
        # extractor's — a value-taking short consumes the REST of the cluster (or the
        # next token) and stops, so no bound short is dropped regardless of ordering.
        cluster = token[1:]
        consumed_next = False
        j = 0
        while j < len(cluster):
            entry = spec.get("-" + cluster[j])
            if entry is None:
                j += 1
                continue
            canonical, takes_value = entry
            if takes_value:
                remainder = cluster[j + 1:]
                if remainder.startswith("="):    # `-R=value`
                    remainder = remainder[1:]
                if remainder:                    # `-Rvalue`
                    found.add(f"{canonical}={remainder}")
                elif i + 1 < n:                  # `-R value`
                    found.add(f"{canonical}={tokens[i + 1]}")
                    consumed_next = True
                else:                            # `-R` (value missing; degenerate)
                    found.add(canonical)
                break
            found.add(canonical)                 # boolean short; keep walking
            j += 1
        i += 2 if consumed_next else 1
    return found
