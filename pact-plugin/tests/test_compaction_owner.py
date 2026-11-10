"""Tests for hooks/shared/compaction_owner.py.

An in-process teammate's compaction frames are lead-shaped, and in every capture
the transcript records that tell the two apart landed after the compaction hooks
had returned. So
PostCompact stages the summary and settle() decides later. Every arm builds a
temporary projects/ tree under the config root the autouse fixture redirects
to, stages through stage_summary, and drives settle() with a fake clock: `now`
is fixed, `monotonic` advances only when `sleep` is called, and a callback can
append records on the Nth sleep.
"""

import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from shared import compaction_owner as co

HOOKS = Path(__file__).resolve().parents[1] / "hooks"
STAGED = datetime(2026, 9, 14, 1, 24, 47, tzinfo=timezone.utc)
SID = "4ec31948-bbe5-4ef4-841c-631d1ef31e61"
BODY = (
    "1. Primary Request and Intent: read three short text files in a scratch project "
    "and report their line counts to the team-lead.\n2. Key Technical Concepts: reading "
    "files and counting lines.\n3. Current Work: the counts were gathered and sent.\n"
    "4. Pending Tasks: none."
)
SUMMARY = f"<analysis>\nWorking notes that are not kept.\n</analysis>\n\n<summary>\n{BODY}\n</summary>"
BODY_B = BODY.replace("three short text files", "two long text files")
SUMMARY_B = f"<summary>\n{BODY_B}\n</summary>"
LEAD_OWN = "THE LEAD'S OWN SUMMARY"


class Runaway(BaseException):
    """Raised past settle()'s own except when a poll does not stop."""


class Clock:
    """now() is fixed; monotonic() advances only through sleep()."""

    def __init__(self, at=STAGED, on_sleep=None, limit=200):
        self.at = at
        self.t = 1000.0
        self.sleeps = 0
        self.slept = 0.0
        self.on_sleep = on_sleep or {}
        self.limit = limit

    def now(self):
        return self.at

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps += 1
        if self.sleeps > self.limit:
            raise Runaway("settle did not stop polling")
        self.slept += seconds
        self.t += seconds
        action = self.on_sleep.get(self.sleeps)
        if action:
            action()


def later(seconds, **kwargs):
    return Clock(at=STAGED + timedelta(seconds=seconds), **kwargs)


def staged_ns(pending):
    return pending.name[len("compact-summary.pending-"):-len(".json")]


class Tree:
    """projects/<slug>/<sid>.jsonl, <sid>/subagents/agent-*.jsonl and a session dir."""

    def __init__(self, root):
        self.project = root / "projects" / "-scratch-cmp-lead"
        self.subagents = self.project / SID / "subagents"
        self.subagents.mkdir(parents=True)
        self.lead = self.project / f"{SID}.jsonl"
        self.teammate = self.subagents / "agent-acmp-probe-0123456789abcdef.jsonl"
        for path in (self.lead, self.teammate):
            self.append(path, {"type": "user", "message": {"role": "user", "content": "earlier turn"}})
        self.session = root / "pact-sessions" / "scratch-cmp-lead" / SID
        self.session.mkdir(parents=True)
        self.canonical = self.session / "compact-summary.txt"

    @staticmethod
    def append(path, record):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def summary(self, path, body=BODY):
        self.append(path, {"type": "user", "isCompactSummary": True,
                           "message": {"role": "user",
                                       "content": "This session is being continued from a previous conversation.\n\nSummary:\n" + body}})

    def frame(self, summary=SUMMARY):
        return {"hook_event_name": "PostCompact", "agent_type": "repacted:pact-orchestrator", "session_id": SID,
                "transcript_path": str(self.lead), "compact_summary": summary, "trigger": "auto"}

    def stage(self, summary=SUMMARY, at=STAGED, frame=None):
        assert co.stage_summary(frame or self.frame(summary), str(self.session), now=lambda: at)
        return sorted(self.session.glob("compact-summary.pending-*.json"), key=lambda p: int(staged_ns(p)))[-1]

    def settle(self, clock, wait_s=0.0):
        return co.settle(str(self.session), wait_s=wait_s, now=clock.now, monotonic=clock.monotonic, sleep=clock.sleep)

    def names(self):
        return sorted(path.name for path in self.session.iterdir())

    def kept(self, kind):
        return sorted(int(path.name.rsplit("-", 1)[1][:-len(".txt")])
                      for path in self.session.glob(f"compact-summary.{kind}-*.txt"))

    def attributions(self):
        journal = self.session / "session-journal.jsonl"
        if not journal.exists():
            return []
        events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        return [event for event in events if event.get("type") == "compaction_attributed"]

    def verdicts(self):
        return [(event["verdict"], event["basis"]) for event in self.attributions()]


