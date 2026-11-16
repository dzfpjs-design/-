// Actual ObjectStateStudio + actual NativeCandidateQueryV139, React DOM and
// StrictMode. Only HTTP transport fixtures are synthetic. No query POST,
// model, backend authority, real browser or database durability is claimed.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';
import {background as readOnlyBackground} from './object-state-background-v267.fixture.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'object-state-candidate-bridge-v140-'));const entry=join(temporary,'entry.mjs');
  // Import only modules present in V139: the first red must be the missing
  // object-page integration, never a collection error for a future module.
  await writeFile(entry,'export {default as Studio} from '+JSON.stringify(join(project,'src/ObjectStateStudio.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\nexport {default as BundledReact} from "react";\n');
  const entries={react:'index.js','react/jsx-runtime':'jsx-runtime.js','react/jsx-dev-runtime':'jsx-dev-runtime.js'};
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{external:Object.keys(entries),output:{format:'es',inlineDynamicImports:true,paths:key=>Object.hasOwn(entries,key)?pathToFileURL(join(project,'node_modules/react',entries[key])).href:key}}}});
  const output=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(output,'bundle the real existing object-page component and its actual children');const file=join(temporary,'component.mjs');await writeFile(file,output.code);return import(pathToFileURL(file).href);
 })();return bundle;
}
const id=value=>'14000000-0000-4000-8000-'+String(value).padStart(12,'0');
const account={user_id:id(1),workspace_id:id(2),username:'synthetic-object-operator',role:'operator'},otherAccount={...account,user_id:id(9),username:'synthetic-other-operator'};
const instance=id(3),release=id(4),otherRelease=id(8),entity=id(5),otherEntity=id(7);
const copy=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let complete,settled=false;const promise=new Promise(done=>{complete=done;});return {promise,resolve(value){settled=true;complete(value);},get settled(){return settled;}};};
function lockManager(){const tails=new Map();return {request(name,options,callback){assert.equal(options.mode,'exclusive');const prior=tails.get(name)||Promise.resolve(),next=prior.catch(()=>{}).then(callback);tails.set(name,next);return next.finally(()=>{if(tails.get(name)===next)tails.delete(name);});}};}
function object(identity,targetRelease){return {id:identity,name:identity===entity?'示例林班甲':'示例林班乙',version:1,object_compatibility:{status:'current',target_release_id:targetRelease,usable:true,source_label:identity===entity?'示例林班甲':'示例林班乙',source_type:'测试对象',source_release_number:targetRelease===release?1:2}};}
function savedQuery({actor=account.user_id,entityID=entity,releaseID=release,index=1,name='林班甲的原学习型变化候选',text='甲对象已核查候选'}={}){
 return {actor,value:{schema:'native-candidate-query-view-v139',id:id(100+index),version:1,status:'sufficient',request_id:id(200+index),request:{request_id:id(200+index),release_id:releaseID,entity_id:entityID,field:'review_status',text},job_id:id(300+index),integrity_error:null,source_changed:false,not_authoritative:true,candidate_only:true,records:[{observation_id:id(400+index),entity_id:entityID,task_id:id(500+index),name,attributes:{'差异候选像素数':12,'候选说明':'需结合原件与人工核查'},review_state:'confirmed',source_time_verified:false,physical_state_verified:false}],truncated:false}};
}
function stateView(identity){return {background:null,state:{entity_id:identity,fields:{},gaps:[{field:'实地情况',reason:'missing'}],conflicts:[],status:'insufficient',physical_state:'unknown',current_state_verified:false,requirements_basis:'available_report_fields',required_fields:[],requirements_defined:false,field_freshness_seconds:{},excluded_records:[],unclocked_candidates:[{observation_id:id(401),source_id:id(601),source_clock_utc:'unknown',not_authoritative:true,message:'采集时间未知，未用于对象状态'}],candidates_truncated:false,truncated:false,evidence_ids:[],freshness_seconds:86400,authoritative_resource_version:1,rule_version:'reported-state-v5',state_hash:'a'.repeat(64),state_version:1,evaluated_at:'2026-10-21T01:00:00+00:00',history:[],history_truncated:false,input_issues:[],corrected_inputs:[],inputs_truncated:false}};}
function Button({children,primary,...props}){return React.createElement('button',{type:'button',...props},children);}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources forbidden in the synthetic DOM');}}
async function harness(run){
 const errors=[],violations=[],calls=[],opened=[],commits=[],gates=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://object-candidate-bridge.invalid/',pretendToBeVisual:true,resources:new NoResources(),virtualConsole:console});
 const storage=dom.window.localStorage,session=dom.window.sessionStorage,token='synthetic-object-bridge-token',base='/api/workspace/instances/'+instance+'/native-candidate-queries';
 const state={account:copy(account),who:copy(account),releaseID:release,detailGate:null,identityGate:null,queries:[savedQuery(),savedQuery({entityID:otherEntity,index:2,name:'林班乙的独立候选',text:'乙对象已核查候选'}),savedQuery({releaseID:otherRelease,index:3,name:'新发布版本的独立候选',text:'新版本已核查候选'}),savedQuery({actor:otherAccount.user_id,index:4,name:'另一账号的独立候选',text:'另一账号已核查候选'})]};
 Object.defineProperty(dom.window.navigator,'locks',{configurable:true,value:lockManager()});
 session.setItem('ga-setup-csrf',token);session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);
 const fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({method,path:url.pathname+url.search,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);assert.equal(method,'GET','the object-page bridge only reads saved records; no query POST, model or gateway');assert.equal(url.search,'');
   const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),state.account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),token);const respond=value=>new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
   if(url.pathname==='/api/me'){const snapshot=copy(state.who),gate=state.identityGate;if(gate&&snapshot.user_id===otherAccount.user_id&&!gate.started){gate.started=true;gate.request_actor=snapshot.user_id;await gate.promise;gate.returned=true;}return respond(snapshot);}
   if(url.pathname===base)return respond({schema:'native-candidate-query-list-v139',queries:state.queries.filter(row=>row.actor===state.who.user_id).map(row=>copy(row.value))});
   if(url.pathname.startsWith(base+'/')){
    const queryID=url.pathname.slice((base+'/').length),row=state.queries.find(row=>row.actor===state.who.user_id&&row.value.id===queryID);assert.ok(row,'detail reads a previously saved fixture for the actual actor');const snapshot=copy(row.value),gate=state.detailGate;if(gate)await gate.promise;return respond(snapshot);
   }
   const match=url.pathname.match(new RegExp('^/api/workspace/instances/'+instance+'/objects/([a-f0-9-]+)/state-background$'));
   if(match){assert.ok([entity,otherEntity].includes(match[1]));const original=stateView(match[1]).state,{state_hash,state_version,evaluated_at,history,history_truncated,...value}=original;return respond({...readOnlyBackground({actor:state.who.user_id,identity:match[1]}),workspace_id:account.workspace_id,instance_id:instance,stored_state:{version:state_version,state_hash,evaluated_at,value}});}
   throw Error('Unexpected bridge HTTP route '+method+' '+url.pathname);
  }catch(error){violations.push(error.stack||error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:storage,sessionStorage:session,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};const prior=Object.fromEntries(Object.keys(globals).map(key=>[key,Object.getOwnPropertyDescriptor(globalThis,key)]));for(const[key,value]of Object.entries(globals))Object.defineProperty(globalThis,key,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 let root;const container=dom.window.document.getElementById('root');
 try{
  const renderer=await import('react-dom/client'),actual=await component();assert.equal(actual.BundledReact,React);assert.equal(renderer.version,React.version);actual.setSession(token,account.workspace_id,account.user_id,account.username);
  const recordCommit=()=>commits.push({actor:state.account.user_id,release:state.releaseID,selected:container.querySelector('[aria-label="状态核查对象"]')?.value,candidateText:container.querySelector('.native-candidate-query-v139')?.textContent||''});
  const render=async()=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(React.Profiler,{id:'actual-object-state',onRender:recordCommit},React.createElement(actual.Studio,{account:state.account,instanceId:instance,releaseID:state.releaseID,objects:[object(entity,state.releaseID),object(otherEntity,state.releaseID)],Btn:Button,canWrite:true,formKey:'ga-v140-object:'+state.account.workspace_id+':'+state.account.user_id+':'+instance,onOpenTask:identity=>opened.push(identity)})))));await settle();};
  const settle=async(turns=12)=>{for(let index=0;index<turns;index++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const wait=async(predicate,label)=>{for(let index=0;index<100;index++){if(predicate())return;await settle(1);}assert.ok(predicate(),label+'; '+JSON.stringify({text:container.textContent.slice(0,1600),calls:calls.slice(-12),errors:errors.slice(0,1).map(error=>String(error.stack||error)),violations:violations.slice(0,1)}));};
  const mount=async()=>{root=renderer.createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});await render();};
  const button=label=>[...container.querySelectorAll('button')].find(node=>node.textContent===label),panel=()=>container.querySelector('.native-candidate-query-v139');
  const choose=async identity=>{await wait(()=>{const node=container.querySelector('[aria-label="状态核查对象"]');return node&&!node.disabled;},'the actual current-scope read must finish before object selection');const node=container.querySelector('[aria-label="状态核查对象"]');assert.ok(node);assert.equal(node.disabled,false);await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLSelectElement.prototype,'value').set.call(node,identity);node.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});await settle();assert.equal(container.querySelector('[aria-label="状态核查对象"]').value,identity);};
  const click=async label=>{const node=button(label);assert.ok(node,'actual button '+label);assert.equal(node.disabled,false);await act(async()=>node.click());await settle();};
  const ready=()=>wait(()=>!!panel()?.querySelector('[aria-label="候选搜索文字"]')&&!button('读取保存的查询')?.disabled,'object page exposes the real saved-candidate panel for its selected object');
  const openCandidate=async()=>{await ready();assert.ok(panel().textContent.includes('保存的查询历史（1）'),'only selected object/release saved history is offered');await click('查看这次查询');};
  const gate=()=>{const value=deferred();gates.push(value);return value;};
  await run({dom,state,calls,opened,commits,container,storage,session,base,mount,render,settle,wait,panel,choose,click,ready,openCandidate,gate,setSession:actual.setSession});
  assert.deepEqual(violations,[]);assert.deepEqual(errors,[]);assert.equal(calls.some(row=>row.method!=='GET'),false,'every bridge operation is read-only');
 }finally{gates.forEach(value=>value.resolve());if(root)await act(async()=>root.unmount());dom.window.close();for(const[key,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,key,descriptor);else delete globalThis[key];}}
}

