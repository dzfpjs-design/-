// Actual Shadow component, durable Journal and runtime client; HTTP is synthetic.
// No fixture below proves SQL/Worker authority, domain quality or browser layout.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){if(!bundle)bundle=(async()=>{
 temporary=await mkdtemp(join(tmpdir(),'threshold-shadow-react-v151-'));const entry=join(temporary,'entry.mjs');
 await writeFile(entry,'export {default as Shadow} from '+JSON.stringify(join(project,'src/ThresholdShadowV151.jsx'))+';\nexport {ShadowJournalV151,shadowScopeV151} from '+JSON.stringify(join(project,'src/threshold-shadow-contract-v151.mjs'))+';\nexport {ThresholdStudyJournalV149,studyScopeV149,emptyStudyFormV149} from '+JSON.stringify(join(project,'src/threshold-study-contract-v149.mjs'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
 const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
 const output=result.output.find(item=>item.type==='chunk'&&item.isEntry),path=join(temporary,'component.mjs');assert.ok(output,'actual component bundle exists');await writeFile(path,output.code);return import(pathToFileURL(path).href);
})();return bundle;}
const id=n=>'15110000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value)),sha=raw=>createHash('sha256').update(raw).digest('hex');
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-shadow-admin',role:'admin'},evaluationID=id(3),grantID=id(4),rootPath='/api/local-rules/evolution',csrf='synthetic-shadow-csrf';
const baseline={id:id(10),title:'原正式规则',field:'count',field_label:'数量',operator:'gt',threshold:20,unit:'株'},candidate={...baseline,id:id(11),title:'并列候选规则',threshold:18};
const range={workspace_id:account.workspace_id,instance_id:id(12),release_id:id(13),entity_id:id(14),source_id:id(15),baseline_id:baseline.id,baseline_hash:'a'.repeat(64),candidate_capability_id:candidate.id,candidate_hash:'b'.repeat(64),source_hash:'c'.repeat(64),rule_id:'count-warning',field:'count',field_label:'数量',unit:'株',operator:'gt',baseline_threshold:20,candidate_threshold:18,instance_name:'测试业务实例',release_name:'原启用配置',entity_name:'测试林木对象',source_name:'原数值登记来源',baseline_name:baseline.title,candidate_name:candidate.title};
const sample=(n,label,value)=>({id:id(n),version:1,root_id:id(n+100),observation_id:id(n+200),entity_id:id(30),entity_label:'独立测试对象',label,reason:'合成机制标签，不作领域质量证据',classification:label==='should_alert'?(value>20?'TP':'FN'):'TN',field_label:'数量',value,unit:'株',eligible:true,issue:null});
const samples=[sample(40,'should_alert',20),sample(41,'should_alert',22),sample(42,'should_not_alert',16),sample(43,'should_not_alert',18)];
const evaluation={schema:'threshold-study-evaluation-view-v149',workspace_id:account.workspace_id,id:evaluationID,version:9,status:'completed',actor_id:account.user_id,title:'已完成的合成机制比较',candidate_id:id(44),baseline,candidate,training:[],samples,criteria:{max_additional_reviews:1},purpose:'mechanism_check',limitations:'仅证明机制，不能批准领域应用',freeze_hash:'d'.repeat(64),job:{id:id(45),status:'succeeded',attempts:1,error:null},progress:{completed:8,total:8,reserved:8},result:{per_sample:samples.map(row=>({id:row.id,expected:row.label,value:row.value,baseline_matched:row.value>20,candidate_matched:row.value>18,issue:null})),baseline:{tp:1,tn:2,fp:0,fn:1,unknown:0,invalid:0,alerts:1},candidate:{tp:2,tn:2,fp:0,fn:0,unknown:0,invalid:0,alerts:2},mechanism_gate_passed:true,issues:[]},error:null,current_issue:null,can_edit:false,can_freeze:false,can_run:false,can_cancel:false,eligible_for_trial:false};
const issue='原来源已停用；人工批准记录保留，原数值和机器结果暂不可用';
const expiry=()=>new Date(Date.now()+2*24*60*60*1000).toISOString().slice(0,16);
function grant({status='active',version=1,sourceBad=false,used=0,title='原已批准影子观察',reason='原批准理由',expires_at='2026-10-25T12:00:00+00:00'}={}){
 const consumptions=used?[{schema:'threshold-shadow-consumption-view-v151',id:id(50),observation_id:id(51),source_record_id:'synthetic-original-record',status:'completed',job_id:id(52),job_status:'succeeded',error:null,values:sourceBad?null:{count:20},baseline_matched:sourceBad?null:false,candidate_matched:sourceBad?null:true,invocation_id:id(53),late:false,created_at:'2026-10-21T12:00:00+00:00'}]:[];
 const human_history=[{version:1,operation:'create',reason:version===1?reason:'原批准理由',actor_id:account.user_id}];for(let versionIndex=2;versionIndex<=version;versionIndex++)human_history.push({version:versionIndex,operation:versionIndex===version&&status==='ended'?'end':versionIndex%2===0?'pause':'resume',reason:versionIndex===version?reason:'原第'+versionIndex+'版操作理由',actor_id:account.user_id});
 return {schema:'threshold-shadow-view-v151',workspace_id:account.workspace_id,id:grantID,version,actor_id:account.user_id,status,title,reason,evaluation_id:evaluationID,scope:{...range,baseline_threshold:sourceBad?null:range.baseline_threshold,candidate_threshold:sourceBad?null:range.candidate_threshold},baseline:{...baseline,threshold:sourceBad?null:20},candidate:{...candidate,threshold:sourceBad?null:18},max_inputs:2,used_inputs:used,expires_at,epoch:1,current_issue:sourceBad?issue:status==='paused'?'影子观察已暂停，明确恢复后仅处理新登记':null,can_pause:status==='active',can_resume:status==='paused'&&!sourceBad,can_end:status!=='ended',eligible_for_trial:false,consumptions,human_history};
}
function keys(value,expected){assert.deepEqual(Object.keys(value).sort(),[...expected].sort());}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).then(()=>callback({name,mode:'exclusive'})),tail=next.then(()=>{},()=>{});tails.set(name,tail);return next.finally(()=>{if(tails.get(name)===tail)tails.delete(name);});}};}

