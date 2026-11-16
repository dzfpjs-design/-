// Actual Quality component, Journal and runtime client. HTTP below is synthetic.
// These tests do not prove SQL inventory, a Worker result, domain quality or layout.
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
 temporary=await mkdtemp(join(tmpdir(),'threshold-quality-react-v152-'));const entry=join(temporary,'entry.mjs');
 await writeFile(entry,'export {default as Quality} from '+JSON.stringify(join(project,'src/ThresholdQualityV152.jsx'))+';\nexport * from '+JSON.stringify(join(project,'src/quality-review-contract-v152.mjs'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
 const paths={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(paths),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(paths,key)?pathToFileURL(join(project,'node_modules/react',paths[key])).href:key}}}});
 const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk,'actual production component is bundled');const path=join(temporary,'component.mjs');await writeFile(path,chunk.code);return import(pathToFileURL(path).href);
})();return bundle;}
const id=n=>'15210000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value)),sha=raw=>createHash('sha256').update(raw).digest('hex');
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-quality-admin',role:'admin'},evaluation={id:id(3),workspace_id:account.workspace_id,version:9},reviewID=id(4),shadowID=id(5),rootPath='/api/local-rules/evolution',csrf='synthetic-quality-csrf';
const baseline={id:id(10),title:'原登记规则',field:'count',field_label:'数量',operator:'gt',threshold:20,unit:'株'},candidate={...baseline,id:id(11),title:'候选规则',threshold:18};
const range={workspace_id:account.workspace_id,instance_id:id(12),release_id:id(13),entity_id:id(14),source_id:id(15),baseline_id:baseline.id,baseline_hash:'a'.repeat(64),candidate_capability_id:candidate.id,candidate_hash:'b'.repeat(64),source_hash:'c'.repeat(64),rule_id:'count-warning',field:'count',field_label:'数量',unit:'株',operator:'gt',baseline_threshold:20,candidate_threshold:18,instance_name:'合成质量审核实例',release_name:'冻结发布',entity_name:'登记对象',source_name:'数值登记来源',baseline_name:baseline.title,candidate_name:candidate.title};
const material={material_id:id(16),name:'合成核查依据.txt',sha256:sha('synthetic material bytes'),content_path:'/api/setup/materials/'+id(16)+'/content'};
const issue='原来源当前不可核对，机器数值隐藏；原人工判断和撤回入口保留';
const sample=(n,group,reason)=>({group,observation_id:id(n),observation_version:2,instance_id:range.instance_id,baseline_capability_id:baseline.id,observed_at:'2026-10-21T09:00:00+08:00',entity_id:id(n+100),entity_name:group==='evaluation'?'独立评估对象':'影子输入对象',field:'count',field_label:'数量',unit:'株',value:19,feedback_id:id(n+200),feedback_version:1,label:'should_alert',reason,eligible:true,issue:null});
const samples=[sample(20,'evaluation','独立样本原标签理由不可丢'),sample(21,'shadow','未来输入原标签理由不可丢')];
const counts={tp:0,tn:0,fp:0,fn:1,unknown:0,alerts:0,total:1},better={tp:1,tn:0,fp:0,fn:0,unknown:0,alerts:1,total:1};
const statistics={evaluation:{baseline:counts,candidate:better,issues:[]},shadow:{baseline:counts,candidate:better,issues:[]},issues:[]};
const initialRefs=()=>samples.map((row,index)=>({feedback_id:row.feedback_id,expected_version:row.feedback_version,material_refs:[{material_id:material.material_id,sha256:material.sha256,locator:'第'+(index+3)+'页第2行'}]}));
const draftFields=()=>({title:'合成机制审核，不代表领域质量',scope_statement:'仅本次固定实例与登记范围',sampling_statement:'两个独立来源组分别核查并保留局限',limitations:'不证明现场真值或代表性',unresolved_items:[],representative_confirmation:true,label_sources:initialRefs()});
function review({status='draft',version=1,sourceBad=false}={}){
 const human_history=[{version:1,operation:'create',reason:'最初保存的抽样说明',actor_id:account.user_id}];
 for(let n=2;n<=version;n++)human_history.push({version:n,operation:n===2?'approve':'revoke',reason:n===2?'原明确批准理由':'原明确撤回理由',actor_id:account.user_id});
 return {schema:'threshold-quality-review-view-v152',workspace_id:account.workspace_id,id:reviewID,version,actor_id:account.user_id,status,evaluation_id:evaluation.id,evaluation_version:evaluation.version,shadow_id:shadowID,shadow_version:4,purpose:'mechanism_check',...draftFields(),scope:{...range,baseline_threshold:sourceBad?null:20,candidate_threshold:sourceBad?null:18},baseline:{...baseline,threshold:sourceBad?null:20},candidate:{...candidate,threshold:sourceBad?null:18},samples:samples.map(row=>({...row,value:sourceBad?null:row.value,eligible:!sourceBad,issue:sourceBad?issue:null})),statistics:sourceBad?null:copy(statistics),approval_issues:[],current_issue:sourceBad?issue:null,approval_hash:status==='draft'?null:'d'.repeat(64),approved_by:status==='draft'?null:account.user_id,production_eligible:false,can_edit:status==='draft'&&!sourceBad,can_approve:status==='draft'&&!sourceBad,can_reject:status==='draft',can_revoke:status==='approved',human_history};
}
function optionView(evaluationID,selected,{sourceBad=false,emptySource=false,actor=account}={}){
 return {schema:'threshold-quality-options-v152',workspace_id:actor.workspace_id,evaluation_id:evaluationID,evaluation_version:emptySource?null:evaluation.version,purpose:emptySource?null:'mechanism_check',scope:emptySource?null:copy(range),baseline:emptySource?null:copy(baseline),candidate:emptySource?null:copy(candidate),shadows:emptySource?[]:[{id:shadowID,version:4,status:'ended',title:'已结束的合成影子观察',reason:'原有限观察已明确结束',used_inputs:1,completed_inputs:1,eligible:true,issue:null}],selected_shadow_id:selected,shadow_version:selected&&!emptySource?4:null,samples:emptySource?[]:selected?copy(samples):[],materials:emptySource?[]:[copy(material)],materials_has_more:false,current_issue:sourceBad?issue:null,can_create:!!selected&&!sourceBad};
}
function keys(value,expected){assert.deepEqual(Object.keys(value).sort(),[...expected].sort());}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).then(()=>callback({name,mode:'exclusive'})),tail=next.then(()=>{},()=>{});tails.set(name,tail);return next.finally(()=>{if(tails.get(name)===tail)tails.delete(name);});}};}
function deferred(){let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};}

