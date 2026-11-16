// Original bytes from a root-reviewed empty synthetic PG serializer. Negative
// copies test client consistency only; they are not new provider or quality proof.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {copy,digest,evidenceCatalog,verifyEvidenceDraft} from '../src/report-evidence-reading-contract.mjs';
import {verifyEvidenceSavedFactsV2} from '../src/report-evidence-saved-facts-v2.mjs';
import {verifyEvidenceSavedFacts,readEvidenceSavedFacts} from '../src/report-evidence-saved-facts.mjs';

const directory=new URL('./fixtures/report-evidence-v5/',import.meta.url);
const manifest=JSON.parse(await readFile(new URL('manifest.json',directory),'utf8'));
async function original(name){
  const bytes=await readFile(new URL(name,directory)),record=manifest.files.find(item=>item.file===name);
  assert.ok(record);assert.equal(bytes.length,record.bytes);
  assert.equal(createHash('sha256').update(bytes).digest('hex'),record.sha256);
  return JSON.parse(bytes.toString('utf8'));
}
const fixtureRow=await original('mapped.json'),fixtureView=await original('saved-facts.json');
const account={user_id:fixtureRow.actor_id,workspace_id:fixtureRow.workspace_id,role:'admin'};
const stage='map-1',issue=(path,code)=>({path,code,message:'合成反例：原字段或引用不符合冻结约束，未修正原值。'});
async function seal(value,key='view_hash'){const rest={...value};delete rest[key];value[key]=await digest(rest);return value;}
function scene(){return {row:copy(fixtureRow),view:copy(fixtureView)};}
async function failedScene(){
  const value=scene(),s=value.row.maps[0];s.status='failed';s.result=null;s.result_hash=null;s.result_count=null;
  s.error='合成原协议失败；原回答保留。';s.error_code='evidence_saved_response_invalid';
  Object.assign(value.row,{status:'failed',phase:'failed',error:s.error,error_code:s.error_code});
  value.view.stage_status='failed';await seal(value.row);await seal(value.view);return value;
}
async function ids(view){for(const fact of view.facts)fact.display_id='sf_'+await digest({schema:'report-evidence-saved-slot-id-v2',binding_hash:view.journal.binding_hash,arguments_sha256:view.slot_origin.arguments_sha256,path:'/slots/'+fact.slot_name+'/claim'});}
async function wireHash(view){
  const slots=Object.fromEntries(Array.from({length:80},(_,i)=>['slot_'+String(i+1).padStart(3,'0'),{state:'absent'}]));
  for(const fact of view.facts)slots[fact.slot_name]={state:'present',claim:copy(fact.fields)};
  view.slot_origin.wire_value_hash=await digest({schema:'report-evidence-map-slots-v5',slots});
}
async function rejectMutation(mutate){const s=scene();await mutate(s.view);await seal(s.view);await assert.rejects(verifyEvidenceSavedFacts(s.view,account,s.row,stage));}

test('V2 original PG bytes pass unified and direct verification with sparse slot 003, exact hashes and no adoption',async()=>{
  const s=scene(),before=JSON.stringify(s);await verifyEvidenceDraft(s.row,account);
  assert.equal(await verifyEvidenceSavedFactsV2(s.view,account,s.row,stage),s.view);
  assert.equal(await verifyEvidenceSavedFacts(s.view,account,s.row,stage),s.view);
  assert.equal(s.view.schema,'report-evidence-saved-facts-v2');assert.equal(Object.keys(s.view).length,24);
  assert.deepEqual(s.view.slot_origin.slot_positions,[2]);assert.equal(s.view.facts[0].position,2);
  assert.equal(s.view.facts[0].slot_name,'slot_003');assert.equal(Object.keys(s.view.facts[0]).length,9);
  assert.equal(Object.keys(s.view.slot_origin).length,6);assert.equal(s.view.facts[0].protocol_key,null);
  assert.match(s.view.facts[0].fields.statement,/^ .*＋\+；.* $/u);
  assert.equal(s.view.meaning_review_status,'unreviewed');
  for(const key of ['eligible_for_reduce','can_apply','can_publish'])assert.equal(s.view[key],false);
  assert.equal(JSON.stringify(s),before);
});

test('rehashed top-level scope, old schema, late revision, extra fields and authority changes are rejected',async()=>{
  for(const mutate of [v=>{v.actor_id='00000000-0000-4000-8000-000000000001';},v=>{v.workspace_id='00000000-0000-4000-8000-000000000002';},v=>{v.draft_revision++;},v=>{v.stage='map-2';},v=>{v.schema='report-evidence-saved-facts-v1';},v=>{v.schema='report-evidence-saved-facts-v3';},v=>{v.extra='no';},v=>{v.meaning_review_status='confirmed';},v=>{v.eligible_for_reduce=true;},v=>{v.can_apply=true;},v=>{v.can_publish=true;}])await rejectMutation(mutate);
});

