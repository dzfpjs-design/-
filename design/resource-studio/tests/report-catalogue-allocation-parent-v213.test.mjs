// Synthetic controller/DOM integration, not real source, PG, HTTP or paid proof.
import test,{after} from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,dirname,resolve} from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {catalogueFixture,cleanup,ids,document as documentValue,consumer,origin,scope,revision,stamp,digest,id} from './report-business-catalogue-v211-fixture.mjs';
import {Storage} from './report-catalogue-allocation-v213-fixture.mjs';
import {canonical,blankEditor} from '../src/report-business-catalogue-v211-protocol.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..'),requireProject=createRequire(join(project,'package.json'));
const originalCrypto=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
let temporary,loading;
async function controllerClass(){
 if(!loading)loading=(async()=>{temporary=await mkdtemp(join(tmpdir(),'catalogue-parent-v213-'));const vite=createRequire(requireProject.resolve('vite/package.json')),{build}=await import(pathToFileURL(vite.resolve('esbuild')).href),target=join(temporary,'controller.mjs');
  await build({entryPoints:[join(project,'src/report-business-catalogue-v211-controller.mjs')],outfile:target,bundle:true,format:'esm',platform:'node',logLevel:'silent',plugins:[{name:'no-network-parent',setup(b){b.onResolve({filter:/^\.\/runtime-api$/},()=>({path:'runtime',namespace:'parent-test'}));b.onLoad({filter:/.*/,namespace:'parent-test'},()=>({loader:'js',contents:"export const json=()=>{throw Error('No real network allowed')};export const hashBytes=async b=>Buffer.from(await crypto.subtle.digest('SHA-256',b)).toString('hex');"}));}}]});return (await import(pathToFileURL(target).href)).ReportBusinessCatalogueControllerV211;})();return loading;
}
after(async()=>{await cleanup();if(temporary)await rm(temporary,{recursive:true,force:true});if(originalCrypto)Object.defineProperty(globalThis,'crypto',originalCrypto);else delete globalThis.crypto;});
const copy=v=>structuredClone(v);
function detail(version=1,doc=documentValue()){
 const hash=digest(doc);return {schema:'report-business-catalogue-draft-view-v211',id:ids.draft,version,actor_id:ids.actor,workspace_id:ids.workspace,record_hash:digest({synthetic_record:version,hash}),document_hash:hash,origin:origin(),document:copy(doc),plan_ref:null,
  permissions:{can_save:true,can_confirm:false,requires_current_source_check:true},validation:{unassigned_positions:[],pending_ownership_positions:[],open_missing_info_keys:['frequency'],issues:[]},
  history:Array.from({length:version},(_,index)=>({version:index+1,operation:index?'save':'create',request_id:id(50+index),document_hash:index===version-1?hash:digest(documentValue()),saved_at:stamp,actions:[]}))};
}
async function fixture(){
 const Controller=await controllerClass(),storage=new Storage(),session=new Storage(),account={user_id:ids.actor,workspace_id:ids.workspace,role:'operator'},state={head:detail(),calls:[]};
 for(const[k,v]of Object.entries({'ga-setup-csrf-user':ids.actor,'ga-setup-csrf-workspace':ids.workspace,'ga-setup-csrf':'parent-test-only'}))session.setItem(k,v);
 const request=async(path,options={})=>{state.calls.push({path,method:options.method||'GET'});assert.notEqual(options.method,'POST','Parent adapter cannot perform an old save or provider call');if(path==='/me')return copy(account);if(path.includes('/drafts?'))return {schema:'report-business-catalogue-draft-list-v211',consumer_id:ids.consumer,offset:0,limit:20,has_more:false,records:[{id:ids.draft,version:state.head.version,record_hash:state.head.record_hash,document_hash:state.head.document_hash,origin:origin(),plan_ref:null}]};if(path.endsWith('/drafts/'+ids.draft))return copy(state.head);throw Error('Unexpected parent-only route '+path);};
 const c=new Controller({producerId:ids.producer,revision,account,consumer:consumer(),request,storage,session,locks:{request:(_k,_o,fn)=>fn()},sha:async bytes=>Buffer.from(await webcrypto.subtle.digest('SHA-256',bytes)).toString('hex')});
 c.loading=false;c.draftId=ids.draft;c.server=copy(state.head);c.draft={document:copy(state.head.document),actions:[],base_version:1,base_record_hash:state.head.record_hash,base_document_hash:state.head.document_hash,instruction:'',undo:null,editor:blankEditor()};c.persist();
 c.write(c.key+':selection',{schema:'report-business-catalogue-selection-v211',scope:scope(),draft_id:ids.draft});
 const reference={id:ids.draft,workspace_id:ids.workspace,actor_id:ids.actor,version:1,record_hash:c.server.record_hash,document_hash:c.server.document_hash,origin_hash:'9'.repeat(64)},context={schema:'report-business-catalogue-allocation-context-v213',producer_id:ids.producer,revision,draft_ref:reference};
 return {Controller,c,storage,session,account,state,reference,context,request};
}

