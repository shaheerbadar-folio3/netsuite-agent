# Live sandbox acceptance

Status: **not executed against a live sandbox**. This checklist is deliberately separate
from automated fixture tests. Record evidence and dates here after deployment.

## Connection and permissions

- [ ] SuiteCloud validates and deploys the package to the intended sandbox.
- [ ] OAuth M2M ping succeeds using the dedicated integration role.
- [ ] Administrator can open the Suitelet; non-admin cannot GET or POST to it.
- [ ] Integration role has only View on business records and Edit on agent jobs.
- [ ] Wrong integration user/role, bad CSRF, and another user's job IDs are rejected.
- [ ] Cleanup is scheduled, and only agent jobs are eligible for deletion.

## Business correctness

Prepare at least 20 questions with expected results from independent saved searches or
verified SuiteQL. Include these categories:

- [ ] Customer signup dates including exact start/end boundaries and imported customers.
- [ ] Active/inactive customers under the actual business definition.
- [ ] Orders by date/status and customers with no orders.
- [ ] Return authorizations versus receipts versus credits, including partial returns.
- [ ] Header/line joins without duplicate order counts or amounts.
- [ ] Aggregates with null values, multiple subsidiaries, and different currencies.
- [ ] Empty results are distinct from failed queries.
- [ ] A follow-up such as “only last month” correctly applies to the prior question.
- [ ] Ambiguous terms cause a clarification rather than an invented definition.
- [ ] Answers show source SQL, applied definitions, document/schema versions, and actual rows.

For every question record the expected interpretation, expected row identities/aggregates,
observed result, pass/fail, document version, model tag/digest, and query latency. Do not score
a syntactically valid query as correct unless its result and meaning are correct.

## Freshness and customization

- [ ] A new customer appears in a subsequent live query without retraining or reindexing rows.
- [ ] A newly created empty custom record type is discovered after schema refresh.
- [ ] A new custom field appears in the schema and can be selected after document updates.
- [ ] Changed/removed fields do not remain silently usable after refresh.
- [ ] Editing the document changes the next question's interpretation and version hash.
- [ ] Failed table probes are visible and the required business tables have usable coverage.
- [ ] Unavailable joins/fields cause clarification or an explicit failure.

## Reliability and usability

- [ ] Previous/Next pages work, with deterministic ordering including tie-breakers.
- [ ] Counts describe the full query scope, not just the displayed page.
- [ ] Record links open the intended view record for supported directly selected IDs.
- [ ] A browser reload reconnects to the active question.
- [ ] Cancellation prevents publication even if inference/query work was already underway.
- [ ] Worker restart after claiming a job recovers after lease expiry.
- [ ] A completed answer whose publication fails is replayed without re-inference.
- [ ] An offline worker leaves a clear queued/offline state.
- [ ] Oversized results and platform page limits are explicit.
- [ ] The scheduled cleanup and local retention remove only expired application data.

## Local model and privacy

- [ ] Ollama confirms cloud features are disabled and uses a locally installed model.
- [ ] Model requests succeed with structured output using the pinned Ollama version.
- [ ] Peak memory and response times are measured on the intended computer.
- [ ] Accuracy on the real question suite meets the business owner's agreed threshold.
- [ ] No external AI service receives prompts, schema, documents, or results.
- [ ] Credentials and database files have restricted access and are absent from source control.
- [ ] Backup and restore are tested on a separate data directory.

Production release requires the necessary checks above to pass; unresolved schema or model
failures are implementation follow-ups, not instructions to ignore validation.
