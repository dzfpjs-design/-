import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';

test('enrollment requires explicit authorization and preserves an uncertain request across remount',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','localStorage','sessionStorage','crypto','__cameraFixture'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const state=globalThis.__cameraFixture={posts:[],lost:true};
 state.api=async(path,opts={})=>{
  assert.equal(opts.headers['X-Workspace-ID'],'ws');
  if(path==='/media/gateways')return [{id:'gw',name:'测试网关'}];
  if(path.startsWith('/media/onvif-enrollments/options?'))return {credential_references:['GA_ONVIF_CAMERA_SECRET_FIXTURE_TEST'],gateway_version:4};
  if(path==='/media/onvif-enrollments'){
   const body=JSON.parse(opts.body);state.posts.push(body);if(state.lost){state.lost=false;throw Error('结果未知');}
   state.last=body;
   return {request_id:body.request_id,state:'configured',scan_id:body.scan_id,candidate_id:body.candidate_id,gateway_id:body.gateway_id};
  }
  if(path.endsWith('/cancel')){const body=JSON.parse(opts.body);return {request_id:body.request_id,state:'cancelled',scan_id:body.scan_id,candidate_id:body.candidate_id,gateway_id:body.gateway_id};}
  if(path.startsWith('/media/onvif-enrollments/requests/')){const body=state.last;return {request_id:body.request_id,state:'configured',scan_id:body.scan_id,candidate_id:body.candidate_id,gateway_id:body.gateway_id,gateway_state:'missing'};}
  throw Error('unexpected fixture call');
 };
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'camera-fixture',enforce:'pre',resolveId(id){if(/(?:^|\/)runtime-api(?:\.js)?$/.test(id))return '\0camera-api';},load(id){if(id==='\0camera-api')return 'export const json=(...a)=>globalThis.__cameraFixture.api(...a)';}}]});let root;
 try{
  const Component=(await server.ssrLoadModule('/src/OnvifCameraEnrollment.jsx')).default;const {createRoot}=await import('react-dom/client');const Btn=({children,...p})=>React.createElement('button',p,children);
  const props={account:{role:'admin',workspace_id:'ws',user_id:'u'},scan:{id:'scan'},device:{candidate_id:'a'.repeat(32)},Btn};
  const render=async()=>act(async()=>{root.render(React.createElement(Component,props));await Promise.resolve();await Promise.resolve();});
  const click=async element=>act(async()=>{assert.ok(element);element.click();await Promise.resolve();await Promise.resolve();});
  const button=t=>[...document.querySelectorAll('button')].find(b=>b.textContent===t);
  root=createRoot(document.getElementById('root'));await render();await click(document.querySelector('summary'));
  const select=async(label,value)=>act(async()=>{const e=document.querySelector(`[aria-label="${label}"]`);e.value=value;e.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await Promise.resolve();await Promise.resolve();});
  await select('选择摄像头接入的视频网关','gw');await select('摄像头凭据引用','GA_ONVIF_CAMERA_SECRET_FIXTURE_TEST');
  assert.equal(button('配置摄像头到视频网关').disabled,true);const boxes=[...document.querySelectorAll('input[type=checkbox]')].filter(e=>e.parentElement.textContent.includes('我已核实设备身份，允许')||e.parentElement.textContent.includes('我允许向所选网关写入'));await click(boxes[0]);await click(boxes[1]);await click(button('配置摄像头到视频网关'));
  assert.equal(state.posts.length,1);assert.ok(button('结束待核对请求'));const requestID=state.posts[0].request_id;
  const currentKey=[...Array(localStorage.length)].map((_,i)=>localStorage.key(i)).find(k=>k.startsWith('ga-onvif-enrollment:'));
  const closedKey='ga-onvif-enrollment:ws:u:scan:'+('a'.repeat(32))+':closed-tab';localStorage.setItem(closedKey,localStorage.getItem(currentKey));sessionStorage.clear();
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));await render();
  await click(button('读取本机接入记录'));await select('选择本机摄像头接入记录',closedKey);await click(button('恢复所选请求'));assert.ok(button('结束待核对请求'));
  await click(button('按原请求重试'));assert.equal(state.posts[1].request_id,requestID);assert.match(document.body.textContent,/仍需读取通道并验证画面/);
  await click(button('核对原接入和网关'));assert.match(document.body.textContent,/网关中未找到原通道/);await click(button('恢复原网关通道'));assert.equal(state.posts[2].request_id,requestID);
  assert.doesNotMatch([...Array(localStorage.length)].map((_,i)=>localStorage.getItem(localStorage.key(i))).join(' '),/fixture-password/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});
