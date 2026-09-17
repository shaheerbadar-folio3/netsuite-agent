// Local browser QA only. Uses the real Suitelet and RESTlet in an in-memory NetSuite harness.
// Never use as an application server or point it at real credentials/data.
const http=require('node:http');
const {sandbox}=require('../tests/netsuite-harness.cjs');
const h=sandbox();
const server=http.createServer((req,res)=>{
 let body='';req.on('data',chunk=>{body+=chunk;if(body.length>20000)req.destroy();});
 req.on('end',()=>{try{
  h.admin();const data=h.ui(body?JSON.parse(body):{},req.method);
  if(req.method==='GET')data.body=data.body.replace('ADMINISTRATOR · READ-ONLY ANALYTICS','LOCAL TEST FIXTURE · SYNTHETIC DATA');
  res.writeHead(200,data.headers);res.end(typeof data.body==='string'?data.body:JSON.stringify(data.body));
  if(req.method==='POST' && ['ask','page'].includes(JSON.parse(body).action))setTimeout(()=>{
   const {job}=h.api('claim');if(!job)return;
   const sql='SELECT id, companyname FROM customer ORDER BY id';
   const result=h.api('query',{job:job.id,lease:job.lease,sql,page:0,record_links:[{column:'id',record_type:'customer'}]}).result;
   h.api('complete',{job:job.id,lease:job.lease,result:{kind:'result',...result,sql,interpretation:'All customers (synthetic UI test).',definitions:['Customers means customer records'],schema_version:'fixture',document_version:'fixture',queried_at:new Date().toISOString()}});
  },500);
 }catch(e){res.writeHead(500);res.end('Fixture error');}});
});
server.listen(8877,'127.0.0.1',()=>process.stdout.write('Synthetic UI fixture: http://127.0.0.1:8877\n'));
