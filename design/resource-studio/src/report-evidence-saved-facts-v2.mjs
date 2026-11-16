// Display-only V5 slots. These checks bind a server view to its frozen draft;
// public value hashes do not prove provider bytes, meaning or adoption rights.
import {same,digest,fullKeys,fullInt,fullSha,fullText,evidenceSpan,
  evidenceCatalog,verifyEvidenceDraft} from './report-evidence-reading-contract.mjs';
import {stableStringify} from './content-signature.js';
import {evidenceIdentityEncoding} from './report-evidence-v5-contract.mjs';

const deliveries={'report-evidence-map-generation-v5':'deepseek-strict-report-tool-v2','report-evidence-map-generation-v6':'deepseek-strict-report-tool-v3','report-evidence-map-generation-v7':'qwen-tokenplan-native-json-schema-v1'},wireSchema='report-evidence-map-slots-v5';
const kinds=['职责','来源','治理','互动','业务','步骤','条件','协同','对象','关系','冲突','其他'];
const statuses=['explicit','inferred','unknown'];
const states=['available','missing','quarantined','not_json','unsupported_structure'];
const metadataKeys='delivery,response_body_sha256,response_body_bytes,response_body_encoding,http_status,finish_reason,response_choice_count,tool_call_count,tool_call_id,tool_name,tool_type,tool_calls_hash,arguments_sha256,arguments_bytes,arguments_encoding,envelope_valid,envelope_errors,usage_available,usage_present'.split(',');
const bounded=8*1024*1024,maximumWire=256*1024;
const need=(ok)=>{if(!ok)throw Error('已保存槽位的范围、原回答或引用无法核对；原稿与原编号保留，未采用返回内容。');};
const string=value=>typeof value==='string'&&fullText(value,bounded);
const nonempty=(value,max)=>typeof value==='string'&&fullText(value,max)&&!!value.trim();
const slotName=position=>'slot_'+String(position+1).padStart(3,'0');

function checkIssues(items){
  need(Array.isArray(items)&&items.length<=2000);
  for(const item of items)need(fullKeys(item,'path,code,message')&&string(item.path)
    &&typeof item.code==='string'&&/^[a-z][a-z0-9_]*$/.test(item.code)&&nonempty(item.message,4000));
}

function checkMetadata(metadata,stage,delivery){
  need(fullKeys(metadata,metadataKeys.join(','))&&stage.receipt
    &&same(metadata,Object.fromEntries(metadataKeys.map(key=>[key,stage.receipt[key]])))
    &&metadata.delivery===delivery
    &&metadata.response_body_sha256===stage.journal?.wire_sha256
    &&metadata.response_body_bytes===stage.journal?.wire_bytes
    &&fullInt(metadata.response_body_bytes)&&metadata.response_body_bytes<=maximumWire
    &&metadata.response_body_encoding==='utf-8-surrogateescape-v1'
    &&Number.isSafeInteger(metadata.http_status)
    &&typeof metadata.envelope_valid==='boolean'
    &&Array.isArray(metadata.envelope_errors)&&metadata.envelope_errors.every(code=>typeof code==='string'&&/^[a-z][a-z0-9_]*$/.test(code))
    &&typeof metadata.usage_available==='boolean'&&typeof metadata.usage_present==='boolean'
    &&(metadata.arguments_encoding===null||metadata.arguments_encoding==='utf-8'));
  for(const key of ['response_body_sha256','tool_calls_hash','arguments_sha256'])need(metadata[key]===null||fullSha.test(metadata[key]));
  for(const key of ['response_body_bytes','response_choice_count','tool_call_count','arguments_bytes'])need(metadata[key]===null||fullInt(metadata[key]));
  if(metadata.arguments_bytes!==null)need(metadata.arguments_bytes<=metadata.response_body_bytes);
  for(const key of ['finish_reason','tool_call_id','tool_name','tool_type'])need(metadata[key]===null||string(metadata[key]));
}

