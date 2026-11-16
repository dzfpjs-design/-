import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('gateway guide directs users to the real entry, without creating or replacing data',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div><button id="video-gateway-add">添加视频网关</button>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const {default:Guide,GATEWAY_GUIDES} = await server.ssrLoadModule('/src/GatewayGuide.jsx');
  const {createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));
  let clicked=0,upload=0;document.getElementById('video-gateway-add').addEventListener('click',()=>clicked++);
  const Btn=({children,...props})=>React.createElement('button',props,children);
  await act(async()=>root.render(React.createElement(Guide,{category:'video',onUpload:()=>upload++,Btn})));
  assert.match(document.body.textContent,/发现或配置不代表画面已验证/);
  const discovery=document.createElement('section');discovery.id='onvif-discovery';discovery.tabIndex=-1;document.body.append(discovery);
  await act(async()=>document.querySelectorAll('#root button')[1].click());assert.equal(document.activeElement.id,'onvif-discovery');
  await act(async()=>document.querySelector('#root button').click());
  assert.equal(document.activeElement.id,'video-gateway-add');assert.equal(clicked,0,'navigation must not create or mutate a connection');
  await act(async()=>root.render(React.createElement(Guide,{category:'maps',onUpload:()=>upload++,Btn})));
  await act(async()=>document.querySelector('#root button').click());assert.match(document.querySelector('#root').textContent,/尚未读取完成/);
  const details=document.createElement('details');details.innerHTML='<summary id="wms-gateway-add">添加地图服务</summary><input aria-label="地图服务名称">';document.body.append(details);
  await act(async()=>document.querySelector('#root button').click());assert.equal(details.open,true,'WMS form must be opened, not just the folded summary focused');assert.equal(document.activeElement.id,'wms-gateway-add');
  const select=document.createElement('select');select.id='mqtt-gateway-select';select.innerHTML='<option value="new">新增传感接入</option><option value="existing" selected>已有来源</option>';document.body.append(select);
  await act(async()=>root.render(React.createElement(Guide,{category:'sensor',onUpload:()=>upload++,Btn})));
  assert.match(document.querySelector('#root').textContent,/选择“新增传感接入”/);
  await act(async()=>document.querySelector('#root button').click());assert.equal(select.value,'existing','navigation must not replace a selected or unsaved gateway');assert.equal(document.activeElement.id,'mqtt-gateway-select');
  await act(async()=>root.render(React.createElement(Guide,{category:'docs',onUpload:()=>upload++,Btn})));
  await act(async()=>document.querySelector('#root button').click());assert.equal(upload,1);
  const ftps=document.createElement('details');ftps.id='ftps-document-add';ftps.innerHTML='<summary>添加已授权目录连接</summary>';document.body.append(ftps);
  await act(async()=>[...document.querySelectorAll('#root button')].find(x=>x.textContent==='从已授权目录导入').click());assert.equal(upload,1);assert.equal(ftps.open,true,'FTPS entry opens the actual connection form');
  assert.equal(Object.keys(GATEWAY_GUIDES).length,8);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});
