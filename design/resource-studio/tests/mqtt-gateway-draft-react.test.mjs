import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act,useRef,useState} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('browsing MQTT makes no draft write; the first edit is saved and restored without overwriting existing drafts',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/setup/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT','__mqttFixture'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 globalThis.__mqttFixture={calls:0,last:null,initial:{mqttGatewayDrafts:{other:{name:'已有草稿'}}}};
 const mocked=['MqttAssociationsPanel','MqttReceptionPanel','MqttDailyStudio'];
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'mqtt-draft-fixtures',enforce:'pre',resolveId(id){if(/(?:^|\/)runtime-api(?:\.js)?$/.test(id))return '\0mqtt-api';if(mocked.some(n=>id==='./'+n||id==='./'+n+'.jsx'))return '\0mqtt-component';},load(id){if(id==='\0mqtt-api')return `export const json=async()=>[]; export const api=async()=>({json:async()=>[],headers:new Map()}); export const hashBytes=async()=>'';`;if(id==='\0mqtt-component')return `import React from 'react';export default ()=>React.createElement('div');`;}}]});
 let root;
 try{
  const Studio=(await server.ssrLoadModule('/src/MqttStudio.jsx')).default;const {createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));
  const Btn=({children,primary,...p})=>React.createElement('button',p,children),Field=({label,children})=>React.createElement('label',null,label,children);
  function Harness(){const [workspace,setWorkspace]=useState(globalThis.__mqttFixture.initial);const ref=useRef(workspace);ref.current=workspace;return React.createElement(Studio,{account:{role:'admin',workspace_id:'ws',user_id:'u'},workspace,Btn,Field,onSave:async()=>{},onCommit:fn=>{const next=fn(ref.current);ref.current=next;globalThis.__mqttFixture.calls++;globalThis.__mqttFixture.last=next;setWorkspace(next);return true;}});}
  await act(async()=>{root.render(React.createElement(Harness));await Promise.resolve();await Promise.resolve();});
  assert.equal(globalThis.__mqttFixture.calls,0,'loading and browsing must not create a server configuration');
  const input=document.querySelector('input[placeholder="例如：北侧气象站"]');assert.ok(input);
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(input,'我的传感器');input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));});
  assert.equal(globalThis.__mqttFixture.calls,1);const saved=globalThis.__mqttFixture.last;
  assert.equal(saved.mqttGatewayDrafts.new.name,'我的传感器');assert.equal(saved.mqttGatewayDrafts.other.name,'已有草稿');assert.ok(saved.mqttGatewayDrafts.new.requestId);
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));globalThis.__mqttFixture.initial=saved;globalThis.__mqttFixture.calls=0;
  await act(async()=>{root.render(React.createElement(Harness));await Promise.resolve();await Promise.resolve();});
  assert.equal(globalThis.__mqttFixture.calls,0,'restoring an existing draft must not create or overwrite it');
  assert.equal(document.querySelector('input[placeholder="例如：北侧气象站"]').value,'我的传感器');
  assert.equal(globalThis.__mqttFixture.initial.mqttGatewayDrafts.new.requestId,saved.mqttGatewayDrafts.new.requestId);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,d] of old){if(d)Object.defineProperty(globalThis,k,d);else delete globalThis[k];}}
});
