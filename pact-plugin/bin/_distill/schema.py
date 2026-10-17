"""The candidate schema for repact-distill: the JSON schema sent to the model and our own check.

Location: pact-plugin/bin/_distill/schema.py. backend.py passes OUTPUT_SCHEMA to
`claude -p --json-schema`; bin/repact-distill runs validate_output() on what comes back, because
a schema given to the model is a request, not a guarantee. prompts.py fingerprints the schema.

A candidate (plan contract 12, with why/apply/summary added for KB-GATE's lesson grammar):
  {kind, title, claim, why, apply, summary, applies_to, versions,
   evidence: [{type: session|task|memory, ref, ts}], confidence: verified|stated|inferred, scope}
Evidence is a pointer, never a quote: each ref must be one of the `--- <ref> <ts>` headers of
the chunk the model was sent (provenance), and its type must match the ref's prefix. The header's
ts replaces the model's, so a date is never taken from model output.
Anything else (an extra key, a wrong type, a length past its cap, a ref not in the chunk) makes
that candidate invalid: it is dropped and counted by reason, never repaired.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

KINDS = ("gotcha", "entity", "pattern", "architecture", "synthesis")     # vault-note KIND_PATHS
CONFIDENCE = ("verified", "stated", "inferred")
SCOPES = ("general", "project")
EVIDENCE_TYPES = ("session", "task", "memory")
MAX_CANDIDATES = 8
MAX_EVIDENCE = 5
KEYS_MIN, KEYS_MAX = 3, 8
VERSIONS_MAX = 5
# (min, max) characters per text field; the title and summary caps are KB-GATE's own.
TEXT_LIMITS = {"title": (8, 90), "claim": (20, 400), "why": (10, 300), "apply": (10, 300),
               "summary": (10, 160)}
VERSION_MAX_CHARS = 40
REF_MAX_CHARS = 200
KEY_PHRASE = r"^[a-z0-9][a-z0-9.+#/_-]*(?: [a-z0-9][a-z0-9.+#/_-]*){1,4}$"   # KB-GATE KEY_PHRASE
_KEY_RE = re.compile(KEY_PHRASE)
_HEADER_RE = re.compile(r"^--- (\S+)(?: (\S*))?$", re.M)
_FORBIDDEN_TITLE = re.compile(r"[()*\n]")

CANDIDATE_FIELDS = ("kind", "title", "claim", "why", "apply", "summary", "applies_to", "versions",
                    "evidence", "confidence", "scope")


def _text(name: str) -> dict:
    low, high = TEXT_LIMITS[name]
    return {"type": "string", "minLength": low, "maxLength": high}


OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {"candidates": {"type": "array", "maxItems": MAX_CANDIDATES, "items": {
        "type": "object",
        "additionalProperties": False,
        "required": list(CANDIDATE_FIELDS),
        "properties": {
            "kind": {"type": "string", "enum": list(KINDS)},
            "title": _text("title"), "claim": _text("claim"), "why": _text("why"),
            "apply": _text("apply"), "summary": _text("summary"),
            "applies_to": {"type": "array", "minItems": KEYS_MIN, "maxItems": KEYS_MAX,
                           "items": {"type": "string", "pattern": KEY_PHRASE}},
            "versions": {"type": "array", "maxItems": VERSIONS_MAX,
                         "items": {"type": "string", "maxLength": VERSION_MAX_CHARS}},
            "evidence": {"type": "array", "minItems": 1, "maxItems": MAX_EVIDENCE, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["type", "ref", "ts"],
                "properties": {"type": {"type": "string", "enum": list(EVIDENCE_TYPES)},
                               "ref": {"type": "string", "maxLength": REF_MAX_CHARS},
                               "ts": {"type": "string", "maxLength": 40}}}},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
            "scope": {"type": "string", "enum": list(SCOPES)},
        }}}},
}


class Invalid(ValueError):
    """A candidate breaks the schema; the message is a reason kind, never a value."""


def chunk_refs(chunk_text: str, raw_text: Optional[str] = None) -> dict:
    """{ref: ts} for every `--- <ref> <ts>` segment header in the text the model was sent. With
    `raw_text` (the chunk before redaction), only a header present with the same ts in BOTH: a
    note line that redaction's folding turned into a header (fullwidth dashes, a ZWSP) is no ref."""
    sent = {match.group(1): match.group(2) or "" for match in _HEADER_RE.finditer(chunk_text)}
    if raw_text is None:
        return sent
    raw = {match.group(1): match.group(2) or "" for match in _HEADER_RE.finditer(raw_text)}
    return {ref: ts for ref, ts in sent.items() if raw.get(ref) == ts}


