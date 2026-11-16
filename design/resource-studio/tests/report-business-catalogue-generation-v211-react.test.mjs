// Actual offline ReactDOM, synthetic GET/negative preview only; not target-runtime or paid-source acceptance.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {webcrypto,randomUUID} from 'node:crypto';
import {createRequire} from 'node:module';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),requireProject=createRequire(join(project,'package.json'));
let temporary,Panel;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){if(Panel)return Panel;temporary=await mkdtemp(join(tmpdir(),'catalogue-generation-v211-'));const vite=createRequire(requireProject.resolve('vite/package.json')),{build}=await import(pathToFileURL(vite.resolve('esbuild')).href),out=join(temporary,'generation.mjs');
 await build({entryPoints:[join(project,'src/ReportBusinessCatalogueGenerationV211.jsx')],outfile:out,bundle:true,format:'esm',platform:'node',jsx:'automatic',logLevel:'silent',loader:{'.css':'empty'},plugins:[{name:'offline-generation-http',setup(builder){builder.onResolve({filter:/^\.\/runtime-api$/},()=>({path:'offline-api',namespace:'offline-generation'}));builder.onLoad({filter:/.*/,namespace:'offline-generation'},()=>({loader:'js',contents:'export const json=()=>{throw Error("unprovided transport");};export const hashBytes=async bytes=>Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",bytes))).map(n=>n.toString(16).padStart(2,"0")).join("");'}));builder.onResolve({filter:/^react(?:\/.*)?$/},args=>({path:pathToFileURL(requireProject.resolve(args.path)).href,external:true}));}}]});Panel=(await import(pathToFileURL(out).href)).default;return Panel;
}
class OfflineOnly extends ResourceLoader{fetch(url){throw Error('unexpected network '+url);}}
async function harness(run,{valid=true,model=true}={}){
 const errors=[],dom=new JSDOM('<main id="app"></main>',{url:'http://offline.invalid/',resources:new OfflineOnly(),virtualConsole:new VirtualConsole().on('jsdomError',error=>errors.push(error))}),storage=dom.window.localStorage,account={user_id:randomUUID(),workspace_id:randomUUID(),role:'operator'},producer={id:randomUUID(),revision:3,status:'failed'},models=model?[{id:randomUUID(),generation_profile:'catalogue-v211',provider:'qwen',model:'qwen3.8-max-0902',name:'已登记的目录模型'}]:[];
 const calls=[],state={guard:null,posts:[],actual:account,revision:3,previewHold:null},previous=new Map();let root;
 const transport=async(path,options={})=>{calls.push(path);if(path==='/me')return {...state.actual};if(options.method==='POST'){state.posts.push({path,raw:options.body});if(state.previewHold)await state.previewHold;const error=Error('合成负控：原报告没有完整来源证明，未准备调用');error.status=409;throw error;}assert.equal(path,'/setup/report-saved-map-designs?producer_id='+producer.id);return {schema:'report-saved-map-design-list-v178',producer_id:producer.id,items:[]};};
 for(const[name,value]of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,localStorage:storage,HTMLElement:dom.window.HTMLElement,HTMLInputElement:dom.window.HTMLInputElement,HTMLTextAreaElement:dom.window.HTMLTextAreaElement,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,IS_REACT_ACT_ENVIRONMENT:true,crypto:webcrypto})){previous.set(name,Object.getOwnPropertyDescriptor(globalThis,name));Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});}
 const container=dom.window.document.querySelector('#app'),settle=async()=>{await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});},until=async(predicate)=>{for(let n=0;n<80;n++){await settle();if(predicate())return;}throw Error('offline component did not settle: '+container.textContent);},button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label),click=async label=>{assert.ok(button(label),label);assert.equal(button(label).disabled,false,label);await act(async()=>button(label).click());await settle();};
 const edit=async(label,value)=>{const element=container.querySelector('[aria-label="'+label+'"]');assert.ok(element,label);const proto=element.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLTextAreaElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(proto,'value').set.call(element,value);element.dispatchEvent(new dom.window.Event('input',{bubbles:true}));element.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});await settle();};
 try{const Component=await component(),{createRoot}=await import('react-dom/client');root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});const props={account,producer:valid?producer:{id:producer.id,revision:null,status:'failed'},models,request:transport,storage,locks:{request:async(_key,_options,fn)=>fn()},prepareDraft:async()=>state.revision,onRegisterGuard:guard=>{state.guard=guard;},currentAvailable:true};
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Component,props))));await settle();};await render();await until(()=>valid?state.guard?.()===true:!!container.querySelector('[role="alert"]'));
  await run({container,storage,account,producer,models,calls,state,button,click,edit,until,settle,render,props});assert.deepEqual(errors,[]);
 }finally{if(root)await act(async()=>root.unmount());for(const[name,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];}dom.window.close();}
}

