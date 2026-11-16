// Root-only Node contracts over the actual shipped sources. No browser acceptance,
// project import, real API, database, file upload, supplier or model is exercised.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import vm from 'node:vm';

const operatorSource=readFileSync(new URL('../src/generic_agents/web/operator.js',import.meta.url),'utf8');
const indexHTML=readFileSync(new URL('../src/generic_agents/web/index.html',import.meta.url),'utf8');
const inline=indexHTML.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(inline,'actual operator HTML must contain its inline application');
const requireFrontend=createRequire(new URL('../design/resource-studio/package.json',import.meta.url));
const {JSDOM}=requireFrontend('jsdom');
const scope={user_id:'actor-A',workspace_id:'workspace-A'};
const actor={...scope,username:'synthetic-operator',role:'operator',workspaces:[{id:scope.workspace_id,name:'合成工作区'}]};
const plain=value=>JSON.parse(JSON.stringify(value));
const flush=async()=>{for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));};

function memory() {
  const values=new Map(),reads=[];
  return {values,reads,failWrite:false,badReadback:false,lastWrite:null,
    getItem(key){reads.push(key);if(this.badReadback&&this.lastWrite===key)return 'READBACK_CONFLICT';return values.get(key)??null;},
    setItem(key,value){if(this.failWrite)throw Error('Synthetic storage quota failure');values.set(key,String(value));this.lastWrite=key;},
    removeItem(key){values.delete(key);}};
}
function helpers() {const context=vm.createContext({});vm.runInContext(operatorSource,context);return context;}
function body(overrides={}) {
  return {source_id:'source',source_record_id:'original-record',processing_version:'1.0.0',entity_id:'entity',
    observed_at:'2026-10-20T16:05:00.000Z',business_day:'2026-10-21',attributes:{temperature:0.5},
    evidence_ids:[],extractor_id:'rules',startup_mode:'manual',...overrides};
}
function draft(h,overrides={}) {
  return {...plain(h.observationJournalBlank()),source_id:'source',source_record_id:'original-record',entity_id:'entity',
    extractor_id:'rules',observed_local:'2026-10-21T00:05',fields:[{key:'temperature',type:'number',value:'0.5'}],...overrides};
}
function record(value,who=actor,identity='saved-observation') {
  const original={...plain(value),acquisition_id:null,sensor_asset_id:null,sensor_modality:null};
  if(!original.evidence_ids.length)delete original.evidence_ids;
  return {id:identity,workspace_id:who.workspace_id,source_id:value.source_id,source_record_id:value.source_record_id,
    processing_version:value.processing_version,entity_id:value.entity_id,observed_at:value.observed_at,business_day:value.business_day,
    data:{submitted_by:who.user_id,extractor_id:value.extractor_id,input_hash:'a'.repeat(64),original_request:original}};
}
function stage(h,storage,value=body()) {
  return h.observationJournalStage(storage,h.observationJournalRead(storage,scope),value,draft(h));
}

