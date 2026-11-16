// Actual offline ReactDOM, native controller and WebCrypto with the explicit
// synthetic transport in useFixture. Root separately verifies real parser/DB.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {act} from 'react';
import {useFixture,cleanup,ids,id,value,referenceText} from './layered-case-use-v210-fixture.mjs';
after(cleanup);

const open=async h=>{await h.nativeReady();await h.click('处理原参考文件');await h.until(()=>!!h.container.querySelector('[aria-label="原参考文件处理理由"]'),'native human controls opened');};
const prepare=async(h,label,reason='核对原参考文件后，沿原版本办理。')=>{await h.edit('原参考文件处理理由',reason);await h.click(label);assert.ok(h.container.querySelector('[aria-label="确认原参考版本操作"]'));};
const confirm=async h=>{await h.click('确认办理原参考版本操作');await h.until(()=>!h.container.querySelector('[aria-label="确认原参考版本操作"]'),'native operation confirmation settled');await h.nativeReady();};
const nativeRaw=h=>{const key=h.nativePendingKey();assert.ok(key,'native original pending is retained');return {key,raw:h.storage.getItem(key)};};
const nativeRecords=h=>Array.from({length:h.storage.length},(_,n)=>h.storage.key(n)).filter(key=>key.startsWith('ga-case-reference-native-v210:'));

test('failed original version has a reachable retry preserving the exact document, job and native command',async()=>useFixture({existing:true,ready:false,native:{status:'failed'}},async h=>{
 await open(h);assert.ok(h.text().includes('原版本解析失败'));assert.ok(h.text().includes('本地解析暂时失败'));assert.equal(h.container.querySelector('input[type="file"]'),null);
 const job=h.state.nativeJobID;await prepare(h,'继续原参考版本解析','原本地解析故障已核对，继续原版本。');assert.equal(h.state.nativePosts.length,0);await confirm(h);
 assert.equal(h.state.nativePosts.length,1);const sent=h.state.nativePosts[0];assert.equal(sent.kind,'retry');assert.equal(sent.path,'/runtime-documents/versions/'+ids.version+'/retry');assert.equal(sent.body.expected_version,1);assert.equal(h.state.nativeJobID,job);assert.equal(h.state.nativeStatus,'queued');assert.equal(h.state.posts.length,0,'no new Use or upload command');assert.equal(h.nativePendingKey(),null);
 assert.equal(h.state.calls.some(row=>row.path.includes('/searches')||row.path.includes('/blocks')||row.path.includes('/citations')),false,'recovery reads no source text or search inventory');
}));

test('queued original can be cancelled without selecting a newer version or replacing the reference',async()=>useFixture({existing:true,ready:false,native:{status:'queued',latestDifferent:true}},async h=>{
 await open(h);await prepare(h,'取消原参考版本解析','先取消这份原参考，保留原件和历史。');await confirm(h);
 assert.equal(h.state.nativeStatus,'cancelled');assert.equal(h.state.nativePosts[0].kind,'cancel');assert.equal(h.state.nativePosts[0].path,'/runtime-documents/versions/'+ids.version+'/cancel');assert.equal(h.state.calls.some(row=>row.path==='/runtime-documents/versions/'+id(33)),false,'no GET or action against latest substitute');assert.equal(h.state.posts.length,0);assert.ok(h.text().includes('原版本解析已取消'));
}));

test('original delivery failure offers its native receipt retry with the actual event version',async()=>useFixture({existing:true,ready:true,native:{status:'parsed',deliveryStatus:'blocked_delivery',deliveryCanRetry:true}},async h=>{
 await open(h);assert.ok(h.text().includes('独立接收暂时失败'));await prepare(h,'重试原参考结果的数据接收','原解析已完成，仅重试同一结果的独立接收。');await confirm(h);
 const sent=h.state.nativePosts[0];assert.equal(sent.kind,'retry_receipt');assert.equal(sent.path,'/runtime-documents/versions/'+ids.version+'/receipts/'+id(32)+'/retry');assert.equal(sent.body.expected_version,1);assert.equal(h.state.nativeDeliveryVersion,2);assert.equal(h.state.nativeDeliveryStatus,'received');assert.equal(h.state.nativeVersion,1,'receipt retry does not invent a parse-version transition');assert.equal(h.state.posts.length,0);
}));

