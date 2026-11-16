import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useState} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {initialWorkspace} from '../src/catalog.js';

test('automation instruction is business scoped, persisted and recovers a failed save without reusing global text',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/?view=automation'});
 const keys=['window','document','navigator','location','history','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const previous=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const k of ['window','document','navigator','location','history','localStorage','sessionStorage'])Object.defineProperty(globalThis,k,{value:dom.window[k],configurable:true,writable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 localStorage.setItem('three-agent-automation-v1-instruction','旧全局输入不应泄露');
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'stub-review',enforce:'pre',resolveId(id){if(id==='./AutomationReviewPanel')return '\0stub-review'},load(id){if(id==='\0stub-review')return 'export default function Panel(){return null}';}}]});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/AutomationStudio.jsx')).default;const {createRoot}=await import('react-dom/client');
  const Btn=({children,icon,primary,ghost,...p})=>React.createElement('button',p,children),Field=({label,children})=>React.createElement('label',null,label,children),Icon=()=>null,Dialog=({children})=>React.createElement('div',null,children);
  let saved={...initialWorkspace(),businesses:['巡护','灾害'],automationInstructions:{'巡护':'巡护原要求','灾害':'灾害原要求'}};let fail=false;const voice=[];
  function Host({account}){const[w,setW]=useState(saved);return React.createElement(Component,{workspace:w,Icon,Btn,Field,Dialog,runtime:true,account,onBack(){},onNext(){},onCommitWorkspace(next){if(fail)return false;saved=next;setW(next);return true;},onRegisterGuard(){},onVoice:(...v)=>voice.push(v)});}
  const render=async(account)=>{root=createRoot(document.getElementById('root'));await act(async()=>{root.render(React.createElement(Host,{account}));await Promise.resolve()})};
  const click=async(button)=>act(async()=>{assert.ok(button);button.click();await Promise.resolve()});
  const button=t=>[...document.querySelectorAll('button')].find(x=>x.textContent===t);
  const textarea=()=>document.querySelector('[aria-label="自动化修改要求"]');
  const change=async(text)=>act(async()=>{const el=textarea();Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(el,text);el.dispatchEvent(new dom.window.Event('input',{bubbles:true}));await Promise.resolve()});
  const account={role:'admin',workspace_id:'w1',user_id:'u1',username:'one'};
  await render(account);assert.equal(textarea().value,'巡护原要求');await click(button('语音输入'));assert.deepEqual(voice,[['automation_instruction','巡护']]);
  await click([...document.querySelectorAll('button')].find(x=>x.textContent.includes('灾害')&&x.className.includes('category')));assert.equal(textarea().value,'灾害原要求');
  await change('灾害新要求');assert.equal(saved.automationInstructions['灾害'],'灾害新要求');assert.equal(saved.automationInstructions['巡护'],'巡护原要求');
  await change('password=abcdefghijk');assert.match(document.body.textContent,/疑似凭据/);assert.doesNotMatch([...Array(sessionStorage.length)].map((_,i)=>sessionStorage.getItem(sessionStorage.key(i))).join(' '),/abcdefghijk/);await change('灾害新要求');
  fail=true;await change('未保存要求');assert.match(document.body.textContent,/重试保存修改要求/);assert.ok([...Array(sessionStorage.length)].some((_,i)=>sessionStorage.key(i).includes('w1:u1:')));
  await act(async()=>root.unmount());fail=false;await render(account);assert.match(document.body.textContent,/本标签保留了此账号、此业务尚未保存/);assert.equal(textarea().value,'灾害新要求');assert.equal(textarea().disabled,true);await click(button('恢复本机修改并保存'));assert.equal(saved.automationInstructions['灾害'],'未保存要求');
  await act(async()=>root.unmount());dom.window.history.replaceState(null,'','?view=automation');await render({...account,workspace_id:'w2',user_id:'u2'});assert.doesNotMatch(document.body.textContent,/本标签保留了此账号/);assert.doesNotMatch(textarea().value,/旧全局输入/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [key,descriptor]of previous){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
});

test('business list changes never persist display defaults and do not overwrite a failed edit',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/?view=automation'}),keys=['window','document','navigator','location','history','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];
 const previous=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const k of keys.slice(0,-1))Object.defineProperty(globalThis,k,{value:dom.window[k],configurable:true,writable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'stub-review',enforce:'pre',resolveId(id){if(id==='./AutomationReviewPanel')return '\0stub-review'},load(id){if(id==='\0stub-review')return 'export default function Panel(){return null}';}}]});let root;
 try{const Component=(await server.ssrLoadModule('/src/AutomationStudio.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,icon,primary,ghost,...p})=>React.createElement('button',p,children),Field=({label,children})=>React.createElement('label',null,label,children),Icon=()=>null,Dialog=({children})=>React.createElement('div',null,children);
  let update,commits=0,fail=false;const account={role:'admin',workspace_id:'w',user_id:'u',username:'n'},base={...initialWorkspace(),businesses:['A'],automation:undefined};
  function Host(){const[w,setW]=useState(base);update=setW;return React.createElement(Component,{workspace:w,Icon,Btn,Field,Dialog,runtime:true,account,onBack(){},onNext(){},onCommitWorkspace(next){commits++;if(fail)return false;setW(next);return true;},onRegisterGuard(){}});}
  root=createRoot(document.getElementById('root'));await act(async()=>{root.render(React.createElement(Host));await Promise.resolve()});assert.equal(commits,0);
  for(const names of [['A','B'],['C','B'],['B']]){await act(async()=>{update(w=>({...w,businesses:names}));await Promise.resolve()});assert.equal(commits,0,'display defaults must never be committed during business changes');}
  const select=[...document.querySelectorAll('select')].find(el=>el.getAttribute('aria-label')?.includes('执行方式'));assert.ok(select);fail=true;
  await act(async()=>{select.value='自动运行';select.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await Promise.resolve()});assert.equal(select.value,'自动运行');
  await act(async()=>{update(w=>({...w,businesses:['D']}));await Promise.resolve()});assert.equal(select.value,'自动运行');assert.match(document.body.textContent,/尚未可靠保存/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d]of previous){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});