test('journal reads only the exact actor/workspace slot, never another identity',()=>{
  const h=helpers(),storage=memory();storage.values.set('ga-operator-observation-v128:private:other','BROKEN_PRIVATE_CONTENT');
  const original=h.observationJournalRead(storage,scope);
  assert.deepEqual(storage.reads,[original.key]);assert.equal(original.value.request,null);
  const foreign=h.observationJournalRead(storage,{user_id:'actor-B',workspace_id:'workspace-A'});
  assert.notEqual(foreign.key,original.key);
});
test('human draft is written/read back and restored without changing the original characters',()=>{
  const h=helpers(),storage=memory(),original=h.observationJournalRead(storage,scope);
  const value=draft(h,{source_record_id:'人工原编号🙂',fields:[{key:'note',type:'string',value:' 原文\n保留🙂 '}],evidence_id:'saved-file'});
  const saved=h.observationJournalWrite(storage,original,{...original.value,draft:value});
  assert.deepEqual(plain(h.observationJournalRead(storage,scope).value.draft),value);
  assert.equal(saved.raw,storage.values.get(saved.key));
});
test('stage freezes the complete wire including processing version, source timezone day and decimal',()=>{
  const h=helpers(),storage=memory(),value=body(),saved=stage(h,storage,value);
  assert.equal(saved.value.request.wire,JSON.stringify(value));assert.equal(saved.value.request.status,'unknown');
  assert.equal(h.observationJournalRead(storage,scope).value.request.wire,saved.value.request.wire);
  assert.equal(plain(saved.value).request.saved_id,null);
});
for(const evidence_ids of [[],['saved-file']])test('matching full server original is acknowledged with evidence inventory '+evidence_ids.length,()=>{
  const h=helpers(),storage=memory(),value=body({evidence_ids}),original=stage(h,storage,value);
  const saved=h.observationJournalOutcome(storage,original,original.value.request.wire,{row:record(value)});
  assert.equal(saved.value.request.status,'saved');assert.equal(saved.value.request.saved_id,'saved-observation');
  assert.equal(saved.value.request.wire,original.value.request.wire);
});
const mismatches={
  actor:row=>{row.data.submitted_by='actor-B';},workspace:row=>{row.workspace_id='workspace-B';},
  source:row=>{row.source_id='other-source';},record:row=>{row.source_record_id='other-record';},
  version:row=>{row.processing_version='2';},entity:row=>{row.entity_id='other-entity';},
  time:row=>{row.observed_at='2026-10-20T16:06:00Z';},day:row=>{row.business_day='2026-10-20';},
  extractor:row=>{row.data.extractor_id='other-rules';},hash:row=>{row.data.input_hash='not-a-hash';},
  original:row=>{row.data.original_request.attributes.temperature=9;},
  evidence:row=>{row.data.original_request.evidence_ids=['unrequested-file'];},
};
for(const [name,change] of Object.entries(mismatches))test('mismatched '+name+' cannot clear the original pending request',()=>{
  const h=helpers(),storage=memory(),original=stage(h,storage),row=record(body());change(row);
  assert.throws(()=>h.observationJournalOutcome(storage,original,original.value.request.wire,{row}));
  assert.equal(storage.values.get(original.key),original.raw);assert.equal(original.value.request.saved_id,null);
});
test('4xx and unknown errors preserve wire and never claim the request was saved',()=>{
  const h=helpers(),storage=memory(),original=stage(h,storage),wire=original.value.request.wire;
  const rejected=h.observationJournalOutcome(storage,original,wire,{error:{status:409,message:'当前来源已撤回'}});
  assert.equal(rejected.value.request.status,'rejected');assert.equal(rejected.value.request.saved_id,null);
  assert.equal(rejected.value.request.wire,wire);
  const unknown=h.observationJournalOutcome(storage,rejected,wire,{error:{message:'Synthetic lost response'}});
  assert.equal(unknown.value.request.status,'unknown');assert.equal(unknown.value.request.wire,wire);
});
test('quota failure and stale-window CAS never overwrite the retained request',()=>{
  const h=helpers(),storage=memory(),original=h.observationJournalRead(storage,scope);
  storage.failWrite=true;assert.throws(()=>h.observationJournalStage(storage,original,body(),draft(h)));
  assert.equal(storage.values.size,0);storage.failWrite=false;
  const saved=stage(h,storage);assert.throws(()=>h.observationJournalWrite(storage,original,original.value));
  assert.equal(storage.values.get(saved.key),saved.raw);
});
test('readback failure is an error, not permission to send or clear an original',()=>{
  const h=helpers(),storage=memory(),original=h.observationJournalRead(storage,scope);storage.badReadback=true;
  assert.throws(()=>h.observationJournalStage(storage,original,body(),draft(h)),/读回/);
});
test('explicitly retaining and restoring a request preserves both original wire and new human work',()=>{
  const h=helpers(),storage=memory(),original=stage(h,storage),wire=original.value.request.wire;
  const fresh=h.observationJournalRetain(storage,original);
  assert.equal(fresh.value.request,null);assert.equal(fresh.value.history[0].request.wire,wire);
  const edited=h.observationJournalWrite(storage,fresh,{...fresh.value,draft:draft(h,{source_record_id:'另一份人稿'})});
  const restored=h.observationJournalRetain(storage,edited,0);
  assert.equal(restored.value.request.wire,wire);assert.equal(restored.value.history[0].draft.source_record_id,'另一份人稿');
});
for(const [name,value] of [['nonfinite',Infinity],['NUL','bad\0input'],['surrogate','bad\uD800']])test('unsafe '+name+' cannot become a frozen request',()=>{
  const h=helpers(),storage=memory(),original=h.observationJournalRead(storage,scope);
  assert.throws(()=>h.observationJournalStage(storage,original,body({attributes:{value}}),draft(h)));
  assert.equal(storage.values.size,0);
});
test('broken, foreign or expanded cached envelopes are retained without repair',()=>{
  const h=helpers(),storage=memory(),original=h.observationJournalRead(storage,scope);
  for(const value of ['{broken',JSON.stringify({...original.value,scope:{...scope,user_id:'other'}}),JSON.stringify({...original.value,credentials:'must-not-be-adopted'})]) {
    storage.values.set(original.key,value);assert.throws(()=>h.observationJournalRead(storage,scope));assert.equal(storage.values.get(original.key),value);
  }
});

