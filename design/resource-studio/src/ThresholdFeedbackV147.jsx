import React,{useEffect,useRef,useState} from 'react';
import ThresholdStudyV149 from './ThresholdStudyV149.jsx';
import {json} from './runtime-api';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {THRESHOLD_ROOT_V147,thresholdLabelsV147,thresholdScopeV147,thresholdIdentityV147,checkedThresholdContextV147,checkedThresholdViewV147,checkedThresholdRecoveryV147,thresholdRecoveryPathV147,emptyThresholdFormV147} from './threshold-feedback-contract-v147.mjs';
import {ThresholdFeedbackRecoveryJournalV148,thresholdRequestStatePathV148,checkedThresholdRequestStateV148,checkedThresholdHistoryV148,checkedThresholdHistoryLookupV148} from './threshold-request-recovery-contract-v148.mjs';

const numeric=value=>Object.is(value,-0)?'-0.0':String(value);
const statusText={draft:'未提交草稿',submitted:'已提交',cancelled:'草稿已取消'};
function DefaultButton({children,primary,...props}){return <button type="button" {...props}>{children}</button>;}

export default function ThresholdFeedbackV147({record,instanceId,account,Btn=DefaultButton}){
 const scopeKey=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,instanceId,record?.id,record?.extractor_id,record?.version]),renderScope=useRef(scopeKey);renderScope.current=scopeKey;
 const ctxRef=useRef(null),epoch=useRef(0),operation=useRef(null);
 const [opened,setOpened]=useState(false),[verified,setVerified]=useState(null),[form,setForm]=useState(emptyThresholdFormV147),[context,setContext]=useState(null),[view,setView]=useState(null),[pending,setPending]=useState(null),[drafts,setDrafts]=useState([]),[probeHash,setProbeHash]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[cacheError,setCacheError]=useState(''),[notice,setNotice]=useState('');
 const [retirement,setRetirement]=useState(null),[requestState,setRequestState]=useState(null),[retireOpen,setRetireOpen]=useState(false),[retireReason,setRetireReason]=useState(''),[history,setHistory]=useState(null);
 const visible=opened&&verified===scopeKey,writable=['admin','operator'].includes(account?.role);
 useEffect(()=>{epoch.current++;ctxRef.current?.journal?.dispose();ctxRef.current=null;operation.current=null;setOpened(false);setVerified(null);setBusy(false);setContext(null);setView(null);setPending(null);setRetirement(null);setRequestState(null);setRetireOpen(false);setRetireReason('');setHistory(null);setForm(emptyThresholdFormV147());setError('');setCacheError('');setNotice('');return()=>{epoch.current++;ctxRef.current?.journal?.dispose();if(ctxRef.current)ctxRef.current.closed=true;};},[scopeKey]);
 useEffect(()=>{
  const wake=()=>{const ctx=ctxRef.current;if(!ctx||!opened)return;const ticket=++epoch.current;setVerified(null);operation.current=null;setBusy(false);if(document.visibilityState==='hidden')return;ctx.identity(ticket).then(()=>{if(ctx.current(ticket))setVerified(scopeKey);}).catch(cause=>{if(ctx.current(ticket))setError(cause.message+' 原稿和原请求保留。');});};
  window.addEventListener('focus',wake);document.addEventListener('visibilitychange',wake);return()=>{window.removeEventListener('focus',wake);document.removeEventListener('visibilitychange',wake);};
 },[opened,scopeKey]);
 function setLocal(ctx,next){
  if(ctxRef.current!==ctx||renderScope.current!==ctx.scopeKey||ctx.closed)return false;
  try{ctx.journal.save(next);setForm(structuredClone(next));if(ctx.journal.isUnsaved())throw Error('停止接收的说明尚未完整保存在本机。');setCacheError('');return true;}catch(cause){setForm(structuredClone(ctx.journal.form||next));setCacheError(cause.message+' 本机稿未完整保存，暂不发送。');return false;}
 }
 function refreshDrafts(ctx){if(!ctx.current(epoch.current))return;try{setDrafts(ctx.journal.drafts());}catch(cause){if(ctx.current(epoch.current))setError(cause.message+' 本机保留稿未被覆盖。');}}
 async function operate(callback){
  const ctx=ctxRef.current,ticket=epoch.current;if(!ctx?.current(ticket)||operation.current)return;const token={};operation.current=token;setBusy(true);setError('');
  try{await callback(ctx,ticket);}catch(cause){if(ctx.current(ticket)){setError(cause.message+' 原输入和原请求保留。');try{const restored=await ctx.journal.pending();if(!ctx.current(ticket))return;const retiring=await ctx.journal.retirement();if(ctx.current(ticket)){setPending(restored||retiring?.original||null);setRetirement(retiring);}}catch(storageError){if(ctx.current(ticket))setCacheError(storageError.message);}}}
  finally{if(operation.current===token){operation.current=null;if(ctxRef.current===ctx)setBusy(false);}}
 }
 async function open(){
  if(operation.current)return;ctxRef.current?.journal?.dispose();let scope;
  try{scope=thresholdScopeV147(account,instanceId,record);if(typeof record.extractor_id!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(record.extractor_id))throw Error('原规则身份无法核对；原稿保留，未读取其它判断。');}catch(cause){setError(cause.message);return;}
  const ctx={scope,scopeKey,account:{...account},capabilityID:record.extractor_id,closed:false,journal:new ThresholdFeedbackRecoveryJournalV148(scope),view:null,basis:null,ruleID:null},request=scopedPreparationRequester(account,json);
  ctxRef.current=ctx;const ticket=++epoch.current;setOpened(true);setVerified(null);setError('');setNotice('');setContext(null);setView(null);setHistory(null);setProbeHash(null);setRequestState(null);setRetireOpen(false);
  ctx.current=now=>!ctx.closed&&ctxRef.current===ctx&&renderScope.current===ctx.scopeKey&&epoch.current===now;
  ctx.identity=async now=>{try{if(!ctx.current(now)||document.visibilityState==='hidden')throw Error('页面或原登记范围已变化。');const me=thresholdIdentityV147(await request('/me'),ctx.account);if(!ctx.current(now))throw Error('页面或原登记范围已变化。');return me;}catch(cause){if(ctx.current(now))setVerified(null);throw cause;}};
  ctx.read=async(path,now,options={})=>{const me=await ctx.identity(now);if(options.method&&options.method!=='GET'&&!['admin','operator'].includes(me.role))throw Error('当前账号没有保存判断的权限。');let value,failed;try{value=await request(path,options);}catch(cause){failed=cause;}await ctx.identity(now);if(failed)throw failed;return value;};
  await operate(async(current,now)=>{
   await current.identity(now);
   try{setForm(structuredClone(current.journal.open()));setRetireReason(current.journal.retirementReason);setCacheError('');}catch(cause){if(current.journal.form)setForm(structuredClone(current.journal.form));setCacheError(cause.message+' 本机稿尚未完整保存，暂不发送。');}
   const restored=await current.journal.pending();if(!current.current(now))return;
   const retiring=await current.journal.retirement();if(!current.current(now))return;setPending(restored||retiring?.original||null);setRetirement(retiring);refreshDrafts(current);
   // Original-ID recovery needs the actual identity and local original, not a
   // newly readable/published rule. Missing current context only blocks writes.
   if(current.current(now))setVerified(scopeKey);
   let rule;try{rule=await current.read('/local-rules/'+encodeURIComponent(current.capabilityID),now);}catch(cause){
    if(![404,409,422].includes(cause.status))throw cause;
    const saved=checkedThresholdHistoryLookupV148(await current.read(THRESHOLD_ROOT_V147+'/observations/'+current.scope.observation_id+'/feedback-history',now),current.scope,null,current.capabilityID);
    if(!current.current(now))return;current.ruleID=saved?.basis_reference.rule_id||null;setHistory(saved);setNotice(saved?'原规则资料暂不可核，已独立读回只读人工历史；不能据此保存新判断或用于策略。':'原规则资料暂不可核，未找到已保存判断；原本机稿保留，暂不能发送新判断。');return;
   }
   if(rule?.id!==record.extractor_id||!Array.isArray(rule.rules)||rule.rules.length!==1||typeof rule.rules[0].id!=='string')throw Error('此原登记不属于首片支持的唯一受管理数值规则。');
   current.ruleID=rule.rules[0].id;await loadContext(current,now);
   if(current.current(now))setVerified(scopeKey);
  });
 }
 async function loadContext(ctx,ticket){
  if(ctx.current(ticket)){ctx.basis=null;ctx.view=null;setContext(null);setView(null);setHistory(null);}
  const value=checkedThresholdContextV147(await ctx.read(THRESHOLD_ROOT_V147+'/observations/'+ctx.scope.observation_id+'/feedback-context?'+new URLSearchParams({rule_id:ctx.ruleID}),ticket),ctx.scope,ctx.ruleID);
  if(!ctx.current(ticket))return;ctx.basis=value.basis;ctx.view=value.feedback;setContext(value);setView(value.feedback);
  if(!value.supported){const saved=checkedThresholdHistoryLookupV148(await ctx.read(THRESHOLD_ROOT_V147+'/observations/'+ctx.scope.observation_id+'/feedback-history?'+new URLSearchParams({rule_id:ctx.ruleID}),ticket),ctx.scope,ctx.ruleID,ctx.capabilityID);if(ctx.current(ticket))setHistory(saved);return;}
  const local=ctx.journal.form||emptyThresholdFormV147();
  if(value.supported&&local.target===null&&local.label===null&&!local.reason&&!local.cancel_reason){const next=value.feedback?{label:value.feedback.label,reason:value.feedback.reason,cancel_reason:'',target:value.feedback.can_edit?{kind:'save',id:value.feedback.id,version:value.feedback.version}:null}:{...local,target:{kind:'create',observation_version:value.basis.observation_version}};setLocal(ctx,next);}
 }
 function edit(delta){const ctx=ctxRef.current;if(!visible||busy||pending||!ctx?.journal.recoveryReady)return;setLocal(ctx,{...(ctx.journal.form||form),...delta});}
 function accept(ctx,value,sent){
  ctx.view=value;setView(value);setPending(null);setProbeHash(null);const body=JSON.parse(sent.exact_body),local=ctx.journal.form;
  const tail=sent.path.slice(('/api'+THRESHOLD_ROOT_V147).length),hasLabel=Object.prototype.hasOwnProperty.call(body,'label');
  const bound=tail==='/feedback'?local?.target?.kind==='create'&&local.target.observation_version===body.expected_observation_version:
   tail.endsWith('/revisions')?local?.target===null:local?.target?.kind==='save'&&local.target.id===value.id&&local.target.version===body.expected_version;
  // Shared nonce recovery proves that original command, not that another
  // owner's independent editor was saved. Only advance a matching local intent;
  // otherwise leave its entire slot and human text unchanged.
  const matches=!!local&&bound&&local.label===(hasLabel?body.label:value.label)&&local.reason===(hasLabel?body.reason:value.reason)
   &&(!tail.endsWith('/cancel')||local.cancel_reason===body.reason);
  const sameResult=!hasLabel||value.label===body.label&&value.reason===body.reason;
  if(matches&&sameResult)setLocal(ctx,{...local,target:value.can_edit?{kind:'save',id:value.id,version:value.version}:null});
  setNotice(!matches||!sameResult?'原编号的服务器回执已核对；本窗口的另一份人稿仍独立保留，尚未保存到该服务器稿。':value.status==='draft'?'服务器已保存判断草稿；这还不是正式提交或策略变更。':value.status==='submitted'?'判断已正式提交，原历史保留；尚未改变任何规则。':'草稿取消已保存，原判断和历史仍保留。');refreshDrafts(ctx);
 }
 async function original(ctx,ticket,sent,allowPost){
  await ctx.journal.withOriginal(sent,()=>ctx.identity(ticket),async(original,active)=>{
   const retiring=await ctx.journal.retirement();if(!ctx.current(ticket))return;if(retiring){if(JSON.stringify(retiring.original)!==JSON.stringify(original))throw Error('另一个原请求正在核对停止接收，未替换任何正文。');await resolveState(ctx,ticket,original,await readState(ctx,ticket,original),retiring);return;}
   const lookup=async()=>checkedThresholdRecoveryV147(await ctx.read(thresholdRecoveryPathV147(original),ticket),ctx.scope,original);
   let value;try{value=await lookup();}catch(cause){if(cause.status!==409||allowPost)throw cause;await resolveState(ctx,ticket,original,await readState(ctx,ticket,original));return;}
   if(!value&&allowPost){if(!ctx.basis)throw Error('原依据目前不可核，只能按原编号查证，暂不发送。');if(!active||!ctx.journal.canSend())throw Error('原请求尚未完整保留或多窗口协调不可用，未发送。');
    const response=await ctx.read(original.path.slice(4),ticket,{method:original.method,body:original.exact_body});checkedThresholdRecoveryV147({schema:'threshold-evolution-request-recovery-v147',found:true,feedback:response},ctx.scope,original);
    value=await lookup();if(!value)throw Error('保存回应尚未由原编号读回证实，请保留原请求继续核对。');
   }
   if(value){if(!ctx.current(ticket))return;ctx.journal.settleInside(original,value);if(ctx.current(ticket))accept(ctx,value,original);}
   else if(!allowPost)await resolveState(ctx,ticket,original,await readState(ctx,ticket,original));
  });
 }
 async function readState(ctx,ticket,original){
  const value=await checkedThresholdRequestStateV148(await ctx.read(thresholdRequestStatePathV148(original),ticket),ctx.scope,original);await ctx.identity(ticket);return value;
 }
 function checkSavedEntry(entry,saved){
  if(entry.id!==saved.feedback_id||entry.root_id!==saved.root_id||entry.revision!==saved.revision||entry.version<saved.version||(entry.version===saved.version&&entry.status!==saved.status))throw Error('原保存引用与实际判断历史不一致，未结算原请求。');
 }
 async function resolveState(ctx,ticket,original,state,retiring=null){
  if(!ctx.current(ticket))return;setRequestState(state);
  if(state.outcome==='unknown'){setProbeHash(original.wire_sha256);setNotice(retiring?'停止接收结果尚未查明；只能继续核对，或明确按同一编号重试停止接收。':'尚未查到原请求；这不表示它永远不会送达。可以明确停止接收，再修改原稿。');return;}
  if(state.outcome==='saved'){
   let saved,historyOnly;
   try{saved=checkedThresholdViewV147(await ctx.read(THRESHOLD_ROOT_V147+'/feedback/'+state.saved.feedback_id,ticket),ctx.scope);checkSavedEntry(saved,state.saved);}
   catch(cause){if(cause.status!==409)throw cause;historyOnly=checkedThresholdHistoryV148(await ctx.read(THRESHOLD_ROOT_V147+'/feedback/'+state.saved.feedback_id+'/history',ticket),ctx.scope,ctx.ruleID,ctx.capabilityID);checkSavedEntry(historyOnly.entry,state.saved);}
   if(!ctx.current(ticket))return;ctx.journal.resolveOriginal(original,state,retiring);setRetirement(null);setRetireOpen(false);
   if(saved){accept(ctx,saved,original);return;}
   ctx.basis=null;ctx.view=null;setContext(null);setView(null);setHistory(historyOnly);setPending(null);setProbeHash(null);setNotice('原操作已经保存。历史理由已读回，但机器依据目前不可用，不能用于策略；本窗口人稿仍独立保留。');refreshDrafts(ctx);return;
  }
  ctx.journal.resolveOriginal(original,state,retiring);setPending(null);setRetirement(null);setRetireOpen(false);setProbeHash(null);setNotice('原请求已停止接收，原文和本窗口人稿已经保留。正在核对当前版本。');
  await loadContext(ctx,ticket);if(!ctx.current(ticket))return;
  const local=ctx.journal.form,target=ctx.basis?(ctx.view?(ctx.view.can_edit?{kind:'save',id:ctx.view.id,version:ctx.view.version}:null):{kind:'create',observation_version:ctx.basis.observation_version}):null;
  if(target&&JSON.stringify(local.target)!==JSON.stringify(target)){ctx.journal.archive();if(!setLocal(ctx,{...local,target}))throw Error('当前版本的人稿尚未完整保存，暂不另存。');}
  setNotice('原请求已永久停止接收，原文和本窗口人稿已保留；仅在当前依据、权限和版本可核对时，才能修改后明确另存。');refreshDrafts(ctx);
 }
 function editRetireReason(value){
  const ctx=ctxRef.current;if(!visible||busy||retirement||!ctx?.journal.recoveryReady)return;setRetireReason(value);try{ctx.journal.saveRetirementReason(value);if(!ctx.journal.isUnsaved())setCacheError('');}catch(cause){setCacheError(cause.message+' 停止接收说明仍保留，暂不发送。');}
 }
 async function retirementAction(ctx,ticket,value,send){
  await ctx.journal.withRetirement(value,()=>ctx.identity(ticket),async active=>{
   let state=await readState(ctx,ticket,value.original);if(state.outcome==='unknown'&&send){
    if(!active||!ctx.journal.canSend())throw Error('停止接收原请求尚未完整保存，不能发送。');
    const response=await checkedThresholdRequestStateV148(await ctx.read(value.path.slice(4),ticket,{method:'POST',body:value.exact_body}),ctx.scope,value.original);await ctx.identity(ticket);if(response.outcome==='unknown')throw Error('停止接收回应不能证明结果，请继续核对。');
    state=await readState(ctx,ticket,value.original);
   }
   await resolveState(ctx,ticket,value.original,state,value);
  });
 }
 async function confirmRetirement(){await operate(async(ctx,ticket)=>{
  if(!writable||!pending||requestState?.outcome!=='unknown'||requestState.original_request_id!==pending.request_id||requestState.original_sha256!==pending.wire_sha256||!ctx.journal.canSend()||cacheError)throw Error('请先核对原请求、权限与本机稿，再明确停止接收。');
  const existing=await ctx.journal.retirement();if(!ctx.current(ticket))return;if(existing){setRetirement(existing);throw Error('已有停止接收操作待核对，不能换编号或正文。');}
  const value=await ctx.journal.installRetirement(pending,ctx.journal.retirementReason,()=>ctx.identity(ticket));if(!ctx.current(ticket))return;setRetirement(value);await retirementAction(ctx,ticket,value,true);
 });}
 async function mutate(action){await operate(async(ctx,ticket)=>{
  if(!visible||!writable||!ctx.basis||!ctx.journal.canSend()||cacheError)throw Error('当前依据、权限或本机保存条件尚未完整，未发送。');
  const existing=await ctx.journal.pending();if(!ctx.current(ticket))return;if(existing){setPending(existing);throw Error('已有原请求待核对，不能替换其编号或正文。');}
  const request_id=crypto.randomUUID(),local=ctx.journal.form,current=ctx.view;let method='POST',path=THRESHOLD_ROOT_V147+'/feedback',body;
  if(action==='save'){
   if(!current){if(local.target?.kind!=='create')throw Error('请先重新核对原登记范围。');body={request_id,observation_id:ctx.scope.observation_id,rule_id:ctx.ruleID,expected_observation_version:local.target.observation_version,label:local.label,reason:local.reason};}
   else{if(!current.can_edit||local.target?.kind!=='save'||local.target.id!==current.id||local.target.version!==current.version)throw Error('服务器草稿版本已变化，请先对照已有判断，本机稿仍保留。');method='PUT';path+='/'+current.id;body={request_id,expected_version:local.target.version,label:local.label,reason:local.reason};}
  }else if(action==='revisions'){
   if(!current?.can_revise)throw Error('当前还不能创建新的判断修订。');ctx.journal.archive();if(!setLocal(ctx,emptyThresholdFormV147()))throw Error('新修订准备稿尚未保存，未发送。');path+='/'+current.root_id+'/revisions';body={request_id,expected_head_revision:current.history.at(-1).revision,label:null,reason:''};
  }else{
   if(!current||local.target?.id!==current.id||local.target.version!==current.version)throw Error('请先保存并核对当前版本，原稿保留。');
   if(action==='submit'){if(!current.can_submit||local.label!==current.label||local.reason!==current.reason)throw Error('请先保存当前判断和理由，再明确提交。');body={request_id,expected_version:current.version,confirm:true};}
   else{if(!current.can_cancel||!local.cancel_reason.trim())throw Error('请填写取消此草稿的理由。');body={request_id,expected_version:current.version,reason:local.cancel_reason};}path+='/'+current.id+'/'+action;
  }
  const sent=await ctx.journal.install(method,path,body,()=>ctx.identity(ticket));if(!ctx.current(ticket))return;setPending(sent);setProbeHash(null);setRequestState(null);await original(ctx,ticket,sent,true);
 });}
 async function loadExisting(){await operate(async(ctx,ticket)=>{
  if(ctx.journal.isUnsaved())throw Error('请先保存本机稿，再读取替换的服务器草稿。');await loadContext(ctx,ticket);if(!ctx.view)return;
  const value=checkedThresholdViewV147(await ctx.read(THRESHOLD_ROOT_V147+'/feedback/'+ctx.view.id,ticket),ctx.scope);if(!ctx.current(ticket))return;ctx.view=value;setView(value);
  const active=await ctx.journal.pending();if(!ctx.current(ticket))return;if(active)throw Error('原请求尚待核对，已显示服务器历史，但没有替换本机稿。');ctx.journal.archive();setLocal(ctx,{label:value.label,reason:value.reason,cancel_reason:'',target:value.can_edit?{kind:'save',id:value.id,version:value.version}:null});refreshDrafts(ctx);
 });}
 function close(){const ctx=ctxRef.current;if(ctx?.journal.isUnsaved()){setError('请先重试保存本机稿，再收起判断；当前人工说明仍保留。');return;}if(busy)return;epoch.current++;setVerified(null);setOpened(false);}
 const basis=visible?context?.basis:null,current=visible?view:null,historical=visible?history:null,protectedDraft=!!ctxRef.current?.journal.recoveryReady,canEdit=visible&&protectedDraft&&!pending&&!retirement&&!busy&&writable&&context?.supported&&(current?current.can_edit:true),matches=current&&form.target?.id===current.id&&form.target.version===current.version&&form.label===current.label&&form.reason===current.reason;
 const canStop=!!pending&&!retirement&&requestState?.outcome==='unknown'&&requestState.original_request_id===pending.request_id&&requestState.original_sha256===pending.wire_sha256;
 const inputTime=record?.review_basis?.observed_at;
 return <section className="threshold-feedback-v147 media-form">
  {!opened?<Btn onClick={open}>纠正提示判断</Btn>:<>
   <div className="actions"><h4>纠正提示判断</h4><Btn disabled={busy} onClick={close}>收起判断</Btn></div>
   <p>这是对“本来是否应该提示”的业务判断，与上方登记一致性核查分开；不会更新现场状态、修改阈值或启用候选策略。</p>
   {!visible?<><p role="status">正在核对当前登录与原登记范围。原人稿、原请求保留。</p><Btn disabled={busy} onClick={open}>重新核对当前登录</Btn></>:<>
    {context&&!context.supported&&<p role="alert">{context.issue}</p>}
    {basis&&<article><p>{basis.field_label}{basis.unit?'（'+basis.unit+'）':''}：{numeric(basis.actual_value)}；原规则：{basis.operator==='gt'?'大于':'小于'} {numeric(basis.threshold)}{basis.unit?' '+basis.unit:''}。</p><p>原规则实际{basis.actual_hit?'已提示':'未提示'}。{typeof inputTime==='string'&&Number.isFinite(Date.parse(inputTime))?'原数据登记时间：'+new Date(inputTime).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})+'（北京时间，来源时间未物理核实）。':'原数据时间待核对。'}</p></article>}
    {basis&&<><label>提示应否发生<select aria-label="提示应否发生" value={form.label||''} disabled={!canEdit} onChange={event=>edit({label:event.target.value||null})}><option value="">请明确选择</option>{Object.entries(thresholdLabelsV147).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
     <label>判断理由<textarea aria-label="判断理由" value={form.reason} maxLength={2000} disabled={!canEdit} onInput={event=>edit({reason:event.currentTarget.value})}/></label>
     <div className="actions"><Btn primary disabled={!canEdit||!!cacheError||!ctxRef.current?.journal.canSend()} onClick={()=>mutate('save')}>保存判断草稿</Btn><Btn disabled={busy||!current?.can_submit||!matches||!!pending||!!cacheError||!writable} onClick={()=>mutate('submit')}>提交判断</Btn>{current&&<Btn disabled={busy} onClick={loadExisting}>打开已有判断</Btn>}{current?.can_revise&&<Btn disabled={busy||!!pending||!!cacheError||!writable} onClick={()=>mutate('revisions')}>新建判断修订</Btn>}</div>
     {current?.can_cancel&&<><label>取消草稿理由<textarea aria-label="取消草稿理由" value={form.cancel_reason} maxLength={2000} disabled={!canEdit} onInput={event=>edit({cancel_reason:event.currentTarget.value})}/></label><Btn disabled={!canEdit||!form.cancel_reason.trim()||!!cacheError||!matches} onClick={()=>mutate('cancel')}>取消判断草稿</Btn></>}
    </>}
    {!protectedDraft&&<p role="alert">本机稿初始化未完成，原保存字节保留；暂不能编辑或发送新的判断。</p>}
    {cacheError&&<p role="alert">{cacheError}</p>}{cacheError&&<Btn disabled={busy||!ctxRef.current?.journal.owner} onClick={()=>{const ctx=ctxRef.current;if(!ctx?.current(epoch.current))return;try{const saved=ctx.journal.retryPersistence();setForm(structuredClone(saved));setRetireReason(ctx.journal.retirementReason);setCacheError('');refreshDrafts(ctx);}catch(cause){setCacheError(cause.message+' 原稿保留，未发送。');}}}>重试保存本机稿</Btn>}
    {!ctxRef.current?.journal.canSend()&&<p>尚不具备完整保存或多窗口协调条件；可读回原判断，暂不发送新操作。</p>}
    {pending&&<aside><p role="status">原请求待核对。原编号、方法、正文及摘要已保留；刷新或收起不会自动重发。</p><Btn disabled={busy||!!retirement} onClick={()=>operate((ctx,ticket)=>original(ctx,ticket,pending,false))}>核对原判断请求</Btn><Btn disabled title="原判断不直接重发；可先核对，再明确停止接收并修改。">按原编号重试判断</Btn>
     {canStop&&<Btn disabled={busy||!writable||!!cacheError||!ctxRef.current?.journal.canSend()} onClick={()=>setRetireOpen(true)}>停止接收这个未保存请求</Btn>}
     {canStop&&retireOpen&&<div><p>只有服务器确认停止接收后，才可以修改后另存。原请求与本窗口人稿都会保留；已保存判断不会被撤销。</p><label>停止接收理由<textarea aria-label="停止接收理由" value={retireReason} maxLength={2000} disabled={busy||!writable||!protectedDraft} onInput={event=>editRetireReason(event.currentTarget.value)}/></label><Btn disabled={busy||!writable||!retireReason.trim()||!!cacheError||!ctxRef.current?.journal.canSend()} onClick={confirmRetirement}>确认停止接收原请求</Btn><Btn disabled={busy} onClick={()=>setRetireOpen(false)}>暂不停止接收</Btn></div>}
    </aside>}
    {retirement&&<aside><p role="status">停止接收结果尚待核对。原停止接收请求与人稿已保留；只读核对不会重新发送原判断。</p><Btn disabled={busy} onClick={()=>operate((ctx,ticket)=>retirementAction(ctx,ticket,retirement,false))}>核对停止接收结果</Btn><Btn disabled={busy||!writable||requestState?.outcome!=='unknown'||requestState.original_request_id!==retirement.original.request_id||!!cacheError||!ctxRef.current?.journal.canSend()} onClick={()=>operate((ctx,ticket)=>retirementAction(ctx,ticket,retirement,true))}>按原编号重试停止接收</Btn></aside>}
    {busy&&<p role="status">正在核对或保存当前操作；原请求与人稿保留。</p>}
    {current&&<><article><h5>当前已提交判断</h5>{current.latest_submitted?<p>{thresholdLabelsV147[current.latest_submitted.label]} · {current.latest_submitted.reason}（修订 {current.latest_submitted.revision}）。未提交的新稿不会推翻此判断。</p>:<p>尚无已提交判断；保存草稿不等于提交。</p>}</article><details><summary>判断修订历史（{current.history.length}）</summary>{current.history.map(item=><p key={item.id}>修订 {item.revision} · {statusText[item.status]} · {item.actor_id===account?.user_id?(account?.username||'本账号'):'另一位操作者'} · {thresholdLabelsV147[item.label]||'尚未选择'}：{item.reason||'理由待填写'}{item.cancel_reason?'；取消说明：'+item.cancel_reason:''}</p>)}</details></>}
    {historical&&<article><h5>历史已提交判断（{historical.basis_available?'只读':'依据暂不可用'}）</h5><p>{historical.basis_available?'这些理由已经保存；当前仅核对历史，尚未读回可操作依据，不可用于策略、阈值评估或审批。':'这些理由已经保存；原处理依据目前无法完整核对，不可用于策略、阈值评估或审批。'}</p>{historical.latest_submitted?<p>{thresholdLabelsV147[historical.latest_submitted.label]} · {historical.latest_submitted.reason}（修订 {historical.latest_submitted.revision}）。</p>:<p>尚无已提交判断；历史草稿不等于提交。</p>}<details><summary>只读判断历史（{historical.history.length}）</summary>{historical.history.map(item=><p key={item.id}>修订 {item.revision} · {statusText[item.status]} · {item.actor_id===account?.user_id?(account?.username||'本账号'):'另一位操作者'} · {thresholdLabelsV147[item.label]||'尚未选择'}：{item.reason||'理由待填写'}{item.cancel_reason?'；取消说明：'+item.cancel_reason:''}</p>)}</details></article>}
    <details><summary>本机保留稿（{drafts.length}）</summary>{drafts.map(item=><p key={item.owner}>{thresholdLabelsV147[item.form.label]||'尚未选择'} · {item.form.reason||'空稿'} <Btn disabled={busy||!!pending} onClick={()=>{const ctx=ctxRef.current;try{const restored=ctx.journal.restore(item.owner);setForm(restored);setCacheError('');refreshDrafts(ctx);}catch(cause){setError(cause.message);}}}>恢复这份本机稿</Btn></p>)}</details>
   </>}
   {error&&<p role="alert" className="error">{error}</p>}{notice&&visible&&<p role="status">{notice}</p>}
  </>}
  {!opened&&error&&<p role="alert">{error}</p>}
  <ThresholdStudyV149 record={record} instanceId={instanceId} account={account} Btn={Btn}
   blockedReason={historical&&!historical.basis_available?'原处理依据目前不可用于阈值比较，人工说明和比较草稿保留。':''}/>
 </section>;
}
