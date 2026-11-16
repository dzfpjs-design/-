// Actual Policy component and Journals; HTTP is a closed synthetic read fixture.
// This verifies display wording, not PG recovery, execution or browser layout.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {JSDOM,ResourceLoader} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'threshold-policy-label-v157-'));
  const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as Policy} from '+JSON.stringify(join(project,'src/ThresholdPolicyV153.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
  const modules={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(modules),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(modules,key)?pathToFileURL(join(project,'node_modules/react',modules[key])).href:key}}}});
  const chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);
  const file=join(temporary,'component.mjs');await writeFile(file,chunk.code);return import(pathToFileURL(file).href);
 })();
 return bundle;
}

const id=n=>'15710000-0000-4000-8000-'+String(n).padStart(12,'0');
const account={user_id:id(1),workspace_id:id(2),role:'admin',username:'synthetic-label-admin'};
const review={id:id(3),workspace_id:account.workspace_id,version:2},policyID=id(4),rootPath='/api/local-rules/evolution';
const baseline={id:id(10),title:'批准基线规则',field:'count',field_label:'数量',operator:'gt',threshold:20,unit:'株'};
const candidate={...baseline,id:id(11),title:'有限候选规则',threshold:18};
const scope={workspace_id:account.workspace_id,instance_id:id(12),release_id:id(13),entity_id:id(14),source_id:id(15),baseline_id:baseline.id,baseline_hash:'a'.repeat(64),candidate_capability_id:candidate.id,candidate_hash:'b'.repeat(64),source_hash:'c'.repeat(64),rule_id:'count-warning',field:'count',field_label:'数量',unit:'株',operator:'gt',baseline_threshold:20,candidate_threshold:18,instance_name:'合成状态说明实例',release_name:'原批准发布',entity_name:'原登记对象',source_name:'人工数值登记来源',baseline_name:baseline.title,candidate_name:candidate.title};
const spec={field:'count',field_label:'数量',unit:'株',value_kind:'number',source_timezone:'Asia/Shanghai',clock_unknown_allowed:true};
function grant(invocationStatus){
 const failed=invocationStatus==='unknown';
 const row={schema:'threshold-policy-selection-view-v153',workspace_id:account.workspace_id,id:id(5),version:failed?3:2,policy_id:policyID,choice:'candidate',status:failed?'failed':'reserved',source_record_id:'本次原来源记录',processing_version:'v1',observed_at:null,business_day:'2026-10-21',entity_name:scope.entity_name,source_name:scope.source_name,field:'count',field_label:'数量',unit:'株',value:19,extractor:candidate,baseline,invocation_status:invocationStatus,actual_matched:null,baseline_projection_matched:null,late:false,issue:null,observation_status:failed?'failed':'received',job_status:failed?'failed':'running',receipt_status:null,business_declaration:null,feedback:null,can_feedback:false};
 return {schema:'threshold-policy-view-v153',workspace_id:account.workspace_id,id:policyID,version:1,actor_id:account.user_id,review_id:review.id,status:'active',title:'合成有限实际采用',reason:'原明确采用理由',purpose:'mechanism_check',scope,baseline,candidate,max_candidate_inputs:2,used_candidate_inputs:1,expires_at:'2026-10-27T12:00:00+08:00',epoch:1,choice:'candidate',current_issue:null,can_input:true,can_pause:true,can_resume:false,can_rollback:true,production_eligible:false,selections:[row],history:[{version:1,operation:'create',reason:'原明确采用理由',actor_id:account.user_id,origin:'human',selection_id:null}],input_spec:spec,monitor:{statistics:null,issue:null,last_check_at:null,last_stop:null}};
}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).then(()=>callback({name,mode:'exclusive'})),tail=next.then(()=>{},()=>{});tails.set(name,tail);return next.finally(()=>{if(tails.get(name)===tail)tails.delete(name);});}};}

