import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {JSDOM} from 'jsdom';
import {goalDataFixture,initializeGoalDataController,id,copy,sha} from './authored-goal-data-workflow-v288-fixture.mjs';
import {OPERATIONS,INTENTS,SCHEMAS,canonical,goalDataScopeKey,goalDataHasPending,checkedGoalDataBody,checkedGoalDataPreview,checkedGoalDataContext,checkedPreparationPolicy,freezePreparationPolicy,goalDataReadbackFromSaved} from '../src/authored-goal-data-workflow-v288-protocol.mjs';
import {decisionScopeKey,checkedDecisionBody,decisionHasPending} from '../src/authored-decision-workflow-v286-protocol.mjs';
import {decisionFixture,initializeDecisionController} from './authored-decision-workflow-v286-fixture.mjs';
let browser,prior,createController;
before(async()=>{
 browser=new JSDOM('<!doctype html><html></html>',{url:'https://goal-data-controller.invalid/'});
 const globals={window:browser.window,document:browser.window.document,navigator:browser.window.navigator,sessionStorage:browser.window.sessionStorage,localStorage:browser.window.localStorage,crypto:webcrypto,fetch:()=>{throw Error('No actual network in synthetic controller tests');}};
 prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});
 await initializeGoalDataController();
 await initializeDecisionController();
 ({createAuthoredWorkflowControllerClass:createController}=await import('../src/authored-workflow-controller-engine.mjs'));
});
after(()=>{browser?.window.close();for(const[key,value]of prior||[]){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}});

test('fixed V288 family requires six actual selections and preserves independent policy and intent drafts',async()=>{
 const f=goalDataFixture(),c=f.make();await c.start();
 assert.equal(c.key,goalDataScopeKey(f.scope));assert.notEqual(c.key,decisionScopeKey(f.scope));
 assert.throws(()=>createController('v289'));assert.throws(()=>createController({DECISION_BASE:'/anything'}));
 assert.deepEqual(Object.keys(c.draft.tool_bindings),OPERATIONS);
 assert.ok(Object.values(c.draft.tool_bindings).every(pin=>pin.capability_id===''&&pin.actor_id===''));
 assert.equal(c.draft.reviewer_id,'');assert.equal(c.draft.preparation_policy,null);
 await assert.rejects(c.buildPreview());assert.equal(f.state.posts.length,0);
 await f.choose(c);const initial=copy(c.draft.preparation_policy),{policy_hash,...unsigned}=initial;
 assert.equal(policy_hash,sha(canonical(unsigned)));assert.equal(initial.timezone,'Asia/Shanghai');
 for(const intent of INTENTS){c.setDecisionIntent(intent);await c.buildPreview();assert.equal(c.body().decision_intent,intent);assert.deepEqual(c.body().preparation_policy,initial);}
 assert.equal(checkedGoalDataBody(c.body()).schema,SCHEMAS.previewCommand);assert.throws(()=>checkedDecisionBody(c.body()));assert.equal(Object.hasOwn(c.body(),'preparation_policy_choice'),false);
 const saved=copy(c.draft);c.dispose();const cold=f.make();await cold.start();
 assert.deepEqual(cold.draft,saved);assert.equal(cold.preview,null);assert.equal(cold.acknowledged,false);
 assert.equal(JSON.parse(f.storage.getItem(c.key+':draft')).schema,SCHEMAS.draft);assert.equal(f.state.posts.length,0);
});

test('policy choices reject impossible acquisition and stale async edits without guessing a Goal window',async()=>{
 const f=goalDataFixture(),c=await f.ready(),before=copy(c.draft);
 await assert.rejects(c.setPreparationPolicy({time_basis:'fixed_window',allow_acquisition:true,max_age_seconds:null}));
 await assert.rejects(c.setPreparationPolicy({time_basis:'rolling_latest',allow_acquisition:false,max_age_seconds:null}));
 await assert.rejects(c.setPreparationPolicy({time_basis:'rolling_latest',allow_acquisition:false,max_age_seconds:3600,timezone:'UTC'}));
 assert.deepEqual(c.draft,before);assert.throws(()=>c.setDecisionIntent('automatic_execution'));
 await c.setPreparationPolicy({time_basis:'fixed_window',allow_acquisition:false,max_age_seconds:7200});
 assert.equal(c.draft.preparation_policy.max_age_seconds,7200);assert.equal(c.preview,null);
 const valid=copy(c.draft.preparation_policy);valid.timezone='invalid/zone';assert.throws(()=>checkedPreparationPolicy(valid));
 let resolve;const originalSha=c.sha;c.sha=()=>new Promise(done=>{resolve=done;});
 const computing=c.setPreparationPolicy({time_basis:'rolling_latest',allow_acquisition:true,max_age_seconds:120});
 c.setDecisionIntent('operational_action');resolve('a'.repeat(64));await assert.rejects(computing,/选项已变化/);
 c.sha=originalSha;assert.equal(c.draft.preparation_policy.time_basis,'fixed_window');assert.equal(c.draft.decision_intent,'operational_action');
 c.editPreparationPolicyChoice({time_basis:'rolling_latest',max_age_seconds:''});assert.equal(c.draft.preparation_policy,null);
 c.dispose();const cold=f.make();await cold.start();assert.equal(cold.draft.preparation_policy_choice.max_age_seconds,'');assert.equal(cold.draft.preparation_policy_choice.time_basis,'rolling_latest');
 await assert.rejects(cold.confirmPreparationPolicy());assert.equal(cold.draft.preparation_policy,null);assert.equal(f.state.posts.length,0);
 const blocked=f.context();blocked.human_roles=[];blocked.flow=null;blocked.can_preview=false;blocked.reason='请先保存原人工流程';
 assert.equal(checkedGoalDataContext(blocked,f.scope),blocked);
 const policy=await freezePreparationPolicy({time_basis:'fixed_window',allow_acquisition:false,max_age_seconds:null},async bytes=>sha(bytes));assert.equal(policy.max_age_seconds,null);
});

