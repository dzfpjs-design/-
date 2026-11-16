// Opt-in strict delivery is a separate protocol. Value hashes check DTO
// consistency; the immutable server journal alone proves original HTTP bytes.
import specs from './report-evidence-v4-specs.mjs';
import {copy,same,digest,fullKeys,fullInt,fullSha,fullUUID,fullText} from './report-full-reading-contract.mjs';
import {stableStringify} from './content-signature.js';
import {evidenceV3PythonJSON} from './report-evidence-v3-contract.mjs';
import {evidenceBinding,evidenceCommandHash,checkEvidenceClaim} from './report-evidence-reading-contract.mjs';

const need=(ok,message='本次读取的发送范围、交付版本或原回答绑定无法核对；原稿与原编号保留。')=>{if(!ok)throw Error(message);};
const size=value=>new TextEncoder().encode(stableStringify(value)).length;
const domain='report-evidence-platform-ids-v1';
export const evidenceV4=preview=>preview?.map_preparation?.generation_contract?.schema==='report-evidence-map-generation-v4';
export const evidenceV4ReduceBlocked='此读取方式当前只保存逐组事实与原文依据，尚不支持汇总业务草案；不会自动更换方式或新增调用。';
export function evidenceSentBytes(summary,preview){const key=evidenceV4(preview)?'prepared_request_bytes':'prepared_messages_bytes';need(fullInt(summary?.[key]));return summary[key];}
export function evidenceV4Policy(cap){
 const e=cap?.execution,o=e?.report_evidence_output;
 need(e?.provider==='deepseek'&&['deepseek-flash','deepseek-v4-pro'].includes(e.model)&&e.thinking_mode==='disabled'&&fullInt(e.max_tokens,128)&&e.max_tokens<=8192&&fullKeys(o,'schema,max_tokens,map_transport')&&o.schema==='report-evidence-output-v2'&&o.map_transport===specs.delivery&&fullInt(o.max_tokens,128)&&o.max_tokens<=131072);
 return {version:'cloud-evidence-2026-10-17/map-v4',stage:'map-v4',max_input_bytes:900000,transport_timeout_seconds:180,max_output_tokens:Math.min(o.max_tokens,8192),max_images:0,context_token_bound:1000000,full_report:true,long_context_verified:true};
}
export async function checkEvidenceV4Generation(spec,cap){
 const policy={single_batch:true,...evidenceV4Policy(cap)},options={...copy(specs.map.dispatch_options),model:cap.execution.model,max_tokens:policy.max_output_tokens};
 need(same(spec,{...specs.map,dispatch_options:options,input_policy:policy}));return spec;
}
export function evidenceV4Request(spec,payload){
 need(spec.schema==='report-evidence-map-generation-v4');
 const messages=[{role:'system',content:specs.strict_prefix+stableStringify(spec.output_schema)},
  {role:'system',content:evidenceV3PythonJSON({schema:'report-evidence-map-task-v4',task:spec.task,purpose:'candidate_only',source_policy:'untrusted_evidence',full_report_confirmed:false})},
  {role:'user',content:evidenceV3PythonJSON(payload)}];
 const request={...copy(spec.dispatch_options),messages},n=size(request),summary={prepared_request_bytes:n,token_bound:n+spec.dispatch_options.max_tokens+1024,max_input_bytes:spec.input_policy.max_input_bytes};
 need(n<=Math.min(spec.input_policy.max_input_bytes,spec.max_prepared_bytes)&&summary.token_bound<=spec.input_policy.context_token_bound);
 return {request,messages,input_summary:summary};
}
async function seal(value,key){need(fullSha.test(value?.[key]));const rest={...value};delete rest[key];need(value[key]===await digest(rest));}
export function evidenceV4Dispatch(preview,authorized){return {schema:'report-evidence-v4-dispatch-v1',model_id:preview.body.model_id,authorized,map_preparation_hash:preview.map_preparation.preparation_hash,map_plan_hash:preview.map_preparation.plan.plan_hash,request_hash:authorized.request_hash};}
export async function checkEvidenceV4Preparation(preview,catalog,cap){
 const m=preview.map_preparation,spec=m.generation_contract,plan=m.plan;
 await checkEvidenceV4Generation(spec,cap);
 need(fullKeys(plan,'schema,source_hash,catalog_hash,max_prepared_bytes,max_chunks,empty_sources,chunks,plan_hash')&&plan.schema==='report-evidence-read-plan-v2'&&plan.source_hash===catalog.descriptor.source_hash&&plan.catalog_hash===catalog.descriptor.catalog_hash&&plan.max_prepared_bytes===spec.max_prepared_bytes&&plan.max_chunks===spec.max_chunks&&same(plan.empty_sources,catalog.descriptor.empty_sources)&&Array.isArray(plan.chunks)&&plan.chunks.length>0&&plan.chunks.length<=24);await seal(plan,'plan_hash');
 const sources=catalog.descriptor.ordered_source_refs,indexKeys=Object.keys(catalog.index),ordered=[];
 need(Array.isArray(sources)&&new Set(sources).size===sources.length);
 for(const source of sources){const refs=indexKeys.filter(ref=>catalog.index[ref].source_ref===source);need(refs.every(ref=>fullInt(catalog.index[ref].start)));refs.sort((a,b)=>catalog.index[a].start-catalog.index[b].start);need(refs.every((ref,i)=>i===0||catalog.index[refs[i-1]].start<catalog.index[ref].start));ordered.push(...refs);}
 need(ordered.length===indexKeys.length&&new Set(ordered).size===indexKeys.length);
 const flattened=[];let bytes=0,tokens=0;
 for(const [i,c] of plan.chunks.entries()){
  need(fullKeys(c,'chunk_id,span_refs,source_refs,payload_hash,messages_hash,request_hash,input_summary,chunk_hash')&&c.chunk_id==='map-'+(i+1)&&Array.isArray(c.span_refs)&&c.span_refs.length>0&&c.span_refs.every(ref=>catalog.index[ref]));await seal(c,'chunk_hash');
  need(same(c.source_refs,[...new Set(c.span_refs.map(ref=>catalog.index[ref].source_ref))]));
  const payload={schema:'report-evidence-read-payload-v1',source_hash:catalog.descriptor.source_hash,catalog_hash:catalog.descriptor.catalog_hash,spans:c.span_refs.map(ref=>[ref,catalog.index[ref].text])},actual=evidenceV4Request(spec,{...payload,context:copy(m.context),user_instruction:m.user_instruction});
  need(c.payload_hash===await digest(payload)&&c.messages_hash===await digest(actual.messages)&&c.request_hash===await digest(actual.request)&&same(c.input_summary,actual.input_summary));flattened.push(...c.span_refs);bytes+=actual.input_summary.prepared_request_bytes;tokens+=actual.input_summary.token_bound;
 }
 need(same(flattened,ordered)&&same(m.input_summary,{map_call_count:plan.chunks.length,map_token_bound:tokens,prepared_request_bytes:bytes,maximum_total_calls:plan.chunks.length,full_report_confirmed:false})&&same(preview.input_summary,m.input_summary));return m;
}

