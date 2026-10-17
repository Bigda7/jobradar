# Paginated API query performance

Reviewed locally on 2026-10-17. No production, publication, dependency, schema,
source schedule, matching rule, notification, or proxy deadline changes.

## Cause and change

The old `/jobs` and `/matches` page query selected a canonical source URL, source
name, and source display name through three independent correlated subqueries.
For 200 results this repeated the same link choice up to 600 times. On a freshly
restored synthetic database without fresh planner statistics, PostgreSQL chose
repeated bitmap index intersections. Running `ANALYZE` improved those plans, but
the application should not rely on that to avoid redundant work.

The updated query first selects only the requested page's opportunity/evaluation
IDs using the original filters, ordering, limit and offset. A page-scoped window
query ranks eligible links with `row_number()` by the original quality-descending,
ID-ascending order. It returns all three source fields from the same winning row.
Only then are full opportunity/evaluation objects joined for the response.

The window input is joined to the selected page, not the entire opportunity
catalog. Hidden records, inactive listings, disabled sources and untrusted stored
links are excluded under the existing policy. An explicit source filter applies
both to eligibility and the selected link. Counts remain separately filtered;
requests still use two SELECTs, not an N+1 query per response item.

The intermediate single-correlated-ID prototype improved latency but had limited
headroom under combined load. The final implementation is the page-scoped batch
window query, not that prototype. No new indexes, materialized views, cache TTL,
omitted descriptions, reduced page sizes, or relaxed deadlines were introduced.

## Verification

The final unchanged-policy run completed without interruption: initial 50-item
batch max6.108s, initial200-item max5.051s versus the prior19.434s; later200-item
batches2.478/2.574s. Parent peak858,406,912 bytes (~819 MiB), no parent OOM/swap.
All10k records/evaluations were unchanged/reused, all14k descriptions/identities
survived failure groups, and the real worker OOM caused exactly one automatic
restart. Owned containers/networks/volumes/cgroups were verified and cleaned.
1152 ordinary +115 separate PostgreSQL tests passed; Linux1149 passed with3
host-covered tool skips and115 PG deselected. Static checks passed. These are
local AMD64 results, not native ARM64 or production deployment acceptance.

Regression tests cover query shape/count, full response fields, pagination offsets,
score/newest/company ordering, source filtering, equal-quality link ties, hidden
opportunities, inactive/disabled/unsafe links, and an opportunity without listings.
The same cases run on SQLite and real isolated PostgreSQL.

`deploy/resource-prototype/profile_api.py` restores only the task's hash-checked
synthetic 14k-record snapshot into an owned fixture. It can extract the previous
API module from an existing local image without starting that container, compare
14 complete JSON responses, and collect SQL timings plus EXPLAIN summaries before
and after fixture-only ANALYZE. No credentials or production rows are used/output.
API Python work runs on the host in this diagnostic; it is not an aggregate
resource or native ARM64 benchmark. Every captured query/data value is synthetic.

`check_2gb.py --image jobradar-local:api-performance-v2-20261017 --restore-fixture ...`
provides the separate actual aggregate-resource acceptance. It enforces the same
1408 MiB/no-swap/two-CPU budget around API, PostgreSQL, collector, bot and backup
verifier, with 14k records/10k active and persisted real scoring results. It runs
50/200-item pages at concurrency eight, ingestion/evaluation reuse, bounded backup
restore/corrupt rejection, worker OOM/restart/stub recovery, PID64 and DB/API crash
data checks. All API load batches must now complete below the unchanged ten-second
proxy deadline; a 200-item batch is no longer allowed to pass merely below 30s.

Use only existing local JobRadar image tags or immutable IDs; the runner never
implicitly pulls a candidate. Keep failed/intermediate evidence, and distinguish
warm evaluation reuse from a cold first full rescore. The final verified timings,
test counts, image identity and remaining boundaries are in workspace
`API_PERFORMANCE_PACKAGE_2026-10-17.md`.

Local acceptance does not prove native host throughput, whole-OS capacity, all
future concurrency, real Internet/Vercel latency, provider availability, or actual
Telegram delivery. No new production action or recurring monitor is authorized.
