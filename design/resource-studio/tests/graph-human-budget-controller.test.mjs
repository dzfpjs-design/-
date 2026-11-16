import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {GraphHumanBudgetController} from '../src/graph-human-budget-controller.mjs';
import {continuationCanonical as canonical,continuationHash as hash} from '../src/same-goal-continuation-v312-controller.mjs';

const id=n=>'31900000-0000-4000-8000-'+String(n).padStart(12,'0');
const account={user_id:id(1),workspace_id:id(2),role:'operator'};
const task={id:id(3),instance_release_id:id(4)};
const sha=character=>character.repeat(64);
const path=[{activation_id:'a25',node_id:'review_decision',role:'business'}];
const base='/workspace/tasks/'+task.id+'/graph/human-budget';
class Memory{
 constructor(){this.values=new Map();this.fail=false;}
 getItem(key){return this.values.get(key)??null;}
 setItem(key,value){if(this.fail)throw Error('存储失败');this.values.set(key,String(value));}
}
function fixture(){
 const storage=new Memory(),session=new Memory(),calls=[];
 for(const [key,value] of Object.entries({'ga-setup-csrf':'local-token','ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id}))session.setItem(key,value);
 const context={schema:'workflow-graph-human-budget-view-v1',task_id:task.id,task_version:4,path,path_hash:sha('a'),
  records:[],permissions:{can_submit:true,can_decide:false}};
 const state={lost:false,context};
 const request=async(route,options)=>{
  assert.equal(options.headers['X-CSRF-Token'],'local-token');
  assert.equal(options.headers['X-Workspace-ID'],account.workspace_id);
  calls.push({route,method:options.method||'GET',body:options.body});
  if(route==='/me')return account;
  if(!options.method)return structuredClone(state.context);
  const original=[...storage.values.entries()].find(([key])=>key.endsWith(':original'));
  assert.ok(original);
  const saved=JSON.parse(original[1]);
  assert.equal(saved.state,'pending');
  assert.equal(canonical(saved.body),options.body);
  assert.equal(saved.body_hash,await hash(options.body,webcrypto));
  if(state.lost)throw Error('连接断开，原结果尚未读回');
  return structuredClone(state.context);
 };
 const make=()=>new GraphHumanBudgetController({account,task,request,storage,session,crypto:webcrypto,
  locks:{request:async(key,options,run)=>run()}});
 return {make,storage,session,calls,state};
}
test('lost budget write restores the exact original, refresh is read-only and retry reuses the bytes',async()=>{
 const f=fixture(),first=f.make();
 assert.equal(f.calls.length,0);
 await first.open();
 first.update({submitReason:'还剩一项原流程人工复核'});
 f.state.lost=true;
 assert.equal(await first.submit('submit'),false);
 const original=first.pending.body.request_id;
 assert.equal(first.guard(),true);
 first.dispose();
 const reopened=f.make();
 await reopened.open();
 assert.equal(reopened.pending.body.request_id,original);
 await assert.rejects(()=>reopened.submit('submit'));
 assert.equal(f.calls.filter(row=>row.method==='POST').length,1);
 f.state.lost=false;
 f.state.context={...f.state.context,task_version:5,path:[],path_hash:null,
  records:[{submission_hash:sha('b'),status:'in_review',stage:0,path,reason:'还剩一项原流程人工复核'}],
  permissions:{can_submit:false,can_decide:false}};
 assert.equal(await reopened.retry(),true);
 const writes=f.calls.filter(row=>row.method==='POST');
 assert.equal(writes.length,2);
 assert.equal(writes[0].route,base);
 assert.equal(writes[1].body,writes[0].body);
 assert.equal(reopened.pending,null);
 assert.equal(reopened.context.records[0].status,'in_review');
});

test('approval uses the explicitly displayed current submission and does not reuse another decision draft',async()=>{
 const f=fixture(),controller=f.make(),record={submission_hash:sha('b'),status:'in_review',stage:1,path,reason:'本次人工额度申请'};
 f.state.context={...f.state.context,records:[record],permissions:{can_submit:false,can_decide:true}};
 await controller.open();
 controller.update({decision:'approve',decisionReason:'核对原路径和当前审批岗位'});
 f.state.context.records=[{...record,submission_hash:sha('c')}];
 await controller.refresh();
 await assert.rejects(()=>controller.submit('decide'));
 assert.equal(f.calls.some(row=>row.method==='POST'),false);
 controller.update({decision:'return',decisionReason:'新申请需要补充原处理依据'});
 assert.equal(await controller.submit('decide'),true);
 const write=f.calls.find(row=>row.method==='POST');
 assert.equal(write.route,base+'/decide');
 assert.deepEqual(JSON.parse(write.body),{request_id:JSON.parse(write.body).request_id,expected_task_version:4,
  submission_hash:sha('c'),decision:'return',reason:'新申请需要补充原处理依据'});
 assert.equal(controller.draft.decisions[sha('b')].choice,'approve');
});

test('failed local custody and changed CSRF stop writes and hide the old task data',async()=>{
 const f=fixture(),controller=f.make();
 await controller.open();
 f.storage.fail=true;
 controller.update({submitReason:'不能丢失的人工输入'});
 assert.equal(controller.guard(),false);
 await assert.rejects(()=>controller.submit('submit'));
 f.storage.fail=false;
 controller.retrySave();
 assert.equal(controller.guard(),true);
 f.session.setItem('ga-setup-csrf','replacement');
 assert.equal(controller.view().denied,true);
 assert.equal(controller.view().context,null);
 assert.equal(controller.view().draft,null);
 assert.equal(f.calls.some(row=>row.method==='POST'),false);
});
