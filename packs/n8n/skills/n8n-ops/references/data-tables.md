# Data tables

n8n Data Tables through the REST API and the `dataTable` node: what each accepts, how each pages, and the filter trap that writes rows you did not target. Every endpoint below is labelled with its pagination style, because the convention differs per endpoint. Read before any data-table write or any count taken from one.

## Canonical rules
1. The `dataTable` node combines multiple filter conditions with OR, never AND, and `matchType` does not change it (n8n 2.37.10, measured 2026-09-10).
2. Filter write nodes (`update`, `upsert`, `deleteRows`) on ONE pre-joined key column; assert `filters.conditions.length === 1` in a build check.
3. The node's output shows only the intended row even when it over-matched: audit by diffing the WHOLE table around the write.
4. The REST API honours AND and OR correctly, so a REST test proves nothing about the node path.
5. `/rows` pages with `limit` (max 250) plus the opaque `nextCursor`; `skip`, `offset`, `page` and `take` return 400.
6. A bare `/rows` read returns only the first 100 rows with no truncation signal: always pass `limit` and consume the cursor.
7. Check the HTTP status before parsing a body: a 400 parsed as rows reads as an empty table right after a successful write.
8. Update rows with `PATCH /data-tables/{id}/rows/update`; `PATCH /rows` is 405.
9. Tables are listed instance-wide but a workflow reads only tables in ITS project: pass `projectId` when creating tables and workflows.
10. Changing an upsert key re-inserts every row whose new key is null: back-fill the key column through the API before switching.
11. Never point an unverified data-table write at a real table: use a scratch table seeded with a deliberate near-miss.
12. Design discriminating tests: include a case where the benign and the alarming hypothesis predict different results (a condition matching nothing, disjoint singles).

## Endpoints (base `/api/v1`)

| Call | Pagination | Notes |
|---|---|---|
| `GET /data-tables` | `limit` + `nextCursor`; `take` → 400 "Unknown query parameter 'take'" (verified 2026-09-26) | lists tables instance-wide, other teams' included |
| `POST /data-tables` | — | `{name, columns:[{name,type}], projectId}` |
| `GET /data-tables/{id}/rows` | `limit` ≤ 250 + `cursor`; bare read = first 100 | the cursor base64-decodes to `{"limit":250,"offset":250}`, but `offset` is not accepted |
| `POST /data-tables/{id}/rows` | — | insert |
| `POST /data-tables/{id}/rows/upsert` · `PATCH …/rows/update` · `…/rows/delete` · `…/rows/clear` | — | filter `{"type":"and"\|"or","filters":[{columnName,condition,value}]}` |
| `POST /data-tables/{id}/columns` | — | adds a column (201) |
| folders, for contrast: `GET /projects/{id}/folders` | `take`/`skip`; `limit` → 400 | response `{data, count}` |

Read a whole table with `limit=250` and follow the cursor until it is absent.

## The node

- Type `n8n-nodes-base.dataTable`, typeVersion 1.1 (observed 2026-09). Ops: `insert`, `get`, `update`, `upsert`, `rowExists`, `rowNotExists`, `deleteRows`, `clear`.
- `dataTableId` is a resourceLocator `{"__rl":true,"value":"<id>","mode":"id"}`.
- `rowNotExists` is a pass-through FILTER: it emits the input items with no matching row and keeps `$json`, so source → `rowNotExists` → process is a clean dedup gate. Filters shape `{"filters":{"conditions":[{"keyName":"<col>","keyValue":"={{ $json.key }}"}]}}`.
- `insert` and `update` use `columns.mappingMode:"defineBelow"`, `columns.value:{…}` and a `columns.schema` array (types string, number, boolean, date, json); include the schema on API-built nodes.
- `insert` clobbers `$json` with `{id, createdAt, updatedAt}`: run it as a parallel sibling of the branch that needs the item.
- `parameters.matchType` of any value throws "unexpected match type"; `filters.matchType` is ignored.

## The discriminating OR test (copy this shape)

| conditions | rows updated | AND predicts | OR predicts |
|---|---|---|---|
| single condition (control) | [1,2] | — | — |
| two conditions matching nothing | [] | [] | [] (and "filter ignored" would update all) |
| disjoint singles | [2,3] | [] | [2,3] |

Measured on one instance: 56 dataTable nodes carried conditions and none set `matchType` (2026-09-10). A live workflow filtering on two columns meaning AND was exposed; flag such nodes, do not fix them unasked.

## Examples
- A first test used conditions whose union covered the table, so "filter ignored" and "OR" gave identical output and the wrong alarm was raised.
- A read-back parsed a 400 body as rows and reported "0 rows" right after a 99-row write.

