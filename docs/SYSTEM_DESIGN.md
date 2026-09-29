# NetSuite Local Agent — system design

This diagram describes the implementation in this repository. NetSuite hosts the chat, queue, and business data. A local Python worker pulls jobs, uses local Ollama inference, and calls NetSuite to execute validated operations. All connections between the worker and NetSuite are initiated locally; no inbound tunnel is required.

## 1. Components and communication

Arrows show data flow; response arrows do not imply an inbound connection to the laptop. Dashed arrows are supporting or optional paths.

```mermaid
flowchart LR
    subgraph Browser[Administrator browser]
        UI[Chat UI<br/>Questions, follow-ups, pages, approvals]
    end

    subgraph Cloud[NetSuite account]
        SL[Suitelet<br/>Session, admin, CSRF and ownership checks]
        Jobs[(Job records<br/>Requests, leases, results,<br/>drafts and receipts)]
        API[Worker RESTlet<br/>Integration identity and lease checks]
        Query[N/query<br/>Read-only SuiteQL]
        Records[(NetSuite business records)]
        Write[N/record<br/>Approved record creation]
        Auth[OAuth 2.0 token endpoint]
        Cleanup[Scheduled cleanup]
    end

    subgraph Local[Local machine or Docker host]
        Worker[Python worker and engine<br/>Routing, retries, heartbeats]
        Model[Ollama<br/>Local model inference]
        Guard[SQL validator<br/>Oracle AST and schema checks]
        DB[(SQLite<br/>Schema, completion journal,<br/>audit metadata)]
        Knowledge[Business document<br/>Definitions and business rules]
        Schema[Schema discovery<br/>Inventory and field probes]
        SDF[SuiteCloud CLI<br/>Optional custom-type deployment]
        Ops[FastAPI management<br/>Health, status, schema refresh]
    end

    UI -->|HTTPS JSON: ask, status, page, cancel, approve| SL
    SL -->|Enqueue and read status| Jobs
    SL -->|Job ID, progress, result or draft| UI
    Worker -->|Outbound HTTPS: claim, heartbeat, query, complete| API
    API -->|Claim jobs and persist completions| Jobs
    Jobs -->|Request, history and stored result| API
    API -->|JSON: job and lease, rows, metadata, errors| Worker
    Worker -.->|Signed JWT client assertion| Auth
    Auth -.->|Short-lived access token| Worker
    Knowledge -->|Selected excerpts and version hash| Worker
    DB -->|Schema snapshot or completed answer| Worker
    Worker -->|Question, history, schema and definitions| Model
    Model -->|Structured plan: SQL, clarify, create or unsupported| Worker
    Worker -->|Proposed SQL| Guard
    Guard -->|Validated SQL or rejection| Worker
    API -->|Read guard and paged SQL execution| Query
    Query -->|SELECT| Records
    Records -->|Rows| Query
    Query -->|Rows, count and page metadata| API
    Worker -->|Durable answer before complete call| DB
    Schema -.->|Outbound inventory and probe requests| API
    Schema -.->|Versioned snapshot and diagnostics| DB
    Worker -.->|Approved creation actions via RESTlet| API
    API -.->|Rebuild and validate approved draft| Write
    Write -.->|Save record and return ID| Records
    Worker -.->|Approved custom type and artifact hash| SDF
    SDF -.->|Separate SuiteCloud auth: validate and deploy| Records
    Ops -.->|Inspect worker or request refresh| Worker
    Cleanup -.->|Remove expired queue records| Jobs
```

The browser talks only to the NetSuite Suitelet. The local FastAPI service is a management interface, not the chat backend. In Docker, port `8765` is bound to localhost and Ollama is reachable on the private Compose network at port `11434`.

## 2. One analytics request, end to end

```mermaid
sequenceDiagram
    autonumber
    actor Admin
    participant UI as Browser chat
    participant SL as Suitelet
    participant Q as NetSuite job records
    participant W as Local worker
    participant R as Worker RESTlet
    participant D as Local document and SQLite
    participant L as Ollama
    participant N as NetSuite N/query

    Admin->>UI: Ask a business question
    UI->>SL: ask(question, parent, mode, csrf)
    SL->>Q: Save pending job with owner
    SL-->>UI: Job ID
    W->>R: claim(worker), OAuth bearer token
    R->>Q: Claim pending or expired-lease job
    R-->>W: ID, lease, request, history, timezone
    W->>D: Check completion journal
    alt Previously completed job
        D-->>W: Stored answer
    else New analytics request
        W->>D: Load schema and retrieve business excerpts
        D-->>W: Relevant schema, definitions and versions
        W->>L: Question + context, HTTP POST /api/chat
        L-->>W: JSON plan with SQL and interpretation
        W->>W: Validate plan and SQL AST
        W->>R: query(job, lease, sql, page, record_links)
        R->>R: Check lease and read-only SQL guard
        R->>N: Execute paged SuiteQL
        N-->>R: Result rows and count
        R-->>W: Rows, pagination and supported record links
        Note over W,L: Validation/query errors can trigger bounded replanning.<br/>Returned analytics rows are not sent for model summarization.
        W->>D: Commit result and audit metadata
    end
    W->>R: complete(job, lease, result)
    R->>Q: Save answer and mark done
    loop Browser polls while job is active
        UI->>SL: status(id, csrf)
        SL->>Q: Read owned job
        SL-->>UI: State and result
    end
    UI-->>Admin: Table, SQL, definitions, record links and pages
```

The browser polls job status every 3 seconds. The worker waits 5 seconds between loop iterations by default and renews an active job's 120-second lease every 25 seconds. These are independent loops; responses do not use WebSockets or server push.

