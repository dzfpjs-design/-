export const pairDraftKey=(workspace,user)=>'ga-geo-pair:'+workspace+':'+user;
export const pairReviewKey=(workspace,user,id)=>'ga-geo-pair-review:'+workspace+':'+user+':'+id;
export function emptyPair(){return {before:'',after:'',roi:['','','',''],alignment:'same_grid',threshold:'40',confirmed:false,pending:null};}
export function readPair(storage,key){const raw=storage.getItem(key);if(!raw)return emptyPair();const value=JSON.parse(raw);if(!value||typeof value.before!=='string'||typeof value.after!=='string'||!Array.isArray(value.roi)||value.roi.length!==4||typeof value.confirmed!=='boolean'||!['same_grid','reproject_after_to_before'].includes(value.alignment)||value.pending&&(!/^[a-zA-Z0-9_-]{12,100}$/.test(value.pending.request_id)))throw Error('比较草稿或原请求无法读取；原记录保留，请勿另建请求。');return value;}
export function pairBody(form,scenes,id){
 const a=scenes.find(s=>s.id===form.before),b=scenes.find(s=>s.id===form.after);
 if(!a||!b||a.status!=='ready'||b.status!=='ready')throw Error('请选择两幅已保存RGB预览的影像。');
 if(a.id===b.id||a.input_sha256===b.input_sha256)throw Error('A、B原件相同，请选择不同影像。');
 if(!a.configuration?.map_image_confirmed||!b.configuration?.map_image_confirmed)throw Error('请先在各影像中确认地图参考。');
 if(!form.confirmed)throw Error('请核对RGB含义与数值范围，并确认可以比较。');
 const number=v=>{if(!/^\d+$/.test(String(v)))throw Error('范围与阈值需填写非负整数。');return Number(v);};
 const roi=form.roi.map(number),threshold=number(form.threshold),[x0,y0,x1,y1]=roi;
 if(!(0<=x0&&x0<x1&&x1<=a.metadata.width&&0<=y0&&y0<y1&&y1<=a.metadata.height))throw Error('比较范围超出A影像的像素边界。');
 if((x1-x0)*(y1-y0)>16000000)throw Error('范围超过1600万像素，请明确缩小范围。');
 if(threshold<1||threshold>255)throw Error('差异阈值需为1至255。');
 return {request_id:id(),before_scene_id:a.id,after_scene_id:b.id,before_version:a.version,after_version:b.version,roi,alignment:form.alignment,threshold,rgb_comparability_confirmed:true};
}
