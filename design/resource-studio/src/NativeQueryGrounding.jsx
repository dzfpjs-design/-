import React from 'react';
import {queryGroundingState} from './native-query-grounding-contract.mjs';

export default function NativeQueryGrounding({row}){
 const state=queryGroundingState(row);
 if(!state.applicable)return null;
 if(!state.valid)return <section className="media-session" aria-label="原文依据核对"><p role="alert">原文依据摘要不完整，暂时不能确认。原计划和原答保留，请恢复后继续核对。</p></section>;
 const value=state.grounding;
 if(!value.steps.length&&!value.blocker_count)return null;
 return <section className="media-session" aria-label="原文依据核对"><h6>原文依据核对</h6>
  {value.steps.map((step,index)=>{const parameters=row.candidate.tool_steps.find(item=>item.id===step.id).parameters;return <div key={step.id}><strong>全文检索 {index+1}</strong><p>实际检索文字：<q>{step.text}</q></p><p>匹配方式：{parameters.match_mode==='any_terms'?'任意一个检索词出现即可':'所有检索词都要出现'}；最多返回 {parameters.top_k} 条。</p><p>原文依据：<q style={{whiteSpace:'pre-wrap'}}>{step.quote}</q></p><p>{step.status==='supported'?'服务器已逐词核对：检索词均有原文依据。':'这些检索词的原文依据还需核对。'}</p>{step.tokens.length>0&&<details><summary>查看逐词核对明细</summary><table><thead><tr><th>检索词</th><th>查询使用次数</th><th>原文出现次数</th></tr></thead><tbody>{step.tokens.map(item=><tr key={item.token}><td>{item.token}</td><td>{item.required_count}</td><td>{item.source_count}</td></tr>)}</tbody></table></details>}</div>;})}
  {value.blocker_count>0&&<div role="status"><p>还有 {value.blocker_count} 项需要核对：</p><ul>{value.diagnostics.slice(0,10).map((item,index)=><li key={index}>{item.message}</li>)}</ul>{value.blocker_count>10&&<p>其余 {value.blocker_count-10} 项已保存，请先解决当前问题后继续核对。</p>}</div>}
  <p>词语有出处仍需核对“全部词”或“任意词”、资料范围和排名限制；确认前请对照原需求。</p>
 </section>;
}