A follow-up creates another job linked to its parent. Up to four earlier turns supply questions and interpretations, not prior analytics row sets. A page request creates a new job that revalidates the original SQL and queries NetSuite again without model planning. Pages are live queries, not a frozen database snapshot.

## 3. Optional creation and approval

```mermaid
flowchart TD
    Request[Create request picked up by worker] --> Route{Record entry or new custom type?}
    Route -->|Record entry| Inspect[RESTlet capabilities and field inspection]
    Inspect --> Plan[Local field extraction / Ollama planning]
    Plan --> Prepare[RESTlet resolves references and builds unsaved record]
    Prepare --> Draft[Store review draft with nonce and expiry]
    Route -->|New custom type| Artifact[Build isolated SDF project and hash artifacts]
    Artifact --> Draft
    Draft --> Review[Administrator reviews preview in chat]
    Review -->|Reject| Stop[Cancel draft]
    Review -->|Approve matching nonce| Requeue[Suitelet records approval and requeues same job]
    Requeue --> Claim[Worker claims execution job]
    Claim --> Stored[Fetch approved draft from NetSuite]
    Stored --> Branch{Approved operation}
    Branch -->|Record entry| Rebuild[Rebuild and check preview drift]
    Rebuild --> Save[N/record save]
    Branch -->|Custom type| Verify[Verify artifacts and account; remote SDF validation]
    Verify --> Deploy[Mark execution started; SuiteCloud deployment]
    Save --> Receipt[Persist execution receipt]
    Deploy --> Receipt
    Receipt --> Result[Journal and publish result to chat]
    Deploy -.-> Refresh[Request local schema refresh]
```

Execution uses the stored approved draft, not a fresh model-generated execution payload. Creation and SDF require their respective feature flags and account permissions. SuiteCloud uses its own configured authentication, separate from RESTlet OAuth. If a save/deployment outcome is uncertain, the system requires reconciliation rather than promising exactly-once execution. Cancellation is blocked after execution has started.

## 4. What data moves and where it lives

- **Browser → Suitelet:** question, mode, parent/source job, page number, or approval nonce; the NetSuite session and CSRF token protect these requests.
- **Worker → RESTlet:** JSON containing `action`, worker ID and operation fields. Job operations carry the job ID and lease. HTTPS uses an OAuth bearer token obtained using a certificate-signed JWT assertion; the private key stays local.
- **Worker → Ollama:** the analytics question, recent interpretation history, selected schema, business-document excerpts, timezone and relevant repair errors. Creation planning uses capability/field metadata and requested values. Ollama returns structured JSON; it does not execute database operations.
- **NetSuite → worker → job record → browser:** actual result rows, count/page metadata, SQL, interpretation, definitions, version hashes and supported record links. Analytics figures come from NetSuite execution.
- **NetSuite job records:** temporary questions, state, owner, leases, results, drafts, approvals and execution receipts. These are separate from business records.
- **Local SQLite:** schema snapshots/status, durable completed answers and audit events. Audit events contain metadata; the completion journal can contain business result data.
- **Local files:** business definitions, credentials and optional SDF artifacts/logs. Business definitions are reloaded for each question and selected by deterministic retrieval; this implementation has no vector database.

## 5. Background work and failure handling

- Schema discovery calls RESTlet inventory and probes. Cold start waits for an initial usable snapshot; later refreshes run in the background while the worker serves the last good snapshot. The default refresh interval is one hour; analytics reject snapshots older than twice the configured interval. Failed probes remain visible as partial coverage.
- SQL validation checks the parsed statement against discovered schema before execution. A second RESTlet guard limits execution to reads, and NetSuite applies the integration identity's permissions.
- The worker checks cancellation/lease loss through heartbeats. Expired read-job leases can be reclaimed. The SQLite completion journal allows completed answers to be republished after interruption without rerunning inference; an interrupted read before journaling can run again.
- One worker per integration deployment is supported. A local process lock prevents duplicate workers using the same data directory.
- Local retention purges old completions and audit events; the NetSuite scheduled cleanup removes eligible old job records. Both default to seven days and are configured separately.
- `/health` reports process availability. `/status` and `/schema/refresh` require the local management bearer token.

## Implementation references

- Browser, session checks and job actions: [`chat.html`](../netsuite/FileCabinet/SuiteScripts/netsuite-agent/chat.html), [`suitelet.js`](../netsuite/FileCabinet/SuiteScripts/netsuite-agent/suitelet.js).
- Queue protocol, leases and query execution: [`worker_restlet.js`](../netsuite/FileCabinet/SuiteScripts/netsuite-agent/worker_restlet.js), [`lib.js`](../netsuite/FileCabinet/SuiteScripts/netsuite-agent/lib.js).
- Orchestration, planning and validation: [`worker.py`](../agent/worker.py), [`engine.py`](../agent/engine.py), [`llm.py`](../agent/llm.py), [`sql.py`](../agent/sql.py).
- Authentication and storage: [`netsuite.py`](../agent/netsuite.py), [`store.py`](../agent/store.py), [`schema.py`](../agent/schema.py), [`knowledge.py`](../agent/knowledge.py).
- Creation paths: [`creation.py`](../agent/creation.py), [`creation.js`](../netsuite/FileCabinet/SuiteScripts/netsuite-agent/creation.js), [`sdf.py`](../agent/sdf.py).
- Runtime and defaults: [`app.py`](../agent/app.py), [`config.py`](../agent/config.py), [`compose.yaml`](../compose.yaml).

Scope: source-code architecture, not a live deployment audit. Existing query flows have sandbox verification noted in the README; creation/SDF flows require separate sandbox acceptance.
