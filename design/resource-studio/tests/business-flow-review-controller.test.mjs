import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {BusinessFlowReviewController as Controller,REVIEW_BASE,reviewCopy,reviewIssueTarget} from '../src/business-flow-review-controller.mjs';
import {createRetainedControllerLifecycle} from '../src/retained-controller-lifecycle.mjs';

const id=n=>'10000000-0000-4000-8000-'+n.toString(16).padStart(12,'0');
function memory(){const data=new Map();return {data,failWrites:false,getItem(k){return data.get(k)??null;},setItem(k,v){if(this.failWrites)throw Error('synthetic quota');data.set(k,String(v));},removeItem(k){data.delete(k);}};}
const hash=async raw=>Buffer.from(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(raw))).toString('hex');
function fixture(){
 const storage=memory(),session=memory(),scope={producerId:id(1),actorId:id(2),workspaceId:id(3)};
 session.setItem('ga-setup-csrf','public-synthetic-csrf');session.setItem('ga-setup-csrf-user',scope.actorId);session.setItem('ga-setup-csrf-workspace',scope.workspaceId);
 const baseline={schema:'report-business-flow-wire-v182',binding_hash:'a'.repeat(64),business:{name:'合成业务',flows:[{flow_id:'review',name:'未完成流程',nodes:[{node_id:'check',name:'待补步骤'}],edges:[]}],questions:[]},evidence:[]};
 const diag={schema:'business-flow-review-diagnostics-v1',available:true,value:baseline,issues:[{code:'missing',path:'/business/flows/0/nodes/0/role',message:'请补充负责岗位',span_ref:null}],source_evidence:[{span_ref:'w:p1/s1',source_text:'完整的公开合成原文。',text_origin:'platform_resolved_source'}],structural_valid:false,source_valid:false,semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};
 const make=n=>({attempt_id:id(n),name:'业务候选 '+n,version:0,value:reviewCopy(baseline),note:'',baseline_value:reviewCopy(baseline),diagnostics:reviewCopy(diag),saved:false,source_available:true,can_apply:false});
 const rows=new Map([[id(4),make(4)],[id(5),make(5)]]),commands=new Map(),calls=[],mode={lose:false,drop:false,genericConflict:false,wait:null,foreign:false};
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});
  assert.equal(options.headers?.['X-Workspace-ID'],scope.workspaceId);assert.equal(options.headers?.['X-CSRF-Token'],'public-synthetic-csrf');
  if(path==='/me')return {user_id:mode.foreign?id(99):scope.actorId,workspace_id:scope.workspaceId,role:'operator'};
  if(path.startsWith(REVIEW_BASE+'/candidates?'))return {items:[...rows.values()].map(r=>({attempt_id:r.attempt_id,name:r.name,status:'failed',saved_version:r.version}))};
  if(path.startsWith(REVIEW_BASE+'/requests/')){const nonce=path.split('/requests/')[1].split('?')[0],expected=new URLSearchParams(path.split('?')[1]).get('expected_sha256'),saved=commands.get(nonce);if(saved)assert.equal(saved.sha,expected);return {schema:'business-flow-review-original-result-v1',request_id:nonce,found:!!saved,body_sha256:expected,result:saved?.result||null};}
  const match=new RegExp('^'+REVIEW_BASE+'/attempts/([^/]+)(/save)?$').exec(path);assert.ok(match,'known API '+path);const row=rows.get(match[1]);assert.ok(row);
  if(!match[2])return reviewCopy(row);
  assert.equal(options.method,'POST');const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body),['request_id','expected_version','value','note']);
  const original=commands.get(body.request_id);if(original){assert.equal(original.raw,options.body);return reviewCopy(original.result);}
  if(mode.drop)throw Error('synthetic not delivered');
  if(mode.genericConflict)throw Object.assign(Error('原库存暂无法核对'),{status:409,detail:{code:'inventory_unavailable'}});
  if(body.expected_version!==row.version)throw Object.assign(Error('服务器稿件已有新版本'),{status:409,detail:{code:'business_flow_review_version_conflict',not_applied:true,current_version:row.version}});
  const result={schema:'business-flow-review-save-result-v1',id:id(50),attempt_id:row.attempt_id,request_id:body.request_id,version:row.version+1,status:'incomplete',source_binding:{},baseline_value:reviewCopy(row.baseline_value),value:reviewCopy(body.value),note:body.note,diagnostics:reviewCopy(diag),source_binding_hash:'b'.repeat(64),baseline_hash:'c'.repeat(64),semantic_quality_verified:false,can_apply:false,can_publish:false,can_execute:false};
  commands.set(body.request_id,{raw:options.body,sha:await hash(options.body),result:reviewCopy(result)});Object.assign(row,reviewCopy(result),{saved:true});
  if(mode.wait)await mode.wait;
  if(mode.lose){mode.lose=false;throw Error('synthetic lost after save');}return reviewCopy(result);
 };
 const locks={request:async(name,options,fn)=>{assert.equal(options.mode,'exclusive');return fn();}};
 const options={...scope,api,storage,session,locks,crypto:webcrypto};
 return {storage,session,scope,rows,commands,calls,mode,options,posts:()=>calls.filter(x=>x.method==='POST')};
}
async function opened(f){const c=new Controller(f.options);await c.open();await c.select(id(4));return c;}
function edit(c,note='保留未完成的人工修订'){const v=reviewCopy(c.form.value);v.business.flows[0].name='人工修订流程';c.edit(v,note);}

