// Actual ReactDOM/StrictMode components, journals and runtime-api. Only the
// public HTTP fixtures/storage are synthetic; this is not model or PG evidence.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {proposalScope,proposalJournalKey,emptyProposalForm} from '../src/native-query-proposal-contract.mjs';
import {planningScope,planningStorageKey} from '../src/native-step-planning-contract.mjs';
import {executionScope,executionStorageKey} from '../src/native-step-execution-contract.mjs';
import {suggestionCase} from '../src/native-suggestion-contract.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function components(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'native-descendant-v143-'));const entry=join(temporary,'entry.mjs');
  const names={Query:'NativeQueryProposals',Planning:'NativeStepPlanning',Execution:'NativeStepExecution',Experience:'NativeMethodExperience',Suggestions:'NativeMethodSuggestions'};
  await writeFile(entry,Object.entries(names).map(([name,file])=>'export {default as '+name+'} from '+JSON.stringify(join(project,'src/'+file+'.jsx'))+';').join('\n')+'\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\n');
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);const output=join(temporary,'components.mjs');await writeFile(output,chunk.code);return import(pathToFileURL(output).href);
 })();return bundle;
}
const id=n=>'14310000-0000-4000-8000-'+String(n).padStart(12,'0'),H='a'.repeat(64);
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-descendant-operator',role:'operator'},task={id:id(3),version:4,title:'原已保存人工业务任务'},nonce=id(4),planID=id(5),suggestionID=id(6);
const reason='当前启用版本无法核实，暂不能开始新的执行。',csrf='synthetic-descendant-csrf',base='/api/workspace/tasks/'+task.id;
const evidence={source:'archive',minimum_verification:'recorded',minimum_records_per_entity:1,time:{mode:'archive',timezone:'Asia/Shanghai'}};
const experience=()=>({schema:'native-method-experience-v1',task_id:task.id,version:4,can_manage:true,field_options:[],template_options:[],available_cases:[],has_more:false,permissions:{},source:{accepted:true,acceptance_hash:H,title:task.title,accepted_at:'2026-10-21T00:00:00Z',evidence_refs:[]},draft:null,use:null,evolution:null,history:{uses:[],disengagements:[],confirmations:[],feedback:[]}});
function deferred(){let release;return {promise:new Promise(resolve=>{release=resolve;}),release:()=>release()};}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources are forbidden');}}
function Button({children,primary,ghost,icon,...props}){return React.createElement('button',{type:'button',...props},children);}
function cloudDetail(){
 return {id:suggestionID,task_id:task.id,task_version:4,version:2,record_hash:H,status:'failed',source_kind:'cloud_model',permissions:{save:{allowed:false},decline:{allowed:true},cancel:{allowed:true},retry:{allowed:true},import:{allowed:false},local_recover:{allowed:true}},history:[],cloud_input:{contract_version:'native-method-projection-v1',fields:[{alias:'F1',type:'number',templates:['units'],flags:{source_identity:'known',declared_time:'unknown',units:'unknown',comparability:'unknown'},records_bucket:'2+'}],source:'observations',criteria:['evidence_sufficient'],consumed_templates:[],allowed_pairs:[{alias:'F1',template:'units',pattern_id:'declared_units_v1'}],limitations:['proposal_only','unknown_is_not_verified','no_old_values','no_device_or_model_execution','no_automatic_review']},generation_contract:{version:'native-method-generation-v1',prompt_version:'native-method-prompt-v1',prompt_hash:null,output_contract_hash:null,generation_contract_hash:null,rules:['只核对原结构，不证明现场事实。'],max_steps:5},dispatch_messages_hash:null,dispatch_options:null,capacity:null,raw_quality:null,error_code:null,missing_required_pairs:[],field_mapping:[{alias:'F1',field:'reading',field_label:'登记值'}],patterns:[],title:'原已付且有回复的候选',summary:'仅供人工核对',limits:'不改变原验收',steps:[],saved_edit:null,has_saved_response:true,provider:'synthetic-provider',model:'synthetic-model',call:{status:'received',usage:{},cost:{status:'unknown'}},source_error:null};
}
async function harness(kind,operation,run){
 const errors=[],violations=[],calls=[],gates=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://native-descendant-v143.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole:console});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,state={blocked:reason,nextMeGate:null,localRecovery:false},originalReason='完整保留的原操作说明';
 for(const[key,value]of Object.entries({'ga-setup-csrf':csrf,'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':account.workspace_id,'ga-setup-csrf-username':account.username}))session.setItem(key,value);
 const tails=new Map();Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:{request(name,_options,callback){const result=(tails.get(name)||Promise.resolve()).then(callback);tails.set(name,result.catch(()=>{}));return result;}}});
 let original,key,queryView;
 const planBase=base+'/intelligent-plan',executionBase=planBase+'/'+planID+'/execution',suggestionBase=base+'/experience/suggestions';
 if(kind==='Query'){
  original={operation,proposalId:operation==='execute'?id(8):null,body:{request_id:nonce,expected_version:4,reason:originalReason,...(operation==='execute'?{proposal_id:id(8)}:{model_profile_id:id(9),preview_hash:H,cloud_authorized:true})}};
  const scope=proposalScope(account,task.id);key=proposalJournalKey(account,task.id);storage.setItem(key,JSON.stringify({scope,form:{...emptyProposalForm(),question:'原对象的原资料核查',reason:originalReason,evidenceConfirmed:true},dirty:false,pending:original}));
  queryView={schema:'native-query-proposal-v1',task_id:task.id,task_version:4,mode:'reference_only',draft:{question:'原对象的原资料核查',evidence,revision:1,hash:H},current:null,history:[],models:[],can_manage:true,can_create:true,scope_error:null,catalog:{objects:[],fields:[],relation_types:[],query_tools:[]},adopted_goal_draft:null,reference_query:null};
 }else if(kind==='Planning'){
  const scope=planningScope(account,task.id);key=planningStorageKey(scope);original={scope,operation:'start',plan_id:null,body:{request_id:nonce,expected_version:4,reason:originalReason,model_profile_id:id(9),preview_hash:H,cloud_authorized:true}};
  storage.setItem(key,JSON.stringify({schema:1,scope,form:{model_profile_id:id(9),reason:originalReason},pending:original}));
 }else if(kind==='Execution'){
  const scope=executionScope(account,task.id,planID);key=executionStorageKey(scope);original={scope,operation,body:{request_id:nonce,expected_version:4,reason:originalReason,...(operation==='approve'?{expected_plan_version:3,approval_hash:H,authorize_external_inputs:false}:{expected_execution_version:2,acknowledge_possible_charge:true})}};
  storage.setItem(key,JSON.stringify({schema:1,scope,form:{reason:originalReason},pending:original}));
 }else{
  const policy=operation==='policy',retry=operation==='retry';
  original={operation,suggestionID:retry?suggestionID:null,path:policy?'/policy':retry?'/'+suggestionID+'/retry':operation==='local_start'?'/local/start':'/start',body:{request_id:nonce,expected_version:4,reason:originalReason,...(policy?{enabled:true,expected_policy_version:null,policy_record_hash:null,model_profile_id:id(9),preview_hash:H,projection_contract_hash:H,generation_contract_hash:H,cloud_authorized:true,daily_limit:1,valid_days:7}:retry?suggestionCase(cloudDetail()):operation==='local_start'?{preview_hash:H}:{model_profile_id:id(9),preview_hash:H,projection_contract_hash:H,generation_contract_hash:H,cloud_authorized:true})}};
  key='ga-native-suggestion:'+JSON.stringify([account.workspace_id,account.username,task.id]);
  storage.setItem(key,JSON.stringify({selected:retry?suggestionID:'',edits:{},reason:originalReason,modelID:'',dailyLimit:1,validDays:7,pending:original}));
 }
 const originalRaw=storage.getItem(key),respond=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),path=url.pathname+url.search,method=options.method||'GET';calls.push({path,method,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),csrf);
   if(path==='/api/me'&&method==='GET'){const gate=state.nextMeGate;state.nextMeGate=null;if(gate)await gate.promise;return respond(account);}
   if(kind==='Query'){
    if(path===base+'/query-proposals'&&method==='GET')return respond(queryView);
    if(path===base+'/supporting-queries?limit=20'&&method==='GET')return respond({task_id:task.id,task_version:4,items:[],has_more:false,next_cursor:null,permissions:{can_execute:true}});
    if(method==='POST'&&[base+'/supporting-queries/requests/recover',base+'/query-proposals/requests/recover'].includes(path)){const body=JSON.parse(options.body);assert.deepEqual(body.original_request,original.body);return respond(operation==='execute'?{status:'unknown',task_version:4}:{saved:false,abandoned:false,task_version:4});}
    if(method==='POST'&&[base+'/query-proposals',base+'/supporting-queries'].includes(path))return respond({detail:'Synthetic fresh query transport must be blocked'},503);
   }
   if(kind==='Planning'){
    if(path===planBase&&method==='GET')return respond({task_version:4,can_manage:true,can_stop:true,can_recover:true,scope_error:null,models:[{id:id(9),name:'原已发布模型',provider:'synthetic',model:'frozen-model'}],current:null,history:[]});
    if(path===planBase+'/requests/recover'&&method==='POST'){assert.deepEqual(JSON.parse(options.body).original_request,original.body);return respond({saved:false,abandoned:false,task_version:4});}
    if(path===planBase&&method==='POST')return respond({detail:'Synthetic fresh planning transport must be blocked'},503);
   }
   if(kind==='Execution'){
    if(path===executionBase&&method==='GET')return respond({task_version:4,plan_id:planID,execution:operation==='retry'?{id:id(10),version:2,status:'failed',steps:[],history:[],possible_charge:false}:null,can_preview:true,can_approve:true,can_retry:true,can_stop:true,can_review:false,can_recover:true,scope_error:null});
    if(path===executionBase+'/requests/recover'&&method==='POST'){assert.deepEqual(JSON.parse(options.body).original_request,original.body);return respond({saved:false,abandoned:false,task_version:4});}
    if(method==='POST'&&[executionBase+'/approve',executionBase+'/retry'].includes(path))return respond({detail:'Synthetic fresh execution transport must be blocked'},503);
   }
   if(kind==='Experience'&&path===base+'/experience'&&method==='GET')return respond(experience());
   if(['Experience','Suggestions'].includes(kind)){
    if(path===suggestionBase+'/models'&&method==='GET')return respond([]);
    if(path===suggestionBase+'?limit=20'&&method==='GET')return respond({items:operation==='retry'?[cloudDetail()]:[],version:4,next_cursor:null,policy:null,permissions:{start:{allowed:true},local_start:{allowed:true}}});
    if(path===suggestionBase+'/'+suggestionID&&method==='GET')return respond(cloudDetail());
    if(path===suggestionBase+'/requests/recover'&&method==='POST'){assert.deepEqual(JSON.parse(options.body).original_request,original.body);return respond({version:4,saved:false,abandoned:false});}
    if(path===suggestionBase+original.path&&method==='POST'){
     assert.deepEqual(JSON.parse(options.body),original.body);
     if(operation==='retry'&&state.localRecovery)return respond({version:4,suggestion:{...cloudDetail(),status:'needs_review'}});
     return respond({detail:'Synthetic fresh suggestion transport must be blocked'},503);
    }
   }
   throw Error('Unexpected synthetic request '+method+' '+path);
  }catch(error){violations.push(error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 const container=dom.window.document.getElementById('root');let root;
 try{
  const actual=await components();actual.setSession(csrf,account.workspace_id,account.user_id,account.username);
  const settle=async()=>{await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});for(let turn=0;turn<16;turn++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const props=()=>({task,account,Btn:Button,locked:false,executionBlockedReason:state.blocked,onChanged:async()=>{},...(kind==='Query'?{goal:{goal_confirmed:true}}:kind==='Execution'?{planID,planVersion:3}:kind==='Suggestions'?{experience:experience(),methodDirty:false,methodPending:null}:{})});
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual[kind],props()))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const button=label=>{const node=[...container.querySelectorAll('button')].find(item=>item.textContent.trim()===label);assert.ok(node,'actual '+kind+' button '+label+'; '+container.textContent.slice(0,900));return node;};
  const click=async label=>{const node=button(label);assert.equal(node.disabled,false,label+' remains enabled');await act(async()=>node.click());await settle();};
  await run({state,storage,container,calls,key,original,originalRaw,mount,render,button,click,settle,holdMe(){const gate=deferred();gates.push(gate);state.nextMeGate=gate;return gate;}});
  await act(async()=>root.unmount());root=null;assert.deepEqual(errors,[],'actual ReactDOM must render without errors');assert.deepEqual(violations,[],'all HTTP contracts remain explicit');
 }finally{for(const gate of gates)gate.release();if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}

