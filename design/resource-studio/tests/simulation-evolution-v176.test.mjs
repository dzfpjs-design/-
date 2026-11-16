// Synthetic browser journal and HTTP transport tests. Not API/PG/browser proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto,randomUUID} from 'node:crypto';
import {SimulationEvolutionClientV176,EVOLUTION_BASE_V176,evolutionSHAV176,evolutionKeyV176,checkEvolutionRecordV176,candidateThresholdV176,evaluationReadinessV176} from '../src/simulation-evolution-ui-v176.mjs';
import {numericValue} from '../src/typed-numbers-v132.mjs';

const ids={actor:'11111111-1111-4111-8111-111111111111',workspace:'22222222-2222-4222-8222-222222222222',environment:'44444444-4444-4444-8444-444444444444',observation:'55555555-5555-4555-8555-555555555555',feedback:'77777777-7777-4777-8777-777777777777',study:'88888888-8888-4888-8888-888888888888'};
const mark={simulation:true,origin_kind:'simulated_equipment',field_verified:false,test_only:true,production_activation:false};
class Storage{
 constructor(){this.values=new Map();this.rejectPending=false;this.rejectDraft=false;}
 get length(){return this.values.size;}
 key(index){return [...this.values.keys()][index]??null;}
 getItem(key){return this.values.get(key)??null;}
 setItem(key,value){if(this.rejectPending&&key.endsWith(':pending')||this.rejectDraft&&key.includes(':draft:'))throw Error('synthetic storage quota');this.values.set(key,String(value));}
 removeItem(key){this.values.delete(key);}
}
const failure=(status,message='synthetic definitive rejection')=>Object.assign(Error(message),{status});
function feedback(scope,body,version=1){return {id:ids.feedback,workspace_id:scope.workspace_id,actor_id:scope.actor_id,version,environment_id:ids.environment,observation_id:ids.observation,...mark,record_hash:'a'.repeat(64),data:{schema:'simulation-feedback-record-v176',...mark,label:body.label,reason:body.reason,basis:{schema:'simulation-feedback-basis-v176',...mark,environment_id:ids.environment,observation_id:ids.observation,origin:{instance_id:scope.instance_id},field:'temperature_c',value:38,unit:'°C',observed_at:'2026-10-23T00:00:00+00:00'}}};}
function study(scope,body){return {id:ids.study,workspace_id:scope.workspace_id,actor_id:scope.actor_id,version:1,environment_id:ids.environment,status:'frozen',frozen_hash:'b'.repeat(64),record_hash:'c'.repeat(64),...mark,data:{schema:'simulation-study-record-v176',...mark,freeze:{schema:'simulation-study-freeze-v176',...mark,environment_id:ids.environment,title:body.title,reason:body.reason,baseline_manifest:{specification:{rules:[{field:'temperature_c',threshold:35,operator:'gt'}]}},candidate_manifest:{specification:{rules:[{field:'temperature_c',threshold:body.candidate_threshold,operator:'gt'}]}},samples:[0,1,2,3].map((value)=>({observation_id:randomUUID(),sample_id:randomUUID(),invocation_id:randomUUID(),feedback_id:randomUUID(),label:value%2?'should_not_alert':'should_alert',value:38-value,unit:'°C',observed_at:`2026-10-23T00:00:0${value}+00:00`,reason:'synthetic label',basis:{origin:{instance_id:scope.instance_id}}}))},result:null}};}
function scene(){
 const storage=new Storage(),scope={actor_id:ids.actor,workspace_id:ids.workspace,instance_id:randomUUID(),role:'operator'},requests=[],receipts=new Map(),studies=new Map();let feedbackRows=[],me={user_id:scope.actor_id,workspace_id:scope.workspace_id,role:scope.role},postHook=null,lookupHook=null,filteredFeedback=false;
 const clientCrypto={subtle:webcrypto.subtle,randomUUID};
 const tails=new Map(),locks={request(key,_options,run){const next=(tails.get(key)||Promise.resolve()).then(run),tail=next.then(()=>{},()=>{});tails.set(key,tail);return next.finally(()=>{if(tails.get(key)===tail)tails.delete(key);});}};
 const save=async(path,request)=>{
  const body=JSON.parse(request.body);let result,operation;
  if(path===EVOLUTION_BASE_V176+'/feedback'){
   const expected=feedbackRows.find(row=>row.observation_id===body.observation_id&&row.actor_id===scope.actor_id)?.version||0;
   if(body.expected_version!==expected)throw failure(409,'feedback version changed');
   result=feedback(scope,body,expected+1);feedbackRows=[result];operation='feedback.save';
  }else if(path===EVOLUTION_BASE_V176+'/studies'){
   result=study(scope,body);studies.set(result.id,result);operation='study.create';
  }else throw Error('unexpected synthetic POST');
  receipts.set(body.request_id,{...mark,found:true,request_id:body.request_id,operation,target_id:result.id,body_sha256:await evolutionSHAV176(request.body,clientCrypto),result});return result;
 };
 const api=async(path,request)=>{
  requests.push({path,...request});
  if(path==='/me')return {...me};
  if(request?.method==='POST')return postHook?postHook(path,request):save(path,request);
  if(path.includes('/evolution/requests/')){if(lookupHook)return lookupHook(path);const identity=path.split('/evolution/requests/')[1].split('?')[0];return receipts.get(identity)||{...mark,found:false,request_id:identity};}
  if(path.startsWith(EVOLUTION_BASE_V176+'/feedback?')){const observationID=new URL('https://synthetic.invalid'+path).searchParams.get('observation_id');return filteredFeedback&&observationID?{...mark,rows:feedbackRows.filter(row=>row.observation_id===observationID),limit:100,observation_id:observationID,complete:true}:{...mark,rows:feedbackRows.slice(0,100),limit:100};}
  if(path.startsWith(EVOLUTION_BASE_V176+'/studies/')){const identity=path.split('/studies/')[1];if(!studies.has(identity))throw failure(404,'synthetic study unavailable');return studies.get(identity);}
  throw Error('unexpected synthetic read '+path);
 };
 const create=options=>new SimulationEvolutionClientV176(api,scope,{storage,locks,crypto:clientCrypto,...options});
 const detail={observation_id:ids.observation,processing_status:'processed',origin:{environment_id:ids.environment,instance_id:scope.instance_id}};
 return {scope,storage,api,create,requests,receipts,studies,detail,save,clientCrypto,postCount:()=>requests.filter(row=>row.method==='POST').length,setPost:value=>{postHook=value;},setLookup:value=>{lookupHook=value;},setMe:value=>{me=value;},setFeedback:value=>{feedbackRows=value;},setFilteredFeedback:value=>{filteredFeedback=value;}};
}
const human={expected_version:0,label:'should_alert',reason:'这次模拟高温应当提醒；这是机制标注。'};