def _check_text(candidate: dict, name: str) -> None:
    value = candidate[name]
    low, high = TEXT_LIMITS[name]
    if not isinstance(value, str) or not low <= len(value.strip()) <= high:
        raise Invalid(f"{name}: not a string of {low}-{high} characters")


def _check_list(value, name: str, low: int, high: int) -> list:
    if not isinstance(value, list) or not low <= len(value) <= high:
        raise Invalid(f"{name}: not a list of {low}-{high} items")
    return value


def _check_evidence(items, refs: dict) -> list:
    out = []
    for item in _check_list(items, "evidence", 1, MAX_EVIDENCE):
        if not isinstance(item, dict) or set(item) != {"type", "ref", "ts"}:
            raise Invalid("evidence: an item does not have exactly type, ref and ts")
        kind, ref = item["type"], item["ref"]
        if kind not in EVIDENCE_TYPES or not isinstance(ref, str) or not isinstance(item["ts"], str):
            raise Invalid("evidence: a wrong type")
        if ref not in refs:
            raise Invalid("evidence: a ref that is not in the chunk")
        if not ref.startswith(f"{kind}:"):
            raise Invalid("evidence: the type does not match the ref")
        out.append({"type": kind, "ref": ref, "ts": refs[ref]})
    return out


def _grounded(key: str, candidate: dict) -> bool:
    """KB-GATE's key rule (gate.check_keys): the key occurs as whole words in the lowercased,
    whitespace-collapsed title and claim. Checked here so the gate never refuses for it."""
    haystack = " ".join(f"{candidate['title']} {candidate['claim']}".lower().split())
    return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack) is not None


def validate_candidate(candidate, refs: dict) -> dict:
    """The candidate, normalised (evidence ts from the chunk headers), or Invalid."""
    if not isinstance(candidate, dict):
        raise Invalid("not an object")
    keys = set(candidate)
    if keys - set(CANDIDATE_FIELDS):
        raise Invalid("extra keys")
    if set(CANDIDATE_FIELDS) - keys:
        raise Invalid("missing keys")
    for name in TEXT_LIMITS:
        _check_text(candidate, name)
    if _FORBIDDEN_TITLE.search(candidate["title"]):
        raise Invalid("title: parentheses, asterisks or a newline")
    for name, allowed in (("kind", KINDS), ("confidence", CONFIDENCE), ("scope", SCOPES)):
        if candidate[name] not in allowed:
            raise Invalid(f"{name}: not one of the allowed values")
    applies = _check_list(candidate["applies_to"], "applies_to", KEYS_MIN, KEYS_MAX)
    if not all(isinstance(key, str) and _KEY_RE.match(key) for key in applies):
        raise Invalid("applies_to: an item is not 2-5 lowercase words")
    if not all(_grounded(key, candidate) for key in applies):
        raise Invalid("applies_to: an item is not word for word in the title or claim")
    versions = _check_list(candidate["versions"], "versions", 0, VERSIONS_MAX)
    if not all(isinstance(v, str) and len(v) <= VERSION_MAX_CHARS for v in versions):
        raise Invalid("versions: an item is not a short string")
    return dict(candidate, evidence=_check_evidence(candidate["evidence"], refs))


def validate_output(output, chunk_text: str, raw_text: Optional[str] = None) -> tuple:
    """(valid candidates, Counter of invalid reasons) for one backend result. An output that is
    not {"candidates": [...]} counts as one invalid 'output shape' and yields nothing. Evidence
    refs come from chunk_refs(chunk_text, raw_text)."""
    invalid: Counter = Counter()
    if not isinstance(output, dict) or set(output) != {"candidates"} \
            or not isinstance(output["candidates"], list):
        invalid["output shape"] += 1
        return [], invalid
    items = output["candidates"]
    if len(items) > MAX_CANDIDATES:
        invalid["too many candidates"] += len(items) - MAX_CANDIDATES
        items = items[:MAX_CANDIDATES]
    refs = chunk_refs(chunk_text, raw_text)
    valid = []
    for candidate in items:
        try:
            valid.append(validate_candidate(candidate, refs))
        except Invalid as exc:
            invalid[str(exc)] += 1
    return valid, invalid
