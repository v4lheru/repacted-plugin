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

from project_tasks_support import PLUGIN_ROOT, load, run_cli
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


# ---------------------------------------------------------------------------------------------
# keeping the pages short: retire, auto-retire, grouping, the opt-in decision log
# ---------------------------------------------------------------------------------------------

def limits_page(world):
    return (world.repo / "docs" / "known-limits.md").read_text()


def bind(world, ref, task_id):
    world.store.link(ref, "reference", f"ptask:{TEAM}/{task_id}")


class TestKeepingThePagesShort:
    @pytest.mark.slow
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


