"""
D10 P1 TASKS-CORE (the design notes §3, §4, §9; test plan §12.1
items 1, 2, 5, 6 and 8): replay determinism, cycles, a corrupt line, the evidence rule, the
credential and D26 rules, where the log lives, and the read side's import closure.
"""
from __future__ import annotations

import ast
import gc
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


class TestSizeLimits:
    def test_an_event_over_4_kb_is_refused_naming_its_longest_field(self, world):
        W, R, store, repo = world
        before = _log(repo).read_bytes()
        with pytest.raises(W.Refused, match="longest field: tags"):
            store.add("many tags", tags=[f"tag-{i:04d}-{'x' * 30}" for i in range(120)])
        assert _log(repo).read_bytes() == before


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

    @pytest.mark.parametrize("evidence", [[],])
    def test_a_raw_done_without_a_verified_item_is_corrupt(self, world, evidence):
        W, R, store, repo = world
        a = store.add("a")
        _raw(repo, op="done", t=a, evidence=evidence)
        state = R.load(repo)
        assert state.tasks[a]["status"] == "planned" and state.corrupt_count == 1


class TestEvidenceTargetsFollowTheLinkRules:
    """R2 and ruling 1: every evidence target passes the link rules of its kind before it is
    recorded; run: is a bare log file name only."""

    def _sha(self, repo):
        return git(repo, "rev-parse", "HEAD").stdout.strip()

    @pytest.mark.parametrize("bad", [
        "run:/Users/someone/Obsidian Vault - New/01-Personal/x.md",])
    def test_a_bad_item_beside_a_verified_one_refuses_the_whole_done(self, world, bad):
        W, R, store, repo = world
        a = store.add("a")
        before = _log(repo).read_bytes()
        with pytest.raises(W.Refused):
            store.done(a, ["commit:" + self._sha(repo), bad])
        assert _log(repo).read_bytes() == before


class TestReopen:
    """Ruling 2: done reopens to active; dropped reopens to planned (back in the ready queue)."""

    def test_done_reopens_to_active(self, world):
        W, R, store, repo = world
        a = store.add("a")
        store.done(a, ["commit:" + git(repo, "rev-parse", "HEAD").stdout.strip()])
        store.reopen(a, note="regressed")
        assert R.load(repo).tasks[a]["status"] == "active"


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


# ---------------------------------------------------------------------------------------------
# the read side stays importable by the session-start brief (§7): stdlib only
# ---------------------------------------------------------------------------------------------

def _stdlib_names(names):
    """The members of `names` that are stdlib modules. sys.stdlib_module_names on 3.10+; on
    3.9 each is judged by where it is found: built-in, frozen, or under the stdlib dir and
    outside site-packages (realpath both sides: Homebrew reports the stdlib via opt/)."""
    import sys
    if hasattr(sys, "stdlib_module_names"):
        return names & set(sys.stdlib_module_names)
    import importlib.util
    import os
    import sysconfig
    paths = {k: os.path.realpath(v) for k, v in sysconfig.get_paths().items()}
    site = (paths["purelib"], paths["platlib"])
    found = set()
    for name in names:
        spec = importlib.util.find_spec(name)
        origin = getattr(spec, "origin", None) or ""
        if origin in ("built-in", "frozen") or (
                origin and os.path.realpath(origin).startswith(paths["stdlib"])
                and not os.path.realpath(origin).startswith(site)):
            found.add(name)
    return found


def test_the_read_side_imports_only_the_stdlib_and_no_subprocess_or_network():
    tree = ast.parse(READER_PATH.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    names.discard("__future__")
    assert names <= _stdlib_names(names), names
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
# A full (generation-2) collection walks every object the whole suite has left on the heap, so one
# landing in the timed window measures the test process, not the replay: on Python 3.14 in CI it
# took the window to 204-475 ms (2026-10-17), and a per-file probe of the local suite caught 40-90
# ms single passes. The test collects first, so the next full pass cannot be due inside the window,
# and asserts that none ran there.
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

    def note_full_pass(phase, info):
        if phase == "start" and info["generation"] == 2:
            full_passes.append(info)

    before = parse_baseline()
    full_passes = []
    gc.collect()
    gc.callbacks.append(note_full_pass)
    try:
        started = time.perf_counter()
        state = R.load(repo)
        R.summary_line(state)
        elapsed = time.perf_counter() - started
    finally:
        gc.callbacks.remove(note_full_pass)
    assert not full_passes, "a full collection ran inside the timed window"
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
