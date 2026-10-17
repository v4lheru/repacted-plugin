"""
D10 P1 TASKS-CORE (the design notes §3, §4, §9; test plan §12.1
items 1, 2, 5, 6 and 8): replay determinism, cycles, a corrupt line, the evidence rule, the
credential and D26 rules, where the log lives, and the read side's import closure.
"""
from __future__ import annotations

import ast
import json
import random
import tempfile
import time

import pytest

from project_tasks_support import (INERT_MARKER, READER_PATH, git, kb_profile, new_repo, reader,
                                   writer)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    kb_profile(tmp_path, monkeypatch)
    # run: evidence is verified against the runs dirs (P2): point both at empty temp dirs, so a
    # real log on this machine can never verify a test's made-up run name.
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    repo = new_repo(tmp_path / "repo")
    W = writer()
    return W, reader(), W.init(repo), repo


def _log(repo):
    return repo / ".repacted" / "tasks.jsonl"


# ---------------------------------------------------------------------------------------------
# where the log lives (§3.1): the MAIN checkout, found by stat only
# ---------------------------------------------------------------------------------------------

class TestMainCheckout:
    def test_a_plain_checkout_and_its_subdirectories(self, tmp_path):
        R = reader()
        repo = new_repo(tmp_path / "repo")
        (repo / "a" / "b").mkdir(parents=True)
        assert R.find_main_checkout(repo) == repo.resolve()
        assert R.find_main_checkout(repo / "a" / "b") == repo.resolve()

    def test_a_linked_worktree_resolves_to_the_main_checkout(self, tmp_path):
        R = reader()
        repo = new_repo(tmp_path / "repo")
        git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
        assert R.find_main_checkout(tmp_path / "wt") == repo.resolve()

    def test_a_submodule_is_its_own_main_checkout(self, tmp_path):
        R = reader()
        inner = new_repo(tmp_path / "inner")
        outer = new_repo(tmp_path / "outer")
        git(outer, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "sub")
        assert (outer / "sub" / ".git").is_file()
        assert R.find_main_checkout(outer / "sub") == (outer / "sub").resolve()

    def test_a_bare_repo_or_a_plain_folder_has_none(self, tmp_path):
        R = reader()
        bare = tmp_path / "bare.git"
        git(tmp_path, "init", "-q", "--bare", str(bare))
        (tmp_path / "plain").mkdir()
        assert R.find_main_checkout(bare) is None
        assert R.find_main_checkout(tmp_path / "plain") is None

    def test_a_bare_repo_inside_a_checkout_is_not_that_checkout(self, tmp_path):
        R = reader()
        outer = new_repo(tmp_path / "outer")
        git(outer, "init", "-q", "--bare", str(outer / "mirror.git"))
        assert R.find_main_checkout(outer / "mirror.git") is None

    def test_a_writer_in_a_worktree_appends_to_the_main_checkouts_log(self, world, tmp_path):
        W, R, store, repo = world
        git(repo, "worktree", "add", "-q", str(tmp_path / "wt"), "-b", "side")
        ref = W.Store.locate(tmp_path / "wt").add("written from a worktree")
        assert ref in R.load(repo).tasks
        assert not (tmp_path / "wt" / ".repacted" / "tasks.jsonl").exists()


# ---------------------------------------------------------------------------------------------
# item 1: replay determinism, duplicates
# ---------------------------------------------------------------------------------------------

def _populate(store):
    a, b, c = store.add("alpha"), store.add("beta", priority="P1"), store.add("gamma")
    store.dep(a, b)
    store.set(b, "status", "active")
    store.set(c, "rank", 2)
    store.link(a, "input", "doc:docs/plan.md")
    store.set(a, "tags", ["gate", "d10"])
    return a, b, c


