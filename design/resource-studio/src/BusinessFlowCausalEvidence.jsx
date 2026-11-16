import React from 'react';

const actors={business_agent:'业务驱动智能体',perception_agent:'多模感知智能体',data_agent:'数据管理智能体',human:'人工岗位',task_center:'任务中心',execution_service:'执行服务'};
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const text=value=>typeof value==='string'&&value.trim().length>0;
function validSource(source){return object(source)&&(source.kind==='entry'?text(source.label):source.kind==='step'&&text(source.step_name)&&text(source.output_name)&&typeof source.actor==='string'&&typeof source.role==='string');}
function validRow(row){return object(row)&&Number.isSafeInteger(row.flow_index)&&row.flow_index>=0&&text(row.flow_name)&&text(row.node_id)&&text(row.step_name)&&text(row.input_name)&&Array.isArray(row.sources)&&row.sources.every(validSource)&&Array.isArray(row.citations)&&row.citations.every(text);}

export default function BusinessFlowCausalEvidence({view,flowIndex,nodeId,currentChanged=false,onCitation}){
 if(view===null||view===undefined)return null;
 const valid=object(view)&&view.schema==='business-flow-causal-source-view-v190'&&view.original_candidate_only===true&&view.runtime_entry_binding_verified===false&&typeof view.current_draft_matches_original==='boolean'&&Array.isArray(view.rows)&&view.rows.every(validRow);
 if(!valid)return <section className="bfr-card" aria-label="资料来源核对"><h4>资料来源核对</h4><p role="alert">资料来源记录暂不能完整核对，未显示为已具备资料或执行依据。</p></section>;
 const changed=currentChanged===true||view.current_draft_matches_original===false;
 const rows=Number.isSafeInteger(flowIndex)&&flowIndex>=0&&text(nodeId)?view.rows.filter(row=>row.flow_index===flowIndex&&row.node_id===nodeId):[];
 return <section className="bfr-card" aria-label="资料来源核对">
  <h4>资料来源核对</h4>
  <p>以下仅为原 AI 候选中声明的资料来源，不代表资料已经具备、内容属实或获得执行权限。</p>
  {changed&&<p role="alert"><strong>本稿已修改。</strong> 下列记录仅对应原 AI 候选，需要重新核对，不能当作当前稿的来源核验结果。</p>}
  {!rows.length?<p role="status">当前步骤没有对应的原候选记录；不会按相同名称猜配资料来源。</p>:rows.map((row,index)=><article key={row.flow_index+':'+row.node_id+':'+index}>
   <h5>{changed?'原候选所需资料':'所需资料'}：{row.input_name}</h5><p>原候选步骤：{row.step_name}</p>
   {row.sources.length>1&&<p>以下为备选来源（或），尚需明确采用哪项，并非全部已经具备。</p>}
   {!row.sources.length?<p>原候选没有明确资料来源，仍需补充核对。</p>:<ul>{row.sources.map((source,sourceIndex)=><li key={sourceIndex}>
    {source.kind==='entry'?<><strong>流程入口资料：待绑定</strong><p>{source.label}</p><p>尚未证明该资料真实存在、已经接入或已完成运行绑定。</p></>:<><p>由“{source.step_name}”提供“{source.output_name}”。</p><p>负责角色：{Object.hasOwn(actors,source.actor)?actors[source.actor]:'原候选角色待核对'}{source.role?' · '+source.role:''}</p></>}
   </li>)}</ul>}
   {!!row.citations.length&&<div>{row.citations.map((ref,citationIndex)=><button type="button" key={citationIndex} disabled={typeof onCitation!=='function'} onClick={()=>onCitation?.(ref)}>查看原文依据 {citationIndex+1}</button>)}</div>}
   {!row.citations.length&&<p>原候选没有列出此项原文依据，仍须核对。</p>}
  </article>)}
 </section>;
}
