"""
Location: pact-plugin/hooks/shared/protocol_descriptor.py
Summary: The ONE teaching source of the teachback / HANDOFF / wait contract. Field
         names, enum values, thresholds and the wait vocabulary are IMPORTED from
         the validating modules (teachback_schema, handoff_schema, amendments,
         ref_dispositions, intentional_wait, ops.submit, ops.dispatch); only the
         one-line rule strings and the view templates are authored here. The
         `render_*` functions turn the descriptor into the text of every
         model-read restatement, each a marked region:
         - the coder's side (2.1, `repact submit`): skills/teammate/SKILL.md, the
           field pages teammate-teachback.md and teammate-handoff.md, the
           Teams-only wait rules in teammate-waiting.md, the HANDOFF skeleton and
           guide in hooks/shared/ops/brief.py, and the two Teams spawn notes in
           hooks/shared/peer_context.py.
         The lead's side has no region: `repact dispatch` writes the brief.
Used by: dev/protocol/render.py (writes or checks the regions),
         tests/contract/test_protocol_rendered.py (pins every region to the
         rendered text and the descriptor to the validators). NO hook imports
         this module at runtime: the regions are committed, so brief.py and
         peer_context.py read plain constants and the hook import closures stay
         as they are.

Contract: pure module; no I/O; import never raises.

Why a descriptor and committed regions rather than a generator at install time:
the surface stays readable in git and in the plugin cache without running
anything, and `render.py --check` (run by the contract test) proves the committed
text equals the rendered text byte for byte. A rule string that disagrees with a
validator is one file away from the validator.
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
from shared.ops.dispatch import TWO_PHASE_MIN
from shared.ops.submit import (
    HANDOFF_MAX as SUBMIT_HANDOFF_MAX,
    LIST_KEYS as SUBMIT_LIST_KEYS,
    REQUIRED_KEYS as SUBMIT_REQUIRED_KEYS,
    TEACHBACK_MAX as SUBMIT_TEACHBACK_MAX,
)
from shared.ref_dispositions import VERBS as REF_VERBS
from shared.teachback_schema import (
    TEACHBACK_OBJECT_FIELDS,
    TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN,
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
REJECTION_READER = "repact inspect <id> --key rejection"
AMEND_READER = "repact inspect <id> --key amend_<n>"
# The one line a lead sends after a directive is on disk. amendments.wake_line() builds the
# same line from real values; tests/contract/test_protocol_rendered.py pins the two together.
WAKE_LINE = f"[{LEAD}→<teammate>] Task #<id>: <{'|'.join(WAKE_VERBS)}> on disk, digest <d>."
UNCERTAINTY_PREFIXES = ("fixed:", "known-limit:", "not-applicable:")
REF_DISPOSITIONS = tuple(f"{verb}:" for verb in REF_VERBS)

# Rules of the four string fields of a teachback, by name. The object field's
# rule is on its Field below.
_TEACHBACK_RULES = {
    "understanding": "the brief restated: what you build, constraints, interfaces",
    "most_likely_wrong": "the part of your plan most likely to be wrong",
    "least_confident_item": "one assumption for the lead to confirm",
    "first_action": "your first concrete step",
}

_VARIETY_ACK_SUBKEYS = (
    ("rationale_articulates_this_dispatch", " | ".join(f'"{v}"' for v in TEACHBACK_VARIETY_ACK_VALID_VALUES)),
    ("concern", '"<non-empty unless yes>"'),
)

# The field tuples are current: the contract test pins them to the validators, and the 2.1
# views below read them. The stored and notify lines are the 2.0 Agent Teams wording, kept as
# data. The `wait` ceremony is current in Teams mode.
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
            Field("refs", "object", required_when="your task carries project tasks or ACs",
                  rule='one per ref, `{"T-<id>": "done: <evidence>"}` or `{"AC-<n>": "done: <passing run log stem>"}`, '
                       'else `partial:`, `deferred:` or `dropped: <why>`'),
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

# The rev bound the task store can hold exactly; the wait staleness horizon.
HANDOFF_REV_MAX = REV_MAX
WAIT_STALE_MINUTES = DEFAULT_THRESHOLD_MINUTES


# ---------------------------------------------------------------------------
# Views. Each returns the exact text of one marked region (no trailing newline).
# The sentences are the ones the surfaces carried before generation; only the
# contract pieces (names, enums, counts, thresholds, lines) come from PROTOCOL.
# ---------------------------------------------------------------------------

def _tick(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def render_skill_wait() -> str:
    """teammate-waiting.md (Teams only): the SET shape, the vocabulary, since and covers_since."""
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


# ---------------------------------------------------------------------------
# 2.1: the coder's path. One brief, two
# skeleton files and one command, `repact submit`, which checks, repairs and
# stores. The texts below say only what ops/submit.py really does.
# ---------------------------------------------------------------------------

SUBMIT_TEACHBACK = "repact submit <id> --teachback <file>"
SUBMIT_HANDOFF = "repact submit <id>"
# The line a two-phase teachback ends the turn on (ops/submit.py prints it, and
# hooks/subagent_stop.py lets a turn end on it).
TEACHBACK_STOP_LINE = "TEACHBACK SUBMITTED #<id>"
# What each exit of `repact submit` means (bin/_repact_ops.py, Exit codes).
SUBMIT_EXITS = (
    ("0", "stored, and accept will pass the checks it can see now. End your turn."),
    ("2", "nothing stored. It lists every problem in one message: fix them all, then run it again."),
    ("3", "stored, but accept will refuse for the reasons it lists (most often: no passing run of "
          "the brief's tcount on the tree as it is now). Fix them, rerun the tests with the brief's "
          "tcount path, then run submit again."),
)
_SUBMIT_REPAIRS = ("it sets `rev` itself, makes a relative `produced` path absolute against your "
                   "worktree, and adds a missing " + ", ".join(f"`{k}`" for k in SUBMIT_LIST_KEYS)
                   + " as `[]`")

# What to write in each HANDOFF key, for the field page and the brief's guide.
_HANDOFF_RULES = {
    "produced": "one item per file you changed, its path first, then what changed",
    "decisions": "the choices you made, one line each with its why",
    "reasoning_chain": "a few lines on how you got there",
    "uncertainty": ("each item opens with " + ", ".join(f"`{p}`" for p in UNCERTAINTY_PREFIXES[:-1])
                    + f" (it goes into the PR) or `{UNCERTAINTY_PREFIXES[-1]}`"),
    "integration": "what the lead or the next unit must know to use this",
    "open_questions": "what is still open; an unfinished step opens with `remaining:`",
}


# The skeleton's placeholder for each required key (submit refuses a placeholder left in place).
_SKELETON_PLACEHOLDERS = {
    "produced": ["<absolute path in your worktree>: <what changed>"],
    "integration": "<what the lead or the next unit must know to use this>",
}


def _required_word(name: str) -> str:
    if name in SUBMIT_REQUIRED_KEYS:
        return "required"
    if name in SUBMIT_LIST_KEYS:
        return "a list, may be empty"
    return "recommended" if name in HANDOFF_RECOMMENDED_FIELDS else "optional"


def render_skill_steps() -> str:
    """SKILL.md §The work: the five steps from the brief to the stored HANDOFF, and submit's exits."""
    return "\n".join([
        "1. Read the brief whole. Work only in its worktree, with absolute paths or `git -C <worktree>`.",
        "2. Teachback: fill the teachback skeleton in your own words, then run the brief's "
        f"`{SUBMIT_TEACHBACK}` line. No Edit, Write or Bash for the work before it is stored; "
        "reading is fine.",
        "   - It prints `Accepted automatically`: go on.",
        f"   - It prints `Stop here`: end your turn with the one line `{TEACHBACK_STOP_LINE}`. The lead "
        f"resumes you. This happens when the variety total is {TWO_PHASE_MIN}+ or your answer is "
        "not `yes`.",
        "3. Do the work. Invoke the skills your agent file lists first.",
        "4. Run tests the way the brief's Tests line says: with the tcount path it gives (a bare "
        "`tcount` can be another install), from the worktree, after your last edit; for a fix or a "
        "feature, a red run that names the test file comes first. A count you report must come from "
        "a recorded run; accept reads the runs from the ledger.",
        "5. Hand in: fill the HANDOFF skeleton (replace every `<...>` placeholder), then run the "
        f"brief's `{SUBMIT_HANDOFF}` line, without `--teachback`. It repairs what is mechanical: "
        f"{_SUBMIT_REPAIRS}. Then:",
        *[f"   - exit {code}: {meaning}" for code, meaning in SUBMIT_EXITS],
    ])


