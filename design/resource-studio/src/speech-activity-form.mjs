export const vadUploadKey=(workspace,user)=>'ga-vad-upload:'+workspace+':'+user;
export const vadReviewKey=(workspace,user,id)=>'ga-vad-review:'+workspace+':'+user+':'+id;
export function vadRequestValue(sha,configuration){
 if(!/^[a-f0-9]{64}$/.test(sha)||!['mono','mix','1','2','3','4','5','6','7','8'].includes(configuration.channel)||!Number.isFinite(configuration.threshold)||configuration.threshold<.1||configuration.threshold>.9)throw Error('音频摘要、通道或检测门槛无效。');
 return JSON.stringify({configuration:{channel:configuration.channel,threshold:configuration.threshold},input_sha256:sha});
}
export function confirmVAD(row,pending){
 if(!pending||row.request_id!==pending.request_id||row.input_sha256!==pending.input_sha256||row.request_hash!==pending.request_hash||row.size_bytes!==pending.size||row.configuration.channel!==pending.configuration.channel||row.configuration.threshold!==pending.configuration.threshold)throw Error('服务器记录与原音频或语音区间设置不一致，请保留原请求核对。');
 return row;
}
export function reviewValue(row,local){
 const value=local??row.review_draft??row.review??{decisions:row.result.activity.intervals.map(()=> 'uncertain'),note:''};
 if(!Array.isArray(value.decisions)||value.decisions.length!==row.result.activity.intervals.length||value.decisions.some(d=>!['speech','not_speech','uncertain'].includes(d))||typeof value.note!=='string'||value.note.length>5000)throw Error('核查草稿与保存的区间不一致，请保留草稿并重新读取记录。');
 return {decisions:value.decisions,note:value.note};
}
export const sameReview=(a,b)=>!!a&&!!b&&a.note===b.note&&JSON.stringify(a.decisions)===JSON.stringify(b.decisions);
