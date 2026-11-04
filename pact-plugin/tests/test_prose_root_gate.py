"""Regression gate: LLM-facing prose must not hardcode the Claude config root.

Agents follow prose literally, not the Python resolver, so a `~/.claude/...`
literal in an instruction surface makes a non-default-root install read, write,
and in one case execute against the WRONG install's state tree.

Three arms, two prose assertions, one allowlist, and a control per direction.
Implemented in pathlib rather than shell grep as a PORTABILITY requirement:
ARM 1's lookahead is PCRE and BSD `/usr/bin/grep` rejects `-P`. Where `grep -P`
appears to work, an interactive shell function may be rewriting `grep` to
`ugrep` — that function does not exist in CI.
"""

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent

# The census population. NOT the nine dirs the model gate walks, and no
# top-level iterdir pass: that scope yields 357 out-of-population hits across
# 86 files (tests 227, hooks 100, README 21, telegram 6, reference 3).
CENSUS_DIRS = ("agents", "commands", "skills", "protocols")

# Suffix ALLOWLIST, and it is load-bearing rather than defence-in-depth: it is
# the only mechanism that excludes `__pycache__/*.pyc`. No (path, literal,
# count) triple can key a pycache path — the count is a property of the
# reader's installed interpreters and moved 0 -> 13 -> 0 -> 13 -> 17 during
# development while nobody edited anything. Filter by suffix, never by
# directory name; suffix fails closed.
# Measured by reviewer-impl: 17 of 17 .pyc raise UnicodeDecodeError under a
# strict utf-8 read, and an errors="replace" read recovers 2 banned literals
# from them. So WITHOUT this filter ARM 1 HARD-REDS on allowlist-absent keys;
# it does not merely drift. `.sh` was added after measuring that exactly one
# .sh file sits in the population and carries zero hits - it was invisible to
# all three arms, which is a fail-OPEN on detection.
SCANNED_SUFFIXES = (".py", ".md", ".json", ".txt", ".yml", ".yaml",
                    ".toml", ".sh")

# This file necessarily contains the banned literals in its own allowlist.
SELF_EXCLUDED_FILES = frozenset({"tests/test_prose_root_gate.py"})

# ⚠️ THE `(?=/)` LOOKAHEAD IS LOAD-BEARING — DO NOT "FIX" IT.
# It is the only reason this arm stays quiet on the migration's OWN output.
# The prescribed shell form is `"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/..."`,
# where `$HOME/.claude` is followed by `}` and not `/`. Broadening to
# `(?=[/}])`, or dropping the lookahead, REDS ON CORRECTLY MIGRATED CODE — but
# NOT on the same text, and the distinction is the point:
#   BROADENING to `(?=[/}])` reds the SHELL form `"${CLAUDE_CONFIG_DIR:-$HOME/
#     .claude}/..."`, because `$HOME/.claude` there is followed by `}`. It
#     leaves the `{config_dir}` definition sentence QUIET.
#   DROPPING the lookahead reds BOTH the shell form AND every file carrying the
#     definition sentence, which writes a trailing backticked `~/.claude`.
# Spec site 1, the CRITICAL registration site, migrates to the shell form, so
# BOTH edits break it. Measured. See the control
# `test_arm1_stays_quiet_on_the_migrations_own_output` below.
ARM1 = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude(?=/)")

