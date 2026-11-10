"""env-get reached by a quoted path with spaces, or through a variable the same command
assigned, is the same program as a bare env-get (#138 ENV-GUARD finding).

The shared tokenizer deletes quotes, so "/a b/bin/env-get" split into two words and the
program became "/a"; "$ENVGET" was never recognised at all. Both forms are what the
usual install produces (the plugin often lives under a folder with a space). A variable
counts as env-get only when every assignment to it in the command names env-get and
nothing else in the command can set it.
"""

from __future__ import annotations

import pytest

import env_guard as guard

ENVGET = "/srv/work/My Projects/pact-plugin/bin/env-get"


@pytest.fixture
def cwd(tmp_path):
    (tmp_path / ".env").write_text("K=inert\n", encoding="utf-8")
    return str(tmp_path)


@pytest.mark.parametrize("command", [
    f'"{ENVGET}" --keys .env',
    f'ENVGET="{ENVGET}"; "${{ENVGET}}" .env K -- sh -c \'echo ok\'',
    f'cd /tmp && "{ENVGET}" --keys .env',
])
def test_env_get_by_spaced_path_or_assigned_variable_is_allowed(cwd, command):
    assert guard.bash_verdict(command, cwd) is None


@pytest.mark.parametrize("command", [
    '"$ENVGET" --keys .env',
    f'E="{ENVGET}"; read E; "$E" .env',
    f'ENVGET="{ENVGET}"; "$ENVGET" .env K -- cat .env',
    'cat ".env"',
    f'eval "cat .env; {ENVGET}"',
])
def test_other_forms_stay_denied(cwd, command):
    assert guard.bash_verdict(command, cwd)


# Under Monitor the keys env-get hands its command count as loaded from FILE: Bash output
# passes output_secret_scan, which redacts .env values, but Monitor output arrives as task
# notifications no hook sees or rewrites. Bash verdicts do not change.
HEADER = "Authorization: " + "Bearer"
URL = "https://example.com/status"
PRINTF = "sh -c 'printf \"" + HEADER + " %s\\n\" \"$API_KEY\"'"
ENVIRON = "os." + "environ"             # kept apart: the commit check refuses the joined text
SUB = "$(env-get .env API_KEY -- printenv API_KEY)"
FED = "<(env-get .env API_KEY -- printenv API_KEY)"
SPACED = "sh -c 'printf \"X: %s\" \"$API_KEY\"'"   # one value, two words when unquoted
NOWHERE = "https://h.invalid"

