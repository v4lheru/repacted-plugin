"""Certification of the corrected CLAUDE.md write-path containment predicate.

WHAT IS UNDER CERTIFICATION. `_atomic_write_text` decides containment by KERNEL
OBJECT ANCESTRY on a pinned directory descriptor, in both twins
(`hooks/shared/claude_md_manager.py` and
`skills/pact-memory/scripts/working_memory.py`):

    anchor = (st_dev, st_ino) of os.stat(project_root)
    node   = os.open(target.parent, O_RDONLY|O_DIRECTORY)   # FOLLOWS symlinks
    walk up via os.open("..", dir_fd=node) comparing (st_dev, st_ino)
    until the anchor matches (CONTAINED) or a directory is its own parent
    (filesystem root -> REFUSE)

and then performs temp-create, fchmod, fsync, rename and cleanup THROUGH that
same descriptor. There is no `Path.resolve()`, no `os.path.realpath` and no
string comparison anywhere in the decision. The object CHECKED is the object
MUTATED, by construction rather than by agreement.

CERTIFICATION IS BIDIRECTIONAL, WHICH IS THE POINT. The defect being repaired
lives on the ALLOW branch: the superseded predicate both permitted a two-leg
symlink composition AND refused a benign leaf pointing outside. A suite made
only of refusals passes by refusing everything, which is the cardinal
over-block. Every refusal here is paired with a positive control that writes
successfully in the same harness.

--------------------------------------------------------------------------
THREE HAZARDS THAT DEFEAT A CARELESS SUITE. All three are measured, not
theorised, and each is defended against in a specific test below.
--------------------------------------------------------------------------

1. THE WALK IS UNREACHABLE ON A FLAT TOPOLOGY. The loop tests
   `os.fstat(node)` against the anchor and BREAKS before it ever reaches
   `os.open("..", dir_fd=node)`. So when `target.parent` IS the anchor -- the
   legacy `./CLAUDE.md` shape -- that site executes ZERO times. An injection
   aimed there never fires and the write simply succeeds.
   `test_flat_topology_makes_the_walk_site_unreachable` DEMONSTRATES that
   vacuity rather than asserting it, and every capability test states its
   observed hit count instead of inferring firing from a green result.

2. THE CAPABILITY MESSAGES ARE SPLIT ACROSS SOURCE LITERALS. The walk's
   message is built by implicit concatenation of two adjacent string
   literals, so a line-oriented search of the SOURCE for the rendered phrase
   `refusing write: platform lacks directory-descriptor ancestry traversal`
   returns ZERO matches while the AST-joined constant is that full sentence.
   MEASURED: `grep -c` for the rendered phrase is 0 in BOTH twins; the
   fragment `"ancestry traversal"` sits alone on its own source line.
   CONSEQUENCE FOR ANYONE VERIFYING THIS FILE: grepping the twins for a
   message asserted below will give you a FALSE NEGATIVE and make a correct
   assertion look like it references a string that does not exist. Do not
   "fix" it. The only sound instruments are a RUNTIME assertion on `str(exc)`
   (what every test here does) or an AST join (what
   `TestRefusalMessageContract` does). The messages are written here as
   single unsplit literals precisely so that they remain greppable from the
   TEST side even though they are not from the source side.

3. THE CAPABILITY BRANCH HAS NEVER EXECUTED ON ANY SUPPORTED PLATFORM.
   `os.open` is in `os.supports_dir_fd` on every interpreter measured, so no
   natural path reaches any of the four mappings. The injections in
   `TestCapabilityBranchInjection` are the first and only thing that has ever
   run them. Nothing in this file may be read as evidence that the capability
   is absent anywhere real -- the branch is triggered ARTIFICIALLY here.

DEBUGGING PRIOR, from two independent parties. On a branch that has never
executed, a surprising result is more likely to be the instrument than the
code, and the two most likely instrument faults are a FLAT FIXTURE and a
MIS-SPECIFIED INJECTION PREDICATE, in that order. Establish which you are
holding before reporting a finding -- and if the code really is wrong, that is
a real finding and must not be smoothed over.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# hooks/ and the pact-memory scripts dir on path (mirrors the sibling test
# files: test_containment_guard.py adds hooks/; the scripts dir is added
# here so the twin imports as a top-level `working_memory`).
_TESTS = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# The six refusal messages, as RENDERED at runtime.
#
# Written as single unsplit literals on purpose -- see hazard 2 in the module
# docstring. In the SOURCE, MSG_CAP_WALK is two adjacent literals, so searching
# the twins for it returns nothing.
# ---------------------------------------------------------------------------

MSG_BOUNDARY = "refusing write: cannot establish the containment boundary"
MSG_EXHAUSTION = "refusing write: containment walk did not terminate"
MSG_ESCAPE = "refusing write: target escapes the project containment boundary"
MSG_CAP_WALK = (
    "refusing write: platform lacks directory-descriptor ancestry traversal"
)
MSG_CAP_TEMP = "refusing write: platform lacks directory-descriptor file creation"
MSG_CAP_RENAME = "refusing write: platform lacks directory-descriptor rename"
# The anchor is validated BEFORE the containment walk, so this refusal reports a
# caller error rather than a boundary result. It is registered here because the
# contract is the full rendered SET: an unregistered message is a refusal path
# nobody certified, which is the gap this file exists to close.
MSG_NO_ANCHOR = "refusing write: no containment anchor was supplied"

ALL_MESSAGES = (
    MSG_BOUNDARY,
    MSG_EXHAUSTION,
    MSG_ESCAPE,
    MSG_CAP_WALK,
    MSG_CAP_TEMP,
    MSG_CAP_RENAME,
    MSG_NO_ANCHOR,
)

CANONICAL_REL = "hooks/shared/claude_md_manager.py"
TWIN_REL = "skills/pact-memory/scripts/working_memory.py"
PLUGIN_ROOT = _TESTS.parent

# The ancestry walk's inline iteration bound. Pinned here because the design
# deliberately made it an INLINE LITERAL rather than a module constant (a
# constant would sit outside the region the twin drift gate compares), so this
# assertion is the only thing outside that gate which notices it changing.
WALK_BOUND = 1024

# A deep in-project path must be ALLOWED at a NAMED depth. The number is the
# whole point: the superseded bound admitted 63 levels, so a test at depth 5 or
# 10 passes while proving nothing about it. The certification floor is 70; this
# clears it with margin.
DEEP_DEPTH = 80


# ---------------------------------------------------------------------------
# Twin parametrisation
# ---------------------------------------------------------------------------

_TWIN_PARAMS = ("canonical", "skill")


def _load_twin(which: str):
    if which == "canonical":
        import shared.claude_md_manager as mod
        return mod
    from scripts import working_memory as mod
    return mod


@pytest.fixture(params=_TWIN_PARAMS)
def twin(request):
    """Both copies of the security function, exercised independently.

    WHAT THIS AXIS IS FOR, stated narrowly. The drift gate guarantees TEXT
    identity of the function body. What it structurally cannot catch is
    IDENTICAL TEXT BEHAVING DIFFERENTLY because of module-level context -- a
    different import set, a different ContainmentError class object, a
    different fcntl availability. That is not hypothetical: `uuid` had to be
    added to BOTH twins when the descriptor design landed, and an asymmetry
    there would have been invisible to a body-only gate.

    So this axis covers the obligations whose outcome depends on the predicate
    executing correctly IN ITS MODULE CONTEXT -- the topology matrix, the four
    capability sites, the negative control. Obligations that test CALL-SITE
    integration (per-site anchors, status strings, skip-status shapes) are
    deliberately SINGLE-twin: the twins have genuinely different call sites, so
    duplicating those would assert sameness where difference is CORRECT, and
    the resulting failure would be "fixed" by making the twins wrongly alike.

    The drift gate also does not prove the skill copy is ever EXERCISED, which
    is how a prior non-vacuity experiment reported a healthy number while the
    skill side had almost no behavioural coverage of its own.
    """
    return _load_twin(request.param)


# ---------------------------------------------------------------------------
# Topology helpers
# ---------------------------------------------------------------------------

def _fd_count() -> int:
    """Open descriptors for this process. -1 when /dev/fd is unavailable."""
    try:
        return len(os.listdir("/dev/fd"))
    except OSError:  # pragma: no cover - platform without /dev/fd
        return -1


def _nested_project(root: Path):
    """project/sub/CLAUDE.md -- NESTED, so the ancestry walk actually runs.

    Returns (project, target). Do not replace this with a flat layout: see
    hazard 1. `sub` is one real level below the anchor, which is the minimum
    that makes `os.open("..", dir_fd=)` execute at all.
    """
    project = root / "project"
    holder = project / "sub"
    holder.mkdir(parents=True)
    target = holder / "CLAUDE.md"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    return project, target


def _flat_project(root: Path):
    """project/CLAUDE.md -- FLAT, where target.parent IS the anchor.

    Used ONLY to demonstrate the vacuity hazard. Never use it to certify the
    walk site.
    """
    project = root / "project"
    project.mkdir(parents=True)
    target = project / "CLAUDE.md"
    target.write_text("ORIGINAL\n", encoding="utf-8")
    return project, target


def _two_leg_composition(project: Path, outside: Path) -> Path:
    """The topology that produced the regression, both legs.

    Leg 1: `project/.claude` -> symlink OUT to `outside`.
    Leg 2: `outside/CLAUDE.md` -> symlink back IN to `project/real.md`.

    A predicate that resolves the whole target path lands on `project/real.md`,
    sees it inside the anchor, and ALLOWS. The write traverses only the PARENT
    chain, lands in `outside`, and binds the leaf there as a directory entry --
    so the guard certified a path the write never touched. Returns the
    unresolved target `project/.claude/CLAUDE.md`.
    """
    project.mkdir(parents=True, exist_ok=True)
    outside.mkdir(parents=True, exist_ok=True)
    victim = project / "real.md"
    victim.write_text("IN-PROJECT VICTIM\n", encoding="utf-8")
    os.symlink(str(outside), str(project / ".claude"), target_is_directory=True)
    os.symlink(str(victim), str(outside / "CLAUDE.md"))
    return project / ".claude" / "CLAUDE.md"


def _volume_is_case_insensitive(base: Path) -> bool:
    probe = base / "CaseProbe"
    probe.mkdir()
    return (base / "caseprobe").exists()


def _find_python39():
    """Best-effort discovery of a real 3.9 interpreter, mirroring the
    established pattern in test_bootstrap_gate.py. Returns None when absent --
    callers skip rather than fail."""
    import shutil

    for candidate in (shutil.which("python3.9"), "/usr/bin/python3"):
        if not candidate or not Path(candidate).exists():
            continue
        try:
            probe = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=10
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if (probe.stdout + probe.stderr).strip().startswith("Python 3.9"):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Capability injection harness
# ---------------------------------------------------------------------------

class _CapabilityInjector:
    """Raises at EXACTLY ONE capability site.

    MEMBERSHIP PREDICATE, stated because leaving it unstated is what let the
    count be wrong three times. The covered set is EVERY `os.*` call in
    `_atomic_write_text` whose `NotImplementedError` must not reach the caller
    unmapped. That is SIX sites, of which FOUR are MAPPED to a distinct
    `ContainmentError` and TWO are SWALLOWED.

    THE SIXTH ARRIVED WHEN THE LINE-ENDING RESTORE MOVED INTO THIS FUNCTION,
    AND IT IS THE PREDICATE ABOVE DOING ITS JOB. `_detect_line_ending` opens the
    TARGET through the pinned parent descriptor, so it is a new `os.*` call
    whose NotImplementedError must not reach the caller. It is SWALLOWED rather
    than mapped, and that is deliberate: this read chooses a line ending, it
    decides nothing about containment, so a refusal here would be a new
    over-block on an axis that is not containment. A platform without
    directory-descriptor support still refuses the WRITE one moment later at
    site 3, which is where that refusal belongs.

    `dir_fd` was never the discriminator -- only a proxy close enough to look
    like one. Counting "sites that raise ContainmentError" gives four; counting
    "sites the capability contract covers" gives five; and an argument sweep on
    `dir_fd` misses the parent open (which passes none) while a read of the
    mapped-site list misses the cleanup unlink (which passes one). Three
    observers counted three different sets and each believed the list complete.
    Ask of any new `os.*` call: must its NotImplementedError not reach the
    caller? Yes means it belongs here, whatever its arguments look like.

    Four of the six sites are `os.open`, so a naive patch cannot tell them
    apart. The MECHANICAL discriminator -- an implementation detail of this
    injector, not the definition -- is the triple (dir_fd, path, O_CREAT). The
    third term earns its place: sites 3 and 6 agree on the first two, so a
    predicate built on the pair alone fires at BOTH and lands two hits where
    each row asserts one:

        site 1  parent-directory open   os.open,    dir_fd is None      MAPPED
        site 2  ancestry walk           os.open,    dir_fd, path ".."   MAPPED
        site 3  temp create             os.open,    dir_fd, O_CREAT     MAPPED
        site 4  rename                  os.replace                      MAPPED
        site 5  cleanup unlink          os.unlink,  dir_fd            SWALLOWED
        site 6  line-ending detect      os.open,    dir_fd, no O_CREAT
                                                                     SWALLOWED

    Getting this subtly wrong lands the injection on a DIFFERENT site while
    every assertion still passes -- which is why each test asserts the
    site-distinct message rather than merely that a ContainmentError was
    raised.

    Site 5 needs a PRECONDITION the others do not: the cleanup arm only runs
    after something already failed, so site 5 also raises at the rename to
    reach it. The rename raise is scaffolding; `unlink_hits` is what proves the
    site-5 injection itself fired.

    Also records, for non-vacuity and fd accounting:
      * `hits`         - times the chosen site actually fired
      * `unlink_hits`  - times the cleanup unlink fired (site 5 only)
      * `walk_calls`   - times the walk's os.open was reached at all
      * `parent_closes`- closes counted against the CAPTURED parent fd. Exactly
                         1 is correct; 0 is a leak; 2 is a double-close.
                         Counting closes globally cannot distinguish these.
    """

    def __init__(self, site: int, exc_factory):
        self.site = site
        self.exc_factory = exc_factory
        self.hits = 0
        self.unlink_hits = 0
        self.walk_calls = 0
        self.parent_fd = None
        self.parent_closes = 0
        self._real_open = os.open
        self._real_replace = os.replace
        self._real_close = os.close
        self._real_unlink = os.unlink

    def open(self, path, flags, mode=0o777, *, dir_fd=None, **kw):
        is_walk = dir_fd is not None and path == ".."
        is_parent = dir_fd is None
        if is_walk:
            self.walk_calls += 1
        is_create = dir_fd is not None and path != ".." and bool(flags & os.O_CREAT)
        is_detect = dir_fd is not None and path != ".." and not (flags & os.O_CREAT)
        hit = (
            (self.site == 1 and is_parent)
            or (self.site == 2 and is_walk)
            or (self.site == 3 and is_create)
            or (self.site == 6 and is_detect)
        )
        if hit:
            self.hits += 1
            raise self.exc_factory()
        if dir_fd is None:
            fd = self._real_open(path, flags, mode, **kw)
        else:
            fd = self._real_open(path, flags, mode, dir_fd=dir_fd, **kw)
        if is_parent and flags & os.O_DIRECTORY and self.parent_fd is None:
            self.parent_fd = fd
        return fd

    def replace(self, src, dst, **kw):
        # Site 5 raises here too, as the PRECONDITION that reaches the cleanup
        # arm -- the ContainmentError this produces is the one site 5 asserts
        # survives, so it is scaffolding rather than the subject.
        if self.site in (4, 5):
            self.hits += 1
            raise self.exc_factory()
        return self._real_replace(src, dst, **kw)

    def unlink(self, path, *, dir_fd=None, **kw):
        if self.site == 5:
            self.unlink_hits += 1
            raise self.exc_factory()
        return self._real_unlink(path, dir_fd=dir_fd, **kw)

    def close(self, fd):
        if self.parent_fd is not None and fd == self.parent_fd:
            self.parent_closes += 1
        return self._real_close(fd)

    def install(self, monkeypatch):
        monkeypatch.setattr(os, "open", self.open)
        monkeypatch.setattr(os, "replace", self.replace)
        monkeypatch.setattr(os, "close", self.close)
        monkeypatch.setattr(os, "unlink", self.unlink)


def _unsupported():
    return NotImplementedError("simulated: dir_fd unsupported on this platform")


def _eacces():
    return PermissionError(13, "simulated permission failure")


# ===========================================================================
# FORCE THE CAPABILITY BRANCH BY INJECTION.
# ===========================================================================

# ===========================================================================
# The two-leg composition through real production entry points
# ===========================================================================

class TestTwoLegCompositionThroughProductionEntryPoints:
    """The regression topology, driven through each of the production
    entry points that regressed (the staleness one went with the 1.5.0 cut) -- named individually so this cannot be
    discharged by testing one of them.

    The verdict is keyed on the RETURNED STATUS STRING, not on a filesystem
    delta: an absent file cannot distinguish "refused" from "no-opped for an
    unrelated reason", so a delta-only assertion would pass against a function
    that never reached the write at all.
    """

    _LEGACY = "# Project Memory\n\n## Pinned Context\n\n## Working Memory\n"

    def test_migrate_to_managed_structure_refuses(self, tmp_path, monkeypatch):
        from shared.claude_md_manager import migrate_to_managed_structure

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        target = _two_leg_composition(project, outside)
        # Legacy content reachable THROUGH both legs, so the write path is
        # reached rather than short-circuited by a no-op branch.
        target.write_text(self._LEGACY, encoding="utf-8")
        victim_before = (project / "real.md").read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = migrate_to_managed_structure()

        assert result == (
            "Migration skipped: project CLAUDE.md path precondition not met."
        )
        assert (project / "real.md").read_bytes() == victim_before
        assert "PACT_MANAGED_START" not in (outside / "CLAUDE.md").read_text(
            encoding="utf-8"
        )

    def test_update_session_info_refuses(self, tmp_path, monkeypatch):
        from shared.session_resume import update_session_info

        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        _two_leg_composition(project, outside)
        victim_before = (project / "real.md").read_bytes()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))

        result = update_session_info("sid-123", "PACT-team")

        # EXACT string. `result is None or "skipped" in result` would pass on
        # the None returned when no project dir is set -- a disjunction that
        # cannot fail for the right reason.
        assert result == "Session info skipped: path precondition not met."
        assert (project / "real.md").read_bytes() == victim_before
        assert "SESSION_START" not in (outside / "CLAUDE.md").read_text(
            encoding="utf-8"
        )



# ===========================================================================
# Refusals, each paired with a positive control
# ===========================================================================

class TestPredicateRefusals:
    """Every refusal below is paired with `test_positive_control_*`, which
    writes successfully through the SAME twin in the same harness. Without the
    pairing a refusal assertion is satisfied by a predicate that refuses
    everything."""

    def test_positive_control_plain_nested_write_is_allowed(self, tmp_path, twin):
        """A plain nested write, and the non-vacuity control for this whole class."""
        project, target = _nested_project(tmp_path)
        twin._atomic_write_text(target, "PAYLOAD\n", project)
        assert target.read_text(encoding="utf-8") == "PAYLOAD\n"

    def test_two_leg_composition_refused_at_the_predicate(self, tmp_path, twin):
        """The two-leg composition at the predicate level, for BOTH twins.

        The entry-point tests above drive only the canonical copy, because the
        regressing entry points live there. This is the skill twin's
        independent exercise of the same topology.
        """
        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        target = _two_leg_composition(project, outside)
        victim_before = (project / "real.md").read_bytes()
        outside_before = (outside / "CLAUDE.md").read_bytes()

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "ESCAPED\n", project)

        assert str(excinfo.value) == MSG_ESCAPE
        assert (project / "real.md").read_bytes() == victim_before
        assert (outside / "CLAUDE.md").read_bytes() == outside_before

    def test_symlinked_parent_out_of_project_refused(self, tmp_path, twin):
        """The single-leg parent-out escape."""
        project = tmp_path / "proj"
        outside = tmp_path / "outside"
        project.mkdir()
        outside.mkdir()
        os.symlink(str(outside), str(project / ".claude"), target_is_directory=True)
        target = project / ".claude" / "CLAUDE.md"

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "ESCAPED\n", project)

        assert str(excinfo.value) == MSG_ESCAPE
        assert not (outside / "CLAUDE.md").exists()

    def test_sibling_prefix_refused_structurally(self, tmp_path, twin):
        """`/abc` is refused under anchor `/ab`.

        RATIONALE UPDATED FOR THE CORRECTED PREDICATE. This case used to be
        explained by the choice of `os.path.commonpath` over `str.startswith`;
        that primitive has left the write path entirely. The refusal is now
        STRUCTURAL and has nothing to do with text: `/abc` simply never walks
        up to `/ab`'s inode. The string-prefix trap it guards against cannot
        even be expressed in the current predicate.
        """
        anchor = tmp_path / "ab"
        anchor.mkdir()
        sibling = tmp_path / "abc"  # shares the STRING prefix, not a path child
        sibling.mkdir()
        target = sibling / "CLAUDE.md"
        target.write_text("orig\n", encoding="utf-8")

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", anchor)

        assert str(excinfo.value) == MSG_ESCAPE
        assert target.read_text(encoding="utf-8") == "orig\n"

    def test_absent_parent_refused_at_check_time(self, tmp_path, twin):
        """An absent parent -- strictness, by construction rather than by policy.

        The superseded predicate lexically completed a non-existent parent and
        certified it CONTAINED; if the parent then appeared as an outward
        symlink the write escaped, deterministically and without a race.
        `os.open` on an absent parent raises, so the fail-open closes for free.
        """
        project = tmp_path / "proj"
        project.mkdir()
        target = project / "does-not-exist" / "CLAUDE.md"

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", project)

        # The boundary could not be established at all -- distinct from escape.
        assert str(excinfo.value) == MSG_BOUNDARY

    def test_symlink_loop_in_target_parent_chain_refused(self, tmp_path, twin):
        """A symlink loop in the TARGET parent chain.

        A loop reaches the kernel as ELOOP -- an OSError -- on every
        interpreter, because there is no second, userspace resolver here to
        disagree about whether a loop is even an error. The superseded
        predicate inherited `Path.resolve()`'s version-dependent behaviour on
        exactly this input.
        """
        project = tmp_path / "proj"
        project.mkdir()
        os.symlink(str(project / "loop_b"), str(project / "loop_a"))
        os.symlink(str(project / "loop_a"), str(project / "loop_b"))
        target = project / "loop_a" / "CLAUDE.md"

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", project)

        assert str(excinfo.value) == MSG_BOUNDARY

    def test_symlink_loop_in_anchor_chain_refused(self, tmp_path, twin):
        """A symlink loop in the ANCHOR chain.

        The anchor is `os.stat(project_root)`, so a loop there raises in the
        same handler. Both sides use ONE resolver -- the kernel.
        """
        project, target = _nested_project(tmp_path)
        anchor_a = tmp_path / "anchor_a"
        anchor_b = tmp_path / "anchor_b"
        os.symlink(str(anchor_b), str(anchor_a))
        os.symlink(str(anchor_a), str(anchor_b))

        with pytest.raises(twin.ContainmentError) as excinfo:
            twin._atomic_write_text(target, "new\n", anchor_a)

        assert str(excinfo.value) == MSG_BOUNDARY


# ===========================================================================
# The ALLOW branch, where the repaired defect lives
# ===========================================================================

class TestPredicateAllows:
    """The over-block certification. A guard tested only for refusal passes by
    refusing everything, and the shipped defect was on this branch."""

    def test_benign_symlinked_dot_claude_parent_absolute_allowed(self, tmp_path, twin):
        """A benign symlinked `.claude` PARENT -- and this is the test that FAILS if anyone adds
        O_NOFOLLOW to the parent open.

        `.claude` is a symlink whose target is INSIDE the project. Both the
        superseded predicate and the code before it allowed this. O_NOFOLLOW on
        the parent open would refuse ANY symlinked final component of the
        parent path, which is a NEW over-block on an axis that is not
        containment -- and it would add nothing, because the ancestry test runs
        ON the opened descriptor, so there is no check-then-open gap to close.
        """
        project = tmp_path / "proj"
        real_dir = project / "config" / "claude"
        real_dir.mkdir(parents=True)
        os.symlink(str(real_dir), str(project / ".claude"), target_is_directory=True)
        target = project / ".claude" / "CLAUDE.md"

        twin._atomic_write_text(target, "PAYLOAD\n", project)

        assert (real_dir / "CLAUDE.md").read_text(encoding="utf-8") == "PAYLOAD\n"

# ===========================================================================
# The leaf-outside-root coupling tripwire
# ===========================================================================

# ===========================================================================
# Exhaustion is distinguishable from escape
# ===========================================================================

class _FakeStat:
    """Minimal stand-in: the walk reads only st_dev and st_ino."""

    def __init__(self, dev, ino):
        self.st_dev = dev
        self.st_ino = ino


# ===========================================================================
# Mode determinism under a restrictive umask
# ===========================================================================

# ===========================================================================
# Descriptor hygiene, allow and refuse counted SEPARATELY
# ===========================================================================

# ===========================================================================
# The message contract itself
# ===========================================================================

# ===========================================================================
# Loop handling at the version floor
# ===========================================================================

_FLOOR_PROBE = '''
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, {hooks!r})
from shared.claude_md_manager import _atomic_write_text, ContainmentError

root = Path(tempfile.mkdtemp())
results = []

# target-parent chain loop
p = root / "t" ; p.mkdir()
os.symlink(str(p / "b"), str(p / "a")) ; os.symlink(str(p / "a"), str(p / "b"))
try:
    _atomic_write_text(p / "a" / "CLAUDE.md", "x\\n", p)
    results.append("target:NO-RAISE")
except ContainmentError as e:
    results.append("target:ContainmentError:" + str(e))
except BaseException as e:
    results.append("target:RAW:" + type(e).__name__)

# anchor chain loop
q = root / "q" / "sub" ; q.mkdir(parents=True)
os.symlink(str(root / "ab"), str(root / "aa")) ; os.symlink(str(root / "aa"), str(root / "ab"))
try:
    _atomic_write_text(q / "CLAUDE.md", "x\\n", root / "aa")
    results.append("anchor:NO-RAISE")
except ContainmentError as e:
    results.append("anchor:ContainmentError:" + str(e))
except BaseException as e:
    results.append("anchor:RAW:" + type(e).__name__)

print("|".join(results))
'''


