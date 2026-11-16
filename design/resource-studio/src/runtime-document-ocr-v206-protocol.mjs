// Local OCR is a separate, explicitly unreviewed corpus. No business fact authority.
import {canonical,clone,uuid,checkedDelivery} from './runtime-document-v205-protocol.mjs';
export {canonical,clone,uuid,checkedDelivery};
export const OCR_BASE='/runtime-document-ocr';
export const need=(ok,message='识别操作或原页依据无法核对，原输入和请求保留。')=>{if(!ok)throw Error(message);};
export const hash=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const positive=value=>Number.isSafeInteger(value)&&value>0;
const count=value=>Number.isSafeInteger(value)&&value>=0;
const text=(value,max=2000)=>typeof value==='string'&&!!value.trim()&&value.length<=max;
const exact=(value,keys)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===keys.split(',').sort().join(',');
export function parsePages(value){
 need(typeof value==='string'&&value.length<=1000,'页码填写过长。');const pages=[];
 for(const part of value.split(/[,，、\s]+/u).filter(Boolean)){const match=/^(\d+)(?:[-—](\d+))?$/.exec(part);need(match,'请填写页码或范围，例如 1,3-5。');const first=Number(match[1]),last=Number(match[2]||match[1]);need(positive(first)&&last>=first&&last<=500&&last-first<100,'页码须在1至500之间，每次最多100页。');for(let page=first;page<=last;page++){need(!pages.includes(page),'页码不能重复。');pages.push(page);need(pages.length<=100,'每次最多识别100页。');}}
 need(pages.length,'请填写要识别的页码。');return pages;
}
export function commandPath(kind,scope,context={}){
 need(['actor_id','workspace_id','instance_id','document_version_id'].every(key=>uuid(scope[key])));
 if(kind==='start')return OCR_BASE+'/versions/'+scope.document_version_id+'/runs';
 if(kind==='search')return OCR_BASE+'/instances/'+scope.instance_id+'/searches';
 if(kind==='cancel_run'||kind==='retry_run'){need(uuid(context.run_id));return OCR_BASE+'/runs/'+context.run_id+'/'+(kind==='cancel_run'?'cancel':'retry');}
 if(kind==='retry_page'){need(uuid(context.page_id)&&uuid(context.run_id));return OCR_BASE+'/pages/'+context.page_id+'/retry';}
 if(kind==='retry_receipt'){need(uuid(context.result_id)&&uuid(context.event_id));return OCR_BASE+'/results/'+context.result_id+'/receipts/'+context.event_id+'/retry';}
 throw Error('未登记的识别操作。');
}
export function checkedBody(kind,value){
 need(uuid(value?.request_id));
 if(kind==='start'){need(exact(value,'request_id,release_id,profile_hash,range_mode,pages')&&uuid(value.release_id)&&hash(value.profile_hash)&&['missing_text','all','pages'].includes(value.range_mode)&&Array.isArray(value.pages)&&value.pages.length<=100&&value.pages.every(page=>positive(page)&&page<=500)&&new Set(value.pages).size===value.pages.length);need(value.range_mode==='pages'?value.pages.length>0:value.pages.length===0);}
 else if(kind==='search')need(exact(value,'request_id,release_id,keyword')&&uuid(value.release_id)&&text(value.keyword,200));
 else need(['cancel_run','retry_run','retry_page','retry_receipt'].includes(kind)&&exact(value,'request_id,expected_version,reason')&&positive(value.expected_version)&&text(value.reason));
 return value;
}
export function checkedPending(value,scope){
 need(exact(value,'schema,scope,kind,context,body,raw,sha256,rejected')&&value.schema==='runtime-document-ocr-outbox-v206'&&canonical(value.scope)===canonical(scope)&&typeof value.raw==='string'&&value.raw===canonical(value.body)&&hash(value.sha256));checkedBody(value.kind,value.body);commandPath(value.kind,scope,value.context);
 need(value.rejected===null||exact(value.rejected,'status,message')&&[400,403,409,413,415,422].includes(value.rejected.status)&&typeof value.rejected.message==='string');return clone(value);
}
export function checkedRun(value,scope){
 need(value&&uuid(value.id)&&positive(value.version)&&value.document_version_id===scope.document_version_id&&value.instance_id===scope.instance_id&&typeof value.name==='string'&&hash(value.profile?.manifest_hash)&&typeof value.profile.name==='string'&&['missing_text','all','pages'].includes(value.range_mode)&&Array.isArray(value.requested_pages)&&value.requested_pages.every(positive)&&['queued','preparing','running','completed','partial','failed','cancelled'].includes(value.status)&&(value.page_count===null||positive(value.page_count))&&count(value.selected_count)&&value.counts&&['queued','running','text','no_text','failed','cancelled'].every(key=>count(value.counts[key]))&&typeof value.can_retry==='boolean'&&typeof value.can_cancel==='boolean'&&Array.isArray(value.previous_errors)&&value.previous_errors.every(error=>typeof error==='string'));
 need(Object.keys(value.counts).sort().join(',')==='cancelled,failed,no_text,queued,running,text'&&Object.values(value.counts).reduce((sum,n)=>sum+n,0)===value.selected_count,'页数与识别进度暂无法核对，请刷新。');need(value.stopping===undefined||typeof value.stopping==='boolean');if(value.coverage)need((value.coverage.unselected_count===null||count(value.coverage.unselected_count))&&typeof value.coverage.selected_processing_complete==='boolean'&&value.coverage.whole_document_text_verified===false&&Array.isArray(value.coverage.warnings)&&value.coverage.warnings.every(warning=>typeof warning==='string'));return value;
}
export function imagePath(value){need(uuid(value?.id||value?.result_id));const path=OCR_BASE+'/results/'+(value.result_id||value.id)+'/image';need(value.image_url===path||value.image_url==='/api'+path);return path;}
export function checkedImage(value){
 const hasImage=value.image_url!==null;need(hasImage?(hash(value.image_sha256)&&positive(value.image_width)&&positive(value.image_height)):(value.image_sha256===null&&value.image_width===null&&value.image_height===null));if(hasImage)imagePath(value);return value;
}
export function checkedResult(value){need(value&&uuid(value.id)&&positive(value.ordinal)&&['processing','text','no_text','failed','cancelled'].includes(value.status)&&typeof value.text_excerpt==='string'&&count(value.regions_count)&&(value.result_hash===null||hash(value.result_hash)));checkedImage(value);checkedDelivery(value.delivery);return value;}
export function checkedPage(value,runID=null){need(value&&uuid(value.id)&&positive(value.version)&&uuid(value.run_id)&&(!runID||value.run_id===runID)&&positive(value.page_number)&&['queued','running','text','no_text','failed','cancelled'].includes(value.status)&&typeof value.can_retry==='boolean'&&(value.latest_result_id===null||uuid(value.latest_result_id))&&Array.isArray(value.results)&&value.results.length<=100&&(value.results_has_more===undefined||typeof value.results_has_more==='boolean'));value.results.forEach(checkedResult);need(value.latest_result_id===null||value.results.some(result=>result.id===value.latest_result_id));return value;}
export function checkedBox(value,width,height){need(Array.isArray(value)&&value.length===4&&value.every(Number.isFinite)&&value[0]>=0&&value[1]>=0&&value[2]>0&&value[3]>0&&value[0]+value[2]<=width+0.00001&&value[1]+value[3]<=height+0.00001,'文字框与原页尺寸不一致，未显示替代定位。');return value;}
export function checkedRegion(value,image){need(value&&/^r[1-9]\d*$/.test(value.id)&&typeof value.text==='string'&&value.unreviewed===true&&(value.score===null||Number.isFinite(value.score)));checkedBox(value.box,image.image_width,image.image_height);return value;}
export function citationPath(value,{requireURL=false}={}){
 need(value&&uuid(value.result_id)&&/^r[1-9]\d*$/.test(value.region_id)&&hash(value.result_hash));const path=OCR_BASE+'/results/'+value.result_id+'/citations/'+value.region_id,query='?expected_result_hash='+value.result_hash;
 if(requireURL||value.citation_url!==undefined){need(typeof value.citation_url==='string'&&value.citation_url.startsWith('/')&&!value.citation_url.startsWith('//'));const url=new URL(value.citation_url,'https://runtime-ocr.invalid');need(url.origin==='https://runtime-ocr.invalid'&&!url.hash&&(url.pathname===path||url.pathname==='/api'+path)&&[...url.searchParams.keys()].join(',')==='expected_result_hash'&&url.searchParams.get('expected_result_hash')===value.result_hash);}
 return path+query;
}
export function checkedCitation(value,expected,scope){
 citationPath(value);need(value.instance_id===scope.instance_id&&['document_version_id','document_id','run_id','page_id','result_id'].every(key=>uuid(value[key]))&&text(value.source_name,250)&&positive(value.version_sequence)&&positive(value.page_number)&&hash(value.raw_sha256)&&hash(value.profile?.manifest_hash)&&typeof value.profile.name==='string'&&typeof value.text==='string'&&value.unreviewed===true);checkedImage(value);checkedBox(value.box,value.image_width,value.image_height);
 for(const key of ['document_version_id','document_id','source_name','version_sequence','run_id','page_id','page_number','result_id','region_id','result_hash','raw_sha256','image_sha256','image_width','image_height'])if(expected[key]!==undefined)need(value[key]===expected[key]);
 if(expected.profile)need(canonical(value.profile)===canonical(expected.profile));if(expected.box)need(canonical(value.box)===canonical(expected.box));
 if(expected.text!==undefined){let actual=value.text;if(expected.text_is_excerpt){const span=expected.char_span;need(span&&count(span.start)&&positive(span.end)&&span.end>span.start);let index=0;actual='';for(const character of value.text){if(index>=span.start&&index<span.end)actual+=character;if(++index>=span.end)break;}need(index===span.end);}need(actual===expected.text,'识别文字与原结果不一致，未显示替代引用。');}return value;
}
export function checkedSearch(value,scope){
 need(value&&uuid(value.id)&&positive(value.version)&&value.instance_id===scope.instance_id&&text(value.keyword,200)&&['queued','searching','completed','failed'].includes(value.status)&&Array.isArray(value.hits)&&value.hits.length<=100);
 const coverage=value.coverage;need(coverage&&['selected_documents','eligible_results','unavailable_results','document_limit','result_limit','region_limit','hit_limit','excerpt_limit'].every(key=>count(coverage[key]))&&typeof coverage.limited==='boolean'&&(coverage.notice===null||typeof coverage.notice==='string'));
 for(const hit of value.hits){need(hit.instance_id===scope.instance_id&&hit.unreviewed===true&&typeof hit.text==='string'&&hit.text.length<=2400&&Array.from(hit.text).length<=1200&&hash(hit.result_hash)&&text(hit.source_name,250)&&positive(hit.version_sequence)&&positive(hit.page_number)&&['document_version_id','document_id','run_id','page_id'].every(key=>uuid(hit[key]))&&hash(hit.raw_sha256)&&hash(hit.profile?.manifest_hash)&&typeof hit.profile.name==='string'&&typeof hit.text_is_excerpt==='boolean'&&exact(hit.char_span,'start,end')&&count(hit.char_span.start)&&positive(hit.char_span.end)&&hit.char_span.end>hit.char_span.start);citationPath(hit,{requireURL:true});checkedImage(hit);checkedBox(hit.box,hit.image_width,hit.image_height);}return value;
}
export function checkedRecovery(value,pending){
 need(value?.schema==='runtime-document-ocr-command-v206'&&typeof value.found==='boolean');if(!value.found){need(value.body===null&&value.result===null);return null;}need(canonical(value.body)===pending.raw);const result=value.result;
 if(pending.kind==='search'){checkedSearch(result,pending.scope);need(result.keyword===pending.body.keyword);}
 else if(pending.kind==='start'||pending.kind==='cancel_run'||pending.kind==='retry_run'){checkedRun(result,pending.scope);if(pending.kind==='start')need(result.profile.manifest_hash===pending.body.profile_hash&&result.range_mode===pending.body.range_mode&&canonical(result.requested_pages)===canonical(pending.body.pages));else need(result.id===pending.context.run_id&&result.version>pending.body.expected_version);}
 else if(pending.kind==='retry_page'){checkedPage(result,pending.context.run_id);need(result.id===pending.context.page_id&&result.version>pending.body.expected_version);}
 else{checkedResult(result);need(result.id===pending.context.result_id&&result.delivery?.event_id===pending.context.event_id&&result.delivery.version>pending.body.expected_version);}return clone(result);
}