@pytest.fixture
def tree(tmp_path):
    return Tree(tmp_path / ".claude")


def _fill(path, mebibytes):
    line = json.dumps({"type": "assistant", "message": {"content": "x" * 1000}}) + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line * (mebibytes * 1024 * 1024 // len(line) + 1))


# --------------------------------------------------------------------------
# Stage
# --------------------------------------------------------------------------


def test_stage_writes_one_private_pending_file_and_leaves_the_summary_file_alone(tree):
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    pending = tree.stage()
    assert tree.canonical.read_text(encoding="utf-8") == LEAD_OWN
    assert tree.names() == [pending.name, "compact-summary.txt"]
    assert stat.S_IMODE(pending.stat().st_mode) == 0o600
    record = json.loads(pending.read_text(encoding="utf-8"))
    assert set(record) == {"summary", "transcript_path", "session_id", "staged_at", "offsets"}
    assert (record["summary"], record["session_id"], record["transcript_path"]) == (SUMMARY, SID, str(tree.lead))
    assert datetime.fromisoformat(record["staged_at"]) == STAGED
    assert record["offsets"] == {
        os.path.realpath(tree.lead): tree.lead.stat().st_size,
        os.path.realpath(tree.teammate): tree.teammate.stat().st_size,
    }


@pytest.mark.parametrize("summary", ["",])
def test_stage_without_a_summary_writes_nothing(tree, summary):
    frame = {**tree.frame(), "compact_summary": summary}
    assert co.stage_summary(frame, str(tree.session)) is False
    assert tree.names() == []


def test_stage_records_no_offsets_when_the_transcripts_cannot_be_located(tree):
    pending = tree.stage(frame={**tree.frame(), "session_id": "../escape"})
    assert json.loads(pending.read_text(encoding="utf-8"))["offsets"] == {}


def test_an_offset_failure_stages_the_summary_without_offsets(tree, monkeypatch):
    """A transcript that vanishes between locating it and reading its size must
    not cost the summary: it is staged without offsets and read from the start."""
    real_transcripts = co._transcripts

    def vanished(*args):
        raise FileNotFoundError("the transcript went away before its size was read")

    monkeypatch.setattr(co, "_transcripts", vanished)
    pending = tree.stage()
    record = json.loads(pending.read_text(encoding="utf-8"))
    assert (record["summary"], record["offsets"]) == (SUMMARY, {})
    monkeypatch.setattr(co, "_transcripts", real_transcripts)
    tree.summary(tree.lead)
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]


# --------------------------------------------------------------------------
# Settle: the verdicts
# --------------------------------------------------------------------------


def test_a_record_in_the_lead_transcript_promotes_the_summary(tree):
    tree.canonical.write_text("AN OLDER LEAD SUMMARY", encoding="utf-8")
    tree.stage()
    tree.summary(tree.lead)
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == SUMMARY
    assert stat.S_IMODE(tree.canonical.stat().st_mode) == 0o600
    assert tree.names() == ["compact-summary.head.txt", "compact-summary.txt", "session-journal.jsonl"]
    [event] = tree.attributions()
    assert (event["verdict"], event["basis"], event["latency_s"]) == ("lead", "content", 9.0)


