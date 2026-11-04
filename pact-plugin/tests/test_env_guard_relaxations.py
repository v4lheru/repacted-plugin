"""
Location: pact-plugin/tests/test_env_guard_relaxations.py
Summary: The rules that let a wrongly blocked command through hooks/env_guard.py, each with
         the bypasses it could open. Every ALLOW table is a shape that used to be blocked;
         every DENY table is a way to reach a protected file through the same rule, which
         must stay blocked.
           TEMPLATE   .env.example and its kin are not protected by name; a link to a real
                      env file, an extra suffix or a look-alike name still is.
           COPY       copying or moving TO a protected name is a write; FROM one is a leak.
           HEREDOC    a bare-delimiter body is text, its substitutions and (when printed)
                      its variables stay live.
           ESCAPE     an escaped backtick or `\\$(` opens no substitution.
           ENV-GET    $(env-get FILE KEY -- cmd) carries what cmd prints, not the file.
           SEARCH     --exclude-dir prunes the walk; filters match case-sensitively; a
                      repeated rg -u reads hidden files.
           GIT        the staging checks follow a literal variable, a plain cd and a
                      subshell to the directory git runs in, and nothing else.
           DIRECTORY  a read or a search is judged in the directory it runs in, by the
                      same rule; where that is unknown a relative search root denies.
           ROOT       a search root the shell computes is denied unless it can be spelled.
           COMMENT    a `#` hides a comment and nothing after it: not the next line, and
                      not the rest of a line when it sits inside a word.
Used by: the plugin suite. The replay of the field rows these rules came from is
         tests/test_env_guard_field_rows.py.

The fixture tree holds inert files only (MARKER). No real env or credential file is opened.
"""
from __future__ import annotations

import os
import subprocess

import pytest

import env_guard as guard
from project_tasks_support import GIT_ENV

MARKER = "inert-marker-5e1d"
SECRET = f"DEMO_KEY={MARKER}\n"
EXCLUDES = "--exclude='.env*' --exclude='*.env' --exclude=.envrc --exclude=.netrc"


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """Built once (the guard only reads it): env files, templates, links to an env file under
    template names, a clean repo, two repos with an untracked env file, a plain directory
    that holds one."""
    root = tmp_path_factory.mktemp("relax").resolve()

    def put(rel: str, text: str = "x\n") -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def git(repo: str, *args: str) -> None:
        subprocess.run(["git", "-C", str(root / repo), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})

    for name in (".env", "app.env", ".envrc", "sub/.env.local", ".env.example.local",
                 ".env.local.ехample", "big/vendor/pkg/.env", "dirty/.env", "sp ace/.env",
                 "fresh/.env", "UP/.ENV"):
        put(name, SECRET)
    for name in (".env.example", "config/.env.sample", "onlytpl/.env.example", "notes.txt",
                 "local.template", "src/main.py", "big/src/a.py", "clean/README.md",
                 "dirty/README.md", "sp ace/README.md", "plain/a.txt"):
        put(name)
    (root / ".env.template").symlink_to(root / ".env")
    os.link(root / ".env", root / ".env.dist")
    (root / "tpl-link").mkdir()
    (root / "tpl-link" / ".env.example").symlink_to(root / ".env")
    for repo in ("clean", "dirty", "sp ace"):
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "README.md")
        git(repo, "commit", "-q", "-m", "root")
    return root


def reason(command: str, tree, tool: str = "Bash") -> str | None:
    command = command.replace("{T}", str(tree))
    tool_input = {"command": command} if tool == "Bash" else {"file_path": command}
    output = guard.decide({"tool_name": tool, "tool_input": tool_input, "cwd": str(tree)})
    decision = (output or {}).get("hookSpecificOutput", {})
    if decision.get("permissionDecision") != "deny":
        return None
    assert MARKER not in decision["permissionDecisionReason"], "a value reached the reason"
    return decision["permissionDecisionReason"]


# ── TEMPLATE ────────────────────────────────────────────────────────────────────────────
TEMPLATE_ALLOW = [
    "cat .env.example",
    "grep -rn KEY config",
]
TEMPLATE_DENY = [
    "cat .env.template",
    "cp .env .env.example",
    "cat .env.example > /tmp/x; cat .env",
]


@pytest.mark.parametrize("command", TEMPLATE_ALLOW)
def test_a_template_name_is_read_freely(tree, command):
    assert reason(command, tree) is None


