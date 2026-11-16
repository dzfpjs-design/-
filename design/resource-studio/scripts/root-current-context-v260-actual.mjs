// ROOT-EXECUTED acceptance helper. The author does not run this file or read its
// private input. It uses production ReactDOM + authenticated ordinary HTTP GETs;
// it is not native-browser, source-report quality or end-to-end M3 evidence.
import assert from 'node:assert/strict';
import {readFile,writeFile,rename,chmod,stat,mkdtemp,rm} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {tmpdir} from 'node:os';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import {webcrypto,randomUUID} from 'node:crypto';

const require=createRequire(new URL('../package.json',import.meta.url));
const project=resolve(fileURLToPath(new URL('..',import.meta.url)));
const originalConsole={log:console.log,error:console.error,warn:console.warn};
const summary={schema:'root-current-context-acceptance-result-v260',surface:'offline_reactdom_with_real_http',native_browser_verified:false,mode:process.env.GA_V260_ACCEPTANCE_MODE||'prepare',passed:false,checkpoints:[],http:{GET:0,HEAD:0,blocked_writes:0},console_warnings:0,console_errors:0};
let stage='input',temporary=null,root=null,window=null,restoreGlobals=()=>{},restoreConsole=()=>{};
const check=(value,code)=>{if(!value){const error=Error(code);error.acceptanceCode=code;throw error;}};
const exact=(value,required,optional=[])=>value&&typeof value==='object'&&!Array.isArray(value)&&required.every(k=>Object.hasOwn(value,k))&&Object.keys(value).every(k=>required.includes(k)||optional.includes(k));
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const checkpoint=value=>{stage=value;summary.checkpoints.push(value);};
class MemoryStorage{
 constructor(rows=[]){this.rows=new Map(rows);}
 get length(){return this.rows.size;}
 key(index){return [...this.rows.keys()][index]??null;}
 getItem(key){return this.rows.get(String(key))??null;}
 setItem(key,value){this.rows.set(String(key),String(value));}
 removeItem(key){this.rows.delete(String(key));}
}

