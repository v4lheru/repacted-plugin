"""Contract 8, CARRIERS AGREE: a rule stated in several files says the same thing in each.

Rule homes (contract 6) pins a phrase in one file; it cannot see two files disagree. Seven findings
of one field report were that: a default of three reviewers in one file and one in another. This
check reads the CLAIM of eight rules from the home and from every line that states the rule, in every
markdown file under agents/, commands/, protocols/, reference/ and skills/ and in the user-layer
core, and fails when a carrier differs from the home:

  reviewer   the default number of reviewers, and when several run in parallel.
             Home: the persona's §11 Lean defaults.
  agents     a fresh agent per task, and how many agents run at once.
             Home: the persona's §11 Lean defaults.
  threshold  the variety bands. Home: hooks/shared/variety_scorer.py, imported, never copied.
             The single-task threshold is COMPACT_MAX and the plan-mode threshold PLAN_MODE_MIN.
  approval   the lead asks for approval of an irreversible act only through AskUserQuestion, and
             the list of those acts. Home: the persona's §3 Integrity row. The act list is checked
             on plugin files only: the user-layer core keeps its own, longer list.
  stop       an agent is stopped once its task is accepted, and a reviewer with open findings
             stays until they are verified. Home: the persona's §11. The secretary, and the
             commands that stop everyone at the end of a session, are outside this rule.
  wait_timeout   repact-wait runs in the foreground under the Bash timeout, and how many exit-3
             runs precede ending the turn. Home: the teammate skill's §Waiting and waking.
  heavy_compute  the worker cap and the niceness of heavy local compute.
             Home: the lead playbook's shell rule.
  tests_lean the regression tests per bug, and which code gets adversarial tests.
             Home: the teammate skill's HANDOFF rule (the persona states the high-risk half
             only and sits at its byte ceiling). test_lean_defaults.py pins nothing this reads.

A line that states a rule in words the check cannot read is reported, never skipped. A line that
points at the home and states nothing is not a claim. PENDING names the carriers that disagree in
a file another unit owns; an entry that no longer disagrees fails, so the list cannot go stale.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from shared.variety_scorer import (COMPACT_MAX, MAX_SCORE, MIN_SCORE, ORCHESTRATE_MAX,
                                   PLAN_MODE_MAX, PLAN_MODE_MIN)
from tests.contract._surfaces import PLUGIN_ROOT

SCOPE = ("agents", "commands", "protocols", "reference", "skills")
PERSONA = "agents/pact-orchestrator.md"
USER_CORE = "user-layer/CLAUDE.core.md"
RULES = ("reviewer", "agents", "threshold", "approval", "stop", "wait_timeout", "heavy_compute", "tests_lean")
TEAMMATE_SKILL, LEAD_PLAYBOOK = "skills/teammate/SKILL.md", "skills/pact-lead-playbook/SKILL.md"
HOMES = {"wait_timeout": TEAMMATE_SKILL, "heavy_compute": LEAD_PLAYBOOK, "tests_lean": TEAMMATE_SKILL}   # else PERSONA
UNREADABLE = "states the rule in words this check cannot read"

# Carriers in files this unit may not edit: (file, rule) -> (owner, the wording it needs).
PENDING: dict[tuple[str, str], tuple[str, str]] = {}

NUMBER = {"a": 1, "one": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5}
POINTER = re.compile(r"§\s?(?:3|11)\b|Lean defaults|Integrity row|#s5-escalation|approval rule in the core|core(?:'s)? (?:Safety )?rule")


def _number(word: str) -> int | None:
    return int(word) if word.isdigit() else NUMBER.get(word.lower())


def _plain(line: str) -> str:
    return line.replace("*", "").replace("`", "")


# --- reviewer ---------------------------------------------------------------------------------
REVIEWER_STATES = re.compile(r"(?i)\breviewers?\b[^.;:]{0,20}\bdefault\b|\bdefault\b[^.;:]{0,20}\breviewers?\b"
                             r"|\bin parallel only when\b|\bconcurrent reviewers\b")
REVIEWER_COUNT = re.compile(r"(?i)\b(\w+) reviewers?\b[^.;:]{0,20}\bdefault\b|\bdefault\b[^.;:]{0,20}?\b(\w+) reviewers?\b")
SEVERAL_WHEN = re.compile(r"(?i)in parallel only when ([^:.;(]+)")
RISK_FLOOR = re.compile(r"(?i)\brisk(?: score)?(?: is)?\s*(?:≥|>=)?\s*(\d)\b")


def read_reviewer(line: str) -> dict | None:
    if "reviewer" not in line.lower() or not REVIEWER_STATES.search(line):
        return None
    claim = {}
    for match in REVIEWER_COUNT.finditer(line):
        count = _number(match.group(1) or match.group(2))
        if count is not None:
            claim["default reviewers"] = count
    when = SEVERAL_WHEN.search(line)
    if when:
        risk = RISK_FLOOR.search(when.group(1))
        asked = "user asks" in when.group(1).lower()
        if risk or asked:
            claim["several in parallel when"] = (("the user asks",) if asked else ()) + \
                                                ((f"risk >= {risk.group(1)}",) if risk else ())
        else:
            claim[UNREADABLE] = True
    return claim


# --- agents -----------------------------------------------------------------------------------
AGENT_CAP = re.compile(r"(?i)\bat most (\w+) (?:agents|teammates|specialists)\b|\bat most (\w+) at once\b"
                       r"|\bcap \((\w+) at once\)|\b(\w+) (?:agents|teammates|specialists|coders) at once\b")
CAP_UNLESS = re.compile(r"(?i)\bat once,? unless ([^.;:,)]+)")
PARALLEL_EXAMPLE = re.compile(r"(?i)= (\d+) coders\b")
FRESH = re.compile(r"(?i)\bfresh agent (?:per|for each) task\b")
REUSE_DEFAULT = re.compile(r"(?i)\breus\w+ [^.;]{0,60}\b(?:by default|is the default)\b")


def read_agents(line: str) -> dict | None:
    caps = [m for m in AGENT_CAP.finditer(line)]
    examples = PARALLEL_EXAMPLE.findall(line)
    fresh, reuse = FRESH.search(line), REUSE_DEFAULT.search(line)
    if not (caps or examples or fresh or reuse):
        return None
    claim = {}
    for match in caps:
        count = _number(next(group for group in match.groups() if group))
        if count is not None:
            claim["agents at once"] = count
    for example in examples:
        claim["agents at once, by example"] = int(example)
    unless = CAP_UNLESS.search(line)
    if unless and "agents at once" in claim:
        clause = unless.group(1).strip().lower()
        claim["more than the cap when"] = "the user asks" if re.match(r"(the user asks|asked)\b", clause) else clause
    if fresh or reuse:
        claim["agent per task"] = "reuse" if reuse and not fresh else "fresh"
    return claim


# --- threshold --------------------------------------------------------------------------------
FLOORS = (COMPACT_MAX + 1, PLAN_MODE_MIN, PLAN_MODE_MAX + 1)
CEILINGS = (COMPACT_MAX, ORCHESTRATE_MAX, PLAN_MODE_MAX)
SPANS = {(lo, hi) for lo in (MIN_SCORE, *FLOORS) for hi in (*CEILINGS, MAX_SCORE) if lo < hi}
VARIETY_CONTEXT = re.compile(r"(?i)\bvariety\b|\bcomplexity (?:total|score)\b")
_NUM = r"(?<![\w.,#/-])(\d{1,2})(?![\w.,%/])"
BAND_RANGE = re.compile(r"(?<![\w.,#/-])(\d{1,2})\s?[-–]\s?(\d{1,2})(?![\w.,%/-])")
BAND_FLOOR = re.compile(rf"(?:>=|≥)\s?{_NUM}|(?<![\w.,#/-])(\d{{1,2}})\+(?![\w])|{_NUM} (?:and|or) (?:above|more)\b|\b(?:below|under) {_NUM}")
BAND_CEILING = re.compile(rf"(?:<=|≤)\s?{_NUM}|{_NUM} or (?:less|below|fewer)\b")
SINGLE_TASK = re.compile(r"(?i)\bcomplexity total(?: is)? (\w+) or less\b")
PLAN_MODE_FIRST = re.compile(r"(?i)plan-mode\W{0,3}first at (\w+)\+?")
NEAR = 48   # a band number counts when `variety` or `complexity total` stands this close to it


def _near_context(line: str, start: int, end: int) -> bool:
    return bool(VARIETY_CONTEXT.search(line[max(0, start - NEAR):end + NEAR]))


def read_threshold(line: str) -> dict | None:
    named = SINGLE_TASK.search(line), PLAN_MODE_FIRST.search(line)
    if not (VARIETY_CONTEXT.search(line) or any(named)):
        return None
    claim = {}
    for label, match in zip(("single-task threshold", "plan-mode threshold"), named):
        if match:
            number = _number(match.group(1))
            claim[label if number is not None else UNREADABLE] = number if number is not None else True
    taken = [match.span() for match in named if match]   # a named claim is reported once, not as a band too

    def counts(match: re.Match, *numbers: int) -> bool:
        return all(MIN_SCORE <= n <= MAX_SCORE for n in numbers) and _near_context(line, *match.span()) \
            and not any(start < match.end() and match.start() < end for start, end in taken)

    for match in BAND_RANGE.finditer(line):
        lo, hi = int(match.group(1)), int(match.group(2))
        if lo < hi and counts(match, lo, hi):
            claim.setdefault("variety bands", []).append((lo, hi))
    for kind, pattern in (("band floors", BAND_FLOOR), ("band ceilings", BAND_CEILING)):
        for match in pattern.finditer(line):
            number = int(next(group for group in match.groups() if group))
            if counts(match, number):
                claim.setdefault(kind, []).append(number)
    return claim or None


def threshold_disagreements(claim: dict) -> list[str]:
    wrong = []
    if claim.get("single-task threshold", COMPACT_MAX) != COMPACT_MAX:
        wrong.append(f"single-task threshold {claim['single-task threshold']}, variety_scorer.py says {COMPACT_MAX}")
    if claim.get("plan-mode threshold", PLAN_MODE_MIN) != PLAN_MODE_MIN:
        wrong.append(f"plan-mode threshold {claim['plan-mode threshold']}, variety_scorer.py says {PLAN_MODE_MIN}")
    wrong += [f"variety band {lo}-{hi} is no band of variety_scorer.py" for lo, hi in claim.get("variety bands", ())
              if (lo, hi) not in SPANS]
    wrong += [f"{n} is no band floor of variety_scorer.py {FLOORS}" for n in claim.get("band floors", ()) if n not in FLOORS]
    wrong += [f"{n} is no band ceiling of variety_scorer.py {CEILINGS}" for n in claim.get("band ceilings", ())
              if n not in CEILINGS]
    return wrong


# --- approval ---------------------------------------------------------------------------------
ACTS = {"merge": re.compile(r"(?i)\bmerg(?:e|es|ed|ing)\b"),
        "force push": re.compile(r"(?i)\bforce[- ]push"),
        "branch deletion": re.compile(r"(?i)\bbranch delet\w+|\bdelet\w+ (?:the |a )?(?:\w+ )?branch\b"),
        "PR close": re.compile(r"(?i)\bPR close\b|\bclos\w+ (?:the |a )?PRs?\b")}
APPROVAL_WORD = re.compile(r"(?i)\bapprov\w*|\bauthoriz\w*|\buser-gated\b")
ASKS = re.compile(r"(?i)\b(?:ask|request|re-request)\w*\b[^.;,]{0,40}\b(?:approv|authoriz)\w*"
                  r"|\b(?:approv|authoriz)\w*\b[^.;,]{0,25}\b(?:ask|request)\w*")
OTHER_CHANNEL = re.compile(r"(?i)\bplain text\b|\bin (?:the )?chat\b|\btelegram_ask\b|\bSendMessage\b")
NEGATED = re.compile(r"(?i)\b(?:never|not|no|don't|cannot|without)\b[^.;]{0,80}$")
USER_WORDS = re.compile(r"(?i)\bown-turn\b|\bexplicit (?:user )?(?:intent|authorization)\b|\bstated intent\b|\buser's words\b")
ACT_LIST_MIN = 3   # a line naming this many of the acts beside an approval word states the list


def read_approval(line: str, user_layer: bool = False, never_cell: str = "") -> dict | None:
    acts = tuple(name for name, pattern in ACTS.items() if pattern.search(line))
    if not acts or not APPROVAL_WORD.search(line):
        return None
    claim = {}
    if len(acts) >= ACT_LIST_MIN and not user_layer:
        claim["irreversible acts"] = acts
    others = [m.group(0).lower() for m in OTHER_CHANNEL.finditer(line)
              if m.group(0) not in never_cell and not NEGATED.search(line[:m.start()])]
    if "AskUserQuestion" in line or others:
        claim["asks through"] = tuple(sorted({"AskUserQuestion"} & {t for t in ("AskUserQuestion",) if t in line}
                                             | set(others)))
    elif ASKS.search(line) and not USER_WORDS.search(line) and not POINTER.search(line):
        claim[UNREADABLE] = True
    return claim or None


def read_home_approval(text: str) -> dict:
    """The Integrity row: what the lead must never do (cell 2) and what approves (cell 3)."""
    rows = [line for line in text.splitlines() if line.startswith("| **Integrity** |")]
    assert len(rows) == 1, "the persona has no single Integrity row"
    _, _, never, always, *_ = (cell.strip() for cell in rows[0].split("|"))
    assert "plain text" in never and "own-turn order approves" in always, rows[0]
    claim = read_approval(_plain(always), never_cell=never)
    assert claim and set(claim) == {"irreversible acts", "asks through"}, claim
    return claim


# --- stop -------------------------------------------------------------------------------------
TEAMMATE = re.compile(r"(?i)\b(?:agent|teammate|specialist|reviewer|coder)s?\b")
STOP_ONCE = re.compile(r"(?i)\bstop\w*\b[^.;|]{0,60}\bonce (?:its|their) tasks? (?:is|are) accepted\b")
KEPT = re.compile(r"(?i)\b(?:persists?|remains?|stays?) (?:alive|until|as consultants?)\b|\bkeep\w* (?:\w+ )?alive\b"
                  r"|\bkept alive\b|\bdo not (?:shut ?down|stop)\b|\bno preemptive shutdown\b")
STOP_WORD = re.compile(r"(?i)\b(?:shut ?down|stop)(?:s|ped|ping)?\s[^.;|]{0,60}\b(?:after|until|when|once)\b")
UNTIL = re.compile(r"(?i)\buntil ([^.;|,()]+)")
ONCE_ACCEPTED, UNTIL_VERIFIED = "once its task is accepted", "stays until they are verified"
# Not this rule: the secretary serves the session, and pause, refresh and wrap-up stop everyone.
SESSION_END = re.compile(r"(?i)\bsecretary\b|\bowns the teammate shutdown\b|/repacted:(?:pause|refresh|wrap-up)\b"
                         r"|\bby name\b|\bconsolidation\b|\bend-session\b")


def read_stop(line: str) -> dict | None:
    if not TEAMMATE.search(line):
        return None
    claim = {}
    for clause in re.split(r"[.;|]", line):
        if SESSION_END.search(clause):
            continue
        if STOP_ONCE.search(clause):
            claim["an agent is stopped"] = ONCE_ACCEPTED
        kept = KEPT.search(clause)
        if kept and "open findings" in clause.lower():
            until = UNTIL.search(clause)
            verified = not until or until.group(1).strip().lower() in ("they are verified", "its findings are verified")
            claim["a reviewer with open findings"] = UNTIL_VERIFIED if verified else f"stays until {until.group(1).strip()}"
        elif kept:
            claim["an agent is stopped"] = f"not then ({kept.group(0).lower()})"
        elif STOP_WORD.search(clause) and TEAMMATE.search(clause) and not STOP_ONCE.search(clause):
            claim[UNREADABLE] = True
    return claim or None


# --- wait_timeout -----------------------------------------------------------------------------
WAIT_CONTEXT = re.compile(r"(?i)\brepact-wait\b|\bBash (?:tool )?timeout\b|\bin a row, end the turn\b")
TIMEOUT_MS = re.compile(r"(?i)\btimeout \(?(?:declared max )?(\d{6,7})\b")
RUNS_IN = re.compile(r"(?i)\b(foreground|background)\b")
EXIT3_RUNS = re.compile(r"(?i)\bafter (\w+) in a row, end the turn\b|\bon the (\w+), end the turn\b")
ORDINAL = {"second": 2, "third": 3, "fourth": 4}


def read_wait_timeout(line: str) -> dict | None:
    if not WAIT_CONTEXT.search(line):
        return None
    claim = {}
    timeout = TIMEOUT_MS.search(line)
    if timeout:
        claim["bash timeout ms"] = int(timeout.group(1))
    for match in RUNS_IN.finditer(line):
        if match.group(1).lower() == "foreground" or not NEGATED.search(line[:match.start()]):
            claim["runs in"] = match.group(1).lower()
            break
    runs = EXIT3_RUNS.search(line)
    if runs:
        word = next(group for group in runs.groups() if group)
        count = _number(word) if _number(word) is not None else ORDINAL.get(word.lower())
        claim["exit-3 runs before ending the turn" if count is not None else UNREADABLE] = count or True
    return claim or None


# --- heavy_compute ----------------------------------------------------------------------------
HEAVY_CONTEXT = re.compile(r"(?i)\bnice(?:ness| -n)? \d|\bheavy (?:local )?compute\b")
WORKERS = re.compile(r"(?i)\b(\w+)[- ](?:parallel )?workers?\b")
NICENESS = re.compile(r"(?i)\bnice(?: -n)? (\d+)\b|\bniceness (\d+)\b")


def read_heavy_compute(line: str) -> dict | None:
    if not HEAVY_CONTEXT.search(line):
        return None
    claim = {}
    counts = [_number(m.group(1)) for m in WORKERS.finditer(line) if _number(m.group(1)) is not None]
    if counts:
        claim["workers"] = counts[0]
    nice = NICENESS.search(line)
    if nice:
        claim["niceness"] = int(next(group for group in nice.groups() if group))
    return claim or None


# --- tests_lean -------------------------------------------------------------------------------
REGRESSION_PER_BUG = re.compile(r"(?i)\b(\w+) regression tests? per (?:real )?bug\b")
ADVERSARIAL = re.compile(r"(?i)\badversarial (?:boundary )?tests\b")
HIGH_RISK = re.compile(r"(?i)\bhigh[- ]risk\b|\bsecurity (?:code|concern)\b")


def read_tests_lean(line: str) -> dict | None:
    claim = {}
    regression = REGRESSION_PER_BUG.search(line)
    if regression:
        count = _number(regression.group(1))
        claim["regression tests per bug" if count is not None else UNREADABLE] = count or True
    if ADVERSARIAL.search(line):
        if HIGH_RISK.search(line):
            claim["adversarial tests for"] = "high-risk code"
        else:
            claim[UNREADABLE] = True
    return claim or None


READERS = {"reviewer": read_reviewer, "agents": read_agents, "threshold": read_threshold, "approval": read_approval,
           "stop": read_stop, "wait_timeout": read_wait_timeout, "heavy_compute": read_heavy_compute,
           "tests_lean": read_tests_lean}
HOME_FACETS = {"reviewer": {"default reviewers", "several in parallel when"},
               "agents": {"agents at once", "more than the cap when", "agent per task"},
               "threshold": {"single-task threshold", "plan-mode threshold"},
               "stop": {"an agent is stopped", "a reviewer with open findings"},
               "wait_timeout": {"bash timeout ms", "runs in", "exit-3 runs before ending the turn"},
               "heavy_compute": {"workers", "niceness"},
               "tests_lean": {"regression tests per bug", "adversarial tests for"}}


def carrier_files(root: Path, user_core: Path | None) -> list[tuple[str, Path]]:
    found = [(p.relative_to(root).as_posix(), p) for d in SCOPE for p in sorted((root / d).rglob("*.md"))]
    return found + ([(USER_CORE, user_core)] if user_core and user_core.is_file() else [])


def home_claims(root: Path) -> dict[str, dict]:
    """Each rule's claim as its home states it; a rule the home states twice must agree with itself."""
    texts = {name: (root / name).read_text(encoding="utf-8") for name in {PERSONA, *HOMES.values()}}
    claims: dict[str, dict] = {rule: {} for rule in HOME_FACETS}
    for rule, facets in HOME_FACETS.items():
        home = HOMES.get(rule, PERSONA)
        for line in texts[home].splitlines():
            for facet, value in (READERS[rule](_plain(line)) or {}).items():
                if facet in facets:
                    assert claims[rule].setdefault(facet, value) == value, f"{home} states {facet} twice, differently"
        assert set(claims[rule]) == facets, f"{home} does not state {sorted(facets - set(claims[rule]))}"
    claims["approval"] = read_home_approval(texts[PERSONA])
    return claims


