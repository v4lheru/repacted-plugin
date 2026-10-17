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
    "cat .env.example", "head -3 config/.env.sample", "cat '.env.example'",
    "cat .env\\.example", "cat .ENV.EXAMPLE", "cat onlytpl/.env.exam*",
    "cat .env.example | sed 's/=.*/=/'", "grep KEY .env.example", "grep -rn KEY onlytpl",
    "grep -rn KEY config",
]
TEMPLATE_DENY = [
    "cat .env.template",                    # a symlink to .env under a template name
    "cat tpl-link/.env.example",
    "cat .env.dist",                        # a second hard link: it may be the env file
    "cat .env.example.local",               # a suffix after the template suffix
    "cat .env.example.",
    'cat ".env.local.ехample"',             # Cyrillic look-alike letters: not a template
    "cat .env.example .env",
    "cat .env.e*",                          # the glob also matches .env.example.local
    "cat ./.env.example/../.env",
    "grep -rn KEY tpl-link",
    "grep -R KEY tpl-link",
    # A template name must not become the way out for a real file.
    "cp .env .env.example", "mv .env .env.sample", "ln -s .env .env.example",
    "ln .env x.example", "cat .env > .env.example", "sed -n p .env > .env.example",
    "cat .env.example > /tmp/x; cat .env",
]


@pytest.mark.parametrize("command", TEMPLATE_ALLOW)
def test_a_template_name_is_read_freely(tree, command):
    assert reason(command, tree) is None


@pytest.mark.parametrize("command", TEMPLATE_DENY)
def test_a_template_name_does_not_cover_a_real_env_file(tree, command):
    assert reason(command, tree)


def test_the_read_tool_follows_the_same_template_rule(tree):
    assert reason(str(tree / ".env.example"), tree, tool="Read") is None
    assert reason(str(tree / ".env.template"), tree, tool="Read")
    assert reason(str(tree / ".env.dist"), tree, tool="Read")
    assert reason(str(tree / ".env.example.local"), tree, tool="Read")


def test_a_blocked_read_says_how_to_name_a_scratch_file(tree):
    text = reason("cat .env", tree)
    assert "name outside the class" in text and "env-get --keys" in text and "\n" not in text


# ── COPY ────────────────────────────────────────────────────────────────────────────────
COPY_ALLOW = [
    "cp local.template .env.local", "cp notes.txt sub/.env.local", "mv notes.txt .envrc",
    "cp notes.txt .env", "cp local.template .env.development.local; ls .env.development.local",
]
COPY_DENY = [
    "cp .env notes-copy.txt", "mv .env plain.txt", "cp .env plain", "cp .env plain/",
    "cp .env sub/.env.local plain/",        # several sources into a directory
    "cp notes.txt .env plain/",             # the protected file is a source, not the target
    "cp .env .env.local notes.txt",
    "cp -t plain .env", "cp -vt plain .env", "cp --target-directory plain .env",
    "cp --target-directory=plain .env",     # every operand is a source
    "cp .env.local",                        # one operand: nothing says it is the target
    "ln -s .env link2.txt",
    # GNU keeps the file written over under another name (the review's row t13: main DENY).
    "cp --suffix=.example -b notes.txt .env", "cp -b notes.txt .env",
    "mv --backup=numbered notes.txt .env", "cp -S .bak notes.txt sub/.env.local",
]


@pytest.mark.parametrize("command", COPY_ALLOW)
def test_copying_onto_a_protected_name_is_a_write(tree, command):
    assert reason(command, tree) is None


@pytest.mark.parametrize("command", COPY_DENY)
def test_copying_a_protected_file_out_is_denied(tree, command):
    assert reason(command, tree)


