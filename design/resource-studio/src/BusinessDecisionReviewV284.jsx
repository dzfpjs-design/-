import React,{useEffect,useRef,useState} from 'react';
import {checkedDecisionReviewContext,decisionReviewContextPath,decisionReviewView,decisionPlanView,decisionPlanSummaryView,isBusinessDecisionReviewResult} from './business-decision-review-v284.mjs';

const preserved='这里只重新读取已保存的原建议与依据，不重新调用模型、补采或确认业务完成。';
const identityError='当前账号或工作区已变化，原建议已隐藏；请按当前任务范围重新读取。';
function currentIdentity(readerId,workspaceId){
 try{const user=globalThis.sessionStorage?.getItem('ga-setup-csrf-user'),workspace=globalThis.sessionStorage?.getItem('ga-setup-csrf-workspace');return user===readerId&&workspace===workspaceId;}catch{return false;}
}
function readError(error){return error?.status===403?'当前账号无权读取这份原建议，已保存记录仍保留。':error?.status===404?'原建议或来源记录暂不能读取，请核对原任务后再试。':error?.status===409?'原建议与保存的任务来源暂不能完整核对，未显示替代结果。':'原建议与依据读取失败，原记录仍保留，请重新读取。';}

function FactEvidence({fact}){
 return <article className="media-form" aria-label={fact.kindLabel+'依据'}>
  <strong>{fact.kindLabel}</strong>
  {fact.declaration?<><p>仅说明已登记能力及其声明，不是已取得的观测。</p>{fact.targetLabels.length>0&&<p>声明提供或申请的字段：{fact.targetLabels.join('、')}</p>}</>:
   <><p>{fact.label}：<strong style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{fact.valueText}</strong> · {fact.unitLabel}</p><p>{fact.timeLabel}</p></>}
  {fact.citations.map((citation,index)=><div key={index}><p>原来源：{citation.source.label}</p>
   {citation.quote===null?<p role="note">原来源没有可摘录文字；本页不补写引用。</p>:
    fact.declaration||citation.source.kind==='capability'?<details><summary>展开此登记声明的原引用</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:'16rem',overflow:'auto'}}>{citation.quote}</pre></details>:
     <blockquote style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{citation.quote}</blockquote>}
  </div>)}
 </article>;
}
function Evidence({facts}){return facts.length?<section aria-label="与此意见对应的原依据">{facts.map((fact,index)=><FactEvidence key={index} fact={fact}/>)}</section>:<p role="note">此意见没有引用具体事实；不会自动补作支持依据。</p>;}
function FieldList({label,items}){return <p><strong>{label}：</strong>{items.length?items.join('、'):'无此项'}</p>;}

export function DecisionPlanSummaryV285({value,taskId}){
 let view;try{view=decisionPlanSummaryView(value,{taskId});}catch{return <p role="alert">原本地计划的版本、范围或状态暂不能完整核对，未显示推测路由；请重新读取原任务记录。</p>;}
 return <section className="media-form" aria-label="原本地决策计划"><h4>原本地决策计划</h4>
  {view.simulation&&<p role="note"><strong>模拟资料流程（SIM）</strong> · 不代表现场效果或生产启用。</p>}
  <p role="status"><strong>{view.route}</strong></p><p>{view.reason}</p><p>{view.wait}</p><p>{view.selection}</p>
  <p>{view.noGap?'原计划未列出当前资料缺项；这不证明资料充分、一致或目标完成。':'原计划保留 '+view.missingCount+' 项当前资料缺口，具体字段见原业务建议。'}</p>
  {view.conflictCount>0&&<p>原计划保留 {view.conflictCount} 项事实冲突，请在原建议中核对双方记录和引用。</p>}
  <p role="note">本摘要只解释已保存的路由，不重新计划、授权、补采、调用模型或确认独立验收。</p>
 </section>;
}