def render_teachback_fields() -> str:
    """teammate-teachback.md: the fields, what submit refuses, and when the lead reviews it."""
    tb = PROTOCOL.teachback
    rows = [f"| `{f.name}` | {f.rule} |" for f in tb.fields if f.kind == "str"]
    ack = next(f for f in tb.fields if f.kind == "object" and not f.required_when)
    rr = next(f for f in tb.fields if f.required_when)
    rows.append(f"| `{ack.name}` | an object: `{ack.subkeys[0][0]}` is "
                + ", ".join(f"`{v}`" for v in TEACHBACK_VARIETY_ACK_VALID_VALUES[:-1])
                + f" or `{TEACHBACK_VARIETY_ACK_VALID_VALUES[-1]}`"
                + f"; `{ack.subkeys[1][0]}` says why unless the answer is `yes` |")
    rows.append(f"| `{rr.name}` | required when {rr.required_when}, optional below: an object of exactly "
                + _tick(k for k, _ in rr.subkeys) + ", each a non-empty string |")
    return "\n".join([
        "| Field | What to write |",
        "|---|---|",
        *rows,
        "",
        "`repact submit --teachback` refuses, listing every problem at once:",
        "- a field missing or empty, or an answer outside "
        + "/".join(TEACHBACK_VARIETY_ACK_VALID_VALUES) + ", or `concern` empty when the answer is not `yes`;",
        f"- `{rr.name}` missing at {TEACHBACK_REASONING_RECONSTRUCTION_REQUIRED_MIN}+, or with other keys "
        "or an empty value;",
        "- a key that only looks like a field (another case, a lookalike letter) or holds a control "
        "character: spell it exactly;",
        "- a `<...>` placeholder left from the skeleton;",
        f"- a teachback over {SUBMIT_TEACHBACK_MAX // 1024} KB;",
        "- a different teachback once one is accepted: go on with the work instead.",
        "",
        f"It is accepted at once when the variety total is under {TWO_PHASE_MIN} and your answer is `yes`. "
        f"Otherwise the lead reads it: end your turn on `{TEACHBACK_STOP_LINE}` (Teams mode: run the "
        "`repact-wait` line it prints).",
    ])