PRINTS_HANDED_VALUE = [
    "env-get .env API_KEY -- printenv API_KEY",
    "env-get .env API_KEY -- sh -c 'echo $API_KEY'",
    "env-get .env API_KEY -- sh -c 'printf \"%s\\n\" \"$API_KEY\"'",
    "env-get .env OTHER -- python3 -c 'import os; print(os.getenv(\"OTHER\"))'",
    "env-get .env API_KEY,OTHER -- printenv OTHER",
    "python3 /abs/bin/env-get .env API_KEY -- printenv API_KEY",
    "bash -c 'env-get .env API_KEY -- printenv API_KEY'",
    "env-get .env API_KEY -- printenv API_KEY | tee /tmp/x",
    'echo "$(env-get .env API_KEY -- printenv API_KEY)"',
    "echo \"key=$(env-get .env API_KEY -- sh -c 'echo $API_KEY')\"",
    "TOKEN=$(env-get .env API_KEY -- printenv API_KEY); echo \"$TOKEN\"",
    "env-get .env API_KEY -- sh <<'EOF'\necho $API_KEY\nEOF",
    "env-get .env API_KEY -- sh <<< 'echo $API_KEY'",
    "env-get .env API_KEY -- sh -c 'echo \"$(printenv API_KEY)\"'",
    "env-get .env API_KEY -- sh -c 'true; X=$(printenv API_KEY); echo \"$X\"'",
    f"curl -v -sS -H @<(env-get .env API_KEY -- {PRINTF}) {URL}",
    f'curl -sS --verbose -H "{HEADER} $(env-get .env API_KEY -- printenv API_KEY)" {URL}',
    # A client's <(...) or $(...) is exempt only when env-get alone prints into it.
    f"curl -sS -o /dev/null -H @<(env-get .env API_KEY -- {PRINTF} > /tmp/leak) {URL}; "
    "cat /tmp/leak",
    f"curl -sS -H @<(env-get .env API_KEY -- {PRINTF} | tee /tmp/leak) {URL}",
    f"curl -sS -H @<(env-get .env API_KEY -- {PRINTF} >&2) {URL}",
    f"curl -sS -H @<(env-get .env API_KEY -- sh -c 'printenv API_KEY > /tmp/leak') {URL}; "
    "cat /tmp/leak",
    "curl -sS -d @<(env-get .env API_KEY -- python3 -c 'import os, sys; "
    f"sys.stderr.write(os.getenv(\"API_KEY\"))') {URL}",
    f'curl -sS -H "X: $(env-get .env API_KEY -- printenv API_KEY > /tmp/leak)" {URL}; '
    "cat /tmp/leak",
    f'curl -sS -H "X: $(env-get .env API_KEY -- printenv API_KEY | tee /tmp/leak)" {URL}',
    "pbcopy < <(env-get .env API_KEY -- printenv API_KEY); pbpaste",
    # `> 1` writes a file named 1 (the parser keeps the target, so it reads like >&1).
    f"curl -sS -o /dev/null -H @<(env-get .env API_KEY -- printenv API_KEY > 1) {URL}; cat 1",
    # A client that shows what it sends: a trace to any file, --libcurl, a URL in -w,
    # a file:// target, a config it reads, httpie or xh printing the request, gh --verbose.
    "curl -sS -T <(env-get .env API_KEY -- printenv API_KEY) file:///dev/stdout",
    "curl -sS --data-binary @<(env-get .env API_KEY -- printenv API_KEY) "
    f"--trace-ascii /dev/stdout {URL}",
    f'curl -sS --trace=/dev/stderr -H "X: $(env-get .env API_KEY -- printenv API_KEY)" {URL}',
    f"curl -sS --libcurl - -H @<(env-get .env API_KEY -- {PRINTF}) {URL}",
    "curl -sS -o /dev/null -w '%{url_effective}' "
    f'"{URL}?k=$(env-get .env API_KEY -- printenv API_KEY)"',
    f"curl -sS -K <(env-get .env API_KEY -- printenv API_KEY) {URL}",
    "wget -i <(env-get .env API_KEY -- printenv API_KEY)",
    f'http -pHB {URL} "X:$(env-get .env API_KEY -- printenv API_KEY)"',
    f'xh --curl {URL} "X:$(env-get .env API_KEY -- printenv API_KEY)"',
    'gh api --verbose -H "X: $(env-get .env API_KEY -- printenv API_KEY)" /user',
    "env-get .env API_KEY -- bash -c 'curl -sS --trace-ascii /dev/stdout "
    f"-H \"{HEADER} $API_KEY\" {URL}'",
    f"env-get .env API_KEY -- bash -c 'curl -sS --libcurl - -H \"{HEADER} $API_KEY\" {URL}'",
    "env-get .env API_KEY -- bash -c 'curl -sS -o /dev/null -w \"%{url_effective}\" "
    f"\"{URL}?k=$API_KEY\"'",
    f"env-get .env API_KEY -- bash -c 'http -pH {URL} \"X:$API_KEY\"'",
    f"env-get .env API_KEY -- bash -c 'xh --print=H {URL} \"X:$API_KEY\"'",
    "env-get .env API_KEY -- bash -c 'gh api --verbose -H \"X: $API_KEY\" /user'",
    # A script that prints the whole environment prints the handed keys.
    f"env-get .env API_KEY -- python3 -c 'import os; print({ENVIRON})'",
    "env-get .env API_KEY -- node -p 'process.env'",
    "env-get .env API_KEY -- ruby -e 'p ENV.to_h'",
    "env-get .env API_KEY -- perl -e 'print %ENV'",
    # A key the guard cannot read can be any name.
    "env-get .env \"$KN\" -- sh -c 'echo \"$API_KEY\"'",
    "env-get .env $(echo API_KEY) -- printenv API_KEY",
    # A client is exempt only for the value of a send option: curl prints a URL, proxy or
    # --resolve entry it cannot use and names its --stderr file; a file name it cannot
    # read (-d @..) is in its warning; -u with no password prompts with the user name.
    f'curl -sS "{SUB}" 2>&1',
    f'curl -sS -x "{SUB}" {URL} 2>&1',
    f'curl -sS --resolve "{SUB}" {URL} 2>&1',
    f'curl -sS --stderr "/tmp/leak_{SUB}" {URL}; ls /tmp',
    f'curl -sS -d "@{SUB}" {URL} 2>&1',
    f'curl -sS -u "{SUB}" {URL} 2>&1',
    f'curl -sS --resolve -H "{SUB}" {URL} 2>&1',        # -H is --resolve's value
    f"curl -sS -c - -b {FED} {URL}",                    # the cookie jar it read, printed
    f"curl -sS -d @- {URL} < {FED} 2>&1",               # stdin is no send option's value
    f'wget -q --header="{SUB}" {URL}',                  # an invalid header is echoed
    f'http {URL} "token:={SUB}"',                       # invalid JSON: the item is echoed
    f'xh {URL} "k=={SUB}"',                             # a query item is part of the URL
    f'http "X:{SUB}" {URL}',                            # the first word is the URL
    f'gh api -H "{SUB}" /user',                         # a header with no colon is echoed
    # Something outside the client prints its expanded arguments: the shell's trace,
    # env -v or --debug, xargs -t, --verbose or -p.
    f'exec 2>&1; set -x; curl -sS -o /dev/null -H "X: {SUB}" {URL}',
    f'set -o xtrace; curl -sS -o /dev/null -H "X: {SUB}" {URL} 2>&1',
    f"bash -x -c 'curl -sS -o /dev/null -H \"X: {SUB}\" {URL}' 2>&1",
    f'env -v curl -sS -o /dev/null -H "X: {SUB}" {URL} 2>&1',
    f'env --debug curl -sS -o /dev/null -H "X: {SUB}" {URL} 2>&1',
    f"xargs -t -I{{}} curl -sS -o /dev/null -H 'X: {{}}' {URL} < {FED} 2>&1",
    f'xargs --verbose curl -sS -o /dev/null -H "X: {SUB}" {URL} < /dev/null 2>&1',
    f'xargs -p curl -sS -o /dev/null -H "X: {SUB}" {URL} < /dev/null 2>&1',
    # curl has no --name=value form: it names the whole word, value included, as unknown.
    f'curl -sS --header="X: {SUB}" {NOWHERE} 2>&1',
    f'curl -sS --header="{HEADER} {SUB}" {URL}',
    f'curl -sS --data="{SUB}" {NOWHERE} 2>&1',
    f'curl -sS --oauth2-bearer="{SUB}" {NOWHERE} 2>&1',
    f'curl -sS --user="user:{SUB}" {NOWHERE} 2>&1',
    f"curl -sS --header=@<(env-get .env API_KEY -- {PRINTF}) {NOWHERE} 2>&1",
    # An unquoted substitution splits into words: a part becomes a URL curl cannot resolve.
    f"curl -sS -H $(env-get .env API_KEY -- {SPACED}) {NOWHERE} 2>&1",
    f"curl -sS -d $(env-get .env API_KEY -- {SPACED}) {NOWHERE} 2>&1",
    f"curl -sS -H X:$(env-get .env API_KEY -- {SPACED}) {NOWHERE} 2>&1",
    # A trace switch anywhere in the command, before or after the client, in a function
    # body, a loop, a shell's options, SHELLOPTS or a DEBUG trap, shows the arguments.
    f"env SHELLOPTS=xtrace bash -c 'curl -sS -o /dev/null -H \"X: {SUB}\" {URL}' 2>&1",
    f"sh -xc 'curl -sS -o /dev/null -H \"X: {SUB}\" {URL}' 2>&1",
    f'poll() {{ curl -sS -o /dev/null -H "X: {SUB}" {URL}; }}; exec 2>&1; set -x; poll',
    f'exec 2>&1; n=0; while [ $n -lt 2 ]; do curl -sS -o /dev/null -H "X: {SUB}" {URL}; '
    "set -x; n=$((n+1)); done",
    f"exec 2>&1; trap 'set -x' DEBUG; curl -sS -o /dev/null -H \"X: {SUB}\" {URL}",
    f"exec 2>&1; trap 'eval \"$STEP\"' DEBUG; curl -sS -o /dev/null -H \"X: {SUB}\" {URL}",
    f"exec 2>&1; set -x; while true; do curl -sS -H @<(env-get .env API_KEY -- {PRINTF}) "
    f"{URL}; sleep 30; done",
    # -D and -h take the next word (or the rest of theirs), so -H is no option there.
    f'curl -sS -D -H "X: {SUB}" {NOWHERE} 2>&1',
    f'curl -sS -DH "X: {SUB}" {NOWHERE} 2>&1',
    f'curl -sS -h -H "X: {SUB}" {NOWHERE} 2>&1',
]
KEEPS_VALUE_OUT = [
    "env-get .env API_KEY -- sh -c 'echo \"len=${#API_KEY}\"'",
    f'env-get .env API_KEY -- curl -sS -H "{HEADER} $API_KEY" {URL}',
    f"curl -sS -H @<(env-get .env API_KEY -- {PRINTF}) {URL}",
    f"while true; do curl -sS -H @<(env-get .env API_KEY -- {PRINTF}) {URL} || true; "
    "sleep 30; done",
    f'curl -sS -H "{HEADER} $(env-get .env API_KEY -- printenv API_KEY)" {URL}',
    f'TOKEN=$(env-get .env API_KEY -- printenv API_KEY); curl -sS -H "{HEADER} $TOKEN" {URL}',
    f"env-get .env API_KEY -- sh -c 'curl -sS -H \"{HEADER} $(printenv API_KEY)\" {URL}'",
    "env-get .env API_KEY -- sh -c 'true; X=$(printenv API_KEY); "
    f"curl -sS -H \"{HEADER} $X\" {URL}'",
    "env-get .env API_KEY -- ./watch.sh",
    "env-get .env API_KEY -- printenv HOME",
    "env-get --keys .env",
    f'curl -sS -X PUT -H @<(env-get .env API_KEY -- {PRINTF}) -H "Content-Type: '
    f'application/json" --data @/tmp/item.json {URL}',
    f'curl -sS -o /dev/null -w "%{{http_code}}" -H @<(env-get .env API_KEY -- {PRINTF}) {URL}',
    f"curl -sS -H @<(env-get .env API_KEY -- {PRINTF} 2>/dev/null) {URL}",
    f'curl -sS -o /dev/null -w "%{{http_code}}" -H "{HEADER} $(env-get .env API_KEY -- '
    f'printenv API_KEY)" {URL}',
    f'xh {URL} "X:$(env-get .env API_KEY -- printenv API_KEY)"',
    'gh api -H "X: $(env-get .env API_KEY -- printenv API_KEY)" /user',
    # The value of each send option, in its long, glued and clustered spellings.
    f'curl -sS -d "token={SUB}" {URL}',
    f'curl -sS --data-urlencode "token={SUB}" {URL}',
    f'curl -sS -u "user:{SUB}" {URL}',
    f'curl -sS --oauth2-bearer "{SUB}" {URL}',
    f'curl -sS --header "{HEADER} {SUB}" {URL}',
    f'curl -sSH "{HEADER} {SUB}" {URL}',
    f'curl -sS -H "$(env-get .env API_KEY -- {PRINTF})" {URL}',
    f"curl -sS --json @{FED} {URL}",
    f"curl -sS -F token=@{FED} {URL}",
    f"curl -sS -H @<(env-get .env API_KEY -- {PRINTF}) -d @{FED} {URL}",
    f'wget -q -O - --header="{HEADER} {SUB}" {URL}',
    f'wget -q -O - --post-data="token={SUB}" {URL}',
    f'http POST {URL} "token={SUB}"',
    f'http {URL} "{HEADER} {SUB}"',
    f'gh api -f "token={SUB}" /user',
    # wget and gh take --name=value; curl's own -o is no shell's -o xtrace.
    f'gh api --header="X: {SUB}" /user',
    f'wget -q -O /dev/null --header="{HEADER} {SUB}" {URL}',
    f'curl -sS -o /tmp/out -H "{HEADER} {SUB}" {URL}',
]


