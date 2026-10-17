"""
Location: pact-plugin/tests/test_env_guard_review_rows.py
Summary: The repro inputs of the adversarial review of hooks/env_guard.py, as table rows,
         and the boundary of the reader they led to (hooks/shell_words.py read_script).
           ROWS     each command the review found allowed that reads, searches or stages a
                    protected file. A row is (finding, probe id, directory, main's verdict,
                    command); every one must be denied now. "DENY" rows are shapes main
                    refused and the relaxed checks let through; "ALLOW" rows got through
                    on main as well.
           KEPT     the wrongly blocked shapes the relaxed checks let through, which closing
                    the rows above must not block again.
           READER   what read_script returns, what it refuses, and that it refuses fast.
Used by: the plugin suite. The rules themselves are tabled in test_env_guard_relaxations.py;
         the process-listing rows of the same review are in test_process_table_guard.py.

The fixture tree holds inert files only (MARKER). No real env or credential file is opened.
"""
from __future__ import annotations

import os
import random
import subprocess
import time

import pytest

import env_guard as guard
import process_table_guard
import shell_words
from project_tasks_support import GIT_ENV

MARKER = "inert-marker-7c2a"
SECRET = f"KEY={MARKER}\nDEMO_KEY={MARKER}\n"


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """home/ and w/: proj (env files at its root, under conf/ and deep/a/, and link.txt, a
    link to .env), clean (none), other (one at its root and one under x/), repo (a repository
    with an untracked env file), cleanrepo (a repository without one), `sp ace` (one)."""
    root = tmp_path_factory.mktemp("review").resolve()

    def put(rel: str, text: str = "KEY plain text\n") -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    for name in ("home/.env", "w/proj/.env", "w/proj/.env.production", "w/proj/deep/a/.env.local",
                 "w/proj/conf/app.env", "w/other/.env", "w/other/x/.env", "w/repo/.env",
                 "w/sp ace/.env"):
        put(name, SECRET)
    for name in ("w/proj/notes.txt", "w/proj/src/code.txt", "w/proj/src/sub/x.txt",
                 "w/proj/.env.example", "w/proj/.env.sample", "w/clean/a.txt", "w/clean/sub/b.txt",
                 "w/clean/x/c.txt", "w/repo/README.md", "w/repo/ok.txt", "w/cleanrepo/README.md",
                 "w/cleanrepo/new.txt", "w/sp ace/a.txt"):
        put(name)
    (root / "w/proj/link.txt").symlink_to(".env")
    for repo in ("w/repo", "w/cleanrepo"):
        for args in (["init", "-q", "-b", "main"], ["add", "README.md"],
                     ["commit", "-q", "-m", "root"]):
            subprocess.run(["git", "-C", str(root / repo), *args], check=True,
                           capture_output=True, env={**os.environ, **GIT_ENV})
    return root


@pytest.fixture(autouse=True)
def session(tree, monkeypatch):
    monkeypatch.setenv("HOME", str(tree / "home"))
    monkeypatch.delenv("CDPATH", raising=False)


def reason(command: str, tree, where: str = "proj") -> str | None:
    payload = {"tool_name": "Bash", "tool_input": {"command": command.replace("{T}", str(tree))},
               "cwd": str(tree / "w" / where)}
    decision = (guard.decide(payload) or {}).get("hookSpecificOutput", {})
    if decision.get("permissionDecision") != "deny":
        return None
    assert MARKER not in decision["permissionDecisionReason"], "a value reached the reason"
    return decision["permissionDecisionReason"]


def nest(depth: int, inner: str, opener: str = "$(") -> str:
    return "echo " + opener * depth + inner + ")" * depth


