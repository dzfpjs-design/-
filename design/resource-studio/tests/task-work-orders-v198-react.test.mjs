// Actual JSX, ReactDOM and StrictMode with synthetic authenticated server IO.
// This is not browser, PG, device action or independent acceptance evidence.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const id=n=>'19800000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=v=>JSON.parse(JSON.stringify(v));
const scope={actor_id:id(1),workspace_id:id(2),task_id:id(3)},base='/workspace/work-orders-v198';
const flags={execution_completed:false,independent_acceptance:false,world_state_verified:false};
let temporary,compiled;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'task-work-orders-v198-'));const old=process.env.NODE_ENV;let result;
  try{process.env.NODE_ENV='development';result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/TaskWorkOrdersV198.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});}finally{if(old===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=old;}
  const entry=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(entry);const target=join(temporary,'TaskWorkOrdersV198.mjs');await writeFile(target,entry.code);return (await import(pathToFileURL(target).href)).default;
 })();return compiled;
}
class Memory{constructor(){this.map=new Map();this.fail=false;}getItem(k){if(this.fail)throw Error('synthetic failed storage');return this.map.get(k)??null;}setItem(k,v){if(this.fail)throw Error('synthetic failed storage');this.map.set(k,String(v));}removeItem(k){if(this.fail)throw Error('synthetic failed storage');this.map.delete(k);}}
function scene(){return {schema:'workflow-work-order-view-v198',id:id(4),batch_id:id(5),parent_task_id:scope.task_id,title:'核对受损样地',description:'核对登记样地并记录未观测区域。',expected_result:'提交样地核对说明与待补充事项。',assignee_id:scope.actor_id,assignee_name:'样地核对员',due_at:'2026-10-25T18:00:00+08:00',simulation_only:true,scope:'human_work_orders',status:'assigned',version:1,note:'',updated_at:'2026-10-24T10:00:00+08:00',history:[{version:1,status:'assigned',operation:'assigned',note:'',actor_id:id(6),actor_name:'任务负责人',at:'2026-10-24T10:00:00+08:00',request_id:null}],...flags,permissions:{progress:true,submit:true,return:false,cancel:false}};}
async function harness(){
 assert.equal(process.env.NODE_ENV,'development','Use one React/ReactDOM development runtime');
 const errors=[],external=[],calls=[],saved=new Map(),storage=new Memory(),server={item:scene(),loseResponse:false},callbacks=[];
 const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 class NoResources extends ResourceLoader{fetch(url){external.push(String(url));throw Error('External resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-work-orders.invalid/',resources:new NoResources(),virtualConsole});
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_name,_options,run)=>run()}});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,MutationObserver:dom.window.MutationObserver,sessionStorage:dom.window.sessionStorage,localStorage:storage,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true,fetch:()=>{throw Error('Unexpected real HTTP');}};
 const previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 sessionStorage.setItem('ga-setup-csrf-user',scope.actor_id);sessionStorage.setItem('ga-setup-csrf-workspace',scope.workspace_id);sessionStorage.setItem('ga-setup-csrf','synthetic-csrf');
 const api=async(path,options={})=>{
  calls.push({path,method:options.method||'GET',body:options.body});assert.equal(options.headers?.['X-Workspace-ID'],scope.workspace_id);assert.equal(options.headers?.['X-CSRF-Token'],'synthetic-csrf');
  if(path==='/me'){if(server.delayFirstIdentity&&!server.oldIdentityCaptured){server.oldIdentityCaptured=true;return new Promise((_resolve,reject)=>{server.rejectOldIdentity=()=>{const error=Error('synthetic old mount denied');error.status=403;reject(error);};});}return {user_id:scope.actor_id,workspace_id:scope.workspace_id,role:'operator'};}
  const url=new URL('https://synthetic.invalid'+path);
  if(url.pathname.startsWith(base+'/requests/')){const nonce=url.pathname.split('/').at(-1);return {schema:'workflow-work-order-original-result-v198',request_id:nonce,found:saved.has(nonce),body_sha256:url.searchParams.get('expected_sha256'),result:copy(saved.get(nonce)||null)};}
  if(url.pathname===base){assert.equal(url.searchParams.get('parent_task_id'),scope.task_id);return {schema:'workflow-work-order-list-v198',scope:'human_work_orders',mode:url.searchParams.get('mode'),items:[copy(server.item)],...flags};}
  if((options.method||'GET')==='GET'&&path===base+'/'+id(4))return copy(server.item);
  assert.equal(options.method,'POST');assert.equal(path,base+'/'+id(4)+'/submit');
  const outbox=[...storage.map].find(([key])=>key.endsWith(':outbox'));assert.ok(outbox);const pending=JSON.parse(outbox[1]);assert.equal(pending.raw,options.body);assert.equal(pending.sha256,[...new Uint8Array(await webcrypto.subtle.digest('SHA-256',new TextEncoder().encode(options.body)))].map(n=>n.toString(16).padStart(2,'0')).join(''));
  const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['expected_version','note','request_id']);assert.equal(body.expected_version,server.item.version);
  const item=server.item;item.version++;item.status='completion_reported';item.note=body.note;item.updated_at='2026-10-24T11:00:00+08:00';item.permissions={progress:false,submit:false,return:false,cancel:false};item.history.push({version:item.version,status:item.status,operation:'submit',note:body.note,actor_id:scope.actor_id,actor_name:'样地核对员',at:item.updated_at,request_id:body.request_id});const output=copy(item);delete output.permissions;
  const result={schema:'workflow-work-order-result-v198',request_id:body.request_id,item:output,...flags};saved.set(body.request_id,result);if(server.loseResponse)throw Error('synthetic committed response lost');return copy(result);
 };
 const container=document.getElementById('root');let root,currentGuard;
 const register=(guard,previousGuard)=>{if(guard)currentGuard=guard;else if(!previousGuard||currentGuard===previousGuard)currentGuard=null;};
 const until=async(predicate,label)=>{for(let i=0;i<90;i++){if(predicate())return;await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});}assert.deepEqual(errors,[]);assert.ok(predicate(),label);};
 const mount=async()=>{const WorkOrders=await component(),{createRoot}=await import('react-dom/client');root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(WorkOrders,{task:{id:scope.task_id},api,actor:{user_id:scope.actor_id},workspace:{workspace_id:scope.workspace_id},onRegisterGuard:register,onSaved:value=>callbacks.push(value)}))));await until(()=>container.textContent.includes('核对受损样地'),'authenticated list ready');};
 const click=async(label,{includes=false}={})=>{const button=[...container.querySelectorAll('button')].find(row=>includes?row.textContent.includes(label):row.textContent===label);assert.ok(button,'actual button '+label);assert.equal(button.disabled,false);await act(async()=>button.click());};
 const edit=async value=>{const input=container.querySelector('textarea[aria-label="工作单办理说明"]');assert.ok(input);assert.equal(input.disabled,false);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,value);input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});};
 return {dom,container,errors,external,calls,storage,server,callbacks,mount,click,edit,until,guard:()=>currentGuard?.(),unmount:async()=>{if(root){await act(async()=>root.unmount());root=null;}},close:async()=>{if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,{configurable:true,...descriptor});else delete globalThis[key];}}};
}

