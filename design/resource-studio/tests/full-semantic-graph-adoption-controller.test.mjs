import test from 'node:test';
import assert from 'node:assert/strict';
import {graphFixture,seal} from './full-semantic-graph-adoption-fixture.mjs';
import {copy} from '../src/report-evidence-reading-contract.mjs';
import {FullSemanticGraphAdoptionController,verifyGraphEditorContext,verifyGraphAdoptionPreview} from '../src/full-semantic-graph-adoption-controller.mjs';
const posts=f=>f.calls.filter(c=>c.path.endsWith('/adopt'));
async function ready(f){const c=await f.open();c.edit(f.ready());assert.deepEqual(c.issues(),[]);return c;}
const accept=f=>async(scope,settle)=>{assert.deepEqual(scope,f.scope);await settle(copy(f.saved));};

test('complete human choices go to the actual closed preview and preserve original sources',async()=>{
 const f=await graphFixture(),c=await ready(f),original=copy(c.state.value);await c.makePreview();assert.equal(c.preview.kind,'root');assert.equal(c.preview.value.binding.source_semantics_status,'not_certified');assert.deepEqual(c.state.value,original);assert.equal(posts(f).length,0);await assert.rejects(c.submit(false));assert.equal(posts(f).length,0);
});
test('unsent choices survive reload in a separate working copy; updates never overwrite prior human input',async()=>{
 const f=await graphFixture(),c=await ready(f),key=c.key,original=f.storage.getItem(key);c.dispose();const next=await f.open();assert.notEqual(next.key,key);assert.deepEqual(next.state.value,JSON.parse(original).value);next.edit({...next.state.value,ui:{...next.state.value.ui,privateNote:'保留的人稿'}});assert.equal(f.storage.getItem(key),original);assert.ok(next.localSaved);
});
test('lost adoption response restores same immutable request via GET without another adoption',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();f.mode.after=true;await assert.rejects(c.submit(true));const pending=copy(c.pending);assert.equal(pending.rejection,null);assert.equal(posts(f).length,1);c.dispose();f.mode.after=false;const restored=await f.open();await restored.recover();assert.deepEqual(restored.pending.body,pending.body);assert.ok(restored.pending.receipt);assert.equal(posts(f).length,1);await restored.acceptCurrent(accept(f));assert.equal(restored.pending,null);assert.equal(f.storage.getItem(restored.outboxKey),null);assert.deepEqual(JSON.parse(f.storage.getItem(restored.prefix+'accepted:'+pending.body.request_id)).body,pending.body);
});
test('not found never discards unknown request; explicit retry uses original nonce and complete body',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();f.mode.before=true;await assert.rejects(c.submit(true));const body=copy(c.pending.body);await assert.rejects(c.archiveRejected());assert.equal(await c.recover(),false);assert.equal(posts(f).length,1);f.mode.before=false;await c.recover(true);assert.deepEqual(JSON.parse(posts(f)[1].body),body);assert.ok(c.pending.receipt);
});
test('explicit rejection can be archived, while no unknown or accepted request can be discarded',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();f.mode.reject=true;await assert.rejects(c.submit(true));const pending=copy(c.pending);assert.equal(pending.rejection.status,409);await c.archiveRejected();assert.equal(c.pending,null);assert.deepEqual(JSON.parse(f.storage.getItem(c.prefix+'rejected:'+pending.body.request_id)),pending);
});
test('current configuration must be settled through the real callback before removing accepted outbox',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();await c.submit(true);await assert.rejects(c.acceptCurrent(async()=>copy(f.saved)),/尚未读入/);assert.ok(c.pending.receipt);await assert.rejects(c.acceptCurrent(async(_scope,settle)=>settle({revision:f.saved.revision,configuration:{version:1}})));assert.ok(c.pending.receipt);await c.acceptCurrent(accept(f));assert.equal(c.pending,null);
});
test('after adoption, a new revision explicitly binds the current parent and immutable previous receipt',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();await c.submit(true);const first=copy(c.pending.receipt);await c.acceptCurrent(accept(f));await c.load();c.chooseBusiness('通用资料确认');assert.deepEqual(c.state.value.parent,{id:first.origin.adoption_id,receipt_hash:first.receipt.receipt_hash});c.edit({...c.state.value,owner_bindings:c.state.value.owner_bindings.map((row,i)=>i===1?{...row,reason:row.reason+' 明确修订目的。'}:row)});await c.makePreview();assert.equal(c.preview.kind,'revision');await c.submit(true);assert.equal(c.pending.receipt.receipt.parent_id,first.origin.adoption_id);assert.equal(c.pending.receipt.receipt.root_id,first.origin.adoption_id);assert.equal(posts(f).length,2);
});
test('changing a draft during preview invalidates that response and does not adopt',async()=>{
 const f=await graphFixture(),c=await ready(f);let resume,entered;const waiting=new Promise(r=>entered=r);f.mode.previewPause=()=>new Promise(r=>{resume=r;entered();});const pending=c.makePreview();await waiting;c.state.generation++;resume();await assert.rejects(pending,/输入.*变化/);assert.equal(c.preview,null);assert.equal(posts(f).length,0);
});
test('quota failure retains all current input, prevents preview and allows retry after storage recovers',async()=>{
 const f=await graphFixture(),c=await ready(f),write=f.storage.setItem;f.storage.setItem=()=>{throw Error('合成容量不足');};c.edit({...c.state.value,ui:{...c.state.value.ui,privateNote:'不得丢失'}});assert.equal(c.localSaved,false);assert.equal(c.state.value.ui.privateNote,'不得丢失');await assert.rejects(c.makePreview());assert.equal(posts(f).length,0);f.storage.setItem=write;assert.equal(c.persist(),true);
});
test('concurrent working-key modification preserves the other copy and allows explicit fork',async()=>{
 const f=await graphFixture(),c=await ready(f);const other={...JSON.parse(f.storage.getItem(c.key)),generation:999};f.storage.setItem(c.key,JSON.stringify(other));c.edit({...c.state.value,ui:{...c.state.value.ui,privateNote:'本页输入'}});assert.equal(c.localSaved,false);assert.equal(JSON.parse(f.storage.getItem(c.key)).generation,999);const old=c.key;c.forkWorking();assert.notEqual(c.key,old);assert.equal(c.localSaved,true);assert.equal(c.state.value.ui.privateNote,'本页输入');
});
test('session switches hide earlier source and deny adoption without overwriting original journal',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();const old=f.storage.getItem(c.key);f.tab.setItem('ga-setup-csrf-user',crypto.randomUUID());await assert.rejects(c.submit(true));assert.equal(c.denied,true);assert.equal(c.context,null);assert.equal(posts(f).length,0);assert.equal(f.storage.getItem(c.key),old);
});
test('missing multi-window protection permits result recovery but blocks new adoption',async()=>{
 const f=await graphFixture(),c=await f.open({locks:null});c.edit(f.ready());await assert.rejects(c.makePreview(),/多窗口/);assert.equal(posts(f).length,0);
});
test('tampered receipt and pending hashes are retained and fail closed before cleanup or another adoption',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();await c.submit(true);const pending=copy(c.pending);pending.receipt.receipt.configuration_hash='b'.repeat(64);f.storage.setItem(c.outboxKey,JSON.stringify(pending));await assert.rejects(c.acceptCurrent(accept(f)));assert.ok(f.storage.getItem(c.outboxKey));assert.equal(posts(f).length,1);
});
test('preview rehash alone cannot conceal dropped graph nodes, changed role or missing owner coverage',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();const command=c.preview.command;
 for(const change of [b=>b.graph.nodes.pop(),b=>b.role_bindings['核对负责人'].user_id=crypto.randomUUID(),b=>b.owner_bindings.pop()]){const value=copy(c.preview.value);change(value.binding);value.binding=await seal(value.binding,'binding_hash');const changed=await seal(value,'preview_hash');await assert.rejects(verifyGraphAdoptionPreview(changed,command,'root',{scope:f.scope,context:c.context,members:f.members}));}
});
test('context requires complete exact owner field inventory and source identity',async()=>{
 const f=await graphFixture();await verifyGraphEditorContext(f.context,f.scope);const changed=copy(f.context);changed.owners[2].source_fields.pop();await assert.rejects(verifyGraphEditorContext(changed,f.scope));const bad=copy(f.context);bad.handoff_id=crypto.randomUUID();await assert.rejects(verifyGraphEditorContext(bad,f.scope));
});
test('other-business and earlier page drafts remain recoverable after explicitly choosing a fresh arrangement',async()=>{
 const f=await graphFixture(),c=await ready(f),oldKey=c.key,old=copy(c.state);c.chooseBusiness('通用资料确认');assert.notEqual(c.key,oldKey);assert.deepEqual(JSON.parse(f.storage.getItem(oldKey)),old);assert.ok(c.otherDrafts.some(d=>d.key===oldKey));c.restoreDraft(oldKey);assert.deepEqual(c.state.value,old.value);
});

