# Unattended comPACT in CI

Run `/repacted:comPACT` on one issue in GitHub Actions with nobody at the terminal. The run ends at a **draft pull request**. It never merges, never force-pushes and never pushes the default branch. A person reviews the change and merges it.

The workflow is [`templates/ci/unattended-compact.yml`](../../templates/ci/unattended-compact.yml). This page covers what to set up before the first run, what holds the run back, and what it costs.

## How a run goes

1. A maintainer adds the label `repacted-run` to an issue, or starts the workflow by hand (**Actions > unattended-compact > Run workflow**) with an issue number.
2. **authorize** checks the trigger, the person and the pins. No model runs here and no secret is read.
3. **work** checks out the default branch and the plugin release you pinned. It runs `claude -p` with `REPACT_UNATTENDED=1` inside a container, exports the result as plain files from a second container with no key and no network, diffs them in a fresh checkout of the base and uploads the diff as a patch. This job holds the Claude key and a **read-only** token.
4. **publish** runs on a fresh runner with no model. It refuses a patch that touches, at any depth and in any case, `.github/`, `.git/`, `.claude/`, `.repacted/`, `.vscode/`, `.devcontainer/`, `.mcp.json`, `CLAUDE.md`, `CLAUDE.local.md`, `.gitattributes`, `.envrc`, `.gitmodules` or `CODEOWNERS`. It also refuses a binary change, a symlink, a submodule and a mode change. It applies the patch to the index only and runs nothing from it. Then it pushes one new branch, `repacted/issue-<n>-run-<id>`, without force, and runs `gh pr create --draft`. Any other path with a dot-named part is listed at the top of the PR body.

Each job runs on its own fresh runner, and the split is deliberate. GitHub-hosted runners give the job passwordless `sudo`, so a model with a shell on the runner could replace `git` or `gh` for any later step in its own job. A model and a write token must never share a job.

The model does not get a shell on the runner either. The work job starts it with `docker run` on an image pinned by digest (`MODEL_IMAGE`). It runs as the runner's own non-root uid, with every capability dropped and `no-new-privileges`, and without `sudo`, the docker socket or a host namespace. The container sees only the checkout (writable, `.git` included, so comPACT can commit and make a worktree), the plugin release (read-only) and the prompt (read-only). So the model cannot read the runner's processes or the runtime token a later step gets, and it cannot rewrite the action files. The runner's git never opens the model's `.git`, because git runs config, attributes and hooks from a repository. Instead a second container (same image, no key, `--network none`) runs `templates/ci/collect_change.py export`: it takes the one worktree under `.worktrees/` that holds a change, or else the main checkout, committed and uncommitted work alike, and writes its tree out as plain files. Two worktrees with a change are refused. The top-level `.claude/` is kept as it is at the base: the plugin's session hook writes `.claude/CLAUDE.md` there, and publish refuses `.claude/` anyway. The runner treats those files as hostile: it refuses a symlink, a FIFO or other special file, a `.git` at any depth and anything under `.worktrees/`, lays the rest over a fresh `actions/checkout` of the base sha and takes the diff there, with hooks, fsmonitor, transports, external diff and textconv off.

## Setup

### 1. Copy the workflow and pin the plugin

Copy the template to `.github/workflows/unattended-compact.yml`. Set `REPACTED_SHA` to the full commit sha of the rePACTed release you reviewed. The shipped placeholder is all zeros, and the authorize job refuses it with `set REPACTED_SHA to the full commit sha of the release you reviewed`. The plugin's hooks, including the merge guard, are code that runs in your CI, so pin them the way you pin an action. Take the sha from the canonical repository, not from a link or a fork: `git ls-remote https://github.com/v4lheru/repacted-plugin refs/tags/<tag>`. A checkout by repository and sha also resolves commits that exist only in a fork of that repository, so a sha you did not confirm there can be someone else's code.

### 2. Triggers

Only `issues: labeled` and `workflow_dispatch` start the workflow. Never add `pull_request_target`, `pull_request`, `issue_comment`, `workflow_run` or any other event that a stranger's fork, comment or review can fire. A fork cannot fire `issues` or `workflow_dispatch`.

A label is weaker proof than it looks, because anyone with the **triage** role can add one. So authorize also asks the API for the role of the person who added the label (or started the run). It goes on only for `write`, `maintain` or `admin`, and never for a bot. A manual start must come from the default branch.

No automation may apply `repacted-run`: no labeler action, bot or script. A script that runs on a person's token shows up as that person, with that person's role, so authorize lets it through. A keyword labeler would then let any stranger who writes the keyword start a run.