async function harness(run,{savedReview=null,sourceMode='normal',blockedReason=''}={}){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-quality.invalid/',pretendToBeVisual:true,resources:new NoResources()}),calls=[],errors=[],violations=[],gates=new Set();let root,actual,activeKey;
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,state={account:copy(account),evaluation:copy(evaluation),review:savedReview?copy(savedReview):null,sourceMode,blockedReason,authStatus:200,postMode:'normal',failStorage:false,badOptions:false,optionGate:null,commands:new Map()};
 const storageProto=Object.getPrototypeOf(storage),setItem=storageProto.setItem;
 storageProto.setItem=function(key,value){if(this===storage&&state.failStorage&&String(key).startsWith('ga-threshold-quality-v152:')&&String(key).includes(':draft:'))throw Error('synthetic quality draft quota');return setItem.call(this,key,value);};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const original=()=>{const raw=storage.getItem(activeKey);assert.ok(raw,'actual Journal writes before HTTP');const value=JSON.parse(raw);assert.equal(value.schema,'threshold-quality-original-v152');assert.equal(value.method,'POST');assert.equal(value.scope.actor_id,state.account.user_id);assert.equal(value.scope.workspace_id,state.account.workspace_id);assert.equal(value.scope.evaluation_id,state.evaluation.id);assert.equal(value.wire_sha256,sha(value.exact_body));return value;};
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET',call={method,path:url.pathname,search:url.search,raw:options.body};calls.push(call);try{
  assert.equal(url.origin,dom.window.location.origin);assert.equal(options.credentials,'same-origin');const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),state.account.workspace_id);
  if(url.pathname==='/api/me')return state.authStatus===200?response(copy(state.account)):response({detail:'当前身份已失效'},state.authStatus);
  if(method==='GET'&&url.pathname===rootPath+'/threshold-quality-options'){
   assert.equal(url.searchParams.get('evaluation_id'),state.evaluation.id);if(state.sourceMode==='409')return response({detail:issue},409);
   const selected=url.searchParams.get('shadow_id'),value=optionView(state.evaluation.id,selected,{sourceBad:state.sourceMode==='empty',emptySource:state.sourceMode==='empty',actor:state.account});
   if(state.badOptions)value.workspace_id=id(999);const gate=state.optionGate;if(gate){state.optionGate=null;gates.add(gate);gate.entered.resolve();try{await gate.release.promise;}finally{gates.delete(gate);}}return response(value);
  }
  if(method==='GET'&&url.pathname===rootPath+'/threshold-quality-reviews'){assert.equal(url.searchParams.get('evaluation_id'),state.evaluation.id);return response({schema:'threshold-quality-review-list-v152',workspace_id:state.account.workspace_id,reviews:state.review?[copy(state.review)]:[],next_after:null});}
  if(method==='GET'&&url.pathname===rootPath+'/threshold-quality-reviews/'+reviewID){assert.ok(state.review);return response(copy(state.review));}
  if(method==='GET'&&url.pathname.startsWith(rootPath+'/threshold-quality-requests/')){
   const sent=original();assert.equal(url.pathname,rootPath+'/threshold-quality-requests/'+sent.request_id);assert.equal(url.searchParams.get('expected_sha256'),sent.wire_sha256);assert.equal(url.searchParams.get('original_method'),'POST');assert.equal(url.searchParams.get('original_path'),sent.path);
   const saved=state.commands.get(sent.request_id);if(saved){assert.equal(saved.raw,sent.exact_body);assert.equal(saved.path,sent.path);}
   return response(saved?{schema:'threshold-quality-request-state-v152',status:'saved',target_id:reviewID,version:saved.view.version,http_status:saved.status,detail:null}:{schema:'threshold-quality-request-state-v152',status:'unknown',target_id:null,version:null,http_status:null,detail:null});
  }
  if(method==='POST'&&(url.pathname===rootPath+'/threshold-quality-reviews'||url.pathname.startsWith(rootPath+'/threshold-quality-reviews/'+reviewID+'/'))){
   assert.equal(state.authStatus,200);assert.equal(headers.get('X-CSRF-Token'),csrf);const sent=original(),body=JSON.parse(options.body);assert.equal(url.pathname,sent.path);assert.equal(options.body,sent.exact_body);assert.equal(body.request_id,sent.request_id);
   const previous=state.commands.get(body.request_id);if(previous){assert.equal(previous.raw,options.body);return response(copy(state.review),previous.status);}
   if(state.postMode==='unknown'){state.postMode='normal';throw Error('synthetic quality transport unknown');}
   const action=url.pathname===rootPath+'/threshold-quality-reviews'?'create':url.pathname.split('/').at(-1);let status=200;
   if(action==='create'||action==='update'){
    keys(body,['schema','request_id','evaluation_id','expected_evaluation_version','shadow_id','expected_shadow_version','title','scope_statement','sampling_statement','limitations','unresolved_items','representative_confirmation','label_sources',...(action==='update'?['expected_version']:[])]);assert.equal(body.schema,'threshold-quality-draft-v152');assert.equal(body.evaluation_id,state.evaluation.id);assert.equal(body.expected_evaluation_version,evaluation.version);assert.equal(body.shadow_id,shadowID);assert.equal(body.expected_shadow_version,4);assert.equal(state.sourceMode,'normal');if(action==='create')assert.equal(state.review,null);else assert.equal(body.expected_version,state.review.version);
    for(const ref of body.label_sources){keys(ref,['feedback_id','expected_version','material_refs']);assert.ok(samples.some(row=>row.feedback_id===ref.feedback_id&&row.feedback_version===ref.expected_version));for(const refMaterial of ref.material_refs){keys(refMaterial,['material_id','sha256','locator']);assert.equal(refMaterial.material_id,material.material_id);assert.equal(refMaterial.sha256,material.sha256);assert.ok(refMaterial.locator.trim());}}
    const next=review();for(const key of ['title','scope_statement','sampling_statement','limitations','unresolved_items','representative_confirmation','label_sources'])next[key]=copy(body[key]);next.actor_id=state.account.user_id;next.evaluation_id=state.evaluation.id;next.version=action==='create'?1:state.review.version+1;next.human_history=action==='create'?[{version:1,operation:'create',reason:body.sampling_statement,actor_id:state.account.user_id}]:[...state.review.human_history,{version:next.version,operation:'update',reason:body.sampling_statement,actor_id:state.account.user_id}];state.review=next;status=action==='create'?201:200;
   }else{
    assert.ok(['approve','reject','revoke'].includes(action));keys(body,['schema','request_id','expected_version','reason','confirm']);assert.equal(body.schema,'threshold-quality-control-v152');assert.equal(body.expected_version,state.review.version);assert.equal(body.confirm,true);assert.ok(body.reason.trim());assert.equal(state.review['can_'+action],true);const nextStatus={approve:'approved',reject:'rejected',revoke:'revoked'}[action];state.review={...state.review,status:nextStatus,version:state.review.version+1,can_edit:false,can_approve:false,can_reject:false,can_revoke:action==='approve',approval_hash:action==='approve'?'d'.repeat(64):state.review.approval_hash,approved_by:action==='approve'?state.account.user_id:state.review.approved_by,human_history:[...state.review.human_history,{version:state.review.version+1,operation:action,reason:body.reason,actor_id:state.account.user_id}]};
   }
   state.commands.set(body.request_id,{raw:options.body,path:url.pathname,status,view:copy(state.review)});if(state.postMode==='lost'){state.postMode='normal';throw Error('synthetic quality transport lost after save');}return response(copy(state.review),status);
  }
  if(method==='GET'&&url.pathname==='/api/local-rules/'+baseline.id)return response({id:baseline.id,rules:[{id:range.rule_id}],context_available:false});
  const feedbackSample=samples.find(row=>url.pathname===rootPath+'/observations/'+row.observation_id+'/feedback-context');
  if(method==='GET'&&feedbackSample){assert.equal(url.searchParams.get('rule_id'),range.rule_id);const row=feedbackSample,basis={schema:'threshold-evolution-feedback-basis-v147',basis_hash:'f'.repeat(64),observation_id:row.observation_id,observation_version:row.observation_version,baseline_capability_id:baseline.id,baseline_capability_hash:range.baseline_hash,rule_id:range.rule_id,field:row.field,field_label:row.field_label,unit:row.unit,operator:'gt',threshold:20,actual_value:row.value,actual_hit:false,instance_id:row.instance_id,release_id:range.release_id,entity_id:row.entity_id,source_id:range.source_id},entry={id:row.feedback_id,version:row.feedback_version,root_id:id(600),revision:1,previous_id:null,status:'submitted',actor_id:state.account.user_id,label:row.label,reason:row.reason,cancel_reason:null,classification:'FN'},feedback={schema:'threshold-evolution-feedback-view-v147',...entry,basis,history:[entry],latest_submitted:entry,can_edit:false,can_cancel:false,can_submit:false,can_revise:true};return response({schema:'threshold-evolution-feedback-context-v147',supported:true,issue:null,basis,feedback});}
  throw Error('Unexpected quality HTTP '+method+' '+url.pathname);
 }catch(cause){if(!String(cause.message).startsWith('synthetic quality transport'))violations.push(cause.stack);throw cause;}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 try{
  actual=await component();assert.equal(actual.BundledReact,React);actual.setSession(csrf,state.account.workspace_id,state.account.user_id,state.account.username);const probe=new actual.QualityReviewJournalV152(actual.qualityScopeV152(state.account,state.evaluation));activeKey=probe.active;
  const container=dom.window.document.getElementById('root'),settle=async()=>{for(let i=0;i<8;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});},wait=async(predicate,label)=>{for(let i=0;i<80;i++){if(predicate())return;await settle();}assert.ok(predicate(),label+'; '+container.textContent.slice(0,900));};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label),field=label=>container.querySelector('[aria-label="'+label+'"]');
  const click=async label=>{const node=button(label);assert.ok(node,'actual '+label+' exists');assert.equal(node.disabled,false,label+' is enabled');await act(async()=>node.click());await settle();};
  const edit=async(label,value)=>{const node=field(label);assert.ok(node,'actual '+label+' exists');assert.equal(node.disabled,false);assert.ok(!node.closest('fieldset')?.disabled,label+' fieldset is editable');await act(async()=>{node.value=value;node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  const check=async(prefix,index=0)=>{const label=[...container.querySelectorAll('label')].filter(node=>node.textContent.trim().startsWith(prefix))[index],node=label?.querySelector('input[type="checkbox"]');assert.ok(node,'actual '+prefix+' checkbox exists');assert.equal(node.disabled,false);assert.ok(!node.closest('fieldset')?.disabled);await act(async()=>node.click());await settle();};
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Quality,{evaluation:state.evaluation,account:state.account,Btn:Button,blockedReason:state.blockedReason}))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:cause=>errors.push(cause.message),onCaughtError:cause=>errors.push(cause.message)});await render();},remount=async()=>{await act(async()=>root.unmount());root=null;await mount();};
  const open=async()=>{await click('核对质量依据');await wait(()=>button('刷新标签与原件')?.disabled===false,'quality history and options complete');};
  const fill=async()=>{await edit('已结束的影子观察',shadowID);await wait(()=>container.textContent.includes(samples[0].reason),'actual sample preview is read');const values=draftFields();for(const[fieldName,value]of [['质量审核名称',values.title],['适用范围说明',values.scope_statement],['抽样与覆盖说明',values.sampling_statement],['质量限制说明',values.limitations]])await edit(fieldName,value);await check('我已说明样本覆盖范围与局限');for(let i=0;i<samples.length;i++){await check('将这项已提交判断纳入质量审核',i);await edit('核查原件 '+(i+1),material.material_id);await edit('原件位置 '+(i+1)+' 1','第'+(i+3)+'页第2行');}};
  const persisted=value=>Array.from({length:storage.length},(_,index)=>storage.getItem(storage.key(index))).filter(Boolean).some(raw=>{const contains=item=>item===value||!!item&&typeof item==='object'&&Object.values(item).some(contains);try{return contains(JSON.parse(raw));}catch{return raw===value;}}),unloading=()=>{const event=new dom.window.Event('beforeunload',{cancelable:true});dom.window.dispatchEvent(event);return event.defaultPrevented;};
  await mount();assert.equal(calls.length,0,'closed Quality has zero HTTP');await run({actual,container,calls,state,storage,session,button,field,click,edit,check,open,fill,wait,render,remount,persisted,unloading,get activeKey(){return activeKey;},setAccount:async next=>{state.account=copy(next);actual.setSession(csrf,next.workspace_id,next.user_id,next.username);const probe=new actual.QualityReviewJournalV152(actual.qualityScopeV152(next,state.evaluation));activeKey=probe.active;await render();}});assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{
  // Invalidate the actual component before releasing a delayed fixture response.
  // Drain its continuations under this DOM/globals so a failed assertion cannot
  // leak an unfinished act() or request into the next mounted test.
  await act(async()=>{if(root)root.unmount();for(const gate of gates)gate.release.resolve();await new Promise(done=>setImmediate(done));});
  for(let i=0;i<4;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});
  storageProto.setItem=setItem;dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}
 }
}