test('feedback raw nonce and SHA are written and read before POST, then original GET settles',async()=>{
 const s=scene(),c=s.create();s.setPost(async(path,request)=>{const pending=JSON.parse(s.storage.getItem(evolutionKeyV176(s.scope)+':pending'));assert.equal(pending.raw,request.body);assert.equal(pending.sha256,await evolutionSHAV176(request.body,s.clientCrypto));assert.equal(s.storage.getItem(c.key+':original:'+pending.request_id),JSON.stringify(pending));return s.save(path,request);});
 const value=await c.saveFeedback(ids.environment,s.detail,human);assert.equal(value.found,true);assert.equal(value.result.data.reason,human.reason);assert.equal(s.postCount(),1);assert.equal(await c.pending(),null);
 const firstPost=s.requests.findIndex(row=>row.method==='POST');assert.ok(s.requests.slice(0,firstPost).some(row=>row.path.includes('/evolution/requests/')));
});
test('a dropped successful response is resolved only by original GET, with no second POST',async()=>{
 const s=scene(),c=s.create();s.setPost(async(path,request)=>{await s.save(path,request);throw Error('synthetic response dropped');});
 const value=await c.saveFeedback(ids.environment,s.detail,human);assert.equal(value.found,true);assert.equal(s.postCount(),1);const remount=s.create();assert.equal(await remount.pending(),null);assert.equal(s.postCount(),1);
});
test('unknown response survives remount, preserves raw body and blocks another request',async()=>{
 const s=scene(),c=s.create();s.setPost(async()=>{throw Error('synthetic disconnect before acknowledgement');});await assert.rejects(c.saveFeedback(ids.environment,s.detail,human));const original=await c.pending(),remount=s.create();assert.equal((await remount.pending()).raw,original.raw);assert.equal(s.postCount(),1);await assert.rejects(remount.stage('feedback.save',ids.environment,ids.observation,{...human,observation_id:ids.observation}));assert.equal(s.postCount(),1);await assert.rejects(remount.releaseRejected(original));
});
test('wrong original operation or body SHA never releases pending',async()=>{
 for(const field of ['operation','body_sha256']){const s=scene(),c=s.create(),record=await c.stage('feedback.save',ids.environment,ids.observation,{...human,observation_id:ids.observation});await s.save(record.path,{body:record.raw});const saved=s.receipts.get(record.request_id);s.setLookup(()=>({...saved,[field]:field==='operation'?'study.create':'0'.repeat(64)}));await assert.rejects(c.settle(record));assert.equal((await c.pending()).request_id,record.request_id);}
});
test('account or workspace change before send is fenced without POST',async()=>{
 const s=scene(),c=s.create(),record=await c.stage('feedback.save',ids.environment,ids.observation,{...human,observation_id:ids.observation});s.setMe({user_id:randomUUID(),workspace_id:s.scope.workspace_id,role:s.scope.role});await assert.rejects(c.sendOriginal(record));assert.equal(s.postCount(),0);assert.equal((await c.pending()).request_id,record.request_id);
});
test('scope disposal during lookup cannot clear another page pending request',async()=>{
 const s=scene();let live=true;const c=s.create({current:()=>live}),record=await c.stage('feedback.save',ids.environment,ids.observation,{...human,observation_id:ids.observation});await s.save(record.path,{body:record.raw});s.setLookup(()=>{live=false;return s.receipts.get(record.request_id);});await assert.rejects(c.settle(record));assert.ok(s.storage.getItem(c.key+':pending'));
});
test('partial storage failure retains an orphan original and requires persistence before send',async()=>{
 const s=scene(),c=s.create();s.storage.rejectPending=true;await assert.rejects(c.stage('feedback.save',ids.environment,ids.observation,{...human,observation_id:ids.observation}));const remount=s.create(),record=await remount.pending();assert.ok(record);assert.equal(remount.dirty,true);await assert.rejects(remount.sendOriginal(record));assert.equal(s.postCount(),0);s.storage.rejectPending=false;await remount.persistPending();assert.equal(remount.dirty,false);assert.equal((await remount.sendOriginal(record)).found,true);assert.equal(s.postCount(),1);
});
test('stale feedback CAS is detected before a new command is staged',async()=>{
 const s=scene(),c=s.create();s.setFeedback([feedback(s.scope,human,2)]);await assert.rejects(c.saveFeedback(ids.environment,s.detail,human),/版本/);assert.equal(s.postCount(),0);assert.equal(await c.pending(),null);
});
test('cross instance sample and fake field authority are rejected',async()=>{
 const s=scene(),c=s.create();await assert.rejects(c.saveFeedback(ids.environment,{...s.detail,origin:{...s.detail.origin,instance_id:randomUUID()}},human));assert.equal(s.postCount(),0);const row=feedback(s.scope,human);row.data.basis.origin.instance_id=randomUUID();assert.throws(()=>checkEvolutionRecordV176(row,'feedback',s.scope,ids.environment));row.data.basis.origin.instance_id=s.scope.instance_id;row.production_activation=true;assert.throws(()=>checkEvolutionRecordV176(row,'feedback',s.scope,ids.environment));
});
test('candidate numeric token preserves a user decimal and invalid partial numbers remain unsent',async()=>{
 assert.equal(numericValue(candidateThresholdV176('35.0')),35);assert.throws(()=>candidateThresholdV176('1e'));assert.throws(()=>candidateThresholdV176('Infinity'));const s=scene(),c=s.create();const record=await c.stage('study.create',ids.environment,null,{environment_id:ids.environment,title:'模拟分时比较',feedback_refs:[{feedback_id:ids.feedback,expected_version:1}],candidate_threshold:candidateThresholdV176('35.0'),reason:'保留原数值类型'});assert.match(record.raw,/"candidate_threshold":35\.0/);assert.equal(s.postCount(),0);
});
test('definitive rejection can be archived only after original GET remains negative',async()=>{
 const s=scene(),c=s.create();s.setPost(async()=>{throw failure(409);});await assert.rejects(c.saveFeedback(ids.environment,s.detail,human));const record=await c.pending();await c.releaseRejected(record);assert.equal(await c.pending(),null);assert.ok(s.storage.getItem(c.key+':retired:'+record.request_id));assert.ok(s.storage.getItem(c.key+':original:'+record.request_id));
});
test('a restored study ref loads current server state rather than an original cached result',async()=>{
 const s=scene(),c=s.create();const result=await c.createStudy(ids.environment,{title:'模拟阈值方案',threshold:'36.0',reason:'比较目的',feedbackIDs:[ids.feedback]},[feedback(s.scope,human)]);assert.equal(result.found,true);const current=s.studies.get(ids.study);current.version=2;current.status='completed';current.data.result={...mark,schema:'simulation-local-threshold-result-v176',study_hash:current.frozen_hash,status:'completed',per_sample:[],production_rule_changes:0,shadow_started:false};const remount=s.create();const loaded=await remount.studies(ids.environment);assert.equal(loaded.rows[0].version,2);assert.equal(loaded.rows[0].status,'completed');assert.ok(s.requests.some(row=>row.path===EVOLUTION_BASE_V176+'/studies/'+ids.study));assert.equal(s.postCount(),1);
});
test('human causes and an incomplete threshold survive remount with independent tab drafts',()=>{
 const s=scene(),a=s.create(),b=s.create();const value={environmentID:ids.environment,feedbackDrafts:{[ids.observation]:{...human,reason:'人工原因不应丢失'}},studyDrafts:{[ids.environment]:{title:'未完稿',threshold:'1e',reason:'继续填写',feedbackIDs:[]}},selectedSamples:{}};a.saveDraft(value);b.saveDraft({...value,environmentID:''});const drafts=s.create().readDrafts();assert.equal(drafts.length,2);assert.equal(drafts.find(row=>row.owner===a.owner).value.feedbackDrafts[ids.observation].reason,'人工原因不应丢失');assert.equal(drafts.find(row=>row.owner===a.owner).value.studyDrafts[ids.environment].threshold,'1e');assert.equal(s.postCount(),0);
});
test('fewer clear samples and tied time boundaries do not enable evaluation',()=>{
 const s=scene(),row=study(s.scope,{title:'t',reason:'r',candidate_threshold:36});assert.equal(evaluationReadinessV176(row).ready,true);row.data.freeze.samples[3].label='uncertain';assert.equal(evaluationReadinessV176(row).ready,false);row.data.freeze.samples[3].label='should_alert';row.data.freeze.samples.forEach(item=>{item.observed_at='2026-10-23T00:00:00Z';});assert.equal(evaluationReadinessV176(row).ready,false);
});
test('changed study version is refused before allocating another command',async()=>{
 const s=scene(),c=s.create(),old=study(s.scope,{title:'t',reason:'r',candidate_threshold:36});s.studies.set(old.id,{...old,version:2});await assert.rejects(c.evaluate(old),/版本/);assert.equal(await c.pending(),null);assert.equal(s.postCount(),0);
});

