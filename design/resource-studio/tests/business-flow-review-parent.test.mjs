// Actual parent handlers/effects; only the child's guard return is a seam.
// The event harness does not establish React DOM, browser or DB evidence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {build} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {fixture,interrupted,id,account,memory,tabFor,missing,reseal,nodes,text} from './report-evidence-reading-fixture.mjs';

const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const originalSession=globalThis.sessionStorage;globalThis.sessionStorage??=tabFor();
let Panel,temporary;
test.before(async()=>{
 temporary=await mkdtemp(join(tmpdir(),'flow-review-parent-'));
 const result=await build({root:project,configFile:false,envDir:temporary,cacheDir:join(temporary,'cache'),logLevel:'silent',build:{ssr:join(project,'src/ReportEvidenceReadingDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:name=>name==='react'?pathToFileURL(join(project,'node_modules/react/index.js')).href:name}}}});
 const file=join(temporary,'parent.mjs');await writeFile(file,result.output.find(item=>item.isEntry).code);Panel=(await import(pathToFileURL(file))).ReportEvidenceReadingPanel;
});
test.after(async()=>{if(temporary)await rm(temporary,{recursive:true,force:true});if(originalSession===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=originalSession;});

test('actual parent preserves recovered working input, rechecks a late child guard and ignores superseded cleanup',async()=>{
 const f=await fixture(),draft=await interrupted(f),cache=memory(),tab=tabFor(),calls=[];
 const before={window:globalThis.window,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage};
 globalThis.window={addEventListener(){},removeEventListener(){}};globalThis.localStorage=cache;globalThis.sessionStorage=tab;
 const listing=await reseal({schema:'report-evidence-reading-list-v1',actor_id:account.user_id,workspace_id:account.workspace_id,items:[]},'list_hash');
 const control={close:0,guard:null,canUnload:null};const base='/setup/report-evidence-readings';
 const request=async(path,options={})=>{
  calls.push({path,method:options.method||'GET'});
  if(path==='/me')return account;
  if(path==='/setup/understanding/models')return f.models;
  if(path===base)return listing;
  if(path===base+'/'+draft.id)return draft;
  if(path==='/setup/report-drafts/active')return {blocked:false};
  if(path.startsWith('/setup/report-drafts/full-source-options/'))return f.options;
  throw missing();
 };
 // A failed report has no new-reading textarea. Recover an actual session's
 // working input instead of inventing a rendered control or setting dirty.
 const {ReportEvidenceReadingSession}=await import('../src/report-evidence-reading-session.mjs');
 const prior=new ReportEvidenceReadingSession({account,materialID:f.original_ref.id,request,storage:cache,tabStorage:tab,locks:{request:async(_name,_options,fn)=>fn()}});
 prior.adopt(draft,{catalog:f.catalog});
 const humanNote='父页尚未保存的要求，必须保留',write=cache.setItem;
 cache.setItem=()=>{throw Error('synthetic local write interruption');};
 assert.equal(prior.editForm({note:humanNote}),false);
 const memoryDraft=prior.capture();assert.ok(memoryDraft);assert.equal(memoryDraft.draft_id,draft.id);assert.equal(memoryDraft.form.note,humanNote);assert.equal(memoryDraft.dirty,true);assert.deepEqual(memoryDraft.form.selection,f.selection,'recovered source choice remains the actual original frozen version');
 cache.setItem=write;prior.dispose();
 const props={account,material:{id:f.original_ref.id,version:1,hash:f.original_ref.hash,name:'公开合成报告.docx'},Dialog:({children})=>children,Btn:({children})=>children,
  initialDraftID:draft.id,memoryDraft,request,storage:cache,tabStorage:tab,locks:{request:async(_name,_options,fn)=>fn()},prepareDraft:async()=>1,
  onClose:()=>control.close++,onRegisterCloseGuard:(guard,_capture,canUnload)=>{control.guard=guard;control.canUnload=canUnload;}};
 const ui=mount(Panel,props);
 try{
  let child;
  for(let turn=0;turn<100;turn++){
   await ui.flush();const tree=ui.render();child=nodes(tree).find(node=>typeof node.type==='function'&&node.type.name==='BusinessFlowReview');
   if(child)break;
   const alerts=nodes(tree).filter(node=>node.props.role==='alert').map(text);
   // Restored input appears before the original saved report finishes loading.
   // Until then options is still null, so this exact retention warning is valid.
   assert.ok(alerts.every(message=>message==='保留的版本已不在当前清单；输入仍保留，新预览不会自动替换。'),'only the exact pre-load source-choice retention notice is allowed: '+alerts.join(' | '));
  }
  assert.ok(child,'real failed report renders repair child');
  assert.deepEqual(nodes(ui.render()).filter(node=>node.props.role==='alert').map(text),[],'after loading the original report no source or integrity error remains');
  assert.equal(child.props.producerId,draft.id);assert.equal(child.props.actorId,account.user_id);assert.equal(child.props.workspaceId,account.workspace_id);
  const old=()=>true;let safe=false;const current=()=>safe;
  child.props.onRegisterGuard(old);child.props.onRegisterGuard(current);child.props.onRegisterGuard(null,old);
  assert.equal(control.guard(),false,'old cleanup cannot clear newer guard');assert.equal(control.canUnload(),false);
  const initialReads=calls.filter(row=>row.path===base).length;
  await ui.click('重新读取服务器保存状态');assert.equal(calls.filter(row=>row.path===base).length,initialReads,'parent operate cannot change report while child unsafe');
  await ui.click('返回资料，保留原稿');assert.equal(control.close,0);
  const recovered=()=>Array.from({length:cache.length},(_,index)=>JSON.parse(cache.getItem(cache.key(index)))).find(value=>value?.owner!==memoryDraft.owner&&value?.draft_id===draft.id&&value?.dirty===true&&value?.form?.note===humanNote);
  assert.ok(recovered(),'actual parent recovered and reliably persisted the original working input');
  safe=true;
  assert.equal(control.guard(),false,'parent human input opens its own leave prompt');
  assert.ok(nodes(ui.render()).some(node=>node.props['aria-label']==='离开证据读取前保护输入'));
  safe=false;await ui.click('保留本机稿并返回');assert.equal(control.close,0,'late local failure/busy in child blocks pending parent leave action');
  assert.ok(recovered(),'blocked leave preserves the recovered working input');
  assert.ok(nodes(ui.render()).some(node=>node.props['aria-label']==='离开证据读取前保护输入'),'leave prompt remains open');
  safe=true;await ui.click('保留本机稿并返回');assert.equal(control.close,1,'after child protection succeeds the same explicit leave may proceed');
  props.account={...account,user_id:id(99)};
  const hidden=ui.render();assert.match(text(hidden),/暂不显示私人报告内容/);
  assert.equal(text(hidden).includes(humanNote),false);
  assert.equal(calls.filter(row=>row.method!=='GET').length,0,'navigation recovery creates no provider or write request');
 }finally{ui.unmount();Object.assign(globalThis,before);}
});
