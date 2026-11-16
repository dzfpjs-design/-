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
async function component(){if(!compiled)compiled=(async()=>{
 folder=await mkdtemp(join(tmpdir(),'external-handoff-'));const reactURL=pathToFileURL(join(project,'node_modules/react/index.js')).href;
 const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/ExternalCapabilityList.jsx'),write:false,minify:false,rollupOptions:{external:id=>id==='react'||id===reactURL,output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?reactURL:id}}}});
 process.env.NODE_ENV='development';const file=join(folder,'component.mjs');await writeFile(file,result.output.find(r=>r.type==='chunk'&&r.isEntry).code);return(await import(pathToFileURL(file).href)).default;
})();return compiled;}
const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);

test('operator can validate a server record and a different administrator can reopen and publish it without local draft',{concurrency:false,timeout:30000},async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true}),old=new Map();
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true})){
  old.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 }
 let root;const row={id:'record-1',workspace_id:'w1',namespace:'external',name:'weather',release_version:'1.0.0',kind:'tool',status:'draft',version:1,
  data:{description:'天气查询',contract_version:'1.0',execution:{adapter:'http',endpoint:'https://example.org/private-path',side_effect:false},data_policy:{external_allowed:true},input_schema:{type:'object',properties:{}},output_schema:{type:'object',properties:{}}}};
 const request=async(path,options={})=>{
  assert.equal(options.headers['X-Workspace-ID'],'w1');
  if(path==='/capabilities')return [structuredClone(row)];
  if(path.startsWith('/external-capability-registrations/lookup'))return structuredClone(row);
  const body=JSON.parse(options.body);assert.equal(body.expected_version,row.version);
  if(path.endsWith('/validate')){row.status='validated';row.version++;return structuredClone(row);}
  if(path.endsWith('/publish')){row.status='published';row.version++;return structuredClone(row);}
  throw Error('unexpected request');
 };
 try{
  const C=await component(),{createRoot}=await import('react-dom/client');root=createRoot(document.querySelector('#root'));
  const render=async account=>act(async()=>{root.render(React.createElement(C,{account,kind:'tool',Btn,request}));await Promise.resolve();await Promise.resolve();});
  const click=async label=>{const el=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===label);assert.ok(el,label);assert.equal(el.disabled,false,label);await act(async()=>{el.click();await Promise.resolve();await Promise.resolve();});};
  await render({user_id:'operator',workspace_id:'w1',role:'operator'});await click('打开登记记录');await click('校验已有契约');assert.equal(row.status,'validated');
  await render({user_id:'admin',workspace_id:'w1',role:'admin'});await click('打开登记记录');
  const reason=document.querySelector('[aria-label="已有能力发布说明"]');assert.ok(reason);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(reason,'已核对契约');reason.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  await click('发布已有能力');assert.equal(row.status,'published');assert.match(document.body.textContent,/契约已发布/);assert.doesNotMatch(document.body.textContent,/private-path/);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});

test('existing record retry preflight failure preserves an earlier uncertain operation',{concurrency:false,timeout:30000},async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true}),old=new Map();
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true})){
  old.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 }
 let root,phase=0;const row={id:'record-1',workspace_id:'w1',namespace:'external',name:'weather',release_version:'1.0.0',kind:'tool',status:'draft',version:1,data:{description:'天气查询',contract_version:'1.0',execution:{adapter:'http',endpoint:'https://example.org/',side_effect:false},data_policy:{external_allowed:true},input_schema:{type:'object',properties:{}},output_schema:{type:'object',properties:{}}}};
 const request=async(path)=>{
  if(path==='/capabilities')return [structuredClone(row)];
  if(path.startsWith('/external-capability-registrations/lookup')){if(phase===2)throw Error('前置查询断线');return structuredClone(row);}
  if(path.endsWith('/validate')){phase=1;const error=Error('请求未确认');error.status=422;throw error;}throw Error('unexpected');};
 try{
  const C=await component(),{createRoot}=await import('react-dom/client');root=createRoot(document.querySelector('#root'));
  const click=async label=>{const el=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===label);assert.ok(el,label);assert.equal(el.disabled,false,label);await act(async()=>{el.click();await Promise.resolve();await Promise.resolve();});};
  await act(async()=>{root.render(React.createElement(C,{account:{user_id:'operator',workspace_id:'w1',role:'operator'},kind:'tool',Btn,request}));await Promise.resolve();await Promise.resolve();});
  await click('打开登记记录');await click('校验已有契约');await click('核对已有能力原操作');phase=2;await click('按原内容重试已有能力');
  assert.match(document.body.textContent,/请先核对原操作/);assert.match(document.body.textContent,/结果尚未确认/);assert.ok([...document.querySelectorAll('button')].some(x=>x.textContent==='核对已有能力原操作'));
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});