Adding the label means "I read this issue and want a model to act on it". On a label event the model gets the issue as the event carried it, so an edit after the label does not reach it. On a manual start the work job reads the issue live, perhaps an hour later behind the concurrency group. So authorize records when the issue was last edited, and the work job refuses an issue edited after that. The model reads the issue title and body. They reach it as data through `env:` and a file, never as `${{ }}` inside a `run:` block, so a title like `$(curl ...)` stays text in the shell. The model can still be talked into things by the text, though. That is why the work job holds nothing that can push.

### 3. Token permissions

The workflow sets `permissions: {}` and grants each job only what it needs:

| Job | Permissions | Why |
|---|---|---|
| authorize | `contents: read`, `issues: read` | read the role of the person who added the label; on a manual start, when the issue was last edited |
| work | `contents: read`, `issues: read` | check out the code; read the issue on a manual start |
| publish | `contents: write`, `pull-requests: write` | push the new branch; open the draft PR |

No job has `administration`, `actions`, `workflows` or any other write right. A token without `workflows` cannot push a change to `.github/workflows/`, and publish refuses such a patch before it gets that far.

**`contents: write` can merge a PR through the API when nothing else stops it.** Branch protection is what stops it (step 5). Without branch protection the template does not stop at a PR. The token could merge.

### 4. The Claude key

Create the environment `repacted-unattended` (**Settings > Environments**) and add the secret `ANTHROPIC_API_KEY` there, not as a repository secret. Only the work job names that environment, so no other job can read the key. Under **Deployment branches and tags**, allow the default branch only. Otherwise a workflow on any writer's branch can name `repacted-unattended` and read the key. Use a key made for this workflow, with a spend limit set in the Anthropic Console. The model's own job holds the key, so treat it as exposed to whatever the issue text persuades the model to do. You can also add required reviewers to the environment, and then every run waits for a person to approve it.

### 5. Branch protection (required)

Protect the default branch with a branch rule or a ruleset:

- **Require a pull request before merging** with **Require review from Code Owners**, or with at least **2 required approvals**. One approval is not enough once step 6 is on (see there).
- **Require approval of the most recent reviewable push**. The bot pushed the branch and opened the PR, so its own approval never counts.
- **Require status checks to pass**, and list your test jobs as required checks.
- **Block force pushes** and **restrict deletions**.
- Turn on **Do not allow bypassing the above settings**, so a protection that admins can skip still holds for them.

### 6. The repository setting

Turn **on** *Allow GitHub Actions to create and approve pull requests* (**Settings > Actions > General > Workflow permissions**). It is one checkbox that covers both acts, and with it off `gh pr create` fails.

**The setting is repo-wide.** With it on, any workflow in the repository that has `pull-requests: write` can approve a pull request as `github-actions[bot]`. That includes a PR that someone else opened, for example a writer's own PR, approved by a workflow on their branch. The bot did not push that PR, so with one required approval it would count. That is why step 5 asks for Code Owner review or 2 approvals.

### 7. The checks on the PR

**A pull request opened with `GITHUB_TOKEN` does not start the repository's own workflows.** GitHub does this to stop a workflow from triggering itself in a loop. So the draft PR arrives with no checks run. The required checks from step 5 block the merge until they pass.

Starting the checks runs the model's code in your CI, with the repository's secrets and the default token permission. So keep this order:

1. **Review every file of the PR first**, including tests, build files and package scripts (`conftest.py`, a `postinstall`, a `Makefile`). These run in the checks.
2. Then start the checks, by closing and reopening the PR or by pushing a commit to its branch.

Two settings limit what a check can reach. Set the default workflow permissions to **Read repository contents** (**Settings > Actions > General > Workflow permissions**). Keep CI secrets in environments with **required reviewers**, not as repository secrets, so a check cannot read them until a person approves.

### 8. Actions caches

The work job runs on the default branch, and runs on the default branch can write to the Actions cache that every branch restores from. GitHub does not sign or check cache contents. The container keeps the model away from the runner's cache token, but if the model escaped the container it could write a cache entry, and a later workflow that restores it would run the model's files.

**No privileged workflow in this repository may restore an Actions cache**: nothing with secrets, a write token, a release or a deploy may use `actions/cache` or the `cache:` input of a `setup-*` action. If such a workflow must keep its cache, do not adopt this template in that repository.

### The App-token variant: CI starts before anyone reviews