export function evidenceV4ResponseReady(stage,preview){
 const r=stage?.receipt,d=stage?.journal;if(!r||!d?.consumable||!stage.raw_response_saved)return false;
 const meta=Object.fromEntries(specs.metadata_keys.map(k=>[k,r[k]]));
 return meta.delivery===specs.delivery&&meta.envelope_valid===true&&Array.isArray(meta.envelope_errors)&&meta.envelope_errors.length===0&&meta.http_status===200&&meta.finish_reason==='tool_calls'&&meta.response_choice_count===1&&meta.tool_call_count===1&&meta.tool_name===specs.tool_name&&meta.tool_type==='function'&&typeof meta.tool_call_id==='string'&&!!meta.tool_call_id.trim()&&fullSha.test(meta.tool_calls_hash)&&fullSha.test(meta.arguments_sha256)&&fullInt(meta.arguments_bytes,1)&&meta.arguments_bytes<=256*1024&&meta.arguments_encoding==='utf-8'&&meta.response_body_encoding==='utf-8-surrogateescape-v1'&&fullSha.test(meta.response_body_sha256)&&fullInt(meta.response_body_bytes,1)&&meta.response_body_bytes<=256*1024&&same(r.evidence_profile,evidenceV4Policy(preview.preparation_cap));
}

