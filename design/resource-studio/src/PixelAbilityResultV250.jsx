import React from 'react';
export default function PixelAbilityResultV250({receipt}){
 const value=receipt.output?.pixel_comparison,valid=receipt.candidate_only===true&&value?.schema==='workflow-pixel-result-v250'&&value.coordinate_space==='pixel'&&value.georeference_available===false&&value.candidate_only===true&&value.object_state_updated===false&&['candidate_pixels','common_valid_pixels','unknown_pixels'].every(key=>Number.isSafeInteger(value[key])&&value[key]>=0)&&value.candidate_pixels<=value.common_valid_pixels;
 if(!valid)return <div role="alert">这份像素能力回执尚未核对，未将它显示为成功机器成果。请在本任务原图与处理进度区重新读取原依据。</div>;
 return <div className="ability-result"><strong>本地两期PNG像素候选已保存</strong><p>{value.candidate_pixels===0?'本次模型未检出变化候选，仍可能存在漏检。':'本次模型产生变化候选，需对照原图人工核查。'}</p><p>候选像素 {value.candidate_pixels} · 有像素范围 {value.common_valid_pixels} · 缺少像素 {value.unknown_pixels}</p><small>独立数据接收、人工办理和正式验收另行核对；不表示对象事实、地理面积或领域质量通过。</small></div>;
}
