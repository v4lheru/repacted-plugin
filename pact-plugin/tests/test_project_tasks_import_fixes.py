"""
Location: pact-plugin/tests/test_project_tasks_import_fixes.py
Summary: The import-md fixes for the defects the D10 P4b dry run measured on this repo's own docs
         (docs/test/d10-p4b-dryrun.md §1.2): blockquoted bullets are read (a log line becomes a
         note, a queue line becomes one task per item), bold-lead status sentences are not tasks,
         and a `DONE (<label>, <sha>)` bullet keeps its subject as the title with the refs as
         evidence.
Used by: the plugin suite. The importer is bin/_tasks/importers.py (P4a); the older mapping pins
         are in test_project_tasks_importers.py.
"""
from __future__ import annotations

from project_tasks_support import INERT_MARKER, git, load, new_repo
from test_project_tasks_importers import IMPORTERS_PATH, _write

import pytest

ARCHIVE = "docs/archive/progress.md"

PROGRESS_LOG_MD = """# Progress checkpoint

> **While you slept (2026-09-28, overnight log; newest first):**
> - 11:20 CEST, you're back: HALT #221 closed by your decision.
> - 06:30 CEST: merged ENV-GUARD and the D10 P6 atlas.

> **Latest update (2026-09-28 ~00:40 CEST):**
> - **Merged since the last checkpoint:** D3 STATUS, D5 DOCTOR.
> - **In flight:**
>   - #186 D10 P4a views (fork-builder).
> - **Next queue:** Q3 SEARCH (search-builder), Q6 index page, Q4b trial run (needs the switch), P5 retire; KB-GATE (vault-note write filter).
> - **Blocked:** #138 sandbox gates on #139.

## Where things stand

- **main** is at `31d7bd1` (local). Everything after that needs one approved push.
- **Done and merged:**
  - PREPARE, ARCHITECT, CODE and TEST.
- **Measured:** orchestrator turn 1 is 92,759 on PACT vs 62,867 on repacted.
- **Designs accepted and committed:**
  - the design notes.
- **Acme's scheduled jobs are DISABLED** (D23). The vault receives no automatic writes.
- **Vault ambient recall redesign.** The per-term floor cannot separate answerable prompts.
- **Make the export deterministic when the cache is disabled.** It differs per run today.
"""

QUEUE_TITLES = ["Q3 SEARCH (search-builder)", "Q6 index page", "Q4b trial run (needs the switch)",
                "P5 retire", "KB-GATE (vault-note write filter)"]

DONE_MD = """# Backlog

- **DONE (CLEANUP-A, b2c709aa):** ~~SWEEP:~~ protocols/workflows.md lines 28, 67 and 218 still send the lead to /repacted:rePACT; re-point them.
- **DONE (CLEANUP-B, 8e142f81, 30504a1a):** ~~SWEEP: one shared dispatch-template reference~~ (architect-pact; about 20 KB saved).
- **CORE-ROUTE: DONE (merged 4ffc2e3c, tag repacted-m3c).** Native route through claudeMdExcludes.
- **DONE (D10 P3, repacted-d10-p3):** ~~D10 drift_flags is quadratic since P4a S1.~~ drift_flags now builds open_children once.
- **Merged work DONE.** already in main.
"""


@pytest.fixture
def importers():
    return load("tasks_importer_fixes_under_test", IMPORTERS_PATH)


@pytest.fixture
def world(tmp_path, monkeypatch, importers):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    repo = new_repo(tmp_path / "repo")
    return importers, importers.W.init(repo), repo


def _items(proposal):
    return {item["line"]: item for item in proposal["items"]}


def _titles(proposal):
    return [item["title"] for item in proposal["items"]]


# ---------------------------------------------------------------------------------------------
# (1) blockquoted bullets
# ---------------------------------------------------------------------------------------------