test('actual object selection reveals same-object candidate history and original Task callback without any POST',()=>harness(async h=>{
 await h.mount();assert.equal(h.calls.length,0,'an unselected object does not run or read a candidate query');await h.choose(entity);await h.ready();assert.equal(h.panel().querySelector('summary').textContent,'查找已接收的变化候选');assert.ok(!h.panel().textContent.includes('乙对象已核查候选'));assert.ok(!h.panel().textContent.includes('新版本已核查候选'));
 await h.click('核对已保存状态');assert.ok(h.container.textContent.includes('尚无通过核对、可采用的登记字段'));assert.ok(h.container.textContent.includes('现场状态：尚未核实'));assert.ok(h.container.textContent.includes('未用于状态'));
 await h.openCandidate();await h.wait(()=>h.panel().textContent.includes('林班甲的原学习型变化候选'),'actual saved candidate and original review displayed');assert.ok(h.panel().textContent.includes('原业务人工核查：已核查'));assert.ok(h.panel().textContent.includes('采集时间未知'));await h.click('打开原业务任务与依据');assert.deepEqual(h.opened,[id(501)]);assert.ok(h.calls.some(row=>row.path===h.base+'/'+id(101)));assert.equal(h.calls.some(row=>row.method!=='GET'),false);
}));

