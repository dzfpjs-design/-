import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {randomUUID} from 'node:crypto';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('任务设备证据保存失败后刷新，核对原请求并恢复服务端保存结果',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});
 Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(_key,_opts,fn)=>fn({})}});
 const require=createRequire(import.meta.url),React=require('react'),{act}=React,{createRoot}=require('react-dom/client');
 const built=await build({entryPoints:['src/NativeSensorEvidence.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),module={exports:{}};
 new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
 const Component=module.exports.default,Btn=({children,...props})=>React.createElement('button',props,children),account={workspace_id:'w',user_id:'u',role:'admin'},task={id:'task'};
 const record={kind:'data',id:randomUUID(),record_hash:'a'.repeat(64),object_name:'林地',field_name:'温度',value:28,unit:'℃',device_name:'温度计',source_timestamp:null,received_at:'2026-11-14T08:00:00Z',quality:'unverified',freshness:'unknown'};
 let packet=null,original=null,posts=0,contextUnavailable=false;
 const request=async(path,opts={})=>{
  if(path.endsWith('/context')){if(contextUnavailable)throw Error('原来源不可用');return {task_version:3,records:[record],packets:packet?[packet]:[],has_more:false,cloud_analysis_supported:false}}
  if(path.includes('/packets?page='))return {page:Number(path.split('=').at(-1)),packets:packet?[packet]:[],has_more:false};
  if(path.endsWith('/packets')){posts++;original=JSON.parse(opts.body);packet={id:'packet',created_at:'2026-11-14T08:00:00Z',records:[record],notice:'读数待核实'};throw Error('response lost')}
  if(path.includes('/requests/'))return {state:'saved',packet};throw Error(path);
 };
 let root=createRoot(document.getElementById('root'));
 const wait=()=>new Promise(resolve=>setTimeout(resolve,0));
 const render=()=>act(async()=>{root.render(React.createElement(Component,{account,task,Btn,request}));await wait()});
 const click=async label=>{const button=[...document.querySelectorAll('button')].find(b=>b.textContent===label);assert.ok(button,label);await act(async()=>{button.click();await wait()})};
 try{
  await render();await click('查看设备证据');assert.match(document.body.textContent,/云端分析需另行核对并授权/);
  await act(async()=>{document.querySelector('input[type=checkbox]').click();await wait()});await click('保存所选设备证据');
  assert.equal(posts,1);assert.equal(original.selection[0].record_hash,record.record_hash);assert.match(document.body.textContent,/尚未核对/);
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();await click('查看设备证据');
  assert.match(document.body.textContent,/上次保存结果尚待核对/);await click('核对原请求');
  assert.equal(posts,1);assert.match(document.body.textContent,/设备证据包已保存/);assert.match(document.body.textContent,/林地/);
  assert.equal(localStorage.getItem('sensor-packet:w:u:task:pending'),null);
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();await click('查看设备证据');assert.match(document.body.textContent,/已保存的证据包/);assert.match(document.body.textContent,/28 ℃/);
  contextUnavailable=true;await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();await click('查看设备证据');assert.match(document.body.textContent,/原来源不可用/);assert.match(document.body.textContent,/28 ℃/);
 }finally{await act(async()=>root.unmount());dom.window.close()}
});

test('管理员可以只读查看其他负责人已保存的设备证据',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(_key,_opts,fn)=>fn({})}});
 const require=createRequire(import.meta.url),React=require('react'),{act}=React,{createRoot}=require('react-dom/client');const built=await build({entryPoints:['src/NativeSensorEvidence.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),module={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,module,module.exports);
 const Btn=({children,...props})=>React.createElement('button',props,children),record={kind:'data',id:randomUUID(),object_name:'林地',field_name:'温度',value:28,unit:'℃',device_name:'温度计',source_timestamp:null,quality:'unverified',freshness:'unknown'},packet={id:'one',created_at:'2026-11-14T08:00:00Z',records:[record],notice:'待核实'};
 const request=async(path)=>path.endsWith('/context')?{task_version:1,records:[],packets:[],has_more:false,cloud_analysis_supported:false,read_only:true}:path.includes('/packets?page=')?{page:1,packets:[packet],has_more:false}:Promise.reject(Error(path));const root=createRoot(document.getElementById('root'));
 try{await act(async()=>{root.render(React.createElement(module.exports.default,{account:{workspace_id:'w',user_id:'admin',role:'admin'},task:{id:'task'},Btn,request}));await new Promise(r=>setTimeout(r,0))});await act(async()=>{[...document.querySelectorAll('button')].find(b=>b.textContent==='查看设备证据').click();await new Promise(r=>setTimeout(r,0))});assert.match(document.body.textContent,/其他负责人的任务/);assert.match(document.body.textContent,/28 ℃/);assert.equal([...document.querySelectorAll('button')].some(b=>b.textContent==='保存所选设备证据'),false)}finally{await act(async()=>root.unmount());dom.window.close()}
});