# ── HEREDOC ─────────────────────────────────────────────────────────────────────────────
HEREDOC_ALLOW = [
    "cat > src/New.cs <<EOF\npublic string Name { get; set; }\nEOF",
    "cat <<EOF > notes.md\nRun env to see\nset\nEOF",
    "cat <<EOF\ncat .env\nenv\nprintenv\nEOF",           # printed as text, never run
    "cat <<EOF\n\\$(cat .env)\nEOF",                     # an escaped substitution is text
    "source .env; cat > out.conf <<EOF\nkey=$DEMO_KEY\nEOF",    # written to a file
    "source .env; cat 1>out.txt <<EOF\n$DEMO_KEY\nEOF",
]
HEREDOC_DENY = [
    "cat <<EOF\n$(cat .env)\nEOF",                       # a live substitution in the body
    "cat <<EOF\n`cat .env`\nEOF",
    "cat <<-EOF\n\t$(cat .env)\n\tEOF",
    "cat <<EOF\n\\\\$(cat .env)\nEOF",                   # an escaped backslash, then a live one
    "source .env; cat <<EOF\n$DEMO_KEY\nEOF",            # a loaded value printed by the reader
    "source .env && cat <<EOF\n${DEMO_KEY}\nEOF",
    "source .env; cat <<EOF | tee out.log\n$DEMO_KEY\nEOF",
    "source .env; cat <<EOF >&2\n$DEMO_KEY\nEOF",        # stderr is the transcript too
    "source .env; cat <<EOF 2>/dev/null\n$DEMO_KEY\nEOF",
    "source .env; tee out.txt <<EOF\n$DEMO_KEY\nEOF",
    "source .env; cat > >(tee log) <<EOF\n$DEMO_KEY\nEOF",    # a redirect that is shown anyway
    "source .env; cat <<EOF > >(cat)\n$DEMO_KEY\nEOF",
    "source .env; cat > /dev/stderr <<EOF\n$DEMO_KEY\nEOF",
    "source .env; cat <<EOF >/dev/fd/2\n$DEMO_KEY\nEOF",
    "source .env; echo hi > log.txt; cat <<EOF\n$DEMO_KEY\nEOF",   # another command's redirect
    "bash <<EOF\ncat .env\nEOF",                         # the body is run
    "cat <<EOF | sh\ncat .env\nEOF",
    "sh <<EOF\nset\nEOF",
    "python3 - <<EOF\nprint(open('.env').read())\nEOF",
    "cat <<EOF; cat .env\nx\nEOF",                       # a second command on the operator line
    "cat <<EOF\nx\nEOF\ncat .env",                       # and one after the body
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
    f'grep -rn "uses \\`x\\` here" src {EXCLUDES}',
    'echo "\\`cat .env\\`"', 'echo "\\$(cat .env)"', "echo \\`cat .env\\`", "echo '`cat .env`'",
]
ESCAPE_DENY = [
    'echo "\\\\`cat .env`"',                # the backslash is escaped, the backtick is live
    'echo "\\\\$(cat .env)"',
    "echo `cat .env`", 'x=`cat .env`; echo "$x"',
    "echo `echo \\`cat .env\\``",           # a nested substitution inside backticks
    'echo "$(echo "\\`"; cat .env)"',
    'grep -n "a \\`b\\` c" .env',
]


@pytest.mark.parametrize("command", ESCAPE_ALLOW)
def test_an_escaped_substitution_opens_nothing(tree, command):
    assert reason(command, tree) is None


@pytest.mark.parametrize("command", ESCAPE_DENY)
def test_a_live_substitution_beside_an_escaped_one_is_denied(tree, command):
    assert reason(command, tree)


# ── ENV-GET ─────────────────────────────────────────────────────────────────────────────
ENV_GET_ALLOW = [
    'n=$(env-get .env DEMO_KEY -- ./count.sh "select 1"); echo "rows=$n"',
    "n=$(python3 bin/env-get .env DEMO_KEY -- ./count.sh); echo $n",
    'for i in 1 2; do n=$(env-get sub/.env.local DEMO_KEY -- ./count.sh); echo "t$i $n"; done',
]
ENV_GET_DENY = [
    'n=$(cat .env); echo "$n"',
    'n=$(env-get .env DEMO_KEY -- cat .env); echo "$n"',          # cmd itself reads the file
    "n=$(env-get .env DEMO_KEY -- sh -c 'cat .env'); echo \"$n\"",
    'n=$(env-get --keys .env; cat .env); echo "$n"',              # a second command
    "n=$(env-get .env DEMO_KEY -- ./count.sh && cat .env); echo $n",
    "n=$(env-get .env DEMO_KEY -- ./count.sh < .env); echo $n",
    "n=$(not-env-get .env DEMO_KEY -- ./count.sh); echo $n",      # a look-alike program
    "n=$(envget .env); echo $n",
]


@pytest.mark.parametrize("command", ENV_GET_ALLOW)
def test_a_value_handed_over_by_env_get_is_not_the_file(tree, command):
    assert reason(command, tree) is None


