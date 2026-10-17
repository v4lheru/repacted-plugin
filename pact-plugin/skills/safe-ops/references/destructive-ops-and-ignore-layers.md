# Destructive operations and ignore layers

Context: how to delete only what you meant to delete, and how to keep files out of a build or
upload when git's ignore file does not govern it. Read this before any delete (files, rows, Drive
items, branches), before a Docker build or `railway up`, before writing an ignore file, and before
cleaning a scratchpad.

## Canonical rules
1. Delete by the exact ID you recorded when you created or found the object (file ID, row PK, branch SHA). Never delete by a name, pattern or search query.
2. If a query-driven delete is truly unavoidable, print the matched set and stop; a human reads the list. A match never feeds an irreversible verb in the same script.
3. Treat `contains`, `LIKE`, globs and fuzzy search as over-matching in any API you have not probed. A missed match leaves litter; an extra match destroys data.
4. Google Drive `name contains` tokenises and ignores trailing spaces: `'ZZ '` matched `zz` inside base64 filenames. It is not a substring match.
5. Drive `files.delete` is permanent and skips the trash; recovery needs a Workspace admin restore within about 25 days. Prefer `PATCH {trashed: true}`.
6. Put test artifacts in one disposable container (a dedicated parent folder, schema or prefix) so cleanup is "delete that container by ID".
7. Review teardown code as hard as the code under test: cleanup has the larger blast radius.
8. `.gitignore` governs git only. A Docker build context, `railway up`, `npm pack`, `scp -r` and CI artifacts each need their own exclusions and include everything by default.
9. There are three layers on a Railway Docker deploy: git, then the upload (`.gitignore` plus `.railwayignore`), then the build (`.dockerignore`). The earliest layer that drops a file wins.
10. `railway up` (like `fly deploy`, `gcloud app deploy`) uploads the WORKING TREE, not HEAD: uncommitted files, peers' in-flight edits and untracked secrets ship.
11. Deploy from a clean detached worktree at your own HEAD when others share the tree; a file that does not exist cannot ship.
12. Write `.dockerignore` before the first container build, with `.env*`, `*.pem`, `*.key`, `credentials.json`, `token.json`, `service-account*.json`.
13. An image layer is append-only: `RUN rm .env` in a later layer does not remove it from the image. Keep it out of the context instead.
14. Docker ignore patterns: leading and trailing slashes are ignored, and a pattern without `**` matches only at the context root. Never use `**` in a build-context ignore file.
15. Check every ignore pattern against the SOURCE TREE: a PII rule naming a common word (`candidates`, `data`, `reports`) can remove a source directory. Add `!src/...` tripwires.
16. Verify exclusion on the artifact: `docker run --rm <img> sh -c 'ls -a /app'`, list image layers, or `curl` the route. Reading the ignore file is not evidence.
17. `git checkout -- <path>` and `git checkout <ref> -- <path>` overwrite uncommitted work and also update the index; restore by writing `git show HEAD:<path>` to disk instead.
18. Before ending a session that wrote scratch data, sweep the scratchpad for PII and raw payloads you copied there, and delete them by path.

## Examples
- 1: cleanup by `name contains 'ZZ '` permanently deleted three of the user's Drive files along with the five probes.
- 8: a gitignored `.env` with live keys was one `COPY . .` from a published image after a switch to a Dockerfile.
- 9: a route fixed in `.gitignore` and then in `.dockerignore` still 404'd in production; `.railwayignore` dropped it at upload.
- 14: `/candidates/` in `.dockerignore` was believed to be anchored; Docker ignores the slash.
