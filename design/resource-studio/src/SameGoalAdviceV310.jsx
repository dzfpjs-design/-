import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json} from './runtime-api';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import {SameGoalAdviceControllerV310} from './same-goal-advice-v310-controller.mjs';
import {adviceCanonical} from './same-goal-advice-v310-controller.mjs';
import './same-goal-advice-v310.css';
import SameGoalContinuationV312 from './SameGoalContinuationV312.jsx';
import FormalContinuationContextV344 from './FormalContinuationContextV344.jsx';
import AdvicePreparationV2 from './AdvicePreparationV2.jsx';

const PHASES={queued:'等待整理资料',preparing:'正在整理资料',awaiting_consent:'资料已保存，待核对授权',send_queued:'分析申请已保存',generating:'正在分析',closing:'回答已保存，正在收尾核对',ready_for_review:'可以审核建议',reviewed:'审核结论已保存',cancelled:'已取消，原记录保留',needs_review:'需要核对原申请'};
const DECISIONS={accepted:'采用建议',rejected:'不采用建议',needs_revision:'需补充'};
const SCOPES={provided_excerpt_only:'仅审阅已提供的摘录',provided_source_text_only:'仅审阅本次提供的文字',not_reviewed:'未认定已经审阅'};
const SOURCE_NAMES={data_packet:'本次数据与接收记录',document_evidence:'批准的业务资料摘录'};
const SUITABILITY={suitable:'模型认为可考虑',unsuitable:'模型认为不适合',uncertain:'模型认为仍需核对'};
const QUOTA_STATES={ready:'可供本任务一次分析使用',allocated:'已分配给原分析申请',used:'已使用，请查看原分析记录',unavailable:'尚无可用费用上限'};
const PRICE_STATES={ready:'已有有效官方价格依据',unavailable:'尚无当前有效价格依据'};
const STEPS=['选择资料','核对与授权','预览费用','开始分析','审核建议'];