test('prominent generation entry remains separate from human editing, shows only registered catalogue models and saves real typed requirements',async()=>harness(async h=>{
 assert.ok(h.button('自动整理业务清单'));assert.equal(h.container.querySelector('textarea'),null);assert.equal(h.state.posts.length,0);await h.click('自动整理业务清单');
 const select=h.container.querySelector('[aria-label="业务目录模型"]');assert.equal(select.options.length,2);assert.equal(h.button('核对完整资料与单次报价').disabled,true);
 await h.edit('业务目录模型',h.models[0].id);await h.edit('业务目录整理要求','人工原要求😀\n完整保留并先核对来源。');assert.equal(h.button('核对完整资料与单次报价').disabled,false);assert.ok([...Array(h.storage.length)].some((_,i)=>(h.storage.getItem(h.storage.key(i))||'').includes('人工原要求😀')));
 await h.click('核对完整资料与单次报价');await h.until(()=>h.container.textContent.includes('没有完整来源证明'));assert.equal(h.state.posts.length,1);assert.ok(h.state.posts[0].path.endsWith('/preview-indexed'));assert.equal(h.container.querySelector('[aria-label="业务目录整理要求"]').value,'人工原要求😀\n完整保留并先核对来源。');assert.equal(h.container.textContent.includes('同意并开始整理业务清单'),false);assert.equal(h.container.textContent.includes('目录建议已保存'),false);
}));
test('missing dedicated model is a readable blocked state with no old-model fallback or model calls',async()=>harness(async h=>{await h.click('自动整理业务清单');assert.ok(h.container.textContent.includes('没有已登记的目录专用模型'));assert.equal(h.button('核对完整资料与单次报价').disabled,true);assert.equal(h.state.posts.length,0);},{model:false}));
test('normal prepareDraft revision advance keeps the same in-flight operation and original typed labour',async()=>harness(async h=>{
 await h.click('自动整理业务清单');await h.edit('业务目录模型',h.models[0].id);await h.edit('业务目录整理要求','配置正常保存后仍保留这个要求。');let release;h.state.revision=4;h.state.previewHold=new Promise(resolve=>{release=resolve;});await h.click('核对完整资料与单次报价');await h.until(()=>h.state.posts.length===1);
 assert.equal(JSON.parse(h.state.posts[0].raw).revision,4);h.props.producer={...h.producer,revision:4};await h.render();assert.equal(h.state.guard(),false);assert.equal(h.container.querySelector('[aria-label="业务目录整理要求"]').value,'配置正常保存后仍保留这个要求。');h.state.previewHold=null;release();await h.until(()=>h.container.textContent.includes('没有完整来源证明'));assert.equal(h.state.posts.length,1);
}));
test('incomplete producer metadata blocks safely instead of crashing the parent or creating a draft',async()=>harness(async h=>{assert.ok(h.container.querySelector('[role="alert"]'));assert.equal(h.button('自动整理业务清单').disabled,true);assert.equal(h.state.posts.length,0);assert.equal(h.state.guard(),false);},{valid:false}));
