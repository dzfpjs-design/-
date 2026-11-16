// Closed browser commands. Original datasets are never stored in browser drafts.
export const BD_BASE='/business-data';
export const BD_KINDS={http_json:'HTTP JSON接口',xlsx:'Excel表格',csv:'CSV表格',json:'JSON文件'};
export const BD_CONVERSIONS={identity:'保持原类型',string:'转为文本',integer:'转为整数',number:'转为数值',boolean:'转为是或否',date:'ISO日期'};
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,SHA=/^[a-f0-9]{64}$/;
export const need=(ok,message='原业务数据请求无法核对，原输入已保留。')=>{if(!ok)throw Error(message);};
export const clone=x=>JSON.parse(JSON.stringify(x));
export function canonical(x){if(Array.isArray(x))return '['+x.map(canonical).join(',')+']';if(x&&typeof x==='object')return '{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';need(x===null||['string','boolean'].includes(typeof x)||typeof x==='number'&&Number.isFinite(x));return JSON.stringify(x);}
const exact=(x,keys)=>x&&typeof x==='object'&&!Array.isArray(x)&&Object.keys(x).sort().join(',')===keys.split(',').sort().join(',');
const positive=x=>Number.isSafeInteger(x)&&x>0,uuid=x=>typeof x==='string'&&UUID.test(x),text=x=>typeof x==='string'&&!!x.trim();
export function commandPath(kind,c={}){
 const source=()=>{need(uuid(c.source_id));return BD_BASE+'/sources/'+c.source_id;};
 const read=()=>{need(uuid(c.read_id));return BD_BASE+'/reads/'+c.read_id;};
 const imp=()=>{need(uuid(c.import_id));return BD_BASE+'/imports/'+c.import_id;};
 if(kind==='create_source')return BD_BASE+'/sources';
 if(kind==='edit_source')return source();
 if(['pause','resume_source'].includes(kind))return source()+'/'+(kind==='pause'?'pause':'resume');
 if(kind==='read')return source()+'/read';if(kind==='upload')return source()+'/uploads';
 if(kind==='retry_day_ack'){need(uuid(c.event_id));return source()+'/days/'+c.event_id+'/retry-data-delivery';}
 if(kind==='retry_read')return read()+'/retry';if(kind==='binding')return BD_BASE+'/bindings';
 if(kind==='preview'){need(uuid(c.binding_id));return BD_BASE+'/bindings/'+c.binding_id+'/previews';}
 if(['confirm','cancel','resume'].includes(kind))return imp()+'/'+kind;
 if(kind==='retry_ack'){need(uuid(c.row_id));return imp()+'/rows/'+c.row_id+'/retry-data-delivery';}
 throw Error('未登记的业务数据操作，原请求保留。');
}
export function checkedBody(kind,b){
 need(uuid(b?.request_id));
 if(['create_source','edit_source'].includes(kind)){
  need(exact(b,'request_id,name,kind,endpoint,secret_ref'+(kind==='edit_source'?',expected_version':''))&&text(b.name)&&Object.hasOwn(BD_KINDS,b.kind));
  need(b.endpoint===null||text(b.endpoint));need(b.secret_ref===null||typeof b.secret_ref==='string'&&/^GA_CONNECTOR_SECRET_[A-Z0-9_]+$/.test(b.secret_ref),'凭证只能填写服务器已配置的凭证引用，不能填写密钥。');
  if(b.kind==='http_json'){need(text(b.endpoint));need(!/\/\/[^/\s]*@|[?&](?:api_key|key|token|access_token|password|secret|signature|authorization)=/i.test(b.endpoint),'地址不能包含账号密码或密钥，请使用服务器凭证引用。');}
  else need(b.endpoint===null&&b.secret_ref===null);
 }else if(kind==='upload')need(exact(b,'request_id,expected_version,file_name,file_size,file_sha256')&&text(b.file_name)&&positive(b.file_size)&&b.file_size<=20*1024*1024&&SHA.test(b.file_sha256));
 else if(kind==='read')need(exact(b,'request_id,expected_version'));
 else if(kind==='binding'){
  need(exact(b,'request_id,source_id,source_expected_version,read_id,instance_id,release_id,object_type,schema_hash,record_set,external_id_path,fields'));
  for(const k of ['source_id','read_id','instance_id','release_id'])need(uuid(b[k]));
  need(positive(b.source_expected_version)&&text(b.object_type)&&SHA.test(b.schema_hash)&&typeof b.record_set==='string'&&b.record_set.length<=1000&&text(b.external_id_path)&&Array.isArray(b.fields)&&b.fields.length>0);
  need(new Set(b.fields.map(f=>f.target)).size===b.fields.length);
  b.fields.forEach(f=>need(exact(f,'target,source,conversion')&&text(f.target)&&text(f.source)&&Object.hasOwn(BD_CONVERSIONS,f.conversion)));
 }else if(kind==='preview')need(exact(b,'request_id,read_id')&&uuid(b.read_id));
 else if(kind==='confirm')need(exact(b,'request_id,expected_version,confirm_hash,accept_partial')&&SHA.test(b.confirm_hash)&&typeof b.accept_partial==='boolean');
 else need(exact(b,'request_id,expected_version,reason')&&text(b.reason)&&b.reason.length<=2000);
 if(Object.hasOwn(b,'expected_version'))need(positive(b.expected_version));return b;
}
export function checkedPending(value,scope){
 need(exact(value,'schema,scope,kind,context,method,body,raw,sha256,rejected')&&value.schema==='business-data-outbox-v204'&&canonical(value.scope)===canonical(scope));
 need(value.method===(value.kind==='edit_source'?'PUT':'POST')&&typeof value.raw==='string'&&SHA.test(value.sha256)&&canonical(JSON.parse(value.raw))===value.raw&&value.raw===canonical(value.body));
 checkedBody(value.kind,value.body);commandPath(value.kind,value.context);need(value.rejected===null||exact(value.rejected,'status,message')&&[400,403,409,413,415,422].includes(value.rejected.status)&&typeof value.rejected.message==='string');return clone(value);
}
export function checkedRecovery(value,p){
 need(value?.schema==='business-data-command-v204'&&typeof value.found==='boolean');
 if(!value.found){need(value.body===null&&value.result===null);return null;}
 need(canonical(value.body)===p.raw&&value.result&&typeof value.result==='object');
 const r=value.result;need(uuid(r.id));
 if(['edit_source','pause','resume_source'].includes(p.kind))need(r.id===p.context.source_id);
 if(['read','upload','retry_read'].includes(p.kind)){need(r.source_id===p.context.source_id);if(p.kind==='retry_read')need(r.id===p.context.read_id);}
 if(p.kind==='binding')need(r.source_id===p.body.source_id&&r.read_id===p.body.read_id&&r.instance_id===p.body.instance_id&&r.release_id===p.body.release_id&&r.object_type===p.body.object_type&&r.schema_hash===p.body.schema_hash);
 if(p.kind==='preview')need(r.binding_id===p.context.binding_id&&r.read_id===p.body.read_id&&r.instance_id===p.scope.instance_id);
 if(p.kind==='retry_ack')need(r.id===p.context.row_id&&r.data_delivery&&Number.isSafeInteger(r.data_delivery.version)&&r.data_delivery.version>p.body.expected_version&&['pending_delivery','received','blocked_delivery'].includes(r.data_delivery.status));
 if(p.kind==='retry_day_ack')need(r.id===p.context.event_id&&r.source_id===p.context.source_id&&Number.isSafeInteger(r.version)&&r.version>p.body.expected_version&&r.data_delivery?.event_id===p.context.event_id&&r.data_delivery.version===r.version&&['pending_delivery','received','blocked_delivery'].includes(r.data_delivery.status));
 if(['confirm','cancel','resume','retry_ack'].includes(p.kind)&&p.kind!=='retry_ack')need(r.id===p.context.import_id);
 return clone(r);
}
export function locationLabel(value){if(!value||typeof value!=='object')return '原资料中的对应记录';return Object.entries(value).filter(([,v])=>typeof v==='string'||typeof v==='number').map(([k,v])=>({'sheet':'工作表','row':'行','line':'行','index':'记录位置','pointer':'记录位置','record_set':'数据组'}[k]||'位置')+' '+v).join(' · ')||'原资料中的对应记录';}