class TestReplayDeterminism:
    def test_twenty_shuffles_replay_to_one_state(self, world):
        W, R, store, repo = world
        _populate(store)
        events = R.read_log(_log(repo)).events
        want = R.state_digest(R.replay(events))
        rng = random.Random(7)
        for _ in range(20):
            shuffled = list(events)
            rng.shuffle(shuffled)
            assert R.state_digest(R.replay(shuffled)) == want

    def test_a_byte_identical_duplicate_is_dropped(self, world):
        W, R, store, repo = world
        _populate(store)
        events = R.read_log(_log(repo)).events
        state = R.replay(events + [dict(events[3])])
        assert R.state_digest(state) == R.state_digest(R.replay(events))
        assert state.eid_conflicts == []

    def test_one_eid_with_two_contents_is_kept_once_and_flagged(self, world):
        W, R, store, repo = world
        a, b, c = _populate(store)
        events = R.read_log(_log(repo)).events
        rename = next(e for e in events if e["op"] == "create" and e["t"] == c)
        twin = {**rename, "title": "gamma prime"}
        one = R.replay(events + [twin])
        other = R.replay([twin] + events)
        assert R.state_digest(one) == R.state_digest(other)
        assert [x["eid"] for x in one.eid_conflicts] == [rename["eid"]]
        smallest = min(json.dumps(e, sort_keys=True, separators=(",", ":")) for e in (rename, twin))
        assert one.tasks[c]["title"] == json.loads(smallest)["title"]


# ---------------------------------------------------------------------------------------------
# item 2: cycles
# ---------------------------------------------------------------------------------------------

class TestCycles:
    def _refused_without_write(self, W, repo, fn, *refs):
        before = _log(repo).read_bytes()
        with pytest.raises(W.Refused) as err:
            fn()
        assert _log(repo).read_bytes() == before
        for ref in refs:
            assert ref in str(err.value)
        return str(err.value)

    def test_a_direct_cycle_is_refused_naming_the_path(self, world):
        W, R, store, repo = world
        a, b = store.add("a"), store.add("b")
        store.dep(a, b)
        message = self._refused_without_write(W, repo, lambda: store.dep(b, a), a, b)
        assert "cycle" in message

    def test_a_three_node_cycle_is_refused(self, world):
        W, R, store, repo = world
        a, b, c = store.add("a"), store.add("b"), store.add("c")
        store.dep(a, b)
        store.dep(b, c)
        self._refused_without_write(W, repo, lambda: store.dep(c, a), a, b, c)

    def test_a_self_dependency_is_refused(self, world):
        W, R, store, repo = world
        a = store.add("a")
        self._refused_without_write(W, repo, lambda: store.dep(a, a), a)

    def test_a_parent_cycle_is_refused(self, world):
        W, R, store, repo = world
        a, b, c = store.add("a"), store.add("b"), store.add("c")
        store.set(b, "parent", a)
        store.set(c, "parent", b)
        self._refused_without_write(W, repo, lambda: store.set(a, "parent", c), a, b, c)


# ---------------------------------------------------------------------------------------------
# item 5: a corrupt (partial) last line
# ---------------------------------------------------------------------------------------------

class TestCorruptLine:
    def test_a_truncated_last_line_is_skipped_counted_and_isolated(self, world):
        W, R, store, repo = world
        _populate(store)
        log = _log(repo)
        clean_events = R.read_log(log).events
        data = log.read_bytes()
        last_start = data.rstrip(b"\n").rfind(b"\n") + 1
        n_lines = data.count(b"\n")
        log.write_bytes(data[:last_start] + data[last_start:last_start + 25])   # crash mid-write
        parsed = R.read_log(log)
        assert [line for line, _ in parsed.corrupt] == [n_lines]
        assert R.state_digest(R.replay(parsed.events)) == R.state_digest(R.replay(clean_events[:-1]))
        new = store.add("after the crash")
        parsed = R.read_log(log)
        assert [line for line, _ in parsed.corrupt] == [n_lines]       # still isolated, same number
        assert new in R.replay(parsed.events).tasks
        assert R.load(repo).corrupt_count == 1


# ---------------------------------------------------------------------------------------------
# item 6: done needs evidence
# ---------------------------------------------------------------------------------------------

