"""
Location: pact-plugin/tests/test_env_guard_field_rows.py
Summary: A replay of the guard denials one long field session produced (2026-10-01), as a
         table: each row keeps the SHAPE of the command that was blocked (its cd, its
         quoting, its flags, where its files sit), with invented names and inert files.
           false       the block was wrong. `passes` says whether the guards now let it run;
                       a row left blocked carries the text its deny must give for a rerun.
           real        the block caught a real leak: it must still deny.
           plausible   the block may have caught one: it still denies where the searched
                       tree holds an env file (30); 27 and 42 searched a tree that holds none.
           overridden  the caller got the same end another way: the two staging rows must
                       still deny; the third was an internal error and now passes.
         test_the_measure pins the counts the table declares; the row test checks each one.
Used by: the plugin suite. The rules behind each pass have their bypass tests in
         tests/test_env_guard_relaxations.py and tests/test_process_table_guard.py.

Every file in the fixture is inert (MARKER). No real env or credential file is opened.
"""
from __future__ import annotations

import subprocess
from collections import Counter
from pathlib import Path
from typing import Callable, NamedTuple

import pytest

import env_guard as guard
import process_table_guard
from project_tasks_support import GIT_ENV

MARKER = "inert-marker-5e1d"
EXCLUDE_HINT = "--exclude='.env*'"


@pytest.fixture(autouse=True)
def _git_env(monkeypatch):
    for key, value in GIT_ENV.items():
        monkeypatch.setenv(key, value)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def put(root: Path, *names: str, text: str = "x\n") -> None:
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.fixture
def field(tmp_path) -> dict[str, Path]:
    """The layout the session worked in: a project directory (the session's cwd) holding an
    env file, two unpacked archives below it, and worktrees beside them. Paths hold a space."""
    base = tmp_path / "work dir"
    home = base / "clients" / "acme"
    extract = home / "extracted"
    front, back = extract / "front-app", extract / "back-app"
    tree, tree_env = extract / "trees" / "wt-a", extract / "trees" / "wt-c"
    secret = f"DEMO_KEY={MARKER}\n"
    put(base, "other proj/.env", text=secret)
    put(home, ".env", text=secret)
    put(home, "docs/handoff/notes.md")
    put(front, ".env", "certs/tls.key", text=secret)
    put(front, ".env.example", text="DEMO_KEY=\n")
    put(front, "src/api/items.ts", "src/api/sync.ts", "src/services/auth.ts",
        "vite.config.ts")
    put(back, "Sources/Web/Portal/.env.old", "Certificates/tls.key", text=secret)
    put(back, "global.json", "Sources/Infra/Persistence/Configurations/Conf.cs",
        "Sources/Micro/Accounts/Config.cs", "Tests/Base/TestBase.cs",
        "Tests/Reports.Tests/A.cs", "nginx/api.conf")
    put(tree, "Sources/Micro/Accounts/API/Controllers/User/UserController.cs",
        "Sources/Micro/Accounts/API/Controllers/Account/Models/Other.cs",
        "Sources/Micro/Metrics/API/Controllers/C.cs",
        "Sources/Micro/Metrics/Application/Queries/AQuery.cs",
        "Sources/Abstractions/Application.Base/Application.Base.csproj",
        "Sources/Micro/Reports/Reports.Application/R.csproj",
        "Sources/Micro/Reports/Reports.Application/Properties/Info.cs",
        "Sources/a/Store.cs", "Sources/a/Handler.cs", "Tests/a/StoreTests.cs",
        "Tests/Base/Matrix.cs", "Tests/Micro/Alpha/Alpha.Tests/Security/MatrixTests.cs",
        "Tests/Micro/Beta/Beta.Tests/Security/MatrixTests.cs",
        "Tests/Micro/Gamma/Gamma.Tests/Security/MatrixTests.cs",
        "Tests/V2/Context/ContextTests.cs", "local/scripts/smoke.sh", "local/scripts/gen.sh",
        "local-dev.env.template", *[f"bin/obj{n}.dll" for n in range(60)])
    put(tree_env, "local/.generated/local.env", text=secret)
    put(tree_env, "compose.yml", "docker/migrate.sh", "local/scripts/compose.sh")
    return {"HOME": home, "E": extract, "FRONT": front, "BACK": back, "WT": tree,
            "WTC": tree_env, "HANDOFF": home / "docs" / "handoff", "S": tmp_path / "scratch"}


