import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';

test('Modbus and OPC UA drafts are single-editor, restore after handoff, and keep secrets out of storage',async()=>{
 const dom=new JSDOM('<div id="one"></div><div id="two"></div>',{url:'http://localhost'});
 globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.sessionStorage=dom.window.sessionStorage;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});
 const held=new Set();Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(name,opt,fn)=>{
   if(held.has(name))return fn(null);held.add(name);try{return await fn({name})}finally{held.delete(name)}
 }}});
 globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const require=createRequire(import.meta.url),React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
 const Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
 for(const [entry,prefix] of [['ManagedModbusAuthorizations.jsx','managed-modbus-auth-draft:w:u'],['ManagedOpcuaAuthorizations.jsx','managed-opcua-auth-draft:w:u']]){
  const built=await build({entryPoints:['src/'+entry],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),loaded={exports:{}};
  new Function('require','module','exports',built.outputFiles[0].text)(require,loaded,loaded.exports);
  const Component=loaded.exports.default,root=createRoot(document.getElementById('one')),other=createRoot(document.getElementById('two'));
  const request=async()=>({enabled:true,vault_ready:true,networks:['192.168.0.0/24'],authorizations:[]});
  const account={workspace_id:'w',user_id:'u',role:'admin'};
  await act(async()=>{root.render(React.createElement(Component,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});
  const name=document.querySelector('#one form input'),setter=Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set;
  assert.equal(name.disabled,false);
  await act(async()=>{setter.call(name,'已保存的名称');name.dispatchEvent(new dom.window.Event('input',{bubbles:true}))});
  assert.equal(JSON.parse(localStorage.getItem(prefix)).name,'已保存的名称');
  await act(async()=>{other.render(React.createElement(Component,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});
  assert.match(document.getElementById('two').textContent,/另一标签页正在编辑/);
  assert.equal(document.querySelector('#two form fieldset').disabled,true);
  await act(async()=>{root.unmount();await new Promise(r=>setTimeout(r,0))});
  const retry=[...document.querySelectorAll('#two button')].find(x=>x.textContent==='重新获取编辑权');
  await act(async()=>{retry.click();await new Promise(r=>setTimeout(r,0))});
  const name2=document.querySelector('#two form input');assert.equal(name2.value,'已保存的名称');assert.equal(name2.disabled,false);
  const host=document.querySelectorAll('#two form input')[1];
  await act(async()=>{setter.call(host,'192.168.0.23');host.dispatchEvent(new dom.window.Event('input',{bubbles:true}))});
  const draft=JSON.parse(localStorage.getItem(prefix));assert.equal(draft.name,'已保存的名称');assert.equal(draft.host,'192.168.0.23');
  if(entry.startsWith('ManagedOpcua')){
   const input=document.querySelector('#two input[type=file]');
   Object.defineProperty(input,'files',{value:[new dom.window.File(['PRIVATE-TEST-KEY'],'test-key.pem')],configurable:true});
   await act(async()=>{input.dispatchEvent(new dom.window.Event('change',{bubbles:true}))});
   assert.equal([...Array(localStorage.length)].map((_,i)=>localStorage.getItem(localStorage.key(i))).join('').includes('PRIVATE-TEST-KEY'),false);
  }
  await act(async()=>{other.unmount();await new Promise(r=>setTimeout(r,0))});
 }
 dom.window.close();
});
