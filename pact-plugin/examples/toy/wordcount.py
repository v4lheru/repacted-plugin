#!/usr/bin/env python3
"""wordcount: count the lines, words and characters of a text, and list its most common words.

Location: pact-plugin/examples/toy/wordcount.py. The example project /repacted:demo copies
into a scratch directory. One test in test_wordcount.py fails on purpose: the demo asks one
specialist to find the bug and fix it.

Usage: python3 wordcount.py [--top N] [FILE]    (reads standard input when FILE is omitted)
"""
import argparse
import re
import sys
from collections import Counter

WORD = re.compile(r"[a-z0-9']+")


def words(text):
    """The words of text, lowercased, with punctuation dropped."""
    return WORD.findall(text.lower())


def count(text):
    """(lines, words, characters) of text, counted the way `wc` counts them."""
    return text.count("\n"), len(text.split(" ")), len(text)


def top(text, n):
    """The n most common words as (word, count) pairs; a tie is broken alphabetically."""
    counts = Counter(words(text))
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:n]


def main(argv=None):
    """Print the counts, then the top words. Returns the exit code: 0 done, 1 unreadable file."""
    parser = argparse.ArgumentParser(prog="wordcount", description=__doc__.split("\n")[0])
    parser.add_argument("file", nargs="?", help="the file to read (default: standard input)")
    parser.add_argument("--top", type=int, default=3, help="how many common words to list")
    args = parser.parse_args(argv)
    if args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as error:
            print(f"wordcount: cannot read {args.file}: {error.strerror}", file=sys.stderr)
            return 1
    else:
        text = sys.stdin.read()
    lines, word_count, chars = count(text)
    print(f"{lines} lines, {word_count} words, {chars} characters")
    for word, times in top(text, args.top):
        print(f"{times:>4}  {word}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
