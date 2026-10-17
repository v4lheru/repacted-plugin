"""
Location: pact-plugin/hooks/shared/paths.py
Summary: Single source of truth for PACT's state roots.
         get_claude_config_dir() resolves $CLAUDE_CONFIG_DIR (fail-loud,
         monkeypatch-safe, single-path), falling back to ~/.claude; every PACT
         hook that reads/writes state under the config dir derives its base
         from it.
Used by: shared/{constants,session_registry,failure_log,merge_guard_common,
         task_utils,pact_context,...}.py and the hook entrypoints
         (dispatch_gate, session_init, session_end, ...).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# One path segment the plugin may join under a trusted root: a positive allowlist, so `.`,
# `..`, separators, controls, whitespace and shell or URL metacharacters all reject by
# construction (an `x.name == x` identity check admits `..`). Lives here, in the lightest
# shared module, so the task store and the fast hooks can import it without the package.
SAFE_PATH_COMPONENT_RE = re.compile(r"[A-Za-z0-9_-]+")


# Characters stripped from every string that flows into model-visible
# output (custom_instructions, systemMessage). Covers:
#   - C0 control chars 0x00-0x1F (includes NUL, BEL, tab 0x09,
#     LF 0x0A, CR 0x0D, ESC 0x1B, etc.)
#   - DEL (0x7F)
#   - NEL (U+0085), LINE SEPARATOR (U+2028), PARAGRAPH SEPARATOR
#     (U+2029) — Unicode line terminators recognized by
#     `str.splitlines()` and by LLM tokenizers; crafted names
#     containing these survive a naive C0-only filter and can inject
#     new lines into the rendered output.
# Single SSOT for the strip set: this regex IS the canonical
# SESSION_ID_CONTROL_CHARS_RE. It lives here, in a leaf, because pact_context imports it and
# session_state imports pact_context. Producer-side sanitizers in
# pact_context.init() (S9 defense) and session_init.py's
# classification ladder share one regex; previously each module
# defined its own copy and asymmetric strip sets across interpolation
# sinks could become the attacker's entry point. See security-engineer
# the symmetric-sanitisation rule.
#
# \ud83d\udd34 IT IS NARROWER THAN `_PROMPT_CONTROL_CHARS_RE`, ON PURPOSE, AND A WIDENING
# OF THAT ONE MUST NOT BE COPIED HERE. The two are different objects with
# different jobs, and the differences are the design:
#   - THIS one is a DETECTOR. Every call site uses `.search()`, so it answers
#     `does this identifier carry a line breaker`. It carries NO `+`, and that
#     is correct: for a search predicate a one-or-more quantifier matches the
#     same set of strings and buys nothing.
#   - THE OTHER one is a REPLACER. Its call sites use `.sub(" ", value)`, and
#     there the `+` IS load-bearing: without it a run of N control characters
#     becomes N spaces rather than one.
# ON LINE BREAKERS THE TWO ARE EQUAL. The other one covers U+0085 inside its
# `\x7f-\x9f` range, and this one names U+0085 by codepoint. This one omits
# ONLY the non-line-breaking C1 characters, which is what the list above says
# it intends. Making them one definition changes behaviour in either
# direction: the replacer would stop removing `\x80-\x84` and `\x86-\x9f` from
# model-visible prose, or this detector would start refusing session ids for
# C1 characters that break no line.
SESSION_ID_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f\u0085\u2028\u2029]")

def is_safe_path_component(value) -> bool:
    """True iff `value` is a non-empty string that is one safe path segment."""
    if not isinstance(value, str) or not value:
        return False
    return SAFE_PATH_COMPONENT_RE.fullmatch(value) is not None


def get_claude_config_dir(env=None, home=None) -> Path:
    """Resolve the Claude Code config/state root.

    Pure resolver of (env, home) with both defaulting to the live process
    globals, so L2/resolution-consumer tests drive it via monkeypatch.setenv +
    Path.home redirect (NO DI — injecting there is a vacuous green), while L1
    resolver-unit tests MAY pass env=/home= to assert the contract directly.

    Precedence (fail-loud — NEVER a silent wrong-root fallback):
      1. $CLAUDE_CONFIG_DIR, honored EVEN IF the dir does not yet exist (the
         platform creates it; only CONSUMERS fail-open on a missing dir).
         set-but-empty / whitespace == UNSET.
           "~"      -> home
           "~/x"    -> home / "x"     (exact-prefix slice; monkeypatch-safe)
           "/abs"   -> Path("/abs")
           "rel"    -> Path("rel")    (honored as-is; surfaced via observability)
      2. fallback (unset): home / ".claude"

    Returns an UNRESOLVED path — the single .resolve() stays at the call sites
    that perform containment checks. NO expanduser/lstrip/removeprefix.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    raw = (env.get("CLAUDE_CONFIG_DIR") or "").strip()
    if not raw:
        return home / ".claude"
    if raw == "~":
        return home
    if raw.startswith("~/"):
        return home / raw[2:]          # exact 2-char prefix — NOT lstrip, NOT removeprefix
    return Path(raw)