@pytest.mark.parametrize("command", ENV_GET_DENY)
def test_a_substitution_that_reads_the_file_itself_is_denied(tree, command):
    assert reason(command, tree)


def test_a_substitution_nested_in_a_printed_word_is_judged_without_an_internal_error(tree):
    nested = ('echo "same=$([ "$before" = "$(env-get .env DEMO_KEY -- ./hash.sh)" ] '
              '&& echo yes || echo no)"')
    assert reason(nested, tree) is None
    assert reason('echo "x=$(echo "$(cat .env)")"', tree)


# ── SEARCH ──────────────────────────────────────────────────────────────────────────────
SEARCH_ALLOW = [
    "grep -rn KEY big --exclude-dir=vendor", "grep -rn KEY big --exclude-dir vendor",
    "grep -rn KEY big --exclude-dir='vend*'", "grep -rn KEY big --exclude-dir=pkg",
    f"grep -rn KEY . {EXCLUDES}", "grep -rn KEY . --include='*.py'", "grep -rn KEY plain",
    "rg -uu KEY -g '!.env*' -g '!*.env' -g '!.envrc' -g '!.netrc'",
    "rg --hidden KEY --iglob '!.ENV*' --iglob '!*.ENV' --iglob '!.ENVRC' --iglob '!.NETRC'",
]
SEARCH_DENY = [
    "grep -rn KEY big",
    "grep -rn KEY big --exclude-dir=vendo",             # the name must match
    "grep -rn KEY big --exclude-dir=big",               # a root is searched whatever its name
    "grep -rn KEY big --exclude-dir=big/vendor",        # a path is not a base name
    "grep -rn KEY big/vendor --exclude-dir=vendor",
    "rg -uu KEY big --exclude-dir=vendor",              # not an rg option: nothing is pruned
    # An exclude or include that still lets an env file through.
    "grep -rn KEY . --exclude='.ENV*' --exclude='*.ENV' --exclude=.ENVRC --exclude=.NETRC",
    "rg -uu KEY -g '!.ENV*' -g '!*.ENV' -g '!.ENVRC' -g '!.NETRC'",
    "grep -rn KEY . --exclude='.env'", "grep -rn KEY . --exclude='src/.env*'",
    "grep -rn KEY . --include='*'", "grep -rn KEY . --include='*.env'",
    "grep -rn KEY . --include='*.py' --include='.env*'",
    # Flags that make the search read hidden or ignored files.
    "rg -uu KEY", "rg -u -u KEY", "rg -uuu KEY", "rg --unrestricted --unrestricted KEY",
    "rg --hidden KEY", "rg -. KEY", "rg --no-ignore KEY", "ag -u KEY", "ag --hidden KEY",
    # A pattern argument that is really a second command.
    "grep -rn 'KEY' plain; cat .env", "grep -rn KEY plain `cat .env`",
    "grep -rn \"$(cat .env)\" plain",
]


@pytest.mark.parametrize("command", SEARCH_ALLOW)
def test_a_search_that_cannot_reach_an_env_file_is_allowed(tree, command):
    assert reason(command, tree) is None


@pytest.mark.parametrize("command", SEARCH_DENY)
def test_a_search_that_can_reach_an_env_file_is_denied(tree, command):
    assert reason(command, tree)


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


def test_pruning_the_bulk_does_not_hide_an_env_file_beyond_it(bulky):
    (bulky / "zz").mkdir()
    (bulky / "zz" / ".env").write_text(SECRET, encoding="utf-8")
    assert "zz/.env" in reason("grep -rn KEY . --exclude-dir=bulk", bulky)
    # Not pruned, so the limit is hit before the walk can clear the tree: still denied.
    for unpruned in ("--exclude-dir=./bulk", "--exclude-dir=bul", "--exclude=bulk"):
        assert reason(f"grep -rn KEY . {unpruned}", bulky)
    assert reason("rg -uu KEY . --exclude-dir=bulk", bulky)


def test_an_env_file_inside_a_pruned_directory_is_not_read_by_the_search(bulky):
    (bulky / "bulk" / "deep" / ".env").write_text(SECRET, encoding="utf-8")
    assert reason("grep -rn KEY . --exclude-dir=bulk", bulky) is None
    assert reason("grep -rn KEY bulk --exclude-dir=bulk", bulky)        # bulk is the root here


