// Actual ReactDOM + component + controller + runtime-api + browser storage.
// Only HTTP responses are synthetic. Not browser, API/PG or field acceptance.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,webcrypto,randomUUID} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){if(!bundle)bundle=(async()=>{
 temporary=await mkdtemp(join(tmpdir(),'simulation-evolution-react-v176-'));const entry=join(temporary,'entry.mjs');
 await writeFile(entry,'export {default as Component} from '+JSON.stringify(join(project,'src/SimulationEvolutionV176.jsx'))+';\nexport * from '+JSON.stringify(join(project,'src/simulation-evolution-ui-v176.mjs'))+';\nexport {json,setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
 const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
 const output=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(output);const path=join(temporary,'component.mjs');await writeFile(path,output.code);return import(pathToFileURL(path).href);
})();return bundle;}
const id=n=>'17630000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value)),sha=raw=>createHash('sha256').update(raw).digest('hex');
const mark={simulation:true,origin_kind:'simulated_equipment',field_verified:false,test_only:true,production_activation:false};
class NoResources extends ResourceLoader{fetch(){throw Error('external resource forbidden in synthetic DOM');}}
function locks(){const tails=new Map();return {request(key,_options,run){const next=(tails.get(key)||Promise.resolve()).then(run),tail=next.then(()=>{},()=>{});tails.set(key,tail);return next.finally(()=>{if(tails.get(key)===tail)tails.delete(key);});}};}

async function harness(run){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://simulation-evolution.invalid/',pretendToBeVisual:true,resources:new NoResources()});
 const storage=dom.window.localStorage,scope={actor_id:id(1),workspace_id:id(2),instance_id:randomUUID(),role:'operator'},account={user_id:scope.actor_id,workspace_id:scope.workspace_id,role:scope.role,username:'模拟机制标注员'},environment=id(4),observation=id(5),sampleID=id(6),calls=[],violations=[],errors=[];
 const sample={id:sampleID,version:1,actor_id:scope.actor_id,definition_id:id(7),status:'received',invocation_id:id(8),observation_id:observation,task_id:null,signal_id:null,receipt_evidence_id:id(9),created_at:'2026-10-23T00:00:02Z',received_at:'2026-10-23T00:00:03Z',issue:null,recovery_status:null,recovery_job_id:null,...mark};
 const detail={...sample,origin:{schema:'simulation-origin-v176',environment_id:environment,instance_id:scope.instance_id,...mark},attributes:{temperature_c:38},field_units:{temperature_c:'°C'},observed_at:'2026-10-23T00:00:01Z',processing_status:'processed',time_semantics:'simulator_sample_time',source_time_verified:false,extracted:{attributes:{temperature_c:38},anomalies:[{field:'temperature_c',value:38,rule_id:'warm',severity:'warning'}],method:'registered_numeric_rules'}};
 const index={environments:[{id:environment,version:1,schema:'simulation-environment-v176',instance_id:scope.instance_id,release_id:id(10),note:'独立模拟联调环境',...mark}],definitions:[{id:sample.definition_id,environment_id:environment,fields:['temperature_c']}],samples:[sample],daily:[]};
 const state={feedback:[],commands:new Map(),drop:false,recoveryVisible:true};let actual,root;
 const response=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
 const record=(body,version)=>({id:id(11),workspace_id:scope.workspace_id,actor_id:scope.actor_id,version,environment_id:environment,observation_id:observation,record_hash:'a'.repeat(64),...mark,data:{schema:'simulation-feedback-record-v176',...mark,label:body.label,reason:body.reason,basis:{schema:'simulation-feedback-basis-v176',...mark,environment_id:environment,observation_id:observation,origin:copy(detail.origin),field:'temperature_c',value:38,unit:'°C',observed_at:detail.observed_at,baseline_manifest:{specification:{rules:[{field:'temperature_c',threshold:35,operator:'gt'}]}}}}});
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({path:url.pathname,query:url.search,method,body:options.body});try{
  assert.equal(url.origin,dom.window.location.origin);assert.equal(options.credentials,'same-origin');assert.equal(new Headers(options.headers).get('X-Workspace-ID'),scope.workspace_id);
  if(url.pathname==='/api/me')return response(account);
  if(method==='GET'&&url.pathname==='/api/workspace/simulation-v176/feedback')return response({...mark,rows:copy(state.feedback),limit:100,observation_id:url.searchParams.get('observation_id'),complete:true});
  if(method==='GET'&&url.pathname==='/api/workspace/simulation-v176/samples/'+sampleID)return response(detail);
  if(method==='GET'&&url.pathname.includes('/evolution/requests/')){const requestID=url.pathname.split('/').at(-1),pending=JSON.parse(storage.getItem(actual.evolutionKeyV176(scope)+':pending'));assert.ok(pending);assert.equal(url.searchParams.get('expected_sha256'),sha(pending.raw));return response(state.recoveryVisible&&state.commands.has(requestID)?copy(state.commands.get(requestID)):{...mark,found:false,request_id:requestID});}
  if(method==='POST'&&url.pathname==='/api/workspace/simulation-v176/feedback'){
   assert.equal(new Headers(options.headers).get('X-CSRF-Token'),'synthetic-evolution-csrf');const body=JSON.parse(options.body),pending=JSON.parse(storage.getItem(actual.evolutionKeyV176(scope)+':pending'));assert.equal(pending.raw,options.body);assert.equal(pending.sha256,sha(options.body));assert.equal(storage.getItem(actual.evolutionKeyV176(scope)+':original:'+body.request_id),JSON.stringify(pending));assert.equal(body.expected_version,state.feedback[0]?.version||0);
   const result=record(body,body.expected_version+1);state.feedback=[result];state.commands.set(body.request_id,{...mark,found:true,request_id:body.request_id,operation:'feedback.save',target_id:result.id,body_sha256:sha(options.body),result});if(state.drop)throw Object.assign(Error('synthetic lost successful response'),{syntheticDrop:true});return response(result);
  }
  throw Error('unexpected synthetic HTTP '+method+' '+url.pathname);
 }catch(error){if(!error.syntheticDrop)violations.push(error.message);throw error;}};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:storage,sessionStorage:dom.window.sessionStorage,crypto:webcrypto,fetch,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,IS_REACT_ACT_ENVIRONMENT:true};
 const prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 try{
  for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
  const {createRoot}=await import('react-dom/client');actual=await component();assert.equal(actual.BundledReact,React);actual.setSession('synthetic-evolution-csrf',scope.workspace_id,scope.actor_id,account.username);const container=document.getElementById('root');
  const settle=async()=>{for(let i=0;i<5;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:e=>errors.push(e),onRecoverableError:e=>errors.push(e)});await act(async()=>root.render(React.createElement(actual.Component,{api:actual.json,scope,index})));await settle();};
  const remount=async()=>{await act(async()=>root.unmount());await mount();};
  const button=text=>[...container.querySelectorAll('button')].find(node=>typeof text==='function'?text(node.textContent.trim()):node.textContent.trim()===text);
  const wait=async(predicate,label)=>{for(let i=0;i<100;i++){if(predicate())return;await settle();}assert.fail('timeout '+label+'; '+container.textContent.slice(-1000));};
  const click=async text=>{const node=button(text);assert.ok(node,'actual button '+text);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const reason=()=>[...container.querySelectorAll('label')].find(node=>node.textContent.startsWith('判断原因'))?.querySelector('textarea');
  const input=async value=>{const node=reason();assert.ok(node);assert.equal(node.closest('fieldset').disabled,false);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();};
  const radio=async()=>{await wait(()=>container.querySelector('input[type=radio][value=should_alert]'),'sample detail and feedback loaded');const node=container.querySelector('input[type=radio][value=should_alert]');await act(async()=>node.click());await settle();};
  await mount();await wait(()=>button(text=>text.startsWith('取样 1'))?.disabled===false,'environment loaded');
  await run({state,calls,storage,scope,actual,container,button,click,reason,input,radio,wait,settle,remount,record});assert.deepEqual(violations,[]);assert.deepEqual(errors,[]);
 }finally{try{if(root)await act(async()=>root.unmount());}finally{dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}}
}

