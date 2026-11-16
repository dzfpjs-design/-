import test from 'node:test';
import assert from 'node:assert/strict';
import {associationKey,newAssociationDraft,savedAssociationDraft,associationDefinition,associationRequest,associationStatusRequest,sameAssociation,mayFinishOriginDraft,associationPendingConfirmed,rejectedAssociationDraft,savedAssociationTransport,ASSOCIATION_VERSION_CONFLICT,associationHeading,associationState,associationDeliveryState,mqttReceiptSummary,retryAssociationDelivery} from '../src/mqtt-association-policy.mjs';
import {compatibleRule} from '../src/local-rule-policy.mjs';
import {mergeWorkspacePatch} from '../src/workspace-patches.mjs';
import {withoutSecrets} from '../src/catalog.js';

const gateway={id:'source-one',version:4,config_hash:'confirmed-source',configuration:{topics:{'north/data':'north-device'},mapping:{t:{attribute:'空气温度',unit:'C'},h:{attribute:'湿度',unit:'%'}}}};
const objects=[{id:'first-object',package_id:'forest-package',entity_type:'station',fields:[{key:'temperature',label:'温度',unit:'C'},{key:'humidity',label:'湿度',unit:'%'}]},{id:'other-object',package_id:'forest-package',entity_type:'station',fields:[{key:'temperature',label:'温度',unit:'C'},{key:'humidity',label:'湿度',unit:'%'}]}];
const rules=[{id:'local-one',data:{specification:{managed_numeric_rule:{package_id:'forest-package',entity_type:'station'},rules:[{field:'temperature',unit:'C'}]}}},{id:'local-two',data:{specification:{managed_numeric_rule:{package_id:'forest-package',entity_type:'station'},rules:[{field:'temperature',unit:'C'}]}}}];
const draft=()=>({...newAssociationDraft(gateway),topic:'north/data',entity_id:'first-object',extractor_id:'local-one',field_links:{'空气温度':'temperature','湿度':'humidity'}});
const row={id:'association-one',version:6,revision:2,config_hash:gateway.config_hash,topic:'north/data',entity_id:'first-object',extractor_id:'local-one',field_links:draft().field_links,status:'active',entity_label:'北站',instance_name:'林场业务',rule_name:'已确认温度规则'};