ROWS = [
    # R1: a `<<` that is no heredoc, or a delimiter read short, hid every later line; a body
    # fed to a program that runs it was dropped as text.
    ("R1", "h1", "proj", "DENY", "echo $((1<<2))\ncat .env"),
    ("R1", "h2", "proj", "DENY", "python3 -c '\nmask = 1 << 4\nprint(mask)\n'\ncat .env"),
    ("R1", "h3", "proj", "DENY", 'echo "multi\nline <<EOF"\ncat .env\nEOF'),
    ("R1", "h4", "proj", "DENY", "x=$((1<<4))\ngrep -rn KEY ."),
    ("R1", "h4b", "proj", "DENY", "x=$((1<<4))\ncat link.txt"),
    ("R1", "h18", "proj", "DENY", "echo $((1<<2))\ngit -C {T}/w/repo add -A"),
    ("R1", "h9", "proj", "DENY", 'echo "a\\"b <<EOF"\ncat .env\nEOF'),
    ("R1", "h26", "proj", "DENY", "cat <<EOF+1\nx\nEOF+1\ncat .env\nEOF"),
    ("R1", "h28", "proj", "DENY", 'cat <<E"O"F\nx\nEOF\ncat .env\nE'),
    # A `!` delimiter is read now (it was refused): a body fed to a shell is still judged, and
    # so is the line after the closing `!` (KEPT bang-body: the body of `cat` is data).
    ("R1", "h30", "proj", "DENY", "bash <<!\ncat .env\n!\nls"),
    ("R1", "h31", "proj", "DENY", "cat <<!\nx\n!\ncat .env"),
    ("R1", "h5", "proj", "DENY", "tcsh <<EOF\ncat .env\nEOF"),
    ("R1", "h6", "proj", "DENY", "make -f - <<EOF\nall:\n\tcat .env\nEOF"),
    # R3: a value that holds a flag was not spelled out, so the file beside it went unseen.
    ("R3", "v1", "proj", "DENY", 'F="-n .env"; cat $F'),
    ("R3", "v2", "proj", "DENY", 'true; F="-n .env"; eval "cat $F"'),
    ("R3", "v3", "proj", "DENY", 'true; F="-n .env"; bash -c "cat $F"'),
    ("R3", "v4", "proj", "DENY", 'true; F="-- .env"; cat ${=F} 2>/dev/null || cat $F'),
    # R4: a cd after `&&` was taken as made though the step before it can fail.
    ("R4", "d1", "proj", "DENY", "X=$(false) && cd {T}/w/clean; grep -rn KEY ."),
    ("R4", "d1b", "proj", "DENY", "X=$(false) && cd {T}/w/clean; cat link.txt"),
    ("R4", "d2", "proj", "DENY", "> /nonexistent/x && cd {T}/w/clean; grep -rn KEY ."),
    ("R4", "d2b", "proj", "DENY", "< /nonexistent/x && cd {T}/w/clean; grep -rn KEY ."),
    ("R4", "d40", "proj", "DENY", "(false) && cd {T}/w/clean; grep -rn KEY ."),
    ("R4", "d40b", "proj", "DENY", "(cd src && false) && cd {T}/w/clean; cat link.txt"),
    ("R4", "d41", "proj", "DENY", "((0)) && cd {T}/w/clean; grep -rn KEY ."),
    ("R4", "d41b", "proj", "DENY", "timeout && cd {T}/w/clean; grep -rn KEY ."),
    # R5: a substitution inside a substitution; one in the program's place.
    *[("R5", f"dn{depth}", "proj", "DENY", nest(depth, "cat .env")) for depth in (2, 3, 4)],
    *[("R5", f"de{depth}", "proj", "DENY", nest(depth, "echo cat .env")) for depth in (2, 3, 4)],
    ("R5", "dq2", "proj", "DENY", 'echo "$("$(echo cat)" .env)"'),
    ("R5", "dq4", "proj", "DENY", "echo $(`echo cat` .env)"),
    ("R5", "dq5", "proj", "DENY", "echo $( $(echo cat) .env )"),
    ("R5", "dq6", "proj", "DENY", "echo $($(echo cat .env))"),
    ("R5", "dq9", "proj", "DENY", "echo $( $( $( $(echo cat .env))))"),
    # R6: where the directory is unknown a glob was tried against five sample names only.
    ("R6", "s8", "proj", "DENY", 'cd "$NOWHERE_SET" 2>/dev/null; cat .env.p*'),
    ("R6", "s8b", "proj", "DENY", 'cd "$PWD" && cat .env.p*'),
    # R10: a move the tracker does not model; a variable set again under a computed name.
    ("R10", "d3", "proj", "DENY",
     "if false; then bash <<X\nfi\nX\ncd {T}/w/clean\nfi; grep -rn KEY ."),
    ("R10", "d4", "proj", "DENY",
     'true; Y="cd {T}/w/proj"; cd {T}/w/clean; eval "$Y"; grep -rn KEY .'),
    ("R10", "d5", "proj", "DENY",
     'true; Y="cd {T}/w/proj"; trap "$Y" DEBUG; cd {T}/w/clean; grep -rn KEY .'),
    ("R10", "d6", "proj", "DENY", "cd {T}/w/clean; $(echo cd) {T}/w/proj; grep -rn KEY ."),
    ("R10", "d6b", "proj", "DENY", "true; C=cd; cd {T}/w/clean; $C {T}/w/proj; grep -rn KEY ."),
    ("R10", "d7", "proj", "DENY", "cd {T}/w/clean && env -C {T}/w/proj grep -rn KEY ."),
    ("R10", "d8", "proj", "DENY",
     "read CDPATH <<< {T}/w/other; cd {T}/w/clean; cd x; grep -rn KEY ."),
    ("R10", "d8b", "proj", "DENY",
     "cd {T}/w/clean; for CDPATH in {T}/w/other; do :; done; cd x; grep -rn KEY ."),
    ("R10", "d10", "proj", "DENY",
     "cd {T}/w/clean && git -C {T}/w/proj grep --no-index -n KEY ."),
    ("R10", "d15", "proj", "DENY", "cd {T}/w/proj/src(:h) && grep -rn KEY ."),
    ("R10", "d17", "proj", "DENY",
     "cd {T}/w/clean; . /dev/stdin <<< 'cd {T}/w/proj'; grep -rn KEY ."),
    ("R10", "s1b", "proj", "DENY", "cd {T}/w/clean && grep -rn -eKEY ../proj"),
    ("R10", "g1", "repo", "DENY",
     'W="{T}/w/cleanrepo"; x=W; read "$x" <<< "{T}/w/repo"; cd "$W" && git add -A'),
    ("R10", "g1b", "repo", "DENY",
     'W="{T}/w/cleanrepo"; x=W; export "$x={T}/w/repo"; cd "$W" && git add -A'),
    ("R10", "d42", "proj", "DENY", "cd {T}/w/clean; noglob cd {T}/w/proj; grep -rn KEY ."),
    ("R10", "d42b", "proj", "DENY", "cd {T}/w/clean; repeat 1 cd {T}/w/proj; grep -rn KEY ."),
    # P1: text inside a comment, or a `#` that opens none, hid the next command.
    ("P1", "h7", "proj", "ALLOW", "ls # <<X\ncat .env\nX"),
    ("P1", "h8", "proj", "ALLOW", "ls # <<'X'\ncat .env\nX"),
    ("P1", "c10", "proj", "ALLOW", "true # $(\ncat .env # )"),
    ("P1", "c11", "proj", "ALLOW", "true # don't\necho `cat .env` # isn't"),
    ("P1", "c12", "proj", "ALLOW", "echo $'a\\'b'; cat .env; echo \\'"),
    ("P1", "c13", "proj", "ALLOW", "echo ${x/ #/y}; cat .env"),
    ("P1", "c14", "proj", "ALLOW", "echo a\\ #b; cat .env"),
    # P2: a search root the shell computes from $PWD or $HOME, or matches by a bracket class.
    # Written in two pieces: the purity scan reads the variable's name and `:` as a labelled value.
    ("P2", "s2", "clean", "ALLOW", "grep -rn KEY $PWD" ":h/other"),
    ("P2", "s2b", "clean", "ALLOW", "grep -rn KEY $PWD[1,-6]other"),
    ("P2", "s2c", "clean", "ALLOW", "grep -rn KEY $HOME:h/w/other"),
    ("P2", "x4", "clean", "ALLOW", "grep -rn KEY {T}/w/[^x]ther"),
    ("P2", "x5", "clean", "ALLOW", "grep -rn KEY {T}/w/[[:alpha:]]ther"),
    # P3, the two staging shapes: -A inside a cluster, and git's other name for add.
    ("P3", "g3", "repo", "ALLOW", "git add -Av"),
    ("P3", "g3b", "repo", "ALLOW", "git stage -A"),
    # Denied as a side effect of the rules above.
    ("R5", "k4", "proj", "ALLOW", "$(echo cat) .env"),
    ("R5", "dl2", "proj", "ALLOW", nest(2, "cat link.txt")),
    ("R5", "n3", "proj", "ALLOW", nest(150, "cat link.txt")),
    ("R6", "x3", "proj", "ALLOW", "cat .[^x]nv"),
    ("R4", "g4", "repo", "ALLOW", "X=$(false) && cd {T}/w/cleanrepo; git add -A"),
    ("R10", "s1", "clean", "ALLOW", "grep -rn -eKEY {T}/w/other"),
    ("R1", "h21", "proj", "ALLOW",
     'awk -f /dev/stdin <<EOF\nBEGIN{while((getline l < ".env")>0) print l}\nEOF'),
    ("R1", "h27", "proj", "ALLOW", "cat <<'EOF'x\na\nEOFx\ncat .env\nEOF"),
    # Found while closing them: the same holes by another spelling.
    ("R1", "m3", "proj", "ALLOW", "cat <<EOF > >(\ncat link.txt\nEOF\nbash\n)"),
    ("R1", "m4", "proj", "ALLOW", "cat <<EOF |\ncat link.txt\nEOF\nsh"),
    ("R1", "m5", "proj", "ALLOW", "{\nbash\n} <<EOF\ncat link.txt\nEOF"),
    ("R1", "m7", "proj", "ALLOW", '(( "1" <<EOF ))\ncat link.txt\nEOF'),
    ("R1", "m10", "proj", "ALLOW", "cat <<EOF\\\nX\ncat link.txt\nEOFX"),
    ("R1", "m37", "proj", "ALLOW", "bash <<EOF\ncat link.txt"),
    ("R1", "m33", "proj", "ALLOW", "W=link.txt; bash <<EOF\ncat $W\nEOF"),
    ("R1", "m1", "proj", "ALLOW",
     "cat <<__repact_body_9__\ncd {T}/w/clean\n__repact_body_9__\ngrep -rn KEY ."),
    ("R1", "m2", "proj", "ALLOW", "bash -c 'cat <<__repact_heredoc_0__\ncd {T}/w/clean\n"
                                  "__repact_heredoc_0__\ngrep -rn KEY .'"),
    ("R4", "m14", "proj", "ALLOW", "X=1 2>/nonexistent/x/y && cd {T}/w/clean; cat link.txt"),
    ("R4", "m16", "proj", "ALLOW", "true && cd {T}/w/clean && false; cat link.txt"),
    ("R10", "m17", "proj", "ALLOW", "{T}/w/clean; cat link.txt"),
    ("R10", "m24b", "clean", "ALLOW", "env -iC {T}/w/proj grep -rn KEY ."),
    ("R6", "m21b", "clean", "ALLOW", "cd /nonexistent/zz && cat x*.env"),
    ("P1", "m12", "proj", "ALLOW", "echo a \\\n# x\ncat link.txt"),
    ("P1", "m28", "proj", "ALLOW", "cat $'li\\x6ek.txt'"),
    ("P1", "m29", "proj", "ALLOW", "$'c\\x61t' link.txt"),
    ("P3", "m41", "repo", "ALLOW", "git add -Af"),
]