function serialLocks() {
  const queues=new Map();return {request(name,options,fn){
    assert.equal(options.mode,'exclusive');const previous=queues.get(name)||Promise.resolve();
    const next=previous.then(fn);queues.set(name,next.catch(()=>{}));return next;
  }};
}
function response(value,status=200) {return {ok:status>=200&&status<300,status,text:async()=>JSON.stringify(value),json:async()=>plain(value)};}
function deferred() {let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function domEnvironment({storage=memory(),locks=serialLocks(),who=actor,entitiesAvailable=true,fieldType='number'}={}) {
  const dom=new JSDOM(indexHTML,{url:'http://localhost/',runScripts:'outside-only'}),window=dom.window,context=dom.getInternalVMContext();
  window.TextEncoder=globalThis.TextEncoder;
  Object.defineProperty(window,'localStorage',{value:storage});Object.defineProperty(window.navigator,'locks',{value:locks,configurable:true});
  const state={posts:[],gets:[],cookie:plain(who),post:async value=>response(record(value,who),201),get:null};
  window.fetch=async(path,options={})=>{
    if(options.method==='POST') {state.posts.push({path,wire:options.body,headers:plain(options.headers)});return state.post(JSON.parse(options.body),options);}
    state.gets.push(path);if(state.get)return state.get(path,options);
    if(path==='/api/me')return response(state.cookie);
    if(path==='/api/evidence')return response([]);
    throw Error('Unexpected Node contract GET: '+path);
  };
  vm.runInContext('let sensingOffset=0,deviceOffset=0,sensingOverview={},deviceOverview={};',context);
  vm.runInContext(operatorSource,context);vm.runInContext(inline,context);
  window.fixture={me:plain(who),caps:[
    {id:'package',kind:'domain_package',status:'published',data:{allowed_agent_roles:[],specification:{entity_schemas:{sample:{type:'object',properties:{temperature:{type:fieldType,title:'温度'}},required:['temperature']}}}}},
    {id:'source',kind:'source',status:'published',data:{description:'已登记来源',allowed_agent_roles:['perception'],execution:{adapter:'upload'},specification:{timezone:'Asia/Shanghai'}}},
    {id:'rules',kind:'tool',status:'published',data:{description:'本地规则',allowed_agent_roles:['perception'],execution:{adapter:'builtin',handler:'numeric_rules'},specification:{}}},
  ],entities:entitiesAvailable?[{id:'entity',schema_capability_id:'package',entity_type:'sample',external_id:'entity',data:{name:'合成对象'}}]:[]};
  // Skip unrelated asset augmenters only. The real HTML render/bind, original
  // form, shipped attachFileProcessing and every journal/POST guard run intact.
  vm.runInContext("me=fixture.me;caps=fixture.caps;entities=fixture.entities;overview={observations:[],events:[]};current='perception';augment=()=>{};csrf='synthetic-csrf';render();attachFileProcessing();",context);
  const run=text=>vm.runInContext(text,context),form=()=>window.document.querySelector('#observationForm');
  const button=name=>form().querySelector('[data-observation-'+name+']');
  const set=(id,value,event='input')=>{const input=form().querySelector('#'+id);input.value=value;input.dispatchEvent(new window.Event(event,{bubbles:true}));};
  const fill=async()=>{set('source','source','change');set('observedEntity','entity','change');set('extractor','rules','change');
    set('record','原始登记🙂');set('observedAt','2026-10-21T00:05');const field=form().querySelector('[data-field="temperature"]');field.value='0.5';field.dispatchEvent(new window.Event('input',{bubbles:true}));await flush();};
  const submit=async()=>{form().dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));await flush();};
  const read=()=>plain(window.observationJournalRead(storage,scope).value);
  return {dom,window,context,storage,state,run,form,button,set,fill,submit,read,close:()=>window.close()};
}

