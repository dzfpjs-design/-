import {stableStringify} from './content-signature.js';

const hash=/^[a-f0-9]{64}$/,uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const object=value=>value&&typeof value==='object'&&!Array.isArray(value),positive=value=>Number.isSafeInteger(value)&&value>0;
const ids=value=>Array.isArray(value)&&value.every(id=>typeof id==='string')&&new Set(value).size===value.length;
const fail=()=>{throw Error('原应用恢复记录无法核对，原请求保留，暂不提交新的应用。');};
export const applyJournalKey=account=>account?.workspace_id&&account?.user_id?'ga-setup-understanding-apply:'+encodeURIComponent(account.workspace_id)+':'+encodeURIComponent(account.user_id):null;
export const applyScope=(account,analysisID)=>({workspace_id:account?.workspace_id,actor_id:account?.user_id,analysis_id:analysisID});
export function normalizedApplyBody(body){
 if(!object(body)||!positive(body.version)||!positive(body.revision)||!uuid.test(body.request_id)||!hash.test(body.result_hash)||!hash.test(body.configuration_hash)||!ids(body.names??[])||!ids(body.object_names??[])||typeof (body.accept_domain??false)!=='boolean')fail();
 return {version:body.version,revision:body.revision,names:[...(body.names??[])],object_names:[...(body.object_names??[])],accept_domain:body.accept_domain??false,request_id:body.request_id.toLowerCase(),result_hash:body.result_hash,configuration_hash:body.configuration_hash};
}
export async function digestApplyText(value){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value));return Array.from(new Uint8Array(bytes),n=>n.toString(16).padStart(2,'0')).join('');}
export const digestApplyValue=value=>digestApplyText(stableStringify(value));
export async function makeApplyPending(account,record,choice,{revision,request_id,workspace}){
 const scope=applyScope(account,record?.id);if(Object.values(scope).some(value=>typeof value!=='string'||!value)||![1,2,3,4,5].includes(record.contract_version??1)||record.status!=='review'||record.apply_basis?.revision!==revision)fail();
 const body=normalizedApplyBody({version:record.version,revision,...choice,request_id,result_hash:record.result_hash,configuration_hash:record.apply_basis.configuration_hash});
 return {schema:'setup-understanding-apply-pending-v1',scope,body,command_hash:await digestApplyValue({scope,body}),local_workspace_hash:await digestApplyValue(workspace)};
}
export function validateApplyPending(pending,account){
 if(!object(pending)||pending.schema!=='setup-understanding-apply-pending-v1'||!object(pending.scope)||Object.keys(pending.scope).sort().join(',')!=='actor_id,analysis_id,workspace_id'||pending.scope.workspace_id!==account?.workspace_id||pending.scope.actor_id!==account?.user_id||typeof pending.scope.analysis_id!=='string'||!pending.scope.analysis_id||!hash.test(pending.command_hash)||!hash.test(pending.local_workspace_hash))fail();
 const body=normalizedApplyBody(pending.body);if(stableStringify(body)!==stableStringify(pending.body))fail();
 if(pending.confirmed_receipt!==undefined&&!object(pending.confirmed_receipt))fail();
 return pending;
}
export function readApplyPending(storage,key,account){if(!key)return null;let raw;try{raw=storage.getItem(key);}catch{fail();}if(raw===null)return null;let pending;try{pending=JSON.parse(raw);}catch{fail();}return validateApplyPending(pending,account);}
export const sameApplyPending=(left,right)=>!!left&&!!right&&stableStringify({scope:left.scope,body:left.body,command_hash:left.command_hash,local_workspace_hash:left.local_workspace_hash})===stableStringify({scope:right.scope,body:right.body,command_hash:right.command_hash,local_workspace_hash:right.local_workspace_hash});
export function writeApplyPending(storage,key,pending,account){
 if(!key)fail();validateApplyPending(pending,account);const current=readApplyPending(storage,key,account);if(current&&!sameApplyPending(current,pending))throw Error('另一页面已有原应用待核对，两份输入保留，未发送新的应用。');
 storage.setItem(key,JSON.stringify(pending));const saved=readApplyPending(storage,key,account);if(!sameApplyPending(saved,pending)||stableStringify(saved.confirmed_receipt)!==stableStringify(pending.confirmed_receipt))fail();return saved;
}
export async function verifyApplyReceipt(reply,pending,{checkConfiguration=true}={}){
 const receipt=reply?.receipt,scope=pending.scope,body=pending.body;
 if(!object(receipt)||reply.state!=='saved'||receipt.schema!=='setup-understanding-apply-v1'||receipt.status!=='applied'||receipt.request_id!==body.request_id||receipt.command_hash!==pending.command_hash||await digestApplyValue({scope,body})!==pending.command_hash||Object.entries(scope).some(([key,value])=>receipt[key]!==value)||receipt.original_version!==body.version||receipt.original_revision!==body.revision||receipt.configuration_hash!==body.configuration_hash||receipt.result_hash!==body.result_hash||!positive(receipt.applied_revision)||!hash.test(receipt.applied_configuration_hash)||typeof receipt.draft_id!=='string'||!receipt.draft_id||!hash.test(receipt.receipt_hash)||reply.analysis?.id!==scope.analysis_id||reply.analysis.result_hash!==body.result_hash||typeof reply.current_matches!=='boolean')fail();
 const {receipt_hash,...value}=receipt;if(await digestApplyValue(value)!==receipt_hash)fail();
 if(reply.current_matches&&(reply.revision!==receipt.applied_revision||reply.current_revision!==receipt.applied_revision||reply.analysis.status!=='applied'))fail();
 if(reply.current_matches&&checkConfiguration){
  if(reply.configuration_encoding!=='python-json-sha256-v1'||typeof reply.configuration_json!=='string'||await digestApplyText(reply.configuration_json)!==receipt.applied_configuration_hash)fail();
  let configuration;try{configuration=JSON.parse(reply.configuration_json);}catch{fail();}if(!object(configuration))fail();reply.configuration=configuration;
 }
 return receipt;
}
export function finishApplyPending(storage,key,pending,account){
 const current=readApplyPending(storage,key,account);if(!sameApplyPending(current,pending)||!current.confirmed_receipt)fail();
 const backupKey=key+':saved:'+pending.body.request_id,encoded=JSON.stringify(current);storage.setItem(backupKey,encoded);if(storage.getItem(backupKey)!==encoded)throw Error('成功回执的本机备份读回失败，原请求保留，请重试核对。');
 if(!sameApplyPending(readApplyPending(storage,key,account),pending))fail();storage.removeItem(key);
 if(storage.getItem(key)!==null)fail();
}
export function archiveUnresolvedApply(storage,key,pending,account){
 const current=readApplyPending(storage,key,account);if(!sameApplyPending(current,pending)||current.confirmed_receipt)fail();
 const archiveKey=key+':ended:'+pending.body.request_id,archive={schema:'setup-understanding-apply-ended-v1',pending:current};
 storage.setItem(archiveKey,JSON.stringify(archive));if(storage.getItem(archiveKey)!==JSON.stringify(archive))throw Error('原应用请求备份读回失败，仍保留等待中的原请求。');
 const pointer={archive_key:archiveKey,analysis_id:pending.scope.analysis_id,request_id:pending.body.request_id};storage.setItem(key+':ended-latest',JSON.stringify(pointer));if(storage.getItem(key+':ended-latest')!==JSON.stringify(pointer))throw Error('恢复历史索引保存失败，原请求仍保留。');
 const latest=readApplyPending(storage,key,account);if(!sameApplyPending(latest,pending)||latest.confirmed_receipt)fail();storage.removeItem(key);if(storage.getItem(key)!==null)fail();return pointer;
}
export function readEndedApply(storage,key,account){
 if(!key)return null;const raw=storage.getItem(key+':ended-latest');if(raw===null)return null;
 let pointer,archive;try{pointer=JSON.parse(raw);if(pointer.archive_key!==key+':ended:'+pointer.request_id)fail();archive=JSON.parse(storage.getItem(pointer.archive_key));}catch{fail();}
 if(archive?.schema!=='setup-understanding-apply-ended-v1')fail();const pending=validateApplyPending(archive.pending,account);if(pointer.analysis_id!==pending.scope.analysis_id||pointer.request_id!==pending.body.request_id)fail();return pointer;
}
