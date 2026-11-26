import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('preparation survives a lost reply and reload without resending or claiming analysis',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  root=createRoot(document.getElementById('root'));let phase='',lost=true,lostPrepare=true,unsafePreview=true,authorized={},policyBodies=[],prepares=0,previews=0,requestId='';
  const source={title:'批准资料',selection:{kind:'approved_source_set',source_set_ref:{source_set_id:'one',source_set_hash:'hash'}}};
  const ctx={enabled:true,can_prepare:true,can_start:false,sources:[source],goal_hash:'g',head_hash:'h',expires_at:'2026-11-26T00:00:00Z',max_tokens:1024,model:'DeepSeek'};
  const request=async(path,options={})=>{
   const body=options.body?JSON.parse(options.body):null;
   if(path.endsWith('/availability'))return {enabled:true};
   if(path.endsWith('/context'))return ctx;
   if(path.endsWith('/preparations')&&!body)return {items:phase?[{plan_id:'plan',request_id:requestId,phase}]:[]};
   if(path.endsWith('/policy')){policyBodies.push(body);if(lost){lost=false;throw Error('lost reply');}return {policy_ref:{id:'policy',record_hash:'hash'}};}
   if(path.endsWith('/preparations')&&body){prepares++;requestId=body.request.request_id;phase='waiting';if(lostPrepare){lostPrepare=false;throw Error('lost reply');}return {plan_id:'plan'};}
   if(path.endsWith('/preparations/plan')&&!body)return {phase,version:2,goal_hash:'g',head_hash:'h',can_process:true,can_preview:true,can_start:false};
   if(path.endsWith('/documents')&&body){phase='review';return {phase};}
   if(path.endsWith('/documents'))return {documents:['a','b'].map(id=>({id,text:'已批准资料',version:authorized[id]?2:1,authorized:!!authorized[id]}))};
   if(path.endsWith('/consent')){assert.equal(body.confirmed,true);authorized[body.evidence_id]=true;return {authorized:true};}
   if(path.endsWith('/preview')){previews++;return {messages:[{role:'user',content:unsafePreview?[{type:'image_url',image_url:{url:'example'}}]:'本次资料'}],can_start:false};}
   throw Error('unexpected path');
  };
  const locks={request:async(_name,_options,callback)=>callback({name:'synthetic'})};
  const render=()=>React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,locks});
  const click=async text=>act(async()=>[...document.querySelectorAll('button')].find(item=>item.textContent===text).click());
  await act(async()=>root.render(render()));
  await click('使用批准资料');
  assert.match(document.body.textContent,/申请结果待确认/);
  assert.match(document.body.textContent,/继续准备/);
  await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));root.render(render());});
  await click('继续准备');assert.equal(policyBodies.length,2);
  assert.deepEqual(policyBodies[0],policyBodies[1]);assert.equal(prepares,1);
  assert.match(document.body.textContent,/申请结果待确认/);
  await click('继续准备');assert.equal(prepares,1,'a saved preparation is discovered, not recreated');
  await click('整理资料');assert.match(document.body.textContent,/核对资料/);
  for(let i=0;i<2;i++){
   await act(async()=>document.querySelector('input[type=checkbox]').click());
   await click('确认授权');
  }
  await click('查看分析内容');assert.equal(previews,1);
  assert.match(document.body.textContent,/预览暂不可用/);
  assert.equal([...document.querySelectorAll('button')].some(item=>item.textContent==='开始分析'),false);
  unsafePreview=false;await click('查看分析内容');assert.equal(previews,2);
  assert.equal([...document.querySelectorAll('button')].find(item=>item.textContent==='开始分析').disabled,true);
  assert.match(document.body.textContent,/分析服务暂不可用/);
  await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));root.render(render());});
  assert.match(document.body.textContent,/已授权/);assert.equal(prepares,1);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('two tabs serialize preparation and discover the same saved application',async()=>{
 const dom=new JSDOM('<!doctype html><div id="one"></div><div id="two"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let first,second;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  first=createRoot(document.getElementById('one'));second=createRoot(document.getElementById('two'));
  let pendingLock=Promise.resolve(), release, entered, phase='',policyCalls=0,prepares=0,requestId='';
  const blocker=new Promise(resolve=>{release=resolve;}), reached=new Promise(resolve=>{entered=resolve;});
  const locks={request:(_name,_options,callback)=>{const next=pendingLock.then(()=>callback({name:'synthetic'}));pendingLock=next.catch(()=>{});return next;}};
  const source={title:'批准资料',selection:{kind:'approved_source_set',source_set_ref:{source_set_id:'one',source_set_hash:'hash'}}};
  const request=async(path,options={})=>{
   const body=options.body?JSON.parse(options.body):null;
   if(path.endsWith('/availability'))return {enabled:true};
   if(path.endsWith('/context'))return {enabled:true,can_prepare:true,sources:[source],goal_hash:'g',head_hash:'h',expires_at:'2026-11-26T00:00:00Z',max_tokens:1024,model:'DeepSeek'};
   if(path.endsWith('/preparations')&&!body)return {items:phase?[{plan_id:'plan',request_id:requestId,phase}]:[]};
   if(path.endsWith('/policy')){policyCalls++;entered();await blocker;return {policy_ref:{id:'policy',record_hash:'hash'}};}
   if(path.endsWith('/preparations')&&body){prepares++;requestId=body.request.request_id;phase='waiting';return {plan_id:'plan'};}
   if(path.endsWith('/preparations/plan'))return {phase,can_process:true,can_start:false};
   throw Error('unexpected path');
  };
  const props={account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,locks};
  await act(async()=>{first.render(React.createElement(Panel,props));second.render(React.createElement(Panel,props));});
  await act(async()=>document.querySelector('#one button').click());await reached;
  await act(async()=>document.querySelector('#two button').click());
  await act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
  assert.equal(policyCalls,1);assert.equal(prepares,1);
  assert.match(document.querySelector('#one').textContent,/整理资料/);
  assert.match(document.querySelector('#two').textContent,/整理资料/);
 }finally{if(first)await act(async()=>first.unmount());if(second)await act(async()=>second.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
