import {REVIEW_BASE,reviewCopy,safeReviewJSON} from './business-flow-review-controller.mjs';
const need=(ok,message='核对记录与原请求无法核实，原输入仍保留。')=>{if(!ok)throw Error(message);};
const hash=/^[a-f0-9]{64}$/,uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const flags=v=>['semantic_quality_verified','can_apply','can_publish','can_execute'].every(k=>v?.[k]===false);
const provenRejection=v=>v?.not_applied===true&&(v.code==='business_flow_author_review_source_not_applied'||(v.code==='business_flow_author_review_version_conflict'&&Number.isSafeInteger(v.current_version)&&v.current_version>=1));
async function sha(c,raw){return Array.from(new Uint8Array(await c.crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw))),x=>x.toString(16).padStart(2,'0')).join('');}
export function authorReviewState(c,id=c.state.selected){return c.state.authorReviews?.[id]||null;}
function state(c,id=c.state.selected){need(uuid.test(id||''));c.state.authorReviews??={};return c.state.authorReviews[id]??={preview:null,reason:'',ack_scope:false,ack_inferences:false,pending:null,result:null,archives:[]};}
function persist(c){need(c.persist(),c.error);}
function editable(c){c.check();need(c.canEdit&&c.localSaved&&!c.pending,'请先保全本机稿并核对原保存结果。');}
function saved(c){editable(c);need(c.form&&!c.form.dirty&&c.detail?.saved&&c.form.version===c.detail.version,'请先保存当前修改并读取服务器这一版本，未核对未保存的内容。');}
export function editAuthorReview(c,patch){editable(c);const s=state(c);need(!s.pending,'原核对结果尚未确定，先查询原请求。');need(Object.keys(patch).every(k=>['reason','ack_scope','ack_inferences'].includes(k)));if('reason'in patch)need(typeof patch.reason==='string'&&Array.from(patch.reason).length<=12000);for(const key of ['ack_scope','ack_inferences'])if(key in patch)need(typeof patch[key]==='boolean');safeReviewJSON(patch);Object.assign(s,reviewCopy(patch));persist(c);}
export async function previewAuthorReview(c){return c.operation(()=>c.locked(async()=>{
 saved(c);const id=c.state.selected,s=state(c,id);need(!s.pending,'请先恢复原核对请求。');await c.loadDetail(id);saved(c);
 const v=await c.http(REVIEW_BASE+'/attempts/'+id+'/author-review-preview');c.check();need(c.state.selected===id&&v?.schema==='business-flow-author-review-preview-v1'&&v.attempt_id===id&&v.expected_version===c.detail.version&&uuid.test(v.save_request_id)&&hash.test(v.value_hash)&&hash.test(v.source_snapshot_hash)&&Array.isArray(v.flows)&&v.current_source_checked===true&&flags(v));safeReviewJSON(v);
 s.preview=reviewCopy(v);s.ack_scope=false;s.ack_inferences=false;s.result=null;persist(c);c.notice='当前保存版本的连线和原文已检查，请逐项核对内容后明确记录作者核对。';c.notify();return v;
}));}
function exact(c,p){c.check();const s=authorReviewState(c,p.attemptId),local=c.readOwn().authorReviews?.[p.attemptId];need(s?.pending?.raw===p.raw&&s.pending.sha===p.sha&&local?.pending?.raw===p.raw&&local.pending.sha===p.sha);return s;}
function checkResult(c,v,p){need(v?.schema==='author-review-result-v1'&&uuid.test(v.id)&&v.version===1&&v.status==='qualified'&&v.attempt_id===p.attemptId&&v.request_id===p.body.request_id&&v.human_review==='author_self_reviewed'&&v.reviewed_by===c.scope.actorId&&flags(v));safeReviewJSON(v);need(v.draft_version===p.body.expected_version&&v.value_hash===p.body.value_hash&&v.save_request_id===p.body.save_request_id&&v.reason===p.body.reason&&v.ack_scope===true&&v.ack_inferences===true&&hash.test(v.qualification_hash)&&hash.test(v.source_snapshot_hash)&&hash.test(v.review_record_hash));return v;}
function accept(c,p,v){const s=exact(c,p);checkResult(c,v,p);const before=reviewCopy(c.state);s.result=reviewCopy(v);s.pending=null;if(!c.persist()){c.state=before;throw Error('服务器已返回结果，但本机恢复未保存；原号仍保留，请重新查询。');}c.notice='这一保存版本的作者核对记录已保存；未采用到正式配置，也未启动执行。';c.notify();return v;}
async function resolve(c,p,send){const s=exact(c,p);need(JSON.stringify(p.body)===p.raw&&await sha(c,p.raw)===p.sha);c.check();
 const reply=await c.http(REVIEW_BASE+'/author-review-requests/'+p.body.request_id+'?expected_sha256='+p.sha);exact(c,p);
 need(reply?.schema==='author-review-original-result-v1'&&reply.request_id===p.body.request_id&&reply.body_sha256===p.sha&&typeof reply.found==='boolean');
 if(reply.found)return accept(c,p,reply.result);need(reply.result===null);
 if(!send){c.notice='尚未查到原核对回执，不代表未提交。原请求保留，未另起编号。';c.notify();return reply;}
 editable(c);need(!p.rejection,'原请求已明确拒绝，请先保留被拒请求并重新核对版本。');persist(c);
 try{const v=await c.http(REVIEW_BASE+'/attempts/'+p.attemptId+'/qualify',{method:'POST',body:p.raw});return accept(c,p,v);}
 catch(error){exact(c,p);if([409,422].includes(error.status)&&provenRejection(error.detail)){s.pending={...p,rejection:reviewCopy(error.detail)};persist(c);}throw error;}
}
export async function submitAuthorReview(c){return c.operation(()=>c.locked(async()=>{
 saved(c);const id=c.state.selected,s=state(c,id),p=s.preview;need(p&&!s.pending&&p.expected_version===c.form.version&&s.ack_scope===true&&s.ack_inferences===true&&Array.from(s.reason.trim()).length>=2,'请核对已保存版本、明确勾选两项并填写核对说明。');
 const body={schema:'business-flow-author-review-command-v1',request_id:c.crypto.randomUUID(),expected_version:p.expected_version,save_request_id:p.save_request_id,value_hash:p.value_hash,ack_scope:true,ack_inferences:true,reason:s.reason};
 const raw=JSON.stringify(body);need(new TextEncoder().encode(raw).length<=32768,'核对说明超过请求容量，未截断或提交。');const pending={attemptId:id,body,raw,sha:await sha(c,raw),rejection:null};c.check();s.pending=pending;persist(c);return resolve(c,reviewCopy(pending),true);
}));}
export async function recoverAuthorReview(c,send=false){return c.operation(()=>{const work=async()=>{const p=authorReviewState(c)?.pending;need(p,'没有待恢复的核对请求。');return resolve(c,reviewCopy(p),send);};return send?c.locked(work):work();});}
export async function loadAuthorReviews(c){return c.operation(async()=>{c.check();const id=c.state.selected;need(uuid.test(id||''));const rows=await c.http(REVIEW_BASE+'/attempts/'+id+'/author-reviews');need(Array.isArray(rows));for(const v of rows){need(v?.schema==='author-review-result-v1'&&v.attempt_id===id&&v.reviewed_by===c.scope.actorId&&v.human_review==='author_self_reviewed'&&flags(v));safeReviewJSON(v);}c.check();state(c,id).history=reviewCopy(rows);persist(c);return rows;});}
export function archiveRejectedAuthorReview(c){editable(c);const s=state(c);need(provenRejection(s.pending?.rejection),'未证明未提交，原号必须保留。');s.archives.push(reviewCopy(s.pending));s.pending=null;s.preview=null;s.ack_scope=false;s.ack_inferences=false;persist(c);c.notice='被明确拒绝的原请求已保留，请保存并读取新版本后重新核对。';c.notify();}
