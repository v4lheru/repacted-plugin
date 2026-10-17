"""The output check on model candidates for repact-distill (KB lite layer R3). Fails closed.

Location: pact-plugin/bin/_distill/postcheck.py. The backend unit (U5.4) runs every
candidate through check_candidate() (or filter_candidates() for a batch) before it is spooled
or proposed to KB-GATE: `refuse` drops it, `hold` sends it to review and never auto-accepts it,
`accept` lets KB-GATE apply its own rules.

refuse: any string in the candidate (keys included, and all of them joined so a value split
across fields is seen) carries a credential (shared/scrub credential_reason, D25) or anything
redact.residual_kinds() still detects (split or encoded secrets, PII, denylist and identity
names through the leak gate's views); a value that is not plain JSON, too large or too deep;
a broken denylist or missing views; any error in this module.
hold: a capitalised proper noun outside code that is not on the tech-name allowlist (the
specificity guard: unpatterned client or employer names); a lesson phrased as an instruction to
the agent, or carrying prompt-injection phrasing; a redaction marker the model copied.
Reasons are kind names, never values.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Optional

import _repact_common as common
from _distill import redact
from shared import scrub

ACCEPT, HOLD, REFUSE = "accept", "hold", "refuse"
MAX_CHARS = 64 * 1024
MAX_DEPTH = 16
MAX_NODES = 10_000

# Lowercase. Tech, tooling and framework words that are capitalised in ordinary lessons.
TECH_NAMES = frozenset("""
python postgres postgresql supabase sqlite mysql mariadb redis docker kubernetes helm terraform
git github gitlab bash zsh shell linux macos ubuntu debian windows unix posix node nodejs deno
typescript javascript react next nextjs vite vitest pytest jest playwright npm pnpm yarn uv pip
json jsonl yaml toml csv html css sql http https api apis rest graphql cli ci cd url urls uri ssh
tls ssl jwt oauth utc iso utf ascii unicode nfkc nfkd regex claude anthropic opus sonnet haiku
fable openai gpt gemini google gcp aws azure slack telegram n8n jira confluence obsidian markdown
vscode chrome safari firefox pact repacted handoff handoffs teachback kb mcp llm ai ui ux id ids
os io db orm rls fts sdk env pr prs mr todo readme changelog license ok true false null none
monday tuesday wednesday thursday friday saturday sunday january february march april may june
july august september october november december sqlalchemy pydantic fastapi flask django asyncio
asyncpg drizzle prisma tailwind astra fetch stdin stdout stderr dns ip ipv4 ipv6 tcp udp yes no
""".split())

_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
_NOT_PROSE = re.compile(r"https?://\S+|\S*[/\\]\S*|\S+\.\S+\.\S+|\[REDACTED:[^\]]*\]")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'_-]*")
_SENTENCE_END = ".!?:;\n"
_LEAD_IN = " \t\"'(*_#>-[|"
_DIRECTIVE = re.compile(
    r"^(?:always|never|you (?:must|should|need to)|from now on|do not|don't|make sure (?:to|you)"
    r"|be sure to|remember to|ignore|disregard|forget|stop)\b", re.I)
_INJECTION = (
    re.compile(r"\b(?:ignore|disregard|forget|override|bypass)\b[^.\n]{0,40}\b(?:instructions?"
               r"|rules?|prompts?|guidelines?|polic(?:y|ies)|guard(?:rail)?s?|safety)\b", re.I),
    re.compile(r"\b(?:you are now|new instructions?|system prompt)\b", re.I),
    re.compile(r"\b(?:(?:do not|don't|never) (?:tell|inform|alert|ask)|without (?:telling|asking"
               r"|informing)) (?:the )?(?:user|lead|human|owner)\b", re.I),
    re.compile(r"\b(?:exfiltrate|upload|post|send)\b[^.\n]{0,60}(?:https?://|webhook)", re.I),
    re.compile(r"\b(?:curl|wget)\b[^\n]{0,80}\|\s*(?:sh|bash|zsh)\b", re.I))


@dataclass(frozen=True)
class Verdict:
    action: str          # accept | hold | refuse
    reasons: tuple       # kind names, never values


class _NotJson(ValueError):
    """The candidate is not a plain, bounded JSON value."""


def profile_allow_names() -> tuple:
    """Extra names from profile distill.allow_names (a list of strings), lowercased."""
    distill = common.profile().get("distill")
    names = distill.get("allow_names") if isinstance(distill, dict) else None
    if not isinstance(names, list):
        return ()
    return tuple(name.strip().lower() for name in names if isinstance(name, str) and name.strip())


def _strings(value, depth: int = 0, seen: Optional[list] = None) -> list:
    """Every string in a JSON value, keys included. Raises _NotJson on anything else."""
    seen = [] if seen is None else seen
    if depth > MAX_DEPTH or len(seen) > MAX_NODES:
        raise _NotJson("too deep or too many nodes")
    if isinstance(value, str):
        seen.append(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _NotJson("a key is not a string")
            seen.append(key)
            _strings(item, depth + 1, seen)
    elif isinstance(value, list):
        for item in value:
            _strings(item, depth + 1, seen)
    elif isinstance(value, float) and not math.isfinite(value):
        raise _NotJson("a number is not finite")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise _NotJson(f"a {type(value).__name__} is not JSON")
    return seen


def _refusals(strings: list, lists: redact.Lists) -> list:
    reasons = []
    joined = "\n".join(strings)
    if any(scrub.credential_reason("candidate", text) for text in strings):
        reasons.append("secret")
    for kind in redact.residual_kinds(joined, lists):
        if kind not in reasons:
            reasons.append(kind)
    return reasons


def _proper_nouns(text: str, allowed: frozenset) -> bool:
    """True when prose holds a capitalised word that is not sentence-initial, not on the allowlist
    and not identifier-shaped (a digit or an underscore in it)."""
    prose = _NOT_PROSE.sub(" ", _CODE.sub(" ", text))
    for match in _WORD.finditer(prose):
        word = match.group(0).strip("'-")
        if len(word) < 2 or not word[0].isupper() or any(c.isdigit() or c == "_" for c in word):
            continue
        before = prose[:match.start()].rstrip(_LEAD_IN)
        if not before or before[-1] in _SENTENCE_END:
            continue
        if word.lower() not in allowed and word.lower().rstrip("s") not in allowed:
            return True
    return False


def _sentences(text: str) -> list:
    return [part.strip(_LEAD_IN) for part in re.split(r"(?<=[.!?:;])\s+|\n+", text) if part.strip()]


def _holds(strings: list, allowed: frozenset) -> list:
    reasons = []
    if any(redact.MARKER in text for text in strings):
        reasons.append("redaction marker")
    if any(_proper_nouns(text, allowed) for text in strings):
        reasons.append("proper noun")
    if any(_DIRECTIVE.match(sentence) for text in strings for sentence in _sentences(text)):
        reasons.append("imperative")
    if any(regex.search(text) for regex in _INJECTION for text in strings):
        reasons.append("injection phrasing")
    return reasons


def check_candidate(candidate, lists: Optional[redact.Lists] = None,
                    allow_names: Iterable[str] = ()) -> Verdict:
    """The R3 verdict on one candidate. Never raises: any error refuses."""
    try:
        strings = _strings(candidate)
        if sum(len(text) for text in strings) > MAX_CHARS:
            return Verdict(REFUSE, ("too large",))
        lists = lists if lists is not None else redact.load_lists()
        refused = _refusals(strings, lists)
        if refused:
            return Verdict(REFUSE, tuple(refused))
        allowed = TECH_NAMES | {name.lower() for name in allow_names}
        held = _holds(strings, frozenset(allowed))
        return Verdict(HOLD, tuple(held)) if held else Verdict(ACCEPT, ())
    except _NotJson:
        return Verdict(REFUSE, ("not json",))
    except redact.ListsBroken:
        return Verdict(REFUSE, (redact.SKIP_DENYLIST,))
    except redact.ViewsUnavailable:
        return Verdict(REFUSE, (redact.SKIP_VIEWS,))
    except Exception as exc:  # fail closed: a check that cannot run refuses
        return Verdict(REFUSE, (f"check error ({type(exc).__name__})",))


def filter_candidates(candidates: Iterable) -> list:
    """[(candidate, Verdict)] for a batch, with the lists and allow names read once. A failure to
    read them refuses every candidate."""
    try:
        lists = redact.load_lists()
        allow = profile_allow_names()
    except Exception as exc:  # fail closed for the whole batch
        reason = redact.SKIP_DENYLIST if isinstance(exc, redact.ListsBroken) \
            else f"check error ({type(exc).__name__})"
        return [(candidate, Verdict(REFUSE, (reason,))) for candidate in candidates]
    return [(candidate, check_candidate(candidate, lists, allow)) for candidate in candidates]