test('V152 actual Quality saves source references and locators and cold-remounts without approving',async()=>{
 await harness(async({calls,state,open,fill,click,wait,storage,activeKey,remount,field,persisted})=>{
  await open();await fill();assert.equal(calls.filter(row=>row.method==='POST').length,0);await click('保存质量审核草稿');await wait(()=>state.review&&storage.getItem(activeKey)===null,'saved original is independently GET-checked');
  assert.equal(state.review.status,'draft');assert.deepEqual(state.review.label_sources,initialRefs());assert.equal(state.review.production_eligible,false);assert.equal(calls.filter(row=>row.method==='POST').length,1);assert.equal(persisted('第3页第2行'),true);
  const before=calls.length;await remount();await open();assert.ok(calls.slice(before).every(row=>row.method==='GET'));assert.equal(field('质量审核名称').value,draftFields().title);assert.equal(field('原件位置 1 1').value,'第3页第2行');assert.equal(state.review.status,'draft');
 });
});

for(const outcome of ['lost','unknown'])test('V152 '+outcome+' approval-draft response retains exact original across remount and never auto-POSTs',async()=>{
 await harness(async({calls,state,open,fill,click,wait,storage,activeKey,remount,field})=>{
  await open();await fill();state.postMode=outcome;await click('保存质量审核草稿');await wait(()=>storage.getItem(activeKey)!==null&&calls.some(row=>row.method==='POST'),'actual durable original remains after transport failure');const raw=storage.getItem(activeKey),sent=JSON.parse(raw),before=calls.length;
  await remount();await open();assert.equal(storage.getItem(activeKey),raw);assert.ok(calls.slice(before).every(row=>row.method==='GET'));assert.equal(field('质量审核名称').value,draftFields().title);await click('核对原质量请求');
  if(outcome==='lost'){await wait(()=>storage.getItem(activeKey)===null,'saved original is resolved by GET');assert.equal(state.commands.size,1);assert.equal(state.review.version,1);}else{assert.equal(storage.getItem(activeKey),raw);await click('核对原质量请求');assert.equal(state.commands.size,0);assert.equal(state.review,null);}
  const lookups=calls.slice(before).filter(row=>row.path.startsWith(rootPath+'/threshold-quality-requests/'));assert.ok(lookups.length);for(const row of lookups){const url=new URL(row.path+row.search,'https://threshold-quality.invalid');assert.equal(url.pathname,rootPath+'/threshold-quality-requests/'+sent.request_id);assert.equal(url.searchParams.get('expected_sha256'),sent.wire_sha256);}assert.equal(calls.filter(row=>row.method==='POST').length,1);
 });
});

