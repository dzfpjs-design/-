import {digest, same} from './report-full-reading-contract.mjs';

const SHA=/^[a-f0-9]{64}$/,UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const ALG=/^[a-z0-9][a-z0-9-]{0,79}$/;
const keys=(o,names)=>!!o&&typeof o==='object'&&!Array.isArray(o)&&Object.keys(o).sort().join(',')===names.split(',').sort().join(',');
const safeInt=x=>Number.isSafeInteger(x)&&x>0;
const bad=()=>{throw Error('保存范围或服务器回执无法核对；原记录保留。');};
const HASHES=['source_hash','raw_hash','preview_hash','view_hash','candidate_hash','quarantine_hash','command_hash','receipt_hash'];
const RECEIPT='schema,workspace_id,actor_id,draft_id,stage,request_id,draft_revision,source_hash,raw_hash,preview_hash,view_hash,algorithm_version,candidate_hash,quarantine_hash,command_hash,receipt_hash';

export function provisionalScope(account,row,stage){
 if(!UUID.test(account?.user_id||'')||!UUID.test(account?.workspace_id||'')||!account?.role||
    row?.actor_id!==account.user_id||row?.workspace_id!==account.workspace_id||!UUID.test(row?.id||'')||
    !safeInt(row.revision)||!/^extract-[12]$/.test(stage||'')||!row.extractions?.some(s=>s.batch_id===stage)||
    !SHA.test(row.preview?.source_hash||'')||!SHA.test(row.preview?.preview_hash||''))bad();
 return {workspace_id:account.workspace_id,actor_id:account.user_id,draft_id:row.id,stage};
}

export async function provisionalBody(account,row,stage,preview){
 const scope=provisionalScope(account,row,stage);
 if(preview?.schema!=='report-full-failed-candidate-preview-v1'||preview.status!=='needs_review'||preview.confirmed!==false||
    preview.can_apply!==false||preview.can_publish!==false||!SHA.test(preview.view_hash||'')||!ALG.test(preview.algorithm_version||'')||
    !SHA.test(preview.scope?.raw_hash||'')||preview.scope?.revision!==row.revision||preview.scope?.preview_hash!==row.preview.preview_hash||
    Object.entries(scope).some(([k,v])=>preview.scope?.[k]!==v))bad();
 const {view_hash,...signed}=preview;if(await digest(signed)!==view_hash)bad();
 const base={revision:row.revision,source_hash:row.preview.source_hash,raw_hash:preview.scope.raw_hash,
             view_hash,algorithm_version:preview.algorithm_version};
 const h=await digest({namespace:'generic-agents/report-full-provisional-request/v1',...scope,preview_hash:row.preview.preview_hash,body:base});
 const variant=((parseInt(h[16],16)&3)|8).toString(16);
 const n=h.slice(0,12)+'5'+h.slice(13,16)+variant+h.slice(17,32);
 const request_id=n.slice(0,8)+'-'+n.slice(8,12)+'-'+n.slice(12,16)+'-'+n.slice(16,20)+'-'+n.slice(20,32);
 return {...base,request_id};
}

export async function checkPending(entry,account,row,stage){
 const scope=provisionalScope(account,row,stage);
 if(!keys(entry,'schema,scope,body,preview_hash')||entry.schema!=='report-full-provisional-intent-v1'||!same(entry.scope,scope)||
    !SHA.test(entry.preview_hash||'')||entry.preview_hash!==row.preview.preview_hash||
    !keys(entry.body,'revision,request_id,source_hash,raw_hash,view_hash,algorithm_version')||!safeInt(entry.body.revision)||
    entry.body.revision>row.revision||!UUID.test(entry.body.request_id||'')||!SHA.test(entry.body.source_hash||'')||
    entry.body.source_hash!==row.preview.source_hash||!SHA.test(entry.body.raw_hash||'')||!SHA.test(entry.body.view_hash||'')||!ALG.test(entry.body.algorithm_version||''))bad();
 const {request_id,...base}=entry.body;
 const h=await digest({namespace:'generic-agents/report-full-provisional-request/v1',...scope,preview_hash:entry.preview_hash,body:base});
 const n=h.slice(0,12)+'5'+h.slice(13,16)+((parseInt(h[16],16)&3)|8).toString(16)+h.slice(17,32);
 if(request_id!==n.slice(0,8)+'-'+n.slice(8,12)+'-'+n.slice(12,16)+'-'+n.slice(16,20)+'-'+n.slice(20,32))bad();
 return entry;
}

export async function checkReceipt(receipt,account,row,stage){
 const scope=provisionalScope(account,row,stage);
 if(!keys(receipt,RECEIPT)||receipt.schema!=='report-full-provisional-receipt-v1'||
    Object.entries(scope).some(([k,v])=>receipt[k]!==v)||!UUID.test(receipt.request_id||'')||
    !safeInt(receipt.draft_revision)||receipt.draft_revision>row.revision||!ALG.test(receipt.algorithm_version||'')||
    HASHES.some(k=>!SHA.test(receipt[k]||''))||receipt.source_hash!==row.preview.source_hash||
    receipt.preview_hash!==row.preview.preview_hash)bad();
 const {receipt_hash,...unsigned}=receipt;if(await digest(unsigned)!==receipt_hash)bad();
 const body={revision:receipt.draft_revision,request_id:receipt.request_id,source_hash:receipt.source_hash,
   raw_hash:receipt.raw_hash,view_hash:receipt.view_hash,algorithm_version:receipt.algorithm_version};
 if(await digest({kind:'report_full_provisional_save',...scope,body})!==receipt.command_hash)bad();
 return receipt;
}

export async function checkList(result,account,row,stage){
 const scope=provisionalScope(account,row,stage);
 if(result?.schema!=='report-full-provisional-list-v1'||Object.entries(scope).some(([k,v])=>result[k]!==v)||
    !Array.isArray(result.items)||result.items.length>10)bad();
 const seen=new Set();
 for(const item of result.items){
  if(!UUID.test(item?.id||'')||seen.has(item.id)||item.status!=='needs_review'||item.confirmed!==false)bad();
  seen.add(item.id);await checkReceipt(item.receipt,account,row,stage);
 }
 return result.items;
}

export async function checkRecord(result,account,row,stage,expectedReceipt=null){
 if(result?.schema!=='report-full-provisional-v1'||!UUID.test(result.id||'')||result.status!=='needs_review'||
    result.confirmed!==false||result.can_apply!==false||result.can_publish!==false||
    !result.candidate||result.candidate.schema!=='report-business-candidate-v3'||!Array.isArray(result.quarantine))bad();
 await checkReceipt(result.receipt,account,row,stage);
 if(expectedReceipt&&!same(result.receipt,expectedReceipt))bad();
 if(await digest(result.candidate)!==result.receipt.candidate_hash||await digest(result.quarantine)!==result.receipt.quarantine_hash)bad();
 return result;
}

export const provisionalKey=(account,row,stage)=>'ga-full-provisional:'+account.workspace_id+':'+account.user_id+':'+row.id+':'+stage;