class TestEvidenceRule:
    def test_set_status_done_is_refused(self, world):
        W, R, store, repo = world
        a = store.add("a")
        with pytest.raises(W.Refused, match="evidence"):
            store.set(a, "status", "done")

    def test_a_raw_set_status_done_line_is_skipped_as_corrupt(self, world):
        """The read side refuses it too: a hand-written or foreign line cannot close a task."""
        W, R, store, repo = world
        a = store.add("a")
        with _log(repo).open("a") as log:
            log.write(json.dumps({"v": 1, "eid": "20990101T000000000Z-0badc0de",
                                  "ts": "2099-01-01T00:00:00.000Z", "by": "user", "op": "set",
                                  "t": a, "field": "status", "value": "done"}) + "\n")
        state = R.load(repo)
        assert state.tasks[a]["status"] == "planned" and state.corrupt_count == 1

    def test_done_without_evidence_is_refused(self, world):
        W, R, store, repo = world
        a = store.add("a")
        with pytest.raises(W.Refused):
            store.done(a, [])

    def test_done_with_a_missing_commit_is_refused(self, world):
        W, R, store, repo = world
        a = store.add("a")
        with pytest.raises(W.Refused, match="unverified: commit:"):
            store.done(a, ["commit:" + "0" * 40])
        assert R.load(repo).tasks[a]["status"] == "planned"

    def test_done_with_a_real_commit_succeeds(self, world):
        W, R, store, repo = world
        a = store.add("a")
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        store.done(a, ["commit:" + sha])
        task = R.load(repo).tasks[a]
        assert task["status"] == "done"
        assert [(e["target"], e["verified"]) for e in task["evidence"]] == [("commit:" + sha, True)]

    def test_a_tag_and_a_doc_at_a_sha_verify(self, world):
        W, R, store, repo = world
        (repo / "docs").mkdir()
        (repo / "docs" / "plan.md").write_text("plan\n")
        git(repo, "add", "docs/plan.md")
        git(repo, "commit", "-q", "-m", "plan")
        git(repo, "tag", "v1")
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        a, b = store.add("a"), store.add("b")
        store.done(a, ["tag:v1"])
        store.done(b, [f"doc:docs/plan.md@{sha}"])
        state = R.load(repo)
        assert state.tasks[a]["status"] == state.tasks[b]["status"] == "done"

    def test_run_evidence_alone_is_refused_as_unverified(self, world):
        W, R, store, repo = world
        a = store.add("a")
        with pytest.raises(W.Refused, match="unverified: run:"):
            store.done(a, ["run:2026-09-27T195022Z-97299-python.log"])

    def test_a_parent_with_an_open_child_is_refused(self, world):
        W, R, store, repo = world
        parent, child = store.add("parent"), store.add("child")
        store.set(child, "parent", parent)
        sha = git(repo, "rev-parse", "HEAD").stdout.strip()
        with pytest.raises(W.Refused) as err:
            store.done(parent, ["commit:" + sha])
        assert child in str(err.value)
        store.done(child, ["commit:" + sha])
        store.done(parent, ["commit:" + sha])
        assert R.load(repo).tasks[parent]["status"] == "done"


# ---------------------------------------------------------------------------------------------
# item 8: D25 (credential text) and D26 (paths)
# ---------------------------------------------------------------------------------------------

