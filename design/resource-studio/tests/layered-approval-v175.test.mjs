// Synthetic browser-store/transport unit tests only. These do not prove API,
// PostgreSQL, real permissions, browser usability or production readiness.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,randomUUID} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {createApprovalController,approvalCommandPath,approvalScopeKey,approvalSHA,checkApprovalTask,isManagedApprovalTask,approvalExecutionAllowed,readApprovalDraft,writeApprovalDraft,listApprovalDrafts} from '../src/layered-approval-v175.mjs';
import {freshNumber,typedRequestJSON,numericDisplay} from '../src/typed-numbers-v132.mjs';

if(!globalThis.crypto)Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});
const actor='11111111-1111-4111-8111-111111111111',workspace='22222222-2222-4222-8222-222222222222',subject='33333333-3333-4333-8333-333333333333';
class Storage{
 constructor(){this.values=new Map();}
 get length(){return this.values.size;}
 key(index){return [...this.values.keys()][index]??null;}
 getItem(key){return this.values.get(key)??null;}
 setItem(key,value){this.values.set(key,String(value));}
 removeItem(key){this.values.delete(key);}
}
const failure=(status,message='synthetic rejection')=>Object.assign(Error(message),{status});
function scene(options={}){
 const storage=options.storage||new Storage(),requests=[],receipts=new Map(),scope={actor_id:actor,workspace_id:workspace,role:'admin',subject:'settings:example'};
 let me={user_id:actor,workspace_id:workspace,role:'admin'},lookupFailure=404,postHook=null;
 const api=async(path,request)=>{
  requests.push({path,...request});
  if(path==='/me')return {...me};
  if(request?.method==='POST'){
   if(postHook)return postHook(path,request);
   const value=JSON.parse(request.body),result={schema:'approval-setting-receipt-v175',operation:'unit',id:subject,version:1,request_id:value.request_id};
   receipts.set(value.request_id,{schema:'layered-approval-original-result-v175',request_id:value.request_id,operation:'unit',subject_id:subject,body_sha256:await approvalSHA(request.body,webcrypto.subtle),result});return result;
  }
  if(path.includes('/requests/')){const id=path.split('/requests/')[1].split('?')[0];if(receipts.has(id))return receipts.get(id);throw failure(lookupFailure);}
  return {};
 };
 const locks={request:async(_key,_options,callback)=>callback()},crypto={subtle:webcrypto.subtle,randomUUID:options.randomUUID||randomUUID};
 const create=(extra={})=>createApprovalController({api,scope,storage,locks,crypto,...extra});
 return {storage,requests,receipts,scope,create,setIdentity:value=>{me=value;},setPost:hook=>{postHook=hook;},setLookupFailure:code=>{lookupFailure=code;},postCount:()=>requests.filter(x=>x.method==='POST').length};
}
const body={unit_id:subject,expected_version:0,name:'真实组织示例',parent_id:null,enabled:true};
const send=controller=>controller.send(approvalCommandPath('unit',subject),'unit',subject,body);

