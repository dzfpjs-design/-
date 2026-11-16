// This is the human selection gate; the server still verifies frozen source integrity.
export function understandingMaterialEligibility(material){
 if(!material?.id)return {eligible:false,label:'资料未找到，暂不能使用',reason:'资料未找到，请取消选择后重新核对'};
 if(material.archived)return {eligible:false,label:'已归档，暂不能使用',reason:'资料已归档，请取消选择，或恢复资料后重新核对'};
 if(material.status!=='parsed'){const labels={queued:'等待解析完成',parsing:'正在解析，尚不能使用',needs_input:'资料文字尚待核对确认',failed:'解析失败，请先处理失败原因',cancelled:'解析已取消，尚不能使用'};const label=labels[material.status]||'资料解析状态尚未核对';return {eligible:false,label,reason:label};}
 const ocr=material.ocr;
 if(!ocr)return {eligible:true,legacy:true,label:`已解析 ${material.block_count??0} 段；旧资料覆盖情况仍由服务器核对`,reason:null};
 if(ocr.coverage_status==='complete')return {eligible:true,legacy:false,label:material.document_copy?'PDF 副本文字已全部核对，可用于理解':`资料覆盖检查完成 · ${material.block_count??0} 段`,reason:null};
 const labels={blocked:'资料覆盖存在缺口，暂不能用于理解',needs_ocr:'页面或图片文字尚待识别、保存与确认',pending:'正在核对资料覆盖范围，请等待完成',legacy:'旧资料的完整覆盖情况尚待核对'};
 const label=labels[ocr.coverage_status]||'资料覆盖状态尚未核对，暂不能用于理解';
 return {eligible:false,label,reason:label+(typeof ocr.reason==='string'&&ocr.reason.trim()?'；'+ocr.reason:'')};
}
export function defaultUnderstandingMaterialIDs(materials){
 const ready=(materials||[]).filter(material=>understandingMaterialEligibility(material).eligible);
 const copiedSources=new Set(ready.map(material=>material.document_copy?.source_material_id).filter(id=>typeof id==='string'&&id));
 return ready.filter(material=>!copiedSources.has(material.id)).map(material=>material.id);
}
export function understandingMaterialChoice(material,ids,{canEdit,busy=false}={}){const eligibility=understandingMaterialEligibility(material),checked=ids.includes(material.id);return {...eligibility,checked,disabled:busy||!canEdit||!eligibility.eligible&&!checked};}
export function understandingMaterialProblems(materials,ids){const byID=new Map((materials||[]).map(material=>[material.id,material]));return ids.flatMap(id=>{const material=byID.get(id),state=understandingMaterialEligibility(material);return state.eligible?[]:[{id,name:material?.name||'清单中未找到的已选资料',reason:state.reason,missing:!material}];});}
export function assertUnderstandingMaterialSelection(materials,ids){if(!Array.isArray(ids)||new Set(ids).size!==ids.length||ids.some(id=>typeof id!=='string'||!id))throw Error('资料选择范围无法核对，未准备或发送云端理解。');const problems=understandingMaterialProblems(materials,ids);if(problems.length)throw Error('已选资料尚不能用于理解：'+problems.map(item=>item.name+'（'+item.reason+'）').join('；')+'。请先取消这些选择，或完成资料核对后再继续。');return ids;}