class TestScrubAndD26:
    def test_a_credential_in_a_title_is_refused_naming_only_the_kind(self, world):
        W, R, store, repo = world
        before = _log(repo).read_bytes()
        with pytest.raises(W.Refused) as err:
            store.add(f"rotate {INERT_MARKER} today")
        assert "test-secret" in str(err.value)
        assert INERT_MARKER not in str(err.value)
        assert _log(repo).read_bytes() == before

    def test_a_credential_in_a_note_or_tag_is_refused(self, world):
        W, R, store, repo = world
        a = store.add("a")
        for field, value in (("note", f"key {INERT_MARKER}"), ("tags", [INERT_MARKER])):
            with pytest.raises(W.Refused):
                store.set(a, field, value)

    def test_a_kb_link_that_climbs_out_is_refused(self, world):
        W, R, store, repo = world
        a = store.add("a")
        with pytest.raises(W.Refused):
            store.link(a, "reference", "kb:../../01-Personal/x.md")

    def test_a_kb_symlink_pointing_out_of_the_kb_root_is_refused(self, world, tmp_path):
        W, R, store, repo = world
        kb = tmp_path / "vault" / "05-Education" / "Coding-Notes"
        (tmp_path / "vault" / "01-Personal" / "x.md").write_text("private\n")
        (kb / "escape.md").symlink_to(tmp_path / "vault" / "01-Personal" / "x.md")
        (kb / "Patterns").mkdir()
        (kb / "Patterns" / "ok.md").write_text("ok\n")
        a = store.add("a")
        with pytest.raises(W.Refused):
            store.link(a, "reference", "kb:escape.md")
        store.link(a, "reference", "kb:Patterns/ok.md")
        assert ("reference", "kb:Patterns/ok.md") in {(l["kind"], l["target"])
                                                     for l in R.load(repo).tasks[a]["links"]}

    @pytest.mark.parametrize("target", ["doc:/etc/hosts", "doc:../outside.md", "doc:docs/../../x.md"])
    def test_an_absolute_or_escaping_doc_path_is_refused(self, world, target):
        W, R, store, repo = world
        a = store.add("a")
        with pytest.raises(W.Refused):
            store.link(a, "output", target)


class TestSizeLimits:
    def test_an_event_over_4_kb_is_refused_naming_its_longest_field(self, world):
        W, R, store, repo = world
        before = _log(repo).read_bytes()
        with pytest.raises(W.Refused, match="longest field: tags"):
            store.add("many tags", tags=[f"tag-{i:04d}-{'x' * 30}" for i in range(120)])
        assert _log(repo).read_bytes() == before

    def test_a_note_over_500_characters_is_refused_not_shortened(self, world):
        W, R, store, repo = world
        with pytest.raises(W.Refused, match="500"):
            store.add("long note", note="n" * 501)


# ---------------------------------------------------------------------------------------------
# rev 2 (architect-pact review of P1)
# ---------------------------------------------------------------------------------------------

def _raw(repo, **event):
    """Hand-append one event line, bypassing the writer (a foreign or hand-edited line)."""
    base = {"v": 1, "eid": "20990101T000000000Z-0badc0de", "ts": "2099-01-01T00:00:00.000Z", "by": "user"}
    with _log(repo).open("a") as log:
        log.write(json.dumps({**base, **event}) + "\n")


class TestRawEventsCannotCloseWithoutVerifiedEvidence:
    """R1: the read side refuses a done (or a verified accept) that carries no verified item."""

    @pytest.mark.parametrize("evidence", [[], [{"target": "commit:" + "0" * 40, "verified": False}]])
    def test_a_raw_done_without_a_verified_item_is_corrupt(self, world, evidence):
        W, R, store, repo = world
        a = store.add("a")
        _raw(repo, op="done", t=a, evidence=evidence)
        state = R.load(repo)
        assert state.tasks[a]["status"] == "planned" and state.corrupt_count == 1

    def test_a_raw_verified_accept_without_a_verified_item_is_corrupt(self, world):
        W, R, store, repo = world
        a = store.add("a")
        _raw(repo, op="accept", t=a, ptask="s-1/5", rev=1, verified=True, evidence=[])
        state = R.load(repo)
        assert state.tasks[a]["status"] == "planned" and state.corrupt_count == 1

    def test_an_unverified_accept_with_no_evidence_is_well_formed(self, world):
        W, R, store, repo = world
        a = store.add("a")
        _raw(repo, op="accept", t=a, ptask="s-1/5", rev=1, verified=False, evidence=[])
        state = R.load(repo)
        assert state.corrupt_count == 0 and state.tasks[a]["status"] == "planned"
        assert R.accepted_without_evidence(state)


