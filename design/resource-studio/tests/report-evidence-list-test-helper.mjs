import {digest} from '../src/report-evidence-reading-contract.mjs';
export async function listV2(list,count=0){const value={schema:'report-evidence-reading-list-v2',actor_id:list.actor_id,workspace_id:list.workspace_id,list,quarantined_count:count};return {...value,list_v2_hash:await digest(value)};}