@pytest.mark.parametrize("command", TEMPLATE_DENY)
def test_a_template_name_does_not_cover_a_real_env_file(tree, command):
    assert reason(command, tree)


def test_a_blocked_read_says_how_to_name_a_scratch_file(tree):
    text = reason("cat .env", tree)
    assert "name outside the class" in text and "env-get --keys" in text and "\n" not in text


# ── COPY ────────────────────────────────────────────────────────────────────────────────
COPY_ALLOW = [
    "cp local.template .env.local",
]
COPY_DENY = [
    "cp .env notes-copy.txt",
    "cp .env.local",
    "cp -S .bak notes.txt sub/.env.local",
]


@pytest.mark.parametrize("command", COPY_ALLOW)
def test_copying_onto_a_protected_name_is_a_write(tree, command):
    assert reason(command, tree) is None


# ── HEREDOC ─────────────────────────────────────────────────────────────────────────────
HEREDOC_ALLOW = [
    "cat > src/New.cs <<EOF\npublic string Name { get; set; }\nEOF",
]
HEREDOC_DENY = [
    "cat <<EOF\n$(cat .env)\nEOF",
    "source .env; cat > >(tee log) <<EOF\n$DEMO_KEY\nEOF",
    "cat <<EOF\nx\nEOF\ncat .env",
]


@pytest.mark.parametrize("command", HEREDOC_ALLOW)
def test_a_bare_heredoc_body_is_text(tree, command):
    assert reason(command, tree) is None


@pytest.mark.parametrize("command", HEREDOC_DENY)
def test_a_heredoc_that_runs_or_reveals_a_protected_file_is_denied(tree, command):
    assert reason(command, tree)


# ── ESCAPE ──────────────────────────────────────────────────────────────────────────────
ESCAPE_ALLOW = [
    'grep -n "a \\`b\\` c" notes.txt --exclude=\'.env*\'',
]
ESCAPE_DENY = [
    'echo "\\\\`cat .env`"',
    'grep -n "a \\`b\\` c" .env',
]


@pytest.mark.parametrize("command", ESCAPE_DENY)
def test_a_live_substitution_beside_an_escaped_one_is_denied(tree, command):
    assert reason(command, tree)


# ── ENV-GET ─────────────────────────────────────────────────────────────────────────────
ENV_GET_ALLOW = [
    'n=$(env-get .env DEMO_KEY -- ./count.sh "select 1"); echo "rows=$n"',
]
ENV_GET_DENY = [
    'n=$(cat .env); echo "$n"',
    "n=$(envget .env); echo $n",
]


@pytest.mark.parametrize("command", ENV_GET_ALLOW)
def test_a_value_handed_over_by_env_get_is_not_the_file(tree, command):
    assert reason(command, tree) is None


def test_a_substitution_nested_in_a_printed_word_is_judged_without_an_internal_error(tree):
    nested = ('echo "same=$([ "$before" = "$(env-get .env DEMO_KEY -- ./hash.sh)" ] '
              '&& echo yes || echo no)"')
    assert reason(nested, tree) is None
    assert reason('echo "x=$(echo "$(cat .env)")"', tree)


# ── SEARCH ──────────────────────────────────────────────────────────────────────────────
SEARCH_ALLOW = [
    "grep -rn KEY big --exclude-dir=vendor",
    "rg --hidden KEY --iglob '!.ENV*' --iglob '!*.ENV' --iglob '!.ENVRC' --iglob '!.NETRC'",
]
SEARCH_DENY = [
    "grep -rn KEY big",
    "grep -rn KEY . --include='*.py' --include='.env*'",
    "grep -rn \"$(cat .env)\" plain",
]


@pytest.mark.parametrize("command", SEARCH_ALLOW)
def test_a_search_that_cannot_reach_an_env_file_is_allowed(tree, command):
    assert reason(command, tree) is None


def test_flags_held_in_a_variable_get_the_written_out_hint(tree):
    text = reason(f'X="{EXCLUDES}"; grep -rn $X KEY .', tree)
    assert "written out in the command" in text


@pytest.fixture
def bulky(tmp_path, monkeypatch):
    """A tree larger than the walk limit: 40 files under bulk/, the env file after them."""
    monkeypatch.setattr(guard, "WALK_LIMIT", 30)
    for n in range(40):
        (tmp_path / "bulk" / "deep").mkdir(parents=True, exist_ok=True)
        (tmp_path / "bulk" / f"f{n}.txt").write_text("x\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x\n", encoding="utf-8")
    return tmp_path