def repo_tree(field):
    git(field["WT"], "init", "-q", "-b", "main")
    git(field["WT"], "add", "-A")
    git(field["WT"], "commit", "-q", "-m", "root")


def repo_front(field):
    git(field["FRONT"], "init", "-q", "-b", "develop")


def repo_front_env_excluded(field):
    repo_front(field)
    put(field["FRONT"], ".git/info/exclude", text=".env\n.env.*\n")


def repo_back_env_excluded(field):
    git(field["BACK"], "init", "-q", "-b", "develop")
    put(field["BACK"], ".git/info/exclude", text=".env\n.env.*\n")


def repo_both(field):
    repo_front(field)
    git(field["BACK"], "init", "-q", "-b", "develop")


def repo_nested_worktree(field):
    repo_tree(field)
    nested = field["WT"] / ".worktrees" / "x"
    put(nested, "a.txt")
    git(nested, "init", "-q", "-b", "main")
    git(nested, "add", "a.txt")


class Row(NamedTuple):
    id: str
    kind: str                       # false | real | plausible | overridden
    passes: bool
    session: str                    # the fixture directory the session's cwd is
    command: str                    # @NAME@ is the fixture path of that name
    says: str = ""                  # a denied row's reason must hold this
    setup: Callable | None = None
    guard: str = "env"
    limit: int = 0                  # WALK_LIMIT for the row (0 = the real one)


BASELINE = ('git add -A && git commit -qm "Baseline import of the supplied archive." '
            "&& git log --oneline && git ls-files | wc -l")
SCRATCH = "name outside the class"

