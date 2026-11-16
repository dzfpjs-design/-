export const reviewChoices=[{value:'confirmed',label:'确认原输入与提取回执一致'},{value:'rejected',label:'拒绝这份资料，保留原因'}];
export function restoreDecision(saved,choices){
 if(!choices?.length)return '';
 const value=saved?.pending?.decision??saved?.decision??choices[0].value;
 if(!choices.some(c=>c.value===value))throw Error('核查结论草稿无法恢复，请保留原记录');
 return value;
}
export function acquisitionLabel(row){
 if(row.status==='authorized')return row.parent_ended?'原目标已结束，不会继续下发':'已授权，等待下发';
 if(row.status==='unknown'||row.status==='dispatching')return '设备动作结果未知';
 if(row.status==='awaiting_observation')return row.device_action_terminal?'设备已确认结束，等待合格资料归集':'设备已接受请求，尚未确认结束';
 return ({succeeded:'原采集资料已核对并归集',rejected:'设备请求已永久结束，未取得合格资料',cancelled:'已停止此采集的后续下发',blocked_resource:'设备当前不可用',blocked_connector:'设备接口未接通'})[row.status]||'需要核对原采集状态';
}
export function reviewBody(record,decision){
 if(!['confirmed','rejected'].includes(decision))throw Error('请选择确认或拒绝结论');
 return {expected_version:record.version,expected_input_hash:record.input_hash,decision};
}
