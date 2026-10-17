# wordcount, the demo project

A small command-line tool that counts the lines, words and characters of a text and lists its most common words. `/repacted:demo` copies this directory into a scratch directory and asks one specialist to fix the one failing test.

```bash
python3 wordcount.py --top 3 README.md
python3 -m pytest -q          # or: python3 -m unittest -v
```

`fixtures/fake-credentials.conf` holds a made-up key. It exists so the release checks have a known secret-shaped line to find; it is not a key for any service, and the demo does not copy it.