test('V152 options409 leaves historical human labels readable and allows explicit revoke of an approved review',async()=>{
 await harness(async({container,calls,state,open,click,edit,check,wait,button})=>{
  await open();await click('打开这项质量审核');await wait(()=>button('撤回质量审核')?.disabled===false,'approved original remains controllable');assert.ok(container.textContent.includes(samples[0].reason));assert.ok(container.textContent.includes('最初保存的抽样说明'));assert.ok(!button('保存质量审核草稿')||button('保存质量审核草稿').disabled);assert.equal(container.querySelectorAll('table').length,0);
  await click('撤回质量审核');await edit('质量审核操作理由','原来源不可核，明确撤回审核，保留原标签与材料位置');await check('我确认本次质量审核操作');await click('确认撤回质量审核');await wait(()=>state.review.status==='revoked','source failure does not block actual explicit revoke');assert.equal(state.review.samples[0].value,null);assert.equal(state.review.statistics,null);assert.ok(container.textContent.includes('原明确批准理由'));assert.deepEqual(calls.filter(row=>row.method==='POST').map(row=>row.path),[rootPath+'/threshold-quality-reviews/'+reviewID+'/revoke']);
 },{savedReview:review({status:'approved',version:2,sourceBad:true}),sourceMode:'409',blockedReason:'原比较依据不允许新写入'});
});

