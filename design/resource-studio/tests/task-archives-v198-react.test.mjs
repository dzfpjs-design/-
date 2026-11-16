import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import React,{act,useEffect} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {scope,archiveID,fixture,copy} from './task-archives-v198-fixture.mjs';
// Real ReactDOM/StrictMode, synthetic closed HTTP DTOs. No network, original
// source files, database, provider or model execution is permitted by this test.
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temp,bundle;
after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{temp=await mkdtemp(join(tmpdir(),'task-archives-v198-react-'));
  const result=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),mode:'test',logLevel:'silent',build:{ssr:join(project,'src/TaskArchivesV198.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const entry=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(entry);const file=join(temp,'TaskArchivesV198.mjs');await writeFile(file,entry.code);return (await import(pathToFileURL(file).href)).default;
 })();return bundle;
}
class NoResources extends ResourceLoader{fetch(){throw Error('external resources are forbidden in archive DOM tests');}}
async function harness(run,{storageFailure=false}={}){
 const errors=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://synthetic-archives.invalid/',resources:new NoResources(),virtualConsole:console});
 const names=['window','document','navigator','HTMLElement','Event','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'],previous=Object.fromEntries(names.map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 for(const [name,value]of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true}))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request:async(_key,_options,fn)=>fn()}});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,server=fixture(storage),container=dom.window.document.getElementById('root');let root,guard,probe={setup:0,cleanup:0};
 const originalSet=dom.window.Storage.prototype.setItem;if(storageFailure)dom.window.Storage.prototype.setItem=function(key,value){if(this===storage)throw Error('synthetic denied local storage');return originalSet.call(this,key,value);};
 const settle=async()=>{for(let i=0;i<12;i++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
 const button=label=>{const result=[...container.querySelectorAll('button')].find(node=>node.textContent===label);assert.ok(result,'actual DOM button '+label);return result;};
 const click=async label=>{const control=button(label);assert.equal(control.disabled,false,label+' is enabled');await act(async()=>control.click());await settle();};
 const input=async(label,value)=>{const control=container.querySelector('[aria-label="'+label+'"]');assert.ok(control,'actual form '+label);assert.equal(control.readOnly,false,label+' editable');const proto=control.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype,setter=Object.getOwnPropertyDescriptor(proto,'value').set;await act(async()=>{setter.call(control,value);control.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});await settle();};
 const expand=async label=>{const summary=[...container.querySelectorAll('summary')].find(node=>node.textContent===label);assert.ok(summary,'actual details '+label);await act(async()=>{summary.parentElement.open=true;summary.parentElement.dispatchEvent(new dom.window.Event('toggle'));});await settle();};
 try{
  const {createRoot}=await import('react-dom/client'),Component=await component();
  function Probe(){useEffect(()=>{probe.setup++;return()=>{probe.cleanup++;};},[]);return null;}
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(Probe),React.createElement(Component,{task:copy(server.task),api:server.api,actor:scope.actor_id,workspace:scope.workspace_id,onRegisterGuard:value=>{guard=value;}}))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:e=>errors.push(e),onCaughtError:e=>errors.push(e),onRecoverableError:e=>errors.push(e)});await render();};
  const unmount=async()=>{if(root){const last=root;root=null;await act(async()=>last.unmount());await settle();}};
  await run({...server,dom,container,storage,session,mount,unmount,render,settle,button,click,input,expand,text:()=>container.textContent,get guard(){return guard;},get probe(){return probe;}});
  await unmount();assert.deepEqual(errors,[],'no actual ReactDOM or jsdom errors');
 }finally{if(root)await act(async()=>root.unmount());dom.window.Storage.prototype.setItem=originalSet;dom.window.close();for(const key of names){if(previous[key])Object.defineProperty(globalThis,key,previous[key]);else delete globalThis[key];}}
}

test('StrictMode archive journey lazily reads original products, preserves text on remount and GET-recovers one lost save',async()=>harness(async h=>{
 await h.mount();assert.ok(h.probe.setup>=2&&h.probe.cleanup>=1);assert.match(h.text(),/候选档案/);assert.match(h.text(),/工程模拟 · 非人工核对/);assert.equal(h.text().includes('现场核对的原保存说明'),false,'full source output is not eagerly repeated');
 await h.expand('查看来源 1 的保存内容');assert.ok(h.text().includes('现场核对的原保存说明\n多行记录保持原样'));assert.ok(h.text().includes('明确为空'));assert.ok(h.text().includes('0'));assert.equal(h.text().includes('actual-human-activation'),false);assert.equal(h.text().includes(scope.actor_id),false);
 await h.input('档案标题','人工修订的档案\n标题');await h.input('候选说明','我的原始说明\n仍待核对');assert.equal(h.guard(),true);await h.unmount();await h.mount();assert.equal(h.container.querySelector('[aria-label="候选说明"]').value,'我的原始说明\n仍待核对');
 h.state.mode='aftercommit';await h.click('保存修订为新版本');const outbox=Object.keys(h.storage).find(key=>key.endsWith(':outbox'));assert.ok(outbox);const original=h.storage.getItem(outbox);assert.equal(h.calls.filter(row=>row.method==='POST').length,1);assert.equal(h.container.querySelector('[aria-label="候选说明"]').readOnly,true);
 await h.unmount();await h.mount();assert.equal(h.storage.getItem(outbox),original);assert.equal(h.calls.filter(row=>row.method==='POST').length,1);await h.click('只核对原保存请求');assert.equal(h.storage.getItem(outbox),null);assert.equal(h.calls.filter(row=>row.method==='POST').length,1);assert.match(h.text(),/本次修订已保存为新版本/);
 await h.click('查看第 1 版');assert.match(h.text(),/历史版本，只读保留/);assert.ok(h.text().includes('当前说明是待核提案'));await h.click('查看最新版');assert.equal(h.container.querySelector('[aria-label="档案标题"]').value,'人工修订的档案\n标题');
 assert.equal([...h.container.querySelectorAll('button')].some(node=>/正式确认|确认归档|完结任务/.test(node.textContent)),false);
}));

