import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createTaskArchivesController,taskArchiveScopeKey,checkedArchiveView,checkedArchiveList} from '../src/task-archives-v198.mjs';
import {scope,archiveID,fixture,memory,copy,digest} from './task-archives-v198-fixture.mjs';
const locks={request:async(_key,_options,run)=>run()};
async function setup(options={}){const storage=options.storage||memory(),session=options.session||memory(),s=fixture(storage),c=createTaskArchivesController({api:s.api,scope,storage,session,crypto:webcrypto,locks,...options});c.mount();await c.open();return {...s,c,session};}
const posts=s=>s.calls.filter(call=>call.method==='POST');

test('Task scope key is stable and a cold open reads real-contract list/history without a write',async()=>{
 const s=await setup();assert.equal(taskArchiveScopeKey(scope),taskArchiveScopeKey({task_id:scope.task_id,actor_id:scope.actor_id,workspace_id:scope.workspace_id}));
 assert.equal(s.c.view.id,archiveID);assert.equal(s.c.form.values.title,s.versions[0].value.title);assert.equal(posts(s).length,0);assert.equal(s.c.guard(),true);
 s.c.filter={record_type:'现场处置记录',q:'待核提案'};await s.c.refresh();assert.equal(s.c.items.length,1);assert.ok(s.calls.at(-2).path.includes('record_type='));
});
test('local text survives remount; exact original request precedes one POST and lost response recovers by GET only',async()=>{
 const s=await setup();s.c.edit({title:'多行标题\n保持原文',summary:'完整修订\n不自动确认'});const originalJournal=s.c.journal;
 s.c.dispose();const c=createTaskArchivesController({api:s.api,scope,storage:s.storage,session:s.session,crypto:webcrypto,locks});c.mount();await c.open();assert.equal(c.journal,originalJournal);assert.equal(c.form.values.summary,'完整修订\n不自动确认');
 s.state.mode='aftercommit';await assert.rejects(c.save(),/响应丢失/);const raw=c.pending.raw,nonce=c.pending.body.request_id,original=s.storage.getItem(c.prefix+'original:'+nonce);assert.equal(posts(s).length,1);
 c.dispose();const recovered=createTaskArchivesController({api:s.api,scope,storage:s.storage,session:s.session,crypto:webcrypto,locks});recovered.mount();await recovered.open();assert.equal(recovered.pending.raw,raw);assert.equal(posts(s).length,1);
 await recovered.recover();assert.equal(posts(s).length,1);assert.equal(recovered.pending,null);assert.equal(recovered.form.dirty,false);assert.equal(recovered.view.version,2);assert.equal(s.storage.getItem(c.prefix+'original:'+nonce),original);assert.equal(s.commands.size,1);
 await recovered.select(archiveID,1);assert.equal(recovered.view.value.summary,s.versions[0].value.summary);assert.equal(recovered.view.history.length,2);
});
test('not-found is not permission to auto repeat; explicit retry keeps original bytes/nonce and one saved version',async()=>{
 const s=await setup();s.c.edit({summary:'显式核对后恢复原保存'});s.state.mode='beforecommit';await assert.rejects(s.c.save());const original=s.c.pending.raw;
 await s.c.recover();assert.equal(posts(s).length,1);assert.equal(s.c.pending.not_found,true);assert.equal(s.commands.size,0);
 s.state.mode='';await s.c.retrySame();assert.equal(posts(s).length,2);assert.equal(s.state.postBodies[0],original);assert.equal(s.state.postBodies[1],original);assert.equal(s.commands.size,1);assert.equal(s.versions.length,2);
});
test('CAS definitive refusal is exact, preserves old input and cannot silently issue a fresh request',async()=>{
 const s=await setup();s.c.edit({summary:'旧版本上填写的人工说明'});s.append({title:'另一位操作者已保存的版本'});await assert.rejects(s.c.save());const pending=copy(s.c.pending),original=s.storage.getItem(s.c.prefix+'original:'+pending.body.request_id);
 assert.equal(pending.rejection.not_applied,true);await assert.rejects(s.c.retrySame());assert.equal(posts(s).length,1);
 await s.c.archiveRejected();assert.equal(posts(s).length,1);assert.equal(s.c.pending,null);assert.equal(s.c.form.values.summary,'旧版本上填写的人工说明');assert.equal(s.c.form.conflict,true);assert.equal(s.storage.getItem(s.c.prefix+'original:'+pending.body.request_id),original);assert.ok(s.storage.getItem(s.c.prefix+'rejected:'+pending.body.request_id));
 await s.c.useCurrent();assert.equal(s.c.form.values.title,'另一位操作者已保存的版本');assert.equal(s.c.retained()[0].values.summary,'旧版本上填写的人工说明');assert.equal(s.c.form.dirty,false);
});
test('forged CAS hash is unknown rather than authoritative not-applied',async()=>{
 const s=await setup();s.c.edit({summary:'原输入保留'});s.append({title:'新版本'});s.state.mode='spoofedConflict';await assert.rejects(s.c.save());assert.equal(s.c.pending.rejection,undefined);
 await assert.rejects(s.c.archiveRejected());assert.ok(s.storage.getItem(s.c.shared));assert.equal(posts(s).length,1);
});
test('storage failure keeps server history readable, keeps in-memory input and blocks all POSTs',async()=>{
 const storage=memory(),set=storage.setItem;storage.setItem=()=>{throw Error('synthetic storage denial');};const s=await setup({storage});assert.equal(s.c.view.id,archiveID);assert.equal(posts(s).length,0);assert.equal(s.c.storageBlocked,true);
 // A healthy journal can become unavailable after open, without losing input.
 storage.setItem=set;s.c.pendingUnreadable=false;s.c.journal=s.c.prefix+'draft:synthetic';s.c.persist();s.c.edit({summary:'不能丢失的本页输入'});storage.setItem=()=>{throw Error('synthetic full');};s.c.edit({summary:'继续保留本页输入'});assert.equal(s.c.form.values.summary,'继续保留本页输入');assert.equal(s.c.guard(),false);await assert.rejects(s.c.save());assert.equal(posts(s).length,0);
 await s.c.select(archiveID,1);assert.equal(s.c.view.value.title,s.versions[0].value.title);assert.equal(s.c.form.values.summary,'继续保留本页输入');
});
test('failed use-current read never clears dirty user work; source-history error preserves prior verified view',async()=>{
 const s=await setup();s.c.edit({summary:'尚未保存的人工作业'});s.state.denyDetail=true;await assert.rejects(s.c.useCurrent());assert.equal(s.c.form.dirty,true);assert.equal(s.c.form.values.summary,'尚未保存的人工作业');
 await assert.rejects(s.c.select(archiveID,1));assert.equal(s.c.view.version,1);assert.equal(s.c.view.value.sources.length,2);assert.equal(posts(s).length,0);
});
test('demoted original owner may read and recover; neither recovery nor readonly history requires a provider or POST',async()=>{
 const s=await setup();s.c.edit({summary:'降权前原说明'});s.state.mode='aftercommit';await assert.rejects(s.c.save());const count=posts(s).length;
 s.state.role='viewer';s.c.dispose();const c=createTaskArchivesController({api:s.api,scope,storage:s.storage,session:s.session,crypto:webcrypto,locks:null});c.mount();await c.open();assert.equal(c.canEdit,false);await c.select(archiveID,1);await c.recover();assert.equal(c.view.version,2);assert.ok(c.pending?.direct_result);assert.equal(posts(s).length,count);assert.ok(s.storage.getItem(c.shared));await assert.rejects(c.retrySame());assert.equal(posts(s).length,count);
});
test('identity changes conceal content and leave durable original bytes isolated from a different actor',async()=>{
 const s=await setup();s.c.edit({summary:'原账号私有草稿'});s.state.mode='beforecommit';await assert.rejects(s.c.save());const raw=s.storage.getItem(s.c.shared);s.state.actor_id='19800000-0000-4000-8000-000000000099';await assert.rejects(s.c.recover());assert.equal(s.c.denied,true);assert.equal(s.c.readable,false);assert.equal(s.storage.getItem(s.c.shared),raw);assert.equal(posts(s).length,1);
 const other=createTaskArchivesController({api:s.api,scope:{...scope,actor_id:s.state.actor_id},storage:s.storage,session:s.session,crypto:webcrypto,locks});other.mount();await other.open();assert.equal(other.pending,null);assert.notEqual(other.prefix,s.c.prefix);assert.equal(other.form.values.summary,s.versions[0].value.summary);assert.equal(posts(s).length,1);
});
test('closed candidate flags, cross-task references and original SHA cannot become authority or be silently replaced',async()=>{
 const s=await setup();for(const key of ['formal_archive','human_confirmed','entity_state_updated']){const value=s.view();value[key]=true;assert.throws(()=>checkedArchiveView(value,scope,archiveID));}
 const view=s.view();view.value.sources[0].snapshot.task_id='19800000-0000-4000-8000-000000000099';assert.throws(()=>checkedArchiveView(view,scope,archiveID));const list=s.list();list.items[0].status='formal';assert.throws(()=>checkedArchiveList(list,scope));
 s.c.edit({summary:'原输入'});s.state.mode='beforecommit';await assert.rejects(s.c.save());const p=copy(s.c.pending);p.raw=p.raw.replace('原输入','替换输入');assert.notEqual(await digest(p.raw),p.sha256);s.storage.setItem(s.c.shared,JSON.stringify(p));await assert.rejects(s.c.recover());assert.equal(posts(s).length,1);assert.ok(s.storage.getItem(s.c.shared));
});

