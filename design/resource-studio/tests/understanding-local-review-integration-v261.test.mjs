// True ReactDOM integration of the production UnderstandingDialog and its real
// embedded private-draft container. All HTTP/store records here are synthetic;
// this is not evidence of actual server durability, model quality or a browser.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import React,{act} from 'react';
import {createHash,randomUUID,webcrypto} from 'node:crypto';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {build} from 'vite';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';

const project=resolve(fileURLToPath(new URL('..',import.meta.url))),require=createRequire(join(project,'package.json')),reactPath=require.resolve('react'),reactURL=pathToFileURL(reactPath).href;
const clone=value=>JSON.parse(JSON.stringify(value)),stable=value=>JSON.stringify((function normalize(v){return Array.isArray(v)?v.map(normalize):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(key=>[key,normalize(v[key])])):v;})(value)),digest=value=>createHash('sha256').update(stable(value)).digest('hex'),sha=raw=>createHash('sha256').update(raw).digest('hex'),hash=letter=>letter.repeat(64);
const account={user_id:'a1111111-1111-4111-8111-111111111111',workspace_id:'b2222222-2222-4222-8222-222222222222',username:'synthetic-reviewer',role:'operator'},analysisID='c3333333-3333-4333-8333-333333333333',draftID='d4444444-4444-4444-8444-444444444444',derivedID='e5555555-5555-4555-8555-555555555555';
const entry='核对并修订已保存建议（本地）',viewerEntry='恢复本人的核对稿（只读）',submitLabel='另存为待核对建议（仅本地）',reason='原文不足以支持这项业务，明确舍弃整项';
const gate=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};},sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
class Memory{constructor(){this.rows=new Map();this.reads=[];}get length(){return this.rows.size;}key(i){return [...this.rows.keys()][i]??null;}getItem(key){this.reads.push(key);return this.rows.get(key)??null;}setItem(key,value){this.rows.set(key,String(value));}removeItem(key){this.rows.delete(key);}}
function lockManager(){const queues=new Map();return {request(key,options,work){const next=(queues.get(key)||Promise.resolve()).then(work,work);queues.set(key,next.catch(()=>{}));return next;}};}
const workspace=()=>({instanceProfile:{name:'合成核查配置',domain:'合成资料核查',goal:'保留人工判断'},instanceObjects:[],businesses:[],flows:{},materials:[]});
function business(name){return {operation:'upsert',original_name:'',name,trigger:'由人工明确提出',output:'待核对的业务说明',steps:[],citations:[],inference_reason:'合成建议，仅验证页面与保存接缝，不代表业务正确。'};}
function source(){const raw={domain:'合成资料核查',objects:[],businesses:[business('待舍弃的不可靠业务'),business('保留人工核查业务')],questions:[]};return {analysis_id:analysisID,source_version:7,contract_version:4,raw_hash:hash('a'),raw_candidate:raw,source_blocks:[{material_id:'synthetic-material',block_id:'synthetic-block',material_name:'合成授权材料',ordinal:1,text:'合成的完整授权段落，保留人工核查，不授权设备执行。'}],item_hashes:{'/businesses/0':digest(raw.businesses[0]),'/businesses/1':digest(raw.businesses[1])},citation_hashes:{}};}
function originalRecord(){return {id:analysisID,version:7,status:'failed',created_at:'2026-10-31T00:00:00+00:00',provider:'qwen',model:'synthetic-saved-answer',source_revision:35,materials:[],completed_batches:0,total_batches:1,error:'合成资料引用核对未通过，已保存原回答。',result:null,result_hash:null,applied_revision:null,receipts:[],review_selection:{names:[],object_names:[],accept_domain:false},before_context:{businesses:[],flows:{},object_names:[]},contract_version:4,raw_response_hash:hash('a'),local_review_available:true,recovery:{mode:'local_only',reason:'原回答已保存，可本地核对，不再调用云。'},citation_normalizations:[],apply_basis:{revision:35,configuration_hash:hash('b')}};}
function privateDraft(s){const value={source:{analysis_id:s.analysis_id,source_version:s.source_version,raw_hash:s.raw_hash,contract_version:s.contract_version},edits:[]};return {schema:'local-proposal-review-draft-v261',id:draftID,version:1,workspace_id:account.workspace_id,owner_id:account.user_id,source_id:analysisID,source_version:s.source_version,raw_hash:s.raw_hash,open_request_id:'f6666666-6666-4666-8666-666666666666',status:'editing',created_at:'2026-10-31T00:00:00+00:00',updated_at:'2026-10-31T00:00:00+00:00',value,value_hash:digest(value),source_state:'not_revalidated',can_submit:false,local_submission_available:true};}
function fixture(){
 const f={source:source(),original:originalRecord(),calls:[],unexpected:[],role:'operator',revision:35,saves:new Map(),attempts:new Map(),sourceGate:null,saveGate:null,lostSave:false,saveLookupUnavailable:false,derived:null};f.row=privateDraft(f.source);
 const history='/api/setup/understanding/'+analysisID+'/local-review-drafts',base='/api/setup/local-review-drafts/'+draftID;
 const saveReply=receipt=>({schema:'local-proposal-review-draft-save-v261',save_receipt:clone(receipt),current_draft:clone(f.row),current_matches:f.row.version===receipt.version_after&&f.row.value_hash===receipt.value_hash});
 const response=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 f.fetch=async(input,options={})=>{
  const u=new URL(typeof input==='string'?input:input.url,'https://integration.invalid'),path=u.pathname,method=options.method||'GET',headers=new Headers(options.headers),call={path,search:u.search,method,raw:options.body??null};f.calls.push(call);
  assert.equal(options.credentials,'same-origin');assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),'synthetic-parent-session');
  if(path==='/api/me'&&method==='GET')return response({...account,role:f.role});
  if(path==='/api/setup/understanding/models'&&method==='GET')return response([]);
  if(path==='/api/setup/understanding'&&method==='GET')return response(f.derived?[clone(f.derived),clone(f.original)]:[clone(f.original)]);
  if(path==='/api/setup/understanding/'+analysisID&&method==='GET')return response(f.original);
  if(path==='/api/setup/understanding/'+derivedID&&method==='GET'&&f.derived)return response(f.derived);
  if(path==='/api/setup/draft'&&method==='GET')return response({revision:f.revision,configuration:workspace()});
  if(path===history&&method==='GET')return response([clone(f.row)]);
  if(path===base&&method==='GET')return response(f.row);
  if(path===base+'/source'&&method==='GET'){if(f.sourceGate)await f.sourceGate.promise;return response(f.source);}
  if(path.startsWith(base+'/saves/by-request/')&&method==='GET'){
   if(f.saveLookupUnavailable)return response({detail:'合成网络不可用，原保存回执暂未核对'},503);const nonce=path.split('/').at(-1),receipt=f.saves.get(nonce),expected=Number(u.searchParams.get('expected_version'));if(receipt)assert.equal(u.searchParams.get('expected_body_hash'),receipt.body_hash);return response({schema:'local-proposal-review-save-lookup-v261',found:!!receipt,save_reply:receipt?saveReply(receipt):null,current_draft:clone(f.row),version_state:f.row.version===expected?'same':f.row.version>expected?'advanced':'ahead',absence_is_non_execution:false});
  }
  if(path===base&&method==='PUT'){
   assert.equal(f.role,'operator');const b=JSON.parse(options.body),prior=f.saves.get(b.request_id);if(prior){assert.equal(prior.body_hash,digest(b));return response(saveReply(prior));}assert.equal(b.version,f.row.version);assert.deepEqual(b.value.source,f.row.value.source);const receipt={schema:'local-proposal-review-draft-save-receipt-v261',draft_id:draftID,workspace_id:account.workspace_id,owner_id:account.user_id,request_id:b.request_id,body_hash:digest(b),version_before:f.row.version,version_after:f.row.version+1,value_hash:digest(b.value)};receipt.receipt_hash=digest(receipt);f.row.version++;f.row.value=clone(b.value);f.row.value_hash=receipt.value_hash;f.saves.set(b.request_id,receipt);const reply=saveReply(receipt);if(f.saveGate)await f.saveGate.promise;if(f.lostSave){f.lostSave=false;throw Error('Synthetic response loss after save');}return response(reply);
  }
  if(path.startsWith(base+'/submissions/by-request/')&&method==='GET'){
   const attempt=f.attempts.get(path.split('/').at(-1));if(attempt)assert.equal(u.searchParams.get('expected_body_hash'),attempt.receipt.body_hash);return response({schema:'local-proposal-review-submission-lookup-v261',found:!!attempt,submission:attempt?clone(attempt):null,rejection:null,absence_is_non_execution:false});
  }
  if(path===base+'/submissions'&&method==='POST'){
   assert.equal(f.role,'operator');const b=JSON.parse(options.body),prior=f.attempts.get(b.request_id);if(prior){assert.equal(prior.receipt.body_hash,digest(b));return response(prior);}assert.equal(b.version,f.row.version);assert.equal(b.value_hash,f.row.value_hash);assert.equal(b.revision,f.revision);assert.equal(b.confirm_local_changes,true);assert.deepEqual(b.operations,[{op:'discard_item',path:'/businesses/0',expected_hash:f.source.item_hashes['/businesses/0'],reason}]);
   const result={...clone(f.source.raw_candidate),businesses:[clone(f.source.raw_candidate.businesses[1])]},resultHash=digest(result);
   const receipt={schema:'local-proposal-review-submission-receipt-v261',submission_id:randomUUID(),workspace_id:account.workspace_id,owner_id:account.user_id,draft_id:draftID,draft_version:b.version,request_id:b.request_id,body_hash:digest(b),value_hash:b.value_hash,source_id:analysisID,source_raw_hash:f.source.raw_hash,source_request_hash:hash('c'),configuration_revision:f.revision,configuration_hash:hash('b'),operations_hash:digest(b.operations),policy_hash:hash('d'),derived_analysis_id:derivedID,derived_result_hash:resultHash};receipt.receipt_hash=digest(receipt);
   f.derived={...clone(f.original),id:derivedID,version:1,status:'review',result,result_hash:resultHash,error:null,completed_batches:1,total_batches:1,source_revision:f.revision,raw_response_hash:null,local_review_available:false,local_review_provenance:{policy:'local-proposal-review-v261',submission_id:receipt.submission_id,receipt_hash:receipt.receipt_hash,source_id:analysisID}};
   const reply={schema:'local-proposal-review-submission-v261',receipt,analysis:clone(f.derived),verification_state:'saved_not_live_revalidated'};f.attempts.set(b.request_id,reply);f.row.status='submitted';f.row.version++;return response(reply,201);
  }
  // A real paid preview/start, apply, reuse or configuration write would fail
  // here. No successful fallback transport or controller is injected.
  f.unexpected.push(call);throw Error('Unexpected synthetic API operation '+method+' '+path);
 };
 f.writes=()=>f.calls.filter(call=>call.method!=='GET');f.puts=()=>f.calls.filter(call=>call.method==='PUT');f.submits=()=>f.calls.filter(call=>call.method==='POST'&&call.path===base+'/submissions');return f;
}