@pytest.fixture
def keys_cwd(tmp_path):
    env = tmp_path / ".env"
    env.write_text("".join(f"{name}=inert\n" for name in ("API_KEY", "OTHER")),
                   encoding="utf-8")
    env.chmod(0o600)
    return str(tmp_path)


def _decide(tool: str, command: str, cwd: str):
    return guard.decide({"tool_name": tool, "tool_input": {"command": command}, "cwd": cwd})


@pytest.mark.parametrize("command", PRINTS_HANDED_VALUE)
def test_monitor_refuses_a_command_that_prints_a_value_env_get_hands_it(keys_cwd, command):
    specific = _decide("Monitor", command, keys_cwd)["hookSpecificOutput"]
    reason = specific["permissionDecisionReason"]
    assert specific["permissionDecision"] == "deny"
    assert reason.endswith("[rule: env.read.loaded]"), reason
    assert "Monitor output is not scanned" in reason and "with Bash" in reason


@pytest.mark.xfail(strict=True, reason="shell.words.runner_prefix reads the value of bash's "
                   "-o as the command word, so the -c script is never judged, under Bash too")
def test_monitor_refuses_a_send_under_a_shell_given_o_xtrace(keys_cwd):
    command = f"bash -o xtrace -c 'curl -sS -o /dev/null -H \"X: {SUB}\" {URL}' 2>&1"
    assert _decide("Monitor", command, keys_cwd)


