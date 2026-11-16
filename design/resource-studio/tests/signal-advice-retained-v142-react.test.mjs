// Actual React DOM/StrictMode and runtime-api, with synthetic GET responses.
// This proves the retained read-only render boundary, not model/backend quality.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname,join,resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {webcrypto} from 'node:crypto';
import React,{act} from 'react';
import {JSDOM,ResourceLoader,VirtualConsole} from 'jsdom';
import {build} from 'vite';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let temporary,bundle;
after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});});
async function component(){
 if(!bundle)bundle=(async()=>{
  temporary=await mkdtemp(join(tmpdir(),'signal-advice-retained-react-'));
  const entry=join(temporary,'entry.mjs');
  await writeFile(entry,'export {default as Studio} from '+JSON.stringify(join(project,'src/SignalAdviceStudio.jsx'))+';\nexport {setSession} from '+JSON.stringify(join(project,'src/runtime-api.js'))+';\n');
  const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),mode:'test',logLevel:'silent',build:{ssr:entry,write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:id}}}});
  const chunk=result.output.find(item=>item.type==='chunk'&&item.isEntry);assert.ok(chunk);
  const output=join(temporary,'studio.mjs');await writeFile(output,chunk.code);return import(pathToFileURL(output).href);
 })();return bundle;
}
class NoResources extends ResourceLoader{fetch(){throw Error('External resources are forbidden');}}
function Button({children,primary,...props}){return React.createElement('button',props,children);}

