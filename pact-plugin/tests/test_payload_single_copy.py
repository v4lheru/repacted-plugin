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

    @pytest.mark.parametrize("rev, fixed", [("2", 2),])
    def test_a_digit_string_rev_becomes_an_int(self, rev, fixed):
        out, rows = normalise_handoff(dict(HANDOFF, rev=rev))
        assert out["rev"] == fixed and type(out["rev"]) is int
        assert rows == [{"key": "handoff", "field": "rev", "coercion": "digit_string_to_int"}]


class TestNormaliseTeachback:
    @pytest.mark.parametrize("ack, wrapped", [
        ("yes", {"rationale_articulates_this_dispatch": "yes"}),])
    def test_a_string_acknowledgment_is_wrapped(self, ack, wrapped):
        out, rows = normalise_teachback(dict(TEACHBACK, variety_acknowledgment=ack))
        assert out["variety_acknowledgment"] == wrapped
        assert rows == [{"key": "teachback_submit", "field": "variety_acknowledgment",
                         "coercion": "string_wrapped_as_object"}]

    @pytest.mark.parametrize("ack", ["",])
    def test_anything_else_is_left_for_the_validator(self, ack):
        sent = dict(TEACHBACK, variety_acknowledgment=ack)
        out, rows = normalise_teachback(sent)
        assert out is sent and rows == []


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


