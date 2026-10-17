#!/usr/bin/env python3
"""demo_setup: copy the toy project into a new scratch directory for /repacted:demo.

Location: pact-plugin/examples/demo_setup.py. commands/demo.md runs it once, from the user's
working directory. It makes a fresh directory under the system temp dir, refuses when that
directory resolves inside the git repository of the working directory (a TMPDIR set inside
it), copies the toy's code, test, README and .gitignore (not fixtures/: the fake key there is
for the release checks only), and makes the copy its own git repository with one starting
commit, so the fix shows as a diff. The .gitignore lists .env, which the commit check requires
before it allows a commit. Stdlib only.

Usage: python3 demo_setup.py
Prints `demo dir: <path>` and exits 0, or prints a `refused:` line and exits 1.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOY = Path(__file__).resolve().parent / "toy"
FILES = ("wordcount.py", "test_wordcount.py", "README.md", ".gitignore")
GIT_ID = ("-c", "user.name=demo", "-c", "user.email=demo@example.invalid", "-c", "commit.gpgsign=false")


def repo_top(cwd):
    """The resolved top of the git repository holding cwd, or None outside one (or with no git)."""
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True,
                             text=True, check=False)
    except OSError:
        return None
    return Path(out.stdout.strip()).resolve() if out.returncode == 0 and out.stdout.strip() else None


def git(demo, *args):
    subprocess.run(["git", "-C", str(demo), *GIT_ID, *args], check=True, capture_output=True)


def main(cwd=None):
    """Make the scratch copy; returns the exit code."""
    demo = Path(tempfile.mkdtemp(prefix="repacted-demo.")).resolve()
    top = repo_top(cwd or Path.cwd())
    if top is not None and demo.is_relative_to(top):
        demo.rmdir()
        print(f"refused: the scratch directory {demo} is inside this repository ({top}). The demo "
              f"never writes into your repository. Way through: point TMPDIR outside the "
              f"repository, then run /repacted:demo again.")
        return 1
    for name in FILES:
        shutil.copy2(TOY / name, demo / name)
    try:
        git(demo, "init", "-q")
        git(demo, "add", "-A")
        git(demo, "commit", "-qm", "Start from the demo project.")
    except (OSError, subprocess.CalledProcessError):
        print("note: git is unavailable, so the copy has no starting commit; the demo still runs.")
    print(f"demo dir: {demo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