test('actual form saves edited values across render/remount without an automatic POST',async()=>{
  const storage=memory(),first=domEnvironment({storage});await first.fill();assert.equal(first.state.posts.length,0);first.close();
  const second=domEnvironment({storage});await flush();assert.equal(second.form().querySelector('#record').value,'原始登记🙂');
  assert.equal(second.form().querySelector('[data-field="temperature"]').value,'0.5');assert.equal(second.state.posts.length,0);second.close();
});
for(const [name,options] of [['object missing from page',{entitiesAvailable:false}],['field type changed',{fieldType:'string'}]])test('actual restore preserves original raw field when '+name,async()=>{
  const storage=memory(),first=domEnvironment({storage});await first.fill();first.close();
  const restored=domEnvironment({storage,...options});await flush();
  const original=restored.form().querySelector('[data-field="temperature"]');assert.equal(original.value,'0.5');assert.equal(original.dataset.type,'number');
  assert.equal(original.tagName,'TEXTAREA');assert.match(restored.form().textContent,/原稿字段/);
  restored.set('record','只改原编号仍保留数值');await flush();
  assert.deepEqual(restored.read().draft.fields,[{key:'temperature',type:'number',value:'0.5'}]);assert.equal(restored.state.posts.length,0);restored.close();
});
test('an explicit entity change retains the prior complete human draft before rebuilding fields',async()=>{
  const ui=domEnvironment();await ui.fill();ui.set('observedEntity','','change');await flush();
  assert.equal(ui.read().draft.entity_id,'');assert.deepEqual(ui.read().draft.fields,[]);
  assert.equal(ui.read().history.at(-1).draft.entity_id,'entity');
  assert.deepEqual(ui.read().history.at(-1).draft.fields,[{key:'temperature',type:'number',value:'0.5'}]);assert.equal(ui.state.posts.length,0);ui.close();
});
function restoredRawField(type) {
  const h=helpers(),storage=memory(),original=h.observationJournalRead(storage,scope);
  const value=type==='boolean'?'true':type==='integer'?'2':'0.5';
  h.observationJournalWrite(storage,original,{...original.value,draft:draft(h,{fields:[{key:'temperature',type,value}]})});
  return domEnvironment({storage,fieldType:'string'});
}
for(const [type,value] of [['boolean','yes'],['boolean','False'],['integer','1.5'],['integer','9007199254740993'],['integer',' '],['number','NaN'],['number','1e309'],['number','0x10']])test('actual raw '+type+' submit refuses '+JSON.stringify(value)+' without changing the original input',async()=>{
  const ui=restoredRawField(type);await flush();const field=ui.form().querySelector('[data-field="temperature"]');
  assert.equal(field.tagName,'TEXTAREA');assert.equal(field.dataset.type,type);field.value=value;
  field.dispatchEvent(new ui.window.Event('input',{bubbles:true}));await flush();await ui.submit();
  assert.equal(ui.state.posts.length,0);assert.equal(ui.read().request,null);
  assert.deepEqual(ui.read().draft.fields,[{key:'temperature',type,value}]);assert.equal(field.value,value);
  assert.match(ui.form().querySelector('[data-observation-error]').textContent,/原输入保留/);ui.close();
});
for(const [type,value,expected] of [['boolean','true',true],['boolean','false',false],['integer','-2',-2],['integer','1e3',1000],['number','.5',0.5],['number','1e-5',0.00001],['number','-0.25',-0.25]])test('actual raw '+type+' submit explicitly validates '+value+' and sends its typed value',async()=>{
  const ui=restoredRawField(type);await flush();const field=ui.form().querySelector('[data-field="temperature"]');
  assert.equal(field.tagName,'TEXTAREA');assert.equal(field.dataset.type,type);
  if(type==='boolean'&&value==='false'){
    field.value='arbitrary';field.dispatchEvent(new ui.window.Event('input',{bubbles:true}));await flush();await ui.submit();
    assert.equal(ui.state.posts.length,0);assert.equal(ui.read().request,null);assert.equal(field.value,'arbitrary');
  }
  field.value=value;
  field.dispatchEvent(new ui.window.Event('input',{bubbles:true}));await flush();await ui.submit();
  assert.equal(ui.state.posts.length,1);assert.equal(JSON.parse(ui.state.posts[0].wire).attributes.temperature,expected);
  assert.equal(typeof JSON.parse(ui.state.posts[0].wire).attributes.temperature,typeof expected);
  assert.equal(ui.read().request.status,'saved');assert.deepEqual(ui.read().draft.fields,[{key:'temperature',type,value}]);ui.close();
});
test('lost reply/remount/manual retry POST the identical frozen bytes, time, record and version',async()=>{
  const storage=memory(),first=domEnvironment({storage});await first.fill();first.state.post=async()=>{throw Error('Synthetic reply loss');};
  await first.submit();const wire=first.state.posts[0].wire;assert.equal(first.read().request.wire,wire);assert.equal(first.read().request.status,'unknown');first.close();
  const second=domEnvironment({storage});await flush();assert.equal(second.state.posts.length,0);
  second.form().querySelector('#record').value='不可改写原请求';second.form().querySelector('#observedAt').value='2030-01-01T10:00';
  await second.button('retry').onclick();await flush();assert.equal(second.state.posts.length,1);assert.equal(second.state.posts[0].wire,wire);
  assert.equal(JSON.parse(wire).processing_version,'1.0.0');assert.equal(JSON.parse(wire).business_day,'2026-10-21');
  assert.equal(JSON.parse(wire).observed_at,'2026-10-20T16:05:00.000Z');assert.equal(second.read().request.status,'saved');
  assert.equal(second.state.posts[0].headers['X-Workspace-ID'],scope.workspace_id);assert.equal(second.state.posts[0].headers['X-CSRF-Token'],'synthetic-csrf');second.close();
});
test('saved receipt is persisted before any list refresh; failing explicit load cannot cause a resubmission',async()=>{
  const ui=domEnvironment();await ui.fill();await ui.submit();assert.equal(ui.read().request.status,'saved');assert.equal(ui.state.posts.length,1);
  assert.equal(ui.state.gets.includes('/api/overview'),false,'successful POST must not auto-load');
  ui.state.get=async path=>path==='/api/me'?response(actor):response({detail:'Synthetic list unavailable'},503);
  await ui.button('refresh').onclick();await flush();assert.equal(ui.state.posts.length,1);assert.equal(ui.read().request.saved_id,'saved-observation');
  assert.match(ui.form().querySelector('[data-observation-error]').textContent,/保存记录仍在本机/);assert.equal(ui.button('retry').disabled,true);ui.close();
});
test('actual 409 keeps original wire/error and reload does not automatically retry',async()=>{
  const storage=memory(),ui=domEnvironment({storage});await ui.fill();ui.state.post=async()=>response({detail:'当前来源已撤回'},409);await ui.submit();
  const original=ui.read().request;assert.equal(original.status,'rejected');assert.equal(original.saved_id,null);assert.match(original.error,/已撤回/);ui.close();
  const restored=domEnvironment({storage});await flush();assert.equal(restored.state.posts.length,0);assert.equal(restored.read().request.wire,original.wire);restored.close();
});
test('storage failure blocks actual submit and navigation, retaining visible human input',async()=>{
  const ui=domEnvironment();await ui.fill();ui.storage.failWrite=true;ui.set('record','不能丢的人稿');await flush();await ui.submit();
  assert.equal(ui.state.posts.length,0);assert.equal(ui.form().querySelector('#record').value,'不能丢的人稿');
  ui.window.document.querySelector('nav button').click();assert.equal(ui.run('current'),'perception');
  const event=new ui.window.Event('beforeunload',{cancelable:true});ui.window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);ui.close();
});
test('missing WebLocks leaves an original readable but cannot send, including direct handler invocation',async()=>{
  const storage=memory(),h=helpers();stage(h,storage);const ui=domEnvironment({storage,locks:null});await flush();
  await ui.submit();await ui.button('retry').onclick();await flush();assert.equal(ui.state.posts.length,0);
  assert.equal(ui.read().request.wire,JSON.stringify(body()));assert.match(ui.form().textContent,/浏览器/);ui.close();
});
test('matching saved Evidence IDs remain frozen even when absent from the recent file list',async()=>{
  const storage=memory(),h=helpers(),value=body({evidence_ids:['older-evidence'],attributes:{},extractor_id:'file-rules'});
  const original=h.observationJournalRead(storage,scope);h.observationJournalStage(storage,original,value,draft(h,{evidence_id:'older-evidence',extractor_id:'file-rules'}));
  const ui=domEnvironment({storage});await flush();await ui.button('retry').onclick();await flush();
  assert.equal(ui.state.posts[0].wire,JSON.stringify(value));assert.equal(ui.read().request.status,'saved');ui.close();
});
test('mismatched server original stays unknown rather than claiming a saved ID',async()=>{
  const ui=domEnvironment();await ui.fill();ui.state.post=async value=>{const row=record(value);row.data.original_request.attributes.temperature=88;return response(row,201);};
  await ui.submit();assert.equal(ui.read().request.status,'unknown');assert.equal(ui.read().request.saved_id,null);assert.match(ui.read().request.error,/不一致/);ui.close();
});
test('late POST after global account scope changes cannot write either the new journal or old saved state',async()=>{
  const ui=domEnvironment(),gate=deferred();await ui.fill();ui.state.post=()=>gate.promise;await ui.submit();
  const before=ui.read().request;ui.run("me={user_id:'actor-B',workspace_id:'workspace-B',role:'operator'}");
  gate.resolve(response(record(JSON.parse(before.wire)),201));await flush();assert.deepEqual(ui.read().request,before);
  assert.equal([...ui.storage.values.keys()].some(key=>key.includes('actor-B')),false);ui.close();
});
test('actual /me changes while POST is in flight invalidate the old UI without adopting the reply',async()=>{
  const ui=domEnvironment(),gate=deferred();await ui.fill();ui.state.post=()=>gate.promise;await ui.submit();const before=ui.read().request;
  ui.state.cookie={...actor,user_id:'actor-B'};gate.resolve(response(record(JSON.parse(before.wire)),201));await flush();
  assert.equal(ui.run('me'),null);assert.deepEqual(ui.read().request,before);assert.equal(ui.window.document.querySelector('#app').classList.contains('hidden'),true);ui.close();
});
test('a late journal POST 401 cannot clear a newer same-actor load identity or CSRF',async()=>{
  const ui=domEnvironment(),gate=deferred();await ui.fill();ui.state.post=()=>gate.promise;await ui.submit();const wire=ui.read().request.wire;
  ui.state.get=async path=>response(path==='/api/me'?actor:path==='/api/overview'?{observations:[],events:[]}:[]);
  ui.run("csrf='new-load-csrf'");assert.equal(await ui.window.load(),true);
  gate.resolve(response({detail:'Synthetic old POST session expired'},401));await flush();
  assert.deepEqual(plain(ui.run('me')),actor);assert.equal(ui.run('csrf'),'new-load-csrf');assert.equal(ui.read().request.wire,wire);
  assert.equal(ui.state.posts.length,1);ui.close();
});
test('explicit retain/new draft and restoring history never silently replace an unknown request',async()=>{
  const ui=domEnvironment();await ui.fill();ui.state.post=async()=>{throw Error('Synthetic unknown');};await ui.submit();const wire=ui.read().request.wire;
  await ui.button('new').onclick();await flush();assert.equal(ui.read().request,null);assert.equal(ui.read().history[0].request.wire,wire);
  ui.set('record','另一个未提交的人稿');await flush();await ui.form().querySelector('[data-observation-history] button').onclick();await flush();
  assert.equal(ui.read().request.wire,wire);assert.equal(ui.read().history[0].draft.source_record_id,'另一个未提交的人稿');assert.equal(ui.state.posts.length,1);ui.close();
});
test('same-scope stale tab keeps human text and can explicitly retain it without clobbering the other tab',async()=>{
  const storage=memory(),locks=serialLocks(),a=domEnvironment({storage,locks}),b=domEnvironment({storage,locks});
  await a.fill();b.set('record','第二页必须保留的人稿');await flush();assert.equal(b.state.posts.length,0);
  assert.equal(b.form().querySelector('#record').value,'第二页必须保留的人稿');
  await b.button('reread').onclick();await flush();assert.equal(b.read().draft.source_record_id,'原始登记🙂');
  assert.equal(b.read().history.at(-1).draft.source_record_id,'第二页必须保留的人稿');a.close();b.close();
});

