// Shares the existing review controller, identity fence, durable scope and lock.
// These independent DTOs never relax the older author-review protocol.
import {REVIEW_BASE,reviewCopy,safeReviewJSON} from './business-flow-review-controller.mjs';
import {isNestedFlowPlan,nestedCapacity} from './nested-flow-review.mjs';

export const NESTED_REVIEW_MODES=Object.freeze(['author_self_review','engineering_simulation']);
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,HASH=/^[a-f0-9]{64}$/;
const VALUE_SCHEMA='report-business-flow-nested-plan-v192';
const need=(ok,message='递归流程核对记录无法验证；原输入和原请求仍保留。')=>{if(!ok)throw Error(message);};
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const flags=value=>['semantic_quality_verified','can_apply','can_publish','can_execute'].every(key=>value?.[key]===false);
const modeValid=value=>NESTED_REVIEW_MODES.includes(value);
const canonical=value=>JSON.stringify(value,(_key,item)=>object(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
const same=(left,right)=>canonical(left)===canonical(right);
const fresh=()=>({preview:null,reason:'',ack_scope:false,ack_inferences:false,result:null});
const rejected=value=>value?.code==='nested_review_source_not_applied'&&value.not_applied===true;
const COMMAND_KEYS=Object.freeze(['schema','request_id','expected_version','save_request_id','value_hash','ack_scope','ack_inferences','reason','review_mode']);

export function nestedAuthorReviewState(c,id=c.state.selected){return c.state.nestedAuthorReviews?.[id]||null;}
export function nestedAuthorReviewForm(c,id=c.state.selected){const s=nestedAuthorReviewState(c,id);return s?.forms?.[s.mode]||null;}
function state(c,id=c.state.selected){
 need(UUID.test(id||''));c.state.nestedAuthorReviews??={};
 return c.state.nestedAuthorReviews[id]??={schema:'nested-author-review-local-v195',mode:'author_self_review',forms:{author_self_review:fresh(),engineering_simulation:fresh()},pending:null,history:[],archives:[],notFound:false};
}
function persist(c){need(c.persist(),c.error);}
function active(c,id=c.state.selected){c.check();need(c.state.selected===id,'已切换业务稿；原核对输入和原请求保留在原业务。');}
function editable(c){active(c);need(c.canEdit&&c.localSaved&&!c.pending,'请先保全输入并核对原草稿保存结果。');}
function saved(c){editable(c);need(c.form&&!c.form.dirty&&c.detail?.saved&&c.detail.source_available===true&&c.form.version===c.detail.version&&isNestedFlowPlan(c.form.value),'请先保存当前递归流程并读取服务器这一版本，再进行核对。');}
async function hash(c,raw){return [...new Uint8Array(await c.crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw)))].map(value=>value.toString(16).padStart(2,'0')).join('');}

export function editNestedAuthorReview(c,patch){
 editable(c);const s=state(c);need(!s.pending,'原核对结果尚未确定，请先查询原请求。');
 need(object(patch)&&Object.keys(patch).every(key=>['mode','reason','ack_scope','ack_inferences'].includes(key)));
 if('mode'in patch){need(modeValid(patch.mode));if(patch.mode!==s.mode){s.mode=patch.mode;s.forms[s.mode].preview=null;s.forms[s.mode].ack_scope=false;s.forms[s.mode].ack_inferences=false;}}
 const form=s.forms[s.mode];if('reason'in patch){need(typeof patch.reason==='string'&&Array.from(patch.reason).length<=12000);form.reason=patch.reason;}
 for(const key of ['ack_scope','ack_inferences'])if(key in patch){need(typeof patch[key]==='boolean');form[key]=patch[key];}
 safeReviewJSON(s);persist(c);
}

function checkPreview(c,value,mode,id){
 const counts=nestedCapacity(c.form.value),flows=c.form.value.business.flows;
 need(value?.schema==='business-flow-nested-review-preview-v195'&&value.attempt_id===id&&value.expected_version===c.detail.version&&UUID.test(value.save_request_id||'')&&HASH.test(value.value_hash||'')&&HASH.test(value.source_snapshot_hash||'')&&value.value_schema===VALUE_SCHEMA&&value.review_mode===mode&&value.simulation_only===(mode==='engineering_simulation')&&integer(value.open_question_count)&&value.open_question_count===c.form.value.business.questions.length&&value.business_name===c.form.value.business.name&&Array.isArray(value.flows)&&value.flows.length===flows.length&&flags(value),'保存版本、核对方式、问题数量或来源预览无法核对。');
 value.flows.forEach((flow,index)=>need(flow?.flow_index===index&&flow.name===flows[index].name&&flow.action_count===counts[index].actions,'预览中的流程和递归动作数量与当前保存稿不一致。'));
 safeReviewJSON(value);return value;
}
export async function previewNestedAuthorReview(c){return c.operation(()=>c.locked(async()=>{
 saved(c);const id=c.state.selected,s=state(c),mode=s.mode;need(!s.pending,'请先恢复原核对请求。');await c.loadDetail(id);active(c,id);saved(c);need(mode==='engineering_simulation'||c.form.value.business.questions.length===0,'仍有未解决的待核问题，请先修订并保存，再检查作者自审版本。');
 const view=await c.http(REVIEW_BASE+'/attempts/'+id+'/nested-review-preview?review_mode='+mode);active(c,id);saved(c);need(s.mode===mode,'核对方式已变化，请重新检查保存稿。');checkPreview(c,view,mode,id);
 const form=s.forms[mode];form.preview=reviewCopy(view);form.ack_scope=false;form.ack_inferences=false;s.notFound=false;persist(c);c.notice=mode==='engineering_simulation'?'工程模拟预览已读取；这不是人工业务审查，待核问题仍保留。':'已读取这一保存版本；请对照全部流程和原文，处理全部待核问题后明确作者自审。';c.notify();return view;
}));}