class TestBlockquotes:
    def test_a_blockquoted_log_line_is_a_note_by_line_not_a_task(self, importers):
        proposal = importers.parse_md(PROGRESS_LOG_MD, archive=ARCHIVE)
        lines = {item["line"] for item in proposal["items"]}
        assert not lines & {4, 5, 8, 9, 12}
        log_notes = [note for note in proposal["notes"] if "blockquoted log line" in note]
        assert [note.split(":")[0] for note in log_notes] == ["L4", "L5", "L8", "L9", "L12"]
        assert "HALT" not in " ".join(log_notes)         # a note names the line, never its text

    def test_a_queue_line_becomes_one_planned_task_per_item_in_order(self, importers):
        proposal = importers.parse_md(PROGRESS_LOG_MD, archive=ARCHIVE)
        queue = [item for item in proposal["items"] if item["line"] == 11]
        assert [item["title"] for item in queue] == QUEUE_TITLES
        assert {item["status"] for item in queue} == {"planned"}
        assert all({"kind": "reference", "target": f"doc:{ARCHIVE}#L11"} in item["links"]
                   for item in queue)
        assert len({item["key"] for item in queue}) == len(QUEUE_TITLES)

    def test_the_queue_items_come_before_the_body_in_proposal_order(self, importers):
        titles = _titles(importers.parse_md(PROGRESS_LOG_MD, archive=ARCHIVE))
        assert titles[:len(QUEUE_TITLES)] == QUEUE_TITLES

    def test_a_queue_line_outside_a_blockquote_splits_the_same_way(self, importers):
        text = "- **Next:** Fix the parser, then ship it; Write the report.\n"
        titles = _titles(importers.parse_md(text, archive=ARCHIVE))
        assert titles == ["Fix the parser", "then ship it", "Write the report"]

    def test_a_queue_line_carrying_a_credential_is_withheld_whole(self, importers):
        text = f"> - **Next queue:** P6 atlas, rotate {INERT_MARKER}, P5 retire.\n"
        proposal = importers.parse_md(text, archive=ARCHIVE)
        assert proposal["items"] == [] and [w["line"] for w in proposal["withheld"]] == [1]

    def test_applying_a_queue_line_twice_writes_its_tasks_once(self, world):
        I, store, repo = world
        path = _write(repo, "docs/progress.md", PROGRESS_LOG_MD)
        proposal = I.parse_md(path.read_text(), archive=ARCHIVE)
        I.apply(store, proposal)
        I.apply(store, I.parse_md(path.read_text(), archive=ARCHIVE))
        titles = [task["title"] for task in I.W.PT.load(repo).tasks.values()]
        assert sorted(t for t in titles if t in QUEUE_TITLES) == sorted(QUEUE_TITLES)


# ---------------------------------------------------------------------------------------------
# (2) status sentences
# ---------------------------------------------------------------------------------------------

class TestStatusLines:
    def test_bold_lead_status_sentences_are_skipped_with_the_reason(self, importers):
        proposal = importers.parse_md(PROGRESS_LOG_MD, archive=ARCHIVE)
        lines = {item["line"] for item in proposal["items"]}
        assert not lines & {16, 17, 19, 20, 22}
        skipped = {entry["line"]: entry["reason"] for entry in proposal["skipped"]}
        assert set(skipped) >= {16, 17, 19, 20, 22}
        assert all("status line" in skipped[line] for line in (16, 17, 19, 20, 22))

    def test_a_work_item_is_kept_even_when_it_mentions_a_state(self, importers):
        items = _items(importers.parse_md(PROGRESS_LOG_MD, archive=ARCHIVE))
        assert items[23]["title"] == "Vault ambient recall redesign"
        assert items[24]["title"] == "Make the export deterministic when the cache is disabled"

    def test_a_done_marked_bullet_is_never_a_status_line(self, importers):
        items = _items(importers.parse_md(DONE_MD, archive="docs/archive/backlog.md"))
        assert items[7]["status"] == "done" and items[5]["status"] == "done"


# ---------------------------------------------------------------------------------------------
# (3) DONE titles
# ---------------------------------------------------------------------------------------------