function loadEnvironment() {
  const dom=new JSDOM(indexHTML,{url:'http://localhost/',runScripts:'outside-only'}),window=dom.window,context=dom.getInternalVMContext();
  window.TextEncoder=globalThis.TextEncoder;
  const state={calls:[],handler:null};window.fetch=async(path,options={})=>{state.calls.push(path);return state.handler(path,options);};
  vm.runInContext('let sensingOffset=0,deviceOffset=0,sensingOverview={},deviceOverview={};',context);
  vm.runInContext(operatorSource,context);vm.runInContext(inline,context);window.fixture=plain(actor);vm.runInContext("me=fixture;caps=['old-capabilities'];globalThis.renderCount=0;render=()=>{renderCount++;};csrf='synthetic-csrf';",context);
  return {dom,window,state,run:text=>vm.runInContext(text,context),close:()=>window.close()};
}
test('actual load writes no globals while its batch waits; signout invalidates all late results',async()=>{
  const env=loadEnvironment(),gate=deferred();env.state.handler=path=>path==='/api/me'?Promise.resolve(response(actor)):gate.promise;
  const loading=env.window.load();await flush();assert.deepEqual(plain(env.run('caps')),['old-capabilities']);
  env.window.signoutView();gate.resolve(response([]));assert.equal(await loading,false);
  assert.equal(env.run('me'),null);assert.deepEqual(plain(env.run('caps')),['old-capabilities']);assert.equal(env.run('renderCount'),0);env.close();
});
for(const [name,changed] of [['actor',{...actor,user_id:'other'}],['workspace',{...actor,workspace_id:'other'}],['role',{...actor,role:'viewer'}]])test('actual load refuses a changed final '+name+' before one global assignment',async()=>{
  const env=loadEnvironment();let identities=0;env.state.handler=async path=>response(path==='/api/me'?(++identities===1?actor:changed):[]);
  await assert.rejects(env.window.load(),/已变化/);assert.equal(env.run('me'),null);assert.equal(env.window.document.querySelector('#app').classList.contains('hidden'),true);
  assert.deepEqual(plain(env.run('caps')),['old-capabilities']);assert.equal(env.run('renderCount'),0);env.close();
});
test('a first /me mismatch masks the old actor immediately without loading another scope into it',async()=>{
  const env=loadEnvironment();env.window.document.querySelector('#app').classList.remove('hidden');
  env.state.handler=async path=>{assert.equal(path,'/api/me');return response({...actor,user_id:'actor-B'});};
  await assert.rejects(env.window.load(),/已变化/);assert.equal(env.run('me'),null);assert.equal(env.run('csrf'),'');
  assert.equal(env.window.document.querySelector('#app').classList.contains('hidden'),true);assert.deepEqual(env.state.calls,['/api/me']);env.close();
});
test('newer actual load wins and an older delayed generation cannot overwrite it',async()=>{
  const env=loadEnvironment(),gate=deferred();let mode='old';const newer={...actor,user_id:'actor-B',username:'new-operator'};
  env.state.handler=path=>path==='/api/me'?Promise.resolve(response(mode==='old'?actor:newer)):mode==='old'?gate.promise:Promise.resolve(response(['new-scope']));
  const old=env.window.load();await flush();mode='new';env.window.fixture=plain(newer);env.run('me=fixture');assert.equal(await env.window.load(),true);gate.resolve(response(['old-scope']));assert.equal(await old,false);
  assert.deepEqual(plain(env.run('me')),newer);assert.deepEqual(plain(env.run('caps')),['new-scope']);assert.equal(env.run('renderCount'),1);env.close();
});
test('a late old-load 401 cannot sign out the newer actor or erase its CSRF and retained draft',async()=>{
  const env=loadEnvironment(),gate=deferred();let mode='old';const newer={...actor,user_id:'actor-B',workspace_id:'workspace-B'};
  env.state.handler=path=>path==='/api/me'?Promise.resolve(response(mode==='old'?actor:newer)):mode==='old'?gate.promise:Promise.resolve(response([]));
  const old=env.window.load(),oldFailure=assert.rejects(old,/Synthetic expired old session/);await flush();
  mode='new';env.window.fixture=plain(newer);env.run("me=fixture;csrf='new-actor-csrf'");
  env.window.localStorage.setItem('new-actor-human-draft','must remain');assert.equal(await env.window.load(),true);
  gate.resolve(response({detail:'Synthetic expired old session'},401));await oldFailure;
  assert.deepEqual(plain(env.run('me')),newer);assert.equal(env.run('csrf'),'new-actor-csrf');
  assert.equal(env.window.localStorage.getItem('new-actor-human-draft'),'must remain');assert.equal(env.run('renderCount'),1);env.close();
});
test('a current-generation 401 still masks the page and invalidates its identity',async()=>{
  const env=loadEnvironment();env.window.document.querySelector('#app').classList.remove('hidden');
  env.state.handler=async()=>response({detail:'Synthetic current session expired'},401);
  await assert.rejects(env.window.load(),/Synthetic current session expired/);assert.equal(env.run('me'),null);assert.equal(env.run('csrf'),'');
  assert.equal(env.window.document.querySelector('#app').classList.contains('hidden'),true);env.close();
});
test('failed actual list fetch never partially replaces old globals',async()=>{
  const env=loadEnvironment();env.state.handler=async path=>path==='/api/me'?response(actor):response({detail:'Synthetic batch failure'},503);
  await assert.rejects(env.window.load(),/Synthetic batch failure/);assert.deepEqual(plain(env.run('me')),actor);
  assert.deepEqual(plain(env.run('caps')),['old-capabilities']);assert.equal(env.run('renderCount'),0);env.close();
});
