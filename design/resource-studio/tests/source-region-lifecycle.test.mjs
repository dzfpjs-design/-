import test from 'node:test';
import assert from 'node:assert/strict';
import {FullSemanticSourceReviewController,sourceReviewCommandHash} from '../src/full-semantic-source-review-controller.mjs';
import {FullSemanticRegionMappingController,regionCommandHash} from '../src/full-semantic-region-mapping-controller.mjs';
import {copy,digest} from '../src/report-evidence-reading-contract.mjs';
import {createRetainedControllerLifecycle} from '../src/retained-controller-lifecycle.mjs';

// These real controllers consume synthetic DTOs and durable browser storage.
// This is lifecycle evidence, not React mount, source authority or browser acceptance.
function memory(){const rows=new Map();return {get length(){return rows.size;},key:i=>[...rows.keys()][i]??null,getItem:k=>rows.get(k)??null,setItem:(k,v)=>rows.set(k,String(v)),removeItem:k=>rows.delete(k)};}
function barrier(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function retained(){const queue=[],lifecycle=createRetainedControllerLifecycle({defer:fn=>queue.push(fn)});return {lifecycle,flush:()=>{while(queue.length)queue.shift()();}};}
async function fixture(kind,{storage=memory(),tab=memory(),scope={actor_id:crypto.randomUUID(),workspace_id:crypto.randomUUID(),handoff_id:crypto.randomUUID()}}={}){
 const H='a'.repeat(64),value='合成生命周期领域',owner={path:'/domain',kind:'domain',label:'合成领域',value_hash:await digest(value),ref_hashes:[],evidence_status:'context',pending_reasons:['context_not_source_evidence']};
 const manifest={schema:'report-full-semantic-source-manifest-v1',handoff_id:scope.handoff_id,design_hash:H,result_hash:H,source_hash:H,catalog_hash:H,privacy:{schema:'evidence-privacy-v1',projection_hash:H},owners:[owner],refs:[]};manifest.manifest_hash=await digest(manifest);
 const source=kind==='source',base='/setup/'+(source?'full-semantic-source-reviews/':'full-semantic-region-mappings/')+scope.handoff_id;
 const state={schema:source?'report-full-semantic-source-review-state-v1':'report-full-semantic-region-state-v1',handoff_id:scope.handoff_id,active:true,configuration:{revision:1,hash:H},manifest,...(source?{reviews:[]}:{sessions:[],proofs:[]}),can_confirm:false,can_publish:false};
 const evidence={schema:'report-full-semantic-source-review-evidence-v1',manifest_hash:manifest.manifest_hash,owner:{...copy(owner),value},references:[],formal_source_status:'pending_final_view_projection',can_confirm:false,can_publish:false};
 tab.setItem('ga-setup-csrf','synthetic-lifecycle-'+scope.actor_id);tab.setItem('ga-setup-csrf-user',scope.actor_id);tab.setItem('ga-setup-csrf-workspace',scope.workspace_id);
 const calls=[],mode={hold:null,error:null};
 function hold(path){const entered=barrier(),release=barrier();mode.hold={path,entered,release};return {entered:entered.promise,release:()=>release.resolve()};}
 async function api(path,options={}){
  calls.push({path,method:options.method||'GET',body:options.body});
  if(mode.hold?.path===path){const paused=mode.hold;paused.entered.resolve();await paused.release.promise;}
  if(mode.error)throw mode.error;
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};
  if(path===base)return copy(state);
  if(path.startsWith(base+'/evidence?'))return copy(evidence);
  if(path.startsWith(base+'/requests/'))return {schema:source?'report-full-semantic-source-review-recovery-v1':'report-full-semantic-region-recovery-v1',found:false};
  throw Error('Unexpected synthetic lifecycle path: '+path);
 }
 const options={api,handoffId:scope.handoff_id,actorId:scope.actor_id,workspaceId:scope.workspace_id,storage,tabStorage:tab,locks:{request:async(_name,_mode,fn)=>fn()}};
 const Controller=source?FullSemanticSourceReviewController:FullSemanticRegionMappingController;
 const make=()=>new Controller(options),scopeKey=Object.values(scope).join(':');
 async function input(c){
  if(source){await c.select('/domain');c.edit({decision:'pending',note:'保留尚未提交的人工作业',checked_ref_hashes:[]});}
  else{
   // A valid restored local form may retain a previous occurrence while current
   // source state has no matching session. It never authorizes an entry POST.
   c.state.session_id=crypto.randomUUID();c.state.ref_hash=H;c.state.generation=1;
   c.state.forms[c.formKey()]={manifest_hash:manifest.manifest_hash,selection_hash:H,version:0,mapping:null,image_acknowledgements:[],privacy_acknowledged:true,dirty:true,generation:1};
   assert.equal(c.persist(),true);
  }
 }
 async function pending(c){
  const body=source?{schema:'report-full-semantic-source-review-save-v1',request_id:crypto.randomUUID(),manifest_hash:manifest.manifest_hash,owner_path:'/domain',owner_value_hash:owner.value_hash,expected_version:0,value:{decision:'pending',note:'原请求正文',checked_ref_hashes:[]}}:{schema:'report-full-semantic-region-proof-command-v1',request_id:crypto.randomUUID(),session_id:c.state.session_id,manifest_hash:manifest.manifest_hash,configuration_revision:1,configuration_hash:H,preview_hash:H,consent:true};
  const command_hash=await (source?sourceReviewCommandHash:regionCommandHash)(scope,body);
  const p=source?{schema:'full-semantic-source-review-outbox-v1',scope:copy(scope),body,command_hash,generation:c.state.generation,attempted:true,rejection:null}:{schema:'full-semantic-region-outbox-v1',scope:copy(scope),kind:'proof',body,session_id:body.session_id,command_hash,generation:c.state.generation,form_key:null,attempted:true,rejection:null};
  storage.setItem(c.outboxKey,JSON.stringify(p));c.pending=c.readPending();return p;
 }
 return {kind,source,scope,scopeKey,storage,tab,base,state,calls,mode,hold,options,make,input,pending};
}

