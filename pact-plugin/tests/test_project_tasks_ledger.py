"""
Location: pact-plugin/tests/test_project_tasks_ledger.py
Summary: The project ledger (hooks/shared/task_log/ledger.py): a lead acceptance appends the HANDOFF's known limits, decisions (DL<n>) and remaining
         items to .repacted/ledger.jsonl, a remaining item becomes a proposed task owned by the
         user, a re-accept writes nothing twice, a repo without a task log gets nothing, render
         adds docs/known-limits.md (and docs/decision-log.md when the config opts in), and
         `repact-task ledger --backfill` is idempotent. A limit is retired by `repact-task ledger
         retire` or by a later `fixed:` item of the same project task, and leaves the page; a
         decision that is only a test result is not recorded. The adversarial rows drive hostile HANDOFF text through the write and the
         render: Markdown and HTML injection, a credential value, huge and unicode text, and odd
         HANDOFF shapes. Uses the bridge tests' World (temp repo, temp CLAUDE_CONFIG_DIR).
"""
from __future__ import annotations

import json
import re

import pytest

from project_tasks_support import INERT_MARKER, PLUGIN_ROOT, load, run_cli
from test_project_tasks_bridge import TEAM, TASK_B, World, lead_accepts, run_hook

LEDGER_PATH = PLUGIN_ROOT / "hooks" / "shared" / "task_log" / "ledger.py"
VIEWS_PATH = PLUGIN_ROOT / "bin" / "_tasks" / "views.py"
HANDOFF = {"uncertainty": ["known-limit: no Windows support", "fixed: the lock race"],
           "decisions": ["use one flock for both files", "a sibling ledger file"],
           "open_questions": ["remaining: add a verb that retires a limit", "should DL ids be reused?"]}
PTASK = f"{TEAM}/{TASK_B}"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def ledger():
    return load("project_tasks_ledger_under_test", LEDGER_PATH)


def views():
    return load("tasks_views_for_ledger", VIEWS_PATH)


def entries(world):
    path = world.repo / ".repacted" / "ledger.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def configure_views(world, decision_log=True):
    views = {"progress": "docs/progress.md"}
    if decision_log:
        views["decision_log"] = "docs/decision-log.md"
    (world.repo / ".repacted" / "config.json").write_text(json.dumps({"views": views}))


# ---------------------------------------------------------------------------------------------
# the acceptance writes, once
# ---------------------------------------------------------------------------------------------

class TestAcceptance:
    def test_the_lead_accepting_writes_limits_numbered_decisions_and_proposed_remaining(self, world):
        world.task(handoff=HANDOFF)             # bound to no project task: the ledger still fills
        result = run_hook(lead_accepts(world))
        assert result.returncode == 0
        got = entries(world)
        assert [(e["kind"], e["text"]) for e in got] == [
            ("limit", "no Windows support"), ("decision", "use one flock for both files"),
            ("decision", "a sibling ledger file"), ("remaining", "add a verb that retires a limit")]
        assert [e.get("id") for e in got if e["kind"] == "decision"] == ["DL1", "DL2"]
        assert {e["ptask"] for e in got} == {PTASK}
        proposed = world.state().tasks[got[3]["t"]]
        assert (proposed["owner"], proposed["tags"], proposed["status"]) == ("user", ["proposed"], "planned")
        assert proposed["note"].startswith(f"Proposed from the HANDOFF of {PTASK} (rev 1):")
        line = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "1 known limit(s)" in line and "DL1, DL2" in line and got[3]["t"] in line
        assert ".repacted/ledger.jsonl merge=union" in (world.repo / ".gitattributes").read_text()
        queue = run_cli(world.repo, "next", "--all").stdout     # proposed: never recommended
        assert got[3]["t"] not in queue and "1 proposed from accepted HANDOFFs" in queue

    def test_a_second_acceptance_of_the_same_handoff_writes_nothing_twice(self, world):
        world.task(handoff=HANDOFF)
        run_hook(lead_accepts(world))
        before, tasks_before = entries(world), len(world.state().tasks)
        world.task(handoff=HANDOFF, revision_number=2)
        run_hook(lead_accepts(world))
        assert entries(world) == before and len(world.state().tasks) == tasks_before

    def test_a_repo_that_declined_the_task_log_gets_no_ledger(self, world):
        (world.repo / ".repacted" / "tasks.jsonl").unlink()
        world.task(handoff=HANDOFF)
        assert run_hook(lead_accepts(world)).returncode == 0
        assert not (world.repo / ".repacted" / "ledger.jsonl").exists()


