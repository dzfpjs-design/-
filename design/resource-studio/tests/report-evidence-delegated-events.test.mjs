import {listV2} from './report-evidence-list-test-helper.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {resolve,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {build} from 'vite';
import {mount} from '../scripts/react-event-harness.mjs';
import {fixture,completed,generated,id,account,memory,tabFor,missing,reseal,nodes,text} from './report-evidence-v3-fixture.mjs';
import {copy,digest,evidenceCommandHash,verifyEvidenceDraft} from '../src/report-evidence-reading-contract.mjs';
import {verifyDelegatedEvidenceOrigin} from '../src/report-evidence-delegated-origin.mjs';

let Panel,temporary;
test.before(async()=>{temporary=await mkdtemp(join(tmpdir(),'delegated-evidence-ui-'));const built=await build({configFile:false,envDir:temporary,logLevel:'silent',cacheDir:join(temporary,'cache'),build:{ssr:resolve('src/ReportEvidenceReadingDialog.jsx'),write:false,minify:false,rollupOptions:{output:{format:'es',inlineDynamicImports:true,paths:id=>id==='react'?pathToFileURL(resolve('node_modules/react/index.js')).href:id}}}}),file=join(temporary,'dialog.mjs');await writeFile(file,built.output.find(o=>o.isEntry).code);Panel=(await import(pathToFileURL(file))).ReportEvidenceReadingPanel;});
test.after(()=>rm(temporary,{recursive:true,force:true}));