test('six-port preview binds both platform reviews and rejects foreign version fields and actions',async()=>{
 const f=goalDataFixture(),c=await f.ready(),body=c.body();
 assert.equal(checkedGoalDataPreview(c.preview,body,f.scope),c.preview);assert.equal(c.preview.binding.graph.entry,'context_reader');
 assert.equal(c.preview.projection.steps.filter(row=>row.kind==='ability').length,6);
 for(const node of ['review_data','review_decision'])assert.equal(c.preview.binding.graph.nodes.find(row=>row.id===node).actor_id,id(4));
 for(const mutate of [v=>v.schema='authored-decision-workflow-preview-command-v286',v=>v.node_actions.local_prepare='data',v=>v.external_node_ids.push('evaluation_packet'),v=>v.tool_bindings.local_prepare=copy(v.tool_bindings.context_reader)]){
  const altered=copy(body);mutate(altered);assert.throws(()=>checkedGoalDataBody(altered));
 }
 for(const mutate of [v=>v.binding.workspace_id=id(999),v=>v.binding.graph.nodes.find(row=>row.id==='review_data').actor_id=id(3),v=>v.projection.steps.find(row=>row.node_id==='review_decision').actor_id=id(3),v=>v.binding.preparation_policy.max_age_seconds=99]){
  const altered=copy(c.preview);mutate(altered);assert.throws(()=>checkedGoalDataPreview(altered,body,f.scope));
 }
 const before=copy(c.draft);f.state.caps.find(cap=>cap.operation==='evaluation_packet').hash='f'.repeat(64);
 await c.refresh();assert.deepEqual(c.draft,before);await assert.rejects(c.buildPreview(),/发布版本已变化/);assert.equal(f.state.posts.length,0);
});

test('saved lost response cold recovery uses exact original GET and independent adoption readback',async()=>{
 const f=goalDataFixture(),c=await f.ready();c.acknowledge(true);f.state.lose=true;
 await assert.rejects(c.adopt());const original=copy(c.pending);
 assert.equal(original.body.schema,SCHEMAS.adoptCommand);assert.equal(goalDataHasPending(f.scope,f.storage),true);
 assert.equal(f.state.posts.length,1);assert.equal(f.state.adoptions.length,1);
 c.dispose();const cold=f.make();await cold.start();
 assert.equal(cold.pending,null);assert.equal(cold.configurationReadbackPending,false);assert.equal(cold.guard(),true);
 assert.equal(f.state.posts.length,1);assert.equal(f.adopted.length,1);assert.equal(goalDataHasPending(f.scope,f.storage),false);
 assert.ok(f.state.gets[0].endsWith(original.body.request_id+'?expected_hash='+original.sha256));
 const backup=JSON.parse(f.storage.getItem(c.key+':original:'+original.body.request_id));assert.equal(backup.raw,original.raw);
 assert.equal(cold.result.can_execute,false);assert.equal(cold.result.cloud_authorized,false);
});

test('unknown unsaved original remains GET only and explicit continuation reuses exact raw bytes',async()=>{
 const f=goalDataFixture(),c=await f.ready();c.acknowledge(true);f.state.unknown=true;
 await assert.rejects(c.adopt());const original=copy(c.pending);f.storage.removeItem(c.key+':pending');
 assert.equal(goalDataHasPending(f.scope,f.storage),true);assert.equal(goalDataHasPending({...f.scope,actor_id:id(999)},f.storage),false);
 c.dispose();const cold=f.make();await cold.start();assert.equal(cold.notFound,true);assert.equal(f.state.posts.length,1);
 await cold.recover(false);assert.equal(f.state.posts.length,1);assert.equal(cold.pending.raw,original.raw);
 f.state.unknown=false;await cold.recover(true);assert.deepEqual(f.state.posts,[original.raw,original.raw]);
 assert.equal(f.state.adoptions.length,1);assert.equal(cold.configurationReadbackPending,false);
});