# ---------------------------------------------------------------------------------------------
# the views and the backfill
# ---------------------------------------------------------------------------------------------

class TestViewsAndBackfill:
    def test_render_writes_the_known_limits_and_decision_log_pages(self, world):
        configure_views(world)
        ledger().record(world.store, PTASK, 1, HANDOFF)
        V = views()
        names = [name for name, _, _ in V.render(world.repo)]
        assert {"known_limits", "decision_log"} <= set(names)
        limits = (world.repo / "docs" / "known-limits.md").read_text()
        log = (world.repo / "docs" / "decision-log.md").read_text()
        assert limits.startswith(f"<!-- {V.HEADER} -->")
        assert re.search(r"- no Windows support \(`" + re.escape(PTASK) + r"` rev 1, id `[0-9a-f]{8}`\)",
                         limits)
        assert f"- **DL2** `{PTASK}` rev 1: a sibling ledger file" in log
        assert V.hand_edited(world.repo / "docs" / "decision-log.md") is False
        assert V.drifted(world.repo) == []
        assert not (world.repo / "docs" / "decisions.md").exists()

    def test_backfill_adds_each_accepted_handoff_once(self, world):
        world.task(handoff=HANDOFF)
        world.task(task_id="8", handoff={"decisions": ["third choice"]})
        world.task(task_id="9", status="in_progress", handoff={"decisions": ["not accepted yet"]})
        first = run_cli(world.repo, "ledger", "--backfill", "--team", TEAM)
        assert first.returncode == 0, first.stderr
        assert "2 accepted HANDOFF(s) read; added 1 known limit(s), 3 decision(s), 1 proposed task(s)" \
            in first.stdout
        again = run_cli(world.repo, "ledger", "--backfill", "--team", TEAM)
        assert "added 0 known limit(s), 0 decision(s), 0 proposed task(s)" in again.stdout
        assert [e.get("id") for e in entries(world) if e["kind"] == "decision"] == ["DL1", "DL2", "DL3"]


# ---------------------------------------------------------------------------------------------
# adversarial: the text is model-written
# ---------------------------------------------------------------------------------------------