test('retained saved manual-business advice remains readable when the current release becomes unavailable',async()=>{
 const errors=[],violations=[],console=new VirtualConsole();console.on('jsdomError',error=>errors.push(error));
 const dom=new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>',{url:'https://signal-advice-retained.invalid/',resources:new NoResources(),virtualConsole:console});
 const account={user_id:'14200000-0000-4000-8000-000000000001',workspace_id:'14200000-0000-4000-8000-000000000002'},signal={id:'14200000-0000-4000-8000-000000000003',instance_id:'14200000-0000-4000-8000-000000000004'},business='business_1420000000000001';
 const action={route:'manual_business',business_id:business,task_id:null,input_set:null,criteria:null,purpose:'保留原人工业务核查用途'};
 const row={id:'14200000-0000-4000-8000-000000000005',version:3,status:'needs_confirmation',created_at:'2026-10-21T02:00:00Z',progress:'原建议已保存，等待人工核对。',request:{instance_id:signal.instance_id,signal_id:signal.id,allowed_business_ids:[business],original_task:null,workflow_catalog:[],cloud_input:{references:[]}},draft:action,proposal:{summary:'原模型建议仍需人工核查，不代表现场事实。',findings:[],clarifications:['保留当时的处置范围'],unsupported_actions:[],action}};
 const formKey='synthetic-signal-scope',key=formKey+':advice:'+signal.id,original=JSON.stringify({selected:row.id,action,dirty:false,model:'',pending:null});
 dom.window.localStorage.setItem(key,original);
 const calls=[],fetch=async(raw,options={})=>{
  const url=new URL(String(raw),dom.window.location.href),method=options.method||'GET';calls.push({path:url.pathname+url.search,method,body:options.body});
  try{
   assert.equal(url.origin,dom.window.location.origin);assert.equal(method,'GET','retained history does not authorize a write');
   const headers=new Headers(options.headers);assert.equal(headers.get('X-Workspace-ID'),account.workspace_id);assert.equal(headers.get('X-CSRF-Token'),'synthetic-advice-csrf');
   let value;
   if(url.pathname==='/api/signal-advice'){
    assert.deepEqual([...url.searchParams.entries()],[['instance_id',signal.instance_id],['signal_id',signal.id]]);value=[row];
   }else{assert.equal(url.pathname,'/api/evidence-questions/models');assert.equal(url.search,'');value=[];}
   return new Response(JSON.stringify(value),{headers:{'Content-Type':'application/json'}});
  }catch(error){violations.push(error.message);throw error;}
 };
 const globals={window:dom.window,document:dom.window.document,navigator:dom.window.navigator,HTMLElement:dom.window.HTMLElement,Event:dom.window.Event,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,fetch,crypto:webcrypto,IS_REACT_ACT_ENVIRONMENT:true};
 const prior=Object.fromEntries(Object.keys(globals).map(name=>[name,Object.getOwnPropertyDescriptor(globalThis,name)]));
 for(const[name,value]of Object.entries(globals))Object.defineProperty(globalThis,name,{configurable:true,writable:true,value});dom.window.fetch=fetch;
 const container=dom.window.document.getElementById('root');let root;
 try{
  const {createRoot}=await import('react-dom/client'),actual=await component();actual.setSession('synthetic-advice-csrf',account.workspace_id,account.user_id,'synthetic-operator');
  root=createRoot(container,{onUncaughtError:error=>errors.push(error),onCaughtError:error=>errors.push(error),onRecoverableError:error=>errors.push(error)});
  const settle=async()=>{for(let turn=0;turn<12;turn++)await act(async()=>{await new Promise(resolve=>setImmediate(resolve));});};
  const render=async(release,canWrite)=>{await act(async()=>root.render(React.createElement(React.StrictMode,null,React.createElement(actual.Studio,{signal,formKey,release,Btn:Button,canWrite,disabled:!canWrite,onViewTask:()=>assert.fail('No task action was requested'),onViewAssessment:()=>assert.fail('No assessment action was requested')}))));await settle();};
  const control=label=>container.querySelector('[aria-label="'+label+'"]');
  const button=label=>{const found=[...container.querySelectorAll('button')].find(node=>node.textContent===label);assert.ok(found,'actual button '+label);return found;};
  await render({id:'14200000-0000-4000-8000-000000000006',snapshot:{businesses:[{id:business,name:'已确认人工核查业务'}]}},true);
  assert.deepEqual(errors,[]);assert.equal(control('建议接收业务')?.value,business);assert.ok(container.textContent.includes('已确认人工核查业务'));assert.ok(container.textContent.includes(row.proposal.summary));
  await render(null,false);
  assert.deepEqual(errors,[],'a directory failure must not crash the retained saved advice');
  const businessSelect=control('建议接收业务');assert.ok(businessSelect,'original saved business remains visible');assert.equal(businessSelect.value,business);assert.equal(businessSelect.disabled,true);
  const savedOption=[...businessSelect.options].find(option=>option.value===business);assert.equal(savedOption?.textContent,'原业务');assert.ok(!savedOption.textContent.includes(business));
  assert.equal(control('建议处置用途').value,action.purpose);assert.equal(control('建议处置用途').disabled,true);assert.ok(container.textContent.includes(row.proposal.summary));assert.ok(container.textContent.includes('建议待核对'));
  for(const label of ['核对外发文字','保存处置条件','确认并交接处置','停止等待本建议'])assert.equal(button(label).disabled,true,label+' stays blocked without a current release');
  const reads=calls.filter(call=>call.path.startsWith('/api/signal-advice?')).length;assert.equal(button('读取保存的建议与进度').disabled,false);
  await act(async()=>button('读取保存的建议与进度').click());await settle();assert.equal(calls.filter(call=>call.path.startsWith('/api/signal-advice?')).length,reads+1);
  assert.equal(control('建议接收业务').value,business);assert.equal(dom.window.localStorage.getItem(key),original,'the original local form is not rewritten from a missing current release');assert.equal(calls.filter(call=>call.method!=='GET').length,0);assert.deepEqual(violations,[]);assert.deepEqual(errors,[]);
 }finally{
  if(root)await act(async()=>root.unmount());dom.window.close();for(const[name,descriptor]of Object.entries(prior)){if(descriptor)Object.defineProperty(globalThis,name,descriptor);else delete globalThis[name];}
 }
});
