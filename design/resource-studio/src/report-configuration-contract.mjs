import {copy,same,digest,uuid,reportScope,reportReviewValue} from './report-draft-contract.mjs';
export {copy,same,digest};
const hash=/^[0-9a-f]{64}$/;
const fail=()=>{throw Error('业务交接的范围、来源或回执无法核对，原人稿保留。');};
export function normalizeSourceProjectionRefs(refs){
 if(!Array.isArray(refs)||!refs.length||refs.length>50)fail();const sources=new Set(),copies=new Set();
 for(const ref of refs){const copy=ref?.copy_ref;if(!ref||Object.keys(ref).sort().join(',')!=='copy_ref,provenance_hash,source_material_id'||!uuid.test(ref.source_material_id)||!hash.test(ref.provenance_hash)||!copy||Object.keys(copy).sort().join(',')!=='hash,id,version'||!uuid.test(copy.id)||copy.id===ref.source_material_id||!Number.isSafeInteger(copy.version)||copy.version<1||!hash.test(copy.hash)||sources.has(ref.source_material_id)||copies.has(copy.id))fail();sources.add(ref.source_material_id);copies.add(copy.id);}
 return copy(refs);
}
export async function verifySourceProjectionOptions(value,account,handoff,candidateIndex){
 if(value?.schema!=='report-source-projection-options-v1'||value.workspace_id!==account.workspace_id||value.handoff_id!==handoff.id||value.candidate_index!==candidateIndex||!Array.isArray(value.options))fail();
 const {options_hash,...rest}=value;if(!hash.test(options_hash)||options_hash!==await digest(rest))fail();const copies=new Set();
 const validRef=ref=>ref&&Object.keys(ref).sort().join(',')==='hash,id,version'&&uuid.test(ref.id)&&Number.isSafeInteger(ref.version)&&ref.version>0&&hash.test(ref.hash);
 for(const option of value.options){const source=handoff.materials.find(m=>m.id===option.source_material_id);if(!source||!validRef(option.source_ref)||option.source_ref.id!==source.id||!validRef(option.copy_ref)||option.copy_ref.id===source.id||copies.has(option.copy_ref.id)||typeof option.source_name!=='string'||!option.source_name||typeof option.copy_name!=='string'||!option.copy_name||typeof option.ready!=='boolean'||option.provenance_hash!==null&&!hash.test(option.provenance_hash)||option.reason_code!==null&&typeof option.reason_code!=='string'||option.reason!==null&&typeof option.reason!=='string'||option.page_count!==null&&(!Number.isSafeInteger(option.page_count)||option.page_count<1)||!Number.isSafeInteger(option.confirmed_pages)||option.confirmed_pages<0||option.page_count!==null&&option.confirmed_pages>option.page_count)fail();
  if(option.ready&&(!hash.test(option.provenance_hash)||option.source_ref.hash!==source.hash||option.page_count===null||option.confirmed_pages!==option.page_count)||!option.ready&&(!option.reason||!option.reason_code))fail();copies.add(option.copy_ref.id);
 }
 return value;
}
export function selectedSourceProjectionRefs(options,selection){const refs=[];for(const [sourceID,copyID] of Object.entries(selection)){if(!copyID)continue;const option=options?.options.find(o=>o.source_material_id===sourceID&&o.copy_ref.id===copyID);if(!option?.ready)throw Error('所选PDF尚未完成核对或状态已变化，请保留流程并刷新副本状态。');refs.push({source_material_id:sourceID,copy_ref:copy(option.copy_ref),provenance_hash:option.provenance_hash});}return normalizeSourceProjectionRefs(refs);}
export function reportPathLabel(path){const step=/\/steps\/(\d+)(?:\/|$)/.exec(path||''),labels={name:'名称',trigger:'启动条件',purpose:'步骤目的',agent:'负责智能体',role:'负责角色',output:'输出',condition:'判断条件',next_key:'下一步',else_key:'条件不满足时的去向',citations:'原文出处',inference_reason:'推断说明'},field=(path||'').split('/').filter(Boolean).findLast(key=>Object.hasOwn(labels,key));return (step?'第'+(Number(step[1])+1)+'步':'业务整体')+' · '+(labels[field]||'相关内容');}
export function reportDifference(item,path){let keys=(path||'').split('/').filter(Boolean);if(keys[0]==='result')keys.shift();if(keys[0]==='businesses')keys=keys.slice(2);const get=value=>{for(const key of keys)value=value?.[key];if(value===null||value===undefined||value==='')return '未填写';return typeof value==='string'?value:typeof value==='boolean'?(value?'是':'否'):'相关内容已调整';};return {original:get(item.original_business),current:get(item.business)};}
export function reportBusinessChanges(item){const paths=[];for(const key of ['name','trigger','output'])if(!same(item.original_business?.[key],item.business?.[key]))paths.push('/'+key);const old=item.original_business?.steps||[],next=item.business?.steps||[];for(let i=0;i<Math.max(old.length,next.length);i++)for(const key of ['name','purpose','agent','role','output','condition','next_key','else_key','citations','inference_reason'])if(!same(old[i]?.[key],next[i]?.[key]))paths.push('/steps/'+i+'/'+key);return paths.map(path=>({label:reportPathLabel(path),...reportDifference(item,path)}));}
export function reportFlowFacts(name,flow){return {name,trigger:flow.trigger||'',output:flow.output||'',steps:(flow.steps||[]).map(step=>Object.fromEntries(['id','name','purpose','agent','role','output','condition','nextId','elseId'].map(key=>[key,step[key]||''])))};}
export async function configurationBody(draft,saved,indexes){
 const value=reportReviewValue(draft),entry=draft.review_history?.at(-1);
 if(draft.review_policy!=='private_partial_review_v1'||!draft.validation_report||!entry||entry.receipt.accepted_revision!==draft.revision||!same(entry.value,value)||!value?.result||!Number.isSafeInteger(saved?.revision)||saved.revision<1||!saved.configuration||!Array.isArray(indexes)||!indexes.length||new Set(indexes).size!==indexes.length||indexes.some(i=>!Number.isInteger(i)||i<0||!value.result.businesses[i]||value.result.businesses[i].operation!=='upsert'))throw Error('请先保存人工稿，再选择要带入的新增业务；原稿保留。');
 return {report_revision:draft.revision,review_receipt_hash:entry.receipt.receipt_hash,candidate_hash:await digest(value.result),source_hash:draft.preview.source_hash,validation_report_hash:draft.validation_report?.report_hash||await digest(null),configuration_revision:saved.revision,configuration_hash:await digest(saved.configuration),business_indexes:copy(indexes)};
}
export async function verifyConfigurationPreview(p,account,body,draft){
 if(p?.schema!=='report-configuration-preview-v1'||p.actor_id!==account.user_id||p.workspace_id!==account.workspace_id||p.report_id!==draft.id||!same(p.body,body)||p.review_value_hash!==await digest(reportReviewValue(draft))||!Array.isArray(p.selection)||p.selection.length!==body.business_indexes.length||!Array.isArray(p.conflicts))fail();
 const {preview_hash,...rest}=p;if(!hash.test(preview_hash)||preview_hash!==await digest(rest))fail();
 for(let i=0;i<p.selection.length;i++){const item=p.selection[i],index=body.business_indexes[i];if(item.candidate_index!==index||!same(item.business,reportReviewValue(draft).result.businesses[index])||item.control_flow_unverified!==true||!Array.isArray(item.source_refs)||!Array.isArray(item.modified_paths))fail();}
 return p;
}
export async function verifyHandoffRecord(record,account){
 if(record?.schema!=='report-configuration-handoff-v1'||record.workspace_id!==account.workspace_id||!uuid.test(record.id)||!uuid.test(record.report_id)||!Array.isArray(record.selection)||!Array.isArray(record.materials)||record.materials.some(m=>!uuid.test(m.id)||!Number.isSafeInteger(m.version)||m.version<1||!hash.test(m.hash)))fail();
 const {record_hash,...rest}=record;if(record_hash!==await digest(rest))fail();return record;
}
export async function verifySourceView(view,account,handoffID){
 await verifyHandoffRecord(view?.handoff,account);if(view.handoff.id!==handoffID||!hash.test(view.receipt_hash)||!Array.isArray(view.source_states))fail();
 for(const state of view.source_states)if(!Number.isInteger(state.candidate_index)||typeof state.verified!=='boolean'||!Array.isArray(state.issues)||state.verified&&(!hash.test(state.fact_hash)||!uuid.test(state.source_review_id)))fail();return view;
}
export async function commandHash(account,kind,target,body){return digest({kind,...reportScope(account),...(kind==='handoff'?{report_id:target}:{handoff_id:target}),body});}
export async function verifyConfigurationReply(reply,account,pending){
 const r=reply?.receipt,b=pending.body,view=reply?.configuration;
 if(r?.schema!=='report-configuration-receipt-v1'||r.kind!==pending.kind||r.actor_id!==account.user_id||r.workspace_id!==account.workspace_id||r.request_id!==b.request_id||r.command_hash!==pending.command_hash||r.command_hash!==await commandHash(account,pending.kind,pending.target,b)||!uuid.test(r.handoff_id)||!Number.isSafeInteger(r.accepted_revision)||r.accepted_revision!==b.configuration_revision+1||!hash.test(r.configuration_hash)||!Number.isSafeInteger(view?.revision)||!view.configuration||typeof reply.current_matches!=='boolean')fail();
 if(pending.kind==='handoff'&&(r.report_id!==pending.target||r.preview_hash!==b.preview_hash))fail();
 if(pending.kind==='source_review'&&(r.handoff_id!==pending.target||!uuid.test(r.source_review_id)))fail();
 const {receipt_hash,...rest}=r;if(receipt_hash!==await digest(rest))fail();
 const currentHash=await digest(view.configuration);if(reply.current_matches!==(view.revision===r.accepted_revision&&currentHash===r.configuration_hash))fail();
 if(pending.kind==='handoff'){
  await verifyHandoffRecord(reply.handoff,account);const h=reply.handoff;
  // The server adds only the converted flow to each originally previewed selection.
  const selection=h.selection.map(({flow,...previewed})=>previewed);
  if(h.id!==r.handoff_id||h.record_hash!==r.record_hash||h.actor_id!==account.user_id||h.report_id!==pending.target||h.configuration_id!==view.id||h.request_id!==r.request_id||h.command_hash!==r.command_hash||h.preview_hash!==b.preview_hash||h.source_hash!==b.source_hash||h.candidate_hash!==b.candidate_hash||h.accepted_revision!==r.accepted_revision||h.configuration_hash!==r.configuration_hash||!same(h.selection.map(item=>item.candidate_index),b.business_indexes)||pending.preview&&(!same(selection,pending.preview.selection)||h.raw_hash!==pending.preview.raw_hash||h.review_value_hash!==pending.preview.review_value_hash))fail();
  for(const item of h.selection){const flow=item.flow;if(!flow||typeof flow!=='object'||Array.isArray(flow)||!same(flow.reportSource,{handoff_id:h.id,candidate_index:item.candidate_index})||flow.confirmedAt!==null||flow.confirmedSignature!==null||reply.current_matches&&!same(flow,view.configuration.flows?.[item.business.name]))fail();}
 }
 else{const proof=reply.source_review;if(proof?.schema!=='report-configuration-source-review-v1'||proof.id!==r.source_review_id||proof.handoff_id!==pending.target||proof.actor_id!==account.user_id||proof.workspace_id!==account.workspace_id||proof.request_id!==b.request_id||proof.command_hash!==r.command_hash||proof.configuration_hash!==r.configuration_hash||proof.accepted_revision!==r.accepted_revision||proof.fact_hash!==b.fact_hash||proof.candidate_index!==b.candidate_index||!same(proof.material_refs,b.material_refs))fail();if(b.review_mode!==undefined){if(b.review_mode!=='confirmed_final_view_v1'||b.final_view_confirmed!==true||!same(proof.projection,{schema:'report-source-projection-v1',review_mode:b.review_mode,final_view_confirmed:true,projection_refs:normalizeSourceProjectionRefs(b.projection_refs)}))fail();}else if(proof.projection!==undefined)fail();const {record_hash,...rest}=proof;if(record_hash!==r.record_hash||record_hash!==await digest(rest))fail();}
 return reply;
}
