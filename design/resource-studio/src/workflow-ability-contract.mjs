const copy=value=>JSON.parse(JSON.stringify(value));
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function abilityScope(account,task,step){
 const scope={workspace_id:account.workspace_id,user_id:account.user_id,username:account.username,task_id:task.id,step_id:step.id,visit:task.workflow_runtime.receipts.length};
 if(Object.entries(scope).some(([key,value])=>key==='visit'?!Number.isInteger(value)||value<0:typeof value!=='string'||!value))throw Error('能力请求的账号、任务或步骤尚未核实');
 return scope;
}
export const abilityKey=scope=>'ga-workflow-ability-v2:'+JSON.stringify(scope);
function checkBody(body,scope){
 if(!body||Object.keys(body).sort().join(',')!=='request_id,retry,step_id,version'||!Number.isInteger(body.version)||body.version<1||body.step_id!==scope.step_id||!uuid.test(body.request_id)||typeof body.retry!=='boolean')throw Error('原能力请求无法完整恢复，请保留原记录');
 return body;
}
function checked(value,scope){
 if(value?.schema!==1||!same(value.scope,scope)||!Array.isArray(value.history)||value.history.length>500)throw Error('原能力请求或账号范围无法核对，请保留原记录');
 if(value.pending)checkBody(value.pending.body,scope);
 for(const item of value.history){checkBody(item.body,scope);if(!['saved','version_superseded'].includes(item.status)||!Number.isInteger(item.minimum_version)||item.minimum_version<=item.body.version)throw Error('原能力请求历史无法核对');}
 return value;
}
export function readAbility(storage,scope){const raw=storage.getItem(abilityKey(scope));return checked(raw===null?{schema:1,scope,pending:null,history:[]}:JSON.parse(raw),scope);}
export function writeAbility(storage,scope,next,expected){
 const old=readAbility(storage,scope);next=checked(copy(next),scope);
 if(expected&&!same(old,expected))throw Error('本机原请求已变化，请重新读取，当前请求保留');
 if(!same(next.history.slice(0,old.history.length),old.history))throw Error('原能力请求历史不能改写');
 if(old.pending&&next.pending?.body.request_id===old.pending.body.request_id&&!same(old.pending,next.pending))throw Error('同一能力请求不能更换内容');
 if(old.pending&&!same(old.pending,next.pending)&&!next.history.some(item=>same(item.body,old.pending.body)))throw Error('原能力请求尚未保留，已阻止替换');
 const encoded=JSON.stringify(next);storage.setItem(abilityKey(scope),encoded);
 if(storage.getItem(abilityKey(scope))!==encoded)throw Error('能力恢复记录未完整读回，已阻止发送');
 return readAbility(storage,scope);
}
// The existing server hash excludes version. The full wire body above is still frozen locally.
export async function abilityHash(body){
 const value={request_id:body.request_id,retry:body.retry,step_id:body.step_id};
 const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(value)));
 return Array.from(new Uint8Array(bytes),byte=>byte.toString(16).padStart(2,'0')).join('');
}
export async function checkedAbilityReceipt(receipt,pending,scope){
 const body=checkBody(pending.body,scope);
 if(!receipt||!['step_id','visit','result_version','current_step_id'].every(key=>Object.hasOwn(receipt,key))||typeof receipt.task_status!=='string'||!(receipt.current_step_id===null||typeof receipt.current_step_id==='string')||receipt.request_id!==body.request_id||receipt.task_id!==scope.task_id||receipt.actor_id!==scope.user_id||typeof receipt.saved!=='boolean'||!Number.isInteger(receipt.task_version)||receipt.task_version<1||!Number.isInteger(receipt.current_visit)||receipt.current_visit<0)throw Error('原能力请求回执身份不匹配，原记录保留');
 if(receipt.saved){
  const legacy=receipt.step_id===null&&receipt.visit===null&&receipt.result_version===null;
  if(receipt.status!=='saved'||receipt.request_hash!==await abilityHash(body)||!legacy&&(receipt.step_id!==scope.step_id||receipt.visit!==scope.visit||!Number.isInteger(receipt.result_version)||receipt.result_version<=body.version))throw Error('原能力请求保存内容无法核对，不能认定本次执行已保存');
 }else if(receipt.status!=='unknown'||receipt.request_hash!==null||receipt.step_id!==null||receipt.visit!==null||receipt.result_version!==null)throw Error('原能力请求送达状态无法核对');
 return receipt;
}
export const sameAbilityVisit=(receipt,scope)=>receipt.current_step_id===scope.step_id&&receipt.current_visit===scope.visit;
export function abilityTaskScope(account,taskID){
 const scope={workspace_id:account.workspace_id,user_id:account.user_id,username:account.username,task_id:taskID};
 if(Object.values(scope).some(value=>typeof value!=='string'||!value))throw Error('原能力请求的账号和任务尚未核实');
 return scope;
}
export function readTaskAbilities(storage,taskScope){
 if(!Number.isInteger(storage.length)||typeof storage.key!=='function')throw Error('本机原步骤请求清单无法读取，原记录保留');
 const prefix='ga-workflow-ability-v2:',keys=[];
 for(let index=0;index<storage.length;index++){const key=storage.key(index);if(typeof key==='string'&&key.startsWith(prefix))keys.push(key);}
 const records=[];
 for(const key of [...new Set(keys)].sort()){
  let scope;try{scope=JSON.parse(key.slice(prefix.length));}catch{continue;}
  if(!scope||Object.entries(taskScope).some(([name,value])=>scope[name]!==value))continue;
  if(typeof scope.step_id!=='string'||!scope.step_id||!Number.isInteger(scope.visit)||scope.visit<0||key!==abilityKey(scope))throw Error('本机原步骤请求范围无法核对，原记录保留');
  const journal=readAbility(storage,scope);if(journal.pending||journal.history.length)records.push({scope,journal});
 }
 return records;
}
