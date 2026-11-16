import test from 'node:test';
import assert from 'node:assert/strict';
import {executionScope,sameExecutionIdentity,executionStorageKey,executionSessionStamp,executionSessionMatches,checkExecutionListing,checkExecutionPreview,executionPermissions,executionNeedsPolling,executionConsent,executionConsentMatches,executionPreviewCurrent,executionApproveBody,executionCommandBody,executionPending,restoreExecutionJournal,writeExecutionJournal,executionRecoveryBody,executionRecoveryVersion,executionRequestPath,executionExportMarkdown,workflowNoteSnapshot,workflowNoteSnapshotMatches,workflowNoteCacheMatchesSent,workflowTaskRefreshPolicy,readWorkflowNoteRecovery,preserveWorkflowNoteRecovery} from '../src/native-step-execution-contract.mjs';

const account={workspace_id:'workspace-a',user_id:'user-a',username:'原负责人',role:'operator'},scope=executionScope(account,'task-a','plan-a');
const requestID='12345678-1234-4234-9234-123456789abc',otherID='22345678-1234-4234-9234-123456789abc';
const step=(extra={})=>({index:0,title:'核对原资料',agent:'data',action:'invoke_capability',input:{question:'完整实际问题',values:[0,false]},external:false,destination:null,...extra});
const preview=(extra={})=>({task_version:4,plan_version:3,approval_hash:'approval-basis-hash',steps:[step()],evidence:{source_name:'原业务档案',records:[]},notice:'批准一次原任务只读执行，结果仍需核对。',...extra});
const row=(extra={})=>({id:'execution-a',version:2,status:'needs_review',progress:null,error:null,result_hash:'original-output-hash',steps:[step({status:'succeeded',output:{count:0,checked:false},output_fields:[{key:'count',label:'返回数量',kind:'integer',unit:null}]})],review:null,history:[],possible_charge:false,...extra});
const listing=(extra={})=>({task_version:4,plan_id:'plan-a',execution:null,can_preview:true,can_approve:true,can_retry:false,can_stop:false,can_review:false,can_recover:true,scope_error:null,...extra});
const approve=(extra={})=>{const value=preview();return executionApproveBody({scope,listing:listing(),preview:value,consent:executionConsent(scope,value),externalConsent:false,reason:'已核对全部具体输入',requestID,planVersion:3,...extra});};
const journal=(pending=null)=>({schema:1,scope:{...scope},form:{reason:'原人工说明'},pending});
function storage(){const entries=new Map();return {getItem:key=>entries.get(key)??null,setItem:(key,value)=>entries.set(key,value)};}