for(const change of ['object','actor','release'])test('actual object-page '+change+' switch conceals the prior candidate on first commit and rejects its late detail',()=>harness(async h=>{
 await h.mount();await h.choose(entity);await h.openCandidate();await h.wait(()=>h.panel().textContent.includes('林班甲的原学习型变化候选'),'original candidate visible');
 const gate=h.gate();h.state.detailGate=gate;await h.click('查看这次查询');const boundary=h.commits.length;
 let expectedName,expectedRequest;
 if(change==='object'){
  h.state.detailGate=null;await h.choose(otherEntity);expectedName='林班乙的独立候选';expectedRequest='乙对象已核查候选';
 }else if(change==='actor'){
  h.state.account=copy(otherAccount);h.state.who=copy(otherAccount);h.state.detailGate=null;h.setSession('synthetic-object-bridge-token',otherAccount.workspace_id,otherAccount.user_id,otherAccount.username);await h.render();await h.choose(entity);expectedName='另一账号的独立候选';expectedRequest='另一账号已核查候选';
 }else{
  h.state.releaseID=otherRelease;h.state.detailGate=null;await h.render();expectedName='新发布版本的独立候选';expectedRequest='新版本已核查候选';
 }
 const first=h.commits.slice(boundary).find(row=>change==='object'?row.selected===otherEntity:change==='actor'?row.actor===otherAccount.user_id:row.release===otherRelease);assert.ok(first,'actual new scope committed');assert.ok(!first.candidateText.includes('林班甲的原学习型变化候选')&&!first.candidateText.includes('甲对象已核查候选'),'first new-scope commit must conceal prior candidate and saved search text');
 await h.ready();assert.ok(h.panel().textContent.includes(expectedRequest));assert.ok(!h.panel().textContent.includes('甲对象已核查候选'));await h.openCandidate();await h.wait(()=>h.panel().textContent.includes(expectedName),'new scope reads its own saved candidate');
 gate.resolve();await h.settle();assert.ok(h.panel().textContent.includes(expectedName));assert.ok(!h.panel().textContent.includes('林班甲的原学习型变化候选'));assert.equal(h.calls.some(row=>row.method!=='GET'),false);assert.deepEqual(h.opened,[]);
}));