class TestEvidenceTargetsFollowTheLinkRules:
    """R2 and ruling 1: every evidence target passes the link rules of its kind before it is
    recorded; run: is a bare log file name only."""

    def _sha(self, repo):
        return git(repo, "rev-parse", "HEAD").stdout.strip()

    @pytest.mark.parametrize("bad", [
        "run:/Users/someone/Obsidian Vault - New/01-Personal/x.md",
        "run:../../01-Personal/x.md", "run:~/x.log", "run:runs/x.log",
        "kb:../../01-Personal/x.md", "doc:/etc/hosts", "nonsense:x",
        "branch:main", "kb:Patterns/ok.md",          # valid LINKS, but not evidence kinds
    ])
    def test_a_bad_item_beside_a_verified_one_refuses_the_whole_done(self, world, bad):
        W, R, store, repo = world
        a = store.add("a")
        before = _log(repo).read_bytes()
        with pytest.raises(W.Refused):
            store.done(a, ["commit:" + self._sha(repo), bad])
        assert _log(repo).read_bytes() == before

    def test_a_bare_run_name_is_recorded_as_the_name_only(self, world):
        W, R, store, repo = world
        a = store.add("a")
        name = "2026-09-27T195022Z-97299-python.log"
        store.done(a, ["commit:" + self._sha(repo), "run:" + name])
        items = {e["target"]: e["verified"] for e in R.load(repo).tasks[a]["evidence"]}
        assert items == {"commit:" + self._sha(repo): True, "run:" + name: False}

    @pytest.mark.parametrize("bad", ["run:/var/log/x.log", "run:../x.log", "run:~x.log", "run:a/b.log"])
    def test_a_run_link_is_a_bare_name_too(self, world, bad):
        W, R, store, repo = world
        a = store.add("a")
        with pytest.raises(W.Refused):
            store.link(a, "reference", bad)
        store.link(a, "reference", "run:x-python.log")


class TestReopen:
    """Ruling 2: done reopens to active; dropped reopens to planned (back in the ready queue)."""

    def test_done_reopens_to_active(self, world):
        W, R, store, repo = world
        a = store.add("a")
        store.done(a, ["commit:" + git(repo, "rev-parse", "HEAD").stdout.strip()])
        store.reopen(a, note="regressed")
        assert R.load(repo).tasks[a]["status"] == "active"

    def test_dropped_reopens_to_planned_and_is_ready_again(self, world):
        W, R, store, repo = world
        a = store.add("a")
        store.drop(a)
        store.reopen(a)
        state = R.load(repo)
        assert state.tasks[a]["status"] == "planned"
        assert [t["ref"] for t in R.ready(state)] == [a]


class TestRefCollision:
    """S2: a second create for an existing ref (a cross-clone collision) is flagged, never
    silently ignored, and the kept create does not depend on line order."""

    def test_a_second_create_is_flagged_and_order_independent(self, world):
        W, R, store, repo = world
        a = store.add("first title")
        _raw(repo, op="create", t=a, title="colliding title", status="planned", priority="P1")
        events = R.read_log(_log(repo)).events
        one, other = R.replay(events), R.replay(list(reversed(events)))
        assert R.state_digest(one) == R.state_digest(other)
        assert [c["ref"] for c in one.ref_conflicts] == [a]
        assert one.tasks[a]["title"] == "first title"
        assert any("second create" in flag for flag in R.drift_flags(one))


