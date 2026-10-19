# Rulings and relays

Context: the lead rules fast and constantly, mostly by message, and most losses in a team happen in
that channel. Read this when amending a task mid-flight, ruling on a question, relaying a fact or a
claim between agents, reversing yourself, or writing anything that tells another agent where to look.

## Canonical rules
1. Every post-dispatch ruling is written into the task as an `amend_<n>` key; a message is not persistence. A ruling given in a teachback-acceptance wake is one too: write it on the WORK task, even while that task is still blocked, or the teammate reads silence as acceptance. Message the assignee only when it is idle: a busy in-process one gets the amendment by the relay after its next tool call, and a message would sit until its turn ends and cross its report.
2. A supersession is a ruling too: withdrawing or reversing one is a new `amend_<n>` key under the same rule, or the assignee keeps complying with the withdrawn version.
3. Put the reason in the durable artifact first: a rationale is what the recipient needs most and what evaporates fastest, because an output has a home and a reason does not.
4. Write rulings so they survive being quoted (short, self-contained, reason attached); relayers paraphrase, and a paraphrase reads as the relayer's opinion.
5. A directive sent mid-run lands at the teammate's next turn boundary: tell teammates on long jobs to re-read the inbox and diff the deliverable against ALL outstanding directives before reporting.
6. Unblocking a teammate is a work item: when you remove an obstacle, amend the task text with the new deliverable; enabling guidance sent only by message vanishes.
7. Before accepting a task you amended mid-flight, diff your messages against the task text and the deliverable against the amendment.
8. When a fact arrives that closes someone's open item, relay it in the same turn; ask of anything the user sends: whose open question does this answer?
9. Quote the state your instruction assumes ("master at <sha>, both carriers read 1.6.1"); specificity is what lets the holder of a newer state catch you.
10. Re-verify the premise of a conditional instruction at the moment you write it, especially a fact about your own recent action ("it is unpushed").
11. Before telling an agent where to find something, open it and confirm the thing is there; prefer "find every carrier holding X" to "read it from X", and never pair a pointer with "this is the only copy".
12. Before reversing a ruling, or conceding a pushback, re-read the current artifact: the design may already answer the objection. Over-conceding and over-defending are the same failure.
13. Write the tense you have earned: "I asked X to move it" until you have seen it on disk. A request and its completion are plausible neighbours, and the gap is the unverified step. Never send a message that reports a mutation (a commit, a push, a write) in the same tool batch as the mutation: read its result first.
14. A claim about agent A written only into agent B's task is checked by nobody; tell the affected party directly. A carrier selects its readers.
15. Match the check to the risk: staged, unstaged and untracked are three states, and a clean index says nothing about unstaged edits. A safety assurance needs a timestamp.
16. Keep acceptance and correction messages terse: the ruling, not the praise and the restatement. Every message wakes an idle agent and reloads its whole context.
17. Before asking an agent to research something, check whether a live teammate already did; route the second asker to the first. Stop each agent once its task is accepted and nothing of its awaits verification (persona §11).
18. Sample transcript mtimes twice a few seconds apart before asserting an agent is idle; reported idleness is not observed idleness.
19. An auditor flagging a specialist for something your message authorised is evidence the record is stale: fix the record, do not re-explain the ruling.
20. When a recipient says your two messages state the bar differently, they are almost certainly right: a bar restated from memory drifts toward the method that last worked.
21. On a crossed idle notification, compare timestamps: one that predates your send is a straggler; one that postdates it and still shows the directive unprocessed needs ONE confirm, then stop. The FIRST idle after your send is the delivery boundary itself (a message lands as that turn ends), so it shows nothing yet: wait one more idle before confirming. An idle `result` is cut by the platform ("[result truncated...]") and can repeat a report you already accepted: read the stored HANDOFF (`repact inspect <id> --key handoff`), never the summary, and when the queued status line shows that teammate's tasks all accepted, the summary is a straggler and gets no reply.
22. A teammate idle with `idleReason=failed` left no report: read durable state first (its task file, the artifacts on disk), then re-scope or reassign the step. Never resend the same instruction verbatim; the same turn fails the same way. Straight after a spawn on a model other than the session's, it usually means that model's usage limit: respawn on the session model.
23. Grant a resource a teammate asked for by writing `metadata.<resource>_grant` on the ASKING teammate's task (`docker_grant: "yours until task 12 is accepted"`; null withdraws it), not by message: the relay hands the key over within one tool call and an idle that missed it is sent back to read it, while a message waits for the turn to end.

## Detail

**The distribution finding.** Across four harvests of about sixty tasks, every gap the secretary
caught was in the lead's messages or dispatch content, never in the specialists' artifacts: handoffs
are structured and harvested, rulings in `SendMessage` bodies have no version, no diff and no
reviewer. The cheap detector is to diff your messages against the task file; the unbanked items are
usually rationales. Treat a message as the notification of a write you have already made.

**Why rules fail to fire for the lead.** A rule learned as a reader ("beware X when reviewing") is
indexed by that role's cues and does not fire when you are the author. Record the author-side twin
beside it, and when a correctly written artifact trips your own check, fix the check.

**The verification and the instruction should not share an author.** A scope item named in both the
task and the message still did not ship; a `--stat` read by someone with a reason to look caught it.
A requirement whose only verifier is its own author is a structural gap.

**Refs, not people.** When two parties disagree about whether work exists, suspect they read
different refs before suspecting either is wrong, and name the ref in the report (`git show HEAD:...`
vs the pushed tip).

**Adopt a specific admission verbatim** when a teammate offers harder phrasing about its own gap; a
soft version is what gets relayed onward.

## Examples
- Three amendments sent while a coder polled a long fetch arrived after it reported; the report
  omitted all three and nobody could see why from either end.
- A ruling written into both tasks' metadata but messaged to one agent: the other shipped against
  it, because TaskGet returns the description, not the metadata.
- "Read the stamps from `dispatch_variety`" inside a recovery instruction: that event held nothing
  for the arc; the secretary ignored the pointer, ran a census, and found them in snapshots.
- The lead wrote "I have moved it into its own worktree" into another agent's task seconds after
  merely asking; for twenty minutes the first agent kept editing the release agent's tree.