let folder,bundle;
after(async()=>{if(folder)await rm(folder,{recursive:true,force:true});});
async function production(){if(!bundle)bundle=(async()=>{folder=await mkdtemp(join(tmpdir(),'understanding-local-review-'));const entryFile=join(folder,'entry.jsx');await writeFile(entryFile,'export {default as UnderstandingDialog} from '+JSON.stringify(join(project,'src/UnderstandingDialog.jsx'))+';export {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';');const result=await build({root:project,configFile:false,envDir:folder,cacheDir:join(folder,'cache'),logLevel:'silent',define:{'process.env.NODE_ENV':'"development"'},build:{ssr:entryFile,write:false,minify:false,rollupOptions:{external:name=>['react',reactPath,reactURL].includes(name),output:{format:'es',inlineDynamicImports:true,paths:name=>['react',reactPath,reactURL].includes(name)?reactURL:name}}}}),chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);const path=join(folder,'production.mjs');await writeFile(path,chunk.code);return import(pathToFileURL(path).href);})();return bundle;}
async function scene(f,options={}){
 assert.equal(process.env.NODE_ENV,'development');const errors=[],warnings=[],vc=new VirtualConsole();vc.on('jsdomError',cause=>errors.push(cause));class Offline extends ResourceLoader{fetch(){throw Error('Browser resources forbidden');}}
 const window=new JSDOM('<div id="root"></div>',{url:'https://integration.invalid/',resources:new Offline(),virtualConsole:vc}).window,prior=new Map(),storage=options.storage||new Memory(),session=options.session||new Memory(),locks=lockManager();Object.defineProperty(window.navigator,'locks',{value:locks,configurable:true});window.confirm=()=>{throw Error('Native confirmation forbidden');};window.fetch=f.fetch;
 for(const[key,value]of Object.entries({window,document:window.document,navigator:window.navigator,HTMLElement:window.HTMLElement,Event:window.Event,IS_REACT_ACT_ENVIRONMENT:true,localStorage:storage,sessionStorage:session,fetch:f.fetch,crypto:globalThis.crypto||webcrypto})){prior.set(key,Object.getOwnPropertyDescriptor(globalThis,key));Object.defineProperty(globalThis,key,{value,writable:true,configurable:true});}
 // Bundle import runs the real runtime-api module, then its own setSession
 // establishes the same storage and header identity as the normal parent.
 const code=await production();code.setSession('synthetic-parent-session',account.workspace_id,account.user_id,account.username);
 const originalConsole=console.error;console.error=(...args)=>{warnings.push(args.map(String).join(' '));};
 const{createRoot}=await import('react-dom/client'),container=document.getElementById('root'),root=createRoot(container,{onUncaughtError:cause=>errors.push(cause),onCaughtError:cause=>errors.push(cause),onRecoverableError:cause=>errors.push(cause)}),closed=[],applied=[],prepared=[];
 function Dialog({title,onClose,children}){return React.createElement('section',{role:'dialog','aria-modal':'true','aria-label':title},React.createElement('h1',null,title),React.createElement('button',{type:'button','aria-label':'返回配置（父窗口）',onClick:onClose},'返回配置（父窗口）'),children);}
 const Btn=({children,primary,ghost,...props})=>React.createElement('button',{type:'button',...props},children),Field=({label,children})=>React.createElement('label',null,label,children);
 await act(async()=>root.render(React.createElement(code.UnderstandingDialog,{Dialog,Btn,Field,materials:[],workspace:workspace(),account:{...account,role:f.role},initialRecord:clone(f.original),onClose:()=>closed.push(true),onApplied:()=>{applied.push(true);throw Error('No configuration application allowed');},prepareDraft:()=>{prepared.push(true);throw Error('No new configuration or paid request allowed');}})));
 const button=(label,within=container)=>[...within.querySelectorAll('button')].find(node=>node.textContent===label),aria=(label,within=container)=>[...within.querySelectorAll('[aria-label]')].find(node=>node.getAttribute('aria-label')===label);
 async function until(check){const end=Date.now()+6000;while(Date.now()<end){if(check())return;await act(async()=>sleep(5));}assert.ok(check(),'Expected parent state: '+container.textContent.slice(-550));}
 async function click(label,within=container){const node=typeof label==='string'?button(label,within):label;assert.ok(node,'button '+label);assert.equal(node.matches(':disabled'),false,'enabled '+node.textContent);await act(async()=>node.dispatchEvent(new window.MouseEvent('click',{bubbles:true})));}
 async function value(label,text){const node=aria(label);assert.ok(node);assert.equal(node.matches(':disabled'),false);await act(async()=>{Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(node,text);node.dispatchEvent(new window.Event('input',{bubbles:true}));});}
 return {container,storage,session,closed,applied,prepared,button,aria,until,click,value,close:async()=>{try{await act(async()=>root.unmount());assert.deepEqual(errors,[]);assert.deepEqual(warnings,[]);assert.deepEqual(f.unexpected,[]);}finally{console.error=originalConsole;window.close();for(const[key,descriptor]of prior){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}}};
}

test('V261 actual UnderstandingDialog local-review entry waits for source then PUT and submission return the new review to the real parent without apply or cloud',async()=>{
 const f=fixture(),before=stable(f.original);f.sourceGate=gate();const h=await scene(f);
 try{
  await h.until(()=>h.button(entry));await h.click(entry);await h.until(()=>h.aria('当前私人核对稿'));
  assert.equal(h.container.querySelectorAll('[role="dialog"]').length,1);assert.equal(h.container.querySelectorAll('[aria-modal="true"]').length,1);assert.equal(h.button('舍弃这项业务建议'),undefined);assert.match(h.container.textContent,/原建议或授权原文尚未完成核对/);assert.equal(f.writes().length,0);
  f.sourceGate.resolve();await h.until(()=>h.button('舍弃这项业务建议')&&!h.button('舍弃这项业务建议').matches(':disabled'));await h.click('舍弃这项业务建议',h.aria('业务建议1'));await h.value('业务建议1的修改理由',reason);
  await h.until(()=>f.row.value.edits[0]?.reason===reason&&h.button(submitLabel)&&!h.button(submitLabel).matches(':disabled'));assert.ok(f.puts().length>=1);assert.equal(f.submits().length,0);await h.click(submitLabel);
  await h.until(()=>h.aria('核对资料理解建议')&&h.container.textContent.includes('尚未授予执行权限')&&h.storage.reads.includes('ga-setup-review:'+derivedID));
  assert.equal(f.submits().length,1);assert.equal(f.derived.id,derivedID);assert.match(h.container.textContent,/保留人工核查业务/);assert.equal(h.container.textContent.includes('待舍弃的不可靠业务'),false);assert.match(h.container.textContent,/已保存原回答上本地核对后另存/);assert.equal(h.aria('私人核对原建议'),undefined);assert.equal(h.button('应用选中的建议').matches(':disabled'),true);assert.equal(h.container.textContent.includes(derivedID),false);assert.equal(h.container.querySelector('pre'),null);
  assert.deepEqual(h.applied,[]);assert.deepEqual(h.prepared,[]);assert.deepEqual(h.closed,[]);assert.equal(stable(f.original),before);assert.ok(f.writes().every(call=>call.method==='PUT'&&call.path==='/api/setup/local-review-drafts/'+draftID||call.method==='POST'&&call.path==='/api/setup/local-review-drafts/'+draftID+'/submissions'));
 }finally{f.sourceGate.resolve();await h.close();}
});

test('V261 actual parent configuration-return guard preserves an in-flight then unknown save and full remount recovers its exact nonce with no new write',async()=>{
 const f=fixture(),first=await scene(f);let storage,session,original,originalBytes;
 try{
  await first.click(entry);await first.until(()=>first.button('舍弃这项业务建议')&&!first.button('舍弃这项业务建议').matches(':disabled'));f.saveGate=gate();f.lostSave=true;f.saveLookupUnavailable=true;
  await first.click('舍弃这项业务建议',first.aria('业务建议1'));await first.value('业务建议1的修改理由',reason);await first.until(()=>f.puts().length===1);
  await first.click('返回配置（父窗口）');assert.equal(first.closed.length,0);assert.equal(first.aria('业务建议1的修改理由').value,reason);f.saveGate.resolve();
  await first.until(()=>first.aria('待核对的原申请'));await first.click('返回配置（父窗口）');assert.equal(first.closed.length,0);assert.equal(first.aria('业务建议1的修改理由').value,reason);
  const key=[...first.storage.rows.keys()].find(value=>value.includes('ga-local-proposal-review-v261:')&&value.includes(':original:save:'));assert.ok(key);originalBytes=first.storage.getItem(key);original=JSON.parse(originalBytes);assert.equal(JSON.parse(original.raw).value.edits[0].reason,reason);assert.equal(sha(original.raw),original.raw_sha256);assert.equal(digest(JSON.parse(original.raw)),original.body_hash);assert.equal(f.puts().length,1);storage=first.storage;session=first.session;
 }finally{f.saveGate?.resolve();await first.close();}
 const writes=f.writes().length;f.role='viewer';f.saveLookupUnavailable=false;f.saveGate=null;const second=await scene(f,{storage,session});
 try{
  await second.until(()=>second.button(viewerEntry));await second.click(viewerEntry);await second.until(()=>second.aria('业务建议1的修改理由')&&second.container.textContent.includes('已核对服务器保存'));
  assert.equal(second.aria('业务建议1的修改理由').value,reason);assert.equal(second.aria('业务建议1的修改理由').matches(':disabled'),true);assert.equal(second.aria('待核对的原申请'),undefined);assert.equal(second.button(submitLabel).matches(':disabled'),true);assert.equal(second.button('明确另开一份私人核对稿').matches(':disabled'),true);assert.match(second.container.textContent,/当前账号只读/);assert.equal(f.writes().length,writes);
  assert.ok([...second.storage.rows.values()].includes(originalBytes));assert.ok(f.calls.some(call=>call.method==='GET'&&call.path.endsWith('/saves/by-request/'+JSON.parse(original.raw).request_id)));
  await second.click('返回配置（父窗口）');assert.equal(second.closed.length,1);assert.deepEqual(second.applied,[]);assert.deepEqual(second.prepared,[]);
 }finally{await second.close();}
});

test('V261 actual UnderstandingDialog viewer can restore an owner server draft from empty local storage but cannot edit submit or open a new one',async()=>{
 const f=fixture();f.role='viewer';f.row.version=3;f.row.value.edits=[{op:'discard_item',path:'/businesses/0',reason}];f.row.value_hash=digest(f.row.value);const h=await scene(f);
 try{
  await h.until(()=>h.button(viewerEntry));assert.equal(h.button(entry),undefined);await h.click(viewerEntry);await h.until(()=>h.aria('业务建议1的修改理由')&&h.container.textContent.includes('已核对服务器保存'));
  assert.equal(h.aria('业务建议1的修改理由').value,reason);assert.equal(h.aria('业务建议1的修改理由').matches(':disabled'),true);assert.equal(h.button(submitLabel).matches(':disabled'),true);assert.equal(h.button('明确另开一份私人核对稿').matches(':disabled'),true);assert.equal(f.writes().length,0);assert.equal(h.container.querySelectorAll('[role="dialog"]').length,1);assert.deepEqual(h.applied,[]);assert.deepEqual(h.prepared,[]);
 }finally{await h.close();}
});
