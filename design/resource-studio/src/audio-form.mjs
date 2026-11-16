export const audioKey=(workspace,user)=>'ga-audio-upload:'+workspace+':'+user;
export const audioReviewKey=(workspace,user,id)=>'ga-audio-review:'+workspace+':'+user+':'+id;
export function audioRequestValue(sha,configuration){
 if(!/^[a-f0-9]{64}$/.test(sha)||!['mono','mix','1','2','3','4','5','6','7','8'].includes(configuration.channel)||!['auto','zh','en','yue','ja','ko'].includes(configuration.language))throw Error('音频摘要、通道或语言无效。');
 return JSON.stringify({configuration:{channel:configuration.channel,language:configuration.language},input_sha256:sha});
}
export function confirmAudio(row,pending){
 if(!pending||row.request_id!==pending.request_id||row.input_sha256!==pending.input_sha256||row.request_hash!==pending.request_hash||row.size_bytes!==pending.size||JSON.stringify(row.configuration)!==JSON.stringify(pending.configuration))throw Error('服务器记录与原音频请求不一致，请保留原请求核对。');
 return row;
}