test('actual feedback component restores human cause and saves only after frozen confirmation',async()=>{
 await harness(async({calls,storage,scope,actual,click,reason,input,radio,wait,remount,state,container})=>{
  await click(text=>text.startsWith('取样 1'));await radio();await input('模拟取样高于原阈值，应核查提醒机制。');assert.equal(calls.filter(row=>row.method==='POST').length,0);const drafts=new actual.SimulationEvolutionClientV176(actual.json,scope).readDrafts();assert.equal(drafts[0].value.feedbackDrafts[id(5)].reason,'模拟取样高于原阈值，应核查提醒机制。');
  await remount();await click('继续核对上次样本');await wait(()=>reason()?.value==='模拟取样高于原阈值，应核查提醒机制。','human draft restored');await click('核对并保存正式判断');assert.equal(calls.filter(row=>row.method==='POST').length,0);await click('确认并提交');await wait(()=>state.feedback.length===1&&!storage.getItem(actual.evolutionKeyV176(scope)+':pending'),'server feedback saved and original settled');assert.equal(calls.filter(row=>row.method==='POST').length,1);assert.ok(container.textContent.includes('服务器已保存的判断'));assert.ok(!storage.getItem(actual.evolutionKeyV176(scope)+':pending'));
 });
});
test('actual lost-response flow remounts and resolves original GET without another POST',async()=>{
 await harness(async({state,calls,storage,scope,actual,click,input,radio,wait,remount,reason})=>{
  await click(text=>text.startsWith('取样 1'));await radio();await input('丢回应后也必须保留的人工原因。');state.drop=true;state.recoveryVisible=false;await click('核对并保存正式判断');await click('确认并提交');const key=actual.evolutionKeyV176(scope)+':pending';await wait(()=>!!storage.getItem(key)&&calls.filter(row=>row.method==='POST').length===1,'original request retained after lost response');const original=storage.getItem(key),count=calls.filter(row=>row.method==='POST').length;
  await remount();assert.equal(storage.getItem(key),original);assert.equal(calls.filter(row=>row.method==='POST').length,count);await click('继续核对上次样本');assert.equal(reason().value,'丢回应后也必须保留的人工原因。');state.recoveryVisible=true;await click('核对原请求');await wait(()=>storage.getItem(key)===null,'original GET recovered');assert.equal(calls.filter(row=>row.method==='POST').length,count);assert.equal(state.commands.size,1);
 });
});
test('actual stale feedback confirmation preserves text and refuses changed CAS before POST',async()=>{
 await harness(async({state,calls,click,input,radio,wait,reason,record,container})=>{
  await click(text=>text.startsWith('取样 1'));await radio();await input('当前页面保留的判断原因。');await click('核对并保存正式判断');state.feedback=[record({label:'uncertain',reason:'另一页已保存的新判断'},1)];await click('确认并提交');await wait(()=>container.textContent.includes('服务器反馈版本已变化'),'stale CAS visible');assert.equal(calls.filter(row=>row.method==='POST').length,0);assert.equal(reason().value,'当前页面保留的判断原因。');
 });
});