def differences(rule: str, claim: dict, home: dict) -> list[str]:
    if UNREADABLE in claim:
        return [UNREADABLE]
    if rule == "threshold":
        return threshold_disagreements(claim)
    wrong = []
    for facet, value in claim.items():
        if facet == "agents at once, by example":
            if value > home["agents at once"]:
                wrong.append(f"an example of {value} at once, the home says at most {home['agents at once']}")
        elif facet == "asks through":
            if set(value) - set(home[facet]):
                wrong.append(f"asks through {', '.join(value)}, the home says {', '.join(home[facet])} only")
        elif set(value) != set(home[facet]) if isinstance(value, tuple) else value != home[facet]:
            wrong.append(f"{facet}: {value!r}, the home says {home[facet]!r}")
    return wrong


def scan(root: Path = PLUGIN_ROOT, user_core: Path | None = None,
         pending: dict | None = None) -> tuple[list[str], Counter]:
    """(carriers that differ from the home or cannot be read, claims read per rule)."""
    pending = PENDING if pending is None else pending
    home = home_claims(root)
    problems, checked, excused = [], Counter(), set()
    for name, path in carrier_files(root, user_core):
        for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = _plain(raw)
            for rule in RULES:
                if name == PERSONA and rule == "approval" and raw.startswith("| **Integrity** |"):
                    checked[rule] += 1
                    continue
                claim = read_approval(line, name == USER_CORE) if rule == "approval" else READERS[rule](line)
                if not claim:
                    continue
                checked[rule] += 1
                checked[rule, name] += 1
                for wrong in differences(rule, claim, home.get(rule, {})):
                    if (name, rule) in pending:
                        excused.add((name, rule))
                    else:
                        problems.append(f"{name}:{number}: {rule}: {wrong}")
    for key in sorted(set(pending) - excused):
        problems.append(f"PENDING {key[0]} ({key[1]}) no longer disagrees: remove the entry")
    return problems, checked