class TestDoneTitles:
    def test_the_subject_is_the_title_and_the_sha_is_evidence(self, importers):
        items = _items(importers.parse_md(DONE_MD, archive="docs/archive/backlog.md"))
        first = items[3]
        assert first["title"].startswith("SWEEP: protocols/workflows.md lines 28, 67 and 218 still send")
        assert len(first["title"]) <= importers.W.PT.TITLE_MAX       # cut at the limit, as any title
        assert first["status"] == "done"
        assert "commit:b2c709aa" in first["evidence"]
        assert "doc:docs/archive/backlog.md#L3" in first["evidence"]
        assert first["note"].startswith("DONE (CLEANUP-A)")
        assert "DONE" not in first["title"]

    def test_every_sha_and_tag_in_the_marker_becomes_evidence(self, importers):
        items = _items(importers.parse_md(DONE_MD, archive="docs/archive/backlog.md"))
        assert {"commit:8e142f81", "commit:30504a1a"} <= set(items[4]["evidence"])
        assert items[4]["title"].startswith("SWEEP: one shared dispatch-template reference")
        assert items[6]["title"] == "D10 drift_flags is quadratic since P4a S1"
        assert "tag:repacted-d10-p3" in items[6]["evidence"]

    def test_a_subject_before_the_marker_is_the_title(self, importers):
        item = _items(importers.parse_md(DONE_MD, archive="docs/archive/backlog.md"))[5]
        assert item["title"] == "CORE-ROUTE"
        assert {"commit:4ffc2e3c", "tag:repacted-m3c"} <= set(item["evidence"])

    def test_a_done_bullet_closes_on_its_commit_without_the_archive(self, world):
        I, store, repo = world
        sha = git(repo, "rev-parse", "--short=8", "HEAD").stdout.strip()
        text = f"- **DONE (CLEANUP-A, {sha}):** ~~Fix the thing.~~ It is fixed.\n"
        I.apply(store, I.parse_md(text, archive="docs/archive/backlog.md"))
        task = next(iter(I.W.PT.load(repo).tasks.values()))
        assert task["title"] == "Fix the thing" and task["status"] == "done"


# ---------------------------------------------------------------------------------------------
# (4) a heavy last section is flagged, not guessed (lead ruling (B); the source fix is (C))
# ---------------------------------------------------------------------------------------------

def _sections(first: int, last: int) -> str:
    body = "# Backlog\n\n## First section\n\n" + "".join(f"- **Item a{i}.** Text.\n" for i in range(first))
    return body + "\n## Last section\n\n" + "".join(f"- **Item b{i}.** Text.\n" for i in range(last))


def _heavy(proposal):
    return [note for note in proposal["notes"] if "the file's last heading scopes" in note]


class TestHeavyLastSection:
    def test_the_share_is_a_named_constant_of_one_third(self, importers):
        from fractions import Fraction
        assert importers.LAST_SECTION_WARN_FRACTION == Fraction(1, 3)

    def test_a_last_section_holding_exactly_the_share_is_flagged(self, importers):
        proposal = importers.parse_md(_sections(4, 2), archive="docs/archive/backlog.md")
        notes = _heavy(proposal)
        assert len(notes) == 1 and "2 of 6 items" in notes[0]
        assert notes[0].startswith("L10:")                    # the heading's line, not its text
        assert "Last section" not in notes[0]

    def test_a_last_section_just_under_the_share_is_not_flagged(self, importers):
        assert _heavy(importers.parse_md(_sections(5, 2), archive="docs/archive/backlog.md")) == []

    def test_a_file_whose_last_heading_is_a_title_is_not_flagged(self, importers):
        text = "- **Item one.** Text.\n\n# Title only\n\n- **Item two.** Text.\n"
        assert _heavy(importers.parse_md(text, archive="docs/archive/x.md")) == []

    def test_the_warning_changes_no_item_and_no_tag(self, importers):
        proposal = importers.parse_md(_sections(4, 2), archive="docs/archive/backlog.md")
        assert len(proposal["items"]) == 6
        assert [item["tags"] for item in proposal["items"]][-2:] == [["last-section"], ["last-section"]]
