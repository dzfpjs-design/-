import {defaultNativeGoal,buildNativeGoal} from './native-goal-contract.mjs';

export const proposalStates={queued:'等待生成建议',running:'正在生成建议',response_unknown:'模型回复状态待核对',needs_clarification:'需要补充说明',needs_review:'建议待核对',confirmed:'已核对含义，尚待采用',rejected:'已拒绝此建议',adopted:'已采用此建议',cancelled:'已停止，历史保留',failed:'建议生成未完成'};
export const sourceLabels={archive:'原业务档案',observations:'设备或人工登记记录',either:'档案与登记记录'};
export const operationLabels={mean:'平均值',sum:'合计',count:'证据记录数',min:'最小值',max:'最大值',keyword:'按文字筛选',fulltext:'按内容查找并排序',aggregate:'计算登记值',all_terms:'包含全部关键词',any_terms:'包含任一关键词'};
export const comparisonLabels={eq:'等于',gt:'大于',gte:'至少',lt:'小于',lte:'至多'};
const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const text=value=>typeof value==='string'&&!!value.trim();
const clone=value=>JSON.parse(JSON.stringify(value));
export const proposalScope=(account,taskId)=>JSON.stringify([account.workspace_id,account.user_id||'',account.username,account.role,taskId]);
export const proposalJournalKey=(account,taskId)=>'ga-native-query-proposal:'+proposalScope(account,taskId);
export const proposalSeedKey=(account,taskId)=>'ga-native-query-seed:'+proposalScope(account,taskId);
const normalized=value=>Array.isArray(value)?value.map(normalized):record(value)?Object.fromEntries(Object.keys(value).sort().filter(key=>value[key]!==null&&value[key]!==undefined).map(key=>[key,normalized(value[key])])):value;
export const proposalDraftEqual=(a,b)=>JSON.stringify(normalized(a))===JSON.stringify(normalized(b));
export const emptyProposalForm=()=>({question:'',source:'archive',hours:'24',verification:'confirmed',evidenceConfirmed:false,reason:''});

