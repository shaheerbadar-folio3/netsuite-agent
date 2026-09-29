# NetSuite Local Agent

Administrator-only analytics inside NetSuite, using a locally hosted model and live
read-only SuiteQL, with optional approval-based record creation and SDF custom-type deployment. The application is implemented; connecting and accepting it against
your sandbox requires the account-specific setup below.

**Start here: [Setup and deployment](docs/SETUP.md).**

**Creation setup: [Approved record and custom-type creation](docs/CREATION_SETUP.md).**

## What is included

- NetSuite Suitelet chat with follow-ups, job progress, cancellation, result pagination,
  SQL/definition provenance, and links for directly selected IDs of supported record types.
- Creation drafts, live reference resolution, explicit administrator approval, and durable execution receipts.
- Isolated SDF projects for new custom record types, with validation and account verification before deployment.
- Outbound local worker: your laptop does not need a public URL or an inbound tunnel.
- OAuth 2.0 certificate authentication, automatic token renewal, and restricted integration identity.
- Local Ollama planning with JSON schema output, bounded query repair, and no external AI fallback.
- Business-document loading on every question, deterministic local retrieval, and content version hashes.
- Automatic schema inventory/probes, incremental refresh with a core-table allowlist, periodic full rescan, background refresh while serving the last good snapshot, partial-coverage diagnostics, and stale-schema rejection.
- Oracle SQL AST validation, real NetSuite query execution, and explicit partial/error states.
- SQLite completion journal, replay recovery, leases, cancellation, audit metadata, retention, and backup command.
- SuiteCloud source package, Docker configuration, runbooks, and automated Python/SuiteScript tests.

## Current verification boundary

The code has local automated tests using simulated NetSuite and model responses. Existing
read-query flows have been exercised in the user's sandbox. The new creation flows require
separate sandbox acceptance and write/SDF permissions; local tests do not establish that
every account's record configuration, scripts or workflows are compatible. See
[creation acceptance](docs/CREATION_SETUP.md) and [query acceptance](docs/ACCEPTANCE.md).

Schema probing does not guarantee every NetSuite UI field is exposed to SuiteQL. It does
not infer business joins or recover history that NetSuite does not expose. Those definitions
belong in your business document. Failed probes are visible in schema diagnostics.

Only one worker per integration deployment is supported. Read queries can be retried after
a crash, but stored completed answers are replayed without rerunning the model. There is
no exactly-once guarantee for a read interrupted before durable completion.

## Run locally

The implementation workspace already contains installed Python dependencies in `.venv`.
For a fresh checkout, run `bash scripts/setup.sh` first. Complete `.env`, NetSuite deployment,
the business document, and Ollama installation before starting the worker.

```bash
.venv/bin/netsuite-agent doctor
.venv/bin/netsuite-agent check-netsuite
.venv/bin/netsuite-agent serve
```

The health endpoint is `http://127.0.0.1:8765/health`. Detailed status and schema refresh
require the local bearer token. `scripts/status.py` reads it from `.env` without printing it.

```bash
bash scripts/check.sh
```

## Repository layout

- `agent/`: Python worker, model adapter, authentication, query validation, schema and document processing.
- `netsuite/`: deployable SuiteCloud objects and SuiteScript/UI files.
- `knowledge/business.md`: business-owned definitions; the unconfigured template fails closed.
- `tests/`: backend tests and a simulated NetSuite runtime executing the actual scripts.
- `scripts/`: installation, diagnostics, package validation, and browser fixture server.
- `docs/SETUP.md`: exact installation and account configuration steps.
- `docs/OPERATIONS.md`: lifecycle, troubleshooting, privacy, backups, and migration.
- `docs/ACCEPTANCE.md`: live acceptance checks that remain to be run.

## Data handling

Questions and result pages are stored temporarily in dedicated NetSuite job records and
the local completion journal. Business records are never modified by query execution.
Optional creation uses separate record APIs or SDF only after review and explicit approval.
Creating/updating/deleting the agent's own queue records is a separate application function. SQLite and documents are not encrypted by this application: use
filesystem permissions and full-disk encryption, and protect backups as business data.

Models receive questions, selected schema, document excerpts, and query error messages.
They do not receive the returned rows for narrative summarization in this version. Results
are rendered directly, so counts and figures come from NetSuite rather than model prose.

## Primary references

- [Oracle N/query](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1510275060.html)
- [Oracle query type enumeration](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1510878994.html)
- [Oracle paged SuiteQL limitations](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_157960586441.html)
- [Oracle OAuth client credentials](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_162730264820.html)
- [Oracle OAuth assertion structure](https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_162790605110.html)
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [Ollama local-only settings](https://docs.ollama.com/faq)

The null-extended metadata probe is an implementation approach built from SuiteQL query
capabilities, not an Oracle promise of a complete schema-discovery API.
