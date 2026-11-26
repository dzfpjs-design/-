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
  root=createRoot(document.getElementById('root'));let phase='',lost=true,lostPrepare=true,unsafePreview=true,authorized={},policyBodies=[],prepares=0,previews=0,requestId='',triggerRefresh,offline=false,availableState=null,dataRole=false,docsRead=0,denyNext=false,disabled=false,serverRole='admin';
  const source={title:'批准资料',selection:{kind:'approved_source_set',source_set_ref:{source_set_id:'one',source_set_hash:'hash'}}};
  const ctx={enabled:true,can_prepare:true,can_start:false,sources:[source],goal_hash:'g',head_hash:'h',expires_at:'2099-11-26T00:00:00Z',max_tokens:1024,model:'DeepSeek'};
  const request=async(path,options={})=>{
   if(path==='/me')return {workspace_id:'w',user_id:'u',role:serverRole};
   const body=options.body?JSON.parse(options.body):null;
   if(path.endsWith('/availability')){if(denyNext)throw Object.assign(Error('denied'),{status:403});if(offline)throw Error('offline');return disabled?{enabled:false,has_unfinished:true}:{enabled:true};}
   if(path.endsWith('/context'))return ctx;
   if(path.endsWith('/preparations')&&!body)return {items:phase?[{plan_id:'plan',request_id:requestId,phase}]:[]};
   if(path.endsWith('/policy')){policyBodies.push(body);if(lost){lost=false;throw Error('lost reply');}return {policy_ref:{id:'policy',record_hash:'hash'}};}
   if(path.endsWith('/preparations')&&body){prepares++;requestId=body.request.request_id;phase='waiting';if(lostPrepare){lostPrepare=false;throw Error('lost reply');}return {plan_id:'plan'};}
   if(path.endsWith('/preparations/plan')&&!body)return {phase,version:2,goal_hash:'g',head_hash:'h',can_process:true,can_preview:!dataRole,can_start:false};
   if(path.endsWith('/documents')&&body){phase='review';return {phase};}
   if(path.endsWith('/documents')){docsRead++;return {documents:['a','b'].map(id=>({id,text:'已批准资料',version:authorized[id]?2:1,authorized:!!authorized[id]}))};}
   if(path.endsWith('/consent')){assert.equal(body.confirmed,true);authorized[body.evidence_id]=true;return {authorized:true};}
   if(path.endsWith('/preview')){previews++;return {display:unsafePreview?{unrecognized:true}:{question:'本次问题',instruction:'分析要求',lines:[{label:'来源',value:'来源文字'},{label:'事实 · 项目',value:'温度'},{label:'事实 · 数值',value:'42'}],documents:['资料全文甲','资料全文乙']},can_start:false};}
   throw Error('unexpected path');
  };
  const locks={request:async(_name,_options,callback)=>callback({name:'synthetic'})};
  const render=()=>React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,locks,onRefreshReady:fn=>{triggerRefresh=fn;},onAvailability:value=>{availableState=value;}});
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
  offline=true;await act(async()=>{triggerRefresh();await new Promise(resolve=>setTimeout(resolve,10));});
  assert.equal(availableState,true);assert.match(document.body.textContent,/建议暂不可用/);
  offline=false;
  authorized.a=false;
  await act(async()=>{triggerRefresh();await new Promise(resolve=>setTimeout(resolve,10));});
  assert.match(document.body.textContent,/同意将资料发送/,'refresh rereads authorization changed in another tab');
  await act(async()=>document.querySelector('input[type=checkbox]').click());await click('确认授权');
  await click('查看分析内容');assert.equal(previews,1);
  assert.match(document.body.textContent,/预览暂不可用/);
  assert.equal([...document.querySelectorAll('button')].some(item=>item.textContent==='开始分析'),false);
  unsafePreview=false;await click('查看分析内容');assert.equal(previews,2);
  assert.equal([...document.querySelectorAll('button')].find(item=>item.textContent==='开始分析').disabled,true);
  assert.match(document.body.textContent,/分析服务暂不可用/);
  assert.match(document.body.textContent,/来源文字/);assert.match(document.body.textContent,/资料全文甲/);assert.match(document.body.textContent,/资料全文乙/);
  assert.doesNotMatch(document.body.textContent,/input_hash|private-id|secret-hash|internal system prompt|decision_input/);
  await act(async()=>{root.unmount();root=createRoot(document.getElementById('root'));root.render(render());});
  assert.match(document.body.textContent,/已授权/);assert.equal(prepares,1);
  dataRole=true;const before=docsRead;await act(async()=>{triggerRefresh();await new Promise(resolve=>setTimeout(resolve,10));});
  assert.match(document.body.textContent,/资料已整理，等待业务人员核对/);assert.equal(docsRead,before);assert.doesNotMatch(document.body.textContent,/确认授权/);
  dataRole=false;await act(async()=>{triggerRefresh();await new Promise(resolve=>setTimeout(resolve,10));});assert.match(document.body.textContent,/已批准资料/);
  disabled=true;await act(async()=>{triggerRefresh();await new Promise(resolve=>setTimeout(resolve,10));});
  assert.equal(availableState,true);assert.match(document.body.textContent,/已有申请暂不可用/);assert.doesNotMatch(document.body.textContent,/已批准资料/);
  assert.ok(dom.window.localStorage.getItem('advice-v2:'+JSON.stringify(['w','u','t'])+':active'));
  disabled=false;await act(async()=>{triggerRefresh();await new Promise(resolve=>setTimeout(resolve,10));});
  serverRole='viewer';await act(async()=>{triggerRefresh();await new Promise(resolve=>setTimeout(resolve,10));});
  assert.doesNotMatch(document.body.textContent,/已批准资料|资料全文甲|资料全文乙/);assert.match(document.body.textContent,/登录状态已变化/);
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
   if(path==='/me')return {workspace_id:'w',user_id:'u',role:'admin'};
   const body=options.body?JSON.parse(options.body):null;
   if(path.endsWith('/availability'))return {enabled:true};
   if(path.endsWith('/context'))return {enabled:true,can_prepare:true,sources:[source],goal_hash:'g',head_hash:'h',expires_at:'2099-11-26T00:00:00Z',max_tokens:1024,model:'DeepSeek'};
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

