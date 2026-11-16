// Actual React component and runtime HTTP client. Transport fixtures are
// synthetic; these tests do not claim backend, persistence or browser proof.
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
  temporary=await mkdtemp(join(tmpdir(),'threshold-feedback-v147-'));const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as Feedback} from '+JSON.stringify(join(project,'src/ThresholdFeedbackV147.jsx'))+';\nexport {ThresholdFeedbackJournalV147 as Journal,thresholdScopeV147 as scope} from '+JSON.stringify(join(project,'src/threshold-feedback-contract-v147.mjs'))+';\nexport {setSession,json as runtimeJSON} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(item=>item.type==='chunk'&&item.isEntry),path=join(temporary,'component.mjs');assert.ok(output);await writeFile(path,output.code);return import(pathToFileURL(path).href);
 })();return bundle;
}
const id=n=>'14700000-0000-4000-8000-'+String(n).padStart(12,'0'),copy=value=>JSON.parse(JSON.stringify(value));
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-threshold-operator',role:'operator'},instance=id(3),observation=id(4),capability=id(5),feedback=id(6);
const record={id:observation,version:1,extractor_id:capability,review_basis:{observed_at:'2026-10-21T09:00:00+08:00'}};
const basis={schema:'threshold-evolution-feedback-basis-v147',basis_hash:'a'.repeat(64),observation_id:observation,observation_version:1,baseline_capability_id:capability,baseline_capability_hash:'b'.repeat(64),rule_id:'count_high',field:'count',field_label:'原数量',unit:null,operator:'gt',threshold:20,actual_value:21,actual_hit:true,instance_id:instance,release_id:id(7),entity_id:id(8),source_id:id(9)};
const deferred=()=>{let resolve;const promise=new Promise(done=>{resolve=done;});return {promise,resolve};};
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
class NoResources extends ResourceLoader{fetch(){throw Error('No external resources');}}
function locks(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const next=(tails.get(name)||Promise.resolve()).catch(()=>{}).then(callback);tails.set(name,next);return next.finally(()=>{if(tails.get(name)===next)tails.delete(name);});}};}
function savedView(label,reason,status='draft',version=1){
 const entry={id:feedback,version,root_id:feedback,revision:1,previous_id:null,status,actor_id:account.user_id,label,reason,cancel_reason:status==='cancelled'?'保留原判断，取消此稿':null,classification:label==='should_alert'?'TP':label==='should_not_alert'?'FP':'unknown'};
 return {schema:'threshold-evolution-feedback-view-v147',...entry,basis:copy(basis),history:[copy(entry)],latest_submitted:status==='submitted'?copy(entry):null,can_edit:status==='draft',can_cancel:status==='draft',can_submit:status==='draft'&&label!==null&&!!reason.trim(),can_revise:status!=='draft'};
}
async function harness(run){
 const dom=new JSDOM('<!doctype html><div id="root"></div>',{url:'https://threshold-feedback.invalid/',pretendToBeVisual:true,resources:new NoResources()}),calls=[],violations=[],errors=[];
 const state={account:copy(account),who:copy(account),saved:null,commands:new Map(),loseReply:false,postGate:null,contextGate:null,failWrites:false};
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,storageProto=Object.getPrototypeOf(storage),originalSet=storageProto.setItem;
 storageProto.setItem=function(key,value){if(state.failWrites&&String(key).startsWith('ga-threshold-feedback-v147:'))throw Error('synthetic storage quota');return originalSet.call(this,key,value);};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:locks()});
 const respond=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname,search:url.search,raw:options.body});
  try{
   if(url.pathname==='/api/me')return respond(copy(state.who));
   assert.equal(new Headers(options.headers).get('X-Workspace-ID'),state.account.workspace_id);
   if(method==='GET'&&url.pathname==='/api/local-rules/'+capability)return state.ruleUnavailable?respond({detail:'当前规则清单暂不可读'},503):respond({id:capability,rules:[{id:'count_high',field:'count',operator:'gt',unit:null,threshold:20}],context_available:false,status:'withdrawn'});
   if(method==='GET'&&url.pathname.endsWith('/feedback-context')){const value={schema:'threshold-evolution-feedback-context-v147',supported:true,issue:null,basis:copy(basis),feedback:copy(state.saved)};if(state.contextGate)await state.contextGate.promise;return respond(value);}
   if(method==='GET'&&url.pathname.includes('/requests/')){
    const nonce=url.pathname.split('/').at(-1),command=state.commands.get(nonce);
    if(command){assert.equal(url.searchParams.get('expected_sha256'),createHash('sha256').update(command.raw).digest('hex'));assert.equal(url.searchParams.get('original_method'),command.method);assert.equal(url.searchParams.get('original_path'),command.path);}
    return respond({schema:'threshold-evolution-request-recovery-v147',found:!!command,feedback:command?copy(state.saved):null});
   }
   if(['POST','PUT'].includes(method)&&url.pathname.startsWith('/api/local-rules/evolution/feedback')){
    const body=JSON.parse(options.body),existing=state.commands.get(body.request_id);
    if(existing){assert.equal(existing.raw,options.body);assert.equal(existing.path,url.pathname);assert.equal(existing.method,method);}
    else{
     if(url.pathname.endsWith('/submit'))state.saved=savedView(state.saved.label,state.saved.reason,'submitted',state.saved.version+1);
     else if(url.pathname.endsWith('/cancel'))state.saved=savedView(state.saved.label,state.saved.reason,'cancelled',state.saved.version+1);
     else state.saved=savedView(body.label,body.reason,'draft',state.saved?state.saved.version+1:1);
     state.commands.set(body.request_id,{raw:options.body,method,path:url.pathname});
    }
    if(state.postGate)await state.postGate.promise;
    if(state.loseReply){state.loseReply=false;throw Error('synthetic response lost after actual fixture save');}
    return respond(copy(state.saved),201);
   }
   throw Error('Unexpected route '+method+' '+url.pathname);
  }catch(error){if(!String(error.message).includes('synthetic response lost'))violations.push(error.stack);throw error;}
 };
 const controlledCrypto={randomUUID:()=>webcrypto.randomUUID(),subtle:{digest:async(...args)=>{const raw=new TextDecoder().decode(args[1]);if(state.holdDigestOnce&&raw.includes(state.digestNeedle)){state.holdDigestOnce=false;state.digestEntered=true;await state.digestGate.promise;}return webcrypto.subtle.digest(...args);}}};
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,localStorage:storage,sessionStorage:session,fetch,crypto:controlledCrypto,IS_REACT_ACT_ENVIRONMENT:true},prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));
 for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 let root;const container=dom.window.document.getElementById('root'),gates=[];
 try{
  const actual=await component(),renderer=await import('react-dom/client');assert.equal(actual.BundledReact,React);actual.setSession('synthetic-csrf',account.workspace_id,account.user_id,account.username);
  const settle=async(turns=8)=>{for(let i=0;i<turns;i++)await act(async()=>{await new Promise(done=>setImmediate(done));});};
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Feedback,{record,instanceId:instance,account:state.account,Btn:Button}))));await settle();};
  const mount=async()=>{root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error)});await render();};
  const wait=async(predicate,label)=>{for(let i=0;i<100;i++){if(predicate())return;await settle(1);}assert.ok(predicate(),label+' '+container.textContent.slice(0,1200));};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label);
  const click=async label=>{const node=button(label);assert.ok(node,'actual '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const edit=async(label,value)=>{const node=container.querySelector('[aria-label="'+label+'"]');assert.ok(node);await act(async()=>{Object.getOwnPropertyDescriptor(node.tagName==='SELECT'?dom.window.HTMLSelectElement.prototype:dom.window.HTMLTextAreaElement.prototype,'value').set.call(node,value);node.dispatchEvent(new dom.window.Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});await settle();};
  const gate=()=>{const value=deferred();gates.push(value);return value;};
  await run({state,calls,container,storage,session,mount,render,settle,wait,button,click,edit,gate,actual,dom,unmount:async()=>{await act(async()=>root.unmount());root=null;},setSession:actual.setSession});
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
 }finally{gates.forEach(value=>value.resolve());if(root)await act(async()=>root.unmount());storageProto.setItem=originalSet;dom.window.close();for(const[key,value]of Object.entries(prior)){if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];}}
}

