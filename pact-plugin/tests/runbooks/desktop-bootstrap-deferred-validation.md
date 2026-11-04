# Desktop bootstrap: deferred manual validation

Moved from `test_harness_aware_bootstrap.py` (`test_deferred_desktop_validation_protocol`, an always-skipped
documentation cell). Run it by hand in a real Claude Desktop session; no CI or CLI test can.

```text
POST-MERGE DEFERRED VALIDATION (documentation cell — asserts nothing
runtime; it carries the protocol the user runs in a REAL Desktop session,
which this CLI/CI substrate cannot exercise). SKIPPED, not passed, so this
documentation carrier does not count toward the green total (review MINOR-2).

Fixtures here CAN prove: branch-2 resolves the config-less <uuid>; it does
NOT misfire under CLI cold-start (FIXTURE C) or tmux (TS4); identity-match
still wins for the with-config outlier; the inbox-witness passes GATE 2
config-less while the members[] path stays byte-identical; the marker writes
end-to-end and only from a lead frame (TS12); traversal ids are rejected.

Fixtures CANNOT prove (platform-runtime-only, hence this deferred protocol):
  (1) that the REAL platform never pre-creates teams/<sid>/inboxes/<name>.json
      on a bare reference to a NEVER-spawned name (the inbox-witness
      false-positive vector) — closed by the NEGATIVE LIVE-PROBE below;
  (2) that the real Desktop substrate shape matches the simulated
      teams/<uuid>/{inboxes/secretary.json,file-edits.json} + tasks/<uuid>/
      layout — closed by manual STEP 1.

── NEGATIVE LIVE-PROBE (closes the inbox-precreation vector) ──
In a throwaway session, reference / SendMessage a SYNTHETIC name that is
NEVER Agent-spawned (use 'ghost-sec', NOT 'secretary'), then:
    sid=<live session uuid>
    ls ~/.claude/teams/$sid/inboxes/ | sort > /tmp/inbox_before.txt
    #   ... SendMessage to to="ghost-sec" in the session, then return ...
    ls ~/.claude/teams/$sid/inboxes/ | sort > /tmp/inbox_after.txt
    diff /tmp/inbox_before.txt /tmp/inbox_after.txt           && echo 'NO ghost-sec.json — witness SOUND'           || echo 'ghost-sec.json APPEARED — harden GATE 2 to require '                  'tasks/<team>/1.json status!=pending corroboration'
Absent -> the inbox-witness is sound (inbox-create is spawn/delivery-gated).
Present -> harden GATE 2. The platform may also REJECT an unknown recipient
outright (no inbox, error) — that ALSO confirms soundness. Non-blocking.

── 7-STEP MANUAL DESKTOP PROTOCOL (Bash-observable; non-gated under deadlock) ──
Pre: install the merged plugin; launch PACT in Claude Desktop; run bootstrap.
STEP 0  capture the live UUID + persisted team_name from
        ~/.claude/pact-sessions/*/*/pact-session-context.json.
STEP 1  confirm config-less substrate shape: ls -la ~/.claude/teams/<uuid>/
        (expect inboxes/ + file-edits.json, NO config.json);
        ls ~/.claude/teams/<uuid>/inboxes/ (expect secretary.json);
        ls ~/.claude/tasks/<uuid>/ (expect 1.json).
STEP 2  GATE 1 cleared: the secretary spawns WITHOUT the
        "no Task assigned to owner='secretary'" deny; cat
        ~/.claude/tasks/<uuid>/1.json -> status advanced past 'pending'.
STEP 3  GATE 2 cleared: ls -la ~/.claude/pact-sessions/*/<uuid>/ shows the
        bootstrap-complete marker file.
STEP 4  Edit/Write/Agent UNBLOCKED: a trivial Edit/Write SUCCEEDS in-session
        (no 'bootstrap required' deny). End-to-end deadlock-clear signal.
STEP 5  harvest NON-EMPTY: read_events_from(<session_dir>, 'agent_handoff')
        returns events (the silent-handoff-loss symptom is cleared).
STEP 6  run the NEGATIVE LIVE-PROBE above once.
CRUX gates: STEP 2 + STEP 3 + STEP 4. Report each PASS/FAIL.
```