# ── GIT: the directory the staging checks run in ────────────────────────────────────────
GIT_ALLOW = [
    'W="{T}/clean"; cd "$W"; git add README.md && git commit -q -m "x"',
    'W="{T}/clean"; cd "$W" && git add -A',
    "W={T}/clean && git -C $W add -A",
    'A={T}; B="$A/clean"; cd "$B" && git add -A',
    'W="{T}/clean"; cd "${W}" && git add -A',
    "cd {T}/dirty; cd {T}/clean; git add -A",
    "(cd {T}/dirty); cd {T}/clean && git add -A",
    "(cd {T}/dirty); git -C {T}/clean add -A",
    "make && cd {T}/clean && git add -A",
    'cd "{T}/clean" && git add -A', "cd '{T}/clean' && git add -A",
    "source ./activate.sh; git -C {T}/clean add -A",
    'cd {T}/clean && git add README.md && git commit -m "it\'s \\"quoted\\" $(echo "a b")"',
    # A quote inside $(...) inside double quotes, after a path with a space.
    'cd "{T}/sp ace" && git add README.md && git commit -q -m "x" && for f in a b; do echo '
    '"$f $(grep -n \'\\["\\(a\\|b\\)\' README.md | cut -d: -f1)"; done',
    "cd {T}/fresh && git init -q && git add notes.txt",     # named files in a new repository
]
GIT_DENY = [
    # The same shapes, pointed at a repository with an untracked env file.
    'W="{T}/dirty"; cd "$W" && git add -A', "W={T}/dirty && git -C $W add -A",
    'W="{T}/sp ace"; git -C "$W" add -A', 'A={T}; B="$A/dirty"; cd "$B" && git add -A',
    'W="{T}/clean"; cd "$W/../dirty" && git add -A',
    'W="{T}/clean"; cd "${W}/../dirty" && git add -A',
    "cd {T}/clean; cd {T}/dirty; git add -A", "cd {T}/clean && cd ../dirty && git add -A",
    "cd {T}/clean/../dirty && git add -A",
    'cd "{T}/sp ace" && git add -A && for f in a b; do echo "$f $(grep -n \'\\["\\(a\\|b\\)\' '
    'README.md | cut -d: -f1)"; done',
    'git -C "{T}/sp ace" add -A && echo "$(grep -n \'\\["\\(a\\|b\\)\' README.md)"',
    # A variable that is reassigned, unset or computed between its assignment and its use.
    'W="{T}/clean"; W="{T}/dirty"; cd "$W" && git add -A',
    'W="{T}/clean"; unset W; cd "$W" && git add -A',
    'W="{T}/clean"; read W < /tmp/x; cd "$W" && git add -A',
    'W="{T}/clean"; W=$(echo {T}/dirty); cd "$W" && git add -A',
    'W=$(echo {T}/clean); cd "$W" && git add -A',
    'W="{T}/clean"; for W in {T}/dirty; do cd "$W" && git add -A; done',
    'W="{T}/clean"; eval \'W={T}/dirty\'; cd "$W" && git add -A',
    'W="{T}/clean"; export W={T}/dirty; cd "$W" && git add -A',
    'W="{T}/clean"; : ${W:={T}/dirty}; cd "$W" && git add -A',
    'W="{T}/clean"; W+=/../dirty; cd "$W" && git add -A',
    'W="{T}/clean"; ((W++)); cd "$W" && git add -A',
    'W="{T}/clean"; printf -v W %s {T}/dirty; cd "$W" && git add -A',
    'W="{T}/clean"; cd "${W%clean}dirty" && git add -A',
    'W="{T}/clean"; cd "$W[1]" && git add -A',              # a zsh subscript
    'W="{T}/clean"; cd \'$W\' && git add -A',               # single quotes: not expanded
    'true; W="{T}/clean"; cd "$W" && git add -A',           # not at the start of the command
    'W="{T}/sp ace"; git -C $W add -A',                     # unquoted, and the value has a space
    'W="{T}/clean"; bash <<\'EOF\'\ncd $W && git add -A\nEOF',    # the inner shell has no $W
    # cd chains, subshells, pushd and every move that is not a plain cd.
    "(cd {T}/clean); cd {T}/dirty && git add -A",
    "(cd {T}/clean && true); git -C {T}/dirty add -A",
    "cd {T}/clean && (cd {T}/dirty && git add -A)",
    "cd {T}/dirty && (cd {T}/clean) && git add -A",
    "pushd {T}/dirty && git add -A", "pushd {T}/dirty && popd && git add -A",
    "pushd {T}/clean; pushd {T}/dirty; popd; git add -A",
    "cd {T}/clean; cd -; git add -A", "cd {T}/clean || cd {T}/dirty; git add -A",
    "false && cd {T}/clean; git add -A", "cd {T}/clean | true; git add -A",
    "cd {T}/clean & git add -A", "{ cd {T}/clean; }; git add -A",
    "if true; then cd {T}/clean; fi; git add -A",
    "for d in {T}/clean; do cd $d; done; git add -A",
    "cd() { builtin cd {T}/dirty; }; cd {T}/clean && git add -A",
    "function cd { builtin cd {T}/dirty; }; cd {T}/clean && git add -A",
    "alias cd='cd {T}/dirty;:'; cd {T}/clean && git add -A",
    "builtin cd {T}/clean && git add -A", "command cd {T}/clean && git add -A",
    "cd -P {T}/clean && git add -A", "cd {T}/clean extra && git add -A",
    "cd {T}/nosuch; git add -A", "CDPATH={T}; cd dirty && git add -A",
    'eval "cd {T}/clean"; git add -A', 'cd "$(echo {T}/clean)" && git add -A',
    # A staging command inside a substitution.
    'echo "$(cd {T}/dirty && git add -A)"', "cd {T}/clean && echo `git -C {T}/dirty add -A`",
    'git -C {T}/clean status > "$(git -C {T}/dirty add -A)"',
    # A repository the same command creates: what it would stage cannot be listed yet.
    "git -C {T}/fresh init -q && git -C {T}/fresh add -A",
    "cd {T}/fresh && git init -q && git add -A", "git init -q {T}/fresh; git -C {T}/fresh add .",
    "git clone -q {T}/clean {T}/copy && cd {T}/copy && git add -A",
]