function Button({Btn,primary=false,children,...props}){
 if(Btn)return <Btn primary={primary} {...props}>{children}</Btn>;
 return <button type="button" className={'btn '+(primary?'primary':'')} {...props}>{children}</button>;
}
function clock(value){
 if(typeof value!=='string'||!Number.isFinite(new Date(value).getTime()))return '时间尚未核对';
 return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value))+'（北京时间）';
}
function money(value,currency){
 const prefix=currency==='CNY'?'¥':currency==='USD'?'US$':null;
 if(prefix===null||!Number.isSafeInteger(value)||value<0)return '费用尚未核对';
 const minor=BigInt(value);
 return prefix+String(minor/100n)+'.'+String(minor%100n).padStart(2,'0');
}
function notice(value,fallback){
 if(typeof value!=='string'||!value.trim())return fallback;
 return /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|\b[0-9a-f]{64}\b|^\s*[\[{]|(?:api_key|authorization|traceback|lease_token)\s*[:=]/i.test(value)?fallback:value;
}
function cloudInput(preview){
 const message=preview?.messages?.find(row=>row.role==='user');
 const text=Array.isArray(message?.content)?message.content.find(row=>row.type==='text')?.text:message?.content;
 try{const value=JSON.parse(text);return value&&typeof value==='object'&&value.decision_input?.schema==='same-goal-advice-cloud-input-v302'?value:null;}catch{return null;}
}
function ActualQuestion({preview}){
 const question=cloudInput(preview)?.question;
 return typeof question==='string'&&question.trim()?<section aria-label="本次实际分析问题"><h6>本次实际分析问题</h6><p className="same-advice-text">{question}</p></section>:null;
}
function sourceName(source,index){return SOURCE_NAMES[source?.source_ref]||'原依据 '+(index+1);}
function proposal(input,reference,index){return input?.candidates?.find(row=>row.candidate_ref===reference)?.proposal||'方案 '+(index+1);}
function Stage({number,title,children,actions}){
 return <section className="media-form same-advice-stage"><div className="same-advice-section-heading"><h5><span className="same-advice-number">{number}</span>{title}</h5>{actions}</div>{children}</section>;
}
function Reason({label,value,onChange,disabled}){
 return <label className="same-advice-field">{label}<textarea aria-label={label} rows={2} maxLength={2000} value={typeof value==='string'?value:''} onChange={event=>onChange(event.target.value)} disabled={disabled}/></label>;
}
function ReadStatus({state,label,loading,failed}){
 if(state==='loading')return <p role="status" aria-label={label}>{loading}</p>;
 if(state==='failed')return <p role="alert" aria-label={label}>{failed}</p>;
 return null;
}

function ReviewScope({scope,dataAsOf,input}){
 return <aside className="same-advice-basis" aria-label="作出决定前的资料时间与审阅范围">
  <h6>作出决定前请核对</h6><p><strong>本建议所用资料截至：</strong>{clock(dataAsOf)}</p>
  {scope?.schema==='same-goal-advice-provided-source-review-v309'&&Array.isArray(scope.sources)?<>
   <ul>{scope.sources.map((row,index)=><li key={row.source_ref}><strong>{sourceName(row,index)}：</strong>{SCOPES[row.review_scope]||'审阅范围尚待核对'}</li>)}</ul>
   <p>仅依据已提供的资料，未核验整份文档或现场。</p>
  </>:<p>资料审阅范围待核对。</p>}
  <p>保存决定不会执行设备操作或业务审批。</p>
  {input?.known_conflicts?.length>0&&<p role="note">本次原资料包含 {input.known_conflicts.length} 项不同记录，请先核对双方依据。</p>}
 </aside>;
}

function FactReferences({refs,input}){
 if(Array.isArray(refs)&&refs.length&&!input)return <p role="note">依据暂未读回，请刷新。</p>;
 const facts=(Array.isArray(refs)?refs:[]).map(ref=>input?.facts?.find(row=>row.fact_ref===ref)).filter(Boolean);
 if(!facts.length)return <p role="note">这项意见未引用具体事实。</p>;
 return <details><summary>查看依据（{facts.length}项）</summary>{facts.map((fact,index)=><article className="same-advice-quote" key={fact.fact_ref}>
  <strong>依据 {index+1}</strong><p>{typeof fact.value==='boolean'?(fact.value?'是':'否'):String(fact.value??'原记录未给出值')}{fact.unit?' '+fact.unit:''}</p>
  {fact.time_scope?.start&&<p>记录时间：{clock(fact.time_scope.start)}{fact.time_scope.end!==fact.time_scope.start?' 至 '+clock(fact.time_scope.end):''}</p>}
  {(fact.source_refs||[]).map((citation,i)=><div key={i}><p>{sourceName(input?.sources?.find(row=>row.source_ref===citation.source_ref),i)}</p>{citation.quote?<blockquote>{citation.quote}</blockquote>:<p>原记录没有可展示的文字引用。</p>}</div>)}
 </article>)}</details>;
}

function Opinion({result,preview}){
 const value=result?.model_value,input=cloudInput(preview)?.decision_input;
 if(!value)return null;
 const recommendationIndex=value.candidate_reviews?.findIndex(row=>row.candidate_ref===value.recommendation?.candidate_ref)??-1;
 return <section aria-label="已保存的建议">
  <h5>建议</h5><p className="same-advice-proposal"><strong>{value.recommendation?.candidate_ref?proposal(input,value.recommendation.candidate_ref,Math.max(0,recommendationIndex)):'模型暂未选择方案'}</strong></p>
  <p className="same-advice-text">{value.recommendation?.reason}</p><FactReferences refs={value.recommendation?.supporting_fact_refs} input={input}/>
  <details className="same-advice-candidates"><summary>查看候选方案（{value.candidate_reviews?.length||0}项）</summary>{(value.candidate_reviews||[]).map((candidate,index)=><article className="media-form" key={candidate.candidate_ref}>
   <h6>{proposal(input,candidate.candidate_ref,index)}</h6><p><strong>{SUITABILITY[candidate.suitability]||'模型意见待核对'}</strong></p><p className="same-advice-text">{candidate.reasoning}</p>
   {candidate.blocking_conditions?.length>0&&<><strong>模型列出的前提</strong><ul>{candidate.blocking_conditions.map((line,i)=><li key={i}>{line}</li>)}</ul></>}
   <FactReferences refs={candidate.supporting_fact_refs} input={input}/>
  </article>)}</details>
  {value.conflicts?.length>0&&<section aria-label="原建议保留的资料冲突"><h6>需要核对的不同记录</h6>{value.conflicts.map((conflict,index)=><article className="media-form" key={conflict.conflict_ref}><strong>不同记录 {index+1}</strong><p className="same-advice-text">{conflict.reason}</p><FactReferences refs={conflict.fact_refs} input={input}/></article>)}</section>}
  <details><summary>查看资料审阅情况</summary>{(value.source_reviews||[]).map((row,index)=><article key={row.source_ref}><strong>{sourceName(row,index)}</strong><p className="same-advice-text">{row.reason}</p></article>)}</details>
 </section>;
}

function SentText({preview}){
 return <details className="same-advice-sent"><summary>查看发送内容</summary>
  {(preview?.messages||[]).map((message,index)=><section key={index}><h6>{message.role==='system'?'固定分析要求':'本次问题与资料'}</h6>{typeof message.content==='string'?<pre>{message.content}</pre>:(message.content||[]).map((part,i)=>part.type==='text'?<pre key={i}>{part.text}</pre>:null)}</section>)}
 </details>;
}

// The complete Card is wired only to the fixed controller's checked DTOs.
// It never constructs an API body, nonce, grant, model call or SDK identity.

function Document({document,model,draft,disabled,canAuthorize,onDraft,onAction,Btn}){
 const id=document.evidence_ref.evidence_id,authorized=document.consent.status==='authorized';
 const unavailable=document.consent.status==='model_unavailable';
 return <article className="media-form" aria-label={document.title}>
  <h6>{document.title}</h6><p>{document.source_scope==='approved_excerpt'?'本任务已批准的资料摘录':'本次数据整理与独立接收记录'}</p>
  <p className="same-advice-muted">资料截至：{clock(document.as_of)}</p>
  <details><summary>查看这份资料的完整原文</summary><pre>{document.text}</pre></details>
  {authorized?<><p role="status"><strong>已授权给 {model?.name||'本次指定模型'}</strong></p><details><summary>查看已保存的授权说明</summary>{document.consent.grants.map((grant,index)=><p key={grant.audit_id}>授权说明 {index+1}：{grant.reason}</p>)}</details></>:
   unavailable?<p role="note">指定模型不可用，暂不能授权。</p>:
   <><p>同意将资料发送给 <strong>{model?.name||'本次指定模型'}</strong>。</p>
    <Reason label={'授权说明：'+document.title} value={draft.consent_reasons?.[id]} disabled={disabled||!canAuthorize} onChange={value=>onDraft({consent_reasons:{...draft.consent_reasons,[id]:value}})}/>
    <label className="same-advice-check"><input type="checkbox" checked={draft.consent_checked?.[id]===true} disabled={disabled||!canAuthorize} onChange={event=>onDraft({consent_checked:{...draft.consent_checked,[id]:event.target.checked}})}/>我已核对资料，同意发送给上述模型分析</label>
    <Button Btn={Btn} disabled={disabled||!canAuthorize||draft.consent_checked?.[id]!==true||!draft.consent_reasons?.[id]?.trim()} onClick={()=>onAction('authorizeEvidence',id)}>确认授权</Button>
   </>}
 </article>;
}

function AdminFees({context,draft,blocked,editingBlocked,onDraft,onAction,Btn}){
 const permissions=context.permissions,price=context.price,quota=context.quota;
 const currency=price?.status==='ready'&&typeof price.currency==='string'&&price.currency.trim()?price.currency:null;
 return <details className="same-advice-admin-fees" aria-label="管理员费用设置"><summary>管理员费用设置</summary>
  <p>费用上限仅适用于本次分析，实际费用以供应商账单为准。</p>
  <div className="same-advice-columns">
   <section className="media-form" aria-label="本任务官方价格依据"><h5>官方价格依据</h5><p><strong>{PRICE_STATES[price?.status]||'价格状态尚待核对'}</strong></p>
    {price?.checked_at&&<p>核对时间：{clock(price.checked_at)}</p>}{price?.expires_at&&<p>有效至：{clock(price.expires_at)}</p>}
    <Reason label="更新官方价格依据的说明" value={draft.price_reason} disabled={editingBlocked||permissions.can_register_price!==true} onChange={value=>onDraft({price_reason:value})}/>
    <Button Btn={Btn} disabled={blocked||permissions.can_register_price!==true||!draft.price_reason?.trim()} onClick={()=>onAction('registerPrice')}>更新官方价格依据</Button>
   </section>
   <section className="media-form" aria-label="本任务一次分析费用上限"><h5>一次分析费用上限</h5><p><strong>{QUOTA_STATES[quota?.status]||'费用上限状态尚待核对'}</strong></p>
    {quota?.limits&&<p>已保存的本次上限：{money(quota.limits.max_cost_minor_units,quota.limits.currency)}</p>}{quota?.expires_at&&<p>有效至：{clock(quota.expires_at)}</p>}
    <label className="same-advice-field">本次分析费用上限<span className="same-advice-cost-row"><input aria-label="本次分析费用上限" type="text" inputMode="decimal" maxLength={20} autoComplete="off" value={typeof draft.quota_cost==='string'?draft.quota_cost:''} disabled={editingBlocked||permissions.can_configure_quota!==true||currency===null} onChange={event=>onDraft({quota_cost:event.target.value})}/><span>{currency||'币种待官方价格核对'}</span></span></label>
    <p className="same-advice-muted">金额最多两位小数。</p>
    <Reason label="配置本次费用上限的说明" value={draft.quota_reason} disabled={editingBlocked||permissions.can_configure_quota!==true} onChange={value=>onDraft({quota_reason:value})}/>
    <Button Btn={Btn} disabled={blocked||permissions.can_configure_quota!==true||currency===null||!draft.quota_cost?.trim()||!draft.quota_reason?.trim()} onClick={()=>onAction('configureQuota')}>保存本次分析费用上限</Button>
   </section>
  </div>
 </details>;
}

// Keep every nonempty saved input reachable even before an application exists.
const hasDraftWork=value=>typeof value==='string'?value.length>0:
 value===true?true:Array.isArray(value)?value.some(hasDraftWork):
 value!==null&&typeof value==='object'?Object.values(value).some(hasDraftWork):false;

export function SameGoalAdviceContentV310({view,account,Btn,onAction,onDraft,historyOnly=false}){
 if(view.denied)return <p role="alert">账号、工作区或权限已变化，资料已隐藏，请重新打开对应工作区。</p>;
 const reads=view.reads||{};
 if(view.loading&&!view.context&&!view.list&&!view.plan&&!Object.values(reads).includes('failed'))return <p role="status">正在读取建议…</p>;
 const context=view.context,plan=view.plan,preview=view.preview,draft=view.draft||{};
 if(historyOnly&&!plan&&!view.pending&&!view.needsCheck&&view.localSaved!==false&&!hasDraftWork(draft)){
  return <><ReadStatus state={reads.history} label="历史建议" loading="正在读取历史建议…" failed="历史建议暂未读回，请刷新。"/>
   {view.list&&(view.list.records?.length?view.list.records.map((row,index)=><article key={row.plan_id}><p>建议 {index+1} · {PHASES[row.phase]||'待核对'} · {clock(row.data_as_of)}</p><Button Btn={Btn} disabled={view.busy||view.loading} onClick={()=>onAction('selectPlan',row.plan_id)}>查看</Button></article>):<p>暂无历史建议。</p>)}
  </>;
 }
 const contextReady=reads.context==='ready',permissions=contextReady?context?.permissions||{}:{},pending=!!view.pending,editingBlocked=view.busy||view.loading||pending||view.needsCheck===true;
 const blocked=editingBlocked||!view.localSaved,phase=plan?.phase;
 const documents=view.documents?.documents||[],model=view.documents?.model||preview?.model||context?.model;
 const sources=context?.document_sources||[];
 const selection=sources.findIndex(row=>adviceCanonical(row.selection)===adviceCanonical(draft.selected_document_source??null));
 const ready=phase==='ready_for_review'&&plan?.human_review_available===true&&!!plan?.result;
 const modelReady=model?.available!==false;
 const allAuthorized=documents.length===2&&documents.every(row=>row.consent.status==='authorized');
 const mayPrepare=!plan&&!['loading','failed'].includes(reads.plan)||phase==='cancelled'||phase==='reviewed';
 const step=plan?.result?4:['send_queued','generating','closing'].includes(phase)?3:preview?2:phase==='awaiting_consent'?1:0;
 const canCancel=plan?.cancel_available===true&&['admin','operator'].includes(account?.role);
 const canStart=permissions.can_start===true&&plan?.stopped_reasoning_unlinked!==true;
 const allReasons=Array.isArray(context?.blocked_reasons)?context.blocked_reasons:[];
 const initialEvaluationNotice=allReasons.find(row=>row.code==='initial_evaluation_preview_limited');
 const blockedReasons=allReasons.filter(row=>row.code!=='initial_evaluation_preview_limited');
 return <>
  <ol className="same-advice-progress" aria-label="业务建议办理步骤">{STEPS.map((label,index)=><li key={label} aria-current={index===step?'step':undefined}>{index+1} · {label}</li>)}</ol>
  {plan?.continues_stopped_reasoning===true&&<aside className="info" aria-label="原步骤后续分析"><strong>这份分析接续已停用的原步骤</strong><p>仍需完成后续办理和目标核验。</p></aside>}
  {plan?.stopped_reasoning_unlinked===true&&<aside className="info" aria-label="原分析需要核对"><strong>这份申请未关联已停用的原步骤</strong><p>请先核对申请，尚未开始的旧申请不能直接发送。</p></aside>}
  {view.error&&<p role="alert">{notice(view.error,'操作未完成，请刷新重试。')}</p>}
  {view.notice&&<p role="status">{notice(view.notice,'已更新。')}</p>}
  {!view.localSaved&&<aside className="info" role="alert"><strong>内容尚未保存</strong><p>请重试保存后继续。</p><Button Btn={Btn} disabled={view.busy||view.loading} onClick={()=>onAction('retryLocalSave')}>重试保存</Button></aside>}
  {view.busy&&!view.loading&&<p role="status">正在处理…</p>}
  <ReadStatus state={reads.context} label="当前办理条件读取状态" loading="正在核对办理条件…" failed="办理条件暂未读回，请刷新。"/>
  <ReadStatus state={reads.plan} label="原申请与原答读取状态" loading="正在读取建议…" failed="建议暂未读回，请刷新。"/>
  {view.pending&&<aside className="info" aria-label="原申请送达结果待核对"><strong>操作结果待核对</strong><p>请先核对结果，勿重复提交。</p><div className="actions"><Button Btn={Btn} disabled={view.busy} onClick={()=>onAction('recover')}>查看处理结果</Button>{view.notFound===true&&<Button Btn={Btn} disabled={view.busy||!view.localSaved} onClick={()=>onAction('resendOriginal')}>继续原申请</Button>}{view.pendingRejected&&<Button Btn={Btn} disabled={view.busy||!view.localSaved} onClick={()=>onAction('releaseRejected')}>修改输入</Button>}</div>{view.pendingRejected&&<p>{notice(view.pendingRejected.message,'请求已拒绝，请修改后重试。')}</p>}</aside>}
  {view.needsCheck===true&&!view.pending&&<aside className="info" aria-label="已保存申请的结果原件待核对"><strong>建议结果待核对</strong><p>请先核对结果，勿重复提交。</p><Button Btn={Btn} disabled={view.busy} onClick={()=>onAction('recover')}>核对结果</Button></aside>}
  {contextReady&&context&&<div className="same-advice-muted"><p>{context.goal_purpose||''}</p><p>本次数据整理截至：{clock(context.data_as_of)} · 指定模型：{model?.name||'模型信息尚未核对'}</p></div>}
  {contextReady&&initialEvaluationNotice&&<aside className="info" aria-label="首次评价的分析限制" role="note"><strong>当前不能生成预览</strong><p>{notice(initialEvaluationNotice.message,'当前评价暂不能生成发送预览，请核对数据评价。')}</p></aside>}
  {contextReady&&blockedReasons.length>0&&<aside className="info" aria-label="当前办理条件"><strong>继续前需核对</strong><ul>{blockedReasons.map((row,index)=><li key={index}>{notice(row.message,'当前办理条件尚未满足，请核对业务配置。')}</li>)}</ul><p>已保存的建议仍可查看。</p></aside>}
  {account?.role==='admin'&&context&&(permissions.can_register_price===true||permissions.can_configure_quota===true)&&<AdminFees context={context} draft={draft} blocked={blocked} editingBlocked={editingBlocked} onDraft={onDraft} onAction={onAction} Btn={Btn}/>}
  {!context&&!view.loading&&reads.context!=='failed'&&<p role="note">资料暂未读回，无法创建新建议。</p>}
  {mayPrepare&&contextReady&&<Stage number={1} title={phase==='reviewed'?'为下一份建议整理资料':'整理本任务已批准的资料'}>
   {phase==='reviewed'&&<p>上一份建议和决定仍可查看。</p>}
   <label className="same-advice-field">选择批准资料<select aria-label="选择批准资料" value={selection<0?'':String(selection)} disabled={editingBlocked||permissions.can_prepare!==true} onChange={event=>{if(event.target.value===''){onDraft({selected_document_source:null});return;}const row=sources[Number(event.target.value)];if(row)onDraft({selected_document_source:row.selection});}}><option value="">请选择本次采用的资料</option>{sources.map((row,index)=><option key={adviceCanonical(row.selection)} value={index}>{row.title}{Number.isSafeInteger(row.excerpt_count)?' · '+row.excerpt_count+'段摘录':''}</option>)}</select></label>
   {selection>=0&&sources[selection].description&&<p>{sources[selection].description}</p>}
   {draft.selected_document_source&&selection<0&&<p role="note">所选资料不可用，请重新选择。</p>}
   <p>分析问题沿用任务内容。</p>
   <Reason label="本次整理资料的说明" value={draft.prepare_reason} disabled={editingBlocked||permissions.can_prepare!==true} onChange={value=>onDraft({prepare_reason:value})}/>
   <Button Btn={Btn} primary disabled={blocked||permissions.can_prepare!==true||selection<0||!draft.prepare_reason?.trim()} onClick={()=>onAction('prepare')}>{phase==='reviewed'?'新增建议':'整理资料'}</Button>
  </Stage>}
  {plan&&<p role="status"><strong>{PHASES[phase]||'原记录状态需要核对'}</strong>{plan.data_as_of?' · 本建议资料截至 '+clock(plan.data_as_of):''}</p>}
  {['queued','preparing'].includes(phase)&&<p>资料整理中…</p>}
  {plan&&!['queued','preparing'].includes(phase)&&<Stage number={2} title="核对原文与指定模型授权">
   <ReadStatus state={reads.documents} label="原资料全文读取状态" loading="正在读取资料…" failed="资料暂未读回，请刷新。"/>
   {documents.length?<div className="same-advice-columns">{documents.map(document=><Document key={document.evidence_ref.evidence_id} document={document} model={model} draft={draft} disabled={blocked} canAuthorize={phase==='awaiting_consent'&&permissions.can_authorize===true&&modelReady} onDraft={onDraft} onAction={onAction} Btn={Btn}/>)}</div>:!['loading','failed'].includes(reads.documents)&&<p role="note">资料尚未读取。</p>}
  </Stage>}
  {phase==='awaiting_consent'&&<Stage number={3} title="核对匿名全文与本次费用">
   <Reason label="生成预览的说明" value={draft.preview_reason} disabled={editingBlocked||permissions.can_preview!==true} onChange={value=>onDraft({preview_reason:value})}/>
   <Button Btn={Btn} disabled={blocked||permissions.can_preview!==true||!allAuthorized||!draft.preview_reason?.trim()} onClick={()=>onAction('preparePreview')}>{preview?'重新核对并保存一份预览':'查看发送内容和费用'}</Button>
  </Stage>}
  {plan&&<ReadStatus state={reads.preview} label="原发送预览读取状态" loading="正在读取发送预览…" failed="发送预览暂未读回，请刷新。"/>}
  {preview&&<Stage number={4} title={['send_queued','generating','closing','ready_for_review','reviewed'].includes(phase)?'本次原发送范围与费用预览':'核对后明确开始'}>
   {!plan?.result&&<ActualQuestion preview={preview}/>}
   <p><strong>{preview.model?.name||model?.name||'指定模型'}</strong> · {preview.model?.provider==='qwen'?'Qwen公有云':preview.model?.provider==='deepseek'?'DeepSeek公有云':'已登记的公有云模型'}</p>
   <p className="same-advice-price"><strong>本次费用按上界估算：{money(preview.maximum_cost_minor_units,preview.currency)}</strong></p><p>配置的单次费用上限：{money(preview.configured_ceiling_minor_units,preview.currency)}。实际费用以供应商账单为准。</p>
   <p className="same-advice-muted">价格核对时间：{clock(preview.price_checked_at)}<br/>本次价格依据有效至：{clock(preview.price_expires_at)}</p>
   <SentText preview={preview}/>
   {phase==='awaiting_consent'&&<>
    <label className="same-advice-check"><input type="checkbox" checked={draft.preview_checked===true} disabled={editingBlocked||!canStart} onChange={event=>onDraft({preview_checked:event.target.checked})}/>我已核对这份实际匿名发送全文和本次费用上限，同意开始一次分析</label>
    <Reason label="明确开始分析的说明" value={draft.start_reason} disabled={editingBlocked||!canStart} onChange={value=>onDraft({start_reason:value})}/>
    {!draft.start_reason&&draft.preview_reason?.trim()&&<Button Btn={Btn} disabled={editingBlocked} onClick={()=>onDraft({start_reason:draft.preview_reason})}>沿用预览说明</Button>}
    <div className="actions"><Button Btn={Btn} primary disabled={blocked||!canStart||preview.start_endpoint_available!==true||preview.latest_for_plan!==true||draft.preview_checked!==true||!draft.start_reason?.trim()} onClick={()=>onAction('startAnalysis')}>开始分析</Button></div>
   </>}
  </Stage>}
  {['send_queued','generating','closing'].includes(phase)&&<aside className="info" aria-label="原分析进度"><strong>{PHASES[phase]}</strong><p>{phase==='closing'?'结果保存中，完成后可审核。':'正在等待分析结果。'}</p><Button Btn={Btn} disabled={view.busy} onClick={()=>onAction('refresh')}>刷新进度</Button></aside>}
  {plan?.result&&<Stage number={5} title="核对原建议并保存决定">
   <ActualQuestion preview={preview}/>
   <div className="same-advice-decision"><Opinion result={plan.result} preview={preview}/><div><ReviewScope scope={plan.source_review_scope} dataAsOf={plan.data_as_of} input={cloudInput(preview)?.decision_input}/>
    {ready?<form onSubmit={event=>{event.preventDefault();onAction('review');}} aria-label="保存本次人工决定">
     <fieldset className="same-advice-review-choices" disabled={blocked||permissions.can_review!==true}><legend>这次如何处理</legend>{Object.entries(DECISIONS).map(([value,label])=><label key={value}><input type="radio" name="same-goal-advice-review" value={value} checked={draft.review_decision===value} onChange={()=>onDraft({review_decision:value})}/>{label}</label>)}</fieldset>
     <Reason label="作出此决定的理由" value={draft.review_reason} disabled={editingBlocked||permissions.can_review!==true} onChange={value=>onDraft({review_reason:value})}/>
     {!view.documents&&<p role="note">{reads.documents==='loading'?'正在读取依据，暂不能保存。':'依据尚未读回，请刷新后保存。'}</p>}
     <Button Btn={Btn} primary disabled={blocked||permissions.can_review!==true||!view.documents||!Object.hasOwn(DECISIONS,draft.review_decision)||!draft.review_reason?.trim()} onClick={()=>onAction('review')}>保存本次决定</Button>
    </form>:phase!=='reviewed'&&<p role="note">当前暂不能提交审核。</p>}
    {plan.reviews?.length>0&&<section aria-label="已保存的人工决定"><h6>已保存的人工决定</h6>{plan.reviews.map((review,index)=><article key={index}><strong>{DECISIONS[review.decision]||'原决定'}</strong><p className="same-advice-text">{review.reason}</p><p className="same-advice-muted">{clock(review.reviewed_at)}</p></article>)}</section>}
   </div></div>
  </Stage>}
  {canCancel&&<details><summary>取消本次申请，保留资料和原记录</summary><Reason label="取消本次申请的原因" value={draft.cancel_reason} disabled={editingBlocked} onChange={value=>onDraft({cancel_reason:value})}/><Button Btn={Btn} disabled={blocked||!draft.cancel_reason?.trim()} onClick={()=>onAction('cancel')}>确认取消</Button><p>已发出的请求无法撤回，可能产生费用。</p></details>}
  <ReadStatus state={reads.history} label="历史申请目录读取状态" loading="正在读取历史建议…" failed="历史建议暂未读回，请刷新。"/>
  {view.list&&<details className="same-advice-history"><summary>已保存的建议申请（{view.list.records?.length||0}份）</summary>{view.list.records?.length?view.list.records.map((row,index)=><article key={row.plan_id}><p>建议申请 {index+1} · {PHASES[row.phase]||'原状态待核对'} · {clock(row.data_as_of)}</p><Button Btn={Btn} disabled={view.busy||view.loading||pending||row.plan_id===view.selectedPlanId} onClick={()=>onAction('selectPlan',row.plan_id)}>{row.plan_id===view.selectedPlanId?'正在查看此份申请':'打开这份原申请'}</Button></article>):<p>{plan?'暂无其他申请。':'暂无历史建议。'}</p>}</details>}
 </>;
}

export default function SameGoalAdviceV310({account,task,Btn,requestJSON=json,onRegisterGuard,onSaved,
 storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks,visible=true}){
 const [,draw]=useState(0),[localError,setLocalError]=useState(''),latest=useRef(null),lifetime=useRef(null);
 const [v2Available,setV2Available]=useState(null),v2Refresh=useRef(null);
 const continuationGuards=useRef(new Map()),formalGuards=useRef(new Map()),continuationSource=useRef(null);
 const [continuationSection,setContinuationSection]=useState({scope:'',open:false});
 const [continuationMessage,setContinuationMessage]=useState({scope:'',text:''});
 if(!lifetime.current)lifetime.current=createRetainedControllerLifecycle();
 let csrf='';try{csrf=tabStorage?.getItem('ga-setup-csrf')||'';}catch{}
 const key=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,task?.id,task?.instance_id,task?.instance_release_id,csrf]);
 useEffect(()=>{setV2Available(null);},[key]);
 latest.current={key,visible,onRegisterGuard,onSaved};
 const holder=useMemo(()=>{try{return {controller:new SameGoalAdviceControllerV310({account,task,request:requestJSON,storage,session:tabStorage,locks,
  beforePlanChange:from=>{try{const guard=continuationGuards.current.get(JSON.stringify([key,from||'']));return !guard||guard()===true;}catch{return false;}}}),error:''};}catch{return {controller:null,error:'当前任务无法打开，请重新进入。'};}},[key,requestJSON,storage,tabStorage,locks]);
 const c=holder.controller;
 const continuationScope=()=>JSON.stringify([key,c?.planId||'']);
 const continuationSafe=()=>{try{const key=continuationScope(),guard=continuationGuards.current.get(key),formal=formalGuards.current.get(key);return (!guard||guard()===true)&&(!formal||formal()===true);}catch{return false;}};
 const run=async(method,...args)=>{
  if(!c||latest.current.key!==key)return;
  if(['selectPlan','prepare','cancel'].includes(method)&&!continuationSafe()){
   setContinuationMessage({scope:continuationScope(),text:'请先保存草案或核对申请状态。'});return;
  }
  setLocalError('');try{const result=await c[method](...args);if(latest.current.key===key&&!c.denied&&['prepare','startAnalysis','review','cancel'].includes(method)){
   try{await latest.current.onSaved?.();}catch{if(latest.current.key===key)setLocalError('已保存，任务摘要暂未更新，请刷新。');}
  }return result;}
  catch{if(latest.current.key===key&&c.active&&!c.denied)setLocalError('操作结果待确认，请核对后继续。');}
 };
 useEffect(()=>{
  setLocalError('');const guard=()=>c?c.guard()&&continuationSafe():false;latest.current.onRegisterGuard?.(guard);
  if(!c)return()=>latest.current.onRegisterGuard?.(null,guard);
  const lease=lifetime.current.setup(c,key);if(!lease)return;
  let mounted=true;c.onChange=()=>{if(mounted&&c.active&&!c.suspended&&latest.current.key===key)draw(value=>value+1);};
  const unload=event=>{if(!guard()){event.preventDefault();event.returnValue='';}};
  const focus=()=>{if(latest.current.visible&&c.active&&!c.suspended&&!c.busy)run('refresh');};
  const storageChanged=event=>{if(!event.key||event.key.startsWith(c.key)||event.key.startsWith('ga-setup-csrf'))c.storageChanged();};
  globalThis.addEventListener?.('beforeunload',unload);globalThis.addEventListener?.('focus',focus);globalThis.addEventListener?.('storage',storageChanged);
  run('open');
  return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);globalThis.removeEventListener?.('focus',focus);globalThis.removeEventListener?.('storage',storageChanged);lifetime.current.cleanup(lease);};
 },[c,key]);
 useEffect(()=>{
  if(!c||!visible)return;let stopped=false,timer;
  const schedule=()=>{timer=setTimeout(async()=>{if(stopped||!c.active||c.suspended)return;const view=c.view();if(!view.busy&&!view.loading&&!view.pending&&!view.denied&&['queued','preparing','send_queued','generating','closing'].includes(view.plan?.phase))await run('refresh');if(!stopped)schedule();},15000);};schedule();
  return()=>{stopped=true;clearTimeout(timer);};
 },[c,key,visible]);
 let matches=false;try{matches=tabStorage?.getItem('ga-setup-csrf-user')===account?.user_id&&tabStorage?.getItem('ga-setup-csrf-workspace')===account?.workspace_id&&tabStorage?.getItem('ga-setup-csrf')===csrf;}catch{}
 const view=c?.view();
 // Keep the already authenticated source identity while its parent performs
 // read-only refreshes. No answer/text/permission is retained in this ref.
 if(!matches||view?.denied||continuationSource.current?.key!==key
    ||c?.planId&&continuationSource.current?.plan.plan_id!==c.planId)continuationSource.current=null;
 if(matches&&!view?.denied&&view?.plan?.phase==='reviewed')continuationSource.current={key,
  plan:{plan_id:view.plan.plan_id,phase:view.plan.phase,version:view.plan.version}};
 const continuation=continuationSource.current,sectionScope=continuation?JSON.stringify([key,continuation.plan.plan_id]):'';
 const sectionMounted=!!sectionScope&&continuationSection.scope===sectionScope;
 const sectionOpen=sectionMounted&&continuationSection.open;
 const toggleContinuation=()=>{if(sectionOpen&&!continuationSafe()){
  setContinuationMessage({scope:sectionScope,text:'草案状态待核对，请先刷新。'});return;
 }setContinuationMessage({scope:'',text:''});setContinuationSection({scope:sectionScope,open:!sectionOpen});};
 return <section className="media-form same-goal-advice-v310" aria-label="本任务业务建议" hidden={!visible}>
  <div className="same-advice-heading"><h4>业务建议</h4><div className="actions"><Button Btn={Btn} disabled={!c||view?.busy||!matches||view?.denied} onClick={()=>{run('refresh');v2Refresh.current?.();}}>刷新</Button></div></div>
  {matches&&!view?.denied&&<AdvicePreparationV2 key={key} account={account} task={task} requestJSON={requestJSON} storage={storage} locks={locks} onAvailability={setV2Available} onRefreshReady={value=>{v2Refresh.current=value;}}/>}
  {!c?<p role="alert">{holder.error}</p>:!matches||view.denied?<p role="alert">账号、工作区或权限已变化，资料已隐藏，请重新打开对应工作区。</p>:<>
   {localError&&!view.error&&<p role="alert">{localError}</p>}
   {v2Available===true?<details><summary>{!view.plan&&hasDraftWork(view.draft)?'继续未完成的建议':'历史建议'}</summary><SameGoalAdviceContentV310 view={view} account={account} Btn={Btn} onAction={run} onDraft={patch=>run('updateDraft',patch)} historyOnly/></details>:v2Available===false?
    <SameGoalAdviceContentV310 view={view} account={account} Btn={Btn} onAction={run} onDraft={patch=>run('updateDraft',patch)}/>:null}
   {continuation&&<section className="media-form" aria-label="原建议的后续办理草案">
    <div className="same-advice-heading"><div><h5>后续办理</h5><p>先拟定并保存方案，保存草案不会执行任务或操作设备。</p></div>
     <Button Btn={Btn} disabled={!sectionOpen&&(view.busy||view.loading)} aria-expanded={sectionOpen}
      onClick={toggleContinuation}>{sectionOpen?'收起办理草案':'拟定后续办理方案'}</Button></div>
    {continuationMessage.scope===sectionScope&&continuationMessage.text&&<p role="alert">{continuationMessage.text}</p>}
    {sectionMounted&&<div hidden={!sectionOpen}><SameGoalContinuationV312 key={sectionScope} account={account}
     task={task} advicePlan={continuation.plan} Btn={Btn} requestJSON={requestJSON} storage={storage}
     tabStorage={tabStorage} locks={locks} visible={visible&&sectionOpen}
     onRegisterGuard={(guard,previous)=>{const current=continuationGuards.current.get(sectionScope);
      if(guard)continuationGuards.current.set(sectionScope,guard);
      else if(!previous||current===previous)continuationGuards.current.delete(sectionScope);}}
     onSaved={()=>latest.current.key===key?latest.current.onSaved?.():undefined}/><FormalContinuationContextV344 key={'formal:'+sectionScope} account={account} task={task} advicePlan={continuation.plan} requestJSON={requestJSON} storage={storage} tabStorage={tabStorage} locks={locks} Btn={Btn} onRegisterGuard={(guard,previous)=>{const current=formalGuards.current.get(sectionScope);if(guard)formalGuards.current.set(sectionScope,guard);else if(!previous||current===previous)formalGuards.current.delete(sectionScope);}}/></div>}
   </section>}
  </>}
 </section>;
}