test('a late document response cannot restore content after permission is denied',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  root=createRoot(document.getElementById('root'));let serverRole='admin',release,entered,refresh;
  const waiting=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const request=async(path)=>{
   if(path==='/me')return {workspace_id:'w',user_id:'u',role:serverRole};
   if(path.endsWith('/availability'))return {enabled:true};
   if(path.endsWith('/context'))return {enabled:true,can_prepare:false,sources:[],model:'DeepSeek'};
   if(path.endsWith('/preparations'))return {items:[{plan_id:'plan',request_id:'one',phase:'review'}]};
   if(path.endsWith('/preparations/plan'))return {phase:'review',can_preview:true};
   if(path.endsWith('/documents')){entered();await waiting;return {documents:[{id:'a',text:'禁止恢复的原文',authorized:true}]};}
   throw Error('unexpected path');
  };
  await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,onRefreshReady:value=>{refresh=value;}})));
  await started;serverRole='viewer';
  await act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
  assert.doesNotMatch(document.body.textContent,/禁止恢复的原文/);assert.match(document.body.textContent,/登录状态已变化/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('disabled service reports an existing application on a fresh device',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  root=createRoot(document.getElementById('root'));let available=null,unfinished=true,refresh;
  const request=async path=>{if(path==='/me')return {workspace_id:'w',user_id:'u',role:'admin'};
   if(path.endsWith('/availability'))return {enabled:false,has_unfinished:unfinished};throw Error('unexpected');};
  await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,onAvailability:value=>{available=value;},onRefreshReady:value=>{refresh=value;}})));
  assert.equal(dom.window.localStorage.length,0);assert.equal(available,true);
  assert.match(document.body.textContent,/已有申请暂不可用/);
  assert.equal([...document.querySelectorAll('button')].some(item=>item.textContent.includes('准备')),false);
  unfinished=false;await act(async()=>{await refresh();});assert.equal(available,false);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('a late document response cannot restore content after service is disabled',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  root=createRoot(document.getElementById('root'));let disabled=false,release,entered,refresh;
  const waiting=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const request=async(path)=>{
   if(path==='/me')return {workspace_id:'w',user_id:'u',role:'admin'};
   if(path.endsWith('/availability'))return disabled?{enabled:false,has_unfinished:true}:{enabled:true};
   if(path.endsWith('/context'))return {enabled:true,can_prepare:false,sources:[],model:'DeepSeek'};
   if(path.endsWith('/preparations'))return {items:[{plan_id:'plan',request_id:'one',phase:'review'}]};
   if(path.endsWith('/preparations/plan'))return {phase:'review',can_preview:true};
   if(path.endsWith('/documents')){entered();await waiting;return {documents:[{id:'a',text:'已停用时不可恢复的原文',authorized:true}]};}
   throw Error('unexpected path');
  };
  await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,onRefreshReady:value=>{refresh=value;}})));
  await started;disabled=true;await act(async()=>{await refresh();});
  await act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
  assert.doesNotMatch(document.body.textContent,/已停用时不可恢复的原文/);assert.match(document.body.textContent,/已有申请暂不可用/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

for (const operation of ['process','authorize','preview']) test(`disabling while ${operation} is pending keeps old data hidden`,async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  root=createRoot(document.getElementById('root'));let disabled=false,release,entered,refresh,docReads=0,meReads=0;
  const waiting=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const request=async(path,options={})=>{
   if(path==='/me'){meReads++;return {workspace_id:'w',user_id:'u',role:'admin'};}
   if(path.endsWith('/availability'))return disabled?{enabled:false,has_unfinished:true}:{enabled:true};
   if(path.endsWith('/context'))return {enabled:true,can_prepare:false,sources:[],model:'DeepSeek'};
   if(path.endsWith('/preparations'))return {items:[{plan_id:'plan',request_id:'one',phase:operation==='process'?'waiting':'review'}]};
   if(path.endsWith('/preparations/plan'))return {phase:operation==='process'?'waiting':'review',can_process:true,can_preview:true,version:1,goal_hash:'g',head_hash:'h'};
   if(path.endsWith('/documents')&&!options.method){docReads++;return {documents:[{id:'a',text:'敏感资料',version:1,authorized:operation==='preview'}]};}
   if(options.method==='POST'){entered();await waiting;return operation==='preview'?{display:{question:'迟到的分析',instruction:'说明',lines:[],documents:['迟到的原文']}}:{ok:true};}
   throw Error('unexpected path');
  };
  await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,onRefreshReady:value=>{refresh=value;}})));
  if(operation==='authorize')await act(async()=>document.querySelector('input[type=checkbox]').click());
  const label={process:'整理资料',authorize:'确认授权',preview:'查看分析内容'}[operation];
  await act(async()=>[...document.querySelectorAll('button')].find(item=>item.textContent===label).click());await started;
  disabled=true;await act(async()=>{await refresh();});const before=docReads,beforeMe=meReads;
  await act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
  assert.match(document.body.textContent,/已有申请暂不可用/);
  assert.doesNotMatch(document.body.textContent,/敏感资料|迟到的分析|迟到的原文/);assert.equal(docReads,before);assert.equal(meReads,beforeMe);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('a verified closed application clears only its obsolete active pointer',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  const key='advice-v2:'+JSON.stringify(['w','u','t']),storage=dom.window.localStorage;storage.setItem(key+':active','old-plan');
  let available=null;const request=async path=>path==='/me'?{workspace_id:'w',user_id:'u',role:'admin'}:{enabled:false,has_unfinished:false};
  root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage,onAvailability:value=>{available=value;}})));
  assert.equal(available,false);assert.equal(storage.getItem(key+':active'),null);
  await act(async()=>root.unmount());root=createRoot(document.getElementById('root'));
  storage.setItem(key,JSON.stringify({request_id:'original'}));storage.setItem(key+':active','old-plan');
  await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage,onAvailability:value=>{available=value;}})));
  assert.equal(available,true);assert.ok(storage.getItem(key));assert.match(document.body.textContent,/已有申请暂不可用/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

for(const pendingStep of ['policy','preparation']) test(`disabling during ${pendingStep} preserves the request`,async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  const key='advice-v2:'+JSON.stringify(['w','u','t']),storage=dom.window.localStorage;let disabled=false,release,entered,refresh,prepareWrites=0;
  const waiting=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const request=async(path,options={})=>{
   if(path==='/me')return {workspace_id:'w',user_id:'u',role:'admin'};
   if(path.endsWith('/availability'))return disabled?{enabled:false,has_unfinished:true}:{enabled:true};
   if(path.endsWith('/context'))return {enabled:true,can_prepare:true,sources:[{title:'批准资料',selection:{kind:'approved_source_set'}}],goal_hash:'g',head_hash:'h',expires_at:'2099-01-01T00:00:00Z',max_tokens:1024};
   if(path.endsWith('/preparations')&&!options.method)return {items:[]};
   if(path.endsWith('/policy')){if(pendingStep==='policy'){entered();await waiting;}return {policy_ref:{id:'p',record_hash:'h'}};}
   if(path.endsWith('/preparations')&&options.method){prepareWrites++;if(pendingStep==='preparation'){entered();await waiting;}return {plan_id:'plan'};}
   throw Error('unexpected');
  };
  root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage,locks:{request:async(_name,_opts,callback)=>callback()},onRefreshReady:value=>{refresh=value;}})));
  await act(async()=>[...document.querySelectorAll('button')].find(item=>item.textContent==='使用批准资料').click());await started;
  disabled=true;await act(async()=>{await refresh();});await act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
  assert.equal(prepareWrites,pendingStep==='policy'?0:1);assert.ok(JSON.parse(storage.getItem(key)).request_id);assert.match(document.body.textContent,/已有申请暂不可用/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('a delayed identity check cannot start a write after service is disabled',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  let disabled=false,holdIdentity=false,release,entered,refresh,writes=0;
  const waiting=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const request=async(path,options={})=>{
   if(path==='/me'){if(holdIdentity){holdIdentity=false;entered();await waiting;}return {workspace_id:'w',user_id:'u',role:'admin'};}
   if(path.endsWith('/availability'))return disabled?{enabled:false,has_unfinished:true}:{enabled:true};
   if(path.endsWith('/context'))return {enabled:true,can_prepare:false,sources:[],model:'DeepSeek'};
   if(path.endsWith('/preparations'))return {items:[{plan_id:'plan',request_id:'one',phase:'review'}]};
   if(path.endsWith('/preparations/plan'))return {phase:'review',can_preview:true,version:1,goal_hash:'g',head_hash:'h'};
   if(path.endsWith('/documents'))return {documents:[{id:'a',text:'合成资料',version:1,authorized:true}]};
   if(options.method==='POST'){writes++;return {};}
   throw Error('unexpected');
  };
  root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,onRefreshReady:value=>{refresh=value;}})));
  holdIdentity=true;await act(async()=>[...document.querySelectorAll('button')].find(item=>item.textContent==='查看分析内容').click());await started;
  disabled=true;await act(async()=>{await refresh();});await act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
  assert.equal(writes,0);assert.match(document.body.textContent,/已有申请暂不可用/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('a stale refresh does not request availability after delayed identity',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  let first=true,release,entered,refresh,availabilityReads=0;
  const waiting=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const request=async path=>{if(path==='/me'){if(first){first=false;entered();await waiting;}return {workspace_id:'w',user_id:'u',role:'admin'};}
   if(path.endsWith('/availability')){availabilityReads++;return {enabled:false,has_unfinished:true};}throw Error('unexpected');};
  root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,onRefreshReady:value=>{refresh=value;}})));
  await started;await act(async()=>{await refresh();});await act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
  assert.equal(availabilityReads,1);assert.match(document.body.textContent,/已有申请暂不可用/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('a stale status response cannot start a document read after disable',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/AdvicePreparationV2.jsx');
  let disabled=false,release,entered,refresh,docReads=0;const waiting=new Promise(resolve=>{release=resolve;}),started=new Promise(resolve=>{entered=resolve;});
  const request=async path=>{if(path==='/me')return {workspace_id:'w',user_id:'u',role:'admin'};
   if(path.endsWith('/availability'))return disabled?{enabled:false,has_unfinished:true}:{enabled:true};
   if(path.endsWith('/context'))return {enabled:true,can_prepare:false,sources:[]};
   if(path.endsWith('/preparations'))return {items:[{plan_id:'plan',request_id:'one'}]};
   if(path.endsWith('/preparations/plan')){entered();await waiting;return {phase:'review',can_preview:true};}
   if(path.endsWith('/documents')){docReads++;return {documents:[]};}throw Error('unexpected');};
  root=createRoot(document.getElementById('root'));await act(async()=>root.render(React.createElement(Panel,{account:{workspace_id:'w',user_id:'u',role:'admin'},task:{id:'t'},requestJSON:request,storage:dom.window.localStorage,onRefreshReady:value=>{refresh=value;}})));
  await started;disabled=true;await act(async()=>{await refresh();});await act(async()=>{release();await new Promise(resolve=>setTimeout(resolve,20));});
  assert.equal(docReads,0);assert.match(document.body.textContent,/已有申请暂不可用/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
