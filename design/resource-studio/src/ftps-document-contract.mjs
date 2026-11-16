import {stableStringify} from './content-signature.js';

// A connection only names an operator-approved grant. No password, certificate,
// address or remote file contents belong in the browser recovery journal.
const string=value=>typeof value==='string'&&value.length>0&&value.length<=250;
const version=value=>Number.isSafeInteger(value)&&value>=1;
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
const sha=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const schemas={
 connect:['request_id','name','grant_id'],
 listing:['request_id','version','connection_id'],
 import:['request_id','version','listing_id','file_id','connection_id'],
};
const fail=()=>{throw Error('原请求或服务器回执无法核对，记录已保留；请重新读取或联系管理员。');};
export function checkFtpsAttempt(value){
 if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==2||!Object.hasOwn(schemas,value.kind)||!value.submission||typeof value.submission!=='object'||Array.isArray(value.submission))fail();
 const fields=schemas[value.kind],s=value.submission;
 if(Object.keys(s).length!==fields.length||!fields.every(k=>Object.hasOwn(s,k))||!uuid(s.request_id))fail();
 if(value.kind==='connect'&&(!string(s.name)||s.name.length>100||s.name!==s.name.trim()||/[\u0000-\u001f\u007f]/.test(s.name)||!sha(s.grant_id)))fail();
 if(value.kind!=='connect'&&(!uuid(s.connection_id)||!version(s.version)))fail();
 if(value.kind==='import'&&(!uuid(s.listing_id)||!sha(s.file_id)))fail();
 return value;
}
export function verifyFtpsReceipt(receipt,attempt){
 checkFtpsAttempt(attempt);
 if(!receipt||typeof receipt!=='object'||receipt.request_id!==attempt.submission.request_id||receipt.kind!==attempt.kind||stableStringify(receipt.submission)!==stableStringify(attempt.submission)||!['pending','succeeded','failed','cancelled'].includes(receipt.state))fail();
 if(receipt.state==='succeeded'){
  const r=receipt.result;
  if(!r||typeof r!=='object'||Array.isArray(r))fail();
  if(attempt.kind==='connect'&&(!uuid(r.id)||r.name!==attempt.submission.name||r.grant_id!==attempt.submission.grant_id||!version(r.version)||r.status!=='active'))fail();
  if(attempt.kind==='listing'&&(!uuid(r.listing_id)||!Array.isArray(r.files)||r.files.length>1000||r.files.some(x=>!x||!sha(x.id)||!string(x.name)||!Number.isSafeInteger(x.size)||x.size<=0||typeof x.modified!=='string'||!/^\d{14}(?:\.\d{1,6})?$/.test(x.modified))))fail();
  if(attempt.kind==='import'){
   const s=r.source;
   if(!uuid(r.material_id)||!string(r.name)||typeof r.status!=='string'||!s||s.connection_id!==attempt.submission.connection_id||s.listing_id!==attempt.submission.listing_id||s.file_id!==attempt.submission.file_id||s.name!==r.name||!Number.isSafeInteger(s.size)||s.size<=0||typeof s.modified!=='string'||!/^\d{14}(?:\.\d{1,6})?$/.test(s.modified)||!sha(s.sha256))fail();
  }
 }
 return receipt;
}
export function ftpsBody(attempt){
 checkFtpsAttempt(attempt);
 const {connection_id,...body}=attempt.submission;
 return attempt.kind==='connect'?attempt.submission:body;
}
export function ftpsPath(attempt){
 checkFtpsAttempt(attempt);
 if(attempt.kind==='connect')return '/ftps/connections';
 const path='/ftps/connections/'+encodeURIComponent(attempt.submission.connection_id);
 return path+(attempt.kind==='listing'?'/listings':'/imports');
}
