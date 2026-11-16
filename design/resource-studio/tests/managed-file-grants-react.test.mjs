import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {JSDOM} from 'jsdom';
import {randomUUID} from 'node:crypto';

test('credentials never enter browser persistence and downgrade can safely resolve original request',async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});
 Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(_key,_options,cb)=>cb({})}});
 const require=createRequire(import.meta.url),React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
 const built=await build({entryPoints:['src/ManagedFileGrantStudio.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'});
 const loaded={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,loaded,loaded.exports);
 const Studio=loaded.exports.default,root=createRoot(document.getElementById('root'));let sent;
 const request=async(path,opts={})=>{if(path.endsWith('/grants')&&!opts.method)return {enabled:true,key_ready:true,networks:['192.168.0.0/16'],grants:[]};if(path.endsWith('/grants')){sent=JSON.parse(opts.body);throw Error('network disconnected')};if(path.endsWith('/cancel'))return {request_id:path.split('/').at(-2),state:'cancelled'};throw Error(path)};
 const Btn=({children,...props})=>React.createElement('button',props,children),account={workspace_id:'w',user_id:'u',role:'admin'};
 function field(label){return [...document.querySelectorAll('label')].find(e=>e.textContent.startsWith(label))?.querySelector('input')}
 async function fill(label,value){const node=field(label);assert.ok(node,label);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})}
 async function check(label){const node=field(label);assert.ok(node,label);await act(async()=>{node.click()})}
 try{
  await act(async()=>{root.render(React.createElement(Studio,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});
  await fill('授权名称','演示资料');await fill('服务器 IPv4 地址','192.168.1.5');await fill('被动数据端口起始','49000');await fill('被动数据端口结束','49100');await fill('账号','secret-user-734');await fill('密码','secret-password-934');await check('已确认服务器将此账号限制在授权目录');await check('已确认 FTP 账号、密码和文件明文传输');
  const button=[...document.querySelectorAll('button')].find(x=>x.textContent==='保存目录授权');await act(async()=>{button.click();await new Promise(r=>setTimeout(r,0))});
  assert.equal(sent.username,'secret-user-734');assert.equal(sent.password,'secret-password-934');
  const saved=Object.keys(localStorage).map(key=>localStorage.getItem(key)).join(' ');assert.doesNotMatch(saved,/secret-user-734|secret-password-934/);assert.match(saved,/request_id/);
  assert.match(document.body.textContent,/原请求已保留/);assert.doesNotMatch(document.body.textContent,/secret-password-934/);
  await act(async()=>{root.render(React.createElement(Studio,{account:{...account,role:'viewer'},Btn,request}));await new Promise(r=>setTimeout(r,0))});
  assert.match(document.body.textContent,/可核对或安全结束本人原请求/);
  const cancel=[...document.querySelectorAll('button')].find(x=>x.textContent==='安全结束原请求');await act(async()=>{cancel.click();await new Promise(r=>setTimeout(r,0))});
  assert.equal(localStorage.getItem('managed-file-grant:w:u:pending'),null);
 }finally{await act(async()=>root.unmount());dom.window.close();delete globalThis.window;delete globalThis.document;delete globalThis.localStorage;delete globalThis.navigator;delete globalThis.crypto}
});

test('one browser tab edits a draft while another must acquire the lock',async()=>{
 const dom=new JSDOM('<div id="a"></div><div id="b"></div>',{url:'http://localhost'});globalThis.window=dom.window;globalThis.document=dom.window.document;globalThis.localStorage=dom.window.localStorage;globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});Object.defineProperty(globalThis,'crypto',{value:{randomUUID},configurable:true});
 const held=new Set();Object.defineProperty(dom.window.navigator,'locks',{value:{request:async(key,options,cb)=>{if(held.has(key)&&options.ifAvailable)return cb(null);held.add(key);try{return await cb({})}finally{held.delete(key)}}}});
 const require=createRequire(import.meta.url),React=require('react'),{createRoot}=require('react-dom/client'),{act}=React;
 const built=await build({entryPoints:['src/ManagedFileGrantStudio.jsx'],bundle:true,format:'cjs',platform:'node',packages:'external',write:false,logLevel:'silent'}),loaded={exports:{}};new Function('require','module','exports',built.outputFiles[0].text)(require,loaded,loaded.exports);
 const Studio=loaded.exports.default,a=createRoot(document.querySelector('#a')),b=createRoot(document.querySelector('#b')),account={workspace_id:'w',user_id:'u',role:'admin'},Btn=({children,primary,...p})=>React.createElement('button',p,children),request=async()=>({enabled:true,key_ready:true,networks:['192.168.0.0/16'],grants:[]});
 try{
  await act(async()=>{a.render(React.createElement(Studio,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});
  await act(async()=>{b.render(React.createElement(Studio,{account,Btn,request}));await new Promise(r=>setTimeout(r,0))});
  assert.equal(document.querySelector('#a fieldset').disabled,false);assert.equal(document.querySelector('#b fieldset').disabled,true);
  const input=[...document.querySelectorAll('#a label')].find(x=>x.textContent.startsWith('授权名称')).querySelector('input');
  await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(input,'林场资料');input.dispatchEvent(new dom.window.Event('input',{bubbles:true}))});
  assert.match(localStorage.getItem('managed-file-grant:w:u:draft'),/林场资料/);
  await act(async()=>{a.unmount();await new Promise(r=>setTimeout(r,0))});
  await act(async()=>{[...document.querySelectorAll('#b button')].find(x=>x.textContent==='重新取得编辑权').click();await new Promise(r=>setTimeout(r,0))});
  assert.equal(document.querySelector('#b fieldset').disabled,false);assert.equal([...document.querySelectorAll('#b label')].find(x=>x.textContent.startsWith('授权名称')).querySelector('input').value,'林场资料');
 }finally{await act(async()=>b.unmount());dom.window.close();delete globalThis.window;delete globalThis.document;delete globalThis.localStorage;delete globalThis.navigator;delete globalThis.crypto}
});
