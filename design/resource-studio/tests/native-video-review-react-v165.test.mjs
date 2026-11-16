// Actual React component, runtime-api, Journal and EvidenceView, with closed
// synthetic HTTP/storage seams. This is not PG, a model call, a decoded browser
// video, layout verification or proof of a real human review being durable.
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
 temporary=await mkdtemp(join(tmpdir(),'native-video-review-v165-'));const entry=join(temporary,'entry.mjs');
 await writeFile(entry,'export {default as Review} from '+JSON.stringify(join(project,'src/NativeVideoReviewV165.jsx'))+';\nexport * from '+JSON.stringify(join(project,'src/native-video-review-contract-v165.mjs'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
 const modules={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(modules),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(modules,key)?pathToFileURL(join(project,'node_modules/react',modules[key])).href:key}}}});
 const chunk=result.output.find(row=>row.type==='chunk'&&row.isEntry);assert.ok(chunk);const path=join(temporary,'component.mjs');await writeFile(path,chunk.code);return import(pathToFileURL(path).href);
})();return bundle;}
const id=n=>'16510000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value)),sha=raw=>createHash('sha256').update(raw).digest('hex');
const account={user_id:id(1),workspace_id:id(2),username:'合成核查甲',role:'operator'},peer={...account,user_id:id(30),username:'合成核查乙'},instance=id(3),observation=id(4),release=id(5),run=id(6),frame=id(7),evidence=id(8),csrf='synthetic-video-review-csrf';
const base='/api/workspace/instances/'+instance+'/native-video-reviews',detailPath=base+'/observations/'+observation,videoBytes=Buffer.from('synthetic video transport bytes, not a playable video'),frameBytes=Buffer.from('synthetic frame transport bytes, not a real PNG'),receiptHash='a'.repeat(64),savedAt='2026-10-21T00:30:20.123456+00:00';
function fixture(){return {schema:'native-video-review-view-v165',instance_id:instance,observation_id:observation,run_id:run,release_id:release,receipt_hash:receiptHash,version:0,status:'empty',owner_id:null,draft:{note:'',frame_ids:[],decision:null},submitted_history:[],latest_submitted:null,human_history:[],source_available:true,source_issue:null,run:{id:run,name:'合成网关视频画面',status:'succeeded',input_sha256:sha(videoBytes),gateway_capture:true,original_path:'/api/videos/'+run+'/original',candidate_only:true,source_time_verified:false,physical_state_verified:false},frames:[{id:frame,sample_index:0,seconds:0,evidence_id:evidence,sha256:sha(frameBytes),width:640,height:360,tracks:[{track_ref:'1',label:'person',score:0.75,bbox:[10,20,80,120]}],utc:null,content_path:'/api/evidence/'+evidence+'/content'}],can_save_draft:true,can_submit:true};}
function applySaved(state,body,operation){
 assert.equal(body.expected_version,state.detail.version,'synthetic transport enforces its CAS seam');const version=state.detail.version+1,item={version,actor_id:state.account.user_id,note:body.note,frame_ids:[...body.frame_ids],decision:body.decision,saved_at:savedAt,operation};
 state.detail={...state.detail,version,status:operation==='submit'?'reviewed':'draft',owner_id:state.account.user_id,draft:{note:body.note,frame_ids:[...body.frame_ids],decision:body.decision},human_history:[...state.detail.human_history,item]};
 if(operation==='submit'){const {operation:ignored,...submitted}=item;state.detail.submitted_history.push({...submitted,actor_label:state.account.username});state.detail.latest_submitted=copy(state.detail.submitted_history.at(-1));}
 return {schema:'native-video-review-request-v165',request_id:body.request_id,wire_sha256:'',outcome:'saved',result:{observation_id:observation,version,operation,status:state.detail.status}};
}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).then(()=>callback({name,mode:'exclusive'}));const tail=next.then(()=>{},()=>{});tails.set(name,tail);return next.finally(()=>{if(tails.get(name)===tail)tails.delete(name);});}};}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resource fetch');}}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}

