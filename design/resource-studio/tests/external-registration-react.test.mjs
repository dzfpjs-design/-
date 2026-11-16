import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
process.env.NODE_ENV='development';
const {default:React,act}=await import('react');

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let folder,compiled;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  folder=await mkdtemp(join(tmpdir(),'external-registration-'));const reactURL=pathToFileURL(join(project,'node_modules/react/index.js')).href;
  const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/ExternalCapabilityRegistration.jsx'),write:false,minify:false,rollupOptions:{external:id=>id==='react'||id===reactURL,output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?reactURL:id}}}});
  process.env.NODE_ENV='development';const file=join(folder,'component.mjs');await writeFile(file,result.output.find(r=>r.type==='chunk'&&r.isEntry).code);return(await import(pathToFileURL(file).href)).default;
 })();return compiled;
}
const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
const account={user_id:'u1',workspace_id:'w1',role:'admin'};

async function harness(run){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true}),old=new Map();
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true})){
  old.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 }
 let root;
 try{
  const C=await component(),{createRoot}=await import('react-dom/client');root=createRoot(document.querySelector('#root'));
  const h={dom,C,root,button:label=>[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===label),input:label=>document.querySelector(`[aria-label="${label}"]`),
   async text(label,value){const el=this.input(label);assert.ok(el,label);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});},
   async tickContaining(label){const el=[...document.querySelectorAll('label')].find(x=>x.textContent.includes(label))?.querySelector('input[type="checkbox"]');assert.ok(el,label);await act(async()=>el.click());},
   async click(label){const el=this.button(label);assert.ok(el,label);assert.equal(el.disabled,false,label);await act(async()=>{el.click();await Promise.resolve();await Promise.resolve();});},
   async mount(props){await act(async()=>{root.render(React.createElement(C,{account,kind:'tool',Btn,...props}));await Promise.resolve();});},
   async remount(props){await act(async()=>root.unmount());root=createRoot(document.querySelector('#root'));this.root=root;await this.mount(props);},
   async fill(){await this.text('能力说明','天气查询');await this.text('负责人','业务部门');await this.text('英文标识','weather');await this.text('服务地址','https://example.org/api');
    await this.tickContaining('业务智能体');await this.tickContaining('我确认服务只读取或分析');await this.tickContaining('我确认允许所选智能体');}};
  await run(h);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}

function fakeBackend({loseCreate=false}={}){
 let row=null;const calls=[];
 const request=async(path,options={})=>{
  calls.push({path,workspace:options.headers?.['X-Workspace-ID']});
  if(path.startsWith('/external-capability-registrations/lookup')){if(!row){const e=Error('not found');e.status=404;throw e;}return structuredClone(row);}
  if(path==='/external-capability-registrations/attempts'){
   const attempt=JSON.parse(options.body);const data=attempt.manifest;row={id:'cap-1',workspace_id:'w1',namespace:data.namespace,name:data.name,release_version:data.version,kind:data.kind,data,version:1,status:'draft'};
   if(loseCreate){loseCreate=false;throw Error('连接中断');}return {request_id:attempt.request_id,state:'created',record:structuredClone(row)};
  }
  const body=JSON.parse(options.body);assert.equal(body.expected_version,row.version);
  if(path.endsWith('/validate')){row.status='validated';row.version++;return structuredClone(row);}
  if(path.endsWith('/publish')){row.status='published';row.version++;return structuredClone(row);}
  throw Error('unexpected request');
 };
 return {request,calls,get row(){return row;}};
}

test('saved draft survives remount and can be validated and published through the real contract',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const server=fakeBackend();let guard;await h.mount({request:server.request,onRegisterGuard:fn=>{guard=fn||guard;}});await h.fill();
 await h.click('保存登记草稿');assert.match(document.body.textContent,/登记草稿已保存在当前工作区/);assert.equal(server.row.status,'draft');
 await h.remount({request:server.request,onRegisterGuard:fn=>{guard=fn||guard;}});assert.match(document.body.textContent,/当前版本：草稿/);
 await h.click('校验契约');assert.equal(server.row.status,'validated');assert.match(document.body.textContent,/契约已校验/);
 await h.text('发布说明','已核对服务契约');await h.click('发布能力');assert.equal(server.row.status,'published');assert.match(document.body.textContent,/契约已发布/);
 assert.ok(server.calls.every(call=>call.workspace==='w1'));assert.equal(guard(),true);
}));

