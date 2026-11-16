import React,{useId} from 'react';
import {graphGoalGateView} from './graph-goal-gate-v133.mjs';

export default function GraphGoalGateV133({task}){
 const heading=useId();let view;
 try{view=graphGoalGateView(task);}catch{return <section className="media-form" aria-labelledby={heading}>
  <h3 id={heading}>目标执行限制需核对</h3><p role="alert">目标执行限制暂无法核对，当前不能提交新结果。原输入、草稿、已保存结果和原请求记录保留，仍可保存草稿或只核对原请求。</p>
 </section>;}
 if(view===null)return null;
 return <section className="media-form" aria-labelledby={heading}>
  <h3 id={heading}>{view.title}</h3><p role={view.limited?'alert':'status'}>{view.detail}</p>
  {view.continuation&&<p>{view.continuation}</p>}
  <p>{view.preservation}</p>
 </section>;
}
