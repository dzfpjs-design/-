import test from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {JSDOM} from 'jsdom';
import {createServer} from 'vite';

test('new preparation and existing history share one user-facing area',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
 const account={workspace_id:id(1),user_id:id(2),role:'admin'},task={id:id(3),instance_id:id(4),instance_release_id:id(5)};
 dom.window.sessionStorage.setItem('ga-setup-csrf-user',account.user_id);
 dom.window.sessionStorage.setItem('ga-setup-csrf-workspace',account.workspace_id);
 dom.window.sessionStorage.setItem('ga-setup-csrf','synthetic');
 const selection={kind:'approved_source_set',source_set_ref:{source_set_id:id(6),source_set_hash:'a'.repeat(64)}};
 const permissions=Object.fromEntries(['can_prepare','can_authorize','can_preview','can_start','can_review','can_configure_quota','can_register_price'].map(key=>[key,false]));
 const request=async path=>{
  if(path==='/me')return account;
  if(path.endsWith('/business-advice-v2/availability'))return {enabled:true};
  if(path.endsWith('/business-advice-v2/context'))return {enabled:true,can_prepare:true,sources:[{title:'批准资料',selection}],goal_hash:'a'.repeat(64),head_hash:'b'.repeat(64),expires_at:'2099-11-26T00:00:00Z',max_tokens:1024,model:'DeepSeek'};
  if(path.endsWith('/business-advice-v2/preparations'))return {items:[]};
  if(path.endsWith('/business-advice/context'))return {schema:'same-goal-advice-context-v310',task_id:task.id,task_version:1,title:'合成任务',permissions,document_sources:[],blocked_reasons:[],goal_hash:'a'.repeat(64),mature_head_hash:'b'.repeat(64)};
  if(path.endsWith('/business-advice/preparations'))return {schema:'same-goal-advice-operation-v302/list',task_id:task.id,records:[]};
  throw Error('unexpected path');
 };
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/SameGoalAdviceV310.jsx');
  root=createRoot(document.getElementById('root'));
  const locks={request:async(_name,_options,callback)=>callback({name:'synthetic'})};
  await act(async()=>root.render(React.createElement(Panel,{account,task,requestJSON:request,storage:dom.window.localStorage,tabStorage:dom.window.sessionStorage,locks})));
  await act(async()=>new Promise(resolve=>setTimeout(resolve,20)));
  assert.equal([...document.querySelectorAll('h4')].filter(item=>item.textContent==='业务建议').length,1);
  assert.equal([...document.querySelectorAll('summary')].filter(item=>item.textContent==='历史建议').length,1);
  assert.ok([...document.querySelectorAll('button')].some(item=>item.textContent==='使用批准资料'));
  assert.doesNotMatch(document.body.textContent,/准备新建议|单次费用上限|测试|实验|V2/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('an unreadable history is not presented as an empty history',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {SameGoalAdviceContentV310:Content}=await server.ssrLoadModule('/src/SameGoalAdviceV310.jsx');
  root=createRoot(document.getElementById('root'));
  const view={denied:false,loading:false,localSaved:true,reads:{history:'failed'},context:null,list:null,plan:null};
  await act(async()=>root.render(React.createElement(Content,{view,account:{role:'admin'},historyOnly:true,onAction:()=>{},onDraft:()=>{}})));
  assert.match(document.body.textContent,/历史建议暂未读回/);
  assert.doesNotMatch(document.body.textContent,/暂无历史建议/);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('saved unfinished advice remains accessible after enabling, refresh, relogin and task switching',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
 const account={workspace_id:id(1),user_id:id(2),role:'admin'},task={id:id(3),instance_id:id(4),instance_release_id:id(5)};
 const session=dom.window.sessionStorage,storage=dom.window.localStorage;
 session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);session.setItem('ga-setup-csrf','synthetic');
 const permissions=Object.fromEntries(['can_prepare','can_authorize','can_preview','can_start','can_review','can_configure_quota','can_register_price'].map(key=>[key,key==='can_prepare']));
 const request=async path=>{
  if(path==='/me')return account;
  if(path.endsWith('/business-advice-v2/availability'))return {enabled:true};
  if(path.endsWith('/business-advice-v2/context'))return {enabled:true,can_prepare:false,sources:[],goal_hash:'a'.repeat(64),head_hash:'b'.repeat(64),expires_at:'2099-11-26T00:00:00Z',max_tokens:1024,model:'DeepSeek'};
  if(path.endsWith('/business-advice-v2/preparations'))return {items:[]};
  const current=path.match(/tasks\/([^/]+)\//)?.[1]||task.id;
  if(path.endsWith('/business-advice/context'))return {schema:'same-goal-advice-context-v310',task_id:current,task_version:1,title:'合成任务',permissions,document_sources:[],blocked_reasons:[],goal_hash:'a'.repeat(64),mature_head_hash:'b'.repeat(64)};
  if(path.endsWith('/business-advice/preparations'))return {schema:'same-goal-advice-operation-v302/list',task_id:current,records:[]};
  throw Error('unexpected path');
 };
 const locks={request:async(_name,_options,callback)=>callback({name:'synthetic'})};
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/SameGoalAdviceV310.jsx');
  const {SameGoalAdviceControllerV310}=await server.ssrLoadModule('/src/same-goal-advice-v310-controller.mjs');
  const {webcrypto}=await import('node:crypto');
  const original=new SameGoalAdviceControllerV310({account,task,request,storage,session,locks,crypto:webcrypto});
  await original.open();original.updateDraft({prepare_reason:'用户已保存的工作内容'});original.dispose();
  root=createRoot(document.getElementById('root'));const render=t=>React.createElement(Panel,{account,task:t,requestJSON:request,storage,tabStorage:session,locks});
  const settle=()=>new Promise(resolve=>setTimeout(resolve,30));
  await act(async()=>{root.render(render(task));await settle();});
  const draft=()=>document.querySelector('textarea[aria-label="本次整理资料的说明"]');
  const check=()=>{assert.ok([...document.querySelectorAll('summary')].some(x=>x.textContent==='继续未完成的建议'));assert.equal(draft()?.value,'用户已保存的工作内容');};
  check();await act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click();await settle();});check();
  const other={...task,id:id(99)};await act(async()=>{root.render(render(other));await settle();});
  assert.equal([...document.querySelectorAll('summary')].some(x=>x.textContent==='继续未完成的建议'),false);
  session.setItem('ga-setup-csrf','new-session');await act(async()=>{root.render(render(task));await settle();});check();
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('unavailable optional service keeps history read-only, recovers, and never crosses workspaces',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];
 const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));
 globalThis.window=dom.window;globalThis.document=dom.window.document;
 Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
 let account={workspace_id:id(1),user_id:id(2),role:'admin'},mode='offline',posts=0,releasePending;
 const task={id:id(3),instance_id:id(4),instance_release_id:id(5)},session=dom.window.sessionStorage;
 const updateSession=()=>{session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);session.setItem('ga-setup-csrf','synthetic');};updateSession();
 const permissions=Object.fromEntries(['can_prepare','can_authorize','can_preview','can_start','can_review','can_configure_quota','can_register_price'].map(key=>[key,true]));
 const request=async(path,options={})=>{
  if(options.method==='POST')posts++;
  if(path==='/me')return account;
  if(path.endsWith('/business-advice-v2/availability')){
   if(mode==='pending')return await new Promise(resolve=>{releasePending=resolve;});
   if(mode==='offline')throw Error('network disconnected');
   if(mode==='missing')throw Object.assign(Error('not found'),{status:404});
   if(mode==='denied')throw Object.assign(Error('forbidden'),{status:403});
   return mode==='disabled'?{enabled:false,has_unfinished:false}:{enabled:true};
  }
  if(path.endsWith('/business-advice-v2/context'))return {enabled:true,can_prepare:true,sources:[],goal_hash:'a'.repeat(64),head_hash:'b'.repeat(64),expires_at:'2099-11-26T00:00:00Z',max_tokens:1024,model:'DeepSeek'};
  if(path.endsWith('/business-advice-v2/preparations'))return {items:[]};
  if(path.endsWith('/business-advice/context'))return {schema:'same-goal-advice-context-v310',task_id:task.id,task_version:1,title:'合成任务',permissions,document_sources:[],blocked_reasons:[],goal_hash:'a'.repeat(64),mature_head_hash:'b'.repeat(64)};
  if(path.endsWith('/business-advice/preparations'))return {schema:'same-goal-advice-operation-v302/list',task_id:task.id,records:[]};
  throw Error('unexpected route');
 };
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});
 let root;try{
  const {createRoot}=await import('react-dom/client');const {default:Panel}=await server.ssrLoadModule('/src/SameGoalAdviceV310.jsx');
  root=createRoot(document.getElementById('root'));const locks={request:async(_n,_o,fn)=>fn({name:'synthetic'})};
  const render=()=>React.createElement(Panel,{account,task,requestJSON:request,storage:dom.window.localStorage,tabStorage:session,locks});
  const settle=()=>new Promise(resolve=>setTimeout(resolve,30));
  const refresh=async()=>act(async()=>{[...document.querySelectorAll('button')].find(x=>x.textContent==='刷新').click();await settle();});
  await act(async()=>{root.render(render());await settle();});
  assert.match(document.body.textContent,/建议暂不可用/);assert.match(document.body.textContent,/历史建议/);
  assert.doesNotMatch(document.body.textContent,/整理本任务已批准的资料|开始分析/);assert.equal(posts,0);
  mode='missing';await refresh();assert.match(document.body.textContent,/建议暂不可用/);assert.match(document.body.textContent,/历史建议/);
  mode='disabled';await refresh();assert.match(document.body.textContent,/整理本任务已批准的资料/);
  mode='pending';await refresh();assert.ok(releasePending);const gated=document.querySelector('textarea[aria-label="本次整理资料的说明"]');assert.ok(gated?.matches(':disabled'),'legacy editor is disabled while availability is unknown');assert.equal(posts,0);
  mode='disabled';await act(async()=>{releasePending({enabled:false,has_unfinished:false});await settle();});assert.equal(gated.matches(':disabled'),false);
  mode='enabled';await refresh();assert.match(document.body.textContent,/历史建议/);
  mode='denied';await refresh();assert.match(document.body.textContent,/登录状态已变化/);assert.doesNotMatch(document.body.textContent,/历史建议|整理本任务已批准的资料/);
  mode='disabled';await refresh();assert.match(document.body.textContent,/整理本任务已批准的资料/);
  account={...account,workspace_id:id(9)};updateSession();mode='pending';await act(async()=>{root.render(render());await settle();});
  assert.ok(releasePending);assert.doesNotMatch(document.body.textContent,/整理本任务已批准的资料|开始分析/,'previous workspace availability cannot enable actions while the new one is checking');
  await act(async()=>{releasePending({enabled:false,has_unfinished:false});await settle();});assert.match(document.body.textContent,/整理本任务已批准的资料/);
  mode='offline';await refresh();assert.match(document.body.textContent,/建议暂不可用/);assert.doesNotMatch(document.body.textContent,/整理本任务已批准的资料|开始分析/);assert.equal(posts,0);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});

