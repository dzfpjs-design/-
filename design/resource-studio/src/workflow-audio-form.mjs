export function audioFields(required,ids){
 if(!required)return {};
 if(!Array.isArray(ids)||ids.length!==1||typeof ids[0]!=='string'||!ids[0])throw Error('请选择一项已保存的任务音频。');
 return {audio_ids:[...ids]};
}
export function confirmAudioAssociation(row,intent){
 if(row.request_id!==intent.request_id||row.snapshot_hash!==intent.source_snapshot_hash||row.snapshot.source_run_id!==intent.source_run_id)throw Error('音频关联与原请求不一致，原请求保留。');
 return row;
}