KEPT = [
    # Rows of the review where main denies and nothing is read: the relaxations themselves.
    ("bang-body", "proj", "cat <<!\ncat .env\n!\nls"),
    ("ds1", "proj", "cd {T}/w/clean && grep -rn KEY ."),
    ("d41e", "proj", "X=1 && cd {T}/w/clean; grep -rn KEY ."),      # a literal assignment
    ("d16", "proj", "cd {T}/w/clean; source {T}/w/clean/a.txt 2>/dev/null; grep -rn KEY ."),
    ("x20", "proj", "grep -rn --exclude-dir=a KEY deep"),
    ("t1", "proj", "cat .env.example"),
    ("t28", "proj", "cat ./.env.example ../proj/.env.sample"),
    # Intended: the destination NAME is protected too, so the copy is as guarded as its source.
    ("t4", "proj", "cp notes.txt .env x.env"),
    ("e2", "proj", 'echo "\\\\\\$(cat .env)"'),                     # an escaped `$(` is text
    ("s32", "clean", "grep -rn KEY .. --exclude-dir=proj --exclude-dir=repo "
                     "--exclude-dir='sp ace' --exclude-dir=x --exclude-dir=other"),
    # Everyday shapes the reader must still take as they are.
    ("a1", "cleanrepo",
     "git commit -m \"$(cat <<'EOF'\nfix: stop cat .env from leaking\n\nit's done\nEOF\n)\""),
    ("a2", "clean", "cat > notes.md <<'EOF'\nRun `cat .env` to see $(secrets)\nEOF"),
    ("a3", "clean", "python3 - <<'EOF'\nimport os\nx = 1 << 4\nprint(\"it's\", x)\nEOF"),
    ("a5", "clean", "for ((i=0; i<3; i++)); do echo $i; done; echo $((1<<2))"),
    ("a7", "clean", "psql <<EOF\nselect 'it''s' from t;\nEOF"),
    ("a9", "clean", "ssh host <<'EOF'\nls -la\nEOF"),
    ("a11b", "clean", "source venv/bin/activate 2>/dev/null; grep -rn KEY ."),
    ("a12", "clean", "kubectl apply -f - <<EOF\nkind: X\nenv:\n  - name: A\nEOF"),
    ("a14", "clean", "echo 'it'\"'\"'s' # done"),
    ("a16", "clean", "awk '{print $1}' file.txt; echo \"a # b\"; echo a#b; ls # comment"),
    ("a17", "clean", "cat <<'EOF' > script.sh\n#!/bin/bash\ncat .env\nEOF"),
    ("a20", "clean", "$PYTHON -m pytest tests/ -q"),
    ("a22", "clean",
     "gh pr create --title x --body \"$(cat <<'EOF'\n## Summary\n- reads `.env` no more\nEOF\n)\""),
    ("a24", "clean", "cat <<EOF\nuse \\$(cat .env) carefully\nEOF"),
    ("a28", "clean", "[[ -f a.txt ]] && echo yes || echo no"),
]

# ── The review of the reader itself: what it still let through, and what it newly blocked ─
SYSTEM = "awk '{system($0)}'"
BODY = " <<EOF\ncat .env\nEOF"
# (finding, id, directory, command): allowed before the fix, denied now. Each prints the
# fixture's inert value when run in bash 3.2 or zsh, unless its note says otherwise.
THIRD = [
    # T1: a heredoc fed to a program that runs, or opens, what its stdin names was text.
    ("T1", "b1", "proj", SYSTEM + BODY),
    ("T1", "b1q", "proj", SYSTEM + " <<'EOF'\ncat .env\nEOF"),
    ("T1", "b2", "proj", "perl -ne 'system $_'" + BODY),
    ("T1", "b2q", "proj", "perl -ne 'system $_' <<'EOF'\ncat .env\nEOF"),
    ("T1", "b3", "repo", "git -c alias.r='!sh' r" + BODY),
    ("T1", "b5", "proj", "python3 -c 'import os,sys;os.system(sys.stdin.read())'" + BODY),
    ("T1", "d4", "proj", "head -c 0 <<EOF; " + SYSTEM + " <<EOF2\nx\nEOF\ncat .env\nEOF2"),
    # gawk, nawk, mawk, gsed, yq and timeout are not installed where these were run, and its
    # sort has no --files0-from and started no program: there the verdict is all that was seen.
    *[("T1", f"as-{name}", "proj", f"{name} '{{system($0)}}'" + BODY)
      for name in ("gawk", "nawk", "mawk", "/usr/bin/awk")],
    *[("T1", f"past-{runner.split()[0]}{len(runner)}", "proj", f"{runner} {SYSTEM}" + BODY)
      for runner in ("env", "env A=1", "nice -n 5", "command", "time", "timeout 5", "nohup",
                     "exec", "rtk proxy")],
    ("T1", "variable", "proj", "A=awk; $A '{system($0)}'" + BODY),
    ("T1", "quoted-name", "proj", "'awk' '{system($0)}'" + BODY),
    ("T1", "piped", "proj", "cat <<EOF | " + SYSTEM + "\ncat .env\nEOF"),
    ("T1", "piped2", "proj", "cat <<EOF | tr a a | " + SYSTEM + "\ncat .env\nEOF"),
    ("T1", "sed-e", "proj", "gsed -n e" + BODY),
    ("T1", "sed-f", "proj", "sed -f - notes.txt <<EOF\n1r .env\nEOF"),
    ("T1", "sed-f2", "proj", "sed -f /dev//stdin notes.txt <<EOF\n1r .env\nEOF"),
    ("T1", "awk-fd", "proj",
     'awk -f /dev/fd/3 3<<EOF\nBEGIN{while((getline l < ".env")>0) print l}\nEOF'),
    ("T1", "awk-name", "proj", "awk '{while((getline l < $2)>0) print l}' <<EOF\nf .env\nEOF"),
    ("T1", "awk-link", "proj", "awk '{while((getline l < $0)>0) print l}' <<EOF\nlink.txt\nEOF"),
    ("T1", "py-open", "proj",
     "python3 -c 'import sys;print(open(sys.stdin.read().strip()).read())' <<EOF\n.env\nEOF"),
    ("T1", "py-json", "proj", "python3 -c 'import sys,json;print(open(json.load(sys.stdin)"
                              '["f"]).read())\' <<EOF\n{"f": ".env"}\nEOF'),
    ("T1", "py-file", "proj", "python3 src/run.py" + BODY),          # not run: no such script
    ("T1", "git-paths", "repo", "git hash-object --stdin-paths <<EOF\n.env\nEOF"),   # a hash
    ("T1", "commit-paths", "repo",                                    # refused by git here
     "git commit --pathspec-from-file=- -m x <<EOF\n.env\nEOF"),
    ("T1", "names0", "proj", "tr '\\n' '\\0' <<EOF | sort --files0-from=-\n.env\nEOF"),
    ("T1", "names-short", "proj", "tr '\\n' '\\0' <<EOF | sort --files0=-\n.env\nEOF"),
    ("T1", "sort-program", "proj", "sort -S 1b --compress-prog=sh" + BODY),
    ("T1", "yq", "proj", "yq 'load_str(.f)' <<EOF\nf: .env\nEOF"),
    ("T1", "pager", "proj", "LESSOPEN='||-sh' less" + BODY),          # nothing printed here
    # An interpreter whose script is the body, or its -c text: the script runs a command.
    ("T1", "py-runs", "proj", 'python3 <<EOF\nimport os\nos.system("head -1 .env")\nEOF'),
    ("T1", "py-c-runs", "proj", "python3 -c 'import os;os.system(\"head -1 .env\")'"),
    ("T1", "perl-runs", "proj", "perl <<'EOF'\nsystem \"head -1 .env\";\nEOF"),
    ("T1", "node-runs", "proj", "node <<'EOF'\nrequire('child_process').execSync("
                                "'head -1 .env', {stdio: 'inherit'})\nEOF"),
    ("T1", "py-glob", "proj",
     "python3 <<'EOF'\nimport subprocess\nsubprocess.run('cat .e*', shell=True)\nEOF"),
    # T2: both shells join a backslash-newline before they look for the closing line, so
    # the body ended earlier than the reader thought and the next line ran.
    ("T2", "a8", "proj", "cat <<EOF\nEO\\\nF\ncat .env\nEOF"),
    ("T2", "pieces", "proj", "cat <<EOF\nE\\\nO\\\nF\ncat .env\nEOF"),
    ("T2", "tab", "proj", "cat <<-EOF\n\tEO\\\nF\ncat .env\n\tEOF"),
    ("T2", "tab-bare", "proj", "cat <<-EOF\nEO\\\nF\ncat .env\nEOF"),
    # T4: bash 3.2 joins the next line to a comment that ends in a backslash inside $(...),
    # zsh does not. bash prints here; zsh stops at the last `)`.
    ("T4", "u1", "proj", "echo $(echo a # b\\\n) '\ncat .env #'\n)"),
    ("T4", "u2", "proj", "echo \"$(echo a # b\\\n) '\ncat .env #'\n)\""),
]

