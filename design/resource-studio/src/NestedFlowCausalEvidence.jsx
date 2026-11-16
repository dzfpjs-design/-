import React from 'react';

const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const text=value=>typeof value==='string'&&value.length>0;
const controls={decision:'条件分支',parallel:'并行分支',repeat:'循环核查'};
const actors={business_agent:'业务驱动智能体',perception_agent:'多模感知智能体',data_agent:'数据管理智能体',human:'人工岗位',task_center:'任务中心',execution_service:'执行服务'};
export default function NestedFlowCausalEvidence({view,flowIndex,actionPath,currentChanged=false,onCitation}){
 if(view===null||view===undefined)return null;
 const valid=object(view)&&view.schema==='business-flow-nested-causality-view-v193'&&view.original_candidate_only===true&&view.runtime_entry_binding_verified===false&&typeof view.current_draft_matches_original==='boolean'&&Array.isArray(view.rows);
 if(!valid)return <section className="bfr-card" aria-label="递归步骤资料来源核对"><h5>资料来源核对</h5><p role="alert">原候选的资料来源暂不能完整核对；未显示为已具备资料或执行依据。</p></section>;
 const changed=currentChanged===true||view.current_draft_matches_original===false;
 const rows=view.rows.filter(row=>object(row)&&row.flow_index===flowIndex&&row.action_path===actionPath);
 return <section className="bfr-card" aria-label="递归步骤资料来源核对"><h5>资料来源核对</h5><p>下列仅为原 AI 候选声明，不代表资料已经存在、来源属实或获得执行权限。</p>
  {changed&&<p role="alert"><strong>本稿已修改。</strong> 下列只对应原 AI 在这个位置的候选，需要重新核对；不能继承为当前步骤的资料来源。</p>}
  {!rows.length&&<p>当前位置没有对应的原候选资料记录；不会按名称猜配其他步骤。</p>}
  {rows.map((row,index)=>{
   if(!text(row.input_name)||!text(row.step_name)||!Array.isArray(row.sources)||!Array.isArray(row.citations)||!Array.isArray(row.control_ancestors))return <p role="alert" key={index}>此项原候选资料记录不完整，仍待核对。</p>;
   const ancestry=row.control_ancestors.map(ancestor=>controls[ancestor?.kind]?(controls[ancestor.kind]+(Number.isSafeInteger(ancestor.branch_index)?'第 '+(ancestor.branch_index+1)+' 支':'')):null).filter(Boolean);
   return <article key={row.input_path||index}><strong>原候选所需资料：{row.input_name}</strong><p>原候选步骤：{row.step_name}</p>{ancestry.length>0&&<p>原候选所在结构：{ancestry.join(' → ')}</p>}{row.sources.length>1&&<p>以下为备选来源（或），并非全部已经具备。</p>}
    <ul>{row.sources.map((source,i)=>source?.kind==='entry'?<li key={i}><strong>流程入口资料：待绑定</strong><p>尚未证明资料真实存在、已经接入或完成运行绑定。</p></li>:source?.kind==='step'&&text(source.step_name)&&text(source.output_name)?<li key={i}>由“{source.step_name}”提供“{source.output_name}”。<p>{actors[source.actor]||'角色待核对'}{typeof source.role==='string'&&source.role?' · '+source.role:''}</p></li>:<li key={i}>来源尚不能核对。</li>)}</ul>
    {row.citations.filter(text).map((ref,i)=><button type="button" key={i} disabled={typeof onCitation!=='function'} onClick={()=>onCitation?.(ref)}>查看本项原文依据 {i+1}</button>)}
   </article>;
  })}
 </section>;
}
