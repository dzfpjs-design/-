import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToString} from 'react-dom/server';
import {createServer} from 'vite';
test('empty and proposed supplement screens render before a permission preview is available',async()=>{
 const priorStorage=globalThis.sessionStorage;globalThis.sessionStorage={getItem:()=>null,setItem:()=>{}};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom',plugins:[{name:'no-network-render',enforce:'pre',resolveId(id){if(id.endsWith('runtime-api.js'))return '\0render-api'},load(id){if(id==='\0render-api')return 'export const json=()=>{throw Error("no network in static render")};'}}]});
 try{
  const Screen=(await server.ssrLoadModule('/src/AutomaticReadStudio.jsx')).default,Parent=(await server.ssrLoadModule('/src/SupplementStudio.jsx')).default;
  const Btn=({children,...rest})=>React.createElement('button',rest,children),Field=({label,children})=>React.createElement('label',{},label,children);
  const props={instanceId:'i',account:{workspace_id:'w',username:'u',role:'admin'},Btn,Field,assessment:{id:'a',status:'completed',result:{objects:[]}}};
  for(const plan of [undefined,{id:'p',proposal:{actions:[{key:'k',object_name:'场景',resource:{channel_name:'通道'}}]}}]){const html=renderToString(React.createElement(Screen,{...props,plan}));assert.match(html,/有限的自动读取授权/);assert.match(html,/没有已保存的自动读取授权/)}
  const protectedHTML=renderToString(React.createElement(Parent,props));assert.match(protectedHTML,/核对当前账号与原批准范围/);assert.doesNotMatch(protectedHTML,/补充读取一次|有限的自动读取授权/);
 }finally{await server.close();if(priorStorage===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorStorage}
});
