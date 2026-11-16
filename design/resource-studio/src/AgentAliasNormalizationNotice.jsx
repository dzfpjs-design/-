import React from 'react';

const POLICY='setup-v5-agent-aliases-v261';
const ORIGINAL='业务驱动智能体';
const CANONICAL='业务智能体';
const ERROR='智能体名称规范化记录无法核对';
const need=value=>{if(!value)throw Error(ERROR);};

// Display-only validation. It does not rewrite the model answer, approve a
// business, bind a capability, or grant execution authority.
export function checkedAgentAliasNormalizations(normalizations){
 if(normalizations===undefined)return [];
 need(Array.isArray(normalizations));const seen=new Set();
 return normalizations.map(row=>{
  need(row!==null&&typeof row==='object'&&!Array.isArray(row)&&Object.keys(row).sort().join('|')==='canonical|original|path|policy');
  need(row.policy===POLICY&&row.original===ORIGINAL&&row.canonical===CANONICAL&&typeof row.path==='string'&&row.path.length<=120);
  const match=/^\/businesses\/(0|[1-9][0-9]*)\/steps\/(0|[1-9][0-9]*)\/agent$/.exec(row.path);
  need(match&&match[0]===row.path&&!seen.has(row.path));
  const businessIndex=Number(match[1]),stepIndex=Number(match[2]);
  need(Number.isSafeInteger(businessIndex)&&Number.isSafeInteger(stepIndex)&&Number.isSafeInteger(businessIndex+1)&&Number.isSafeInteger(stepIndex+1));seen.add(row.path);
  return {policy:row.policy,path:row.path,original:row.original,canonical:row.canonical,businessIndex,stepIndex};
 });
}

export default function AgentAliasNormalizationNotice({normalizations}){
 let rows;
 try{rows=checkedAgentAliasNormalizations(normalizations);}catch{
  return <aside role="alert" aria-label="智能体名称规范化记录无法核对"><h4>名称采用情况需要核对</h4><p>这份智能体名称规范化记录无法核对，未把它当作名称采用成功。原回答与正常记录保留，请从原记录继续核对业务步骤、能力和权限。</p></aside>;
 }
 if(!rows.length)return null;
 return <aside role="note" aria-label="智能体名称规范化说明"><h4>已统一智能体名称，请继续核对</h4><p>原回答中的名称按固定名称规则采用如下；原回答仍完整保留。</p><ul>{rows.map(row=><li key={row.path}>业务{row.businessIndex+1} · 步骤{row.stepIndex+1}：{row.original} → {row.canonical}</li>)}</ul><p>这只统一名称，不代表业务方案、能力绑定或执行权限已通过核对。请继续核对业务步骤、所需能力和已有授权。</p></aside>;
}
