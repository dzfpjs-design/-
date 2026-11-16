import {needCanonical,needCopy,needUUID,needHash,needSHA,needAssert as need,checkedNeedOriginal,checkedNeedBody,needMatchesOriginal} from './workspace-need-v246-protocol.mjs';

const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const exact=(value,keys)=>object(value)&&Object.keys(value).sort().join('|')===[...keys].sort().join('|');
const nonce=value=>typeof value==='string'&&/^[a-zA-Z0-9_-]{12,100}$/.test(value);
const business=value=>typeof value==='string'&&value.length>0&&Array.from(value).length<=300;

export function needRequestLookupPath(scope,original){
 checkedNeedOriginal(original,scope);
 const query=new URLSearchParams({request_id:original.body.request_id,expected_body_hash:original.body_hash});
 return '/workspace/instances/'+scope.instance_id+'/data-need-requests?'+query.toString();
}

export async function checkedNeedRequestRecovery(value,scope,original,crypto=globalThis.crypto){
 checkedNeedOriginal(original,scope);
 need(await needSHA(original.raw,crypto)===original.sha256&&await needSHA(needCanonical(original.body),crypto)===original.body_hash,'本机原申请正文或摘要不同；原输入保留，未重新提交。');
 need(exact(value,['schema','instance_id','request_id','body_hash','found','request','query_path','read_only','device_actions','cloud_calls'])&&value.schema==='workspace-data-need-request-recovery-v248'&&value.instance_id===scope.instance_id&&nonce(value.request_id)&&value.request_id===original.body.request_id&&value.body_hash===original.body_hash&&typeof value.found==='boolean'&&value.read_only===true&&value.device_actions===false&&value.cloud_calls===0,'服务器返回的原需求保存记录与当前账号范围或原条件不一致；原申请保留。');
 if(!value.found){need(value.request===null&&value.query_path===null);return needCopy(value);}
 const proof=value.request,body=original.body;
 need(exact(proof,['query_id','query_version','actor_id','instance_id','release_id','business_id','entity_id','request_id','body_hash','context_hash','query_request_hash','evidence_level','typed_body','receipt_hash','creation_audit_id','initial_job_id','typed_receipt_audit_id'])&&needUUID(proof.query_id)&&Number.isSafeInteger(proof.query_version)&&proof.query_version>=1&&proof.actor_id===scope.actor_id&&proof.instance_id===scope.instance_id&&needUUID(proof.release_id)&&business(proof.business_id)&&needUUID(proof.entity_id)&&proof.request_id===body.request_id&&proof.body_hash===original.body_hash&&needHash(proof.context_hash)&&needHash(proof.query_request_hash)&&needUUID(proof.creation_audit_id)&&needUUID(proof.initial_job_id));
 need(proof.release_id===body.release_id&&proof.business_id===body.business_id&&proof.entity_id===body.entity_id,'保存记录中的原业务、对象或发布范围不同；没有替换原申请。');
 need(value.query_path==='/api/workspace/instances/'+scope.instance_id+'/data-needs/'+proof.query_id,'返回的原需求详情地址不属于这次请求；未打开其他记录。');
 if(proof.evidence_level==='typed_body_receipt'){
  checkedNeedBody(proof.typed_body);
  need(needHash(proof.receipt_hash)&&needUUID(proof.typed_receipt_audit_id)&&needCanonical(proof.typed_body)===needCanonical(body)&&await needSHA(needCanonical(proof.typed_body),crypto)===original.body_hash,'保存回执中的完整条件与本机原申请不同；原文保留。');
 }else need(proof.evidence_level==='legacy_job_context'&&proof.typed_body===null&&proof.receipt_hash===null&&proof.typed_receipt_audit_id===null,'旧版需求不能冒充完整提交回执；原申请保留。');
 return needCopy(value);
}

export function needRecoveryMatchesView(proof,view,original){
 needMatchesOriginal(view,original);const source=view.copy_descriptor.source;
 need(view.id===proof.query_id&&view.version>=proof.query_version&&source.context_hash===proof.context_hash&&source.request_hash===proof.query_request_hash&&source.release_id===proof.release_id&&source.business_id===proof.business_id&&source.entity_id===proof.entity_id,'原需求详情与刚找回的保存记录不同；原申请保留，请再次只读核对。');
 return true;
}
