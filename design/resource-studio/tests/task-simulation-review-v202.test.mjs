// The real controller with only authenticated transport replaced. No runtime,
// provider, database, device, browser permission, or source authority evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {checkedSimulationReviewBasis,checkedSimulationReviewReceipt} from '../src/task-simulation-review-v202.mjs';
import {fixture,basis,scope,copy,fill,posts,id} from './task-simulation-review-v202-fixture.mjs';

test('review is lazy, keeps original gaps, and journals one explicit same-Job command before POST',async()=>{
 const f=fixture();assert.equal(f.state.calls.length,0);assert.equal(f.c.canConfirm,false);await f.c.open();assert.equal(posts(f).length,0);fill(f.c);assert.equal(f.c.canConfirm,true);const original=copy(f.state.basis);const result=await f.c.confirm();
 assert.equal(result.found,true);assert.equal(posts(f).length,1);const body=JSON.parse(posts(f)[0].body);assert.equal(body.activation_id,scope.activation_id);assert.deepEqual(body.acknowledged_missing_information,original.missing_information);assert.equal(body.basis_hash,original.basis_hash);assert.equal(body.acknowledge_simulation_only,true);assert.equal(f.c.receipt.job_id,id(4));assert.equal(f.c.receipt.gaps_resolved,false);assert.equal(f.c.receipt.human_review_verified,false);assert.deepEqual(f.state.basis,original);assert.equal(f.c.pending,null);assert.equal(f.state.saved.length,1);assert.equal(f.state.saved[0].id,scope.task_id);assert.ok(f.c.notice.includes('资料缺项仍未解决'));assert.ok([...f.storage.map.keys()].some(key=>key.startsWith(f.c.prefix+'confirmed:')));
});

test('lost POST response survives unmount and uses only original request GET; no replacement nonce or Job',async()=>{
 const f=fixture();await f.c.open();fill(f.c);f.state.mode.lostResponse=true;await assert.rejects(()=>f.c.confirm());const original=copy(f.c.pending),raw=f.storage.getItem(f.c.outboxKey);f.c.dispose();const c=f.make();assert.equal(c.pending.raw,original.raw);assert.equal(f.storage.getItem(c.outboxKey),raw);await c.open();assert.equal(posts(f).length,1);assert.equal(c.pending,null);assert.equal(c.form.reason,original.body.reason);assert.equal(c.form.acknowledged,false);assert.equal(c.receipt.job_id,original.job_id);assert.ok(f.state.calls.some(item=>item.path.endsWith('/requests/'+original.body.request_id)));
});

test('unknown or not found never generates a fresh request or automatically resends',async()=>{
 const f=fixture();await f.c.open();fill(f.c);f.state.mode.unknown=true;await assert.rejects(()=>f.c.confirm());const original=copy(f.c.pending);await f.c.recover();await f.c.refresh();await f.c.recover();assert.equal(posts(f).length,1);assert.deepEqual(f.c.pending,original);assert.equal(f.c.canConfirm,false);assert.equal(f.c.pendingNotFound,true);assert.ok(f.c.safeToLeave());await assert.rejects(()=>f.c.archiveRejected());assert.deepEqual(f.c.pending,original);
});

test('a known rejected request is retained before returning to edit; stale basis requires a fresh explicit acknowledgment',async()=>{
 const f=fixture();await f.c.open();fill(f.c);f.state.mode.rejected=true;await assert.rejects(()=>f.c.confirm());const original=copy(f.c.pending);assert.equal(original.rejected.status,409);f.state.basis.basis_hash='c'.repeat(64);f.state.basis.task_version++;await f.c.archiveRejected();assert.equal(f.c.pending,null);assert.equal(f.c.stale,true);assert.equal(f.c.form.reason,original.body.reason);assert.equal(f.c.canConfirm,false);assert.ok(f.storage.getItem(f.c.prefix+'rejected:'+original.body.request_id));f.c.useLatest();assert.equal(f.c.form.acknowledged,false);assert.equal(f.c.canConfirm,false);assert.equal(posts(f).length,1);
});