test('lost create response is recovered by exact identity without a duplicate insert',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const server=fakeBackend({loseCreate:true});let guard;await h.mount({request:server.request,onRegisterGuard:fn=>{if(fn)guard=fn;}});await h.fill();
 await h.click('保存登记草稿');assert.match(document.body.textContent,/结果尚未确认/);assert.equal(guard(),false);
 await h.click('核对原操作');assert.match(document.body.textContent,/当前版本：草稿/);assert.equal(guard(),true);
 assert.equal(server.calls.filter(call=>call.path==='/external-capability-registrations/attempts').length,1);
}));

test('credentials pasted into endpoint or reference are never saved in browser draft',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount({request:fakeBackend().request});await h.text('服务地址','https://name:secret@example.org/?token=secret');
 assert.match(document.body.textContent,/输入尚未保存/);assert.equal(h.button('保存登记草稿').disabled,true);
 await h.text('服务端凭据引用','sk_actual_secret');assert.doesNotMatch(localStorage.getItem(localStorage.key(0))||'',/sk_actual_secret|name:secret|token=secret/);
 const event=new h.dom.window.Event('beforeunload',{cancelable:true});window.dispatchEvent(event);assert.equal(event.defaultPrevented,true,'unsaved sensitive address must warn before reload');
}));

for(const code of [401,403,404,408,422,429,499,500])test(`HTTP ${code} after dispatch stays pending until verified`,{concurrency:false,timeout:30000},()=>harness(async h=>{
 let calls=0;const request=async(path)=>{if(path==='/external-capability-registrations/attempts'){calls++;const e=Error('登记未完成');e.status=code;throw e;}throw Error('unexpected');};
 let guard;await h.mount({request,onRegisterGuard:fn=>{if(fn)guard=fn;}});await h.fill();await h.click('保存登记草稿');
 assert.equal(guard(),false);assert.equal(h.input('英文标识').value,'weather');assert.equal(h.input('英文标识').matches(':disabled'),true);assert.match(document.body.textContent,/结果尚未确认/);assert.equal(calls,1);
}));

test('identity conflict is checked first, then user can preserve inputs and change version',{concurrency:false,timeout:30000},()=>harness(async h=>{
 let original=null;const request=async(path,options={})=>{
  if(path==='/external-capability-registrations/attempts'){const data=JSON.parse(options.body).manifest;original={id:'other',workspace_id:'w1',namespace:data.namespace,name:data.name,release_version:data.version,kind:data.kind,data:{...data,description:'其他人的原记录'},version:1,status:'draft'};const e=Error('版本冲突');e.status=409;throw e;}
  if(path.startsWith('/external-capability-registrations/lookup'))return original;if(path.endsWith('/cancel'))return {request_id:path.split('/').at(-2),state:'cancelled'};throw Error('unexpected');};
 let guard;await h.mount({request,onRegisterGuard:fn=>{if(fn)guard=fn;}});await h.fill();await h.click('保存登记草稿');assert.equal(guard(),false);
 await h.click('核对原操作');assert.match(document.body.textContent,/已有不同内容/);await h.click('取消原登记并修改');
 assert.equal(guard(),true);assert.equal(h.input('英文标识').value,'weather');assert.equal(h.input('能力说明').value,'天气查询');assert.equal(h.input('能力版本').value,'1.0.0');
}));

test('parent re-render with a new guard callback does not abort registration',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const server=fakeBackend();await h.mount({request:server.request,onRegisterGuard:()=>{}});await h.fill();
 await h.mount({request:server.request,onRegisterGuard:()=>{}});await h.click('保存登记草稿');
 assert.equal(server.row.status,'draft');assert.match(document.body.textContent,/登记草稿已保存在当前工作区/);
}));

