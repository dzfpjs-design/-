// Explicit fixed-slot/typed-reduction protocol. DTO value hashes establish
// coherence only; original HTTP/argument bytes remain the server journal's job.
// Old V1–V4 wire, pending commands and validators are never reinterpreted here.
import specsV5 from './report-evidence-v5-specs.mjs';
import specsV6 from './report-evidence-v6-specs.mjs';
import specsV7 from './report-evidence-v7-specs.mjs';
import {copy,same,digest,fullKeys,fullInt,fullSha,fullText,fullUUID} from './report-full-reading-contract.mjs';
import {stableStringify} from './content-signature.js';
import {evidenceV3PythonJSON,checkEvidenceV3Manual} from './report-evidence-v3-contract.mjs';
import {evidenceManualFromProjectionV6} from './report-evidence-manual-v6-adapter.mjs';
import {evidenceManualFromProjection,evidenceManualValidationView} from './report-evidence-manual-v5.mjs';
import {evidenceBinding,evidenceCommandHash,checkEvidenceClaim,checkEvidenceResult,checkEvidenceReconciliation} from './report-evidence-reading-contract.mjs';

const need=(ok,message='本次读取的版本、完整发送范围或原回答证明无法核对；原稿与原编号保留。')=>{if(!ok)throw Error(message);};
const bytes=value=>new TextEncoder().encode(stableStringify(value)).length;
const claimFields='kind,statement,span_refs,status,explanation';
const slotName=position=>'slot_'+String(position+1).padStart(3,'0');
export const evidenceIdentityEncoding=value=>value===null||typeof value==='string'&&value.length<=256&&Array.from(value).every(c=>c.charCodeAt(0)>=32&&c.charCodeAt(0)<=126)&&['','identity'].includes(value.trim().toLowerCase());
const mapDomain='report-evidence-slot-ids-v1',reduceDomain='report-evidence-reduction-ids-v6';
// A private structural codec view, never returned as a model answer or saved
// human value. Model wire may be 1 MiB; the separate human save cap is 512 KiB.
function modelCodecView(result){need(result?.schema==='report-evidence-candidate-projection-v6');const view=copy(result);view.schema='report-evidence-candidate-projection-v5';return evidenceManualFromProjection(view);}
export const evidenceV5=preview=>preview?.map_preparation?.generation_contract?.schema==='report-evidence-map-generation-v5';
export const evidenceV6=preview=>preview?.map_preparation?.generation_contract?.schema==='report-evidence-map-generation-v6';
export const evidenceNative=preview=>preview?.map_preparation?.generation_contract?.schema==='report-evidence-map-generation-v7';
export const evidenceSlots=preview=>evidenceV5(preview)||evidenceV6(preview)||evidenceNative(preview);
const protocols=[
 {map:'report-evidence-map-generation-v5',reduce:'report-evidence-reduction-generation-v3',output:'report-evidence-output-v3',mapStage:'map-v5',reduceStage:'reduce-v3',mapTask:'report-evidence-map-task-v5',reduceTask:'report-evidence-reduction-task-v3',specs:specsV5},
 {map:'report-evidence-map-generation-v6',reduce:'report-evidence-reduction-generation-v4',output:'report-evidence-output-v4',mapStage:'map-v6',reduceStage:'reduce-v4',mapTask:'report-evidence-map-task-v6',reduceTask:'report-evidence-reduction-task-v4',specs:specsV6},
 {map:'report-evidence-map-generation-v7',reduce:'report-evidence-reduction-generation-v5',output:'report-evidence-output-v5',mapStage:'map-v7',reduceStage:'reduce-v5',mapTask:'report-evidence-map-task-v7',reduceTask:'report-evidence-reduction-task-v5',native:true,specs:specsV7},
];
function protocol(value){const schema=value?.map_preparation?.generation_contract?.schema??value?.schema,p=protocols.find(p=>p.map===schema||p.reduce===schema);need(p);return p;}
export function evidenceV5SentBytes(summary){need(fullInt(summary?.prepared_request_bytes));return summary.prepared_request_bytes;}
export function evidenceV5Policy(cap,reduce=false,value=null){
 const e=cap?.execution,o=e?.report_evidence_output,p=value===null?protocols.find(p=>p.output===o?.schema):protocol(value);need(p);const stage=reduce?p.reduceStage:p.mapStage;
 need((p.native?e?.provider==='qwen'&&e.model==='qwen3.8-flash'&&!Object.hasOwn(e,'thinking_mode'):e?.provider==='deepseek'&&['deepseek-flash','deepseek-v4-pro'].includes(e.model)&&e.thinking_mode==='disabled')&&fullInt(e.max_tokens,128)&&e.max_tokens<=8192&&fullKeys(o,'schema,max_tokens,map_transport,reduce_transport'+(p.native?',endpoint':''))&&o.schema===p.output&&o.map_transport===p.specs.delivery&&o.reduce_transport===p.specs.delivery&&(!p.native||o.endpoint==='https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/chat/completions')&&fullInt(o.max_tokens,128)&&o.max_tokens<=131072);
 return {version:'cloud-evidence-2026-10-17/'+stage,stage,max_input_bytes:900000,transport_timeout_seconds:reduce?600:180,max_output_tokens:reduce?o.max_tokens:Math.min(o.max_tokens,8192),max_images:0,context_token_bound:1000000,full_report:true,long_context_verified:true};
}
async function seal(value,key){need(fullSha.test(value?.[key]));const body={...value};delete body[key];need(value[key]===await digest(body));}
export async function checkEvidenceV5Generation(spec,cap,map=null,preparation=null){
 const p=protocol(spec),reduce=spec.schema===p.reduce,template=reduce?p.specs.reduce:p.specs.map;
 const policy={single_batch:true,...evidenceV5Policy(cap,reduce,spec)},options={...copy(template.dispatch_options),model:cap.execution.model,max_tokens:policy.max_output_tokens};
 let expected={...template,input_policy:policy,dispatch_options:options};
 if(reduce){need(map?.generation_contract?.schema===p.map&&preparation);expected={...expected,model_hash:await digest(cap),map_generation_hash:await digest(map.generation_contract),map_preparation_hash:map.preparation_hash,map_plan_hash:map.plan.plan_hash,map_inputs_hash:preparation.map_inputs_hash,options_hash:await digest(options),input_policy_hash:await digest(policy)};}
 need(same(spec,expected));return spec;
}
export function evidenceV5Request(spec,payload){
 const p=protocol(spec),reduce=spec.schema===p.reduce,stage=reduce?p.reduceStage:p.mapStage,messages=[{role:'system',content:p.specs.stage_prefix[stage]+(p.native?'':stableStringify(spec.output_schema))},
  {role:'system',content:evidenceV3PythonJSON({schema:reduce?p.reduceTask:p.mapTask,task:spec.task,purpose:'candidate_only',source_policy:'untrusted_evidence',full_report_confirmed:false})},
  {role:'user',content:evidenceV3PythonJSON(payload)}];
 const request={...copy(spec.dispatch_options),messages},n=bytes(request),input_summary={prepared_request_bytes:n,token_bound:n+spec.dispatch_options.max_tokens+1024,max_input_bytes:spec.input_policy.max_input_bytes};
 need(n<=spec.input_policy.max_input_bytes&&(reduce||n<=spec.max_prepared_bytes)&&input_summary.token_bound<=spec.input_policy.context_token_bound);
 return {request,messages,input_summary};
}
export function evidenceV5Dispatch(preview,authorized){return {schema:'report-evidence-v5-dispatch-v1',model_id:preview.body.model_id,authorized,map_preparation_hash:preview.map_preparation.preparation_hash,map_plan_hash:preview.map_preparation.plan.plan_hash,request_hash:authorized.request_hash};}
function orderedRefs(catalog){
 const keys=Object.keys(catalog.index),refs=[];need(Array.isArray(catalog.descriptor.ordered_source_refs)&&new Set(catalog.descriptor.ordered_source_refs).size===catalog.descriptor.ordered_source_refs.length);
 for(const source of catalog.descriptor.ordered_source_refs){const selected=keys.filter(ref=>catalog.index[ref].source_ref===source);need(selected.every(ref=>fullInt(catalog.index[ref].start)));selected.sort((a,b)=>catalog.index[a].start-catalog.index[b].start);need(selected.every((ref,i)=>!i||catalog.index[selected[i-1]].start<catalog.index[ref].start));refs.push(...selected);}
 need(refs.length===keys.length&&new Set(refs).size===keys.length);return refs;
}
export async function checkEvidenceV5Preparation(preview,catalog,cap){
 const m=preview.map_preparation,s=m.generation_contract,plan=m.plan;await checkEvidenceV5Generation(s,cap);
 need(fullKeys(plan,'schema,source_hash,catalog_hash,max_prepared_bytes,max_chunks,empty_sources,chunks,plan_hash')&&plan.schema==='report-evidence-read-plan-v2'&&plan.source_hash===catalog.descriptor.source_hash&&plan.catalog_hash===catalog.descriptor.catalog_hash&&plan.max_prepared_bytes===s.max_prepared_bytes&&plan.max_chunks===s.max_chunks&&same(plan.empty_sources,catalog.descriptor.empty_sources)&&Array.isArray(plan.chunks)&&plan.chunks.length>0&&plan.chunks.length<=24);await seal(plan,'plan_hash');
 const flattened=[];let n=0,tokens=0;
 for(const [i,c] of plan.chunks.entries()){
  need(fullKeys(c,'chunk_id,span_refs,source_refs,payload_hash,messages_hash,request_hash,input_summary,chunk_hash')&&c.chunk_id==='map-'+(i+1)&&Array.isArray(c.span_refs)&&c.span_refs.length>0&&c.span_refs.every(ref=>catalog.index[ref]));await seal(c,'chunk_hash');need(same(c.source_refs,[...new Set(c.span_refs.map(ref=>catalog.index[ref].source_ref))]));
  const payload={schema:'report-evidence-read-payload-v1',source_hash:catalog.descriptor.source_hash,catalog_hash:catalog.descriptor.catalog_hash,spans:c.span_refs.map(ref=>[ref,catalog.index[ref].text])},prepared=evidenceV5Request(s,{...payload,context:copy(m.context),user_instruction:m.user_instruction});
  need(c.payload_hash===await digest(payload)&&c.messages_hash===await digest(prepared.messages)&&c.request_hash===await digest(prepared.request)&&same(c.input_summary,prepared.input_summary));flattened.push(...c.span_refs);n+=prepared.input_summary.prepared_request_bytes;tokens+=prepared.input_summary.token_bound;
 }
 need(same(flattened,orderedRefs(catalog))&&same(m.input_summary,{map_call_count:plan.chunks.length,map_token_bound:tokens,prepared_request_bytes:n,maximum_total_calls:plan.chunks.length+1,full_report_confirmed:false})&&same(preview.input_summary,m.input_summary));return m;
}
export function checkEvidenceV5Receipt(receipt,preview,reduce=false){
 const family=protocol(preview),specs=family.specs;
 need(fullKeys(receipt,[...specs.metadata_keys,'provider','model','usage','request_id','call_id','evidence_profile','original_usage_shape','usage_unavailable','usage_unavailable_reason',...(family.native?['response_complete','http_content_encoding','capture_hash']:[])].join(','))&&receipt.delivery===specs.delivery&&same(receipt.evidence_profile,evidenceV5Policy(preview.preparation_cap,reduce,preview))&&typeof receipt.envelope_valid==='boolean'&&Array.isArray(receipt.envelope_errors)&&receipt.envelope_errors.every(e=>typeof e==='string'&&/^[a-z][a-z0-9_]*$/.test(e))&&typeof receipt.usage_available==='boolean'&&typeof receipt.usage_present==='boolean'&&typeof receipt.usage_unavailable==='boolean'&&receipt.usage_available===!receipt.usage_unavailable&&['missing','null','object','array','string','boolean','number','unsupported'].includes(receipt.original_usage_shape));
 if(family.native)need(typeof receipt.response_complete==='boolean'&&fullSha.test(receipt.capture_hash)&&(receipt.http_content_encoding===null||fullText(receipt.http_content_encoding,256)));
 for(const key of ['response_body_sha256','tool_calls_hash','arguments_sha256'])need(receipt[key]===null||fullSha.test(receipt[key]));
 for(const key of ['response_body_bytes','response_choice_count','tool_call_count','arguments_bytes'])need(receipt[key]===null||fullInt(receipt[key]));
 need(Number.isSafeInteger(receipt.http_status)&&fullSha.test(receipt.response_body_sha256)&&fullInt(receipt.response_body_bytes)&&receipt.response_body_encoding==='utf-8-surrogateescape-v1'&&(receipt.arguments_encoding===null||receipt.arguments_encoding==='utf-8'));
 for(const key of ['finish_reason','tool_call_id','tool_name','tool_type','usage_unavailable_reason'])need(receipt[key]===null||fullText(receipt[key],1024*1024));
 if(receipt.usage_unavailable)need(same(receipt.usage,{})&&typeof receipt.usage_unavailable_reason==='string');
 else need(receipt.usage_unavailable_reason===null&&['prompt_tokens','completion_tokens','total_tokens'].every(k=>fullInt(receipt.usage?.[k]))&&receipt.usage.prompt_tokens+receipt.usage.completion_tokens===receipt.usage.total_tokens);
 return receipt;
}
export function evidenceV5ResponseReady(stage,preview,reduce=false){
 const protocolValue=protocol(preview),specs=protocolValue.specs,r=stage?.receipt,d=stage?.journal,p=specs.stage_policy[reduce?protocolValue.reduceStage:protocolValue.mapStage];if(!r||!d?.consumable||!stage.raw_response_saved)return false;
 const complete=protocolValue.native?r.finish_reason==='stop'&&r.tool_call_count===0&&['tool_name','tool_type','tool_call_id','tool_calls_hash'].every(k=>r[k]===null)&&r.response_complete===true&&fullSha.test(r.capture_hash)&&evidenceIdentityEncoding(r.http_content_encoding):r.finish_reason==='tool_calls'&&r.tool_call_count===1&&r.tool_name===p.tool_name&&r.tool_type==='function'&&typeof r.tool_call_id==='string'&&!!r.tool_call_id.trim()&&fullSha.test(r.tool_calls_hash);
 return r.delivery===specs.delivery&&r.envelope_valid===true&&Array.isArray(r.envelope_errors)&&r.envelope_errors.length===0&&r.http_status===200&&complete&&r.response_choice_count===1&&fullSha.test(r.arguments_sha256)&&fullInt(r.arguments_bytes,1)&&r.arguments_bytes<=p.max_response_bytes&&r.arguments_encoding==='utf-8'&&r.response_body_encoding==='utf-8-surrogateescape-v1'&&fullSha.test(r.response_body_sha256)&&fullInt(r.response_body_bytes,1)&&r.response_body_bytes<=p.max_response_bytes&&r.arguments_bytes<=r.response_body_bytes&&r.response_body_sha256===d.wire_sha256&&r.response_body_bytes===d.wire_bytes&&same(r.evidence_profile,evidenceV5Policy(preview.preparation_cap,reduce,preview));
}
async function toolProjection(p,row,stage,reduce=false){
 const specs=protocol(row.preview).specs;
 need(evidenceV5ResponseReady(stage,row.preview,reduce));checkEvidenceV5Receipt(stage.receipt,row.preview,reduce);await seal(p,'projection_hash');const d=stage.journal,meta=Object.fromEntries(specs.metadata_keys.map(k=>[k,stage.receipt[k]]));
 await seal(d,'descriptor_hash');const b=d.binding;need(d.schema==='report-evidence-journal-descriptor-v'+(reduce?'2':'1')&&b.schema==='report-evidence-journal-binding-v'+(reduce?'2':'1')&&b.actor_id===row.actor_id&&b.workspace_id===row.workspace_id&&b.draft_id===row.id&&b.stage===(reduce?'reduce':stage.chunk_id)&&b.reserved_call_id===stage.reserved_call_id&&b.source_hash===row.preview.source_hash&&b.catalog_hash===row.preview.map_preparation.catalog_descriptor.catalog_hash&&stage.receipt.call_id===stage.reserved_call_id&&stage.receipt.provider===row.preview.provider&&stage.receipt.model===row.preview.model&&d.wire_kind==='text'&&d.encoding==='utf-8-surrogatepass-v1');
 need(same(p.binding,d.binding)&&p.binding_hash===d.binding_hash&&p.binding_hash===await digest(p.binding)&&p.journal_descriptor_hash===d.descriptor_hash&&p.journal_wire_sha256===d.wire_sha256&&p.http_body_sha256===meta.response_body_sha256&&p.http_body_bytes===meta.response_body_bytes&&p.http_body_encoding===meta.response_body_encoding&&p.arguments_sha256===meta.arguments_sha256&&p.arguments_bytes===meta.arguments_bytes&&same(p.tool_metadata,meta)&&p.tool_metadata_hash===await digest(meta)&&same(p.evidence_profile,stage.receipt.evidence_profile)&&p.evidence_profile_hash===await digest(p.evidence_profile));
}
export async function verifyEvidenceV5MapProjection(p,row,stage,catalog){
 const specs=protocol(row.preview).specs;
 need(fullKeys(p,'schema,delivery,wire_schema,codec,derivation_version,binding,binding_hash,journal_descriptor_hash,journal_wire_sha256,http_body_sha256,http_body_bytes,http_body_encoding,arguments_sha256,arguments_bytes,wire_value_hash,view_hash,codec_hash,slot_positions,tool_metadata,tool_metadata_hash,evidence_profile,evidence_profile_hash,allowed_span_refs_hash,claims,projection_hash')&&p.schema==='report-evidence-map-slots-projection-v5'&&p.delivery===specs.delivery&&p.wire_schema==='report-evidence-map-slots-v5'&&p.codec==='map-slots-to-v3-v1'&&p.derivation_version===mapDomain&&Array.isArray(p.claims)&&p.claims.length<=80&&Array.isArray(p.slot_positions)&&p.slot_positions.length===p.claims.length&&p.slot_positions.every((v,i)=>fullInt(v)&&v<80&&(!i||v>p.slot_positions[i-1])));
 await toolProjection(p,row,stage);const chunk=row.preview.map_preparation.plan.chunks.find(c=>c.chunk_id===stage.chunk_id);need(chunk&&p.allowed_span_refs_hash===await digest(chunk.span_refs));
 const binding=evidenceBinding(catalog,chunk.span_refs),wire={schema:p.wire_schema,slots:Object.fromEntries(Array.from({length:80},(_,i)=>[slotName(i),{state:'absent'}]))},view={schema:'report-evidence-map-v3',claims:[]};
 for(const [i,c] of p.claims.entries()){
  checkEvidenceClaim(c,binding);const {key,...claim}=c;need(fullKeys(claim,claimFields));const position=p.slot_positions[i],path='/slots/'+slotName(position)+'/claim';
  need(key==='mc_'+(await digest({domain:mapDomain,binding_hash:p.binding_hash,arguments_sha256:p.arguments_sha256,codec:p.codec,wire_schema:p.wire_schema,path})).slice(0,32));wire.slots[slotName(position)]={state:'present',claim:copy(claim)};view.claims.push(copy(claim));
 }
 const wireHash=await digest(wire),viewHash=await digest(view);need(p.wire_value_hash===wireHash&&p.view_hash===viewHash&&p.codec_hash===await digest({schema:p.codec,wire,arguments_sha256:p.arguments_sha256,wire_value_hash:wireHash,view,view_hash:viewHash,slot_positions:p.slot_positions})&&stage.result_hash===await digest(p)&&stage.result_count===p.claims.length);return p;
}
export async function evidenceV5Inputs(row,catalog){
 const m=row.preview.map_preparation,items={},claims=[],proofs=[],used=new Set(),calls=new Set();need(evidenceSlots(row.preview)&&row.maps.length===m.plan.chunks.length);
 for(const [i,stage] of row.maps.entries()){
  const c=m.plan.chunks[i];need(stage.chunk_id===c.chunk_id&&stage.status==='succeeded'&&stage.result);const p=await verifyEvidenceV5MapProjection(stage.result,row,stage,catalog);need(!calls.has(p.binding.reserved_call_id));calls.add(p.binding.reserved_call_id);
  proofs.push({chunk_id:c.chunk_id,chunk_hash:c.chunk_hash,map_preparation_hash:m.preparation_hash,map_plan_hash:m.plan.plan_hash,messages_hash:c.messages_hash,request_hash:c.request_hash,allowed_span_refs_hash:await digest(c.span_refs),binding_hash:p.binding_hash,journal_descriptor_hash:p.journal_descriptor_hash,journal_wire_sha256:p.journal_wire_sha256,http_body_sha256:p.http_body_sha256,http_body_bytes:p.http_body_bytes,http_body_encoding:p.http_body_encoding,arguments_sha256:p.arguments_sha256,arguments_bytes:p.arguments_bytes,tool_metadata_hash:p.tool_metadata_hash,evidence_profile_hash:p.evidence_profile_hash,wire_value_hash:p.wire_value_hash,view_hash:p.view_hash,codec_hash:p.codec_hash,slot_positions_hash:await digest(p.slot_positions),projection_hash:p.projection_hash});
  for(const [j,claim] of p.claims.entries()){items[c.chunk_id+'/claims/'+j]=copy(claim);claims.push(copy(claim));claim.span_refs.forEach(ref=>used.add(ref));}
 }
 const refs=orderedRefs(catalog).filter(ref=>used.has(ref));need(refs.length===used.size&&claims.length>0,'各资料组均未提取出事实，不能为空证据额外付费汇总。');return {items,claims,proofs,order:Object.keys(items),refs,spans:refs.map(ref=>[ref,catalog.index[ref].text])};
}
export async function verifyEvidenceV5ReductionPreparation(p,row,catalog,cap){
 const family=protocol(row.preview),specs=family.specs,m=row.preview.map_preparation,r=p.reduction_preparation,inputs=await evidenceV5Inputs(row,catalog);
 need(fullKeys(r,'schema,map_preparation_hash,map_plan_hash,map_proofs,map_inputs_hash,input_order,input_items_hash,input_claims_hash,generation_contract,generation_contract_hash,payload,payload_hash,messages_hash,request_hash,input_summary,output_capacity,call_count,requires_separate_authorization,can_apply,can_publish,preparation_hash')&&r.schema==='report-evidence-reduction-preparation-v3'&&r.call_count===1&&r.requires_separate_authorization===true&&r.can_apply===false&&r.can_publish===false);await seal(r,'preparation_hash');await checkEvidenceV5Generation(r.generation_contract,cap,m,r);
 const hashes={map_preparation_hash:m.preparation_hash,map_plan_hash:m.plan.plan_hash,map_inputs_hash:await digest(inputs.proofs),input_items_hash:await digest(inputs.items),input_claims_hash:await digest(inputs.claims)};
 need(Object.entries(hashes).every(([k,v])=>r[k]===v)&&same(r.map_proofs,inputs.proofs)&&same(r.input_order,inputs.order)&&r.generation_contract_hash===await digest(r.generation_contract));
 const payload={schema:'report-evidence-reduction-payload-v3',source_hash:catalog.descriptor.source_hash,catalog_hash:catalog.descriptor.catalog_hash,...hashes,input_order_hash:await digest(inputs.order),context:copy(m.context),user_instruction:m.user_instruction,input_claims:inputs.claims,spans:inputs.spans},actual=evidenceV5Request(r.generation_contract,payload);
 need(same(r.payload,payload)&&r.payload_hash===await digest(payload)&&r.messages_hash===await digest(actual.messages)&&r.request_hash===await digest(actual.request)&&same(r.input_summary,actual.input_summary)&&same(r.output_capacity,{schema:'report-evidence-output-capacity-v2',max_output_tokens:r.generation_contract.dispatch_options.max_tokens,max_wire_value_bytes:1024*1024,max_response_body_bytes:1024*1024,completion_required:family.native?'stop':'tool_calls',tool_name:family.native?null:specs.stage_policy[family.reduceStage].tool_name,input_claim_count:inputs.claims.length,required_reconciliation_entries:inputs.claims.length,token_estimate:null,fit_guaranteed:false,overflow_behavior:'preserve_response_and_fail'}));return p;
}
async function candidateIDs(result,contextHash){
 const id=async(prefix,path)=>prefix+(await digest({domain:reduceDomain,derivation_context_hash:contextHash,path})).slice(0,32);
 for(const [i,c] of result.global_claims.entries())need(c.key===await id('gc_','/result/global_claims/'+i));
 for(const [bi,b] of result.businesses.entries()){const base='/result/businesses/'+bi;for(const [collection,prefix] of [['steps','st_'],['participants','pa_'],['interactions','in_'],['conditions','co_'],['coordination','cd_']])for(const [i,item] of b[collection].entries())need(item.key===await id(prefix,base+'/'+collection+'/'+i));}
}
export async function verifyEvidenceV5ReduceProjection(row,catalog){
 const specs=protocol(row.preview).specs,stage=row.reduction,outer={...stage.projection_metadata,result:row.model_result,reconciliation:stage.reconciliation};
 need(fullKeys(outer,'schema,delivery,binding,binding_hash,journal_descriptor_hash,journal_wire_sha256,http_body_sha256,http_body_bytes,http_body_encoding,arguments_sha256,arguments_bytes,tool_metadata,tool_metadata_hash,evidence_profile,evidence_profile_hash,reduction_preparation_hash,map_preparation_hash,map_plan_hash,map_inputs_hash,arguments_projection_hash,reduction_projection_metadata,result,reconciliation,projection_hash')&&outer.schema==='report-evidence-reduction-tool-projection-v3'&&outer.delivery===specs.delivery);await toolProjection(outer,row,stage,true);
 const r=stage.preview.reduction_preparation,m=row.preview.map_preparation,inputs=await evidenceV5Inputs(row,catalog),meta=outer.reduction_projection_metadata;
 need(fullKeys(meta,'schema,codec,id_policy,wire_bytes_sha256,wire_bytes,wire_value_hash,codec_value_hash,source_hash,catalog_hash,input_order,input_items_hash,allowed_span_refs_hash,context_hash,derivation_context_hash,projection_hash')&&meta.schema==='report-evidence-reduction-projection-v6'&&meta.codec==='report-evidence-reduction-codec-v6'&&meta.id_policy===reduceDomain&&meta.wire_bytes_sha256===outer.arguments_sha256&&meta.wire_bytes===outer.arguments_bytes&&meta.source_hash===catalog.descriptor.source_hash&&meta.catalog_hash===catalog.descriptor.catalog_hash&&same(meta.input_order,inputs.order)&&meta.input_items_hash===await digest(inputs.items)&&meta.allowed_span_refs_hash===await digest(inputs.refs)&&meta.context_hash===await digest(m.context));
 const {derivation_context_hash,projection_hash,...context}=meta;need(derivation_context_hash===await digest(context));const reduced={...meta,result:row.model_result,reconciliation:stage.reconciliation};await seal(reduced,'projection_hash');
 need(outer.arguments_projection_hash===projection_hash&&outer.reduction_preparation_hash===r.preparation_hash&&outer.map_preparation_hash===m.preparation_hash&&outer.map_plan_hash===m.plan.plan_hash&&outer.map_inputs_hash===r.map_inputs_hash);
 const manual=modelCodecView(row.model_result),view=evidenceManualValidationView(manual);checkEvidenceResult(view.result,evidenceBinding(catalog,inputs.refs));await candidateIDs(row.model_result,derivation_context_hash);checkEvidenceReconciliation(row.model_result,stage.reconciliation,inputs.items);
 const codec={result:copy(manual.result),reconciliation:stage.reconciliation.map(entry=>{const {input_id,...rest}=entry,input_position=inputs.order.indexOf(input_id);need(input_position>=0);return {input_position,...rest};})},wire=copy(codec);wire.result.schema='report-evidence-candidate-wire-v6';
 for(const b of wire.result.businesses)for(const s of b.steps)for(const key of ['next_index','else_index'])s[key]=s[key]===null?{kind:'end'}:{kind:'step',index:s[key]};
 for(const o of wire.result.objects)for(const f of o.fields)f.unit=f.unit===null?{kind:'unspecified'}:{kind:'specified',value:f.unit};
 need(meta.codec_value_hash===await digest(codec)&&meta.wire_value_hash===await digest(wire)&&row.model_result_hash===await digest(row.model_result)&&stage.result_hash===await digest(outer)&&stage.result_count===['global_claims','objects','relationships','businesses','questions'].reduce((n,key)=>n+row.model_result[key].length,0));return outer;
}
export function evidenceV5ManualAvailability(row){if(row.review_value!==null)return {allowed:true,reason:''};if(!row.model_result)return {allowed:false,reason:''};return bytes(modelCodecView(row.model_result))<=512*1024?{allowed:true,reason:''}:{allowed:false,reason:'完整原汇总候选超过当前512 KiB人工稿保存上限，仍可在下方逐项只读核对；未截断、采用或生成一份缩短人工稿。'};}
export function evidenceV5ReviewValue(row){if(row.review_value!==null)return copy(row.review_value);return row.model_result&&evidenceV5ManualAvailability(row).allowed?evidenceManualFromProjectionV6(row.model_result):null;}
export async function verifyEvidenceV5Draft(row,account,helpers){
 need(fullKeys(row,'schema,id,actor_id,workspace_id,request_id,status,revision,phase,preview,maps,reduction,model_result,model_result_hash,review_value,review_history,recovery_history,resume_history,start_receipt,error,error_code,job_id,can_apply,can_publish,view_hash')&&row.schema==='report-evidence-reading-draft-v3'&&evidenceSlots(row.preview)&&row.actor_id===account.user_id&&row.workspace_id===account.workspace_id&&fullUUID.test(row.id)&&/^fe_[a-f0-9-]{36}$/.test(row.request_id)&&fullInt(row.revision,1)&&['queued','running','awaiting_merge','response_unknown','failed','review','needs_clarification'].includes(row.status)&&['map','awaiting_reduce','reduce','review','failed','response_unknown','needs_clarification'].includes(row.phase)&&Array.isArray(row.maps)&&Array.isArray(row.review_history)&&Array.isArray(row.recovery_history)&&Array.isArray(row.resume_history)&&(row.error===null||typeof row.error==='string')&&(row.error_code===null||typeof row.error_code==='string')&&(row.job_id===null||fullUUID.test(row.job_id))&&row.can_apply===false&&row.can_publish===false);await seal(row,'view_hash');
 const catalog=await helpers.verifyEvidencePreview(row.preview,account),m=row.preview.map_preparation;need(row.request_id===row.preview.body.request_id&&same(row.maps.map(s=>s.chunk_id),m.plan.chunks.map(c=>c.chunk_id)));for(const s of row.maps)await helpers.stageCheck(s,account,row,catalog);await helpers.stageCheck(row.reduction,account,row,catalog,false,true);
 const entry=async(kind,body,previewHash=row.preview.preview_hash,inputs=null,remaining=[])=>({schema:'report-evidence-reading-command-v3',kind,draft_id:kind==='extract'?null:row.id,body,command_hash:await evidenceCommandHash(account,kind,kind==='extract'?null:row.id,body),binding:{material_id:row.preview.body.selection.original_ref.id,source_hash:row.preview.source_hash,preview_hash:previewHash,map_inputs_hash:inputs,map_plan_hash:m.plan.plan_hash,remaining_chunks:remaining}});
 const start={...row.preview.body,preview_hash:row.preview.preview_hash,consent:true};await helpers.verifyEvidenceReceipt(row.start_receipt,account,await entry('extract',start));need(row.start_receipt.draft_id===row.id);
 if(row.reduction.preview){const p=row.reduction.preview;await helpers.verifyEvidenceReductionPreview(p,account,row);await helpers.verifyEvidenceReceipt(row.reduction.start_receipt,account,await entry('reduce',{...p.body,preview_hash:p.preview_hash,consent:true},p.preview_hash,p.reduction_preparation.map_inputs_hash));}
 else need(row.reduction.start_receipt===null);
 if(row.model_result===null)need(row.model_result_hash===null&&row.reduction.projection_metadata===null&&row.reduction.reconciliation===null);else {need(row.reduction.status==='succeeded');await verifyEvidenceV5ReduceProjection(row,catalog);}
 const humanBinding=evidenceBinding(catalog);if(row.review_value!==null)checkEvidenceV3Manual(row.review_value,humanBinding);let prior=0;
 for(const item of row.review_history){need(fullKeys(item,'receipt,value'));checkEvidenceV3Manual(item.value,humanBinding);const body={revision:item.receipt.accepted_revision-1,request_id:item.receipt.request_id,value:item.value};await helpers.verifyEvidenceReceipt(item.receipt,account,await entry('review',body));need(item.receipt.accepted_revision>prior&&item.receipt.accepted_revision<=row.revision);prior=item.receipt.accepted_revision;}
 for(const receipt of row.recovery_history){await helpers.verifyEvidenceReceipt(receipt,account,await entry('recovery',{revision:receipt.accepted_revision-1,request_id:receipt.request_id}));need(receipt.accepted_revision<=row.revision&&(receipt.schema==='report-evidence-recovery-receipt-v2')===evidenceNative(row.preview));if(evidenceNative(row.preview)){const ids=row.maps.map(s=>s.chunk_id),names=receipt.recovered_stages.map(s=>s.stage);need(same(receipt.remaining_chunks,ids.filter(id=>receipt.remaining_chunks.includes(id)))&&(receipt.completion_scope==='reduction'||same(names,ids.filter(id=>names.includes(id)))));for(const item of receipt.recovered_stages){const stage=item.stage==='reduce'?row.reduction:row.maps.find(s=>s.chunk_id===item.stage);need(stage?.status==='succeeded'&&stage.reserved_call_id===item.call_id&&stage.journal?.descriptor_hash===item.journal_descriptor_hash&&stage.result_hash===item.result_hash&&stage.receipt?.capture_hash===item.capture_hash);}}}
 for(const item of row.resume_history){need(fullKeys(item,'preview,receipt'));const p=item.preview;await helpers.verifyEvidenceResumePreview(p,account,row);await helpers.verifyEvidenceReceipt(item.receipt,account,await entry('resume',{...p.body,preview_hash:p.preview_hash,consent:true},p.preview_hash,null,p.remaining_chunks.map(c=>c.chunk_id)));need(item.receipt.accepted_revision<=row.revision);}
 need(same(row.review_value,row.review_history.at(-1)?.value??null));return row;
}
