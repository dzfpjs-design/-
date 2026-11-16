// Actual independent frontend protocol/Journal. All values and storage are
// synthetic: these tests do not prove report provenance, PG or semantic quality.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {createHash,webcrypto} from 'node:crypto';
import {checkedObjectRevisionValueV166 as checked,checkedObjectRevisionDetailV166 as detail,objectRevisionScopeV166 as scope,objectRevisionCommandV166 as command,checkedObjectRevisionOriginalV166 as original,checkedObjectRevisionRecoveryV166 as recovery,objectRevisionWireSHA256V166 as wireHash,objectRevisionRecoveryPathV166 as recoveryPath,ObjectRevisionJournalV166 as Journal,objectRevisionRefsV166 as refs,objectRevisionRefLabelV166 as refLabel,objectRevisionIdentityV166 as identity} from '../src/report-object-revision-v166.mjs';
const id=n=>'16600000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value)),canonical=value=>JSON.stringify((function walk(item){return Array.isArray(item)?item.map(walk):item&&typeof item==='object'?Object.fromEntries(Object.keys(item).sort().map(key=>[key,walk(item[key])])):item;})(value)),hash=value=>createHash('sha256').update(canonical(value)).digest('hex');
const account={user_id:id(1),workspace_id:id(2),username:'合成对象核查人',role:'operator'},report=id(3),request=id(4),citation={span_ref:'w:p1/s1'};
function value(){return {schema:'report-object-semantic-review-v166',result:{schema:'report-object-semantic-candidate-wire-v166',source_hash:'a'.repeat(64),catalog_hash:'b'.repeat(64),domain:'合成业务领域',global_claims:[],objects:[{name:'档案条目',fields:[{label:'名称',kind:'文本',required:false,unit:null,citations:[citation],inference_reason:''},{label:'数量',kind:'整数',required:false,unit:'株',citations:[citation],inference_reason:''}],citations:[citation],inference_reason:'',business_identity:{status:'not_established',field_labels:[],citations:[],explanation:'当前人工稿尚未明确业务标识，继续保留待核'}}],relationships:[],businesses:[],questions:['业务标识仍待核，不能据技术记录身份认定业务编号']},note:'新结构稿私人说明',object_identity_confirmations:[{object_name:'档案条目',acknowledge_platform_identity:false}]};}
function basis(){return {report_id:report,report_revision:3,model_result_hash:'c'.repeat(64),source_hash:'a'.repeat(64),catalog_hash:'b'.repeat(64),context_hash:'d'.repeat(64),base_value_hash:'e'.repeat(64)};}
const full=()=>scope(account,report,hash(basis()));
function view(saved=false){const current=value();return {schema:'report-object-revision-view-v166',actor_id:account.user_id,workspace_id:account.workspace_id,report_id:report,version:saved?1:0,status:saved?'draft':'empty',basis:basis(),basis_hash:hash(basis()),value:current,draft_history:saved?[{version:1,operation:'save',value:copy(current),saved_at:'2026-10-22T00:00:00.123456+00:00'}]:[],can_save:true,can_confirm:true,source_available:true,source_issue:null};}
function storage(){const data=new Map();return {data,fail:false,get length(){return data.size;},key:index=>[...data.keys()][index]??null,getItem:key=>data.has(key)?data.get(key):null,setItem(key,raw){if(this.fail)throw Error('synthetic quota');data.set(key,String(raw));},removeItem:key=>data.delete(key)};}
function locks(){const tails=new Map();return {request(key,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(key)||Promise.resolve()).then(callback);tails.set(key,next.catch(()=>{}));return next;}};}
function journal(current=full(),options={}){return new Journal(current,{storage:storage(),session:storage(),locks:locks(),...options});}
const authorize=async()=>{};
async function installed(j=journal()){j.open(value());j.save(value());const body=command(j.scope,value(),0,'save',request),sent=await j.install('save',body,authorize);return {j,sent};}
const state=(sent,outcome='saved')=>{const body=JSON.parse(sent.value.exact_body);return {schema:'report-object-revision-request-v166',request_id:sent.value.request_id,wire_sha256:sent.value.wire_sha256,outcome,result:outcome==='unknown'?null:{report_id:report,version:body.expected_version+1,operation:sent.value.operation,status:sent.value.operation==='confirm'?'confirmed':'draft',basis_hash:body.basis_hash,value_hash:hash(body.value)}};};
const previous=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});after(()=>{if(previous)Object.defineProperty(globalThis,'crypto',previous);else delete globalThis.crypto;});

