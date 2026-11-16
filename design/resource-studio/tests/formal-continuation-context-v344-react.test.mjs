import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {build} from 'vite';
import {JSDOM,ResourceLoader} from 'jsdom';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
const project=resolve('.'),require=createRequire(import.meta.url),reactURL=pathToFileURL(require.resolve('react')).href;
let temp,compiled;after(async()=>{if(temp)await rm(temp,{recursive:true,force:true});});
async function component(){if(!compiled)compiled=(async()=>{temp=await mkdtemp(join(tmpdir(),'formal-v344-'));const entry=join(temp,'entry.jsx');await writeFile(entry,'export {default} from '+JSON.stringify(join(project,'src/FormalContinuationContextV344.jsx'))+';');const result=await build({root:project,configFile:false,envDir:temp,cacheDir:join(temp,'cache'),logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:n=>n==='react',output:{format:'es',inlineDynamicImports:true,paths:{react:reactURL}}}}});const file=join(temp,'out.mjs');await writeFile(file,result.output.find(x=>x.type==='chunk'&&x.isEntry).code);return (await import(pathToFileURL(file).href)).default;})();return compiled;}
class Offline extends ResourceLoader{fetch(){throw Error('No network in test');}}
const account={user_id:'account-1',workspace_id:'workspace-1',role:'operator'};
const sample={schema:'formal-continuation-context-v344',available:true,can_start:false,task_completed:false,cloud_calls:0,device_actions:0,advice_generation:2,current_generation:3,advice_data_as_of:'2026-11-01T00:00:00Z',different_generation:true,review:{role:'审核员',person:'reviewer',available:true},steps:[{name:'核对材料',role:'经办人',person:'operator',output:'核对意见',available:true},{name:'审批结果',role:'负责人',output:'审批意见',available:false}],results:[{execution_id:'one',field:{label:'气温'},record_count:1,data_generation:3,current_data:true,simulation_only:true,test_only:true}],reason:'原业务目标仍未验收。'};
async function harness(run){const dom=new JSDOM('<div id="root"></div>',{url:'https://formal.invalid/',resources:new Offline()}),old=new Map();for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,sessionStorage:dom.window.sessionStorage,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true})){old.set(k,Object.getOwnPropertyDescriptor(globalThis,k));Object.defineProperty(globalThis,k,{value:v,writable:true,configurable:true});}
 let root;try{const Component=await component(),{createRoot}=await import('react-dom/client'),container=document.getElementById('root');root=createRoot(container);const calls=[];let response=sample,me=account,fail=false;
 const request=async(path,options)=>{calls.push([path,options]);if(path==='/me')return me;if(fail)throw Error('原记录暂时无法读取');return response;};const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
 let props={account,task:{id:'task-1'},advicePlan:{plan_id:'plan-1'},requestJSON:request,Btn};
 const render=async()=>act(async()=>root.render(React.createElement(Component,props))),click=async label=>{const b=[...container.querySelectorAll('button')].find(x=>x.textContent===label);assert.ok(b,label);await act(async()=>b.click());},until=async fn=>{for(let i=0;i<50&&!fn();i++)await act(async()=>new Promise(r=>setTimeout(r,2)));assert.ok(fn(),container.textContent);};
 await run({container,calls,render,click,until,setFail:v=>fail=v,setMe:v=>me=v,setResponse:v=>response=v,setProps:v=>props={...props,...v}});
 }finally{if(root)await act(async()=>root.unmount());dom.window.close();for(const[k,v]of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}}

test('shows old and new versions, real steps and explicit simulation limit without any write',()=>harness(async h=>{await h.render();await h.click('查看后续步骤');await h.until(()=>h.container.textContent.includes('第 3 版资料'));const s=h.container.textContent;assert.match(s,/第 2 版资料/);assert.match(s,/没有自动进入旧建议/);assert.match(s,/核对材料/);assert.match(s,/账号：operator/);assert.match(s,/责任人当前不可用/);assert.match(s,/不能作为现场事实或真实业务签批/);assert.match(s,/未验收/);assert.ok(h.calls.every(x=>!x[1]||!x[1].method||x[1].method==='GET'));}));
test('failed read can be retried and no stale data is shown for a changed account',()=>harness(async h=>{h.setFail(true);await h.render();await h.click('查看后续步骤');await h.until(()=>h.container.textContent.includes('原记录暂时无法读取'));assert.match(h.container.textContent,/继续办理原业务/);h.setFail(false);await h.click('重试核对');await h.until(()=>h.container.textContent.includes('核对材料'));h.setProps({account:{...account,user_id:'other'}});await h.render();assert.doesNotMatch(h.container.textContent,/核对材料/);h.setMe(account);await h.click('查看后续步骤');await h.until(()=>h.container.textContent.includes('账号或工作区已变化'));assert.doesNotMatch(h.container.textContent,/核对材料/);}));
test('rejects a response that claims it can start or complete the task',()=>harness(async h=>{h.setResponse({...sample,can_start:true});await h.render();await h.click('查看后续步骤');await h.until(()=>h.container.textContent.includes('原流程依据未能完整核对'));assert.doesNotMatch(h.container.textContent,/核对材料/);}));
