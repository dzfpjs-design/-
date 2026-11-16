// Actual production components/React DOM/StrictMode and persisted local journal.
// HTTP responses are synthetic; this is not a real browser, ACK or PG proof.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {webcrypto} from 'node:crypto';
import {build} from 'vite';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {httpApprovalFixture,id,copy} from './http-signal-approval-v247-fixture.mjs';

const project=resolve(fileURLToPath(new URL('..',import.meta.url))),require=createRequire(join(project,'package.json')),reactPath=require.resolve('react'),reactURL=pathToFileURL(reactPath).href;
let folder,bundle;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function components(){
 if(!bundle)bundle=(async()=>{
  folder=await mkdtemp(join(tmpdir(),'http-signal-approval-v247-'));const entry=join(folder,'entry.jsx');
  await writeFile(entry,'export {default as Panel} from '+JSON.stringify(join(project,'src/HTTPSignalApprovalStudioV247.jsx'))+';export {default as Signals} from '+JSON.stringify(join(project,'src/SignalStudio.jsx'))+';export {default as Approval} from '+JSON.stringify(join(project,'src/TaskApprovalPanelV175.jsx'))+';export {json,setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';');
  const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},build:{ssr:entry,write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}});
  const chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);const file=join(folder,'actual.mjs');await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