class TestHostileText:
    def render_limit(self, world, text):
        configure_views(world)
        ledger().record(world.store, PTASK, 1, {"uncertainty": [f"known-limit: {text}"],
                                                "decisions": [text]})
        views().render(world.repo)
        return ((world.repo / "docs" / "known-limits.md").read_text(),
                (world.repo / "docs" / "decision-log.md").read_text())

    def test_markdown_and_html_in_a_limit_render_as_literal_text(self, world):
        hostile = ("<script>alert(1)</script> [x](javascript:alert(1)) ![i](http://t/p.png) **b** "
                   "`c` | cell | ~~s~~ &lt; <!-- render-hash: sha256:" + "0" * 64 + " --> USER-OWNED")
        for page in self.render_limit(world, hostile):
            body = page.rsplit("\n", 2)[0]
            for raw in ("<script>", "](", "**b**", "`c`", "| cell", "~~s~~", "&lt;", "<!-- render", "USER-OWNED"):
                assert not re.search(r"(?<!\\)" + re.escape(raw), body), raw
            assert "\\<script\\>" in page and "\\[x\\](javascript:alert(1))" in page
            assert page.splitlines()[-1].startswith("<!-- render-hash: sha256:")
        assert views().hand_edited(world.repo / "docs" / "known-limits.md") is False

    def test_a_credential_value_is_withheld_from_the_ledger_the_task_and_the_pages(self, world):
        ledger().record(world.store, PTASK, 1, {
            "uncertainty": [f"known-limit: the key {INERT_MARKER} is in the fixture"],
            "open_questions": [f"remaining: rotate {INERT_MARKER}"]})
        raw = (world.repo / ".repacted" / "ledger.jsonl").read_text()
        assert INERT_MARKER not in raw and INERT_MARKER not in (world.repo / ".repacted" / "tasks.jsonl").read_text()
        got = entries(world)
        assert all(e["text"].startswith("(withheld: ") and e["withheld"] for e in got)
        assert got[1]["t"] in world.state().tasks
        configure_views(world)
        views().render(world.repo)
        assert INERT_MARKER not in (world.repo / "docs" / "known-limits.md").read_text()

    def test_huge_and_unicode_text_is_one_capped_line_that_still_decodes(self, world):
        lg = ledger()
        huge = chr(0x1F600) * 5000 + "\n# heading - item"
        report = lg.record(world.store, PTASK, 1, {"uncertainty": [f"known-limit: {huge}"],
                                                   "open_questions": ["remaining: " + "y" * 9000]})
        assert report["proposed"]
        lines = (world.repo / ".repacted" / "ledger.jsonl").read_bytes().splitlines()
        assert all(len(line) <= 4096 for line in lines)
        text = json.loads(lines[0])["text"]
        assert len(text.encode("utf-8")) <= lg.TEXT_MAX_BYTES and text.endswith("…") and "\n" not in text
        limits, _ = self.render_limit(world, "\u202eevil\x1b[31m red\x07\n# not a heading")
        assert "\n# not a heading" not in limits
        assert not any(ch in limits for ch in ("\u202e", "\x1b", "\x07"))

    @pytest.mark.parametrize("handoff", [
        None, "known-limit: x", {"uncertainty": "known-limit: x"}, {"decisions": [1, None, {"a": 1}, ["x"]]},
        {"uncertainty": ["known-limit:   ", "known-limit:"]}, {"open_questions": [True, 3.5]}])
    def test_odd_handoff_shapes_write_nothing_and_never_raise(self, world, handoff):
        report = ledger().record(world.store, PTASK, 1, handoff)
        assert report == {"limits": [], "decisions": [], "proposed": [], "retired": []}
        assert entries(world) == []

    def test_a_hand_edited_ledger_line_is_skipped_not_rendered(self, world):
        lg = ledger()
        lg.record(world.store, PTASK, 1, {"decisions": ["kept"]})
        path = world.repo / ".repacted" / "ledger.jsonl"
        bad = {"v": 1, "kind": "decision", "ptask": "x`y/1", "rev": 1, "key": "0" * 24,
               "text": "spoofed", "id": "DL9"}
        path.write_text(path.read_text() + json.dumps(bad) + "\n{not json\n")
        got, corrupt = lg.read(world.repo)
        assert [e["text"] for e in got] == ["kept"] and corrupt == 2
        assert lg.record(world.store, PTASK, 1, {"decisions": ["next"]})["decisions"] == ["DL2"]


# ---------------------------------------------------------------------------------------------
# keeping the pages short: retire, auto-retire, grouping, the opt-in decision log
# ---------------------------------------------------------------------------------------------

def limits_page(world):
    return (world.repo / "docs" / "known-limits.md").read_text()


def bind(world, ref, task_id):
    world.store.link(ref, "reference", f"ptask:{TEAM}/{task_id}")


