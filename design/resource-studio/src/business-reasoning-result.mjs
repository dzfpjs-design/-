/** Display-only V197 projection. No source validation, approval or execution. */
export const BUSINESS_REASONING_RESULT='business-reasoning-result-v197';
export const REVIEW_STATES=Object.freeze({needs_information:'资料不足，需补充信息',conflicting_evidence:'资料存在冲突，需核对',ready_for_human_review:'可供人工核对'});
export const ASSESSMENTS=Object.freeze({supported:'有资料支持',conflicting:'资料存在冲突',insufficient:'资料不足'});
export const MAX_TEXT_PREVIEW_BYTES=64*1024;
export const MAX_IMAGE_PREVIEW_BYTES=8*1024*1024;
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const keys=(value,expected)=>object(value)&&Object.keys(value).length===expected.length&&expected.every(key=>Object.hasOwn(value,key));
const text=(value,max)=>typeof value==='string'&&value.length>0&&value.length<=max&&/\S/.test(value);
const refs=value=>Array.isArray(value)&&value.length<=3&&value.every(id=>typeof id==='string'&&uuid.test(id))&&new Set(value).size===value.length;

export function isBusinessReasoningResult(value){
 if(!keys(value,['schema','analysis','review_state','recommended_candidate_ref','recommended_context','permissions_verified','can_dispatch'])||value.schema!==BUSINESS_REASONING_RESULT||value.permissions_verified!==false||value.can_dispatch!==false||!Object.hasOwn(REVIEW_STATES,value.review_state))return false;
 const a=value.analysis;if(!keys(a,['summary','candidate_assessments','recommendation'])||!text(a.summary,4000)||!Array.isArray(a.candidate_assessments)||a.candidate_assessments.length<1||a.candidate_assessments.length>16)return false;
 const seen=new Set();for(const row of a.candidate_assessments){if(!keys(row,['candidate_ref','assessment','reason','evidence_refs','missing_information'])||!text(row.candidate_ref,80)||seen.has(row.candidate_ref)||!Object.hasOwn(ASSESSMENTS,row.assessment)||!text(row.reason,4000)||!refs(row.evidence_refs)||!Array.isArray(row.missing_information)||row.missing_information.length>16||!row.missing_information.every(item=>text(item,2000))||row.assessment==='supported'&&!row.evidence_refs.length||row.assessment==='insufficient'&&!row.missing_information.length)return false;seen.add(row.candidate_ref);}
 const r=a.recommendation;if(!keys(r,['candidate_ref','reason','evidence_refs'])||!(r.candidate_ref===null||text(r.candidate_ref,80))||!text(r.reason,4000)||!refs(r.evidence_refs)||value.recommended_candidate_ref!==r.candidate_ref||!(value.recommended_context===null||object(value.recommended_context)))return false;
 if(r.candidate_ref!==null&&(!r.evidence_refs.length||!a.candidate_assessments.some(row=>row.candidate_ref===r.candidate_ref&&row.assessment==='supported')))return false;
 if(r.candidate_ref===null&&value.recommended_context!==null)return false;
 const state=a.candidate_assessments.some(row=>row.assessment==='insufficient'||row.missing_information.length)?'needs_information':a.candidate_assessments.some(row=>row.assessment==='conflicting')?'conflicting_evidence':'ready_for_human_review';
 return value.review_state===state;
}

export function businessReasoningResultView(value,inputs){
 if(!isBusinessReasoningResult(value))throw Error('业务分析结果暂不能完整核对，请重新读取任务记录。');
 const ids=refs(inputs?.evidence_ids)?inputs.evidence_ids:[],evidence=ids.map((id,index)=>({id,label:'资料'+(index+1),ordinal:index+1}));
 const actual=Array.isArray(inputs?.candidates)?inputs.candidates:[];
 const citations=references=>({items:evidence.filter(item=>references.includes(item.id)).map(item=>({id:item.id,label:item.label})),unavailable:references.some(id=>!ids.includes(id))});
 const rows=value.analysis.candidate_assessments.map((row,index)=>{const matches=actual.filter(item=>object(item)&&item.candidate_ref===row.candidate_ref);return {label:'方案'+(index+1),summary:matches.length===1&&text(matches[0].summary,2000)?matches[0].summary:null,assessment:ASSESSMENTS[row.assessment],reason:row.reason,missing:[...row.missing_information],citations:citations(row.evidence_refs)};});
 const recommended=value.recommended_candidate_ref===null?-1:value.analysis.candidate_assessments.findIndex(row=>row.candidate_ref===value.recommended_candidate_ref);
 return {question:text(inputs?.question,4000)?inputs.question:null,summary:value.analysis.summary,state:REVIEW_STATES[value.review_state],rows,evidence,recommendation:{label:recommended<0?'当前没有推荐方案':rows[recommended].label,reason:value.analysis.recommendation.reason,citations:citations(value.analysis.recommendation.evidence_refs)}};
}

export function businessEvidencePath(id){if(typeof id!=='string'||!uuid.test(id))throw Error('资料来源暂不能核对。');return '/evidence/'+encodeURIComponent(id)+'/content';}
const textTypes=new Set(['text/plain','text/csv','text/tab-separated-values','text/markdown','text/vtt','application/json','application/xml','text/xml']);
const imageTypes=new Set(['image/png','image/jpeg','image/webp','image/gif']);
export function evidenceExtension(type){return ({'text/plain':'.txt','text/csv':'.csv','application/json':'.json','application/pdf':'.pdf','image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif','audio/wav':'.wav','audio/mpeg':'.mp3'})[String(type||'').split(';')[0].trim().toLowerCase()]||'.bin';}

/** Bounded stream preview. HTML/SVG/PDF never becomes executable page content. */
export async function readBusinessEvidencePreview(response){
 const type=String(response.headers?.get('content-type')||'').split(';')[0].trim().toLowerCase();
 const kind=textTypes.has(type)?'text':imageTypes.has(type)?'image':null;
 const cancel=async()=>{try{await response.body?.cancel();}catch{}};
 if(!kind){await cancel();return {kind:'download',reason:'此资料为文件，请下载原件查看。'};}
 const maximum=kind==='text'?MAX_TEXT_PREVIEW_BYTES:MAX_IMAGE_PREVIEW_BYTES;
 const declared=response.headers?.get('content-length');
 if(declared&&/^\d+$/.test(declared)&&Number(declared)>maximum){await cancel();return {kind:'download',reason:'资料超过页面预览容量，请下载完整原件查看。'};}
 if(!response.body?.getReader){await cancel();return {kind:'download',reason:'当前环境不能安全预览此资料，请下载原件查看。'};}
 const reader=response.body.getReader(),chunks=[];let size=0;
 try{for(;;){const part=await reader.read();if(part.done)break;size+=part.value.byteLength;if(size>maximum){await reader.cancel();return {kind:'download',reason:'资料超过页面预览容量，请下载完整原件查看。'};}chunks.push(part.value);}}
 finally{reader.releaseLock();}
 const bytes=new Uint8Array(size);let offset=0;for(const part of chunks){bytes.set(part,offset);offset+=part.byteLength;}
 if(kind==='image')return {kind:'image',blob:new Blob([bytes],{type})};
 try{return {kind:'text',text:new TextDecoder('utf-8',{fatal:true}).decode(bytes)};}
 catch{return {kind:'download',reason:'此资料文字编码暂不支持页面预览，请下载原件查看。'};}
}
