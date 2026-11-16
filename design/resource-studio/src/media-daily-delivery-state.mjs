const SHA=/^[a-f0-9]{64}$/,UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b),need=ok=>{if(!ok)throw Error('原每日接收请求或回执无法核对；原编号与内容保留。');};
export function checkedDailyRetry(value,scope){
 need(value&&value.schema==='media-daily-retry-outbox-v203'&&same(value.scope,scope)&&UUID.test(value.event_id)&&typeof value.raw==='string'&&SHA.test(value.sha256)&&typeof value.rejected==='boolean');
 const body=JSON.parse(value.raw);need(same(body,value.body)&&UUID.test(body.request_id)&&Number.isSafeInteger(body.expected_version)&&body.expected_version>=1&&SHA.test(body.payload_hash)&&typeof body.reason==='string'&&/\S/u.test(body.reason)&&body.reason.length<=1000);
 need(Object.keys(body).sort().join(',')==='expected_version,payload_hash,reason,request_id');return value;
}
export function checkedDailyRetryRecovery(value,pending){
 need(value?.schema==='media-daily-retry-request-v203'&&typeof value.found==='boolean');
 if(!value.found){need(value.body===null&&value.result===null);return null;}
 // Object order is not authority. Compare the closed typed command fields.
 need(value.body&&Object.keys(value.body).sort().join(',')===Object.keys(pending.body).sort().join(',')&&Object.keys(pending.body).every(key=>value.body[key]===pending.body[key]));
 const result=value.result;need(result&&result.event_id===pending.event_id&&result.payload_hash===pending.body.payload_hash&&Number.isSafeInteger(result.version)&&result.version>pending.body.expected_version&&['pending_delivery','blocked_delivery','received'].includes(result.status)&&result.cloud_calls===0&&result.physical_state_verified===false);
 return result;
}