test('V267 actor reset releases disposed scope busy while its real identity read remains deferred',()=>harness(async h=>{
 await h.mount();await h.choose(entity);await h.openCandidate();await h.wait(()=>h.panel().textContent.includes('林班甲的原学习型变化候选'),'original actor candidate is visible before the owner changes');
 const oldDetail=h.gate();h.state.detailGate=oldDetail;await h.click('查看这次查询');assert.equal(oldDetail.settled,false);
 const nextIdentity=h.gate();h.state.identityGate=nextIdentity;h.state.detailGate=null;h.state.queries.push(savedQuery({actor:otherAccount.user_id,entityID:otherEntity,index:5,name:'新账号乙对象的独立候选',text:'新账号乙对象原查询'}));const boundary=h.commits.length;
 h.state.account=copy(otherAccount);h.state.who=copy(otherAccount);h.setSession('synthetic-object-bridge-token',otherAccount.workspace_id,otherAccount.user_id,otherAccount.username);await h.render();
 await h.wait(()=>nextIdentity.started,'a real new-actor identity request has reached the explicitly deferred HTTP fixture');assert.equal(nextIdentity.request_actor,otherAccount.user_id);assert.equal(nextIdentity.settled,false);assert.ok(!nextIdentity.returned);
 await h.wait(()=>{const select=h.container.querySelector('[aria-label="状态核查对象"]');return select&&select.value==='';},'owner reset has removed the previous actor object selection');
 const first=h.commits.slice(boundary).find(row=>row.actor===otherAccount.user_id);assert.ok(first);assert.ok(!first.candidateText.includes('林班甲的原学习型变化候选')&&!first.candidateText.includes('甲对象已核查候选'),'first new-owner commit conceals previous private data');
 await h.wait(()=>!h.container.querySelector('[aria-label="状态核查对象"]').disabled,'disposed old-scope busy must not disable an empty current scope while an obsolete identity response is still pending');assert.equal(nextIdentity.settled,false);assert.ok(!nextIdentity.returned,'the selector did not become enabled merely because the deferred read completed');
 h.state.identityGate=null;await h.choose(otherEntity);await h.ready();await h.openCandidate();await h.wait(()=>h.panel().textContent.includes('新账号乙对象的独立候选'),'a new current object reads its actual independently scoped history');
 nextIdentity.resolve();oldDetail.resolve();await h.settle();assert.equal(h.container.querySelector('[aria-label="状态核查对象"]').value,otherEntity);assert.ok(h.panel().textContent.includes('新账号乙对象的独立候选'));assert.ok(!h.panel().textContent.includes('林班甲的原学习型变化候选')&&!h.panel().textContent.includes('甲对象已核查候选'),'late identity and original actor detail cannot restore obsolete data');assert.equal(h.calls.some(row=>row.method!=='GET'),false);assert.deepEqual(h.opened,[]);
}));