function complete(metadata,stage,generation){
  if(generation==='report-evidence-map-generation-v7')return metadata.http_status===200&&metadata.finish_reason==='stop'&&metadata.response_choice_count===1&&metadata.tool_call_count===0&&['tool_call_id','tool_name','tool_type','tool_calls_hash'].every(k=>metadata[k]===null)&&metadata.envelope_valid===true&&metadata.envelope_errors.length===0&&fullSha.test(metadata.arguments_sha256)&&fullInt(metadata.arguments_bytes,1)&&metadata.arguments_bytes<=maximumWire&&metadata.arguments_encoding==='utf-8'&&stage.receipt.response_complete===true&&fullSha.test(stage.receipt.capture_hash)&&evidenceIdentityEncoding(stage.receipt.http_content_encoding);
  return metadata.http_status===200&&metadata.finish_reason==='tool_calls'
    &&metadata.response_choice_count===1&&metadata.tool_call_count===1
    &&metadata.tool_name==='submit_report_evidence_map_slots'&&metadata.tool_type==='function'
    &&nonempty(metadata.tool_call_id,maximumWire)&&fullSha.test(metadata.tool_calls_hash)
    &&metadata.envelope_valid===true&&metadata.envelope_errors.length===0
    &&fullSha.test(metadata.arguments_sha256)&&fullInt(metadata.arguments_bytes,1)
    &&metadata.arguments_bytes<=maximumWire&&metadata.arguments_encoding==='utf-8';
}

function checkReferences(fact,catalog,allowed,preview){
  const refs=fact.fields.span_refs;
  need(Array.isArray(fact.references)&&fact.references.length===(refs!==null&&refs.length<=16?refs.length:0));
  for(const [i,reference] of fact.references.entries()){
    const ref=refs[i],span=catalog.index[ref];
    const state=!evidenceSpan.test(ref)?'invalid':!allowed.has(ref)&&span?'outside_batch':!span?'missing':'resolved';
    need(fullKeys(reference,'span_ref,state,source_ref,start,end,text,source_kind,source_number,revision_kind')
      &&reference.span_ref===ref&&reference.state===state);
    if(state!=='resolved')need(['source_ref','start','end','text','source_kind','source_number','revision_kind'].every(key=>reference[key]===null));
    else {
      const ocr=span.source_ref.startsWith('o:'),number=Number(span.source_ref.slice(3));
      const revision=ocr?preview.source_snapshot.pages.find(page=>page.page===number)?.revision.kind:null;
      need(reference.source_ref===span.source_ref&&reference.start===span.start&&reference.end===span.end
        &&reference.text===span.text&&reference.source_kind===(ocr?'ocr_page':'word_block')
        &&reference.source_number===number&&reference.revision_kind===revision
        &&(!ocr||['raw','edited','confirmed'].includes(revision)));
    }
  }
}

function visibleConstraints(fact){
  const f=fact.fields,refs=f.span_refs;
  return kinds.includes(f.kind)&&nonempty(f.statement,2000)&&statuses.includes(f.status)
    &&typeof f.explanation==='string'&&fullText(f.explanation,1000)
    &&(f.status==='explicit'||!!f.explanation.trim())
    &&Array.isArray(refs)&&refs.length<=16&&new Set(refs).size===refs.length
    &&(f.status!=='explicit'||refs.length>0)&&fact.references.every(reference=>reference.state==='resolved');
}