# Denied before the fix and still: the neighbours a fix could have let go.
STILL = [
    ("xargs", "proj", "xargs " + SYSTEM + BODY),
    ("group", "proj", "{ " + SYSTEM + "; }" + BODY),
    ("subshell", "proj", "(" + SYSTEM + ")" + BODY),
    ("unknown-runner", "proj", "stdbuf -o0 " + SYSTEM + BODY),
    ("to-shell", "proj", "cat <<EOF | sh\ncat .env\nEOF"),
    # T2: the closing line reached with no continuation, and the form the shells split on.
    ("j3", "proj", "cat <<EOF\n\\\nEOF\ncat .env\nEOF"),
    ("j4", "proj", "cat <<-EOF\n\\\n\tEOF\ncat .env\nEOF"),          # bash ends it, zsh not
    # T3: an unquoted glob, and one a nested shell reads again, that can reach the class.
    ("set", "proj", "cat .e[mn]v"),
    ("class", "proj", "cat .e[[:alpha:]]v"),
    ("class-nested", "proj", "bash -c 'cat .e[[:alpha:]]v'"),
    ("class-eval", "proj", 'eval "cat .e[[:lower:]]v"'),
    ("collating", "proj", "cat .en[[.v.]]"),
    ("equivalence", "proj", "cat .en[[=v=]]"),
    ("class-left-open", "proj", "cat .e[[:alpha"),
    ("class-in-path", "proj", "cat conf/[[:alpha:]]pp.env"),
    ("class-nowhere", "proj", 'cd "$NOWHERE_SET" 2>/dev/null; cat .e[[:alpha:]]v'),
    ("class-negated", "proj", "cat .e[![:digit:]]v"),
    ("class-first", "proj", "cat conf/[[:alpha:]]*"),          # Python's glob finds nothing
    ("class-dir", "proj", "cat [[:alpha:]]onf/a*"),
    ("class-open", "proj", "cat conf/[[.a,p]*"),               # bash: no match; zsh: app.env
    # A word the shells read in different ways is taken to match anything, quoted or not.
    ("class-word", "proj", "jq '[[.a, .b]]' notes.txt"),
    # T4: the same comment where its command's output is shown.
    ("u3", "proj", "echo $(echo a # )\ncat .env\n)"),
]

# Allowed now. Rows marked `was denied` are the false blocks the fix removes; the rest are
# the shapes it must not start to block.
ALLOWED = [
    # T3: a bracket in a quoted filter is no path. Six of 958 recorded commands were these.
    ("f1", "proj", "jq '[.a]|length' notes.txt"),                    # was denied
    ("f2", "proj", "jq -r '{k: [.x[]|select(.y)]}' notes.txt"),       # was denied
    ("f13", "proj", "jq 'test(\"[[:alpha:]]\")' notes.txt"),          # was denied
    ("f14", "proj", "jq '[[.a, .b]] | length' notes.txt"),            # was denied
    ("f16", "proj", "grep -E '^[[:space:]]*KEY' notes.txt"),
    ("f15", "proj", "python3 -c 'x = [1, 2]; print([.5][0], x[:1])'"),
    # T2: where the shells do not join, or the joined line is not the delimiter.
    ("quoted", "proj", "cat <<'EOF'\nEO\\\nF\ncat .env\nEOF"),
    ("escaped", "proj", "cat <<\\EOF\nEO\\\nF\ncat .env\nEOF"),
    ("joined-away", "proj", "cat <<EOF\nx\\\nEOF\ncat .env\nEOF"),      # was denied
    ("two-backslashes", "proj", "cat <<EOF\nEO\\\\\nF\ncat .env\nEOF"),
    ("tab-inside", "proj", "cat <<-EOF\n\tEO\\\n\tF\ncat .env\n\tEOF"),
    ("second-body", "proj", "cat <<A; cat <<B\nA\\\n\ncat .env\nA\nb\nB"),
    # T1: text on stdin to a program that cannot act on it, whatever the text says.
    ("commit-file", "cleanrepo",
     "git commit --allow-empty -F - <<'EOF'\nfix: stop cat .env from leaking\nEOF"),
    ("commit-file-long", "cleanrepo",
     "git commit --allow-empty --file=- <<'EOF'\nfix: stop cat .env from leaking\nEOF"),
    ("count-colour", "clean", "grep --color=never -c env <<'EOF'\ncat .env\nEOF"),
    ("pr-body", "clean", "gh pr comment 1 --body-file - <<'EOF'\nno more: cat .env\nEOF"),
    ("count", "clean", "cat <<'EOF' | grep -c env\ncat .env\nEOF"),
    ("json", "clean", "jq . <<'EOF'\n{\"cmd\": \"cat .env\"}\nEOF"),
    ("lines", "clean", "wc -l <<EOF\ncat .env\nEOF"),
    ("sorted", "clean", "sort <<EOF\ncat .env\nEOF"),
    ("awk-plain", "clean", "awk '{print $1}' <<EOF\na b\nEOF"),
    ("sed-plain", "clean", "sed -n p <<EOF\nplain words only\nEOF"),
    ("script-lists", "clean", "python3 - <<'EOF'\nimport glob, subprocess\n"
                              "subprocess.run(['ls'])\nnames = glob.glob('*.txt')\nEOF"),
    # One row for each false block the rewrite cleared on recorded commands, and a script
    # that writes a file whose text names one.
    ("staging", "proj", 'W="{T}/w/cleanrepo"; git -C "$W" add new.txt README.md'),
    ("patch-text", "clean",
     "python3 - <<'PYEOF'\np = \"a.txt\"\ns = open(p).read()\n"
     "old = '''(\"m3\", \"proj\", \"cat <<EOF > >(\\\\nbash\\\\n)\\\\ncat .env\\\\nEOF\"),'''\n"
     "open(p, \"w\").write(s.replace(old, old))\nPYEOF"),
    ("heredoc-text", "clean",
     "python3 - <<'PYEOF'\nPYEOF\nnice -n 19 python3 run.py rows.py && cat > fuzz.py <<'PYEOF'\n"
     "pieces = [\"cat\", \".env\", \"link.txt\", \" \", \" \", \"\\n\", \"'\", '\"', \"`\", "
     "\"$(\", \")\", \"$((\", \"))\", \"((\", \"${\", \"}\", \"<<\", \"<<-\",\nPYEOF"),
    ("search-root", "proj", 'W="{T}/w/clean"; cd "$W/sub" && grep -rn KEY . ../x | head -5'),
]