test('another current operator may retry by genuine Version permission while Use creation and staging remain forbidden',async()=>useFixture({existing:true,ready:false,native:{status:'cancelled',otherOperator:true}},async h=>{
 assert.ok(h.text().includes('不是未启动任务的原发起人'));assert.equal([...h.container.querySelectorAll('button')].some(row=>row.textContent==='把完整案例参考加入本机依据选择'),false);
 await open(h);assert.equal(h.button('继续原参考版本解析').disabled,true,'reason is still required');await prepare(h,'继续原参考版本解析','作为当前有权限的操作员，继续原版本解析。');await confirm(h);
 assert.equal(h.state.nativePosts.length,1);assert.equal(h.state.nativePosts[0].kind,'retry');assert.equal(h.state.posts.length,0);assert.equal(h.state.staged.length,0);
}));

test('a native failure without retry permission remains visible and never manufactures a retry control',async()=>useFixture({existing:true,ready:false,native:{status:'failed',canRetry:false}},async h=>{
 await open(h);assert.ok(h.text().includes('本地解析暂时失败'));assert.equal([...h.container.querySelectorAll('button')].some(row=>row.textContent==='继续原参考版本解析'),false);assert.equal(h.state.nativePosts.length,0);
}));

test('a stopping original keeps polling metadata and reveals retry only after the real terminal permission arrives',async()=>useFixture({existing:true,ready:false,manualPoll:true,native:{status:'cancelled',stopping:true,canRetry:false,canCancel:false}},async h=>{
 await open(h);assert.ok(h.text().includes('已请求停止，等待原解析任务结束'));assert.equal([...h.container.querySelectorAll('button')].some(row=>row.textContent==='继续原参考版本解析'),false);assert.equal(h.state.nativePosts.length,0);
 const reads=h.state.calls.filter(row=>row.path==='/runtime-documents/versions/'+ids.version).length;h.state.nativeStopping=false;h.state.nativeCanRetry=true;await h.tick();await h.until(()=>h.state.calls.filter(row=>row.path==='/runtime-documents/versions/'+ids.version).length>reads,'stopping version metadata polled');await h.until(()=>!![...h.container.querySelectorAll('button')].find(row=>row.textContent==='继续原参考版本解析'),'terminal native retry permission visible');
 assert.equal(h.text().includes('已请求停止，等待原解析任务结束'),false);assert.ok(h.text().includes('原版本解析已取消'));assert.equal(h.button('继续原参考版本解析').disabled,true,'no retry until a human reason and explicit action');assert.equal(h.state.nativePosts.length,0);assert.equal(h.state.posts.length,0);
}));

test('committed native reply loss blocks parent switching and closing, then fresh module recovers the exact original by GET',async()=>useFixture({existing:true,ready:false,native:{status:'queued'}},async h=>{
 await open(h);await prepare(h,'取消原参考版本解析','原取消理由必须跨页面保留。');h.state.nativeLose=true;await confirm(h);const original=nativeRaw(h),pending=JSON.parse(original.raw);
 assert.equal(h.state.nativeCommands.size,1);assert.equal(h.state.nativePosts.length,1);assert.equal(h.state.guard(),false);assert.equal(h.button('另选批准案例作为参考').disabled,true);await h.click('收起原版本处理');assert.ok(h.container.querySelector('[aria-label="原参考文件处理理由"]'));assert.ok(h.text().includes('已保留面板和理由'));
 await h.unmount();await h.mount({reloadModule:true});assert.equal(h.storage.getItem(original.key),original.raw);assert.ok(h.container.querySelector('[aria-label="原参考解析请求结果待核对"]'));await h.click('核对原参考解析请求结果');await h.until(()=>h.nativePendingKey()===null,'same native original recovered');await h.nativeReady();
 assert.equal(h.state.nativePosts.length,1);assert.equal(h.storage.getItem(h.state.nativePosts[0].prefix+h.nativeKey+':original:'+pending.body.request_id),original.raw);assert.equal(h.state.posts.length,0);assert.equal(h.state.guard(),true);
}));

