# VD-E: completion claims

Context: read this before you write "done", "fixed", "I have X", "X is staged", "the tests cover
Y" or a HANDOFF, and before you accept one. These claims describe work from the author's
intent; the artefact is what counts. Proving a deploy or write landed is in
[vd-f-deploy-and-write-proof.md](vd-f-deploy-and-write-proof.md). Entry point:
[vd-e-relay-and-reporting.md](vd-e-relay-and-reporting.md).

## Canonical rules

- **E.25** Before a DONE report, re-read every directive received since you started and state each as done, not done or deferred with a reason. A green suite covers only the properties it was written to test.
  - An absent requirement is harder to spot than one done wrongly, because nothing in the report points at it; silence reads as "handled".
  - Messages sent while a long job is polled may not reach you until the poll returns: shorten poll windows, or make "check the inbox and re-read open directives" a mandatory pre-report step. Recompute a directive's census against current state rather than building to stale numbers.
- **E.26** Never describe an artefact you produced from intent. `ls`, `grep` or `git status` it first; the container being yours does not make its contents yours.
  - Before "I have X", list it; before "X is staged", check the index; before "the tests cover Y", grep the assertions. Run an adversarial audit of your own output for the whole pattern class you claim to have removed, not just your sentinel.
  - Scrubbers match a pattern at any depth and redact whole subtrees, keeping key names and nulls ("present but scrubbed" and "absent" are different facts).
  - An exemption scoped to the container instead of the contents recurs at every layer: a key list, a vendor URL carrying a client value in its query string, a file fixed while its commit still holds the leak, an amend of a shared index, a value validated at one layer and coerced at the next. Ask what X can contain, and what contains the thing you fixed.
  - A recommendation made to someone else needs more verification than an action you take yourself. If you propagated a wrong claim, correct it directly to everyone who received it.
- **E.27** A general action running over an item does not prove that item fixed. If you cannot name the specific thing fixed (the string, the row, the id), your claim is about the action.
  - Cross-read every outbound draft against the record: diff what the message asserts against what the record contains, and treat an assertion with no backing entry as unfounded. Someone without domain knowledge can run this check.
- **E.28** Put file-content proof (the `grep` or `sed -n` lines showing the change) in a completion message, and answer signature or contract questions by pasting source lines, never from memory.
  - A lead's verification can race your write; proof in the message closes that race. Show the proving lines, not the whole file, and never quote a sensitive file.
  - The published source is the contract, not your prose: paste the signature with `file:line`, and prove "nothing changed" with a fresh build line.
- **E.29** In a pre-push handoff, never quote a bare ahead or behind count. Name the ref it was measured against (`git log --oneline <push-target>..HEAD`) and list dirty files deliberately left out.
  - `git status -sb` compares to the branch's upstream, which may be a stale remote branch. Listing what the push will not carry puts that decision with whoever owns the push.
- **E.30** Run `wc -l` on any line count a HANDOFF states; expect it to be understated. Check enum and section descriptions against the artefact's actual distinct values.
  - The gap usually measures late-added verification code, so weight it as cosmetic unless a substantive claim also fails. An undocumented extra enum value is a real downstream contract risk.
- **E.31** The work can be right while the claim about it is wrong. Ask what calls a control, what the denominator includes, and whether the name asserts more than the method establishes.
  - A filter with green tests that is installed on nothing has never run; tests that construct and call it pass forever. Test the WIRING, then delete the install and confirm the test goes red.
  - Put the definition on the wire as a value (`measures`, a count beside a rate, coverage beside a base rate) and split a seductive number into fields that must travel together. Guard a structural test against a future rename to a semantic name.
  - A claim that "X is safe because Y covers it" needs Y's documented bound read. A precision check whose population is selected by the predicate under test measures that predicate's definition. Make a residual executable, not prose.
- **E.32** Diff the artefact names a handoff or plan said it would produce against what is on disk, in both directions. A mismatch is dropped scope, not bookkeeping.
  - Check the count at every derived layer; a gap propagates. A split name (one planned item becoming two) is a plan change with full coverage; a planned name with no successor is dropped scope. Record the inventory as fact and leave the reason blank.
  - `git status` shows an untracked file as `??` forever and `git diff` shows nothing, so git cannot tell an untouched file from one another lane rewrote: hash it or `git add -N` it.
  - A per-lane manifest cannot contain cross-lane edits by construction; sweep every field of every report, then subtract each lane's own manifest. Attribute by who owns the file, not which lane was active.
  - An overlap list is load-bearing only if you intend to split the change. Landing it as one commit moves the gate to "build the union tree and run it green".

## Examples
- E.25: a long job was reported DONE with every self-written check green while three mid-run directives, including the gate that would have caught the other two, were missing.
- E.26: "kept the probe output" was true of 1 of 3 payloads, and a "redacted" capture still held a real address in a nested singular key.
- E.27: an outbound draft said a stakeholder's flagged item was "done too"; the pipeline had run, but the item had never been seen.
- E.28: a peer's signature question answered from memory got two of the answerer's own published functions wrong.
- E.29: "ahead 6" was against a stale remote branch; against the push target it was "ahead 2, behind 0".
- E.30: three HANDOFF line counts ran 15–31% under the real files, and one described two sections of a file that shipped with three.
- E.31: a secret-redacting log filter had 17 green tests and was installed on no handler.
- E.32: an audit handoff's planned interview folders differed from disk, and one scheduled interview had been silently dropped.