async function harness(run,{savedGrant=null,sourceBad=false,blockedReason=''}={}){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-shadow.invalid/',pretendToBeVisual:true,resources:new NoResources()}),calls=[],errors=[],violations=[];
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,state={grant:savedGrant?copy(savedGrant):null,sourceBad,blockedReason,authStatus:200,postMode:'normal',failStorage:false,commands:new Map(),originals:new Map()};
 let root,activeKey;
 const storageProto=Object.getPrototypeOf(storage),setItem=storageProto.setItem;
 storageProto.setItem=function(key,value){if(this===storage&&state.failStorage&&String(key).startsWith('ga-threshold-shadow-v151:')&&String(key).includes(':draft:'))throw Error('synthetic shadow draft quota');return setItem.call(this,key,value);};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const original=()=>{const raw=storage.getItem(activeKey);assert.ok(raw,'actual Journal must persist the original before HTTP');const value=JSON.parse(raw);assert.equal(value.schema,'threshold-shadow-original-v151');assert.equal(value.method,'POST');assert.equal(value.scope.actor_id,account.user_id);assert.equal(value.scope.workspace_id,account.workspace_id);assert.equal(value.scope.evaluation_id,evaluationID);assert.equal(value.wire_sha256,sha(value.exact_body));state.originals.set(value.request_id,copy(value));return value;};
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET',call={method,path:url.pathname,search:url.search,raw:options.body};calls.push(call);try{
  assert.equal(url.origin,dom.window.location.origin);assert.equal(options.credentials,'same-origin');const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);
  if(url.pathname==='/api/me'){assert.equal(method,'GET');return state.authStatus===200?response(account):response({detail:'当前管理员身份已失效'},state.authStatus);}
  if(method==='GET'&&url.pathname===rootPath+'/threshold-shadow-options'){
   assert.equal(url.searchParams.get('evaluation_id'),evaluationID);
   if(state.sourceBad)return response({detail:issue},409);
   return response({schema:'threshold-shadow-options-v151',workspace_id:account.workspace_id,evaluation_id:evaluationID,evaluation_version:evaluation.version,scope:copy(range),baseline:copy(baseline),candidate:copy(candidate),eligible:true,issue:null});
  }
  if(method==='GET'&&url.pathname===rootPath+'/threshold-shadows'){assert.equal(url.searchParams.get('evaluation_id'),evaluationID);return response({schema:'threshold-shadow-list-v151',workspace_id:account.workspace_id,grants:state.grant?[copy(state.grant)]:[],next_after:null});}
  if(method==='GET'&&url.pathname===rootPath+'/threshold-shadows/'+grantID){assert.ok(state.grant);return response(copy(state.grant));}
  if(method==='GET'&&url.pathname.startsWith(rootPath+'/threshold-shadow-requests/')){
   const sent=original();assert.equal(url.pathname,rootPath+'/threshold-shadow-requests/'+sent.request_id);assert.equal(url.searchParams.get('expected_sha256'),sent.wire_sha256);assert.equal(url.searchParams.get('original_method'),'POST');assert.equal(url.searchParams.get('original_path'),sent.path);
   const saved=state.commands.get(sent.request_id);if(saved){assert.equal(saved.raw,sent.exact_body);assert.equal(saved.path,sent.path);}
   return response(saved?{schema:'threshold-shadow-request-state-v151',status:'saved',target_id:grantID,version:saved.view.version,http_status:saved.http_status,detail:null}:{schema:'threshold-shadow-request-state-v151',status:'unknown',target_id:null,version:null,http_status:null,detail:null});
  }
  if(method==='POST'&&(url.pathname===rootPath+'/threshold-shadows'||url.pathname.startsWith(rootPath+'/threshold-shadows/'+grantID+'/'))){
   assert.equal(state.authStatus,200);assert.equal(headers.get('X-CSRF-Token'),csrf);const sent=original(),body=JSON.parse(options.body);assert.equal(options.body,sent.exact_body,'exact durable original wire is sent');assert.equal(url.pathname,sent.path);assert.equal(body.request_id,sent.request_id);
   const previous=state.commands.get(body.request_id);if(previous){assert.equal(previous.raw,options.body);assert.equal(previous.path,url.pathname);return response(copy(previous.view),previous.http_status);}
   if(state.postMode==='lost_unknown'){state.postMode='normal';throw Error('synthetic shadow transport response unknown');}
   let status=200;
   if(url.pathname===rootPath+'/threshold-shadows'){
    keys(body,['schema','request_id','evaluation_id','expected_evaluation_version','title','reason','max_inputs','expires_at','confirm_shadow_only']);assert.equal(body.schema,'threshold-shadow-create-v151');assert.equal(body.evaluation_id,evaluationID);assert.equal(body.expected_evaluation_version,evaluation.version);assert.equal(body.confirm_shadow_only,true);assert.equal(body.max_inputs,2);assert.equal(typeof body.max_inputs,'number');assert.ok(body.title.trim()&&body.reason.trim());assert.match(body.expires_at,/T\d{2}:\d{2}:00\+00:00$/);assert.equal(state.sourceBad,false);assert.equal(state.grant,null);
    state.grant=grant({title:body.title,reason:body.reason,expires_at:body.expires_at});status=201;
   }else{
    const action=url.pathname.slice((rootPath+'/threshold-shadows/'+grantID+'/').length);assert.ok(['pause','resume','end'].includes(action));keys(body,['schema','request_id','expected_version','reason','confirm']);assert.equal(body.schema,'threshold-shadow-control-v151');assert.equal(body.expected_version,state.grant.version);assert.equal(body.confirm,true);assert.ok(body.reason.trim());if(action==='resume')assert.equal(state.sourceBad,false);
    const prior=state.grant,next=action==='pause'?'paused':action==='resume'?'active':'ended';state.grant={...prior,status:next,version:prior.version+1,reason:body.reason,human_history:[...prior.human_history,{version:prior.version+1,operation:action,reason:body.reason,actor_id:account.user_id}],epoch:prior.epoch+(action==='resume'?1:0),current_issue:state.sourceBad?issue:next==='paused'?'影子观察已暂停，明确恢复后仅处理新登记':null,can_pause:next==='active',can_resume:next==='paused'&&!state.sourceBad,can_end:next!=='ended'};
   }
   const saved={raw:options.body,path:url.pathname,http_status:status,view:copy(state.grant)};state.commands.set(body.request_id,saved);
   if(state.postMode==='lost_saved'){state.postMode='normal';throw Error('synthetic shadow transport response lost after save');}
   return response(copy(saved.view),status);
  }
  throw Error('Unexpected shadow HTTP '+method+' '+url.pathname);
 }catch(cause){if(!String(cause.message).startsWith('synthetic shadow transport'))violations.push(cause.stack);throw cause;}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 try{
  const actual=await component();assert.equal(actual.BundledReact,React);actual.setSession(csrf,account.workspace_id,account.user_id,account.username);
  const probe=new actual.ShadowJournalV151(actual.shadowScopeV151(account,evaluation));activeKey=probe.active;
  const old=new actual.ThresholdStudyJournalV149(actual.studyScopeV149(account,range.instance_id,{id:id(90),extractor_id:baseline.id}));old.open();const oldForm=actual.emptyStudyFormV149();oldForm.candidate.title='旧比较方案的人稿必须独立保留';oldForm.candidate.rationale='旧理由不得由影子操作覆盖';old.save(oldForm);old.dispose();
  const oldSnapshot=()=>Object.fromEntries(Array.from({length:storage.length},(_,index)=>storage.key(index)).filter(key=>key.startsWith(old.prefix)).map(key=>[key,storage.getItem(key)])),oldBefore=oldSnapshot(),oldHead=session.getItem(old.prefix+':head');
  const container=dom.window.document.getElementById('root'),settle=async()=>{for(let i=0;i<8;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});};
  const wait=async(predicate,label)=>{for(let i=0;i<80;i++){if(predicate())return;await settle();}assert.ok(predicate(),label+'; '+container.textContent.slice(0,700));};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label),field=label=>container.querySelector('[aria-label="'+label+'"]');
  const click=async label=>{const node=button(label);assert.ok(node,'actual '+label+' exists');assert.equal(node.disabled,false,label+' is available');await act(async()=>node.click());await settle();};
  const edit=async(label,value)=>{const node=field(label);assert.ok(node,'actual '+label+' exists');assert.equal(node.disabled,false,label+' remains editable');await act(async()=>{node.value=value;node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  const confirm=async()=>{const nodes=[...container.querySelectorAll('input[type="checkbox"]')].filter(node=>!node.disabled),node=nodes.at(-1);assert.ok(node,'explicit confirmation exists');assert.equal(node.checked,false,'confirmation has no default consent');await act(async()=>node.click());await settle();};
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Shadow,{evaluation,account,Btn:Button,blockedReason:state.blockedReason}))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:cause=>errors.push(cause.message),onCaughtError:cause=>errors.push(cause.message)});await render();};
  const remount=async()=>{await act(async()=>root.unmount());root=null;await mount();};
  const open=async()=>{await click('查看影子观察');await wait(()=>button('刷新影子观察')?.disabled===false,'scope identity and readonly shadow history load');};
  const fill=async({quota='2'}={})=>{await edit('影子观察名称','仅本对象的未来登记影子观察');await edit('批准理由','管理员明确批准并列观察，不更改正式处理');await edit('最多新登记数',quota);await edit('截止时间',expiry());await edit('时间基准','+00:00');await confirm();};
  const persisted=value=>Array.from({length:storage.length},(_,index)=>storage.getItem(storage.key(index))).filter(Boolean).some(raw=>{const contains=item=>item===value||!!item&&typeof item==='object'&&Object.values(item).some(contains);try{return contains(JSON.parse(raw));}catch{return raw===value;}});
  const unloading=()=>{const event=new dom.window.Event('beforeunload',{cancelable:true});dom.window.dispatchEvent(event);return event.defaultPrevented;};
  await mount();assert.equal(calls.length,0,'closed Shadow does not fetch or POST');
  await run({container,calls,state,storage,session,activeKey,button,field,click,edit,confirm,open,fill,wait,persisted,unloading,remount,render});
  assert.deepEqual(oldSnapshot(),oldBefore,'new shadow Journal cannot clear or replace the original comparison draft');assert.equal(session.getItem(old.prefix+':head'),oldHead);assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{if(root)await act(async()=>root.unmount());storageProto.setItem=setItem;dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}

