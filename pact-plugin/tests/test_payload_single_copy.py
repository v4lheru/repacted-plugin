"""
Location: pact-plugin/tests/test_payload_single_copy.py
Summary: The teachback and HANDOFF write repair and the single-copy digest.
         D3: handoff_ordering_gate repairs the benign drifts (rev "2", key_decisions, a string
         variety_acknowledgment) and returns the write as updatedInput, with one schema_coerced
         journal row per repair; everything else is returned or flagged as before.
         D2: one digest (hooks/shared/payload_digest.py) printed by the stored line and by
         `repact inspect <id> --key`, so the notify names it instead of repeating the payload.
         CH-3 (the design notes): --key rejection and --key amend, the
         teammate's readers of a lead directive, agreeing with bin/repact-wait on what is open.
         Adversarial rows at each boundary: odd revs, unicode, huge strings, both spellings,
         reordered keys, a tampered stored object, hostile CLI arguments.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from shared.handoff_schema import normalise_handoff
from shared.payload_digest import payload_digest
from shared.handoff_schema import REV_MAX
from shared.stored_line import REV_MAX as STORE_REV_MAX
from shared.teachback_schema import normalise_teachback

CLI = Path(__file__).resolve().parent.parent / "bin" / "repact"
TEAM = "session-test0023"
HANDOFF = {"rev": 1, "amendments": {}, "produced": ["a.py"], "decisions": ["d"],
           "uncertainty": [], "integration": [], "open_questions": []}
TEACHBACK = {"understanding": "u", "most_likely_wrong": "m", "least_confident_item": "l",
             "first_action": "f", "variety_acknowledgment": {"rationale_articulates_this_dispatch": "yes"}}


# ─── D3: the normalisers ─────────────────────────────────────────────────────
class TestNormaliseHandoff:
    def test_the_rev_bound_is_the_task_stores(self):
        assert REV_MAX == STORE_REV_MAX == 2**53 - 1

    @pytest.mark.parametrize("rev, fixed", [("2", 2), ("02", 2), (str(REV_MAX), REV_MAX)])
    def test_a_digit_string_rev_becomes_an_int(self, rev, fixed):
        out, rows = normalise_handoff(dict(HANDOFF, rev=rev))
        assert out["rev"] == fixed and type(out["rev"]) is int
        assert rows == [{"key": "handoff", "field": "rev", "coercion": "digit_string_to_int"}]

    @pytest.mark.parametrize("rev", [
        "two", "-1", -1, "2.0", 2.0, True, False, "0", 0, " 2", "2 ", "+2", "",
        str(REV_MAX + 1), "9" * 1_000_000, "²", "٢", "２", None, [2], {"n": 2},
    ], ids=lambda rev: repr(rev)[:20])
    def test_any_other_rev_is_left_for_the_gate_to_return(self, rev):
        sent = dict(HANDOFF, rev=rev)
        out, rows = normalise_handoff(sent)
        assert out is sent and rows == []

    def test_a_legacy_alias_is_renamed_in_place(self):
        sent = {"rev": 1, "produced": [], "key_decisions": ["d"], "uncertainty": [],
                "integration_points": ["i"], "open_questions": []}
        out, rows = normalise_handoff(sent)
        assert list(out) == ["rev", "produced", "decisions", "uncertainty", "integration", "open_questions"]
        assert out["decisions"] == ["d"] and out["integration"] == ["i"]
        assert [row["coercion"] for row in rows] == ["renamed_to_decisions", "renamed_to_integration"]
        assert "key_decisions" in sent, "the input is never mutated"

    def test_both_spellings_present_the_canonical_key_wins_and_the_alias_is_dropped(self):
        """Design rule: decisions wins, key_decisions is dropped, never merged."""
        out, rows = normalise_handoff(dict(HANDOFF, key_decisions=["other"]))
        assert out["decisions"] == ["d"] and "key_decisions" not in out
        assert rows == [{"key": "handoff", "field": "key_decisions", "coercion": "dropped_decisions_present"}]

    def test_unicode_keys_and_values_pass_through_and_rows_never_carry_values(self):
        sent = dict(HANDOFF, rev="3", **{"übersicht": " \U0001f600"})
        out, rows = normalise_handoff(sent)
        assert out["übersicht"] == " \U0001f600" and out["rev"] == 3
        assert "3" not in json.dumps(rows)

    def test_normalising_twice_changes_nothing(self):
        once, _ = normalise_handoff(dict(HANDOFF, rev="4", key_decisions=["x"]))
        twice, rows = normalise_handoff(once)
        assert twice is once and rows == []

    @pytest.mark.parametrize("value", [None, "handoff", ["rev"], 7])
    def test_a_non_object_comes_back_unchanged(self, value):
        assert normalise_handoff(value) == (value, [])


class TestNormaliseTeachback:
    @pytest.mark.parametrize("ack, wrapped", [
        ("yes", {"rationale_articulates_this_dispatch": "yes"}),
        ("  YES ", {"rationale_articulates_this_dispatch": "yes"}),
        ("U3 looks low", {"rationale_articulates_this_dispatch": "concern", "concern": "U3 looks low"}),
        ("é x", {"rationale_articulates_this_dispatch": "concern", "concern": "é x"}),
    ])
    def test_a_string_acknowledgment_is_wrapped(self, ack, wrapped):
        out, rows = normalise_teachback(dict(TEACHBACK, variety_acknowledgment=ack))
        assert out["variety_acknowledgment"] == wrapped
        assert rows == [{"key": "teachback_submit", "field": "variety_acknowledgment",
                         "coercion": "string_wrapped_as_object"}]

    @pytest.mark.parametrize("ack", ["", "   ", "no", "Concern", None, 1, ["yes"],
                                     {"rationale_articulates_this_dispatch": "maybe"}])
    def test_anything_else_is_left_for_the_validator(self, ack):
        sent = dict(TEACHBACK, variety_acknowledgment=ack)
        out, rows = normalise_teachback(sent)
        assert out is sent and rows == []

    def test_a_huge_string_is_wrapped_whole(self):
        text = "z" * 2_000_000
        out, _ = normalise_teachback(dict(TEACHBACK, variety_acknowledgment=text))
        assert out["variety_acknowledgment"]["concern"] == text


# ─── D3: the gate returns the repaired write ─────────────────────────────────




# ─── D2: the digest ──────────────────────────────────────────────────────────
class TestDigest:
    def test_reordered_keys_give_the_same_digest(self):
        reordered = dict(reversed(list(HANDOFF.items())))
        assert list(reordered) != list(HANDOFF)
        assert payload_digest(reordered) == payload_digest(HANDOFF)
        assert len(payload_digest(HANDOFF)) == 12 and int(payload_digest(HANDOFF), 16) >= 0

    def test_a_null_key_and_an_absent_key_give_the_same_digest(self):
        assert payload_digest(dict(HANDOFF, extra=None)) == payload_digest(HANDOFF)

    @pytest.mark.parametrize("change", [{"rev": 2}, {"produced": ["b.py"]}, {"decisions": ["d "]},
                                        {"über": 1}])
    def test_any_change_to_the_stored_object_changes_the_digest(self, change):
        assert payload_digest({**HANDOFF, **change}) != payload_digest(HANDOFF)

    @pytest.mark.parametrize("value", [{"x": object()}, {"x": {1, 2}}])
    def test_a_value_that_is_not_json_has_no_digest(self, value):
        assert payload_digest(value) is None

    def test_a_value_nested_past_the_depth_limit_has_no_digest(self):
        deep: list = []
        for _ in range(100_000):
            deep = [deep]
        assert payload_digest({"x": deep}) is None


# ─── D2: repact inspect --key ──────────────────────────────────────────────────
@pytest.fixture
def home(tmp_path, monkeypatch):
    config = tmp_path / "config"
    (config / "tasks" / TEAM).mkdir(parents=True)
    context = config / "pact-sessions" / "proj" / "sid-payload" / "pact-session-context.json"
    context.parent.mkdir(parents=True)
    context.write_text(json.dumps({"team_name": TEAM}))     # this session's team, stated
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-payload")
    denylist = tmp_path / "deny.txt"
    denylist.write_text("Acme Secret Client\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("REPACT_SCRUB_DENYLIST", str(denylist))
    monkeypatch.setenv("REPACT_SCRUB_DENYLIST_WORDS", str(tmp_path / "none.txt"))
    return config


def write_task(config, task_id, metadata):
    (config / "tasks" / TEAM / f"{task_id}.json").write_text(json.dumps(
        {"id": task_id, "owner": "d23", "status": "in_progress", "metadata": metadata}))


def run(*args):
    """`repact inspect <args>`; the HANDOFF when no --key names another (the old reader's default)."""
    key = [] if "--key" in args or "--relayed-handoff" in args else ["--key", "handoff"]
    return subprocess.run(["bash", str(CLI), "inspect", *args, *key], capture_output=True, text=True,
                          env=dict(os.environ), timeout=30)


class TestRepactPayload:
    def test_prints_the_handoff_scrubbed_with_rev_and_the_stored_digest(self, home):
        handoff = dict(HANDOFF, decisions=["shipped for Acme Secret Client"])
        write_task(home, "88", {"handoff": handoff, "teachback_submit": TEACHBACK})
        result = run("88", "--team", TEAM)
        assert result.returncode == 0, result.stderr
        head, body = result.stdout.split("\n", 1)
        assert head == (f"handoff on #88 (team {TEAM}, owner d23, status in_progress): rev 1, "
                        f"digest {payload_digest(handoff)}")
        assert "Acme" not in body and "[REDACTED:denylist]" in body
        assert json.loads(body)["rev"] == 1

    def test_the_teachback_when_asked(self, home):
        write_task(home, "87", {"teachback_submit": TEACHBACK})
        result = run("87", "--key", "teachback")   # the team is this session's
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith(f"teachback_submit on #87 (team {TEAM}, owner d23, "
                                        f"status in_progress): digest {payload_digest(TEACHBACK)}")

    def test_without_a_session_the_team_is_never_guessed(self, home, monkeypatch):
        write_task(home, "87", {"teachback_submit": TEACHBACK})
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")   # the one team holding #87 is no answer
        result = run("87")
        assert result.returncode == 4 and "task 87 not found" in result.stderr

    def test_a_matching_digest_is_confirmed(self, home):
        write_task(home, "88", {"handoff": HANDOFF})
        result = run("88", "--digest", payload_digest(HANDOFF).upper())
        assert result.returncode == 0 and "digest matches the notify" in result.stdout

    def test_a_tampered_stored_object_is_reported_not_accepted(self, home):
        notified = payload_digest(HANDOFF)
        write_task(home, "88", {"handoff": dict(HANDOFF, produced=["edited-after-notify.py"])})
        result = run("88", "--digest", notified)
        assert result.returncode == 3
        assert "DIGEST MISMATCH" in result.stderr and notified in result.stderr

    @pytest.mark.parametrize("args", [
        ("../../etc/passwd",), ("88", "--team", "../x"), ("88", "--team", ".hidden"),
        ("8/8",), ("88", "--digest", "zz"), ("88", "--digest", "a" * 13), ("٣",),
    ], ids=["task-traversal", "team-traversal", "team-dot", "task-slash", "digest-hex",
            "digest-length", "task-unicode-digit"])
    def test_hostile_arguments_are_a_usage_error(self, home, args):
        result = run(*args)
        assert result.returncode == 2, result.stdout

    def test_a_missing_task_or_payload_exits_4(self, home):
        assert run("99", "--team", TEAM).returncode == 4
        write_task(home, "5", {"note": "x"})
        result = run("5", "--team", TEAM, "--key", "handoff")
        assert result.returncode == 4 and "no handoff stored on #5" in result.stderr


def write_journal(config, *events, session="sid-payload"):
    journal = config / "pact-sessions" / "proj" / session / "session-journal.jsonl"
    journal.write_text("not json\n" + "".join(json.dumps(event) + "\n" for event in events))


class TestRepactPayloadTaskFileGone:
    """T-6dfc17b0: the store drains completed tasks mid-session; the journal still holds them."""

    def events(self, subject="coder: build"):
        return [
            {"type": "agent_handoff", "task_id": "88", "agent": "d23", "task_subject": "old arc",
             "handoff": dict(HANDOFF, produced=["earlier-arc.py"])},
            {"type": "task_metadata_snapshot", "task_id": "88", "subject": "old arc",
             "metadata": {"teachback_submit": {"understanding": "the earlier arc"}}},
            {"type": "task_metadata_snapshot", "task_id": "88", "subject": subject, "owner": "d23",
             "metadata": {"teachback_submit": TEACHBACK, "amend_1": "a ruling"}},
            {"type": "agent_handoff", "task_id": "88", "agent": "d23", "task_subject": subject,
             "handoff": HANDOFF},
            {"type": "agent_handoff", "task_id": "89", "agent": "d24", "task_subject": "other",
             "handoff": dict(HANDOFF, produced=["other-task.py"])},
        ]

    def test_the_handoff_and_teachback_are_read_whole_from_the_journal(self, home):
        (home / "teams" / TEAM).mkdir(parents=True)
        (home / "teams" / TEAM / "config.json").write_text(json.dumps({"leadSessionId": "sid-payload"}))
        write_journal(home, *self.events())
        result = run("88", "--digest", payload_digest(HANDOFF))
        assert result.returncode == 0, result.stderr
        head, match, body = result.stdout.split("\n", 2)
        assert head == (f"handoff on #88 (team {TEAM}, owner d23, task file gone, source: session "
                        f"journal, latest of 2 tasks that used this id: coder: build): rev 1, "
                        f"digest {payload_digest(HANDOFF)}")
        assert match.startswith("digest matches") and json.loads(body) == HANDOFF
        teachback = run("88", "--key", "teachback")
        assert teachback.returncode == 0, teachback.stderr
        assert f"digest {payload_digest(TEACHBACK)}" in teachback.stdout.split("\n", 1)[0]

    def test_the_task_file_wins_while_it_exists(self, home):
        write_journal(home, *self.events())
        write_task(home, "88", {"handoff": dict(HANDOFF, rev=2)})
        head = run("88").stdout.split("\n", 1)[0]
        assert "status in_progress): rev 2" in head and "journal" not in head

    def test_a_team_never_written_to_disk_reads_this_sessions_journal(self, home):
        write_journal(home, *self.events())        # no teams/<team>/config.json at all
        assert run("88").returncode == 0

    def test_after_a_resume_the_journal_is_found_by_the_session_that_names_the_team(self, home):
        """Measured on session-76c7bf88: the team config names the session the team was born in,
        which has no journal; the resumed session's own context names the team and holds it."""
        (home / "teams" / TEAM).mkdir(parents=True)
        (home / "teams" / TEAM / "config.json").write_text(json.dumps({"leadSessionId": "sid-before-resume"}))
        write_journal(home, *self.events())
        other = home / "pact-sessions" / "proj" / "sid-other-team"
        other.mkdir(parents=True)
        (other / "pact-session-context.json").write_text(json.dumps({"team_name": "session-other"}))
        (other / "session-journal.jsonl").write_text(json.dumps(
            {"type": "agent_handoff", "task_id": "88", "agent": "x", "task_subject": "coder: build",
             "handoff": dict(HANDOFF, produced=["another-teams-88.py"])}) + "\n")
        result = run("88", "--team", TEAM, "--digest", payload_digest(HANDOFF))
        assert result.returncode == 0, result.stderr + result.stdout

    def test_a_value_the_journal_cut_is_reported_as_cut(self, home):
        marker = {"_truncated": True, "original_bytes": 40000, "head": "{"}
        write_journal(home, {"type": "task_metadata_snapshot", "task_id": "90", "subject": "s",
                             "metadata": {"teachback_submit": marker}})
        result = run("90", "--key", "teachback")
        assert result.returncode == 0 and "only the head of 40000 bytes was kept" in result.stdout

    @pytest.mark.parametrize("case", ["no-session", "other-session", "stale-team", "forged-lead",
                                      "no-journal", "wrong-task", "not-a-dict"])
    def test_nothing_is_guessed_when_the_journal_cannot_answer(self, home, monkeypatch, case):
        write_journal(home, *self.events())
        args = ["88"]
        if case == "no-session":
            monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
        elif case == "other-session":
            monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "sid-someone-else")
        elif case == "stale-team":
            args += ["--team", "session-gone"]
            monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
        elif case == "forged-lead":
            (home / "teams" / TEAM).mkdir(parents=True)
            (home / "teams" / TEAM / "config.json").write_text(json.dumps({"leadSessionId": "*"}))
            monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "../sid-payload")
        elif case == "no-journal":
            (home / "pact-sessions" / "proj" / "sid-payload" / "session-journal.jsonl").unlink()
        elif case == "wrong-task":
            args = ["8"]
        else:
            write_journal(home, {"type": "agent_handoff", "task_id": "88", "handoff": "x"},
                          {"type": "task_metadata_snapshot", "task_id": "88", "metadata": [1]}, [88])
        result = run(*args)
        assert result.returncode == 4 and "not found" in result.stderr, result.stdout