class TestKeepingThePagesShort:
    def test_retire_takes_a_limit_off_the_page_and_keeps_a_count(self, world):
        configure_views(world, decision_log=False)
        ledger().record(world.store, PTASK, 1, {"uncertainty": ["known-limit: no Windows support",
                                                               "known-limit: no ARM build"]})
        views().render(world.repo)
        short = re.search(r"no Windows support .*id `([0-9a-f]{8})`", limits_page(world)).group(1)
        done = run_cli(world.repo, "ledger", "retire", short, "--reason", "Windows CI cell added")
        assert done.returncode == 0, done.stderr
        page = limits_page(world)
        assert "no Windows support" not in page and "no ARM build" in page
        assert "1 retired limit(s) not shown" in page
        assert entries(world)[-1]["kind"] == "retire" and entries(world)[-1]["how"] == "manual"
        assert "(1 retired)" in run_cli(world.repo, "ledger").stdout
        for ident in (short, "zz", "0" * 24, ""):       # retired already, not hex, unknown, empty
            refused = run_cli(world.repo, "ledger", "retire", ident, "--reason", "x")
            assert refused.returncode != 0 and len(entries(world)) == 3, ident
        assert run_cli(world.repo, "ledger", "retire", short[:6]).returncode != 0   # no --reason

    def test_a_later_fixed_item_of_the_same_project_task_retires_the_limit(self, world):
        bind(world, world.ref, TASK_B)
        bind(world, world.ref, "8")
        lg = ledger()
        lg.record(world.store, PTASK, 1, {"uncertainty": ["known-limit: No verb retires a limit yet.",
                                                          "known-limit: the page only grows"]})
        lg.record(world.store, f"{TEAM}/9", 1,          # another project task: never retires
                  {"uncertainty": ["fixed: the page only grows"]})
        report = lg.record(world.store, f"{TEAM}/8", 1,
                           {"uncertainty": ["fixed: no verb retires a limit yet"]})
        assert len(report["retired"]) == 1
        left = lg.active_limits(lg.read(world.repo)[0])
        assert [e["text"] for e in left] == ["the page only grows"]
        named = lg.record(world.store, f"{TEAM}/9", 2,  # the printed id retires it from any task
                          {"uncertainty": [f"fixed: id `{lg.short_id(left[0])}`, the page shrinks now"]})
        assert named["retired"] == [left[0]["key"]] and lg.active_limits(lg.read(world.repo)[0]) == []

    def test_known_limits_group_by_project_task_newest_first(self, world):
        bind(world, world.ref, TASK_B)
        lg = ledger()
        lg.record(world.store, PTASK, 1, {"uncertainty": ["known-limit: older"]})
        lg.record(world.store, f"{TEAM}/9", 1, {"uncertainty": ["known-limit: unbound"]})
        lg.record(world.store, PTASK, 2, {"uncertainty": ["known-limit: newer"]})
        configure_views(world, decision_log=False)
        views().render(world.repo)
        page = limits_page(world)
        heading = f"## {world.ref}: Build the acceptance bridge"
        assert page.index(heading) < page.index("- newer") < page.index("- older") \
            < page.index(f"## `{TEAM}/9`") < page.index("- unbound")

    def test_the_decision_log_renders_only_when_the_config_names_it(self, world):
        ledger().record(world.store, PTASK, 1, HANDOFF)
        configure_views(world, decision_log=False)
        assert [name for name, _, _ in views().render(world.repo)] == ["progress", "known_limits"]
        assert not (world.repo / "docs" / "decision-log.md").exists()
        configure_views(world)
        assert "decision_log" in [name for name, _, _ in views().render(world.repo)]

    def test_a_decision_that_is_only_a_test_result_is_not_recorded(self, world):
        """Rule (ledger.is_test_result): a pass/fail count, a tcount run stamp or a bare .log path."""
        ledger().record(world.store, PTASK, 1, {"decisions": [
            "Rejection fix: 10 passed on 3.14 (log 2026-10-18T104819Z-66084)",
            "after the move: 487 passed, 2 skipped",
            "runs/2026-10-18T1.log",
            "header pragma -> None / 0 hits, 1 skipped; so the pragma covers one line only",
            "keep one flock for both files"]})
        assert [e["text"] for e in entries(world)] == [
            "header pragma -> None / 0 hits, 1 skipped; so the pragma covers one line only",
            "keep one flock for both files"]

    def test_the_page_lists_the_newest_open_limits_up_to_the_cap_and_counts_the_rest(self, world):
        ledger().record(world.store, PTASK, 1, {"uncertainty": [f"known-limit: limit {n}" for n in range(55)]})
        configure_views(world, decision_log=False)
        views().render(world.repo)
        page = limits_page(world)
        assert page.count("\n- limit ") == 50 and "- limit 54 " in page and "- limit 4 " not in page
        assert "5 older open limit(s) are not shown: `repact-task ledger --limits` lists them all" in page
        listed = run_cli(world.repo, "ledger", "--limits").stdout
        assert "limit 4" in listed and "55 open known limit(s)" in listed
        config = json.loads((world.repo / ".repacted" / "config.json").read_text())
        for value in (3, True, 0, "9", 1.5):            # a config key changes it; odd values refuse
            config["known_limits_max"] = value
            (world.repo / ".repacted" / "config.json").write_text(json.dumps(config))
            if value == 3:
                views().render(world.repo)
                assert limits_page(world).count("\n- limit ") == 3
            else:
                with pytest.raises(Exception, match="known_limits_max"):
                    views().render(world.repo)
