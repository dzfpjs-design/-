// Actual controller, actual frozen packet DTO; synthetic authenticated IO only.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createTaskEvidenceConsentController,checkedConsentPacket,TASK_PACKETS_BASE} from '../src/task-evidence-consent-v199.mjs';
const id=n=>'19900000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=v=>JSON.parse(JSON.stringify(v));
const scope={actor_id:id(1),workspace_id:id(2),task_id:id(3)};
class Memory{constructor(){this.map=new Map();this.fail=false;}getItem(k){if(this.fail)throw Error('synthetic storage failure');return this.map.get(k)??null;}setItem(k,v){if(this.fail)throw Error('synthetic storage failure');this.map.set(k,String(v));}removeItem(k){if(this.fail)throw Error('synthetic storage failure');this.map.delete(k);}}
async function fixture(){
 const storage=new Memory(),session=new Memory(),calls=[],mode={},saved=[];for(const[k,v]of Object.entries({'ga-setup-csrf-user':scope.actor_id,'ga-setup-csrf-workspace':scope.workspace_id,'ga-setup-csrf':'synthetic-csrf'}))session.setItem(k,v);
 const text='任务：核对样地\n温度：38.0 ℃\n仅工程模拟，不证明现场事实。\n',sha=[...new Uint8Array(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))].map(v=>v.toString(16).padStart(2,'0')).join('');
 const packet={schema:'workflow-task-evidence-packet-view-v199',id:id(4),task_id:scope.task_id,actor_id:scope.actor_id,created_at:'2026-10-24T10:00:00+08:00',version:1,simulation_only:true,proposal_only:true,cloud_authorized_at_creation:false,task_title:'样地核对任务',context_status:'ready',evidence_status:'insufficient',source_summary:{object_count:1,observation_count:1,gap_count:1,issue_count:0},evidence:{id:id(5),sha256:sha,media_type:'text/plain',size_bytes:new TextEncoder().encode(text).byteLength,version:1,cloud_model_ids:[]},model_options:[{id:id(6),name:'本任务千问模型',provider:'qwen',model:'test-qwen',content_hash:'a'.repeat(64),authorized:false}],model_options_available:true,text};
 const state={packet,role:'operator'};let c;
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-csrf');
  if(path==='/me')return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:state.role};
  if(path.startsWith(TASK_PACKETS_BASE+'?')){assert.equal(new URL('https://test.invalid'+path).searchParams.get('task_id'),scope.task_id);const item=copy(state.packet);delete item.text;return {schema:'workflow-task-evidence-packet-list-v199',task_id:scope.task_id,items:[item]};}
  if(path===TASK_PACKETS_BASE+'/'+packet.id){const value=copy(state.packet);mode.modifyPacket?.(value);return value;}
  assert.equal(path,'/evidence/'+packet.evidence.id+'/authorize');assert.equal(options.method,'POST');
  const pending=JSON.parse(storage.getItem(c.outboxKey));assert.equal(options.body,pending.raw);assert.equal(await c.hash(options.body),pending.sha256);const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['expected_version','model_profile_id','reason']);assert.equal(body.expected_version,packet.evidence.version);assert.equal(body.model_profile_id,id(6));
  if(mode.unknown||mode.cas){const error=Error('synthetic unconfirmed authorization');if(mode.cas)error.status=409;throw error;}
  packet.evidence.version++;packet.evidence.cloud_model_ids=[id(6)];packet.model_options[0].authorized=true;if(mode.lostResponse)throw Error('synthetic response lost after consent saved');return {id:packet.evidence.id,version:packet.evidence.version,data:{cloud_model_ids:[id(6)]}};
 };
 const locks={request:async(_name,_options,run)=>run()};c=createTaskEvidenceConsentController({api,scope,storage,session,crypto:webcrypto,locks,onSaved:value=>saved.push(value)});c.mount();await c.open();await c.select(packet.id);
 return {c,scope,api,storage,session,state,calls,mode,saved,locks};
}
function fill(c){c.edit({model_id:id(6)});c.edit({reason:'我已核对完整原文，仅允许本任务模型用于业务分析。'});c.edit({acknowledged:true});}
const posts=f=>f.calls.filter(v=>v.method==='POST');

