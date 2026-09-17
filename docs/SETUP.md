# Install and connect the NetSuite agent

This runbook connects the implemented application to your sandbox. Local tests use a
simulated NetSuite runtime and simulated model responses. They do not prove your account's
permissions, schema coverage, model accuracy, or latency. Complete the live checks below
before using answers for business decisions.

## 1. Local dependencies

Use Linux (including WSL2), Python 3.11 or newer, and Node 20+ for JavaScript tests.
The worker uses a POSIX process lock; native Windows is not currently supported.

```bash
cd /home/shaheerbadar/Projects/netsuite-agent
bash scripts/setup.sh
```

If Python reports that ensurepip is unavailable, install your distribution's matching
Python venv package first (for example `python3-venv`). An already prepared `.venv` is
present in the implementation workspace. Do not recreate it unnecessarily.

The setup script creates `.env` only if it does not already exist, generates a local
management token, and restricts file permissions. Never put credentials in the business document.

## 2. Local Ollama model

Install Ollama from its official distribution: https://ollama.com/download/linux.
Start with `qwen3.5:4b`; the download is approximately 3.4 GB and runtime memory is higher.
The inspected computer had about 14 GiB usable RAM and only 2.9 GiB available during planning.
Close memory-heavy applications before loading a model. GPU acceleration is not assumed.

Configure the Ollama process with `OLLAMA_NO_CLOUD=1`, `OLLAMA_NUM_PARALLEL=1`, and
`OLLAMA_MAX_LOADED_MODELS=1`. Keep its listener on loopback. For a manually started instance:

```bash
OLLAMA_NO_CLOUD=1 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 ollama serve
```

If the installer runs Ollama as a service, apply those environment variables to that service
instead of starting a second instance. In another terminal:

```bash
ollama pull qwen3.5:4b
.venv/bin/netsuite-agent benchmark
```

Benchmark measures one synthetic planning request, not NetSuite accuracy. Compare the 9B
model only if hardware allows it. Change `AGENT_MODEL` in `.env` to switch models. There is
no cloud fallback. The backend only permits loopback inference or the private compose
service named `ollama`; it does not send prompts to a vendor AI endpoint.

## 3. Deploy the NetSuite files and objects

In the **sandbox**, enable SuiteScript/server SuiteScript, custom records, and OAuth 2.0 as
needed. Use Oracle's SuiteCloud CLI or IDE to authenticate to the sandbox and validate/deploy
the Account Customization project under `netsuite/`. Follow the installed CLI's login flow;
do not paste passwords or tokens into chat.

Typical SuiteCloud CLI flow (run from `netsuite/`):

```bash
suitecloud account:setup
suitecloud project:validate
suitecloud project:deploy
```

Use the official CLI documentation if your installed version requests additional options.
The package supplies one custom job record, the Suitelet, worker RESTlet, cleanup script,
shared library, and chat HTML. **Deployment records and account-specific role assignments
must be configured in your sandbox**; they are intentionally not assigned guessed IDs.

Create these deployments in Customization > Scripting > Scripts:

1. **NetSuite Agent Chat** (`customscript_nsa_chat`): deploy as a Suitelet, Released, audience
   Administrator only, execute as Current User. Never enable Available Without Login.
   Save its internal URL as a shortcut in NetSuite.
2. **NetSuite Agent Worker** (`customscript_nsa_worker`): deploy as a RESTlet, Released,
   audience restricted to the dedicated integration role/user below. Copy its **External URL**.
3. **NetSuite Agent Retention** (`customscript_nsa_cleanup`): deploy as a scheduled script,
   run daily, set Retention days to 7 (or your chosen 1–90 days). Run under a role able to delete
   the agent's own job records. It never deletes business records. At most 1,000 old jobs are
   removed per run; increase scheduling frequency if necessary.

NetSuite's SuiteCloud server-side validation is mandatory. Local XML parsing verifies
file structure, not every account feature or Oracle XML enum.

## 4. Create the integration identity and OAuth mapping

Create a dedicated integration user and role. UI access remains Administrator-only, but the
worker authenticates using this separate identity:

- Permit OAuth 2.0 access tokens and RESTlet access using the role settings available in your account.
- Grant **View** for the business records the assistant should query, including needed
  subsidiaries and custom records. Add required analytics permissions for N/query.
- Grant **Edit** on **NetSuite Agent Job** through the custom record permission list.
  The worker needs to read and update jobs; the administrator Suitelet creates them.
- Do not grant business-record edit permissions just to make a query work.
- Grant access needed to read CustomRecordType metadata for schema discovery.
- Avoid the Web Services Only role setting for RESTlet integrations.

