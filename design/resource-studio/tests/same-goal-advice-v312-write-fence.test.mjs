// Actual existing transport fence. IAM response is explicit IO; no backend,
// browser, model or successful mutation is simulated.
import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {SameGoalAdviceControllerV310} from '../src/same-goal-advice-v310-controller.mjs';

const id=n=>'31200000-0000-4000-8000-'+String(n).padStart(12,'0');
for(const change of ['dispose','suspend','csrf'])test('completed identity read cannot send after '+change,async()=>{
 const account={user_id:id(1),workspace_id:id(2),role:'admin'};
 const task={id:id(3),instance_id:id(4),instance_release_id:id(5)};
 const rows=new Map(Object.entries({'ga-setup-csrf':'explicit-fixture',
  'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id}));
 const session={getItem:key=>rows.get(key)??null,setItem:(key,value)=>rows.set(key,String(value))};
 const calls=[];
 const controller=new SameGoalAdviceControllerV310({account,task,session,storage:session,crypto:webcrypto,
  request:async(path,options={})=>{calls.push({path,method:options.method||'GET'});
   if(path==='/me')return structuredClone(account);
   throw Error('The old view must never reach its write boundary');}});
 const identity=controller.identity.bind(controller);
 controller.identity=async(...args)=>{const result=await identity(...args);
  if(change==='dispose')controller.dispose();
  else if(change==='suspend')controller.suspended=true;
  else rows.set('ga-setup-csrf','new-fixture-session');
  return result;};
 await assert.rejects(controller.request('/unit-write-boundary',{method:'POST',body:'{}'}));
 assert.deepEqual(calls,[{path:'/me',method:'GET'}]);
 if(change==='csrf')assert.equal(controller.view().denied,true);
 controller.dispose();
});

class Memory{
 constructor(){this.rows=new Map();}
 get length(){return this.rows.size;}
 key(index){return [...this.rows.keys()][index]??null;}
 getItem(key){return this.rows.get(key)??null;}
 setItem(key,value){this.rows.set(key,String(value));}
 removeItem(key){this.rows.delete(key);}
}
async function originalFixture(operation='prepare'){
 const account={user_id:id(1),workspace_id:id(2),role:'admin'};
 const task={id:id(3),instance_id:id(4),instance_release_id:id(5)};
 const storage=new Memory(),session=new Memory(),state={allowed:true,calls:0};
 for(const [key,value]of Object.entries({'ga-setup-csrf':'explicit-fixture',
  'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id}))session.setItem(key,value);
 const controller=new SameGoalAdviceControllerV310({account,task,session,storage,crypto:webcrypto,
  beforePlanChange:()=>state.allowed,request:async()=>{state.calls++;throw Error('Boundary test must not request IO');}});
 controller.initialize();controller.planId=id(6);controller.draft.prepare_reason='原页面人工输入';controller.persist();
 const body=operation==='prepare'?{request_id:id(10),expected_goal_hash:'a'.repeat(64),
  expected_mature_head_hash:'b'.repeat(64),document_source:{kind:'approved_source_set',
   source_set_ref:{source_set_id:id(7),source_set_hash:'c'.repeat(64)}},reason:'另一窗口已有的原申请'}:
  {request_id:id(10),expected_plan_version:1,reason:'只核对同一原申请'};
 const row=await controller.stage(operation,body,{planId:operation==='prepare'?null:id(6)});
 return {controller,storage,state,row,proof:{kind:'server_command_receipt',body_hash:row.body_hash}};
}
test('receipt changing selection cannot discard an unsafe child draft or consume its pending original',async()=>{
 const h=await originalFixture();h.state.allowed=false;
 const pending=h.storage.getItem(h.controller.key+':pending');
 await assert.rejects(h.controller.finish(h.row,id(8),'available',h.proof,{deferRefresh:true}),/草案尚未可靠保存/);
 assert.equal(h.controller.planId,id(6));assert.equal(h.controller.draft.prepare_reason,'原页面人工输入');
 assert.equal(h.storage.getItem(h.controller.key+':pending'),pending);
 assert.equal(h.storage.getItem(h.controller.key+':received:'+h.row.request_id),null);
 assert.ok(h.storage.getItem(h.controller.key+':original:'+h.row.request_id));assert.equal(h.state.calls,0);
 h.controller.dispose();
});
test('child safety is rechecked after awaited original validation before receipt or selection mutation',async()=>{
 const h=await originalFixture(),original=h.controller.storedOriginal.bind(h.controller);
 h.controller.storedOriginal=async row=>{const result=await original(row);h.state.allowed=false;return result;};
 await assert.rejects(h.controller.finish(h.row,id(8),'available',h.proof,{deferRefresh:true}));
 assert.equal(h.controller.planId,id(6));assert.equal(h.storage.getItem(h.controller.key+':received:'+h.row.request_id),null);
 assert.equal(h.controller.pending.request_id,h.row.request_id);assert.equal(h.state.calls,0);h.controller.dispose();
});
test('same-selection receipt may be durably recovered while a child needs local-save repair',async()=>{
 const h=await originalFixture('cancel');h.state.allowed=false;
 await h.controller.finish(h.row,id(6),'available',h.proof,{deferRefresh:true});
 assert.equal(h.controller.planId,id(6));assert.equal(h.controller.pending,null);
 assert.equal(h.storage.getItem(h.controller.key+':pending'),null);
 assert.ok(h.storage.getItem(h.controller.key+':received:'+h.row.request_id));assert.equal(h.state.calls,0);
 h.controller.dispose();
});
test('explicit selection checks child safety before mutating parent input or retaining a different selection',async()=>{
 const h=await originalFixture();h.storage.removeItem(h.controller.key+':pending');
 h.storage.removeItem(h.controller.key+':original:'+h.row.request_id);h.controller.pending=null;
 h.controller.list={records:[{plan_id:id(8)}]};h.state.allowed=false;
 const originalDraft=structuredClone(h.controller.draft),size=h.storage.length;
 await assert.rejects(h.controller.selectPlan(id(8)),/草案尚未可靠保存/);
 assert.equal(h.controller.planId,id(6));assert.deepEqual(h.controller.draft,originalDraft);
 assert.equal(h.storage.length,size);assert.equal(h.state.calls,0);h.controller.dispose();
});
