// Actual component, Journal and runtime-api with synthetic closed HTTP seams.
// No DB/report/model/source authority, layout or adoption is proved here.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash,webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){if(!bundle)bundle=(async()=>{
 temporary=await mkdtemp(join(tmpdir(),'report-object-revision-v166-'));const entry=join(temporary,'entry.mjs');
 await writeFile(entry,'export {default as Revision} from '+JSON.stringify(join(project,'src/ReportObjectRevisionV166.jsx'))+';\nexport * from '+JSON.stringify(join(project,'src/report-object-revision-v166.mjs'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
 const modules={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(modules),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(modules,key)?pathToFileURL(join(project,'node_modules/react',modules[key])).href:key}}}});
 const chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);const path=join(temporary,'component.mjs');await writeFile(path,chunk.code);return import(pathToFileURL(path).href);
})();return bundle;}
const id=n=>'16610000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value)),stable=value=>JSON.stringify((function visit(item){return Array.isArray(item)?item.map(visit):item&&typeof item==='object'?Object.fromEntries(Object.keys(item).sort().map(key=>[key,visit(item[key])])):item;})(value)),sha=raw=>createHash('sha256').update(raw).digest('hex'),hash=value=>sha(stable(value));
const account={user_id:id(1),workspace_id:id(2),username:'合成结构核查人',role:'operator'},report=id(3),csrf='synthetic-object-revision-csrf',base='/api/workspace/report-evidence-readings/'+report+'/object-revisions-v166',savedAt='2026-10-22T01:02:03.123456+00:00',citation={span_ref:'w:p1/s1'};
function value(){return {schema:'report-object-semantic-review-v166',result:{schema:'report-object-semantic-candidate-wire-v166',source_hash:'a'.repeat(64),catalog_hash:'b'.repeat(64),domain:'合成业务领域',global_claims:[],objects:[{name:'档案条目',fields:[{label:'名称',kind:'文本',required:true,unit:null,citations:[citation],inference_reason:''},{label:'数量',kind:'整数',required:false,unit:'株',citations:[citation],inference_reason:''}],citations:[citation],inference_reason:'',business_identity:{status:'not_established',field_labels:[],citations:[],explanation:'当前人工稿尚未明确业务标识，继续保留待核'}}],relationships:[],businesses:[],questions:['业务标识仍待核，不据技术记录身份认定业务编号']},note:'',object_identity_confirmations:[{object_name:'档案条目',acknowledge_platform_identity:false}]};}
function basis(){return {report_id:report,report_revision:3,model_result_hash:'c'.repeat(64),source_hash:'a'.repeat(64),catalog_hash:'b'.repeat(64),context_hash:'d'.repeat(64),base_value_hash:'e'.repeat(64)};}
function fixture(){return {schema:'report-object-revision-view-v166',actor_id:account.user_id,workspace_id:account.workspace_id,report_id:report,version:0,status:'empty',basis:basis(),basis_hash:hash(basis()),value:value(),draft_history:[],can_save:true,can_confirm:true,source_available:true,source_issue:null};}
function applySaved(state,body,operation){assert.equal(body.expected_version,state.detail.version);if(operation==='confirm')assert.ok(body.value.object_identity_confirmations.every(item=>item.acknowledge_platform_identity===true));const version=state.detail.version+1;state.detail={...state.detail,version,status:operation==='confirm'?'confirmed':'draft',value:copy(body.value),draft_history:[...state.detail.draft_history,{version,operation,value:copy(body.value),saved_at:savedAt}]};return {schema:'report-object-revision-request-v166',request_id:body.request_id,wire_sha256:'',outcome:'saved',result:{report_id:report,version,operation,status:state.detail.status,basis_hash:body.basis_hash,value_hash:hash(body.value)}};}
function locks(){const tails=new Map();return {request(key,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(key)||Promise.resolve()).then(()=>callback({name:key,mode:'exclusive'})),tail=next.then(()=>{},()=>{});tails.set(key,tail);return next.finally(()=>{if(tails.get(key)===tail)tails.delete(key);});}};}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resource fetch');}}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}

