"""
Location: pact-plugin/hooks/shared/marker_schema.py
Summary: Shared marker schema constants, signature function and verifier
         (is_marker_set, session_dir_unlinked) for the bootstrap-complete marker. Imported by both
         the producer (bootstrap_marker_writer.py) and the verifier
         (bootstrap_gate.py) so a single source of truth defines the marker's
         content invariants.
Used by: bootstrap_marker_writer.py (producer), bootstrap_gate.py (verifier,
         imported inside its fail-closed import block),
         bootstrap_prompt_gate.py, shared/session_init_resume.py,
         tests/test_marker_schema.py, tests/test_bootstrap_gate.py,
         tests/test_bootstrap_marker_writer.py

Coupling: this module is the SSOT for the marker's wire format. Producer
and verifier MUST import the same MARKER_SCHEMA_VERSION,
MARKER_MAX_BYTES, and expected_marker_signature so their digests
align byte-for-byte. Bumping MARKER_SCHEMA_VERSION invalidates
pre-bump markers automatically (via the digest input).

Security note: the signature is NOT cryptographic provenance. All four
inputs to the digest are readable from the same-user filesystem
(session_id + plugin_root from pact-session-context.json,
plugin_version from plugin.json, marker_version from this module).
A same-user attacker with read access can recompute the digest. The
signature is a fingerprint that closes the trivial Bash-touch bypass
(#662) and creates a detection surface for forgery; it is not a MAC.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

import shared.pact_context as pact_context
from shared.constants import BOOTSTRAP_MARKER_NAME

# Marker schema version. Bump if marker JSON shape changes; verifier
# rejects unknown versions. Producer (bootstrap_marker_writer.py) and
# verifier (is_marker_set below) both read this.
MARKER_SCHEMA_VERSION = 1

# Marker file size cap (bytes). The marker JSON is a small fixed schema
# ({v, sid, sig}); content larger than this is rejected to defend against
# pathological reads. Producer asserts len(payload) <= MARKER_MAX_BYTES
# before write; verifier rejects any on-disk file larger than this.
MARKER_MAX_BYTES = 256


def expected_marker_signature(session_id: str, plugin_root: str,
                              plugin_version: str, marker_version: int) -> str:
    """Compute the expected SHA256 marker signature.

    Inputs are joined with `|` separators in a fixed order so producer and
    verifier compute the same digest:

        sha256(f"{session_id}|{plugin_root}|{plugin_version}|{marker_version}")

    The `marker_version` is part of the digest so a format-version bump
    invalidates pre-bump markers automatically.
    """
    payload = f"{session_id}|{plugin_root}|{plugin_version}|{marker_version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def session_dir_unlinked(session_dir: Path) -> bool:
    """True when no part of the session dir at or below the config dir is a symlink: its
    resolved path is the resolved config dir joined with the part below it. A linked ancestor
    of the config dir itself (/var -> /private/var, a linked ~/.claude) is the user's own layout
    and stays allowed; the writer, the restamp and is_marker_set all ask this one question, so
    a marker the writer stamps is one the gate reads. A session dir outside the
    config dir must have no linked part at all. False on any error.
    """
    try:
        absolute = session_dir.absolute()
        resolved = session_dir.resolve(strict=False)
        config = pact_context.get_claude_config_dir().absolute()
        try:
            below = absolute.relative_to(config)
        except ValueError:
            return resolved == absolute
        return ".." not in below.parts and resolved == config.resolve(strict=False) / below
    except (OSError, RuntimeError, ValueError):
        return False


def is_marker_set(session_dir: "Path | None") -> bool:
    """True iff a properly stamped bootstrap-complete marker exists for this session.

    Requires: a regular file (lstat, no leaf symlink), no symlink at or below the config dir
    (session_dir_unlinked), size <=
    MARKER_MAX_BYTES, exactly keys {v, sid, sig}, v == MARKER_SCHEMA_VERSION, sid ==
    session_dir.name, sig == expected_marker_signature (compare_digest). Anything else is False.
    The signature is a fingerprint against a Bash `touch`, not a MAC (all inputs are readable).
    """
    if not session_dir:
        return False
    session_dir = Path(session_dir)

    # No symlink at or below the config dir (session_dir_unlinked).
    if not session_dir_unlinked(session_dir):
        return False

    marker_path = session_dir / BOOTSTRAP_MARKER_NAME

    # S2: lstat (does NOT follow symlinks) + S_ISREG (regular file only).
    try:
        st = os.lstat(str(marker_path))
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False

    # Verify marker CONTENT (#662 — closes the Bash-touch bypass).
    try:
        if st.st_size <= 0 or st.st_size > MARKER_MAX_BYTES:
            return False
        content = marker_path.read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return False
        if set(parsed.keys()) != {"v", "sid", "sig"}:
            return False
        if not isinstance(parsed["v"], int) or parsed["v"] != MARKER_SCHEMA_VERSION:
            return False
        if not isinstance(parsed["sid"], str) or parsed["sid"] != session_dir.name:
            return False
        if not isinstance(parsed["sig"], str):
            return False
        plugin_root = pact_context.get_plugin_root()
        if not plugin_root:
            return False
        plugin_json_path = Path(plugin_root) / ".claude-plugin" / "plugin.json"
        try:
            plugin_version = json.loads(
                plugin_json_path.read_text(encoding="utf-8")
            ).get("version", "")
        except (OSError, ValueError):
            return False
        if not plugin_version:
            return False
        expected = expected_marker_signature(
            parsed["sid"], plugin_root, plugin_version, parsed["v"]
        )
        if not hmac.compare_digest(parsed["sig"], expected):
            return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False