@pytest.mark.parametrize("command", GIT_ALLOW)
def test_staging_is_checked_in_the_directory_git_runs_in(tree, command):
    assert reason(command, tree) is None


@pytest.mark.parametrize("command", GIT_DENY)
def test_staging_through_an_unfollowed_move_or_into_an_env_file_is_denied(tree, command):
    assert reason(command, tree)


def test_each_staging_deny_gives_the_form_to_rerun(tree):
    unknown = reason('cd "$SOMEWHERE" && git add notes.txt', tree)
    assert "could not check" in unknown and "git -C <path>" in unknown and "\n" not in unknown
    created = reason("git -C {T}/fresh init -q && git -C {T}/fresh add -A", tree)
    assert "creates a repository" in created and "its own command" in created
    ignored = reason("echo .env >> {T}/dirty/.gitignore && git -C {T}/dirty add -A", tree)
    assert "would stage" in ignored and "write it in its own command" in ignored


EXPANSIONS = [
    ('W=/a; cd "$W"', 'W=/a; cd "/a"'),
    ("W=/a && cd $W/b", "W=/a && cd /a/b"),
    ('A=/a; B="$A/b"; cd "$B"', 'A=/a; B="/a/b"; cd "/a/b"'),
    ('W="/a b"; cd "$W"; ls $W', 'W="/a b"; cd "/a b"; ls $W'),         # unquoted: left alone
    ("W=/a; echo '$W' \"$W\"", "W=/a; echo '$W' \"/a\""),
    ("W=/a; echo \\$W $W:h $W[1] ${W:-x}", "W=/a; echo \\$W $W:h $W[1] ${W:-x}"),
    ("ls; W=/a; cd $W", "ls; W=/a; cd $W"),                             # not leading
    ("W=/a; W=/b; cd $W", "W=/a; W=/b; cd $W"),
    ('W=""; cd "$W"', 'W=""; cd "$W"'),                                 # empty: left alone
    ("W=/a; source x.sh; cd $W", "W=/a; source x.sh; cd $W"),           # x.sh may set W
    ("W=$HOME/a; cd $W", "W=$HOME/a; cd $W"),
]


@pytest.mark.parametrize("command, expanded", EXPANSIONS)
def test_only_a_leading_literal_assignment_is_spelled_out(command, expanded):
    assert guard.expand_literal_variables(command) == expanded


