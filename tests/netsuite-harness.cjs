const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const base = path.resolve(__dirname, '../netsuite/FileCabinet/SuiteScripts/netsuite-agent');

function sandbox() {
  const records = new Map(), sessions = new Map(), cacheData = new Map();
  let next = 1;
  const state = {user: 10, role: 3, queries: [], noColumns: false, rows: [{id: 7, companyname: '<script>alert(1)</script>'}]};
  const params = {custscript_nsa_worker_role: 1001, custscript_nsa_worker_user: 20,
    custscript_nsa_worker_name: 'local-primary', custscript_nsa_retention_days: 7};
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
  const modules = {
    'N/runtime': {getCurrentUser:()=>({id:state.user,role:state.role,getPreference:()=> 'Asia/Karachi'}),
      getCurrentSession:()=>({get:({name})=>sessions.get(state.user+':'+name),set:({name,value})=>sessions.set(state.user+':'+name,value)}),
      getCurrentScript:()=>({getParameter:({name})=>params[name],getRemainingUsage:()=>10000})},
    'N/crypto/random':{generateUUID:randomUUID},
    'N/record':{create:()=>wrap(null,{},0),load:({id})=>{const r=records.get(String(id));if(!r)throw Error('Record missing');return wrap(String(id),r.values,r.version);},delete:({id})=>records.delete(String(id))},
    'N/search':{Sort:{ASC:'ASC'},createColumn:x=>x,create:({filters})=>({run:()=>({getRange:({start,end})=>[...records.entries()].filter(([,r])=>matches(r.values,filters)).map(([id])=>({id})).slice(start,end)})})},
    'N/cache':{Scope:{PUBLIC:'PUBLIC'},getCache:()=>({put:({key,value})=>cacheData.set(key,value),get:({key})=>cacheData.get(key)})},
    'N/url':{resolveRecord:({recordType,recordId})=>'/app/common/entity/'+recordType+'.nl?id='+recordId},
    'N/file':{load:()=>({getContents:()=>fs.readFileSync(path.join(base,'chat.html'),'utf8')})},
    'N/query':{
      Type:{CUSTOMER:'customer',TRANSACTION:'transaction'},
      runSuiteQL:({query})=>{
        if(state.standardQueryError)throw state.standardQueryError;
        if(state.standardRows)return {asMappedResults:()=>state.standardRows};
        if(query.includes('CustomRecordType'))return {asMappedResults:()=>[{scriptid:'customrecord_membership'}]};
        if(query.includes('RIGHT OUTER JOIN'))return {asMappedResults:()=>[{id:null,companyname:null}]};
        return {columns:state.noColumns?[]:[{fieldId:'id'},{fieldId:'companyname'}],types:['INTEGER','STRING'],asMappedResults:()=>[]};
      },
      runSuiteQLPaged:({query})=>{if(state.queryError)throw state.queryError;state.queries.push(query);return {count:state.rows.length,pageRanges:state.rows.length?[{index:0}]:[],fetch:()=>({data:{asMappedResults:()=>state.rows}})};}
    }
  };
  function load(filename) {
    let exports;
    vm.runInNewContext(fs.readFileSync(path.join(base,filename),'utf8'), {
      define:(deps,factory)=>{exports=factory(...deps.map(n=>n==='./lib'?lib:modules[n]));},
      Date,Set,Map,JSON,Number,String,Object,Array,Error
    },{filename});
    return exports;
  }
  const lib=load('lib.js');
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
