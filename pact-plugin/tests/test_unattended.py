"""
Location: pact-plugin/tests/test_unattended.py
Summary: Unattended mode: the switch (hooks/shared/unattended.py read_mode), the
         classifier of AskUserQuestion calls, the gate (hooks/unattended_gate.py), the
         command (bin/repact-unattended) and the session-start block.

The classifier sits on the approval channel, so its rows are hostile on purpose: an act
hidden by case, by a look-alike or zero-width character, by spacing, or named only in an
option; odd shapes; a flag that cannot be read. Every such row must answer always-ask, or
(for the flag) OFF. Hook and command runs use a temporary config root, project directory
and session directory.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import session_init
import shared.session_init_lead as session_init_lead
from shared import unattended as u
from helpers import effective_hooks_config  # noqa: E402

PLUGIN = Path(__file__).resolve().parent.parent
HOOK = PLUGIN / "hooks" / "unattended_gate.py"
COMMAND = PLUGIN / "bin" / "repact-unattended"
SESSION = "sess-unattended"


def question(text="Which file layout fits the module?", labels=("One file (Recommended)", "Two files"),
             descriptions=None, **extra):
    options = [{"label": label, "description": (descriptions or {}).get(label, "an option")}
               for label in labels]
    return {"question": text, "header": "Layout", "options": options, **extra}


YES_NO = ("Yes (Recommended)", "No")

# --- the classifier ---------------------------------------------------------

ALWAYS_ASK_ROWS = {
    "merge": (question("Merge the branch now?", YES_NO), "act_named"),
    "accept MR into main": (question("Accept MR !42 into main now that the pipeline is green?", YES_NO), "act_named"),
    "scale to zero": (question("Scale the service to zero?", YES_NO), "act_named"),
    "postgres update": (question("Run the UPDATE on the users table in Postgres?", YES_NO), "act_named"),
    "act only in an unknown field": ({**question("Next step?", YES_NO), "note": "then merge"}, "act_named"),
    "null": (None, "not_a_question"),
    "push to main": (question("Push to main?", YES_NO), "act_named"),
    "force-push": (question("Force-push the rebased branch?", YES_NO), "act_named"),
    "deploy": (question("Deploy to the cluster?", YES_NO), "act_named"),
}


@pytest.mark.parametrize("item, code", ALWAYS_ASK_ROWS.values(), ids=ALWAYS_ASK_ROWS.keys())
def test_a_question_in_doubt_is_always_ask(item, code):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) == (u.CLASS_ALWAYS_ASK, code)


DEFAULT_ROWS = {
    "plain": question(),
    "product is not prod": question("Which name for the field?", ("product_title (Recommended)", "other")),
    "process is not pr": question("Which name for the constant?", ("PROCESS_NAME (Recommended)", "other")),
}


@pytest.mark.parametrize("item", DEFAULT_ROWS.values(), ids=DEFAULT_ROWS.keys())
def test_a_plain_question_with_one_recommended_option_is_default(item):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) == (u.CLASS_DEFAULT, "recommended")
    assert len(verdict.ref) == 12


# 1.4.0 defaulted these reversible questions; they name no local class or hold a word
# outside the local words, so 1.4.1 halts them (a false halt, by design)
FORMER_DEFAULT_ROWS = {
    "postgres is not post": question("Use postgres or sqlite for the cache?", ("postgres (Recommended)", "sqlite")),
    "process is not pr": question("Which process name fits the log line?", ("Worker (Recommended)", "Job")),
    "calendar widget": question("Use a calendar widget or a plain date input?", ("Widget (Recommended)", "Input")),
}


@pytest.mark.parametrize("item", FORMER_DEFAULT_ROWS.values(), ids=FORMER_DEFAULT_ROWS.keys())
def test_a_reversible_question_outside_the_local_class_halts(item):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) in {(u.CLASS_ALWAYS_ASK, "not_local"), (u.CLASS_ALWAYS_ASK, "no_local_class")}


def test_no_local_word_is_an_always_ask_word():
    assert [word for word in u.LOCAL_WORDS if u.names_act(word)] == []


# 1.4.1 inversion: only a question shown to be local and reversible takes the default.
# review9's #55 probe set (36 outward phrasings, 21 defaulted under 1.4.0) is not on disk;
# these 36 are written anew: chat and email, tickets, PRs and MRs, deploys, deletes,
# payments, account changes, and indirect phrasings.
OUTWARD_PROBES = (
    "Ping the client",
    "Bring the fix onto the integration line?",
    "Tear down the staging stack?",
    "Do the thing from yesterday's call?",
)


@pytest.mark.parametrize("text", OUTWARD_PROBES)
def test_an_outward_phrasing_without_a_listed_word_halts(text):
    for item in (question(text, YES_NO), question(text, ("Go ahead (Recommended)", "Wait"))):
        assert u.classify_question(item).cls == u.CLASS_ALWAYS_ASK


LOCAL_ROWS = {
    "name a helper": question("Which name for the new helper function?",
                              ("parse_window (Recommended)", "window_parser"), header="Naming"),
    "fixture folder": question("Which folder for the test fixtures?",
                               ("tests/fixtures (Recommended)", "tests/data"), header="Layout"),
}


@pytest.mark.parametrize("item", LOCAL_ROWS.values(), ids=LOCAL_ROWS.keys())
def test_a_local_question_takes_the_default(item):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) == (u.CLASS_DEFAULT, "recommended")


NOT_LOCAL_ROWS = {
    "unmatched": (question("Which colour for the banner?", ("Blue (Recommended)", "Red")), "no_local_class"),
    "scripts in the test folder": (question("Run the scripts in the test folder?", YES_NO), "not_local"),
    "unknown option field": (
        {**question("Which name for the helper?", YES_NO),
         "options": [{"label": "Yes (Recommended)", "extra": "x"}, {"label": "No"}]}, "not_local"),
}


@pytest.mark.parametrize("item, code", NOT_LOCAL_ROWS.values(), ids=NOT_LOCAL_ROWS.keys())
def test_a_question_not_shown_local_halts(item, code):
    verdict = u.classify_question(item)
    assert (verdict.cls, verdict.code) == (u.CLASS_ALWAYS_ASK, code)


@pytest.mark.parametrize("item, code", [
    (question("Which n\u0430me for the helper?", YES_NO), "non_ascii"),
], ids=[
    "cyrillic a in name",
])
def test_the_local_check_at_its_boundary(item, code):
    verdict = u.classify_question(item)
    assert (verdict.code if verdict.cls == u.CLASS_ALWAYS_ASK else verdict.cls) == code


def classes(tool_input):
    return [(item.index, item.cls, item.code) for item in u.classify_call(tool_input)]


def test_a_call_gets_one_verdict_per_question_and_the_worst_names_the_row():
    verdicts = u.classify_call({"questions": [question(), question("Merge it?", YES_NO), question()]})
    assert [(v.index, v.cls) for v in verdicts] == [
        (1, u.CLASS_DEFAULT), (2, u.CLASS_ALWAYS_ASK), (3, u.CLASS_DEFAULT)]
    assert u.worst(verdicts).code == "act_named"


@pytest.mark.parametrize("tool_input, expected", [
    (None, [(1, "always_ask", "questions_missing")]),
], ids=[
    "null",
])
def test_an_odd_call_never_answers_default_for_the_odd_part(tool_input, expected):
    assert classes(tool_input) == expected


def test_more_questions_than_the_tool_takes_are_all_always_ask():
    verdicts = u.classify_call({"questions": [question() for _ in range(40)]})
    assert len(verdicts) == 16
    assert {(v.cls, v.code) for v in verdicts} == {(u.CLASS_ALWAYS_ASK, "too_many")}


def test_deep_nesting_does_not_raise_and_is_always_ask():
    nested = "x"
    for _ in range(5000):
        nested = [nested]
    assert classes({"questions": [{**question(), "extra": nested}]}) == [(1, "always_ask", "too_long")]


def test_a_check_that_raises_answers_always_ask(monkeypatch):
    monkeypatch.setattr(u, "classify_question", lambda *_: 1 / 0)
    assert classes({"questions": [question()]}) == [(1, "always_ask", "check_error")]


# --- the switch -------------------------------------------------------------

def flag(folder: Path, body) -> None:
    (folder / u.FLAG_NAME).write_bytes(body if isinstance(body, bytes) else json.dumps(body).encode())


def test_the_flag_wins_over_the_environment_and_no_flag_leaves_it_to_the_environment(tmp_path):
    assert u.read_mode(str(tmp_path), {}) == (False, "unset")
    assert u.read_mode(str(tmp_path), {u.ENV_NAME: "1"}) == (True, "env")
    assert u.read_mode(str(tmp_path), {u.ENV_NAME: "0"}) == (False, "unset")
    assert u.read_mode("", {u.ENV_NAME: "1"}) == (True, "env")
    u.write_flag(str(tmp_path), False, "2026-10-03T00:00:00+00:00")
    assert u.read_mode(str(tmp_path), {u.ENV_NAME: "1"}) == (False, "flag")
    u.write_flag(str(tmp_path), True, "2026-10-03T00:00:00+00:00")
    assert u.read_mode(str(tmp_path), {}) == (True, "flag")
    assert os.stat(tmp_path / u.FLAG_NAME).st_mode & 0o777 == 0o600


@pytest.mark.parametrize("body", [
    b"",
], ids=[
    "empty",
])
def test_a_malformed_flag_reads_off_even_when_the_environment_says_on(tmp_path, body):
    flag(tmp_path, body)
    assert u.read_mode(str(tmp_path), {u.ENV_NAME: "1"}) == (False, "flag_malformed")


def test_a_flag_that_is_not_a_readable_file_reads_off(tmp_path):
    directory, link, locked = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    for folder in (directory, link, locked):
        folder.mkdir()
    (directory / u.FLAG_NAME).mkdir()
    flag(tmp_path, {"unattended": True})
    (link / u.FLAG_NAME).symlink_to(tmp_path / u.FLAG_NAME)
    flag(locked, {"unattended": True})
    (locked / u.FLAG_NAME).chmod(0)
    cases = [directory, link] + ([locked] if os.geteuid() != 0 else [])
    for folder in cases:
        assert u.read_mode(str(folder), {u.ENV_NAME: "1"}) == (False, "flag_unreadable"), folder.name


# --- the gate ---------------------------------------------------------------

@pytest.fixture
def root(tmp_path):
    """A temporary home, config root and project; returns (env, session directory)."""
    project = tmp_path / "proj"
    project.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key != u.ENV_NAME and not key.startswith("CLAUDE_")}
    env.update(HOME=str(tmp_path), CLAUDE_CONFIG_DIR=str(tmp_path / "config"),
               CLAUDE_PROJECT_DIR=str(project))
    return env, tmp_path / "config" / "pact-sessions" / "proj" / SESSION


def run_gate(env, tool_input, *, raw=None, tool_name="AskUserQuestion", **extra_env):
    payload = raw if raw is not None else json.dumps(
        {"tool_name": tool_name, "session_id": SESSION, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, text=True,
                          capture_output=True, env={**env, **extra_env}, timeout=60)


def journal(session_dir: Path) -> list[dict]:
    path = session_dir / "session-journal.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_off_allows_the_question_and_writes_nothing(root):
    env, session_dir = root
    done = run_gate(env, {"questions": [question("Merge it?", YES_NO)]})
    assert (done.returncode, json.loads(done.stdout)) == (0, {"suppressOutput": True})
    assert not session_dir.exists()


def test_on_refuses_with_one_verdict_per_question_and_journals_no_question_text(root):
    env, session_dir = root
    done = run_gate(env, {"questions": [question(), question("Merge the zebra branch?", YES_NO)]},
                    REPACT_UNATTENDED="1")
    assert done.returncode == 2
    envelope = json.loads(done.stdout)["hookSpecificOutput"]
    assert envelope["hookEventName"] == "PreToolUse" and envelope["permissionDecision"] == "deny"
    reason = envelope["permissionDecisionReason"]
    assert reason in done.stderr
    assert "q1 DEFAULT" in reason and "q2 ALWAYS-ASK (act_named)" in reason
    assert "telegram_notify" in reason and "telegram_ask" not in reason
    assert "zebra" not in reason and "layout" not in reason
    rows = journal(session_dir)
    assert [row["type"] for row in rows] == ["gate_refusal"]
    row = rows[0]
    assert (row["hook"], row["verdict"], row["rule"], row["class"]) == (
        "unattended_gate", "deny", "act_named", "always_ask")
    assert [(item["class"], item["code"]) for item in row["questions"]] == [
        ("default", "recommended"), ("always_ask", "act_named")]
    text = (session_dir / "session-journal.jsonl").read_text()
    assert "zebra" not in text and "layout" not in text.lower() and "Recommended" not in text
    # a refused question leaves nothing an approval guard could read: one file, the journal
    assert [path.name for path in Path(env["CLAUDE_CONFIG_DIR"]).rglob("*") if path.is_file()] == [
        "session-journal.jsonl"]


def test_a_question_refused_as_always_ask_stays_always_ask_with_new_options(root):
    env, session_dir = root
    first = run_gate(env, {"questions": [question(labels=("Two", "One"))]}, REPACT_UNATTENDED="1")
    again = run_gate(env, {"questions": [question()]}, REPACT_UNATTENDED="1")
    assert "ALWAYS-ASK (no_recommended)" in first.stderr
    assert "ALWAYS-ASK (asked_before)" in again.stderr and "DEFAULT" not in again.stderr
    assert journal(session_dir)[-1]["rule"] == "asked_before"


@pytest.mark.parametrize("raw", [
    "",
],
                         ids=[
    "empty",
])
def test_stdin_that_is_not_a_json_object_exits_0_without_a_decision(root, raw):
    env, session_dir = root
    done = run_gate(env, None, raw=raw, REPACT_UNATTENDED="1")
    assert done.returncode == 0 and "permissionDecision" not in done.stdout
    assert not session_dir.exists()


def test_another_tool_is_allowed_while_on(root):
    env, _ = root
    done = run_gate(env, {"command": "git merge"}, tool_name="Bash", REPACT_UNATTENDED="1")
    assert (done.returncode, json.loads(done.stdout)) == (0, {"suppressOutput": True})


def test_a_malformed_flag_allows_the_question_and_says_so_in_the_journal(root):
    env, session_dir = root
    session_dir.mkdir(parents=True)
    flag(session_dir, b"{broken")
    done = run_gate(env, {"questions": [question()]}, REPACT_UNATTENDED="1")
    assert (done.returncode, json.loads(done.stdout)) == (0, {"suppressOutput": True})
    assert [(row["type"], row["state"], row["cause"]) for row in journal(session_dir)] == [
        ("unattended_mode", "off", "flag_malformed")]


def imported_modules(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_the_gate_and_the_mode_import_no_approval_guard_and_no_way_out():
    """They cannot mint an approval or reach a network, a model or a subprocess: they
    import neither the merge guard nor anything that could."""
    assert imported_modules(PLUGIN / "hooks" / "shared" / "unattended.py") == {
        "__future__", "hashlib", "json", "os", "re", "stat", "tempfile", "unicodedata", "typing"}
    assert imported_modules(HOOK) == {
        "__future__", "json", "os", "shlex", "sys", "pathlib",
        "shared", "shared.pact_context", "shared.session_journal"}


def test_the_gate_is_registered_once_on_the_question_tool_and_is_synchronous():
    config = effective_hooks_config()
    found = [(event, entry.get("matcher"), hook.get("async", False))
             for event, entries in config["hooks"].items() for entry in entries
             for hook in entry.get("hooks", [])
             if hook.get("command", "").endswith('/hooks/unattended_gate.py"')]
    assert found == [("PreToolUse", "AskUserQuestion", False)]


# --- the command ------------------------------------------------------------

def run_command(env, session_dir, *args, **extra_env):
    return subprocess.run([sys.executable, str(COMMAND), *args, "--session-dir", str(session_dir)],
                          text=True, capture_output=True, env={**env, **extra_env}, timeout=60)


def test_the_command_switches_records_and_lists(root):
    env, session_dir = root
    session_dir.mkdir(parents=True)
    refused_off = run_command(env, session_dir, "halt", "--task", "35", "--question", "Merge it")
    assert refused_off.returncode == 3 and "OFF" in refused_off.stderr

    on = run_command(env, session_dir, "on")
    assert on.returncode == 0 and on.stdout.startswith("unattended: ON (flag)")
    assert "UNATTENDED MODE is ON" in on.stdout

    gate = run_gate(env, {"questions": [question(), question("Merge it?", YES_NO),
                                        question("Which colour?", ("Blue (Recommended)", "Red"))]})
    assert gate.returncode == 2
    row = [row for row in journal(session_dir) if row["type"] == "gate_refusal"][0]
    default_ref, merge_ref, open_ref = (item["ref"] for item in row["questions"])

    record = ("--question", "Which layout", "--chose", "Two columns (Recommended)", "--why", "the plan")
    assert run_command(env, session_dir, "default", "--ref", "0" * 12, *record).returncode == 3
    on_always_ask = run_command(env, session_dir, "default", "--ref", merge_ref, *record)
    assert on_always_ask.returncode == 3 and "always-ask" in on_always_ask.stderr
    assert run_command(env, session_dir, "default", *record).returncode == 2
    assert run_command(env, session_dir, "default", "--ref", default_ref, *record).returncode == 0
    assert run_command(env, session_dir, "halt", "--ref", merge_ref, "--task", "35",
                       "--question", "Merge\nit\x1b[31m now").returncode == 0

    listed = run_command(env, session_dir, "list")
    assert listed.returncode == 0
    lines = listed.stdout.splitlines()
    assert lines[0] == "unattended: ON (flag); defaults 1, halts 1, questions refused 3"
    assert any("Which layout -> Two columns (Recommended); why: the plan" in line for line in lines)
    assert any("task 35: Merge it [31m now" in line for line in lines)
    assert any("not recorded: 1 refused question(s)" in line and open_ref in line for line in lines)

    # off is the owner's at their own terminal: no terminal here, so refused
    off = run_command(env, session_dir, "off", REPACT_UNATTENDED="1")
    assert off.returncode == 3 and "[rule: unattended.off_owner_only]" in off.stderr
    assert u.read_mode(str(session_dir)).on and u.controls_on(str(session_dir)).on
    assert [(row["state"], row["cause"]) for row in journal(session_dir)
            if row["type"] == "unattended_mode"] == [("on", "command")]


def test_the_command_needs_a_session_directory(root, tmp_path):
    env, _ = root
    assert run_command(env, tmp_path / "missing", "status").returncode == 4
    bare = subprocess.run([sys.executable, str(COMMAND), "status"], text=True,
                          capture_output=True, env=env, timeout=60)
    assert bare.returncode == 4 and "--session-dir" in bare.stderr


# --- the session-start block --------------------------------------------------

def test_session_start_prints_the_rules_only_while_on_also_after_a_compaction(tmp_path, monkeypatch):
    monkeypatch.delenv(u.ENV_NAME, raising=False)
    monkeypatch.setattr(session_init_lead, "reconstruct_session_dir", lambda *_: str(tmp_path))
    rows = []
    monkeypatch.setattr(session_init_lead, "append_event", lambda event, session_dir=None: rows.append(event))

    assert session_init.unattended_context(SESSION, "/proj", "startup") is None
    assert rows == []

    u.write_flag(str(tmp_path), True, "2026-10-03T00:00:00+00:00")
    for source in ("startup", "resume", "compact", "clear"):
        block = session_init.unattended_context(SESSION, "/proj", source)
        assert block.startswith("UNATTENDED MODE is ON (set by the session flag)")
        assert "AskUserQuestion" in block and "telegram_notify" in block and "never" in block
    assert rows == []

    flag(tmp_path, b"{broken")
    monkeypatch.setenv(u.ENV_NAME, "1")
    assert session_init.unattended_context(SESSION, "/proj", "compact") is None
    assert [(row["type"], row["state"], row["cause"]) for row in rows] == [
        ("unattended_mode", "off", "flag_malformed")]

    (tmp_path / u.FLAG_NAME).unlink()
    rows.clear()
    assert "set by the environment" in session_init.unattended_context(SESSION, "/proj", "startup")
    assert "set by the environment" in session_init.unattended_context(SESSION, "/proj", "compact")
    assert [(row["state"], row["cause"]) for row in rows] == [("on", "env")]