for(const kind of ['source','region']){
 test(kind+': StrictMode replay lets the original real-controller recovery finish once and releases busy',async()=>{
  const f=await fixture(kind),c=f.make(),{lifecycle,flush}=retained(),first=lifecycle.setup(c,f.scopeKey);
  await c.load();await f.input(c);const p=await f.pending(c),saved=copy(c.state),gate=f.hold(f.base+'/requests/'+p.body.request_id);
  const original=c.recover();await gate.entered;assert.equal(c.busy,true);
  lifecycle.cleanup(first);assert.equal(c.suspended,true);assert.throws(()=>c.check());
  const second=lifecycle.setup(c,f.scopeKey);assert.equal(second.token,first.token);flush();
  await assert.rejects(c.recover()); // The replay cannot start a second busy request.
  gate.release();assert.equal((await original).found,false);assert.equal(c.busy,false);c.check();
  assert.equal(f.calls.filter(call=>call.path.includes('/requests/')).length,1);assert.ok(f.calls.every(call=>call.method==='GET'));
  assert.deepEqual(c.state,saved);assert.deepEqual(c.readPending(),p);assert.equal(c.persist(),true);
  lifecycle.cleanup(second);flush();
  const restored=f.make();assert.deepEqual(restored.form(),c.form());assert.deepEqual(restored.pending,p);assert.notEqual(restored.key,c.key);restored.dispose();
 });

 test(kind+': real unmount fences a delayed state response before disposal and never applies it',async()=>{
  const f=await fixture(kind),c=f.make(),{lifecycle,flush}=retained(),lease=lifecycle.setup(c,f.scopeKey),gate=f.hold(f.base);
  const reading=c.load();await gate.entered;lifecycle.cleanup(lease);gate.release();
  await assert.rejects(reading);assert.equal(c.view??null,null);assert.equal(c.active,true);assert.equal(c.suspended,true);
  flush();assert.equal(c.active,false);assert.equal(lifecycle.setup(c,f.scopeKey),null);assert.ok(f.storage.getItem(c.key));
 });

 for(const change of ['actor','workspace','handoff'])test(kind+': '+change+' change retires old awaits and isolates original input and nonce',async()=>{
  const f=await fixture(kind),old=f.make(),{lifecycle,flush}=retained(),lease=lifecycle.setup(old,f.scopeKey);
  await old.load();await f.input(old);const p=await f.pending(old),oldWorking=f.storage.getItem(old.key),oldOutbox=f.storage.getItem(old.outboxKey),gate=f.hold(f.base+'/requests/'+p.body.request_id);
  const reading=old.recover();await gate.entered;lifecycle.cleanup(lease);
  const scope={...f.scope,[change+'_id']:crypto.randomUUID()},next=await fixture(kind,{scope,storage:f.storage,tab:f.tab}),fresh=next.make();
  const nextLease=lifecycle.setup(fresh,next.scopeKey);assert.ok(nextLease);assert.equal(old.active,false);
  gate.release();await assert.rejects(reading);flush();await fresh.load();
  assert.equal(fresh.pending,null);assert.equal(fresh.form(),null);assert.equal(f.storage.getItem(old.key),oldWorking);assert.equal(f.storage.getItem(old.outboxKey),oldOutbox);
  assert.throws(()=>old.check());assert.equal(lifecycle.cleanup(lease),false);assert.equal(lifecycle.cancel(lease.token),false);assert.equal(lifecycle.isCurrent(nextLease.token),true);
  assert.equal(f.calls.filter(call=>call.method==='POST').length,0);fresh.dispose();
 });

 test(kind+': a denied recovery cannot be revived and leaves its original outbox intact',async()=>{
  const f=await fixture(kind),c=f.make(),{lifecycle,flush}=retained(),lease=lifecycle.setup(c,f.scopeKey);
  await c.load();await f.input(c);const p=await f.pending(c),raw=f.storage.getItem(c.outboxKey),gate=f.hold(f.base+'/requests/'+p.body.request_id);
  const recovery=c.recover();await gate.entered;lifecycle.cleanup(lease);f.mode.error=Object.assign(Error('synthetic denied'),{status:403});gate.release();await assert.rejects(recovery);
  assert.equal(c.denied,true);assert.equal(c.busy,false);assert.equal(lifecycle.setup(c,f.scopeKey),null);flush();
  assert.equal(f.storage.getItem(c.outboxKey),raw);assert.equal(c.view,null);assert.equal(c.active,false);
 });

 test(kind+': an unmounted queued WebLock cannot query or resend an old request',async()=>{
  const f=await fixture(kind),queued=barrier(),release=barrier();f.options.locks={request:async(_key,_mode,run)=>{queued.resolve();await release.promise;return run();}};
  const c=f.make(),{lifecycle,flush}=retained(),lease=lifecycle.setup(c,f.scopeKey);await c.load();await f.input(c);const p=await f.pending(c),before=f.calls.length;
  const recovery=c.recover(true);await queued.promise;lifecycle.cleanup(lease);flush();release.resolve();await assert.rejects(recovery);
  assert.equal(c.busy,false);assert.equal(f.calls.length,before);assert.deepEqual(c.readPending(),p);assert.ok(f.storage.getItem(c.key));
 });

 test(kind+': leave persistence failure stays visible through replay and can be retried without discarding labor',async()=>{
  const f=await fixture(kind),c=f.make(),{lifecycle,flush}=retained(),first=lifecycle.setup(c,f.scopeKey);await c.load();await f.input(c);const saved=copy(c.form()),write=f.storage.setItem;
  f.storage.setItem=(key,value)=>{if(key===c.key)throw Error('synthetic quota');write(key,value);};
  assert.equal(c.persist(),false);assert.equal(c.localSaved,false);lifecycle.cleanup(first);const second=lifecycle.setup(c,f.scopeKey);flush();
  assert.equal(c.persist(),false);assert.deepEqual(c.form(),saved);assert.equal(c.localSaved,false);
  f.storage.setItem=write;assert.equal(c.persist(),true);assert.equal(c.localSaved,true);assert.deepEqual(c.readWorking(c.key).forms,c.state.forms);
  lifecycle.cleanup(second);flush();const restored=f.make();assert.deepEqual(restored.form(),saved);restored.dispose();
 });
}
