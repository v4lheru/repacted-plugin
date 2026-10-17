"""Tests for wordcount.py. One of them fails until the bug in wordcount.py is fixed.

Location: pact-plugin/examples/toy/test_wordcount.py. Plain unittest classes, so they run with
`python3 -m pytest` and, without pytest installed, with `python3 -m unittest`.
"""
import contextlib
import io
import os
import tempfile
import unittest

import wordcount

TEXT = "The cat sat.\nThe dog sat on the cat.\n"


class CountTest(unittest.TestCase):
    def test_counts_lines_and_characters(self):
        lines, _, chars = wordcount.count(TEXT)
        self.assertEqual((lines, chars), (2, len(TEXT)))

    def test_counts_words_across_lines(self):
        self.assertEqual(wordcount.count(TEXT)[1], 9)

    def test_top_words_break_ties_alphabetically(self):
        self.assertEqual(wordcount.top(TEXT, 3), [("the", 3), ("cat", 2), ("sat", 2)])


class MainTest(unittest.TestCase):
    def test_prints_counts_then_top_words(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(TEXT)
        self.addCleanup(os.unlink, handle.name)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = wordcount.main([handle.name, "--top", "1"])
        self.assertEqual(code, 0)
        first, second = out.getvalue().splitlines()
        self.assertTrue(first.startswith("2 lines, ") and first.endswith(f", {len(TEXT)} characters"))
        self.assertEqual(second, "   3  the")

    def test_unreadable_file_exits_1(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = wordcount.main([os.path.join(tempfile.gettempdir(), "no-such-file.txt")])
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err.getvalue())


if __name__ == "__main__":
    unittest.main()
