export const segmentKey=(ws,user,source)=>'ga-segment-request:'+ws+':'+user+':'+source;
export const segmentReviewKey=(ws,user,id)=>'ga-segment-review:'+ws+':'+user+':'+id;
export function segmentRequestValue(hash,configuration){
 if(!/^[a-f0-9]{64}$/.test(hash)||!Array.isArray(configuration.indexes)||configuration.indexes.length>250||configuration.indexes.some((v,i)=>!Number.isInteger(v)||v<0||i&&v<=configuration.indexes[i-1])||!['auto','zh','yue','en','ja','ko'].includes(configuration.language))throw Error('语音区间来源、顺序或识别语言无效。');
 return JSON.stringify({configuration:{indexes:configuration.indexes,language:configuration.language},source_snapshot_hash:hash});
}
export function confirmSegments(row,intent){
 if(!intent||row.request_id!==intent.request_id||row.source_run_id!==intent.source_run_id||row.source_snapshot_hash!==intent.source_snapshot_hash||row.request_hash!==intent.request_hash||segmentRequestValue(row.source_snapshot_hash,row.configuration)!==segmentRequestValue(intent.source_snapshot_hash,intent.configuration))throw Error('转写回执与选定原件、区间或语言不一致，请保留原请求。');return row;
}
export function segmentReviewValue(row,local){
 const value=local??row.review_draft??row.review??{texts:row.segments.map(s=>s.text),note:''};
 if(!Array.isArray(value.texts)||value.texts.length!==row.segments.length||value.texts.some(t=>typeof t!=='string'||t.length>6000)||value.texts.reduce((n,t)=>n+t.length,0)>12000||typeof value.note!=='string'||value.note.length>5000)throw Error('核对草稿与已保存转写段不一致，原草稿保留。');return {texts:value.texts,note:value.note};
}
export const sameSegmentReview=(a,b)=>!!a&&!!b&&a.note===b.note&&JSON.stringify(a.texts)===JSON.stringify(b.texts);