test('pre-submit current basis or task version changes block POST and preserve original reason',async()=>{
 const f=fixture();await f.c.open();fill(f.c);f.state.basis.task_version++;await assert.rejects(()=>f.c.confirm(),/变化/);assert.equal(posts(f).length,0);assert.equal(f.c.form.reason,'保留原资料缺项，仅核对已批准的两项模拟设备动作。');f.state.basis.basis_hash='d'.repeat(64);await f.c.refresh();assert.equal(f.c.stale,true);assert.equal(f.c.form.acknowledged,false);assert.equal(f.c.canConfirm,false);
});

test('storage failure keeps human work visible and prevents POST until it is read back durably',async()=>{
 const f=fixture();await f.c.open();fill(f.c);f.storage.fail=true;f.c.edit({reason:'本页输入不可丢弃'});assert.equal(f.c.localSaved,false);assert.equal(f.c.safeToLeave(),false);assert.throws(()=>f.c.confirm());assert.equal(f.c.form.reason,'本页输入不可丢弃');assert.equal(posts(f).length,0);f.storage.fail=false;f.c.retryLocal();f.c.edit({acknowledged:true});assert.equal(f.c.canConfirm,true);
});

test('permission or account changes hide analysis and never mutate the original request',async()=>{
 const f=fixture();await f.c.open();fill(f.c);f.state.mode.unknown=true;await assert.rejects(()=>f.c.confirm());const original=copy(f.c.pending);f.state.mode.forbidden=true;await assert.rejects(()=>f.c.recover());assert.equal(f.c.denied,true);assert.equal(f.c.basis,null);assert.deepEqual(f.c.pending,original);assert.equal(posts(f).length,1);
 const other=fixture();await other.c.open();fill(other.c);other.session.setItem('ga-setup-csrf-user',id(99));await assert.rejects(()=>other.c.identity());assert.equal(other.c.denied,true);assert.equal(posts(other).length,0);
});

test('mismatched original body, actor or Job receipt cannot settle or replay pending work',async()=>{
 for(const modify of [record=>{record.body.reason='替换理由';},record=>{record.actor_id=id(91);},record=>{record.receipt.job_id=id(92);},record=>{record.receipt.gaps_resolved=true;}]){
  const f=fixture();await f.c.open();fill(f.c);f.state.mode.lostResponse=true;await assert.rejects(()=>f.c.confirm());const original=copy(f.c.pending);f.state.mode.modifyRecord=modify;await assert.rejects(()=>f.c.recover());assert.deepEqual(f.c.pending,original);assert.equal(posts(f).length,1);assert.equal(f.state.saved.length,0);
 }
});

test('fresh Task must be accepted by its actual parent before the outbox is cleared',async()=>{
 const f=fixture();await f.c.open();fill(f.c);f.state.mode.parentReject=true;await assert.rejects(()=>f.c.confirm(),/当前任务尚未刷新/);const original=copy(f.c.pending);assert.ok(original);f.state.mode.parentReject=false;await f.c.recover();assert.equal(f.c.pending,null);assert.equal(posts(f).length,1);assert.equal(f.state.saved.length,2);
});

test('only exact engineering-only basis and receipts permit confirmation; model permission is never inferred',()=>{
 for(const mutate of [v=>{v.targets[0].simulation_only=false;},v=>{v.human_review_verified=true;},v=>{v.gaps_resolved=true;},v=>{v.state='not_applicable';},v=>{v.can_confirm='true';},v=>{v.targets=[];},v=>{v.can_dispatch=true;}]){const v=basis();mutate(v);assert.throws(()=>checkedSimulationReviewBasis(v,scope));}
 const non=basis();non.state='not_applicable';non.can_confirm=false;non.reason='当前步骤不适用';assert.equal(checkedSimulationReviewBasis(non,scope).can_confirm,false);
 const f=fixture();assert.throws(()=>checkedSimulationReviewReceipt({schema:'invented'},f.c.pending||{scope,body:{request_id:id(5)},job_id:id(4)}));
});
