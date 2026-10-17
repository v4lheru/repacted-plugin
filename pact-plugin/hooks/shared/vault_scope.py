"""
Location: pact-plugin/hooks/shared/vault_scope.py
Summary: The KB scope pin (D26): the one knowledge folder the plugin may read or write,
         kb_root / vault_scope_root, and is_within for containment checks.
Used by: bin/vault-find, bin/vault-note (and their kb-find, kb-note aliases), bin/_doctor.py,
         bin/_kb/*, bin/recall (via the bin/_repact_common shim), shared/task_log/write.py.
The root comes from the profile's paths.kb (or the legacy paths.vault) and nowhere else: there
is no built-in default, so an unconfigured profile means KBNotConfigured, the KB is off, and
every KB feature reports itself off instead of reading someone else's folder. An optional
kb.require_suffix pins the root to a folder whose path ends in that suffix (a guard against
pointing the KB at the wrong folder of a larger tree). An optional kb.index_page names the KB's
index page; there is none by default.
Map (file order): ENV_VAULT, KB_KEYS, VaultScopeError, KBNotConfigured, is_within, _section,
_configured_root, _check_suffix, kb_root, vault_scope_root, kb_index_page.
"""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path

from shared.plugin_paths import profile, profile_path


ENV_VAULT = "REPACT_VAULT"
# Profile keys under `paths`, in precedence order: the generic name, then the legacy one.
KB_KEYS = ("kb", "vault")


class VaultScopeError(Exception):
    """A requested KB root is misconfigured or lies outside the knowledge folder. The message
    is one line and names the profile key to fix."""


class KBNotConfigured(VaultScopeError):
    """No KB root is configured: the KB is off. Hooks and recall stay silent on it; a CLI
    prints the one-line message and exits non-zero."""


def is_within(path: os.PathLike | str, root: os.PathLike | str) -> bool:
    """True when the RESOLVED path is the resolved root or lies inside it."""
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _section(data: dict, key: str) -> dict:
    """data[key] as a dict ({} when absent). Raises VaultScopeError when it is another type."""
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise VaultScopeError(f"`{key}` in the repacted profile ({profile_path()}) must be an object")
    return value


def _configured_root(paths: dict) -> tuple[str, str]:
    """(profile key, configured value) of the first non-empty KB key in `paths`.

    Raises:
        KBNotConfigured: neither paths.kb nor paths.vault is set.
        VaultScopeError: the chosen key holds something other than a string.
    """
    for key in KB_KEYS:
        value = paths.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if not isinstance(value, str):
            raise VaultScopeError(f"paths.{key} in the repacted profile must be a folder path")
        return f"paths.{key}", value
    raise KBNotConfigured(f"the KB is off: set paths.kb in the repacted profile ({profile_path()}) "
                          "to your knowledge folder")


def _check_suffix(resolved: str, key: str, kb: dict) -> None:
    """Enforce kb.require_suffix when set. A malformed value fails closed, never silently off.

    The comparison is on NFC-normalised, '/'-separated text, so a profile typed in one Unicode
    form still matches a folder name the filesystem returns in the other.
    """
    suffix = kb.get("require_suffix")
    if suffix is None:
        return
    if not isinstance(suffix, str) or not suffix.strip("/ "):
        raise VaultScopeError("kb.require_suffix in the repacted profile must be a non-empty "
                              "folder suffix, or null for none")
    wanted = unicodedata.normalize("NFC", suffix.strip("/ "))
    actual = unicodedata.normalize("NFC", resolved.replace(os.sep, "/"))
    if not actual.endswith("/" + wanted):
        raise VaultScopeError(f"configured KB root {resolved} is not the {wanted} folder; "
                              f"set {key} in the repacted profile to it")


def kb_root() -> Path:
    """The one KB root: paths.kb (else the legacy paths.vault), resolved with realpath.

    Raises:
        KBNotConfigured: no KB key is configured (the KB is off).
        VaultScopeError: the profile shape is wrong, the path is relative, it resolves to a
            filesystem root or the home folder, or it fails kb.require_suffix.
    """
    data = profile()
    key, configured = _configured_root(_section(data, "paths"))
    expanded = os.path.expanduser(configured)
    if not os.path.isabs(expanded):
        raise VaultScopeError(f"{key} in the repacted profile must be an absolute path (or start "
                              "with ~)")
    try:
        resolved = os.path.realpath(expanded)
        home = os.path.realpath(os.path.expanduser("~"))
    except ValueError as exc:  # an embedded NUL byte
        raise VaultScopeError(f"{key} in the repacted profile is not a valid path") from exc
    if os.path.dirname(resolved) == resolved or resolved == home:
        raise VaultScopeError(f"{key} resolves to {resolved}, a filesystem root or the home "
                              "folder; point it at a dedicated knowledge folder")
    _check_suffix(resolved, key, _section(data, "kb"))
    return Path(resolved)


def vault_scope_root() -> Path:
    """The root a vault tool may use: the KB root, or $REPACT_VAULT when it NARROWS to a
    folder inside it (resolved with realpath, so a symlink cannot widen it).

    Raises:
        VaultScopeError: $REPACT_VAULT resolves outside the KB root, or the KB root itself
            is misconfigured (KBNotConfigured when it is not configured at all).
    """
    root = kb_root()
    requested = os.environ.get(ENV_VAULT)
    if not requested:
        return root
    try:
        resolved = os.path.realpath(os.path.expanduser(requested))
    except ValueError as exc:
        raise VaultScopeError(f"{ENV_VAULT} is not a valid path") from exc
    if not is_within(resolved, root):
        raise VaultScopeError(f"{ENV_VAULT}={requested} is outside the knowledge folder {root}; "
                              "it may only narrow to a folder inside it")
    return Path(resolved)


def kb_index_page() -> str | None:
    """The KB's index page (kb.index_page in the profile): a page file name at the KB root whose
    `- [[page]] -- summary` lines vault-find reads and new pages are listed in. None when unset:
    no page is the index, and a new page links only its related pages.

    Raises:
        VaultScopeError: the value is not a plain `.md` file name at the KB root.
    """
    value = _section(profile(), "kb").get("index_page")
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if (not isinstance(value, str) or value != value.strip() or not value.endswith(".md")
            or value.startswith(".") or any(c in value for c in "/\\\0")):
        raise VaultScopeError("kb.index_page in the repacted profile must be a page file name at "
                              "the KB root (e.g. map.md), or null for none")
    return value
