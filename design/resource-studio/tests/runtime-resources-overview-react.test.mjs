import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('overview routing keeps category save and unsaved-device guard',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/?view=resources'});
 const keys=['window','document','location','history','navigator','IS_REACT_ACT_ENVIRONMENT','__overviewTestJSON','__externalGuardBlocked','__ftpsGuardBlocked'];
 const original=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 for(const key of ['window','document','location','history','navigator'])Object.defineProperty(globalThis,key,{value:dom.window[key],configurable:true,writable:true});
 globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 globalThis.__externalGuardBlocked=true;
 globalThis.__ftpsGuardBlocked=true;
 globalThis.__overviewTestJSON=path=>Promise.resolve(path==='/workspace/device-gateways'?{connections:[],has_more:false}:[]);
 const mocked=['LocalModelsStudio','MqttStudio','LocalRulesStudio','BusinessDataStudio','DeviceGatewayStudioV268','GraphSourcesPanel','PublicObservationStudio','NativeGraphSourcesPanelV136','ExternalCapabilityList','MapAccessPanel','FeatureServicesStudio','MapServicesStudio','FtpsDocumentGateway'];
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'overview-navigation-fixtures',enforce:'pre',resolveId(id){
  if(/(?:^|\/)runtime-api(?:\.js)?$/.test(id))return '\0overview-navigation-api';
  const found=mocked.find(name=>id==='./'+name || id==='./'+name+'.jsx');if(found)return '\0fixture:'+found;
 },load(id){
  if(id==='\0overview-navigation-api')return 'export const json=(...args)=>globalThis.__overviewTestJSON(...args)';
  if(id.startsWith('\0fixture:')){const name=id.slice(9);return `import React from 'react';\nconst Fixture=props=>{React.useEffect(()=>{if(${JSON.stringify(name)}==='DeviceGatewayStudioV268'){props.onRegisterGuard(()=>false);return()=>props.onRegisterGuard(null)}if(${JSON.stringify(name)}==='ExternalCapabilityList'){const guard=()=>!globalThis.__externalGuardBlocked;props.onRegisterGuard(guard);return()=>props.onRegisterGuard(null,guard)}if(${JSON.stringify(name)}==='FtpsDocumentGateway'){const guard=()=>!globalThis.__ftpsGuardBlocked;props.onRegisterGuard(guard);return()=>props.onRegisterGuard(null,guard)}},[]);return React.createElement('div',{'data-fixture':${JSON.stringify(name)}},${JSON.stringify(name)})};\nexport default Fixture;export const NativeGraphSourceInstancesV136=Fixture;`;}
 }}]});
 let root;
 try{
  const Screen=(await server.ssrLoadModule('/src/RuntimeResources.jsx')).default,{createRoot}=await import('react-dom/client');root=createRoot(document.querySelector('#root'));
  let saves=0;const Btn=({children,primary,...rest})=>React.createElement('button',rest,children), Field=({children})=>React.createElement('label',null,children);
  const props={account:{workspace_id:'ws',user_id:'actor',role:'admin'},workspace:{},onGo:()=>{},onSave:async()=>{saves++},onCommit:()=>true,onRegisterGuard:()=>{},Btn,Field};
  await act(async()=>{root.render(React.createElement(Screen,props));await Promise.resolve();await Promise.resolve();});
  assert.match(document.body.textContent,/网关与能力管理/);assert.equal(document.querySelector('[data-fixture="GraphSourcesPanel"]'),null);
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='影像与地图').click();await Promise.resolve();});
  for(let i=0;i<8;i++)await act(async()=>{root.render(React.createElement(Screen,{...props,workspace:{revision:i}}));await Promise.resolve();});
  for(const name of ['MapAccessPanel','FeatureServicesStudio','MapServicesStudio'])assert.equal(document.querySelectorAll(`[data-fixture="${name}"]`).length,1,`${name} must not accumulate on updates`);
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='网关与能力总览').click();await Promise.resolve();});
  saves=0;
  const video=[...document.querySelectorAll('article')].find(x=>x.textContent.includes('摄像头与音视频'));
  await act(async()=>{video.querySelector('.resource-overview-action').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.get('category'),'video');assert.equal(saves,1);assert.ok(document.querySelector('[data-fixture="LocalModelsStudio"]'));
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='网关与能力总览').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.has('category'),false);assert.match(document.body.textContent,/网关与能力管理/);
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='通用与专业模型').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.get('category'),'models');
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='网关与能力总览').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.get('category'),'models');assert.match(document.body.textContent,/外部能力的当前编辑或原申请尚未可靠保存/);
  globalThis.__externalGuardBlocked=false;
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='网关与能力总览').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.has('category'),false);
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='文档与内容').click();await Promise.resolve();});
  assert.equal(document.querySelectorAll('[data-fixture="FtpsDocumentGateway"]').length,1);assert.match(document.body.textContent,/前往上传业务资料/);
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='网关与能力总览').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.get('category'),'docs');assert.match(document.body.textContent,/文档目录的输入或原请求尚未可靠保存/);
  globalThis.__ftpsGuardBlocked=false;await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='网关与能力总览').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.has('category'),false);
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='设备控制能力').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.get('category'),'control');const before=saves;
  await act(async()=>{[...document.querySelectorAll('nav button')].find(x=>x.textContent==='网关与能力总览').click();await Promise.resolve();});
  assert.equal(new URL(location.href).searchParams.get('category'),'control');assert.equal(saves,before);assert.match(document.body.textContent,/设备连接的当前编辑或原申请尚未可靠保存/);
 }finally{
  if(root)await act(async()=>root.unmount());await server.close();dom.window.close();
  for(const key of keys){const descriptor=original.get(key);if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}
 }
});
