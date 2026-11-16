import React,{useEffect,useId,useRef,useState} from 'react';
import {json} from './runtime-api';
import {scopedPreparationRequester} from './understanding-preparation-contract.mjs';
import {LearnedGraphSourcesControllerV141} from './native-learned-graph-sources-controller-v141.mjs';
import GraphSourceDeliveryPanelV124 from './GraphSourceDeliveryPanelV124';
import './graph-sources-v122.css';
const UUID=/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const button=({primary,children,...props})=><button type="button" {...props}>{children}</button>;
const modelName=row=>row?.name||'原登记的变化分析能力';
const capabilityLabel=row=>row.name+' · 阈值 '+row.probability_threshold+' · '+(row.alignment==='same_grid'?'原网格一致':'B重投影到A')+' · 第'+row.model_trace.generation+'代';
function ScopeExplanation(){return <><p>只供应本实例实际关联对象的新学习型双影像候选。登记前的原处理仍可查阅，不会补发为新消息；原A/B影像、任务和人工核查仍保存在业务工作台。</p><p>登记不运行模型，也不采用等待规则。采集时刻和对象覆盖尚未核实，不自动更新对象事实。本地工程加载不代表生产使用许可或领域效果通过。</p><ul><li>差异候选像素数：本模型阈值下的候选数量，不能当作实际变化面积。</li><li>未知像素数：本次不能比较的区域，不能当作没有变化。</li></ul></>;}
function CapabilityScope({contract}){return <p>已冻结能力版本 {contract.capability_release_version} · CPU本地运行 · 安装代次 {contract.model_trace.generation} · 候选概率阈值 {contract.probability_threshold} · {contract.alignment==='same_grid'?'要求A/B原网格一致':'允许将B重投影到A'}。原模型与参数不能由后续选择替换。</p>;}
export default function LearnedGraphSourcesPanelV141({account,instanceId,instanceName,Btn=button,onBusy,onReturnToFlow}){
 const heading=useId(),controller=useRef(null),alive=useRef(false),callbacks=useRef({onBusy,onReturnToFlow});callbacks.current={onBusy,onReturnToFlow};
 const activity=useRef({source:false,delivery:false});
 const [,redraw]=useState(0),[fatal,setFatal]=useState(''),[stopConfirmation,setStopConfirmation]=useState(null),[retireConfirmation,setRetireConfirmation]=useState(null);
 const scopeKey=JSON.stringify([account?.user_id,account?.workspace_id,account?.role,instanceId]);
 useEffect(()=>{
  alive.current=true;let current;
  const change=()=>{if(alive.current&&controller.current===current){activity.current.source=current.protectedWork;callbacks.current.onBusy?.(activity.current.source||activity.current.delivery);redraw(value=>value+1);}};
  try{current=new LearnedGraphSourcesControllerV141({api:json,account,scope:{actor_id:account.user_id,workspace_id:account.workspace_id,instance_id:instanceId},onChange:change});controller.current=current;setFatal('');current.open().catch(()=>{});}
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
 if(!c||c.identityStatus!=='verified'||c.corrupt)return <><section className="graph-sources-v122" aria-labelledby={heading}><h2 id={heading}>本地变化候选已保存结果来源</h2><p role="status">{fatal||c?.error||'正在核对当前账号、工作区和实例，本机原稿暂不展示。'}</p><p>原记录保留，身份核对通过前不会读取或提交私有登记稿。</p><Btn disabled={!c||c.busy} onClick={()=>perform(()=>c.recheckIdentity())}>重新核对登录</Btn></section>{delivery}</>;
 return <><section className="graph-sources-v122 native-learned-graph-sources-v141" aria-labelledby={heading}>
  <header className="graph-sources-heading"><div><h2 id={heading}>本地变化候选已保存结果来源</h2><p>{instanceName||'当前业务实例'} · 选择本实例已有来源和实际已发布的变化分析能力。</p></div>{onReturnToFlow&&<Btn disabled={locked||!c.localSaved} onClick={onReturnToFlow}>返回流程确认等待来源</Btn>}</header>
  <ScopeExplanation/>
  {c.error&&<p role="alert" className="graph-sources-problem">{c.error}</p>}{c.notice&&<p role="status">{c.notice}</p>}
  {!c.journal.canSend()&&<p role="alert">本窗口的安全存储或多窗口协调不可用。原记录可查，暂不能发送、重发或结清操作。</p>}
  {!c.localSaved&&<aside role="alert"><p>本页新输入尚未完整保存，请保留页面。恢复浏览器存储后再重试。</p><Btn disabled={locked} onClick={()=>c.saveLocal()}>重试保存本页输入</Btn></aside>}
  <section aria-label="找回本地变化候选来源原稿"><h3>找回本机原稿和原操作</h3><p>重新进入或换窗口后，可接回原登记稿或核对原请求。不会自动发送。</p><Btn disabled={locked} onClick={()=>perform(()=>c.discoverRecovery())}>读取本机保留记录</Btn>
   {c.recovery.problem_count>0&&<p role="alert">部分记录无法核对，原记录保留，未用空白稿覆盖。</p>}
   {c.recovery.loaded&&!c.recovery.items.length&&<p>{c.recovery.next_offset!==null?'本段没有可恢复项，请继续读取后段。':'当前账号和实例没有读取到可恢复项。'}</p>}
   <ul className="graph-sources-recovery-list">{c.recovery.items.map(row=><li key={row.key}><div><strong>{row.label}</strong><p>{row.summary}</p></div><Btn disabled={locked||row.kind==='draft'&&!c.localSaved} onClick={()=>perform(()=>c.restoreRecovery(row))}>{row.kind==='draft'?'保留当前稿并恢复此稿':'接回并只查原结果'}</Btn></li>)}</ul>
   {c.recovery.next_offset!==null&&<Btn disabled={locked} onClick={()=>perform(()=>c.discoverRecovery(true))}>继续读取本机记录</Btn>}
  </section>
  {pending&&<aside className="graph-sources-recovery" aria-label="本地变化候选原请求核对"><h3>{pending.kind==='register'?'原登记结果尚待核对':'原停止结果尚待核对'}</h3><p>{pending.kind==='register'?pending.body.label:pending.body.reason}</p><p>完整原请求已保存；刷新和重新进入后先只查原结果，不会另造编号或自动提交。</p><Btn disabled={locked} onClick={()=>perform(()=>c.recover())}>只查原请求结果</Btn>
   {c.pendingNotFound&&!retirement&&<><Btn disabled={locked||!c.localSaved||!c.journal.canSend()} onClick={()=>perform(()=>c.resend())}>按原内容重试</Btn><label>结束这次未确认操作的原因<textarea maxLength={1000} disabled={locked} value={retireReason} onChange={event=>{setRetireConfirmation(null);c.setReason(pending.body.request_id,event.target.value);}}/></label><p>尚未找到不等于从未送达。明确结束只封住这一原请求，不是停止已有来源，也不删除原稿。</p>
    {retireConfirmation!==retireKey?<Btn disabled={locked||!retireReason.trim()||!c.localSaved||!c.journal.canSend()} onClick={()=>setRetireConfirmation(retireKey)}>结束这次未确认操作</Btn>:<div role="group" aria-label="确认结束原操作"><p>确认保留原稿并结束这一次未确认操作？</p><Btn disabled={locked||!c.localSaved} onClick={()=>{setRetireConfirmation(null);perform(()=>c.retireOriginal());}}>确认结束这次操作</Btn><Btn disabled={locked} onClick={()=>setRetireConfirmation(null)}>继续保留原请求</Btn></div>}
   </>}
  </aside>}
  {retirement&&<aside className="graph-sources-recovery" aria-label="结束操作原结果核对"><h3>结束操作的结果仍待核对</h3><p>{retirement.body.reason}</p><p>原登记或停止操作不会再被本页重发；这次结束请求也完整保留。</p><Btn disabled={locked} onClick={()=>perform(()=>c.recoverRetirement())}>只查结束操作结果</Btn>{c.retirementNotFound&&<Btn disabled={locked||!c.localSaved||!c.journal.canSend()} onClick={()=>perform(()=>c.resendRetirement())}>按原内容重试结束操作</Btn>}</aside>}
  <div className="graph-sources-layout"><section className="graph-sources-form" aria-label="登记本地变化候选结果来源"><h3>登记本地变化候选结果来源</h3>
   {!c.canRegister&&<p>{c.options?.reason||'只有获服务器许可的管理员可以新增或停止来源；历史和原请求仍可核对。'}</p>}
   <label>结果来源名称<input maxLength={200} disabled={locked} value={c.form.label} placeholder="便于流程负责人辨认" onChange={event=>c.edit({label:event.target.value})}/></label>
   {['source','capability'].map(kind=>{const rows=kind==='source'?c.options?.sources||[]:c.options?.capabilities||[],chosen=c.form[kind];return <div key={kind}><label>{kind==='source'?'已有变化结果来源':'已登记的本地变化分析能力'}<select disabled={locked} value={chosen?.id||''} onChange={event=>perform(()=>c.choose(kind,event.target.value))}><option value="" disabled>请明确选择</option>{chosen&&!rows.some(row=>row.id===chosen.id)&&<option value={chosen.id}>{kind==='source'?chosen.name:capabilityLabel(chosen)}（保留原选择，需重新核对）</option>}{rows.map(row=><option key={row.id} value={row.id}>{kind==='source'?row.name+' · 来源版本 '+row.release_version:capabilityLabel(row)}</option>)}</select></label>{c.options&&!rows.length&&<p>{kind==='source'?(c.options.next_after?'本页暂无可选来源，可继续读取。':'尚无本实例已保存的归集来源，请先在已启用业务中完成真实变化分析、关联对象并接收结果。'):'尚无可核对的已发布学习型变化能力，请先在影像资源页完成真实本地分析，并登记为业务处理能力。'}</p>}</div>;})}
   {c.options?.next_after&&<Btn disabled={locked} onClick={()=>perform(()=>c.more('sources'))}>读取更多变化候选来源</Btn>}
   <div className="graph-sources-actions"><Btn disabled={locked||!c.canRegister||!c.localSaved||!!pending||!!retirement||!c.journal.canSend()} onClick={()=>perform(()=>c.previewRegistration())}>预览并核对登记范围</Btn><Btn disabled={locked} onClick={()=>perform(()=>c.refresh())}>刷新实际来源和登记记录</Btn></div><p>{c.localSaved?'本页输入已保存在本机。':'本页输入尚未完整保存。'}</p>
   {c.preview&&<aside className="graph-sources-preview" aria-label="本地变化候选来源登记确认"><h4>{c.preview.body.label}</h4><p>{c.form.source?.name} · 来源版本 {c.preview.value.contract.source_release_version}</p><p>{modelName(c.form.capability)}</p><CapabilityScope contract={c.preview.value.contract}/><p>{c.preview.value.meaning}</p><ScopeExplanation/><Btn primary disabled={locked||!c.canRegister||!c.localSaved||!!pending||!!retirement||!c.journal.canSend()} onClick={()=>perform(()=>c.register())}>确认登记并保存</Btn></aside>}
  </section><section className="graph-sources-records" aria-label="已登记本地变化候选来源"><h3>已登记的本地变化候选来源</h3>
   {!c.registrations.loaded?<p role="status">正在读取登记记录…</p>:!c.registrations.items.length?<p>本页尚无本地变化候选来源登记记录。登记列表为空不表示旧原稿或处理结果被删除。</p>:<ul className="graph-sources-record-list">{c.registrations.items.map(row=><li key={row.id}><button type="button" disabled={locked} aria-pressed={selected?.id===row.id} onClick={()=>{setStopConfirmation(null);perform(()=>c.selectRegistration(row.id));}}><strong>{row.label}</strong><span>{row.source_name||'原登记来源'} · {row.capability_name||'原已登记变化分析能力'}</span><span>{row.status==='active'?'登记保留':'已停止供应'} · {row.current_availability.label}</span></button></li>)}</ul>}
   {c.registrations.next_after&&<Btn disabled={locked} onClick={()=>perform(()=>c.more('registrations'))}>读取更多登记记录</Btn>}
   {selected&&<article className="graph-sources-detail"><h4>{selected.label}</h4><p>{selected.source_name||'原登记来源'} · 来源版本 {selected.contract.source_release_version}</p><p>{selected.capability_name||'原已登记变化分析能力'}</p><CapabilityScope contract={selected.contract}/><p>{selected.current_availability.label}</p><ScopeExplanation/><p>登记本身没有接纳或等待匹配回执。请在业务工作台查看原变化候选处理的数据接收与接纳情况，再到等待任务核对是否匹配。</p>
    {selected.status==='active'&&c.canRegister&&<><label>停止此来源的原因<textarea maxLength={1000} value={reason} disabled={locked} onChange={event=>{setStopConfirmation(null);c.setReason(selected.id,event.target.value);}}/></label><p>停止会阻止此来源的新接纳和新执行，历史记录、原A/B影像与人稿保留；不会停止物理设备。</p>{stopConfirmation!==stopKey?<Btn disabled={locked||!!pending||!!retirement||!reason.trim()||!c.localSaved||!c.journal.canSend()} onClick={()=>setStopConfirmation(stopKey)}>停止此变化候选结果来源</Btn>:<div role="group" aria-label="确认停止变化候选来源"><Btn disabled={locked||!c.localSaved} onClick={()=>{setStopConfirmation(null);perform(()=>c.revoke());}}>确认停止此来源</Btn><Btn disabled={locked} onClick={()=>setStopConfirmation(null)}>继续保留来源</Btn></div>}</>}
   </article>}
  </section></div>
 </section>{delivery}</>;
}