test('V151 explicit preview and approval use the actual independent Journal without changing formal processing',async()=>{
 await harness(async({container,calls,state,open,fill,click,wait,storage,activeKey})=>{
  await open();assert.ok(calls.every(call=>call.method==='GET'));assert.ok(container.textContent.includes('影子观察，不改变正式处理'));assert.ok(container.textContent.includes(range.entity_name)&&container.textContent.includes(range.source_name));
  await fill();assert.equal(calls.filter(call=>call.method==='POST').length,0,'editing/confirming never silently approves');await click('批准影子观察');
  await wait(()=>state.grant&&storage.getItem(activeKey)===null,'independently read original approval settles');const posts=calls.filter(call=>call.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].path,rootPath+'/threshold-shadows');assert.equal(state.grant.eligible_for_trial,false);
 });
});

test('V151 saved approval with lost response remounts and finds the same nonce using GET only',async()=>{
 await harness(async({calls,state,open,fill,click,wait,storage,activeKey,remount})=>{
  await open();await fill();state.postMode='lost_saved';await click('批准影子观察');await wait(()=>state.grant&&storage.getItem(activeKey)!==null,'lost reply keeps the actual original outbox');const raw=storage.getItem(activeKey),sent=JSON.parse(raw),posts=calls.filter(call=>call.method==='POST').length,before=calls.length;
  await remount();await open();assert.equal(calls.filter(call=>call.method==='POST').length,posts,'remount cannot replay a saved approval');assert.equal(storage.getItem(activeKey),raw);await click('核对原影子请求');await wait(()=>storage.getItem(activeKey)===null,'saved original nonce is independently resolved');
  assert.ok(calls.slice(before).every(call=>call.method==='GET'));assert.ok(calls.slice(before).some(call=>call.path===rootPath+'/threshold-shadow-requests/'+sent.request_id));assert.equal(state.commands.size,1);
 });
});