def test_a_tree_past_the_limit_is_denied_until_the_bulk_is_pruned(bulky):
    assert "more than 30 entries" in reason("grep -rn KEY .", bulky)
    assert reason("grep -rn KEY . --exclude-dir=bulk", bulky) is None
    assert reason(f"grep -rn KEY . {EXCLUDES}", bulky) is None


def test_an_env_file_inside_a_pruned_directory_is_not_read_by_the_search(bulky):
    (bulky / "bulk" / "deep" / ".env").write_text(SECRET, encoding="utf-8")
    assert reason("grep -rn KEY . --exclude-dir=bulk", bulky) is None
    assert reason("grep -rn KEY bulk --exclude-dir=bulk", bulky)        # bulk is the root here


# ── GIT: the directory the staging checks run in ────────────────────────────────────────
GIT_ALLOW = [
    'W="{T}/clean"; cd "$W"; git add README.md && git commit -q -m "x"',
    "cd {T}/fresh && git init -q && git add notes.txt",
]
GIT_DENY = [
    'W="{T}/dirty"; cd "$W" && git add -A',
    'W="{T}/clean"; eval \'W={T}/dirty\'; cd "$W" && git add -A',
    "pushd {T}/clean; pushd {T}/dirty; popd; git add -A",
    "cd() { builtin cd {T}/dirty; }; cd {T}/clean && git add -A",
    "git clone -q {T}/clean {T}/copy && cd {T}/copy && git add -A",
]


@pytest.mark.parametrize("command", GIT_ALLOW)
def test_staging_is_checked_in_the_directory_git_runs_in(tree, command):
    assert reason(command, tree) is None


def test_each_staging_deny_gives_the_form_to_rerun(tree):
    unknown = reason('cd "$SOMEWHERE" && git add notes.txt', tree)
    assert "could not check" in unknown and "git -C <path>" in unknown and "\n" not in unknown
    created = reason("git -C {T}/fresh init -q && git -C {T}/fresh add -A", tree)
    assert "creates a repository" in created and "its own command" in created
    ignored = reason("echo .env >> {T}/dirty/.gitignore && git -C {T}/dirty add -A", tree)
    assert "would stage" in ignored and "write it in its own command" in ignored


EXPANSIONS = [
    ('W=/a; cd "$W"', 'W=/a; cd "/a"'),
    ("W=$HOME/a; cd $W", "W=$HOME/a; cd $W"),
]


@pytest.mark.parametrize("command, expanded", EXPANSIONS)
def test_only_a_leading_literal_assignment_is_spelled_out(command, expanded):
    assert guard.expand_literal_variables(command) == expanded


