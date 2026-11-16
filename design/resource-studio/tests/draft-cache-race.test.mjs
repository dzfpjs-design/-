import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';
globalThis.sessionStorage={getItem(){return null;},setItem(){},removeItem(){}};
const {DraftSync}=await import('../src/runtime-api.js');
const {savedConfigurationWire}=await import('../src/configuration-numeric-wire.mjs');
const {readDraftRecord,readDraftRecovery,draftOutboxKey,adoptSavedDraft,resolveDraftRecovery}=await import('../src/draft-cache-contract.mjs');
const storage=()=>{const values=new Map();return {values,get length(){return values.size;},key:index=>[...values.keys()][index]||null,getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};};
const lockManager=()=>{const tails=new Map();return {request(key,_options,operation){const promise=(tails.get(key)||Promise.resolve()).then(operation);tails.set(key,promise.catch(()=>{}));return promise;}};};
const response=(obj,status=200)=>new Response(JSON.stringify(obj),{status,headers:{'Content-Type':'application/json'}});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const saved={revision:8,configuration:{note:'A 服务器已保存'},automation_review:{version:1,businesses:[]}};
const source=await readFile(new URL('../src/RuntimeSetup.jsx',import.meta.url),'utf8');
const block=source.slice(source.indexOf(' async function adoptServerDraft(saved){'),source.indexOf(' async function acceptUnderstanding(saved,goBusiness=true)'));
function actualAdopt(store,locks,account,sync,key){const state={value:null},live={current:null},fn=new Function('accountRef','sync','key','adoptSavedDraft','localStorage','DraftSync','setSave','setW','live','setDraftView','checkReviewView','savedConfigurationWire','return '+block)({current:account},{current:sync},{current:key},(...args)=>adoptSavedDraft(...args,locks),store,DraftSync,()=>{},value=>state.value=value,live,()=>{},x=>x,savedConfigurationWire);return {fn,state};}
for(const order of ['receipt_then_CAS','CAS_then_receipt'])test('actual review adoption and DraftSync preserve B human draft in '+order,async()=>{
 const store=storage(),tabA=storage(),tabB=storage(),locks=lockManager(),key='ga-setup:human:w',started=deferred(),release=deferred();
 globalThis.fetch=async()=>{started.resolve();await release.promise;return response({detail:'版本冲突'},409);};
 const a=new DraftSync(store,key,7,()=>{},{locks,tabStorage:tabA}),b=new DraftSync(store,key,7,()=>{},{locks,tabStorage:tabB});b.save({note:'B 人工未提交稿'});await started.promise;
 const {fn,state}=actualAdopt(store,locks,{workspace_id:'w',username:'human'},a,key);
 if(order==='CAS_then_receipt'){release.resolve();await assert.rejects(b.wait());await fn(saved);}else{await fn(saved);release.resolve();await assert.rejects(b.wait());}
 assert.deepEqual(state.value,saved.configuration);const shared=readDraftRecord(store,key);assert.equal(shared.pending,true);assert.equal(shared.configuration.note,'B 人工未提交稿');
 const refreshed=readDraftRecovery(store,key,tabB);assert.equal(refreshed.source,'own');assert.equal(refreshed.local.configuration.note,'B 人工未提交稿');assert.equal(refreshed.local.pending,true);
 assert.equal(readDraftRecovery(store,key,tabA).own,null);assert.equal(readDraftRecord(store,draftOutboxKey(key,b.owner)).configuration.note,'B 人工未提交稿');
});
test('two writer inputs are durable independently and foreign pending blocks a second PUT',async()=>{
 const store=storage(),bTab=storage(),cTab=storage(),locks=lockManager(),key='ga-setup:human:w',started=deferred(),release=deferred();let requests=0;
 globalThis.fetch=async()=>{requests++;started.resolve();await release.promise;return response({detail:'版本冲突'},409);};
 const b=new DraftSync(store,key,7,()=>{},{locks,tabStorage:bTab}),c=new DraftSync(store,key,7,()=>{},{locks,tabStorage:cTab});b.save({note:'B 人稿'});await started.promise;c.save({note:'C 人稿'});await assert.rejects(c.wait());
 assert.equal(requests,1);assert.equal(readDraftRecovery(store,key,bTab).local.configuration.note,'B 人稿');assert.equal(readDraftRecovery(store,key,cTab).local.configuration.note,'C 人稿');assert.equal(readDraftRecord(store,key).configuration.note,'B 人稿');release.resolve();await assert.rejects(b.wait());
});
test('generation one ACK preserves newer generation two until its own acknowledgement',async()=>{
 const store=storage(),tab=storage(),locks=lockManager(),first=deferred(),second=deferred(),one=deferred(),two=deferred();let calls=0;
 globalThis.fetch=async()=>{if(++calls===1){first.resolve();await one.promise;return response({revision:8});}second.resolve();await two.promise;return response({revision:9});};
 const sync=new DraftSync(store,'draft',7,()=>{},{locks,tabStorage:tab});sync.save({note:'g1'});await first.promise;sync.save({note:'g2'});one.resolve();await second.promise;
 const middle=readDraftRecovery(store,'draft',tab).local;assert.equal(middle.configuration.note,'g2');assert.equal(middle.pending,true);assert.equal(middle.revision,8);two.resolve();await sync.wait();assert.equal(readDraftRecord(store,'draft').pending,false);assert.equal(readDraftRecord(store,'draft').configuration.note,'g2');
});
test('cloned session pointer never reuses an active writer or overwrites its outbox',()=>{
 const store=storage(),originalTab=storage(),cloneTab=storage(),locks=lockManager(),first=new DraftSync(store,'draft',7,()=>{},{locks,tabStorage:originalTab});first.stage({note:'original'});for(const [key,value] of originalTab.values)cloneTab.setItem(key,value);
 const clone=new DraftSync(store,'draft',7,()=>{},{locks,tabStorage:cloneTab});assert.notEqual(clone.owner,first.owner);assert.equal(readDraftRecovery(store,'draft',cloneTab).local.configuration.note,'original');clone.stage({note:'clone'});
 assert.equal(readDraftRecovery(store,'draft',originalTab).local.configuration.note,'original');assert.equal(readDraftRecovery(store,'draft',cloneTab).local.configuration.note,'clone');
});
test('explicit recovery checks the exact generation, keeps foreign work and stages replacement first',async()=>{
 const store=storage(),tab=storage(),locks=lockManager(),key='draft',sync=new DraftSync(store,key,7,()=>{},{locks,tabStorage:tab});sync.stage({note:'g1'});const first=readDraftRecovery(store,key,tab).local;sync.stage({note:'g2'});
 await assert.rejects(resolveDraftRecovery(store,tab,key,first,saved,false,locks),/更新/);assert.equal(readDraftRecovery(store,key,tab).local.configuration.note,'g2');
 const original=readDraftRecovery(store,key,tab).local;store.setItem(key,JSON.stringify(original));const replacement=new DraftSync(store,key,8,()=>{},{locks,tabStorage:tab});replacement.stage(original.configuration);await resolveDraftRecovery(store,tab,key,original,saved,true,locks);
 assert.equal(readDraftRecord(store,draftOutboxKey(key,sync.owner)).pending,false);assert.equal(readDraftRecovery(store,key,tab).local.pending,true);assert.equal(readDraftRecovery(store,key,tab).local.draft_owner,replacement.owner);
});
test('late old-scope ACK writes only its own principal and retains newer unsent input',async()=>{
 const store=storage(),tab=storage(),locks=lockManager(),started=deferred(),release=deferred();globalThis.fetch=async()=>{started.resolve();await release.promise;return response({revision:8});};
 const old=new DraftSync(store,'ga-setup:one:w',7,()=>{},{locks,tabStorage:tab});old.save({note:'sent old'});await started.promise;old.save({note:'unsent old'});old.suspend();store.setItem('ga-setup:two:w',JSON.stringify({revision:12,pending:false,configuration:{note:'new account'}}));release.resolve();await assert.rejects(old.wait());
 assert.equal(readDraftRecord(store,'ga-setup:two:w').configuration.note,'new account');assert.equal(readDraftRecovery(store,'ga-setup:one:w',tab).local.configuration.note,'unsent old');
});
test('quota and corrupted journals block PUT while preserving original records',async()=>{
 let calls=0;globalThis.fetch=async()=>{calls++;return response({revision:8});};const locks=lockManager(),tab=storage();
 const quota={getItem:()=>null,setItem(){throw Error('quota');}};const full=new DraftSync(quota,'draft',7,()=>{},{locks,tabStorage:tab});assert.throws(()=>full.save({note:'keep on page'}),/quota/);assert.equal(calls,0);
 const store=storage();store.setItem('draft','{corrupt');const sync=new DraftSync(store,'draft',7,()=>{},{locks,tabStorage:tab});sync.save({note:'human'});await assert.rejects(sync.wait());assert.equal(store.getItem('draft'),'{corrupt');assert.equal(readDraftRecord(store,draftOutboxKey('draft',sync.owner)).configuration.note,'human');assert.equal(calls,0);
});

