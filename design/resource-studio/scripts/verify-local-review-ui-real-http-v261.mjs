// Root-only offline ReactDOM + real, authenticated JSON HTTP verification.
// Authoring this file does not execute it. No browser, HTML fetch, proxy,
// screenshots, model requests, new drafts, submissions or adoption are used.
// PRIVATE_VERIFICATION_CONFIG: absolute path to a pre-existing mode-0600 JSON:
// {schema:'verify-local-review-ui-real-http-v261',origin,cookie,csrf_token,
//  account:{user_id,workspace_id},source_analysis_id,private_draft_id,
//  expected_draft:{version,value_hash,source_version,raw_hash},
//  engineering_constraints:[{citation_path,material_id,block_id,quote,
//                            inference_path?:'/businesses/0/inference_reason'}],
//  evidence_path?:absoluteNewPrivateFile,timeout_ms?:30000..900000}
// An optional PRIVATE_VERIFICATION_ALLOW_DRAFT_PUT=1 permits only PUT of the
// specified existing draft. The script never edits/clicks save, and all POSTs
// remain forbidden. Default verification requires zero server writes.
import React,{act} from 'react';
import {build} from 'vite';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {readFile,stat,open,mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,dirname,join,isAbsolute} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {createHash,randomUUID,webcrypto} from 'node:crypto';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),require=createRequire(join(project,'package.json')),reactPath=require.resolve('react'),reactURL=pathToFileURL(reactPath).href;
const nativeFetch=globalThis.fetch,UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,SHA=/^[0-9a-f]{64}$/;
const state={schema:'local-review-ui-real-http-verification-v261',run_id:randomUUID(),ok:false,stage:'initializing',checkpoints:[],http:[],mounts:[],errors:[],warnings:[],denied_writes:0,put_enabled:process.env.PRIVATE_VERIFICATION_ALLOW_DRAFT_PUT==='1',native_browser:false,new_server_save_verified:false,backend_restart_verified:false,live_source_revalidated:false,semantic_quality_verified:false};
let config,temporary,bundle,activeDOM=null;
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value),text=value=>typeof value==='string',uuid=value=>text(value)&&value.length===36&&UUID.test(value),hash=value=>text(value)&&value.length===64&&SHA.test(value);
const exact=(value,keys)=>plain(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
const same=(a,b)=>canonical(a)===canonical(b),copy=value=>JSON.parse(JSON.stringify(value));
function canonical(value){return JSON.stringify((function sort(v){return Array.isArray(v)?v.map(sort):plain(v)?Object.fromEntries(Object.keys(v).sort().map(key=>[key,sort(v[key])])):v;})(value));}
function need(value,code){if(!value){const error=Error(code);error.safeCode=code;throw error;}}
function digest(raw){return createHash('sha256').update(raw).digest('hex');}
const sleep=ms=>new Promise(done=>setTimeout(done,ms));
class Memory{constructor(){this.rows=new Map();this.reads=[];}get length(){return this.rows.size;}key(i){return [...this.rows.keys()][i]??null;}getItem(key){this.reads.push(key);return this.rows.get(key)??null;}setItem(key,value){this.rows.set(key,String(value));}removeItem(key){this.rows.delete(key);}}
function locks(){const queues=new Map();return {request(key,options,work){const next=(queues.get(key)||Promise.resolve()).then(work,work);queues.set(key,next.catch(()=>{}));return next;}};}
function checkpoint(code){state.stage=code;state.checkpoints.push({code,at:new Date().toISOString(),get_count:state.http.filter(row=>row.method==='GET').length});process.stdout.write(JSON.stringify({checkpoint:code,gets:state.http.filter(row=>row.method==='GET').length})+'\n');}
function scrub(value){if(text(value)){let result=value;for(const secret of [config?.cookie,config?.csrf_token])if(secret)result=result.split(secret).join('[redacted]');return result;}if(Array.isArray(value))return value.map(scrub);if(plain(value))return Object.fromEntries(Object.entries(value).map(([key,item])=>[key,scrub(item)]));return value;}
async function exclusiveJSON(path,value){const file=await open(path,'wx',0o600);try{await file.writeFile(JSON.stringify(scrub(value),null,2)+'\n');await file.sync();}finally{await file.close();}}

async function input(){
 const path=process.env.PRIVATE_VERIFICATION_CONFIG;need(text(path)&&isAbsolute(path),'absolute_private_config_required');const metadata=await stat(path);need(metadata.isFile()&&(metadata.mode&0o777)===0o600&&metadata.size<=1024*1024,'private_config_must_be_0600_json');
 const value=JSON.parse(await readFile(path,'utf8')),required=['schema','origin','cookie','csrf_token','account','source_analysis_id','private_draft_id','expected_draft','engineering_constraints'],optional=['evidence_path','timeout_ms'];
 need(plain(value)&&required.every(key=>Object.hasOwn(value,key))&&Object.keys(value).every(key=>required.includes(key)||optional.includes(key))&&value.schema==='verify-local-review-ui-real-http-v261','config_shape_invalid');
 const origin=new URL(value.origin);need(['http:','https:'].includes(origin.protocol)&&['127.0.0.1','localhost','[::1]'].includes(origin.hostname)&&origin.origin===value.origin&&origin.pathname==='/'&&!origin.search&&!origin.hash&&!origin.username&&!origin.password,'explicit_existing_loopback_origin_required');
 need(text(value.cookie)&&value.cookie.length>0&&!/[\r\n\u0000]/u.test(value.cookie)&&text(value.csrf_token)&&value.csrf_token.length>0&&!/[\r\n\u0000]/u.test(value.csrf_token),'session_config_invalid');
 need(exact(value.account,['user_id','workspace_id'])&&uuid(value.account.user_id)&&uuid(value.account.workspace_id)&&uuid(value.source_analysis_id)&&uuid(value.private_draft_id),'owner_scope_invalid');
 need(exact(value.expected_draft,['version','value_hash','source_version','raw_hash'])&&Number.isSafeInteger(value.expected_draft.version)&&value.expected_draft.version>=1&&Number.isSafeInteger(value.expected_draft.source_version)&&value.expected_draft.source_version>=1&&hash(value.expected_draft.value_hash)&&hash(value.expected_draft.raw_hash),'expected_draft_pins_invalid');
 need(Array.isArray(value.engineering_constraints)&&value.engineering_constraints.length>=1&&value.engineering_constraints.length<=100,'explicit_engineering_checks_required');const seen=new Set();
 for(const check of value.engineering_constraints){const keys=['citation_path','material_id','block_id','quote'];need(plain(check)&&keys.every(key=>Object.hasOwn(check,key))&&Object.keys(check).every(key=>keys.includes(key)||key==='inference_path')&&keys.every(key=>text(check[key])&&check[key].length>0)&&!seen.has(check.citation_path),'constraint_check_invalid');if(Object.hasOwn(check,'inference_path'))need(text(check.inference_path)&&/^\/(?:businesses\/(?:0|[1-9][0-9]*)(?:\/steps\/(?:0|[1-9][0-9]*))?|objects\/(?:0|[1-9][0-9]*)(?:\/fields\/(?:0|[1-9][0-9]*))?|relationships\/(?:0|[1-9][0-9]*))\/inference_reason$/u.exec(check.inference_path)?.[0]===check.inference_path,'inference_path_invalid');seen.add(check.citation_path);}
 if(value.evidence_path!==undefined){need(text(value.evidence_path)&&isAbsolute(value.evidence_path)&&resolve(value.evidence_path)!==resolve(path),'independent_evidence_path_required');try{await stat(value.evidence_path);need(false,'evidence_already_exists');}catch(error){if(error.code!=='ENOENT')throw error;}}
 if(value.timeout_ms!==undefined)need(Number.isSafeInteger(value.timeout_ms)&&value.timeout_ms>=30000&&value.timeout_ms<=900000,'timeout_invalid');return {...value,timeout_ms:value.timeout_ms??240000};
}

const observed={drafts:new Map(),sources:new Map(),lists:[],records:[],me:null};
function allowedGET(path){
 const source=config.source_analysis_id,base='/api/setup/local-review-drafts/';
 if(['/api/me','/api/setup/draft','/api/setup/understanding','/api/setup/understanding/models','/api/setup/understanding/'+source,'/api/setup/understanding/'+source+'/local-review-drafts'].includes(path))return true;
 const found=/^\/api\/setup\/local-review-drafts\/([0-9a-f-]{36})(\/source)?$/u.exec(path);
 return !!found&&(found[1]===config.private_draft_id||observed.lists.some(row=>row.id===found[1]));
}
async function apiFetch(inputValue,options={}){
 need(text(inputValue)&&inputValue.startsWith('/api/'),'normal_relative_api_path_required');const url=new URL(inputValue,config.origin);need(url.origin===config.origin&&!url.username&&!url.password&&!url.hash,'api_origin_changed');
 const method=(options.method||'GET').toUpperCase(),path=url.pathname,headers=new Headers(options.headers);
 need(!headers.has('Cookie')&&(!headers.has('X-Workspace-ID')||headers.get('X-Workspace-ID')===config.account.workspace_id)&&(!headers.has('X-CSRF-Token')||headers.get('X-CSRF-Token')===config.csrf_token),'ui_session_headers_changed');
 if(method!=='GET'){const permitted=state.put_enabled&&method==='PUT'&&path==='/api/setup/local-review-drafts/'+config.private_draft_id;if(!permitted){state.denied_writes++;need(false,'server_write_forbidden');}}
 else need(allowedGET(path),'unapproved_api_read');
 headers.set('Cookie',config.cookie);headers.set('X-Workspace-ID',config.account.workspace_id);headers.set('X-CSRF-Token',config.csrf_token);headers.set('Accept','application/json');
 const begin=Date.now(),row={method,path,query:url.search,status:null,elapsed_ms:null};state.http.push(row);
 const response=await nativeFetch(url,{...options,method,headers,credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(config.timeout_ms)});row.status=response.status;row.elapsed_ms=Date.now()-begin;
 need((response.headers.get('Content-Type')||'').split(';')[0].trim()==='application/json','json_api_response_required');const body=await response.clone().json();
 if(response.ok){
  if(path==='/api/me')observed.me=copy(body);
  else if(path==='/api/setup/understanding/'+config.source_analysis_id+'/local-review-drafts'){need(Array.isArray(body),'owner_history_not_array');observed.lists=copy(body);}
  else if(path==='/api/setup/understanding/'+config.source_analysis_id)observed.records.push(copy(body));
  else{const match=/^\/api\/setup\/local-review-drafts\/([0-9a-f-]{36})(\/source)?$/u.exec(path);if(match)(match[2]?observed.sources:observed.drafts).set(match[1],copy(body));}
 }
 return response;
}
async function getJSON(path){const response=await apiFetch(path);need(response.ok,'real_api_read_failed');return response.json();}
function expectedDraft(value){need(value.version===config.expected_draft.version&&value.value_hash===config.expected_draft.value_hash&&value.source_version===config.expected_draft.source_version&&value.raw_hash===config.expected_draft.raw_hash,'saved_private_draft_changed');}
async function compile(){
 if(bundle)return bundle;temporary=await mkdtemp(join(tmpdir(),'local-review-real-http-'));const entry=join(temporary,'entry.jsx');await writeFile(entry,'export {default as Parent} from '+JSON.stringify(join(project,'src/UnderstandingDialog.jsx'))+';export {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';export {checkedPrivateReviewDraftV261} from '+JSON.stringify(join(project,'src/LocalProposalReviewDialogV261.jsx'))+';export {inspectLocalProposalSource,selectedLocalProposalQuote} from '+JSON.stringify(join(project,'src/LocalProposalReviewV261.jsx'))+';');
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},build:{ssr:entry,write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}}),chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);need(chunk,'production_bundle_missing');const path=join(temporary,'production.mjs');await writeFile(path,chunk.code);bundle=await import(pathToFileURL(path).href);return bundle;
}

