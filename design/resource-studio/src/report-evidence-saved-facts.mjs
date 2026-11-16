// Read-only projection of an immutable answer. This never repairs a response,
// validates its meaning, adopts a result, or changes a paid command/state.
import {copy,same,digest,fullUUID,fullSha,fullKeys,fullInt,fullText,evidenceSpan,
  evidenceCatalog,verifyEvidenceDraft} from './report-evidence-reading-contract.mjs';
import {stableStringify} from './content-signature.js';
import {verifyEvidenceSavedFactsV2} from './report-evidence-saved-facts-v2.mjs';

const kinds=['职责','来源','治理','互动','业务','步骤','条件','协同','对象','关系','冲突','其他'];
const statuses=['explicit','inferred','unknown'],available=['available','missing','quarantined','not_json','unsupported_structure'];
const bounded=8*1024*1024;
const need=(condition,message='已保存事实的范围或摘要无法核对；未采用返回内容，原失败记录与请求保留。')=>{if(!condition)throw Error(message);};
const string=value=>typeof value==='string'&&fullText(value,bounded);
const nonempty=(value,max)=>typeof value==='string'&&fullText(value,max)&&!!value.trim();
function sourceStage(row,stage){const found=row?.maps?.find(s=>s.chunk_id===stage);need(/^map-([1-9]|1\d|2[0-4])$/.test(stage)&&found);return found;}
function identity(value,account){if(value?.user_id!==account.user_id||value?.workspace_id!==account.workspace_id||value?.role!==account.role)throw Object.assign(Error('当前账号或工作区已变化，未显示原私人回答。'),{code:'SESSION_SCOPE_CHANGED'});}
function issues(items){need(Array.isArray(items)&&items.length<=2000);for(const item of items)need(fullKeys(item,'path,code,message')&&string(item.path)&&/^[a-z][a-z0-9_]*$/.test(item.code)&&nonempty(item.message,4000));}

export function evidenceSavedFactsScope(account,row,stage){
  const s=sourceStage(row,stage);need(fullUUID.test(account?.user_id)&&fullUUID.test(account?.workspace_id)&&['admin','operator','viewer'].includes(account.role)&&fullUUID.test(row.id)&&fullInt(row.revision,1));
  return stableStringify({actor:account.user_id,workspace:account.workspace_id,role:account.role,draft:row.id,revision:row.revision,view_hash:row.view_hash,stage,source_hash:row.preview?.source_hash,reserved_call_id:s.reserved_call_id,descriptor_hash:s.journal?.descriptor_hash??null,wire_sha256:s.journal?.wire_sha256??null});
}

