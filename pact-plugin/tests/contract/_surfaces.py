"""Shared helpers for the contract checks (repacted-fork.md §8, build plan §3 W0-INFRA).

A LOADED SURFACE is prose the harness puts in front of a model: every agent body, command, protocol
and skill entry file. Reference files under skills/*/references/ load only on demand and are not
counted here.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
AGENTS = PLUGIN_ROOT / "agents"
ORCHESTRATOR = AGENTS / "pact-orchestrator.md"

AGENT_LISTINGS = 2   # each agent is listed as pact-<name> and repacted:pact-<name> until M10 (build plan C1)


def rel(path: Path, root: Path = PLUGIN_ROOT) -> str:
    return path.relative_to(root).as_posix()


def loaded_surfaces(root: Path = PLUGIN_ROOT) -> list[Path]:
    paths = [*root.glob("agents/*.md"), *root.glob("commands/*.md"),
             *root.glob("protocols/*.md"), *root.glob("skills/*/SKILL.md")]
    return sorted(p for p in paths if p.is_file())


def python_modules(root: Path = PLUGIN_ROOT) -> list[Path]:
    """Every shipped Python module: *.py under hooks/, bin/, telegram/, scripts/ and skills/*/scripts/,
    plus the extensionless bin/ entry points whose first line is a python shebang."""
    paths = [p for d in ("hooks", "bin", "telegram", "scripts") for p in (root / d).rglob("*.py")]
    paths += root.glob("skills/*/scripts/**/*.py")
    paths += [p for p in (root / "bin").iterdir() if p.is_file() and not p.suffix
              and re.match(rb"#![^\n]*python", p.read_bytes()[:80])]
    return sorted(p for p in paths if p.is_file() and "__pycache__" not in p.parts)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def frontmatter(text: str) -> dict[str, str]:
    """Top-level `key: value` pairs; a `|` or `>` block scalar is joined into one line."""
    match = _FRONTMATTER.match(text)
    if not match:
        return {}
    out: dict[str, str] = {}
    key = None
    for line in match.group(1).splitlines():
        if line.startswith((" ", "\t")) and key:
            out[key] = (out[key] + " " + line.strip()).strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            out[key] = "" if value in ("|", ">", "|-", ">-") else value.strip("'\"")
    return out


def body(text: str) -> str:
    match = _FRONTMATTER.match(text)
    return text[match.end():] if match else text


def description_chars(path: Path) -> int:
    return len(frontmatter(read(path)).get("description", ""))


def model_invocable(path: Path) -> bool:
    return frontmatter(read(path)).get("disable-model-invocation", "").lower() != "true"


def combined_catalog(root: Path = PLUGIN_ROOT) -> dict[str, int]:
    """Catalog characters as repacted-fork.md §3 counts them (the same definition as
    tests/user_layer/test_catalog_budget.py at the repo root, which enforces the TARGET)."""
    parts = {
        "skills": sum(description_chars(p) for p in root.glob("skills/*/SKILL.md")),
        "commands": sum(description_chars(p) for p in root.glob("commands/*.md") if model_invocable(p)),
        "agents": AGENT_LISTINGS * sum(description_chars(p) for p in root.glob("agents/*.md")),
    }
    parts["total"] = sum(parts.values())
    return parts


def headings(text: str) -> list[str]:
    """Markdown heading lines (stripped), ignoring fenced code."""
    out, fenced = [], False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and re.match(r"#{1,6} ", stripped):
            out.append(stripped)
    return out