function ReviewBody({context,plan}){
 const view=decisionReviewView(context);let planView=null,planError=false;
 try{planView=decisionPlanView(plan,context);}catch{planError=true;}
 return <>
  {view.simulation&&<p role="note"><strong>模拟资料流程（SIM）</strong> · 原建议不代表现场效果或生产启用。</p>}
  <p role="note">以下分别呈现模型意见和服务器已保存的机械核查。登记能力、申请测量、原观测和独立收件是不同记录，不能互相替代。</p>
  <p role="status"><strong>{view.state}</strong></p>
  {planView&&<aside className="info" aria-label="原本地计划路由"><strong>{planView.route}</strong><p>{planView.reason}</p><p>人工填写只保存核查意见，不解除原审批、补采权限、事实冲突或目标完成条件。</p></aside>}
  {planError&&<p role="alert">原路由与此建议暂不能完整核对；建议原件仍可阅读，本页不推断后续动作。</p>}
  <section aria-label="当前资料的系统核查"><h5>当前资料与真实缺口</h5><p>{view.consistency}</p><p>{view.sufficient?'服务器记录：本次原冻结资料满足当前资料要求。':'服务器记录：本次原冻结资料尚不能认定充分。'}这不等于目标已经验收。</p><FieldList label="仍缺少的当前资料" items={view.missing}/></section>
  <section aria-label="逐项方案意见与系统端口核查"><h5>逐项方案核对</h5>{view.candidates.map((candidate,index)=><article className="media-form" key={index}>
   <h6>{candidate.label}</h6><p style={{whiteSpace:'pre-wrap'}}>{candidate.proposal}</p>
   <p><strong>{candidate.suitability}</strong></p><p style={{whiteSpace:'pre-wrap'}}>{candidate.reasoning}</p>
   {candidate.blocking.length>0?<><strong>模型列出的额外前提</strong><ul>{candidate.blocking.map((line,i)=><li key={i} style={{whiteSpace:'pre-wrap'}}>{line}</li>)}</ul></>:<p>模型未列出额外前提；这不代表已有执行许可。</p>}
   <div aria-label={candidate.label+'系统核查'}><p><strong>系统端口核查：</strong>{candidate.applicability}</p><p>{candidate.support}；端口覆盖不会变成已收到的观测。</p><FieldList label="已声明覆盖的字段" items={candidate.covered}/><FieldList label="未覆盖的字段" items={candidate.uncovered}/><FieldList label="条件尚不明确的字段" items={candidate.unknown}/></div>
   <Evidence facts={candidate.facts}/>
  </article>)}</section>
  <section aria-label="模型的原推荐"><h5>模型原推荐</h5><p><strong>{view.recommendation.label}</strong></p><p style={{whiteSpace:'pre-wrap'}}>{view.recommendation.reason}</p><Evidence facts={view.recommendation.facts}/></section>
  {view.conflicts.length>0&&<section aria-label="原事实冲突双方"><h5>需核查的原事实冲突</h5>{view.conflicts.map((conflict,index)=><article className="media-form" key={index}><h6>{conflict.label} · {conflict.unit}</h6><p>{conflict.time}</p><p style={{whiteSpace:'pre-wrap'}}>{conflict.reason}</p>{conflict.sides.map((fact,i)=><section key={i} aria-label={'冲突一方'+(i+1)}><strong>原记录{i+1}</strong><FactEvidence fact={fact}/></section>)}</article>)}</section>}
  <details><summary>本次原来源核对情况</summary>{view.sources.map((source,index)=><article key={index}><strong>{source.label} · {source.assessment}</strong><p style={{whiteSpace:'pre-wrap'}}>{source.reason}</p><p>原引用分配了 {source.factCount} 项事实；资料包提及某能力不会把能力声明变成资料包事实。</p></article>)}</details>
  <p role="note">本页不验证或授予设备权限、来源真实性、现场结果、目标完成或数据验收。实际状态仍以原任务及独立回执为准。</p>
 </>;
}

/** Uses only the parent's same-origin JSON API. All retry actions are GETs. */
export default function BusinessDecisionReviewV284({value,taskId,activationId,account,readerId,workspaceId,requestJSON,plan=null}){
 const reader=readerId||account?.user_id||account?.id,workspace=workspaceId||account?.workspace_id;
 const key=[reader,workspace,account?.role,taskId,activationId,value?.input_hash].join('|');
 const latest=useRef(key);latest.current=key;
 const [attempt,setAttempt]=useState(0),[state,setState]=useState({key:null,status:'loading'});
 useEffect(()=>{
  const controller=new AbortController();let active=true;
  const live=()=>active&&!controller.signal.aborted&&latest.current===key;
  const changed=()=>{if(!currentIdentity(reader,workspace)){controller.abort();if(active&&latest.current===key)setState({key,status:'error',message:identityError});}};
  setState({key,status:'loading'});
  let path;
  try{const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;if(!uuid.test(reader)||!uuid.test(workspace)||typeof requestJSON!=='function'||!isBusinessDecisionReviewResult(value)||!currentIdentity(reader,workspace))throw Error(identityError);path=decisionReviewContextPath(taskId,activationId);}catch{setState({key,status:'error',message:'原任务、账号或建议范围暂不能核对，未读取其他任务。'});return()=>{active=false;controller.abort();};}
  globalThis.addEventListener?.('storage',changed);globalThis.addEventListener?.('focus',changed);
  Promise.resolve().then(()=>{if(!live()||!currentIdentity(reader,workspace))throw Error(identityError);return requestJSON(path,{method:'GET',signal:controller.signal,headers:{'X-Workspace-ID':workspace}});})
   .then(response=>{if(!live())return;if(!currentIdentity(reader,workspace)){changed();return;}const context=checkedDecisionReviewContext(response,{taskId,activationId,result:value});if(live())setState({key,status:'ready',context});})
   .catch(error=>{if(!live()||error?.name==='AbortError')return;setState({key,status:'error',message:readError(error)});});
  return()=>{active=false;controller.abort();globalThis.removeEventListener?.('storage',changed);globalThis.removeEventListener?.('focus',changed);};
 },[key,attempt,requestJSON]);
 const current=!currentIdentity(reader,workspace)?{status:'error',message:identityError}:state.key===key?state:{status:'loading'};
 return <section className="media-form" aria-label="业务建议与系统核查"><h4>业务建议与系统核查</h4><p role="note">{preserved}</p>
  {current.status==='loading'&&<p role="status">正在读取原建议及对应资料依据…</p>}
  {current.status==='error'&&<><p role="alert">{current.message}</p><button type="button" onClick={()=>setAttempt(count=>count+1)}>重新读取原建议与依据</button></>}
  {current.status==='ready'&&<ReviewBody key={key} context={current.context} plan={plan}/>}
 </section>;
}
