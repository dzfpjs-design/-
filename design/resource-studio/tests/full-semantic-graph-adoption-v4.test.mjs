import test from 'node:test';
import assert from 'node:assert/strict';
import {sourceAdoptionFixture,waitPolicy} from './full-semantic-graph-adoption-v4-fixture.mjs';
import {graphFixture} from './full-semantic-graph-adoption-fixture.mjs';
import {graphWireCanonical,graphWireDigest,graphWireRequestJSON} from '../src/full-semantic-graph-wire.mjs';
import {FullSemanticGraphAdoptionController,checkedGraphAdoptionPending,verifyGraphEditorContext,verifyGraphAdoptionPreview,graphAdoptionPendingScopes} from '../src/full-semantic-graph-adoption-controller.mjs';
const copy=v=>JSON.parse(JSON.stringify(v));
const posts=f=>f.calls.filter(c=>c.path.endsWith('/adopt'));
const accept=f=>async(_scope,settle)=>settle(copy(f.saved));
async function ready(f){const c=await f.open();c.edit(f.ready(c));assert.deepEqual(c.issues(),[]);return c;}

test('source-bound preview preserves exact registered schema and new local fraction, without adopting',async()=>{
 const f=await sourceAdoptionFixture(),c=await ready(f);await c.makePreview();
 const domain=c.preview.command.owner_bindings.find(row=>row.owner_path==='/domain'),subscription=c.preview.command.graph.subscriptions[0];
 assert.equal(domain.resolution,'typed');assert.match(domain.reason,/发起人从本次选定对象中明确选择/);
 assert.deepEqual(domain.clauses.find(clause=>clause.source_field==='value').targets,[{kind:'wait',id:'wait_1'},{kind:'value_ref',id:subscription.correlation_ref}]);
 const sent=f.calls.find(v=>v.path.endsWith('/preview'));
 assert.ok(sent.path.includes('/v4/'));assert.ok(sent.body.includes('"minimum":1.0'));assert.ok(sent.body.includes('"multipleOf":1e-05'));assert.ok(sent.body.includes('"timeout_seconds":60.5'));
 assert.equal(c.preview.family,'v4');assert.equal(c.preview.command.schema,'report-full-semantic-graph-preview-command-v4');
 assert.equal(graphWireCanonical(c.preview.command.event_source_bindings.subscription_1.contract),graphWireCanonical(f.contract));
 assert.equal(posts(f).length,0);await assert.rejects(c.submit(false));
});

test('v4 unknown response reload sends the exact original body and never borrows the current context route',async()=>{
 const f=await sourceAdoptionFixture(),c=await ready(f);await c.makePreview();f.mode.before=true;await assert.rejects(c.submit(true));
 const pending=c.readPending(),exact=pending.exact_body;assert.equal(pending.schema,'full-semantic-graph-adoption-pending-v3');assert.equal(pending.family,'v4');
 assert.ok(exact.includes('"minimum":1.0'));c.dispose();
 const next=new FullSemanticGraphAdoptionController({...f.options,api:f.api,contextFamily:'v3'});
 assert.equal(await next.recover(),false);assert.equal(posts(f).length,1);f.mode.before=false;await next.recover(true);
 assert.equal(posts(f).length,2);assert.equal(posts(f)[1].body,exact);assert.ok(next.pending.receipt);
 assert.ok(f.calls.filter(v=>v.path.includes('/requests/')).every(v=>v.path.includes('/v4/')));
 await next.acceptCurrent(accept(f));assert.equal(next.pending,null);
});

