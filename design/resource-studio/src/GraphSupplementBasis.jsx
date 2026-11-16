import React from 'react';

const statusLabels={
 blocked:'原流程受阻',failed:'原流程未完成',stopped:'原流程已停止',
 waiting:'原流程仍在等待',running:'原流程处理中',cancelled:'原流程已取消',succeeded:'原流程已完成',
};
const errorLabels={
 max_steps:'原目标的人工及执行步骤额度已用尽。',
 max_tool_calls:'原目标的能力调用额度已用尽。',
 required_receipts_missing:'原流程缺少完成标准所需的回执。',
 stop_guard:'原流程达到停止条件。',
 exception_guard:'原流程因异常条件停止。',
};
const originalNodeLabels={
 packet:'资料包准备',
 reasoning:'业务推理',
 review_decision:'业务建议核对',
};
function nodeName(task,id,index){
 const label=task.graph_node_labels?.[id];
 const step=task.business?.flow?.steps?.find(row=>row.id===(label?.source_step_id||id));
 const known=Object.hasOwn(originalNodeLabels,id)?originalNodeLabels[id]:null;
 return step?.name||label?.name||label?.display_name||known||'原流程步骤 '+(index+1);
}
function humanText(value){
 if(typeof value!=='string'||!value.trim()||/Traceback|(?:KeyError|AttributeError|TypeError)|secret|authorization|^\s*[\[{]/i.test(value))return '';
 return value.replace(/\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b/gi,'相应记录').replace(/\b[a-f0-9]{64}\b/gi,'原校验记录');
}
export default function GraphSupplementBasis({value,task,accepted=false}){
 const original=Object.hasOwn(statusLabels,value?.original_graph_status)?statusLabels[value.original_graph_status]:'原流程状态已保留';
 const reason=humanText(value?.reason);
 const error=value?.original_graph_error;
 const explanation=Object.hasOwn(errorLabels,error)?errorLabels[error]:typeof error==='string'&&/[\u3400-\u9fff]/.test(error)?humanText(error):'';
 const skipped=Array.isArray(value?.skipped_node_ids)?value.skipped_node_ids.filter(id=>typeof id==='string'):[];
 return <section className="info" aria-label={accepted?'已验收的补充办理依据':'补充办理验收依据'}>
  <h4>{accepted?'补充办理已独立验收':'按补充办理依据独立验收'}</h4>
  <p>{original}。本次验收以补充办理、数据接收和人工复核所保存的依据为准。原流程状态、结果和受阻记录继续保留。</p>
  {reason&&<p><strong>补充办理说明：</strong>{reason}</p>}
  {error&&<p><strong>原流程受阻原因：</strong>{explanation||'原受阻原因已保存，可在原流程记录中核对。'}</p>}
  {Number.isSafeInteger(value?.evidence_generation)&&value.evidence_generation>0&&<p>所用正式资料评估：第 {value.evidence_generation} 代。</p>}
  {skipped.length>0&&<><strong>原流程未完成的步骤</strong><ul>{skipped.map((id,index)=><li key={id+':'+index}>{nodeName(task,id,index)}</li>)}</ul></>}
 </section>;
}
