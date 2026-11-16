import test from 'node:test';
import assert from 'node:assert/strict';
import {graphWireCanonical,graphWireDigest,graphWireParseCanonical} from '../src/full-semantic-graph-wire.mjs';
import {nativeGraphSourceContractV136,verifyNativeGraphSourceContractV136,nativeSourceCommandV136,nativeSourceCommandHashV136,nativeSourceOutboxV136,checkNativeSourceOptionsV136,checkNativeSourceProducerV136,checkNativeSourceReceiptV136,checkNativeSourceStateV136,checkNativeAudioStagesV136,emptyNativeSourceFormV136,nativeSourceFormV136} from '../src/native-graph-sources-v136.mjs';
import {NativeGraphSourceJournalV136,nativeSourcePrefixV136} from '../src/native-graph-sources-journal-v136.mjs';
import {enumerateNativeSourceRecoveryV136,readNativeSourceRecoveryV136} from '../src/native-graph-sources-recovery-v136.mjs';
import {NativeGraphSourcesControllerV136} from '../src/native-graph-sources-controller-v136.mjs';
import {verifyWaitContract} from '../src/full-semantic-graph-adoption-protocol.mjs';
import {freezeGraphEventSource,readGraphEventSource} from '../src/full-semantic-graph-source-draft.mjs';
import {id,account,scope,source,trace,clone,memory,sessionFor,locks,contract,command,installation,producer,outbox,server,deferred} from './fixtures/native-graph-sources-v136.mjs';

function journal(storage=memory(),session=sessionFor(),sharedLocks=locks()){
 return new NativeGraphSourceJournalV136({scope,storage,session,locks:sharedLocks,checkForm:nativeSourceFormV136,checkRequest:nativeSourceOutboxV136});
}
async function scene({storage=memory(),session=sessionFor(),sharedLocks=locks(),backend=null,who=account}={}){
 backend??=await server();const controller=new NativeGraphSourcesControllerV136({api:backend.api,scope,account:who,storage,session,locks:sharedLocks});await controller.open();
 return {controller,backend,storage,session,sharedLocks};
}
async function preview(scene){const c=scene.controller;c.edit({label:'待人工核对的本地语音'});c.choose('source',source.id);c.choose('installation',trace.installation_id);await c.previewRegistration();return c.preview;}
const registrationPosts=backend=>backend.state.calls.filter(call=>call.path===backend.base&&call.options.method==='POST');

