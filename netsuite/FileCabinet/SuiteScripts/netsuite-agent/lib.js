/**
 * @NApiVersion 2.1
 */
define(['N/record', 'N/search', 'N/runtime', 'N/crypto/random'], (record, search, runtime, random) => {
    const TYPE = 'customrecord_nsa_job';
    const F = {
        state: 'custrecord_nsa_state', owner: 'custrecord_nsa_owner', request: 'custrecord_nsa_request',
        result: 'custrecord_nsa_result', lease: 'custrecord_nsa_lease', until: 'custrecord_nsa_until',
        created: 'custrecord_nsa_created', worker: 'custrecord_nsa_worker'
    };
    const fail = message => { throw new Error(message); };
    const param = name => runtime.getCurrentScript().getParameter({name});
    const admin = () => { if (Number(runtime.getCurrentUser().role) !== 3) fail('Administrator role required'); };
    function integration() {
        const role = Number(param('custscript_nsa_worker_role'));
        const user = Number(param('custscript_nsa_worker_user'));
        if (!role || !user || Number(runtime.getCurrentUser().role) !== role || Number(runtime.getCurrentUser().id) !== user)
            fail('Integration identity not authorized');
    }
    const identifier = value => /^[a-z][a-z0-9_]*$/i.test(String(value));
    function load(id) {
        if (!/^\d+$/.test(String(id))) fail('Invalid job ID');
        return record.load({type: TYPE, id: String(id), isDynamic: false});
    }
    const get = (r, name) => r.getValue({fieldId: F[name]});
    const set = (r, name, value) => r.setValue({fieldId: F[name], value});
    const json = (r, name, fallback) => { const value = get(r, name); return value ? JSON.parse(value) : fallback; };
    function owned(id) {
        const r = load(id);
        if (Number(get(r, 'owner')) !== Number(runtime.getCurrentUser().id)) fail('Job not found');
        return r;
    }
    function held(body) {
        const r = load(body.job);
        if (get(r, 'worker') !== body.worker || get(r, 'lease') !== body.lease || Number(get(r, 'until')) < Date.now())
            fail('Job lease lost');
        return r;
    }
    function save(r) { return r.save({enableSourcing: false, ignoreMandatoryFields: false}); }
    function list(filters, limit=20) {
        return search.create({type: TYPE, filters, columns: [search.createColumn({name: 'internalid', sort: search.Sort.ASC})]})
            .run().getRange({start: 0, end: limit});
    }
    function enqueue(request) {
        const owner = Number(runtime.getCurrentUser().id);
        const pending = list([[F.owner, 'equalto', owner], 'AND', [F.state, 'is', 'pending']], 5);
        if (pending.length >= 5) fail('Too many pending questions; wait or cancel an existing question');
        const r = record.create({type: TYPE, isDynamic: false});
        r.setValue({fieldId: 'name', value: 'Question ' + random.generateUUID()});
        set(r, 'state', 'pending'); set(r, 'owner', owner); set(r, 'created', Date.now());
        set(r, 'request', JSON.stringify(request));
        return String(save(r));
    }
    return {TYPE, F, fail, param, admin, integration, identifier, load, get, set, json, owned, held, save, list, enqueue,
        uuid: () => random.generateUUID()};
});