async function harness(runTest,{saved=false,sourceBad=false,currentAvailable=true}={}){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://report-object-revision.invalid/',pretendToBeVisual:true,resources:new NoResources()}),storage=dom.window.localStorage,session=dom.window.sessionStorage,calls=[],violations=[],errors=[];
 const state={account:{...account},propsAccount:{...account},currentAvailable,detail:fixture(),commands:new Map(),postMode:'normal',failDraft:false,meGate:null,meEntered:false};let root,actual;
 if(saved){const old=value();old.note='服务器原有的新结构私人稿';old.result.objects[0].fields[0].required=false;applySaved(state,{request_id:id(10),expected_version:0,basis_hash:state.detail.basis_hash,value:old},'save');}
 if(sourceBad)Object.assign(state.detail,{source_available:false,source_issue:'原报告版本或原来源已变化，原人稿与历史保留',can_save:false,can_confirm:false});
 const storageProto=Object.getPrototypeOf(storage),setItem=storageProto.setItem;storageProto.setItem=function(key,raw){if(this===storage&&state.failDraft&&String(key).startsWith('ga-report-object-revision-v166:')&&String(key).includes(':draft:'))throw Error('synthetic object draft quota');return setItem.call(this,key,raw);};Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const response=(body,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname,search:url.search,raw:options.body});try{
  assert.equal(url.origin,dom.window.location.origin);assert.equal(options.credentials,'same-origin');const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),state.account.workspace_id);
  if(method==='GET'&&url.pathname==='/api/me'){if(state.meGate){const gate=state.meGate;state.meGate=null;state.meEntered=true;await gate;}return response(state.account);}
  if(method==='GET'&&url.pathname===base)return response(copy(state.detail));
  if(method==='GET'&&url.pathname.startsWith(base+'/requests/')){const requestID=url.pathname.split('/').at(-1),expected=url.searchParams.get('expected_sha256'),prior=state.commands.get(requestID);assert.match(expected,/^[0-9a-f]{64}$/);if(prior){assert.equal(prior.hash,expected);return response(copy(prior.reply));}return response({schema:'report-object-revision-request-v166',request_id:requestID,wire_sha256:expected,outcome:'unknown',result:null});}
  if(method==='POST'&&[base+'/save',base+'/confirm'].includes(url.pathname)){
   assert.equal(headers.get('X-CSRF-Token'),csrf);const body=JSON.parse(options.body),operation=url.pathname.split('/').at(-1);assert.deepEqual(Object.keys(body).sort(),['request_id','expected_version','basis_hash','value'].sort());assert.equal(body.basis_hash,state.detail.basis_hash);
   const active=Array.from({length:storage.length},(_,index)=>storage.key(index)).filter(key=>key.endsWith(':active')).map(key=>JSON.parse(storage.getItem(key))).find(item=>item.request_id===body.request_id);assert.ok(active,'actual Journal persists before HTTP');const immutable=JSON.parse(storage.getItem(active.key));assert.equal(immutable.exact_body,options.body);assert.equal(immutable.path,url.pathname);assert.equal(immutable.wire_sha256,sha(options.body));assert.equal(immutable.scope.actor_id,state.account.user_id);assert.equal(immutable.scope.report_id,report);
   const prior=state.commands.get(body.request_id);if(prior){assert.equal(prior.raw,options.body);assert.equal(prior.path,url.pathname);return response(copy(prior.reply));}
   if(state.postMode==='unknown'){state.postMode='normal';throw Error('synthetic unknown request before save');}
   if(state.postMode==='conflict'){state.postMode='normal';const other=value();other.note='同一核查人在另一窗口保存的版本';applySaved(state,{request_id:id(50),expected_version:state.detail.version,basis_hash:state.detail.basis_hash,value:other},'save');return response({detail:'另一个窗口已保存新版本'},409);}
   if(body.expected_version!==state.detail.version)return response({detail:'原保存版本已经失效'},409);
   const reply=applySaved(state,body,operation);reply.wire_sha256=sha(options.body);state.commands.set(body.request_id,{hash:reply.wire_sha256,raw:options.body,path:url.pathname,reply:copy(reply)});if(state.postMode==='lost'){state.postMode='normal';throw Error('synthetic saved response lost');}return response(reply);
  }
  throw Error('Unexpected object revision HTTP '+method+' '+url.pathname);
 }catch(cause){if(!cause.message.startsWith('synthetic '))violations.push(cause.stack);throw cause;}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,next]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value:next});dom.window.fetch=fetch;
 try{
  // Do not initialise ReactDOM's input support before the JSDOM document exists.
  const {createRoot}=await import('react-dom/client');actual=await component();assert.equal(actual.BundledReact,React);actual.setSession(csrf,account.workspace_id,account.user_id,account.username);
  const container=document.getElementById('root'),settle=async()=>{for(let n=0;n<6;n++)await act(async()=>{await new Promise(done=>setImmediate(done));await new Promise(done=>setTimeout(done,0));});},wait=async(predicate,label)=>{for(let n=0;n<60;n++){if(predicate())return;await settle();}assert.ok(predicate(),label+'; '+container.textContent.slice(0,1000));};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label),field=label=>container.querySelector('[aria-label="'+label+'"]');
  const click=async label=>{const node=button(label);assert.ok(node,'actual '+label);assert.equal(node.disabled,false,label+' is enabled');await act(async()=>node.click());await settle();};
  const edit=async(label,next)=>{const node=field(label);assert.ok(node);assert.equal(node.disabled,false);assert.ok(!node.readOnly);await act(async()=>{const prototype=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLSelectElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,next);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  const check=async label=>{const node=field(label);assert.ok(node,'actual checkbox '+label);assert.equal(node.type,'checkbox');assert.equal(node.disabled,false);const details=node.closest('details');if(details)assert.equal(details.open,true,'open the actual source choices before selecting them');await act(async()=>node.click());await settle();};
  const toggle=async label=>{const node=[...container.querySelectorAll('summary')].find(item=>item.textContent.trim()===label);assert.ok(node,'actual '+label);await act(async()=>node.click());await settle();};
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Revision,{account:state.propsAccount,reportId:report,Btn:Button,currentAvailable:state.currentAvailable}))));await settle();},mount=async()=>{root=createRoot(container,{onUncaughtError:cause=>errors.push(cause.message),onCaughtError:cause=>errors.push(cause.message)});await render();},remount=async()=>{await act(async()=>root.unmount());root=null;await mount();};
  const open=async()=>{await click(button('打开独立对象修订')?'打开独立对象修订':'重新打开新结构稿');await wait(()=>field('新结构稿私人说明')&&!button('读取服务器新结构版本')?.disabled,'current structure detail loaded');};
  const active=()=>{const key=Array.from({length:storage.length},(_,index)=>storage.key(index)).find(key=>key.endsWith(':active'));if(!key)return null;const head=storage.getItem(key);return {key,head,record:JSON.parse(storage.getItem(JSON.parse(head).key))};};
  const unloading=()=>{const event=new dom.window.Event('beforeunload',{cancelable:true});dom.window.dispatchEvent(event);return event.defaultPrevented;};
  await mount();assert.equal(calls.length,0,'closed object revision makes no HTTP');await runTest({actual,state,container,calls,storage,session,button,field,click,edit,check,toggle,render,remount,open,wait,settle,active,unloading});assert.ok(calls.every(call=>call.method==='GET'||call.method==='POST'&&[base+'/save',base+'/confirm'].includes(call.path)),'the actual component never writes old report/model/configuration endpoints');assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{
  state.failDraft=false;try{if(root)await act(async()=>root.unmount());}finally{state.releaseMe?.();try{for(let n=0;n<3;n++)await act(async()=>new Promise(done=>setImmediate(done)));if(actual)for(let n=0;n<6;n++){const clean=new actual.ObjectRevisionJournalV166(actual.objectRevisionScopeV166(account,report,hash(basis())),{storage,session,locks:dom.window.navigator.locks});try{clean.open(value());if(!clean.unsaved)break;clean.save(clean.value);}finally{clean.dispose();}}}finally{storageProto.setItem=setItem;dom.window.close();for(const[key,next]of Object.entries(prior)){if(next)Object.defineProperty(globalThis,key,next);else delete globalThis[key];}}}
 }
}