for(const operation of ['create','execute'])test('V143 Query '+operation+' original unknown cannot first dispatch during current-read failure, but original probe survives',()=>harness('Query',operation,async h=>{
 await h.mount();assert.equal(h.button('恢复原查询请求').disabled,true,'negative or unknown original query is not execution permission while current configuration is unknown');
 await h.click('核对原查询请求是否已保存');assert.equal(h.button('恢复原查询请求').disabled,true);assert.ok(h.container.textContent.includes('原请求尚未保存'));assert.deepEqual(JSON.parse(h.storage.getItem(h.key)).pending.body,h.original.body);
 assert.equal(h.calls.filter(row=>row.method==='POST'&&!row.path.endsWith('/requests/recover')).length,0);
}));
test('V143 Planning original start cannot be resumed as a new cloud call while its probe and human reason remain',()=>harness('Planning','start',async h=>{
 await h.mount();assert.equal(h.button('按原编号恢复原请求').disabled,true);await h.click('核对原请求是否已保存');assert.equal(h.button('按原编号恢复原请求').disabled,true);assert.equal(h.storage.getItem(h.key),h.originalRaw);assert.equal(h.calls.some(row=>row.method==='POST'&&row.path===base+'/intelligent-plan'),false);
}));
for(const operation of ['approve','retry'])test('V143 Execution '+operation+' negative probe preserves original request without allowing first dispatch',()=>harness('Execution',operation,async h=>{
 await h.mount();await h.click('核对原执行请求是否保存');assert.equal(h.button('按原编号继续送达').disabled,true,'original approval or retry may still be an unsent execution');assert.equal(h.storage.getItem(h.key),h.originalRaw);assert.equal(h.calls.filter(row=>row.method==='POST'&&!row.path.endsWith('/requests/recover')).length,0);
}));
function originalSuggestionPreserved(h){
 const rows=Array.from({length:h.storage.length},(_,index)=>{try{return JSON.parse(h.storage.getItem(h.storage.key(index)));}catch{return null;}});
 assert.ok(rows.some(row=>row?.raw===h.originalRaw),'the exact legacy raw is retained in its immutable migration backup');
 const stored=rows.filter(row=>row?.schema==='native-suggestion-outbox-v130').flatMap(row=>row.requests).find(item=>item.body.request_id===nonce);
 assert.ok(stored,'the shared outbox retains the original nonce');assert.deepEqual(stored.body,h.original.body,'the complete original command remains unchanged');
}
for(const kind of ['Suggestions','Experience'])test('V143 '+kind+' forwards current-read failure to the actual local suggestion original request',()=>harness(kind,'local_start',async h=>{
 await h.mount();assert.equal(h.button('恢复原建议请求').disabled,true,'local candidate creation is still a new operation when not yet saved');await h.click('核对原建议请求');assert.equal(h.button('恢复原建议请求').disabled,true);originalSuggestionPreserved(h);assert.equal(h.calls.filter(row=>row.method==='POST'&&!row.path.endsWith('/requests/recover')).length,0);
}));
for(const operation of ['start','policy'])test('V143 Suggestions '+operation+' cannot dispatch a cloud request or enable automatic policy while current eligibility is unknown',()=>harness('Suggestions',operation,async h=>{
 await h.mount();assert.equal(h.button('恢复原建议请求').disabled,true);await h.click('核对原建议请求');originalSuggestionPreserved(h);assert.equal(h.calls.filter(row=>row.method==='POST'&&!row.path.endsWith('/requests/recover')).length,0);
}));
test('V143 Suggestions checks the latest current gate after actual identity await, retaining the exact original cloud request',()=>harness('Suggestions','start',async h=>{
 h.state.blocked='';await h.mount();const gate=h.holdMe();await h.click('恢复原建议请求');h.state.blocked=reason;await h.render();gate.release();await h.settle();
 assert.equal(h.calls.filter(row=>row.method==='POST'&&row.path===base+'/experience/suggestions/start').length,0,'a stale click must not cross the identity-await boundary into a new cloud dispatch');originalSuggestionPreserved(h);assert.equal(h.button('核对原建议请求').disabled,false);
}));
test('V143 Suggestions keeps saved-response local recovery available without a new cloud authorization',()=>harness('Suggestions','retry',async h=>{
 h.state.localRecovery=true;assert.deepEqual(Object.keys(h.original.body).sort(),['request_id','expected_version','reason','expected_suggestion_version','record_hash'].sort());assert.equal(h.original.body.record_hash,cloudDetail().record_hash);assert.equal(h.original.body.expected_suggestion_version,cloudDetail().version);
 await h.mount();assert.equal(h.button('恢复原建议请求').disabled,false);await h.click('恢复原建议请求');const rows=h.calls.filter(row=>row.method==='POST'&&row.path.endsWith('/'+suggestionID+'/retry'));assert.equal(rows.length,1);assert.deepEqual(JSON.parse(rows[0].body),h.original.body);assert.equal(Object.hasOwn(JSON.parse(rows[0].body),'cloud_authorized'),false);
}));
