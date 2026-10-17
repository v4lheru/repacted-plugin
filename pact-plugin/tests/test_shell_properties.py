"""tests/test_shell_properties.py

The five property tests of the shell lane (refactor §3.9): stdlib random.Random(seed), 500
cases each, the failing command and seed in the assertion message. No Hypothesis.
  P1  words(cmd) == shlex.split(cmd) on the shlex-compatible grammar
  P2  read_script raises only Unreadable and returns quickly on 20 KB inputs
  P3  mask_quotes preserves the length and every unquoted operator position
  P4  (env) replacing any single-quoted inert span by `x` never changes the verdict
  P5  (merge) a danger literal inside a masked span never flips is_dangerous_command unless
      the span reaches a shell or the leg is not positively data (fail closed, §3.7 As
      built); outside a masked span it always does
"""
from __future__ import annotations

import random
import re
import shlex
import time

import pytest

import env_guard as guard
from shell.merge import classify
from shell.words import Unreadable, mask_quotes, read_script, words

CASES = 500
SEEDS = [1, 2, 3]
TAIL = re.compile(r" \[rule: ([a-z_]+(?:\.[a-z_]+)+)\]$")
M = "gh pr " + "merge 5"
PLAIN = ["a", "b7", "file.txt", "--flag", "-n", "x=y", "path/to/f", "1", "@", "%s", "..", "tests/x_y.py"]
OPERATORS = ["&&", "||", ";", "|", "&"]


def _word(rng: random.Random) -> str:
    kind = rng.randrange(5)
    if kind == 0:
        return "'" + " ".join(rng.choices(PLAIN + ["$x", "*", "|", ";"], k=rng.randrange(1, 4))) + "'"
    if kind == 1:
        return '"' + " ".join(rng.choices(PLAIN + ["*", "|", ";"], k=rng.randrange(1, 4))) + '"'
    if kind == 2:
        return "a\\ b" if rng.random() < 0.5 else "\\$x"
    return rng.choice(PLAIN)


def _command(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randrange(1, 4)):
        parts.append(" ".join(_word(rng) for _ in range(rng.randrange(1, 5))))
        parts.append(" " + rng.choice(OPERATORS) + " ")
    return "".join(parts[:-1])


@pytest.mark.parametrize("seed", SEEDS)
def test_p1_words_agree_with_shlex_on_the_shared_grammar(seed):
    rng = random.Random(seed)
    for _ in range(CASES):
        command = _command(rng)
        assert words(command) == shlex.split(command), (seed, command)


@pytest.mark.parametrize("seed", SEEDS)
def test_p2_read_script_raises_only_unreadable_and_returns_quickly(seed):
    rng = random.Random(seed)
    for _ in range(20):
        pieces = []
        while sum(map(len, pieces)) < 20_000:
            pieces.append(_command(rng) + rng.choice(["\n", "\n", " <<'EOF'\nbody\nEOF\n", " $(", " `", " '", "\\\n"]))
        text = "".join(pieces)
        started = time.perf_counter()
        try:
            read_script(text)
        except Unreadable:
            pass
        except Exception as error:  # noqa: BLE001 — the property is the exception class
            raise AssertionError((seed, type(error).__name__, text[:120])) from error
        assert time.perf_counter() - started < 0.5, (seed, len(text))


@pytest.mark.parametrize("seed", SEEDS)
def test_p3_mask_quotes_keeps_the_length_and_every_unquoted_operator(seed):
    rng = random.Random(seed)
    for _ in range(CASES):
        command = _command(rng)
        masked = mask_quotes(command)
        assert len(masked) == len(command), (seed, command)
        spans = []
        quote = ""
        for i, char in enumerate(command):      # the unquoted operator positions, read plainly
            if quote:
                if char == quote:
                    quote = ""
                continue
            if char in "'\"":
                quote = char
                continue
            if char in "|;&":
                spans.append(i)
        for i in spans:
            assert masked[i] == command[i], (seed, command, i)


# ── P4: the env guard and inert single-quoted spans ───────────────────────────────────────

# Heads whose single-quoted arguments are text they print, match or hand to a program as data:
# never a path the guard reads. A fixed deny-shaped leg is appended to half the cases, so the
# property covers both verdicts: an inert span neither masks nor makes a refusal.
ENV_HEADS = ["echo", "printf '%s'", "python3 tool.py", "jq -n", "awk", "sed -n", "git commit -m",
             "echo -n", "grep -n -e"]