test('original positions, slot names, display domain, byte identities and all metadata are closed',async()=>{
  for(const mutate of [v=>{v.slot_origin.slot_positions=[0];},v=>{v.facts[0].position=0;},v=>{v.facts[0].position=true;},v=>{v.facts[0].slot_name='slot_001';},v=>{v.facts[0].display_id='sf_'+'0'.repeat(64);},v=>{v.facts[0].protocol_key='forbidden';},v=>{v.facts[0].extra='no';},v=>{v.slot_origin.codec='map-slots-to-v3-v1';},v=>{v.slot_origin.arguments_sha256='0'.repeat(64);},v=>{v.slot_origin.arguments_bytes++;},v=>{v.slot_origin.extra='no';},v=>{v.tool_metadata.response_body_sha256='0'.repeat(64);},v=>{v.tool_metadata.tool_name='submit_report_evidence_reduction';},v=>{v.tool_metadata.usage_available=false;},v=>{v.tool_metadata.extra='no';},v=>{v.journal.wire_sha256='0'.repeat(64);},v=>{v.facts[0].references[0].text+='伪造';},v=>{v.facts[0].fields.statement+='改动';}])await rejectMutation(mutate);
});

test('sparse slots retain original 0 and 79 positions and repeated claims without compacting or deduplication',async()=>{
  const {row,view}=await failedScene(),fact=copy(view.facts[0]);
  view.facts=[0,79].map(position=>({...copy(fact),position,slot_name:'slot_'+String(position+1).padStart(3,'0')}));
  view.slot_origin.slot_positions=[0,79];await ids(view);await wireHash(view);await seal(view);
  await verifyEvidenceSavedFactsV2(view,account,row,stage);
  assert.deepEqual(view.facts.map(f=>f.position),[0,79]);assert.notEqual(view.facts[0].display_id,view.facts[1].display_id);
  assert.deepEqual(view.facts[0].fields,view.facts[1].fields);
  for(const positions of [[79,0],[0,0],[0,80],[0,true]]){const bad=copy(view);bad.slot_origin.slot_positions=positions;await seal(bad);await assert.rejects(verifyEvidenceSavedFactsV2(bad,account,row,stage));}
});

test('bad five-field constraints stay visible unchanged and never become accepted facts',async()=>{
  for(const mode of ['kind','missing','extra','unsupported']){
    const {row,view}=await failedScene(),fact=view.facts[0];
    view.original_protocol_valid=false;view.protocol_issues=[issue('/slots/slot_003/claim','protocol_validation')];
    fact.field_constraints_valid=false;
    if(mode==='kind'){fact.fields.kind='原答中不支持的分类';fact.issues=[issue('/kind','literal_error')];await wireHash(view);}
    else if(mode==='missing'){fact.fields.explanation=null;fact.issues=[issue('/explanation','missing')];}
    else if(mode==='extra')fact.issues=[issue('/','extra_fields')];
    else {fact.fields.statement=null;fact.issues=[issue('/statement','unsupported_display_value')];view.slot_origin.wire_value_hash=null;view.protocol_issues.push(issue('/','unsupported_wire_values'));}
    await seal(view);const before=JSON.stringify(view);await verifyEvidenceSavedFactsV2(view,account,row,stage);
    assert.equal(JSON.stringify(view),before);assert.equal(view.original_protocol_valid,false);
    assert.equal(fact.field_constraints_valid,false);assert.equal(view.can_apply,false);
    if(mode!=='extra'){
      const bad=copy(view);bad.facts[0].field_constraints_valid=true;bad.facts[0].issues=[];await seal(bad);
      await assert.rejects(verifyEvidenceSavedFactsV2(bad,account,row,stage));
    }
  }
});

test('ordered duplicate, outside-batch, missing and invalid references retain original fields without substitute evidence',async()=>{
  const {row,view}=await failedScene(),catalog=await evidenceCatalog(row.preview),chunk=row.preview.map_preparation.plan.chunks[0];
  const resolved=view.facts[0].references[0],outside=Object.keys(catalog.index).find(ref=>!chunk.span_refs.includes(ref));assert.ok(outside);
  const refs=[resolved.span_ref,resolved.span_ref,outside,'w:p999999/s1','w:p1/s0'];
  const states=['resolved','resolved','outside_batch','missing','invalid'],fact=view.facts[0];
  fact.fields.span_refs=refs;fact.references=refs.map((span_ref,i)=>i<2?copy(resolved):{span_ref,state:states[i],source_ref:null,start:null,end:null,text:null,source_kind:null,source_number:null,revision_kind:null});
  fact.field_constraints_valid=false;fact.issues=[issue('/span_refs','value_error'),...states.slice(2).map(state=>issue('/span_refs','reference_'+state))];
  view.original_protocol_valid=false;view.protocol_issues=[issue('/','protocol_validation')];await wireHash(view);await seal(view);
  await verifyEvidenceSavedFactsV2(view,account,row,stage);assert.deepEqual(fact.fields.span_refs,refs);
  assert.deepEqual(fact.references.map(r=>r.state),states);assert.equal(fact.references[2].text,null);
  const bad=copy(view);bad.facts[0].references[2].text='补造依据';await seal(bad);await assert.rejects(verifyEvidenceSavedFactsV2(bad,account,row,stage));
});