test('feedback is opt-in; actual rule/context, empty label, saved draft and explicit submission stay separate',()=>harness(async h=>{
 await h.mount();assert.equal(h.calls.length,0);await h.click('纠正提示判断');await h.wait(()=>!!h.container.querySelector('[aria-label="提示应否发生"]'),'human label available');
 assert.equal(h.container.querySelector('[aria-label="提示应否发生"]').value,'');assert.ok(h.container.textContent.includes('原数量'));assert.ok(h.container.textContent.includes('21'));assert.ok(h.container.textContent.includes('登记一致性核查'));
 await h.edit('提示应否发生','should_not_alert');await h.edit('判断理由','合成机制反馈：这次本不应提示');await h.click('保存判断草稿');await h.wait(()=>h.container.textContent.includes('服务器已保存判断草稿'),'server draft is read back');
 assert.equal(h.state.saved.status,'draft');await h.click('提交判断');await h.wait(()=>h.state.saved?.status==='submitted'&&h.container.textContent.includes('判断已正式提交'),'submitted displayed beside history');assert.equal(h.state.saved.status,'submitted');assert.equal(h.calls.filter(row=>['POST','PUT'].includes(row.method)).length,2);assert.ok(!h.container.textContent.includes('候选策略已生效'));
}));

test('lost reply retains exact nonce/body across remount and settles only through original SHA GET',()=>harness(async h=>{
 await h.mount();await h.click('纠正提示判断');await h.edit('提示应否发生','should_alert');await h.edit('判断理由','原请求与理由必须保留');h.state.loseReply=true;await h.click('保存判断草稿');
 const posts=h.calls.filter(row=>row.method==='POST');assert.equal(posts.length,1);const sent=JSON.parse(posts[0].raw);assert.ok(h.container.textContent.includes('原请求待核对'));
 await h.unmount();await h.mount();assert.equal(h.calls.filter(row=>row.method==='POST').length,1);await h.click('纠正提示判断');await h.click('核对原判断请求');await h.wait(()=>!h.container.textContent.includes('原请求待核对'),'original request recovered');
 assert.ok(h.calls.some(row=>row.method==='GET'&&row.path.endsWith('/requests/'+sent.request_id)));assert.equal(h.calls.filter(row=>row.method==='POST').length,1);assert.equal(h.container.querySelector('[aria-label="判断理由"]').value,'原请求与理由必须保留');
}));

