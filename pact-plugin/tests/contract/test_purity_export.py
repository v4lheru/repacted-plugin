"""Export mode of the purity scan: the public-release leak gate (G3, G5) on a staged tree.

Location: pact-plugin/tests/contract/test_purity_export.py. The engine is _purity.py
(export_scan, scan_metadata, export_compare, export_context_from_env). Case ids L3-L13, L17, L18
are the verification note's leak-gate rows; L1, L2, L14-L16, L19 and L20 test the exporter
(manifest, overrides, review ledger) and live with it.

Every name here is SYNTHETIC and built in tmp dirs; the real denylist never enters this file.
Each refusal has a clean twin that passes, so a broken fixture cannot pass as a refusal.

Counter-test by revert (2026-10-16, one check stubbed per run; red rows): fold -> lower(): 13 L7;
joined view empty: 11 L8, 1 L3, 1 L9, the large-file row; decoded view empty: 10 L9, 1 L10;
no path hits: 4 L3; walk limited to git ls-files: 2 L4, 1 L5, 1 L6; reads without the NUL/UTF-8
check: 6 L5; no default symlink allowlist: 1 L6; require_export a no-op: 7 L11/L12, 1 L10;
ZERO_TOLERANCE empty: 1 L13.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from tests.contract import _purity as P

NAME = "Zorvanex"                 # a synthetic client name, 8 characters
SHORT = "Vexo"                    # below JOIN_MIN: never matched across a split
WORD = "quillet"                  # a word-list term (whole words only)
IDENTITY = "Pella Marsk"          # a synthetic profile identity
EMPLOYER = "Acmecorp"             # a synthetic export-only name (substring)
PROJECT = "Lantern"               # a synthetic export-only whole-word name
CLEAN = "the deploy notes for the staging cluster"


def _ctx(**over):
    base = dict(mode="full", denylist=[NAME, SHORT], words=[WORD],
                profile={"identity": {"name": IDENTITY}}, export_names=[EMPLOYER],
                export_words=[PROJECT])
    base.update(over)
    return P.Context(**base)


def _personal(hits):
    return [hit for hit in hits if hit.cls == "personal"]


def _stage(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if isinstance(body, bytes) else body.encode("utf-8"))
    return root


def _text_hits(text, rel="notes/a.md", ctx=None):
    return _personal(P.scan_export_text(rel, text, ctx or _ctx()))


# ---------------------------------------------------------------------------------------------
# L3: a name in a path
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("rel", [f"skills/{NAME.lower()}-infra/SKILL.md",
                                 f"docs/{NAME.upper()}.md",
                                 "skills/zor-vanex/SKILL.md"])
def test_l3_a_name_in_a_file_path_is_reported_on_line_0(tmp_path, rel):
    hits = P.export_scan(_stage(tmp_path, {rel: CLEAN}), _ctx())
    assert [(h.line, h.cls) for h in hits if h.path == rel] == [(0, "personal")]


def test_l3_a_name_in_an_empty_directory_name_is_reported(tmp_path):
    (tmp_path / "notes" / f"{NAME}-archive").mkdir(parents=True)
    hits = P.export_scan(tmp_path, _ctx())
    assert [h.path for h in _personal(hits)] == [f"notes/{NAME}-archive"]


def test_l3_clean_paths_pass(tmp_path):
    assert P.export_scan(_stage(tmp_path, {"skills/plain-infra/SKILL.md": CLEAN}), _ctx()) == []


# ---------------------------------------------------------------------------------------------
# L4: untracked files in the staged tree
# ---------------------------------------------------------------------------------------------

def test_l4_an_untracked_file_is_scanned_and_git_internals_are_not(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _stage(tmp_path, {"README.md": CLEAN, "notes/new.md": f"see {NAME}"})
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md"], check=True)
    assert "notes/new.md" not in P.core_files(tmp_path)          # the old route never saw it
    hits = P.export_scan(tmp_path, _ctx())
    assert [(h.path, h.cls) for h in hits] == [("notes/new.md", "personal")]


def test_l4_a_nested_git_directory_is_scanned(tmp_path):
    _stage(tmp_path, {"vendor/.git/config": f"[remote] url = {NAME}"})
    assert [h.path for h in _personal(P.export_scan(tmp_path, _ctx()))] == ["vendor/.git/config"]


# ---------------------------------------------------------------------------------------------
# L5: binary and unreadable files fail closed
# ---------------------------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR" + b"\x00" * 13 + b"tEXtAuthor\x00" + NAME.encode()


@pytest.mark.parametrize("body", [PNG, f"hello {NAME}".encode("utf-16"),
                                  b"x" * 9000 + b"\x00" + NAME.encode(),
                                  "caf\xe9 latin-1".encode("latin-1")],
                         ids=["png", "utf-16", "nul-after-8k", "not-utf-8"])
def test_l5_a_non_text_file_is_a_finding(tmp_path, body):
    hits = P.export_scan(_stage(tmp_path, {"assets/f.bin": body}), _ctx())
    assert [(h.path, h.cls) for h in hits] == [("assets/f.bin", "export/binary")]


def test_l5_a_binary_passes_only_with_its_sha256_on_the_allowlist(tmp_path):
    import hashlib
    _stage(tmp_path, {"assets/logo.png": PNG})
    right = {"assets/logo.png": hashlib.sha256(PNG).hexdigest()}
    wrong = {"assets/logo.png": hashlib.sha256(PNG + b"x").hexdigest()}
    assert P.export_scan(tmp_path, _ctx(), binary_allowlist=right) == []
    assert [h.cls for h in P.export_scan(tmp_path, _ctx(), binary_allowlist=wrong)] == ["export/binary"]


def test_l5_oversize_empty_and_unreadable_files(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "MAX_EXPORT_BYTES", 1024)
    _stage(tmp_path, {"big.md": "a" * 2048, "empty.md": "", "locked.md": CLEAN})
    os.chmod(tmp_path / "locked.md", 0)
    try:
        found = {(h.path, h.cls) for h in P.export_scan(tmp_path, _ctx())}
    finally:
        os.chmod(tmp_path / "locked.md", 0o644)
    expected = {("big.md", "export/oversize")}
    if os.geteuid() != 0:                                         # root reads a 000 file
        expected.add(("locked.md", "export/unreadable"))
    assert found == expected


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_l5_a_special_file_is_a_finding(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    assert [(h.path, h.cls) for h in P.export_scan(tmp_path, _ctx())] == [("pipe", "export/special")]


# ---------------------------------------------------------------------------------------------
# L6: symlinks
# ---------------------------------------------------------------------------------------------

def test_l6_only_the_allowlisted_license_link_passes(tmp_path):
    _stage(tmp_path, {"LICENSE": "MIT", "pact-plugin/README.md": CLEAN, "docs/a.md": CLEAN})
    (tmp_path / "pact-plugin" / "LICENSE").symlink_to("../LICENSE")
    assert P.export_scan(tmp_path, _ctx()) == []
    (tmp_path / "pact-plugin" / "docs").symlink_to("../docs")
    assert [(h.path, h.cls) for h in P.export_scan(tmp_path, _ctx())] == [
        ("pact-plugin/docs", "export/symlink")]


def test_l6_a_listed_link_escaping_the_tree_or_naming_a_private_path_fails(tmp_path):
    root = tmp_path / "public"
    _stage(root, {"README.md": CLEAN})
    (root / "out").symlink_to("../../elsewhere")
    (root / "named").symlink_to(f"../{NAME}/notes")
    hits = P.export_scan(root, _ctx(), symlink_allowlist={"out": "../../elsewhere"})
    assert {(h.path, h.cls) for h in hits} == {("out", "export/symlink"), ("named", "export/symlink"),
                                              ("named", "personal")}


# ---------------------------------------------------------------------------------------------
# L7: Unicode look-alikes and invisible characters
# ---------------------------------------------------------------------------------------------

UNICODE_FORMS = {
    "cyrillic a": NAME.replace("a", "\u0430"),
    "greek capital zeta": "\u0396" + NAME[1:],
    "cyrillic o and e": NAME.replace("o", "\u043e").replace("e", "\u0435"),
    "greek omicron": NAME.replace("o", "\u03bf"),
    "fullwidth": "".join(chr(ord(c) + 0xFEE0) for c in NAME),
    "zero-width joiner": NAME[:4] + "\u200d" + NAME[4:],
    "zero-width space": NAME[:4] + "\u200b" + NAME[4:],
    "soft hyphen": NAME[:4] + "\u00ad" + NAME[4:],
    "word joiner and bom": NAME[:2] + "\u2060" + NAME[2:5] + "\ufeff" + NAME[5:],
    "combining accent": NAME[:3] + "\u0301" + NAME[3:],
    "precomposed accent": NAME.replace("a", "\u00e1"),
    "mathematical bold": "".join(chr(0x1D400 + ord(c) - 65) if c.isupper() else chr(0x1D41A + ord(c) - 97)
                                 for c in NAME),
}


@pytest.mark.parametrize("label", sorted(UNICODE_FORMS))
def test_l7_unicode_variants_are_caught_and_plain_scan_file_is_unchanged(label):
    text = f"line one\nreport for {UNICODE_FORMS[label]} today\n"
    hits = _text_hits(text)
    assert [h.line for h in hits] == [2], label
    assert not _personal(P.scan_file("notes/a.md", text, _ctx())), "CI scan_file behaviour changed"


def test_l7_the_identity_and_a_word_are_folded_too():
    assert [h.line for h in _text_hits("by P\u0435lla Mar\u200bsk")] == [1]
    cyrillic_word = WORD.upper().replace("I", "\u0406")
    assert [h.line for h in _text_hits(f"the {cyrillic_word} team")] == [1]
    assert _text_hits(f"{WORD}ian") == []                       # words stay whole-word


def test_l7_clean_unicode_text_passes():
    assert _text_hits("caf\u00e9 \u0437\u0434\u0440\u0430\u0432\u043e \uff21\uff22 \u200b ok") == []


# ---------------------------------------------------------------------------------------------
# L8: a name split by a line break or markup
# ---------------------------------------------------------------------------------------------

SPLITS = {"newline": "Zorva\nnex", "crlf": "Zorva\r\nnex", "bold": "Zorva**nex**",
          "underscore": "Zorva_nex", "backtick": "`Zorva`nex", "hyphen wrap": "Zorva-\nnex",
          "space": "Zorva nex", "blockquote wrap": "Zorva\n> nex", "comment wrap": "Zorva\n# nex",
          "strike": "~~Zorva~~nex", "table cell": "Zorva|nex"}


@pytest.mark.parametrize("label", sorted(SPLITS))
def test_l8_a_split_name_is_caught_on_its_first_line(label):
    hits = _text_hits(f"intro\n{SPLITS[label]} rest\n")
    assert hits and {h.line for h in hits} == {2}, label


def test_l8_short_entries_are_not_joined_but_still_caught_whole():
    assert _text_hits("Ve\nxo and Ve-xo") == []                   # below JOIN_MIN by design
    assert [h.line for h in _text_hits(f"ask {SHORT}")] == [1]


def test_l8_clean_prose_with_markup_passes():
    assert _text_hits("**Zorva** is not a\nname; *nex*t steps\n") == []


# ---------------------------------------------------------------------------------------------
# L9: encoded names
# ---------------------------------------------------------------------------------------------

def _b64(text, urlsafe=False):
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    return encode(text.encode()).decode()


ENCODED = {
    "url": "https://x.example/q?c=" + urllib.parse.quote(NAME, safe="").replace("Z", "%5A"),
    "url twice": urllib.parse.quote("a %5A" + NAME[1:], safe=""),
    "url split": "q=Zorva%20nex",
    "html decimal": "&#90;orvanex",
    "html hex": "Zorv&#x61;nex",
    "json u escape": '"\\u005aorvanex"',
    "python x escape": "'Zorv\\x61nex'",
    "base64 alone": _b64(NAME),
    "base64 sentence": _b64(f"Authorization for {NAME} staging, rotated monthly"),
    "base64 urlsafe": _b64(f"??>{NAME}??>", urlsafe=True),
}


@pytest.mark.parametrize("label", sorted(ENCODED))
def test_l9_an_encoded_name_is_caught_on_its_line(label):
    hits = _text_hits(f"one\ntwo\nlink {ENCODED[label]} end\n")
    assert hits and {h.line for h in hits} == {3}, label


def test_l9_clean_encodings_pass():
    text = "\n".join([_b64(CLEAN), "https://x.example/a%20b?c=%2Fd", "&amp; &#65;BC", '"\\u0041BC"',
                      "test_purity_export_module_identifier", "QWxhZGRpbjpvcGVuIHNlc2FtZQ=="])
    assert _text_hits(text) == []


# ---------------------------------------------------------------------------------------------
# L10: metadata goes through the same scan
# ---------------------------------------------------------------------------------------------

def test_l10_commit_metadata_is_scanned():
    ctx = _ctx()
    assert [h.path for h in _personal(P.scan_metadata("commit-message", f"Fix the {NAME} export", ctx))] \
        == ["<commit-message>"]
    assert _personal(P.scan_metadata("author", f"{IDENTITY} <dev@example.com>", ctx))
    assert _personal(P.scan_metadata("tag", "Release 1.8.0\n\n" + _b64(f"for {NAME}"), ctx))
    assert P.scan_metadata("commit-message", "Release 1.8.0 with clean notes", ctx) == []
    with pytest.raises(P.FailClosed):
        P.scan_metadata("commit-message", "x", _ctx(mode="public", denylist=[]))


# ---------------------------------------------------------------------------------------------
# L11, L12: the wrong mode or a missing identity fails closed
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("ctx", [_ctx(mode="public"), _ctx(denylist=[]), _ctx(denylist=["", "  "]),
                                 _ctx(profile={}), _ctx(profile={"identity": {"name": " "}}),
                                 _ctx(profile={"identity": "Pella"})],
                         ids=["public", "empty", "blank-entries", "no-identity", "blank-identity",
                              "identity-not-a-dict"])
def test_l11_l12_export_refuses_anything_but_a_full_context(tmp_path, ctx):
    _stage(tmp_path, {"a.md": CLEAN})
    with pytest.raises(P.FailClosed):
        P.export_scan(tmp_path, ctx)


def test_l11_the_public_escape_does_not_reach_an_export(tmp_path):
    env = {P.ENV_DIR: str(tmp_path), P.ENV_PUBLIC: "1"}          # an empty denylist folder
    assert P.context_from_env(env).mode == "public"              # the CI route still exists
    with pytest.raises(P.FailClosed):
        P.export_context_from_env(env)


def test_l11_a_full_env_with_an_identity_builds_an_export_context(tmp_path, monkeypatch):
    (tmp_path / P.DENYLIST_NAMES[0]).write_text(NAME + "\n")
    (tmp_path / P.DENYLIST_NAMES[1]).write_text(WORD + "\n")
    (tmp_path / P.EXPORT_LIST_NAMES[0]).write_text(EMPLOYER + "\n")
    (tmp_path / P.EXPORT_LIST_NAMES[1]).write_text(PROJECT + "\n")
    monkeypatch.setattr(P.common, "profile", lambda: {"identity": {"name": IDENTITY}})
    ctx = P.export_context_from_env({P.ENV_DIR: str(tmp_path), P.ENV_PUBLIC: "1"})
    assert ctx.mode == "full" and ctx.denylist
    monkeypatch.setattr(P.common, "profile", lambda: {})
    with pytest.raises(P.FailClosed):
        P.export_context_from_env({P.ENV_DIR: str(tmp_path)})


# ---------------------------------------------------------------------------------------------
# L13: no tolerated personal hits on export
# ---------------------------------------------------------------------------------------------

def test_l13_a_baseline_tolerating_a_personal_hit_is_refused(tmp_path):
    hits = P.export_scan(_stage(tmp_path, {"a.md": f"see {NAME}"}), _ctx())
    tolerated = [P._entry(hit) for hit in hits]
    with pytest.raises(P.FailClosed):
        P.export_compare(hits, tolerated, _ctx())
    with pytest.raises(P.FailClosed):
        P.export_compare([], [{"path": "x", "line": 0, "class": "export/binary", "id": "0"}], _ctx())
    assert [h.cls for h in P.export_compare(hits, [], _ctx()).new] == ["personal"]


# ---------------------------------------------------------------------------------------------
# L17, L18: derived data and references resolve against the staged tree
# ---------------------------------------------------------------------------------------------

def test_l17_derived_data_files_are_scanned_not_skipped(tmp_path):
    rel = "pact-plugin/tests/contract/purity_baseline.json"
    row = {"entries": [{"path": f"pact-plugin/skills/{NAME.lower()}-infra/SKILL.md", "line": 3}]}
    _stage(tmp_path, {rel: json.dumps(row), "pact-plugin/tests/contract/budgets.json": "{}"})
    assert [h.path for h in _personal(P.export_scan(tmp_path, _ctx()))] == [rel]


def test_l18_a_reference_into_an_excluded_skill_dangles_on_the_staged_tree(tmp_path):
    ref = "repacted" + ":" + "gone-skill"                        # built: this file must not dangle
    _stage(tmp_path, {"pact-plugin/skills/kept/SKILL.md": f"see {ref} first"})
    assert [(h.path, h.cls) for h in P.export_scan(tmp_path, _ctx())] == [
        ("pact-plugin/skills/kept/SKILL.md", "dangling-ref")]
    _stage(tmp_path, {"pact-plugin/skills/gone-skill/SKILL.md": CLEAN})
    assert P.export_scan(tmp_path, _ctx()) == []


# ---------------------------------------------------------------------------------------------
# adversarial boundaries
# ---------------------------------------------------------------------------------------------

def test_a_deeply_nested_file_and_path_are_scanned(tmp_path):
    rel = "/".join(f"d{i}" for i in range(60)) + "/leaf.md"
    hits = P.export_scan(_stage(tmp_path, {rel: f"\n\n{NAME}"}), _ctx())
    assert [(h.path, h.line) for h in _personal(hits)] == [(rel, 3)]


def test_a_name_at_the_end_of_a_file_without_newline_and_in_a_large_file(tmp_path):
    body = (CLEAN + "\n") * 20000 + "Zorva\nnex"
    hits = _personal(P.export_scan(_stage(tmp_path, {"big.md": body}), _ctx()))
    assert [h.line for h in hits] == [20001]


def test_the_author_field_allowance_holds_in_export_mode(tmp_path):
    manifest = json.dumps({"name": "p", "author": {"name": IDENTITY}})
    _stage(tmp_path, {"pact-plugin/.claude-plugin/plugin.json": manifest})
    assert P.export_scan(tmp_path, _ctx()) == []
    _stage(tmp_path, {"pact-plugin/.claude-plugin/plugin.json": manifest.replace('"p"', f'"{IDENTITY}"')})
    assert _personal(P.export_scan(tmp_path, _ctx()))


def test_hits_and_contexts_never_print_a_value(tmp_path):
    hits = P.export_scan(_stage(tmp_path, {"a.md": f"see {NAME}"}), _ctx())
    assert NAME not in repr(hits) and NAME not in repr(_ctx())