async function harness(runTest,{saved=false,sourceBad=false,currentAvailable=true}={}){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://native-video-review.invalid/',pretendToBeVisual:true,resources:new NoResources()}),calls=[],violations=[],errors=[],storage=dom.window.localStorage,session=dom.window.sessionStorage;
 let root,actual;const state={account:{...account},propsAccount:{...account},releaseID:release,currentAvailable,detail:fixture(),commands:new Map(),postMode:'normal',failDraft:false,badMedia:false,meGate:null,meEntered:false};
 if(saved){const result=applySaved(state,{expected_version:0,request_id:id(10),note:'服务器原有的个人核查草稿',frame_ids:[frame],decision:'uncertain'},'draft');assert.equal(result.result.version,1);}
 if(sourceBad)state.detail={...state.detail,source_available:false,source_issue:'原视频依据当前不可核，原人工意见保留',run:null,frames:[],can_save_draft:false,can_submit:false};
 const storageProto=Object.getPrototypeOf(storage),setItem=storageProto.setItem;storageProto.setItem=function(key,value){if(this===storage&&state.failDraft&&String(key).includes('ga-native-video-review-v165:')&&String(key).includes(':draft:'))throw Error('synthetic video draft quota');return setItem.call(this,key,value);};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});let urls=0;dom.window.URL.createObjectURL=()=> 'blob:synthetic-video-review-'+(++urls);dom.window.URL.revokeObjectURL=()=>{};
 const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const fetch=async(raw,options={})=>{const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname,search:url.search,raw:options.body});try{
  assert.equal(url.origin,dom.window.location.origin);assert.equal(options.credentials,'same-origin');const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),state.account.workspace_id);
  if(url.pathname==='/api/me'&&method==='GET'){if(state.meGate){const gate=state.meGate;state.meGate=null;state.meEntered=true;await gate;}return json(state.account);}
  if(url.pathname===detailPath&&method==='GET')return json(copy(state.detail));
  if(url.pathname==='/api/videos/'+run+'/original'&&method==='GET'){assert.equal(url.searchParams.get('workspace_id'),state.account.workspace_id);return new Response(state.badMedia?Buffer.from('bad video'):videoBytes,{headers:{'Content-Type':'video/mp4'}});}
  if(url.pathname==='/api/evidence/'+evidence+'/content'&&method==='GET')return new Response(state.badMedia?Buffer.from('bad frame'):frameBytes,{headers:{'Content-Type':'image/png'}});
  if(url.pathname.startsWith(base+'/requests/')&&method==='GET'){
   const requestID=url.pathname.split('/').at(-1),expected=url.searchParams.get('expected_sha256');assert.match(expected,/^[0-9a-f]{64}$/);const saved=state.commands.get(requestID);if(saved){assert.equal(saved.hash,expected);return json(copy(saved.reply));}return json({schema:'native-video-review-request-v165',request_id:requestID,wire_sha256:expected,outcome:'unknown',result:null});
  }
  if(method==='POST'&&[detailPath+'/draft',detailPath+'/submit'].includes(url.pathname)){
   assert.equal(headers.get('X-CSRF-Token'),csrf);const body=JSON.parse(options.body),operation=url.pathname.split('/').at(-1);assert.deepEqual(Object.keys(body).sort(),['request_id','expected_version','note','frame_ids','decision',...(operation==='submit'?['confirm_candidate_only']:[])].sort());
   const active=Array.from({length:storage.length},(_,i)=>storage.key(i)).filter(key=>key.endsWith(':active')).map(key=>JSON.parse(storage.getItem(key))).find(head=>head.request_id===body.request_id);assert.ok(active,'actual Journal must persist the immutable request before HTTP');const immutable=JSON.parse(storage.getItem(active.key));assert.equal(immutable.exact_body,options.body);assert.equal(immutable.path,url.pathname);assert.equal(immutable.wire_sha256,sha(options.body));assert.equal(immutable.scope.actor_id,state.account.user_id);
   const prior=state.commands.get(body.request_id);if(prior){assert.equal(prior.raw,options.body);assert.equal(prior.path,url.pathname);return json(copy(prior.reply));}
   if(state.postMode==='unknown'){state.postMode='normal';throw Error('synthetic unknown transport before save');}
   if(state.postMode==='conflict'){state.postMode='normal';applySaved(state,{request_id:id(50),expected_version:state.detail.version,note:'同一核查人另一窗口已保存的说明',frame_ids:[frame],decision:'uncertain'},'draft');return json({detail:'保存版本已变化，原请求未保存'},409);}
   if(body.expected_version!==state.detail.version)return json({detail:'原保存版本已失效'},409);
   const reply=applySaved(state,body,operation);reply.wire_sha256=sha(options.body);state.commands.set(body.request_id,{raw:options.body,path:url.pathname,hash:reply.wire_sha256,reply:copy(reply)});
   if(state.postMode==='lost'){state.postMode='normal';throw Error('synthetic response lost after save');}return json(reply);
  }
  throw Error('Unexpected video review HTTP '+method+' '+url.pathname);
 }catch(cause){if(!cause.message.startsWith('synthetic '))violations.push(cause.stack);throw cause;}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,URL:dom.window.URL,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 try{
  // ReactDOM detects DOM/input support when its module first loads. Load it
  // after this real JSDOM window/document is installed, never before it.
  const {createRoot}=await import('react-dom/client');
  actual=await component();assert.equal(actual.BundledReact,React);actual.setSession(csrf,state.account.workspace_id,state.account.user_id,state.account.username);
  const container=document.getElementById('root'),settle=async()=>{for(let n=0;n<6;n++)await act(async()=>{await new Promise(done=>setImmediate(done));await new Promise(done=>setTimeout(done,0));});};
  const wait=async(predicate,label)=>{for(let n=0;n<60;n++){if(predicate())return;await settle();}assert.ok(predicate(),label+'; '+container.textContent.slice(0,1000));};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent.trim()===label),field=label=>container.querySelector('[aria-label="'+label+'"]');
  const click=async label=>{const node=button(label);assert.ok(node,'actual '+label);assert.equal(node.disabled,false,label+' must be enabled');await act(async()=>node.click());await settle();};
  const edit=async(label,value)=>{const node=field(label);assert.ok(node,'actual '+label);assert.equal(node.disabled,false);assert.ok(!node.readOnly,label+' is editable');await act(async()=>{const prototype=node.tagName==='TEXTAREA'?dom.window.HTMLTextAreaElement.prototype:dom.window.HTMLSelectElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  const check=async()=>{const node=field('确认仅核查候选与画面');assert.ok(node);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Review,{account:state.propsAccount,instanceId:instance,observationId:observation,releaseID:state.releaseID,currentAvailable:state.currentAvailable,Btn:Button}))));await settle();};
  const mount=async()=>{root=createRoot(container,{onUncaughtError:cause=>errors.push(cause.message),onCaughtError:cause=>errors.push(cause.message)});await render();};
  const remount=async()=>{await act(async()=>root.unmount());root=null;await mount();};
  const open=async()=>{await click(button('查看视频并填写核查意见')?'查看视频并填写核查意见':'重新打开已保存核查');await wait(()=>field('我的核查说明')&&!button('读取服务器保存版本')?.disabled,'cold detail opens');};
  const fill=async(note='对照候选画面，仍无法确认现场状态')=>{await edit('我的核查说明',note);await edit('我的核查判断','uncertain');if(!container.textContent.includes('已选择 1 张依据画面'))await click('将当前画面作为核查依据');};
  const active=()=>{const key=Array.from({length:storage.length},(_,i)=>storage.key(i)).find(key=>key.endsWith(':active'));return key?{key,head:storage.getItem(key),record:JSON.parse(storage.getItem(JSON.parse(storage.getItem(key)).key))}:null;};
  const unloading=()=>{const event=new dom.window.Event('beforeunload',{cancelable:true});dom.window.dispatchEvent(event);return event.defaultPrevented;};
  await mount();assert.equal(calls.length,0,'closed mounted review makes no HTTP request');await runTest({actual,state,calls,container,storage,session,button,field,click,edit,check,render,remount,open,fill,active,wait,settle,unloading});assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{
  state.failDraft=false;
  try{if(root)await act(async()=>root.unmount());}
  finally{state.releaseMe?.();try{for(let n=0;n<3;n++)await act(async()=>new Promise(done=>setImmediate(done)));}
   finally{storageProto.setItem=setItem;dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}}
 }
}