test('V152 safe empty options cannot hide the opened review human samples or resurrect machine values',async()=>{
 await harness(async({container,calls,open,click,wait,button})=>{
  await open();await click('打开这项质量审核');await wait(()=>button('撤回质量审核')?.disabled===false,'safe review detail is independently cold-read');assert.ok(container.textContent.includes(samples[0].reason),'opened detail retains its independent evaluation label reason');assert.ok(container.textContent.includes(samples[1].reason),'opened detail retains its shadow-input label reason');assert.ok(container.textContent.includes('尚无可核对样本')===false);assert.equal(container.querySelectorAll('table').length,0,'damaged machine statistics remain hidden');assert.equal(calls.filter(row=>row.method==='POST').length,0);
 },{savedReview:review({status:'approved',version:2,sourceBad:true}),sourceMode:'empty',blockedReason:'原比较依据不允许新写入'});
});

test('V152 a sample explicitly opens the real original business-feedback component and its scoped context',async()=>{
 await harness(async({container,calls,open,edit,click,wait})=>{
  await open();await edit('已结束的影子观察',shadowID);assert.ok(!calls.some(row=>row.path==='/api/local-rules/'+baseline.id));await click('核对这条业务判断');assert.ok(!calls.some(row=>row.path==='/api/local-rules/'+baseline.id),'mounting original feedback is itself opt-in');await click('纠正提示判断');await wait(()=>container.textContent.includes('当前已提交判断'),'real V147 feedback has read its original saved label');const context=calls.find(row=>row.path===rootPath+'/observations/'+samples[0].observation_id+'/feedback-context');assert.ok(context);assert.equal(new URLSearchParams(context.search).get('rule_id'),range.rule_id);assert.ok(container.textContent.includes(samples[0].reason));assert.ok(calls.every(row=>row.method==='GET'));
 });
});

