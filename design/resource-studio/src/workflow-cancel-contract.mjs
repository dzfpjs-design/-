const copy=value=>JSON.parse(JSON.stringify(value));
const canonical=value=>JSON.stringify(Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b))));
export function cancellationScope(account,taskID){
 const scope={workspace_id:account.workspace_id,user_id:account.user_id,username:account.username,task_id:taskID};
 if(Object.values(scope).some(value=>typeof value!=='string'||!value))throw Error('取消请求的账号和任务尚未核实');
 return scope;
}
export const cancellationKey=scope=>'ga-workflow-cancellation:'+JSON.stringify(scope);
function checkBody(body){
 if(!body||Object.keys(body).sort().join(',')!=='reason,request_id,retained_note,version'||!Number.isInteger(body.version)||body.version<1||typeof body.reason!=='string'||!body.reason.trim()||body.reason.length>1000||typeof body.retained_note!=='string'||body.retained_note.length>5000||!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(body.request_id))throw Error('原取消请求无法完整恢复，请保留原记录');
 return body;
}
function checkedJournal(value,scope){
 if(value.schema!==1||JSON.stringify(value.scope)!==JSON.stringify(scope)||typeof value.reason!=='string'||value.reason.length>1000||!Array.isArray(value.history))throw Error('原取消草稿或账号范围无法核对，请保留原记录');
 if(value.pending)checkBody(value.pending.body);
 for(const item of value.history)checkBody(item.body);
 return value;
}
export function readCancellation(storage,scope){
 const raw=storage.getItem(cancellationKey(scope));return checkedJournal(raw===null?{schema:1,scope,reason:'',pending:null,history:[]}:JSON.parse(raw),scope);
}
export function writeCancellation(storage,scope,value){
 const old=readCancellation(storage,scope),next=checkedJournal(copy(value),scope);
 if(old.pending&&next.pending?.body.request_id===old.pending.body.request_id&&JSON.stringify(next.pending.body)!==JSON.stringify(old.pending.body))throw Error('同一取消请求不能更换内容');
 if(old.pending&&JSON.stringify(old.pending)!==JSON.stringify(next.pending)&&!next.history.some(item=>JSON.stringify(item.body)===JSON.stringify(old.pending.body)))throw Error('原取消请求尚未保留，已阻止替换');
 const encoded=JSON.stringify(next);storage.setItem(cancellationKey(scope),encoded);
 if(storage.getItem(cancellationKey(scope))!==encoded)throw Error('取消恢复记录未完整读回，已阻止发送');
 return readCancellation(storage,scope);
}
export async function cancellationHash(body){
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(checkBody(body))));
 return Array.from(new Uint8Array(bytes),byte=>byte.toString(16).padStart(2,'0')).join('');
}
export async function checkedCancellationReceipt(receipt,pending,scope){
 const body=checkBody(pending.body);
 if(receipt?.request_id!==body.request_id||receipt.task_id!==scope.task_id||typeof receipt.saved!=='boolean'||!Number.isInteger(receipt.version))throw Error('原取消请求回执身份不匹配，原记录保留');
 if(receipt.saved){
  const saved=receipt.cancellation;
  if(receipt.status!=='saved'||receipt.request_hash!==await cancellationHash(body)||receipt.version<=body.version||saved?.actor!==scope.user_id||saved.reason!==body.reason.trim()||saved.retained_note!==body.retained_note)throw Error('原取消请求的保存内容无法核对，不能认定本次取消成功');
 }
 return receipt;
}
