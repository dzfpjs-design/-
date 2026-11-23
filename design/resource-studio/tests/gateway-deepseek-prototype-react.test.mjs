import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {initialWorkspace} from '../src/catalog.js';
import {makeGatewayForm,gatewayRecord,commitGateway} from '../src/gateway-schema.js';

test('DeepSeek gateway prototype explains local scope without showing a fictitious quota',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','location','history','localStorage','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.location=dom.window.location;globalThis.history=dom.window.history;globalThis.localStorage=dom.window.localStorage;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{
  const {default:Gateway}=await server.ssrLoadModule('/src/GatewayStudio.jsx');
  const {createRoot}=await import('react-dom/client');root=createRoot(document.getElementById('root'));
  const form=makeGatewayForm(null,'models');Object.assign(form,{protocol:'DeepSeek',name:'合成模型连接',settings:{budget:17,overBudget:'等待人工调整额度'}});
  const workspace=commitGateway(initialWorkspace(),gatewayRecord(form,'synthetic-connection'));
  const Btn=({children,icon,primary,ghost,...props})=>React.createElement('button',props,children);
  const Field=({children,label})=>React.createElement('label',null,label,children);
  await act(async()=>root.render(React.createElement(Gateway,{workspace,initialCategory:'models',initialConnection:'synthetic-connection',onCommit:()=>true,onBack:()=>{},Icon:()=>null,Btn,Field,Dialog:()=>null})));
  const tab=[...document.querySelectorAll('button')].find(node=>node.textContent.includes('运行策略'));assert.ok(tab);
  await act(async()=>tab.click());const text=document.getElementById('root').textContent;
  assert.match(text,/本页草稿不会更改真实服务配置/);assert.match(text,/历史金额设置仍保留/);
  assert.doesNotMatch(text,/每日费用上限（元）/);assert.doesNotMatch(text,/超限处理/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [key,descriptor] of old){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
});
