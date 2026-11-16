import {planningScope,planningSessionStamp,planningSessionMatches} from './native-step-planning-contract.mjs';

export const EXECUTION_STATES={queued:'等待只读执行',running:'正在只读执行',needs_review:'真实结果待人工核对',accepted:'结果已采用',rejected:'结果已拒绝',failed:'执行未完成',blocked:'执行条件需要恢复',cancelled:'已停止后续执行'};
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const text=value=>typeof value==='string'&&value.trim().length>0;
const integer=value=>Number.isInteger(value)&&value>=0;
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
const fail=message=>{throw Error(message);};
const exact=(value,keys)=>record(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
export function executionCanonical(value){if(Array.isArray(value))return '['+value.map(executionCanonical).join(',')+']';if(record(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+executionCanonical(value[key])).join(',')+'}';return JSON.stringify(value);}
const copy=value=>JSON.parse(JSON.stringify(value));
export function executionScope(account,taskID,planID){const scope={...planningScope(account,taskID),plan_id:planID};if(!text(planID))fail('原计划身份尚未完整读取，暂不能执行。');return scope;}
export function sameExecutionIdentity(left,right){return !!left&&!!right&&['workspace_id','user_id','username','task_id','plan_id'].every(key=>left[key]===right[key]);}
export function sameExecutionScope(left,right){return sameExecutionIdentity(left,right)&&left.role===right.role;}
export function executionStorageKey(scope){return 'ga-native-step-execution:'+JSON.stringify([scope.workspace_id,scope.user_id,scope.username,scope.task_id,scope.plan_id]);}
export const executionSessionStamp=planningSessionStamp;
export const executionSessionMatches=planningSessionMatches;
export const emptyExecutionForm=()=>({reason:''});
function checkStep(step,preview=false){
 if(!record(step)||!integer(step.index)||!['title','action','agent'].every(key=>text(step[key]))||(!Object.hasOwn(step,'input')||step.input===undefined)||typeof step.external!=='boolean'||(step.destination!=null&&typeof step.destination!=='string')||(step.external&&!text(step.destination)))fail('执行步骤、完整实际输入或外发目的地尚未读取，不能批准。');
 if(!preview&&!text(step.status))fail('原执行步骤状态尚未完整读取。');
 if(step.output_fields!=null&&(!Array.isArray(step.output_fields)||step.output_fields.some(field=>!record(field)||!['key','label','kind'].every(key=>text(field[key]))||(field.unit!=null&&typeof field.unit!=='string'))))fail('原工具结果字段说明尚未完整读取。');
 return step;
}
export function checkExecutionRow(value){
 if(!record(value)||!text(value.id)||!integer(value.version)||!Object.hasOwn(EXECUTION_STATES,value.status)||!Array.isArray(value.steps)||!Array.isArray(value.history)||typeof value.possible_charge!=='boolean')fail('原执行回执尚未完整读取，已保存记录保留。');
 value.steps.forEach(step=>checkStep(step));
 if(value.review!=null&&(!record(value.review)||!['accepted','rejected'].includes(value.review.decision)||!text(value.review.reason)||!text(value.review.at)))fail('原结果核查决定尚未完整读取。');
 if(value.result_hash!=null&&!text(value.result_hash))fail('原结果核查依据尚未完整读取。');
 return value;
}
export function checkExecutionListing(value,scope=null){
 if(!record(value)||!integer(value.task_version)||!text(value.plan_id)||!['can_preview','can_approve','can_retry','can_stop','can_review'].every(key=>typeof value[key]==='boolean')||(value.scope_error!=null&&typeof value.scope_error!=='string')||(value.can_recover!==undefined&&typeof value.can_recover!=='boolean'))fail('原任务执行权限或记录尚未完整读取，请刷新核对。');
 if(scope&&value.plan_id!==scope.plan_id)fail('服务器返回了另一份计划的执行记录，原请求保留。');
 if(!Object.hasOwn(value,'execution'))fail('原执行记录尚未完整读取。');
 if(value.execution!==null)checkExecutionRow(value.execution);return value;
}
export function checkExecutionPreview(value){
 if(!record(value)||!integer(value.task_version)||!integer(value.plan_version)||!text(value.approval_hash)||!Array.isArray(value.steps)||!value.steps.length||!Object.hasOwn(value,'evidence')||value.evidence===null||value.evidence===undefined||typeof value.notice!=='string')fail('完整执行预览尚未读取，不能批准本次调用。');
 value.steps.forEach(step=>checkStep(step,true));if(new Set(value.steps.map(step=>step.index)).size!==value.steps.length)fail('执行步骤重复，请重新预览。');return value;
}
export function executionPermissions(value){return {preview:!!value?.can_preview,approve:!!value?.can_approve,retry:!!value?.can_retry,stop:!!value?.can_stop,review:!!value?.can_review,recover:value?.can_recover??!!(value?.can_stop||value?.can_review||value?.can_approve||value?.can_retry)};}
export function executionNeedsPolling(value){return ['queued','running'].includes(value?.execution?.status);}
export function executionPreviewCurrent(preview,listing,planVersion){return !!preview&&!!listing&&!listing.scope_error&&preview.task_version===listing.task_version&&(planVersion==null||preview.plan_version===planVersion);}
export function executionConsent(scope,preview){checkExecutionPreview(preview);return {scope:{...scope},preview:executionCanonical(preview)};}
export function executionConsentMatches(consent,scope,preview){return !!consent&&sameExecutionScope(consent.scope,scope)&&consent.preview===executionCanonical(checkExecutionPreview(preview));}
const checkedReason=value=>{if(typeof value!=='string'||!value.trim()||value.trim().length>2000)fail('请填写本次执行或核查理由（最多2000字）。');return value.trim();};
function common(scope,listing,reason,requestID,operation){checkExecutionListing(listing,scope);if(scope.role==='viewer'||!executionPermissions(listing)[operation])fail('当前账号无权进行本次执行操作。');if(!uuid(requestID))fail('原请求编号无法核对。');return {request_id:requestID,expected_version:listing.task_version,reason:checkedReason(reason)};}
export function executionApproveBody({scope,listing,preview,consent,externalConsent,reason,requestID,planVersion}){
 const body=common(scope,listing,reason,requestID,'approve');checkExecutionPreview(preview);
 if(!executionPreviewCurrent(preview,listing,planVersion))fail('原任务、计划或执行条件已变化，请重新预览后批准。');
 if(!executionConsentMatches(consent,scope,preview))fail('请先核对全部实际输入和目的地，再批准本次只读执行。');
 const external=preview.steps.some(step=>step.external);if(external&&externalConsent!==true)fail('请明确批准本次完整输入向以上登记目的地外发。');
 return {...body,expected_plan_version:preview.plan_version,approval_hash:preview.approval_hash,authorize_external_inputs:external};
}
export function executionCommandBody({scope,listing,operation,reason,requestID,acknowledgePossibleCharge=false,decision}){
 if(!['retry','stop','review'].includes(operation))fail('执行操作无法核对。');
 const body=common(scope,listing,reason,requestID,operation),row=listing.execution;if(!row)fail('原执行记录尚未读取。');
 if((operation==='retry'||operation==='review'&&decision!=='rejected')&&listing.scope_error)fail('当前执行范围已变化，请先核对原记录。');
 const result={...body,expected_execution_version:row.version};
 if(operation==='retry'){if(acknowledgePossibleCharge!==true)fail('请先确认原调用可能已经处理或计费，重试也可能计费。');result.acknowledge_possible_charge=true;}
 if(operation==='review'){if(!['accepted','rejected'].includes(decision)||!text(row.result_hash))fail('请选择核对决定，并先读取已保存的完整结果。');result.decision=decision;result.result_hash=row.result_hash;}
 return result;
}
export function checkExecutionPending(value,scope){
 if(!exact(value,['scope','operation','body'])||!sameExecutionIdentity(value.scope,scope)||!exact(value.scope,['workspace_id','user_id','username','role','task_id','plan_id'])||!text(value.scope.role)||!['approve','retry','stop','review'].includes(value.operation)||!uuid(value.body?.request_id)||!integer(value.body?.expected_version)||!text(value.body?.reason)||value.body.reason.length>2000)fail('原执行请求身份或完整内容无法核对，请保留原记录。');
 const fields=['request_id','expected_version','reason'];let extra=[];
 if(value.operation==='approve'){extra=['expected_plan_version','approval_hash','authorize_external_inputs'];if(!integer(value.body.expected_plan_version)||!text(value.body.approval_hash)||typeof value.body.authorize_external_inputs!=='boolean')fail('原执行批准记录不完整，请保留原请求。');}
 else {extra=['expected_execution_version'];if(!integer(value.body.expected_execution_version))fail('原执行版本无法核对。');if(value.operation==='retry'){extra.push('acknowledge_possible_charge');if(value.body.acknowledge_possible_charge!==true)fail('原重试计费确认缺失。');}if(value.operation==='review'){extra.push('decision','result_hash');if(!['accepted','rejected'].includes(value.body.decision)||!text(value.body.result_hash))fail('原人工核查决定不完整。');}}
 if(!exact(value.body,[...fields,...extra]))fail('原执行请求包含无法核对的字段，请保留原记录。');return value;
}
export function executionPending(scope,operation,body){const value={scope:{...scope},operation,body:copy(body)};return checkExecutionPending(value,scope);}
export function restoreExecutionJournal(value,scope){
 if(value===null)return {schema:1,scope:{...scope},form:emptyExecutionForm(),pending:null};
 if(!exact(value,['schema','scope','form','pending'])||value.schema!==1||!sameExecutionIdentity(value.scope,scope)||!exact(value.scope,['workspace_id','user_id','username','role','task_id','plan_id'])||!text(value.scope.role)||!exact(value.form,['reason'])||typeof value.form.reason!=='string'||value.form.reason.length>2000||(value.pending!==null&&!record(value.pending)))fail('本机执行草稿无法完整恢复，已阻止写入，请保留原记录。');
 if(value.pending)checkExecutionPending(value.pending,scope);return copy(value);
}
export function writeExecutionJournal(storage,key,state,scope,knownPending=null){
 restoreExecutionJournal(state,scope);const previous=restoreExecutionJournal(JSON.parse(storage.getItem(key)||'null'),scope);
 if(previous.pending&&executionCanonical(previous.pending)!==executionCanonical(state.pending)&&!(state.pending===null&&executionCanonical(previous.pending)===executionCanonical(knownPending)))fail('另一页面保留了原执行请求，请先恢复，不能覆盖本机记录。');
 const encoded=JSON.stringify(state);storage.setItem(key,encoded);if(storage.getItem(key)!==encoded)fail('本机执行恢复记录未能读回，已阻止本次操作。');return state;
}
export function executionRecoveryBody(pending,scope,version,action){
 checkExecutionPending(pending,scope);if(scope.role==='viewer'||!integer(version)||!['probe','abandon'].includes(action))fail('原执行恢复条件或当前权限无效。');
 return {operation:pending.operation,original_request:copy(pending.body),action,expected_version:version,reason:action==='abandon'?'明确作废未保存的原执行请求，保留草稿和历史':'仅核对原执行请求是否保存，不新增工具调用'};
}
export function executionRecoveryVersion(probe,listing){const version=probe?.task_version??listing?.task_version;if(!integer(version))fail('原请求任务版本尚未读取。');return version;}
export function executionRequestPath(pending,scope){checkExecutionPending(pending,scope);return '/'+pending.operation;}

// A server refresh from an execution must not acknowledge or replace human edits.
export function workflowNoteSnapshot(current){return {step_id:current.task.workflow_runtime.current_step_id,note:current.note||'',branch:current.branch||'',override:current.override||'',verdict:current.verdict||'',pending:!!(current.dirty||current.pendingAction),...(current.pendingAction?{action:copy(current.pendingAction)}:{})};}
export function workflowNoteSnapshotMatches(sent,current){return !!sent?.task&&!!current?.task&&sent.task.id===current.task.id&&sent.task.workflow_runtime.current_step_id===current.task.workflow_runtime.current_step_id&&['note','branch','override','verdict'].every(key=>sent[key]===current[key])&&!current.pendingAction;}
export function workflowNoteCacheMatchesSent(sentRaw,currentRaw){return typeof sentRaw==='string'&&sentRaw===currentRaw;}
export function workflowTaskRefreshPolicy(current,incoming){
 if(!current?.task||!incoming||current.task.id!==incoming.id||!integer(incoming.version)||incoming.version<current.task.version)return {apply:false,archive:false,replaceDraft:false};
 const unsaved=!!(current.dirty||current.pendingAction),changed=current.task.workflow_runtime.current_step_id!==incoming.workflow_runtime.current_step_id||(incoming.status==='workflow_cancelled'&&current.task.status!=='workflow_cancelled');
 return {apply:true,archive:unsaved&&changed,replaceDraft:changed||!unsaved};
}
export function workflowRemoteDraftChanged(current,incoming){
 const step=current?.task?.workflow_runtime.current_step_id;
 if(!step||!current.dirty||incoming.workflow_runtime.current_step_id!==step)return false;
 const before=current.task.workflow_runtime.drafts?.[step]||{},after=incoming.workflow_runtime.drafts?.[step]||{};
 if(executionCanonical(before)===executionCanonical(after))return false;
 return !((after.note||'')===(current.note||'')&&(after.branch==null?'':after.branch?'yes':'no')===(current.branch||'')&&(after.override_reason||'')===(current.override||'')&&(after.perception_decision||'')===(current.verdict||''));
}
function checkNoteRecovery(value,scope){
 if(!exact(value,['schema','scope','drafts'])||value.schema!==1||!exact(value.scope,['workspace_id','user_id','username','task_id'])||!['workspace_id','user_id','username','task_id'].every(key=>text(scope?.[key])&&value.scope[key]===scope[key])||!Array.isArray(value.drafts)||value.drafts.some(entry=>!exact(entry,['draft','saved_at'])||!record(entry.draft)||!text(entry.saved_at)||!text(entry.draft.step_id)||!['note','branch','override','verdict'].every(key=>typeof entry.draft[key]==='string')||typeof entry.draft.pending!=='boolean'||(entry.draft.action!=null&&!record(entry.draft.action))))fail('原人工说明恢复记录无法完整核对，已保留原记录并阻止覆盖。');return value;
}
export function readWorkflowNoteRecovery(storage,key,scope){const raw=storage.getItem(key);return checkNoteRecovery(raw===null?{schema:1,scope:{...scope},drafts:[]}:JSON.parse(raw),scope);}
export function preserveWorkflowNoteRecovery(storage,key,scope,draft,at){
 const state=copy(readWorkflowNoteRecovery(storage,key,scope));
 if(!state.drafts.some(entry=>executionCanonical(entry.draft)===executionCanonical(draft)))state.drafts.push({draft:copy(draft),saved_at:at});checkNoteRecovery(state,scope);
 const encoded=JSON.stringify(state);storage.setItem(key,encoded);if(storage.getItem(key)!==encoded)fail('旧步骤说明未能读回，已阻止覆盖当前人稿。');return state;
}

export const executionFieldLabels={value:'处理结果',count:'数量',total:'合计',sample_count:'有效对象数',missing_count:'缺少字段的对象数',records:'结果记录',evidence:'原始依据',source:'来源',sources:'来源清单',source_name:'来源名称',attributes:'业务字段',fields:'字段清单',status:'状态',reason:'说明',message:'说明',title:'步骤名称',agent:'执行智能体',action:'处理动作',input:'完整实际输入',output:'已保存真实结果',steps:'逐步执行记录',destination:'实际目的地',external:'是否外发',unit:'声明单位',type:'字段类型',kind:'字段类型',name:'名称',version:'版本',capability_name:'工具名称',result_hash:'结果核对凭据',progress:'后台进度',error:'失败说明',history:'原操作历史',review:'人工核查决定',decision:'核查决定',at:'保存时间',started_at:'开始时间',finished_at:'结束时间',saved_at:'保存时间',observed_at:'原观察时间',evidence_status:'依据完整性',purpose:'目标用途',goal:'原业务目标',plan:'已保存计划',execution:'执行与核查记录',receipt:'真实调用收据',provider:'服务供应方',usage:'服务回报用量',total_tokens:'服务回报 token 数',duration_ms:'耗时（毫秒）',execution_advanced_workflow:'本次执行是否自动推进人工流程',physical_state_verified:'是否核实现场状态',possible_charge:'原调用可能计费',question:'核查问题',required_fields:'要求核对字段',entity_ids:'业务对象范围',selected_entity_ids:'已选对象范围',entity_snapshots:'原对象快照',object_snapshots:'原对象快照',input_bindings:'输入来源',output_fields:'输出字段说明'};
const semantic=value=>typeof value==='boolean'?(value?'是':'否'):value===null||value===undefined?'未提供，保持未知':String(value);
export function executionExportMarkdown(value){
 const lines=['# 原任务只读执行与人工核查记录','','计划、真实执行、结果采用和正式业务验收分别保存。未知单位、时间及费用保持未知。',''];
 function render(item,depth=2,label='已保存记录'){
  lines.push('#'.repeat(Math.min(depth,6))+' '+label,'');
  if(item===null||typeof item!=='object'){lines.push(semantic(item),'');return;}
  if(Array.isArray(item)){lines.push('共 '+item.length+' 项。','');item.forEach((entry,index)=>render(entry,depth+1,'第 '+(index+1)+' 项'));return;}
  for(const [key,entry] of Object.entries(item)){const name=executionFieldLabels[key]||key;if(entry!==null&&typeof entry==='object')render(entry,depth+1,name);else lines.push('- '+name+'：'+semantic(entry),'');}
 }
 render(value);return lines.join('\n');
}
