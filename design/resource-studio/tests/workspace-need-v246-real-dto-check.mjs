// Root supplies a capture from ordinary authenticated production API calls.
// No synthetic transport, fixture import, network, credentials or fallback.
// Usage: node tests/workspace-need-v246-real-dto-check.mjs /absolute/capture.json
// Capture: {account,instance_id,options,original,fresh,field_key,purpose,
//           expected_value:27,expected_kind?,expected_unit?}
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {isAbsolute} from 'node:path';
import {webcrypto} from 'node:crypto';
import {needScope,checkedNeedOptions,checkedNeedView,compatibleNeedCopy,needCanonical} from '../src/workspace-need-v246-protocol.mjs';
const file=process.argv[2];
assert.ok(file&&isAbsolute(file),'Supply an explicit absolute path to actual API response capture; no fixture fallback');
const capture=JSON.parse(await readFile(file,'utf8'));
assert.ok(capture&&typeof capture.field_key==='string'&&typeof capture.purpose==='string');
assert.equal(capture.expected_value,27,'This acceptance check proves the selected integer quantity 27');
const scope=needScope(capture.account,capture.instance_id),options=checkedNeedOptions(capture.options);
const original=await checkedNeedView(capture.original,scope,webcrypto),fresh=await checkedNeedView(capture.fresh,scope,webcrypto);
assert.notEqual(original.id,fresh.id,'Another question must be a distinct persisted query');
assert.notEqual(original.copy_descriptor.source.request_id,fresh.copy_descriptor.source.request_id,'Another question must not rewrite the original request');
assert.equal(original.purpose,capture.purpose);
assert.equal(fresh.purpose,capture.purpose,'Preserve the exact original sentence');
assert.deepEqual(original.fields,fresh.fields);
assert.ok(fresh.fields.includes(capture.field_key));
assert.equal(original.copy_descriptor.source.business_id,fresh.copy_descriptor.source.business_id);
assert.equal(original.entity_id,fresh.entity_id);
assert.equal(needCanonical(original.copy_descriptor.conditions),needCanonical(fresh.copy_descriptor.conditions),'Source, review and time conditions must stay identical');
compatibleNeedCopy(original.copy_descriptor,options);
compatibleNeedCopy(fresh.copy_descriptor,options);
assert.equal(needCanonical(original.copy_descriptor.source_fields),needCanonical(fresh.copy_descriptor.source_fields),'No field type, unit, label or schema substitution');
const meta=fresh.copy_descriptor.source_fields.find(f=>f.key===capture.field_key);
assert.ok(meta&&meta.kind&&meta.schema_hash);
if(Object.hasOwn(capture,'expected_kind'))assert.equal(meta.kind,capture.expected_kind);
if(Object.hasOwn(capture,'expected_unit'))assert.equal(meta.unit,capture.expected_unit);
const source=fresh.copy_descriptor.conditions.source;
if(source==='observations'){
 const evidence=(fresh.records||[]).find(r=>r.extracted_values?.[capture.field_key]===27);
 assert.ok(evidence,'Actual detail must contain the selected quantity as a numeric evidence value');
 assert.equal(evidence.original_values?.[capture.field_key],27,'The recorded input must also preserve integer 27');
 assert.equal(typeof evidence.extracted_values[capture.field_key],'number');
 assert.ok(Number.isInteger(evidence.extracted_values[capture.field_key]));
 assert.ok(evidence.observation_id&&evidence.reported_at&&evidence.review_input_hash,'Preserve source, time and review context');
 assert.equal(evidence.physical_state_verified,false,'Record agreement must not claim physical-state verification');
}else{
 const evidence=(fresh.archive_evidence||[]).find(r=>r.fields?.[capture.field_key]===27);
 assert.ok(evidence&&Number.isInteger(evidence.fields[capture.field_key]),'Actual archive evidence must contain integer 27');
}
assert.equal(original.parent_goal_completed,false);
assert.equal(fresh.parent_goal_completed,false);
console.log(JSON.stringify({actual_api_views:2,scope_verified:true,distinct_persisted_queries:true,original_sentence_preserved:true,field_metadata_preserved:true,selected_quantity:27,value_type:'integer',source,physical_state_claimed:false}));
