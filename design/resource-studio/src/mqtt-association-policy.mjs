import {receiptStatus} from './mqtt-ui-policy.mjs';
// Display/form policy only. The server checks source authority and frozen business contracts.
const canonical=value=>JSON.stringify(value,(_key,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
export function associationKey(sourceID,routeID='new'){return sourceID+':route:'+routeID;}
export function newAssociationDraft(gateway,old={}){return {
 requestId:crypto.randomUUID(),baseVersion:null,baseHash:old.baseHash||gateway.config_hash,topic:old.topic||'',
 entity_id:old.entity_id||'',extractor_id:old.extractor_id||'',field_links:{...old.field_links},include_existing:false,
 ...(Object.hasOwn(old,'instance_release_id')?{instance_release_id:old.instance_release_id}:{})
};}
export function savedAssociationDraft(gateway,row){return {...newAssociationDraft(gateway),
 baseVersion:row.version,baseHash:row.config_hash,topic:row.topic,entity_id:row.entity_id,
 extractor_id:row.extractor_id,field_links:{...row.field_links},...(row.instance?.release_id?{instance_release_id:row.instance.release_id}:{})};}
export function associationDestination(object){
 if(!object)return {available:false,reason:'业务目的地尚未读取，请保留草稿并重新核对对象。',instance_release_id:null};
 if(object.instance===null||object.instance===undefined)return {available:true,reason:'',instance_release_id:null};
 const target=object.instance,identity=target.instance_release_id;
 if(target.current!==true||typeof identity!=='string'||!identity.trim())return {available:false,reason:'业务目的地当前不可用：'+(target.reason||'当前实例版本尚未核实，请重新读取对象。'),instance_release_id:null};
 return {available:true,reason:'',instance_release_id:identity,release_number:target.instance_release_number};
}
export function associationDestinationIssue(draft,object){
 const target=associationDestination(object);if(!target.available)return target.reason;
 return (draft?.instance_release_id||null)!==target.instance_release_id?'业务目的地尚未按当前实例版本明确确认。原字段、规则与输入保留，请核对并采用当前业务目的地后再保存。':'';
}
export function associationDefinition(draft,gateway,objects,rules){
 if(draft.baseHash!==gateway.config_hash)throw Error('来源约定已变化。请对照当前来源重新编辑，原草稿保留。');
 if(!Object.hasOwn(gateway.configuration.topics,draft.topic||''))throw Error('请选择当前来源已确认的数据主题。');
 const object=objects.find(o=>o.id===draft.entity_id);
 if(!object)throw Error('请选择已有且可用的业务对象。');
 const destinationIssue=associationDestinationIssue(draft,object);if(destinationIssue)throw Error(destinationIssue);
 const measurements=Object.values(gateway.configuration.mapping),links=draft.field_links||{};
 if(Object.keys(links).length!==measurements.length||measurements.some(m=>!links[m.attribute]))throw Error('请逐项确认每个测量对应的业务字段。');
 if(new Set(Object.values(links)).size!==measurements.length)throw Error('不同测量不能覆盖同一业务字段。');
 for(const measurement of measurements){const field=object.fields.find(f=>f.key===links[measurement.attribute]);
  if(!field||field.unit!==undefined&&(field.unit||null)!==(measurement.unit||null))throw Error('业务字段或单位不一致。本页不会自动换算单位。');}
 if(!rules.some(r=>r.id===draft.extractor_id))throw Error('请选择与对象、字段和单位一致的已发布本地规则。');
 const destination=associationDestination(object);
 return {topic:draft.topic,entity_id:draft.entity_id,extractor_id:draft.extractor_id,
  ...(destination.instance_release_id?{instance_release_id:destination.instance_release_id}:{}),
  field_links:{...links},include_existing:draft.include_existing===true};
}
export function associationRequest(draft,gateway,row,objects,rules){
 if(draft.pending)return draft.pending;
 if(row&&draft.baseVersion!==row.version)throw Error('此关联已被另一页面修改。当前编辑保留，请对照服务器版本。');
 return {request_id:draft.requestId,expected_version:row?draft.baseVersion:gateway.version,...associationDefinition(draft,gateway,objects,rules)};
}
const destinationID=value=>Object.hasOwn(value,'instance_release_id')?value.instance_release_id||null:value.instance?.release_id||null;
export function sameAssociation(draft,row){return canonical({topic:draft.topic,entity_id:draft.entity_id,extractor_id:draft.extractor_id,field_links:draft.field_links||{},instance_release_id:destinationID(draft)})===canonical({topic:row.topic,entity_id:row.entity_id,extractor_id:row.extractor_id,field_links:row.field_links||{},instance_release_id:destinationID(row)});}
export function associationStatusRequest(row,status){
 if(!['active','paused'].includes(status))throw Error('请核对关联接收状态。');
 return {request_id:crypto.randomUUID(),expected_version:row.version,status,include_existing:false};
}
export function mayFinishOriginDraft(drafts,originKey,savedKey,request){return originKey!==savedKey&&drafts?.[originKey]?.pending?.request_id===request.request_id;}
export function associationPendingConfirmed(draft){return !!draft?.pending?.request_id&&draft.pending.request_id===draft.confirmedRequest?.request_id&&canonical(draft.pending)===canonical(draft.confirmedRequest);}
export const ASSOCIATION_VERSION_CONFLICT='关联版本已更新，本次请求未提交。请对照当前版本重新确认。';
export function savedAssociationTransport(path,method,request,signal){
 // Aborting this fetch stops browser waiting only; the durable nonce and exact body stay unchanged.
 if(!path||!['POST','PUT'].includes(method)||!request?.request_id)throw Error('原保存请求不完整，请保留草稿并核对。');
 return {path,options:{method,body:JSON.stringify(request),signal}};
}
export function rejectedAssociationDraft(draft,error){
 if(error?.status!==409||error.message!==ASSOCIATION_VERSION_CONFLICT||!draft?.pending)return null;
 return {...draft,requestId:crypto.randomUUID(),pending:null,pendingPath:null,pendingMethod:null,pendingOriginKey:null,
  rejectedRequests:[...(draft.rejectedRequests||[]).slice(-9),{request:draft.pending,path:draft.pendingPath||null,method:draft.pendingMethod||null,
   reason:ASSOCIATION_VERSION_CONFLICT,rejected_at:new Date().toISOString()}]};
}
export function associationHeading(row){return [row.instance_name||'独立业务对象',row.entity_label||'已登记对象'].join(' · ');}
export function associationState(row){if(row.needs_review)return row.issue||'来源约定或业务目的地已变化，需重新核对';return row.status==='paused'?'此关联已暂停':row.legacy?'原有对象关联':'以后收到的数据使用此关联';}
export function associationDeliveryState(row){
 if(row.processing?.status==='processed')return '已提取候选信息';
 if(row.processing?.status==='failed'||row.submission?.status==='failed'||row.status==='failed')return '处理未完成，原件保留';
 if(['blocked','blocked_delivery','stopped','superseded','invalidated'].includes(row.status)||row.current===false||row.needs_review)return '当前关联不可继续处理，原记录保留';
 if(row.observation_id||row.submission||['submitted','pending','processing'].includes(row.status))return '等待后台处理';
 return '已保存此关联的处理记录';
}
export function mqttReceiptSummary(row){
 if(row.status==='quarantined')return receiptStatus(row);
 if(Array.isArray(row.associations)&&row.associations.length)return '原件已保存 · '+row.associations.length+' 条对象处理记录';
 return receiptStatus(row);
}
export function retryAssociationDelivery(row){return row.can_submit===true&&Number.isInteger(row.version)&&row.version>0&&row.current!==false&&!row.needs_review&&row.status!=='invalidated'&&row.status!=='processed'&&row.processing?.status!=='processed'&&row.processing?.status!=='pending'&&row.submission?.status!=='pending';}