test('parent snapshot binds only the real seven-field context and fences all human buffers',async()=>{
 const f=await fixture(),c=f.c;assert.equal(c.allocationSnapshot().draft_ref.origin_hash,undefined);
 assert.equal(c.bindAllocationContext({...f.context,draft_ref:{...f.reference,document_hash:'0'.repeat(64)}}),false);
 assert.equal(c.bindAllocationContext({...f.context,draft_ref:{...f.reference,actor_id:id(999)}}),false);
 assert.equal(c.bindAllocationContext(f.context),true);const original=c.allocationSnapshot();assert.deepEqual(original.draft_ref,f.reference);
 for(const change of [d=>d.instruction='后来要求',d=>d.editor.group_keys=['patrol_requirements'],d=>d.undo={document:documentValue(),actions:[]},d=>d.editor.assignment.reason='后来填写的说明']){c.draft=copy(original.local_draft);change(c.draft);assert.equal(c.allocationIsCurrent(original),false);}
 c.draft=copy(original.local_draft);assert.equal(c.allocationIsCurrent(original),true);c.server=detail(2);assert.equal(c.allocationSnapshot().draft_ref.origin_hash,undefined,'head changes invalidate the old binding');
});

test('parent apply backup is read back before adoption and no legacy save is sent',async()=>{
 const old=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
 try{const f=await fixture(),c=f.c;c.bindAllocationContext(f.context);const s=c.allocationSnapshot();assert.equal(await c.backupBeforeAllocation(s),true);
  const next=documentValue();next.allocation_groups[0].reason='待核的归类建议已经由用户明确采纳';f.state.head=detail(2,next);
  assert.equal(await c.adoptAllocationSaved(f.state.head,f.reference),true);assert.deepEqual(c.draft.document,next);assert.equal(c.draft.base_version,2);assert.equal(c.staleBase(),false);
  assert.ok([...f.storage.data.keys()].some(k=>k.includes(':backup:')));assert.ok(f.state.calls.every(call=>call.method==='GET'));
 }finally{if(old)Object.defineProperty(globalThis,'crypto',old);else delete globalThis.crypto;}
});

test('later instruction undo and selection survive server adoption and a fresh controller reopen',async()=>{
 const old=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
 try{const f=await fixture(),c=f.c;c.bindAllocationContext(f.context);await c.backupBeforeAllocation(c.allocationSnapshot());
  c.draft.instruction='请求送出后后来填写的完整要求😀';c.draft.editor.group_keys=['patrol_requirements'];c.draft.undo={document:documentValue(),actions:[]};c.persist();const later=copy(c.draft);
  const next=documentValue();next.allocation_groups[0].reason='服务器已保存的所选建议';f.state.head=detail(2,next);
  assert.equal(await c.adoptAllocationSaved(f.state.head,f.reference),true);assert.deepEqual(c.draft,later);assert.equal(c.server.version,2);assert.equal(c.staleBase(),true);assert.match(c.notice,/后来本机修改仍完整保留/);
  const reopened=new f.Controller({producerId:ids.producer,revision,account:f.account,consumer:consumer(),request:f.request,storage:f.storage,session:f.session,locks:{request:(_k,_o,fn)=>fn()},sha:async b=>Buffer.from(await webcrypto.subtle.digest('SHA-256',b)).toString('hex')});await reopened.start();
  assert.deepEqual(reopened.draft,later);assert.equal(reopened.server.version,2);assert.equal(reopened.staleBase(),true);assert.ok([...f.storage.data.values()].some(raw=>raw.includes(later.instruction)));
 }finally{if(old)Object.defineProperty(globalThis,'crypto',old);else delete globalThis.crypto;}
});

test('backup write failure and wrong-target result cannot advance or overwrite a human draft',async()=>{
 const old=Object.getOwnPropertyDescriptor(globalThis,'crypto');Object.defineProperty(globalThis,'crypto',{configurable:true,value:webcrypto});
 try{const f=await fixture(),c=f.c;c.bindAllocationContext(f.context);const before=copy(c.draft);f.storage.fail=(op,key)=>op==='set'&&key.includes(':allocation-before-v213:');
  await assert.rejects(()=>c.backupBeforeAllocation(c.allocationSnapshot()),/storage write failed/);assert.equal(c.localSaved,false);assert.deepEqual(c.draft,before);assert.equal(f.state.calls.length,0);
  f.storage.fail=null;await assert.rejects(()=>c.adoptAllocationSaved({...detail(2),id:id(999)},f.reference),/当前人工目录|保存版本/);assert.deepEqual(c.draft,before);
 }finally{if(old)Object.defineProperty(globalThis,'crypto',old);else delete globalThis.crypto;}
});

test('actual editor mounts the inline entry on demand and write guard closes on unavailable inventory',async()=>{
 await catalogueFixture({existing:true,injectedPersistence:true},async h=>{assert.equal(h.state.calls.some(c=>c.path.includes('allocation-context')),false);
  await h.click('自动整理未归类要求');await h.until(()=>h.state.calls.some(c=>c.path.includes('allocation-context')),'inline allocation reads actual parent-scoped route');
  await h.until(()=>h.state.guard?.({write:true})===false,'unavailable inventory cannot grant another paid activity');assert.equal(h.state.posts.length,0);
 });
});

test('a cold editor restore mounts an unknown allocation journal instead of omitting its guard',async()=>{
 await catalogueFixture({existing:true,injectedPersistence:true},async h=>{
  const prefix='ga-catalogue-allocation-v213:'+ids.workspace+':'+ids.actor+':'+ids.producer+':'+ids.draft;h.storage.setItem(prefix+':pending','damaged original pointer');
  await h.unmount();await h.mount({reloadModule:true});await h.until(()=>h.container.querySelector('[aria-label="自动整理未归类要求"]'),'original allocation scope restored');await h.until(()=>h.state.guard?.()===false,'unknown original request retains its leave guard');assert.equal(h.state.posts.length,0);
 });
});