test('V152 actor switch masks original local work before late options can publish into the new scope',async()=>{
 await harness(async({container,state,calls,open,edit,button,wait,setAccount,field,persisted})=>{
  await open();await edit('质量审核名称','甲账号独立未提交审核稿');const entered=deferred(),release=deferred();state.optionGate={entered,release};
  // Finish the click's act scope before starting a different render's act scope;
  // only the HTTP fixture remains deferred, never an unawaited click helper.
  await act(async()=>button('刷新标签与原件').click());await entered.promise;
  try{const next={...account,user_id:id(70),username:'synthetic-other-admin'};await setAccount(next);assert.ok(!container.textContent.includes('甲账号独立未提交审核稿'));assert.equal(field('质量审核名称'),null,'first paint cannot display previous actor form');}
  finally{await act(async()=>{release.resolve();await new Promise(done=>setImmediate(done));});}
  assert.equal(field('质量审核名称'),null);await open();await wait(()=>!!field('质量审核名称'),'new actor opens an independently owned blank form');assert.equal(field('质量审核名称').value,'');assert.equal(persisted('甲账号独立未提交审核稿'),true,'original actor draft remains durable');assert.equal(calls.filter(row=>row.method==='POST').length,0);
 });
});

test('V152 wrong-scope or extended DTO is rejected without a write or a raw fallback',async()=>{
 await harness(async({actual,calls,state,open,container,button})=>{
  const scope=actual.qualityScopeV152(account,evaluation),valid=optionView(evaluation.id,null);assert.throws(()=>actual.checkedQualityOptionsV152({...valid,extra:true},scope,null));assert.throws(()=>actual.checkedQualityReviewV152({...review(),workspace_id:id(77)},scope));state.badOptions=true;await open();assert.ok(container.textContent.includes('无法完整核对'));assert.ok(!button('保存质量审核草稿')||button('保存质量审核草稿').disabled);assert.equal(container.querySelectorAll('table').length,0);assert.equal(calls.filter(row=>row.method==='POST').length,0);
 });
});