test('V166 actual all-optional draft persists and only explicit per-object acknowledgement enables independent confirmation',async()=>{
 await harness(async({state,calls,container,field,button,click,edit,check,open,remount,active,wait})=>{
  await open();assert.equal(field('档案条目技术身份确认').checked,false);assert.equal(field('档案条目 · 名称必填').checked,true);await check('档案条目 · 名称必填');await edit('新结构稿私人说明','明确保留业务标识尚未明确的新结构稿');assert.equal(button('明确确认新结构版本').disabled,true);await click('另存新结构草稿');await wait(()=>state.detail.version===1&&active()===null,'new independent draft saved');assert.equal(state.detail.value.result.objects[0].fields.every(item=>!item.required),true);assert.equal(state.detail.value.result.objects[0].business_identity.status,'not_established');await remount();await open();assert.equal(field('档案条目 · 名称必填').checked,false);assert.equal(field('档案条目技术身份确认').checked,false);assert.equal(field('新结构稿私人说明').value,'明确保留业务标识尚未明确的新结构稿');await check('档案条目技术身份确认');await click('明确确认新结构版本');await wait(()=>state.detail.status==='confirmed'&&active()===null,'explicit new confirmation saved');assert.equal(state.detail.version,2);assert.equal(calls.filter(item=>item.method==='POST').length,2);assert.ok(container.textContent.includes('尚未采用到配置'));assert.ok(!container.querySelector('input[placeholder*="UUID"]'));assert.ok(container.textContent.includes('业务标识尚未明确'));
 });
});
test('V166 referenced business identity uses existing fields and original position labels without changing required or unique constraints',async()=>{
 await harness(async({state,calls,field,button,edit,check,toggle,click,open,wait,active,container})=>{
  await open();await edit('档案条目业务标识现状','referenced');assert.equal(button('另存新结构草稿').disabled,true);await check('档案条目标识字段 · 名称');await toggle('身份说明的原文依据 · 0处');await check('档案条目身份依据 · Word正文第1处 · 片段1');await edit('档案条目业务标识说明','只按原片段说明已有名称字段，仍不授予唯一性');assert.equal(field('档案条目 · 名称必填').checked,true);assert.equal(field('档案条目技术身份确认').checked,false);await click('另存新结构草稿');await wait(()=>state.detail.version===1&&active()===null,'identity reference saved');const identity=state.detail.value.result.objects[0].business_identity;assert.deepEqual(identity.field_labels,['名称']);assert.deepEqual(identity.citations,[citation]);assert.ok(container.textContent.includes('不会因此获得唯一约束'));assert.ok(!Object.hasOwn(state.detail.value.result.objects[0],'unique'));assert.equal(calls.filter(item=>item.method==='POST').length,1);
 });
});
test('V166 lost saved response remount recovers by original SHA GET with no repeated save',async()=>{
 await harness(async({state,calls,field,edit,click,open,remount,active,wait})=>{
  await open();await edit('新结构稿私人说明','保存成功但回复丢失的人稿');state.postMode='lost';await click('另存新结构草稿');await wait(()=>active()&&state.detail.version===1,'saved response lost');const original=active().record,before=calls.length;await remount();await open();await wait(()=>active()===null,'GET original settles saved request');assert.ok(calls.slice(before).every(item=>item.method==='GET'));assert.equal(calls.filter(item=>item.method==='POST').length,1);assert.equal(field('新结构稿私人说明').value,'保存成功但回复丢失的人稿');assert.ok(calls.slice(before).some(item=>item.search==='?expected_sha256='+original.wire_sha256));
 });
});
test('V166 unknown original remains immutable while later edits are retained through exact-wire explicit retry',async()=>{
 await harness(async({state,calls,field,edit,check,click,open,active,wait,remount})=>{
  await open();await edit('新结构稿私人说明','原保存时的人稿');state.postMode='unknown';await click('另存新结构草稿');await wait(()=>active()!==null,'unknown request remains');const original=active().record;await check('档案条目 · 名称必填');await edit('新结构稿私人说明','后来仍须保留的新修改');assert.equal(active().record.exact_body,original.exact_body);const before=calls.length;await remount();await open();assert.ok(calls.slice(before).every(item=>item.method==='GET'));assert.equal(field('新结构稿私人说明').value,'后来仍须保留的新修改');await click('按原文重试结构稿请求');await wait(()=>state.detail.version===1&&active()===null,'explicit original retry saved');assert.equal(state.detail.value.note,'原保存时的人稿');assert.equal(field('新结构稿私人说明').value,'后来仍须保留的新修改');assert.equal(field('档案条目 · 名称必填').checked,false);const posts=calls.filter(item=>item.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].raw,posts[1].raw);
 });
});
test('V166 CAS conflict requires same-basis fresh own version and second original lookup before explicit new save',async()=>{
 await harness(async({state,calls,field,edit,click,open,active,wait})=>{
  await open();await edit('新结构稿私人说明','冲突时不能丢失的本窗口说明');state.postMode='conflict';await click('另存新结构草稿');await wait(()=>active()&&state.detail.version===1,'CAS conflict preserves old request');const original=active().record;assert.equal(field('新结构稿私人说明').value,'冲突时不能丢失的本窗口说明');const before=calls.length;await click('按服务器新版本继续编辑');await wait(()=>active()===null,'explicit second lookup settles old CAS');const reads=calls.slice(before);assert.ok(reads.every(item=>item.method==='GET'));assert.equal(reads.filter(item=>item.path.startsWith(base+'/requests/')).length,1);assert.ok(reads.some(item=>item.path===base));assert.equal(field('新结构稿私人说明').value,'冲突时不能丢失的本窗口说明');assert.equal(calls.filter(item=>item.method==='POST').length,1);await click('另存新结构草稿');await wait(()=>state.detail.version===2&&active()===null,'explicit later save uses new version');const body=JSON.parse(calls.filter(item=>item.method==='POST').at(-1).raw);assert.equal(body.expected_version,1);assert.notEqual(body.request_id,original.request_id);assert.equal(body.value.note,'冲突时不能丢失的本窗口说明');
 });
});
test('V166 latest current-source gate after identity await blocks late new POST without discarding local work',async()=>{
 await harness(async({state,calls,field,edit,click,open,render,wait,settle,storage})=>{
  await open();await edit('新结构稿私人说明','来源状态变化仍须保留的说明');state.meGate=new Promise(resolve=>{state.releaseMe=resolve;});await click('另存新结构草稿');await wait(()=>state.meEntered,'identity is awaiting');state.currentAvailable=false;await render();state.releaseMe();await settle();assert.equal(calls.filter(item=>item.method==='POST').length,0);assert.equal(field('新结构稿私人说明').value,'来源状态变化仍须保留的说明');assert.equal(field('新结构稿私人说明').readOnly,true);assert.ok(Array.from({length:storage.length},(_,i)=>storage.getItem(storage.key(i))).some(raw=>raw?.includes('来源状态变化仍须保留的说明')));
 });
});
test('V166 fresh identity mismatch hides private structure and leaves the durable local draft untouched',async()=>{
 await harness(async({state,calls,field,edit,click,open,wait,storage})=>{
  await open();await edit('新结构稿私人说明','不能泄露给另一个登录身份的说明');state.account={...state.account,role:'viewer'};await click('另存新结构草稿');await wait(()=>field('新结构稿私人说明')===null,'identity mismatch masks private structure');assert.equal(calls.filter(item=>item.method==='POST').length,0);assert.ok(Array.from({length:storage.length},(_,i)=>storage.getItem(storage.key(i))).some(raw=>raw?.includes('不能泄露给另一个登录身份的说明')));
 });
});
test('V166 source unavailable preserves saved human structures/history read-only without inventing a fresh base',async()=>{
 await harness(async({state,calls,container,field,button,open})=>{
  await open();assert.equal(field('新结构稿私人说明').value,'服务器原有的新结构私人稿');assert.equal(field('新结构稿私人说明').readOnly,true);assert.equal(field('档案条目 · 名称必填').checked,false);assert.equal(field('档案条目 · 名称必填').disabled,true);assert.equal(button('另存新结构草稿').disabled,true);assert.equal(button('明确确认新结构版本').disabled,true);assert.ok(container.textContent.includes('原人稿与历史保留'));assert.ok(container.textContent.includes('中国标准时间'));assert.equal(state.detail.version,1);assert.equal(calls.filter(item=>item.method==='POST').length,0);
 },{saved:true,sourceBad:true,currentAvailable:false});
});
test('V166 quota remount retains unfinished input and requires actual retry-readback before server save',async()=>{
 await harness(async({state,calls,field,button,edit,click,open,remount,unloading,wait,active})=>{
  await open();state.failDraft=true;await edit('新结构稿私人说明','额度不足时未落盘的人工作业');assert.equal(field('新结构稿私人说明').value,'额度不足时未落盘的人工作业');assert.equal(unloading(),true);assert.equal(button('另存新结构草稿').disabled,true);await click('返回原报告，保留此稿');assert.equal(field('新结构稿私人说明').value,'额度不足时未落盘的人工作业');assert.equal(calls.filter(item=>item.method==='POST').length,0);await remount();await open();assert.equal(field('新结构稿私人说明').value,'额度不足时未落盘的人工作业');state.failDraft=false;await click('重试本机暂存');await wait(()=>!unloading(),'explicit actual Journal write-read clears warning');await click('另存新结构草稿');await wait(()=>state.detail.version===1&&active()===null,'real component uses server save transport');assert.equal(state.detail.value.note,'额度不足时未落盘的人工作业');assert.equal(calls.filter(item=>item.method==='POST').length,1);
 });
});
test('V166 server can_* permissions independently block original retry and edits while original GET remains available',async()=>{
 await harness(async({state,calls,field,button,edit,click,open,wait,active})=>{
  await open();await edit('新结构稿私人说明','原请求待核时的私人稿');state.postMode='unknown';await click('另存新结构草稿');await wait(()=>active()!==null,'original unknown retained');state.detail.can_save=false;state.detail.can_confirm=false;state.detail.source_issue='当前来源写资格已撤回';await click('读取服务器新结构版本');await click('核对原结构稿请求');assert.equal(button('按原文重试结构稿请求').disabled,true);assert.equal(field('新结构稿私人说明').readOnly,true);assert.equal(field('档案条目业务标识现状').disabled,true);assert.equal(field('新结构稿私人说明').value,'原请求待核时的私人稿');assert.equal(calls.filter(item=>item.method==='POST').length,1);
 });
});
test('V166 unfinished identity explanation survives remount and never installs an invalid original request',async()=>{
 await harness(async({state,calls,field,button,edit,click,open,remount,wait,active})=>{
  await open();await edit('档案条目业务标识说明','');await edit('新结构稿私人说明','尚未写完的说明仍须保留');assert.equal(button('另存新结构草稿').disabled,true);assert.equal(active(),null);await remount();await open();assert.equal(field('档案条目业务标识说明').value,'');assert.equal(field('新结构稿私人说明').value,'尚未写完的说明仍须保留');assert.equal(calls.filter(item=>item.method==='POST').length,0);await edit('档案条目业务标识说明','已补充：业务标识仍待核，不将技术身份当业务编号');await click('另存新结构草稿');await wait(()=>state.detail.version===1&&active()===null,'only the complete explicit draft creates a request');assert.equal(state.detail.value.note,'尚未写完的说明仍须保留');
 });
});
test('V166 three failed quota generations recover only the latest human work without ghost warnings after final readback',async()=>{
 await harness(async({state,calls,field,edit,click,open,remount,unloading,storage,session})=>{
  await open();state.failDraft=true;const notes=['R1：第一代未落盘的人稿','R2：第二代继续修改的人稿','R3：第三代最终应保存的人稿'];
  for(const note of notes){await edit('新结构稿私人说明',note);assert.equal(field('新结构稿私人说明').value,note);assert.equal(unloading(),true);await remount();await open();assert.equal(field('新结构稿私人说明').value,note,'actual remount rescues the latest unsaved work');}
  state.failDraft=false;await click('重试本机暂存');const headKey=Array.from({length:session.length},(_,index)=>session.key(index)).find(key=>key.startsWith('ga-report-object-revision-v166:')&&key.endsWith(':head'));assert.ok(headKey);const pointer=JSON.parse(session.getItem(headKey));assert.equal(JSON.parse(storage.getItem(pointer.key)).value.note,notes.at(-1),'the final local write-read really succeeded');
  await remount();await open();assert.equal(field('新结构稿私人说明').value,notes.at(-1),'an old failed ancestor must not replace the saved latest work');assert.equal(unloading(),false,'no discarded rescue ancestor may keep a ghost unload warning');assert.equal(calls.filter(item=>item.method==='POST').length,0,'all quota rescue work is local until a later explicit server save');
 });
});