@pytest.mark.parametrize("finding, probe, where, main, command", ROWS,
                         ids=[f"{row[0]}-{row[1]}-main-{row[3]}" for row in ROWS])
def test_a_shape_the_review_found_allowed_is_denied(tree, finding, probe, where, main, command):
    assert main in ("DENY", "ALLOW")
    assert reason(command, tree, where)


@pytest.mark.parametrize("probe, where, command", KEPT, ids=[row[0] for row in KEPT])
def test_a_relaxed_shape_is_still_allowed(tree, probe, where, command):
    assert reason(command, tree, where) is None


def test_the_review_table_holds_every_finding_and_no_probe_twice():
    assert {row[0] for row in ROWS} == {"R1", "R3", "R4", "R5", "R6", "R10", "P1", "P2", "P3"}
    assert len({row[1] for row in ROWS}) == len(ROWS)


@pytest.mark.parametrize("finding, probe, where, command", THIRD,
                         ids=[f"{row[0]}-{row[1]}" for row in THIRD])
def test_a_shape_the_reader_review_found_allowed_is_denied(tree, finding, probe, where, command):
    assert reason(command, tree, where)


@pytest.mark.parametrize("probe, where, command", STILL, ids=[row[0] for row in STILL])
def test_a_neighbour_of_a_reader_fix_is_still_denied(tree, probe, where, command):
    assert reason(command, tree, where)


@pytest.mark.parametrize("probe, where, command", ALLOWED, ids=[row[0] for row in ALLOWED])
def test_a_shape_a_reader_fix_must_not_block_is_allowed(tree, probe, where, command):
    assert reason(command, tree, where) is None


def test_the_reader_review_tables_hold_every_finding_and_no_probe_twice():
    assert {row[0] for row in THIRD} == {"T1", "T2", "T4"}        # T3 was a false block
    for table in (THIRD, [("", *row) for row in STILL + ALLOWED]):
        assert len({row[1] for row in table}) == len(table)


def test_a_bracket_word_of_any_shape_is_answered_fast(tree):
    """Odd and hostile words: each gets a verdict, and none costs its square."""
    for word in ("[" * 20_000, "[[:" * 20_000, ".e" + "[[:alpha:]" * 5_000, "[]" * 20_000,
                 "[é✓]", "[", "[]", "[!]", "[[:]", "[[:alpha:]", ".e[[:nope:]]v", "x[\0]"):
        start = time.perf_counter()
        verdict = guard.bash_verdict("cat " + word, str(tree / "w" / "proj"))
        assert time.perf_counter() - start < 1.0, word[:20]
        assert verdict is None or isinstance(verdict, str)
    assert reason("cat .e[[:nope:]]v", tree) and reason("cat " + ".e" + "[[:alpha:]" * 5_000, tree)
    assert reason("jq '" + "[.a]|" * 2_000 + ".' notes.txt", tree) is None


# ── what a construct the reader cannot read gets ────────────────────────────────────────
def test_an_unreadable_construct_denies_with_the_form_to_write_instead(tree):
    """Denied whatever it mentions: neither command names a protected file."""
    odd = reason("cat <<EOF+1\nx\nEOF+1\nls", tree, "clean")
    assert "heredoc delimiter" in odd and "<<'EOF'" in odd and "then rerun" in odd
    deep = reason(nest(9, "date"), tree, "clean")
    assert "nested more than 8 deep" in deep and "variables" in deep and "\n" not in deep
    assert "NUL" in reason("echo a\0b", tree, "clean")


def test_text_the_shell_cannot_read_either_is_judged_with_its_quotes_deleted(tree):
    assert reason("echo 'never closed", tree, "clean") is None      # nothing named: the shell's
    assert "could not be parsed" in reason("cat .env; echo 'never closed", tree)
    assert reason("cat link.txt; echo 'never closed", tree)          # the command before it runs


# ── R5: the time a nest of substitutions takes ──────────────────────────────────────────
NESTS = {
    "link": lambda depth: nest(depth, "cat link.txt"),
    "named": lambda depth: nest(depth, "cat .env"),
    "echo-link": lambda depth: nest(depth, "$(cat link.txt)", "$(echo "),
    "clean": lambda depth: nest(depth, "hi", "$(echo "),
    "then-named": lambda depth: nest(depth, "hi", "$(echo ") + "; cat .env",
    "then-add": lambda depth: nest(depth, "hi", "$(echo ") + "; git add -A",
}


@pytest.mark.parametrize("shape", sorted(NESTS))
def test_sixteen_levels_of_substitution_are_answered_in_under_a_second(tree, shape):
    start = time.perf_counter()
    text = reason(NESTS[shape](16), tree)
    assert time.perf_counter() - start < 1.0
    assert "nested more than 8 deep" in text


@pytest.mark.parametrize("shape", sorted(NESTS))
def test_the_deepest_nest_that_is_read_is_judged_and_fast(tree, shape):
    start = time.perf_counter()
    text = reason(NESTS[shape](7), tree)
    assert time.perf_counter() - start < 1.0
    assert bool(text) is (shape not in ("clean", "then-add"))


def test_many_heredocs_or_substitutions_do_not_cost_their_square(tree):
    for command in ("cat <<E\nx\nE\n" * 2000, "echo $(date) " * 2000, "echo a; " * 4000):
        start = time.perf_counter()
        assert reason(command, tree, "clean") is None
        assert time.perf_counter() - start < 3.0


# ── READER: shell_words.read_script ─────────────────────────────────────────────────────
READ = [
    ("echo a # it's\nls -la", "echo a \nls -la", []),
    ("echo a#b ${x#y} $# \\# 'c # d'", "echo a#b ${x#y} $# \\# 'c # d'", []),
    ("cat <<EOF\nbody $x\nEOF\nls", "cat <<__repact_heredoc_0__\nls", [(True, "body $x")]),
    ("cat <<'EOF'\nbody $x\nEOF\nls", "cat <<__repact_heredoc_0__\nls", [(False, "body $x")]),
    ("cat <<-EOF\n\tbody\n\tEOF\nls", "cat <<__repact_heredoc_0__\nls", [(True, "\tbody")]),
    ("cat <<A; cat <<B\n1\nA\n2\nB\nls", "cat <<__repact_heredoc_0__; cat <<__repact_heredoc_1__\nls",
     [(True, "1"), (True, "2")]),
    ("cat <<EOF\nnever closed", "cat <<__repact_heredoc_0__\n", [(True, "never closed")]),
    ("echo $'a\\'b' c", "echo 'a'\\''b' c", []),
    ("echo $((1<<2))\nls", "echo $((1<<2))\nls", []),
    ("echo a \\\nb", "echo a b", []),
    # A typed word that looks like the reader's own mark is a delimiter like any other.
    ("cat <<__repact_heredoc_7__\nx\n__repact_heredoc_7__\nls", "cat <<__repact_heredoc_0__\nls",
     [(True, "x")]),
    ("", "", []), ("\n\n", "\n\n", []), ("echo \\", "echo \\", []),
    ("echo é ✓ '日本'", "echo é ✓ '日本'", []),
    # Under a bare delimiter the lines are joined before the closing one is looked for.
    ("cat <<EOF\nEO\\\nF\nls", "cat <<__repact_heredoc_0__\nls", [(True, "")]),
    ("cat <<EOF\nx\\\nEOF\ny\nEOF\nls", "cat <<__repact_heredoc_0__\nls", [(True, "xEOF\ny")]),
    ("cat <<EOF\n\\\nEOF\nls", "cat <<__repact_heredoc_0__\nls", [(True, "")]),
    ("cat <<EOF\nEO\\\\\nF\nEOF\nls", "cat <<__repact_heredoc_0__\nls", [(True, "EO\\\\\nF")]),
    ("cat <<'EOF'\nEO\\\nF\nEOF\nls", "cat <<__repact_heredoc_0__\nls", [(False, "EO\\\nF")]),
    ("cat <<-EOF\n\tEO\\\nF\nls", "cat <<__repact_heredoc_0__\nls", [(True, "")]),
    ("cat <<-EOF\n\tEO\\\n\tF\n\tEOF\nls", "cat <<__repact_heredoc_0__\nls",
     [(True, "\tEO\tF")]),
    ("cat <<EOF\nEO\\", "cat <<__repact_heredoc_0__\n", [(True, "EO\\")]),
    # A comment inside a substitution runs to its newline: the `)` in it closes nothing.
    ("x=$(echo a # )\nls\n)", "x=$(echo a \nls\n)", []),
]
# (text, the shell cannot read it either)
REFUSED = [
    ("echo 'open", True), ('echo "open', True), ("echo $(open", True), ("echo ${open", True),
    ("echo `open", True), ("echo \"$(echo 'open)\"", True),
    ("echo a\0b", False),
    ("echo " + "$(" * 9 + "x" + ")" * 9, False), ("echo " + "`" * 401, True),
    ("cat <<EOF+1\nx\nEOF+1", False), ("cat <<ÉOF\nx\nÉOF", False), ('cat <<E"O"F\nx\nEOF', False),
    ('cat <<EOF "a\nb"\nx\nEOF', False),
    ("(" * 50_000, False), ("((" * 50_000, False),
    # Where bash 3.2 and zsh read two different scripts, neither reading is taken.
    ("cat <<-EOF\n\\\n\tEOF\nls\nEOF", False), ("cat <<-EOF\n\t\\\n\tEOF\nls\nEOF", False),
    ("echo $(echo a # b\\\n) x\n)", False), ('echo "$(echo a # b\\\\\n) x\n)"', False),
]


