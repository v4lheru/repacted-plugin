# Monday.com user lookups

Context: two Monday GraphQL traps in people-column lookups that send notifications to the wrong
person. The wider Monday notes (write traps, column value encodings, create-column defaults, form
label divergence, injection-safe variables) are in `work-management-and-chat.md`; read that too.

## Canonical rules
1. `users(ids: [null])` or `users(ids: [])` does not error and does not return nothing: it returns ALL users in the account. `data.users[0]` is then an arbitrary person.
2. Guard every people-column lookup for an empty person BEFORE the query (an IF node on the id), and route the empty case to its own branch. Empty people columns are normal (a non-Monday submitter, a manually created item).
3. Type the email-lookup variable as `[String!]`, not `[String]`: `query ($emails: [String!]) { users(emails: $emails) { id } }`. The looser type errors, returns no user, and leaves the people column blank.
4. Keep user input in GraphQL VARIABLES, never interpolated into the query text (quote-only escaping is injectable). The typed-variable fix above keeps that.

## Examples
- 1, 2: an item with an empty Requester made a reminder workflow look up `users(ids: [null])`, and the reminder DM went to an unrelated colleague (2026-07-14).
- 3: `[String]` failed with "used in position expecting type [String!]", and a re-import that swapped the query in silently stopped populating the requester (2026-07-16).
