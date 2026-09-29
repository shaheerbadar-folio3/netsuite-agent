/**
 * @NApiVersion 2.1
 * @NScriptType Restlet
 */
define(['N/query', 'N/runtime', 'N/cache', 'N/url', 'N/search', './lib', './creation'],
    (query, runtime, cache, url, search, lib, creation) => {
    const PAGE_SIZE = 100;
    function workerLock(worker) {
        const expected = String(lib.param('custscript_nsa_worker_name') || 'local-primary');
        if (worker !== expected) lib.fail('Unexpected worker identity');
        // Deployment supports one worker process. NetSuite optimistic record saves protect job claims.
    }
    function statusCache() { return cache.getCache({name: 'nsa_worker_health', scope: cache.Scope.PUBLIC}); }
    function claim(body) {
        statusCache().put({key: 'last_seen', value: String(Date.now()), ttl: 300});
        const candidates = lib.list([[lib.F.state, 'is', 'pending'], 'OR',
            [[lib.F.state, 'is', 'running'], 'AND', [lib.F.until, 'lessthan', Date.now()]]], 10);
        for (const candidate of candidates) {
            let r = lib.load(candidate.id);
            if (lib.get(r, 'state') !== 'pending' && !(lib.get(r, 'state') === 'running' && Number(lib.get(r, 'until')) < Date.now())) continue;
            const lease = lib.uuid();
            lib.set(r, 'state', 'running'); lib.set(r, 'lease', lease);
            lib.set(r, 'worker', body.worker); lib.set(r, 'until', Date.now() + 120000);
            try { lib.save(r); } catch (e) { if (e.name === 'RCRD_HAS_BEEN_CHANGED') continue; throw e; }
            r = lib.load(candidate.id);
            if (lib.get(r, 'lease') !== lease) continue;
            const request = lib.json(r, 'request', {});
            const job = {id: String(candidate.id), lease, request, timezone: runtime.getCurrentUser().getPreference({name: 'TIMEZONE'})};
            const history = [];
            let parent = request.parent || request.source;
            const seen = new Set();
            for (let i = 0; parent && i < 4; i++) {
                if (seen.has(String(parent))) break;
                seen.add(String(parent));
                try {
                    const previous = lib.load(parent);
                    if (String(lib.get(previous, 'owner')) !== String(lib.get(r, 'owner'))) lib.fail('Invalid history owner');
                    const pr = lib.json(previous, 'request', {}), result = lib.json(previous, 'result', {});
                    if (request.kind === 'page' && i === 0) job.source_result = result;
                    history.unshift({question: pr.question || '', interpretation: result.interpretation || result.message || '',
                        ...(result.kind === 'draft' ? {draft: result.preview} : {})});
                    parent = pr.parent || pr.source;
                } catch (e) { break; }
            }
            job.history = history;
            return {job};
        }
        return {job: null};
    }
    function assertRead(sql) {
        if (typeof sql !== 'string' || sql.length > 16000) lib.fail('Invalid query size');
        // Defense in depth. Python validates the full AST; N/query exposes no write execution method.
        const stripped = sql.replace(/'(?:''|[^'])*'/g, "''").replace(/"(?:""|[^"])*"/g, 'identifier');
        if (!/^\s*(SELECT|WITH)\b/i.test(stripped) || /;|--|\/\*|\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|GRANT|REVOKE|INTO|FOR\s+UPDATE)\b/i.test(stripped))
            lib.fail('Only a single read query is allowed');
        if (!/\bORDER\s+BY\b/i.test(stripped)) lib.fail('ORDER BY is required');
        if (/\bcustomrecord_nsa_/i.test(stripped)) lib.fail('Agent storage cannot be queried');
    }
    function queryStage(stage, operation) {
        try { return operation(); }
        catch (e) {
            const error = new Error(stage + ' [' + String(e.name || 'ERROR') + ']: ' + String(e.message || 'Query failed').slice(0, 1000));
            error.name = String(e.name || 'QUERY_EXECUTION_FAILED');
            throw error;
        }
    }
    function execute(body) {
        const r = lib.held(body);
        if (lib.get(r, 'state') !== 'running') lib.fail('Job is no longer running');
        assertRead(body.sql);
        if (!Number.isInteger(body.page) || body.page < 0 || body.page >= 1000) lib.fail('Invalid page');
        let paged;
        try {
            paged = queryStage('create paged query', () => query.runSuiteQLPaged({query: body.sql, pageSize: PAGE_SIZE, metaDataProvider: 'SUITE_QL'}));
        } catch (e) {
            if (e.name !== 'UNEXPECTED_ERROR') throw e;
            // NetSuite's non-paged API returns at most 5,000 result rows.
            // Exactly 5,000 could be truncated: never claim completeness in that case.
            const fallback = queryStage('standard query fallback', () => query.runSuiteQL({query: body.sql, metaDataProvider: 'SUITE_QL'}).asMappedResults());
            if (fallback.length >= 5000) lib.fail('Paged query failed and the standard query reached its 5000-row limit. Narrow the question; completeness cannot be verified.');
            paged = {
                count: fallback.length,
                pageRanges: Array.from({length: Math.ceil(fallback.length / PAGE_SIZE)}, (_, index) => ({index})),
                fetch: ({index}) => ({data: {asMappedResults: () => fallback.slice(index * PAGE_SIZE, (index + 1) * PAGE_SIZE)}})
            };
        }
        if (body.page > 0 && body.page >= paged.pageRanges.length) lib.fail('Page no longer exists; rerun the question');
        const rows = paged.count ? queryStage('fetch query page', () => paged.fetch({index: body.page}).data.asMappedResults()) : [];
        const allowedTypes = new Set(['customer', 'vendor', 'employee', 'contact', 'subsidiary', 'department', 'location']);
        const transactionTypes = {SalesOrd: 'salesorder', CustInvc: 'invoice', CashSale: 'cashsale',
            CustCred: 'creditmemo', CustRfnd: 'customerrefund', RtnAuth: 'returnauthorization',
            ItemRcpt: 'itemreceipt', ItemShip: 'itemfulfillment', PurchOrd: 'purchaseorder', VendBill: 'vendorbill'};
        const specifications = Array.isArray(body.record_links) ? body.record_links.slice(0, 10) : [];
        const links = rows.map(row => {
            const mapped = {};
            for (const spec of specifications) {
                if (!lib.identifier(spec.column) || !/^\d+$/.test(String(row[spec.column]))) continue;
                const recordType = spec.record_type === 'transaction' && lib.identifier(spec.type_column)
                    ? transactionTypes[row[spec.type_column]] : allowedTypes.has(spec.record_type) ? spec.record_type : null;
                if (!recordType) continue;
                try { mapped[spec.column] = url.resolveRecord({recordType, recordId: row[spec.column], isEditMode: false}); } catch (e) { /* Some records have no accessible UI route. */ }
            }
            return mapped;
        });
        const result = {rows, links, page: body.page, page_size: PAGE_SIZE, total: paged.count,
            has_more: body.page + 1 < paged.pageRanges.length,
            truncated: paged.count > paged.pageRanges.length * PAGE_SIZE,
            notice: 'Live query; data can change between pages. Totals are computed by NetSuite.'};
        if (JSON.stringify(result).length > 80000) lib.fail('Result page too large; select fewer or shorter fields');
        return {result};
    }
    function inventory() {
        const names = Object.values(query.Type).filter(n => typeof n === 'string' && lib.identifier(n));
        // A documented analytics query type; no private Records Catalog HTTP endpoints are used.
        const custom = query.runSuiteQL({query: 'SELECT ScriptID FROM CustomRecordType ORDER BY ScriptID', metaDataProvider: 'SUITE_QL'}).asMappedResults();
        if (custom.length >= 5000) lib.fail('Custom-record inventory needs paging for this account');
        for (const r of custom) if (r.scriptid && lib.identifier(r.scriptid)) names.push(r.scriptid);
        return {tables: [...new Set(names.map(n => n.toLowerCase()))].filter(n => !n.startsWith('customrecord_nsa_')).sort()};
    }
    function probe(body) {
        // Bounded batch size keeps RESTlet governance predictable while allowing faster discovery.
        if (!Array.isArray(body.tables) || body.tables.length > 12) lib.fail('Probe accepts at most twelve tables');
        const tables = [], failures = [];
        for (const name of body.tables) {
            if (!lib.identifier(name) || name.startsWith('customrecord_nsa_')) lib.fail('Invalid table');
            try {
                const result = query.runSuiteQL({query: 'SELECT * FROM ' + name + ' WHERE 1 = 0', metaDataProvider: 'SUITE_QL'});
                let fields = (result.columns || []).map((c, index) => ({name: String(c.fieldId || c.alias || '').toLowerCase(),
                    label: String(c.label || ''), type: String((result.types || [])[index] || '')})).filter(f => lib.identifier(f.name));
                if (!fields.length) {
                    // SQL-string result sets may omit Column objects. A null-extended outer join
                    // exposes mapped field names even for empty tables, without reading business rows.
                    const projection = query.runSuiteQL({query: 'SELECT t.* FROM ' + name +
                        ' t RIGHT OUTER JOIN (SELECT 1 AS anchor FROM DUAL) d ON 1 = 0', metaDataProvider: 'SUITE_QL'}).asMappedResults();
                    fields = Object.keys(projection[0] || {}).filter(lib.identifier).map(n => ({name: n.toLowerCase(), label: '', type: ''}));
                }
                if (!fields.length) lib.fail('N/query did not expose usable column metadata for this table');
                tables.push({name: name.toLowerCase(), fields});
            } catch (e) { failures.push({table: name, code: String(e.name || 'METADATA_UNAVAILABLE'), message: String(e.message || 'Metadata unavailable').slice(0, 1000)}); }
        }
        return {tables, failures};
    }
    function queryDiagnostics() {
        // Fixed read-only probes: no caller-supplied SQL and no business detail rows.
        const checks = [
            ['count_plain', 'SELECT COUNT(*) AS total_customers FROM customer'],
            ['count_order_alias', 'SELECT COUNT(*) AS total_customers FROM customer ORDER BY total_customers'],
            ['count_order_expression', 'SELECT COUNT(*) AS total_customers FROM customer ORDER BY COUNT(*)'],
            ['id_projection_empty', 'SELECT id FROM customer WHERE 1 = 0 ORDER BY id']
        ];
        return {checks: checks.map(([name, sql]) => {
            try {
                const rows = query.runSuiteQL({query: sql, metaDataProvider: 'SUITE_QL'}).asMappedResults();
                return {name, sql, ok: true, rows};
            } catch (e) {
                return {name, sql, ok: false, code: String(e.name || 'ERROR'), message: String(e.message || 'Query failed').slice(0, 1000)};
            }
        })};
    }
    function post(body) {
        try {
            lib.integration(); workerLock(body.worker);
            let value;
            switch (body.action) {
                case 'ping': {
                    const role = runtime.getCurrentUser().role;
                    let role_script_id = '';
                    try {
                        const found = search.lookupFields({
                            type: search.Type.ROLE, id: role, columns: ['scriptid']
                        });
                        const raw = found && found.scriptid;
                        role_script_id = String(
                            typeof raw === 'string' ? raw
                                : (Array.isArray(raw) && raw[0] && (raw[0].value || raw[0].text)) || ''
                        ).toLowerCase();
                    } catch (e1) {
                        try {
                            const rows = query.runSuiteQL({
                                query: 'SELECT scriptid FROM role WHERE id = ' + Number(role),
                                metaDataProvider: 'SUITE_QL'
                            }).asMappedResults();
                            role_script_id = String(rows[0] && rows[0].scriptid || '').toLowerCase();
                        } catch (e2) { /* Role script ID lookup is best-effort for SDF grants. */ }
                    }
                    value = {service: 'netsuite-agent', version: '0.1.0', role, role_script_id};
                    break;
                }
                case 'claim': value = claim(body); break;
                case 'heartbeat': {
                    const r = lib.held(body);
                    const cancelled = lib.get(r, 'state') === 'cancelled';
                    if (!cancelled) { lib.set(r, 'until', Date.now() + 120000); lib.save(r); }
                    statusCache().put({key: 'last_seen', value: String(Date.now()), ttl: 300});
                    value = {cancelled}; break;
                }
                case 'complete': {
                    const r = lib.held(body);
                    if (lib.get(r, 'state') === 'cancelled') { value = {cancelled: true}; break; }
                    if (lib.get(r, 'state') === 'done') { value = {completed: true}; break; }
                    if (lib.get(r, 'state') !== 'running') lib.fail('Job is no longer running');
                    const result = JSON.stringify(body.result);
                    if (!body.result || !['result', 'clarify', 'unsupported', 'error', 'draft', 'created', 'uncertain'].includes(body.result.kind) || result.length > 95000)
                        lib.fail('Invalid result');
                    lib.set(r, 'result', result); lib.set(r, 'state', 'done'); lib.save(r);
                    value = {completed: true}; break;
                }
                case 'query': value = execute(body); break;
                case 'query_diagnostics': value = queryDiagnostics(); break;
                case 'schema_inventory': value = inventory(); break;
                case 'schema_probe': value = probe(body); break;
                default:
                    if (typeof body.action === 'string' && body.action.startsWith('creation_')) value = creation.handle(body);
                    else lib.fail('Unknown action');
            }
            return {ok: true, ...value};
        } catch (e) {
            return {ok: false, error: {code: String(e.name || 'ERROR'), message: String(e.message || 'Operation failed').slice(0, 1500)}};
        }
    }
    return {post};
});
