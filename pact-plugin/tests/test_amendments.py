"""
Location: pact-plugin/tests/test_amendments.py
Summary: Tests for hooks/shared/amendments.py, the amend_<n> convention and the rules the
         HANDOFF gate applies (dispositions, rev pin, rejection rev).
"""

from __future__ import annotations

import pytest

from shared import amendments as rules


def amendment(text="Do the thing.", kind="ruling"):
    return {"text": text, "at": "2026-09-26T16:19:50Z", "from": "team-lead", "kind": kind}


class TestKeys:
    @pytest.mark.parametrize("key,number", [
        ("amend_1", 1),])
    def test_amendment_number(self, key, number):
        assert rules.amendment_number(key) == number

    def test_amendments_are_numeric_order_and_skip_malformed(self):
        metadata = {"amend_10": amendment("ten"), "amend_2": amendment("two"),
                    "amend_3": "   ", "amend_4": {"kind": "note"}, "amend_5": 7, "amend_6": ["text"],
                    "amend_7": None, "amend_8": {"text": 3}, "amend_9": "",
                    "lead_amendments": ["old key"], "handoff": {}}
        assert list(rules.amendments(metadata)) == ["amend_2", "amend_10"]

    def test_a_plain_string_is_an_amendment_with_that_text_and_no_author(self):
        """Field: the lead wrote amend_1 by hand as a string, and the HANDOFF gate saw none."""
        found = rules.amendments({"amend_2": amendment("two"), "amend_1": "Diff the types against the contract."})
        assert list(found) == ["amend_1", "amend_2"]
        assert found["amend_1"] == {"text": "Diff the types against the contract."}
        assert rules.missing_dispositions(found, {"amendments": {"amend_2": "ack"}}) == [
            ("amend_1", "Diff the types against the contract.")]
        line = rules.relay_line("amend_1", "34", found["amend_1"])
        assert line.startswith("AMENDMENT amend_1 (from: unknown) on #34 (note): Diff the types")


class TestDispositions:
    @pytest.mark.parametrize("value,ok", [
        ("ack", True),])
    def test_is_disposition(self, value, ok):
        assert rules.is_disposition(value) is ok

    def test_missing_lists_undisposed_keys_with_a_100_char_preview(self):
        long_text = "x" * 150
        task_amendments = {"amend_1": amendment("short"), "amend_2": amendment(long_text),
                           "amend_3": amendment("third")}
        handoff = {"amendments": {"amend_1": "ack", "amend_3": "applied"}}
        missing = rules.missing_dispositions(task_amendments, handoff)
        assert missing == [("amend_2", "x" * 100), ("amend_3", "third")]


class TestRevisions:
    @pytest.mark.parametrize("previous,new,ok", [
        (None, {"rev": 1}, True),])
    def test_rev_problem(self, previous, new, ok):
        assert (rules.rev_problem(previous, new) is None) is ok

    def test_rev_problem_names_the_expected_rev(self):
        assert rules.rev_problem({"rev": 2}, {"rev": 2}) == "handoff.rev must be 3 (given: 2)"


class TestRelayLine:
    def test_lead_amendment_line(self):
        line = rules.relay_line("amend_3", "34", amendment("Use the placeholder class."))
        assert line == "LEAD AMENDMENT amend_3 on #34 (ruling): Use the placeholder class."

    def test_another_author_is_named(self):
        value = dict(amendment("x"), **{"from": "architect-pact", "kind": "note"})
        line = rules.relay_line("amend_1", "7", value)
        assert line == "AMENDMENT amend_1 (from: architect-pact) on #7 (note): x"

    # Second review, finding 1. The value is whatever a task file holds, and any peer can
    # write one. Before: a missing `from` printed the LEAD label, a 1 MB text arrived as
    # 1,000,270 characters, and a text or `from` with a line break put its own
    # "LEAD AMENDMENT amend_9" line into the addressee's context.


GOOD_REJECTION = {"reason": "The gate test is missing.", "corrections": ["Add the gate test."],
                  "since": "2026-10-11T10:40:58+00:00", "revision_number": 1, "rev": 2}
NAN = float("nan")


def rejection(**changes):
    value = dict(GOOD_REJECTION, **changes)
    return {key: item for key, item in value.items() if item is not ...}


def _waiter():
    """bin/repact-wait as a module: the tool the shape gate must never be narrower than."""
    from importlib.machinery import SourceFileLoader
    from importlib.util import module_from_spec, spec_from_loader
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "bin" / "repact-wait"
    loader = SourceFileLoader("_repact_wait_under_test", str(path))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class TestRejectionShape:
    """CH-2 (the design notes): the shape a rejection must have."""

    @pytest.mark.parametrize("key", list(rules.REJECTION_KEYS)[:1])
    def test_the_documented_object_and_a_clearing_none_pass(self, key):
        assert rules.rejection_shape_problem(key, GOOD_REJECTION, {"rev": 2}) is None
        assert rules.rejection_shape_problem(key, dict(GOOD_REJECTION, later_field={"a": 1}), {"rev": 2}) is None
        assert rules.rejection_shape_problem(key, None, {"rev": 2}) is None
        assert rules.rejection_shape_problem(key, rejection(since="2026-10-11T10:40:58Z"), None) is None

    @pytest.mark.parametrize("value", ["rejected",])
    def test_a_value_that_is_not_the_object_is_a_problem(self, value):
        problem = rules.rejection_shape_problem("handoff_rejection", value, None)
        assert problem and "handoff_rejection" in problem and len(problem) < 300


