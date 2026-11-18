import {digest,fullKeys,fullUUID} from './report-full-reading-contract.mjs';
const need=x=>{if(!x)throw Error('定向启用状态无法核对，请重新读取。');};
export async function verifyScopedActivationStatus(value,account,plan){
 need(fullKeys(value,'schema,workspace_id,actor_id,preview_id,activation_id,status,plan_status,expires_at,execution_history,this_action_calls_model,user_confirmed_business,in_flight_may_continue,status_hash')&&value.schema==='report-scoped-activation-status-v1');
 need(value.workspace_id===account.workspace_id&&value.actor_id===account.user_id&&value.preview_id===plan.preview_id&&account.role==='admin'&&fullUUID.test(value.preview_id||'')&&['not_found','active','revoked','expired'].includes(value.status)&&['saved','started','expired'].includes(value.plan_status));
 need(value.execution_history==='unknown'&&value.this_action_calls_model===false&&value.user_confirmed_business===false&&value.in_flight_may_continue===true);
 need(value.status==='not_found'?value.activation_id===null&&value.expires_at===null:fullUUID.test(value.activation_id||'')&&typeof value.expires_at==='string'&&Number.isFinite(Date.parse(value.expires_at)));
 const {status_hash,...unsigned}=value;need(/^[0-9a-f]{64}$/.test(status_hash||'')&&await digest(unsigned)===status_hash);
 return value;
}