test('local storage failure keeps visible human input, prevents POST and prevents closing away the unsaved draft',()=>harness(async h=>{
 await h.mount();await h.click('纠正提示判断');h.state.failWrites=true;await h.edit('判断理由','配额失败仍保留这段人工说明');
 assert.equal(h.container.querySelector('[aria-label="判断理由"]').value,'配额失败仍保留这段人工说明');assert.equal(h.button('保存判断草稿').disabled,true);await h.click('收起判断');assert.ok(h.container.querySelector('[aria-label="判断理由"]'));assert.equal(h.calls.filter(row=>row.method!=='GET').length,0);
 h.state.failWrites=false;await h.click('重试保存本机稿');assert.equal(h.container.querySelector('[aria-label="判断理由"]').value,'配额失败仍保留这段人工说明');await h.click('收起判断');await h.click('纠正提示判断');assert.equal(h.container.querySelector('[aria-label="判断理由"]').value,'配额失败仍保留这段人工说明');
}));

test('cookie-only late POST cannot settle the shared original or claim success under another current role',()=>harness(async h=>{
 await h.mount();await h.click('纠正提示判断');await h.edit('提示应否发生','should_not_alert');await h.edit('判断理由','晚回执仍须重新核对实际登录');const gate=h.gate();h.state.postGate=gate;await h.click('保存判断草稿');await h.wait(()=>h.calls.some(row=>row.method==='POST'),'original POST entered');
 h.state.who={...h.state.who,role:'viewer'};gate.resolve();await h.settle();assert.ok(!h.container.textContent.includes('服务器已保存判断草稿'));
 const values=Object.keys(h.storage).map(key=>h.storage.getItem(key));assert.ok(values.some(raw=>raw.includes('exact_body')&&raw.includes('request_id')),'the original remains durably recoverable');assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
}));

test('an actor prop switch masks old context on its first render and ignores a delayed old GET',()=>harness(async h=>{
 await h.mount();const gate=h.gate();h.state.contextGate=gate;await h.click('纠正提示判断');await h.wait(()=>h.calls.some(row=>row.path.endsWith('/feedback-context')),'old context GET entered');
 h.state.account={...h.state.account,user_id:id(90)};h.state.who=copy(h.state.account);h.setSession('synthetic-csrf',h.state.account.workspace_id,h.state.account.user_id,h.state.account.username);await h.render();assert.ok(!h.container.textContent.includes('原数量'));
 gate.resolve();await h.settle();assert.ok(!h.container.textContent.includes('原数量'));assert.equal(h.calls.filter(row=>row.method!=='GET').length,0);
}));

