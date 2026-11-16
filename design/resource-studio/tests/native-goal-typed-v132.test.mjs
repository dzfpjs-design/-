import test from 'node:test';
import assert from 'node:assert/strict';
import {buildNativeGoal,buildToolGoalCriteria,defaultNativeGoal} from '../src/native-goal-contract.mjs';
import {buildNativeQuery,queryTypedValue} from '../src/native-query-contract.mjs';
import {isTypedNumber,numericToken,numericValue,typedRequestJSON,freshNumber} from '../src/typed-numbers-v132.mjs';
import {GOAL_REQUEST_SCHEMA,NativeGoalJournal,nativeGoalScope,freezeNativeGoalRequest,checkNativeGoalRequest,nativeGoalRequestJSON,nativeGoalRecoveryJSON,nativeGoalExportText} from '../src/native-goal-request-v132.mjs';
import {queryTools,queryMetadata,queryRow} from './native-query-fixtures.mjs';

// Actual builders/journal/download validator with synthetic storage and locks.
// These tests do not stand in for a browser or a server/DB recovery journey.
let serial=0;
const uuid=()=> '32000000-0000-4000-8000-'+String(++serial).padStart(12,'0');
const account={user_id:uuid(),workspace_id:uuid()},taskID=uuid(),scope=nativeGoalScope(account,taskID);
const options={typedNumbers:true},copy=value=>JSON.parse(JSON.stringify(value));
const tool={id:'business-tool',name:'已登记计算',version:1,content_hash:'frozen',step_names:['核对资料'],sources:['workflow_step'],output_fields:[{key:'value',label:'计算值',kind:'number',unit:null},{key:'count',label:'记录数',kind:'integer',unit:'条'}]};
const metadata={...queryMetadata,fields:{...queryMetadata.fields,measurement:'登记数值'},field_kinds:{...queryMetadata.field_kinds,measurement:'number'},tools:[tool]};
const form=(patch={})=>({...defaultNativeGoal('核对原登记资料'),fields:['measurement','count','name'],...patch});
const command=(patch={})=>({request_id:uuid(),expected_version:3,reason:'保留本次原说明',...patch});
const original=(patch={})=>freezeNativeGoalRequest('define',command({goal_spec:buildNativeGoal(form({targetField:'measurement',targetOperator:'eq',targetValue:'-0.0'}),metadata,options),...patch}),scope);
function originalEqual(actual,expected){assert.deepEqual(actual,copy(expected));assert.equal(actual.exact_body,expected.exact_body);assert.equal(nativeGoalRequestJSON(actual,scope),expected.exact_body);assert.ok(nativeGoalRecoveryJSON(actual,scope,{expected_version:4,reason:'核对原文'}).includes('"original_request":'+expected.exact_body));}
class Storage{
 constructor(){this.map=new Map();this.reads=[];this.onGet=null;this.onSet=null;this.onRemove=null;}
 get length(){return this.map.size;}
 key(index){return [...this.map.keys()][index]??null;}
 getItem(key){this.reads.push(key);return this.onGet?this.onGet(key,this.map.get(key)??null):this.map.get(key)??null;}
 setItem(key,value){this.onSet?.(key,value);this.map.set(key,String(value));}
 removeItem(key){this.onRemove?.(key);this.map.delete(key);}
}
class Locks{
 constructor(){this.tails=new Map();}
 request(name,setting,callback){assert.equal(setting.mode,'exclusive');const before=this.tails.get(name)||Promise.resolve(),next=before.then(callback);this.tails.set(name,next.catch(()=>{}));return next;}
}
function deferred(){let resolve;const promise=new Promise(done=>resolve=done);return {promise,resolve};}
function fixture(settings={}){
 const storage=settings.storage||new Storage(),tabStorage=Object.hasOwn(settings,'tabStorage')?settings.tabStorage:new Storage(),locks=Object.hasOwn(settings,'locks')?settings.locks:new Locks(),actualScope=settings.scope||scope;
 const legacyKey='legacy-goal:'+Object.values(actualScope).join(':'),journal=new NativeGoalJournal({storage,tabStorage,locks,scope:actualScope,legacyKey,randomUUID:uuid});
 const value=journal.open(form(settings.form));return {storage,tabStorage,locks,journal,value};
}

