const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
const plain=value=>!!value&&typeof value==='object'&&!Array.isArray(value);
const copy=value=>JSON.parse(JSON.stringify(value));
const sha=value=>typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);
const uuid=value=>typeof value==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
export function manualCanonical(value){
 if(Array.isArray(value))return '['+value.map(manualCanonical).join(',')+']';
 if(plain(value))return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+manualCanonical(value[key])).join(',')+'}';
 return JSON.stringify(value);
}
export async function manualHash(value){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(manualCanonical(value)))),byte=>byte.toString(16).padStart(2,'0')).join('');}

export function manualFields(fields){
 if(!Array.isArray(fields)||!fields.length||fields.length>30)throw Error('登记字段尚未完整核对，请刷新已发布能力。');
 const seen=new Set();
 return fields.map(field=>{
  if(!plain(field)||typeof field.key!=='string'||!field.key||seen.has(field.key)||typeof field.label!=='string'||!field.label.trim()||!['string','number','integer','boolean'].includes(field.type)||typeof field.required!=='boolean')throw Error('登记字段契约无法核对，未开放提交。');
  seen.add(field.key);
  if(field.format!==undefined&&(field.type!=='string'||field.format!=='date'))throw Error('此字段格式尚不支持人工登记。');
  for(const key of ['minLength','maxLength'])if(field[key]!==undefined&&(!Number.isSafeInteger(field[key])||field[key]<0||field.type!=='string'))throw Error('文字长度限制无法核对。');
  for(const key of ['minimum','maximum'])if(field[key]!==undefined&&(!Number.isFinite(field[key])||!['number','integer'].includes(field.type)))throw Error('数值范围无法核对。');
  if(field.unit!=null&&typeof field.unit!=='string')throw Error('字段单位无法核对。');
  if(field.pattern!==undefined&&(typeof field.pattern!=='string'||field.type!=='string'))throw Error('文字格式限制无法核对。');
  if(field.enum!==undefined&&(!Array.isArray(field.enum)||!field.enum.length||field.enum.some(value=>!scalarType(value,field.type))))throw Error('字段可选值无法核对。');
  return {...field};
 });
}
function scalarType(value,type){return type==='string'?typeof value==='string':type==='boolean'?typeof value==='boolean':typeof value==='number'&&Number.isFinite(value)&&(type!=='integer'||Number.isSafeInteger(value));}
export function registrationDefinition(cap){
 if(!cap)throw Error('请选择已发布的本地处理能力。');
 if(cap.registration_mode==='unsupported')throw Error(cap.registration_issue||'此能力尚不支持人工登记。');
 const modern=own(cap,'registration_mode')||own(cap,'registration_fields');
 if(modern){
  if(!['passthrough','threshold'].includes(cap.registration_mode))throw Error('登记方式无法核对，未开放提交。');
  return {mode:cap.registration_mode,fields:manualFields(cap.registration_fields)};
 }
 // Old numeric-rule records keep their original finite-number form.
 if(!Array.isArray(cap.rules)||!cap.rules.length||cap.rules.some(rule=>typeof rule.field!=='string'||!rule.field))throw Error('此旧能力没有可核对的数值登记字段。');
 return {mode:'threshold',fields:manualFields([...new Set(cap.rules.map(rule=>rule.field))].map(key=>({key,label:key,type:'number',required:true})))};
}
export function registrationAttributes(cap,values){
 const {fields}=registrationDefinition(cap);if(!plain(values))throw Error('原登记输入无法恢复。');
 const allowed=new Set(fields.map(field=>field.key)),result=Object.create(null);
 if(Object.keys(values).some(key=>!allowed.has(key)&&values[key]!==''&&values[key]!==undefined))throw Error('登记字段已变化，请保留原稿并核对当前字段。');
 for(const field of fields){
  const raw=values[field.key];
  if(raw===undefined||raw===null||raw===''){if(field.required)throw Error('请填写'+field.label+'（必填）。');continue;}
  let value=raw;
  if(['number','integer'].includes(field.type)){
   if(typeof raw==='string'){if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(raw.trim()))throw Error(field.label+'不是有效数值。');value=Number(raw);}
  }
  if(!scalarType(value,field.type))throw Error(field.label+'的类型或数值精度不符合登记要求。');
  if(field.format==='date'){
   const date=/^(?!0000)\d{4}-\d{2}-\d{2}$/.test(value)?new Date(value+'T00:00:00Z'):null;
   if(!date||!Number.isFinite(date.getTime())||date.toISOString().slice(0,10)!==value)throw Error(field.label+'需填写有效日期。');
  }
  const length=typeof value==='string'?[...value].length:null;
  if(field.minLength!==undefined&&length<field.minLength||field.maxLength!==undefined&&length>field.maxLength)throw Error(field.label+'的文字长度不符合登记要求。');
  if(field.minimum!==undefined&&value<field.minimum||field.maximum!==undefined&&value>field.maximum)throw Error(field.label+'超出已登记的数值范围。');
  // Registered patterns remain authoritative on the server; never execute an
  // arbitrary capability regex in the browser or change text to satisfy it.
  if(field.enum&&!field.enum.some(item=>Object.is(item,value)))throw Error('请从'+field.label+'的已登记选项中选择。');
  result[field.key]=value;
 }
 if(!Object.keys(result).length)throw Error('请至少填写一个登记字段。');
 return result;
}
export function checkedManualIdentity(value,account){
 if(value?.workspace_id!==account.workspace_id||value?.user_id!==account.user_id||value?.username!==account.username)throw Error('当前登录账号或工作区已改变，原输入与请求保留，请重新核对会话。');
 return value;
}
export function reviewScope(account,instanceID,observationID){
 const scope={workspace_id:account.workspace_id,user_id:account.user_id,username:account.username,instance_id:instanceID,observation_id:observationID};
 if(Object.values(scope).some(value=>typeof value!=='string'||!value))throw Error('核查账号与原登记范围尚未核实。');
 return scope;
}
export const reviewKey=scope=>'ga-reported-review-v1:'+manualCanonical(scope);
function checkBody(body){
 if(!plain(body)||Object.keys(body).sort().join(',')!=='decision,expected_input_hash,expected_version,reason,request_id'||!Number.isSafeInteger(body.expected_version)||body.expected_version<1||!uuid(body.request_id)||!sha(body.expected_input_hash)||!['confirmed','rejected'].includes(body.decision)||typeof body.reason!=='string'||!body.reason.trim()||body.reason.length>2000)throw Error('原核查请求无法完整恢复，请保留原记录。');
 return body;
}
function checkedJournal(value,scope){
 if(!plain(value)||value.schema!==1||manualCanonical(value.scope)!==manualCanonical(scope)||typeof value.reason!=='string'||value.reason.length>2000||!['confirmed','rejected'].includes(value.decision)||!Array.isArray(value.history)||value.history.length>100)throw Error('原核查草稿或账号范围无法恢复，请保留原记录。');
 if(value.pending)checkBody(value.pending.body);
 for(const entry of value.history)checkBody(entry.body);
 return value;
}
export function readManualReview(storage,scope){const raw=storage.getItem(reviewKey(scope));let value;try{value=raw===null?{schema:1,scope,reason:'',decision:'confirmed',pending:null,history:[]}:JSON.parse(raw);}catch{throw Error('原核查草稿无法读取，请保留浏览器记录。');}return checkedJournal(value,scope);}
export function writeManualReview(storage,scope,value,expected){
 const old=readManualReview(storage,scope),next=checkedJournal(copy(value),scope);
 if(expected&&manualCanonical(old)!==manualCanonical(expected))throw Error('另一页面已更新核查记录，原请求保留，请刷新核对。');
 if(old.pending&&manualCanonical(old.pending)!==manualCanonical(next.pending)&&!next.history.some(entry=>manualCanonical(entry.body)===manualCanonical(old.pending.body)))throw Error('原核查请求尚未保留，不能更换编号或内容。');
 const encoded=JSON.stringify(next);storage.setItem(reviewKey(scope),encoded);if(storage.getItem(reviewKey(scope))!==encoded)throw Error('核查恢复记录未完整保存，未发送请求。');
 return readManualReview(storage,scope);
}
export async function checkedManualReview(review,pending,scope){
 const body=checkBody(pending.body);
 if(!plain(review)||review.request_id!==body.request_id||review.reviewed_by!==scope.user_id||review.body_hash!==await manualHash(body)||review.input_hash!==body.expected_input_hash||review.decision!==body.decision||review.reason!==body.reason)throw Error('原核查回执的账号或内容不一致，不能认定本次请求成功。');
 return review;
}
export async function checkedManualProbe(value,pending,scope){
 if(typeof value?.found!=='boolean'||value.observation_id!==scope.observation_id||value.request_id!==pending.body.request_id||!Number.isSafeInteger(value.version)||value.version<1)throw Error('原核查请求的查询回执无法核对。');
 if(value.found){if(value.actor_matches!==true)throw Error('原核查请求不属于当前账号。');await checkedManualReview(value.review,pending,scope);}
 else if(value.review!==null&&value.review!==undefined)throw Error('未知核查请求不能携带其他核查回执。');
 return value;
}
export function checkedRegistrationResolution(value,body){
 if(typeof value?.found!=='boolean')throw Error('原登记请求的查询结果无法核对。');
 if(!value.found){if(value.request_matches!==null||value.record!==null)throw Error('未知登记不能携带其他保存结果。');return value;}
 const row=value.record;
 if(typeof value.request_matches!=='boolean'||!row||typeof row.id!=='string'||row.source_id!==body.source_id||row.request_id!==body.source_record_id||row.processing_version!==(body.processing_version||'1.0.0'))throw Error('保存记录与原登记编号不一致。');
 if(value.request_matches&&(row.entity_id!==body.entity_id||row.extractor_id!==body.extractor_id||manualCanonical(row.review_basis?.original_attributes)!==manualCanonical(body.attributes)))throw Error('保存记录与原登记内容不一致。');
 return value;
}
export async function checkedManualPost(value,pending,scope){
 if(value?.id!==scope.observation_id||value?.workspace_id!==scope.workspace_id||!Array.isArray(value?.data?.verification_history))throw Error('原登记核查的保存回执无法核对。');
 const matches=value.data.verification_history.filter(item=>item.request_id===pending.body.request_id);
 if(matches.length!==1)throw Error('未取得此编号的完整核查回执，原请求保留。');
 return checkedManualReview(matches[0],pending,scope);
}
export function reviewableRecord(record){
 const basis=record?.review_basis;
 return record?.status==='processed'&&record?.data_received===true&&record?.can_verify===true&&plain(basis)&&!basis.integrity_issue&&sha(basis.verification_input_hash)&&plain(basis.original_attributes)&&plain(basis.extracted_attributes);
}
export const displayManualValue=value=>value===undefined?'未登记':value===null?'未确定':value===true?'是':value===false?'否':String(value);
