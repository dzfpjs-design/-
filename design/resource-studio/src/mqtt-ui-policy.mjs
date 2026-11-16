// Display and form rules only. Server checks remain authoritative.
export const MQTT_FIELDS = {
  record_key:'数据编号字段', device_key:'设备编号字段', time_key:'采集时间字段',
  values_key:'测量值所在字段', units_key:'单位所在字段'
};
export function referenceInputAllowed(value){return typeof value==='string'&&('GA_MQTT_'.startsWith(value)||/^GA_MQTT_[A-Z0-9_]{0,80}$/.test(value));}
export function newMQTTDraft(){return {
  requestId:crypto.randomUUID(), name:'', broker:'', topics:[{topic:'',device:''}],
  measurements:[{input:'',attribute:'',unit:''}],
  fields:{record_key:'record_id',device_key:'device_id',time_key:'observed_at',values_key:'values',units_key:'units'},
  accessLoginRef:'',accessKeyRef:'',baseVersion:null
};}
export function gatewayDraft(row){return {
  name:row.name,broker:row.configuration.broker,baseVersion:row.version,
  topics:Object.entries(row.configuration.topics).map(([topic,device])=>({topic,device})),
  measurements:Object.entries(row.configuration.mapping).map(([input,value])=>({input,...value})),
  fields:{...newMQTTDraft().fields,...row.configuration.fields},
  accessLoginRef:row.configuration.username_ref,accessKeyRef:row.configuration.password_ref
};}
export function mqttBody(draft){
  if(!draft.name.trim())throw Error('请填写接入名称。');
  if(!/^mqtts?:\/\/[^/@?#\s:]+:\d+$/.test(draft.broker)||/:\/\/[^/]*@/.test(draft.broker))
    throw Error('请填写不含账号密码的 MQTT 服务地址和端口。');
  const topics={},mapping={};
  if(!draft.topics.length||draft.topics.length>32)throw Error('请确认 1 至 32 个设备主题。');
  for(const {topic,device} of draft.topics){
    if(!topic||!device.trim()||/[+#\x00-\x1f]/.test(topic)||topic.startsWith('$')||Object.hasOwn(topics,topic)||Object.values(topics).includes(device))
      throw Error('设备编号与具体主题须一一对应，不能重复或使用通配符。');
    topics[topic]=device;
  }
  if(!draft.measurements.length||draft.measurements.length>64)throw Error('请确认 1 至 64 个测量字段。');
  for(const {input,attribute,unit} of draft.measurements){
    if(!input||!attribute||Object.hasOwn(mapping,input)||Object.values(mapping).some(x=>x.attribute===attribute))
      throw Error('请逐个填写报文字段和测量名称，不能重复或覆盖。');
    mapping[input]={attribute,unit};
  }
  if(Boolean(draft.accessLoginRef)!==Boolean(draft.accessKeyRef)||[draft.accessLoginRef,draft.accessKeyRef].some(x=>x&&!/^GA_MQTT_[A-Z0-9_]{1,80}$/.test(x)))
    throw Error('请填写成对的服务端凭据引用，不能填写账号或密码原文。');
  return {name:draft.name,configuration:{broker:draft.broker,topics,mapping,fields:draft.fields,
    username_ref:draft.accessLoginRef,password_ref:draft.accessKeyRef},
    ...(draft.baseVersion?{expected_version:draft.baseVersion}:{request_id:draft.requestId})};
}
export function authorizationReviewProblem(draft,row){
  if(draft?.baseVersion!==row.version)return '来源版本已变化，请用服务器配置重新编辑并核对后再授权。';
  let body;try{body=mqttBody(draft);}catch{return '来源草稿尚未完整，请先核对并保存接入配置。';}
  const canonical=value=>JSON.stringify(value,(_key,v)=>v&&typeof v==='object'&&!Array.isArray(v)?Object.fromEntries(Object.keys(v).sort().map(k=>[k,v[k]])):v);
  const original={...row.configuration,fields:{...newMQTTDraft().fields,...row.configuration.fields}};
  if(body.name.trim()!==row.name||canonical(body.configuration)!==canonical(original))
    return '来源约定有尚未提交的编辑，请先保存接入配置或用服务器配置重新编辑，再授权接收。';
  return '';
}
const issueNames={missing_capture_time:'缺少采集时间',invalid_capture_time:'采集时间无法识别',
  unreadable_packet:'报文无法按约定读取',device_topic_binding_mismatch:'设备编号与主题不符',
  qos_not_verified:'接收方式尚未核验',retained_packet_needs_time_review:'历史保留消息需核对时间',
  record_content_conflict:'相同数据编号的内容冲突'};
export function mqttIssue(issue){const [kind,field]=issue.split(':');return issueNames[kind]||
  (kind==='invalid_value'?'测量值无效：'+field:kind==='unit_conflict'?'单位不符：'+field:'接收依据需要核对');}
export function receiptStatus(row){
  if(row.status==='quarantined')return '待核查，不用于对象状态';
  if(row.processing?.status==='processed')return '已提取候选信息';
  if(row.processing?.status==='failed'||row.submission?.status==='failed')return '处理未完成，原件保留';
  if(row.observation_id||row.submission)return '等待后台处理';
  return '原件已保存，等待关联业务对象';
}
export function numericObjectOptions(entities,capabilities){return entities.flatMap(entity=>{
  const cap=capabilities.find(c=>c.id===entity.schema_capability_id&&c.kind==='domain_package'&&c.status==='published');
  const props=cap?.data?.specification?.entity_schemas?.[entity.entity_type]?.properties||{};
  const fields=Object.entries(props).filter(([,s])=>['number','integer'].includes(s.type)).map(([key,s])=>({key,label:s.title||key,unit:s.unit}));
  return fields.length?[{id:entity.id,label:entity.data?.name||entity.external_id,package_id:cap.id,entity_type:entity.entity_type,fields}]:[];
});}
export function localRuleOptions(capabilities){return capabilities.filter(c=>c.status==='published'&&['tool','specialist_model'].includes(c.kind)
  &&c.data?.execution?.adapter==='builtin'&&c.data?.execution?.handler==='numeric_rules'
  &&Object.keys(c.data.execution).length===2&&c.data?.allowed_agent_roles?.includes('perception')
  &&!c.data?.data_policy?.cloud_allowed&&!c.data?.data_policy?.external_allowed);}
