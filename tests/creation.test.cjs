const test=require('node:test');
const assert=require('node:assert/strict');
const {sandbox}=require('./netsuite-harness.cjs');
function pending(h){const csrf=h.csrf();const id=h.ui({action:'ask',question:'Create a customer',mode:'create',csrf}).body.id;return {csrf,id,job:h.api('claim').job};}
function draft(h,p,type='customer',payload={fields:{companyname:'Acme'}}){const result=h.api('creation_prepare',{job:p.id,lease:p.job.lease,record_type:type,payload});assert.equal(result.ok,true,JSON.stringify(result));assert.ok(result.result);h.api('complete',{job:p.id,lease:p.job.lease,result:result.result});return result.result;}
function approve(h,p,d){h.admin();const result=h.ui({action:'approve',id:p.id,nonce:d.nonce,csrf:p.csrf}).body;assert.equal(result.ok,true,JSON.stringify(result));return h.api('claim').job;}

test('preview saves nothing, explicit approval executes exactly once even if the response is lost',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p);assert.equal(h.state.businessSaves.length,0);
 assert.equal(h.api('creation_execute',{job:p.id,lease:p.job.lease}).ok,false);
 const j=approve(h,p,d),result=h.api('creation_execute',{job:j.id,lease:j.lease});
 assert.equal(result.ok,true);assert.equal(result.result.kind,'created');assert.equal(result.result.id,'500');
 assert.equal(h.api('creation_execute',{job:j.id,lease:j.lease}).result.id,'500');
 assert.equal(h.state.businessSaves.length,1);
});
test('existing custom record entry uses its type and mandatory name',()=>{
 const h=sandbox(),p=pending(h);const d=draft(h,p,'customrecord_membership',{fields:{name:'Member A'}});
 const j=approve(h,p,d);assert.equal(h.api('creation_execute',{job:j.id,lease:j.lease}).result.kind,'created');
 assert.equal(h.state.businessSaves[0].type,'customrecord_membership');
});
test('missing fields return clarification; unknown types, reserved types and login changes fail closed',()=>{
 const h=sandbox(),p=pending(h);
 assert.ok(h.api('creation_prepare',{job:p.id,lease:p.job.lease,record_type:'customer',payload:{fields:{}}}).issues.some(s=>s.includes('companyname')));
 for(const type of ['customrecord_nsa_job','picktask','madeup'])assert.equal(h.api('creation_inspect',{job:p.id,lease:p.job.lease,record_type:type}).ok,false);
 assert.equal(h.api('creation_prepare',{job:p.id,lease:p.job.lease,record_type:'employee',payload:{fields:{giveaccess:true}}}).ok,false);
 assert.equal(h.state.businessSaves.length,0);
});
test('sales order references must resolve uniquely and item lines are required',()=>{
 const h=sandbox(),p=pending(h),payload={fields:{entity:{text:'TEST US Customer'}},sublists:{item:[{item:{id:'55'},quantity:2}]}};
 const d=draft(h,p,'salesorder',payload);assert.equal(d.payload.fields.entity.id,'55');
 assert.equal(d.preview.sublists.item[0].quantity.value,2);
 assert.equal(h.state.businessSaves.length,0);
 const h2=sandbox(),p2=pending(h2);h2.state.references.push({id:'56',name:'TEST US Customer'});
 const failed=h2.api('creation_prepare',{job:p2.id,lease:p2.job.lease,record_type:'salesorder',payload});
 assert.equal(failed.ok,false);assert.match(failed.error.message,/multiple matches/);
});
test('item display names and subsidiary labels resolve without requiring the primary ID field',()=>{
 const h=sandbox();
 h.state.references=[{id:'10',name:'TEST US Customer',companyname:'TEST US Customer',entityid:'CUST10'},
  {id:'1',name:'Parent : Honeycomb Mfg',namenohierarchy:'Honeycomb Mfg'},
  {id:'1334',itemid:'F3-PL-HW',displayname:'F3 PL Hardware',name:'F3 PL Hardware'}];
 const p=pending(h);
 const payload={fields:{entity:{text:'TEST US Customer'},subsidiary:{text:'Honeycomb Mfg.'}},
  sublists:{item:[{item:{text:'F3 PL Hardware'},quantity:2}]}};
 const d=draft(h,p,'salesorder',payload);
 assert.equal(d.payload.fields.entity.id,'10');
 assert.equal(d.payload.fields.subsidiary.id,'1');
 assert.equal(d.payload.sublists.item[0].item.id,'1334');
 assert.equal(h.state.businessSaves.length,0);
});
test('forged nonce, expired draft, wrong owner, and disabled writes cannot execute',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p);h.admin();
 assert.equal(h.ui({action:'approve',id:p.id,nonce:'fake',csrf:p.csrf}).body.ok,false);
 h.state.user=99;const csrf=h.ui({},'GET').body.match(/name="csrf-token" content="([^"]+)"/)[1];
 assert.equal(h.ui({action:'approve',id:p.id,nonce:d.nonce,csrf}).body.ok,false);
 h.admin();const row=h.records.get(p.id).values,expired=JSON.parse(row.custrecord_nsa_result);expired.expires_at=0;row.custrecord_nsa_result=JSON.stringify(expired);
 assert.equal(h.ui({action:'approve',id:p.id,nonce:d.nonce,csrf:p.csrf}).body.ok,false);
 h.params.custscript_nsa_creation_enabled=false;
 assert.equal(h.api('creation_capabilities',{job:p.id,lease:p.job.lease}).ok,false);
 assert.equal(h.state.businessSaves.length,0);
});
test('follow-up correction invalidates the old draft',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p);h.admin();
 assert.equal(h.ui({action:'ask',question:'Change the name',parent:p.id,csrf:p.csrf}).body.ok,true);
 assert.equal(h.ui({action:'approve',id:p.id,nonce:d.nonce,csrf:p.csrf}).body.ok,false);
});
test('changed sourced values require a new review',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p);const j=approve(h,p,d);h.state.defaultCurrency='2';
 const result=h.api('creation_execute',{job:j.id,lease:j.lease});assert.equal(result.result.kind,'clarify');
 assert.match(result.result.message,/currency/i);
 assert.equal(h.state.businessSaves.length,0);
});
test('display-label and calculated-total drift do not block an otherwise identical approval',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p,'salesorder',{
  fields:{entity:{id:'55'},memo:'AO-TEST-001'},
  sublists:{item:[{item:{id:'55'},quantity:2}]}});
 // Simulate NetSuite returning different display text and totals on the second build.
 d.preview.fields.entity.text='Old Label';
 d.preview.fields.total={label:'Total',value:99,text:'99.00'};
 d.preview.sublists.item[0].amount={label:'Amount',value:50,text:'50.00'};
 d.preview.sublists.item[0].item.text='Stale Item Name';
 const row=h.records.get(p.id).values;row.custrecord_nsa_result=JSON.stringify(d);
 const j=approve(h,p,d);
 const result=h.api('creation_execute',{job:j.id,lease:j.lease});
 assert.equal(result.result.kind,'created',JSON.stringify(result));
 assert.equal(h.state.businessSaves.length,1);
});
test('save-time uncertainty is persisted and never retried',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p),j=approve(h,p,d);h.state.businessSaveError=true;
 assert.equal(h.api('creation_execute',{job:j.id,lease:j.lease}).result.kind,'uncertain');
 assert.equal(h.api('creation_execute',{job:j.id,lease:j.lease}).result.kind,'uncertain');
 assert.equal(h.state.businessSaves.length,1);
});
test('SDF execution requires approval; durable start prevents replay after lost worker',()=>{
 const h=sandbox(),p=pending(h);const d=h.api('creation_type_draft',{job:p.id,lease:p.job.lease,specification:{script_id:'customrecord_inspection',name:'Inspection'},xml:'<customrecordtype/>',artifact_hash:'a'.repeat(64)}).result;
 h.api('complete',{job:p.id,lease:p.job.lease,result:d});const j=approve(h,p,d);
 assert.equal(h.api('creation_begin',{job:j.id,lease:j.lease}).ok,true);
 assert.equal(h.api('creation_begin',{job:j.id,lease:j.lease}).ok,false);
 h.admin();assert.equal(h.ui({action:'cancel',id:j.id,csrf:p.csrf}).body.ok,false);
 assert.equal(h.api('creation_receipt',{job:j.id,lease:j.lease,result:{kind:'created',script_id:'customrecord_inspection'}}).ok,true);
 assert.equal(h.api('creation_approved',{job:j.id,lease:j.lease}).receipt.kind,'created');
});
test('draft rejection blocks subsequent approval',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p);h.admin();
 assert.equal(h.ui({action:'reject',id:p.id,csrf:p.csrf}).body.ok,true);
 assert.equal(h.ui({action:'approve',id:p.id,nonce:d.nonce,csrf:p.csrf}).body.ok,false);
});