# ─── CH-3: --key rejection and --key amend ───────────────────────────────────
WAITER = CLI.parent / "repact-wait"
REJECTION = {"reason": "the rev is missing", "corrections": ["add rev"], "revision_number": 1,
             "since": "2026-10-11T10:00:00+00:00"}


def waiter_holds(metadata, name):
    """What bin/repact-wait itself returns on: the reader must print exactly when it holds."""
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader
    loader = SourceFileLoader("_repact_wait_for_payload", str(WAITER))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return bool(module.holding({"status": "in_progress", "metadata": metadata}, (name,)))


def blocks(stdout):
    """{header key: (header line, parsed body)} of a --key rejection/amend print."""
    found, lines = {}, stdout.splitlines()
    starts = [i for i, line in enumerate(lines) if " on #" in line and line.endswith(
        tuple("0123456789abcdef")) and "digest " in line and not line.startswith((" ", '"'))]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(lines)
        body = "\n".join(line for line in lines[start + 1:end] if not line.startswith("digest matches"))
        found[lines[start].split(" on #")[0]] = (lines[start], json.loads(body))
    return found


class TestRepactPayloadRejection:
    def test_an_open_handoff_rejection_is_printed_with_its_revision_rev_and_digest(self, home):
        rejection = dict(REJECTION, rev=1, reason="wrong for Acme Secret Client")
        write_task(home, "88", {"handoff": HANDOFF, "handoff_rejection": rejection})
        result = run("88", "--key", "rejection", "--digest", payload_digest(rejection))
        assert result.returncode == 0, result.stderr
        head, body = result.stdout.split("\n", 1)
        assert head == (f"handoff_rejection on #88 (team {TEAM}, owner d23, status in_progress): "
                        f"revision_number 1, rev 1, digest {payload_digest(rejection)}")
        assert "Acme" not in body and "[REDACTED:denylist]" in body
        assert "digest matches the wake" in body

    def test_an_open_teachback_rejection_and_both_at_once(self, home):
        write_task(home, "87", {"teachback_rejection": REJECTION})
        result = run("87", "--key", "rejection")
        assert result.returncode == 0 and list(blocks(result.stdout)) == ["teachback_rejection"]
        both = {"handoff": HANDOFF, "handoff_rejection": dict(REJECTION, rev=1),
                "teachback_rejection": REJECTION}
        write_task(home, "87", both)
        result = run("87", "--key", "rejection", "--digest", payload_digest(REJECTION))
        assert result.returncode == 0, result.stderr      # the digest of any object printed
        found = blocks(result.stdout)
        assert list(found) == ["handoff_rejection", "teachback_rejection"]
        assert found["teachback_rejection"][1] == REJECTION
        assert run("87", "--key", "rejection", "--digest", "0" * 12).returncode == 3

    @pytest.mark.parametrize("metadata", [
        {"teachback_rejection": REJECTION, "revision_number": 2},                 # answered
        {"handoff_rejection": dict(REJECTION, rev=1), "handoff": dict(HANDOFF, rev=2)},
        {"handoff_rejection": None}, {"handoff_rejection": ""}, {"handoff_rejection": 0},
        {"handoff_rejection": {}}, {"handoff_rejection": []}, {"rejection": REJECTION},
        {"handoff_rejections": REJECTION}, {},
    ], ids=["answered-teachback", "answered-handoff", "none", "empty-string", "zero", "empty-dict",
            "empty-list", "bare-name", "plural", "no-metadata"])
    def test_nothing_open_exits_4_exactly_when_the_waiter_does_not_hold(self, home, metadata):
        write_task(home, "5", metadata)
        result = run("5", "--key", "rejection")
        assert not waiter_holds(metadata, "rejection")
        assert result.returncode == 4 and "no open rejection on #5" in result.stderr
        assert result.stdout == ""

    @pytest.mark.parametrize("value", [
        "a plain string", 7, 1.5, True, ["a", 1], {"revision_number": True}, {"rev": "x"},
        {"reason": "ünïcödé \u2028 \u202e 日本語 \U0001f600", "revision_number": 1},
        {"reason": "lone \ud800 surrogate", "revision_number": 1},
        {"reason": "\x00\x1b[2J\r\n", "corrections": None, "revision_number": 2**70},
    ], ids=["string", "int", "float", "bool", "list", "bool-revision", "string-rev", "unicode",
            "surrogate", "control-chars-huge-int"])
    def test_a_rejection_of_any_type_prints_and_agrees_with_the_waiter(self, home, value):
        metadata = {"handoff_rejection": value}
        write_task(home, "5", metadata)
        result = run("5", "--key", "rejection")
        assert waiter_holds(metadata, "rejection")
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[0].startswith("handoff_rejection on #5 (")
        assert "\x1b" not in result.stdout and "Traceback" not in result.stderr

    def test_a_huge_value_is_cut_and_a_legal_one_is_not(self, home):
        legal = dict(REJECTION, reason="r" * 2000, corrections=["c" * 4000] * 20)
        write_task(home, "5", {"teachback_rejection": legal})
        result = run("5", "--key", "rejection")
        assert result.returncode == 0 and "[cut:" not in result.stdout
        assert blocks(result.stdout)["teachback_rejection"][1] == legal
        write_task(home, "5", {"teachback_rejection": "x" * 3_000_000})
        result = run("5", "--key", "rejection")
        assert result.returncode == 0 and len(result.stdout) < 101_000
        assert "[cut: 3000002 characters stored" in result.stdout

    def test_a_deeply_nested_value_does_not_raise(self, home):
        (home / "tasks" / TEAM / "5.json").write_text(
            '{"id": "5", "metadata": {"handoff_rejection": ' + "[" * 600 + "1" + "]" * 600 + "}}")
        result = run("5", "--key", "rejection")
        assert result.returncode in (0, 4) and "Traceback" not in result.stderr

    def test_a_secret_shaped_value_is_scrubbed(self, home):
        secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" * 2
        write_task(home, "5", {"teachback_rejection": dict(REJECTION, corrections=[f"token {secret}"])})
        result = run("5", "--key", "rejection")
        assert result.returncode == 0 and secret not in result.stdout

    def test_a_hostile_key_name_is_printed_on_one_bounded_line(self, home):
        key = "evil\n\x1b[31m" + "k" * 500 + "_rejection"
        write_task(home, "5", {key: REJECTION})
        result = run("5", "--key", "rejection")
        assert result.returncode == 0
        head = result.stdout.splitlines()[0]
        assert head.startswith("evil") and "\x1b" not in head and len(head) < 300

    @pytest.mark.parametrize("content", [None, "", "not json", "[1, 2]", '"text"', "null",
                                         '{"metadata": "x"}', '{"metadata": ["handoff_rejection"]}'],
                             ids=["missing", "empty", "corrupt", "list", "string", "null",
                                  "metadata-string", "metadata-list"])
    @pytest.mark.parametrize("key", ["rejection", "amend"])
    def test_a_missing_or_corrupt_task_file_exits_4_with_no_journal_fallback(self, home, content, key):
        if content is not None:
            (home / "tasks" / TEAM / "88.json").write_text(content)
        write_journal(home, {"type": "agent_handoff", "task_id": "88", "agent": "d23",
                             "task_subject": "s", "handoff": HANDOFF},
                      {"type": "task_metadata_snapshot", "task_id": "88", "subject": "s",
                       "metadata": {"handoff_rejection": REJECTION, "amend_1": "from the journal"}})
        result = run("88", "--key", key)
        assert result.returncode == 4 and result.stdout == "", result.stdout
        assert "Traceback" not in result.stderr

    @pytest.mark.parametrize("task", ["../88", "8/8", "8\\8", ".88", "..", "88\n", "", "a" * 200,
                                      "tasks/x/88", "/etc/passwd", "88\x00"])
    @pytest.mark.parametrize("key", ["rejection", "amend"])
    def test_a_task_id_with_a_separator_or_a_leading_dot_is_a_usage_error(self, home, task, key):
        if "\x00" in task:
            pytest.skip("a NUL cannot be passed in argv")
        result = run(task, "--key", key)
        assert result.returncode == 2 and result.stdout == ""


