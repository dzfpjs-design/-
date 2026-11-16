// Whole saved human review -> unconfirmed workspace design. No model calls or execution grants.
import {copy,same,digest,fullKeys,fullSha,fullUUID,fullInt,verifyEvidenceDraft} from './report-evidence-reading-contract.mjs';
import {evidenceManualValidationView} from './report-evidence-manual-v5.mjs';
import {OBJECT_DESIGN_V169,checkedObjectAdoptionDesign,deriveObjectAdoptionFlows} from './object-adoption-v169.mjs';
export {copy,same,digest};
const fail=()=>{throw Error('完整业务设计的范围、内容或回执无法核对；原人工稿和请求保留。');};
const need=value=>{if(!value)fail();};
const bodyKeys='schema,request_id,report_revision,review_receipt_hash,manual_value_hash,model_result_hash,source_hash,catalog_hash,context_hash,configuration_revision,configuration_hash,share_scope';
const designKeys='schema,handoff_id,report_id,report_revision,review_receipt_hash,manual_value_hash,model_result_hash,source_hash,catalog_hash,context_hash,result,business_bindings,execution_policy,design_hash';
export const fullSemanticScope=account=>({actor_id:account.user_id,workspace_id:account.workspace_id});
export const fullSemanticWritable=account=>['admin','operator'].includes(account.role);
export function checkFullSemanticCommand(body,{submit=false}={}){
 need(fullKeys(body,bodyKeys+(submit?',preview_hash,consent':''))&&body.schema==='report-full-semantic-handoff-command-v1'&&fullUUID.test(body.request_id)&&fullInt(body.report_revision,1)&&fullInt(body.configuration_revision,1)&&body.share_scope==='whole_result_excluding_private_note_v1');
 for(const key of ['review_receipt_hash','manual_value_hash','model_result_hash','source_hash','catalog_hash','context_hash','configuration_hash'])need(fullSha.test(body[key]));
 if(submit)need(fullSha.test(body.preview_hash)&&body.consent===true);return body;
}
export function fullSemanticSavedReady(session){
 const {row,state}=session||{},last=row?.review_history?.at(-1);
 return !!(session?.localSaved&&row?.review_value&&state&&!state.dirty&&!state.structure_form&&!state.pending&&state.draft_id===row.id&&state.revision===row.revision&&last?.receipt?.accepted_revision===row.revision&&same(state.value,row.review_value)&&same(last.value,row.review_value));
}
export async function buildFullSemanticCommand(account,row,value,configuration,requestID){
 await verifyEvidenceDraft(row,account);const entry=row.review_history.at(-1);
 need(value?.schema==='report-evidence-manual-review-v5'&&same(value,row.review_value)&&entry?.receipt.accepted_revision===row.revision&&same(entry.value,value)&&fullInt(configuration?.revision,1)&&configuration.configuration&&fullUUID.test(requestID));
 evidenceManualValidationView(value);
 return checkFullSemanticCommand({schema:'report-full-semantic-handoff-command-v1',request_id:requestID,report_revision:row.revision,review_receipt_hash:entry.receipt.receipt_hash,manual_value_hash:await digest(value),model_result_hash:row.model_result_hash,source_hash:row.preview.source_hash,catalog_hash:row.preview.map_preparation.catalog_descriptor.catalog_hash,context_hash:await digest(row.preview.map_preparation.context),configuration_revision:configuration.revision,configuration_hash:await digest(configuration.configuration),share_scope:'whole_result_excluding_private_note_v1'});
}
async function sealed(value,key){need(fullSha.test(value?.[key]));const {[key]:hash,...rest}=value;need(hash===await digest(rest));}
export async function verifyFullSemanticDesign(design,{body,result}={}){
 if(design?.schema===OBJECT_DESIGN_V169){if(body?.schema&&body.schema!=='report-object-adoption-command-v169')fail();return checkedObjectAdoptionDesign(design,{origin:body?.origin,result});}
 need(fullKeys(design,designKeys)&&design.schema==='report-full-semantic-design-v1'&&fullUUID.test(design.handoff_id)&&fullUUID.test(design.report_id)&&fullInt(design.report_revision,1)&&design.execution_policy==='requires_full_semantic_runtime_v1');
 for(const key of ['review_receipt_hash','manual_value_hash','model_result_hash','source_hash','catalog_hash','context_hash'])need(fullSha.test(design[key]));
 evidenceManualValidationView({schema:'report-evidence-manual-review-v5',result:design.result,note:''});
 need(design.result.source_hash===design.source_hash&&design.result.catalog_hash===design.catalog_hash);
 if(body)for(const key of ['report_revision','review_receipt_hash','manual_value_hash','model_result_hash','source_hash','catalog_hash','context_hash'])need(same(design[key],body[key]));
 if(result)need(same(design.result,result));
 need(Array.isArray(design.business_bindings)&&design.business_bindings.length===design.result.businesses.length);
 const ids=new Set(),names=new Set();
 for(const [i,b] of design.result.businesses.entries()){
  const mapping=design.business_bindings[i];need(fullKeys(mapping,'candidate_index,name,step_ids,participant_ids,interaction_ids,condition_ids,coordination_ids')&&mapping.candidate_index===i&&mapping.name===b.name&&b.name===b.name.trim()&&!names.has(b.name));names.add(b.name);
  for(const [field,collection] of [['step_ids','steps'],['participant_ids','participants'],['interaction_ids','interactions'],['condition_ids','conditions'],['coordination_ids','coordination']]){need(Array.isArray(mapping[field])&&mapping[field].length===b[collection].length);for(const id of mapping[field]){need(fullUUID.test(id)&&!ids.has(id));ids.add(id);}}
 }
 await sealed(design,'design_hash');return design;
}
export async function deriveFullSemanticFlows(design){
 if(design?.schema===OBJECT_DESIGN_V169)return deriveObjectAdoptionFlows(design);
 const flows={};for(const [i,b] of design.result.businesses.entries()){
  const ids=design.business_bindings[i].step_ids,target=index=>index===null?'end':ids[index];
  Object.defineProperty(flows,b.name,{enumerable:true,configurable:true,writable:true,value:{trigger:b.trigger,output:b.output,steps:b.steps.map((s,j)=>({id:ids[j],name:s.name,purpose:s.purpose,agent:s.agent,role:s.role,output:s.output,condition:s.branch_condition,nextId:target(s.next_index),elseId:target(s.else_index)})),confirmedAt:null,confirmedSignature:null,fullSemanticSource:{schema:'report-full-semantic-flow-ref-v1',handoff_id:design.handoff_id,design_hash:design.design_hash,candidate_index:i,business_hash:await digest(b)}}});
 }return flows;
}
export async function verifyFullSemanticConfiguration(configuration){
 if(!Object.hasOwn(configuration,'fullSemanticDesigns')){need(!Object.values(configuration.flows||{}).some(f=>Object.hasOwn(f,'fullSemanticSource')));return [];}
 need(Array.isArray(configuration.fullSemanticDesigns));const ids=new Set(),names=new Set();
 for(const design of configuration.fullSemanticDesigns){await verifyFullSemanticDesign(design);need(!ids.has(design.handoff_id));ids.add(design.handoff_id);for(const [name,expected] of Object.entries(await deriveFullSemanticFlows(design))){need(!names.has(name));names.add(name);const actual=configuration.flows?.[name];need(actual&&same(actual.fullSemanticSource,expected.fullSemanticSource)&&actual.trigger===expected.trigger&&actual.output===expected.output&&Array.isArray(actual.steps)&&actual.steps.length===expected.steps.length);for(const [i,s] of expected.steps.entries())need(Object.entries(s).every(([key,value])=>same(actual.steps[i][key],value)));need(configuration.businesses.some(n=>(typeof n==='string'?n:n.name)===name));}}
 for(const f of Object.values(configuration.flows||{}))if(Object.hasOwn(f,'fullSemanticSource'))need(configuration.fullSemanticDesigns.some(d=>d.handoff_id===f.fullSemanticSource?.handoff_id&&d.business_bindings.some(b=>configuration.flows[b.name]===f)));
 return configuration.fullSemanticDesigns;
}
function checkMaterials(materials){need(Array.isArray(materials)&&materials.length===2&&materials[0].id!==materials[1].id);for(const m of materials)need(fullKeys(m,'id,name,version,hash,evidence_id')&&fullUUID.test(m.id)&&typeof m.name==='string'&&!!m.name&&fullInt(m.version,1)&&fullSha.test(m.hash)&&fullUUID.test(m.evidence_id));}
export async function verifyFullSemanticPreview(p,account,{body,row,value}={}){
 need(fullKeys(p,'schema,actor_id,workspace_id,report_id,body,design,flows,materials,conflicts,source_status,can_confirm,can_publish,preview_hash')&&p.schema==='report-full-semantic-configuration-preview-v1'&&p.actor_id===account.user_id&&p.workspace_id===account.workspace_id&&fullUUID.test(p.report_id)&&p.source_status==='unconfirmed'&&p.can_confirm===false&&p.can_publish===false&&Array.isArray(p.conflicts)&&p.conflicts.every(x=>typeof x==='string'));
 checkFullSemanticCommand(p.body);if(body)need(same(p.body,body));if(row)need(p.report_id===row.id);
 await verifyFullSemanticDesign(p.design,{body:p.body,result:value?.result});need(p.design.report_id===p.report_id&&same(p.flows,await deriveFullSemanticFlows(p.design)));checkMaterials(p.materials);
 if(row){const s=row.preview.source_snapshot;need(same(p.materials,[s.original,s.copy].map(m=>({...m.ref,name:m.name,evidence_id:m.evidence_id}))));}
 await sealed(p,'preview_hash');return p;
}
export async function fullSemanticCommandHash(account,reportID,body){checkFullSemanticCommand(body,{submit:true});need(fullUUID.test(reportID));return digest({schema:'report-full-semantic-handoff-command-hash-v1',...fullSemanticScope(account),report_id:reportID,body});}
export async function verifyFullSemanticReply(reply,account,pending){
 need(fullKeys(reply,'schema,record,receipt,configuration,current_matches')&&reply.schema==='report-full-semantic-handoff-reply-v1'&&typeof reply.current_matches==='boolean');
 const {record:r,receipt:t,configuration:c}=reply,p=pending.preview,b=pending.body;
 await verifyFullSemanticPreview(p,account);checkFullSemanticCommand(b,{submit:true});const {preview_hash,consent,...base}=b;need(same(base,p.body)&&preview_hash===p.preview_hash&&pending.report_id===p.report_id);
 const commandHash=await fullSemanticCommandHash(account,pending.report_id,b);need(commandHash===pending.command_hash);
 need(fullKeys(r,'schema,id,actor_id,workspace_id,report_id,configuration_id,request_id,command_hash,preview_hash,design,materials,accepted_revision,configuration_hash,record_hash')&&r.schema==='report-full-semantic-handoff-v1'&&fullUUID.test(r.id)&&fullUUID.test(r.configuration_id)&&r.id===p.design.handoff_id&&r.actor_id===account.user_id&&r.workspace_id===account.workspace_id&&r.report_id===pending.report_id&&r.request_id===b.request_id&&r.command_hash===commandHash&&r.preview_hash===p.preview_hash&&r.accepted_revision===b.configuration_revision+1&&fullSha.test(r.configuration_hash)&&same(r.design,p.design)&&same(r.materials,p.materials));await sealed(r,'record_hash');
 need(fullKeys(t,'schema,actor_id,workspace_id,report_id,handoff_id,request_id,command_hash,preview_hash,design_hash,accepted_revision,configuration_hash,record_hash,receipt_hash')&&t.schema==='report-full-semantic-handoff-receipt-v1');
 for(const key of ['actor_id','workspace_id','report_id','request_id','command_hash','preview_hash','accepted_revision','configuration_hash','record_hash'])need(t[key]===r[key]);need(t.handoff_id===r.id&&t.design_hash===r.design.design_hash);await sealed(t,'receipt_hash');
 need(c?.id===r.configuration_id&&fullInt(c.revision,1)&&c.configuration&&typeof c.configuration==='object');const currentHash=await digest(c.configuration);need(reply.current_matches===(c.revision===t.accepted_revision&&currentHash===t.configuration_hash));
 if(reply.current_matches){need(Array.isArray(c.configuration.fullSemanticDesigns)&&c.configuration.fullSemanticDesigns.filter(d=>d.handoff_id===r.id).length===1&&same(c.configuration.fullSemanticDesigns.find(d=>d.handoff_id===r.id),r.design));for(const [name,flow] of Object.entries(p.flows))need(same(c.configuration.flows?.[name],flow)&&c.configuration.businesses.some(n=>(typeof n==='string'?n:n.name)===name));}
 return reply;
}
