# Operations

## Normal lifecycle

Start Ollama, then `netsuite-agent serve`. Initial schema discovery runs before job claiming;
regular refresh occurs hourly by default. New business records require no indexing because
queries run live. New fields and custom records need a successful schema refresh and, where
necessary, updated business definitions.

The Suitelet polls for completion every three seconds; the worker polls every five seconds.
The worker lease lasts two minutes and renews every 25 seconds during processing. A dead
worker's job can be reclaimed after expiry. A local OS lock prevents duplicate processes
using the same data directory. Run **one worker deployment**, not several machines sharing
the same worker identity. NetSuite optimistic locking must remain enabled on the job record.

Cancelling a job stops local inference after the next heartbeat. A NetSuite query already
executing may finish, but its answer is not published to the cancelled job. Read execution
is not guaranteed to be cancelled on the NetSuite server.

Retries are limited to three query-generation attempts. Authentication/transport problems
surface through worker state; there is no infinite model loop. A stored completion is reused
if publishing failed, but a process crash before storage can cause a read to execute again.

## Health and freshness

- `/health`: unauthenticated process liveness only. It is not an assertion of readiness.
- `/status`: bearer-protected model availability, worker state, document version, and schema refresh status.
- `POST /schema/refresh`: bearer-protected request to refresh at the next safe opportunity;
  requests are debounced to at most one attempt per minute.
- `scripts/schema_report.py`: lists discovered tables, counts, and failed probes locally.

A snapshot older than twice the configured refresh interval is rejected for new queries.
Page requests preserve the original business interpretation and document version. They use
a new live query and are rejected when the schema version has changed. There is no cross-page
database snapshot; concurrent NetSuite changes may alter rows and counts.

Schema column names are discovered automatically where probes succeed. The schema is not a
complete permission inventory or a business ontology. Wide tables are reduced to relevant
fields in model context. Missing context can produce a clarification rather than a guessed query.
If a needed table repeatedly fails discovery, inspect its channel availability and permissions
in Records Catalog; do not disable validation to force the query through.

## Common failures

- **Worker offline or initializing:** check the local process, computer sleep, network, and
  initial schema scan. NetSuite health is based on the last claim/heartbeat, not a persistent connection.
- **OAuth HTTP error:** check the exact account, certificate ID, certificate expiry, integration
  mapping, system clock, and role/user deployment parameters. A failed authorization invalidates
  the cached token for the next request. No credentials appear in application logs.
- **No usable metadata:** inspect schema diagnostics and the integration role. Verify the null
  projection query on a safe table in your sandbox. If it is incompatible, this discovery
  adapter needs account-specific work; the application does not pretend discovery succeeded.
- **Ollama unreachable / missing model:** start the local service and pull the configured tag.
- **Insufficient memory / very slow inference:** free memory; reduce context only after checking
  document budget; compare model candidates with real test questions. A 4B model is a candidate,
  not a universal accuracy guarantee.
- **Oversized business rules:** keep mandatory sections concise and split other concepts into
  meaningful headings. Avoid storing customer lists or transaction exports in the document.
- **Query failed after retries:** inspect generated SQL and definitions. Verify field IDs,
  joins, status/date semantics, and unsupported functions. Fix business rules or the adapter,
  then ask again. An error is never represented as zero matching records.
- **Page too large:** select fewer fields, exclude long text, or narrow the question. Stored
  answer size is capped below the conservative SuiteScript long-text limit.

## Security boundaries

Administrator visibility is enforced server-side in the Suitelet. CSRF tokens bind POST
requests to the current NetSuite session; per-job ownership prevents another session/user
from substituting job IDs. The RESTlet accepts only the configured integration user/role
and worker identity. The local service has no public query-submission endpoint.

The model has no shell, browser, arbitrary HTTP, credentials, record editing, or deletion
tools. SQL passes structural validation, schema validation, and a second RESTlet check.
NetSuite N/query executes reads. Read-only queries still consume resources and can be
semantically wrong; tests and verified business definitions remain necessary.

The Ollama process must have cloud features disabled as documented in SETUP.md. Inference
endpoints are restricted to local/private service addresses. NetSuite HTTPS destinations
are derived from the configured account, redirects are not followed, and environment HTTP
proxies are ignored. There is no remote analytics/telemetry integration in the application.

## Retention and backups

The local worker deletes expired completion/audit rows during polling, and the scheduled
NetSuite cleanup script removes old application jobs. Neither deletes business records.
NetSuite cleanup must actually be scheduled; merely deploying its source is insufficient.
SQLite row deletion does not securely erase old disk blocks or backups. Configure disk
encryption and backup retention according to your business's policy.

Create a consistent SQLite backup:

```bash
.venv/bin/netsuite-agent backup --output /secure/path/agent-backup.sqlite3
```

The destination must not already exist. Also back up the business document and configuration
through your approved secure process. Keep the private OAuth key separate from ordinary
source backups. Do not commit `.env`, `data/`, or `secrets/`.

Restore with the worker stopped: move the current data directory aside, create a fresh
restricted directory, copy the backup to `data/agent.sqlite3`, restore ownership and mode
0600, then restart. Do not copy an old WAL or SHM file alongside a restored SQLite backup.

## Server migration and rollback

Stop the laptop worker, take a backup, move configuration/document/state securely to the
server, and verify model availability. Start one server worker using the same deployment.
Check health, run a known query, and confirm pending jobs recover. The NetSuite UI does not
need a new inbound address.

For rollback, stop the server worker before starting the laptop copy. Revoke or rotate any
credentials that moved outside the intended host. Retain the prior application release,
dependency lock, model tag/digest, schema version, and business-document version with your
release records. Pin the model digest in your operational records because registry tags can change.


## Inference performance and plan reuse

Answers include `timings` (model and NetSuite seconds), `ollama` (per-model-call load,
prompt processing, generation durations and token counts when available), and
`context_chars`. Missing Ollama metrics mean the server did not supply them.
The planner includes core business rules and relevant sections, and selects a bounded
schema subset. If definitions or fields are absent, it must clarify rather than invent them.

The worker holds up to 128 successful plans in memory for five minutes. A fresh standalone
question can reuse a plan only with matching question text, full document hash, schema
version and refresh time, account/endpoint/client/worker, model, and timezone. Follow-ups
with history and questions detected as time-dependent bypass reuse. SQL containing date/time
constructs or string literals is conservatively excluded. Relative-date requests are
replanned rather than reusing dates calculated earlier. Restarting clears the cache.
Every reused plan is validated again and executed against NetSuite with current permissions;
result rows are never reused for a new question. A failed cached execution evicts the plan
and returns to bounded model repair. Existing completion replay remains specific to the
same job ID and is separate from plan reuse.

To compare performance, start a fresh chat for each standalone repetition (history-bearing
follow-ups intentionally bypass the cache). Inspect `plan_cache_hit`, model/NetSuite timings,
and Ollama prompt/generation timings. Do not infer an end-to-end speedup from token reduction
alone. No model or hardware configuration change is required for these optimizations.
