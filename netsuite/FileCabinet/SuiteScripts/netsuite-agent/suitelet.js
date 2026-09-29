/**
 * @NApiVersion 2.1
 * @NScriptType Suitelet
 */
define(['N/file', 'N/runtime', 'N/cache', './lib'], (file, runtime, cache, lib) => {
    function send(response, data) {
        response.setHeader({name: 'Content-Type', value: 'application/json; charset=utf-8'});
        response.setHeader({name: 'Cache-Control', value: 'no-store'});
        response.write(JSON.stringify(data));
    }
    function onRequest(context) {
        try {
            lib.admin();
            const session = runtime.getCurrentSession();
            if (context.request.method === 'GET') {
                let csrf = session.get({name: 'nsa_csrf'});
                if (!csrf) { csrf = lib.uuid(); session.set({name: 'nsa_csrf', value: csrf}); }
                const html = file.load({id: 'SuiteScripts/netsuite-agent/chat.html'}).getContents();
                context.response.setHeader({name: 'Content-Type', value: 'text/html; charset=utf-8'});
                context.response.setHeader({name: 'Cache-Control', value: 'no-store'});
                context.response.setHeader({name: 'X-Content-Type-Options', value: 'nosniff'});
                context.response.write(html.replace('__CSRF_TOKEN__', csrf));
                return;
            }
            if (String(context.request.body || '').length > 20000) lib.fail('Request too large');
            const body = JSON.parse(context.request.body || '{}');
            if (!body.csrf || body.csrf !== session.get({name: 'nsa_csrf'})) lib.fail('Session expired; reload this page');
            let result;
            switch (body.action) {
                case 'ask': {
                    const question = String(body.question || '').trim();
                    if (!question || question.length > 6000) lib.fail('Enter a question of 1–6000 characters');
                    if (body.parent) {
                        const previous = lib.owned(body.parent);
                        if (lib.json(previous, 'result', {}).kind === 'draft' && lib.get(previous, 'state') === 'done') {
                            lib.set(previous, 'state', 'superseded'); lib.save(previous);
                        }
                    }
                    result = {id: lib.enqueue({kind: 'ask', question, mode: body.mode === 'create' ? 'create' : 'auto', parent: body.parent || null})}; break;
                }
                case 'approve': {
                    const r = lib.owned(body.id), draft = lib.json(r, 'result', {}), request = lib.json(r, 'request', {});
                    if (request.kind === 'execute' && request.approved_nonce === body.nonce) {
                        result = {id: String(body.id)}; break;
                    }
                    if (lib.get(r, 'state') !== 'done' || draft.kind !== 'draft' || !body.nonce || draft.nonce !== body.nonce || draft.expires_at < Date.now())
                        lib.fail('Draft changed, was rejected, or expired. Request a new draft.');
                    request.kind = 'execute'; request.approved_nonce = draft.nonce;
                    request.approved_by = String(runtime.getCurrentUser().id); request.approved_at = Date.now();
                    lib.set(r, 'request', JSON.stringify(request));lib.set(r, 'state', 'pending');lib.save(r);
                    result = {id: String(body.id)}; break;
                }
                case 'reject': {
                    const r = lib.owned(body.id);
                    if (lib.get(r, 'state') !== 'done' || lib.json(r, 'result', {}).kind !== 'draft') lib.fail('Draft is not awaiting approval');
                    lib.set(r, 'state', 'cancelled');lib.save(r);result = {rejected: true};break;
                }
                case 'page': {
                    const source = lib.owned(body.source), answer = lib.json(source, 'result', {});
                    if (answer.kind !== 'result' || !Number.isInteger(body.page) || body.page < 0 || body.page >= 1000)
                        lib.fail('Invalid page request');
                    result = {id: lib.enqueue({kind: 'page', source: String(body.source), page: body.page})}; break;
                }
                case 'status': {
                    const r = lib.owned(body.id);
                    result = {id: String(body.id), state: lib.get(r, 'state'), result: lib.json(r, 'result', null)}; break;
                }
                case 'cancel': {
                    const r = lib.owned(body.id);
                    if (lib.json(r, 'request', {}).execution_started) lib.fail('Execution has started and cannot safely be cancelled. Wait for its result.');
                    if (['pending', 'running'].includes(lib.get(r, 'state'))) { lib.set(r, 'state', 'cancelled'); lib.save(r); }
                    result = {cancelled: lib.get(r, 'state') === 'cancelled'}; break;
                }
                case 'health': {
                    const lastSeen = Number(cache.getCache({name: 'nsa_worker_health', scope: cache.Scope.PUBLIC}).get({key: 'last_seen'}) || 0);
                    result = {online: Date.now() - lastSeen < 90000, last_seen: lastSeen}; break;
                }
                default: lib.fail('Unknown action');
            }
            send(context.response, {ok: true, ...result});
        } catch (e) {
            send(context.response, {ok: false, error: String(e.message || 'Request failed').slice(0, 1500)});
        }
    }
    return {onRequest};
});
