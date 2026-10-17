"""Point the shared agent and protocols links at one product's root, before and after a session.

Location: pact-plugin/bin/_agentlinks.py (owned by launcher; plan section 1.2 A1 as
amended by the live gate). Used by: bin/repacted-memdir (`--links`, `--merge-split`, `--status`),
which bin/repact calls around every alternate-mode session. Stdlib only.

WHY THE launcher MOVES THE LINKS ITSELF (MEASURED, live gate #97): Claude Code reads
<config>/agents/ at session START, before SessionStart's symlinks.py re-points the links, and
refreshes it only later. Bare-name teammates spawned early in the first session after a switch
therefore got the PREVIOUS product's agent body. So the launcher points the links at repacted
before `claude` starts, and its exit trap points them back at PACT's root.

The link set mirrors hooks/shared/symlinks.py exactly:
  <config>/agents/pact-*.md       -> <root>/agents/pact-*.md   (one per agent file in <root>)
  <home>/.claude/protocols/pact-plugin -> <root>/protocols    (and <config>/protocols/pact-plugin
                                                                too when the config dir differs)
Rules, the same as symlinks.py: an existing link is swapped atomically (a temporary link beside
it, then os.replace), a missing one is created, and a REAL file or dir is never touched: it is
reported and left. Every link is read back afterwards and must resolve to its target.

PACT's root is read from <config>/plugins/installed_plugins.json, never guessed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _memdir import Refusal, VerifyFailed  # noqa: E402  one error vocabulary for the CLI

PREFERRED_PACT_KEY = "PACT@pact-marketplace"
PROTOCOLS_LINK = Path("protocols") / "pact-plugin"
SWAP_SUFFIX = ".repact-swap-tmp"  # not ".md": a pact-*.md sweep never meets it

Pair = Tuple[Path, Path]  # (link path, target)
Say = Callable[[str], None]


def own_plugin_root() -> Path:
    """The repacted plugin root this file ships in (bin/..)."""
    return Path(__file__).resolve().parents[1]


def pact_install_root(config_root: Path) -> Path:
    """PACT's install path from installed_plugins.json. Raises Refusal when not certain."""
    registry = config_root / "plugins" / "installed_plugins.json"
    try:
        plugins = json.loads(registry.read_text(encoding="utf-8")).get("plugins", {})
    except (OSError, ValueError, AttributeError) as exc:
        raise Refusal(f"cannot read PACT's install path from {registry} "
                      f"({exc.__class__.__name__})") from exc
    keys = sorted(k for k in plugins if isinstance(k, str) and k.startswith("PACT@"))
    if not keys:
        raise Refusal(f"{registry} lists no PACT@ plugin, so PACT's root is unknown")
    key = PREFERRED_PACT_KEY if PREFERRED_PACT_KEY in keys else keys[0] if len(keys) == 1 else None
    if key is None:
        raise Refusal(f"{registry} lists several PACT@ plugins ({', '.join(keys)}); "
                      "cannot tell which one PACT sessions load")
    entries = [e for e in plugins[key] if isinstance(e, dict)] if isinstance(plugins[key], list) else []
    user = [e for e in entries if e.get("scope") == "user"]
    chosen = user if user else entries
    if len(chosen) != 1 or not isinstance(chosen[0].get("installPath"), str):
        raise Refusal(f"{registry} has {len(chosen)} usable install entries for {key}; "
                      "expected exactly one")
    root = Path(chosen[0]["installPath"])
    if not (root / "agents").is_dir() or not (root / "protocols").is_dir():
        raise Refusal(f"PACT's install path {root} (from {registry}) has no agents/ or "
                      "protocols/ dir")
    return root


def link_pairs(config_root: Path, home: Path, product_root: Path) -> List[Pair]:
    """Every link symlinks.py manages, paired with its target under product_root."""
    pairs = [(config_root / "agents" / f.name, f)
             for f in sorted((product_root / "agents").glob("pact-*.md"))]
    link_roots = [home / ".claude"]
    if os.path.realpath(config_root) != os.path.realpath(home / ".claude"):
        link_roots.append(config_root)
    pairs += [(root / PROTOCOLS_LINK, product_root / "protocols") for root in link_roots]
    return pairs


def _points_at(link: Path, target: Path) -> bool:
    return link.is_symlink() and os.path.realpath(link) == os.path.realpath(target)


def _swap(link: Path, target: Path) -> None:
    """Atomic re-point of an EXISTING symlink (caller guarantees is_symlink)."""
    tmp = link.with_name(link.name + SWAP_SUFFIX)
    if os.path.lexists(tmp):
        os.unlink(tmp)
    try:
        tmp.symlink_to(target)
        os.replace(tmp, link)
    except OSError:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def point_links(pairs: List[Pair], *, apply: bool, label: str, say: Say = print) -> int:
    """Point each link at its target. Returns the count of real files left alone."""
    changed, kept = [], []
    for link, target in pairs:
        if _points_at(link, target):
            continue
        if os.path.lexists(link) and not link.is_symlink():
            kept.append(link)  # a real file or dir: a deliberate override; never touched
            continue
        changed.append((link, target))
    say(f"links -> {label}: {len(changed)} to point, {len(pairs) - len(changed) - len(kept)} "
        f"already there, {len(kept)} real file(s) left alone")
    for link in kept:
        print(f"repact: links: {link} is a real file, not a link; left alone", file=sys.stderr)
    if not apply:
        return len(kept)
    for link, target in changed:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            _swap(link, target)
        else:
            link.symlink_to(target)
    wrong = [str(link) for link, target in changed if not _points_at(link, target)]
    if wrong:
        raise VerifyFailed(f"VERIFY FAILED: {len(wrong)} link(s) do not resolve to {label} "
                           f"after re-pointing: {', '.join(wrong)}")
    return len(kept)


def ownership(pairs: List[Pair], roots: Dict[str, Optional[Path]]) -> List[str]:
    """One summary line plus a line per link that is not a link into a known root."""
    known = {label: os.path.realpath(root) for label, root in roots.items() if root is not None}
    counts: Dict[str, int] = {}
    odd = []
    for link, _ in pairs:
        if not os.path.lexists(link):
            owner = "missing"
        elif not link.is_symlink():
            owner = "real file"
        else:
            real = os.path.realpath(link)
            owner = next((label for label, root in known.items()
                          if real == root or real.startswith(root + os.sep)), "other")
        counts[owner] = counts.get(owner, 0) + 1
        if owner not in known:
            odd.append(f"    {link}: {owner}")
    summary = ", ".join(f"{n} -> {owner}" for owner, n in sorted(counts.items()))
    return [f"  links ({len(pairs)}): {summary}"] + odd
