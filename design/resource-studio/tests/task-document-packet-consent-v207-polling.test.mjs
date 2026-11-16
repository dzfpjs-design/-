// Actual component/controller with captured interval callbacks and an explicit
// synthetic GET seam. Root separately verifies real Job/Packet commit windows.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {packetFixture,cleanup,id,ids} from './task-document-packet-consent-v207-fixture.mjs';
after(cleanup);
const reads=h=>h.state.calls.filter(row=>row.path==='/runtime-task-documents/tasks/'+ids.task+'/packets').length;

for(const graphStatus of ['running','waiting'])test('workflow_waiting '+graphStatus+' retries only original GET after a visible 409 and stops after the actual packet is read',async()=>packetFixture({manualPoll:true,graphStatus,taskStatus:'workflow_waiting'},async h=>{
 await h.edit('资料包授权模型',ids.model);await h.edit('资料包云发送授权说明','任务提交中间窗口也不能丢失的原理由。');h.state.listReplies.push({error:409,message:'原Packet已保存，原Job终态尚待提交核对。'});await h.click('刷新任务资料包');assert.ok(h.text().includes('原Packet已保存，原Job终态尚待提交核对。'));assert.equal(h.container.querySelector('[aria-label="资料包完整发送文本"]'),null);assert.equal(h.text().includes('尚未产生可核对的任务资料包'),false,'409 is not converted to an empty success');const before=reads(h);await h.tick();await h.until(()=>!!h.container.querySelector('[aria-label="资料包完整发送文本"]'),'original packet obtained by GET');assert.equal(reads(h),before+1);assert.equal(h.field('资料包云发送授权说明').value,'任务提交中间窗口也不能丢失的原理由。');assert.equal(h.state.posts.length,0);const found=reads(h);await h.tick();assert.equal(reads(h),found,'success ends automatic packet polling');assert.equal(h.state.calls.some(row=>row.method!=='GET'),false);
}));

test('task version changes during a busy missing-packet read coalesce into one idle catch-up GET',async()=>packetFixture({manualPoll:true,graphStatus:'waiting',taskStatus:'workflow_waiting',empty:true},async h=>{
 let release;h.state.listReplies.push({error:409,message:'原调用还在提交结果。'},{empty:true});h.state.hold={part:'/tasks/'+ids.task+'/packets',promise:new Promise(resolve=>{release=resolve;})};const before=reads(h);await h.click('刷新任务资料包');h.setTask({...h.task(),version:2});await h.render();h.setTask({...h.task(),version:3});await h.render();assert.equal(reads(h),before+1,'version changes do not overlap the original GET');h.state.hold=null;release();await h.until(()=>reads(h)===before+2&&!h.button('刷新任务资料包').disabled,'one deferred GET after busy clears');await h.settle();assert.equal(reads(h),before+2);assert.equal(h.state.posts.length,0);
}));

for(const graphStatus of ['succeeded','cancelled','failed','stopped'])test('terminal '+graphStatus+' does not poll or perform a deferred version GET',async()=>packetFixture({manualPoll:true,graphStatus:'waiting',taskStatus:'workflow_waiting',empty:true},async h=>{
 const before=reads(h);h.setTask({...h.task(),version:2,status:graphStatus==='succeeded'?'workflow_completed':'workflow_'+graphStatus,graph_runtime:{status:graphStatus}});await h.render();await h.tick();assert.equal(reads(h),before);assert.equal(h.state.posts.length,0);
}));

test('an executing-looking status without a real nonterminal graph does not authorize automatic reads',async()=>packetFixture({manualPoll:true,empty:true},async h=>{const before=reads(h);h.setTask({...h.task(),version:2});await h.render();await h.tick();assert.equal(reads(h),before);assert.equal(h.state.posts.length,0);}));

test('a real retained authorization pending blocks GET polling and version catch-up without changing its raw or reason',async()=>packetFixture({manualPoll:true,graphStatus:'waiting',taskStatus:'workflow_waiting'},async h=>{
 await h.edit('资料包授权模型',ids.model);await h.edit('资料包云发送授权说明','原授权仍未知，不得借轮询换稿。');await h.check();h.state.lose=true;await h.click('允许将这份资料包发送给所选模型');const original=h.storage.getItem(h.key+':pending');assert.ok(original);assert.equal(h.state.posts.length,1);h.state.listReplies.push({error:409,message:'固定原资料包尚待原终态核对。'});await h.unmount();await h.mount({reloadModule:true});assert.ok(h.text().includes('固定原资料包尚待原终态核对。'));const before=reads(h);h.setTask({...h.task(),version:2});await h.render();await h.tick();assert.equal(reads(h),before);assert.equal(h.storage.getItem(h.key+':pending'),original);assert.equal(JSON.parse(original).body.reason,'原授权仍未知，不得借轮询换稿。');assert.equal(h.state.posts.length,1);
}));

test('a version update after a packet is present preserves the displayed original and does not restart polling',async()=>packetFixture({manualPoll:true,graphStatus:'running',taskStatus:'workflow_waiting'},async h=>{const before=reads(h);h.setTask({...h.task(),version:2});await h.render();await h.tick();assert.equal(reads(h),before);assert.ok(h.text().includes(h.packetText));assert.equal(h.state.posts.length,0);}));

test('late polling response cannot repopulate a different Task or send any replacement command',async()=>packetFixture({manualPoll:true,graphStatus:'waiting',taskStatus:'workflow_waiting',empty:true},async h=>{
 let release;h.state.hold={part:'/tasks/'+ids.task+'/packets',promise:new Promise(resolve=>{release=resolve;})};const before=reads(h);await h.tick();assert.equal(reads(h),before+1);h.setTask({...h.task(),id:id(91),instance_id:id(92),instance_release_id:id(93)});await h.render();h.state.empty=false;h.state.hold=null;release();await h.settle();assert.equal(h.container.querySelector('[aria-label="资料包完整发送文本"]'),null);assert.equal(h.text().includes(h.packetText),false);assert.equal(h.state.posts.length,0);
}));