test('native full contract and canonical outer hash match explicit family, without extractor',async()=>{
 const value=await contract();assert.equal(await verifyWaitContract(value),value);assert.equal(await verifyNativeGraphSourceContractV136(value),value);
 const row=await producer();assert.equal(await checkNativeSourceProducerV136(row,scope),row);
 const canonical=freezeGraphEventSource({producer_id:row.id,label:row.label,contract_hash:row.contract_hash,contract:row.contract});
 const restored=readGraphEventSource({event_source_fragments:{wait:canonical}},'wait');assert.equal(graphWireCanonical(restored.contract),graphWireCanonical(value));assert.equal(Object.hasOwn(restored.contract,'extractor_id'),false);
});
for(const mutation of [value=>{value.extractor_id=id(20);},value=>{value.schema='graph-event-observation-source-contract-v1';},value=>{value.adapter_id='processed-observation-v1';},value=>{value.run_type='VideoRun';},value=>{value.output_schema.properties.observed_at={type:'string'};},value=>{value.output_schema.properties.physical_state_verified={const:true};},value=>{value.installation.backend='cloud';},value=>{value.installation.generation=1.5;},value=>{delete value.installation_hash;},value=>{value.result_schema.properties.attributes.properties['机器转写文字'].maxLength=20000;}]){
 test('native closed contract rejects mixed family or changed semantics '+mutation.toString(),async()=>{const value=clone(await contract());mutation(value);assert.throws(()=>nativeGraphSourceContractV136(value));await assert.rejects(()=>verifyWaitContract(value));});
}
test('native hashes reject re-sealed outer contract when original installation hash changed',async()=>{
 const value=clone(await contract());value.installation.generation++;value.contract_hash=await graphWireDigest(Object.fromEntries(Object.entries(value).filter(([key])=>key!=='contract_hash')));await assert.rejects(()=>verifyNativeGraphSourceContractV136(value));
});
test('old processing source remains explicit and original float schema spelling is preserved',async()=>{
 const value={schema:'graph-event-observation-source-contract-v1',adapter_id:'processed-observation-v1',event_kind:'observation.data',correlation_kind:'entity_id',source_id:id(40),source_hash:'c'.repeat(64),source_release_version:'1.0.0',extractor_id:id(41),extractor_hash:'d'.repeat(64),extractor_release_version:'1.0.0',result_schema:{type:'object',properties:{value:{type:'number',minimum:0}}},output_schema:{type:'object',properties:{value:{type:'number',minimum:0}}}};
 const parsed=graphWireParseCanonical(graphWireCanonical(value).replaceAll('"minimum":0','"minimum":-0.0'));const withHashes={...parsed,output_schema_hash:await graphWireDigest(parsed.output_schema)};withHashes.contract_hash=await graphWireDigest(withHashes);
 await verifyWaitContract(withHashes);const fragment=freezeGraphEventSource({producer_id:id(42),label:'原处理来源',contract_hash:await graphWireDigest(withHashes),contract:withHashes});assert.match(fragment,/"minimum":-0\.0/);assert.equal(graphWireCanonical(readGraphEventSource({event_source_fragments:{w:fragment}},'w').contract),graphWireCanonical(withHashes));
});
test('register/revoke/retirement hash domains are actual distinct backend domains',async()=>{
 const register=await command();assert.equal(await nativeSourceCommandHashV136(scope,'register',register),await graphWireDigest({schema:'graph-event-native-audio-producer-command-hash-v1',workspace_id:scope.workspace_id,actor_id:scope.actor_id,command:register}));
 const revoke={schema:'graph-event-native-audio-producer-revocation-v1',request_id:id(50),instance_id:scope.instance_id,producer_id:id(7),producer_version:1,contract_hash:'e'.repeat(64),reason:'明确停止'};
 assert.equal(await nativeSourceCommandHashV136(scope,'revoke',revoke),await graphWireDigest({schema:'graph-event-native-audio-producer-revocation-command-hash-v1',workspace_id:scope.workspace_id,actor_id:scope.actor_id,command:revoke}));
 const retirement={schema:'graph-event-native-audio-source-retirement-v1',request_id:id(51),instance_id:scope.instance_id,kind:'register',original:register,reason:'结束未确认操作'};
 assert.equal(await nativeSourceCommandHashV136(scope,'retirement',retirement),await graphWireDigest({schema:'graph-event-native-audio-source-retirement-command-hash-v1',workspace_id:scope.workspace_id,actor_id:scope.actor_id,command:retirement}));
 assert.throws(()=>nativeSourceCommandV136({...retirement,request_id:register.request_id},'retirement',scope));
});
test('options may be empty with a cursor; no UUID or extractor substitution',async()=>{
 const value={schema:'graph-event-native-audio-producer-options-v1',instance_id:scope.instance_id,sources:[],installations:[await installation()],next_after:id(55),can_register:true,reason:'继续读取来源'};await checkNativeSourceOptionsV136(value,scope);assert.equal(value.sources.length,0);
 await assert.rejects(()=>checkNativeSourceOptionsV136({...value,extractors:[]},scope));
});
test('wrong instance, changed command and receipt target cannot clear original outbox',async()=>{
 const pending=await outbox(),row=await producer();await checkNativeSourceReceiptV136(row,pending,scope);
 for(const mutate of [value=>{value.id='not-an-id';},value=>{value.registration.command.request_id=id(67);},value=>{value.registration.actor_id=id(68);},value=>{value.contract.instance_id=id(69);},value=>{value.label='另一来源';}]){const invalid=clone(row);mutate(invalid);await assert.rejects(()=>checkNativeSourceReceiptV136(invalid,pending,scope));}
 const absent={schema:'graph-event-native-audio-source-request-state-v1',instance_id:scope.instance_id,kind:'register',request_id:pending.body.request_id,outcome:'not_found',command_hash:null,saved:null,retirement:null};await checkNativeSourceStateV136(absent,pending,scope);await assert.rejects(()=>checkNativeSourceStateV136({...absent,command_hash:pending.command_hash},pending,scope));
});
test('stages keep collection/reception/admission distinct and reject a claimed waiting match',()=>{
 const record={id:id(70),entity_id:id(71),record:{task_id:id(72)}},value={schema:'native-audio-graph-receipt-stages-v136',observation_id:record.id,source_id:source.id,entity_id:record.entity_id,task_id:record.record.task_id,collected:true,data_received:true,data_status:'not_declared',data_message:'已接收，但原计划未声明此归集包。',deliveries:[{intent_id:id(73),producer_id:id(7),source_label:'语音结果',status:'admitted',admission:{id:id(74),sequence:1,admitted_at:'2026-10-20T00:00:00+00:00'}}],waiting_match:'not_checked',waiting_message:'请到等待任务核对',candidate_only:true,source_time_verified:false,physical_state_verified:false};
 checkNativeAudioStagesV136(value,record);assert.throws(()=>checkNativeAudioStagesV136({...value,waiting_match:'matched'},record));assert.throws(()=>checkNativeAudioStagesV136({...value,physical_state_verified:true},record));assert.throws(()=>checkNativeAudioStagesV136({...value,entity_id:id(75)},record));
});
test('copied tab forks draft ownership, both local drafts survive',()=>{
 const storage=memory(),firstSession=sessionFor(),first=journal(storage,firstSession);first.open(emptyNativeSourceFormV136());first.save({...emptyNativeSourceFormV136(),label:'第一窗口的人稿'});
 const second=journal(storage,memory([...firstSession.values]));const restored=second.open(emptyNativeSourceFormV136());assert.equal(restored.form.label,'第一窗口的人稿');second.save({...restored.form,label:'复制窗口另存人稿'});first.save({...restored.form,label:'第一窗口后续劳动'});
 const found=enumerateNativeSourceRecoveryV136(storage,scope,{limit:30});assert.ok(found.items.some(row=>row.value.form?.label==='复制窗口另存人稿'));assert.ok(found.items.some(row=>row.value.form?.label==='第一窗口后续劳动'));assert.notEqual(first.owner,second.owner);
});
test('bounded local discovery never getItem/parses a foreign scope, and empty page has continuation',()=>{
 const storage=memory();for(let i=0;i<15;i++)storage.setItem('ga-native-graph-sources-v136:'+id(90)+':'+id(91)+':'+id(92)+':request:'+id(100+i),'{private invalid');
 const own=journal(storage);own.open(emptyNativeSourceFormV136());own.save({...emptyNativeSourceFormV136(),label:'本范围原稿'});storage.reads.length=0;
 const page=enumerateNativeSourceRecoveryV136(storage,scope,{limit:1,scan:1});assert.deepEqual(page.items,[]);assert.equal(page.next_offset,1);assert.deepEqual(storage.reads,[]);
 const next=enumerateNativeSourceRecoveryV136(storage,scope,{offset:15,limit:30});assert.ok(next.items.length);assert.ok(storage.reads.every(key=>key.startsWith(nativeSourcePrefixV136(scope))));
});
test('selected exact local raw refuses slot replacement',()=>{
 const storage=memory(),own=journal(storage);own.open(emptyNativeSourceFormV136());own.save({...emptyNativeSourceFormV136(),label:'原劳动'});const selected=enumerateNativeSourceRecoveryV136(storage,scope).items.find(row=>row.kind==='draft'&&row.value.form.label==='原劳动');assert.ok(selected,'select the saved editable slot, not the separate blank entry snapshot');assert.ok(selected.raw.includes('原劳动'));storage.setItem(selected.key,selected.raw.replace('原劳动','后来劳动'));assert.notEqual(storage.getItem(selected.key),selected.raw);assert.throws(()=>readNativeSourceRecoveryV136(storage,scope,selected));
});
test('shared pending cannot be overwritten by a second tab or a blank mount',async()=>{
 const storage=memory(),shared=locks(),one=journal(storage,sessionFor(),shared),two=journal(storage,sessionFor(),shared);one.open(emptyNativeSourceFormV136());two.open(emptyNativeSourceFormV136());const original=await outbox();await one.install(original,async()=>{});
 const reopened=journal(storage,sessionFor(),shared);assert.equal(reopened.open(emptyNativeSourceFormV136()).pending.value.body.request_id,original.body.request_id);
 const other=await outbox(await command(id(9)));await assert.rejects(()=>two.install(other,async()=>{}),/另一窗口/);
});
test('native controller previews and writes original outbox before real transport POST',async()=>{
 const s=await scene();await preview(s);let checked=false;s.backend.state.beforePost=async(path,body,raw)=>{if(path===s.backend.base){const head=s.controller.journal.head('active');assert.equal(head.value.exact_body,raw);assert.equal(head.value.body.request_id,body.request_id);checked=true;}};
 await s.controller.register();assert.equal(checked,true);assert.equal(registrationPosts(s.backend).length,1);assert.equal(s.controller.pending,null);assert.match(s.controller.notice,/原操作保存/);assert.ok([...s.storage.values.keys()].some(key=>key.includes(':request:')));
});
test('lost committed reply remount only GETs original nonce, no new request or POST',async()=>{
 const s=await scene();await preview(s);s.backend.state.lost='after';await assert.rejects(()=>s.controller.register());const original=s.controller.pending.exact_body,nonce=s.controller.pending.body.request_id;s.controller.dispose();
 const next=await scene({...s,session:sessionFor()});const before=registrationPosts(s.backend).length;assert.equal(next.controller.pending.exact_body,original);await next.controller.recover();assert.equal(next.controller.pending,null);assert.equal(registrationPosts(s.backend).length,before);assert.equal(s.backend.saved.get(nonce).registration.command.request_id,nonce);
});
test('not-found remains unknown; explicit resend retains exact bytes and nonce',async()=>{
 const s=await scene();await preview(s);s.backend.state.lost='before';await assert.rejects(()=>s.controller.register());const original=s.controller.pending.exact_body;await s.controller.recover();assert.equal(s.controller.pendingNotFound,true);assert.equal(registrationPosts(s.backend).length,1);await s.controller.resend();assert.equal(registrationPosts(s.backend).length,2);assert.ok(registrationPosts(s.backend).every(row=>row.options.body===original));assert.equal(s.controller.pending,null);
});
test('wrong actual HTTP receipt stays pending even after source was actually saved',async()=>{
 const s=await scene();await preview(s);s.backend.state.wrongReply=true;await assert.rejects(()=>s.controller.register());assert.ok(s.controller.pending);s.backend.state.wrongReply=false;await s.controller.recover();assert.equal(s.controller.pending,null);assert.equal(registrationPosts(s.backend).length,1);
});
test('fresh source/version change does not replace the original selected pin',async()=>{
 const s=await scene();await preview(s);const prior=clone(s.controller.form.source);s.backend.state.optionsSources=[{...source,content_hash:'f'.repeat(64)}];await s.controller.refresh();assert.deepEqual(s.controller.form.source,prior);await assert.rejects(()=>s.controller.previewRegistration(),/原选来源/);assert.equal(registrationPosts(s.backend).length,0);
});
test('missing WebLocks blocks preview and send, but preserves pending for read-only recovery',async()=>{
 const s=await scene({sharedLocks:null});s.controller.edit({label:'保留的人稿'});s.controller.choose('source',source.id);s.controller.choose('installation',trace.installation_id);await assert.rejects(()=>s.controller.previewRegistration());assert.equal(s.backend.state.calls.some(call=>call.options.method==='POST'),false);
});
test('session write failure preserves shared pending for GET and forbids all POST',async()=>{
 const backend=await server(),storage=memory(),own=journal(storage),value=await outbox();own.open(emptyNativeSourceFormV136());await own.install(value,async()=>{});backend.saved.set(value.body.request_id,await producer(value.body));const session=sessionFor();session.failWrite=true;
 const s=await scene({backend,storage,session});assert.equal(s.controller.journal.canSend(),false);assert.equal(s.controller.pending.body.request_id,value.body.request_id);await s.controller.recover();assert.ok(s.controller.pending,'read-only mode cannot erase original pointer');assert.equal(s.backend.state.calls.some(call=>call.options.method==='POST'),false);
});
test('quota failure retains in-memory input, marks protected work and blocks POST',async()=>{
 const s=await scene();s.storage.failWrite=true;s.controller.edit({label:'必须保留的新劳动'});assert.equal(s.controller.form.label,'必须保留的新劳动');assert.equal(s.controller.localSaved,false);assert.equal(s.controller.protectedWork,true);await assert.rejects(()=>s.controller.previewRegistration());assert.equal(s.backend.state.calls.some(call=>call.options.method==='POST'),false);s.storage.failWrite=false;assert.equal(s.controller.saveLocal(),true);
});
test('partial local save followed by session failure can be saved again without losing the prior raw draft',()=>{
 const storage=memory(),session=sessionFor(),own=journal(storage,session);own.open(emptyNativeSourceFormV136());own.save({...emptyNativeSourceFormV136(),label:'先前已存劳动'});session.failWrite=true;assert.throws(()=>own.save({...emptyNativeSourceFormV136(),label:'指针失败仍在本机的劳动'}));session.failWrite=false;own.save({...emptyNativeSourceFormV136(),label:'恢复后的人稿'});
 assert.ok(enumerateNativeSourceRecoveryV136(storage,scope,{limit:30}).items.some(row=>row.value.form?.label==='指针失败仍在本机的劳动'));assert.equal(JSON.parse(session.getItem(own.prefix+'form-head')).form.label,'恢复后的人稿');
});
test('restored session writes allow explicit save without clearing shared original request',async()=>{
 const backend=await server(),storage=memory(),own=journal(storage),value=await outbox();own.open(emptyNativeSourceFormV136());await own.install(value,async()=>{});const session=sessionFor();session.failWrite=true;const s=await scene({backend,storage,session});session.failWrite=false;s.controller.edit({label:'恢复存储后另存的人稿'});assert.equal(s.controller.localSaved,true);assert.equal(s.controller.pending.body.request_id,value.body.request_id);assert.equal(s.controller.journal.head('active').value.exact_body,value.exact_body);
});
test('safe ending uses separate nonce; lost ending reply cold recovery clears only verified originals',async()=>{
 const s=await scene();await preview(s);s.backend.state.lost='before';await assert.rejects(()=>s.controller.register());await s.controller.recover();const original=s.controller.pending;
 s.controller.setReason(original.body.request_id,'明确结束这次未确认登记');s.backend.state.lost='after';await assert.rejects(()=>s.controller.retireOriginal());const ending=s.controller.retirement;assert.notEqual(ending.body.request_id,original.body.request_id);assert.equal(graphWireCanonical(ending.body.original),original.exact_body);await s.controller.recoverRetirement();assert.equal(s.controller.pending,null);assert.equal(s.controller.retirement,null);assert.equal(s.backend.saved.size,0);assert.equal(s.backend.retired.size,1);
});
test('original wins retirement race: actual saved original is shown, not a fabricated retirement',async()=>{
 const s=await scene();await preview(s);s.backend.state.lost='before';await assert.rejects(()=>s.controller.register());await s.controller.recover();const body=clone(s.controller.pending.body);s.controller.setReason(body.request_id,'结束未确认操作');
 s.backend.state.beforePost=async(path)=>{if(path===s.backend.base+'/command-retirements')s.backend.saved.set(body.request_id,await producer(body));};await assert.rejects(()=>s.controller.retireOriginal(),/already saved/);assert.ok(s.controller.retirement);await s.controller.recoverRetirement();assert.equal(s.controller.pending,null);assert.equal(s.controller.retirement,null);assert.equal(s.backend.retired.size,0);assert.match(s.controller.notice,/原操作保存/);
});
test('two tabs cannot register separate requests while one unknown original owns shared head',async()=>{
 const backend=await server(),storage=memory(),sharedLocks=locks(),one=await scene({backend,storage,sharedLocks}),two=await scene({backend,storage,sharedLocks});await preview(one);await preview(two);backend.state.lost='before';await assert.rejects(()=>one.controller.register());await assert.rejects(()=>two.controller.register(),/另一窗口/);assert.equal(registrationPosts(backend).length,1);assert.equal(one.controller.journal.head('active').value.body.request_id,one.controller.pending.body.request_id);
});
test('role loss can cold GET original saved receipt with a fresh scoped controller',async()=>{
 const s=await scene();await preview(s);s.backend.state.lost='after';await assert.rejects(()=>s.controller.register());s.controller.dispose();const viewer={...account,role:'viewer'};s.backend.state.who=viewer;const next=await scene({...s,who:viewer,session:sessionFor(viewer)});assert.equal(next.controller.canRegister,false);await next.controller.recover();assert.equal(next.controller.pending,null);assert.equal(registrationPosts(s.backend).length,1);
});
test('cookie identity mismatch before storage open never reads private native draft',async()=>{
 const backend=await server(),storage=memory();storage.setItem(nativeSourcePrefixV136(scope)+'form:entry:'+id(80),'private');backend.state.who={...account,user_id:id(81)};
 const c=new NativeGraphSourcesControllerV136({api:backend.api,account,scope,storage,session:sessionFor(),locks:locks()});await assert.rejects(()=>c.open());assert.equal(c.identityStatus,'concealed');assert.deepEqual(storage.reads,[]);assert.equal(backend.state.calls.some(call=>call.options.method==='POST'),false);
});
test('old identity epoch cannot finish preview or re-expose a concealed scope',async()=>{
 const s=await scene(),gate=deferred();s.controller.edit({label:'仍保留原人稿'});s.controller.choose('source',source.id);s.controller.choose('installation',trace.installation_id);s.backend.state.beforePost=async(path)=>{if(path===s.backend.base+'/preview')await gate.promise;};
 const pending=s.controller.previewRegistration();while(!s.backend.state.calls.some(call=>call.path===s.backend.base+'/preview'))await Promise.resolve();s.controller.conceal();s.backend.state.who={...account,user_id:id(83)};await assert.rejects(()=>s.controller.recheckIdentity());gate.resolve();await assert.rejects(()=>pending);assert.equal(s.controller.identityStatus,'concealed');assert.equal(s.controller.preview,null);assert.equal(s.controller.form.label,'仍保留原人稿');
});