test('normal send saves exact original and independent receipt before releasing pending slot',async()=>{
 const s=scene(),c=s.create();await c.open();const result=await send(c);assert.equal(result.version,1);assert.equal(s.postCount(),1);assert.equal(c.state.pending,null);
 const post=s.requests.find(x=>x.method==='POST'),value=JSON.parse(post.body),prefix=approvalScopeKey(s.scope)+':';
 const original=JSON.parse(s.storage.getItem(prefix+'original:'+value.request_id));assert.equal(original.exact_body,post.body);assert.equal(original.body_sha256,await approvalSHA(post.body,webcrypto.subtle));assert.ok(s.storage.getItem(prefix+'receipt:'+value.request_id));
});
test('lost successful POST response recovers by GET and does not POST again',async()=>{
 const s=scene();s.setPost(async(_path,request)=>{const value=JSON.parse(request.body);s.receipts.set(value.request_id,{schema:'layered-approval-original-result-v175',request_id:value.request_id,operation:'unit',subject_id:subject,body_sha256:await approvalSHA(request.body,webcrypto.subtle),result:{schema:'approval-setting-receipt-v175',operation:'unit',id:subject,version:1,request_id:value.request_id}});throw Error('response dropped');});
 const c=s.create();await c.open();assert.equal((await send(c)).version,1);assert.equal(s.postCount(),1);assert.equal(s.requests.filter(x=>x.path.includes('/requests/')).length,1);assert.equal(c.state.pending,null);
});
test('unknown response survives remount and original GET restores receipt without replay',async()=>{
 const s=scene();s.setPost(async()=>{throw Error('connection lost');});const first=s.create();await assert.rejects(()=>send(first));const row=first.state.pending;assert.ok(row);assert.equal(first.state.missing,true);first.dispose();
 s.receipts.set(row.request_id,{schema:'layered-approval-original-result-v175',request_id:row.request_id,operation:'unit',subject_id:subject,body_sha256:row.body_sha256,result:{schema:'approval-setting-receipt-v175',operation:'unit',id:subject,version:1,request_id:row.request_id}});
 const second=s.create();await second.open();await second.recover();assert.equal(s.postCount(),1);assert.equal(second.state.pending,null);
});
test('404 remains unknown and a new command cannot overwrite the original body',async()=>{
 const s=scene();s.setPost(async()=>{throw Error('unknown');});const c=s.create();await assert.rejects(()=>send(c));const original=c.state.pending.exact_body;await assert.rejects(()=>send(c),/原请求/);assert.equal(c.state.pending.exact_body,original);assert.equal(s.postCount(),1);await assert.rejects(()=>c.editRejected());assert.ok(c.state.pending);
});
test('same original re-send first performs GET and never changes nonce or bytes',async()=>{
 const s=scene();s.setPost(async()=>{throw Error('unknown');});const c=s.create();await assert.rejects(()=>send(c));const original=c.state.pending;s.setPost(async(_path,request)=>({schema:'approval-setting-receipt-v175',operation:'unit',id:subject,version:1,request_id:JSON.parse(request.body).request_id}));await c.resend();const posts=s.requests.filter(x=>x.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].body,posts[1].body);assert.equal(JSON.parse(posts[1].body).request_id,original.request_id);
});
test('definite CAS rejection is archived; editing does not erase original request',async()=>{
 const s=scene();s.setPost(async()=>{throw failure(409,'version changed');});const c=s.create();await assert.rejects(()=>send(c));const row=c.state.pending;assert.equal(row.rejection_status,409);await c.editRejected();assert.equal(c.state.pending,null);assert.ok(s.storage.getItem(c.key+':original:'+row.request_id));assert.ok(s.storage.getItem(c.key+':rejected:'+row.request_id));
});
test('lookup conflict does not turn into missing or release the request',async()=>{
 const s=scene();s.setLookupFailure(409);s.setPost(async()=>{throw failure(409);});const c=s.create();await assert.rejects(()=>send(c));assert.equal(c.state.missing,false);assert.ok(c.state.pending);await assert.rejects(()=>c.editRejected());assert.equal(s.postCount(),1);
});
test('cross-account or workspace identity change prevents network mutation',async()=>{
 const s=scene();s.setIdentity({user_id:subject,workspace_id:workspace,role:'admin'});const c=s.create();await assert.rejects(()=>send(c),/账号/);assert.equal(s.postCount(),0);assert.equal(c.state.pending,null);
});
test('browser storage failure prevents POST even though the form exists in memory',async()=>{
 const storage=new Storage();storage.setItem=()=>{throw Error('quota');};const s=scene({storage}),c=s.create();await assert.rejects(()=>send(c),/quota/);assert.equal(s.postCount(),0);
});
test('missing Web Locks blocks fresh sends rather than silently dropping concurrency protection',async()=>{
 const s=scene(),c=s.create({locks:null});await assert.rejects(()=>send(c),/浏览器/);assert.equal(s.postCount(),0);
});
test('corrupt or rewritten original-body record fails closed on remount',async()=>{
 const s=scene();s.setPost(async()=>{throw Error('unknown');});const c=s.create();await assert.rejects(()=>send(c));const pending=c.state.pending;s.storage.setItem(c.key+':original:'+pending.request_id,'{}');const second=s.create();await assert.rejects(()=>second.open());assert.equal(s.postCount(),1);
});
test('mismatched recovered subject or body hash cannot be accepted as this command',async()=>{
 const s=scene();s.setPost(async()=>{throw Error('unknown');});const c=s.create();await assert.rejects(()=>send(c));const row=c.state.pending;s.receipts.set(row.request_id,{schema:'layered-approval-original-result-v175',request_id:row.request_id,operation:'unit',subject_id:actor,body_sha256:row.body_sha256,result:{}});await assert.rejects(()=>c.recover(),/不一致/);assert.ok(c.state.pending);
});
test('nonce generator collision cannot overwrite a successful original with different content',async()=>{
 const nonce='44444444-4444-4444-8444-444444444444',s=scene({randomUUID:()=>nonce}),c=s.create();await send(c);await assert.rejects(()=>c.send(approvalCommandPath('unit',subject),'unit',subject,{...body,name:'changed'}),/编号已经存在/);assert.equal(s.postCount(),1);assert.equal(JSON.parse(s.storage.getItem(c.key+':original:'+nonce)).exact_body.includes('changed'),false);
});
test('full task revision keeps optional inputs and original decimal token in exact UTF-8 wire',async()=>{
 const s=scene(),taskScope={...s.scope,subject:'task:'+subject},c=s.create({scope:taskScope});s.setPost(async(_path,request)=>({schema:'layered-approval-task-receipt-v175',task_id:subject,managed_id:actor,request_id:JSON.parse(request.body).request_id,operation:'revise',version:2,task_version:3,status:'draft'}));const value={title:'完整输入',note:'私有说明',graph_inputs:{measure:freshNumber('1.0'),optional:null,flags:[true,false]},goal_spec:null};await c.send(approvalCommandPath('task_revise',subject),'task_revise',subject,{expected_version:1,reason:'修订依据',value});const raw=s.requests.find(x=>x.method==='POST').body;assert.match(raw,/"measure":1\.0/);assert.match(raw,/"optional":null/);assert.match(raw,/"goal_spec":null/);
});
test('human draft saves unfinished numeric text and all nested values for refresh',()=>{
 const storage=new Storage(),key=approvalScopeKey({actor_id:actor,workspace_id:workspace,subject:'task:'+subject}),value={value:{title:'草稿',note:'工作说明',graph_inputs:{x:freshNumber('1.0')},goal_spec:null},reason:'理由',base_version:2,field_inputs:{'graph_inputs/x':'1e'},field_errors:{'graph_inputs/x':'未完成'}};writeApprovalDraft(storage,key,value);const restored=readApprovalDraft(storage,key);assert.equal(restored.field_inputs['graph_inputs/x'],'1e');assert.equal(numericDisplay(restored.value.graph_inputs.x),'1.0');assert.ok(listApprovalDrafts(storage,key).length);assert.equal(typedRequestJSON(restored.value),typedRequestJSON(value.value));
});
test('different browser writer records survive another writer becoming the current pointer',()=>{
 const storage=new Storage(),key='scoped-example',other={schema:'approval-human-draft-v175',owner_id:'55555555-5555-4555-8555-555555555555',at:'2026-01-01T00:00:00Z',value:{title:'另一窗口原稿'}};storage.setItem(key+':draft-owner:'+other.owner_id,typedRequestJSON(other));storage.setItem(key+':draft',typedRequestJSON(other));writeApprovalDraft(storage,key,{title:'当前窗口稿'});assert.equal(listApprovalDrafts(storage,key).length,2);assert.ok(listApprovalDrafts(storage,key).some(x=>x.value.title==='另一窗口原稿'));
});
test('managed tasks cannot use old execution view while draft/approved/accepted',()=>{
 for(const status of ['draft','in_review','approved','accepted','acceptance_returned'])assert.equal(approvalExecutionAllowed({status:'approval_'+status,layered_approval_v175:{status}}),false);assert.equal(approvalExecutionAllowed({layered_approval_v175:{status:'executing'}}),true);assert.equal(approvalExecutionAllowed({status:'workflow_running'}),true);assert.equal(isManagedApprovalTask({status:'approval_draft'}),true);assert.throws(()=>checkApprovalTask({schema:'old',task_id:subject},subject));assert.throws(()=>approvalCommandPath('task_magic',subject));
});
test('real workspace mounts settings and managed panel; old graph acceptance is not used for managed tasks',async()=>{
 const workspaceSource=await readFile(new URL('../src/BusinessWorkspace.jsx',import.meta.url),'utf8'),graph=await readFile(new URL('../src/GraphTaskPanel.jsx',import.meta.url),'utf8'),settings=await readFile(new URL('../src/ApprovalSettingsV175.jsx',import.meta.url),'utf8'),task=await readFile(new URL('../src/TaskApprovalPanelV175.jsx',import.meta.url),'utf8');assert.match(workspaceSource,/<ApprovalSettingsV175/);assert.match(workspaceSource,/isManagedApprovalTask\(task\).*TaskApprovalPanelV175/);assert.match(graph,/!isManagedApprovalTask\(task\).*GraphGoalGateV133/);assert.match(task,/view\.permissions\|\|\{\}/);assert.match(task,/submission_hash:view.submission_hash/);assert.match(task,/basis_hash:action.basis_hash/);assert.match(task,/expected_version:intent\?\.expected_version/);assert.match(settings,/data\.members\.map/);assert.match(settings,/stages.length>=20/);assert.equal(task.includes('dangerouslySetInnerHTML'),false);
});