test('new associations default to future inputs and preserve old binding draft fields without inherited replay authorization',()=>{
 const d=newAssociationDraft(gateway,{...draft(),include_existing:true,requestId:'old-uuid'});
 assert.equal(d.include_existing,false);assert.notEqual(d.requestId,'old-uuid');assert.equal(d.topic,'north/data');assert.deepEqual(d.field_links,draft().field_links);
 assert.equal(associationRequest(d,gateway,null,objects,rules).include_existing,false);
 const stale=newAssociationDraft(gateway,{...draft(),baseHash:'previous-source'});
 assert.equal(stale.baseHash,'previous-source');assert.throws(()=>associationDefinition(stale,gateway,objects,rules),/来源约定已变化/);
});
test('a source can independently select different objects and local rules without creating another source connection',()=>{
 const a=associationRequest(draft(),gateway,null,objects,rules),b=associationRequest({...draft(),entity_id:'other-object',extractor_id:'local-two'},gateway,null,objects,rules);
 assert.equal(a.expected_version,gateway.version);assert.equal(b.expected_version,gateway.version);assert.equal(a.topic,b.topic);assert.notEqual(a.entity_id,b.entity_id);assert.notEqual(a.extractor_id,b.extractor_id);
 assert.equal(a.configuration,undefined);assert.notEqual(a.request_id,b.request_id);
});
test('editing uses association CAS and creates a new version rather than overwriting source settings',()=>{
 const d={...savedAssociationDraft(gateway,row),extractor_id:'local-two'},body=associationRequest(d,gateway,row,objects,rules);
 assert.equal(body.expected_version,6);assert.equal(body.request_id,d.requestId);assert.equal(body.include_existing,false);
 assert.throws(()=>associationRequest(d,gateway,{...row,version:7},objects,rules),/另一页面/);
});
test('explicit replay intent is a boolean and status resume never implicitly replays old originals',()=>{
 assert.equal(associationDefinition({...draft(),include_existing:true},gateway,objects,rules).include_existing,true);
 assert.equal(associationDefinition({...draft(),include_existing:'true'},gateway,objects,rules).include_existing,false);
 assert.equal(associationStatusRequest(row,'active').include_existing,false);
 assert.equal(associationStatusRequest(row,'active',true).include_existing,false);
 assert.equal(associationStatusRequest(row,'paused',true).include_existing,false);
 assert.throws(()=>associationStatusRequest(row,'deleted'));
});
test('only the exact server non-committed CAS rejection releases a frozen request while retaining human edits and original UUID',()=>{
 const d={...savedAssociationDraft(gateway,row),extractor_id:'local-two'},request=associationRequest(d,gateway,row,objects,rules);
 const frozen={...d,pending:request,pendingPath:'/mqtt/routes/'+row.id,pendingMethod:'PUT'};
 const rejected=rejectedAssociationDraft(frozen,{status:409,message:ASSOCIATION_VERSION_CONFLICT});
 assert.equal(rejected.pending,null);assert.equal(rejected.baseVersion,d.baseVersion);assert.equal(rejected.extractor_id,'local-two');assert.deepEqual(rejected.field_links,d.field_links);
 assert.notEqual(rejected.requestId,d.requestId);assert.deepEqual(rejected.rejectedRequests[0].request,request);assert.equal(rejected.rejectedRequests[0].path,frozen.pendingPath);
 for(const error of [{status:409,message:'其他内容冲突'},{status:500,message:ASSOCIATION_VERSION_CONFLICT},{status:422,message:'字段拒绝'},{message:'响应丢失'}])assert.equal(rejectedAssociationDraft(frozen,error),null);
 const stored=withoutSecrets({mqttBindingDrafts:{key:rejected}});assert.equal(stored.mqttBindingDrafts.key.rejectedRequests[0].request.request_id,request.request_id);
});
test('stopping browser waiting preserves the exact durable create or status request and cannot unlock an unknown result',()=>{
 const request=associationRequest(draft(),gateway,null,objects,rules),frozen={...draft(),pending:request,pendingPath:'/mqtt/gateways/'+gateway.id+'/routes',pendingMethod:'POST'};
 const controller=new AbortController(),transport=savedAssociationTransport(frozen.pendingPath,frozen.pendingMethod,frozen.pending,controller.signal);
 assert.equal(transport.options.signal,controller.signal);assert.equal(transport.options.signal.aborted,false);
 controller.abort();assert.equal(transport.options.signal.aborted,true);
 assert.equal(rejectedAssociationDraft(frozen,Object.assign(Error('已停止等待'),{name:'AbortError'})),null);
 assert.deepEqual(associationRequest(frozen,gateway,null,[],[]),request);
 const retried=savedAssociationTransport(frozen.pendingPath,frozen.pendingMethod,frozen.pending,new AbortController().signal);
 assert.equal(retried.path,transport.path);assert.equal(retried.options.body,transport.options.body);assert.equal(retried.options.signal.aborted,false);
 const status=associationStatusRequest(row,'paused'),statusTransport=savedAssociationTransport('/mqtt/routes/'+row.id+'/status','POST',status,new AbortController().signal);
 assert.deepEqual(JSON.parse(statusTransport.options.body),status);assert.equal(statusTransport.options.method,'POST');
 assert.throws(()=>savedAssociationTransport('/mqtt/routes/x','GET',request,controller.signal));
});
test('uncertain saves retry exactly the original UUID and content even after options or CAS change',()=>{
 const original=associationRequest({...draft(),include_existing:true},gateway,null,objects,rules);
 const pending=JSON.parse(JSON.stringify(original)),d={...draft(),pending,topic:'not-current',baseHash:'old',requestId:'other',include_existing:false};
 assert.deepEqual(associationRequest(d,{...gateway,version:99},row,[],[]),original);
 const persisted=withoutSecrets({mqttBindingDrafts:{[associationKey(gateway.id)]:d}});
 assert.deepEqual(persisted.mqttBindingDrafts[associationKey(gateway.id)].pending,original);
});
test('each mapping is complete and distinct with exact units and current source topic',()=>{
 const invalid=[{baseHash:'older-source'},{topic:'unknown/data'},{entity_id:'unavailable'},{extractor_id:'remote-rule'},
  {field_links:{'空气温度':'temperature'}},{field_links:{'空气温度':'temperature','湿度':'temperature'}},
  {field_links:{'空气温度':'humidity','湿度':'temperature'}},{field_links:{...draft().field_links,extra:'other'}}];
 for(const patch of invalid)assert.throws(()=>associationDefinition({...draft(),...patch},gateway,objects,rules));
 assert.equal(compatibleRule(rules[0],objects[0],draft().field_links,Object.values(gateway.configuration.mapping)),true);
 assert.equal(compatibleRule(rules[0],{...objects[0],package_id:'another-package'},draft().field_links,Object.values(gateway.configuration.mapping)),false);
});
test('association journals merge only changed route entries and leave sibling rule or source drafts intact',()=>{
 const key=associationKey(gateway.id,row.id),other=associationKey(gateway.id,'another'),baseline={mqttBindingDrafts:{[key]:{text:'before'},[other]:{text:'old'}},localRuleDrafts:{a:{text:'keep'}}};
 const current={...baseline,mqttBindingDrafts:{...baseline.mqttBindingDrafts,[other]:{text:'later sibling'}}};
 const next=mergeWorkspacePatch(current,baseline,{mqttBindingDrafts:{...baseline.mqttBindingDrafts,[key]:{text:'mine'}}});
 assert.deepEqual(next.mqttBindingDrafts[other],{text:'later sibling'});assert.deepEqual(next.mqttBindingDrafts[key],{text:'mine'});assert.deepEqual(next.localRuleDrafts,baseline.localRuleDrafts);
});
test('a saved-row recovery finishes only its own original create journal and cannot erase another new association',()=>{
 const original=associationKey(gateway.id),saved=associationKey(gateway.id,row.id),request={request_id:'original-create'};
 assert.equal(mayFinishOriginDraft({[original]:{pending:request}},original,saved,request),true);
 assert.equal(mayFinishOriginDraft({[original]:{pending:{request_id:'later-create'}}},original,saved,request),false);
 assert.equal(mayFinishOriginDraft({[original]:{}},original,saved,request),false);
 assert.equal(mayFinishOriginDraft({[saved]:{pending:request}},saved,saved,request),false);
});
test('a previous saved request cannot claim a later blocked or stopped request has committed',()=>{
 const old=associationRequest(draft(),gateway,null,objects,rules),next={...old,request_id:crypto.randomUUID(),extractor_id:'local-two'};
 assert.equal(associationPendingConfirmed({pending:next,confirmedRequest:old}),false);
 assert.equal(associationPendingConfirmed({pending:{...old,extractor_id:'local-two'},confirmedRequest:old}),false);
 assert.equal(associationPendingConfirmed({pending:next,confirmedRequest:{...next}}),true);
 assert.equal(associationPendingConfirmed({pending:{},confirmedRequest:{}}),false);
 assert.equal(associationPendingConfirmed({confirmedRequest:old}),false);
 const controller=new AbortController();controller.abort();
 assert.equal(associationPendingConfirmed({pending:next,confirmedRequest:old,signal:controller.signal}),false);
});
test('readable association state does not expose internal identifiers or confuse paused and stale with active',()=>{
 assert.equal(associationHeading(row),'林场业务 · 北站');assert.equal(associationHeading({id:'internal-uuid'}),'独立业务对象 · 已登记对象');
 assert.match(associationState({...row,status:'paused'}),/已暂停/);assert.match(associationState({...row,needs_review:true}),/重新核对/);
 assert.equal(sameAssociation(savedAssociationDraft(gateway,row),row),true);assert.equal(sameAssociation({...savedAssociationDraft(gateway,row),extractor_id:'local-two'},row),false);
});
test('each original association reports its own processing rather than borrowing another object success',()=>{
 assert.match(associationDeliveryState({processing:{status:'processed'}}),/候选信息/);
 assert.match(associationDeliveryState({submission:{status:'failed',error:'failed'}}),/原件保留/);
 assert.match(associationDeliveryState({status:'invalidated'}),/不可继续/);
 assert.match(associationDeliveryState({status:'submitted',observation_id:'saved'}),/等待后台/);
});
test('multiple object records supersede empty legacy receipt pointers without implying every object finished',()=>{
 const original={status:'awaiting_object_binding',data_received:true,processing:null,observation_id:null};
 const processed={...original,associations:[{processing:{status:'processed'}},{processing:{status:'processed'}}]};
 assert.equal(mqttReceiptSummary(processed),'原件已保存 · 2 条对象处理记录');assert.doesNotMatch(mqttReceiptSummary(processed),/等待关联/);
 assert.equal(mqttReceiptSummary({...original,associations:[{processing:{status:'failed'}},{status:'pending'}]}),'原件已保存 · 2 条对象处理记录');
 assert.match(mqttReceiptSummary({...processed,status:'quarantined'}),/待核查/);
 assert.match(mqttReceiptSummary({...original,associations:[]}),/等待关联/);
 assert.match(mqttReceiptSummary({...original,associations:'invalid'}),/等待关联/);
});
test('retry requires explicit current server permission and positive delivery CAS',()=>{
 const d={can_submit:true,current:true,version:3,status:'submitted',processing:{status:'failed'}};
 assert.equal(retryAssociationDelivery(d),true);
 for(const patch of [{can_submit:false},{can_submit:undefined},{version:undefined},{version:0},{current:false},{needs_review:true},{status:'invalidated'},{processing:{status:'processed'}},{processing:{status:'pending'}},{submission:{status:'pending'}}])assert.equal(retryAssociationDelivery({...d,...patch}),false);
});
