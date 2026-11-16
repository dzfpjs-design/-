import test from 'node:test';
import assert from 'node:assert/strict';
import {nestedSet,nestedArrayEdit,nestedPhase,nestedFlow,nestedCapacity,nestedCitationUses,nestedPointer} from '../src/nested-flow-review.mjs';
import {nestedPlan,STEP_PATH,A,B} from './nested-flow-review-fixture.mjs';
const copy=value=>structuredClone(value);

test('editing nested roles and ports preserves complete phases, branches, conditions and retained values',()=>{
 const value=nestedPlan();value.retained_human_detail={unknown:[null,false,0,1.0]};const before=copy(value);
 const first=nestedSet(value,[...STEP_PATH,0,'role'],'重新指定的通知岗位');
 const next=nestedSet(first,[...STEP_PATH,0,'inputs'],['完整第一项资料','第二项资料']);
 const phase=next.business.flows[0].phases[0];assert.equal(phase.kind,'decision');assert.equal(phase.branches[0].phases[0].kind,'parallel');assert.equal(phase.branches[0].phases[0].branches[1].phases[0].kind,'repeat');assert.deepEqual(phase.branches[1].phases,[]);assert.equal(phase.branches[0].phases[0].wait_mode,'all');assert.equal(phase.branches[0].phases[0].branches[0].phases[0].steps[0].role,'重新指定的通知岗位');assert.deepEqual(next.retained_human_detail,before.retained_human_detail);assert.equal(next.business.flows[0].nodes,undefined);assert.deepEqual(value,before);
});

test('moving and deleting whole nested items never flattens children or mutates the prior draft',()=>{
 const value=nestedPlan(),before=copy(value),moved=nestedArrayEdit(value,STEP_PATH,{kind:'down',index:0});
 const steps=moved.business.flows[0].phases[0].branches[0].phases[0].branches[0].phases[0].steps;assert.deepEqual(steps.map(step=>step.name),['记录通知回执','通知责任人员']);assert.deepEqual(steps[1],before.business.flows[0].phases[0].branches[0].phases[0].branches[0].phases[0].steps[0]);
 const removed=nestedArrayEdit(moved,['business','flows',0,'phases',0,'branches'],{kind:'remove',index:0});assert.deepEqual(removed.business.flows[0].phases[0].branches,[before.business.flows[0].phases[0].branches[1]]);assert.deepEqual(value,before);
});

test('three control layers are allowed and a fourth fails without clipping or changing the original input',()=>{
 let value=nestedPlan();value.business.flows[0].phases[0].branches[0].phases=[];
 value=nestedArrayEdit(value,['business','flows',0,'phases',0,'branches',0,'phases'],{kind:'append',item:nestedPhase('parallel')});
 const second=['business','flows',0,'phases',0,'branches',0,'phases',0,'branches',0,'phases'];value=nestedArrayEdit(value,second,{kind:'append',item:nestedPhase('decision')});assert.equal(nestedCapacity(value)[0].depth,3);const before=copy(value);
 assert.throws(()=>nestedArrayEdit(value,[...second,0,'branches',0,'phases'],{kind:'append',item:nestedPhase('repeat')}),/3层控制结构/);assert.deepEqual(value,before);
});

test('citation ownership includes order, nested branches, dispatch, loop checks, completion and questions',()=>{
 const value=nestedPlan(),before=copy(value),uses=nestedCitationUses(value),paths=uses.map(row=>row.path);
 for(const suffix of ['/phase_order/citations/0','/phases/0/decision/attribution/citations/0','/phases/0/branches/1/attribution/citations/0','/phases/0/branches/0/phases/0/dispatch/attribution/citations/0','/phases/0/branches/0/phases/0/join_attribution/citations/0','/phases/0/branches/0/phases/0/branches/1/phases/0/check/attribution/citations/0','/completion/attribution/citations/0'])assert.ok(paths.some(path=>path.endsWith(suffix)),suffix);
 assert.ok(uses.some(row=>row.span_ref===B&&row.path==='/business/questions/0/citations/1'));assert.ok(uses.filter(row=>row.span_ref===A).length>8);assert.deepEqual(value,before);
});

test('limits and unsafe or stale edit locations preserve the complete draft',()=>{
 const value=nestedPlan(),before=copy(value);assert.throws(()=>nestedArrayEdit(value,STEP_PATH,{kind:'up',index:0}),/边界/);assert.throws(()=>nestedSet(value,['__proto__','polluted'],true),/修改位置/);assert.throws(()=>nestedSet(value,['business','missing','name'],'x'),/原结构已变化/);assert.equal({}.polluted,undefined);
 const maximum=nestedPlan();maximum.business.flows=Array.from({length:16},()=>nestedFlow());assert.throws(()=>nestedArrayEdit(maximum,['business','flows'],{kind:'append',item:nestedFlow()}),/16个工作流程/);assert.deepEqual(value,before);assert.equal(nestedPointer(STEP_PATH),'/business/flows/0/phases/0/branches/0/phases/0/branches/0/phases/0/steps');
});
