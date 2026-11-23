// Mounted real React components and runtime-api, synthetic transport only.
// No browser, model, backend, fixture file, component or hook replacement.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act,useLayoutEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {defaultNativeGoal} from '../src/native-goal-contract.mjs';
import {typedRequestJSON} from '../src/typed-numbers-v132.mjs';
import {queryMetadata} from './native-query-fixtures.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{temp=await mkdtemp(join(tmpdir(),'native-goal-typed-react-'));const entry=join(temp,'entry.mjs');
  await writeFile(entry,'export {default as Studio} from '+JSON.stringify(join(project,'src/NativeGoalStudio.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\n');
  const result=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const file=join(temp,'studio.mjs');await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
const account={user_id:'13210000-0000-4000-8000-000000000001',workspace_id:'13210000-0000-4000-8000-000000000002',username:'synthetic-operator',role:'operator'};
const task={id:'13210000-0000-4000-8000-000000000003',title:'原数值目标任务',version:3};
const legacyKey=id=>'ga-native-goal:'+account.workspace_id+':'+account.username+':'+id;
const oldForm=()=>({...defaultNativeGoal('仅当前账号可见的原人工目标稿'),fields:['reading'],targetField:'reading',targetValue:'-0.0',reason:'明确核对原数值'});
const goal=id=>({...queryMetadata,task_id:id,version:3,task_status:'workflow_waiting',can_manage:true,can_stop:false,goal_confirmed:false,goal:null,fields:{reading:'核查数值'},field_kinds:{reading:'number'},entity_ids:['original-object'],field_units:{reading:null},goal_tools:[],relation_types:[],check:null,check_worker:null,acceptance:null,cancelled:null,scope_error:null});
const copy=value=>structuredClone(value);
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden');}}
const locks=()=>{const tails=new Map();return {request(name,options,fn){assert.equal(options.mode,'exclusive');const previous=tails.get(name)||Promise.resolve(),next=previous.then(fn);tails.set(name,next.catch(()=>{}));return next;}};};
async function harness(run){
 const errors=[],violations=[],virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://native-goal-typed.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole});
 const backing=dom.window.localStorage,session=dom.window.sessionStorage,state={me:copy(account),task:copy(task),goal:goal(task.id),quota:false,meGate:null,lost:false,visibility:'visible',exportRaw:null},calls=[],blobs=[],commits=[];
 const storage={get length(){return backing.length;},key:i=>backing.key(i),getItem:key=>backing.getItem(key),removeItem:key=>backing.removeItem(key),setItem(key,value){if(state.quota&&key.includes(':working:'))throw new dom.window.DOMException('Synthetic quota full','QuotaExceededError');backing.setItem(key,value);}};
 const original=JSON.stringify({form:oldForm(),pending:null});storage.setItem(legacyKey(task.id),original);
 session.setItem('ga-setup-csrf','synthetic-goal-csrf');session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 Object.defineProperty(dom.window,'localStorage',{configurable:true,value:storage});Object.defineProperty(dom.window.document,'visibilityState',{configurable:true,get:()=>state.visibility});Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 // Match Python JSON's actual signed-zero response bytes. JSON.stringify
 // would erase -0 before the real runtime-api Response.json() can decode it.
 const respond=(value,status=200)=>{const raw=typedRequestJSON(value);if(value===state.goal)state.lastGoalResponse=raw;return new Response(raw,{status,headers:{'Content-Type':'application/json'}});};
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET',path=url.pathname+url.search;calls.push({path,method,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),'synthetic-goal-csrf');
   if(path==='/api/me'){assert.equal(method,'GET');const identity=copy(state.me),gate=state.meGate;if(gate)await gate.promise;return respond(identity);}
   const base='/api/workspace/tasks/'+state.task.id;
   if(path===base+'/goal/export'){assert.equal(method,'GET');assert.ok(state.exportRaw);return new Response(state.exportRaw,{headers:{'Content-Type':'application/json'}});}
   if(path===base+'/goal'&&method==='GET')return respond(state.goal);
   if(path===base+'/goal/budget-display'&&method==='GET')return respond({detail:'目标计数展示暂不可用'},404);
   if(path===base+'/goal'&&method==='POST'){
    const outbox=Array.from({length:storage.length},(_,i)=>storage.key(i)).find(key=>key.startsWith('ga-native-goal-v132:')&&key.endsWith(':outbox'));
    assert.ok(outbox,'original typed request is durable before POST');assert.equal(JSON.parse(storage.getItem(outbox)).exact_body,options.body);
    if(state.lost)throw new TypeError('Synthetic request response lost');throw Error('This test never authorizes a successful formal goal mutation');
   }
   if(path===base+'/goal/requests/recover'&&method==='POST')return respond({version:state.goal.version,saved:false,abandoned:false});
   const ancillary=new Set([base+'/query-proposals',base+'/supporting-queries?limit=20',base+'/intelligent-plan',base+'/goal/acquisitions',base+'/experience']);
   if(ancillary.has(path)&&method==='GET')return respond({detail:'本合成传输不提供此独立资料清单'},503);
   throw Error('Unexpected synthetic route '+method+' '+path);
  }catch(error){if(!(state.lost&&method==='POST'&&path.endsWith('/goal')&&error instanceof TypeError))violations.push(error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},descriptors=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const [key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 const container=dom.window.document.getElementById('root'),gates=[];let root,guard;
 const createURL=Object.getOwnPropertyDescriptor(URL,'createObjectURL'),revokeURL=Object.getOwnPropertyDescriptor(URL,'revokeObjectURL'),anchorClick=dom.window.HTMLAnchorElement.prototype.click;
 Object.defineProperty(URL,'createObjectURL',{configurable:true,value:blob=>{blobs.push(blob);return 'blob:synthetic-goal';}});Object.defineProperty(URL,'revokeObjectURL',{configurable:true,value:()=>{}});dom.window.HTMLAnchorElement.prototype.click=function(){};
 const settle=async()=>{for(let i=0;i<16;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const gate=()=>{let release;const value={promise:new Promise(resolve=>{release=resolve;}),release:()=>release()};gates.push(value);return value;};
 try{
  const {createRoot}=await import('react-dom/client'),{Studio,setSession}=await component();setSession('synthetic-goal-csrf',account.workspace_id,account.user_id,account.username);
  function Btn({children,primary,ghost,icon,...props}){return React.createElement('button',{type:'button',...props},children);}
  function Probe(){useLayoutEffect(()=>{commits.push({task_id:state.task.id,text:container.textContent,values:[...container.querySelectorAll('input,textarea')].map(node=>node.value)});},[state.task.id]);return null;}
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Studio,{task:copy(state.task),account,Btn,locked:false,onChanged:async()=>{},onRegisterGuard:value=>{guard=value;}}),React.createElement(Probe))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const unmount=async()=>{if(root){const current=root;root=null;await act(async()=>current.unmount());await settle();}};
  const click=async label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(node,'actual button '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const edit=async(label,value)=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node,label);const prototype=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype,setter=Object.getOwnPropertyDescriptor(prototype,'value').set;await act(async()=>{setter.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();assert.equal(container.querySelector('[aria-label="'+label+'"]').value,value);};
  const snapshots=()=>Array.from({length:storage.length},(_,i)=>storage.key(i)).sort().map(key=>[key,storage.getItem(key)]),contains=text=>container.textContent.includes(text)||[...container.querySelectorAll('input,textarea')].some(node=>node.value.includes(text));
  await run({dom,container,state,storage,calls,blobs,commits,original,mount,unmount,render,settle,gate,click,edit,snapshots,contains,get guard(){return guard;}});await unmount();assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{gates.forEach(item=>item.release());if(root)await act(async()=>root.unmount());if(createURL)Object.defineProperty(URL,'createObjectURL',createURL);else delete URL.createObjectURL;if(revokeURL)Object.defineProperty(URL,'revokeObjectURL',revokeURL);else delete URL.revokeObjectURL;dom.window.HTMLAnchorElement.prototype.click=anchorClick;dom.window.close();for(const [key,descriptor]of Object.entries(descriptors)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}

test('actual Goal first identity and focus mismatch conceal private draft without deleting or sending',()=>harness(async h=>{
 h.state.meGate=h.gate();await h.mount();assert.equal(h.contains(oldForm().purpose),false);assert.equal(h.storage.getItem(legacyKey(task.id)),h.original);
 h.state.meGate.release();h.state.meGate=null;await h.settle();assert.equal(h.contains(oldForm().purpose),true);const before=h.snapshots();
 h.state.me={...account,user_id:'13210000-0000-4000-8000-000000000099'};await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();
 assert.equal(h.contains(oldForm().purpose),false);assert.deepEqual(h.snapshots(),before);assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));

test('actual Goal quota keeps incomplete raw text through identity masking and explicit durable recovery',()=>harness(async h=>{
 await h.mount();h.state.quota=true;await h.edit('目标期望值','1.');await assert.rejects(h.guard(),/未持久保存/);const event=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
 h.state.meGate=h.gate();await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.equal(h.container.querySelector('[aria-label="目标期望值"]'),null);
 h.state.meGate.release();h.state.meGate=null;await h.settle();assert.equal(h.container.querySelector('[aria-label="目标期望值"]').value,'1.');
 h.state.quota=false;await h.click('另存本页目标稿并核对原请求');await h.guard();await h.unmount();await h.mount();assert.equal(h.container.querySelector('[aria-label="目标期望值"]').value,'1.');assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));

test('actual Goal observation hours retain raw text across remount and reject a rounded fractional hour before POST',()=>harness(async h=>{
 await h.mount();const source=h.container.querySelector('[aria-label="目标资料来源"]');assert.ok(source);
 const setter=Object.getOwnPropertyDescriptor(h.dom.window.HTMLSelectElement.prototype,'value').set;
 await act(async()=>{setter.call(source,'observations');source.dispatchEvent(new h.dom.window.Event('change',{bubbles:true}));});await h.settle();
 for(const raw of ['-','1.','24.0000000000000001']){
  const hours=h.container.querySelector('[aria-label="目标资料有效小时数"]');assert.equal(hours.type,'text');assert.equal(hours.inputMode,'numeric');
  await h.edit('目标资料有效小时数',raw);await h.unmount();await h.mount();assert.equal(h.container.querySelector('[aria-label="目标资料有效小时数"]').value,raw);
 }
 await h.click('确认原业务目标');assert.equal(h.calls.filter(call=>call.method==='POST').length,0);assert.equal(h.container.querySelector('[aria-label="目标资料有效小时数"]').value,'24.0000000000000001');
}));

for(const raw of ['-0.0','1e21'])test('actual Goal '+raw+' lost response cold-restores only original request and probe bytes',()=>harness(async h=>{
 await h.mount();await h.edit('目标期望值',raw);h.state.lost=true;await h.click('确认原业务目标');
 const first=h.calls.find(call=>call.method==='POST'&&call.path.endsWith('/goal'));assert.ok(first);assert.ok(first.body.includes('"expected":'+raw));
 const key=Array.from({length:h.storage.length},(_,i)=>h.storage.key(i)).find(value=>value.startsWith('ga-native-goal-v132:')&&value.endsWith(':outbox')),original=h.storage.getItem(key);assert.ok(original);
 await h.unmount();await h.mount();assert.equal(h.storage.getItem(key),original);assert.equal(h.calls.filter(call=>call.method==='POST'&&call.path.endsWith('/goal')).length,1);
 await h.click('核对原请求是否已保存');const probe=h.calls.find(call=>call.path.endsWith('/goal/requests/recover'));assert.ok(probe.body.includes('"original_request":'+first.body));assert.equal(h.storage.getItem(key),original);
}));

test('actual Goal scope switch is concealed in its first committed layout before passive reset',()=>harness(async h=>{
 await h.mount();assert.equal(h.contains(oldForm().purpose),true);h.state.task={...task,id:'13210000-0000-4000-8000-000000000088'};h.state.goal=goal(h.state.task.id);h.state.meGate=h.gate();await h.render();
 const first=h.commits.find(item=>item.task_id===h.state.task.id);assert.ok(first);assert.ok(!first.text.includes(oldForm().purpose)&&!first.values.includes(oldForm().purpose));assert.equal(h.storage.getItem(legacyKey(task.id)),h.original);
 h.state.meGate.release();h.state.meGate=null;await h.settle();assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));

test('actual saved query negative zero stays visible and download uses original Response.text bytes',()=>harness(async h=>{
 const spec={purpose:'已确认的原数值目标',required_fields:['reading'],observation_mode:'existing_only',criteria:[{id:'query-zero',kind:'query_value',field:'value',operator:'eq',expected:-0.0}],data_query:{tool_steps:[{id:'one',capability_id:'aggregate-fixed',input_from:'evidence',parameters:{field:'reading',operation:'min'}}]}};
 h.state.goal={...goal(task.id),goal_confirmed:true,goal:spec,acceptance:{reason:'原验收已保存',at:'2026-10-01T00:00:00Z'},can_manage:false};
 h.state.exportRaw='{"schema":"native-business-goal-export-v1","goal":{"task_id":"'+task.id+'","values":[-0.0,1.0,1e21]},"physical_state_verified":false,"learning_auto_published":false,"production_ready":false}';
 await h.mount();assert.ok(h.state.lastGoalResponse.includes('"expected":-0.0'),'synthetic server actually supplied the signed-zero JSON token');const saved=h.container.querySelector('[aria-label="已固定的原目标查询"]');assert.ok(saved);assert.ok(saved.textContent.includes('-0.0'));
 await h.click('导出原目标与验收依据');assert.equal(h.blobs.length,1);assert.equal(await h.blobs[0].text(),h.state.exportRaw);assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));

function confirmedEvidence(h,{archive=12,observations=13,basis='a'.repeat(64)}={}){
 const rows=Array.from({length:archive+observations},(_,index)=>({source:index<archive?'resource_entity':'observation',attributes:{reading:index+1},observed_at:'2026-10-30T01:00:00Z'}));
 h.state.goal={...goal(h.state.task.id),goal_confirmed:true,goal:{purpose:'完整核对当前冲突依据',required_fields:['reading'],observation_mode:'allow_new_observation',criteria:[],subgoals:[],evidence:{source:'either',minimum_verification:'unverified_extraction',time:{mode:'latest'}},data_query:null,relation_query:null},check:{checked_at:'2026-10-30T02:00:00Z',evaluation:{basis_hash:basis,checks:[],flow_finished:false,data_received:false,ready_for_acceptance:false},evidence:{evidence:rows.slice(0,archive),observation_evidence:rows.slice(archive)},gaps:[]},reviewable_records:[]};
 return rows;
}
const evidenceArticles=h=>[...h.container.querySelectorAll('article[aria-label^="原目标依据"]')];
test('V258 actual Goal shows all twenty-five returned archive and observation evidence with read-only pages',()=>harness(async h=>{
 confirmedEvidence(h);await h.mount();await h.edit('业务目标操作理由','完整浏览依据时保留的原操作理由');const before=h.snapshots(),seen=new Set();
 const remember=()=>{for(const node of evidenceArticles(h)){seen.add(node.getAttribute('aria-label'));assert.match(node.textContent,/这里只确认原目标依据，不直接认定现场正常/);}};
 const panel=()=>h.container.querySelector('[aria-label="原目标依据完整浏览"]');assert.ok(panel());assert.match(panel().textContent,/本次核查已返回25条依据，当前显示第1—10条；第1页，共3页/);assert.equal(evidenceArticles(h).length,10);assert.ok(evidenceArticles(h).every(node=>node.textContent.includes('已有业务档案')));remember();
 await h.click('下一页目标依据');assert.match(panel().textContent,/当前显示第11—20条；第2页，共3页/);assert.equal(evidenceArticles(h).filter(node=>node.textContent.includes('已有业务档案')).length,2);assert.equal(evidenceArticles(h).filter(node=>node.textContent.includes('已登记观测依据')).length,8);remember();
 await h.click('下一页目标依据');assert.match(panel().textContent,/当前显示第21—25条；第3页，共3页/);assert.equal(evidenceArticles(h).length,5);remember();assert.equal(seen.size,25);assert.equal([...h.container.querySelectorAll('button')].find(node=>node.textContent==='下一页目标依据').disabled,true);
 await h.click('上一页目标依据');assert.equal(evidenceArticles(h)[0].getAttribute('aria-label'),'原目标依据11');assert.equal(h.container.querySelector('[aria-label="业务目标操作理由"]').value,'完整浏览依据时保留的原操作理由');assert.deepEqual(h.snapshots(),before);await h.guard();assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));
test('V258 actual Goal preserves evidence page on same-basis refresh and resets for a changed saved basis',()=>harness(async h=>{
 confirmedEvidence(h);await h.mount();await h.click('下一页目标依据');assert.equal(evidenceArticles(h)[0].getAttribute('aria-label'),'原目标依据11');
 h.state.goal.check.checked_at='2026-10-30T02:01:00Z';const old=h.state.goal.check.evidence.evidence[0];h.state.goal.check.evidence.evidence[0]={observed_at:old.observed_at,attributes:old.attributes,source:old.source};await h.click('更新目标核查进度');assert.equal(evidenceArticles(h)[0].getAttribute('aria-label'),'原目标依据11','fresh read time and JSON key order do not move the human review position');
 h.state.goal.check.evaluation.basis_hash='b'.repeat(64);await h.click('更新目标核查进度');assert.equal(evidenceArticles(h)[0].getAttribute('aria-label'),'原目标依据1');assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));
test('V258 actual Goal resets page for changed evidence content or task and explicitly reports an empty returned set',()=>harness(async h=>{
 confirmedEvidence(h);await h.mount();await h.click('下一页目标依据');h.state.goal.check.evidence.observation_evidence[12].attributes.reading=999;await h.click('更新目标核查进度');assert.equal(evidenceArticles(h)[0].getAttribute('aria-label'),'原目标依据1');await h.click('下一页目标依据');
 h.state.task={...task,id:'13210000-0000-4000-8000-000000000089'};confirmedEvidence(h,{archive:0,observations:0});await h.render();const panel=h.container.querySelector('[aria-label="原目标依据完整浏览"]');assert.ok(panel);assert.match(panel.textContent,/本次核查已返回0条依据/);assert.match(panel.textContent,/本次核查没有返回可展示的依据/);assert.equal(evidenceArticles(h).length,0);assert.equal(panel.querySelector('button'),null);assert.equal(h.storage.getItem(legacyKey(task.id))!==null,true);assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));

test('a verified simulated continuation never claims the original graph reached its end',()=>harness(async h=>{
 confirmedEvidence(h,{archive:1,observations:0});h.state.goal.check.evaluation={...h.state.goal.check.evaluation,flow_finished:true,formal_continuation:{formal_completion_verified:true,simulation_only:true,test_only:true}};
 await h.mount();assert.match(h.container.textContent,/原分析已停用，续段步骤已核对/);assert.match(h.container.textContent,/仅用于模拟或技术测试/);assert.doesNotMatch(h.container.textContent,/原流程步骤：已到达结束节点/);
}));

test('accepted layered continuation uses frozen acceptance while labeling the older check historical',()=>harness(async h=>{
 confirmedEvidence(h,{archive:1,observations:0});
 h.state.goal.check.evaluation.checks=[{id:'human',kind:'human_confirmation',description:'最终人工验收',passed:false}];
 h.state.goal.check_historical=true;h.state.goal.can_manage=false;
 h.state.goal.approval_completion={accepted:true,acceptance:{basis:{formal_continuation:{simulation_only:true,test_only:true,original_node_completed:false}}}};
 await h.mount();assert.match(h.container.textContent,/历史目标核对/);assert.match(h.container.textContent,/最终人工验收：已保存人工验收/);
 assert.match(h.container.textContent,/已保存独立验收记录/);assert.match(h.container.textContent,/原分析已停用，续段步骤已核对/);
 assert.match(h.container.textContent,/仅用于模拟或技术测试/);assert.doesNotMatch(h.container.textContent,/原流程步骤：仍需处理|等待最终人工验收|原流程步骤：已到达结束节点/);
 assert.equal(h.container.querySelector('[aria-label="业务目标操作理由"]'),null);assert.equal(h.calls.filter(call=>call.method==='POST').length,0);
}));