export async function verifyEvidenceSavedFactsV2(value,account,row,stage){
  await verifyEvidenceDraft(row,account);
  const preview=row.preview,s=row.maps.find(item=>item.chunk_id===stage),generation=preview.map_preparation.generation_contract.schema;
  const catalog=await evidenceCatalog(preview),chunk=preview.map_preparation.plan.chunks.find(item=>item.chunk_id===stage);
  need(row.schema==='report-evidence-reading-draft-v3'&&Object.hasOwn(deliveries,generation)
    &&/^map-([1-9]|1\d|2[0-4])$/.test(stage)&&s&&chunk
    &&fullKeys(value,'schema,actor_id,workspace_id,draft_id,draft_revision,stage,stage_status,generation_schema,source_hash,catalog_hash,messages_hash,journal,availability,message,original_protocol_valid,protocol_issues,facts,slot_origin,tool_metadata,meaning_review_status,eligible_for_reduce,can_apply,can_publish,view_hash')
    &&value.schema==='report-evidence-saved-facts-v2');
  need(value.actor_id===account.user_id&&value.workspace_id===account.workspace_id&&value.draft_id===row.id
    &&value.draft_revision===row.revision&&value.stage===stage&&value.stage_status===s.status
    &&value.generation_schema===generation&&value.source_hash===preview.source_hash
    &&value.catalog_hash===catalog.descriptor.catalog_hash&&value.messages_hash===chunk.messages_hash
    &&same(value.journal,s.journal)&&states.includes(value.availability)&&nonempty(value.message,4000)
    &&value.meaning_review_status==='unreviewed'&&value.eligible_for_reduce===false
    &&value.can_apply===false&&value.can_publish===false&&fullSha.test(value.view_hash));
  const {view_hash,...body}=value;
  need(view_hash===await digest(body)&&new TextEncoder().encode(stableStringify(value)).length<=bounded);
  checkIssues(value.protocol_issues);need(Array.isArray(value.facts)&&value.facts.length<=80);
  if(value.availability==='missing'||value.availability==='quarantined'){
    need(value.facts.length===0&&value.slot_origin===null&&value.tool_metadata===null
      &&value.original_protocol_valid===null&&value.protocol_issues.length===0);
    need(value.availability==='missing'?value.journal===null:value.journal?.consumable===false);
    return value;
  }
  need(value.journal?.consumable===true);checkMetadata(value.tool_metadata,s,deliveries[generation]);
  if(value.availability==='not_json'||value.availability==='unsupported_structure'){
    need(value.facts.length===0&&value.slot_origin===null&&value.original_protocol_valid===false
      &&value.protocol_issues.some(issue=>issue.code===(value.availability==='not_json'?'response_or_arguments_invalid':'unsupported_structure')));
    if(value.availability==='unsupported_structure')need(complete(value.tool_metadata,s,generation));
    return value;
  }
  // An incomplete/nonunique tool cannot supply even a first display fact.
  need(complete(value.tool_metadata,s,generation)&&typeof value.original_protocol_valid==='boolean'
    &&value.original_protocol_valid===(value.protocol_issues.length===0));
  const origin=value.slot_origin;
  need(fullKeys(origin,'wire_schema,codec,wire_value_hash,arguments_sha256,arguments_bytes,slot_positions')
    &&origin.wire_schema===wireSchema&&origin.codec==='report-evidence-slot-display-only-v1'
    &&(origin.wire_value_hash===null||fullSha.test(origin.wire_value_hash))
    &&origin.arguments_sha256===value.tool_metadata.arguments_sha256
    &&origin.arguments_bytes===value.tool_metadata.arguments_bytes
    &&Array.isArray(origin.slot_positions)&&origin.slot_positions.length===value.facts.length
    &&origin.slot_positions.every((position,i)=>fullInt(position)&&position<80&&(i===0||origin.slot_positions[i-1]<position)));
  const allowed=new Set(chunk.span_refs),ids=new Set();let reconstructable=true;
  const wire={schema:wireSchema,slots:Object.fromEntries(Array.from({length:80},(_,position)=>[slotName(position),{state:'absent'}]))};
  for(const [i,fact] of value.facts.entries()){
    const fields=fact?.fields,position=origin.slot_positions[i];
    need(fullKeys(fact,'position,slot_name,display_id,protocol_key,fields,field_constraints_valid,issues,references,meaning_review_status')
      &&fact.position===position&&fact.slot_name===slotName(position)&&fact.protocol_key===null
      &&typeof fact.display_id==='string'&&/^sf_[a-f0-9]{64}$/.test(fact.display_id)&&!ids.has(fact.display_id)
      &&fullKeys(fields,'kind,statement,span_refs,status,explanation')
      &&['kind','statement','status','explanation'].every(key=>fields[key]===null||string(fields[key]))
      &&(fields.span_refs===null||Array.isArray(fields.span_refs)&&fields.span_refs.every(string))
      &&typeof fact.field_constraints_valid==='boolean'&&fact.meaning_review_status==='unreviewed');
    need(fact.display_id==='sf_'+await digest({schema:'report-evidence-saved-slot-id-v2',
      binding_hash:value.journal.binding_hash,arguments_sha256:origin.arguments_sha256,
      path:'/slots/'+fact.slot_name+'/claim'}));ids.add(fact.display_id);checkIssues(fact.issues);
    checkReferences(fact,catalog,allowed,preview);
    const visible=visibleConstraints(fact),extra=fact.issues.some(issue=>issue.code==='extra_fields');
    need(fact.field_constraints_valid===(fact.issues.length===0)
      &&(!fact.field_constraints_valid||visible)&&(!visible||fact.field_constraints_valid||extra));
    if(extra||Object.values(fields).some(field=>field===null))reconstructable=false;
    else wire.slots[fact.slot_name]={state:'present',claim:fields};
    if(!fact.field_constraints_valid)need(value.original_protocol_valid===false);
  }
  if(reconstructable)need(origin.wire_value_hash===await digest(wire));
  if(origin.wire_value_hash===null)need(!reconstructable&&value.protocol_issues.some(issue=>issue.code==='unsupported_wire_values'));
  if(value.original_protocol_valid)need(origin.wire_value_hash!==null&&value.facts.every(fact=>fact.field_constraints_valid));
  // A successful immutable projection provides a second, independent value
  // binding; an invalid display is never substituted for that projection.
  if(s.result)need(origin.wire_value_hash===s.result.wire_value_hash
    &&same(origin.slot_positions,s.result.slot_positions)&&same(value.tool_metadata,s.result.tool_metadata));
  return value;
}