test('native request never delivered can continue only after found false and with identical raw bytes and nonce',async()=>useFixture({existing:true,ready:false,native:{status:'queued'}},async h=>{
 await open(h);await prepare(h,'取消原参考版本解析');h.state.nativeSkipPost=true;await confirm(h);const original=nativeRaw(h);assert.equal(h.state.nativeCommands.size,0);await h.click('核对原参考解析请求结果');assert.ok(h.button('按原编号继续原参考解析操作'));await h.click('按原编号继续原参考解析操作');await h.until(()=>h.nativePendingKey()===null,'same original delivered explicitly');await h.nativeReady();assert.equal(h.state.nativePosts.length,2);assert.equal(h.state.nativePosts[0].raw,h.state.nativePosts[1].raw);assert.equal(h.state.nativePosts[1].body.request_id,JSON.parse(original.raw).body.request_id);
}));

for(const failure of ['receipt','draft','remove'])test('native '+failure+' storage failure after commit preserves pending and original for same-result recovery',async()=>useFixture({existing:true,ready:false,native:{status:'queued'}},async h=>{
 await open(h);await prepare(h,'取消原参考版本解析','存储故障也不能丢失的原理由。');const proto=h.dom.window.Storage.prototype,set=proto.setItem,remove=proto.removeItem;
 proto.setItem=function(key,raw){const committed=h.state.nativeCommands.size>0,native=key.startsWith('ga-case-reference-native-v210:');if(committed&&native&&((failure==='receipt'&&key.includes(':received:'))||(failure==='draft'&&(key.includes(':draft-owner:')||key.endsWith(h.nativeKey+':draft')))))throw Error('原参考恢复存储暂时不可写');return set.call(this,key,raw);};
 proto.removeItem=function(key){if(failure==='remove'&&h.state.nativeCommands.size>0&&key.startsWith('ga-case-reference-native-v210:')&&key.endsWith(h.nativeKey+':pending'))return;return remove.call(this,key);};
 try{await confirm(h);const original=nativeRaw(h);assert.equal(h.state.nativeCommands.size,1);assert.equal(h.state.nativePosts.length,1);assert.equal(h.state.guard(),false);proto.setItem=set;proto.removeItem=remove;await h.click('核对原参考解析请求结果');await h.until(()=>h.nativePendingKey()===null,'native result archived after local storage restored');await h.nativeReady();assert.equal(h.state.nativePosts.length,1);assert.equal(h.storage.getItem(h.state.nativePosts[0].prefix+h.nativeKey+':original:'+JSON.parse(original.raw).body.request_id),original.raw);}finally{proto.setItem=set;proto.removeItem=remove;}
}));

test('fresh module owners preserve either native reason and explicit restore backs up the current reason without caching source text',async()=>useFixture({existing:true,ready:false,native:{status:'failed'}},async h=>{
 await open(h);await h.edit('原参考文件处理理由','窗口A处理理由：先核对原失败。');const first=nativeRecords(h).filter(key=>key.includes(':draft-owner:')).map(key=>({key,raw:h.storage.getItem(key)}));
 await h.unmount();await h.mount({reloadModule:true});await open(h);assert.equal(h.field('原参考文件处理理由').value,'窗口A处理理由：先核对原失败。');await h.edit('原参考文件处理理由','窗口B处理理由：增加原版本说明。');await h.unmount();await h.mount({reloadModule:true});await open(h);await h.click('读取原参考处理保留稿');const article=[...h.container.querySelectorAll('article[aria-label^="原参考处理保留稿"]')].find(node=>node.textContent.includes('窗口A处理理由'));assert.ok(article);await act(async()=>[...article.querySelectorAll('button')].find(node=>node.textContent==='恢复这份原参考处理理由').click());await h.until(()=>h.field('原参考文件处理理由').value==='窗口A处理理由：先核对原失败。','old native owner reason explicitly restored');
 const retained=nativeRecords(h).filter(key=>key.includes(':retained:')).map(key=>JSON.parse(h.storage.getItem(key)));assert.ok(retained.some(row=>JSON.parse(row.raw).draft.reason==='窗口B处理理由：增加原版本说明。'));for(const row of first)assert.equal(h.storage.getItem(row.key),row.raw);const local=nativeRecords(h).map(key=>h.storage.getItem(key)).join('\n');for(const text of [...Object.values(value),referenceText])assert.equal(local.includes(text),false,'no original case/reference values in recovery records');assert.equal(h.state.nativePosts.length,0);
}));

