// Actual React component and runtime HTTP client. The transport below is a
// synthetic fixture, not evidence of SQL durability, race safety or a browser.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto,createHash} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'threshold-feedback-v148-'));const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as Feedback} from '+JSON.stringify(join(project,'src/ThresholdFeedbackV147.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(item=>item.type==='chunk'&&item.isEntry),path=join(temporary,'component.mjs');assert.ok(output);await writeFile(path,output.code);return import(pathToFileURL(path).href);
 })();return bundle;
}
const id=n=>'14800000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value));
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-recovery-operator',role:'operator'},instance=id(3),observation=id(4),capability=id(5),feedback=id(6),rootPath='/api/local-rules/evolution';
const record={id:observation,version:1,extractor_id:capability,review_basis:{observed_at:'2026-10-21T09:00:00+08:00'}};
const basis={schema:'threshold-evolution-feedback-basis-v147',basis_hash:'a'.repeat(64),observation_id:observation,observation_version:1,baseline_capability_id:capability,baseline_capability_hash:'b'.repeat(64),rule_id:'count_high',field:'count',field_label:'机器字段不得显示为已核实历史',unit:null,operator:'gt',threshold:543210.5,actual_value:918273.25,actual_hit:true,instance_id:instance,release_id:id(7),entity_id:id(8),source_id:id(9)};
const sha=value=>createHash('sha256').update(value).digest('hex');
// The receipt oracle has only strings; sorted JSON is the frozen UTF-8
// canonical encoding here, with no inferred floating point representation.
function canonical(value){if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';return JSON.stringify(value);}
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).catch(()=>{}).then(callback);tails.set(name,next);return next.finally(()=>{if(tails.get(name)===next)tails.delete(name);});}};}
function savedView(label='should_alert',reason='原服务器人工理由',status='draft',version=1){
 const entry={id:feedback,version,root_id:feedback,revision:1,previous_id:null,status,actor_id:account.user_id,label,reason,cancel_reason:null,classification:label==='should_alert'?'TP':label==='should_not_alert'?'FP':'unknown'};
 return {schema:'threshold-evolution-feedback-view-v147',...entry,basis:copy(basis),history:[copy(entry)],latest_submitted:status==='submitted'?copy(entry):null,can_edit:status==='draft',can_cancel:status==='draft',can_submit:status==='draft'&&label!==null&&!!reason.trim(),can_revise:status!=='draft'};
}
function historyView(view){
 const human=value=>Object.fromEntries(['id','version','root_id','revision','previous_id','status','actor_id','label','reason','cancel_reason'].map(key=>[key,value[key]]));
 return {schema:'threshold-evolution-feedback-history-v148',entry:human(view),history:view.history.map(human),latest_submitted:view.latest_submitted?human(view.latest_submitted):null,basis_reference:Object.fromEntries(['observation_id','instance_id','release_id','baseline_capability_id','rule_id','basis_hash'].map(key=>[key,basis[key]])),basis_available:false,issue:'原处理依据目前无法完整核对，历史理由保留，不可用于策略。',eligible_for_candidate:false,can_edit:false,can_cancel:false,can_submit:false,can_revise:false};
}
function snapshot(view){
 const value={id:view.id,workspace_id:account.workspace_id,actor_id:view.actor_id,root_id:view.root_id,revision:view.revision,previous_id:view.previous_id,observation_id:observation,baseline_capability_id:capability,rule_id:basis.rule_id,status:view.status,version:view.version,data:{schema:'threshold-evolution-feedback-record-v147',basis:copy(basis),label:view.label,reason:view.reason,cancel_reason:view.cancel_reason}};
 value.data.record_hash=sha(canonical(value));return value;
}
function containsString(storage,expected){
 const visit=value=>value===expected||!!value&&typeof value==='object'&&Object.values(value).some(visit);
 return Object.keys(storage).some(key=>{const raw=storage.getItem(key);if(raw===expected)return true;try{return visit(JSON.parse(raw));}catch{return false;}});
}
async function harness(run){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-feedback-recovery.invalid/',pretendToBeVisual:true,resources:new NoResources()}),calls=[],violations=[],errors=[],gates=[];
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,state={saved:savedView(),basisAvailable:true,commands:new Map(),retirement:null,retireAttempts:[],rejectFirstPUT:true,original:null,loseRetireBeforeCommit:false,statusGate:null,statusEntered:false,failStorage:null,ruleFailure:null};
 const storageProto=Object.getPrototypeOf(storage),originalSet=storageProto.setItem;
 storageProto.setItem=function(key,value){if(String(key).startsWith('ga-threshold-feedback-v147:')&&(state.failStorage==='draft'&&String(key).includes(':draft:')||state.failStorage==='retirement-form'&&String(key).includes(':retirement-form:')))throw Error('synthetic first-open storage quota');return originalSet.call(this,key,value);};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const respond=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 function originalFor(nonce){
  const command=state.commands.get(nonce);if(command)return command;if(state.original&&JSON.parse(state.original.raw).request_id===nonce)return state.original;
  // A fresh command is correctly looked up BEFORE its first PUT. Verify that
  // preflight against its already-persisted wire, without pretending it ran.
  const candidates=[],visit=value=>{if(!value||typeof value!=='object')return;if(typeof value.exact_body==='string'){const body=JSON.parse(value.exact_body);if(body.request_id===nonce&&['POST','PUT'].includes(value.method)&&typeof value.path==='string')candidates.push({raw:value.exact_body,method:value.method,path:value.path});}Object.values(value).forEach(visit);};
  for(const key of Object.keys(storage)){try{visit(JSON.parse(storage.getItem(key)));}catch(error){if(error instanceof assert.AssertionError)throw error;}}
  assert.ok(candidates.length,'a fresh preflight must already have a durable original wire');assert.ok(candidates.every(value=>JSON.stringify(value)===JSON.stringify(candidates[0])));return candidates[0];
 }
 function checkOriginal(url,nonce){
  const original=originalFor(nonce);assert.equal(url.searchParams.get('expected_sha256'),sha(original.raw));assert.equal(url.searchParams.get('original_method'),original.method);assert.equal(url.searchParams.get('original_path'),original.path);
 }
 function requestState(nonce){
  const original=originalFor(nonce),command=state.commands.get(nonce),retirement=state.retirement?.original_request_id===nonce?state.retirement:null;
  return {schema:'threshold-request-state-v148',original_request_id:nonce,original_method:original.method,original_path:original.path,original_sha256:sha(original.raw),outcome:command?'saved':retirement?'retired':'unknown',saved:command?{command_id:command.id,feedback_id:command.view.id,root_id:command.view.root_id,revision:command.view.revision,version:command.view.version,status:command.view.status,result_hash:sha(canonical(command.snapshot))}:null,retirement:copy(retirement)};
 }
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET',call={method,path:url.pathname,search:url.search,raw:options.body};calls.push(call);
  try{
   if(url.pathname==='/api/me')return respond(copy(account));
   assert.equal(new Headers(options.headers).get('X-Workspace-ID'),account.workspace_id);
   if(method==='GET'&&url.pathname==='/api/local-rules/'+capability)return state.ruleFailure?respond({detail:state.ruleFailure===403?'当前没有读取规则的权限':'原规则资料目前无法核对'},state.ruleFailure):respond({id:capability,rules:[{id:basis.rule_id,field:basis.field,operator:basis.operator,unit:basis.unit,threshold:basis.threshold}],context_available:false,status:'withdrawn'});
   if(method==='GET'&&url.pathname.endsWith('/feedback-context'))return respond({schema:'threshold-evolution-feedback-context-v147',supported:state.basisAvailable,issue:state.basisAvailable?null:'原处理依据目前无法完整核对',basis:state.basisAvailable?copy(basis):null,feedback:state.basisAvailable?copy(state.saved):null});
   if(method==='GET'&&url.pathname===rootPath+'/observations/'+observation+'/feedback-history'){
    assert.ok(url.searchParams.get('rule_id')===null||url.searchParams.get('rule_id')===basis.rule_id);return respond({schema:'threshold-feedback-history-lookup-v148',found:true,history:historyView(state.saved)});
   }
   if(method==='GET'&&url.pathname===rootPath+'/feedback/'+feedback+'/history')return respond(historyView(state.saved));
   if(method==='GET'&&url.pathname===rootPath+'/feedback/'+feedback)return state.basisAvailable?respond(copy(state.saved)):respond({detail:'原处理依据目前无法完整核对'},409);
   const statusMatch=url.pathname.match(/\/requests\/([^/]+)\/status$/);
   if(method==='GET'&&statusMatch){
    checkOriginal(url,statusMatch[1]);const value=requestState(statusMatch[1]);
    if(value.outcome==='retired'&&state.statusGate){state.statusEntered=true;await state.statusGate.promise;}return respond(value);
   }
   const retireMatch=url.pathname.match(/\/requests\/([^/]+)\/retire$/);
   if(method==='POST'&&retireMatch){
    const body=JSON.parse(options.body);assert.deepEqual(Object.keys(body).sort(),['schema','request_id','original_method','original_path','original_wire_text','original_sha256','reason','confirm'].sort());assert.equal(body.schema,'threshold-request-retirement-command-v148');assert.equal(body.confirm,true);assert.equal(retireMatch[1],JSON.parse(state.original.raw).request_id);assert.notEqual(body.request_id,retireMatch[1]);assert.equal(body.original_method,'PUT');assert.equal(body.original_path,state.original.path);assert.equal(body.original_wire_text,state.original.raw);assert.equal(body.original_sha256,sha(state.original.raw));assert.ok(body.reason.trim());
    assert.ok(containsString(storage,options.body),'the exact retirement wire must be durably read back before its POST');assert.ok(containsString(storage,state.original.raw),'the original PUT wire must still be retained before retirement');
    const first=state.retireAttempts[0];if(first){assert.equal(options.body,first.raw);assert.equal(body.request_id,first.body.request_id);}state.retireAttempts.push({raw:options.body,body:copy(body)});
    if(state.loseRetireBeforeCommit){state.loseRetireBeforeCommit=false;throw Error('synthetic retirement reply lost before fixture commit');}
    if(!state.retirement){const receipt={schema:'threshold-request-retirement-receipt-v148',id:id(30),request_id:body.request_id,original_request_id:retireMatch[1],actor_id:account.user_id,workspace_id:account.workspace_id,original_method:body.original_method,original_path:body.original_path,original_sha256:body.original_sha256,reason:body.reason,outcome:'retired'};state.retirement={...receipt,receipt_hash:sha(canonical(receipt))};}
    return respond(requestState(retireMatch[1]));
   }
   if(method==='GET'&&url.pathname.startsWith(rootPath+'/requests/')){
    const nonce=url.pathname.split('/').at(-1);checkOriginal(url,nonce);const command=state.commands.get(nonce);return state.basisAvailable?respond({schema:'threshold-evolution-request-recovery-v147',found:!!command,feedback:command?copy(command.view):null}):respond({detail:'原处理依据目前无法完整核对'},409);
   }
   if(method==='PUT'&&url.pathname===rootPath+'/feedback/'+feedback){
    const body=JSON.parse(options.body);assert.ok(containsString(storage,options.body),'the actual original wire is saved before each feedback PUT');
    if(state.rejectFirstPUT){state.rejectFirstPUT=false;state.original=copy(call);return respond({detail:'本次保存被拒绝，请保留原稿核对'},409);}
    if(options.body===state.original.raw)return respond({detail:state.retirement?'此原请求已停止接收':'原保存仍被拒绝'},409);
    assert.ok(state.retirement,'no fresh feedback nonce before the permanent retirement fixture exists');assert.notEqual(body.request_id,JSON.parse(state.original.raw).request_id);
    state.saved=savedView(body.label,body.reason,'draft',state.saved.version+1);state.commands.set(body.request_id,{id:id(40),raw:options.body,method,path:url.pathname,view:copy(state.saved),snapshot:snapshot(state.saved)});return respond(copy(state.saved));
   }
   throw Error('Unexpected route '+method+' '+url.pathname);
  }catch(error){if(!String(error.message).startsWith('synthetic retirement reply lost'))violations.push(error.stack);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 let root;const container=dom.window.document.getElementById('root');
 try{
  const actual=await component(),renderer=await import('react-dom/client');assert.equal(actual.BundledReact,React);actual.setSession('synthetic-recovery-csrf',account.workspace_id,account.user_id,account.username);
  const settle=async(turns=8)=>{for(let i=0;i<turns;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});};
  const mount=async()=>{root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Feedback,{record,instanceId:instance,account,Btn:Button}))));await settle();};
  const wait=async(predicate,label)=>{for(let i=0;i<100;i++){if(predicate())return;await settle(1);}assert.ok(predicate(),label+' '+container.textContent.slice(0,1000));};
  const button=label=>[...container.querySelectorAll('button')].find(node=>typeof label==='string'?node.textContent===label:label.test(node.textContent));
  const click=async label=>{const node=button(label);assert.ok(node,'actual '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const input=label=>container.querySelector('[aria-label="'+label+'"]')||[...container.querySelectorAll('label')].find(node=>node.textContent.startsWith(label))?.querySelector('input,textarea,select');
  const edit=async(label,value)=>{const node=input(label);assert.ok(node,'actual field '+label);assert.equal(node.disabled,false);const prototype=node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:node.tagName==='INPUT'?dom.window.HTMLInputElement.prototype:dom.window.HTMLTextAreaElement.prototype;await act(async()=>{Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  const gate=()=>{const value=deferred();gates.push(value);return value;};
  await run({state,calls,container,storage,session,dom,mount,settle,wait,button,click,input,edit,gate,unmount:async()=>{await act(async()=>root.unmount());root=null;}});assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{gates.forEach(value=>value.resolve());if(root)await act(async()=>root.unmount());storageProto.setItem=originalSet;dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}
const retireButton='停止接收这个未保存请求',confirmRetireButton='确认停止接收原请求';
async function rejectedPUT(h,reason){
 await h.mount();assert.equal(h.calls.length,0);await h.click('纠正提示判断');await h.edit('判断理由',reason);await h.click('保存判断草稿');await h.wait(()=>!!h.state.original,'the actual component PUT was rejected');assert.equal(h.state.commands.size,0);assert.equal(h.input('判断理由').value,reason);await h.click('核对原判断请求');
}
const originalWrites=h=>h.calls.filter(row=>row.method==='PUT'&&row.path===h.state.original.path&&row.raw===h.state.original.raw);
async function beginRetirement(h){await h.click(retireButton);await h.edit('停止接收理由','保留这份原请求，核对后改正本窗口理由');await h.click(confirmRetireButton);}
function checkedLocalInitialization(h){
 const prefix='ga-threshold-feedback-v147:'+Object.values({actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:instance,observation_id:observation}).join(':'),owner=h.session.getItem(prefix+':head');assert.ok(owner,'retry writes and reads the current tab head');
 const draft=JSON.parse(h.storage.getItem(prefix+':draft:'+owner)),reason=JSON.parse(h.storage.getItem(prefix+':retirement-form:'+owner));assert.equal(draft?.owner,owner);assert.equal(draft?.scope.observation_id,observation);assert.equal(draft?.form.reason,h.state.saved.reason);assert.equal(reason?.owner,owner);assert.equal(reason?.scope.actor_id,account.user_id);assert.equal(reason?.scope.observation_id,observation);assert.equal(reason?.reason,'');
}

test('a rejected original PUT is retained; only cold retired status unlocks its own draft for an explicit fresh save',()=>harness(async h=>{
 const reason='原PUT被拒也不能丢掉这份人工理由';await rejectedPUT(h,reason);const original=copy(h.state.original),gate=h.gate();h.state.statusGate=gate;
 await beginRetirement(h);await h.wait(()=>h.state.statusEntered,'a retired POST response must be checked by a new status GET');assert.equal(h.input('判断理由').value,reason);assert.equal(h.input('判断理由').disabled,true,'a POST response alone is not the retirement cold read');assert.equal(originalWrites(h).length,1);
 gate.resolve();await h.wait(()=>h.input('判断理由')?.disabled===false,'cold retired status permits editing');assert.equal(h.input('判断理由').value,reason);assert.ok(containsString(h.storage,original.raw),'retirement archives the immutable original instead of clearing it');assert.ok(containsString(h.storage,h.state.retireAttempts[0].raw),'the exact retirement operation stays recoverable');
 const revised='明确停止原请求后另存这份修正理由';await h.edit('判断理由',revised);await h.click('保存判断草稿');await h.wait(()=>h.state.saved.reason===revised&&h.state.commands.size===1,'a human explicit save uses the current server draft');assert.equal(originalWrites(h).length,1,'the rejected PUT is never repeated');const fresh=h.calls.filter(row=>row.method==='PUT').at(-1);assert.notEqual(JSON.parse(fresh.raw).request_id,JSON.parse(original.raw).request_id);assert.equal(h.state.retireAttempts.length,1);
}));

test('an unknown retirement survives remount and is explicitly retried with its exact original nonce and body after GET lookup',()=>harness(async h=>{
 const reason='停止接收回应丢失仍保留我的说明';await rejectedPUT(h,reason);h.state.loseRetireBeforeCommit=true;await beginRetirement(h);await h.wait(()=>h.state.retireAttempts.length===1,'the first exact retirement POST entered');const sent=copy(h.state.retireAttempts[0]);assert.equal(h.state.retirement,null);assert.equal(originalWrites(h).length,1);
 await h.unmount();const before=h.calls.length;await h.mount();await h.click('纠正提示判断');await h.wait(()=>!!h.button('核对停止接收结果')&&!h.button('核对停止接收结果').disabled,'the retained retirement has a GET-only recovery entry');assert.ok(h.calls.slice(before).every(row=>row.method==='GET'),'mount and open cannot automatically repeat either write');
 const beforeProbe=h.calls.length;await h.click('核对停止接收结果');await h.wait(()=>h.calls.slice(beforeProbe).some(row=>row.method==='GET'&&row.path.endsWith('/status')),'status is looked up before an explicit retirement retry');assert.equal(h.state.retireAttempts.length,1);assert.equal(h.input('判断理由').value,reason);assert.equal(h.input('判断理由').disabled,true);
 await h.click('按原编号重试停止接收');await h.wait(()=>!!h.state.retirement&&h.input('判断理由')?.disabled===false,'the same retirement eventually has a cold confirmed fence');assert.equal(h.state.retireAttempts.length,2);assert.equal(h.state.retireAttempts[1].raw,sent.raw);assert.equal(h.state.retireAttempts[1].body.request_id,sent.body.request_id);assert.equal(new Set(h.state.retireAttempts.map(row=>row.body.request_id)).size,1);assert.equal(originalWrites(h).length,1);assert.equal(h.state.commands.size,0,'retirement is not another feedback save');assert.equal(h.input('判断理由').value,reason);assert.ok(containsString(h.storage,sent.raw));
}));

test('unavailable machine basis still opens independently checked human history without classification, machine numbers or fresh authority',()=>harness(async h=>{
 const reason='已经正式提交的人工作业必须仍能读回';h.state.saved=savedView('should_not_alert',reason,'submitted',2);h.state.basisAvailable=false;await h.mount();assert.equal(h.calls.length,0);await h.click('纠正提示判断');await h.wait(()=>h.calls.some(row=>row.path.endsWith('/feedback-history'))&&h.container.textContent.includes(reason),'unsupported machine context uses the dedicated history lookup');
 assert.ok(h.container.textContent.includes('历史')&&h.container.textContent.includes('不可用于策略'));assert.ok(!h.container.textContent.includes(basis.field_label));assert.ok(!h.container.textContent.includes(String(basis.actual_value)));assert.ok(!h.container.textContent.includes(String(basis.threshold)));assert.ok(!/\b(?:TP|TN|FP|FN)\b|classification/.test(h.container.textContent));assert.ok(!h.container.textContent.includes(feedback),'internal feedback IDs are not the user flow');
 for(const name of ['保存判断草稿','提交判断','新建判断修订','取消判断草稿'])assert.ok(!h.button(name)||h.button(name).disabled,name+' cannot obtain authority from history');for(const node of h.container.querySelectorAll('input,textarea,select'))assert.equal(node.disabled,true,'history-only must not offer a writable judgment editor');assert.ok(![...h.container.querySelectorAll('button')].some(node=>!node.disabled&&/候选|评估|审批|启用/.test(node.textContent)));assert.ok(h.calls.every(row=>row.method==='GET'),'reading historical human reasons never creates a command or model call');
}));

test('first-open draft quota failure can be explicitly persisted after storage recovers without closing away protected work',()=>harness(async h=>{
 h.state.failStorage='draft';await h.mount();await h.click('纠正提示判断');await h.wait(()=>!!h.button('重试保存本机稿')&&!h.button('收起判断').disabled,'initial local draft failure has finished without sending');assert.equal(h.input('判断理由')?.disabled,true);assert.ok(h.calls.every(row=>row.method==='GET'));
 const warning=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(warning);assert.equal(warning.defaultPrevented,true,'the unsaved initial draft is protected');h.state.failStorage=null;
 await h.click('重试保存本机稿');await h.wait(()=>h.input('判断理由')?.disabled===false,'retry completes the full same-owner draft initialization');checkedLocalInitialization(h);await h.edit('判断理由','首次配额恢复后保留的独立人稿');await h.click('收起判断');await h.click('纠正提示判断');assert.equal(h.input('判断理由').value,'首次配额恢复后保留的独立人稿');assert.ok(h.calls.every(row=>row.method==='GET'));
}));

test('first-open retirement-form quota failure has a usable full persistence retry instead of an unsaved close dead end',()=>harness(async h=>{
 h.state.failStorage='retirement-form';await h.mount();await h.click('纠正提示判断');await h.wait(()=>!!h.button('重试保存本机稿')&&!h.button('收起判断').disabled,'initial retirement-form write failure is visible');assert.equal(h.input('判断理由')?.disabled,true);const warning=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(warning);assert.equal(warning.defaultPrevented,true);assert.ok(h.calls.every(row=>row.method==='GET'));
 h.state.failStorage=null;await h.click('重试保存本机稿');await h.wait(()=>h.input('判断理由')?.disabled===false,'both draft and retirement form are read back before editing resumes');checkedLocalInitialization(h);
 await h.edit('判断理由','停止说明初始保存失败后的人工理由');await h.click('收起判断');await h.click('纠正提示判断');assert.equal(h.input('判断理由').value,'停止说明初始保存失败后的人工理由');assert.ok(h.calls.every(row=>row.method==='GET'));
}));

test('a rule GET conflict can discover server human history without guessing rule ID, while forbidden reads never fall back',()=>harness(async h=>{
 const reason='规则资料暂坏仍可只读核对这份已提交理由';h.state.saved=savedView('should_not_alert',reason,'submitted',2);h.state.basisAvailable=false;h.state.ruleFailure=409;await h.mount();assert.equal(h.calls.length,0);await h.click('纠正提示判断');await h.wait(()=>h.calls.some(row=>row.path.endsWith('/feedback-history'))&&h.container.textContent.includes(reason),'a rule 409 does not suppress the independently authorized history lookup');
 const lookup=h.calls.find(row=>row.path.endsWith('/feedback-history'));assert.equal(new URLSearchParams(lookup.search).has('rule_id'),false,'the broken rule is not replaced by a client-guessed rule');assert.ok(h.calls.slice(0,h.calls.indexOf(lookup)).some(row=>row.path==='/api/me'),'identity is checked before history discovery');assert.ok(!h.container.textContent.includes(String(basis.actual_value)));assert.ok(!/\b(?:TP|TN|FP|FN)\b|classification/.test(h.container.textContent));for(const label of ['保存判断草稿','提交判断','新建判断修订','取消判断草稿'])assert.ok(!h.button(label)||h.button(label).disabled);assert.ok(h.calls.every(row=>row.method==='GET'));
 await h.click('收起判断');h.state.ruleFailure=403;const previous=h.calls.length;await h.click('纠正提示判断');await h.wait(()=>h.container.textContent.includes('当前没有读取规则的权限'),'an actual permission denial is retained');assert.ok(!h.calls.slice(previous).some(row=>row.path.endsWith('/feedback-history')||row.path.endsWith('/history')),'403 cannot be converted into a weaker reader');assert.ok(!h.container.textContent.includes(reason),'a new denied operation must not re-display the previous historical reason');assert.ok(h.calls.every(row=>row.method==='GET'));
}));

test('an existing nonempty retirement reason survives a remount draft quota failure and its same-owner persistence retry',()=>harness(async h=>{
 const reason='原停止接收说明必须跨首次草稿配额失败保留';await rejectedPUT(h,'原判断理由与停止说明分别保存');await h.click(retireButton);await h.edit('停止接收理由',reason);
 const prefix='ga-threshold-feedback-v147:'+Object.values({actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:instance,observation_id:observation}).join(':'),firstOwner=h.session.getItem(prefix+':head');assert.equal(JSON.parse(h.storage.getItem(prefix+':retirement-form:'+firstOwner)).reason,reason);const original=copy(h.state.original);
 await h.unmount();h.state.failStorage='draft';await h.mount();await h.click('纠正提示判断');await h.wait(()=>!!h.button('重试保存本机稿')&&!h.button('重试保存本机稿').disabled,'the remounted draft quota fault has a recovery action');h.state.failStorage=null;await h.click('重试保存本机稿');await h.click('核对原判断请求');await h.click(retireButton);
 assert.equal(h.input('停止接收理由').value,reason,'the prior checked reason must already be protected in memory before the new draft write can fail');const secondOwner=h.session.getItem(prefix+':head');assert.notEqual(secondOwner,firstOwner);assert.equal(JSON.parse(h.storage.getItem(prefix+':retirement-form:'+secondOwner)).reason,reason,'retry must not migrate the head to an empty reason');assert.equal(JSON.parse(h.storage.getItem(prefix+':retirement-form:'+firstOwner)).reason,reason);
 await h.unmount();await h.mount();await h.click('纠正提示判断');await h.click('核对原判断请求');await h.click(retireButton);assert.equal(h.input('停止接收理由').value,reason);assert.equal(h.state.retireAttempts.length,0,'saving a local reason does not issue a retirement');assert.equal(h.calls.filter(row=>row.method==='PUT'&&row.raw===original.raw).length,1,'a local recovery never repeats the original PUT');
}));
