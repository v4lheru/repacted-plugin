"""
Location: pact-plugin/hooks/shared/team_policy.py
Summary: The team policy: `.repacted/policy.json`, committed at the root of a work tree, which
         can only TIGHTEN the guards for everyone working in it, never loosen them. A mode moves
         toward blocking only, deny words and network programs are added only, and the egress
         pragma can only be switched off. Anything that would loosen, and anything this reader
         cannot trust (a symlink, a file outside the work tree, malformed JSON, NaN or a huge
         number, a duplicate, unknown or look-alike key, a wrong type, a file over 64 KiB) is
         ignored and noted, never fatal and never applied. There is no switch that turns the
         policy off: that switch would be the loosening route. Work trees nest (a submodule, a
         vendored repository, an empty `.git`), so the policies of every enclosing work tree
         apply together, the stricter value winning: reading more can never loosen.
Used by: shared/evidence/ledger.py load_constants (modes, commit_rule.deny_words),
         shared/pact_config.py get_enum (pact_options), hooks/egress_guard.py (egress),
         shared/dispatch_helpers.py project_scope_allowed (agents.project_scope),
         shared/cost_cap.py (session.cost_cap_usd, via session_cost_cap), the
         refusals of write_checks.py, amendment_gate.py, dispatch_gate.py,
         handoff_ordering_gate.py and egress_guard.py (refusal_note), and bin/_doctor_policy.py
         (the doctor line and `repact-doctor --policy`).
Doc: reference/config.md, section "Team policy".

Map, in file order: the schema tables; locating and reading the file (load); the readers per
section; the merges (tighten_constants, tighten_option, egress_settings); notes (refusal_note).

Extension: a new key is one SECTIONS row (its reader) plus a merge function its consumer calls.
A top-level key named in RESERVED is noted as reserved for a later release (none today).
"""
from __future__ import annotations

import json
import math
import os
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

POLICY_PATH = Path(".repacted") / "policy.json"
VERSION = 1
MAX_BYTES = 64 * 1024
MAX_ITEMS = 200            # deny words or network programs one policy may add
MAX_WORD = 100             # characters in one deny word
MAX_INT_DIGITS = 18        # a longer integer literal is refused before it is built
NOTE_ITEMS = 5             # ignored items a refusal names before "and N more"
NOTE_VALUE = 40            # characters of one echoed key or value (repository text)
NOTE_MAX = 500             # characters of a whole refusal note

# Check modes in bin/checks.json "modes": each check's own words, ranked from open to blocking,
# and the mode its reader falls back to when the user's word cannot be read. The shipped modes
# repeat ledger.DEFAULT_MODES and hooks/amendment_gate.py; tests/test_team_policy.py pins both.
MODE_RANK = {"off": 0, "shadow": 1, "advisory": 2, "note": 2, "gate": 3, "enforce": 3}
_GATE_PAIR = {"gate": "enforce", "enforce": "gate"}   # the one alias the readers share
_USE_MODES = ("off", "shadow", "advisory", "gate")
CHECK_MODES: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "handoff_evidence": (_USE_MODES, "advisory"),
    "uncertainty_disposition": (_USE_MODES, "advisory"),
    "commit_rule": (_USE_MODES, "gate"),
    "amendment_gate": (("off", "shadow", "note", "enforce"), "shadow"),
    "ref_dispositions": (("off", "note", "enforce"), "enforce"),
    "rejection_shape": (("off", "note", "enforce"), "enforce"),
}
# PACT_* enum options (shared/pact_config.py) a policy may raise, ranked from open to blocking.
OPTION_ORDER = ("shadow", "warn", "deny")
OPTIONS = ("PACT_DISPATCH_INLINE_MISSION_MODE", "PACT_DISPATCH_VARIETY_MODE")
# Top-level keys a later release will read (see Extension above): noted as reserved until then.
RESERVED: Tuple[str, ...] = ()
# session.cost_cap_usd: dollars, above 0 and at most this (pact_config.USD_MAX says the same).
MAX_COST_CAP_USD = 1_000_000
# agents.project_scope, ranked from blocking to open: a policy can only move it to "off".
AGENT_SCOPES = ("off", "allow")
PROGRAM_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-")


