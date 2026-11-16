import test from 'node:test';import assert from 'node:assert/strict';import React,{act} from 'react';import {JSDOM} from 'jsdom';import {createServer} from 'vite';
async function setup({request,account={workspace_id:'w',user_id:'u',role:'admin'},pending=null,component='SensorAutomationStudio'}){
 const dom=new JSDOM('<div id="root"></div>',{url:'http://localhost/setup/'}),keys=['window','document','navigator','location','localStorage','sessionStorage','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 Object.assign(globalThis,{window:dom.window,document:dom.window.document,location:dom.window.location,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true});Object.defineProperty(globalThis,'navigator',{value:{userAgent:dom.window.navigator.userAgent,locks:{request:async(_key,_opts,fn)=>fn()}},configurable:true});
 if(pending)localStorage.setItem('sensor-auto:w:u:pending',JSON.stringify(pending));
 const vite=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true,include:[]},appType:'custom'});const Component=(await vite.ssrLoadModule('/src/'+component+'.jsx')).default,{createRoot}=await import('react-dom/client');
 const Btn=({children,primary,...props})=>React.createElement('button',props,children),root=createRoot(document.getElementById('root'));await act(async()=>{root.render(React.createElement(Component,{account,Btn,request}));await Promise.resolve();await Promise.resolve()});
 return {async cleanup(){await act(async()=>root.unmount());await vite.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}};
}
const click=text=>[...document.querySelectorAll('button')].find(x=>x.textContent.includes(text));
test('demoted user can safely end a preserved original request',async()=>{
 const pending={scope:'w:u',body:{request_id:'7f02599e-4126-4d65-b3b4-fc6e91db5541',protocol:'modbus',connection_id:'abc',enabled:true}};const requests=[];
 const ui=await setup({account:{workspace_id:'w',user_id:'u',role:'viewer'},pending,request:async(path)=>{requests.push(path);return {request_id:pending.body.request_id,state:'cancelled'}}});
 try{assert.match(document.body.textContent,/上次设置结果尚待核对/);assert.ok(!click('保存并启用'));await act(async()=>{click('核对并安全结束原请求').click();await Promise.resolve();await Promise.resolve()});assert.equal(localStorage.getItem('sensor-auto:w:u:pending'),null);assert.equal(requests.length,1);assert.match(requests[0],/cancel$/)}finally{await ui.cleanup()}
});
test('bad recovery receipt keeps the original request',async()=>{
 const pending={scope:'w:u',body:{request_id:'7f02599e-4126-4d65-b3b4-fc6e91db5541',protocol:'modbus',connection_id:'abc',enabled:true}};const ui=await setup({account:{workspace_id:'w',user_id:'u',role:'viewer'},pending,request:async()=>({request_id:'other',state:'cancelled'})});
 try{await act(async()=>{click('核对并安全结束原请求').click();await Promise.resolve();await Promise.resolve()});assert.ok(localStorage.getItem('sensor-auto:w:u:pending'));assert.match(document.body.textContent,/原请求仍待核对/)}finally{await ui.cleanup()}
});
test('business inbox labels candidates and only acknowledges explicitly',async()=>{
 const alert={id:'a',version:1,object_name:'林地 A',field_name:'温度',device_name:'气象站',value:26,unit:'℃',threshold:{operator:'above',value:25},status:'unconfirmed',quality:'unverified',freshness:'unknown',received_at:'2026-01-01T00:00:00Z'};const sent=[];
 const ui=await setup({component:'SensorAlertsPanel',request:async(path,opts)=>{sent.push([path,opts]);if(path==='/sensor-automation/alerts')return {alerts:[alert],unverified_count:0};return {alert:{...alert,status:'acknowledged'}}}});
 try{assert.match(document.body.textContent,/林地 A · 温度/);assert.match(document.body.textContent,/不能作为现场结论/);assert.equal(sent.filter(x=>x[1]?.method==='POST').length,0);await act(async()=>{click('确认收到').click();await Promise.resolve();await Promise.resolve()});assert.equal(sent.filter(x=>x[1]?.method==='POST').length,1)}finally{await ui.cleanup()}
});
test('late polling response cannot restore an already acknowledged alert',async()=>{
 const oldSet=globalThis.setInterval,oldClear=globalThis.clearInterval;let poll,resolveOld,count=0;
 globalThis.setInterval=(callback)=>{poll=callback;return 100};globalThis.clearInterval=()=>{};
 const alert={id:'a',version:1,object_name:'林地 A',field_name:'温度',device_name:'气象站',value:26,unit:'℃',threshold:{operator:'above',value:25},status:'unconfirmed',quality:'unverified',freshness:'unknown',received_at:'2026-01-01T00:00:00Z'};
 const delayed=new Promise(resolve=>{resolveOld=resolve});const request=async(path,opts)=>{if(opts?.method==='POST')return {alert:{...alert,status:'acknowledged',version:2}};count++;if(count===2)return delayed;return {alerts:[count===1?alert:{...alert,status:'acknowledged',version:2}],unverified_count:0}};
 let ui;try{ui=await setup({component:'SensorAlertsPanel',request});assert.ok(click('确认收到'));await act(async()=>{void poll();await Promise.resolve()});await act(async()=>{click('确认收到').click();await Promise.resolve();await Promise.resolve()});assert.match(document.body.textContent,/已确认收到/);await act(async()=>{resolveOld({alerts:[alert],unverified_count:0});await Promise.resolve();await Promise.resolve()});assert.match(document.body.textContent,/已确认收到/);assert.equal(click('确认收到'),undefined)}finally{if(ui)await ui.cleanup();globalThis.setInterval=oldSet;globalThis.clearInterval=oldClear}
});