for(const raw of ['-0.0','1.0','1e21'])test('actual Goal field builder keeps fresh number token '+raw,()=>{
 const draft=form({targetField:'measurement',targetOperator:'eq',targetValue:raw}),before=copy(draft),spec=buildNativeGoal(draft,metadata,options),expected=spec.criteria.find(row=>row.kind==='field_value').expected;
 assert.equal(isTypedNumber(expected),true);assert.equal(numericToken(expected),raw);assert.ok(typedRequestJSON(spec).includes('"expected":'+raw));assert.deepEqual(draft,before);assert.throws(()=>JSON.stringify(spec),/原数值/);
});
for(const raw of ['-0.0','1.0','1e21'])test('actual tool and final query criteria preserve fresh token '+raw,()=>{
 const tools=buildToolGoalCriteria(form({toolCriteria:[{id:'one',capabilityId:tool.id,kind:'tool_value',field:'value',operator:'eq',expected:raw}]}),metadata,2,options);
 const query=buildNativeQuery(form({querySteps:[queryRow()],queryCriterion:{enabled:true,field:'value',operator:'eq',expected:raw}}),metadata,options);
 assert.equal(numericToken(tools[0].expected),raw);assert.equal(numericToken(query.criterion.expected),raw);
 assert.ok(typedRequestJSON(tools).includes('"expected":'+raw));assert.ok(typedRequestJSON(query).includes('"expected":'+raw));
});
test('legacy builder defaults remain ordinary numbers while typed opt-in keeps float identity',()=>{
 const draft=form({targetField:'measurement',targetOperator:'eq',targetValue:'1.0'}),legacy=buildNativeGoal(draft,metadata),typed=buildNativeGoal(draft,metadata,options);
 assert.equal(legacy.criteria[2].expected,1);assert.equal(isTypedNumber(legacy.criteria[2].expected),false);assert.equal(numericToken(typed.criteria[2].expected),'1.0');
 assert.equal(queryTypedValue('1.0','number'),1);assert.equal(numericToken(queryTypedValue('1.0','number','标准',options)),'1.0');
});
for(const bad of ['9007199254740993','1.0000000000000001','9007199254740992.0','1e21','-0.0','1.5'])test('Goal integer rejects unsafe/rounded/fractional input '+bad,()=>{
 const draft=form({targetField:'count',targetOperator:'eq',targetValue:bad});assert.throws(()=>buildNativeGoal(draft,metadata,options));assert.equal(draft.targetValue,bad);
 assert.throws(()=>buildNativeGoal(draft,metadata));
 assert.throws(()=>buildToolGoalCriteria(form({toolCriteria:[{id:'one',capabilityId:tool.id,kind:'tool_value',field:'count',operator:'eq',expected:bad}]}),metadata,2,options));
 assert.throws(()=>buildToolGoalCriteria(form({toolCriteria:[{id:'one',capabilityId:tool.id,kind:'tool_value',field:'count',operator:'eq',expected:bad}]}),metadata));
 assert.throws(()=>buildNativeQuery(form({querySteps:[queryRow()],queryCriterion:{enabled:true,field:'sample_count',operator:'eq',expected:bad}}),metadata,options));
 assert.throws(()=>buildNativeQuery(form({querySteps:[queryRow()],queryCriterion:{enabled:true,field:'sample_count',operator:'eq',expected:bad}}),metadata));
});
test('number rejects an unsafe bare integer but accepts explicit finite float/exponent',()=>{
 for(const bad of ['9007199254740993',9007199254740992,1e21,Infinity,NaN,'Infinity','0x10','',true])assert.throws(()=>queryTypedValue(bad,'number','数值',options));
 assert.equal(numericToken(queryTypedValue('9007199254740993.0','number','数值',options)),'9007199254740993.0');
 for(const raw of ['9007199254740991','1.0','1e0'])assert.equal(numericValue(queryTypedValue(raw,'integer','整数',options)),Number(raw));
});
test('typed query parameters still enforce numeric enum/range and safe integer limits',()=>{
 const full=patch=>form({querySteps:[queryRow({capabilityId:'fulltext-fixed',parameters:{field:'name',text:'杉木',top_k:'20',min_score:'-0.0',match_mode:'all_terms',...patch}})]});
 const value=buildNativeQuery(full({}),metadata,options).dataQuery.tool_steps[0].parameters;
 assert.equal(numericToken(value.min_score),'-0.0');assert.equal(numericToken(value.top_k),'20');
 for(const patch of [{top_k:'21'},{top_k:'1.5'},{top_k:'9007199254740993'},{min_score:'51.0'},{min_score:'-0.1'}])assert.throws(()=>buildNativeQuery(full(patch),metadata,options));
 const enumTools=copy(queryTools);enumTools[2].parameters_schema.properties.min_score.enum=[0,1];const enumMetadata={...metadata,query_tools:enumTools};
 assert.equal(numericToken(buildNativeQuery(full({min_score:'1.0'}),enumMetadata,options).dataQuery.tool_steps[0].parameters.min_score),'1.0');assert.throws(()=>buildNativeQuery(full({min_score:'2.0'}),enumMetadata,options));
});
test('freshness hours are exact bounded integers in both builder modes, without changing the human text',()=>{
 for(const setting of [{},options]){
  for(const raw of ['-','24.0000000000000001','1.0000000000000001','1.5','0','721','9007199254740993']){const draft=form({source:'observations',hours:raw});assert.throws(()=>buildNativeGoal(draft,metadata,setting),/整数小时/);assert.equal(draft.hours,raw);}
  for(const raw of ['1.','24.0','7.2e2']){const draft=form({source:'observations',hours:raw});assert.equal(buildNativeGoal(draft,metadata,setting).evidence.time.max_age_seconds,Number(raw)*3600);assert.equal(draft.hours,raw);}
 }
 assert.equal(buildNativeGoal(form({source:'archive',hours:'未完成旧输入'}),metadata,options).evidence.time.mode,'archive');
});
test('working journal preserves incomplete freshness text through refresh without creating a request',()=>{
 for(const raw of ['-','1.','24.0000000000000001']){const a=fixture();a.journal.save(form({source:'observations',hours:raw}));const b=fixture({storage:a.storage,tabStorage:a.tabStorage,locks:a.locks});assert.equal(b.value.form.hours,raw);assert.equal(b.journal.pending(),null);assert.equal(a.storage.getItem(a.journal.outboxKey),null);}
});
test('new pending stores exact typed body and accepts only a non-authoritative numeric mirror',()=>{
 const value=original(),saved=copy(value);assert.equal(value.schema,GOAL_REQUEST_SCHEMA);assert.ok(value.exact_body.includes('"expected":-0.0'));assert.equal(Object.is(saved.body.goal_spec.criteria[2].expected,-0),false);
 assert.equal(checkNativeGoalRequest(saved,scope),saved);assert.equal(nativeGoalRequestJSON(saved,scope),value.exact_body);
 const recovery=nativeGoalRecoveryJSON(saved,scope,{expected_version:5,reason:'核对原请求'});assert.ok(recovery.includes('"original_request":'+value.exact_body));assert.equal(JSON.parse(recovery).expected_version,5);
});
test('exact exponent/float original survives storage mirror normalization and recovery embedding',()=>{
 const value=freezeNativeGoalRequest('define',command({goal_spec:{values:[freshNumber('1.0'),freshNumber('1e21'),freshNumber('-0.0')]}}),scope),stored=copy(value);
 assert.ok(nativeGoalRequestJSON(stored,scope).includes('[1.0,1e21,-0.0]'));assert.ok(nativeGoalRecoveryJSON(stored,scope,{expected_version:4,reason:'核对'}).includes('"original_request":'+value.exact_body));
 assert.equal(stored.body.goal_spec.values[0],1);assert.equal(stored.body.goal_spec.values[1],1e21);
});
test('malformed/foreign pending cannot be re-signed or sent',()=>{
 const value=original();for(const change of [item=>({...item,extra:true}),item=>({...item,scope:{...item.scope,actor_id:uuid()}}),item=>({...item,action:'other'}),item=>({...item,body:{...item.body,reason:'different'}}),item=>({...item,exact_body:item.exact_body.replace('-0.0','1.0')}),item=>({...item,exact_body:item.exact_body.slice(0,-1)+',"reason":"duplicate"}'})])assert.throws(()=>nativeGoalRequestJSON(change(value),scope));
});
test('legacy pending keeps its original body/nonce and old serialization path',()=>{
 const value={action:'accept',body:command({basis_hash:'a'.repeat(64)})},raw=JSON.stringify(value);assert.equal(nativeGoalRequestJSON(value,scope),JSON.stringify(value.body));
 assert.deepEqual(JSON.parse(nativeGoalRecoveryJSON(value,scope,{expected_version:4,reason:'核对'})).original_request,value.body);assert.equal(JSON.stringify(value),raw);assert.equal(Object.hasOwn(value,'schema'),false);
});
test('empty mount durably forks a draft without inventing an original request',()=>{
 const {journal,storage}=fixture();assert.equal(journal.canSend,true);assert.equal(journal.pending(),null);assert.equal(storage.getItem(journal.outboxKey),null);assert.deepEqual(journal.work(journal.key).value.form,form());
});
test('another page editing draft cannot replace the first page unknown exact request',async()=>{
 const a=fixture(),b=fixture({storage:a.storage,locks:a.locks}),value=original();await assert.rejects(a.journal.request(value,{fresh:true},()=>{throw Error('lost response');}));const raw=a.storage.getItem(a.journal.outboxKey);
 b.journal.save(form({purpose:'另一页的人工输入'}));assert.equal(a.storage.getItem(a.journal.outboxKey),raw);assert.equal(b.journal.form.purpose,'另一页的人工输入');assert.equal(a.journal.pending().exact_body,value.exact_body);
});
test('full original body is written/read back before a callback may send',async()=>{
 const {journal,storage}=fixture(),value=original();let calls=0;await journal.request(value,{fresh:true},(stored,settle)=>{calls++;assert.equal(storage.getItem(journal.outboxKey),JSON.stringify(stored));assert.equal(stored.exact_body,value.exact_body);assert.equal(settle(),null);});assert.equal(calls,1);assert.equal(journal.pending(),null);
});
test('lost reply remount keeps exact nonce/text without automatically sending',async()=>{
 const a=fixture(),value=original();await assert.rejects(a.journal.request(value,{fresh:true},()=>{throw Error('lost');}));const raw=a.storage.getItem(a.journal.outboxKey),b=fixture({storage:a.storage,tabStorage:a.tabStorage,locks:a.locks});
 originalEqual(b.value.pending,value);assert.equal(a.storage.getItem(a.journal.outboxKey),raw);let calls=0;await b.journal.request(b.value.pending,{},(stored,settle)=>{calls++;assert.equal(nativeGoalRequestJSON(stored,scope),value.exact_body);settle();});assert.equal(calls,1);
});
test('fresh new nonce cannot bypass another tab unknown original',async()=>{
 const a=fixture(),b=fixture({storage:a.storage,locks:a.locks}),value=original();await assert.rejects(a.journal.request(value,{fresh:true},()=>{throw Error('unknown');}));let calls=0;await assert.rejects(b.journal.request(original(),{fresh:true},()=>calls++),/待核对/);assert.equal(calls,0);originalEqual(a.journal.pending(),value);
});
test('WebLock covers actual callback wait and settlement across tabs',async()=>{
 const a=fixture(),b=fixture({storage:a.storage,locks:a.locks}),entered=deferred(),release=deferred();let second=0;
 const first=a.journal.request(original(),{fresh:true},async(_stored,settle)=>{entered.resolve();await release.promise;settle();});await entered.promise;
 const next=b.journal.request(original(),{fresh:true},(_stored,settle)=>{second++;settle();});await Promise.resolve();assert.equal(second,0);release.resolve();await first;await next;assert.equal(second,1);
});
test('changed same-nonce body refuses replay; a replaced shared slot refuses settlement',async()=>{
 const {journal,storage}=fixture(),value=original();await assert.rejects(journal.request(value,{fresh:true},()=>{throw Error('unknown');}));let calls=0;const changed=original({request_id:value.body.request_id,reason:'changed'});await assert.rejects(journal.request(changed,{},()=>calls++));assert.equal(calls,0);
 const replacement=original();await assert.rejects(journal.request(value,{},(_stored,settle)=>{storage.setItem(journal.outboxKey,JSON.stringify(replacement));settle();}),/槽已变化/);originalEqual(journal.pending(),replacement);
});
test('working CAS conflict preserves memory input and never reaches POST callback',async()=>{
 const {journal,storage}=fixture(),record=journal.work(journal.key).value,raw=storage.getItem(journal.key);record.form.purpose='别页新稿';storage.setItem(journal.key,JSON.stringify(record));let calls=0;
 await assert.rejects(journal.request(original(),{fresh:true},()=>calls++),/另一页面/);assert.equal(calls,0);assert.notEqual(storage.getItem(journal.key),raw);assert.equal(journal.form.purpose,form().purpose);assert.equal(journal.pending(),null);
});
test('outbox readback failure blocks send and keeps the stored original',async()=>{
 const {journal,storage}=fixture(),value=original();let wrote=false,calls=0;storage.onSet=key=>{if(key===journal.outboxKey)wrote=true;};storage.onGet=(key,stored)=>key===journal.outboxKey&&wrote?null:stored;
 await assert.rejects(journal.request(value,{fresh:true},()=>calls++),/读回/);assert.equal(calls,0);storage.onGet=null;originalEqual(journal.pending(),value);
});
test('cleanup failure keeps the original until exact explicit recovery',async()=>{
 const {journal,storage}=fixture(),value=original();storage.onRemove=()=>{throw Error('denied');};await assert.rejects(journal.request(value,{fresh:true},(_stored,settle)=>settle()));originalEqual(journal.pending(),value);
 storage.onRemove=null;await journal.request(value,{},(_stored,settle)=>settle());assert.equal(journal.pending(),null);
});
for(const fault of ['missing','read','write','readback'])test('session '+fault+' retains discoverable work/outbox and blocks send',async()=>{
 const a=fixture(),value=original();a.journal.save(form({purpose:'不能丢的原稿'}));await assert.rejects(a.journal.request(value,{fresh:true},()=>{throw Error('unknown');}));
 const tab=fault==='missing'?null:new Storage();if(fault==='read')tab.onGet=()=>{throw Error('denied');};if(fault==='write')tab.onSet=()=>{throw Error('denied');};if(fault==='readback')tab.onGet=()=>null;
 const b=fixture({storage:a.storage,locks:a.locks,tabStorage:tab});assert.equal(b.journal.canSend,false);originalEqual(b.value.pending,value);assert.ok(b.journal.workingPage().items.some(item=>item.label==='不能丢的原稿'));let calls=0;await assert.rejects(b.journal.request(value,{},()=>calls++));assert.equal(calls,0);
});
test('missing WebLocks still allows original/draft reads but blocks callbacks',async()=>{
 const a=fixture(),value=original();await assert.rejects(a.journal.request(value,{fresh:true},()=>{throw Error('unknown');}));const b=fixture({storage:a.storage,locks:null});originalEqual(b.value.pending,value);assert.equal(b.journal.canSend,false);let calls=0;await assert.rejects(async()=>b.journal.request(value,{},()=>calls++),/多窗口/);assert.equal(calls,0);
});
test('copied session tab forks independent working slot',()=>{
 const a=fixture();a.journal.save(form({purpose:'原页稿'}));const copied=new Storage();for(const [key,value] of a.tabStorage.map)copied.setItem(key,value);const b=fixture({storage:a.storage,locks:a.locks,tabStorage:copied});assert.notEqual(a.journal.key,b.journal.key);assert.equal(b.journal.form.purpose,'原页稿');b.journal.save(form({purpose:'复制页新稿'}));assert.equal(a.journal.work(a.journal.key).value.form.purpose,'原页稿');
});
test('legacy raw stays untouched until original response is validated and settled',async()=>{
 const a=fixture(),old={form:form({purpose:'旧人工稿'}),pending:{action:'check',body:command()}},raw=JSON.stringify(old,null,2);a.storage.setItem(a.journal.legacyKey,raw);const b=fixture({storage:a.storage,locks:a.locks});assert.equal(a.storage.getItem(a.journal.legacyKey),raw);assert.equal(b.journal.form.purpose,'旧人工稿');
 await assert.rejects(b.journal.request(old.pending,{},()=>{throw Error('unknown');}));assert.equal(a.storage.getItem(a.journal.legacyKey),raw);
 await b.journal.request(old.pending,{},(stored,settle)=>{assert.equal(nativeGoalRequestJSON(stored,scope),JSON.stringify(old.pending.body));settle();});
 assert.equal(JSON.parse(a.storage.getItem(a.journal.legacyKey)).pending,null);assert.equal(JSON.parse([...a.storage.map].find(([key])=>key.startsWith(a.journal.prefix+'legacy:'))[1]).raw,raw);
});
test('legacy and typed pending both remain recoverable without overwriting either',async()=>{
 const a=fixture(),typed=original();await assert.rejects(a.journal.request(typed,{fresh:true},()=>{throw Error('unknown');}));const old={form:form(),pending:{action:'check',body:command()}};a.storage.setItem(a.journal.legacyKey,JSON.stringify(old));
 assert.deepEqual(a.journal.pending(),old.pending);await a.journal.request(old.pending,{},(_stored,settle)=>originalEqual(settle(),typed));originalEqual(a.journal.pending(),typed);
});
test('legacy changed by another writer cannot be cleared after response',async()=>{
 const a=fixture(),old={form:form(),pending:{action:'check',body:command()}},replacement=JSON.stringify({...old,form:form({purpose:'新旧槽人稿'})});a.storage.setItem(a.journal.legacyKey,JSON.stringify(old));
 await assert.rejects(a.journal.request(old.pending,{},(_stored,settle)=>{a.storage.setItem(a.journal.legacyKey,replacement);settle();}),/旧请求槽/);assert.equal(a.storage.getItem(a.journal.legacyKey),replacement);
});
test('corrupt legacy raw is never replaced by an empty mount',()=>{
 const a=fixture();a.storage.setItem(a.journal.legacyKey,'{broken');assert.throws(()=>fixture({storage:a.storage,locks:a.locks}));assert.equal(a.storage.getItem(a.journal.legacyKey),'{broken');
});
test('explicit restore preserves current draft and does not send a request',async()=>{
 const a=fixture();a.journal.save(form({purpose:'需要恢复的原稿'}));const b=fixture({storage:a.storage,locks:a.locks}),selected=b.journal.workingPage().items.find(row=>row.label==='需要恢复的原稿');assert.ok(selected);
 const restored=await b.journal.restore(selected.key,form({purpose:'恢复前本页稿'}));assert.equal(restored.form.purpose,'需要恢复的原稿');assert.ok(b.journal.workingPage().items.some(row=>row.label==='恢复前本页稿'));assert.equal(restored.pending,null);
});
test('scope pagination is bounded and never reads another actor/task private value',()=>{
 const a=fixture();for(let i=0;i<4;i++)a.journal.fork(form({purpose:'保留稿'+i}));const foreign='ga-native-goal-v132:foreign:working:'+uuid();a.storage.setItem(foreign,'{private malformed');a.storage.reads=[];
 const one=a.journal.workingPage({limit:2}),two=a.journal.workingPage({limit:2,after:one.next_cursor});assert.equal(one.items.length,2);assert.equal(two.items.length,2);assert.equal(two.next_cursor,null);assert.equal(new Set([...one.items,...two.items].map(row=>row.key)).size,4);assert.equal(a.storage.reads.includes(foreign),false);assert.throws(()=>a.journal.workingPage({limit:51}));
});
test('other actor/workspace/task does not open old outbox or work slot',async()=>{
 const a=fixture(),value=original();await assert.rejects(a.journal.request(value,{fresh:true},()=>{throw Error('unknown');}));for(const changed of [{actor_id:uuid()},{workspace_id:uuid()},{task_id:uuid()}]){a.storage.reads=[];const b=fixture({storage:a.storage,locks:a.locks,scope:{...scope,...changed}});assert.equal(b.value.pending,null);assert.equal(b.journal.workingPage().items.length,0);assert.equal(a.storage.reads.includes(a.journal.outboxKey),false);assert.equal(a.storage.reads.includes(a.journal.key),false);}
});
test('explicit stale local original recovery retains the same nonce, never invents a new command',async()=>{
 const a=fixture(),b=fixture({storage:a.storage,locks:a.locks}),value=original();await assert.rejects(a.journal.request(value,{fresh:true},()=>{throw Error('unknown');}));await a.journal.request(value,{},(_stored,settle)=>settle());let calls=0;
 await b.journal.request(value,{},(stored,settle)=>{calls++;assert.equal(stored.body.request_id,value.body.request_id);assert.equal(stored.exact_body,value.exact_body);settle();});assert.equal(calls,1);assert.equal(b.journal.pending(),null);
});
test('download returns actual response text unchanged, including numeric token spellings',async()=>{
 const raw='{"schema":"native-business-goal-export-v1","goal":{"task_id":"'+taskID+'","values":[-0.0,1.0,1e21]},"physical_state_verified":false,"learning_auto_published":false,"production_ready":false}';
 const response={ok:true,headers:{get:()=> 'application/json; charset=utf-8'},text:async()=>raw};assert.equal(await nativeGoalExportText(response,taskID),raw);assert.notEqual(JSON.stringify(JSON.parse(raw)),raw);
});
for(const fault of ['http','type','json','task','claim'])test('download '+fault+' cannot produce a substitute export',async()=>{
 const data={schema:'native-business-goal-export-v1',goal:{task_id:taskID},physical_state_verified:false,learning_auto_published:false,production_ready:false};if(fault==='task')data.goal.task_id=uuid();if(fault==='claim')data.production_ready=true;
 const response={ok:fault!=='http',headers:{get:()=>fault==='type'?'text/html':'application/json'},text:async()=>fault==='json'?'{broken':JSON.stringify(data)};await assert.rejects(nativeGoalExportText(response,taskID));
});