async function harness(run,{kind='panel'}={}){
 assert.equal(process.env.NODE_ENV,'development');const f=httpApprovalFixture(),errors=[],vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error));
 class NoNetwork extends ResourceLoader{fetch(){throw Error('External resources forbidden in synthetic actual React tests');}}
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://http-approval-react.invalid',resources:new NoNetwork(),virtualConsole:vc});
 f.state.signals=[];
 const appAPI=async(path,options={})=>{
  if(path==='/me'||path.startsWith(f.base)||path==='/workspace/tasks/'+f.taskID+'/signal-approval-source')return f.api(path,options);
  const method=options.method||'GET';assert.equal(method,'GET','No automatic submit, approval, start, capture or model command');
  if(path==='/workspace/instances/'+f.instanceId+'/signals')return {records:copy(f.state.signals),has_more:false};
  if(path==='/workspace/instances/'+f.instanceId+'/reported-observations')return {records:[],has_more:false};
  if(path==='/workspace/tasks/'+f.taskID+'/approval-v175')return f.approval();
  if(path==='/workspace/approval-settings-v175')return {schema:'approval-settings-view-v175',units:[{id:id(41),name:'业务部门',parent_id:null,enabled:true}],members:[{id:f.account.user_id,username:f.account.username}],policies:[],grants:[],can_manage:false};
  if(path==='/workspace/tasks/'+f.taskID)return {...f.task(),status:f.state.currentTaskStatus||f.task().status};
  throw Object.assign(Error('本合成场景没有启用此附属能力：'+path),{status:503});
 };
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href);assert.equal(url.origin,dom.window.location.origin);try{return new Response(JSON.stringify(await appAPI(url.pathname.replace(/^\/api/,'')+url.search,options)),{headers:{'Content-Type':'application/json'}});}catch(error){if(error.name==='AssertionError')errors.push(error);return new Response(JSON.stringify({detail:error.message}),{status:error.status||503,headers:{'Content-Type':'application/json'}});}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,StorageEvent:dom.window.StorageEvent,localStorage:f.storage,sessionStorage:f.session,crypto:webcrypto,fetch,location:dom.window.location,history:dom.window.history,IS_REACT_ACT_ENVIRONMENT:true,addEventListener:dom.window.addEventListener.bind(dom.window),removeEventListener:dom.window.removeEventListener.bind(dom.window)},prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,configurable:true,writable:true});Object.defineProperty(dom.window.navigator,'locks',{value:f.locks,configurable:true});dom.window.HTMLElement.prototype.scrollIntoView=()=>{};
 const actual=await components(),{createRoot}=await import('react-dom/client');actual.setSession(f.csrf,f.account.workspace_id,f.account.user_id,f.account.username);
 const container=document.getElementById('root'),navigations=[];let root,props,guard;
 const oldConsoleError=console.error;console.error=(...values)=>{const message=values.map(String).join(' ');if(/overlapping act|without await/.test(message))errors.push(Error(message));else oldConsoleError(...values);};
 const Btn=({children,primary,ghost,small,icon,...rest})=>React.createElement('button',{type:'button',...rest},children);
 const register=(fn,previous)=>{if(fn)guard=fn;else if(!previous||guard===previous)guard=null;};
 const onViewTask=async(taskID,checkEntry)=>{checkEntry?.();navigations.push(taskID);};
 const manual={id:f.businessID,name:'普通人工核查业务',available:true,execution_mode:'confirmed_human_workflow',flow:{steps:[{id:'manual',agent:'人工确认'}]}},image={id:'business_image_v243',name:'V243图像技术复核',available:true,execution_mode:'confirmed_graph_workflow',flow:{steps:[{id:'human_image',agent:'人工确认'}]}};
 const render=async()=>act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(kind==='approval'?actual.Approval:kind==='signals'?actual.Signals:actual.Panel,props))));
 const mount=async()=>{
  const common={account:f.account,instanceId:f.instanceId,releaseID:f.releaseID,enabled:true,Btn,onViewTask,onRegisterGuard:register};
  props=kind==='approval'?{task:f.task(),account:f.account,api:actual.json,onSaved:async()=>{},onRegisterGuard:register}:kind==='signals'?{...common,objects:[],enabledBusinesses:[manual.id,image.id],release:{id:f.releaseID,snapshot:{businesses:[manual,image],object_types:[]}},onViewAssessment:()=>{}}:{...common,api:actual.json,storage:f.storage,tabStorage:f.session,locks:f.locks};
  root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();
 };
 const unmount=async()=>{if(root){const old=root;root=null;await act(async()=>old.unmount());await Promise.resolve();}};
 const until=async(predicate,label='actual V247 state')=>{for(let turn=0;turn<350;turn++){assert.deepEqual(errors,[]);if(predicate())return;await act(async()=>new Promise(resolve=>setTimeout(resolve,2)));}assert.ok(predicate(),label+': '+container.textContent.slice(0,1500));};
 const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label);
 const click=async label=>{await until(()=>button(label)&&!button(label).matches(':disabled'),label);const parents=[];for(let parent=button(label).parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS'&&!parent.open)parents.unshift(parent);for(const parent of parents)await act(async()=>parent.querySelector(':scope > summary').click());await act(async()=>button(label).click());};
 const changeNode=async(node,value)=>{assert.ok(node);assert.equal(node.matches(':disabled'),false);await act(async()=>{const proto=node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});};
 const change=async(label,value)=>changeNode(container.querySelector('[aria-label="'+label+'"]'),value);
 const check=async label=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node);assert.equal(node.matches(':disabled'),false);await act(async()=>node.click());};
 const ready=async()=>{await until(()=>container.querySelector('[aria-label="HTTP 审批异常来源"]')&&!container.querySelector('[aria-label="HTTP 审批异常来源"]').matches(':disabled'));const parents=[];for(let parent=container.querySelector('[aria-label="HTTP 审批异常来源"]').parentElement;parent;parent=parent.parentElement)if(parent.tagName==='DETAILS'&&!parent.open)parents.unshift(parent);for(const parent of parents)await act(async()=>parent.querySelector(':scope > summary').click());};
 const fill=async(reason='仅将这个范围的新异常保存为审批草稿')=>{await change('HTTP 审批异常来源',f.sourceID);await check('HTTP 审批对象：东侧样地');await change('HTTP 审批接收业务',f.businessID);await change('HTTP 审批授权有效小时','6');await change('HTTP 审批草稿总额度','3');await change('HTTP 审批授权启用理由',reason);await check('确认 HTTP 仅创建正式审批草稿');};
 try{await run({f,dom,container,mount,unmount,render,until,button,click,change,changeNode,check,ready,fill,navigations,readTask:()=>actual.json('/workspace/tasks/'+f.taskID),guard:()=>guard,text:()=>container.textContent,update:async patch=>{props={...props,...patch};await render();}});assert.deepEqual(errors,[]);}
 finally{try{await unmount();assert.deepEqual(errors,[]);}finally{console.error=oldConsoleError;dom.window.close();for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}}
}