test('actual StrictMode work order submits once, restores original request after remount, and never claims independent acceptance',async()=>{
 const h=await harness();try{
  await h.mount();assert.equal(h.calls.filter(row=>row.method==='POST').length,0,'StrictMode mounting is GET only');await h.click('核对受损样地',{includes:true});await h.until(()=>!!h.container.querySelector('textarea'),'actual detail loaded');
  const text=h.container.textContent;for(const expected of ['办理说明','预期产出','样地核对员','北京时间','仅工程模拟（SIM）'])assert.ok(text.includes(expected),expected);for(const hidden of [id(1),id(3),id(4),'workflow-work-order-view-v198'])assert.equal(text.includes(hidden),false);
  assert.equal([...h.container.querySelectorAll('button')].some(row=>row.textContent==='退回补充'),false,'server permissions hide unauthorized commands');
  await h.edit('人工核对的完整说明\n未知范围仍需补充');assert.equal(h.guard(),true);h.server.loseResponse=true;await h.click('提交办理结果');await h.until(()=>h.container.textContent.includes('原请求尚待核对'),'lost response remains original pending');
  assert.equal(h.calls.filter(row=>row.method==='POST').length,1);assert.equal(h.guard(),true,'durable pending may leave safely');const nonce=JSON.parse(h.calls.find(row=>row.method==='POST').body).request_id;
  await h.unmount();await h.mount();await h.until(()=>h.container.textContent.includes('办理结果已保存并核对'),'saved original recovered on mount');
  assert.equal(h.calls.filter(row=>row.method==='POST').length,1);assert.ok(h.calls.some(row=>row.path.includes('/requests/'+nonce+'?')));assert.equal(h.container.querySelector('textarea').value,'人工核对的完整说明\n未知范围仍需补充');assert.ok(h.container.textContent.includes('尚不表示独立验收通过或实际动作已完成'));assert.equal(h.callbacks.at(-1).kind,'work_order');
  assert.ok(h.container.textContent.includes('查看办理历史（2条）'));assert.ok(h.container.textContent.includes('人工核对的完整说明'));assert.deepEqual(h.errors,[]);assert.deepEqual(h.external,[]);
 }finally{await h.close();}
});