export function checkEvidenceV4Receipt(receipt,preview){
 need(fullKeys(receipt,[...specs.metadata_keys,'provider','model','usage','request_id','call_id','evidence_profile','original_usage_shape','usage_unavailable','usage_unavailable_reason'].join(','))&&receipt.delivery===specs.delivery&&same(receipt.evidence_profile,evidenceV4Policy(preview.preparation_cap))&&typeof receipt.envelope_valid==='boolean'&&Array.isArray(receipt.envelope_errors)&&receipt.envelope_errors.every(e=>typeof e==='string'&&/^[a-z][a-z0-9_]*$/.test(e))&&typeof receipt.usage_available==='boolean'&&typeof receipt.usage_present==='boolean'&&typeof receipt.usage_unavailable==='boolean'&&receipt.usage_available===!receipt.usage_unavailable&&['missing','null','object','array','string','boolean','number','unsupported'].includes(receipt.original_usage_shape));
 for(const k of ['response_body_sha256','tool_calls_hash','arguments_sha256'])need(receipt[k]===null||fullSha.test(receipt[k]));
 for(const k of ['response_body_bytes','response_choice_count','tool_call_count','arguments_bytes'])need(receipt[k]===null||fullInt(receipt[k]));
 need(Number.isInteger(receipt.http_status)&&receipt.response_body_encoding==='utf-8-surrogateescape-v1'&&(receipt.arguments_encoding===null||receipt.arguments_encoding==='utf-8'));
 for(const k of ['finish_reason','tool_call_id','tool_name','tool_type','usage_unavailable_reason'])need(receipt[k]===null||fullText(receipt[k],256*1024));
 if(receipt.usage_unavailable)need(same(receipt.usage,{})&&typeof receipt.usage_unavailable_reason==='string');
 else need(receipt.usage_unavailable_reason===null&&['prompt_tokens','completion_tokens','total_tokens'].every(k=>fullInt(receipt.usage[k]))&&receipt.usage.prompt_tokens+receipt.usage.completion_tokens===receipt.usage.total_tokens);
 return receipt;
}

export async function verifyEvidenceV4MapProjection(value,row,stage,catalog){
 // The wrapper shape below is deliberately closed. It is not a replacement
 // for loading/verifying the original immutable response on the server.
 need(fullKeys(value,'schema,delivery,evidence_profile,http_body_sha256,http_body_bytes,http_body_encoding,journal_wire_sha256,arguments_sha256,arguments_bytes,tool_metadata,tool_metadata_hash,arguments_projection,claims,projection_hash')&&value.schema==='report-evidence-map-tool-projection-v4'&&value.delivery===specs.delivery&&evidenceV4ResponseReady(stage,row.preview));
 await seal(value,'projection_hash');checkEvidenceV4Receipt(stage.receipt,row.preview);
 const meta=Object.fromEntries(specs.metadata_keys.map(k=>[k,stage.receipt[k]])),d=stage.journal,inner=value.arguments_projection,chunk=row.preview.map_preparation.plan.chunks.find(c=>c.chunk_id===stage.chunk_id);
 need(chunk&&same(value.tool_metadata,meta)&&value.tool_metadata_hash===await digest(meta)&&same(value.evidence_profile,stage.receipt.evidence_profile)&&value.http_body_sha256===meta.response_body_sha256&&value.http_body_bytes===meta.response_body_bytes&&value.http_body_encoding===meta.response_body_encoding&&value.journal_wire_sha256===d.wire_sha256&&value.http_body_sha256===d.wire_sha256&&value.http_body_bytes===d.wire_bytes&&value.arguments_sha256===meta.arguments_sha256&&value.arguments_bytes===meta.arguments_bytes&&same(value.claims,inner?.claims));
 need(fullKeys(inner,'schema,derivation_version,binding,binding_hash,wire_sha256,wire_value_hash,allowed_span_refs_hash,claims,projection_hash')&&inner.schema==='report-evidence-map-projection-v3'&&inner.derivation_version===domain&&same(inner.binding,d.binding)&&inner.binding_hash===d.binding_hash&&inner.binding_hash===await digest(inner.binding)&&inner.wire_sha256===value.arguments_sha256&&inner.allowed_span_refs_hash===await digest(chunk.span_refs)&&Array.isArray(inner.claims)&&inner.claims.length<=80);await seal(inner,'projection_hash');
 const binding=evidenceBinding(catalog,chunk.span_refs),wire={schema:'report-evidence-map-v3',claims:[]};
 for(const [i,c] of inner.claims.entries()){checkEvidenceClaim(c,binding);need(c.key==='mc_'+(await digest({domain,binding_hash:inner.binding_hash,wire_sha256:inner.wire_sha256,path:'/claims/'+i})).slice(0,32));const {key,...claim}=c;wire.claims.push(claim);}
 need(inner.wire_value_hash===await digest(wire)&&stage.result_hash===await digest(value)&&stage.result_count===value.claims.length);return value;
}