test('V165 actual review opens original/frame evidence and saves a personal draft before explicit final confirmation',async()=>{
 await harness(async({state,calls,container,button,field,open,fill,check,click,wait,remount})=>{
  await open();await wait(()=>container.querySelector('video')&&container.querySelector('svg image'),'actual EvidenceView checks both transport hashes');assert.ok(container.textContent.includes('未提供拍摄时间'));await fill();assert.equal(button('提交人工核查意见').disabled,true);await click('保存我的核查草稿');await wait(()=>state.detail.version===1&&!button('保存我的核查草稿')?.disabled,'server draft saved and current version read back');assert.equal(state.detail.status,'draft');assert.equal(state.detail.submitted_history.length,0);await remount();await open();assert.equal(field('我的核查说明').value,state.detail.draft.note);assert.equal(field('确认仅核查候选与画面').checked,false);await check();await click('提交人工核查意见');await wait(()=>state.detail.status==='reviewed'&&container.textContent.includes('我的意见'),'explicit final review is saved');assert.equal(state.detail.version,2);assert.equal(calls.filter(row=>row.method==='POST').length,2);assert.ok(container.textContent.includes('中国标准时间'));assert.ok(container.textContent.includes('不改变原机器结果或对象状态'));assert.equal(field('确认仅核查候选与画面').checked,false);
 });
});
test('V165 lost saved reply remount performs only GET recovery and preserves the original immutable command',async()=>{
 await harness(async({state,calls,field,open,fill,click,wait,active,storage,remount})=>{
  await open();await fill('丢回复前的核查原稿必须保留');state.postMode='lost';await click('保存我的核查草稿');await wait(()=>active()&&state.detail.version===1,'saved server response is lost');const sent=active(),before=calls.length;await remount();await open();await wait(()=>active()===null,'GET original saved reply settles active pointer');assert.equal(field('我的核查说明').value,'丢回复前的核查原稿必须保留');assert.ok(calls.slice(before).every(row=>row.method==='GET'));assert.equal(calls.filter(row=>row.method==='POST').length,1);const stored=Array.from({length:storage.length},(_,i)=>storage.getItem(storage.key(i)));assert.ok(stored.includes(JSON.stringify(sent.record)));
 });
});
test('V165 unknown original is never auto-posted on remount; explicit retry sends exact nonce and wire',async()=>{
 await harness(async({state,calls,open,fill,click,wait,active,remount,field})=>{
  await open();await fill('未知送达时保存的原始核查说明');state.postMode='unknown';await click('保存我的核查草稿');await wait(()=>active()!==null,'unknown original persists');const sent=active().record,before=calls.length;await remount();await open();await wait(()=>!document.querySelector('button')?.disabled,'scope is read back');assert.ok(calls.slice(before).every(row=>row.method==='GET'));assert.equal(field('我的核查说明').value,'未知送达时保存的原始核查说明');await click('按原请求重试');await wait(()=>state.detail.version===1&&active()===null,'explicit same-wire retry saved');const posts=calls.filter(row=>row.method==='POST');assert.equal(posts.length,2);assert.equal(posts[0].raw,posts[1].raw);assert.equal(JSON.parse(posts[1].raw).request_id,sent.request_id);assert.ok(calls.slice(before).find(row=>row.path.startsWith(base+'/requests/')));
 });
});
test('V165 CAS conflict preserves human input and requires fresh version plus second original lookup before a new nonce',async()=>{
 await harness(async({state,calls,field,open,fill,click,wait,active,storage})=>{
  await open();await fill('本窗口需要保留的核查说明');state.postMode='conflict';await click('保存我的核查草稿');await wait(()=>state.detail.version===1&&active()!==null,'conflict loads server version without clearing original');const sent=active().record;assert.equal(field('我的核查说明').value,'本窗口需要保留的核查说明');await click('核对原保存请求');await click('按原请求重试');assert.equal(active().record.exact_body,sent.exact_body,'old CAS body remains after explicit same-wire409');const before=calls.length;await click('按最新保存版本继续编辑');await wait(()=>active()===null,'explicit CAS resolution archives immutable original');const after=calls.slice(before);assert.ok(after.find(row=>row.path===detailPath));assert.equal(after.filter(row=>row.path.startsWith(base+'/requests/')).length,1,'independent second lookup is required');assert.ok(after.every(row=>row.method==='GET'));assert.equal(field('我的核查说明').value,'本窗口需要保留的核查说明');assert.ok(Array.from({length:storage.length},(_,i)=>storage.key(i)).some(key=>key.includes('cas-conflict:'+sent.request_id)));await click('保存我的核查草稿');await wait(()=>state.detail.version===2&&active()===null,'new command requires explicit save');const posts=calls.filter(row=>row.method==='POST');assert.equal(posts.length,3);assert.equal(posts[0].raw,posts[1].raw);const latest=JSON.parse(posts[2].raw);assert.notEqual(latest.request_id,sent.request_id);assert.equal(latest.expected_version,1);assert.equal(latest.note,'本窗口需要保留的核查说明');
 });
});
test('V165 a release change during fresh identity prevents a late new POST and hides old draft',async()=>{
 await harness(async({state,calls,field,open,fill,click,render,wait,storage,settle})=>{
  await open();await fill('旧版本私稿不应出现在新版本');state.meGate=new Promise(resolve=>{state.releaseMe=resolve;});await click('保存我的核查草稿');await wait(()=>state.meEntered,'fresh identity is awaiting');state.releaseID=id(40);await render();assert.equal(field('我的核查说明'),null);assert.ok(!document.body.textContent.includes('旧版本私稿不应出现在新版本'));state.releaseMe();await settle();assert.equal(calls.filter(row=>row.method==='POST').length,0);assert.ok(Array.from({length:storage.length},(_,i)=>storage.getItem(storage.key(i))).some(raw=>raw?.includes('旧版本私稿不应出现在新版本')));
 });
});
test('V165 fresh role mismatch masks private draft and never posts under the changed identity',async()=>{
 await harness(async({state,calls,field,open,fill,click,wait,storage})=>{
  await open();await fill('权限变化仍要保留的个人输入');state.account={...state.account,role:'viewer'};await click('保存我的核查草稿');await wait(()=>field('我的核查说明')===null,'new /me role mismatch hides private data');assert.equal(calls.filter(row=>row.method==='POST').length,0);assert.ok(Array.from({length:storage.length},(_,i)=>storage.getItem(storage.key(i))).some(raw=>raw?.includes('权限变化仍要保留的个人输入')));
 });
});
test('V165 unavailable source still exposes saved personal and named team history with no media or new write',async()=>{
 await harness(async({state,calls,container,button,field,open})=>{
  state.detail.submitted_history=[{version:1,actor_id:peer.user_id,actor_label:peer.username,note:'团队已保存的候选人工核查意见',frame_ids:[frame],decision:'uncertain',saved_at:savedAt}];state.detail.latest_submitted=copy(state.detail.submitted_history[0]);await open();assert.equal(field('我的核查说明').value,'服务器原有的个人核查草稿');assert.equal(field('我的核查说明').readOnly,true);assert.equal(field('我的核查判断').disabled,true);assert.equal(field('确认仅核查候选与画面').disabled,true);assert.ok(container.textContent.includes(peer.username+'的意见'));assert.equal(container.querySelector('video'),null);assert.equal(container.querySelector('svg'),null);assert.equal(button('保存我的核查草稿').disabled,true);assert.equal(button('提交人工核查意见').disabled,true);assert.ok(container.textContent.includes('原人工意见保留'));assert.equal(calls.filter(row=>row.method==='POST').length,0);assert.ok(calls.every(row=>!row.path.startsWith('/api/videos/')&&!row.path.startsWith('/api/evidence/')));
 },{saved:true,sourceBad:true,currentAvailable:false});
});
test('V165 quota preserves raw human work, blocks save and return, and releases warning only after exact readback',async()=>{
 await harness(async({state,calls,field,button,open,edit,click,unloading,remount,wait})=>{
  await open();state.failDraft=true;await edit('我的核查说明','暂存额度不足时的人工作业');assert.equal(field('我的核查说明').value,'暂存额度不足时的人工作业');assert.equal(unloading(),true);assert.equal(button('保存我的核查草稿').disabled,true);await click('返回收件记录');assert.equal(field('我的核查说明').value,'暂存额度不足时的人工作业');assert.equal(calls.filter(row=>row.method==='POST').length,0);await remount();await open();assert.equal(field('我的核查说明').value,'暂存额度不足时的人工作业');assert.equal(unloading(),true);state.failDraft=false;await click('重试本机暂存');await wait(()=>!unloading(),'actual draft write-read clears memory warning');await click('保存我的核查草稿');assert.equal(state.detail.draft.note,'暂存额度不足时的人工作业');assert.equal(calls.filter(row=>row.method==='POST').length,1);
 });
});
test('V166 video draft survives repeated quota remounts without resurrecting an older note',async()=>{
 await harness(async({state,calls,field,open,edit,click,unloading,remount,wait})=>{
  await open();state.failDraft=true;await edit('我的核查说明','第一代未落盘视频核查稿');await remount();await open();assert.equal(field('我的核查说明').value,'第一代未落盘视频核查稿');
  await edit('我的核查说明','第二代应保留的最新视频核查稿');await remount();await open();assert.equal(field('我的核查说明').value,'第二代应保留的最新视频核查稿');
  state.failDraft=false;await click('重试本机暂存');await wait(()=>!unloading(),'all exact rescued ancestors cleared only after readback');await remount();await open();assert.equal(field('我的核查说明').value,'第二代应保留的最新视频核查稿');assert.equal(unloading(),false);assert.equal(calls.filter(row=>row.method==='POST').length,0);
 });
});
test('V165 invalid media bytes are not rendered as trusted original or evidence',async()=>{
 await harness(async({state,container,open,wait,calls})=>{
  state.badMedia=true;await open();await wait(()=>container.textContent.includes('摘要不一致'),'actual EvidenceView rejects synthetic bad bytes');assert.equal(container.querySelector('video'),null);assert.equal(container.querySelector('svg image'),null);assert.equal(calls.filter(row=>row.method==='POST').length,0);
 });
});
test('V165 current write flags independently disable fresh save and original retry while preserving read-only media and draft',async()=>{
 await harness(async({state,calls,container,field,button,open,fill,click,wait,active})=>{
  await open();await fill('原请求未知后来源写资格撤回');state.postMode='unknown';await click('保存我的核查草稿');await wait(()=>active()!==null,'original remains active');state.detail.can_save_draft=false;state.detail.can_submit=false;state.detail.source_issue='原来源已不具备新的核查保存权限';await click('读取服务器保存版本');await click('核对原保存请求');assert.equal(button('按原请求重试').disabled,true);assert.equal(button('保存我的核查草稿').disabled,true);assert.equal(field('我的核查说明').readOnly,true);assert.equal(field('我的核查判断').disabled,true);assert.equal(button('移除当前依据画面').disabled,true);assert.equal(field('查看保存画面').disabled,false);assert.equal(field('我的核查说明').value,'原请求未知后来源写资格撤回');assert.ok(container.querySelector('video'));assert.equal(calls.filter(row=>row.method==='POST').length,1);
 });
});