test('non-numeric select option IDs survive preview and execution',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p,'subsidiary',{fields:{country:{text:'Pakistan'}}});
 assert.equal(d.payload.fields.country.id,'PK');const j=approve(h,p,d);
 assert.equal(h.api('creation_execute',{job:j.id,lease:j.lease}).result.kind,'created');
 assert.equal(h.state.businessSaves[0].data.country,'PK');
});

test('server field inspection avoids client-only accessors for body and item fields',()=>{
 const h=sandbox(),p=pending(h);
 const result=h.api('creation_inspect',{job:p.id,lease:p.job.lease,record_type:'salesorder'});
 assert.equal(result.ok,true,JSON.stringify(result));
 assert.ok(result.fields.some(f=>f.id==='entity'));
 assert.ok(result.sublists.item.some(f=>f.id==='quantity'));
 assert.ok(!result.fields.some(f=>f.id==='giveaccess'));
 assert.equal(h.state.businessSaves.length,0);
});

test('custom entry owner popup resolves an employee without saving',()=>{
 const h=sandbox(),p=pending(h);
 h.state.references.push({id:'2447',name:'Shaheer Badar'});
 const d=draft(h,p,'customrecord_membership',{fields:{name:'Inspection',owner:{text:'Shaheer Badar'}}});
 assert.equal(d.payload.fields.owner.id,'2447');
 assert.equal(h.state.businessSaves.length,0);
});

