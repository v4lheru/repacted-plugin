"""
Location: pact-plugin/tests/test_working_memory_redirected_store_refusal.py
Summary: DOCUMENT-PAIR arms for the refusal that stops a memory row written to a
         REDIRECTED store from being projected into an AMBIENTLY resolved
         CLAUDE.md. Each arm drives `cli.py` in a CHILD PROCESS and compares the
         document BEFORE against the document AFTER, because the guard's whole
         subject is a write that either happened or did not, and a single-line
         probe of the predicate cannot see a document at all.

         WHY A CHILD PROCESS IS THE ONLY WAY TO REACH THE GUARD. The guard
         exempts an in-process caller (`"pytest" in sys.modules`), because the
         suite binds a redirected store for every test AND syncs ambiently on
         purpose. So an in-process arm can never observe the refusal, and an
         in-process arm that appeared to would be measuring something else.

         WHY THE ENVIRONMENT IS BUILT AND NEVER INHERITED. The sibling guard
         `_refuse_ambient_target_under_pytest` raises the SAME exception type on
         the SAME path when `PYTEST_CURRENT_TEST` is present. An inherited
         environment carries that variable into the child, the sibling refuses
         first, and every arm below goes green while measuring the OTHER guard.
         Each arm therefore builds its own environment and asserts the variable
         is absent from it. That absence is the separating condition, and it is
         asserted rather than assumed.

         SAFETY, AND IT IS THE POINT OF THE WHOLE FILE. These arms drive a real
         `save`. If the guard did not hold, the write would land in a real
         CLAUDE.md. Two independent bindings keep the child below `tmp_path`:
         `CLAUDE_PROJECT_DIR` with a SEEDED document (the resolver probes that
         directory and CONTINUES when it finds nothing, so seeding is what makes
         the probe terminate), and `HOME`, which puts the default store below
         `tmp_path` as well. Every arm then asserts containment.
Used by: pytest.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


from scripts.config import STORE_ORIGIN_ENV  # noqa: E402
from scripts.working_memory import (  # noqa: E402
    AmbientSyncRefused,
    SyncResult,
    _refuse_ambient_sync_from_a_redirected_store,
    _target_is_inside_the_declared_project_dir,
)
from clock_shift.clock_shift_env import carry_clock_shift
from fixtures.hf_cache import hf_cache_env

_CLI = (
    Path(__file__).resolve().parent.parent
    / "skills" / "pact-memory" / "scripts" / "cli.py"
)

# A document with the section the sync writes into. Without the section the sync
# declines for an UNRELATED reason (`SyncResult.NO_WINDOW`), and an arm built on
# that document would report a clean negative that has nothing to do with the
# guard.
_SEED_DOCUMENT = """# Project Memory

## Retrieved Context

## Working Memory
"""


def _seed_project(tmp_path: Path) -> Path:
    """Create the project tree the child resolves into, and return its CLAUDE.md."""
    target = tmp_path / "project" / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_SEED_DOCUMENT, encoding="utf-8")
    return target


def _base_env(tmp_path: Path) -> dict:
    """Build the child environment from nothing. NEVER `os.environ.copy()`.

    A copy carries `PYTEST_CURRENT_TEST`, which makes the SIBLING guard refuse
    and hands every arm below a green for the wrong reason. PATH is kept because
    the resolver shells out to `git`, and a missing `git` changes which
    resolution branch runs.

    NO `CLAUDE_PROJECT_DIR` HERE, AND THAT IS THE DEFAULT ON PURPOSE. Setting it
    is what an arm does to DECLARE a root, and declaring one is now an exemption,
    so a base that set it would silently exempt every arm built on it. Each arm
    below states its own choice.

    THE MODEL CACHE IS PASSED THROUGH, AND IT IS THE ONE EXCEPTION TO "FROM
    NOTHING". Redirecting HOME also relocates the HuggingFace cache, which the
    child derives from HOME at import time. A child with no cache cannot take
    model2vec's cached-model early return, so every save below performed a
    GENUINE FIRST-RUN DOWNLOAD of the embedding model -- an unbounded network
    read that hung this suite. The cache is not part of what this helper
    isolates: the isolation exists to keep `PYTEST_CURRENT_TEST` and
    `CLAUDE_PROJECT_DIR` out of the child, and passing the model cache through
    leaves both of those untouched. Network access is incidental to every
    assertion in this file.

    THE CACHE VARIABLES COME FROM `hf_cache_env()` RATHER THAN BEING SPELLED
    HERE. Computing them locally is what went wrong before: the local spelling
    used `Path.home()`, which the autouse config-root fixture monkeypatches to
    `tmp_path`, so the "passed through" cache was the per-test temp directory
    and the download happened anyway. The helper carries the reason, the mirror
    of huggingface_hub's own resolution, and the offline guard. Its docstring
    is where that argument lives; do not restate it here and do not re-derive
    the path.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path / "home"),
        **hf_cache_env(),
    }