def test_a_heredoc_body_is_kept_apart_and_spelled_only_under_a_bare_delimiter():
    """The reader takes each body out of the text before the names are spelled."""
    state = guard._State()
    flat = guard._read("W=/a; cat <<'EOF'\nit's $W\nEOF\ncat <<EOF\n$W\nEOF\ncd $W", state)
    command, rounds = guard._literal_rounds(flat)
    assert command.endswith("cd /a") and "$W" not in command
    (quoted, kept), (bare, spelled) = state.bodies.values()
    assert (quoted, kept) == (False, "it's $W")
    assert bare and guard._spell_rounds(spelled, rounds) == "/a"


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
    # A plain cd, pushd or subshell is followed: the search runs where no env file is.
    "cd src && grep -rn KEY .", "cd src; grep -rn KEY .", "cd src\ngrep -rn KEY .",
    "cd {T}/src && rg --hidden KEY", "pushd src && grep -rn KEY .", "(cd src && grep -rn KEY .)",
    "cd src && (cd ..) && grep -rn KEY .", 'echo "$(cd src && grep -rn KEY .)"',
    "cd dirty && grep -rn KEY ../src", "D=src; cd $D && grep -rn KEY .",
    'D="{T}/src"; cd "$D/sub" && grep -rn KEY .', "\\cd src && grep -rn KEY .",
    "cd plain && cat a.txt", 'cd "sp ace" && cat a.txt', "cd sp\\ ace && cat a.txt",
    "cd src && grep -rn 'cd ..; cat link.txt' .",       # a pattern is not a second command
    # The directory is not known: a plain name is judged by name, an absolute root is walked,
    # a search that excludes the class or prints no line needs no walk.
    'cd "$ELSEWHERE" && cat a.txt',
    'cd "$ELSEWHERE" && cat .env.example', 'cd "$ELSEWHERE" && grep -rn KEY {T}/src',
    f'cd "$ELSEWHERE" && grep -rn KEY . {EXCLUDES}', 'cd "$ELSEWHERE" && grep -rln KEY .',
    'cd "$ELSEWHERE" && ls * && rm -rf build/*',
]
DIRECTORY_DENY = [
    # Followed into, or back out to, a directory that holds an env file.
    "cd dirty && cat .env", "cd plain && cat ../.env", "cd plain && grep -rn KEY ..",
    "cd deep && grep -rn KEY .", "cd src && cd .. && grep -rn KEY .",
    "cd src/../dirty && grep -rn KEY .", "cd src && grep -rn KEY {T}",
    "cd src && grep -rn KEY ../dirty", 'cd "sp ace" && grep -rn KEY .',
    "cd src && grep -rn KEY . && cd ../dirty && grep -rn KEY .",
    "cd dirty/inner && rg --hidden KEY ..", "D=src; cd $D/.. && grep -rn KEY .",
    "cd dirty && grep -rnE 'CLIENT_SECRET|ClientSecret' --include='*' -I . 2>/dev/null | cut -c1-9",
    # A link under a plain name resolves from the directory the reader runs in.
    "cd plain && cat ../link.txt", "cd plain; head -3 ../link.txt",
    "cd src && cat ../dirty/../link.txt", "cd plain && sed -n 1,5p ../link.txt",
    "cd src && cat ../link.txt | head", "cd src && cat < ../link.txt",
    'cd plain && echo "$(cat ../link.txt)"', 'cd src && echo "$(cd .. && cat link.txt)"',
    # A scope ends: the search after it runs in the directory before it.
    "(cd src && grep -rn KEY .); grep -rn KEY .", "(cd src); grep -rn KEY .",
    "cd src && (cd ..; grep -rn KEY .)", "echo $(cd src); grep -rn KEY .",
    "pushd src && grep -rn KEY . && popd && grep -rn KEY .", "pushd src; popd; grep -rn KEY .",
    'cd src && echo "$(grep -rn KEY ..)"',
    # A move that did not happen, or may not have: never taken as made.
    "cd nowhere; grep -rn KEY .", "cd nowhere 2>/dev/null; grep -rn KEY .",
    "cd src & grep -rn KEY .", "cd src | cat; grep -rn KEY .", "true | cd src; grep -rn KEY .",
    "cd nowhere || cd src; grep -rn KEY .", "false && cd src; grep -rn KEY .",
    "true || cd src; grep -rn KEY .", "[ -d nowhere ] && cd src; grep -rn KEY .",
    "if false; then cd src; fi; grep -rn KEY .", "for d in src; do cd src; done; grep -rn KEY .",
    "while false; do cd src; done; grep -rn KEY .", "case x in y) cd src;; esac; grep -rn KEY .",
    "! cd src; grep -rn KEY .", "сd src && grep -rn KEY .",          # a Cyrillic с: not cd
    # A move this cannot read: the directory is unknown, so a relative root is denied.
    'cd "$ELSEWHERE" && grep -rn KEY .', 'cd "$ELSEWHERE" && rg --hidden KEY',
    "cd $(pwd) && grep -rn KEY .", "cd `pwd` && grep -rn KEY .", "cd - && grep -rn KEY .",
    "cd src; cd -; grep -rn KEY .", "cd -P src && grep -rn KEY ..", "cd src plain; grep -rn KEY .",
    "cd ~nobody && grep -rn KEY .", "{ cd src; }; grep -rn KEY ..",
    "builtin cd src && grep -rn KEY ..", "command cd src; grep -rn KEY ..",
    'eval "cd src"; grep -rn KEY ..', "X=cd; $X src; grep -rn KEY ..",
    'cd src && cd "$OLDPWD" && grep -rn KEY .',
    # cd redefined, or run where this cannot see it: no later move is followed.
    "trap 'cd ..' DEBUG; cd src/sub && grep -rn KEY .",
    "trap 'cd {T}' DEBUG; cd {T}/src && grep -rn KEY .",
    "cd() { builtin cd ..; }; cd src/sub && grep -rn KEY .",
    "function cd { builtin cd ..; }; cd src/sub && grep -rn KEY .",
    "alias cd='cd ..;'; cd src && grep -rn KEY .", "CDPATH=dirty cd inner && grep -rn KEY ..",
    "export CDPATH={T}/dirty; cd src && grep -rn KEY .",
    "cdpath=({T}/dirty); cd src && grep -rn KEY .",
    "cd src; PWD=../dirty; grep -rn KEY $PWD", "cd src && OLDPWD=x && grep -rn KEY .",
    # The variable the cd uses is set again, unset or computed before it.
    "D=src; D=.; cd $D && grep -rn KEY .", "D=src; unset D; cd $D && grep -rn KEY .",
    "D=src; D=$(echo .); cd $D && grep -rn KEY .", "D=src; D+=/..; cd $D && grep -rn KEY .",
    "D=src; read D <<< .; cd $D && grep -rn KEY .", "D=src; export D=.; cd $D && grep -rn KEY .",
    "D=src; for D in .; do :; done; cd $D && grep -rn KEY .",
    "D=src; cd ${D/src/.} && grep -rn KEY .", "D=src; eval D=.; cd $D && grep -rn KEY .",
    "D=src; printf -v D .; cd $D && grep -rn KEY .",
    "D=src; f() { D=.; }; f; cd $D && grep -rn KEY .",
    # Through a linked directory `cd ..` is ambiguous, and `..` as a root is the real parent.
    "cd safe/lnk && grep -rn KEY ..", "cd safe/lnk && cd .. && grep -rn KEY .",
    "cd dirty/lnk3 && cd .. && grep -rn KEY .",
    # Where the directory is unknown a protected name, or a glob that can match one, denies.
    'cd "$ELSEWHERE" && cat .env', 'cd "$ELSEWHERE" && cat sub/.env.local',
    'cd "$ELSEWHERE" && cat .e*', 'cd "$ELSEWHERE" && cat *',
    # An empty "$ELSEWHERE" leaves the shell where it was, and there link.txt is the env file.
    'cd "$ELSEWHERE" && cat *.txt',
    'cd "$ELSEWHERE" && grep -rn KEY {T}',
]


