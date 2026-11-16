// Explicit unit API/storage seams for the new source bridge. Actual API/worker
// and parent-component persistence remain Root's separate runtime acceptance.
import test,{after,before} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(join(project,'package.json'));
let directory,Controller;
before(async()=>{
 directory=await mkdtemp(join(tmpdir(),'case-reference-import-v210-'));
 const vite=createRequire(req.resolve('vite/package.json')),{build}=await import(pathToFileURL(vite.resolve('esbuild')).href);
 const out=join(directory,'controller.mjs');await build({entryPoints:[join(project,'src/runtime-task-document-v207-controller.mjs')],outfile:out,bundle:true,format:'esm',platform:'node',logLevel:'silent',plugins:[{name:'explicit-unused-browser-api-seam',setup(b){b.onResolve({filter:/^\.\/runtime-api$/},()=>({path:'runtime-api',namespace:'unit-only'}));b.onLoad({filter:/.*/,namespace:'unit-only'},()=>({loader:'js',contents:"export const json=()=>{throw Error('Use the explicit unit transport');};export const api=()=>{throw Error('Binary IO not part of this unit');};export const hashBytes=async()=>{throw Error('No command hash in read-only staging');};"}));}}]});
 Controller=(await import(pathToFileURL(out).href)).TaskRuntimeDocumentControllerV207;
});
after(async()=>{if(directory)await rm(directory,{recursive:true,force:true});});
const id=n=>'21000000-0000-4000-8000-'+String(n).padStart(12,'0'),hash='a'.repeat(64);
const selector=(version=16,block='b1')=>({kind:'native',version_id:id(version),attempt_id:id(17),parse_hash:hash,block_id:block,char_span:{start:0,end:10}});
class Memory{
 constructor(){this.values=new Map();this.fail=()=>false;}
 getItem(key){return this.values.get(key)??null;}
 setItem(key,value){if(this.fail(key))throw Error('explicit storage fault');this.values.set(key,String(value));}
 removeItem(key){this.values.delete(key);}
}
function fixture(){
 const storage=new Memory(),session=new Memory(),account={user_id:id(1),workspace_id:id(2)},task={id:id(3),instance_id:id(4),instance_release_id:id(5)};
 session.setItem('ga-setup-csrf-user',account.user_id);session.setItem('ga-setup-csrf-workspace',account.workspace_id);session.setItem('ga-setup-csrf','synthetic-only');
 const selectors=[selector(),selector(16,'b2')],calls=[],context={can_select:true,input_ports:[{key:'runtime_sources'}]};
 const use={schema:'layered-case-use-view-v210',id:id(10),target:{task_id:task.id,instance_id:task.instance_id,release_id:task.instance_release_id},status:'active',stage:{code:'ready'},document:{version_id:id(16),name:'案例参考.txt',ack_verified:true},selectors,permissions:{stage_sources:true}};
 const version={id:id(16),version:2,document_id:id(18),instance_id:task.instance_id,sequence:1,name:'案例参考.txt',status:'parsed',raw_saved:true,raw_sha256:hash,parse_hash:hash,blocks_count:2,warnings:[],can_retry:false,can_cancel:false,latest_attempt_id:id(17),delivery:{event_id:id(19),version:1,status:'received',verified:true,can_retry:false,error:null,previous_error:null}};
 const controller=new Controller({account,task,storage,session,locks:{request:async(_key,_options,action)=>action()},request:async(path,options={})=>{
  calls.push({path,method:options.method||'GET'});
  if(path==='/me')return {user_id:account.user_id,workspace_id:account.workspace_id};
  if(path.includes('/layered-case-uses-v210/uses/'))return structuredClone(use);
  if(path==='/runtime-documents/versions/'+version.id)return structuredClone(version);
  throw Error('Unspecified unit transport');
 }});
 controller.loading=false;controller.context=context;
 controller.refresh=async()=>{controller.context=context;}; // independent already-validated source read seam
 controller.draft.input_key='runtime_sources';controller.draft.reason='保留原人工采用理由';
 const old=selector(6);controller.draft.selectors=[old];controller.draft.labels=[{source_name:'当前普通资料',version_sequence:2,location_label:'原文块1'}];
 return {controller,storage,session,use,selectors,calls,context,old};
}
test('stage full reference preserves local and already adopted ordinary sources without any write request',async()=>{
 const f=fixture(),server=selector(7);f.controller.applied.runtime_sources={entries:[{selector:server,source_name:'审批原资料',version_sequence:1,location_label:'原文块1'}]};
 f.controller.draft.source_set_id=id(80);f.controller.set={id:id(80)};
 const result=await f.controller.importCaseReference({useID:f.use.id,selectors:f.selectors});
 assert.equal(result.selector_count,4);assert.deepEqual(f.controller.draft.selectors,[f.old,server,...f.selectors]);
 assert.equal(f.controller.draft.reason,'保留原人工采用理由');assert.equal(f.controller.draft.source_set_id,'');assert.equal(f.controller.set,null);
 assert.ok(f.calls.every(call=>call.method==='GET'));assert.match(f.controller.notice,/尚未保存资料集/);
 const snapshot=structuredClone(f.controller.draft);await f.controller.importCaseReference({useID:f.use.id,selectors:f.selectors});assert.deepEqual(f.controller.draft,snapshot);
});
test('unknown original request in another window prevents staging and preserves existing draft',async()=>{
 const f=fixture(),before=structuredClone(f.controller.draft);f.storage.setItem(f.controller.key+':pending','retained original request');
 await assert.rejects(f.controller.importCaseReference({useID:f.use.id,selectors:f.selectors}),/另一窗口/);
 assert.deepEqual(f.controller.draft,before);assert.equal(f.calls.length,0);
});
test('cross-task or changed full range cannot be staged',async()=>{
 for(const kind of ['task','range']){
  const f=fixture(),before=structuredClone(f.controller.draft);if(kind==='task')f.use.target.task_id=id(99);else f.use.selectors=[f.selectors[0]];
  await assert.rejects(f.controller.importCaseReference({useID:f.use.id,selectors:f.selectors}),/案例参考/);assert.deepEqual(f.controller.draft,before);
 }
});
test('full reference capacity refusal never truncates or overwrites ordinary choices',async()=>{
 const f=fixture();f.controller.draft.selectors=Array.from({length:19},(_,n)=>selector(100+n));f.controller.draft.labels=f.controller.draft.selectors.map(()=>({source_name:'普通资料',version_sequence:1,location_label:'原文块1'}));const before=structuredClone(f.controller.draft);
 await assert.rejects(f.controller.importCaseReference({useID:f.use.id,selectors:f.selectors}),/超过20项/);assert.deepEqual(f.controller.draft,before);
});
test('storage failure after backup retains original memory and independently saved human draft',async()=>{
 const f=fixture(),before=structuredClone(f.controller.draft);f.storage.fail=key=>key===f.controller.key+':draft';
 await assert.rejects(f.controller.importCaseReference({useID:f.use.id,selectors:f.selectors}),/原稿已独立保留/);
 assert.deepEqual(f.controller.draft,before);assert.equal(f.controller.localSaved,false);
 const saved=[...f.storage.values.entries()].filter(([key])=>key.includes(':draft-retained:')).map(([,raw])=>JSON.parse(raw));assert.equal(saved.length,1);assert.deepEqual(saved[0].draft,before);assert.ok(f.calls.every(call=>call.method==='GET'));
});
test('partial same-reference choices require explicit resolution instead of silent replacement',async()=>{
 const f=fixture();f.controller.draft.selectors=[{...f.selectors[0],char_span:{start:0,end:5}}];f.controller.draft.labels=[{source_name:'此前手选片段',version_sequence:1,location_label:'原文块1'}];const before=structuredClone(f.controller.draft);
 await assert.rejects(f.controller.importCaseReference({useID:f.use.id,selectors:f.selectors}),/其他片段/);assert.deepEqual(f.controller.draft,before);
});