export function proposalEvidence(form){
 if(!form.evidenceConfirmed)return null;
 if(!Object.hasOwn(sourceLabels,form.source))throw Error('请选择本任务已有资料范围');
 const archive=form.source==='archive',hours=Number(form.hours);
 if(!archive&&(!Number.isInteger(hours)||hours<1||hours>720))throw Error('请填写1至720之间的资料有效小时数');
 if(!archive&&!['confirmed','unverified_extraction'].includes(form.verification))throw Error('请核对登记资料要求');
 return {source:form.source,minimum_verification:archive?'recorded':form.verification,minimum_records_per_entity:1,time:archive?{mode:'archive',timezone:'Asia/Shanghai'}:{mode:'latest',max_age_seconds:hours*3600,timezone:'Asia/Shanghai'}};
}
export function proposalFormFromDraft(draft){
 if(!record(draft)||typeof draft.question!=='string')throw Error('服务器查询说明无法读取，原输入保留');
 const evidence=draft.evidence;
 if(evidence!==null&&evidence!==undefined&&(!record(evidence)||!Object.hasOwn(sourceLabels,evidence.source)||!record(evidence.time)))throw Error('原查询资料范围无法恢复，请保留记录');
 if(evidence&&!['archive','latest'].includes(evidence.time.mode))throw Error('原查询使用当前表单不能编辑的时间范围；已保存说明保留，请管理员核对');
 return {...emptyProposalForm(),question:draft.question,source:evidence?.source||'archive',verification:evidence?.source==='archive'?'confirmed':evidence?.minimum_verification||'confirmed',hours:String((evidence?.time?.max_age_seconds||86400)/3600),evidenceConfirmed:!!evidence};
}
export function proposalDraftBody(form){if(typeof form.question!=='string'||form.question.length>4000)throw Error('查询说明最多4000字');return {question:form.question,evidence:proposalEvidence(form)};}
export function checkProposalView(value,taskId){
 if(record(value)&&value.draft===null)value={...value,draft:{question:'',evidence:null,revision:0,hash:null}};
 if(!record(value)||value.task_id!==taskId||!Number.isInteger(value.task_version)||value.task_version<1||!record(value.draft)||!Number.isInteger(value.draft.revision)||typeof value.draft.question!=='string'||!Array.isArray(value.history)||!Array.isArray(value.models)||typeof value.can_manage!=='boolean'||!['goal_draft','reference_only'].includes(value.mode))throw Error('查询助手返回记录与原任务无法核对，原输入和请求保留');
 for(const row of [...value.history,...(value.current?[value.current]:[])]){
  if(!record(row)||!text(row.id)||!Number.isInteger(row.version)||!Object.hasOwn(proposalStates,row.status))throw Error('查询建议记录无法核对，原记录保留');
  if(row.candidate){const c=row.candidate;if(!record(c)||typeof c.summary!=='string'||!Array.isArray(c.fields)||c.fields.some(field=>typeof field!=='string')||!Array.isArray(c.tool_steps)||c.tool_steps.some(step=>!record(step)||!text(step.id)||!text(step.capability_id)||!text(step.input_from)||!record(step.parameters))||!Array.isArray(c.questions)||!Array.isArray(c.limitations)||c.questions.concat(c.limitations).some(line=>typeof line!=='string')||c.relation_query&&(!Array.isArray(c.relation_query.root_ids)||!Array.isArray(c.relation_query.relation_types)))throw Error('已保存建议的字段、关系或处理范围无法核对，原稿和回执保留');}
 }
 return value;
}
export function newerProposalView(previous,next,taskId){checkProposalView(next,taskId);if(previous&&(next.task_version<previous.task_version||next.task_version===previous.task_version&&next.draft.revision<previous.draft.revision))return previous;return next;}
export function checkProposalPreview(value,view){
 if(!record(value)||value.task_id!==view.task_id||!text(value.preview_hash)||!Array.isArray(value.messages)||!value.messages.length||value.messages.some(item=>!record(item)||typeof item.role!=='string'||typeof item.content!=='string')||value.task_version!==view.task_version||value.draft_revision!==view.draft.revision||!Number.isInteger(value.input_bytes)||value.input_bytes<1)throw Error('发送预览与当前说明或任务版本不一致，请重新核对完整发送范围');
 return value;
}
export function checkProposalJournal(value,scope){
 if(value===null)return null;
 if(!record(value)||value.scope!==scope||!record(value.form)||typeof value.form.question!=='string'||typeof value.dirty!=='boolean'||value.pending&&(!record(value.pending)||!record(value.pending.body)||!text(value.pending.body.request_id)||!['draft','create','review','adopt','cancel','execute','support_cancel'].includes(value.pending.operation)))throw Error('本机查询恢复记录无法读取，原记录保留，已阻止覆盖');
 return value;
}
export function reconcileProposalSave(sent,current,currentDirty,serverDraft){
 if(sent.operation!=='draft')return {form:current,dirty:currentDirty};
 const server=proposalFormFromDraft(serverDraft),matchesSent=proposalDraftEqual(proposalDraftBody(current),{question:sent.body.question,evidence:sent.body.evidence});
 const matchesServer=serverDraft.question===sent.body.question&&proposalDraftEqual(serverDraft.evidence,sent.body.evidence);
 return matchesSent&&matchesServer?{form:sent.body.evidence===null?{...current,question:server.question,evidenceConfirmed:false}:{...server,reason:current.reason},dirty:false}:{form:current,dirty:true};
}
export function proposalGoalSpec(value){const spec=value?.goal_spec||value?.spec||value;if(!record(spec)||!Array.isArray(spec.required_fields)||!Array.isArray(spec.criteria)||!record(spec.evidence))throw Error('已采用的原目标草稿格式无法核对');return spec;}
export function goalFormFromProposal(value,metadata){
 const spec=proposalGoalSpec(value),evidence=spec.evidence;
 if(!['archive','latest'].includes(evidence.time?.mode))throw Error('此时间范围尚不能由原目标表单准确表达，原稿保留');
 if(spec.criteria.some(row=>!['evidence_sufficient','human_confirmation','field_value','query_value'].includes(row.kind))||spec.criteria.filter(row=>row.kind==='field_value').length>1||spec.criteria.filter(row=>row.kind==='query_value').length>1)throw Error('此建议的完成条件尚不能由原目标表单完整表达，原稿保留');
 const relation=spec.relation_query,field=spec.criteria.find(row=>row.kind==='field_value'),query=spec.criteria.find(row=>row.kind==='query_value');
 const form={...defaultNativeGoal(spec.purpose),fields:[...spec.required_fields],source:evidence.source,confirmation:evidence.source==='archive'?'confirmed':evidence.minimum_verification,hours:(evidence.time.max_age_seconds||86400)/3600,allowNew:false,targetField:field?.field||'',targetOperator:field?.operator||'eq',targetValue:field?.expected??'',querySteps:(spec.data_query?.tool_steps||[]).map(row=>({id:row.id,capabilityId:row.capability_id,inputFrom:row.input_from,parameters:clone(row.parameters)})),queryCriterion:query?{enabled:true,field:query.field,operator:query.operator,expected:query.expected}:{enabled:false,field:'',operator:'eq',expected:''},...(relation?{relationScope:{enabled:true,types:[...relation.relation_types],roots:[...relation.root_ids],direction:relation.direction,hops:relation.max_hops,includeRoots:relation.include_roots,at:relation.at?new Date(new Date(relation.at).getTime()+8*3600*1000).toISOString().slice(0,16):''}}:{})};
 buildNativeGoal(form,metadata);return form;
}
export function preserveQuerySeed(storage,account,taskId,form){
 const key=proposalSeedKey(account,taskId),existing=storage.getItem(key),body={scope:proposalScope(account,taskId),question:form.queryQuestion?.trim()||form.purpose?.trim()||'',source:form.source||'archive',hours:String(form.hours||24),verification:form.confirmation||'confirmed'};
 if(existing){const old=JSON.parse(existing);if(!proposalDraftEqual(old,body))throw Error('此原任务已有不同的查询说明恢复稿，已阻止覆盖');return body;}
 const encoded=JSON.stringify(body);storage.setItem(key,encoded);if(storage.getItem(key)!==encoded)throw Error('任务已保存，但查询说明尚未完整保留，请按原请求恢复');return body;
}
export function proposalCatalog(view){return view?.context||view?.catalog||{};}
export function proposalFieldLabel(catalog,key){const fields=catalog.fields;return (Array.isArray(fields)?fields.find(item=>item.key===key)?.label:fields?.[key]?.label||fields?.[key])||({value:'计算结果',sample_count:'参与计算的证据记录数',field:'处理字段',operation:'计算方式'})[key]||'登记输出';}
export function proposalFieldUnit(catalog,key){const fields=catalog.fields;return (Array.isArray(fields)?fields.find(item=>item.key===key)?.unit:fields?.[key]?.unit)||null;}
