/**
 * @NApiVersion 2.1
 */
define(['N/record', 'N/search', 'N/query', 'N/url', './lib'], (record, search, query, url, lib) => {
    // Explicit create support, not the N/query table inventory. Expand only after sandbox verification.
    const STANDARD = ['customer','vendor','employee','contact','salesorder','purchaseorder','estimate',
        'invoice','creditmemo','cashsale','returnauthorization','vendorbill','vendorcredit','journalentry',
        'inventoryitem','noninventoryitem','serviceitem','assemblyitem','kititem','itemgroup',
        'lotnumberedinventoryitem','serializedinventoryitem','pricinggroup','pricelevel',
        'subsidiary','department','location','classification'];
    const SUBLISTS = ['item','expense','line','member'];
    const BLOCKED = /^(id|internalid|externalid|customform|type|recordtype|baserecordtype|rectype|_eml_nkey_|nsapi.*|nluser|nlrole|nlsub|nldept|nlloc|entryformquerystring|sys_.*|.*password.*|giveaccess|accessrole|roles|emailpassword|requirepwdchange|isinactive|lastmodified.*|createddate|datecreated|balance|subsidiaryedition|nexus)$/i;
    const READONLY = new Set(['total','subtotal','taxtotal','tax2total','shippingcost','discounttotal']);
    // Sourced display noise and formula totals must not block an otherwise identical approval.
    const PREVIEW_IGNORE = new Set([...READONLY, 'taxamount', 'grossamount', 'costestimate',
        'quantityfulfilled', 'quantitybilled', 'quantitybackordered', 'quantitycommitted',
        'amountordered', 'amountbilled', 'fulfillable', 'printitems']);
    const MATERIAL_BODY = new Set(['entity','subsidiary','currency','trandate','memo','department',
        'class','location','employee','salesrep','partner','leadsource','otherrefnum','ponumber',
        'companyname','firstname','lastname','email','phone','entityid','itemid','parent','country']);
    const MATERIAL_LINE = new Set(['item','quantity','rate','price','taxcode','department','class',
        'location','description','units','inventorylocation']);
    const PRIORITY = ['entity','subsidiary','currency','trandate','item','quantity','price','rate'];
    const REFERENCES = {subsidiary:['subsidiary','name'],currency:['currency','name'],location:['location','name'],
        department:['department','name'],class:['classification','name'],employee:['employee','entityid'],
        salesrep:['employee','entityid'],supervisor:['employee','entityid'],owner:['employee','entityid'],item:['item','itemid'],
        account:['account','name'],incomeaccount:['account','name'],assetaccount:['account','name'],
        expenseaccount:['account','name'],cogsaccount:['account','name'],pricinggroup:['pricinggroup','name']};
    // Alternate searchable labels: users often supply display/UI names, not primary IDs.
    const NAME_FIELDS = {
        item: ['itemid', 'displayname'],
        subsidiary: ['namenohierarchy', 'name'],
        customer: ['entityid', 'companyname', 'altname'],
        vendor: ['entityid', 'companyname', 'altname'],
        employee: ['entityid', 'firstname', 'lastname', 'email'],
        currency: ['name', 'symbol'],
        account: ['name', 'displayname'],
        location: ['name'],
        department: ['name'],
        classification: ['name'],
        pricinggroup: ['name']
    };
    const hasOwn = (obj, key) => Object.prototype.hasOwnProperty.call(obj, key);
    const isEmpty = v => v === '' || v === null || v === undefined || (Array.isArray(v) && !v.length);
    function stage(label, operation) {
        try { return operation(); }
        catch (e) {
            const code = String(e && e.name || 'ERROR');
            throw new Error(label + ' [' + code + ']: ' + String(e && e.message || 'NetSuite returned no error detail'));
        }
    }
    const normalizeLabel = value => String(value || '').toLowerCase().replace(/[.]+$/g, '').replace(/\s+/g, ' ').trim();
    let customTypeCache = null;
    function enabled() {
        const value = lib.param('custscript_nsa_creation_enabled');
        if (value !== true && value !== 'T') lib.fail('Business creation is disabled on this worker deployment');
    }
    function running(body) {
        enabled(); const r = lib.held(body);
        if (lib.get(r,'state') !== 'running') lib.fail('Job is no longer running');
        return r;
    }
    function customTypeExists(script) {
        const id = String(script || '').toLowerCase();
        if (customTypeCache) return customTypeCache.includes(id);
        // Targeted existence check avoids loading the full CustomRecordType inventory
        // on every inspect/prepare for a known customrecord_... type.
        // SuiteQL ScriptID comparisons are case-sensitive; always match on LOWER(...).
        const rows = query.runSuiteQL({
            query: "SELECT ScriptID FROM CustomRecordType WHERE LOWER(ScriptID) = '" +
                id.replace(/'/g, "''") + "'",
            metaDataProvider: 'SUITE_QL'
        }).asMappedResults();
        return rows.some(r => String(r.scriptid || '').toLowerCase() === id);
    }
    function customTypes() {
        if (customTypeCache) return customTypeCache;
        const rows = query.runSuiteQL({query:'SELECT ScriptID FROM CustomRecordType ORDER BY ScriptID',metaDataProvider:'SUITE_QL'}).asMappedResults();
        if (rows.length >= 5000) lib.fail('Custom type inventory reached the limit; paging is required before creation');
        customTypeCache = rows.map(r=>String(r.scriptid).toLowerCase()).filter(n=>/^customrecord_[a-z0-9_]+$/.test(n) && !n.startsWith('customrecord_nsa_'));
        return customTypeCache;
    }
    function checkType(type) {
        if (STANDARD.includes(type)) return;
        if (/^customrecord_[a-z0-9_]+$/.test(type) && !type.startsWith('customrecord_nsa_') && customTypeExists(type)) return;
        lib.fail('Record type is not in the creation capability catalog: ' + String(type));
    }
    function available(script) {
        if (!/^customrecord_[a-z][a-z0-9_]{0,26}$/.test(script) || script.startsWith('customrecord_nsa_')) lib.fail('Invalid custom record type ID');
        if (customTypeExists(script)) lib.fail('That custom record type already exists. This operation only creates new types.');
    }
    function existingCustomFieldIds(ids) {
        // custrecord_* script IDs are account-global across all custom record types.
        const hits = [];
        for (const raw of ids || []) {
            const id = String(raw || '').toLowerCase();
            if (!/^custrecord_[a-z][a-z0-9_]{0,28}$/.test(id)) continue;
            let found = false;
            for (const table of ['CustomRecordCustomField', 'customrecordcustomfield', 'CustomField']) {
                try {
                    const rows = query.runSuiteQL({
                        query: 'SELECT scriptid FROM ' + table + " WHERE LOWER(scriptid) = '" +
                            id.replace(/'/g, "''") + "'",
                        metaDataProvider: 'SUITE_QL'
                    }).asMappedResults();
                    if (rows && rows.length) { found = true; break; }
                } catch (e) { /* Table name differs by account metadata provider. */ }
            }
            if (found) hits.push(id);
        }
        return hits;
    }
    function assertFieldsAvailable(spec) {
        const ids = (spec && Array.isArray(spec.fields) ? spec.fields : [])
            .map(f => f && f.script_id).filter(Boolean);
        const clash = existingCustomFieldIds(ids);
        if (clash.length) {
            lib.fail('Custom field script ID(s) already exist in this account (field IDs are global across types): '
                + clash.join(', ')
                + '. Choose unique IDs such as custrecord_<typeabbrev>_date for the new type.');
        }
    }
    function fieldInfo(f) {
        if (!f) return null;
        return {id:f.id,label:String(f.label || f.id),type:String(f.type),mandatory:Boolean(f.isMandatory)};
    }
    function writable(f, id) {
        // N/record server fields do not expose the N/currentRecord client
        // isReadOnly/isDisabled properties. NetSuite validates assignments on
        // the unsaved record; the explicit blocked fields remain excluded.
        return f && !BLOCKED.test(id) && !READONLY.has(id) &&
            !['inlinehtml','password','summary','image'].includes(String(f.type));
    }
    function inspect(type) {
        checkType(type);
        const r = record.create({type,isDynamic:true});
        const fields = r.getFields().map(id=>{const f=r.getField({fieldId:id});return writable(f,id)?fieldInfo(f):null;}).filter(Boolean);
        const sublists = {};
        // Custom record entries rarely use the supported transaction sublists; skip the
        // empty selectNewLine loop that still costs governance on every inspect.
        const candidates = type.startsWith('customrecord_') ? [] : r.getSublists().filter(n=>SUBLISTS.includes(n));
        for (const id of candidates) {
            try {
                r.selectNewLine({sublistId:id});
                sublists[id] = r.getSublistFields({sublistId:id}).map(fieldId=>{
                    const f=r.getCurrentSublistField({sublistId:id,fieldId});return writable(f,fieldId)?fieldInfo(f):null;
                }).filter(Boolean);
                r.cancelLine({sublistId:id});
            } catch(e) { sublists[id] = []; }
        }
        return {record_type:type,fields,sublists,notes:'Only creation is supported. Address/inventory-detail subrecords, transforms and employee login access require dedicated handlers.'};
    }
    function reference(type,id) {
        if(id==='entity') return ['purchaseorder','vendorbill','vendorcredit'].includes(type)?['vendor','entityid']:['customer','entityid'];
        if(id==='parent' && type==='subsidiary') return ['subsidiary','name'];
        return REFERENCES[id];
    }
    function searchFields(searchType, primary) {
        const extras = NAME_FIELDS[searchType] || [];
        return [...new Set([primary, ...extras].filter(Boolean))];
    }
    function labelVariants(wanted) {
        const text = String(wanted || '').trim();
        const variants = [text];
        const stripped = text.replace(/[.]+$/g, '').trim();
        if (stripped && stripped !== text) variants.push(stripped);
        return variants;
    }
    function optionMatch(options, wanted, byId) {
        if (byId) {
            const matches = options.filter(o => String(o.value) === wanted);
            return matches.length === 1 ? matches[0] : null;
        }
        const w = normalizeLabel(wanted);
        const predicates = [
            o => normalizeLabel(o.text) === w,
            o => {
                const text = normalizeLabel(o.text);
                return text.endsWith(' : ' + w) || text.endsWith(': ' + w);
            },
            o => w.length >= 3 && normalizeLabel(o.text).includes(w)
        ];
        for (const predicate of predicates) {
            const matches = options.filter(predicate);
            if (matches.length === 1) return matches[0];
            if (matches.length > 1) {
                lib.fail(`multiple matches: ${matches.slice(0,10).map(o=>`${o.text} (ID ${o.value})`).join(', ')}`);
            }
        }
        return null;
    }
    function rowLabel(row, fields) {
        for (const field of fields) {
            try {
                const value = row.getValue({name: field});
                if (value !== null && value !== undefined && String(value) !== '') return String(value);
            } catch (e) { /* Column may be unavailable for this search type. */ }
        }
        return String(row.id);
    }
    function searchRows(searchType, field, operator, wanted, columns) {
        try {
            return search.create({
                type: searchType,
                filters: [[field, operator, wanted]],
                columns
            }).run().getRange({start: 0, end: 11});
        } catch (e) {
            return null;
        }
    }
    function resolveBySearch(searchType, primary, wanted) {
        const fields = searchFields(searchType, primary);
        const columns = ['internalid', ...fields.filter((name, index, all) => all.indexOf(name) === index)].slice(0, 4);
        const variants = labelVariants(wanted);
        for (const field of fields) {
            for (const variant of variants) {
                const rows = searchRows(searchType, field, 'is', variant, columns);
                if (!rows) continue;
                if (rows.length === 1) return {id: String(rows[0].id), text: rowLabel(rows[0], fields)};
                if (rows.length > 1) {
                    lib.fail(`multiple matches for "${wanted}": ` +
                        rows.map(row => `${rowLabel(row, fields)} (ID ${row.id})`).join(', '));
                }
            }
        }
        for (const field of fields) {
            for (const variant of variants) {
                const rows = searchRows(searchType, field, 'contains', variant, columns);
                if (!rows || !rows.length) continue;
                const exact = rows.filter(row => fields.some(name => {
                    try { return normalizeLabel(row.getValue({name})) === normalizeLabel(variant); }
                    catch (e) { return false; }
                }));
                const chosen = exact.length === 1 ? exact : (rows.length === 1 ? rows : null);
                if (chosen && chosen.length === 1) return {id: String(chosen[0].id), text: rowLabel(chosen[0], fields)};
                if (rows.length > 1) {
                    lib.fail(`multiple matches for "${wanted}": ` +
                        rows.map(row => `${rowLabel(row, fields)} (ID ${row.id})`).join(', '));
                }
            }
        }
        return null;
    }
    function fieldText(value) {
        if (typeof value === 'string') return value.trim();
        if (Array.isArray(value) && value.length) {
            const first = value[0];
            if (typeof first === 'string') return first.trim();
            if (first && typeof first === 'object')
                return String(first.text || first.value || '').trim();
        }
        return '';
    }
    function resolveEmployeeById(wanted) {
        // Integration roles often cannot use internalid/anyof or lookupFields for
        // employees. Try several read paths before giving up.
        try {
            const found = search.lookupFields({
                type: 'employee', id: wanted, columns: ['entityid', 'firstname', 'lastname']
            });
            const text = fieldText(found && found.entityid) ||
                [fieldText(found && found.firstname), fieldText(found && found.lastname)]
                    .filter(Boolean).join(' ').trim();
            if (text) return {id: wanted, text};
        } catch (e) { /* continue */ }
        for (const operator of ['is', 'anyof']) {
            try {
                const rows = search.create({
                    type: 'employee',
                    filters: [['internalid', operator, wanted]],
                    columns: ['internalid', 'entityid', 'firstname', 'lastname']
                }).run().getRange({start: 0, end: 2});
                if (rows.length === 1) {
                    return {
                        id: String(rows[0].id),
                        text: rowLabel(rows[0], ['entityid', 'firstname', 'lastname'])
                    };
                }
            } catch (e) { /* continue */ }
        }
        try {
            const id = Number(wanted);
            if (!Number.isFinite(id) || id <= 0) return null;
            const rows = query.runSuiteQL({
                query: 'SELECT id, entityid FROM employee WHERE id = ' + id,
                metaDataProvider: 'SUITE_QL'
            }).asMappedResults();
            if (rows.length === 1 && rows[0].id != null) {
                return {
                    id: String(rows[0].id),
                    text: String(rows[0].entityid || wanted)
                };
            }
        } catch (e) { /* continue */ }
        return null;
    }
    function resolve(f, input, type, id) {
        if (!input || typeof input !== 'object' || Array.isArray(input) ||
            Object.keys(input).some(k => !['id', 'text'].includes(k)) ||
            (!hasOwn(input,'id') && !hasOwn(input,'text')))
            lib.fail(`${f.label || id}: provide an exact name or internal ID reference`);
        const wanted = String(input.id || input.text || '').trim();
        if (!wanted) lib.fail(`${id}: a reference value is required`);
        let options=null;
        try {
            options=f.getSelectOptions({
                filter: input.id ? wanted : (input.text || ''),
                operator: input.id ? 'is' : 'contains'
            });
        } catch(e) { /* Popup fields need a search resolver. */ }
        if ((!options || !options.length) && input.id) {
            try { options=f.getSelectOptions({filter:'',operator:'contains'}); } catch(e) { /* ignore */ }
        }
        if (Array.isArray(options) && options.length) {
            try {
                const matched = optionMatch(options, wanted, Boolean(input.id));
                if (matched && options.length < 500) return {id:String(matched.value),text:String(matched.text)};
            } catch (e) {
                if (String(e.message || '').startsWith('multiple matches')) lib.fail(`${id}: ${e.message}`);
                throw e;
            }
        }
        const target=reference(type,id);
        if (!target) lib.fail(`${id}: cannot uniquely resolve "${wanted}" from available options. This popup field needs a configured reference resolver.`);
        if (input.id && !/^[0-9]+$/.test(String(input.id).trim())) lib.fail(`${id}: invalid internal ID`);
        if (input.id) {
            const idWanted = String(input.id).trim();
            if (target[0] === 'employee') {
                const found = stage('lookup '+id+' employee by internal ID',()=>{
                    // Prefer the field's select options (fast) over employee record APIs
                    // that are often denied or multi-second for integration roles.
                    if (input.text) {
                        try {
                            const named = f.getSelectOptions({
                                filter: String(input.text).trim(), operator: 'contains'
                            });
                            if (Array.isArray(named) && named.length) {
                                const matched = optionMatch(named, String(input.text).trim(), false);
                                if (matched && String(matched.value) === idWanted)
                                    return {id: idWanted, text: String(matched.text)};
                            }
                        } catch (e) {
                            if (String(e.message || '').startsWith('multiple matches')) throw e;
                        }
                    }
                    // Approved drafts already resolved this numeric ID during prepare.
                    // Skip slow/denied employee lookups; setValue/save still validate.
                    return {
                        id: idWanted,
                        text: input.text ? String(input.text).trim() : idWanted
                    };
                });
                return found;
            }
            const rows=stage('resolve '+id+' by internal ID',()=>search.create({type:target[0],filters:[['internalid','anyof',idWanted]],columns:['internalid',target[1]]}).run().getRange({start:0,end:11}));
            if(rows.length!==1) lib.fail(`${id}: ${rows.length?'multiple matches':'no exact match'} for "${idWanted}"` +
                (rows.length?': '+rows.map(row=>`${row.getValue({name:target[1]})} (ID ${row.id})`).join(', '):''));
            return {id:String(rows[0].id),text:String(rows[0].getValue({name:target[1]}))};
        }
        try {
            const resolved = resolveBySearch(target[0], target[1], wanted);
            if (resolved) return resolved;
        } catch (e) {
            if (String(e.message || '').includes('multiple matches')) lib.fail(`${id}: ${e.message}`);
            throw e;
        }
        lib.fail(`${id}: no exact match for "${wanted}". Provide the internal ID if the display name differs from the record name/ID.`);
    }
    function valueFor(f,value,type,id) {
        if (f.type==='select' || f.type==='radio') {
            const resolved = resolve(f,value,type,id);
            return {kind:'reference', id:resolved.id, text:resolved.text || ''};
        }
        if (f.type==='multiselect') {
            if(!Array.isArray(value)) lib.fail(`${id}: expected reference list`);
            return value.map(v=>{
                const resolved=resolve(f,v,type,id);
                return {kind:'reference', id:resolved.id, text:resolved.text || ''};
            });
        }
        if(f.type==='date') {
            if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(value)) lib.fail(`${id}: date must be YYYY-MM-DD`);
            const [y,m,d]=value.split('-').map(Number), date=new Date(y,m-1,d);
            if(date.getFullYear()!==y || date.getMonth()!==m-1 || date.getDate()!==d) lib.fail(`${id}: invalid calendar date`);
            return date;
        }
        if(['datetime','datetimetz','timeofday'].includes(f.type)) lib.fail(`${id}: date/time fields require a dedicated timezone-aware handler`);
        if(f.type==='checkbox') {if(typeof value!=='boolean')lib.fail(`${id}: expected true or false`);return value;}
        if(['integer','float','currency','percent','posinteger','nonneginteger','rate','ratehighprecision'].includes(f.type)) {
            if(typeof value!=='number'||!Number.isFinite(value))lib.fail(`${id}: expected a finite number`);
            if(['integer','posinteger','nonneginteger'].includes(f.type) && !Number.isInteger(value))lib.fail(`${id}: expected an integer`);
            return value;
        }
        if(typeof value!=='string' || value.length>4000)lib.fail(`${id}: expected text of at most 4000 characters`);
        return value;
    }
    const ordered = obj => Object.keys(obj).sort((a,b)=>{
        const ai=PRIORITY.indexOf(a),bi=PRIORITY.indexOf(b);
        return (ai<0?100:ai)-(bi<0?100:bi)||a.localeCompare(b);
    });
    function jsonValue(v) {
        if(v instanceof Date) return `${v.getFullYear()}-${String(v.getMonth()+1).padStart(2,'0')}-${String(v.getDate()).padStart(2,'0')}`;
        return v === undefined ? null : v;
    }
    function display(r,id,sublist) {
        const opts={fieldId:id,...(sublist?{sublistId:sublist}:{})};
        const value=sublist?r.getCurrentSublistValue(opts):r.getValue(opts);
        let text='';try{text=sublist?r.getCurrentSublistText(opts):r.getText(opts);}catch(e){}
        return {value:jsonValue(value),text:typeof text==='string'?text:Array.isArray(text)?text.join(', '):''};
    }
    function build(type,payload) {
        checkType(type);
        if(!payload || typeof payload!=='object' || Array.isArray(payload) || Object.keys(payload).some(k=>!['fields','sublists'].includes(k)))lib.fail('Invalid creation payload');
        const fields=payload.fields || {}, sublists=payload.sublists || {};
        if(Array.isArray(fields)||Array.isArray(sublists)||typeof fields!=='object'||typeof sublists!=='object')lib.fail('Invalid fields or sublists');
        if(JSON.stringify(payload).length>20000 || Object.keys(fields).length>80)lib.fail('Creation draft too large');
        const r=record.create({type,isDynamic:true}), normalized={fields:{},sublists:{}}, preview={record_type:type,fields:{},sublists:{}},issues=[];
        function assign(id,value,sublist) {
            if(!lib.identifier(id))lib.fail('Invalid field ID');
            const opts={fieldId:id,...(sublist?{sublistId:sublist}:{})};
            const f=sublist?r.getCurrentSublistField(opts):r.getField(opts);
            if(!writable(f,id))lib.fail('Field is unavailable for creation: '+id);
            const converted=stage('resolve field '+(sublist?sublist+'.':'')+id,()=>valueFor(f,value,type,id));
            const writeValue = converted && converted.kind==='reference' ? converted.id
                : Array.isArray(converted) && converted[0] && converted[0].kind==='reference'
                    ? converted.map(item=>item.id) : converted;
            stage('assign field '+(sublist?sublist+'.':'')+id,()=>{
                if(sublist)r.setCurrentSublistValue({...opts,value:writeValue,ignoreFieldChange:false});
                else r.setValue({...opts,value:writeValue,ignoreFieldChange:false});
            });
            if (converted && converted.kind==='reference') {
                const out={id:String(converted.id)};
                if (value && typeof value==='object' && !Array.isArray(value) &&
                    value.id && String(value.id) === out.id && value.text)
                    out.text=String(value.text);
                else if (converted.text) out.text=String(converted.text);
                else if (value && typeof value==='object' && value.text)
                    out.text=String(value.text);
                return out;
            }
            if (Array.isArray(converted) && converted[0] && converted[0].kind==='reference') {
                return converted.map((item, index)=>{
                    const source = Array.isArray(value) ? value[index] : null;
                    const out={id:String(item.id)};
                    if (source && source.id && String(source.id) === out.id && source.text)
                        out.text=String(source.text);
                    else if (item.text) out.text=String(item.text);
                    else if (source && source.text) out.text=String(source.text);
                    return out;
                });
            }
            return jsonValue(converted);
        }
        for(const id of ordered(fields)) normalized.fields[id]=assign(id,fields[id]);
        for(const sublist of Object.keys(sublists).sort()) {
            if(!SUBLISTS.includes(sublist)||!r.getSublists().includes(sublist))lib.fail('Unsupported sublist: '+sublist);
            const rows=sublists[sublist];
            if(!Array.isArray(rows)||rows.length>50)lib.fail('At most 50 lines per sublist');
            normalized.sublists[sublist]=[];preview.sublists[sublist]=[];
            for(const [index,row] of rows.entries()) {
                if(!row||typeof row!=='object'||Array.isArray(row)||Object.keys(row).length>40)lib.fail('Invalid sublist line');
                r.selectNewLine({sublistId:sublist});
                const line={};
                for(const id of ordered(row))line[id]=assign(id,row[id],sublist);
                const snapshot={};
                for(const id of r.getSublistFields({sublistId:sublist}).sort()) {
                    const f=r.getCurrentSublistField({sublistId:sublist,fieldId:id});
                    if(BLOCKED.test(id)||!f)continue;
                    const val=display(r,id,sublist);
                    if(f.isMandatory&&isEmpty(val.value))issues.push(`${sublist} line ${index+1}: ${f.label||id} (${id}) is required`);
                    if(hasOwn(row,id)||f.isMandatory||['item','quantity','rate','amount','taxcode','department','class','location'].includes(id)) snapshot[id]={label:f.label||id,...val};
                }
                if(issues.length){r.cancelLine({sublistId:sublist});continue;}
                r.commitLine({sublistId:sublist});
                normalized.sublists[sublist].push(line);preview.sublists[sublist].push(snapshot);
            }
        }
        if(['salesorder','purchaseorder','estimate','invoice','creditmemo','cashsale','returnauthorization'].includes(type) && !r.getLineCount({sublistId:'item'})) issues.push('At least one item line is required');
        for(const id of r.getFields().sort()) {
            if(BLOCKED.test(id))continue;
            const f=r.getField({fieldId:id});if(!f)continue;
            const val=display(r,id);
            if(f.isMandatory && isEmpty(val.value))issues.push(`${f.label||id} (${id}) is required`);
            // Include sourced defaults and calculated totals, not just requested values.
            if(!isEmpty(val.value) && (writable(f,id)||READONLY.has(id)||hasOwn(fields,id))) preview.fields[id]={label:f.label||id,...val};
        }
        return {record:r,payload:normalized,preview,issues};
    }
    function previewValue(entry) {
        if (!entry || typeof entry !== 'object') return null;
        const value = entry.value;
        if (value === null || value === undefined || value === '') return null;
        if (typeof value === 'number') return Number.isFinite(value) ? value : null;
        if (typeof value === 'boolean') return value;
        return String(value);
    }
    function comparablePreview(preview, payload) {
        const bodyKeys = new Set(Object.keys((payload && payload.fields) || {}));
        const fields = {};
        for (const id of Object.keys((preview && preview.fields) || {}).sort()) {
            if (PREVIEW_IGNORE.has(id)) continue;
            if (!bodyKeys.has(id) && !MATERIAL_BODY.has(id)) continue;
            const value = previewValue(preview.fields[id]);
            if (value !== null) fields[id] = value;
        }
        const sublists = {};
        for (const name of Object.keys((preview && preview.sublists) || {}).sort()) {
            const requestedRows = ((payload && payload.sublists) || {})[name] || [];
            sublists[name] = (preview.sublists[name] || []).map((row, index) => {
                const requested = requestedRows[index] || {};
                const out = {};
                for (const id of Object.keys(row || {}).sort()) {
                    if (PREVIEW_IGNORE.has(id)) continue;
                    if (id === 'amount' && !hasOwn(requested, id)) continue;
                    if (!hasOwn(requested, id) && !MATERIAL_LINE.has(id)) continue;
                    const value = previewValue(row[id]);
                    if (value !== null) out[id] = value;
                }
                return out;
            });
        }
        return {record_type: preview && preview.record_type, fields, sublists};
    }
    function previewDrift(approved, current, payload) {
        const left = comparablePreview(approved, payload);
        const right = comparablePreview(current, payload);
        if (JSON.stringify(left) === JSON.stringify(right)) return null;
        const changes = [];
        const ids = new Set([...Object.keys(left.fields), ...Object.keys(right.fields)]);
        for (const id of [...ids].sort()) {
            if (left.fields[id] !== right.fields[id]) changes.push(`${id}: ${left.fields[id]} → ${right.fields[id]}`);
        }
        for (const name of new Set([...Object.keys(left.sublists), ...Object.keys(right.sublists)])) {
            const a = left.sublists[name] || [], b = right.sublists[name] || [];
            const count = Math.max(a.length, b.length);
            for (let i = 0; i < count; i++) {
                const rowIds = new Set([...Object.keys(a[i] || {}), ...Object.keys(b[i] || {})]);
                for (const id of [...rowIds].sort()) {
                    const before = (a[i] || {})[id], after = (b[i] || {})[id];
                    if (before !== after) changes.push(`${name}[${i}].${id}: ${before} → ${after}`);
                }
            }
        }
        return changes.slice(0, 12).join('; ') || 'preview values changed';
    }
    function makeDraft(operation,details) {
        return {kind:'draft',operation,nonce:lib.uuid(),expires_at:Date.now()+30*60000,...details,
            message:'Review the exact values below. No business record has been saved. Approval expires in 30 minutes. NetSuite save-time scripts and workflows may apply additional changes.'};
    }
    function approved(body) {
        const r=running(body), request=lib.json(r,'request',{}), draft=lib.json(r,'result',{});
        if(request.kind!=='execute'||!request.approved_nonce||request.approved_by!==String(lib.get(r,'owner')))lib.fail('Explicit administrator approval required');
        if(request.execution_receipt)return {r,request,receipt:request.execution_receipt};
        if(request.execution_started)lib.fail('Execution may already have occurred. Check NetSuite before submitting another request; automatic retry is blocked.');
        if(draft.kind!=='draft'||draft.nonce!==request.approved_nonce)lib.fail('Approved draft no longer matches');
        if(draft.expires_at<Date.now())lib.fail('Approval expired; prepare a new draft');
        return {r,request,draft};
    }
    function begin(body) {
        const a=approved(body);
        if(a.receipt)return a;
        a.request.execution_started=Date.now();lib.set(a.r,'request',JSON.stringify(a.request));lib.save(a.r);
        return a;
    }
    function receipt(body,result) {
        const r=lib.held(body),request=lib.json(r,'request',{});
        if(request.kind!=='execute'||!request.execution_started)lib.fail('No approved execution in progress');
        if(request.execution_receipt)return request.execution_receipt;
        request.execution_receipt=result;lib.set(r,'request',JSON.stringify(request));lib.save(r);return result;
    }
    function execute(body) {
        const a=stage('check approved draft',()=>approved(body));if(a.receipt)return {result:a.receipt};
        if(a.draft.operation!=='record')lib.fail('Not a business record draft');
        const built=stage('rebuild unsaved approved draft (save not attempted)',()=>build(a.draft.record_type,a.draft.payload));
        if(built.issues.length)return {result:{kind:'clarify',message:built.issues.join('\n')}};
        if(JSON.stringify(built.payload)!==JSON.stringify(a.draft.payload))
            return {result:{kind:'clarify',message:'Resolved field values changed since review. Please ask for a new draft and review it again.'}};
        const drift = previewDrift(a.draft.preview, built.preview, a.draft.payload);
        if(drift)
            return {result:{kind:'clarify',message:'NetSuite defaults or calculated values changed since review ('+drift+'). Please ask for a new draft and review it again.'}};
        stage('persist execution marker (save not attempted)',()=>begin(body));
        // Never retry save: a user-event failure can occur after a record has been committed.
        let id;
        try { id=String(built.record.save({enableSourcing:true,ignoreMandatoryFields:false})); }
        catch(e) {return {result:receipt(body,{kind:'uncertain',message:'NetSuite save did not report success: '+String(e.message).slice(0,800)+'. A save-time script may have partially completed. Verify in NetSuite before submitting again.'})};}
        let link='';try{link=url.resolveRecord({recordType:a.draft.record_type,recordId:id,isEditMode:false});}catch(e){}
        const result={kind:'created',operation:'record',record_type:a.draft.record_type,id,link,message:`Created ${a.draft.record_type} (internal ID ${id}). Open the record to verify final values after NetSuite workflows.`};
        return {result:receipt(body,result)};
    }
    function handle(body) {
        customTypeCache = null;
        running(body);
        switch(body.action) {
            case 'creation_capabilities':return {capabilities:{standard:STANDARD,custom:customTypes(),sublists:SUBLISTS}};
            case 'creation_inspect':return inspect(body.record_type);
            case 'creation_prepare': {
                const b=build(body.record_type,body.payload);
                if(b.issues.length)return {issues:b.issues};
                const draft=makeDraft('record',{record_type:body.record_type,payload:b.payload,preview:b.preview});
                if(JSON.stringify(draft).length>85000)lib.fail('Draft too large; reduce the number of fields or lines');
                return {result:draft};
            }
            case 'creation_type_available':available(body.script_id);return {available:true};
            case 'creation_type_draft': {
                available(body.specification.script_id);
                assertFieldsAvailable(body.specification);
                if(!/^[a-f0-9]{64}$/.test(body.artifact_hash)||typeof body.xml!=='string'||body.xml.length>50000)lib.fail('Invalid customization artifact');
                const entryRoles = Array.isArray(body.entry_roles)
                    ? body.entry_roles.map(r => String(r || '').trim().toLowerCase())
                        .filter(r => /^customrole_[a-z][a-z0-9_]*$/.test(r)).slice(0, 8)
                    : [];
                const permissionLabel = entryRoles.length
                    ? ('Administrator FULL; integration role FULL (' + entryRoles.join(', ') + ')')
                    : 'Administrator FULL only';
                return {result:makeDraft('custom_type',{
                    specification:body.specification,artifact_hash:body.artifact_hash,xml:body.xml,
                    entry_roles:entryRoles,
                    preview:{...body.specification,permissions:permissionLabel,entry_roles:entryRoles,
                        validation:'Local artifact prepared; SuiteCloud server validation runs after approval'}})};
            }
            case 'creation_approved': {const a=approved(body);return a.receipt?{receipt:a.receipt}:{draft:a.draft};}
            case 'creation_execute':return execute(body);
            case 'creation_begin': {const a=approved(body);if(a.draft?.operation!=='custom_type')lib.fail('Not a customization draft');begin(body);return {started:true};}
            case 'creation_receipt': {
                const r=lib.held(body),draft=lib.json(r,'result',{});
                if(draft.operation!=='custom_type'||body.result?.kind!=='created'||body.result.script_id!==draft.specification.script_id)lib.fail('Invalid customization receipt');
                return {result:receipt(body,body.result)};
            }
            default:lib.fail('Unknown creation operation');
        }
    }
    return {handle};
});
