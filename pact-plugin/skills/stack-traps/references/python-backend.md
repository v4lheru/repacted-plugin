# Python backend stack traps

Context: Python backend behaviour that silently contradicts what the code assumes: event loops
in tests, framework exception funnels, static-file caching, Celery scheduling and redelivery,
python-pptx template mechanics, and Unicode folding. Read this before relying on one of these
guarantees or when a symptom matches an Example.

## Canonical rules
1. A sync test calling `asyncio.run()` closes its loop and leaves none current; later session-scoped pytest-asyncio tests fail "coroutine … was never awaited".
2. Signature: passes alone, fails in the directory run, traceback ends in pytest-asyncio's `get_event_loop`. Run the file alone, then grep `asyncio.run|loop.close|set_event_loop`.
3. `@pytest.mark.asyncio` does not fix it. Use a helper that creates, runs, closes and RESTORES the previous loop in `finally`. (pytest-asyncio session loop scope; not anyio.)
4. An `except <Type> -> "<specific subsystem>"` handler is valid only for exceptions reachable at that call site. Copied where a new dependency exists, it blames a healthy subsystem.
5. Identify a failure by STATE (e.g. `pool is None`), write the reason at the catch site, and do not catch a dependency's errors at your boundary just to hide its gap.
6. FastAPI turns any generic exception from the ASGI `receive` channel into `400 "There was an error parsing the body"`. A body-size limiter must raise an `HTTPException` subclass (413).
7. A `try/except` in your own middleware cannot intercept that raise. Falsify the limiter: raise the cap above the payload and resend; if the 413 persists, something else produces it.
8. Starlette `BaseHTTPMiddleware` OVERWRITES route-set headers: a blanket `Cache-Control` stamp replaced `immutable` on hashed assets. Prefer route headers plus a `StaticFiles` subclass.
9. A bare `FileResponse` never returns 304 (it handles `Range` only). The 304 path lives in `StaticFiles.file_response`; stamp headers on what `super().file_response()` returns.
10. Test the 304 explicitly (`If-None-Match`), and count every path that serves `index.html` (`/`, the catch-all fallback, a file branch) before fixing one. (Starlette 0.41.3.)
11. Celery beat is off unless enabled. One worker replica: embedded `-B`. More than one: a dedicated single-replica `celery beat` service, or each periodic task fires N times.
12. On Railway the worker start command often lives only in the live service config; enabling beat is a deploy gate a human must set. Flag it in the HANDOFF.
13. With `acks_late` on Redis: `visibility_timeout` > hard `task_time_limit` > soft limit > longest real task, or Redis redelivers and the task runs twice (default timeout 3600 s).
14. A per-task `time_limit` above `visibility_timeout` reopens redelivery. `task_autoretry_for=(Exception,)` without `task_max_retries` is an unbounded retry storm.
15. The soft time limit is a safety net; the primary fix for hung jobs is explicit per-call HTTP/SDK timeouts budgeted under it. Calibrate limits from observed production runtimes.
16. python-pptx: dump shape, paragraph and run structure plus positions of source AND target before writing code. Google-Slides shape names are opaque; map by position or label text.
17. Replace text at the FIRST run and delete only sibling `<a:r>` after it. `run.text` alone leaves stale runs; `text_frame.text` wipes formatting.
18. Tables: `deepcopy` an existing `<a:tr>` and `addnext` BEFORE writing rows, or row 3's data overwrites row 2. Copy `pPr`/`rPr` from a reference paragraph to keep styling.
19. A template of repeated literal tokens is fillable: address by (slide, shape index path, paragraph, run, occurrence). Generate the token map from the template at render time.
20. `shape_id` is not unique within a slide. Resolve every index path to a shape OBJECT before mutating, since a deletion shifts later indices. Replace multiple tokens in one run right-to-left.
21. Slide duplication: clone the source part's relationships under the SAME rIds (`relate_to()` allocates new ones, so `r:embed` breaks and PowerPoint "repairs"). `add_slide` appends; reorder `_sldIdLst`.
22. python-pptx 1.0.2 `_next_slide_partname` has no collision check: delete then add slides and two parts share a name; a slide vanishes with only a `UserWarning: Duplicate name`. Rename on collision.
23. Re-open the saved file and scan every shape (recursing into groups) and table for surviving tokens; delete the output if any remain.
24. NFD/NFKD fold base+combining marks, never stroked or dotless letters (ø ł đ ð ı ħ þ ß æ œ). Name folding = NFKD + strip `\p{M}` and `\p{Cf}` + an explicit fold table.
25. Strip `\p{Cf}` (soft hyphen, ZWJ, BOM) only for comparison, never from a returned value. Prefer NFKD to NFD for extracted text (it folds `ﬁ`).
26. Test identifier handling with EQUALITY over paired ASCII/non-ASCII populations plus a negative control; a list of expected `true`s can be satisfied by widening the list.
27. A half-fix that widens a regex without the fold table moves the same names to a different refusal code; measure what the downstream comparison sees, not what the regex accepts.

## Examples
- 1: 13 red tests in an HTTP-ingest module looked like a broken security feature and were one harness fault.
- 4: a copied `except RuntimeError -> "Database connection not available"` reported a model-client failure as a database outage.
- 6: a chunked-body limiter "worked" but returned 400; raising the cap produced 422, proving the guard was load-bearing.
- 22: a deck re-render after deleting a slide silently dropped a different slide.
- 24: three of ten non-ASCII names (ø, ł, ı) still failed after "the normaliser already NFD-strips these".
