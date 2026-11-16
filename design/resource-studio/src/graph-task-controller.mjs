// Durable per-activation human work. A missing response is never a new command.
import {inspectGraphMessageStatus} from './graph-message-status.mjs';
import {freshNumber,numericValue,isTypedNumber,numericToken,typedRequestJSON,parseTypedJSON,plainTyped,cloneTyped,typedEqual,numericDisplay} from './typed-numbers-v132.mjs';
import {graphWireParseCanonical,graphWireCanonical,graphWireDigest,graphWireNumericTokens,graphWireClone,verifyGraphWire} from './full-semantic-graph-wire.mjs';
const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const int=v=>Number.isSafeInteger(v)&&v>=0;
const uuid=v=>typeof v==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(v);
const text=v=>typeof v==='string'&&v.length>0;
const need=(ok,message='原任务、草稿或请求无法核对，原记录保留。')=>{if(!ok)throw Error(message);};
const copy=v=>JSON.parse(JSON.stringify(v));
const exact=(v,keys)=>object(v)&&Object.keys(v).sort().join('|')===[...keys].sort().join('|');
const order=(a,b)=>{const x=[...a],y=[...b];for(let i=0;i<Math.min(x.length,y.length);i++){const n=x[i].codePointAt(0)-y[i].codePointAt(0);if(n)return n;}return x.length-y.length;};

