import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';

test('discovery is explicitly started and confirms untrusted candidates without claiming video access',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','localStorage','crypto','__onvifFixture'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.localStorage=dom.window.localStorage;Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const state=globalThis.__onvifFixture={records:[],posts:[],uncertain:true};
 state.api=async(path,opts)=>{if(path==='/media/onvif/scans'&&!opts.method)return state.hideFromList?[]:state.records;if(path.startsWith('/media/onvif/scans/requests/')){const row=state.records.find(r=>r.request_id===decodeURIComponent(path.split('/').at(-1)));if(row)return row;const e=Error('未找到');e.status=404;throw e;}if(path==='/media/onvif/scans'){const data=JSON.parse(opts.body);state.posts.push(data);if(state.uncertain){state.uncertain=false;throw Error('断线');}state.records=[{id:'s1',workspace_id:'ws',actor_id:'u',request_id:data.request_id,checked_at:'2026-11-15T00:00:00Z',devices:[{candidate_id:'a'.repeat(32),name:'测试摄像头',source_ip:'192.168.1.2',service_address:'http://192.168.1.2/onvif'}],confirmed_ids:[]}];return state.records[0];}if(path==='/media/onvif/scans/s1/confirm'){state.records[0].confirmed_ids.push(JSON.parse(opts.body).candidate_ids[0]);return state.records[0];}throw Error(path);};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'onvif-fixture',enforce:'pre',resolveId(id){if(/(?:^|\/)runtime-api(?:\.js)?$/.test(id))return '\0onvif-api';},load(id){if(id==='\0onvif-api')return `export const json=(...a)=>globalThis.__onvifFixture.api(...a)`;}}]});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/OnvifDiscovery.jsx')).default;const {createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));const Btn=({children,...p})=>React.createElement('button',p,children);
  const render=async()=>act(async()=>{root.render(React.createElement(Component,{account:{role:'admin',workspace_id:'ws',user_id:'u'},Btn}));await Promise.resolve();await Promise.resolve();});
  const button=t=>[...document.querySelectorAll('button')].find(b=>b.textContent===t);const click=async t=>act(async()=>{assert.ok(button(t),t);button(t).click();await Promise.resolve();await Promise.resolve();});
  await render();assert.equal(state.posts.length,0);assert.equal(button('开始发现摄像头').disabled,true);
  await act(async()=>document.querySelector('input[type=checkbox]').click());await click('开始发现摄像头');assert.equal(state.posts.length,1);assert.ok(button('按原请求重试发现'));const first=state.posts[0].request_id;
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();assert.ok(button('按原请求重试发现'));await click('按原请求重试发现');assert.equal(state.posts[1].request_id,first);assert.match(document.body.textContent,/测试摄像头/);assert.match(document.body.textContent,/未经认证/);
  await click('记录为待接入设备');assert.match(document.body.textContent,/已记录候选；设备身份与画面仍需核验/);assert.equal(state.records[0].confirmed_ids.length,1);
  state.hideFromList=true;localStorage.setItem('ga-onvif-scan:ws:u',first);await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();assert.equal(localStorage.getItem('ga-onvif-scan:ws:u'),null);assert.match(document.body.textContent,/测试摄像头/);assert.equal(button('按原请求重试发现'),undefined,'old receipt must be found outside the latest 20 records');
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});

test('workspace switch discards late responses and binds requests to the original workspace',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','localStorage','__onvifFixture'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.localStorage=dom.window.localStorage;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 let finishOld;const calls=[];globalThis.__onvifFixture={api:async(path,opts)=>{calls.push(opts.headers['X-Workspace-ID']);return opts.headers['X-Workspace-ID']==='old'?new Promise(resolve=>{finishOld=resolve;}):[];}};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'onvif-scope',enforce:'pre',resolveId(id){if(/(?:^|\/)runtime-api(?:\.js)?$/.test(id))return '\0onvif-scope-api';},load(id){if(id==='\0onvif-scope-api')return `export const json=(...a)=>globalThis.__onvifFixture.api(...a)`;}}]});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/OnvifDiscovery.jsx')).default;const {createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));const Btn=({children,...p})=>React.createElement('button',p,children);
  const node=(workspace)=>React.createElement(Component,{key:workspace+':u:admin',account:{role:'admin',workspace_id:workspace,user_id:'u'},Btn});
  await act(async()=>{root.render(node('old'));await Promise.resolve();});
  await act(async()=>{root.render(node('new'));await Promise.resolve();});
  await act(async()=>{finishOld([{id:'oldscan',workspace_id:'old',checked_at:'2026-11-15',devices:[{candidate_id:'a'.repeat(32),name:'旧工作区设备',source_ip:'192.168.0.2'}],confirmed_ids:[]}]);await Promise.resolve();});
  assert.deepEqual(calls,['old','new']);assert.doesNotMatch(document.body.textContent,/旧工作区设备/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});
