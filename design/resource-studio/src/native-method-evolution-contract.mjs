import {methodCaseBody,methodExpiry} from './native-method-contract.mjs';
export const EVOLUTION_STATES={draft:'修订草稿',candidate:'等待人工审核',trusted:'已批准，后续任务可明确采用',rejected:'已拒绝，未发布',revoked:'已撤回，历史保留',labels_saved:'标签已冻结，尚未比较',blocked:'独立性核对受阻',compared:'已保存同输入模板对比'};
export const EXPECTATIONS={required:'必须核查',unnecessary:'不需要这项',unknown:'尚不能判断'};
export const METRICS={required_covered:'必需项覆盖',required_missing:'必需项遗漏',unnecessary:'不必要项',unknown:'未知项',evidence_ready:'依据可核对',evidence_blocked:'依据受阻',check_count:'核查项数'};
export const stable=value=>JSON.stringify(value,(_,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const ops=['revise','evaluate','draft','submit','review','revoke'];
export async function evolutionHash(body){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(stable(body))))].map(x=>x.toString(16).padStart(2,'0')).join('');}
export function evolutionScope(account,taskID,revision='new'){
 const scope={workspace_id:account.workspace_id,actor_id:account.user_id,task_id:taskID,revision};
 if(Object.values(scope).some(value=>typeof value!=='string'||!value))throw Error('当前账号与任务身份尚未核对，不能保存修订请求');return scope;
}
export const evolutionKey=scope=>'ga-native-method-evolution:'+stable(scope);
export function evolutionForm(row){return {title:row?.title||'',summary:row?.summary||'',limits:row?.limits||'',steps:(row?.steps||[]).map(({id,kind,field,instruction})=>({id,kind,field,instruction})),reason:'',expiry:'',parent_id:'',feedback:[],tests:[],test_id:''};}
export function readEvolution(storage,scope){
 const raw=storage.getItem(evolutionKey(scope));if(raw===null)return {raw:null,schema:1,scope,form:evolutionForm(),dirty:false,pending:null,receipts:[]};
 let value;try{value=JSON.parse(raw);}catch{throw Error('本机修订恢复记录无法读取，原记录保留');}
 if(value.schema!==1||stable(value.scope)!==stable(scope)||!value.form||!Array.isArray(value.form.steps)||!Array.isArray(value.form.tests)||!Array.isArray(value.receipts)||value.pending&&(!ops.includes(value.pending.operation)||typeof value.pending.body?.request_id!=='string'||!Number.isInteger(value.pending.body.expected_version)||!value.pending.form_at_send))throw Error('本机修订记录范围或原请求不完整，原记录保留');
 return {...value,raw};
}
export function writeEvolution(storage,scope,next,previous){
 const key=evolutionKey(scope);if(storage.getItem(key)!==previous.raw)throw Error('另一页面已更新本机修订，请重新读取；本页内容保留');
 const {raw:ignored,...value}=next;const raw=JSON.stringify({...value,schema:1,scope});storage.setItem(key,raw);
 if(storage.getItem(key)!==raw)throw Error('本机修订未能读回，已阻止发送');return {...value,schema:1,scope,raw};
}
export function ownEvolutionEntries(storage,scope){
 const rows=[];for(let i=0;i<storage.length;i++){const key=storage.key(i);if(!key?.startsWith('ga-native-method-evolution:'))continue;let other;try{other=JSON.parse(key.slice('ga-native-method-evolution:'.length));}catch{continue;}
 if(['workspace_id','actor_id','task_id'].every(name=>other[name]===scope[name])){const value=readEvolution(storage,other);if(value.dirty||value.pending)rows.push(value);}}
 return rows;
}
export function checkedEvolution(view,taskID){
 const value=view?.evolution,problem=()=>{throw Error('修订与评测资料未完整读取，原稿和待核请求保留');},strings=items=>Array.isArray(items)&&items.every(item=>typeof item==='string'),permission=item=>typeof item?.allowed==='boolean'&&typeof item.reason==='string';
 if(view?.task_id!==taskID||!Number.isInteger(view.version)||value?.schema!=='native-method-evolution-v1'||!Array.isArray(value.revisions)||!Array.isArray(value.evaluations)||!Array.isArray(value.feedback_options)||!permission(value.permissions?.revise))problem();
 const artifact=item=>typeof item?.id==='string'&&Number.isInteger(item.version)&&item.version>0&&hash(item.record_hash)&&typeof item.status==='string';
 const content=item=>typeof item?.title==='string'&&typeof item.summary==='string'&&typeof item.limits==='string'&&Array.isArray(item.steps)&&item.steps.every(step=>['id','kind','field','instruction'].every(key=>typeof step[key]==='string'));
 if(value.baseline!==null&&(!artifact(value.baseline)||!content(value.baseline)||!hash(value.baseline.content_hash)))problem();
 for(const row of value.revisions)if(!artifact(row)||!content(row)||!hash(row.content_hash)||!hash(row.proposal_hash)||!Array.isArray(row.parent?.steps)||!['edit','evaluate','submit','review','revoke'].every(key=>permission(row.permissions?.[key])))problem();
 for(const row of value.evaluations){
  if(!artifact(row)||typeof row.case_id!=='string'||!hash(row.proposal_hash)||!['labels_saved','blocked','compared'].includes(row.status)||typeof row.review_ready!=='boolean'||!strings(row.reasons)||!Array.isArray(row.tests)||typeof row.independence?.passed!=='boolean'||!strings(row.independence.reasons))problem();
  for(const item of row.tests)if(typeof item.task_id!=='string'||typeof item.task_title!=='string'||!Array.isArray(item.labels)||item.labels.some(label=>!Object.keys(EXPECTATIONS).includes(label.expectation)||['kind','field','reason'].some(key=>typeof label[key]!=='string')))problem();
  if(row.comparison!==null&&(!row.comparison||!['baseline','candidate','delta'].every(arm=>Object.keys(METRICS).every(key=>Number.isInteger(row.comparison[arm]?.[key])&&(arm==='delta'||row.comparison[arm][key]>=0)))||!Array.isArray(row.comparison.tests)))problem();
 }
 return value;
}
export async function checkedEvolutionReceipt(receipt,original,scope){
 if(receipt?.request_id!==original.body.request_id||receipt.operation!==original.operation||!['saved','unknown','abandoned','legacy_unknown'].includes(receipt.status)||receipt.request_hash!==await evolutionHash(original.body))throw Error('原请求回执无法精确核对，原请求保留');
 if(receipt.status==='legacy_unknown')throw Error('旧回执没有不可变产物证明，不能推断本次修订已保存');
 if(['workspace_id','actor_id','task_id'].some(name=>receipt[name]!==scope[name]))throw Error('回执账号、工作区或任务不一致，原请求保留');
 if(receipt.status==='saved'){
  const a=receipt.original,b=receipt.current;if(receipt.saved!==true||!a||!b||!['revision','evaluation'].includes(a.kind)||typeof a.id!=='string'||a.id!==b.id||!Number.isInteger(a.version)||!Number.isInteger(b.version)||b.version<a.version||!hash(a.record_hash)||!hash(b.record_hash)||typeof b.status!=='string')throw Error('原产物与当前版本回执不完整，原请求保留');
  if(original.operation==='evaluate'&&a.kind!=='evaluation'||original.operation!=='evaluate'&&a.kind!=='revision'||original.operation!=='revise'&&original.operation!=='evaluate'&&a.id!==original.body.case_id)throw Error('原请求产物范围不一致，原请求保留');
 }else if(receipt.saved!==false||receipt.original!==null||receipt.current!==null)throw Error('未确认请求不能当作保存成功');
 return receipt;
}
export function acknowledgeEvolution(storage,scope,original,receipt){
 const current=readEvolution(storage,scope);if(!current.pending||stable(current.pending)!==stable(original))return null;
 const unchanged=stable(current.form)===stable(original.form_at_send);
 const savedDraft=['revise','draft'].includes(original.operation)||original.operation==='evaluate'&&original.body.action==='freeze';
 return writeEvolution(storage,scope,{...current,pending:null,dirty:savedDraft&&receipt.status==='saved'&&unchanged&&receipt.original.record_hash===receipt.current.record_hash?false:current.dirty,receipts:[...current.receipts,{...receipt,body:original.body,operation:original.operation} ]},current);
}
export const feedbackKey=row=>stable({task_id:row.task_id,use_hash:row.use_hash,index:row.index,entry_hash:row.entry_hash});
export function matchingFeedback(row,parent){return !!parent&&row.parent_case_id===parent.id&&row.parent_case_version===parent.version&&row.parent_record_hash===parent.record_hash&&row.parent_content_hash===parent.content_hash;}
export function labelPairs(parent,candidate){const map=new Map();for(const step of [...(parent?.steps||[]),...(candidate?.steps||[])])map.set(stable([step.kind,step.field]),{kind:step.kind,field:step.field,expectation:'unknown',reason:''});return [...map.values()];}
export function evolutionBody(operation,view,form,row,parent,decision){
 const reason=form.reason.trim();if(!reason)throw Error('请填写本次修订、标注或审核的说明');
 const common={request_id:crypto.randomUUID(),expected_version:view.version,reason};
 const content={title:form.title.trim(),summary:form.summary.trim(),limits:form.limits.trim(),steps:form.steps.map(({id,kind,field,instruction})=>({id,kind,field,instruction:instruction.trim()}))};
 if(['revise','draft'].includes(operation)){if(!content.title||!content.steps.length||content.steps.length>5)throw Error('请填写修订名称与一至五项核查做法');for(const step of content.steps)if(!view.field_options.find(field=>field.key===step.field)?.templates.includes(step.kind))throw Error('请核对已登记业务字段与核查方式');}
 if(operation==='revise'){
  if(!parent?.record_hash||parent.status!=='trusted')throw Error('请先核对已批准的父方法');
  const refs=view.evolution.feedback_options.filter(item=>matchingFeedback(item,parent)&&form.feedback.includes(feedbackKey(item))).map(({task_id,use_hash,index,entry_hash})=>({task_id,use_hash,index,entry_hash}));if(refs.length>20)throw Error('一次修订最多选择二十条反馈');if(!refs.length)throw Error('请选择至少一条正式验收并消费此父方法的反馈');
  return {...common,parent_case_id:parent.id,parent_case_version:parent.version,parent_record_hash:parent.record_hash,parent_content_hash:parent.content_hash,...content,feedback_refs:refs};
 }
 const caseBody={...common,...methodCaseBody(row)};
 if(operation==='draft')return {...caseBody,acceptance_hash:view.source.acceptance_hash,...content};
 if(operation==='freeze'){
  if(!form.tests.length)throw Error('请先选择独立验收任务并保存每项人工标签');if(form.tests.length>20||form.tests.some(item=>!item.labels.length||item.labels.length>100))throw Error('请为一至二十个测试任务分别保存一至一百项标签');for(const item of form.tests)for(const label of item.labels)if(!label.reason.trim()||!Object.keys(EXPECTATIONS).includes(label.expectation))throw Error('每项标签都需要独立结论与理由，未知不能省略');
  return {...caseBody,action:'freeze',proposal_hash:row.proposal_hash,tests:form.tests.map(({task_id,expected_task_version,acceptance_hash,labels})=>({task_id,expected_task_version,acceptance_hash,labels:labels.map(({kind,field,expectation,reason})=>({kind,field,expectation,reason:reason.trim()}))}))};
 }
 if(operation==='compare'){const evaluation=row.latest_evaluation;if(!evaluation)throw Error('请先独立保存并冻结输入与标签');return {...caseBody,action:'compare',proposal_hash:row.proposal_hash,evaluation_id:evaluation.id,expected_evaluation_version:evaluation.version,evaluation_record_hash:evaluation.record_hash};}
 if(operation==='review')return {...caseBody,decision,valid_until:methodExpiry(form.expiry)};
 return caseBody;
}