test('native confirmation keeps its original reason while polling is paused, and cancellation resumes normal GETs',async()=>useFixture({existing:true,ready:false,manualPoll:true,native:{status:'queued'}},async h=>{
 await open(h);await prepare(h,'取消原参考版本解析','人工确认期间保留这条原理由。');const confirmation=h.container.querySelector('[aria-label="确认原参考版本操作"]'),before=h.state.calls.length;await h.tick();assert.equal(h.state.calls.length,before);assert.equal(h.container.querySelector('[aria-label="确认原参考版本操作"]'),confirmation);assert.equal(h.state.guard(),false);await h.click('继续核对原版本处理');await h.tick();await h.until(()=>h.state.calls.length>before,'normal native/Use reads resume');await h.nativeReady();assert.equal(h.state.nativePosts.length,0);
}));

test('automatic parent refresh cannot collapse an open native reason form while fixed-version progress still refreshes',async()=>useFixture({existing:true,ready:false,manualPoll:true,native:{status:'queued'}},async h=>{
 await open(h);await h.edit('原参考文件处理理由','正在编辑的原处理理由不能被自动刷新收起。');const contexts=h.state.calls.filter(row=>row.path.includes('/layered-case-uses-v210/tasks/')&&row.path.endsWith('/context')).length,versions=h.state.calls.filter(row=>row.path==='/runtime-documents/versions/'+ids.version).length;
 await h.tick();await h.until(()=>h.state.calls.filter(row=>row.path==='/runtime-documents/versions/'+ids.version).length>versions,'fixed original status refreshed');assert.equal(h.state.calls.filter(row=>row.path.includes('/layered-case-uses-v210/tasks/')&&row.path.endsWith('/context')).length,contexts);assert.equal(h.field('原参考文件处理理由').value,'正在编辑的原处理理由不能被自动刷新收起。');assert.ok(h.button('收起原版本处理'));assert.equal(h.state.nativePosts.length,0);
}));

for(const recovery of [false,true])test('native '+(recovery?'original recovery':'successful cancel')+' keeps the same open helper and human reason during delayed parent metadata refresh',async()=>useFixture({existing:true,ready:false,native:{status:'queued'}},async h=>{
 await open(h);const helper=h.container.querySelector('[aria-label="原案例参考文件的解析与接收恢复"]');await prepare(h,'取消原参考版本解析','取消已保存之后仍应保留的原处理理由。');
 let release;const hold=()=>{h.state.hold={part:'/layered-case-uses-v210/tasks/'+ids.task+'/context',promise:new Promise(resolve=>{release=resolve;})};};
 if(recovery){h.state.nativeLose=true;await confirm(h);assert.ok(h.nativePendingKey());hold();await h.click('核对原参考解析请求结果');}
 else{hold();await h.click('确认办理原参考版本操作');}
 await h.until(()=>h.nativePendingKey()===null,'native original already committed and received');assert.equal(h.state.nativeCommands.size,1);assert.equal(h.state.nativePosts.length,1);
 assert.equal(h.container.querySelector('[aria-label="原案例参考文件的解析与接收恢复"]'),helper,'parent does not unmount the notifying child');assert.equal(h.field('原参考文件处理理由').value,'取消已保存之后仍应保留的原处理理由。');assert.ok(h.button('收起原版本处理'));
 h.state.hold=null;release();await h.until(()=>h.state.guard(),'parent refresh and native notification finished');assert.equal(h.container.querySelector('[aria-label="原案例参考文件的解析与接收恢复"]'),helper);assert.equal(h.field('原参考文件处理理由').value,'取消已保存之后仍应保留的原处理理由。');assert.ok(h.text().includes('原版本解析已取消'));assert.equal(h.state.nativePosts.length,1);assert.equal(h.state.posts.length,0);
}));