test('saved v4 nonce recovers after source revoke or current editor failure without a second adoption',async()=>{
 const f=await sourceAdoptionFixture(),c=await ready(f);await c.makePreview();f.mode.after=true;await assert.rejects(c.submit(true));const exact=c.pending.exact_body;
 f.context.event_wait.sources[0].available=false;f.context.event_wait.sources[0].status='revoked';f.context.event_wait.sources[0].reason='来源已暂停';f.context.event_wait.available=false;f.context.event_wait.reason='暂无可用来源';f.mode.contextFailure=true;c.dispose();
 const restored=new FullSemanticGraphAdoptionController({...f.options,api:f.api,contextFamily:'v4'});await assert.rejects(restored.load());
 await restored.recover();assert.ok(restored.pending.receipt);assert.equal(restored.pending.exact_body,exact);assert.equal(posts(f).length,1);
 await restored.acceptCurrent(accept(f));assert.equal(restored.pending,null);
});

test('v4 revisions retain exact source contracts and freeze the previous receipt',async()=>{
 const f=await sourceAdoptionFixture(),c=await ready(f);await c.makePreview();await c.submit(true);const first=copy(c.pending.receipt);await c.acceptCurrent(accept(f));await c.load();
 c.chooseBusiness(f.context.design.result.businesses[0].name);assert.deepEqual(c.state.value.parent,{id:first.origin.adoption_id,receipt_hash:first.receipt.receipt_hash});
 await c.makePreview();assert.equal(c.preview.kind,'revision');assert.equal(c.preview.command.schema,'report-full-semantic-graph-revision-preview-command-v4');await c.submit(true);
 assert.equal(c.pending.receipt.receipt.schema,'report-full-semantic-graph-revision-receipt-v4');assert.equal(c.pending.receipt.receipt.parent_id,first.origin.adoption_id);
 assert.equal(posts(f)[1].body,c.pending.exact_body);await verifyGraphEditorContext(await f.open().then(v=>v.context),f.scope);
});

test('a locally normalized or changed v4 body cannot replace the signed original, and scope/family cannot migrate',async()=>{
 const f=await sourceAdoptionFixture(),c=await ready(f);await c.makePreview();f.mode.before=true;await assert.rejects(c.submit(true));const original=f.storage.getItem(c.outboxKey),value=JSON.parse(original);
 for(const change of [p=>p.family='v3',p=>p.scope.actor_id=crypto.randomUUID(),p=>p.body.business_name='另一业务',p=>p.exact_body=p.exact_body.replace('"minimum":1.0','"minimum":2.0')]){
  const bad=copy(value);change(bad);f.storage.setItem(c.outboxKey,JSON.stringify(bad));await assert.rejects(c.recover());assert.ok(f.storage.getItem(c.outboxKey));assert.equal(posts(f).length,1);
 }
 f.storage.setItem(c.outboxKey,original);const normalized=copy(value);normalized.exact_body=JSON.stringify(normalized.body);f.storage.setItem(c.outboxKey,JSON.stringify(normalized));await assert.rejects(c.recover());assert.equal(posts(f).length,1);
 f.storage.setItem(c.outboxKey,original);assert.equal(graphAdoptionPendingScopes(f.storage,{actorId:f.scope.actor_id,workspaceId:f.scope.workspace_id})[0].request_id,value.body.request_id);
});

test('preview seals cannot conceal source contract normalization or a forged v3 base binding hash',async()=>{
 const f=await sourceAdoptionFixture(),c=await ready(f);await c.makePreview();const {command}=c.preview;
 for(const change of [b=>({...b,base_binding_hash:'a'.repeat(64)}),b=>({...b,graph:{...b.graph,subscriptions:b.graph.subscriptions.map(s=>({...s,output_schema:copy(s.output_schema)}))}})]){
  const body=f.typed(graphWireRequestJSON(change(c.preview.value.binding))),{binding_hash,...unsigned}=body,binding=await f.seal(unsigned,'binding_hash');
  const {preview_hash,...rest}=c.preview.value,bad=await f.seal({...rest,binding},'preview_hash');
  await assert.rejects(verifyGraphAdoptionPreview(bad,command,'root',{scope:f.scope,context:c.context,members:f.members}));
 }
});