async function data({partial=false,unknown=false,generatedResult=false,completedResult=false}={}){
 const base=await fixture(),f=await fixture({...base.preview.body,request_id:'fl_'+id(20)}),done=unknown?await completed(f):null,row=generatedResult?await generated(f):partial||completedResult?await completed(f):copy(f.draft);
 for(const candidate of [done,row])if(candidate){for(const stage of [...candidate.maps,candidate.reduction])if(stage.receipt)stage.receipt.request_id=null;await reseal(candidate,'view_hash');}
 if(partial){row.status='failed';row.phase='failed';row.maps[1]={...row.maps[1],status:'pending',reserved_call_id:null,journal:null,raw_response_saved:false,receipt:null,result:null,result_hash:null,result_count:null};await reseal(row,'view_hash');}
 if(unknown){row.status='response_unknown';row.phase='response_unknown';row.revision=2;row.maps[0].status='response_unknown';row.maps[0].reserved_call_id=done.maps[0].reserved_call_id;await reseal(row,'view_hash');}
 const basis={schema:'report-evidence-delegated-basis-v1',workspace_id:account.workspace_id,actor_id:account.user_id,
  original_draft_id:id(72),original_revision:5,original_job_id:id(73),original_start_receipt_hash:'1'.repeat(64),
  original_source_hash:row.preview.source_hash,original_projection_hash:row.preview.projection_hash,original_first_raw_hash:'2'.repeat(64),
  original_first_candidate_receipt_hash:'3'.repeat(64),original_messages_hashes:['4'.repeat(64),'5'.repeat(64)],
  new_messages_hashes:row.preview.map_preparation.plan.chunks.map(c=>c.messages_hash),new_preview_hash:row.preview.preview_hash,
  operator_asserted_history_hash:'6'.repeat(64),history_digest_verification:'operator_asserted_not_independently_verified',
  historical_second_send:'not_independently_proven',source_relationship:'same_report_word_and_ocr_views_overlap',
  new_analysis:true,not_original_batch_recovery:true,authorization_basis:'authenticated_actor_submitted_request',
  user_reviewed_history:false,user_confirmed_business:false,duplicate_cost_risk:true};
 basis.history_assessment=await reseal({schema:'report-evidence-history-assessment-v1',workspace_id:basis.workspace_id,actor_id:basis.actor_id,original_draft_id:basis.original_draft_id,original_revision:basis.original_revision,original_start_receipt_hash:basis.original_start_receipt_hash,original_source_hash:basis.original_source_hash,second_messages_hash:basis.original_messages_hashes[1],original_job_id:basis.original_job_id,original_job_data_hash:'8'.repeat(64),original_job_failed_single_attempt_no_active_lease:true,original_authorization_and_failure_audit_verified:true,first_response_ledger_and_candidate_verified:true,second_stage_pending_and_workspace_ledger_clear:true,independent_transport_or_provider_proof:false,conclusion:'conditional_engineering_inference_not_provider_proof',assumptions:['runtime_code_not_independently_attested','database_and_audit_integrity_assumed','no_unobserved_external_gateway_bypass_assumed']},'assessment_hash');
 const payload={schema:'report-evidence-delegated-origin-v1',basis,new_draft_id:row.id,new_job_id:id(74),request_id:row.request_id,command_hash:'7'.repeat(64)};
 return {f,row,done,reply:{schema:'report-evidence-delegated-origin-v1',origin:{payload,origin_hash:await digest(payload)},can_apply:false,can_publish:false}};
}
async function until(ui,predicate){for(let i=0;i<150;i++){await ui.flush();if(predicate(ui.render()))return;}assert.fail('未达到可观察页面状态');}
async function environment(run,{originError,mutate,hold=false,partial=false,unknown=false,generatedResult=false,completedResult=false,rejectRecovery=false,rowMutate}={}){
 const {f,row,done,reply}=await data({partial,unknown,generatedResult,completedResult}),cache=memory(),tab=tabFor(),calls=[],control={invalid:0,release:null,current:row,recoveryReply:null};
 if(rowMutate){rowMutate(row);await reseal(row,'view_hash');}
 const before={window:globalThis.window,localStorage:globalThis.localStorage,sessionStorage:globalThis.sessionStorage};
 globalThis.window={addEventListener(){},removeEventListener(){}};globalThis.localStorage=cache;globalThis.sessionStorage=tab;
 const list=await reseal({schema:'report-evidence-reading-list-v1',actor_id:account.user_id,workspace_id:account.workspace_id,items:[]},'list_hash');
 const request=async(path,o={})=>{calls.push({path,method:o.method});if(path==='/me')return account;if(path==='/setup/understanding/models')return f.models;if(path==='/setup/report-evidence-readings/list-v2')return listV2(list);if(path==='/setup/report-evidence-readings/'+row.id)return control.current;if(path.startsWith('/setup/report-evidence-readings/'+row.id+'/recovery-receipts/')){if(control.recoveryReply)return control.recoveryReply;throw missing();}if(path==='/setup/report-evidence-readings/'+row.id+'/recover-saved'&&o.method==='POST'){if(rejectRecovery)throw Object.assign(Error('stale'),{status:422,detail:{code:'evidence_saved_response_invalid',message:'原响应未通过核对。'}});const body=JSON.parse(o.body),next=copy(control.current);next.maps[0]=copy(done.maps[0]);next.status='failed';next.phase='failed';next.revision++;next.error='已免费恢复原结果，剩余资料尚未发送。';const receipt=await reseal({schema:'report-evidence-recovery-receipt-v1',kind:'report_evidence_recovery',actor_id:account.user_id,workspace_id:account.workspace_id,draft_id:row.id,request_id:body.request_id,accepted_revision:next.revision,command_hash:await evidenceCommandHash(account,'recovery',row.id,body)},'receipt_hash');next.recovery_history=[...next.recovery_history,receipt];await reseal(next,'view_hash');control.current=next;control.recoveryReply={draft:next,receipt};return control.recoveryReply;}if(path==='/setup/report-evidence-delegated/'+row.id+'/origin'){assert.equal(o.method,'POST');if(hold)await new Promise(resolve=>{control.release=resolve;});if(originError)throw Object.assign(Error('denied'),{status:originError});const v=copy(reply);if(mutate)await mutate(v);return v;}throw missing();};
 const props={account,material:{id:f.original_ref.id,version:1,hash:f.original_ref.hash,name:'合成报告.docx'},Dialog:({children})=>children,Btn:({children})=>children,prepareDraft:async()=>1,onClose(){},onRegisterCloseGuard(){},onSessionInvalid(){control.invalid++;},initialDraftID:row.id,request,storage:cache,tabStorage:tab,locks:{request:async(_key,_options,fn)=>fn()}};
 const ui=mount(Panel,props);
 try{await run({f,row,reply,ui,props,calls,control});}finally{ui.unmount();Object.assign(globalThis,before);}
}

