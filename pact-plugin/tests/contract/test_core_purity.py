"""Contract D8-G, CORE PURITY: personal content, identity and secrets stay out of the shareable core.

Design: the design notes section 5 (D8-G, ratchet mode). The engine is
tests/contract/_purity.py; the baseline is tests/contract/purity_baseline.json, which holds NO
values (path, line, class, a hash of the hit's line with the hit masked, and a scope).

The gate: every hit on the core tree must be in the baseline (a NEW hit fails, named by path, line
and class), and every baseline entry must still be hit (a STALE entry fails until
`python3 tests/contract/_purity.py --shrink` tightens it in the same commit). The personal class
needs the scrub denylist: the merge gate fails closed without it, and public CI states
REPACT_PURITY_NO_DENYLIST=1 to run everything else.

Every planted value below is built from inert pieces at test time; none sits in this file whole.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.contract import _purity as P


@pytest.fixture(scope="module")
def texts():
    """The real core tree, read once."""
    return {rel: text for rel in P.core_files() if (text := P.read_core(rel)) is not None}


@pytest.fixture(scope="module")
def baseline():
    return P.load_baseline()


@pytest.fixture(scope="module")
def gate(tmp_path_factory):
    """The merge gate's context (it fails closed without a denylist) and ONE scan of the tree.
    Built under the same scrubbed home the per-test conftest fixture gives `--check` (no
    CLAUDE_CONFIG_DIR, Path.home() -> tmp), so the contract does not depend on who runs it:
    the owner-name check against the real profile is the user-layer scans' job."""
    home = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        patch.setattr(Path, "home", lambda: home)
        ctx = P.context_from_env()
    return ctx, P.scan_tree(ctx)


def _ctx(denylist=(), words=(), profile=None, mode="full"):
    return P.Context(mode=mode, denylist=list(denylist), words=list(words), profile=profile or {})


def _plant(texts, rel, line):
    """The file's real text with one line appended: an in-memory copy of the tree."""
    assert rel in texts, rel
    return texts[rel].rstrip("\n") + "\n" + line + "\n"


def _new_in(rel, text, ctx, baseline):
    """New hits in one planted file, against the real baseline entries for that file."""
    return P.compare(P.scan_file(rel, text, ctx), baseline, ctx, paths={rel}).new


def _first(texts, prefix, suffix=".md"):
    return next(rel for rel in sorted(texts) if rel.startswith(prefix) and rel.endswith(suffix))


# ---------------------------------------------------------------------------------------------
# the gate itself
# ---------------------------------------------------------------------------------------------

def test_the_core_tree_matches_its_purity_baseline(gate, baseline):
    ctx, hits = gate
    result = P.compare(hits, baseline, ctx)
    assert not result.new, "NEW purity hits (fix them; never baseline a leak):\n" + "\n".join(
        f"  {hit.path}:{hit.line}: {hit.cls}" for hit in result.new)
    assert not result.stale, (
        f"{len(result.stale)} baseline entr(ies) no longer hit; tighten the ratchet in this commit: "
        "python3 tests/contract/_purity.py --shrink\n" + "\n".join(
            f"  {e['path']}:{e['line']}: {e['class']}" for e in result.stale[:20]))


# ---------------------------------------------------------------------------------------------
# modes: the merge gate fails closed, public CI states it runs without the denylist
# ---------------------------------------------------------------------------------------------

class TestModes:
    def test_a_failing_gate_prints_no_denylist_word_profile_value_or_list_item(self):
        """A failing TestCheckMode test printed its `gate` fixture, the Context's denylist with it,
        into the pytest log (2026-10-04). pytest renders a fixture with saferepr."""
        from _pytest._io.saferepr import saferepr
        ctx = _ctx(denylist=["Quillon Varnet"], words=["varnetword"], profile={"name": "Ostrel Bain"})
        shown = saferepr((ctx, []), maxsize=None) + str(ctx)
        assert "denylist=<1 hidden>" in shown
        assert not any(word in shown for word in ("Quillon", "varnetword", "Ostrel"))

    def test_no_denylist_and_no_public_flag_fails_closed(self, tmp_path):
        env = {"REPACT_PURITY_DENYLIST_DIR": str(tmp_path / "none")}
        with pytest.raises(P.FailClosed) as err:
            P.context_from_env(env)
        assert "REPACT_PURITY_NO_DENYLIST" in str(err.value)

    def test_public_mode_runs_without_the_personal_class(self, tmp_path):
        env = {"REPACT_PURITY_DENYLIST_DIR": str(tmp_path / "none"), "REPACT_PURITY_NO_DENYLIST": "1"}
        ctx = P.context_from_env(env)
        assert ctx.mode == "public" and not P.evaluated("personal", ctx)
        assert P.evaluated("home-path", ctx) and P.evaluated("secret/aws-key", ctx)

    def test_a_denylist_directory_gives_the_full_gate(self, tmp_path):
        (tmp_path / "scrub-denylist.txt").write_text("# comment\n" + "Zeph" + "yrion\n")
        ctx = P.context_from_env({"REPACT_PURITY_DENYLIST_DIR": str(tmp_path)})
        assert ctx.mode == "full" and ctx.denylist == ["Zeph" + "yrion"]
        assert P.evaluated("personal", ctx)

    def test_public_mode_on_the_real_tree_is_clean_against_the_baseline(self, gate, baseline, tmp_path):
        env = {"REPACT_PURITY_DENYLIST_DIR": str(tmp_path / "none"), "REPACT_PURITY_NO_DENYLIST": "1"}
        public = P.context_from_env(env)
        result = P.compare(gate[1], baseline, public)    # the structural classes are mode-independent
        assert not result.new and not result.stale