test('actual archive DOM keeps unknown original request and explicitly repeats identical bytes only after a negative GET',async()=>harness(async h=>{
 await h.mount();await h.input('候选说明','必须使用同一个原请求恢复');h.state.mode='beforecommit';await h.click('保存修订为新版本');const raw=h.state.postBodies[0];assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
 assert.equal([...h.container.querySelectorAll('button')].some(node=>node.textContent==='按原内容重试同一次保存'),false);await h.click('只核对原保存请求');assert.match(h.text(),/不代表未提交/);assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
 h.state.mode='';await h.click('按原内容重试同一次保存');assert.deepEqual(h.state.postBodies,[raw,raw]);assert.equal(h.commands.size,1);assert.equal(h.versions.length,2);
}));

test('actual CAS refusal preserves original input/version; historical read error leaves the prior source view available',async()=>harness(async h=>{
 await h.mount();await h.input('候选说明','旧稿里的完整人工说明');h.append({title:'服务器另一版'});await h.click('保存修订为新版本');assert.match(h.text(),/服务器明确拒绝/);assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
 await h.click('保留被拒原请求，查看最新版');assert.equal(h.container.querySelector('[aria-label="候选说明"]').value,'旧稿里的完整人工说明');assert.match(h.text(),/服务器已有另一版本/);
 await h.click('另存本页输入并使用最新版');assert.equal(h.container.querySelector('[aria-label="档案标题"]').value,'服务器另一版');await h.expand('查看另行保留的本页输入（1 份）');assert.ok(h.text().includes('旧稿里的完整人工说明'));assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
 h.state.denyDetail=true;await h.click('查看第 1 版');assert.match(h.text(),/归集历史暂时无法核对/);assert.ok(h.container.querySelector('[aria-label="档案来源"]'));assert.equal(h.container.querySelector('[aria-label="档案标题"]').value,'服务器另一版');
}));

test('demoted owner cold-opens history and recovers original reply using only GET; account denial immediately hides source text',async()=>harness(async h=>{
 await h.mount();await h.input('候选说明','原账号保留的说明');h.state.mode='aftercommit';await h.click('保存修订为新版本');const outbox=Object.keys(h.storage).find(key=>key.endsWith(':outbox')),raw=h.storage.getItem(outbox),count=h.calls.filter(row=>row.method==='POST').length;
 h.state.role='viewer';await h.unmount();await h.mount();assert.match(h.text(),/当前为只读权限/);await h.click('查看第 1 版');await h.click('只核对原保存请求');assert.equal(h.calls.filter(row=>row.method==='POST').length,count);assert.equal(h.storage.getItem(outbox),null);assert.equal(h.container.querySelector('[aria-label="候选说明"]').readOnly,true);
 await h.expand('查看来源 1 的保存内容');assert.ok(h.text().includes('现场核对的原保存说明'));const records=Object.keys(h.storage).map(key=>[key,h.storage.getItem(key)]);h.state.actor_id='19800000-0000-4000-8000-000000000099';await act(async()=>h.dom.window.dispatchEvent(new h.dom.window.Event('focus')));await h.settle();assert.match(h.text(),/原档案与输入已隐藏/);assert.equal(h.text().includes('现场核对的原保存说明'),false);assert.equal(h.container.querySelector('textarea'),null);assert.equal(h.calls.filter(row=>row.method==='POST').length,count);for(const [key,value]of records)assert.equal(h.storage.getItem(key),value);assert.ok(raw);
}));

test('denied local storage still shows server history but disables new revision and permits zero POST',async()=>harness(async h=>{
 await h.mount();assert.ok(h.container.querySelector('[aria-label="档案详情"]'));assert.match(h.text(),/本机保存失败/);assert.equal(h.button('保存修订为新版本').disabled,true);await h.input('候选说明','只在本页保留，先不能提交');assert.equal(h.guard(),false);assert.equal(h.container.querySelector('[aria-label="候选说明"]').value,'只在本页保留，先不能提交');await h.click('查看第 1 版');assert.match(h.text(),/历史版本，只读保留/);assert.equal(h.calls.filter(row=>row.method==='POST').length,0);
},{storageFailure:true}));