# ── DIRECTORY: a read or a search is judged where it runs ───────────────────────────────
@pytest.fixture(scope="module")
def rooms(tmp_path_factory):
    """A project whose root holds an env file and a link to it under a plain name. src/ and
    plain/ hold none; dirty/ (a repo), deep/a/b/ and `sp ace/` hold one. safe/lnk points
    into dirty/ and dirty/lnk3 out of it, so a `cd ..` from either lands in one directory
    in a logical shell and in another in a physical one."""
    root = tmp_path_factory.mktemp("rooms").resolve()
    for name in (".env", "dirty/.env", "deep/a/b/.env", "sp ace/.env", "sub/.env.local"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(SECRET, encoding="utf-8")
    for name in ("notes.txt", "src/main.py", "src/sub/x.py", "plain/a.txt", "dirty/README.md",
                 "dirty/inner/x.txt", "safe/x.txt", "clean2/inner/y.txt", "sp ace/a.txt",
                 ".env.example"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x\n", encoding="utf-8")
    (root / "link.txt").symlink_to(root / ".env")
    (root / "safe" / "lnk").symlink_to(root / "dirty" / "inner")
    (root / "dirty" / "lnk3").symlink_to(root / "clean2" / "inner")
    subprocess.run(["git", "-C", str(root / "dirty"), "init", "-q", "-b", "main"], check=True,
                   capture_output=True, env={**os.environ, **GIT_ENV})
    return root


@pytest.fixture
def no_cdpath(monkeypatch):
    monkeypatch.delenv("CDPATH", raising=False)


DIRECTORY_ALLOW = [
    "cd src && grep -rn KEY .",
    'cd "$ELSEWHERE" && ls * && rm -rf build/*',
]
DIRECTORY_DENY = [
    "cd dirty && cat .env",
    'cd plain && echo "$(cat ../link.txt)"',
    "while false; do cd src; done; grep -rn KEY .",
    "cd src; cd -; grep -rn KEY .",
    "D=src; D=.; cd $D && grep -rn KEY .",
    'cd "$ELSEWHERE" && grep -rn KEY {T}',
]


@pytest.mark.parametrize("command", DIRECTORY_ALLOW)
def test_a_command_is_judged_in_the_directory_it_runs_in(rooms, no_cdpath, command):
    assert reason(command, rooms) is None


@pytest.mark.parametrize("command", DIRECTORY_DENY)
def test_a_move_does_not_take_a_reader_out_of_sight(rooms, no_cdpath, command):
    assert reason(command, rooms)


def test_a_search_after_cd_is_limited_by_the_directory_it_walks(bulky):
    (bulky / ".env").write_text(SECRET, encoding="utf-8")
    assert reason("cd src && grep -rn KEY .", bulky) is None        # the bulk is not under src
    assert "more than 30 entries" in reason("cd bulk && grep -rn KEY .", bulky)
    assert reason("cd src && grep -rn KEY ..", bulky)


# ── ROOT: a search root the shell computes is not a clean tree ──────────────────────────
ROOT_ALLOW = [
    'grep -rn KEY "$PWD"/src',
    "grep -rn KEY src/**",
]
ROOT_DENY = [
    'grep -rn KEY "$PWD"',
    'grep -rn KEY "$PWDX/src"',
    'R=dirty; grep -rn KEY "$R"',
]


@pytest.mark.parametrize("command", ROOT_ALLOW)
def test_a_root_the_guard_can_spell_is_walked(rooms, command):
    assert reason(command, rooms) is None


# ── COMMENT: a `#` hides only a comment ─────────────────────────────────────────────────
COMMENT_ALLOW = [
    "ls # cat .env",
    "cd src # go\ngrep -rn KEY .",
]
COMMENT_DENY = [
    "echo hi # note\ncat .env",
    "echo ${x#a}; cat link.txt",
    "echo ${x#a}; git -C dirty add -A",
]


@pytest.mark.parametrize("command", COMMENT_ALLOW)
def test_a_comment_is_not_a_command(rooms, command):
    assert reason(command, rooms) is None


@pytest.mark.parametrize("command", COMMENT_DENY)
def test_a_command_after_a_comment_or_a_hash_in_a_word_is_still_judged(rooms, command):
    assert reason(command, rooms)



@pytest.mark.parametrize("command", ENV_GET_DENY)
def test_a_substitution_that_reads_the_file_itself_is_denied(tree, command):
    assert reason(command, tree)



@pytest.mark.parametrize("command", COPY_DENY)
def test_copying_a_protected_file_out_is_denied(tree, command):
    assert reason(command, tree)



@pytest.mark.parametrize("command", GIT_DENY)
def test_staging_through_an_unfollowed_move_or_into_an_env_file_is_denied(tree, command):
    assert reason(command, tree)



@pytest.mark.parametrize("command", SEARCH_DENY)
def test_a_search_that_can_reach_an_env_file_is_denied(tree, command):
    assert reason(command, tree)



@pytest.mark.parametrize("command", ROOT_DENY)
def test_a_root_that_reaches_an_env_file_or_cannot_be_known_is_denied(rooms, command):
    assert reason(command, rooms)



def test_pruning_the_bulk_does_not_hide_an_env_file_beyond_it(bulky):
    (bulky / "zz").mkdir()
    (bulky / "zz" / ".env").write_text(SECRET, encoding="utf-8")
    assert "zz/.env" in reason("grep -rn KEY . --exclude-dir=bulk", bulky)
    # Not pruned, so the limit is hit before the walk can clear the tree: still denied.
    for unpruned in ("--exclude-dir=./bulk", "--exclude-dir=bul", "--exclude=bulk"):
        assert reason(f"grep -rn KEY . {unpruned}", bulky)
    assert reason("rg -uu KEY . --exclude-dir=bulk", bulky)



def test_the_read_tool_follows_the_same_template_rule(tree):
    assert reason(str(tree / ".env.example"), tree, tool="Read") is None
    assert reason(str(tree / ".env.template"), tree, tool="Read")
    assert reason(str(tree / ".env.dist"), tree, tool="Read")
    assert reason(str(tree / ".env.example.local"), tree, tool="Read")