# ---------------------------------------------------------------------------------------------
# the ratchet, both directions
# ---------------------------------------------------------------------------------------------

class TestRatchet:
    def test_a_removed_hit_is_stale_until_the_baseline_shrinks(self, texts, baseline):
        ctx = _ctx(mode="public")
        # An entry whose id occurs once in its file: one dangling name on three lines has no
        # single line to remove.
        ids = [(e["path"], e["id"]) for e in baseline]
        entry = next(e for e in baseline
                     if P.evaluated(e["class"], ctx) and ids.count((e["path"], e["id"])) == 1)
        # Locate the line by the entry's IDENTITY, not its recorded line number: an entry keeps
        # matching after its line moves (edits above it), so the recorded number can be stale.
        (line_no,) = {h.line for h in P.scan_file(entry["path"], texts[entry["path"]], ctx) if h.id == entry["id"]}
        lines = texts[entry["path"]].splitlines()
        removed = "\n".join(line for number, line in enumerate(lines, 1) if number != line_no)
        hits = P.scan_file(entry["path"], removed, ctx)
        result = P.compare(hits, baseline, ctx, paths={entry["path"]})
        assert not result.new and entry in result.stale
        tightened = P.shrunk(baseline, hits, ctx, paths={entry["path"]})
        after = P.compare(hits, tightened, ctx, paths={entry["path"]})
        assert not after.new and not after.stale and len(tightened) < len(baseline)

    def test_a_line_that_moves_keeps_its_identity(self, texts, baseline):
        ctx = _ctx(mode="public")
        entry = next(e for e in baseline if P.evaluated(e["class"], ctx))
        moved = "an added first line\n" + texts[entry["path"]]
        result = P.compare(P.scan_file(entry["path"], moved, ctx), baseline, ctx, paths={entry["path"]})
        assert not result.new and not result.stale

    def test_the_identity_is_a_hash_of_the_masked_line_never_of_the_value(self):
        ctx = _ctx(mode="public")
        ip = "10." + "20.30.40"
        (hit,) = [h for h in P.scan_file("x.md", f"host at {ip} here\n", ctx) if h.cls == "private-ip"]
        assert hit.id != hashlib.sha256(ip.encode()).hexdigest()[:16]
        other = "10." + "20.30.41"
        (same,) = [h for h in P.scan_file("x.md", f"host at {other} here\n", ctx) if h.cls == "private-ip"]
        assert same.id == hit.id                       # the value is not part of the identity

    def test_the_baseline_file_has_no_field_that_could_hold_a_value(self, gate):
        """Structural, not a substring hunt: every entry field is a tracked core path, a known class
        name, a 16-hex id, a line number or a scope token, and the one free-text field (a refresh
        reason) is clean under the scanner itself. Nothing is left that could carry a value."""
        ctx, _ = gate
        doc = json.loads(P.BASELINE.read_text(encoding="utf-8"))
        tracked = set(P.core_files())
        classes = P.known_classes()
        for entry in doc["entries"]:
            assert set(entry) == {"path", "line", "class", "id", "scope"}, entry.keys()
            assert entry["path"] in tracked, "a baseline path that is not a tracked core file"
            assert entry["class"] in classes, entry["class"]
            assert len(entry["id"]) == 16 and set(entry["id"]) <= set("0123456789abcdef")
            assert isinstance(entry["line"], int) and entry["scope"] in ("core", "test")
        reasons = "\n".join(str(refresh.get("reason", "")) for refresh in doc.get("refreshes", []))
        assert P.scan_file("refresh-reasons", reasons, ctx) == []
        assert set(doc) == {"_doc", "version", "refreshes", "entries"}


