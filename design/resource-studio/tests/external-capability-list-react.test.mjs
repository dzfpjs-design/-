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
  folder=await mkdtemp(join(tmpdir(),'external-capabilities-'));
  const reactURL=pathToFileURL(join(project,'node_modules/react/index.js')).href;
  const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/ExternalCapabilityList.jsx'),write:false,minify:false,rollupOptions:{external:id=>id==='react'||id===reactURL,output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?reactURL:id}}}});
  process.env.NODE_ENV='development';const file=join(folder,'component.mjs');await writeFile(file,result.output.find(r=>r.type==='chunk'&&r.isEntry).code);return(await import(pathToFileURL(file).href)).default;
 })();return compiled;
}
const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
const record=name=>({id:name,kind:'tool',name,release_version:'1.0.0',status:'published',data:{contract_version:'1.0',description:'描述',execution:{adapter:'http',endpoint:'https://private.invalid'}}});

test('failure is retryable and only the authorized workspace records render',{concurrency:false,timeout:30000},async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true}),old=new Map();
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true})){
  old.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 }
 let root,failed=true;const calls=[];
 const request=async(path,options)=>{calls.push({path,workspace:options.headers['X-Workspace-ID']});if(failed)throw Error('private error');return [record('weather')];};
 try{
  const C=await component(),{createRoot}=await import('react-dom/client');root=createRoot(dom.window.document.getElementById('root'));
  await act(async()=>root.render(React.createElement(C,{account:{user_id:'u1',workspace_id:'w1'},kind:'tool',request,Btn})));
  assert.match(document.body.textContent,/暂时无法读取/);assert.doesNotMatch(document.body.textContent,/private error/);
  failed=false;await act(async()=>[...document.querySelectorAll('button')].find(x=>x.textContent==='重试读取').click());
  assert.match(document.body.textContent,/weather/);assert.match(document.body.textContent,/契约已发布/);assert.doesNotMatch(document.body.textContent,/private\.invalid/);
  assert.ok(calls.every(x=>x.path==='/capabilities'&&x.workspace==='w1'));
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});

test('late response for the previous account cannot appear in the new workspace',{concurrency:false,timeout:30000},async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true}),old=new Map();
 for(const [key,value] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true})){
  old.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});
 }
 let root;const pending=[];
 const request=(path,options)=>new Promise(resolve=>pending.push({workspace:options.headers['X-Workspace-ID'],resolve}));
 try{
  const C=await component(),{createRoot}=await import('react-dom/client');root=createRoot(dom.window.document.getElementById('root'));
  await act(async()=>root.render(React.createElement(C,{account:{user_id:'u1',workspace_id:'w1'},kind:'tool',request,Btn})));
  await act(async()=>root.render(React.createElement(C,{account:{user_id:'u2',workspace_id:'w2'},kind:'tool',request,Btn})));
  await act(async()=>pending[1].resolve([record('new-record')]));assert.match(document.body.textContent,/new-record/);
  await act(async()=>pending[0].resolve([record('old-record')]));assert.doesNotMatch(document.body.textContent,/old-record/);
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
});