# --- fixtures ---------------------------------------------------------------------------------
HOME = "\n".join([
    "| **Integrity** | Fabricate user input, ask approval in plain text | Only the user's own-turn order approves "
    "a merge, force push, branch deletion, PR close; else `AskUserQuestion` |",
    "A dispatch whose complexity total is 6 or less is single-task dispatch. A FRESH agent per task; at most 2 "
    "agents at once unless the user asks for more. ONE reviewer by default; several in parallel only when the "
    "user asks or risk ≥ 3.",
    "comPACT: variety 4-6; orchestrate: variety 7+, `/repacted:plan-mode` first at 11+.",
    "Stop each agent once its task is accepted and nothing of its awaits verification: a reviewer with open "
    "findings stays until they are verified; fixes go to the code's author if still running, else a fresh coder.",
])
TEAMMATE_HOME = "\n".join([
    "6. Stay in the turn: run `repact-wait <task id>` in the FOREGROUND (Bash timeout 600000). Exit 3: again; "
    "on the third, end the turn (the wait stays).",
    "Tests are lean. One regression test per real bug or behaviour change. High-risk code (security, "
    "validation/parsers, shared contracts) always gets adversarial boundary tests.",
])
PLAYBOOK_HOME = "Heavy compute uses at most 2 workers and `nice -n 19` (D11).\n"
HOME_TEXTS = {PERSONA: HOME, TEAMMATE_SKILL: TEAMMATE_HOME, LEAD_PLAYBOOK: PLAYBOOK_HOME}
AGREEING = "\n".join([
    "Dispatch **ONE reviewer** by default. Dispatch several in parallel only when the user asks or the risk score is 3 or more: then three types.",
    "The default is one reviewer. The reviewer default is in the persona's §11 Lean defaults. Do NOT stop a reviewer with open findings.",
    "A fresh agent per task is the default; the agent cap (2 at once) holds, and \"fix 2 bugs\" = 2 coders.",
    "At most 2 at once unless asked. Mandatory at 3+ parallel coders and at variety >= 7.",
    "Low 4-6, Medium 7-10, High 11-14, Extreme 15-16; variety 4-10 skips plan-mode; variety below 7 skips the auditor.",
    "Small tasks (complexity total 6 or less): the work task alone, with its stamp and its owner, claimed by the teammate, and its HANDOFF may cite lines 4-12 of a file or 9+ rows.",
    "Merge is irreversible: always use `AskUserQuestion` to request merge authorization, never plain text.",
    "Irreversible actions (merge, force push, branch deletion, PR close) need the user's explicit authorization; ask via `AskUserQuestion`.",
    "Stop each specialist once its task is accepted. Ask for merge approval as the persona's §3 Integrity row says.",
    "Run `repact-wait` in the foreground (Bash timeout 600000); after 3 in a row, end the turn.",
    "Heavy local compute: at most 2 workers and `nice -n 19`; tcount caps pytest at 2 workers at niceness 19.",
    "One regression test per bug; adversarial boundary tests for high-risk code only.",
])
PLANTED_LINE = AGREEING.count("\n") + 2   # the planted line follows the agreeing ones
PLANTED = [
    ("Dispatch three reviewers by default.", "reviewer: default reviewers: 3"),
    ("ONE reviewer by default; several in parallel only when the user asks or risk ≥ 2.", "reviewer: several in parallel when"),
    ("Several reviewers in parallel only when it seems wise.", f"reviewer: {UNREADABLE}"),
    ("At most 3 agents at once unless the user asks.", "agents: agents at once: 3"),
    ("At most 2 agents at once unless the lead sees fit.", "agents: more than the cap when"),
    ('Parallel is mandatory ("fix 3 bugs" = 3 coders).', "agents: an example of 3 at once"),
    ("Reuse an idle teammate by default.", "agents: agent per task: 'reuse'"),
    ("A dispatch whose complexity total is 7 or less is single-task dispatch.", "threshold: single-task threshold 7"),
    ("A dispatch whose complexity total is small or less is single-task dispatch.", f"threshold: {UNREADABLE}"),
    ("Run `/repacted:plan-mode` first at 12+.", "threshold: plan-mode threshold 12"),
    ("High variety is 12-14 and needs a plan.", "threshold: variety band 12-14"),
    ("Method reconstruction is required at variety 12+.", "threshold: 12 is no band floor"),
    ("Ask in the chat for merge authorization.", "approval: asks through in the chat"),
    ("Use telegram_ask to request approval to merge.", "approval: asks through telegram_ask"),
    ("Ask the user to approve the merge.", f"approval: {UNREADABLE}"),
    ("Irreversible actions (merge, force push, branch deletion) need authorization.", "approval: irreversible acts"),
    ("Teammates persist until PR merge.", "stop: an agent is stopped: 'not then (persist until)'"),
    ("Completed-phase teammates remain as consultants.", "stop: an agent is stopped: 'not then (remain as consultants)'"),
    ("A reviewer with open findings stays until the merge.", "stop: a reviewer with open findings: 'stays until the merge'"),
    ("Shut down the reviewers after the user's merge decision.", f"stop: {UNREADABLE}"),
    ("Run `repact-wait` under a Bash timeout 300000.", "wait_timeout: bash timeout ms: 300000"),
    ("Run `repact-wait` in the background and end the turn.", "wait_timeout: runs in: 'background'"),
    ("Exit 3: after 5 in a row, end the turn.", "wait_timeout: exit-3 runs before ending the turn: 5"),
    ("Heavy compute: at most 4 workers and `nice -n 19`.", "heavy_compute: workers: 4"),
    ("Heavy compute: at most 2 workers and `nice -n 10`.", "heavy_compute: niceness: 10"),
    ("Write two regression tests per bug.", "tests_lean: regression tests per bug: 2"),
    ("Adversarial boundary tests for every module.", f"tests_lean: {UNREADABLE}"),
]