# ---------------------------------------------------------------------------------------------
# design section 5.2: the nine plant rows, each on an in-memory copy of the REAL tree
# ---------------------------------------------------------------------------------------------

class TestPlantRows:
    def test_row1_a_real_home_path_in_a_skill_reference(self, texts, baseline):
        rel = _first(texts, "pact-plugin/skills/stack-traps/references/")
        line = "see /Users/" + "qz" + "tester" + "/notes/setup.md for the steps"
        new = _new_in(rel, _plant(texts, rel, line), _ctx(mode="public"), baseline)
        assert [(h.path, h.cls) for h in new] == [(rel, "home-path")]
        assert new[0].line == len(_plant(texts, rel, line).splitlines())

    def test_row2_a_private_ip_in_a_bin_docstring(self, texts, baseline):
        rel = "pact-plugin/bin/scrub-check"
        new = _new_in(rel, _plant(texts, rel, "# reach it at " + "10." + "20.30.40"), _ctx(mode="public"), baseline)
        assert [h.cls for h in new] == ["private-ip"]

    def test_row3_a_cloud_project_id_in_a_reference(self, texts, baseline):
        rel = _first(texts, "pact-plugin/reference/")
        line = "gcloud run deploy --project " + "acme" + "-prod-4821"
        new = _new_in(rel, _plant(texts, rel, line), _ctx(mode="public"), baseline)
        assert [h.cls for h in new] == ["cloud-id"]

    def test_row4_a_slack_user_id_in_an_agent_body(self, texts, baseline):
        rel = "pact-plugin/agents/pact-architect.md"
        new = _new_in(rel, _plant(texts, rel, "ping " + "U0" + "QZ7TEST1" + " when done"), _ctx(mode="public"), baseline)
        assert [h.cls for h in new] == ["user-id"]

    def test_row5_the_profile_email_in_a_command(self, texts, baseline):
        rel = "pact-plugin/commands/wrap-up.md"
        email = "qa.tester" + "@" + "acmecorp" + ".io"
        ctx = _ctx(profile={"identity": {"email": email}})
        new = _new_in(rel, _plant(texts, rel, f"mail {email} the summary"), ctx, baseline)
        assert "personal" in [h.cls for h in new]

    def test_row6_a_denylisted_project_name_in_an_example_line(self, texts, baseline):
        rel = _first(texts, "pact-plugin/skills/verification-discipline/references/vd-a1")
        project = "Zeph" + "yrion Dash"
        new = _new_in(rel, _plant(texts, rel, f"- Example ({project}): the count was wrong."),
                      _ctx(denylist=[project]), baseline)
        assert [h.cls for h in new] == ["personal"]

    def test_row7_a_reference_to_a_skill_the_core_does_not_have(self, texts, baseline):
        rel = _first(texts, "pact-plugin/skills/safe-ops/")
        new = _new_in(rel, _plant(texts, rel, "Follow `repacted:" + "no-such-skill` for the rest."),
                      _ctx(mode="public"), baseline)
        assert [h.cls for h in new] == ["dangling-ref"]

    @pytest.mark.parametrize("line,refs", [
        ("<!-- " + "repacted:" + "begin notes -->", 0),
        ("<!-- " + "repacted:" + "end notes -->", 0),
        ("`" + "repacted:" + "begin/end <region>` markers", 0),
        ("ends at " + "repacted:" + "end.", 0),
        ("repacted:" + "beginx", 1),
        ("repacted:" + "Begin", 1),
        ("repacted:" + "END", 1),
        ("repacted:" + "begin-foo", 1),
        ("repacted:" + "end_", 1),
        ("repacted:" + "endpoint", 1),
        ("repacted:" + "begins", 1),
        ("repacted:" + "bеgin", 1),             # Cyrillic e: the ASCII token is "b", not "begin"
        ("repacted:" + "еnd", 0),               # no ASCII token at all: no ref shape
        ("<!-- " + "repacted:" + "begin x --> see " + "repacted:" + "no-such-skill", 1),
    ])
    def test_only_the_exact_region_marker_words_are_not_refs(self, line, refs):
        hits = [h for h in P.scan_file("x.md", line + "\n", _ctx(mode="public")) if h.cls == "dangling-ref"]
        assert len(hits) == refs, [h.line for h in hits]

    def test_row8_a_fresh_secret_assignment_outside_the_baseline(self, texts, baseline):
        rel = next(r for r in sorted(texts) if r.startswith("pact-plugin/commands/")
                   and not any(e["path"] == r for e in baseline))
        line = 'DEPLOY_TOKEN = "' + "q7Rk2Lm9" + "Xp4Tz8Wv3Nb6" + '"'
        new = _new_in(rel, _plant(texts, rel, line), _ctx(mode="public"), baseline)
        assert [h.cls for h in new] == ["secret/secret-assignment"]

    @staticmethod
    def _row9_new(texts, baseline):
        ctx = _ctx(mode="public")
        entry = next(e for e in baseline if e["class"].startswith("secret/"))
        # The baselined line is found by the entry's id, never its recorded line number (advisory:
        # an edit above the line moves it without changing the entry). An id can sit on several lines.
        line_no = min(h.line for h in P.scan_file(entry["path"], texts[entry["path"]], ctx) if h.id == entry["id"])
        line = texts[entry["path"]].splitlines()[line_no - 1]
        return entry, _new_in(entry["path"], _plant(texts, entry["path"], line), ctx, baseline)

    def test_row9_one_more_hit_on_a_baselined_line_raises_the_count(self, texts, baseline):
        entry, new = self._row9_new(texts, baseline)
        assert [(h.path, h.cls) for h in new] == [(entry["path"], entry["class"])]

    def test_row9_holds_after_an_edit_above_the_baselined_line(self, texts, baseline):
        entry = next(e for e in baseline if e["class"].startswith("secret/"))
        moved = {**texts, entry["path"]: "an added first line\n" + texts[entry["path"]]}
        _, new = self._row9_new(moved, baseline)
        assert [(h.path, h.cls) for h in new] == [(entry["path"], entry["class"])]

    def test_the_overlay_literal_is_a_hit(self, texts, baseline):
        rel = "pact-plugin/agents/pact-architect.md"
        new = _new_in(rel, _plant(texts, rel, "install repacted" + "-personal too"), _ctx(mode="public"), baseline)
        assert [h.cls for h in new] == ["overlay-literal"]