ROWS = [
    # ── recursive code search in a tree that holds an env file ──────────────────────────
    Row("9", "false", False, "HOME",        # the searched tree really holds one at its root
        r'''cd "@FRONT@"; /usr/bin/grep -rn -i "api-version" --exclude-dir=node_modules '''
        r'''--exclude-dir=dist --exclude-dir=build --exclude-dir=.git . | head -20''',
        EXCLUDE_HINT),
    Row("12", "false", True, "HOME",
        r'''cd "@E@"; grep -rhoE "['\`]/(alpha|gamma|admin)/" front-app/src | sort | uniq -c; '''
        r'''ls back-app/Sources/Micro; find back-app/Sources/Micro -maxdepth 3 -name Dockerfile '''
        r'''| sed 's|back-app/Sources/Micro/||' | tr '\n' ' ' '''),
    Row("23", "false", True, "BACK",
        r'''cd "@E@"; grep -rhoE "['\`]/?(alpha|gamma|report)/[^'\`]*['\`]" front-app/src '''
        r'''| sort -u; echo ---; cat back-app/nginx/api.conf'''),
    Row("24", "false", True, "HOME",
        r'''cd "@BACK@/Sources/Infra/Persistence/Configurations"; /usr/bin/grep -rnE '''
        r'''"OwnsOne|OwnsMany|HasMany\(\w+ => \w+\.(Titles|Tags)\)" . | cut -c1-170'''),
    Row("25", "false", True, "HOME",
        r'''cd "@WT@/Sources" && /usr/bin/grep -rn -E "(class|record|struct) ItemKey" . | head -3'''),
    Row("26", "false", True, "E",
        r'''cd "@WT@/Sources" && grep -rln "class StaffCaller" . | xargs sed -n '''
        r''''/class StaffCaller/,/^}/p'; grep -rn "class ListItemsHandler\|class ListItems\b" . '''
        r'''| head; '''),
    Row("28", "false", True, "HOME",
        r'''cd "@WT@/Sources/Micro/Metrics/API/Controllers" && /usr/bin/grep -rn -A3 '''
        r''''Route(".*space/{spaceId' . | /usr/bin/grep "Route\|AllowedFor"; echo; '''
        r'''/usr/bin/grep -rn "AllowedForRole" . | awk -F'AllowedForRole' '{print $2}' '''
        r'''| sort | uniq -c'''),
    Row("34", "false", False, "BACK",       # the directory it searches is past the scan limit
        r'''cd "@BACK@/Tests" && /usr/bin/grep -nE 'UseInMemory|UseSqlite|class ' '''
        r'''Base/TestBase.cs; /usr/bin/grep -rnE 'Trait\(' . | head -3; /usr/bin/grep -nE '''
        r''''TestBase|Fact|Theory' Reports.Tests/*.cs | head''', EXCLUDE_HINT, limit=3),
    Row("39", "false", True, "E",
        r'''W="@WT@/Sources"; cd "$W/Abstractions/Application.Base"; ls DependencyInjection; '''
        r'''/usr/bin/grep -n 'Include=' Application.Base.csproj; /usr/bin/grep -rn '''
        r''''InternalsVisibleTo' . ../../Micro/Reports/Reports.Application/*.csproj '''
        r'''../../Micro/Reports/Reports.Application/Properties 2>/dev/null'''),
    Row("41", "false", True, "BACK",
        r'''cd "@WT@" && grep -rn 'bool IsArchived' -A10 Sources | head -14'''),
    # ── a relative path resolved against the wrong directory ────────────────────────────
    Row("21", "false", True, "HOME",
        r'''cd "@WT@/Sources/Micro/Accounts/API/Controllers/User" && sed -n '270,300p' '''
        r'''UserController.cs; grep -n "class\|public" ../../Controllers/Account/Models/Reset.cs '''
        r'''2>/dev/null || grep -rn "class Reset" ../.. | head -2'''),
    # ── "too many entries to check" ─────────────────────────────────────────────────────
    Row("7", "false", False, "BACK",        # its third search is unfiltered over an env file
        r'''cd "@BACK@" && cat global.json 2>/dev/null; grep -rhoE '<LangVersion>[^<]+' '''
        r'''--include=*.props --include=*.csproj . | sort | uniq -c; grep -rlE 'sdk:[0-9.]+' '''
        r'''--include=Dockerfile* --include=*.yml . 2>/dev/null | head -3; grep -rhoE '''
        r''''registry.example/sdk:[0-9.a-z-]+' . 2>/dev/null | sort | uniq -c | head''',
        EXCLUDE_HINT),
    Row("8", "false", False, "BACK",        # zsh hands $X to grep as ONE word: no exclude applies
        r'''cd "@BACK@" && cat global.json 2>/dev/null; X="--exclude=.env* --exclude=*.env '''
        r'''--exclude=.envrc --exclude=.netrc --exclude-dir=node_modules --exclude-dir=.git"; '''
        r'''grep -rhoE $X '<LangVersion>[^<]+' --include=*.props --include=*.csproj Sources '''
        r'''| sort | uniq -c; grep -rhoE $X 'registry.example/sdk:[0-9.a-z-]+' . | sort | head''',
        "written out in the command"),
    Row("35", "false", True, "BACK",
        r'''cd "@WT@" && /usr/bin/grep -rIn "LinkCheck\|localhost/test\|FallbackUrl" . '''
        r'''--exclude-dir=.git --exclude-dir=bin --exclude-dir=obj | /usr/bin/grep -v '''
        r'''"/Security/" | cut -c1-220 | head -25''', limit=80),
    # ── mis-parsed commands ─────────────────────────────────────────────────────────────
    Row("10", "false", True, "HOME",
        r'''cd "@FRONT@/src"; /usr/bin/grep -n "^\s*\`/\|^\s*'/\|^\s*\.\(get\|post\)" '''
        r'''api/items.ts api/sync.ts | head -40; /usr/bin/grep -rn -i '''
        r'''"reports\|/orders" --exclude='.env*' . | head'''),
    Row("14", "false", True, "HOME",
        r'''cd "@WT@"; ls .env.development.local 2>&1; cp local-dev.env.template '''
        r'''.env.development.local; git check-ignore -q .env.development.local && echo IGNORED; '''
        r'''(nice -n 19 npm run dev > /tmp/dev.log 2>&1 &); for i in $(seq 1 30); do curl -sk '''
        r'''-o /dev/null https://localhost:8000 && break; sleep 1; done; tail -8 /tmp/dev.log'''),
    Row("37", "false", True, "BACK",
        'mkdir -p "@WT@/Tests/V2/Resolution" && cd "$_" && H=$(/usr/bin/sed -n 1,9p '
        '../Context/ContextTests.cs) && cat > FilterTests.cs <<EOF\n${H}\n\nusing System;\n\n'
        'public class FilterTests\n{\n    private sealed class Body\n    {\n'
        '        public Guid SpaceId { get; init; }\n        public short Attempts { get; set; }\n'
        '    }\n}\nEOF\ncat > ScopeTests.cs <<EOF\n${H}\n\npublic class ScopeTests { }\nEOF\n'
        'cd "@WT@"; export DOTNET_ROOT=$HOME/.dotnet PATH=$HOME/.dotnet:$PATH; L=@S@/logs; '
        'nice -n 19 dotnet test Tests/V2/T.csproj -m:2 > $L/c5.log 2>&1; /usr/bin/grep -E '
        '"error CS|Passed!|Failed!" -A2 $L/c5.log | /usr/bin/cut -c1-250 | /usr/bin/head -30'),
    Row("40", "false", True, "E",
        'cd "@WT@/Sources/Micro/Metrics/Application/Queries"; python3 - <<\'EOF\'\n'
        "import glob,re\nn=0\nfor p in sorted(glob.glob('**/*Query.cs',recursive=True)):\n"
        "    s=open(p).read()\n    if 'IBound' in s: print('already',p); continue\n"
        "    open(p,'w').write(s); n+=1\nprint(n)\nEOF\n"
        "/usr/bin/grep -rn 'IBoundRequest' . | wc -l"),
    # ── example and scratch env files ───────────────────────────────────────────────────
    Row("11", "false", True, "HOME",
        r'''cd "@E@"; sed -n 1,17p front-app/src/services/auth.ts; ls -a front-app | head -40; '''
        r'''cat front-app/.env.example 2>/dev/null | sed 's/=.*/=/' ; grep -n "https\|port" '''
        r'''front-app/vite.config.ts'''),
    Row("16", "false", False, "HOME",       # the file is written by a script: contents unseen
        'cd "@WT@" && S=@S@/gstest && rm -rf "$S" && mkdir -p "$S/local/scripts" && cp '
        'local/scripts/gen.sh "$S/local/scripts/" && \\\necho "T1:"; /bin/bash '
        '"$S/local/scripts/gen.sh"; echo "exit=$? made=$([ -e $S/local/.generated/local.env ] '
        '&& echo yes || echo no)";\necho "T3 keys:"; awk -F= \'/^[A-Z]/{print $1, '
        '(length($2)>0?"nonempty":"EMPTY")}\' "$S/local/.generated/local.env"; rm -rf "$S"',
        SCRATCH),
    Row("29", "false", False, "HOME",
        r'''cd "@WT@" && S=@S@; /usr/bin/grep -o '\${[A-Z_]*:?' compose.yml | sed '''
        r''''s/\${//;s/:?//' | sort -u > $S/vars.txt; (while read v; do [ "$v" = DEMO_USER ] || '''
        r'''echo "$v=dummy"; done < $S/vars.txt) > $S/nodemo.env; docker compose -f compose.yml '''
        r'''--env-file $S/nodemo.env config --quiet 2>&1 | head -3; (cat $S/nodemo.env; echo '''
        r'''"DEMO_USER=") > $S/emptydemo.env''', SCRATCH),
    Row("32", "false", False, "HANDOFF",
        r'''S="$TMPDIR/gentest"; rm -rf "$S"; mkdir -p "$S/local/.generated" && cp '''
        r'''"@WT@/local/scripts/gen.sh" "$S/local/" && printf 'A=1\nB=2\n' > '''
        r'''"$S/local/.generated/local.env" && bash "$S/local/gen.sh" --regen B; awk -F= '''
        r''''{print $1, length($2)}' "$S/local/.generated/local.env"; rm -rf "$S"''', SCRATCH),
    # ── a value handed to a command by env-get, whose output is a count ─────────────────
    Row("33", "false", True, "HOME",
        r'''cd "@WTC@" && local/scripts/compose.sh --profile seed run --rm seeder > '''
        r'''$TMPDIR/seed.out 2>&1; echo "exit=$?"; for i in 1 2 3; do sleep 10; n=$(env-get '''
        r'''local/.generated/local.env DB_PASSWORD -- $TMPDIR/sql.sh "SELECT COUNT(*) FROM T '''
        r'''WHERE X='1'"); echo "t+${i}0s rows=$n"; [ "$n" = 780 ] && break; done'''),
    # ── an internal error on a nested substitution, reported as "could not be parsed" ────
    Row("17", "overridden", True, "HOME",
        'cd "@WT@" && S=@S@/gstest && rm -rf "$S" && mkdir -p "$S/local/scripts"; '
        'G="$S/local/.generated"; F="$G/local.env"\necho "T2:"; /bin/bash '
        '"$S/local/scripts/old.sh" >/dev/null; env-get --keys "$F" | tr \'\\n\' \' \'; echo\n'
        'h() { env-get "$F" DB_PASSWORD -- sh -c \'printf %s "$DB_PASSWORD" | shasum | cut '
        '-c1-8\'; }\nbefore=$(h); echo "T3:"; /bin/bash "$S/local/scripts/new.sh" '
        '--add-missing; echo "exit=$? same=$([ "$before" = "$(h)" ] && echo yes || echo no) '
        'mode=$(stat -f %Lp "$F")"\nenv-get "$F" REDIS_PASSWORD -- sh -c \'echo '
        '"len=${#REDIS_PASSWORD}"\'; rm -rf "$S"'),
    # ── searches for secret NAMES across every file of a tree ───────────────────────────
    # 30 ran in a tree that holds an env file and still denies. 27 and 42 ran in one that
    # holds none: their block came from the session's directory, which did. The same search
    # in a tree that holds one is denied (DIRECTORY_DENY in test_env_guard_relaxations.py).
    Row("27", "plausible", True, "BACK",
        r'''cd "@WT@" && /usr/bin/grep -rnE "ClientSecret|GetClientSecret|CLIENT_SECRET_KEY" '''
        r'''--include='*' -I . 2>/dev/null | /usr/bin/grep -vE "/bin/|/obj/|\.git/" '''
        r'''| cut -c1-220'''),
    Row("30", "plausible", False, "WTC",
        r'''cd "@WTC@" && /usr/bin/grep -rn "__LocalMock\|local-mock-marker" --include='*' '''
        r'''-I . 2>/dev/null | /usr/bin/grep -v "/bin/\|/obj/" | cut -c1-180; echo ---; '''
        r'''/usr/bin/grep -n "^sql()\|sqlcmd" docker/migrate.sh | head''', EXCLUDE_HINT),
    Row("42", "plausible", True, "BACK",
        r'''cd "@WT@" && /usr/bin/grep -rn "ClientSecret" --include='*.cs' --include='*.json' '''
        r'''--include='*.yml' . | /usr/bin/grep -v "/obj/\|/bin/"; /usr/bin/grep -rn "fragment" '''
        r'''. --include='*' 2>/dev/null | /usr/bin/grep -v "/obj/" | cut -c1-150'''),
    # ── git staging ─────────────────────────────────────────────────────────────────────
    Row("1", "overridden", False, "HANDOFF",
        r'''cd "@E@"; for d in front-app back-app; do (cd "$d" && git init -q -b develop && '''
        r'''git add -A && git commit -qm "Baseline import of the supplied $d archive." && echo '''
        r'''"$d: $(git rev-parse --short HEAD) $(git ls-files | wc -l) files"); done''',
        "could not check"),
    Row("2", "overridden", False, "HANDOFF",
        r'''F="@FRONT@"; git -C "$F" init -q -b develop && git -C "$F" add -A && git -C "$F" '''
        r'''commit -qm "Baseline import of the supplied archive." && git -C "$F" log --oneline''',
        "creates a repository"),
    Row("3", "real", False, "HANDOFF", f'cd "@FRONT@" && {BASELINE}', "would stage .env",
        setup=repo_front),
    Row("5", "real", False, "E", f'cd "@FRONT@" && {BASELINE}', "certs/tls.key",
        setup=repo_front_env_excluded),
    Row("6", "real", False, "E", f'cd "@BACK@" && {BASELINE}', "Certificates/tls.key",
        setup=repo_back_env_excluded),
    # The exclude it writes is not there when the guard looks, and it would not have covered
    # the key file (row 5): left blocked, with the form to rerun.
    Row("4", "false", False, "HANDOFF",
        r'''cd "@E@"; for d in front-app back-app; do printf '.env\n.env.*\n' >> '''
        r'''"$d/.git/info/exclude"; done; cd front-app && ''' + BASELINE,
        "its own command", setup=repo_both),
    Row("19", "false", True, "HOME",
        r'''W="@WT@"; cd "$W"; /usr/bin/git add Sources/a/Store.cs Tests/a/StoreTests.cs && '''
        r'''/usr/bin/git commit -q -m "Keep one live state per connection so a new URL replaces '''
        r'''the previous one." && /usr/bin/git add Sources/a/Handler.cs && /usr/bin/git commit '''
        r'''-q -m "Log the provider's name through placeholders." && /usr/bin/git log '''
        r'''--oneline -3 && /usr/bin/git status --short | head''', setup=repo_tree),
    Row("20", "false", True, "HOME",
        r'''W="@WT@"; cd "$W" && git branch --show-current && git add Sources/a/Store.cs '''
        r'''Tests/a/StoreTests.cs && git commit -q -m "Keep the declaration of inherited '''
        r'''actions." && git add Sources/a/Handler.cs && git commit -q -m "Send the id as a '''
        r'''parameter." && git log --oneline -4 && git status --short | wc -l''',
        setup=repo_tree),
    Row("38", "false", True, "WTC",
        r'''cd "@WT@" && git add Tests/Base/Matrix.cs Tests/Micro/{Alpha/Alpha,Beta/Beta}.Tests/Security/'''
        r'''MatrixTests.cs && git commit -q -m "Extend the isolation matrix." && git log '''
        r'''--oneline -1 && git status --short | wc -l; for f in Gamma/Gamma Beta/Beta; do echo "$f '''
        r'''$(/usr/bin/grep -n '\["\(spa\|api\|admin\)' Tests/Micro/$f.Tests/Security/'''
        r'''MatrixTests.cs | cut -d: -f1 | tr '\n' ' ')"; done''', setup=repo_tree),
    Row("lead", "false", True, "WT",
        r'''W=.worktrees/x && git -C $W commit -q -m "Fix the thing."''',
        setup=repo_nested_worktree),
    # ── process table ───────────────────────────────────────────────────────────────────
    Row("18", "false", True, "HOME",
        r'''cd "@WT@" && /usr/bin/grep -n 'ps\b\|ps -a\|Health\|^check\|exited' '''
        r'''local/scripts/smoke.sh | cut -c1-170''', guard="process"),
    Row("22", "real", False, "HANDOFF",
        r'''docker exec db sh -c 'grep -m2 -oE "/[^ ]*(a|b)[^ ]*" /proc/1/maps; echo "---"; '''
        r'''tr "\0" " " < /proc/1/cmdline | cut -c1-60' ''', "Process listings",
        guard="process"),
]