test('original SHA recovery survives a failed current rule GET without enabling a fresh POST',()=>harness(async h=>{
 await h.mount();await h.click('纠正提示判断');await h.edit('提示应否发生','should_not_alert');await h.edit('判断理由','当前规则暂不可读仍要找回这个原请求');h.state.loseReply=true;await h.click('保存判断草稿');await h.wait(()=>h.state.commands.size===1&&h.container.textContent.includes('原请求待核对'),'original saved before the lost reply');
 await h.unmount();h.state.ruleUnavailable=true;await h.mount();await h.click('纠正提示判断');await h.wait(()=>!!h.button('核对原判断请求')&&!h.button('核对原判断请求').disabled,'original lookup is independent of current catalog');assert.equal(h.button('按原编号重试判断').disabled,true);
 await h.click('核对原判断请求');await h.wait(()=>!h.container.textContent.includes('原请求待核对'),'old command recovered read-only');assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
}));

test('independent tab draft slots and quota warnings cannot be overwritten or released by another tab save',()=>harness(async h=>{
 await h.mount();assert.equal(h.calls.length,0);
 const session=seed=>{const values=new Map(seed);return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),entries:()=>[...values]};};
 const firstSession=session(),secondSession=session(),scope=h.actual.scope(account,instance,record),sharedLocks=locks();
 const first=new h.actual.Journal(scope,{storage:h.storage,session:firstSession,locks:sharedLocks,crypto:webcrypto});first.open();first.save({...first.form,reason:'甲窗口的独立原稿'});
 // Duplicating a real browser tab may copy sessionStorage. Opening still forks
 // an owner slot instead of modifying the original tab's draft in place.
 for(const[key,value]of firstSession.entries())secondSession.setItem(key,value);
 const second=new h.actual.Journal(scope,{storage:h.storage,session:secondSession,locks:sharedLocks,crypto:webcrypto});second.open();second.save({...second.form,reason:'乙窗口另有人工说明'});
 assert.notEqual(first.owner,second.owner);assert.equal(first.readDraft(first.owner).row.form.reason,'甲窗口的独立原稿');assert.equal(second.readDraft(second.owner).row.form.reason,'乙窗口另有人工说明');
 h.state.failWrites=true;assert.throws(()=>first.save({...first.form,reason:'甲窗口配额失败但不能丢失'}));h.state.failWrites=false;second.save({...second.form,reason:'乙窗口保存成功'});assert.equal(first.isUnsaved(),true);
 const warning=new h.dom.window.Event('beforeunload',{cancelable:true});h.dom.window.dispatchEvent(warning);assert.equal(warning.defaultPrevented,true);
 first.save(first.form);assert.equal(first.isUnsaved(),false);first.dispose();second.dispose();
 const restoredFirst=new h.actual.Journal(scope,{storage:h.storage,session:firstSession,locks:sharedLocks,crypto:webcrypto}),restoredSecond=new h.actual.Journal(scope,{storage:h.storage,session:secondSession,locks:sharedLocks,crypto:webcrypto});
 assert.equal(restoredFirst.open().reason,'甲窗口配额失败但不能丢失');assert.equal(restoredSecond.open().reason,'乙窗口保存成功');restoredFirst.dispose();restoredSecond.dispose();assert.equal(h.calls.length,0);
}));