test('linked origin is hash-bound to task, exact sources and explicit uncertainty',async()=>{
 const {row,reply}=await data();await verifyDelegatedEvidenceOrigin(reply,account,row);
 for(const mutate of [b=>b.actor_id=id(98),b=>b.original_source_hash='e'.repeat(64),b=>b.user_confirmed_business=true,b=>b.historical_second_send='proved_not_sent',b=>b.new_messages_hashes.pop(),b=>b.original_messages_hashes[0]=b.new_messages_hashes[0],b=>b.duplicate_cost_risk=false]){
  const bad=copy(reply);mutate(bad.origin.payload.basis);bad.origin.origin_hash=await digest(bad.origin.payload);await assert.rejects(verifyDelegatedEvidenceOrigin(bad,account,row));
 }
});
test('linked receipts expose only safe usage and never expose provider request ids, while legacy fe receipts remain readable',async()=>{
 const legacy=await fixture(),legacyDone=await completed(legacy);assert.notEqual(legacyDone.maps[0].receipt.request_id,null);await verifyEvidenceDraft(legacyDone,account);
 const {row}=await data({partial:true});await verifyEvidenceDraft(row,account);
 for(const mutate of [r=>r.request_id='secret',r=>r.usage.total_tokens=-1,r=>r.usage.total_tokens=Number.MAX_SAFE_INTEGER+1,r=>r.usage.extra='secret',r=>r.finish_reason='unknown-secret']){
  const bad=copy(row);mutate(bad.maps[0].receipt);await reseal(bad,'view_hash');await assert.rejects(verifyEvidenceDraft(bad,account));
 }
 const reduced=(await data({generatedResult:true})).row;await verifyEvidenceDraft(reduced,account);reduced.reduction.receipt.request_id='secret';await reseal(reduced,'view_hash');await assert.rejects(verifyEvidenceDraft(reduced,account));
});
test('actual linked parent reports unknown provider total without inventing a charge',async()=>environment(async({ui})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-status']==='failed'));assert.match(text(ui.render()),/供应商用量无法核对；未推算费用/);
},{partial:true}));
test('actual linked parent omits unknown-usage warning when provider supplies a valid total',async()=>environment(async({ui})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-status']==='failed'));assert.doesNotMatch(text(ui.render()),/供应商用量无法核对/);
},{partial:true,rowMutate:row=>{row.maps[0].receipt.usage.total_tokens=140;}}));
test('actual linked parent independently warns when only the reduction total is missing',async()=>environment(async({ui})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-status']==='review'));assert.match(text(ui.render()),/供应商用量无法核对；未推算费用/);
},{generatedResult:true,rowMutate:row=>{for(const s of row.maps)s.receipt.usage.total_tokens=140;}}));
test('history assessment is bound to original identity and scope even if both hashes are recomputed',async()=>{
 const {row,reply}=await data();
 for(const mutate of [a=>a.actor_id=id(99),a=>a.workspace_id=id(99),a=>a.original_draft_id=id(99),a=>a.original_revision++,a=>a.second_messages_hash='f'.repeat(64),a=>a.independent_transport_or_provider_proof=true,a=>a.conclusion='proved',a=>a.assumptions.pop(),a=>a.original_authorization_and_failure_audit_verified=false]){
  const bad=copy(reply),a=bad.origin.payload.basis.history_assessment;mutate(a);await reseal(a,'assessment_hash');bad.origin.origin_hash=await digest(bad.origin.payload);await assert.rejects(verifyDelegatedEvidenceOrigin(bad,account,row));
 }
 const noHash=copy(reply);noHash.origin.payload.basis.operator_asserted_history_hash=null;noHash.origin.payload.basis.history_digest_verification='not_provided';noHash.origin.origin_hash=await digest(noHash.origin.payload);await verifyDelegatedEvidenceOrigin(noHash,account,row);
 noHash.origin.payload.basis.history_digest_verification='operator_asserted_not_independently_verified';noHash.origin.origin_hash=await digest(noHash.origin.payload);await assert.rejects(verifyDelegatedEvidenceOrigin(noHash,account,row));
 const corrupt=copy(reply);corrupt.origin.payload.basis.history_assessment.assessment_hash='f'.repeat(64);corrupt.origin.origin_hash=await digest(corrupt.origin.payload);await assert.rejects(verifyDelegatedEvidenceOrigin(corrupt,account,row));
});
test('actual parent opens a bound origin without an operator supplied history digest',async()=>environment(async({ui,row})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-draft-id']===row.id));assert.match(text(ui.render()),/旧批次是否发出尚无独立证明/);
},{mutate:async v=>{v.origin.payload.basis.operator_asserted_history_hash=null;v.origin.payload.basis.history_digest_verification='not_provided';v.origin.origin_hash=await digest(v.origin.payload);}}));
test('actual parent rejects a rehashed history assessment with a forged independent proof',async()=>environment(async({ui})=>{
 await until(ui,t=>text(t).includes('来源暂无法核对'));assert.equal(nodes(ui.render()).some(n=>n.props['data-evidence-draft-id']),false);
},{mutate:async v=>{const a=v.origin.payload.basis.history_assessment;a.independent_transport_or_provider_proof=true;await reseal(a,'assessment_hash');v.origin.origin_hash=await digest(v.origin.payload);}}));
test('actual parent loads linked record with explicit independent-analysis and overlap warning, then survives remount',async()=>environment(async({ui,row,calls,props})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-draft-id']===row.id));let output=text(ui.render());
 for(const phrase of ['独立分段分析','范围重叠','旧批次是否发出尚无独立证明','可能产生重复费用','尚未人工确认'])assert.ok(output.includes(phrase),phrase);
 assert.equal(calls.filter(c=>c.path.endsWith('/origin')).length,1);assert.ok(!calls.some(c=>c.method==='PUT'||c.path==='/setup/report-evidence-readings'&&c.method==='POST'));
 ui.unmount();const newer=mount(Panel,props);try{await until(newer,t=>nodes(t).some(n=>n.props['data-evidence-draft-id']===row.id));assert.match(text(newer.render()),/独立分段分析/);}finally{newer.unmount();}
}));
for(const status of [401,403])test('actual parent hides private linked record when origin returns '+status,async()=>environment(async({ui,control})=>{
 await until(ui,t=>text(t).includes('暂不显示私人报告内容'));assert.equal(control.invalid,1);assert.equal(nodes(ui.render()).some(n=>n.props['data-evidence-draft-id']),false);
},{originError:status}));
test('rehashed but forged origin never opens record or enables a paid action',async()=>environment(async({ui,calls})=>{
 await until(ui,t=>text(t).includes('来源暂无法核对'));assert.equal(nodes(ui.render()).some(n=>n.props['data-evidence-draft-id']),false);assert.equal(nodes(ui.render()).some(n=>typeof n.props.onClick==='function'&&text(n.props.children)==='预览完整范围与预计调用'),false);assert.ok(!calls.some(c=>c.method==='PUT'||c.path==='/setup/report-evidence-readings'&&c.method==='POST'));
},{mutate:async v=>{v.origin.payload.basis.user_confirmed_business=true;v.origin.origin_hash=await digest(v.origin.payload);}}));
test('role or account replacement hides content and a late origin reply cannot expose it',async()=>environment(async({ui,props,control})=>{
 await until(ui,()=>!!control.release);props.account={...account,user_id:id(99)};assert.match(text(ui.render()),/暂不显示私人报告内容/);control.release();await ui.flush();assert.match(text(ui.render()),/暂不显示私人报告内容/);assert.equal(nodes(ui.render()).some(n=>n.props['data-evidence-draft-id']),false);
},{hold:true}));

