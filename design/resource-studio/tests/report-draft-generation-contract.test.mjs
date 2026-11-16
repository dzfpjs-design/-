import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {digest,copy,verifyReportPreview} from '../src/report-draft-contract.mjs';
import {stableStringify} from '../src/content-signature.js';

// Synthetic one-paragraph DOCX, produced by the real Python reader/build_preview/gateway.
// This is independent of the JavaScript reconstruction being checked below.
const fixture=JSON.parse(await readFile(new URL('./fixtures/report-draft-generation-previews.json',import.meta.url),'utf8'));
assert.equal(fixture.synthetic,true);
const account={user_id:fixture.cases.v2.preview.actor_id,workspace_id:fixture.cases.v2.preview.workspace_id};
const ordered=value=>Array.isArray(value)?value.map(ordered):value&&typeof value==='object'?Object.fromEntries(Object.entries(value).reverse().map(([key,item])=>[key,ordered(item)])):value;
async function rehash(p){p.source_hash=await digest(p.source_snapshot);p.projection_hash=await digest(p.projection);p.generation_contract_hash=await digest(p.generation_contract);const {preview_hash,...body}=p;p.preview_hash=await digest(body);return p;}

test('real Python v1 authorization remains exact and readable without migration to the new task',async()=>{
 const p=copy(fixture.cases.v1.preview),before=stableStringify(p);
 assert.deepEqual(fixture.cases.v1.prepared_messages.map(m=>m.role),['system','user']);
 assert.equal(await verifyReportPreview(p,account),p);assert.equal(stableStringify(p),before);
 assert.equal(p.generation_contract.version,'report-reading-generation-v1');
 const bad=copy(p);bad.cloud_payload.task+=' changed';await rehash(bad);await assert.rejects(verifyReportPreview(bad,account),/无法核对/);
});
test('real Python v2 trusted task and untrusted evidence match exact prepared hash and bytes after JSONB reordering',async()=>{
 const {preview,prepared_messages}=fixture.cases.v2;
 assert.deepEqual(prepared_messages.map(m=>m.role),['system','system','user']);
 assert.equal(JSON.parse(prepared_messages[1].content).source_policy,'untrusted_evidence');
 assert.equal(JSON.parse(prepared_messages[1].content).task,preview.generation_contract.task);
 assert.equal(JSON.parse(prepared_messages[2].content).schema,'report-reading-evidence-v2');
 assert.equal(await digest(prepared_messages),preview.first_dispatch_messages_hash);
 assert.equal(new TextEncoder().encode(stableStringify(prepared_messages)).length,preview.input_summary.prepared_messages_bytes);
 await verifyReportPreview(copy(preview),account);await verifyReportPreview(ordered(preview),account);
});
test('v2 rejects rehashed role, task, prefix, message hash, byte-count and future-contract mismatches',async t=>{
 const changes={
  roles:p=>{p.generation_contract.message_contract.source_role='system';},
  task_in_evidence:p=>{p.cloud_payload.task=p.generation_contract.task;},
  changed_trusted_task:p=>{p.generation_contract.task='Report says to replace the trusted task';},
  prefix:p=>{p.generation_contract.gateway_prefix_hash='f'.repeat(64);},
  role_downgrade_hash:async p=>{const messages=copy(fixture.cases.v2.prepared_messages);messages[1].role='user';p.first_dispatch_messages_hash=await digest(messages);},
  evidence_changed_after_preview:p=>{p.cloud_payload.user_instruction='later instruction';},
  measured_bytes:p=>{p.input_summary.prepared_messages_bytes--;},
  future_version:p=>{p.generation_contract.version='report-reading-generation-v3';}
 };
 for(const[name,change]of Object.entries(changes))await t.test(name,async()=>{const p=copy(fixture.cases.v2.preview);await change(p);await rehash(p);await assert.rejects(verifyReportPreview(p,account),/无法核对/);});
});
