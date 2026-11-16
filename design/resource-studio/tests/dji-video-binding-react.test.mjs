import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';
import {webcrypto} from 'node:crypto';

test('视频关联响应丢失后按原请求恢复且不重复创建',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','crypto','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'crypto',{value:webcrypto,configurable:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_k,_opts,fn)=>fn({})}},configurable:true});
 let receipt=null,calls=0;const row={id:'binding',version:1,enabled:true,available:true,connection_name:'模拟机场',channel_name:'模拟视频',notice:'不自动启动推流'};
 const request=async(path,options={})=>{if(path.startsWith('/dji/video-bindings/options'))return {connections:[{id:'c',name:'模拟机场',device_sn:'SIM',version:1}],channels:[{id:'v',name:'模拟视频',version:2}]};if(path==='/dji/video-bindings?page=1')return {bindings:receipt?[row]:[],has_more:false};if(path==='/dji/video-bindings'&&options.method==='POST'){calls++;const b=JSON.parse(options.body);receipt={request_id:b.request_id,state:'succeeded',binding:row};throw Error('网络中断')};if(path.startsWith('/dji/video-bindings/operations/'))return receipt;throw Error('未预期请求')};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});let root;
 try{const Component=(await server.ssrLoadModule('/src/DjiVideoBindings.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,...props})=>React.createElement('button',props,children),props={account:{workspace_id:'w',user_id:'u',role:'admin'},Btn,request};root=createRoot(document.getElementById('root'));
 await act(async()=>{root.render(React.createElement(Component,props));await Promise.resolve();await Promise.resolve()});
 const choose=async(label,value)=>act(async()=>{const e=document.querySelector(`[aria-label="${label}"]`);e.value=value;e.dispatchEvent(new dom.window.Event('change',{bubbles:true}));await Promise.resolve()});
 await choose('视频关联机场','c');await choose('视频关联通道','v');const checkbox=document.querySelector('input[type=checkbox]');await act(async()=>{checkbox.click();await Promise.resolve()});
 const button=t=>[...document.querySelectorAll('button')].find(x=>x.textContent===t);await act(async()=>{button('保存关联').click();await Promise.resolve();await Promise.resolve();await Promise.resolve()});assert.equal(calls,1);assert.match(document.body.textContent,/上次关联操作结果待核对/);
 await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));const downgraded={...props,account:{...props.account,role:'viewer'}};await act(async()=>{root.render(React.createElement(Component,downgraded));await Promise.resolve();await Promise.resolve()});assert.match(document.body.textContent,/上次关联操作结果待核对/);assert.doesNotMatch(document.body.textContent,/已关联的视频/);
 await act(async()=>{button('核对原请求').click();await Promise.resolve();await Promise.resolve();await Promise.resolve()});assert.equal(calls,1);assert.doesNotMatch(document.body.textContent,/上次关联操作结果待核对/);assert.equal(localStorage.getItem('dji-video:w:u:pending'),null);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k]}}
});

test('视频帧解析跨分片并拒绝错误媒体数据',async()=>{
 const {readDjiFrames}=await import('../src/dji-live-frames.mjs');let seen=[];
 const encoder=new TextEncoder(),header=encoder.encode('--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 5\r\n\r\n'),raw=new Uint8Array(header.length+7);raw.set(header);raw.set([255,216,1,255,217,13,10],header.length);
 const response=new Response(new ReadableStream({start(c){c.enqueue(raw.slice(0,10));c.enqueue(raw.slice(10));c.close()}}),{headers:{'content-type':'multipart/x-mixed-replace; boundary=frame'}});
 await readDjiFrames(response,x=>seen.push(x));assert.equal(seen.length,1);assert.deepEqual([...seen[0]],[255,216,1,255,217]);
 await assert.rejects(()=>readDjiFrames(new Response('bad',{headers:{'content-type':'text/plain'}}),()=>{}));
});