test('explicit recovery resolution prevents an old page ACK and retry from resurrecting resolved work',async()=>{
 const store=storage(),tab=storage(),locks=lockManager(),started=deferred(),release=deferred();let calls=0;
 globalThis.fetch=async()=>{calls++;started.resolve();await release.promise;return response({revision:8});};
 const sync=new DraftSync(store,'draft',7,()=>{},{locks,tabStorage:tab});sync.save({note:'g1 sent'});await started.promise;sync.save({note:'g2 pending'});const original=readDraftRecovery(store,'draft',tab).local;
 await resolveDraftRecovery(store,tab,'draft',original,{revision:7,configuration:{note:'current server'}},false,locks);release.resolve();await assert.rejects(sync.wait());sync.retry();await assert.rejects(sync.wait());
 assert.equal(calls,1);const row=readDraftRecord(store,draftOutboxKey('draft',sync.owner));assert.equal(row.pending,false);assert.equal(row.configuration.note,'g2 pending');assert.equal(row.recovery_choice,'server');
});

test('resolving another page generation never overwrites concurrent newer outbox text',async()=>{
 const store=storage(),tab=storage(),locks=lockManager(),sync=new DraftSync(store,'draft',7,()=>{},{locks,tabStorage:tab});sync.stage({note:'g1'});const original=readDraftRecovery(store,'draft',tab).local;store.setItem('draft',JSON.stringify(original));
 const write=store.setItem;let interleaved=false;store.setItem=(key,value)=>{if(key.includes(':resolved:')&&!interleaved){interleaved=true;sync.stage({note:'g2 human concurrent'});}write(key,value);};
 await resolveDraftRecovery(store,tab,'draft',original,saved,false,locks);const refreshed=readDraftRecovery(store,'draft',tab);assert.equal(refreshed.local.configuration.note,'g2 human concurrent');assert.equal(refreshed.local.draft_generation,2);assert.equal(refreshed.local.pending,true);
});

