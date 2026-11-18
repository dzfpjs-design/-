import test from 'node:test';
import assert from 'node:assert/strict';
import {webcrypto} from 'node:crypto';
import {digest} from '../src/report-evidence-reading-contract.mjs';
import {verifyEvidenceListV2} from '../src/report-evidence-list-v2.mjs';
globalThis.crypto ??= webcrypto;
const account = {user_id:'actor', workspace_id:'workspace'};
async function fixture(count=1){
  const list = {schema:'report-evidence-reading-list-v1',actor_id:'actor',workspace_id:'workspace',items:[]};
  list.list_hash = await digest(list);
  const v = {schema:'report-evidence-reading-list-v2',actor_id:'actor',workspace_id:'workspace',list,quarantined_count:count};
  v.list_v2_hash = await digest(v); return v;
}
test('distinguishes quarantined count without exposing an actionable record', async()=>{
  const v = await fixture(); assert.equal((await verifyEvidenceListV2(v,account)).quarantined_count,1);
  assert.deepEqual(v.list.items,[]);
  await assert.rejects(verifyEvidenceListV2({...v,source:'secret'},account));
  await assert.rejects(verifyEvidenceListV2({...v,quarantined_count:0},account));
  await assert.rejects(verifyEvidenceListV2(v,{...account,user_id:'other'}));
});
