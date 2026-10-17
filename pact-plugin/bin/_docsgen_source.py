"""_docsgen_source: what ships, read as it ships, for the reference generator.

Location: pact-plugin/bin/_docsgen_source.py. Used by bin/_docsgen.py (the page
renderer behind bin/repact-docs-gen) and nothing else.

The generator documents the plugin tree it runs in. Two modes, chosen by what is on disk:

    public   no export tooling beside the plugin (the public repo, a staged export, an
             install): every file is read as it is.
    private  the plugin sits at <repo>/repacted/pact-plugin and <repo>/tools/public-export/
             holds the export manifests: each path is classified with the exporter's own
             manifest code. `exclude` and unclassified paths read as absent, `transform`
             paths read through the exporter's substitution and line-drop rules, so the
             text equals what the public tree holds and a public `--check` sees no diff.

Python sources the generator reads values from (the rule registry, the config registry) are
executed from that same shipped text under a private module name, never imported as
`shared.*`, so a public run and a private run read the same bytes.

Example:
    tree = ShippedTree(Path(plugin_root))
    text = tree.text("commands/init.md")        # None when the file does not ship
"""

from __future__ import annotations

import os
import re
import sys
import types
from pathlib import Path
from typing import List, Optional

EXPORT_DIR = ("tools", "public-export")
PRIVATE_PARENT = "repacted"


class SourceError(RuntimeError):
    """The tree cannot be read as it ships (a manifest problem or an unexpected action)."""


class ShippedTree:
    """The plugin tree as it ships: `text`, `exists` and `listdir` hide what is excluded."""

    def __init__(self, plugin_root: Path):
        self.root = Path(plugin_root).resolve()
        self._export = _find_export_dir(self.root)
        self._actions = {}
        self._subs, self._drops = [], []
        if self._export is not None:
            self._load_export()

    @property
    def mode(self) -> str:
        return "private" if self._export is not None else "public"

    def ships(self, rel: str) -> bool:
        """True when `rel` (relative to the plugin root) is a file that ships."""
        if not (self.root / rel).is_file():
            return False
        if self._export is None:
            return True
        return self._action(rel) not in ("exclude", None)

    def text(self, rel: str) -> Optional[str]:
        """The file's text as it ships, or None when it does not ship."""
        if not self.ships(rel):
            return None
        raw = (self.root / rel).read_text(encoding="utf-8")
        action = self._action(rel) if self._export is not None else "include"
        if action == "include":
            return raw
        if action == "transform":
            return self._transforms.apply_transforms(self._manifest_path(rel), raw,
                                                     self._subs, self._drops).text
        raise SourceError(f"{rel}: export action {action!r} is not a docs input")

    def listdir(self, rel_dir: str) -> List[str]:
        """Sorted entry names under `rel_dir` (files and directories), hidden or not."""
        directory = self.root / rel_dir
        return sorted(os.listdir(directory)) if directory.is_dir() else []

    def is_executable(self, rel: str) -> bool:
        return os.access(self.root / rel, os.X_OK)

    def load_module(self, rel: str, name: str) -> types.ModuleType:
        """Execute the shipped text of a Python leaf module under a private name."""
        source = self.text(rel)
        if source is None:
            raise SourceError(f"{rel}: does not ship, so the pages cannot be generated")
        module = types.ModuleType(name)
        module.__file__ = str(self.root / rel)
        sys.modules[name] = module      # dataclasses resolve their module through sys.modules
        try:
            exec(compile(source, str(self.root / rel), "exec"), module.__dict__)
        finally:
            sys.modules.pop(name, None)
        return module

    # --- private mode ------------------------------------------------------------------

    def _manifest_path(self, rel: str) -> str:
        return f"{self.root.name}/{rel}"

    def _action(self, rel: str) -> Optional[str]:
        key = self._manifest_path(rel)
        if key not in self._actions:
            result = self._manifest.classify(self._fragments, [key])
            if result.conflicts or result.invalid:
                raise SourceError("; ".join(result.conflicts + result.invalid))
            rule = result.assignments.get(key)
            if rule is None:
                print(f"repact-docs-gen: {key} is unclassified, so it is left out",
                      file=sys.stderr)
            self._actions[key] = rule.action if rule is not None else None
        return self._actions[key]

    def _load_export(self) -> None:
        tool_dir = str(self._export)
        if tool_dir not in sys.path:
            sys.path.insert(0, tool_dir)
        try:
            import manifest as manifest_module
            import transforms as transforms_module
        except ImportError as error:
            raise SourceError(f"export tooling at {tool_dir} did not load: {error}") from error
        self._manifest, self._transforms = manifest_module, transforms_module
        try:
            self._fragments = manifest_module.load_manifest(self._export / "manifest.d")
            self._subs = transforms_module.load_rules(self._export / "substitutions.d",
                                                      "substitution", self._fragments)
            self._drops = transforms_module.load_rules(self._export / "linedrop.d",
                                                       "linedrop", self._fragments)
        except (manifest_module.ManifestError, transforms_module.RuleError) as error:
            raise SourceError(f"export manifest: {error}") from error


def _find_export_dir(plugin_root: Path) -> Optional[Path]:
    """<repo>/tools/public-export when the plugin is the private <repo>/repacted/<plugin>."""
    if plugin_root.parent.name != PRIVATE_PARENT:
        return None
    candidate = plugin_root.parent.parent.joinpath(*EXPORT_DIR)
    if (candidate / "manifest.d").is_dir() and (candidate / "manifest.py").is_file():
        return candidate
    return None


# --- frontmatter and docstrings --------------------------------------------------------

_BLOCK = re.compile(r"^[>|][+-]?$")


def frontmatter(text: str) -> dict:
    """The `key: value` pairs of a Markdown file's leading `---` block (a YAML subset:
    plain, single- or double-quoted scalars and `>`/`|` blocks); {} when there is none."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    fields, key, block = {}, None, []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if key is not None and (line.startswith((" ", "\t")) or not line.strip()):
            block.append(line.strip())
            continue
        if key is not None:
            fields[key], key, block = " ".join(b for b in block if b), None, []
        name, sep, value = line.partition(":")
        if not sep or not name.strip() or name.startswith((" ", "\t")):
            continue
        value = value.strip()
        if _BLOCK.match(value):
            key, block = name.strip(), []
        else:
            fields[name.strip()] = _unquote(value)
    if key is not None:
        fields[key] = " ".join(b for b in block if b)
    return fields


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def script_summary(text: str) -> str:
    """The first paragraph of a script's module docstring or leading `#` comment."""
    lines = text.split("\n")
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    if not lines:
        return ""
    first = lines[0].lstrip()
    paragraph = []
    if first.startswith(('"""', "'''")):
        quote = first[:3]
        lines = [first[3:]] + lines[1:]
        for line in lines:
            done = quote in line
            line = line.split(quote, 1)[0]
            if not line.strip():
                if paragraph or done:
                    break
                continue
            paragraph.append(line.strip())
            if done:
                break
    else:
        for line in lines:
            if not line.startswith("#"):
                break
            body = line.lstrip("#").strip()
            if not body:
                if paragraph:
                    break
                continue
            paragraph.append(body)
    return " ".join(paragraph)