def _tree(root: Path, carrier: str, core: str = "") -> tuple[Path, Path]:
    for rel_path, body in {**HOME_TEXTS, "commands/peer-review.md": carrier, "core.md": core}.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return root, root / "core.md"


def test_the_scan_passes_a_tree_whose_carriers_agree(tmp_path):
    core = "Without stated intent, ask (AskUserQuestion) before push to master, merge, force-push, branch delete, deploy."
    problems, checked = scan(*_tree(tmp_path, AGREEING, core), pending={})
    assert problems == []
    assert all(checked[rule] >= 2 for rule in RULES), dict(checked)


def test_the_scan_reports_each_planted_contradiction_and_each_unreadable_carrier(tmp_path):
    for number, (line, report) in enumerate(PLANTED):
        problems, _ = scan(*_tree(tmp_path / str(number), AGREEING + "\n" + line), pending={})
        assert len(problems) == 1 and problems[0].startswith(f"commands/peer-review.md:{PLANTED_LINE}: " + report), (line, problems)


def test_a_core_that_asks_through_another_channel_is_reported_but_its_longer_act_list_is_not(tmp_path):
    problems, _ = scan(*_tree(tmp_path, AGREEING, "Ask in the chat before a merge, force-push, branch delete or deploy approval."),
                       pending={})
    assert [p.split(": ", 1)[1] for p in problems] == ["approval: asks through in the chat, the home says AskUserQuestion only"]