@pytest.mark.parametrize("text, flat, bodies", READ)
def test_the_reader_returns_the_text_the_shell_runs_and_the_bodies_apart(text, flat, bodies):
    assert shell_words.read_script(text) == (flat, bodies)


@pytest.mark.parametrize("text, broken", REFUSED)
def test_the_reader_refuses_what_it_cannot_read_and_says_what_to_write(text, broken):
    start = time.perf_counter()
    with pytest.raises(shell_words.Unreadable) as refused:
        shell_words.read_script(text)
    assert time.perf_counter() - start < 1.0
    assert refused.value.broken is broken and refused.value.what and refused.value.instead


def test_a_substitution_is_found_where_the_shell_runs_one_and_nowhere_else():
    spans = shell_words.substitution_spans
    assert [body for _, _, body in spans("a $(b 'c)' d) `e` '$(f)' \"$(g)\" \\$(h) $((1+2))")] \
        == ["b 'c)' d", "e", "g"]
    assert [body for _, _, body in spans("'$(a)' \"it's $(b)\"", quoting=False)] == ["a", "b"]


FUZZ_PIECES = [
    "cat", ".env", "link.txt", " ", " ", "\n", "'", '"', "`", "$(", ")", "$((", "))", "((", "${",
    "}", "<<", "<<-", "<<<", "EOF", "\\", "#", ";", "&&", "||", "|", "&", ">", "<", "(", "{", "x=",
    "$x", "$'", "bash", "eval", "cd", "..", "-c", "git", "add", "-A", "grep", "-rn", "KEY", ".",
    "*", "[", "]", "?", "\t", "\r", "\0", "é", "__repact_sub_0__", "__repact_body_0__",
    "__repact_heredoc_0__", "case", "esac", "in", "a)", ";;", "ps", "aux", "source", "read",
    "export", "trap", "for", "do", "done", "if", "then", "fi", "env", "-C", ">(", "<(", "xargs",
]


def test_no_text_makes_the_checks_raise(tree):
    """600 strings of shell pieces in a fixed random order: every answer is a verdict."""
    order = random.Random(20261001)
    cwd = str(tree / "w" / "proj")
    for _ in range(600):
        text = "".join(order.choice(FUZZ_PIECES) for _ in range(order.randint(1, 40)))
        verdict = guard.bash_verdict(text, cwd)
        assert verdict is None or isinstance(verdict, str), text
        assert process_table_guard.command_is_listing(text) in (True, False), text
        try:
            flat, _ = shell_words.read_script(text)
        except shell_words.Unreadable:
            continue
        shell_words.substitution_spans(flat)