ENV_TAILS = ["", "", " ; cat .env", " && printenv", " ; grep -r KEY ."]


def _env_command(rng: random.Random) -> tuple[str, str]:
    """(a command with single-quoted inert spans, the same with each span replaced by 'x')."""
    legs, flat = [], []
    for _ in range(rng.randrange(1, 4)):
        head = rng.choice(ENV_HEADS)
        spans = ["'" + " ".join(rng.choices(PLAIN + ["$HOME", "*", "|"], k=rng.randrange(1, 4))) + "'"
                 for _ in range(rng.randrange(0, 3))]
        legs.append(" ".join([head, *spans]))
        flat.append(" ".join([head, *(["'x'"] * len(spans))]))
    operator = rng.choice(["&&", ";", "|"])
    tail = rng.choice(ENV_TAILS)
    return f" {operator} ".join(legs) + tail, f" {operator} ".join(flat) + tail


def _env_verdict(command: str, cwd: str) -> tuple[bool, str | None]:
    output = guard.decide({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd})
    if not output or output["hookSpecificOutput"].get("permissionDecision") != "deny":
        return False, None
    match = TAIL.search(output["hookSpecificOutput"]["permissionDecisionReason"])
    return True, match.group(1) if match else None


@pytest.mark.parametrize("seed", SEEDS)
def test_p4_an_inert_single_quoted_span_never_changes_the_env_verdict(seed, tmp_path):
    (tmp_path / ".env").write_text("K=inert-marker-5e1d\n", encoding="utf-8")
    (tmp_path / "tool.py").write_text("pass\n", encoding="utf-8")
    rng = random.Random(seed)
    for _ in range(CASES // 5):
        command, flat = _env_command(rng)
        assert _env_verdict(command, str(tmp_path)) == _env_verdict(flat, str(tmp_path)), (seed, command)


# ── P5: mask soundness on the merge guard ─────────────────────────────────────────────────

MASKED_TEMPLATES = [                       # (template with {span}, carrier)
    ("jq -n '{span}'", 11),
    ("awk '{span}' f", 11),
    ("grep -e \"{span}\" log.txt", 11),
    ("printf '%s\\n' \"{span}\"", 11),
    ("sed -n '{span}' f", 11),
    ("python3 tool.py '{{\"cmd\":\"{span}\"}}'", 12),
    ("node tool.js '[\"{span}\"]'", 12),
    ("echo '{span}' | python3 tool.py", 13),
    ("cat <<'EOF' | python3 tool.py\n{span}\nEOF", 13),
]
DANGERS = [M, "git push --" + "force origin main", "git branch -D feat", "gh pr " + "close 5"]
# §3.7 As built: carriers 11-12 fail closed, so a leg is masked only when it is positively
# data. For awk that is the documented allowlist, restated here apart from strip.py: the
# program is an argv word, so one opening with `-` is an option awk reads, and `system(`, a
# pipe, getline or a gawk `@name` call can run a command. Such a leg is checked whole.
_AWK_RUNS = re.compile(r"system\s*\(|\||getline|@\s*[A-Za-z_]")


def _positively_data(template: str, span: str) -> bool:
    if not template.startswith("awk "):
        return True
    return not span.startswith("-") and not _AWK_RUNS.search(span)


@pytest.mark.parametrize("seed", SEEDS)
def test_p5_a_danger_literal_inside_a_masked_span_never_fires_unless_it_reaches_a_shell(seed):
    """Never fires unless the span reaches a shell or the leg is not positively data; a leg
    not positively data is kept whole and its literal fires (fail closed)."""
    rng = random.Random(seed)
    for _ in range(CASES // 3):
        template, _carrier = rng.choice(MASKED_TEMPLATES)
        danger = rng.choice(DANGERS)
        filler = " ".join(rng.choices(PLAIN, k=rng.randrange(0, 3)))
        span = f"{filler} {danger} {filler}".strip()
        inert = template.format(span=span)
        data = _positively_data(template, span)
        assert classify.is_dangerous_command(inert) is not data, (seed, inert, data)
        to_shell = inert + " | sh"
        assert classify.is_dangerous_command(to_shell), (seed, to_shell)
        outside = inert + " && " + danger
        assert classify.is_dangerous_command(outside), (seed, outside)