test('two tabs probe again under the send lock and produce only one POST',async()=>{
 const s=scene(),a=s.create(),b=s.create(),record=await a.stage('feedback.save',ids.environment,ids.observation,{...human,observation_id:ids.observation});let initial=0,release;const gate=new Promise(resolve=>{release=resolve;});
 s.setLookup(async()=>{const saved=s.receipts.get(record.request_id);if(saved)return saved;if(initial<2){initial++;if(initial===2)release();await gate;}return {...mark,found:false,request_id:record.request_id};});
 const results=await Promise.all([a.sendOriginal(record),b.sendOriginal(record)]);assert.ok(results.every(row=>row.found));assert.equal(s.postCount(),1);assert.equal(await a.pending(),null);
});
test('failed draft writes survive a scope switch in isolated memory without leaking to another instance',()=>{
 const s=scene(),client=s.create();s.storage.rejectDraft=true;assert.throws(()=>client.saveDraft({reason:'未落盘的人工判断原文'}));client.dispose();const other=new SimulationEvolutionClientV176(s.api,{...s.scope,instance_id:randomUUID()},{storage:s.storage,crypto:s.clientCrypto});assert.equal(other.readDrafts().length,0);const restored=s.create().readDrafts();assert.equal(restored[0].persisted,false);assert.equal(restored[0].value.reason,'未落盘的人工判断原文');
});
test('a truncated feedback list cannot imply version zero; exact observation lookup recovers older CAS',async()=>{
 const s=scene(),client=s.create();const newer=Array.from({length:100},()=>{const row=feedback(s.scope,human,1);row.id=randomUUID();row.observation_id=randomUUID();row.data.basis.observation_id=row.observation_id;return row;}),old=feedback(s.scope,human,3);s.setFeedback([...newer,old]);await assert.rejects(client.saveFeedback(ids.environment,s.detail,{...human,expected_version:0}),/上限/);assert.equal(s.postCount(),0);s.setFilteredFeedback(true);assert.equal((await client.saveFeedback(ids.environment,s.detail,{...human,expected_version:3})).result.version,4);assert.equal(s.postCount(),1);
});
