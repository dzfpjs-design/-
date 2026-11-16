// Literal retrieval and frozen source citations, without model or fact authority.
export const RD_BASE='/runtime-documents';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/,SHA=/^[a-f0-9]{64}$/;
export const need=(ok,message='原资料操作或引用依据无法核对，原输入和请求保留。')=>{if(!ok)throw Error(message);};
export const clone=x=>JSON.parse(JSON.stringify(x));
export const uuid=x=>typeof x==='string'&&UUID.test(x);
const integer=x=>Number.isSafeInteger(x)&&x>=1,text=(x,max=2000)=>typeof x==='string'&&x.length<=max&&!!x.trim();
const exact=(x,fields)=>x&&typeof x==='object'&&!Array.isArray(x)&&Object.keys(x).sort().join(',')===fields.split(',').sort().join(',');
export function canonical(x){if(Array.isArray(x))return '['+x.map(canonical).join(',')+']';if(x&&typeof x==='object')return '{'+Object.keys(x).sort().map(k=>JSON.stringify(k)+':'+canonical(x[k])).join(',')+'}';need(x===null||typeof x==='string'||typeof x==='boolean'||typeof x==='number'&&Number.isFinite(x));return JSON.stringify(x);}
export function commandPath(kind,scope,c={}){
 need(uuid(scope.actor_id)&&uuid(scope.workspace_id)&&uuid(scope.instance_id));
 if(kind==='upload_document')return RD_BASE+'/instances/'+scope.instance_id+'/documents';
 if(kind==='upload_version'){need(uuid(c.document_id));return RD_BASE+'/documents/'+c.document_id+'/versions';}
 if(kind==='search')return RD_BASE+'/instances/'+scope.instance_id+'/searches';
 need(uuid(c.version_id));
 if(kind==='retry'||kind==='cancel')return RD_BASE+'/versions/'+c.version_id+'/'+kind;
 if(kind==='retry_receipt'){need(uuid(c.event_id));return RD_BASE+'/versions/'+c.version_id+'/receipts/'+c.event_id+'/retry';}
 throw Error('未登记的资料操作，原请求保留。');
}
export function checkedBody(kind,body){
 need(uuid(body?.request_id));
 if(kind==='upload_document'||kind==='upload_version'){
  need(exact(body,'request_id,release_id,title,file_name,file_size,file_sha256'+(kind==='upload_version'?',expected_version,prior_version_id':''))&&uuid(body.release_id)&&text(body.title,200)&&text(body.file_name,250)&&!/[\\/\u0000-\u001f]/u.test(body.file_name)&&/\.(pdf|docx|txt|md)$/i.test(body.file_name)&&Number.isSafeInteger(body.file_size)&&body.file_size>0&&body.file_size<=20*1024*1024&&SHA.test(body.file_sha256));
  if(kind==='upload_version')need(integer(body.expected_version)&&uuid(body.prior_version_id));
 }else if(kind==='search')need(exact(body,'request_id,release_id,keyword')&&uuid(body.release_id)&&text(body.keyword,200));
 else need(['retry','cancel','retry_receipt'].includes(kind)&&exact(body,'request_id,expected_version,reason')&&integer(body.expected_version)&&text(body.reason));
 return body;
}
export function checkedDelivery(value){
 if(value===null)return null;
 need(value&&uuid(value.event_id)&&integer(value.version)&&['pending_delivery','received','blocked_delivery','integrity_error'].includes(value.status)&&typeof value.verified==='boolean'&&typeof value.can_retry==='boolean');
 need(value.error===null||typeof value.error==='string');need(value.previous_error===null||typeof value.previous_error==='string');return value;
}
export function checkedVersion(value,scope){
 need(value&&uuid(value.id)&&uuid(value.document_id)&&value.instance_id===scope.instance_id&&integer(value.version)&&integer(value.sequence)&&typeof value.name==='string'&&['queued','parsing','parsed','needs_input','failed','cancelled'].includes(value.status)&&typeof value.raw_saved==='boolean'&&(value.raw_sha256===null||SHA.test(value.raw_sha256))&&(value.parse_hash===null||SHA.test(value.parse_hash))&&Number.isSafeInteger(value.blocks_count)&&value.blocks_count>=0&&Array.isArray(value.warnings)&&value.warnings.every(x=>typeof x==='string')&&typeof value.can_retry==='boolean'&&typeof value.can_cancel==='boolean'&&(value.latest_attempt_id===null||uuid(value.latest_attempt_id)));
 need(value.stopping===undefined||typeof value.stopping==='boolean');if(value.stopping)need(value.status==='cancelled'&&value.can_retry===false,'原解析尚未停止，不能显示为可重试。');checkedDelivery(value.delivery);return value;
}
export function checkedDocument(value,scope){need(value&&uuid(value.id)&&integer(value.version)&&value.instance_id===scope.instance_id&&typeof value.title==='string'&&uuid(value.latest_version_id));checkedVersion(value.latest,scope);need(value.latest.id===value.latest_version_id&&value.latest.document_id===value.id);return value;}
export function checkedSearch(value,scope){need(value&&uuid(value.id)&&integer(value.version)&&value.instance_id===scope.instance_id&&text(value.keyword,200)&&['queued','searching','completed','failed'].includes(value.status)&&Array.isArray(value.hits));if(value.coverage!==null&&value.coverage!==undefined){const c=value.coverage;need(['selected','eligible','unavailable','document_limit','hit_limit'].every(k=>Number.isSafeInteger(c[k])&&c[k]>=0)&&typeof c.limited==='boolean'&&(c.notice===null||typeof c.notice==='string'));}else need(value.status!=='completed');for(const hit of value.hits){need(hit.instance_id===scope.instance_id&&typeof hit.text==='string'&&typeof hit.title==='string'&&typeof hit.name==='string'&&integer(hit.sequence)&&SHA.test(hit.block_hash)&&typeof hit.text_is_excerpt==='boolean'&&exact(hit.char_span,'start,end')&&Number.isSafeInteger(hit.char_span.start)&&Number.isSafeInteger(hit.char_span.end)&&hit.char_span.start>=0&&hit.char_span.end>hit.char_span.start);citationPath(hit,{requireURL:true});checkedDelivery(hit.delivery);}return value;}
export function checkedPending(value,scope){
 need(exact(value,'schema,scope,kind,context,body,raw,sha256,rejected')&&value.schema==='runtime-document-outbox-v205'&&canonical(value.scope)===canonical(scope)&&typeof value.raw==='string'&&value.raw===canonical(value.body)&&value.raw===canonical(JSON.parse(value.raw))&&SHA.test(value.sha256));checkedBody(value.kind,value.body);commandPath(value.kind,scope,value.context);
 need(value.rejected===null||exact(value.rejected,'status,message')&&[400,403,409,413,415,422].includes(value.rejected.status)&&typeof value.rejected.message==='string');return clone(value);
}
export function checkedRecovery(value,p){
 need(value?.schema==='runtime-document-command-v205'&&typeof value.found==='boolean');if(!value.found){need(value.body===null&&value.result===null);return null;}
 need(canonical(value.body)===p.raw);const r=value.result;
 if(p.kind==='upload_document'||p.kind==='upload_version'){checkedDocument(r,p.scope);need(r.title===p.body.title&&r.latest.name===p.body.file_name&&r.latest.raw_sha256===p.body.file_sha256);if(p.kind==='upload_version')need(r.id===p.context.document_id&&r.version>p.body.expected_version&&r.latest.id!==p.body.prior_version_id);}
 else if(p.kind==='search'){checkedSearch(r,p.scope);need(r.keyword===p.body.keyword);}
 else{checkedVersion(r,p.scope);need(r.id===p.context.version_id&&r.document_id===p.context.document_id);if(p.kind==='retry_receipt')need(r.delivery?.event_id===p.context.event_id&&r.delivery.version>p.body.expected_version);else need(r.version>p.body.expected_version);}
 return clone(r);
}
export function citationPath(value,{requireURL=false}={}){
 need(value&&uuid(value.version_id)&&uuid(value.document_id)&&uuid(value.instance_id)&&uuid(value.attempt_id)&&text(value.block_id,250)&&SHA.test(value.parse_hash)&&SHA.test(value.raw_sha256));
 const path=RD_BASE+'/versions/'+value.version_id+'/citations/'+encodeURIComponent(value.block_id),query='?attempt_id='+value.attempt_id+'&expected_parse_hash='+value.parse_hash;
 if(requireURL||value.citation_url!==undefined){need(typeof value.citation_url==='string'&&value.citation_url.startsWith('/')&&!value.citation_url.startsWith('//'));const url=new URL(value.citation_url,'https://runtime-documents.invalid');need(url.origin==='https://runtime-documents.invalid'&&!url.hash&&(url.pathname===path||url.pathname==='/api'+path)&&[...url.searchParams.keys()].sort().join(',')==='attempt_id,expected_parse_hash'&&url.searchParams.get('attempt_id')===value.attempt_id&&url.searchParams.get('expected_parse_hash')===value.parse_hash);}
 return path+query;
}
export function checkedCitation(value,expected,scope){
 citationPath(value);need(value.instance_id===scope.instance_id&&['version_id','document_id','attempt_id','block_id','parse_hash','raw_sha256'].every(k=>value[k]===expected[k])&&SHA.test(value.block_hash)&&(!expected.block_hash||value.block_hash===expected.block_hash)&&typeof value.text==='string'&&typeof value.title==='string'&&typeof value.name==='string'&&integer(value.sequence));if(expected.text!==undefined){let text=value.text;if(expected.text_is_excerpt){need(exact(expected.char_span,'start,end')&&Number.isSafeInteger(expected.char_span.start)&&Number.isSafeInteger(expected.char_span.end)&&expected.char_span.start>=0&&expected.char_span.end>expected.char_span.start);let index=0,excerpt='';for(const char of value.text){if(index>=expected.char_span.start&&index<expected.char_span.end)excerpt+=char;if(++index>=expected.char_span.end)break;}need(index===expected.char_span.end);text=excerpt;}need(text===expected.text,'引用原文与已保存的原结果不一致，未显示替代正文。');}checkedDelivery(value.delivery);return value;
}
export function locationLabel(value){if(!value||typeof value!=='object')return '原资料中的对应位置';return Object.entries(value).filter(([,v])=>['string','number'].includes(typeof v)).map(([k,v])=>({'page':'页','line':'行','paragraph':'段','body_index':'正文位置','row':'行','table':'表格','sheet':'工作表'}[k]||'位置')+' '+v).join(' · ')||'原资料中的对应位置';}