function checkRecord(c,value,{mode,id}={}){
 const reviewMode=mode??value?.review_mode,attempt=id??c.state.selected,simulation=reviewMode==='engineering_simulation';
 need(modeValid(reviewMode)&&value?.schema==='business-flow-nested-review-result-v195'&&UUID.test(value.id||'')&&value.version===1&&value.attempt_id===attempt&&UUID.test(value.request_id||'')&&integer(value.draft_version)&&value.draft_version>=1&&UUID.test(value.save_request_id||'')&&value.reviewed_by===c.scope.actorId&&value.review_mode===reviewMode&&value.simulation_only===simulation&&value.value_schema===VALUE_SCHEMA&&value.status===(simulation?'engineering_reviewed':'qualified')&&value.human_review===(simulation?'not_human_review':'author_self_reviewed')&&value.ack_scope===true&&value.ack_inferences===true&&typeof value.reason==='string'&&typeof value.reviewed_at==='string'&&HASH.test(value.value_hash||'')&&HASH.test(value.qualification_hash||'')&&HASH.test(value.source_snapshot_hash||'')&&HASH.test(value.review_record_hash||'')&&isNestedFlowPlan(value.value)&&object(value.source_binding)&&flags(value),'核对回执的账号、版本、模式或权限边界不一致；未显示为核对成功。');
 need(Array.isArray(value.value.business?.flows)&&Array.isArray(value.value.business?.questions)&&(simulation||value.value.business.questions.length===0),'回执中的流程或待核问题与核对性质不一致。');safeReviewJSON(value);return value;
}
function checkResult(c,value,p){checkRecord(c,value,{mode:p.body.review_mode,id:p.attemptId});need(value.request_id===p.body.request_id&&value.draft_version===p.body.expected_version&&value.save_request_id===p.body.save_request_id&&value.value_hash===p.body.value_hash&&value.reason===p.body.reason&&value.source_snapshot_hash===p.source_snapshot_hash&&same(value.value,p.saved_value),'原核对回执与发出的保存版本、原值或说明不一致。');return value;}
function exact(c,p){
 active(c,p.attemptId);const s=nestedAuthorReviewState(c,p.attemptId),local=c.readOwn().nestedAuthorReviews?.[p.attemptId];
 need(s?.pending?.raw===p.raw&&s.pending.sha===p.sha&&local?.pending?.raw===p.raw&&local.pending.sha===p.sha,'原核对请求尚未可靠保全，未提交替代请求。');return s;
}
function accept(c,p,result){
 const s=exact(c,p);checkResult(c,result,p);if(p.direct_result)need(same(p.direct_result,result),'原编号回执与先前保存的直接响应不一致。');
 const before=reviewCopy(c.state);s.forms[p.body.review_mode].result=reviewCopy(result);s.pending=null;s.notFound=false;
 if(!c.persist()){c.state=before;throw Error('服务器核对结果已返回，但本机恢复记录尚未保存；原编号保留，请重新查询。');}
 c.notice=result.simulation_only?'这一版本的工程模拟检查已保存；不是人工业务审查，待核问题未据此解决，也未采用或执行。':'这一保存版本的作者自审记录已保存；后续修订需要重新核对，当前未采用或执行。';c.notify();return result;
}
async function resolve(c,p,send){
 let s=exact(c,p);const body=p.body;need(object(body)&&Object.keys(body).length===COMMAND_KEYS.length&&COMMAND_KEYS.every(key=>Object.hasOwn(body,key))&&body.schema==='business-flow-nested-review-command-v195'&&UUID.test(body.request_id||'')&&integer(body.expected_version)&&body.expected_version>=1&&UUID.test(body.save_request_id||'')&&HASH.test(body.value_hash||'')&&body.ack_scope===true&&body.ack_inferences===true&&typeof body.reason==='string'&&Array.from(body.reason.trim()).length>=2&&Array.from(body.reason).length<=12000&&modeValid(body.review_mode)&&HASH.test(p.sha||'')&&HASH.test(p.source_snapshot_hash||'')&&isNestedFlowPlan(p.saved_value),'原核对请求的协议、范围或完整内容无法核对。');safeReviewJSON(p);need(JSON.stringify(body)===p.raw&&await hash(c,p.raw)===p.sha,'原核对正文或摘要已变化。');exact(c,p);
 const reply=await c.http(REVIEW_BASE+'/nested-review-requests/'+p.body.request_id+'?expected_sha256='+p.sha);s=exact(c,p);
 need(reply?.schema==='business-flow-nested-review-original-result-v195'&&reply.request_id===p.body.request_id&&reply.body_sha256===p.sha&&typeof reply.found==='boolean','原编号查询回执无法核对。');
 if(reply.found)return accept(c,p,reply.result);need(reply.result===null);s.notFound=true;persist(c);
 if(!send){c.notice='尚未查到原核对回执，不表示请求未到达；原编号和完整内容保留，未重新提交。';c.notify();return reply;}
 editable(c);need(!p.rejection&&!p.direct_result,'已有直接回执或明确拒绝，不能重新发送。');persist(c);
 try{
  const result=await c.http(REVIEW_BASE+'/attempts/'+p.attemptId+'/qualify-nested',{method:'POST',body:p.raw});s=exact(c,p);checkResult(c,result,p);s.pending={...p,direct_result:reviewCopy(result)};persist(c);return resolve(c,reviewCopy(s.pending),false);
 }catch(error){s=exact(c,p);if([409,422].includes(error.status)&&rejected(error.detail)){s.pending={...s.pending,rejection:reviewCopy(error.detail)};persist(c);}throw error;}
}
export async function submitNestedAuthorReview(c){return c.operation(()=>c.locked(async()=>{
 saved(c);const id=c.state.selected,s=state(c),form=s.forms[s.mode],p=form.preview;
 need(p&&!s.pending&&p.review_mode===s.mode&&p.expected_version===c.form.version&&form.ack_scope===true&&form.ack_inferences===true&&Array.from(form.reason.trim()).length>=2,'请检查已保存版本、明确勾选两项并填写核对说明。');
 checkPreview(c,p,s.mode,id);need(s.mode==='engineering_simulation'||p.open_question_count===0,'仍有未解决的待核问题，请先修订并保存；不能记录作者自审。');
 const body={schema:'business-flow-nested-review-command-v195',request_id:c.crypto.randomUUID(),expected_version:p.expected_version,save_request_id:p.save_request_id,value_hash:p.value_hash,ack_scope:true,ack_inferences:true,reason:form.reason,review_mode:s.mode};
 const raw=JSON.stringify(body);need(new TextEncoder().encode(raw).length<=32768,'核对说明超过请求容量，未截断或提交。');const savedValue=reviewCopy(c.form.value),generation=c.form.generation,sha=await hash(c,raw);
 active(c,id);need(c.form.generation===generation&&!c.form.dirty&&c.form.version===p.expected_version&&s.mode===body.review_mode&&same(c.form.value,savedValue),'核对提交准备期间稿件或核对方式已变化；新输入保留，未发送。');
 const pending={attemptId:id,body,raw,sha,source_snapshot_hash:p.source_snapshot_hash,saved_value:savedValue,direct_result:null,rejection:null};s.pending=pending;s.notFound=false;persist(c);return resolve(c,reviewCopy(pending),true);
}));}
export async function recoverNestedAuthorReview(c,send=false){return c.operation(()=>{
 const work=async()=>{const p=nestedAuthorReviewState(c)?.pending;need(p,'没有待恢复的递归核对请求。');return resolve(c,reviewCopy(p),send);};return send?c.locked(work):work();
});}
export function archiveRejectedNestedAuthorReview(c){
 editable(c);const s=state(c);need(rejected(s.pending?.rejection),'尚未证明原请求未应用，原编号必须保留。');s.archives.push(reviewCopy(s.pending));s.pending=null;s.notFound=false;s.forms[s.mode].preview=null;s.forms[s.mode].ack_scope=false;s.forms[s.mode].ack_inferences=false;persist(c);c.notice='已保留被明确拒绝的原请求；请保存并重新检查当前版本。';c.notify();
}
export async function loadNestedAuthorReviews(c){return c.operation(async()=>{
 active(c);const id=c.state.selected,rows=await c.http(REVIEW_BASE+'/attempts/'+id+'/nested-reviews');active(c,id);need(Array.isArray(rows));rows.forEach(value=>checkRecord(c,value,{id}));state(c,id).history=reviewCopy(rows);persist(c);return rows;
});}
