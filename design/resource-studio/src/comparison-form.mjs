export function comparisonFields(required,ids,confirmed){
 if(!required)return {comparison_scene_ids:[],comparison_confirmed:false};
 if(!Array.isArray(ids)||ids.length!==2||ids.some(id=>typeof id!=='string'||!id)||ids[0]===ids[1])throw Error('请分别选择A参考影像与B对照影像。');
 if(confirmed!==true)throw Error('请先核对A/B顺序、RGB含义与分析范围。');
 return {comparison_scene_ids:[...ids],comparison_confirmed:true};
}

export function comparisonRequired(adapter){return ['local_geo_pair','local_learned_geo_pair'].includes(adapter);}

export function comparisonEvidenceRow(id,result){
 return {id,result,result_hash:JSON.stringify(result.artifacts),
  ...(result.learned_model?{request_kind:'learned_binary_change'}:{})};
}