test('unfinished graph saves as incomplete using exact original bytes and GET-first single POST',async()=>{
 const f=fixture(),c=await opened(f);edit(c);const baseline=reviewCopy(c.detail.baseline_value),original=reviewCopy(c.form.value);await c.save();
 assert.equal(c.pending,null);assert.equal(c.form.version,1);assert.equal(c.form.dirty,false);assert.equal(f.posts().length,1);
 const post=f.posts()[0],body=JSON.parse(post.body),probe=f.calls.findIndex(x=>x.path.includes('/requests/'+body.request_id));assert.ok(probe<f.calls.indexOf(post));
 assert.deepEqual(f.rows.get(id(4)).value,original);assert.deepEqual(c.detail.baseline_value,baseline);assert.equal(c.detail.status,'incomplete');assert.equal(c.detail.can_apply,false);
 c.dispose();const restored=await opened(f);assert.deepEqual(restored.form.value,original);assert.equal(restored.form.version,1);assert.equal(f.posts().length,1);restored.dispose();
});

test('lost save response, refresh and original GET recovery retain later typing and never repost',async()=>{
 const f=fixture(),c=await opened(f);edit(c,'原发送说明');f.mode.lose=true;await assert.rejects(c.save());const original=reviewCopy(c.pending),oldKey=c.key;
 edit(c,'等待期间继续输入，不能被原回执覆盖');c.dispose();const next=await opened(f);
 assert.notEqual(next.key,oldKey);assert.deepEqual(next.pending,original);assert.equal(next.form.note,'等待期间继续输入，不能被原回执覆盖');
 await next.recover();assert.equal(f.posts().length,1);assert.equal(next.pending,null);assert.equal(next.form.version,1);assert.equal(next.form.dirty,true);assert.equal(next.form.note,'等待期间继续输入，不能被原回执覆盖');
 assert.ok(f.storage.getItem(oldKey));next.dispose();
});

test('not found is not non-execution; retry is explicit and uses the same nonce and raw body',async()=>{
 const f=fixture(),c=await opened(f);edit(c);f.mode.drop=true;await assert.rejects(c.save());const raw=c.pending.raw,nonce=c.pending.body.request_id;edit(c,'后来输入');
 const reply=await c.recover();assert.equal(reply.found,false);assert.equal(c.pending.raw,raw);assert.throws(()=>c.archiveRejected());assert.equal(f.posts().length,1);
 f.mode.drop=false;await c.recover(true);assert.equal(f.posts().length,2);assert.equal(f.posts()[1].body,raw);assert.equal(JSON.parse(f.posts()[1].body).request_id,nonce);assert.equal(c.form.note,'后来输入');assert.equal(c.form.dirty,true);c.dispose();
});

test('known CAS rejection can be retained separately; generic rejection cannot clear original request',async()=>{
 const f=fixture(),c=await opened(f);edit(c);f.rows.get(id(4)).version=2;await assert.rejects(c.save());assert.equal(c.pending.rejection.code,'business_flow_review_version_conflict');const raw=c.pending.raw;
 c.archiveRejected();assert.equal(c.pending,null);assert.equal(c.state.archives[0].raw,raw);await c.loadDetail();assert.equal(c.form.note,'保留未完成的人工修订');c.alignVersion();assert.equal(c.form.version,2);await c.save();assert.equal(c.form.version,3);c.dispose();
 const g=fixture(),d=await opened(g);edit(d);g.mode.genericConflict=true;await assert.rejects(d.save());assert.equal(d.pending.rejection,null);assert.throws(()=>d.archiveRejected());assert.ok(d.pending);d.dispose();
});

