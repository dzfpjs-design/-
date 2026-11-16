import test from 'node:test';
import assert from 'node:assert/strict';
import {planningScope,samePlanningScope,planningStorageKey,planningSessionStamp,planningSessionMatches,emptyPlanningForm,checkPlanningListing,checkPlanningPreview,planningConsent,planningConsentMatches,planningPreviewCurrent,planningCanStart,planningNeedsPolling,planningPermissions,planningRecoveryVersion,planningStartBody,planningStopBody,planningPending,restorePlanningJournal,writePlanningJournal,planningRecoveryBody,planningRequestPath} from '../src/native-step-planning-contract.mjs';

const account={workspace_id:'workspace-a',user_id:'user-a',username:'负责人',role:'admin'},scope=planningScope(account,'task-a');
const model={id:'model-a',name:'已配置云模型',provider:'provider-a',model:'model-version'};
const requestID='12345678-1234-4234-9234-123456789abc';
const otherID='22345678-1234-4234-9234-123456789abc';
const preview=()=>({task_version:4,preview_hash:'verified-preview-hash',model:{...model},messages:[{role:'system',content:'只规划，不执行。\n不得越权。'},{role:'user',content:'当前业务目标及完整人工步骤。'}],options:{temperature:0.2,max_tokens:800},tools:[{id:'read-records',name:'查询当前档案',external:false}],input_bytes:168,notice:'经授权后只发起一次云调用。'});
const listing=()=>({task_version:4,can_manage:true,scope_error:null,models:[{...model}],current:null,history:[]});
const row=(status='needs_review')=>({id:'plan-a',version:2,status,created_at:'2026-10-14T00:00:00Z',error:null,progress:{stage:'saved',message:'计划已保存'},proposal:{summary:'核对资料',steps:[{title:'核对当前依据',agent:'业务智能体',action:'核对原资料',capability_name:'原资料查询'}]},model_receipt:{model:'model-version',usage:{total_tokens:213}},has_saved_response:true,request_id:requestID});
function start(extra={}){const value=preview();return planningStartBody({scope,listing:listing(),form:{model_profile_id:model.id,reason:'已核对发送内容'},preview:value,consent:planningConsent(scope,value),requestID,...extra});}
function storage(){const values=new Map();return {getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};}
function state(pending=null){return {schema:1,scope:{...scope},form:{model_profile_id:model.id,reason:'保存本次决策'},pending};}