# ---------------------------------------------------------------------------------------------
# D28 (lead ruling on #209): the author's own name is allowed ONLY in the three author fields
# ---------------------------------------------------------------------------------------------

AUTHOR = "Qz" + "ra Testova"                     # an inert fixture identity, built at test time


def _author_ctx(**extra):
    return _ctx(profile={"identity": {"name": AUTHOR}}, **extra)


def _with_json(texts, rel, edit):
    doc = json.loads(texts[rel])
    edit(doc)
    return json.dumps(doc, indent=2) + "\n"


def _personal(rel, text, ctx):
    return [h for h in P.scan_file(rel, text, ctx) if h.cls == "personal"]


class TestAuthorFieldAllowance:
    PLUGIN, MARKET, NOTICE = "pact-plugin/.claude-plugin/plugin.json", ".claude-plugin/marketplace.json", \
        "pact-plugin/NOTICE.md"

    def test_the_name_in_the_plugin_author_field_is_allowed(self, texts):
        text = _with_json(texts, self.PLUGIN, lambda d: d["author"].update(name=AUTHOR))
        assert _personal(self.PLUGIN, text, _author_ctx()) == []

    def test_the_name_elsewhere_in_the_plugin_manifest_is_a_hit(self, texts):
        text = _with_json(texts, self.PLUGIN, lambda d: d.update(description=f"built by {AUTHOR}"))
        assert len(_personal(self.PLUGIN, text, _author_ctx())) == 1

    def test_the_name_as_marketplace_owner_is_allowed(self, texts):
        text = _with_json(texts, self.MARKET, lambda d: d["owner"].update(name=AUTHOR))
        assert _personal(self.MARKET, text, _author_ctx()) == []

    def test_the_name_as_a_marketplace_plugin_author_is_allowed(self, texts):
        """The same author credit as plugin.json's, shown in the listing (lead ruling on #209)."""
        text = _with_json(texts, self.MARKET, lambda d: d["plugins"][0]["author"].update(name=AUTHOR))
        assert _personal(self.MARKET, text, _author_ctx()) == []

    def test_the_name_elsewhere_in_the_marketplace_is_a_hit(self, texts):
        text = _with_json(texts, self.MARKET, lambda d: d["plugins"][0].update(description=f"by {AUTHOR}"))
        assert len(_personal(self.MARKET, text, _author_ctx())) == 1

    def test_the_name_on_the_notice_modifier_line_is_allowed_and_nowhere_else(self, texts):
        lines = texts[self.NOTICE].splitlines()
        modifier = next(i for i, line in enumerate(lines) if line.startswith("- Modified by"))
        lines[modifier] = f"- Modified by {AUTHOR}"
        assert _personal(self.NOTICE, "\n".join(lines) + "\n", _author_ctx()) == []
        lines.append(f"- Thanks to {AUTHOR} for the port.")
        hits = _personal(self.NOTICE, "\n".join(lines) + "\n", _author_ctx())
        assert [h.line for h in hits] == [len(lines)]

    def test_the_same_name_in_a_skill_body_is_a_hit(self, texts):
        rel = _first(texts, "pact-plugin/skills/safe-ops/")
        assert len(_personal(rel, _plant(texts, rel, f"Ask {AUTHOR} before a deploy."), _author_ctx())) == 1

    def test_without_a_profile_identity_no_field_is_allowed(self, texts):
        """The allowance keys on profile identity.name: a name known only from the denylist is
        a hit even in an author field (fail safe)."""
        text = _with_json(texts, self.PLUGIN, lambda d: d["author"].update(name=AUTHOR))
        assert len(_personal(self.PLUGIN, text, _ctx(denylist=[AUTHOR]))) == 1

    def test_a_longer_value_holding_the_name_is_not_allowed(self, texts):
        text = _with_json(texts, self.PLUGIN, lambda d: d["author"].update(name=f"{AUTHOR} and a co-author"))
        assert len(_personal(self.PLUGIN, text, _author_ctx())) == 1