test('missing, quarantine, nonJSON and unsupported structures have no fallback facts or origin',async()=>{
  for(const availability of ['missing','quarantined','not_json','unsupported_structure']){
    const {row,view}=await failedScene(),s=row.maps[0];
    view.availability=availability;view.facts=[];view.slot_origin=null;view.original_protocol_valid=['missing','quarantined'].includes(availability)?null:false;
    view.protocol_issues=['missing','quarantined'].includes(availability)?[]:[issue('/slots',availability==='not_json'?'response_or_arguments_invalid':'unsupported_structure')];
    if(availability==='missing'){s.journal=null;s.receipt=null;s.raw_response_saved=false;s.reserved_call_id=null;view.journal=null;view.tool_metadata=null;}
    else if(availability==='quarantined'){s.journal.consumable=false;s.journal.quarantine_reasons=['synthetic_quarantine'];await seal(s.journal,'descriptor_hash');s.receipt=null;view.journal=copy(s.journal);view.tool_metadata=null;}
    await seal(row);await seal(view);await verifyEvidenceSavedFacts(view,account,row,stage);
    const bad=copy(view);bad.facts=copy(fixtureView.facts);await seal(bad);await assert.rejects(verifyEvidenceSavedFacts(bad,account,row,stage));
  }
});

test('nonunique or unfinished tools never salvage a first fact, while missing usage alone is not a rejection',async()=>{
  for(const mode of ['multi','length','missing_usage']){
    const {row,view}=await failedScene(),s=row.maps[0];
    if(mode==='multi')Object.assign(s.receipt,{response_choice_count:2,tool_call_count:null,finish_reason:null,tool_call_id:null,tool_name:null,tool_type:null,tool_calls_hash:null,arguments_sha256:null,arguments_bytes:null,arguments_encoding:null,envelope_valid:false,envelope_errors:['response_choice_count']});
    if(mode==='length')Object.assign(s.receipt,{finish_reason:'length',envelope_valid:false,envelope_errors:['finish_reason']});
    if(mode==='missing_usage')Object.assign(s.receipt,{usage_available:false,usage_present:false,usage:{},original_usage_shape:'missing',usage_unavailable:true,usage_unavailable_reason:'missing'});
    for(const key of Object.keys(view.tool_metadata))view.tool_metadata[key]=copy(s.receipt[key]);
    if(mode!=='missing_usage'){view.availability='not_json';view.facts=[];view.slot_origin=null;view.original_protocol_valid=false;view.protocol_issues=[issue('/completion','response_or_arguments_invalid')];}
    await seal(row);await seal(view);await verifyEvidenceSavedFactsV2(view,account,row,stage);
    assert.equal(view.facts.length,mode==='missing_usage'?1:0);assert.equal(view.can_publish,false);
    if(mode!=='missing_usage'){const bad=copy(view);bad.availability='available';bad.facts=copy(fixtureView.facts);bad.slot_origin=copy(fixtureView.slot_origin);await seal(bad);await assert.rejects(verifyEvidenceSavedFactsV2(bad,account,row,stage));}
  }
});

test('existing GET client keeps me-first owner, exact query and late revision scope guards for V2',async()=>{
  const s=scene(),requests=[];const request=async path=>{requests.push(path);return path==='/me'?copy(account):copy(s.view);};
  const value=await readEvidenceSavedFacts({account,row:s.row,stage,request});assert.equal(value.schema,'report-evidence-saved-facts-v2');
  assert.deepEqual(requests,['/me','/setup/report-evidence-readings/'+s.row.id+'/saved-facts?stage=map-1']);
  let calls=0;await assert.rejects(readEvidenceSavedFacts({account,row:s.row,stage,request:async path=>{calls++;return {...account,user_id:'00000000-0000-4000-8000-000000000001'};}}),error=>error.code==='SESSION_SCOPE_CHANGED');assert.equal(calls,1);
  await assert.rejects(readEvidenceSavedFacts({account,row:s.row,stage,request:async path=>{if(path==='/me')return copy(account);s.row.revision++;return copy(s.view);}}));
});