test('V247 actual directory explains unavailable image business and presents a human scope, duration and total quota',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();assert.match(h.text(),/异常转审批/);assert.match(h.text(),/本次数值异常没有原图像/);const blocked=[...h.container.querySelector('[aria-label="HTTP 审批接收业务"]').options].find(option=>option.value==='business_image_v243');assert.equal(blocked.disabled,true);assert.ok(!h.text().includes('target_hash'));assert.ok(!h.text().includes('冷核'));assert.ok(!h.text().includes('原发生'));assert.equal(h.f.state.posts.length,0);
}));
test('V247 actual explicit grant sends the selected six-hour three-draft scope and does not approve, execute or collect',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();await h.click('启用本次审批草稿授权');await h.until(()=>h.text().includes('启用决定已保存')&&!h.container.querySelector('[aria-label="原 HTTP 审批草稿授权请求"]'));assert.equal(h.f.state.posts.length,1);assert.equal(h.f.state.posts[0].body.valid_for_hours,6);assert.equal(h.f.state.posts[0].body.max_tasks,3);assert.deepEqual(h.f.state.posts[0].body.entity_ids,[h.f.entityID]);assert.equal(h.f.state.routes[0].used_tasks,0);assert.deepEqual(h.navigations,[]);assert.ok(!h.button('查看对应任务'));
}));
test('V247 actual wait for data never masquerades as a draft and the saved draft opens only on an explicit guarded action',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();await h.click('启用本次审批草稿授权');await h.until(()=>h.text().includes('启用决定已保存'));const item=h.f.occurrence();h.f.state.occurrences=[item];await h.click('更新来源、授权与草稿进度');await h.until(()=>h.text().includes('等待数据管理智能体接收完成'));assert.ok(!h.button('查看对应任务'));assert.deepEqual(h.navigations,[]);h.f.state.occurrences=[{...item,version:2,status:'approval_draft',reason:null,task_id:h.f.taskID,task_path:'/api/workspace/tasks/'+h.f.taskID,receipt_hash:'1'.repeat(64)}];h.f.state.routes[0].used_tasks=1;await h.click('更新来源、授权与草稿进度');await h.until(()=>h.button('查看对应任务'));assert.match(h.text(),/后续审批与执行以任务页为准/);assert.ok(!h.text().includes('尚未开始业务执行'));assert.equal(h.f.state.posts.length,1);await h.click('查看对应任务');assert.deepEqual(h.navigations,[h.f.taskID]);assert.equal(h.f.state.posts.length,1);
}));
test('V247 actual delivered occurrence stays historical when its corresponding Task is executing or already accepted',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();await h.click('启用本次审批草稿授权');await h.until(()=>h.text().includes('启用决定已保存'));
 h.f.state.occurrences=[h.f.occurrence({status:'approval_draft',version:2,task_id:h.f.taskID})];h.f.state.routes[0].used_tasks=1;
 const opened=[];
 await h.update({onViewTask:async(taskID,checkEntry)=>{checkEntry();const current=await h.readTask();checkEntry();assert.equal(current.id,taskID);opened.push(current.status);}});
 for(const status of ['workflow_running','approval_accepted']){
  h.f.state.currentTaskStatus=status;await h.click('更新来源、授权与草稿进度');await h.until(()=>h.button('查看对应任务'));
  assert.equal(h.f.state.occurrences[0].status,'approval_draft');assert.match(h.text(),/已生成对应业务任务/);assert.match(h.text(),/这条异常已生成审批草稿，打开任务查看当前办理进度；后续审批与执行以任务页为准/);
  assert.ok(!h.text().includes('尚未开始业务执行'));assert.ok(!h.text().includes('等待人工核对、修改和提交正式审批'));
  await h.click('查看对应任务');await h.until(()=>opened.includes(status));
 }
 assert.deepEqual(opened,['workflow_running','approval_accepted']);assert.equal(h.f.state.posts.length,1);
}));
test('V247 actual failed source checks remain blocked after an explicit same-record check request',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();await h.click('启用本次审批草稿授权');await h.until(()=>h.text().includes('启用决定已保存'));const item=h.f.occurrence({status:'blocked_integrity',version:4,reason:'原始数据的Memory核对记录缺失'});h.f.state.occurrences=[item];await h.click('更新来源、授权与草稿进度');await h.until(()=>h.text().includes('原始数据核对未通过'));assert.match(h.text(),/Memory核对记录缺失/);await h.change('HTTP 异常重新检查理由','原始数据修复后，再检查本条记录');await h.click('重新检查此条异常');await h.until(()=>h.f.state.posts.length===2&&!h.container.querySelector('[aria-label="原 HTTP 审批草稿授权请求"]'));assert.equal(h.f.state.posts[1].body.expected_version,4);assert.equal(h.f.state.occurrences[0].status,'blocked_integrity');assert.ok(!h.button('查看对应任务'));assert.match(h.text(),/不重新采集/);
}));
test('V247 actual lost reply is recoverable after remount and a temporarily missing result cannot create a second grant',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();h.f.state.mode='lost';await h.click('启用本次审批草稿授权');await h.until(()=>h.button('核对上次操作结果')&&!h.button('核对上次操作结果').disabled);const raw=h.f.storage.getItem(h.f.prefix+'pending');assert.ok(raw);assert.equal(h.guard()(),true);await h.unmount();h.f.state.recoveryVisible=false;await h.mount();await h.until(()=>h.button('核对上次操作结果')&&!h.button('核对上次操作结果').disabled);await h.click('核对上次操作结果');await h.until(()=>h.button('按原条件重试上次操作'));assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),raw);assert.equal(h.f.state.posts.length,1);h.f.state.recoveryVisible=true;await h.click('核对上次操作结果');await h.until(()=>!h.container.querySelector('[aria-label="原 HTTP 审批草稿授权请求"]'));assert.equal(h.f.state.posts.length,1);assert.equal(h.f.storage.getItem(h.f.prefix+'original:'+JSON.parse(raw).body.request_id),raw);
}));
test('V247 actual paused instance keeps new enable blocked while allowing the saved authorization to be stopped',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();await h.click('启用本次审批草稿授权');await h.until(()=>h.text().includes('启用决定已保存'));await h.update({enabled:false});await h.until(()=>h.container.querySelector('[aria-label="HTTP 审批授权暂停理由"]')&&!h.container.querySelector('[aria-label="HTTP 审批授权暂停理由"]').matches(':disabled'));assert.equal(h.button('启用本次审批草稿授权').disabled,true);await h.change('HTTP 审批授权暂停理由','暂停实例期间不再接入新草稿');await h.click('暂停之后的新草稿');await h.until(()=>h.text().includes('暂停决定已保存'));assert.equal(h.f.state.routes[0].status,'paused');assert.equal(h.f.state.posts.length,2);
}));
test('V247 actual oversized and quota-failed input stays editable until corrected and reliably read back',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();await h.change('HTTP 审批授权启用理由','原'.repeat(8100));assert.equal(h.container.querySelector('[aria-label="HTTP 审批授权启用理由"]').value.length,8100);assert.equal(h.container.querySelector('[aria-label="HTTP 审批授权启用理由"]').matches(':disabled'),false);assert.equal(h.guard()(),false);await h.change('HTTP 审批授权启用理由','我主动缩短后的理由');await h.until(()=>h.guard()());h.f.storage.failSet=key=>key===h.f.prefix+'form';await h.change('HTTP 审批授权启用理由','保存失败仍保留的输入');await h.change('HTTP 审批授权启用理由','仍能继续纠正输入');assert.equal(h.guard()(),false);await h.click('另存 HTTP 授权人稿并允许离开');await h.until(()=>h.text().includes('当前人稿已独立保留'));assert.equal(h.guard()(),true);h.f.storage.failSet=null;await h.click('恢复第1份 HTTP 人稿');await h.until(()=>h.container.querySelector('[aria-label="HTTP 审批授权启用理由"]').value==='仍能继续纠正输入'&&!h.container.querySelector('[aria-label="HTTP 审批授权启用理由"]').matches(':disabled'));assert.equal(h.f.state.posts.length,0);
}));
test('V247 actual account switch during delayed enable hides old input and keeps the unknown original untouched',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill('只属于原账号的授权理由');let release;h.f.state.postGate=new Promise(resolve=>release=resolve);await h.click('启用本次审批草稿授权');await h.until(()=>h.f.state.posts.length===1);const raw=h.f.storage.getItem(h.f.prefix+'pending');h.f.session.setItem('ga-setup-csrf-user',id(90));h.f.state.identity={...h.f.account,user_id:id(90)};await h.update({account:h.f.state.identity});await act(async()=>release());await h.until(()=>!h.text().includes('只属于原账号的授权理由'));assert.equal(h.f.storage.getItem(h.f.prefix+'pending'),raw);assert.equal(h.f.storage.getItem(h.f.prefix+'saved:'+JSON.parse(raw).body.request_id),null);assert.equal(h.f.state.posts.length,1);
}));
test('V247 actual SignalStudio connects its parent guard to failed drafts while saved unknown operations may leave',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.ready();await h.fill();h.f.storage.failSet=key=>key===h.f.prefix+'form';await h.change('HTTP 审批授权启用理由','父界面必须保留的输入');let allowed;await act(async()=>{allowed=h.guard()();});assert.equal(allowed,false);h.f.storage.failSet=null;await h.click('重试保存 HTTP 授权人稿');await h.check('确认 HTTP 仅创建正式审批草稿');h.f.state.mode='unknown';await h.click('启用本次审批草稿授权');await h.until(()=>h.button('核对上次操作结果')&&!h.button('核对上次操作结果').disabled);await act(async()=>{allowed=h.guard()();});assert.equal(allowed,true);assert.ok(h.f.storage.getItem(h.f.prefix+'pending'));assert.equal(h.f.state.posts.length,1);
},{kind:'signals'}));
test('V247 actual legacy manual selector omits V243 whose displayed steps are human but execution mode is a graph',{concurrency:false,timeout:30000},()=>harness(async h=>{
 h.f.state.signals=[{id:id(60),version:1,entity_id:h.f.entityID,status:'unassigned',note:'',basis_hash:'3'.repeat(64),tasks:[],integrity_issue:null,basis:{candidate_only:false,release_id:h.f.releaseID,object_name:'东侧样地',source_name:'HTTP温湿度模拟',reported_at:'2026-10-29T01:00:00+00:00',extracted:{anomalies:[]},trigger_rules:[],original_attributes:{temperature:27}}}];await h.mount();await h.until(()=>h.container.querySelector('[aria-label="异常接收业务"]'));const choices=[...h.container.querySelector('[aria-label="异常接收业务"]').options];assert.ok(choices.some(option=>option.value===h.f.businessID));assert.ok(!choices.some(option=>option.value==='business_image_v243'));assert.equal(h.f.state.posts.length,0);
},{kind:'signals'}));
test('V247 actual approval draft shows original values, units, simulation, unverified time and data receipt before any submit or start',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.until(()=>h.container.querySelector('[aria-label="HTTP 异常原依据与独立收件"]'));const source=h.container.querySelector('[aria-label="HTTP 异常原依据与独立收件"]');assert.match(source.textContent,/东侧样地/);assert.match(source.textContent,/温度/);assert.match(source.textContent,/27/);assert.match(source.textContent,/℃/);assert.match(source.textContent,/模拟来源，未经现场验证/);assert.match(source.textContent,/来源时钟尚未认证/);assert.match(source.textContent,/数据管理智能体已接收/);assert.match(h.text(),/修订任务草稿/);assert.equal(h.f.state.posts.length,0);assert.equal(h.f.task().status,'approval_draft');
},{kind:'approval'}));
test('V247 actual damaged source or a lost task marker stays blocked and never displays a fabricated received value',{concurrency:false,timeout:30000},()=>harness(async h=>{
 h.f.state.sourceIssue='原异常记录存在，但任务来源标记缺失';await h.mount();await h.until(()=>h.container.querySelector('[aria-label="原异常依据完整性受阻"]'));assert.match(h.text(),/任务来源标记缺失/);assert.ok(!h.container.querySelector('[aria-label="HTTP 异常原依据与独立收件"]'));assert.equal(h.f.state.posts.length,0);h.f.state.sourceIssue=null;await h.click('重新核对原异常依据');await h.until(()=>h.container.querySelector('[aria-label="HTTP 异常原依据与独立收件"]'));assert.equal(h.f.state.posts.length,0);
},{kind:'approval'}));
test('V247 actual non-V247 approval task remains an ordinary editable draft without an invented HTTP origin',{concurrency:false,timeout:30000},()=>harness(async h=>{
 h.f.state.sourceFound=false;await h.mount();await h.until(()=>h.text().includes('修订任务草稿')&&h.f.state.reads.some(row=>row.path.endsWith('/signal-approval-source'))&&!h.text().includes('正在独立核对'));assert.ok(!h.container.querySelector('[aria-label="HTTP 异常原依据与独立收件"]'));assert.ok(!h.container.querySelector('[aria-label="原异常依据完整性受阻"]'));assert.equal(h.f.state.posts.length,0);
},{kind:'approval'}));
test('V247 actual approval source refresh does not replace human revisions and unsent work survives a cold remount',{concurrency:false,timeout:30000},()=>harness(async h=>{
 await h.mount();await h.until(()=>h.container.querySelector('[aria-label="HTTP 异常原依据与独立收件"]')&&[...h.container.querySelectorAll('label')].some(node=>node.textContent.startsWith('任务说明')));const note=()=>[...h.container.querySelectorAll('label')].find(node=>node.textContent.startsWith('任务说明')).querySelector('textarea');await h.changeNode(note(),'人工修改的审批说明必须保留');await h.click('重新核对原异常依据');await h.until(()=>h.container.querySelector('[aria-label="HTTP 异常原依据与独立收件"]'));assert.equal(note().value,'人工修改的审批说明必须保留');await h.unmount();await h.mount();await h.until(()=>[...h.container.querySelectorAll('label')].some(node=>node.textContent.startsWith('任务说明')));assert.equal(note().value,'人工修改的审批说明必须保留');assert.equal(h.f.state.posts.length,0);
},{kind:'approval'}));
