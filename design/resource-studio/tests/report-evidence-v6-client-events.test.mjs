// Root executes these actual JSX/session handlers with public synthetic DTOs.
// The request stubs below do not establish API/auth/model/browser correctness.
import test from 'node:test';import assert from 'node:assert/strict';
import {readFile,mkdtemp,writeFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';import {build} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {memory,tabFor,missing,reseal,nodes,text} from './report-evidence-v4-fixture.mjs';
import {copy,digest,evidenceCommandHash,verifyEvidenceDraft} from '../src/report-evidence-reading-contract.mjs';
const priorSession=globalThis.sessionStorage;globalThis.sessionStorage??=tabFor();
const {ReportEvidenceReadingSession}=await import('../src/report-evidence-reading-session.mjs');
let Panel,Editor,Preview,temporary;
const directory=new URL('./fixtures/report-evidence-v6/',import.meta.url),base='/setup/report-evidence-readings';
async function fixture(){const f={};for(const name of ['preview','mapped','reduced'])f[name]=JSON.parse(await readFile(new URL(name+'.json',directory),'utf8'));f.account={user_id:f.preview.actor_id,workspace_id:f.preview.workspace_id,role:'admin'};return f;}
async function reviewReply(row,body,account){const receipt=await reseal({schema:'report-evidence-review-receipt-v1',kind:'report_evidence_review',actor_id:account.user_id,workspace_id:account.workspace_id,draft_id:row.id,request_id:body.request_id,accepted_revision:body.revision+1,value_hash:await digest(body.value),command_hash:await evidenceCommandHash(account,'review',row.id,body)},'receipt_hash'),draft=copy(row);draft.revision=body.revision+1;draft.review_value=copy(body.value);draft.review_history.push({receipt,value:copy(body.value)});await reseal(draft,'view_hash');return {draft,receipt,current_matches:true};}
test.before(async()=>{temporary=await mkdtemp(join(tmpdir(),'evidence-v6-events-'));const built=await build({configFile:false,envDir:temporary,logLevel:'silent',cacheDir:join(temporary,'cache'),build:{ssr:resolve('src/ReportEvidenceReadingDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:id}}}}),path=join(temporary,'dialog.mjs');await writeFile(path,built.output.find(o=>o.isEntry).code);const module=await import(pathToFileURL(path));Panel=module.ReportEvidenceReadingPanel;Editor=module.EvidenceCandidateEditor;Preview=module.EvidenceCallPreview;});
test.after(async()=>{await rm(temporary,{recursive:true,force:true});if(priorSession===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorSession;});
async function ready(ui,predicate){for(let i=0;i<100;i++){await ui.flush();const tree=ui.render();if(predicate(tree)&&!text(tree).includes('正在核对来源或保存记录'))return tree;const errors=nodes(tree).filter(n=>n.props.role==='alert').map(text);if(errors.length)assert.fail(errors.join(' | '));}assert.fail('Actual GEN6 panel did not reach its observable state');}
async function environment(run){
 const f=await fixture(),row=copy(f.reduced),storage=memory(),tab=tabFor(f.account),calls=[],control={row,hold:false},prior={window:globalThis.window,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage};globalThis.window={addEventListener(){},removeEventListener(){}};globalThis.localStorage=storage;globalThis.sessionStorage=tab;
 const list=await reseal({schema:'report-evidence-reading-list-v1',actor_id:f.account.user_id,workspace_id:f.account.workspace_id,items:[]},'list_hash'),props={account:f.account,material:{id:f.preview.body.selection.original_ref.id,version:f.preview.body.selection.original_ref.version,hash:f.preview.body.selection.original_ref.hash,name:'新空库合成GEN6报告.docx'},initialDraftID:row.id,Dialog:({children})=>children,Btn:({children})=>children,prepareDraft:async()=>{assert.fail('A saved candidate must not save configuration');},onClose(){},storage,tabStorage:tab,locks:{request:async(_n,_o,fn)=>fn()},request:async(path,options={})=>{
  calls.push({path,...options});if(path==='/me')return f.account;if(path==='/setup/understanding/models')return [{id:f.preview.body.model_id,name:'合成已登记模型',model:f.preview.model}];if(path===base)return list;if(path===base+'/'+row.id)return control.row;
  if(path.includes('/review-receipts/')){if(control.reply&&path.endsWith(control.reply.receipt.request_id))return control.reply;throw missing();}
  if(path.endsWith('/review')&&options.method==='PUT'){control.body=JSON.parse(options.body);if(control.hold)await new Promise(resolve=>{control.release=resolve;});control.reply=await reviewReply(control.row,control.body,f.account);control.row=control.reply.draft;return control.reply;}
  throw missing();
 }},ui=mount(Panel,props);
 try{await ready(ui,t=>nodes(t).some(n=>n.props['data-evidence-draft-id']===row.id));await run({f,row,ui,props,calls,control,storage});}finally{ui.unmount();Object.assign(globalThis,prior);}
}

test('GEN6 preview describes whole POST size and a separately authorized summary without exposing transport internals',async()=>{
 const f=await fixture();let starts=0;const props={preview:f.preview,busy:false,writable:true,pending:[{}],onStart(){starts++;}},ui=mount(Preview,props);try{
  const value=text(ui.render());assert.match(value,/发送内容大小（正文和格式要求）/);assert.ok(value.includes(f.preview.input_summary.prepared_request_bytes.toLocaleString()));assert.match(value,/届时单独预览并明确授权/);assert.doesNotMatch(value,/尚不支持汇总|prepared_request_bytes|tool_choice|beta\/chat|map-v6|compiler/);
  assert.equal(ui.button('同意本次完整范围并开始提取事实').props.disabled,true);assert.equal(starts,0);props.pending=[];ui.render();await ui.click('同意本次完整范围并开始提取事实');assert.equal(starts,1);
 }finally{ui.unmount();}
});

test('GEN6 actual parent saves the complete indexed human value and cold-mounts the exact server result',async()=>environment(async({row,ui,props,calls,control})=>{
 const original=copy(row.model_result),child=nodes(ui.render()).find(n=>n.type===Editor&&n.props.writable);assert.ok(child);const before=copy(child.props.value),editor=mount(Editor,child.props);
 try{await editor.input('result.global_claims.0.statement','人工核对跨业务职责末字六');await editor.input('result.businesses.0.steps.0.role','人工核对负责人');await editor.input('.note','完整人工说明末字六');}finally{editor.unmount();}
 ui.render();await ui.click('保存完整人工稿并核对服务器回执');await ready(ui,t=>text(t).includes('服务器已保存人工稿'));
 const expected=copy(before);expected.result.global_claims[0].statement='人工核对跨业务职责末字六';expected.result.businesses[0].steps[0].role='人工核对负责人';expected.note='完整人工说明末字六';assert.deepEqual(control.body.value,expected);
 assert.deepEqual(control.row.model_result,original);await verifyEvidenceDraft(control.row,props.account);assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.equal(calls.filter(c=>c.method==='POST').length,0);
 const reopened=mount(Panel,{...props,storage:memory()});try{await ready(reopened,t=>nodes(t).some(n=>n.type===Editor&&n.props.writable&&n.props.value.note===expected.note));assert.deepEqual(nodes(reopened.render()).find(n=>n.type===Editor&&n.props.writable).props.value,expected);}finally{reopened.unmount();}
}));

test('GEN6 unknown private save preserves original command3 and later input while the original receipt wins',async()=>{
 const f=await fixture(),storage=memory(),calls=[],control={row:copy(f.reduced),reply:null},session=new ReportEvidenceReadingSession({account:f.account,materialID:f.preview.body.selection.original_ref.id,storage,tabStorage:tabFor(f.account),locks:{request:async(_n,_o,fn)=>fn()},request:async(path,options={})=>{
  calls.push({path,...options});if(path==='/me')return f.account;if(path===base+'/'+control.row.id)return control.row;if(path.includes('/review-receipts/')){if(control.reply)return control.reply;throw missing();}if(options.method==='PUT'){throw Object.assign(Error('synthetic transport unknown'),{status:0});}throw missing();
 }});try{
  await session.load(control.row.id);const value=copy(session.state.value);value.note='原人工说明甲';session.editValue(value);await assert.rejects(session.save());const pending=copy(session.state.pending),raw=JSON.stringify(pending);assert.equal(pending.schema,'report-evidence-reading-command-v3');assert.equal(pending.binding.map_inputs_hash,null);
  const later=copy(session.state.value);later.note='后来人工说明乙';session.editValue(later);await assert.rejects(session.save());assert.equal(JSON.stringify(session.state.pending),raw);assert.equal(calls.filter(c=>c.method==='PUT').length,1);
  control.reply=await reviewReply(control.row,pending.body,f.account);control.row=control.reply.draft;await session.save({probeOnly:true});assert.equal(session.state.pending,null);assert.equal(session.state.value.note,'后来人工说明乙');assert.equal(session.state.dirty,true);assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.equal(calls.some(c=>c.method==='POST'),false);
 }finally{session.dispose();}
});

test('GEN6 late accepted handler cannot reveal private input after actor replacement',async()=>environment(async({ui,props,calls,control,storage})=>{
 const child=nodes(ui.render()).find(n=>n.type===Editor&&n.props.writable),editor=mount(Editor,child.props);await editor.input('.note','原账号私人末字六');editor.unmount();ui.render();control.hold=true;const operation=ui.button('保存完整人工稿并核对服务器回执').props.onClick();for(let i=0;i<100&&!control.release;i++)await ui.flush();assert.ok(control.release);
 props.account={...props.account,user_id:'10000000-0000-4000-8000-000000000099'};assert.match(text(ui.render()),/暂不显示私人报告内容/);control.release();await operation;await ui.flush();const frame=ui.render();assert.match(text(frame),/暂不显示私人报告内容/);assert.equal(nodes(frame).some(n=>n.type===Editor||n.props.value==='原账号私人末字六'),false);assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.ok(Array.from({length:storage.length},(_,i)=>storage.getItem(storage.key(i))).some(v=>v.includes('原账号私人末字六')));
}));
