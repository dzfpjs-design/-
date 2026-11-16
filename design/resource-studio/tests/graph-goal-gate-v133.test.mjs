// Pure display contract fixtures; these grant no execution or Goal authority.
import test from 'node:test';
import assert from 'node:assert/strict';
import {GRAPH_GOAL_GATE_SCHEMA,inspectGraphGoalGate,graphGoalGateView} from '../src/graph-goal-gate-v133.mjs';

const projection=(status='active',reason=null,canSubmit=true,canRetry=true)=>({schema:GRAPH_GOAL_GATE_SCHEMA,status,reason,can_submit_new_result:canSubmit,can_retry_ability:canRetry});
const task=value=>({goal_spec:{purpose:'合成核查目标'},graph_goal_gate_v133:value});
const invalid=value=>assert.throws(()=>inspectGraphGoalGate(value),error=>error.code==='GRAPH_GOAL_GATE_INVALID');

test('legacy non-Goal tasks omit the display, while Goal markers require a server projection',()=>{
 assert.equal(inspectGraphGoalGate({}),null);assert.equal(graphGoalGateView({goal_spec:null}),null);
 for(const value of [{goal_spec:{}},{goal_spec:false},{native_goal_context:{}},{native_goal_context:false}])invalid(value);
 for(const value of [null,undefined,[],false])invalid({graph_goal_gate_v133:value});
});

test('server projection is closed, detached and independently controls result and ability actions',()=>{
 const value=projection('budget_blocked','max_tool_calls',true,false),input=task(value),before=structuredClone(input),checked=inspectGraphGoalGate(input);
 assert.deepEqual(checked,value);checked.can_submit_new_result=false;assert.deepEqual(input,before);
 const view=graphGoalGateView(input);assert.equal(view.gate.can_submit_new_result,true);assert.equal(view.gate.can_retry_ability,false);
 assert.match(view.detail,/能力调用额度/);assert.match(view.continuation,/重试不能解除/);
});

for(const [status,reason,label]of [['expired','deadline_expired','截止时间'],['stopped','goal_stopped','已停止'],['accepted','goal_accepted','验收结果'],['budget_blocked','max_steps','执行额度'],['unavailable','goal_context_unavailable','执行条件']])test('explains '+status+' without promising a retry can restore authority',()=>{
 const input=task(projection(status,reason,false,false)),before=structuredClone(input),view=graphGoalGateView(input);
 assert.ok(view.title.includes(label));assert.equal(view.gate.can_submit_new_result,false);assert.equal(view.gate.can_retry_ability,false);
 assert.match(view.preservation,/保存草稿/);assert.match(view.preservation,/只核对原请求/);assert.deepEqual(input,before);
 assert.ok(!JSON.stringify(view).includes('剩余额度充足'));
});

test('active communicates a per-action check, without evaluating local deadlines or private usage',()=>{
 const input={...task(projection()),graph_goal_reservations_v133:{items:{fake:{}}},goal_spec:{deadline:'1900-01-01T00:00:00Z',max_steps:1,max_tool_calls:1}};
 const view=graphGoalGateView(input);assert.equal(view.gate.status,'active');assert.match(view.detail,/每次/);assert.match(view.detail,/不表示剩余额度或新的执行授权/);assert.equal(view.continuation,null);
});

for(const [name,change]of [
 ['extra field',value=>{value.calls_used=0;}],['wrong schema',value=>{value.schema='legacy-goal';}],
 ['unknown status',value=>{value.status='unlimited';}],['raw reason',value=>{value.reason='SELECT secret FROM ledger';}],
 ['numeric permission',value=>{value.can_submit_new_result=1;}],['missing permission',value=>{delete value.can_retry_ability;}],
 ['expired permission',value=>{value.status='expired';value.reason='deadline_expired';}],
 ['mismatched reason',value=>{value.status='stopped';value.reason='max_steps';value.can_submit_new_result=false;value.can_retry_ability=false;}],
 ['exhausted calls still permit retry',value=>{value.status='budget_blocked';value.reason='max_tool_calls';}],
 ['exhausted steps still permit a human result',value=>{value.status='budget_blocked';value.reason='max_steps';value.can_retry_ability=false;}],
 ['active denies a new result',value=>{value.can_submit_new_result=false;}],
 ['exhausted calls deny remaining human work',value=>{value.status='budget_blocked';value.reason='max_tool_calls';value.can_submit_new_result=false;value.can_retry_ability=false;}]
])test('rejects '+name+' while preserving the received record',()=>{
 const value=projection();change(value);const input=task(value),before=structuredClone(input);invalid(input);assert.deepEqual(input,before);
});