test('V151 unknown original approval is retained across remount and repeated lookup without a replacement nonce',async()=>{
 await harness(async({calls,state,open,fill,click,wait,storage,activeKey,remount})=>{
  await open();await fill();state.postMode='lost_unknown';await click('批准影子观察');await wait(()=>storage.getItem(activeKey)!==null,'unknown request remains original');const raw=storage.getItem(activeKey),sent=JSON.parse(raw),before=calls.length;
  await remount();await open();await click('核对原影子请求');await click('核对原影子请求');assert.equal(storage.getItem(activeKey),raw);assert.equal(state.grant,null);assert.equal(state.commands.size,0);assert.ok(calls.slice(before).every(call=>call.method==='GET'));assert.ok(calls.slice(before).filter(call=>call.path.includes('/threshold-shadow-requests/')).every(call=>call.path.endsWith('/'+sent.request_id)));assert.equal(calls.filter(call=>call.method==='POST').length,1);
 });
});

test('V151 unavailable source keeps safe history and permits explicit pause and permanent end without fresh approval',async()=>{
 await harness(async({container,calls,state,open,click,edit,confirm,wait,button})=>{
  await open();await click('查看这项观察');await wait(()=>button('暂停影子观察')?.disabled===false,'the original grant is explicitly opened from its safe history');assert.ok(container.textContent.includes(issue));assert.ok(container.textContent.includes('原批准理由'));assert.ok(!button('批准影子观察')||button('批准影子观察').disabled);
  const cells=[...container.querySelectorAll('tbody tr')][0]?.children;assert.ok(cells,'original receipt remains inspectable');assert.equal(cells[2].textContent,'依据不可用或尚无结果');assert.equal(cells[3].textContent,'未展示');assert.equal(cells[4].textContent,'未展示');
  await click('暂停影子观察');await edit('操作理由','原来源停用，停止后续预约');await confirm();await click('确认暂停影子观察');await wait(()=>state.grant.status==='paused'&&button('结束影子观察')?.disabled===false,'source failure does not block pause');
  assert.ok(!button('恢复影子观察')||button('恢复影子观察').disabled);await click('结束影子观察');await edit('操作理由','明确永久结束，保留已占用额度和原回执');await confirm();await click('确认结束影子观察');await wait(()=>state.grant.status==='ended','original authorization is permanently ended');
  assert.equal(state.grant.used_inputs,1);assert.equal(state.grant.consumptions[0].values,null);assert.equal(state.grant.consumptions[0].candidate_matched,null);assert.equal(state.grant.scope.baseline_threshold,null);assert.equal(state.grant.scope.candidate_threshold,null);assert.equal(state.grant.eligible_for_trial,false);assert.ok(container.textContent.includes('原批准理由'),'latest stop reason does not hide the original human approval history');const posts=calls.filter(call=>call.method==='POST');assert.deepEqual(posts.map(call=>call.path),[rootPath+'/threshold-shadows/'+grantID+'/pause',rootPath+'/threshold-shadows/'+grantID+'/end']);
 },{savedGrant:grant({sourceBad:true,used:1}),sourceBad:true,blockedReason:'当前依据禁止新执行'});
});