test('save failures readback conflict and changed identity retain work and cannot create hidden authorization',async()=>{
 const f=goalDataFixture(),c=await f.ready();c.acknowledge(true);f.storage.failSet=true;
 await assert.rejects(c.adopt());assert.equal(f.state.posts.length,0);f.storage.failSet=false;c.persist();
 c.acknowledge(true);f.state.readbackError=true;await assert.rejects(c.adopt());
 assert.equal(c.pending,null);assert.equal(c.configurationReadbackPending,true);assert.equal(c.guard(),false);
 f.state.readbackError=false;await c.applyConfiguration();assert.equal(f.state.posts.length,1);assert.equal(c.guard(),true);
 const other={revision:c.result.accepted_revision+1,configuration:{businesses:[f.scope.business_name],authoredWorkflowGraphs:{[f.scope.business_name]:{...copy(c.result.origin),adoption_id:id(999)}}}};
 assert.throws(()=>goalDataReadbackFromSaved(other,c.result,f.scope),error=>error.code==='AUTHORED_GOAL_DATA_READBACK_SUPERSEDED');
 const g=goalDataFixture(),old=await g.ready();g.state.identity.user_id=id(999);await assert.rejects(old.buildPreview());
 assert.equal(old.denied,true);assert.equal(old.context,null);assert.equal(old.preview,null);assert.equal(g.state.posts.length,0);
});

for(const[family,fixture,hasPending]of [['V286',decisionFixture,decisionHasPending],['V288',goalDataFixture,goalDataHasPending]]){
 test(family+' successful adoption readback obligation survives failure cold remount and missing legacy marker',async()=>{
  const f=fixture(),c=await f.ready();c.acknowledge(true);f.state.readbackError=true;await assert.rejects(c.adopt());
  const result=copy(c.result),marker=c.key+':configuration-readback:'+result.request_id,original=f.storage.getItem(c.key+':original:'+result.request_id);
  assert.equal(c.pending,null);assert.equal(JSON.parse(f.storage.getItem(marker)).disposition,'required');assert.equal(hasPending(f.scope,f.storage),true);
  c.dispose();const cold=f.make();await cold.start();
  assert.equal(cold.configurationReadbackPending,true);assert.equal(cold.result.id,result.id);assert.equal(cold.guard(),false);
  await assert.rejects(cold.buildPreview());assert.equal(f.state.posts.length,1);assert.equal(f.storage.getItem(cold.key+':original:'+result.request_id),original);
  f.state.readbackError=false;await cold.applyConfiguration();assert.equal(JSON.parse(f.storage.getItem(marker)).disposition,'confirmed');assert.equal(cold.guard(),true);assert.equal(hasPending(f.scope,f.storage),false);
  cold.dispose();f.storage.removeItem(marker);f.state.readbackError=true;const legacy=f.make();await legacy.start();
  assert.equal(legacy.configurationReadbackPending,true);assert.equal(legacy.guard(),false);assert.equal(f.state.posts.length,1);assert.equal(hasPending(f.scope,f.storage),true);
  f.state.readbackError=false;await legacy.applyConfiguration();assert.equal(legacy.guard(),true);assert.equal(f.state.posts.length,1);
  legacy.dispose();const confirmed=f.make();await confirmed.start();assert.equal(confirmed.configurationReadbackPending,false);assert.equal(confirmed.guard(),true);
 });
 test(family+' crashes writing required or received preserve original GET-only recovery and readback duty',async()=>{
  for(const suffix of [':configuration-readback:',':received:']){
   const f=fixture(),c=await f.ready();c.acknowledge(true);const write=f.storage.setItem.bind(f.storage);let failed=false;
   f.storage.setItem=(key,value)=>{if(!failed&&key.includes(suffix)){failed=true;throw Error('synthetic journal write interruption');}write(key,value);};
   await assert.rejects(c.adopt());assert.equal(failed,true);assert.ok(c.pending);assert.equal(f.state.posts.length,1);assert.equal(c.guard(),false);
   const original=copy(c.pending);f.storage.setItem=write;c.dispose();f.state.readbackError=true;const cold=f.make();await cold.start();
   assert.equal(cold.pending,null);assert.equal(cold.configurationReadbackPending,true);assert.equal(cold.guard(),false);assert.equal(f.state.posts.length,1);
   assert.ok(f.state.adoptions.some(row=>row.origin.command_hash===sha(original.raw)));
   f.state.readbackError=false;await cold.applyConfiguration();assert.equal(cold.guard(),true);assert.equal(f.state.posts.length,1);assert.equal(hasPending(f.scope,f.storage),false);
  }
 });
}