test('actual linked parent restores a partially processed draft after remount without sending model calls',async()=>environment(async({ui,row,calls,props})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-status']==='failed'));assert.match(text(ui.render()),/已成功1\/2个资料组/);assert.match(text(ui.render()),/独立分段分析/);ui.unmount();const newer=mount(Panel,props);try{await until(newer,t=>nodes(t).some(n=>n.props['data-evidence-status']==='failed'));assert.match(text(newer.render()),/已成功1\/2个资料组/);assert.ok(!calls.some(c=>c.path.endsWith('/resume')||c.path.endsWith('/recover-saved')));}finally{newer.unmount();}
},{partial:true}));
test('linked partial parent explains unavailable continuation without offering a fake preview',async()=>environment(async({ui,calls})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-status']==='failed'));const rendered=text(ui.render());
 assert.match(rendered,/后台暂未提供继续处理剩余资料组/);assert.equal(nodes(ui.render()).some(n=>text(n.props.children)==='预览继续未处理资料'&&n.props.onClick),false);
 assert.ok(ui.button('免费尝试恢复已保存回答'));assert.equal(calls.some(c=>c.path.endsWith('/resume-preview')),false);
},{partial:true}));
test('linked completed parent exposes scoped read-only consolidation preview and retains saved results',async()=>environment(async({ui,calls,row})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-status']==='awaiting_merge'));const rendered=text(ui.render());
 assert.match(rendered,/已成功2\/2个资料组/);assert.ok(nodes(ui.render()).some(n=>typeof n.type==='function'&&n.type.name==='DelegatedReducePreview'&&n.props.row.id===row.id));assert.equal(nodes(ui.render()).some(n=>text(n.props.children)==='预览汇总业务草案的范围与费用'&&n.props.onClick),false);assert.equal(calls.some(c=>c.path.endsWith('/reduce-preview')),false);
},{completedResult:true}));