export async function verifyEvidenceSavedFacts(value,account,row,stage){
  if(value?.schema==='report-evidence-saved-facts-v2')return verifyEvidenceSavedFactsV2(value,account,row,stage);
  await verifyEvidenceDraft(row,account);const s=sourceStage(row,stage),preview=row.preview,catalog=await evidenceCatalog(preview),chunk=preview.map_preparation.plan.chunks.find(c=>c.chunk_id===stage);
  need(chunk&&fullKeys(value,'schema,actor_id,workspace_id,draft_id,draft_revision,stage,stage_status,generation_schema,source_hash,catalog_hash,messages_hash,journal,availability,message,original_protocol_valid,protocol_issues,facts,meaning_review_status,eligible_for_reduce,can_apply,can_publish,view_hash')&&value.schema==='report-evidence-saved-facts-v1');
  need(value.actor_id===account.user_id&&value.workspace_id===account.workspace_id&&value.draft_id===row.id&&value.draft_revision===row.revision&&value.stage===stage&&value.stage_status===s.status&&value.generation_schema===preview.map_preparation.generation_contract.schema&&value.source_hash===preview.source_hash&&value.catalog_hash===catalog.descriptor.catalog_hash&&value.messages_hash===chunk.messages_hash&&same(value.journal,s.journal));
  need(available.includes(value.availability)&&nonempty(value.message,4000)&&value.meaning_review_status==='unreviewed'&&value.eligible_for_reduce===false&&value.can_apply===false&&value.can_publish===false&&fullSha.test(value.view_hash));
  const {view_hash,...body}=value;need(view_hash===await digest(body));issues(value.protocol_issues);need(Array.isArray(value.facts)&&value.facts.length<=80);
  if(value.availability!=='available'){
    need(value.facts.length===0&&value.original_protocol_valid===(value.availability==='unsupported_structure'?false:null));
    if(value.availability==='missing')need(value.journal===null);else need(value.journal!==null&&value.journal.consumable===(value.availability!=='quarantined'));
    return value;
  }
  need(value.journal?.consumable===true&&typeof value.original_protocol_valid==='boolean'&&value.original_protocol_valid===(value.protocol_issues.length===0));
  const allowed=new Set(chunk.span_refs),displayIDs=new Set();
  for(let position=0;position<value.facts.length;position++){
    const fact=value.facts[position],fields=fact?.fields;
    need(fullKeys(fact,'position,display_id,protocol_key,fields,field_constraints_valid,issues,references,meaning_review_status')&&fact.position===position&&/^sf_[a-f0-9]{64}$/.test(fact.display_id)&&!displayIDs.has(fact.display_id)&&(fact.protocol_key===null||string(fact.protocol_key))&&fullKeys(fields,'kind,statement,status,explanation,span_refs')&&['kind','statement','status','explanation'].every(k=>fields[k]===null||string(fields[k]))&&(fields.span_refs===null||Array.isArray(fields.span_refs)&&fields.span_refs.every(string))&&typeof fact.field_constraints_valid==='boolean'&&fact.meaning_review_status==='unreviewed');
    need(fact.display_id==='sf_'+await digest({schema:'report-evidence-saved-fact-id-v1',binding_hash:await digest(value.journal.binding),wire_sha256:value.journal.wire_sha256,position}));displayIDs.add(fact.display_id);issues(fact.issues);
    need(Array.isArray(fact.references)&&fact.references.length===(fields.span_refs!==null&&fields.span_refs.length<=16?fields.span_refs.length:0));
    for(let i=0;i<fact.references.length;i++){
      const r=fact.references[i],ref=fields.span_refs[i],span=catalog.index[ref],expectedState=!evidenceSpan.test(ref)?'invalid':!allowed.has(ref)&&span?'outside_batch':!span?'missing':'resolved';
      need(fullKeys(r,'span_ref,state,source_ref,start,end,text,source_kind,source_number,revision_kind')&&r.span_ref===ref&&r.state===expectedState);
      if(expectedState!=='resolved')need(['source_ref','start','end','text','source_kind','source_number','revision_kind'].every(k=>r[k]===null));
      else {
        const ocr=span.source_ref.startsWith('o:'),number=Number(span.source_ref.slice(3)),page=ocr?preview.source_snapshot.pages[number-1]:null;
        need(r.source_ref===span.source_ref&&r.start===span.start&&r.end===span.end&&r.text===span.text&&r.source_kind===(ocr?'ocr_page':'word_block')&&r.source_number===number&&r.revision_kind===(ocr?page?.revision.kind:null));
      }
    }
    const refs=fields.span_refs,valid=kinds.includes(fields.kind)&&nonempty(fields.statement,2000)&&statuses.includes(fields.status)&&typeof fields.explanation==='string'&&fullText(fields.explanation,1000)&&(fields.status==='explicit'||!!fields.explanation.trim())&&Array.isArray(refs)&&refs.length<=16&&new Set(refs).size===refs.length&&(fields.status!=='explicit'||refs.length>0)&&fact.references.every(r=>r.state==='resolved');
    need(fact.field_constraints_valid===valid&&fact.field_constraints_valid===(fact.issues.length===0));
  }
  if(value.original_protocol_valid){if(['report-evidence-map-generation-v3','report-evidence-map-generation-v4'].includes(value.generation_schema))need(value.facts.every(f=>f.field_constraints_valid&&f.protocol_key===null));else need(value.facts.every(f=>f.field_constraints_valid&&/^[a-z][a-z0-9_]{0,39}$/.test(f.protocol_key))&&new Set(value.facts.map(f=>f.protocol_key)).size===value.facts.length);}
  return value;
}

export async function readEvidenceSavedFacts({account,row,stage,request,check=()=>{}}){
  const original=copy(row),scope=evidenceSavedFactsScope(account,original,stage);check();identity(await request('/me'),account);check();
  const value=await request('/setup/report-evidence-readings/'+original.id+'/saved-facts?stage='+stage);check();
  await verifyEvidenceSavedFacts(value,account,original,stage);check();need(scope===evidenceSavedFactsScope(account,row,stage));return value;
}