def test_a_pending_carrier_is_excused_until_it_agrees(tmp_path):
    root, core = _tree(tmp_path, AGREEING)
    owned = "commands/wrap-up.md"
    entry = {(owned, "reviewer"): ("wrap1", "ONE reviewer by default")}
    (root / owned).write_text("# Wrap-up\n", encoding="utf-8")
    assert scan(root, core, pending=entry)[0] == [f"PENDING {owned} (reviewer) no longer disagrees: remove the entry"]
    (root / owned).write_text("Dispatch three reviewers by default.\n", encoding="utf-8")
    assert scan(root, core, pending=entry)[0] == []
    assert len(scan(root, core, pending={})[0]) == 1


def test_the_thresholds_come_from_the_scorer_and_the_home_states_them(tmp_path):
    assert (COMPACT_MAX, PLAN_MODE_MIN) == (6, ORCHESTRATE_MAX + 1)
    assert (MIN_SCORE, COMPACT_MAX) in SPANS and (PLAN_MODE_MIN, PLAN_MODE_MAX) in SPANS and (12, 14) not in SPANS
    root, core = _tree(tmp_path, AGREEING)
    (root / PERSONA).write_text(HOME.replace("at most 2 agents at once unless the user asks for more. ", ""), encoding="utf-8")
    try:
        scan(root, core, pending={})
    except AssertionError as error:
        assert "does not state" in str(error) and "agents at once" in str(error)
    else:
        raise AssertionError("a home that drops a claim must fail")
