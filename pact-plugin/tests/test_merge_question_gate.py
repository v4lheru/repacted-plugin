"""
Location: pact-plugin/tests/test_merge_question_gate.py
Summary: The pre-question check (hooks/merge_question_gate.py): an AskUserQuestion that
         names a merge-class command is refused before it is shown when no option of it
         could approve, and passes when one could. The check asks merge_guard_post's own
         mint function, writes no approval, and allows on its own error.

         Destructive verbs are assembled at runtime so this file stays inert to a live
         guard that reads the transcript.
"""

import io
import json

import pytest

import merge_guard_post
import merge_question_gate as gate
from merge_guard_post import _mint_contexts_from_bundle
from merge_guard_pre import check_merge_authorization
from merge_guard_repo_fixture import cli_repo_is_o_r  # noqa: F401  # re-export: autouse fixture (hermetic cwd + env)

MERGE = "mer" + "ge"
GLAB = "gl" + "ab"
CMD = f"{GLAB} mr {MERGE} 13 --squash"
YES = f"Yes, {MERGE}"


def _question(text, options, multi=False):
    return {"question": text, "multiSelect": multi,
            "options": [{"label": label, "description": desc} for label, desc in options]}


def _payload(*questions):
    return {"tool_name": "AskUserQuestion", "tool_input": {"questions": list(questions)}}


