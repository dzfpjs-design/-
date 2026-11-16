import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {build} from 'vite';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let folder,compiled;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function component(){
 if(!compiled)compiled=(async()=>{
  folder=await mkdtemp(join(tmpdir(),'instance-capabilities-'));
  const reactURL=pathToFileURL(join(project,'node_modules/react/index.js')).href;
  const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),mode:'test',logLevel:'silent',define:{'process.env.NODE_ENV':JSON.stringify('development')},build:{ssr:join(project,'src/InstanceCapabilityOverview.jsx'),write:false,minify:false,rollupOptions:{external:id=>id==='react'||id===reactURL,output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?reactURL:id}}}});
  const file=join(folder,'component.mjs');await writeFile(file,result.output.find(r=>r.type==='chunk'&&r.isEntry).code);return(await import(pathToFileURL(file).href)).default;
 })();return compiled;
}
const instance={id:'i1',status:'partially_active',active_release_id:'r1',enabled_business_ids:['b1']};
const release={id:'r1',instance_id:'i1',snapshot:{resources:[{id:'v1',name:'已配置摄像头',category:'video'},{id:'m1',name:'已配置模型',category:'models'},{id:'p1',name:'nasa_modis',category:'maps',protocol:'PUBLIC_OBSERVATION',details:{public_source_id:'s1'}}],businesses:[{id:'b1',name:'日常业务',flow:{steps:[{id:'s1',name:'图像识别',execution:{adapter:'local_specialist'}}]}}]}};

test('真实组件只读当前实例，显示失败重试且快捷入口直接调用原操作',{concurrency:false,timeout:30000},async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true}),old=new Map();
 for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true})){
  old.set(k,Object.getOwnPropertyDescriptor(globalThis,k));Object.defineProperty(globalThis,k,{value:v,configurable:true,writable:true});
 }
 const calls=[],opened=[];let failed=true,root;
 const request=async(path)=>{calls.push(path);if(path.startsWith('/public-observations'))return {instance_id:'i1',sources:[{source_id:'s1',name:'NASA MODIS 卫星影像',status:'published'}]};if(failed)throw Error('offline');return {instance_id:'i1',sources:[{id:'v1',status:'available'}]};};
 const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
 try{
  const C=await component(),{createRoot}=await import('react-dom/client');root=createRoot(dom.window.document.getElementById('root'));
  const props={instanceId:'i1',instance,release,account:{user_id:'u1',workspace_id:'w1',role:'viewer'},Btn,request,onObservation:()=>opened.push('day'),onDevice:()=>opened.push('device'),onDocuments:()=>opened.push('docs'),onTasks:()=>opened.push('tasks')};
  const button=label=>[...dom.window.document.querySelectorAll('button')].find(x=>x.textContent===label);
  await act(async()=>root.render(React.createElement(C,props)));assert.equal(calls.length,0);
  const toggle=button('查看能力与观测'),panel=dom.window.document.getElementById(toggle.getAttribute('aria-controls'));
  assert.ok(panel);assert.equal(toggle.getAttribute('aria-expanded'),'false');assert.equal(panel.hidden,true);
  await act(async()=>button('查看能力与观测').click());
  assert.equal(button('收起能力与观测').getAttribute('aria-expanded'),'true');assert.equal(panel.hidden,false);
  assert.match(dom.window.document.body.textContent,/摄像头和 WMS 来源状态暂未读到/);
  assert.match(dom.window.document.body.textContent,/已配置模型/);assert.match(dom.window.document.body.textContent,/不等于设备在线/);
  assert.match(dom.window.document.body.textContent,/NASA MODIS 卫星影像/);assert.doesNotMatch(dom.window.document.body.textContent,/nasa_modis/);
  failed=false;await act(async()=>button('重试核对').click());
  assert.match(dom.window.document.body.textContent,/配置核验通过；在线状态待检查/);
  await act(async()=>{button('查看当日视频、音频与影像').click();button('查看设备数据与每日整理').click();button('管理运行期资料').click();button('查看业务任务').click();});assert.deepEqual(opened,['day','device','docs','tasks']);
  await act(async()=>root.render(React.createElement(C,{...props,onDevice:()=>false})));
  await act(async()=>button('查看设备数据与每日整理').click());assert.match(dom.window.document.querySelector('[role="alert"]').textContent,/入口暂不可用/);
  await act(async()=>button('管理运行期资料').click());assert.doesNotMatch(dom.window.document.body.textContent,/入口暂不可用/);
  assert.ok(calls.every(path=>path.includes('i1')));
  await act(async()=>root.render(React.createElement(C,{...props,instance:{...instance,status:'paused'}})));
  assert.match(dom.window.document.body.textContent,/已暂停/);assert.doesNotMatch(dom.window.document.body.textContent,/已配置摄像头/);assert.equal(button('查看业务任务').disabled,true);
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}
 }
});

test('切换实例后迟到的旧来源响应不会覆盖新实例',{concurrency:false,timeout:30000},async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/',pretendToBeVisual:true}),old=new Map();
 for(const [k,v] of Object.entries({window:dom.window,document:dom.window.document,navigator:dom.window.navigator,IS_REACT_ACT_ENVIRONMENT:true})){
  old.set(k,Object.getOwnPropertyDescriptor(globalThis,k));Object.defineProperty(globalThis,k,{value:v,configurable:true,writable:true});
 }
 const pending=[];let root;
 const request=path=>new Promise(resolve=>pending.push({path,resolve}));
 const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
 try{
  const C=await component(),{createRoot}=await import('react-dom/client');root=createRoot(dom.window.document.getElementById('root'));
  const props={instanceId:'i1',instance,release,account:{user_id:'u1',workspace_id:'w1',role:'viewer'},Btn,request};
  await act(async()=>root.render(React.createElement(C,props)));
  await act(async()=>[...dom.window.document.querySelectorAll('button')].find(x=>x.textContent==='查看能力与观测').click());
  assert.equal(pending.length,2);
  const nextInstance={...instance,id:'i2',active_release_id:'r2'},nextRelease={...release,id:'r2',instance_id:'i2',snapshot:{...release.snapshot,resources:[{id:'v2',name:'新实例摄像头',category:'video'}]}};
  await act(async()=>root.render(React.createElement(C,{...props,instanceId:'i2',instance:nextInstance,release:nextRelease})));
  assert.equal(pending.length,4);
  await act(async()=>{pending[2].resolve({instance_id:'i2',sources:[{id:'v2',status:'available'}]});pending[3].resolve({instance_id:'i2',sources:[]});});
  assert.match(dom.window.document.body.textContent,/新实例摄像头/);
  await act(async()=>{pending[0].resolve({instance_id:'i1',sources:[{id:'v1',status:'available'}]});pending[1].resolve({instance_id:'i1',sources:[]});});
  assert.match(dom.window.document.body.textContent,/新实例摄像头/);assert.doesNotMatch(dom.window.document.body.textContent,/已配置摄像头/);
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();for(const [key,value] of old){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}
 }
});
