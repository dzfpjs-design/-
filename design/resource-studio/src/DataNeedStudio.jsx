import {restoreDecision} from './native-acquisition-contract.mjs';
import React,{useEffect,useMemo,useRef,useState} from 'react';
import {json} from './runtime-api';
import {WorkspaceNeedControllerV246} from './workspace-need-v246-controller.mjs';
import {needCanonical,needBody} from './workspace-need-v246-protocol.mjs';
import {createRetainedControllerLifecycle} from './retained-controller-lifecycle.mjs';
import {measurementUnit} from './measurement-unit-v342.mjs';
const labels={queued:'等待核对',sufficient:'资料已满足要求',insufficient:'资料尚不足',conflict:'记录之间有冲突',pending_delivery:'正在送达资料缺口',received:'正在核对',unresolved:'需要补充依据',needs_review:'已有记录待人工核查',needs_existing_evidence:'需要补充有效记录',awaiting_collection:'等待数据归集回执',resolved:'本次资料需求已满足',cancelled:'已停止核对'};
const value=x=>x===null||x===undefined?'未填写':typeof x==='object'?JSON.stringify(x):String(x);
export function SavedAction({storageKey,endpoint,canWrite,Btn,makeBody,onSaved,label,choices}){
 const [decision,setDecision]=useState(restoreDecision(null,choices)),[reason,setReason]=useState(''),[pending,setPending]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[cacheError,setCacheError]=useState('');
 const request=useRef(null),life=useRef(0);
 useEffect(()=>{life.current++;setBusy(false);setError('');setCacheError('');request.current=null;setPending(null);setReason('');setDecision(restoreDecision(null,choices));try{
  const saved=JSON.parse(localStorage.getItem(storageKey)||'null');if(saved){if(typeof saved.reason!=='string'||(saved.pending&&!saved.pending.request_id))throw Error('原操作草稿无法恢复，请保留记录');setReason(saved.reason);setDecision(restoreDecision(saved,choices));request.current=saved.pending||null;setPending(saved.pending||null);}
 }catch(e){setCacheError(e.message);}return()=>{life.current++;};},[storageKey]);
 function edit(next){try{localStorage.setItem(storageKey,JSON.stringify({reason:next,decision,pending:request.current}));setReason(next);}catch(e){setCacheError('理由没有保存，已阻止提交：'+e.message);}}
 function chooseDecision(next){try{if(pending)return;const choice=restoreDecision({decision:next},choices);localStorage.setItem(storageKey,JSON.stringify({reason,decision:choice,pending:null}));setDecision(choice);}catch(e){setCacheError(e.message);}}
 async function submit(){if(!canWrite||cacheError)return;const epoch=life.current;setBusy(true);setError('');try{
  if(!request.current){if(!reason.trim())throw Error('请填写核查理由');const body={request_id:crypto.randomUUID(),reason:reason.trim(),...makeBody(decision)};localStorage.setItem(storageKey,JSON.stringify({reason,decision,pending:body}));request.current=body;setPending(body);}
  await json(endpoint,{method:'POST',body:JSON.stringify(request.current)});if(epoch!==life.current)return;
  localStorage.removeItem(storageKey);request.current=null;setPending(null);setReason('');await onSaved();
 }catch(e){if(epoch===life.current)setError(e.message+' 原请求保留，请核对后沿同一编号恢复。');}finally{if(epoch===life.current)setBusy(false);}}
 return <div className="need-action">{choices?.length>0&&<label>{label}的结论<select aria-label={label+'结论'} disabled={busy||!!pending||!!cacheError||!canWrite} value={decision} onChange={e=>chooseDecision(e.target.value)}>{choices.map(c=><option value={c.value} key={c.value}>{c.label}</option>)}</select></label>}<label>{label}的理由<input maxLength={2000} aria-label={label+'理由'} disabled={busy||!!pending||!!cacheError||!canWrite} value={reason} onChange={e=>edit(e.target.value)}/></label>
 {pending&&<p>待核请求已保存。恢复使用原内容，不重复创建或替换条件。</p>}{cacheError&&<p role="alert">{cacheError}</p>}{error&&<p role="alert">{error}</p>}
 <Btn disabled={busy||!!cacheError||!canWrite||(!pending&&!reason.trim())} onClick={submit}>{busy?'正在保存…':pending?'恢复原'+label+'请求':label}</Btn></div>;
}
const needStatuses={queued:'等待核对',sufficient:'所选资料已满足本次要求',insufficient:'所选资料仍不足',conflict:'登记结果有冲突',needs_clarification:'问题需要澄清',needs_confirmation:'条件等待确认',blocked_model:'处理条件受阻',failed:'本次核对未完成',pending_delivery:'缺口正在送达业务',received:'缺口已接收，正在核对',unresolved:'需要补充依据',needs_review:'已有记录待人工核查',needs_existing_evidence:'需要补充有效记录',awaiting_collection:'等待数据归集回执',resolved:'本次资料需求已满足',cancelled:'已停止核对'};
const needStatus=value=>needStatuses[value]||'处理条件待核对';
export default function DataNeedStudio({instanceId,account,Btn,onRegisterGuard,api=json,storage=globalThis.localStorage,tabStorage=globalThis.sessionStorage,locks=globalThis.navigator?.locks}){
 const latest=useRef(),life=useRef(),formNode=useRef(null);if(!life.current)life.current=createRetainedControllerLifecycle();let token='';try{token=tabStorage.getItem('ga-setup-csrf')||'';}catch{}
 const owner=needCanonical([account?.user_id,account?.workspace_id,account?.role,instanceId,token]);latest.current={owner,onRegisterGuard};
 const [stored,setStored]=useState(null),[localError,setLocalError]=useState('');
 const holder=useMemo(()=>{try{return {c:new WorkspaceNeedControllerV246({account,instanceId,api,storage,session:tabStorage,locks,scopeAvailable:()=>latest.current.owner===owner})};}catch(e){return {error:e.message};}},[owner,api,storage,tabStorage,locks]),c=holder.c,v=stored?.owner===owner?stored.value:null;
 useEffect(()=>{
  const guard=()=>latest.current.owner===owner&&!!c?.guard();latest.current.onRegisterGuard?.(guard);if(!c)return()=>latest.current.onRegisterGuard?.(null,guard);
  const lease=life.current.setup(c,owner);if(!lease)return;let mounted=true;c.onChange=value=>{if(mounted)setStored({owner,value});};setStored({owner,value:c.view()});
  const unload=e=>{if(!guard()){e.preventDefault();e.returnValue='';}};globalThis.addEventListener?.('beforeunload',unload);if(!c.started){c.started=true;c.start().catch(()=>{});}
  return()=>{mounted=false;latest.current.onRegisterGuard?.(null,guard);globalThis.removeEventListener?.('beforeunload',unload);life.current.cleanup(lease);};
 },[owner,c]);
 async function run(fn){setLocalError('');try{return await fn();}catch(e){if(latest.current.owner===owner)setLocalError(e.message);}}
 async function rereadSaved(){return run(()=>c.action(()=>c.readLastSaved()));}
 async function applyCopy(choice){const result=await run(()=>c.applyCopy(choice));if(latest.current.owner===owner&&!c.copyIntent&&choice==='use'){formNode.current.open=true;formNode.current.scrollIntoView?.({block:'start'});}return result;}
 async function download(){const d=c.detail;if(!d)return;const data=await c.request(c.base+'/'+d.id+'/export'),url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='业务资料需求与核查依据.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
 if(v?.denied)return <section className="data-need-studio" aria-label="资料需求范围已隐藏"><p role="alert">账号、工作区或实例已变化，原问题、人稿和原申请保留；未采用迟到结果。</p></section>;
 if(!v?.authenticated)return <section className="data-need-studio"><h2>业务需要哪些资料</h2><p role="status">正在核对当前账号与实例，完成前隐藏原问题和人稿。</p>{(holder.error||v?.error)&&<p role="alert">{holder.error||v.error}</p>}{c&&<Btn disabled={v?.busy} onClick={()=>run(()=>c.start())}>重新读取原资料需求</Btn>}</section>;
 const draft=v.draft,f=draft.form,options=v.options,detail=v.detail,object=options?.objects.find(e=>e.id===f.entity_id),writer=['admin','operator'].includes(account.role),editDisabled=!writer||v.busy||!v.loaded||!!v.pending||!!v.copyIntent||v.detached,disabled=editDisabled||!v.localSaved,copy=v.copyIntent?.descriptor;
 const fieldName=k=>draft.origin?.source_fields?.find(x=>x.key===k)?.label||object?.field_labels?.[k]||'原选择的字段';
 const currentBodyIssue=(()=>{try{if(!v.optionsCurrent||!options)return '当前目录尚未核对，原选择保留。';needBody(draft,options,'preview-only-request-v246');return '';}catch(e){return e.message;}})();
 return <section className="data-need-studio" aria-label="业务资料需求与核对"><h2>业务需要哪些资料</h2><p>选择真实业务、对象和中文字段，按你的原句核对已有记录。登记数值、现场状态和业务目标分别核对；此入口不启动设备或调用模型。</p>
 {[v.error,localError].filter(Boolean).filter((x,i,a)=>a.indexOf(x)===i).map((message,i)=><p role="alert" key={i}>{message}</p>)}{v.notice&&<p role="status">{v.notice}</p>}
 {v.requestProof&&<p role="status" aria-label="原需求找回依据">{v.requestProof.evidence_level==='typed_body_receipt'?'已核对这次提交的完整保存回执；原始输入继续保留在本机，恢复没有重新发起查询。':'已按原需求的历史记录找回。旧版没有独立保存完整提交回执；原始输入继续保留在本机，没有补造旧回执。'}</p>}
 {v.lastSaved&&!v.pending&&<Btn disabled={v.busy} onClick={rereadSaved}>重新读取原需求详情</Btn>}
 {v.legacy&&<aside className="info"><p>旧版填写记录原文已独立保留，没有覆盖旧缓存。</p>{v.legacy.purpose&&<p>旧稿原句：{v.legacy.purpose}</p>}{v.legacy.pending&&<><p>旧版原申请尚待核对；缺少原账号或精确正文证明时不会重新签名、改条件或自动提交。</p><Btn disabled={v.busy||!v.legacy.body} onClick={()=>run(()=>c.recoverLegacy())}>只核对旧版原申请</Btn></>}</aside>}
 {v.pending&&<aside className="info" aria-label="原资料需求申请"><h3>原资料需求仍待核对</h3><p>原句：{v.pending.body.purpose}</p><p>系统会按已保存的原申请核对服务器结果，不受近期清单条数限制。没有查到不能证明原请求未保存；原对象、字段、来源、时效和原始输入继续保留，不会自动重新提交。</p><Btn disabled={v.busy||!v.localSaved} onClick={()=>run(()=>c.recover(false))}>只核对原资料需求申请</Btn>{v.notFound&&writer&&<Btn disabled={v.busy||!v.localSaved} onClick={()=>run(()=>c.recover(true))}>按原号原条件重试</Btn>}</aside>}
 {copy&&<aside className="media-form" aria-label="原条件另问预览"><h3>保留原需求，另问一份</h3><p>原句：{copy.conditions.purpose}</p><p>业务：{detail?.business_name||'原业务'}；对象：{detail?.object_name||'原对象'}。原来源：{copy.conditions.source==='archive'?'业务档案':'设备或人工登记记录'}。</p><ul>{copy.source_fields.map(field=><li key={field.key}>{field.label} · {field.kind||'类型尚未声明'} · {field.unit||'无单位'}</li>)}</ul><p>{copy.conditions.source==='archive'?'档案查询不适用登记时效与观测确认等级。':'确认要求：'+(copy.conditions.confirmation==='confirmed'?'已人工核查':'允许尚未人工核查的提取记录')+'；登记时效 '+copy.conditions.max_age_hours+' 小时。'}</p>
 {copy.source.release_id!==copy.target.release_id&&<p role="alert">当前启用版本与原需求不同。这里只在对象、业务标识、字段类型、单位及完整约束仍兼容时，供你明确确认采用当前版本；旧需求不变。</p>}
 {copy.reasons.map((reason,i)=><p role="alert" key={i}>{reason}</p>)}{v.copyIntent.hasWork&&<p>新建表已有未提交到服务器的人稿。你可以保留两份，选择继续当前填写或改用原条件。</p>}
 <div className="actions"><Btn disabled={v.busy||!copy.can_copy||!v.localSaved} onClick={()=>applyCopy('use')}>{v.copyIntent.hasWork?'保留当前人稿，再用原条件填表':'将原条件填入新建表（不提交）'}</Btn><Btn disabled={v.busy||!copy.can_copy||!v.localSaved} onClick={()=>applyCopy('save_only')}>另存原条件，继续当前填写</Btn><Btn disabled={v.busy} onClick={()=>c.cancelCopy()}>取消复制，保留当前稿</Btn></div><p>采用预览只填写新表，不会自动发起查询；旧问题、依据和人稿继续保留。</p></aside>}
 <details ref={formNode} className="need-create"><summary>新增资料需求</summary>{!options?<p>当前业务和对象目录尚未读到，不能把未知范围当空范围。</p>:<>
 {!v.optionsCurrent&&<p role="alert">以下是上次目录，当前尚未重新核对；原选择保留，提交前会重新核对。</p>}{options.objects_truncated&&<p>当前只列前100个可用对象，未列对象仍保留，不能按名称替换。</p>}
 <fieldset className="media-form" disabled={editDisabled} style={{border:0,padding:0}}>
 <label>资料用于哪个业务<select aria-label="资料需求业务" value={f.business_id} onChange={e=>c.edit({business_id:e.target.value})}><option value="">选择已启用业务</option>{f.business_id&&!options.businesses.some(b=>b.id===f.business_id)&&<option value={f.business_id} disabled>原业务当前不可用（已保留）</option>}{options.businesses.map(b=><option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
 <label>核对哪个对象<select aria-label="资料需求对象" value={f.entity_id} onChange={e=>c.edit({entity_id:e.target.value})}><option value="">选择业务对象</option>{f.entity_id&&!object&&<option value={f.entity_id} disabled>原对象当前不可用（已保留）</option>}{options.objects.map(e=><option key={e.id} value={e.id}>{e.name}</option>)}</select></label>
 <label>你需要核对什么（保留原句）<textarea aria-label="资料用途" value={f.purpose} onChange={e=>c.edit({purpose:e.target.value})}/></label><p>输入先保存在本机，保存需求后才发往服务器；最多2000字，超出时保留原文供你调整。</p>
 <div><p>需要核对的字段</p>{(object?.fields||[]).map(k=><label className="checkbox" key={k}><input type="checkbox" aria-label={'资料字段：'+(object.field_labels?.[k]||k)} checked={f.fields.includes(k)} onChange={e=>c.edit({fields:e.target.checked?[...f.fields,k]:f.fields.filter(x=>x!==k)})}/>{object.field_labels?.[k]||k}</label>)}{f.fields.filter(k=>!object?.fields.includes(k)).map((k,i)=><label key={k}><input type="checkbox" aria-label={'保留的失效字段'+(i+1)} checked onChange={()=>c.edit({fields:f.fields.filter(x=>x!==k)})}/>{fieldName(k)}（当前目录不可用，仍保留；取消须由你明确选择）</label>)}</div>
 <label>资料来源<select aria-label="资料需求来源" value={f.source} onChange={e=>c.edit({source:e.target.value})}><option value="observations">设备或人工登记记录</option><option value="archive">业务档案</option></select></label>
 {f.source==='observations'&&<><label>采用哪类记录<select aria-label="资料确认要求" value={f.confirmation} onChange={e=>c.edit({confirmation:e.target.value})}><option value="confirmed">已人工核查的记录</option><option value="unverified_extraction">已提取的记录，允许尚未人工核查</option></select></label><label>登记时间距现在最多几小时<input type="text" inputMode="numeric" aria-label="资料有效小时数" value={f.max_age_hours} onChange={e=>c.edit({max_age_hours:e.target.value})}/></label></>}
 </fieldset>{draft.origin&&<p>这份新表来自已保存原条件，原问题没有改写。采用时的字段类型与单位已固定，提交前还会复核当前范围。</p>}{currentBodyIssue&&<p role="note">{currentBodyIssue}</p>}
 <Btn primary disabled={disabled||!v.optionsCurrent||!!currentBodyIssue||v.submitted||!!v.legacy?.pending} onClick={()=>run(()=>c.create())}>{v.busy?'正在核对并保存…':'保存资料需求并核对'}</Btn>{v.submitted&&<p role="status">这份完整条件已保存。修改后才能另建查询；原查询继续保留。</p>}<Btn disabled={v.busy||!!v.pending||!v.localSaved||!!v.copyIntent} onClick={()=>run(()=>c.newDraft())}>保留当前稿，开始新的填写</Btn>
 </>}</details>
 {!v.localSaved&&v.loaded&&<><p role="alert">当前尝试输入仍保留在页面，尚未确认存储成功；未发送新需求。</p><Btn disabled={v.busy} onClick={()=>run(()=>c.saveAgain())}>重试保存当前人稿</Btn><Btn disabled={v.busy} onClick={()=>run(()=>c.retainDraft())}>另存当前人稿并允许离开</Btn></>}
 {v.stageFailedBeforePost&&<Btn disabled={v.busy} onClick={()=>run(()=>c.start())}>重新读回已保留的原申请</Btn>}
 {v.detached&&<p role="status">当前稿已独立保留；恢复为活动人稿并核对当前范围后才能提交。</p>}
 {v.history.length>0&&<details><summary>已保留的人稿（{v.history.length}份）</summary>{v.history.map((item,i)=><article key={item.key}><p>第{i+1}份：{item.draft.form.purpose||'尚未填写原句'} · {item.draft.form.fields.length} 个所选字段</p><Btn disabled={v.busy||!!v.pending||!!v.copyIntent} onClick={()=>run(async()=>{await c.restoreDraft(item.key);formNode.current.open=true;})}>恢复第{i+1}份人稿</Btn></article>)}</details>}
 <div className="section-title"><h3>已保存的资料需求</h3><Btn disabled={v.busy} onClick={()=>run(()=>c.refresh())}>更新处理进度</Btn></div>{!v.listCurrent&&<p role="alert">当前清单尚未重新读回，上次记录仍保留，不能认为没有需求。</p>}{!v.items.length?<p>{v.listCurrent?'当前实例还没有已保存资料需求。':'资料需求数量尚未核对。'}</p>:v.items.map(item=><article className="media-session" key={item.id}><h4>{item.purpose}</h4><p>{item.business_name} · {item.object_name} · 资料核对：{needStatus(item.result_status||item.query_status)} · 缺口协调：{needStatus(item.status)}</p><Btn disabled={v.busy} onClick={()=>run(()=>c.select(item.id))}>查看要求与依据</Btn></article>)}{v.truncated&&<p>这里只列最近100条，未列需求仍保留；查不到原申请不代表未保存。</p>}
 {detail&&<section className="need-detail" aria-label="已保存的资料需求与原依据"><h3>{detail.purpose}</h3><p>{detail.business_name} · {detail.object_name}。资料核对：{needStatus(detail.result_status||detail.query_status)}；缺口协调：{needStatus(detail.status)}。</p>
 <p>所选字段：{detail.fields.map(k=>detail.field_labels?.[k]||k).join('、')}；来源：{detail.requirement?.source==='archive'?'业务档案':'设备或人工登记记录'}。原句、业务和对象范围随本次需求保存。</p>
 {detail.requirement?.source==='observations'&&<p>原确认要求：{detail.requirement.minimum_verification==='confirmed'?'已人工核查':'允许尚未人工核查的提取记录'}；登记时间距本次核对时不超过 {detail.requirement.time?.max_age_seconds/3600} 小时。登记时间未经现场核验。</p>}
 {detail.clarifications.length>0&&<aside aria-label="资料需求的具体澄清问题"><h4>需要明确哪些条件</h4><ul>{detail.clarifications.map((q,i)=><li key={i}>{q}</li>)}</ul><p>原问题与条件仍保存。请保留原条件另问，不用反复按旧条件重新核对来代替澄清。</p></aside>}
 {detail.error&&<p role="alert">{detail.error}</p>}{detail.reason&&<p>{detail.reason}</p>}{!detail.scope_valid&&<p role="alert">原实例或业务范围已改变；以下只展示历史，不能用新范围关闭旧需求。</p>}
 <div className="actions"><Btn disabled={v.busy} onClick={()=>run(download)}>导出原需求与核查依据</Btn><Btn disabled={v.busy||!writer||!!v.pending||!v.localSaved||!!v.legacy?.pending} onClick={()=>run(()=>c.previewCopy(detail.id))}>保留原条件另问</Btn></div>
 {(detail.records||[]).map(record=><article className="media-session" key={record.observation_id}><h4>{record.source_name}</h4><p>原登记时间：{new Date(record.reported_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}（北京时间） · {record.review==='confirmed'?'登记已人工核查':record.review==='rejected'?'登记已被拒绝':'登记待核查'}</p><table><thead><tr><th>字段</th><th>原登记输入</th><th>提取回执</th><th>单位及来源</th></tr></thead><tbody>{detail.fields.map(k=><tr key={k}><td>{detail.field_labels?.[k]||k}</td><td>{value(record.original_values?.[k])}</td><td>{value(record.extracted_values?.[k])}</td><td>{measurementUnit(record.measurement_units?.[k],record.extracted_values?.[k])}</td></tr>)}</tbody></table><p>单位来自报文或已确认的接入映射；核对登记与回执不证明设备校准、现场状态或完成其他业务目标。</p>
 <section>{record.review_receipt&&<p>已保存核查：{record.review_receipt.reason} · {new Date(record.review_receipt.reviewed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</p>}{record.review_history?.length>0&&<details><summary>已保存核查历史（{record.review_history.length}条）</summary>{record.review_history.map((r,i)=><p key={i}>{r.decision==='confirmed'?'确认登记核查':'拒绝登记'}：{r.reason} · {new Date(r.reviewed_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}</p>)}{record.review_history_truncated&&<p>更早的核查仍保存在原记录中。</p>}</details>}</section>
 <SavedAction storageKey={c.legacyKey+':verify:'+record.observation_id} endpoint={'/observations/'+record.observation_id+'/verify'} canWrite={writer&&detail.scope_valid&&!!record.original_values} Btn={Btn} label="确认登记核查" makeBody={()=>({expected_version:record.version,expected_input_hash:record.review_input_hash,decision:'confirmed'})} onSaved={()=>run(()=>c.refresh())}/>
 </article>)}
 {(detail.archive_evidence||[]).map((record,i)=><article className="media-session" key={i}><h4>已有业务档案</h4>{detail.fields.map(k=><p key={k}>{detail.field_labels?.[k]||k}：{value(record.fields[k])}</p>)}<p>档案第{record.version}版；不作为新的设备观测。</p></article>)}
 {!detail.records?.length&&!detail.archive_evidence?.length&&<p>本次没有可在这里采用的有效记录；字段齐全、处理完成和独立收件应分别核对。</p>}
 {detail.background&&<p>最近后台核对：{detail.background.checked_at?new Date(detail.background.checked_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'时间待核'}（北京时间）；只核对已保存资料，不调用模型、不启动设备，也不据此确认现场。</p>}
 <SavedAction storageKey={c.legacyKey+':check:'+detail.id} endpoint={c.base+'/'+detail.id+'/check'} canWrite={writer&&detail.scope_valid} Btn={Btn} label="重新核对现有资料" makeBody={()=>({expected_version:detail.version})} onSaved={()=>run(()=>c.refresh())}/>
 </section>}
 </section>;
}
