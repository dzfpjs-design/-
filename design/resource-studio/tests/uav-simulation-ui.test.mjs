import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('visible draft survives remount, remains account scoped, and excludes another tab',async()=>{
  const dom=new JSDOM('<div id="one"></div><div id="two"></div>',{url:'http://localhost'});
  globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;
  Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
  const held=new Set();Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(name,opt,fn)=>{
    if(held.has(name))return fn(null);held.add(name);try{return await fn({name})}finally{held.delete(name)}
  }}});
  globalThis.IS_REACT_ACT_ENVIRONMENT=true;
  const require=createRequire(import.meta.url),React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
  const built=await build({entryPoints:['src/UavSimulationStudio.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'});
  const loaded={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,loaded,loaded.exports);
  const Studio=loaded.exports.default,root=createRoot(document.getElementById('one')),other=createRoot(document.getElementById('two'));
  const request=async()=>({missions:[]}),account={workspace_id:'w1',user_id:'u1',role:'admin'};
  await act(async()=>{root.render(React.createElement(Studio,{account,request}));await new Promise(r=>setTimeout(r,0))});
  const input=document.querySelector('#one [aria-label="事件名称"]');
  const setter=Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set;
  await act(async()=>{setter.call(input,'核查烟雾');input.dispatchEvent(new dom.window.Event('input',{bubbles:true}))});
  assert.equal(JSON.parse(localStorage.getItem('uav-simulation:w1:u1:draft-v1')).fields.event,'核查烟雾');
  await act(async()=>{other.render(React.createElement(Studio,{account,request}));await new Promise(r=>setTimeout(r,0))});
  assert.match(document.getElementById('two').textContent,/另一标签页/);
  assert.equal(document.querySelector('#two [aria-label="事件名称"]').disabled,true);
  await act(async()=>{root.render(React.createElement(Studio,{account:{...account,user_id:'u2'},request}));await new Promise(r=>setTimeout(r,0))});
  assert.equal(document.querySelector('#one [aria-label="事件名称"]').value,'');
  await act(async()=>{root.render(React.createElement(Studio,{account,request}));await new Promise(r=>setTimeout(r,0))});
  assert.equal(document.querySelector('#one [aria-label="事件名称"]').value,'核查烟雾');
  await act(async()=>{root.unmount();other.unmount()});dom.window.close();
});