export async function verifyEvidenceV4Draft(row,account,helpers){
 need(fullKeys(row,'schema,id,actor_id,workspace_id,request_id,status,revision,phase,preview,maps,reduction,model_result,model_result_hash,review_value,review_history,recovery_history,resume_history,start_receipt,error,error_code,job_id,can_apply,can_publish,view_hash')&&row.schema==='report-evidence-reading-draft-v2'&&evidenceV4(row.preview)&&row.actor_id===account.user_id&&row.workspace_id===account.workspace_id&&fullUUID.test(row.id)&&/^fe_[a-f0-9-]{36}$/.test(row.request_id)&&fullInt(row.revision,1)&&['queued','running','awaiting_merge','response_unknown','failed','needs_clarification'].includes(row.status)&&['map','awaiting_reduce','failed','response_unknown','needs_clarification'].includes(row.phase)&&Array.isArray(row.maps)&&Array.isArray(row.recovery_history)&&Array.isArray(row.resume_history)&&(row.error===null||typeof row.error==='string')&&(row.error_code===null||typeof row.error_code==='string')&&(row.job_id===null||fullUUID.test(row.job_id))&&row.can_apply===false&&row.can_publish===false);await seal(row,'view_hash');
 // Common scope/status/stage/receipt validation stays in the existing
 // dispatcher; V4 cannot carry a fabricated Reduce or human adoption.
 need(row.model_result===null&&row.model_result_hash===null&&row.review_value===null&&Array.isArray(row.review_history)&&row.review_history.length===0&&row.reduction?.preview===null&&row.reduction.start_receipt===null&&row.reduction.projection_metadata===null&&row.reduction.reconciliation===null&&row.reduction.status==='not_started');
 const catalog=await helpers.verifyEvidencePreview(row.preview,account),m=row.preview.map_preparation;
 need(row.request_id===row.preview.body.request_id&&same(row.maps.map(s=>s.chunk_id),m.plan.chunks.map(c=>c.chunk_id)));
 for(const s of row.maps)await helpers.stageCheck(s,account,row,catalog);await helpers.stageCheck(row.reduction,account,row,catalog,false,true);
 need(['reserved_call_id','journal','receipt','result_hash','result_count'].every(k=>row.reduction[k]===null)&&row.reduction.raw_response_saved===false);
 const entry=async(kind,body,previewHash=row.preview.preview_hash,remaining=[])=>({schema:'report-evidence-reading-command-v2',kind,draft_id:kind==='extract'?null:row.id,body,command_hash:await evidenceCommandHash(account,kind,kind==='extract'?null:row.id,body),binding:{material_id:row.preview.body.selection.original_ref.id,source_hash:row.preview.source_hash,preview_hash:previewHash,map_inputs_hash:null,map_plan_hash:m.plan.plan_hash,remaining_chunks:remaining}});
 const body={...row.preview.body,preview_hash:row.preview.preview_hash,consent:true};await helpers.verifyEvidenceReceipt(row.start_receipt,account,await entry('extract',body));need(row.start_receipt.draft_id===row.id);
 for(const r of row.recovery_history){await helpers.verifyEvidenceReceipt(r,account,await entry('recovery',{revision:r.accepted_revision-1,request_id:r.request_id}));need(r.accepted_revision<=row.revision);}
 for(const item of row.resume_history){need(fullKeys(item,'preview,receipt'));const p=item.preview;await helpers.verifyEvidenceResumePreview(p,account,row);await helpers.verifyEvidenceReceipt(item.receipt,account,await entry('resume',{...p.body,preview_hash:p.preview_hash,consent:true},p.preview_hash,p.remaining_chunks.map(c=>c.chunk_id)));need(item.receipt.accepted_revision<=row.revision);}
 return row;
}
