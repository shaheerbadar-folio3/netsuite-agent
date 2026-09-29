const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const base = path.resolve(__dirname, '../netsuite/FileCabinet/SuiteScripts/netsuite-agent');

function sandbox() {
  const records = new Map(), sessions = new Map(), cacheData = new Map();
  let next = 1;
  const state = {user: 10, role: 3, businessSaves: [], defaultCurrency: '1', references: [{id:'55',name:'TEST US Customer'}], queries: [], noColumns: false, rows: [{id: 7, companyname: '<script>alert(1)</script>'}]};
  const params = {custscript_nsa_worker_role: 1001, custscript_nsa_worker_user: 20,
    custscript_nsa_creation_enabled: true, custscript_nsa_worker_name: 'local-primary', custscript_nsa_retention_days: 7};
  function wrap(id, values, version) {
    const data = structuredClone(values);
    return {getValue: ({fieldId}) => data[fieldId], setValue: ({fieldId,value}) => {data[fieldId]=value;},
      save() {if(id && records.get(String(id))?.version!==version){const e=Error('Conflict');e.name='RCRD_HAS_BEEN_CHANGED';throw e;}
        id=String(id||next++);records.set(id,{values:structuredClone(data),version:(version||0)+1});return id;}};
  }
  function matches(values, filter) {
    if(!filter?.length)return true;
    if(typeof filter[0]==='string' && !Array.isArray(filter[1])) {
      const [field,op,wanted]=filter, actual=values[field];
      if(op==='is')return String(actual)===String(wanted);
      if(op==='equalto')return Number(actual)===Number(wanted);
      if(op==='lessthan')return Number(actual)<Number(wanted);
      throw Error('Unknown search operator '+op);
    }
    let value=matches(values,filter[0]);
    for(let i=1;i<filter.length;i+=2)value=filter[i]==='AND' ? value&&matches(values,filter[i+1]) : value||matches(values,filter[i+1]);
    return value;
  }
  function business(type) {
    const data={currency:state.defaultCurrency}, lines=[], current={};
    const definitions={name:{type:'text',isMandatory:type.startsWith('customrecord_')},
      companyname:{type:'text',isMandatory:type==='customer'},memo:{type:'text'},owner:{type:'select'},
      entity:{type:'select',isMandatory:type==='salesorder'},subsidiary:{type:'select'},currency:{type:'select'},
      country:{type:'select'},trandate:{type:'date'},giveaccess:{type:'checkbox'},quantity:{type:'float'},
      item:{type:'select',isMandatory:true},rate:{type:'currency'},amount:{type:'currency'}};
    function field(id){const f=definitions[id];return f?{id,label:id,...f,
      get isReadOnly(){throw new Error('Invalid invocation method: isReadOnly');},
      get isDisabled(){throw new Error('Invalid invocation method: isDisabled');},
      getSelectOptions:()=>id==='country'?[{value:'PK',text:'Pakistan'}]:id==='currency'?[{value:'1',text:'USD'},{value:'2',text:'EUR'}]:null}:null;}
    return {getFields:()=>['companyname','name','memo','entity','subsidiary','currency','country','trandate','giveaccess'],
      getField:({fieldId})=>field(fieldId),getValue:({fieldId})=>data[fieldId],
      getText:({fieldId})=>fieldId==='currency'?(data.currency==='1'?'USD':'EUR'):String(data[fieldId]||''),
      setValue:({fieldId,value})=>{if(state.businessAssignError===fieldId)throw {name:'UNEXPECTED_ERROR'};data[fieldId]=value;},
      getSublists:()=>type==='salesorder'?['item']:[],getSublistFields:()=>['item','quantity','rate','amount'],
      getCurrentSublistField:({fieldId})=>field(fieldId),
      selectNewLine:()=>{for(const k of Object.keys(current))delete current[k];},
      setCurrentSublistValue:({fieldId,value})=>{current[fieldId]=value;},
      getCurrentSublistValue:({fieldId})=>current[fieldId],getCurrentSublistText:({fieldId})=>String(current[fieldId]||''),
      commitLine:()=>lines.push({...current}),cancelLine:()=>{},getLineCount:()=>lines.length,
      save:()=>{state.businessSaves.push({type,data:{...data},lines:[...lines]});if(state.businessSaveError)throw Error('Save-time error');return '500';}};
  }
  const modules = {
    'N/runtime': {getCurrentUser:()=>({id:state.user,role:state.role,getPreference:()=> 'Asia/Karachi'}),
      getCurrentSession:()=>({get:({name})=>sessions.get(state.user+':'+name),set:({name,value})=>sessions.set(state.user+':'+name,value)}),
      getCurrentScript:()=>({getParameter:({name})=>params[name],getRemainingUsage:()=>10000})},
    'N/crypto/random':{generateUUID:randomUUID},
    'N/record':{create:({type})=>type==='customrecord_nsa_job'?wrap(null,{},0):business(type),load:({id})=>{const r=records.get(String(id));if(!r)throw Error('Record missing');return wrap(String(id),r.values,r.version);},delete:({id})=>records.delete(String(id))},
    'N/search':{Sort:{ASC:'ASC'},createColumn:x=>x,lookupFields:({type,id})=>{
        if(state.employeeLookupError)throw state.employeeLookupError;
        const r=state.references.find(r=>String(r.id)===String(id));
        if(!r)throw Error('Employee not found');
        return {entityid:r.entityid||r.name};
      },create:({type,filters,columns})=>type!=='customrecord_nsa_job'?({run:()=>({getRange:()=>{
        const clause=Array.isArray(filters?.[0])?filters[0]:filters;
        const [field,op,wanted]=clause;
        if(type==='employee'&&field==='internalid'&&state.employeeIdSearchError)throw {name:'UNEXPECTED_ERROR'};
        return state.references.filter(r=>{
          if(field==='internalid'||op==='anyof')return String(r.id)===String(wanted);
          const actual=r[field]??r.name??r.itemid??r.displayname??r.companyname??r.namenohierarchy??'';
          if(op==='is')return String(actual)===String(wanted);
          if(op==='contains')return String(actual).toLowerCase().includes(String(wanted).toLowerCase());
          return false;
        }).map(r=>({id:r.id,getValue:({name})=>r[name]??r.name??r.itemid??r.displayname??r.companyname??r.namenohierarchy}));
      }})}):({run:()=>({getRange:({start,end})=>[...records.entries()].filter(([,r])=>matches(r.values,filters)).map(([id])=>({id})).slice(start,end)})})},
    'N/cache':{Scope:{PUBLIC:'PUBLIC'},getCache:()=>({put:({key,value})=>cacheData.set(key,value),get:({key})=>cacheData.get(key)})},
    'N/url':{resolveRecord:({recordType,recordId})=>'/app/common/entity/'+recordType+'.nl?id='+recordId},
    'N/file':{load:()=>({getContents:()=>fs.readFileSync(path.join(base,'chat.html'),'utf8')})},
    'N/query':{
      Type:{CUSTOMER:'customer',TRANSACTION:'transaction'},
      runSuiteQL:({query})=>{
        if(state.standardQueryError)throw state.standardQueryError;
        if(state.employeeSuiteQlError && /FROM employee/i.test(query))throw state.employeeSuiteQlError;
        if(state.standardRows)return {asMappedResults:()=>state.standardRows};
        if(query.includes('CustomRecordType')){
          const wanted=query.match(/WHERE\s+(?:LOWER\()?ScriptID\)?\s*=\s*'([^']+)'/i);
          if(wanted){
            const id=wanted[1].toLowerCase();
            const hit=id==='customrecord_membership';
            return {asMappedResults:()=>hit?[{scriptid:'CUSTOMRECORD_MEMBERSHIP'}]:[]};
          }
          return {asMappedResults:()=>[{scriptid:'customrecord_membership'}]};
        }
        const employeeId=query.match(/FROM employee WHERE id = (\d+)/i);
        if(employeeId){
          const row=state.references.find(r=>String(r.id)===employeeId[1]);
          return {asMappedResults:()=>row?[{id:row.id,entityid:row.entityid||row.name}]:[]};
        }
        if(query.includes('RIGHT OUTER JOIN'))return {asMappedResults:()=>[{id:null,companyname:null}]};
        return {columns:state.noColumns?[]:[{fieldId:'id'},{fieldId:'companyname'}],types:['INTEGER','STRING'],asMappedResults:()=>[]};
      },
      runSuiteQLPaged:({query})=>{if(state.queryError)throw state.queryError;state.queries.push(query);return {count:state.rows.length,pageRanges:state.rows.length?[{index:0}]:[],fetch:()=>({data:{asMappedResults:()=>state.rows}})};}
    }
  };
  function load(filename) {
    let exports;
    vm.runInNewContext(fs.readFileSync(path.join(base,filename),'utf8'), {
      define:(deps,factory)=>{exports=factory(...deps.map(n=>n==='./lib'?lib:n==='./creation'?creation:modules[n]));},
      Date,Set,Map,JSON,Number,String,Object,Array,Error
    },{filename});
    return exports;
  }
  const lib=load('lib.js');
  const creation=load('creation.js');
  const restlet=load('worker_restlet.js'),suitelet=load('suitelet.js'),cleanup=load('cleanup.js');
  function ui(body,method='POST'){
    let output='';const headers={};suitelet.onRequest({request:{method,body:JSON.stringify(body)},response:{setHeader:({name,value})=>headers[name]=value,write:x=>output+=x}});
    return {body:method==='GET'?output:JSON.parse(output),headers};
  }
  function api(action,payload={}){state.user=20;state.role=1001;return restlet.post({action,worker:'local-primary',...payload});}
  function admin(){state.user=10;state.role=3;}
  function csrf(){admin();return ui({},'GET').body.match(/name="csrf-token" content="([^"]+)"/)[1];}
  return {state,records,params,ui,api,admin,csrf,cleanup,lib};
}
module.exports={sandbox};