// Python hashes the JSON-decoded request. Match its scalar float spelling too;
// JSON.stringify uses a different exponent threshold and omits exponent zeros.
function pythonNumber(value){
 need(Number.isFinite(value)&&(!Number.isInteger(value)||Number.isSafeInteger(value)),'数值超出可准确保存的范围。');
 const wire=JSON.stringify(value);
 if(Number.isInteger(value))return wire;
 let result=Math.abs(value)<0.0001?value.toExponential():wire;
 if(result.includes('e'))result=result.replace(/e([+-]?)(\d+)$/,(_m,sign,digits)=>'e'+(sign||'+')+digits.padStart(2,'0'));
 return result;
}
export function graphCanonical(value){
 if(Array.isArray(value))return '['+value.map(graphCanonical).join(',')+']';
 if(object(value))return '{'+Object.keys(value).sort(order).map(key=>graphCanonical(key)+':'+graphCanonical(value[key])).join(',')+'}';
 if(typeof value==='number')return pythonNumber(value);
 need(value===null||typeof value==='boolean'||typeof value==='string');
 if(typeof value==='string')need(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value));
 return JSON.stringify(value);
}
// A v3 outbox's JSON mirror is never numeric authority. Its original typed
// command is exclusively exact_body, which checkedPending reparses below.
const mirrorJSON=value=>JSON.stringify(plainTyped(value));
const sortedMirror=value=>Array.isArray(value)?value.map(sortedMirror):object(value)?Object.fromEntries(Object.keys(value).sort(order).map(key=>[key,sortedMirror(value[key])])):value;
const sameMirror=(a,b)=>mirrorJSON(sortedMirror(a))===mirrorJSON(sortedMirror(b));
const publicEqual=(a,b)=>{if(Object.is(a,b))return true;if(!a||!b||typeof a!=='object'||typeof b!=='object'||Array.isArray(a)!==Array.isArray(b))return false;const left=Object.keys(a).sort(order),right=Object.keys(b).sort(order);return left.length===right.length&&left.every((key,index)=>key===right[index]&&publicEqual(a[key],b[key]));};
const same=(a,b)=>a?.schema==='graph-task-outbox-v3'||b?.schema==='graph-task-outbox-v3'?sameMirror(a,b):graphCanonical(a)===graphCanonical(b);
export async function graphHash(value,cryptoAPI=globalThis.crypto){
 return [...new Uint8Array(await cryptoAPI.subtle.digest('SHA-256',new TextEncoder().encode(graphCanonical(value))))]
  .map(value=>value.toString(16).padStart(2,'0')).join('');
}
const unsupported='此待办包含当前表单尚不支持的字段规则，已保留原草稿；暂不能提交结果。';
const scalar=v=>v===null||['string','boolean'].includes(typeof v)||(typeof v==='number'||isTypedNumber(v))&&Number.isFinite(numericValue(v));
const numberText=value=>isTypedNumber(value)?numericToken(value):Object.is(value,-0)?'-0.0':String(value);
export const humanNullToggle=value=>({mode:value?.mode==='null'?'unset':'null',raw:''});
export async function readWorkspaceBuckets(api,instanceID,{onCurrent=()=>{},onHistory=()=>{}}={}){
 const identity=encodeURIComponent(instanceID);
 const current=Promise.allSettled([api('/instances'),api('/instances/'+identity+'/releases'),api('/instances/'+identity+'/objects')]).then(onCurrent);
 const history=Promise.allSettled([api('/workspace/instances/'+identity+'/tasks'),api('/workspace/instances/'+identity+'/observations'),api('/workspace/instances/'+identity+'/daily')]).then(onHistory);
 return Promise.allSettled([current,history]);
}
export function inspectHumanSchema(schema,{typedNumbers=false}={}){
 try{
  const sourceTokens=typedNumbers?graphWireNumericTokens(schema):{},pointer=key=>key.replaceAll('~','~0').replaceAll('/','~1');
  need(object(schema)&&schema.type==='object'&&object(schema.properties)&&Array.isArray(schema.required||[])&&schema.additionalProperties===false);
  need(Object.keys(schema).every(key=>['type','title','description','properties','required','additionalProperties'].includes(key)));
  const required=schema.required||[];
  need(required.every(key=>typeof key==='string'&&Object.hasOwn(schema.properties,key))&&new Set(required).size===required.length);
  const fields=Object.entries(schema.properties).map(([key,spec],index)=>{
   need(object(spec)&&Object.keys(spec).every(name=>['type','title','description','enum','minLength','maxLength','pattern','format','minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf'].includes(name)));
   const types=Array.isArray(spec.type)?spec.type:[spec.type],nonnull=types.filter(value=>value!=='null');
   need(nonnull.length===1&&new Set(types).size===types.length&&nonnull.every(value=>['string','boolean','number','integer'].includes(value)));
   const type=nonnull[0],nullable=types.includes('null');
   const enumTokens=(spec.enum||[]).map((_value,index)=>sourceTokens['/properties/'+pointer(key)+'/enum/'+index]||null);
   if(spec.enum!==undefined){
    need(Array.isArray(spec.enum)&&spec.enum.length>0&&spec.enum.length<=100&&spec.enum.every(value=>scalar(value)&&(value===null?nullable:type==='integer'?Number.isSafeInteger(numericValue(value)):type==='number'?typeof value==='number'||typedNumbers&&isTypedNumber(value):typeof value===type)));
    const encoded=spec.enum.map((value,index)=>{if(!typedNumbers)return graphCanonical(value);if(typeof value!=='number'&&!isTypedNumber(value))return typedRequestJSON(value);const original=isTypedNumber(value)?value:enumTokens[index]!==null?freshNumber(enumTokens[index],type,'原合同选项'):freshNumber(value,type,'原合同选项');return typedRequestJSON(original);});
    need(new Set(encoded).size===spec.enum.length);
   }
   for(const size of ['minLength','maxLength'])if(spec[size]!==undefined)need(type==='string'&&int(spec[size]));
   if(spec.pattern!==undefined){need(type==='string'&&typeof spec.pattern==='string'&&spec.pattern.length<=1000);new RegExp(spec.pattern,'u');}
   if(spec.format!==undefined)need(type==='string'&&['date','date-time','email'].includes(spec.format));
   for(const limit of ['minimum','maximum','exclusiveMinimum','exclusiveMaximum','multipleOf'])if(spec[limit]!==undefined)need(['number','integer'].includes(type)&&(typeof spec[limit]==='number'||typedNumbers&&isTypedNumber(spec[limit]))&&Number.isFinite(numericValue(spec[limit]))&&(limit!=='multipleOf'||numericValue(spec[limit])>0));
   need(spec.title===undefined||typeof spec.title==='string');need(spec.description===undefined||typeof spec.description==='string');
   return {key,type,nullable,required:required.includes(key),label:spec.title||key.replaceAll('_',' ')||'结果字段 '+(index+1),spec:typedNumbers?cloneTyped(spec):copy(spec),...(typedNumbers?{enum_tokens:enumTokens}:{})};
  });
  need(fields.length<=80);return {supported:true,fields,reason:''};
 }catch{return {supported:false,fields:[],reason:unsupported};}
}
export function humanValues(schema,draft,{typedNumbers=false}={}){
 const model=inspectHumanSchema(schema,{typedNumbers});if(!model.supported)return {};
 if(object(draft)&&draft.schema==='graph-human-form-draft-v1'&&object(draft.values)){need(exact(draft,['schema','values']));return checkValues(model,draft.values);}
 need(draft==null||object(draft)&&Object.keys(draft).every(key=>Object.hasOwn(schema.properties,key)),'服务器原草稿不能自动填入当前字段，请先对照并保留。');
 const values={};
 for(const field of model.fields){
  const value=object(draft)&&Object.hasOwn(draft,field.key)?draft[field.key]:undefined;
  if(value===undefined)values[field.key]={mode:'unset',raw:''};
  else if(field.spec.enum){const index=field.spec.enum.findIndex(item=>typedNumbers?publicEqual(plainTyped(item),plainTyped(value)):same(item,value));need(index>=0);values[field.key]={mode:'value',raw:String(index)};}
  else if(value===null){need(field.nullable);values[field.key]={mode:'null',raw:''};}
  else{need(field.type==='integer'?Number.isSafeInteger(numericValue(value)):field.type==='number'?(typeof value==='number'||typedNumbers&&isTypedNumber(value))&&Number.isFinite(numericValue(value)):typeof value===field.type);if(typedNumbers&&typeof value==='number'&&Number.isInteger(value))need(Number.isSafeInteger(value),'服务器原数值缺少可核对的数值文本，原稿保留，请明确重新填写。');values[field.key]={mode:'value',raw:typedNumbers&&['integer','number'].includes(field.type)?numberText(value):String(value)};}
 }
 return values;
}
function checkValues(model,values){
 need(object(values)&&Object.keys(values).length===model.fields.length);
 for(const field of model.fields){const value=values[field.key];need(exact(value,['mode','raw'])&&['unset','null','value'].includes(value.mode)&&typeof value.raw==='string'&&value.raw.length<=16000);}
 return copy(values);
}
export function humanOutput(schema,values,{typedNumbers=false}={}){
 const model=inspectHumanSchema(schema,{typedNumbers});need(model.supported,unsupported);checkValues(model,values);const result={};
 for(const field of model.fields){
  const {mode,raw}=values[field.key],spec=field.spec,label=field.label;
  if(mode==='unset'){need(!field.required,'请填写“'+label+'”。');continue;}
  let value;
  if(mode==='null'){need(field.nullable,'“'+label+'”不能填写为空。');value=null;}
  else if(spec.enum){need(/^\d+$/.test(raw)&&Number(raw)<spec.enum.length,'请选择“'+label+'”。');const index=Number(raw);value=spec.enum[index];if(typedNumbers&&(typeof value==='number'||isTypedNumber(value)))value=isTypedNumber(value)?value:freshNumber(field.enum_tokens[index]??value,field.type,label);}
  else if(field.type==='boolean'){need(['true','false'].includes(raw),'请选择“'+label+'”。');value=raw==='true';}
  else if(['number','integer'].includes(field.type)){
   need(raw.trim()!==''&&/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(raw),'请填写“'+label+'”的有效数值。');
   if(typedNumbers)value=freshNumber(raw,field.type,label);
   else{value=Number(raw);pythonNumber(value);need(field.type!=='integer'||Number.isSafeInteger(value),'“'+label+'”须填写可准确保存的整数。');}
  }else value=raw;
  if(spec.enum)need(spec.enum.some(item=>typedNumbers?publicEqual(plainTyped(item),plainTyped(value)):same(item,value)),'请选择“'+label+'”的可用选项。');
  if(value!==null&&field.type==='string'){
   const length=[...value].length;
   need(spec.minLength===undefined||length>=spec.minLength,'“'+label+'”内容过短。');need(spec.maxLength===undefined||length<=spec.maxLength,'“'+label+'”内容过长。');
   need(spec.pattern===undefined||new RegExp(spec.pattern,'u').test(value),'“'+label+'”格式不符合要求。');
   if(spec.format==='date')need(/^\d{4}-\d{2}-\d{2}$/.test(value)&&!Number.isNaN(Date.parse(value))&&new Date(value).toISOString().slice(0,10)===value,'请填写“'+label+'”的有效日期。');
   if(spec.format==='date-time')need(!Number.isNaN(Date.parse(value))&&/T.*(?:Z|[+-]\d{2}:\d{2})$/i.test(value),'请填写“'+label+'”的完整时间和时区。');
   if(spec.format==='email')need(/^[^\s@]+@[^\s@]+$/.test(value),'请核对“'+label+'”的邮箱格式。');
  }
  if(typeof value==='number'||isTypedNumber(value)){
   const actual=numericValue(value),bound=key=>spec[key]===undefined?undefined:numericValue(spec[key]);
   need(spec.minimum===undefined||actual>=bound('minimum'),'“'+label+'”小于允许范围。');need(spec.maximum===undefined||actual<=bound('maximum'),'“'+label+'”大于允许范围。');
   need(spec.exclusiveMinimum===undefined||actual>bound('exclusiveMinimum'),'“'+label+'”须大于所列下限。');need(spec.exclusiveMaximum===undefined||actual<bound('exclusiveMaximum'),'“'+label+'”须小于所列上限。');
   if(spec.multipleOf!==undefined)need(Math.abs(actual/bound('multipleOf')-Math.round(actual/bound('multipleOf')))<1e-10,'“'+label+'”不符合数值间隔。');
  }
  result[field.key]=value;
 }
 if(typedNumbers)typedRequestJSON(result);else graphCanonical(result);return result;
}
export const graphTaskScopeKey=scope=>JSON.stringify([scope.actor_id,scope.workspace_id,scope.task_id]);
const scopeKeys=['actor_id','workspace_id','task_id'];
function checkedScope(scope){need(exact(scope,scopeKeys)&&scopeKeys.every(key=>text(scope[key])));return copy(scope);}
export function graphHumanInputsReady(human){
 if(human?.inputs_available===false)return false;
 if(!Object.hasOwn(human||{},'required_inputs'))return true;
 return Array.isArray(human.required_inputs)&&human.required_inputs.every(item=>item?.available===true&&(!Object.hasOwn(item,'availability_code')||item.availability_code==='ready'));
}
export function graphHumanCanComplete(human){
 return graphHumanInputsReady(human)&&(!Object.hasOwn(human||{},'activation_status')||human.activation_status==='ready'&&human.can_complete===true);
}
const nestedTask=detail=>Object.hasOwn(detail,'workflow_review_mode')||Object.hasOwn(detail,'workflow_simulation_only');
const exclusiveInputAmbiguous=detail=>typeof detail?.graph_runtime?.error==='string'&&detail.graph_runtime.error.startsWith('exclusive_reference_ambiguous:');
function checkedNotices(detail){
 need(!Object.hasOwn(detail,'workflow_service_notices_v195'),'站内通知只能读取本账号的公开投影，不能读取内部通知仓。');
 if(!Object.hasOwn(detail,'workflow_notices_v195'))return;
 const rows=detail.workflow_notices_v195,seen=new Set();need(Array.isArray(rows)&&rows.length<=1000,'本账号站内通知清单暂无法核对。');
 for(const item of rows){
  need(exact(item,['id','sender_name','message','delivered_at','delivery_status','read_status','simulation_only'])&&uuid(item.id)&&!seen.has(item.id)&&(item.sender_name===null||text(item.sender_name)&&item.sender_name.trim().length>0)&&text(item.message)&&Array.from(item.message).length<=8000&&item.message.trim().length>0&&typeof item.delivered_at==='string'&&/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(item.delivered_at)&&Number.isFinite(Date.parse(item.delivered_at))&&item.delivery_status==='delivered_to_in_app'&&item.read_status==='not_tracked'&&typeof item.simulation_only==='boolean','站内通知的发送人、内容、送达或阅读状态无法核对，未生成阅读回执。');
  if(Object.hasOwn(detail,'workflow_simulation_only'))need(item.simulation_only===detail.workflow_simulation_only,'站内通知与任务的工程标识不一致。');seen.add(item.id);
 }
}
function checkedDetail(detail,scope){
 need(object(detail)&&detail.id===scope.task_id&&int(detail.version)&&object(detail.graph_runtime)&&int(detail.graph_runtime.version)&&int(detail.graph_runtime.cancel_epoch)&&Array.isArray(detail.graph_runtime.commands)&&object(detail.graph_runtime.activations)&&Array.isArray(detail.graph_pending_humans));
 need(typeof detail.graph_runtime.graph_hash==='string'&&/^[a-f0-9]{64}$/.test(detail.graph_runtime.graph_hash));
 if(detail.workspace_id!==undefined)need(detail.workspace_id===scope.workspace_id);
 const nested=nestedTask(detail);if(nested)need(['author_self_review','engineering_simulation'].includes(detail.workflow_review_mode)&&typeof detail.workflow_simulation_only==='boolean'&&detail.workflow_simulation_only===(detail.workflow_review_mode==='engineering_simulation'));
 checkedNotices(detail);
 const seen=new Set();for(const human of detail.graph_pending_humans){
  need(object(human)&&text(human.activation_id)&&!seen.has(human.activation_id)&&text(human.node_id)&&text(human.actor_id)&&int(human.version)&&typeof human.can_edit==='boolean'&&object(human.output_schema));
  if(nested)need(Object.hasOwn(human,'required_inputs')&&['ready','waiting_guard'].includes(human.activation_status)&&typeof human.can_complete==='boolean','递归任务未提供完整输入或处理状态，原草稿保留。');
  if(Object.hasOwn(human,'required_inputs')){
   need(Array.isArray(human.required_inputs)&&human.required_inputs.length<=1000&&typeof human.inputs_available==='boolean');
   for(const [index,item]of human.required_inputs.entries()){
    need(object(item)&&text(item.label)&&typeof item.available==='boolean'&&Object.hasOwn(item,'value')&&(nested?['input','output','exclusive_output','port_projection']:['input','output']).includes(item.source)&&object(item.schema)&&(item.producer_activation_id===null||text(item.producer_activation_id)));
    if(nested)need(int(item.ordinal)&&item.ordinal===index&&text(item.source_name)&&item.source_name.trim().length>0&&['ready','missing','ambiguous',...(item.source==='port_projection'?['unavailable']:[])].includes(item.availability_code)&&item.available===(item.availability_code==='ready'),'递归任务输入的次序、来源或可用状态无法核对，未提交结果。');
   }
   need(human.inputs_available===human.required_inputs.every(item=>item.available));
  }
  seen.add(human.activation_id);const activation=detail.graph_runtime.activations[human.activation_id];
  need(object(activation)&&activation.id===human.activation_id&&activation.node_id===human.node_id&&activation.version===human.version&&activation.epoch===detail.graph_runtime.cancel_epoch&&(nested?activation.status===human.activation_status:activation.status==='ready'));
  if(nested){need(Array.isArray(detail.graph_runtime.ready_human_ids)&&detail.graph_runtime.ready_human_ids.includes(human.activation_id)===(human.activation_status==='ready'));need(!human.can_complete||human.activation_status==='ready'&&human.inputs_available===true,'等待输入或条件的人工作业不能标为可完成。');}
 }
 return detail;
}
function commandBody(pending){
 if(['message_retry','reasoning_retirement'].includes(pending.operation))return {actor_id:pending.scope.actor_id,body:pending.body};
 const body=pending.schema==='graph-task-outbox-v3'?graphWireParseCanonical(pending.exact_body):pending.body,result={actor_id:pending.scope.actor_id,expected_version:body.version,cancel_epoch:body.cancel_epoch};
 if(pending.operation==='cancel')return {...result,reason:body.reason};
 return {...result,activation_id:body.activation_id,[pending.operation==='draft'?'draft':'output']:body[pending.operation==='draft'?'draft':'output']};
}
function checkedPending(value,scope){
 if(value?.schema==='graph-task-retirement-outbox-v1'){
  need(exact(value,['schema','scope','operation','body','exact_body','body_hash','expected_job_id','rejected'])
   &&same(value.scope,scope)&&value.operation==='reasoning_retirement'&&uuid(value.expected_job_id));
  const b=value.body;
  need(exact(b,['request_id','activation_id','version','cancel_epoch','expected_head_hash','confirm_retire','reason'])
   &&uuid(b.request_id)&&text(b.activation_id)&&int(b.version)&&int(b.cancel_epoch)&&/^[a-f0-9]{64}$/.test(b.expected_head_hash)
   &&b.confirm_retire===true&&text(b.reason)&&b.reason.trim()&&[...b.reason].length<=1000&&!b.reason.includes('\0')
   &&value.exact_body===JSON.stringify(b)&&/^[a-f0-9]{64}$/.test(value.body_hash));
  need(value.rejected===null||exact(value.rejected,['status','message'])&&[400,409,422].includes(value.rejected.status)&&typeof value.rejected.message==='string');
  graphCanonical(value);return copy(value);
 }
 if(value?.schema==='graph-task-outbox-v3'){
  need(exact(value,['schema','scope','operation','body','exact_body','body_hash','form_generation','rejected'])&&same(value.scope,scope)&&value.operation==='complete'&&text(value.exact_body)&&int(value.form_generation)&&/^[a-f0-9]{64}$/.test(value.body_hash));
  const body=graphWireParseCanonical(value.exact_body);
  need(exact(body,['request_id','activation_id','version','cancel_epoch','output'])&&uuid(body.request_id)&&text(body.activation_id)&&int(body.version)&&int(body.cancel_epoch)&&object(body.output));
  need(sameMirror(JSON.parse(JSON.stringify(body)),value.body),'原请求镜像与完整原正文不一致，记录保留。');
  need(value.rejected===null||exact(value.rejected,['status','message'])&&[400,409,422].includes(value.rejected.status)&&typeof value.rejected.message==='string');
  return copy(value);
 }
 if(value?.schema==='graph-task-outbox-v2'){
  need(exact(value,['schema','scope','operation','body','exact_body','body_hash','expected_job_id','rejected'])
   &&same(value.scope,scope)&&value.operation==='message_retry'&&uuid(value.expected_job_id));
  const body=value.body;
  need(exact(body,['request_id','job_id','job_version','reason'])&&uuid(body.request_id)&&uuid(body.job_id)&&body.job_id===value.expected_job_id&&int(body.job_version)&&body.job_version>=1
   &&text(body.reason)&&body.reason.trim()&&[...body.reason].length<=1000&&!body.reason.includes('\0')
   &&value.exact_body===JSON.stringify(body)&&/^[a-f0-9]{64}$/.test(value.body_hash));
  need(value.rejected===null||exact(value.rejected,['status','message'])&&[400,409,422].includes(value.rejected.status)&&typeof value.rejected.message==='string');
  graphCanonical(value);return copy(value);
 }
 need(exact(value,['schema','scope','operation','body','exact_body','body_hash','form_generation','rejected'])&&value.schema==='graph-task-outbox-v1'&&same(value.scope,scope)&&['draft','complete','cancel'].includes(value.operation));
 const body=value.body,keys=['request_id','version','cancel_epoch',...(value.operation==='cancel'?['reason']:['activation_id',value.operation==='draft'?'draft':'output'])];
 need(exact(body,keys)&&uuid(body.request_id)&&int(body.version)&&int(body.cancel_epoch)&&value.exact_body===JSON.stringify(body)&&/^[a-f0-9]{64}$/.test(value.body_hash)&&int(value.form_generation));
 if(value.operation==='cancel')need(text(body.reason)&&body.reason.length<=1000);else need(text(body.activation_id)&&object(body[value.operation==='draft'?'draft':'output']));
 need(value.rejected===null||exact(value.rejected,['status','message'])&&[400,409,422].includes(value.rejected.status)&&typeof value.rejected.message==='string');
 graphCanonical(value);return copy(value);
}
function messageCandidate(detail){
 const status=inspectGraphMessageStatus(detail),error=status?.error;
 need(status?.can_retry===true&&object(error)&&uuid(error.job_id)&&int(error.job_version)&&error.job_version>=1,
  '当前没有获准恢复的原消息检查，请刷新服务器状态；原窗口和草稿保留。');
 return {job_id:error.job_id,job_version:error.job_version};
}
function retirementCandidate(detail,identity,actor){
 const state=detail.graph_runtime,activation=state?.activations?.[identity],job=detail.graph_jobs?.[identity];
 const head=detail.current_goal_evaluation_v288?.head_hash,label=detail.graph_node_labels?.[identity];
 need(object(activation)&&activation.node_id==='reasoning'&&activation.status==='ready'&&int(activation.version)
  &&object(job)&&job.phase==='queued'&&job.attempt===1&&uuid(job.job_id)
  &&int(state.cancel_epoch)&&activation.epoch===state.cancel_epoch&&/^[a-f0-9]{64}$/.test(head||'')
  &&label?.actor_id===actor,'原步骤、负责人或最新评价已变化，请重新核对；未停止步骤。');
 return {activation_id:identity,version:activation.version,cancel_epoch:state.cancel_epoch,head_hash:head,job_id:job.job_id};
}
function retirementReceipt(detail,pending,absent=false){
 const nonce=pending.body.request_id,records=detail.graph_reasoning_retirements_v337;
 need((records===undefined||object(records))&&!detail.graph_runtime.commands.some(row=>row.request_id===nonce)
  &&!detail.graph_retry_requests?.[nonce]&&!detail.graph_wait_retries?.[nonce], '原请求的操作家族无法核对，记录保留。');
 const row=records?.[nonce];
 if(absent){need(row===undefined,'原步骤停用记录与查询结果不一致。');return;}
 const transport=detail.graph_jobs?.[pending.body.activation_id];
 need(object(row)&&row.schema==='graph-reasoning-retirement-v337'&&row.request_id===nonce
  &&row.actor_id===pending.scope.actor_id&&row.body_hash===pending.body_hash&&row.activation_id===pending.body.activation_id
  &&row.job_id===pending.expected_job_id&&row.head_hash===pending.body.expected_head_hash&&row.status==='retired'
  &&transport?.job_id===pending.expected_job_id&&transport.phase==='retired_for_new_evaluation',
  '原步骤停用回执尚未与当前任务核对，记录保留。');
 return {actor_id:row.actor_id,body_hash:row.body_hash,activation_id:row.activation_id,job_id:row.job_id};
}
function messageReceipt(detail,pending,absent=false){
 const nonce=pending.body.request_id,records=detail.graph_wait_retries,other=detail.graph_retry_requests;
 need((records===undefined||object(records))&&(other===undefined||object(other))
  &&!detail.graph_runtime.commands.some(item=>item.request_id===nonce)
  &&!(other&&Object.hasOwn(other,nonce)),'原请求的操作家族无法核对，记录保留。');
 const found=records&&Object.hasOwn(records,nonce);
 if(absent){need(!found,'原消息恢复记录与查询结果不一致，原请求保留。');return;}
 const expected={actor_id:pending.scope.actor_id,body_hash:pending.body_hash,job_id:pending.expected_job_id};
 need(found&&exact(records[nonce],['actor_id','body_hash','job_id'])&&same(records[nonce],expected),
  '当前任务未包含此原消息检查的持久回执，原请求保留。');
 return expected;
}
export class GraphTaskController{
 constructor({api,scope,storage=globalThis.localStorage,session=globalThis.sessionStorage,locks=globalThis.navigator?.locks,crypto=globalThis.crypto,onChange=()=>{},onSaved=async()=>true,typedNumbers=false}){
  Object.assign(this,{api,scope:checkedScope(scope),storage,session,locks,crypto,onChange,onSaved,typedNumbers});
  this.prefix='ga-graph-task:'+graphTaskScopeKey(this.scope)+':';this.outboxKey=this.prefix+'outbox';this.forms={};this.pending=null;this.pendingNotFound=false;this.detail=null;this.error='';this.busy=false;this.denied=false;this.disposed=false;this.localSaved=true;this.cancelReason='';this.cancelSaved=true;
  try{
   this.sessionPin=this.session?Object.fromEntries(['user','workspace','csrf'].map(key=>[key,this.session.getItem('ga-setup-csrf'+(key==='csrf'?'':'-'+key))])):null;
   if(this.sessionPin)need(this.sessionPin.user===this.scope.actor_id&&this.sessionPin.workspace===this.scope.workspace_id&&text(this.sessionPin.csrf));
   const raw=this.storage.getItem(this.outboxKey);if(raw!==null)this.pending=checkedPending(JSON.parse(raw),this.scope);
   const reason=this.storage.getItem(this.prefix+'cancel-reason');if(reason!==null){need(typeof JSON.parse(reason)==='string');this.cancelReason=JSON.parse(reason);}
  }catch(error){this.error='本机原请求或登录范围无法读取，已阻止写入，请保留此浏览器记录。';this.denied=true;}
 }
 active(){
  if(this.sessionPin)try{need(['user','workspace','csrf'].every(key=>this.session.getItem('ga-setup-csrf'+(key==='csrf'?'':'-'+key))===this.sessionPin[key]));}catch{this.denied=true;}
  need(!this.disposed&&!this.denied,'账号、工作区或任务已变化，原草稿和请求保留。');
 }
 notify(){if(!this.disposed)this.onChange(this);}
 schemaEqual(a,b){return this.typedNumbers?sameMirror(a,b):same(a,b);}
 snapshot(value){return this.typedNumbers?graphWireClone(value):copy(value);}
 async pendingHash(pending){return pending.schema==='graph-task-outbox-v3'?graphWireDigest(commandBody(pending),{crypto:this.crypto}):graphHash(commandBody(pending),this.crypto);}
 dispose(){this.disposed=true;}
 async request(path,options={}){this.active();const value=await this.api(path,{...options,headers:{...options.headers,'X-Workspace-ID':this.scope.workspace_id,...(this.sessionPin?{'X-CSRF-Token':this.sessionPin.csrf}:{})}});this.active();return value;}
 async identity(){this.active();const me=await this.request('/me');this.active();if(me.user_id!==this.scope.actor_id||me.workspace_id!==this.scope.workspace_id){this.denied=true;this.notify();throw Error('登录账号或工作区已变化，原草稿和请求保留。');}return me;}
 async open(){await this.identity();const detail=await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id));await this.identity();this.ingest(detail);return this.snapshot(this.detail);}
 async refresh(){
  this.active();need(!this.busy,'请先核对当前请求，再刷新任务状态。');this.busy=true;this.notify();
  try{await this.identity();const detail=await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id));await this.identity();this.ingest(detail);if(!this.pending)this.error='';return this.snapshot(this.detail);}
  catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}
 }
 write(key,value){const encoded=JSON.stringify(value);this.storage.setItem(key,encoded);need(this.storage.getItem(key)===encoded,'本机恢复记录未能完整读回，尚未向服务器提交。');return encoded;}
 persistForm(identity){
  const form=this.forms[identity];try{
   const record=Object.fromEntries(Object.entries(form).filter(([key])=>!['localSaved','persistedSignature'].includes(key))),signature=JSON.stringify(record);
   // Every distinct draft first gets an immutable recovery slot. Two windows
   // may update their shared latest pointer, but cannot erase each other's work.
   if(form.persistedSignature!==signature)this.write(this.prefix+'history:local:'+encodeURIComponent(identity)+':'+this.crypto.randomUUID(),record);
   this.write(this.prefix+'draft:'+encodeURIComponent(identity),record);form.persistedSignature=signature;form.localSaved=true;
   this.localSaved=Object.values(this.forms).every(value=>value.localSaved!==false);return true;
  }catch(error){form.localSaved=false;this.localSaved=false;this.error='草稿暂未保存到本机，请勿离开此页。'+error.message;return false;}
 }
 localRecoveries(identity){
  const form=this.forms[identity],values=[],seen=new Set();if(!form)return values;
  for(let index=0;index<this.storage.length;index++){
   const key=this.storage.key(index);if(!key?.startsWith(this.prefix+'history:local:'+encodeURIComponent(identity)+':'))continue;
   try{const record=JSON.parse(this.storage.getItem(key));if(record.activation_id!==identity||record.node_id!==form.node_id||record.graph_hash!==form.graph_hash||!this.schemaEqual(record.schema,form.schema))continue;const signature=graphCanonical(record.values);if(seen.has(signature)||same(record.values,form.values))continue;seen.add(signature);values.push(record);}catch{this.error='部分旧草稿无法读取，原恢复记录保留。';}
  }
  return values.reverse();
 }
 restoreLocal(identity,record){
  this.active();const form=this.forms[identity];need(form&&record.activation_id===identity&&record.node_id===form.node_id&&record.graph_hash===form.graph_hash&&this.schemaEqual(record.schema,form.schema));
  const currentSchema=this.detail?.graph_pending_humans.find(item=>item.activation_id===identity)?.output_schema||form.schema;
  checkValues(inspectHumanSchema(currentSchema,{typedNumbers:this.typedNumbers}),record.values);need(this.persistForm(identity));form.values=copy(record.values);form.dirty=true;form.generation++;need(this.persistForm(identity));this.notify();
 }
 editCancelReason(reason){this.active();need(typeof reason==='string'&&reason.length<=1000);this.cancelReason=reason;try{this.write(this.prefix+'cancel-reason',reason);this.cancelSaved=true;}catch{this.cancelSaved=false;this.error='取消原因尚未保存到本机，请勿离开此页。';}this.notify();}
 ingest(detail){
  this.active();checkedDetail(detail,this.scope);
  if(this.detail&&detail.version<this.detail.version)return false;
  if(this.detail)need(detail.graph_runtime.graph_hash===this.detail.graph_runtime.graph_hash,'任务执行设计已变化，原草稿保留。');
  this.detail=this.snapshot(detail);
  for(const human of detail.graph_pending_humans){
   const identity=human.activation_id,model=inspectHumanSchema(human.output_schema,{typedNumbers:this.typedNumbers});let form=this.forms[identity];
   let remoteValues={},restoreIssue=false;try{remoteValues=model.supported?humanValues(human.output_schema,human.draft,{typedNumbers:this.typedNumbers}):{};}catch{remoteValues=humanValues(human.output_schema,null,{typedNumbers:this.typedNumbers});restoreIssue=true;}
   if(!form){
    try{
     const raw=this.storage.getItem(this.prefix+'draft:'+encodeURIComponent(identity));
     if(raw!==null){form=JSON.parse(raw);need(form.activation_id===identity&&form.node_id===human.node_id&&form.graph_hash===detail.graph_runtime.graph_hash&&this.schemaEqual(form.schema,human.output_schema)&&int(form.generation)&&int(form.base_version)&&int(form.cancel_epoch)&&typeof form.dirty==='boolean');if(model.supported)checkValues(model,form.values);}
     else form={activation_id:identity,node_id:human.node_id,graph_hash:detail.graph_runtime.graph_hash,schema:copy(human.output_schema),values:remoteValues,base_version:human.version,cancel_epoch:detail.graph_runtime.cancel_epoch,generation:0,dirty:false,restoreIssue,remoteConflict:restoreIssue};
     form.localSaved=true;this.forms[identity]=form;
    }catch(error){this.denied=true;this.error='此待办的本机草稿无法恢复，原记录保留。';this.notify();throw error;}
   }
   need(form.node_id===human.node_id&&form.graph_hash===detail.graph_runtime.graph_hash&&this.schemaEqual(form.schema,human.output_schema),'原待办字段或执行设计已变化，原草稿保留。');
   if(form.dirty&&form.base_version!==human.version)form.remoteConflict=true;
   else if(!form.dirty&&!this.pending){form.values=remoteValues;form.base_version=human.version;form.cancel_epoch=detail.graph_runtime.cancel_epoch;form.remoteConflict=restoreIssue;form.restoreIssue=restoreIssue;}
  }
  this.notify();return true;
 }
 edit(identity,field,value){
  this.active();const human=this.detail?.graph_pending_humans.find(item=>item.activation_id===identity),form=this.forms[identity];
  need(human&&human.can_edit&&human.actor_id===this.scope.actor_id&&form&&inspectHumanSchema(human.output_schema,{typedNumbers:this.typedNumbers}).supported,'当前账号不能编辑此待办。');
  need(Object.hasOwn(form.values,field)&&exact(value,['mode','raw'])&&['unset','null','value'].includes(value.mode)&&typeof value.raw==='string');
  form.values[field]=copy(value);form.dirty=true;form.generation++;this.persistForm(identity);this.notify();
 }
 acknowledgeRemote(identity){
  this.active();const form=this.forms[identity],human=this.detail.graph_pending_humans.find(item=>item.activation_id===identity);need(form&&human&&form.remoteConflict);
  this.write(this.prefix+'history:draft:'+this.crypto.randomUUID(),form);
  if(human.draft!=null&&!(form.server_drafts||[]).some(value=>same(value,human.draft)))form.server_drafts=[...(form.server_drafts||[]),copy(human.draft)];
  form.base_version=human.version;form.cancel_epoch=this.detail.graph_runtime.cancel_epoch;form.remoteConflict=false;form.restoreIssue=false;form.dirty=true;form.generation++;need(this.persistForm(identity));this.notify();
 }
 async locked(fn){need(this.locks&&typeof this.locks.request==='function','浏览器缺少多窗口保存保护，已禁用提交；本页仍可查看和保留草稿。');return this.locks.request(this.prefix+'lock',{mode:'exclusive'},async()=>{this.active();return fn();});}
 currentPending(){const raw=this.storage.getItem(this.outboxKey);return raw===null?null:checkedPending(JSON.parse(raw),this.scope);}
 async retryMessages(reason){
  this.active();need(!this.busy,'请先等待当前请求核对完成。');
  const selected=messageCandidate(this.detail);
  need(typeof reason==='string'&&reason.trim()&&[...reason].length<=1000&&!reason.includes('\0'),'请填写恢复原消息检查的原因。');
  this.busy=true;this.error='';this.notify();
  try{return await this.locked(async()=>{
   const me=await this.identity();need(['admin','operator'].includes(me.role),'当前账号不能恢复消息检查。');
   need(!this.currentPending()&&!this.pending,'原请求仍待核对，请先读取原提交回执。');
   need(this.guard(),'本机草稿或恢复记录尚未可靠保存，未提交消息检查恢复。');
   const current=await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id));await this.identity();this.ingest(current);
   const candidate=messageCandidate(current);
   need(same(candidate,selected),'原消息检查已有变化，请核对刷新后的状态；未替换为另一个检查。');
   const body={request_id:this.crypto.randomUUID(),job_id:selected.job_id,job_version:selected.job_version,reason};
   const pending={schema:'graph-task-outbox-v2',scope:copy(this.scope),operation:'message_retry',body,
    exact_body:JSON.stringify(body),body_hash:'',expected_job_id:selected.job_id,rejected:null};
   pending.body_hash=await graphHash(commandBody(pending),this.crypto);this.active();
   need(!this.currentPending()&&!this.pending,'另一窗口已保留原请求，尚未发送。');
   this.write(this.outboxKey,pending);this.pending=copy(pending);this.pendingNotFound=false;this.notify();
   await this._sendMessageRetry(pending);return this._recover();
  });}catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}
 }
 async _sendMessageRetry(pending){
  const me=await this.identity();need(['admin','operator'].includes(me.role),'当前账号不能恢复消息检查。');
  need(same(this.currentPending(),pending),'另一窗口已改变原请求，尚未重发。');
  need(await graphHash(commandBody(pending),this.crypto)===pending.body_hash);this.active();
  need(same(this.currentPending(),pending),'另一窗口已改变原请求，尚未重发。');
  this.pendingNotFound=false;this.notify();
  try{await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id)+'/graph/retry-messages-v2',
   {method:'POST',body:pending.exact_body});}
  catch(error){
   if([400,409,422].includes(error.status)&&same(this.currentPending(),pending)){
    const rejected={...pending,rejected:{status:error.status,message:error.message}};
    this.write(this.outboxKey,rejected);this.pending=copy(rejected);
   }
   throw error;
  }
 }
 async resendMessageRetry(){
  this.active();need(!this.busy,'请先等待当前请求核对完成。');this.busy=true;this.error='';this.notify();
  try{return await this.locked(async()=>{
   const pending=this.currentPending();need(pending?.operation==='message_retry'&&!pending.rejected,
    '只有尚未核对的原消息恢复请求可以按原内容重试。');
   const result=await this._recover();if(result.found)return result;
   need(same(this.currentPending(),pending),'原请求已由另一窗口改变，请重新核对。');
   const candidate=messageCandidate(this.detail);
   need(candidate.job_id===pending.expected_job_id&&candidate.job_version===pending.body.job_version,
    '原消息检查或版本已变化，原请求保留，未恢复其他检查。');
   need(this.guard(),'本机草稿或恢复记录尚未可靠保存，未重发。');
   await this._sendMessageRetry(pending);return this._recover();
  });}catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}
 }
 async retireReasoning(identity){
  this.active();need(!this.busy,'请先等待当前请求核对完成。');
  const selected=retirementCandidate(this.detail,identity,this.scope.actor_id);
  this.busy=true;this.error='';this.notify();
  try{return await this.locked(async()=>{
   const me=await this.identity();need(['admin','operator'].includes(me.role),'当前账号不能办理原步骤。');
   need(!this.currentPending()&&!this.pending&&this.guard(),'原请求或本机草稿尚未可靠保存，请先核对。');
   const current=await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id));await this.identity();this.ingest(current);
   need(same(retirementCandidate(current,identity,this.scope.actor_id),selected),'原步骤或最新评价已变化；未停止步骤。');
   const body={request_id:this.crypto.randomUUID(),activation_id:identity,version:selected.version,cancel_epoch:selected.cancel_epoch,
    expected_head_hash:selected.head_hash,confirm_retire:true,reason:'原评价已被新评价替换，停止尚未执行的原步骤并保留原记录。'};
   const pending={schema:'graph-task-retirement-outbox-v1',scope:copy(this.scope),operation:'reasoning_retirement',body,
    exact_body:JSON.stringify(body),body_hash:'',expected_job_id:selected.job_id,rejected:null};
   pending.body_hash=await graphHash(commandBody(pending),this.crypto);checkedPending(pending,this.scope);this.active();
   need(!this.currentPending()&&!this.pending,'另一窗口已保留原请求，尚未发送。');
   this.write(this.outboxKey,pending);this.pending=copy(pending);this.pendingNotFound=false;this.notify();
   await this._sendRetirement(pending);return this._recover();
  });}catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}
 }
 async _sendRetirement(pending){
  const me=await this.identity();need(['admin','operator'].includes(me.role),'当前账号不能办理原步骤。');
  need(same(this.currentPending(),pending)&&await graphHash(commandBody(pending),this.crypto)===pending.body_hash,
   '原请求正文无法核对，未重发。');this.active();
  try{await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id)+'/graph/retire-obsolete-reasoning',
   {method:'POST',body:pending.exact_body});}
  catch(error){if([400,409,422].includes(error.status)&&same(this.currentPending(),pending)){
   const rejected={...pending,rejected:{status:error.status,message:error.message}};this.write(this.outboxKey,rejected);this.pending=copy(rejected);
  }throw error;}
 }
 async resendRetirement(){
  this.active();need(!this.busy&&this.pendingNotFound,'请先核对原请求是否已保存。');this.busy=true;this.error='';this.notify();
  try{return await this.locked(async()=>{
   const pending=this.currentPending();need(pending?.operation==='reasoning_retirement'&&!pending.rejected,'原请求不可直接重试。');
   const result=await this._recover();if(result.found)return result;
   const current=retirementCandidate(this.detail,pending.body.activation_id,this.scope.actor_id);
   need(current.job_id===pending.expected_job_id&&current.version===pending.body.version
    &&current.cancel_epoch===pending.body.cancel_epoch&&current.head_hash===pending.body.expected_head_hash,
    '原步骤或最新评价已变化；保留原请求，未重发。');
   need(this.guard(),'本机草稿或恢复记录尚未可靠保存，未重发。');
   await this._sendRetirement(pending);return this._recover();
  });}catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}
 }
 async submit(operation,identity=null,reason=''){
  need(['draft','complete','cancel'].includes(operation));need(!this.busy,'请先等待当前请求核对完成。');this.busy=true;this.error='';this.notify();
  try{return await this.locked(async()=>{
   const me=await this.identity();need(['admin','operator'].includes(me.role),'当前账号无权提交人工处理结果。');need(!this.currentPending()&&!this.pending,'原请求仍待核对，请先读取原提交回执。');
   const current=await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id));await this.identity();this.ingest(current);
   let body,generation=0;
   if(operation==='cancel'){
    need(this.detail.permissions?.can_cancel_task===true,'当前账号不能取消此任务。');need(reason.trim()&&reason.length<=1000,'请填写取消原因。');
    body={request_id:this.crypto.randomUUID(),version:this.detail.graph_runtime.version,cancel_epoch:this.detail.graph_runtime.cancel_epoch,reason};
   }else{
    const human=this.detail.graph_pending_humans.find(item=>item.activation_id===identity),form=this.forms[identity];
    need(human&&human.can_edit&&human.actor_id===this.scope.actor_id&&form&&!form.remoteConflict,'此待办已有变化或尚未核对服务器新稿；本页草稿保留。');
    if(operation==='complete'){need(!exclusiveInputAmbiguous(this.detail),'同一项资料出现多个分支结果，需核查任务证据；可以先保存草稿，未提交完成结果。');need(graphHumanInputsReady(human),'本步骤所需输入尚未产生或仍需核查，可以先保存草稿；未提交完成结果。');need(!nestedTask(this.detail)||graphHumanCanComplete(human),'本步骤仍在等待输入或执行条件，可以先保存草稿；当前不能提交完成结果。');}
    need(inspectHumanSchema(human.output_schema,{typedNumbers:this.typedNumbers}).supported,unsupported);need(this.persistForm(identity),'本机草稿尚未保存，未提交。');generation=form.generation;
    const output=operation==='draft'?{schema:'graph-human-form-draft-v1',values:copy(form.values)}:humanOutput(human.output_schema,form.values,{typedNumbers:this.typedNumbers});
    body={request_id:this.crypto.randomUUID(),activation_id:identity,version:human.version,cancel_epoch:this.detail.graph_runtime.cancel_epoch,[operation==='draft'?'draft':'output']:output};
   }
   let pending;
   if(this.typedNumbers&&operation==='complete'){
    const graphHash=this.detail.graph_runtime.graph_hash,form=this.forms[identity];
    const response=await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id)+'/graph/command-hash',
     {method:'POST',body:typedRequestJSON({schema:'workflow-graph-command-hash-command-v1',operation:'complete',command:body})});
    await this.identity();const value=verifyGraphWire(response);
    need(exact(value,['schema','actor_id','workspace_id','task_id','graph_hash','operation','command','body_hash'])
     &&value.schema==='workflow-graph-command-hash-v1'&&value.operation==='complete'&&value.actor_id===this.scope.actor_id
     &&value.workspace_id===this.scope.workspace_id&&value.task_id===this.scope.task_id&&value.graph_hash===graphHash
     &&typeof value.body_hash==='string'&&/^[a-f0-9]{64}$/.test(value.body_hash),'原数值准备结果与当前待办或账号不一致，原稿保留。');
    const exactBody=graphWireCanonical(value.command);
    need(typedEqual(parseTypedJSON(exactBody),body),'服务器核对的数值类型或原正文不一致，原稿保留。');
    need(this.forms[identity]===form&&form.generation===generation&&!form.remoteConflict
     &&this.detail.graph_runtime.graph_hash===graphHash,'核对数值期间输入或原待办已变化，请保留当前稿重新提交。');
    pending={schema:'graph-task-outbox-v3',scope:copy(this.scope),operation,
     body:JSON.parse(JSON.stringify(JSON.parse(exactBody))),exact_body:exactBody,body_hash:value.body_hash,form_generation:generation,rejected:null};
    checkedPending(pending,this.scope);need(await this.pendingHash(pending)===value.body_hash,'原数值正文与服务器摘要不一致，未发送。');
   }else{
    pending={schema:'graph-task-outbox-v1',scope:copy(this.scope),operation,body,exact_body:JSON.stringify(body),body_hash:'',form_generation:generation,rejected:null};
    pending.body_hash=await graphHash(commandBody(pending),this.crypto);
   }
   this.active();need(!this.currentPending(),'另一窗口已保留请求，尚未发送。');
   this.write(this.outboxKey,pending);this.pending=copy(pending);this.pendingNotFound=false;this.notify();
   try{
    await this.identity();this.active();need(same(this.currentPending(),pending));
    await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id)+'/graph/'+operation,{method:operation==='draft'?'PUT':'POST',body:pending.exact_body});
   }catch(error){
    if([400,409,422].includes(error.status)&&same(this.currentPending(),pending)){pending.rejected={status:error.status,message:error.message};this.write(this.outboxKey,pending);this.pending=copy(pending);}
    this.error=error.message;throw error;
   }
   return this._recover();
  });}catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}
 }
 async _recover(){
  const pending=this.currentPending();need(pending,'没有需要核对的原请求。');this.pending=copy(pending);
  need(await this.pendingHash(pending)===pending.body_hash);await this.identity();
  const response=await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id)+'/graph/requests/'+pending.body.request_id);await this.identity();
  need(response?.schema==='workflow-graph-request-recovery-v1'&&typeof response.found==='boolean');checkedDetail(response.task,this.scope);
  need(same(this.currentPending(),pending),'原请求已由另一窗口改变，请重新读取。');
  const message=pending.operation==='message_retry',retirement=pending.operation==='reasoning_retirement';
  if(!response.found){
   need(response.request===null);if(message)messageReceipt(response.task,pending,true);if(retirement)retirementReceipt(response.task,pending,true);
   this.ingest(response.task);this.pendingNotFound=true;this.error='服务器暂未查到原请求；编号和内容仍保留，不会自动重发。';return {found:false,task:response.task};
  }
  const expected=message?messageReceipt(response.task,pending):retirement?retirementReceipt(response.task,pending):{request_id:pending.body.request_id,op:pending.operation,actor_id:this.scope.actor_id,body_hash:pending.body_hash};
  need(exact(response.request,message?['actor_id','body_hash','job_id']:retirement?['actor_id','body_hash','activation_id','job_id']:['request_id','op','actor_id','body_hash'])&&same(response.request,expected),'原回执与当前账号、操作或完整内容不一致，原请求保留。');
  this.pendingNotFound=false;
  const current=await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id));await this.identity();checkedDetail(current,this.scope);
  need(current.version>=response.task.version&&current.graph_runtime.version>=response.task.graph_runtime.version
   &&(!this.detail||current.version>=this.detail.version&&current.graph_runtime.version>=this.detail.graph_runtime.version),
   '当前任务读回早于原回执，原请求保留。');
  if(message)messageReceipt(current,pending);
  else if(retirement)retirementReceipt(current,pending);
  else need(current.graph_runtime.commands.some(item=>same(item,expected)),'当前任务读回早于原回执，原请求保留。');
  this.ingest(current);need(await this.onSaved(this.snapshot(current))!==false,'当前任务尚未读入本页，原请求保留。');this.active();need(same(this.currentPending(),pending),'原请求已由另一窗口改变，请重新读取。');
  const identity=pending.body.activation_id,form=identity?this.forms[identity]:null;
  if(form){
   const human=current.graph_pending_humans.find(item=>item.activation_id===identity);
   const ownCurrentDraft=pending.operation==='draft'&&human&&human.version===pending.body.version+1&&same(human.draft,pending.body.draft);
   if(ownCurrentDraft){
    form.base_version=human.version;form.cancel_epoch=current.graph_runtime.cancel_epoch;form.remoteConflict=false;
    if(form.generation===pending.form_generation)form.dirty=false;
   }else if(pending.operation==='draft'){
    // Confirming A's old command is not permission to use B's newer draft CAS.
    // Closed activations also retain A's visible local history for comparison.
    form.dirty=true;form.remoteConflict=true;
   }else if(pending.operation==='complete'){
    const activation=current.graph_runtime.activations[identity];
    need(activation?.status==='completed'&&(pending.schema==='graph-task-outbox-v3'?publicEqual(activation.output,commandBody(pending).output):same(activation.output,pending.body.output)),'原结果回执与当前已保存输出不一致，原请求保留。');
    if(form.generation===pending.form_generation)form.dirty=false;
   }
   need(this.persistForm(identity),'服务器已保存，但本机恢复记录尚未结清；请保留原请求后重试核对。');
  }
  this.write(this.prefix+'history:request:'+pending.body.request_id,{pending,receipt:response.request,task_version:current.version});
  this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null);this.pending=null;this.pendingNotFound=false;this.error='';this.notify();return {found:true,task:this.snapshot(current)};
 }
 async recover(){need(!this.busy,'请先等待当前请求核对。');this.busy=true;this.notify();try{return await this.locked(()=>this._recover());}catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}}
 async resendHumanResult(){
  this.active();need(!this.busy&&this.pendingNotFound,'请先只读核对原结果是否已保存。');this.busy=true;this.notify();
  try{return await this.locked(async()=>{
   const pending=this.currentPending();need(pending?.schema==='graph-task-outbox-v3'&&pending.operation==='complete'&&!pending.rejected,'只有明确未找到的原人工结果可按原正文重试。');
   const found=await this._recover();if(found.found)return found;
   need(same(this.currentPending(),pending)&&await this.pendingHash(pending)===pending.body_hash);
   const me=await this.identity();need(['admin','operator'].includes(me.role),'当前账号不能重试原人工结果。');
   if(nestedTask(this.detail)){const body=graphWireParseCanonical(pending.exact_body),human=this.detail.graph_pending_humans.find(item=>item.activation_id===body.activation_id);need(human&&human.can_edit&&human.actor_id===this.scope.actor_id&&!exclusiveInputAmbiguous(this.detail)&&graphHumanCanComplete(human),'原任务输入或分支结果已有变化；原请求保留，只能继续核对回执或先处理输入，未重新提交完成结果。');}
   try{await this.request('/workspace/tasks/'+encodeURIComponent(this.scope.task_id)+'/graph/complete',{method:'POST',body:pending.exact_body});}
   catch(error){if([400,409,422].includes(error.status)&&same(this.currentPending(),pending)){pending.rejected={status:error.status,message:error.message};this.write(this.outboxKey,pending);this.pending=copy(pending);}throw error;}
   return this._recover();
  });}catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}
 }
 async archiveRejected(){
  need(!this.busy);this.busy=true;this.notify();try{return await this.locked(async()=>{
   const pending=this.currentPending();need(pending?.rejected,'原请求结果尚不确定，只能继续核对原编号。');
   const result=await this._recover();if(result.found)return result;
   need(same(this.currentPending(),pending));this.write(this.prefix+'history:request:'+pending.body.request_id,{pending,rejected:true});
   this.storage.removeItem(this.outboxKey);need(this.storage.getItem(this.outboxKey)===null);this.pending=null;this.pendingNotFound=false;this.error='已保留被拒请求和本页草稿；请核对变化后重新保存。';this.notify();return result;
  });}catch(error){this.error=error.message;throw error;}finally{this.busy=false;this.notify();}
 }
 guard(){
  let safe=true;for(const identity of Object.keys(this.forms))if(!this.persistForm(identity))safe=false;
  try{this.write(this.prefix+'cancel-reason',this.cancelReason);this.cancelSaved=true;}catch{this.cancelSaved=false;safe=false;}
  try{if(this.pending)need(same(this.currentPending(),this.pending));}catch{safe=false;}
  this.notify();return safe;
 }
}
export const createGraphTaskController=options=>new GraphTaskController(options);
