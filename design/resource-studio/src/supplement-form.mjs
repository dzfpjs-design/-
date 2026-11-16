import {approvalPlanState} from './supplement-approval-v245-protocol.mjs';
export const blankSupplement=()=>({keys:[],seconds:6,consent:false});
export function readySupplement(row,form,role){return role==='admin'&&row?.status==='proposed'&&form.consent===true&&Number.isInteger(form.seconds)&&form.seconds>=4&&form.seconds<=12&&form.keys.length>=1&&form.keys.length<=3&&new Set(form.keys).size===form.keys.length&&form.keys.every(k=>row.proposal.actions.some(a=>a.key===k));}
export function recoverSupplement(action,instanceId){return !!action&&action.instanceId===instanceId&&typeof action.id==='string'&&action.body?.confirm_one_time_read===true&&typeof action.body.request_id==='string';}
export function needsSupplement(assessment){return assessment?.status==='completed'&&assessment.result?.objects?.some(o=>o.gaps.length>0);}
export function supplementConfirmationState(action,row,instanceId,scope=null){
 if(!recoverSupplement(action,instanceId)||row?.id!==action.id||row.instance_id!==instanceId)return 'conflict';
 if(!scope)return !row.approval_request_id&&!row.approved&&!(row.sessions?.length)?'unknown':'unverified';
 return approvalPlanState(action,row,scope);
}
