// Root alone runs this with its existing private input. The author does not
// execute it or read that input. Offline ReactDOM + real JSON HTTP only: no
// browser, server writes, fabricated responses, or source/admission claims.
import {readFile,writeFile,rename,link,chmod,stat,mkdtemp,rm} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {tmpdir} from 'node:os';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {webcrypto,randomUUID} from 'node:crypto';

const require=createRequire(new URL('../package.json',import.meta.url)),project=resolve(fileURLToPath(new URL('..',import.meta.url)));
const originalConsole={log:console.log,error:console.error,warn:console.warn};
const summary={schema:'root-current-saved-history-acceptance-v260',surface:'offline_reactdom_with_real_json_http',passed:false,checkpoints:[],http_gets:0,blocked_http_writes:0,local_storage_write_attempts:0,mounts:0,claims:0,source_quotes:0,current_business_names:0,console_errors:0,console_warnings:0,console_logs:0,native_browser_verified:false,full_parent_verified:false,source_revalidated:false,files_revalidated:false,current_configuration_revalidated:false,semantic_quality_verified:false,complete_operational_inventory_checked:false,can_apply:false,can_publish:false,original_request_settled:false};
const check=(ok,code)=>{if(!ok){const error=Error(code);error.acceptanceCode=code;throw error;}};
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const exact=(v,fields)=>object(v)&&Object.keys(v).sort().join('|')===fields.split(' ').sort().join('|');
const uuid=v=>typeof v==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(v);
const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
const safe=(v,fallback)=>typeof v==='string'&&/^[A-Za-z0-9_][A-Za-z0-9_.:-]{0,99}$/.test(v)?v:fallback;
const privateError=e=>e?{name:String(e.name||'Error'),code:e.code??null,acceptance_code:e.acceptanceCode??null,message:String(e.message??e),stack:typeof e.stack==='string'?e.stack:null,detail:e.detail??null}:null;
let stage='input',outputPath=null,diagnosticPath=null,outputAuthorized=false,temporary=null,root=null,window=null,restoreGlobals=()=>{},restoreConsole=()=>{},diagnosticFailure=null,finalizing=false,failureSnapshot=null,failureError=null;
let diagnosticReader=()=>({stage,summary:{...summary}}),queue=Promise.resolve();const ownedFiles=new Map(),httpTrace=[],runtimeErrors=[],consoleTrace=[];
async function writePrivate(path,value){
 const raw=JSON.stringify(value),temp=path+'.'+randomUUID()+'.tmp';await writeFile(temp,raw,{flag:'wx',mode:0o600});
 try{if(!ownedFiles.has(path))await link(temp,path);else{check(await readFile(path,'utf8')===ownedFiles.get(path),'private_output_changed_externally');await rename(temp,path);}await chmod(path,0o600);check((await stat(path)).mode%512===0o600&&await readFile(path,'utf8')===raw,'private_output_readback_failed');ownedFiles.set(path,raw);}finally{await rm(temp,{force:true});}
}
function progress(phase='in_progress',error=null){
 if(!outputAuthorized||finalizing&&phase==='in_progress')return queue;
 queue=queue.then(async()=>{await writePrivate(outputPath,{...summary,phase});await writePrivate(diagnosticPath,{schema:'root-current-saved-history-diagnostic-v260',is_recovery_checkpoint:false,phase,...(failureSnapshot||diagnosticReader()),error:privateError(error||failureError)});}).catch(error=>{diagnosticFailure=error;});return queue;
}
function checkpoint(label){stage=label;summary.checkpoints.push(label);void progress();}
class StorageTrap{
 get length(){return 0;}key(){return null;}getItem(){return null;}
 fail(){summary.local_storage_write_attempts++;throw Error('saved_history_must_not_write_localStorage');}
 setItem(){this.fail();}removeItem(){this.fail();}clear(){this.fail();}
}
class SessionMemory{
 constructor(){this.rows=new Map();}get length(){return this.rows.size;}key(i){return [...this.rows.keys()][i]??null;}getItem(key){return this.rows.get(String(key))??null;}setItem(key,value){this.rows.set(String(key),String(value));}removeItem(key){this.rows.delete(String(key));}clear(){this.rows.clear();}
}
async function main(){
 check(process.env.NODE_ENV==='development','development_react_required');
 const inputPath=process.env.GA_V260_ALLOCATION_ACCEPTANCE_INPUT,output=process.env.GA_V260_SAVED_HISTORY_OUTPUT;
 check(typeof inputPath==='string'&&inputPath.length>0&&typeof output==='string'&&output.length>0,'explicit_input_and_new_output_required');
 outputPath=resolve(output);diagnosticPath=outputPath+'.diagnostic.private.json';check(outputPath!==resolve(inputPath)&&diagnosticPath!==resolve(inputPath),'output_must_not_replace_input');
 const input=JSON.parse(await readFile(resolve(inputPath),'utf8')),hasApplication=Object.hasOwn(input,'expected_application');
 check(exact(input,'schema origin cookie csrf_token account producer source_draft_id working_draft_id binding accepted_original consumer_id human_note local_state_path'+(hasApplication?' expected_application':''))&&input.schema==='root-current-allocation-acceptance-v260','invalid_existing_input_shape');
 check(typeof input.local_state_path==='string'&&outputPath!==resolve(input.local_state_path)&&diagnosticPath!==resolve(input.local_state_path),'output_must_not_replace_old_checkpoint');
 outputAuthorized=true;
 check(object(input.account)&&uuid(input.account.user_id)&&uuid(input.account.workspace_id)&&['admin','operator','viewer'].includes(input.account.role)&&object(input.producer)&&uuid(input.producer.id)&&uuid(input.working_draft_id)&&uuid(input.consumer_id),'invalid_saved_history_scope');
 check(object(input.binding)&&uuid(input.binding.id)&&input.binding.actor_id===input.account.user_id&&input.binding.workspace_id===input.account.workspace_id&&input.binding.current_working_draft_ref?.id===input.working_draft_id&&input.binding.source_pins?.producer_id===input.producer.id,'input_binding_scope_mismatch');
 check(exact(input.accepted_original,'raw sha256')&&typeof input.accepted_original.raw==='string'&&hash(input.accepted_original.sha256),'invalid_actual_original_hash_capture');
 check(typeof input.cookie==='string'&&input.cookie.length>0&&!/[\r\n]/.test(input.cookie)&&typeof input.csrf_token==='string'&&input.csrf_token.length>0,'invalid_actual_auth');
 const url=new URL(input.origin);check(['http:','https:'].includes(url.protocol)&&url.pathname==='/'&&!url.search&&!url.hash&&!url.username&&!url.password,'invalid_same_origin');
 const origin=url.origin,workspaceId=input.account.workspace_id,nativeFetch=globalThis.fetch,BASE='/setup/report-saved-map-designs',draftPath='/api/setup/report-business-catalogues/v211/drafts/'+input.working_draft_id,indexPath='/api'+BASE+'/saved-records/by-producer/'+input.producer.id,recordPath='/api'+BASE+'/'+input.consumer_id+'/saved-record';check(typeof nativeFetch==='function','native_fetch_unavailable');
 const ledger=new Map(),storage=new StorageTrap(),session=new SessionMemory();
 diagnosticReader=()=>({stage,last_action:summary.last_action||null,summary:{...summary},http:httpTrace.map(row=>({...row})),runtime_errors:runtimeErrors.map(privateError),console:consoleTrace.map(row=>({...row})),dom_text:window?.document?.getElementById('app')?.textContent??null});
 await progress();check(!diagnosticFailure&&ownedFiles.has(outputPath)&&ownedFiles.has(diagnosticPath),'exclusive_private_output_unavailable');
 const authenticatedFetch=async(resource,options={})=>{
  const address=new URL(typeof resource==='string'?resource:resource.url,origin+'/'),method=(options.method||'GET').toUpperCase();
  if(method!=='GET'||options.body!==undefined){summary.blocked_http_writes++;throw Error('all_http_writes_forbidden');}
  check(address.origin===origin&&!address.username&&!address.password,'unexpected_http_origin');
  const allowed=address.pathname==='/api/me'&&!address.search||address.pathname===draftPath&&!address.search||address.pathname===recordPath&&!address.search||address.pathname===indexPath;
  check(allowed,'http_endpoint_outside_saved_history_allowlist');
  if(address.pathname===indexPath){const q=address.searchParams;check([...q.keys()].sort().join('|')==='binding_id|limit|offset'&&q.get('binding_id')===input.binding.id&&q.get('limit')==='20'&&/^(0|[1-9][0-9]*)$/.test(q.get('offset')||'')&&Number(q.get('offset'))%20===0,'unexpected_saved_index_query');}
  const headers=new Headers(options.headers);for(const[k,value]of [['X-Workspace-ID',workspaceId],['X-CSRF-Token',input.csrf_token],['Cookie',input.cookie]]){if(headers.has(k))check(headers.get(k)===value,'actual_auth_header_mismatch');headers.set(k,value);}headers.set('Accept','application/json');
  const trace={method,path:address.pathname+address.search,status:null,started_at:new Date().toISOString(),completed_at:null,json:null,error:null};httpTrace.push(trace);summary.http_gets++;void progress();
  let response;try{response=await nativeFetch(address,{...options,method,headers,redirect:'error',cache:'no-store'});trace.status=response.status;check((response.headers.get('Content-Type')||'').toLowerCase().includes('application/json'),'non_json_http_response_forbidden');trace.json=await response.clone().json();ledger.set(trace.path,trace.json);trace.completed_at=new Date().toISOString();}catch(error){trace.error=privateError(error);trace.completed_at=new Date().toISOString();void progress();throw error;}void progress();return response;
 };
 const request=async(path,options={})=>{check(typeof path==='string'&&path.startsWith('/')&&!path.startsWith('//'),'invalid_component_path');const response=await authenticatedFetch(origin+(path.startsWith('/api/')?path:'/api'+path),options),value=await response.json();if(!response.ok){const error=Error('actual_http_'+response.status);error.status=response.status;error.detail=value.detail;throw error;}return value;};
 const account=await request('/me');check(account.user_id===input.account.user_id&&account.workspace_id===workspaceId&&account.role===input.account.role,'actual_identity_mismatch');
 const React=require('react'),{act}=React,{JSDOM,ResourceLoader,VirtualConsole}=require('jsdom');check(typeof act==='function','development_act_unavailable');
 class Offline extends ResourceLoader{fetch(){const error=Error('external_resource_forbidden');runtimeErrors.push(error);throw error;}}
 const vc=new VirtualConsole();vc.on('jsdomError',error=>runtimeErrors.push(error));window=new JSDOM('<!doctype html><html><body><main id="app"></main></body></html>',{url:origin+'/',pretendToBeVisual:true,resources:new Offline(),virtualConsole:vc}).window;
 Object.defineProperty(window,'localStorage',{value:storage,configurable:true});Object.defineProperty(window,'sessionStorage',{value:session,configurable:true});
 const globals={window,document:window.document,navigator:window.navigator,HTMLElement:window.HTMLElement,Event:window.Event,crypto:webcrypto,localStorage:storage,sessionStorage:session,fetch:authenticatedFetch,IS_REACT_ACT_ENVIRONMENT:true},prior=new Map(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});restoreGlobals=()=>{for(const[key,value]of prior){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}};
 console.log=(...args)=>{summary.console_logs++;consoleTrace.push({kind:'log',text:args.map(String).join(' ')});};console.warn=(...args)=>{summary.console_warnings++;consoleTrace.push({kind:'warning',text:args.map(String).join(' ')});};console.error=(...args)=>{const text=args.map(String).join(' ');if(/not wrapped in act|not configured to support act/.test(text))summary.console_warnings++;else summary.console_errors++;consoleTrace.push({kind:'error',text});};restoreConsole=()=>Object.assign(console,originalConsole);
 temporary=await mkdtemp(join(tmpdir(),'root-current-saved-history-v260-'));const entry=join(temporary,'entry.jsx'),out=join(temporary,'production.mjs'),viteRequire=createRequire(require.resolve('vite/package.json')),{build}=await import(pathToFileURL(viteRequire.resolve('esbuild')).href);
 await writeFile(entry,'export {default as History} from '+JSON.stringify(join(project,'src/CatalogueCurrentSavedHistoryV260.jsx'))+';export * from '+JSON.stringify(join(project,'src/catalogue-current-saved-history-v260.mjs'))+';export {canonical,sha} from '+JSON.stringify(join(project,'src/catalogue-current-allocation-v260-protocol.mjs'))+';export {checkedDocument} from '+JSON.stringify(join(project,'src/report-business-catalogue-v211-protocol.mjs'))+';export {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';',{mode:0o600});
 await build({entryPoints:[entry],outfile:out,bundle:true,format:'esm',platform:'node',logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},loader:{'.css':'empty'},plugins:[{name:'one-real-react',setup(b){b.onResolve({filter:/^react$/},()=>({path:require.resolve('react'),external:true}));}}]});await chmod(out,0o600);
 const code=await import(pathToFileURL(out).href);code.setSession(input.csrf_token,workspaceId,account.user_id,account.username);
 check(await code.sha(input.accepted_original.raw)===input.accepted_original.sha256,'actual_original_bytes_hash_mismatch');
 const originalBody=JSON.parse(input.accepted_original.raw);check(originalBody.generation_profile==='catalogue-current-allocation-v260'&&uuid(originalBody.request_id)&&originalBody.binding_id===input.binding.id,'actual_original_binding_mismatch');
 const scope=code.currentSavedHistoryScopeV260(account,input.producer.id,input.binding.id),readWorking=async()=>{const row=await request(draftPath);check(row?.schema==='report-business-catalogue-draft-view-v211'&&row.id===input.working_draft_id&&row.workspace_id===workspaceId&&row.actor_id===account.user_id&&Number.isSafeInteger(row.version)&&row.version>=1&&hash(row.document_hash)&&Number.isSafeInteger(row.origin?.input_count),'actual_working_document_scope_mismatch');code.checkedDocument(row.document,row.origin.input_count);check(await code.sha(code.canonical(row.document))===row.document_hash,'actual_working_document_hash_mismatch');return row;};
 const working=await readWorking();checkpoint('actual_identity_original_hash_and_current_business_mapping_read');
 const container=document.getElementById('app'),{createRoot}=require('react-dom/client'),Btn=({children,...props})=>React.createElement('button',{type:'button',...props},children);
 const settle=async()=>{await act(async()=>new Promise(done=>setTimeout(done,20)));check(runtimeErrors.length===0&&summary.console_errors===0,'unexpected_component_runtime_error');check(!diagnosticFailure,'private_diagnostic_write_failed');};
 const until=async(predicate,code)=>{const deadline=Date.now()+900000;while(Date.now()<deadline){check(runtimeErrors.length===0&&summary.console_errors===0,'unexpected_component_runtime_error');check(!container.querySelector('[role="alert"]'),'saved_history_component_reported_read_error');if(predicate())return;await settle();}check(false,code);};
 const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label),click=async label=>{summary.last_action=label;await progress();await until(()=>button(label)&&!button(label).matches(':disabled'),'saved_history_action_not_ready');await act(async()=>button(label).click());};
 const indexKey=offset=>indexPath+'?'+new URLSearchParams({binding_id:input.binding.id,offset:String(offset),limit:'20'});
 const mount=async businesses=>{summary.mounts++;for(const key of [...ledger.keys()])if(key.startsWith(indexPath+'?')||key===recordPath)ledger.delete(key);root=createRoot(container,{onUncaughtError:error=>runtimeErrors.push(error),onCaughtError:error=>runtimeErrors.push(error),onRecoverableError:error=>runtimeErrors.push(error)});await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(code.History,{account,producerId:input.producer.id,bindingId:input.binding.id,businesses,api:request,Btn}))));};
 const unmount=async()=>{if(root){const previous=root;root=null;await act(async()=>previous.unmount());check(container.textContent==='','component_unmount_left_visible_history');}};
 const inspect=async()=>{
  let offset=0,index,row,indexPosition;
  for(let page=0;page<100;page++){
   await until(()=>ledger.has(indexKey(offset))&&container.textContent.includes('第'+(offset/20+1)+'页 · 本页')&&button('返回最新记录')&&!button('返回最新记录').matches(':disabled'),'actual_saved_index_not_rendered');
   index=code.checkedCurrentSavedIndexV260(ledger.get(indexKey(offset)),scope,{offset,limit:20});indexPosition=index.records.findIndex(item=>item.consumer_id===input.consumer_id);if(indexPosition>=0){row=index.records[indexPosition];break;}check(index.has_more,'actual_consumer_not_in_owned_saved_index');await click('下一页历史');offset+=20;
  }
  check(row&&['review','needs_clarification'].includes(row.status),'actual_saved_consumer_not_readable');check(row.request_sha256===input.accepted_original.sha256,'saved_index_not_the_actual_accepted_original');
  await click('查看第'+(offset+indexPosition+1)+'项已保存建议');await until(()=>ledger.has(recordPath)&&container.querySelector('[aria-label="已保存机答与原文依据"]'),'actual_saved_record_not_rendered');
  const record=await code.checkedCurrentSavedRecordV260(ledger.get(recordPath),scope,row),projection=record.answer.projection;
  check(projection.decisions.length===20,'expected_actual_twenty_claims_missing');let quotes=0,names=0;
  for(const decision of projection.decisions){
   const article=container.querySelector('[aria-label="报告要求第'+(decision.input_position+1)+'条的已保存建议"]');check(article&&article.textContent.includes(decision.claim.statement)&&article.textContent.includes(decision.raw_decision.reason),'saved_claim_or_reason_missing_from_dom');
   const blocks=[...article.querySelectorAll('blockquote')];check(blocks.length===decision.source_spans.length,'saved_source_quote_count_mismatch');for(const[i,span]of decision.source_spans.entries()){check(blocks[i].querySelector(':scope > p')?.textContent===span.record.text,'complete_original_quote_missing');check(blocks[i].querySelector('details summary')?.textContent==='查看引用位置','saved_source_position_not_disclosed');quotes++;}
   const mapped=decision.raw_decision.business_keys.map(key=>working.document.businesses.find(b=>b.key===key)?.name).filter(name=>typeof name==='string'&&name.trim());for(const name of mapped){check(article.textContent.includes(name)&&article.textContent.includes('当前工作稿中的对应业务'),'current_business_name_mapping_missing');names++;}if(mapped.length)check(article.textContent.includes('不能据此认定为当时的业务名称'),'current_name_misrepresented_as_historical_name');
  }
  const technical=new Set([input.consumer_id,input.binding.id,input.producer.id,...projection.decisions.flatMap(d=>d.raw_decision.business_keys)]),primary=[...container.querySelectorAll('h1,h2,h3,h4,h5,h6,button,summary')].map(node=>node.textContent.trim());check(primary.every(label=>!technical.has(label)),'internal_identifier_used_as_primary_label');
  check(container.querySelectorAll('pre,code,textarea,input,select').length===0,'unexpected_debug_or_edit_surface');check([...container.querySelectorAll('button')].every(node=>!/(确认费用|开始本批|采纳|发布|执行|保存到服务器)/.test(node.textContent)),'unexpected_write_action');
  check(container.textContent.includes('查看的是已保存结果；采纳前仍需重新核对当前资料'),'saved_only_boundary_missing');
  summary.claims=projection.decisions.length;summary.source_quotes=quotes;summary.current_business_names=names;return {record,offset};
 };
 stage='first_mount';await mount(working.document.businesses);const first=await inspect();checkpoint('first_mount_actual_index_saved_record_all_claims_quotes_and_current_names_verified');
 await unmount();stage='second_mount';const workingAgain=await readWorking();check(workingAgain.version===working.version&&workingAgain.document_hash===working.document_hash,'working_business_mapping_changed_between_mounts');await mount(workingAgain.document.businesses);const second=await inspect();check(second.offset===first.offset&&code.canonical(second.record)===code.canonical(first.record),'remounted_saved_record_or_page_changed');checkpoint('full_unmount_remount_read_same_actual_page_and_saved_record');await unmount();
 check(summary.blocked_http_writes===0&&summary.local_storage_write_attempts===0&&runtimeErrors.length===0&&summary.console_errors===0,'unexpected_write_storage_or_runtime_error');check(httpTrace.every(row=>row.method==='GET'&&row.status===200),'http_read_not_successful');summary.passed=true;checkpoint('zero_http_writes_zero_localstorage_writes_no_fullview_or_source_gate_reads');
}

try{await main();}catch(error){summary.passed=false;summary.failed_stage=stage;summary.error_name=safe(error?.name,'Error');summary.error_code=safe(error?.acceptanceCode||error?.code,'acceptance_operation_failed');process.exitCode=1;failureError=error;failureSnapshot=diagnosticReader();finalizing=true;await progress('failed',error);}finally{
 if(root)try{const React=require('react');await React.act(async()=>root.unmount());}catch(error){summary.passed=false;summary.cleanup_error=safe(error.code,'component_cleanup_failed');process.exitCode=1;}
 try{window?.close();}catch{}restoreConsole();restoreGlobals();if(temporary)try{await rm(temporary,{recursive:true,force:true});}catch{summary.passed=false;summary.cleanup_error='temporary_cleanup_failed';process.exitCode=1;}
 finalizing=true;await progress(summary.passed?'passed':'failed');await queue;if(diagnosticFailure){summary.passed=false;summary.output_error=safe(diagnosticFailure.acceptanceCode||diagnosticFailure.code,'private_output_failed');process.exitCode=1;}originalConsole.log(JSON.stringify(summary));
}
