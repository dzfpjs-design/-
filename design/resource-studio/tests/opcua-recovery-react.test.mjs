import test from 'node:test';import assert from 'node:assert/strict';import React,{act} from 'react';import {JSDOM} from 'jsdom';import {createServer} from 'vite';
const defer=()=>{let resolve;const promise=new Promise(r=>{resolve=r});return {promise,resolve}};
async function setup(transport,account={workspace_id:'w',user_id:'u',role:'admin'},pending=null){
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_key,_opts,fn)=>fn()}},configurable:true});
 if(pending)localStorage.setItem('opcua-pending:w:u',JSON.stringify(pending));const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});
 const Component=(await server.ssrLoadModule('/src/OpcuaGatewayStudio.jsx')).default,{createRoot}=await import('react-dom/client');const Btn=({children,...props})=>React.createElement('button',props,children),root=createRoot(document.getElementById('root'));
 await act(async()=>{root.render(React.createElement(Component,{account,Btn,transport}));await Promise.resolve();await Promise.resolve()});return {async cleanup(){await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}};
}
function button(text){return [...document.querySelectorAll('button')].find(x=>x.textContent.includes(text));}
test('a demoted user retains an uncertain request until the same server receipt is confirmed',async()=>{
 const request_id='53f37e1b-07d2-4ea2-9d9c-91d2fc71c634',pending={scope:'w:u',type:'read',connectionId:'11111111-1111-4111-8111-111111111111',path:'/opcua/connections/11111111-1111-4111-8111-111111111111/readings',body:{request_id,version:1}};let answer='wrong';
 const transport=async(path)=>{if(path.includes('/operations/'))return {request_id:answer==='wrong'?'other':request_id,kind:'read',state:'cancelled'};throw Error('viewer must not access admin endpoints')};const ui=await setup(transport,{workspace_id:'w',user_id:'u',role:'viewer'},pending);
 try{await act(async()=>{button('核对并结束原请求').click();await Promise.resolve();await Promise.resolve()});assert.ok(localStorage.getItem('opcua-pending:w:u'));assert.match(document.body.textContent,/无法核对/);answer='valid';await act(async()=>{button('核对并结束原请求').click();await Promise.resolve();await Promise.resolve()});assert.equal(localStorage.getItem('opcua-pending:w:u'),null);assert.match(document.body.textContent,/原请求已结束/);}finally{await ui.cleanup()}
});
test('A-B-A selection ignores the first A response when it arrives last',async()=>{
 const a='11111111-1111-4111-8111-111111111111',b='22222222-2222-4222-8222-222222222222',ar=defer(),ab=defer();let aReads=0,aBindings=0;
 const transport=async(path)=>{if(path==='/opcua/grants')return {grants:[]};if(path==='/opcua/connections')return {connections:[{id:a,name:'A',source:{node_id:'ns=2;s=A'}},{id:b,name:'B',source:{node_id:'ns=2;s=B'}}]};
  if(path===`/opcua/connections/${a}/readings`)return ++aReads===1?ar.promise:{readings:[{id:'new',state:'succeeded',value:22,unit:'℃',quality:'good',status_name:'Good',received_at:'2026-01-01T00:00:00Z'}]};
  if(path===`/opcua/connections/${a}/binding`)return ++aBindings===1?ab.promise:{binding:{entity_id:'new',field:'新字段'},entities:[{id:'new',label:'新对象'}]};
  if(path===`/opcua/connections/${b}/readings`)return {readings:[]};if(path===`/opcua/connections/${b}/binding`)return {binding:null,entities:[]};throw Error(path)};
 const ui=await setup(transport);try{const select=[...document.querySelectorAll('select')].find(x=>x.textContent.includes('A'));for(const value of [a,b,a])await act(async()=>{select.value=value;select.dispatchEvent(new window.Event('change',{bubbles:true}));await Promise.resolve();await Promise.resolve()});
  await act(async()=>{ar.resolve({readings:[{id:'old',state:'succeeded',value:11,unit:'℃',quality:'good',status_name:'Good',received_at:'2026-01-01T00:00:00Z'}]});ab.resolve({binding:{entity_id:'old',field:'旧字段'},entities:[{id:'old',label:'旧对象'}]});await Promise.resolve();await Promise.resolve()});
  assert.match(document.body.textContent,/新对象/);assert.match(document.body.textContent,/新字段/);assert.doesNotMatch(document.body.textContent,/旧对象|旧字段|11 ℃/);assert.match(document.body.textContent,/22 ℃/);
 }finally{await ui.cleanup()}
});