function findAria(node,label){return [...node.querySelectorAll('[aria-label]')].find(item=>item.getAttribute('aria-label')===label);}
function button(node,label){return [...node.querySelectorAll('button')].find(item=>item.textContent===label);}
function rawPath(raw,path){let value=raw;for(const key of path.split('/').slice(1)){need(value!==null&&typeof value==='object'&&Object.hasOwn(value,key),'frozen_source_pointer_missing');value=value[key];}return value;}
async function until(check,code){const end=Date.now()+config.timeout_ms;while(Date.now()<end){if(check())return;await act(async()=>sleep(25));}need(check(),code);}
async function click(node){need(node&&node.isConnected&&!node.matches(':disabled'),'requested_ui_action_unavailable');await act(async()=>node.click());}
function inferenceNotes(model){const notes=[];for(const item of model.items){if(item.raw.inference_reason)notes.push(item.raw.inference_reason);for(const child of item.collection==='businesses'?item.raw.steps:item.collection==='objects'?item.raw.fields:[])if(child.inference_reason)notes.push(child.inference_reason);}return notes;}

async function mountAndVerify(index,me,record,reference){
 state.stage='mount_'+index;const httpStart=state.http.length,errors=[],warnings=[],vc=new VirtualConsole();vc.on('jsdomError',error=>errors.push(error));class Offline extends ResourceLoader{fetch(){throw Error('External resources forbidden');}}
 const window=new JSDOM('<div id="root"></div>',{url:'https://offline-local-review.invalid/',resources:new Offline(),virtualConsole:vc}).window,prior=new Map(),storage=new Memory(),session=new Memory();Object.defineProperty(window.navigator,'locks',{value:locks(),configurable:true});window.confirm=()=>{throw Error('Native confirm forbidden');};window.fetch=apiFetch;
 for(const[key,value]of Object.entries({window,document:window.document,navigator:window.navigator,HTMLElement:window.HTMLElement,Event:window.Event,IS_REACT_ACT_ENVIRONMENT:true,localStorage:storage,sessionStorage:session,fetch:apiFetch,crypto:globalThis.crypto||webcrypto})){prior.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});}
 const originalError=console.error,originalWarn=console.warn;console.error=(...args)=>warnings.push(args.map(String).join(' '));console.warn=(...args)=>warnings.push(args.map(String).join(' '));let root,container;
 const phase={index,account_verified:true,existing_server_draft_verified:false,citations_checked:0,engineering_constraints_checked:0,edits_checked:0,inference_notes_checked:0,returned_to_original_parent:false,source_snapshot:null,private_draft_snapshot:null};state.mounts.push(phase);
 try{
  const code=await compile();code.setSession(config.csrf_token,me.workspace_id,me.user_id,me.username);const{createRoot}=await import('react-dom/client');container=document.getElementById('root');activeDOM=container;root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
  function Dialog({title,onClose,children}){return React.createElement('section',{role:'dialog','aria-modal':'true','aria-label':title},React.createElement('h1',null,title),React.createElement('button',{type:'button',onClick:onClose},'返回配置（核验窗口）'),children);}
  const Btn=({children,primary,ghost,...props})=>React.createElement('button',{type:'button',...props},children),Field=({label,children})=>React.createElement('label',null,label,children);
  let closed=0,saved=0,applied=0,prepared=0;const workspaceReply=await getJSON('/api/setup/draft');need(plain(workspaceReply.configuration)&&Number.isSafeInteger(workspaceReply.revision),'actual_configuration_unavailable');
  await act(async()=>root.render(React.createElement(code.Parent,{Dialog,Btn,Field,materials:record.materials||[],workspace:workspaceReply.configuration,account:me,initialRecord:record,onClose:()=>{closed++;},onApplied:()=>{applied++;throw Error('Adoption forbidden');},prepareDraft:()=>{prepared++;throw Error('New authority or model call forbidden');}})));
  const entryLabel=['admin','operator'].includes(me.role)?'核对并修订已保存建议（本地）':'恢复本人的核对稿（只读）';await until(()=>button(container,entryLabel),'local_review_entry_missing');await click(button(container,entryLabel));
  await until(()=>findAria(container,'本人服务器核对稿')&&!container.textContent.includes('正在读取本人已保存的核对稿与原申请'),'owner_draft_history_not_ready');
  need(container.querySelectorAll('[role="dialog"]').length===1&&container.querySelectorAll('[aria-modal="true"]').length===1,'nested_modal_detected');
  const rows=observed.lists;for(const row of rows)await code.checkedPrivateReviewDraftV261(row,me,config.source_analysis_id);const target=rows.findIndex(row=>row.id===config.private_draft_id);need(target>=0,'target_draft_not_in_visible_history');
  const history=findAria(container,'本人服务器核对稿'),openButtons=[...history.querySelectorAll('button')].filter(node=>node.textContent==='恢复这份私人核对稿');need(openButtons.length===rows.length,'owner_history_dom_mismatch');
  if(!openButtons[target].matches(':disabled'))await click(openButtons[target]);
  await until(()=>observed.sources.has(config.private_draft_id)&&findAria(container,'当前私人核对稿')&&container.textContent.includes('已核对服务器保存')&&!container.textContent.includes('正在读取本人已保存的核对稿与原申请'),'target_private_draft_not_ready');
  need(state.http.slice(httpStart).some(row=>row.method==='GET'&&row.path==='/api/setup/local-review-drafts/'+config.private_draft_id+'/source'&&row.status===200),'fresh_source_not_read_this_mount');
  const actual=await code.checkedPrivateReviewDraftV261(observed.drafts.get(config.private_draft_id),me,config.source_analysis_id);expectedDraft(actual);need(same(actual.value,reference.value),'private_draft_value_changed');const source=observed.sources.get(config.private_draft_id),model=code.inspectLocalProposalSource(source);need(same(model.ref,actual.value.source),'frozen_source_pins_changed');
  // A target GET alone is insufficient: the visible editor must display every
  // saved intent/reason/range, plus the original answer's actual citation text.
  const panel=findAria(container,'仅在本地核对原建议');need(panel,'actual_review_editor_missing');
  for(const citation of model.citations.values()){const article=findAria(panel,citation.label);need(article&&article.querySelector('blockquote')?.textContent===(citation.original.quote||'（原引用为空）'),'original_citation_not_visible');phase.citations_checked++;}
  need(phase.citations_checked>0,'original_citations_missing');
  for(const edit of actual.value.edits){
   if(edit.op==='discard_item'){const item=model.items.find(value=>value.path===edit.path);need(item&&findAria(panel,item.label),'saved_discard_not_visible');need(findAria(panel,item.label+'的修改理由')?.value===edit.reason,'saved_reason_not_visible');}
   else{const citation=model.citations.get(edit.path);need(citation,'saved_citation_pointer_missing');need(findAria(panel,citation.label+'的修改理由')?.value===edit.reason&&findAria(panel,citation.label+'的起始字符')?.value===edit.start_text&&findAria(panel,citation.label+'的结束字符')?.value===edit.end_text,'saved_citation_intent_not_visible');const replacement=code.selectedLocalProposalQuote(source,edit);need(findAria(panel,citation.label+'的新引用预览')?.textContent===replacement.quote,'saved_replacement_not_visible');}
   phase.edits_checked++;
  }
  for(const check of config.engineering_constraints){
   const citation=model.citations.get(check.citation_path),edit=actual.value.edits.find(value=>value.op==='replace_citation'&&value.path===check.citation_path);need(citation&&edit,'engineering_saved_replacement_missing');const replacement=code.selectedLocalProposalQuote(source,edit);need(same(replacement,{material_id:check.material_id,block_id:check.block_id,quote:check.quote}),'engineering_saved_selection_changed');const block=model.blocks.get(JSON.stringify([check.material_id,check.block_id]));need(block&&block.text.includes(check.quote),'engineering_quote_not_in_authorized_block');
   const article=findAria(panel,citation.label);need(article?.querySelector('blockquote')?.textContent===(citation.original.quote||'（原引用为空）')&&findAria(article,citation.label+'的新引用预览')?.textContent===check.quote&&findAria(article,citation.label+'的只读授权原文')?.value===block.text,'engineering_original_and_replacement_not_separate');
   const select=findAria(article,citation.label+'的授权段落');need(select?.value===JSON.stringify([check.material_id,check.block_id])&&select.selectedOptions[0]?.textContent===block.material_name+' · 第'+block.ordinal+'段','engineering_source_label_mismatch');
   if(check.inference_path){const note=rawPath(source.raw_candidate,check.inference_path);need(text(note)&&note.length>0&&[...panel.querySelectorAll('p')].some(node=>node.textContent==='原推断说明：'+note),'engineering_inference_not_separately_labelled');}
   phase.engineering_constraints_checked++;
  }
  const notes=inferenceNotes(model),counts=new Map();for(const note of notes)counts.set(note,(counts.get(note)||0)+1);for(const[note,count]of counts)need([...panel.querySelectorAll('p')].filter(node=>node.textContent==='原推断说明：'+note).length===count,'saved_inference_note_missing');phase.inference_notes_checked=notes.length;
  need(panel.textContent.includes('不调用云、不应用配置，也不授权执行')&&panel.textContent.includes('引用存在不代表业务、对象关系或能力权限已通过核对'),'authority_boundary_notice_missing');need(!panel.querySelector('pre'),'raw_json_debug_view_detected');
  phase.source_snapshot=copy(source);phase.private_draft_snapshot=copy(actual);phase.dom={text:container.textContent,html:container.innerHTML};phase.existing_server_draft_verified=true;
  await click(button(container,'返回原建议'));await until(()=>findAria(container,'核对资料理解建议')&&button(container,entryLabel),'original_parent_return_failed');need(storage.reads.includes('ga-setup-review:'+config.source_analysis_id),'parent_not_bound_to_original_source');need(saved===0&&applied===0&&prepared===0&&closed===0,'unexpected_parent_action');phase.returned_to_original_parent=true;
  need(errors.length===0&&warnings.length===0,'reactdom_diagnostic_reported');phase.fresh_empty_local_store=true;checkpoint('mount_'+index+'_existing_private_draft_verified');return actual;
 }catch(error){if(container)phase.failure_dom={text:container.textContent,html:container.innerHTML};throw error;}
 finally{try{if(root)await act(async()=>root.unmount());}finally{phase.react_errors=errors.map(error=>({name:error.name,message:error.message,stack:error.stack}));phase.console_warnings=warnings;activeDOM=null;console.error=originalError;console.warn=originalWarn;window.close();for(const[key,descriptor]of prior){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}}
}