def test_a_record_in_a_teammate_transcript_keeps_the_lead_summary(tree):
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    ns = staged_ns(tree.stage())
    tree.summary(tree.teammate)
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == LEAD_OWN
    assert (tree.session / f"compact-summary.teammate-{ns}.txt").read_text(encoding="utf-8") == SUMMARY
    assert tree.names() == ["compact-summary.teammate-" + ns + ".txt", "compact-summary.txt", "session-journal.jsonl"]
    assert tree.verdicts() == [("teammate", "content")]


@pytest.mark.parametrize("seconds", [0,])
def test_an_unmatched_summary_younger_than_expiry_stays_staged(tree, seconds):
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    pending = tree.stage()
    assert tree.settle(later(seconds)) == []
    assert tree.names() == [pending.name, "compact-summary.txt"]
    assert tree.canonical.read_text(encoding="utf-8") == LEAD_OWN
    assert tree.attributions() == []


def test_an_expired_summary_is_parked_even_when_there_is_no_summary_file(tree):
    """A summary no transcript records may be a teammate's, so it never becomes
    compact-summary.txt, even when that file does not exist."""
    ns = staged_ns(tree.stage())
    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED)]
    assert not tree.canonical.exists()
    assert (tree.session / f"compact-summary.unattributed-{ns}.txt").read_text(encoding="utf-8") == SUMMARY


def test_an_expired_summary_never_replaces_an_existing_summary_file(tree):
    tree.canonical.write_text(LEAD_OWN, encoding="utf-8")
    ns = staged_ns(tree.stage())
    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED)]
    assert tree.canonical.read_text(encoding="utf-8") == LEAD_OWN
    assert (tree.session / f"compact-summary.unattributed-{ns}.txt").read_text(encoding="utf-8") == SUMMARY


@pytest.mark.parametrize("canonical", [None,])
@pytest.mark.parametrize("content", ["{not json",])
def test_an_unreadable_staged_file_is_parked_and_never_becomes_the_summary_file(tree, canonical, content):
    if canonical:
        tree.canonical.write_text(canonical, encoding="utf-8")
    (tree.session / "compact-summary.pending-1000.json").write_text(content, encoding="utf-8")
    assert tree.settle(Clock()) == [(co.UNKNOWN, co.EXPIRED)]
    assert (tree.session / "compact-summary.unattributed-1000.txt").read_text(encoding="utf-8") == content
    assert tree.canonical.exists() is bool(canonical)
    if canonical:
        assert tree.canonical.read_text(encoding="utf-8") == canonical
    [event] = tree.attributions()
    assert "latency_s" not in event


_PARK_CHILD = """
import json, sys
from shared import compaction_owner as co
print(json.dumps(co.settle(sys.argv[1])))
"""


def test_a_fifo_pending_is_parked_unread_without_stalling(tree):
    """Opening a FIFO blocks until a writer appears, so settle runs in a child
    process that a timeout can end."""
    os.mkfifo(tree.session / "compact-summary.pending-1000.json")
    root = tree.project.parents[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}
    env.update(PYTHONPATH=str(HOOKS), CLAUDE_CONFIG_DIR=str(root), HOME=str(root.parent))
    try:
        proc = subprocess.run([sys.executable, "-c", _PARK_CHILD, str(tree.session)],
                              capture_output=True, text=True, timeout=15, env=env)
    except subprocess.TimeoutExpired:
        pytest.fail("settle blocked opening a FIFO pending")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == [[co.UNKNOWN, co.EXPIRED]]
    assert stat.S_ISFIFO(os.lstat(tree.session / "compact-summary.unattributed-1000.txt").st_mode)
    [event] = tree.attributions()
    assert (event["verdict"], event["basis"]) == ("unknown", "expired") and "latency_s" not in event