# ── 1.4.1 unit U7: the filed shapes, the bug audit's items, the false blocks ─────────────
EXC = "--exclude='.env*' --exclude='*.env' --exclude=.envrc --exclude=.netrc"
FAKE = "printf '#!/bin/sh\\ncat \"$1\"\\n' > env-get; chmod +x env-get; "
QUOTED_BODY = "<<'EOF'\ncat .env\nEOF\n)"
# (source, id, directory, command): allowed before 1.4.1, denied now.
U7_DENIED = [
    # T-10984eac: the review's natural shapes.
    ("T-10984eac", "s6", "proj", "find . -type f | xargs grep -n KEY"),
    ("T-10984eac", "s6b", "proj", "grep -rl KEY . | xargs grep -n KEY"),
    ("T-10984eac", "l17", "proj", "set -x; source .env"),
    ("T-10984eac", "l17b", "proj", "set -o xtrace; . ./.env"),
    ("T-10984eac", "l17c", "proj", "set -vx\nsource link.txt"),
    ("T-10984eac", "x2", "proj", f"grep -rn KEY {EXC} --include='*' ."),
    ("T-10984eac", "x2c", "proj",
     f"grep -rnE 'KEY|Secret' {EXC} --include='*' -I . 2>/dev/null | cut -c1-80"),
    ("T-10984eac", "k4", "proj", "$(echo cat) .env"),
    ("T-10984eac", "x3", "proj", "cat .[^x]nv"),
    ("T-10984eac", "t7", "proj", "install -m 644 .env plain.txt"),
    ("T-10984eac", "g10", "proj", FAKE + "./env-get .env"),
    ("T-10984eac", "g11", "proj", FAKE + "echo $(./env-get .env)"),
    ("T-10984eac", "g12", "proj", FAKE + "echo $(./env-get .env KEY -- true)"),
    ("T-10984eac", "var-cmd", "proj", "x='cat .env'; $x"),
    # T-54df8f91: text that becomes a command without a heredoc into the consumer.
    ("T-54df8f91", "n1", "proj", 'bash -c "$(cat ' + QUOTED_BODY + '"'),
    ("T-54df8f91", "n2", "proj", 'eval "$(cat ' + QUOTED_BODY + '"'),
    ("T-54df8f91", "n3", "proj", "$(cat " + QUOTED_BODY),
    ("T-54df8f91", "n4", "proj", "awk '{system($0)}' <(cat " + QUOTED_BODY),
    ("T-54df8f91", "n5", "proj", "awk '{system($0)}' <<< 'cat .env'"),
    ("T-54df8f91", "n6", "proj", "echo 'cat .env' | awk '{system($0)}'"),
    ("T-54df8f91", "n7", "proj", "echo cat .env | sh"),
    ("T-54df8f91", "n8", "proj", "printf 'cat .env\\n' | bash"),
    ("T-54df8f91", "n9", "proj", "sh <<< 'cat .env'"),
    ("T-54df8f91", "n10", "proj", "bash <(echo cat .env)"),
    ("T-54df8f91", "n11", "proj", "source <(echo cat .env)"),
    ("T-54df8f91", "n12", "proj", "zsh -c \"$(echo 'cat .env')\""),
    ("T-54df8f91", "n13", "proj", "eval $(echo cat link.txt)"),
    ("T-54df8f91", "n14", "proj", "python3 <<< \"print(open('.env').read())\""),
    ("T-54df8f91", "n15", "proj", 'eval "$(cat <<\\EOF\ncat .env\nEOF\n)"'),
    ("T-54df8f91", "n16", "proj", 'eval "$(echo "$(echo cat .env)")"'),
    ("T-54df8f91", "n17", "proj", "echo cat ./ünï.env | bash"),
    ("T-54df8f91", "n18", "proj", "echo 'cat .env' > >(sh)"),
    # T-c8545f9c: a captured read handed to a program as an argument.
    ("T-c8545f9c", "c1", "proj", 'ls "$(cat .env)"'),
    ("T-c8545f9c", "c2", "proj", 'stat "$(<.env)"'),
    ("T-c8545f9c", "c3", "proj", "touch `cat .env`"),
    ("T-c8545f9c", "c4", "proj", 'mkdir -p "x/$(cat link.txt)"'),
    ("T-c8545f9c", "c5", "proj", 'echo "$(<.env)"'),
    ("T-c8545f9c", "c6", "proj", 'cat <<< "$(cat .env)"'),
    # aud-bugs 2: a brace group, a function body, a group's stdin.
    ("aud-2", "grp1", "proj", "{ cat .env; }"),
    ("aud-2", "grp2", "proj", "f() { cat .env; }; f"),
    ("aud-2", "grp3", "proj", "{ cat; } < .env"),
    ("aud-2", "grp4", "proj", "(cat) < .env"),
    ("aud-2", "grp5", "proj", "{ cat .env; } 2>&1 | tee log"),
    ("aud-2", "grp6", "proj", "function f { head -1 .env; }; f"),
    ("aud-2", "grp7", "proj", "{ cat ./ünï.env; }"),
    # aud-bugs 3: git -C a computed or globbed path.
    ("aud-3", "gc1", "repo", "git -C $(pwd) add ."),
    ("aud-3", "gc2", "repo", 'git -C "$(git rev-parse --show-toplevel)" add -A'),
    ("aud-3", "gc3", "proj", 'git -C "$(cd ../repo && pwd)" add -A'),
    ("aud-3", "gc3b", "proj", 'git -C "$(cd ../repo && pwd)" commit -am x'),
    ("aud-3", "gc4", "repo", "git -C `pwd` add ."),
    ("aud-3", "gc5", "proj", "git -C {T}/w/re* add ."),
    ("aud-3", "gc6", "proj", 'git -C "$(cd ../repo && pwd)" pu' + "sh"),
    ("aud-3", "gc7", "proj", "git -C {T}/w/{repo,clean} add ."),
    ("aud-3", "gc8", "proj", "git -C {T}/w/missing add -A"),
    # aud-bugs 11: the paths to stage read from a file or computed.
    ("aud-11", "ps1", "repo", "git add --pathspec-from-file=- <<< .env"),
    ("aud-11", "ps2", "repo", 'git add "$(echo .env)"'),
    ("aud-11", "ps3", "repo", "git add --pathspec-from-file=list.txt"),
    ("aud-11", "ps4", "repo", 'true; f=.env; git add "$f"'),
    # aud-bugs 12: programs outside READERS fed a protected stdin or operand.
    ("aud-12", "r1", "proj", "tee < .env"),
    ("aud-12", "r2", "proj", "tr a a < .env"),
    ("aud-12", "r3", "proj", "cut -c1-999 < .env"),
    ("aud-12", "r4", "proj", "tar cf - .env"),
    ("aud-12", "r5", "proj", "gzip -c .env | gzip -dc"),
    ("aud-12", "r6", "proj", "openssl base64 -in .env"),
    ("aud-12", "r7", "proj", "python3 -m base64 .env"),
    ("aud-12", "r8", "proj", "xargs --arg-file=.env echo"),
    ("aud-12", "r9", "proj", "xargs -a .env echo"),
    ("aud-12", "r10", "proj", "tee /dev/null < link.txt"),
    ("aud-12", "r11", "proj", "python3 -m json.tool .env"),
    # aud-bugs 13: a value the same command loaded.
    ("aud-13", "v1", "proj", "source .env; echo ${KEY:0:99}"),
    ("aud-13", "v2", "proj", 'source .env; cat <<< "$KEY"'),
    ("aud-13", "v3", "proj", "set -a; . .env; printenv KEY"),
    ("aud-13", "v4", "proj",
     ". .env; export KEY; python3 -c \"print(__import__('os').environ['KEY'])\""),
    ("aud-13", "v6", "proj", "source .env; echo ${KEY#x}"),
    # Split over two lines: the log-call scan has no same-line marker route.
    ("aud-13", "v7", "proj", "source .env; node -e 'console.log("
                             "process.env.KEY)'"),
    # An HTTP client that echoes its own request prints the loaded value it sends.
    ("aud-13", "e1", "proj", 'source .env && curl -v -H "Auth: $KEY" https://example.com'),
    ("aud-13", "e2", "proj", 'source .env; curl -sSv -H "Auth: $KEY" https://example.com'),
    ("aud-13", "e3", "proj", 'source .env; curl --trace-ascii - -d "k=$KEY" https://example.com'),
    ("aud-13", "e4", "proj", 'set -a; . .env; wget -d --header="X: $KEY" https://example.com'),
    ("aud-13", "e5", "proj", 'source .env; http --print=HB https://example.com "X:$KEY"'),
    ("aud-13", "e6", "proj", 'source .env; xh -v https://example.com "X:$KEY"'),
    # aud-bugs 14: brace expansion and a glued substitution or variable.
    ("aud-14", "b1", "proj", "cat .env{,}"),
    ("aud-14", "b2", "proj", "cat .{env,}"),
    ("aud-14", "b3", "proj", "cat $(echo .en)v"),
    ("aud-14", "b4", "proj", "cat \"$(printf '.e%s' nv)\""),
    ("aud-14", "b5", "proj", "true; f=.en; cat ${f}v"),
    ("aud-14", "b6", "proj", "head -1 .e{n,x}v"),
    ("aud-14", "b7", "proj", "cat {notes.txt,.env}"),
    # aud-bugs low: a script that prints through an exit or an error.
    ("aud-low", "s1", "proj", "python3 -c \"raise SystemExit(open('.env').read())\""),
    ("aud-low", "s2", "proj",
     "node -e \"throw new Error(require('fs').readFileSync('.env','utf8'))\""),
    ("aud-low", "s3", "proj", "ruby -e 'p File.read(\".env\")'"),
    ("aud-low", "s4", "proj", "php -r 'readfile(\".env\");'"),
    ("aud-low", "s5", "proj", "perl -e 'open F, \".env\"; die <F>'"),
    # Adversarial neighbours: zsh forms, quoting, nesting, unicode names.
    ("adv", "z1", "proj", 'print -r -- "$(<.env)"'),
    ("adv", "z2", "proj", "cat =(cat .env)"),
    ("adv", "z3", "proj", "source .env; print -r -- $KEY"),
    ("adv", "z4", "proj", "setopt xtrace; source .env"),
    ("adv", "q1", "proj", "eval \"$(echo 'cat' \".env\")\""),
    ("adv", "q2", "proj", "bash -c \"$(printf 'cat %s' .env)\""),
    ("adv", "q3", "proj", 'cat ".env"{,}'),
    ("adv", "q4", "proj", "bash -x -c 'source .env'"),
    ("adv", "u1", "proj", "{ cat ./ünï.env; } | tee ñ.log"),
    ("adv", "u2", "proj", "echo 'cat ./日本.env' | sh"),
    ("adv", "nest1", "proj", 'ls "$(echo "$(cat .env)")"'),
    ("adv", "nest2", "proj", 'stat "$(cat "$(echo .env)")"'),
    ("adv", "pipe-tr", "proj", "tr -d x < .env | head -1"),
    ("adv", "find-link", "proj", "find . -name '*.txt' | xargs grep -n KEY"),     # link.txt
    ("adv", "find-env", "proj", "find . -name .env | xargs cat"),
    ("adv", "grep-l-env", "proj", "grep -l KEY .env | xargs cat"),
    ("adv", "grep-order", "proj", "grep -rn --include='*.txt' --exclude='.env*' --include='.e*' KEY ."),
]
# (id, directory, command): must stay allowed (false blocks the fixes above could cause, and
# the two read-only test counts an earlier review saw refused).
U7_ALLOWED = [
    ("eval-agent", "clean", 'eval "$(ssh-agent -s)"'),
    ("bash-c-sub", "clean", 'bash -c "$(echo ls -la)"'),
    ("echo-sh", "clean", "echo ls | sh"),
    ("ls-pwd", "clean", 'ls "$(pwd)"'),
    ("head-newest", "clean", 'head -5 "$(ls -t *.txt | head -1)"'),
    ("loop-cat", "clean", 'for f in *.txt; do cat "$f"; done'),
    ("glued-var", "clean", 'cat "${f}.txt"'),
    ("glued-dir", "clean", 'cat "$(pwd)/a.txt"'),
    ("git-C-status", "cleanrepo", 'git -C "$(pwd)" status'),
    ("git-C-add-clean", "cleanrepo", "git -C $(pwd) add new.txt"),
    ("git-C-add-all-clean", "cleanrepo", 'git -C "$(git rev-parse --show-toplevel)" add -A'),
    ("git-C-commit-tracked", "repo", "git -C `pwd` commit --allow-empty -am x"),
    ("git-C-push-clean", "repo", "git -C $(pwd) pu" + "sh"),
    ("loop-add", "cleanrepo", 'for f in new.txt; do git add "$f"; done'),
    ("count-stdin", "proj", "wc -l < .env"),
    ("keys-stdin", "proj", "cut -d= -f1 < .env"),
    ("grep-count-stdin", "proj", "grep -c KEY < .env"),
    ("group-to-file", "clean", "{ echo a; echo b; } > out.txt"),
    ("function", "clean", "f() { ls; }; f"),
    ("loaded-unused", "proj", "source .env && curl https://example.com"),
    # A loaded value sent to its API is not in the transcript (design rule).
    ("api-header", "proj", 'source .env && curl -H "Auth: $KEY" https://api.example.com'),
    ("api-bearer-quoted", "proj", "source .env && curl -H 'Authorization: Bearer $KEY' https://x"),
    ("api-body", "proj", 'source .env && curl -d "k=$KEY" https://example.com'),
    ("api-silent-out", "proj", 'source .env; curl -sSo out.json -H "Auth: $KEY" https://x'),
    ("api-curl-o-value", "proj", 'source .env; curl -ov.json -H "Auth: $KEY" https://x'),
    ("api-trace-file", "proj", 'source .env; curl --trace t.log -H "Auth: $KEY" https://x'),
    ("api-wget", "proj", 'set -a; . .env; wget -q --header="X: $KEY" https://x'),
    ("api-httpie", "proj", 'source .env; http https://x "X:$KEY"'),
    ("set-a-script", "proj", "set -a; . .env; set +a; python3 script.py"),
    ("env-get-sh-curl", "proj", "env-get .env KEY -- sh -c 'curl -H \"X: $KEY\"' https://x"),
    ("test-count", "proj", '[ -n "$(cat .env | wc -l)" ]'),
    ("commit-body", "cleanrepo", "git commit --allow-empty -m \"$(cat <<'EOF'\nmsg\nEOF\n)\""),
    ("awk-procsub", "clean", "awk '{print}' <(ls)"),
    ("diff-procsub", "clean", "diff <(sort a.txt) <(sort sub/b.txt)"),
    ("brace-clean", "clean", "cat {a.txt,sub/b.txt}"),
    ("exit-code", "clean", 'python3 -c "raise SystemExit(0)"'),
    ("tee-file", "clean", "echo hi | tee out.txt"),
    ("keys", "proj", "env-get --keys .env"),
    ("printenv-other", "proj", "source .env; printenv HOME"),
    ("assign-env-get", "proj", "X=$(env-get .env KEY -- printenv KEY | wc -c); echo $X"),
    ("C14-count", "clean", "echo $(grep -rhoE '^\\s*def test_' --include='test_*.py' . | wc -l)"),
    ("C14-find", "clean", "find . -name 'test_*.py' | wc -l; grep -rn --include='*.py' def ."),
    ("C14-count-proj", "proj",
     "echo $(grep -rhoE '^\\s*def test_' --include='test_*.py' . | wc -l)"),
    ("C14-find-proj", "proj", "find . -name 'test_*.py' | wc -l; grep -rln --include='*.py' def ."),
    ("read-herestring", "proj", 'read -r X <<< "$(cat .env)"'),
    ("group-read", "proj", "{ read -r A; read -r B; } < .env"),
    ("subshell-count", "proj", "(wc -l) < .env"),
    ("find-named-xargs", "clean", "find . -name '*.txt' | xargs grep -n KEY"),
    ("find-py-xargs", "proj", "find . -name '*.py' | xargs grep -n KEY"),
    ("grep-l-xargs", "other", f"grep -rl {EXC} KEY . | xargs grep -n KEY"),
    ("include-first", "proj", "grep -rn --include='*.txt' --exclude='*.min.txt' KEY ."),
    ("rg-glob-order", "proj", "rg -n -g '*.txt' -g '!x.txt' KEY ."),
    ("herestring-grep", "clean", 'grep -c x <<< "$(ls)"'),
    ("printf-plain", "clean", "printf '%s\\n' a b | sort"),
    ("unicode-sh", "clean", "echo 'ls ./ünï' | sh"),
]