To keep the repository setting in step 6 off and get CI to start on its own, open the PR with a GitHub App installation token instead of `GITHUB_TOKEN`. Give the App `contents: write` and `pull_requests: write` on this repository only. Store its private key as a secret in a second environment that only the publish job names. Mint the token in publish with a sha-pinned step, and pass it to `publish_change.py` as `GH_TOKEN`. Use it for the push too, by checking out with `token:`. The work job stays as it is. The App cannot approve its own PR under the rule in step 5.

This variant gives up the review-first order of step 7. A PR from a branch of the same repository starts the repository's `pull_request` workflows as soon as it opens, **with the repository's secrets**: the rule that withholds secrets from forks does not apply. So the model's code runs in your CI before any person has read it. Use this variant only when those workflows hold no secrets and run with a read-only token.

## What the merge guard refuses

In the work job the plugin's hooks run with `REPACT_UNATTENDED=1` (see [unattended runs](../../reference/unattended-runs.md)). They refuse each of these:

- `gh pr merge`, a PR close, a branch delete, a force push and a push to `main` or `master`. The guard approves these only after an answered `AskUserQuestion`.
- Every `AskUserQuestion`, while unattended mode is on. So no approval can ever be minted. A merge question gets the verdict `ALWAYS-ASK` and halts.

This is defence in depth. **The hard boundary is the token and branch protection.** The work job has no token that can push, and the default branch takes no merge without a human approval. A guard is code a model might find a way around. A token without the right does not have that weakness.

## Cost

- `--max-budget-usd` stops the `claude -p` run at `REPACTED_MAX_BUDGET_USD`. That is a repository variable (**Settings > Variables**), and the default is `5`.
- The same number goes to `PACT_SESSION_COST_CAP_USD`, so the plugin's session cost cap refuses new specialist spawns once the spend reaches it. See [Session cost cap](../../reference/config.md#session-cost-cap). A team policy `session.cost_cap_usd` in `.repacted/policy.json` can lower it further.
- `timeout-minutes` bounds each job (5, 60, 10), and the concurrency group lets one run per issue go at a time. A second label on the same issue waits rather than cancelling a paid run.

## Worked example: the toy repo

`examples/toy` is a word counter with one failing test: `count()` splits on a single space, so two spaces count as an extra word.

**Locally, with no model and no network.** The template's own tests run every job of the workflow on a scratch copy of the toy repo, with stub `gh`, `git push`, `docker run` and `claude` on `PATH`:

```bash
cd pact-plugin
python3 -m pytest -q tests/test_unattended_ci_template.py tests/test_unattended_ci_publish.py
```

In `test_a_run_ends_at_one_draft_pull_request_on_a_new_branch`, the stub model fixes the bug. It then tries `gh pr merge 1 --admin` and `git push --force origin HEAD:main`, and both fail because the work job has no token. Publish then makes exactly two calls that carry a token: `git push origin HEAD:refs/heads/repacted/issue-7-run-123` and `gh pr create --draft --base main`.

**On GitHub, by hand.**

1. Create a new private repository and push the contents of `examples/toy` to its `main` (leave out `fixtures/`).
2. Do setup steps 1 to 8 above.
3. Open an issue: *"count() miscounts words separated by two spaces"*, and add the label `repacted-run`.
4. Watch **Actions**. authorize names the person and their role. work prints which checkout it exported and the size of the patch. publish prints the URL of the draft PR from `repacted/issue-1-run-<id>`.
5. Review every file of the diff, then start the checks (step 7). Approve, then merge yourself.

## Known limits

- The template has not been run on a live GitHub runner. The tests run its shell steps on a local stand-in for the runner, with stubs; the container's flags, the export and the collect step were checked with a local Docker and the pinned image. The work step's `claude -p` call (Claude Code 2.1.302) was run once for real on a toy repository, on a workstation rather than in the container: comPACT fixed the issue, committed on a branch of its own in the checkout, and the export, collect and publish steps turned that into a two-file draft PR (with stub `git push` and `gh`), for $0.30.
- `--permission-mode bypassPermissions` lets the model run any tool inside the container. The plugin's hooks still refuse what they refuse. The sandbox is the container on a fresh VM, with a read-only token. A container escape would put the model on the runner, where it could write Actions caches (step 8).
- Only a worktree under the checkout's `.worktrees/` (where `/repacted:worktree-setup` puts one) is collected; a worktree made outside the checkout is gone when its container exits. Commits are flattened: the PR gets one commit with the result tree, not the model's history.
- The issue text can steer the model, and the model's container holds the Claude key. Use a dedicated, spend-limited key.
- The container has open network access, which the model needs for the API and the CLI install. On a private repository the model can send your source code out, as well as the key. Do not adopt the template in a repository whose code must not leave it.