test('identity keys bind workspace, stable user, username and task without delimiter collisions',()=>{
 assert.throws(()=>planningScope({...account,user_id:''},'task-a'),/身份/);
 assert.notEqual(planningStorageKey(scope),planningStorageKey(planningScope({...account,user_id:'user-b'},'task-a')));
 assert.notEqual(planningStorageKey(scope),planningStorageKey(planningScope(account,'task-b')));
 assert.equal(samePlanningScope(scope,planningScope(account,'task-a')),true);
 assert.equal(samePlanningScope(scope,{...scope,role:'viewer'}),false);
 assert.deepEqual(emptyPlanningForm(),{model_profile_id:'',reason:''});
});
test('session guard rejects credential rotation, account switch, missing session identity and late old scope',()=>{
 const saved=storage();for(const [key,value] of Object.entries({'ga-setup-csrf':'original-token','ga-setup-csrf-workspace':scope.workspace_id,'ga-setup-csrf-user':scope.user_id,'ga-setup-csrf-username':scope.username}))saved.setItem(key,value);
 const stamp=planningSessionStamp(saved);assert.equal(planningSessionMatches(scope,saved,stamp),true);
 saved.setItem('ga-setup-csrf','new-token');assert.equal(planningSessionMatches(scope,saved,stamp),false);
 const newStamp=planningSessionStamp(saved);assert.equal(planningSessionMatches({...scope,user_id:'user-b'},saved,newStamp),false);
 saved.removeItem('ga-setup-csrf-user');assert.equal(planningSessionMatches(scope,saved,planningSessionStamp(saved)),false);
});
test('listing accepts saved proposal and history, never presents execution states as planning completion',()=>{
 const value={...listing(),current:row(),history:[row('cancelled')]};assert.equal(checkPlanningListing(value),value);
 assert.throws(()=>checkPlanningListing({...value,current:row('executed')}),/完整读取/);
 assert.throws(()=>checkPlanningListing({...value,current:{...row(),proposal:{summary:'partial'}}}),/步骤/);
 assert.throws(()=>checkPlanningListing({...value,models:[model,model]}),/重复/);
});
test('backend row_view superseded history remains readable after creating the second plan',()=>{
 // Keys mirror native_step_planning.row_view, including gateway's actual receipt keys.
 const history=JSON.parse('{"id":"old-plan","version":3,"status":"superseded","created_at":"2026-10-14T00:00:00","error":null,"progress":"计划已保存","proposal":{"summary":"核对原依据","steps":[{"title":"核对档案","agent":"data","action":"query_evidence"}]},"model_receipt":{"provider":"openai_compatible","model":"planning-test-model","usage":{"total_tokens":37},"request_id":"provider-request","call_id":"call-a"},"has_saved_response":true,"request_id":"12345678-1234-4234-9234-123456789abc"}');
 const wire={...listing(),can_stop:true,can_recover:true,current:{...row('queued'),id:'new-plan'},history:[history],history_truncated:false};
 assert.equal(checkPlanningListing(wire).history[0].status,'superseded');assert.equal(planningNeedsPolling({...wire,current:history}),false);
 assert.throws(()=>planningStopBody({scope,listing:{...wire,current:history},form:{reason:'不能停止被替代计划'},requestID}),/不能停止/);
});
test('full gateway content and options are mandatory before authorization',()=>{
 const value=preview();assert.equal(checkPlanningPreview(value),value);
 for(const patch of [{messages:[]},{messages:[{role:'user',content:{redacted:'not actual text'}}]},{options:null},{tools:[{id:'read',name:'查询'}]},{input_bytes:-1},{model:{id:model.id}}])assert.throws(()=>checkPlanningPreview({...value,...patch}));
});
test('consent binds all actual messages, request options, tool scope, model identity and account',()=>{
 const value=preview(),consent=planningConsent(scope,value);assert.equal(planningConsentMatches(consent,scope,structuredClone(value)),true);
 for(const change of [copy=>copy.messages[0].content+='新权限',copy=>copy.options.temperature=0.8,copy=>copy.tools[0].external=true,copy=>copy.model.model='new-version',copy=>copy.model.id='model-b',copy=>copy.task_version++,copy=>copy.preview_hash+='changed']){const changed=structuredClone(value);change(changed);assert.equal(planningConsentMatches(consent,scope,changed),false);}
 assert.equal(planningConsentMatches(consent,{...scope,username:'另一账号'},value),false);
 assert.equal(planningConsentMatches(consent,{...scope,role:'viewer'},value),false);
});
test('reason edits keep the authorized cloud text while start carries only the verified preview hash',()=>{
 const value=preview(),consent=planningConsent(scope,value),body=start({preview:value,consent,form:{model_profile_id:model.id,reason:'  修改后的操作理由  '}});
 assert.deepEqual(body,{request_id:requestID,expected_version:4,reason:'修改后的操作理由',model_profile_id:model.id,preview_hash:value.preview_hash,cloud_authorized:true});
 assert.equal(planningConsentMatches(consent,scope,value),true);assert.equal(Object.hasOwn(body,'messages'),false);
});
test('stale versions, changed model deployment, unapproved preview and viewers cannot start',()=>{
 assert.throws(()=>start({listing:{...listing(),task_version:5}}),/变化/);
 assert.throws(()=>start({listing:{...listing(),models:[{...model,model:'rotated-model'}]}}),/变化/);
 assert.throws(()=>start({form:{model_profile_id:'model-b',reason:'理由'}}),/变化/);
 assert.throws(()=>start({consent:null}),/授权/);
 assert.throws(()=>start({scope:{...scope,role:'viewer'}}),/不能开始/);
 assert.throws(()=>start({listing:{...listing(),can_manage:false}}),/不能开始/);
 assert.throws(()=>start({requestID:'not-a-uuid'}),/编号/);
 assert.throws(()=>start({form:{model_profile_id:model.id,reason:' '}}),/理由/);
});
test('same profile ID with changed model deployment makes the preview and checkbox stale',()=>{
 const value=preview();assert.equal(planningPreviewCurrent(value,listing(),model.id),true);
 assert.equal(planningPreviewCurrent(value,{...listing(),models:[{...model,model:'changed-deployment'}]},model.id),false);
 assert.equal(planningPreviewCurrent(value,{...listing(),models:[]},model.id),false);
 assert.equal(planningPreviewCurrent(value,{...listing(),task_version:5},model.id),false);
});
test('queued, running and unknown calls block new requests; unknown never starts automatic polling',()=>{
 for(const status of ['queued','running','response_unknown']){const value={...listing(),current:row(status)};assert.equal(planningCanStart(value),false);assert.throws(()=>start({listing:value}),/不能开始/);}
 for(const status of ['queued','running'])assert.equal(planningNeedsPolling({...listing(),current:row(status)}),true);
 for(const status of ['needs_review','failed','response_unknown','cancelled'])assert.equal(planningNeedsPolling({...listing(),current:row(status)}),false);
 assert.equal(planningCanStart({...listing(),current:row('cancelled')}),true);
 assert.equal(planningCanStart({...listing(),scope_error:'当前步骤变化'}),false);
});
test('stop binds task and plan versions and remains possible for an unknown call',()=>{
 const value={...listing(),current:row('response_unknown')},body=planningStopBody({scope,listing:value,form:{reason:'先停止未知调用的后续处理'},requestID});
 assert.deepEqual(body,{request_id:requestID,expected_version:4,expected_plan_version:2,reason:'先停止未知调用的后续处理'});
 assert.throws(()=>planningStopBody({scope,listing:{...value,current:row('cancelled')},form:{reason:'重复停止'},requestID}),/不能停止/);
});
test('expired scope independently blocks generation while original actor can stop and recover',()=>{
 const expired={...listing(),can_manage:false,scope_error:'原步骤已经变化',can_stop:true,can_recover:true,current:row('response_unknown')};
 assert.deepEqual(planningPermissions(expired),{manage:false,stop:true,recover:true});assert.equal(planningCanStart(expired),false);
 assert.equal(planningStopBody({scope,listing:expired,form:{reason:'停止旧规划'},requestID}).expected_plan_version,2);
 assert.throws(()=>planningStopBody({scope,listing:{...expired,can_stop:false},form:{reason:'无权停止'},requestID}),/不能停止/);
 assert.throws(()=>start({listing:expired}),/不能开始/);
 const pending=planningPending(scope,'start',start());assert.equal(planningRecoveryBody(pending,scope,planningRecoveryVersion({task_version:7,version:6},expired),'probe').expected_version,7);
 assert.equal(planningRecoveryVersion({version:8},expired),8);assert.equal(planningRecoveryVersion(null,expired),4);
});
test('pending requests preserve immutable complete original bodies and account/task scope',()=>{
 const body=start(),pending=planningPending(scope,'start',body);body.reason='later change';assert.equal(pending.body.reason,'已核对发送内容');
 const restored=restorePlanningJournal(JSON.parse(JSON.stringify(state(pending))),scope);assert.deepEqual(restored.pending,pending);
 assert.equal(planningRequestPath(pending,scope),'');
 for(const foreign of [{...scope,task_id:'task-b'},{...scope,user_id:'user-b'},{...scope,workspace_id:'workspace-b'}]){assert.throws(()=>restorePlanningJournal(state(pending),foreign),/草稿/);assert.throws(()=>planningRecoveryBody(pending,foreign,4,'probe'),/身份/);}
 assert.throws(()=>restorePlanningJournal(state({...pending,body:{...pending.body,cloud_authorized:false}}),scope),/授权/);
 assert.throws(()=>restorePlanningJournal(state({...pending,body:{...pending.body,unexpected:'rewritten'}}),scope),/授权/);
});
test('probe and abandonment use the exact original request and never mint a cloud request',()=>{
 const pending=planningPending(scope,'start',start());for(const action of ['probe','abandon']){const body=planningRecoveryBody(pending,scope,9,action);assert.equal(body.operation,'start');assert.equal(body.action,action);assert.equal(body.expected_version,9);assert.deepEqual(body.original_request,pending.body);assert.equal(Object.hasOwn(body,'request_id'),false);body.original_request.reason='mutated response consumer';assert.equal(pending.body.reason,'已核对发送内容');}
 assert.throws(()=>planningRecoveryBody(pending,{...scope,role:'viewer'},4,'probe'),/权限/);
});
test('stop recovery has a fixed plan identity, URL and original plan version',()=>{
 const stop=planningStopBody({scope,listing:{...listing(),current:row('running')},form:{reason:'停止规划'},requestID}),pending=planningPending(scope,'stop',stop,'plan/a');
 assert.equal(planningRequestPath(pending,scope),'/plan%2Fa/stop');const body=planningRecoveryBody(pending,scope,12,'probe');assert.equal(body.plan_id,'plan/a');assert.equal(body.original_request.expected_plan_version,2);assert.equal(body.original_request.expected_version,4);
});
test('journal survives reload with no in-memory request and cannot silently replace another pending request',()=>{
 const saved=storage(),key=planningStorageKey(scope),pending=planningPending(scope,'start',start());writePlanningJournal(saved,key,state(pending),scope);
 const restored=restorePlanningJournal(JSON.parse(saved.getItem(key)),scope);assert.deepEqual(restored.pending,pending);
 assert.throws(()=>writePlanningJournal(saved,key,state(null),scope),/另一页面/);
 assert.throws(()=>writePlanningJournal(saved,key,state(planningPending(scope,'start',start({requestID:otherID}))),scope),/另一页面/);
 assert.deepEqual(JSON.parse(saved.getItem(key)).pending,pending);
 writePlanningJournal(saved,key,state(null),scope,pending);assert.equal(JSON.parse(saved.getItem(key)).pending,null);
});
test('storage write/readback failures and corrupted journals fail closed without clearing saved pending',()=>{
 const pending=planningPending(scope,'start',start()),saved=storage(),key=planningStorageKey(scope);writePlanningJournal(saved,key,state(pending),scope);
 const failing={getItem:saved.getItem,setItem:()=>{throw Error('quota exceeded');}};assert.throws(()=>writePlanningJournal(failing,key,state(null),scope,pending),/quota/);assert.deepEqual(JSON.parse(saved.getItem(key)).pending,pending);
 const silent={getItem:()=>null,setItem:()=>{}};assert.throws(()=>writePlanningJournal(silent,key,state(),scope),/读回/);
 saved.setItem(key,'broken');assert.throws(()=>writePlanningJournal(saved,key,state(),scope));assert.equal(saved.getItem(key),'broken');
 for(const pendingValue of [undefined,false,''])assert.throws(()=>restorePlanningJournal({...state(),pending:pendingValue},scope),/草稿/);
});