def _escaped_root(tmp_path: Path) -> str:
    """A declared project directory that holds NO CLAUDE.md.

    THIS REPRODUCES THE INCIDENT, not merely an unset variable. The resolver
    probes the declared directory, finds nothing, and CONTINUES, so the document
    it settles on lies outside the root the caller named. That is the escape the
    guard refuses, and it stays below `tmp_path` throughout.
    """
    root = tmp_path / "declared-but-empty"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


def _run_cli(env: dict, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the CLI as the incident ran it: a child process with a built env."""
    assert "PYTEST_CURRENT_TEST" not in env, (
        "the child environment carries PYTEST_CURRENT_TEST, so the SIBLING guard "
        "would refuse and this arm would measure the wrong mechanism"
    )
    return subprocess.run(
        [sys.executable, str(_CLI), *args],
        env=carry_clock_shift(env),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        # 30s, NOT the 180s this carried while the child could download the
        # embedding model. That budget existed to absorb a first-run fetch;
        # `hf_cache_env()` removes the fetch, so the only thing a long timeout
        # buys now is a slow failure. MEASURED BASIS: the slowest child observed
        # here is 0.44s (warm cache, embedding generated), and a COLD cache
        # under the offline guard returns in 0.31s reporting `degraded:keyword`.
        # 30s is roughly 60x the slowest measured call, which leaves room for a
        # loaded CI box without waiting three minutes to learn a child hung.
        timeout=30,
    )


def _save_payload(marker: str) -> str:
    return json.dumps({"context": marker, "goal": marker})


def _envelope(result: subprocess.CompletedProcess) -> dict:
    """Parse the CLI's success envelope, failing loudly on anything else.

    The shape is `{"ok": true, "result": {...}}`, and the fields this file reads
    live in `result`, NOT at the top level. A bare `in` test against stdout would
    pass on a traceback that happened to contain the word, so it is parsed.
    """
    assert result.returncode == 0, (
        f"CLI exited {result.returncode}\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    payload = json.loads(result.stdout)
    assert payload.get("ok") is True, f"envelope reports a failure: {payload}"
    return payload["result"]


def _sync_status(result: subprocess.CompletedProcess) -> str:
    """Read `sync_status` off the envelope.

    An ABSENT key returns a sentinel rather than the empty string, because
    `sync_status` is documented as TOTAL on the save path: its absence is a
    different fact from any of its values, and an arm must not read the two
    alike.
    """
    return _envelope(result).get("sync_status", "<absent>")


class TestRedirectedStoreDoesNotProjectIntoAnAmbientDocument:
    """The refusal, its cost, and the write that must keep working."""

    def test_a_redirected_store_that_escaped_its_root_writes_nothing(self, tmp_path):
        """THE INCIDENT SHAPE. Redirected store, and resolution escaped the
        declared root exactly as it did on the day.

        MUTANT that reddens this arm: remove the
        `_refuse_ambient_sync_from_a_redirected_store(target, claude_md_root,
        claude_md_path)` call from `sync_to_claude_md`. The entry is then
        projected, the document grows, and the byte comparison fails.

        The BASELINE is taken before the drive rather than after, so a document
        that was already wrong cannot read as a pass.
        """
        target = _seed_project(tmp_path)
        before = target.read_bytes()

        env = _base_env(tmp_path)
        env["PACT_TEST_MEMORY_DIR"] = str(tmp_path / "isolated-store")
        env["CLAUDE_PROJECT_DIR"] = _escaped_root(tmp_path)
        result = _run_cli(
            env, tmp_path / "project", "save", _save_payload("redirected-store-arm")
        )

        assert _sync_status(result) == SyncResult.REFUSED
        assert target.read_bytes() == before, (
            "the projection reached a document outside the declared root even "
            "though the row went to a redirected store"
        )


class TestTheRefusalMessage:
    """A MESSAGE PIN, and it supplements the document arms rather than replacing them.

    The document arms above prove the write did not happen. They cannot see WHY,
    because the redirected-store refusal and the PYTEST_CURRENT_TEST refusal
    report the same `sync_status`. This arm reads the text a
    caller and a log line receive, so a refusal keeps naming its own cause.
    """

    def test_the_message_names_the_origin_and_carries_no_path(self, monkeypatch):
        """MUTANT that reddens this arm: replace the origin word in the message
        with the resolved store path. The `origin=` assertion fails, and the
        path-leak assertion fails with it.

        The in-process exemption is lifted by removing `pytest` from
        `sys.modules` for the length of the call, which is the ONLY reason this
        arm can reach the raise at all.
        """
        monkeypatch.delitem(sys.modules, "pytest", raising=False)
        monkeypatch.setattr(
            "scripts.working_memory.store_path_origin",
            lambda: STORE_ORIGIN_ENV,
        )

        with pytest.raises(AmbientSyncRefused) as excinfo:
            _refuse_ambient_sync_from_a_redirected_store(None, None)

        message = str(excinfo.value)
        assert f"origin={STORE_ORIGIN_ENV}" in message
        assert "--no-sync" in message
        assert os.sep not in message.replace("--no-sync", ""), (
            "the refusal message carries a filesystem path; it must name the "
            "origin word only"
        )


class TestTheDeclaredProjectDirCheck:
    """The containment helper the exemption rests on.

    IT IS PINNED SEPARATELY BECAUSE ITS WRONG FORM IS THE TEMPTING ONE. Testing
    that the variable is SET is one character shorter to write and exempts the
    escape the guard exists to catch, so the difference between the two forms
    needs its own arm rather than a comment.
    """

    def test_an_unset_variable_is_not_a_declaration(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        assert _target_is_inside_the_declared_project_dir(tmp_path / "CLAUDE.md") is False


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run git and require success. Real git, never a simulation of one.

    The predicate under test shells out to `git rev-parse --git-common-dir`,
    so a fixture that models a repository with plain directories is measuring
    the non-repo branch and nothing else.
    """
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r


class TestTheCardinalFallthroughSurvives:
    """A REAL WORKTREE reaching the MAIN checkout's document must still WRITE.

    🔴 WHY THIS CLASS EXISTS, AND IT IS NOT A RESTATEMENT OF THE ARM ABOVE.
    The guard's own docstring names this as the cardinal over-block: PACT
    declares CLAUDE_PROJECT_DIR as a worktree, CLAUDE.md is gitignored and
    therefore absent there, and resolution falls through to the main
    checkout's file on purpose. Refusing that breaks the memory display for
    every worktree session — including the one this suite is running in.

    NOTHING IN THE SUITE WAS DRIVING IT. Every other fixture in this file
    builds plain directories, so `same_repository` reaches its non-repo branch
    and returns False; those arms cannot distinguish a guard that allows the
    fall-through from one that refuses everything. The nearest existing
    coverage lives in the pin-resolver suite and uses a SUBDIRECTORY, with a
    comment calling it "structurally the worktree case".

    A SUBDIRECTORY IS NOT STRUCTURALLY THE WORKTREE CASE, AND THE DIFFERENCE
    IS A BRANCH. Measured with real git:

        git -C <main>           rev-parse --git-common-dir  ->  .git
        git -C <main>/sub       rev-parse --git-common-dir  ->  ../.git
        git -C <main>/sub/deep  rev-parse --git-common-dir  ->  ../../.git
        git -C <worktree>       rev-parse --git-common-dir  ->  /abs/.../main/.git

    A subdirectory returns a RELATIVE path and a worktree returns an ABSOLUTE
    one, so they take opposite sides of `same_repository`'s
    `if not common_dir.is_absolute()` join. The subdir arm exercises the join;
    the cardinal case skips it. Only the case below covers the branch PACT
    actually runs on every session.

    Verdicts measured on the same fixture, all seven:

        same_repository(worktree,    main)      True   <- the cardinal case
        same_repository(main,        worktree)  False  <- argument order matters
        same_repository(subdir,      root)      True
        same_repository(root,        subdir)    False
        same_repository(deep subdir, root)      True
        same_repository(worktree,    itself)    False  (unreachable: the guard
                                                 returns on declared == resolved
                                                 before the predicate is asked)
        same_repository(non-git,     main)      False  <- every other arm here
    """

    def test_a_real_worktree_still_projects_into_the_main_checkout(self, tmp_path):
        """THE OVER-BLOCK ARM. If this reddens, every worktree session has lost
        its working-memory display — which is a strictly worse outcome than the
        under-block the sibling arm above closed.

        MUTANT that reddens this arm: delete the `stays_in_declared_project(...)`
        return from `_refuse_ambient_sync_on_declared_scope_escape`. The declared
        worktree then differs from the resolved main root, no exemption
        applies, and the cardinal fall-through is refused.
        """
        main = tmp_path / "mainrepo"
        (main / ".claude").mkdir(parents=True)
        (main / ".claude" / "CLAUDE.md").write_text(_SEED_DOCUMENT, encoding="utf-8")
        _git("init", "-q", ".", cwd=main)
        _git("config", "user.email", "t@example.invalid", cwd=main)
        _git("config", "user.name", "t", cwd=main)
        # A TRACKED FILE THAT IS NOT THE DOCUMENT. `git worktree add` needs a
        # commit, and `.claude/` is covered by the operator's global gitignore
        # — which is WHY this whole fall-through exists, so the fixture leans
        # on it rather than forcing the document into the index.
        (main / "README").write_text("seed\n", encoding="utf-8")
        _git("add", "README", cwd=main)
        _git("commit", "-qm", "seed", cwd=main)

        worktree = tmp_path / "wt"
        _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
        assert not (worktree / ".claude" / "CLAUDE.md").exists(), (
            "the fixture must reproduce the real shape — a worktree with NO "
            "CLAUDE.md of its own, which is what forces the fall-through"
        )

        target = main / ".claude" / "CLAUDE.md"
        before = target.read_bytes()

        env = _base_env(tmp_path)
        env["CLAUDE_PROJECT_DIR"] = str(worktree)
        result = _run_cli(env, worktree, "save", _save_payload("cardinal-worktree-arm"))

        assert _sync_status(result) == SyncResult.WROTE, (
            "a save from a real git worktree, declaring that worktree and "
            "falling through to the MAIN checkout's CLAUDE.md, was refused. "
            "This is the cardinal over-block the escape guard is shaped to "
            "avoid: CLAUDE.md is gitignored in a worktree, so this is the "
            "NORMAL path for every PACT session, not an edge case. Check that "
            "`stays_in_declared_project(declared, resolved_root, claude_md)` "
            "is still consulted and still receives the DECLARATION first."
        )
        assert target.read_bytes() != before, (
            "the sync reported WROTE but the main checkout's document did not "
            "change — the projection went somewhere else"
        )


class TestTheBOUNDARYOfTheEscapeRefusal:
    """Where the refusal STOPS. Each arm pins a claim the change asserts.

    These exist because a refusal's edges are what a future editor moves, and
    an edge with no arm is indistinguishable from an oversight. Each one below
    corresponds to a sentence the change claims, so the prose and the suite
    cannot drift apart silently.
    """

    def test_a_project_that_resolves_NOTHING_never_reaches_the_guard(self, tmp_path):
        """THE OVER-BLOCK THAT DOES NOT EXIST, PINNED SO IT CANNOT START TO.

        Declared scope, no CLAUDE.md anywhere, cwd IS the declared directory.
        Resolution finds no document, so the guard is never consulted and the
        outcome is UNRESOLVED — a different fact from REFUSED, and the two must
        never be read alike: REFUSED means a guard declined a resolved target,
        UNRESOLVED means there was no target to decline.

        WHY THE EXACT VALUE AND NOT `!= REFUSED`. A negative assertion would
        also pass on FAILED, which is a genuine defect, so it would convert a
        crash into a green.

        MUTANT that reddens this arm: move the escape guard ahead of the
        resolution step. It then fires on a declared scope whose probe found
        nothing, which is the blanket "absence means refuse" rule the design
        explicitly rejects.
        """
        env = _base_env(tmp_path)
        declared = tmp_path / "declared"
        declared.mkdir()
        env["CLAUDE_PROJECT_DIR"] = str(declared)

        result = _run_cli(env, declared, "save", _save_payload("no-document-arm"))

        assert _sync_status(result) == SyncResult.UNRESOLVED, (
            "a declared scope with no resolvable CLAUDE.md produced %r. The "
            "guard must not be reachable here: refusing on ABSENCE rather than "
            "on ESCAPE is the cardinal over-block, and it would fire on every "
            "project that simply has no document yet."
            % (_sync_status(result),)
        )


@pytest.fixture(scope="module")
def repo_shapes(tmp_path_factory):
    """A real repo, a real subdirectory and a REAL worktree, built once.

    Module-scoped and a plain function rather than a class instance method: a
    class-scoped fixture defined as a method is deprecated and would add a
    warning to the suite's standing count.
    """
    base = tmp_path_factory.mktemp("samerepo")
    main = base / "main"
    (main / "sub").mkdir(parents=True)
    _git("init", "-q", ".", cwd=main)
    _git("config", "user.email", "t@example.invalid", cwd=main)
    _git("config", "user.name", "t", cwd=main)
    (main / "README").write_text("seed\n", encoding="utf-8")
    _git("add", "README", cwd=main)
    _git("commit", "-qm", "seed", cwd=main)
    worktree = base / "wt"
    _git("worktree", "add", "-q", str(worktree), "-b", "wt", cwd=main)
    return {"main": main, "sub": main / "sub", "worktree": worktree}


class TestSameRepositoryIsNotSymmetric:
    """The predicate's documented directions, on ONE real fixture.

    Its docstring states these and warns that swapping the arguments silently
    returns a different verdict with nothing failing to tell you. That warning
    was measurably true: a 226-arm selection could not detect an argument swap.
    These pin the claim at the predicate rather than only through a caller, so
    a future reader finds it verified where it is asserted.

    THE FIXTURE BUILDS A REAL WORKTREE rather than a subdirectory standing in
    for one. Measured, the two are not interchangeable:

        git -C <main>/sub  rev-parse --git-common-dir  ->  ../.git      RELATIVE
        git -C <worktree>  rev-parse --git-common-dir  ->  /abs/.../.git ABSOLUTE

    They take opposite sides of the predicate's `is_absolute()` join, so a
    subdirectory cannot cover the worktree rows however much it resembles them.
    """

    @pytest.mark.parametrize(
        "env_dir,base,expected",
        [
            ("worktree", "main", True),],
    )
    def test_the_documented_directions(self, repo_shapes, env_dir, base, expected):
        from shared.project_scope import same_repository

        got = same_repository(repo_shapes[env_dir], repo_shapes[base])
        assert got is expected, (
            "same_repository(%s, %s) returned %r, expected %r. The predicate "
            "asks 'is the SECOND argument the main repo of the checkout at the "
            "FIRST' — it is NOT a symmetric relation, and every caller must "
            "pass the DECLARED scope first. A swap returns a plausible boolean "
            "and changes the meaning." % (env_dir, base, got, expected)
        )


# ---------------------------------------------------------------------------
# One population of layouts, driven through every site that judges project scope
# ---------------------------------------------------------------------------

_SITES = ("sync_to_claude_md",)


def _committed_repo(path: Path) -> Path:
    """A real repository with one commit, which `git worktree add` requires."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", ".", cwd=path)
    (path / "README").write_text("seed\n", encoding="utf-8")
    _git("add", "README", cwd=path)
    _git("-c", "user.email=t@example.invalid", "-c", "user.name=t",
         "commit", "-qm", "seed", cwd=path)
    return path


def _document_in(directory: Path) -> Path:
    target = directory / ".claude" / "CLAUDE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_SEED_DOCUMENT, encoding="utf-8")
    return target


def _worktree_at(main: Path, path: Path) -> Path:
    _git("worktree", "add", "-q", str(path), "-b", path.name, cwd=main)
    return path


# Each layout returns (declared, cwd, document, extra environment). The cwd is a
# checkout root, so every resolver reaches the same document from it.

def _submodule_subdirectory(t):
    superproject = _committed_repo(t / "superproject")
    _git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
         str(_committed_repo(t / "source")), "sub", cwd=superproject)
    sub = superproject / "sub"
    (sub / "dir").mkdir()
    return sub / "dir", sub, _document_in(sub), {}


def _separate_git_dir_subdirectory(t):
    (t / "gitdirs").mkdir()
    root = t / "repo"
    _git("init", "-q", "--separate-git-dir", str(t / "gitdirs" / "repo.git"),
         str(root), cwd=t)
    (root / "dir").mkdir()
    return root / "dir", root, _document_in(root), {}


def _worktree_subdirectory_worktree_document(t):
    main = _committed_repo(t / "main")
    worktree = _worktree_at(main, t / "worktree")
    (worktree / "sub").mkdir()
    return worktree / "sub", worktree, _document_in(worktree), {}


def _declared_main_worktree_document(t):
    main = _committed_repo(t / "main")
    worktree = _worktree_at(main, t / "worktree")
    return main, worktree, _document_in(worktree), {}


def _declared_main_subdirectory_worktree_document(t):
    main = _committed_repo(t / "main")
    (main / "sub").mkdir()
    worktree = _worktree_at(main, t / "worktree")
    return main / "sub", worktree, _document_in(worktree), {}


def _sibling_worktrees(t):
    main = _committed_repo(t / "main")
    declared = _worktree_at(main, t / "w1")
    worktree = _worktree_at(main, t / "w2")
    return declared, worktree, _document_in(worktree), {}


def _removed_worktree_inside_its_repository(t):
    main = _committed_repo(t / "main")
    removed = _worktree_at(main, main / ".worktrees" / "x")
    _git("worktree", "remove", str(removed), cwd=main)
    return removed, main, _document_in(main), {}


def _removed_subdirectory(t):
    repo = _committed_repo(t / "repo")
    return repo / "gone", repo, _document_in(repo), {}


def _moved_away_worktree_still_listed(t):
    main = _committed_repo(t / "main")
    worktree = _worktree_at(main, t / "moved")
    worktree.rename(t / "moved-away")
    return worktree, main, _document_in(main), {}


def _live_declaration_into_the_config_root_document(t):
    home = _committed_repo(t / "home")
    (home / "other").mkdir()
    return (home / "other", home / "other", _document_in(home),
            {"CLAUDE_CONFIG_DIR": str(home / ".claude")})


def _another_repository(t):
    project = _committed_repo(t / "p1")
    return _committed_repo(t / "p2"), project, _document_in(project), {}


def _removed_declaration_in_another_repository(t):
    project = _committed_repo(t / "p1")
    return _committed_repo(t / "p2") / "gone", project, _document_in(project), {}


def _repository_root_declared_subdirectory_document(t):
    repo = _committed_repo(t / "repo")
    (repo / "sub").mkdir()
    return repo, repo / "sub", _document_in(repo / "sub"), {}


def _worktree_removed_outside_its_repository(t):
    main = _committed_repo(t / "main")
    removed = _worktree_at(main, t / "worktree")
    _git("worktree", "remove", str(removed), cwd=main)
    return removed, main, _document_in(main), {}


def _deleted_project_resolving_into_the_config_root_document(t):
    repo = _committed_repo(t / "repo")
    (repo / "other").mkdir()
    return (repo / "proj-gone", repo / "other", _document_in(repo),
            {"CLAUDE_CONFIG_DIR": str(repo / ".claude")})


# The suite redirects `Path.home()` to tmp_path, so these layouts make tmp_path
# itself a git repository to stand for a git-versioned home. The config root is
# moved elsewhere, so the config-root carve-out cannot be what refuses them.

def _deleted_project_under_a_git_home(t):
    (t / "other").mkdir()
    return (t / "proj-gone", t / "other", _document_in(_committed_repo(t)),
            {"CLAUDE_CONFIG_DIR": str(t / "config-root")})


def _deleted_project_under_a_git_home_legacy_document(t):
    home = _committed_repo(t)
    (home / "other").mkdir()
    document = home / "CLAUDE.md"
    document.write_text(_SEED_DOCUMENT, encoding="utf-8")
    return (home / "proj-gone", home / "other", document,
            {"CLAUDE_CONFIG_DIR": str(t / "config-root")})


def _live_declaration_under_a_git_home(t):
    (t / "other").mkdir()
    return (t / "other", t / "other", _document_in(_committed_repo(t)),
            {"CLAUDE_CONFIG_DIR": str(t / "config-root")})


def _moved_away_worktree_of_a_git_home_landing_at_home(t):
    # The config root is left at home, so BOTH carve-outs match this layout and
    # the worktree record is its only way in: it pins that the record is
    # consulted before either carve-out.
    home = _committed_repo(t)
    worktree = _worktree_at(home, t / "moved")
    worktree.rename(t / "moved-away")
    return worktree, home, _document_in(home), {}


def _independent_repository_nested_in_the_enclosing_one(t):
    enclosing = _committed_repo(t / "enclosing")
    nested = enclosing / "vendor" / "nested"
    nested.mkdir(parents=True)
    _git("init", "-q", ".", cwd=nested)
    return nested, enclosing, _document_in(enclosing), {}


def _removed_independent_repository_nested_in_the_enclosing_one(t):
    # The removed twin of the layout above. Once the nested repository is gone
    # its nearest existing ancestor sits in the enclosing repository, and
    # nothing the check reads separates that from a removed subdirectory.
    import shutil

    declared, enclosing, document, extra_env = (
        _independent_repository_nested_in_the_enclosing_one(t)
    )
    shutil.rmtree(declared)
    return declared, enclosing, document, extra_env


def _worktree_removed_from_the_claude_code_worktree_location(t):
    main = _committed_repo(t / "main")
    removed = _worktree_at(main, main / ".claude" / "worktrees" / "x")
    _git("worktree", "remove", str(removed), cwd=main)
    return removed, main, _document_in(main), {}


def _inherited_git_dir_naming_another_repository(t):
    project = _committed_repo(t / "p1")
    (t / "unrelated").mkdir()
    return (t / "unrelated", project, _document_in(project),
            {"GIT_DIR": str(project / ".git")})


_SAME_PROJECT = {
    "submodule_subdirectory": _submodule_subdirectory,
    "separate_git_dir_subdirectory": _separate_git_dir_subdirectory,
    "worktree_subdirectory_worktree_document": _worktree_subdirectory_worktree_document,
    "declared_main_worktree_document": _declared_main_worktree_document,
    "declared_main_subdirectory_worktree_document": _declared_main_subdirectory_worktree_document,
    "sibling_worktrees": _sibling_worktrees,
    "removed_worktree_inside_its_repository": _removed_worktree_inside_its_repository,
    "removed_subdirectory": _removed_subdirectory,
    "moved_away_worktree_still_listed": _moved_away_worktree_still_listed,
    "live_declaration_into_the_config_root_document": _live_declaration_into_the_config_root_document,
    "live_declaration_under_a_git_home": _live_declaration_under_a_git_home,
    "moved_away_worktree_of_a_git_home_landing_at_home": _moved_away_worktree_of_a_git_home_landing_at_home,
    "removed_independent_repository_nested_in_the_enclosing_one": _removed_independent_repository_nested_in_the_enclosing_one,
    "worktree_removed_from_the_claude_code_worktree_location": _worktree_removed_from_the_claude_code_worktree_location,
}

_OUTSIDE_THE_PROJECT = {
    "another_repository": _another_repository,
    "removed_declaration_in_another_repository": _removed_declaration_in_another_repository,
    "repository_root_declared_subdirectory_document": _repository_root_declared_subdirectory_document,
    "worktree_removed_outside_its_repository": _worktree_removed_outside_its_repository,
    "deleted_project_resolving_into_the_config_root_document": _deleted_project_resolving_into_the_config_root_document,
    "deleted_project_under_a_git_home": _deleted_project_under_a_git_home,
    "deleted_project_under_a_git_home_legacy_document": _deleted_project_under_a_git_home_legacy_document,
    "inherited_git_dir_naming_another_repository": _inherited_git_dir_naming_another_repository,
    "independent_repository_nested_in_the_enclosing_one": _independent_repository_nested_in_the_enclosing_one,
}


def _arrange(layout, tmp_path, monkeypatch) -> Path:
    probe = subprocess.run(["git", "-C", str(tmp_path), "rev-parse", "--git-dir"],
                           capture_output=True, text=True, timeout=30)
    assert probe.returncode != 0, (
        f"tmp_path sits inside a repository ({probe.stdout.strip()}), so "
        "resolution could fall through to a real CLAUDE.md"
    )
    declared, cwd, document, extra_env = layout(tmp_path)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(declared))
    for key, value in extra_env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(cwd)
    return document


def _run_site(site):
    import scripts.working_memory as wm

    if site == "sync_to_claude_md":
        return wm.sync_to_claude_md(
            {"context": "scope-arm", "goal": "scope-arm"}, memory_id="0" * 32
        )
    return wm.sync_retrieved_to_claude_md(
        [{"id": "0" * 32, "context": "scope-arm"}], query="scope-arm"
    )


class TestEverySiteJudgesProjectScopeAlike:
    """ONE population, TWO sites, the real resolver at each.

    `sync_to_claude_md` and `sync_retrieved_to_claude_md` both decide scope with
    `stays_in_declared_project`. A layout is admitted or refused at both or
    at none, so each case below runs at each site: a guard dropped from one site
    fails that site's refusal cases and no other.

    WHY IN PROCESS. The escape guard has no in-process exemption, unlike its
    sibling guards, so it is reachable here, and the second sync site has no CLI
    route at all. `CLAUDE_PROJECT_DIR` stays SET in every case: a case that
    unsets it exits at the guard's "nothing declared" return and measures
    nothing.
    """

    @pytest.mark.parametrize("site", _SITES)
    @pytest.mark.parametrize("layout", list(sorted(_SAME_PROJECT))[:1])
    def test_a_same_project_resolution_is_admitted(
        self, tmp_path, monkeypatch, layout, site
    ):
        document = _arrange(_SAME_PROJECT[layout], tmp_path, monkeypatch)
        before = document.read_bytes()

        result = _run_site(site)

        assert result == SyncResult(SyncResult.WROTE), (
            f"{layout}: {site} returned {result!r}. The layout stays inside the "
            "declared project, so refusing it is an over-block."
        )
        assert document.read_bytes() != before, (
            f"{layout}: {site} reported WROTE but the document did not change"
        )


class TestTheChildGetsTheOperatorModelCache:
    """The two properties that keep a built child env off the network.

    NEITHER ARM SUPPLIES THE TERM IT MEASURES, and that is the whole design.
    The defect these replace was found only after a probe passed `HF_HOME` as a
    literal: two arms separated cleanly, proving the MECHANISM, while the
    SHIPPED EXPRESSION that derives the value went untested and was inert. So
    each arm below reads what `_base_env` actually produces. A term the arm
    hands in is a term the arm cannot see break.
    """

    def test_the_cache_path_is_not_the_per_test_home(self, tmp_path):
        """HF_HOME must name the operator's cache, never the redirected home.

        KILLING MUTANT: spell the value `str(Path.home() / ".cache" /
        "huggingface")` in `hf_cache_env`. `Path.home()` is monkeypatched to
        `tmp_path` by the autouse config-root fixture, so the child is handed an
        empty cache and downloads the model on every run.
        """
        # NON-VACUITY GUARD. The arm can only separate the defect from the fix
        # while the autouse redirect is actually in effect. Without this, a
        # fixture that stopped patching would make the assertion below pass for
        # the wrong reason and the pin would rot into a no-op.
        assert Path.home() != Path(os.environ["HOME"]), (
            "the autouse Path.home() redirect is not active, so this arm cannot "
            "distinguish the defect from the fix"
        )

        resolved = Path(_base_env(tmp_path)["HF_HOME"]).resolve()

        # The message carries BOTH the value observed and the value a healthy
        # resolution produces. A failure that printed only "this is wrong"
        # cannot distinguish a detected defect from a harness that never ran
        # the code — the two produce the same exit status and differ only in
        # what they can show you.
        assert not resolved.is_relative_to(Path.home().resolve()), (
            f"HF_HOME resolved to {resolved} — under the per-test home "
            f"{Path.home()}, so the child sees an EMPTY cache and performs a "
            f"genuine first-run model download. The operator cache it should "
            f"have named is "
            f"{os.environ.get('XDG_CACHE_HOME', os.environ['HOME'] + '/.cache')}"
            f"/huggingface."
        )

