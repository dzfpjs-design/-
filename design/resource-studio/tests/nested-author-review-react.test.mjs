// Actual JSX + StrictMode ReactDOM and the real review controller/outbox.
// Synthetic transport only: no actual API, browser, provider or human approval.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,resolve,join} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {fixture,open,id} from './nested-author-review-fixture.mjs';
import {nestedAuthorReviewState as state,nestedAuthorReviewForm as form} from '../src/nested-author-review.mjs';
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let directory,compiled;
after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{directory=await mkdtemp(join(tmpdir(),'nested-author-react-'));const built=await build({root:project,configFile:false,envDir:directory,cacheDir:join(directory,'cache'),logLevel:'silent',mode:'test',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/NestedFlowAuthorReview.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});const entry=built.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(entry);const target=join(directory,'component.mjs');await writeFile(target,entry.code);return(await import(pathToFileURL(target).href)).default;})();return compiled;
}
async function browser(){
 assert.equal(process.env.NODE_ENV,'development');const errors=[],network=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));class NoResources extends ResourceLoader{fetch(url){network.push(String(url));throw Error('External resources forbidden');}}
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-nested-author.invalid/',resources:new NoResources(),virtualConsole:console}),globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Element:dom.window.Element,Node:dom.window.Node,Event:dom.window.Event,MouseEvent:dom.window.MouseEvent,IS_REACT_ACT_ENVIRONMENT:true,fetch:url=>{network.push(String(url));throw Error('Real HTTP forbidden');}},previous=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});
 const Component=await component(),{createRoot}=await import('react-dom/client'),container=dom.window.document.getElementById('root');let root,c;
 const paint=()=>root?.render(React.createElement(React.StrictMode,null,React.createElement(Component,{controller:c})));
 async function mount(controller){c=controller;root=createRoot(container,{onCaughtError:error=>errors.push(error),onUncaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});c.onChange=paint;await act(async()=>paint());}
 async function unmount(){if(root){c.onChange=()=>{};await act(async()=>root.unmount());root=null;}}
 const buttons=()=>[...container.querySelectorAll('button')],button=label=>{const found=buttons().find(node=>node.textContent===label);assert.ok(found,'actual control '+label);return found;};
 async function click(label){const node=button(label);assert.equal(node.disabled,false,label+' is enabled');await act(async()=>node.click());}
 async function until(check,label){for(let step=0;step<80;step++){assert.deepEqual(errors,[]);if(check())return;await act(async()=>{await new Promise(done=>setImmediate(done));});}assert.ok(check(),label);}
 async function select(value){const node=container.querySelector('select[aria-label="递归流程核对方式"]');assert.ok(node);assert.equal(node.disabled,false);await act(async()=>{node.value=value;node.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});}
 async function type(value){const node=container.querySelector('textarea[aria-label="递归流程核对说明"]');assert.ok(node);assert.equal(node.readOnly,false);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});}
 async function acknowledge(){for(const label of ['明确核对递归流程范围','明确本次核对性质']){const node=container.querySelector('input[aria-label="'+label+'"]');assert.ok(node);assert.equal(node.checked,false);assert.equal(node.disabled,false);await act(async()=>node.click());}}
 async function close(){try{await unmount();c?.dispose();assert.deepEqual(errors,[]);assert.deepEqual(network,[]);}finally{dom.window.close();for(const[key,value]of previous){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}}
 return {dom,container,mount,unmount,click,button,until,select,type,acknowledge,close,text:()=>container.textContent||''};
}