test('V151 fresh identity403 blocks approval while retaining actual persisted human input',async()=>{
 await harness(async({container,calls,state,open,fill,click,wait,persisted})=>{
  await open();await fill();assert.equal(persisted('管理员明确批准并列观察，不更改正式处理'),true);const before=calls.length;state.authStatus=403;await click('批准影子观察');await wait(()=>calls.slice(before).some(call=>call.path==='/api/me')&&!container.querySelector('[aria-label="批准理由"]'),'403 masks the formerly verified view');assert.equal(calls.filter(call=>call.method==='POST').length,0);assert.equal(persisted('管理员明确批准并列观察，不更改正式处理'),true);assert.equal(state.grant,null);
 });
});

test('V151 explicit resume honors can_resume despite a pause notice and preserves the used quota and deadline',async()=>{
 const original=grant({status:'paused',version:4,used:1,reason:'原第四版暂停理由'});
 await harness(async({container,calls,state,open,click,edit,confirm,wait})=>{
  await open();await click('查看这项观察');await wait(()=>container.textContent.includes('影子观察已暂停')&&!container.querySelector('[aria-label="操作理由"]'),'paused grant is explicitly cold-read');
  await click('恢复影子观察');await edit('操作理由','只恢复原期限内的新登记，不追处理暂停期间的记录');await confirm();await click('确认恢复影子观察');await wait(()=>state.grant.status==='active','explicit resume is independently saved');
  const posts=calls.filter(call=>call.method==='POST');assert.equal(posts.length,1);assert.equal(posts[0].path,rootPath+'/threshold-shadows/'+grantID+'/resume');assert.equal(JSON.parse(posts[0].raw).expected_version,4);assert.equal(state.grant.used_inputs,original.used_inputs);assert.equal(state.grant.max_inputs,original.max_inputs);assert.equal(state.grant.expires_at,original.expires_at);assert.equal(state.grant.eligible_for_trial,false);
 },{savedGrant:original});
});