def reason(row: Row, field: dict[str, Path]) -> str | None:
    """The deny reason the row's guard gives its command, or None when it lets it run."""
    command = row.command
    for name, path in field.items():
        command = command.replace(f"@{name}@", str(path))
    payload = {"tool_name": "Bash", "tool_input": {"command": command},
               "cwd": str(field[row.session])}
    output = (process_table_guard if row.guard == "process" else guard).decide(payload)
    decision = (output or {}).get("hookSpecificOutput", {})
    if decision.get("permissionDecision") != "deny":
        return None
    assert MARKER not in decision["permissionDecisionReason"], "a value reached the reason"
    return decision["permissionDecisionReason"]


@pytest.mark.parametrize("row", ROWS, ids=[f"{row.kind}-{row.id}" for row in ROWS])
def test_each_field_row_gets_the_declared_verdict(row, field, monkeypatch):
    if row.limit:
        monkeypatch.setattr(guard, "WALK_LIMIT", row.limit)
    if row.setup:
        row.setup(field)
    text = reason(row, field)
    assert (text is None) is row.passes, text
    if text is not None:
        assert row.says and row.says in text, text


def test_the_measure():
    """The counts the table declares (each row is checked by the test above)."""
    kinds = Counter(row.kind for row in ROWS)
    assert kinds == {"false": 29, "real": 4, "plausible": 3, "overridden": 3}
    passing = sorted(row.id for row in ROWS if row.passes and row.kind == "false")
    blocked = sorted(row.id for row in ROWS if not row.passes and row.kind == "false")
    # Left blocked: 9 and 7 search a tree that holds an env file without excluding it, 8
    # keeps its excludes in a shell variable, 34 searches a directory past the scan limit,
    # 4 stages before its exclude exists, and 16, 29 and 32 read a scratch file they named
    # inside the class.
    assert blocked == sorted(["9", "7", "8", "34", "4", "16", "29", "32"])
    assert len(passing) == 21                       # of the 29 false rows
    # Every real row still denies. Of the plausible ones, the search over a tree that holds
    # an env file denies; the two over a tree that holds none pass. Of the overridden ones,
    # the two staging rows deny and the internal error (17) is gone.
    assert all(not row.passes for row in ROWS if row.kind == "real")
    assert [row.id for row in ROWS if row.kind == "plausible" and not row.passes] == ["30"]
    assert [row.id for row in ROWS if row.kind == "overridden" and row.passes] == ["17"]


