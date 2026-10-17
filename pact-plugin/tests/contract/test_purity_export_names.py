"""Export-only name lists: employer, client and project names the export scan must catch.

Location: pact-plugin/tests/contract/test_purity_export_names.py. The engine is _purity.py
(export_context_from_env, require_export, scan_export_text, export_scan). The lists are read only
for an export, never by the private scrub or a plain --check, and the D28 author allowance never
exempts them. Every name here is SYNTHETIC; the real lists never enter this file. Each bypass row
has a clean twin, so a fixture that cannot hit does not pass as a catch.
"""
from __future__ import annotations

import base64
import json
import urllib.parse

import pytest

from tests.contract import _purity as P

EMPLOYER = "Acmecorp"             # export-denylist.txt: substring
PROJECT = "Lantern"               # export-denylist-words.txt: also an English word, so whole word
IDENTITY = "Pella Marsk"
CLEAN = "the deploy notes for the staging cluster"


def _ctx(**over):
    base = dict(mode="full", denylist=["Zorvanex"], words=["quillet"],
                profile={"identity": {"name": IDENTITY}}, export_names=[EMPLOYER],
                export_words=[PROJECT])
    base.update(over)
    return P.Context(**base)


def _hits(text, ctx=None, rel="notes/a.md"):
    return [hit for hit in P.scan_export_text(rel, text, ctx or _ctx()) if hit.cls == "personal"]


def _folder(tmp_path, names=EMPLOYER + "\n", words=PROJECT + "\n"):
    (tmp_path / P.DENYLIST_NAMES[0]).write_text("Zorvanex\n")
    (tmp_path / P.DENYLIST_NAMES[1]).write_text("quillet\n")
    for name, body in zip(P.EXPORT_LIST_NAMES, (names, words)):
        if body is not None:
            (tmp_path / name).write_bytes(body if isinstance(body, bytes) else body.encode())
    return {P.ENV_DIR: str(tmp_path)}


@pytest.fixture
def identity(monkeypatch):
    monkeypatch.setattr(P.common, "profile", lambda: {"identity": {"name": IDENTITY}})


# ---------------------------------------------------------------------------------------------
# bypass routes: each encoding of the name is caught, its clean twin is not
# ---------------------------------------------------------------------------------------------

def _b64(text):
    return base64.b64encode(text.encode()).decode()


BYPASSES = {
    "upper": "built for ACMECORP last year",
    "mixed": "built for aCmEcOrP last year",
    "fullwidth": "built for " + "".join(chr(ord(c) + 0xFEE0) for c in EMPLOYER) + " last year",
    "zero-width": "built for Acme\u200bcorp last year",
    "soft-hyphen": "built for Acme\u00adcorp last year",
    "confusables": "built for \u0410cme\u0441\u043erp last year",
    "combining": "built for A\u0301cme\u0301corp last year",
    "line-break": "built for Acme\ncorp last year",
    "emphasis": "built for **Acme**_corp_ last year",
    "base64": "token " + _b64("built for Acmecorp") + " end",
    "url": "see https://x.test/" + urllib.parse.quote("Acmecorp", safe="").replace("c", "%63", 1),
    "html-entity": "built for Acme&#99;orp last year",
    "json-escape": json.dumps({"client": "\\u0041cmecorp"}),
    "code-fence": "```python\nclient = 'Acmecorp'\n```\n",
    "json-string": json.dumps({"client": EMPLOYER}),
    "word-case": "the LANTERN rollout",
    "word-fullwidth": "the " + "".join(chr(ord(c) + 0xFEE0) for c in PROJECT) + " rollout",
    "word-zero-width": "the Lan\u2060tern rollout",
    "word-in-backticks": "the `lantern` rollout",
}


@pytest.mark.parametrize("text", BYPASSES.values(), ids=BYPASSES.keys())
def test_an_export_name_is_caught_through_each_encoding(text):
    assert _hits(text), "the export name went through"
    assert _hits(text, _ctx(export_names=["Unrelated"], export_words=["unrelated"])) == []


@pytest.mark.parametrize("text", ["lanternfish swim deep", "the Lanterns were lit", "relantern"])
def test_a_whole_word_entry_does_not_hit_inside_a_longer_word(text):
    assert _hits(text) == []
    assert _hits(text, _ctx(export_names=[PROJECT]))          # control: as a substring it would


def test_a_name_in_a_staged_path_is_reported_on_line_0(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "acmecorp-infra.md").write_text(CLEAN)
    (tmp_path / "skills" / "plain-infra.md").write_text(CLEAN)
    hits = P.export_scan(tmp_path, _ctx())
    assert [(h.path, h.line, h.cls) for h in hits] == [("skills/acmecorp-infra.md", 0, "personal")]