test('execution scope and storage identity include the original plan and stable user',()=>{
 assert.notEqual(executionStorageKey(scope),executionStorageKey(executionScope(account,'task-a','plan-b')));
 assert.notEqual(executionStorageKey(scope),executionStorageKey(executionScope({...account,user_id:'other'},'task-a','plan-a')));
 assert.equal(sameExecutionIdentity(scope,{...scope,role:'admin'}),true);
 assert.throws(()=>executionScope(account,'task-a',''),/计划身份/);
 assert.throws(()=>executionScope({...account,user_id:''},'task-a','plan-a'),/身份/);
});
test('execution session rejects account changes and credential rotation',()=>{
 const saved=storage();for(const [key,value] of Object.entries({'ga-setup-csrf':'old-token','ga-setup-csrf-workspace':scope.workspace_id,'ga-setup-csrf-user':scope.user_id,'ga-setup-csrf-username':scope.username}))saved.setItem(key,value);
 const stamp=executionSessionStamp(saved);assert.equal(executionSessionMatches(scope,saved,stamp),true);
 saved.setItem('ga-setup-csrf','new-token');assert.equal(executionSessionMatches(scope,saved,stamp),false);
 saved.setItem('ga-setup-csrf-user','another-user');assert.equal(executionSessionMatches(scope,saved,executionSessionStamp(saved)),false);
});
test('execution statuses remain independent of M52 Plan states and cross-plan records are refused',()=>{
 for(const status of ['queued','running','needs_review','accepted','rejected','failed','blocked','cancelled'])assert.equal(checkExecutionListing(listing({execution:row({status})}),scope).execution.status,status);
 assert.throws(()=>checkExecutionListing(listing({execution:row({status:'superseded'})}),scope),/回执/);
 assert.throws(()=>checkExecutionListing(listing({plan_id:'plan-b'}),scope),/另一份计划/);
 assert.throws(()=>checkExecutionListing({...listing(),can_approve:undefined}),/权限/);
 assert.throws(()=>checkExecutionListing(listing({execution:row({review:{decision:'accepted',reason:'核对',at:null}})})),/决定/);
});
test('concrete inputs, complete evidence and external destinations are mandatory for preview',()=>{
 const value=preview();assert.equal(checkExecutionPreview(value),value);
 for(const changed of [{steps:[]},{evidence:null},{steps:[{...step(),input:undefined}]},{steps:[step({external:true,destination:null})]},{steps:[step(),step()]},{steps:[{...step(),index:-1}]}]){
  if(changed.steps?.[0]&&Object.hasOwn(changed.steps[0],'input')&&changed.steps[0].input===undefined)delete changed.steps[0].input;
  assert.throws(()=>checkExecutionPreview(preview(changed)));
 }
});
test('approval consent binds all actual inputs, destinations, source evidence, versions and actor',()=>{
 const value=preview(),consent=executionConsent(scope,value);assert.equal(executionConsentMatches(consent,scope,structuredClone(value)),true);
 for(const change of [copy=>copy.steps[0].input.values[0]=5,copy=>copy.steps[0].destination='https://another.example/read',copy=>{copy.steps[0].external=true;copy.steps[0].destination='https://registered.example/read';},copy=>copy.evidence.source_name='另一份来源',copy=>copy.task_version++,copy=>copy.plan_version++,copy=>copy.approval_hash+='changed']){const changed=structuredClone(value);change(changed);assert.equal(executionConsentMatches(consent,scope,changed),false);}
 assert.equal(executionConsentMatches(consent,{...scope,plan_id:'other'},value),false);
 assert.equal(executionConsentMatches(consent,{...scope,role:'viewer'},value),false);
});
test('local approval contains only fixed request, versions and approval hash',()=>{
 assert.deepEqual(approve(),{request_id:requestID,expected_version:4,reason:'已核对全部具体输入',expected_plan_version:3,approval_hash:'approval-basis-hash',authorize_external_inputs:false});
 assert.equal(Object.hasOwn(approve(),'input'),false);
 assert.throws(()=>approve({consent:null}),/先核对/);
 assert.throws(()=>approve({requestID:'bad'}),/编号/);
 assert.throws(()=>approve({reason:' '}),/理由/);
});
test('external approval needs a second explicit permission for exactly the previewed input',()=>{
 const value=preview({steps:[step({external:true,destination:'https://registered.example:443/readonly'})]}),args={preview:value,consent:executionConsent(scope,value)};
 assert.throws(()=>approve(args),/明确批准/);
 assert.equal(approve({...args,externalConsent:true}).authorize_external_inputs,true);
 const changed=structuredClone(value);changed.steps[0].input.question+='changed';assert.throws(()=>approve({...args,preview:changed,externalConsent:true}),/先核对/);
});
test('stale task, plan, revoked scope and viewer cannot approve',()=>{
 assert.equal(executionPreviewCurrent(preview(),listing(),3),true);
 for(const args of [{listing:listing({task_version:5})},{planVersion:4},{listing:listing({scope_error:'当前步骤已变化'})},{scope:{...scope,role:'viewer'}},{listing:listing({can_approve:false})}])assert.throws(()=>approve(args));
});
test('retries explicitly acknowledge possible charges and bind the original execution version',()=>{
 const args={scope,listing:listing({execution:row({status:'failed',possible_charge:true}),can_retry:true}),reason:'已核对原失败并同意重试',requestID,operation:'retry'};
 assert.throws(()=>executionCommandBody(args),/计费/);
 assert.deepEqual(executionCommandBody({...args,acknowledgePossibleCharge:true}),{request_id:requestID,expected_version:4,reason:args.reason,expected_execution_version:2,acknowledge_possible_charge:true});
 assert.throws(()=>executionCommandBody({...args,listing:{...args.listing,scope_error:'原授权撤回'},acknowledgePossibleCharge:true}),/范围/);
});
test('original actor can stop and recover expired scope using independent flags',()=>{
 const expired=listing({execution:row({status:'running'}),scope_error:'原目标已超时',can_preview:false,can_approve:false,can_stop:true,can_recover:true});
 assert.deepEqual(executionPermissions(expired),{preview:false,approve:false,retry:false,stop:true,review:false,recover:true});
 assert.equal(executionCommandBody({scope,listing:expired,reason:'停止后续执行',requestID,operation:'stop'}).expected_execution_version,2);
 assert.throws(()=>executionCommandBody({scope,listing:{...expired,can_stop:false},reason:'停止',requestID,operation:'stop'}),/无权/);
 const pending=executionPending(scope,'approve',approve());assert.equal(executionRecoveryBody(pending,scope,8,'probe').expected_version,8);
 assert.equal(executionPermissions({...expired,can_recover:false}).recover,false);
});
test('review binds exact saved output, adoption can explicitly become rejection without erasing original',()=>{
 const args={scope,listing:listing({execution:row(),can_review:true}),reason:'核对原资料后采用',requestID,operation:'review',decision:'accepted'};
 const accepted=executionCommandBody(args);assert.equal(accepted.result_hash,'original-output-hash');assert.equal(accepted.decision,'accepted');
 const withdrawn=executionCommandBody({...args,listing:listing({execution:row({status:'accepted',review:{decision:'accepted',reason:'旧决定',at:'2026-10-15T01:00:00Z'}}),can_review:true}),decision:'rejected',reason:'发现依据不充分，撤回采用'});assert.equal(withdrawn.decision,'rejected');assert.equal(withdrawn.result_hash,accepted.result_hash);
 for(const change of [{decision:'maybe'},{listing:listing({execution:row({result_hash:null}),can_review:true})},{listing:listing({execution:row(),can_review:false})}])assert.throws(()=>executionCommandBody({...args,...change}));
});
test('durable pending body is copied before POST and survives complete reload without memory',()=>{
 const body=approve(),pending=executionPending(scope,'approve',body),saved=storage(),key=executionStorageKey(scope);body.reason='later edit';
 writeExecutionJournal(saved,key,journal(pending),scope);const restored=restoreExecutionJournal(JSON.parse(saved.getItem(key)),scope);
 assert.equal(restored.pending.body.reason,'已核对全部具体输入');assert.equal(executionRequestPath(restored.pending,scope),'/approve');assert.equal(restored.form.reason,'原人工说明');
 assert.deepEqual(restoreExecutionJournal(null,scope),{schema:1,scope:{...scope},form:{reason:''},pending:null});
});
test('cross-user, cross-task and cross-plan recovery cannot reuse a fixed original approval',()=>{
 const pending=executionPending(scope,'approve',approve());
 for(const other of [{...scope,user_id:'other'},{...scope,task_id:'other'},{...scope,plan_id:'other'},{...scope,workspace_id:'other'}]){assert.throws(()=>restoreExecutionJournal(journal(pending),other),/草稿/);assert.throws(()=>executionRecoveryBody(pending,other,4,'probe'),/身份/);}
 const downgraded={...scope,role:'viewer'};assert.equal(restoreExecutionJournal(journal(pending),downgraded).pending.body.request_id,requestID);assert.throws(()=>executionRecoveryBody(pending,downgraded,4,'probe'),/权限/);
});
test('probe and abandon preserve exact original request; operation-specific fields are never reconstructed',()=>{
 const cases=[['approve',approve()],['retry',executionCommandBody({scope,listing:listing({execution:row(),can_retry:true}),reason:'重试',requestID,operation:'retry',acknowledgePossibleCharge:true})],['stop',executionCommandBody({scope,listing:listing({execution:row(),can_stop:true}),reason:'停止',requestID,operation:'stop'})],['review',executionCommandBody({scope,listing:listing({execution:row(),can_review:true}),reason:'采用',requestID,operation:'review',decision:'accepted'})]];
 for(const [operation,body] of cases){const pending=executionPending(scope,operation,body);for(const action of ['probe','abandon']){const recovered=executionRecoveryBody(pending,scope,9,action);assert.equal(recovered.operation,operation);assert.deepEqual(recovered.original_request,body);assert.equal(Object.hasOwn(recovered,'request_id'),false);assert.equal(recovered.expected_version,9);recovered.original_request.reason='mutated';assert.equal(pending.body.reason,body.reason);}}
 assert.equal(executionRecoveryVersion({task_version:9},listing()),9);assert.equal(executionRecoveryVersion(null,listing()),4);
});
test('corrupt and unknown journals or pending fields cannot be overwritten or silently cleared',()=>{
 const pending=executionPending(scope,'approve',approve()),saved=storage(),key=executionStorageKey(scope);writeExecutionJournal(saved,key,journal(pending),scope);
 assert.throws(()=>writeExecutionJournal(saved,key,journal(),scope),/另一页面/);
 assert.throws(()=>writeExecutionJournal(saved,key,journal(executionPending(scope,'approve',approve({requestID:otherID}))),scope),/另一页面/);
 assert.deepEqual(JSON.parse(saved.getItem(key)).pending,pending);
 writeExecutionJournal(saved,key,journal(),scope,pending);assert.equal(JSON.parse(saved.getItem(key)).pending,null);
 for(const changed of [{schema:2},{pending:false},{form:{reason:'说明',secret:'unknown'}},{pending:{...pending,body:{...pending.body,unexpected:true}}},{pending:{...pending,body:{...pending.body,authorize_external_inputs:'true'}}}])assert.throws(()=>restoreExecutionJournal({...journal(pending),...changed},scope));
 saved.setItem(key,'broken');assert.throws(()=>writeExecutionJournal(saved,key,journal(),scope));assert.equal(saved.getItem(key),'broken');
});
test('storage quota and failed readback preserve the existing pending evidence',()=>{
 const pending=executionPending(scope,'approve',approve()),saved=storage(),key=executionStorageKey(scope);writeExecutionJournal(saved,key,journal(pending),scope);
 assert.throws(()=>writeExecutionJournal({getItem:saved.getItem,setItem:()=>{throw Error('quota');}},key,journal(),scope,pending),/quota/);assert.deepEqual(JSON.parse(saved.getItem(key)).pending,pending);
 assert.throws(()=>writeExecutionJournal({getItem:()=>null,setItem:()=>{}},key,journal(),scope),/读回/);
});
test('only active execution polls and readable export keeps zero false unknown and all records',()=>{
 for(const status of ['queued','running'])assert.equal(executionNeedsPolling(listing({execution:row({status})})),true);
 for(const status of ['needs_review','accepted','rejected','failed','blocked','cancelled'])assert.equal(executionNeedsPolling(listing({execution:row({status})})),false);
 const markdown=executionExportMarkdown({execution:row(),output:{count:0,checked:false,unknown:null,unit:null},records:[{value:1},{value:2}]});
 assert.match(markdown,/^# 原任务只读执行/);assert.match(markdown,/数量：0/);assert.match(markdown,/checked：否/);assert.match(markdown,/未提供，保持未知/);assert.match(markdown,/第 2 项/);assert.doesNotMatch(markdown,/```json/);
});
test('execution refresh preserves all dirty manual fields and a pending original completion',()=>{
 const current={task:{id:'task-a',version:4,workflow_runtime:{current_step_id:'manual-a'}},note:'还在输入',branch:'no',override:'代处理说明',verdict:'uncertain',dirty:true,pendingAction:{request_id:requestID,step_id:'manual-a'}};
 const incoming={...current.task,version:5,native_execution_waiting:false};
 assert.deepEqual(workflowTaskRefreshPolicy(current,incoming),{apply:true,archive:false,replaceDraft:false});
 assert.deepEqual(workflowNoteSnapshot(current),{step_id:'manual-a',note:'还在输入',branch:'no',override:'代处理说明',verdict:'uncertain',pending:true,action:current.pendingAction});
 const pendingOnly={...current,dirty:false};assert.equal(workflowTaskRefreshPolicy(pendingOnly,incoming).replaceDraft,false);
 assert.deepEqual(workflowTaskRefreshPolicy(current,{...incoming,id:'other-task'}),{apply:false,archive:false,replaceDraft:false});
 assert.equal(workflowTaskRefreshPolicy(current,{...incoming,version:3}).apply,false);
});
test('late note save cannot acknowledge new input, another step or another task',()=>{
 const sent={task:{id:'task-a',version:4,workflow_runtime:{current_step_id:'manual-a'}},note:'已发送的文字',branch:'yes',override:'代办原因',verdict:'uncertain',dirty:true,pendingAction:null};
 assert.equal(workflowNoteSnapshotMatches(sent,{...sent,task:{...sent.task,version:5}}),true);
 for(const patch of [{note:'后续输入'},{branch:'no'},{override:'新代办原因'},{verdict:'abnormal'},{pendingAction:{request_id:requestID}},{task:{...sent.task,id:'other'}},{task:{...sent.task,workflow_runtime:{current_step_id:'manual-b'}}}])assert.equal(workflowNoteSnapshotMatches(sent,{...sent,...patch}),false);
});
test('cross-step refresh archives exact original human labor before replacing the displayed step',()=>{
 const current={task:{id:'task-a',version:4,workflow_runtime:{current_step_id:'manual-a'}},note:'旧步骤尚未送达的文字',branch:'no',override:'旧理由',verdict:'uncertain',dirty:true,pendingAction:{request_id:requestID,step_id:'manual-a',note:'原提交文字'}};
 const next={...current.task,version:6,workflow_runtime:{current_step_id:'manual-b'}};assert.deepEqual(workflowTaskRefreshPolicy(current,next),{apply:true,archive:true,replaceDraft:true});
 const saved=storage(),key='manual-recovery',identity={workspace_id:scope.workspace_id,user_id:scope.user_id,username:scope.username,task_id:scope.task_id},draft=workflowNoteSnapshot(current);
 const result=preserveWorkflowNoteRecovery(saved,key,identity,draft,'2026-10-15T01:00:00Z');assert.deepEqual(result.drafts[0].draft,draft);
 assert.deepEqual(readWorkflowNoteRecovery(saved,key,identity).drafts[0].draft.action,current.pendingAction);
 assert.equal(preserveWorkflowNoteRecovery(saved,key,identity,draft,'2026-10-15T02:00:00Z').drafts.length,1);
 assert.throws(()=>preserveWorkflowNoteRecovery(saved,key,{...identity,user_id:'other-user'},draft,'2026-10-15T01:00:00Z'),/覆盖/);
 const failing={getItem:saved.getItem,setItem:()=>{throw Error('quota');}};assert.throws(()=>preserveWorkflowNoteRecovery(failing,key,identity,{...draft,note:'新旧稿'},'2026-10-15T03:00:00Z'),/quota/);assert.deepEqual(readWorkflowNoteRecovery(saved,key,identity).drafts[0].draft,draft);
 saved.setItem(key,'unknown');assert.throws(()=>preserveWorkflowNoteRecovery(saved,key,identity,draft,'2026-10-15T01:00:00Z'));assert.equal(saved.getItem(key),'unknown');
});
test('scope change blocks adoption while flagged rejection can withdraw a previously adopted result',()=>{
 const args={scope,listing:listing({execution:row({status:'accepted'}),can_review:true,scope_error:'原件不可再核对'}),reason:'撤回不再可靠的采用决定',requestID,operation:'review'};
 assert.equal(executionCommandBody({...args,decision:'rejected'}).decision,'rejected');assert.throws(()=>executionCommandBody({...args,decision:'accepted'}),/范围/);
});
test('successful manual save cannot clear another tab draft written while the POST was in flight',()=>{
 const saved=storage(),key='original-note';saved.setItem(key,JSON.stringify({step_id:'manual-a',note:'发送时的人稿',pending:true}));const sentRaw=saved.getItem(key);
 assert.equal(workflowNoteCacheMatchesSent(sentRaw,saved.getItem(key)),true);
 saved.setItem(key,JSON.stringify({step_id:'manual-a',note:'另一页较新的人稿',branch:'no',pending:true}));
 assert.equal(workflowNoteCacheMatchesSent(sentRaw,saved.getItem(key)),false);assert.match(saved.getItem(key),/另一页较新/);
 assert.equal(workflowNoteCacheMatchesSent(null,null),false);saved.setItem(key,'unknown-corruption');assert.equal(workflowNoteCacheMatchesSent(sentRaw,saved.getItem(key)),false);
});