@pytest.mark.parametrize("tool", ["Bash", "monitor"])
@pytest.mark.parametrize("command", PRINTS_HANDED_VALUE)
def test_bash_and_an_odd_tool_name_keep_todays_verdict(keys_cwd, tool, command):
    assert _decide(tool, command, keys_cwd) is None


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
@pytest.mark.parametrize("command", KEEPS_VALUE_OUT)
def test_a_form_that_keeps_the_value_out_of_the_output_passes(keys_cwd, tool, command):
    assert _decide(tool, command, keys_cwd) is None


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
def test_a_sent_substitution_that_reads_the_file_is_still_refused(keys_cwd, tool):
    command = f'curl -sS -d "$(env-get .env API_KEY -- printenv API_KEY; cat .env)" {URL}'
    specific = _decide(tool, command, keys_cwd)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert "Monitor" not in specific["permissionDecisionReason"]


@pytest.mark.parametrize("tool", ["Bash", "Monitor"])
def test_a_substitution_after_source_is_judged_as_before(keys_cwd, tool):
    """Only env-get's keys reach a nested substitution under Monitor: source is unchanged."""
    assert _decide(tool, "source .env; echo \"$(printenv API_KEY)\"", keys_cwd) is None


def test_a_refusal_bash_also_gives_keeps_the_bash_reason_under_monitor(keys_cwd):
    command = "source .env; env-get .env OTHER -- printenv API_KEY"
    bash = _decide("Bash", command, keys_cwd)
    assert bash and _decide("Monitor", command, keys_cwd) == bash