test('read-only history exposes saved result without any mutation controls',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'http://localhost/'});
 const keys=['window','document','navigator','IS_REACT_ACT_ENVIRONMENT'];const old=new Map(keys.map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));globalThis.window=dom.window;globalThis.document=dom.window.document;Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true});globalThis.IS_REACT_ACT_ENVIRONMENT=true;
 const server=await createServer({configFile:false,server:{middlewareMode:true,hmr:false,ws:false},optimizeDeps:{noDiscovery:true}});let root;
 try{
  const {createRoot}=await import('react-dom/client'),{ReadOnlyAdviceHistory:History}=await server.ssrLoadModule('/src/SameGoalAdviceV310.jsx');root=createRoot(document.getElementById('root'));
  let actions=[];const view={reads:{history:'ready',plan:'ready'},list:{records:[{plan_id:'old',phase:'reviewed'}]},selectedPlanId:'old',plan:{plan_id:'old',result:{model_value:{recommendation:{reason:'已保存的业务意见'},candidate_reviews:[]}},reviews:[]}};
  await act(async()=>root.render(React.createElement(History,{view,account:{role:'admin'},task:{},onAction:(...args)=>actions.push(args)})));
  assert.match(document.body.textContent,/已保存的业务意见/);assert.doesNotMatch(document.body.textContent,/开始分析|保存本次决定|取消本次申请|整理本任务/);assert.deepEqual(actions,[]);
 }finally{if(root)await act(async()=>root.unmount());await server.close();dom.window.close();for(const [k,v] of old){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}}
});