# ---------------------------------------------------------------------------------------------
# what is NOT a hit
# ---------------------------------------------------------------------------------------------

def test_a_hit_never_shows_its_value_in_a_repr():
    """A failing gate prints its hits (pytest shows the repr): that output must not carry the value."""
    ip = "10." + "20.30.40"
    (hit,) = [h for h in P.scan_file("x.md", f"at {ip}\n", _ctx(mode="public")) if h.cls == "private-ip"]
    assert hit.value == ip and ip not in repr(hit) and ip not in repr([hit])


class TestPlaceholders:
    @pytest.mark.parametrize("line", [
        "/Users/<name>/project", "/Users/you/code", "/home/runner/work", "user@example.com",
        "--project <PROJECT_ID>", "--project my-project", "use `repacted:<skill>` links",
        "`repacted:bootstrap` starts a session", "`repacted:pact-orchestrator` is the lead",
        "`repacted:safe-ops` applies"])
    def test_placeholders_and_real_core_names_pass(self, line):
        hits = P.scan_file("x.md", line + "\n", _ctx(mode="public"))
        assert hits == [], [h.cls for h in hits]


# ---------------------------------------------------------------------------------------------
# the CI check: one pytest invocation per workflow, so CI runs the gate through --check
# ---------------------------------------------------------------------------------------------

class TestCheckMode:
    """The workflow may carry exactly ONE pytest invocation (test_ci_collection_scope), so the named
    `core-purity` job runs `_purity.py --check`: exit 0 when the tree matches the baseline, 1 on any
    NEW or STALE entry, printing classes and counts, never a value."""

    def test_check_passes_on_the_real_tree(self, gate, capsys):
        assert P.main(["--check"]) == 0
        assert "purity: clean" in capsys.readouterr().out

    def test_check_fails_on_a_new_hit_and_prints_no_value(self, gate, monkeypatch, capsys):
        ip = "10." + "20.30.41"
        extra = P.scan_file("x.md", f"at {ip}\n", _ctx(mode="public"))
        monkeypatch.setattr(P, "scan_tree", lambda ctx, texts=None: list(gate[1]) + extra)
        assert P.main(["--check"]) == 1
        out = capsys.readouterr().out
        assert "NEW private-ip 1" in out and ip not in out

    def test_check_fails_on_a_stale_entry(self, gate, monkeypatch, capsys):
        monkeypatch.setattr(P, "scan_tree", lambda ctx, texts=None: list(gate[1])[1:])
        assert P.main(["--check"]) == 1
        assert "STALE" in capsys.readouterr().out

    def test_the_workflow_job_runs_check_not_a_second_pytest(self):
        workflow = (Path(__file__).resolve().parents[3] / ".github" / "workflows" / "tests.yml").read_text()
        job = workflow.split("\n  core-purity:\n", 1)[1].split("\n\n", 1)[0]
        runs = [line.strip() for line in job.splitlines() if line.strip().startswith("run:")]
        assert "run: python tests/contract/_purity.py --check" in runs
        assert not [run for run in runs if "pytest" in run], runs
