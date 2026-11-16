import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {createGraphSignalRoutingController} from '../src/graph-signal-routing-session-v134.mjs';
import {checkGraphRouteOptions,checkGraphRoutePreview,buildGraphRoutePreview,graphRouteRecord,checkGraphRouteRecord,graphRoutePreviewReadable,inspectSignalGraphTask,inspectSignalGraphDelivery} from '../src/graph-signal-routing-v134.mjs';
import {editTaskInput} from '../src/graph-task-inputs.mjs';
import {graphWireParseCanonical,graphWireCanonical,graphWireRequestJSON} from '../src/full-semantic-graph-wire.mjs';
import {fixture,syntheticAPI,Storage,session,scope,account,releaseID,businessID,entityID,sourceID,extractorID,id,wire,copy,seal,previewFor,freeze} from './graph-signal-routing-fixture-v134.mjs';
const locks={request:async(_key,_options,callback)=>callback()};
async function setup({storage=new Storage(),tab=session(),backend=null}={}){const f=await fixture(),service=backend||syntheticAPI(f),c=createGraphSignalRoutingController({api:service.api,scope,account,storage,session:tab,locks,crypto:webcrypto});c.setEnvironment({releaseID,enabled:true});await c.open();return {c,f,...service,storage,tab};}
async function fill(c,raw='-0.0'){
 c.edit('source_id',sourceID);c.edit('extractor_id',extractorID);c.edit('entity_ids',[entityID]);c.selectBusiness(businessID);c.setEntityFields(['entity']);
 const draft=copy(c.form.input_draft);draft.values.threshold={mode:'value',raw};c.edit('input_draft',draft);c.edit('purpose','核对实际触发对象并保留原依据');c.edit('required_fields',['temperature']);c.edit('reason','明确只允许原本机图范围');
}
test('closed server options retain unavailable graph choices and reject hash, schema and permission contradictions',async()=>{
 const f=await fixture();assert.equal((await checkGraphRouteOptions(wire(f.options),scope)).businesses[0].name,f.business.name);
 for(const change of [value=>value.options_hash='0'.repeat(64),value=>value.max_entities=50,value=>value.execution_granted=true,value=>value.businesses[0].graph_summary.abilities[0].adapter='http']){const bad=copy(f.options);change(bad);await assert.rejects(checkGraphRouteOptions(wire(bad),scope));}
 const unavailable=await seal({...f.options,can_enable:false,blocking_reason:'请先确认原自动运行范围',businesses:[{...f.business,available:false,reason:'业务当前停用',input_metadata:null,graph_summary:null}]},'options_hash');assert.equal((await checkGraphRouteOptions(wire(unavailable),scope)).businesses[0].available,false);
});
test('explicit per-trigger input expansion preserves negative zero and exponent while no first object or missing target is invented',async()=>{
 const {c,f}=await setup();await fill(c);const body=buildGraphRoutePreview(c.form,c.options,scope);const raw=graphWireCanonical(graphWireParseCanonical(graphRouteRecord(scope,'enable',null,{...body,schema:'signal-graph-routing-enable-command-v134',request_id:id(90),preview_hash:'b'.repeat(64),confirm_graph_execution:true}).exact_body));assert.ok(raw.includes('"threshold":-0.0'));
 const checked=await checkGraphRoutePreview(wire(await previewFor(f,body)),body,scope);assert.equal(checked.expanded[0].graph_inputs.entity,entityID);assert.ok(Object.is(checked.expanded[0].graph_inputs.threshold,-0));assert.equal(checked.command.goal_spec.deadline,null);
 c.edit('entity_ids',[]);assert.throws(()=>buildGraphRoutePreview(c.form,c.options,scope),/选择/);c.edit('entity_ids',[entityID]);c.edit('required_fields',[]);assert.throws(()=>buildGraphRoutePreview(c.form,c.options,scope),/核查字段/);
 await fill(c,'1e21');const exponent=await c.makePreview();assert.equal(exponent.expanded[0].graph_inputs.threshold,1e21);assert.ok(graphWireCanonical(exponent.command).includes('1e21'));
 const normalized=graphWireParseCanonical(graphWireCanonical(await previewFor(f,exponent.original_command)).replaceAll(':1e21',':1e+21'));const serverSpelling=await seal(normalized,'preview_hash');assert.equal((await checkGraphRoutePreview(wire(serverSpelling),exponent.original_command,scope)).command.graph_inputs.threshold,1e21);assert.equal((await checkGraphRoutePreview(wire(serverSpelling),graphWireParseCanonical(graphWireRequestJSON(exponent.original_command)),scope)).command.graph_inputs.threshold,1e21);
});
test('selected fields must exist for every selected object and numeric partial work stays raw with no preview mutation',async()=>{
 const {c,f,state}=await setup();await fill(c,'-');const before=state.calls.length;await assert.rejects(c.makePreview());assert.equal(c.form.input_draft.values.threshold.raw,'-');assert.equal(state.calls.slice(before).filter(row=>row.path.endsWith('/preview')).length,0);
 const bad=await seal({...f.options,businesses:[{...f.business,goal_fields:[{...f.business.goal_fields[0],entity_ids:[id(8)]}]}]},'options_hash');c.options=await checkGraphRouteOptions(wire(bad),scope);assert.throws(()=>buildGraphRoutePreview(c.form,c.options,scope),/所有已选对象/);
});
test('preview binds complete graph and literal scope, rejects changed body or inconsistent actual actor inventory',async()=>{
 const {c,f}=await setup();await fill(c);const command=buildGraphRoutePreview(c.form,c.options,scope),good=await previewFor(f,command);await checkGraphRoutePreview(wire(good),command,scope);
 const bad=copy(good);bad.binding.abilities=[];const resealed=await seal(bad,'preview_hash');await assert.rejects(checkGraphRoutePreview(wire(resealed),command,scope));
 await assert.rejects(checkGraphRoutePreview(wire(good),{...command,reason:'另一份确认范围'},scope));
 const tooMany=copy(good);tooMany.binding.graph.nodes.find(node=>node.kind==='ability').execution.arguments={areas:Array.from({length:501},()=> '明确区域')};assert.throws(()=>graphRoutePreviewReadable(tooMany),/完整核对/);
});
test('an edit after actual preview invalidates consent and creates no immutable request or POST',async()=>{
 const {c,state,storage}=await setup();await fill(c);await c.makePreview();c.edit('confirmed',true);c.edit('purpose','修改后的原业务目的');await assert.rejects(c.enable(),/预览/);assert.equal(c.preview,null);assert.equal(c.form.confirmed,false);assert.equal(state.posts.length,0);assert.equal([...storage.map.keys()].some(key=>key.includes(':request:')),false);
});
test('commit then response loss is restored through original HTTP200 lookup after fresh session without a second POST',async()=>{
 const {c,state,storage,f,api}=await setup();await fill(c);await c.makePreview();c.edit('confirmed',true);state.lose=true;await assert.rejects(c.enable());const original=c.pending.exact_body,request=c.pending.body.request_id;assert.equal(c.pendingNotFound,false);assert.equal(state.posts.length,1);
 c.dispose();const cold=await setup({storage,tab:session(),backend:{state,api}});assert.equal(cold.c.pending.exact_body,original);await cold.c.recover();assert.equal(cold.c.pending,null);assert.equal(state.posts.length,1);assert.equal(JSON.parse(storage.getItem(cold.c.prefix+'request:'+request)).exact_body,original);assert.ok(cold.c.listing.routes.length);assert.equal(f.business.name,cold.c.listing.routes[0].business.name);
});
test('negative lookup permits only explicit same-body resend and unrelated form edits never replace pending',async()=>{
 const {c,state}=await setup();await fill(c);await c.makePreview();c.edit('confirmed',true);state.notSent=true;await assert.rejects(c.enable());const body=c.pending.exact_body;c.edit('purpose','另一页仍需保留的新目的');await c.recover();assert.equal(c.pendingNotFound,true);assert.equal(state.posts.length,1);state.notSent=false;await c.resend();assert.equal(state.posts.length,2);assert.equal(state.posts[1].body,body);assert.equal(c.form.purpose,'另一页仍需保留的新目的');assert.equal(c.pending,null);
});
test('new command never dispatches when local immutable write fails, while 1000 keypresses use bounded current-owner slots',async()=>{
 const {c,storage,state}=await setup();for(let index=1;index<=1000;index++)c.edit('reason','理'.repeat(index));assert.equal([...storage.map.keys()].filter(key=>key.includes(':form:'+c.owner+':')).length,2);assert.ok([...storage.map.values()].reduce((total,raw)=>total+raw.length,0)<15000);await fill(c);await c.makePreview();c.edit('confirmed',true);storage.fail=true;await assert.rejects(c.enable());assert.equal(state.posts.length,0);assert.equal(c.guard(),false);assert.equal(c.form.reason,'明确只允许原本机图范围');storage.fail=false;c.persist();assert.equal(c.guard(),true);
});
test('cloned session snapshot survives source owner cycling both slots before the second tab activates',async()=>{
 const {c,storage,tab,api,state}=await setup();c.edit('purpose','复制之前的甲稿末字');const cloned=new Storage(tab);for(let index=0;index<6;index++)c.edit('purpose','甲页后来修改'+index);
 const b=await setup({storage,tab:cloned,backend:{api,state}});assert.equal(b.c.form.purpose,'复制之前的甲稿末字');assert.notEqual(b.c.owner,c.owner);b.c.edit('purpose','乙页独立末字');assert.equal(c.form.purpose,'甲页后来修改5');assert.ok([...storage.map.values()].some(raw=>raw.includes('乙页独立末字')));assert.equal(tab.getItem(c.pointerKey).includes('甲页后来修改5'),true);
});
test('identity mismatches hide before storage opens, focus during deferred action conceals and stale identity never reopens it',async()=>{
 const {c,state,storage,tab}=await setup();c.edit('purpose','甲账号本机私稿');const raw=tab.getItem(c.pointerKey);let release;state.meGate=new Promise(resolve=>release=resolve);const old=c.load().catch(()=>{});await Promise.resolve();state.actor={...account,user_id:id(98)};const epoch=c.identityEpoch;c.recheckIdentity();assert.equal(c.identityStatus,'checking');assert.ok(c.identityEpoch>epoch);release();state.meGate=null;await old;for(let index=0;index<5;index++)await Promise.resolve();assert.notEqual(c.identityStatus,'verified');assert.equal(tab.getItem(c.pointerKey),raw);assert.ok(storage.length);
});
test('source unavailable during independent refresh does not turn settled receipt back into unknown or resend',async()=>{
 const {c,state}=await setup();await fill(c);await c.makePreview();c.edit('confirmed',true);state.lose=true;await assert.rejects(c.enable());state.refreshFail=true;const result=await c.recover();assert.equal(result.found,true);assert.equal(c.pending,null);assert.equal(state.posts.length,1);assert.ok(c.error.includes('已保存'));assert.ok(c.notice.includes('已保存'));
});
test('a forged recovery actor or original hash cannot settle the exact pending pointer',async()=>{
 const h=await setup();await fill(h.c);await h.c.makePreview();h.c.edit('confirmed',true);h.state.lose=true;await assert.rejects(h.c.enable());const pending=h.c.pending,raw=h.storage.getItem(h.c.pendingKey),saved=h.state.saved.get(pending.body.request_id);const badReceipt=await seal({...saved.receipt,actor_id:id(99)},'receipt_hash');h.state.saved.set(pending.body.request_id,{...saved,receipt:badReceipt});await assert.rejects(h.c.recover());assert.equal(h.storage.getItem(h.c.pendingKey),raw);assert.equal(h.c.pending.exact_body,pending.exact_body);
});
test('route-gate matrix is independent and fail closed, while old normal Tasks have no routing restriction',()=>{
 assert.equal(inspectSignalGraphTask({}),null);assert.throws(()=>inspectSignalGraphTask({signal_graph_origin_v134:{}}));
 for(const status of ['active','paused','expired','unavailable']){const value={schema:'signal-graph-task-state-v134',status,message:'原授权状态已由服务器核对',can_submit_new_result:status==='active',can_retry_ability:status==='active',delivery_id:id(30)};assert.equal(inspectSignalGraphTask({signal_graph_route_v134:value}).status,status);assert.throws(()=>inspectSignalGraphTask({signal_graph_route_v134:{...value,can_retry_ability:status!=='active'}}));}
});
test('signal graph status cannot hide malformed state or claim a queued reservation is a created Task',()=>{
 const value={schema:'signal-graph-routing-public-v134',delivery_id:id(30),status:'reserved',task_id:null,message:'原异常已保留',terminal:false,automatic_retry:false};assert.equal(inspectSignalGraphDelivery({graph_route:value}).status,'reserved');assert.throws(()=>inspectSignalGraphDelivery({graph_route:{...value,status:'created'}}));assert.throws(()=>inspectSignalGraphDelivery({graph_route:{...value,automatic_retry:true}}));
});
test('original record mirror is never numeric authority; exact original tokens are restored and tamper is rejected',async()=>{
 const {c}=await setup();await fill(c,'1e21');const command=buildGraphRoutePreview(c.form,c.options,scope),record=graphRouteRecord(scope,'enable',null,{...command,schema:'signal-graph-routing-enable-command-v134',request_id:id(91),preview_hash:'a'.repeat(64),confirm_graph_execution:true});assert.equal(checkGraphRouteRecord(copy(record),scope).body.graph_inputs.threshold,1e21);const changed=copy(record);changed.body.reason='替换内容';assert.throws(()=>checkGraphRouteRecord(changed,scope));assert.throws(()=>checkGraphRouteRecord(record,{...scope,workspace_id:id(99)}));
});
test('lack of browser Web Locks blocks any new command while an intact draft and read-only scope are retained',async()=>{
 const {c,state,storage}=await setup();await fill(c);await c.makePreview();c.edit('confirmed',true);c.locks=null;await assert.rejects(c.enable(),/多窗口保存保护/);assert.equal(state.posts.length,0);assert.equal(c.form.purpose,'核对实际触发对象并保留原依据');assert.ok(storage.length);assert.equal(c.guard(),true);
});
test('an old completed enable keeps the immutable receipt while a later current paused projection is displayed',async()=>{
 const {c,state}=await setup();await fill(c);await c.makePreview();c.edit('confirmed',true);await c.enable();const original=c.requests[0],body=original.record.body,saved=state.saved.get(body.request_id),marker=c.storage.getItem(c.prefix+'completed:'+body.request_id);const routes=saved.routes.map(route=>({...route,status:'paused',paused_at:'2026-10-20T01:00:00+00:00'}));state.listing=freeze({...state.listing,instance_version:3,routes});state.saved.set(body.request_id,freeze({...saved,instance_version:3,routes,current_matches:false}));await c.restoreRequest(original);assert.equal(c.pending,null);assert.equal(c.listing.routes[0].status,'paused');assert.equal(c.storage.getItem(c.prefix+'completed:'+body.request_id),marker);assert.equal(state.posts.length,1);
});
