import React,{useEffect,useId,useRef,useState} from 'react';
import {json} from './runtime-api';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {NativeGraphSourcesControllerV136} from './native-graph-sources-controller-v136.mjs';
import GraphSourceDeliveryPanelV124 from './GraphSourceDeliveryPanelV124';
import './graph-sources-v122.css';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const button=({primary,children,...props})=><button type="button" {...props}>{children}</button>;
const modelName=row=>row?.name||'原登记的本地语音模型';
function ScopeExplanation(){return <><p>只供应本实例实际关联到的本地整片语音转写候选。原音频、原任务及人工核查仍在业务工作台保存，不在此页展示转写正文。</p><p>登记不启动转写，也不自动采用等待规则。采集时刻、对象覆盖与现场状态尚未核实，结果不会自动更新现场状态。</p><ul><li>已执行整片转写：是或否。</li><li>机器转写文字：本地模型已保存的文字候选，需人工核对。</li></ul></>;}
export default function NativeGraphSourcesPanelV136({account,instanceId,instanceName,Btn=button,onBusy,onReturnToFlow}){
 const heading=useId(),controller=useRef(null),alive=useRef(false),callbacks=useRef({onBusy,onReturnToFlow});callbacks.current={onBusy,onReturnToFlow};
 const activity=useRef({source:false,delivery:false});
 const [,redraw]=useState(0),[fatal,setFatal]=useState(''),[stopConfirmation,setStopConfirmation]=useState(null),[retireConfirmation,setRetireConfirmation]=useState(null);
 const scopeKey=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,instanceId]);
 useEffect(()=>{
  alive.current=true;let current;
  const change=()=>{if(alive.current&&controller.current===current){activity.current.source=current.protectedWork;callbacks.current.onBusy?.(activity.current.source||activity.current.delivery);redraw(value=>value+1);}};
  try{current=new NativeGraphSourcesControllerV136({api:json,account,scope:{actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:instanceId},onChange:change});controller.current=current;setFatal('');current.open().catch(()=>{});}
  catch(error){controller.current=null;setFatal(error.message||'请重新核对登录与实例范围，尚未发送。');}
  const wake=()=>{current?.conceal();if(document.visibilityState!=='hidden')current?.recheckIdentity().catch(()=>{});};
  const leave=event=>{if(current?.protectedWork||activity.current.delivery){event.preventDefault();event.returnValue='';}};
  window.addEventListener('focus',wake);document.addEventListener('visibilitychange',wake);window.addEventListener('beforeunload',leave);
  return()=>{alive.current=false;current?.dispose();if(controller.current===current)controller.current=null;callbacks.current.onBusy?.(false);window.removeEventListener('focus',wake);document.removeEventListener('visibilitychange',wake);window.removeEventListener('beforeunload',leave);};
 },[scopeKey]);
 const candidate=controller.current,c=candidate?.scope.actor_id===account?.user_id&&candidate.scope.workspace_id===account?.workspace_id&&candidate.role===account?.role&&candidate.scope.instance_id===instanceId?candidate:null;
 async function perform(callback){try{await callback();}catch{}if(alive.current)redraw(value=>value+1);}
 const locked=!c||c.busy||c.corrupt||activity.current.delivery,selected=c?.detail,pending=c?.pending,retirement=c?.retirement;
 const delivery=<GraphSourceDeliveryPanelV124 key={account?.workspace_id+':'+account?.user_id} actorID={account?.user_id} workspaceID={account?.workspace_id} producer={selected?{id:selected.id,label:selected.label}:null} Btn={Btn} concealed={!c||c.identityStatus!=='verified'} blocked={!c||c.busy} onBusy={value=>{const changed=activity.current.delivery!==value;activity.current.delivery=value;callbacks.current.onBusy?.(activity.current.source||value);if(changed&&alive.current)redraw(number=>number+1);}}/>;
 const reason=selected?c.form.reasons[selected.id]||'':'',retireReason=pending?c.form.reasons[pending.body.request_id]||'':'';
 const stopKey=selected?selected.id+':'+selected.version+':'+reason:null,retireKey=pending?pending.command_hash+':'+retireReason:null;
 if(!c||c.identityStatus!=='verified'||c.corrupt)return <><section className="graph-sources-v122" aria-labelledby={heading}><h2 id={heading}>本地语音已保存结果来源</h2><p role="status">{fatal||c?.error||'正在核对当前账号、工作区和实例，本机原稿暂不展示。'}</p><p>原记录保留，身份核对通过前不会读取或提交私有登记稿。</p><Btn disabled={!c||c.busy} onClick={()=>perform(()=>c.recheckIdentity())}>重新核对登录</Btn></section>{delivery}</>;
 return <><section className="graph-sources-v122 native-graph-sources-v136" aria-labelledby={heading}>
  <header className="graph-sources-heading"><div><h2 id={heading}>本地语音已保存结果来源</h2><p>{instanceName||'当前业务实例'} · 选择真实已保存处理的来源和本地语音模型。</p></div>{onReturnToFlow&&<Btn disabled={locked||!c.localSaved} onClick={onReturnToFlow}>返回流程确认等待来源</Btn>}</header>
  <ScopeExplanation/>
  {c.error&&<p role="alert" className="graph-sources-problem">{c.error}</p>}{c.notice&&<p role="status">{c.notice}</p>}
  {!c.journal.canSend()&&<p role="alert">本窗口的安全存储或多窗口协调不可用。原记录可查，暂不能发送、重发或结清操作。</p>}
  {!c.localSaved&&<aside role="alert"><p>本页新输入尚未完整保存，请保留页面。恢复浏览器存储后再重试。</p><Btn disabled={locked} onClick={()=>c.saveLocal()}>重试保存本页输入</Btn></aside>}
  <section aria-label="找回本地语音来源原稿"><h3>找回本机原稿和原操作</h3><p>重新进入或换窗口后，可接回原登记稿或核对原请求。不会自动发送。</p><Btn disabled={locked} onClick={()=>perform(()=>c.discoverRecovery())}>读取本机保留记录</Btn>
   {c.recovery.problem_count>0&&<p role="alert">部分记录无法核对，原记录保留，未用空白稿覆盖。</p>}
   {c.recovery.loaded&&!c.recovery.items.length&&<p>{c.recovery.next_offset!==null?'本段没有可恢复项，请继续读取后段。':'当前账号和实例没有读取到可恢复项。'}</p>}
   <ul className="graph-sources-recovery-list">{c.recovery.items.map(row=><li key={row.key}><div><strong>{row.label}</strong><p>{row.summary}</p></div><Btn disabled={locked||row.kind==='draft'&&!c.localSaved} onClick={()=>perform(()=>c.restoreRecovery(row))}>{row.kind==='draft'?'保留当前稿并恢复此稿':'接回并只查原结果'}</Btn></li>)}</ul>
   {c.recovery.next_offset!==null&&<Btn disabled={locked} onClick={()=>perform(()=>c.discoverRecovery(true))}>继续读取本机记录</Btn>}
  </section>
  {pending&&<aside className="graph-sources-recovery" aria-label="本地语音原请求核对"><h3>{pending.kind==='register'?'原登记结果尚待核对':'原停止结果尚待核对'}</h3><p>{pending.kind==='register'?pending.body.label:pending.body.reason}</p><p>完整原请求已保存；刷新和重新进入后先只查原结果，不会另造编号或自动提交。</p><Btn disabled={locked} onClick={()=>perform(()=>c.recover())}>只查原请求结果</Btn>
   {c.pendingNotFound&&!retirement&&<><Btn disabled={locked||!c.localSaved||!c.journal.canSend()} onClick={()=>perform(()=>c.resend())}>按原内容重试</Btn><label>结束这次未确认操作的原因<textarea maxLength={1000} disabled={locked} value={retireReason} onChange={event=>{setRetireConfirmation(null);c.setReason(pending.body.request_id,event.target.value);}}/></label><p>尚未找到不等于从未送达。明确结束只封住这一原请求，不是停止已有来源，也不删除原稿。</p>
    {retireConfirmation!==retireKey?<Btn disabled={locked||!retireReason.trim()||!c.localSaved||!c.journal.canSend()} onClick={()=>setRetireConfirmation(retireKey)}>结束这次未确认操作</Btn>:<div role="group" aria-label="确认结束原操作"><p>确认保留原稿并结束这一次未确认操作？</p><Btn disabled={locked||!c.localSaved} onClick={()=>{setRetireConfirmation(null);perform(()=>c.retireOriginal());}}>确认结束这次操作</Btn><Btn disabled={locked} onClick={()=>setRetireConfirmation(null)}>继续保留原请求</Btn></div>}
   </>}
  </aside>}
  {retirement&&<aside className="graph-sources-recovery" aria-label="结束操作原结果核对"><h3>结束操作的结果仍待核对</h3><p>{retirement.body.reason}</p><p>原登记或停止操作不会再被本页重发；这次结束请求也完整保留。</p><Btn disabled={locked} onClick={()=>perform(()=>c.recoverRetirement())}>只查结束操作结果</Btn>{c.retirementNotFound&&<Btn disabled={locked||!c.localSaved||!c.journal.canSend()} onClick={()=>perform(()=>c.resendRetirement())}>按原内容重试结束操作</Btn>}</aside>}
  <div className="graph-sources-layout"><section className="graph-sources-form" aria-label="登记本地语音结果来源"><h3>登记本地语音结果来源</h3>
   {!c.canRegister&&<p>{c.options?.reason||'只有获服务器许可的管理员可以新增或停止来源；历史和原请求仍可核对。'}</p>}
   <label>结果来源名称<input maxLength={200} disabled={locked} value={c.form.label} placeholder="便于流程负责人辨认" onChange={event=>c.edit({label:event.target.value})}/></label>
   {['source','installation'].map(kind=>{const rows=kind==='source'?c.options?.sources||[]:c.options?.installations||[],chosen=c.form[kind];return <div key={kind}><label>{kind==='source'?'已有语音结果来源':'实际本地语音模型'}<select disabled={locked} value={chosen?.id||''} onChange={event=>perform(()=>c.choose(kind,event.target.value))}><option value="" disabled>请明确选择</option>{chosen&&!rows.some(row=>row.id===chosen.id)&&<option value={chosen.id}>{chosen.name}（保留原选择，需重新核对）</option>}{rows.map(row=><option key={row.id} value={row.id}>{row.name}{kind==='source'?' · 来源版本 '+row.release_version:' · CPU本地运行 · 安装代次 '+row.trace.generation}</option>)}</select></label>{c.options&&!rows.length&&<p>{kind==='source'?(c.options.next_after?'本页暂无可选来源，可继续读取。':'尚无可用的本地语音结果来源，请先在已启用业务中完成并关联本地语音处理。'):'尚未读取到可核对的本地语音安装，请先在资源页准备本地模型。'}</p>}</div>;})}
   {c.options?.next_after&&<Btn disabled={locked} onClick={()=>perform(()=>c.more('sources'))}>读取更多语音来源</Btn>}
   <div className="graph-sources-actions"><Btn disabled={locked||!c.canRegister||!c.localSaved||!!pending||!!retirement||!c.journal.canSend()} onClick={()=>perform(()=>c.previewRegistration())}>预览并核对登记范围</Btn><Btn disabled={locked} onClick={()=>perform(()=>c.refresh())}>刷新实际来源和登记记录</Btn></div><p>{c.localSaved?'本页输入已保存在本机。':'本页输入尚未完整保存。'}</p>
   {c.preview&&<aside className="graph-sources-preview" aria-label="本地语音来源登记确认"><h4>{c.preview.body.label}</h4><p>{c.form.source?.name} · 来源版本 {c.preview.value.contract.source_release_version}</p><p>{modelName(c.form.installation)} · CPU本地运行 · 安装代次 {c.preview.value.contract.installation.generation}</p><p>{c.preview.value.meaning}</p><ScopeExplanation/><Btn primary disabled={locked||!c.canRegister||!c.localSaved||!!pending||!!retirement||!c.journal.canSend()} onClick={()=>perform(()=>c.register())}>确认登记并保存</Btn></aside>}
  </section><section className="graph-sources-records" aria-label="已登记本地语音来源"><h3>已登记的本地语音来源</h3>
   {!c.registrations.loaded?<p role="status">正在读取登记记录…</p>:!c.registrations.items.length?<p>本页尚无本地语音来源登记记录。登记列表为空不表示旧原稿或处理结果被删除。</p>:<ul className="graph-sources-record-list">{c.registrations.items.map(row=><li key={row.id}><button type="button" disabled={locked} aria-pressed={selected?.id===row.id} onClick={()=>{setStopConfirmation(null);perform(()=>c.selectRegistration(row.id));}}><strong>{row.label}</strong><span>{row.source_name||'原登记来源'} · {row.installation_name||'原本地语音模型'}</span><span>{row.status==='active'?'登记保留':'已停止供应'} · {row.current_availability.label}</span></button></li>)}</ul>}
   {c.registrations.next_after&&<Btn disabled={locked} onClick={()=>perform(()=>c.more('registrations'))}>读取更多登记记录</Btn>}
   {selected&&<article className="graph-sources-detail"><h4>{selected.label}</h4><p>{selected.source_name||'原登记来源'} · 来源版本 {selected.contract.source_release_version}</p><p>{selected.installation_name||'原本地语音模型'} · CPU本地运行 · 安装代次 {selected.contract.installation.generation}</p><p>{selected.current_availability.label}</p><ScopeExplanation/><p>登记本身没有接纳或等待匹配回执。请在业务工作台查看原语音处理的数据接收与接纳情况，再到等待任务核对是否匹配。</p>
    {selected.status==='active'&&c.canRegister&&<><label>停止此来源的原因<textarea maxLength={1000} value={reason} disabled={locked} onChange={event=>{setStopConfirmation(null);c.setReason(selected.id,event.target.value);}}/></label><p>停止会阻止此来源的新接纳和新执行，历史记录、原音频与人稿保留；不会停止物理设备。</p>{stopConfirmation!==stopKey?<Btn disabled={locked||!!pending||!!retirement||!reason.trim()||!c.localSaved||!c.journal.canSend()} onClick={()=>setStopConfirmation(stopKey)}>停止此语音结果来源</Btn>:<div role="group" aria-label="确认停止语音来源"><Btn disabled={locked||!c.localSaved} onClick={()=>{setStopConfirmation(null);perform(()=>c.revoke());}}>确认停止此来源</Btn><Btn disabled={locked} onClick={()=>setStopConfirmation(null)}>继续保留来源</Btn></div>}</>}
   </article>}
  </section></div>
 </section>{delivery}</>;
}