test('browser restart without session pointers exposes every principal-scoped pending outbox',()=>{
 const store=storage(),firstTab=storage(),secondTab=storage(),locks=lockManager(),b=new DraftSync(store,'ga-setup:human:w',7,()=>{},{locks,tabStorage:firstTab}),c=new DraftSync(store,'ga-setup:human:w',7,()=>{},{locks,tabStorage:secondTab});b.stage({note:'B preserved'});c.stage({note:'C preserved'});store.setItem('ga-setup:human:w',JSON.stringify(readDraftRecovery(store,'ga-setup:human:w',firstTab).local));
 const other=new DraftSync(store,'ga-setup:other:w',7,()=>{},{locks,tabStorage:storage()});other.stage({note:'private other principal'});
 const restarted=readDraftRecovery(store,'ga-setup:human:w',storage());assert.equal(restarted.candidates.length,2);assert.deepEqual(new Set(restarted.candidates.map(row=>row.configuration.note)),new Set(['B preserved','C preserved']));
});

test('session pointer refusal still exposes independently durable human text on refresh',()=>{
 const store=storage(),locks=lockManager(),blockedTab={getItem:()=>null,setItem(){throw Error('session quota');}},sync=new DraftSync(store,'ga-setup:human:w',7,()=>{},{locks,tabStorage:blockedTab});let calls=0;globalThis.fetch=async()=>{calls++;return response({revision:8});};
 assert.throws(()=>sync.save({note:'pointer failure human draft'}),/session quota/);assert.equal(calls,0);
 const restored=readDraftRecovery(store,'ga-setup:human:w',blockedTab);assert.equal(restored.local.pending,true);assert.equal(restored.local.configuration.note,'pointer failure human draft');assert.equal(restored.candidates.length,1);
});
