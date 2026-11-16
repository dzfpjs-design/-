const hash=/^[0-9a-f]{64}$/i,uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const actions=['review','apply','undo'],positive=value=>Number.isInteger(value)&&value>0;
const ids=value=>Array.isArray(value)&&value.every(id=>typeof id==='string'&&id.length>0)&&new Set(value).size===value.length;
const sameIDs=(a,b)=>ids(a)&&ids(b)&&a.length===b.length&&a.every((id,i)=>id===b[i]);
const fail=()=>{throw Error('字段修改记录或原请求无法核对，已保存内容保留，暂不提交。');};
const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(key=>Object.hasOwn(value,key));
export const isEditRecord=record=>record?.contract_version===6&&record.edit_mode===true;
export function editReview(record){
 if(!isEditRecord(record)||!hash.test(record.result_hash)||!Array.isArray(record.edit_review))fail();
 const rows=record.edit_review;
 if(!ids(rows.map(row=>row?.id)))fail();
 for(const row of rows){
  if(!['type','target_label','field_label','inference_reason'].every(key=>typeof row[key]==='string')||!Object.hasOwn(row,'before')||!Object.hasOwn(row,'after'))fail();
  if(!['effects','dependencies','blocked_reasons'].every(key=>Array.isArray(row[key])&&row[key].every(value=>typeof value==='string'))||!ids(row.dependencies)||!Array.isArray(row.citations))fail();
 }
 return rows;
}
export function selectedEditOperations(record){
 const choice=record.review_selection;
 if(!choice)return [];
 if(choice.result_hash!==record.result_hash||!ids(choice.operation_ids))fail();
 const available=new Set(editReview(record).map(row=>row.id));if(choice.operation_ids.some(id=>!available.has(id)))fail();
 return [...choice.operation_ids];
}
export function editSelectionProblems(record,selection){
 const rows=editReview(record),available=new Map(rows.map(row=>[row.id,row]));
 if(!ids(selection)||selection.some(id=>!available.has(id)))return ['选择已变化，请重新核对服务器保存的修改建议。'];
 const problems=[];
 for(const id of selection){const row=available.get(id);problems.push(...row.blocked_reasons.map(reason=>`${row.target_label}：${reason}`));
  for(const dependency of row.dependencies)if(!selection.includes(dependency))problems.push(`${row.target_label}依赖“${available.get(dependency)?.target_label||'另一项关联修改'}”，请逐项核对并选择；不会自动勾选。`);
 }
 return problems;
}
export function makeEditPending(action,record,selection,{revision,request_id}){
 if(!actions.includes(action)||!isEditRecord(record)||!positive(record.version)||!hash.test(record.result_hash)||!uuid.test(request_id))fail();
 if(record.edit_receipts?.some(receipt=>receipt.request_id===request_id))fail();
 if(action==='undo'){if(record.status!=='applied'||record.edit_noop||!positive(revision)||!ids(record.applied_operation_ids)||!record.applied_operation_ids.length)fail();}
 else{
  if(record.status!=='review'||!ids(selection)||selection.some(id=>!editReview(record).some(row=>row.id===id)))fail();
  if(action==='apply'&&(!positive(revision)||!selection.length||editSelectionProblems(record,selection).length||!sameIDs(selection,selectedEditOperations(record))))throw Error('请先保存审核选择并核对关联修改，再应用；原选择保留。');
 }
 const body={version:record.version,...(action!=='review'?{revision}:{}),result_hash:record.result_hash,...(action!=='undo'?{operation_ids:[...selection]}:{}),request_id};
 return validateEditPending({schema:'setup-edits-request-v1',record_id:record.id,action,body,expected_operation_ids:[...(action==='undo'?record.applied_operation_ids:selection)]});
}
function validateEditPending(pending){
 if(!exact(pending,['schema','record_id','action','body','expected_operation_ids'])||pending.schema!=='setup-edits-request-v1'||typeof pending.record_id!=='string'||!pending.record_id||!actions.includes(pending.action)||!ids(pending.expected_operation_ids))fail();
 const body=pending.body,action=pending.action,keys=['version',...(action!=='review'?['revision']:[]),'result_hash',...(action!=='undo'?['operation_ids']:[]),'request_id'];
 if(!exact(body,keys)||!positive(body.version)||!hash.test(body.result_hash)||!uuid.test(body.request_id)||(action!=='review'&&!positive(body.revision))||(action!=='undo'&&(!ids(body.operation_ids)||!sameIDs(body.operation_ids,pending.expected_operation_ids))))fail();
 return pending;
}
const signature=pending=>JSON.stringify([pending.schema,pending.record_id,pending.action,pending.body.version,pending.body.revision,pending.body.result_hash,pending.body.operation_ids,pending.body.request_id,pending.expected_operation_ids]);
export function editJournalKey(account,id){return account?.workspace_id&&account?.username&&id?'ga-setup-edits:'+encodeURIComponent(account.workspace_id)+':'+encodeURIComponent(account.username)+':'+encodeURIComponent(id):null;}
export function readEditPending(storage,key){
 if(!key)return null;
 try{const raw=storage.getItem(key);return raw?validateEditPending(JSON.parse(raw)):null;}catch{throw Error('本机字段修改请求暂不能读取，原记录保留；请先核对，暂不另起操作。');}
}
export function writeEditPending(storage,key,pending){
 validateEditPending(pending);if(!key)fail();const prior=readEditPending(storage,key);
 if(prior&&signature(prior)!==signature(pending))throw Error('已有字段修改操作结果尚未核对，请先恢复原请求。');
 try{storage.setItem(key,JSON.stringify(pending));const saved=readEditPending(storage,key);if(!saved||signature(saved)!==signature(pending))fail();}catch{throw Error('原操作请求尚未保存到本机，未提交服务器；请检查浏览器存储。');}
}
export function clearEditPending(storage,key,pending){
 const saved=readEditPending(storage,key);if(!saved||signature(saved)!==signature(pending))return false;
 storage.removeItem(key);return true;
}
export function editReceipt(record,pending){
 validateEditPending(pending);if(!isEditRecord(record)||record.id!==pending.record_id||!Array.isArray(record.edit_receipts))fail();
 const matches=record.edit_receipts.filter(receipt=>receipt.request_id===pending.body.request_id);
 if(matches.length>1)fail();if(!matches.length)return null;const receipt=matches[0];
 const expected=pending.action==='review'?['review','noop']:pending.action==='apply'?['applied','noop']:['undone','noop'];
 if(receipt.action!==pending.action||receipt.result_hash!==pending.body.result_hash||!positive(receipt.revision)||!expected.includes(receipt.status)||!sameIDs(receipt.operation_ids,pending.expected_operation_ids))fail();
 return receipt;
}
export function editRequestState(record,pending){
 if(editReceipt(record,pending))return 'confirmed';
 return record.result_hash===pending.body.result_hash&&record.version===pending.body.version&&record.status===(pending.action==='undo'?'applied':'review')?'retryable':'conflict';
}
const settled=['review','applied','undone','cancelled','failed'];
export function fencedEditConflict(record,pending){
 if(editReceipt(record,pending)||!positive(record.version)||record.version<=pending.body.version||!settled.includes(record.status))return null;
 editReview(record);const operation_ids=selectedEditOperations(record);
 return {record_id:record.id,old_version:pending.body.version,current_version:record.version,current_status:record.status,
  old_result_hash:pending.body.result_hash,current_result_hash:record.result_hash,original_receipt_absent:true,
  receipt_request_ids:record.edit_receipts.map(receipt=>receipt.request_id),operation_ids,result_hash:record.result_hash};
}
// Caller holds the same browser cache lock used for active request writes.
export function archiveFencedEditPending(storage,key,pending,record,account){
 const observation=fencedEditConflict(record,pending);
 if(!observation)throw Error('尚未证明原操作已失效或没有原回执，原请求保留，暂不结束重试。');
 if(!key||key!==editJournalKey(account,pending.record_id))fail();
 const active=readEditPending(storage,key);if(!active||signature(active)!==signature(pending))throw Error('另一页面的原请求已变化，不归档或清除其活动请求。');
 const retainedKey=key+':retained:'+pending.body.request_id,identity={workspace_id:account.workspace_id,username:account.username};
 let raw;
 try{raw=storage.getItem(retainedKey);
  if(raw){
   const retained=JSON.parse(raw),proof=retained.observation;
   validateEditPending(retained.original);
   if(retained.schema!=='setup-edits-retained-request-v1'||signature(retained.original)!==signature(pending)||retained.account?.workspace_id!==identity.workspace_id||retained.account?.username!==identity.username||proof?.record_id!==pending.record_id||proof.old_version!==pending.body.version||!positive(proof.current_version)||proof.current_version<=proof.old_version||!settled.includes(proof.current_status)||proof.original_receipt_absent!==true||proof.old_result_hash!==pending.body.result_hash||!hash.test(proof.current_result_hash)||!Array.isArray(proof.receipt_request_ids)||proof.receipt_request_ids.includes(pending.body.request_id))fail();
  }else{
   raw=JSON.stringify({schema:'setup-edits-retained-request-v1',account:identity,original:pending,observation,retained_at:new Date().toISOString()});
   storage.setItem(retainedKey,raw);
  }
  if(storage.getItem(retainedKey)!==raw)fail();
 }catch{throw Error('原请求完整归档未完成，活动请求保留；请检查浏览器存储后再核对。');}
 const current=readEditPending(storage,key);
 if(!current||signature(current)!==signature(pending))throw Error('另一页面保存了新的活动请求；原请求已归档，不清除新请求。');
 storage.removeItem(key);
 if(storage.getItem(key)!==null)throw Error('活动请求尚未清除，原请求归档已保留，请重新读取核对。');
 return {operation_ids:[...observation.operation_ids],result_hash:observation.result_hash};
}