test('actual focus identity change hides private title and local note without writes or extra commands',async()=>{
 const h=await harness();try{
  await h.mount();await h.click('核对受损样地',{includes:true});await h.until(()=>!!h.container.querySelector('textarea'),'detail ready');await h.edit('仅原账号的未提交说明');const before=[...h.storage.map];
  await act(async()=>{sessionStorage.setItem('ga-setup-csrf-user',id(99));h.dom.window.dispatchEvent(new h.dom.window.Event('focus'));});await h.until(()=>h.container.textContent.includes('原工作单与输入已隐藏'),'old account content concealed');
  assert.equal(h.container.textContent.includes('核对受损样地'),false);assert.equal(h.container.textContent.includes('仅原账号的未提交说明'),false);assert.equal(h.container.querySelector('textarea'),null);assert.deepEqual([...h.storage.map],before);assert.equal(h.calls.filter(row=>row.method==='POST').length,0);assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('actual failed local storage keeps typed note, blocks submit and navigation until protected',async()=>{
 const h=await harness();try{
  await h.mount();await h.click('核对受损样地',{includes:true});await h.until(()=>!!h.container.querySelector('textarea'),'detail ready');h.storage.fail=true;await h.edit('存储失败也不能丢掉这段办理说明');
  assert.equal(h.container.querySelector('textarea').value,'存储失败也不能丢掉这段办理说明');await act(async()=>assert.equal(h.guard(),false));assert.ok(h.container.textContent.includes('本机输入尚未安全保存'));assert.equal([...h.container.querySelectorAll('button')].find(row=>row.textContent==='提交办理结果').disabled,true);assert.equal(h.calls.filter(row=>row.method==='POST').length,0);
  h.storage.fail=false;await h.click('重试本机保存');assert.equal(h.guard(),true);assert.equal(h.container.querySelector('textarea').value,'存储失败也不能丢掉这段办理说明');assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});

test('a delayed 403 from the disposed StrictMode mount cannot conceal the current authorized mount',async()=>{
 const h=await harness();try{
  h.server.delayFirstIdentity=true;await h.mount();assert.equal(typeof h.server.rejectOldIdentity,'function');assert.ok(h.container.textContent.includes('核对受损样地'));
  const before=[...h.storage.map];await act(async()=>{h.server.rejectOldIdentity();await new Promise(resolve=>setImmediate(resolve));});
  assert.ok(h.container.textContent.includes('核对受损样地'));assert.equal(h.container.textContent.includes('原工作单与输入已隐藏'),false);assert.equal(h.container.textContent.includes('synthetic old mount denied'),false);assert.deepEqual([...h.storage.map],before);assert.equal(h.calls.filter(row=>row.method==='POST').length,0);
  await h.click('核对受损样地',{includes:true});await h.until(()=>!!h.container.querySelector('textarea'),'current mount can continue authorized reads');assert.deepEqual(h.errors,[]);
 }finally{await h.close();}
});