async function main(){
 check(['prepare','restore'].includes(summary.mode),'invalid_mode');
 const inputPath=process.env.GA_V260_ACCEPTANCE_INPUT;check(typeof inputPath==='string'&&inputPath.length>0,'missing_input_path');
 const input=JSON.parse(await readFile(resolve(inputPath),'utf8'));
 check(exact(input,['schema','origin','cookie','csrf_token','workspace_id','source_draft_id','working_draft_id','source_context','expected_source_document','expected_working_document','producer','human_note'],['local_state_path'])&&input.schema==='root-current-context-acceptance-v260','invalid_input_shape');
 check([input.workspace_id,input.source_draft_id,input.working_draft_id].every(uuid),'invalid_input_scope');
 check(typeof input.cookie==='string'&&input.cookie.length>0&&!/[\r\n]/.test(input.cookie)&&typeof input.csrf_token==='string'&&input.csrf_token.length>0,'invalid_auth_input');
 check(typeof input.human_note==='string'&&input.human_note.trim().length>0&&Array.from(input.human_note).length<=8000,'invalid_human_note');
 check(input.producer&&typeof input.producer==='object'&&!Array.isArray(input.producer)&&uuid(input.producer.id)&&Number.isSafeInteger(input.producer.revision)&&input.producer.revision>0,'invalid_producer');
 const url=new URL(input.origin);check(['http:','https:'].includes(url.protocol)&&url.username===''&&url.password===''&&url.pathname==='/'&&url.search===''&&url.hash==='','invalid_origin');
 const origin=url.origin,nativeFetch=globalThis.fetch;check(typeof nativeFetch==='function','missing_node_fetch');
 const React=require('react'),{act}=React,{JSDOM,ResourceLoader,VirtualConsole}=require('jsdom');check(typeof act==='function','react_development_act_required');
 const priorStatePath=input.local_state_path?resolve(input.local_state_path):null;
 if(input.local_state_path!==undefined)check(typeof input.local_state_path==='string'&&input.local_state_path.length>0,'invalid_local_state_path');
 if(summary.mode==='restore')check(!!priorStatePath,'restore_requires_local_state_path');
 let diskRaw=null,disk=null;
 if(priorStatePath){try{diskRaw=await readFile(priorStatePath,'utf8');disk=JSON.parse(diskRaw);}catch(e){if(e.code!=='ENOENT')throw e;if(summary.mode==='restore')throw e;}if(summary.mode==='prepare')check(disk===null,'prepare_state_already_exists_use_restore');}
 const storage=new MemoryStorage(),session=new MemoryStorage();
 const runtimeErrors=[],responseLedger=new Map();
 class Offline extends ResourceLoader{fetch(){const error=Error('external_resource_forbidden');runtimeErrors.push(error);throw error;}}
 const virtualConsole=new VirtualConsole();virtualConsole.on('jsdomError',error=>runtimeErrors.push(error));
 window=new JSDOM('<!doctype html><html><body><main id="app"></main></body></html>',{url:origin+'/',pretendToBeVisual:true,resources:new Offline(),virtualConsole}).window;
 const authenticatedFetch=async(resource,options={})=>{
  const address=new URL(typeof resource==='string'?resource:resource.url,origin+'/');
  check(address.origin===origin&&address.pathname.startsWith('/api/'),'unexpected_http_target');
  const method=(options.method||'GET').toUpperCase();
  if(!['GET','HEAD'].includes(method)){summary.http.blocked_writes++;const error=Error('all_http_writes_forbidden');runtimeErrors.push(error);throw error;}
  const headers=new Headers(options.headers);headers.set('Cookie',input.cookie);headers.set('X-Workspace-ID',input.workspace_id);headers.set('X-CSRF-Token',input.csrf_token);
  summary.http[method]++;const response=await nativeFetch(address,{...options,method,headers,redirect:'error',cache:'no-store'});
  if(response.headers.get('Content-Type')?.includes('application/json')){try{responseLedger.set(address.pathname+address.search,await response.clone().json());}catch{}}
  return response;
 };
 const get=async path=>{const response=await authenticatedFetch(origin+'/api'+path);check(response.ok,'preflight_http_'+response.status);return response.json();};
 const account=await get('/me');check(uuid(account.user_id)&&account.workspace_id===input.workspace_id&&['admin','operator','viewer'].includes(account.role),'actual_identity_mismatch');
 if(Object.hasOwn(input.producer,'actor_id'))check(input.producer.actor_id===account.user_id,'producer_actual_actor_mismatch');
 if(Object.hasOwn(input.producer,'workspace_id'))check(input.producer.workspace_id===input.workspace_id,'producer_actual_workspace_mismatch');
 if(summary.mode==='prepare')check(['admin','operator'].includes(account.role),'prepare_requires_existing_write_role');
 const expectedStateScope={workspace_id:input.workspace_id,actor_id:account.user_id,source_draft_id:input.source_draft_id,working_draft_id:input.working_draft_id};
 if(disk){check(exact(disk,['schema','scope','human_note','working_server_document_hash','checkpoint','storage','session'])&&disk.schema==='root-current-context-local-state-v260','invalid_local_state_shape');check(JSON.stringify(disk.scope)===JSON.stringify(expectedStateScope)&&disk.human_note===input.human_note,'local_state_scope_mismatch');for(const key of ['storage','session']){check(Array.isArray(disk[key])&&disk[key].every(row=>Array.isArray(row)&&row.length===2&&row.every(v=>typeof v==='string'))&&new Set(disk[key].map(row=>row[0])).size===disk[key].length,'invalid_local_state_rows');for(const[k,v]of disk[key])(key==='storage'?storage:session).setItem(k,v);}}
 for(const[k,v]of Object.entries({'ga-setup-csrf':input.csrf_token,'ga-setup-csrf-user':account.user_id,'ga-setup-csrf-workspace':input.workspace_id}))session.setItem(k,v);
 const globals={window,document:window.document,navigator:window.navigator,HTMLElement:window.HTMLElement,Event:window.Event,localStorage:storage,sessionStorage:session,crypto:webcrypto,fetch:authenticatedFetch,IS_REACT_ACT_ENVIRONMENT:true,addEventListener:window.addEventListener.bind(window),removeEventListener:window.removeEventListener.bind(window)};
 const previous=new Map(Object.keys(globals).map(k=>[k,Object.getOwnPropertyDescriptor(globalThis,k)]));for(const[k,v]of Object.entries(globals))Object.defineProperty(globalThis,k,{value:v,writable:true,configurable:true});restoreGlobals=()=>{for(const[k,v]of previous){if(v)Object.defineProperty(globalThis,k,v);else delete globalThis[k];}};
 const tails=new Map(),locks={request(name,options,callback){check(options?.mode==='exclusive','unexpected_lock_mode');const next=(tails.get(name)||Promise.resolve()).then(callback);tails.set(name,next.catch(()=>{}));return next;}};Object.defineProperty(window.navigator,'locks',{value:locks,configurable:true});
 console.warn=(...args)=>{summary.console_warnings++;};console.error=(...args)=>{const text=args.map(v=>typeof v==='string'?v:'').join(' ');if(/not wrapped in act|not configured to support act/.test(text))summary.console_warnings++;else summary.console_errors++;};restoreConsole=()=>{console.warn=originalConsole.warn;console.error=originalConsole.error;};
 temporary=await mkdtemp(join(tmpdir(),'root-current-context-v260-'));
 const viteRequire=createRequire(require.resolve('vite/package.json')),{build}=await import(pathToFileURL(viteRequire.resolve('esbuild')).href),entry=join(temporary,'entry.jsx'),bundle=join(temporary,'production.mjs');
 await writeFile(entry,'export {default as Workspace} from '+JSON.stringify(join(project,'src/ReportBusinessCatalogueWorkspaceV212.jsx'))+';export * from '+JSON.stringify(join(project,'src/catalogue-current-context-v260-protocol.mjs'))+';export {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';',{mode:0o600});
 await build({entryPoints:[entry],outfile:bundle,bundle:true,format:'esm',platform:'node',logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},loader:{'.css':'empty'},plugins:[{name:'one-real-react',setup(builder){builder.onResolve({filter:/^react$/},()=>({path:require.resolve('react'),external:true}));}}]});await chmod(bundle,0o600);
 const code=await import(pathToFileURL(bundle).href);code.setSession(input.csrf_token,input.workspace_id,account.user_id,account.username);
 // Root supplies a real captured context. Use it only to locate/read actual
 // records, then check the component's own normal source-context GET below.
 // Do not repeat its expensive cold read before mounting the component.
 const sourceScope=code.currentCatalogueScopeV260(account,input.source_draft_id),sourceContext=code.checkedCurrentSourceV260(input.source_context,sourceScope);
 check(input.producer.id===sourceContext.source_pins.producer_id,'producer_scope_mismatch');
 const catalogue='/setup/report-business-catalogues/v211',sourceBefore=await get(catalogue+'/drafts/'+input.source_draft_id),binding=code.checkedCurrentBindingV260(await get(code.CURRENT_CATALOGUE_BASE_V260+'/working-copies/'+input.working_draft_id),sourceScope),consumerScope={actor_id:account.user_id,workspace_id:input.workspace_id,producer_id:sourceContext.source_pins.producer_id,consumer_id:sourceContext.source_pins.consumer_id,projection_hash:sourceBefore.origin.projection_hash,revision:sourceBefore.origin.revision,...(sourceBefore.origin.origin_mode==='failed_catalogue_partial_v212'?{origin_mode:sourceBefore.origin.origin_mode}:{})};
 const workingBefore=await code.checkedCurrentWorkingV260(await get(catalogue+'/drafts/'+input.working_draft_id),binding,consumerScope);
 check(code.sameV260(sourceBefore.document,input.expected_source_document)&&code.sameV260(workingBefore.document,input.expected_working_document),'expected_server_documents_mismatch');
 check(sourceBefore.id===sourceContext.source_draft_ref.id&&sourceBefore.actor_id===account.user_id&&sourceBefore.workspace_id===input.workspace_id&&sourceBefore.version===sourceContext.source_draft_ref.version&&sourceBefore.record_hash===sourceContext.source_draft_ref.record_hash&&sourceBefore.document_hash===sourceContext.source_draft_ref.document_hash&&await code.sha(code.canonical(sourceBefore.document))===sourceBefore.document_hash,'source_actual_ref_mismatch');
 check(binding.current_working_draft_ref.id===input.working_draft_id&&binding.source_draft_ref.id===input.source_draft_id&&code.sameV260(binding.source_draft_ref,sourceContext.source_draft_ref)&&code.sameV260(binding.source_pins,sourceContext.source_pins),'binding_actual_copy_mismatch');
 if(disk)check(disk.working_server_document_hash===workingBefore.document_hash,'restored_server_baseline_changed');
 const firstBusiness=workingBefore.document.businesses[0];check(firstBusiness&&typeof firstBusiness.key==='string','working_document_has_no_business');check(firstBusiness.purpose!==input.human_note,'human_note_must_differ_from_server_baseline');
 checkpoint('actual_identity_binding_and_server_documents_verified');
 const {createRoot}=await import(pathToFileURL(require.resolve('react-dom/client')).href),container=window.document.getElementById('app');let leaveGuard=null;
 const Btn=({children,primary,ghost,icon,...props})=>React.createElement('button',{type:'button',...props},children),register=(next,previous)=>{if(next)leaveGuard=next;else if(!previous||leaveGuard===previous)leaveGuard=null;};
 const settle=async()=>{await act(async()=>new Promise(resolve=>setTimeout(resolve,5)));check(runtimeErrors.length===0,'unexpected_runtime_error');check(summary.console_errors===0,'unexpected_react_console_error');};
 const until=async(predicate,label)=>{const deadline=Date.now()+300000;while(Date.now()<deadline){check(runtimeErrors.length===0,'unexpected_runtime_error');if(predicate())return;await settle();}check(false,label);};
 const button=text=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===text);
 const clickNode=async(node,label)=>{check(node&&!node.matches(':disabled'),label);await act(async()=>node.click());};
 const click=async text=>{summary.last_action=text;await until(()=>button(text)&&!button(text).matches(':disabled'),'button_unavailable');await clickNode(button(text),'button_disabled');};
 const purpose=()=>{const details=container.querySelector('[aria-label="当前业务详情"]');if(!details)return null;const label=[...details.querySelectorAll('label')].find(node=>node.textContent.trim()==='业务目的');return label?.htmlFor?window.document.getElementById(label.htmlFor):null;};
 const mount=async()=>{root=createRoot(container,{onUncaughtError:error=>runtimeErrors.push(error),onCaughtError:error=>runtimeErrors.push(error),onRecoverableError:error=>runtimeErrors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(code.Workspace,{account,producer:input.producer,models:[],Btn,storage,tabStorage:session,locks,onRegisterGuard:register}))));};
 const unmount=async()=>{if(root){const previousRoot=root;root=null;await act(async()=>previousRoot.unmount());await Promise.resolve();}};
 const selectServerDraft=async draftId=>{let offset=0;for(let page=0;page<100;page++){const list=await get(catalogue+'/drafts?'+new URLSearchParams({consumer_id:consumerScope.consumer_id,offset:String(offset),limit:'20'}));check(list.consumer_id===consumerScope.consumer_id&&list.offset===offset&&list.limit===20&&Array.isArray(list.records),'unexpected_server_draft_list');const index=list.records.findIndex(row=>row.id===draftId);if(index>=0){await click('打开第'+(offset+index+1)+'份目录');return;}check(list.has_more,'actual_draft_missing_from_server_list');await click('较早的目录');offset+=20;}check(false,'server_draft_inventory_bound_exceeded');};
 const enterOriginalEditor=async()=>{const activities=await get('/setup/report-saved-map-designs?'+new URLSearchParams({producer_id:input.producer.id}));check(activities.producer_id===input.producer.id&&Array.isArray(activities.items),'unexpected_activity_list');const rows=activities.items.filter(row=>row.generation_profile==='catalogue-v211'),index=rows.findIndex(row=>row.id===consumerScope.consumer_id);check(index>=0,'original_consumer_missing');const partial=sourceBefore.origin.origin_mode==='failed_catalogue_partial_v212';check(partial?rows[index].status==='failed':['review','needs_clarification'].includes(rows[index].status),'actual_consumer_origin_mode_mismatch');await until(()=>container.querySelectorAll('.catalogue-generation-activities > article').length===rows.length,'actual_activity_list_not_rendered');const item=container.querySelectorAll('.catalogue-generation-activities > article')[index],open=[...item.querySelectorAll('button')].find(node=>node.textContent.trim()==='查看这次活动');await clickNode(open,'actual_activity_unavailable');await click(partial?'核对部分建议':'核对并编辑这份业务目录');await until(()=>purpose()||container.querySelector('[aria-label="服务器已保存业务目录"]'),'catalogue_editor_not_ready');if(!purpose())await selectServerDraft(input.source_draft_id);await until(()=>purpose(),'original_source_draft_not_open');check(!container.querySelector('[aria-label="当前配置独立工作副本"]'),'unexpected_managed_entry_at_source');};
 const openByDiscovery=async()=>{await click('返回已有目录');await until(()=>container.querySelector('[aria-label="服务器已保存业务目录"]'),'server_draft_list_not_open');await selectServerDraft(input.working_draft_id);await until(()=>container.querySelector('[aria-label="当前配置独立工作副本"]')&&purpose(),'managed_editor_not_ready');};
 const localPurposeSaved=()=>{for(let i=0;i<storage.length;i++){const key=storage.key(i);if(!key?.startsWith('ga-catalogue-working-v260:'))continue;try{const row=JSON.parse(storage.getItem(key));if(row.schema==='report-business-catalogue-local-v211'&&row.scope.actor_id===account.user_id&&row.scope.workspace_id===input.workspace_id&&row.scope.managed_binding_id===binding.id&&row.scope.working_draft_id===input.working_draft_id&&row.draft_id===input.working_draft_id&&row.draft.document.businesses.find(b=>b.key===firstBusiness.key)?.purpose===input.human_note)return true;}catch{}}return false;};
 const stateValue=label=>({schema:'root-current-context-local-state-v260',scope:expectedStateScope,human_note:input.human_note,working_server_document_hash:workingBefore.document_hash,checkpoint:label,storage:[...storage.rows],session:[...session.rows].filter(([key])=>!key.startsWith('ga-setup-csrf'))});
 const saveState=async label=>{if(!priorStatePath||summary.mode!=='prepare')return;const raw=JSON.stringify(stateValue(label));if(diskRaw===null){await writeFile(priorStatePath,raw,{flag:'wx',mode:0o600});}else{check(await readFile(priorStatePath,'utf8')===diskRaw,'local_state_changed_by_another_process');const next=priorStatePath+'.'+randomUUID()+'.tmp';await writeFile(next,raw,{flag:'wx',mode:0o600});try{await rename(next,priorStatePath);}finally{await rm(next,{force:true});}}await chmod(priorStatePath,0o600);check((await stat(priorStatePath)).mode%512===0o600,'local_state_permissions_not_private');check(await readFile(priorStatePath,'utf8')===raw,'local_state_readback_failed');diskRaw=raw;};
 await mount();await enterOriginalEditor();await openByDiscovery();
 check(responseLedger.has('/api'+code.CURRENT_CATALOGUE_BASE_V260+'/working-copies/'+input.working_draft_id),'managed_discovery_not_read');
 check(!button('自动整理未归类要求')&&!button('确认业务目录并生成范围计划'),'legacy_write_controls_exposed');
 if(summary.mode==='prepare'){check(!purpose().matches(':disabled'),'working_purpose_not_editable');await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(purpose(),input.human_note);purpose().dispatchEvent(new window.Event('input',{bubbles:true}));});}
 await until(()=>purpose()?.value===input.human_note&&localPurposeSaved(),'managed_local_human_note_not_saved');await until(()=>leaveGuard&&leaveGuard()===true,'parent_leave_guard_not_ready');await saveState('local_human_saved');checkpoint('actual_server_discovery_and_local_human_save_verified');
 await unmount();await mount();await enterOriginalEditor();await openByDiscovery();await until(()=>purpose()?.value===input.human_note&&localPurposeSaved(),'full_parent_remount_lost_human_note');checkpoint('full_parent_remount_restored_managed_human_note');
 await click('返回原报告目录');await until(()=>purpose()&&!container.querySelector('[aria-label="当前配置独立工作副本"]'),'original_directory_return_failed');check(purpose().value===input.expected_source_document.businesses[0].purpose,'original_source_local_document_unexpectedly_changed');
 await click('在当前配置继续整理');await click('读取本人服务器副本历史');
 let historyBefore=null,historyCount=0,selectedReceipt=null;
 for(let page=0;page<100;page++){const query=new URLSearchParams({source_draft_id:input.source_draft_id});if(historyBefore)query.set('before_id',historyBefore);const history=code.checkedCurrentHistoryV260(await get(code.CURRENT_CATALOGUE_BASE_V260+'/history?'+query),sourceScope,historyBefore),index=history.items.findIndex(receipt=>receipt.outcome==='accepted'&&receipt.working_draft_ref.id===input.working_draft_id);if(index>=0){selectedReceipt=history.items[index];await click('读取副本原记录'+(historyCount+index+1));break;}check(history.has_more,'working_copy_missing_from_actor_history');historyCount+=history.items.length;historyBefore=history.next_cursor;await click('继续读取副本历史');}
 check(!!selectedReceipt,'history_inventory_bound_exceeded');await until(()=>button('打开当前配置工作副本')&&!button('打开当前配置工作副本').matches(':disabled'),'history_hydration_not_complete');
 const serverKey=code.currentCataloguePrefixV260(sourceScope)+'server:'+selectedReceipt.request_id,cached=JSON.parse(storage.getItem(serverKey)||'null');check(!!cached,'actual_history_original_not_retained');code.checkedCurrentRecoveryV260(cached,sourceScope,null,selectedReceipt);check(cached.original_raw&&await code.sha(cached.original_raw)===selectedReceipt.raw_sha256,'history_original_raw_sha_mismatch');check(container.querySelector('[aria-label="工作副本原保存回执"]')?.textContent.includes(JSON.parse(cached.original_raw).reason),'history_original_reason_not_shown');
 const originalRequestPath='/api'+code.CURRENT_CATALOGUE_BASE_V260+'/requests/'+selectedReceipt.request_id+'?'+new URLSearchParams({raw_sha256:selectedReceipt.raw_sha256});check(responseLedger.has(originalRequestPath),'history_original_request_not_read');
 await click('打开当前配置工作副本');await until(()=>container.querySelector('[aria-label="当前配置独立工作副本"]')&&purpose()?.value===input.human_note,'history_entry_lost_same_managed_human_note');checkpoint('actor_server_history_actual_original_and_copy_verified');
 const sourceContextPath='/api'+code.CURRENT_CATALOGUE_BASE_V260+'/source-context?'+new URLSearchParams({draft_id:input.source_draft_id}),actualSourceContext=code.checkedCurrentSourceV260(responseLedger.get(sourceContextPath),sourceScope);check(code.sameV260(actualSourceContext.source_draft_ref,sourceContext.source_draft_ref)&&code.sameV260(actualSourceContext.source_pins,sourceContext.source_pins)&&code.sameV260(actualSourceContext.target_configuration,sourceContext.target_configuration)&&actualSourceContext.context_hash===sourceContext.context_hash,'component_current_source_context_changed');
 const sourceAfter=await get(catalogue+'/drafts/'+input.source_draft_id),workingAfter=await get(catalogue+'/drafts/'+input.working_draft_id);check(code.sameV260(sourceAfter.document,input.expected_source_document)&&sourceAfter.version===sourceBefore.version&&sourceAfter.record_hash===sourceBefore.record_hash,'original_server_catalogue_changed');check(code.sameV260(workingAfter.document,input.expected_working_document)&&workingAfter.version===workingBefore.version&&workingAfter.record_hash===workingBefore.record_hash,'working_server_catalogue_changed');
 check(summary.http.blocked_writes===0,'component_attempted_forbidden_write');check(runtimeErrors.length===0&&summary.console_errors===0,'unexpected_component_error');await until(()=>leaveGuard&&leaveGuard()===true,'final_parent_guard_not_ready');await saveState('all_readonly_paths_verified');checkpoint('zero_http_writes_and_server_documents_unchanged');
 await unmount();summary.passed=true;summary.local_state_persisted=!!priorStatePath&&summary.mode==='prepare';summary.independent_process_restore=summary.mode==='restore';
}
try{await main();}catch(error){summary.passed=false;summary.failed_stage=stage;summary.failure_code=error.acceptanceCode||(error.code==='ENOENT'?'required_file_missing':'acceptance_operation_failed');process.exitCode=1;}finally{if(root){try{const React=require('react');await React.act(async()=>root.unmount());}catch{}}try{window?.close();}catch{}restoreConsole();restoreGlobals();if(temporary)await rm(temporary,{recursive:true,force:true});originalConsole.log(JSON.stringify(summary));}