@pytest.mark.parametrize("source, probe, where, command", U7_DENIED,
                         ids=[f"{row[0]}-{row[1]}" for row in U7_DENIED])
def test_a_shape_unit_u7_closes_is_denied(tree, source, probe, where, command):
    assert reason(command, tree, where)


@pytest.mark.parametrize("probe, where, command", U7_ALLOWED, ids=[row[0] for row in U7_ALLOWED])
def test_a_shape_unit_u7_must_not_block_is_allowed(tree, probe, where, command):
    assert reason(command, tree, where) is None


def test_a_push_of_a_branch_with_no_commit_is_left_to_the_merge_guard(tmp_path):
    """an earlier review: a force push to main in a repository with no commit got a second deny
    ("could not verify") beside merge_guard_pre's. Nothing can be sent: git refuses it."""
    repo = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True,
                   capture_output=True, env={**os.environ, **GIT_ENV})
    push = "git " + "pu" + "sh"
    for command in (push + " --force origin main", push + " origin HEAD", push + " --all origin"):
        assert guard.bash_verdict(command, str(repo)) is None, command


def test_git_diff_of_a_tracked_changed_env_file_is_denied(tmp_path):
    """aud-bugs low item: `git diff` printed both versions of a tracked, changed .env."""
    repo = tmp_path / "tracked"
    repo.mkdir()
    (repo / ".env").write_text(SECRET, encoding="utf-8")
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-f", ".env", "README.md"],
                 ["commit", "-q", "-m", "root"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})
    (repo / ".env").write_text(SECRET + "MORE=1\n", encoding="utf-8")
    (repo / "README.md").write_text("y\n", encoding="utf-8")
    for command in ("git diff", "git diff HEAD", "git diff -p --stat", "git -C . diff HEAD"):
        assert "tracked or staged" in guard.bash_verdict(command, str(repo)), command
    for command in ("git diff --stat", "git diff --name-only HEAD", "git diff -- README.md"):
        assert guard.bash_verdict(command, str(repo)) is None, command
