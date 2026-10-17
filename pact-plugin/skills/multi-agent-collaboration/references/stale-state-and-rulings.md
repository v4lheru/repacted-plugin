# Stale state, crossed messages and rulings

Context: in an async team every message and every earlier read is a snapshot, and rulings travel
through channels that lose them. Read this when an instruction contradicts what you just did, when a
ruling arrives by message, before acting on anything you escalated, and before writing into state
another agent also writes.

## Canonical rules
1. Earlier reads of shared state are timestamped evidence: re-measure the target immediately before writing or reporting, and reconcile with the current state instead of re-applying.
2. When a directive contradicts work you already reported, it probably crossed yours in flight: check git and task state, reply with the evidence, and do not rebuild.
3. In a shared tree, a contradicting directive can also mean a peer overwrote your file; the same read distinguishes the two, and they need opposite responses.
4. Never act on any agent's description of file state, the lead's and your own earlier one included; read the file. Expect recipients to verify yours, and say that is fine.
5. When two live instructions point opposite ways, more messages cannot settle it: the tree can. Modified paths plus a grep for the new symbols show who holds the pen.
6. Ask "is this already satisfied?" before "how do I do it?"; then mine the stale instruction for what the file still lacks, and report the reconciliation instead of silently no-oping.
7. An instruction is a snapshot too: a user decision that post-dates it wins. When its premise moved, check who could have decided in the interval; caution is its own claim.
8. Sequencing lowers the odds of a race, only an interval check detects one: confirm nothing changed between starting a measurement and reporting it.
9. A content check is not an interval check: asserting the target line is unique says nothing about whether someone wrote in between. Use primitives that fail when the target moved.
10. A ruling that changes what a task means belongs in the task text; a message is only the notification. Ask for it to be written in, and relay it before you stand down.
11. Task text drains with the task store: anything that must outlive it also goes into a versioned file or pact-memory.
12. When an acknowledgement lists only part of what you reported, ask which carrier holds the rest; "logged" is not a location.
13. To reach a teammate who has not started, amend the task text they will read at claim; message only someone already mid-flight. Say what is CLOSED, not just what is true.
14. Correct a wrong claim on the channel where you made it, not only in the file; a file fix does not reach anyone who read the message.
15. An escalated question is a hold, not a default: do independent work, and re-read the inbox before acting on the escalated point, before any irreversible step and before a protocol-boundary message.
16. A directive binds from when it was sent, not from when you noticed it; if you diverged, retract with a UTC timeline and hand the decisions back.
17. Silence on an async channel is not a fact: write the state of the exchange ("asked, not yet answered"), never an absence conclusion, and never into durable state.
18. When two agents reach the same decision, compare the reasons: a correct conclusion carried by a false rationale invites a later revert.
19. Holding the argument that resolves a question is not authority to close it: route it to the owner, say whether you think it is dispositive, and follow either way.
20. Relay a decision you did not make by QUOTING it; when a peer asserts a ruling without a quote, ask for the quote before complying or arguing.
21. After shipping a control or interface change, re-audit advice you gave before it and correct every recipient ("right when I said it, wrong now").
22. Comment on your own invariant, never on the current state of a file you do not own; the other side will change it and your sentence goes false.
23. Before concluding a peer ignored your request, read their artifact's rationale (headers, docblocks, commit body); the argument may beat yours.
24. Put findings in task metadata and send a one-line ping; less in flight means fewer crossed messages. Drain the inbox right before composing.
25. Before writing into a store you do not own, re-read the target in the same sequence as the write; if the content is already there, do not write.
26. `SendMessage` success means the inbox, not the reader: after three unacknowledged sends, switch carriers and put the answer itself in a durable artifact.

## Detail

**Fail-if-moved primitives.** `Edit` against on-disk content (a stale-anchor failure means the file
moved: re-read and re-anchor), `git rm` rather than `rm`, `mv -n`, an if-match header, a version
column. For a full-object PUT (an n8n workflow, a document), fetch immediately before each write,
never reuse a snapshot across two edits (you are the other writer), and declare the post-state
(node count, route tally) before writing so you can assert it after; absence in a PUT body is an
instruction to delete.

**Showing an "already done" crossing.** `git show HEAD:<file> | grep -c <phrase>` against the same on
the pushed tip settles "we read different trees" without anyone conceding. People are unreliable
about their own recent actions ("I pushed"); check `git ls-remote` or `merge-base --is-ancestor`.
Never force-push to reconcile. Say which direction a discrepancy errs, safe or unsafe.

**Split around a crossing.** When your escalation and a ruling may cross, do the part that does not
depend on the answer now, say which part is parked on which assumption, and prefer a design that is
correct under every possible ruling.

**Assignments that echo your own tasks.** A task you created can come back as a fresh assignment
notice; check it (status, owner, whether a handoff is populated) before acting, and if the store has
been reaped, the journal and the work product are the evidence. A spawn context that cannot name
your role is not authoritative about it; your agent definition is.

**Scope findings about someone else.** A lead may rule by message and leave the task text stale. An
artifact that carefully argues its own boundary is usually the signature of an instruction; phrase
the finding as an ownership question, not an accusation.

**A rule buried as a bullet in an unrelated memory does not fire.** If a lesson must change behaviour
on its own, it needs its own entry (see repacted:memory-hygiene).

## Examples
- A lead's "proceed with commit 1" crossed the teammate's stage-ready notice twice; verifying the
  staged state and flagging the race avoided a duplicate rebuild both times.
- A carve-out ("these two tokens are exempt") lived only in one inbox; the lane was split and
  respawned, and a fresh agent reading the task alone would have violated the ruling by complying.
- An agent read three unanswered questions as "there is no such field" and was about to record it;
  the answer ("yes, it exists") was already in flight.
- A lead quoted text for correction that the receiver had rewritten a minute after the lead read it;
  both reads were correct, and the file's mtime showed a race, not a stale copy.