Set the worker deployment parameters:

- `custscript_nsa_worker_role`: the integration role's numeric internal ID.
- `custscript_nsa_worker_user`: the integration user's numeric internal ID.
- `custscript_nsa_worker_name`: `local-primary` (must match `.env`).

Create an integration record with OAuth 2.0 client credentials (M2M) enabled and RESTlets
scope. Record the client ID privately. Generate an RSA certificate locally:

```bash
mkdir -p secrets
chmod 700 secrets
openssl req -new -x509 -newkey rsa:3072 -nodes \
  -keyout secrets/private.pem -out secrets/public.pem -days 365 \
  -subj '/CN=NetSuite Local Agent'
chmod 600 secrets/private.pem
```

In NetSuite's OAuth 2.0 Client Credentials (M2M) setup, map the application, integration user,
role, and **public** certificate. Copy the certificate ID. The private key never goes to
NetSuite. NetSuite signs the integration's authority through this mapping; no client secret
is needed by this flow. Sandbox refreshes may require recreating the mapping.

Populate `.env` with your account ID (for example `1234567_SB1`), RESTlet external URL,
client ID, certificate ID, and private key path. Keep the generated management token.

```bash
.venv/bin/netsuite-agent check-netsuite
```

Expected: `ok: true`, service `netsuite-agent`, and your integration role ID. If authentication
fails, check certificate mapping, account hostname, role audience, clock synchronization,
and deployment parameters. Do not log the access token to troubleshoot.

## 5. Fill in your business document

Edit `knowledge/business.md`. Replace all relevant placeholders with verified business
definitions and remove `BUSINESS_DOCUMENT_NOT_CONFIGURED`. Keep missing definitions
explicit rather than inventing them. The agent intentionally refuses query generation
while the template marker remains.

Use `## Core rules` for definitions that must always be included. Put other concepts in
separate `##` sections using the words administrators normally use. State exact field IDs,
status values, timezone, currency treatment, row grain, and verified relationships.

The file is reread for each new question. Its content hash appears on answers. Small
documents are included in full; large documents use local lexical retrieval while preserving
core rules. Oversized mandatory rules fail clearly rather than being silently truncated.
No separate embedding model or vector database is required.

## 6. Start the application and inspect readiness

```bash
.venv/bin/netsuite-agent doctor
.venv/bin/netsuite-agent serve
```

The worker discovers the schema before claiming questions. Full initial discovery can take
several minutes and uses NetSuite governance. It enumerates documented N/query types and
custom record script IDs, then probes metadata in batches of six. If SQL-string Column
objects are unavailable, a null-extended join attempts to discover mapped field names
without extracting business rows, including for empty tables. **Both paths require live
verification in your account.** Unsupported record types are reported as failed; they are
never silently fabricated. The previous snapshot survives a complete refresh failure and
queries fail closed once it becomes stale.

View readiness without placing the bearer token in shell history:

```bash
.venv/bin/python scripts/status.py
```

It must report an installed model, a configured business document, and a successful schema
refresh. Inspect `data/agent.sqlite3` or the schema diagnostics command for failed tables:

```bash
.venv/bin/python scripts/schema_report.py
```

Verify customer, transaction, and your business's custom records are present. A successful
refresh with some unsupported tables is partial coverage, not universal coverage. The
document supplies join semantics; discovery does not infer trustworthy business joins.

Open the Suitelet inside NetSuite as Administrator and submit a real question. It should
move from queued to running, then return result rows or a clarification.

## 7. Required live acceptance checks

Use `docs/ACCEPTANCE.md`. These checks are not marked passed by the local test suite.
Do not claim production readiness until actual results, metadata refresh, authorization,
latency, and memory have been measured in the sandbox.

## 8. Move to a server later

Stop the local worker before starting a replacement. **One active worker per integration
deployment is supported**; horizontal multi-worker operation is not implemented.

Install the same locked dependencies on the server, copy the business document and private
configuration securely, and move the SQLite database using the backup procedure in OPERATIONS.md.
Either run Ollama and the worker on that server, or use the compose configuration:

```bash
docker compose up -d --build
docker compose exec ollama ollama pull qwen3.5:4b
```

The compose model service has no publicly exposed port, and the management port binds to
server loopback. Set private-key file permissions so the unprivileged container user (UID
10001) can read the mounted Docker secret without making the key world-readable. Rootless
Docker/user-namespace mappings may require adjusting ownership. The sample compose setup
uses CPU inference; GPU configuration is hardware-specific. Pin and verify the Ollama
image version during deployment rather than relying on a floating tag.
