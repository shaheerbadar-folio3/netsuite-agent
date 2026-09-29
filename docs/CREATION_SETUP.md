# Approved creation: setup and sandbox acceptance

The chat now supports preparing standard records, entries in existing custom record types,
and new custom record type definitions. Every operation requires an explicit **Approve**
button click by the requesting Administrator. Typing “yes”, model output, or a business
document cannot authorize a save.

## 1. Deploy the updated scripts

Stop the worker while updating the NetSuite scripts, then run:

```bash
cd /home/shaheerbadar/Projects/netsuite-agent/netsuite
suitecloud project:validate
suitecloud project:deploy
```

The existing deployment records are retained. The package adds `creation.js` and the
`custscript_nsa_creation_enabled` script parameter, and updates the Suitelet, worker and UI.
Reload the chat after deployment. Do not run an old worker with the new creation controls.

## 2. Grant the integration role creation permissions

The chat Administrator and the OAuth integration identity are different security contexts.
Your integration role needs **Create** (or the least higher level NetSuite requires) for
each business record type you intend to create, plus View access to referenced records.
For existing custom record types, grant Create or Edit to the integration role in their
Permissions subtab. Retain Edit permission on NetSuite Agent Job.

Examples: sales order creation needs Sales Order creation permission and access to the
chosen customer, item, subsidiary, currency, accounting and tax configuration. A subsidiary
requires applicable account features and subsidiary permissions. This feature does not
bypass account rules, required custom fields, workflows or user event scripts.

On **NetSuite Agent Worker > deployment > Parameters**, check **Enable approved business
creation**. The backend also requires this setting in `.env`:

```dotenv
AGENT_CREATION_ENABLED=true
```

Both switches default off. Query access continues to use read-only SuiteQL.

## 3. Enable new custom record type deployment (separate authentication)

Install SuiteCloud CLI for Node.js 4.x on the same host/user as the worker. Configure a
**dedicated noninteractive CI authentication ID** with SuiteCloud's documented certificate
based CI setup (`suitecloud account:setup:ci --help`). Its certificate mapping and role
must authorize SDF and custom record type customization. This is separate from the
RESTlet OAuth certificate configuration. A browser-authenticated developer auth ID may
work locally but can block for reauthentication; use CI authentication for unattended work.

Example (replace account / certificate / key path with your values):

```bash
suitecloud account:setup:ci \
  --account "$AGENT_ACCOUNT" \
  --authid agent-sdf \
  --certificateid "$AGENT_CERTIFICATE_ID" \
  --privatekeypath "$AGENT_PRIVATE_KEY_PATH"
```

You can reuse the same certificate files as RESTlet OAuth if that NetSuite role is also
allowed to deploy SDF customizations; otherwise create a dedicated certificate and role.

Verify the auth ID exists:

```bash
suitecloud account:manageauth --list
```

If that command reports secure storage problems, fix OS keyring/libsecret access for the
user that runs the worker, then recreate the CI auth ID.

Add to `.env`:

```dotenv
AGENT_SDF_ENABLED=true
AGENT_SDF_AUTH_ID=agent-sdf
AGENT_SUITECLOUD_BIN=suitecloud
AGENT_SDF_TIMEOUT=600
```

Keep `AGENT_ACCOUNT` set to the same destination account. Restart the worker after changing
these values. `netsuite-agent doctor` reports whether custom-type deployment is configured.
The worker generates an isolated
project under `data/creation-deployments/<job-id>/`, runs server validation before presenting
a draft, verifies the validation report's Account ID, and validates again before deployment.
It will refuse to deploy if it cannot verify the account or the approved files have changed.
The code expects the SuiteCloud 4.x summary (`Account ID` and `Status: SUCCESS`).

The draft includes the exact generated XML and a digest binding the complete project.
Draft preparation is local and fast; SuiteCloud `project:validate` and `project:deploy`
run after approval (with a short auth preflight). If validate hangs, check
`suitecloud account:manageauth --list` as the **same OS user** that runs the worker and
fix secure storage / recreate CI auth. `AGENT_SDF_TIMEOUT` defaults to 120 seconds.
Only structured custom record type definitions are accepted; the model cannot supply
shell commands, JavaScript or arbitrary deployment files. Existing script IDs are rejected;
this flow is not an update mechanism. Concurrent account customization outside the agent
must be coordinated to avoid a script-ID collision between validation and deployment.

**New types grant Administrator FULL and the integration worker custom role FULL**
(`[scriptid=customrole_...]`) so the agent can create entries immediately after deploy.
The live worker role script ID is resolved via ping; you can also set
`AGENT_INTEGRATION_ROLE_ID=customrole_...` explicitly. The draft preview lists the granted roles.
Schema refresh is requested after successful deployment and probes only the new type.

## 4. Restart and test

```bash
cd /home/shaheerbadar/Projects/netsuite-agent
.venv/bin/netsuite-agent serve
```

Use the chat's **Create a record or custom type** request type to skip the initial query
router. Automatic mode can also route creation requests. Keep Create mode selected when
answering creation clarifications.

Test with real sandbox references and unique names:

1. “Create a customer named Agent Sandbox Review Test. Ask me for any required fields.”
2. “Create a sales order for customer TEST US Customer, item F3 PL Hardware, quantity 2,
   memo AO-TEST-001. Ask me to review before saving.”
3. “Create an entry in [an existing custom type] with [its fields and values].”
4. “Create a new custom record type named Agent Equipment Inspection, script ID
   customrecord_agent_inspection, with a Date field custrecord_ai_date and an optional
   text field custrecord_ai_notes. Include the Name field.”

For each:

- Confirm no business record/type exists merely because a draft was prepared.
- Inspect names, internal IDs, sourced defaults, lines, currency, prices and totals.
- Reject one draft and verify that no creation occurs.
- Change one draft in a follow-up; its old approval must be rejected.
- Approve the revised draft. Verify the resulting record in NetSuite against the preview,
  including any save-time workflow changes.
- Click approval again or reload while executing; confirm only one record was created.
- Test duplicate customer names, invalid item IDs, required custom fields and permissions
  before expanding the capability catalog.

## Current supported scope

Standard creation catalog: customer, vendor, employee, contact, salesorder, purchaseorder,
estimate, invoice, creditmemo, cashsale, returnauthorization, vendorbill, vendorcredit,
journalentry, inventoryitem, noninventoryitem, serviceitem, assemblyitem, kititem, itemgroup,
lotnumberedinventoryitem, serializedinventoryitem, pricinggroup, pricelevel, subsidiary,
department, location and classification. Record-specific account acceptance is still required.

Existing custom types are discovered dynamically, excluding agent storage. Writable fields
are obtained from an unsaved dynamic `N/record` instance. The supported line sublists are
`item`, `expense`, `line`, and `member` (up to 50 lines each). Sourced values are rechecked
against the approved preview immediately before save; changed values require a new draft.

References use exact select-option matches or fixed live search resolvers. Ambiguous names
require an explicit ID. Large custom popup reference fields without a resolver are rejected,
not guessed. Add a reviewed resolver in `creation.js` for account-specific popup references.

New types support Name inclusion, description, and up to 40 fields of types TEXT, TEXTAREA,
CLOBTEXT, INTEGER, FLOAT, CURRENCY, CHECKBOX and DATE. New type list/reference fields,
standalone custom fields, modifying existing definitions, transformations, address and
inventory-detail subrecords, employee login/role/password changes, updates and deletes
are not included. The agent must report these limits rather than claim success.

Dynamic-record previews are not a simulation of every NetSuite save-time script. Some
validation and sourcing occur only during save. Dates accept ISO calendar dates; datetime
fields need a dedicated timezone-aware handler.

## Recovery and logs

A durable execution-start marker is written in NetSuite **before** a business save or SDF
deployment. Successful IDs/receipts are stored before normal queue completion. A crash or
timeout after execution starts does not cause automatic replay. This prevents blind duplicate
creation but is not a distributed exactly-once guarantee.

If the result says **Execution needs verification**, inspect NetSuite and the deployment log
before submitting a fresh request. Do not delete the job or clear its execution marker to
force a retry. An SDF deployment can partially succeed, and a user-event error can occur
after a business record is committed.

SDF stdout/stderr logs and XML remain in `data/creation-deployments/<job-id>/` with restricted
file permissions for reconciliation. These files are not covered by the ordinary completion
journal purge; archive/remove them according to your retention policy after reconciliation.
Run only one worker and one SDF executor per deployment. Remote records can change between
preview and save; preview equality checking reduces but cannot eliminate that race.

## References

- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/chapter_N3170023.html
- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_1526908548.html
- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/SDFxml.html
- https://docs.oracle.com/en/cloud/saas/netsuite/ns-online-help/section_156049843194.html

### Local model planning timeout

Creation planning uses `AGENT_CREATION_INFERENCE_TIMEOUT` (default 90 seconds).
Explicit creates prefer deterministic drafts and skip the SuiteQL planner, so most
drafts or permission errors return in seconds. Raise this only if fallback Ollama
extraction needs more time on CPU; it does not accelerate inference. Planning
timeouts return an actionable error and do not automatically retry record
execution. Restart the backend after changing this setting.

Also set `AGENT_POLL_SECONDS=2` (worker claim interval) so queued questions start
quickly. The chat UI polls status every 1 second after submit.

### Required fields and defaults

After a business record type is inspected, model output is constrained to draft
preparation for that type. It cannot return a field clarification in that stage.
The unsaved NetSuite record sources defaults and reports fields that remain
required, while reference lookup reports missing or ambiguous matches. Review
shows the sourced values before saving. Save-time scripts and workflows can
still impose additional requirements that unsaved inspection cannot discover.

### Constrained field extraction

Business-record preparation now uses a catalog of live field keys. The model
returns extracted values with a user quote, not arbitrary NetSuite payload JSON.
Python compiles reference objects and sublist rows, rejects unknown/system fields,
and excludes values without literal user evidence. Names are still resolved by
NetSuite. Omitted date/currency/rate fields are left for NetSuite sourcing.
Technical extraction failures are errors, not requests for the user to invent
missing business data. Logs record field names and line counts, not field values.
Natural-language date conversion and multiselect extraction require additional
handlers; this version does not claim support for every writable field type.
