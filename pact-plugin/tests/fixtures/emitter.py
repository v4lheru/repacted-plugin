"""
Location: pact-plugin/tests/fixtures/emitter.py
Summary: Shared HANDOFF and journal-path constants.
Used by: test_handoff_schema.py (VALID_HANDOFF) and
         test_dogfood_livelock_invariant.py (WRITABLE_TEST_JOURNAL).
"""

VALID_HANDOFF = {
    "produced": ["src/auth.ts"],
    "decisions": ["Used JWT"],
    "uncertainty": [],
    "integration": ["UserService"],
    "open_questions": [],
}


# A non-empty journal path sentinel; never written.
WRITABLE_TEST_JOURNAL = "/pact-test-session/session-journal.jsonl"
