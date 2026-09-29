# Creation verification — 2026-09-22

## Passed

- Python: 74 tests, including the existing read pipeline and new creation planning,
  approval execution, immutable SDF artifacts, destination verification, deployment
  uncertainty, and draft-versus-execution completion caching.
- SuiteScript: 25 tests (14 existing, 11 new). The new tests cover previews without saves,
  existing custom entries, sales order reference resolution, missing fields, ownership,
  expired/forged approvals, draft replacement/rejection, revalidation, nonnumeric select
  option IDs, duplicate execution and ambiguous save/deployment outcomes.
- Local package validation: XML parsing, script IDs/file references, SuiteScript JSDoc
  annotations, JavaScript syntax and inline UI JavaScript syntax.
- Oracle server validation of the updated application package: **SUCCESS**, 23/23 steps,
  zero errors and zero warnings, account TSTDRV2044449.
- Oracle server validation of an isolated generated custom record type: **SUCCESS**, 17/17
  steps, zero errors and zero warnings. The fixture included TEXT, TEXTAREA, CLOBTEXT,
  INTEGER, FLOAT, CURRENCY, CHECKBOX and DATE fields. It was validated, not deployed.
- Browser verification against the in-memory fixture: draft display, readable labels and
  reference IDs, explicit approval followed by a creation receipt/link, and rejection
  disabling approval. This uses the actual Suitelet/RESTlet code with simulated NetSuite APIs.
- Live local Ollama planning against a strictly simulated NetSuite adapter: capabilities
  -> metadata inspection -> customer draft, correct companyname and subsidiary ID, passed
  in 119.13 seconds. The benchmark cannot call NetSuite or save records.

Initial model checks exposed unnecessary clarification before metadata inspection and
asking the user to repeat a supplied company name. The backend now forces metadata
inspection for a recognized type before accepting premature clarification/preparation;
the prompt explicitly distinguishes preparing an unsaved review draft from asking for
approval, and includes a concrete field-mapping example. The final full benchmark passed.
This single synthetic pass is not a guarantee of model accuracy for every record type.

Reproduce local tests with `bash scripts/check.sh`. Optionally run
`.venv/bin/python scripts/creation_benchmark.py` for the local model check; it may take
several minutes on CPU and never writes to NetSuite.

## Still required in the sandbox

- Deploy the updated application scripts.
- Grant the integration role creation and reference-read permissions, and enable creation.
- Configure dedicated same-account SDF CI authentication for new custom record types.
- Run real record saves and a real custom type deployment through the chat approval UI.
- Verify account-specific fields, tax, subrecords, workflows, user events, source defaults,
  required permissions and final saved values for each record type before operational use.

No business records or custom record types were created in the real account during this
implementation. Validation is not deployment, and the execution tests used simulated APIs.
Supported scope and unsupported subrecords/operations are listed in CREATION_SETUP.md.