class TestAcceptAndReject:
    """The acceptance bridge's writers (lead ruling on #173: they live with the store). Each is
    idempotent on (op, ptask, rev) under the lock; accept checks every target against the link
    rules first, and is verified when any item verifies."""

    def _sha(self, repo):
        return git(repo, "rev-parse", "HEAD").stdout.strip()

    def test_a_verified_accept_closes_the_task(self, world):
        W, R, store, repo = world
        a = store.add("a")
        assert store.accept(a, "session-1/7", 1, ["commit:" + self._sha(repo)]) is True
        task = R.load(repo).tasks[a]
        assert task["status"] == "done"
        assert task["accepts"][0]["ptask"] == "session-1/7" and task["accepts"][0]["verified"] is True
        assert ("commit:" + self._sha(repo)) in [e["target"] for e in task["evidence"]]

    def test_an_accept_with_nothing_verified_leaves_the_task_open_and_flagged(self, world):
        W, R, store, repo = world
        a = store.add("a")
        store.start(a)
        store.accept(a, "session-1/7", 1, ["run:2026-09-27T195022Z-97299-python.log"])
        state = R.load(repo)
        assert state.tasks[a]["status"] == "active"
        assert [t["ref"] for t in R.accepted_without_evidence(state)] == [a]

    def test_an_accept_with_no_evidence_at_all_is_recorded_unverified(self, world):
        W, R, store, repo = world
        a = store.add("a")
        store.accept(a, "session-1/7", 1, [])
        assert R.load(repo).tasks[a]["accepts"][0]["verified"] is False

    def test_a_repeated_accept_writes_once(self, world):
        W, R, store, repo = world
        a = store.add("a")
        store.accept(a, "session-1/7", 1, [])
        before = _log(repo).read_bytes()
        assert store.accept(a, "session-1/7", 1, []) is False
        assert _log(repo).read_bytes() == before
        assert store.accept(a, "session-1/7", 2, []) is True          # a new revision is new

    def test_an_accept_target_that_breaks_the_link_rules_refuses_it(self, world):
        W, R, store, repo = world
        a = store.add("a")
        before = _log(repo).read_bytes()
        with pytest.raises(W.Refused):
            store.accept(a, "session-1/7", 1, ["commit:" + self._sha(repo), "kb:../../01-Personal/x.md"])
        assert _log(repo).read_bytes() == before

    def test_a_verified_accept_on_a_parent_with_an_open_child_is_refused(self, world):
        W, R, store, repo = world
        parent, child = store.add("parent"), store.add("child")
        store.set(child, "parent", parent)
        with pytest.raises(W.Refused) as err:
            store.accept(parent, "session-1/7", 1, ["commit:" + self._sha(repo)])
        assert child in str(err.value)
        store.accept(parent, "session-1/8", 1, [])                    # unverified: closes nothing
        assert R.load(repo).tasks[parent]["status"] == "planned"

    @pytest.mark.parametrize("ptask", ["session-1", "session 1/7", "../x/7", ""])
    def test_a_malformed_ptask_is_refused(self, world, ptask):
        W, R, store, repo = world
        a = store.add("a")
        with pytest.raises(W.Refused):
            store.accept(a, ptask, 1, [])
        with pytest.raises(W.Refused):
            store.reject(a, ptask, 1)

    def test_a_reject_is_recorded_once_and_changes_no_status(self, world):
        W, R, store, repo = world
        a = store.add("a")
        store.start(a)
        assert store.reject(a, "session-1/7", 1) is True
        before = _log(repo).read_bytes()
        assert store.reject(a, "session-1/7", 1) is False
        assert _log(repo).read_bytes() == before
        task = R.load(repo).tasks[a]
        assert task["status"] == "active" and [(r["ptask"], r["rev"]) for r in task["rejects"]] == [("session-1/7", 1)]


# ---------------------------------------------------------------------------------------------
# the read side stays importable by the session-start brief (§7): stdlib only
# ---------------------------------------------------------------------------------------------