try{
 need(process.env.NODE_ENV==='development','development_reactdom_required');need(typeof nativeFetch==='function','native_fetch_unavailable');config=await input();checkpoint('private_configuration_loaded');
 const me=await getJSON('/api/me');need(me.user_id===config.account.user_id&&me.workspace_id===config.account.workspace_id&&text(me.role),'actual_owner_identity_mismatch');
 const record=await getJSON('/api/setup/understanding/'+config.source_analysis_id);need(record.id===config.source_analysis_id&&record.status==='failed'&&[4,5].includes(record.contract_version)&&record.local_review_available===true&&record.raw_response_hash===config.expected_draft.raw_hash,'original_failed_source_not_available');
 const reference=await getJSON('/api/setup/local-review-drafts/'+config.private_draft_id);expectedDraft(reference);need(reference.owner_id===me.user_id&&reference.workspace_id===me.workspace_id&&reference.source_id===config.source_analysis_id,'private_draft_scope_mismatch');checkpoint('real_owner_source_and_existing_draft_read');
 const first=await mountAndVerify(1,me,record,reference);const refreshed=await getJSON('/api/setup/local-review-drafts/'+config.private_draft_id);expectedDraft(refreshed);need(same(refreshed.value,first.value),'server_draft_changed_between_mounts');await mountAndVerify(2,me,record,refreshed);
 const final=await getJSON('/api/setup/local-review-drafts/'+config.private_draft_id);expectedDraft(final);need(same(final.value,reference.value),'server_human_draft_changed');need(state.denied_writes===0&&state.http.every(row=>row.method==='GET'),'verification_must_remain_read_only');state.ok=true;checkpoint('full_parent_remount_restored_same_server_draft');
}catch(error){state.errors.push({name:error.name,code:error.safeCode||'verification_failed',message:error.message,stack:error.stack});state.stage='failed';if(activeDOM)state.failure_dom={text:activeDOM.textContent,html:activeDOM.innerHTML};process.exitCode=1;}
finally{
 if(temporary)await rm(temporary,{recursive:true,force:true});
 if(config?.evidence_path)try{await exclusiveJSON(config.evidence_path,state);}catch(error){state.ok=false;state.errors.push({name:error.name,code:'private_evidence_write_failed'});process.exitCode=1;}
 const summary={ok:state.ok,layer:'offline_reactdom_real_json_http',mounts:state.mounts.length,verified_mounts:state.mounts.filter(phase=>phase.existing_server_draft_verified&&phase.returned_to_original_parent).length,gets:state.http.filter(row=>row.method==='GET').length,writes:state.http.filter(row=>row.method!=='GET').length,denied_writes:state.denied_writes,citations_checked:state.mounts.reduce((total,phase)=>total+phase.citations_checked,0),engineering_checks:state.mounts.reduce((total,phase)=>total+phase.engineering_constraints_checked,0),edits_checked:state.mounts.reduce((total,phase)=>total+phase.edits_checked,0),native_browser:false,new_server_save_verified:false,live_source_revalidated:false,semantic_quality_verified:false,failure_code:state.ok?null:state.errors[0]?.code||'verification_failed'};
 process.stdout.write(JSON.stringify(summary)+'\n');
}