test('a failure response after a real commit cannot discard the original receipt as a rejection',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();f.mode.afterReject=true;await assert.rejects(c.submit(true));assert.equal(c.pending.rejection.status,409);
 await assert.rejects(c.archiveRejected(),/已保存原请求/);assert.equal(c.pending.rejection,null);assert.ok(c.pending.receipt);assert.ok(f.storage.getItem(c.outboxKey));assert.equal(posts(f).length,1);
 c.dispose();const restored=await f.open();await restored.recover();await restored.acceptCurrent(accept(f));assert.equal(restored.pending,null);assert.equal(posts(f).length,1);
});

test('a locally resealed fake receipt cannot replace the actual original server ledger',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();await c.submit(true);const pending=copy(c.pending);
 pending.receipt.receipt.record_hash='b'.repeat(64);pending.receipt.receipt=await seal(pending.receipt.receipt,'receipt_hash');f.storage.setItem(c.outboxKey,JSON.stringify(pending));c.pending=pending;
 await assert.rejects(c.recover(),/真实记录不一致/);await assert.rejects(c.acceptCurrent(accept(f)));assert.ok(f.storage.getItem(c.outboxKey));assert.equal(posts(f).length,1);
});
test('local receipt presence never clears the nonce when the server cannot find its real record',async()=>{
 const f=await graphFixture(),c=await ready(f);await c.makePreview();await c.submit(true);f.requests.clear();await assert.rejects(c.acceptCurrent(accept(f)),/尚未核实/);assert.ok(c.pending.receipt);assert.ok(f.storage.getItem(c.outboxKey));assert.equal(posts(f).length,1);
});