test('the real model is not auto selected and exact consent POST is durably journaled before sending',async()=>{
 const f=await fixture();assert.equal(f.c.form.model_id,null);assert.equal(f.c.canAuthorize,false);assert.equal(posts(f).length,0);fill(f.c);assert.equal(f.c.canAuthorize,true);
 const result=await f.c.authorize();assert.equal(posts(f).length,1);assert.equal(result.authorized,true);assert.equal(result.original_request_application_proven,false);assert.equal(f.c.pending,null);assert.equal(f.saved[0].kind,'evidence_consent');assert.ok(f.c.notice.includes('模型尚未被本页面调用'));
 const body=JSON.parse(posts(f)[0].body);assert.equal(Object.hasOwn(body,'request_id'),false,'old Evidence API body is unchanged');assert.equal(body.reason,'我已核对完整原文，仅允许本任务模型用于业务分析。');
 assert.ok([...f.storage.map.keys()].some(k=>k.startsWith(f.c.prefix+'original:')));assert.ok([...f.storage.map.keys()].some(k=>k.startsWith(f.c.prefix+'confirmed:')));
});

test('response lost after real permission saved is recovered by GET packet only across remount',async()=>{
 const f=await fixture();fill(f.c);f.mode.lostResponse=true;await assert.rejects(()=>f.c.authorize());const original=copy(f.c.pending);f.c.dispose();
 const restored=createTaskEvidenceConsentController({api:f.api,scope,storage:f.storage,session:f.session,crypto:webcrypto,locks:f.locks});restored.mount();await restored.open();
 assert.equal(posts(f).length,1);assert.equal(restored.pending,null);assert.equal(restored.form.reason,original.body.reason);assert.equal(restored.model.authorized,true);assert.equal(restored.form.acknowledged,false);assert.equal(restored.form.stale,false);
 assert.ok(f.calls.filter(v=>v.path===TASK_PACKETS_BASE+'/'+original.packet_id).length>=2);
});

for(const failure of ['unknown','cas'])test(failure+' keeps exact local nonce and reason after GET still has no authorization',async()=>{
 const f=await fixture();fill(f.c);f.mode[failure]=true;await assert.rejects(()=>f.c.authorize());const original=copy(f.c.pending);await f.c.recover();await f.c.recover();
 assert.equal(posts(f).length,1);assert.deepEqual(f.c.pending,original);assert.equal(f.c.form.reason,original.body.reason);assert.equal(f.c.canAuthorize,false);assert.ok(f.c.notice.includes('不自动重发'));assert.ok(f.c.safeToLeave());
});

test('whole text hash, source identity and actual model pin cannot be substituted during recovery',async()=>{
 const f=await fixture();fill(f.c);f.mode.unknown=true;await assert.rejects(()=>f.c.authorize());const original=copy(f.c.pending);
 f.mode.modifyPacket=p=>{p.text=p.text.replace('38.0','40.0');};await assert.rejects(()=>f.c.recover(),/摘要/);assert.deepEqual(f.c.pending,original);
 f.mode.modifyPacket=p=>{p.model_options[0].content_hash='b'.repeat(64);p.model_options[0].authorized=true;p.evidence.cloud_model_ids=[id(6)];};const result=await f.c.recover();assert.equal(result.authorized,false);assert.deepEqual(f.c.pending,original);assert.equal(posts(f).length,1);
});

test('storage failure retains human reason and disables new authorization until readback protection succeeds',async()=>{
 const f=await fixture();fill(f.c);f.storage.fail=true;f.c.edit({reason:'这个授权理由即使存储暂时失败也不能丢失'});assert.equal(f.c.safeToLeave(),false);assert.equal(f.c.canAuthorize,false);assert.throws(()=>f.c.authorize());assert.equal(posts(f).length,0);assert.equal(f.c.form.reason,'这个授权理由即使存储暂时失败也不能丢失');
 f.storage.fail=false;assert.equal(f.c.retryLocal(),true);f.c.edit({acknowledged:true});assert.equal(f.c.canAuthorize,true);f.c.busy=true;assert.equal(f.c.safeToLeave(),false);f.c.busy=false;
});

test('viewer, missing current model availability and identity changes never create permission',async()=>{
 const f=await fixture();fill(f.c);f.state.role='viewer';await f.c.identity();assert.equal(f.c.canAuthorize,false);assert.throws(()=>f.c.authorize());f.state.role='operator';await f.c.identity();f.state.packet.model_options=[];f.state.packet.model_options_available=false;await f.c.refresh();assert.equal(f.c.detail.text,f.state.packet.text);assert.equal(f.c.canAuthorize,false);
 const before=[...f.storage.map];f.session.setItem('ga-setup-csrf-user',id(99));await assert.rejects(()=>f.c.identity());assert.equal(f.c.readable,false);assert.deepEqual([...f.storage.map],before);assert.equal(posts(f).length,0);
});

test('malformed permission flags and invented model choices are rejected, not inferred',async()=>{
 const f=await fixture(),p=copy(f.state.packet);p.model_options[0].authorized=true;assert.throws(()=>checkedConsentPacket(p,scope));assert.throws(()=>f.c.edit({model_id:id(99)}));assert.equal(f.c.form.model_id,null);assert.equal(posts(f).length,0);
});
