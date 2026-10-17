"""Keeps the plugin suite from collecting the example projects.

Location: pact-plugin/examples/conftest.py. The toy under examples/toy/ ships with a test that
fails on purpose (/repacted:demo fixes it in a scratch copy), so a pytest run rooted at
pact-plugin/ must skip it. The demo copies only examples/toy/, never this file, so the copy
collects its tests as usual. tests/test_demo_toy.py runs the toy's tests on their own.
"""

collect_ignore = ["toy"]
