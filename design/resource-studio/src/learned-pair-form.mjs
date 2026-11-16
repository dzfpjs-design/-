export const learnedPairKey=(workspace,user)=>'ga-learned-pair:'+workspace+':'+user;
export const emptyLearnedPair=()=>({before:'',after:'',roi:['','','',''],alignment:'same_grid',threshold:'0.5',confirmed:false,pending:null});
export function readLearnedPair(storage,key){
 const raw=storage.getItem(key);if(!raw)return emptyLearnedPair();const value=JSON.parse(raw);
 if(!value||typeof value.before!=='string'||typeof value.after!=='string'||!Array.isArray(value.roi)||value.roi.length!==4||value.roi.some(v=>typeof v!=='string')||typeof value.threshold!=='string'||typeof value.confirmed!=='boolean'||!['same_grid','reproject_after_to_before'].includes(value.alignment)||value.pending&&(!/^[a-zA-Z0-9_-]{12,100}$/.test(value.pending.request_id)||typeof value.pending.model_installation_id!=='string'))throw Error('变化分析草稿或原请求无法读取；未覆盖，请先恢复浏览器存储。');
 return value;
}
export function learnedPairBody(form,scenes,model,catalog,id){
 if(!model||model.status!=='ready'||model.recipe_hash!==catalog.model_recipe_hash||model.pipeline_hash!==catalog.pipeline_hash)throw Error('请先完成当前版本的本地模型安装检查。');
 const a=scenes.find(s=>s.id===form.before),b=scenes.find(s=>s.id===form.after);
 if(!a||!b||a.status!=='ready'||b.status!=='ready'||!a.configuration?.map_image_confirmed||!b.configuration?.map_image_confirmed)throw Error('请选择两幅已确认RGB与地图参考的影像。');
 if(a.input_sha256===b.input_sha256)throw Error('A、B原件相同，请选择不同影像。');
 if(!form.confirmed)throw Error('请核对两幅影像的RGB含义与数值范围。');
 const roi=form.roi.map(v=>{if(!/^\d+$/.test(v))throw Error('范围需填写非负整数。');return Number(v);}),[x0,y0,x1,y1]=roi;
 const bounds=a.configuration.window||[0,0,a.metadata.width,a.metadata.height];
 if(!(bounds[0]<=x0&&x0<x1&&x1<=bounds[2]&&bounds[1]<=y0&&y0<y1&&y1<=bounds[3]))throw Error('范围超出A已确认的读取区域。');
 if(Math.min(x1-x0,y1-y0)<32||(x1-x0)*(y1-y0)>16000000)throw Error('分析区域每边至少32像素，总计不超过1600万像素。');
 const threshold=Number(form.threshold);if(!form.threshold.trim()||!Number.isFinite(threshold)||threshold<=0||threshold>=1)throw Error('概率阈值需在0至1之间。');
 return {request_id:id(),model_installation_id:model.id,model_generation:model.generation,model_recipe_hash:model.recipe_hash,before_scene_id:a.id,after_scene_id:b.id,before_version:a.version,after_version:b.version,roi,alignment:form.alignment,probability_threshold:threshold,rgb_comparability_confirmed:true};
}