class TestClosedOnArrival:
    """Review F6: the write-time check uses the waiter's predicate against the task after the write."""

    @pytest.mark.parametrize("given,task_revision,closed", [
        (1, 2, True),])
    def test_the_revision_number_boundary(self, given, task_revision, closed):
        metadata = {} if task_revision is None else {"revision_number": task_revision}
        value = rejection(revision_number=given)
        for key in rules.REJECTION_KEYS:
            after = rules.after_write(metadata, {key: value})
            problem = rules.closed_on_arrival(key, value, after)
            assert bool(problem) is closed is (not rules.open_rejection(key, value, after))
            if problem:
                assert f'Write "revision_number": {task_revision}' in problem and len(problem.splitlines()) == 1

    def test_a_handoff_rejection_is_judged_by_rev_when_both_carry_one(self):
        after = {"handoff": {"rev": 3}, "revision_number": 1}
        assert '"rev": 3' in rules.closed_on_arrival("handoff_rejection", rejection(rev=2, revision_number=9), after)
        assert rules.closed_on_arrival("handoff_rejection", rejection(rev=3), after) is None
        assert rules.closed_on_arrival("handoff_rejection", rejection(rev=4), after) is None
        assert rules.closed_on_arrival("teachback_rejection", rejection(rev=2), after) is None   # rev is not its number


class TestOpenRejection:
    def test_it_is_the_waiters_predicate(self):
        """Moved from bin/repact-wait so the waiter and the reader cannot disagree: the same
        answers on the same inputs, for as long as the waiter keeps its own copy."""
        waiter = _waiter()
        own = getattr(waiter, "_open_rejection", None) or waiter.open_rejection
        cases = [
            ("handoff_rejection", {"rev": 1}, {"handoff": {"rev": 1}}),
            ("handoff_rejection", {"rev": 1}, {"handoff": {"rev": 2}}),
            ("handoff_rejection", {"rev": True}, {"handoff": {"rev": 2}}),
            ("handoff_rejection", {"revision_number": 1}, {"revision_number": 2, "handoff": {"rev": 2}}),
            ("teachback_rejection", {"revision_number": 2}, {"revision_number": 2}),
            ("teachback_rejection", {"revision_number": 1}, {"revision_number": 2}),
            ("teachback_rejection", {"revision_number": "1"}, {"revision_number": 2}),
            ("teachback_rejection", {}, {}), ("teachback_rejection", "text", {}), ("teachback_rejection", 0, {}),
            ("teachback_rejection", {"revision_number": NAN}, {"handoff": "not an object"}),
        ]
        for key, record, metadata in cases:
            assert rules.open_rejection(key, record, metadata) == own(key, record, metadata), (key, record)
        assert rules.open_rejection("handoff_rejection", {"rev": 1}, {"handoff": {"rev": 1}}) is True
        assert rules.open_rejection("handoff_rejection", {"rev": 1}, {"handoff": {"rev": 2}}) is False


class TestUnreadRejections:
    def test_a_rejection_is_handed_over_once_per_revision_number(self):
        metadata = {"teachback_rejection": rejection(reason="Name the mode key.", rev=...)}
        lines, seen = rules.unread_lines(metadata, "18", set())
        assert lines == ["REJECTION teachback_rejection on #18: Name the mode key.; "
                         "run repact inspect 18 --key rejection"]
        assert seen == {"teachback_rejection@1r0"}
        assert rules.unread_lines(metadata, "18", seen) == ([], seen)
        again = {"revision_number": 2, "teachback_rejection": rejection(reason="Still missing.", revision_number=2)}
        lines, seen = rules.unread_lines(again, "18", seen)
        assert len(lines) == 1 and "Still missing." in lines[0]
        assert seen == {"teachback_rejection@1r0", "teachback_rejection@2r2"}

    def test_both_keys_amendments_and_grants_come_in_one_call(self):
        metadata = {"amend_1": amendment(), "docker_grant": "yes", "handoff": {"rev": 1},
                    "handoff_rejection": GOOD_REJECTION | {"rev": 1}, "teachback_rejection": GOOD_REJECTION}
        lines, seen = rules.unread_lines(metadata, "19", set())
        assert [line.split()[0] for line in lines] == ["LEAD", "GRANT", "REJECTION", "REJECTION"]
        assert {"amend_1", "docker_grant", "handoff_rejection@1r1", "teachback_rejection@1r2"} == seen


class TestCommitKindAndWakeLine:
    def test_commit_and_halt_are_kinds(self):
        assert {"commit", "halt"} <= set(rules.KINDS)
        assert rules.make_amendment("stop", "halt", "T") == {"text": "stop", "at": "T", "from": "team-lead",
                                                              "kind": "halt"}

    def test_a_commit_amendment_carries_its_sha(self):
        value = rules.make_amendment("committed Add the gate.", "commit", "T", sha="ABCDEF1")
        assert value == {"text": "committed Add the gate.", "at": "T", "from": "team-lead", "kind": "commit",
                         "sha": "abcdef1"}
        assert rules.relay_line("amend_2", "19", value) == \
            "LEAD AMENDMENT amend_2 on #19 (commit): committed Add the gate. [sha abcdef1]"