# Same firing behaviour as ARM1, extended to capture the path tail so an
# allowlist entry keys on WHICH literal is allowed. Without the tail, every
# entry would key on the bare root prefix and a file could swap an allowed
# `~/.claude/pact-memory/memory.db` for a forbidden `~/.claude/tasks/` while
# holding its count. Occurrence granularity, not line granularity.
ARM1_KEY = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude/[^\s`\"'),*]*")

# ARM 1 stops at `.claude/` and is therefore blind to a BARE config root with
# no path after it - `cd ~/.claude`, `CONFIG=~/.claude`. ARM 3 closes that.
# The exclusion set is the whole design and each character earns its place:
#   /  ARM 1's job, and firing here too would double-count
#   `  the `{config_dir}` definition sentence writes a trailing backticked
#      `~/.claude`; without this, every carrier of that sentence needs a triple
#   }  the migrated shell form ends `$HOME/.claude}` - WITHOUT THIS, ARM 3 REDS
#      ON CORRECTLY MIGRATED CODE, the same trap as ARM 1's lookahead
#   \w and -  `.claude-zai`, `.claudex` are OTHER installs, not this root
# Measured on the migrated tree: 0 occurrences, so this costs no allowlist
# entries. It can acquire them if someone writes a bare root deliberately.
ARM3 = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude(?![/`\w}-])")

# Targeted pin on known sites — NOT a class gate. Misses single-quoted
# '.claude', Path.home().joinpath(".claude"), os.path.expanduser("~/.claude"),
# and Path("~/.claude").expanduser(). Do not read it as covering the class.
ARM2 = re.compile(r"Path\.home\(\)\s*/\s*\"\.claude\"")

_DB_PIN = (
    "pact-memory DB, default-root-pinned by design at "
    "skills/pact-memory/scripts/config.py:186 (#1410)"
)
_DB_PIN_MIRRORED = (
    _DB_PIN + " — MIRRORED PAIR: this is the one allowlist entry with a SECOND "
    "mechanical detector. scripts/verify-protocol-extracts.sh reds if a future "
    "editor migrates one half without the other, so the count is not the sole "
    "guard here."
)
# Telegram's prose and code moved TOGETHER off the default root: telegram/
# config.py resolves CONFIG_DIR from $CLAUDE_CONFIG_DIR (else ~/.claude), and
# reference/init/telegram.md writes the .env under
# "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pact-telegram". Neither half may move
# alone, or the setup writes the .env where the server never reads it. This pin
# holds both halves; it carries no allowlist entry any more.
#
# THIS READS telegram/config.py, WHICH CENSUS_DIRS DELIBERATELY EXCLUDES, AND
# THAT IS CORRECT. CENSUS_DIRS is the population of the VIOLATION SCAN. A
# premise pin is a different operation on a different file, and it reads outside
# that population BECAUSE THAT IS WHY IT NEEDS A PIN AT ALL - the premise
# lives where the scan does not look. Do not "tidy" this into CENSUS_DIRS.
_TELEGRAM_PREMISE = ("telegram/config.py", 'os.environ.get("CLAUDE_CONFIG_DIR")')
_TELEGRAM_PROSE = ("reference/init/telegram.md",
                   '"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pact-telegram"')

# (path, literal, count, justification). The count closes the hole in BOTH
# directions: a new site pushes it above declared, a removed site drops it
# below, so a stale entry reds instead of outliving its reason.
ARM1_ALLOWLIST = [
    # the protocol consolidation: the SSOT copy is deleted; the recovery table moved to recovery.md.
    ("protocols/recovery.md", "~/.claude/pact-memory/memory.db", 1, _DB_PIN_MIRRORED),
    # the secretary rework: the two storage-path mentions moved verbatim to the skill's long form.
    ("skills/pact-memory/references/memory-long-form.md", "~/.claude/pact-memory/memory.db", 2, _DB_PIN),
    ("skills/pact-memory/scripts/cli.py", "~/.claude/pact-memory/...", 1,
     "illustrative, no consumer — a stderr-scrubbing comment. NOT 'pact-memory, "
     "by design': that reason is false here and would license the next bare "
     "pact-memory tail that DOES have a consumer."),
    ("skills/pact-memory/scripts/config.py", "~/.claude/pact-memory", 1, _DB_PIN),
    ("skills/pact-memory/scripts/database.py", "~/.claude/pact-memory/memory.db", 1, _DB_PIN),
]

ARM2_ALLOWLIST = [
    ("skills/pact-memory/scripts/config.py", 186, _DB_PIN),
]


def _scanned_files():
    for subdir in CENSUS_DIRS:
        for path in sorted((PLUGIN_ROOT / subdir).rglob("*")):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            rel = path.relative_to(PLUGIN_ROOT).as_posix()
            if rel in SELF_EXCLUDED_FILES:
                continue
            yield rel, path.read_text(encoding="utf-8", errors="replace")


def _allowlist_delta(found, declared):
    """Both directions of the allowlist comparison, in one place.

    Lifted out of the live check so the tail-discrimination arm below exercises
    THE SHIPPED COMPARISON rather than a copy of it - a reimplementation could
    drift from this while the suite stayed green.
    """
    stale = {k: (found.get(k, 0), n) for k, n in declared.items() if found.get(k, 0) < n}
    over = {k: (found[k], declared.get(k, 0)) for k in found if found[k] > declared.get(k, 0)}
    return stale, over


def _arm1_counts():
    found = {}
    for rel, text in _scanned_files():
        for match in ARM1_KEY.finditer(text):
            found[(rel, match.group(0))] = found.get((rel, match.group(0)), 0) + 1
    return found


# --------------------------------------------------------------------------
# ARM 1 — the census population


def test_arm1_allowlist_is_exact_in_both_directions():
    """Over AND under in ONE test, deliberately.

    Splitting the two directions across two tests means deleting one silently
    opens that direction. ARM 2 does both in a single test and is structurally
    stronger for it; this now matches.
    """
    declared = {(p, lit): n for p, lit, n, _ in ARM1_ALLOWLIST}
    stale, over = _allowlist_delta(_arm1_counts(), declared)
    assert not stale, (
        "Allowlist entries declare more occurrences than exist. The site was "
        f"fixed or moved; delete the entry rather than leave the hole: {stale}"
    )
    assert not over, (
        "LLM-facing prose hardcodes the Claude config root. Agents follow prose "
        "literally, so each of these misdirects a non-default-root install. "
        f"Migrate to `{{config_dir}}` (narrative) or "
        '`"${CLAUDE_CONFIG_DIR:-$HOME/.claude}/..."` (shell), or add an '
        f"allowlist entry WITH a justification. found>declared: {over}"
    )


# The file and substitution used by the tail-discrimination arm. Chosen because
# it is the entry whose justification names this exact hazard.
_SWAP_FILE = "skills/pact-memory/references/memory-long-form.md"
_SWAP_FROM = "~/.claude/pact-memory/memory.db"
_SWAP_TO = "~/.claude/tasks/"
# The counterfactual key: ARM1_KEY with the tail capture removed.
_ROOT_ONLY_KEY = re.compile(r"(?:~|\$HOME|\$\{HOME\})/\.claude/")


def _counts_for(text, key_re):
    found = {}
    for match in key_re.finditer(text):
        k = (_SWAP_FILE, match.group(0))
        found[k] = found.get(k, 0) + 1
    return found


# --------------------------------------------------------------------------
# ARM 2 — Path.home() in code


# --------------------------------------------------------------------------
# Prose assertions


PLACEHOLDER_TOKENS = ("{team_name}", "{session_dir}", "{plugin_root}", "{config_dir}")
PAIR = ("commands/bootstrap.md", "agents/pact-orchestrator.md")


# --------------------------------------------------------------------------
# Controls — every assertion shown able to fire, in all three directions


