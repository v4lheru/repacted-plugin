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
