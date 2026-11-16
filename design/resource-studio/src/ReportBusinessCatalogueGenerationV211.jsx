import React,{useEffect,useRef,useState} from 'react';
import {json} from './runtime-api';
import {CatalogueGenerationClientV211,catalogueGenerationModelsV211,catalogueGenerationStatusV211,catalogueSourceAvailable} from './report-business-catalogue-generation-v211.mjs';
import './report-business-catalogue-generation-v211.css';

const Button=({children,primary,...props})=><button type="button" className={primary?'primary':undefined} {...props}>{children}</button>;
const stages=['queued','running','response_unknown'];
function ContextValue({value}){
 if(value===null)return <span>明确为空</span>;
 if(Array.isArray(value))return <ol>{value.map((item,i)=><li key={i}><ContextValue value={item}/></li>)}</ol>;
 if(typeof value==='object')return <dl>{Object.entries(value).map(([key,item])=><React.Fragment key={key}><dt>{key}</dt><dd><ContextValue value={item}/></dd></React.Fragment>)}</dl>;
 return <span>{typeof value==='boolean'?(value?'是':'否'):String(value)}</span>;
}
function SendingScope({preview}){
 const [claimsShown,setClaimsShown]=useState(30),[spansShown,setSpansShown]=useState(20);
 const payload=preview.sent_payload,claims=payload.indexed_inputs.input_claims,spans=payload.spans;
 return <details className="catalogue-generation-scope"><summary>核对完整发送范围与原文</summary>
  <p>本次发送全部 {claims.length} 条已提取要求、全部 {spans.length} 处对应原文，以及下方背景与要求。完整资料目录保留在服务器，不发送给公有云。</p>
  <ul>{preview.materials.map((material,i)=><li key={material.id||i}>{material.name||material.title||'原报告资料'}{material.version?' · 第 '+material.version+' 版':''}</li>)}</ul>
  <details><summary>全部已提取要求 · {claims.length} 条</summary>{claims.slice(0,claimsShown).map(entry=><article key={entry.input_position}><h5>要求 {entry.input_position+1}</h5><p>{entry.claim.statement}</p><small>{{explicit:'模型标为原文明示',inferred:'模型推断，仍需核对',unknown:'资料尚不明确'}[entry.claim.status]}</small>{entry.claim.explanation&&<p>{entry.claim.explanation}</p>}<small>原文位置：{entry.claim.span_refs.join('、')||'没有可用原文位置'}</small></article>)}{claimsShown<claims.length&&<button type="button" onClick={()=>setClaimsShown(n=>n+40)}>继续显示原要求（已显示 {Math.min(claimsShown,claims.length)}/{claims.length}）</button>}</details>
  <details><summary>全部引用原文 · {spans.length} 处</summary>{spans.slice(0,spansShown).map(([ref,text])=><article key={ref}><small>{ref}</small><p>{text}</p></article>)}{spansShown<spans.length&&<button type="button" onClick={()=>setSpansShown(n=>n+30)}>继续显示原文（已显示 {Math.min(spansShown,spans.length)}/{spans.length}）</button>}</details>
  <details><summary>本次背景与原始要求</summary><p>{payload.user_instruction||'未提供额外原始要求'}</p><ContextValue value={payload.context}/></details>
  <p>本次目录要求：{payload.catalogue_note||'整理候选业务、共同要求和需要确认的问题。'}</p>
 </details>;
}
export default function ReportBusinessCatalogueGenerationV211({account,producer,models=[],request=json,Btn=Button,storage,locks,prepareDraft,onRegisterGuard,onRegisterRefresh,onConsumer,currentAvailable=true}){
 // prepareDraft may legitimately save a newer configuration revision mid-preview.
 // Scope changes dispose the client; that same report's version advance must not.
 const key=[account?.user_id,account?.workspace_id,account?.role,producer?.id,Number.isSafeInteger(producer?.revision)&&producer.revision>0?'ready':'unready'].join(':'),latest=useRef();
 latest.current={key,account,producer,models,request,prepareDraft,onConsumer,currentAvailable};
 const holder=useRef(null),[snapshot,setSnapshot]=useState(null),[bootError,setBootError]=useState(''),[expanded,setExpanded]=useState(false),formRoot=useRef(null);
 const visible=snapshot?.key===key,v=visible?snapshot.value:null,c=holder.current;
 const choices=catalogueGenerationModelsV211(models),choice=choices.find(row=>row.id===v?.form.model_id);
 const available=()=>typeof latest.current.currentAvailable==='function'?latest.current.currentAvailable():latest.current.currentAvailable!==false;
 useEffect(()=>{
  let active=true,client;
  setSnapshot(null);setBootError('');
  try{
   client=new CatalogueGenerationClientV211({account,producer,storage,locks,models:()=>latest.current.models,
    request:(path,options)=>{if(latest.current.key!==key)throw Error('账号或报告已经变化，原请求保留。');return latest.current.request(path,options);},
    prepareDraft:async()=>{if(typeof latest.current.prepareDraft!=='function')throw Error('配置保存入口尚未接通，未准备新调用。');return latest.current.prepareDraft();},currentAvailable:available,
    onChange:value=>{if(active&&holder.current===client&&latest.current.key===key)setSnapshot({key,value});}});
   holder.current=client;const guard=options=>latest.current.key===key&&client.guard(options);onRegisterGuard?.(guard);
   const refresh=async()=>{if(!active||latest.current.key!==key||holder.current!==client)return false;const ok=await client.act(()=>client.refresh());return ok!==false&&active&&latest.current.key===key&&client.inventoryVerified;};
   onRegisterRefresh?.(refresh);
   const unload=event=>{if(!guard()){event.preventDefault();event.returnValue='';}};
   const changed=event=>{if(event.key?.startsWith(client.key)&&!client.busy)client.pending().then(()=>client.emit()).catch(error=>{client.localSaved=false;client.error=error.message;client.emit();});};
   window.addEventListener('beforeunload',unload);window.addEventListener('storage',changed);
   client.open();
   const timer=setInterval(()=>{if(!client.closed&&!client.busy&&!client.loading&&!client.pendingRecord&&!client.preview&&client.items.some(row=>stages.includes(row.status)))client.act(()=>client.refresh());},5000);
   return()=>{active=false;clearInterval(timer);window.removeEventListener('beforeunload',unload);window.removeEventListener('storage',changed);client.dispose();onRegisterGuard?.(null);onRegisterRefresh?.(null,refresh);if(holder.current===client)holder.current=null;};
  }catch(error){if(active){setBootError(error.message);onRegisterGuard?.(()=>false);}return()=>{active=false;client?.dispose();onRegisterGuard?.(null);};}
 },[key]);
 const operate=fn=>{const client=holder.current;if(client&&!client.closed)return fn(client);};
 const open=()=>{setExpanded(true);formRoot.current?.scrollIntoView?.({block:'nearest'});};
 const writable=['admin','operator'].includes(account?.role)&&available();
 const sourceAvailable=catalogueSourceAvailable(producer);
 const blocked=!v||v.loading||v.busy||v.denied,hasActive=v?.items.some(row=>['queued','running','response_unknown'].includes(row.status)),canGenerate=writable&&!blocked&&!v?.pending&&v?.localSaved&&!hasActive&&!!choice&&sourceAvailable;
 const ready=v?.selected&&['review','needs_clarification'].includes(v.selected.status)&&!!v.selected.catalogue;
 return <section className="catalogue-generation-v211" aria-label="自动整理业务清单">
  <header><div><h3>从已读取的报告要求整理业务</h3><p>把报告要求整理为业务清单、共同要求和待核问题，再由你修改确认。</p></div><Btn primary disabled={!!bootError||v?.denied} onClick={open}>自动整理业务清单</Btn></header>
  {bootError&&<p role="alert">{bootError}</p>}
  {!bootError&&!visible&&<p role="status">正在核对当前账号、原请求和服务器活动…</p>}
  {v?.denied?<p role="alert">账号或工作区身份已变化，原内容已隐藏；本机要求和原请求保留在原账号范围。</p>:v&&<>
   {v.error&&<p className="catalogue-generation-error" role="alert">{v.error}</p>}{v.notice&&<p role="status">{v.notice}</p>}{v.busy&&<p role="status">正在核对原记录，请稍候…</p>}
   {!v.localSaved&&<aside role="alert"><p>本机要求或原请求尚未可靠保护，请保留此页，不要重新建立调用。</p><Btn disabled={v.busy} onClick={()=>operate(client=>client.act(async()=>{client.persistForm();}))}>重试保存本机要求</Btn>{v.pending&&!v.pendingDurable&&<Btn disabled={v.busy} onClick={()=>operate(client=>client.protectOriginal())}>重试保护未发送的原请求</Btn>}</aside>}
   {v.pending&&<aside className="catalogue-generation-pending"><h4>原{v.pending.operation==='start'?'启动':'回答恢复'}申请尚待核对</h4><p>原编号和完整正文保留。未知结果不能换号再次调用。</p><strong>这次申请中保存的整理要求</strong><p>{v.pending.binding.note||'未填写额外整理要求。'}</p><p>继续办理会使用这里已保存的要求和第{v.pending.binding.revision}版配置；请先核对后再继续。</p><div className="actions"><Btn disabled={v.busy} onClick={()=>operate(client=>client.recoverOriginal())}>只核对原请求</Btn><Btn disabled={v.busy||!writable||!v.pendingDurable} onClick={()=>operate(client=>client.retryOriginal())}>明确继续同一原请求</Btn><Btn disabled={v.busy||!writable} onClick={()=>operate(client=>client.retireOriginal())}>保留并归档未发送或明确拒绝的原申请</Btn></div><details><summary>查看原申请标识</summary><p>编号：{v.pending.request_id}</p><p>摘要：{v.pending.sha256}</p><p>原模型登记：{v.pending.binding.model_id}</p></details></aside>}
   {expanded&&<div ref={formRoot}>
    <p>原报告的失败状态、回答与费用记录保留。后台会先核对完整已保存内容，来源不合格时拒绝准备，不会重新提取或调用模型。</p>
    {!sourceAvailable&&<p role="status">新目录需要可核对的报告来源。已有目录仍可查看。</p>}
    <div className="catalogue-generation-form"><label>本次目录模型<select aria-label="业务目录模型" value={choice?v.form.model_id:''} disabled={blocked||!writable||!!v.pending} onChange={event=>operate(client=>client.saveForm({...client.form,model_id:event.target.value}))}><option value="">请选择已登记模型</option>{choices.map(row=><option key={row.id} value={row.id}>{row.name||'已登记的 Qwen 模型'} · {row.model||row.execution?.model}</option>)}</select></label>
     {!choices.length&&<p>当前没有已登记的目录专用模型，请管理员核对登记。原活动仍可读取，没有使用其他模型代替。</p>}
     <label>希望怎样整理业务清单<textarea aria-label="业务目录整理要求" maxLength={2000} value={v.form.note} readOnly={blocked||!writable||!!v.pending} onChange={event=>operate(client=>client.saveForm({...client.form,note:event.target.value}))} placeholder="例如：按实际办理的业务分类，共同要求单列，责任不明确的保留为问题。"/></label>
     <small>{v.localSaved?'本机要求已保存，刷新可恢复；尚未发送给云模型。':'当前输入尚未可靠保存，请先重试保护。'}</small>
     <Btn primary disabled={!canGenerate} onClick={()=>operate(client=>client.prepare())}>核对完整资料与单次报价</Btn>
    </div>
    {v.retainedForms.length>0&&<details><summary>本机保留的整理要求</summary>{v.retainedForms.map(row=><article key={row.key}><p>{row.value.note||'未填写额外要求'}</p><small>{new Date(row.at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · {row.persisted?'已写入本机':'仍在本页保留，恢复时需重新保护'}</small><Btn disabled={blocked||!!v.pending||!writable} onClick={()=>operate(client=>client.restoreForm(row))}>恢复这份本机要求</Btn></article>)}</details>}
    {v.preview&&<section className="catalogue-generation-quote"><h4>确认这一次目录整理</h4><p>全部 {v.preview.view.map_count} 组已存提取结果、{v.preview.view.claim_count} 条要求、{v.preview.view.sent_payload.spans.length} 处原文；最多 1 次新的公有云调用。</p><p>模型：{v.preview.view.model}。本次费用上限 US${v.preview.view.budget.maximum_cost_usd.toLocaleString('en-US',{maximumFractionDigits:6})}，并受服务器预算限制。</p><p>完整请求 {v.preview.view.input_summary.prepared_request_bytes.toLocaleString()} 字节；保守预留 {v.preview.view.input_summary.token_bound.toLocaleString()} token。</p>
     <SendingScope key={v.preview.view.preview_hash} preview={v.preview.view}/><label className="catalogue-generation-consent"><input type="checkbox" checked={v.consent} disabled={blocked||!!v.pending||!writable} onChange={event=>operate(client=>client.acknowledge(event.target.checked))}/>我已核对本次完整范围与费用，同意仅这一次目录整理。</label>
     <div className="actions"><Btn primary disabled={!canGenerate||!v.consent} onClick={()=>operate(client=>client.start())}>同意并开始整理业务清单</Btn><Btn disabled={v.busy} onClick={()=>operate(client=>client.cancelPreview())}>返回修改，不启动</Btn></div>
     <small>覆盖全部已存声明不能证明原报告无遗漏；目录仍需人工核对，不授予配置采用、设备操作或额外模型调用权限。</small>
    </section>}
   </div>}
   <section className="catalogue-generation-activities"><header><h4>服务器保存的目录活动</h4><Btn disabled={blocked} onClick={()=>operate(client=>client.act(()=>client.refresh()))}>刷新活动与原回答</Btn></header>
    {!v.loading&&!v.activitiesLoaded&&<p>目录活动尚未读取成功，请重试读取；目前不能判断是否已有活动。</p>}
    {v.activitiesLoaded&&v.error&&<p>本次操作未完成，已成功读取的活动暂时保留；请重试核对最新状态。</p>}
    {v.activitiesLoaded&&!v.error&&!v.loading&&!v.busy&&!v.items.length&&<p>本人在这份报告下还没有目录活动。未列出旧方案作为目录结果。</p>}
    {v.items.map((row,i)=><article key={row.id}><div><strong>目录活动 {i+1} · {catalogueGenerationStatusV211(row.status)}</strong><p>{row.note||'整理候选业务、共同要求和待核问题。'}</p></div><Btn disabled={blocked||!!v.pending} onClick={()=>operate(client=>client.select(row.id))}>查看这次活动</Btn></article>)}
   </section>
   {v.selected&&<section className="catalogue-generation-result"><h4>{catalogueGenerationStatusV211(v.selected.status)}</h4>{v.selected.error&&<p role="alert">{v.selected.error}</p>}{stages.includes(v.selected.status)&&<p role="status">本页只读取原后台活动的进度，不自动重发或另建调用。</p>}
    {v.capture&&<details open={v.selected.status==='failed'}><summary>原回答与保存情况</summary>{v.capture.available?<><p>HTTP {v.capture.http_status??'待核对'} · 结束原因 {v.capture.finish_reason||'尚未取得'} · {v.capture.response_complete?'原回答已完整保存':'原回答完整性仍待核对'}</p><p>原回应 {v.capture.response_bytes??'待核对'} 字节，正文 {v.capture.arguments_bytes??'待核对'} 字节。</p>{v.capture.problem&&<p role="alert">{v.capture.problem}</p>}</>:<p>尚无可显示的完整原回答；不能据此推断未发送，也不能自动换号调用。</p>}</details>}
    {['failed','response_unknown'].includes(v.selected.status)&&<><p>核对已存回答只检查同一次原捕获，不修写回答，不重新调用模型。</p><Btn disabled={blocked||!writable||!!v.pending} onClick={()=>operate(client=>client.recoverSaved())}>核对这次已存回答</Btn></>}
    {v.selected.status==='failed'&&<aside className="catalogue-generation-partial" aria-label="核对失败回答中的部分建议"><h4>这次回答尚未通过完整核对</h4><p>可以单独查看其中可安全读取的业务建议。尚待归类、重复归类或名称依据不足的要求会明确保留待核对状态，原失败与费用记录继续保留。</p><Btn disabled={blocked||!!v.pending||typeof onConsumer!=='function'||typeof c?.partialForEditing!=='function'} onClick={()=>operate(client=>client.act(async()=>{const row=await client.partialForEditing(),accepted=await latest.current.onConsumer?.(row);client.fence();if(accepted===false)throw Error('当前人工稿尚未可靠保存，请先保护原稿再切换。');client.notice='已打开同一次失败回答的部分建议，仍需逐项处理问题并另保存人工稿；没有重新调用模型。';}))}>核对部分建议</Btn><small>只读取同一次已保存回答。无法安全展开时会说明原因，不会将原调用改为成功。</small></aside>}
    {ready&&<><p>已保存 {v.selected.catalogue.wire.businesses.length} 项候选业务、{v.selected.catalogue.wire.questions.length} 项待核问题，完整对应 {v.selected.catalogue.input_count} 条原要求。</p><p>下一步可修改业务目的、边界、共同要求和归属。这里尚未确认业务范围，也未创建运行任务。</p><Btn primary disabled={blocked||!!v.pending||typeof onConsumer!=='function'} onClick={()=>operate(client=>client.act(async()=>{const row=await client.consumerForEditing(),accepted=await latest.current.onConsumer?.(row);client.fence();if(accepted===false)throw Error('当前人工稿尚未允许切换，请保留原目录继续核对。');client.notice='已打开这份真实目录建议，人工草稿仍需单独保存与确认。';}))}>核对并编辑这份业务目录</Btn>{typeof onConsumer!=='function'&&<p role="alert">目录编辑入口尚未接通，已保存结果仍可读取。</p>}</>}
   </section>}
  </>}
 </section>;
}
