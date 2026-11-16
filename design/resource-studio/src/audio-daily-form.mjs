export function confirmDailyDetail(record,value){
 if(!value?.record||['key','kind','id','input_sha256'].some(k=>record[k]!==value.record[k]))throw Error('归集依据与当前原件不一致，请保留原记录重新核对。');
 return value;
}
export function dailyRetryPath(record,value){
 confirmDailyDetail(record,value);
 if(record.task_id||!value.integrity_verified||!['failed','cancelled'].includes(value.current_status))throw Error('请通过原业务任务或核对当前状态后重试，不能替换原请求。');
 const base={transcription:'/audio/',activity:'/audio-activity/',segments:'/audio-segments/'}[record.kind];
 if(!base||!Number.isInteger(value.current_version)||value.current_version<1)throw Error('原处理请求的版本或类型无法核对。');
 return base+record.id+'/retry';
}
