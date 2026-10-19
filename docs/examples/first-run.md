# Your first run: the demo

The demo shows rePACTed doing one small, real piece of work. It copies a tiny example project into a scratch directory, sends one specialist to fix its one failing test, and prints the session report. It writes nothing to your own repository.

Before you start, finish steps 1 to 4 of the [quickstart](../quickstart.md), so `repact-doctor` passes its checks. It works with plain subagents (the default) and with Agent Teams.

## Run it

Start Claude Code as the [lead](../glossary.md#lead) and run:

```
/repacted:demo
```

## What happens

1. **The scratch copy.** The lead copies `examples/toy/` (a word counter, about 60 lines of Python) into a new directory under your system temp folder and makes it its own git repository:

   ```
   demo dir: /var/folders/.../T/repacted-demo.8uzs3h6k
   ```

   If that directory would land inside your current repository (for example through a `TMPDIR` set inside it), the demo prints a `refused:` line and stops.

2. **The failing test.** The lead runs the toy's tests:

   ```
   FAILED test_wordcount.py::CountTest::test_counts_words_across_lines - Asserti...
   1 failed, 4 passed in 0.01s
   ```

3. **One comPACT round.** The lead runs [`/repacted:comPACT`](../glossary.md#compact) with one backend specialist and the brief "make the failing test pass by fixing the bug; do not edit the test". The specialist works only inside the scratch copy, then writes its report (a [HANDOFF](../glossary.md#handoff)). The lead reads it and runs the tests again:

   ```
   5 passed in 0.00s
   ```

4. **The fix.** The lead commits in the scratch copy and shows the change:

   ```
   -    return text.count("\n"), len(text.split(" ")), len(text)
   +    return text.count("\n"), len(words(text)), len(text)
   ```

   Splitting on a single space counted "sat.\nThe" as one word. The fix counts words the same way the rest of the tool does.

5. **The report.** The lead prints `repact-report` for this session. It is about 20 lines; the first rows look like this (numbers trimmed):

   ```
   spend by role        transcripts   calls   input-side   share   output
     lead                          1     ...
     teammate                      1     ...
   lead wakes           ...
   rejections           ...
   ```

   It shows what each role spent, how often the lead woke up for nothing, and how many reports were sent back.

## Afterwards

The scratch directory is yours to delete. To use rePACTed on your own code, run `/repacted:init --repo` in your repository, then `/repacted:comPACT <task>` on a real task.

The toy also holds `fixtures/fake-credentials.conf`, a made-up key. The demo does not copy it. It is there so the release checks always have one known secret-shaped line to find and accept by its review.