class PolicyError(ValueError):
    """The file cannot be trusted as a whole; the message says why."""


@dataclass
class Policy:
    """The policy that applies in one work tree. `path` is set when a file was found (the
    nearest one), `paths` lists every file applied, nearest first; `values` holds only what
    passed its reader; `ignored` names everything that did not, one short phrase each. The
    merges fill `raised` ("<section>.<key>" -> what the policy set over this machine's value)
    and `loosening` (values ignored because they would loosen) as consumers ask."""
    path: Optional[str] = None
    values: dict = field(default_factory=dict)
    ignored: List[str] = field(default_factory=list)
    raised: Dict[str, str] = field(default_factory=dict)
    loosening: List[str] = field(default_factory=list)
    paths: List[str] = field(default_factory=list)


_cache: Dict[str, Policy] = {}


def reset() -> None:
    """Forget the cached policies and what their merges noted (tests)."""
    _cache.clear()


# ---------------------------------------------------------------------------
# Locating and reading
# ---------------------------------------------------------------------------

def work_tree_roots(cwd: Optional[str] = None) -> List[Path]:
    """Every directory, from cwd up, holding a `.git` directory or file, nearest first; empty
    outside a work tree. A linked worktree is a root of its own (it reads its own checked-out
    policy), and so is every work tree it sits inside."""
    try:
        start = Path(os.path.realpath(cwd or os.getcwd()))
        return [d for d in (start, *start.parents) if os.path.lexists(d / ".git")]
    except (OSError, ValueError):
        return []


def work_tree_root(cwd: Optional[str] = None) -> Optional[Path]:
    """The nearest work tree root from cwd up (work_tree_roots), or None outside one."""
    roots = work_tree_roots(cwd)
    return roots[0] if roots else None


def load(cwd: Optional[str] = None) -> Policy:
    """The policy that applies where cwd is (the process cwd when None): every enclosing work
    tree's file, merged stricter-wins (_union). Never raises: a file that cannot be trusted
    adds no values and one ignored line."""
    roots = work_tree_roots(cwd)
    if not roots:
        return Policy()
    key = str(roots[0])
    if key not in _cache:
        _cache[key] = _union([_load(root) for root in roots])
    return _cache[key]


def _stricter_modes(a: dict, b: dict) -> dict:
    return {**a, **{k: w for k, w in b.items() if MODE_RANK[w] > MODE_RANK.get(a.get(k), -1)}}


def _stricter_options(a: dict, b: dict) -> dict:
    return {**a, **{k: w for k, w in b.items()
                    if OPTION_ORDER.index(w) > OPTION_ORDER.index(a.get(k, OPTION_ORDER[0]))}}


# How two files' values for one key combine: the stricter, or both lists together.
_STRICTER: Dict[str, Callable] = {
    "modes": _stricter_modes,
    "pact_options": _stricter_options,
    "deny_words": lambda a, b: list(dict.fromkeys(a + b)),
    "network_programs": lambda a, b: list(dict.fromkeys(a + b)),
    "allow_pragma": lambda a, b: a and b,
    "project_scope": lambda a, b: "off" if "off" in (a, b) else a,
    "cost_cap_usd": min,
}


def _union(policies: List[Policy]) -> Policy:
    """One Policy from the files of nested work trees (nearest first): each value the
    stricter of theirs, each ignored line prefixed with its file when there are several."""
    found = [p for p in policies if p.path]
    if not found:
        return Policy()
    if len(found) == 1:
        found[0].paths = [found[0].path]
        return found[0]
    merged = Policy(found[0].path, paths=[p.path for p in found])
    for policy in found:
        merged.ignored += [f"{_path_shown(policy.path)}: {line}" for line in policy.ignored]
        for key, value in policy.values.items():
            have = merged.values.get(key)
            merged.values[key] = value if have is None else _STRICTER[key](have, value)
    return merged


