# Contributing

Thank you for helping. Bug reports, ideas and pull requests are all welcome.

## Before you start

- For a bug, open an issue with the bug template. Paste the refusal line or the `repact-doctor` output if you have one, with any secret removed.
- For a larger change, open a feature issue first, so we can agree on the shape before you write code.
- Report a security problem privately. See [SECURITY.md](SECURITY.md).

## Sign off every commit (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org/) (DCO). By signing off, you state that you wrote the change, or have the right to submit it under the project's MIT License.

Add the sign-off with `-s`:

```
git commit -s -m "Fix the doctor's mode line on Linux."
```

This adds a line like `Signed-off-by: Your Name <you@example.com>` to the commit. Every commit in a pull request needs it. To sign off commits you already made, run `git rebase --signoff main` and push again.

## Two kinds of paths

This repository is built from two sources, and a pull request takes a different route for each.

**Public-owned paths** are written here, in this repository:

- `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`
- `docs/`
- `.github/workflows/`, `.github/ISSUE_TEMPLATE/`, `.github/PULL_REQUEST_TEMPLATE.md`

A pull request on these paths is reviewed and merged here, as usual.

**Export-owned paths** are everything else: the plugin in `pact-plugin/`, `.claude-plugin/`, `LICENSE`, `NOTICE.md`, `dev/`, `scripts/`, `testing/` and the other root files. A script exports them from the maintainer's source repository on each release. A commit made here on one of these paths would be overwritten by the next export, so it is not merged here.

Instead, the maintainer imports your pull request into the source repository, and your change ships in the next release. The pull request is then closed, not merged. The imported commit carries a `Co-authored-by:` line with your name, so you keep the credit.

To make the import work:

- Send one plain-text change. Renames, copies, file mode changes and binary files cannot be imported; the maintainer makes those by hand.
- Keep the change small and on the lines you need. The export rewrites a few lines in some files. A change on a rewritten line cannot be imported, and the maintainer makes it by hand.
- Do not mix public-owned and export-owned paths in one pull request.

## Test your change

From the repository root, with Python 3.9 or newer and pytest:

```
python3 -m pytest -q pact-plugin/tests
```

For a change in one area, run only the test files that cover it. To check the plugin manifest, run:

```
claude plugin validate pact-plugin
```

Add one regression test for each bug you fix or behaviour you change.

## Style

- Write plain English: short sentences, no marketing words.
- A commit message is one sentence, with no body. The plugin's own commit check refuses other shapes.

## Code of conduct

Everyone who takes part follows the [Code of Conduct](CODE_OF_CONDUCT.md).
