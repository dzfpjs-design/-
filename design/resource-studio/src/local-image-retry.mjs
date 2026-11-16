import {manualCanonical,manualHash} from './manual-registration-contract.mjs';

const schema='local-image-retry-journal-v1';
const plain=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const same=(a,b)=>manualCanonical(a)===manualCanonical(b);
const message='原处理重试记录无法核对，请保留原请求并读取服务器回执';
export const localImageRetryScope=(account,instanceId,row)=>({workspace_id:account.workspace_id,user_id:account.user_id,instance_id:instanceId,observation_id:row.id,entity_id:row.entity_id});
export const localImageRetryKey=scope=>'ga-local-image-retry:'+['workspace_id','user_id','instance_id','observation_id'].map(key=>encodeURIComponent(scope[key])).join(':');
function validPending(pending){
 const body=pending?.body;
 return plain(pending)&&typeof pending.target_release_id==='string'&&!!pending.target_release_id&&hash(pending.body_hash)
  &&plain(body)&&Object.keys(body).sort().join(',')==='expected_version,reason,request_id'
  &&typeof body.request_id==='string'&&/^[a-zA-Z0-9_-]{12,100}$/.test(body.request_id)
  &&Number.isInteger(body.expected_version)&&body.expected_version>0&&typeof body.reason==='string'&&!!body.reason.trim();
}
export function readLocalImageRetry(storage,scope){
 const raw=storage.getItem(localImageRetryKey(scope));
 const value=raw===null?{schema,scope:{...scope},pending:null,history:[]}:JSON.parse(raw);
 if(!plain(value)||value.schema!==schema||!same(value.scope,scope)||!Array.isArray(value.history)||(value.pending!==null&&!validPending(value.pending)))throw Error(message);
 return {raw,value};
}
function write(storage,scope,journal,value){
 const key=localImageRetryKey(scope);if(storage.getItem(key)!==journal.raw)throw Error('另一页面已更新原重试请求，当前记录保留，请先核对');
 const raw=JSON.stringify(value);storage.setItem(key,raw);if(storage.getItem(key)!==raw)throw Error('原重试请求未完整保存，尚未发送');
 return {raw,value};
}
export function localImageRetryIssue(row,releaseID){
 if(row?.retry_receipts_verified!==true)return '原重试回执尚未通过核对，不能发起新重试';
 if(!releaseID||row.retry_target_release_id!==releaseID)return '原处理固定于其他实例版本，不能自动改签；原图、输入与人工说明保留';
 if(row.retry_available!==true)return row.retry_reason||'当前不满足原处理重试条件，原图与回执保留';
 if(row.status!=='failed'||row.retry_limit!==3||!Number.isInteger(row.retry_attempts)||row.retry_attempts<1||row.retry_attempts>=row.retry_limit||!Number.isInteger(row.version)||row.version<1)return '原处理次数或版本尚未核实，请重新读取保存记录';
 return '';
}
export async function stageLocalImageRetry(storage,scope,journal,row,releaseID,current=()=>{}){
 if(journal.value.pending)return journal;
 const issue=localImageRetryIssue(row,releaseID);if(issue)throw Error(issue);
 if(row.id!==scope.observation_id||row.entity_id!==scope.entity_id)throw Error(message);
 const body={request_id:crypto.randomUUID(),expected_version:row.version,reason:'用户核对原件后重试本地处理'};
 const pending={body,body_hash:await manualHash(body),target_release_id:releaseID};
 current();
 return write(storage,scope,journal,{...journal.value,pending});
}
export async function localImageRetryReceipt(row,journal,scope){
 const pending=journal.value.pending;if(!pending)return null;
 if(row?.id!==scope.observation_id||row.entity_id!==scope.entity_id||row.retry_receipts_verified!==true||!Array.isArray(row.retry_requests))throw Error(message);
 if(await manualHash(pending.body)!==pending.body_hash)throw Error(message);
 const matches=row.retry_requests.filter(receipt=>receipt.request_id===pending.body.request_id);
 if(!matches.length)return null;
 const receipt=matches[0],body=pending.body;
 if(matches.length!==1||receipt.request_hash!==pending.body_hash||receipt.actor_id!==scope.user_id||receipt.original_version!==body.expected_version
  ||receipt.queued_version!==body.expected_version+1||!Number.isInteger(row.version)||row.version<receipt.queued_version
  ||!Number.isInteger(receipt.attempt)||receipt.attempt<1||receipt.attempt>3||!Number.isFinite(Date.parse(receipt.at)))throw Error(message);
 return receipt;
}
export async function settleLocalImageRetry(storage,scope,journal,row,current=()=>{}){
 const receipt=await localImageRetryReceipt(row,journal,scope);if(!receipt)throw Error('尚未读到匹配的原重试回执，原编号与条件继续保留');
 const history=[...journal.value.history,{pending:journal.value.pending,receipt}];
 current();
 return write(storage,scope,journal,{...journal.value,pending:null,history:history.slice(-20)});
}
export function requireOriginalLocalImageRetry(row,journal,releaseID){
 const pending=journal.value.pending;if(!validPending(pending))throw Error(message);
 if(pending.target_release_id!==releaseID)throw Error('原重试固定于原实例版本；目前只能核对原回执，不能自动改签提交');
 const issue=localImageRetryIssue(row,releaseID);if(issue)throw Error(issue);
 if(row.version!==pending.body.expected_version)throw Error('服务器处理版本已变化且未找到原回执，原重试请求保留，请核对历史记录');
}