def test_no_row_carries_a_real_path_or_a_value():
    for row in ROWS:
        assert "/Users/" not in row.command and "/home/" not in row.command
        assert MARKER not in row.command


# ── A loop variable is each word of its list; globs past the budget are judged blind ──────
_DOT = "." + "env"


@pytest.fixture
def loop_cwd(tmp_path):
    (tmp_path / _DOT).write_text("K=inert\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    return str(tmp_path)


@pytest.mark.parametrize("command", [
    f'for W in {_DOT}; do cat "$W"; done',
    f'W=notes.txt; for W in {_DOT}; do cat "$W"; done',           # the assignment before it loses
    f'for W in notes.txt {_DOT}; do head -1 $W; done',            # not only the first word
    'for f in .e*; do cat "$f"; done',
    f'select W in {_DOT}; do cat $W; done',
    f'for W ({_DOT}) cat $W',                                     # zsh
    f'for W in "{_DOT}"; do cat "$W"; done',
    "for W in '.e'nv; do cat $W; done",                           # quoted in part
    f'for W in ./{_DOT}; do cat $W; done',
    f'for   W   in   notes.txt   {_DOT} ; do head -1 $W; done',
    f'for W in notes.txt; do cat "$W"; done; for W in {_DOT}; do cat $W; done',
    f'for W in {" ".join(["notes.txt"] * 20_000)} {_DOT}; do cat $W; done',
])
def test_loop_over_a_protected_word_is_denied(loop_cwd, command):
    assert guard.bash_verdict(command, loop_cwd)


@pytest.mark.parametrize("command", [
    'for W in notes.txt; do cat "$W"; done',
    'for f in *.txt; do cat "$f"; done',
    'for f in *; do cat "$f"; done',
    'for i in 1 2 3; do echo $i; done',
    'for f in src/*.py; do wc -l "$f"; done',
    f'for W in {_DOT}; do echo hi; done',                         # named, never read
    f'for W in {_DOT}; do cat "$X"; done',                        # another variable
    f'for W in {_DOT}.example; do cat "$W"; done',
    'W=notes.txt; while read W; do cat "$W"; done < list',
])
def test_ordinary_loop_stays_allowed(loop_cwd, command):
    assert guard.bash_verdict(command, loop_cwd) is None


def test_glob_budget_denies_a_command_with_more_globs(tmp_path):
    """Up to names.GLOB_BUDGET globs are expanded against the disk; a command with more is
    denied and told to split, whatever the later globs are named, so the cost stops growing
    and the answer stays closed. Each command starts with a full budget."""
    from shell.env import names
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    cwd = str(tmp_path)                                           # holds no protected file
    within = " ".join(f"a{n}*" for n in range(names.GLOB_BUDGET - 1))
    beyond = " ".join(f"a{n}*" for n in range(names.GLOB_BUDGET))
    assert guard.bash_verdict("cat .e*", cwd) is None             # the disk says: no match
    assert guard.bash_verdict(f"ls {within} && cat .e*", cwd) is None
    assert guard.bash_verdict(f"ls {within} && cat *.txt", cwd) is None   # 64 globs, clean tail
    assert guard.bash_verdict(f"ls {within} {within} && cat *.txt", cwd) is None  # 64 distinct
    assert guard.bash_verdict(f"ls {beyond} && cat .e*", cwd) == names.GLOB_BUDGET_MESSAGE
    assert guard.bash_verdict(f"ls {beyond} && cat *.txt", cwd) == names.GLOB_BUDGET_MESSAGE
    assert str(names.GLOB_BUDGET) in names.GLOB_BUDGET_MESSAGE
    assert guard.bash_verdict("cat .e*", cwd) is None             # a new command, a new budget


@pytest.mark.parametrize("command", [
    "cat {many} li*",
    "ls {many} > /dev/null; cat li*",
])
def test_a_link_to_a_protected_file_is_not_reached_past_the_glob_budget(tmp_path, command):
    """Past the budget a glob was judged by its name alone, so one matching a link to a
    protected file passed. `li*` alone is denied by what it matches; so is the long form."""
    from shell.env import names
    (tmp_path / _DOT).write_text("K=inert\n", encoding="utf-8")
    (tmp_path / "link").symlink_to(tmp_path / _DOT)
    cwd = str(tmp_path)
    many = " ".join(f"z{n}*.txt" for n in range(names.GLOB_BUDGET + 6))
    assert guard.bash_verdict("cat li*", cwd)                     # the control: the disk says so
    assert guard.bash_verdict(command.format(many=many), cwd) == names.GLOB_BUDGET_MESSAGE


def test_long_env_get_line_is_judged_in_linear_time(tmp_path):
    """The writer-then-target search ran in the square of the line length inside one regex
    call no timer interrupts: 80 KB took about 20 s. The bound here is half of that."""
    import json
    import sys
    import time
    payload = {"tool_name": "Bash", "cwd": str(tmp_path),
               "tool_input": {"command": "env-get x " * 8_000}}
    started = time.monotonic()
    subprocess.run([sys.executable, str(Path(guard.__file__))], input=json.dumps(payload),
                   capture_output=True, text=True, timeout=60, cwd=tmp_path)
    assert time.monotonic() - started < 10