@pytest.mark.parametrize("command", DIRECTORY_ALLOW)
def test_a_command_is_judged_in_the_directory_it_runs_in(rooms, no_cdpath, command):
    assert reason(command, rooms) is None


@pytest.mark.parametrize("command", DIRECTORY_DENY)
def test_a_move_does_not_take_a_reader_out_of_sight(rooms, no_cdpath, command):
    assert reason(command, rooms)


def test_an_unknown_directory_deny_gives_the_form_to_rerun(rooms, no_cdpath):
    text = reason('cd "$ELSEWHERE" && grep -rn KEY .', rooms)
    assert "absolute" in text and "`cd <path> &&`" in text and "--exclude='.env*'" in text


def test_a_search_after_cd_is_limited_by_the_directory_it_walks(bulky):
    (bulky / ".env").write_text(SECRET, encoding="utf-8")
    assert reason("cd src && grep -rn KEY .", bulky) is None        # the bulk is not under src
    assert "more than 30 entries" in reason("cd bulk && grep -rn KEY .", bulky)
    assert reason("cd src && grep -rn KEY ..", bulky)


# ── ROOT: a search root the shell computes is not a clean tree ──────────────────────────
ROOT_ALLOW = [
    'grep -rn KEY "$PWD"/src', "grep -rn KEY ${PWD}/src", 'cd src && grep -rn KEY "$PWD"',
    "grep -rn KEY sr*", "grep -rn KEY src", 'grep -rln KEY "$PWD"',
    f'grep -rn KEY "$PWD" {EXCLUDES}', "rg KEY d*",                   # rg skips hidden files
]
ROOT_DENY = [
    'grep -rn KEY "$PWD"', "grep -rn KEY ${PWD}", 'grep -rn KEY "${PWD}/dirty"',
    'grep -rn KEY -- "$PWD"', 'grep -rn KEY src "$PWD"', 'rg --hidden KEY "$PWD"',
    'cd src && grep -rn KEY "$OLDPWD"', 'cd src && grep -rn KEY "$PWD/.."',
    'grep -rn KEY "$(pwd)"', "grep -rn KEY $(git rev-parse --show-toplevel)",
    "rg --hidden KEY `pwd`", 'grep -rn KEY "$UNSET_ROOT"', 'grep -rn KEY "$PWDX/src"',
    "grep -rn KEY ~+/dirty", "cd src && grep -rn KEY ~-",
    "grep -rn KEY d*", "grep -rn KEY ./*/", "grep -rn KEY dirt?", "grep -rn KEY d[ie]*",
    "grep -rn KEY {dirty,src}", "grep -rn KEY src/**", "cd dirty && grep -rn KEY ../d*",
    'cd "$ELSEWHERE" && grep -rn KEY d*', "R=.; grep -rn KEY $R", 'R=dirty; grep -rn KEY "$R"',
]


