# Teachback (field reference)

Read this only when `repact submit --teachback` names a field. The brief's teachback skeleton already holds every key; you replace the `<...>` values.

## Action: store teachback now

A teachback says the brief back in your own words before you change anything, so a wrong reading costs one message instead of a rework. Write it from the brief and the code you have read, not from the task title. Then run the brief's `repact submit <id> --teachback <file>` line, and do what it prints.

<!-- protocol:teachback-fields -->
| Field | What to write |
|---|---|
| `understanding` | the brief restated: what you build, constraints, interfaces |
| `most_likely_wrong` | the part of your plan most likely to be wrong |
| `least_confident_item` | one assumption for the lead to confirm |
| `first_action` | your first concrete step |
| `variety_acknowledgment` | an object: `rationale_articulates_this_dispatch` is `yes`, `no` or `concern`; `concern` says why unless the answer is `yes` |
| `reasoning_reconstruction` | required when the variety total is 11+, optional below: an object of exactly `decision_attribution`, `assumption_trace`, `contingency_clause`, each a non-empty string |

`repact submit --teachback` refuses, listing every problem at once:
- a field missing or empty, or an answer outside yes/no/concern, or `concern` empty when the answer is not `yes`;
- `reasoning_reconstruction` missing at 11+, or with other keys or an empty value;
- a key that only looks like a field (another case, a lookalike letter) or holds a control character: spell it exactly;
- a `<...>` placeholder left from the skeleton;
- a teachback over 64 KB;
- a different teachback once one is accepted: go on with the work instead.

It is accepted at once when the variety total is under 10 and your answer is `yes`. Otherwise the lead reads it: end your turn on `TEACHBACK SUBMITTED #<id>` (Teams mode: run the `repact-wait` line it prints).
<!-- /protocol -->

## Writing it well

- `understanding`: what you will build, the constraints, and the interfaces it touches. Name files.
- `most_likely_wrong` and `least_confident_item`: be specific. "Nothing" helps no one; name the assumption the lead can confirm in one line.
- `variety_acknowledgment`: the brief's variety stamp gives a score and a reason for novelty, scope, uncertainty and risk. Answer `yes` when the reasons describe this task, `no` when they do not, `concern` when one is wrong or missing, and say which in `concern`. A reason that opens `WITHHELD:` is deliberate: answer `concern` and quote it.
- `reasoning_reconstruction` (11+): `decision_attribution` names who decided what in the brief, `assumption_trace` the assumptions your plan rests on, `contingency_clause` what you do if the main one is false.

## When the lead reviews it

A correction arrives as an amendment (`repact inspect <id> --key amend_<n>`). Rewrite the whole teachback file and run the same submit line again; once it is accepted, go on with the work.
