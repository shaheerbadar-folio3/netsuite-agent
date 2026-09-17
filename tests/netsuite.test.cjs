const test=require('node:test');
const assert=require('node:assert/strict');
const {sandbox}=require('./netsuite-harness.cjs');

test('admin question -> claim -> query -> completion -> UI result',()=>{
 const h=sandbox(),csrf=h.csrf();
 const submitted=h.ui({action:'ask',question:'Show customers',csrf}).body;
 assert.equal(submitted.ok,true);
 const claim=h.api('claim');assert.equal(claim.job.id,submitted.id);
 const query=h.api('query',{job:claim.job.id,lease:claim.job.lease,sql:'SELECT id, companyname FROM customer ORDER BY id',page:0,record_links:[{column:'id',record_type:'customer'}]});
 assert.equal(query.ok,true);assert.equal(query.result.rows[0].id,7);assert.match(query.result.links[0].id,/id=7/);
 assert.equal(h.api('complete',{job:claim.job.id,lease:claim.job.lease,result:{kind:'result',...query.result}}).ok,true);
 h.admin();const status=h.ui({action:'status',id:claim.job.id,csrf}).body;
 assert.equal(status.state,'done');assert.equal(status.result.rows.length,1);
});

test('non-admin and invalid CSRF cannot enqueue',()=>{
 const h=sandbox(),csrf=h.csrf();h.state.role=1002;
 assert.equal(h.ui({action:'ask',question:'test',csrf}).body.ok,false);
 h.admin();assert.equal(h.ui({action:'ask',question:'test',csrf:'wrong'}).body.ok,false);
 assert.equal(h.records.size,0);
});

test('different admin cannot read another administrator question',()=>{
 const h=sandbox(),csrf=h.csrf();const {id}=h.ui({action:'ask',question:'test',csrf}).body;
 h.state.user=99;const page=h.ui({},'GET').body;const other=page.match(/name="csrf-token" content="([^"]+)"/)[1];
 assert.equal(h.ui({action:'status',id,csrf:other}).body.ok,false);
});

test('wrong integration identity is denied',()=>{
 const h=sandbox();h.params.custscript_nsa_worker_user=99;
 assert.equal(h.api('ping').ok,false);
});

test('lease loss and business mutations are rejected',()=>{
 const h=sandbox(),csrf=h.csrf();const {id}=h.ui({action:'ask',question:'test',csrf}).body;
 const {job}=h.api('claim');
 assert.equal(h.api('query',{job:id,lease:'wrong',sql:'SELECT id FROM customer ORDER BY id',page:0}).ok,false);
 assert.equal(h.api('query',{job:id,lease:job.lease,sql:'DELETE FROM customer',page:0}).ok,false);
 assert.equal(h.state.queries.length,0);
});

test('cancelled question cannot execute or publish results',()=>{
 const h=sandbox(),csrf=h.csrf();const {id}=h.ui({action:'ask',question:'test',csrf}).body;
 const {job}=h.api('claim');h.admin();h.ui({action:'cancel',id,csrf});
 assert.equal(h.api('heartbeat',{job:id,lease:job.lease}).cancelled,true);
 assert.equal(h.api('query',{job:id,lease:job.lease,sql:'SELECT id FROM customer ORDER BY id',page:0}).ok,false);
 assert.equal(h.api('complete',{job:id,lease:job.lease,result:{kind:'result'}}).cancelled,true);
});

test('expired lease is reclaimed with a new token',()=>{
 const h=sandbox(),csrf=h.csrf();const {id}=h.ui({action:'ask',question:'test',csrf}).body;
 const first=h.api('claim').job;
 h.records.get(id).values.custrecord_nsa_until=Date.now()-1;
 const second=h.api('claim').job;
 assert.equal(second.id,first.id);assert.notEqual(second.lease,first.lease);
 assert.equal(h.api('complete',{job:id,lease:first.lease,result:{kind:'error'}}).ok,false);
});

test('schema includes custom records and supports empty-table projection fallback',()=>{
 const h=sandbox();const inventory=h.api('schema_inventory');
 assert.ok(inventory.tables.includes('customrecord_membership'));
 h.state.noColumns=true;
 const result=h.api('schema_probe',{tables:['customer']});
 assert.equal(result.tables[0].fields[0].name,'id');assert.equal(result.failures.length,0);
 assert.equal(h.api('schema_probe',{tables:['customer; DROP TABLE customer']}).ok,false);
});

test('cleanup deletes expired jobs only',()=>{
 const h=sandbox(),csrf=h.csrf();const a=h.ui({action:'ask',question:'old',csrf}).body.id;
 const b=h.ui({action:'ask',question:'new',csrf}).body.id;
 h.records.get(a).values.custrecord_nsa_created=Date.now()-9*86400000;
 h.cleanup.execute();assert.equal(h.records.has(a),false);assert.equal(h.records.has(b),true);
});

 test('query failure identifies execution stage and NetSuite error code',()=>{
 const h=sandbox(),csrf=h.csrf();h.ui({action:'ask',question:'Count customers',csrf});
 const {job}=h.api('claim');
 h.state.queryError=Object.assign(new Error('An unexpected SuiteScript error has occurred'),{name:'SSS_INVALID_TYPE_ARG'});
 const result=h.api('query',{job:job.id,lease:job.lease,sql:'SELECT COUNT(*) AS total FROM customer ORDER BY total',page:0});
 assert.equal(result.ok,false);
 assert.equal(result.error.code,'SSS_INVALID_TYPE_ARG');
 assert.match(result.error.message,/create paged query \[SSS_INVALID_TYPE_ARG\]/);
});

for (const count of [1, 101, 5000]) {
 test('standard query fallback completeness boundary '+count,()=>{
  const h=sandbox(),csrf=h.csrf();h.ui({action:'ask',question:'Count customers',csrf});
  const {job}=h.api('claim');
  h.state.queryError=Object.assign(new Error('Unexpected'),{name:'UNEXPECTED_ERROR'});
  h.state.standardRows=Array.from({length:count},(_,id)=>({id,total_customers:123}));
  const payload={job:job.id,lease:job.lease,sql:'SELECT COUNT(*) AS total_customers FROM customer ORDER BY total_customers',page:0};
  const result=h.api('query',payload);
  if(count===5000){assert.equal(result.ok,false);assert.match(result.error.message,/completeness cannot be verified/);}
  else {assert.equal(result.ok,true);assert.equal(result.result.total,count);assert.equal(result.result.rows.length,Math.min(100,count));
   if(count===101){const next=h.api('query',{...payload,page:1});assert.equal(next.result.rows.length,1);assert.equal(next.result.rows[0].id,100);}
  }
 });
}

test('fixed query diagnostics require the integration identity and ignore caller SQL',()=>{
 const h=sandbox();
 const result=h.api('query_diagnostics',{sql:'DELETE FROM customer'});
 assert.equal(result.ok,true);assert.equal(result.checks.length,4);
 assert.ok(result.checks.every(c=>c.sql.startsWith('SELECT ')));
 h.params.custscript_nsa_worker_user=99;
 assert.equal(h.api('query_diagnostics').ok,false);
});
