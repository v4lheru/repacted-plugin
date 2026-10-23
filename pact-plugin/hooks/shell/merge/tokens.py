"""
Location: pact-plugin/hooks/shell/merge/tokens.py
Summary: Authorization-token lifecycle of the merge guard: TTL, directory, prefix, use markers
         and the cleanup passes (consumed, unused, orphan).
Used by: hooks/shared/merge_guard_common.py (the façade), merge_guard_pre/post and the
         other shell.merge modules per the imports below. Carved from merge_guard_common.py
         at 0edd3594; every decision is pinned by tests/fixtures/shell/decisions/live.jsonl.
         A token is the use ledger of one approval, not the consent: the consent is the
         user's answer in the session transcript, which merge_guard_pre re-reads
         (shared/approval_record.py) before it claims a use. Token files and transcripts are
         protected places (shell/settings.py). Posture: same OS user as the model, so not an
         adversarial sandbox; a faithful single-command approval click must always mint.
         Threat model and design rules: the design notes.
Map: TOKEN_TTL, TOKEN_DIR, TOKEN_PREFIX, MAX_USES, USE_MARKER_SUFFIX, ORPHAN_TOKEN_MAX_AGE_SECONDS, LAYER1_SUCCESS_STDOUT_PATTERNS, cleanup_consumed_tokens, cleanup_unused_tokens, _read_token, _expired_data, cleanup_orphan_tokens.

Token lifecycle (pinned by TestTokenLifecycleInvariants):
I-1 (at most one unused token per full binding): write_token retires its twin before O_EXCL.
I-2 (successful operation immediately retires the token): the post hook renames it.
I-3 (TTL expiry retires the token): find_valid_tokens and write_token remove expired tokens.
I-4 (failed operation preserves token for retry within TTL up to MAX_USES): .use-N slots.
I-5 (cross-session tokens never valid): find_valid_token compares sessions.
"""

from __future__ import annotations

import glob
import json
import os
import time
from pathlib import Path


# Token TTL in seconds
TOKEN_TTL = 900



def _config_dir_reader():
    """The lane's one upward edge (design C5): the config-dir SSOT, hooks/shared/paths.py,
    loaded by path. Importing `shared.paths` by name would run shared/__init__, which
    imports the merge_guard_common façade, which imports this module: a cycle at import
    time whenever this module is imported first. paths.py itself imports only the stdlib."""
    import importlib.util

    source = Path(__file__).resolve().parent.parent.parent / "shared" / "paths.py"
    spec = importlib.util.spec_from_file_location("_pact_shell_paths", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.get_claude_config_dir


# Token directory, read once at import from the config-dir SSOT; tests patch this attribute.
TOKEN_DIR = _config_dir_reader()()

# Token file prefix
TOKEN_PREFIX = "merge-authorized-"  # pragma: allowlist secret

# Uses per token: 2 allows one retry of an identical command after a transient failure.
MAX_USES = 2

# Suffix used by per-use marker files. Each marker file is created via
# O_EXCL to atomically claim one use slot of an N-use token (#720 Bug C).
USE_MARKER_SUFFIX = ".use-"

# Orphan reaping age, a fixed multiple of TOKEN_TTL; disk hygiene only (TTL already expires).
ORPHAN_TOKEN_MAX_AGE_SECONDS = 12 * TOKEN_TTL

# Per op_type stdout substring that proves success and retires the token (None skips it).
LAYER1_SUCCESS_STDOUT_PATTERNS: dict[str, str | None] = {
    "merge": "Merged pull request",
    "close": "Closed pull request",
    "branch-delete": "Deleted branch",
    "force-push": None,
    # push-to-main (GAP3): a plain `git push` to main/master — emits to STDERR like
    # force-push, so None (Block 3 skipped; Block 2 platform-success is load-bearing,
    # fail-closed-on-no-signal → TTL/MAX_USES safety net, not bypass).
    "push-to-main": None,
}


def cleanup_consumed_tokens(token_dir: Path) -> None:
    """Remove stale .consumed token files and .use-N markers older than TOKEN_TTL."""
    now = time.time()
    patterns = (
        str(token_dir / f"{TOKEN_PREFIX}*.consumed"),
        str(token_dir / f"{TOKEN_PREFIX}*{USE_MARKER_SUFFIX}*"),
    )
    for pattern in patterns:
        for stale_path in glob.glob(pattern):
            try:
                # Use file modification time as a proxy for consumption time
                mtime = os.path.getmtime(stale_path)
                if now - mtime > TOKEN_TTL:
                    try:
                        os.unlink(stale_path)
                    except OSError:
                        pass
            except OSError:
                # File may have been cleaned up concurrently — ignore
                pass


def cleanup_unused_tokens(token_dir: Path, context: dict | None = None,
                          session_id: str = "") -> None:
    """Delete every expired token, and atomically retire (rename to .consumed) the unused
    tokens a new approval replaces: with a `context`, this session's tokens whose whole
    context equals it (one unused token per full binding: op, target, flags, repo); with
    none, every unused token. A live token for another binding or session is left to its
    own use and TTL. Never raises."""
    now = time.time()
    pattern = str(token_dir / f"{TOKEN_PREFIX}*")
    for path in glob.glob(pattern):
        # Already terminal — skip to avoid creating .consumed.consumed shape.
        if path.endswith(".consumed"):
            continue
        # Per-use slot markers are NOT tokens; preserve them as audit trail
        # alongside their parent token's retirement (cleanup_consumed_tokens
        # reaps them at the TOKEN_TTL boundary).
        if USE_MARKER_SUFFIX in os.path.basename(path):
            continue
        try:
            token_data = _read_token(path)
            if _expired_data(token_data, now):
                os.unlink(path)
                continue
            if context is not None and (
                    token_data is None or token_data.get("context") != context
                    or token_data.get("session_id", "") != (session_id or "")):
                # Another binding or session, or unreadable: the read side judges it.
                continue
            os.rename(path, path + ".consumed")
        except (FileNotFoundError, OSError):
            # Concurrent retire (another writer's cleanup, or _consume_token
            # claiming the same path) won the race — the invariant holds
            # either way. Swallow.
            pass


def _read_token(path: str) -> dict | None:
    """The token file's JSON object, or None when it cannot be read as one."""
    try:
        with open(path, "r") as f:
            token_data = json.load(f)
    except (OSError, ValueError):
        return None
    return token_data if isinstance(token_data, dict) else None


def _expired_data(token_data: dict | None, now: float) -> bool:
    """True for a readable token whose expires_at is a number in the past."""
    if token_data is None:
        return False
    expires_at = token_data.get("expires_at")
    return (isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool)
            and expires_at < now)


def cleanup_orphan_tokens(
    token_dir: Path,
    max_age_seconds: int = ORPHAN_TOKEN_MAX_AGE_SECONDS,
) -> None:
    """Reap unconsumed tokens older than max_age_seconds (disk hygiene)."""
    now = time.time()
    pattern = str(token_dir / f"{TOKEN_PREFIX}*")
    for path in glob.glob(pattern):
        if path.endswith(".consumed"):
            continue
        if USE_MARKER_SUFFIX in os.path.basename(path):
            continue
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            # File gone between glob and stat — race-safe no-op.
            continue
        if now - mtime > max_age_seconds:
            try:
                os.unlink(path)
            except OSError:
                # Race vs concurrent cleanup or permission flake — swallow.
                pass
