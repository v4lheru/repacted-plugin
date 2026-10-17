"""Skill rule bullets and a local TF-IDF shortlist over them (stdlib only, no network).

Location: pact-plugin/bin/rule_match.py. Used by distill-inbox (plan --suggest): the
5 skill rules closest to an inbox lesson, as a hint the model reads before it decides whether
the lesson duplicates one.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# A rule is one `- **<ID>** text` bullet in a skill's references/*.md.
RULE_LINE = re.compile(r"^- \*\*([A-Z][A-Za-z0-9]*\.\d+)\*\* (.+)$", re.M)
WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Rule:
    file: str       # skill-relative path, e.g. verification-discipline/references/vd-a1-....md
    rule_id: str    # e.g. A1.46; ids alone collide across skills
    text: str

    @property
    def label(self) -> str:
        return f"{self.file}#{self.rule_id}"


def extract_rules(skills_dir: Path) -> list[Rule]:
    """Every id-marked rule bullet, in sorted-path then in-file order (the measured order)."""
    rules: list[Rule] = []
    for path in sorted(Path(skills_dir).rglob("references/*.md")):
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(skills_dir))
        rules.extend(Rule(relative, match.group(1), match.group(2)) for match in RULE_LINE.finditer(text))
    return rules


def _tokens(text: str) -> list[str]:
    return WORD.findall(text.lower())


def tfidf_top(lesson: str, rules: list[Rule], top: int) -> list[dict]:
    """Local TF-IDF cosine shortlist: the `top` rules closest to `lesson`."""
    documents = [Counter(_tokens(rule.text)) for rule in rules]
    document_frequency = Counter(term for document in documents for term in document)
    total = len(documents)

    def vector(counts: Counter) -> dict:
        return {term: count * math.log((1 + total) / (1 + document_frequency.get(term, 0)))
                for term, count in counts.items()}

    query = vector(Counter(_tokens(lesson)))
    query_norm = math.sqrt(sum(value * value for value in query.values())) or 1.0
    scored = []
    for rule, document in zip(rules, documents):
        weights = vector(document)
        dot = sum(weight * query.get(term, 0.0) for term, weight in weights.items())
        norm = math.sqrt(sum(value * value for value in weights.values())) or 1.0
        scored.append((dot / (norm * query_norm), rule))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"rule": rule.label, "file": rule.file, "score": round(score, 3)}
            for score, rule in scored[:top]]