test('V157 actual Policy input cards explain unknown and running calls without claiming results or leaking unknown enums',async()=>{
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-policy-label.invalid/',pretendToBeVisual:true,resources:new NoResources()});
 const calls=[],errors=[],violations=[];let root,current=grant('unknown');
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const response=value=>new Response(JSON.stringify(value),{status:200,headers:{'Content-Type':'application/json'}});
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname});
  try{
   assert.equal(method,'GET','status display never writes');assert.equal(url.origin,dom.window.location.origin);
   assert.equal(options.credentials,'same-origin');assert.equal(new Headers(options.headers).get('X-Workspace-ID'),account.workspace_id);
   if(url.pathname==='/api/me')return response(account);
   if(url.pathname===rootPath+'/threshold-policy-options'){
    assert.equal(url.searchParams.get('review_id'),review.id);
    return response({schema:'threshold-policy-options-v153',workspace_id:account.workspace_id,review_id:review.id,review_version:review.version,purpose:'mechanism_check',scope,baseline,candidate,max_candidate_inputs_limit:30,max_duration_seconds:604800,environment:'verification',can_create:false,current_issue:'已有原策略，请明确读取'});
   }
   if(url.pathname===rootPath+'/threshold-policies'){
    assert.equal(url.searchParams.get('review_id'),review.id);
    return response({schema:'threshold-policy-list-v153',workspace_id:account.workspace_id,grants:[current],next_after:null});
   }
   if(url.pathname===rootPath+'/threshold-policies/'+policyID)return response(current);
   throw Error('Unexpected status-display HTTP '+url.pathname);
  }catch(cause){violations.push(cause.message);throw cause;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 try{
  const actual=await component();assert.equal(actual.BundledReact,React);actual.setSession('synthetic-status-csrf',account.workspace_id,account.user_id,account.username);
  const container=dom.window.document.getElementById('root');
  const settle=async()=>{for(let i=0;i<8;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label);
  const wait=async(predicate,label)=>{for(let i=0;i<80;i++){if(predicate())return;await settle();}assert.ok(predicate(),label+'; '+container.textContent.slice(0,700));};
  const click=async label=>{const node=button(label);assert.ok(!!node,'actual '+label);assert.equal(node.disabled,false,label+' is enabled');await act(async()=>node.click());await settle();};
  const card=()=>[...container.querySelectorAll('h6')].find(node=>node.textContent==='原来源记录：本次原来源记录')?.closest('article');
  root=createRoot(container,{onUncaughtError:cause=>errors.push(cause.message),onCaughtError:cause=>errors.push(cause.message)});
  await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Policy,{review,account}))));await settle();
  assert.equal(calls.length,0,'closed component is inert');
  await click('明确采用');await wait(()=>button('刷新实际策略与结果')?.disabled===false,'scope read completes');
  const summary=[...container.querySelectorAll('summary')].find(node=>node.textContent==='已保存的实际策略');assert.ok(!!summary);
  await act(async()=>summary.click());await settle();assert.equal(summary.parentElement.open,true);
  await click('查看这项实际策略');
  for(const [status,label] of [['unknown','结果未知，需核查原回执'],['running','已登记调用，结果未保存'],['backend_future_phase_987','状态待核对']]){
   if(status!=='unknown'){current=grant(status);await click('刷新实际策略与结果');}
   await wait(()=>card()?.textContent.includes('实际调用：'+label),'actual input card has human call state');
   const text=card().textContent;
   assert.ok(text.includes('实际结果：尚无可展示结果'),'unconfirmed call has no formal result');
   assert.ok(text.includes('数据管理：尚无数据接收事件'),'call status never implies reception');
   for(const unsupported of [status,'成功回执已保存','实际处理完成','已接收并核对回执','实际结果：已提示','可重试'])assert.equal(text.includes(unsupported),false,'unconfirmed card does not claim '+unsupported);
  }
  assert.ok(calls.length>0&&calls.every(row=>row.method==='GET'),'reading and refreshing sends no commands');
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();
  for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}
 }
});