def render_handoff_fields() -> str:
    """teammate-handoff.md: every key, what submit repairs and what it refuses."""
    ho = PROTOCOL.handoff
    by_name = {f.name: f for f in ho.fields}
    rows = [f"| `{name}` | {_required_word(name)}: {_HANDOFF_RULES[name]} |"
            for name in HANDOFF_CANONICAL_FIELDS]
    verbs = [f"`{verb}: <{why}>`" for verb, why in zip(DISPOSITION_VERBS, ("where", "why", "why"))]
    rows.append(f"| `amendments` | one entry per `{AMEND_PREFIX}<n>` on the task, keyed by it: "
                + ", ".join(verbs) + f" or `{ACK}`; `{{}}` with none |")
    rows.append(f"| `refs` | one entry per ref or AC the skeleton lists: `{REF_VERBS[0]}: <evidence>`, or "
                + ", ".join(f"`{v}:`" for v in REF_VERBS[1:-1]) + f" or `{REF_VERBS[-1]}:` with the why |")
    rows.append("| `rev` | do not set it: submit assigns it, one more than the stored one |")
    return "\n".join([
        "| Key | Rule |",
        "|---|---|",
        *rows,
        "",
        f"`repact submit` repairs what is mechanical: {_SUBMIT_REPAIRS}. It refuses, listing every "
        "problem at once and storing nothing:",
        "- " + " or ".join(f"`{k}`" for k in SUBMIT_REQUIRED_KEYS) + " missing;",
        "- a `produced` path that resolves outside your worktree;",
        "- an `uncertainty` item with none of " + ", ".join(f"`{p}`" for p in UNCERTAINTY_PREFIXES) + ";",
        f"- an `{AMEND_PREFIX}<n>` on the task with no entry in `amendments` (it prints the amendment's text);",
        "- a listed ref or AC with no entry in `refs`, or an entry that opens with none of "
        + ", ".join(f"`{v}:`" for v in REF_VERBS) + ";",
        "- a key that only looks like a canonical one, a `<...>` placeholder left from the skeleton, "
        f"or a HANDOFF over {SUBMIT_HANDOFF_MAX // 1024} KB;",
        "- a teachback that is not accepted yet.",
    ])


