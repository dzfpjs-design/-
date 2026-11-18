import {digest, fullKeys, fullInt, fullSha, verifyEvidenceList} from './report-evidence-reading-contract.mjs';

// This independently versioned wrapper keeps the existing exact list contract.
export async function verifyEvidenceListV2(value, account) {
  if (!fullKeys(value, 'schema,actor_id,workspace_id,list,quarantined_count,list_v2_hash')
      || value.schema !== 'report-evidence-reading-list-v2'
      || value.actor_id !== account.user_id || value.workspace_id !== account.workspace_id
      || !fullInt(value.quarantined_count, 0) || value.quarantined_count > 100
      || !fullSha.test(value.list_v2_hash)) throw new Error('报告列表状态无法核验');
  const unsigned = {...value}; delete unsigned.list_v2_hash;
  if (await digest(unsigned) !== value.list_v2_hash) throw new Error('报告列表状态无法核验');
  await verifyEvidenceList(value.list, account);
  return value;
}