test('V166 all-optional object fields are valid without a fabricated name, UUID or required flag',()=>{
 const current=value(),before=canonical(current);assert.equal(checked(current),current);assert.equal(current.result.objects[0].fields.every(field=>field.required===false),true);assert.equal(canonical(current),before);assert.equal(current.object_identity_confirmations[0].acknowledge_platform_identity,false);
});
test('V166 draft and explicit confirmation have separate acknowledgement gates',()=>{
 assert.doesNotThrow(()=>command(full(),value(),0,'save',request));assert.throws(()=>command(full(),value(),0,'confirm',request));const current=value();current.object_identity_confirmations[0].acknowledge_platform_identity=true;assert.equal(command(full(),current,0,'confirm',request).value.object_identity_confirmations[0].acknowledge_platform_identity,true);
 for(const change of [v=>v.object_identity_confirmations[0].acknowledge_platform_identity=1,v=>v.object_identity_confirmations[0].object_name='别的对象',v=>v.object_identity_confirmations.push(copy(v.object_identity_confirmations[0]))]){const bad=value();change(bad);assert.throws(()=>checked(bad));}
});
test('V166 referenced identity requires existing distinct fields plus real-shaped span references and explanation',()=>{
 const current=value(),identity=current.result.objects[0].business_identity;Object.assign(identity,{status:'referenced',field_labels:['名称'],citations:[citation]});assert.equal(checked(current),current);assert.equal(current.result.objects[0].fields[0].required,false);
 for(const mutate of [v=>v.field_labels=[],v=>v.field_labels=['缺失字段'],v=>v.field_labels=['名称','名称'],v=>v.citations=[],v=>v.explanation='',v=>v.status='unique',v=>v.extra=true]){const bad=copy(current);mutate(bad.result.objects[0].business_identity);assert.throws(()=>checked(bad));}
});
test('V166 unresolved business identity means no chosen business fields, not a claim of absence',()=>{
 const current=value();current.result.objects[0].business_identity.field_labels=['名称'];assert.throws(()=>checked(current));current.result.objects[0].business_identity.field_labels=[];assert.doesNotThrow(()=>checked(current));assert.ok(current.result.objects[0].business_identity.explanation.includes('尚未明确'));
});
test('V166 field types, units, boolean required and closed shapes retain the original independent gates',()=>{
 for(const mutate of [v=>v.result.objects[0].fields[0].required=1,v=>v.result.objects[0].fields[0].kind='uuid',v=>v.result.objects[0].fields[0].unit='株',v=>v.result.objects[0].fields[0].extra=true,v=>v.result.objects[0].fields=[],v=>v.result.objects[0].fields.push(copy(v.result.objects[0].fields[0])),v=>v.result.objects.push(copy(v.result.objects[0]))]){const bad=value();mutate(bad);assert.throws(()=>checked(bad));}
});
test('V166 refuses old family/proofs/model keys instead of retagging or repairing them',()=>{
 for(const mutate of [v=>v.schema='report-evidence-manual-review-v5',v=>v.result.schema='report-evidence-candidate-wire-v5',v=>v.result.projection_hash='f'.repeat(64),v=>v.result.objects[0].key='fake_id',v=>v.result.objects[0].business_identity.source_time_verified=true]){const bad=value();mutate(bad);assert.throws(()=>checked(bad));}
});
test('V166 span references are unique closed original positions and never manufactured quote text',()=>{
 const current=value();assert.deepEqual(refs(current),['w:p1/s1']);assert.equal(refLabel('w:p1/s1'),'Word正文第1处 · 片段1');assert.equal(refLabel('o:p2/s3'),'PDF第2页 · 片段3');for(const item of [{span_ref:'elsewhere'}, {span_ref:'w:p1/s1',quote:'造引文'}]){const bad=value();bad.result.objects[0].fields[0].citations=[item];assert.throws(()=>checked(bad));}const bad=value();bad.result.objects[0].citations=[citation,citation];assert.throws(()=>checked(bad));
});
test('V166 local unfinished identity and overlong note retain human text while outbound commands reject it',()=>{
 const current=value();current.result.objects[0].business_identity={status:'referenced',field_labels:[],citations:[],explanation:''};current.note='字'.repeat(12001);const j=journal();j.open(value());j.save(current);assert.equal(j.value.note,current.note);assert.doesNotThrow(()=>checked(current,{complete:false}));assert.throws(()=>command(full(),current,0,'save',request));const next=journal(full(),{storage:j.storage,session:j.session,locks:j.locks});assert.equal(next.open(value()).note,current.note);j.dispose();next.dispose();
});
test('V166 generic JSON safety rejects getters, cycles, holes, nonfinite numbers and invalid Unicode',()=>{
 let called=false;const getter=value();Object.defineProperty(getter,'note',{enumerable:true,get(){called=true;return '';}});assert.throws(()=>checked(getter));assert.equal(called,false);
 for(const mutate of [v=>v.note='坏\ud800',v=>v.note='坏\u0000',v=>v.result.objects[0].fields[0].required=NaN,v=>v.result.questions=new Array(1),v=>v.result.extra=v]){const bad=value();mutate(bad);assert.throws(()=>checked(bad));}
});
test('V166 relationship shape retains existing-context names; actual endpoint membership remains a server check',()=>{
 const current=value();current.result.relationships=[{name:'条目关联',description:'合成关联说明',source_type:'档案条目',target_type:'原上下文对象',cardinality:'unspecified',citations:[citation],inference_reason:''}];assert.doesNotThrow(()=>checked(current));current.result.relationships[0].target_type='';assert.throws(()=>checked(current));
});
test('V166 preserved business local indices and human roles keep their structural gates without invented IDs',()=>{
 const current=value();current.result.businesses=[{operation:'upsert',original_name:'',name:'档案核对',trigger:'人工提出核对',output:'核对记录',steps:[{name:'人工核对原文',purpose:'对照原始记录',agent:'人工确认',role:'核查人',output:'核对意见',branch_condition:'',citations:[citation],inference_reason:'',next_index:null,else_index:null}],citations:[citation],inference_reason:'',participants:[],interactions:[],conditions:[],coordination:[]}];const before=canonical(current);assert.equal(checked(current),current);assert.equal(canonical(current),before);
 for(const mutate of [item=>item.steps[0].next_index=0,item=>item.steps[0].next_index=true,item=>item.steps[0].role='',item=>item.steps[0].key='invented',item=>item.steps[0].else_index=0]){const bad=copy(current);mutate(bad.result.businesses[0]);assert.throws(()=>checked(bad));}
});
test('V166 detail cold hash/history checks preserve drafts and explicitly hide writes on unavailable sources',async()=>{
 assert.equal((await detail(view(),account,report)).version,0);const saved=view(true);Object.assign(saved,{source_available:false,source_issue:'原报告版本已变化，原人稿保留',can_save:false,can_confirm:false});assert.equal((await detail(saved,account,report)).value.note,value().note);
 for(const mutate of [v=>v.basis_hash='f'.repeat(64),v=>v.actor_id=id(90),v=>v.draft_history[0].version=true,v=>v.value.note='当前行与末版本不一致',v=>v.can_save=true]){const bad=copy(saved);mutate(bad);await assert.rejects(()=>detail(bad,account,report));}
});
test('V166 nonce version and operation are closed and no schema is added to the exact four-key command',()=>{
 const body=command(full(),value(),0,'save','request_id_12345');assert.deepEqual(Object.keys(body),['request_id','expected_version','basis_hash','value']);for(const version of [true,0.0+0.5,-1,'0',-0])assert.throws(()=>command(full(),value(),version,'save',request));for(const requestID of ['short','含中文请求号123456','x'.repeat(101)])assert.throws(()=>command(full(),value(),0,'save',requestID));
});
test('V166 original is exact UTF-8 bytes with original actor/report/path/nonce, never a reserialized request',async()=>{
 const {j,sent}=await installed();assert.equal(sent.value.wire_sha256,createHash('sha256').update(sent.value.exact_body).digest('hex'));assert.equal(await original(sent.value,full()),sent.value);assert.equal((await j.pending()).raw,sent.raw);assert.ok(recoveryPath(sent.value).includes('?expected_sha256='+sent.value.wire_sha256));
 for(const mutate of [v=>v.exact_body+=' ',v=>v.path=v.path.replace('/save','/confirm'),v=>v.scope.actor_id=id(80),v=>v.wire_sha256='f'.repeat(64)]){const bad=copy(sent.value);mutate(bad);await assert.rejects(()=>original(bad,full()));}assert.notEqual(await wireHash('{"a":1}'),await wireHash('{ "a":1}'));j.dispose();
});
test('V166 saved request result is the original version/value hash, not a newer current row',async()=>{
 const {j,sent}=await installed();assert.equal((await recovery(state(sent),sent.value)).outcome,'saved');for(const mutate of [v=>v.result.version=2,v=>v.result.version=true,v=>v.result.value_hash='f'.repeat(64),v=>v.result.basis_hash='f'.repeat(64),v=>v.result.status='confirmed',v=>v.outcome='rejected']){const bad=state(sent);mutate(bad);await assert.rejects(()=>recovery(bad,sent.value));}j.dispose();
});
test('V166 actor and basis draft scopes isolate private work while old basis pending remains GET-recoverable',async()=>{
 const {j,sent}=await installed();const nextScope=scope(account,report,'f'.repeat(64)),next=journal(nextScope,{storage:j.storage,session:j.session,locks:j.locks});assert.equal(next.open(value()).note,value().note);assert.equal((await next.pending()).raw,sent.raw);await assert.rejects(()=>next.transmit(sent,authorize,()=>assert.fail('old basis cannot POST')));const other=journal(scope({...account,user_id:id(91),username:'别的核查人'},report,full().basis_hash),{storage:j.storage,session:j.session,locks:j.locks});other.open(value());assert.equal(await other.pending(),null);assert.throws(()=>identity({...account,role:'viewer'},account));j.dispose();next.dispose();other.dispose();
});
test('V166 storage quota protects human work, prevents POST setup and restores only inactive owner memory',async()=>{
 const j=journal();j.open(value());const changed=value();changed.note='未落盘的当前核查人说明';j.storage.fail=true;assert.throws(()=>j.save(changed),/quota/);await assert.rejects(()=>j.install('save',command(full(),changed,0,'save',request),authorize));assert.equal(await j.pending(),null);const concurrent=journal(full(),{storage:j.storage,session:j.session,locks:j.locks});assert.notEqual(concurrent.open(value()).note,changed.note,'a live second owner cannot consume or clear the first private unsaved draft');j.dispose();const restored=journal(full(),{storage:j.storage,session:j.session,locks:j.locks});assert.equal(restored.open(value()).note,changed.note);j.storage.fail=false;restored.save(changed);assert.equal(restored.unsaved,false);concurrent.dispose();restored.dispose();
});
test('V166 missing WebLocks cannot install any outbound command',async()=>{
 const j=journal(full(),{locks:null});j.open(value());j.save(value());await assert.rejects(()=>j.install('save',command(full(),value(),0,'save',request),authorize));assert.equal(await j.pending(),null);j.dispose();
});
test('V166 pending immutable command cannot be silently replaced by a later human draft',async()=>{
 const {j,sent}=await installed();const changed=value();changed.note='后来稿';j.save(changed);await assert.rejects(()=>j.install('save',command(full(),changed,0,'save',id(50)),authorize));assert.equal((await j.pending()).raw,sent.raw);assert.equal(j.value.note,'后来稿');j.dispose();
});
test('V166 hash and lock waits recheck latest authority before request persistence and POST',async()=>{
 const j=journal();j.open(value());j.save(value());let calls=0;await assert.rejects(()=>j.install('save',command(full(),value(),0,'save',request),async()=>{if(++calls>1)throw Error('synthetic late scope');}));assert.equal(await j.pending(),null);const {sent}=await installed(j);calls=0;let posted=false;await assert.rejects(()=>j.transmit(sent,async()=>{if(++calls>1)throw Error('synthetic late role');},()=>{posted=true;}));assert.equal(posted,false);assert.equal((await j.pending()).raw,sent.raw);j.dispose();
});
test('V166 saved receipt archives exact original and quota cannot prematurely clear it',async()=>{
 const {j,sent}=await installed();j.storage.fail=true;await assert.rejects(()=>j.finish(sent,state(sent),authorize));assert.equal((await j.pending()).raw,sent.raw);j.storage.fail=false;await j.finish(sent,state(sent),authorize);assert.equal(await j.pending(),null);assert.equal(j.storage.getItem(j.prefix+'saved:'+request),sent.raw);assert.equal(j.storage.getItem(sent.key),sent.raw);j.dispose();
});
test('V166 no own-version progress or changed basis cannot settle unknown CAS',async()=>{
 const {j,sent}=await installed();let reads=0;for(const next of [view(),{...view(true),basis_hash:'f'.repeat(64)}])await assert.rejects(()=>j.resolveConflict(sent,authorize,async()=>next,async()=>{reads++;return state(sent,'unknown');}));assert.equal(reads,0);assert.equal((await j.pending()).raw,sent.raw);j.dispose();
});
test('V166 explicit same-basis version progress uses a second original GET and preserves later human input',async()=>{
 const {j,sent}=await installed(),changed=value();changed.note='冲突后保留的后来稿';j.save(changed);const calls=[];await j.resolveConflict(sent,authorize,async()=>{calls.push('detail');return view(true);},async()=>{calls.push('original');return state(sent,'unknown');});assert.deepEqual(calls,['detail','original']);assert.equal(await j.pending(),null);assert.equal(j.storage.getItem(j.prefix+'cas-conflict:'+request),sent.raw);assert.equal(j.value.note,changed.note);j.dispose();
});
test('V166 a second saved lookup is recovered as saved rather than falsely classified as CAS rejection',async()=>{
 const {j,sent}=await installed();await j.resolveConflict(sent,authorize,async()=>view(true),async()=>state(sent));assert.equal(j.storage.getItem(j.prefix+'saved:'+request),sent.raw);assert.equal(j.storage.getItem(j.prefix+'cas-conflict:'+request),null);j.dispose();
});
test('V166 final pending digest scope loss cannot clear the immutable active pointer',async()=>{
 const {j,sent}=await installed(),before=Object.getOwnPropertyDescriptor(globalThis,'crypto');let count=0,valid=true,entered,release;const waiting=new Promise(resolve=>{entered=resolve;}),gate=new Promise(resolve=>{release=resolve;});
 Object.defineProperty(globalThis,'crypto',{configurable:true,value:{randomUUID:()=>webcrypto.randomUUID(),subtle:{digest(algorithm,bytes){if(++count===2){entered();return gate.then(()=>webcrypto.subtle.digest(algorithm,bytes));}return webcrypto.subtle.digest(algorithm,bytes);}}}});
 try{const resolving=j.resolveConflict(sent,async()=>{if(!valid)throw Error('synthetic final scope loss');},async()=>view(true),async()=>state(sent,'unknown'));await waiting;valid=false;release();await assert.rejects(resolving,/final scope loss/);assert.equal(j.storage.getItem(j.prefix+'active'),sent.headRaw);assert.equal(j.storage.getItem(sent.key),sent.raw);assert.equal(j.storage.getItem(j.prefix+'cas-conflict:'+request),null);}finally{release();Object.defineProperty(globalThis,'crypto',before);j.dispose();}
});