def _py_lines(name: str, value) -> list:
    """`name = <value>` as Python source, one key or item per line (json literals are Python here)."""
    import json
    if isinstance(value, dict):
        body = [f"    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in value.items()]
        return [f"{name} = {{", *body, "}"]
    body = [f"    {json.dumps(item, ensure_ascii=False)}," for item in value]
    return [f"{name} = (", *body, ")"]


def handoff_skeleton_template() -> dict:
    """The HANDOFF skeleton the brief writes, without `refs` (brief.py adds one per carried ref)."""
    out = {}
    for name in HANDOFF_CANONICAL_FIELDS:
        if name in SUBMIT_REQUIRED_KEYS:
            out[name] = _SKELETON_PLACEHOLDERS[name]
        else:
            out[name] = [] if name in SUBMIT_LIST_KEYS else ""
    out["amendments"] = {}
    return out


def handoff_guide() -> tuple:
    """The brief's short HANDOFF block, one line per rule submit checks."""
    return (
        "- produced: one item per file, its path first (made absolute; it must be in your worktree).",
        "- integration: required: what the lead or the next unit must know to use this.",
        "- uncertainty: each item opens with " + ", ".join(UNCERTAINTY_PREFIXES[:-1])
        + f" or {UNCERTAINTY_PREFIXES[-1]}.",
        f"- amendments: one entry per {AMEND_PREFIX}<n> on the task ("
        + ", ".join(f"{verb}:" for verb in DISPOSITION_VERBS) + f" or {ACK}).",
        "- refs: one entry per listed ref or AC (" + ", ".join(f"{v}:" for v in REF_VERBS) + ").",
        "- Replace every <...> placeholder. Do not set rev: the command assigns it.",
    )


def render_brief_handoff() -> str:
    """hooks/shared/ops/brief.py: the HANDOFF skeleton template and the brief's guide, as Python source."""
    return "\n".join([*_py_lines("HANDOFF_SKELETON", handoff_skeleton_template()), "",
                      *_py_lines("HANDOFF_GUIDE", handoff_guide())])


def render_spawn_notes() -> str:
    """The two constants peer_context.py appends to a Teams teammate's spawn context, as Python source."""
    return "\n".join([
        "_TEACHBACK_REMINDER = (",
        '    "\\n\\nTEACHBACK TIMING: your brief (the file your spawn prompt names) holds the teachback "',
        f'    "skeleton and the exact `{SUBMIT_TEACHBACK}` line. Run it before any Edit/Write/Bash "',
        '    "for the work; reading is fine. The teachback is a gate: when submit says the lead "',
        '    "reviews it, run the `repact-wait` line it prints. The teammate skill holds the rest."',
        ")",
        "",
        "_COMPLETION_AUTHORITY_NOTE = (",
        '    "\\n\\nCOMPLETION AUTHORITY: you do NOT mark your own tasks `completed` and never write "',
        '    "the teachback or the HANDOFF with TaskUpdate. Hand in with the brief\'s `repact submit <id>` line; the "',
        '    "team-lead\'s `repact accept` transitions status to `completed`. After each submit, send the "',
        '    "team-lead one line, SET `intentional_wait{reason=' + WAIT_REASON_DEFAULT + '}` and run "',
        f'    "`{WAIT_COMMAND}` in the foreground."',
        ")",
    ])


# Region name -> (relative path from the plugin root, renderer). The ONE map the
# dev renderer and the contract test share.
REGIONS: dict[str, tuple[str, object]] = {
    "steps": ("skills/teammate/SKILL.md", render_skill_steps),
    "teachback-fields": ("skills/teammate/references/teammate-teachback.md", render_teachback_fields),
    "handoff-fields": ("skills/teammate/references/teammate-handoff.md", render_handoff_fields),
    "wait": ("skills/teammate/references/teammate-waiting.md", render_skill_wait),
    "brief-handoff": ("hooks/shared/ops/brief.py", render_brief_handoff),
    "spawn-notes": ("hooks/shared/peer_context.py", render_spawn_notes),
}

# The key names a hand copy of the contract would carry. A line outside a region
# naming CONTRACT_KEYS_MAX or more of them is a restatement, not a pointer.
CONTRACT_KEYS: tuple[str, ...] = (
    "teachback_submit", "variety_acknowledgment", "reasoning_reconstruction", "metadata.handoff",
    "intentional_wait", "covers_since", "expected_resolver", "handoff_rejection", "teachback_rejection",
)
CONTRACT_KEYS_MAX = 3