test('recovering an older saved receipt does not label it the current server version',async()=>{
 const s=await setup();s.c.edit({summary:'已提交的原说明'});s.state.mode='aftercommit';await assert.rejects(s.c.save());s.append({summary:'其后另一版说明'});await s.c.recover();assert.equal(s.c.pending,null);assert.equal(s.c.view.version,2);assert.equal(s.c.form.values.summary,'已提交的原说明');assert.equal(s.c.form.conflict,true);assert.equal(s.c.currentVersion,3);assert.equal(posts(s).length,1);await s.c.useCurrent();assert.equal(s.c.form.values.summary,'其后另一版说明');
});

test('disposing during original hashing cannot submit a request from an obsolete mount',async()=>{
 let release;const gate=new Promise(resolve=>{release=resolve;}),crypto={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:async(...args)=>{const result=await webcrypto.subtle.digest(...args);await gate;return result;}}};
 const s=await setup({crypto});s.c.edit({summary:'离开前保存的本机输入'});const saving=s.c.save();await new Promise(resolve=>setImmediate(resolve));s.c.dispose();release();await assert.rejects(saving);assert.equal(posts(s).length,0);assert.equal(s.storage.getItem(s.c.shared),null);assert.ok(s.storage.getItem(s.c.journal).includes('离开前保存的本机输入'));
});

test('a late 403 from a disposed mount cannot conceal the next verified mount',async()=>{
 const s=await setup();let rejectOld,hold=true;const original=s.c.api;s.c.api=(path,options)=>path==='/me'&&hold?new Promise((_resolve,reject)=>{rejectOld=reject;}):original(path,options);
 const stale=s.c.identity();hold=false;s.c.dispose();s.c.mount();await s.c.identity();const error=Error('obsolete denied response');error.status=403;rejectOld(error);await assert.rejects(stale);assert.equal(s.c.denied,false);assert.equal(s.c.readable,true);assert.equal(posts(s).length,0);
});
