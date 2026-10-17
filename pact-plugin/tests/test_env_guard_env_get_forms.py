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
    f"'{ENVGET}' --keys .env",
    f'"{ENVGET}" .env K -- sh -c \'test -n "$K" && echo set\'',
    f'ENVGET="{ENVGET}"\n"$ENVGET" --keys .env',
    f'ENVGET="{ENVGET}"; "${{ENVGET}}" .env K -- sh -c \'echo ok\'',
    f'export ENVGET="{ENVGET}" && $ENVGET --keys .env',
    f'nice -n 19 "{ENVGET}" --keys .env',
    f'cd /tmp && "{ENVGET}" --keys .env',
])
def test_env_get_by_spaced_path_or_assigned_variable_is_allowed(cwd, command):
    assert guard.bash_verdict(command, cwd) is None


@pytest.mark.parametrize("command", [
    '"$ENVGET" --keys .env',                                  # never assigned here
    f'E="{ENVGET}"; E=/bin/cat; "$E" .env',                   # reassigned to cat
    f'E="{ENVGET}"; E+=x; "$E" .env',                         # appended to
    f'E="{ENVGET}"; read E; "$E" .env',                       # set from stdin
    'E=/bin/cat; "$E" .env',                                  # a variable, but not env-get
    f'"{ENVGET}" .env K -- cat .env',                         # the wrapped command still reads it
    f'ENVGET="{ENVGET}"; "$ENVGET" .env K -- cat .env',
    '"/srv/work/My Projects/bin/not-env-get" .env | xargs cat',   # its names, then read
    'cat ".env"',
    f'bash -c "cat .env; {ENVGET}"',                          # a script, not a program word
    f"sh -c 'cat .env {ENVGET}'",
    f'eval "cat .env; {ENVGET}"',
])
def test_other_forms_stay_denied(cwd, command):
    assert guard.bash_verdict(command, cwd)
