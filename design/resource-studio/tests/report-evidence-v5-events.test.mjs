import {listV2} from './report-evidence-list-test-helper.mjs';
// Actual component handlers with public synthetic server DTOs/test doubles.
// Root runs these; they do not establish live API, model quality or browser UX.
import test from 'node:test';import assert from 'node:assert/strict';
import {readFile,mkdtemp,writeFile,rm} from 'node:fs/promises';import {tmpdir} from 'node:os';import {resolve,join} from 'node:path';import {pathToFileURL} from 'node:url';import {build} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {memory,tabFor,missing,reseal,nodes,text} from './report-evidence-v4-fixture.mjs';
import {copy,digest,evidenceCommandHash,verifyEvidenceDraft} from '../src/report-evidence-reading-contract.mjs';
const priorSession=globalThis.sessionStorage;globalThis.sessionStorage??=tabFor();let Panel,Editor,Preview,ReductionPreview,temporary;
const directory=new URL('./fixtures/report-evidence-v5/',import.meta.url),base='/setup/report-evidence-readings';
async function fixture(){const f={};for(const name of ['preview','mapped','reduced','reduce-preview'])f[name]=JSON.parse(await readFile(new URL(name+'.json',directory),'utf8'));f.account={user_id:f.preview.actor_id,workspace_id:f.preview.workspace_id,role:'admin'};return f;}
test.before(async()=>{temporary=await mkdtemp(join(tmpdir(),'evidence-v5-events-'));const built=await build({configFile:false,envDir:temporary,logLevel:'silent',cacheDir:join(temporary,'cache'),build:{ssr:resolve('src/ReportEvidenceReadingDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:id}}}}),path=join(temporary,'dialog.mjs');await writeFile(path,built.output.find(o=>o.isEntry).code);const module=await import(pathToFileURL(path));Panel=module.ReportEvidenceReadingPanel;Editor=module.EvidenceCandidateEditor;Preview=module.EvidenceCallPreview;ReductionPreview=module.EvidenceReductionPreview;});
test.after(async()=>{await rm(temporary,{recursive:true,force:true});if(priorSession===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=priorSession;});
async function ready(ui,predicate){for(let i=0;i<100;i++){await ui.flush();const tree=ui.render();if(predicate(tree)&&!text(tree).includes('正在核对来源或保存记录'))return tree;const errors=nodes(tree).filter(n=>n.props.role==='alert').map(text);if(errors.length)assert.fail(errors.join(' | '));}assert.fail('Actual Gen5 panel did not reach its observable state');}
async function environment(run,kind='reduced'){
 const f=await fixture(),row=copy(f[kind]),storage=memory(),tab=tabFor(f.account),calls=[],control={row,hold:false},prior={window:globalThis.window,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage};globalThis.window={addEventListener(){},removeEventListener(){}};globalThis.localStorage=storage;globalThis.sessionStorage=tab;
 const list=await reseal({schema:'report-evidence-reading-list-v1',actor_id:f.account.user_id,workspace_id:f.account.workspace_id,items:[]},'list_hash'),props={account:f.account,material:{id:f.preview.body.selection.original_ref.id,version:f.preview.body.selection.original_ref.version,hash:f.preview.body.selection.original_ref.hash,name:'新空库合成报告.docx'},initialDraftID:row.id,Dialog:({children})=>children,Btn:({children})=>children,prepareDraft:async()=>{assert.fail('Opening or reviewing a saved candidate must not save configuration');},onClose(){},storage,tabStorage:tab,locks:{request:async(_n,_o,fn)=>fn()},request:async(path,options={})=>{
  calls.push({path,...options});if(path==='/me')return f.account;if(path==='/setup/understanding/models')return [{id:f.preview.body.model_id,name:'合成已登记模型',model:f.preview.model}];if(path===base+'/list-v2')return listV2(list);if(path===base+'/'+row.id)return control.row;
  if(path.includes('/review-receipts/')){if(control.reply&&path.endsWith(control.reply.receipt.request_id))return control.reply;throw missing();}
  if(path.endsWith('/review')&&options.method==='PUT'){
   const body=JSON.parse(options.body);control.body=body;if(control.hold)await new Promise(resolve=>{control.release=resolve;});
   const receipt=await reseal({schema:'report-evidence-review-receipt-v1',kind:'report_evidence_review',actor_id:f.account.user_id,workspace_id:f.account.workspace_id,draft_id:row.id,request_id:body.request_id,accepted_revision:body.revision+1,value_hash:await digest(body.value),command_hash:await evidenceCommandHash(f.account,'review',row.id,body)},'receipt_hash');
   control.row=copy(control.row);control.row.revision=body.revision+1;control.row.review_value=copy(body.value);control.row.review_history.push({receipt,value:copy(body.value)});await reseal(control.row,'view_hash');control.reply={draft:control.row,receipt,current_matches:true};return control.reply;
  }
  if(path.endsWith('/reduce-preview')&&options.method==='POST'){const p=copy(f['reduce-preview']);p.body=JSON.parse(options.body);await reseal(p,'preview_hash');return p;}
  throw missing();
 }},ui=mount(Panel,props);
 try{await ready(ui,t=>nodes(t).some(n=>n.props['data-evidence-draft-id']===row.id));await run({f,row,ui,props,calls,control,storage});}finally{ui.unmount();Object.assign(globalThis,prior);}
}

test('Gen5 full POST size is readable and its separate reduction is not falsely blocked or automatically authorized',async()=>{
 const f=await fixture();let calls=0;const props={preview:f.preview,busy:false,writable:true,pending:[{}],onStart:()=>{calls++;}},ui=mount(Preview,props);try{
  const value=text(ui.render());assert.match(value,/发送内容大小（正文和格式要求）/);assert.ok(value.includes(f.preview.input_summary.prepared_request_bytes.toLocaleString()));assert.match(value,/届时单独预览并明确授权/);assert.doesNotMatch(value,/尚不支持汇总|prepared_request_bytes|tool_choice|submit_report_evidence|beta\/chat|map-v5/);
  assert.equal(ui.button('同意本次完整范围并开始提取事实').props.disabled,true);props.pending=[];ui.render();assert.equal(calls,0);await ui.click('同意本次完整范围并开始提取事实');assert.equal(calls,1);
 }finally{ui.unmount();}
});

test('actual parent edits complete indexed manual value, saves once and reopens the exact accepted server human candidate',async()=>environment(async({f,row,ui,calls,control,storage,props})=>{
 const original=copy(row.model_result),child=nodes(ui.render()).find(n=>n.type===Editor&&n.props.writable);assert.ok(child);assert.equal(child.props.value.schema,'report-evidence-manual-review-v5');const before=copy(child.props.value),editor=mount(Editor,child.props);
 try{await editor.input('result.businesses.0.steps.0.purpose','人工核对职责末字Z');await editor.input('result.businesses.0.steps.0.role','人工核对员');await editor.input('result.global_claims.0.statement','人工核对跨业务职责末字T');await editor.input('.note','本人核对说明末字Q');}finally{editor.unmount();}
 ui.render();await ui.click('保存完整人工稿并核对服务器回执');await ready(ui,t=>text(t).includes('服务器已保存人工稿'));assert.equal(control.body.value.note,'本人核对说明末字Q');assert.equal(control.body.value.result.businesses[0].steps[0].role,'人工核对员');const expectedClaims=copy(before.result.global_claims);expectedClaims[0].statement='人工核对跨业务职责末字T';assert.deepEqual(control.body.value.result.global_claims,expectedClaims);assert.deepEqual(control.body.value.result.relationships,before.result.relationships);for(const name of ['participants','interactions','conditions','coordination'])assert.deepEqual(control.body.value.result.businesses[0][name],before.result.businesses[0][name]);assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.equal(calls.filter(c=>c.method==='POST').length,0);await verifyEvidenceDraft(control.row,f.account);assert.deepEqual(control.row.model_result,original);
 // A new component/session reads the accepted server value; no old local
 // pending body is converted or silently adopted as a provider answer.
 const reopened=mount(Panel,{...props,storage:memory()});try{await ready(reopened,t=>nodes(t).some(n=>n.type===Editor&&n.props.writable&&n.props.value.note==='本人核对说明末字Q'));const human=nodes(reopened.render()).find(n=>n.type===Editor&&n.props.writable);assert.deepEqual(human.props.value,control.body.value);}finally{reopened.unmount();}assert.ok(storage.length>0);
}));

test('actual object checkbox keeps false in local recovery and blocks PUT until a person explicitly restores a required text field',async()=>environment(async({row,ui,props,calls,control,storage})=>{
 const original=copy(row.model_result),message='对象尚缺明确的必填文本识别字段，保留当前修改并补充核对。';
 // The original public model fixture has no objects. This is an explicit
 // synthetic HUMAN edit seed through the real parent edit handler, not an
 // altered provider fixture or evidence that the UI can add object rows.
 const seed={name:'人工合成待核对象',fields:[{label:'识别名称',kind:'文本',required:true,unit:null,citations:[],inference_reason:'人工定义待核'}],citations:[],inference_reason:'对象定义及来源尚待核对'};
 const human=()=>nodes(ui.render()).find(n=>n.type===Editor&&n.props.writable);human().props.onEdit(['result','objects'],[seed]);human().props.onEdit(['note'],'保留人工说明末字🙂');
 function requiredControl(editor){const section=nodes(editor.render()).find(n=>n.type==='section'&&Array.isArray(n.props.children)&&n.props.children[0]?.type==='h5'&&nodes(n.props.children).some(c=>c.props['aria-label']==='result.objects.0.fields.0.unit'));assert.ok(section,'actual object field section');const box=nodes(section).find(n=>n.type==='input'&&n.props.type==='checkbox');assert.ok(box,'actual required checkbox');return box;}
 let editor=mount(Editor,human().props);try{assert.equal(requiredControl(editor).props.checked,true);requiredControl(editor).props.onChange({target:{checked:false}});}finally{editor.unmount();}
 editor=mount(Editor,human().props);try{assert.equal(requiredControl(editor).props.checked,false);assert.ok(nodes(editor.render()).some(n=>n.props.role==='alert'&&text(n)===message));}finally{editor.unmount();}
 await ui.click('保存完整人工稿并核对服务器回执');assert.ok(nodes(ui.render()).some(n=>n.props.role==='alert'&&text(n)===message));assert.equal(calls.filter(c=>['PUT','POST'].includes(c.method)).length,0);assert.deepEqual(control.row.model_result,original);
 const saved=Array.from({length:storage.length},(_,i)=>JSON.parse(storage.getItem(storage.key(i)))).find(v=>v.schema==='report-evidence-reading-working-v1'&&v.dirty&&v.value?.note==='保留人工说明末字🙂');assert.ok(saved);assert.equal(saved.pending,null);assert.equal(saved.value.result.objects[0].fields[0].required,false);assert.equal(human().props.value.result.objects[0].fields[0].required,false);
 const reopened=mount(Panel,props);try{
  await ready(reopened,t=>nodes(t).some(n=>typeof n.props.onClick==='function'&&text(n.props.children)==='保全当前输入并恢复此稿'));await reopened.click('保全当前输入并恢复此稿');await ready(reopened,t=>nodes(t).some(n=>n.type===Editor&&n.props.writable&&n.props.value.note==='保留人工说明末字🙂'));
  const restored=()=>nodes(reopened.render()).find(n=>n.type===Editor&&n.props.writable);assert.equal(restored().props.value.result.objects[0].fields[0].required,false);editor=mount(Editor,restored().props);try{assert.equal(requiredControl(editor).props.checked,false);requiredControl(editor).props.onChange({target:{checked:true}});}finally{editor.unmount();}
  control.hold=true;const saving=reopened.button('保存完整人工稿并核对服务器回执').props.onClick();for(let i=0;i<100&&!control.release;i++)await reopened.flush();assert.ok(control.release,'valid save reached the actual PUT handler');const originalCommand=copy(control.body);
  editor=mount(Editor,restored().props);try{requiredControl(editor).props.onChange({target:{checked:false}});await editor.input('.note','保留后来人工末字Q');}finally{editor.unmount();}
  const inFlight=Array.from({length:storage.length},(_,i)=>JSON.parse(storage.getItem(storage.key(i)))).find(v=>v.value?.note==='保留后来人工末字Q');assert.ok(inFlight);assert.deepEqual(inFlight.pending.body,originalCommand);assert.equal(inFlight.pending.body.value.result.objects[0].fields[0].required,true);assert.equal(inFlight.value.result.objects[0].fields[0].required,false);
  control.release();await saving;await reopened.flush();assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.equal(calls.filter(c=>c.method==='POST').length,0);assert.deepEqual(control.body,originalCommand);assert.equal(control.body.value.note,'保留人工说明末字🙂');assert.equal(control.row.review_value.result.objects[0].fields[0].required,true);assert.equal(restored().props.value.result.objects[0].fields[0].required,false);assert.equal(restored().props.value.note,'保留后来人工末字Q');
  const later=Array.from({length:storage.length},(_,i)=>JSON.parse(storage.getItem(storage.key(i)))).find(v=>v.value?.note==='保留后来人工末字Q');assert.equal(later.pending,null);assert.equal(later.dirty,true);await reopened.click('保存完整人工稿并核对服务器回执');assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.ok(nodes(reopened.render()).some(n=>n.props.role==='alert'&&text(n)===message));assert.deepEqual(control.row.model_result,original);
 }finally{reopened.unmount();}
}));

test('actual separate Gen5 reduction preview shows all input declarations and full request size without starting a paid reduction',async()=>environment(async({f,ui,calls})=>{
 await ui.click('预览汇总业务草案的范围与费用');await ready(ui,t=>nodes(t).some(n=>n.type===ReductionPreview));
 const child=nodes(ui.render()).find(n=>n.type===ReductionPreview),preview=mount(ReductionPreview,child.props);
 try{
  const rendered=preview.render(),tree=text(rendered),r=f['reduce-preview'].reduction_preparation,p=child.props.preview.reduction_preparation;
  assert.equal(nodes(rendered).some(n=>n.props['data-evidence-reduce-preview']===child.props.preview.preview_hash),true);
  assert.deepEqual(p.payload.input_claims,r.payload.input_claims);assert.ok(tree.includes('使用'+r.payload.input_claims.length+'条已保存原声明'));
  assert.match(tree,/发送内容大小（正文和格式要求）/);assert.equal(p.input_summary.prepared_request_bytes,r.input_summary.prepared_request_bytes);assert.ok(tree.includes(r.input_summary.prepared_request_bytes.toLocaleString()));
  assert.ok(tree.includes('本次回答最多'+r.output_capacity.max_output_tokens.toLocaleString()+' token'));assert.match(tree,/尚不能保证容纳全部内容/);
  assert.equal(p.call_count,1);assert.equal(p.requires_separate_authorization,true);assert.equal(p.output_capacity.fit_guaranteed,false);
  assert.equal(preview.button('同意本次范围并汇总业务草案').props.disabled,false);
  assert.equal(calls.filter(c=>c.method==='POST'&&c.path.endsWith('/reduce-preview')).length,1);assert.equal(calls.some(c=>c.method==='POST'&&c.path.endsWith('/reduce')),false);
 }finally{preview.unmount();}
},'mapped'));

test('late Gen5 save cannot display private manual content in a replacement actor frame',async()=>environment(async({ui,props,calls,control,storage})=>{
 const child=nodes(ui.render()).find(n=>n.type===Editor&&n.props.writable),editor=mount(Editor,child.props);await editor.input('.note','账号A私人末字R');editor.unmount();ui.render();control.hold=true;const pending=ui.button('保存完整人工稿并核对服务器回执').props.onClick();for(let i=0;i<100&&!control.release;i++)await ui.flush();assert.ok(control.release);props.account={...props.account,user_id:'10000000-0000-4000-8000-000000000099'};const frame=ui.render();assert.match(text(frame),/暂不显示私人报告内容/);assert.equal(nodes(frame).some(n=>n.type===Editor||n.props.value==='账号A私人末字R'),false);control.release();await pending;await ui.flush();assert.match(text(ui.render()),/暂不显示私人报告内容/);assert.equal(calls.filter(c=>c.method==='PUT').length,1);assert.ok(Array.from({length:storage.length},(_,i)=>storage.getItem(storage.key(i))).some(v=>v.includes('账号A私人末字R')));
}));