@pytest.mark.parametrize("command", ROOT_ALLOW)
def test_a_root_the_guard_can_spell_is_walked(rooms, command):
    assert reason(command, rooms) is None


@pytest.mark.parametrize("command", ROOT_DENY)
def test_a_root_that_reaches_an_env_file_or_cannot_be_known_is_denied(rooms, command):
    assert reason(command, rooms)


# ── COMMENT: a `#` hides only a comment ─────────────────────────────────────────────────
COMMENT_ALLOW = [
    "ls # cat .env", "ls # see .env\nls", "cat notes.txt # not link.txt", "# cat .env\nls",
    "echo a#b; cat notes.txt", 'echo "a # b"; cat notes.txt', "cd src # go\ngrep -rn KEY .",
]
COMMENT_DENY = [
    # The line after a trailing comment is a command of its own.
    "echo hi # note\ncat .env", "echo hi # note\ncat link.txt", "echo hi #note; cat ok\ncat .env",
    "echo 'a # b' # c\nhead link.txt", "ls # don't\ncat .env", "ls # c \\\ncat .env",
    "cd src # go\ngrep -rn KEY ..", "cd src;# x\ncat ../link.txt",
    'echo "$(ls # c\ncat .env)"', "echo x # c\ngit -C dirty add -A",
    # A `#` inside a word opens no comment: what follows on the line still runs.
    "echo a#b; cat .env", "echo ${x#a}; cat link.txt", "[ $# -gt 0 ] || cat .env",
    "echo ${#x}; head -1 link.txt", "echo $((16#ff)); cat .env",
    "git log --format=%h#%s; cat .env", "echo \\# x; cat .env",
    "echo ${x#a}; git -C dirty add -A",
]


@pytest.mark.parametrize("command", COMMENT_ALLOW)
def test_a_comment_is_not_a_command(rooms, command):
    assert reason(command, rooms) is None


@pytest.mark.parametrize("command", COMMENT_DENY)
def test_a_command_after_a_comment_or_a_hash_in_a_word_is_still_judged(rooms, command):
    assert reason(command, rooms)