def _run_main(monkeypatch, capsys, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = gate.main()
    return code, capsys.readouterr()


REFUSED = {
    "label-merge-when-green": _question("Ship MR 13?", [(f"{MERGE.title()} when green", f"Runs: {CMD}"), ("Not yet", "wait")]),
    "label-merge": _question("Ship MR 13?", [(MERGE.title(), f"Runs: {CMD}"), ("No", "wait")]),
    "label-approve": _question("Ship MR 13?", [("Approve", f"Runs: {CMD}"), ("No", "wait")]),
    "label-yes-merge-plus-words": _question("Ship MR 13?", [(f"{YES} when green", f"Runs: {CMD}"), ("No", "wait")]),
    "command-only-in-question": _question(f"Run `{CMD}` now?", [("Yes please", "go"), ("No", "wait")]),
    "command-in-question-yes-label": _question(f"Run `{CMD}` now?", [("Yes", "go"), ("No", "wait")]),
    "multiselect": _question("Ship MR 13?", [(YES, f"Run `{CMD}`"), ("Not yet", "wait")], multi=True),
    # odd inputs
    "cyrillic-e-in-label": _question("Ship MR 13?", [(f"Yеs, {MERGE}", f"Run `{CMD}`"), ("No", "wait")]),
    "fullwidth-label": _question("Ship MR 13?", [("Ｙｅｓ", f"Run `{CMD}`"), ("No", "wait")]),
    "label-with-zero-width-space": _question("Ship MR 13?", [("Yes​", f"Run `{CMD}`"), ("No", "wait")]),
    "command-split-across-options": _question("Ship MR 13?", [(YES, f"Run `{GLAB} mr`"), ("Yes", f"`{MERGE} 13`"), ("No", CMD)]),
    "empty-question-text": _question("", [("Go", f"Run `{CMD}`"), ("No", "wait")]),
    "two-commands-in-one-option": _question("Ship?", [(YES, f"Run `{CMD}` and `{GLAB} mr {MERGE} 14`"), ("No", "wait")]),
}

ALLOWED = {
    "yes-merge-and-not-yet": (_question("Ship MR 13?", [(YES, f"Run `{CMD}`"), ("Not yet", "wait")]),),
    "lowercase-yes": (_question("Ship MR 13?", [("yes", f"Run `{CMD}`"), ("No", "wait")]),),
    "no-destructive-command": (_question("Run `git status` first?", [("Yes", "run it"), ("No", "skip")]),),
    "plain-question": (_question("Which colour?", [("Red", "warm"), ("Blue", "cold")]),),
    "one-of-two-command-questions-can-approve": (
        _question("Ship MR 13?", [(MERGE.title(), f"Runs: {CMD}"), ("No", "wait")]),
        _question("Ship MR 14?", [(YES, f"Run `{GLAB} mr {MERGE} 14`"), ("Not yet", "wait")]),
    ),
    "empty-question-text-approving-option": (_question("", [(YES, f"Run `{CMD}`"), ("No", "wait")]),),
}


class TestRefusedBeforeTheQuestionIsShown:
    @pytest.mark.parametrize("name", REFUSED)
    def test_no_option_could_approve(self, name):
        reason = gate.decide(_payload(REFUSED[name]))
        assert reason is not None, name
        assert "Nothing would be approved by any option" in reason
        assert merge_guard_post.APPROVAL_LABELS in reason
        assert "literal command in its description" in reason

    @pytest.mark.parametrize("name", REFUSED)
    def test_the_mint_agrees_for_every_option(self, name):
        """The refusal is true: the real mint approves nothing for any click on the bundle."""
        question = REFUSED[name]
        for option in question["options"]:
            contexts, _ = _mint_contexts_from_bundle([question], {question["question"]: option["label"]})
            assert contexts == []

    def test_deny_envelope_and_exit_code(self, monkeypatch, capsys):
        code, out = _run_main(monkeypatch, capsys, _payload(REFUSED["label-merge-when-green"]))
        assert code == 2
        decision = json.loads(out.out)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "does not read as an approval" in decision["permissionDecisionReason"]


class TestAllowed:
    @pytest.mark.parametrize("name", ALLOWED)
    def test_passes(self, name):
        assert gate.decide(_payload(*ALLOWED[name])) is None

    def test_a_passed_question_mints_only_on_the_real_click(self):
        """Passing the question approves nothing: the decline still mints no context."""
        (question,) = ALLOWED["yes-merge-and-not-yet"]
        assert _mint_contexts_from_bundle([question], {question["question"]: "Not yet"})[0] == []
        assert len(_mint_contexts_from_bundle([question], {question["question"]: YES})[0]) == 1

    def test_huge_text_is_read_without_error(self):
        filler = "lorem ipsum " * 150_000
        assert gate.decide(_payload(_question(filler, [("Yes", filler), ("No", "x")]))) is None
        assert gate.decide(_payload(_question(filler + f" `{CMD}`", [("Go", "x"), ("No", "x")]))) is not None

    def test_more_options_than_the_tool_shows_is_not_judged(self):
        options = [(f"Option {n}", f"Run `{CMD}`") for n in range(gate.MAX_OPTIONS_TRIED + 1)]
        assert gate.decide(_payload(_question("Ship?", options))) is None

    @pytest.mark.parametrize("tool_input", [None, "text", [], {}, {"questions": "x"}, {"questions": []},
                                            {"questions": [None, 7, "q", {"question": 3, "options": "x"}]}])
    def test_unreadable_payload_allows(self, tool_input):
        assert gate.decide({"tool_name": "AskUserQuestion", "tool_input": tool_input}) is None

    def test_other_tool_is_ignored(self):
        payload = _payload(REFUSED["label-merge"])
        payload["tool_name"] = "Bash"
        assert gate.decide(payload) is None


class TestItsOwnErrorApprovesNothing:
    @pytest.fixture
    def no_token_may_be_written(self, monkeypatch, tmp_path):
        def refuse(*_args, **_kwargs):
            raise AssertionError("the pre-question check must never write an approval")
        monkeypatch.setattr(merge_guard_post, "write_token", refuse)
        monkeypatch.setattr(merge_guard_post, "TOKEN_DIR", tmp_path)
        return tmp_path

    @pytest.mark.parametrize("error", [RuntimeError("boom"), SystemExit(2), RecursionError()])
    def test_an_error_allows_and_mints_nothing(self, error, monkeypatch, capsys, no_token_may_be_written):
        def broken():
            raise error
        monkeypatch.setattr(gate, "_load_mint", broken)
        code, out = _run_main(monkeypatch, capsys, _payload(*ALLOWED["yes-merge-and-not-yet"]))
        assert code == 0
        assert json.loads(out.out) == {"suppressOutput": True}
        assert list(no_token_may_be_written.iterdir()) == []
        assert check_merge_authorization(CMD, token_dir=no_token_may_be_written) is not None

    def test_stdin_that_is_not_json_allows(self, monkeypatch, capsys, no_token_may_be_written):
        monkeypatch.setattr("sys.stdin", io.StringIO("{not json"))
        assert gate.main() == 0
        assert json.loads(capsys.readouterr().out) == {"suppressOutput": True}

    @pytest.mark.parametrize("name", ["yes-merge-and-not-yet", "one-of-two-command-questions-can-approve"])
    def test_an_allowed_question_writes_no_token(self, name, monkeypatch, capsys, no_token_may_be_written):
        code, _ = _run_main(monkeypatch, capsys, _payload(*ALLOWED[name]))
        assert code == 0
        assert list(no_token_may_be_written.iterdir()) == []
        assert check_merge_authorization(CMD, token_dir=no_token_may_be_written) is not None