for(const generations of [3,4])test('V166 quota rescue of '+generations+' authoring generations cannot resurrect an older failed draft after final readback',()=>{
 const store=storage(),session=storage(),coordination=locks(),options={storage:store,session,locks:coordination},writers=[];let newest=value();
 try{
  for(let generation=1;generation<generations;generation++){
   const current=journal(full(),options);writers.push(current);current.open(value());if(generation>1)assert.equal(current.value.note,newest.note,'the latest failed human work is actually rescued');newest=copy(value());newest.note='R'+generation+'：本代尚未落盘的人工作业';store.fail=true;assert.throws(()=>current.save(newest),/quota/);current.dispose();
  }
  const final=journal(full(),options);writers.push(final);assert.equal(final.open(value()).note,newest.note);store.fail=false;final.save(final.value);
  const pointer=JSON.parse(session.getItem(final.draftPrefix+'head'));assert.equal(JSON.parse(store.getItem(pointer.key)).value.note,newest.note,'both stores really read back the final human value');final.dispose();
  const reopened=journal(full(),options);writers.push(reopened);assert.equal(reopened.open(value()).note,newest.note,'no old rescued ancestor may override the final saved value');assert.equal(reopened.unsaved,false,'successfully recovered ancestors are no longer outstanding work');
 }finally{
  store.fail=false;writers.forEach(current=>current.dispose());
  // Drain only this synthetic fixture after an old-source assertion failure;
  // this cleanup is not an automatic product recovery path.
  for(let n=0;n<generations+2;n++){const cleanup=journal(full(),options);try{cleanup.open(value());if(!cleanup.unsaved)break;cleanup.save(newest);}finally{cleanup.dispose();}}
 }
});
test('V166 saving a chosen inactive rescue must preserve a different inactive owner that was never its ancestor',()=>{
 const store=storage(),session=storage(),coordination=locks(),options={storage:store,session,locks:coordination},a=journal(full(),options),b=journal(full(),options),all=[a,b];const first=value(),second=value();first.note='独立A稿，B从未救援这一稿';second.note='独立B稿，本次明确保存它';
 try{
  a.open(value());b.open(value());store.fail=true;assert.throws(()=>a.save(first),/quota/);assert.throws(()=>b.save(second),/quota/);a.dispose();b.dispose();
  const chosen=journal(full(),options);all.push(chosen);assert.equal(chosen.open(value()).note,second.note);store.fail=false;chosen.save(chosen.value);chosen.dispose();
  const remaining=journal(full(),options);all.push(remaining);assert.equal(remaining.open(value()).note,first.note,'an unrelated inactive branch remains recoverable');assert.equal(remaining.unsaved,true,'this is real unsaved independent work, not a cleared ancestor');remaining.save(remaining.value);
 }finally{store.fail=false;all.forEach(current=>current.dispose());for(let n=0;n<4;n++){const cleanup=journal(full(),options);try{cleanup.open(value());if(!cleanup.unsaved)break;cleanup.save(second);}finally{cleanup.dispose();}}}
});