async function eligibleCapture(row){
 const stage=row.maps[0].chunk_id,call_id=row.maps[0].reserved_call_id;
 const scope={workspace_id:account.workspace_id,actor_id:account.user_id,draft_id:row.id,revision:row.revision,stage,source_hash:row.preview.source_hash,preview_hash:row.preview.preview_hash,messages_hash:row.preview.map_preparation.plan.chunks[0].messages_hash,call_id};
 const capture={state:'complete',request_started:true,headers_saved:true,http_status:200,content_encoding_safe:true,body_bytes:3,body_sha256:'a'.repeat(64),response_complete:true,observed_bytes:3,omitted_bytes:0,prefix_only:false,reason:null,capture_hash:'b'.repeat(64),encoding_supported:true};
 const remaining=row.maps.filter(s=>s.status!=='succeeded'&&s.chunk_id!==stage).map(s=>s.chunk_id);
 const basis_hash=await digest({workspace_id:account.workspace_id,actor_id:account.user_id,draft_id:row.id,revision:row.revision,stage,call_id,capture_hash:capture.capture_hash,source_hash:row.preview.source_hash,remaining});
 return reseal({schema:'report-delegated-http-capture-view-v1',scope,capture,ledger:{status:'succeeded',reserved_tokens:1000,charged_tokens:230,usage_reported:true},outcome:'normal_end_unverified',finish_reason:'stop',choice_count:1,recovery:{status:'eligible',blocker:null,target_stage:stage,remaining_count:remaining.length,basis_hash},can_view_body:false,body_access:'download_disabled',can_apply:false,can_publish:false,can_resend:false},'view_hash');
}
test('actual parent requires preflight and restores saved answer without starting another model call',async()=>environment(async({ui,row,calls,props,control})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-status']==='response_unknown'));
 assert.equal(ui.button('免费尝试恢复已保存回答').props.disabled,true);
 const capture=nodes(ui.render()).find(n=>n.type?.name==='DelegatedEvidenceCapture'&&n.props.stage===row.maps[0].chunk_id);
 assert.ok(capture);await capture.props.onCapture(await eligibleCapture(row));await ui.flush();
 assert.equal(ui.button('免费尝试恢复已保存回答').props.disabled,false);
 await ui.click('免费尝试恢复已保存回答');
 await until(ui,t=>text(t).includes('已成功1/2个资料组'));
 assert.equal(calls.filter(c=>c.path.endsWith('/recover-saved')&&c.method==='POST').length,1);
 assert.equal(calls.some(c=>c.path.endsWith('/resume')),false);
 assert.equal(control.current.maps[1].status,'pending');
 ui.unmount();const newer=mount(Panel,props);try{await until(newer,t=>text(t).includes('已成功1/2个资料组'));}finally{newer.unmount();}
},{unknown:true}));
test('actual parent disables recovery after the server rejects a stale preflight',async()=>environment(async({ui,row,calls})=>{
 await until(ui,t=>nodes(t).some(n=>n.props['data-evidence-status']==='response_unknown'));
 const capture=nodes(ui.render()).find(n=>n.type?.name==='DelegatedEvidenceCapture'&&n.props.stage===row.maps[0].chunk_id);
 await capture.props.onCapture(await eligibleCapture(row));await ui.flush();await ui.click('免费尝试恢复已保存回答');
 await until(ui,()=>ui.button('免费尝试恢复已保存回答').props.disabled===true);
 assert.equal(calls.filter(c=>c.path.endsWith('/recover-saved')&&c.method==='POST').length,1);
 assert.equal(calls.some(c=>c.path.endsWith('/resume')),false);
},{unknown:true,rejectRecovery:true}));