class TestRepactPayloadAmend:
    def test_undisposed_amendments_print_in_numeric_order_with_their_digests(self, home):
        ruling = {"text": "use the Acme Secret Client path", "at": "t", "from": "team-lead",
                  "kind": "ruling"}
        metadata = {"amend_10": {"text": "ten", "kind": "note"}, "amend_2": "a plain string",
                    "amend_1": ruling, "amend_3": {"text": "disposed"},
                    "handoff": dict(HANDOFF, amendments={"amend_3": "ack"})}
        write_task(home, "88", metadata)
        result = run("88", "--key", "amend", "--digest", payload_digest("a plain string"))
        assert result.returncode == 0, result.stderr
        found = blocks(result.stdout)
        assert list(found) == ["amend_1", "amend_2", "amend_10"]
        assert found["amend_10"][0] == (f"amend_10 on #88 (team {TEAM}, owner d23, status "
                                        f"in_progress): digest {payload_digest(metadata['amend_10'])}")
        assert found["amend_2"][1] == "a plain string"
        assert "Acme" not in result.stdout and "disposed" not in result.stdout
        assert run("88", "--key", "amend", "--digest", "f" * 12).returncode == 3

    @pytest.mark.parametrize("metadata", [
        {}, {"amend_x": "t"}, {"amend_": "t"}, {"amend_1a": "t"}, {"amend_-1": "t"},
        {"amend_1.5": "t"}, {"amend_1\n": "t"}, {" amend_1": "t"}, {"AMEND_1": "t"},
        {"amend_1": "t", "handoff": {"amendments": {"amend_1": "ack"}}},
    ], ids=["none", "letters", "empty-suffix", "trailing-letter", "negative", "decimal",
            "line-break", "leading-space", "upper", "disposed"])
    def test_a_non_numeric_suffix_is_not_an_amendment_as_for_the_waiter(self, home, metadata):
        write_task(home, "5", metadata)
        result = run("5", "--key", "amend")
        assert not waiter_holds(metadata, "amend")
        assert result.returncode == 4 and "no undisposed amendment on #5" in result.stderr
        assert result.stdout == ""

    @pytest.mark.parametrize("metadata", [
        {"amend_1": None}, {"amend_1": {}}, {"amend_1": 0}, {"amend_007": "t"},
        {"amend_" + "9" * 5000: "t", "amend_2": "u"}, {"amend_٣": "t"},
        {"amend_1": "t", "handoff": {"amendments": ["amend_1"]}},
        {"amend_1": "t", "handoff": "amend_1"}, {"amend_1": "ünïcödé \ud800 \u2028"},
        {"amend_1": {"text": "x" * 500_000}},
    ], ids=["null", "empty", "zero", "leading-zeros", "5000-digits", "unicode-digit",
            "amendments-list", "handoff-string", "unicode", "huge"])
    def test_whatever_the_waiter_holds_on_is_printed_and_never_raises(self, home, metadata):
        write_task(home, "5", metadata)
        result = run("5", "--key", "amend")
        assert waiter_holds(metadata, "amend")
        assert result.returncode == 0, result.stderr
        assert "Traceback" not in result.stderr and len(result.stdout) < 250_000

    def test_the_default_key_and_the_lead_keys_are_unchanged_by_a_directive(self, home):
        write_task(home, "88", {"handoff": HANDOFF, "handoff_rejection": dict(REJECTION, rev=1),
                                "amend_1": "ruling"})
        result = run("88")
        assert result.returncode == 0 and result.stdout.startswith("handoff on #88 (")