def _load(root: Path) -> Policy:
    path = root / POLICY_PATH
    try:
        text = _read(root, path)
    except FileNotFoundError:
        return Policy()
    except PolicyError as error:
        return Policy(str(path), {}, [f"the whole file: {error}"])
    except (OSError, ValueError) as error:
        return Policy(str(path), {}, [f"the whole file: unreadable ({type(error).__name__})"])
    try:
        document = json.loads(text, object_pairs_hook=_object, parse_constant=_no_constant,
                              parse_float=_finite_float, parse_int=_bounded_int)
    except (ValueError, RecursionError) as error:
        why = str(error) if isinstance(error, PolicyError) else "malformed JSON"
        return Policy(str(path), {}, [f"the whole file: {why}"])
    if not isinstance(document, dict):
        return Policy(str(path), {}, ["the whole file: not a JSON object"])
    policy = Policy(str(path))
    _read_document(document, policy)
    return policy


def _read(root: Path, path: Path) -> str:
    """The file's text, refused when it is not a regular file inside the work tree."""
    folder = os.lstat(root / POLICY_PATH.parent)          # FileNotFoundError: no policy
    if stat.S_ISLNK(folder.st_mode):
        raise PolicyError(f"{POLICY_PATH.parent} is a symbolic link")
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise PolicyError("it is a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise PolicyError("not a regular file")
    # A hard link is read like any file: its text is bounded, and a policy only tightens.
    if os.path.commonpath([os.path.realpath(path), str(root)]) != str(root):
        raise PolicyError("it resolves outside the work tree")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        if os.fstat(handle.fileno()).st_size > MAX_BYTES:
            raise PolicyError(f"larger than {MAX_BYTES // 1024} KiB")
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise PolicyError(f"larger than {MAX_BYTES // 1024} KiB")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise PolicyError("not UTF-8 text") from None


class _Object(dict):
    """A JSON object that remembers the keys it saw twice (each is ignored, not either copy)."""
    duplicates: Tuple[str, ...] = ()


def _object(pairs) -> _Object:
    seen, twice = {}, []
    for key, value in pairs:
        if key in seen:
            twice.append(key)
        seen[key] = value
    result = _Object((k, v) for k, v in seen.items() if k not in twice)
    result.duplicates = tuple(dict.fromkeys(twice))
    return result


def _no_constant(name: str):
    raise PolicyError(f"{name} is not a JSON number")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise PolicyError(f"the number {text[:20]} is out of range")
    return value


def _bounded_int(text: str) -> int:
    if len(text.lstrip("-")) > MAX_INT_DIGITS:
        raise PolicyError(f"an integer of {len(text)} digits is out of range")
    return int(text)


# ---------------------------------------------------------------------------
# Readers, one per section: each keeps what is valid and notes the rest
# ---------------------------------------------------------------------------

def _shown(value) -> str:
    """A key or value from the file as a note may echo it: ascii() (no newline or control
    character reaches agent context) and at most NOTE_VALUE characters."""
    text = ascii(value)
    return text if len(text) <= NOTE_VALUE else text[:NOTE_VALUE - 3] + "..."


def _path_shown(path) -> str:
    """A file path as a note may echo it: ascii() without its quotes."""
    return ascii(str(path))[1:-1]


def _known_key(key: str, known, where: str, policy: Policy) -> bool:
    """True for an exact known key; otherwise one ignored line, naming a look-alike as such."""
    if key in known:
        return True
    folded = unicodedata.normalize("NFKC", key).casefold().strip()
    lookalike = next((k for k in known if k == folded), None)
    shown = _shown(key)
    if not where and key in RESERVED:
        policy.ignored.append(f"{shown}: reserved for a later release, not read by this one")
        return False
    policy.ignored.append(f"{where}{shown}: a look-alike of '{lookalike}', not the key itself"
                          if lookalike else f"{where}{shown}: not a known key")
    return False


def _duplicates(obj, where: str, policy: Policy) -> None:
    for key in getattr(obj, "duplicates", ()):
        policy.ignored.append(f"{where}{_shown(key)}: given more than once, so neither copy "
                              "is used")


def _section(document: dict, name: str, policy: Policy) -> Optional[dict]:
    value = document[name]
    if not isinstance(value, dict):
        policy.ignored.append(f"{name}: must be an object, not {type(value).__name__}")
        return None
    _duplicates(value, f"{name}.", policy)
    return value


def _read_version(document: dict, policy: Policy) -> None:
    value = document["version"]
    if type(value) is not int or value != VERSION:
        policy.ignored.append(f"version {_shown(value)}: only version {VERSION} is known; the keys "
                              f"below are read as version {VERSION}")


def _read_modes(document: dict, policy: Policy) -> None:
    section = _section(document, "modes", policy)
    for key, raw in (section or {}).items():
        if not _known_key(key, CHECK_MODES, "modes.", policy):
            continue
        vocabulary = CHECK_MODES[key][0]
        word = raw.strip().lower() if isinstance(raw, str) else None
        rank = MODE_RANK.get(word) if word is not None else None
        mapped = next((w for w in vocabulary if MODE_RANK[w] == rank), None)
        if mapped is None:
            policy.ignored.append(f"modes.{key} {_shown(raw)}: not a mode of this check "
                                  f"({', '.join(vocabulary)})")
            continue
        policy.values.setdefault("modes", {})[key] = mapped


def _words(raw, where: str, policy: Policy, valid: Callable[[str], bool]) -> List[str]:
    if not isinstance(raw, list):
        policy.ignored.append(f"{where}: must be a list of strings")
        return []
    kept = []
    for item in raw:
        if len(kept) >= MAX_ITEMS:
            policy.ignored.append(f"{where}: more than {MAX_ITEMS} items; the rest are not used")
            break
        if isinstance(item, str) and valid(item):
            kept.append(item.strip())
        else:
            policy.ignored.append(f"{where} item {_shown(item)}: not a usable word")
    return kept


def _deny_word(word: str) -> bool:
    word = word.strip()
    return 0 < len(word) <= MAX_WORD and all(unicodedata.category(c)[0] != "C" for c in word)


def _read_commit_rule(document: dict, policy: Policy) -> None:
    section = _section(document, "commit_rule", policy)
    for key, raw in (section or {}).items():
        if _known_key(key, ("deny_words",), "commit_rule.", policy):
            words = _words(raw, "commit_rule.deny_words", policy, _deny_word)
            if words:
                policy.values["deny_words"] = words


def _read_options(document: dict, policy: Policy) -> None:
    section = _section(document, "pact_options", policy)
    for key, raw in (section or {}).items():
        if not _known_key(key, OPTIONS, "pact_options.", policy):
            continue
        word = raw.strip().lower() if isinstance(raw, str) else None
        if word not in OPTION_ORDER:
            policy.ignored.append(f"pact_options.{key} {_shown(raw)}: not one of "
                                  f"{', '.join(OPTION_ORDER)}")
            continue
        policy.values.setdefault("pact_options", {})[key] = word


def _program(name: str) -> bool:
    return 0 < len(name) <= 64 and set(name) <= PROGRAM_CHARS and not name.startswith("-")


def _read_egress(document: dict, policy: Policy) -> None:
    section = _section(document, "egress", policy)
    for key, raw in (section or {}).items():
        if not _known_key(key, ("allow_pragma", "network_programs"), "egress.", policy):
            continue
        if key == "allow_pragma":
            if type(raw) is not bool:
                policy.ignored.append(f"egress.allow_pragma {_shown(raw)}: must be true or "
                                      "false")
            elif raw is False:
                policy.values["allow_pragma"] = False
        else:
            programs = _words(raw, "egress.network_programs", policy, _program)
            if programs:
                policy.values["network_programs"] = programs


def _read_agents(document: dict, policy: Policy) -> None:
    section = _section(document, "agents", policy)
    for key, raw in (section or {}).items():
        if not _known_key(key, ("project_scope",), "agents.", policy):
            continue
        word = raw.strip().lower() if isinstance(raw, str) else None
        if word not in AGENT_SCOPES:
            policy.ignored.append(f"agents.{key} {_shown(raw)}: not one of "
                                  f"{', '.join(AGENT_SCOPES)}")
            continue
        policy.values["project_scope"] = word


def valid_cost_cap(value) -> bool:
    """A JSON number (not a bool, not a string) above 0, finite and at most MAX_COST_CAP_USD."""
    return (type(value) in (int, float) and math.isfinite(value)
            and 0 < value <= MAX_COST_CAP_USD)


def _read_session(document: dict, policy: Policy) -> None:
    section = _section(document, "session", policy)
    for key, raw in (section or {}).items():
        if not _known_key(key, ("cost_cap_usd",), "session.", policy):
            continue
        if not valid_cost_cap(raw):
            policy.ignored.append(f"session.cost_cap_usd {_shown(raw)}: must be a number "
                                  f"above 0 and at most {MAX_COST_CAP_USD:,}")
            continue
        policy.values["cost_cap_usd"] = float(raw)


SECTIONS: Dict[str, Callable[[dict, Policy], None]] = {
    "version": _read_version,
    "modes": _read_modes,
    "commit_rule": _read_commit_rule,
    "pact_options": _read_options,
    "egress": _read_egress,
    "agents": _read_agents,
    "session": _read_session,
}


def _read_document(document: dict, policy: Policy) -> None:
    _duplicates(document, "", policy)
    for key in document:
        if _known_key(key, SECTIONS, "", policy):
            SECTIONS[key](document, policy)


# ---------------------------------------------------------------------------
# Merges: the user's value and the policy's, the stricter one wins
# ---------------------------------------------------------------------------

def readable_mode(check: str, raw) -> Optional[str]:
    """The check's own word for the user's raw value (the word itself or its gate / enforce
    alias), or None when the check's reader cannot read it."""
    vocabulary = CHECK_MODES[check][0]
    word = str(raw).strip().lower() if raw is not None else ""
    word = word if word in vocabulary else _GATE_PAIR.get(word, "")
    return word if word in vocabulary else None


def consumer_mode(check: str, raw) -> str:
    """The mode the check's own reader makes of the user's word: readable_mode, or the
    shipped mode when it reads nothing."""
    return readable_mode(check, raw) or CHECK_MODES[check][1]


def _loosens(policy: Policy, line: str) -> None:
    if line not in policy.loosening:
        policy.loosening.append(line)


def tighten_constants(constants: dict, cwd: Optional[str] = None) -> dict:
    """bin/checks.json with the policy merged in: each mode the stricter of the two, the deny
    words the user's plus the policy's. The same object when there is nothing to merge."""
    try:
        return _tighten_constants(constants, load(cwd))
    except Exception:   # noqa: BLE001 - a merge that fails leaves the user's own values
        return constants


def _tighten_constants(constants: dict, policy: Policy) -> dict:
    modes, words = policy.values.get("modes"), policy.values.get("deny_words")
    if not modes and not words:
        return constants
    merged = dict(constants)
    user_modes = constants.get("modes") if isinstance(constants.get("modes"), dict) else {}
    new_modes = dict(user_modes)
    for check, word in (modes or {}).items():
        current = consumer_mode(check, user_modes.get(check))
        if MODE_RANK[word] > MODE_RANK[current]:
            new_modes[check] = word
            policy.raised[f"modes.{check}"] = f"{word} (this machine: {current})"
        elif MODE_RANK[word] < MODE_RANK[current]:
            _loosens(policy, f"modes.{check} {word!r}: looser than this machine's {current!r}")
    if new_modes != user_modes:
        merged["modes"] = new_modes
    if words:
        merged["commit_rule"] = _with_words(constants.get("commit_rule"), words, policy)
    return merged


def _with_words(rule, words: List[str], policy: Policy) -> dict:
    rule = dict(rule) if isinstance(rule, dict) else {}
    current = rule.get("deny_words")
    if not isinstance(current, list) or not all(isinstance(w, str) for w in current):
        from shared.commit_rules import DEFAULT_DENY_WORDS
        current = list(DEFAULT_DENY_WORDS)
    have = {w.casefold() for w in current}
    added = [w for w in dict.fromkeys(words) if w.casefold() not in have]
    if added:
        shown = ", ".join(_shown(w) for w in added[:NOTE_ITEMS])
        policy.raised["commit_rule.deny_words"] = f"adds {shown}"
    rule["deny_words"] = list(current) + added
    return rule


def tighten_option(name: str, value: str, cwd: Optional[str] = None) -> str:
    """A PACT_* enum option after the policy: the stricter of the resolved value and the
    policy's. Never raises; the value unchanged when the policy cannot be read."""
    try:
        policy = load(cwd)
        wanted = policy.values.get("pact_options", {}).get(name)
    except Exception:   # noqa: BLE001 - a policy that cannot be read tightens nothing
        return value
    if wanted is None:
        return value
    current = OPTION_ORDER.index(value) if value in OPTION_ORDER else -1
    if OPTION_ORDER.index(wanted) > current:
        policy.raised[f"pact_options.{name}"] = f"{wanted} (this machine: {value})"
        return wanted
    if OPTION_ORDER.index(wanted) < current:
        _loosens(policy, f"pact_options.{name} {wanted!r}: looser than this machine's {value!r}")
    return value


def project_scope(cwd: Optional[str] = None) -> str:
    """"off" when the policy turns project-scope specialists off, else "allow" (this
    machine has no switch of its own, so "allow" in a policy changes nothing). Never raises."""
    try:
        policy = load(cwd)
        wanted = policy.values.get("project_scope")
    except Exception:   # noqa: BLE001 - a policy that cannot be read tightens nothing
        return "allow"
    if wanted == "off":
        policy.raised["agents.project_scope"] = "off (this machine: allow)"
        return "off"
    return "allow"


def session_cost_cap(user: Optional[float], cwd: Optional[str] = None
                     ) -> Tuple[Optional[float], str]:
    """(the session cost cap in dollars, who set it: "this machine" or "team policy"), the
    lower of the user's value and the policy's; (None, "") when neither sets one. A policy cap
    above the user's is ignored and noted as loosening. Never raises."""
    mine = (user, "this machine") if user is not None else (None, "")
    try:
        policy = load(cwd)
        wanted = policy.values.get("cost_cap_usd")
    except Exception:   # noqa: BLE001 - a policy that cannot be read tightens nothing
        return mine
    if wanted is None:
        return mine
    if user is None or wanted < user:
        here = f"{user:g}" if user is not None else "unset"
        policy.raised["session.cost_cap_usd"] = f"{wanted:g} (this machine: {here})"
        return wanted, "team policy"
    if wanted > user:
        _loosens(policy, f"session.cost_cap_usd {wanted:g}: looser than this machine's {user:g}")
    return mine


def egress_settings(cwd: Optional[str] = None) -> Tuple[bool, frozenset]:
    """(whether the egress pragma skips a line, the network programs the policy adds)."""
    policy = load(cwd)
    values = policy.values
    allow = values.get("allow_pragma", True)
    programs = frozenset(values.get("network_programs", ()))
    if not allow:
        policy.raised["egress.allow_pragma"] = "false"
    if programs:
        shown = ", ".join(sorted(programs)[:NOTE_ITEMS])
        policy.raised["egress.network_programs"] = f"adds {shown}"
    return allow, programs


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def sources(policy: Policy) -> str:
    """The policy's files as a note shows them: each path ascii(), nearest first."""
    return " and ".join(_path_shown(p) for p in (policy.paths or [policy.path]) if p)


def raised(cwd: Optional[str] = None) -> Dict[str, str]:
    """What the policy raised so far in this process: "<section>.<key>" -> what it set."""
    return dict(load(cwd).raised)


def ignored(cwd: Optional[str] = None) -> List[str]:
    """Everything the policy holds that was not applied: read-time and merge-time lines."""
    policy = load(cwd)
    return list(policy.ignored) + list(policy.loosening)


def refusal_note(prefixes, cwd: Optional[str] = None) -> str:
    """' Team policy <path> set <key> to <value>; ignored in it: ...' for the keys under
    `prefixes` (e.g. ("modes.commit_rule", "commit_rule.")) that the policy raised in this
    process, or '' when it raised none of them. A refusal appends it so nobody is blocked by a
    committed file without being told which file and which key."""
    try:
        policy = load(cwd)
        keys = [k for k in policy.raised if any(k.startswith(p) for p in prefixes)]
        if not policy.path or not keys:
            return ""
        note = f" Team policy {sources(policy)} set "
        note += "; ".join(f"{k} {policy.raised[k]}" for k in keys)
        skipped = ignored(cwd)
        if skipped:
            more = len(skipped) - NOTE_ITEMS
            note += "; ignored in it: " + "; ".join(skipped[:NOTE_ITEMS])
            note += f"; and {more} more (repact-doctor --policy)" if more > 0 else ""
        if len(note) > NOTE_MAX:
            note = note[:NOTE_MAX - 30] + " ... (repact-doctor --policy)"
        return note + "."
    except Exception:   # noqa: BLE001 - a note must never break the refusal it explains
        return ""
