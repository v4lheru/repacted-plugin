"""
Location: pact-plugin/hooks/shared/protocol_descriptor.py
Summary: The ONE teaching source of the teachback / HANDOFF / wait contract. Field
         names, enum values, the reconstruction threshold, REV_MAX and the wait
         vocabulary are IMPORTED from the validating modules (teachback_schema,
         handoff_schema, intentional_wait, amendments, stored_line); only the
         one-line rule strings, the mistakes table and the view templates are
         authored here. The `render_*` functions turn the descriptor into the
         text of every model-read restatement: the marked regions in
         skills/teammate/SKILL.md, protocols/completion.md,
         reference/dispatch/teachback-gated-dispatch.md and the two spawn notes
         in hooks/shared/peer_context.py.
Used by: dev/protocol/render.py (writes or checks the regions),
         tests/contract/test_protocol_rendered.py (pins every region to the
         rendered text and the descriptor to the validators). NO hook imports
         this module at runtime: the regions are committed, so the hook-infra
         closures (hook_infra_classifier) stay as they are.

Contract: pure module; stdlib only; no I/O; import never raises.

Why a descriptor and committed regions rather than a generator at install time:
the surface stays readable in git and in the plugin cache without running
anything, and `render.py --check` (run by the contract test) proves the committed
text equals the rendered text byte for byte. A rule string that disagrees with a
validator is now one file away from the validator instead of sixteen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.amendments import ACK, AMEND_PREFIX, DISPOSITION_VERBS, LEAD, WAKE_VERBS
from shared.handoff_schema import (
    HANDOFF_CANONICAL_FIELDS,
    HANDOFF_RECOMMENDED_FIELDS,
    REV_MAX,
)
from shared.intentional_wait import (
    DEFAULT_THRESHOLD_MINUTES,
    KNOWN_REASONS,
    KNOWN_RESOLVERS,
)
from shared.stored_line import WHAT
from shared.teachback_schema import (
    TEACHBACK_OBJECT_FIELDS,
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
    TEACHBACK_RECOMMENDED_BAND_MIN,
    TEACHBACK_REQUIRED_FIELDS,
    TEACHBACK_REQUIRED_SUBKEYS,
    TEACHBACK_VARIETY_ACK_VALID_VALUES,
)


@dataclass(frozen=True)
class Field:
    """One key of a ceremony payload and the one-line rule a teammate needs for it."""

    name: str
    kind: str                       # "str" | "int" | "object" | "list" | "list|str"
    rule: str = ""
    required: bool = True
    required_when: str = ""         # non-empty: required only in this case
    values: tuple[str, ...] = ()    # enum, when the value is one of a fixed set
    subkeys: tuple[tuple[str, str], ...] = ()   # (name, rule) of an object field


@dataclass(frozen=True)
class Ceremony:
    """One metadata write a teammate makes: its key, fields and the lines around it."""

    key: str
    fields: tuple[Field, ...]
    stored_line: str
    notify: str
    rejection_key: str
    mistakes: tuple[tuple[str, str], ...] = ()
    ordering: tuple[str, ...] = ()
    rejection_reasons: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def field_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields)

    def required_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.required and not f.required_when)


@dataclass(frozen=True)
class Protocol:
    teachback: Ceremony
    handoff: Ceremony
    wait: Ceremony


WAIT_REASON_DEFAULT = "awaiting_lead_completion"
WAIT_RESOLVER_DEFAULT = "lead"
SINCE_COMMAND = "date -u +%Y-%m-%dT%H:%M:%S+00:00"
# The post-notify wait: returns at once when the lead already accepted, rejected or amended.
WAIT_COMMAND = "repact-wait <id> --until completed,rejection,amend"
# How a teammate reads what the lead wrote: the value is in the task file, never in a message.
REJECTION_READER = "repact-payload <id> --key rejection"
AMEND_READER = "repact-payload <id> --key amend"
# The one line a lead sends after a directive is on disk. amendments.wake_line() builds the
# same line from real values; tests/contract/test_protocol_rendered.py pins the two together.
WAKE_LINE = f"[{LEAD}→<teammate>] Task #<id>: <{'|'.join(WAKE_VERBS)}> on disk, digest <d>."
UNCERTAINTY_PREFIXES = ("fixed:", "known-limit:", "not-applicable:")
REF_DISPOSITIONS = ("done:", "partial:", "deferred:", "dropped:")

# Rules of the four string fields of a teachback, by name. The object field's
# rule is on its Field below.
_TEACHBACK_RULES = {
    "understanding": "the brief restated: what you build, constraints, interfaces",
    "most_likely_wrong": "",
    "least_confident_item": "one assumption for the lead to confirm",
    "first_action": "",
}

_VARIETY_ACK_SUBKEYS = (
    ("rationale_articulates_this_dispatch", " | ".join(f'"{v}"' for v in TEACHBACK_VARIETY_ACK_VALID_VALUES)),
    ("concern", '"<non-empty unless yes>"'),
)

PROTOCOL = Protocol(
    teachback=Ceremony(
        key="teachback_submit",
        fields=(
            *(Field(name, "str", _TEACHBACK_RULES[name])
              for name in TEACHBACK_REQUIRED_FIELDS if name not in TEACHBACK_OBJECT_FIELDS),
            *(Field(name, "object", subkeys=_VARIETY_ACK_SUBKEYS)
              for name in TEACHBACK_REQUIRED_FIELDS if name in TEACHBACK_OBJECT_FIELDS),
            Field("reasoning_reconstruction", "object",
                  rule="exactly these keys, non-empty strings",
                  required_when=f"the variety total is {TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN}+",
                  subkeys=tuple((k, "") for k in TEACHBACK_REQUIRED_SUBKEYS)),
        ),
        stored_line="Teachback stored on #<id> (...), digest <d>",
        notify="[{sender}→team-lead] Teachback submitted on Task #<id>, digest <d>.",
        rejection_key="teachback_rejection",
        mistakes=(
            (f"`variety_acknowledgment` as a string, or a value outside {'/'.join(TEACHBACK_VARIETY_ACK_VALID_VALUES)}",
             "an OBJECT; `concern` required unless `yes`"),
            ("`reasoning_reconstruction` inside `metadata.handoff`",
             "a sibling field on `metadata.teachback_submit`"),
            ("`reasoning_reconstruction` with other sub-keys or empty values",
             f"exactly the {len(TEACHBACK_REQUIRED_SUBKEYS)} keys, each a non-empty string"),
            ("`intentional_wait` nested inside `teachback_submit`",
             "a separate top-level key, in a separate `TaskUpdate`"),
        ),
        ordering=("`metadata.teachback_submit` write FIRST",
                  "notify the team-lead via `SendMessage` with the task id and digest SECOND",
                  f"`intentional_wait{{reason={WAIT_REASON_DEFAULT}}}` SET THIRD",
                  f"`{WAIT_COMMAND}`"),
        rejection_reasons=(
            ("missing_reasoning_reconstruction",
             f"absent at variety {TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN}+"),
            ("malformed_reasoning_reconstruction",
             "not an object of exactly " + ", ".join(f"`{k}`" for k in TEACHBACK_REQUIRED_SUBKEYS)),
            ("empty_reasoning_reconstruction_field",
             "a sub-key that is missing, not a string or empty"),
        ),
    ),
    handoff=Ceremony(
        key="handoff",
        fields=(
            Field("rev", "int",
                  rule="a bare positive integer, 1 on the first write, +1 on each rewrite"),
            Field("amendments", "object",
                  rule=(f'{{"{AMEND_PREFIX}<n>": '
                        + " | ".join(f'"{verb}: <{why}>"' for verb, why in zip(DISPOSITION_VERBS, ("where", "why", "why")))
                        + f' | "{ACK}"}}')),
            *(Field(name, "list|str", required=name not in HANDOFF_RECOMMENDED_FIELDS)
              for name in HANDOFF_CANONICAL_FIELDS),
            Field("refs", "object", required_when="your task carries project tasks",
                  rule='one per ref, `{"T-<id>": "done: <evidence>"}`, else `partial:`, `deferred:` or `dropped: <why>`'),
        ),
        stored_line="HANDOFF stored on #<id> (owner <you>): rev N, digest <d>",
        notify="[{sender}→team-lead] Task complete on #<id>, rev <n>, digest <d>.",
        rejection_key="handoff_rejection",
        ordering=("`metadata.handoff` FIRST", "notify with task id, rev and digest",
                  "SET the wait", f"`{WAIT_COMMAND}`"),
    ),
    wait=Ceremony(
        key="intentional_wait",
        fields=(
            Field("reason", "str", values=tuple(sorted(KNOWN_REASONS))),
            Field("expected_resolver", "str", values=tuple(sorted(KNOWN_RESOLVERS))),
            Field("since", "str", rule=f"a tz-aware UTC clock reading, never a guess: run `{SINCE_COMMAND}` alone"),
            Field("covers_since", "str",
                  rule="equals `since` on a new wait; a re-SET of a wait you still hold takes a fresh `since` "
                       "and carries `covers_since` unchanged"),
        ),
        stored_line="",
        notify="",
        rejection_key="",
    ),
)

# The stored-line word for each ceremony key, from the hook that prints the line;
# the rev bound the task store can hold exactly; the wait staleness horizon.
STORED_WHAT = dict(WHAT)
HANDOFF_REV_MAX = REV_MAX
WAIT_STALE_MINUTES = DEFAULT_THRESHOLD_MINUTES


# ---------------------------------------------------------------------------
# Views. Each returns the exact text of one marked region (no trailing newline).
# The sentences are the ones the surfaces carried before generation; only the
# contract pieces (names, enums, counts, thresholds, lines) come from PROTOCOL.
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven", 8: "Eight", 9: "Nine"}


def _tick(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _teachback_field_list(ceremony: Ceremony) -> str:
    """`understanding` (rule), `most_likely_wrong`, ... and `variety_acknowledgment`, an OBJECT {...}"""
    strings = [f for f in ceremony.fields if f.kind == "str"]
    objects = [f for f in ceremony.fields if f.kind == "object" and not f.required_when]
    parts = [f"`{f.name}`" + (f" ({f.rule})" if f.rule else "") for f in strings]
    for obj in objects:
        shape = ", ".join(f'"{k}": {v}' for k, v in obj.subkeys)
        parts.append(f"`{obj.name}`, an OBJECT `{{{shape}}}`")
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def render_skill_teachback() -> str:
    """SKILL.md §Teachback steps 2-5: the write, the conditional field, the stored line, the notify."""
    tb = PROTOCOL.teachback
    strings = [f for f in tb.fields if f.kind == "str"]
    required = [f for f in tb.fields if not f.required_when]
    conditional = [f for f in tb.fields if f.required_when]
    lines = [
        f"2. Write `metadata.{tb.key}`, an OBJECT, in ONE `TaskUpdate`. {_NUMBER_WORDS[len(required)]} fields, "
        f"the first {_NUMBER_WORDS[len(strings)].lower()} non-empty strings: {_teachback_field_list(tb)}. "
        "A `WITHHELD:` rationale is deliberate: answer `concern` and quote it.",
    ]
    for f in conditional:
        keys = ", ".join(f'"{k}"' for k, _ in f.subkeys)
        lines.append(f"3. When {f.required_when}, add `{f.name}` `{{{keys}}}`: {f.rule}. Optional below that.")
    lines += [
        f"4. Read the tool result: notify only after its line `{tb.stored_line}`. `NOT STORED`, a refusal or a "
        "correction: do not notify, write the FULL object again (a partial write erases what you omit). "
        "No line at all: read the task file back; notify only if it holds your write.",
        f"5. Notify, one line: `{tb.notify}` Never repeat the teachback: the lead reads the stored copy.",
        f"6. SET the wait (below) in a SEPARATE `TaskUpdate`, then `{WAIT_COMMAND}`. The lead completes Task A; "
        "you never do.",
        f"7. Rejected (`metadata.{tb.rejection_key}`; read it: `{REJECTION_READER}`): CLEAR the wait, rewrite the full "
        "object on the same task with `metadata.revision_number` +1 (2 on the first resubmit), notify, SET again.",
        "",
        "### Common mistakes",
        "",
        "| # | Wrong shape | Canonical shape |",
        "|---|---|---|",
    ]
    lines += [f"| {i} | {wrong} | {right} |" for i, (wrong, right) in enumerate(tb.mistakes, 1)]
    return "\n".join(lines)


def render_skill_handoff() -> str:
    """SKILL.md §HANDOFF steps 4-6: the key list in order, the field rules, the stored line, the order."""
    ho = PROTOCOL.handoff
    always = [f for f in ho.fields if not f.required_when]
    recommended = [f.name for f in always if not f.required]
    by_case = [f for f in ho.fields if f.required_when]
    rules = {f.name: f.rule for f in ho.fields if f.rule}
    lines = [
        f"4. Write `metadata.{ho.key}`, an OBJECT, in ONE `TaskUpdate`. Keys, in order: "
        f"{_tick(f.name for f in always)}. All but {_tick(recommended)} (recommended) are required; "
        "a list may be empty. Keys, not labels: `decisions`, never `key_decisions`.",
        f"   - `rev`: {rules['rev']}. `2.5`, `true` and `0` are returned; `\"2\"` is repaired.",
        f"   - `amendments`: `{rules['amendments']}`, one entry per key; `{{}}` with none.",
        f"   - Each `uncertainty` item opens with {', '.join(f'`{p}`' for p in UNCERTAINTY_PREFIXES[:-1])}"
        f" (into the PR) or `{UNCERTAINTY_PREFIXES[-1]}`.",
    ]
    for f in by_case:
        lines.append(f"   - `{f.name}`, when {f.required_when}: {f.rule}. A missing ref returns the write.")
    lines += [
        f"5. Read the tool result: notify only after its line `{ho.stored_line}`; another owner means the wrong "
        "task. `NOT STORED` or a refusal (a gate can RETURN the write) means no copy is on disk: do not notify, "
        "fix what it names and write the FULL object again. No line at all: read `rev` back from the task file; "
        "notify only if the disk holds your rev.",
        f"6. Order: {' → '.join(ho.ordering)}. The notify, one line: `{ho.notify}` "
        "Never repeat the HANDOFF: the lead reads the stored copy.",
    ]
    return "\n".join(lines)


def render_skill_wait() -> str:
    """SKILL.md §Waiting and waking items 1-4: the SET shape, the vocabulary, since and covers_since."""
    wait = PROTOCOL.wait
    by_name = {f.name: f for f in wait.fields}
    reasons = [r for r in by_name["reason"].values if r != WAIT_REASON_DEFAULT]
    shape = (f'{{"reason": "{WAIT_REASON_DEFAULT}", "expected_resolver": "{WAIT_RESOLVER_DEFAULT}", '
             '"since": "<now>", "covers_since": "<now>"}')
    return "\n".join([
        f"1. SET before idling on any protocol wait, as a top-level key: "
        f"`TaskUpdate(taskId, metadata={{\"{wait.key}\": {shape}}})`.",
        f"2. `reason` and `expected_resolver` are non-empty strings. Resolvers: "
        f"{_tick(by_name['expected_resolver'].values)}. Other reasons: {_tick(reasons)}.",
        f"3. `since` is {by_name['since'].rule}; only it and `repact-wait` may run before acceptance.",
        f"4. `covers_since` {by_name['covers_since'].rule}: the write replaces the whole object.",
    ])


def render_completion_acceptance() -> str:
    """protocols/completion.md: what the lead checks on a teachback or HANDOFF, one line per ceremony."""
    tb, ho = PROTOCOL.teachback, PROTOCOL.handoff
    strings = [f.name for f in tb.fields if f.kind == "str"]
    ack = next(f for f in tb.fields if f.kind == "object" and not f.required_when)
    rr = next(f for f in tb.fields if f.required_when)
    reasons = dict(tb.rejection_reasons)
    ho_required = [f.name for f in ho.fields if f.required and not f.required_when and f.kind != "int"
                   and f.name != "amendments"]
    ho_recommended = [f.name for f in ho.fields if not f.required]
    return "\n".join([
        "Rendered from `hooks/shared/protocol_descriptor.py`:",
        f"- `{tb.key}`: an object with {_tick(strings)} (non-empty strings) and `{ack.name}` "
        f"(an object: `{ack.subkeys[0][0]}` in {'/'.join(TEACHBACK_VARIETY_ACK_VALID_VALUES)}, "
        f"`{ack.subkeys[1][0]}` non-empty unless yes).",
        f"- `{rr.name}`: required when {rr.required_when} (missing → `missing_reasoning_reconstruction`); "
        f"{reasons['malformed_reasoning_reconstruction']} → `malformed_reasoning_reconstruction`; "
        f"{reasons['empty_reasoning_reconstruction_field']} → `empty_reasoning_reconstruction_field`. "
        f"Recommended at {TEACHBACK_RECOMMENDED_BAND_MIN}-{TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN - 1}.",
        f"- `{ho.key}`: `rev` {ho.fields[0].rule}; `amendments` one disposition per `{AMEND_PREFIX}<n>` "
        f"({'/'.join(DISPOSITION_VERBS)} or {ACK}); required {_tick(ho_required)}; {_tick(ho_recommended)} "
        f"recommended; `refs` when {ho.fields[-1].required_when}.",
        f"- The wake, sent after the write is stored: `{WAKE_LINE}` One line, the digest from the stored line, "
        f"never a value; the teammate reads with `{REJECTION_READER}` or `{AMEND_READER}`.",
    ])


def render_dispatch_task_a() -> str:
    """The Task A description every dispatch pastes (the lines inside TaskCreate's description=)."""
    tb = PROTOCOL.teachback
    strings = [f.name for f in tb.fields if f.kind == "str"]
    ack = next(f for f in tb.fields if f.kind == "object" and not f.required_when)
    return "\n".join([
        '    description="DOGFOOD TEACHBACK GATE for {feature}.\\n\\n"',
        f'                "Submit TEACHBACK by writing metadata.{tb.key} using the CANONICAL field schema '
        f'(do NOT improvise key names): {", ".join(strings)}, {ack.name} (an OBJECT). '
        'See the teammate skill for field semantics. "',
        '                "Then send the notify SendMessage with the task id and the digest the stored line printed, '
        'never the teachback itself (teammate skill, Teachback step 5). "',
        f'                "SET intentional_wait{{reason={WAIT_REASON_DEFAULT}, expected_resolver={WAIT_RESOLVER_DEFAULT}, '
        'since=<canonical_since() output>, covers_since=<the same value as since>}. Idle. "',
        '                "DO NOT mark this task completed — team-lead-only completion. Lead will mark completed "',
        '                "after teachback acceptance, then send the one-line wake; Task B is then claimable. "',
        f'                "If TEACHBACK is rejected, team-lead writes metadata.{tb.rejection_key} and sends the one-line "',
        f'                "wake; read it with {REJECTION_READER} and revise on this same task.\\n\\n"',
        '                "When Task B unblocks, claim it (TaskUpdate status=in_progress) BEFORE any implementation tool-use — '
        'it is pre-assigned to you but still pending; you flip it, not the lead. Task B stays blocked until the lead '
        'accepts this teachback: that is the gate, not a desync.\\n\\n"',
        '                "Mission for Task B: the primary-work task assigned to you in your TaskList (the work task, NOT '
        "this TEACHBACK gate task), identified by its subject (the '{name}: {mission}' pattern). Claim it after this "
        'teachback is accepted."',
    ])


def render_dispatch_task_b() -> str:
    """The lines that close every Task B description (claim first, never self-complete, HANDOFF, rejection)."""
    ho = PROTOCOL.handoff
    return "\n".join([
        '                "FIRST claim this task (TaskUpdate status=in_progress) before any implementation tool-use — '
        'it is pre-assigned to you but still pending; you flip it, not the lead.\\n\\n"',
        '                "DO NOT mark this task completed yourself. "',
        f'                "After staging artifacts, write metadata.{ho.key}, send notify SendMessage with task id, rev and '
        'digest (teammate skill, HANDOFF step 6) to team-lead, "',
        f'                "SET intentional_wait{{reason={WAIT_REASON_DEFAULT}}}. Idle. Lead will mark "',
        f'                "completed after HANDOFF validation. If team-lead rejects, team-lead writes metadata.{ho.rejection_key}; "',
        f'                "read it with {REJECTION_READER} and revise on this same task.\\n\\nWrite the HANDOFF whatever the size of the diff — a task whose '
        'deliverable is a JUDGEMENT rather than a CHANGE carries its value in the rationale, not in the diff.'
        '\\n\\nUpstream: TEACHBACK Task #{A_id}.",',
    ])


def render_spawn_notes() -> str:
    """The two constants peer_context.py appends to every teammate's spawn context, as Python source."""
    tb, ho = PROTOCOL.teachback, PROTOCOL.handoff
    return "\n".join([
        "_TEACHBACK_REMINDER = (",
        f'    "\\n\\nTEACHBACK TIMING: write metadata.{tb.key} on Task A BEFORE any Edit/Write/Bash "',
        f'    "call; the only shell exceptions are `{SINCE_COMMAND}` (the wait\'s `since`) "',
        '    "and `repact-wait` after the notify. The teachback is a gate: Task B stays blocked until "',
        '    "the team-lead accepts. The teammate skill holds the format."',
        ")",
        "",
        "_COMPLETION_AUTHORITY_NOTE = (",
        '    "\\n\\nCOMPLETION AUTHORITY: you do NOT mark your own tasks `completed`. Write the HANDOFF "',
        f'    "(metadata.{ho.key}) or the teachback to the task and stay `in_progress`; the team-lead "',
        '    "transitions status to `completed` on accept. On a Task A (teachback) + Task B (work) pair: "',
        f'    "claim A, submit, SET `intentional_wait{{reason={WAIT_REASON_DEFAULT}}}`, run "',
        f'    "`{WAIT_COMMAND.replace("<id>", "<A id>")}` in the foreground, and begin B only when A "',
        '    "is `completed` on disk."',
        ")",
    ])


# Region name -> (relative path from the plugin root, renderer). The ONE map the
# dev renderer and the contract test share.
REGIONS: dict[str, tuple[str, object]] = {
    "teachback": ("skills/teammate/SKILL.md", render_skill_teachback),
    "handoff": ("skills/teammate/SKILL.md", render_skill_handoff),
    "wait": ("skills/teammate/SKILL.md", render_skill_wait),
    "acceptance": ("protocols/completion.md", render_completion_acceptance),
    "dispatch-task-a": ("reference/dispatch/teachback-gated-dispatch.md", render_dispatch_task_a),
    "dispatch-task-b": ("reference/dispatch/teachback-gated-dispatch.md", render_dispatch_task_b),
    "spawn-notes": ("hooks/shared/peer_context.py", render_spawn_notes),
}

# The key names a hand copy of the contract would carry. A line outside a region
# naming CONTRACT_KEYS_MAX or more of them is a restatement, not a pointer.
CONTRACT_KEYS: tuple[str, ...] = (
    "teachback_submit", "variety_acknowledgment", "reasoning_reconstruction", "metadata.handoff",
    "intentional_wait", "covers_since", "expected_resolver", "handoff_rejection", "teachback_rejection",
)
CONTRACT_KEYS_MAX = 3
