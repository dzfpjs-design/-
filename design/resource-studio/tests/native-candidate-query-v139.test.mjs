// The actual component, React DOM/StrictMode, runtime-api and request journal.
// HTTP below is synthetic: these tests prove UI/request contracts, not backend
// provenance, real learned inference, a browser, or database restart durability.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto,createHash} from 'node:crypto';
import React,{act,useLayoutEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {CandidateQueryJournalV139,candidateQueryScopeV139,candidateQueryPrefixV139,candidateQueryCommandV139,forgetCandidateQueryDraftV139} from '../src/native-candidate-query-v139.mjs';
import {stableStringify} from '../src/content-signature.js';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'native-candidate-query-v139-'));const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as Panel} from '+JSON.stringify(join(project,'src/NativeCandidateQueryV139.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(output,'bundle the actual query component and actual dependencies');const file=join(temporary,'component.mjs');await writeFile(file,output.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
const id=value=>'13900000-0000-4000-8000-'+String(value).padStart(12,'0');
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-candidate-operator',role:'operator'},instance=id(3),release=id(4),entity=id(5),taskID=id(6);
const scope=candidateQueryScopeV139(account,instance,release,entity),prefix=candidateQueryPrefixV139(scope);
const copy=value=>JSON.parse(JSON.stringify(value));
const hash=value=>createHash('sha256').update(stableStringify(value)).digest('hex');
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
function lockManager(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const prior=tails.get(name)||Promise.resolve(),next=prior.catch(()=>{}).then(callback);tails.set(name,next);return next.finally(()=>{if(tails.get(name)===next)tails.delete(name);});}};}
function object(identity=entity){return {id:identity,name:identity===entity?'实际关联的示例林班':'另一个明确对象',version:1,object_compatibility:{status:'current',target_release_id:release,usable:true,source_label:'示例林班',source_type:'测试对象',source_release_number:1}};}
function queryView(body,index=1){return {schema:'native-candidate-query-view-v139',id:id(100+index),version:1,status:'sufficient',request_id:body.request_id,request:copy(body),job_id:id(200+index),integrity_error:null,source_changed:false,not_authoritative:true,candidate_only:true,records:[{observation_id:id(300+index),entity_id:body.entity_id,task_id:taskID,name:'已保存的学习型双影像候选',attributes:{'差异候选像素数':12,'候选说明':'仅供人工核查'},review_state:'confirmed',source_time_verified:false,physical_state_verified:false}],truncated:false};}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden in the synthetic DOM');}}
async function harness(run){
 const errors=[],violations=[],calls=[],opened=[],commits=[],gates=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://candidate-query.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole:console});
 const backing=dom.window.localStorage,session=dom.window.sessionStorage,token='synthetic-candidate-token';
 const state={account:copy(account),who:copy(account),entity,visibility:'visible',quota:false,missingLocks:false,listError:false,lost:null,postGate:null,meGate:null,beforePost:null,queries:new Map(),transform:value=>value};
 const storage={get length(){return backing.length;},key:index=>backing.key(index),getItem:key=>backing.getItem(key),removeItem:key=>backing.removeItem(key),setItem(key,value){if(state.quota&&key.includes('ga-native-candidate-query-v139:')&&key.includes(':draft:'))throw new dom.window.DOMException('Synthetic quota','QuotaExceededError');backing.setItem(key,value);}};
 const locks=lockManager();Object.defineProperty(dom.window,'localStorage',{configurable:true,value:storage});Object.defineProperty(dom.window.navigator,'locks',{configurable:true,get:()=>state.missingLocks?undefined:locks});Object.defineProperty(dom.window.document,'visibilityState',{configurable:true,get:()=>state.visibility});
 session.setItem('ga-setup-csrf',token);session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 const base='/api/workspace/instances/'+instance+'/native-candidate-queries';
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET',path=url.pathname+url.search;calls.push({method,path,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),state.account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),token);
   const respond=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
   if(url.pathname==='/api/me'){assert.equal(method,'GET');assert.equal(url.search,'');const who=copy(state.who),gate=state.meGate;if(gate)await gate.promise;return respond(who);}
   assert.ok(url.pathname===base||url.pathname.startsWith(base+'/'),'only the real candidate-query routes are allowed');
   const actor=state.who.user_id;
   if(url.pathname===base){
    assert.equal(url.search,'');
    if(method==='GET'){if(state.listError)return respond({detail:'Synthetic query list unavailable'},503);return respond({schema:'native-candidate-query-list-v139',queries:[...state.queries.values()].filter(row=>row.actor===actor).map(row=>state.transform(copy(row.value)))});}
    assert.equal(method,'POST');const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['entity_id','field','release_id','request_id','text']);assert.equal(body.release_id,release);assert.equal(body.entity_id,state.entity);
    if(state.beforePost)await state.beforePost(body,options.body);if(state.lost==='before'){state.lost=null;throw Error('synthetic lost before commit');}
    const key=actor+':'+body.request_id,prior=state.queries.get(key);if(prior)assert.deepEqual(prior.value.request,body,'original nonce never changes body');
    const value=prior?.value||queryView(body,state.queries.size+1);if(!prior)state.queries.set(key,{actor,value});const gate=state.postGate;if(gate)await gate.promise;
    if(state.lost==='after'){state.lost=null;throw Error('synthetic lost after commit');}return respond(state.transform(copy(value)),201);
   }
   assert.equal(method,'GET');
   if(url.pathname.startsWith(base+'/requests/')){
    const nonce=url.pathname.slice((base+'/requests/').length),row=state.queries.get(actor+':'+nonce);assert.equal([...url.searchParams.keys()].join(','),'expected_hash');assert.match(url.searchParams.get('expected_hash'),/^[a-f0-9]{64}$/);
    if(row)assert.equal(url.searchParams.get('expected_hash'),hash(row.value.request),'lookup binds the entire original command');return respond({found:!!row,query:row?state.transform(copy(row.value)):null});
   }
   assert.equal(url.search,'');const identity=url.pathname.slice((base+'/').length),row=[...state.queries.values()].find(row=>row.actor===actor&&row.value.id===identity);assert.ok(row,'detail must be an actual saved synthetic query');return respond(state.transform(copy(row.value)));
  }catch(error){if(error.message.startsWith('synthetic lost'))throw error;violations.push(error.stack||error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};const prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 let root;const container=dom.window.document.getElementById('root');
 try{
  // Load the real development/test renderer before Vite can default NODE_ENV.
  const renderer=await import('react-dom/client'),actual=await component();assert.equal(actual.BundledReact,React);assert.equal(renderer.version,React.version);actual.setSession(token,account.workspace_id,account.user_id,account.username);
  function Witness(){useLayoutEffect(()=>{commits.push({actor:state.account.user_id,entity:state.entity,text:container.textContent,inputs:[...container.querySelectorAll('input')].map(node=>node.value)});});return React.createElement(actual.Panel,{account:state.account,instanceId:instance,releaseID:release,objects:[object(),object(id(7))],selectedObject:state.entity,Btn:Button,onOpenTask:identity=>opened.push(identity)});}
  const settle=async(turns=12)=>{for(let index=0;index<turns;index++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const wait=async(predicate,label)=>{for(let index=0;index<100;index++){if(predicate())return;await settle(1);}assert.ok(predicate(),label+'; '+JSON.stringify({text:container.textContent.slice(0,1600),calls:calls.slice(-12).map(({method,path})=>({method,path})),errors:errors.map(error=>String(error.stack||error)).slice(0,1),violations:violations.slice(0,1)}));};
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Witness))));await settle();};
  const mount=async()=>{root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const unmount=async()=>{if(root){const value=root;root=null;await act(async()=>value.unmount());await settle();}};
  const control=label=>container.querySelector('[aria-label="'+label+'"]'),button=text=>[...container.querySelectorAll('button')].find(node=>node.textContent===text);
  const change=async(label,value)=>{const node=control(label);assert.ok(node,'actual control '+label);assert.equal(node.disabled,false);const name=node.tagName==='SELECT'?'HTMLSelectElement':'HTMLInputElement',setter=Object.getOwnPropertyDescriptor(dom.window[name].prototype,'value').set;await act(async()=>{setter.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();assert.equal(control(label)?.value,value);};
  const click=async label=>{const node=button(label);assert.ok(node,'actual button '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const ready=()=>wait(()=>!!control('候选搜索文字')&&!button('读取保存的查询')?.disabled,'actual query form ready after fresh identity');
  const snapshot=()=>Array.from({length:storage.length},(_,index)=>storage.key(index)).sort().map(key=>[key,storage.getItem(key)]);
  const posts=()=>calls.filter(row=>row.method==='POST');
  const gate=()=>{const value=deferred();gates.push(value);return value;};
  await run({dom,state,calls,opened,commits,storage,session,base,container,mount,unmount,render,settle,wait,control,button,change,click,ready,snapshot,posts,gate,setSession:actual.setSession});
  await unmount();assert.deepEqual(violations,[]);assert.deepEqual(errors,[]);
 }finally{gates.forEach(value=>value.resolve());if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}forgetCandidateQueryDraftV139(scope);}
}

test('actual panel requires one selected object and never starts a query on mount',()=>harness(async h=>{
 h.state.entity='';await h.mount();assert.ok(h.container.textContent.includes('请先在上方选择一个业务对象'));assert.equal(h.calls.length,0);assert.equal(h.control('候选搜索文字'),null);
 h.state.entity=entity;await h.render();await h.ready();assert.equal(h.posts().length,0);assert.ok(h.container.textContent.includes('不会重新推理'));assert.ok(h.container.textContent.includes('当前对象和版本还没有可显示的查询'));
}));

test('actual search stores exact nonce/body before lookup and POST, then opens original task',()=>harness(async h=>{
 await h.mount();await h.ready();await h.change('候选搜索文字','候选像素');assert.equal(h.posts().length,0);let stored=false;
 h.state.beforePost=(body,raw)=>{const head=JSON.parse(h.storage.getItem(prefix+'active')),record=JSON.parse(h.storage.getItem(head.key));assert.equal(record.exact_body,raw);assert.deepEqual(record.body,body);assert.equal(record.body_hash,hash(body));assert.ok(h.calls.some(call=>call.method==='GET'&&call.path===h.base+'/requests/'+body.request_id+'?expected_hash='+record.body_hash));stored=true;};
 await h.click('开始查询');await h.wait(()=>h.container.textContent.includes('已保存的学习型双影像候选'),'actual saved candidate visible');assert.equal(stored,true);assert.equal(h.posts().length,1);assert.ok(h.container.textContent.includes('原业务人工核查：已核查'));assert.ok(h.container.textContent.includes('采集时间未知'));assert.equal(h.storage.getItem(prefix+'active'),null);
 await h.click('打开原业务任务与依据');assert.deepEqual(h.opened,[taskID]);await h.click('读取保存的查询');assert.equal(h.posts().length,1,'history refresh only reads');
}));

test('actual committed lost reply remount only finds the same original nonce',()=>harness(async h=>{
 await h.mount();await h.ready();await h.change('候选检索字段','review_status');await h.change('候选搜索文字','已核查');h.state.lost='after';await h.click('开始查询');await h.wait(()=>!!h.button('只核对原查询'),'unknown query remains recoverable');
 const rawPost=h.posts()[0].body,body=JSON.parse(rawPost),head=JSON.parse(h.storage.getItem(prefix+'active')),raw=h.storage.getItem(head.key);assert.equal(h.state.queries.size,1);await h.unmount();const postCount=h.posts().length;
 await h.mount();await h.ready();await h.wait(()=>h.container.textContent.includes('已保存的学习型双影像候选'),'cold original lookup restored real response');assert.equal(h.posts().length,postCount);assert.equal(h.storage.getItem(head.key),raw);assert.equal(JSON.parse(raw).exact_body,rawPost);assert.ok(h.calls.some(row=>row.method==='GET'&&row.path===h.base+'/requests/'+body.request_id+'?expected_hash='+hash(body)));
}));

test('actual negative lookup after lost request does not resend until explicit same-wire retry',()=>harness(async h=>{
 await h.mount();await h.ready();await h.change('候选搜索文字','差异');h.state.lost='before';await h.click('开始查询');await h.wait(()=>!!h.button('只核对原查询'),'original unknown held');const raw=h.posts()[0].body;
 await h.unmount();await h.mount();await h.ready();await h.wait(()=>h.container.textContent.includes('尚未找到这个原请求'),'negative original lookup shown');assert.equal(h.posts().length,1);assert.equal(h.control('候选搜索文字').disabled,true);
 await h.click('只核对原查询');assert.equal(h.posts().length,1);await h.click('按原条件重试');await h.wait(()=>h.container.textContent.includes('已保存的学习型双影像候选'),'explicit original retry saved');assert.equal(h.posts().length,2);assert.equal(h.posts()[1].body,raw);assert.equal(h.state.queries.size,1);
}));

test('actual saved query survives history read failure without losing original request or posting again',()=>harness(async h=>{
 await h.mount();await h.ready();await h.change('候选搜索文字','候选');h.state.beforePost=()=>{h.state.listError=true;};await h.click('开始查询');await h.wait(()=>h.container.textContent.includes('原检索已保存'),'saved response survives later list failure');assert.ok(h.container.textContent.includes('已保存的学习型双影像候选'));const original=h.posts()[0].body;
 await h.unmount();await h.mount();await h.ready();await h.wait(()=>h.container.textContent.includes('已保存的学习型双影像候选'),'saved hint only triggers authoritative original lookup');assert.equal(h.posts().length,1);assert.ok(h.snapshot().some(([key,raw])=>key.startsWith(prefix+'request:')&&JSON.parse(raw).exact_body===original));
}));

test('actual account/scope switch conceals old results on the first commit and fences late POST reply',()=>harness(async h=>{
 await h.mount();await h.ready();await h.change('候选搜索文字','原账号私有检索文字');const gate=h.gate();h.state.postGate=gate;await h.click('开始查询');await h.wait(()=>h.state.queries.size===1,'synthetic request committed but reply held');const old=h.snapshot(),other={...account,user_id:id(8),username:'另一个操作员'};
 h.state.account=other;h.state.who=other;h.state.entity=id(7);h.setSession('synthetic-candidate-token',other.workspace_id,other.user_id,other.username);await h.render();await h.ready();const commit=h.commits.find(row=>row.actor===other.user_id);assert.ok(commit);assert.ok(!commit.text.includes('原账号私有检索文字')&&!commit.inputs.includes('原账号私有检索文字'));
 await h.change('候选搜索文字','新账号自己的文字');const next=h.snapshot();gate.resolve();h.state.postGate=null;await h.settle();assert.equal(h.control('候选搜索文字').value,'新账号自己的文字');assert.ok(!h.container.textContent.includes('已保存的学习型双影像候选'));assert.deepEqual(h.snapshot(),next);assert.ok(old.some(([key])=>key===prefix+'active'));assert.notEqual(h.storage.getItem(prefix+'active'),null,'late old reply cannot settle the old outbox after identity changed');
}));

test('actual focus identity check masks the old private draft before a mismatched cookie response',()=>harness(async h=>{
 await h.mount();await h.ready();await h.change('候选搜索文字','原账号不可外露的文字');const gate=h.gate();h.state.meGate=gate;await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.equal(h.control('候选搜索文字'),null,'concealed while fresh identity pending');
 const other={...account,user_id:id(9)};h.state.who=other;h.state.meGate=null;await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.equal(h.control('候选搜索文字'),null);gate.resolve();await h.settle();assert.equal(h.control('候选搜索文字'),null,'late old /me cannot reveal old draft');assert.equal(h.posts().length,0);assert.ok(h.snapshot().some(([,raw])=>raw.includes('原账号不可外露的文字')));
}));

test('actual quota failure retains editable memory across remount and prevents request creation',()=>harness(async h=>{
 await h.mount();await h.ready();h.state.quota=true;await h.change('候选搜索文字','即使本机保存失败也保留的输入');assert.equal(h.button('开始查询').disabled,true);const event=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);assert.equal(h.posts().length,0);
 await h.unmount();await h.mount();await h.ready();assert.equal(h.control('候选搜索文字').value,'即使本机保存失败也保留的输入');assert.equal(h.button('开始查询').disabled,true);h.state.quota=false;await h.click('重试保存检索输入');assert.equal(h.button('开始查询').disabled,false);assert.equal(h.posts().length,0);const safe=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(safe);assert.equal(safe.defaultPrevented,false);
}));

test('actual missing WebLocks keeps drafts readable and blocks POST',()=>harness(async h=>{
 h.state.missingLocks=true;await h.mount();await h.ready();await h.change('候选搜索文字','可保存但不能发送');assert.equal(h.button('开始查询').disabled,true);assert.equal(h.posts().length,0);assert.ok(h.container.textContent.includes('暂不能提交'));await h.unmount();await h.mount();await h.ready();assert.equal(h.control('候选搜索文字').value,'可保存但不能发送');assert.equal(h.posts().length,0);
}));

test('actual changed source hides candidate values and never claims an empty result means normal',()=>harness(async h=>{
 await h.mount();await h.ready();h.state.transform=value=>({...value,source_changed:true,records:[]});await h.change('候选搜索文字','变化');await h.click('开始查询');await h.wait(()=>h.container.textContent.includes('原资料或人工核查已变化'),'changed source state visible');assert.ok(!h.container.textContent.includes('已保存的学习型双影像候选'));assert.ok(h.container.textContent.includes('不表示现场正常'));assert.equal(h.posts().length,1);
}));

test('actual saved queued/failed progress stays readable and a fresh retry is an explicit new query',()=>harness(async h=>{
 await h.mount();await h.ready();h.state.transform=value=>({...value,status:'queued',records:[]});await h.change('候选搜索文字','需要继续核对');await h.click('开始查询');await h.wait(()=>h.container.textContent.includes('已保存，等待本地检索'),'saved queued progress visible');assert.equal(h.posts().length,1);const original=h.posts()[0].body,nonce=JSON.parse(original).request_id,key=prefix+'request:'+nonce,raw=h.storage.getItem(key);
 h.state.transform=value=>({...value,status:'failed',records:[]});await h.click('查看这次查询');await h.wait(()=>h.container.textContent.includes('检索未完成，原请求保留'),'known failed query remains accessible');assert.equal(h.posts().length,1);assert.ok(h.container.textContent.includes('会保存一条新的检索'));
 await h.click('开始查询');assert.equal(h.posts().length,2);assert.notEqual(JSON.parse(h.posts()[1].body).request_id,nonce);assert.equal(h.storage.getItem(key),raw,'failed original request is retained unchanged');
}));

test('shared real journal refuses another original request and never replaces it with a draft',async()=>{
 const old=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
 const map=new Map(),storage={getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value),removeItem:key=>map.delete(key)},session=()=>{const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value)};};
 try{
  const locks=lockManager(),left=new CandidateQueryJournalV139(scope,{storage,session:session(),locks}),right=new CandidateQueryJournalV139(scope,{storage,session:session(),locks});left.open();right.open();left.save({field:'search_text',text:'左边人稿'});right.save({field:'search_text',text:'右边人稿'});
  const original=await left.install(candidateQueryCommandV139(scope,{field:'search_text',text:'原请求'},id(901)),async()=>{}),before=storage.getItem(prefix+'active');right.save({field:'search_text',text:'另页修改也不能覆盖原请求'});assert.equal(storage.getItem(prefix+'active'),before);
  await assert.rejects(right.install(candidateQueryCommandV139(scope,{field:'search_text',text:'第二请求'},id(902)),async()=>{}),/另一窗口/);assert.equal(storage.getItem(original.key),original.raw);assert.equal(storage.getItem(prefix+'active'),before);
  const changed=JSON.parse(original.raw);changed.body.text='替换后的槽';changed.exact_body=stableStringify(changed.body);changed.body_hash=hash(changed.body);storage.setItem(original.key,JSON.stringify(changed));let sent=false;await assert.rejects(left.withOriginal(original,async()=>{},async()=>{sent=true;}),/原请求已/);assert.equal(sent,false);
 }finally{if(old)Object.defineProperty(globalThis,'crypto',old);else delete globalThis.crypto;}
});