def test_the_author_allowance_never_exempts_an_export_name():
    rel = "pact-plugin/.claude-plugin/plugin.json"
    author = f"Pella {EMPLOYER}"
    text = json.dumps({"name": "x", "author": {"name": author}})
    ctx = _ctx(profile={"identity": {"name": author}})
    assert _hits(text, _ctx(profile={"identity": {"name": author}}, export_names=["Unrelated"]), rel) == []
    assert len(_hits(text, ctx, rel)) == 1                    # the identity is allowed, the employer is not


# ---------------------------------------------------------------------------------------------
# loading: export only, fail closed, list file shapes
# ---------------------------------------------------------------------------------------------

def test_a_non_export_run_never_loads_the_export_lists(tmp_path):
    env = _folder(tmp_path)
    ctx = P.context_from_env(env)
    assert ctx.mode == "full" and ctx.export_names == [] and ctx.export_words == []
    assert [h for h in P.scan_file("a.md", f"built for {EMPLOYER}", ctx) if h.cls == "personal"] == []
    assert [h for h in P.scan_file("a.md", "built for Zorvanex", ctx) if h.cls == "personal"]


def test_an_export_loads_both_lists_from_the_folder(tmp_path, identity):
    ctx = P.export_context_from_env(_folder(tmp_path))
    assert (ctx.export_names, ctx.export_words) == ([EMPLOYER], [PROJECT])
    assert "export names 1 entries" in P.describe_inputs(ctx) and EMPLOYER not in P.describe_inputs(ctx)


def test_an_export_reads_each_list_from_its_own_variable(tmp_path, identity, monkeypatch):
    for var, name in (("REPACT_SCRUB_DENYLIST", "a.txt"), ("REPACT_SCRUB_DENYLIST_WORDS", "b.txt"),
                      (P.ENV_EXPORT_LISTS[0], "c.txt"), (P.ENV_EXPORT_LISTS[1], "d.txt")):
        (tmp_path / name).write_text(f"{name[0]}entry\n")
        monkeypatch.setenv(var, str(tmp_path / name))
    monkeypatch.delenv(P.ENV_DIR, raising=False)
    ctx = P.export_context_from_env()
    assert (ctx.export_names, ctx.export_words) == (["centry"], ["dentry"])


@pytest.mark.parametrize("names, words, missing", [
    (None, PROJECT + "\n", P.EXPORT_LIST_NAMES[0]),
    (EMPLOYER + "\n", None, P.EXPORT_LIST_NAMES[1]),
    ("", PROJECT + "\n", P.EXPORT_LIST_NAMES[0]),
    ("# only a comment\n\n   \n", PROJECT + "\n", P.EXPORT_LIST_NAMES[0]),
    (EMPLOYER + "\n", b"\xef\xbb\xbf\r\n", P.EXPORT_LIST_NAMES[1]),
], ids=["names-missing", "words-missing", "names-empty", "names-comments-only", "words-bom-only"])
def test_a_missing_or_empty_export_list_fails_closed(tmp_path, identity, names, words, missing):
    with pytest.raises(P.FailClosed, match=missing) as refused:
        P.export_context_from_env(_folder(tmp_path, names, words))
    assert P.ENV_DIR in str(refused.value)                    # the way through is named
    (tmp_path / "ok").mkdir()                                  # control: both lists present
    assert P.export_context_from_env(_folder(tmp_path / "ok")).export_names == [EMPLOYER]


def test_a_plain_check_does_not_need_the_export_lists(tmp_path):
    assert P.context_from_env(_folder(tmp_path, None, None)).mode == "full"


@pytest.mark.parametrize("over", [dict(export_names=[]), dict(export_words=[]),
                                  dict(export_names=["", "  "])], ids=["names", "words", "blank"])
def test_require_export_refuses_a_context_without_export_names(tmp_path, over):
    (tmp_path / "a.md").write_text(CLEAN)
    with pytest.raises(P.FailClosed, match="export-denylist"):
        P.export_scan(tmp_path, _ctx(**over))
    assert P.export_scan(tmp_path, _ctx()) == []


def test_a_list_with_bom_crlf_comments_and_blanks_loads_only_its_entries(tmp_path, identity):
    body = b"\xef\xbb\xbf# employer names\r\n\r\n" + EMPLOYER.encode() + b"\r\n  \r\n# end\r\n"
    ctx = P.export_context_from_env(_folder(tmp_path, body))
    assert ctx.export_names == [EMPLOYER]
    assert _hits("built for acmecorp", ctx)


def test_reports_never_carry_an_export_name():
    ctx = _ctx()
    hit = _hits(f"built for {EMPLOYER}", ctx)[0]
    assert EMPLOYER not in repr(ctx) and EMPLOYER not in repr(hit) and PROJECT not in repr(ctx)
    assert "export_names=<1 hidden>" in repr(ctx)
