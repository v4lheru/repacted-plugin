"""
Location: pact-plugin/tests/test_merge_guard_150_canonical.py
Summary: 1.5.0: the merge guard reads a word the shell decodes to plain text (`"pu"'sh'`,
         `$'\\x70ush'`, `$"push"`, `pu\\sh`) in its canonical spelling, so each gated command
         gates in every such spelling exactly as in its plain form: push, the push-option
         merge, branch -D, push --delete, push :branch, gh pr merge, glab mr merge, a quoted
         -C directory, and the same inside $(..), backticks, bash -c and eval. The respelling
         never touches a word whose quotes change what the shell reads (glob, tilde, `=`,
         reserved words, heredoc delimiters, assignments). An approval minted on a quoted
         spelling authorizes that command and no other.
"""
import subprocess
import time

import pytest

import merge_guard_pre as guard
import shared.merge_guard_common as mgc
from merge_guard_post import _mint_context_from_bundle, write_token
from project_tasks_support import GIT_ENV
from shared.merge_guard_common import canonical_spelling, detect_command_operation_type


def _gated(command, tmp_path):
    return guard.check_merge_authorization(command, token_dir=tmp_path / "tokens",
                                           cwd=str(tmp_path)) is not None


# (plain form, spellings the shell reads as the same command)
SPELLINGS = [
    ("git push --force origin main", [
        "git \"pu\"'sh' --force origin main", "git $'\\x70ush' --force origin main",
        "git $\"push\" --force origin main", "git pu\\sh --force origin main",
        "git push $'--force' origin \"main\"", "\"git\" push --force origin main"]),
    ("git push -o merge_request.merge_when_pipeline_succeeds origin feature", [
        "git 'push' -o merge_request.merge_when_pipeline_succeeds origin feature",
        "git push '-o' 'merge_request.merge_when_pipeline_succeeds' origin feature"]),
    ("git branch -D feature", [
        "git 'branch' -D feature", "git br\"anch\" -D feature", "git $'branch' $'-D' feature"]),
    ("git push origin --delete feature", [
        "git 'push' origin --delete feature", "git push origin $'--delete' feature",
        "git $\"push\" origin --delete feature"]),
    ("git push origin :feature", [
        "git 'push' origin :feature", "git $\"push\" origin ':feature'", "git pu\\sh origin :feature"]),
    ("gh pr merge 5", ["gh 'pr' merge 5", "gh pr $'merge' 5", "\"gh\" pr merge 5", "gh pr 'merge' '5'"]),
    ("glab mr merge 5", ["glab mr 'merge' 5", "glab $\"mr\" merge 5", "glab 'mr' 'merge' 5"]),
    ("git -C /tmp/x push --force origin main", [
        "git '-C' /tmp/x 'push' --force origin main", "git -C '/tmp/x' 'push' --force origin main",
        "git -C \"/tmp/a b\" 'push' --force origin main"]),
    ("echo $(git push --force origin main)", [
        "echo $(git 'push' --force origin main)", "echo \"$(git \"pu\"sh --force origin main)\"",
        "echo `git 'push' --force origin main`", "echo $(echo $(git 'pu'sh --force origin main))"]),
    ("bash -c 'git push --force origin main'", [
        "bash -c 'git \"pu\"sh --force origin main'", "bash -c \"git 'push' --force origin main\"",
        "sh -lc \"git \\\"push\\\" --force origin main\""]),
    ("eval 'git push --force origin main'", [
        "eval \"git 'push' --force origin main\"", "eval git 'push' --force origin main"]),
    ("cd /x && git push --force origin main", ["cd /x && git 'push' --force origin main"]),
]


@pytest.mark.parametrize("plain, spelling", [(p, s) for p, spellings in SPELLINGS for s in spellings])
def test_a_decoded_spelling_gates_like_its_plain_form(plain, spelling, tmp_path):
    assert _gated(plain, tmp_path)
    assert _gated(spelling, tmp_path)
    assert detect_command_operation_type(canonical_spelling(spelling)) == \
        detect_command_operation_type(plain)


@pytest.mark.parametrize("command", [
    "echo '~' '*' '?' '[a]' '{a,b}' '=ls' '!x' '#x' 'a b' '' '$x'",
    "'if' true; 'then' x",
    "cat <<'EOF'\ngit push --force origin main\nEOF",
    "cat <<-\"EOF\"\nx\nEOF",
    "A'=1' git push",
    "echo '2'>out",                                # a quoted digit before > is not an fd
    "git commit -m \"don't push\"",
    "git push origin main # it's fine",            # a comment's apostrophe: unreadable, as typed
    "git 'push origin main",                       # an open quote
    "git 'рush' --force origin main",              # Cyrillic er: not plain ASCII
    "git 'ｐｕｓｈ' --force origin main",             # fullwidth
    "git \"pu\x00sh\" --force origin main",        # a raw NUL stays inside the word
])
def test_a_word_whose_quotes_matter_keeps_them(command):
    assert canonical_spelling(command) == command