def test_a_linked_pending_is_parked_without_reading_through_the_link(tree, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"summary": "TEXT FROM OUTSIDE THE SESSION", "transcript_path": str(tree.lead),
                                   "session_id": SID, "staged_at": STAGED.isoformat(), "offsets": {}}), encoding="utf-8")
    before = outside.read_bytes()
    (tree.session / "compact-summary.pending-1000.json").symlink_to(outside)
    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED)]
    parked = tree.session / "compact-summary.unattributed-1000.txt"
    assert parked.is_symlink() and os.readlink(parked) == str(outside)
    assert outside.read_bytes() == before
    assert not tree.canonical.exists()
    [event] = tree.attributions()
    assert "latency_s" not in event


def test_a_stamp_far_in_the_future_cannot_hold_the_queue(tree):
    tree.stage(at=STAGED + timedelta(days=1))
    assert tree.settle(Clock()) == [(co.UNKNOWN, co.EXPIRED)]


def test_a_summary_whose_transcript_cannot_be_located_never_blocks_a_later_one(tree):
    """A lookup that raises must make the summary unattributable, not restart the
    pass. Restored and re-raised, one bad record holds the queue for good: the
    raise comes before the age check, so it never expires either."""
    poison = tree.stage(frame={**tree.frame(), "transcript_path": f"{tree.lead}\x00x"})
    tree.stage(SUMMARY_B)
    tree.summary(tree.lead, body=BODY_B)

    assert tree.settle(later(9)) == []
    assert not tree.canonical.exists(), "the poisoned summary must hold its place until it expires"

    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED), (co.LEAD, co.CONTENT)]
    assert tree.canonical.read_text(encoding="utf-8") == SUMMARY_B
    parked = tree.session / f"compact-summary.unattributed-{staged_ns(poison)}.txt"
    assert parked.read_text(encoding="utf-8") == SUMMARY
    assert tree.verdicts() == [("unknown", "expired"), ("lead", "content")]


def test_any_raising_transcript_lookup_parks_the_summary(tree, monkeypatch):
    """The guard is not narrowed to the one error a NUL path happens to raise."""
    def unreachable(*args):
        raise OSError("the transcripts directory is unreadable")

    monkeypatch.setattr(co, "_locate", unreachable)
    tree.stage()
    assert tree.settle(later(co.EXPIRE_S + 1)) == [(co.UNKNOWN, co.EXPIRED)]
    assert not tree.canonical.exists()


def _dropped(tree):
    """The cause of each compaction_summary_dropped row that actually landed."""
    journal = tree.session / "session-journal.jsonl"
    if not journal.exists():
        return []
    events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    return [event["cause"] for event in events if event.get("type") == "compaction_summary_dropped"]


@pytest.mark.parametrize("frame", [None,],
                         ids=["null",])
def test_a_frame_that_is_not_a_dict_is_refused_without_a_drop_row(tree, frame):
    """Each non-object shape a JSON frame can take. It holds no summary, so there is
    nothing to discard, and a drop row would falsely record a lost one."""
    assert co.stage_summary(frame, str(tree.session), now=lambda: STAGED) is False
    assert _dropped(tree) == [], "a malformed frame was journaled as a discarded summary"
    assert tree.names() == []