test('failed parent read after a committed cancel retains the prior reference and recovery view for a later GET-only refresh',async()=>useFixture({existing:true,ready:false,native:{status:'queued'}},async h=>{
 await open(h);const helper=h.container.querySelector('[aria-label="原案例参考文件的解析与接收恢复"]');await prepare(h,'取消原参考版本解析','父级读取失败也不能丢失此理由。');let reject;
 h.state.hold={part:'/layered-case-uses-v210/tasks/'+ids.task+'/context',promise:new Promise((_resolve,fail)=>{reject=fail;})};await h.click('确认办理原参考版本操作');await h.until(()=>h.nativePendingKey()===null,'native cancel receipt durably stored');assert.equal(h.state.nativePosts.length,1);
 h.state.hold=null;reject(Object.assign(Error('案例参考元数据暂时不可读'),{status:503}));await h.until(()=>h.text().includes('案例参考元数据暂时不可读'),'parent error visible');assert.ok(h.text().includes('当前仍显示上次核对的参考与进度'));assert.ok(h.text().includes(value.caveats));assert.equal(h.container.querySelector('[aria-label="原案例参考文件的解析与接收恢复"]'),helper);assert.equal(h.field('原参考文件处理理由').value,'父级读取失败也不能丢失此理由。');await h.until(()=>h.state.guard(),'no original request remains pending');
 await h.click('更新案例参考与实际进度');await h.until(()=>!h.text().includes('案例参考元数据暂时不可读'),'later metadata GET recovered');assert.equal(h.container.querySelector('[aria-label="原案例参考文件的解析与接收恢复"]'),helper);assert.equal(h.field('原参考文件处理理由').value,'父级读取失败也不能丢失此理由。');assert.equal(h.state.nativePosts.length,1);assert.equal(h.nativePendingKey(),null);
}));

test('late parent refresh after committed native cancellation cannot repopulate another task scope',async()=>useFixture({existing:true,ready:false,native:{status:'queued'}},async h=>{
 await open(h);await prepare(h,'取消原参考版本解析','原任务取消只办理一次。');let release;h.state.hold={part:'/layered-case-uses-v210/tasks/'+ids.task+'/context',promise:new Promise(resolve=>{release=resolve;})};await h.click('确认办理原参考版本操作');await h.until(()=>h.nativePendingKey()===null,'original cancellation already archived');
 h.setTask({id:id(99),version:1});await h.render();h.state.hold=null;release();await h.settle();assert.equal(h.container.querySelector('[aria-label="原案例参考文件的解析与接收恢复"]'),null);assert.equal(h.text().includes(value.caveats),false);assert.equal(h.state.nativePosts.length,1);assert.equal(h.nativePendingKey(),null);
}));

for(const changed of ['user','workspace'])test('actual '+changed+' switch hides native controls and preserves the pending original without stale writes',async()=>useFixture({existing:true,ready:false,native:{status:'queued'}},async h=>{
 await open(h);await prepare(h,'取消原参考版本解析');h.state.nativeLose=true;await confirm(h);const original=nativeRaw(h),old=h.button('核对原参考解析请求结果');h.session.setItem(changed==='user'?'ga-setup-csrf-user':'ga-setup-csrf-workspace',id(99));await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.equal(h.container.querySelector('[aria-label="原参考文件处理理由"]'),null);const count=h.state.calls.length;await act(async()=>old.click());await h.settle();assert.equal(h.state.calls.length,count);assert.equal(h.storage.getItem(original.key),original.raw);assert.equal(h.state.nativePosts.length,1);
}));

test('late native recovery response after task scope changes cannot reveal prior reference or clear its original',async()=>useFixture({existing:true,ready:false,native:{status:'queued'}},async h=>{
 await open(h);await prepare(h,'取消原参考版本解析');h.state.nativeLose=true;await confirm(h);const original=nativeRaw(h);let release;h.state.hold={part:'/runtime-documents/requests/',promise:new Promise(resolve=>{release=resolve;})};await h.click('核对原参考解析请求结果');h.setTask({id:id(99),version:1});await h.render();release();h.state.hold=null;await h.settle();assert.equal(h.container.querySelector('[aria-label="原案例参考文件的解析与接收恢复"]'),null);assert.equal(h.text().includes(value.caveats),false);assert.equal(h.storage.getItem(original.key),original.raw);assert.equal(h.state.nativePosts.length,1);
}));