test('v2 context validates exact source/policy pins; inactive history may retain individually healthy sources',async()=>{
 const f=await sourceAdoptionFixture(),context=(await f.open()).context;await verifyGraphEditorContext(context,f.scope);
 const inactive={...context,active:false,proofs:context.proofs.map(p=>({...p,current_source_verified:false})),event_wait:{...context.event_wait,available:false,reason:'旧版本仅供查看'}};
 await verifyGraphEditorContext(inactive,f.scope);
 for(const event_wait of [{...context.event_wait,policy:{...waitPolicy,source_time_verified:true}},{...context.event_wait,sources:[{...context.event_wait.sources[0],contract_hash:'a'.repeat(64)}]},{...context.event_wait,sources:[{...context.event_wait.sources[0],status:'revoked'}]}])await assert.rejects(verifyGraphEditorContext({...context,event_wait},f.scope));
});

test('old v1 outbox continues on original routes even when the controller reads v4 context',async()=>{
 const f=await graphFixture(),c=await f.open();c.edit(f.ready());await c.makePreview();f.mode.before=true;await assert.rejects(c.submit(true));const legacy=JSON.parse(f.storage.getItem(c.outboxKey));legacy.schema='full-semantic-graph-adoption-pending-v1';delete legacy.family;delete legacy.exact_body;const old=JSON.stringify(legacy);f.storage.setItem(c.outboxKey,old);c.dispose();
 const restored=new FullSemanticGraphAdoptionController({...f.options,contextFamily:'v4'});assert.equal(await restored.recover(),false);assert.equal(f.storage.getItem(restored.outboxKey),old);
 f.mode.before=false;await restored.recover(true);assert.ok(restored.pending.receipt);assert.equal(restored.pending.schema,'full-semantic-graph-adoption-pending-v1');assert.ok(f.calls.filter(v=>v.path.includes('/requests/')).every(v=>!v.path.includes('/v4/')));
});

test('transport-only negative zero survives v2 storage mirror without claiming PostgreSQL durability',async()=>{
 const f=await sourceAdoptionFixture(),c=await ready(f);await c.makePreview();f.mode.before=true;await assert.rejects(c.submit(true));
 const p=JSON.parse(f.storage.getItem(c.outboxKey));p.schema='full-semantic-graph-adoption-pending-v2';p.exact_body=p.exact_body.replace('"timeout_seconds":60.5','"timeout_seconds":-0.0');p.body=JSON.parse(JSON.stringify(JSON.parse(p.exact_body)));
 const restored=checkedGraphAdoptionPending(p,f.scope);assert.ok(Object.is(restored.body.graph.nodes.find(n=>n.kind==='wait').timeout_seconds,-0));assert.ok(graphWireCanonical(restored.body).includes('"timeout_seconds":-0.0'));
 // This isolated serializer check does not submit an invalid zero deadline.
 assert.equal(posts(f).length,1);assert.equal(typeof await graphWireDigest(restored.body),'string');
});

test('old v2 source outbox is read and replayed unchanged instead of being upgraded to typed v3 storage',async()=>{
 const f=await sourceAdoptionFixture(),c=await ready(f);await c.makePreview();f.mode.before=true;await assert.rejects(c.submit(true));
 const legacy=JSON.parse(f.storage.getItem(c.outboxKey));legacy.schema='full-semantic-graph-adoption-pending-v2';const original=JSON.stringify(legacy);f.storage.setItem(c.outboxKey,original);c.dispose();
 const next=new FullSemanticGraphAdoptionController({...f.options,api:f.api,contextFamily:'v3'});
 assert.equal(await next.recover(),false);assert.equal(f.storage.getItem(next.outboxKey),original);assert.equal(posts(f).length,1);
 f.mode.before=false;await next.recover(true);assert.equal(next.pending.schema,'full-semantic-graph-adoption-pending-v2');assert.equal(posts(f)[1].body,legacy.exact_body);assert.ok(next.pending.receipt);
});