test('an aborted page keeps pending input and never automatically reposts after reload',{concurrency:false,timeout:30000},()=>harness(async h=>{
 let resolveCreate;const calls=[];const request=(path,opts)=>{calls.push(path);if(path==='/external-capability-registrations/attempts')return new Promise(resolve=>{resolveCreate=resolve;});throw Error('must not automatically query or submit');};
 let guard;await h.mount({request,onRegisterGuard:fn=>{if(fn)guard=fn;}});await h.fill();
 await act(async()=>{h.button('保存登记草稿').click();await Promise.resolve();});assert.equal(guard(),false);assert.equal(calls.length,1);
 await h.remount({request,onRegisterGuard:fn=>{if(fn)guard=fn;}});assert.match(document.body.textContent,/结果尚未确认/);assert.equal(calls.length,1);assert.equal(guard(),false);
 await act(async()=>resolveCreate({}));assert.equal(calls.length,1);assert.match(document.body.textContent,/结果尚未确认/);
}));

test('retry rejection cannot clear an earlier uncertain create operation',{concurrency:false,timeout:30000},()=>harness(async h=>{
 let attempts=0;const request=async(path)=>{
  if(path==='/external-capability-registrations/attempts'){attempts++;const e=Error('请求未确认');e.status=attempts===1?408:422;throw e;}
  if(path.startsWith('/external-capability-registrations/lookup')){const e=Error('未找到');e.status=404;throw e;}throw Error('unexpected');};
 let guard;await h.mount({request,onRegisterGuard:fn=>{if(fn)guard=fn;}});await h.fill();await h.click('保存登记草稿');await h.click('核对原操作');
 await h.click('按原内容重试');assert.equal(attempts,2);assert.equal(guard(),false);assert.match(document.body.textContent,/请先核对原操作/);assert.ok(h.button('核对原操作'));
}));

test('retry preflight failure keeps an earlier uncertain validation operation',{concurrency:false,timeout:30000},()=>harness(async h=>{
 const backend=fakeBackend();let phase=0;const request=async(path,options)=>{
  if(path.endsWith('/validate')){phase=1;const e=Error('超时');e.status=408;throw e;}
  if(path.startsWith('/external-capability-registrations/lookup')&&phase===2)throw Error('前置查询断线');
  return backend.request(path,options);};
 let guard;await h.mount({request,onRegisterGuard:fn=>{if(fn)guard=fn;}});await h.fill();await h.click('保存登记草稿');await h.click('校验契约');await h.click('核对原操作');phase=2;
 await h.click('按原内容重试');assert.equal(guard(),false);assert.match(document.body.textContent,/请先核对原操作/);assert.ok(h.button('核对原操作'));
}));

test('cancel timeout keeps draft locked; confirmed cancel permits editing after remount',{concurrency:false,timeout:30000},()=>harness(async h=>{
 let cancelCalls=0;let original;
 const request=async(path,opts)=>{
  if(path==='/external-capability-registrations/attempts'){original=JSON.parse(opts.body);throw Error('连接中断');}
  if(path.endsWith('/cancel')){cancelCalls++;if(cancelCalls===1)throw Error('取消结果未知');return {request_id:original.request_id,state:'cancelled'};}
  throw Error('unexpected');
 };
 await h.mount({request});await h.fill();await h.click('保存登记草稿');await h.click('取消原登记并修改');
 assert.equal(h.input('英文标识').matches(':disabled'),true);assert.match(document.body.textContent,/原操作和输入仍保留/);
 await h.remount({request});await h.click('取消原登记并修改');assert.equal(h.input('英文标识').matches(':disabled'),false);
 assert.equal(h.input('英文标识').value,'weather');assert.match(document.body.textContent,/原登记已安全取消/);
}));

test('cancel after server created record adopts original instead of allowing duplicate',{concurrency:false,timeout:30000},()=>harness(async h=>{
 let original;
 const request=async(path,opts)=>{
  if(path==='/external-capability-registrations/attempts'){original=JSON.parse(opts.body);throw Error('响应丢失');}
  if(path.endsWith('/cancel')){const data=original.manifest;return {request_id:original.request_id,state:'created',record:{id:'cap-1',workspace_id:'w1',namespace:data.namespace,name:data.name,release_version:data.version,kind:data.kind,data,version:1,status:'draft'}};}
  throw Error('unexpected');
 };
 await h.mount({request});await h.fill();await h.click('保存登记草稿');await h.click('取消原登记并修改');
 assert.match(document.body.textContent,/原登记已先完成/);assert.match(document.body.textContent,/当前版本：草稿/);assert.ok(h.button('校验契约'));
}));