test('V152 local quota failure protects the human draft until explicit save and cold recovery',async()=>{
 await harness(async({state,container,calls,open,edit,click,wait,field,unloading,persisted,remount})=>{
  await open();state.failStorage=true;await edit('质量审核名称','本机配额失败也不能丢的质量说明');assert.equal(field('质量审核名称').value,'本机配额失败也不能丢的质量说明');assert.equal(unloading(),true);await click('收起质量审核');assert.ok(field('质量审核名称'),'unsaved human work cannot be hidden');state.failStorage=false;await click('重试保存质量本机稿');await wait(()=>!unloading(),'exact persistence releases only the saved warning');assert.equal(persisted('本机配额失败也不能丢的质量说明'),true);await remount();await open();assert.equal(field('质量审核名称').value,'本机配额失败也不能丢的质量说明');assert.equal(calls.filter(row=>row.method==='POST').length,0);assert.ok(container.textContent.includes('不自动认证现场真值'));
 });
});

test('V152 rescued nonlive draft clears its old warning only after actual readback and cannot replace the newer human text',async()=>{
 await harness(async({state,calls,open,edit,click,wait,field,unloading,persisted,remount})=>{
  await open();state.failStorage=true;await edit('质量审核名称','旧窗口未落盘质量理由甲');assert.equal(unloading(),true);
  await remount();await open();assert.equal(field('质量审核名称').value,'旧窗口未落盘质量理由甲','new owner must rescue the original human work even when its own first persistence fails');await edit('质量审核名称','新窗口已修改并实际保存质量理由乙');state.failStorage=false;await click('重试保存质量本机稿');await wait(()=>persisted('新窗口已修改并实际保存质量理由乙'),'new owner actually saves and reads back its complete form');
  const warningAfterSuccessfulReadback=unloading();await remount();await open();assert.deepEqual({warningAfterSuccessfulReadback,title:field('质量审核名称').value},{warningAfterSuccessfulReadback:false,title:'新窗口已修改并实际保存质量理由乙'},'nonlive rescued owner cannot leave a ghost warning or override the later durable owner');assert.equal(calls.filter(row=>row.method==='POST').length,0);
 });
});