// Resource page selector uses actual instances. It never asks for an identifier
// and never substitutes the setup draft for an issued runtime instance.
export function NativeGraphSourceInstancesV136({account,Btn=button,onBusy,onReturnToFlow}){
 const [instances,setInstances]=useState([]),[selected,setSelected]=useState(''),[verified,setVerified]=useState(null),[error,setError]=useState(''),[protectedWork,setProtectedWork]=useState(false),[refresh,setRefresh]=useState(0);
 const key=JSON.stringify([account?.user_id,account?.workspace_id,account?.role]),epoch=useRef(0),keyRef=useRef(key);keyRef.current=key;
 useEffect(()=>{
  let live=true;const generation=++epoch.current,request=scopedPreparationRequester(account,json);
  const current=()=>live&&epoch.current===generation&&keyRef.current===key;
  async function load(){
   setVerified(null);try{const first=await request('/me');if(!current())return;if(first.user_id!==account.user_id||first.workspace_id!==account.workspace_id||first.role!==account.role)throw Error('当前登录身份已变化，原稿保留。');
    const rows=await request('/instances'),last=await request('/me');if(!current())return;
    if(last.user_id!==account.user_id||last.workspace_id!==account.workspace_id||last.role!==account.role)throw Error('读取期间登录身份已变化，原稿保留。');
    if(!Array.isArray(rows)||rows.length>100||rows.some(row=>!UUID.test(row.id)||typeof row.name!=='string'||row.active_release_id!==null&&!UUID.test(row.active_release_id)))throw Error('实际业务实例清单无法核对，未用初始化草稿替代。');
    setInstances(rows);setVerified(key);setError('');
   }catch(problem){if(current()){setVerified(null);setError(problem.message);}}
  }
  const wake=()=>{epoch.current++;setVerified(null);if(document.visibilityState!=='hidden')setRefresh(value=>value+1);};
  load();window.addEventListener('focus',wake);document.addEventListener('visibilitychange',wake);
  return()=>{live=false;epoch.current++;window.removeEventListener('focus',wake);document.removeEventListener('visibilitychange',wake);};
 },[key,refresh]);
 const visible=verified===key,row=instances.find(value=>value.id===selected);
 return <section aria-label="选择语音结果所属业务实例"><h2>本地语音结果用于流程等待</h2><p>请选择真实已发行并启用的业务实例。尚未生成或启用时，请先完成业务配置和启用；本页不创建运行任务。</p>
  {error&&<p role="alert">{error}</p>}{!visible&&<p role="status">正在核对登录与业务实例清单，本机人稿暂不展示。</p>}
  <div hidden={!visible}><label>业务实例<select value={selected} disabled={protectedWork} onChange={event=>setSelected(event.target.value)}><option value="">请选择实际业务实例</option>{instances.map(item=><option key={item.id} value={item.id}>{item.name}{item.active_release_id?'':'（尚未启用，只可核对历史）'}</option>)}</select></label>{instances.length===100&&<p>仅列出最近100个实例。更早实例请从原业务工作台进入，原记录仍保留。</p>}{visible&&!instances.length&&<p>当前尚无已发行实例，请先完成业务配置和生成。</p>}</div>
  {selected&&<div hidden={!visible}><NativeGraphSourcesPanelV136 key={key+':'+selected} account={account} instanceId={selected} instanceName={row?.name} Btn={Btn} onReturnToFlow={onReturnToFlow} onBusy={value=>{setProtectedWork(value);onBusy?.(value);}}/></div>}
 </section>;
}