def test_an_empty_session_dir_writes_nothing_into_the_working_directory(tree, tmp_path, monkeypatch):
    """Path("") is ".", so staging into an empty session_dir would write the summary,
    which is conversation content, into whatever directory the hook runs in: the
    user's project, where it can be committed."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    assert co.stage_summary(tree.frame(), "", now=lambda: STAGED) is False
    assert sorted(path.name for path in project.iterdir()) == []


def test_a_stage_that_finds_no_free_stamp_journals_the_discard(tree, monkeypatch):
    """Every stamp the stage would try is taken, so the summary cannot be kept. It is
    lost either way; what must not happen is losing it without a trace."""
    stamp = 1_800_000_000_000_000_000
    for offset in range(co.STAGE_TRIES):
        (tree.session / f"compact-summary.pending-{stamp + offset}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(co.time, "time_ns", lambda: stamp)

    assert co.stage_summary(tree.frame(), str(tree.session), now=lambda: STAGED) is False
    assert _dropped(tree) == ["no_free_stamp"], "the discarded summary left no journal row"


def test_a_stage_the_filesystem_will_not_link_journals_the_discard_with_its_cause(tree, monkeypatch):
    """A discard that is not exhaustion: the filesystem refuses hard links. The session
    dir is still writable, so the discard can be journaled, and the cause says why."""
    def refused(src, dst, *args, **kwargs):
        raise PermissionError("hard links are not permitted on this filesystem")

    monkeypatch.setattr(co.os, "link", refused)
    assert co.stage_summary(tree.frame(), str(tree.session), now=lambda: STAGED) is False
    assert _dropped(tree) == ["PermissionError"], "a discard that was not exhaustion left no journal row"


def test_the_discard_is_journaled_under_a_registered_type_and_field():
    """A row landing does not show its type is registered: the journal validates only
    the types it knows and writes an unknown type unchecked. So the registration is
    pinned from the writer's side, where removing it would otherwise go unnoticed."""
    from shared.session_journal import _REQUIRED_FIELDS_BY_TYPE

    assert _REQUIRED_FIELDS_BY_TYPE.get("compaction_summary_dropped") == {"cause": str}


def test_a_journal_that_cannot_be_written_never_makes_the_stage_raise(tree, monkeypatch):
    """The stage runs inside a hook, so reporting a discard must not become a new way
    for the hook to fail."""
    def refused(src, dst, *args, **kwargs):
        raise PermissionError("hard links are not permitted on this filesystem")

    def unwritable(*args, **kwargs):
        raise OSError("the journal cannot be written")

    monkeypatch.setattr(co.os, "link", refused)
    monkeypatch.setattr(co.session_journal, "append_event", unwritable)
    assert co.stage_summary(tree.frame(), str(tree.session), now=lambda: STAGED) is False


def test_a_repeated_stamp_at_stage_time_destroys_the_earlier_record(tree, monkeypatch):
    """stage_summary names its pending from time_ns() and writes it with a helper
    that ends in os.replace, which overwrites. A repeated stamp therefore destroys
    the earlier record at the moment the later one is staged, before any claim
    exists, so none of the restore guards can reach it. The premise is this
    module's own: the free-name walk is justified by two summaries holding the
    same stamp when the wall clock steps back.
    """
    first = tree.stage()
    repeated = int(staged_ns(first))
    monkeypatch.setattr(co.time, "time_ns", lambda: repeated)
    tree.stage(SUMMARY_B)

    names = {SUMMARY: "lead", SUMMARY_B: "teammate"}
    kept = sorted(names.get(json.loads(path.read_text(encoding="utf-8"))["summary"], "unknown")
                  for path in tree.session.glob("compact-summary.pending-*.json"))
    assert kept == ["lead", "teammate"], "the second stage overwrote the first record"


def test_two_stages_on_the_real_clock_both_survive(tree):
    """Live positive control for the arm above, in the same run: on an unpinned
    clock two stages take different stamps and both records survive. Without it a
    null in that arm could mean the harness never staged at all, which would read
    as safety.
    """
    tree.stage()
    tree.stage(SUMMARY_B)

    names = {SUMMARY: "lead", SUMMARY_B: "teammate"}
    kept = sorted(names.get(json.loads(path.read_text(encoding="utf-8"))["summary"], "unknown")
                  for path in tree.session.glob("compact-summary.pending-*.json"))
    assert kept == ["lead", "teammate"]


# --------------------------------------------------------------------------
# Settle: offsets, order, claims and the poll
# --------------------------------------------------------------------------


def test_a_record_followed_by_nine_mebibytes_is_still_found_from_the_offset(tree):
    tree.stage()
    tree.summary(tree.lead)
    _fill(tree.lead, 9)
    assert tree.lead.stat().st_size > 9 * 1024 * 1024
    assert tree.settle(later(9)) == [(co.LEAD, co.CONTENT)]