def test_an_assignment_typed_plainly_is_respelled_only_in_its_value():
    assert canonical_spelling("FOO='bar' git 'push'") == "FOO=bar git push"


@pytest.mark.parametrize("look_alike", ["git рush --force origin main", "git ｐｕｓｈ --force origin main"])
def test_a_unicode_look_alike_reads_as_its_unquoted_self(look_alike, tmp_path):
    quoted = look_alike.replace(look_alike.split()[1], "'%s'" % look_alike.split()[1])
    assert _gated(quoted, tmp_path) == _gated(look_alike, tmp_path)


def test_a_nul_in_a_dollar_quote_ends_the_word_as_bash_does(tmp_path):
    assert canonical_spelling("git $'pu\\x00sh' --force origin main") == "git pu --force origin main"
    assert _gated("git $'pu\\x00sh' --force origin main", tmp_path) == \
        _gated("git pu --force origin main", tmp_path)


def test_a_one_megabyte_quoted_command_is_read_in_time(tmp_path):
    """The read is linear: four times the text takes about four times as long (a quadratic read
    takes sixteen). The ratio holds on any machine; the absolute cap only stops a hang (a 2026
    laptop reads the megabyte in 4-6 s, a shared CI runner in about 14)."""
    def timed(words):
        command = "git \"pu\"sh --force origin main " + "'a' " * words
        started = time.monotonic()
        assert canonical_spelling(command).startswith("git push --force origin main a a ")
        assert _gated(command, tmp_path)
        return time.monotonic() - started
    quarter, full = timed(62_500), timed(250_000)
    assert full < 8 * quarter and full < 60, (quarter, full)


def test_a_long_command_is_refused_the_same_with_and_without_kept_readings(tmp_path, monkeypatch):
    # The readers keep what they read of a long text (views._read_long_once, _LONG_TEXT):
    # the refusal and its rule id are those of a check that reads everything afresh.
    import shell.merge.views as views
    keeps_from = views._LONG_TEXT
    pad = "'a' " * 20_000
    gated = []
    for command in ("git \"pu\"sh --force origin main " + pad,
                    "echo " + pad + "; sh -c 'git push --force origin main'",
                    "echo " + pad + "; git status"):
        def refusal():
            return guard.check_merge_authorization(command, token_dir=tmp_path / "tokens",
                                                   cwd=str(tmp_path))
        monkeypatch.setattr(views, "_LONG_TEXT", len(command) + 1)
        afresh = refusal()
        monkeypatch.setattr(views, "_LONG_TEXT", keeps_from)
        assert [refusal(), refusal()] == [afresh, afresh]
        gated.append(afresh is not None)
    assert gated == [True, True, False]


# --- an approval minted on a quoted spelling binds that command -------------------------

def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", "remote.git")
    _git(tmp_path, "init", "-q", "-b", "main", "work")
    work = tmp_path / "work"
    _git(work, "commit", "-q", "--allow-empty", "-m", "root")
    _git(work, "remote", "add", "origin", str(tmp_path / "remote.git"))
    mgc.set_invocation_cwd(str(work))
    yield tmp_path
    mgc.set_invocation_cwd(None)


def test_an_approval_of_a_quoted_spelling_authorizes_that_command_and_no_other(repo):
    quoted = "git \"pu\"'sh' --force origin main"
    question = {"question": "Proceed?", "multiSelect": False,
                "options": [{"label": "Yes", "description": "Run `%s` now" % quoted}]}
    context, refusal = _mint_context_from_bundle([question], {"Proceed?": "Yes"})
    assert context is not None, refusal
    tokens = repo / "tokens"
    tokens.mkdir()
    assert write_token(context, token_dir=tokens)
    work = str(repo / "work")

    def decide(command):
        return guard.check_merge_authorization(command, token_dir=tokens, cwd=work)

    assert decide("git \"pu\"'sh' --force origin dev") is not None          # another branch
    assert decide("git $'pu'sh --force upstream main") is not None          # another remote
    assert decide("git 'push' origin --delete main") is not None            # another operation
    assert decide("git \"pu\"'sh' --force --no-verify origin main") is not None  # another flag
    assert decide(quoted) is None                                           # the approved one
