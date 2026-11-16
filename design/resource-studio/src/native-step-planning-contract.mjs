export const PLANNING_STATES={queued:'等待后台规划',running:'正在生成计划',needs_review:'计划已保存，等待人工核对',failed:'规划未完成',response_unknown:'云调用结果待核实',cancelled:'已停止后续规划',superseded:'已由后续计划替代'};
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const integer=value=>Number.isInteger(value)&&value>=0;
const text=value=>typeof value==='string'&&value.trim().length>0;
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
const fail=message=>{throw Error(message);};
const exact=(value,keys)=>record(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
export function planningScope(account,taskID){
 const value={workspace_id:account?.workspace_id,user_id:account?.user_id,username:account?.username,role:account?.role,task_id:taskID};
 if(Object.values(value).some(item=>!text(item)))fail('当前账号或任务身份尚未核实，原草稿和请求保留。');
 return value;
}
export function samePlanningScope(left,right){return !!left&&!!right&&['workspace_id','user_id','username','role','task_id'].every(key=>left[key]===right[key]);}
const samePlanningIdentity=(left,right)=>!!left&&!!right&&['workspace_id','user_id','username','task_id'].every(key=>left[key]===right[key]);
export function planningStorageKey(scope){return 'ga-native-step-planning:'+JSON.stringify([scope.workspace_id,scope.user_id,scope.username,scope.task_id]);}
export function planningSessionStamp(storage){return ['ga-setup-csrf','ga-setup-csrf-workspace','ga-setup-csrf-user','ga-setup-csrf-username'].map(key=>storage.getItem(key)||'').join('\u0000');}
export function planningSessionMatches(scope,storage,stamp){
 return planningSessionStamp(storage)===stamp&&[['ga-setup-csrf-workspace','workspace_id'],['ga-setup-csrf-user','user_id'],['ga-setup-csrf-username','username']].every(([key,field])=>storage.getItem(key)===scope[field]);
}
export function emptyPlanningForm(){return {model_profile_id:'',reason:''};}
function checkModel(model){if(!record(model)||!['id','name','provider','model'].every(key=>text(model[key])))fail('模型身份尚未完整读取，请重新读取后核对。');return model;}
export function checkPlanningRow(row){
 if(!record(row)||!text(row.id)||!integer(row.version)||!Object.hasOwn(PLANNING_STATES,row.status)||!text(row.created_at)||typeof row.has_saved_response!=='boolean'||!text(row.request_id))fail('规划记录尚未完整读取，原记录保留。');
 if(row.proposal!==null&&row.proposal!==undefined){if(!record(row.proposal)||typeof row.proposal.summary!=='string'||!Array.isArray(row.proposal.steps)||row.proposal.steps.some(step=>!record(step)||!['title','agent','action'].every(key=>typeof step[key]==='string')||(step.capability_name!=null&&typeof step.capability_name!=='string')))fail('计划步骤尚未完整读取，不能据此执行。');}
 if(row.progress!=null&&typeof row.progress!=='string'&&!record(row.progress)||row.model_receipt!=null&&!record(row.model_receipt))fail('规划进度或调用收据尚未完整读取。');
 if(row.can_stop!==undefined&&typeof row.can_stop!=='boolean')fail('原规划停止权限尚未完整读取。');
 return row;
}
export function checkPlanningListing(value){
 if(!record(value)||!integer(value.task_version)||typeof value.can_manage!=='boolean'||!Array.isArray(value.models)||!Array.isArray(value.history)||(value.scope_error!=null&&typeof value.scope_error!=='string'))fail('原任务规划清单尚未完整读取，请刷新核对。');
 if(['can_stop','can_recover','history_truncated'].some(key=>value[key]!==undefined&&typeof value[key]!=='boolean'))fail('原规划操作权限或历史范围尚未完整读取。');
 value.models.forEach(checkModel);if(new Set(value.models.map(model=>model.id)).size!==value.models.length)fail('模型清单存在重复身份，请刷新核对。');
 if(value.current!=null)checkPlanningRow(value.current);value.history.forEach(checkPlanningRow);return value;
}
export function checkPlanningPreview(value){
 if(!record(value)||!integer(value.task_version)||!text(value.preview_hash)||!Array.isArray(value.messages)||!value.messages.length||value.messages.some(message=>!record(message)||!text(message.role)||typeof message.content!=='string')||!record(value.options)||!Array.isArray(value.tools)||value.tools.some(tool=>!record(tool)||!text(tool.id)||!text(tool.name)||typeof tool.external!=='boolean')||!integer(value.input_bytes)||typeof value.notice!=='string')fail('完整发送预览尚未读取，不能授权云调用。');
 checkModel(value.model);return value;
}
function canonical(value){if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';if(record(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical(value[key])).join(',')+'}';return JSON.stringify(value);}
export function planningConsent(scope,preview){checkPlanningPreview(preview);return {scope:{...scope},preview:canonical(preview)};}
export function planningConsentMatches(consent,scope,preview){return !!consent&&samePlanningScope(consent.scope,scope)&&consent.preview===canonical(checkPlanningPreview(preview));}
export function planningPreviewCurrent(preview,listing,modelID){return !!preview&&!!listing&&preview.task_version===listing.task_version&&preview.model.id===modelID&&listing.models.some(model=>canonical(model)===canonical(preview.model));}
export function planningCanStart(listing){return !!listing&&!listing.scope_error&&!['queued','running','response_unknown'].includes(listing.current?.status);}
export function planningNeedsPolling(listing){return ['queued','running'].includes(listing?.current?.status);}
export function planningPermissions(listing){return {manage:!!listing?.can_manage,stop:listing?.can_stop??listing?.current?.can_stop??!!listing?.can_manage,recover:listing?.can_recover??!!listing?.can_manage};}
export function planningRecoveryVersion(result,listing){const version=result?.task_version??result?.version??listing?.task_version;if(!integer(version))fail('原请求任务版本尚未完整读取。');return version;}
function checkedReason(value){if(typeof value!=='string'||!value.trim()||value.trim().length>2000)fail('请填写本次操作理由（最多2000字）。');return value.trim();}
export function planningStartBody({scope,listing,form,preview,consent,requestID}){
 checkPlanningListing(listing);checkPlanningPreview(preview);
 if(scope.role==='viewer'||!listing.can_manage||!planningCanStart(listing))fail('当前任务不能开始新的规划，请先核对权限及原规划状态。');
 if(!uuid(requestID))fail('原请求编号无法核对。');
 if(!planningPreviewCurrent(preview,listing,form.model_profile_id))fail('任务版本或模型已经变化，请重新预览并授权。');
 if(!planningConsentMatches(consent,scope,preview))fail('请先核对完整发送文字，再授权这一次云调用。');
 return {request_id:requestID,expected_version:listing.task_version,reason:checkedReason(form.reason),model_profile_id:form.model_profile_id,preview_hash:preview.preview_hash,cloud_authorized:true};
}
export function planningStopBody({scope,listing,form,requestID}){
 checkPlanningListing(listing);if(scope.role==='viewer'||!planningPermissions(listing).stop||!listing.current||['cancelled','superseded'].includes(listing.current.status))fail('当前账号或原规划状态不能停止后续规划。');if(!uuid(requestID))fail('原请求编号无法核对。');
 return {request_id:requestID,expected_version:listing.task_version,expected_plan_version:listing.current.version,reason:checkedReason(form.reason)};
}
export function planningPending(scope,operation,body,planID=null){
 const value={scope:{...scope},operation,plan_id:planID,body:JSON.parse(JSON.stringify(body))};checkPlanningPending(value,scope);return value;
}
export function checkPlanningPending(value,scope){
 if(!record(value)||!samePlanningIdentity(value.scope,scope)||!['start','stop'].includes(value.operation)||!uuid(value.body?.request_id)||!integer(value.body?.expected_version)||!text(value.body?.reason))fail('本机原请求身份或内容无法核对，已阻止重发，请保留原记录。');
 const body=value.body;if(value.operation==='start'){if(value.plan_id!==null||!exact(body,['request_id','expected_version','reason','model_profile_id','preview_hash','cloud_authorized'])||!text(body.model_profile_id)||!text(body.preview_hash)||body.cloud_authorized!==true)fail('原云调用授权记录不完整，请保留原请求。');}
 else if(!text(value.plan_id)||!exact(body,['request_id','expected_version','expected_plan_version','reason'])||!integer(body.expected_plan_version))fail('原停止请求不完整，请保留原请求。');
 return value;
}
export function restorePlanningJournal(value,scope){
 if(value===null)return {schema:1,scope:{...scope},form:emptyPlanningForm(),pending:null};
 // Role changes retain identity, while write access is always checked against the live account.
 const identityMatches=samePlanningIdentity(value?.scope,scope);
 if(!exact(value,['schema','scope','form','pending'])||value.schema!==1||!identityMatches||!exact(value.scope,['workspace_id','user_id','username','role','task_id'])||!text(value.scope.role)||!exact(value.form,['model_profile_id','reason'])||typeof value.form.model_profile_id!=='string'||typeof value.form.reason!=='string'||value.form.reason.length>2000||(value.pending!==null&&!record(value.pending)))fail('本机计划草稿无法完整恢复，已阻止写入，请保留原记录。');
 if(value.pending)checkPlanningPending(value.pending,value.scope);
 return JSON.parse(JSON.stringify(value));
}
export function writePlanningJournal(storage,key,state,scope,knownPending=null){
 restorePlanningJournal(state,scope);const previous=restorePlanningJournal(JSON.parse(storage.getItem(key)||'null'),scope);
 if(previous.pending&&canonical(previous.pending)!==canonical(state.pending)&&!(state.pending===null&&canonical(previous.pending)===canonical(knownPending)))fail('另一页面保留了原规划请求，请先恢复该请求，不能覆盖本机记录。');
 const encoded=JSON.stringify(state);storage.setItem(key,encoded);if(storage.getItem(key)!==encoded)fail('本机恢复记录未能读回，已阻止本次操作。');return state;
}
export function planningRecoveryBody(pending,scope,version,action){
 // Recovery stays with the original account and task; current permissions still apply.
 checkPlanningPending(pending,scope);if(scope.role==='viewer'||!integer(version)||!['probe','abandon'].includes(action))fail('原请求核对条件或当前权限无效。');
 return {operation:pending.operation,original_request:JSON.parse(JSON.stringify(pending.body)),...(pending.operation==='stop'?{plan_id:pending.plan_id}:{}),action,expected_version:version,reason:action==='abandon'?'明确作废未保存的原规划请求，保留草稿及历史':'只核对原规划请求是否已保存，不新增云调用'};
}
export function planningRequestPath(pending,scope){checkPlanningPending(pending,scope);return pending.operation==='start'?'':'/'+encodeURIComponent(pending.plan_id)+'/stop';}
