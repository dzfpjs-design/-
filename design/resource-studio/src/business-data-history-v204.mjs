// Read-only view validation; no command construction, storage or source I/O.
const keys=(value,names)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join(',')===names.split(',').sort().join(',');
const text=(value,max)=>typeof value==='string'&&value.length<=max;
function need(value){if(!value)throw Error('处理记录的范围、内容或分页无法核对，已保留此前读取的记录。');}
export function checkedBusinessDataHistoryV204(value,type,id){
 need(keys(value,'schema,subject_type,subject_id,entries,next_cursor,continuation')&&value.schema==='business-data-history-v204'&&value.subject_type===type&&value.subject_id===id&&['read','import'].includes(type));
 need(Array.isArray(value.entries)&&value.entries.length<=20&&new Set(value.entries.map(e=>e?.id)).size===value.entries.length);
 for(const entry of value.entries)need(keys(entry,'id,at,actor,label,detail,status')&&text(entry.id,500)&&entry.id.length>0&&text(entry.at,80)&&/^\d{4}-\d\d-\d\dT/.test(entry.at)&&/(?:Z|[+-]\d\d:\d\d)$/.test(entry.at)&&Number.isFinite(Date.parse(entry.at))&&text(entry.actor,500)&&text(entry.label,1000)&&text(entry.detail,10000)&&['info','succeeded','failed','request'].includes(entry.status));
 need(value.next_cursor===null||text(value.next_cursor,8000)&&value.next_cursor.length>0);
 need(keys(value.continuation,'count,notice')&&(value.continuation.count===null||Number.isSafeInteger(value.continuation.count)&&value.continuation.count>=0)&&(value.continuation.notice===null||text(value.continuation.notice,10000)));
 return value;
}
export function businessDataHistoryPathV204(type,id,cursor=null){
 need(['read','import'].includes(type)&&typeof id==='string'&&/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(id));
 need(cursor===null||text(cursor,8000)&&cursor.length>0);
 return '/business-data/'+(type==='read'?'reads':'imports')+'/'+id+'/history'+(cursor===null?'':'?cursor='+encodeURIComponent(cursor));
}