def test_an_identical_body_recorded_before_the_offset_does_not_match(tree):
    tree.summary(tree.lead)
    tree.summary(tree.teammate)
    tree.stage()
    assert tree.settle(later(9)) == []


def test_a_transcript_now_shorter_than_its_offset_is_read_from_the_start(tree):
    _fill(tree.teammate, 1)
    tree.stage()
    tree.teammate.write_text("", encoding="utf-8")
    tree.summary(tree.teammate)
    assert tree.settle(later(9)) == [(co.TEAMMATE, co.CONTENT)]


def _claim(pending, claimed_at, pid=4242, base=None):
    """Rename a pending the way a settler claims it, stamped at claimed_at.

    base is the pending the claim resolves back to, its own unless an arm needs a
    claim that collides with a different pending.
    """
    claim_ns = int(claimed_at.timestamp()) * 1_000_000_000 + claimed_at.microsecond * 1_000
    claim = pending.with_name(f"{(base or pending).name}.claimed-{pid}-{claim_ns}")
    os.replace(pending, claim)
    return claim


def _second_commits_after_first_walks(monkeypatch, first, second):
    """Run another settler's whole walk-and-commit between one settler's walk and its commit.

    The walk only reports a name that was free when it looked. `first` names the
    pending the late settler walks from; `second` is the other settler, run to
    completion after that walk has chosen its stamp and before the late settler
    writes there. The other settler runs under a different pid, as it would in use:
    settlers are separate hook processes, so two of them never share one. That is
    simulated within this process by patching os.getpid; no second process runs.
    """
    real_free = co._free_pending
    real_pid = co.os.getpid
    fired = []

    def walk(folder, pending_name):
        chosen = real_free(folder, pending_name)
        if not fired and pending_name == first:
            fired.append(True)
            monkeypatch.setattr(co.os, "getpid", lambda: real_pid() + 1)
            try:
                second()
            finally:
                monkeypatch.setattr(co.os, "getpid", real_pid)
        return chosen

    monkeypatch.setattr(co, "_free_pending", walk)
    return fired


def _kept(tree, names):
    return sorted(names.get(json.loads(path.read_text(encoding="utf-8"))["summary"], "unknown")
                  for path in tree.session.glob("compact-summary.pending-*.json"))


# --------------------------------------------------------------------------
# The content match
# --------------------------------------------------------------------------


def _analysed(analysis, body=BODY):
    return f"<analysis>\n{analysis}\n</analysis>\n\n<summary>\n{body}\n</summary>"


_LONG_MENTION = "an earlier plan that was replaced before any work began. " * 5


_EXCERPT = ("An earlier compaction listed the files read, the counts sent to the team-lead "
            "and the tasks still pending, one item per line. ") * 3


def _quoting_summary():
    """A lead summary that quotes an analysis closing tag and then a summary block,
    so the longest analysis reading leaves the quoted excerpt as a second body."""
    body = BODY + ("\n5. Notes: an earlier analysis quoted a </analysis> tag and then "
                   f"<summary>{_EXCERPT}</summary> from a previous compaction.")
    return f"<analysis>\nWorking notes that are not kept.\n</analysis>\n\n<summary>\n{body}\n</summary>", body


_FIFO_CHILD = """
import json, sys
from shared import compaction_owner as co
elapsed = [0.0]
def sleep(seconds):
    elapsed[0] += seconds
print(json.dumps(co.settle(sys.argv[1], wait_s=co.READ_WAIT_S, monotonic=lambda: elapsed[0], sleep=sleep)))
"""


# --------------------------------------------------------------------------
# Captured frames
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# The journal event
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

_PERMITTED_KEYS = {"session_id", "transcript_path", "compact_summary"}
_OWNER_SOURCE = HOOKS / "shared" / "compaction_owner.py"


_CONSUMER_ATTRIBUTES = {
    "postcompact_archive.py": {"settle", "stage_summary"},
    "shared/session_init_resume.py": {"settle"},
    "bootstrap_gate.py": {"settle", "READ_WAIT_S"},
}