test('actual default author blocks open questions and explicit engineering review preserves its original nonce through remount and GET recovery',{timeout:30000,concurrency:false},async()=>{
 const h=await browser(),f=fixture();let c=await open(f);
 try{
  await h.mount(c);assert.equal(h.container.querySelector('select').value,'author_self_review');assert.equal(f.posts().length,0);assert.equal(h.button('检查已保存的递归流程').disabled,true);assert.ok(h.text().includes('当前稿还有 1 项待核问题'));assert.ok(h.text().includes('请先修订并保存'));assert.equal(h.container.querySelector('[aria-label="本次递归核对范围"]'),null);assert.equal(h.container.querySelectorAll('input[type="checkbox"]').length,0);assert.equal(f.calls.some(call=>call.path.includes('/nested-review-preview')),false);assert.equal(f.posts().length,0);
  await h.select('engineering_simulation');assert.equal(form(c).preview,null);assert.ok(h.text().includes('工程模拟，非人工业务审查'));await h.click('检查已保存的递归流程');await h.until(()=>form(c)?.preview?.review_mode==='engineering_simulation'&&!c.busy,'distinct engineering preview');assert.equal(h.container.querySelector('input[type="checkbox"]').checked,false);await h.acknowledge();await h.type('保留全部待核问题，仅验证工程接线');f.mode.lost=true;await h.click('记录此版本的工程模拟检查');await h.until(()=>!!state(c).pending&&!c.busy,'lost reply exposes original request recovery');
  const pending=structuredClone(state(c).pending),oldKey=c.key,oldJournal=f.storage.getItem(oldKey);assert.equal(pending.body.review_mode,'engineering_simulation');assert.equal(f.posts().length,1);assert.equal(h.container.querySelector('select').disabled,true);assert.equal(h.container.querySelector('textarea').readOnly,true);assert.equal(form(c).result,null);
  await h.unmount();c.dispose();c=await open(f);await h.mount(c);assert.equal(state(c).pending.raw,pending.raw);assert.equal(f.storage.getItem(oldKey),oldJournal);assert.ok(h.text().includes('非人工业务审查'));await h.click('查询原递归核对结果');await h.until(()=>state(c).pending===null&&!c.busy,'GET restores the saved engineering record');assert.equal(f.posts().length,1);assert.equal(form(c).result.human_review,'not_human_review');assert.equal(form(c).result.simulation_only,true);assert.equal(form(c).result.value.business.questions.length,1);assert.ok(h.text().includes('待核问题未据此解决'));await h.click('读取递归核对历史');await h.until(()=>state(c).history.length===1&&!c.busy,'server history loaded');assert.ok(h.container.querySelector('[aria-label="已保存的递归核对历史"]').textContent.includes('工程模拟检查（非人工业务审查）'));assert.deepEqual(f.detail,f.original);assert.equal(f.calls.some(call=>/author-review-preview|\/qualify$|business-flow-executions/.test(call.path)),false);
 }finally{await h.close();}
});

test('actual card retains its note and blocks every write when local persistence fails',{timeout:30000,concurrency:false},async()=>{
 const h=await browser(),f=fixture(),c=await open(f);
 try{await h.mount(c);await h.select('engineering_simulation');await h.click('检查已保存的递归流程');await h.until(()=>!!form(c)?.preview&&!c.busy,'engineering preview loaded');await h.acknowledge();await h.type('必须保留的工程核对输入');f.storage.fail=true;await h.click('记录此版本的工程模拟检查');await h.until(()=>!c.busy&&!c.localSaved,'failed journal cannot initiate network mutation');assert.equal(f.posts().length,0);assert.ok(h.text().includes('本机保存失败'));assert.equal(h.container.querySelector('textarea').value,'必须保留的工程核对输入');assert.equal(h.container.querySelector('textarea').readOnly,true);let safe;await act(async()=>{safe=c.safeToLeave();});assert.equal(safe,false);assert.ok(state(c).pending);assert.equal(form(c).result,null);}finally{f.storage.fail=false;await h.close();}
});

test('actual identity denial immediately conceals nested names, notes and preview without deleting another account journal',{timeout:30000,concurrency:false},async()=>{
 const h=await browser(),f=fixture(),c=await open(f);
 try{await h.mount(c);await h.select('engineering_simulation');await h.click('检查已保存的递归流程');await h.until(()=>!!form(c)?.preview&&!c.busy,'private scope shown only after current identity');await h.acknowledge();await h.type('原账号私有核对说明');f.mode.drop=true;await h.click('记录此版本的工程模拟检查');await h.until(()=>!!state(c).pending&&!c.busy,'original unknown request retained');const before=f.storage.snapshot();f.session.setItem('ga-setup-csrf-user',id(99));await act(async()=>{await c.recheckIdentity().catch(()=>{});});await h.until(()=>!h.container.querySelector('select,textarea'),'denied identity hides all private controls');assert.ok(!h.text().includes('递归业务核对'));assert.ok(!h.text().includes('原账号私有核对说明'));assert.deepEqual(f.storage.snapshot(),before);assert.equal(f.posts().length,1);assert.ok(state(c).pending);}finally{await h.close();}
});
