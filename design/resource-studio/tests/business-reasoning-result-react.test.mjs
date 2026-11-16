// Actual JSX + ReactDOM/StrictMode; synthetic authenticated GET responses only.
// This is not browser download, real evidence permission, model or DB proof.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {BUSINESS_REASONING_RESULT,MAX_TEXT_PREVIEW_BYTES} from '../src/business-reasoning-result.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),id=n=>'19700000-0000-4000-8000-'+String(n).padStart(12,'0');
let temporary,compiled;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'business-reasoning-result-'));
  const previous=process.env.NODE_ENV;let result;
  try{process.env.NODE_ENV='development';result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/BusinessReasoningResult.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});}
  finally{if(previous===undefined)delete process.env.NODE_ENV;else process.env.NODE_ENV=previous;}
  const entry=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(entry);
  const target=join(temporary,'BusinessReasoningResult.mjs');await writeFile(target,entry.code);
  return (await import(pathToFileURL(target).href)).default;
 })();return compiled;
}

function scene(){
 const inputs={question:'怎样核对异常并安排后续工作？',evidence_ids:[id(2),id(1)],candidates:[{candidate_ref:'private_proposal_first',summary:'先核对受损范围',context:{private_context:'不要直接展示此上下文'}},{candidate_ref:'private_proposal_second',summary:'扩大范围建议',context:null},{candidate_ref:'private_proposal_third',summary:'资料尚不足的建议',context:null}]};
 const value={schema:BUSINESS_REASONING_RESULT,analysis:{summary:'已有资料支持第一方案，另需补充当前设备观测。',candidate_assessments:[{candidate_ref:'private_proposal_first',assessment:'supported',reason:'本次资料说明需先核对范围。',evidence_refs:[id(2)],missing_information:[]},{candidate_ref:'private_proposal_second',assessment:'conflicting',reason:'另份资料与该范围不一致。',evidence_refs:[id(1)],missing_information:[]},{candidate_ref:'private_proposal_third',assessment:'insufficient',reason:'缺少当前设备观测。',evidence_refs:[],missing_information:['需提供当前设备的有效观测。']}],recommendation:{candidate_ref:'private_proposal_first',reason:'先核对范围，再由流程决定下一步。',evidence_refs:[id(2)]}},review_state:'needs_information',recommended_candidate_ref:'private_proposal_first',recommended_context:null,permissions_verified:false,can_dispatch:false};
 return {inputs,value};
}

test('actual StrictMode result hides internals, reads only on demand, retries and releases download blobs',async()=>{
 assert.equal(process.env.NODE_ENV,'development','Run React and ReactDOM in the same development runtime');
 const errors=[],external=[],calls=[],downloads=[],created=[],revoked=[];
 const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>errors.push(error));
 class NoResources extends ResourceLoader{fetch(url){external.push(String(url));throw Error('No external resources are allowed');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-result.invalid/',resources:new NoResources(),virtualConsole});
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Node:dom.window.Node,MutationObserver:dom.window.MutationObserver,sessionStorage:dom.window.sessionStorage,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true};
 const prior=new Map(Object.keys(globals).concat('fetch').map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 sessionStorage.setItem('ga-setup-csrf','synthetic-result-csrf');
 const oldCreate=Object.getOwnPropertyDescriptor(URL,'createObjectURL'),oldRevoke=Object.getOwnPropertyDescriptor(URL,'revokeObjectURL');
 Object.defineProperty(URL,'createObjectURL',{configurable:true,value:blob=>{const url='blob:synthetic-result/'+(created.length+1);created.push({url,blob});return url;}});
 Object.defineProperty(URL,'revokeObjectURL',{configurable:true,value:url=>revoked.push(url)});
 dom.window.HTMLAnchorElement.prototype.click=function(){downloads.push({href:this.href,name:this.download});};
 const longText='资'.repeat(Math.ceil((MAX_TEXT_PREVIEW_BYTES+1)/3));let firstAttempts=0;
 Object.defineProperty(globalThis,'fetch',{configurable:true,writable:true,value:async(path,options={})=>{
  calls.push({path,options});assert.equal(options.method||'GET','GET');assert.equal(options.credentials,'same-origin');assert.equal(options.headers['X-CSRF-Token'],'synthetic-result-csrf');
  if(path==='/api/evidence/'+id(2)+'/content'){
   firstAttempts++;if(firstAttempts===1)return new Response(JSON.stringify({detail:'synthetic temporary failure'}),{status:503,headers:{'content-type':'application/json'}});
   return new Response('<script>never_execute()</script>\n已保存资料原文',{headers:{'content-type':'text/plain; charset=utf-8'}});
  }
  if(path==='/api/evidence/'+id(1)+'/content')return new Response(longText,{headers:{'content-type':'text/plain','content-length':String(new TextEncoder().encode(longText).byteLength)}});
  throw Error('Unexpected authenticated path '+path);
 }});
 const container=document.getElementById('root'),{inputs,value}=scene();let root;
 const until=async(predicate,label)=>{for(let i=0;i<60;i++){if(predicate())return;await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});}assert.ok(predicate(),label);};
 const click=async label=>{const button=[...container.querySelectorAll('button')].find(item=>item.textContent===label);assert.ok(button,'actual button '+label);assert.equal(button.disabled,false);await act(async()=>button.click());};
 try{
  const Result=await component(),{createRoot}=await import('react-dom/client');root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
  await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Result,{value,inputs}))));
  assert.deepEqual(errors,[]);assert.equal(calls.length,0,'StrictMode does not fetch or dispatch automatically');
  const text=container.textContent;
  for(const expected of ['业务问题','先核对受损范围','有资料支持','资料存在冲突','资料不足','需提供当前设备的有效观测。','模型建议不等于业务批准'])assert.ok(text.includes(expected),expected);
  for(const hidden of [id(1),id(2),'private_proposal_first','private_context','can_dispatch','recommended_context','business-reasoning-result-v197'])assert.equal(text.includes(hidden),false,hidden+' stays hidden');
  await click('查看资料1');await until(()=>!!container.querySelector('[role="alert"]'),'failure shown with retry');
  await click('重试查看资料1');await until(()=>!!container.querySelector('[aria-label="资料1原文"]'),'actual authenticated text loaded');
  assert.equal(firstAttempts,2);assert.ok(container.querySelector('pre').textContent.includes('<script>never_execute()</script>'));
  assert.equal(container.querySelectorAll('script,iframe,object,embed').length,0,'source text is escaped, never executed');
  await click('查看资料2');await until(()=>container.textContent.includes('超过页面预览容量'),'oversized preview is blocked without truncating');
  assert.equal(container.textContent.includes(longText),false);
  await click('下载资料2');await until(()=>downloads.length===1&&container.textContent.includes('下载已交给浏览器'),'download was handed to browser rather than claimed saved');
  assert.equal(downloads[0].name,'资料2.txt');assert.equal(created.length,1);
  assert.equal(await created[0].blob.text(),longText,'download retains the entire original');
  assert.equal(calls.length,4);assert.ok(calls.every(item=>(item.options.method||'GET')==='GET'));
  await act(async()=>root.unmount());root=null;
  assert.deepEqual(revoked,[created[0].url]);assert.deepEqual(external,[]);assert.deepEqual(errors,[]);
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();
  for(const[key,descriptor]of prior){if(descriptor)Object.defineProperty(globalThis,key,{configurable:true,...descriptor});else delete globalThis[key];}
  for(const[key,descriptor]of [['createObjectURL',oldCreate],['revokeObjectURL',oldRevoke]]){if(descriptor)Object.defineProperty(URL,key,descriptor);else delete URL[key];}
 }
});
