# Implementation verification — 2026-09-14

## Local model setup follow-up

Ollama 0.34.0 with qwen3.5:4b passed a live synthetic benchmark in 32.27 seconds on
the user's computer, with valid SuiteQL generated in one attempt. Fixed the sampler
grammar rejection by removing large decoding-grammar length constraints while retaining
Pydantic response limits. Query plans now require nonempty SQL, and the benchmark
validates queries with bounded repair before reporting success. 49 Python tests pass.
The remaining live NetSuite checks below are still outstanding.

## Completed locally

- 46 Python tests passed, including SQL safety/field validation, document updates,
  schema refresh changes, OAuth assertion verification, completion replay, cancellation,
  management authorization, and the complete adapter/worker protocol using synthetic services.
- 9 SuiteScript tests passed, executing the actual shared library, RESTlet, Suitelet, and
  cleanup script in an in-memory NetSuite harness.
- SDF XML parsed; script IDs, file references, and all JavaScript syntax passed local checks.
- Real FastAPI HTTP startup passed; liveness returned success, unauthorized management
  access returned 401, and authorized status correctly reported missing business configuration.
- Browser verification of the actual Suitelet HTML with synthetic data passed: question
  submission, completion display, a source link, SQL/provenance disclosure, and HTML-like
  database text rendered as text instead of executing.

The fixture server is separate from the application and never uses real business data.
Its UI is visibly labelled as a local test fixture. Fixture tests do not validate NetSuite
platform behavior or model correctness.

## Not yet verified / external setup required

- NetSuite account login, SuiteCloud server validation, deployment, role audience, and OAuth M2M mapping.
- Real account schema inventory and zero-row/null-projection metadata discovery, including empty custom records.
- Real SuiteQL answers and record links against the business owner's expected results.
- Ollama installation, model download, actual structured-output behavior, speed, and memory use.
- Business definitions: the template intentionally remains unconfigured.
- Docker build/run and server hardware: configuration is supplied but no Docker deployment was performed.
- Live retention schedule, backup/restore, and sandbox restart/cancellation behavior.

Use SETUP.md followed by ACCEPTANCE.md. Account-specific failures require resolution before
calling the deployment production-ready.

## Approved creation update — 2026-09-22

See [CREATION_VERIFICATION.md](CREATION_VERIFICATION.md) for current creation tests,
Oracle server validation and local-model evidence. The historical outstanding checks above
describe the original implementation, not the current creation verification status.