def test_the_read_side_imports_only_the_stdlib_and_no_subprocess_or_network():
    tree = ast.parse(READER_PATH.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    names.discard("__future__")
    import sys
    assert names <= set(sys.stdlib_module_names), names
    assert not names & {"subprocess", "socket", "urllib", "http", "sqlite3", "multiprocessing"}, names


# ---------------------------------------------------------------------------------------------
# speed (§3.4): replay of a 512 KB log, measured and pinned
# ---------------------------------------------------------------------------------------------

# Measured 2026-09-27 (P1): parse 7.0 ms + replay 1.9 ms + summary 1.9 ms standalone; 15-18 ms for
# load + summary inside pytest at nice 19 (2,628 events, 876 tasks). The pin is about 5x that, a
# regression tripwire; P3 owns the tighter 25 ms brief pin. Under a loaded `nice 19` run the wall
# clock reached 0.155 s, so the budget also scales with a same-run baseline (a plain json.loads of
# every line, the cheapest part of the load): load + summary may take REPLAY_BASELINE_RATIO times
# that. Standalone the ratio is ~4 (11 ms vs 2.6 ms); a quadratic replay would be orders above.
REPLAY_BUDGET_S = 0.1
REPLAY_BASELINE_RATIO = 10


def test_replay_of_a_512_kb_log_is_measured(world, capsys):
    W, R, store, repo = world
    lines, size, n = [], 0, 0
    while size < 512 * 1024:
        ref = f"T-{n:08x}"
        ts = f"2026-09-27T10:{(n // 60) % 60:02d}:{n % 60:02d}.{n % 1000:03d}Z"
        for i, event in enumerate((
                {"op": "create", "t": ref, "title": f"synthetic task number {n}", "status": "planned",
                 "priority": "P2"},
                {"op": "set", "t": ref, "field": "note", "value": "x" * 120},
                {"op": "dep_add", "t": ref, "on": f"T-{max(n - 1, 0):08x}"} if n else
                {"op": "set", "t": ref, "field": "rank", "value": 1})):
            event = {"v": 1, "eid": f"20260927T10{n:06d}{i}Z-{n:08x}", "ts": ts, "by": "import", **event}
            line = json.dumps(event, separators=(",", ":")) + "\n"
            lines.append(line)
            size += len(line)
        n += 1
    _log(repo).write_text("".join(lines))

    def parse_baseline() -> float:
        started = time.perf_counter()
        for line in lines:
            json.loads(line)
        return time.perf_counter() - started

    before = parse_baseline()
    started = time.perf_counter()
    state = R.load(repo)
    R.summary_line(state)
    elapsed = time.perf_counter() - started
    baseline = max(before, parse_baseline())
    budget = max(REPLAY_BUDGET_S, REPLAY_BASELINE_RATIO * baseline)
    with capsys.disabled():
        print(f"\nREPLAY 512KB: {len(lines)} events, {n} tasks, {elapsed * 1000:.1f} ms "
              f"(parse baseline {baseline * 1000:.1f} ms, budget {budget * 1000:.0f} ms)")
    assert len(state.tasks) == n
    assert elapsed < budget


# D10 P3 (lead addition): drift_flags classified every planned task with a fresh open_children
# index, so it was quadratic (MEASURED 0.757 s on 3,000 tasks, beside ready() at 0.003 s). It builds
# the index once. The budget is ~10x the linear cost, a tripwire that does not flake.
DRIFT_BUDGET_S = 0.15


def test_drift_flags_is_linear_on_3000_tasks(capsys):
    R = reader()
    events = []
    for n in range(3000):
        ref = f"T-{n:08x}"
        ts = f"2026-09-27T10:{(n // 60) % 60:02d}:{n % 60:02d}.{n % 1000:03d}Z"
        event = {"v": 1, "eid": f"20260927T10{n:07d}Z-{n:08x}", "ts": ts, "by": "import",
                 "op": "create", "t": ref, "title": f"task {n}", "status": "planned", "priority": "P2"}
        if n % 10:
            event["parent"] = f"T-{n - n % 10:08x}"
        events.append(event)
    state = R.replay(events)
    assert len(state.tasks) == 3000
    started = time.perf_counter()
    R.drift_flags(state)
    elapsed = time.perf_counter() - started
    with capsys.disabled():
        print(f"\nDRIFT FLAGS 3000 tasks: {elapsed * 1000:.1f} ms")
    assert elapsed < DRIFT_BUDGET_S