test('V151 an out-of-range raw input quota is never clamped and survives close and remount',async()=>{
 await harness(async({calls,open,fill,click,button,field,persisted,remount})=>{
  await open();await fill({quota:'31'});assert.equal(field('最多新登记数').value,'31');assert.equal(persisted('31'),true);if(button('批准影子观察')&&!button('批准影子观察').disabled)await click('批准影子观察');assert.equal(calls.filter(call=>call.method==='POST').length,0);assert.equal(field('最多新登记数').value,'31');await remount();await open();assert.equal(field('最多新登记数').value,'31');assert.equal(calls.filter(call=>call.method==='POST').length,0);
 });
});

test('V151 local draft quota protects human work and beforeunload until explicit persistence succeeds',async()=>{
 await harness(async({calls,state,open,edit,click,button,field,wait,unloading,persisted,remount})=>{
  await open();state.failStorage=true;await edit('影子观察名称','配额失败后仍要保留的影子说明');assert.equal(field('影子观察名称').value,'配额失败后仍要保留的影子说明');assert.equal(unloading(),true,'unsaved human work prevents unloading');assert.ok(!button('批准影子观察')||button('批准影子观察').disabled);await click('收起影子观察');assert.ok(field('影子观察名称'),'failed local persistence cannot hide the unsaved draft');
  state.failStorage=false;await click('重试保存本机稿');await wait(()=>!unloading(),'successful exact readback releases only this draft warning');assert.equal(persisted('配额失败后仍要保留的影子说明'),true);await remount();await open();assert.equal(field('影子观察名称').value,'配额失败后仍要保留的影子说明');assert.equal(calls.filter(call=>call.method==='POST').length,0);
 });
});