test('switching candidate while a save waits cannot overwrite that candidate or later human work',async()=>{
 const f=fixture(),c=await opened(f);edit(c);let release;f.mode.wait=new Promise(resolve=>{release=resolve;});const save=c.save();
 for(let i=0;i<30&&!f.posts().length;i++)await new Promise(resolve=>setImmediate(resolve));assert.equal(f.posts().length,1);
 await c.select(id(5));edit(c,'第二候选独立修订');release();await save;assert.equal(c.state.selected,id(5));assert.equal(c.form.note,'第二候选独立修订');assert.equal(c.form.dirty,true);assert.equal(c.state.forms[id(4)].version,1);c.dispose();
});

test('local write failure blocks sends and candidate changes; export preserves in-memory labor',async()=>{
 const f=fixture(),c=await opened(f);f.storage.failWrites=true;edit(c,'本机容量失败仍需保留');assert.equal(c.localSaved,false);await assert.rejects(c.save());await assert.rejects(c.select(id(5)));assert.equal(f.posts().length,0);assert.ok(c.exportText().includes('本机容量失败仍需保留'));
 f.storage.failWrites=false;assert.equal(c.persist(),true);await c.save();assert.equal(f.posts().length,1);c.dispose();
});

test('oversize draft is retained without truncation or a new outbox',async()=>{
 const f=fixture(),c=await opened(f),v=reviewCopy(c.form.value);v.business.name='字'.repeat(400000);c.edit(v,'大稿保留');await assert.rejects(c.save(),/1MiB/);assert.equal(c.pending,null);assert.equal(f.posts().length,0);assert.equal(c.form.value.business.name.length,400000);assert.ok(c.exportText().includes('大稿保留'));c.dispose();
});

test('different accounts cannot read cached work or finish an old in-flight request',async()=>{
 const f=fixture(),c=await opened(f);edit(c,'原账号私人输入');f.session.setItem('ga-setup-csrf-user',id(99));assert.throws(()=>c.exportText(),/会话已变化/);await assert.rejects(c.save());assert.equal(c.identityStatus,'concealed');assert.equal(f.posts().length,0);c.dispose();
 const other=fixture();other.mode.foreign=true;const d=new Controller(other.options);await assert.rejects(d.open());assert.equal(d.owner,null);assert.equal(other.storage.data.size,0);d.dispose();
});

test('StrictMode retained lifetime resumes, true unmount fences awaits, and source issue paths target editors',async()=>{
 const f=fixture(),c=await opened(f),queue=[],lifecycle=createRetainedControllerLifecycle({defer:fn=>queue.push(fn)});
 const first=lifecycle.setup(c,'A');lifecycle.cleanup(first);const second=lifecycle.setup(c,'A');queue.splice(0).forEach(fn=>fn());assert.ok(second);assert.equal(c.active,true);edit(c);await c.save();
 lifecycle.cleanup(second);assert.throws(()=>c.edit(c.form.value,'不得覆盖'),/范围已变化/);queue.splice(0).forEach(fn=>fn());assert.equal(c.active,false);
 assert.deepEqual(reviewIssueTarget('/business/flows/2/nodes/3/role'),{flow:2,kind:'nodes',index:3});assert.deepEqual(reviewIssueTarget('/business/flows/1/edges/0/to_node'),{flow:1,kind:'edges',index:0});assert.equal(reviewIssueTarget('/evidence/7/quote'),null);
});

test('read-only receipt recovery remains possible without write locks; new sends remain blocked',async()=>{
 const f=fixture(),c=await opened(f);edit(c);f.mode.lose=true;await assert.rejects(c.save());c.locks=null;await c.recover();assert.equal(c.pending,null);assert.equal(f.posts().length,1);edit(c,'只读恢复后新稿');await assert.rejects(c.save(),/多窗口/);assert.equal(f.posts().length,1);c.dispose();
});

test('leave guard preserves unsaved memory, retries local persistence and permits only idle durable pending work',async()=>{
 const f=fixture(),c=await opened(f);f.storage.failWrites=true;edit(c,'离页前必须保留的人工文字');
 assert.equal(c.safeToLeave(),false);assert.equal(c.form.note,'离页前必须保留的人工文字');assert.equal(c.localSaved,false);
 f.storage.failWrites=false;assert.equal(c.safeToLeave(),true);assert.equal(c.localSaved,true);
 assert.equal(JSON.parse(f.storage.getItem(c.key)).forms[id(4)].note,'离页前必须保留的人工文字');
 f.mode.drop=true;await assert.rejects(c.save());const original=c.pending.raw;
 assert.equal(c.safeToLeave(),true);assert.equal(JSON.parse(f.storage.getItem(c.key)).pending.raw,original);
 c.busy=true;assert.equal(c.safeToLeave(),false);assert.equal(c.pending.raw,original);c.busy=false;
 assert.equal(c.safeToLeave(),true);c.dispose();
});