test('recovering another owner shared original preserves the open component human draft and does not POST it',()=>harness(async h=>{
 await h.mount();await h.click('纠正提示判断');await h.edit('提示应否发生','should_not_alert');const localReason='乙窗口独立原稿：不能被甲窗口回执覆盖';await h.edit('判断理由',localReason);
 const values=new Map(),otherSession={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value))};
 const other=new h.actual.Journal(h.actual.scope(account,instance,record),{storage:h.storage,session:otherSession,locks:h.dom.window.navigator.locks,crypto:webcrypto});
 try{
  other.open();other.save({...other.form,label:'should_alert',reason:'甲窗口另有原请求',target:{kind:'create',observation_version:1}});
  const body={request_id:webcrypto.randomUUID(),observation_id:observation,rule_id:'count_high',expected_observation_version:1,label:'should_alert',reason:'甲窗口另有原请求'};
  const original=await other.install('POST','/local-rules/evolution/feedback',body,async()=>{assert.equal((await h.actual.runtimeJSON('/me')).user_id,account.user_id);});
  await h.actual.runtimeJSON('/local-rules/evolution/feedback',{method:'POST',body:original.exact_body});assert.equal(h.calls.filter(row=>row.method==='POST').length,1);
  await h.click('保存判断草稿');await h.wait(()=>h.container.textContent.includes('原请求待核对'),'the actual component discovers the shared original');await h.click('核对原判断请求');await h.wait(()=>!h.container.textContent.includes('原请求待核对'),'the actual original GET settles its nonce');
  assert.equal(h.container.querySelector('[aria-label="判断理由"]').value,localReason,'HTTP recovery may display the other owner server row but must preserve this owner editor');
  assert.ok(Object.keys(h.storage).some(key=>key.includes(':draft:')&&JSON.parse(h.storage.getItem(key)).form.reason===localReason),'this independent human draft remains durably saved');
  assert.equal(h.calls.filter(row=>row.method==='POST').length,1,'recovering the shared original is GET-only, not a new save of either draft');
 }finally{other.dispose();}
}));

test('a session head whose local draft is missing cannot leave an editable unprotected human draft that close loses',()=>harness(async h=>{
 await h.mount();const inspect=new h.actual.Journal(h.actual.scope(account,instance,record)),missingOwner=id(88);h.session.setItem(inspect.prefix+':head',missingOwner);
 await h.click('纠正提示判断');await h.wait(()=>!!h.container.querySelector('[aria-label="判断理由"]')||h.container.textContent.includes('无法核对'),'invalid local head is handled without a collection failure');
 const input=h.container.querySelector('[aria-label="判断理由"]');
 if(!input||input.disabled){assert.equal(h.calls.filter(row=>row.method!=='GET').length,0);return;}
 const reason='本机head失配之后新输入也必须保住';await h.edit('判断理由',reason);assert.equal(h.container.querySelector('[aria-label="判断理由"]').value,reason);await h.click('收起判断');
 if(!h.container.querySelector('[aria-label="判断理由"]'))await h.click('纠正提示判断');
 assert.equal(h.container.querySelector('[aria-label="判断理由"]')?.value,reason,'either editing was explicitly blocked, or close/reopen must retain the human work');
 assert.equal(h.calls.filter(row=>row.method!=='GET').length,0);
}));

test('a delayed old-scope local pending digest cannot publish its original or draft list into a verified new actor',()=>harness(async h=>{
 await h.mount();const scope=h.actual.scope(account,instance,record),oldJournal=new h.actual.Journal(scope,{storage:h.storage,session:h.session,locks:h.dom.window.navigator.locks,crypto:webcrypto}),oldReason='旧账号甲的保留稿绝不能进入新账号';
 try{
  oldJournal.open();oldJournal.save({...oldJournal.form,label:'should_alert',reason:oldReason,target:{kind:'create',observation_version:1}});
  const body={request_id:webcrypto.randomUUID(),observation_id:observation,rule_id:'count_high',expected_observation_version:1,label:'should_alert',reason:oldReason};await oldJournal.install('POST','/local-rules/evolution/feedback',body,async()=>{});
  const gate=h.gate();h.state.digestNeedle=oldReason;h.state.digestGate=gate;h.state.holdDigestOnce=true;await h.click('纠正提示判断');await h.wait(()=>h.state.digestEntered===true,'actual pending reader is paused at its real hashBytes boundary');
  h.state.account={...h.state.account,user_id:id(91),username:'new-actor'};h.state.who=copy(h.state.account);h.setSession('synthetic-csrf',h.state.account.workspace_id,h.state.account.user_id,h.state.account.username);await h.render();await h.click('纠正提示判断');await h.wait(()=>!!h.container.querySelector('[aria-label="判断理由"]'),'new actor has its own verified context');await h.edit('判断理由','新账号乙的独立人稿');
  gate.resolve();await h.settle();assert.ok(!h.container.textContent.includes(oldReason),'old async draft discovery must not enter the visible new actor scope');assert.ok(!h.container.textContent.includes('原请求待核对'),'old pending must not enter the new actor original slot');assert.equal(h.container.querySelector('[aria-label="判断理由"]').value,'新账号乙的独立人稿');assert.equal(h.calls.filter(row=>row.method!=='GET').length,0);
 }finally{oldJournal.dispose();}
}));