test('approval rebuild errors identify the field and confirm save was not attempted',()=>{
 const h=sandbox(),p=pending(h),d=draft(h,p);
 const j=approve(h,p,d);
 h.state.businessAssignError='companyname';
 const failed=h.api('creation_execute',{job:j.id,lease:j.lease});
 assert.equal(failed.ok,false);
 assert.match(failed.error.message,/save not attempted/);
 assert.match(failed.error.message,/assign field companyname/);
 assert.match(failed.error.message,/UNEXPECTED_ERROR/);
 assert.equal(h.state.businessSaves.length,0);
});

test('owner name draft executes via direct employee lookup despite broken ID search',()=>{
 const h=sandbox(),p=pending(h);
 h.state.references.push({id:'2447',name:'Shaheer Badar'});
 h.state.employeeIdSearchError=true;
 const d=draft(h,p,'customrecord_membership',{fields:{name:'Inspection',owner:{text:'Shaheer Badar'}}});
 const j=approve(h,p,d);
 const result=h.api('creation_execute',{job:j.id,lease:j.lease});
 assert.equal(result.ok,true,JSON.stringify(result));
 assert.equal(result.result.kind,'created');
 assert.equal(h.state.businessSaves[0].data.owner,'2447');
});

test('owner approval survives lookupFields UNEXPECTED_ERROR via SuiteQL fallback',()=>{
 const h=sandbox(),p=pending(h);
 h.state.references.push({id:'2447',name:'Shaheer Badar'});
 const d=draft(h,p,'customrecord_membership',{fields:{name:'Inspection',owner:{text:'Shaheer Badar'}}});
 const j=approve(h,p,d);
 h.state.employeeLookupError={name:'UNEXPECTED_ERROR',message:''};
 h.state.employeeIdSearchError=true;
 const result=h.api('creation_execute',{job:j.id,lease:j.lease});
 assert.equal(result.ok,true,JSON.stringify(result));
 assert.equal(result.result.kind,'created');
 assert.equal(h.state.businessSaves[0].data.owner,'2447');
});

test('owner approval reuses reviewed employee ID when employee reads are fully denied',()=>{
 const h=sandbox(),p=pending(h);
 h.state.references.push({id:'2447',name:'Shaheer Badar'});
 const d=draft(h,p,'customrecord_membership',{fields:{name:'Inspection',owner:{text:'Shaheer Badar'}}});
 assert.equal(d.payload.fields.owner.id,'2447');
 assert.equal(d.payload.fields.owner.text,'Shaheer Badar');
 const j=approve(h,p,d);
 h.state.employeeLookupError={name:'UNEXPECTED_ERROR',message:''};
 h.state.employeeIdSearchError=true;
 h.state.employeeSuiteQlError={name:'UNEXPECTED_ERROR',message:''};
 h.state.references.length=0;
 const result=h.api('creation_execute',{job:j.id,lease:j.lease});
 assert.equal(result.ok,true,JSON.stringify(result));
 assert.equal(result.result.kind,'created');
 assert.equal(h.state.businessSaves[0].data.owner,'2447');
});

test('invalid owner ID still fails on assign when employee reads are denied',()=>{
 const h=sandbox(),p=pending(h);
 h.state.references.push({id:'2447',name:'Shaheer Badar'});
 const d=draft(h,p,'customrecord_membership',{fields:{name:'Inspection',owner:{text:'Shaheer Badar'}}});
 const row=h.records.get(p.id).values;
 const stored=JSON.parse(row.custrecord_nsa_result);
 stored.payload.fields.owner={id:'999999',text:'Missing Person'};
 row.custrecord_nsa_result=JSON.stringify(stored);
 const j=approve(h,p,stored);
 h.state.employeeLookupError={name:'UNEXPECTED_ERROR',message:''};
 h.state.employeeIdSearchError=true;
 h.state.employeeSuiteQlError={name:'UNEXPECTED_ERROR',message:''};
 h.state.references.length=0;
 h.state.businessAssignError='owner';
 const result=h.api('creation_execute',{job:j.id,lease:j.lease});
 assert.equal(result.ok,false);
 assert.match(result.error.message,/assign field owner|save not attempted/);
 assert.equal(h.state.businessSaves.length,0);
});
